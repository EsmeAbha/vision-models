"""Read the pages a PDF already carries the text for, instead of OCR-ing them.

A generated PDF -- a bill, a statement, an appraisal written in Word -- has
its exact text in the file. Rendering that to pixels and reading it back with
OCR is slow (a GPU model per page, and minutes of engine start-up) and can
only lose accuracy. So each page is checked first: one with a usable text
layer is read from it directly, tables included; only a page that is really
an image goes to OCR. A scan that was OCR'd before it reached us (a
"searchable" PDF) has a text layer too, and is read the same way.

The output has the shape the OCR gives -- plain lines, and tables as HTML
<table> markup in reading order -- so field search, the table workbook and
the review screen treat both the same.

Measured on a 195-page appraisal: 166 pages had text; 91 of them read
directly in about 9s, and 76 whose tables came out with merged columns went
to OCR with the 29 scanned ones -- 105 pages OCR'd instead of 195. A digital
bill needs no OCR at all, so the GPU engine is never started for it.
"""
from __future__ import annotations

import html
import re

import pdfplumber

# Fewer letters and digits than this and a page is not worth trusting.
MIN_ALNUM = 60
# A page mostly covered by images, with little text, is a scan with a header
# or a stamp printed on it: its real content is in the picture.
SCAN_IMAGE_SHARE = 0.5
SCAN_MAX_ALNUM = 300
# Fonts without a usable encoding come out as "(cid:12)"; such a page reads
# as noise and is better OCR'd.
MAX_CID_SHARE = 0.05


def _usable(page, text):
    alnum = sum(c.isalnum() for c in text)
    if alnum < MIN_ALNUM:
        return False
    if text.count("(cid:") * 8 > max(len(text), 1) * MAX_CID_SHARE:
        return False
    area = (page.width * page.height) or 1
    covered = sum(max(0, (i["x1"] - i["x0"])) * max(0, (i["bottom"] - i["top"]))
                  for i in page.images) / area
    if covered > SCAN_IMAGE_SHARE and alnum < SCAN_MAX_ALNUM:
        return False
    return True


def _table_html(rows):
    cells = []
    for row in rows:
        if not any((c or "").strip() for c in row):
            continue
        tds = "".join(f"<td>{html.escape(re.sub(r'\\s+', ' ', c or '').strip())}</td>" for c in row)
        cells.append(f"<tr>{tds}</tr>")
    return f"<table border=1>{''.join(cells)}</table>" if cells else ""


_NUM = re.compile(r"\d[\d,]*\.?\d*")


def _merged(rows):
    """A table whose columns came out merged: values crammed into one cell,
    or most cells empty. Borderless tables in generated reports do this, and
    the OCR's layout model separates them properly -- such a page is OCR'd."""
    cells = [c for r in rows for c in r]
    empty = sum(1 for c in cells if not (c or "").strip()) / max(len(cells), 1)
    stacked = any(c and "\n" in c and len(_NUM.findall(c)) >= 2 for c in cells)
    crammed = any(c and len(_NUM.findall(c)) >= 3 and len(c) > 40 for c in cells)
    return empty > 0.4 or stacked or crammed


def _inside(obj, box):
    x0, top, x1, bottom = box
    cx = (obj["x0"] + obj["x1"]) / 2
    cy = (obj["top"] + obj["bottom"]) / 2
    return x0 <= cx <= x1 and top <= cy <= bottom


def page_text(page):
    """One page as lines and HTML tables, top to bottom; None when a table on
    it did not come apart cleanly and the page is better OCR'd."""
    tables = []
    for t in page.find_tables():
        rows = t.extract()
        # A "table" of one row or one column is a ruled box around text,
        # better left as lines.
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
            if _merged(rows):
                return None
            tables.append((t.bbox, rows))
    boxes = [b for b, _ in tables]
    rest = page.filter(lambda o: not any(_inside(o, b) for b in boxes)) if boxes else page
    blocks = []
    for line in rest.extract_text_lines(x_tolerance=1.5, y_tolerance=3, return_chars=False):
        text = line["text"].strip()
        if text:
            blocks.append((line["top"], text))
    for (x0, top, x1, bottom), rows in tables:
        markup = _table_html(rows)
        if markup:
            blocks.append((top, markup))
    blocks.sort(key=lambda b: b[0])
    return "\n".join(b for _, b in blocks)


def read(path, pages=None):
    """{page number: text} for every page with a usable text layer.

    `pages` limits it to those page numbers (1-based); a page missing from
    the result needs OCR.
    """
    out = {}
    with pdfplumber.open(path) as pdf:
        wanted = pages or range(1, len(pdf.pages) + 1)
        for n in wanted:
            if not 1 <= n <= len(pdf.pages):
                continue
            page = pdf.pages[n - 1]
            try:
                text = page_text(page)
            except Exception:
                text = None     # unreadable here; OCR will have it
            if text is not None and _usable(page, text):
                out[n] = text
            page.flush_cache()
    return out
