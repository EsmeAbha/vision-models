"""What was read before, so a session can be picked up rather than redone.

Two files per run under run_history/: a small JSON of what it was, and the
transcript beside it as .txt. Re-reading a document costs a model load and a
GPU, so the text is worth keeping; but drawing a list of sixty file names
should not mean parsing sixty transcripts, which is what one combined file
cost. Measured at the 200-record cap: 23ms and 4.7MB read, to list names.

Records are capped by count rather than age. The cap is about keeping the
folder legible, not about space.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

_here = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(_here, "run_history")

KEEP = 200
_ID_OK = re.compile(r"^[0-9a-f]{8,64}$")


def _path(rid):
    return os.path.join(HISTORY_DIR, f"{rid}.json")


def _text_path(rid):
    return os.path.join(HISTORY_DIR, f"{rid}.txt")


def _is_record(name):
    """A run's own record, not the layout kept beside it. Both end in .json;
    counting the layouts as records halved the cap and logged a "skipping"
    line for every one on every listing."""
    return name.endswith(".json") and not name.endswith(".layout.json")


def _layout_path(rid):
    return os.path.join(HISTORY_DIR, f"{rid}.layout.json")


def load_layout(rid):
    """Where the OCR found each block on each page, or None if not kept."""
    if not _ID_OK.match(str(rid or "")):
        return None
    try:
        with open(_layout_path(rid), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _source_path(rid, ext=""):
    """Where the document itself is kept, so it can be looked at later.

    Uploads live in a temp folder and in a dict in memory: a server restart
    loses both, and the side panel then answers "that upload is no longer
    held" for every run in the list. The whole point of the panel is checking
    a value against the page it came from, which is not something that should
    expire.
    """
    if ext:
        return os.path.join(HISTORY_DIR, f"{rid}.src{ext}")
    import glob as _glob
    hits = _glob.glob(os.path.join(HISTORY_DIR, f"{rid}.src.*"))
    return hits[0] if hits else ""


def record(run, model_name=""):
    """Write or refresh this run's record. Returns the path, or None.

    Called again when fields are pulled, so reopening a run shows what was
    found rather than only the transcript.
    """
    rid = str(run.get("id") or "")
    if not _ID_OK.match(rid):
        return None
    # A thread is worth remembering once it holds anything: a document that
    # was read, or an exchange that was typed. Requiring a finished run meant
    # a conversation with no document was never written down at all, and a
    # page reload threw it away.
    if run.get("status") != "done" and not run.get("turns"):
        return None
    os.makedirs(HISTORY_DIR, exist_ok=True)

    entry = {
        "id": rid,
        "file": run.get("file", ""),
        # Where the document sat on this PC when it was picked: what keeps a
        # value traceable to its file after the upload itself is gone.
        "source_path": run.get("source_path", ""),
        "model": run.get("model", ""),
        "model_name": model_name or run.get("model", ""),
        "at": run.get("at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        # `at` is for reading and only resolves to the second, so two runs
        # started in the same second sort by whatever order the directory
        # happens to list them in. `seq` is what the order actually uses.
        "seq": time.time(),
        "elapsed": run.get("elapsed"),
        "annotated": run.get("annotated") or None,
        "fields": run.get("fields") or [],
        "field_summary": run.get("field_summary") or "",
        # Every question and answer typed in this thread, in order. A chat is
        # one document plus what was asked about it, so they belong to the
        # same record rather than a list of their own.
        "turns": run.get("turns") or [],
        # What to call it in the sidebar when there is no document to name.
        "title": run.get("title") or "",
        "title_ai": bool(run.get("title_ai")),
        # What kind of document it was read as, so the Pexl template for that
        # kind is still found after a restart.
        "doc_type": run.get("doc_type") or "",
    }
    # Keep the first timestamp across refreshes, so a run does not jump to
    # the top of the list every time its fields are pulled again.
    existing = load(rid, with_text=False)
    if existing:
        entry["at"] = existing.get("at", entry["at"])
        entry["seq"] = existing.get("seq", entry["seq"])
        if not entry["source_path"]:
            entry["source_path"] = existing.get("source_path", "")
    # What the analyst signed off, each pinned to the value it signed off. A
    # caller that never loaded the approvals (a thread save for a run not in
    # memory) must not wipe them, so absent means keep.
    if "approved" in run:
        entry["approved"] = run.get("approved") or {}
    else:
        entry["approved"] = (existing or {}).get("approved") or {}

    if run.get("layout"):
        with open(_layout_path(rid), "w", encoding="utf-8") as fh:
            json.dump(run["layout"], fh)

    # The transcript goes beside the record, not inside it, so listing runs
    # never reads it.
    # Keep the document beside the record, once.
    src = run.get("source_file") or ""
    if src and os.path.exists(src):
        ext = os.path.splitext(src)[1].lower() or ".bin"
        dest = _source_path(rid, ext)
        if not os.path.exists(dest):
            try:
                shutil.copyfile(src, dest)
            except OSError as e:
                print(f"history: could not keep {os.path.basename(src)}: {e}",
                      flush=True)
    kept = _source_path(rid)
    entry["source"] = os.path.basename(kept) if kept else ""

    text = run.get("text") or ""
    entry["chars"] = len(text)
    with open(_text_path(rid), "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(_path(rid), "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2)
    _trim()
    return _path(rid)


def load(rid, with_text=True):
    """One record. Pass with_text=False to skip reading the transcript."""
    if not _ID_OK.match(str(rid or "")):
        return None
    try:
        with open(_path(rid), encoding="utf-8") as fh:
            entry = json.load(fh)
    except (OSError, ValueError):
        return None
    if not with_text:
        return entry
    if "text" not in entry:
        # Records written before the split keep their text inline; everything
        # since has it beside them.
        try:
            with open(_text_path(rid), encoding="utf-8") as fh:
                entry["text"] = fh.read()
        except OSError:
            entry["text"] = ""
    return entry


def summaries(limit=60):
    """Every remembered run, newest first, without the transcripts.

    The list is for choosing from, and a transcript is tens of kilobytes;
    sending them all to draw a sidebar would be wasteful.
    """
    out = []
    if not os.path.isdir(HISTORY_DIR):
        return out
    for name in os.listdir(HISTORY_DIR):
        if not _is_record(name):
            continue
        entry = load(name[:-5], with_text=False)
        if not entry:
            # A half-written or hand-edited record costs itself, not the list.
            print(f"history: skipping {name}", flush=True)
            continue
        out.append({
            "id": entry["id"], "file": entry["file"],
            "model": entry["model"], "model_name": entry["model_name"],
            "at": entry["at"], "seq": entry.get("seq", 0),
            "elapsed": entry.get("elapsed"),
            "chars": entry.get("chars", len(entry.get("text") or "")),
            "fields": len(entry.get("fields") or []),
            "turns": len(entry.get("turns") or []),
            "title": entry.get("title", ""),
            "display": display_title(entry),
        })
    out.sort(key=lambda e: (e.get("seq") or 0, e["at"]), reverse=True)
    return out[:limit]


_TYPE_NAMES = {"utility-bill": "Utility bill", "bank-statement": "Bank statement",
               "appraisal": "Appraisal", "lease-document": "Lease", "tax-bill": "Tax bill",
               "rent-roll": "Rent roll"}
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month(value):
    """"09/05/2026" or "2026-09-05" -> "Sep 2026"; None if it is not a date."""
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", value or "")
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{_MONTHS[int(m.group(1)) - 1]} {m.group(3)}"
    m = re.search(r"\b(\d{4})-(\d{1,2})-\d{1,2}\b", value or "")
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{_MONTHS[int(m.group(2)) - 1]} {m.group(1)}"
    return None


# What each type searches for, to tell the type of a record saved before
# the type itself was.
_TYPE_MARKS = {"Utility bill": {"total_electricity_bill", "total_gas_bill", "total_water_bill"},
               "Bank statement": {"ending_balance", "beginning_balance", "total_debits"},
               "Appraisal": {"appraised_as_is_value", "cap_rate", "appraised_date"},
               "Lease": {"monthly_rent", "lease_begin_date", "security_deposit"},
               "Tax bill": {"total_tax_due", "tax_year", "parcel_id"}}


def _kind_from_fields(names):
    hits = [(len(marks & names), kind) for kind, marks in _TYPE_MARKS.items()]
    hits = [h for h in hits if h[0]]
    # Searched for every schema at once (an unrecognised document): no type.
    return max(hits)[1] if len(hits) == 1 else None


def display_title(entry):
    """What the sidebar calls a record, the way a chat app names a chat: the
    title the analysis gave it; else the kind of document, who it is about
    and when, from its fields; else the file name made readable."""
    title = (entry.get("title") or "").strip()
    # A typed question names a chat with no document. With a document, the
    # first thing typed ("extract the data from the bill") names nothing.
    if title and (entry.get("title_ai") or not entry.get("file")):
        return title
    found = {}
    for r in entry.get("fields") or []:
        if r.get("verdict") in ("yes", "check") and r.get("value") and r.get("field") not in found:
            found[r["field"]] = r["value"].strip()
    kind = _TYPE_NAMES.get(entry.get("doc_type") or "") or _kind_from_fields(
        {r.get("field") for r in entry.get("fields") or []})
    who = next((found[k] for k in ("provider_name", "property_name", "landlord_name",
                                   "tax_authority", "address", "property_address") if found.get(k)), None)
    if who:
        who = re.split(r"[,(\n]", who)[0].strip()[:40]
        # A label read as its own value ("Address") names nothing.
        if who.lower() in {"address", "property", "name", "provider", "n/a", "none"}:
            who = None
    when = next((m for m in (_month(found.get(k)) for k in (
        "billing_date", "date", "statement_date", "appraised_date", "tax_bill_date",
        "lease_begin_date", "lease_date")) if m), None)
    parts = [p for p in (kind, who, when) if p]
    if len(parts) >= 2:
        return " \u00b7 ".join(parts)
    stem = os.path.splitext(entry.get("file") or "")[0]
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or "Untitled"


def forget(rid):
    gone = False
    for path in (_path(rid), _text_path(rid), _layout_path(rid),
                 _source_path(rid) or _path(rid)):
        try:
            os.remove(path)
            gone = True
        except OSError:
            pass
    return gone


def _trim(keep=KEEP):
    """Drop the oldest records once there are more than the cap.

    Counting the files first means the usual case, which is being under the
    cap, costs one listdir rather than reading and sorting every record on
    every single run.
    """
    try:
        count = sum(1 for n in os.listdir(HISTORY_DIR) if _is_record(n))
    except OSError:
        return
    if count <= keep:
        return
    for stale in summaries(limit=10 ** 6)[keep:]:
        forget(stale["id"])
