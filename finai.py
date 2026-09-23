"""The chat page: say what you want, a local model works out how to do it.

Served at /finai on port 7870. The root path lists the existing pages instead
of replacing them, and nothing here shares state with 7860-7864.

There is no fixed workflow on this page. You type, and the model decides what
to look at and which tools to run -- list a folder, inspect a document, pull
it into Excel, read a spreadsheet back, ask what it has learned. A rent roll
is not special; it is only the first report type it happens to know.

Its working is shown as it goes, because a figure you cannot trace is a figure
you cannot use. Every tool call and every result appears above the answer.

    ./.venv/Scripts/python finai.py      # http://127.0.0.1:7870/finai
"""
import base64
import html
import os
import shutil
import time

import gradio as gr
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import finai_agent as FA
import ollama_proxy

_here = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(_here, "uploads", "finai")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

MODELS = ["gpt-oss-64k:latest", "gpt-oss-128k:latest", "gemma4-32k:latest",
          "qwen2.5:14b-instruct-16k"]


def _stash(path):
    """Keep an uploaded file where the tools can reach it by a stable path."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dest = os.path.join(UPLOAD_DIR, os.path.basename(path))
    try:
        if os.path.abspath(path) != os.path.abspath(dest):
            shutil.copy2(path, dest)
    except OSError:
        return path
    return dest


def _paths_in_history(history):
    """Files attached earlier in this conversation.

    Gradio keeps an attachment in the history as its path, in one of several
    shapes depending on how the turn was built. Without this, the second
    question about a file got no path at all and the model asked for it again
    -- which reads as the agent forgetting what it was just handed.
    """
    found = []

    def walk(v):
        if isinstance(v, str):
            if os.path.sep in v and os.path.isfile(v):
                found.append(v)
        elif isinstance(v, dict):
            for key in ("path", "file", "url", "name"):
                if isinstance(v.get(key), str):
                    walk(v[key])
            if "content" in v:
                walk(v["content"])
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)

    for turn in history or []:
        walk(turn)
    return list(dict.fromkeys(found))


def respond(message, history, folder, model):
    """One turn. Yields as the agent works so its reasoning is visible."""
    text = (message or {}).get("text", "") if isinstance(message, dict) else str(message)
    files = (message or {}).get("files", []) if isinstance(message, dict) else []

    images, docs, folders, notes = [], [], [], []
    for f in files or []:
        p = _stash(f)
        ext = os.path.splitext(p)[1].lower()
        if ext in IMAGE_EXT:
            with open(p, "rb") as fh:
                images.append(base64.b64encode(fh.read()).decode())
        elif ext == ".zip":
            # Unpacked here rather than left for the model to puzzle over: a
            # zip of per-unit folders is the usual way this work arrives, and
            # the agent needs a folder path, not an archive.
            try:
                import zips
                root, note = zips.extract(p)
                folders.append(root)
                notes.append(f"{os.path.basename(p)}: {note}")
            except Exception as e:
                notes.append(f"{os.path.basename(p)} could not be unpacked: "
                             f"{type(e).__name__}: {e}")
        else:
            docs.append(p)
            if ext in (".xlsx", ".xlsm"):
                # A workbook with Tenant/Suite headings is a template to fill,
                # and is kept so it need not be attached again next time.
                try:
                    import unit_extract as _U
                    _U.remember_template(p)
                except Exception:
                    pass

    prompt = text.strip()
    if folder and folder.strip():
        prompt += f"\n\n[The user is working in this folder: {folder.strip()}]"
    if docs:
        prompt += "\n\n[Files the user attached, already on disk:]\n" + \
                  "\n".join(f"- {d}" for d in docs)
    if folders:
        prompt += ("\n\n[Folders unpacked from what the user attached. Each "
                   "immediate sub-folder is one unit:]\n"
                   + "\n".join(f"- {d}" for d in folders))
    if notes:
        prompt += "\n\n[" + "; ".join(notes) + "]"
    earlier = [p for p in _paths_in_history(history) if p not in docs]
    if earlier:
        prompt += "\n\n[Files from earlier in this conversation:]\n" + \
                  "\n".join(f"- {d}" for d in earlier[-5:])
    if images and not prompt:
        prompt = "Describe what this image shows and what you would do with it."
    if not prompt:
        yield "Tell me what you want done."
        return

    # Gradio hands history back as message dicts; the model wants the same
    # shape minus anything it cannot read.
    past = []
    for h in (history or [])[-8:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
            content = h.get("content")
            if isinstance(content, str) and content.strip():
                past.append({"role": h["role"], "content": content})

    lines = []
    t0 = time.time()

    def on_event(kind, payload):
        if kind == "tool":
            lines.append(f"- **{payload}**")
        elif kind == "result":
            lines.append(f"  <sub>{html.escape(payload[:300])}</sub>")

    # The agent is synchronous; show a holding line first so the page does not
    # look dead while a 20B model thinks.
    yield "*working…*"

    answer, steps = FA.run_agent(prompt, history=past, model=model,
                                 images=images or None, on_event=on_event)

    work = ""
    if lines:
        work = ("<details><summary>what it did ("
                f"{len(steps)} tool call(s), {time.time() - t0:.0f}s)</summary>\n\n"
                + "\n".join(lines) + "\n\n</details>\n\n")

    books = [s["result"].get("workbook") for s in steps
             if isinstance(s.get("result"), dict) and s["result"].get("workbook")]
    if books:
        answer += "\n\n**Workbook written:**\n" + "\n".join(
            f"`{b}`" for b in dict.fromkeys(books))
    yield work + answer


with gr.Blocks(title="FinAI", fill_height=True) as demo:
    gr.Markdown(
        "### FinAI — local document agent\n"
        "Ask for what you want. The model looks at what is on disk, decides "
        "what to run, and reuses what it has learned about a report type when "
        "it recognises one. Everything stays on this machine.\n\n"
        "*It reads scanned pages by itself with the local vision model, so "
        "there is nothing to switch on. A figure is reported as clean only "
        "when the document's own printed totals agree with it; anything "
        "doubtful comes back flagged, with the reason.*"
    )
    with gr.Row():
        folder_in = gr.Textbox(label="Working folder (optional)", scale=4,
                               placeholder=r"C:\...\reports")
        model_in = gr.Dropdown(MODELS, value=MODELS[0], label="Local model",
                               scale=2)

    gr.ChatInterface(
        respond,
        # Gradio 6 dropped the `type` argument: message dicts are the only
        # history format now, which is the shape `respond` already expects.
        multimodal=True,
        additional_inputs=[folder_in, model_in],
        textbox=gr.MultimodalTextbox(
            placeholder="e.g. what is in this folder?  /  pull the rent roll "
                        "out of that report into Excel  /  what have you "
                        "learned so far?",
            file_count="multiple",
            file_types=None),
        examples=[
            [{"text": "What does this document say?", "files": []}],
            [{"text": "Find the rent roll in that report and put it in Excel. "
                      "Tell me what needs checking.", "files": []}],
            [{"text": "What report types have you learned, and how often has "
                      "each one reconciled?", "files": []}],
        ],
    )

app = FastAPI(title="FinAI")

# The local models, reachable over the network behind the same password.
app.include_router(ollama_proxy.router)


@app.get("/", response_class=HTMLResponse)
def index():
    return """<!doctype html><meta charset="utf-8">
<title>Document tools</title>
<style>
 body{font:15px/1.6 system-ui,sans-serif;margin:3rem auto;max-width:44rem;color:#222}
 a{color:#0b5;text-decoration:none} a:hover{text-decoration:underline}
 li{margin:.4rem 0} .new{font-weight:600}
 code{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}
</style>
<h2>Document tools</h2>
<p class=new><a href="/finai">FinAI &mdash; chat with the local agent</a>
 &nbsp;<small>new, experimental</small></p>
<p>The existing pages run as separate services on their own ports:</p>
<ul>
 <li><a href="http://127.0.0.1:7860">7860</a> &mdash; OCR (PaddleOCR-VL via vLLM)</li>
 <li><a href="http://127.0.0.1:7861">7861</a> &mdash; page &rarr; rows extraction</li>
 <li><a href="http://127.0.0.1:7862">7862</a> &mdash; appraisal fields</li>
 <li><a href="http://127.0.0.1:7863">7863</a> &mdash; folder / loan extraction</li>
 <li><a href="http://127.0.0.1:7864">7864</a> &mdash; lease abstraction</li>
</ul>
<h3>Local model API</h3>
<p>The Ollama API is proxied at <code>/ollama</code>, behind the same
 username and password as the chat page. Ollama itself stays bound to
 localhost.</p>
<ul>
 <li><code>POST /ollama/api/chat</code> &mdash; native Ollama</li>
 <li><code>POST /ollama/v1/chat/completions</code> &mdash; OpenAI-compatible</li>
 <li><a href="/ollama/api/tags"><code>GET /ollama/api/tags</code></a> &mdash; list models</li>
</ul>
<p><small>Start one with
 <code>./.venv/Scripts/python folder_ui.py</code> and so on. Nothing on this
 page shares state with them.</small></p>
"""


def _serve_opts():
    """Where to listen, and the password that is required to leave localhost.

    The same VM_HOST / VM_USER / VM_PASS convention as the other pages, with
    one difference: here a password is REQUIRED rather than advised.

    Those pages accept an upload and hand back a result. This one hands a
    chat model tools that read any path on this machine -- list a folder,
    open a spreadsheet, extract a document. On an open port that is not a
    file-upload form, it is a way for anyone who can reach it to read the
    disk by asking. So binding beyond localhost without credentials is
    refused rather than warned about.
    """
    host = os.environ.get("VM_HOST", "127.0.0.1")
    user, password = os.environ.get("VM_USER"), os.environ.get("VM_PASS")
    auth = (user, password) if user and password else None
    if host != "127.0.0.1" and not auth:
        raise SystemExit(
            "REFUSING to listen on %s with no password.\n"
            "This page's tools can read any file on this machine, so it is\n"
            "not safe to expose unauthenticated. Set VM_USER and VM_PASS, or\n"
            "start it with serve_finai.ps1 -Password \"something-long\"."
            % host)
    return host, auth


HOST, AUTH = _serve_opts()
app = gr.mount_gradio_app(app, demo, path="/finai", auth=AUTH)

if __name__ == "__main__":
    import uvicorn
    where = "127.0.0.1" if HOST == "127.0.0.1" else HOST
    print(f"FinAI on http://{where}:7870/finai"
          + ("  (password required)" if AUTH else "  (localhost only)"),
          flush=True)
    uvicorn.run(app, host=HOST, port=7870, log_level="warning")
