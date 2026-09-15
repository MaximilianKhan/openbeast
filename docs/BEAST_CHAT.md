# beast-chat — the rig's sessions, from your phone

**Status: SHIPPED 2026-09-14.** Built to the design in
[BEAST_CHAT_PLAN.md](BEAST_CHAT_PLAN.md). Off by default
(`BEAST_CHAT=false`); nothing about a rig that leaves it off changes.

## The concept

OpenBeast already runs work that takes hours: autonomous agents chewing
through a refactor, eval sweeps, quantization runs, campaign orchestrators
that step through a dozen stages overnight. Until now, watching any of it
meant sitting at the rig with a terminal, and *intervening* meant killing the
process and starting over with `--resume`.

beast-chat is the operator console for that work. From a phone on your
tailnet: list every agent and job running on the rig, watch its transcript
stream live, send it a message that lands at its next turn, stop it, start a
new one. Put the phone to sleep mid-stream, reopen it an hour later, and the
transcript picks up at the exact byte it left off.

```
RIG                                                   PHONE (tailnet)
runner.py agents ──┐
scripts/job.sh   ──┼─▶ .run/sessions/   ◀── ledger ─── chat_server :3003
campaigns        ──┘   <id>.json (record)                    ▲
                       <id>/inbox.jsonl (steering)           │
                                                   tailscale :8445
                                                             │
                                             read  = tailnet login
                                             write = chat-scoped device key
```

The load-bearing fact: **a session writes its own record.** `runner.py`
registers itself; `job.sh` registers the command it wraps. Nothing registers
on a session's behalf, which is why every spawn path — the MCP `start_agent`
tool, `agent.sh`, `openbeast-client agent`, a bash campaign — shows up in the
console without any of them knowing beast-chat exists.

**Is not:** a replacement for Open WebUI (your chat history already lives
there, published at `:443`), a public-internet service (tailnet only — this
stack never funnels), or a way to reach Claude Code sessions on the rig.

## Quickstart

```bash
# 1. rig: turn it on
echo 'BEAST_CHAT=true'                >> openbeast.conf
echo 'CHAT_OPERATORS=you@example.com' >> openbeast.conf   # your tailnet login
./stop.sh && ./start.sh -d

# 2. rig: publish it to the tailnet
./scripts/setup-tailscale.sh --publish-chat

# 3. rig: enroll the phone for WRITE access (reading needs no key)
./scripts/clients.sh enroll phone --label "My phone" --scope chat
#   → prints the key ONCE. Paste it into the console on the phone.

# 4. phone: open https://<rig>.<tailnet>.ts.net:8445 and Add to Home Screen
```

Verify from the rig itself at any point:

```bash
./scripts/doctor.sh | grep -i chat      # health row + auth posture
curl -s localhost:3003/api/chat/health  # {"status":"ok", ...}
```

## The session model: agents and jobs

Everything in the console is a **session**, and there are exactly two kinds.

| | `agent` | `job` |
|---|---|---|
| What it is | a `runner.py` process — a model in a tool loop | any long-running command |
| Registered by | `runner.py`, at startup | `scripts/job.sh run` |
| Transcript | `agents/logs/agent-<id>.jsonl` (typed events) | `.run/sessions/<id>.log` (raw output) |
| Console view | rendered turns, tool calls, reasoning | a followed log tail |
| Steerable | **yes** — messages land at the next turn | no; you can stop it |
| Stop | inbox `stop`, then SIGTERM the group | SIGTERM the process group |

Both live at `.run/sessions/<id>.json` (mode 0600), with `state ∈ running ·
done · failed · stopped · lost`. `lost` is the honest answer for a session
whose process is gone without a terminal event — the crash case. It is
distinct from `stopped`, which means a person did it on purpose.

### Registering a campaign as a job

The sessions worth watching from a phone are often not agents at all. They
are plain bash: `campaign_master.sh`, an overnight sweep, a quantization run.
Wrap one and it becomes a session:

```bash
./scripts/job.sh run --title "T1.17 campaign" -- bash scratch/campaign_master.sh
```

```
Job started.
  ID:       20260914-210307-d02bc946
  Title:    T1.17 campaign
  PID:      208524 (process group 208524)
  Workdir:  /home/max/Documents/openbeast
  Log:      /home/max/Documents/openbeast/.run/sessions/20260914-210307-d02bc946.log
```

What the wrapper guarantees, and why each one matters:

- **It outlives your shell.** Launched `nohup`-style with stdin from
  `/dev/null`, so closing the terminal (or losing an SSH session) does not
  take the campaign with it.
- **It gets its own process group**, with both `pid` and `pgid` in the
  record. A campaign is a shell with children and grandchildren; `job.sh
  stop` signals the *group*, so nothing is left orphaned and still burning
  GPU. It also means a stop can never reach back and kill the shell that
  started it.
- **stdout and stderr are merged** into one 0600 log — a job's output is
  whatever the job printed, which on this rig can include a token in an
  environment dump.
- **The terminal state is the truth.** Exit 0 → `done`, anything else →
  `failed` (with the exit code as the summary), SIGTERM → `stopped`.

```bash
./scripts/job.sh list                 # every job, newest first
./scripts/job.sh list --state running
./scripts/job.sh show <id>            # record + the last 20 log lines
./scripts/job.sh stop <id>            # SIGTERM the group, SIGKILL after 30s
```

`list` and `show` are read-only and deliberately do not source
`lib/conf.sh` — sourcing it generates and appends a SearXNG secret, and
inspecting a job must never mutate the rig's config.

## How steering works, and why messages land at the turn boundary

The inbox is `.run/sessions/<id>/inbox.jsonl`, one append-only op per line.
The console's composer writes a `say`; the Stop button writes a `stop`.

`runner.py` reads the inbox **once per turn**, at the point where it
assembles the next request. A `say` becomes a user message tagged `[operator
message]` and is *stored* in the conversation — unlike the plan block, which
is re-injected fresh every turn and never remembered. You want the agent to
remember what you told it.

**So a message you send during a 90-second `bash` call lands when that call
returns, not while it runs.** This is the same contract Claude Code's mobile
app has, and it is a deliberate choice rather than a limitation: the
alternative is mutating the message list underneath an in-flight request,
which corrupts the conversation and the token accounting with it. The
console acknowledges a send with "queued for next turn" so the semantics are
never a surprise.

A `stop` op is handled at the same boundary: the current tool call finishes,
a `done` event is written with `summary: "stopped by operator"`, and the
process exits 0. Cleanly — the transcript stays replayable.

### Steering is disabled inside eval runs. Hard.

An eval unit is an agent session too: `evals/run_eval.py` spawns `runner.py`
as a subprocess for every task. That makes the eval harness the one place
where this feature would be actively destructive.

An OpenBeast leaderboard row is a **paired measurement**: arm A and arm B run
the same tasks against the same cache era, and the difference between them is
the claim. A single operator message injected into one arm's agent changes
that arm's prompt, its token counts, and possibly its outcome — and nothing
in the resulting row would say so. It would not look like corruption. It
would look like a result. Campaigns on this rig run for days and produce
numbers that go into a paper.

So steering is locked out by **two independent conditions**, either of which
alone disables it:

1. `OPENBEAST_TASK_PATHS` is set in the environment — the eval harness's
   marker.
2. Steering is **opt-in in the first place**: off unless `BEAST_CHAT=true`,
   `--steer`, or `--session-id` is passed.

Both are evaluated once at startup and cached. Under eval mode the inbox is
never opened, never created, and never even stat-ed — a pre-planted inbox
file is ignored, not drained. Cache-key purity is therefore preserved by
construction: the era hash can only ever see a steer that happened, and one
cannot happen in eval mode.

Why two locks and not one? Because lock 1 is not sufficient *by
construction*. `run_eval.py` derives `OPENBEAST_TASK_PATHS` from paths found
in the task spec and pops it when a spec has none — it is a property of the
task text, not of "am I in an eval". Today all 291 v5 variants carry such
paths, so lock 1 happens to cover the whole suite; a future path-less task
would silently lose it. Lock 2 does not depend on task content at all.

A consequence worth knowing: because the ledger rides the same opt-in,
`list_agents` sees ledger records only on a rig where beast-chat is
configured. That is the feature flag working, not a bug.

## The auth model

Two tiers, because reading and acting are not the same risk.

**Reading = tailnet identity.** `tailscale serve` injects
`Tailscale-User-Login` on every request it proxies. `CHAT_OPERATORS` in
`openbeast.conf` is a comma-separated allowlist of logins; anything not on it
gets **404, never 403** — the beast-gate convention, so a caller learns
nothing about what exists. There is nothing to type on the phone.

Left empty, the allowlist is **not enforced**: every login on your tailnet
can read every session. That is the single-operator default and it is fine on
a tailnet you own outright. `doctor` says so out loud, every run, so it stays
a decision rather than a drift.

**Writing = an enrolled device key with the `chat` scope.** Sending a
message, stopping a session, and starting an agent additionally require a
bearer key from `.run/clients.json` whose record carries `scopes: ["chat"]`.

The reason is blunt: `POST /api/chat/sessions` starts an agent, and an agent
runs `bash` on the rig. That is remote code execution. `Tailscale-User-Login`
is a header — real when tailscale injects it, forgeable by any process
already on the box. A forgeable header is enough to *watch*. It is not enough
to *act*.

```bash
./scripts/clients.sh enroll phone --label "My phone" --scope chat
./scripts/clients.sh list                      # SCOPES column
./scripts/clients.sh scope phone add chat      # grant later
./scripts/clients.sh scope phone remove chat   # take it back, no restart
./scripts/clients.sh revoke phone              # cut the device off entirely
```

Scopes are additive to the version-1 registry: a device enrolled before
scopes existed has no `scopes` field, reads as having none, and keeps working
for inference exactly as before. **Absence is never a grant.** Inference
itself needs no scope — an enrolled, un-revoked key is the whole check, which
is what every existing device already relies on.

A lost phone is one `revoke` away from silence, and the registry hot-reloads:
the next write fails within one request, no restart.

Loopback callers skip both checks through the existing proof-of-locality
token, identical to beast-gate. Every request is audited to
`.run/chat-audit.jsonl` — `ts, login, device, route, session, outcome, ms`.
Message *text* is never logged, only its sha256 and length, matching the
tool-audit rule.

## Publishing (`--publish-chat`)

```bash
./scripts/setup-tailscale.sh --publish-chat     # :8445 → 127.0.0.1:CHAT_PORT
./scripts/setup-tailscale.sh --unpublish-chat   # take it back down
```

Same pre-checks as every other surface (MagicDNS + HTTPS Certificates must be
on for the tailnet), and the script now prints the full mount table so you
can see the whole published footprint at a glance:

```
      Tailnet serve mounts:
        PORT    SURFACE                             STATE
        443     Open WebUI (:3000)                  published
        8443    inference (llama-server / beast-gate)  published
        8444    beast-slot status API (:3002)       -
        8445    beast-chat console (:3003)          published
        8889    SearXNG for thin clients (:8888)    -
```

Unlike `--publish-slot`, this mounts `/` rather than one path: the console is
a page plus its own API, and every route under it enforces the two-tier auth
in-process. There is nothing to narrow with `--set-path`.

beast-chat is deliberately **not** behind beast-gate. The gate is
inference-shaped — one hardcoded upstream, usage scraping, `MAX_INFLIGHT=2` —
and teaching it a per-path upstream map for a single consumer is more risk
than a second published port. beast-slot made the same call.

`--unpublish-chat` reads no config at all: the mount is keyed by the
published port (8445), not by `CHAT_PORT`. Taking a surface down has to work
on a rig whose `openbeast.conf` is broken, because that is exactly when you
want to.

## Enrolling a phone, end to end

1. Install Tailscale on the phone and sign in to the same tailnet.
2. On the rig: `./scripts/clients.sh enroll phone --label "My phone" --scope chat`
3. Copy the key **now** — only its sha256 is stored and nothing on the rig
   can print it again.
4. Open `https://<rig>.<tailnet>.ts.net:8445` on the phone. Sessions list
   immediately (that is the tailnet login doing its job).
5. Paste the key into the console's settings. The composer and Stop button
   activate.
6. Share → **Add to Home Screen**. It installs as an app.

**Quit NordVPN or any full-tunnel VPN first.** Its kill switch severs the
tailnet mid-stream while the rig stays perfectly healthy — the single most
confusing failure mode on this stack (README § Remote access).

## Durability: what survives what

| Event | Effect |
|---|---|
| Tool server (`:3001`) restarts | Agents started with `detach` **survive** and stay listed, tailable, steerable. Non-detached spawns keep the historical contract: they die with the server. |
| `chat_server` restarts | Nothing is lost. The ledger is on disk; the phone reopens its stream with `from=<offset>`. |
| Phone sleeps 10 minutes | Reattach resumes at the exact byte. No duplicate events, no gap. |
| Rig reboots | Sessions whose pid is gone reconcile to `lost` on the next listing — `reconcile` matches pid **and** process start time, so a recycled pid is never mistaken for a live session. |
| A session finishes | Record keeps for 30 days, then prunes. Transcripts stay where they are. |

`list_agents` and `check_agent` in the MCP tool server read the ledger first
and their in-memory map second, so an agent is visible regardless of which
process spawned it or how many times `:3001` has restarted since.
`tail_agent` gained a `from_offset` parameter with the same offset-cursor
contract the console uses — pass back the offset you were handed and you
never re-download bytes you already have.

## Troubleshooting

**The console 502s from the phone.** `:8445` is published but nothing is
listening. Check `BEAST_CHAT=true` is actually in `openbeast.conf` and the
stack was restarted after: `./start.sh --status` should show a `chat` row.
`doctor` reports this as a **failure**, not a warning, precisely because the
mount makes it look reachable.

**Sessions list but the composer does nothing.** That is the two-tier auth
working as designed: your login reads, but the device has no `chat` scope.
`./scripts/clients.sh show phone` — if `scopes` reads `-`, run
`./scripts/clients.sh scope phone add chat`.

**404 on every route.** Your tailnet login is not in `CHAT_OPERATORS`. The
404 is deliberate (403 would confirm the service exists). Compare
`tailscale status --json | grep -i login` against the conf value.

**A message never arrives.** Check the session is an `agent` and not a
`job` — jobs have no inbox. Then confirm the agent is not an eval unit: under
`run_eval.py` the inbox is never read, by design, and the console says so on
the session view.

**A job shows `lost`.** Its process vanished without writing a terminal
state — usually an OOM kill or a rig reboot. The log is still on disk at
`.run/sessions/<id>.log`; its last lines are the diagnosis.

**`job.sh stop` says "already stopped" but the work is still running.**
Something escaped the process group — a `systemd-run` scope or a
`setsid`-wrapped child. Those detach on purpose and no group signal reaches
them. Find them with `pgrep -af <script-name>`.

**Everything was fine, then the whole tailnet went dark.** NordVPN. See
above.

## Out of scope (deliberately)

- Tailscale funnel, or any anonymous-internet exposure.
- Editing or branching a session's history from the phone.
- Multi-operator concurrent steering. This is a single-operator rig; a second
  operator is another `CHAT_OPERATORS` entry, with no arbitration between
  them.
- Push notifications. Deferred, not rejected — revisit after a week of real
  use (`docs/TODO.md`).
- Raising the 2000-character tool-result truncation in transcripts. The API
  surfaces `result_truncated: true` so the console can label it; changing the
  cap is its own decision.
