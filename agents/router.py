#!/usr/bin/env python3
"""OpenBeast agent-spawn router — a thin proxy in front of llama-server.

Solves the verified problem (docs/RESEARCH_FINDINGS §8-11): local models won't
reliably call start_agent on their own. Instead of trusting the model's
judgment, the router intercepts each chat turn, DETECTS a spawn-intent with a
grammar-constrained pre-flight classification, and on a hit spawns the agent
DIRECTLY — then tells the user. Everything else passes through untouched, with
the model's normal thinking-on behavior.

Flow for POST /v1/chat/completions:
  1. Recall-oriented keyword prefilter on the last user turn (cheap; skips the
     classify for obviously-non-spawn turns so normal chat stays fast).
  2. If it passes, a grammar-constrained pre-flight call to the SAME upstream
     model with enable_thinking=false + json_schema {spawn,task,workdir}
     (~500ms, proven 16/16). This call opts ITSELF out of thinking; it does not
     affect the user's normal thinking-on turns.
  3. spawn=true  -> POST MCPO /start_agent {task,workdir}; return a synthetic
     assistant reply ("started agent <id>"), honoring the stream flag.
  4. spawn=false -> transparently proxy the ORIGINAL request upstream
     (streaming or not), model behaves exactly as if the router weren't there.
All other paths (/v1/models, /health, GET, non-chat POST) forward transparently.

Env:
  OPENBEAST_ROUTER_PORT      listen port (default 8080)
  OPENBEAST_LLAMA_UPSTREAM   real llama-server (default http://127.0.0.1:8081)
  OPENBEAST_MCPO_URL         MCPO base for start_agent (default http://127.0.0.1:3001)
"""
import json
import os
import re
import uuid

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

# Defaults match the WIRED stack topology (router 8088 in front of llama-server
# 8080) so `python3 agents/router.py` standalone doesn't collide with
# llama-server on 8080. start.sh passes these explicitly anyway.
PORT = int(os.environ.get("OPENBEAST_ROUTER_PORT", "8088"))
UPSTREAM = os.environ.get("OPENBEAST_LLAMA_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
MCPO = os.environ.get("OPENBEAST_MCPO_URL", "http://127.0.0.1:3001").rstrip("/")

# Recall-oriented prefilter: if the last user turn contains NONE of these, it is
# almost certainly not a delegation request, so we skip the classify call and
# pass straight through (keeps normal chat at zero added latency). Broad on
# purpose — a false positive only costs one classify; a false negative would
# miss a spawn. The grammar classify is the real decision-maker.
_HINTS = re.compile(
    r"\b(agent|background|spawn|launch|kick[ -]?off|in parallel|meanwhile|"
    r"while (i|we)|on your own|by yourself|yourself|handle (it|this|the)|"
    r"take care of|don'?t (wait|block)|report back|check back|go ahead and|"
    r"autonomous|delegate)\b",
    re.IGNORECASE,
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "spawn": {"type": "boolean"},
        "task": {"type": "string"},
        "workdir": {"type": "string"},
    },
    "required": ["spawn", "task", "workdir"],
}
_CLASSIFIER_SYS = (
    "You are a routing classifier. spawn=true ONLY if the user asks YOU to "
    "perform a large, self-contained job as a BACKGROUND AGENT that runs on its "
    "own while the conversation continues (spawn/launch/kick off an agent, 'in "
    "the background', 'handle it while I...', 'do the whole X yourself, I'll "
    "check back'). For quick questions, single small edits, explanations, or "
    "questions ABOUT agents/background concepts, spawn=false. When true, write a "
    "clear complete task description for the agent and the working directory "
    "(default '.'). Output only JSON."
)


def _last_user_text(messages):
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):  # OpenAI content-parts form
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


async def _classify(client, user_text):
    """Grammar-constrained {spawn,task,workdir}. Thinking disabled so a
    reasoning model can't burn its budget before emitting the JSON."""
    body = {
        "messages": [
            {"role": "system", "content": _CLASSIFIER_SYS},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0,
        "max_tokens": 400,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "route", "schema": _SCHEMA, "strict": True}},
    }
    # Returns (spawn: bool, task: str, workdir: str). spawn reflects the model's
    # raw decision; the caller decides what to do when spawn=true but the task
    # came back too thin (so we surface it instead of silently passing through).
    try:
        r = await client.post(f"{UPSTREAM}/v1/chat/completions", json=body, timeout=60)
        content = r.json()["choices"][0]["message"].get("content") or ""
        d = json.loads(content)
        if d.get("spawn"):
            return True, (d.get("task") or "").strip(), (d.get("workdir") or ".").strip()
    except Exception:
        pass  # any classify failure -> treat as no-spawn (fail safe: never block a turn)
    return False, "", "."


async def _spawn(client, task, workdir):
    """Spawn via the real MCPO start_agent tool. Returns (agent_id, error)."""
    try:
        r = await client.post(f"{MCPO}/start_agent",
                              json={"task": task, "workdir": workdir}, timeout=30)
        txt = r.text
        try:
            data = r.json()
            txt = data if isinstance(data, str) else json.dumps(data)
        except Exception:
            pass
        m = re.search(r"([0-9]{8}-[0-9]{6}-[0-9a-f]{8})", txt)  # agent-id shape
        return (m.group(1) if m else txt.strip()[:80]), None
    except Exception as e:
        return None, str(e)


def _synthetic(model, text, stream):
    """An OpenAI-shaped assistant reply (the router's own message)."""
    cid = "chatcmpl-router-" + uuid.uuid4().hex[:12]
    if not stream:
        return JSONResponse({
            "id": cid, "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": text}}],
        })

    async def gen():
        first = {"id": cid, "object": "chat.completion.chunk", "model": model,
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]}
        yield f"data: {json.dumps(first)}\n\n"
        done = {"id": cid, "object": "chat.completion.chunk", "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        yield f"data: {json.dumps(done)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# Hop-by-hop headers must not be forwarded in either direction (RFC 7230 §6.1);
# httpx sets its own content-length for a fixed body, so forwarding the client's
# would conflict. content-encoding is deliberately NOT stripped from the
# response: aiter_raw() relays still-encoded bytes, so the header must stay
# truthful (do not switch to aiter_bytes without also stripping it).
_HOP_BY_HOP = {"host", "content-length", "transfer-encoding", "connection"}


async def _proxy_through(request, client, body_bytes):
    """Transparently relay a request upstream, streaming the response back."""
    upstream_url = f"{UPSTREAM}{request.url.path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    req = client.build_request(request.method, upstream_url, content=body_bytes, headers=headers)
    try:
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        # Upstream (llama-server) unreachable — return a clean 502, not a raw 500.
        return JSONResponse(
            {"error": {"message": f"model server unreachable via router: {e}",
                       "type": "upstream_unavailable"}}, status_code=502)

    async def body_iter():
        async for chunk in resp.aiter_raw():
            yield chunk
    hdrs = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP}
    return StreamingResponse(body_iter(), status_code=resp.status_code,
                             headers=hdrs, background=_Closer(resp))


class _Closer:
    """Close the upstream streaming response after the client is served."""
    def __init__(self, resp): self.resp = resp
    async def __call__(self): await self.resp.aclose()


async def chat_completions(request: Request):
    client: httpx.AsyncClient = request.app.state.client
    raw = await request.body()
    try:
        body = json.loads(raw)
    except Exception:
        return await _proxy_through(request, client, raw)  # not JSON we understand

    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    model = body.get("model", "local")
    user_text = _last_user_text(messages)

    # Route only genuine user turns that clear the recall prefilter.
    if user_text and _HINTS.search(user_text):
        spawn, task, workdir = await _classify(client, user_text)
        if spawn and len(task) <= 8:
            # Detected a delegation request but couldn't extract a usable task —
            # surface it rather than silently letting the model answer inline.
            return _synthetic(model,
                "That looks like a request to run something as a background agent, "
                "but I couldn't pin down a clear task for it. Want to rephrase it as "
                "a concrete task, or should I just handle it here inline?", stream)
        if spawn:
            agent_id, err = await _spawn(client, task, workdir)
            if agent_id:
                msg = (f"🦁 Started a background agent (`{agent_id}`) to: {task}\n\n"
                       f"It's running independently in `{workdir}` — ask me to check on "
                       f"it anytime, and we can keep working here in the meantime.")
            else:
                msg = (f"I tried to start a background agent for that, but spawning "
                       f"failed ({err}). I can do it inline instead — want me to proceed?")
            return _synthetic(model, msg, stream)

    return await _proxy_through(request, client, raw)


async def passthrough(request: Request):
    client: httpx.AsyncClient = request.app.state.client
    return await _proxy_through(request, client, await request.body())


from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app):
    app.state.client = httpx.AsyncClient(timeout=None)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = Starlette(
    routes=[
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/{path:path}", passthrough, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
    ],
    lifespan=_lifespan,
)


if __name__ == "__main__":
    import uvicorn
    print(f"OpenBeast router on :{PORT}  ->  upstream {UPSTREAM}  (spawn via {MCPO})")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
