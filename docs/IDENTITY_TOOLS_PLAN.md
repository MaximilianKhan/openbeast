# Identity-aware chat tools — per-user / per-conversation file isolation

**Status: investigation complete (2026-07-09), implementation decision pending.**
Companion to `docs/RBAC_PLAN.md` (Phase 2) and the "per-conversation file
isolation" item in `docs/TODO.md`.

## What we verified (ground truth, 2026-07-09)

1. **Open WebUI DOES forward identity to external tool servers.** With
   `ENABLE_FORWARD_USER_INFO_HEADERS=true` (already set in our
   `docker-compose.yml` for the router), the tool-server call path
   (`utils/tools.py:167` in the running 0.10.x container) adds:
   - `X-OpenWebUI-User-Id` / `-Name` / `-Email` / `-Role`
   - **`chat_id` and `message_id` headers** (`FORWARD_SESSION_INFO_HEADER_*`)

   So both per-USER and per-CONVERSATION isolation have their key available
   at the HTTP layer. The TODO's assumption that option 2 was "the hard
   path" is stale — WebUI sends the chat id.

2. **mcpo (0.0.20, latest) drops those headers at the MCP boundary.** Its
   `client_header_forwarding` feature filters headers and builds
   `meta = {"headers": ...}` (`utils/main.py:342-349`) — then calls
   `session.call_tool(name, arguments=args)` **without ever passing
   `meta`**. The feature is incomplete upstream for stdio servers. Nothing
   reaches `mcp_server.py`. Confirmed against the installed source; we are
   on the latest release.

## The fork in the road

### Option A — wait for / patch upstream mcpo
Pass `meta` through `call_tool` and read it in FastMCP context. Smallest
diff, but we'd run a fork until (if) it merges. Fragile.

### Option B — own the WebUI tool server (recommended)
Replace mcpo *for the WebUI connection only* with a thin FastAPI app
(`agents/openapi_tools.py`) that serves `agents/tools.py` directly:

- FastAPI generates `openapi.json` natively — WebUI speaks to it exactly as
  it speaks to mcpo today (mcpo itself is FastAPI underneath).
- Headers arrive DIRECTLY: `_base_dir()` can shard the workspace per user
  (`$OPENBEAST_FILES_DIR/<user-id>/`), per chat, or both; the manifest
  (shipped 2026-07-09, `agents/tools.py::_manifest_log`) records into the
  right shard automatically since it keys off the write path.
- Per-profile keys become native: one process, admin + guest keys checked
  per-request, guest surface = web tools — replaces the two-instance MCPO
  dance with less machinery (start.sh gets simpler, not more complex).
- `agents/mcp_server.py` STAYS — it serves OpenCode and any real MCP
  client. Only the WebUI-facing OpenAPI surface moves in-house.
- Drops a pip dependency (mcpo) from the serving path.

Estimated: ~200 lines + tests. The RBAC Phase 2 work (keys in
`openbeast.conf`, `setup-mcpo-keys.sh`, configure-webui bearer wiring)
carries over unchanged — only the process behind the URL changes.

### Option C — per-role isolation only (no new code)
The two keyed MCPO instances already know their caller's PROFILE. But
guests have no file tools, so per-role sharding isolates nothing that
matters. Rejected as a no-op.

## What shipped now regardless (2026-07-09)

**The workspace manifest** (TODO option 3, no identity needed):
`write_file`/`edit_file` landing inside `OPENBEAST_FILES_DIR` append
`{ts, action, path, bytes}` to `.manifest.jsonl` — fail-soft, never
self-indexing, covered by `tests/test_manifest.py`. The system prompt
tells the model to consult it for "what files have I made?". This is the
index half of the isolation story; sharding is the other half.

## Recommendation

Build Option B in a focused session: implement `agents/openapi_tools.py`,
port the Phase 2 key checks into it, shard `_base_dir()` by
`X-OpenWebUI-User-Id` (per-user, option 1) with per-chat subdirs
(option 2) behind a conf knob, verify end-to-end from two WebUI accounts,
then retire the guest MCPO instance. Retention/GC for the manifest rides
along (delete entries + files older than `FILES_RETENTION_DAYS`, off by
default).
