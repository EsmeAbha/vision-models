"""Write the workbook, and mark everything that could not be proven.

Two sheets, following the house style in the knowledge pack:

  "Source (as printed)"  one row per physical line of the report, totals kept,
                         values exactly as they were printed.
  "Issues"               one row per failed check, with the reason, the file,
                         the page and the row, so a person can go straight to
                         the place in the PDF and settle it.

Any cell touched by a failed check is filled yellow and carries a Review Note.
The rule the users stated is that a doubtful figure is never silently dropped
and never silently kept: it stays visible, and it says why it is doubtful.
Blank would hide it; a confident value would be worse.
"""
from __future__ import annotations

import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

YELLOW = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
HEADER = Font(bold=True)
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(bottom=THIN)
MONEY = "#,##0.00;(#,##0.00)"
WHOLE = "#,##0;(#,##0)"
# The figure is stored as it was printed -- 13.0, not 0.13 -- and the format
# puts the sign back. Storing 0.13 so that Excel's own percent format applies
# would mean the cell no longer holds the number on the page, and every sheet
# here is meant to be checkable against the document line by line.
PCT = '0.0"%"'
TEXT = "@"


def _flagged_rows(results):
    """(page, y) -> {"why": [reasons], "cols": {column indexes}}.

    The column is kept, not just the row. A reader asked to check a flagged
    line should be looking at the figure that did not reconcile, and on a
    table nine columns wide a whole highlighted row does not tell them which
    one that is.
    """
    flags = {}
    for r in results:
        if r["ok"]:
            continue
        key = (r.get("page"), r.get("y"))
        if r["diff"] is not None:
            why = (f"{r['check']}: printed {r['expected']:,.2f}, computed "
                   f"{r['got']:,.2f} (difference {r['diff']:,.2f}) - {r['note']}")
        else:
            why = f"{r['check']}: {r['got']} ({r['note']})"
        f = flags.setdefault(key, {"why": [], "cols": set()})
        f["why"].append(why)
        scope = str(r.get("scope", ""))
        if scope.startswith("col") and scope[3:].isdigit():
            f["cols"].add(int(scope[3:]))
    return flags


def _column_formats(rows, n_cols):
    """The printed type of each column, so the sheet reads like the report."""
    from .tabular import classify

    out = []
    for i in range(n_cols):
        kinds = {}
        for r in rows:
            v = r["values"][i] if i < len(r["values"]) else None
            if v:
                k = classify(v)
                kinds[k] = kinds.get(k, 0) + 1
        if not kinds:
            out.append(None)
            continue
        kind = max(kinds, key=kinds.get)
        out.append({"percent": PCT, "date": TEXT, "money": MONEY,
                    "number": WHOLE}.get(kind))
    return out


def write(path, rows, cols, results, source_name="", column_names=None,
          sheet_name="Source (as printed)"):
    """Write the workbook. Returns (n_rows, n_flagged)."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    names = column_names or [f"Value {i + 1}" for i in range(len(cols))]
    head = ["Page", "Line", "Label"] + list(names) + ["Review Note"]
    ws.append(head)
    for c in range(1, len(head) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = HEADER
        cell.border = BOX
        cell.alignment = Alignment(vertical="bottom", wrap_text=False)

    flags = _flagged_rows(results)
    formats = _column_formats(rows, len(cols))
    n_flagged = 0

    for i, r in enumerate(rows, start=1):
        flag = flags.get((r["page"], r["y"]))
        note = "; ".join(flag["why"]) if flag else ""
        # Values are written as numbers where they parse, so the sheet can be
        # used for arithmetic, and as the printed string where they do not --
        # never reformatted into something the report did not say.
        vals = []
        for v in r["values"]:
            if v is None:
                vals.append(None)
                continue
            from .tabular import parse_number
            n = parse_number(v)
            vals.append(n if n is not None else v)

        ws.append([r["page"], i, r["label"]] + vals + [note or None])
        excel_row = ws.max_row

        for j in range(len(names)):
            cell = ws.cell(row=excel_row, column=4 + j)
            fmt = formats[j] if j < len(formats) else None
            if fmt and isinstance(cell.value, (int, float)):
                cell.number_format = fmt
            elif isinstance(cell.value, float):
                cell.number_format = MONEY

        if note:
            n_flagged += 1
            # The label and the note are marked so the row can be found, and
            # then only the figures that actually failed.
            for c in (3, len(head)):
                ws.cell(row=excel_row, column=c).fill = YELLOW
            for j in sorted(flag["cols"]):
                if j < len(names):
                    ws.cell(row=excel_row, column=4 + j).fill = YELLOW

    widths = [6, 6, 52] + [16] * len(names) + [70]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    _issues_sheet(wb, results, source_name)
    wb.save(path)
    return ws.max_row - 1, n_flagged


def _issues_sheet(wb, results, source_name):
    ws = wb.create_sheet("Issues")
    ws.append(["Check", "Page", "Row label", "Printed", "Computed",
               "Difference", "Why it is flagged"])
    for c in range(1, 8):
        ws.cell(row=1, column=c).font = HEADER
        ws.cell(row=1, column=c).border = BOX

    failures = [r for r in results if not r["ok"]]
    for r in failures:
        ws.append([
            r["check"], r.get("page"), r["label"],
            r["expected"] if isinstance(r["expected"], (int, float)) else str(r["expected"]),
            r["got"] if isinstance(r["got"], (int, float)) else str(r["got"]),
            r["diff"], r["note"],
        ])
        for c in range(1, 8):
            ws.cell(row=ws.max_row, column=c).fill = YELLOW

    passed = len(results) - len(failures)
    ws.append([])
    ws.append(["Summary", f"{passed} of {len(results)} checks passed"])
    ws.cell(row=ws.max_row, column=1).font = HEADER
    ws.append(["Source", os.path.basename(source_name) if source_name else ""])
    ws.append(["Written", datetime.now().strftime("%Y-%m-%d %H:%M")])
    if not failures:
        ws.append([])
        ws.append(["Every printed total reconciles with the figures above it. "
                   "No cell required a review flag."])

    for col, w in zip("ABCDEFG", (18, 6, 46, 16, 16, 14, 60)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    return ws
