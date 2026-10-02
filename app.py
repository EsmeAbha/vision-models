import os
import gc
import html
import json
import itertools
import collections
import shlex
import time
import atexit
import shutil
import tempfile
import threading
import subprocess
import urllib.request

_here = os.path.dirname(os.path.abspath(__file__))
_cert_bundle = os.path.join(_here, "combined_cacert.pem")
if os.path.exists(_cert_bundle):
    os.environ.setdefault("SSL_CERT_FILE", _cert_bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _cert_bundle)
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import torch
import gradio as gr

import field_search
import pdf_pages
import save_output

# PaddleOCR-VL cannot run on Windows here: Application Control blocks
# libpaddle's DLL. It runs in WSL instead, against the vLLM server that also
# lives there, so its worker is launched through wsl.exe and every path handed
# to it is translated to /mnt/<drive>/... form.
WSL_PADDLE_PYTHON = "/home/esme_abha/ocr/.venv-paddle/bin/python"
WSL_VLLM_START = "/home/esme_abha/ocr/start_server.sh"
VLLM_URL = "http://localhost:8000/v1"
# Desktop apps (Chrome/Teams/explorer) sit around 4.5GB; anything much above
# that means a model is still resident.
VRAM_IDLE_CEILING_MB = 6000
OUTPUT_DIR = os.path.join(_here, "outputs")

OCR_WORKERS = {
    "deepseek_ocr": {
        "cmd": [
            os.path.join(_here, ".venv-deepseek-ocr", "Scripts", "python.exe"),
            os.path.join(_here, "deepseek_ocr_worker.py"),
            "--serve",
        ],
        "wsl": False,
    },
    "paddleocr_vl": {
        # CUDA_VISIBLE_DEVICES='' keeps paddle off the card, and it has to.
        # vLLM already holds a CUDA context there with pinned memory (WSL2
        # needs that for UVA, and the server will not start without it).
        # Paddle opening a SECOND context for PP-DocLayoutV3 takes the whole
        # WSL session down: the worker dies with no traceback and no
        # faulthandler dump, vLLM dies in the same instant, and the only
        # symptom upstream is "OCR worker exited unexpectedly".
        #
        # So layout detection runs on CPU. Recognition was always the vLLM
        # server's job, so only the layout pass pays for it; output is
        # byte-identical either way.
        "cmd": [
            "wsl.exe", "-e", "bash", "-lc",
            "CUDA_VISIBLE_DEVICES='' " + shlex.quote(WSL_PADDLE_PYTHON)
            + " -u " + shlex.quote(
                pdf_pages.win_to_wsl(os.path.join(_here, "paddleocr_worker.py")))
            + " --serve",
        ],
        "wsl": True,
    },
}

MODELS = {
    "PaddleOCR-VL (document OCR)": "paddleocr_vl",
    "DeepSeek-OCR (document OCR)": "deepseek_ocr",
}

DEFAULT_PROMPTS = {
    "deepseek_ocr": "<image>\n<|grounding|>Convert the document to markdown. ",
    "paddleocr_vl": "(no prompt needed - fixed layout+recognition pipeline)",
}

_lock = threading.Lock()
_state = {"kind": None, "model": None, "processor": None, "ocr_proc": None,
          "vllm_proc": None}


def _vllm_up(timeout=3):
    try:
        with urllib.request.urlopen(VLLM_URL + "/models", timeout=timeout):
            return True
    except Exception:
        return False


def start_vllm(log=print):
    """Bring up the PaddleOCR-VL vLLM server in WSL (idempotent).

    Held as a child process so the WSL distro stays alive; nohup'ing it inside
    WSL is not enough, the distro is torn down when the launching wsl.exe exits.
    """
    if _vllm_up():
        log("vLLM server already running")
        return
    log("starting vLLM server in WSL (first start loads the model, ~40s)...")
    _state["vllm_proc"] = subprocess.Popen(
        ["wsl.exe", "-e", "bash", "-lc", WSL_VLLM_START],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for i in range(120):
        if _vllm_up():
            log("vLLM server ready")
            return
        time.sleep(1)
    raise RuntimeError("vLLM server did not come up within 120s")


def gpu_used_mb():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1


def stop_vllm(log=print):
    """Stop the server and WAIT for its VRAM to come back.

    This must block. The card is 16GB; vLLM holds ~5.5GB and Qwen3-VL's 4-bit
    load needs ~6GB on top of ~4.3GB of desktop apps. Starting the load while
    the server is still shutting down segfaults the process partway through
    loading weights, so returning early here crashes the app.
    """
    proc = _state.get("vllm_proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    _state["vllm_proc"] = None
    subprocess.run(["wsl.exe", "-e", "bash", "-lc", "pkill -f 'vllm serve'"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not _vllm_up(timeout=2) and gpu_used_mb() < 0:
        return
    before = gpu_used_mb()
    for _ in range(60):
        if not _vllm_up(timeout=2) and gpu_used_mb() <= VRAM_IDLE_CEILING_MB:
            break
        time.sleep(1)
    else:
        log(f"warning: VRAM still at {gpu_used_mb()}MB after stopping vLLM")
        return
    time.sleep(2)  # let the driver actually reclaim the pages
    log(f"vLLM stopped, VRAM {before}MB -> {gpu_used_mb()}MB")


atexit.register(stop_vllm)


def _stop_ocr_worker():
    proc = _state.get("ocr_proc")
    if proc is not None and proc.poll() is None:
        proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    _state["ocr_proc"] = None


def unload_current():
    if _state["model"] is not None:
        del _state["model"]
    _stop_ocr_worker()
    _state["kind"] = None
    _state["model"] = None
    _state["processor"] = None
    gc.collect()
    torch.cuda.empty_cache()


def load_ocr_worker(kind):
    spec = OCR_WORKERS[kind]
    if spec["wsl"]:
        start_vllm()
    proc = subprocess.Popen(
        spec["cmd"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=_here,
    )
    # PaddleOCR is extremely chatty on stderr. Nothing was reading this pipe,
    # so once the OS buffer (~64KB) filled, the worker blocked forever on write
    # and the whole request hung. Drain it continuously into a ring buffer.
    tail = collections.deque(maxlen=400)

    def _drain(stream, sink):
        try:
            for line in stream:
                sink.append(line.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=_drain, args=(proc.stderr, tail), daemon=True).start()
    _state.update(kind=kind, ocr_proc=proc, stderr_tail=tail)
    # Warm the model now so the first real request isn't slower than the rest.
    warmup_prompt = DEFAULT_PROMPTS[kind]
    _ocr_request(_here + "\\test_document.png", warmup_prompt, warmup=True)


def ensure_model(kind, progress=None):
    proc = _state.get("ocr_proc")
    if _state["kind"] == kind and proc is not None and proc.poll() is None:
        # The worker is up, but the server it talks to may not be: it lives in
        # WSL and can be stopped independently of this process.
        if OCR_WORKERS[kind]["wsl"] and not _vllm_up():
            start_vllm()
        return
    if _state["kind"] == kind:
        # Worker died (crash, or killed outside this process) -- rebuild it
        # instead of failing every request from here on.
        print("OCR worker is gone, restarting it", flush=True)
        _state["kind"] = None
    if progress:
        progress(0, desc=f"Loading {kind} (first switch loads weights, ~10-20s)...")
    unload_current()
    if kind in OCR_WORKERS:
        load_ocr_worker(kind)
    else:
        raise ValueError(f"unknown model kind: {kind}")


def _ocr_request(image_path, prompt, warmup=False):
    proc = _state["ocr_proc"]
    if proc is None or proc.poll() is not None:
        raise RuntimeError("OCR worker process is not running")
    out_dir = tempfile.mkdtemp(prefix="ocr_")
    send_img, send_out = image_path, out_dir
    if OCR_WORKERS[_state["kind"]]["wsl"]:
        send_img = pdf_pages.win_to_wsl(image_path)
        send_out = pdf_pages.win_to_wsl(out_dir)
    req = json.dumps({"image_path": send_img, "prompt": prompt, "out_dir": send_out})
    proc.stdin.write(req + "\n")
    proc.stdin.flush()
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("###RESULT_JSON###"):
            resp = json.loads(line[len("###RESULT_JSON###"):])
            if not resp["ok"]:
                raise RuntimeError(resp["error"])
            if warmup:
                return None, None
            return resp["text"], resp["image"]
    tail = "\n".join(_state.get("stderr_tail") or [])[-2000:]
    raise RuntimeError(f"OCR worker exited unexpectedly.\n{tail}")


def run_deepseek_ocr(image_path, prompt):
    if "<image>" not in prompt:
        prompt = "<image>\n" + prompt
    return _ocr_request(image_path, prompt)


def run_paddleocr_vl(image_path, prompt):
    return _ocr_request(image_path, prompt)


def _as_paths(file_obj):
    """Normalise the file input to a list of paths.

    gr.File hands back a single object or a list depending on file_count, and
    the object is sometimes a tempfile wrapper rather than a plain path.
    """
    if not file_obj:
        return []
    items = file_obj if isinstance(file_obj, (list, tuple)) else [file_obj]
    out = []
    for it in items:
        p = getattr(it, "name", it)
        if p:
            out.append(str(p))
    return out


def _ocr_request_batch(image_paths):
    """Recognise several pages in a single worker call (PaddleOCR-VL only)."""
    proc = _state["ocr_proc"]
    if proc is None or proc.poll() is not None:
        raise RuntimeError("OCR worker process is not running")
    out_dir = tempfile.mkdtemp(prefix="ocr_")
    paths, send_out = list(image_paths), out_dir
    if OCR_WORKERS[_state["kind"]]["wsl"]:
        paths = [pdf_pages.win_to_wsl(p) for p in paths]
        send_out = pdf_pages.win_to_wsl(out_dir)
    proc.stdin.write(json.dumps({"image_paths": paths, "out_dir": send_out}) + "\n")
    proc.stdin.flush()
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("###RESULT_JSON###"):
            resp = json.loads(line[len("###RESULT_JSON###"):])
            if not resp["ok"]:
                raise RuntimeError(resp["error"])
            return resp["texts"]
    tail = "\n".join(_state.get("stderr_tail") or [])[-2000:]
    raise RuntimeError(f"OCR worker exited unexpectedly.\n{tail}")


def _run_one(kind, image_path, prompt):
    if kind == "deepseek_ocr":
        return run_deepseek_ocr(image_path, prompt)
    if kind == "paddleocr_vl":
        return run_paddleocr_vl(image_path, prompt)
    raise ValueError(kind)


def run(model_label, image_path, pdf_file, prompt, pages_spec, dpi, batch=4,
        progress=gr.Progress()):
    """Streaming handler. Yields (status, text, image, html) as pages finish."""
    kind = MODELS[model_label]
    pdf_paths = _as_paths(pdf_file)
    show_html = gr.update(visible=(kind == "paddleocr_vl"))

    if not pdf_paths and image_path is None:
        yield "Upload an image or one or more PDFs first.", "", None, show_html
        return

    log = []

    def status(msg):
        log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        return "\n".join(log)

    with _lock:
        try:
            # ---------------------------------------------------- single image
            if not pdf_paths:
                yield status(f"loading {kind}..."), "", None, show_html
                ensure_model(kind, progress)
                yield status("running inference..."), "", None, show_html
                t = time.time()
                text, img = _run_one(kind, image_path, prompt)
                st = status(f"done in {time.time() - t:.1f}s, {len(text)} chars")
                html = text if kind == "paddleocr_vl" else ""
                yield st, text, img, gr.update(value=html,
                                               visible=(kind == "paddleocr_vl"))
                return

            # --------------------------------------------- one or more PDFs
            plan, grand = [], 0
            for p in pdf_paths:
                try:
                    n_total = pdf_pages.page_count(p)
                    n_todo = len(pdf_pages.parse_pages(pages_spec, n_total))
                except Exception as e:
                    yield (status(f"{os.path.basename(p)}: unreadable "
                                  f"({type(e).__name__}: {e}) - skipping"),
                           "", None, show_html)
                    continue
                plan.append((p, n_total, n_todo))
                grand += n_todo
            if not plan:
                yield status("no readable PDFs"), "", None, show_html
                return

            multi = len(plan) > 1
            summary = ", ".join(f"{os.path.basename(p)} ({n}/{t}p)"
                                for p, t, n in plan)
            yield (status(f"{len(plan)} PDF(s), {grand} page(s) total at "
                          f"{int(dpi)} dpi: {summary}"), "", None, show_html)

            yield status(f"loading {kind}..."), "", None, show_html
            ensure_model(kind, progress)

            work = tempfile.mkdtemp(prefix="pdfpages_")
            t_all = time.time()

            def iter_pages():
                """Render lazily, so only a batch of PNGs exists at a time."""
                for idx, (path, _t, _n) in enumerate(plan):
                    name = os.path.basename(path)
                    sub = os.path.join(work, f"{idx:03d}")
                    for pno, png, info in pdf_pages.render_pdf(
                            path, sub, dpi=int(dpi), pages=pages_spec):
                        yield name, pno, png, info

            # PaddleOCR-VL fans a whole batch of pages out to vLLM at once;
            # DeepSeek-OCR has no batch path, so it stays one page at a time.
            step = int(batch) if kind == "paddleocr_vl" else 1
            pages = iter_pages()
            parts, last_img, done, failed = [], None, 0, 0

            while True:
                chunk = list(itertools.islice(pages, step))
                if not chunk:
                    break
                first, last = chunk[0], chunk[-1]
                if len(chunk) == 1:
                    label = f"{first[0]} p{first[1]}"
                elif first[0] == last[0]:
                    label = f"{first[0]} p{first[1]}-{last[1]}"
                else:
                    label = f"{first[0]} p{first[1]} .. {last[0]} p{last[1]}"

                yield (status(f"{label}: recognising ({len(chunk)} page(s), "
                              f"{done}/{grand} done)"),
                       "\n\n".join(parts), last_img, show_html)
                t = time.time()
                try:
                    if step > 1:
                        texts = _ocr_request_batch([c[2] for c in chunk])
                    else:
                        text, img = _run_one(kind, chunk[0][2], prompt)
                        texts, last_img = [text], img or last_img
                    err = None
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    texts = [f"ERROR on {label}: {err}"] * len(chunk)
                    failed += len(chunk)
                el = time.time() - t

                for (name, pno, png, _info), text in zip(chunk, texts):
                    marker = save_output.page_marker(name if multi else "", pno)
                    parts.append(f"{marker}\n{text}")
                    try:
                        os.remove(png)      # a long batch would fill the disk
                    except OSError:
                        pass
                done += len(chunk)
                progress(done / max(grand, 1), desc=label)
                joined = "\n\n".join(parts)
                html = joined if kind == "paddleocr_vl" else ""
                note = f"FAILED after {el:.1f}s -- {err}" if err else (
                    f"done in {el:.1f}s ({el / len(chunk):.1f}s/page), "
                    f"{sum(len(x) for x in texts)} chars")
                yield (status(f"{label}: {note}"), joined, last_img,
                       gr.update(value=html, visible=(kind == "paddleocr_vl")))

            joined = "\n\n".join(parts)
            html = joined if kind == "paddleocr_vl" else ""
            el = time.time() - t_all
            tail = f", {failed} page(s) FAILED" if failed else ""
            st = status(f"ALL DONE: {len(parts)} page(s) from {len(plan)} PDF(s) "
                        f"in {el:.1f}s ({el / max(len(parts), 1):.1f}s/page), "
                        f"{len(joined)} chars{tail}")
            shutil.rmtree(work, ignore_errors=True)
            yield st, joined, last_img, gr.update(value=html,
                                                  visible=(kind == "paddleocr_vl"))
        except Exception as e:
            yield status(f"ERROR: {type(e).__name__}: {e}"), "", None, show_html


def save_result(text, pdf_file, image_path):
    """Write the current result to .md / .html / .xlsx and offer them for download."""
    if not text or not str(text).strip():
        return gr.update(value=None, visible=False), "Nothing to save yet - run something first."
    paths = _as_paths(pdf_file)
    if len(paths) > 1:
        src = f"{os.path.splitext(os.path.basename(paths[0]))[0]}_and_{len(paths) - 1}_more"
    else:
        src = (paths[0] if paths else None) or image_path or "output"
    try:
        files = save_output.save_all(str(text), base_name=src, out_dir=OUTPUT_DIR)
    except Exception as e:
        return gr.update(value=None, visible=False), f"Save failed: {type(e).__name__}: {e}"
    names = "\n".join("  " + f for f in files)
    return gr.update(value=files, visible=True), f"Saved {len(files)} file(s):\n{names}"


def on_model_change(label):
    kind = MODELS[label]
    return (
        DEFAULT_PROMPTS[kind],
        gr.update(visible=(kind != "paddleocr_vl")),
        gr.update(visible=(kind == "paddleocr_vl"), value=""),
    )


def _serve_opts():
    """Where to listen, and whether to demand a password.

    Defaults to localhost: these pages have no auth of their own and accept
    file uploads, so binding wider has to be a deliberate act. Set VM_HOST to
    0.0.0.0 to reach them from another machine, and set VM_USER/VM_PASS unless
    the network is one you fully trust.
    """
    host = os.environ.get("VM_HOST", "127.0.0.1")
    user, password = os.environ.get("VM_USER"), os.environ.get("VM_PASS")
    auth = (user, password) if user and password else None
    share = os.environ.get("VM_SHARE", "").lower() in ("1", "true", "yes")
    if host != "127.0.0.1" and not auth:
        print("WARNING: listening on %s with no VM_USER/VM_PASS set -- anyone "
              "who can reach this port can upload files and read results"
              % host, flush=True)
    if share:
        # A share link is a tunnel through Gradio's relay, so uploads and
        # results leave this machine even though the model does not. Worth
        # saying out loud in a project whose point is staying local.
        print("NOTE: VM_SHARE is on -- a public gradio.live URL will be created "
              "and traffic will pass through Gradio's servers", flush=True)
        if not auth:
            print("REFUSING to open a public link with no password; set "
                  "VM_USER and VM_PASS", flush=True)
            share = False
    return host, auth, share


# ---------------------------------------------------------------- appearance
#
# The look is borrowed from the document workspace so the two read as one
# product: an off-white page, white panels, a deep green for anything
# actionable. Appearance only -- no component, handler or default below this
# line changes, and build_demo() is untouched because other modules import it.
#
# The typeface goes through the theme, not through the stylesheet below.
# Gradio writes font-family: var(--font) onto its elements individually, so a
# rule aimed at the container is simply not inherited by them -- which is why
# the colours took and the type did not. Setting the variable moves all of it.
#
# The face is named, not fetched. gr.themes.Font is the plain kind: it yields
# a family name and no @font-face at all, unlike GoogleFont (which downloads)
# and LocalFont (which points at one of the sixteen faces Gradio ships, none
# of them this one). So the browser resolves these from what is already
# installed, and nothing leaves the machine.
_FONT = [gr.themes.Font("Inter"), gr.themes.Font("Segoe UI"),
         gr.themes.Font("Arial"), gr.themes.Font("sans-serif")]
_FONT_MONO = [gr.themes.Font("Consolas"), gr.themes.Font("ui-monospace"),
              gr.themes.Font("monospace")]

_GREEN = gr.themes.Color(
    c50="#f1f6f3", c100="#dee9e4", c200="#bdd3ca", c300="#93b6a8",
    c400="#5d8b7a", c500="#245c4d", c600="#1f5144", c700="#1a4238",
    c800="#163b35", c900="#12312c", c950="#0d2420",
)
_STONE = gr.themes.Color(
    c50="#fafbf8", c100="#f5f6f1", c200="#e2e7df", c300="#ccd4c8",
    c400="#a3ae9e", c500="#76817b", c600="#5d6862", c700="#47514c",
    c800="#333b37", c900="#252d29", c950="#161b18",
)

THEME = gr.themes.Base(
    primary_hue=_GREEN,
    secondary_hue=_GREEN,
    neutral_hue=_STONE,
    font=_FONT,
    font_mono=_FONT_MONO,
).set(
    body_background_fill="#f5f6f1",
    body_text_color="#1b2420",
    body_text_color_subdued="#57615c",
    block_background_fill="#ffffff",
    block_border_color="#e2e7df",
    block_label_text_color="#3f4a45",
    block_title_text_color="#1b2420",
    border_color_primary="#e2e7df",
    panel_background_fill="#ffffff",
    input_background_fill="#ffffff",
    input_border_color="#e2e7df",
    button_primary_background_fill="#245c4d",
    button_primary_background_fill_hover="#1a4238",
    button_primary_text_color="#ffffff",
    button_secondary_background_fill="#ffffff",
    button_secondary_text_color="#245c4d",
    color_accent="#245c4d",
    link_text_color="#245c4d",
    slider_color="#245c4d",
    # Every one of the above again, for dark mode. Setting only the light
    # values looked like nothing had changed at all to anyone whose browser
    # prefers dark: Gradio keeps its own dark block, which ignores them and
    # falls back to the far ends of the ramps -- a near-black page with
    # faintly green buttons. The workspace this borrows from has one palette
    # and no dark variant, so the page holds that palette either way rather
    # than inventing a dark scheme nothing was designed against.
    body_background_fill_dark="#f5f6f1",
    body_text_color_dark="#1b2420",
    body_text_color_subdued_dark="#57615c",
    block_background_fill_dark="#ffffff",
    block_border_color_dark="#e2e7df",
    block_label_text_color_dark="#3f4a45",
    block_title_text_color_dark="#1b2420",
    border_color_primary_dark="#e2e7df",
    panel_background_fill_dark="#ffffff",
    input_background_fill_dark="#ffffff",
    input_border_color_dark="#e2e7df",
    button_primary_background_fill_dark="#245c4d",
    button_primary_background_fill_hover_dark="#1a4238",
    button_primary_text_color_dark="#ffffff",
    button_secondary_background_fill_dark="#ffffff",
    button_secondary_text_color_dark="#245c4d",
    link_text_color_dark="#245c4d",
)

# The type scale, which the theme's single font setting cannot express. The
# workspace runs two faces, not one: a serif for headings over a sans UI, and
# that pairing is most of what makes it look the way it does. Setting only the
# sans left the headings in the right family and the wrong voice.
#
# Georgia and Segoe UI are both stock on this machine, so nothing is fetched.
CSS = """
/* No cap: the sidebar already takes the left, and a fixed 1500px left a
   400px dead strip on the right of a 1900px window. A wide ceiling keeps
   line lengths sane on an ultra-wide display without wasting an ordinary
   one. */
.gradio-container{max-width:2100px!important;font-synthesis:none;font-size:14px;
  padding-right:26px!important}
.gradio-container h1{font-family:Georgia,'Times New Roman',serif!important;
  font-size:36px;font-weight:400;letter-spacing:-1.3px;color:#1b2420;
  margin:9px 0 10px}
.gradio-container h2{font-family:Georgia,'Times New Roman',serif!important;
  font-size:25px;font-weight:400;letter-spacing:-.6px;color:#1b2420}
.gradio-container h3{font-size:15px;font-weight:600;color:#1b2420}
.gradio-container .prose p,.gradio-container .prose li{font-size:13px;
  color:#57615c;line-height:1.7}
.gradio-container .prose strong{color:#1b2420;font-weight:600}
.vm-page .block .label-wrap span,.vm-page label>span,.vm-page label span{
  font-size:12px!important;font-weight:600!important;letter-spacing:0!important;
  color:#3f4a45!important;text-transform:none!important}
.gradio-container input[type=text],.gradio-container input[type=number],
.gradio-container select,.gradio-container textarea{font-size:13px;color:#1b2420}
button.primary,button.secondary{font-size:12px;font-weight:600;letter-spacing:.2px}
footer{display:none!important}

/* The borders. Gradio draws panels flat by default, so the page read as one
   undivided sheet; the workspace gives every card an outline and it is what
   separates one step from the next. */
/* Drop targets are dashed, like the workspace's upload areas. */
.gradio-container .upload-container,.gradio-container .wrap.svelte-.dropzone,
.gradio-container [data-testid=block-label]+div .wrap{background:#f9fbf6}
.gradio-container .image-container,.gradio-container .file-preview,
.gradio-container .upload-container{border-radius:7px}

/* Dark mode, settled once instead of token by token.
   Gradio's dark block has 242 declarations; pinning them individually left a
   dozen still pointing at the dark end of the ramp -- which is how a dark
   label chip ended up carrying dark text. Every one of those points at a
   neutral step, so inverting the ramp itself inside .dark turns all of them
   light in one move, and the page keeps the single palette it was designed
   with whichever mode the browser is in. */
:root.dark,:root .dark,.dark{
  --neutral-950:#fafbf8;--neutral-900:#f5f6f1;--neutral-800:#ffffff;
  --neutral-700:#e2e7df;--neutral-600:#ccd4c8;--neutral-500:#a3ae9e;
  --neutral-400:#76817b;--neutral-300:#5d6862;--neutral-200:#47514c;
  --neutral-100:#1b2420;--neutral-50:#161b18;
  --block-label-background-fill:#f1f4ef;
  --block-label-text-color:#3f4a45;
}
.gradio-container [data-testid=block-label],.gradio-container .block-label{
  background:#f1f4ef!important;color:#3f4a45!important;
  border-color:#e2e7df!important}

/* Sidebar, borrowed from the workspace: dark panel, lime mark, quiet nav. */
.vm-side{background:#163b35!important;border:0!important;padding:26px 16px 18px!important}
.vm-side *{color:#c9dbd1}
.vm-brand{display:flex;align-items:center;gap:10px;padding:0 8px;
  font-size:23px;font-weight:650;color:#f4f8ef;letter-spacing:-1px;line-height:1.1}
.vm-brand small{display:block;margin-top:4px;font-size:9px;font-weight:500;
  letter-spacing:1.8px;color:#a6bdb0}
.vm-mark{height:31px;width:29px;background:#d9eeaf;border-radius:7px 7px 15px 7px;
  display:grid;place-items:center;transform:rotate(-6deg);flex-shrink:0}
.vm-mark svg{width:19px;height:19px;stroke:#214b40;stroke-width:1.7;fill:none;
  stroke-linecap:round;stroke-linejoin:round;transform:rotate(6deg)}
.vm-navlabel{font-size:10px;letter-spacing:1.7px;font-weight:650;color:#a6bdb0;
  margin:30px 8px 10px}
.vm-nav{border:0!important;background:transparent!important;padding:0!important}
.vm-nav .wrap{flex-direction:column!important;gap:4px!important}
.vm-nav label{display:flex!important;align-items:center;padding:11px 14px!important;
  border-radius:7px!important;border:0!important;background:transparent!important;
  color:#c9dbd1!important;font-size:13px!important;font-weight:500!important;
  letter-spacing:0!important;cursor:pointer;width:100%}
.vm-nav label,.vm-nav label span,.vm-side .vm-nav label *{
  color:#d4e4da!important;opacity:1!important}
.vm-nav label:hover{background:#1d4a41!important}
.vm-nav label.selected,.vm-nav label.selected span,.vm-nav label.selected *{
  color:#173226!important}
.vm-nav label.selected{background:#d9eeaf!important;color:#224431!important;
  font-weight:650!important}
.vm-nav input[type=radio]{display:none!important}
.vm-foot{margin-top:28px;padding:13px 12px;border-top:1px solid #2b514a}
.vm-status{font-size:11px;color:#a6bdb0;line-height:1.5}
.vm-status small{display:block;color:#93aa9e;font-size:10px;margin-top:3px}
.vm-dot{display:inline-block;width:7px;height:7px;border-radius:50%;
  background:#a7d474;margin-right:7px}

/* Furniture borrowed from the workspace: an eyebrow over the title, icons
   in the nav, the glance rail, and the dashed drop targets. */
.vm-eyebrow{font-size:10px;letter-spacing:1.8px;font-weight:650;color:#688371;
  text-transform:uppercase;margin:2px 2px 0}

/* Nav icons. mask-image takes the label's own colour, so the active pill's
   dark ink and the idle light green both come out right with no extra rules. */
.vm-nav label::before{content:"";width:16px;height:16px;flex-shrink:0;margin-right:11px;
  background:currentColor;-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;
  -webkit-mask-position:center;mask-position:center;
  -webkit-mask-size:contain;mask-size:contain}
.vm-nav label:nth-of-type(1)::before{-webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23000' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='3' width='7' height='7' rx='1'/%3E%3Crect x='14' y='3' width='7' height='7' rx='1'/%3E%3Crect x='3' y='14' width='7' height='7' rx='1'/%3E%3Crect x='14' y='14' width='7' height='7' rx='1'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23000' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='3' width='7' height='7' rx='1'/%3E%3Crect x='14' y='3' width='7' height='7' rx='1'/%3E%3Crect x='3' y='14' width='7' height='7' rx='1'/%3E%3Crect x='14' y='14' width='7' height='7' rx='1'/%3E%3C/svg%3E")}
.vm-nav label:nth-of-type(2)::before{-webkit-mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23000' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m12 3 10 5-10 5L2 8zm-10 9 10 5 10-5M2 16l10 5 10-5'/%3E%3C/svg%3E");
  mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23000' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m12 3 10 5-10 5L2 8zm-10 9 10 5 10-5M2 16l10 5 10-5'/%3E%3C/svg%3E")}


/* Device row under the sidebar status. */
.vm-profile{display:flex;align-items:center;gap:10px;margin-top:14px;padding-top:13px;
  border-top:1px solid #2b514a}
.vm-avatar{width:31px;height:31px;border-radius:50%;background:#2c584d;color:#cfe3d5;
  display:grid;place-items:center;font-size:9px;font-weight:700;flex-shrink:0}
.vm-profile strong{display:block;font-size:12px;color:#dbe9df;font-weight:600}
.vm-profile small{display:block;font-size:9.5px;color:#93aa9e;margin-top:2px}


/* Headings and hints are decoration, so they carry a class of their own and
   are drawn flat. The previous attempt selected them by what the block
   contained, with a child combinator -- Gradio wraps HTML content in a div,
   so the marker was a grandchild and the rule never matched, which is why a
   heading was still being boxed like an input. */
.vm-flat,.vm-flat.block,.gradio-container .vm-flat{border:0!important;
  padding:0!important;background:transparent!important;box-shadow:none!important;
  min-width:0!important;overflow:visible!important}
/* Both drop targets the same height, so the two columns end level. */
.vm-sect .image-container,.vm-sect .upload-container{min-height:148px}

/* Sections, not cards.
   A section is spacing and a heading. The border round each control is the one
   Gradio already draws, so there is no second panel to fight and no grey band
   between them. */
/* A Row of controls should not draw a box round the row itself. */

/* The numbered steps and the hints under the controls. */
.vm-stepn{width:22px;height:22px;border-radius:5px;background:#f0f3e9;color:#748469;
  font-size:10px;font-weight:700;display:grid;place-items:center;flex-shrink:0}
.vm-step b{font-size:14px;font-weight:600;color:#1b2420;display:block}
.vm-step small{display:block;font-size:11px;color:#6b756f;margin-top:2px}

/* Skill cards on the Skills page. */
.vm-skill{border:1px solid #e2e7df;border-radius:10px;background:#fff;padding:18px 20px}
.vm-skill h3{font-family:Georgia,'Times New Roman',serif!important;font-size:19px!important;
  font-weight:400!important;color:#1b2420;margin:0 0 4px}
.vm-snote{font-size:12px;color:#57615c;margin:0 0 14px;line-height:1.6}
.vm-srow{display:flex;gap:12px;padding:7px 0;border-top:1px solid #eef1ea;font-size:12px}
.vm-srow span{color:#6b756f;min-width:84px;flex-shrink:0}
.vm-smodel{display:inline-block;margin-bottom:12px;padding:4px 10px;border-radius:20px;
  background:#eef4e6;color:#2c4a36;font-size:11px;font-weight:600}
.vm-srow b{color:#1b2420;font-weight:600}

/* The prompt bar, sitting at the foot of the page as one unit. */
/* Fill the column rather than floating in the middle of it. */

/* Save and its downloads sit on one line, so they should start on one line. */
.vm-sect .vm-dl{align-self:start}
/* Measured: a slider's reset button is 26x24 inside a container that clips at
   24px. A blanket min-height meant for the Save button stretched it to 44 and
   the browser cut the icon in half. Action buttons only. */
.vm-sect button:not(.reset-button){min-height:44px}
.vm-sect .reset-button{min-height:0!important;height:22px!important;
  align-self:center}
/* An empty result panel should not reserve the height of a full one. */
.vm-sect .image-container{max-height:230px}

/* ---------------------------------------------------------------- rhythm
   One scale for the whole page, so a gap is never picked by eye again:
   4px inside a control, 10px between controls, 20px between sections.
   Every box gets the same padding and radius, whatever it holds. */
.gradio-container .block{background:#fff!important;
  border:1px solid #e2e7df!important;border-radius:10px!important;
  padding:12px 14px!important;box-shadow:none!important;min-width:0!important}
/* A Row is a layout, not a box: it must not draw one round its children. */
.gradio-container .form{background:transparent!important;border:0!important;
  padding:0!important;gap:10px!important}
.vm-sect{margin:0 0 20px 0!important;gap:10px!important}
.vm-sect>*{width:100%}
.vm-step{display:flex;align-items:center;gap:10px;margin:0 2px 4px}
.vm-hint{font-size:11px;color:#6b756f;line-height:1.6;margin:4px 2px 0}
.vm-page{padding:4px 2px}

/* --------------------------------------------------- measured corrections
   Everything below was set from the rendered page, not from guesswork. */

/* The empty downloads placeholder was 236px of nothing inside a 288px box.
   Specificity has to beat .gradio-container .block, hence the doubled class. */
.gradio-container .block.vm-dl{padding:8px 10px!important;max-height:96px!important;
  overflow:auto!important}
.gradio-container .block.vm-dl .empty{min-height:0!important;height:56px!important}
.gradio-container .block.vm-dl .wrap{min-height:0!important}

/* The two upload boxes were 150px and 176px, so the hints under them sat 26px
   apart -- the file box stacks its label above the drop area, the image box
   floats it. Match the image box to the taller one. */

/* Columns inside a Row were 16px apart while everything else used 10px. */
.vm-page .row{gap:10px!important}

/* Measured on the rendered page. The title was being drawn as a bordered
   control box (120px tall, 1px border); it is a heading, so it is flattened
   by what it contains rather than by touching the call site. */
.gradio-container .block:has(>.prose h1),.gradio-container .block:has(h1){
  border:0!important;padding:0!important;background:transparent!important;
  box-shadow:none!important}

/* The image box measured 150px and the file box 176px -- one floats its label
   over the drop area, the other stacks it above -- which left the hints under
   them 26px out of line. Pin both. */
.gradio-container .block.vm-drop{height:172px!important}
.gradio-container .block.vm-drop .image-container,
.gradio-container .block.vm-drop .center.boundedheight{
  height:100%!important;min-height:0!important}

/* The prompt bar sat 75px below the last section; everything else uses 20. */

/* ------------------------------------------------------- the prompt card
   Shaped like the workspace's task card: a heading, a roomy cream box, then
   a rule with the note on one side and the action on the other. */
/* A panel in its own right, the width of the two above it. */
.gradio-container .block.vm-prompt{border:0!important;padding:0!important;
  background:transparent!important}
.vm-prompt textarea{background:#fdfefa!important;border:1px solid #dce3d5!important;
  border-radius:7px!important;min-height:104px!important;padding:12px 13px!important;
  font-size:13px!important;line-height:1.6}
/* Footer: a rule across the card, note left, action right. */

/* ------------------------------------------------------------ upload tabs
   Styled as the workspace's segmented control: a quiet row of labels with the
   active one underlined, not a pair of boxed buttons. */
.vm-tabs{border:0!important;background:transparent!important;padding:0!important}
.vm-tabs .tab-nav,.vm-tabs .tab-container{border:0!important;
  border-bottom:1px solid #e2e7df!important;gap:20px!important;
  background:transparent!important;padding:0 2px!important;margin-bottom:14px!important}
.vm-tabs button[role=tab]{background:transparent!important;border:0!important;
  border-bottom:2px solid transparent!important;border-radius:0!important;
  padding:8px 1px!important;min-height:0!important;font-size:12.5px!important;
  font-weight:600!important;color:#6b756f!important}
.vm-tabs button[role=tab].selected{color:#1b2420!important;
  border-bottom-color:#245c4d!important}
.vm-tabs .tab-container.visually-hidden{display:none!important}
/* Clicking a tab left a box drawn round the label. A focus ring is for
   finding your place with the keyboard, not for confirming a mouse click, so
   it is dropped for the pointer and kept for :focus-visible. */
.vm-tabs button[role=tab]:focus:not(:focus-visible){outline:none!important;
  box-shadow:none!important}
.vm-tabs .tabitem{border:0!important;padding:0!important;background:transparent!important}

/* ------------------------------------------------------- document type
   The field chips show what will be asked for, so the analyst can see the
   shape of the answer before running anything. */
.gradio-container .block.vm-doctype{border:0!important;padding:0!important;
  background:transparent!important;margin-bottom:10px}
.vm-fields{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}
.vm-field{font-size:11px;padding:3px 9px;border-radius:20px;background:#eef4e6;
  color:#2c4a36;font-weight:500}
.vm-ok{font-size:11.5px;color:#2c6a4a;margin:8px 0 0;font-weight:500}
.vm-warn{font-size:11.5px;color:#8a5a1e;margin:8px 0 0;font-weight:500;
  background:#fdf6e8;border:1px solid #f0e3c6;border-radius:7px;padding:8px 10px}

/* Field pickers sit inside the prompt card, so they carry no panel. */
.gradio-container .block.vm-fieldpick,.gradio-container .block.vm-extra{
  border:0!important;background:transparent!important;padding:0!important;
  margin-bottom:10px}
.vm-fieldpick .wrap{gap:6px!important;flex-wrap:wrap!important}
.vm-fieldpick label{background:#f4f7ef!important;border:1px solid #e2e7df!important;
  border-radius:20px!important;padding:4px 11px!important;font-size:11px!important;
  font-weight:500!important;color:#3f4a45!important;margin:0!important}
.vm-fieldpick label.selected{background:#eef4e6!important;
  border-color:#cfe0bd!important;color:#2c4a36!important}

/* The field results table. */
.gradio-container .block.vm-fieldtable{padding:0!important;overflow:hidden}
.vm-fieldtable table{font-size:12px}
.vm-fieldtable th{background:#f4f7ef!important;font-weight:600!important;
  color:#3f4a45!important;font-size:11px!important}
.vm-fieldtable td{color:#1b2420}

/* ---------------------------------------------------------- output panel
   One surface for everything a run produces, so the page reads as "these are
   the inputs, that is what came back" rather than as one long form. The
   sections inside it keep their headings and lose their own edges -- a panel
   inside a panel was what made the left column look so busy earlier. */
.vm-inpanel,.vm-outpanel{background:#fff!important;border:1px solid #e2e7df!important;
  border-radius:12px!important;padding:18px 20px 6px!important;
  align-self:flex-start!important}
.vm-inpanel .vm-sect,.vm-outpanel .vm-sect{margin-bottom:18px!important}
.vm-inpanel .block,.vm-outpanel .block{border:0!important;background:transparent!important;
  padding:0!important}
/* The things you read out of, or type into, keep a surface of their own. */
.vm-inpanel textarea,.vm-outpanel textarea{background:#fafbf7!important;border:1px solid #e6ebe0!important;
  border-radius:8px!important;padding:11px 12px!important}
.vm-outpanel .block.vm-fieldtable{border:1px solid #e6ebe0!important;
  border-radius:8px!important;overflow:hidden}
.vm-outpanel .image-container{border:1px solid #e6ebe0!important;
  border-radius:8px!important;background:#fafbf7!important}
.vm-outpanel .block.vm-dl{border:1px solid #e6ebe0!important;border-radius:8px!important;
  background:#fafbf7!important;padding:8px 10px!important}
.vm-panelhead,.vm-outhead{display:flex;align-items:baseline;gap:10px;padding-bottom:13px;
  margin-bottom:16px;border-bottom:1px solid #eef1ea}
.vm-panelhead b,.vm-outhead b{font-family:Georgia,'Times New Roman',serif;font-size:19px;
  font-weight:400;color:#1b2420}
.vm-panelhead span,.vm-outhead span{font-size:11px;color:#6b756f}

/* The input side, matching the output side. The controls keep a surface so
   they still read as things to fill in; the panel is the only border. */
.vm-inpanel input:not([type=radio]):not([type=checkbox]),
.vm-inpanel select,.vm-inpanel .wrap.center{
  background:#fafbf7!important;border:1px solid #e6ebe0!important;
  border-radius:8px!important}
.vm-inpanel .image-container,.vm-inpanel .center.boundedheight{
  border:1px dashed #cfd9c6!important;border-radius:8px!important;
  background:#fafbf7!important}
.vm-inpanel .vm-hint{margin:6px 2px 0}
/* A slider is a control, not a box: it must not gain a field surface. */
.vm-inpanel input[type=range]{background:transparent!important;border:0!important}

/* ------------------------------------------------------------- composer
   One box you type into, its controls along the bottom edge and a round send
   button on the right. The text is the thing; everything else keeps out of
   its way until it is wanted. */
.vm-composer{margin-top:14px!important;background:#fff!important;
  border:1px solid #dfe5d9!important;border-radius:24px!important;
  padding:16px 18px 12px!important;gap:0!important;
  box-shadow:0 1px 2px rgba(31,45,40,.04),0 8px 24px rgba(31,45,40,.05)!important}
.vm-composer:focus-within{border-color:#b9cdb4!important;
  box-shadow:0 1px 2px rgba(31,45,40,.05),0 10px 30px rgba(31,45,40,.08)!important}
/* The field has no edge of its own: the composer is the edge. */
.vm-composer .block,.vm-composer .form{background:transparent!important;
  border:0!important;padding:0!important;box-shadow:none!important}
.vm-composer textarea{background:transparent!important;border:0!important;
  box-shadow:none!important;resize:none!important;padding:2px 4px!important;
  font-size:14px!important;line-height:1.6!important;color:#1b2420!important}
.vm-composer textarea::placeholder{color:#93a09a!important}
.vm-composer textarea{min-height:46px!important;max-height:240px!important}
.vm-composer textarea:focus{outline:none!important}

.vm-composer-bar{margin-top:10px!important;align-items:center!important;
  gap:10px!important;flex-wrap:nowrap!important}
.vm-composer button.vm-send{margin-left:auto!important}
/* The document type reads as a chip on the bar, not as a form field. */
.vm-composer .vm-doctype input{background:#f3f7ee!important;
  border:1px solid #e2e9da!important;border-radius:18px!important;
  padding:7px 14px!important;font-size:12px!important;font-weight:500!important;
  color:#2c4a36!important;cursor:pointer}
.vm-composer .vm-doctype{flex:0 0 auto!important;width:232px!important}

/* Send: round, and only as loud as it needs to be. */
.vm-composer button.vm-send{width:40px!important;min-width:40px!important;
  height:40px!important;min-height:40px!important;border-radius:50%!important;
  padding:0!important;font-size:17px!important;line-height:1!important;
  display:grid!important;place-items:center!important;flex:0 0 auto}
.vm-composer button.vm-send::after{content:none!important}

.vm-composer-note{margin-top:10px!important}
.vm-composer-note .vm-hint,.vm-composer-note .vm-ok,.vm-composer-note .vm-warn{
  margin-top:0}
"""


# The sidebar. Dashboard is the OCR page exactly as it was; Skills is listed
# because the nav was asked for, and says plainly that it is not wired to
# anything rather than pretending to be a feature. Adding another page is a
# name here and a gr.Column(visible=False) in build_demo.
NAV_PAGES = ["Dashboard", "Skills"]

BRAND_HTML = """
<div class="vm-brand">
  <span class="vm-mark">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19C1 5 13 3 21 3c0 12-6 18-16 16zm0 0L17 7"/></svg>
  </span>
  <div>Local OCR<small>VISION MODELS</small></div>
</div>
<div class="vm-navlabel">WORKSPACE</div>
"""

SIDEBAR_FOOT_HTML = """
<div class="vm-foot">
  <div class="vm-status"><span class="vm-dot"></span>Running on this device
    <small>Nothing leaves the machine</small></div>
  <div class="vm-profile"><span class="vm-avatar">GPU</span>
    <div><strong>RTX 5080</strong><small>16GB &middot; one model at a time</small></div></div>
</div>
"""

# The two models over the two kinds of file, because each pairing behaves
# differently enough to be worth choosing deliberately: only DeepSeek-OCR reads
# the prompt, only PaddleOCR-VL batches pages, and they produce different
# panels. Picking one sets the Model and the prompt on the Dashboard and sends
# you there; nothing about the run itself, or what comes out of it, changes.
#
# Each prompt is the model's own default rather than one written here. A
# generalised "extract every printed label and value" instruction was tried
# against a real bill and read no better -- worse on one run -- while the
# defaults are the ones every measurement in this project was taken with.
SKILL_DIR = os.path.join(_here, "ocr_skills")


def load_skills(directory=SKILL_DIR):
    """Every skill file on disk, ordered by filename so the page is stable.

    One JSON file per skill, in their own folder. Not skills/ -- that one
    already belongs to the table-extraction recipes, which are a different
    thing that happens to share the word.

    A skill only presets the Model and the prompt; it cannot change what a run
    does or what comes out of it. So a broken file costs that one card and is
    skipped with a note, rather than taking the page down.
    """
    out = []
    if not os.path.isdir(directory):
        print(f"skills: no {directory}, the Skills page will be empty", flush=True)
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"skills: skipping {name}: {type(e).__name__}: {e}", flush=True)
            continue
        missing = [k for k in ("name", "model", "takes", "gives") if not cfg.get(k)]
        if missing:
            print(f"skills: skipping {name}, missing {missing}", flush=True)
            continue
        if cfg["model"] not in MODELS:
            print(f"skills: skipping {name}, unknown model {cfg['model']!r}", flush=True)
            continue
        out.append(cfg)
    return out


SKILLS = load_skills()


def doc_type_card(cfg):
    """One document type: which reader it picks, and what it is read for."""
    fields = cfg.get("fields") or []
    chips = "".join(f'<span class="vm-field">{html.escape(f)}</span>' for f in fields)
    body = (f'<div class="vm-fields">{chips}</div>' if chips else
            '<p class="vm-snote">No fixed field list &mdash; the whole document '
            'is transcribed.</p>')
    return (f'<div class="vm-skill"><h3>{html.escape(cfg["name"])}</h3>'
            f'<div class="vm-smodel">{html.escape(cfg["reader"])}</div>'
            f'<p class="vm-snote">{html.escape(cfg.get("why", ""))}</p>{body}</div>')


def skill_card(skill):
    """One card. Values come from the file, so they are escaped, not trusted."""
    rows = [("Takes", skill["takes"]),
            ("Prompt", skill.get("prompt_note", "")),
            ("Gives back", skill["gives"]),
            ("Speed", skill.get("speed", ""))]
    body = "".join(f'<div class="vm-srow"><span>{html.escape(k)}</span>'
                   f'<b>{html.escape(str(v))}</b></div>'
                   for k, v in rows if v)
    return (f'<div class="vm-skill"><h3>{html.escape(skill["name"])}</h3>'
            f'<p class="vm-snote">{html.escape(skill.get("note", ""))}</p>'
            f'<div class="vm-smodel">{html.escape(skill["model"])}</div>{body}</div>')



# Signposting for the Dashboard. Nothing here changes what a run does; it
# answers the questions the page could not previously answer on its own --
# which of the two upload boxes to use, which controls the chosen model
# actually reads, and why the first run takes a minute.
EYEBROW_HTML = (
    '<div class="vm-eyebrow">WORKSPACE &middot; LOCAL</div>'
)

def flat(markup):
    """A heading or hint, drawn as itself rather than as a control."""
    return gr.HTML(markup, elem_classes="vm-flat")


def hint(text):
    return f'<p class="vm-hint">{html.escape(text)}</p>'


def step_head(number, title, sub="", note=""):
    """A numbered section heading, with an optional note aligned right."""
    tail = f'<small>{html.escape(sub)}</small>' if sub else ""
    right = f'<span class="vm-cardnote">{html.escape(note)}</span>' if note else ""
    return (f'<div class="vm-step"><span class="vm-stepn">{number:02d}</span>'
            f'<div><b>{html.escape(title)}</b>{tail}</div>{right}</div>')


DOC_TYPE_DIR = os.path.join(_here, "doc_types")
FREE_FORM = "Anything else / write my own"


def load_doc_types(directory=DOC_TYPE_DIR):
    """Document types and the fields each one is usually read for."""
    out = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"doc types: skipping {name}: {type(e).__name__}: {e}", flush=True)
            continue
        if not cfg.get("name") or cfg.get("reader") not in MODELS:
            print(f"doc types: skipping {name}, bad name or reader", flush=True)
            continue
        out.append(cfg)
    return out


DOC_TYPES = load_doc_types()


def doc_type(name):
    return next((d for d in DOC_TYPES if d["name"] == name), None)


def split_fields(text):
    """Free-typed extra fields: commas or newlines, blanks dropped."""
    parts = (text or "").replace("\n", ",").split(",")
    return [f.strip() for f in parts if f.strip()]


def compose_prompt(cfg, fields=None, extra=""):
    """Always empty: the reader is asked to transcribe, never to extract.

    A field list was composed into the instruction here. On a scanned utility
    bill that turned a 42-second, 1,451-character transcription into a
    330-second, 35,505-character loop -- the model repeating itself until it
    stopped. A bare comma-separated list produced 28 characters instead.
    DeepSeek-OCR answers one short question about a page well; it does not
    take a field list. The fields are kept in the type files as the statement
    of what is wanted, for whatever does the extracting.
    """
    return ""


def doc_type_note(type_name, model_label):
    """One line: the reader in use, and any warning that it will ignore you.

    Deliberately terse. The reasoning behind each type is on the Skills page;
    under the composer only two things matter -- which reader is about to run,
    and whether typing here will have any effect on it.
    """
    short = model_label.split(" (")[0]
    cfg = doc_type(type_name)
    if cfg is None:
        if model_label.startswith("PaddleOCR"):
            return ('<p class="vm-warn">PaddleOCR-VL runs a fixed pipeline and '
                    'ignores what you type. Switch reader in step 01 to use an '
                    'instruction.</p>')
        return f'<p class="vm-hint">{html.escape(short)} will follow what you type.</p>'
    wants = cfg["reader"]
    fields = len(cfg.get("fields") or [])
    if model_label != wants:
        return (f'<p class="vm-warn">This type reads best with '
                f'{html.escape(wants.split(" (")[0])}. Change the reader in step 01.</p>')
    tail = f"{fields} fields ready for step 06" if fields else "whole page transcribed"
    return (f'<p class="vm-hint">{html.escape(short)} &middot; '
            f'{html.escape(tail)}</p>')


def build_demo():
    """Construct the UI.

    Deliberately not executed at import. Other modules import this one to
    reuse the OCR worker (ensure_model / _ocr_request_batch / stop_vllm),
    and building Gradio components at import time blew up inside a live
    request from another app with 'Dropdown' object has no attribute '_id'.
    """
    # fill_width, because Gradio caps its own container at 1536px and that left
    # a 229px dead strip on the right of a 1900px window. Gradio's own
    # parameter rather than a CSS override, so it survives an upgrade.
    with gr.Blocks(title="Local Vision Models - RTX 5080", fill_width=True) as demo:
        with gr.Sidebar(width=244, elem_classes="vm-side"):
            flat(BRAND_HTML)
            nav = gr.Radio(NAV_PAGES, value=NAV_PAGES[0], show_label=False,
                           container=False, elem_classes="vm-nav")
            flat(SIDEBAR_FOOT_HTML)

        # Every page is built, and the nav only changes which one is visible.
        # Nothing below is re-created on a click, so a run in progress is not
        # disturbed by looking at another page.
        with gr.Column(visible=True, elem_classes="vm-page") as page_dashboard:
            flat(EYEBROW_HTML)
            gr.Markdown(
                "# Local Vision Models\n"
                "Runs entirely on your RTX 5080 \u2014 nothing leaves the machine. "
                "Say what kind of document it is, drop the file in, hit Run. "
                "PDFs are read page by page and stream in live; step 06 then "
                "searches that text for the fields you asked for.\n"
                "Switching readers unloads the previous one to stay within 16GB VRAM."
            )
            with gr.Row(equal_height=False):
                with gr.Column(scale=1, min_width=380, elem_classes="vm-inpanel"):
                    flat('<div class="vm-panelhead"><b>Input</b><span>The document, and how to read it</span></div>')
                   
                    with gr.Column(elem_classes="vm-sect"):
                        flat(step_head(1, "Choose a reader"))
                        model_dd = gr.Dropdown(
                            choices=list(MODELS.keys()),
                            value="PaddleOCR-VL (document OCR)",
                            label="Model",
                            info="PaddleOCR-VL reads layout and tables and ignores "
                                 "the prompt. DeepSeek-OCR follows the prompt and can "
                                 "draw boxes, but reads tables less reliably.",
                        )

                    # Side by side. Stacked, these two ran to most of a screen on
                    # their own, and the choice between them reads better as a
                    # choice when they sit next to each other.
                    with gr.Column(elem_classes="vm-sect"):
                        flat(step_head(2, "Add your document"))
                        with gr.Tabs(elem_classes="vm-tabs"):
                            with gr.Tab("Image"):
                                image_in = gr.Image(type="filepath",
                                                    label="Upload image",
                                                    elem_classes="vm-drop")
                                flat(hint("A photo or scan \u2014 PNG, JPG or WEBP. "
                                          "Read in one pass."))
                            with gr.Tab("PDF"):
                                pdf_in = gr.File(label="...or upload PDFs (several is fine)",
                                                 file_types=[".pdf"], file_count="multiple",
                                                 elem_classes="vm-drop")
                                flat(hint("Several are fine. Each page is rendered at the "
                                          "DPI below, then read; results stream in as they "
                                          "finish. A PDF here takes precedence over an "
                                          "image on the other tab."))

                    with gr.Column(elem_classes="vm-sect"):
                        flat(step_head(3, "Page options",
                                       "PDFs only, ignored for an image."))
                        with gr.Row():
                            pages_in = gr.Textbox(label="Pages", value="",
                                                  placeholder="all, or 1-3 / 1,4,7", scale=2)
                            dpi_in = gr.Slider(150, 600, value=300, step=50,
                                               label="Render DPI", scale=3)
                        batch_in = gr.Slider(1, 16, value=4, step=1,
                                             label="Pages per vLLM batch")
                        flat(hint(
                            "Pages: blank reads every page. "
                            "Render DPI: higher is sharper and slower; 300 suits most "
                            "scans. Batch: PaddleOCR-VL only, higher is faster with "
                            "coarser updates; DeepSeek-OCR always reads one page at a time."))

                with gr.Column(scale=1, min_width=380, elem_classes="vm-outpanel"):
                    flat('<div class="vm-outhead"><b>Output</b><span>What the reader gave back</span></div>')
                   
                    with gr.Column(elem_classes="vm-sect"):
                        flat(step_head(4, "Watch it run"))
                        status_out = gr.Textbox(label="Progress (live)", lines=6,
                                                max_lines=6, autoscroll=True,
                                                placeholder="Nothing running yet. The first "
                                                            "run after starting the app loads "
                                                            "the model and can take around a "
                                                            "minute before the first page "
                                                            "appears. Later runs are seconds.")
                        text_out = gr.Textbox(label="Result (raw)", lines=16,
                                              placeholder="The transcribed text lands here, "
                                                          "and can be copied straight out or "
                                                          "saved as .md / .html / .xlsx.")
                    with gr.Column(elem_classes="vm-sect"):
                        img_out = gr.Image(label="Annotated output (OCR grounding, if produced)",
                                           height=220, visible=False)
                        flat(hint("Choose DeepSeek-OCR to also get the page back with "
                                  "boxes drawn on it. PaddleOCR-VL does not produce one."))
                        html_out = gr.HTML(label="Rendered table (PaddleOCR-VL)", visible=False)
                    with gr.Column(elem_classes="vm-sect"):
                        flat(step_head(5, "Keep the result",
                                          "Available once a run has produced text."))
                        # The downloads list appears under the button once there
                        # is something in it. Empty, it was a third of a screen
                        # of white reserved to hold nothing.
                        save_btn = gr.Button("Save output (.md / .html / .xlsx)")
                        files_out = gr.File(label="Download", file_count="multiple",
                                            visible=False, elem_classes="vm-dl")

                    with gr.Column(elem_classes="vm-sect"):
                        flat(step_head(6, "Pull out fields",
                                       "Searches the text above for the labels "
                                       "printed on the page.",
                                       note="After a run"))
                        fields_cb = gr.CheckboxGroup(
                            choices=[], value=[], visible=False,
                            label="Fields to look for",
                            info="Set by the document type. Untick what you do not need.",
                            elem_classes="vm-fieldpick",
                        )
                        extra_in = gr.Textbox(
                            value="", visible=False, lines=1,
                            label="Any other fields",
                            placeholder="Comma separated, e.g. Tenant name, Lease expiry",
                            elem_classes="vm-extra",
                        )
                        find_btn = gr.Button("Find these fields in the result")
                        fields_out = gr.Dataframe(
                            headers=["Field", "Value", "Looks right", "Found where"],
                            datatype=["str", "str", "str", "str"],
                            row_count=(0, "dynamic"), col_count=(4, "fixed"),
                            interactive=False, wrap=True, visible=False,
                            elem_classes="vm-fieldtable",
                        )
                        fields_note = gr.HTML(elem_classes="vm-flat")

            # The prompt and Run sit at the foot of the page, as one bar. The
            # handler is unchanged: what matters to it is the order of the
            # inputs list below, not where the boxes are drawn.
            with gr.Column(elem_classes="vm-composer"):
                prompt_in = gr.Textbox(
                    value=DEFAULT_PROMPTS["paddleocr_vl"],
                    show_label=False,
                    container=False,
                    lines=2,
                    max_lines=12,
                    placeholder="What should the reader do with this document?",
                    elem_classes="vm-prompt",
                )
                with gr.Row(elem_classes="vm-composer-bar"):
                    doc_type_dd = gr.Dropdown(
                        choices=[d["name"] for d in DOC_TYPES] + [FREE_FORM],
                        value=FREE_FORM,
                        label="Document type",
                        show_label=False,
                        container=False,
                        scale=0,
                        min_width=230,
                        elem_classes="vm-doctype",
                    )

                    run_btn = gr.Button("\u2191", variant="primary", scale=0,
                                        elem_classes="vm-send")
                doc_note = gr.HTML(doc_type_note(FREE_FORM, "PaddleOCR-VL (document OCR)"),
                                   elem_classes="vm-flat vm-composer-note")
        with gr.Column(visible=False, elem_classes="vm-page") as page_skills:
            flat(EYEBROW_HTML)
            gr.Markdown(
                elem_classes="vm-flat",
                value=(
                    "# Skills\n"
                    "Three things make up a run. You pick the **document type**; "
                    "it picks the reader for you. The reader **transcribes** the "
                    "page. Step 06 then **searches that text** for the field "
                    "labels printed on it.\n\n"
                    "Fields are never asked of the reader. Asked for a nine-field "
                    "list, one reader looped to 35,505 characters in five and a "
                    "half minutes on a one-page bill; a bare list of names "
                    "returned 28 characters. Reading and extracting are separate "
                    "jobs here because measuring them together showed they have "
                    "to be."
                ),
            )

            flat('<div class="vm-step vm-step-plain"><div><b>Document types</b>'
                 '<small>What you choose on the Dashboard. Each one sets its '
                 'reader, and names the fields step 06 will look for.</small>'
                 '</div></div>')
            for row_start in range(0, len(DOC_TYPES), 3):
                with gr.Row():
                    for cfg in DOC_TYPES[row_start:row_start + 3]:
                        with gr.Column():
                            gr.HTML(doc_type_card(cfg))

            flat('<div class="vm-step vm-step-plain"><div><b>Readers</b>'
                 '<small>The two models underneath, and what each is good and '
                 'bad at. You do not normally choose these directly.</small>'
                 '</div></div>')
            picks = []
            for row_start in (0, 2):
                with gr.Row():
                    for skill in SKILLS[row_start:row_start + 2]:
                        with gr.Column():
                            gr.HTML(skill_card(skill))
                            picks.append((skill, gr.Button("Use this reader")))

            flat('<div class="vm-step vm-step-plain"><div><b>Finding fields</b>'
                 '<small>Step 06 on the Dashboard, once a run has produced '
                 'text.</small></div></div>')
            gr.Markdown(
                elem_classes="vm-flat",
                value=(
                    "A field is found by the label **printed on the page**, not by "
                    "a model, so nothing can be invented: a label that is not there "
                    "is reported as not found.\n\n"
                    "- Each field carries its other spellings, so a field called "
                    "*Account number* still matches a bill that says *Account No.*, "
                    "and you can add your own.\n"
                    "- The value is looked for beside the label, to the right of it, "
                    "beneath a column heading, and further down past any run of "
                    "other labels, which covers bills that print all the labels "
                    "first and all the values after.\n"
                    "- Each field knows the shape its value should have. Given "
                    "`Due Date | 171.73 | 09/28/2026` it takes the date, not the "
                    "amount sitting closer.\n"
                    "- A value found in the wrong shape is marked **CHECK** rather "
                    "than accepted quietly. That is what a table which has slipped "
                    "a row looks like."
                ),
            )

        nav.change(lambda choice: [gr.update(visible=(choice == name))
                                   for name in NAV_PAGES],
                   inputs=nav, outputs=[page_dashboard, page_skills])

        # Each skill hands the Dashboard its model and that model's prompt, then
        # shows it. The prompt sent here is the same value on_model_change would
        # write for that model, so it does not matter which of the two lands
        # last -- there is no race to lose.
        for _skill, _button in picks:
            _button.click(
                lambda s=_skill: (gr.update(value=s["model"]),
                                  gr.update(value=DEFAULT_PROMPTS[MODELS[s["model"]]]),
                                  gr.update(value="Dashboard"),
                                  gr.update(visible=True),
                                  gr.update(visible=False)),
                outputs=[model_dd, prompt_in, nav, page_dashboard, page_skills],
            )

        # Choosing a document type writes the instruction and says whether the
        # reader currently selected will actually read it. It deliberately does
        # not change the Model: that control has its own handler which rewrites
        # this box, and the two would race for the last word.
        def on_doc_type(type_name, model_label):
            """Choosing the document chooses the reader with it.

            An analyst handed a PDF should not have to know that a rent roll
            wants the table reader and an appraisal wants the promptable one.
            Setting the Model here fires its own handler, which rewrites the
            prompt box -- but that handler is chained to put the document
            type's instruction back afterwards, so the order resolves itself
            rather than racing.
            """
            cfg = doc_type(type_name)
            if cfg is None:
                return (gr.update(), doc_type_note(type_name, model_label), gr.update(),
                        gr.update(choices=[], value=[], visible=False),
                        gr.update(value="", visible=bool(known)))
            wants = cfg["reader"]
            known = cfg.get("fields") or []
            composed = compose_prompt(cfg)
            # A type with no field list wants the whole document, so there is
            # nothing to tick and the pickers stay out of the way.
            return (gr.update(value=composed or DEFAULT_PROMPTS[MODELS[wants]]),
                    doc_type_note(type_name, wants),
                    gr.update(value=wants),
                    gr.update(choices=known, value=known, visible=bool(known)),
                    gr.update(value="", visible=bool(known)))

        def on_fields(type_name, fields, extra):
            cfg = doc_type(type_name)
            if cfg is None:
                return gr.update()
            return gr.update(value=compose_prompt(cfg, fields, extra))

        doc_type_dd.change(on_doc_type, inputs=[doc_type_dd, model_dd],
                           outputs=[prompt_in, doc_note, model_dd, fields_cb, extra_in])
        fields_cb.change(on_fields, inputs=[doc_type_dd, fields_cb, extra_in],
                         outputs=prompt_in)
        extra_in.submit(on_fields, inputs=[doc_type_dd, fields_cb, extra_in],
                        outputs=prompt_in)
        extra_in.blur(on_fields, inputs=[doc_type_dd, fields_cb, extra_in],
                      outputs=prompt_in)
        # Changing the reader re-checks the advice, which may have just become
        # right or wrong for the type already chosen.
        model_dd.change(lambda t, m: doc_type_note(t, m),
                        inputs=[doc_type_dd, model_dd], outputs=doc_note)
        # on_model_change rewrites the prompt with the new model's default, which
        # would throw away the instruction a document type had just written --
        # and the advice above tells you to change the model, so that is the
        # normal path, not an edge case. .then() runs after it on the same
        # trigger, so the document type gets the last word deterministically.
        def keep_doc_instruction(type_name, fields, extra):
            cfg = doc_type(type_name)
            composed = compose_prompt(cfg, fields, extra) if cfg else ""
            return gr.update(value=composed) if composed else gr.update()

        model_dd.change(on_model_change, inputs=model_dd,
                        outputs=[prompt_in, img_out, html_out]).then(
            keep_doc_instruction, inputs=[doc_type_dd, fields_cb, extra_in],
            outputs=prompt_in)
        def pull_fields(text, chosen, extra):
            """Search the transcript for the chosen labels.

            Nothing here calls a model. The value comes from the page or it is
            reported missing, and every row says which label matched and where
            the value sat relative to it -- so a wrong answer is visible as a
            wrong answer rather than a confident number.
            """
            wanted = list(chosen or []) + split_fields(extra)
            if not (text or "").strip():
                return (gr.update(visible=False),
                        hint("Run a document first: this searches the text in Result."))
            if not wanted:
                return (gr.update(visible=False),
                        hint("Choose a document type, or type the field names you want."))
            rows, missing, odd = [], 0, 0
            for r in field_search.find_fields(text, wanted):
                if not r["found"]:
                    missing += 1
                elif not r["shape_ok"]:
                    odd += 1
                where = r["evidence"].split("[")[-1].rstrip("]") if r["evidence"] else ""
                if not r["found"]:
                    verdict = ""
                elif r.get("guessed"):
                    verdict = "guess"
                elif r["shape_ok"]:
                    verdict = "yes"
                else:
                    verdict = "CHECK"
                rows.append([r["field"], r["value"] or "not found", verdict, where])
            parts = [f"{len(rows) - missing} of {len(rows)} found."]
            if missing:
                parts.append(f"{missing} label(s) not printed on the page.")
            if odd:
                parts.append(f"{odd} value(s) marked CHECK: found, but not the shape "
                             f"the field expects, often a table that has slipped a row.")
            return gr.update(value=rows, visible=True), hint(" ".join(parts))

        find_btn.click(pull_fields, inputs=[text_out, fields_cb, extra_in],
                       outputs=[fields_out, fields_note])

        save_btn.click(save_result, inputs=[text_out, pdf_in, image_in],
                       outputs=[files_out, status_out])
        run_btn.click(
            run,
            inputs=[model_dd, image_in, pdf_in, prompt_in, pages_in, dpi_in, batch_in],
            outputs=[status_out, text_out, img_out, html_out],
        )

    return demo

if __name__ == "__main__":
    try:
        _host, _auth, _share = _serve_opts()
        demo = build_demo()
        demo.queue().launch(server_name=_host, server_port=7860,
                            inbrowser=(_host == "127.0.0.1"), auth=_auth,
                            share=_share, theme=THEME, css=CSS)
    finally:
        _stop_ocr_worker()
