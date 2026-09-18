"""One token format for every document, however the words were obtained.

    {page, text, x0, x1, top, bottom, source: "text"|"ocr", pass: int}

Everything downstream -- row grouping, column binning, parsing, validation --
works on this and nothing else. A born-digital PDF and a scanned one that went
through OCR become the same shape here, so a parser written for one works on
the other without knowing which it got.

Nothing in this module reads a value for meaning. It reports where the marks
are on the page; deciding what they mean is the parser's job, and checking it
is the validator's.
"""
from __future__ import annotations

import os
import re


def _rotated_words(chars, row_tol=2.0, space=1.2):
    """Assemble words from characters drawn on their side.

    A page with /Rotate 90 or 270 stores its text unrotated, with a rotation
    in each character's matrix: the line advances along y, not x. pdfplumber's
    word grouping is horizontal, so on such a page it joins characters that
    are on different lines and splits the ones that are on the same line. The
    result is not empty -- which is the danger. It is a plausible-looking set
    of tokens in the wrong order, and nothing downstream can tell.

    So the axes are swapped back before words are assembled. Which way the
    text runs is read from the matrix rather than assumed: b>0 means the
    advance is +y and the display order is the reverse of the stored one.
    """
    if not chars:
        return []
    b = sum(c["matrix"][1] for c in chars if "matrix" in c)
    flip = b > 0

    pts = []
    for c in chars:
        if flip:
            x0, x1 = -float(c["bottom"]), -float(c["top"])
        else:
            x0, x1 = float(c["top"]), float(c["bottom"])
        pts.append({"t": c["text"], "x0": x0, "x1": x1,
                    "top": float(c["x0"]), "bottom": float(c["x1"])})

    pts.sort(key=lambda c: (c["top"], c["x0"]))
    lines, cur, ref = [], [], None
    for c in pts:
        if ref is not None and abs(c["top"] - ref) > row_tol:
            lines.append(cur)
            cur = []
            ref = None
        cur.append(c)
        if ref is None:
            ref = c["top"]
    if cur:
        lines.append(cur)

    words = []
    for ln in lines:
        ln.sort(key=lambda c: c["x0"])
        buf = []
        for c in ln:
            if buf and c["x0"] - buf[-1]["x1"] > space:
                words.append(buf)
                buf = []
            buf.append(c)
        if buf:
            words.append(buf)

    out = []
    for w in words:
        txt = "".join(c["t"] for c in w).strip()
        if not txt:
            continue
        out.append({
            "text": txt,
            "x0": w[0]["x0"], "x1": w[-1]["x1"],
            "top": min(c["top"] for c in w),
            "bottom": max(c["bottom"] for c in w),
        })
    return out


def from_pdf(path, pages=None):
    """Tokens from a PDF's own text layer, via pdfplumber.

    Returns [] when the file has no text layer -- that is a fact about the
    file, not an error, and the caller decides whether to OCR it.
    """
    import pdfplumber

    out = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            if pages and pno not in pages:
                continue
            try:
                chars = page.chars
                sideways = [c for c in chars if not c.get("upright", True)]
                if chars and len(sideways) >= 0.6 * len(chars):
                    words = _rotated_words(sideways)
                else:
                    words = page.extract_words(use_text_flow=False,
                                               keep_blank_chars=False)
            except Exception:
                continue
            for w in words:
                out.append({
                    "page": pno,
                    "text": w["text"],
                    "x0": round(float(w["x0"]), 1),
                    "x1": round(float(w["x1"]), 1),
                    "top": round(float(w["top"]), 1),
                    "bottom": round(float(w["bottom"]), 1),
                    "source": "text",
                    "pass": 0,
                })
    return out


def from_ocr_json(words, page=1, pass_no=1, scale=1.0):
    """Tokens from the OCR word list ({t,x,y,w,h}) used by the OCR scripts.

    `scale` converts render pixels back to PDF points, so OCR coordinates and
    text-layer coordinates are comparable on the same page.
    """
    out = []
    for w in words:
        x, y = float(w["x"]) / scale, float(w["y"]) / scale
        ww, hh = float(w["w"]) / scale, float(w["h"]) / scale
        out.append({
            "page": page,
            "text": w["t"],
            "x0": round(x, 1),
            "x1": round(x + ww, 1),
            "top": round(y, 1),
            "bottom": round(y + hh, 1),
            "source": "ocr",
            "pass": pass_no,
        })
    return out


_VOWEL = re.compile(r"[aeiouyAEIOUY]")
_PUNCT_EDGE = re.compile(r"^[^\w$]+|[^\w%]+$")


def _wordlike(tok):
    """Is this token a word, a figure or a code -- or is it glyph soup?

    Mojibake is built out of exactly the characters a business document is
    full of: commas, colons, hyphens, quotes. Counting "legible characters"
    therefore scores ",:!IT-" at 100% and passes a ruined text layer. What
    broken text does NOT have is word structure, so that is what is measured.
    """
    t = _PUNCT_EDGE.sub("", tok.strip())
    if not t:
        return False
    core = t.replace(",", "").replace(".", "").replace("/", "").replace("-", "")
    core = core.replace("$", "").replace("%", "").replace(":", "")
    if not core:
        return False
    if core.isdigit():
        return True                      # a figure, a date, a year
    if any(c.isdigit() for c in core) and core.isalnum():
        return True                      # unit code: A101, 3BR, 225bush
    letters = [c for c in core if c.isalpha()]
    if len(letters) < 2:
        return False                     # stray single glyph
    if len(letters) < len(core) * 0.6:
        return False                     # letters drowned in punctuation
    return True                          # word, or abbreviation (LLC, SF, TI)


def has_text_layer(path, sample_pages=6, min_chars=200, min_wordlike=0.90):
    """(bool, note). False when the words are pixels, or are mojibake.

    Some PDFs carry a text layer that renders perfectly and extracts as
    nonsense: the embedded font subsets have no usable ToUnicode map, so the
    glyphs are right and the character codes behind them are not. A lease
    report here extracts as ",:!IT-" and ":;:" while looking clean on screen.

    That case is more dangerous than a plain scan. A scan yields no text and
    is obviously unreadable; this yields plenty of text, passes a length
    check, and then feeds confident garbage into every stage downstream. So
    the text is tested for word structure rather than character class -- on
    real reports 97-99% of tokens are words, figures or codes, against 65%
    for the broken one.
    """
    try:
        toks = from_pdf(path, pages=set(range(1, sample_pages + 1)))
    except Exception as e:
        return False, f"unreadable: {type(e).__name__}: {e}"
    if not toks:
        return False, "no text layer - the words are pixels"

    joined = " ".join(t["text"] for t in toks)
    if len(joined) < min_chars:
        return False, f"only {len(joined)} characters of text - likely scanned"

    # Single glyphs are judged on neither side. A dash standing for an empty
    # cell and a rule of "=" are layout marks, not text, and they are common
    # enough in a clean report to drag it down: counting them put a good
    # income statement at 92% against a ruined lease report at 77%, which is
    # too little daylight for a threshold. Ignoring them separates the two at
    # 99% and 79%.
    body = [t for t in toks if len(t["text"].strip()) > 1]
    if len(body) < 20:
        return False, f"only {len(body)} multi-character tokens - likely scanned"
    good = sum(1 for t in body if _wordlike(t["text"]))
    ratio = good / len(body)
    if ratio < min_wordlike:
        return False, (f"text layer is mojibake (only {ratio:.0%} of tokens are "
                       f"words, figures or codes) - the embedded fonts have no "
                       f"usable character map, so this needs OCR")
    return True, f"{len(toks)} tokens, {ratio:.0%} wordlike"


# --------------------------------------------------------------- masked dumps
_DIGIT = re.compile(r"\d")


def mask(text):
    """Digits to #, so a layout can be studied without exposing figures."""
    return _DIGIT.sub("#", text)


def dump_page(tokens, page, masked=True, max_rows=120, tol=2.0):
    """A readable coordinate dump of one page, for a person or a model.

    Masked by default. The point of the dump is the shape of the page, and a
    layout is just as legible with the digits replaced -- which means it can be
    shown to a model without handing over the client's numbers.
    """
    rows = group_rows([t for t in tokens if t["page"] == page], tol=tol)
    lines = []
    for r in rows[:max_rows]:
        parts = []
        for t in r:
            s = mask(t["text"]) if masked else t["text"]
            parts.append(f"{s}@{t['x0']:.0f}-{t['x1']:.0f}")
        lines.append(f"y={r[0]['top']:7.1f}  " + "  ".join(parts))
    return "\n".join(lines)


def group_rows(tokens, tol=2.0):
    """Tokens grouped into visual rows by vertical tolerance.

    By tolerance, never by rounding `top` into buckets: two words on the same
    printed line routinely differ by a fraction of a point, and a bucket edge
    falling between them splits the row in half.
    """
    rows, cur = [], []
    for t in sorted(tokens, key=lambda t: (t["top"], t["x0"])):
        if cur and abs(t["top"] - cur[0]["top"]) > tol:
            rows.append(sorted(cur, key=lambda t: t["x0"]))
            cur = []
        cur.append(t)
    if cur:
        rows.append(sorted(cur, key=lambda t: t["x0"]))
    return rows


def row_text(row, sep=" "):
    return sep.join(t["text"] for t in row)
