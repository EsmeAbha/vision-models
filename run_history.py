"""What was read before, so a session can be picked up rather than redone.

One JSON per run under run_history/, newest read first. The transcript is
kept with it: re-reading a document costs a model load and a GPU, and the
text has already been paid for once.

Records are capped by count rather than age. A long document is a few tens of
kilobytes, so the cap is about keeping the folder legible, not about space.
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
        "text": run.get("text") or "",
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

    with open(_path(rid), "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2)
    _trim()
    return _path(rid)


def load(rid):
    if not _ID_OK.match(str(rid or "")):
        return None
    try:
        with open(_path(rid), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


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
        entry = load(name[:-5])
        if not entry:
            # A half-written or hand-edited record costs itself, not the list.
            print(f"history: skipping {name}", flush=True)
            continue
        out.append({
            "id": entry["id"], "file": entry["file"],
            "model": entry["model"], "model_name": entry["model_name"],
            "at": entry["at"], "seq": entry.get("seq", 0),
            "elapsed": entry.get("elapsed"),
            "chars": len(entry.get("text") or ""),
            "fields": len(entry.get("fields") or []),
        })
    out.sort(key=lambda e: (e.get("seq") or 0, e["at"]), reverse=True)
    return out[:limit]


def forget(rid):
    try:
        os.remove(_path(rid))
        return True
    except OSError:
        return False


def _trim(keep=KEEP):
    entries = summaries(limit=10 ** 6)
    for stale in entries[keep:]:
        forget(stale["id"])
