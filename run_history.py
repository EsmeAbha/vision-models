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
import time

_here = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(_here, "run_history")

KEEP = 200
_ID_OK = re.compile(r"^[0-9a-f]{8,64}$")


def _path(rid):
    return os.path.join(HISTORY_DIR, f"{rid}.json")


def _text_path(rid):
    return os.path.join(HISTORY_DIR, f"{rid}.txt")


def record(run, model_name=""):
    """Write or refresh this run's record. Returns the path, or None.

    Called again when fields are pulled, so reopening a run shows what was
    found rather than only the transcript.
    """
    rid = str(run.get("id") or "")
    if not _ID_OK.match(rid) or run.get("status") != "done":
        return None
    os.makedirs(HISTORY_DIR, exist_ok=True)

    entry = {
        "id": rid,
        "file": run.get("file", ""),
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
    }
    # Keep the first timestamp across refreshes, so a run does not jump to
    # the top of the list every time its fields are pulled again.
    existing = load(rid)
    if existing:
        entry["at"] = existing.get("at", entry["at"])
        entry["seq"] = existing.get("seq", entry["seq"])

    # The transcript goes beside the record, not inside it, so listing runs
    # never reads it.
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
        if not name.endswith(".json"):
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
        })
    out.sort(key=lambda e: (e.get("seq") or 0, e["at"]), reverse=True)
    return out[:limit]


def forget(rid):
    gone = False
    for path in (_path(rid), _text_path(rid)):
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
        count = sum(1 for n in os.listdir(HISTORY_DIR) if n.endswith(".json"))
    except OSError:
        return
    if count <= keep:
        return
    for stale in summaries(limit=10 ** 6)[keep:]:
        forget(stale["id"])
