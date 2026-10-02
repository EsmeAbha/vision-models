"""A deal's workbook: where its fields go, and filling them in as they arrive.

A deal is a folder under deals/. It holds the template exactly as it was
supplied, a working copy that accumulates, and a JSON that records where each
field belongs. Plain files, so a person can open the folder and see what the
machine thinks without running anything, which is the bargain doc_types/ and
ocr_skills/ already make.

The mapping is proposed by reading the workbook rather than declared. Your
template carries labels the same way your documents do, so the sheet is
scanned for a cell whose text names a field and the value is written beside
it. The proposal is shown before anything is written and can be corrected,
and the correction is what gets stored: the next document into the same deal
reuses it rather than re-deriving and drifting.

Nothing is ever written to the template itself.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

import openpyxl

import field_search

_here = os.path.dirname(os.path.abspath(__file__))
DEAL_DIR = os.path.join(_here, "deals")

TEMPLATE_NAME = "template.xlsx"
WORKING_NAME = "filled.xlsx"
RECORD_NAME = "deal.json"

_CELL = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]{0,6})$")
_ID_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# How far right of a label to look for its empty cell before giving up. A
# label and its value sit next to each other on a form; three columns is
# already generous and stops a scan wandering into the next block.
REACH = 3


def slug(name):
    out = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return (out or "deal")[:64]


# ----------------------------------------------------------------- scanning

def _as_text(value):
    return "" if value is None else str(value).strip()


def _is_blank(cell):
    return cell.value is None or str(cell.value).strip() == ""


def _label_keys(fields):
    """(label, field, how closely that label names the field) for every
    spelling worth looking for.

    Closeness matters more than order on the page. "utility" is a listed
    synonym for Provider name, so a section heading reading UTILITY will
    match it, and on a template with an actual "Provider" row two lines
    below, the heading would win simply by being first. Ranking the field's
    own name above a shared word, and a shared word above an unrelated
    synonym, puts that right.
    """
    out = []
    for field in fields:
        own = field_search._key(field)
        own_words = set(own.split())
        for key in field_search.labels_for(field):
            if key == own:
                closeness = 0
            elif own_words & set(key.split()):
                closeness = 1
            else:
                closeness = 2
            out.append((key, field, closeness))
    return out


def propose_mapping(path, fields):
    """Where each field looks like it belongs in this workbook.

    Every cell that names a field is collected first, then the best label
    for each field is chosen, then targets are handed out. Doing it in that
    order stops a vague match early on the sheet from claiming a field that
    a precise match further down was going to fill.

    A field the workbook never mentions is simply absent: a template with no
    room for a field should not grow one.
    """
    book = _open(path)
    try:
        keys = _label_keys(fields)
        found = {}
        for sheet in book.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    text = _as_text(cell.value)
                    if not text or len(text) > 80:
                        continue
                    flat = field_search._key(text.rstrip(":").strip())
                    if not flat:
                        continue
                    for key, field, closeness in keys:
                        if flat != key:
                            continue
                        found.setdefault(field, []).append({
                            "sheet": sheet.title, "cell": cell,
                            "label_cell": cell.coordinate, "label_text": text,
                            "matched": key, "closeness": closeness,
                        })

        # Closest naming first, then the longer label, then up the page.
        order = sorted(
            found,
            key=lambda f: min((c["closeness"], -len(c["matched"]),
                               c["cell"].row, c["cell"].column)
                              for c in found[f]))

        taken, out = set(), {}
        for field in order:
            candidates = sorted(
                found[field],
                key=lambda c: (c["closeness"], -len(c["matched"]),
                               c["cell"].row, c["cell"].column))
            for candidate in candidates:
                sheet = book[candidate["sheet"]]
                target = _target_for(sheet, candidate["cell"], taken)
                if target is None:
                    continue
                out[field] = {
                    "field": field, "sheet": candidate["sheet"],
                    "label_cell": candidate["label_cell"],
                    "label_text": candidate["label_text"],
                    "matched": candidate["matched"],
                    "cell": target[0], "how": target[1],
                }
                taken.add((candidate["sheet"], target[0]))
                break
        return [out[f] for f in fields if f in out]
    finally:
        book.close()


def _target_for(sheet, label, taken):
    """The cell a value next to this label would go in, or None.

    Right first, because that is how a form reads, then directly below for
    the column-heading layout. A cell that already holds something is left
    alone: that is someone else's data, or another label.
    """
    for step in range(1, REACH + 1):
        right = sheet.cell(row=label.row, column=label.column + step)
        if not _is_blank(right):
            break
        if (sheet.title, right.coordinate) in taken:
            # Another field already owns this label's cell. One label
            # means one value, so this field has no home here.
            # Wandering further right would hand "Billing date" the
            # cell beside the template's only "Statement date" row
            # and quietly invent a column for it.
            return None
        return right.coordinate, "right of the label" if step == 1 else \
            f"{step} cells right of the label"

    below = sheet.cell(row=label.row + 1, column=label.column)
    if _is_blank(below) and (sheet.title, below.coordinate) not in taken:
        return below.coordinate, "under the label"
    return None


def _open(path):
    try:
        return openpyxl.load_workbook(path)
    except Exception as e:
        # openpyxl trips over a ColumnDimension 'level' on some workbooks.
        # Say which file and why, rather than an empty failure.
        raise RuntimeError(f"could not open {os.path.basename(path)}: "
                           f"{type(e).__name__}: {e}") from e


# ------------------------------------------------------------------- deals

def deal_path(deal_id, *parts):
    return os.path.join(DEAL_DIR, deal_id, *parts)


def load_deal(deal_id):
    record = deal_path(deal_id, RECORD_NAME)
    if not os.path.isfile(record):
        return None
    with open(record, encoding="utf-8") as fh:
        deal = json.load(fh)
    deal["id"] = deal_id
    return deal


def list_deals():
    out = []
    if not os.path.isdir(DEAL_DIR):
        return out
    for name in sorted(os.listdir(DEAL_DIR)):
        try:
            deal = load_deal(name)
        except (OSError, ValueError) as e:
            print(f"deals: skipping {name}: {type(e).__name__}: {e}", flush=True)
            continue
        if deal:
            out.append(deal)
    return out


def save_deal(deal):
    deal = dict(deal)
    deal_id = deal.pop("id")
    os.makedirs(deal_path(deal_id), exist_ok=True)
    with open(deal_path(deal_id, RECORD_NAME), "w", encoding="utf-8") as fh:
        json.dump(deal, fh, indent=2)
    deal["id"] = deal_id
    return deal


def create_deal(name, workbook_bytes, fields, deal_id=None):
    """Start a deal from the workbook you supplied, and propose its mapping."""
    deal_id = deal_id or slug(name)
    if not _ID_OK.match(deal_id):
        raise ValueError(f"{deal_id!r} is not a usable folder name")
    if os.path.isdir(deal_path(deal_id)):
        raise FileExistsError(f"a deal called {deal_id!r} already exists")

    os.makedirs(deal_path(deal_id), exist_ok=True)
    with open(deal_path(deal_id, TEMPLATE_NAME), "wb") as fh:
        fh.write(workbook_bytes)

    try:
        proposed = propose_mapping(deal_path(deal_id, TEMPLATE_NAME), fields)
    except Exception:
        shutil.rmtree(deal_path(deal_id), ignore_errors=True)
        raise

    # The working copy is what accumulates; the template stays as supplied.
    shutil.copyfile(deal_path(deal_id, TEMPLATE_NAME),
                    deal_path(deal_id, WORKING_NAME))

    return save_deal({
        "id": deal_id,
        "name": name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mapping": proposed,
        "confirmed": False,
        "filled": {},
        "history": [],
    })


def set_mapping(deal_id, mapping):
    """Store a corrected mapping. This is what later documents reuse."""
    deal = load_deal(deal_id)
    if deal is None:
        raise FileNotFoundError(deal_id)
    clean = []
    seen = set()
    for row in mapping:
        field = str(row.get("field") or "").strip()
        if not field:
            raise ValueError("a mapping row needs a field name")
        if field in seen:
            raise ValueError(f"{field!r} is mapped twice")
        seen.add(field)
        cell = str(row.get("cell") or "").upper()
        if not _CELL.match(cell):
            raise ValueError(f"{field!r} points at {cell!r}, "
                             f"which is not a cell")
        clean.append({"field": field, "sheet": row.get("sheet") or "",
                      "cell": cell, "label_cell": row.get("label_cell", ""),
                      "label_text": row.get("label_text", ""),
                      "matched": row.get("matched", ""),
                      "how": row.get("how", "set by hand")})
    deal["mapping"] = clean
    deal["confirmed"] = True
    return save_deal(deal)


def apply_values(deal_id, values, source=""):
    """Write what this document found into the deal's working copy.

    Values accumulate: a later document fills cells an earlier one left
    empty. A cell already carrying a value from an earlier run is reported
    rather than overwritten, so two documents disagreeing is visible instead
    of silently resolved by whichever ran last.
    """
    deal = load_deal(deal_id)
    if deal is None:
        raise FileNotFoundError(deal_id)

    working = deal_path(deal_id, WORKING_NAME)
    book = _open(working)
    written, skipped, clashed = [], [], []
    try:
        for row in deal["mapping"]:
            field = row["field"]
            raw = values.get(field)
            if raw is None or str(raw).strip() == "":
                skipped.append({"field": field, "cell": row["cell"],
                                "why": "not found in this document"})
                continue

            sheet = book[row["sheet"]] if row.get("sheet") in book.sheetnames \
                else book.active
            already = deal["filled"].get(field)
            if already and str(already.get("value")) != str(raw):
                clashed.append({"field": field, "cell": row["cell"],
                                "kept": already.get("value"), "offered": raw,
                                "from": already.get("source", "")})
                continue
            if already:
                continue

            sheet[row["cell"]] = raw
            written.append({"field": field, "cell": row["cell"], "value": raw,
                            "sheet": sheet.title})
            deal["filled"][field] = {"value": raw, "cell": row["cell"],
                                     "source": source}
        book.save(working)
    finally:
        book.close()

    deal["history"].append({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source,
        "written": len(written), "skipped": len(skipped),
        "clashed": len(clashed),
    })
    save_deal(deal)
    return {"written": written, "skipped": skipped, "clashed": clashed,
            "workbook": working}
