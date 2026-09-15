# beast-chat — the rig's sessions, from your phone

**Status: SHIPPED 2026-09-14.** Designed in
[BEAST_CHAT_PLAN.md](BEAST_CHAT_PLAN.md); **this page is the shipped
behaviour and wins wherever the two differ** — several of the plan's
statements were corrected in review, and the plan was not rewritten. Off by
default (`BEAST_CHAT=false`); nothing about a rig that leaves it off changes.

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
                                        read  = identity (tailnet login,
                                                device key, or local token)
                                        write = chat-scoped device key
```

The load-bearing fact: **a session writes its own record.** Nothing registers
a session on its behalf — two writers for one id race, and the loser's
`started_by`, `command` and steering cursor are whatever happened to land
last.

Which processes write one:

- `scripts/job.sh run` — **always**. Any command you wrap is a session.
- `runner.py` — when it is started with `--steer` or `--session-id`. The
  console passes both for every agent it spawns, so anything started *from*
  beast-chat is a session by construction.
- An agent started any other way (`agent.sh`, the MCP `start_agent` tool,
  `openbeast-client agent`) behaves exactly as it did before beast-chat
  existed and is **not** a ledger session unless you ask for one with
  `--steer`. That flag is the entire opt-in; there is no config value that
  turns it on. See *Steering is disabled inside eval runs* for why.

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

# 3. rig: enroll the phone for WRITE access (reads need identity, not a key)
./scripts/clients.sh enroll phone --label "My phone" --scope chat
#   → prints the key ONCE. Paste it into the console on the phone.

# 4. phone: open https://<rig>.<tailnet>.ts.net:8445 and Add to Home Screen
```

Verify from the rig itself at any point:

```bash
./scripts/doctor.sh | grep -i chat      # health row + auth posture

# Health is the one route an unidentified caller may have, and it answers
# with exactly {"status":"ok"} — liveness, nothing else. The detail fields
# need proof you are on the box:
curl -s -H "X-OpenBeast-Local: $(cat .run/chat-local.token)" \
     localhost:3003/api/chat/health
```

## The session model: agents and jobs

Everything in the console is a **session**, and there are exactly two kinds.

| | `agent` | `job` |
|---|---|---|
| What it is | a `runner.py` process — a model in a tool loop | any long-running command |
| Registered by | `runner.py` itself, when started with `--steer`/`--session-id` | `scripts/job.sh run`, always |
| Transcript | `agents/logs/agent-<id>.jsonl` (typed events) | `.run/sessions/<id>.log` (raw output); `agents/logs/job-<id>.log` when the console started it |
| Console view | rendered turns, tool calls, reasoning | a followed log tail |
| Steerable | **yes** — messages land at the next turn | no; you can stop it |
| Stop | inbox `stop`, then SIGTERM the group | SIGTERM the process group, SIGKILL on escalation |

**What a `stop` actually reaches** (corrected in the v1.4.0 review, where the
code and two comments said "the whole tree" and meant something narrower): the
job's own process group — the supervisor and every child that has **not**
detached into a session of its own. A descendant that calls `setsid` /
`start_new_session=True` leaves that group *by design*: `evals/run_eval.py`
does exactly that so a per-task timeout can SIGKILL one agent's group without
touching the run. Such a descendant has to reap itself, and `run_eval.py` now
does (`_install_signal_reaper`) — before that fix, stopping a campaign from
the phone killed the harness and left an eval unit running, still holding a
server slot and still writing the task's fixtures, against a relaunch that
necessarily re-ran it. **If you add another self-sessioning spawner, give it
the same handler; `stop` cannot reach it for you.**

**`meta` on session creation is caller free-form with reserved keys.**
`pid_start` and `cursor` are the server's: the first is the process-identity
proof that stops a recycled pid from making a dead session look alive, the
second is the steering inbox position. They are stripped from caller input
(and `pid_start` is assigned, never inherited) because a forged `pid_start`
does not read as corruption — it reads as a session that already finished,
while its command keeps running for hours. Anything else you attach is kept.

Both live at `.run/sessions/<id>.json` (mode 0600), with `state ∈ running ·
done · failed · stopped · lost`. `lost` is the honest answer for a session
whose process is gone without a terminal event — the crash case. It is
distinct from `stopped`, which means a person did it on purpose, **including
when that person had to force-kill it**: a job that ignores SIGTERM and dies
to the SIGKILL escalation is still recorded `stopped`, never `lost`.

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
  `failed` (with the exit code as the summary), an operator stop → `stopped`.
  That holds even for a job that ignores the polite signal: when `stop`
  escalates, the supervisor is force-killed before it can write anything, so
  the CLI writes `stopped` itself after probing the process directly. (It
  deliberately does not re-read the state through the ledger accessor to
  decide: that accessor reconciles first and persists `lost`, which is what
  used to make an operator stop look like a crash.)

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
console acknowledges a send with "queued — lands at the next turn" so the
semantics are never a surprise.

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
in the resulting row would say so. **It would not look like corruption. It
would look like a result**, and it would be defended as one for as long as
the number survived. Campaigns on this rig run for days and end up in a
paper. That asymmetry is why the guard is three locks and not one.

Steering is off unless **all three** of these hold:

1. `OPENBEAST_EVAL` is **not** set. `run_eval.py` exports it for every unit it
   spawns, unconditionally — it is not derived from anything, so there is no
   task, no flag and no shell that can fail to set it. The same spawn also
   strips any inherited `OPENBEAST_BEAST_CHAT` from the child environment.
2. `OPENBEAST_TASK_PATHS` is **not** set — the harness's older marker, checked
   first and kept as the belt to lock 1's braces.
3. `--steer` or `--session-id` was passed **on the command line**. There is no
   environment opt-in: `BEAST_CHAT=true` in `openbeast.conf` publishes the
   console, it does not arm steering in any runner that happens to inherit the
   shell's environment.

Lock 3 is worded that way because the config route was the hole. `conf.sh`
exports `OPENBEAST_BEAST_CHAT` unconditionally and `run_eval.py` hands the
child a copy of its own environment, so on a configured rig the "opt-in" was
open in every eval subprocess and lock 2 was carrying the guard alone — and
lock 2 is derived from the *task text* (`run_eval.py` sets
`OPENBEAST_TASK_PATHS` from paths found in the spec and pops it when a spec
has none). A future path-less task would have silently lost the last lock. An
argv flag cannot be inherited by accident.

All three are evaluated once at startup and cached. Under eval mode the inbox
is never opened, never created, and never even stat-ed — a pre-planted inbox
file is ignored, not drained. Resume replays the transcript under the same
gate, so a `steer` event recorded on a previous run is not re-applied inside
an eval either. Cache-key purity is preserved by construction: the era hash
can only ever see a steer that happened, and one cannot happen in eval mode.

A consequence worth knowing: because the ledger rides the same gate, an agent
is a ledger session only when it was started with `--steer`/`--session-id`.
`list_agents` still lists agents this tool server spawned from its in-memory
map; what it gains from the ledger is the sessions it did *not* spawn.

## The auth model

Two tiers, because reading and acting are not the same risk. Under both of
them sits one rule: **a caller with no identity gets nothing.**

**Identity is required, everywhere except health.** Three things count as
identity: a `Tailscale-User-Login` header the tailnet proxy injected, the
locality token below, or an enrolled chat-scoped device key (curl and the
client CLI have no header to be injected into). A request carrying none of
them is refused on every route — `404`, the same answer a stranger gets.
`/api/chat/health` is the single exception, and to an unidentified caller it
answers `{"status":"ok"}` and nothing else, because `start.sh` and
`healthcheck.sh` must be able to ask whether the process is alive without a
credential. Session counts, the ledger path and the auth posture are a map of
the rig and need a read credential like everything else.

*Loopback is not a trust boundary.* Being reachable only on 127.0.0.1 keeps
out the network; it does not keep out a **web browser on this box**, and a
browser is exactly the thing that will follow a link, resolve a hostile
domain to 127.0.0.1, and issue requests to the console with the operator's
own machine as the source. `tailscale serve` also proxies *from* 127.0.0.1,
so the peer address proves nothing about who is calling. That is why a
loopback caller must still identify itself — with `.run/chat-local.token`
(0600, minted at startup, the same proof-of-locality trick beast-gate uses)
— and why the app carries a trusted-host check on top.

**Reading = tailnet identity.** `tailscale serve` injects
`Tailscale-User-Login` on every request it proxies. `CHAT_OPERATORS` in
`openbeast.conf` is a comma-separated allowlist of logins; a login that is
not on it gets **404, never 403** — the beast-gate convention, so a caller
learns nothing about what exists. There is nothing to type on the phone.

Left empty, the allowlist is **not enforced, and that is a real grant**:
every *identified* login on your tailnet can read every session — every
transcript, every command, everything an agent printed. It does not open the
door to an anonymous caller (identity is still required), but between people
on the tailnet it is no boundary at all. That is the single-operator default
and it is fine on a tailnet you own outright. `doctor` says so out loud,
every run, so it stays a decision rather than a drift.

**Writing = an enrolled device key with the `chat` scope.** Sending a
message, stopping a session, and starting an agent additionally require a
bearer key from `.run/clients.json` whose record carries `scopes: ["chat"]`.

The reason is blunt: `POST /api/chat/sessions` starts an agent, and an agent
runs `bash` on the rig. That is remote code execution. `Tailscale-User-Login`
is a header — real when tailscale injects it, forgeable by any process
already on the box. A forgeable header is enough to *watch*. It is not enough
to *act*.

**Every write failure is the same 404.** No key, an unknown key, a revoked
key, a key without the `chat` scope: one answer, and it is the answer an
unlisted login already gets. The earlier split — `401 "device key required"`
for a listed operator with no key, `404` for everything else — was a
**membership oracle**: the status code told an anonymous prober whether the
login it had guessed was in `CHAT_OPERATORS`, which is precisely the fact the
404-everywhere convention exists to hide. A console that has a key saved and
starts getting 404s has been revoked, unscoped, or was never enrolled;
`./scripts/clients.sh show <device>` on the rig is the way to tell which.

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

**Readers can be revoked the same way.** The operator allowlist is re-read on
every check, from `CHAT_OPERATORS` in the environment and from
`.run/chat-operators` (one login per line, `#` comments) — and an open
SSE stream re-authorizes on its heartbeat, so removing a login also ends the
transcript someone already had streaming. Neither needs a stack restart.

A caller that presents `.run/chat-local.token` satisfies both tiers at once:
reading it is proof of being on the box, which is strictly more than a device
key proves. Everything else needs the two tiers above.

Every request is audited to `.run/chat-audit.jsonl` — `ts, login, device,
route, session, outcome, ms` — including the ones that were refused, and with
the login the caller *claimed*, so a denial records who was probing. Message
*text* is never logged, only its sha256 and length, matching the tool-audit
rule; a spawn additionally records the command's sha256, because the command
is the one thing the scope system is gating.

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

**The stream reader is bounded.** A replay from zero — a fresh page load, the
Replay button, or the mid-stream `lost` reset — used to read the whole
transcript into memory in one blocking call inside the async generator, so a
long campaign log starved every other attached stream and `/api/chat/health`
while it did. Each read now takes at most `STREAM_MAX_READ` (256 KB, matching
the steering inbox) and runs off the event loop; the caller already re-reads
without sleeping while lines keep coming, so a backlog is *paged* rather than
slurped, and the byte offset it returns is exactly the resume point. A
producer that emits no newline at all cannot wedge the reader: past
`STREAM_MAX_LINE` the chunk is delivered as one line and the offset advances
over it.


| Event | Effect |
|---|---|
| Tool server (`:3001`) restarts | Agents started with `detach` keep running. They stay *listed* through the ledger only if they were started with `--steer`/`--session-id`; otherwise they vanish from `list_agents` with the in-memory map, exactly as before beast-chat existed. Non-detached spawns keep the historical contract: they die with the server. |
| `chat_server` restarts | Nothing is lost. The ledger is on disk; the phone reopens its stream with `from=<offset>`. |
| Phone sleeps 10 minutes | Reattach resumes at the exact byte. No duplicate events, no gap. |
| Transcript is rotated or truncated under a live stream | The stream notices mid-poll, emits a `lost` frame, and restarts at 0 rather than skipping content or handing the reader half an event. |
| Rig reboots | Sessions whose pid is gone reconcile to `lost` on the next listing — `reconcile` matches pid **and** process start time, so a recycled pid is never mistaken for a live session. |
| A session finishes | The record stays. `sessions.prune(days=30)` is the tool for clearing old terminal records, but **nothing calls it on a schedule** — run it yourself when the ledger gets long. Agent transcripts under `agents/logs/` are never touched by it; the ledger is an index, not the archive. |

`list_agents` and `check_agent` in the MCP tool server read the ledger first
and their in-memory map second, so a session that registered itself is
visible regardless of which process spawned it or how many times `:3001` has
restarted since. `tail_agent` gained a `from_offset` parameter with the same
offset-cursor contract the console uses — pass back the offset you were
handed and you never re-download bytes you already have. It never splits a
line to do it: an event bigger than the read window is read whole, and one
bigger than the 4 MB ceiling is refused with a message saying so rather than
served as two unparseable halves.

## Troubleshooting

**The console 502s from the phone.** `:8445` is published but nothing is
listening. Check `BEAST_CHAT=true` is actually in `openbeast.conf` and the
stack was restarted after: `./start.sh --status` should show a `chat` row.
`doctor` reports this as a **failure**, not a warning, precisely because the
mount makes it look reachable.

**Sessions list but sending fails.** Writes need a device key with the `chat`
scope, and every way of not having one answers **404** — no key, unknown key,
revoked key, no `chat` scope. The uniformity is deliberate: a status code that
distinguished those cases would also tell a prober which logins are in
`CHAT_OPERATORS`. Diagnose it on the rig, not from the phone:
`./scripts/clients.sh show phone`; if `scopes` reads `-`, run
`./scripts/clients.sh scope phone add chat`; if the device is gone entirely,
enroll it again.

**404 on every route, including the console page.** Either your tailnet login
is not in `CHAT_OPERATORS`, or the request reached the server with no
identity at all (no `Tailscale-User-Login` header and no
`.run/chat-local.token`) — a direct `curl localhost:3003/` does exactly that.
The 404 is deliberate in both cases; 403 would confirm the service exists.
Compare `tailscale status --json | grep -i login` against the conf value, and
from the box itself pass `-H "X-OpenBeast-Local: $(cat .run/chat-local.token)"`.

**A message never arrives.** Check the session is an `agent` and not a
`job` — jobs have no inbox. Then confirm the agent is not an eval unit: under
`run_eval.py` the inbox is never read, by design. Then check the agent was
started with `--steer`/`--session-id` at all: one that was not has no inbox
and no ledger record, so there is nothing to deliver to.

**A session shows `lost`.** `lost` means one thing: the record still said
`running`, and the process that owned it is gone without ever writing a
terminal event. It is a *reconciled* state, computed on read by matching the
recorded pid **and** its start time against the live process — never a state
a session writes about itself.

Expect it after a rig reboot, an OOM kill, a `kill -9` of the session's
process group from outside beast-chat, or a power cut — and after nothing
else. In particular it is **not** what an operator stop looks like: a job
stopped with `job.sh stop` records `stopped` even when it ignored SIGTERM and
had to be force-killed, and an agent stopped from the console records
`stopped` after its `done` event. A `lost` job whose log ends mid-command is
a crash to investigate: the log is still on disk at `.run/sessions/<id>.log`
and its last lines are the diagnosis (`dmesg | grep -i oom` for the usual
suspect). A `lost` job whose log ends cleanly means the supervisor died
between the work finishing and the record being written — rare, and the log
is authoritative over the state.

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
