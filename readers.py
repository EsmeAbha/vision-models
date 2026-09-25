"""Read a document -- any document -- and give back its text.

The first version of this agent could only pull tables out of financial
reports. Handed a CV it answered "no table or structured data was found, no
further action is needed", which is a useless thing to say to someone who
asked what a document says. Reading a file is the ordinary case; extracting a
table is the special one.

So this reads what is actually there: PDF text layers, Word, PowerPoint,
Excel, plain text, CSV, JSON. When a PDF has no text layer the pages are
rendered and read by the local vision model, which needs no permission and no
separate OCR server -- it is the same Ollama already answering, and nothing
leaves the machine.

That last point matters more than it looks. OCR used to mean unloading the
language model and bringing vLLM up in WSL, which is why it was something a
person had to allow. Reading a scan through the vision model costs a page of
inference, so there is nothing left to ask permission for: if a document
cannot be read any other way, it is read this way.
"""
from __future__ import annotations

import base64
import io
import json
import os

import requests

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
# One model for everything. Reading a scan needs vision, and not every model
# has it -- but quietly loading a second one to cover the gap is worse than
# the gap: on a 16GB card it evicts the first, costs a minute, and sometimes
# fails the load outright with a 500. If the chosen model cannot see, that is
# reported, not worked around behind the user's back.
VISION_MODEL = os.environ.get("FINAI_MODEL", "gpt-oss-64k:latest")

_CAN_SEE = {}


def model_can_see(model=None):
    """Does the configured model accept images? Asked once, then remembered."""
    model = model or VISION_MODEL
    if model in _CAN_SEE:
        return _CAN_SEE[model]
    try:
        r = requests.post(f"{OLLAMA}/api/show", json={"model": model}, timeout=30)
        caps = r.json().get("capabilities", []) if r.ok else []
        _CAN_SEE[model] = "vision" in caps
    except Exception:
        _CAN_SEE[model] = False
    return _CAN_SEE[model]


def vision_models():
    """Which installed models could read a scan, for telling the user."""
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=30)
        return [m["name"] for m in r.json().get("models", [])
                if "vision" in (m.get("capabilities") or [])]
    except Exception:
        return []

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".ini"}


def _clip(text, limit):
    if len(text) <= limit:
        return text, False
    return text[:limit], True


# ----------------------------------------------------------------- per format

def _read_pdf_text(path, pages=None):
    """Page text from the PDF's own layer. Returns (pages_dict, total_chars)."""
    import pdfplumber

    out, total = {}, 0
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            if pages and i not in pages:
                continue
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            out[i] = txt
            total += len(txt)
    return out, total


def _render(path, page_no, scale=2.0):
    """One page as PNG bytes, honouring the page's own rotation.

    pypdfium2 ignores /Rotate, which once rendered a landscape schedule on its
    side and produced 120 characters where there were 2,876.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    try:
        page = doc[page_no - 1]
        try:
            rot = (360 - page.get_rotation()) % 360
            img = page.render(scale=scale, rotation=rot).to_pil()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        finally:
            page.close()
    finally:
        doc.close()


def _unload(model, timeout=120):
    """Evict a model so the next one has room to load."""
    try:
        requests.post(f"{OLLAMA}/api/generate",
                      json={"model": model, "keep_alive": 0}, timeout=timeout)
    except Exception:
        pass


def best_vision_model():
    """An installed model that can see, preferring the smallest."""
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=30)
        cands = [(m.get("size", 0), m["name"]) for m in r.json().get("models", [])
                 if "vision" in (m.get("capabilities") or [])]
        return sorted(cands)[0][1] if cands else None
    except Exception:
        return None


def read_pages_with_vision(path, page_nos, timeout=600):
    """Transcribe several pages in ONE visit to the vision model.

    The expensive part is not the looking, it is the swapping: on a 16GB card
    the text model and the vision model cannot both be resident, so each
    change of mind costs an evict and a 13-18GB load, and asking for them one
    page at a time would pay that twice per page.

    So the working model is evicted once, every page is read while the vision
    model is up, and it is evicted in turn. The text it produces goes back as
    an ordinary tool result -- the conversation never moved, so nothing has to
    be replayed to catch the main model up.
    """
    vm = best_vision_model()
    if not vm:
        return {}, None

    _unload(VISION_MODEL if VISION_MODEL != vm else "")
    out = {}
    try:
        for pg in page_nos:
            try:
                out[pg] = read_page_with_vision(path, pg, model=vm,
                                                timeout=timeout)
            except Exception as e:
                out[pg] = f"[page {pg} could not be read: {type(e).__name__}]"
    finally:
        # Put the card back the way it was found, so the next ordinary turn
        # is not the one that pays for this.
        _unload(vm)
    return out, vm


def read_page_with_vision(path, page_no, model=VISION_MODEL, timeout=300):
    """Transcribe one page using the local vision model."""
    png = _render(path, page_no)
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": ("Transcribe every word and number on this page, "
                        "keeping the layout as rows. Do not summarise, do not "
                        "explain, do not add anything that is not printed. If "
                        "a value is unclear, write it as [unclear]."),
            "images": [base64.b64encode(png).decode()],
        }],
        "stream": False,
        "options": {"temperature": 0, "top_p": 1, "top_k": 1, "seed": 7},
    }
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    return (r.json().get("message", {}) or {}).get("content", "")


def _read_docx(path):
    import docx

    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _read_pptx(path):
    from pptx import Presentation

    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"--- slide {i} ---")
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def _read_xlsx(path, max_rows=200):
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    parts = []
    for name in wb.sheetnames:
        ws = wb[name]
        parts.append(f"--- sheet: {name} ---")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows:
                parts.append(f"... ({name} continues)")
                break
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


# -------------------------------------------------------------------- the API

def read_document(path, pages=None, max_chars=18000, allow_vision=True):
    """Text of a document, whatever kind it is.

    Returns {kind, text, pages_read, how, truncated, note}. `how` says where
    the text came from -- the file's own text layer, or the vision model --
    because a reader should always be able to tell which they are looking at.
    """
    if not os.path.isfile(path):
        return {"error": f"no such file: {path}"}
    ext = os.path.splitext(path)[1].lower()

    try:
        if ext in TEXT_EXT:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text, cut = _clip(fh.read(), max_chars)
            return {"kind": ext.lstrip("."), "how": "plain text", "text": text,
                    "truncated": cut}

        if ext == ".docx":
            text, cut = _clip(_read_docx(path), max_chars)
            return {"kind": "word", "how": "document text", "text": text,
                    "truncated": cut}

        if ext == ".pptx":
            text, cut = _clip(_read_pptx(path), max_chars)
            return {"kind": "powerpoint", "how": "slide text", "text": text,
                    "truncated": cut}

        if ext in (".xlsx", ".xlsm", ".xls"):
            text, cut = _clip(_read_xlsx(path), max_chars)
            return {"kind": "excel", "how": "cell values", "text": text,
                    "truncated": cut}

        if ext != ".pdf":
            return {"error": f"I do not read {ext} files yet."}

        want = set(pages) if pages else None
        by_page, total = _read_pdf_text(path, want)
        n_pages = len(by_page)

        # Enough text to work with: use it. It is exact, and rendering a page
        # to pixels and reading it back introduces errors that the text layer
        # does not have.
        if total >= 120 * max(n_pages, 1) or (total > 400 and not allow_vision):
            body = "\n".join(f"--- page {p} ---\n{t}"
                             for p, t in sorted(by_page.items()) if t.strip())
            text, cut = _clip(body, max_chars)
            return {"kind": "pdf", "how": "the PDF's own text layer",
                    "pages_read": sorted(by_page), "text": text,
                    "truncated": cut}

        if not allow_vision:
            return {"kind": "pdf", "how": "none",
                    "error": "This PDF has little or no text layer and reading "
                             "it as images is switched off.",
                    "pages_read": []}

        if not model_can_see() and not best_vision_model():
            return {"kind": "pdf (scanned)", "how": "none", "pages_read": [],
                    "error": "This PDF has no usable text layer and no "
                             "installed model can see, so its contents cannot "
                             "be read. Tell the user; do not guess.",
                    "fix": "ollama pull gemma3:4b"}

        # Scanned. Swap to a model that can see, read every page needed while
        # it is up, and swap back. One visit, however many pages.
        todo = (sorted(want) if want else sorted(by_page)[:6])[:8]
        pages_text, used_model = read_pages_with_vision(path, todo)
        done, chunks = [], []
        for p in todo:
            if p in pages_text:
                chunks.append(f"--- page {p} (read as an image) ---\n"
                              + pages_text[p])
                done.append(p)
        text, cut = _clip("\n".join(chunks), max_chars)
        note = (f"This document has no usable text layer, so the pages were "
                f"rendered and read by the local vision model"
                f"{' (' + used_model + ')' if used_model else ''}. "
                f"Transcription from an image is less exact than a text "
                f"layer: check any figure that matters against the page.")
        return {"kind": "pdf (scanned)", "how": "the local vision model",
                "pages_read": done, "text": text, "truncated": cut,
                "note": note}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
