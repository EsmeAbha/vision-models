"""One Ollama model, on the network, behind an API key.

Ollama has no authentication of any kind. The usual way to reach it from
another machine is OLLAMA_HOST=0.0.0.0, which hands every machine on the
network the whole API with no key at all: anyone who can reach the port can
run generations on this GPU, list what is installed, and pull or delete
models. So Ollama stays bound to localhost and this sits in front of it.

What it gives out is deliberately narrow:

  * a named allowlist of models, from QWEN_API_MODEL. A request for anything
    else is refused by name rather than quietly answered by the wrong model.
  * the inference routes only. The native /api/* surface is not proxied at
    all, so /api/delete and /api/pull are not reachable through here even by
    accident. There is nothing to block because there is nothing exposed.
  * reads only. Nothing routed through here can change what is installed.

    GET  /v1/models                   what this key can use
    POST /v1/chat/completions         OpenAI chat, streaming or not
    POST /v1/completions              OpenAI plain completion
    POST /v1/messages                 Anthropic Messages, streaming or not
    POST /v1/messages/count_tokens    Anthropic token count
    GET  /healthz                     no key needed, says nothing secret

Two API shapes, because the clients differ. Anything that speaks OpenAI takes
base_url http://<host>:11435/v1 with the key as api_key. Claude Code speaks
only the Anthropic Messages API, so /v1/messages is forwarded to Ollama's own
Anthropic-compatible endpoint -- which exists from Ollama 0.14 and is what
makes this work at all.

    QWEN_API_KEY     required to listen anywhere but loopback
    QWEN_API_MODEL   comma-separated; the first is the default when a request
                     names no model. Default "qwen3.8-32k,qwen3.8:27b".
    QWEN_API_HOST    default 127.0.0.1
    QWEN_API_PORT    default 11435
    OLLAMA_URL       default http://127.0.0.1:11434
"""
from __future__ import annotations

import os
import ipaddress
import json
import secrets
import sys

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# An allowlist rather than one name, because the same key has to reach the
# 32k variant Claude Code needs and the stock model everything else uses.
# First one wins when a request names none.
MODELS = [m.strip() for m in os.environ.get(
    "QWEN_API_MODEL", "qwen3.8-32k,qwen3.8:27b").split(",") if m.strip()]
MODEL = MODELS[0] if MODELS else "qwen3.8-32k"
KEY = os.environ.get("QWEN_API_KEY", "")
HOST = os.environ.get("QWEN_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_API_PORT", "11435"))

app = FastAPI(title="qwen api", docs_url=None, redoc_url=None)


def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "")


def _presented(request):
    """The key from either header, because clients differ.

    An OpenAI client sends Authorization: Bearer <key>. Plenty of other
    things send X-API-Key. Accepting both costs nothing and saves the person
    at the other end guessing which one this wants.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-api-key") or "").strip()


def _check_key(request):
    # No key configured means loopback only -- main() refuses to bind wider
    # without one -- and on loopback there is nobody to authenticate.
    if not KEY:
        return
    if not secrets.compare_digest(_presented(request), KEY):
        # 401 with no detail about what was wrong with it. Saying "no key
        # given" versus "wrong key" tells someone probing which half to work
        # on.
        raise HTTPException(401, "a valid API key is required",
                            headers={"WWW-Authenticate": "Bearer"})


def _check_model(name):
    """Refuse anything off the allowlist, and say so plainly.

    Silently rewriting the caller's model to an allowed one would be worse:
    they would get answers from something other than what they asked for and
    have no way to tell.
    """
    if name and name not in MODELS:
        allowed = ", ".join(repr(m) for m in MODELS)
        raise HTTPException(
            404, f"this key serves {allowed} only, not {name!r}. "
                 f"GET /v1/models lists what is available.")


def _same_model(a, b):
    """Whether two Ollama model names mean the same model.

    Ollama reports "qwen3.8-32k:latest" in /api/tags but accepts and echoes
    "qwen3.8-32k" everywhere else. Comparing the two strings directly made
    /healthz report a model that was installed and serving as "not
    installed" -- a false alarm that sends someone looking for a fault that
    is not there, which is worse than no health check at all.
    """
    norm = lambda s: (s or "").split(":")[0] if (s or "").endswith(":latest") \
        else (s or "")
    return norm(a) == norm(b)


@app.get("/healthz")
async def healthz():
    """Liveness, with nothing secret in it -- no key needed."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{OLLAMA}/api/tags")
        tags = [m.get("name") for m in r.json().get("models", [])]
    except Exception:
        return JSONResponse({"ok": False, "ollama": "not answering"}, 503)
    missing = [m for m in MODELS
               if not any(_same_model(m, t) for t in tags)]
    if missing:
        return JSONResponse(
            {"ok": False, "ollama": "up",
             "model": f"not installed: {', '.join(missing)}"}, 503)
    return {"ok": True, "model": MODEL, "models": MODELS}


@app.get("/v1/models")
async def models(request: Request):
    _check_key(request)
    return {"object": "list",
            "data": [{"id": m, "object": "model", "owned_by": "ollama"}
                     for m in MODELS]}


async def _read_payload(request):
    """The request body as a dict, or a 400 that says which it was."""
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "the body is not valid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "the body should be a JSON object")
    return payload


async def _forward(path, payload, streaming):
    """Send one request upstream and hand back whatever comes out.

    Shared by both API shapes, because what has to be right here -- no read
    timeout, an unbuffered relay, closing the client whichever way the
    response ends -- is the same for a chat completion and a message, and
    two copies of it would drift.

    Only the headers Ollama needs are sent. anthropic-version and
    anthropic-beta are deliberately dropped rather than refused: a client
    that sends them is not wrong to, and Ollama has no use for them.
    """
    # No read timeout. A 27B model that does not fit on the card answers
    # slowly, and a proxy that gives up before the model does is worse than
    # no proxy. The connect timeout stays short: Ollama is on this machine,
    # so a slow connect means it is not there at all.
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    try:
        req = client.build_request(
            "POST", f"{OLLAMA}{path}", json=payload,
            headers={"content-type": "application/json"})
        upstream = await client.send(req, stream=streaming)
    except httpx.ConnectError:
        await client.aclose()
        return JSONResponse(
            {"error": {"message": f"Ollama is not answering on {OLLAMA}.",
                       "type": "upstream_unavailable"}}, 502)
    except Exception as e:
        await client.aclose()
        return JSONResponse(
            {"error": {"message": f"{type(e).__name__}: {e}",
                       "type": "upstream_error"}}, 502)

    if not streaming:
        try:
            body = await upstream.aread()
            return JSONResponse(json.loads(body), upstream.status_code)
        except ValueError:
            return JSONResponse(
                {"error": {"message": "upstream did not return JSON",
                           "type": "upstream_error"}}, 502)
        finally:
            await upstream.aclose()
            await client.aclose()

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(), status_code=upstream.status_code,
        media_type="text/event-stream",
        # Without this a proxy in between can hold the whole stream and hand
        # it over at the end, which looks exactly like a hung request.
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


@app.api_route("/v1/chat/completions", methods=["POST"])
@app.api_route("/v1/completions", methods=["POST"])
async def complete(request: Request):
    _check_key(request)
    payload = await _read_payload(request)
    _check_model(payload.get("model"))
    # Pinned rather than defaulted, so a request that omits the model still
    # reaches one this serves.
    payload.setdefault("model", MODEL)
    return await _forward(request.url.path, payload,
                          bool(payload.get("stream")))


@app.post("/v1/messages")
async def messages(request: Request):
    """Anthropic Messages, forwarded to Ollama's own compatible endpoint.

    This route is the whole reason the gateway exists in this shape: Claude
    Code speaks the Messages API and nothing else, so /v1/chat/completions is
    no use to it. Ollama has served /v1/messages since 0.14, which means the
    body can go straight through -- tools, tool_result blocks, system
    prompts and all -- rather than being translated here. A translation layer
    is a thing that silently drops the field nobody tested.
    """
    _check_key(request)
    payload = await _read_payload(request)
    _check_model(payload.get("model"))
    payload.setdefault("model", MODEL)
    return await _forward("/v1/messages", payload,
                          bool(payload.get("stream")))


def _estimate_tokens(payload):
    """Characters over four, when nothing better is available.

    Rough on purpose, and only a fallback. It counts the whole request --
    system prompt, messages and tool definitions -- because for Claude Code
    the tool definitions are most of it, and a count that left them out would
    be wrong by about twenty thousand tokens.
    """
    chars = 0
    for part in ("system", "messages", "tools"):
        if part in payload:
            chars += len(json.dumps(payload[part], ensure_ascii=False))
    return max(1, chars // 4)


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    """Upstream's count if it has one, an estimate if it does not.

    Ollama 0.40 answers 404 here. Rather than pass that back -- which stops
    a client that counts before it sends -- the estimate is returned with a
    flag saying it is one, so a caller can tell a real count from a guess.
    """
    _check_key(request)
    payload = await _read_payload(request)
    _check_model(payload.get("model"))
    payload.setdefault("model", MODEL)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as c:
            r = await c.post(f"{OLLAMA}/v1/messages/count_tokens", json=payload,
                             headers={"content-type": "application/json"})
        if r.status_code == 200:
            return JSONResponse(r.json(), 200)
    except Exception:
        # Fall through to the estimate: a token count is not worth failing a
        # request over.
        pass

    return {"input_tokens": _estimate_tokens(payload), "estimated": True}


def main():
    if not _loopback(HOST) and not KEY:
        # Fail closed. Binding to the network with no key is not a thing to
        # do by accident, so it is refused rather than warned about.
        print("refusing to start.\n"
              f"  QWEN_API_HOST is {HOST!r}, so this would listen beyond this "
              "machine,\n"
              "  but QWEN_API_KEY is not set -- that would put an "
              "unauthenticated\n"
              "  model server on the network. Set a key and start it again.",
              file=sys.stderr)
        raise SystemExit(2)

    import uvicorn
    gate = "API key required" if KEY else "no key (loopback only)"
    print(f"{MODEL} on http://{HOST}:{PORT}/v1  [{gate}]", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
