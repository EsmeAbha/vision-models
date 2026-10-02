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
import deal_book
import field_search
import fill_template
import pdf_pages
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


def sweep_temp(hours=STALE_HOURS):
    """Remove upload and page-render folders left by an earlier server.

    Nothing here survives a restart: an upload is only useful while the run
    that reads it is alive, and rendered pages only until they are read.
    """
    cutoff = time.time() - hours * 3600
    root = tempfile.gettempdir()
    removed = 0
    for name in os.listdir(root):
        if not name.startswith(("vision_up_", "vision_pdf_")):
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
    return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))


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
        })
    return out


# -------------------------------------------------------------------- uploads

@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
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
             "folder": str(folder), "pdf": is_pdf, "pages": pages}
    with _state_lock:
        _uploads[fid] = entry
        _evict_uploads()
    return {"id": fid, "name": name, "pdf": is_pdf, "pages": pages,
            "preview": None if is_pdf else f"/api/files/{fid}/preview"}


@app.get("/api/files/{fid}/preview")
def api_preview(fid: str):
    with _state_lock:
        entry = _uploads.get(fid)
    if not entry or entry["pdf"]:
        raise HTTPException(404, "no preview for that file")
    return FileResponse(entry["path"])


# ----------------------------------------------------------------------- runs

class RunReq(BaseModel):
    file_id: str
    model: str
    prompt: str = ""
    pages: str = ""
    dpi: int = 300


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
           "fields": [], "field_summary": "", "filled": None}
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

    parts = [f"<!-- page {n} -->\n{body}" for n, body in zip(numbers, texts)]
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

    rows, missing, odd = [], 0, 0
    for r in field_search.find_fields(run["text"], wanted):
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
                     "evidence": r["evidence"] or ""})

    parts = [f"{len(rows) - missing} of {len(rows)} found."]
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


@app.get("/api/deals")
def api_deals():
    return [{"id": d["id"], "name": d["name"], "created": d.get("created", ""),
             "confirmed": d.get("confirmed", False),
             "mapped": len(d.get("mapping") or []),
             "filled": len(d.get("filled") or {}),
             "documents": len(d.get("history") or [])}
            for d in deal_book.list_deals()]


@app.post("/api/deals")
async def api_create_deal(name: str = Form(...), file: UploadFile = File(...)):
    """Start a deal from a workbook you supply, and propose where fields go.

    Nothing is written yet. The proposal comes back for checking, and the
    mapping that gets stored is whatever is confirmed afterwards.
    """
    if not (name or "").strip():
        raise HTTPException(400, "the deal needs a name")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "the template has to be an .xlsx workbook")
    try:
        deal = deal_book.create_deal(name.strip(), await file.read(),
                                     _all_fields())
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return deal


@app.get("/api/deals/{deal_id}")
def api_deal(deal_id: str):
    deal = deal_book.load_deal(deal_id)
    if deal is None:
        raise HTTPException(404, f"no deal {deal_id!r}")
    return deal


class MappingReq(BaseModel):
    mapping: list


@app.put("/api/deals/{deal_id}/mapping")
def api_set_mapping(deal_id: str, req: MappingReq):
    """Store the mapping as corrected. Later documents reuse this one."""
    try:
        return deal_book.set_mapping(deal_id, req.mapping)
    except FileNotFoundError:
        raise HTTPException(404, f"no deal {deal_id!r}")
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


class ApplyReq(BaseModel):
    run: str


@app.post("/api/deals/{deal_id}/apply")
def api_apply_to_deal(deal_id: str, req: ApplyReq):
    """Put one document's fields into the deal's workbook."""
    with _state_lock:
        run = _runs.get(req.run)
    if not run:
        raise HTTPException(404, "no such run")
    if not run["fields"]:
        raise HTTPException(400, "pull the fields out first")

    values = {r["field"]: r["value"] for r in run["fields"] if r["value"]}
    try:
        out = deal_book.apply_values(deal_id, values, source=run["file"])
    except FileNotFoundError:
        raise HTTPException(404, f"no deal {deal_id!r}")
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    out["download"] = f"/api/deals/{deal_id}/workbook"
    out.pop("workbook", None)
    return out


@app.get("/api/deals/{deal_id}/workbook")
def api_deal_workbook(deal_id: str):
    deal = deal_book.load_deal(deal_id)
    if deal is None:
        raise HTTPException(404, f"no deal {deal_id!r}")
    path = deal_book.deal_path(deal_id, deal_book.WORKING_NAME)
    if not os.path.isfile(path):
        raise HTTPException(404, "this deal has no workbook yet")
    return FileResponse(path, filename=f"{deal_id}.xlsx")


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
