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

Auth (plan §5):
  reads   `Tailscale-User-Login` must appear in OPENBEAST_ARTIFACT_OPERATORS
          (falls back to OPENBEAST_CHAT_OPERATORS; BOTH unset = single-user
          rig, everyone allowed). An unlisted login gets 404, never 403 —
          a 403 would confirm the service exists. A private artifact owned
          by someone else is 404 for the same reason.
  writes  POST/PATCH/DELETE need the proof-of-locality token
          (`X-OpenBeast-Local` == .run/artifact-local.token, minted 0600 at
          startup, agents/edge.py:412 pattern). A phone on the tailnet can
          view; only the rig can publish.
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
import json
import os
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import artifact as store  # noqa: E402

REPO_DIR = os.path.dirname(_HERE)
UI_DIR = os.path.join(_HERE, "artifact_ui")

_HDR_LOGIN = "tailscale-user-login"
_HDR_LOCAL = "x-openbeast-local"

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


def _mint_local_token() -> str:
    """Shared secret proving the caller can read this box's filesystem.

    Not the peer address: `tailscale serve` reverse-proxies every remote
    caller into 127.0.0.1, so loopback proves nothing (agents/edge.py:412
    learned this the hard way). Regenerated each start, 0600.
    """
    token = uuid.uuid4().hex
    path = os.path.join(_run_dir(), "artifact-local.token")
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
    return _html.escape("" if value is None else str(value), quote=True)


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


def _fill(template: str, values: dict) -> str:
    for key, val in values.items():
        template = template.replace("{{%s}}" % key, val)
    return template


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
    owner: str | None = None


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


# --- app ---------------------------------------------------------------------

def create_app() -> FastAPI:
    """App factory — reads config at call time so tests can vary env."""
    operators = _operators()          # ordered; [0] owns CLI publishes
    operator_set = set(operators)
    local_token = _mint_local_token()
    audit_path = os.path.join(_run_dir(), "artifact-audit.jsonl")

    metrics_lock = threading.Lock()
    hits: dict = defaultdict(int)        # (route, outcome) -> count
    latency_ms: dict = defaultdict(float)

    app = FastAPI(
        title="OpenBeast artifacts",
        version="1.0",
        description="Durable URLs for model-authored HTML "
                    "(see agents/artifact_server.py).",
    )
    app.state.local_token = local_token
    app.state.operators = operators

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

    @app.middleware("http")
    async def _audit_and_meter(request: Request, call_next):
        t0 = time.monotonic()
        request.state.extra = {}
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            ms = int((time.monotonic() - t0) * 1000)
            audit({"ts": _now(),
                   "login": request.headers.get(_HDR_LOGIN) or None,
                   "route": request.url.path, "id": None, "n": None,
                   "outcome": "error", "ms": ms})
            with metrics_lock:
                hits[(request.url.path, "error")] += 1
            raise
        ms = int((time.monotonic() - t0) * 1000)
        route = request.scope.get("route")
        label = getattr(route, "path", None) or request.url.path
        params = request.scope.get("path_params") or {}
        entry = {
            "ts": _now(),
            "login": request.headers.get(_HDR_LOGIN) or None,
            "route": label,
            "id": params.get("artifact_id"),
            "n": params.get("n"),
            "outcome": status,
            "ms": ms,
        }
        entry.update(getattr(request.state, "extra", {}) or {})
        audit(entry)
        with metrics_lock:
            hits[(label, "ok" if status < 400 else str(status))] += 1
            latency_ms[(label,)] += ms
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

    def viewer_of(request: Request) -> str | None:
        """The caller's login, or None on a rig with no allowlist.

        Raises 404 (never 403) for an unlisted login: to a stranger this
        service does not exist.
        """
        login = (request.headers.get(_HDR_LOGIN) or "").strip()
        if not operators:
            return login.lower() or None
        if login and login.lower() in operator_set:
            return login.lower()
        if is_local(request):
            # The rig itself (CLI / MCP tool with the locality token). It acts
            # as the first operator, which is who a CLI publish belongs to.
            return operators[0] if operators else None
        raise HTTPException(status_code=404, detail="Not Found")

    def require_local(request: Request) -> None:
        """Write guard, used as a DEPENDENCY: FastAPI solves dependencies
        before it validates the request body, so a malformed publish from the
        tailnet gets the same 404 as a well-formed one — a 422 would confirm
        the route exists."""
        if not is_local(request):
            # 404, not 403: writes are invisible from the tailnet.
            raise HTTPException(status_code=404, detail="Not Found")

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

    def default_owner(request: Request, body_owner: str | None) -> str | None:
        if body_owner and body_owner.strip():
            return body_owner.strip()
        login = (request.headers.get(_HDR_LOGIN) or "").strip()
        if login:
            return login.lower()
        return operators[0] if operators else None

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
        rows = store.list_artifacts(viewer=viewer, limit=200)
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

    @app.get("/api/artifacts/health")
    def health(request: Request):
        # Liveness for doctor.sh / healthcheck.sh. No allowlist check: it
        # leaks nothing but "the service is up", and probes have no login.
        try:
            root = store.store_root()
            count = len(store.list_artifacts(limit=0))
            ok = os.path.isdir(root)
        except Exception as e:
            return {"status": "error", "detail": str(e)}
        return {"status": "ok" if ok else "error", "artifacts": count,
                "auth": "allowlist" if operators else "open",
                "store": root}

    @app.get("/api/artifacts")
    def api_list(request: Request, limit: int = 25, owner: str = ""):
        viewer = viewer_of(request)
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
                owner=default_owner(request, body.owner))
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
                meta = store.set_visibility(artifact_id, body.visibility)
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
    def metrics():
        """Prometheus text exposition, hand-rolled (no client dep), same as
        the identity tool server. No login labels: cardinality + privacy —
        per-caller detail lives in the audit log."""
        lines = [
            "# HELP openbeast_artifact_requests_total Requests by route/outcome",
            "# TYPE openbeast_artifact_requests_total counter",
        ]
        with metrics_lock:
            for (route, outcome), n in sorted(hits.items()):
                lines.append(
                    f'openbeast_artifact_requests_total{{route="{route}",'
                    f'outcome="{outcome}"}} {n}')
            lines += [
                "# HELP openbeast_artifact_latency_ms_total Cumulative ms by route",
                "# TYPE openbeast_artifact_latency_ms_total counter",
            ]
            for (route,), ms in sorted(latency_ms.items()):
                lines.append(
                    f'openbeast_artifact_latency_ms_total{{route="{route}"}} {ms:.0f}')
        try:
            lines += [
                "# HELP openbeast_artifacts_stored Artifacts in the store",
                "# TYPE openbeast_artifacts_stored gauge",
                f"openbeast_artifacts_stored {len(store.list_artifacts(limit=0))}",
            ]
        except Exception:
            pass
        return "\n".join(lines) + "\n"

    return app


def main() -> None:
    import uvicorn
    host = os.environ.get("OPENBEAST_BIND", "127.0.0.1")
    port = int(os.environ.get("OPENBEAST_ARTIFACT_PORT", "3004"))
    app = create_app()
    print(f"OpenBeast artifact server on {host}:{port} "
          f"(store {store.store_root()})")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
