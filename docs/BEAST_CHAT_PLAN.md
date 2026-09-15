# beast-chat Plan — remote access to the rig's live sessions

**Status: DESIGN, DECISIONS LOCKED (2026-09-14). Nothing built.** Max
ratified the three open questions the same day (see the bottom of this
doc): phone may start agents (chat scope), push deferred with a TODO, name
`beast-chat` / conf `BEAST_CHAT=true` / port `:8445` locked. Build lane = a worktree
(`../openbeast-chat`), because Phases 0–1 touch `agents/runner.py`, a
harness file, and the T1.17 / Tier-3 campaign is mid-flight on the main
tree. Merge at a campaign boundary (see "Known dependencies").

The one-line pitch: what Claude Code's mobile app does for Claude Code
sessions, OpenBeast does for its own agent sessions. From a phone on the
tailnet: list every agent and job running on the rig, watch its transcript
stream live, send it a message that lands at its next turn, stop it, start a
new one. Reattach after the phone sleeps and pick up exactly where the
stream left off.

## What we verified (ground truth, 2026-09-14)

Most of the server side exists. The gaps are specific.

**Already there, reusable as-is**

| Piece | Where | Note |
|---|---|---|
| Agent session registry | `agents/mcp_server.py:228-243` (`_AgentRecord`, `_agents`) | in-memory only |
| Agent lifecycle tools | `start_agent / check_agent / list_agents / stop_agent / tail_agent` | MCP + REST |
| Authenticated HTTP surface over those tools | `agents/openapi_tools.py` (:3001) | JWT identity, RBAC, per-call audit, `/metrics` |
| Durable transcript | `agents/logs/agent-<id>.jsonl`, written by `log_event()` (`runner.py:399-402`) | typed events: `spawn start iteration assistant tool_call compaction error done max_iterations` |
| Replay from transcript | `_rebuild_messages_from_log` (`runner.py:122-160`), `_orphaned_log_report` (`mcp_server.py:400`) | already survives a server restart for `check_agent` |
| Per-turn external-state injection | `_with_plan()` (`runner.py:289-301`, called at `:456`) | the plan block is re-read every turn and never stored; steering wants the same property |
| Tailnet publish with per-path scoping | `scripts/setup-tailscale.sh:186` (`--set-path=/api/slot`) | beast-slot's pattern |
| Per-device keys + registry | `scripts/clients.sh`, `.run/clients.json` v1, `edge.py:126 Registry` | unknown fields round-trip, so a `scopes` field is additive |
| Proof-of-locality token | `edge.py:412-460` | `tailscale serve` makes every remote caller look like loopback; do not reinvent |
| Offset-cursor SSE reattach, reference design | `llama.cpp/tools/server/server-stream.cpp`, `GET /v1/stream?conv_id&from=N` | for inference streams; we lift the contract, not the code |
| Extension that serves HTML+JSON on its own port | `extensions/dashboard/` | stdlib http.server, polling |

**The four gaps**

1. **No steering.** `runner.py` reads nothing mid-run: no stdin, no signal
   handler, no file watch. Injecting a message today means kill + `--resume`.
2. **No live tail cursor.** `tail_agent` (`mcp_server.py:826-859`) returns
   the last N lines, 50 KB cap, no offset. A poller re-downloads the same
   bytes. All five tools return formatted text, not JSON.
3. **Registry dies with the tool server.** `_agents` is in-memory,
   `list_agents` never scans disk (`:761-785`), and the `atexit` hook
   SIGTERMs every agent process group on server exit (`:246-256`).
   Restart the tool server and every live agent dies and vanishes.
4. **No published route.** beast-gate allowlists exactly five inference
   paths to one hardcoded upstream (`edge.py:94-100`, `:668`). `:3001` is
   deliberately never published. The dashboard is polled, not streamed.

Two facts that shape the design:

- Eval units are agent sessions too (`evals/run_eval.py:594` spawns
  `runner.py` as a subprocess with `OPENBEAST_TASK_PATHS` in the child env).
  Steering an eval unit would silently corrupt a paired measurement. Eval
  mode must disable the inbox, hard.
- The campaign orchestrators (`scratch/campaign_master.sh`, stage scripts)
  are plain bash. They are the sessions Max most wants to see from a phone
  right now, and they are not agents. The design needs a "job" session
  kind, not just "agent".

## What beast-chat is and is not

**Is:** an operator console for the rig's own sessions. Sessions come in
two kinds: `agent` (a `runner.py` process: MCP-spawned, `agent.sh`,
`openbeast-client agent`, eval units read-only) and `job` (any long-running
command registered through a thin wrapper: campaigns, quantization runs,
benchmarks).

**Is not:** a replacement for Open WebUI (chat history already syncs there,
published at `:443`), a public-internet service (tailnet only, no funnel),
or a way to reach Claude Code sessions on the rig. Claude Code has its own
Remote Control for that. Voice, multi-user chat rooms, and cloud fallback
stay rejected per the ODS decision.

## Architecture

Five pieces, in dependency order. Each is independently useful.

### 1. Session ledger (`agents/sessions.py`)

The durable source of truth, written by the **session itself**, not the
spawner, so every spawn path appears without changing the spawners.

- `.run/sessions/<id>.json`: `{id, kind, title, pid, pgid, started_at,
  updated_at, state, workdir, model, transcript, inbox, cursor, meta}`.
  `state ∈ running | done | failed | stopped | lost`. `lost` = pid gone
  without a terminal event (the crash case).
- Agent sessions: `runner.py` writes the record at `start`, touches
  `updated_at` on every `log_event`, finalizes on `done` /
  `max_iterations` / unhandled exception. Session id = the existing
  `agent_id` when spawned by MCP, else derived from the log filename.
- Job sessions: `scripts/job.sh run --title "T1.17 campaign" -- bash
  scratch/campaign_master.sh` registers the record, tees stdout+stderr to
  `.run/sessions/<id>.log`, marks terminal state from the exit code.
- `list_sessions()` scans the directory, reconciles `running` records
  against `/proc/<pid>` (pid + start time, not pid alone), and marks
  `lost`. Old `done` records prune after 30 days; transcripts stay where
  they are.
- `mcp_server.list_agents` and `check_agent` read the ledger first and the
  in-memory map second. Gap 3 closes here.
- **Durability change:** the `atexit` kill applies only to sessions the
  tool server started without `detach`. beast-chat starts agents detached
  by default. A tool-server restart no longer kills them.

### 2. Steering inbox (in `runner.py`)

- `.run/sessions/<id>/inbox.jsonl`, append-only, one op per line:
  `{"op":"say","text":..., "from":"maxjkh@…", "ts":...}`,
  `{"op":"stop"}`, `{"op":"pause"}`, `{"op":"resume"}`.
- Read **once per turn**, right where `_with_plan` assembles the request
  (`runner.py:456`). `say` ops append a user message tagged
  `[operator message]` to `messages` (stored, unlike the plan block:
  the model should remember it) and emit a `steer` event to the
  transcript. `stop` finishes the current tool call, writes a
  `done` event with `summary: "stopped by operator"`, exits 0.
  `pause` blocks at the turn boundary until `resume` or `stop`.
- Turn-boundary semantics are the same as Claude Code's: a message sent
  during a long tool call lands when the call returns. The UI says so.
- **Eval guard — TWO independent locks (revised 2026-09-14 after a build
  finding).** The plan originally specified a single lock on
  `OPENBEAST_TASK_PATHS`. That is necessary but *not sufficient by
  construction*: `evals/run_eval.py:~580-585` derives the var from
  `/tmp/eval…` paths found in the task spec and **pops it when the spec
  has none**, so the marker is a property of the task text, not of "am I
  in an eval". Measured today: 291/291 v5 variants do contain such paths,
  so the single lock happens to cover the whole current suite — but a
  future path-less task would silently lose the guard. Locks as built:
  (1) `OPENBEAST_TASK_PATHS` set → steering off, checked as the first
  statement of `runner._steering_enabled()`; (2) steering is **opt-in**
  regardless — off unless `BEAST_CHAT=true`, `--steer`, or `--session-id`.
  Evaluated once at startup and cached; under eval mode the inbox is
  never opened, created, or stat-ed. Cache-key purity holds because the
  era hash never sees a steer unless one happened, and one cannot happen
  in eval mode.
  **Follow-up (integration pass, not yet done):** add an unconditional
  `child_env["OPENBEAST_EVAL"] = "1"` in `run_eval.py` and check it as a
  third lock. `run_eval.py` is NOT in `evals/cache.py CONTEXT_FILES`, so
  that change costs no era — but it must still land at a row boundary
  because the harness is re-invoked per row.
- **Consequence of lock 2:** the session ledger rides the same opt-in, so
  `list_agents` sees ledger records only when beast-chat is configured.
  That is the intended feature-flag behavior, not a bug.
- New transcript event `steer` joins the vocabulary and `_rebuild_messages_from_log` replays it on `--resume`.

### 3. Sessions API (`agents/chat_server.py`, `:3003`, loopback)

FastAPI, same shape as `openapi_tools.py`. JSON everywhere.

| Route | Purpose |
|---|---|
| `GET /api/chat/sessions?state=running` | ledger listing |
| `GET /api/chat/sessions/{id}` | record + derived status (iterations, tokens, last event) |
| `GET /api/chat/sessions/{id}/events?from=N` | **SSE.** Replays transcript bytes from offset N, then follows live (inotify with poll fallback). Every event carries its end offset so the client resumes with `from=` after any disconnect. Gap 2 closes here. |
| `POST /api/chat/sessions/{id}/send` | append `say` to the inbox |
| `POST /api/chat/sessions/{id}/stop` | inbox `stop`; escalate to SIGTERM group after 30 s, SIGKILL after 60 s (jobs: signal only) |
| `POST /api/chat/sessions` | start a detached agent (delegates to `mcp_server.start_agent`) or job |
| `GET /api/chat/health`, `GET /api/chat/metrics` | same conventions as the tool server |

- Tool-call results in the transcript are truncated to 2000 chars
  (`runner.py:569-574`). The API surfaces `result_truncated: true` so the
  UI can label it; raising the cap is a separate decision, not part of
  this plan.
- Contract versioned like beast-slot: `beast_chat: 1`, `min_client: 1`,
  additive changes never bump.

### 4. Console (mobile-first PWA, served by the API at `/`)

- One HTML file, no framework, dark by default, `Add to Home Screen`
  manifest. Two screens: session list (state, title, age, last line) and
  session view (streaming transcript, composer pinned to the bottom,
  Stop button, "queued for next turn" acknowledgement on send).
- Reattach is free: the page stores the last offset per session and
  reopens the SSE with `from=`.
- Renders `job` sessions as a plain log tail with the same follow/reattach.

### 5. Publish + auth (`setup-tailscale.sh --publish-chat`)

- `tailscale serve --bg --https=8445 http://127.0.0.1:3003`. Own port, own
  `--unpublish-chat`, same pre-checks (MagicDNS + HTTPS certs).
- **Read auth = tailnet identity.** `tailscale serve` injects
  `Tailscale-User-Login` on every proxied request. `CHAT_OPERATORS` in
  `openbeast.conf` is an allowlist of logins; anything else is 404, never
  403 (beast-gate convention). Nothing to type on the phone.
- **Write auth = enrolled device key.** `send`, `stop`, and `POST
  /sessions` additionally require a key from `.run/clients.json` whose
  record carries `scopes: ["chat"]` (`clients.sh enroll phone --scope
  chat`). Rationale: starting an agent is remote code execution on the
  rig. A spoofable header is enough to watch, not enough to act.
- Loopback callers skip both checks via the existing proof-of-locality
  token, identical to beast-gate.
- Every request is audited to `.run/chat-audit.jsonl` (`ts, login,
  device, route, session, outcome, ms`; message text is never logged,
  only its sha256 and length, matching the tool-audit rule).
- Rate limit: 60 writes/min per device; SSE connections are excluded from
  in-flight accounting so an idle tail cannot starve anything.
- Not behind beast-gate. The gate is inference-shaped (single upstream,
  usage scraping, `MAX_INFLIGHT=2`). Adding a per-path upstream map there
  for one consumer is more risk than a second published port. beast-slot
  made the same call.

## Phases

| Phase | Scope | Est. | Ships as |
|---|---|---|---|
| 0 | Session ledger; runner writes it; `list_agents`/`check_agent` read it; `tail_agent` gains `from_offset` + JSON; detach flag disables `atexit` kill for detached agents | half day | PR A (harness) |
| 1 | Steering inbox in `runner.py` (`say/stop/pause/resume`), `steer` event, resume replay, eval guard + tests | 1 day | PR A |
| 2 | Sessions API + SSE replay/follow + PWA console, loopback only, `BEAST_CHAT=true` in conf, `start.sh` wiring, `doctor.sh` row, `healthcheck.sh` probe | 1–2 days | PR B |
| 3 | `--publish-chat`, tailnet-login read auth, `scopes` on device records, write auth, audit, rate limit, tests in `test_clients.sh` + `test_chat_server.py` | half day | PR B |
| 4 | `scripts/job.sh` job sessions; wrap `campaign_master.sh` relaunches in it; docs: `BEAST_CHAT.md`, `FEATURES.md` bullet, `REMOTE_ACCESS_PLAN.md` superseded-in-part note, INSTALL §7 | half day | PR C |
| 5 (optional) | Push on terminal state or `error` event via self-hosted ntfy on the tailnet; PWA subscribes | half day | PR D |

Total: about four working days, two of them GPU-free and parallel to the
running campaign.

## Verification checklist

- [ ] Kill the tool server while an agent runs: agent survives, still
      listed, still tailable, still steerable.
- [ ] Phone sleeps 10 min mid-stream: reopen, transcript resumes from the
      exact offset, no duplicate or missing events.
- [ ] Send a message during a 60 s tool call: it lands at the next turn,
      appears as `steer` in the transcript, model responds to it.
- [ ] `stop` during a tool call: tool completes, `done` written, process
      gone, ledger `stopped`.
- [ ] Eval unit under `run_eval.py`: inbox directory never created, a
      pre-planted inbox is ignored, cache key unchanged.
- [ ] Unlisted tailnet login: 404 on every route. Listed login without
      device key: reads work, writes 401. Enrolled key without `chat`
      scope: writes 404.
- [ ] Revoke the phone in `clients.sh`: next write fails within one
      request (registry hot-reload).
- [ ] `campaign_master.sh` relaunched via `job.sh`: appears as a job,
      tail follows, stop signals the process group cleanly.
- [ ] `tests/run_tests.sh` and CI green; `doctor.sh` shows the chat row.
- [ ] Audit file 0600, no message bodies inside.

## Out of scope (deliberately)

- Tailscale funnel or any anonymous-internet exposure.
- Editing or branching a session's history from the phone.
- Multi-operator concurrent steering (single-operator rig; second
  operator is a `CHAT_OPERATORS` entry, no arbitration).
- Replacing Open WebUI's chat, or mirroring WebUI chats into the console.
- A native app. The PWA is the client.
- Raising the 2000-char tool-result truncation in transcripts.

## Known dependencies / caveats

- **Era discipline.** Phases 0–1 change `runner.py`. The T1.17 chain and
  the Tier-3 mini-A/B run from the main tree via `run_eval.py`
  subprocesses. Merge PR A only when no paired row is in flight: after
  stage D's Tier-3 verdict, and never between the two rows of a pair.
  Stage D itself pulls main before Tier-3, so a merge before stage D
  would put Tier-3 in a new era relative to the greedy floor that
  calibrates it. Build in `../openbeast-chat`, keep the PR open, merge at
  the boundary.
- `Tailscale-User-Login` is only present on requests proxied by
  `tailscale serve`. A local process can forge it. That is inside the
  existing loopback trust model (same as the WebUI identity headers
  before JWT mode) and is why writes need the device key.
- inotify on btrfs is fine; the poll fallback exists for the NFS case
  nobody has yet.
- The `atexit` change alters `start_agent` semantics for OpenCode users
  who relied on "close the server, agents die". Detach becomes the
  default only for beast-chat starts; MCP `start_agent` keeps today's
  behavior unless `detach=true` is passed.
- FastAPI and `sse-starlette` (or hand-rolled SSE, which is 30 lines and
  avoids a dependency; preference: hand-rolled, matching the router's
  `_synthetic` style).

## Decisions (Max, 2026-09-14)

1. **Phone may start agents in v1.** Gated by the `chat` device scope as
   designed. Phase 2's `POST /api/chat/sessions` ships, not deferred.
2. **Push notifications: not yet.** Phase 5 stays optional and unbuilt; a
   TODO is filed in `docs/TODO.md` ("beast-chat push via self-hosted ntfy,
   revisit after a week of real use").
3. **Name, conf key, port: locked.** `beast-chat`, `BEAST_CHAT=true`,
   `:8445` (`--publish-chat` / `--unpublish-chat`).
