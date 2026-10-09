# The OCR path: how it fits together, and what broke it

Written after a long debugging session on 2026-10-07. The path works again;
this records what it is, what actually broke, and — because most of the
session was spent on wrong answers — which explanations are false, so nobody
re-walks them.

## The pieces

A page read crosses two operating systems:

```
  vision_server.py   (Windows, .venv)            the chat page on :7862
      imports app.py, which owns the models
         |
         | ensure_model("paddleocr_vl")
         v
  start_vllm()  ->  wsl.exe bash -lc ~/ocr/start_server.sh
         |              (WSL) vLLM serving PaddleOCR-VL-1.6 on :8000
         |              held as a CHILD PROCESS, or WSL tears the distro down
         v
  load_ocr_worker() -> .venv-paddleocr\Scripts\python.exe paddleocr_worker.py --serve
         |              (WINDOWS) layout detection on the GPU,
         |              recognition sent to :8000
         |
         | one JSON request per line on stdin, ###RESULT_JSON### back
         v
  _ocr_request()  ->  the page
```

**The 16GB card is shared three ways** and that is the crux of the whole
thing:

| | VRAM |
|---|---|
| vLLM at `--gpu-memory-utilization 0.40` | ~6.5GB |
| Paddle's PP-DocLayoutV3 (the worker) | ~2GB |
| Windows desktop | ~1GB |

At `0.62` vLLM leaves ~4GB, and the worker dies with
`fatal : Memory allocation failure`. **Paddle needs VRAM of its own.**

## What actually broke it

### 1. A zero-byte FlashInfer autotune cache

`~/.cache/vllm/flashinfer_autotune_cache/.../autotune_configs.json` was
truncated to 0 bytes by a process killed mid-write, and every vLLM start then
died:

```
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
RuntimeError: Engine core initialization failed. See root cause above.
```

`start_server.sh` now deletes any 0-byte cache before launching. This is what
broke a previously working system, and it masked everything else for hours:
vLLM never got far enough for the real memory problem to appear.

### 2. vLLM was taking too much of the card

`--gpu-memory-utilization` is now **0.40**, leaving ~8.5GB for the worker and
the desktop. This was the actual fault behind the symptom you reported — "it's
supposed to use the VRAM".

### 3. The worker must run on Windows, with the GPU

Two separate mistakes were buried here:

- **Inside WSL, a paddle pipeline takes the whole distro down.** No traceback,
  no faulthandler dump, and the vLLM server dies in the same instant — which
  is why the worker appeared to "kill" vLLM and why stderr was always empty.
  It happens with the GPU *and* when forced to CPU.
- **`CUDA_VISIBLE_DEVICES=''` is wrong here.** Paddle is a GPU build;
  denying it the device is what makes it unstable. Every failing log said so:
  *"You are using GPU version Paddle, but your CUDA device is not set
  properly. CPU device will be used by default."*

`pdf2text.py --ocr` has always run Paddle on Windows with the GPU, which is
why it kept working when the page did not.

### 4. The server's output was thrown away

`start_vllm` ran its child with `stdout=DEVNULL, stderr=DEVNULL`, so a crash
in the first second looked exactly like a slow load: a spinner for 120s, then
"vLLM server did not come up". It now captures the output, fails as soon as
the child exits, and reports the genuine last lines.

### 5. The worker's stderr is also written to a file

`%TEMP%\ocr_worker_stderr.log`. The in-memory tail came back empty on every
failing run, leaving nothing to diagnose from.

### 6. Ollama competes for the same card

`gpt-oss-64k` stays resident on its keep-alive after any chat question and
holds **12.8GB**. With vLLM wanting ~6.5GB they do not fit, and free VRAM
appears to swing between ~3GB and ~14GB depending on when it is measured —
which makes every memory reading inconsistent. CLAUDE.md already says to free
it first; nothing automates it yet.

```
curl -s -X POST http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-oss-64k:latest","keep_alive":0}'
```

## Verified working

```
test_document.png                      done in 70s, 1,684 chars, table parsed
bank_statement_1_digital_multipage.pdf done in 15s, 21,520 chars, 2 pages, 4 tables
```

## Wrong answers, so nobody repeats them

Most of the session went into these. Every one looked well-evidenced at the
time; each was an artefact of something else.

| claim | verdict |
|---|---|
| Paddle and vLLM cannot coexist | **False.** They share the card fine once vLLM leaves room. The "proof" was collected while vLLM was dying of the corrupt cache and while test teardown was killing it. |
| Layout detection should run on CPU | **False, and actively harmful.** A GPU-built paddle denied its device crashes the WSL distro. |
| `--enforce-eager` / `--no-enable-flashinfer-autotune` were phantom fixes | **They were real**, but only because the card was over-committed. Worth retesting now that vLLM is at 0.40. |
| The login shell (`bash -lc`) breaks the worker | **False.** Same failure with a plain `env` invocation. |
| The Windows worker is impossible here | **False.** `WinError 1455` (`cublas64_12.dll`, 5.6GB pagefile) was measured while Ollama held 15.85GB of commit. |
| `SSL_CERT_FILE` pointing at the bundle kills the pipeline | **True in WSL** (6s to build without it, dead in 2s with it) — but moot now the worker runs on Windows, where the bundle is correct and wanted. |

## A trap still in app.py

`stop_vllm()` ends with

```python
subprocess.run(["wsl.exe","-e","bash","-lc","pkill -f 'vllm serve'"])
```

a **global** kill, registered with `atexit`. Any Python process that imports
`app.py` therefore kills every vLLM server on the machine when it exits —
including one another session is using. `test_chat_context.py` imports
`vision_server`, which imports `app.py`, so **running the test suite stops a
running OCR server.** This produced a large share of the contradictory
evidence in this session. It should kill only its own child.

## Worth doing

1. Free Ollama's model in `start_vllm` before claiming the card.
2. Make `stop_vllm` kill only its own child.
3. Fix the worker error message: distinguish "exited, rc=N" from "no reply in
   N seconds". `OCR worker exited unexpectedly` is raised when stdout goes
   quiet, and has been shown while the worker was still alive.
4. Retest `--enforce-eager` and `--no-enable-flashinfer-autotune` at 0.40;
   dropping them would restore some speed if the card now has room.
