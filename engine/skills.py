"""What was learned about a kind of report, kept so it is not worked out twice.

The first time a report type is handled, the layout has to be discovered: where
the columns sit, which rows are sections, which rows are the report talking
about itself rather than data, which columns carry a repeating series. That
discovery is the expensive part and the part that varies, so it is written
down -- as a recipe a machine applies and a note a person can read and correct.

The next document of the same kind is recognised from what it prints about
itself and the recipe is reused. A document that matches nothing is handled
from scratch and, once its totals reconcile, becomes a new skill. A document
that matches but then fails its checks is not forced through: the mismatch is
recorded on the skill, because a recipe that has started failing is worth more
as a question than as an answer.

Two rules hold throughout:

* Nothing identifying is stored. The signature is hashed, for the same reason
  the template registry hashes it -- masking digits does not hide an entity
  name, and a skill library that quietly accumulates client names is a leak
  that travels with the repository.

* A skill never suppresses a doubt. Recipes carry flag detectors alongside
  their extraction rules, and a flag raised is a cell coloured and a reason
  written, never a value quietly dropped or quietly kept.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import registry

_here = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(os.path.dirname(_here), "skills")


# --------------------------------------------------------------- the library

def load_all(directory=SKILL_DIR):
    """Every recipe on disk, newest-looking first is not assumed -- order is
    by name so a run is reproducible."""
    out = []
    if not os.path.isdir(directory):
        return out
    for slug in sorted(os.listdir(directory)):
        path = os.path.join(directory, slug, "recipe.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"skills: skipping {slug}: {type(e).__name__}: {e}", flush=True)
            continue
        cfg["_dir"] = os.path.join(directory, slug)
        out.append(cfg)
    return out


def match(tokens, directory=SKILL_DIR, threshold=0.6):
    """(recipe, score) for the best-matching skill, or (None, best_score).

    Below the threshold nothing is returned rather than the nearest thing.
    Applying one report's column positions to another fails silently, because
    a value in the wrong column still looks like a value.
    """
    fp = registry.fingerprint(tokens)
    sig = registry.signature_hashes(fp)
    best, best_score = None, 0.0
    for cfg in load_all(directory):
        s = registry.similarity(sig, cfg.get("signature", []))
        if s > best_score:
            best, best_score = cfg, s
    return (best, best_score) if best_score >= threshold else (None, best_score)


def save(slug, tokens, recipe, notes="", directory=SKILL_DIR):
    """Write a skill: the machine recipe, and the note a person reads."""
    d = os.path.join(directory, slug)
    os.makedirs(d, exist_ok=True)

    fp = registry.fingerprint(tokens)
    cfg = dict(recipe)
    cfg.update({
        "slug": slug,
        "signature": registry.signature_hashes(fp, keep=8),
        "page_w": fp["page_w"],
        "page_h": fp["page_h"],
        "created": cfg.get("created", datetime.now().strftime("%Y-%m-%d %H:%M")),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    cfg.setdefault("runs", {"applied": 0, "reconciled": 0, "failed": 0})
    with open(os.path.join(d, "recipe.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)

    md = os.path.join(d, "SKILL.md")
    if not os.path.isfile(md) or notes:
        with open(md, "w", encoding="utf-8") as fh:
            fh.write(_skill_md(cfg, notes))
    return d


def record_run(cfg, reconciled, note="", directory=SKILL_DIR):
    """Keep score. A recipe that starts failing should say so on its face."""
    d = cfg.get("_dir") or os.path.join(directory, cfg["slug"])
    path = os.path.join(d, "recipe.json")
    try:
        with open(path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
    except (OSError, ValueError):
        return
    runs = on_disk.setdefault("runs", {"applied": 0, "reconciled": 0, "failed": 0})
    runs["applied"] += 1
    runs["reconciled" if reconciled else "failed"] += 1
    on_disk["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if note:
        on_disk.setdefault("history", []).append(
            {"when": on_disk["updated"], "reconciled": bool(reconciled),
             "note": note})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(on_disk, fh, indent=2)


def _skill_md(cfg, notes):
    cols = cfg.get("columns", [])
    flags = cfg.get("flags", [])
    lines = [
        f"# {cfg.get('title', cfg['slug'])}",
        "",
        cfg.get("summary", ""),
        "",
        "## Recognising it",
        "",
        "Matched on the strings the report prints about itself -- its title, "
        "report id and column headings -- hashed, never stored as text. The "
        "file name is not used: the sender chooses it and changes it.",
        "",
        "## Reading it",
        "",
        f"- {len(cols)} columns, pinned once and carried across every page, "
        "because the headings are printed only on the first.",
    ]
    if cfg.get("text_columns"):
        lines.append(f"- {len(cfg['text_columns'])} text column(s), found by "
                     "left edge: numbers align right, words align left.")
    if cfg.get("sections", {}).get("markers"):
        lines.append("- Sections: " + ", ".join(cfg["sections"]["markers"]))
    if cfg.get("series", {}).get("columns"):
        s = cfg["series"]
        lines.append(f"- Repeating series {s['columns']} laid across the row, "
                     f"one group per step.")
    if cfg.get("skip", {}).get("contains"):
        lines.append("- Rows whose label contains "
                     + ", ".join(repr(x) for x in cfg["skip"]["contains"])
                     + " are the report summarising itself, not data. They are "
                       "read as the printed totals to check against, and never "
                       "added to them.")
    lines += ["", "## Checking it", "",
              "Extraction is accepted only when the figures read off the page "
              "reproduce the totals the report prints about itself. The "
              "reconciliation is the evidence; nothing is trusted because it "
              "looked right."]
    if flags:
        lines += ["", "## Known confusions", "",
                  "Raised as a coloured cell and a written reason. Never "
                  "silently corrected.", ""]
        for f in flags:
            lines.append(f"- **{f.get('name')}** -- {f.get('why', '')}")
    if notes:
        lines += ["", "## Notes", "", notes]
    lines += ["", "---", "",
              f"Learned {cfg.get('created', '')}; updated {cfg.get('updated', '')}."]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ applying a skill

def _num(v):
    from .tabular import parse_number
    return parse_number(v) if v else None


def classify(row, cfg):
    """What this row is: 'section', 'summary', 'subtotal', 'data' or 'other'."""
    label = (row["label"] or "").strip()
    sec = cfg.get("sections", {})
    for m in sec.get("markers", []):
        if label.startswith(m):
            return "section", m
    skip = cfg.get("skip", {})
    for frag in skip.get("contains", []):
        if frag in label:
            return "summary", None
    for pre in skip.get("startswith", []):
        if label.startswith(pre):
            return "subtotal", None
    key = cfg.get("data_requires_column")
    if key is not None and _num(row["values"][key] if key < len(row["values"])
                                else None) is None:
        return "other", None
    return "data", None


def apply(cfg, tokens, pages=None):
    """Run a learned recipe. Returns a result dict; raises nothing quietly.

    The return carries the rows, the totals the report printed about itself,
    the reconciliation of one against the other, and every flag raised. A
    caller writing a workbook has everything it needs to colour a cell and say
    why.
    """
    from . import tabular as T

    cols = [{"x1": c["x1"], "n": 0, "lo": c["x1"] - 6, "hi": c["x1"] + 6}
            for c in cfg.get("columns", [])]
    names = [c["name"] for c in cfg.get("columns", [])]

    pages = pages or cfg.get("pages")
    page_list = sorted({t["page"] for t in tokens}) if not pages else list(pages)

    rows = []
    for pg in page_list:
        r, _c, _l = T.read_rows(tokens, page=pg, columns=cols)
        rows += r

    section = None
    data, summaries, flags = [], [], []
    for r in rows:
        kind, marker = classify(r, cfg)
        if kind == "section":
            section = marker
            continue
        if kind == "summary":
            summaries.append(r)
            continue
        if kind in ("subtotal", "other"):
            continue
        r = dict(r)
        r["section"] = section
        data.append(r)
        flags += _flags_for(r, cfg, names)

    totals = _sum_sections(data, cfg)
    printed = _printed_totals(summaries, cfg)
    checks = _reconcile(totals, printed, cfg, names)
    return {"rows": data, "summaries": summaries, "column_names": names,
            "totals": totals, "printed": printed, "checks": checks,
            "flags": flags}


def _flags_for(row, cfg, names):
    out = []
    for f in cfg.get("flags", []):
        if f.get("when") != "label_token_in_column_range":
            continue
        col = f["column"]
        if col < len(row["values"]) and row["values"][col]:
            continue                      # the column read fine, no doubt
        lo, hi = f["x_range"]
        hits = [t["text"] for t in row.get("label_tokens", [])
                if lo <= t["x1"] <= hi]
        if hits:
            out.append({
                "name": f["name"],
                "page": row["page"], "y": row["y"],
                "column": col,
                "label": row["label"],
                "why": f.get("why", ""),
                "detail": f"'{' '.join(hits)}' sits in the "
                          f"{names[col] if col < len(names) else col} column, "
                          f"which read empty",
            })
    return out


def _sum_sections(data, cfg):
    out = {}
    for r in data:
        sec = r.get("section") or "(none)"
        bucket = out.setdefault(sec, {})
        for i, v in enumerate(r["values"]):
            n = _num(v)
            if n is not None:
                bucket[i] = round(bucket.get(i, 0.0) + n, 2)
        bucket["_rows"] = bucket.get("_rows", 0) + 1
    return out


def _printed_totals(summaries, cfg):
    out = {}
    for label, spec in cfg.get("printed", {}).items():
        for r in summaries:
            if (r["label"] or "").startswith(spec["startswith"]):
                out[label] = {i: _num(r["values"][i])
                              for i in range(len(r["values"]))
                              if _num(r["values"][i]) is not None}
                break
    return out


def _reconcile(totals, printed, cfg, names):
    checks = []
    for key, spec in cfg.get("printed", {}).items():
        want = printed.get(key)
        got = totals.get(spec.get("section", key))
        if not want or not got:
            continue
        for i, expected in want.items():
            if i not in got:
                continue
            diff = round(got[i] - expected, 2)
            checks.append({
                "check": "printed_total",
                "scope": f"col{i}",
                "label": f"{key}: {names[i] if i < len(names) else i}",
                "expected": expected, "got": got[i], "diff": diff,
                "ok": abs(diff) <= 0.011,
                "note": f"{got.get('_rows', 0)} row(s) in section "
                        f"{spec.get('section', key)}",
                "page": None, "y": None,
            })
    return checks
