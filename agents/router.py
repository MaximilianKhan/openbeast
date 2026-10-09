#!/usr/bin/env python3
"""OpenBeast agent-spawn router — a thin proxy in front of llama-server.

Solves the verified problem (docs/RESEARCH_FINDINGS §8-11): local models won't
reliably call start_agent on their own. Instead of trusting the model's
judgment, the router intercepts each chat turn, DETECTS a spawn-intent with a
grammar-constrained pre-flight classification, and on a hit spawns the agent
DIRECTLY — then tells the user. Everything else passes through untouched, with
the model's normal thinking-on behavior.

Flow for POST /v1/chat/completions:
  -1. Is this a request a frontend could have sent? The router holds the
     admin tool key and spends it on the caller's say-so, so it is a deputy
     any web page the operator visits would like to borrow. Three checks,
     before identity is even read:
       - Host is pinned (agents/hostpolicy.py): a DNS name re-pointed at
         127.0.0.1 is refused with a 400 before a route runs.
       - A cross-site browser request (Sec-Fetch-Site: cross-site, or an
         Origin that is not one of our own hosts) is refused with a 403 on
         every POST. WebUI's backend and OpenCode send neither header.
       - The spawn path needs Content-Type: application/json. A page can
         send text/plain cross-site WITHOUT a preflight; it cannot send
         JSON. Anything else is proxied, never classified, never spawned.
     And when the inference server is keyed (LLAMA_API_KEY), the spawn path
     needs that key as the caller's Bearer: every configured frontend already
     sends it, and without it a typed role header (or no identity at all, on
     a single-user rig) is just a local process asking for the admin key.
  0. Identity gate (docs/RBAC_PLAN.md Phase 2). Open WebUI forwards the
     caller's role when ENABLE_FORWARD_USER_INFO_HEADERS=true (set in
     docker-compose.yml) — as the plain X-OpenWebUI-User-Role header, or, in
     signed-identity mode (OPENBEAST_IDENTITY_JWT_SECRET set), ONLY inside
     the HS256 X-OpenWebUI-User-Jwt (open-webui utils/headers.py returns the
     JWT *instead of* the plain headers). The router verifies that token with
     the same secret/issuer as the identity tool server:
       - role == "admin"            -> spawn path enabled (steps 1-3 below).
       - role present, != "admin"   -> prefilter+classify SKIPPED entirely:
         guest turns get zero added latency and can never spawn; the request
         passes through transparently (step 4). A forged/expired JWT counts
         here too.
       - no identity at all         -> spawn allowed (fail-open) ONLY on a
         single-user rig. The gate fails CLOSED when
         OPENBEAST_ROUTER_REQUIRE_IDENTITY=true, when WebUI auth is on
         (OPENBEAST_WEBUI_AUTH=true), or when signed identity is configured:
         those are the multi-user rigs, and on the JWT one every WebUI turn
         arrives with no plain role header at all — header-only gating used
         to read every guest turn as "anonymous" and hand it the admin key.
     A plain role header is IGNORED in JWT mode (WebUI never sends one there,
     so one that arrives was typed by somebody).
  1. Recall-oriented keyword prefilter on the last user turn (cheap; skips the
     classify for obviously-non-spawn turns so normal chat stays fast).
  2. If it passes, a grammar-constrained pre-flight call to the SAME upstream
     model with enable_thinking=false + json_schema {spawn,task,workdir}
     (~500ms, proven 16/16). This call opts ITSELF out of thinking; it does not
     affect the user's normal thinking-on turns.
  3. spawn=true  -> POST MCPO /start_agent {task,workdir}; return a synthetic
     assistant reply ("started agent <id>"), honoring the stream flag.
     The caller's identity headers travel WITH the spawn (the JWT in JWT
     mode, the plain X-OpenWebUI-User-*/Chat-Id headers otherwise), so the
     tool server audits the agent under the real account and anchors its
     workdir inside that account's workspace shard.
  4. spawn=false -> transparently proxy the ORIGINAL request upstream
     (streaming or not), model behaves exactly as if the router weren't there.
All other paths (/v1/models, /health, GET, non-chat POST) forward transparently.

All forwarded X-OpenWebUI-User-* headers also travel UPSTREAM on proxied
requests (they're ordinary non-hop-by-hop headers, relayed by _proxy_through).

Media URLs (every POST): with an mmproj loaded, llama-server DOWNLOADS any
http(s) URL a request names as an image/audio/video part — from loopback,
with none of tools.fetch's SSRF guards. A part may carry its media inline
(a data: URL or raw base64) and nothing else; a request naming a URL is
refused with a 400 here, and so is a POST body that is not JSON, because a
body this proxy cannot read is a body it cannot vet.

Header trust, stated once: in header mode the plain role header is believed
as sent, exactly as the tool server believes it (agents/openapi_tools.py
trust note) — the checks above keep browsers out, the inference key keeps
out a local process that lacks it, and on a rig with tool keys but NO
inference key a local process can still type the header. Signed identity
(scripts/setup-mcpo-keys.sh --with-jwt) is what closes that, here and there.

Env:
  OPENBEAST_ROUTER_PORT      listen port (default 8088)
  OPENBEAST_LLAMA_UPSTREAM   real llama-server (default http://127.0.0.1:8080)
  OPENBEAST_MCPO_URL         MCPO base for start_agent (default http://127.0.0.1:3001)
  OPENBEAST_ROUTER_ALLOWED_HOSTS  extra Host / Origin names to answer to,
                             comma-separated (default: loopback, this
                             machine's name, *.ts.net — agents/hostpolicy.py)
  OPENBEAST_ROUTER_REQUIRE_IDENTITY  "true" = no identity, no spawn. Any
                             other value = automatic: fail-open only when
                             neither of the two below is on.
  OPENBEAST_WEBUI_AUTH       "true" = login wall on -> anonymous turns can't spawn
  OPENBEAST_IDENTITY_JWT_SECRET  signed-identity mode: verify the forwarded
                             JWT (same value WebUI signs with) -> role from it
  ROUTER_CLASSIFY_MODEL      beast-hydra (HYDRA=true, a `classify` route in
                             hydra.toml): the classify call names this model
                             so hydra can place it off the one-slot primary;
                             without that route start.sh names the default
                             route. Unset = the body carries no model, as always.
  OPENBEAST_HYDRA_CALLER_TOKEN_FILE  beast-hydra: the 0600 token the router
                             presents as X-Hydra-Caller on proxied and
                             classify calls, vouching for the WebUI identity
                             headers it forwards. Unset = no header added.
  ROUTER_INSTINCT            off|shadow|enforce (default off): beast-instinct's
                             router.spawn_intent, consulted AFTER the identity
                             gate and BEFORE the classify. Shadow never changes
                             a turn; enforce can only SKIP the classify on a
                             confident "inline" (agents/instinct/routerhook.py).
                             Only hinted turns are scored (the decision runs on
                             this same primary 27B, replacing the classify).
                             Fails open. INSTINCT_URL / INSTINCT_KEY_FILE say
                             where the service answers.

Order on a spawn-candidate turn (the beast-hydra <-> beast-instinct
reconciliation, item 3): identity gate -> instinct (skip-only) -> the
generative classify, unchanged.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
import jwt as pyjwt
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from hydra_caller import HEADER as _HYDRA_CALLER_HEADER
from hydra_caller import CallerToken
from hostpolicy import PinnedHostMiddleware, host_allowed, trusted_hosts
from instinct.routerhook import RouterInstinct

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
# Keyed llama-server (LLAMA_API_KEY): the router's OWN upstream calls (the
# classify probe) must present the bearer. The proxy path is unaffected — it
# forwards the client's Authorization header untouched.
_LLAMA_KEY = os.environ.get("OPENBEAST_API_KEY", "").strip()
UPSTREAM_HEADERS = {"Authorization": f"Bearer {_LLAMA_KEY}"} if _LLAMA_KEY else {}
# Signed identity: the SAME secret Open WebUI signs with
# (FORWARD_USER_INFO_HEADER_JWT_SECRET) and the identity tool server verifies
# with. conf.sh exports it; start.sh's router process inherits it.
JWT_SECRET = os.environ.get("OPENBEAST_IDENTITY_JWT_SECRET", "").strip()
_WEBUI_AUTH = os.environ.get("OPENBEAST_WEBUI_AUTH", "false").strip().lower() == "true"
# Identity gate hardening: when true, a request carrying NO identity may never
# spawn (fail-closed). Explicit "true" forces it; otherwise it turns on by
# itself on any rig with more than one person on it — a WebUI login wall or
# signed identity. It used to default to false everywhere (and conf.sh always
# exports "false"), so on a --with-jwt rig, where WebUI sends the role ONLY
# inside the JWT, every guest turn looked anonymous and spawned with the
# admin key. Single-user/no-auth setups keep fail-open: WebUI sends no
# identity there at all.
# beast-hydra (docs/BEAST_HYDRA_PLAN.md §6.7): both inert unless configured.
CLASSIFY_MODEL = os.environ.get("ROUTER_CLASSIFY_MODEL", "").strip()
_HYDRA_CALLER = CallerToken()
# beast-instinct: reads ROUTER_INSTINCT; "off" (the default, and any unknown
# value) makes zero instinct calls.
_INSTINCT = RouterInstinct()
REQUIRE_IDENTITY = (
    os.environ.get("OPENBEAST_ROUTER_REQUIRE_IDENTITY", "").strip().lower() == "true"
    or _WEBUI_AUTH or bool(JWT_SECRET)
)

# The names this router answers to, and the only Origins it takes a POST from.
ALLOWED_HOSTS = trusted_hosts(os.environ.get("OPENBEAST_ROUTER_ALLOWED_HOSTS", ""))

# Role header Open WebUI forwards when ENABLE_FORWARD_USER_INFO_HEADERS=true
# (verified in open-webui 0.10.2: env.py FORWARD_USER_INFO_HEADER_USER_ROLE
# defaults to "X-OpenWebUI-User-Role"; utils/headers.py sends the raw role).
_ROLE_HEADER = "x-openwebui-user-role"
_JWT_HEADER = "x-openwebui-user-jwt"
# What travels with a spawn so the tool server can attribute + shard it.
_PLAIN_IDENTITY_HEADERS = ("x-openwebui-user-id", "x-openwebui-user-email",
                           _ROLE_HEADER, "x-openwebui-user-name")
_CHAT_HEADER = "x-openwebui-chat-id"

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


def _header(headers, name):
    """Case-insensitive lookup over Starlette Headers or a plain dict."""
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


def _caller_role(headers, jwt_secret=None):
    """The caller's role, or None when the request carries no identity.

    JWT mode (a secret is configured): the role comes ONLY from a verified
    X-OpenWebUI-User-Jwt — same algorithm, issuer and required claims as
    openapi_tools.identity_from. A token that fails verification returns the
    sentinel "invalid" (a non-admin role: it can never spawn). The plain role
    header is ignored: WebUI replaces it with the JWT in this mode, so a plain
    one was written by whoever sent the request.
    Header mode: the plain X-OpenWebUI-User-Role, as sent.
    """
    if jwt_secret is None:
        jwt_secret = JWT_SECRET
    if jwt_secret:
        token = (_header(headers, _JWT_HEADER) or "").strip()
        if not token:
            return None
        try:
            claims = pyjwt.decode(token, jwt_secret, algorithms=["HS256"],
                                  issuer="open-webui",
                                  options={"require": ["exp", "sub"]})
        except pyjwt.PyJWTError as exc:
            logging.warning("router: rejected identity token: %s", exc)
            return "invalid"
        role = claims.get("role")
        return role if isinstance(role, str) else "invalid"
    return _header(headers, _ROLE_HEADER)


def _spawn_allowed(headers, require_identity=None, jwt_secret=None):
    """Identity gate for the spawn path (docs/RBAC_PLAN.md Phase 2).

    Pure decision function over a headers mapping (Starlette's Headers or a
    plain dict — lookup is case-insensitive either way):
      role == "admin" (any case)  -> True
      role present, != "admin"    -> False  (guests/pending/forged tokens
                                             can never spawn)
      no identity                 -> not require_identity
        (fail-open only on single-user installs that send no identity; see
         REQUIRE_IDENTITY for when it hardens on its own)
    The role is read from the verified JWT in signed-identity mode and from
    the plain header otherwise — see _caller_role.
    """
    if require_identity is None:
        require_identity = REQUIRE_IDENTITY
    role = _caller_role(headers, jwt_secret)
    if role is None:
        return not require_identity
    return role.strip().lower() == "admin"


def _cross_site(headers, allowed=None):
    """True when a browser says this request came from somebody else's page.

    Sec-Fetch-Site is the browser's own verdict; Origin covers the browsers
    that do not send it. An Origin is ours when its host is on the Host
    allowlist (WebUI on localhost:3000 calling localhost:8088 is cross-ORIGIN
    and perfectly fine); `null` — a sandboxed frame, a file — is nobody's.
    No such header at all is a non-browser caller: WebUI's backend, OpenCode,
    curl. Those are the frontends, and they are not what this stops.
    """
    if allowed is None:
        allowed = ALLOWED_HOSTS
    if (_header(headers, "sec-fetch-site") or "").strip().lower() == "cross-site":
        return True
    origin = _header(headers, "origin")
    if origin is None:
        return False
    return not host_allowed(urlsplit(origin.strip()).netloc, allowed)


def _cross_site_refusal():
    return JSONResponse(
        {"error": {"message": "cross-site request refused: the OpenBeast "
                   "router takes requests from its own frontends, not from "
                   "another site's page. If this page is yours, serve it from "
                   "a host named in OPENBEAST_ROUTER_ALLOWED_HOSTS.",
                   "type": "cross_site_refused"}}, status_code=403)


def _content_type(headers):
    return (_header(headers, "content-type") or "").split(";")[0].strip().lower()


def _is_json_request(headers):
    """Content-Type: application/json, parameters aside. The one body type a
    cross-site page cannot send without a preflight this router never grants."""
    return _content_type(headers) == "application/json"


def _presents_inference_key(headers, key=None):
    """True when the caller's Bearer is the inference key, or none is set.

    On a keyed rig every frontend is configured with LLAMA_API_KEY (the proxy
    path forwards it; llama-server and hydra refuse a turn without it), so it
    doubles as proof the caller IS a configured frontend — no second secret
    for the launcher to hand out. Unset = nothing to check, as before.
    Compared as bytes: Starlette decodes headers as latin-1 and
    compare_digest on a non-ASCII str raises.
    """
    if key is None:
        key = _LLAMA_KEY
    if not key:
        return True
    auth = _header(headers, "authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return hmac.compare_digest(token.encode("utf-8", "surrogateescape"),
                               key.encode("utf-8", "surrogateescape"))


class BadBody(ValueError):
    """A POST body the router refuses to forward (-> 400)."""


def _parse_json_body(raw):
    """The body as parsed JSON, or BadBody. FAIL CLOSED, like beast-gate's
    _sanitize_body: llama-server's parser has no depth limit and no digit
    limit, so a body Python cannot parse can still be one it accepts — and
    forwarding it verbatim would carry a media URL straight past the check
    below. Strict UTF-8 for the same reason (llama-server takes nothing else).
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BadBody("request body is not UTF-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as e:
        raise BadBody(f"request body is not valid JSON ({type(e).__name__})")


def _fetchable(value):
    """True when llama-server would go and GET (or open) this media value.

    Its handle_media() downloads anything that starts with "http", reads
    file:// from --media-path, and otherwise decodes the value in place (a
    data: URL or raw base64 — which is how OpenAI's input_audio.data
    arrives). Allowlisted rather than mirrored: inline data never contains a
    colon, so anything with a scheme that is not data: is refused, and so is
    a scheme-less "httpbin.org/x", which curl would fetch as http://.
    """
    if not isinstance(value, str):
        return False
    v = value.lstrip()
    if v[:5].lower() == "data:":
        return False
    return ":" in v or v[:4].lower() == "http"


def _remote_media(body):
    """The first media part that names a URL instead of carrying its data.

    Returns the part's key ("image_url", ...) or None. Walks the whole body,
    not just messages[].content[]: /v1/responses puts the same parts under
    `input`, /v1/messages nests Anthropic image blocks inside tool results,
    and all of them (and their /input_tokens, /apply-template siblings) end
    in the same download. Shapes, from llama.cpp tools/server:
      image_url: {"url": ...}   or, on /v1/responses, image_url: "..."
      input_audio / input_video: {"data": ... | "url": ...}
      {"type": "image", "source": {"type": "url", "url": ...}}   (Anthropic)
    Iterative: the body's depth is the caller's to choose.
    """
    stack = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        for key, val in node.items():
            if key == "image_url":
                found = [val.get("url")] if isinstance(val, dict) else [val]
            elif key in ("input_audio", "input_video") and isinstance(val, dict):
                found = [val.get("data"), val.get("url")]
            elif (key == "source" and isinstance(val, dict)
                  and node.get("type") == "image"):
                found = [val.get("url")]
            else:
                found = ()
            if any(_fetchable(f) for f in found):
                return key
            stack.append(val)
    return None


def _vetted_body(raw, must_parse=True):
    """Parse a POST body and refuse what must not reach llama-server.

    Returns (body, None), or (None, a 400 response). must_parse=False lets a
    body that is not JSON through unread as (None, None) — a multipart
    upload, which no chat route can read as JSON either.
    """
    try:
        body = _parse_json_body(raw)
    except BadBody as e:
        if not must_parse:
            return None, None
        return None, JSONResponse(
            {"error": {"message": f"{e}. The OpenBeast router forwards JSON "
                       "request bodies only.", "type": "invalid_request_error"}},
            status_code=400)
    key = _remote_media(body)
    if key:
        return None, JSONResponse(
            {"error": {"message": f"remote media URL refused in `{key}`: the "
                       "model server would download it from inside the rig. "
                       "Send the media inline as a data: URL "
                       "(data:image/png;base64,...) instead.",
                       "type": "invalid_request_error"}}, status_code=400)
    return body, None


def _identity_headers(headers, jwt_secret=None):
    """The caller's identity, re-sent on the spawn call.

    Without it /start_agent saw an anonymous admin-key call: the audit row
    said user=null and the tool server skipped the per-user workspace shard,
    so the classifier's "." resolved to the tool server's own cwd (the repo).
    JWT mode forwards the signed token (the tool server re-verifies it and
    ignores plain headers); header mode forwards the plain headers. The chat
    id rides along in both, as WebUI sends it.

    Starlette decodes header bytes as latin-1 and httpx encodes str values
    as ASCII, so a non-ASCII value (an internationalized email) would raise
    in client.post and break the spawn. Such values go back out as the
    exact bytes WebUI sent; one that isn't latin-1 at all is dropped.
    """
    if jwt_secret is None:
        jwt_secret = JWT_SECRET
    names = (_JWT_HEADER,) if jwt_secret else _PLAIN_IDENTITY_HEADERS
    out = {}
    for name in names + (_CHAT_HEADER,):
        v = _header(headers, name)
        if not v:
            continue
        if not v.isascii():
            try:
                v = v.encode("latin-1")
            except UnicodeEncodeError:
                continue
        out[name] = v
    return out


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
    if CLASSIFY_MODEL:
        body["model"] = CLASSIFY_MODEL
    headers = dict(UPSTREAM_HEADERS)
    tok = _HYDRA_CALLER.get()
    if tok:
        headers[_HYDRA_CALLER_HEADER] = tok
    # Returns (spawn: bool, task: str, workdir: str). spawn reflects the model's
    # raw decision; the caller decides what to do when spawn=true but the task
    # came back too thin (so we surface it instead of silently passing through).
    try:
        r = await client.post(f"{UPSTREAM}/v1/chat/completions", json=body,
                              headers=headers, timeout=60)
        content = r.json()["choices"][0]["message"].get("content") or ""
        d = json.loads(content)
        if d.get("spawn"):
            return True, (d.get("task") or "").strip(), (d.get("workdir") or ".").strip()
    except Exception as exc:
        logging.debug("classify failed (passing through): %s", exc)
        # any classify failure -> treat as no-spawn (fail safe: never block a turn)
    return False, "", "."


async def _spawn(client, task, workdir, identity=None):
    """Spawn via the real MCPO start_agent tool. Returns (agent_id, error).

    `identity` = the caller's identity headers (_identity_headers), sent
    alongside the admin key so the spawn is attributed and sharded."""
    try:
        r = await client.post(f"{MCPO}/start_agent",
                              json={"task": task, "workdir": workdir},
                              headers={**(identity or {}), **MCPO_HEADERS},
                              timeout=30)
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
    if _HYDRA_CALLER.configured:
        # Ours, never a caller's: the token is what makes hydra trust the
        # identity headers relayed alongside it.
        headers = {k: v for k, v in headers.items() if k.lower() != "x-hydra-caller"}
        tok = _HYDRA_CALLER.get()
        if tok:
            headers[_HYDRA_CALLER_HEADER] = tok
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
    if _cross_site(request.headers):
        return _cross_site_refusal()
    raw = await request.body()
    body, refusal = _vetted_body(raw)
    if refusal is not None:
        return refusal
    if not isinstance(body, dict):
        return await _proxy_through(request, client, raw)  # upstream's 400 to give
    # The spawn path is for a request a configured frontend sent: JSON by
    # Content-Type (what a cross-site page cannot send unasked) and, on a
    # keyed rig, carrying the inference key. Anything else is still a chat
    # turn — proxied, never classified, never spawned.
    frontend = (_is_json_request(request.headers)
                and _presents_inference_key(request.headers))

    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    model = body.get("model", "local")
    user_text = _last_user_text(messages)

    # Identity gate FIRST (docs/RBAC_PLAN.md Phase 2): non-admin turns skip
    # prefilter + classify entirely — zero added latency, and no path to
    # start_agent regardless of phrasing. See _spawn_allowed for the rules.
    # Then beast-instinct (ROUTER_INSTINCT; off by default = no call at all):
    # shadow only records, enforce may only SKIP the classify below. Then,
    # for genuine user turns that clear the recall prefilter, the unchanged
    # generative classify.
    hinted = False
    turn = None
    if user_text and frontend and _spawn_allowed(request.headers):
        hinted = bool(_HINTS.search(user_text))
        # Hinted turns only (routerhook): the decision's engine is this
        # primary, and its one slot belongs to the user's turn.
        turn = await _INSTINCT.consult(user_text, hinted)
        if turn.skip:
            return await _proxy_through(request, client, raw)
    if hinted:
        spawn, task, workdir = await _classify(client, user_text)
        _INSTINCT.classified(turn, spawn)   # paired baseline; fire-and-forget
        if spawn and len(task) <= 8:
            # Detected a delegation request but couldn't extract a usable task —
            # surface it rather than silently letting the model answer inline.
            return _synthetic(model,
                "That looks like a request to run something as a background agent, "
                "but I couldn't pin down a clear task for it. Want to rephrase it as "
                "a concrete task, or should I just handle it here inline?", stream)
        if spawn:
            agent_id, err = await _spawn(client, task, workdir,
                                         _identity_headers(request.headers))
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
    raw = await request.body()
    if request.method == "POST":
        # Same two refusals as the chat route: llama-server answers chat
        # under other names too (/chat/completions, /v1/responses,
        # /v1/messages, ...), so vetting one path would vet nothing.
        if _cross_site(request.headers):
            return _cross_site_refusal()
        if raw.strip():
            multipart = _content_type(request.headers).startswith("multipart/")
            _, refusal = _vetted_body(raw, must_parse=not multipart)
            if refusal is not None:
                return refusal
    return await _proxy_through(request, client, raw)


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
    # First, before any route: a DNS name re-pointed at 127.0.0.1 makes a
    # hostile page same-origin with this port, and same-origin may send JSON
    # and type any header — the role header included.
    middleware=[Middleware(PinnedHostMiddleware, allowed_hosts=ALLOWED_HOSTS,
                           allow_env="OPENBEAST_ROUTER_ALLOWED_HOSTS")],
)


if __name__ == "__main__":
    import sys

    import uvicorn
    print(f"OpenBeast router on :{PORT}  ->  upstream {UPSTREAM}  (spawn via {MCPO})")
    if _MCPO_KEY and not JWT_SECRET and not _LLAMA_KEY:
        # The one case the checks at the top of this file leave open.
        print("  Note: the tool server is keyed, but the router cannot tell a "
              "frontend from any other local process:\n"
              "        a typed X-OpenWebUI-User-Role header is believed. Run "
              "scripts/setup-mcpo-keys.sh --with-jwt\n"
              "        (signed identity) or set LLAMA_API_KEY in openbeast.conf "
              "to close that.", file=sys.stderr)
    # Loopback ALWAYS, deliberately unlike the sibling servers: the spawn path
    # is fail-open on a single-user rig (see REQUIRE_IDENTITY), so honoring a
    # BIND_HOST=0.0.0.0 here would hand agent-spawn to the whole LAN. WebUI
    # reaches it on 127.0.0.1, and start.sh probes 127.0.0.1 for readiness.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
