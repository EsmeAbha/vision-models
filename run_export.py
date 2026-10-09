"""The two workbooks a finished run can be downloaded as.

Both are handed to another tool's API, so neither works anything out. The
fields workbook is the values as they were found on the page and nothing else
-- no totals, no derived columns, no reformatting of a number into what a
spreadsheet would rather see. A value that was not printed is left empty
rather than filled in with a zero, because an empty cell and a zero mean
different things to whatever reads this next.

    fields_workbook  one row per document, one column per field
    tables_workbook  the tables the reader found, a sheet each
"""
from __future__ import annotations

import io
import os
import posixpath

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

import save_output

# The three columns that identify the document, before the fields start.
LEAD = ["Path", "Folder Name", "File Name", "Page"]


def split_path(path, name=""):
    """(path, folder, file) for a document, from whatever the client knew.

    A browser upload only carries the file name -- the directory it came from
    is not something a page is allowed to see -- so Path and Folder Name stay
    empty unless the file arrived through a folder pick, which does carry a
    relative path. They are columns the next tool fills or ignores; inventing
    a path here would be worse than leaving it blank.
    """
    raw = (path or "").replace("\\", "/").strip("/")
    leaf = os.path.basename(raw) or (name or "")
    folder = posixpath.dirname(raw)
    return raw, folder, leaf


def fields_workbook(runs, out_path, fields=None):
    """Path | Folder Name | File Name | <one column per field>.

    `runs` is a list of {path, file, rows}, where rows are the field records
    find_fields produced. One row of the sheet per document, so a batch and a
    single read have the same shape.

    The column order follows `fields` when given -- the document type's own
    order, which is the order the other tool expects -- rather than the order
    values happened to come back in.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Fields"

    names = list(fields or [])
    for run in runs:
        for row in run.get("rows") or []:
            if row.get("field") and row["field"] not in names:
                names.append(row["field"])

    header = LEAD + names
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="center")

    for run in runs:
        path, folder, leaf = split_path(run.get("path"), run.get("file", ""))
        # One row per page, not per file. Twelve monthly bills in a twelve page
        # PDF are twelve records; collapsing them into one row keeps page 1 and
        # loses the other eleven without saying so.
        by_page = {}
        for row in run.get("rows") or []:
            if not row.get("field"):
                continue
            by_page.setdefault(row.get("page") or 1, {})[row["field"]] = (
                row.get("value") or "")
        for page in sorted(by_page):
            found = by_page[page]
            # Written as text exactly as printed. An account number with a
            # leading zero, or an amount with its currency symbol, is the
            # value that was on the page; letting a spreadsheet retype it as a
            # number loses both.
            ws.append([path, folder, leaf, page]
                      + [found.get(n, "") for n in names])

    widths = [34, 22, 28, 6] + [20] * len(names)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "E2"

    _evidence_sheet(wb, runs)
    _save(wb, out_path)
    return out_path


def _evidence_sheet(wb, runs):
    """Where each value came from, on a sheet of its own.

    Kept apart from the Fields sheet so that one stays exactly the shape the
    next tool reads, and kept at all because a value with no provenance cannot
    be checked by the person who has to sign off on it.
    """
    ws = wb.create_sheet("Evidence")
    ws.append(["File Name", "Page", "Field", "Value", "Verdict",
               "Matched on", "Line"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for run in runs:
        _p, _f, leaf = split_path(run.get("path"), run.get("file", ""))
        for row in run.get("rows") or []:
            ws.append([leaf, row.get("page") or 1, row.get("field", ""),
                       row.get("value", ""), row.get("verdict", ""),
                       row.get("where", ""), row.get("evidence", "")])
    for i, w in enumerate([28, 6, 26, 26, 10, 18, 70], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def tables_workbook(text, out_path):
    """The tables the reader rendered, one sheet per table.

    Read back out of the transcript rather than rebuilt, so what lands in the
    sheet is what the page showed. Headers are written as an ordinary first
    row: naming them would mean deciding which row is the header, and that is
    a judgement the next tool should make, not this one.
    """
    import pandas as pd

    pages = save_output.split_pages(text or "")
    used, wrote = set(), 0
    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        for label, content in pages:
            if "<table" not in content:
                continue
            try:
                tables = pd.read_html(io.StringIO(content))
            except Exception:
                continue
            for j, df in enumerate(tables):
                if df.empty:
                    continue
                base = label if j == 0 else f"{label}_{j + 1}"
                df.to_excel(xw, sheet_name=save_output._sheet_name(base, used),
                            index=False, header=False)
                wrote += 1
        if not wrote:
            # ExcelWriter refuses to save a workbook with no sheets, and a
            # file that says why is better than a failed download.
            pd.DataFrame({"note": ["the reader found no table on this document"]}
                         ).to_excel(xw, sheet_name="empty", index=False)
    return out_path


def _save(wb, out_path):
    folder = os.path.dirname(os.path.abspath(out_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    wb.save(out_path)
