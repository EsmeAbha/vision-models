"""Check the guards that only matter once this page is reachable from outside.

Starts its own server on a spare port with a password set, so it does not
disturb the loopback one. What it proves: the API is shut without the
password, guessing at it is slowed down, an archive that would explode on disk
is refused before it is unpacked, and the server will not even start listening
beyond this machine without a password.

    .\\.venv\\Scripts\\python.exe test_appraisal_public.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PORT = 7899
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "correct-horse-battery-staple"
HERE = Path(__file__).resolve().parent
PY = HERE / ".venv" / "Scripts" / "python.exe"

FAILED = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        wanted {want!r}")
        print(f"        got    {got!r}")
        FAILED.append(label)


def call(path, data=None, cookie=None, method=None):
    """-> (status, body). Never raises for an HTTP error status."""
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}"), e.headers
        except Exception:
            return e.code, {}, e.headers


def post_zip(path, cookie=None):
    boundary = "----pubboundary4471"
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="file"; '
               b'filename="a.zip"\r\n')
    body.write(b"Content-Type: application/zip\r\n\r\n")
    body.write(Path(path).read_bytes())
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        BASE + "/api/jobs", data=body.getvalue(),
        headers={"Content-Type":
                 f"multipart/form-data; boundary={boundary}"})
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def zip_bomb(path):
    """A small archive that declares a very large unpacked size."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 400 MB of zeroes compresses to a few hundred KB; eight of them put
        # the declared total over the 12 GB ceiling at a ratio far past 200x.
        blob = b"\0" * (400 * 1024 * 1024)
        for i in range(8):
            zf.writestr(f"pad_{i}.bin", blob)


def wait_up(proc, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(BASE + "/api/session", timeout=2).read()
            return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    print("the server refuses to listen outside without a password")
    env = dict(os.environ, APPRAISAL_HOST="0.0.0.0")
    env.pop("APPRAISAL_PASSWORD", None)
    done = subprocess.run([str(PY), str(HERE / "appraisal_server.py")],
                          capture_output=True, text=True, env=env, timeout=120,
                          cwd=str(HERE))
    check("it exits rather than starting", done.returncode, 2)
    check("and says why", "APPRAISAL_PASSWORD" in done.stdout, True)

    env = dict(os.environ, APPRAISAL_PASSWORD=PASSWORD,
               APPRAISAL_PORT=str(PORT), APPRAISAL_HOST="127.0.0.1")
    proc = subprocess.Popen([str(PY), str(HERE / "appraisal_server.py")],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, cwd=str(HERE))
    try:
        if not wait_up(proc):
            print("the guarded server did not come up")
            print((proc.stdout.read() if proc.stdout else "")[:2000])
            return 2

        print("\nthe page says a password is needed")
        _s, body, _h = call("/api/session")
        check("required", body.get("required"), True)
        check("not signed in yet", body.get("signed_in"), False)

        print("\nthe API is shut without it")
        status, body, _h = call("/api/spec")
        check("spec is 401", status, 401)
        status, _b = post_zip(__file__)
        check("upload is 401", status, 401)

        print("\nthe wrong password is refused")
        status, body, _h = call("/api/login", {"password": "hunter2"})
        check("401", status, 401)
        check("no session handed out", body.get("ok"), None)

        print("\nthe right password opens it")
        status, body, headers = call("/api/login", {"password": PASSWORD})
        check("200", status, 200)
        raw = headers.get("set-cookie") or ""
        check("a session cookie is set", "appraisal_session=" in raw, True)
        check("the cookie is HttpOnly", "HttpOnly" in raw, True)
        check("and SameSite=strict", "samesite=strict" in raw.lower(), True)
        cookie = raw.split(";")[0]

        status, body, _h = call("/api/spec", cookie=cookie)
        check("the spec opens up", status, 200)
        check("and it is the real spec", len(body.get("rows", [])), 10)

        print("\na cross-origin call with a good cookie is still refused")
        req = urllib.request.Request(BASE + "/api/spec")
        req.add_header("Cookie", cookie)
        req.add_header("Origin", "http://evil.example")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                check("it refuses", r.status, 403)
        except urllib.error.HTTPError as e:
            check("it refuses with 403", e.code, 403)

        print("\na zip bomb is refused before it is unpacked")
        tmp = Path(tempfile.mkdtemp(prefix="bomb_"))
        bomb = tmp / "bomb.zip"
        zip_bomb(bomb)
        packed = bomb.stat().st_size
        check("the archive itself is small", packed < 5 * 1024 ** 2, True)
        status, body = post_zip(bomb, cookie=cookie)
        check("refused with 413", status, 413)
        check("the reason names the expansion",
              "unpacks to" in body.get("detail", "")
              or "expands" in body.get("detail", ""), True)

        unpacked = HERE / "uploads" / "appraisals"
        big = [p for p in unpacked.rglob("pad_*.bin")] if unpacked.exists() else []
        check("nothing from it reached the disk", big, [])

        print("\na file that is not a zip is refused")
        notzip = tmp / "x.zip"
        notzip.write_bytes(b"definitely not a zip")
        status, body = post_zip(notzip, cookie=cookie)
        check("refused with 400", status, 400)
        check("the reason says so",
              "readable zip" in body.get("detail", ""), True)

        # Last, because a lockout is per client address and would otherwise
        # shut this test out of its own session.
        print("\nguessing is slowed down")
        codes = [call("/api/login", {"password": f"no{i}"})[0]
                 for i in range(5)]
        check("the first guesses are refused", codes[0], 401)
        check("then it starts answering 429", 429 in codes, True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if FAILED and proc.stdout:
            print("\n--- what the server said ---")
            print(proc.stdout.read()[:4000])

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
