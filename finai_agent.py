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
# ONE model. Swapping between two on a 16GB card evicts and reloads
# 13-18GB each time -- that is where the minute-long stalls and the 500s
# came from, since Ollama cannot hold two of these at once.
DEFAULT_MODEL = os.environ.get("FINAI_MODEL", "gpt-oss-64k:latest")
VISION_MODEL = DEFAULT_MODEL

_here = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_here, "outputs")

SYSTEM = """You are a document analyst working entirely on one local machine. Financial documents are your usual work, but you are not limited to them.

**Your environment:**
- Windows. Paths use backslashes and often contain spaces; always quote them.
- You act only through tools. You cannot see a file until you have read it.
- Everything stays on this machine. Nothing you handle leaves it.

**Your core responsibilities:**
1. Answer what was asked, from what the documents actually say.
2. Get data out into the shape the user wants, whatever the layout.
3. Tell the user plainly what could not be verified.

**Your process:**
1. Look before acting. Given a folder, list it. Given a file, inspect or read it. Never assume what a document contains.
2. For a question about content, call read_document and answer from its text.
3. For data into a spreadsheet: call inspect_document first. If it names a known report, use extract_document, which reconciles against the totals the document prints about itself. If it does not, use extract_as_asked and pass the user's requirement in their own words.
4. For anything no tool covers, write it with run_python. Read the traceback, fix it, run it again.
5. Report what the tools returned, including what failed.

**Quality standards:**
- Every figure and fact you state comes from a tool result. If you have not read it, read it before answering.
- When a tool reports values it could not find in the source, name them. Those are the ones that may be invented.
- When text was read from images rather than a text layer, say so: transcription can misread a digit.
- When checks fail or cells are flagged, lead with that, not with the total.

**Output format:**
- Lead with the answer or the problem, not with what you did.
- State counts plainly: rows produced, checks passed, values unverified.
- Give the workbook path when one was written.
- Be brief. No preamble, no summary of your own steps unless asked.

**Edge cases:**
- Document has no table: read it anyway and answer. Tables are one case, not the point.
- Layout is unfamiliar: use extract_as_asked. Never refuse for that reason.
- No tool fits: use run_python. Only say you cannot if run_python is off or the machine genuinely lacks what is needed.
- A path does not exist: list the folder and look, rather than asking again."""


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
    # Every file, not a chosen few. This listed only documents, so a folder
    # holding a zip came back as "count: 0, files: []" -- and the model, told
    # the folder was empty, correctly gave up. It was not being unintelligent;
    # the tool had lied to it. A tool that hides things makes the model look
    # like the thing that failed.
    docs, other = [], []
    for dp, _d, fs in os.walk(path):
        for fn in sorted(fs):
            ext = os.path.splitext(fn)[1].lower()
            rec = {"path": os.path.join(dp, fn), "name": fn, "kind": ext}
            (docs if ext in (".pdf", ".xlsx", ".xls", ".csv", ".docx",
                             ".pptx", ".txt", ".md") else other).append(rec)
        if len(docs) + len(other) > 400:
            break
    files = (docs + other)[:250]
    return {"count": len(files), "documents": len(docs),
            "other_files": len(other), "files": files}


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
    if not (os.path.isdir(root) or
            (root.lower().endswith(".zip") and os.path.isfile(root))):
        return {"error": f"not a folder or zip: {root}"}
    if template and os.path.isfile(template):
        template = U.remember_template(template)
    else:
        template = U.find_template()
    if not template:
        return {"error": "I have no template to fill. Attach the template "
                         "workbook once and I will remember it."}

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(root.rstrip("/\\")))[0]
    out = os.path.join(OUTPUT_DIR, out_name or f"{stem} - units.xlsx")
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


def run_python(code: str, timeout: int = 180, **_):
    """Write and run code, for anything the other tools do not cover."""
    import run_code

    return run_code.run_python(code, timeout=timeout)


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
    "run_python": run_python,
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
        "name": "run_python",
        "description": __import__("run_code").HELP,
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Python to run. print() "
                                                      "what you want to see."},
            "timeout": {"type": "integer", "description": "seconds, default 180"}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "read_document",
        "description": "Read any document and return its text: PDF, Word, PowerPoint, Excel, text or CSV. Scanned PDFs are handled automatically by reading the pages as images, so no permission is needed and you should never say a document cannot be read.\n\nUse this to ANSWER something: what does this say, summarise it, find the clause about X, who signed it, is there a date, does it mention Y. If the user wants prose back, this is the tool.\n\nDo NOT use this when the user wants a grid back -- rows and columns in a spreadsheet. That is extract_as_asked. 'What does the lease say about rent?' is this tool; 'list every lease with its rent' is not.\n\nPass `pages` when the document is long and you know which part you need; leaving it empty reads from the start and may be truncated. The result says how the text was obtained: if it says the vision model, transcription can misread a digit and you must pass that warning on.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "pages": {"type": "string",
                      "description": "optional, e.g. '1-3'. Omit for the "
                                     "whole document."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "extract_as_asked",
        "description": "Pull data OUT of any document and into an Excel workbook, in whatever shape the user described, whatever the document's layout. Works on layouts nobody has seen before.\n\nUse this when the user wants DATA IN A SPREADSHEET: 'give me a list of', 'put X in Excel', 'one row per tenant', 'extract the table', 'I need columns A, B and C'. The giveaway is that they describe a SHAPE -- rows, columns, one-per-something.\n\nDo NOT use this to answer a question about what a document says -- that is read_document. 'What does this lease say about renewal?' is a question; 'list every lease with its renewal date' is an extraction. If the user wants prose back, read it; if they want a grid back, extract it.\n\nPass `requirement` as the user's own words, in full, including which columns they named and what each row should represent. Do not summarise their requirement -- the wording is what decides the output columns.\n\nEvery value returned is searched for in the document's own text before you see it. The result says how many values were checked and how many were NOT found. Values not found may have been invented: always report that count and name them.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "requirement": {"type": "string",
                            "description": "What the user asked for, in full."},
            "pages": {"type": "string", "description": "optional, e.g. '38-41'"}},
            "required": ["path", "requirement"]}}},
    {"type": "function", "function": {
        "name": "list_folder",
        "description": "List the documents in a folder: PDFs, Excel, CSV and Word files, with their full paths.\n\nUse this when the user points at a FOLDER, or refers to 'these documents' without naming a file. It answers 'what is here', nothing more.\n\nDo NOT use it when the user has given you a file path -- go straight to that file with inspect_document or read_document. Only fall back to listing when a path you tried does not exist, to find the real name.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Folder or file path"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "inspect_document",
        "description": "Ask what a PDF IS, without reading its contents: how many pages, whether it has a usable text layer, and whether it contains a report type this agent has already learned to extract and on which pages.\n\nTriggers: 'do you already know this report', 'have you seen this format before', 'do you recognise it', 'how many pages is it', 'will this need OCR', 'can you handle this one'. Any question about the FILE rather than what it says.\n\nUse it BEFORE extracting, to choose the extraction tool. If it names a known report, extract_document reconciles against the document's own printed totals. If it names none, use extract_as_asked.\n\nDo NOT use it to find out what a document SAYS -- that is read_document. Do NOT list the folder first when you have been given a file path; come straight here.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "extract_document",
        "description": "Extract a report this agent has ALREADY LEARNED, using the stored recipe for its layout, and check every figure against the totals the report prints about itself.\n\nOnly worth using when inspect_document reported a known report type. It is faster and stronger than extract_as_asked because it proves the arithmetic rather than just checking that figures appear in the source -- but it only works on layouts already learned.\n\nIf inspect_document found no known report, use extract_as_asked instead. Do not force this tool onto an unfamiliar layout.\n\nThe result reports checks passed and cells flagged. A failed check means the figures read do not add up to the total the document states: say so plainly and name which column.",
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
        "description": "Look inside an existing Excel workbook: its sheet names, and the first rows of one sheet.\n\nUse this to inspect a spreadsheet that already exists -- a template you must fill, a reference file to compare against, or output you produced earlier and want to check.\n\nFor the full contents of a spreadsheet as text, read_document handles .xlsx too and returns every sheet. This tool is for a quick look at the shape: what sheets exist and what the headings are.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "sheet": {"type": "string"},
            "max_rows": {"type": "integer"}}, "required": ["path"]}}},
]


# ------------------------------------------------------------- the agent loop

def _last_failed(steps):
    """Did the most recent tool call fail?

    A result counts as a failure when it carries an error, or when a code run
    came back with ok=False. That is the moment a model is most likely to
    stop early, and the moment it is most worth another turn.
    """
    if not steps:
        return False
    r = steps[-1].get("result")
    if not isinstance(r, dict):
        return False
    return bool(r.get("error")) or r.get("ok") is False


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
              images=None, max_steps=24, on_event=None):
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
    messages.append(user_msg)

    steps = []
    nudged = False
    for step in range(max_steps):
        t0 = time.time()
        data = None
        for attempt in range(3):
            try:
                data = _chat(model, messages, SCHEMA)
                break
            except Exception as e:
                # A 500 here is usually not a broken request: it is Ollama
                # failing to load this model because another is resident and
                # the card is full. That clears once the other is evicted, so
                # it is worth waiting for. Giving up on a transient error is
                # the same failure as giving up on a traceback.
                last = f"{type(e).__name__}: {e}"
                if attempt == 2:
                    return (f"The local model did not answer after 3 tries: "
                            f"{last}. If that is a 500, the GPU may be full: "
                            f"another model is loaded and there is no room "
                            f"for this one."), steps
                say("tool", f"(model error, retrying in {4 * (attempt + 1)}s)")
                time.sleep(4 * (attempt + 1))

        msg = data.get("message", {}) or {}
        calls = msg.get("tool_calls") or []
        if msg.get("content") and not calls:
            # The model has stopped. Stopping is not the same as finishing:
            # a model that has just been handed a traceback will often report
            # what went wrong and fall silent, because nothing asked it to
            # continue. A person told "this task" tries again; the model
            # cannot, unless the loop gives it another turn.
            #
            # So when the last thing that happened was a failure, push back
            # once and let it keep going. Once only -- twice is nagging, and
            # a model that has genuinely hit a wall should be allowed to say
            # so rather than be made to invent a way around it.
            if steps and not nudged and _last_failed(steps):
                nudged = True
                say("tool", "(not done yet - asking it to continue)")
                messages.append({k: v for k, v in msg.items() if k != "images"})
                messages.append({"role": "user", "content": (
                    "That did not succeed. The error above is information, "
                    "not a dead end: work out what it tells you and try a "
                    "different approach. If you genuinely cannot do it, say "
                    "exactly what is missing.")})
                continue
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
