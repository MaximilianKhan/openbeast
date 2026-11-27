#!/usr/bin/env python3
"""OpenBeast identity-aware tool server — the WebUI-facing OpenAPI surface.

Replaces mcpo for the Open WebUI connection (docs/archive/IDENTITY_TOOLS_PLAN.md
Option B). mcpo converts MCP→OpenAPI generically but drops the HTTP headers
Open WebUI forwards (X-OpenWebUI-User-Id/-Role, X-OpenWebUI-Chat-Id) at the
MCP boundary — so tools could never know WHO was calling. This server owns
the HTTP layer, which buys three things mcpo structurally can't provide:

  IDENTITY   Per-user workspace sharding: relative tool paths (and the
             .manifest.jsonl index) resolve inside
             $OPENBEAST_FILES_DIR/users/<user-id>[/chats/<chat-id>], so one
             family member's files are namespaced away from another's.
             Headerless (single-user) requests keep the shared root —
             fresh-install behavior is unchanged. Published artifacts are the
             exception to the sharding rule: they are addressed by owner, and
             the owner is the caller's forwarded EMAIL, because that is the
             namespace the artifact server authorises readers in (D21). An
             identified caller with no usable email is REFUSED (R1) — the
             old fallback to the rig's first operator let a second account
             publish, list and republish as that operator.
  PROFILES   Both RBAC Phase 2 keys are checked natively in ONE process:
             the admin key reaches all tools, the guest key only
             GUEST_TOOLS — web_search, fetch, weather (404 elsewhere); no
             key configured = open loopback Phase-1 parity. Replaces the
             two-instance MCPO split. weather is a web read, but note it
             also signs in to Open WebUI AS ADMIN to read the CALLER'S OWN
             user.info.location (agents/weather.py caller_location) — only
             that user's record, keyed by the forwarded user id.
  AUDIT      Every tool call is appended to .run/tool-audit.jsonl (0600)
             with ts/user/role/chat/tool/status/ms, an argument HMAC under
             the install's .run/tool-audit.key and a power-of-two size
             bound — never the arguments themselves, and a copy of the trail
             without the key cannot confirm a guessed query either.

agents/mcp_server.py remains the MCP (stdio) surface for OpenCode and any
real MCP client; this module imports and serves the same 19 tool functions
(plus the WebUI-only `weather`: 20 here), so the two surfaces cannot drift.

Env:
  OPENBEAST_TOOLS_PORT        listen port (default 3001 — note start.sh and
                              healthcheck.sh probe 3001; changing this is
                              for standalone runs only)
  OPENBEAST_BIND              bind address         (default 127.0.0.1)
  OPENBEAST_MCPO_ADMIN_KEY    admin profile key    (either set => auth on)
  OPENBEAST_MCPO_GUEST_KEY    guest profile key
  OPENBEAST_ALLOW_OPEN_TOOLS  true = serve keyless on a non-loopback bind
                              anyway (otherwise main() refuses to start)
  OPENBEAST_TOOLS_ALLOWED_HOSTS  extra Host names to answer to, comma-
                              separated (default: loopback, this machine's
                              name, *.ts.net and any literal IP address —
                              agents/hostpolicy.py)
  OPENBEAST_FILES_SHARDING    off | user | chat    (default user)
  OPENBEAST_FILES_DIR         workspace root (start.sh exports it)
  OPENBEAST_TOOL_AUDIT_PATH   audit log file (default: $OPENBEAST_RUN_DIR,
                              else <repo>/.run, + /tool-audit.jsonl). The
                              test suite points it at a temp dir so fixture
                              rows never land in the rig's real audit trail.

Trust note: identity headers are accepted as sent. On this stack the only
network path to this port is loopback or WebUI itself (main() refuses a
keyless non-loopback bind unless ALLOW_OPEN_TOOLS=true); a caller who can
forge headers here can already reach every service directly. Signed-JWT
identity (Open WebUI's FORWARD_USER_INFO_HEADER_JWT_SECRET) is the
enterprise upgrade — see docs/TODO.md.

Loopback is not the whole of that threat model: a web page the operator
visits can reach a loopback port too, by pointing a DNS name it controls at
127.0.0.1 (rebinding), and it is then same-origin with this server — JSON
POSTs to /bash included. So the Host header is pinned (PinnedHostMiddleware,
the allowlist every other OpenBeast server uses); a browser cannot forge it.
"""
import hashlib
import hmac
import inspect
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime

import jwt as pyjwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import create_model

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import mcp_server as impl  # noqa: E402  (plain callables; FastMCP decor returns fn)
import tools as _tools     # noqa: E402
from hostpolicy import PinnedHostMiddleware, trusted_hosts  # noqa: E402

REPO_DIR = os.path.dirname(_HERE)

# The full WebUI tool surface — the 19 functions the MCP server exposes,
# plus `weather` (20).
# `language_reference` (beast-lang's pull surface) is read-only and harmless,
# and it is STILL admin-only: GUEST_TOOLS below is web reads only (search,
# fetch, weather — outbound reads of guarded or fixed hosts) on purpose, and a
# profile that is "web-only, plus the things that seemed safe" is not a
# profile anyone can reason about. tests/test_lang_reference_tool.py pins it.
# `platform_docs` (beast-docs) is admin-only for the same reason, and because
# its live mode makes outbound requests (to configured doc hosts only).
TOOL_NAMES = [
    "bash", "read_file", "write_file", "edit_file", "list_files", "grep",
    "fetch", "web_search", "skill",
    "start_agent", "start_skill_agent", "check_agent", "list_agents",
    "stop_agent", "tail_agent",
    "publish_artifact", "list_artifacts",
    "language_reference", "platform_docs",
    # WebUI-only (not an MCP tool): it needs the caller's identity to find
    # where the caller is (agents/weather.py).
    "weather",
]
GUEST_TOOLS = {"web_search", "fetch", "weather"}

_HDR_USER = "x-openwebui-user-id"
_HDR_ROLE = "x-openwebui-user-role"
_HDR_CHAT = "x-openwebui-chat-id"
_HDR_JWT = "x-openwebui-user-jwt"
# The bridge between the two identity namespaces (D21). Open WebUI forwards
# it whenever ENABLE_FORWARD_USER_INFO_HEADERS is on (docker-compose.yml sets
# it), and a tailnet login IS an email address — which the WebUI user id,
# a UUID, can never be.
_HDR_EMAIL = "x-openwebui-user-email"

# Tools whose output is addressed by IDENTITY rather than by workspace path.
_ARTIFACT_TOOLS = ("publish_artifact", "list_artifacts")

# Every header that says WHO is calling (R8). The artifact server refuses a
# duplicated identity header (D29); this server — now the surface that
# RESOLVES OWNERSHIP for a publish — silently took the first, which is the
# same defect on the newer surface. `authorization` is in the list because it
# selects the RBAC profile: "take the first" is a silent answer there too.
_IDENTITY_HEADERS = (_HDR_USER, _HDR_ROLE, _HDR_CHAT, _HDR_JWT, _HDR_EMAIL,
                     "authorization")

# A plausible local@domain, checked as a SHAPE (R1). `"@" in login` was the
# whole test before, so the literal string "@" minted an owner named "@", and
# a non-string claim was str()'d into `"['max@example.com']"` and accepted.
# The domain deliberately allows a single label with no dot: a Tailscale login
# from a GitHub/Okta connector really is `someone@github`.
_EMAIL_RE = re.compile(
    r"^[^\s@,;:<>\"'\\/]{1,64}@[a-z0-9](?:[a-z0-9.-]{0,253}[a-z0-9])?$")

# What an identified caller is told when nothing usable reached us. It names
# the setting, because the fix is one env var away and the caller is a model
# that will otherwise retry forever.
_NO_EMAIL_HEADER = (
    "publish/list refused: this request carries a user id but no usable email "
    "address, and an artifact is owned by the email its reader presents. "
    "Turn on ENABLE_FORWARD_USER_INFO_HEADERS for Open WebUI (it is set in "
    "docker-compose.yml) so it forwards X-OpenWebUI-User-Email, then retry.")
_NO_EMAIL_JWT = (
    "publish/list refused: the signed identity token carries no usable "
    "`email` claim, and an artifact is owned by the email its reader "
    "presents. Configure Open WebUI's ENABLE_FORWARD_USER_INFO_HEADERS so the "
    "token it signs carries the user's email, then retry.")

# Shard path components come from headers — sanitize hard.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize(component: str) -> str:
    """Filesystem-safe AND injective: two distinct raw ids can never map to
    the same shard. The readable prefix keeps `ls` friendly; the digest of
    the RAW id guarantees uniqueness ("max!" vs "max?", all-unicode ids,
    >64-char ids all collide on the prefix alone)."""
    s = _UNSAFE.sub("_", component)[:48].strip("._") or "u"
    digest = hashlib.sha256(component.encode()).hexdigest()[:8]
    return f"{s}-{digest}"


def ambiguous_identity_headers(request: Request) -> list:
    """The identity headers this request sent more than once (R8).

    Starlette keeps every copy and `headers.get()` returns the FIRST, so
    `X-OpenWebUI-User-Email: max@…` + `X-OpenWebUI-User-Email: kid@…`
    published as max without a word. Two answers to "who are you" is not an
    identity, it is a question about one — and this server is the surface
    that turns that answer into a page's OWNER. Refuse, the way the artifact
    server already refuses a doubled `Tailscale-User-Login` (D29).
    """
    return [h for h in _IDENTITY_HEADERS
            if len(request.headers.getlist(h)) > 1]


def _refuse_ambiguous_identity(request: Request) -> None:
    dupes = ambiguous_identity_headers(request)
    if dupes:
        raise HTTPException(
            status_code=400,
            detail=("ambiguous identity: " + ", ".join(sorted(dupes)) +
                    " sent more than once. Send each identity header exactly "
                    "once — this server resolves the owner of a published "
                    "page from them."))


def _audit_path() -> str:
    """Where the tool-call audit trail goes. Overridable so tests never
    append synthetic rows (forged users, denied /bash probes, "../../etc")
    to the rig's real security log — an operator could not tell them apart
    from a genuine attack, and a genuine attack could hide among them."""
    explicit = os.environ.get("OPENBEAST_TOOL_AUDIT_PATH", "").strip()
    if explicit:
        return explicit
    run_dir = os.environ.get("OPENBEAST_RUN_DIR", "").strip() or \
        os.path.join(REPO_DIR, ".run")
    return os.path.join(run_dir, "tool-audit.jsonl")


# The largest request body any tool call may send. The pre-body gate (see
# create_app) refuses anything bigger with a 413 BEFORE it is buffered: with
# no cap, one POST of a few hundred MB sat in the same unified memory the
# model runs in. 16 MiB is far above any real call (write_file content is
# model-authored text; publish_artifact takes a path, not the page).
_DEFAULT_MAX_BODY = 16 * 1024 * 1024

# Audit fields copied from a request are capped like `error`/`claimed_user`:
# a header can be ~16 KB, and an uncapped row let one keyless request append
# that much to the trail.
_AUDIT_FIELD_CAP = 200


def _max_body_bytes() -> int:
    raw = os.environ.get("OPENBEAST_TOOLS_MAX_BODY_BYTES", "").strip()
    try:
        n = int(raw) if raw else _DEFAULT_MAX_BODY
    except ValueError:
        n = _DEFAULT_MAX_BODY
    return n if n > 0 else _DEFAULT_MAX_BODY


def _audit_key(audit_path: str) -> bytes:
    """The per-install key the argument digest is an HMAC under.

    A plain sha256 of the canonical argument JSON was confirmable offline:
    the tool signatures fix every field but the query/URL/command, so a
    reader of the trail could test guesses (and args_bytes gave the exact
    length). The key lives BESIDE the trail (tool-audit.key, 0600), never in
    it, so a copy of the log that leaves the rig — a backup, a log shipper —
    no longer confirms guesses. It hides nothing from the account that owns
    both files; nothing on the box could.
    """
    path = os.path.join(os.path.dirname(audit_path) or ".", "tool-audit.key")
    try:
        os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            with open(path, "rb") as f:
                key = f.read().strip()
            if len(key) >= 32:
                return key
            raise OSError("audit key too short")
        key = secrets.token_hex(32).encode()
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key
    except OSError:
        # Unwritable run dir: digests still correlate within this process,
        # and still confirm nothing offline.
        return secrets.token_hex(32).encode()


def _size_bound(n: int) -> int:
    """The smallest power of two >= n: the size class of a call, not the
    exact length of the one unknown field in it."""
    return 1 << max(0, (n - 1).bit_length())


def _cap(value):
    return None if value is None else str(value)[:_AUDIT_FIELD_CAP]


def _key_matches(token: str, key: str) -> bool:
    """Constant-time key check on BYTES. compare_digest on str raises
    TypeError for any non-ASCII character, and Starlette decodes header bytes
    as latin-1 — so `Bearer caf\xe9` used to escape check_auth as an unhandled
    500: no 401/403, no audit row, no denied metric, a traceback per probe.
    Same fix as edge.py / chat_server.py / artifact_server.py."""
    return hmac.compare_digest(token.encode("utf-8", "surrogateescape"),
                               key.encode("utf-8", "surrogateescape"))


def create_app() -> FastAPI:
    """App factory — reads config at call time so tests can vary env."""
    admin_key = os.environ.get("OPENBEAST_MCPO_ADMIN_KEY", "").strip()
    guest_key = os.environ.get("OPENBEAST_MCPO_GUEST_KEY", "").strip()
    # EITHER key present flips the server to keyed mode. It used to require
    # BOTH, which failed open: an operator setting only the admin key — the
    # intuitive way to "lock it down" — silently got a fully open server.
    # Now a missing profile key simply means that profile doesn't exist
    # (fail closed); setup-mcpo-keys.sh still generates the pair.
    keyed = bool(admin_key or guest_key)
    if keyed and not (admin_key and guest_key):
        missing = "guest" if admin_key else "admin"
        print(f"WARNING: only one RBAC key configured — the {missing} "
              f"profile is DISABLED (fail closed). Run "
              f"scripts/setup-mcpo-keys.sh to generate both.",
              file=sys.stderr)
    sharding = os.environ.get("OPENBEAST_FILES_SHARDING", "user").strip().lower()
    if sharding not in ("off", "user", "chat"):
        sharding = "user"
    files_dir = os.environ.get("OPENBEAST_FILES_DIR", "")
    audit_path = _audit_path()
    # O_CREAT's 0600 only applies to a NEW file; a trail created 0644 by an
    # older build stays world-readable until tightened once here.
    try:
        os.chmod(audit_path, 0o600)
    except OSError:
        pass
    audit_key = _audit_key(audit_path)
    max_body = _max_body_bytes()
    # Signed identity (enterprise): when this secret is set — the SAME value
    # given to Open WebUI as FORWARD_USER_INFO_HEADER_JWT_SECRET — WebUI
    # stops sending plain X-OpenWebUI-User-* headers and instead mints an
    # HS256 JWT (sub/role/iss=open-webui/exp≈300s) per call. We verify it,
    # which kills header forgery: a caller can no longer claim to be another
    # user by typing a header. Absent JWT = anonymous (router/CLI callers);
    # PRESENT but invalid/expired/forged = 401.
    jwt_secret = os.environ.get("OPENBEAST_IDENTITY_JWT_SECRET", "").strip()

    # /metrics counters (Prometheus text format, hand-rolled — no client dep)
    metrics_lock = threading.Lock()
    calls = defaultdict(int)     # (tool, profile, outcome) -> count
    latency_ms = defaultdict(float)  # (tool,) -> total ms

    def identity_from(request: Request) -> tuple:
        """Resolve (user_id, role, chat_id, email) for this call.

        JWT mode (secret set): identity comes ONLY from the verified token —
        plain identity headers are ignored (they'd be forgeable), and that
        includes the email: an unsigned X-OpenWebUI-User-Email next to a
        signed token would be a way to publish as somebody else.
        Header mode (no secret): the legacy X-OpenWebUI-User-* headers,
        trusted as sent (loopback threat model; see module docstring).
        """
        # chat_id stays a plain header even in JWT mode (WebUI doesn't put
        # it in the token). Forgeable — but it only selects a subdirectory
        # INSIDE the verified caller's own shard, so the blast radius is
        # self-inflicted.
        chat = request.headers.get(_HDR_CHAT, "").strip() or None
        if jwt_secret:
            token = request.headers.get(_HDR_JWT, "").strip()
            if not token:
                return None, None, chat, None   # anonymous (router, curl)
            try:
                claims = pyjwt.decode(
                    token, jwt_secret, algorithms=["HS256"],
                    issuer="open-webui",
                    # NOT "email". A token without one is a perfectly valid
                    # identity for the other 17 tools, and requiring it here
                    # would 401 the whole surface on any --with-jwt rig whose
                    # WebUI does not put an email in the token — breaking
                    # bash, read_file and every agent tool to protect
                    # publishing. The publish path refuses on its own, with a
                    # message that names the setting; see artifact_owner().
                    options={"require": ["exp", "sub"]},
                )
            except pyjwt.PyJWTError as e:
                raise HTTPException(status_code=401,
                                    detail=f"invalid identity token: {e}")
            return (str(claims["sub"]), claims.get("role"), chat,
                    claims.get("email"))
        user = request.headers.get(_HDR_USER, "").strip() or None
        role = request.headers.get(_HDR_ROLE, "").strip() or None
        email = request.headers.get(_HDR_EMAIL, "").strip() or None
        return user, role, chat, email

    def denied_identity(request: Request, status: int = 0) -> tuple:
        """(user, role, chat, claimed_user) for a DENIED call's audit row.

        The denial row used to copy the raw plain headers even in JWT mode,
        stamped identity=jwt — so any local caller could write "alice probed
        /bash" into the trail by typing her id. Now the row carries only what
        identity_from would VERIFY (nothing, if the token is absent or bad);
        a typed id in JWT mode is kept, clearly marked, as claimed_user.
        Header mode follows the same rule when the KEY check failed (401 /
        403): a caller with no valid key has proved nothing, so the id it
        typed is a claim, not the caller. With a valid key (a guest's 404)
        the header id is as trusted as on any header-mode row.
        """
        try:
            user, role, chat, _email = identity_from(request)
        except HTTPException:
            user, role, chat = None, None, \
                (request.headers.get(_HDR_CHAT, "").strip() or None)
        claimed = None
        if keyed and not jwt_secret and status in (401, 403):
            return None, None, chat, user
        if jwt_secret:
            typed = request.headers.get(_HDR_USER, "").strip()
            if typed and typed != user:
                claimed = typed
        return user, role, chat, claimed

    app = FastAPI(
        title="OpenBeast local tools",
        version="1.0",
        description="Identity-aware tool server (see agents/openapi_tools.py).",
        # /docs and /redoc OFF: FastAPI's pages pull floating, unpinned
        # swagger-ui@5 / redoc@2 bundles from a CDN into THIS origin — the
        # one serving /bash — and are broken offline anyway. WebUI reads
        # only /openapi.json.
        docs_url=None, redoc_url=None,
    )
    # Rebinding defence: a hostile DNS name pointed at 127.0.0.1 is refused
    # here, before a route (or the key check) runs. This was the one server
    # with /bash on it and the only one without the check. Literal addresses
    # stay welcome: this server binds BIND_HOST, so WebUI, the router and the
    # probes dial it as 127.0.0.1, [::1] or that one LAN/tailnet address
    # (ob_probe_host) — and an address is not a name an attacker can re-point.
    # (Added below, after PreBodyGate, so the Host check stays OUTERMOST.)
    allowed_hosts = trusted_hosts(
        os.environ.get("OPENBEAST_TOOLS_ALLOWED_HOSTS", ""))
    app.state.allowed_hosts = allowed_hosts

    def check_auth(request: Request, tool: str) -> str:
        """Returns the caller's profile; raises 401/403 like keyed mcpo did."""
        if not keyed:
            return "open"
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token:
            raise HTTPException(status_code=401, detail="API key required")
        if admin_key and _key_matches(token, admin_key):
            return "admin"
        if guest_key and _key_matches(token, guest_key):
            if tool not in GUEST_TOOLS:
                # The guest profile has no such tool — mirror the old guest
                # instance, where denied tools did not exist at all.
                raise HTTPException(status_code=404, detail="Not Found")
            return "guest"
        raise HTTPException(status_code=403, detail="Invalid API key")

    def artifact_owner(user, email) -> str:
        """Who a page published through THIS server belongs to (D21/R1).

        The two identity surfaces do not share a namespace. Open WebUI names
        a caller by a UUID; agents/artifact_server.py authorises a reader by
        their TAILNET LOGIN, which is an email address. Handing the store the
        UUID minted an owner that no principal can ever present: the page
        came back 404 to the human who asked for it, 404 to the rig, and
        could not be recovered — sharing it is owner-only, so only DELETE was
        left. So the forwarded email is the bridge: it is in the reader's
        namespace by construction.

        R1 — WHAT HAPPENS WHEN THERE ISN'T ONE. D21 fell back to
        `default_owner()` for everybody, which is worse than the tombstone it
        replaced: a SECOND WebUI account with no forwarded email publishes AS
        the rig's first operator, and from there enumerates that operator's
        private pages through list_artifacts and republishes over them at the
        operator's own URL (demonstrated). The fallback survives for exactly
        one case — `user is None`, meaning NO caller identity reached this
        server at all: the rig itself, the agent router, a curl from the box.
        An IDENTIFIED caller with no usable email is REFUSED, loudly, with the
        setting named. Never mint someone else as owner.

        And the email is validated as a SHAPE, not by `"@" in login`: that
        test passed the literal string "@" (minting an owner named "@") and
        str()'d a list claim into an owner nobody can present.
        """
        login = email.strip().lower() if isinstance(email, str) else ""
        if login and len(login) <= 254 and _EMAIL_RE.match(login):
            return login
        if user is None:
            # No identity at all: the rig / CLI / router path. `default_owner`
            # is the rig's own first operator, which is a principal a real
            # person can present — and nobody else's identity was available
            # to steal here in the first place.
            try:
                import artifact as _artifact
                return _artifact.default_owner()
            except Exception:
                return "rig"
        raise HTTPException(status_code=400,
                            detail=_NO_EMAIL_JWT if jwt_secret
                            else _NO_EMAIL_HEADER)

    def shard_for(user: str | None, chat: str | None) -> str | None:
        """Workspace shard for this caller, or None for the shared root."""
        if not files_dir or sharding == "off" or not user:
            return None  # single-user / no-auth install: shared root
        parts = [os.path.expanduser(files_dir), "users", _sanitize(user)]
        if sharding == "chat" and chat:
            parts += ["chats", _sanitize(chat)]
        shard = os.path.join(*parts)
        os.makedirs(shard, mode=0o700, exist_ok=True)
        return shard

    def audit(user, role, chat, tool: str, profile: str, ok: bool,
              ms: int, args: dict, err: str = "", owner: str = "",
              claimed_user=None) -> None:
        """Append-only call log. Argument CONTENTS never leave the request —
        only a KEYED digest (HMAC under the install's tool-audit.key, see
        _audit_key) and a power-of-two size bound, so a copy of the trail
        can neither leak a chat nor confirm a guessed query offline."""
        try:
            blob = json.dumps(args, sort_keys=True, default=str).encode()
            entry = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "user": _cap(user),
                "role": _cap(role),
                "chat": _cap(chat),
                "profile": profile,
                "tool": tool,
                "ok": ok,
                "ms": ms,
                "args_hmac": hmac.new(audit_key, blob,
                                      hashlib.sha256).hexdigest()[:16],
                "args_bytes_max": _size_bound(len(blob)),
                "identity": "jwt" if jwt_secret else "header",
            }
            if err:
                entry["error"] = err[:200]
            if owner:
                # D21 provenance: BOTH halves of the bridge, so an operator
                # can tie a page owned by an email back to the WebUI account
                # that published it (`user` above is that raw id).
                entry["artifact_owner"] = owner
            if claimed_user:
                # An UNVERIFIED id the caller typed (JWT mode ignores plain
                # headers). Kept for forensics, never as `user`.
                entry["claimed_user"] = str(claimed_user)[:200]
            os.makedirs(os.path.dirname(audit_path), mode=0o700,
                        exist_ok=True)
            # 0600 like every sibling audit/ledger (edge, artifact, chat):
            # it names WebUI accounts, chats and artifact owners.
            fd = os.open(audit_path,
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # the audit trail must never break the tool call

    def admit(request: Request, name: str) -> tuple:
        """(profile, user, role, chat, email) — or an audited denial.

        Headers only: the pre-body gate runs this before one body byte is
        received, and the endpoint runs it again (defence in depth).
        Denials MUST reach the audit trail and metrics — a guest probing
        /bash, a wrong-key brute force, or forged JWTs are exactly the
        events an audit log exists for.
        """
        try:
            # R8: two answers to "who is calling" is not an identity. First,
            # before the key check, because a doubled `authorization` picks
            # the PROFILE by the same first-wins accident.
            _refuse_ambiguous_identity(request)
            profile = check_auth(request, name)
            user, role, chat, email = identity_from(request)
        except HTTPException as e:
            d_user, d_role, d_chat, claimed = denied_identity(
                request, e.status_code)
            audit(d_user, d_role, d_chat,
                  name, "denied", False, 0, {},
                  f"http {e.status_code}: {e.detail}",
                  claimed_user=claimed)
            with metrics_lock:
                calls[(name, "denied", "error")] += 1
            raise
        return profile, user, role, chat, email

    def refuse_admitted(name: str, profile: str, ident: tuple, status: int,
                        detail: str) -> HTTPException:
        """Audit + count a refusal of an ADMITTED caller (413, 422, a
        workdir escape), and hand back the exception to raise/answer."""
        user, role, chat = ident
        audit(user, role, chat, name, profile, False, 0, {},
              f"http {status}: {detail}")
        with metrics_lock:
            calls[(name, profile, "error")] += 1
        return HTTPException(status_code=status, detail=detail)

    tool_set = frozenset(TOOL_NAMES)

    class PreBodyGate:
        """Authenticate, then cap, BEFORE the body is read.

        FastAPI reads and validates the whole body to build the endpoint's
        argument model, so a key check inside the endpoint came after the
        server had already held the body in RAM: one keyless POST /bash of
        300 MB took the process from 80 MB to 1.33 GB of the unified memory
        the model shares, and a malformed one got a 422 echoing its input
        with no audit row. This gate runs `admit` from the headers alone,
        then buffers the body only up to `max_body` (Content-Length checked
        up front, a streamed body counted as it arrives) — 413 past that.
        """

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            name = scope.get("path", "").lstrip("/")
            if (scope["type"] != "http" or scope.get("method") != "POST"
                    or name not in tool_set):
                await self.app(scope, receive, send)
                return
            request = Request(scope)
            try:
                profile, user, role, chat, _email = admit(request, name)
            except HTTPException as e:
                await JSONResponse({"detail": e.detail},
                                   status_code=e.status_code,
                                   headers=e.headers)(scope, receive, send)
                return
            ident = (user, role, chat)
            too_big = f"request body over the {max_body}-byte cap"
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_body:
                e = refuse_admitted(name, profile, ident, 413, too_big)
                await JSONResponse({"detail": e.detail}, status_code=413)(
                    scope, receive, send)
                return
            chunks, total = [], 0
            while True:
                msg = await receive()
                if msg["type"] != "http.request":
                    return  # client went away: nothing to answer
                total += len(msg.get("body", b""))
                if total > max_body:
                    e = refuse_admitted(name, profile, ident, 413, too_big)
                    await JSONResponse({"detail": e.detail}, status_code=413)(
                        scope, receive, send)
                    return
                chunks.append(msg.get("body", b""))
                if not msg.get("more_body", False):
                    break
            body, replayed = b"".join(chunks), False

            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body,
                            "more_body": False}
                return await receive()

            await self.app(scope, replay, send)

    def register(name: str) -> None:
        fn = getattr(impl, name)
        sig = inspect.signature(fn)
        fields = {}
        for pname, p in sig.parameters.items():
            ann = p.annotation if p.annotation is not inspect.Parameter.empty else str
            default = p.default if p.default is not inspect.Parameter.empty else ...
            fields[pname] = (ann, default)
        ArgsModel = create_model(f"{name}_args", **fields)

        # Sync endpoint on purpose: FastAPI runs it in a worker thread, so
        # blocking tools don't stall the event loop AND the ContextVar
        # override is naturally scoped to this request's thread.
        def endpoint(request: Request, args: ArgsModel):  # type: ignore[valid-type]
            # PreBodyGate already admitted this caller; checked again here
            # so the endpoint is never the unguarded half of the pair.
            profile, user, role, chat, email = admit(request, name)
            kwargs = args.model_dump()
            # A spawned agent's workdir must not silently escape the
            # caller's shard: mcp_server resolves a RELATIVE workdir against
            # the server process CWD (the repo!), which the ContextVar can't
            # reach across the subprocess boundary. Anchor it here instead.
            try:
                shard = shard_for(user, chat)
            except OSError as e:
                # A shard that cannot be created (FILES_DIR is a file, full
                # or read-only disk) is an audited 500, not a bare traceback
                # with no row in the trail.
                raise refuse_admitted(
                    name, profile, (user, role, chat), 500,
                    f"could not create the caller's workspace: "
                    f"{e.strerror or e}")
            if (shard and name in ("start_agent", "start_skill_agent")
                    and not os.path.isabs(kwargs.get("workdir", "."))):
                # normpath collapses "..", so verify the anchored path is
                # still INSIDE the shard — a relative workdir of
                # "../../somewhere" must be an error, not a silent escape.
                # (An ABSOLUTE workdir stays allowed as-is: spawning is
                # admin-only, and pointing an agent at a real project dir
                # is the documented use; the shard anchors relative paths.)
                anchored = os.path.normpath(
                    os.path.join(shard, kwargs.get("workdir", ".")))
                if os.path.commonpath([os.path.normpath(shard), anchored]) \
                        != os.path.normpath(shard):
                    # An attempt to walk out of the shard is exactly what
                    # the trail is for: audited and counted, then refused.
                    raise refuse_admitted(
                        name, profile, (user, role, chat), 400,
                        "relative workdir escapes the caller's workspace")
                kwargs["workdir"] = anchored
            token = _tools.set_base_dir_override(shard) if shard else None
            wtoken = None
            if name == "weather":
                import weather as _weather
                wtoken = _weather.CALLER_NEAR.set(_weather.caller_location(
                    user, os.path.join(REPO_DIR, "openbeast.conf")))
            # Published pages are owned by their publisher: mcp_server's
            # tool functions never see the request, so hand the identity
            # down the same way the workspace shard travels. What travels is
            # the TAILNET-NAMESPACE login (artifact_owner, D21) — never the
            # WebUI UUID, which no reader can ever present.
            otoken, owner = None, ""
            if name in _ARTIFACT_TOOLS:
                # R1: the refusal must ESCAPE. It used to be computed inside
                # a bare `except Exception`, which would have swallowed it
                # and published under whatever the ContextVar still held.
                try:
                    owner = artifact_owner(user, email)
                except HTTPException as e:
                    audit(user, role, chat, name, profile, False, 0, {},
                          f"http {e.status_code}: {e.detail}")
                    with metrics_lock:
                        calls[(name, profile, "error")] += 1
                    raise
                try:
                    import artifact as _artifact
                    # The raw WebUI id rides along as the ALIAS: the store
                    # records it as meta["owner_webui_id"] for PROVENANCE
                    # only (R6) — it authorises nothing, and the API never
                    # returns it. `owner` above is the whole of the identity.
                    otoken = _artifact.set_owner_override(owner, user)
                except Exception:
                    otoken = None
            t0 = time.monotonic()
            ok, err = True, ""
            try:
                result = fn(**kwargs)
            except Exception as e:  # surface as a 500 with the message
                ok, err = False, str(e)
                raise HTTPException(status_code=500, detail={"message": str(e)})
            finally:
                if token is not None:
                    _tools.reset_base_dir_override(token)
                if wtoken is not None:
                    import weather as _weather
                    _weather.CALLER_NEAR.reset(wtoken)
                if otoken is not None:
                    try:
                        import artifact as _artifact
                        _artifact.reset_owner_override(otoken)
                    except Exception:
                        pass
                elapsed = int((time.monotonic() - t0) * 1000)
                audit(user, role, chat, name, profile, ok, elapsed, kwargs,
                      err, owner)
                with metrics_lock:
                    calls[(name, profile, "ok" if ok else "error")] += 1
                    latency_ms[(name,)] += elapsed
            return result

        app.post(
            f"/{name}",
            summary=name.replace("_", " ").title(),
            description=(fn.__doc__ or name).strip(),
            operation_id=name,
        )(endpoint)

    for _name in TOOL_NAMES:
        register(_name)

    # Starlette runs the LAST-added middleware first: Host pinning, then the
    # pre-body gate, then the routes. A rebinding page is refused before it
    # can so much as write a denial row.
    app.add_middleware(PreBodyGate)
    app.add_middleware(PinnedHostMiddleware, allowed_hosts=allowed_hosts,
                       allow_env="OPENBEAST_TOOLS_ALLOWED_HOSTS",
                       allow_ip_literals=True)

    @app.exception_handler(RequestValidationError)
    async def audited_validation_error(request: Request, exc):
        """Malformed arguments from an ADMITTED caller (the gate already
        refused everyone else) — audited and counted, then FastAPI's 422."""
        name = request.url.path.lstrip("/")
        if request.method == "POST" and name in tool_set:
            try:
                profile, user, role, chat, _email = admit(request, name)
                refuse_admitted(name, profile, (user, role, chat), 422,
                                "invalid arguments")
            except HTTPException:
                pass   # unreachable past the gate; admit audited it anyway
        return await request_validation_exception_handler(request, exc)

    @app.get("/health")
    def health():  # liveness for start.sh / healthcheck.sh
        return {"status": "ok", "tools": len(TOOL_NAMES),
                "auth": "keyed" if keyed else "open", "sharding": sharding,
                "identity": "jwt" if jwt_secret else "header"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        """Prometheus text exposition — per-tool call counts + latency.
        Loopback-only like everything else; scrape with a local Prometheus.
        No user labels here (cardinality + privacy) — per-user detail lives
        in the audit log."""
        lines = [
            "# HELP openbeast_tool_calls_total Tool calls by tool/profile/outcome",
            "# TYPE openbeast_tool_calls_total counter",
        ]
        with metrics_lock:
            for (tool, profile, outcome), n in sorted(calls.items()):
                lines.append(
                    f'openbeast_tool_calls_total{{tool="{tool}",'
                    f'profile="{profile}",outcome="{outcome}"}} {n}'
                )
            lines += [
                "# HELP openbeast_tool_latency_ms_total Cumulative tool wall-clock ms",
                "# TYPE openbeast_tool_latency_ms_total counter",
            ]
            for (tool,), ms in sorted(latency_ms.items()):
                lines.append(f'openbeast_tool_latency_ms_total{{tool="{tool}"}} {ms:.0f}')
        return "\n".join(lines) + "\n"

    return app


def bind_is_loopback(host: str) -> bool:
    """True when `host` only listens on this machine — mirrors conf.sh's
    ob_bind_is_loopback (127.*, ::1, [::1], localhost). Anything else
    (0.0.0.0, ::, a LAN/tailnet IP, a hostname, empty) is a network bind."""
    h = (host or "").strip()
    if h.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(h.strip("[]")).is_loopback
    except ValueError:
        return False


def open_exposure_refusal(env=None) -> str | None:
    """The reason to refuse serving, or None when it is safe to start.

    Keyless mode answers every tool — bash included — to anyone who can
    reach the port, and identity headers are taken as sent (see the trust
    note above). That is Phase-1 parity on loopback and remote code
    execution on a LAN/tailnet bind (review identity-rbac-4). conf.sh only
    warns; this is where it is enforced. OPENBEAST_ALLOW_OPEN_TOOLS=true is
    the operator's explicit acknowledgement (mirrors ob_tools_exposed_open)."""
    env = os.environ if env is None else env
    host = env.get("OPENBEAST_BIND", "127.0.0.1")
    if bind_is_loopback(host):
        return None
    if (env.get("OPENBEAST_MCPO_ADMIN_KEY", "").strip()
            or env.get("OPENBEAST_MCPO_GUEST_KEY", "").strip()):
        return None
    if env.get("OPENBEAST_ALLOW_OPEN_TOOLS", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return None
    return (f"refusing to serve the tool server on non-loopback "
            f"BIND_HOST={host!r} with no MCPO_ADMIN_KEY/MCPO_GUEST_KEY: "
            f"anyone who can reach {host}:3001 could run shell commands as "
            f"this user. Run scripts/setup-mcpo-keys.sh, bind 127.0.0.1 "
            f"(remote access via scripts/setup-tailscale.sh), or set "
            f"ALLOW_OPEN_TOOLS=true in openbeast.conf to accept the risk.")


def main() -> None:
    import uvicorn
    host = os.environ.get("OPENBEAST_BIND", "127.0.0.1")
    port = int(os.environ.get("OPENBEAST_TOOLS_PORT", "3001"))
    refusal = open_exposure_refusal()
    if refusal:
        print(f"Error: {refusal}", file=sys.stderr)
        sys.exit(2)
    # This process holds the RBAC keys, the JWT secret and the WebUI admin
    # password in its environ, and it is $PPID (or a grandparent, via
    # start_agent -> runner) of every model-authored shell: make it
    # non-dumpable before it serves anything, not lazily on first spawn.
    _tools.harden_process()
    print(f"OpenBeast identity tool server on {host}:{port} "
          f"({len(TOOL_NAMES)} tools)")
    # Bounded on SIGTERM (see beast-chat's GRACEFUL_SHUTDOWN_S): uvicorn's
    # default waits forever for open requests, and a web fetch or a tool call
    # in flight then held the whole stack's stop open.
    uvicorn.run(create_app(), host=host, port=port, log_level="warning",
                timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
