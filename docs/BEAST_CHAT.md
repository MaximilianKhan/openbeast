# beast-chat — the rig's sessions, from your phone

**Status: shipped in v1.4.0 (2026-09-14); this page describes `main` as of
2026-09-30.** Since v1.6.0 the console starts agents and jobs from a
new-session sheet (with operator presets), pauses and resumes agents, installs
as a PWA, pushes a notification when a session ends (opt-in, through the ntfy
extension), exports a transcript as a beast-artifact page, shows a rig status
strip, and turns links in a transcript into links. Designed in
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
  console passes both for every agent it spawns, and the MCP `start_agent`
  tool always passes `--session-id` (which *implies* `--steer`), so anything
  started *from* beast-chat **or by the local model through the tool server**
  is a session by construction — and therefore steerable and stoppable by any
  chat-scoped device key.
- An agent started any other way (`agent.sh`, `openbeast-client agent`)
  behaves exactly as it did before beast-chat existed and is **not** a ledger
  session unless you ask for one with `--steer` (`./agent.sh --steer "…"`).
  That flag is the entire opt-in; there is no config value that turns it on.
  See *Steering is disabled inside eval runs* for why.

Every session the console starts, and every `job.sh run` job, gets
`OPENBEAST_SESSION_ID=<its id>` in its environment. Anything it publishes to
beast-artifact is stamped with that id, and the page links back here
([BEAST_ARTIFACT.md § Where a page came from](BEAST_ARTIFACT.md#where-a-page-came-from-session-links)).

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

# 4. phone: open https://<rig>.<tailnet>.ts.net:8445, paste the key in the
#    🔑 sheet, then Add to Home Screen (it installs as an app)
```

Push notifications when a session ends are a separate opt-in; see
*Push notifications* below.

Verify from the rig itself at any point:

```bash
./scripts/doctor.sh | grep -i chat      # health row + auth posture

# Health is the one API route an unidentified caller may have (the console
# page and its /icon.svg are ungated markup and always answer 200 — they ship
# no session data, so a 200 there proves nothing about your login), and it answers
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
the same handler; `stop` cannot reach it for you after its parent is gone.**

What the console's `stop` does add (review 2026-09-29): immediately before it
signals the session, it reads the process tree and signals every group a
*descendant* leads, too — which is how an agent's bash-tool command (run
under `start_new_session=True` by `agents/tools.py`) is stopped with it
instead of running on as an orphan with no timeout left. Only groups a
descendant leads are signalled, each after re-checking its start time, and
only on this signal pass: a descendant that ignores SIGTERM is not chased
once its parent has died (the SIGKILL escalation can no longer find it).

The runner itself now does the same for every stop path, `job.sh stop` and
MCP `stop_agent` included: `agents/runner.py` handles SIGTERM/SIGHUP/SIGINT
by SIGTERMing each in-flight tool command's group (`tools._LIVE_GROUPS`),
SIGKILLing whatever is left after 2 s, and then dying of the original signal
— so a SIGTERM-ignoring tool command is killed too, as long as the runner
gets the SIGTERM before any SIGKILL.

**`meta` on session creation is caller free-form with reserved keys.**
`pid_start`, `boot_id` and `cursor` are the server's
(`sessions.SERVER_OWNED_META`): the first two are the process-identity proof
that stops a recycled pid from making a dead session look alive, the third is
the steering inbox position. They are stripped from caller input
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
./scripts/job.sh run --title "T1.17 campaign" -- bash scratch/campaign_master3.sh
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
`/api/chat/health` is the only API route excepted — `/` and `/icon.svg` are
ungated markup, carry no session data, and always answer 200 — and to an
unidentified caller health
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
message, stopping, pausing or resuming a session, starting an agent or a job
(its dry run included), exporting a transcript and sending a test alert
additionally require a bearer key from `.run/clients.json` whose record
carries `scopes: ["chat"]`. The model list, the presets list and the rig
strip are reads.

The reason is blunt: `POST /api/chat/sessions` starts an agent, and an agent
runs `bash` on the rig. That is remote code execution. `Tailscale-User-Login`
is a header — real when tailscale injects it, forgeable by any process
already on the box. A forgeable header is enough to *watch*. It is not enough
to *act*.

"Any process on the box" includes the **host-network containers**: Open
WebUI and SearXNG run `network_mode: host`, so a compromise of either reaches
`127.0.0.1:3003` as loopback and can claim any login — without being able to
read the 0600 token files. To close that, have `chat_server` also listen on a
Unix socket and honour the login header **only** there:
`OPENBEAST_CHAT_SOCKET=/path/to/.run/chat.sock` (created 0600 in a 0700
directory; `tailscaled` runs as root, so it can still connect) plus
`OPENBEAST_CHAT_LOGIN_FROM=unix`, and point `tailscale serve` at
`unix:/path/to/.run/chat.sock`. TCP loopback then needs the locality token or
a device key like any other caller. The default stays `loopback`.

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

**Readers can be revoked the same way — through `.run/chat-operators`.** The
allowlist is the union of `CHAT_OPERATORS` and `.run/chat-operators` (one
login per line, `#` comments). The FILE is re-read on every check, and an open
SSE stream re-authorizes on its heartbeat, so removing a login there ends even
a transcript someone already had streaming, with no restart. `CHAT_OPERATORS`
is different: `conf.sh` exports it into `chat_server`'s environment at start,
so editing it in `openbeast.conf` changes nothing until `./stop.sh &&
./start.sh` — and a login still listed there stays authorized whatever the
file says. For revocation you can do without a restart, keep the list in the
file.

A caller that presents `.run/chat-local.token` satisfies both tiers at once:
reading it is proof of being on the box, which is strictly more than a device
key proves. Everything else needs the two tiers above.

Every request is audited to `.run/chat-audit.jsonl` — `ts, login, device,
verified, peer, route, session, outcome, ms` — including the ones that were
refused, and with the login the caller *claimed* (clipped to 256 characters),
so a denial records who was probing; `verified: false` plus the socket `peer`
is what tells a forged login from your own phone. Unverified denials are
sampled per peer (`OPENBEAST_CHAT_AUDIT_DENIALS_PER_MIN`, default 60; the rest
become one `denials_suppressed` row with a count) and the file rotates to
`.1` past `OPENBEAST_CHAT_AUDIT_MAX_MB` (default 50), so an unauthenticated
loop cannot fill the disk the ledger lives on. A stop's actual SIGTERM/SIGKILL
deliveries are rows too (`route: "stop escalation"`), and so is every stream
close, with the bytes it served. Message
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
5. Tap the key icon (🔑) in the console's header and paste the key under
   *Device key*. Sending, Stop, Pause and starting sessions work from then
   on.
6. Share → **Add to Home Screen**. It installs as an app (see *Installable,
   and honest offline*).

**Quit NordVPN or any full-tunnel VPN first.** Its kill switch severs the
tailnet mid-stream while the rig stays perfectly healthy — the single most
confusing failure mode on this stack (README § Remote access).

## Console features

### Starting a session: the new-session sheet

The **+** button opens a sheet with two tabs.

- **Agent**: a task, an optional model (the list comes from beast-slot,
  `GET /api/chat/models`), max iterations and a working directory. The agent
  is started with `--session-id … --steer`, so it is steerable, and the task
  goes after `--`, so a task that starts with `-` is never read as a runner
  flag. It calls the rig's configured inference endpoint
  (`OPENBEAST_AGENT_INFERENCE_URL`, which `conf.sh` derives from
  `INFERENCE_URL` or an explicit `AGENT_INFERENCE_URL`), the same one MCP
  `start_agent` uses; with neither set, the runner's own default.
- **Job**: one of the operator's **presets**, or *Custom command* for a shell
  command typed on the phone. It runs under `scripts/job.sh`'s supervisor.

**Review…** asks the server for a dry run (`POST /api/chat/sessions` with
`"dry_run": true`, same write gate, nothing spawned), and the confirm dialog
shows the argv it returned. **Start** runs that. Only the per-start values
differ: a fresh session id and the transcript path named after it. The dry
run also returns `plan_sha256`; the console sends it back as
`confirm_sha256`, and the server answers 409 ("review again") if what it
would run has changed since. A preset edited on disk between Review and
Start is refused, not run. A client that sends no `confirm_sha256` behaves
as before. Starting needs the device key like every other write.

**Spawned sessions do not inherit the stack's secrets.** Jobs started from
the console or the API do **not** get `OPENAI_API_KEY`, `HF_TOKEN`,
`GITHUB_TOKEN`/`GH_TOKEN`, or any `OPENBEAST_`/`LLAMA_`/`WEBUI_`/`SEARXNG_`
variable naming a key, token, secret or password — the same list the bash
tool scrubs — nor the notification URL. Agents keep only their inference key
(`OPENBEAST_API_KEY`, `OPENAI_API_KEY`). The same command started with
`job.sh run` from a terminal keeps your shell's environment. A job that needs
a credential should read it from a file (or set it in the preset's command).

### Presets: `.run/chat-presets.json`

Presets are one-tap commands, written by you on the rig. Because every entry
is a command a phone can start, the file is ignored (with the reason shown
in the sheet) unless it is a regular file (not a symlink), owned by the
stack's user, mode **0600**, and at most 256 KB:

```json
{"presets": [
  {"name": "doctor", "title": "openbeast doctor", "cmd": "./scripts/doctor.sh",
   "workdir": "~/Documents/openbeast", "description": "health check"},
  {"name": "scores", "cmd": "python3 evals/scoring.py --show"}
]}
```

```bash
chmod 600 .run/chat-presets.json
```

| Field | Rule |
|---|---|
| `name` | required; `[A-Za-z0-9._-]`, 1–64 characters; a repeated name is skipped (the first wins) |
| `cmd` | required; a shell command, run with `bash -lc`; at most 8,192 characters |
| `title` | optional; the session's title (200 characters) |
| `workdir` | optional; where it runs (1,024 characters) |
| `description` | optional; shown in the sheet (500 characters) |

An entry that breaks a rule is dropped; the rest load. The phone sends the
preset's *name* (`"preset": "<name>"`, which cannot be combined with `cmd`);
the command is resolved on the rig, at Start as well as at Review.

### Pause and resume

**Pause / Resume** (agents only) write the `pause` / `resume` inbox ops the
runner already honours (`POST …/pause`, `…/resume`). A pause lands at the
next turn boundary, like a message. Jobs and finished sessions answer 409: a
shell command has no turn to pause at.

### Export to an artifact

**Export** (`POST /api/chat/sessions/<id>/export`) publishes the transcript
as a beast-artifact page: turns, tool calls and results (already clipped to
2,000 characters), every byte HTML-escaped and run through the bash tool's
secret list — secret-named env values; `NAME=` / `NAME:` assignments whose
name is secret-shaped, quoted JSON keys and hyphenated headers included
(`X-OpenBeast-Device-Key`, `X-OpenBeast-Local`); `--api-key` / `--token` /
`--password` flags; `Authorization:` credentials of any scheme; a URL's
`user:password@`, `curl -u`, PEM private-key blocks and well-known token
prefixes (`ghp_`, `github_pat_`, `hf_`, `sk-`, `xox?-`, `glpat-`, `AKIA`); and,
by value, the rig's own unnamed secrets — the notify topic URL and token and
the `.run/` locality tokens and raw-origin key. Every pattern is linear, so a
huge log line cannot stall the server while it exports. The page's
title and description are scrubbed too. Redaction is pattern-based and
errs toward over-redacting (`prompt_tokens: 512` shows as `[redacted]`); read
the page before you widen its visibility.

The page is **private**, at a stable id (`uuid5` of the session id), so a
re-export is the next version at the same URL. It carries the session id, so
the viewer links back to this console. It is **owned by the tailnet login
that pressed Export** (forwarded to beast-artifact with the locality token),
so the link opens on that phone. If beast-artifact has an operator allowlist,
that login must be on it. Otherwise, and for an export made on the rig with
the local token, the page belongs to the rig (`rig`) and opens for the rig's
admins. Needs `BEAST_ARTIFACT=true` and a running artifact server; otherwise
409 with the reason.

### Push notifications

Opt-in, off by default. When a session goes from `running` to one of the
states you choose, `chat_server` POSTs a short message to an ntfy-compatible
topic URL. The self-hosted way is the **ntfy extension**
([extensions/ntfy/README.md](../extensions/ntfy/README.md)):

```bash
./scripts/ext.sh enable ntfy
# openbeast.conf
#   CHAT_NOTIFY_URL=http://127.0.0.1:3005/openbeast-<long-random-topic>
#   CHAT_NOTIFY_ON=failed,lost,done              # the default
#   CHAT_NOTIFY_TOKEN_FILE=~/.config/openbeast/ntfy.token   # optional, 0600
./stop.sh && ./start.sh -d
./scripts/setup-tailscale.sh --publish-ntfy       # :8447, for the phone app
./scripts/doctor.sh                               # "notifications" rows
```

Then subscribe to the same topic in the ntfy app, on the server
`https://<rig>.<tailnet>.ts.net:8447`.

| Key | Default | What it does |
|---|---|---|
| `CHAT_NOTIFY_URL` | empty (off) | The topic URL. Anything that is not `http(s)://` turns notifications off with one stderr line |
| `CHAT_NOTIFY_ON` | `failed,lost,done` | Which terminal states notify (`done`, `failed`, `stopped`, `lost`) |
| `CHAT_NOTIFY_TOKEN_FILE` | empty | A file holding a bearer token (ntfy's `tk_…`). Read at send time; never argv, env or a log |
| `CHAT_PUBLIC_URL` | detected | The console URL a notification's link opens. Unset, it is the `:8445` name `tailscale serve` publishes, else `http://localhost:<CHAT_PORT>` |
| `OPENBEAST_CHAT_NOTIFY_PERIOD_S` | `5` | How often the ledger is diffed (env only) |

**The payload rule: title, state and a link, never transcript text.** A
notification crosses a relay and sits on a lock screen, and with ntfy's
default access the topic is readable by anyone who knows it. So the title is
run through the export's secret scrubber, and a job whose title is just
(part of) its own command — the default for console, API and `job.sh run`
jobs — sends `job <last 8 of its id>` instead. An agent's title (its task) is
sent scrubbed. `failed` and `lost` go out at high priority.

**The topic URL is treated as a secret.** With ntfy's default read-write
access the topic name is the credential, so `conf.sh` does not export
`CHAT_NOTIFY_URL`: only the chat server's own process receives it
(`ob_exec_chat_server`), and never on an argv. It is not in `./start.sh -d`'s
unit environment, not in any spawned agent's or job's environment, and not
in any model-run command's. One consequence: a `python3
agents/chat_server.py` started by hand gets no URL and sends nothing.

Delivery is bounded. A session notifies at most once a minute, and one diff
sends at most 10; past that it sends one "N more sessions ended" summary.
Every ~5 s the ledger is compared with `.run/notify-state.json` (0600), so a
job that ended while the server was down still notifies on the next start. A
failing endpoint costs one stderr line per five minutes, and an alert it
could not take is retried on later passes (at most once a minute) until it is
delivered or 24 hours have passed since the session ended.
**Test alert** in the 🔑 sheet sends one on demand (`POST
/api/chat/notify/test`, write gate; 409 when notifications are off).

**iOS.** Android and desktop ntfy clients hold a connection to your server,
so nothing leaves the tailnet. iOS cannot: instant delivery needs
`NTFY_BASE_URL` and `NTFY_UPSTREAM_BASE_URL=https://ntfy.sh`, which makes
your server send ntfy.sh a poll request (message id and a hash of the topic
URL, not the content) for every notification. The content stays on the
tailnet; the fact and timing of each notification do not. Both are empty by
default, and without them iOS shows messages only when the app is opened.
Details: [extensions/ntfy/README.md](../extensions/ntfy/README.md).

### Rig status strip

The list header shows the GPU lease holder (the same pid + start-time check
as `gpu-lease.sh status`), whether the inference server answers `/health`
(the configured `INFERENCE_URL`, cached 5 s), and how many sessions are
running (`GET /api/chat/rig`, read tier).

### Links in a transcript

`https://` URLs and artifact pages (`…/a/<uuid>`) in transcript text become
links (`rel="noopener"`, built as text nodes plus anchors). Nothing in a
transcript is ever parsed as HTML, and nothing else becomes a link.

### Installable, and honest offline

`/manifest.webmanifest`, PNG icons at 180/192/512 px (iOS ignores an SVG
touch icon) and a service worker (`/sw.js`) that caches only the page shell
— never `/api/*`, never a stream. **Add to Home Screen** installs it as an
app. With the rig unreachable, an open console keeps the last list on screen
under an "offline — last updated …" banner; a cold start while offline has
no list to show, because session data is never cached.

### Big transcripts, and the composer

A session larger than 128 KB opens at its last 128 KB (`/events?tail=`),
with a note; **Replay** loads everything. Enter sends a message, Shift+Enter
is a new line. A message is capped at 4,000 characters, the most an agent
receives whole; the server trims surrounding whitespace first and stores
exactly the text it checked.

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
| `chat_server` restarts | Nothing is lost. The ledger is on disk; the phone reopens its stream with `from=<offset>`. A console-started job runs under `job.sh`'s supervisor, which records its own `done`/`failed`/`stopped` and does its own TERM→KILL on a stop, so neither depends on the server being alive; an agent writes its own verdict. |
| Phone sleeps 10 minutes | Reattach resumes at the exact byte. No duplicate events, no gap. |
| Transcript is rotated or truncated under a live stream | The stream notices mid-poll, emits a `lost` frame, and restarts at 0 rather than skipping content or handing the reader half an event. |
| Rig reboots | Every session from a previous boot reconciles to `lost` on the next listing. `reconcile` matches pid **and** process start time **and** the boot id (`/proc/sys/kernel/random/boot_id`, stamped at register). The boot id is what makes this row true rather than approximately true: the start time is *ticks since boot*, so across a reboot the other two compare against a different clock and can agree by coincidence — which is also why the signalling path checks it before any `killpg`. A record written before this existed, or on a kernel that will not report a boot id, falls back to the pid+start proof rather than being declared dead. |
| A session finishes | The record stays for 30 days. `chat_server` sweeps terminal records older than that once per start, and the daily logrotate run does the same even with `BEAST_CHAT=false` (`sessions.prune(30, keep_logs=True)`): the index entry, its inbox and its lock go; a `job.sh` job's `.run/sessions/<id>.log` — its only output — is **kept**, and agent transcripts under `agents/logs/` are not touched by this sweep. Calling `sessions.prune()` yourself (no `keep_logs`) removes the logs too. With the opt-in `AGENT_LOG_RETENTION_DAYS`, the daily logrotate run also deletes old transcripts no record names — in `agents/logs/` and, since 2026-09-29, the `.run/sessions/<id>.log` files this sweep left behind. |

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

**404 on every API route.** Either your tailnet login is not in
`CHAT_OPERATORS`, or the request reached the server with no identity at all
(no `Tailscale-User-Login` header and no `.run/chat-local.token`) — a direct
`curl localhost:3003/api/chat/sessions` does exactly that. Note the console
page itself is NOT part of this symptom: `/`, `/icon.svg`, the PNG icons,
`/manifest.webmanifest` and `/sw.js` are ungated markup and answer 200 to anyone who can reach the port, so loading the page
tells you nothing about your identity — the session list inside it is what
404s.
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
process group from outside beast-chat, or a power cut. One more case is a
known gap: an **agent** stopped by MCP `stop_agent` or a plain `kill <pid>`
dies on the signal before it can write `stopped`, so it reads `lost` (fixing
that is a `runner.py` change, held for an eval-era boundary). A job started
through the console now runs under `job.sh`'s supervisor and follows
`job.sh`'s rules exactly: a command killed by a signal nobody here sent is
`failed` with its exit status. In particular `lost` is **not** what an operator stop looks like: a job
stopped with `job.sh stop` records `stopped` even when it ignored SIGTERM and
had to be force-killed, and an agent stopped from the console records
`stopped` after its `done` event.

**Who stopped it.** A console job's supervisor records `stopped | stopped by
operator` for *any* SIGTERM, SIGINT or SIGHUP it receives, so the summary
alone cannot tell your Stop from a `kill` someone ran on the rig. The record
can: a stop through the console or the API writes `meta.stop_requested_by`
(the login), `stop_requested_device` and `stop_requested_at` before it
signals anything, and the audit log has the `POST /stop` row plus one
`stop escalation` row per signal actually delivered. No `stop_requested_by`
means the signal came from outside beast-chat. A `lost` job whose log ends mid-command is
a crash to investigate: the log is still on disk at `.run/sessions/<id>.log`
and its last lines are the diagnosis (`dmesg | grep -i oom` for the usual
suspect). A `lost` job whose log ends cleanly means the supervisor died
between the work finishing and the record being written — rare, and the log
is authoritative over the state.

**Does a console-started job survive `./stop.sh`?** Under `./start.sh -d`
with a reachable user systemd, yes, since the 2026-09-17 review. Under `./start.sh -d` the supervisor, `chat_server` and everything it
spawns used to share ONE systemd unit: `start_new_session` leaves the process
group, not the cgroup, so any `./stop.sh` — including the `./stop.sh &&
./start.sh` an update asks for — killed every job started from the phone, and
those jobs shared llama-server's memory cap (an OOM in a job could take the
model down). `chat_server` now starts each session through `systemd-run
--user --scope` when it finds itself inside a service cgroup and systemd-run
can reach the user manager; otherwise it spawns plainly, as before (and says
so once on stderr). Leaving the unit means leaving its `MemoryMax` too, so each
scope carries a cap of its own — `OPENBEAST_CHAT_JOB_MEM_PCT` percent of RAM
(default 50, swap off; `0` disables) — because an unbounded phone-started job
is exactly the runaway the stack's cap exists to contain. The same figure
also bounds them *together*: every scope goes into
`openbeast-chat-jobs.slice`, which carries that cap as an aggregate (a
runtime drop-in, set with `systemctl --user set-property --runtime`), so two
runaway jobs cannot add up to the box. If the slice cannot be capped, the
scopes stay where they were and each keeps its own cap. The pid,
the ledger record and Stop are unchanged (a scope execs the command in place).
**Under a foreground `./start.sh`, or where `systemd-run --user` cannot reach
a user manager, there is no scope and no memory cap on the job**: it runs in
`chat_server`'s own cgroup, and `chat_server` says so once on stderr. Console
jobs run under `job.sh`'s supervisor, so a `chat_server`
restart does not cost them their verdict: the supervisor writes
`done`/`failed`/`stopped` itself.

**`./stop.sh` with a phone attached.** The server ends open streams after 5
seconds of SIGTERM instead of waiting for the phone to hang up (it used to
linger indefinitely, still streaming from a stack that was "stopped"). The
console's EventSource reconnects by itself and resumes from its last offset.

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
- Notification content beyond title, state and a link. The payload rule is
  the feature (see *Push notifications*).
- Raising the 2000-character tool-result truncation in transcripts. The API
  surfaces `result_truncated: true` so the console can label it; changing the
  cap is its own decision.
