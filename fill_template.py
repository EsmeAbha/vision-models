"""Drop extracted fields into the cells of a spreadsheet you already have.

A template is two files in fill_templates/: the workbook itself, and a JSON
mapping that says which document type it is for and which field belongs in
which cell. Both are plain files a person can read and correct without
running anything, which is the same bargain doc_types/ and ocr_skills/ make.

The workbook is never written to. Every fill copies it into outputs/ under a
timestamped name and writes to the copy, so the blank form survives a mistake.

Values are written as text by default. A spreadsheet would rather have a
number, but converting silently is how a debit of "- $3,880.32" becomes a
positive 3880.32 when the minus has been eaten upstream. So a template opts
in per field with "as_number", and anything that will not parse cleanly is
left as the text that was actually found.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

import openpyxl

_here = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(_here, "fill_templates")
_CELL = re.compile(r"^[A-Za-z]{1,3}[1-9][0-9]{0,6}$")


def load_templates(directory=TEMPLATE_DIR):
    """Every readable template mapping, ordered by filename.

    A broken or half-written mapping costs that one template and is skipped
    with a note, rather than taking the whole list down.
    """
    out = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"templates: skipping {name}: {type(e).__name__}: {e}",
                  flush=True)
            continue

        problem = _problem_with(cfg, directory)
        if problem:
            print(f"templates: skipping {name}, {problem}", flush=True)
            continue

        cfg = dict(cfg)
        cfg["id"] = os.path.splitext(name)[0]
        cfg["path"] = os.path.join(directory, cfg["workbook"])
        out.append(cfg)
    return out


def _problem_with(cfg, directory):
    """Why this mapping cannot be used, or None if it can."""
    for key in ("name", "doc_type", "workbook", "cells"):
        if not cfg.get(key):
            return f"no {key}"
    if not isinstance(cfg["cells"], dict):
        return "cells is not an object"
    if os.path.basename(cfg["workbook"]) != cfg["workbook"]:
        return "workbook must be a bare filename in the same folder"
    if not os.path.isfile(os.path.join(directory, cfg["workbook"])):
        return f"workbook {cfg['workbook']} is missing"
    for field, ref in cfg["cells"].items():
        if not _CELL.match(str(ref)):
            return f"{field!r} points at {ref!r}, which is not a cell"
    return None


def for_doc_type(name, directory=TEMPLATE_DIR):
    return [t for t in load_templates(directory) if t["doc_type"] == name]


def as_number(text):
    """The number inside a found value, or None if it is not cleanly one.

    Deliberately strict. "1,204.90" and "$5,940.20" are numbers; "08/31/2026"
    and "+ $5,569.77 (6 items)" are not, and guessing at those is how a wrong
    figure gets into a spreadsheet looking like it was typed there.
    """
    if text is None:
        return None
    s = str(text).strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    s = re.sub(r"^[+\-]\s*", lambda m: "-" if "-" in m.group() else "", s)
    s = re.sub(r"[$£€]\s?", "", s).replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    value = float(s)
    if negative:
        value = -abs(value)
    return int(value) if value.is_integer() and "." not in s else value


def fill(template, values, out_dir="outputs", stem=None):
    """Copy the workbook and write the values into it.

    `values` maps field name to the text that was found. A field with no
    value, or one the template does not place, is simply not written, so a
    half-read document gives a half-filled form rather than a wrong one.

    Returns (path, written, skipped).
    """
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = stem or os.path.splitext(os.path.basename(template["workbook"]))[0]
    dest = os.path.join(out_dir, f"{base}_{stamp}.xlsx")

    # Copy first, then edit the copy. The blank form is the thing you cannot
    # regenerate, so it never gets opened for writing.
    shutil.copyfile(template["path"], dest)

    try:
        book = openpyxl.load_workbook(dest)
    except Exception as e:
        os.remove(dest)
        # openpyxl trips over a ColumnDimension 'level' on some workbooks.
        # Say so plainly rather than reporting an empty failure.
        raise RuntimeError(
            f"openpyxl could not open {template['workbook']}: "
            f"{type(e).__name__}: {e}") from e

    sheet_name = template.get("sheet") or ""
    if sheet_name and sheet_name in book.sheetnames:
        sheet = book[sheet_name]
    elif sheet_name:
        book.close()
        os.remove(dest)
        raise RuntimeError(f"{template['workbook']} has no sheet "
                           f"{sheet_name!r}; it has {book.sheetnames}")
    else:
        sheet = book.active

    numeric = set(template.get("as_number") or [])
    written, skipped = [], []
    for field, ref in template["cells"].items():
        raw = values.get(field)
        if raw is None or str(raw).strip() == "":
            skipped.append({"field": field, "cell": ref, "why": "not found"})
            continue
        out = raw
        if field in numeric:
            number = as_number(raw)
            if number is None:
                skipped.append({"field": field, "cell": ref,
                                "why": f"{raw!r} is not cleanly a number"})
                continue
            out = number
        sheet[ref] = out
        written.append({"field": field, "cell": ref, "value": out})

    book.save(dest)
    book.close()
    return dest, written, skipped
