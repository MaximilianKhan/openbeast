# Harness notes — what could make a model perform better in OpenBeast's agent loop

A running log, opened 2026-11-16, of harness additions and improvements suggested by
evidence from v5 runs and their audits. Every entry names its evidence; nothing here is
adopted until it is measured as its own arm (a runner or tools change moves the eval era,
so it is judged on a full re-run, never on a re-roll of failed units).

Open question this log feeds (see the research-paper TODO in `docs/TODO.md`): does each
model need its own harness, or can one unified harness serve GLM-5.3-Flash,
Qwen3.8, DeepSeek v4.1 Flash and the rest, with a few per-model knobs?

Status key: **IDEA** (noted) · **BUILDING** · **ARM** (being measured) · **ADOPTED** · **REJECTED**.

---

## Headroom — the gap the harness work is chasing (2026-11-16)

| Row | v5 (71 units) | Notes |
|---|---|---|
| Claude Opus 5.5 (reference ceiling) | **71/71** (68/71 at the strict 1x wall) | Claude Code's own tools, no turn cap; solver never saw validator output; integrity scan clean |
| GLM-5.3-Flash L0 (daily config, runner v1) | **26/71** | 43 of 45 fails audited as genuine model mistakes *under this harness*; most ended on the turn cap at 10-30% of their wall |
| GLM L1 (Zig packs) | Zig 0/6 -> 3/6 | packs fix the 0.16 API drift |
| GLM arm A (runner v2: G1-G4, G8, G11 v1) | **31/71** | +5 net vs L0 (11 rescued, 6 regressed; single seed). Rescues cluster on the audited frictions (36_b/c, 143, 146, 121, 179); same time and turns as L0 |
| GLM arm B (A + wall-scaled turns, G1) | **36/71** | +5 over A; single seed |
| GLM arm C (B + thinking, `--wall-scale 2`, B's turns) | **18 of the first 21 units** (final PENDING) | B passed 8 of the same 21: 10 rescued, 0 regressed; 14 of the 18 passes inside B's original wall; ≈ 2.3x B's time per unit |

Max's read, and the working hypothesis: GLM-5.3-Flash is a far stronger model than
26/71 suggests, and much of the gap is the harness around it, not the weights. Each arm
above tests one piece of that; whatever gap remains after them is the next experiment.
Future-experiment backlog (beyond A-C): k-seed repeats (G12), "test before done" (G9),
edit-not-rewrite diagnostics (G13), thinking budget sweeps, parallel tool calls, a
Qwen3.8 v5 row and DeepSeek v4.1 Flash on the same arms (the paper's cross-model axis).

## Thinking is the dominant lever (2026-11-16) — status: PENDING C's final

On GLM-5.3-Flash, per-request thinking (G5; `--thinking on`, at `--wall-scale 2`
so its turns equal B's) passed 18 of arm C's first 21 v5 units. Arm B passed 8 of
the same 21, so 10 were rescued and none regressed. 14 of the 18 passes finished
inside B's original wall, so the gain is the thinking, not the doubled time. Per
unit it costs about 2.3x B's time. No other single harness change has moved more
than 5 units over the whole 71: runner v2 (A) +5 over L0, wall-scaled turns (B)
+5 over A. One seed, 21 units, and a partial arm, so the size is provisional and
the direction is not in doubt.

What it changed:
- **The campaign is thinking-first** (`evals/campaigns/v5-thinking-first.json`,
  [MODEL_CAMPAIGN.md §5](MODEL_CAMPAIGN.md#5-harness-arms-thinking-first)). It
  runs first the composite of every change believed to help, with thinking on.
  Then it runs that composite with one component removed at a time, still
  thinking, then a seed arm, and last the no-thinking counterparts. Each
  G-entry below that is still an ARM is now measured *with thinking*, as
  "composite minus it". A component that does not cost more than the seed noise
  when removed is dropped from the recommended config.
- **Every G-entry's evidence is from thinking-off runs.** Turn-cap endings (G1),
  no-op loops (G2, G3) and reasoning slips (G5) may shrink when the model thinks,
  so a fix's value can shrink with them. Packs (G6, G19) supply knowledge thinking
  cannot, so they should hold. The ablations say which is which.
- **Thinking's price is latency.** The report sets it beside the gain (time and
  completion tokens per unit, and passes inside the unwidened wall). No-thinking
  is recommended only if the composite with thinking off comes within noise of it.

Update when C finishes (its full 71) and again when the first thinking-first
campaign reports (DeepSeek v4.1 Flash is next).

## GLM-5.3-Flash (spark-hydra, thinking off) — from the 2026-11-16 v5 L0 audit

Source: the 45-failure audit (one auditor + one skeptic per task; 43 TRUE_FAIL, 2 harness).
Transcripts: `agents/logs/agent-20261116-0[6-8]*.jsonl`; L0 results
`evals/results/eval-glm-5-3-flash-L0-20261116-060847.json`.

| # | Observation (evidence) | Candidate change | Status |
|---|---|---|---|
| G1 | Iteration cap, not time, ended most failures: 20-30 turns spent in 150-400 s of a 1,200-1,800 s wall (122 a-e, 158, 163, 36). | Tell the agent its turn budget up front; a remaining-turns notice at <= 3. | ARM A: in runner v2, 31/71 vs 26/71 (#179) |
| G2 | No-op `update_plan` loops: up to 15 of 25 turns with identical args (196, 183, 173, 178, 190). The prompt's "keep it current" wording invites it. | Don't count / nudge an `update_plan` whose args equal the current plan. | IN RUNNER V2 (#179; arm A 31/71) |
| G3 | Identical tool call + identical result repeated: same `cat` x15 (195), same bash x19 (121), same rebuild x12 with no edit (163_b). | After 3 identical calls, append "you have seen this output N times; act on it". | IN RUNNER V2 (#179; arm A 31/71) |
| G4 | Tool host is macOS/BSD, model assumes GNU: `sed -i 's/…'`, `cat -A`, `timeout`, `<(...)` under `/bin/sh` each cost a turn (124, 122_e, 36_c, 167, 179). | Platform line in the runner prompt; consider `bash -c` for the bash tool. | IN RUNNER V2 (#179; arm A 31/71) |
| G5 | Thinking off: reasoning slips that thinking would likely catch — the missing `x/sqrt(2)` in N(x) debugged for 20 turns (36 b/c/d) despite the sample disagreeing; copied its own carry bug into a hand trace (163_a). | Per-request thinking (`chat_template_kwargs.enable_thinking`), verified live on TensorFold. | ARM C: 18/21 vs B's 8/21 on the same units (10 rescued, 0 regressed; final PENDING). The dominant lever (see above); the default campaign is now thinking-first |
| G6 | Zig 0.16 API drift: pre-0.15 `std.heap.GeneralPurposeAllocator`, `std.io.bufferedWriter`; whole budgets spent grepping lib/std (36_f, 123_f, 163_f, 122_e). | Language packs (L1) + diagnostics/escalation (L2) — the existing ablation arms. | ARM (L1/L2) |
| G7 | A non-streaming turn ran past the gate's 600 s read timeout and the OpenAI client retried it identically: 1,200 s lost, two units never wrote code (168_c, 168_f). | Gate EDGE_READ_TIMEOUT above unit wall (done for evals, 3600); retry policy: never re-send an identical request after a read timeout; consider streaming. | PARTLY ADOPTED |
| G8 | Hung pipelines reported as "[Errno 1] Operation not permitted" instead of a timeout (146). | Always name the timeout and its limit. | IN RUNNER V2 (#179; arm A 31/71) |
| G9 | Model stops short of verifying: several fails had a working fix in a scratch file but never wrote it back to the destination (36_c, 36_d, 143). | A pre-`task_done` check: "the destination file differs from your last passing scratch test" — or a generic "re-run your test against the destination before task_done" nudge. | BUILDING (#185, opt-in `OPENBEAST_AGENT_VERIFY_DONE=1`): the first `task_done` gets one non-blocking "Before finishing: re-run your test against <dest> itself" when a destination the agent wrote was not named by a bash command after the last write. **Measure:** an arm with the switch on vs off at the same `--seed`s (keyed `ag-vdone`); count nudges with `grep -l "Before finishing:" agents/logs/agent-*.jsonl`, and check rescues on 36_c/36_d/143 |
| G11 | Runaway turns: on 168 (c, f) GLM's first code-writing turn generated for > 600 s, uncapped (eval mode sends no max_tokens), reproduced in 3 samples on 2026-11-16 L0 and again in the 09:00 rerun after the gate timeout was raised: now the OpenAI client's own 600 s timeout fires, retries once, and the unit burns its 1,800 s wall without writing a file. | Cap eval turns (e.g. 12-16K completion tokens with thinking off) so the runner's existing truncation recovery returns the head of the turn to the model; client `max_retries=0` on read timeouts. Must apply to every model (era move). | IDEA |
| G12 | **Decoding is effectively deterministic on the Spark server** although the runner sends temperature 0.6: L1/L2 transcripts are byte-identical until the first injected byte; a 23-byte `diagnostics: OK (zig)` flipped 36_f and 123_f, an `ls` mtime flipped 179 (L2 audit, 2026-11-16). Single-sample arm deltas on small cells are trajectory chaos, not effects. | Seed-varied k >= 3 samples per (unit, arm) for any rescue/regression claim; check whether TensorFold honours `temperature`/`seed`; strip volatile bytes (mtimes) from fixtures. | IDEA (methodology). Tooling in #185: `run_eval.py --seed N` (keyed `ag-seed<N>`), so k = 3 is three runs, `--seed 1/2/3` |
| G13 | A failing diagnostic on a `write_file` result invites a full rewrite: 9/29 next mutations were rewrites vs 2/31 after a failed build without diagnostics; the rewrite re-rolls correctness (167_b, 163_d regressed in L2). | Failing diagnostics on write results end with "fix with edit_file at line N", or show only the first error with its line. | BUILDING (#185, opt-in `OPENBEAST_DIAG_EDIT_HINT=1`, only with diagnostics on): a failing block ends "Fix the first error with edit_file at line N; don't rewrite the file". **Measure:** an L2 (diag) arm with vs without it at the same seeds (keyed `ag-ehint`); the rewrite rate of the next mutation after a failing diagnostic (9/29 today) and 167_b / 163_d |
| G14 | Push diagnostics do not save turns: after a reported error the model rebuilds anyway (167_f it5, 163_f, 122_e). | Tell the agent (runner prompt) that write results already carry compiler output; measure as a sub-arm. | IDEA |
| G15 | shellcheck re-reports pre-existing warnings on a file the model only edited; cost 179 a turn. | For edit_file, report only diagnostics new relative to the pre-edit file. | ADOPTED (#185, default on; lint only, compiler errors are never filtered; nothing new reads "diagnostics: no new findings (shell; N pre-existing not repeated)"). Era move |
| G16 | Escalation can only fire for zig on the Mac harness host: no go/c/rust index; the cpp index was built with g++ 16.2.1 and the host has clang 21 (index_gate served:false). The arm is labelled "packs+diag+escalate" anyway. | Stamp per-language card eligibility in the results header and the exposure line ("card-eligible units: 6/38"); build a clang cpp index. | Stamp ADOPTED (#185): `harness.escalation_eligible_langs` and `card_eligible_units` in escalate arms, and "card-eligible units: n/N" in the run banner and the `ablation.py` exposure line. The clang cpp index is still IDEA |
| G18 | **Cache-key trap:** `OPENBEAST_AGENT_THINKING` / `OPENBEAST_AGENT_SEED` changed agent behaviour, but the cache key did not see them. Arm C (thinking on), or any seed repeat run without `--no-cache`, would replay arm A's rows and bank its own rows under A's key. | Put the agent switches in the key, omitted when unset. | ADOPTED (#185): `ag-<parts>` component (thinking, seed, verify-done, edit-hint); stamped in `harness.agent_switches` and on every row (`agent_thinking`, `agent_seed`); `--seed` / `--thinking` flags |
| G17 | Telemetry: shell unit 179 bucketed as python (no `language` on non-variant rows, `ablation.py` defaults to python); `go vet` errors counted as 0 (regex misses the `vet: ` prefix). | `language` on every row; `^(?:vet: )?\S+:\d+:\d+: `. | BUILDING |
| G10 | Request `model` was the rig alias `qwen-27b-q5`, not the served id (cosmetic; audit labels wrong). | Send the served id. | ADOPTED (#177) |
| G19 | **Zig is the only language failing for lack of knowledge** (per-language taxonomy of 153 failed units, L0/L12/A/B), and the v1 pack missed what the v5 zig runs hit: `Thread.spawn` given an allocator or a non-tuple args (`expected a tuple` x9), `std.Thread.ResetEvent`, `Io.File.writeAll` / `openFileIo` / `stdin(io)` / `reader(&buf)` arity, `while (c) : i += 1` (x23), `const std = @std;`, and the lint errors (pointless discard x21, never-mutated var, unused constant, `i0`/`i2` shadowing). | Zig pack v2: five curated bullets, each with OLD fixtures that must fail with the pinned error and a NEW one that must compile; the generated digest is trimmed 35 -> 18 lines to stay at 1,959/2,000 tokens. | BUILDING (#188 + follow-up; v2 is `agents/packs/zig-0.16-v2.md` @`ed18f83e`, eval-only via `OPENBEAST_PACK_VARIANT=v2`; production keeps serving the measured v1 `zig-0.16.md` @`5cdf4b64`). **Measure:** packs arm vs L0 at the same seeds on the 6 zig units: pass rate, turns, and how often the five signatures still appear in transcripts (`docs/LANG_AWARENESS_PLAN.md` §5.1) |
| G20 | **Host friction cost about 95 turns**, more than all non-zig knowledge errors combined: `nproc`/`/proc/cpuinfo`, `cat -A`, `grep -P`, `timeout`, `/usr/bin/time -v`, `pthread_barrier_t`, `sysctlbyname`/`sysconf` without headers, in every language. G4's platform line covers BSD sed, `timeout` and `cat -A` by name only. | A host pack probed from the tool host (`agents/lang/host_pack.py`): every line is a probe that ran and failed, plus a remedy whose own tool probed; given to EVERY unit with packs on (`BEAST_PACKS=1` = language pack + host pack); its sha is in `pack1-<sha8>`. | BUILDING (this PR; 232 tokens on the Mac host, empty on a GNU/glibc host). **Measure:** the same packs arm, non-zig units: turns lost to the host signatures above (`grep -c "nproc: command not found\|illegal option -- A\|invalid option -- P\|timeout: command not found\|pthread_barrier_t"` over the arm's transcripts) vs the ~95-turn L0/L12/A/B baseline, plus pass-rate change on the units that lost turns to them (122 b/c, 124, 146, 159_c, 163, 167_c, 173, 188) |
| G21 | **Rust edition contradiction:** five v5 Rust variants (36_e, 122_d, 123_e, 158_e, 159_e) said "Idiomatic Rust 2021+" but the validator ran bare `rustc -O`, which is edition 2015: `try_into` without an import failed (36_e, `no method named try_into`), costing turns in three runs. Push diagnostics compile Rust with `--edition 2021`, so a file the agent saw pass failed the validator. | Task text and validator both say `rustc -O --edition 2021`, like 167_e and 168_e already did. | BUILDING (this PR; the five units re-proven with `tests/prove_task.py`). A task-text change moves those units' task hash, so their old rows do not replay. **Measure:** the next L0 re-run of the five units: no `E0599 ... try_into` and no edition-2015 errors |
| G22 | Early turns go to discovery the harness could have done: most failing GLM v5 L0 units ended on the turn cap (G1), and transcripts open with `which`/`--version`/`ls` of the task dir. Nothing told the agent its toolchain versions, which CLI tools exist (`timeout` does not on the Mac host), or which task files exist yet. Prior art ([`reviews/HARNESS_PRIOR_ART-2026-11-16.md`](reviews/HARNESS_PRIOR_ART-2026-11-16.md)): Droid's startup environment block, "shown as shell command output", which "helped the agent avoid reissuing similar commands" (Factory, 2025-09-25); Meta-Harness's sandbox snapshot "saves 2-5 early exploration turns", more on weaker models (arXiv 2603.28052); Codex's environment-context message; the prior-art gap table rates ours **Large**. | A first-turn environment block, appended to the task message as probe output (<= 1,200 chars, ~300 tokens): OS/arch; python3, go, rustc, zig, cc/clang/gcc versions (toolchain_versions()' commands, 3 s timeouts, run in parallel); git/make/cmake/timeout/gtimeout/perl/jq present or missing; the workdir; which OPENBEAST_TASK_PATHS exist; a depth <= 2, <= 30-entry listing of the task's own `/tmp/eval_*` dirs. Never an env value, never anything outside `/tmp/eval*`. Host userland quirks stay in `platform_line` and the host pack. | BUILDING (opt-in `OPENBEAST_AGENT_ENV_BLOCK=1`, keyed `ag-envb`; the transcript gets an `env_block` event). **Measure:** an arm with the switch on vs off at the same `--seed`s; turns until the first write_file/edit_file to a destination and the count of `--version`/`which`/`ls /tmp/eval` calls in the first 5 turns (both from `agents/logs/agent-*.jsonl`), turn-cap endings, and net rescues |
| G23 | The runner knows its turn budget but not its wall: units killed by run_eval's timeout got no warning, and a slow model (thinking on, arm C; long single turns, G11) cannot pace itself. Prior art: Droid shows elapsed time only past thresholds (2025-09-25); LangChain deepagents' time-budget warnings (2026-02-17). | run_eval's `run_agent` passes the wall it kills at (scaled by `--jobs` contention) as `OPENBEAST_AGENT_WALL_S` (env, so argv is unchanged; `runner.py --wall-s` for standalone use). With the switch on, the first turn starting past 50% and the first past 80% get one transient notice next to the turns notice: "[wall clock — N s of your M s wall used; finish and verify the destination now]". Never stored in the history. | BUILDING (opt-in `OPENBEAST_AGENT_WALL_WARN=1`, keyed `ag-wwarn`; harness6 -> harness7 because `run_agent` sets the env var). **Measure:** same-seed on/off arm; `grep -c '"wall_notice"' agents/logs/agent-*.jsonl` for exposure; timeout rows (`exit_code -1`) and rescues among units that saw a notice |
| G24 | **The trailing-dot path bug, in every arm since the R1 path guard (2026-09-10).** run_eval extracted the task's `/tmp/eval*` paths with `/tmp/eval[\w\-./]*`, which keeps a sentence-final period, so `OPENBEAST_TASK_PATHS` held `/tmp/eval_gemm/gemm.c.` and the done-check refused `task_done` until that file existed; G22's environment block printed it too. 27 of 320 units (25 of the 71 v5 units). GLM copied its solution to `gemm.c.`, compiled `gemm.rs.`, and went looking for the checker (G25). Measured with `evals/audit/integrity_scan.py --trailing-dot` (turns after the first refused `task_done` / turns that named a dotted path): L0 17/11 (2 units), L1+L2 8/4 (1), A 10/2 (1), B 113/56 (7), **C 85/35 (11)**, E so far 29/25 (10). No verdict flipped directly (the validator reads the real path); B's 122_c spent 43 turns there and failed. | `run_eval.task_paths()` strips trailing periods (for `OPENBEAST_TASK_PATHS` and `fixture_roots`); `tools._task_expected_paths` strips them too. | ADOPTED (#195; harness7 -> harness8; era move). **Measure:** the next arm: `--trailing-dot` reports 0 |
| G25 | **The eval seal covered only the answer key.** Arm C 122_b it15-24: GLM's bash read Claude Code workflow journals (`~/.claude/projects/.../workflows/*/journal.jsonl`), `evals/run_eval.py`, `agents/tools.py` and a cached validation of an earlier run (`evals/cache/*122_gemm_blocked_b*`: "FAIL: mismatch at case 0"). Scan of every GLM v5 arm (L0, L1, L2, A, B, C, E so far): 3 contaminated units, all C, all PASS: 122_b (above), 122_d (`ls /private/tmp/claude-501/`, a timed-out `grep -r ~/.claude` with partial output), 122_e (a timed-out `grep -r ~/.claude`, `ls -d ~/.claude`). Every other hit was the zig stdlib, which lived in a scratch dir on 11-16. | Under eval the agent may not read any checkout's `evals/` (whole), `tests/`, `agents/logs/`, the git dir, the same trees in other clones (pattern), `~/.claude`, or `/tmp/claude-<uid>`; `/tmp/eval*`, `$TMPDIR` and the toolchains on PATH stay readable. File tools refuse by path; macOS bash gets a sandbox-exec read-deny plus a note naming what is sealed. `evals/audit/integrity_scan.py` scans an arm's transcripts per unit and marks reads CONTAMINATED. | ADOPTED (#195; macOS kernel-enforced, Linux by path and command text only). C's 122_b/d/e verdicts are reported as contaminated |
| G26 | **122's lint read comments:** "may not use 'dlopen'" matched `// no BLAS, no dlopen` and failed a correct C++ solution (arm E 122_c; the final file replays PASS under the fix). All five variants shared the lint, and the zig/rust rules (`extern`) had the same hole. | The lint ignores comments, matches the loader/BLAS words with string contents blanked, keeps strings for the language rules (`import "syscall"`, `extern "C"`), and keeps Go's `//go:` / `#cgo` directives. The references open with such a comment; 122 a-e re-proven (PROVEN). | ADOPTED (#195; the 122 task hash moves) |
| G27 | **The agent's `python3` was not the validators'.** The tool shell's `python3` was anaconda 3.9.13; validators run `OPENBEAST_EVAL_PY` (3.12). B 173 spent its first turns finding a 3.11+; E's env block printed `python3: Python 3.9.13` in every unit. | Under eval the bash tool puts a private shim dir first on PATH: `python`, `python3`, `python3.X` exec `OPENBEAST_EVAL_PY` (no `pip`); the venv and shim are write-denied on macOS; the env block probes through the same PATH. Outside eval nothing changes. | ADOPTED (#195; era move via agents/tools.py) |

## Eval host provisioning: the venv incident (2026-11-16) — ADOPTED

**What happened.** The v5 runs of 2026-11-16 used a Python venv and toolchains
(zig 0.16.0, rustup/cargo with rustc 1.100) hand-placed in a session scratch
directory. At 16:46 something rewrote that venv's `python` symlink. Every
agent is `sys.executable agents/runner.py`, so from then on each new agent died
at interpreter startup ("Could not find platform independent libraries"),
before a single model call. The harness had no idea: exit 1, zero tokens, the
fixture untouched, validator FAIL. **22 units were scored as model failures**
until a human read the logs. Durable copies were then made by hand under
`~/.local/opt` (a homebrew-3.12.7 venv from a pip freeze, zig, rustup, cargo, an
env file), which fixed that run and nothing else.

**What it taught.** Two separate holes. (1) The harness host's toolchains
were nobody's: no pin file, no installer, no check beyond profile.py's
`which`. A venv is only as durable as its base interpreter, and a Homebrew
python can move under it at any `brew upgrade`. (2) An interpreter that
cannot start produced rows that looked exactly like a model that tried and
failed.

**The fix** (`evals/toolchains.lock`, `scripts/eval-host.sh`,
`evals/eval_host.py`):
- The repo owns the harness host. The lock pins zig 0.16.0, rustc 1.100.0,
  go 1.23.3 (what the units were proven with) and a python 3.12 eval venv,
  per platform, by sha256. `setup` installs them into
  `~/.local/opt/openbeast-eval` (idempotent, no sudo, ark or mirror offline),
  and the venv's base is a python-build-standalone 3.12.16 **inside the
  prefix**, so no package manager can rewrite it.
- `run_eval` / `profile` / `benchmark_all` check before every live run, set
  up what is missing, and re-exec under the owned venv, so the validator
  pins resolve the provisioned compilers.
- `run_eval` refuses a live run on an interpreter that cannot import its
  stdlib and the runner's packages, and records an agent's startup death as
  `interpreter_error` (an infra row: never cached, never scored), then
  re-checks the interpreter and aborts the run when it is broken. A broken
  interpreter now costs one unmeasured row, not a column of FAILs.

Measured on the Mac (scratch prefix): a fresh `setup` took 33 s (zig from a
community mirror; ziglang.org itself was serving 5 KB/s, hence `@mirrors`),
a second `setup` did nothing, and `vlib.pin_toolchain` then pinned the
prefix's zig and rustup's `1.100.0-aarch64-apple-darwin` rustc.

## Cross-model ideas (not yet model-specific)

- Iteration budget scaled by task size (xl SWE tasks at 30 turns starve a one-tool-call-per-turn model; 176 note). Must apply to every model equally.
- A "test before done" discipline in the runner (G9) may help every model that patches without re-running.
- Whether parallel tool calls per turn (if the model supports them) change the iteration economics.

- Prior art in this repo: [`reviews/DEEPSEEK_HARNESS-2026-10-09.md`](reviews/DEEPSEEK_HARNESS-2026-10-09.md),
  the evaluation of DeepSeek's own agent harness (`dsh`; not integrated). Its "mechanisms
  worth porting" are the vendor's view of what its models need, which makes it the natural
  starting hypothesis set for the DeepSeek v4.1 Flash arm of the paper.

## Per-model vs unified — what to measure

For each candidate: run it as an arm on GLM-5.3-Flash and on at least one other model (Qwen3.8 27B on the rig; DeepSeek v4.1 Flash when it lands). A change that helps one and hurts another is the evidence for per-model knobs; one that helps all belongs in the unified harness.
