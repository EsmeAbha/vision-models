import os
import gc
import html
import json
import itertools
import collections
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
        "cmd": [
            "wsl.exe", "-e", WSL_PADDLE_PYTHON, "-u",
            pdf_pages.win_to_wsl(os.path.join(_here, "paddleocr_worker.py")),
            "--serve",
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
        return gr.update(value=None), "Nothing to save yet - run something first."
    paths = _as_paths(pdf_file)
    if len(paths) > 1:
        src = f"{os.path.splitext(os.path.basename(paths[0]))[0]}_and_{len(paths) - 1}_more"
    else:
        src = (paths[0] if paths else None) or image_path or "output"
    try:
        files = save_output.save_all(str(text), base_name=src, out_dir=OUTPUT_DIR)
    except Exception as e:
        return gr.update(value=None), f"Save failed: {type(e).__name__}: {e}"
    names = "\n".join("  " + f for f in files)
    return gr.update(value=files), f"Saved {len(files)} file(s):\n{names}"


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
.gradio-container{max-width:1500px!important;font-synthesis:none;font-size:14px}
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
.gradio-container .block,.gradio-container .form{background:#fff;
  border:1px solid #e2e7df!important;border-radius:10px;padding:14px}
.gradio-container .form>.block,.gradio-container .form .block{border:0!important;
  padding:0}
.gradio-container .form{padding:0;overflow:hidden}
.gradio-container .block.padded{padding:14px}
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
.vm-promptbar{margin-top:22px;padding:14px;background:#fafbf7;
  border:1px solid #e2e7df;border-radius:10px;align-items:flex-end;gap:12px}
.vm-promptbar .block{border:0!important;background:transparent;padding:0}
.vm-promptbar button{min-height:46px}
.vm-page{padding:4px 2px}
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
    <small>RTX 5080 &middot; nothing leaves the machine</small></div>
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


def build_demo():
    """Construct the UI.

    Deliberately not executed at import. Other modules import this one to
    reuse the OCR worker (ensure_model / _ocr_request_batch / stop_vllm),
    and building Gradio components at import time blew up inside a live
    request from another app with 'Dropdown' object has no attribute '_id'.
    """
    with gr.Blocks(title="Local Vision Models - RTX 5080") as demo:
        with gr.Sidebar(width=244, elem_classes="vm-side"):
            gr.HTML(BRAND_HTML)
            nav = gr.Radio(NAV_PAGES, value=NAV_PAGES[0], show_label=False,
                           container=False, elem_classes="vm-nav")
            gr.HTML(SIDEBAR_FOOT_HTML)

        # Every page is built, and the nav only changes which one is visible.
        # Nothing below is re-created on a click, so a run in progress is not
        # disturbed by looking at another page.
        with gr.Column(visible=True, elem_classes="vm-page") as page_dashboard:
            gr.Markdown(
                "# Local OCR / VLM\n"
                "Runs entirely on your RTX 5080. Pick a model, upload an image **or a "
                "PDF**, hit Run. PDFs are processed page by page and stream in live.\n"
                "Switching models unloads the previous one to stay within 16GB VRAM."
            )
            with gr.Row():
                with gr.Column():
                    model_dd = gr.Dropdown(
                        choices=list(MODELS.keys()),
                        value="PaddleOCR-VL (document OCR)",
                        label="Model",
                    )
                    image_in = gr.Image(type="filepath", label="Upload image")
                    pdf_in = gr.File(label="...or upload PDFs (several is fine)",
                                     file_types=[".pdf"], file_count="multiple")
                    with gr.Row():
                        pages_in = gr.Textbox(label="Pages", value="",
                                              placeholder="all, or 1-3 / 1,4,7", scale=1)
                        dpi_in = gr.Slider(150, 600, value=300, step=50,
                                           label="Render DPI", scale=2)
                    batch_in = gr.Slider(1, 16, value=4, step=1,
                                         label="Pages per vLLM batch (higher = faster, "
                                               "coarser live updates)")
                    with gr.Row():
                        save_btn = gr.Button("Save output (.md / .html / .xlsx)")
                    files_out = gr.File(label="Download", file_count="multiple")
                with gr.Column():
                    status_out = gr.Textbox(label="Progress (live)", lines=10,
                                            max_lines=10, autoscroll=True)
                    text_out = gr.Textbox(label="Result (raw)", lines=22)
                    img_out = gr.Image(label="Annotated output (OCR grounding, if produced)")
                    html_out = gr.HTML(label="Rendered table (PaddleOCR-VL)", visible=False)

            # The prompt and Run sit at the foot of the page, as one bar. The
            # handler is unchanged: what matters to it is the order of the
            # inputs list below, not where the boxes are drawn.
            with gr.Row(elem_classes="vm-promptbar"):
                prompt_in = gr.Textbox(
                    value=DEFAULT_PROMPTS["paddleocr_vl"],
                    label="Prompt / instruction",
                    lines=3,
                    scale=9,
                )
                run_btn = gr.Button("Run", variant="primary", scale=1)

        with gr.Column(visible=False, elem_classes="vm-page") as page_skills:
            gr.Markdown(
                "# Skills\n"
                "Each skill is one reading model paired with the kind of file it "
                "is for. Choosing one sets the Model and prompt on the Dashboard "
                "and takes you there — the run, and everything it produces, is "
                "unchanged."
            )
            picks = []
            for row_start in (0, 2):
                with gr.Row():
                    for skill in SKILLS[row_start:row_start + 2]:
                        with gr.Column():
                            gr.HTML(skill_card(skill))
                            picks.append((skill, gr.Button("Use this skill")))

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

        model_dd.change(on_model_change, inputs=model_dd, outputs=[prompt_in, img_out, html_out])
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
