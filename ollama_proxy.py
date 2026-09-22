"""Expose the local Ollama API to the network, behind a password.

Ollama listens on 127.0.0.1 and nothing else can reach it. The usual way to
change that is OLLAMA_HOST=0.0.0.0, which throws the whole API open to every
machine on the network with no password at all -- anyone who can reach the
port can run generations on this GPU, list the models, and pull or delete
them.

So instead it is proxied through the page that is already serving, on the port
that is already open, behind the credentials already configured. Nothing about
the Ollama service changes, and it stays bound to localhost.

Mounted at /ollama, so the whole API keeps its usual shape:

    POST http://<host>:7870/ollama/api/chat            native
    POST http://<host>:7870/ollama/v1/chat/completions  OpenAI-compatible
    GET  http://<host>:7870/ollama/api/tags             list models

The OpenAI-compatible path matters: any client that speaks to OpenAI can be
pointed at /ollama/v1 with any api_key, and will work unchanged.

Destructive routes are refused. A proxy exists so somebody can USE the models,
and there is no reason a remote caller should be able to delete one.
"""
from __future__ import annotations

import os
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# Pulling and deleting change what is on the machine; a caller who only needs
# to run a model does not need them.
BLOCKED = ("api/delete", "api/pull", "api/push", "api/create", "api/copy")

security = HTTPBasic(auto_error=False)
router = APIRouter()


def _check(credentials: HTTPBasicCredentials | None = Depends(security)):
    """Same credentials as the page. No password set means localhost only."""
    user = os.environ.get("VM_USER")
    password = os.environ.get("VM_PASS")
    if not (user and password):
        # Nothing configured: the app is on localhost, so there is nobody to
        # authenticate. Binding wide without credentials is refused earlier.
        return True
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This endpoint needs the same username and password as the "
                   "page.",
            headers={"WWW-Authenticate": "Basic"})
    ok_user = secrets.compare_digest(credentials.username, user)
    ok_pass = secrets.compare_digest(credentials.password, password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong username or password.",
            headers={"WWW-Authenticate": "Basic"})
    return True


@router.api_route("/ollama/{path:path}",
                  methods=["GET", "POST", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request, _ok=Depends(_check)):
    if any(path.startswith(b) for b in BLOCKED):
        return JSONResponse(
            {"error": f"/{path} is not available through this proxy. It "
                      f"changes what is installed on the machine; run it on "
                      f"the machine itself."},
            status_code=403)

    url = f"{OLLAMA}/{path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() in ("content-type", "accept")}

    # No read timeout: a 25B model on a 16GB card answers slowly, and a proxy
    # that gives up before the model does is worse than no proxy.
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
    try:
        req = client.build_request(request.method, url, content=body,
                                   headers=headers,
                                   params=dict(request.query_params))
        upstream = await client.send(req, stream=True)
    except httpx.ConnectError:
        await client.aclose()
        return JSONResponse(
            {"error": f"Ollama is not answering on {OLLAMA}. Is it running?"},
            status_code=502)
    except Exception as e:
        await client.aclose()
        return JSONResponse(
            {"error": f"{type(e).__name__}: {e}"}, status_code=502)

    async def body_stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {k: v for k, v in upstream.headers.items()
                   if k.lower() in ("content-type", "transfer-encoding")}
    return StreamingResponse(body_stream(),
                             status_code=upstream.status_code,
                             headers=passthrough)
