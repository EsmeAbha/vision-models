"""A page for pulling one table out of a stack of appraisals.

Upload a zip of appraisals; every PDF inside is searched for the manufactured-
housing rating guide, and only that table is taken. mhc_rating_table decides
what counts as the guide and mhc_batch lays the answers out; this file is the
upload, the progress and the download around them.

Run it with the same interpreter as the workspace server:

    .\\.venv\\Scripts\\python.exe appraisal_server.py     # http://127.0.0.1:7885

app.py keeps 7860, the chat page 7862, the FinAI workspace 7880.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import queue
import secrets
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import mhc_batch
import mhc_rating_table as M
import zips

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "appraisal_web"
UNPACK_DIR = ROOT / "uploads" / "appraisals"
OUT_DIR = ROOT / "outputs" / "appraisals"

HOST = os.environ.get("APPRAISAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("APPRAISAL_PORT", "7885"))

# Set APPRAISAL_PASSWORD to put the page behind a login. On loopback it is
# optional -- anyone who can reach 127.0.0.1 is already this user. The moment
# the server is told to listen anywhere else it is required, and start-up
# fails without it: an upload endpoint that unpacks archives to disk is not
# something to leave open to the internet by accident.
PASSWORD = os.environ.get("APPRAISAL_PASSWORD", "")

# A zip of thirty appraisals is already a few hundred megabytes, so the cap is
# generous -- but it is a cap, and the upload is streamed to disk in chunks
# rather than read into memory, because reading a 2 GB archive into RAM to
# check its size is its own kind of failure.
MAX_ZIP = 3 * 1024 ** 3
CHUNK = 1024 * 1024

# What the archive is allowed to become once unpacked. A zip is free to claim
# it holds a petabyte of zeroes in a megabyte of file, and extractall would
# cheerfully try -- so the declared sizes are added up and the ratio checked
# before a single byte is written.
MAX_UNPACKED = 12 * 1024 ** 3
MAX_RATIO = 200
MAX_MEMBERS = 20000

# One archive at a time is being read anyway; this stops a public endpoint
# from being handed fifty at once and unpacking them all.
MAX_ACTIVE_JOBS = 3

SESSION = "appraisal_session"
_token = secrets.token_urlsafe(32)
_fails: dict = {}

_jobs: dict = {}
_lock = threading.Lock()

app = FastAPI(docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


# ------------------------------------------------------------------- access

def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "")


def _authed(request: Request):
    if not PASSWORD:
        return True
    got = request.cookies.get(SESSION) or ""
    return hmac.compare_digest(got, _token)


def _secure(request: Request):
    """Whether to mark the cookie Secure.

    A tunnel terminates TLS and forwards plain HTTP, so the scheme on the
    request is http even though the browser is on https. The forwarded header
    is what says what the browser actually used.
    """
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return (proto or request.url.scheme) == "https"


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    # /api/session and /api/login are how a browser finds out it needs a
    # password and supplies one; gating them behind the password would leave
    # the page with no way to ask for it.
    open_paths = ("/api/login", "/api/session")
    if PASSWORD and path.startswith("/api") and path not in open_paths:
        if not _authed(request):
            return JSONResponse({"detail": "sign in to use this page"}, 401)
        # The session cookie is SameSite=strict, so a cross-site form cannot
        # ride it; this turns away a same-site-but-wrong-origin caller too.
        origin = request.headers.get("origin")
        if origin:
            host = request.headers.get("host", "")
            if origin.split("://")[-1] != host:
                return JSONResponse({"detail": "request not permitted"}, 403)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' "
        "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'")
    return response


class Login(BaseModel):
    password: str = ""


@app.post("/api/login")
def api_login(body: Login, request: Request):
    if not PASSWORD:
        return {"ok": True, "required": False}

    # A password endpoint that is reachable publicly gets guessed at by
    # machines. Back off per client rather than letting it run flat out.
    who = request.client.host if request.client else "?"
    fails, until = _fails.get(who, (0, 0.0))
    if time.time() < until:
        raise HTTPException(429, f"too many attempts -- wait "
                                 f"{int(until - time.time()) + 1}s")

    if not hmac.compare_digest(body.password, PASSWORD):
        fails += 1
        # Three free guesses, then a doubling lockout capped at five minutes.
        lock = time.time() + min(2 ** fails, 300) if fails >= 3 else 0.0
        _fails[who] = (fails, lock)
        raise HTTPException(401, "that is not the password")

    _fails.pop(who, None)
    out = JSONResponse({"ok": True, "required": True})
    out.set_cookie(SESSION, _token, httponly=True, samesite="strict",
                   secure=_secure(request), max_age=12 * 3600)
    return out


@app.get("/api/session")
def api_session(request: Request):
    """Whether this browser needs a password, and whether it already has one."""
    return {"required": bool(PASSWORD), "signed_in": _authed(request)}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))


@app.get("/api/spec")
def api_spec():
    """What the page says it looks for -- read from the locator, not retyped.

    The row list on the page and the row list the extractor matches on have to
    be the same list, or the page ends up advertising a table the software no
    longer looks for.
    """
    return {"columns": M.CLASS_COLUMNS, "rows": M.CORE_ROWS,
            "star_rows": M.STAR_ROWS, "min_score": M.MIN_SCORE,
            "min_core_rows": M.MIN_CORE_ROWS}


# ------------------------------------------------------------------- upload

def _inspect_archive(path):
    """Refuse an archive that would explode on disk, before unpacking a byte.

    zips.extract already drops members that would land outside the
    destination. What it does not do is ask how big they are, and a zip is
    free to claim a megabyte of file holds a petabyte of zeroes. The sizes in
    the central directory are what extraction will act on, so they are what
    gets checked -- and the ratio alongside them, because a stack of PDFs is
    already compressed and does not come anywhere near 200x.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            declared = sum(m.file_size for m in members)
            packed = sum(m.compress_size for m in members) or 1
    except (zipfile.BadZipFile, OSError) as e:
        raise HTTPException(400, f"that is not a readable zip ({type(e).__name__})")

    if len(members) > MAX_MEMBERS:
        raise HTTPException(413, f"that archive holds {len(members)} files; "
                                 f"the limit is {MAX_MEMBERS}")
    if declared > MAX_UNPACKED:
        raise HTTPException(413, f"that archive unpacks to "
                                 f"{declared / 1024 ** 3:.1f} GB; the limit "
                                 f"is {MAX_UNPACKED // 1024 ** 3} GB")
    if declared > 64 * 1024 ** 2 and declared / packed > MAX_RATIO:
        raise HTTPException(413, f"that archive expands {declared // packed}x, "
                                 f"past the {MAX_RATIO}x limit -- appraisals "
                                 f"are already-compressed PDFs and do not")
    return declared, len(members)


@app.post("/api/jobs")
async def api_create_job(file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "upload.zip")
    if not name.lower().endswith(".zip"):
        raise HTTPException(400, "that is not a .zip -- "
                                 "upload the archive of appraisals")

    with _lock:
        busy = sum(1 for j in _jobs.values() if j["status"] == "running")
    if busy >= MAX_ACTIVE_JOBS:
        raise HTTPException(429, f"{busy} archives are already being read; "
                                 f"wait for one to finish")

    UNPACK_DIR.mkdir(parents=True, exist_ok=True)
    jid = uuid.uuid4().hex
    staging = UNPACK_DIR / f"job_{jid}"
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / name

    size = 0
    with open(archive, "wb") as out:
        while True:
            chunk = await file.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_ZIP:
                out.close()
                shutil.rmtree(staging, ignore_errors=True)
                raise HTTPException(
                    413, f"that archive is over {MAX_ZIP // 1024 ** 3} GB")
            out.write(chunk)
    if size == 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(400, "that file is empty")

    try:
        _inspect_archive(archive)
    except HTTPException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    job = {"id": jid, "name": name, "status": "running", "note": "unpacking",
           "total": 0, "done": 0, "files": [], "error": None,
           "workbook": None, "counts": {}, "started": time.time(),
           "elapsed": None, "staging": str(staging),
           "events": queue.Queue()}
    with _lock:
        _jobs[jid] = job

    threading.Thread(target=_do_job, args=(jid, str(archive), str(staging)),
                     daemon=True).start()
    return {"id": jid, "name": name, "size": size}


def _emit(job, payload):
    job["events"].put(payload)


def _do_job(jid, archive, staging):
    with _lock:
        job = _jobs[jid]
    try:
        root, note = zips.extract(archive, dest_dir=staging)
        pdfs = mhc_batch.find_pdfs(root)
        job["total"] = len(pdfs)
        job["note"] = note
        _emit(job, {"type": "start", "total": len(pdfs), "note": note})

        if not pdfs:
            job["status"] = "done"
            job["note"] = f"{note}; no PDF inside that archive"
            job["elapsed"] = time.time() - job["started"]
            _emit(job, {"type": "done", "note": job["note"]})
            return

        def progress(done, total, shown, res):
            job["done"] = done
            job["files"].append(_slim(res))
            _emit(job, {"type": "file", "done": done, "total": total,
                        "result": _slim(res)})

        results = mhc_batch.run(root, progress=progress)
        job["counts"] = mhc_batch.summarise(results)

        out = OUT_DIR / f"job_{jid}"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = out / f"rating_guides_{stamp}.xlsx"
        mhc_batch.write_workbook(results, str(path))
        job["workbook"] = str(path)
        job["_full"] = results

        job["status"] = "done"
        job["elapsed"] = time.time() - job["started"]
        _emit(job, {"type": "done", "counts": job["counts"],
                    "elapsed": job["elapsed"],
                    "workbook": os.path.basename(path)})
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        _emit(job, {"type": "error", "error": job["error"]})
    finally:
        _emit(job, {"type": "end"})
        # The raw archive and its unpacked PDFs have done their job: the
        # table each one holds is already in job["_full"] in memory, and the
        # workbook is already written out to OUT_DIR. Nothing ever read this
        # folder back, and nothing ever deleted it either -- every finished
        # job, success or failure, left its whole unpacked copy on disk
        # forever. One archive of appraisals is hundreds of megabytes.
        shutil.rmtree(staging, ignore_errors=True)


def _slim(res):
    """What the list on the page needs: the verdict, not the whole table."""
    return {"file": res["file"], "status": res["status"], "page": res["page"],
            "score": res["score"], "pages": res["pages"],
            "note": res["note"], "title": res["title"],
            "rows": len([r for r in res["rows"] if not r["section"]])}


# -------------------------------------------------------------------- state

def _job_or_404(jid):
    with _lock:
        job = _jobs.get(jid)
    if not job:
        raise HTTPException(404, "no such job -- upload the archive again")
    return job


@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    job = _job_or_404(jid)
    return {k: job[k] for k in ("id", "name", "status", "note", "total",
                                "done", "files", "error", "counts",
                                "elapsed")} | {
        "workbook": os.path.basename(job["workbook"]) if job["workbook"] else None}


@app.get("/api/jobs/{jid}/table/{index}")
def api_table(jid: str, index: int):
    """One appraisal's guide in full, for the preview panel."""
    job = _job_or_404(jid)
    full = job.get("_full") or []
    if index < 0 or index >= len(full):
        raise HTTPException(404, "no such file in this job")
    res = full[index]
    if res["status"] != "found":
        raise HTTPException(404, "no guide was found in that file")
    return {"file": res["file"], "title": res["title"], "page": res["page"],
            "columns": res["columns"], "rows": res["rows"],
            "score": res["score"]}


@app.get("/api/jobs/{jid}/events")
def api_events(jid: str):
    job = _job_or_404(jid)

    def stream():
        # Replay what already happened, so a listener that connects late --
        # or reconnects -- still sees the files that are already done.
        yield _sse({"type": "start", "total": job["total"],
                    "note": job["note"]})
        for i, res in enumerate(list(job["files"]), 1):
            yield _sse({"type": "file", "done": i, "total": job["total"],
                        "result": res})
        if job["status"] != "running":
            yield _sse({"type": job["status"], "error": job["error"],
                        "counts": job["counts"], "elapsed": job["elapsed"],
                        "workbook": os.path.basename(job["workbook"])
                        if job["workbook"] else None})
            return
        while True:
            try:
                payload = job["events"].get(timeout=30)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if payload.get("type") == "end":
                return
            yield _sse(payload)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _sse(payload):
    return "data: " + json.dumps(payload) + "\n\n"


@app.get("/api/jobs/{jid}/workbook")
def api_workbook(jid: str):
    job = _job_or_404(jid)
    if not job["workbook"] or not os.path.exists(job["workbook"]):
        raise HTTPException(404, "the workbook is not ready yet")
    return FileResponse(job["workbook"],
                        filename=os.path.basename(job["workbook"]),
                        media_type="application/vnd.openxmlformats-"
                                   "officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    import sys

    import uvicorn

    if not _loopback(HOST) and not PASSWORD:
        print(f"refusing to start.\n"
              f"  APPRAISAL_HOST is {HOST!r}, so this would listen beyond "
              f"this machine,\n"
              f"  but APPRAISAL_PASSWORD is not set -- that would leave an "
              f"endpoint that\n"
              f"  accepts and unpacks archives open to anyone who finds it.\n"
              f"  Set a password, or leave the host at 127.0.0.1 and put a "
              f"tunnel in front.")
        sys.exit(2)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate = "password required" if PASSWORD else "no password (loopback only)"
    print(f"appraisal rating-guide extraction on http://{HOST}:{PORT}  "
          f"[{gate}]")
    # proxy_headers is on by default and trusts 127.0.0.1, which is where a
    # tunnel connects from -- that is what makes the Secure cookie correct.
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
