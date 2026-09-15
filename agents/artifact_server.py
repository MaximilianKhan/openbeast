#!/usr/bin/env python3
"""beast-artifact server — the HTTP half (docs/BEAST_ARTIFACT_PLAN.md §2).

Serves model-authored HTML from the artifact store (agents/artifact.py) on
loopback :3004; `tailscale serve --https=8446` publishes it to the tailnet.
This is the repo's first CSP, and the isolation it buys is the whole point of
the service:

  /raw/...   model-authored content. `Content-Security-Policy: sandbox …`
             WITHOUT allow-same-origin gives the document an opaque origin —
             no cookies, no storage, no way to ride the viewer's tailnet
             identity into Open WebUI on :443 — and `connect-src 'none'`
             closes fetch/XHR/WebSocket. Scripts may come only from the four
             CDNs Claude Code allows, so a page written for one system
             renders on the other.
  /a/…, /    OUR shell and gallery. Different, stricter policy: no CDN hosts,
             `frame-ancestors 'none'`, scripts from self only.

Auth (plan §5, as amended by the security model — IDENTITY IS REQUIRED):
  identity  a caller is the LOCAL principal if it presents the locality token
          (`X-OpenBeast-Local` == .run/artifact-local.token, 0600, minted at
          startup — agents/edge.py:412 pattern), otherwise whoever
          `Tailscale-User-Login` says, otherwise ANONYMOUS.
  reads   ANONYMOUS gets 404 on every route but health: no identity, no
          service. With OPENBEAST_ARTIFACT_OPERATORS set (falling back to
          OPENBEAST_CHAT_OPERATORS) the login must also be on that list.
          An unlisted login gets 404, never 403 — a 403 would confirm the
          service exists. A private artifact owned by someone else is 404
          for the same reason, and so is a 405 or a 422: every refusal this
          service makes is the same 404 body, byte for byte.
  writes  POST/PATCH/DELETE need the locality token, checked in MIDDLEWARE
          before the body is read. A phone on the tailnet can view; only the
          rig can publish. Ownership is the principal's — the publish body
          cannot name an owner.
  docs    /docs, /redoc and /openapi.json are OFF: the route table is not
          public information.
  audit   every request → .run/artifact-audit.jsonl (0600):
          {ts, login, route, id, n, outcome, ms}; publish rows add sha256 and
          bytes, never content.

UI templates — agents/artifact_ui/{gallery,shell}.html, loaded at REQUEST
time (edit the HTML, reload the page, no restart) with these exact
placeholders substituted; minimal fallbacks below keep the server working if
the files are absent:

    gallery.html   {{ROWS}}      pre-rendered <a class="card"> rows
                   {{COUNT}}     number of artifacts shown
                   {{VIEWER}}    viewer login, or "" on a single-user rig
    shell.html     {{TITLE}}           artifact title (escaped)
                   {{DESCRIPTION}}     description, may be ""
                   {{ARTIFACT_ID}}     the uuid
                   {{VERSION}}         version being shown, e.g. "3"
                   {{VERSION_OPTIONS}} <option> list for the picker
                   {{RAW_URL}}         same-origin /raw/<id>/v/<n>/ for the iframe
                   {{UPDATED}}         ISO timestamp of the last publish
                   {{VISIBILITY}}      private | tailnet
                   {{SANDBOX}}         (optional) the iframe sandbox attribute,
                                       mirroring the header policy

Env:
  OPENBEAST_ARTIFACT_PORT       listen port          (default 3004)
  OPENBEAST_BIND                bind address         (default 127.0.0.1)
  OPENBEAST_ARTIFACT_OPERATORS  read allowlist, comma-separated logins
  OPENBEAST_CHAT_OPERATORS      fallback allowlist (beast-chat's)
  OPENBEAST_FILES_DIR           workspace root — the store lives under it
  OPENBEAST_RUN_DIR             where the token + audit log go (default .run)
"""
from __future__ import annotations

import base64
import binascii
import hmac
import html as _html
import http.client
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               Response)
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import artifact as store  # noqa: E402

REPO_DIR = os.path.dirname(_HERE)
UI_DIR = os.path.join(_HERE, "artifact_ui")

_HDR_LOGIN = "tailscale-user-login"
_HDR_LOCAL = "x-openbeast-local"

# The ONE unauthenticated route (D12): liveness for doctor.sh, nothing else.
HEALTH_PATH = "/api/artifacts/health"

# Verbs that mutate the store. Only the LOCAL principal may use them (D10),
# and that is decided in middleware, before a body is parsed.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Every refusal is byte-identical (D9): a stranger cannot tell a route that
# exists from one that does not, nor a 405 from a 422 from a real 404.
NOT_FOUND_BODY = {"detail": "Not Found"}

# One constant metric label for everything that matched no route (D11) —
# a raw path here is an attacker-controlled, unbounded metric series.
UNMATCHED_ROUTE = "<unmatched>"

# Who the rig itself is when no allowlist is configured; mirrors
# artifact.default_owner()'s last resort (D3) so a CLI publish on an
# unconfigured rig is readable by the CLI that made it.
LOCAL_LOGIN = "local"

MAX_LIST_LIMIT = 200          # D12: limit=0 must not mean "scan everything"
COUNT_LIMIT = 1_000_000       # operator-only counters, explicitly bounded

# --- the policies ------------------------------------------------------------
# Pinned by tests/test_artifact_server.py. If you weaken either string the
# test fails loudly, on purpose: the isolation IS these headers.

RAW_CSP = (
    "sandbox allow-scripts allow-forms allow-modals allow-popups; "
    "default-src 'none'; "
    "script-src 'unsafe-inline' https://cdnjs.cloudflare.com "
    "https://cdn.jsdelivr.net/npm/ https://cdn.tailwindcss.com "
    "https://code.jquery.com; "
    "style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
)

SHELL_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'self'; object-src 'none'; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)

# The iframe attribute mirrors the header, so the page stays boxed in even if
# a proxy ever strips Content-Security-Policy.
IFRAME_SANDBOX = "allow-scripts allow-forms allow-modals allow-popups"

RAW_HEADERS = {
    "Content-Security-Policy": RAW_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "private, max-age=60",
}

SHELL_HEADERS = {
    "Content-Security-Policy": SHELL_CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}


# --- helpers -----------------------------------------------------------------

def _run_dir() -> str:
    return os.environ.get("OPENBEAST_RUN_DIR", "").strip() or \
        os.path.join(REPO_DIR, ".run")


def _token_path() -> str:
    return os.path.join(_run_dir(), "artifact-local.token")


def _configured_port() -> int:
    try:
        return int(os.environ.get("OPENBEAST_ARTIFACT_PORT", "3004"))
    except (TypeError, ValueError):
        return 3004


def _configured_host() -> str:
    return os.environ.get("OPENBEAST_BIND", "").strip() or "127.0.0.1"


def _read_local_token() -> str:
    try:
        with open(_token_path(), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _health_answers(host: str, port: int, timeout: float = 0.6) -> bool:
    """Is an artifact server already live on this port? (D18)"""
    target = "127.0.0.1" if host in ("", "0.0.0.0", "::", "[::]") else host
    resp = None
    try:
        conn = http.client.HTTPConnection(target, port, timeout=timeout)
        try:
            conn.request("GET", HEALTH_PATH)
            resp = conn.getresponse()
            body = resp.read(256)
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False
    return resp is not None and resp.status == 200 and b'"status"' in body


def _ensure_local_token() -> str:
    """Mint, unless a LIVE server already owns the token file (D18).

    A failed second start used to overwrite the running server's token, so
    scripts/artifact.sh authenticated with a secret nobody honoured and the
    operator got "404 Not Found" for a publish that was really an auth
    failure. main() additionally binds the port BEFORE calling this.
    """
    existing = _read_local_token()
    if existing and _health_answers(_configured_host(), _configured_port()):
        return existing
    return _mint_local_token()


def _mint_local_token() -> str:
    """Shared secret proving the caller can read this box's filesystem.

    Not the peer address: `tailscale serve` reverse-proxies every remote
    caller into 127.0.0.1, so loopback proves nothing (agents/edge.py:412
    learned this the hard way). Regenerated each start, 0600.
    """
    token = uuid.uuid4().hex
    path = _token_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # O_TRUNC keeps an existing file's mode — fchmod explicitly so a
        # token left 0644 by an older run can't stay world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(token)
    except OSError as e:
        print(f"WARNING: could not write {path} ({e}) — local publishing "
              f"(scripts/artifact.sh, the MCP tool) will be refused",
              file=sys.stderr)
    return token


def _operators() -> list[str]:
    """The read allowlist, in configured ORDER: the first entry is who a CLI
    publish belongs to (plan §1, "owner = … the first ARTIFACT_OPERATORS
    entry when published through the CLI"), so a set would be wrong."""
    raw = (os.environ.get("OPENBEAST_ARTIFACT_OPERATORS", "").strip()
           or os.environ.get("OPENBEAST_CHAT_OPERATORS", "").strip())
    out: list[str] = []
    for item in raw.split(","):
        login = item.strip().lower()
        if login and login not in out:
            out.append(login)
    return out


def _esc(value) -> str:
    """HTML-escape, and neutralise BRACES (D16).

    `{` and `}` become entities that render identically, so no escaped value
    can ever spell a `{{PLACEHOLDER}}` — belt to _fill's single-pass braces.
    """
    out = _html.escape("" if value is None else str(value), quote=True)
    return out.replace("{", "&#123;").replace("}", "&#125;")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_template(name: str, fallback: str) -> str:
    """Load agents/artifact_ui/<name> at request time; fall back to the inline
    template so a missing (or half-written) UI file never takes the service
    down — the sibling agent owns those files and may be editing them."""
    try:
        with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
            text = fh.read()
        return text if text.strip() else fallback
    except OSError:
        return fallback


_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _fill(template: str, values: dict) -> str:
    """Substitute every placeholder in ONE pass (D16).

    The old sequential `str.replace` loop re-scanned each inserted value for
    the placeholders that had not run yet: a page whose TITLE was the literal
    text `{{VERSION_OPTIONS}}` had the <option> markup spliced into the
    iframe's title attribute, and the `>` in it closed the <iframe> tag
    BEFORE its src and sandbox attributes — deleting the very sandbox this
    module calls a security control. One pass makes a value inert: whatever
    it contains is output, never input.
    """
    return _PLACEHOLDER_RE.sub(
        lambda m: values.get(m.group(1), m.group(0)), template)


_FALLBACK_GALLERY = """<title>OpenBeast artifacts</title>
<style>
:root{color-scheme:light dark;--fg:#16181d;--bg:#fafbfc;--mut:#5d6470;--line:#e3e6ea}
@media (prefers-color-scheme:dark){:root{--fg:#e8eaed;--bg:#14161a;--mut:#9aa1ad;--line:#282c33}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:52rem;margin:0 auto;padding:1.5rem 1rem 4rem}
h1{font-size:1.25rem;margin:0 0 .25rem}
.sub{color:var(--mut);font-size:.85rem;margin:0 0 1.5rem}
a.card{display:block;padding:.85rem 1rem;margin:0 0 .6rem;border:1px solid var(--line);
border-radius:10px;text-decoration:none;color:inherit}
a.card:hover{border-color:var(--mut)}
.t{font-weight:600}.d{color:var(--mut);font-size:.85rem;margin-top:.15rem}
.m{color:var(--mut);font-size:.75rem;margin-top:.35rem;font-variant-numeric:tabular-nums}
.empty{color:var(--mut);border:1px dashed var(--line);border-radius:10px;padding:2rem;text-align:center}
</style>
<main>
<h1>Artifacts</h1>
<p class="sub">{{COUNT}} published &middot; {{VIEWER}}</p>
{{ROWS}}
</main>
"""

_FALLBACK_SHELL = """<title>{{TITLE}}</title>
<style>
:root{color-scheme:light dark;--fg:#16181d;--bg:#fafbfc;--mut:#5d6470;--line:#e3e6ea}
@media (prefers-color-scheme:dark){:root{--fg:#e8eaed;--bg:#14161a;--mut:#9aa1ad;--line:#282c33}}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif;
display:flex;flex-direction:column}
header{display:flex;gap:.6rem;align-items:center;padding:.5rem .75rem;
border-bottom:1px solid var(--line);flex-wrap:wrap}
header .t{font-weight:600;flex:1 1 12rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
header a,header select{font:inherit;color:var(--mut);background:transparent;
border:1px solid var(--line);border-radius:7px;padding:.2rem .5rem;text-decoration:none}
iframe{flex:1;border:0;width:100%;background:#fff}
@media (prefers-color-scheme:dark){iframe{background:#14161a}}
</style>
<header>
<span class="t" title="{{DESCRIPTION}}">{{TITLE}}</span>
<select id="v" aria-label="version">{{VERSION_OPTIONS}}</select>
<a href="{{RAW_URL}}" target="_blank" rel="noopener">open raw</a>
<a href="/">gallery</a>
</header>
<iframe title="{{TITLE}}" src="{{RAW_URL}}"
 sandbox="allow-scripts allow-forms allow-modals allow-popups"
 referrerpolicy="no-referrer"></iframe>
<script>
document.getElementById('v').addEventListener('change', function (e) {
  location.href = '/a/{{ARTIFACT_ID}}/v/' + encodeURIComponent(e.target.value);
});
</script>
"""


class PublishBody(BaseModel):
    """POST /api/artifacts.

    `html` is the page (UTF-8 text); `html_b64` carries it base64 for callers
    with non-UTF-8 bytes. `files` maps a published path to either a string
    (stored as UTF-8) or {"b64": "..."} for binaries.

    There is deliberately NO `owner` field (D4). Attribution comes from the
    locality-token principal and the login header only — a body that could
    name its own owner let any local writer forge attribution and, by naming
    a login nobody uses, publish a private page no real operator can ever
    see. Unknown fields are ignored, so an older CLI still sending `owner`
    keeps working; the value has no effect.
    """
    html: str | None = None
    html_b64: str | None = None
    title: str | None = None
    description: str | None = None
    favicon: str | None = None
    files: dict | None = None
    artifact_id: str | None = None
    label: str | None = None
    visibility: str = "private"


class PatchBody(BaseModel):
    visibility: str | None = None
    description: str | None = None
    current: int | None = None


def _decode_files(files: dict | None) -> dict:
    out: dict[str, bytes] = {}
    for path, value in (files or {}).items():
        if isinstance(value, str):
            out[str(path)] = value.encode("utf-8")
        elif isinstance(value, dict) and "b64" in value:
            try:
                out[str(path)] = base64.b64decode(value["b64"], validate=True)
            except (binascii.Error, ValueError) as e:
                raise store.ArtifactError(f"bad base64 for {path}: {e}")
        else:
            raise store.ArtifactError(
                f"file {path!r} must be a string or {{'b64': ...}}")
    return out


@dataclass(frozen=True)
class Principal:
    """Who is calling (D1).

    login     the resolved identity, or None for ANONYMOUS.
    local     presented the locality token: this box's own filesystem.
    operator  on the read allowlist (or LOCAL) — may see the route table's
              real errors, /metrics and the detailed health body.

    ANONYMOUS is the least privileged thing there is: it gets 404 on every
    route but health. Before D1 it was the MOST privileged — a caller who
    sent no headers at all read every owner's private artifacts, while a
    caller who named themselves correctly got 404.
    """
    login: str | None
    local: bool
    operator: bool


def _metric_label(value: str) -> str:
    """Prometheus label value escaping (D11): backslash, quote, newline."""
    return (str(value).replace("\\", "\\\\")
            .replace('"', '\\"').replace("\n", "\\n").replace("\r", ""))


# --- app ---------------------------------------------------------------------

def create_app(local_token: str | None = None) -> FastAPI:
    """App factory — reads config at call time so tests can vary env.

    `local_token` lets main() mint the token only AFTER it owns the port
    (D18); passing None keeps the safe default (reuse a live server's token,
    otherwise mint).
    """
    operators = _operators()          # ordered; [0] owns CLI publishes
    operator_set = set(operators)
    if local_token is None:
        local_token = _ensure_local_token()
    audit_path = os.path.join(_run_dir(), "artifact-audit.jsonl")

    metrics_lock = threading.Lock()
    hits: dict = defaultdict(int)        # (route, outcome) -> count
    latency_ms: dict = defaultdict(float)

    # D9: nothing announces the route table. /openapi.json was readable by a
    # stranger and documented the entire write API; /docs pulled unpinned
    # third-party script into the same origin as the viewer shell.
    app = FastAPI(
        title="OpenBeast artifacts",
        version="1.0",
        description="Durable URLs for model-authored HTML "
                    "(see agents/artifact_server.py).",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.state.local_token = local_token
    app.state.operators = operators

    def _flat_404() -> JSONResponse:
        """The single refusal (D9). Same status, same body, same length."""
        return JSONResponse(NOT_FOUND_BODY, status_code=404)

    def audit(entry: dict) -> None:
        try:
            os.makedirs(os.path.dirname(audit_path), exist_ok=True)
            fd = os.open(audit_path,
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # the audit trail must never break a request

    def _labels(request: Request) -> tuple:
        """(metric label, audit route). The METRIC label is a route template
        or one constant (D11) — never attacker text. The audit log may name
        the raw path (it is a file, not an unbounded metric series), capped."""
        route = getattr(request.scope.get("route"), "path", None)
        if route:
            return route, route
        return UNMATCHED_ROUTE, request.url.path[:128]

    def _record(request: Request, status, t0: float,
                extra: dict | None = None) -> None:
        ms = int((time.monotonic() - t0) * 1000)
        metric, route = _labels(request)
        params = request.scope.get("path_params") or {}
        entry = {
            "ts": _now(),
            "login": request.headers.get(_HDR_LOGIN) or None,
            "route": route,
            "id": params.get("artifact_id"),
            "n": params.get("n"),
            "outcome": status,
            "ms": ms,
        }
        entry.update(extra or getattr(request.state, "extra", {}) or {})
        audit(entry)
        outcome = ("error" if status == "error"
                   else "ok" if int(status) < 400 else str(status))
        with metrics_lock:
            hits[(metric, outcome)] += 1
            latency_ms[(metric,)] += ms

    @app.middleware("http")
    async def _gate_audit_meter(request: Request, call_next):
        """Size, identity and write-locality — BEFORE the body is parsed (D10).

        Order matters. (1) An oversized Content-Length dies here, so an
        unauthenticated caller can no longer stream tens of megabytes into
        this process's RAM. (2) The principal is resolved. (3) ANONYMOUS is
        refused everywhere but health (D1), and a non-LOCAL caller is refused
        every write verb — as a flat 404, so the 422 that FastAPI used to
        raise while validating an unauthorised body can no longer confirm
        that the route exists.
        """
        t0 = time.monotonic()
        request.state.extra = {}

        # (1) size first: nothing has read the body yet, and nothing will.
        oversize = False
        raw_len = request.headers.get("content-length")
        if raw_len:
            try:
                oversize = int(raw_len) > store.CAPS["version_bytes"]
            except (TypeError, ValueError):
                oversize = True        # unparseable length: not a request we serve

        # (2) identity.
        principal = resolve_principal(request)
        request.state.principal = principal

        # (3) the gates.
        if oversize:
            deny = "oversize"
        elif principal.login is None and request.url.path != HEALTH_PATH:
            deny = "anonymous"
        elif request.method in WRITE_METHODS and not principal.local:
            deny = "not-local"
        else:
            deny = ""
        if deny:
            _record(request, 404, t0, {"denied": deny})
            return _flat_404()

        # Whoever this is, every store call made while serving the request
        # attributes to them (artifact.default_owner()'s ContextVar). The
        # routes also pass `owner=` explicitly where the store takes it —
        # this is the belt, that is the brace.
        owner_token = store.set_owner_override(principal.login)
        try:
            response = await call_next(request)
        except Exception:
            _record(request, "error", t0, {"outcome": "error"})
            # A 500 announces the route too (D9). The rig and the operators
            # still get the real failure — and the audit row above is written
            # either way, so nothing is swallowed silently.
            if not (principal.local or principal.operator):
                return _flat_404()
            raise
        finally:
            store.reset_owner_override(owner_token)
        _record(request, response.status_code, t0)
        return response

    # --- auth ---------------------------------------------------------------

    def is_local(request: Request) -> bool:
        """Compare BYTES: hmac.compare_digest on str raises TypeError for any
        non-ASCII character, and Starlette decodes headers as latin-1, so one
        hostile 0x80 byte would otherwise become a 500 + traceback."""
        presented = request.headers.get(_HDR_LOCAL, "")
        if not presented or not local_token:
            return False
        return hmac.compare_digest(
            presented.encode("utf-8", "surrogateescape"),
            local_token.encode())

    def resolve_principal(request: Request) -> Principal:
        """Header + token -> Principal (D1). Never raises; never trusts.

        With an allowlist: a listed login is that operator; the locality
        token alone is the first operator (who a CLI publish belongs to);
        anything else — INCLUDING a caller who sent nothing — is anonymous.
        Without an allowlist: identity is still REQUIRED, it is just not
        checked against a list. Anonymous is never a viewer.
        """
        local = is_local(request)
        raw = (request.headers.get(_HDR_LOGIN) or "").strip().lower()
        if operators:
            if raw and raw in operator_set:
                return Principal(login=raw, local=local, operator=True)
            if local:
                return Principal(login=operators[0], local=True, operator=True)
            return Principal(login=None, local=False, operator=False)
        if local:
            # The rig itself. A login header on a local call is the identity
            # server telling us whose call this is.
            return Principal(login=raw or LOCAL_LOGIN, local=True,
                             operator=True)
        if raw:
            return Principal(login=raw, local=False, operator=False)
        return Principal(login=None, local=False, operator=False)

    def principal_of(request: Request) -> Principal:
        p = getattr(request.state, "principal", None)
        return p if isinstance(p, Principal) else resolve_principal(request)

    def viewer_of(request: Request) -> str:
        """The caller's login. Never None: the middleware already turned
        every anonymous request into a 404 (D1), and this is the belt to that
        brace for any route reached another way."""
        login = principal_of(request).login
        if not login:
            raise HTTPException(status_code=404, detail="Not Found")
        return login

    def require_local(request: Request) -> None:
        """Write guard, used as a DEPENDENCY. The middleware already refused
        every non-LOCAL write before the body was read (D10); this stays as
        the second lock on the same door."""
        if not principal_of(request).local:
            # 404, not 403: writes are invisible from the tailnet.
            raise HTTPException(status_code=404, detail="Not Found")

    # --- refusals (D9) ------------------------------------------------------

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        """A stranger gets ONE answer, byte for byte, whatever went wrong.

        A reviewer enumerated the whole route table without credentials:
        405 Method Not Allowed named the verbs a path accepts, and a path
        parameter of the wrong type 422'd with the parameter's name — both
        fire before any route code (and so before any auth check) runs.
        """
        if _trusted(request) and exc.status_code != 404:
            return JSONResponse({"detail": exc.detail},
                                status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
        return _flat_404()

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        if _trusted(request):
            return JSONResponse({"detail": jsonable_encoder(exc.errors())},
                                status_code=422)
        return _flat_404()

    def _trusted(request: Request) -> bool:
        p = principal_of(request)
        return bool(p.local or p.operator)

    def visible_meta(artifact_id: str, viewer: str | None) -> dict:
        try:
            meta = store.get_meta(artifact_id)
        except store.ArtifactError:
            meta = None
        if not meta or not store.can_view(meta, viewer):
            # Someone else's private artifact is indistinguishable from one
            # that does not exist.
            raise HTTPException(status_code=404, detail="Not Found")
        return meta

    def owner_for(request: Request) -> str:
        """Who a publish belongs to (D3/D4): the resolved principal, full
        stop. The body has no say — it never sees an `owner` field again.
        Writes are LOCAL-only, so this is never None."""
        return principal_of(request).login or LOCAL_LOGIN

    def _version_or_current(meta: dict, n) -> int:
        known = [int(v.get("n", 0)) for v in meta.get("versions", [])]
        if n is None:
            cur = int(meta.get("current") or (max(known) if known else 0))
            if cur not in known:
                raise HTTPException(status_code=404, detail="Not Found")
            return cur
        try:
            wanted = int(n)
        except (TypeError, ValueError):
            raise HTTPException(status_code=404, detail="Not Found")
        if wanted not in known:
            raise HTTPException(status_code=404, detail="Not Found")
        return wanted

    # --- gallery + shell ----------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def gallery(request: Request):
        viewer = viewer_of(request)
        rows = store.list_artifacts(viewer=viewer, limit=MAX_LIST_LIMIT)
        cards = []
        for r in rows:
            fav = f"{_esc(r['favicon'])} " if r.get("favicon") else ""
            desc = (f'<div class="d">{_esc(r["description"])}</div>'
                    if r.get("description") else "")
            cards.append(
                f'<a class="card" href="/a/{_esc(r["id"])}">'
                f'<div class="t">{fav}{_esc(r["title"])}</div>{desc}'
                f'<div class="m">v{_esc(r["current"])} &middot; '
                f'{_esc(r["versions"])} version(s) &middot; '
                f'{_esc(r["visibility"])} &middot; {_esc(r["updated_at"])}</div>'
                f'</a>')
        body = "\n".join(cards) or \
            '<div class="empty">No artifacts yet. Publish one with ' \
            'scripts/artifact.sh publish page.html</div>'
        page = _fill(_read_template("gallery.html", _FALLBACK_GALLERY), {
            "ROWS": body,
            "COUNT": str(len(rows)),
            "VIEWER": _esc(viewer or "single-user rig"),
        })
        return HTMLResponse(store.wrap_skeleton(page).decode("utf-8"),
                            headers=SHELL_HEADERS)

    def _shell(request: Request, artifact_id: str, n=None) -> HTMLResponse:
        viewer = viewer_of(request)
        meta = visible_meta(artifact_id, viewer)
        version = _version_or_current(meta, n)
        opts = []
        for v in sorted(meta.get("versions", []),
                        key=lambda v: int(v.get("n", 0)), reverse=True):
            vn = int(v.get("n", 0))
            label = f" · {v['label']}" if v.get("label") else ""
            sel = " selected" if vn == version else ""
            opts.append(f'<option value="{vn}"{sel}>v{vn}{_esc(label)} · '
                        f'{_esc((v.get("ts") or "")[:16])}</option>')
        page = _fill(_read_template("shell.html", _FALLBACK_SHELL), {
            "TITLE": _esc(meta.get("title") or "Untitled"),
            "DESCRIPTION": _esc(meta.get("description") or ""),
            "ARTIFACT_ID": _esc(meta.get("id") or artifact_id),
            "VERSION": str(version),
            "VERSION_OPTIONS": "\n".join(opts),
            "RAW_URL": f"/raw/{_esc(meta.get('id') or artifact_id)}/v/{version}/",
            "UPDATED": _esc(meta.get("updated_at") or ""),
            "VISIBILITY": _esc(meta.get("visibility") or "private"),
            "SANDBOX": IFRAME_SANDBOX,
        })
        return HTMLResponse(store.wrap_skeleton(page).decode("utf-8"),
                            headers=SHELL_HEADERS)

    @app.get("/a/{artifact_id}/v/{n}", response_class=HTMLResponse)
    def shell_versioned(request: Request, artifact_id: str, n: int):
        return _shell(request, artifact_id, n)

    @app.get("/a/{artifact_id}", response_class=HTMLResponse)
    def shell_current(request: Request, artifact_id: str):
        return _shell(request, artifact_id)

    # --- raw ----------------------------------------------------------------

    def _raw_response(data: bytes, ctype: str) -> Response:
        headers = dict(RAW_HEADERS)
        return Response(content=data, media_type=ctype, headers=headers)

    # GET + HEAD: `curl -I` and any probe that only wants the headers must
    # see the policy, not a 405.
    @app.api_route("/raw/{artifact_id}/v/{n}/", methods=["GET", "HEAD"])
    def raw_page(request: Request, artifact_id: str, n: int, theme: str = ""):
        viewer = viewer_of(request)
        meta = visible_meta(artifact_id, viewer)
        version = _version_or_current(meta, n)
        try:
            data, _ = store.read_file(meta["id"], version, "index.html")
        except store.ArtifactError:
            raise HTTPException(status_code=404, detail="Not Found")
        request.state.extra = {"bytes": len(data)}
        html = store.wrap_skeleton(
            data, theme=theme if theme in ("dark", "light") else None)
        return _raw_response(html, "text/html; charset=utf-8")

    @app.api_route("/raw/{artifact_id}/v/{n}/{path:path}",
                   methods=["GET", "HEAD"])
    def raw_file(request: Request, artifact_id: str, n: int, path: str):
        viewer = viewer_of(request)
        meta = visible_meta(artifact_id, viewer)
        version = _version_or_current(meta, n)
        if path.strip("/") in ("", "index.html"):
            return raw_page(request, artifact_id, n)
        try:
            data, ctype = store.read_file(meta["id"], version, path)
        except store.ArtifactError:
            raise HTTPException(status_code=404, detail="Not Found")
        request.state.extra = {"bytes": len(data)}
        return _raw_response(data, ctype)

    # --- api ----------------------------------------------------------------

    @app.get(HEALTH_PATH)
    def health(request: Request):
        """Liveness for doctor.sh / healthcheck.sh — the ONE route an
        anonymous caller may reach, and it says the minimum (D12).

        It used to hand a stranger the absolute store path, the auth mode and
        a count of EVERYONE's artifacts, and it listed the whole store to get
        that count: 48 ms of CPU per unauthenticated hit at five thousand
        artifacts. Detail is now operator/LOCAL only, and the scan with it.
        """
        if not _trusted(request):
            return {"status": "ok"}
        try:
            root = store.store_root()
            count = len(store.list_artifacts(limit=COUNT_LIMIT))
            ok = os.path.isdir(root)
        except Exception as e:
            return {"status": "error", "detail": str(e)}
        return {"status": "ok" if ok else "error", "artifacts": count,
                "auth": "allowlist" if operators else "identified",
                "store": root}

    @app.get("/api/artifacts")
    def api_list(request: Request, limit: int = 25, owner: str = ""):
        viewer = viewer_of(request)
        # D12: clamp. `limit=0` meant "no limit" in the store, so the cheapest
        # possible query was also the most expensive one to serve.
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        rows = store.list_artifacts(owner=owner or None, viewer=viewer,
                                    limit=limit)
        return {"artifacts": rows, "count": len(rows), "viewer": viewer}

    @app.get("/api/artifacts/{artifact_id}")
    def api_get(request: Request, artifact_id: str):
        viewer = viewer_of(request)
        meta = visible_meta(artifact_id, viewer)
        out = dict(meta)
        out["url"] = store.artifact_url(meta["id"])
        out["versions"] = [
            dict(v, url=store.artifact_url(meta["id"], v.get("n")))
            for v in meta.get("versions", [])]
        return out

    @app.post("/api/artifacts", status_code=201)
    def api_publish(request: Request, body: PublishBody,
                    _local: None = Depends(require_local)):
        if body.html_b64:
            try:
                page = base64.b64decode(body.html_b64, validate=True)
            except (binascii.Error, ValueError) as e:
                raise HTTPException(status_code=400,
                                    detail=f"bad base64 html: {e}")
        elif body.html is not None:
            page = body.html.encode("utf-8")
        else:
            raise HTTPException(status_code=400,
                                detail="html or html_b64 is required")
        try:
            files = _decode_files(body.files)
            result = store.publish(
                page, title=body.title, description=body.description,
                favicon=body.favicon, files=files,
                artifact_id=body.artifact_id, label=body.label,
                visibility=(body.visibility or "private"),
                owner=owner_for(request))
        except store.ArtifactError as e:
            raise HTTPException(status_code=400, detail=str(e))
        meta = store.get_meta(result["id"]) or {}
        version = next((v for v in meta.get("versions", [])
                        if int(v.get("n", 0)) == result["version"]), {})
        request.state.extra = {"sha256": version.get("sha256"),
                               "bytes": result.get("bytes")}
        return result

    @app.patch("/api/artifacts/{artifact_id}")
    def api_patch(request: Request, artifact_id: str, body: PatchBody,
                  _local: None = Depends(require_local)):
        try:
            exists = bool(store.get_meta(artifact_id))
        except store.ArtifactError:
            exists = False
        if not exists:
            raise HTTPException(status_code=404, detail="Not Found")
        try:
            meta = None
            if body.visibility is not None:
                # Owner-gated in the store (D5): say WHO is asking rather
                # than letting it guess the rig's first operator.
                meta = store.set_visibility(artifact_id, body.visibility,
                                            owner=owner_for(request))
            if body.description is not None:
                meta = store.set_description(artifact_id, body.description)
            if body.current is not None:
                meta = store.set_current(artifact_id, body.current)
        except store.ArtifactError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if meta is None:
            meta = store.get_meta(artifact_id)
        return {"id": meta["id"], "visibility": meta.get("visibility"),
                "description": meta.get("description"),
                "current": meta.get("current"),
                "url": store.artifact_url(meta["id"])}

    @app.delete("/api/artifacts/{artifact_id}")
    def api_delete(request: Request, artifact_id: str,
                   _local: None = Depends(require_local)):
        try:
            gone = store.remove(artifact_id)
        except store.ArtifactError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not gone:
            raise HTTPException(status_code=404, detail="Not Found")
        return {"id": artifact_id, "removed": True}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request):
        """Prometheus text exposition, hand-rolled (no client dep), same as
        the identity tool server. No login labels: cardinality + privacy —
        per-caller detail lives in the audit log.

        Operator or LOCAL only (D11). It was world-readable, and every
        unmatched path became its own metric series named after the raw
        request path: a stranger could forge arbitrary series, and grow this
        dict without bound. Labels are now a route template or the constant
        "<unmatched>", and escaped on the way out.
        """
        if not _trusted(request):
            raise HTTPException(status_code=404, detail="Not Found")
        lines = [
            "# HELP openbeast_artifact_requests_total Requests by route/outcome",
            "# TYPE openbeast_artifact_requests_total counter",
        ]
        with metrics_lock:
            for (route, outcome), n in sorted(hits.items()):
                lines.append(
                    f'openbeast_artifact_requests_total'
                    f'{{route="{_metric_label(route)}",'
                    f'outcome="{_metric_label(outcome)}"}} {n}')
            lines += [
                "# HELP openbeast_artifact_latency_ms_total Cumulative ms by route",
                "# TYPE openbeast_artifact_latency_ms_total counter",
            ]
            for (route,), ms in sorted(latency_ms.items()):
                lines.append(
                    f'openbeast_artifact_latency_ms_total'
                    f'{{route="{_metric_label(route)}"}} {ms:.0f}')
        try:
            lines += [
                "# HELP openbeast_artifacts_stored Artifacts in the store",
                "# TYPE openbeast_artifacts_stored gauge",
                f"openbeast_artifacts_stored "
                f"{len(store.list_artifacts(limit=COUNT_LIMIT))}",
            ]
        except Exception:
            pass
        return "\n".join(lines) + "\n"

    return app


def main() -> None:
    """Bind the port FIRST, then mint the token (D18).

    A second `start.sh` used to build the app — and so overwrite
    .run/artifact-local.token — before uvicorn discovered the port was taken.
    The live server kept serving with the old secret, and scripts/artifact.sh
    published with the new one and was told "404 Not Found", which is not
    what happened. Now a start that cannot own the port never touches the
    token file.
    """
    import uvicorn
    host = _configured_host()
    port = _configured_port()
    family, stype, proto, _, addr = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM)[0]
    sock = socket.socket(family, stype, proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(addr)
        sock.listen(128)
    except OSError as e:
        sock.close()
        print(f"ERROR: cannot bind {host}:{port} ({e}) — another artifact "
              f"server is probably already running. Its locality token has "
              f"been left alone, so scripts/artifact.sh keeps working.",
              file=sys.stderr)
        raise SystemExit(1)
    app = create_app(local_token=_mint_local_token())
    print(f"OpenBeast artifact server on {host}:{port} "
          f"(store {store.store_root()})")
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
