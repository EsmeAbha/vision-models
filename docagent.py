"""Finding and reading a report, with no user interface attached.

This holds the logic two front ends need: the scan-and-run page, and the chat
agent. It imports no Gradio on purpose. A module that builds interface
components at import time cannot safely be imported from inside a live
request -- doing that once already produced a second set of components and an
"object has no attribute '_id'" failure that took three wrong guesses to find.
"""
import os
import time

from engine import geometry as G
from engine import skills as S
from engine import tabular as T
from engine import validate as V
from engine import writer as W

_here = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_here, "outputs")

# A document longer than this is never OCR'd without being asked about first.
# OCR here means unloading the language model and bringing vLLM up in WSL,
# which is minutes of shared GPU, so it is a decision rather than a side
# effect of pressing a button.
OCR_PROMPT_PAGES = 10


def _pdfs_under(root):
    root = (root or "").strip().strip('"').strip("'")
    if not root:
        return [], "Give a folder or a PDF."
    if os.path.isfile(root) and root.lower().endswith(".pdf"):
        return [root], ""
    if not os.path.isdir(root):
        return [], f"Not a folder or a PDF: {root}"
    out = []
    for dp, _d, fs in os.walk(root):
        for fn in fs:
            if fn.lower().endswith(".pdf"):
                out.append(os.path.join(dp, fn))
    return sorted(out), ("" if out else f"No PDFs under {root}")


def _page_count(path):
    try:
        import pdfplumber
        with pdfplumber.open(path) as d:
            return len(d.pages)
    except Exception:
        return 0


def find_skill(path, tokens=None):
    """Look for a known report type ANYWHERE in the file, not just at the front.

    A rent roll is usually a section inside a larger pack -- this one starts on
    page 38 of a 92-page receiver report -- so fingerprinting the first pages
    finds the cover letter and matches nothing.

    Every page is checked, not a sample. A sparse probe stepping through the
    document missed this rent roll completely: at 92 pages the step was six
    and it sampled 37 and 43, straddling the section. The whole file is read
    once and split by page, so checking them all costs one pass rather than
    one file-open per page.
    """
    if tokens is None:
        try:
            tokens = G.from_pdf(path)
        except Exception:
            return None, 0.0, []
    if not tokens:
        return None, 0.0, []

    by_page = {}
    for t in tokens:
        by_page.setdefault(t["page"], []).append(t)

    hits, best, best_score = {}, None, 0.0
    for pg in sorted(by_page):
        cfg, score = S.match(by_page[pg])
        if score > best_score:
            best, best_score = cfg, score
        if cfg:
            hits[pg] = cfg["slug"]
    if not hits:
        return best, best_score, []

    slug = max(set(hits.values()), key=list(hits.values()).count)
    matched = sorted(pg for pg, s in hits.items() if s == slug)

    # A section's later pages do not carry the full banner the first page
    # does, so they do not fingerprint as the same report and the match
    # stopped at page 38 of a range that runs to 41. Identity finds where the
    # section STARTS; structure finds how far it goes. A following page
    # belongs to it while its numeric columns still land where the skill says
    # they do.
    cfg = next((c for c in S.load_all() if c.get("slug") == slug), None)
    want = {round(c["x1"] / 8) for c in (cfg or {}).get("columns", [])}
    if want:
        full = set(matched)
        for pg in matched:
            for direction in (-1, 1):
                q = pg + direction
                while q in by_page and q not in full:
                    edges = {round(t["x1"] / 8) for t in by_page[q]
                             if T.looks_value(t["text"])}
                    if not edges or len(edges & want) / len(edges) < 0.6:
                        break
                    full.add(q)
                    q += direction
        matched = sorted(full)

    # The page carrying the report's own totals is laid out as a summary, not
    # as the table, so the structural test rejects it -- and it is the one
    # page the reconciliation cannot do without. A following page is taken in
    # when it prints the totals this skill checks against.
    markers = [spec.get("startswith", "")
               for spec in (cfg or {}).get("printed", {}).values()]
    if markers and matched:
        q = matched[-1] + 1
        while q in by_page:
            text = "".join(t["text"] for t in by_page[q])
            if not any(m and m.replace(" ", "") in text for m in markers):
                break
            matched.append(q)
            q += 1
    return best, best_score, matched


def _fmt_pages(pages):
    """[38,39,40,41] -> '38-41'"""
    if not pages:
        return ""
    out, start, prev = [], pages[0], pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        out.append(f"{start}-{prev}" if prev > start else f"{start}")
        start = prev = p
    out.append(f"{start}-{prev}" if prev > start else f"{start}")
    return ",".join(out)


def _run_one(path, pages, learn_new):
    """Read one document. Returns (summary_row, workbook_path_or_None, detail)."""
    name = os.path.basename(path)
    ok, note = G.has_text_layer(path)
    if not ok:
        return ({"File": name, "Records": 0, "Checks": "-", "Flags": "-",
                 "Skill": "-", "Status": "needs OCR (not run)"}, None, note)

    page_set = None
    if pages.strip():
        page_set = set()
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                page_set.update(range(int(a), int(b) + 1))
            elif part:
                page_set.add(int(part))

    whole = G.from_pdf(path)
    found_on = ""
    if page_set is None:
        # No range given: go and find the part of the document that is a
        # report this agent knows, rather than trying to read a 92-page pack
        # as one table.
        cfg0, score0, pages_found = find_skill(path, tokens=whole)
        if pages_found:
            page_set = set(pages_found)
            found_on = _fmt_pages(pages_found)

    toks = ([t for t in whole if t["page"] in page_set] if page_set else whole)
    if not toks:
        return ({"File": name, "Records": 0, "Checks": "-", "Flags": "-",
                 "Skill": "-", "Status": "no text on those pages"}, None, "")

    cfg, score = S.match(toks)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stem = os.path.splitext(name)[0][:60]
    out = os.path.join(OUTPUT_DIR, f"{stem}.xlsx")

    if cfg:
        res = S.apply(cfg, toks)
        ser = S.series_indexes(cfg)
        n, flagged = W.write_skill_workbook(out, cfg, res, ser, source_name=name)
        passed = sum(1 for c in res["checks"] if c["ok"])
        total = len(res["checks"])
        reconciled = total > 0 and passed == total
        S.record_run(cfg, reconciled=reconciled, note=f"{name}, {n} records")
        status = ("every printed total reconciles" if reconciled else
                  f"{total - passed} printed total(s) DO NOT reconcile")
        detail = "\n".join(
            f"   {'OK  ' if c['ok'] else 'FLAG'} {c['label']}: computed "
            f"{c['got']:,.2f} vs printed {c['expected']:,.2f}"
            for c in res["checks"])
        # The reasons themselves, not just a count. Given only a number, the
        # model speculated about what the flags meant -- "formatting, possible
        # OCR quirks" -- which is exactly the invention this design exists to
        # prevent. It can only report a reason it has been given.
        if res["flags"]:
            detail += f"\n\nCells needing a human ({len(res['flags'])}):"
            for f in res["flags"][:12]:
                detail += (f"\n   p{f['page']} {f['label'][:40]} -- "
                           f"{f['name']}: {f['detail']}")
        return ({"File": name, "Pages": found_on or (pages.strip() or "all"),
                 "Records": n, "Checks": f"{passed}/{total}",
                 "Flags": len(res["flags"]), "Skill": f"{cfg['slug']} ({score:.2f})",
                 "Status": status}, out, detail)

    # Nothing known. Read it from first principles and say what was found.
    allr, cols = [], None
    for pg in sorted({t["page"] for t in toks}):
        for b in T.read_tables(toks, page=pg):
            allr += b["rows"]
            cols = b["cols"]
    if not allr or not cols:
        return ({"File": name, "Records": 0, "Checks": "-", "Flags": "-",
                 "Skill": "none", "Status": "no table found"}, None, "")
    results = V.run_all(allr, cols)
    passed = sum(1 for r in results if r["ok"])
    n, flagged = W.write(out, allr, cols, results, source_name=name)
    reconciled = results and passed == len(results)
    status = ("no skill yet; read from scratch and its own totals reconcile"
              if reconciled else
              "no skill yet; read from scratch, some totals do not reconcile")
    if learn_new and reconciled:
        status += " - eligible to learn"
    return ({"File": name, "Records": n, "Checks": f"{passed}/{len(results)}",
             "Flags": flagged, "Skill": "none", "Status": status},
            out, V.report(results, show=8))


