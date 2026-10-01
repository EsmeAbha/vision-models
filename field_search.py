"""Find named fields in a transcript, by matching the label that is printed.

This is deliberately not a model. The OCR models transcribe; asking either of
them for a field list was measured and does not work -- DeepSeek-OCR turned a
42-second, 1,451-character read into a 330-second, 35,505-character loop, and a
bare list of field names returned 28 characters. So the reading stays with the
reader, and the picking-out happens here, over the text it produced.

The rule is that a field is only reported when its label is actually on the
page. A label that is not found is reported as not found, never guessed at.
That is the whole point: an analyst can trust a value because the line it came
from is shown beside it.

Nothing in app.py is changed by this module; it only reads a string.
"""
from __future__ import annotations

import html
import re
import unicodedata

# Labels vary between issuers: one bill says "Account Number", the next says
# "Acct No." and a third puts it on the payment stub as "Account No". A field
# is therefore a name plus the other things that name is printed as.
SYNONYMS = {
    "Account number": ["account no", "acct no", "acct number", "account #",
                       "customer number", "customer no"],
    "Provider name": ["provider", "utility", "supplier", "issued by"],
    "Property name": ["service address", "property address", "premises address",
                      "site address", "property", "premises", "site"],
    "Service address": ["service address", "premises address", "property address"],
    "Due date": ["payment due date", "due by", "pay by", "payment due"],
    "Billing date": ["bill date", "invoice date", "statement date", "issued"],
    "Statement date": ["statement date", "bill date", "as of"],
    "Service period": ["billing period", "period", "service dates"],
    "Total charges": ["total current charges", "total amount due", "amount due",
                      "total due", "balance due", "total"],
    "Meter reading": ["meter no", "meter number", "meter"],
    "Usage": ["kwh used", "consumption", "usage", "units used"],
    "Starting balance": ["beginning balance", "opening balance", "previous balance",
                         "balance forward"],
    "Ending balance": ["closing balance", "new balance", "ending balance"],
    "Total debits": ["debits", "total withdrawals", "withdrawals"],
    "Total credits": ["credits", "total deposits", "deposits"],
    "Cap rate": ["capitalization rate", "cap rate", "overall rate"],
    "As-is market value": ["as is market value", "as-is value", "market value",
                           "as is value"],
    "As-is date": ["as is date", "effective date", "date of value",
                   "valuation date"],
    "Appraiser": ["appraised by", "appraiser", "prepared by"],
    "Net operating income": ["noi", "net operating income"],
    "Land value": ["land value", "site value"],
}

# A value is whatever sits after the label: behind a colon, across a run of
# whitespace, or in the next cell of a table row.
_SEPARATOR = re.compile(r"^[\s:\-\u2013\u2014|]+")
_TABLE_ROW = re.compile(r"\t|\s{2,}|\s*\|\s*")


def normalise(text):
    """Transcript markup down to plain lines.

    Both readers answer in markdown with HTML tables and escape as they go, so
    a printed $184.61 can arrive as &#36;184.61 inside a <td>. Searching the
    raw text would miss every amount on the page.
    """
    if not text:
        return ""
    text = re.sub(r"</t[dh]>\s*<t[dh][^>]*>", "\t", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)     # image placeholders
    text = re.sub(r"<!--.*?-->", "\n", text, flags=re.DOTALL)  # page markers
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\\[nr]", " ", text)   # literal backslash-n inside a cell
    text = re.sub(r"\*\*|__", "", text)
    lines = [re.sub(r"[ \t]+\n", "\n", ln).rstrip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


def _key(s):
    """Comparison form: accents, case, and punctuation stop mattering."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def labels_for(field, extra=None):
    """Every spelling of this field to look for, longest first.

    Longest first matters: "total current charges" must be tried before
    "total", or the wrong line wins on a bill that has both.
    """
    names = [field] + list(SYNONYMS.get(field, [])) + list(extra or [])
    seen, out = set(), []
    for name in sorted(names, key=lambda n: -len(n)):
        k = _key(name)
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _label_pattern(label_key):
    """A label, tolerant of the punctuation a printer puts inside it.

    "Account number" has to match "Account No.", "ACCOUNT NO:" and
    "Account  Number" alike, so the words are fixed and everything between
    them is not.
    """
    words = [re.escape(w) for w in label_key.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9]{0,3}".join(words),
                      re.IGNORECASE)


# Where one value stops and the next label starts, on a line carrying several.
# A tab is a cell boundary: without it here, "Due Date<tab>171.73<tab>09/28/2026"
# came back as one value containing a date, and scored as a good date.
_NEXT_LABEL = re.compile(r"	|\s{2,}|\s+(?=[A-Z][A-Za-z .]{1,28}:)")


def _value_on_line(line, label_key):
    """The value printed after this label on this line, if there is one.

    A separator is required -- a colon, a dash, a tab, or a run of spaces.
    Without that rule "Meter Reading Information" answers the field "Meter
    reading" with the word "Information", which is a heading, not a value.
    """
    match = _label_pattern(label_key).search(line)
    if not match:
        return ""
    rest = line[match.end():]
    separator = re.match(r"\s*[:\-\u2013\u2014]\s*|\t+|\s{2,}", rest)
    if not separator:
        return ""
    rest = rest[separator.end():]
    # Stop before whatever label comes next on the same line.
    cut = _NEXT_LABEL.search(rest)
    return (rest[:cut.start()] if cut else rest).strip(" :|\t")


def _cells(line):
    return [c.strip() for c in _TABLE_ROW.split(line) if c.strip()]




# --------------------------------------------------------------- value shapes
#
# What a field's value should look like. Used to choose between candidates when
# the layout has drifted: a "Due date" that comes back as an amount is wrong
# however close it sits to the label.
SHAPES = {
    "Due date": "date", "Billing date": "date", "Statement date": "date",
    "As-is date": "date", "Invoice date": "date", "Read date": "date",
    "Total charges": "money", "Starting balance": "money",
    "Ending balance": "money", "Total debits": "money", "Total credits": "money",
    "As-is market value": "money", "Land value": "money",
    "Net operating income": "money", "Amount due": "money",
    "Usage": "number", "Consumption": "number", "Cap rate": "rate",
    "Account number": "code", "Meter reading": "code", "Invoice number": "code",
}

_DATE = re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b"
                   r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}",
                   re.IGNORECASE)
_MONEY = re.compile(r"[$£€]\s?-?[\d,]+\.?\d*|\(?-?[\d,]+\.\d{2}\)?")
_NUMBER = re.compile(r"^-?[\d,]+(\.\d+)?$")
_RATE = re.compile(r"\d+(\.\d+)?\s?%|\b0?\.\d+\b")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/]{3,}")


def looks_like(value, shape):
    """Does this value have the shape the field expects?

    An unknown shape accepts anything non-empty -- absence of a rule must not
    become a reason to reject a good value.
    """
    v = (value or "").strip()
    if not v:
        return False
    if shape == "date":
        return bool(_DATE.search(v))
    if shape == "money":
        return bool(_MONEY.search(v))
    if shape == "number":
        return bool(_NUMBER.match(v.strip("$ ")))
    if shape == "rate":
        return bool(_RATE.search(v))
    if shape == "code":
        return bool(_CODE.search(v)) and any(c.isdigit() for c in v)
    return True


def shape_of(field):
    if field in SHAPES:
        return SHAPES[field]
    low = field.lower()
    for word, shape in (("date", "date"), ("balance", "money"), ("amount", "money"),
                        ("charge", "money"), ("value", "money"), ("total", "money"),
                        ("rate", "rate"), ("number", "code"), ("no", "code")):
        if word in low.split() or low.endswith(" " + word):
            return shape
    return "text"


def _is_label_like(line, all_keys):
    """A line that is itself a label, not a value.

    Needed because a run of labels can be printed before the run of values
    they belong to; stepping onto the next label and calling it an answer is
    the commonest way to get a confident wrong number.
    """
    flat = _key(line)
    if not flat:
        return False
    if any(_label_pattern(k).fullmatch(line.strip(" .:")) for k in all_keys):
        return True
    # No digits and reads like words: a heading rather than a value.
    return not any(c.isdigit() for c in line) and len(flat.split()) <= 6


def _candidates(lines, index, key, all_keys):
    """Everywhere the value for this label might have ended up.

    Each candidate carries how far it strayed, so a correct-looking value two
    steps away still loses to an equally correct one right beside the label.
    """
    out = []
    line = lines[index]
    pattern = _label_pattern(key)
    cells = _cells(line)

    same = _value_on_line(line, key)
    if same:
        out.append((same, "beside the label", 0))

    if len(cells) > 1:
        for column, cell in enumerate(cells):
            if not pattern.fullmatch(cell.strip(" .:")):
                continue
            for step, other in enumerate(cells[column + 1:][:3], start=1):
                out.append((other, "same row, %d cell(s) right" % step, step))
            for down in (1, 2):
                if index + down < len(lines):
                    below = _cells(lines[index + down])
                    if len(below) == len(cells) and column < len(below):
                        out.append((below[column], "under the heading", down))
                    elif len(below) == 1 and column == 0:
                        out.append((below[0], "line below", down))

    if pattern.fullmatch(line.strip(" .:")):
        # Label on its own. The value may be directly under it, or further
        # down past a block of other labels.
        run = 0
        for offset in range(1, 9):
            if index + offset >= len(lines):
                break
            nxt = lines[index + offset]
            if _is_label_like(nxt, all_keys):
                run += 1
                continue
            cells_below = _cells(nxt)
            pick = cells_below[-1] if len(cells_below) > 1 else nxt
            out.append((pick.strip(), "below the label" if run == 0
                        else "after %d other label(s)" % run, offset))
            break
    return out


def find_field(text, field, extra=None):
    """Locate one field, tolerating a value that has drifted from its label.

    Returns (value, evidence line, label matched). Candidates are collected
    from beside, right of, and below the label; the one whose shape matches
    the field wins, and distance breaks the tie. If none has the right shape,
    the nearest is returned anyway -- but callers can re-check with
    looks_like(), which is what the UI shows as a warning.
    """
    lines = [ln for ln in normalise(text).splitlines() if ln.strip()]
    shape = shape_of(field)
    keys = labels_for(field, extra)
    best = None
    for key in keys:
        for index in range(len(lines)):
            if not _label_pattern(key).search(lines[index]):
                continue
            for value, how, distance in _candidates(lines, index, key, keys):
                value = value.strip(" :|\t")
                if not value or _label_pattern(key).fullmatch(value.strip(" .:")):
                    continue
                fits = looks_like(value, shape)
                rank = (0 if fits else 1, distance, keys.index(key))
                if best is None or rank < best[0]:
                    best = (rank, value, lines[index], key, how, fits)
        if best and best[0][0] == 0:
            break   # a value of the right shape, on the best label: done
    if not best:
        return "", "", ""
    return best[1], f"{best[2]}  [{best[4]}]", best[3]


def find_fields(text, fields, extra_synonyms=None):
    """Every requested field, found or explicitly not found.

    `shape_ok` is False when a value was located but does not look like what
    the field should hold -- the signature of a table that has slipped a row.
    """
    extra_synonyms = extra_synonyms or {}
    out = []
    for field in fields:
        value, line, label = find_field(text, field, extra_synonyms.get(field))
        guessed = False
        if not value:
            # Nothing carried this label. A few fields are printed without one
            # -- the issuer's name is the letterhead, not "Provider: ..." --
            # so those are inferred, and flagged as inferred.
            value, line, how = find_unlabelled(text, field)
            if value:
                guessed, label, line = True, "", f"{line}  [{how}]"
        shape = shape_of(field)
        out.append({
            "field": field,
            "value": value,
            "found": bool(value),
            "guessed": guessed,
            "matched_label": label,
            "evidence": line,
            "shape": shape,
            "shape_ok": bool(value) and looks_like(value, shape),
        })
    return out


# ------------------------------------------------------- unlabelled fields
#
# Some values are printed without a label at all: the issuer's name is the
# letterhead, not "Provider: ...". Those cannot be found by matching a label,
# so they are guessed -- and reported as guesses, never mixed in with values
# that were found beside their label.
#
# Position is not the rule. On a scanned bill the letterhead was dropped by
# the reader and the only copy of "Metro Light & Power" sat at line 35 of 38,
# in the remittance stub; "first line of the transcript" would have answered
# with the customer's name instead. So the test is what an organisation name
# looks like, and position only breaks ties.
# "service", "city" and the like are dropped deliberately: with them in, the
# guess answered "Service Period" and "Service Address:" -- both labels.
ORG_WORDS = {
    "power", "energy", "electric", "electricity", "gas", "water", "utility",
    "utilities", "communications", "telecom", "bank", "trust", "group",
    "holdings", "properties", "management", "realty", "associates", "partners",
    "company", "corp", "corporation", "inc", "incorporated", "llc", "llp",
    "ltd", "limited", "plc", "municipal", "cooperative", "co-op",
}


def _all_known_labels():
    """Every label the search knows, so a guess never returns one."""
    out = set()
    for field, synonyms in SYNONYMS.items():
        out.add(_key(field))
        out.update(_key(s) for s in synonyms)
    return out
UNLABELLED = {"Provider name": "organisation", "Issuer": "organisation",
              "Utility": "organisation", "Bank": "organisation"}


def looks_like_org(line):
    """Does this line read as an organisation's name?

    Wants a recognisable company word, no digits, and the length of a name
    rather than a sentence. A customer's name fails on the first test, which
    is the distinction that matters on a bill.
    """
    if line.strip().endswith(":"):
        return False          # a label, not a name
    text = line.strip().strip(":")
    if not text or any(c.isdigit() for c in text):
        return False
    if _key(text) in _all_known_labels():
        return False          # it is a field label, so it is not the issuer
    words = _key(text).split()
    if not 1 < len(words) <= 6:
        return False
    if "&" in text and len(words) <= 5:
        return True
    return any(w in ORG_WORDS for w in words)


def find_unlabelled(text, field):
    """Guess a value that is printed without any label.

    Returns (value, evidence, how) or ("", "", "") when nothing plausible is
    on the page. The caller marks whatever comes back as a guess.
    """
    if UNLABELLED.get(field) != "organisation":
        return "", "", ""
    lines = [ln.strip() for ln in normalise(text).splitlines() if ln.strip()]
    best = None
    for index, line in enumerate(lines):
        parts = re.split(r"\t|\s{2,}", line)
        head = re.split(r"\s+(?=[A-Z][A-Za-z .]{1,28}:)", line)[0]
        if head and head not in parts:
            parts.append(head)
        for part in parts:
            if not looks_like_org(part):
                continue
            # Earliest wins, but only among lines that already read as a name.
            if best is None or index < best[0]:
                best = (index, part.strip(), line)
    if not best:
        return "", "", ""
    return best[1], best[2], "no label \u2014 reads as an organisation name"
