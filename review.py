"""Put each extracted value back on the page it came from, for sign-off.

An analyst approves a value by checking it against the source, so every field
and every table is given a box on its page. Boxes are fractions of the full,
uncropped page (0..1 from the top-left), which is the one coordinate space
that survives both the OCR's autocropped render and any size the review
screen draws the page at.

Two ways to find a value, best first:
  - the PDF's own text layer: exact, a box around just those characters.
  - the OCR layout: the region (line, paragraph or table) the value was read
    from. Coarser, but it is all a scanned page has.
"""
from __future__ import annotations

import ctypes
import hashlib
import html
import io
import os
import re

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageDraw, ImageFont

import field_search
import pdf_pages
import save_output

# One colour per field, in the order the fields are listed. Tables are always
# green, so green is not in here.
PALETTE = ["#E8590C", "#1C7ED6", "#AE3EC9", "#F08C00", "#D6336C", "#0C8599",
           "#7048E8", "#E03131", "#5C940D", "#1971C2", "#C2255C", "#9C36B5"]
TABLE_COLOR = "#2B8A3E"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
# Fewer characters than this on a page and it is treated as a scan.
MIN_TEXT_LAYER = 20


# --------------------------------------------------------------- the layout

def page_layout(page_no, info, layout):
    """The OCR's blocks for one page, as fractions of the full page.

    The OCR saw an autocropped render, so its pixels are offset by the crop
    and scaled to that render's size; undoing both puts the box back on the
    whole page.
    """
    full_w, full_h = info["full_size"]
    crop = info.get("crop_box") or (0, 0, full_w, full_h)
    sent_w, sent_h = info.get("size") or (full_w, full_h)
    sx = sent_w / max(layout.get("width") or sent_w, 1)
    sy = sent_h / max(layout.get("height") or sent_h, 1)
    blocks = []
    for b in layout.get("blocks") or []:
        x0, y0, x1, y1 = b["bbox"][:4]
        blocks.append({
            "label": b.get("label", ""),
            "box": [round((x0 * sx + crop[0]) / full_w, 5),
                    round((y0 * sy + crop[1]) / full_h, 5),
                    round((x1 * sx + crop[0]) / full_w, 5),
                    round((y1 * sy + crop[1]) / full_h, 5)],
            "text": b.get("text", ""),
        })
    return {"page": page_no, "blocks": blocks}


def _plain(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip().lower()


# ------------------------------------------------------------ page geometry

class _PdfPage:
    """One PDF page's text layer, mapped the way pdf_pages renders it."""

    def __init__(self, page):
        self.page = page
        page_rot = page.get_rotation()
        self.rot = (360 - page_rot) % 360        # what render_page applies
        w, h = page.get_size()
        if self.rot in (90, 270):
            w, h = h, w
        self.w, self.h = w * 10, h * 10          # device units, any scale works
        self.text = page.get_textpage()
        self.has_text = self.text.count_chars() >= MIN_TEXT_LAYER

    def _frac(self, x, y):
        dx, dy = ctypes.c_long(), ctypes.c_long()
        pdfium_c.FPDF_PageToDevice(self.page.raw, 0, 0, int(self.w), int(self.h),
                                   self.rot // 90, x, y,
                                   ctypes.byref(dx), ctypes.byref(dy))
        return dx.value / self.w, dy.value / self.h

    def find(self, needle, limit=40, exact=False):
        """Every place this text is printed, each as one box.

        exact: case and whole words must match, which table cells need --
        "Month" is a column heading, and "1 month" in another table is not it.
        """
        needle = (needle or "").strip()
        if len(needle) < (1 if exact else 2):
            return []
        out = []
        searcher = self.text.search(needle, match_case=exact,
                                    match_whole_word=exact)
        try:
            while len(out) < limit:
                occ = searcher.get_next()
                if not occ:
                    break
                index, count = occ
                xs, ys = [], []
                for i in range(self.text.count_rects(index, count)):
                    left, bottom, right, top = self.text.get_rect(i)
                    for x, y in ((left, bottom), (right, top)):
                        fx, fy = self._frac(x, y)
                        xs.append(fx)
                        ys.append(fy)
                if xs:
                    out.append([round(min(xs), 5), round(min(ys), 5),
                                round(max(xs), 5), round(max(ys), 5)])
        finally:
            searcher.close()
        return out


def _value_needles(value):
    """The value as printed, then shorter forms of it.

    A value can carry the rest of its line ("$109.27 DUE BY 10/26/2025"), and
    the text layer may space it differently, so the leading words are tried
    on their own too.
    """
    value = re.sub(r"\s+", " ", (value or "")).strip(" :|")
    words = value.split(" ")
    out = [value]
    for n in range(len(words) - 1, 0, -1):
        part = " ".join(words[:n])
        if len(part) >= 3:
            out.append(part)
    if value.startswith("$"):
        out.append(value[1:].split(" ")[0])
    seen, uniq = set(), []
    for n in out:
        if n and n.lower() not in seen:
            seen.add(n.lower())
            uniq.append(n)
    return uniq


def _centre(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _nearest(boxes, anchors):
    """The box closest to any label, or the first when there is no label.

    A value usually sits right of or below its label, so a box above or to
    the left is made to look further away.
    """
    if not anchors:
        return boxes[0]

    def cost(box):
        bx, by = _centre(box)
        best = None
        for a in anchors:
            ax, ay = _centre(a)
            d = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
            if by < a[1] - 0.005 or bx < a[0] - 0.005:
                d *= 3
            best = d if best is None else min(best, d)
        return best
    return min(boxes, key=cost)


def _label_needles(row):
    label = (row.get("label") or "").strip()
    keys = [label] if label else []
    keys += field_search.labels_for(row.get("field", ""))
    return [k for k in keys if len(k) >= 3]


# ----------------------------------------------------------------- locating

def _locate_in_text_layer(pg, row):
    for needle in _value_needles(row.get("value")):
        boxes = pg.find(needle)
        if not boxes:
            continue
        anchors = []
        for label in _label_needles(row):
            anchors = pg.find(label, limit=10)
            if anchors:
                break
        return _nearest(boxes, anchors)
    return None


def _locate_in_blocks(blocks, row):
    if not blocks:
        return None
    labels = [_plain(k) for k in _label_needles(row)]
    for needle in _value_needles(row.get("value")):
        n = _plain(needle)
        hits = [b for b in blocks if n and n in _plain(b["text"])]
        if not hits:
            continue
        with_label = [b for b in hits
                      if any(l and l in _plain(b["text"]) for l in labels)]
        return (with_label or hits)[0]["box"]
    return None


def _cells(table_html):
    """[[cell text, ...], ...] for each row of one table."""
    rows = []
    for tr in re.findall(r"<tr\b.*?</tr>", table_html, flags=re.S | re.I):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<t[dh]\b.*?</t[dh]>", tr, flags=re.S | re.I)]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    return rows


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _locate_table_in_text_layer(pg, table_html):
    """Outline a table by finding its first and last rows on the page.

    Only used when the OCR left no layout (older runs, or a reader that does
    not report one). Each corner cell can be printed elsewhere on the page
    too, so the pair is chosen that sits closest together, top above bottom.
    """
    rows = _cells(table_html)
    if not rows:
        return None
    top = [b for c in rows[0][:1] for b in pg.find(c[:60], 20, exact=True)]
    bottom = [b for c in rows[-1][-1:] for b in pg.find(c[:60], 20, exact=True)]
    # Rows span a table's height, so the bottom corner is the nearest match
    # BELOW the top one and right of it. Height is what matters; a table's
    # width says little, since its last cell is often at the far edge.
    best = None
    for t in top:
        for b in bottom:
            if b[3] < t[1] or b[2] < t[0]:
                continue
            d = (b[3] - t[1]) + 0.1 * abs(b[2] - t[2])
            if best is None or d < best[0]:
                best = (d, t, b)
    if best is None:
        return None
    _, t, b = best
    parts = [t, b]
    # Widen to the row edges: the last cell of the top row and the first cell
    # of the bottom row, wherever they fall between the two corners.
    for text in (rows[0][-1], rows[-1][0]):
        hits = [h for h in pg.find(text[:60], 20, exact=True)
                if t[1] - 0.01 <= h[1] <= b[3] + 0.01]
        if hits:
            parts.append(min(hits, key=lambda h: abs(h[1] - t[1]) + abs(h[1] - b[1])))
    box = _union(parts)
    pad = 0.004
    return [round(max(box[0] - pad, 0), 5), round(max(box[1] - pad, 0), 5),
            round(min(box[2] + pad, 1), 5), round(min(box[3] + pad, 1), 5)]


def _tables_by_page(text):
    """{page number: [table html, ...]} in reading order."""
    out = {}
    for label, content in save_output.split_pages(text or ""):
        m = re.search(r"(\d+)\s*$", label)
        page = int(m.group(1)) if m else 1
        out[page] = re.findall(r"<table\b.*?</table>", content,
                               flags=re.S | re.I)
    return out


def table_fingerprint(table_html):
    return hashlib.sha1(_plain(table_html).encode("utf-8")).hexdigest()[:16]


def field_key(row):
    return f"f:{row.get('page') or 1}:{row.get('field', '')}"


def table_key(page, k):
    return f"t:{page}:{k}"


def is_image(path):
    return os.path.splitext(path or "")[1].lower() in IMAGE_EXT


def build(source, rows, text, layout):
    """Everything the review screen draws: pages, fields, tables.

    `source` may be missing (the document was not kept); the lists still come
    back, just without boxes, so the values can be checked by eye.
    """
    layout_by_page = {p["page"]: p["blocks"] for p in (layout or [])}
    colours, order = {}, []
    for r in rows:
        f = r.get("field", "")
        if f not in colours:
            colours[f] = PALETTE[len(colours) % len(PALETTE)]
            order.append(f)

    # pdfium is not thread-safe; everything below that touches it holds the
    # shared lock, and closes what it opened before letting go.
    with pdf_pages.LOCK:
        return _build_locked(source, rows, text, layout_by_page, colours,
                             order)


def _build_locked(source, rows, text, layout_by_page, colours, order):
    doc = None
    pages = []
    if source and os.path.exists(source):
        if is_image(source):
            pages = [1]
        else:
            doc = pdfium.PdfDocument(source)
            pages = list(range(1, len(doc) + 1))

    fields, tables = [], []
    cache = {}
    try:

        def pdf_page(n):
            if doc is None or not 1 <= n <= len(doc):
                return None
            if n not in cache:
                cache[n] = _PdfPage(doc[n - 1])
            return cache[n]

        for r in rows:
            page = int(r.get("page") or 1)
            item = {"key": field_key(r), "page": page, "field": r.get("field", ""),
                    "value": r.get("value") or "", "verdict": r.get("verdict", ""),
                    "where": r.get("where", ""), "evidence": r.get("evidence", ""),
                    "color": colours.get(r.get("field", "")), "box": None,
                    "precise": False}
            if item["value"]:
                pg = pdf_page(page)
                if pg is not None and pg.has_text:
                    item["box"] = _locate_in_text_layer(pg, r)
                    item["precise"] = item["box"] is not None
                if item["box"] is None:
                    item["box"] = _locate_in_blocks(layout_by_page.get(page), r)
            fields.append(item)

        for page, found in sorted(_tables_by_page(text).items()):
            boxes = [b["box"] for b in layout_by_page.get(page) or []
                     if b["label"] == "table"]
            for k, t in enumerate(found, 1):
                box = boxes[k - 1] if k - 1 < len(boxes) else None
                if box is None:
                    pg = pdf_page(page)
                    if pg is not None and pg.has_text:
                        box = _locate_table_in_text_layer(pg, t)
                tables.append({
                    "key": table_key(page, k), "page": page, "k": k,
                    "sheet": f"page {page}" if k == 1 else f"page {page}_{k}",
                    "html": t, "fingerprint": table_fingerprint(t),
                    "box": box,
                })
    finally:
        for pg in cache.values():
            pg.text.close()
            pg.page.close()
        if doc is not None:
            doc.close()

    return {"pages": pages, "fields": fields, "tables": tables,
            "legend": [{"field": f, "color": colours[f]} for f in order],
            "table_color": TABLE_COLOR}


# ---------------------------------------------------------------- approval

def approval(review, approved):
    """Which keys are approved, given the values as they stand now.

    An approval is stored with the value it approved. If Extract again turns
    up a different value, the old tick no longer counts.
    """
    approved = approved or {}
    done = set()
    need_f = need_t = done_f = done_t = 0
    for f in review["fields"]:
        if not f["value"]:
            continue
        need_f += 1
        if approved.get(f["key"]) == f["value"]:
            done.add(f["key"])
            done_f += 1
    for t in review["tables"]:
        need_t += 1
        if approved.get(t["key"]) == t["fingerprint"]:
            done.add(t["key"])
            done_t += 1
    return {"approved": sorted(done),
            "fields": {"need": need_f, "done": done_f,
                       "complete": done_f == need_f},
            "tables": {"need": need_t, "done": done_t,
                       "complete": done_t == need_t}}


def fingerprint_of(review, key):
    """What an approval of this key is pinned to, or None if no such key."""
    for f in review["fields"]:
        if f["key"] == key:
            return f["value"] or None
    for t in review["tables"]:
        if t["key"] == key:
            return t["fingerprint"]
    return None


# ------------------------------------------------------------------ images

def render(source, page_no, dpi=110):
    """One page as a PIL image, the whole page, rotated as pdf_pages does."""
    if is_image(source):
        if page_no != 1:
            raise IndexError(page_no)
        return Image.open(source).convert("RGB")
    with pdf_pages.LOCK:
        doc = pdfium.PdfDocument(source)
        try:
            if not 1 <= page_no <= len(doc):
                raise IndexError(page_no)
            page = doc[page_no - 1]
            try:
                img, _ = pdf_pages.render_page(page, dpi=dpi, crop=False)
            finally:
                page.close()
            return img.convert("RGB")
        finally:
            doc.close()


def _rgb(hex_colour, alpha):
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def _font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def highlighted_pdf(source, review, dpi=150):
    """The source with every box drawn on it and labelled, as PDF bytes."""
    font = _font(max(11, dpi // 11))
    pages = []
    for n in review["pages"]:
        img = render(source, n, dpi).convert("RGBA")
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(over)
        W, H = img.size

        def px(box):
            return [box[0] * W, box[1] * H, box[2] * W, box[3] * H]

        for t in review["tables"]:
            if t["page"] == n and t["box"]:
                draw.rectangle(px(t["box"]), outline=_rgb(TABLE_COLOR, 255),
                               width=max(3, dpi // 40))
                x0, y0 = px(t["box"])[:2]
                tag = f"Table: {t['sheet']}"
                tw = draw.textlength(tag, font=font)
                draw.rectangle([x0, y0 - font.size - 6, x0 + tw + 10, y0],
                               fill=_rgb(TABLE_COLOR, 255))
                draw.text((x0 + 5, y0 - font.size - 4), tag, font=font,
                          fill=(255, 255, 255, 255))
        # Two fields can be the same printed value (Property name and Address
        # both read the service address). One box, their labels side by side.
        groups = {}
        for f in review["fields"]:
            if f["page"] == n and f["box"]:
                groups.setdefault(tuple(f["box"]), []).append(f)
        for key, members in groups.items():
            box = px(list(key))
            pad = 3
            box = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad]
            first = members[0]["color"]
            draw.rectangle(box, fill=_rgb(first, 60),
                           outline=_rgb(first, 255), width=2)
            x = box[0]
            ty = max(box[1] - font.size - 6, 0)
            for f in members:
                tag = f["field"].replace("_", " ").capitalize()
                tw = draw.textlength(tag, font=font)
                draw.rectangle([x, ty, x + tw + 10, ty + font.size + 6],
                               fill=_rgb(f["color"], 255))
                draw.text((x + 5, ty + 2), tag, font=font,
                          fill=(255, 255, 255, 255))
                x += tw + 12
        pages.append(Image.alpha_composite(img, over).convert("RGB"))
    if not pages:
        raise ValueError("no pages to draw")
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:],
                  resolution=dpi)
    return buf.getvalue()
