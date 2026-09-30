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
  /raw/<id>/v/<n>/~<token>/…
             the SAME content under a capability path, and the one the shell's
             iframe uses. An opaque-origin document is cross-origin to
             everything, this server included, so its own supporting files
             (<img src="chart.png">) are cross-site no-cors loads that
             `Cross-Origin-Resource-Policy: same-origin` refuses — measured in
             Chromium: every supporting file ever published failed to load.
             Files under the token path are served `cross-origin`; the token
             (an HMAC over id+version, keyed by .run/artifact-raw.key) is what
             stops a THIRD-PARTY page in the viewer's browser from embedding
             them. The framed artifact itself can read its own token from
             location.href — which unlocks only that version's files, which it
             can already load; it cannot mint a token for another id or
             version. The token is NOT an identity: every gate below still
             applies.
  /a/…, /    OUR shell and gallery. Different, stricter policy: no CDN hosts,
             `frame-ancestors 'none'`, and script-src admits ONLY the sha256
             of each inline block the template ships with (security-3): not
             'self' (a model-authored /raw/…/x.js is same-origin) and not
             'unsafe-inline' (an escaping slip in a template would run).

Auth (plan §5, as amended by the security model — IDENTITY IS REQUIRED):
  identity  a caller is the LOCAL principal if it presents the locality token
          (`X-OpenBeast-Local` == .run/artifact-local.token, 0600, minted at
          startup — agents/edge.py:412 pattern), otherwise whoever
          `Tailscale-User-Login` says, otherwise ANONYMOUS. The header counts
          only from a peer ON THIS HOST — loopback, or the very address the
          connection was made TO (`tailscale serve` dials the bind address
          from this box, so with BIND_HOST on a LAN address its source IS
          that address; security-1). A LAN caller that forges it is
          ANONYMOUS, not the login it named. The reserved rig names ("rig",
          "local") are never accepted from a header.
  admin   the locality token, and the logins in ARTIFACT_ADMINS (else the
          FIRST valid operator-allowlist entry) — see artifact.admins(). An
          admin views and manages every page and may hand one to another
          owner (F-A1). Every other operator stays owner-only (D22).
  reads   ANONYMOUS gets 404 on every route but health: no identity, no
          service. With OPENBEAST_ARTIFACT_OPERATORS set (falling back to
          OPENBEAST_CHAT_OPERATORS) the login must also be on that list.
          An unlisted login gets 404, never 403 — a 403 would confirm the
          service exists. A private artifact owned by someone else is 404
          for the same reason, and so is a 405 or a 422: every refusal this
          service makes is the same 404 body, byte for byte.
  writes  checked in MIDDLEWARE before the body is read. POST (publish)
          needs the locality token: only the rig publishes. PATCH/DELETE
          (lifecycle: pin, tags, visibility, rollback, delete) take the
          locality token OR an identified tailnet reader presenting an
          enrolled device key with the `artifact` scope (F-A2; enroll with
          `scripts/clients.sh enroll phone --scope artifact`) — small bodies
          only, rate limited, and still owner-or-admin in the store.
          Ownership is the principal's — the publish body cannot name an
          owner.
  docs    /docs, /redoc and /openapi.json are OFF: the route table is not
          public information.
  audit   every request → .run/artifact-audit.jsonl (0600):
          {ts, login, method, route, id, n, outcome, ms}; `login` is the
          RESOLVED principal (null when refused), `local` marks the rig, and a
          login header that was presented but not honoured is kept as
          `claimed_login` beside the socket `peer`. Publish rows add id, n,
          owner, sha256 and bytes; PATCH rows name what changed; never
          content.

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
                   {{UPDATED}}         "2026-09-30 05:29 UTC"
                   {{VISIBILITY}}      private | tailnet
                   {{SANDBOX}}         (optional) the iframe sandbox attribute,
                                       mirroring the header policy
                   {{FAVICON_HREF}}    data: URI of the page's emoji icon
                   {{SESSION_LINK}}    "made by session …" link, or ""
                   {{OWNER}}           owner chip text for an admin, or ""
                   {{PINNED}}          "1" or ""
                   {{TAGS}}            comma-separated tags
                   {{CAN_MANAGE}}      "1" when the viewer owns or admins it

Env:
  OPENBEAST_ARTIFACT_PORT       listen port          (default 3004)
  OPENBEAST_BIND                bind address         (default 127.0.0.1)
  OPENBEAST_ARTIFACT_OPERATORS  read allowlist, comma-separated logins
  OPENBEAST_CHAT_OPERATORS      fallback allowlist (beast-chat's)
  OPENBEAST_ARTIFACT_ADMINS     admins (else the first operator) — env or conf
  OPENBEAST_ARTIFACT_RETAIN_DAYS  opt-in retention sweep; 0/unset = off
  OPENBEAST_FILES_DIR           workspace root — the store lives under it
  OPENBEAST_RUN_DIR             where the token + audit log go (default .run)
"""
from __future__ import annotations

import base64
import binascii
import errno
import hashlib
import hmac
import html as _html
import http.client
import ipaddress
import urllib.parse
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
from starlette.middleware.trustedhost import TrustedHostMiddleware

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import artifact as store  # noqa: E402
from hostpolicy import trusted_hosts  # noqa: E402

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

# meta fields the API must never hand back (R6). `owner_webui_id` is PURE
# PROVENANCE — the publishing surface's own id for the same human, kept so an
# operator can trace a page to the WebUI account that made it. It authorises
# nothing (the store half of R6 removed that), and publishing it was the other
# half of the same finding: `api_get` printed it to every reader of a `tailnet`
# page, and on a rig with no allowlist a stranger who presented that string as
# their login was treated as the owner.
PRIVATE_META_FIELDS = frozenset({"owner_webui_id"})

# Who the rig itself is — the owner of every CLI, campaign and background
# publish, whatever the allowlist says (F-A1; artifact.RIG_OWNER). LOCAL_LOGIN
# is its pre-F-A1 spelling, kept because pages owned by it may still be on
# disk until migrate_legacy_owners() runs (at every start).
RIG_LOGIN = store.RIG_OWNER
LOCAL_LOGIN = store.LEGACY_LOCAL_OWNER

# F-A2. The device scope that lets a PHONE manage pages (pin, tags,
# visibility, rollback, delete) — never publish. Same registry and the same
# fail-closed rule as beast-chat's `chat` scope.
DEVICE_SCOPE = "artifact"
# A lifecycle write is a few hundred bytes of JSON. A device key is a secret,
# not a licence to stream the 90 MB publish body into this process.
REMOTE_WRITE_MAX_BYTES = 64 * 1024
REMOTE_WRITES_PER_MIN = 60
# Gallery rows per page (correctness-06). The API keeps MAX_LIST_LIMIT.
GALLERY_PAGE = 100

# The request-size gate. CAPS["version_bytes"] bounds the DECODED version; a
# binary supporting file travels as base64 inside JSON, which inflates it by
# 4/3, so gating the body at the decoded cap refused a legal 50 MB version as
# "oversize" — a flat 404 the CLI then explained as "owned by someone else".
# 4/3 for base64 plus an eighth for JSON escaping; read at REQUEST time, like
# every other cap, so a test (or an operator) that lowers the cap lowers this.
def _max_body_bytes() -> int:
    cap = int(store.CAPS["version_bytes"])
    return cap * 4 // 3 + cap // 8


MAX_LIST_LIMIT = 200          # D12: limit=0 must not mean "scan everything"
COUNT_LIMIT = 1_000_000       # operator-only counters, explicitly bounded

# The four reasons this server refuses a request, and the ONLY values that
# ever reach a metric label or the audit budget's keys (R5). They are set from
# literals in the middleware, and _deny_label() clamps anything else to
# "other", so this stays a bounded set no caller can grow.
DENY_REASONS = frozenset({"oversize", "ambiguous-identity", "anonymous",
                          "not-local", "rate-limited"})
DENY_OTHER = "other"

# D27/R5: how many REFUSED requests an unidentified caller may write into
# .run/artifact-audit.jsonl — PER REASON, PER WINDOW — before the log goes
# counter-only for that reason. ~89 bytes a row with no bound at all was a
# disk-fill primitive for anyone who could reach the port.
#
# R5: the budget used to be one process-lifetime counter shared by every
# reason, which made it an attacker-triggered BLINDING primitive: ~1000
# anonymous GETs (about a second) spent it, and from then on every refusal
# from an untrusted caller went unrecorded for the life of the process —
# including `ambiguous-identity`, the signature D29 was added to catch. Per
# reason, a flood of the cheapest refusal cannot silence the interesting one;
# per window, the log recovers on its own instead of staying blind until the
# next restart.
#
# What is lost past the budget is real and worth stating: the individual
# login, artifact id and path of each suppressed refusal. What SURVIVES is the
# count and the REASON, because the reason is a metric label below.
DENY_AUDIT_ROWS = 1000
DENY_AUDIT_WINDOW_S = 300.0

# storage-04: the same bound for IDENTIFIED callers. Every request from a
# tailnet login appended an uncapped ~330-byte row, so one buggy client
# polling /api/artifacts/health in a tight loop (or a hostile peer) could
# grow artifact-audit.jsonl without bound between rotations. Per login, per
# window (DENY_AUDIT_WINDOW_S); past it that login's rows are counter-only
# (/metrics still counts every request) with one row saying so. Exempt:
# LOCAL callers (this box's own CLI) and successful writes — a publish,
# republish or delete is the row the log exists for, and is never dropped.
LOGIN_AUDIT_ROWS = 2000
# Distinct login buckets kept at once; past it, logins share one overflow
# bucket, so the budget's own memory is bounded too.
LOGIN_AUDIT_BUCKETS = 1024

# --- the policies ------------------------------------------------------------
# Pinned by tests/test_artifact_server.py. If you weaken either string the
# test fails loudly, on purpose: the isolation IS these headers.

# 'unsafe-eval' (browser-4): Alpine.js, Vue's in-DOM templates, Handlebars
# and _.template compile at runtime and rendered NOTHING without it. It grants
# nothing 'unsafe-inline' does not already grant inside this sandbox: the
# page is model-authored code running in an opaque, network-less origin
# either way. allow-popups-to-escape-sandbox (browser-3): an external link
# opened from the page lands on the real site, not a crippled opaque-origin
# copy of it — our OWN pages opened that way still carry this CSP by header.
RAW_CSP = (
    "sandbox allow-scripts allow-forms allow-modals allow-popups "
    "allow-popups-to-escape-sandbox; "
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com "
    "https://cdn.jsdelivr.net/npm/ https://cdn.tailwindcss.com "
    "https://code.jquery.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
)

# Our own UI. script-src is filled per response with the sha256 of each
# inline <script> block the TEMPLATE ships (computed before substitution, so a
# value spliced into the page can never match): security-3. `'none'` when a
# template has no script.
SHELL_CSP_TEMPLATE = (
    "default-src 'none'; "
    "script-src {scripts}; "
    "style-src 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'self'; object-src 'none'; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)


def shell_csp(hashes=()) -> str:
    scripts = " ".join(f"'sha256-{h}'" for h in hashes) or "'none'"
    return SHELL_CSP_TEMPLATE.format(scripts=scripts)


SHELL_CSP = shell_csp()

_SCRIPT_BLOCK_RE = re.compile(r"<script>(.*?)</script>", re.S | re.I)


def inline_script_hashes(template: str) -> list:
    """base64 sha256 of every bare `<script>` block in a TEMPLATE.

    A block carrying a placeholder is NOT hashed: its content changes with
    what is substituted, so it could not be pinned — and it will not run,
    which is the failure mode we want if a template ever does that."""
    out = []
    text = re.sub(r"<!--.*?-->", "", template or "", flags=re.S)
    for m in _SCRIPT_BLOCK_RE.finditer(text):
        body = m.group(1)
        if "{{" in body:
            continue
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        out.append(base64.b64encode(digest).decode("ascii"))
    return out

# The iframe attribute mirrors the header, so the page stays boxed in even if
# a proxy ever strips Content-Security-Policy.
IFRAME_SANDBOX = ("allow-scripts allow-forms allow-modals allow-popups "
                  "allow-popups-to-escape-sandbox")

RAW_HEADERS = {
    "Content-Security-Policy": RAW_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "private, max-age=60",
}

# Supporting files reached through the capability path. `cross-origin` because
# the only legitimate requester — the sandboxed page — IS cross-origin to us
# (see the module docstring); ACAO because module scripts and fonts are
# CORS-mode fetches and the sandbox's Origin is the literal "null". Neither
# header appears on the untokenized routes, which stay `same-origin`.
RAW_FILE_HEADERS = dict(RAW_HEADERS, **{
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Access-Control-Allow-Origin": "*",
})

# Cross-Origin-Opener-Policy. frame-ancestors/X-Frame-Options stop FRAMING,
# not `window.open`, and the identity rides the network (tailscale serve), not
# a cookie — so any site the viewer visits could open /a/<id>/v/<n> in a popup
# and count its frames (`w.length`: the shell has one iframe, the 404 has
# none), learning which private pages and versions exist without reading a
# byte (an XS-Leak). With COOP the opener's handle is severed on EVERY answer
# that can differ — shell, gallery, API and the flat 404 alike, because COOP
# on the 200 alone would make `w.closed` the same oracle. Set by the gate
# middleware on every response except the capability tree (/raw/<id>/v/<n>/~
# <token>/...): a hostile page cannot address it (it cannot read the token),
# and it is where the sandboxed page opens its own files from — a popup out
# of a sandbox is a network error against a COOP response.
COOP_HEADER = ("Cross-Origin-Opener-Policy", "same-origin")

SHELL_HEADERS = {
    "Content-Security-Policy": SHELL_CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    COOP_HEADER[0]: COOP_HEADER[1],
    "Cache-Control": "no-store",
}


def _coop_applies(path: str) -> bool:
    """Everything but the capability tree gets COOP (see COOP_HEADER)."""
    return not (path.startswith("/raw/") and "/~" in path)


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


def _peer_is_loopback(request) -> bool:
    """May this connection's peer assert an identity by HEADER?

    `Tailscale-User-Login` is trustworthy only because `tailscale serve` is
    the one thing that can put it on a request: it strips any client-supplied
    copy and sets its own, and it reaches us from 127.0.0.1. That premise
    holds only while every peer is loopback — and this server binds
    OPENBEAST_BIND, which BIND_HOST=0.0.0.0 or a LAN address takes off the
    box. A LAN host (or a tailnet node dialling 100.x:3004 directly) then
    sent `Host: localhost` + the owner's login and read every private page,
    ARTIFACT_OPERATORS or not. So the header counts only from a loopback
    peer (or a Unix socket, which has no address and is on this box by
    construction); from anywhere else the caller is ANONYMOUS.

    The converse is NOT claimed: a loopback peer proves nothing about who is
    behind the proxy (edge.py:412), which is why writes need the locality
    token and still do. Fail closed on anything that is not an IP literal.
    """
    client = getattr(request, "client", None)
    if client is None:
        return True
    try:
        addr = _unmap(ipaddress.ip_address((client.host or "").split("%", 1)[0]))
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    # security-1: with BIND_HOST on a LAN address, setup-tailscale.sh mounts
    # :8446 at http://<that address>:3004, and tailscaled — on THIS box —
    # connects from that same address (a connection to one of our own
    # addresses is sourced from it). The peer is then this host, just not
    # 127.0.0.1, and every tailnet reader used to become anonymous: 404 for
    # every page, the `tailnet` ones included. A socket whose PEER address
    # equals the address it was ACCEPTED on is on this host — a remote host
    # cannot complete a TCP handshake from our own address — so it is trusted
    # exactly like loopback, and nothing more.
    server = (request.scope.get("server") if hasattr(request, "scope")
              else None) or (None,)
    try:
        local = _unmap(ipaddress.ip_address(
            str(server[0] or "").split("%", 1)[0]))
    except ValueError:
        return False
    return (not local.is_unspecified) and addr == local


def _unmap(addr):
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


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


def _raw_key() -> bytes:
    """The HMAC key behind the /raw capability path. PERSISTED (unlike the
    locality token): it is baked into URLs sitting in open tabs, and rotating
    it on every restart would break their images for no gain. 0600 in .run/;
    if that cannot be written the key lives for this process only.
    """
    path = os.path.join(_run_dir(), "artifact-raw.key")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            got = fh.read().strip()
        if len(got) >= 32:
            try:
                os.chmod(path, 0o600)     # a key left loose by hand stays ours
            except OSError:
                pass
            return got.encode()
    except OSError:
        pass
    key = uuid.uuid4().hex + uuid.uuid4().hex
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key)
    except OSError:
        pass
    return key.encode()


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
<link rel="icon" href="{{FAVICON_HREF}}">
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
 sandbox="allow-scripts allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox"
 referrerpolicy="no-referrer"></iframe>
<script>
document.getElementById('v').addEventListener('change', function (e) {
  var m = /^\/a\/([^/]+)/.exec(location.pathname);
  if (m) { location.href = '/a/' + m[1] + '/v/' + encodeURIComponent(e.target.value); }
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
    # None = "not asked": private on creation, and no "unchanged" notice on a
    # republish (correctness-05).
    visibility: str | None = None
    # Provenance (F-A3): the beast-chat session that published, if any.
    source_session: str | None = None


class PatchBody(BaseModel):
    visibility: str | None = None
    description: str | None = None
    current: int | None = None
    pinned: bool | None = None
    tags: list | None = None
    owner: str | None = None          # admin only (F-A1)


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
    admin: bool = False
    device: str | None = None


def _metric_label(value: str) -> str:
    """Prometheus label value escaping (D11): backslash, quote, newline."""
    return (str(value).replace("\\", "\\\\")
            .replace('"', '\\"').replace("\n", "\\n").replace("\r", ""))


def _deny_label(reason) -> str:
    """The refusal reason, CLAMPED to the four constants (R5).

    It becomes a metric label and a budget key, so it has to be a bounded set
    — the whole point of D11. Every real value is written from a literal in
    the middleware; anything else collapses to "other" rather than minting a
    series (or a budget) an attacker chose the name of. "" means "not a
    refusal", which is its own perfectly good label.
    """
    if not reason:
        return ""
    try:
        known = reason in DENY_REASONS
    except TypeError:            # unhashable: not a label, not a budget key
        known = False
    return reason if known else DENY_OTHER


class DeviceRegistry:
    """Stat-gated reader for .run/clients.json (schema owned by
    scripts/clients.sh). A local reimplementation, like beast-chat's: the
    file format is the contract, and this server must not need a restart
    when the gate changes. (mtime, size, inode) — mtime alone misses two
    writes inside one timestamp tick, and a stale map is a MISSED REVOCATION.
    """

    def __init__(self, path: str):
        self.path = path
        self._stamp = None
        self._by_hash: dict = {}

    def _reload(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError:
            self._by_hash, self._stamp = {}, None
            return
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        if stamp == self._stamp:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return          # half-written: keep serving the last good map
        by_hash = {}
        devices = data.get("devices") if isinstance(data, dict) else None
        for dev in devices if isinstance(devices, list) else []:
            if not isinstance(dev, dict):
                continue
            digest = str(dev.get("key_sha256") or "").strip().lower()
            if digest:
                by_hash[digest] = dev
        self._by_hash, self._stamp = by_hash, stamp

    def lookup(self, key: str, scope: str):
        """The enrolled, un-revoked device holding `scope` for this key, or
        None. Unknown, revoked and unscoped are all None."""
        if not key:
            return None
        self._reload()
        digest = hashlib.sha256(key.encode("utf-8", "surrogateescape")
                                ).hexdigest()
        for key_hash, dev in self._by_hash.items():
            if hmac.compare_digest(key_hash, digest):
                if dev.get("revoked_at"):
                    return None
                scopes = dev.get("scopes")
                if not isinstance(scopes, (list, tuple)):
                    return None        # no field => no scope (fail closed)
                if scope not in {str(x).strip().lower() for x in scopes}:
                    return None
                return dev
        return None


def _favicon_href(favicon) -> str:
    """A data: SVG that draws the page's emoji — the tab icon the docs
    promise (browser-6). The shell CSP already admits img-src data:."""
    glyph = str(favicon or "").strip()[:32] or "\U0001F981"
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
           "<text y='.9em' font-size='90'>"
           + _html.escape(glyph, quote=True) + "</text></svg>")
    return "data:image/svg+xml," + urllib.parse.quote(svg, safe="")


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
    # D24: redirect_slashes=False, because the slash redirect is an oracle.
    # Starlette answers `/api/artifacts/` with a 307 to `/api/artifacts` when
    # nothing matched — from the ROUTER, after the identity middleware and
    # before any route code, so no auth check can suppress it. A caller who
    # is identified but not an operator got 307 for a route that exists and
    # 404 for one that does not, which is the whole route table read out one
    # path at a time. With redirects off, a trailing slash is simply a path
    # that matches nothing: the same flat 404 as any other miss (D9).
    app = FastAPI(
        title="OpenBeast artifacts",
        version="1.0",
        description="Durable URLs for model-authored HTML "
                    "(see agents/artifact_server.py).",
        docs_url=None, redoc_url=None, openapi_url=None,
        redirect_slashes=False,
    )
    app.state.local_token = local_token
    app.state.operators = operators
    raw_key = _raw_key()
    registry = DeviceRegistry(os.path.join(_run_dir(), "clients.json"))
    remote_hits: dict = defaultdict(list)       # device id -> [monotonic]
    remote_lock = threading.Lock()

    def raw_token(artifact_id: str, n: int) -> str:
        """The capability for one version's /raw tree (module docstring)."""
        msg = f"{artifact_id}\x00{int(n)}".encode("utf-8")
        return hmac.new(raw_key, msg, hashlib.sha256).hexdigest()[:32]

    app.state.raw_token = raw_token

    def _flat_404() -> JSONResponse:
        """The single refusal (D9). Same status, same body, same length."""
        return JSONResponse(NOT_FOUND_BODY, status_code=404,
                            headers=dict([COOP_HEADER]))

    # D27/R5. An UNIDENTIFIED caller can mint audit rows two ways — a refusal,
    # and a hit on the one route exempt from the anonymity gate
    # (/api/artifacts/health) — so BOTH get a budget: the first
    # DENY_AUDIT_ROWS *of each bucket, in each window* are written (an
    # operator still sees who was turned away and why), and past that the
    # bucket is counter-only until the window turns over. The original wording
    # here said refusals were the ONLY such row, and the code believed it: the
    # health path fell through to an un-budgeted append. Corrected in the
    # v1.4.0 review, along with capping the row's `login` field, which was the
    # other half — a bounded row COUNT with an unbounded row SIZE is not a
    # bound. The alternative — rotating the file — would have
    # let a flood push the interesting rows out of the log, which is worse
    # than not writing the flood in the first place.
    #
    # Keyed by reason, so exhausting the cheap one (an anonymous GET) cannot
    # blind the expensive one (two identity headers on the same request).
    deny_audit: dict = defaultdict(
        lambda: {"written": 0, "suppressed": 0, "since": time.monotonic()})

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
        # The capability token is scrubbed: a refusal before routing logs the
        # raw path, and 128 characters is room for /raw/<uuid>/v/N/~<token>/.
        return UNMATCHED_ROUTE, re.sub(r"/~[^/]*", "/~…", request.url.path)[:128]

    def _successful_write(request: Request, status) -> bool:
        try:
            return (request.method in WRITE_METHODS
                    and status != "error" and int(status) < 400)
        except (TypeError, ValueError):
            return False

    def _login_bucket(login: str) -> str:
        """Budget key for one login. Buckets whose window has expired are
        pruned before a new one is admitted; past LOGIN_AUDIT_BUCKETS live
        buckets, new logins share one overflow bucket."""
        key = "login:" + login
        with metrics_lock:
            if key in deny_audit:
                return key
            logins = [k for k in deny_audit if k.startswith("login:")]
            if len(logins) >= LOGIN_AUDIT_BUCKETS:
                now = time.monotonic()
                for k in logins:
                    if now - deny_audit[k]["since"] >= DENY_AUDIT_WINDOW_S:
                        del deny_audit[k]
                if sum(1 for k in deny_audit
                       if k.startswith("login:")) >= LOGIN_AUDIT_BUCKETS:
                    return "login:*"
        return key

    def _budgeted_audit(entry: dict, key: str, limit: int) -> None:
        now = time.monotonic()
        with metrics_lock:
            budget = deny_audit[key]
            if now - budget["since"] >= DENY_AUDIT_WINDOW_S:
                budget.update(written=0, suppressed=0, since=now)
            allowed = budget["written"] < limit
            if allowed:
                budget["written"] += 1
            else:
                budget["suppressed"] += 1
            first_drop = (not allowed and budget["suppressed"] == 1)
        if allowed:
            audit(entry)
        elif first_drop:
            audit({"ts": _now(), "route": entry.get("route"),
                   "outcome": entry.get("outcome"),
                   "denied": "audit-budget",
                   "reason": key,
                   "note": f"{limit} {key} rows logged "
                           f"in this {DENY_AUDIT_WINDOW_S:.0f}s window; "
                           f"further {key} rows are counted in "
                           f"/metrics (with their reason) until it turns "
                           f"over"})

    def _record(request: Request, status, t0: float,
                extra: dict | None = None) -> None:
        ms = int((time.monotonic() - t0) * 1000)
        metric, route = _labels(request)
        params = request.scope.get("path_params") or {}
        _p = getattr(request.state, "principal", None)
        resolved = getattr(_p, "login", None)
        resolved = str(resolved)[:128] if resolved else None
        # CAPPED, like the raw path below. Uncapped, this was the biggest row
        # in the file by two orders of magnitude: an 8 KB header produced an
        # 8 KB audit row, so D27's budget bounded the row COUNT while the
        # BYTES stayed unbounded. Review of v1.4.0.
        claimed = (request.headers.get(_HDR_LOGIN) or "")[:128] or None
        entry = {
            "ts": _now(),
            # WHO the server decided this was — not the raw header (browser-7,
            # security-2): a forged, refused header used to be logged as the
            # victim's own login, and every rig write as null.
            "login": resolved,
            "method": request.method,
            "route": route,
            "id": params.get("artifact_id"),
            "n": params.get("n"),
            "outcome": status,
            "ms": ms,
        }
        if getattr(_p, "local", False):
            entry["local"] = True
        if getattr(_p, "device", None):
            entry["device"] = _p.device
        if claimed and claimed.strip().lower()[:128] != (resolved or ""):
            # Presented and NOT honoured: keep what was claimed and where it
            # came from, so a forgery is traceable to its source instead of
            # reading like the named user's own device misbehaving.
            entry["claimed_login"] = claimed
            client = getattr(request, "client", None)
            entry["peer"] = (getattr(client, "host", None) or "")[:64] or None
        entry.update(extra or getattr(request.state, "extra", {}) or {})
        reason = _deny_label(entry.get("denied"))
        # The budget has to cover every row an UNIDENTIFIED caller can mint,
        # not just the refusals. `/api/artifacts/health` is deliberately
        # exempt from the anonymity gate, so a health hit never sets `denied`
        # and used to fall straight through to the un-budgeted `audit(entry)`
        # below — an unauthenticated, unrotated, unbounded append. The
        # invariant asserted a few lines down ("refusals are the one audit row
        # an UNIDENTIFIED caller can mint at will") was simply false. Review
        # of v1.4.0.
        #
        # `reason` stays the METRIC label (still "" for a success, so the
        # bounded label set is unchanged); `budget_key` is the BUDGET bucket.
        _principal = getattr(request.state, "principal", None)
        _login = getattr(_principal, "login", None)
        budget_key = reason or ("anon-success" if _login is None else "")
        if budget_key and not _trusted(request):
            # D27/R5: counter-only past the budget, PER BUCKET and PER
            # WINDOW. The metrics below still count every single refusal and
            # keep its reason as a label, so what a flood costs is the
            # per-request detail (login, id, path) of the refusals it drowns
            # out — not the fact that they happened, and not their reason.
            # One row per window per reason says so, so the operator is never
            # left wondering where the trail went.
            _budgeted_audit(entry, budget_key, DENY_AUDIT_ROWS)
        elif (_login is not None and not getattr(_principal, "local", False)
              and not _successful_write(request, status)):
            # storage-04: an identified login gets its own budget.
            _budgeted_audit(entry, _login_bucket(str(_login)[:128]),
                            LOGIN_AUDIT_ROWS)
        else:
            audit(entry)
        outcome = ("error" if status == "error"
                   else "ok" if int(status) < 400 else str(status))
        with metrics_lock:
            hits[(metric, outcome, reason)] += 1
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
                oversize = int(raw_len) > _max_body_bytes()
            except (TypeError, ValueError):
                oversize = True        # unparseable length: not a request we serve

        # (2) identity.
        principal = resolve_principal(request)
        request.state.principal = principal

        # (3) the gates.
        if oversize:
            deny = "oversize"
        elif _ambiguous_identity(request):
            # D29: two `Tailscale-User-Login` headers is not a request with an
            # identity, it is a request with a QUESTION about its identity —
            # and "take the first" is a silent answer that a proxy chain, a
            # header-injection bug or a hostile client picks for us. Refused
            # everywhere, health included: this one is never a viewer.
            deny = "ambiguous-identity"
        elif principal.login is None and request.url.path != HEALTH_PATH:
            deny = "anonymous"
        elif request.method in WRITE_METHODS and not principal.local:
            deny = _remote_write_refusal(request, principal)
        else:
            deny = ""
        if deny == "rate-limited":
            _record(request, 429, t0, {"denied": deny})
            return JSONResponse({"detail": "too many changes — wait a minute"},
                                status_code=429,
                                headers=dict([COOP_HEADER]))
        if deny:
            _record(request, 404, t0, {"denied": deny})
            return _flat_404()
        principal = request.state.principal       # may carry the device now

        # Whoever this is, every store call made while serving the request
        # attributes to them (artifact.default_owner()'s ContextVar). This is
        # the BELT ONLY: a ContextVar surviving BaseHTTPMiddleware into
        # Starlette's threadpool is the least stable corner of the framework,
        # and R2 found three mutations relying on nothing else — a reviewer
        # removed this one line and deleted another owner's page. Every route
        # that mutates now passes `owner=owner_for(request)` explicitly, and
        # tests/test_artifact_server.py neuters this override to prove it.
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
        if _coop_applies(request.url.path):
            response.headers[COOP_HEADER[0]] = COOP_HEADER[1]
        return response

    # Host pinning, added LAST so it is OUTERMOST (Starlette's add_middleware
    # inserts at position 0, so the last one added runs first). It has to be
    # outside `_gate_audit_meter`: a hostile Host must be refused BEFORE the
    # identity gate reads `Tailscale-User-Login` and before an audit row is
    # written for it.
    #
    # Why this exists (review of v1.4.0): this server had no Host validation
    # at all, while beast-chat — same week, same tailnet publication — had it.
    # A DNS-rebinding page loaded from `http://evil.example:3004/` that then
    # rebinds to 127.0.0.1 becomes SAME-ORIGIN with this server, and
    # same-origin lets it set arbitrary request headers, including the
    # `Tailscale-User-Login` header that is the entire read gate. On a default
    # single-user rig the owner string is the public constant LOCAL_LOGIN, so
    # nothing even had to be guessed: the page could read the gallery and
    # every `private` artifact, which is precisely what docs/BEAST_ARTIFACT.md
    # promises it cannot do. Writes were never reachable (the locality token
    # is a 0600 file a browser cannot read), so this is a read-confidentiality
    # fix. A browser cannot forge `Host`; that is what makes this the fix.
    # The address we BIND is a Host we answer to. With BIND_HOST set to a LAN
    # address, start.sh and healthcheck.sh probe http://<that address>:<port>,
    # which this middleware refused with 400 "Invalid host header" — so the
    # watchdog saw a healthy server as down and restarted it every five
    # minutes. An IP literal is not a rebinding vector: the attack presents
    # the ATTACKER'S hostname as Host, never the address it resolves to.
    #
    # ONLY a literal IPv4 address. The bind string is config, and config must
    # never be able to WIDEN a security list: "*" resolves (getaddrinfo maps it
    # to ::1 here) and Starlette reads "*" in this list as allow-any, which
    # turned Host pinning off. IPv6 is left out deliberately — the middleware
    # takes the host as Host.split(":")[0], so a bracketed v6 Host can never
    # match whatever is listed; that case needs ARTIFACT_ALLOWED_HOSTS.
    bind = _configured_host()
    try:
        bind = str(ipaddress.IPv4Address(bind))
    except ValueError:
        bind = ""
    allowed = trusted_hosts(",".join((
        os.environ.get("OPENBEAST_ARTIFACT_ALLOWED_HOSTS", ""), bind)))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)
    app.state.allowed_hosts = allowed

    # --- auth ---------------------------------------------------------------

    def _device_key(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        return key or (request.headers.get("x-openbeast-device-key")
                       or "").strip()

    def _remote_write_refusal(request: Request, principal: Principal) -> str:
        """"" when a NON-LOCAL write may proceed, else the deny reason.

        F-A2: lifecycle changes from the phone. Publishing (POST) stays the
        rig's alone. PATCH and DELETE need BOTH halves — an identity the
        tailnet vouched for (the reader) AND an enrolled device key carrying
        the `artifact` scope (the device) — and a small body. Everything the
        store then does is still owner-or-admin for THAT login.
        """
        if request.method not in ("PATCH", "DELETE") or not principal.login:
            return "not-local"
        dev = registry.lookup(_device_key(request), DEVICE_SCOPE)
        if dev is None:
            return "not-local"
        raw_len = request.headers.get("content-length")
        if raw_len is None and request.headers.get("transfer-encoding"):
            return "oversize"          # no length, no streaming
        try:
            if raw_len is not None and int(raw_len) > REMOTE_WRITE_MAX_BYTES:
                return "oversize"
        except ValueError:
            return "oversize"
        dev_id = str(dev.get("id") or "device")[:64]
        now = time.monotonic()
        with remote_lock:
            q = [t for t in remote_hits[dev_id] if now - t < 60.0]
            if len(q) >= REMOTE_WRITES_PER_MIN:
                remote_hits[dev_id] = q
                return "rate-limited"
            q.append(now)
            remote_hits[dev_id] = q
        request.state.principal = Principal(
            login=principal.login, local=False, operator=principal.operator,
            admin=principal.admin, device=dev_id)
        return ""

    def _ambiguous_identity(request: Request) -> bool:
        """More than one copy of an identity header (D29).

        Starlette keeps every copy and `headers.get()` returns the first, so
        `Tailscale-User-Login: max@…` + `Tailscale-User-Login: kid@…` used to
        authorise silently as max. Neither header is trustworthy once there
        are two of them.
        """
        for name in (_HDR_LOGIN, _HDR_LOCAL):
            if len(request.headers.getlist(name)) > 1:
                return True
        return False

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
        if _ambiguous_identity(request):
            # Belt to the middleware's brace: a route reached another way
            # still sees ANONYMOUS, never an arbitrarily-chosen login.
            return Principal(login=None, local=False, operator=False)
        local = is_local(request)
        # A login header from an off-box peer is not an identity at all (see
        # _peer_is_loopback): the caller is who the TOKEN says, or nobody.
        raw = ((request.headers.get(_HDR_LOGIN) or "").strip().lower()
               if _peer_is_loopback(request) else "")
        # The rig's own names are never an identity a HEADER can carry. The
        # rig principal owns every CLI/campaign publish, so accepting it from
        # a header would make it a read password — the defect class R6
        # deleted `owner_webui_id` for (a constant printed in the docs is not
        # a credential). Both spellings, in both modes: an allowlist that
        # happened to list "local" must not reopen it either.
        if raw in store.RESERVED_LOGINS:
            raw = ""
        if operators:
            if raw and raw in operator_set:
                return Principal(login=raw, local=local, operator=True,
                                 admin=local or store.is_admin(raw))
            if local:
                # The rig itself (F-A1): ONE stable owner for everything this
                # box publishes, whatever the allowlist says today. It used
                # to be operators[0], so adding the allowlist later stranded
                # every page published before it under "local".
                return Principal(login=RIG_LOGIN, local=True, operator=True,
                                 admin=True)
            return Principal(login=None, local=False, operator=False)
        if local:
            # The rig itself. A login header on a local call is the identity
            # server telling us whose call this is.
            return Principal(login=raw or RIG_LOGIN, local=True,
                             operator=True, admin=True)
        if raw:
            # No allowlist: identity is required but not listed. NOT an
            # admin unless ARTIFACT_ADMINS names it — the first tailnet login
            # to show up is never auto-trusted with the rig's pages (F-A1).
            return Principal(login=raw, local=False, operator=False,
                             admin=store.is_admin(raw))
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

    def require_manager(request: Request) -> None:
        """PATCH/DELETE guard (F-A2): the rig, or a tailnet reader whose
        request carried an `artifact`-scoped device key (the middleware
        resolved it). Second lock on the middleware's door, like the above."""
        p = principal_of(request)
        if not (p.local or (p.device and p.login)):
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
        # A caller holding an enrolled `artifact` device key has proven a
        # secret, not just claimed a name: it may read why its own lifecycle
        # change was refused (a bad tag), like an operator.
        p = principal_of(request)
        return bool(p.local or p.operator or (p.device and p.login))

    def is_admin(request: Request) -> bool:
        p = principal_of(request)
        return bool(p.local or p.admin)

    def visible_meta(artifact_id: str, viewer: str | None,
                     admin: bool = False) -> dict:
        try:
            meta = store.get_meta(artifact_id)
        except store.ArtifactError:
            meta = None
        if not meta or not store.can_view(meta, viewer, admin=admin):
            # Someone else's private artifact is indistinguishable from one
            # that does not exist.
            raise HTTPException(status_code=404, detail="Not Found")
        return meta

    def owner_for(request: Request) -> str:
        """Who a publish belongs to (D3/D4): the resolved principal, full
        stop. The body has no say — it never sees an `owner` field again.
        Every write has an identity, so this is never None."""
        return principal_of(request).login or RIG_LOGIN

    # --- version resolution (D23) -------------------------------------------
    # The store hardened this once (D14) and the server then re-derived it
    # from raw meta in three more places, so four ordinary corruptions — a
    # `versions` that is not a list, entries that are not dicts, an `n` that
    # is not a number, a `current` that points nowhere — turned five routes
    # into an HTTP 500 for the artifact's LEGITIMATE OWNER, which is the
    # failure D14 exists to prevent. Everything below goes through
    # store._resolvable_versions(), which falls back to the vN directories
    # actually on disk, and coerces `current` instead of trusting it.

    def _meta_id(meta, artifact_id: str) -> str:
        """The id to address the store with: meta's own, when it is a valid
        id, else the one the caller asked for. `meta["id"]` was a KeyError
        (and a bad one an ArtifactError) away from a 500 on five routes."""
        aid = meta.get("id") if isinstance(meta, dict) else None
        if isinstance(aid, str):
            try:
                return store._check_id(aid)
            except store.ArtifactError:
                pass
        return artifact_id

    def _known_versions(meta, artifact_id: str) -> list[int]:
        """Every version a reader may address, ascending. Never raises."""
        try:
            return sorted(set(store._resolvable_versions(
                _meta_id(meta, artifact_id), meta)))
        except Exception:
            return []

    def _version_entries(meta) -> dict:
        """{n: version record} for the entries that ARE records — the labels
        and timestamps the picker shows, with the junk dropped."""
        out: dict[int, dict] = {}
        raw = meta.get("versions") if isinstance(meta, dict) else None
        for v in raw if isinstance(raw, list) else []:
            if not isinstance(v, dict):
                continue
            try:
                n = int(v.get("n", 0))
            except (TypeError, ValueError):
                continue
            if n > 0:
                out[n] = v
        return out

    def _current_version(meta, known: list[int]) -> int:
        """`current`, COERCED (D23): a missing, non-numeric or dangling
        pointer resolves to the newest version that actually exists rather
        than 500ing (or 404ing) the owner out of their own page."""
        if not known:
            return 0
        try:
            cur = int(meta.get("current"))
        except (TypeError, ValueError):
            cur = 0
        return cur if cur in known else max(known)

    def _version_or_current(meta: dict, n, artifact_id: str) -> int:
        known = _known_versions(meta, artifact_id)
        if not known:
            raise HTTPException(status_code=404, detail="Not Found")
        if n is None:
            return _current_version(meta, known)
        try:
            wanted = int(n)
        except (TypeError, ValueError):
            raise HTTPException(status_code=404, detail="Not Found")
        if wanted not in known:
            raise HTTPException(status_code=404, detail="Not Found")
        return wanted

    def _store_error(e: store.ArtifactError) -> HTTPException:
        """Store failure -> HTTP. A cap or a validation error is a real 400
        for the rig; the OWNERSHIP refusal is the flat 404 (D29), because
        "not your artifact" confirms a page exists exactly where a
        nonexistent id would have said Not Found. A disk that said no is
        507 for a full disk, else 500 — with the reason, never a bare
        traceback (correctness-09)."""
        if "not your artifact" in str(e).lower():
            return HTTPException(status_code=404, detail="Not Found")
        if isinstance(e, getattr(store, "ArtifactStorageError", ())):
            full = getattr(e, "errno", None) in (errno.ENOSPC, errno.EDQUOT,
                                                 errno.EFBIG)
            return HTTPException(status_code=507 if full else 500,
                                 detail=str(e))
        return HTTPException(status_code=400, detail=str(e))

    # --- gallery + shell ----------------------------------------------------

    def _ui_response(template: str, page: str) -> HTMLResponse:
        """Our own UI, under a CSP that admits exactly the template's own
        inline scripts (security-3). Hashed from the TEMPLATE, before any
        value was substituted, so nothing a title or description carries can
        ever match a hash."""
        headers = dict(SHELL_HEADERS)
        headers["Content-Security-Policy"] = shell_csp(
            inline_script_hashes(template))
        return HTMLResponse(store.wrap_skeleton(page).decode("utf-8"),
                            headers=headers)

    def _chat_base() -> str:
        """https://<rig>:8445 when beast-chat is published on the tailnet,
        else "" — the "made by session" link renders only when it can open
        (F-A3). The name comes from `tailscale serve`, exactly as the
        artifact base URL does (artifact._detect_base_url)."""
        override = (store.conf_value("CHAT_BASE_URL") or "").strip()
        if override:
            return "" if override.lower() in ("off", "none") \
                else override.rstrip("/")
        try:
            return store.published_base(8445)
        except Exception:
            return ""

    def _session_link(meta) -> str:
        sess = meta.get("source_session") if isinstance(meta, dict) else None
        if not isinstance(sess, str) or not store._SESSION_RE.match(sess):
            return ""
        chat = _chat_base()
        label = f"made by session {_esc(sess)}"
        if not chat:
            return f'<span class="sess">{label}</span>'
        href = f"{chat}/#/s/{urllib.parse.quote(sess, safe='')}"
        return (f'<a class="sess" href="{_esc(href)}" target="_blank" '
                f'rel="noopener">{label}</a>')

    def _row_html(r: dict, admin: bool, viewer: str) -> str:
        search = " ".join(str(x) for x in (
            r.get("title"), r.get("description") or "", r.get("id"),
            " ".join(r.get("tags") or []))).lower()
        fav = f'<span class="fav">{_esc(r["favicon"])}</span> ' \
            if r.get("favicon") else ""
        pin = '<span class="pin" title="pinned">&#9733;</span> ' \
            if r.get("pinned") else ""
        desc = _esc(r.get("description") or "")
        tags = "".join(f'<span class="tag">{_esc(t)}</span>'
                       for t in (r.get("tags") or []))
        owner = r.get("owner") or ""
        owner_chip = (f'<span class="owner">{_esc(owner)}</span>'
                      if admin and owner and owner != viewer else "")
        return (
            f'<li class="row" data-search="{_esc(search)}">'
            f'<a class="card" href="/a/{_esc(r["id"])}">'
            f'<span class="row-top"><span class="row-title">{pin}{fav}'
            f'{_esc(r["title"])}</span>'
            f'<span class="row-age">{_esc(store.human_ts(r.get("updated_at")))}'
            f'</span></span>'
            f'<span class="row-desc">{desc}</span>'
            f'<span class="row-meta">'
            f'<span class="badge" data-visibility="{_esc(r["visibility"])}">'
            f'{_esc(r["visibility"])}</span>'
            f'<span class="vers">v{_esc(r["current"])} of '
            f'{_esc(r["versions"])}</span>{tags}{owner_chip}</span>'
            f'</a></li>')

    @app.get("/", response_class=HTMLResponse)
    def gallery(request: Request, page: int = 1, q: str = "",
                session: str = "", tag: str = ""):
        viewer = viewer_of(request)
        admin = is_admin(request)
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        q = str(q or "").strip()[:100]
        rows, total = store.list_page(
            viewer=viewer, admin=admin, limit=GALLERY_PAGE,
            offset=(page - 1) * GALLERY_PAGE, session=session or None,
            tag=tag or None, pinned_first=True, query=q or None)
        body = "\n".join(_row_html(r, admin, viewer) for r in rows)
        if not rows and (q or session or tag or page > 1):
            # A filter (or a page past the end) that matched nothing is not
            # "nothing published yet" — say what happened.
            body = ('<li class="empty"><h2>No artifact matches that</h2>'
                    '<p><a href="/">Show everything</a></p></li>')
        first = (page - 1) * GALLERY_PAGE + 1
        count = (str(total) if total <= GALLERY_PAGE
                 else f"{first}&ndash;{first + len(rows) - 1} of {total}"
                 if rows else f"0 of {total}")
        keep = {k: v for k, v in (("q", q), ("session", session),
                                  ("tag", tag)) if v}
        links = []
        if page > 1:
            links.append(f'<a class="pg" href="/?{_esc(urllib.parse.urlencode(dict(keep, page=page - 1)))}">&larr; newer</a>')
        if page * GALLERY_PAGE < total:
            links.append(f'<a class="pg" href="/?{_esc(urllib.parse.urlencode(dict(keep, page=page + 1)))}">older &rarr;</a>')
        active = []
        for k in ("q", "session", "tag"):
            if keep.get(k):
                active.append(f'{k}: <b>{_esc(keep[k])}</b>')
        filt = (f'<p class="active">{" &middot; ".join(active)} &middot; '
                f'<a href="/">clear</a></p>' if active else "")
        template = _read_template("gallery.html", _FALLBACK_GALLERY)
        page_html = _fill(template, {
            "ROWS": body,
            "COUNT": count,
            "VIEWER": _esc(viewer or ""),
            "PAGER": "".join(links),
            "FILTERS": filt,
            "Q": _esc(q),
            "ADMIN": "1" if admin else "",
        })
        return _ui_response(template, page_html)

    def _shell(request: Request, artifact_id: str, n=None) -> HTMLResponse:
        viewer = viewer_of(request)
        admin = is_admin(request)
        meta = visible_meta(artifact_id, viewer, admin)
        aid = _meta_id(meta, artifact_id)
        version = _version_or_current(meta, n, artifact_id)
        # The picker is built from the RESOLVABLE versions (D23), not from
        # whatever `versions` happens to hold: the old sort called int() on
        # every entry, so one null in the list 500'd the owner's own page.
        entries = _version_entries(meta)
        opts = []
        for vn in sorted(_known_versions(meta, artifact_id), reverse=True):
            v = entries.get(vn, {})
            label = f" · {v['label']}" if v.get("label") else ""
            sel = " selected" if vn == version else ""
            ts = store.human_ts(v.get("ts"))[:16]
            opts.append(f'<option value="{vn}"{sel}>v{vn}{_esc(label)}'
                        f'{" · " + _esc(ts) if ts else ""}</option>')
        owner = store._owner_of(meta)
        can_manage = admin or (owner and owner == store._norm_login(viewer)) \
            or not owner
        template = _read_template("shell.html", _FALLBACK_SHELL)
        page = _fill(template, {
            "TITLE": _esc(meta.get("title") or "Untitled"),
            "DESCRIPTION": _esc(meta.get("description") or ""),
            "ARTIFACT_ID": _esc(aid),
            "VERSION": str(version),
            "VERSION_OPTIONS": "\n".join(opts),
            # The capability path, so the page's own relative URLs
            # (<img src="chart.png">) inherit the token and load.
            "RAW_URL": f"/raw/{_esc(aid)}/v/{version}/"
                       f"~{raw_token(aid, version)}/",
            "UPDATED": _esc(store.human_ts(meta.get("updated_at"))),
            "VISIBILITY": _esc(meta.get("visibility") or "private"),
            "SANDBOX": IFRAME_SANDBOX,
            "FAVICON_HREF": _esc(_favicon_href(meta.get("favicon"))),
            "SESSION_LINK": _session_link(meta),
            "OWNER": _esc(owner if admin and owner != viewer else ""),
            "PINNED": "1" if meta.get("pinned") is True else "",
            "TAGS": _esc(",".join(store._tags_of(meta))),
            "CAN_MANAGE": "1" if can_manage else "",
        })
        return _ui_response(template, page)

    @app.get("/a/{artifact_id}/v/{n}", response_class=HTMLResponse)
    def shell_versioned(request: Request, artifact_id: str, n: int):
        return _shell(request, artifact_id, n)

    @app.get("/a/{artifact_id}", response_class=HTMLResponse)
    def shell_current(request: Request, artifact_id: str):
        return _shell(request, artifact_id)

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    def favicon(request: Request):
        """browser-6: every view used to fetch this, get the flat 404 and
        write a refused-request audit row. The real icon is a data: URI in
        the page; this just answers the browser's reflex."""
        viewer_of(request)
        return Response(status_code=204,
                        headers={"Cache-Control": "private, max-age=86400"})

    # --- raw ----------------------------------------------------------------

    def _raw_response(data: bytes, ctype: str, request: Request | None = None,
                      ranged: bool = False) -> Response:
        headers = dict(RAW_HEADERS)
        if ranged:
            # browser-9: media (<video src="demo.mp4">) needs byte ranges —
            # WebKit on iOS will not play a source without 206, and no
            # browser can seek without them. ONE range; anything fancier is
            # answered with the whole body, which is always correct.
            headers["Accept-Ranges"] = "bytes"
            rng = _parse_range(request.headers.get("range") if request
                               else None, len(data))
            if rng == "unsatisfiable":
                headers["Content-Range"] = f"bytes */{len(data)}"
                return Response(status_code=416, headers=headers)
            if rng is not None:
                start, end = rng
                headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                return Response(content=data[start:end + 1], status_code=206,
                                media_type=ctype, headers=headers)
        return Response(content=data, media_type=ctype, headers=headers)

    # GET + HEAD: `curl -I` and any probe that only wants the headers must
    # see the policy, not a 405.
    @app.api_route("/raw/{artifact_id}/v/{n}/", methods=["GET", "HEAD"])
    def raw_page(request: Request, artifact_id: str, n: int, theme: str = ""):
        viewer = viewer_of(request)
        meta = visible_meta(artifact_id, viewer, is_admin(request))
        version = _version_or_current(meta, n, artifact_id)
        try:
            data, _ = store.read_file(_meta_id(meta, artifact_id), version,
                                      "index.html")
        except store.ArtifactError:
            raise HTTPException(status_code=404, detail="Not Found")
        request.state.extra = {"bytes": len(data)}
        html = store.wrap_skeleton(
            data, theme=theme if theme in ("dark", "light") else None,
            link_guard=True)
        return _raw_response(html, "text/html; charset=utf-8")

    # --- raw, under the capability path --------------------------------------
    # Registered BEFORE the catch-all file route below, which would otherwise
    # claim "~<token>/…" as a published path (and 404 it: a published segment
    # can never start with "~", so the two namespaces cannot collide).

    def _token_ok(meta, artifact_id: str, n: int, token: str) -> None:
        want = raw_token(_meta_id(meta, artifact_id), n)
        got = token.encode("utf-8", "surrogateescape")
        if not hmac.compare_digest(got, want.encode()):
            raise HTTPException(status_code=404, detail="Not Found")

    @app.api_route("/raw/{artifact_id}/v/{n}/~{token}/",
                   methods=["GET", "HEAD"])
    def raw_page_tokened(request: Request, artifact_id: str, n: int,
                         token: str, theme: str = ""):
        viewer = viewer_of(request)
        _token_ok(visible_meta(artifact_id, viewer, is_admin(request)),
                  artifact_id, n, token)
        return raw_page(request, artifact_id, n, theme)

    @app.api_route("/raw/{artifact_id}/v/{n}/~{token}/{path:path}",
                   methods=["GET", "HEAD"])
    def raw_file_tokened(request: Request, artifact_id: str, n: int,
                         token: str, path: str):
        viewer = viewer_of(request)
        _token_ok(visible_meta(artifact_id, viewer, is_admin(request)),
                  artifact_id, n, token)
        response = raw_file(request, artifact_id, n, path)
        if path.strip().strip("/") not in ("", "index.html"):
            # A supporting file, not the page: loadable by the sandboxed
            # (and therefore cross-origin) document it belongs to.
            for name, value in RAW_FILE_HEADERS.items():
                response.headers[name] = value
        return response

    @app.api_route("/raw/{artifact_id}/v/{n}/{path:path}",
                   methods=["GET", "HEAD"])
    def raw_file(request: Request, artifact_id: str, n: int, path: str):
        viewer = viewer_of(request)
        meta = visible_meta(artifact_id, viewer, is_admin(request))
        version = _version_or_current(meta, n, artifact_id)
        # [26] `.strip("/")` alone left surrounding whitespace, so
        # /raw/<id>/v/<n>/%20index.html missed this delegation while the
        # store's own read_file (which strips whitespace too) matched
        # "index.html" and returned the page — served unwrapped, i.e. with no
        # doctype/charset/viewport, in quirks mode. Both spellings must reach
        # raw_page so both get wrap_skeleton.
        if path.strip().strip("/") in ("", "index.html"):
            return raw_page(request, artifact_id, n)
        try:
            data, ctype = store.read_file(_meta_id(meta, artifact_id),
                                          version, path)
        except store.ArtifactError:
            raise HTTPException(status_code=404, detail="Not Found")
        request.state.extra = {"bytes": len(data)}
        return _raw_response(data, ctype, request, ranged=True)

    # --- api ----------------------------------------------------------------

    # GET + HEAD (D29): FastAPI does not add HEAD to an @app.get route, so a
    # `curl -I` liveness probe — the cheapest one there is, and what a lot of
    # monitors default to — got 404 from a server that was perfectly healthy.
    @app.api_route(HEALTH_PATH, methods=["GET", "HEAD"])
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
                "admins": len(store.admins()),
                "retain_days": store.retain_days(),
                "store": root}

    @app.get("/api/artifacts")
    def api_list(request: Request, limit: int = 25, owner: str = "",
                 offset: int = 0, session: str = "", tag: str = "",
                 q: str = ""):
        viewer = viewer_of(request)
        # D12: clamp. `limit=0` meant "no limit" in the store, so the cheapest
        # possible query was also the most expensive one to serve.
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        offset = max(0, int(offset))
        rows, total = store.list_page(
            owner=owner or None, viewer=viewer, admin=is_admin(request),
            limit=limit, offset=offset, session=session or None,
            tag=tag or None, query=(q or "").strip()[:100] or None)
        return {"artifacts": rows, "count": len(rows), "total": total,
                "offset": offset, "viewer": viewer,
                "admin": is_admin(request)}

    @app.get("/api/artifacts/{artifact_id}")
    def api_get(request: Request, artifact_id: str):
        viewer = viewer_of(request)
        meta = visible_meta(artifact_id, viewer, is_admin(request))
        aid = _meta_id(meta, artifact_id)
        known = _known_versions(meta, artifact_id)
        entries = _version_entries(meta)
        out = {k: v for k, v in meta.items() if k not in PRIVATE_META_FIELDS}
        out["id"] = aid
        out["owner"] = store._owner_of(meta) or None
        out["url"] = store.artifact_url(aid)
        out["current"] = _current_version(meta, known)
        # Built from the resolvable list (D23): `dict(v, ...)` on a null and
        # artifact_url(..., "two") on a non-numeric `n` were each a 500 on
        # this route, for the owner, over a damaged record the store itself
        # can still serve pages out of.
        out["versions"] = [
            dict(entries.get(vn, {}), n=vn, url=store.artifact_url(aid, vn))
            for vn in known]
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
                visibility=body.visibility,
                owner=owner_for(request),
                source_session=body.source_session)
        except store.ArtifactError as e:
            raise _store_error(e)
        meta = store.get_meta(result["id"]) or {}
        # [14] `int(v.get("n", 0))` over raw meta was an AttributeError on a
        # null/string entry and a ValueError on `n: "two"` — raised AFTER the
        # version was written, made current, and appended to index.jsonl, so
        # artifact.sh reported 'publish failed (HTTP 500)' for a page that is
        # on disk and being served. The store only repairs `versions` when it
        # is not a list; a list CONTAINING junk survives verbatim.
        # _version_entries (D23) drops non-records and non-numeric `n` and
        # never raises.
        version = _version_entries(meta).get(result["version"], {})
        # The row the log exists for (browser-7/correctness-04): WHICH page,
        # which version, whose — not just a hash to join against the ledger.
        request.state.extra = {"id": result["id"], "n": result["version"],
                               "owner": result.get("owner"),
                               "created": result.get("created"),
                               "visibility": result.get("visibility"),
                               "sha256": version.get("sha256"),
                               "bytes": result.get("bytes")}
        return result

    @app.patch("/api/artifacts/{artifact_id}")
    def api_patch(request: Request, artifact_id: str, body: PatchBody,
                  _w: None = Depends(require_manager)):
        try:
            current_meta = store.get_meta(artifact_id)
        except store.ArtifactError:
            current_meta = None
        if not current_meta:
            raise HTTPException(status_code=404, detail="Not Found")
        admin = is_admin(request)
        who = owner_for(request)
        # OWNERSHIP BEFORE INPUT VALIDATION. A tailnet login with an
        # artifact-scoped device key can reach this route, so validating the
        # body first answered someone else's private page with a 400 ("invalid
        # tag ...") and a nonexistent id with a 404 — an existence oracle
        # (D9/D29). A caller who can neither own nor administer the page gets
        # the same flat 404 as a missing id, before anything it sent is read.
        # The store mutators below still re-check under their own lock.
        if not admin:
            try:
                store._require_owner(current_meta, who)
            except store.ArtifactError:
                raise HTTPException(status_code=404, detail="Not Found")
        # VALIDATE CALLER INPUT BEFORE ANY WRITE. The ordering below limits
        # the damage of a late failure but cannot remove it: a mixed body with
        # a valid `current` and an INVALID visibility VALUE committed the
        # rollback and then answered 400, so the comment's claim that
        # set_current "is the only one that can fail after a successful
        # sibling" was false. Measured.
        #
        # This check is safe to hoist where a `current` pre-check is not: an
        # enum test on the caller's own input reveals nothing about the
        # artifact, so it cannot become the existence-and-version oracle the
        # note below describes. Tags are the caller's input too.
        if body.visibility is not None and body.visibility not in store.VISIBILITIES:
            raise HTTPException(
                status_code=400,
                detail=f"visibility must be one of "
                       f"{', '.join(store.VISIBILITIES)}")
        if body.tags is not None:
            try:
                store._check_tags(body.tags)
            except store.ArtifactError as e:
                raise HTTPException(status_code=400, detail=str(e))
        if body.owner is not None and not admin:
            # Handing a page to someone else decides who may read it: the
            # rig and admins only. Same flat 404 as any ownership refusal.
            raise HTTPException(status_code=404, detail="Not Found")
        # ORDER IS LOAD-BEARING. These are independent locked store writes
        # with no rollback, so a mixed body whose LATER field fails returns
        # 4xx with the EARLIER field already committed. `set_current` is the
        # only one that can fail after a successful sibling (a version that
        # does not exist), so it goes FIRST; `visibility` — the WIDENING
        # write — and `owner` — which changes who can read — go LAST. Before
        # this, `{"visibility": "tailnet", "current": 999}` answered 400 "no
        # such version" having already made the artifact tailnet-readable.
        # Review of v1.4.0.
        #
        # NOT fixed by pre-validating `current` in this route: every ownership
        # check lives inside the store mutators, so a check up here would run
        # before _require_owner and answer a non-owner "no such version: 999"
        # instead of the flat 404 — turning D29's deliberately indistinguishable
        # refusal into an existence-and-version-count oracle.
        changed: dict = {}
        try:
            meta = None
            if body.current is not None:
                meta = store.set_current(artifact_id, body.current,
                                         owner=who, admin=admin)
                changed["current"] = body.current
            if body.description is not None:
                meta = store.set_description(artifact_id, body.description,
                                             owner=who, admin=admin)
                changed["description"] = True
            if body.pinned is not None:
                meta = store.set_pinned(artifact_id, body.pinned,
                                        owner=who, admin=admin)
                changed["pinned"] = bool(body.pinned)
            if body.tags is not None:
                meta = store.set_tags(artifact_id, body.tags,
                                      owner=who, admin=admin)
                changed["tags"] = store._tags_of(meta)
            if body.visibility is not None:
                before = (store.get_meta(artifact_id) or {}).get("visibility")
                # Owner-gated in the store (D5/R2): say WHO is asking rather
                # than letting it guess the rig's first operator.
                meta = store.set_visibility(artifact_id, body.visibility,
                                            owner=who, admin=admin)
                changed["visibility"] = f"{before}->{body.visibility}"
            if body.owner is not None:
                meta = store.set_owner(artifact_id, body.owner,
                                       owner=who, admin=admin)
                changed["owner"] = meta.get("owner")
        except store.ArtifactError as e:
            request.state.extra = {"changed": changed} if changed else {}
            raise _store_error(e)
        request.state.extra = {"changed": changed}
        if meta is None:
            meta = store.get_meta(artifact_id)
        # [4]/[15] The store mutators return the raw _read_meta dict with no id
        # normalisation, and `store.artifact_url(meta["id"])` sat OUTSIDE the
        # ArtifactError guard above — so an id-less or non-string-id record
        # turned an ALREADY-COMMITTED patch into a 500: the caller is told the
        # visibility change failed when it succeeded and the page is now
        # tailnet-readable. _meta_id is the helper this file already carries for
        # exactly this corruption family; the five GET routes used it and the
        # two write routes did not.
        if not isinstance(meta, dict):
            # Narrow race: an all-None body plus a concurrent DELETE between
            # the existence check and this re-read.
            raise HTTPException(status_code=404, detail="Not Found")
        aid = _meta_id(meta, artifact_id)
        return {"id": aid, "visibility": meta.get("visibility"),
                "description": meta.get("description"),
                "current": meta.get("current"),
                "pinned": meta.get("pinned") is True,
                "tags": store._tags_of(meta),
                "owner": store._owner_of(meta) or None,
                "url": store.artifact_url(aid)}

    @app.delete("/api/artifacts/{artifact_id}")
    def api_delete(request: Request, artifact_id: str,
                   _w: None = Depends(require_manager)):
        try:
            # R2, and the loudest of the four: DELETE is irreversible.
            gone = store.remove(artifact_id, owner=owner_for(request),
                                admin=is_admin(request))
        except store.ArtifactError as e:
            raise _store_error(e)
        if not gone:
            raise HTTPException(status_code=404, detail="Not Found")
        return {"id": artifact_id, "removed": True}

    @app.delete("/api/artifacts/{artifact_id}/v/{n}")
    def api_delete_version(request: Request, artifact_id: str, n: int,
                           _w: None = Depends(require_manager)):
        """Delete one OLD version (F-A2): room under the 200-version cap
        without giving up the URL. Never the current one, never the last."""
        try:
            meta = store.remove_version(artifact_id, n,
                                        owner=owner_for(request),
                                        admin=is_admin(request))
        except store.ArtifactError as e:
            raise _store_error(e)
        return {"id": _meta_id(meta, artifact_id), "removed_version": n,
                "versions": len(_version_entries(meta))}

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
            "# HELP openbeast_artifact_requests_total "
            "Requests by route/outcome/deny-reason",
            "# TYPE openbeast_artifact_requests_total counter",
        ]
        with metrics_lock:
            # R5: `reason` is the third label. Without it every refusal —
            # anonymous, oversize, not-local and the ambiguous-identity signal
            # D29 exists to catch — collapsed into ONE <unmatched>/404 series,
            # so past the audit budget an operator could not even tell which
            # kind of refusal was flooding. It is one of five constants
            # (_deny_label), so the cardinality is bounded by construction.
            for (route, outcome, reason), n in sorted(hits.items()):
                lines.append(
                    f'openbeast_artifact_requests_total'
                    f'{{route="{_metric_label(route)}",'
                    f'outcome="{_metric_label(outcome)}",'
                    f'reason="{_metric_label(reason)}"}} {n}')
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

    # F-A1 migration: pages stored under the pre-F-A1 "local" owner become
    # the rig's. Idempotent (a second start finds nothing), per-id locked,
    # one ledger row per page in the store's index.jsonl, and one summary row
    # here so the operator reading the audit log sees it happened.
    try:
        reowned = store.migrate_legacy_owners()
    except Exception:                                   # noqa: BLE001
        reowned = []
    if reowned:
        audit({"ts": _now(), "login": RIG_LOGIN, "local": True,
               "event": "migrate-owner", "from": LOCAL_LOGIN,
               "to": RIG_LOGIN, "count": len(reowned),
               "ids": reowned[:50]})
    # The default admin is the FIRST operator (ARTIFACT_ADMINS unset). On a
    # rig with several operators that is a widening over v1.6.0, where each
    # operator's private pages were owner-only: the first operator can now
    # read, re-share, chown and delete the others'. Deliberate — but an
    # upgrade must not do it silently, so every start says so, once, on
    # stderr and in the audit log, until ARTIFACT_ADMINS is set.
    ops = store.operators()
    if len(ops) > 1 and not store._logins(store.conf_value("ARTIFACT_ADMINS")):
        note = (f"artifact: ARTIFACT_ADMINS is unset, so the first operator "
                f"({ops[0]}) administers every page, including the private "
                f"pages of the other {len(ops) - 1} operator(s). Set "
                f"ARTIFACT_ADMINS in openbeast.conf to choose explicitly.")
        print(note, file=sys.stderr, flush=True)
        audit({"ts": _now(), "login": RIG_LOGIN, "local": True,
               "event": "admin-default", "admin": ops[0],
               "operators": len(ops)})

    def sweep() -> list:
        """One pass of the opt-in retention sweep (F-A2). Audited per page.
        A no-op unless ARTIFACT_RETAIN_DAYS > 0; never touches pinned."""
        try:
            removed = store.sweep_retention()
        except Exception:                               # noqa: BLE001
            return []
        for aid in removed:
            audit({"ts": _now(), "login": RIG_LOGIN, "local": True,
                   "method": "SWEEP", "route": "retention", "id": aid,
                   "outcome": "removed", "retain_days": store.retain_days()})
        return removed

    app.state.sweep = sweep
    return app


RETENTION_INTERVAL_S = 24 * 3600


def _start_retention_thread(app) -> threading.Thread:
    """Daily retention sweep, in the server process (the one long-lived
    writer). The setting is re-read every pass, so turning it on or off in
    openbeast.conf takes effect by the next day without a restart."""
    def loop():
        while True:
            try:
                app.state.sweep()
            except Exception:                           # noqa: BLE001
                pass
            time.sleep(RETENTION_INTERVAL_S)
    t = threading.Thread(target=loop, name="artifact-retention", daemon=True)
    t.start()
    return t


def _parse_range(header, size: int):
    """One `bytes=a-b` / `bytes=a-` / `bytes=-n` range -> (start, end), None
    for "serve the whole body", or "unsatisfiable". Multi-range and anything
    malformed is None: answering 200 with everything is always correct."""
    if not header or size <= 0:
        return None
    h = str(header).strip()
    if not h.lower().startswith("bytes=") or "," in h:
        return None
    spec = h[6:].strip()
    first, sep, last = spec.partition("-")
    if not sep:
        return None
    try:
        if first == "":
            n = int(last)
            if n <= 0:
                return "unsatisfiable"
            return (max(0, size - n), size - 1)
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0:
        return None
    if start >= size:
        return "unsatisfiable"
    if end < start:
        return None
    return (start, min(end, size - 1))


def _uvicorn_config(app, host: str, port: int):
    """The uvicorn.Config main() serves with — separate so tests load it.

    proxy_headers=False is load-bearing. uvicorn's default trusts
    X-Forwarded-For from 127.0.0.1 and rewrites request.client to it, and
    `tailscale serve` — which dials us from 127.0.0.1 — always sends
    X-Forwarded-For: <tailnet IP>. The app would then see a 100.x peer,
    _peer_is_loopback() would drop Tailscale-User-Login, and every tailnet
    viewer would be anonymous (404 everywhere). The auth peer must be the
    real socket peer; nothing here reads the forwarded address.
    """
    import uvicorn
    return uvicorn.Config(app, host=host, port=port, log_level="warning",
                          proxy_headers=False, forwarded_allow_ips="")


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
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    infos.sort(key=lambda i: i[0] != socket.AF_INET)   # a NAME: prefer IPv4,
    family, stype, proto, _, addr = infos[0]           # where the probes look
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
    _start_retention_thread(app)
    print(f"OpenBeast artifact server on {host}:{port} "
          f"(store {store.store_root()})")
    uvicorn.Server(_uvicorn_config(app, host, port)).run(sockets=[sock])


if __name__ == "__main__":
    main()
