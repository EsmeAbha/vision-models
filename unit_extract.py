"""One row per unit, built only from that unit's own documents.

A lease folder holds a sub-folder per unit, and each of those holds whatever
the tenancy happened to produce: a lease, addenda, a screening report,
move-in paperwork. The fields wanted -- who the tenant is, when the lease
runs, what the rent and deposit are -- can be in any of them.

The rule that matters is isolation. Unit 4E is read from 4E's folder and
nothing else: a separate pass, a fresh prompt, no shared conversation. Two
units in one context is how one lease's rent ends up on another's row, and
that mistake is invisible afterwards because the wrong number still looks
like a number.

Every value the model returns is then checked back against the text it was
supposed to come from. A value that cannot be found in the source is not
silently dropped and not silently kept: it is written to the sheet and
flagged, because a wrong rent that looks clean is worse than a blank one.
"""
from __future__ import annotations

import json
import os
import re
import shutil

import requests

import readers

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("FINAI_EXTRACT_MODEL", "qwen2.5:14b-instruct-16k")

# The blue block of the template, in order. Column letters are where they go.
FIELDS = [
    ("tenant", "J", "Tenant"),
    ("suite", "K", "Suite"),
    ("lease_start", "L", "Lease Start"),
    ("lease_expire", "M", "Lease Expire"),
    ("preferential_rent", "N", "Preferential Rent"),
    ("rent_per_month", "O", "Rent Per Month"),
    ("security_deposit", "P", "Security Deposit"),
    ("concession", "Q", "Concession"),
]

SCHEMA = {
    "type": "object",
    "properties": {
        "tenant": {"type": "string"},
        "suite": {"type": "string"},
        "lease_start": {"type": "string"},
        "lease_expire": {"type": "string"},
        "preferential_rent": {"type": "string"},
        "rent_per_month": {"type": "string"},
        "security_deposit": {"type": "string"},
        "concession": {"type": "string"},
    },
    "required": [k for k, _c, _l in FIELDS],
}

PROMPT = """You are reading the lease file for ONE apartment unit. Extract \
only these fields, from the text below and nothing else.

- tenant: the tenant's full name as written on the lease. If there are two \
tenants, give both separated by " & ".
- suite: the unit or apartment number.
- lease_start: the lease commencement date, as written.
- lease_expire: the lease expiration or end date, as written.
- preferential_rent: the preferential rent amount, if the lease states one.
- rent_per_month: the monthly rent actually payable.
- security_deposit: the security deposit amount.
- concession: any free rent, abatement or concession, e.g. "1 month free".

Rules:
- Copy values exactly as printed. Do not reformat dates or amounts.
- If a field is not stated anywhere in the text, return an empty string for \
it. Do NOT guess, and do NOT carry a value over from a different field.
- An amount must appear in the text. Never calculate one.

TEXT:
"""


# ------------------------------------------------------------------ the units

def find_units(root):
    """[(unit_name, [file paths])] -- one entry per immediate sub-folder."""
    root = (root or "").strip().strip('"').strip("'")
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        files = []
        for dp, _sub, fs in os.walk(d):
            for f in sorted(fs):
                if os.path.splitext(f)[1].lower() in (
                        ".pdf", ".docx", ".txt", ".xlsx", ".csv"):
                    files.append(os.path.join(dp, f))
        out.append((name, files))
    return out


def suite_from_name(name):
    """The unit label the folder itself carries, e.g. '1.25.22 4E' -> '4E'.

    Used only as a cross-check on what the documents say. The folder name is
    chosen by whoever filed it, so it never overrides the lease -- but a
    disagreement between the two is worth a person's eye.
    """
    m = re.search(r"\b(\d{1,3}[A-Za-z]{1,2})\s*$", name.strip())
    return m.group(1) if m else ""


# ------------------------------------------------------------- reading a unit

def unit_text(files, per_file=9000, total=26000):
    """Text of every document in one unit, with its source marked.

    Capped, because a lease with addenda can run to hundreds of pages and the
    fields wanted are in the first part of each document. What was read and
    what was cut is reported, so a missing field can be chased rather than
    guessed at.
    """
    parts, read, skipped = [], [], []
    used = 0
    for path in files:
        if used >= total:
            skipped.append((os.path.basename(path), "budget reached"))
            continue
        res = readers.read_document(path, max_chars=per_file)
        if res.get("error") or not res.get("text"):
            skipped.append((os.path.basename(path),
                            res.get("error", "no text")[:60]))
            continue
        chunk = f"\n===== FILE: {os.path.basename(path)} =====\n{res['text']}"
        parts.append(chunk)
        used += len(chunk)
        read.append({"file": os.path.basename(path), "how": res.get("how", ""),
                     "chars": len(res["text"])})
    return "".join(parts)[:total], read, skipped


# ------------------------------------------------------------- the extraction

def _ask(text, timeout=600):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT + text}],
        "stream": False,
        "format": SCHEMA,
        # Same file, same answer. The shape is enforced by the server rather
        # than asked for in the prompt, because a prompt instruction about
        # JSON is a request and this is a guarantee.
        "options": {"temperature": 0, "top_p": 1, "top_k": 1, "seed": 7,
                    "num_ctx": 16384},
    }
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    content = (r.json().get("message", {}) or {}).get("content", "") or "{}"
    try:
        return json.loads(content)
    except ValueError:
        return {}


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", str(s).lower())


def verify(value, source):
    """Did this value actually come from the document?

    Amounts are compared on digits alone, so "$3,478.88" matches "3478.88"
    however it was printed. Names are compared on their parts, because a lease
    may print "RICHARD A. KAMINSKY" where the model returns "Richard A.
    Kaminsky". A value that matches nothing is the one worth flagging: those
    are where an invention would hide.
    """
    v = str(value or "").strip()
    if not v:
        return True, "blank"
    src = _norm(source)

    digits = re.sub(r"[^0-9]", "", v)
    if digits and len(digits) >= 3:
        return (digits in re.sub(r"[^0-9]", "", source),
                "digits found" if digits in re.sub(r"[^0-9]", "", source)
                else "digits NOT found in source")

    parts = [p for p in re.split(r"[\s,&]+", v) if len(p) > 2]
    if parts:
        hit = sum(1 for p in parts if _norm(p) in src)
        ok = hit >= max(1, len(parts) // 2)
        return ok, f"{hit}/{len(parts)} name parts found"
    return _norm(v) in src, "short value"


def extract_unit(name, files):
    """One unit, read and checked on its own. Returns a row dict."""
    text, read, skipped = unit_text(files)
    row = {"unit": name, "files_read": len(read), "files_skipped": len(skipped),
           "skipped_detail": skipped, "flags": [], "values": {}}
    if not text.strip():
        row["flags"].append("no readable text in this unit's folder")
        return row

    try:
        got = _ask(text)
    except Exception as e:
        row["flags"].append(f"model error: {type(e).__name__}: {e}")
        return row

    for key, _col, label in FIELDS:
        value = str(got.get(key, "") or "").strip()
        ok, why = verify(value, text)
        row["values"][key] = value
        if value and not ok:
            row["flags"].append(f"{label}: '{value}' could not be found in "
                                f"this unit's documents ({why})")
        elif not value:
            row["flags"].append(f"{label}: not stated in this unit's documents")

    folder_suite = suite_from_name(name)
    got_suite = row["values"].get("suite", "")
    if folder_suite and got_suite and _norm(folder_suite) != _norm(got_suite):
        row["flags"].append(
            f"Suite: the folder is named '{folder_suite}' but the documents "
            f"say '{got_suite}' - check which unit this belongs to")
    if folder_suite and not got_suite:
        row["values"]["suite"] = folder_suite
        row["flags"].append(
            f"Suite: taken from the folder name '{folder_suite}' because no "
            f"unit number was found in the documents")
    return row


# ------------------------------------------------------------------ the sheet

YELLOW = "FFF2CC"


def write_template(template, out_path, rows, start_row=4):
    """Fill the blue block, one row per unit, flagging what needs checking."""
    from openpyxl.styles import PatternFill

    shutil.copyfile(template, out_path)
    import openpyxl
    wb = openpyxl.load_workbook(out_path)
    ws = wb[wb.sheetnames[0]]
    fill = PatternFill("solid", start_color=YELLOW, end_color=YELLOW)

    for i, row in enumerate(rows):
        r = start_row + i
        flagged = {}
        for f in row["flags"]:
            label = f.split(":")[0]
            flagged[label] = f
        for key, col, label in FIELDS:
            cell = ws[f"{col}{r}"]
            cell.value = row["values"].get(key, "")
            if label in flagged:
                cell.fill = fill
        note = "; ".join(row["flags"])
        if note:
            ws[f"Y{r}"] = note
            ws[f"Y{r}"].fill = fill
    wb.save(out_path)
    return out_path


def run(root, template, out_path, on_event=None):
    """Every unit under `root`, each read alone, into a copy of the template."""
    def say(msg):
        if on_event:
            on_event(msg)

    units = find_units(root)
    if not units:
        return {"error": f"no unit sub-folders found under {root}"}, []

    rows = []
    for i, (name, files) in enumerate(units, 1):
        say(f"[{i}/{len(units)}] {name} - {len(files)} file(s)")
        rows.append(extract_unit(name, files))
    write_template(template, out_path, rows)

    filled = sum(1 for r in rows
                 for k, _c, _l in FIELDS if r["values"].get(k))
    return {"units": len(rows), "workbook": out_path,
            "values_filled": filled,
            "units_with_flags": sum(1 for r in rows if r["flags"]),
            "total_flags": sum(len(r["flags"]) for r in rows)}, rows
