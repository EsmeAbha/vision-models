"""A local model that decides what to do with a document, and does it.

The page this serves is a chat box, not a form. You say what you want; the
model works out what that means, looks at what is on disk, checks whether it
already knows the report type, and either reuses what it learned or works the
document out from scratch. Nothing about a rent roll is wired in -- a rent
roll is simply the first thing it learned.

Everything runs on this machine. The reasoning is a local Ollama model, the
reading is local code, and no document or figure leaves the box.

The model is given tools rather than instructions about tables. It can look in
a folder, inspect a document, ask what skills exist, extract with one, and
read a spreadsheet back. It cannot OCR unless the person running it has
allowed OCR for that turn, because OCR here means unloading the language model
and bringing vLLM up in WSL -- minutes of a shared GPU, and a decision that
belongs to a person.

Two things the tools guarantee whatever the model decides:

* A figure is only ever reported as clean when the document's own printed
  totals agree with it. The model is not asked to judge that; the code does,
  and hands it the verdict.
* A doubt is never dropped. Flags come back with every extraction and go into
  the answer and the workbook both.
"""
from __future__ import annotations

import json
import os
import time

import requests

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("FINAI_MODEL", "gpt-oss-64k:latest")
VISION_MODEL = os.environ.get("FINAI_VISION_MODEL", "gemma4-32k:latest")

_here = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_here, "outputs")

SYSTEM = """You are a capable assistant working entirely on a local machine, with tools that read and analyse documents. Financial work is what you are used for most, but you are not limited to it: answer whatever is asked.

Your tools:

- read_document reads ANY document and gives you its text -- PDF, Word, PowerPoint, Excel, text, CSV. Use it whenever someone asks what a document says, or asks anything you would need to read it to answer. It handles scanned PDFs by itself, so you never need permission to read something.
- list_folder shows what is on disk.
- inspect_document tells you whether a PDF is a report type you already know how to extract, and on which pages.
- extract_as_asked pulls data out of ANY document into Excel, in whatever shape the user described. Use this whenever someone asks for data in a particular form and no known report type fits -- it works on layouts nobody has seen before. Pass their requirement in their own words.
- extract_document is the faster path for a report type already learned, where the figures must reconcile against the totals the document prints about itself. Prefer it when inspect_document reports a known report; otherwise use extract_as_asked.
- extract_units fills a template with one row per unit, when a folder holds a sub-folder per unit and each holds that unit's own documents. Each unit is read in isolation, so one unit's figures cannot land on another's row.
- list_skills says what report types you have learned.

How to work:

- Read first. If someone gives you a file and asks about it, call read_document and answer from what it says.
- Do not tell someone their document is unusable because it has no table. A CV, a letter and a term sheet are all readable; tables are a special case, not the point.
- To get data out into a spreadsheet: if inspect_document found a known report type, use extract_document; otherwise use extract_as_asked and pass the user's requirement verbatim. Never tell someone you cannot extract from a document because its layout is unfamiliar.
- extract_as_asked reports how many values were checked against the document and how many were NOT found in it. Always pass that on: values not found are the ones that might be invented, and the user must be told which.
- Never invent a number or a fact about a document. Everything you state must come from a tool result. If you have not read it, say so and read it.
- When a tool warns that text came from reading images rather than a text layer, pass that warning on: transcription can misread a digit.
- If an extraction reports failed checks or flagged cells, lead with that and name them. Accuracy outranks tidiness.

Be brief and concrete."""


# ----------------------------------------------------------------- the tools

def _short(path):
    return os.path.basename(path)


def list_folder(path: str, **_):
    """What documents are in a folder."""
    path = (path or "").strip().strip('"').strip("'")
    if os.path.isfile(path):
        return {"files": [{"path": path, "name": _short(path),
                           "kind": os.path.splitext(path)[1].lower()}]}
    if not os.path.isdir(path):
        return {"error": f"not a folder or file: {path}"}
    out = []
    for dp, _d, fs in os.walk(path):
        for fn in sorted(fs):
            ext = os.path.splitext(fn)[1].lower()
            if ext in (".pdf", ".xlsx", ".xls", ".csv", ".docx"):
                out.append({"path": os.path.join(dp, fn), "name": fn,
                            "kind": ext})
        if len(out) > 200:
            break
    return {"count": len(out), "files": out[:200]}


def inspect_document(path: str, **_):
    """Pages, whether it can be read without OCR, and any known report inside."""
    from engine import geometry as G
    import docagent as A

    path = (path or "").strip().strip('"').strip("'")
    if not os.path.isfile(path):
        return {"error": f"no such file: {path}"}
    if not path.lower().endswith(".pdf"):
        return {"error": "inspect_document handles PDFs; use read_spreadsheet "
                         "for Excel"}
    try:
        ok, note = G.has_text_layer(path)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    info = {"name": _short(path), "pages": A._page_count(path),
            "readable_without_ocr": bool(ok), "note": note}
    if not ok:
        info["next"] = ("This document has no usable text layer. It needs OCR, "
                        "which must be allowed by the user.")
        return info
    try:
        tokens = G.from_pdf(path)
        cfg, score, pages = A.find_skill(path, tokens=tokens)
        if cfg and pages:
            info["known_report"] = cfg["slug"]
            info["known_report_title"] = cfg.get("title", "")
            info["match"] = round(score, 2)
            info["found_on_pages"] = A._fmt_pages(pages)
        else:
            info["known_report"] = None
            info["best_match_score"] = round(score, 2)
            info["next"] = ("No skill matches. extract_document will still "
                            "read it and check it against its own totals.")
    except Exception as e:
        info["warning"] = f"{type(e).__name__}: {e}"
    return info


def extract_document(path: str, pages: str = "", **_):
    """Read a document to Excel and check it against its own printed totals."""
    import docagent as A

    path = (path or "").strip().strip('"').strip("'")
    if not os.path.isfile(path):
        return {"error": f"no such file: {path}"}
    try:
        row, out, detail = A._run_one(path, pages or "", True)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    res = {"file": row.get("File"), "pages_used": row.get("Pages", pages or "all"),
           "records": row.get("Records"), "checks": row.get("Checks"),
           "flagged_cells": row.get("Flags"), "skill": row.get("Skill"),
           "status": row.get("Status")}
    if out:
        res["workbook"] = out
    if detail:
        res["check_detail"] = detail[:1800]
    return res


def read_document(path: str, pages: str = "", **_):
    """Read any document and return its text."""
    import readers

    path = (path or "").strip().strip('"').strip("'")
    want = None
    if pages.strip():
        want = set()
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                want.update(range(int(lo), int(hi) + 1))
            elif part:
                want.add(int(part))
    return readers.read_document(path, pages=want)


def extract_as_asked(path: str, requirement: str, pages: str = "", **_):
    """Extract whatever the user described, from any document, any layout."""
    import ai_extract

    path = (path or "").strip().strip('"').strip("'")
    if not os.path.isfile(path):
        return {"error": f"no such file: {path}"}
    want = None
    if pages.strip():
        want = set()
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                want.update(range(int(lo), int(hi) + 1))
            elif part:
                want.add(int(part))

    res = ai_extract.extract(path, requirement, pages=want)
    if res.get("error"):
        return res
    book = ai_extract.to_workbook(res)
    flags = res["flags"]
    return {
        "file": res["file"],
        "columns": res["columns"],
        "rows_extracted": res["n_rows"],
        "values_checked_against_document": res["values_checked"],
        "values_NOT_found_in_document": len(flags),
        "flagged": [{"row": f["row"] + 1, "column": f["column_name"],
                     "value": f["value"]} for f in flags[:10]],
        "uncertain": res["uncertain"][:5],
        "truncated": res["truncated"],
        "how_read": res["how_read"],
        "workbook": book,
        "sample_rows": [r["values"] for r in res["rows"][:5]],
        "seconds": res["seconds"],
    }


def extract_units(root: str, template: str = "", out_name: str = "", **_):
    """One row per unit sub-folder, each read only from its own documents."""
    import unit_extract as U

    root = (root or "").strip().strip('"').strip("'")
    template = (template or "").strip().strip('"').strip("'")
    if not os.path.isdir(root):
        return {"error": f"not a folder: {root}"}
    if not template or not os.path.isfile(template):
        return {"error": "I need the path to the template workbook to fill."}

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = os.path.join(OUTPUT_DIR,
                       out_name or (os.path.basename(root.rstrip("/\\")) +
                                    " - units.xlsx"))
    try:
        summary, rows = U.run(root, template, out)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if summary.get("error"):
        return summary

    # Unit labels and flag reasons only. The values themselves stay in the
    # workbook: this is confidential tenancy data, and there is no reason for
    # it to travel through a chat transcript to be reported on.
    summary["units_detail"] = [
        {"unit": r["unit"], "files_read": r["files_read"],
         "files_skipped": r["files_skipped"],
         "fields_filled": sum(1 for k, _c, _l in U.FIELDS if r["values"].get(k)),
         "flags": r["flags"][:6]}
        for r in rows]
    return summary


def list_skills(**_):
    """Report types this agent has already learned."""
    from engine import skills as S

    out = []
    for cfg in S.load_all():
        runs = cfg.get("runs", {})
        out.append({"slug": cfg.get("slug"), "title": cfg.get("title"),
                    "handles": cfg.get("summary", "")[:200],
                    "applied": runs.get("applied", 0),
                    "reconciled": runs.get("reconciled", 0),
                    "failed": runs.get("failed", 0)})
    return {"count": len(out), "skills": out}


def read_spreadsheet(path: str, sheet: str = "", max_rows: int = 25, **_):
    """Look at an Excel file -- sheet names, headings and the first rows."""
    path = (path or "").strip().strip('"').strip("'")
    if not os.path.isfile(path):
        return {"error": f"no such file: {path}"}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    names = wb.sheetnames
    target = sheet if sheet in names else names[0]
    ws = wb[target]
    rows = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i >= max(1, min(int(max_rows), 60)):
            break
        rows.append([("" if v is None else str(v)[:40]) for v in r[:16]])
    return {"sheets": names, "showing": target, "rows": rows}


TOOLS = {
    "read_document": read_document,
    "extract_as_asked": extract_as_asked,
    "list_folder": list_folder,
    "inspect_document": inspect_document,
    "extract_document": extract_document,
    "list_skills": list_skills,
    "read_spreadsheet": read_spreadsheet,
    "extract_units": extract_units,
}

SCHEMA = [
    {"type": "function", "function": {
        "name": "read_document",
        "description": "Read any document and return its text: PDF, Word, "
                       "PowerPoint, Excel, text or CSV. Use this whenever "
                       "someone asks what a document says, or asks anything "
                       "you would have to read it to answer. Scanned PDFs are "
                       "handled automatically by reading the pages as images, "
                       "so no permission is needed.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "pages": {"type": "string",
                      "description": "optional, e.g. '1-3'. Omit for the "
                                     "whole document."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "extract_as_asked",
        "description": "Extract data from ANY document into Excel, in whatever "
                       "shape the user described, whatever its layout. Pass "
                       "their requirement in their own words -- which columns "
                       "they want, one row per what. Use this whenever someone "
                       "asks for data pulled out in a particular form and no "
                       "known report type fits. Every value is checked back "
                       "against the document and anything not found there is "
                       "flagged for a human.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "requirement": {"type": "string",
                            "description": "What the user asked for, in full."},
            "pages": {"type": "string", "description": "optional, e.g. '38-41'"}},
            "required": ["path", "requirement"]}}},
    {"type": "function", "function": {
        "name": "list_folder",
        "description": "List the documents in a folder (PDF, Excel, CSV, Word). "
                       "Use this first when the user names a folder.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Folder or file path"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "inspect_document",
        "description": "Inspect a PDF: how many pages, whether it can be read "
                       "without OCR, and whether a report type this agent "
                       "already knows appears inside it and on which pages. "
                       "Always inspect before extracting.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "extract_document",
        "description": "Read a PDF into an Excel workbook and check the figures "
                       "against the totals the document prints about itself. "
                       "Returns how many checks passed and how many cells were "
                       "flagged for a human. Pass the page range from "
                       "inspect_document when it found one.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "pages": {"type": "string",
                      "description": "e.g. '38-41'. Empty means find it."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "extract_units",
        "description": "Fill a template workbook with one row per unit, where "
                       "a folder holds a sub-folder per unit (an apartment, a "
                       "loan, a property) and each sub-folder holds that "
                       "unit's own documents. Each unit is read in isolation "
                       "so one unit's figures cannot land on another's row. "
                       "Use this when the user points at a folder of "
                       "sub-folders and wants a row for each.",
        "parameters": {"type": "object", "properties": {
            "root": {"type": "string",
                     "description": "Folder containing one sub-folder per unit"},
            "template": {"type": "string",
                         "description": "Path to the template workbook to fill"},
            "out_name": {"type": "string"}},
            "required": ["root", "template"]}}},
    {"type": "function", "function": {
        "name": "list_skills",
        "description": "List the report types this agent has already learned, "
                       "with how often each reconciled or failed.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_spreadsheet",
        "description": "Read an existing Excel file: sheet names and first rows.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "sheet": {"type": "string"},
            "max_rows": {"type": "integer"}}, "required": ["path"]}}},
]


# ------------------------------------------------------------- the agent loop

def _chat(model, messages, tools=None, timeout=600):
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        # Same answer for the same question: this is financial work, and a
        # figure that changes between runs cannot be checked.
        "options": {"temperature": 0, "top_p": 1, "top_k": 1, "seed": 7},
    }
    if tools:
        body["tools"] = tools
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def run_agent(prompt, history=None, model=DEFAULT_MODEL,
              images=None, max_steps=8, on_event=None):
    """Answer a request, calling tools as the model decides. Yields nothing;
    reports progress through `on_event(kind, text)` so a UI can show its work.
    """
    def say(kind, text):
        if on_event:
            on_event(kind, text)

    messages = [{"role": "system", "content": SYSTEM}]
    for turn in (history or []):
        messages.append(turn)
    user_msg = {"role": "user", "content": prompt}
    if images:
        user_msg["images"] = images
        model = VISION_MODEL          # only this one can see
    messages.append(user_msg)

    steps = []
    for step in range(max_steps):
        t0 = time.time()
        try:
            data = _chat(model, messages, SCHEMA)
        except Exception as e:
            return (f"The local model did not answer: {type(e).__name__}: {e}\n\n"
                    f"Check Ollama is running at {OLLAMA}."), steps

        msg = data.get("message", {}) or {}
        calls = msg.get("tool_calls") or []
        if msg.get("content") and not calls:
            say("answer", msg["content"])
            return msg["content"], steps

        messages.append({k: v for k, v in msg.items() if k != "images"})
        if not calls:
            return (msg.get("content") or "(the model returned nothing)"), steps

        for call in calls:
            fn = (call.get("function") or {})
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            say("tool", f"{name}({json.dumps(args)[:160]})")
            impl = TOOLS.get(name)
            if impl is None:
                result = {"error": f"no such tool: {name}"}
            else:
                try:
                    result = impl(**args)
                except TypeError as e:
                    result = {"error": f"bad arguments: {e}"}
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
            steps.append({"tool": name, "args": args, "result": result,
                          "seconds": round(time.time() - t0, 1)})
            say("result", json.dumps(result)[:600])
            messages.append({"role": "tool", "name": name,
                             "content": json.dumps(result)[:6000]})

    return ("I stopped after several steps without reaching an answer. "
            "The tool results above show how far it got."), steps
