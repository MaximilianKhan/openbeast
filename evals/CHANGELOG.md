# Eval suite changelog

## v5.0, wall-scaled turns (2026-11-16) — opt-in budget, era unchanged

`run_eval.py --turns-per-wall-s N` gives each unit max(its max_iter,
wall_s / N) turns, ignoring the manifest's `max_iter_ceiling`, so the unit's
own wall budget (600 / 900 / 1500 s by tier) is the binding limit and the
turn cap only stops a loop. N = 15 gives medium 40, hard 60, xl 100 (from
15-30). Why: the GLM-5.3-Flash v5 L0 audit found most failing units ended on
max_iterations after 150-400 s of a 1,200-1,800 s wall. The effective budget
is in the cache key (`.mi<N>`) and on every result row (`max_iter`), and the
results header records `turns_per_wall_s`. Default runs are unchanged.

## v5.0, runner v2 (2026-11-16) — agent-harness frictions (era roll)

Same suite, manifest and `HARNESS_VERSION`. `agents/runner.py` and
`agents/tools.py` are hashed into the era, so this change moves it: no row
measured before runner v2 replays into it. Re-measure the v5 baseline after
merging; a pre-v2 row and a v2 row are not one experiment.

Why: an audit of GLM-5.3-Flash's 45 failed v5 units (2026-11-16) found 43
genuine model failures, and harness frictions that cost the model turns in
many of them:

- **Platform line.** The runner's system prompt now names the tool host's
  OS and the bash tool's shell (on macOS: BSD userland, `sed -i ''`, no GNU
  `timeout`). GNU habits failed on the macOS eval host because nothing said
  it was BSD.
- **bash, not sh.** The bash tool runs `/bin/bash -c` when /bin/bash exists
  (else `/bin/sh -c`, as before), and drops an inherited `BASH_ENV`. Process
  substitution `<(...)` failed under sh. This also ships to Mac/Linux clients.
- **Turn budget.** The prompt states "you have N tool turns", and a short
  notice rides with the plan block in the last 3 turns. Most failing units
  ended on max_iterations at 150-400 s of a 1200-1800 s wall budget, still
  exploring.
- **Unchanged update_plan.** Re-sending the current plan gets a nudge (it
  still costs its turn). One unit spent 60% of its turns re-sending one plan.
  The plan block and the instructions no longer say "keep it current".
- **Repeat guard.** The 3rd consecutive identical tool call with an identical
  result gets "You have seen this exact output N times; act on it". The
  audit saw the same `cat` 15x and the same bash command 19x.
- **Timeouts.** A bash timeout always says it timed out and the limit. On
  macOS, killpg on a group of zombies raised EPERM, so a killed hung
  pipeline read as "[Errno 1] Operation not permitted".
- **Eval turn cap.** Eval requests now carry `max_tokens` (they sent none):
  8192 with thinking off/unset, 16384 with `OPENBEAST_AGENT_THINKING=on`,
  the same for every model; `OPENBEAST_AGENT_MAX_TOKENS` still overrides
  (0 = uncapped). The client's read timeout is sized to the cap at 8 tok/s
  (floor 600 s), and eval runs never retry a timed-out turn: a first sizing
  (16K at 20 tok/s) let the client time out first under 4-way load, where a
  long-context turn decoded at ~12 tok/s. On 168 (c, f) the model's first code-writing turn ran past
  the client's 600 s timeout in 5 of 5 attempts, was retried identically, and
  the unit spent its 1,800 s wall without writing a file; a capped turn ends
  as "length" and the existing truncation recovery returns its head with a
  nudge.
- **Seed switch (opt-in).** `OPENBEAST_AGENT_SEED=<int>` sends `seed` on
  every request. The Spark TensorFold server uses a FIXED seed when none is
  sent (probed 2026-11-16: identical text for three temperature-0.6 requests;
  seeds 1/2/3 differ; seed 1 twice is identical), so each Spark run is one
  reproducible draw and tiny prompt differences flip units (G12). Repeat an
  arm under k seeds to separate an effect from that chaos. Like the thinking
  toggle, **the cache key does not see it**: run with `--no-cache` and a
  distinct `--model-name`.
- **Thinking toggle (opt-in, off by default).** `OPENBEAST_AGENT_THINKING=on|off`
  sends `chat_template_kwargs.enable_thinking` true/false on every request;
  unset sends nothing (requests unchanged). It is the switch for a "thinking
  on" ablation arm (`OPENBEAST_AGENT_THINKING=on python evals/run_eval.py
  ...`; run_eval passes the env through). **The cache key does not see this
  variable**, so run such an arm with `--no-cache`: otherwise it replays
  thinking-off rows and banks thinking-on rows under the baseline's key.
  `reasoning_content` is never fed back into the history.

## v5.0, harness6 (2026-11-16) — review-1116 fixes (era roll)

Same suite and manifest (71 units); the harness and `vlib.py` changed, so the cache
era moved (`HARNESS_VERSION` harness5 -> harness6, `vlib.py` rehashed). No
harness5 row replays into harness6: rerun the v5 units measured before it.
One roll covers every review-1116 eval fix below. What changed in how
units are judged:

Harness and vlib:

- A macOS solution can no longer signal processes outside its sandbox
  (it could SIGKILL sibling units, validators, the harness).
- The macOS deny-list sandbox fails closed (verdict `sandbox`) instead of
  running the solution unsandboxed when `sandbox-exec` fails.
- The Linux Landlock confine refuses network sockets with seccomp (UDP,
  and TCP below kernel 6.7, were open).
- `run_solution` keeps the solution's rusage on a normal exit (fixed-CPU
  gates passed over-budget solutions ~1 run in 12); on macOS the solution
  monitor and the leftover sweep read libproc in-process instead of
  forking `ps -A` every 50 ms.
- The validator completion line is no longer a module global an
  in-process solution can read; in-process units remain forgeable by
  frame introspection (documented).
- The leftover sweep kills only provably-owned process groups (reused
  pids no longer kill sibling units), kills before the timeout drain on
  macOS, and a validator's pgids file cannot name an older process.
- One harness per `/tmp/eval_*` root (`run_eval.fixture_lock`): every
  unit holds an flock per fixture root across harness processes; a sweep
  waits (and says so at once), a prover refuses at once with the holder's
  pid.
- The validator's `TMPDIR` is private (`<private>/tmp`).
- `--jobs` is clamped to beast-gate's per-device in-flight cap; the
  remote health check retries; probes never send the ambient key to an
  untrusted `--base-url`; the stratified L2 records the true pi.

Families 163-179:

- 163/167/168: the compiler no longer reads the sealed reference
  (`vlib.build_private`: include lint, private build dir, macOS read-deny);
  a hanging compiled solution fails on its judged warm-up instead of as an
  infra `validator_timeout`; 163's prompt states its 6 s CPU budget.
- 173: the reference absorbs its own undelivered `cancel()` on CPython
  <= 3.12; 24 cancel-tail scenarios pin it (624 scenarios in all).
- 175: the oracle is incremental (~0.2 s instead of ~1.8 s per validation;
  same expected results); the argvcheat arm proves on Linux.
- 176: `shopd/` is copied without following links, under a cap.
- 179: the validator never runs git in the agent's repository (a promisor
  remote's `uploadpack` ran unsandboxed); the fixture history is one
  `git fast-import` (same SHAs, ~0.5 s instead of ~4 s on a Mac).

Families 183-196, the U3 upgrades and the kept-v4 units:

- **Toolchain pins.** The harness resolves and fingerprints every compiler
  and inspection tool before any agent runs and refuses a validation when
  one moved or changed (not banked). U3 compiled units build a private copy
  with the pinned compiler (`vlib.start_build`); every validator gets
  `GOENV=off`, `GOTOOLCHAIN=local` and the pinned rustup toolchain. New
  prover arms: `toolchain:path`, `toolchain:goenv`.
- **Confinement.** 183, 188, 40, 36_a and 123_a run the solution with
  `confine=` (stdlib + a private dir) on macOS and Linux; 40 lints strictly
  and blocks numpy/torch with an audit hook.
- **Kept-v4 gates** of 146, 147, 153, 158 and 159 scale with
  `OPENBEAST_TIMEOUT_SCALE` (the v4 number at scale 1); the prover lint now
  sees a measured time compared with a bare number.
- **122's oracle** builds its columns in the integer domain (bit-identical,
  2x faster on aarch64).

## v5.0 (2026-11-15) — the measured suite (CURRENT; era break)

`evals/SUITE_VERSION` = v5 rolls every cache era; the v4 board is frozen
and nothing is compared across versions. The suite is the manifest
`evals/suites/v5.json`: **71 scored units over 34 base tasks**, all 12
categories and all six languages, no probe units. It is the designed v5
(111 units over 39 bases + 7 probes) minus what failed review: 14 new
families and the U3 upgrades (36, 40, 122, 123) passed three adversarial
rounds and ship; 12 items are the v5.1 backlog (`evals/README.md`), and
24 units of v4 tasks, kept exactly as defined in v4 and proven, keep
their categories covered. A full three-arm profile is 3.73 reference
hours, inside the 4-hour budget even sequentially. Harness: `harness5`.

- Hidden seeded tests inside the validator for new and upgraded units; no
  shipped unit has an expected file or `check.py` in the agent's dir; a
  mismatch prints only the case index.
- Zig API tutoring banned from prompts (L0 is unassisted).
- Oracle seal: `evals/tasks|refs|suites` unreadable under `OPENBEAST_EVAL=1`;
  `fetch`/`web_search` off under eval (or recorded).
- Every shipped unit proven without a model (`tests/prove_task.py`; family
  formats delegate to the family's own prover).
- Validator isolation: stdin-fed script, private cwd and env, solution
  confinement (network, process-info, exec, read-deny / allowlist), a
  **private build cache per validation** (zig, Go, cargo; ccache off),
  deleted afterwards, and a **completion line** (a python validator passes
  only when it ran to its end).
- **Every process the agent started is killed before validation**, detached
  daemons included (env tag, macOS sandbox tag, tree polling with the runner
  as a Linux subreaper, groups and sessions, a cgroup where delegated); a
  unit with survivors is not validated.
- Arms L0/L1/L2 in one process (`run_eval.py --arms`), diagnostics keyed per
  unit, per-unit wall caps, longest-first scheduling, `--slots`.
- `evals/profile.py --budget-h 4` (calibrate k on L0, fixed L2 cut order,
  `budget_skip` never scored) and `evals/ablation.py` (McNemar, MDE /
  within_churn, strata, Horvitz-Thompson for stratified L2, `--pool`).
- Scoring: LANG over multi-language bases only; imputation retired for v5
  and every arm; a full v5 row = the manifest's scored units (read from it).
- The v4 fast pin is renamed `v4-fast` (was `v5-fast`) and stays v4-only.

## v5-fast overlay (2026-09-08) — pinned fast subset, imputation-scored

NOT a new suite version — v4 stays current and frozen; v5-fast is a pinned
SELECTION of it (`evals/suites/v5-fast.json`): the 86 units that
discriminate between reference models + 20 all-pass tripwires. Runs via
`--suite v5-fast`, scores by imputing the 185 saturated units, which
reproduces full-291 capability exactly for in-family models (identity
verified per reference run by `make_fast_suite.py`; raw-subset ranking
measured at τ = +0.810 and rejected). Fast runs never enter the
leaderboard and share cache keys with full runs. ~0.85 h with `--jobs 4`
vs ~5.6 h full.

## v4 (2026-07-08) — the hardened suite (CURRENT)

137 base tasks / 291 effective units across all 12 topic categories.

A full adversarial re-review of v3.5 (see `docs/archive/EVAL_REVIEW_2026-07-07.md`)
found 117 of 159 tasks flagged — inflators (empty submissions / naive
algorithms / dead perf gates passing), deflators (correct code failing on
broken fixtures or comment-tripping lints), and unverifiable properties.
v4 is the rebuild (plan: `docs/archive/EVAL_V4_PLAN.md`):

- **Pruned 25** overlapping / unverifiable tasks (159 → 134).
- **Added 3 DSP tasks** (160 FIR, 161 Goertzel, 162 biquad IIR) so Signal
  Processing is a real category (1 → 4 tasks). Net: 137 tasks.
- **Every surviving flagged task hardened + verified BOTH ways**
  (reference passes; the documented cheat is empirically rejected) under
  9 systemic rules: pre_validate on all fixture tasks, comment/string-
  proof anchored lints, no readable answers, perf/memory gates measured
  to ≥10x, printf fixtures, self-contained variant texts, promise↔assert
  parity, durable 6-language refs, ≥5x timing margins.

v4 numbers are NOT comparable to v3.5. The v3.5 leaderboard is retained as
the "before" for the launch narrative and will be archived on a later
formal-display pass.

## v3.5 (2026-05-07) — legacy

159 base tasks / 323 units. Full report and leaderboard in
`docs/RESULTS.md`. Retained for the evolution story; see the review above
for why it was rebuilt.
