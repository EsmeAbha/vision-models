"""Render a local file as HTML, so it can be read in the side panel.

The point of the panel is checking: a value pulled off a document is worth
nothing to the person who has to sign it off unless they can put it next to
the page it came from. So this renders the source, not a summary of it --
every row of the sheet, every paragraph of the document, in the order they
were written.

PDFs and images do not come through here at all; a browser draws those better
than anything this could produce, and they are served as themselves.
"""
from __future__ import annotations

import html
import os

# Read as text when the extension says so. Anything else is offered as a
# download rather than guessed at -- a binary rendered as text is a screenful
# of noise that looks like a failure of the reader.
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log",
            ".xml", ".yaml", ".yml", ".ini", ".py", ".js", ".sql"}
SHEET_EXT = {".xlsx", ".xlsm", ".xltx"}
DOC_EXT = {".docx"}
RAW_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".html",
           ".htm"}

MAX_ROWS = 2000
MAX_COLS = 60
MAX_TEXT = 400_000

CSS = """
:root { color-scheme: light; }
body { margin: 0; padding: 18px 20px; background: #F4F3EE; color: #1F4E6B;
       font: 13px/1.55 "Segoe UI", system-ui, sans-serif; }
h2 { font-size: 13px; margin: 22px 0 8px; color: #4C687C;
     text-transform: uppercase; letter-spacing: .06em; }
h2:first-child { margin-top: 0; }
pre { white-space: pre-wrap; word-break: break-word; font: 12px/1.5
      "Cascadia Code", Consolas, ui-monospace, monospace;
      background: #fff; border: 1px solid #E6E6DC; border-radius: 10px;
      padding: 14px; }
/* width:max-content stops the browser squeezing twenty columns into the
   panel: without it every heading wrapped to one character per line, which
   is unreadable. The .scroll wrapper scrolls instead. */
table { border-collapse: collapse; font-size: 12px; background: #fff;
        margin-bottom: 8px; width: max-content; max-width: none; }
td, th { border: 1px solid #DEDFD4; padding: 4px 8px; vertical-align: top;
         min-width: 90px; max-width: 320px; overflow-wrap: break-word; }
thead th { white-space: nowrap; }
thead th { background: #E8EEF4; position: sticky; top: 0; font-weight: 650; }
tbody tr:nth-child(even) td { background: #FAFAF6; }
.scroll { overflow-x: auto; }
p { margin: 0 0 8px; }
.note { color: #8A9CA8; font-size: 12px; }
"""


def kind_of(name):
    """How this file should be shown: raw, html, or not at all."""
    ext = os.path.splitext(name or "")[1].lower()
    if ext in RAW_EXT:
        return "raw"
    if ext in SHEET_EXT or ext in DOC_EXT or ext in TEXT_EXT:
        return "html"
    return "none"


def _page(title, body):
    return ("<!doctype html><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style>{body}")


def _sheet_html(path):
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    out = []
    for name in wb.sheetnames:
        ws = wb[name]
        out.append(f"<h2>{html.escape(name)}</h2><div class='scroll'><table>")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= MAX_ROWS:
                out.append("</table></div>"
                           f"<p class='note'>Stopped after {MAX_ROWS} rows.</p>")
                break
            cells = row[:MAX_COLS]
            tag = "th" if i == 0 else "td"
            body = "".join(
                f"<{tag}>{html.escape('' if c is None else str(c))}</{tag}>"
                for c in cells)
            if i == 0:
                out.append(f"<thead><tr>{body}</tr></thead><tbody>")
            else:
                out.append(f"<tr>{body}</tr>")
        else:
            out.append("</tbody></table></div>")
    wb.close()
    return "".join(out) or "<p class='note'>This workbook has no sheets.</p>"


def _docx_html(path):
    import docx

    d = docx.Document(path)
    out = []
    for p in d.paragraphs:
        text = (p.text or "").strip()
        if text:
            out.append(f"<p>{html.escape(text)}</p>")
    for t in d.tables:
        out.append("<div class='scroll'><table>")
        for row in t.rows:
            cells = "".join(f"<td>{html.escape(c.text.strip())}</td>"
                            for c in row.cells)
            out.append(f"<tr>{cells}</tr>")
        out.append("</table></div>")
    return "".join(out) or "<p class='note'>This document has no text.</p>"


def _text_html(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read(MAX_TEXT + 1)
    note = ""
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT]
        note = f"<p class='note'>Showing the first {MAX_TEXT:,} characters.</p>"
    return f"<pre>{html.escape(text)}</pre>{note}"


def to_html(path, name=""):
    """One local file as a readable page. Never raises."""
    name = name or os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    try:
        if ext in SHEET_EXT:
            body = _sheet_html(path)
        elif ext in DOC_EXT:
            body = _docx_html(path)
        elif ext in TEXT_EXT:
            body = _text_html(path)
        else:
            body = (f"<p class='note'>{html.escape(ext or 'That kind of file')}"
                    " cannot be shown here. Download it to open it.</p>")
    except Exception as e:
        body = ("<p class='note'>This file could not be opened: "
                f"{html.escape(type(e).__name__)}: {html.escape(str(e))}</p>")
    return _page(name, body)
