"""Let the agent write and run code, so it is not limited to tools I foresaw.

Seven fixed tools means seven things. Asked to split three pages out of a PDF
-- an ordinary request -- the agent answered "I don't have a tool that can
write a new PDF file", which is honest and useless, and is the same shape of
failure as a model with no tools at all: it knows what it cannot do and has no
way to do anything about it.

An assistant that handles arbitrary work does not have a tool per task. It has
one general tool and writes the rest. That is the whole difference, and it is
what this adds.

The loop matters as much as the execution. Code fails on the first try often,
and the traceback comes back to the model so it can correct and run again --
the same way a person works. A tool that only reported "error" would be
useless; the error IS the useful part.

WHAT THIS IS
    Arbitrary Python, running as you, on this machine, with your files. That
    is the point of it and also the risk of it. It is run in a separate
    process with a timeout so it cannot hang the page, and every run is
    logged, but nothing here sandboxes it: code that can write a PDF can
    delete one. Because the page can be served on the network, the setting
    FINAI_ALLOW_CODE must be switched on deliberately rather than defaulting
    on, and it should stay off on any network you do not control.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime

_here = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(_here, "outputs", "code_runs.log")
PYTHON = os.path.join(_here, ".venv", "Scripts", "python.exe")
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable

# Off unless switched on. This executes whatever the model writes, and the
# page it serves can be reachable from the network.
ENABLED = os.environ.get("FINAI_ALLOW_CODE", "").lower() in ("1", "true", "yes")

PREAMBLE = """\
import sys, os, json, re, math, csv, glob, shutil, zipfile, datetime
sys.path.insert(0, r'{root}')
os.chdir(r'{root}')
"""


def _log(code, result):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} "
                     f"({result.get('seconds')}s, ok={result.get('ok')})\n")
            fh.write(code.strip()[:4000] + "\n")
            if result.get("stderr"):
                fh.write("--- stderr\n" + result["stderr"][:1500] + "\n")
    except OSError:
        pass


def run_python(code: str, timeout: int = 180, **_):
    """Run Python and return whatever it printed, or the error it raised."""
    if not ENABLED:
        return {"error": "Running code is switched off. The person who "
                         "started this page must set FINAI_ALLOW_CODE=1. Do "
                         "not pretend to run it; tell the user it is off."}
    if not (code or "").strip():
        return {"error": "No code given."}

    src = PREAMBLE.format(root=_here) + code
    tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                      encoding="utf-8")
    try:
        tmp.write(src)
        tmp.close()
        t0 = time.time()
        try:
            proc = subprocess.run([PYTHON, "-u", tmp.name],
                                  capture_output=True, text=True,
                                  timeout=max(5, min(int(timeout), 900)),
                                  cwd=_here, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            result = {"ok": False, "timed_out": True,
                      "error": f"The code was still running after {timeout}s "
                               f"and was stopped. Make it do less, or work on "
                               f"fewer pages at a time.",
                      "seconds": timeout}
            _log(code, result)
            return result

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        result = {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            # Truncated from the END: a traceback's last lines say what went
            # wrong, and those are the lines worth keeping.
            "stdout": out[-6000:],
            "stderr": err[-3000:],
            "seconds": round(time.time() - t0, 1),
        }
        if not out and proc.returncode == 0:
            result["note"] = ("The code ran but printed nothing. Print what "
                              "you want to see -- results are not returned "
                              "automatically.")
        _log(code, result)
        return result
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


HELP = """Write and run Python on this machine. Use it for anything the other tools do not cover: splitting or merging PDFs, renaming files, charting, converting formats, arithmetic over many files, anything.

- print() what you want to see. Nothing is returned automatically.
- OPEN FILES FROM DISK inside your code. Never paste a document's contents
into the code as a string literal: it gets truncated, and you end up
processing a fraction of the data while believing you have all of it. Use
openpyxl.load_workbook(path) or pandas.read_excel(path) and iterate every row.
- Before writing output, print how many rows you are about to write and check
it against how many the source has. A count that does not match is the error
showing itself.
- Installed: pypdf (read AND write PDFs -- splitting, merging), pdfplumber (text and layout), pypdfium2 (rendering to images), openpyxl, pandas, python-docx, python-pptx, PIL, requests, numpy. PyPDF2, fitz/pymupdf and reportlab are NOT installed; use pypdf instead of guessing.
- The project folder is the working directory and is on sys.path, so the project's own modules can be imported (readers, ai_extract, engine.*).
- If it fails you get the traceback. Read it, fix the code, run it again.
- Writing Excel: store numbers as NUMBERS, not strings. float('12666.54'),
not '12666.54'. A sheet of numeric-looking text looks correct and is useless:
SUM returns zero, sorting goes alphabetical, and formulas referring to it are
fragile. Same for dates -- use datetime, or leave them as text deliberately
and say so.
- Write output files under outputs/ and tell the user the path."""
