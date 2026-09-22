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
from docagent import (OCR_PROMPT_PAGES, OUTPUT_DIR, _fmt_pages, _page_count,
                      _pdfs_under, _run_one, find_skill)

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
