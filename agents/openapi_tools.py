#!/usr/bin/env python3
"""OpenBeast identity-aware tool server — the WebUI-facing OpenAPI surface.

Replaces mcpo for the Open WebUI connection (docs/IDENTITY_TOOLS_PLAN.md
Option B). mcpo converts MCP→OpenAPI generically but drops the HTTP headers
Open WebUI forwards (X-OpenWebUI-User-Id/-Role, X-OpenWebUI-Chat-Id) at the
MCP boundary — so tools could never know WHO was calling. This server owns
the HTTP layer, which buys three things mcpo structurally can't provide:

  IDENTITY   Per-user workspace sharding: relative tool paths (and the
             .manifest.jsonl index) resolve inside
             $OPENBEAST_FILES_DIR/users/<user-id>[/chats/<chat-id>], so one
             family member's files are namespaced away from another's.
             Headerless (single-user) requests keep the shared root —
             fresh-install behavior is unchanged.
  PROFILES   Both RBAC Phase 2 keys are checked natively in ONE process:
             the admin key reaches all tools, the guest key only
             web_search/fetch (403 elsewhere), no key configured = open
             loopback Phase-1 parity. Replaces the two-instance MCPO split.
  AUDIT      Every tool call is appended to .run/tool-audit.jsonl with
             ts/user/role/chat/tool/status/ms and an argument DIGEST
             (never the arguments themselves — chats stay private).

agents/mcp_server.py remains the MCP (stdio) surface for OpenCode and any
real MCP client; this module imports and serves the same 15 tool functions,
so the two surfaces cannot drift.

Env:
  OPENBEAST_TOOLS_PORT        listen port          (default 3001)
  OPENBEAST_BIND              bind address         (default 127.0.0.1)
  OPENBEAST_MCPO_ADMIN_KEY    admin profile key    (both set => auth on)
  OPENBEAST_MCPO_GUEST_KEY    guest profile key
  OPENBEAST_FILES_SHARDING    off | user | chat    (default user)
  OPENBEAST_FILES_DIR         workspace root (start.sh exports it)

Trust note: identity headers are accepted as sent. On this stack the only
network path to this port is loopback or WebUI itself; a caller who can
forge headers here can already reach every service directly. Signed-JWT
identity (Open WebUI's FORWARD_USER_INFO_HEADER_JWT_SECRET) is the
enterprise upgrade — see docs/TODO.md.
"""
import hashlib
import hmac
import inspect
import json
import os
import re
import sys
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from pydantic import create_model

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import mcp_server as impl  # noqa: E402  (plain callables; FastMCP decor returns fn)
import tools as _tools     # noqa: E402

REPO_DIR = os.path.dirname(_HERE)

# The full WebUI tool surface — same 15 functions the MCP server exposes.
TOOL_NAMES = [
    "bash", "read_file", "write_file", "edit_file", "list_files", "grep",
    "fetch", "web_search", "skill",
    "start_agent", "start_skill_agent", "check_agent", "list_agents",
    "stop_agent", "tail_agent",
]
GUEST_TOOLS = {"web_search", "fetch"}

_HDR_USER = "x-openwebui-user-id"
_HDR_ROLE = "x-openwebui-user-role"
_HDR_CHAT = "x-openwebui-chat-id"

# Shard path components come from headers — sanitize hard.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize(component: str) -> str:
    s = _UNSAFE.sub("_", component)[:64].strip("._")
    return s or "anonymous"


def create_app() -> FastAPI:
    """App factory — reads config at call time so tests can vary env."""
    admin_key = os.environ.get("OPENBEAST_MCPO_ADMIN_KEY", "").strip()
    guest_key = os.environ.get("OPENBEAST_MCPO_GUEST_KEY", "").strip()
    keyed = bool(admin_key and guest_key)
    sharding = os.environ.get("OPENBEAST_FILES_SHARDING", "user").strip().lower()
    if sharding not in ("off", "user", "chat"):
        sharding = "user"
    files_dir = os.environ.get("OPENBEAST_FILES_DIR", "")
    audit_path = os.path.join(REPO_DIR, ".run", "tool-audit.jsonl")

    app = FastAPI(
        title="OpenBeast local tools",
        version="1.0",
        description="Identity-aware tool server (see agents/openapi_tools.py).",
    )

    def check_auth(request: Request, tool: str) -> str:
        """Returns the caller's profile; raises 401/403 like keyed mcpo did."""
        if not keyed:
            return "open"
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token:
            raise HTTPException(status_code=401, detail="API key required")
        if hmac.compare_digest(token, admin_key):
            return "admin"
        if hmac.compare_digest(token, guest_key):
            if tool not in GUEST_TOOLS:
                # The guest profile has no such tool — mirror the old guest
                # instance, where denied tools did not exist at all.
                raise HTTPException(status_code=404, detail="Not Found")
            return "guest"
        raise HTTPException(status_code=403, detail="Invalid API key")

    def shard_for(request: Request) -> str | None:
        """Workspace shard for this caller, or None for the shared root."""
        if not files_dir or sharding == "off":
            return None
        user = request.headers.get(_HDR_USER, "").strip()
        if not user:
            return None  # single-user / no-auth install: shared root
        parts = [os.path.expanduser(files_dir), "users", _sanitize(user)]
        if sharding == "chat":
            chat = request.headers.get(_HDR_CHAT, "").strip()
            if chat:
                parts += ["chats", _sanitize(chat)]
        shard = os.path.join(*parts)
        os.makedirs(shard, mode=0o700, exist_ok=True)
        return shard

    def audit(request: Request, tool: str, profile: str, ok: bool,
              ms: int, args: dict, err: str = "") -> None:
        """Append-only call log. Argument CONTENTS never leave the request —
        only a digest and size, so the audit trail can't leak chats."""
        try:
            blob = json.dumps(args, sort_keys=True, default=str).encode()
            entry = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "user": request.headers.get(_HDR_USER, "") or None,
                "role": request.headers.get(_HDR_ROLE, "") or None,
                "chat": request.headers.get(_HDR_CHAT, "") or None,
                "profile": profile,
                "tool": tool,
                "ok": ok,
                "ms": ms,
                "args_sha256": hashlib.sha256(blob).hexdigest()[:16],
                "args_bytes": len(blob),
            }
            if err:
                entry["error"] = err[:200]
            os.makedirs(os.path.dirname(audit_path), exist_ok=True)
            with open(audit_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # the audit trail must never break the tool call

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
            profile = check_auth(request, name)
            kwargs = args.model_dump()
            shard = shard_for(request)
            token = _tools.set_base_dir_override(shard) if shard else None
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
                audit(request, name, profile, ok,
                      int((time.monotonic() - t0) * 1000), kwargs, err)
            return result

        app.post(
            f"/{name}",
            summary=name.replace("_", " ").title(),
            description=(fn.__doc__ or name).strip(),
            operation_id=name,
        )(endpoint)

    for _name in TOOL_NAMES:
        register(_name)

    @app.get("/health")
    def health():  # liveness for start.sh / healthcheck.sh
        return {"status": "ok", "tools": len(TOOL_NAMES),
                "auth": "keyed" if keyed else "open", "sharding": sharding}

    return app


def main() -> None:
    import uvicorn
    host = os.environ.get("OPENBEAST_BIND", "127.0.0.1")
    port = int(os.environ.get("OPENBEAST_TOOLS_PORT", "3001"))
    print(f"OpenBeast identity tool server on {host}:{port} "
          f"({len(TOOL_NAMES)} tools)")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
