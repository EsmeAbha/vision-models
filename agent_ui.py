"""Hand it a folder or a file, and it works out how to read it.

This is the experimental page. It runs on its own port and shares no state
with the pages on 7860-7864, so nothing here can disturb what already works.

The order is deliberate. SCAN first: it reports what is in the tree, which
documents it can read from their own text layer, which would need OCR, and
which it already knows how to handle from a previous job. Nothing starts until
you have seen those numbers. Only then RUN.

What it knows accumulates. Every report type handled successfully is written
to skills/ as a recipe plus a note, matched next time on what the document
prints about itself. A document that matches a skill is read with it; one that
matches nothing is read from first principles and, if its own printed totals
reconcile, offered as a new skill.

Accuracy governs the output. A figure is never shown as clean unless the
document's own arithmetic agrees with it, and anything doubtful arrives as a
coloured cell with the reason written beside it, on every sheet it appears on.

    ./.venv/Scripts/python agent_ui.py      # http://127.0.0.1:7870
"""
import os
import time
import traceback

import gradio as gr
import pandas as pd

from engine import geometry as G
from engine import skills as S
from engine import tabular as T
from engine import validate as V
from engine import writer as W

_here = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_here, "outputs")

# A document longer than this is never OCR'd without being asked about first.
# OCR on this machine means unloading the language model and bringing vLLM up
# in WSL, which is minutes of shared GPU, so it is a decision rather than a
# side effect of pressing a button.
OCR_PROMPT_PAGES = 10


def _serve_opts():
    host = "0.0.0.0" if os.environ.get("SERVE_LAN") else "127.0.0.1"
    user = os.environ.get("UI_USER")
    pw = os.environ.get("UI_PASS")
    auth = (user, pw) if user and pw else None
    return host, auth, False


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


def scan(root, progress=gr.Progress()):
    """Report what is here and what reading it would take. Starts nothing."""
    paths, err = _pdfs_under(root)
    if err:
        return err, pd.DataFrame(), gr.update(choices=[], value=[])

    rows, needs_ocr = [], []
    for i, p in enumerate(paths):
        progress((i + 1) / max(len(paths), 1), desc=os.path.basename(p)[:44])
        n = _page_count(p)
        try:
            ok, note = G.has_text_layer(p)
        except Exception as e:
            ok, note = False, f"unreadable: {type(e).__name__}: {e}"

        skill, score, where = None, 0.0, ""
        if ok:
            try:
                cfg, score, pages = find_skill(p, tokens=G.from_pdf(p))
                if cfg and pages:
                    skill = cfg["slug"]
                    where = _fmt_pages(pages)
            except Exception:
                pass

        if not ok:
            needs_ocr.append(p)
        rows.append({
            "File": os.path.basename(p),
            "Pages": n,
            "Readable": "yes" if ok else "NO",
            "Known skill": skill or ("-" if ok else ""),
            "Match": f"{score:.2f}" if ok and score else "",
            "Found on": where,
            "Note": note[:90],
            "_path": p,
        })

    df = pd.DataFrame(rows)
    readable = sum(1 for r in rows if r["Readable"] == "yes")
    known = sum(1 for r in rows if r["Known skill"] not in ("-", "", None))
    big = [p for p in needs_ocr if _page_count(p) > OCR_PROMPT_PAGES]

    msg = [f"{len(paths)} PDF(s): {readable} readable from their own text "
           f"layer, {len(needs_ocr)} would need OCR.",
           f"{known} already match a learned skill."]
    if needs_ocr:
        msg.append("")
        msg.append(f"OCR is OFF unless you tick it. {len(needs_ocr)} file(s) "
                   f"need it; {len(big)} of those are longer than "
                   f"{OCR_PROMPT_PAGES} pages and are listed below so the "
                   f"choice is yours.")
        for p in needs_ocr[:12]:
            msg.append(f"   - {os.path.basename(p)} ({_page_count(p)} pages)")
    return "\n".join(msg), df.drop(columns=["_path"]), gr.update(
        choices=[r["File"] for r in rows], value=[r["File"] for r in rows])


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


def run(root, chosen, pages, allow_ocr, learn_new, progress=gr.Progress()):
    paths, err = _pdfs_under(root)
    if err:
        return err, pd.DataFrame(), []
    if chosen:
        keep = set(chosen)
        paths = [p for p in paths if os.path.basename(p) in keep]
    if not paths:
        return "Nothing selected.", pd.DataFrame(), []

    t0 = time.time()
    rows, files, details = [], [], []
    for i, p in enumerate(paths):
        progress((i + 1) / len(paths), desc=os.path.basename(p)[:44])
        try:
            row, out, detail = _run_one(p, pages, learn_new)
        except Exception as e:
            rows.append({"File": os.path.basename(p), "Records": 0,
                         "Checks": "-", "Flags": "-", "Skill": "-",
                         "Status": f"{type(e).__name__}: {e}"})
            details.append(f"=== {os.path.basename(p)}\n"
                           + traceback.format_exc()[-800:])
            continue
        rows.append(row)
        if out:
            files.append(out)
        if detail:
            details.append(f"=== {os.path.basename(p)}\n{detail}")

    skipped = [r for r in rows if "needs OCR" in str(r["Status"])]
    msg = [f"{len(paths)} document(s) in {time.time() - t0:.1f}s.",
           f"{len(files)} workbook(s) written to outputs/."]
    if skipped and not allow_ocr:
        msg.append(f"{len(skipped)} skipped because they have no text layer "
                   f"and OCR was not enabled.")
    elif skipped and allow_ocr:
        msg.append(f"{len(skipped)} need OCR. The OCR stage is not wired into "
                   f"this page yet - it still runs from the OCR page on 7860, "
                   f"so these were left alone rather than half-done.")
    msg.append("")
    msg.append("\n".join(details[:6]))
    return "\n".join(msg), pd.DataFrame(rows), files


def skills_table():
    rows = []
    for cfg in S.load_all():
        runs = cfg.get("runs", {})
        rows.append({
            "Skill": cfg.get("slug", ""),
            "Handles": cfg.get("title", ""),
            "Columns": len(cfg.get("columns", [])),
            "Applied": runs.get("applied", 0),
            "Reconciled": runs.get("reconciled", 0),
            "Failed": runs.get("failed", 0),
            "Updated": cfg.get("updated", ""),
        })
    if not rows:
        return pd.DataFrame([{"Skill": "(none learned yet)"}])
    return pd.DataFrame(rows)


with gr.Blocks(title="Document agent (experimental)") as demo:
    gr.Markdown(
        "## Document agent — experimental\n"
        "Point it at a folder or a single PDF. It scans first and tells you "
        "what it can read, what would need OCR, and what it already knows how "
        "to handle. Nothing runs until you press Run.\n\n"
        "**Accuracy rule:** a figure is shown as clean only when the "
        "document's own printed totals agree with it. Anything doubtful comes "
        "back as a coloured cell with the reason written next to it."
    )

    with gr.Row():
        root_in = gr.Textbox(label="Folder or PDF path", scale=4,
                             placeholder=r"C:\...\reports   or   C:\...\report.pdf")
        pages_in = gr.Textbox(label="Pages (optional)", scale=1,
                              placeholder="38-41")
    with gr.Row():
        scan_btn = gr.Button("1. Scan", variant="secondary")
        run_btn = gr.Button("2. Run", variant="primary")
    with gr.Row():
        ocr_in = gr.Checkbox(False, label="Allow OCR on documents with no text "
                                          "layer (off by default)")
        learn_in = gr.Checkbox(True, label="Offer to learn a new skill when a "
                                           "document reconciles")

    status_out = gr.Textbox(label="What happened", lines=12)
    files_out = gr.Files(label="Workbooks")
    pick_in = gr.CheckboxGroup(label="Documents to run", choices=[])
    table_out = gr.Dataframe(label="Results", wrap=True)

    with gr.Accordion("What it has learned so far", open=False):
        skills_out = gr.Dataframe(value=skills_table, label="Skills")
        refresh_btn = gr.Button("Refresh")

    scan_btn.click(scan, inputs=[root_in],
                   outputs=[status_out, table_out, pick_in])
    run_btn.click(run, inputs=[root_in, pick_in, pages_in, ocr_in, learn_in],
                  outputs=[status_out, table_out, files_out])
    refresh_btn.click(lambda: skills_table(), outputs=[skills_out])

if __name__ == "__main__":
    _host, _auth, _share = _serve_opts()
    demo.queue().launch(server_name=_host, server_port=7870,
                        inbrowser=False, auth=_auth, share=_share)
