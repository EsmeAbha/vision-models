"""Check a parse against what the document says about itself.

A report that prints its own totals has already done the arithmetic. If the
figures read off the page add up to the printed total, the reading is almost
certainly right; if they do not, something is wrong and the cell must not be
shipped as clean. That is the whole basis for trusting the output, and it is
why no number here is ever "probably fine".

Every check returns the same record so a report can be rendered, a cell
flagged, and a reason given to the person who has to fix it:

    {check, scope, label, expected, got, diff, ok, note}
"""
from __future__ import annotations

import re

from .tabular import parse_number

# Printed figures are rounded, so sums carry a little slack. Anything larger
# than a cent per contributing row is a real disagreement, not rounding.
TOL = 0.011


def _val(row, col):
    return parse_number(row["values"][col]) if row["values"][col] else None


def body_indents(rows, tol=2.0):
    """(item_x0, total_min_x0) measured from the rows that carry figures.

    Taken from x0 rather than the level index for two reasons. Levels are
    numbered per page, so level 1 on page 2 need not be the same indent as
    level 1 on page 1; and `indent_levels` counts the title, date and column
    headings as levels too, which on this income statement produced ten of
    them and left the totals at a level nothing matched.

    The item indent is the MOST COMMON one among rows that hold figures, not
    the shallowest. A grand total is often printed flush with the section
    headings rather than indented under them -- on this income statement
    "TOTAL INCOME" and "NET INCOME (LOSS)" sit at x=53 alongside the headers,
    while the 76 line items sit at x=59. Taking the minimum made a grand total
    look like a line item and every check failed. Items outnumber totals by a
    wide margin in any report, so the mode is the stable signal.
    """
    import collections

    xs = [round(r["x0"], 0) for r in rows
          if r["x0"] is not None and any(v for v in r["values"])]
    if not xs:
        return None, None
    item = collections.Counter(xs).most_common(1)[0][0]
    deeper = [x for x in xs if x > item + tol]
    return item, (min(deeper) if deeper else None)


def section_totals(rows, col, total_level=None, item_level=None, tol=TOL,
                   indent_tol=2.0):
    """Each total row against the sum of the items it follows.

    Sections are delimited by the headers above them, so the items counted
    toward a total are those since the last header -- not every item on the
    page, which would silently pass on a report with one section and fail on
    every report with two.
    """
    item_x0, total_x0 = body_indents(rows, indent_tol)
    if item_x0 is None or total_x0 is None:
        return []

    out, bucket = [], []
    for r in rows:
        x = r["x0"]
        if x is None:
            continue
        if x < item_x0 - indent_tol:        # a header starts a new section
            bucket = []
            continue
        if abs(x - item_x0) <= indent_tol:
            v = _val(r, col)
            if v is not None:
                bucket.append((r["label"], v))
            continue
        if x >= total_x0 - indent_tol:
            printed = _val(r, col)
            if printed is None or not bucket:
                bucket = []
                continue
            got = round(sum(v for _l, v in bucket), 2)
            diff = round(got - printed, 2)
            out.append({
                "check": "section_total",
                "scope": f"col{col}",
                "label": r["label"],
                "expected": printed,
                "got": got,
                "diff": diff,
                "ok": abs(diff) <= max(tol, tol * len(bucket)),
                "note": f"{len(bucket)} item(s)",
                "page": r["page"],
                "y": r["y"],
            })
            bucket = []
    return out


# A row that sums the rows above it. "Total", "Subtotal" and "Grand Total"
# are the obvious spellings; "Top 10 Tenant Exposure" is the convention these
# reports use for a subtotal over the largest tenants. "All Others" is NOT
# here on purpose -- it is a residual line, a data row like any other, and
# treating it as a total would make the grand total check compare against
# itself.
TOTAL_LABEL = re.compile(
    r"^\s*(grand\s+)?(sub)?\s*totals?\b"
    r"|^\s*top\s+\d+\b"
    r"|\btotals?\s*$", re.I)


def _hypotheses(pending, col, n_cols):
    """Every way this total could relate to the rows above it: {name: value}.

    Not every column is added up. A rent roll's rent and area are sums, but
    its rent per square foot is a ratio of the two totals and its remaining
    lease term is an average weighted by area -- adding those up gives $327.92
    per square foot, which is less a failed check than a wrong question.
    """
    vals = [_val(r, col) for r in pending]
    vals = [v for v in vals if v is not None]
    if not vals:
        return {}

    out = {"sum": round(sum(vals), 2),
           "average": round(sum(vals) / len(vals), 4)}

    for w in range(n_cols):
        if w == col:
            continue
        num = den = 0.0
        ok = True
        for r in pending:
            wv, cv = _val(r, w), _val(r, col)
            if wv is None or cv is None:
                ok = False
                break
            num += wv * cv
            den += wv
        if ok and den:
            out[f"average weighted by column {w + 1}"] = round(num / den, 4)

    for x in range(n_cols):
        for y in range(n_cols):
            if x == y or col in (x, y):
                continue
            tx = [_val(r, x) for r in pending]
            ty = [_val(r, y) for r in pending]
            if any(v is None for v in tx) or any(v is None for v in ty):
                continue
            den = sum(ty)
            if den:
                out[f"column {x + 1} total over column {y + 1} total"] =                     round(sum(tx) / den, 4)
    return out


def _tolerance(method, printed, n):
    """A sum is exact; an average is printed rounded and built from rounded
    figures, so it needs more slack."""
    if method == "sum":
        return max(TOL, TOL * n)
    return max(0.05, abs(printed) * 0.002)


# How much a method has to recommend itself before an exotic one is believed.
_SIMPLICITY = {"sum": 0, "average": 1}


def _rank(method):
    return _SIMPLICITY.get(method, 2 if method.startswith("average") else 3)


def flat_totals(rows, col, n_cols=1, tol=TOL):
    """Totals in a table whose rows all share one indent.

    The income statement marks a total by printing it at a different indent.
    A rent roll does not: on the tenant exposure table every row, totals
    included, starts at the same x, so `body_indents` finds no total level
    and `section_totals` returns nothing at all. Here the label is the only
    signal available, so the label is what is used.

    A matched total consumes the rows accumulated since the last one and then
    stands in their place, so a grand total below a subtotal is checked
    against the subtotal rather than against the rows the subtotal already
    covered. Without that, a table with both would double-count everything
    above the subtotal and fail a document that is perfectly correct.
    """
    segments, pending = [], []
    for r in rows:
        v = _val(r, col)
        if v is None:
            continue
        if TOTAL_LABEL.search(r["label"] or ""):
            if pending:
                segments.append((r, v, list(pending)))
            pending = [r]
        else:
            pending.append(r)
    if not segments:
        return []

    # The relationship is a property of the COLUMN, settled once and then
    # applied to every total in it. Testing each total against sixty
    # hypotheses independently invites numerology: on this table the grand
    # total's rate per square foot came within 0.07 of the rate averaged and
    # weighted by remaining lease term, which is not a quantity anyone
    # computes. Requiring one method to serve the whole column kills those
    # coincidences, and it makes a failure legible -- the subtotal establishes
    # that the rate is rent over area, so the grand total is reported against
    # rent over area rather than against whatever number happens to be near.
    votes = {}
    hyps = []
    for row, printed, pend in segments:
        h = _hypotheses(pend, col, n_cols)
        hyps.append(h)
        for name, value in h.items():
            if abs(value - printed) <= _tolerance(name, printed, len(pend)):
                # Weighted by how many rows stood behind the agreement. A
                # relationship confirmed across ten tenant rows is evidence;
                # the same relationship "confirmed" by two subtotals is
                # nearly arithmetic-free. Counting segments equally let a
                # simple average of two numbers outrank an area-weighted
                # average that held over ten.
                votes[name] = votes.get(name, 0) + len(pend)
    if votes:
        method = min(votes, key=lambda m: (-votes[m], _rank(m), m))
    else:
        method = "sum"

    out = []
    for (row, printed, pend), h in zip(segments, hyps):
        got = h.get(method)
        if got is None:
            got, used = h.get("sum"), "sum"
        else:
            used = method
        if got is None:
            continue
        ok = abs(got - printed) <= _tolerance(used, printed, len(pend))
        out.append({
            "check": "table_total",
            "scope": f"col{col}",
            "label": row["label"],
            "expected": printed,
            "got": got,
            "diff": round(got - printed, 2),
            "ok": ok,
            "note": (f"{len(pend)} row(s) above it, as {used}" if ok else
                     f"{len(pend)} row(s) above it; does not reconcile as "
                     f"{used}, which is how this column's other totals are "
                     f"reached" if len(segments) > 1 else
                     f"{len(pend)} row(s) above it; does not reconcile"),
            "page": row["page"],
            "y": row["y"],
        })
    return out


def unbinned_tokens(rows):
    """Any numeric token that matched no column. Must be zero, or explained.

    The pack is unambiguous here and experience agrees: a stray number means
    the layout is not understood yet. Shipping around it is how a column ends
    up quietly holding somebody else's figures.
    """
    out = []
    for r in rows:
        if r["unbinned"]:
            out.append({
                "check": "unbinned_token",
                "scope": "row",
                "label": r["label"][:60],
                "expected": "no stray numbers",
                "got": ", ".join(r["unbinned"]),
                "diff": None,
                "ok": False,
                "note": "numeric token outside every column",
                "page": r["page"],
                "y": r["y"],
            })
    return out


def column_completeness(rows, cols, item_level=None):
    """Item rows that filled some columns but not others.

    Usually means a value was missed rather than absent: a report that prints
    a figure in one column normally prints one in the rest of that row.
    """
    item_x0, _total_x0 = body_indents(rows)
    if item_x0 is None:
        return []
    out = []
    for r in rows:
        if r["x0"] is None or abs(r["x0"] - item_x0) > 2.0:
            continue
        filled = [i for i, v in enumerate(r["values"]) if v]
        if filled and len(filled) != len(cols):
            missing = [i for i in range(len(cols)) if i not in filled]
            out.append({
                "check": "ragged_row",
                "scope": "row",
                "label": r["label"][:60],
                "expected": f"{len(cols)} value(s)",
                "got": f"{len(filled)} value(s)",
                "diff": None,
                "ok": False,
                "note": f"empty column(s): {missing}",
                "page": r["page"],
                "y": r["y"],
            })
    return out


def run_all(rows, cols):
    """Every check, for every numeric column.

    Which total check applies is decided by the document, not configured:
    when the rows carry a total indent the sections are read from the
    geometry, and when they are all at one indent the labels are all there
    is to go on.
    """
    results = []
    _item, total_x0 = body_indents(rows)
    indented = total_x0 is not None
    for c in range(len(cols)):
        results += (section_totals(rows, c) if indented
                    else flat_totals(rows, c, len(cols)))
    results += unbinned_tokens(rows)
    if indented:
        results += column_completeness(rows, cols)
    return results


def summarise(results):
    passed = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    by_check = {}
    for r in results:
        b = by_check.setdefault(r["check"], {"pass": 0, "fail": 0})
        b["pass" if r["ok"] else "fail"] += 1
    return {"total": len(results), "passed": len(passed),
            "failed": len(failed), "by_check": by_check,
            "failures": failed}


def report(results, show=12):
    s = summarise(results)
    lines = [f"{s['passed']}/{s['total']} checks passed"]
    for name, b in sorted(s["by_check"].items()):
        lines.append(f"  {name:<18} pass {b['pass']:>3}   fail {b['fail']:>3}")
    if s["failures"]:
        lines.append("")
        lines.append("FAILURES:")
        for f in s["failures"][:show]:
            if f["diff"] is not None:
                lines.append(f"  p{f['page']} {f['label'][:44]:<46} "
                             f"printed {f['expected']:>14,.2f}  "
                             f"summed {f['got']:>14,.2f}  diff {f['diff']:>10,.2f}")
            else:
                lines.append(f"  p{f['page']} {f['label'][:44]:<46} "
                             f"{f['check']}: {f['got']}  ({f['note']})")
        if len(s["failures"]) > show:
            lines.append(f"  ... and {len(s['failures']) - show} more")
    return "\n".join(lines)
