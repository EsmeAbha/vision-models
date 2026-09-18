"""Recognise a report that has been handled before, and reuse what was learned.

The first time a layout is seen, the columns and indents are discovered from
the document. That discovery is worth keeping: the second copy of the same
report should not be re-derived, because re-derivation is where variation
creeps in -- a shorter month with fewer line items can yield a slightly
different column edge, and a config that drifts run to run is a config nobody
can check.

A template is matched on strings the report prints about itself (its title,
its report id, its column headings) plus the page size. Never on the file
name, which the sender chooses and changes.

Stored as JSON, one file per template, so a person can read and correct one
without running anything.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

_here = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(os.path.dirname(_here), "templates")


def _norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def fingerprint(tokens, head_rows=12):
    """Identifying strings from the top of page 1, plus the page geometry.

    The top of a system-generated report is where it names itself: the entity,
    the report title, the period, the column headings. Those are stable across
    months; the figures below them are not.
    """
    from . import geometry as G

    p1 = [t for t in tokens if t["page"] == 1]
    rows = G.group_rows(p1)[:head_rows]
    lines = [_norm(G.row_text(r)) for r in rows]
    lines = [ln for ln in lines if ln]

    width = max((t["x1"] for t in p1), default=0)
    height = max((t["bottom"] for t in p1), default=0)
    return {
        "head_lines": lines,
        "page_w": round(width / 10) * 10,      # coarse, so a hairline differs not
        "page_h": round(height / 10) * 10,
        "n_pages": len({t["page"] for t in tokens}),
    }


_MONTHS = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"(uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\b", re.I)


def _strip_variable(line):
    """Drop the parts that change every period, keep the shape.

    A period line reads "period = oct 2024-dec 2024" one quarter and
    "jan 2025-mar 2025" the next. Masking the digits alone left those two at
    0.71 similarity -- over the threshold, but uncomfortably close to falling
    off it on a report with more variable lines. Month names move exactly as
    the dates do and identify nothing, so they go the same way.
    """
    line = _MONTHS.sub("<m>", line)
    line = re.sub(r"\d", "#", line)
    return re.sub(r"#+", "#", line)


def signature(fp, keep=6):
    """A stable, comparable form of a fingerprint. Stays in memory."""
    return [_strip_variable(ln) for ln in fp["head_lines"][:keep]]


def signature_hashes(fp, keep=8):
    """The signature, one-way hashed, for storing on disk.

    Masking the digits is not enough to make a head line safe to keep. The
    entity name survives it -- an owner LLC is letters, not figures -- so a
    registry of "masked" signatures still accumulates a list of every client
    whose reports have been processed. Matching only ever tests whether two
    sets of lines intersect, and hashes intersect exactly as well as the text
    does, so nothing is lost by storing them unreadable.
    """
    import hashlib

    return [hashlib.sha1(ln.encode("utf-8")).hexdigest()[:12]
            for ln in signature(fp, keep=keep)]


def similarity(a, b):
    """0..1 over two signatures (or fingerprints), order-insensitive."""
    sa = set(a if isinstance(a, list) else signature(a))
    sb = set(b if isinstance(b, list) else signature(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def load_all(directory=TEMPLATE_DIR):
    out = []
    if not os.path.isdir(directory):
        return out
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, fn), encoding="utf-8") as fh:
                cfg = json.load(fh)
            cfg["_path"] = os.path.join(directory, fn)
            out.append(cfg)
        except Exception as e:
            print(f"registry: skipping {fn}: {type(e).__name__}: {e}", flush=True)
    return out


def match(tokens, directory=TEMPLATE_DIR, threshold=0.6):
    """(config, score) for the best-matching known template, or (None, best).

    Below the threshold nothing is returned rather than the nearest thing.
    Reusing a config from a report that merely looks similar would apply
    another layout's column edges to this one, and the failure would be
    silent -- values landing in the wrong column still look like values.
    """
    fp = fingerprint(tokens)
    best, best_score = None, 0.0
    for cfg in load_all(directory):
        s = similarity(signature_hashes(fp), cfg.get("signature", []))
        if s > best_score:
            best, best_score = cfg, s
    if best_score >= threshold:
        return best, best_score
    return None, best_score


def save(template_id, tokens, cols, item_x0, total_x0, column_names,
         directory=TEMPLATE_DIR, notes=""):
    """Record what was learned about this layout."""
    os.makedirs(directory, exist_ok=True)
    fp = fingerprint(tokens)
    cfg = {
        "template_id": template_id,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        # Hashed, never the text. The raw head lines carry the entity name
        # and the opening rows of figures, and even after masking the digits
        # the owner name survives. A template registry that quietly
        # accumulates client names is a leak waiting for whoever clones the
        # repo. Put anything a human needs to read in "notes", by hand.
        "signature": signature_hashes(fp, keep=8),
        "page_w": fp["page_w"],
        "page_h": fp["page_h"],
        "columns": [{"name": n, "x1": c["x1"]}
                    for n, c in zip(column_names, cols)],
        "indents": {"item_x0": item_x0, "total_x0": total_x0},
        "validations": ["section_total", "unbinned_token", "ragged_row"],
        "notes": notes,
    }
    path = os.path.join(directory, f"{template_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return path


def columns_from(cfg):
    """Config columns in the shape the parser expects."""
    return [{"x1": c["x1"], "n": 0, "lo": c["x1"] - 6, "hi": c["x1"] + 6}
            for c in cfg.get("columns", [])]


def column_names(cfg):
    return [c["name"] for c in cfg.get("columns", [])]
