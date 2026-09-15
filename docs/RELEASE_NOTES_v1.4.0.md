# OpenBeast v1.4.0 — beast-chat 📱

**The rig's own work, watchable from a phone.**

v1.3.0 gave the model's *output* a durable URL. v1.4.0 gives the rig's *work* a
window. The machine in the corner runs 10-hour evals, 19-hour paired capability
campaigns and quantization sweeps, and until now the only way to know how any
of it was going was to be sitting at it.

**beast-chat** is the operator console for this rig's own sessions. What Claude
Code's mobile app does for Claude Code sessions, OpenBeast does for its agents
and campaigns: from a phone on your tailnet, list every session running on the
rig, watch its transcript stream live, **send it a message that lands at its
next turn**, stop it, or start a new agent. Put the phone to sleep mid-stream
and reattach an hour later — no gap, no duplicates.

```
https://beast.your-tailnet.ts.net:8445    → 3 sessions
  job    T1.17 capability pair (np1)   running   9h 12m   ████████░░
  agent  port 131_lup_decomp to zig    running     4m     ▏
  agent  summarize yesterday's rows    done       18m
```

---

## Two session kinds, because not everything worth watching is an agent

`agent` is a `runner.py` process, and it is steerable. `job` is *any* command
wrapped by `scripts/job.sh` — campaigns, sweeps, builds:

```bash
./scripts/job.sh run --title "T1.17 capability pair" -- bash scratch/campaign_master.sh
```

A job gets its own process group, so a stop from the phone reaches the
grandchildren instead of orphaning a 27B model holding 23 GB of VRAM. Both pid
and pgid go on record, and the terminal state tells the truth: exit 0 → `done`,
non-zero → `failed`, SIGTERM → `stopped`. An operator stop is not a crash.

Sessions write their **own** ledger record (`.run/sessions/<id>.json`, `0600`),
so every spawn path shows up without knowing beast-chat exists. Agents also now
survive a tool-server restart instead of being SIGTERMed by its `atexit` hook —
which is a bug fix that landed here because the ledger made it visible.

## The reattach contract

`from=N` is a byte offset into the transcript. No server-side cursor, no ring
buffer, no per-client state to lose. Every event carries its end offset, so a
client that drops resumes exactly where it stopped.

The subtle part is that a reconnecting browser re-requests its *original* URL,
stale query parameter included. If the query wins, every radio blip replays the
whole session. The replay header beats the query parameter — and a truncated
transcript produces a clean reset rather than a silent gap.

## The eval guard is the point of this release

An injected operator message changes one arm of a paired measurement. The
resulting leaderboard row does not look corrupted. **It looks like a result.**

The original design had two locks. Adversarial review found that exactly one of
them worked: `scripts/lib/conf.sh` exports the opt-in unconditionally and
`run_eval.py` copies the whole environment into the child, so on any configured
rig that lock was already open. The surviving lock was derived from whether a
task's text happens to mention an eval path — all 291 current variants do, which
made it safe **by luck, not by construction**. Worse, that same marker drives a
shipped A/B intervention, so an arm measuring "path guard off" would have turned
steering *on* for that arm alone.

Three locks now, each sufficient alone, the first unconditional:

1. `run_eval.py` sets `OPENBEAST_EVAL=1` in **every** child environment and
   strips the opt-in. Checked first. No argv overrides it.
2. `OPENBEAST_TASK_PATHS` — kept, checked second, no longer load-bearing.
3. The opt-in is **explicit argv only**: `--steer` / `--session-id`. The
   environment opt-in is deleted, not deprecated.

Proven end to end: a hostile `say` planted in an inbox, spawned through the real
harness with both env flags set, both steering flags passed, and a task naming
no eval path — inbox never opened, no ledger record, no `steer` event, hostile
text in no request payload. The same probe against the pre-fix tree fails five
checks and reaches the model.

Inertness was re-proven after every edit by a differential harness against
`main`: with steering off, transcript, request payloads, stdout and stderr are
byte-identical. An unconfigured rig behaves exactly as it did yesterday.

## Auth is two-tier, and the tiers are not arbitrary

**Reading** is your tailnet login checked against `CHAT_OPERATORS` — unlisted
gets 404, never 403, and anonymous likewise, so a session's existence does not
leak. **Writing** (send, stop, start) additionally requires an enrolled device
key carrying the new `chat` scope, because starting an agent is remote code
execution on the rig and a proxy-injected identity header is forgeable by
anything already on the box. `TrustedHostMiddleware` stops DNS rebinding, which
is what makes loopback not a trust boundary in the first place.

The console *document* is deliberately ungated: a browser cannot put a header on
a document request, the page carries no session data, and gating it only 404'd
the operator on their own rig.

## What review cost, honestly

Three hostile agents, then fixes, then repairs to what the fixes broke. Closed
along the way: sessions started from the phone were never reaped, became
zombies, and reported `running` forever while accepting messages into a dead
process; a record with no recorded start time let `/stop` signal an unrelated
live process group; a headerless request streamed any transcript;
`/openapi.json` advertised the whole write contract to a caller denied
everywhere else; the resume path replayed operator messages with no locks; and a
lost cursor delivered an operator's instruction twice.

All 19 runner/ledger fixes were mutation-checked — each reverted individually,
each breaks a named test, 19/19.

One reviewer finding was **wrong, and it made it into a spec before anyone
noticed**: the operator-stop turn count. The implementing agent pushed back and
was right — ops are consumed before the turn runs, so the original count was
correct. Reverted, with the reasoning left in the code so it does not get
"fixed" again.

## Also in this release

- **The local test runner ran one file.** `tests/run_tests.sh` pytested only
  `test_tools.py`, while CI ran `pytest tests/ -q` as a separate step. Every
  suite outside that file — artifact, chat, sessions, steering, identity;
  several hundred tests — was green in CI and never executed locally. A runner
  that prints `ALL TESTS PASSED` while skipping most of the tests is worse than
  no runner. It now runs what CI runs.
- **One kill discipline, not two.** beast-artifact's health-check restart kills
  by recorded pid because a pattern kill destroyed a live measurement run on
  this box during v1.3.0's development. The chat restart now reads
  `.run/chat.pid` first too, keeping a path-qualified `pkill` only as the
  fallback for a console started outside `start.sh`.
- **The serve-mount map lists every surface.** `setup-tailscale.sh` prints which
  OpenBeast surface sits on which tailnet port; it knew about `:8445` and not
  `:8446`, since the table itself is new here. Both are listed, and `--help`
  prints the whole header again instead of truncating it at a stale line number.
- `stop.sh`, `start.sh`, `doctor.sh` and `healthcheck.sh` all learned the new
  service, and a failure of an opt-in observability surface never takes the
  language model down with it.

## Enabling it

```bash
# openbeast.conf
BEAST_CHAT=true
CHAT_OPERATORS=you@github          # who may READ; empty = any identified tailnet login

./stop.sh && ./start.sh
./scripts/setup-tailscale.sh --publish-chat                     # :8445, for phones
./scripts/clients.sh enroll phone --label "My phone" --scope chat   # WRITE access
```

Off by default. With it off nothing new listens, no ledger is written, and the
steering inbox is never even `stat()`ed.

## Upgrading

**This release rotates the eval cache era.** `agents/runner.py` is one of the
six files `evals/cache.py` hashes into every cache key, and beast-chat changes
it. Banked eval results stay valid and comparable *to each other*; rows measured
after this upgrade belong to a new era and must not be paired with rows from
before it. If you have a campaign in flight, finish it — or plan to re-run both
arms — before pulling. That is why this sat as a draft PR while a 19-hour paired
capability run finished on the rig it was built on.

Everything else is inert. beast-chat is off until you turn it on, the 17 tools
are unchanged in name, schema and behavior, and with the feature disabled the
agent runner is byte-identical in what it sends and prints.

**Verification:** 828 pytest passed / 6 skipped on the merged tree ·
`test_scripts.sh` 165 · `test_clients.sh` 72 · `test_job_sh.sh` 38 · `ruff`
clean · `shellcheck -S error` clean · suite doc-drift and variant-spec audits
clean. Live: headerless request refused, foreign `Host` 400, an API-started job
reaching a terminal state with its process group reaped, and a truncated
transcript producing a clean reset.
