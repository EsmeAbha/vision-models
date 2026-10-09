"""A chat-shaped front end for the local vision models.

The Gradio page in app.py and this server drive the same models through the
same code: ensure_model picks the worker, _run_one reads one page. What
changes is the shape of the page -- a thread you add to rather than a form you
fill in -- which Gradio cannot express. So app.py stays as it is and this
serves its own UI instead, importing app.py rather than reimplementing it.

Run it with the same interpreter as app.py:

    python vision_server.py          # http://127.0.0.1:7862

app.py keeps port 7860; the FinAI workspace keeps 7880.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import (FileResponse, HTMLResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# app.py owns the models. Importing it costs a torch import on startup and
# gives us the worker lifecycle, the default prompts and the skills for free.
# Reimplementing any of that is how two front ends start disagreeing about
# what a run actually does.
import app as vision
import doc_preview
import field_search
import fill_template
import pdf_pages
import run_export
import run_history
import save_output

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "vision_web"
PORT = 7862

# What each model gives back, and so which tabs its result card shows. The
# canvas design calls for a third model (Qwen3-VL); app.py does not offer one,
# so it is not listed here -- the sidebar renders whatever this returns, so
# adding it to app.py's MODELS is all it would take to make it appear.
MODEL_META = {
    "paddleocr_vl": {
        "name": "PaddleOCR-VL", "tag": "OCR", "icon": "table",
        "sub": "Layout + table parsing",
        "tabs": [["table", "Rendered table"], ["raw", "Raw"]],
        "steps": ["Started PaddleOCR-VL worker",
                  "Detected layout, recognized text",
                  "Rendered table"],
    },
    "deepseek_ocr": {
        "name": "DeepSeek-OCR", "tag": "OCR", "icon": "doc",
        "sub": "Document to Markdown",
        "tabs": [["annot", "Annotated"], ["raw", "Markdown"]],
        "steps": ["Started DeepSeek-OCR worker",
                  "Converted document to markdown",
                  "Drew grounding boxes"],
    },
}

_runs: dict = {}
_uploads: dict = {}
_state_lock = threading.Lock()

# Both of these used to grow for as long as the server ran. An upload keeps a
# temp folder on disk and a run keeps its whole transcript in memory, so a
# long session leaked both. Finished runs are on disk in run_history/ and are
# re-read from there when one is reopened, so dropping them here costs
# nothing. 642 stale upload folders, 642MB, were sitting in Temp before this.
MAX_UPLOADS = 40
MAX_RUNS = 100
# A loaded model sits on 11-15GB of VRAM and 21-25GB of commit for as long as
# the server runs, whether or not anything is using it. Nothing else on the
# machine can have that memory back while it waits. Letting it go after a
# quiet spell costs the next run a load it would have paid anyway, and
# changes nothing about what comes out.
IDLE_UNLOAD_SECONDS = int(os.environ.get("VISION_IDLE_UNLOAD", "600"))
_last_used = time.time()
# Anything older than this in Temp is from a previous run of the server.
STALE_HOURS = 6


def _evict_uploads():
    """Drop the oldest uploads and delete their folders.

    An upload a run is still reading is left alone, however old it is.
    """
    busy = {r.get("upload") for r in _runs.values()
            if r.get("status") == "running"}
    for fid in list(_uploads)[:-MAX_UPLOADS or None]:
        if len(_uploads) <= MAX_UPLOADS or fid in busy:
            continue
        entry = _uploads.pop(fid)
        shutil.rmtree(entry.get("folder", ""), ignore_errors=True)


def _evict_runs():
    for rid in list(_runs)[:-MAX_RUNS or None]:
        if len(_runs) <= MAX_RUNS:
            break
        if _runs[rid].get("status") == "running":
            continue
        _runs.pop(rid, None)


def _idle_watcher():
    """Unload the model once nothing has used it for a while.

    Takes app.py's lock without blocking: if a run holds it, the model is in
    use and there is nothing to do. Never interrupts work.
    """
    while True:
        time.sleep(30)
        try:
            if vision._state.get("kind") is None:
                continue
            if time.time() - _last_used < IDLE_UNLOAD_SECONDS:
                continue
            if not vision._lock.acquire(blocking=False):
                continue
            try:
                if vision._state.get("kind") is not None:
                    kind = vision._state["kind"]
                    vision.unload_current()
                    print(f"unloaded {kind} after "
                          f"{IDLE_UNLOAD_SECONDS}s idle", flush=True)
            finally:
                vision._lock.release()
        except Exception as e:
            print(f"idle watcher: {type(e).__name__}: {e}", flush=True)


def sweep_temp(hours=STALE_HOURS):
    """Remove upload, page-render and worker-output folders left behind.

    Nothing here survives a restart: an upload is only useful while the run
    that reads it is alive, rendered pages only until they are read, and a
    worker output folder only while its annotated image is on screen.
    """
    cutoff = time.time() - hours * 3600
    root = tempfile.gettempdir()
    removed = 0
    for name in os.listdir(root):
        # ocr_ folders are app.py's: one per inference, holding the
        # annotated image, and nothing has ever deleted them. They are only
        # needed while the run that produced them is on screen, so the same
        # age cutoff applies.
        if not name.startswith(("vision_up_", "vision_pdf_", "ocr_")):
            continue
        path = os.path.join(root, name)
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed

app = FastAPI(docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


# ----------------------------------------------------------------- the page

@app.get("/", response_class=HTMLResponse)
def index():
    """The page, with its assets stamped by their own modification time.

    Without the stamp the browser keeps serving the app.js it already has,
    and a change to the interface looks like it did not happen -- a button
    removed from the source stays on the screen until someone thinks to hard
    refresh. The stamp changes when the file does, so the browser fetches the
    new one and keeps caching the old one the rest of the time.
    """
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        try:
            stamp = int((WEB / name).stat().st_mtime)
        except OSError:
            continue
        html = html.replace(f"/assets/{name}", f"/assets/{name}?v={stamp}")
    return HTMLResponse(html)


# ----------------------------------------------------------- what is on offer

@app.get("/api/models")
def api_models():
    """Every model app.py can actually run, in the order it lists them."""
    out = []
    for label, kind in vision.MODELS.items():
        meta = MODEL_META.get(kind, {})
        out.append({
            "id": kind,
            "label": label,
            "name": meta.get("name", label),
            "sub": meta.get("sub", ""),
            "tag": meta.get("tag", ""),
            "icon": meta.get("icon", "eye"),
            "tabs": meta.get("tabs", [["raw", "Text"]]),
            "prompt": vision.DEFAULT_PROMPTS.get(kind, ""),
            "takes_prompt": kind != "paddleocr_vl",
        })
    return out


@app.get("/api/skills")
def api_skills():
    """The skill files on disk, as cards.

    A skill presets the model and the prompt and nothing else -- that is all
    ocr_skills/*.json claims to do, and all the sidebar does with one.
    """
    out = []
    for cfg in vision.load_skills():
        kind = vision.MODELS[cfg["model"]]
        meta = MODEL_META.get(kind, {})
        out.append({
            "id": cfg["name"].lower().replace(" ", "-").replace("/", "-"),
            "name": cfg["name"],
            "model": kind,
            "model_label": cfg["model"],
            "model_short": meta.get("name", cfg["model"]),
            "takes": cfg.get("takes", ""),
            "gives": cfg.get("gives", ""),
            "speed": cfg.get("speed", ""),
            "note": cfg.get("note", ""),
            "prompt": vision.DEFAULT_PROMPTS.get(kind, ""),
            "icon": meta.get("icon", "eye"),
        })
    return out


@app.get("/api/doc_types")
def api_doc_types():
    """Document types and the fields each one is usually read for.

    Each type names the reader that suits it, so picking a type picks the
    model. The field list is what gets searched for afterwards; it is never
    put to the model (see compose_prompt in app.py for the measurements that
    settled that).
    """
    out = []
    for cfg in vision.load_doc_types():
        reader = vision.MODELS[cfg["reader"]]
        out.append({
            "id": cfg["name"].lower().replace(" ", "-"),
            "name": cfg["name"],
            "reader": reader,
            "reader_label": cfg["reader"],
            "reader_short": MODEL_META.get(reader, {}).get("name", cfg["reader"]),
            "why": cfg.get("why", ""),
            "fields": list(cfg.get("fields") or []),
            # The field names are PEXL's, so a value pulled here goes straight
            # to its API without a translation step in between. This names the
            # document type they belong to on that side.
            "pexl": cfg.get("pexl", ""),
        })
    return out


# -------------------------------------------------------------------- uploads

@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...),
                     source_path: str = Form("")):
    """Take one document. source_path is where it sat before it was uploaded.

    A browser is not allowed to tell a page the folder a picked file came
    from, so this is empty for an ordinary upload and carries a relative path
    only when the file arrived through a folder pick. It is recorded rather
    than guessed at: the Path and Folder Name columns of the fields workbook
    are for whatever reads that next, and a made-up path would be worse there
    than an empty cell.
    """
    name = os.path.basename(file.filename or "upload")
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".pdf"):
        raise HTTPException(400, f"{ext or 'that'} is not an image or a PDF")

    fid = uuid.uuid4().hex
    folder = Path(tempfile.mkdtemp(prefix="vision_up_"))
    path = folder / name
    path.write_bytes(await file.read())

    is_pdf = ext == ".pdf"
    pages = None
    if is_pdf:
        try:
            pages = pdf_pages.page_count(str(path))
        except Exception as e:
            raise HTTPException(400, f"unreadable PDF: {type(e).__name__}: {e}")

    entry = {"id": fid, "name": name, "path": str(path),
             "folder": str(folder), "pdf": is_pdf, "pages": pages,
             "source_path": (source_path or "").strip()}
    with _state_lock:
        _uploads[fid] = entry
        _evict_uploads()
    return {"id": fid, "name": name, "pdf": is_pdf, "pages": pages,
            "preview": None if is_pdf else f"/api/files/{fid}/preview"}


@app.get("/api/files/{fid}/preview")
def api_preview(fid: str):
    with _state_lock:
        entry = _uploads.get(fid)
    if not entry:
        raise HTTPException(404, "no preview for that file")
    # Inline, and PDFs included: the browser draws one better than anything
    # here could, and the panel is for reading the source beside the values
    # pulled out of it.
    return FileResponse(entry["path"], media_type=_MEDIA.get(
        os.path.splitext(entry["name"])[1].lower()),
        headers={"Content-Disposition":
                 f'inline; filename="{os.path.basename(entry["name"])}"'})


# ----------------------------------------------------------------------- runs

class RunReq(BaseModel):
    file_id: str
    model: str
    prompt: str = ""
    pages: str = ""
    dpi: int = 300
    # Which document type was picked, so the fields workbook can order its
    # columns the way that type declares them.
    doc_type: str = ""


@app.post("/api/run")
def api_run(req: RunReq):
    with _state_lock:
        upload = _uploads.get(req.file_id)
    if not upload:
        raise HTTPException(404, "upload it again -- that file is gone")
    if req.model not in MODEL_META:
        raise HTTPException(400, f"unknown model {req.model!r}")

    rid = uuid.uuid4().hex
    run = {"id": rid, "model": req.model, "file": upload["name"],
           "status": "running", "steps": [], "text": "", "annotated": None,
           "error": None, "started": time.time(), "elapsed": None,
           "events": queue.Queue(), "saved": [],
           "fields": [], "field_summary": "", "filled": None,
           # Carried through so the fields workbook can say where the
           # document came from, and the document type so its columns come
           # out in the order that type declares them.
           "source_path": upload.get("source_path", ""),
           "upload": req.file_id,
           "doc_type": req.doc_type or ""}
    global _last_used
    _last_used = time.time()
    run["upload"] = req.file_id
    with _state_lock:
        _runs[rid] = run
        _evict_runs()

    threading.Thread(target=_do_run, args=(rid, upload, req),
                     daemon=True).start()
    return {"id": rid, "steps": MODEL_META[req.model]["steps"]}


def _do_run(rid, upload, req):
    """One run, on a worker thread, holding app.py's GPU lock while it works."""
    with _state_lock:
        run = _runs[rid]
    kind = req.model
    names = MODEL_META[kind]["steps"]

    def step(i, state, note=""):
        entry = {"i": i, "name": names[i], "state": state, "note": note}
        run["steps"] = [s for s in run["steps"] if s["i"] != i] + [entry]
        run["steps"].sort(key=lambda s: s["i"])
        run["events"].put({"type": "step", **entry})

    page_dir = None
    try:
        # Rasterise the PDF BEFORE the model loads, and outside the lock since
        # it needs no GPU. Loading paddle pushes system commit to its ceiling
        # (its layout model sits in RAM, not VRAM), and pypdfium2's bitmap
        # buffer is what dies there: a 34MB allocation failing with
        # MemoryError while Windows is still growing a lazily-sized page file.
        # Rendering first keeps that allocation out of the spike, which is the
        # difference between the first run working and needing a retry.
        pages, page_dir = (_render_pdf_pages(upload, req) if upload["pdf"]
                           else (None, None))

        # Serialises runs started here -- one GPU, one at a time. It does NOT
        # reach a separate app.py process: a threading.Lock is per-process, so
        # if the Gradio page is also serving, the two can still collide over
        # vLLM and VRAM. Run one front end or the other (see the note in
        # __main__), not both.
        with vision._lock:
            step(0, "active")
            vision.ensure_model(kind)
            step(0, "done")

            step(1, "active")
            t0 = time.time()
            if pages is not None:
                text, annotated = _read_pages(pages, req, kind)
            else:
                text, annotated = vision._run_one(kind, upload["path"],
                                                  req.prompt or "")
            step(1, "done", f"{time.time() - t0:.1f}s, {len(text or '')} chars")

            step(2, "active")
            run["text"] = text or ""
            run["annotated"] = annotated
            step(2, "done")

        run["status"] = "done"
        run["elapsed"] = time.time() - run["started"]
        globals()["_last_used"] = time.time()
        run_history.record(run, MODEL_META.get(kind, {}).get("name", kind))
        run["events"].put({"type": "done", "elapsed": run["elapsed"]})
    except Exception as e:
        run["status"] = "error"
        run["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        run["events"].put({"type": "error", "error": run["error"]})
    finally:
        if page_dir:
            shutil.rmtree(page_dir, ignore_errors=True)
        run["events"].put({"type": "end"})


def _render_pdf_pages(upload, req):
    """Rasterise the pages we were asked for, as (page number, png path).

    render_pdf is a generator of (page number, png path, info) and parses the
    page spec itself, so the spec goes straight through -- parsing it here as
    well would hand it a list where it expects '1-3'.
    """
    out_dir = tempfile.mkdtemp(prefix="vision_pdf_")
    try:
        rendered = list(pdf_pages.render_pdf(upload["path"], out_dir,
                                             dpi=req.dpi, pages=req.pages))
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    if not rendered:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise RuntimeError("that PDF rendered no pages")
    # The caller deletes out_dir once the pages have been read; they are only
    # the way in, and 45 of these folders had been left behind in Temp.
    return [(n, p) for n, p, _ in rendered], out_dir


def _read_pages(pages, req, kind):
    """Read already-rendered page images, joined with page markers."""
    numbers = [n for n, _ in pages]
    images = [p for _, p in pages]

    if kind == "paddleocr_vl":
        # PaddleOCR-VL reads a whole batch in one worker call.
        texts = vision._ocr_request_batch(images)
    else:
        texts = [vision._run_one(kind, p, req.prompt or "")[0] for p in images]

    # save_output.page_marker is the canonical form. Writing our own meant
    # split_pages did not recognise it, so a twelve page PDF came back as one
    # page: one row of fields instead of twelve, and one sheet in the workbook.
    parts = [f"{save_output.page_marker('', n)}\n{body}"
             for n, body in zip(numbers, texts)]
    return "\n\n".join(parts), None


@app.get("/api/runs/{rid}/events")
def api_events(rid: str):
    with _state_lock:
        run = _runs.get(rid)
    if not run:
        raise HTTPException(404, "no such run")

    def stream():
        # Replay what already happened, so a late listener still sees it.
        for entry in list(run["steps"]):
            yield _sse({"type": "step", **entry})
        if run["status"] != "running":
            yield _sse({"type": run["status"], "error": run["error"],
                        "elapsed": run["elapsed"]})
            return
        while True:
            try:
                payload = run["events"].get(timeout=30)
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


@app.get("/api/runs/{rid}")
def api_run_state(rid: str):
    with _state_lock:
        run = _runs.get(rid)
    if not run:
        raise HTTPException(404, "no such run")
    return {
        "id": rid, "model": run["model"], "file": run["file"],
        "status": run["status"], "steps": run["steps"], "text": run["text"],
        "error": run["error"], "elapsed": run["elapsed"],
        "annotated": f"/api/runs/{rid}/annotated" if run["annotated"] else None,
        "saved": [{"i": i, "name": os.path.basename(p)}
                  for i, p in enumerate(run["saved"])],
        "fields": run["fields"], "field_summary": run["field_summary"],
    }


# ----------------------------------------------------------- plain questions

# The OCR models on this page read documents; they do not converse. A question
# typed with nothing attached used to go nowhere at all. This answers it with
# the local text model, on the same machine, so the box is useful for "what
# does a cap rate mean" as well as for reading a page.
CHAT_MODEL = os.environ.get("VISION_CHAT_MODEL", "gpt-oss-64k:latest")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

CHAT_SYSTEM = (
    "You are a careful assistant inside a document-reading tool, answering on "
    "a local machine. Answer the question directly and briefly.\n\n"
    "You cannot see any document here: this is the plain-question path, and no "
    "file was attached. If the question is about a specific document, say that "
    "it needs to be attached and read first rather than guessing at its "
    "contents. Never invent a figure, a date or a quotation."
)

# The same, for a thread that has a document in it. The transcript is sent
# with the question, so the honest instruction is the opposite one: read it,
# quote it, and say when it does not contain the answer.
CHAT_SYSTEM_DOC = (
    "You are a careful assistant inside a document-reading tool, answering on "
    "a local machine. Answer the question directly and briefly.\n\n"
    "The transcript of the document the user is looking at is given below. "
    "Answer from it. Quote the figure or the line as it is printed rather "
    "than rephrasing it, and if the transcript does not contain the answer, "
    "say so plainly. Never invent a figure, a date or a quotation, and never "
    "fill a gap in the transcript from general knowledge."
)

# Ollama is asked for an 8k window, and going over it does not fail -- it
# silently drops messages from the front, which is where the system prompt
# sits. So the window is spent on purpose instead: roughly four characters to
# a token, a reserve for the answer, and the rest shared between the document
# and the history with the newest turns kept first.
CHARS_PER_TOKEN = 4
ANSWER_RESERVE_TOKENS = 1200
DOC_SHARE = 0.55            # of what is left after the system prompt


def _fit_history(history, budget):
    """The most recent turns that fit, in the order they were said.

    Walked newest first so that what survives a tight budget is the part of
    the conversation the next message actually refers to. A turn too long to
    fit on its own is cut rather than dropped, because losing an answer
    entirely is what makes a follow-up like "rewrite that" unanswerable.
    """
    kept, used = [], 0
    for turn in reversed(history or []):
        role = turn.get("role")
        body = (turn.get("content") or "").strip()
        if role not in ("user", "assistant") or not body:
            continue
        if used + len(body) > budget:
            room = budget - used
            if room < 400:
                break
            body = body[-room:]
        kept.append({"role": role, "content": body})
        used += len(body)
    kept.reverse()
    return kept


def _build_messages(prompt, history, document, doc_name):
    """System prompt, document, history and question -- inside the window."""
    ctx = int(os.environ.get("VISION_CHAT_CTX", "8192"))
    total = max(1000, (ctx - ANSWER_RESERVE_TOKENS)) * CHARS_PER_TOKEN

    system = CHAT_SYSTEM_DOC if document else CHAT_SYSTEM
    question = prompt[:8000]
    room = total - len(system) - len(question)

    doc_text, prefix = "", ""
    if document:
        # The line introducing the transcript is part of what gets sent, so it
        # comes out of the allowance too. Counting only the transcript put the
        # request over the window by exactly the length of this label -- and
        # going over is not an error, it drops the system prompt.
        prefix = "Transcript of %s:\n\n" % (doc_name or "the document")
        allowance = max(0, int(room * DOC_SHARE) - len(prefix))
        doc_text = document.strip()
        if len(doc_text) > allowance:
            # The head of a document carries the labels and the summary; the
            # tail is usually the detail tables. Keeping both ends and saying
            # what was dropped beats a silent cut in the middle.
            half = allowance // 2
            doc_text = (doc_text[:half] + "\n\n[... part of this document is "
                        "not shown: it is longer than this window ...]\n\n"
                        + doc_text[-half:])
        room -= len(doc_text) + len(prefix)

    messages = [{"role": "system", "content": system}]
    if doc_text:
        messages.append({"role": "system", "content": prefix + doc_text})
    messages += _fit_history(history, max(room, 0))
    messages.append({"role": "user", "content": question})
    return messages


@app.get("/api/chat_model")
def api_chat_model():
    """Which local model answers typed questions, and whether it is up.

    Exposed so the sidebar can name it rather than the page asserting a
    privacy claim nobody can check. If the service is down, the page should
    say the questions will not work -- not promise they stay local.
    """
    import requests as _rq

    try:
        r = _rq.get(f"{OLLAMA_URL}/api/tags", timeout=4)
        names = [m["name"] for m in r.json().get("models", [])] if r.ok else []
    except Exception:
        return {"model": CHAT_MODEL, "available": False, "where": OLLAMA_URL}
    return {"model": CHAT_MODEL, "available": CHAT_MODEL in names,
            "where": OLLAMA_URL}


class ChatReq(BaseModel):
    prompt: str
    history: list = []
    # The transcript of the document this thread is about, when there is one.
    # A chat is one document plus what was asked about it, so a question like
    # "what is the account number" has something to be answered from.
    document: str = ""
    document_name: str = ""


@app.post("/api/chat")
def api_chat(req: ChatReq):
    """Answer a typed question with the local text model."""
    import requests as _rq

    text = (req.prompt or "").strip()
    if not text:
        raise HTTPException(400, "nothing to answer")

    messages = _build_messages(text, req.history, req.document or "",
                               req.document_name or "")

    try:
        r = _rq.post(f"{OLLAMA_URL}/api/chat", timeout=600, json={
            "model": CHAT_MODEL, "messages": messages, "stream": False,
            "options": {
                # Greedy decoding with a fixed seed was making "rewrite
                # that, more casual" return almost exactly what it returned
                # the first time. Nothing checkable depends on this being
                # deterministic -- field extraction calls no model at all, it
                # matches labels in the transcript -- so the answer is allowed
                # a little room to differ when the question asks it to.
                "temperature": float(os.environ.get("VISION_CHAT_TEMP", "0.3")),
                "top_p": 0.9,
                # The model's own default is a 64k window, and reserving the
                # KV cache for it needs 1.6GB on top of 13.8GB of weights --
                # which failed outright with "cudaMalloc failed ... kv cache"
                # whenever anything else held a few GB of the card. A typed
                # question and eight turns of history do not need 64k, and
                # asking for what is actually used leaves room for the OCR
                # models to coexist.
                "num_ctx": int(os.environ.get("VISION_CHAT_CTX", "8192")),
            },
        })
    except _rq.exceptions.ConnectionError:
        raise HTTPException(503, f"the local model service is not answering on "
                                 f"{OLLAMA_URL}. Start Ollama and try again.")
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")

    if not r.ok:
        raise HTTPException(502, f"the local model returned {r.status_code}. "
                                 f"If that is 500, the GPU may be full.")
    msg = (r.json().get("message") or {})
    answer = (msg.get("content") or "").strip()
    if not answer:
        # Reasoning models can spend the whole budget thinking and return an
        # empty content field. Half an answer beats a blank bubble.
        answer = (msg.get("thinking") or "").strip()
    if not answer:
        raise HTTPException(502, "the local model returned nothing")
    return {"answer": answer, "model": CHAT_MODEL}


class FieldsReq(BaseModel):
    fields: list = []
    extra: str = ""


@app.post("/api/runs/{rid}/fields")
def api_fields(rid: str, req: FieldsReq):
    """Pull the chosen fields out of a finished run's transcript.

    No model is called. The value comes off the page or it is reported
    missing, and every row carries the label that matched and where the value
    sat relative to it -- so a wrong answer reads as a wrong answer instead of
    a confident number. Same grading as the Gradio page's pull_fields.
    """
    with _state_lock:
        run = _runs.get(rid)
    if not run:
        raise HTTPException(404, "no such run")
    if not (run["text"] or "").strip():
        raise HTTPException(400, "that run produced no text to search")

    wanted = [f for f in (req.fields or []) if str(f).strip()]
    wanted += vision.split_fields(req.extra)
    if not wanted:
        raise HTTPException(400, "choose at least one field")

    # One page at a time, because one document is not one record. Twelve
    # monthly bills in a twelve page PDF are twelve bills, and searching the
    # whole transcript at once returns the first match for every field -- one
    # row, every value from page 1, the other eleven silently gone.
    pages = save_output.split_pages(run["text"]) or [("page 1", run["text"])]

    rows, missing, odd = [], 0, 0
    for number, (label, content) in enumerate(pages, 1):
        for r in field_search.find_fields(content, wanted):
            if not r["found"]:
                missing += 1
                verdict = "none"
            elif r.get("guessed"):
                verdict = "guess"
            elif r["shape_ok"]:
                verdict = "yes"
            else:
                odd += 1
                verdict = "check"
            where = r["evidence"].split("[")[-1].rstrip("]") if r["evidence"] else ""
            rows.append({"field": r["field"], "value": r["value"] or "",
                         "verdict": verdict, "where": where,
                         "evidence": r["evidence"] or "",
                         "page": number, "page_label": label})

    found_count = len(rows) - missing
    parts = [f"{found_count} of {len(rows)} found"
             + (f" across {len(pages)} pages." if len(pages) > 1 else ".")]
    if missing:
        parts.append(f"{missing} label(s) not printed on the page.")
    if odd:
        parts.append(f"{odd} value(s) marked CHECK: found, but not the shape the "
                     f"field expects, often a table that has slipped a row.")
    summary = " ".join(parts)

    run["fields"] = rows
    run["field_summary"] = summary
    run_history.record(run, MODEL_META.get(run["model"], {}).get("name", ""))
    return {"rows": rows, "summary": summary}


@app.get("/api/templates")
def api_templates(doc_type: str = ""):
    """Spreadsheets a finished run's fields can be dropped into."""
    out = []
    for t in fill_template.load_templates():
        if doc_type and t["doc_type"] != doc_type:
            continue
        out.append({"id": t["id"], "name": t["name"],
                    "doc_type": t["doc_type"], "workbook": t["workbook"],
                    "sheet": t.get("sheet", ""),
                    "fields": list(t["cells"].keys()),
                    "as_number": list(t.get("as_number") or [])})
    return out


class TemplateReq(BaseModel):
    template: str


@app.post("/api/runs/{rid}/template")
def api_fill_template(rid: str, req: TemplateReq):
    """Fill a copy of the template with this run's fields.

    The blank workbook is never written to. A field the run did not find is
    left empty rather than guessed, and the reply says which cells were
    written and which were not, so a half-read document is visibly half
    filled instead of quietly wrong.
    """
    with _state_lock:
        run = _runs.get(rid)
    if not run:
        raise HTTPException(404, "no such run")
    if not run["fields"]:
        raise HTTPException(400, "pull the fields out first")

    chosen = next((t for t in fill_template.load_templates()
                   if t["id"] == req.template), None)
    if not chosen:
        raise HTTPException(404, f"no template {req.template!r}")

    values = {r["field"]: r["value"] for r in run["fields"] if r["value"]}
    stem = os.path.splitext(run["file"])[0]
    try:
        path, written, skipped = fill_template.fill(
            chosen, values, out_dir=vision.OUTPUT_DIR, stem=stem)
    except Exception as e:
        raise HTTPException(500, str(e))

    run["filled"] = path
    return {"file": os.path.basename(path), "written": written,
            "skipped": skipped,
            "download": f"/api/runs/{rid}/filled"}


@app.get("/api/runs/{rid}/filled")
def api_filled(rid: str):
    with _state_lock:
        run = _runs.get(rid)
    if not run or not run.get("filled"):
        raise HTTPException(404, "fill a template first")
    return FileResponse(run["filled"],
                        filename=os.path.basename(run["filled"]))


def _all_fields():
    """Every field any document type asks for, in a stable order.

    The scan needs a vocabulary to look for in a supplied workbook, and the
    document types are already the statement of what this project reads.
    """
    seen, out = set(), []
    for cfg in vision.load_doc_types():
        for field in cfg.get("fields") or []:
            if field not in seen:
                seen.add(field)
                out.append(field)
    return out


@app.get("/api/history")
def api_history(limit: int = 60):
    """Documents read in earlier sessions, newest first."""
    return run_history.summaries(limit=max(1, min(limit, 200)))



class ThreadReq(BaseModel):
    """A conversation as the page holds it, on its way to disk."""
    id: str
    title: str = ""
    turns: list = []


@app.post("/api/history/thread")
def api_save_thread(req: ThreadReq):
    """Write down a typed conversation so a reload does not lose it.

    Called after every answer. A thread that read a document already has a
    record under the run's id, and this adds the turns to it; a thread with no
    document gets a record of its own, which is the only way one was ever
    going to survive the page being refreshed.
    """
    rid = (req.id or "").strip()
    if not run_history._ID_OK.match(rid):
        raise HTTPException(400, "that is not a thread id")

    turns = []
    for t in (req.turns or [])[-80:]:
        question = (t.get("question") or "").strip()
        answer = (t.get("answer") or "").strip()
        if question or answer:
            turns.append({"question": question[:20000],
                          "answer": answer[:40000],
                          "model": (t.get("model") or "")[:80]})

    with _state_lock:
        run = dict(_runs.get(rid) or {})
    existing = run_history.load(rid) or {}
    # Keep whatever the record already knows: a thread saved after a read must
    # not overwrite the transcript with nothing.
    run.setdefault("id", rid)
    run["status"] = "done"
    run["turns"] = turns
    run["title"] = (req.title or existing.get("title") or "").strip()[:120]
    if not run.get("text"):
        run["text"] = existing.get("text", "")
    if not run.get("file"):
        run["file"] = existing.get("file", "")
    if not run.get("model"):
        run["model"] = existing.get("model", "")

    name = MODEL_META.get(run.get("model") or "", {}).get("name", "")
    run_history.record(run, name or existing.get("model_name", ""))
    return {"saved": rid, "turns": len(turns)}


@app.get("/api/history/{rid}")
def api_history_one(rid: str):
    """One remembered run, transcript and all.

    Reopening reads from here rather than re-running: the text was already
    paid for with a model load, and the upload it came from is long gone.
    """
    entry = run_history.load(rid)
    if entry is None:
        raise HTTPException(404, "that run is not remembered")
    # Put it back in the live table so the fields and template actions on it
    # work exactly as they do for a run from this session.
    with _state_lock:
        if rid not in _runs:
            _runs[rid] = {
                "id": rid, "model": entry["model"], "file": entry["file"],
                "turns": entry.get("turns") or [],
                "title": entry.get("title", ""),
                "status": "done", "steps": [], "text": entry["text"],
                "annotated": entry.get("annotated"), "error": None,
                "started": 0, "elapsed": entry.get("elapsed"),
                "events": queue.Queue(), "saved": [],
                "fields": entry.get("fields") or [],
                "field_summary": entry.get("field_summary", ""),
                "filled": None,
            }
    entry["annotated"] = (f"/api/runs/{rid}/annotated"
                          if entry.get("annotated") else None)
    return entry


@app.delete("/api/history/{rid}")
def api_forget(rid: str):
    if not run_history.forget(rid):
        raise HTTPException(404, "that run is not remembered")
    return {"forgotten": rid}


@app.get("/api/runs/{rid}/annotated")
def api_annotated(rid: str):
    with _state_lock:
        run = _runs.get(rid)
    if not run or not run["annotated"]:
        raise HTTPException(404, "that run drew no boxes")
    return FileResponse(run["annotated"])


@app.post("/api/runs/{rid}/save")
def api_save(rid: str):
    """Write the result to .md / .html / .xlsx, the same way app.py does."""
    with _state_lock:
        run = _runs.get(rid)
    if not run:
        raise HTTPException(404, "no such run")
    if not (run["text"] or "").strip():
        raise HTTPException(400, "nothing to save yet")
    try:
        files = save_output.save_all(run["text"], base_name=run["file"],
                                     out_dir=vision.OUTPUT_DIR)
    except Exception as e:
        raise HTTPException(500, f"save failed: {type(e).__name__}: {e}")
    run["saved"] = files
    return {"saved": [{"i": i, "name": os.path.basename(p)}
                      for i, p in enumerate(files)]}



def _doc_type_fields(doc_type):
    """The field order a document type declares, or [] if it names none."""
    for cfg in vision.load_doc_types():
        if cfg["name"].lower().replace(" ", "-") == doc_type:
            return list(cfg.get("fields") or [])
    return []


XLSX_TYPE = ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet")


def _export_name(run, kind):
    stem = os.path.splitext(os.path.basename(run.get("file") or "run"))[0]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "run"
    return f"{safe}_{kind}_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"


@app.get("/api/runs/{rid}/export/fields")
def api_export_fields(rid: str):
    """The scraped values as a workbook: one row per document, one per field.

    Nothing is worked out here. The cells carry what was found on the page,
    in the document type's own field order, for another tool's API to read.
    """
    with _state_lock:
        run = _runs.get(rid)
    if not run:
        raise HTTPException(404, "no such run")
    if not run.get("fields"):
        raise HTTPException(400, "pull the fields out first")

    order = _doc_type_fields(run.get("doc_type") or "")

    name = _export_name(run, "fields")
    path = os.path.join(vision.OUTPUT_DIR, name)
    run_export.fields_workbook(
        [{"path": run.get("source_path") or "", "file": run.get("file", ""),
          "rows": run["fields"]}], path, fields=order)
    return FileResponse(path, filename=name, media_type=XLSX_TYPE)


@app.get("/api/runs/{rid}/export/tables")
def api_export_tables(rid: str):
    """The rendered table as a workbook, a sheet per table, as it was read."""
    with _state_lock:
        run = _runs.get(rid)
    if not run:
        raise HTTPException(404, "no such run")
    if not (run.get("text") or "").strip():
        raise HTTPException(400, "that run produced nothing to export")

    name = _export_name(run, "tables")
    path = os.path.join(vision.OUTPUT_DIR, name)
    try:
        run_export.tables_workbook(run["text"], path)
    except Exception as e:
        raise HTTPException(500, f"export failed: {type(e).__name__}: {e}")
    return FileResponse(path, filename=name, media_type=XLSX_TYPE)



_MEDIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp",
          ".gif": "image/gif", ".html": "text/html", ".htm": "text/html"}


def _resolve_view(src):
    """Turn a view descriptor into (path, display name).

    Descriptors name something the server already knows about -- an upload, a
    run's export, a file it saved -- and are resolved against that state. A
    path never arrives from the page, so the panel cannot be pointed at an
    arbitrary file on this machine.
    """
    parts = (src or "").split(":")
    kind = parts[0] if parts else ""

    if kind == "upload" and len(parts) == 2:
        with _state_lock:
            entry = _uploads.get(parts[1])
        if not entry:
            raise HTTPException(404, "that upload is no longer held")
        return entry["path"], entry["name"]

    if kind == "run" and len(parts) == 2:
        with _state_lock:
            run = _runs.get(parts[1])
        if not run:
            raise HTTPException(404, "no such run")
        with _state_lock:
            entry = _uploads.get(run.get("upload") or "")
        if not entry:
            raise HTTPException(404, "the document this run read is no "
                                     "longer held; upload it again to see it")
        return entry["path"], entry["name"]

    if kind == "export" and len(parts) == 3:
        rid, what = parts[1], parts[2]
        with _state_lock:
            run = _runs.get(rid)
        if not run:
            raise HTTPException(404, "no such run")
        name = _export_name(run, what)
        path = os.path.join(vision.OUTPUT_DIR, name)
        if what == "fields":
            if not run.get("fields"):
                raise HTTPException(400, "pull the fields out first")
            order = _doc_type_fields(run.get("doc_type") or "")
            run_export.fields_workbook(
                [{"path": run.get("source_path") or "",
                  "file": run.get("file", ""), "rows": run["fields"]}],
                path, fields=order)
        elif what == "tables":
            run_export.tables_workbook(run.get("text") or "", path)
        else:
            raise HTTPException(400, f"unknown export {what!r}")
        return path, name

    if kind == "saved" and len(parts) == 3:
        with _state_lock:
            run = _runs.get(parts[1])
        try:
            idx = int(parts[2])
        except ValueError:
            raise HTTPException(400, "bad file index")
        if not run or idx >= len(run.get("saved") or []):
            raise HTTPException(404, "no such saved file")
        path = run["saved"][idx]
        return path, os.path.basename(path)

    raise HTTPException(400, "that is not something this page can show")


@app.get("/api/view")
def api_view(src: str = ""):
    """One file, ready for an iframe.

    A PDF or an image is sent as itself and the browser draws it. Everything
    else is rendered to HTML first, so a single iframe in the page can show
    any of them without needing to know which it is.
    """
    path, name = _resolve_view(src)
    if not os.path.exists(path):
        raise HTTPException(404, "that file is no longer on disk")

    if doc_preview.kind_of(name) == "raw":
        return FileResponse(path, media_type=_MEDIA.get(
            os.path.splitext(name)[1].lower()),
            headers={"Content-Disposition": f'inline; filename="{name}"'})
    return HTMLResponse(doc_preview.to_html(path, name))


@app.get("/api/runs/{rid}/download/{idx}")
def api_download(rid: str, idx: int):
    with _state_lock:
        run = _runs.get(rid)
    if not run or idx >= len(run["saved"]):
        raise HTTPException(404, "save it first")
    path = run["saved"][idx]
    return FileResponse(path, filename=os.path.basename(path))


def _gradio_is_serving(timeout=1.5):
    """Is app.py's own page already up on 7860?"""
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:7860/", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # answering at all is enough
    except Exception:
        return False


if __name__ == "__main__":
    import uvicorn
    threading.Thread(target=_idle_watcher, daemon=True).start()
    swept = sweep_temp()
    if swept:
        print(f"removed {swept} stale upload/page folder(s) from Temp")
    if _gradio_is_serving():
        # Both front ends load weights onto the same card and both manage the
        # same WSL vLLM server, and nothing coordinates them across processes.
        # Whichever starts a run second can kill the other's worker mid-read.
        print("WARNING: app.py is already serving on 7860. Both front ends "
              "drive the same GPU and the same vLLM server, and they cannot "
              "coordinate across processes -- stop one before running the "
              "other.")
    try:
        print(f"local vision UI on http://127.0.0.1:{PORT}")
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    finally:
        vision._stop_ocr_worker()
