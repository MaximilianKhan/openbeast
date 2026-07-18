# RBAC Plan — multi-user OpenBeast without handing out the filesystem

**Status: Phase 0 + Phase 1 LIVE (2026-07-07); Phase 2 router-identity +
guest-fetch LIVE (2026-07-08) — verified with the real Open WebUI
access-control code.** A `user`-role account resolves `web_search` +
`fetch` (SSRF-guarded) ONLY (bash/file/agent tools denied at the connection
layer); an `admin` account gets all 15. Baked into `configure-webui.sh` so
fresh installs land RBAC'd. Remaining Phase 2 hardening: per-profile MCPO
keys + Sandlock (below-app enforcement).

> ✅ **FIXED (2026-07-08) — the agent-spawn router is now identity-aware.**
> Previously, with `AGENT_ROUTER=true`, `agents/router.py` classified
> **every** chat turn — guest turns included — and could spawn a
> full-filesystem agent via MCPO directly, bypassing the WebUI
> connection-level grants. Now `docker-compose.yml` sets
> `ENABLE_FORWARD_USER_INFO_HEADERS=true` so Open WebUI forwards
> `X-OpenWebUI-User-Role` on every model request, and the router gates its
> spawn path on it: `admin` → spawn allowed; any other role → prefilter +
> classify skipped entirely (zero added latency, no spawn path); header
> absent → allowed by default (single-user/no-auth installs send no
> headers), or denied when `OPENBEAST_ROUTER_REQUIRE_IDENTITY=true`
> (recommended for hardened multi-user installs).
> **Remaining caveat:** a stack started before 2026-07-08 runs the old
> router code until its next restart, and disabling WebUI header
> forwarding re-opens the fail-open default — set
> `OPENBEAST_ROUTER_REQUIRE_IDENTITY=true` if in doubt.

## How to give someone access (the whole UX)

1. **Admin Panel → Users → Add User** (or let them sign up — new signups
   land as `pending`, no access, until you act).
2. Set their **role**: `user` = guest (web search + guarded fetch), `admin`
   = full arsenal. **That role dropdown IS the RBAC tier.** Nothing else to
   touch.
3. They log in from any device on the tailnet and chat. Guests literally do
   not have `bash`/`edit_file` in their model's tool schema — not "denied,"
   *absent*.

To demote/revoke: flip the role back to `user`, or delete the account.

## The problem

Since the Tailscale rollout the WebUI is multi-user (family accounts are the
point), and since the default-tools change every model carries the full
arsenal (`meta.toolIds = ["server:1"]`). The arsenal executes in MCPO **as
the `max` Unix user**. Consequences today:

- Any signed-in WebUI user — including a `user`-role family account — can
  ask the model to run `bash`, `edit_file`, `write_file` and it will execute
  with Max's full filesystem permissions.
- MCPO itself (`:3001`) is unauthenticated; loopback-only saves us from the
  network, but nothing distinguishes callers.

Family members need chat + web search. They do not need — and must not
have — shell access, file mutation, file *reads* (private data), or agent
spawning.

## Building blocks (verified in the running Open WebUI source, 2026-07-07)

Read straight out of `utils/access_control/__init__.py`,
`utils/tools.py`, `utils/misc.py`:

- **`BYPASS_ADMIN_ACCESS_CONTROL` defaults `True`** → an `admin`-role user
  **always resolves every tool**, regardless of a connection's grants.
  Max stays fully armed without any per-connection grant for himself.
- **`config.access_grants`** on a tool-server connection, checked per-user
  at tool-resolution time (`has_connection_access` → `has_access`):
  - empty / missing → **private = admin-only** (non-admins denied).
  - `[{"principal_type":"user","principal_id":"*","permission":"read"}]`
    → **public** (everyone).
  - `{"principal_type":"group","principal_id":"<group_id>",...}` → scope to
    a WebUI group (the future middle tier).
  A denied connection is **silently skipped** — its tools never enter the
  model's schema (not "permission denied" — they don't exist for that user).
- **`config.function_name_filter_list`** (comma-separated, `is_string_allowed`):
  allowlist by default; a `!name` entry blocks. So `"!web_search"` = every
  tool *except* web_search; `"web_search"` = only web_search.

So ONE MCPO instance backs **two connections to the same URL** with
different filters + grants — no second MCPO process needed for v1. `mcpo
--api-key` and split instances are the Phase-2 hard-enforcement upgrade.

## The tiers (v1 — deliberately just two, = the native WebUI role)

**A user's tier IS their Open WebUI role.** No new concepts, no groups to
manage. Assign in Admin Panel → Users → role dropdown (or the API).

| Tier | WebUI role | Tools they get | How |
|---|---|---|---|
| **Owner** | `admin` | all 15 (bash, file r/w/edit, list_files, grep, fetch, web_search, agent mgmt, skills) | `BYPASS_ADMIN_ACCESS_CONTROL` — automatic |
| **Guest** (family) | `user` | **`web_search` + `fetch`** (scheme-filtered, private-network-blocked) | public grant on a filtered connection |
| *(pending)* | `pending` | none (no stack access) | default for new signups until approved |

**Why guest = web_search + guarded fetch (not file reads).** Max's rule:
"search the web and anything that can't harm the OS; no local filesystem."
- `web_search` → SearXNG, no local access. **Safe. In.**
- `fetch` → **In since 2026-07-08**, now that it's SSRF-guarded in
  `agents/tools.py`: http/https schemes only, every hostname resolved
  (all A/AAAA records) and refused if ANY address is
  loopback/private/link-local/reserved, and every redirect hop re-validated.
  `file://`, `http://127.0.0.1:3001` (MCPO), `http://169.254.169.254`
  (metadata) are all refused. The guard applies to admins too —
  defense in depth.
- `read_file`/`list_files`/`grep` → read the local FS (private data). Max
  said no filesystem — **out.**
- `bash` / `write_file` / `edit_file` → mutate the OS. **Out.**
- agent mgmt / skills → privilege amplification + context injection. **Out.**

### Two-connection wiring (implemented by `configure-webui.sh`)
- **`local-mcp`** (privileged): `function_name_filter_list =
  "!web_search,!fetch"` (all 15 other tools), `access_grants = []` →
  admin-only.
- **`local-mcp-web`** (public): `function_name_filter_list =
  "web_search,fetch"`, `access_grants = [{user:*}]` → everyone.
- Every model's `meta.toolIds = ["server:<priv>","server:<web>"]`.
  - Admin resolves both → 13 + web_search + fetch = all 15, **no duplicate**
    (web_search and fetch live only on the public connection).
  - Guest resolves only the public one → **web_search + fetch, nothing
    else.**

Future middle tier ("trusted", e.g. an older kid): add a WebUI group, a
third connection filtered to read-only `read_file`/`list_files`/`grep`
scoped via Sandlock to a shared folder, granted to that group. Deferred —
not built until asked.

## Phases

### Phase 0 — close the default before the first guest account (~10 min)
Set `access_grants` on the existing `local-mcp` connection to the admin
group so `user`-role accounts resolve zero local tools. Family can chat,
nothing else changes for Max. This is the "don't create accounts before
this" gate. One config PUT via `configure-webui.sh`-style API call; make
`configure-webui.sh` assert it thereafter (idempotent).

### Phase 1 — guest profile (~half day)
**As-built differs:** guest got `web_search` only at first; `fetch` joined
the guest tier 2026-07-08 once SSRF-guarded — see the wiring section above.
1. Add a second tool-server connection to the same MCPO URL:
   `arsenal-guest`, `function_name_filter_list: web_search,fetch`,
   `access_grants: []` (everyone).
2. WebUI groups: create `family`; new signups stay `pending` until Max
   assigns them.
3. Model surface for guests: a curated model entry (e.g. "Qwen Chat") whose
   `toolIds` reference the guest connection only; restrict the full-arsenal
   model entries to the admin group via model access control. Guests get a
   clean picker with one model that can search the web — nothing that can
   touch the disk.
4. `configure-webui.sh` owns all of this declaratively (profiles, grants,
   model wiring) so a fresh install lands RBAC'd by default.

### Phase 2 — enforce below the app layer (~1–2 days, ties into Arsenal Phase 1a)
The WebUI checks are policy, not enforcement — a bug or a second frontend
(OpenCode config on a guest laptop, direct `:8443` API use) bypasses them.
Defense in depth:
0. ✅ **DONE 2026-07-08 — router identity gate.** `docker-compose.yml` sets
   `ENABLE_FORWARD_USER_INFO_HEADERS=true`; `agents/router.py` reads
   `X-OpenWebUI-User-Role` and only spawns for `admin` (non-admin turns skip
   classification entirely; absent header is fail-open unless
   `OPENBEAST_ROUTER_REQUIRE_IDENTITY=true`). Unit-tested in
   `tests/test_router.py` (`_spawn_allowed`).
0b. ✅ **DONE 2026-07-08 — fetch SSRF guard + guest fetch.** `fetch()` in
   `agents/tools.py` refuses non-http(s) schemes and any host whose
   resolution includes loopback/private/link-local/reserved addresses, with
   per-hop redirect re-validation. Guest connection filter widened to
   `web_search,fetch`. Tests: `tests/test_fetch_guards.py`.
1. ✅ **DONE 2026-07-09 — per-profile MCPO keys (opt-in).** Enable with
   `scripts/setup-mcpo-keys.sh` (writes `MCPO_ADMIN_KEY`/`MCPO_GUEST_KEY` to
   `openbeast.conf`; `--rotate` to replace). With both keys set, start.sh
   launches TWO instances: admin (:3001, all 15 tools, admin key) and guest
   (:`MCPO_GUEST_PORT`, default 3002, guest key) — and the guest instance
   runs with `OPENBEAST_MCP_TOOLS="web_search,fetch"`, a REGISTRATION
   allowlist in `agents/mcp_server.py`: denied tools don't exist on that
   server (a guest-key holder probing `/bash` gets 404 — there is no tool to
   authorize). configure-webui.sh binds each WebUI connection to its
   instance with a Bearer key; the router presents the admin key for
   `start_agent`; healthcheck.sh watches and restarts both instances keyed.
   Verified live: tool endpoints answer 401 (no key) / 403 (wrong key) /
   200 (right key). Keys absent = Phase 1 behavior. Tests:
   `tests/test_mcp_allowlist.py`.
   **Superseded 2026-07-09 (same day):** the two mcpo instances were
   replaced by the identity tool server `agents/openapi_tools.py` — ONE
   process enforcing both keys (admin=all tools, guest=web-only, denied
   tools 404), plus per-user workspace sharding and a per-call audit
   trail. Same keys, same conf, same WebUI connections. See
   docs/IDENTITY_TOOLS_PLAN.md; tests/test_identity_server.py.
   **Updated 2026-07-17:** keyed mode now engages with EITHER key set — a
   missing key disables that profile (fail closed). Previously BOTH keys
   were required, so configuring only the admin key silently left the
   server fully open. Tests: `test_single_key_fails_closed`.
2. Wrap guest-profile tool execution in the **Sandlock** sandbox from
   [TOOL_ARSENAL_RESEARCH.md](TOOL_ARSENAL_RESEARCH.md): read-only
   filesystem view, network allowed, no exec outside the tool. One policy
   file per profile — the RBAC and Arsenal workstreams converge here.
3. Note: the llama-server API (`:8443` via tailscale) is chat-only — tools
   are executed by frontends, not by llama.cpp — so API access without a
   tool-server key yields inference, not filesystem. Document this
   explicitly; enable `LLAMA_API_KEY` if the tailnet ever includes devices
   Max doesn't own.

### Phase 3 — audit + guardrails (~half day, after real guest usage)
- Per-tool-call audit log in `mcp_server.py` (caller profile, tool, args
  digest, timestamp) → `agents/logs/toolcalls-*.jsonl`.
- Guest rate limits (tool calls/hour) in the guest MCPO wrapper.
- Healthcheck row: assert grants/profiles still match the declared policy
  (config drift detection).

## Verification checklist
1. Family test account in `family` group: chat works, web search works,
   "list the files in /home/max" → model has no such tool (not "permission
   denied" — the tool must not exist in its schema).
2. Same prompt as admin → works.
3. Direct `curl :3001/bash` with no/guest key → 401 (Phase 2).
4. ✅ Guest `fetch` of `file:///etc/passwd` → refused at app level
   ("Error: fetch blocked: scheme 'file' not allowed"); same for
   `http://127.0.0.1:...` and private/link-local targets. Sandlock adds a
   second layer later in Phase 2.
5. Config-drift check green in `./scripts/healthcheck.sh` (Phase 3).

## Out of scope (deliberately)
- Per-user Unix accounts / containers per user — overkill for a family
  deployment; revisit if OpenBeast ever serves untrusted users.
- OpenCode multi-user RBAC — OpenCode is Max's terminal tool; guests don't
  get it. The tailnet + (optional) API key is its boundary.
- Row-level chat privacy hardening inside Open WebUI — upstream's job;
  accounts already have separate histories.

## Known dependencies / caveats (verified 2026-07-07)
- **Relies on `BYPASS_ADMIN_ACCESS_CONTROL=True`** (Open WebUI default): the
  privileged connection is admin-only via *empty* `access_grants`, which
  works because admins bypass grant checks. If that env is set `False`,
  admins are also denied the privileged tools (no explicit admin grant
  exists). Don't flip it without adding an admin group grant first.
- Enforcement is at tool-resolution in `utils/tools.py` — confirmed the
  model-`toolIds` path calls `has_connection_access` and skips denied
  connections before the function filter. A denied tool is *absent* from the
  schema, not "permission denied".

## Open questions for Max
1. ~~Should guests get `fetch` at all, or web_search only?~~ **Resolved
   2026-07-08:** guests get `fetch` now that it's scheme-filtered and
   private-network-blocked at app level.
2. Kids vs adults: one `family` group or two tiers (e.g. teens also get
   read-only `read_file` on a shared folder)?
3. Phase 2 timing: land with Phase 1, or after Sandlock ships in Arsenal
   Phase 1a?
