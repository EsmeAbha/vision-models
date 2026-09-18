"""Group physical lines into the records a report is actually about.

A parser reads a page one printed line at a time, which is the right unit for
checking a document and the wrong unit for using it. One lease occupies a row
naming the tenant, sometimes further rows for space added to the same lease,
and often a run of rows carrying a rent step in each future year. Those are
one thing, and a sheet with one row per printed line leaves the reader to work
that out by eye.

Nothing here knows what a lease is. A record begins where a labelled row sits
at the indent that labelled rows usually sit at, and continues through rows
that do not. That rule is about layout, not about property management, so it
holds for a payment schedule or a trial balance for the same reason it holds
for a rent roll.
"""
from __future__ import annotations

import collections


def record_indent(rows, tol=3.0):
    """The x where a new record's label starts, taken from the document.

    The mode rather than the minimum, for the same reason the validator takes
    the mode of body indents: a note or a continuation can sit further left
    than the records themselves, and one such row would otherwise redefine
    where every record begins.
    """
    xs = [round(r["x0"], 0) for r in rows
          if r["x0"] is not None and r["label"].strip() and any(r["values"])]
    if not xs:
        return None
    return collections.Counter(xs).most_common(1)[0][0]


def _kind(row, head_x0, tol):
    """What this row is doing, judged only by where it sits and what it holds."""
    x0, label, vals = row["x0"], row["label"].strip(), any(row["values"])
    if x0 is not None and abs(x0 - head_x0) <= tol and label:
        return "head"
    if not label and vals:
        return "continuation"
    if label and not vals:
        return "note"
    return "continuation" if vals else "note"


def assemble(rows, head_x0=None, tol=3.0):
    """[{head, lines, notes}] -- one entry per record.

    Rows before the first head are returned under a record with `head` None
    rather than dropped. They are usually column headings, but a document that
    opens mid-record exists too, and silently discarding lines is exactly the
    failure this project is built to avoid.
    """
    head_x0 = record_indent(rows, tol) if head_x0 is None else head_x0
    if head_x0 is None:
        return []

    out, cur = [], None
    for r in rows:
        kind = _kind(r, head_x0, tol)
        if kind == "head":
            if cur is not None:
                out.append(cur)
            cur = {"head": r, "lines": [], "notes": []}
            continue
        if cur is None:
            cur = {"head": None, "lines": [], "notes": []}
        cur["notes" if kind == "note" else "lines"].append(r)
    if cur is not None:
        out.append(cur)
    return out


def series_columns(records, min_records=2, min_rows=2):
    """Columns that carry a repeating sub-series, e.g. a schedule of steps.

    Found by asking which columns the continuation rows fill. A rent step
    prints a date and a rate on each of several rows under one tenant while
    every other column stays empty, so those columns stand out by appearing
    again and again below a head and nowhere else.
    """
    fills = collections.Counter()
    seen = 0
    for rec in records:
        if rec["head"] is None or len(rec["lines"]) < min_rows:
            continue
        seen += 1
        n = len(rec["lines"])
        for i in range(max((len(ln["values"]) for ln in rec["lines"]), default=0)):
            got = sum(1 for ln in rec["lines"]
                      if i < len(ln["values"]) and ln["values"][i])
            # Filled on MOST of the record's continuation rows, not merely
            # present somewhere among them. A step column repeats on every
            # line of the series; a per-record attribute -- a renewal
            # percentage, a guarantee -- prints once on the first line under
            # the head and would otherwise be mistaken for part of the step
            # and dragged across the sheet with nothing beneath it.
            if got >= max(2, n * 0.6):
                fills[i] += 1
    if seen < min_records:
        return []
    return sorted(c for c, n in fills.items() if n >= max(min_records, seen * 0.6))


def head_columns(records, series):
    """Columns belonging to the record itself rather than to its sub-series."""
    if not records:
        return []
    width = 0
    for rec in records:
        if rec["head"] is not None:
            width = max(width, len(rec["head"]["values"]))
    return [i for i in range(width) if i not in set(series)]


def flatten(records, series, head_cols, max_series=None, propagate=True):
    """One row per record: its own fields, then the sub-series laid out across.

    A variable-length series has to go somewhere, and a column per step keeps
    one record on one row where a person can read it. The count is taken from
    the longest record rather than fixed, so nothing is truncated quietly.

    `propagate` fills a blank field on a record from the rows beneath it. A
    report often prints a value once and leaves it implied below; carrying it
    down is what makes the row usable, and it is never invented -- only copied
    from a line that belongs to the same record.
    """
    rows = []
    longest = 0
    for rec in records:
        if rec["head"] is None:
            continue
        # The head row carries the FIRST step in the same columns the lines
        # below use, so it has to be read as one. Taking only the rows beneath
        # dropped a step from every record -- silently, because the remaining
        # steps still looked like a complete series.
        steps = []
        head_step = [rec["head"]["values"][i]
                     if i < len(rec["head"]["values"]) else None
                     for i in series]
        if any(head_step):
            steps.append(head_step)
        for ln in rec["lines"]:
            step = [ln["values"][i] if i < len(ln["values"]) else None
                    for i in series]
            if any(step):
                steps.append(step)
        longest = max(longest, len(steps))

        fields = [rec["head"]["values"][i] if i < len(rec["head"]["values"])
                  else None for i in head_cols]
        if propagate:
            for j, i in enumerate(head_cols):
                if fields[j]:
                    continue
                for ln in rec["lines"]:
                    if i < len(ln["values"]) and ln["values"][i]:
                        fields[j] = ln["values"][i]
                        break
        rows.append({
            "page": rec["head"]["page"],
            "y": rec["head"]["y"],
            "label": rec["head"]["label"],
            "fields": fields,
            "steps": steps,
            "notes": [n["label"].strip() for n in rec["notes"] if n["label"].strip()],
        })

    n = longest if max_series is None else min(longest, max_series)
    for r in rows:
        flat = []
        for k in range(n):
            flat += r["steps"][k] if k < len(r["steps"]) else [None] * len(series)
        r["values"] = r["fields"] + flat
    return rows, n


def column_names(base_names, series_names, n_series):
    """Header for a flattened sheet: the record's own columns, then the steps."""
    out = list(base_names)
    for k in range(n_series):
        out += [f"{s} {k + 1}" for s in series_names]
    return out
