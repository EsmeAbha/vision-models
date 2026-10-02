"""Find the manufactured-housing rating guide in an appraisal, and take only it.

An appraisal runs to a few hundred pages and holds dozens of tables. Exactly
one of them is the rating guide -- the Class A / B / C / Unratable grid that
scores density, age, amenities and the rest. This finds that one and leaves
the rest alone.

It deliberately does not look for the heading. Firms re-type it, and the
sample this was built from reads "Manufacture Housing Communites Rating
Guide" -- two typos in six words. A title is the first thing to drift between
files. What does not drift is the shape: four class columns across the top
and a known set of row labels down the side. So every candidate table is
scored on how much of that shape it carries, and the best-scoring table wins
-- but only if it clears a floor, so an appraisal that simply does not contain
the guide says "not found" instead of handing back the nearest unrelated grid.
"""
from __future__ import annotations

import os
import re

import pdfplumber

CLASS_COLUMNS = ["Class A", "Class B", "Class C", "Unratable"]

# The rows of the guide, in the order the sample prints them. Matching is on
# shape, not on this exact wording -- see _row_label.
CORE_ROWS = ["Density", "Age", "Amenities", "Quality/Layout", "Roads",
             "Utilities", "Parking", "Homes (Quality)", "Homes (Age)",
             "Homes (Mix)"]
STAR_ROWS = ["Star Rating (Non-Woodall)", "Star Rating (Woodall)"]
ALL_ROWS = CORE_ROWS + STAR_ROWS

# Two gates, because the blended score alone is too forgiving. A table that
# carries all four class headers starts at 0.30, so three stray row labels
# would clear a score floor on their own -- and an appraisal's amenity
# comparison really does have rows called Age, Parking and Utilities under
# Class A/B/C headings. Requiring half the guide's rows outright is the gate
# that such a table cannot pass by accident.
MIN_SCORE = 0.45
MIN_CORE_ROWS = 5

# A table carrying essentially the whole guide is the guide; there is no
# reason to read the remaining two hundred pages looking for a better one.
CERTAIN = 0.92

# Which route to believe when two of them read the same table equally well.
# The character rebuild is preferred because it decides where a row ends from
# the row labels, while pdfplumber's ruled reader decides from the ruling --
# and on five of these appraisals the ruling sits a point or so above the last
# line of a wrapped cell, which drops the "Dirt)" of "(Some Gravel & Dirt)"
# into the row beneath. Both score a perfect 1.0 there; only one is right.
ROUTE_RANK = {"chars": 2, "lines": 1, "text": 0}


def _key(s):
    """Fold a cell to letters and digits.

    "Homes (Quality)", "Homes(Quality)" and "HOMES  (QUALITY)" are the same
    label printed by three different firms; they must compare equal. Dropping
    everything that is not alphanumeric is what makes that true, and it also
    disposes of the stray footnote markers and non-breaking spaces that come
    out of a PDF text layer.
    """
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


_ROW_KEYS = [(_key(lbl), lbl) for lbl in ALL_ROWS]
_HEADER_KEYS = {_key(c): c for c in CLASS_COLUMNS}


def _clean(s):
    """Cell text as a human would read it: one line, single spaces."""
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip()


def _cell(row, i):
    return _clean(row[i]) if 0 <= i < len(row) else ""


def _row_label(cell):
    """The canonical row this cell names, or None.

    Short keys match exactly and longer ones match on a prefix. "Age" has to
    be exact or it would also claim "Average Rent" and "Acreage"; "Homes
    (Quality)" is distinctive enough that a trailing footnote should not stop
    it matching.
    """
    n = _key(cell)
    if not n:
        return None
    for key, label in _ROW_KEYS:
        if n == key or (len(key) > 5 and n.startswith(key)):
            return label
    return None


def _header_map(row):
    """Map each class column name to its index, for a row that looks like the header."""
    found = {}
    for i in range(len(row)):
        name = _HEADER_KEYS.get(_key(_cell(row, i)))
        if name and name not in found:
            found[name] = i
    return found


def _score(table):
    """How much this table looks like the rating guide, and what it is made of.

    Returns (score, detail). The weights say what actually identifies the
    guide: the row labels carry it, the class header confirms it, and the two
    star-rating rows are a bonus because they sit in a sub-band that some PDFs
    break out into a table of their own.
    """
    best_header, header_row = {}, -1
    for i, row in enumerate(table[:4]):
        got = _header_map(row)
        if len(got) > len(best_header):
            best_header, header_row = got, i

    labels, label_col = {}, 0
    for col in (0, 1):
        hits = {}
        for i, row in enumerate(table):
            lbl = _row_label(_cell(row, col))
            if lbl and lbl not in hits:
                hits[lbl] = i
        if len(hits) > len(labels):
            labels, label_col = hits, col

    core = sum(1 for l in labels if l in CORE_ROWS)
    star = sum(1 for l in labels if l in STAR_ROWS)
    score = (0.55 * (core / len(CORE_ROWS))
             + 0.15 * (star / len(STAR_ROWS))
             + 0.30 * (len(best_header) / len(CLASS_COLUMNS)))
    return score, {"header": best_header, "header_row": header_row,
                   "labels": labels, "label_col": label_col,
                   "core": core, "star": star}


def _is_band(row):
    """A merged full-width row, like the title or "Comparison to Star Rating".

    pdfplumber renders a merged cell as text in one column and blanks beside
    it, so exactly one non-empty cell in a multi-column row means a band.
    """
    filled = [i for i in range(len(row)) if _cell(row, i)]
    return len(filled) == 1 and len(row) > 1


def _shape(table, detail):
    """Turn a scored table into the rows we hand back.

    Every row of the matched table is kept, including ones this module has
    never heard of -- a firm that adds a "Location" row should not have it
    silently dropped. They are flagged instead, so a reviewer can see what was
    extra.
    """
    header = detail["header"]
    label_col = detail["label_col"]
    start = detail["header_row"] + 1 if detail["header_row"] >= 0 else 0

    if header:
        cols = [c for c in CLASS_COLUMNS if c in header]
        idx = [header[c] for c in cols]
    else:
        # No header row survived extraction. Fall back to the four columns
        # after the label, which is the layout every sample uses.
        cols = list(CLASS_COLUMNS)
        idx = [label_col + 1 + i for i in range(4)]

    out = []
    for row in table[start:]:
        if not any(_cell(row, i) for i in range(len(row))):
            continue
        if _is_band(row):
            text = next(_cell(row, i) for i in range(len(row)) if _cell(row, i))
            # A one-cell row that names a real label is a row whose values
            # failed to extract, not a band; let it fall through below.
            if not (_key(text) in _HEADER_KEYS or _row_label(text)):
                out.append({"category": text, "section": True,
                            "canonical": False, "printed": text,
                            "values": [""] * len(cols)})
                continue
        name = _cell(row, label_col)
        if not name:
            continue
        canon = _row_label(name)
        out.append({"category": canon or name, "section": False,
                    "canonical": canon is not None, "printed": name,
                    "values": [_cell(row, i) for i in idx]})
    return cols, out


def _title_near(page_text, table_rows):
    """The heading as printed, for evidence only -- never for matching."""
    for row in table_rows[:2]:
        if _is_band(row):
            text = next((_cell(row, i) for i in range(len(row))
                         if _cell(row, i)), "")
            if "rating" in text.lower():
                return text
    for line in (page_text or "").splitlines():
        if "rating guide" in line.lower():
            return _clean(line)
    return ""


def _mentions_guide(text):
    """Is this page worth running table extraction on?

    Table extraction is the expensive part and an appraisal is mostly prose. A
    page that does not even name the class columns cannot hold the guide, so
    it is skipped unread -- this is what keeps a 300-page file quick.
    """
    n = _key(text)
    return ("unratable" in n or "ratingguide" in n
            or ("classa" in n and "classc" in n))


# ------------------------------------------------- rebuilding from the words
#
# Real appraisals turned out not to be readable by either strategy above. The
# guide is drawn without ruling that pdfplumber recognises as a table, so the
# lines strategy finds nothing at all on the page; the text strategy then
# treats the whole page as one grid and smears the prose above the table
# across the same columns, which breaks the header row apart and leaves the
# guide scoring just under the floor. Fifteen of fifty-three files failed that
# way, every one of them with zero header columns matched.
#
# What those pages do have is clean word positions. The header labels give the
# column centres, the row labels all share a left edge, and a wrapped line is
# recognisable because one side of it is empty. That is enough to rebuild the
# table without depending on borders at all.

LINE_TOL = 3.0          # characters within this many points share a line
RUN_GAP = 20.0          # a wider gap than this separates two cells


def _lines_of(chars, tol=LINE_TOL):
    """Group characters into visual lines, each sorted left to right."""
    groups = []
    for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
        if groups and abs(c["top"] - groups[-1][0]) <= tol:
            groups[-1][1].append(c)
        else:
            groups.append([c["top"], [c]])
    return [(top, sorted(cs, key=lambda c: c["x0"])) for top, cs in groups]


def _chars_text(chars):
    """Characters back into readable text.

    A space is inserted wherever the gap is wide enough to be one, because a
    PDF is free to position words apart rather than emit a space character --
    and these files do both.
    """
    out, prev = [], None
    for c in sorted(chars, key=lambda c: c["x0"]):
        if prev is not None:
            gap = c["x0"] - prev["x1"]
            if gap > max(0.8, 0.22 * (c.get("size") or 8)):
                out.append(" ")
        out.append(c["text"])
        prev = c
    return _clean("".join(out))


def _find_label(chars, key):
    """Where a label sits on a line, as (x0, x1), or None.

    The line is folded to letters and digits before searching, so it does not
    matter whether the characters came out as "Class A", "Class  A" or
    "ClassA" -- which is the difference between one of these files and the
    next. Each folded character remembers which glyph it came from, so the
    match maps straight back to a position on the page.
    """
    folded, source = [], []
    for c in chars:
        for ch in re.sub(r"[^a-z0-9]+", "", c["text"].lower()):
            folded.append(ch)
            source.append(c)
    at = "".join(folded).find(key)
    if at < 0:
        return None
    return source[at]["x0"], source[at + len(key) - 1]["x1"]


def _column_cuts(line_chars):
    """The x boundaries of the four class columns, from the header labels.

    The headers are centred in their cells, so their own extents are not the
    cell edges -- but the midpoints between neighbouring centres are, and the
    outer two edges follow from the column width. Three of the four headers
    are enough, which leaves room for one to be split oddly.
    """
    centres = []
    for name in CLASS_COLUMNS:
        at = _find_label(line_chars, _key(name))
        if at:
            centres.append((at[0] + at[1]) / 2)
    if len(centres) < 3:
        return None
    centres.sort()
    gaps = [b - a for a, b in zip(centres, centres[1:])]
    width = sorted(gaps)[len(gaps) // 2]
    if width <= 1:
        return None
    cuts = [centres[0] - width / 2]
    cuts += [(a + b) / 2 for a, b in zip(centres, centres[1:])]
    cuts.append(centres[-1] + width / 2)
    while len(cuts) < len(CLASS_COLUMNS) + 1:
        cuts.append(cuts[-1] + width)
    return cuts


def _is_band_line(line_chars, cuts):
    """A merged full-width row, like "Comparison to Star Rating".

    Its text is one unbroken run that crosses column boundaries. A data row
    crosses them too, but with a wide gap at every boundary, because its cells
    are separate pieces of text.
    """
    if len(line_chars) < 2:
        return False
    gaps = [b["x0"] - a["x1"] for a, b in zip(line_chars, line_chars[1:])]
    if gaps and max(gaps) > RUN_GAP:
        return False
    left, right = line_chars[0]["x0"], line_chars[-1]["x1"]
    return sum(1 for c in cuts[1:-1] if left < c < right) >= 2


def _table_from_chars(page):
    """Rebuild the guide from character positions. -> table rows, or None.

    Returns the same list-of-lists shape extract_tables gives, so the scoring
    and shaping below do not care which route produced it.

    This works from page.chars rather than extract_words because the words
    cannot be trusted here. Some of these appraisals carry a text matrix with
    a rounding-error skew in it -- 5e-08 off true -- which is enough for
    pdfplumber to mark every character in the table as not upright and read
    the whole thing as vertical text, one letter to a line. The characters
    themselves are exact; only the orientation verdict is wrong.

    Rows are anchored on the lines that carry a row label, and a row owns
    every line between the midpoints to its neighbours. That ordering matters:
    the label is centred in its row while a wrapped value starts at the top of
    it, so the first line of "Medium (10 sites/acre or less)" is printed
    ABOVE the word "Density". Treating each line as its own row, or merging a
    label-less line into the row above, puts those values one row out.
    """
    chars = [c for c in page.chars if (c.get("text") or "").strip()]
    if not chars:
        return None

    lines = _lines_of(chars)
    header_top, cuts = None, None
    for top, cs in lines:
        got = _column_cuts(cs)
        if got:
            header_top, cuts = top, got
            break
    if not cuts:
        return None

    def mid(c):
        return (c["x0"] + c["x1"]) / 2

    bands, body = {}, []
    for top, cs in lines:
        if top <= header_top:
            continue
        inside = [c for c in cs if mid(c) < cuts[-1]]
        if not inside:
            continue
        if _is_band_line(inside, cuts):
            bands[top] = _chars_text(inside)
        else:
            body.append((top, inside))

    marked = [(top, cs, _chars_text([c for c in cs if mid(c) < cuts[0]]))
              for top, cs in body]
    anchored = [(top, text) for top, _cs, text in marked if text]
    if not anchored:
        return None

    steps = [b - a for (a, _), (b, _) in zip(anchored, anchored[1:])]
    pitch = sorted(steps)[len(steps) // 2] if steps else 25.0

    # A label that wraps puts two label lines in one row -- "Star Rating
    # (Non-" above "Woodall)" -- and they sit far closer together than two
    # real rows do.
    anchors = []
    for top, text in anchored:
        if anchors and top - anchors[-1][1] < 0.6 * pitch:
            anchors[-1][1] = top
            anchors[-1][2] = _clean(f"{anchors[-1][2]} {text}")
        else:
            anchors.append([top, top, text])

    edges = [header_top]
    edges += [(a[1] + b[0]) / 2 for a, b in zip(anchors, anchors[1:])]
    edges.append(anchors[-1][1] + 0.7 * pitch)

    buckets = [[] for _ in anchors]
    for top, cs, _text in marked:
        for i, (lo, hi) in enumerate(zip(edges, edges[1:])):
            if lo < top <= hi:
                buckets[i].append((top, cs))
                break

    built = []
    for (first, _last, label), got in zip(anchors, buckets):
        cells = []
        for a, b in zip(cuts, cuts[1:]):
            parts = [_chars_text([c for c in cs if a <= mid(c) < b])
                     for _top, cs in sorted(got)]
            cells.append(_clean(" ".join(x for x in parts if x)))
        built.append((first, [label] + cells))

    for top, text in bands.items():
        if top <= edges[-1]:
            built.append((top, [text] + [""] * len(CLASS_COLUMNS)))

    rows = [["Category"] + list(CLASS_COLUMNS)]
    rows += [row for _top, row in sorted(built, key=lambda x: x[0])]

    # Everything past the last row the guide defines is the prose that follows
    # the table, so it is cut rather than carried along.
    last = max((i for i, r in enumerate(rows) if _row_label(r[0])), default=0)
    rows = rows[:last + 1]
    return rows if len(rows) > 1 else None


def _tables_on(page):
    """Every table on the page, with the route that found it.

    The character rebuild always runs alongside the ruled read rather than
    only when that comes up empty: a page can yield a ruled table that is not
    the guide while the guide beside it has no ruling at all, and stopping at
    the first route that returned something would miss it.

    The whole-page text strategy stays last and is only reached when nothing
    else cleared the floor. It finds a table on almost any page, and what it
    finds is usually the prose.
    """
    out = []
    try:
        found = page.extract_tables()
    except Exception:
        found = []
    out.extend((t, "lines") for t in found if t and len(t) > 1)

    try:
        rebuilt = _table_from_chars(page)
    except Exception:
        rebuilt = None
    if rebuilt:
        out.append((rebuilt, "chars"))

    if any(_score(t)[0] >= MIN_SCORE for t, _ in out):
        return out

    try:
        found = page.extract_tables({"vertical_strategy": "text",
                                     "horizontal_strategy": "text"})
    except Exception:
        found = []
    out.extend((t, "text") for t in found if t and len(t) > 1)
    return out


def _merge_continuation(best):
    """Re-attach a star-rating band that extraction split into its own table.

    The sub-band under "Comparison to Star Rating" sits inside the same border
    in the sample, but a PDF whose ruling breaks there yields two tables. The
    main one is then missing its last two rows, which is a silent loss rather
    than a visible failure -- so look for them next door.
    """
    if best["detail"]["star"] >= len(STAR_ROWS):
        return best
    for table, _strategy in best["rest"]:
        _score_, detail = _score(table)
        if detail["star"] and not detail["core"]:
            best["table"] = best["table"] + table
            best["score"], best["detail"] = _score(best["table"])
            return best
    return best


def extract_from_pdf(path, display=None):
    """Find the guide in one appraisal. Always returns a result dict."""
    name = display or os.path.basename(path)
    result = {"file": name, "status": "not_found", "page": None, "score": 0.0,
              "title": "", "columns": [], "rows": [], "note": "",
              "pages": 0, "strategy": ""}
    try:
        with pdfplumber.open(path) as pdf:
            result["pages"] = len(pdf.pages)
            best, with_text, looked = None, 0, 0

            # One pass over the document, reading tables only on the pages
            # that mention the class columns, and stopping as soon as a table
            # turns up that plainly is the guide.
            for i, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                if text.strip():
                    with_text += 1
                if not _mentions_guide(text):
                    continue
                looked += 1
                found = _tables_on(page)
                for n, (table, strategy) in enumerate(found):
                    score, detail = _score(table)
                    rank = (score, ROUTE_RANK.get(strategy, 0))
                    if best is None or rank > (best["score"],
                                               ROUTE_RANK.get(best["strategy"], 0)):
                        best = {"score": score, "detail": detail,
                                "table": table, "page": i,
                                "strategy": strategy,
                                "rest": found[:n] + found[n + 1:]}
                if best and best["score"] >= CERTAIN:
                    break

            if with_text == 0:
                result["status"] = "scanned"
                result["note"] = ("no text layer -- this is a scan, and OCR is "
                                  "required before the table can be read")
                return result
            if not looked:
                result["note"] = "no page mentions the class columns"
                return result
            if best is None:
                result["note"] = "the candidate pages held no table"
                return result

            core = best["detail"]["core"]
            if best["score"] < MIN_SCORE or core < MIN_CORE_ROWS:
                result["score"] = round(best["score"], 3)
                result["page"] = best["page"] + 1
                result["note"] = (
                    f"closest table carried {core} of the guide's "
                    f"{len(CORE_ROWS)} rows and scored {best['score']:.2f} "
                    f"-- short of {MIN_CORE_ROWS} rows / {MIN_SCORE:.2f}, "
                    f"so it is not the guide")
                return result

            best = _merge_continuation(best)
            page = pdf.pages[best["page"]]
            cols, rows = _shape(best["table"], best["detail"])

            result.update(
                status="found", page=best["page"] + 1,
                score=round(best["score"], 3), columns=cols, rows=rows,
                strategy=best["strategy"],
                title=_title_near(page.extract_text() or "", best["table"]),
            )
            missing = [r for r in ALL_ROWS
                       if r not in {x["category"] for x in rows}]
            if missing:
                result["note"] = "rows not found: " + ", ".join(missing)
            return result
    except Exception as e:
        result["status"] = "error"
        result["note"] = f"{type(e).__name__}: {e}"
        return result
