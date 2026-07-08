# RBAC Plan — multi-user OpenBeast without handing out the filesystem

**Status: PLANNED (2026-07-07) — Phase 0 is REQUIRED before creating the
first non-admin account.** Nothing implemented yet.

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

## Building blocks (verified in our running instance)

Open WebUI's tool-server connections each carry:
- `config.access_grants` — restrict a connection to specific users/groups
  (empty = everyone). Checked per-user at tool-resolution time.
- `config.function_name_filter_list` — expose only a subset of the
  server's functions on that connection.

So ONE MCPO instance can back **two logical tool profiles** as two
connections with different filters and grants. `mcpo` also supports
`--api-key` and multi-server config files if we later want hard separation.

## Tool classification (the profiles)

| Profile | Tools | Who |
|---|---|---|
| `arsenal-full` | all 17 (bash, read/write/edit file, list_files, grep, fetch, web_search, agent mgmt, skills) | admin group only |
| `arsenal-guest` | `web_search`, `fetch` | everyone (family) |

Deliberately NOT in guest: `read_file`/`list_files`/`grep` (private-data
reads), `bash` (everything), agent tools (spawn = privilege amplification),
skills (loads instructions into context — audit surface).

## Phases

### Phase 0 — close the default before the first guest account (~10 min)
Set `access_grants` on the existing `local-mcp` connection to the admin
group so `user`-role accounts resolve zero local tools. Family can chat,
nothing else changes for Max. This is the "don't create accounts before
this" gate. One config PUT via `configure-webui.sh`-style API call; make
`configure-webui.sh` assert it thereafter (idempotent).

### Phase 1 — guest profile (~half day)
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
1. Split MCPO into two instances with distinct `--api-key`s
   (`arsenal-full` on :3001, `arsenal-guest` on :3002); keys live in
   `openbeast.conf` and per-connection headers in WebUI. Now possession of
   the full-profile key — not UI policy — gates the dangerous tools.
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
4. Guest `fetch` of `file:///etc/passwd` → blocked by sandbox policy
   (Phase 2; fetch tool should also refuse non-http schemes at app level).
5. Config-drift check green in `./scripts/healthcheck.sh` (Phase 3).

## Out of scope (deliberately)
- Per-user Unix accounts / containers per user — overkill for a family
  deployment; revisit if OpenBeast ever serves untrusted users.
- OpenCode multi-user RBAC — OpenCode is Max's terminal tool; guests don't
  get it. The tailnet + (optional) API key is its boundary.
- Row-level chat privacy hardening inside Open WebUI — upstream's job;
  accounts already have separate histories.

## Open questions for Max
1. Should guests get `fetch` at all, or web_search only? (fetch can reach
   internal URLs — mitigated in Phase 2 by sandbox + scheme filtering.)
2. Kids vs adults: one `family` group or two tiers (e.g. teens also get
   read-only `read_file` on a shared folder)?
3. Phase 2 timing: land with Phase 1, or after Sandlock ships in Arsenal
   Phase 1a?
