#!/usr/bin/env python3
"""OpenBeast agent-spawn router — a thin proxy in front of llama-server.

Solves the verified problem (docs/RESEARCH_FINDINGS §8-11): local models won't
reliably call start_agent on their own. Instead of trusting the model's
judgment, the router intercepts each chat turn, DETECTS a spawn-intent with a
grammar-constrained pre-flight classification, and on a hit spawns the agent
DIRECTLY — then tells the user. Everything else passes through untouched, with
the model's normal thinking-on behavior.

Flow for POST /v1/chat/completions:
  0. Identity gate (docs/RBAC_PLAN.md Phase 2). Open WebUI forwards the
     caller's role in X-OpenWebUI-User-Role when
     ENABLE_FORWARD_USER_INFO_HEADERS=true (set in docker-compose.yml):
       - role == "admin"            -> spawn path enabled (steps 1-3 below).
       - role present, != "admin"   -> prefilter+classify SKIPPED entirely:
         guest turns get zero added latency and can never spawn; the request
         passes through transparently (step 4).
       - role header ABSENT         -> spawn allowed (fail-open) because
         single-user / no-auth setups send no identity headers. Hardened
         multi-user installs set OPENBEAST_ROUTER_REQUIRE_IDENTITY=true to
         fail CLOSED (absent header -> no spawn).
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

All forwarded X-OpenWebUI-User-* headers also travel UPSTREAM on proxied
requests (they're ordinary non-hop-by-hop headers, relayed by _proxy_through).

Env:
  OPENBEAST_ROUTER_PORT      listen port (default 8088)
  OPENBEAST_LLAMA_UPSTREAM   real llama-server (default http://127.0.0.1:8080)
  OPENBEAST_MCPO_URL         MCPO base for start_agent (default http://127.0.0.1:3001)
  OPENBEAST_ROUTER_REQUIRE_IDENTITY  "true" = no role header, no spawn
                             (default "false": fail-open for single-user installs)
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager

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
# RBAC Phase 2: when the admin MCPO instance is key-protected
# (OPENBEAST_MCPO_ADMIN_KEY set), spawn calls must present the key as a
# Bearer token. Empty (Phase 1 keyless MCPO) = no header sent.
_MCPO_KEY = os.environ.get("OPENBEAST_MCPO_ADMIN_KEY", "").strip()
MCPO_HEADERS = {"Authorization": f"Bearer {_MCPO_KEY}"} if _MCPO_KEY else {}
# Identity gate hardening: when "true", a request WITHOUT an
# X-OpenWebUI-User-Role header may never spawn (fail-closed). Default "false"
# keeps single-user/no-auth setups working (WebUI sends no identity headers
# until ENABLE_FORWARD_USER_INFO_HEADERS is on and a user is signed in).
REQUIRE_IDENTITY = (
    os.environ.get("OPENBEAST_ROUTER_REQUIRE_IDENTITY", "false").strip().lower() == "true"
)

# Role header Open WebUI forwards when ENABLE_FORWARD_USER_INFO_HEADERS=true
# (verified in open-webui 0.10.2: env.py FORWARD_USER_INFO_HEADER_USER_ROLE
# defaults to "X-OpenWebUI-User-Role"; utils/headers.py sends the raw role).
_ROLE_HEADER = "x-openwebui-user-role"

# Prefilter: if the last user turn contains NONE of these, skip the classify
# call and pass straight through (normal chat = zero added latency). Tuned for
# PRECISION — only genuine delegation phrasing, because a false positive costs
# a ~500ms classify on the single MTP slot and normal coding chat is full of
# words like "handle"/"yourself"/"while we". We deliberately trade catching
# keyword-free IMPLICIT spawns ("handle this huge thing while I grab coffee")
# for keeping every normal turn fast; explicit requests ("spawn/launch an
# agent", "in the background", "autonomous", "report back") still all fire.
_HINTS = re.compile(
    r"\b(agents?|background|spawn|launch|kick[ -]?off|autonomous|delegate|"
    r"in parallel|meanwhile|report back|check back|don'?t (wait|block))\b",
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


def _spawn_allowed(headers, require_identity=None):
    """Identity gate for the spawn path (docs/RBAC_PLAN.md Phase 2).

    Pure decision function over a headers mapping (Starlette's Headers or a
    plain dict — lookup is case-insensitive either way):
      role == "admin" (any case)  -> True
      role present, != "admin"    -> False  (guests/pending can never spawn)
      role header absent          -> not require_identity
        (fail-open by default for single-user installs that send no identity
         headers; OPENBEAST_ROUTER_REQUIRE_IDENTITY=true flips to fail-closed)
    """
    if require_identity is None:
        require_identity = REQUIRE_IDENTITY
    role = None
    for k, v in headers.items():
        if k.lower() == _ROLE_HEADER:
            role = v
            break
    if role is None:
        return not require_identity
    return role.strip().lower() == "admin"


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
    except Exception as exc:
        logging.debug("classify failed (passing through): %s", exc)
        # any classify failure -> treat as no-spawn (fail safe: never block a turn)
    return False, "", "."


async def _spawn(client, task, workdir):
    """Spawn via the real MCPO start_agent tool. Returns (agent_id, error)."""
    try:
        r = await client.post(f"{MCPO}/start_agent",
                              json={"task": task, "workdir": workdir},
                              headers=MCPO_HEADERS, timeout=30)
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

    # Identity gate FIRST (docs/RBAC_PLAN.md Phase 2): non-admin turns skip
    # prefilter + classify entirely — zero added latency, and no path to
    # start_agent regardless of phrasing. See _spawn_allowed for the rules.
    # Route only genuine user turns that clear the recall prefilter.
    if user_text and _spawn_allowed(request.headers) and _HINTS.search(user_text):
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


async def root(request: Request):
    """A browser hitting the router's root would otherwise see llama-server's
    proxied page (no OpenBeast tools) and look 'broken'. Explain instead."""
    return Response(
        "OpenBeast agent-spawn router — this is a headless API endpoint, not a "
        "web UI. It sits in front of llama-server and answers /v1/... requests.\n\n"
        "There are no tools or chat here by design. Use the Open WebUI at "
        "http://localhost:3000 for chat + tools; it talks to this router "
        "automatically.\n",
        media_type="text/plain")


async def passthrough(request: Request):
    client: httpx.AsyncClient = request.app.state.client
    return await _proxy_through(request, client, await request.body())


@asynccontextmanager
async def _lifespan(app):
    app.state.client = httpx.AsyncClient(timeout=None)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = Starlette(
    routes=[
        Route("/", root, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/{path:path}", passthrough, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
    ],
    lifespan=_lifespan,
)


if __name__ == "__main__":
    import uvicorn
    print(f"OpenBeast router on :{PORT}  ->  upstream {UPSTREAM}  (spawn via {MCPO})")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
