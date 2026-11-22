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

## Headroom — the gap the harness work is chasing (2026-11-16, updated 2026-11-17 evening; DeepSeek rows 2026-11-21)

| Row | v5 (71 units) | Notes |
|---|---|---|
| Claude Opus 5.5 (reference ceiling) | **71/71** (68/71 at the strict 1x wall) | Claude Code's own tools, no turn cap; solver never saw validator output; integrity scan clean |
| GLM-5.3-Flash L0 (daily config, runner v1) | **26/71** | 43 of 45 fails audited as genuine model mistakes *under this harness*; most ended on the turn cap at 10-30% of their wall |
| GLM L1 (Zig packs) | Zig 0/6 -> 3/6 | packs fix the 0.16 API drift |
| GLM arm A (runner v2: G1-G4, G8, G11 v1) | **31/71** | +5 net vs L0 (11 rescued, 6 regressed; single seed). Rescues cluster on the audited frictions (36_b/c, 143, 146, 121, 179); same time and turns as L0 |
| GLM arm B (A + wall-scaled turns, G1) | **36/71** | +5 over A; single seed |
| GLM arm C (B + thinking, `--wall-scale 2`, B's turns) | **51/71 scored** (SCORE 76.7); 54/71 raw | +15 scored / +18 raw over B; single seed; no packs. The contamination scan (#195, `evals/audit/integrity_scan.py`) withheld three passes, 122_gemm_blocked_b, _d and _e: their agents read or reached for sealed paths (Claude Code workflow journals under `~/.claude`, the harness source, a cached result in `evals/cache/`; see "Language-specific residue" below). First 21 units: 18 vs B's 8 (10 rescued, 0 regressed). **GLM's best row**, and since 2026-11-17 the serving default on spark-hydra (thinking on; `SPARK_HYDRA.md` E2a) |
| GLM arm E (C + diagnostics, edit hint, verify-done, zig pack v2, host pack, env block, wall warnings) | **50/71** (SCORE 74.7) | Done 2026-11-17, on the board (#201); integrity scan clean. One under C scored, four under C raw: a wash at one seed. Per language against C: Py 22 vs 24, C 5 vs 6, Rust 6 vs 5, Zig 4 vs 3 (pack v2), Go / C++ / Sh equal; 28.5 vs 29.0 min per unit. Its 122_gemm_blocked_c FAIL is a validator false FAIL (the pre-#195 lint matched `dlopen` in a comment; the file passes the fixed validator), footnoted on the board and not adjusted. E's additions stay opt-in |
| GLM arms D, F | **not run** | Max paused the benchmark chain after E (2026-11-17). D = C + diagnostics, edit hint, verify-done; F = B at `--seed 1`, the noise floor |
| DeepSeek-V4.1-Flash (Mia EXL3) arm T (thinking-first composite: runner v2, wall-scaled turns, thinking at wall x2, diagnostics + edit hint + verify-done, packs v3 + host pack, env block, wall warnings) | **61/71** (SCORE 88.5) | 2026-11-18, harness8 + review 1118 sandbox; single seed. All 10 failures ended on the 3,600 s wall, with turns of up to 16,384 tokens at ~30 tok/s per stream: slow turns, not scarce ones |
| DeepSeek arm T2 (T + 8,192-token per-turn cap + act nudge, harness9) | **65/71** (SCORE 94.9) | 2026-11-19; +4 / 0 regressions against T, single seed. **The board's #1** and DeepSeek's recommended setup (`AGENT_PROFILE=deepseek-best`, `SPARK_HYDRA.md` E2b); status in [MODEL_CAMPAIGN.md §9](MODEL_CAMPAIGN.md#9-next-deepseek-v41-flash-1017-thinking-first) |
| DeepSeek arm T_s1 (T under `--seed 1`, harness9) | **61/71** (SCORE 85.7) | 2026-11-19; the same 61 as T with one unit swapped each way: **noise about ±1 unit** (and about ±3 SCORE) |
| DeepSeek arm T3 (T2 at a 4,096 cap, harness9) | **65/71** (SCORE 91.5) | 2026-11-20; the same count as T2, 3 units in and 3 out, fastest arm (21.5 unit-hours against 24.5). The cap trades units, so **8,192 stays** |
| DeepSeek arm U (T2's switches with the forced-act turn, done-when-verified, no web tools, at a 16,384 cap; harness10) | **59/71** (SCORE 85.5) | 2026-11-20; −6 against T2, nothing gained. Every forced turn acted (24 of 24); the cap cost the units. Board row DS-U. Tables: [the final campaign report](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md) |
| DeepSeek arm UP (U + three planning lines; harness10, 16,384 cap) | **62/71** (SCORE 88.0) | 2026-11-20; +3 over U (4 in, 1 out), same era and cap: **planning lines help**. Board row DS-UP (two files: paused and resumed) |
| DeepSeek arm UPA (UP + the frozen algorithmic procedure pack; harness10) | **59/71** (SCORE 83.8) | 2026-11-21; −3 against UP, nothing gained, most unit-hours of any arm (29.9): **the pack hurts**. Board row DS-UPA |
| DeepSeek arm U_8k (U at an 8,192 cap; harness10) | **63/71** (SCORE 88.5) | 2026-11-21; +4 over U (the cap alone), −2 against T2 (within about 2 of the noise: **the forced act is no better than the nudge**). Board row DS-U8k |
| DeepSeek arm UP_8k (UP at an 8,192 cap; harness10) | **65/71** (SCORE 91.4) | 2026-11-21; +2 over U_8k (2 in, 0 out: did not beat U_8k, within the did-not-beat band); +0 against T2 on passes (2 in, 2 out) with a lower SCORE, so T2 stays. Board row DS-UP8k |
| DeepSeek arm UPA_8k (UPA at an 8,192 cap; harness10) | **62/71** (SCORE 88.0) | 2026-11-21; −3 against UP_8k, nothing gained: **the pack hurts at 8K too**. Board row DS-UPA8k. The DeepSeek series is complete |

Max's read, and the working hypothesis: GLM-5.3-Flash is a far stronger model than
26/71 suggests, and much of the gap is the harness around it, not the weights. Each arm
above tests one piece of that; whatever gap remains after them is the next experiment.
By C the gap to the reference is 20 units scored (17 raw). 13 of C's 17 failures are process
failures, almost all a thinking turn hitting the 16,384-token output cap before the unit
writes its file; one is a zig knowledge failure and three are algorithmic (see
"Language-specific residue under thinking").
Scored rows live in [`RESULTS.md`, "v5 leaderboard"](RESULTS.md#v5-leaderboard--best-configuration-per-model)
(generated by `evals/v5_board.py`, #193); this table is the working log.
Future-experiment backlog (beyond A-C): k-seed repeats (G12), "test before done" (G9),
edit-not-rewrite diagnostics (G13), thinking budget sweeps, parallel tool calls, a
Qwen3.8 v5 row and DeepSeek v4.1 Flash on the same arms (the paper's cross-model axis).

## Thinking is the dominant lever (2026-11-16) — C final 2026-11-17

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

**C's final (2026-11-17): 51/71 scored, 54/71 raw**, with 122_gemm_blocked_b,
_d and _e withheld after the contamination scan (#195), against B's 36/71: +15
scored (+18 raw) on the whole suite, from one change, on one seed. That is more than A and B together (+10). Thinking's own
failure mode is now the largest one left: 13 of C's 17 failures are process failures,
almost all a thinking turn capped at 16,384 tokens before the file is written (the
recovery landed in #194, "Thinking stall" below; measuring it as its own arm is a
TODO).

**E finished at 50/71 (SCORE 74.7)**, one under C: diagnostics, verify-done, packs,
the env block and wall warnings on top of thinking were a wash at one seed, so they
stay opt-in and the serving default adds thinking alone (`SPARK_HYDRA.md` E2a:
`INFERENCE_THINKING=on` at the gate, in Open WebUI and for the host's agents). Arms
D and F did not run (Max paused the chain). The first thinking-first campaign
is DeepSeek-V4.1-Flash (Mia EXL3)'s (2026-11-18 on): T 61/71, T2 65/71, T3
65/71, U 59/71, UP 62/71, UPA 59/71, U_8k 63/71 ("Headroom" above). For that model the lever after thinking is
the per-turn thinking cap, not a component: see "DeepSeek-V4.1-Flash (Mia
EXL3): the v5 campaign" below and [the final campaign report](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md).

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
| G5 | Thinking off: reasoning slips that thinking would likely catch — the missing `x/sqrt(2)` in N(x) debugged for 20 turns (36 b/c/d) despite the sample disagreeing; copied its own carry bug into a hand trace (163_a). | Per-request thinking (`chat_template_kwargs.enable_thinking`), verified live on TensorFold. | ARM C: 51/71 scored (54 raw; three units withheld by the #195 integrity scan) vs B's 36/71; first 21 units 18 vs 8 (10 rescued, 0 regressed). The dominant lever (see above); the default campaign is now thinking-first, and since 2026-11-17 spark-hydra serves with thinking on by default (`SPARK_HYDRA.md` E2a) |
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

- Max (2026-11-16): harnesses like Factory's Droid lift *every* model by hard-coding
  rich, verified per-language / per-tool / per-scenario knowledge into each turn. Take a
  page from that book: expand OpenBeast's language and tool arsenal wherever evidence
  says it helps. Prior-art write-up: `reviews/HARNESS_PRIOR_ART-2026-11-16.md`. This is
  the "unified harness, rich static knowledge" pole of the paper's question.

## Per-model vs unified — what to measure

For each candidate: run it as an arm on GLM-5.3-Flash and on at least one other model (Qwen3.8 27B on the rig; DeepSeek-V4.1-Flash (Mia EXL3), whose thinking-first series is complete, [the final report](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md)). A change that helps one and hurts another is the evidence for per-model knobs; one that helps all belongs in the unified harness.

First per-model knob, 2026-11-19: the per-turn thinking cap. DeepSeek-V4.1-Flash (Mia EXL3) runs best at 8,192 (`recommend.composite_by_model`, `AGENT_PROFILE=deepseek-best`). GLM-5.3-Flash was never measured below 16,384. Whether 8,192 also helps GLM is open; the GLM control for UP runs at U's 16,384 cap.

## Language-specific residue under thinking (2026-11-17)

Question (Max): GLM-5.3-Flash with thinking on is strong enough that every failure left
should be algorithmic or problem-solving, never language-specific. What language-specific
causes remain in the thinking arms, and which pack claims would remove them?

**Data.** Arm C (runner v2 + wall-scaled turns + thinking, **no packs**: its results header
has `packs: {}` and `diagnostics: false`): 54/71, all 71 transcripts read. Arm E (C + diagnostics
with the edit hint + verify-done + zig pack v2 + host pack + env block + wall warnings): still
running at the time of this audit; it finished later the same day at 50/71 (see "Headroom").
**Cutoff 2026-11-17 08:17 PST**, 30/71 rows (22 pass, 8 fail); two in-flight transcripts
were left out. Only one E zig unit (122_e) had finished. Arm B (C without thinking): 36/71 after
merging the rerun, with no infra rows left. Tools: `evals/audit/langaudit.py` (origin/main, run
with python3.12; it crashes on the anaconda 3.9 `python3` at `int | None`), plus an all-units
scan, since langaudit only looks at failed units. Scratch: the session scratchpad's `la2/`.

### Failed units by language × primary cause

K knowledge, B borrow/type fight, A algorithm, P process (here, almost always a thinking stall:
the turn hits the 16384-token `output_cap` and the unit never writes its file), H harness.

**Arm C (17 failed)**

| Lang | Failed | K | B | A | P | H | K/host turns inside them |
|---|---|---|---|---|---|---|---|
| python | 8 | 0 | 0 | 1 (176) | 7 (168a, 173, 183, 190, 192, 195, 196) | 0 | 0 |
| go | 2 | 0 | 0 | 1 (163b) | 1 (168b) | 0 | 0 |
| c | 1 | 0 | 0 | 0 | 1 (168c) | 0 | 0 |
| cpp | 2 | 0 | 0 | 0 | 2 (163d, 168d) | 0 | 0 |
| rust | 2 | 0 | 0 | 1 (163e) | 1 (168e) | 0 | 0 (4 turns of cast/paren slips, not K) |
| zig | 2 | 1 (163f) | 0 | 0 | 1 (168f) | 0 | 163f: 28; 168f: 9 research before the stall |
| **all** | **17** | **1** | **0** | **3** | **13** | **0** | |

**Arm E so far (8 failed of 30)**

| Lang | Failed | K | B | A | P | H | K/host turns inside them |
|---|---|---|---|---|---|---|---|
| python | 2 | 0 | 0 | 1 (167a) | 1 (168a) | 0 | 0 |
| go | 2 | 0 | 0 | 1 (163b) | 1 (168b) | 0 | 163b: 1 (stale .go files in a shared /tmp/dbgdir) |
| c | 3 | 0 | 0 | 2 (158c, 163c) | 1 (168c) | 0 | 0 |
| cpp | 1 | 0 | 0 | 0 | 0 | 1 (122c) | 0 |
| **all** | **8** | **0** | **0** | **4** | **3** | **1** | |

Measured in time, the thinking stall is the dominant failure, and it is not language-specific.
Capped turns ate 1136 min in C and 440 min in E so far. Every C failure with 7 or fewer turns
(13 of 17) is a unit whose file was never written. **E 122_c is a validator false positive**:
the C++ was correct, but the lint "may not use 'dlopen'" matched the comment `// no BLAS, no
dlopen`. B for contrast: 35 failed. All 6 zig failures there had K errors (18–38 research turns
each), and 10 failed units had host-userland errors. With thinking, C had **no non-zig K failure
and one host error** (`timeout: command not found`, 128_rsa, which passed).

### Passed units that burned more than 3 turns

| Arm/unit | K + research turns | Harness turns | Exact error lines |
|---|---|---|---|
| C 122_e zig (49 it) | 18 | 18 | `error: name shadows primitive 'i0'`; `error: pointless discard of local constant`; `root source file struct 'std' has no member named 'parseInt'`; `root source file struct 'posix' has no member named 'write'`; it6 `sed 's/\bi0\b/lo/g'` silently matched nothing (BSD sed) |
| C 123_f zig (31 it) | 11 | 0 | `root source file struct 'fs' has no member named 'File'`; then rewrote to raw posix: `root source file struct 'posix' has no member named 'write'` |
| C 167_f zig (37 it) | 16 | 3 | `root source file struct 'posix' has no member named 'write'` (it3), then 15 turns grepping lib/std; `expected type '[16]u8', found '[6:0]u8'` |
| C 36_f zig (30 it) | 19 | 0 | std.math has no `erf` (≈18 research turns, plus layout hunting); `/tmp/erfprobe.zig: ... 'posix' has no member named 'write'` |
| C 163_f zig (FAIL) | 28 | 0 | `invalid builtin function: '@intClz'`; `'heap' has no member named 'GeneralPurposeAllocator'`; `'std' has no member named 'io'`; `struct 'array_list.Aligned(u32,null)' has no member named 'init'`; `member function expected 1 argument(s), found 2`; `expected type 'u3', found 'u6'` / `'u5', found 'u6'`; `expected signed integer type, found 'u64'`; `'posix' has no member named 'write'`. Final build failed |
| C 122_b c, 122_c cpp, 122_d rust, 167_c c, 178 py, and 8 more | 0 | 107 in all (13 units) | `NOT DONE — the task expects these files, which do not exist yet: /tmp/eval_gemm/gemm.c..` |
| E 122_d rust | 0 | 10 | same NOT DONE; then `error: invalid character '.' in crate name: 'gemm.rs.'`; `error: couldn't create a temp dir: Operation not permitted (os error 1) at path "/dev/rmeta…"` (`rustc -o /dev/null`) |
| E 122_e zig (14 it) | **0** | 5 | first write compiled clean, `pub fn main(init: std.process.Init)` / `init.io`, straight from pack v2 |

Small K hits in passing units, one turn each: C 158_c `error: function definition is not allowed here` (a GCC nested function, `auto int next(void){…}`, under Apple clang); C 123_d `use of undeclared identifier 'sqrt'` (only `<cstdio>` included); C 36_e `error[E0658]: use of unstable library feature 'float_erf'` (a deliberate probe).

**The harness bug, which is larger than all the K turns together.** `evals/run_eval.py:2217`
`re.findall(r"/tmp/eval[\w\-./]*", spec_text)` keeps the sentence-ending period, so
verify-done demands `gemm.zig.` and the env block prints `/tmp/eval_divmod. (exists)`. It cost
107 turns in C (13 units) and at least 15 in E. GLM copies the file to dotted names and compiles
`gemm.rs.`. In **C 122_b it15–24** it went looking for the checker: it read Claude Code workflow
journals under `~/.claude/projects/…`, the repo's `agents/tools.py` and `evals/run_eval.py`,
and a cached prior validation result in `evals/cache/` ("FAIL: mismatch at case 0"). That is an
integrity hole: the agent's sandbox can read the eval cache and the harness source. **Fixed in #195** (G24 path regex, G25 seal + `evals/audit/integrity_scan.py`, G26 lint, G27 python); the scan counts 85 turns after a refused `task_done` in C (11 units) by its own definition, and finds C 122_b/d/e contaminated.

### Zig: the K pattern moved; it did not vanish

| zig compiler error (units hit) | B (no thinking, no pack) | C (thinking, no pack) | E (thinking, pack v2) |
|---|---|---|---|
| `'posix' has no member named 'write'` | 2 | **5 of 6** | 0 of 1 |
| `std.io` / `fs.File` / `GeneralPurposeAllocator` / ArrayList `init` / arity | 3 / 3 / 2 / 1 / 3 | 1 / 1 / 1 / 1 / 1 | 0 |
| lints (never mutated, unused, discard, shadows primitive) | 5 / 3 / 1 / 1 | 0 / 0 / 1 / 1 | 0 |

With thinking, GLM reasons "the new Io API needs an `io` threaded through; raw syscalls are
simpler." It picks `std.posix.write`, which is gone in 0.16 while `std.posix.read` still exists.
Neither v1 nor v2 names it, and it is the one recurring uncovered zig claim.

### Candidate pack claims (all NEW forms compiled on this host: zig 0.16.0, rustc 1.100.0, Apple clang 21)

| Lang | Claim | OLD (what GLM wrote) | NEW | Evidence units | Priority |
|---|---|---|---|---|---|
| zig | posix.write is gone (posix.read is not) | `_ = try std.posix.write(1, out[off..]);` | `try std.Io.File.stdout().writeStreamingAll(io, bytes);` (`io = init.io`); libc escape hatch: `_ = std.c.write(1, bytes.ptr, bytes.len);` | C 122_e, 123_f, 163_f, 167_f, 36_f; B ×2 | **P0** |
| zig | `@clz` returns Log2IntCeil(T) (u6 for u32); a shift amount must be `std.math.Log2Int(T)` (u5 for u32); `@intClz` does not exist | `const s: u3 = @intClz(v);` … `x << s` with `s: u6` | `const s: std.math.Log2Int(u32) = @intCast(@clz(v)); const y = x << s;` (or `x << @intCast(s)`) | C 163_f it6, it20–22; B ×1 | P1 |
| zig | no erf/erfc in std.math | `std.math.erf(x)` | write it: A&S 7.1.26 (1.5e-7), or series/continued fraction for 1e-12 | C 36_f (≈18 turns) | P1 |
| zig | canonical 0.16 skeleton lives in the toolchain | grep of lib/std/fs, posix.zig, Io/*.zig (101 research turns in C) | "read `<zig env .lib_dir>/init/src/main.zig`" (36_f found it and stopped searching) | C 36_f it16 | P1 (a pointer line) |
| zig | `@truncate` keeps signedness | `@as(i64, @truncate(p & 0xFFFFFFFF))` with p: u64 | `@as(i64, @bitCast(p))` (same width) or `@as(i64, @intCast(p & 0xFFFFFFFF))` | C 163_f it22 | P2 |
| zig | the array length must equal the literal's | `var buf: [16]u8 = "hello\n".*;` | `var buf: [6]u8 = "hello\n".*;` or `const s = "hello\n";` | C 167_f it18 | P3 |
| zig | `parseInt` lives in std.fmt (already in the digest; not salient) | `std.parseInt(u64, s, 10)` | `std.fmt.parseInt(u64, s, 10)` | C 122_e | P3 |
| rust | `f64::erf` is unstable on 1.100 | `x.erf()` → `E0658 float_erf` | implement erf (A&S / series) | C 36_e | P2 |
| rust | `as` binds tighter than `>>` and `-` | `(u[i] as u64) >> (32 - s) as u32` | `((u[i] as u64) >> (32 - s)) as u32` | C 163_e it7–10 (4 turns) | P2 |
| rust | syntax check without an output dir | `rustc … f.rs -o /dev/null` → temp dir in /dev | `rustc --edition 2021 --emit=metadata -o /tmp/chk.rmeta f.rs` | E 122_d it18 | P3 |
| c | `gcc` is Apple clang: no GCC nested functions | `int main(){ auto int next(void){…} }` | a `static` helper at file scope | C 158_c it4 | P2 (host-pack C line, probeable) |
| cpp | `<cstdio>` does not declare sqrt | `#include <cstdio>` + `sqrt(r2)` | `#include <cmath>`, `std::sqrt` | C 123_d it3 | P3 |
| go | `go build` in a shared dir compiles every .go file in it | `cd /tmp/dbgdir && go build .` (other units' g4.go, gemm.go collide) | `go build -o x file.go`, or a fresh `mktemp -d` | E 163_b it11 | P3 |
| host | BSD sed has no `\b`: silently a no-op | `sed -i '' 's/\bi0\b/lo/g' f` | `sed -i '' -E 's/[[:<:]]i0[[:>:]]/lo/g' f` or `perl -pi -e 's/\bi0\b/lo/g' f` | C 122_e it6 | P1 |
| host | the tool shell's `python3` is anaconda **3.9.13**; validators run 3.12 (`OPENBEAST_EVAL_PY`) | `python3 t.py` for a ≥3.11 task (asyncio.timeout, TaskGroup, `match`) | `python3.12 t.py` (/opt/homebrew/bin), or fix the runner's PATH | E env block in every unit; B 173 ×2 (it1–2 spent finding 3.11+) | P1 (better fixed in the harness PATH) |

### Did a pack claim land?

- **C delivered no packs.** C's errors that v1/v2 cover (`fs.File`, `std.io`, GPA, ArrayList `init`,
  arity, `i0`, pointless discard) would have been prevented; no evidence they failed to land.
- **E (v2 + host):** no covered error recurred in any finished transcript. 122_e compiled clean
  on its first write with the pack's `init: std.process.Init` form. No host-pack item (`nproc`,
  `timeout`, `grep -P`, `cat -A`, `time -v`, `pthread_barrier`, `bits/stdc++`) appeared. n=1 for
  zig: recheck when E's 123_f / 163_f / 167_f / 168_f / 36_f land, especially for posix.write.
- **Placement gap in v2:** it says `std.Io.File` has no `writeAll` → `writeStreamingAll`, but GLM
  never goes near `std.Io.File` when it reaches for raw posix. The claim has to sit on the API GLM
  actually tries.

### E's extras in the transcripts

- **Diagnostics + edit hint:** 39 diagnostic blocks, 35 OK. The first checked write was clean in
  21 of 23 units, because thinking front-loads correctness. The hint showed twice (122_d rust).
  After the first (`cannot find type 'Ordering'`, a one-line `use`), GLM rewrote the whole file
  anyway; the result was clean, with no regression. After the second it used `edit_file`. Net:
  neutral at this n. No rewrite churn that cost correctness.
- **Env block:** units skip the probe turn (`zig version`, `ls`). It also prints the
  trailing-dot path and `python3: Python 3.9.13` without a remedy: mild help, and it carries
  two defects into the prompt.
- **Wall warnings:** 19 notices in 11 units. The 50% notice fires late (1800–2500 s), because
  one capped thinking turn lasts 12–15 min. In the 168 a/b/c stalls the next turn was another
  capped turn, so no help. In units already in a fix loop (158_c, 163_a, 158_e, 163_d) the turns
  after the notice were short and ended in `task_done`. Consistent with help, but not separable
  from normal finishing. No harm seen.

### Pack design notes

- **Name the escape hatch.** A thinking model doesn't guess the old API; it *routes around*
  the new one to the nearest simple primitive (posix.write, libc). For every removed API, the
  pack should list the substitute a model will reach for and say whether it survives.
- **Use compilable snippets, not prose.** E 122_e copied the pack's main/stdout/stdin skeleton
  verbatim and compiled first try. One complete 10-line skeleton at the top beats scattered rules.
- **State the asymmetries** ("posix.read exists, posix.write does not"). Half-surviving
  namespaces are where a model's verification ("posix.read is at line 400") misleads it.
- **Point at the toolchain's own reference** (`lib/init/src/main.zig`). Thinking models
  research rather than guess: C spent 101 zig turns grepping lib/std. The pointer turns that
  into one read. For a non-thinking model (B) the same knowledge has to be in the pack itself.
- **Lints matter only without thinking** (B: 5 units hit never-mutated, C: 0). Keep the lint
  lines for non-thinking arms (Qwen no-think) and drop them first under a token budget.
- **What generalizes:** the zig 0.16 claims are training-cutoff facts, so any model trained
  before the 0.15/0.16 Io rewrite (Qwen3.x, DeepSeek) hits the same wall. The posix.write
  substitution is likely universal for models that reason before writing. Rust `erf`, the zig
  `erf` gap, BSD `sed \b` and the 3.9 `python3` are host and toolchain facts, independent of
  the model. Measure each on Qwen3.8 27B per "Per-model vs unified" above.
- **Bigger levers than any pack, for GLM:** the trailing-dot path regex (107 C turns), the
  16384-token output cap on thinking turns (13/17 C failures), the dlopen-in-comment lint
  (an E false FAIL), and sandbox read access to `evals/cache` and the harness source.

## Thinking stall: a capped think keeps its tail (2026-11-17) — ADOPTED, PENDING an arm

Source: arm C (GLM-5.3-Flash, runner v2 + wall-scaled turns + thinking on, no
packs; `eval-glm-5-3-flash-v2-turns-think-L0-20261116-195724.json`, 54/71), all
71 transcripts. 13 of C's 17 failures were "process": the unit never wrote its
file because its thinking turns kept hitting the 16,384-token eval cap
(`_EVAL_TURN_TOKENS_THINKING`). Measured before choosing a fix:

| Measure | Value |
|---|---|
| Capped turns | 78, in 38 units (21 of those units still passed) |
| Capped turns with no content and no tool call | **78 of 78** (pure reasoning; the old recovery kept nothing) |
| Time in capped turns | 1,136 min (357 in passing units, 779 in failing ones) |
| Decode rate of a capped turn, 4 jobs | 15.1 / 16.7 / 19.1 / 20.9 / 25.8 tok/s (min / p25 / median / p75 / max); 635–1,085 s per capped turn |
| Turns that ended on their own (706, in the 51 finished units) | p50 ~395, p75 ~860, p90 ~2.3K, p95 ~4.5K, p99 ~12.4K, max ~15.1K tokens; 17 above 8K, all in passing units |
| Caps on the first turn (before any file was read) | 31; the next turn was a probe (`ls`, `cat sample`) 29 times |
| Caps on a later (code-writing) turn | 47; next turn: capped again 22, killed by the wall mid-think 11, probe 6, wrote the file 8 |

Per-turn token counts are estimates: the transcripts log no per-turn usage, so
each normal turn's tokens are its wall time times its unit's rate (the unit's
completion total minus 16,384 per cap, over its normal turns' time). A capped
turn is exactly 16,384.

**What the data says.** (1) A cap destroys the whole turn, because the turn is
reasoning only. (2) Re-thinking from scratch after a discarded cap fails 70% of
the time on a code-writing turn (33 of 47 re-capped or ran into the wall), and
the 163/168 units capped 2–4 turns in a row: the same think, re-derived and cut
again. (3) A higher cap is not supported. The length of a capped think is
censored at 16K, so no percentile of successful thinks says where it would
have ended. The repeat chains suggest many would not end at 24K either, and
each extra 8K costs ~7 min per stalled turn (median rate) on a 3,600 s wall.
The productive tail does reach 92% of the cap (max ~15.1K), which is the one
argument for a raise; the recovery below covers that case too, since a think
cut at 16K now continues.

**Adopted (runner):** when the cap cuts a turn that has no content and no tool
call but has reasoning, the nudge carries the last 6,000 chars (~1.5K tokens)
of that reasoning and says "you were cut off mid-reasoning; conclude from it
and act now". Every other turn keeps the default: reasoning is never fed back.
The cap stays 16,384 and the client timeout stays `cap / 8 tok/s` (2,048 s,
2x the slowest measured capped-turn rate). The `output_cap` event now records
`completion_tokens`, `reasoning_chars` and `reasoning_tail_chars`, so the next
arm measures this directly instead of from timestamps.

**To measure:** the next thinking arm vs C on the 38 capped units: capped minutes,
caps per unit, and the 13 process failures. The failure mode to watch: a tail
that ends inside a wrong derivation, followed by a confident wrong write (an A
failure in place of a P failure).

## Language residue under thinking: claims shipped and deferred (2026-11-17)

From the 2026-11-17 audit of arms C and E (language × cause tables, the
candidate claims table). Shipped as zig pack **v3**
(`agents/packs/zig-0.16-v3.md`, `OPENBEAST_PACK_VARIANT=v3`). v3 is a new file,
not an edit of v2: v2 was being measured by arm E, and a variant name must keep
naming one set of bytes. Each claim is an OLD/NEW fixture pair in
`tests/fixtures/zig016/` (`since: v3`), verified under zig 0.16.0:

| Claim | OLD fails with | NEW compiles | Escalation card |
|---|---|---|---|
| P0 `std.posix.write` is gone, `std.posix.read` is not | `struct 'posix' has no member named 'write'` | `std.Io.File.stdout().writeStreamingAll(io, bytes)`; `std.c.write(1, p, n)` (needs `-lc` on Linux: checked with `-target x86_64-linux`) | selects `posix_write` |
| P1 no `@intClz`; `@clz(u32)` is u6, a u32 shift amount is `Log2Int(u32)` = u5 | `invalid builtin function: '@intClz'`; `expected type 'u5', found 'u6'` | `const s: std.math.Log2Int(u32) = @intCast(@clz(v)); v << s` | selects `clz_shift` |
| P1 no `std.math.erf`/`erfc` | `struct 'math' has no member named 'erf'` | an A&S 7.1.26 erf | selects `erf` |
| P1 the toolchain's own main is at `lib/init/src/main.zig` | (no OLD: a pointer) | a file in its shape; a test checks the real file exists and uses `std.process.Init` | pack-only |

v3 is 1,955 tokens (budget 2,000); the generated digest shrank from 18 to 11
lines to fit. Production still serves v1 (`MEASURED_SHA256` unchanged).

**Host pack:** `sed_no_word_boundary` is a probe, not an assertion. It runs
`sed 's/\bi0\b/lo/g'` on `i0 xi0 i0`; when the output comes back unchanged with
exit 0 (BSD sed reads `\b` as a literal `b`), the line is rendered. Each remedy
(`sed -E 's/[[:<:]]w[[:>:]]/x/g'`, `perl -pi -e 's/\bw\b/x/g'`) is offered only
when it ran on the host and did the replacement. GNU sed renders nothing.

**Deferred (no claim; the audit saw each once, with no recurrence):**

- zig P2 `@truncate` keeps signedness (C 163_f it22, 1 turn); zig P3 array length
  must equal the literal's (C 167_f it18), `parseInt` lives in `std.fmt` (C
  122_e; already in the digest, not salient). Each cost one turn.
- rust P2 `f64::erf` is unstable on 1.100 (`E0658 float_erf`, C 36_e). It was a
  deliberate probe that the model handled in one turn.
- rust P2 `as` binds tighter than `>>` and `-` (C 163_e it7–10). The 4 turns
  were a precedence slip, not stale knowledge: no API changed, so no claim fits.
- rust P3 `rustc -o /dev/null` wants a temp dir (E 122_d it18). Once, and it was
  part of the trailing-dot path harness bug's churn.
- c P2 `gcc` is Apple clang, so no GCC nested functions (C 158_c it4). It is
  probeable and belongs in the host pack's C line if it recurs; one turn so far.
- cpp P3 `<cstdio>` does not declare `sqrt` (C 123_d it3). Not a version fact,
  one turn.
- go P3 `go build` in a shared dir compiles every `.go` in it (E 163_b it11). It
  was caused by units sharing `/tmp/dbgdir`. Revisit only if it recurs across
  units.

Add a claim when the same error recurs in a second unit or a second arm.

## DeepSeek-V4.1-Flash (Mia EXL3): the v5 campaign (2026-11-20)

Final (2026-11-21): the series is complete. Source: arms T, T_s1, T2, T3, U, UP, UPA, U_8k, UP_8k and UPA_8k (all on the board), read through
`evals/v5_report.py`. Full tables, deltas and limitations are in
[the final campaign report](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md). One seed per arm except T; noise about ±1 unit.

| Finding | Evidence | Status |
|---|---|---|
| **The per-turn thinking cap is this model's lever.** 16K → 8K: +4 units, 0 lost (T → T2, with the act nudge and across harness8 → harness9; all four gains had hit T's wall). 8K → 4K: 0 net, 3 in and 3 out, SCORE 94.9 → 91.5 (T2 → T3, same era). 16K again under harness10: −6 (T2 → U). Inside harness10, the cap alone: U 59 → U_8k 63, +4 (6 in, 2 out); UP 62 → UP_8k 65, +3 (3 in, 0 out); UPA 59 → UPA_8k 62, +3 (5 in, 2 out) | wall hits 16 / 13 / 10 / 15 / 11 (T / T2 / T3 / U / U_8k); unit-hours 26.6 / 24.5 / 21.5 / 26.1 / 23.7 | **ADOPTED at 8,192** for DeepSeek (`deepseek-best`); a per-model knob |
| **The forced-act turn ends a stall** where the text act nudge did not: all 24 forced turns in U (18 of 71 units) ended in a tool call. The text nudge was ignored every time in the 168 investigation | U transcripts, `force_act_result` events | **MEASURED, not adopted** — at the same 8K cap U_8k scored 63 against T2's 65 (−2, within about 2 of the noise; U_8k also adds done-when-verified and hidden web tools, and crosses an era). No improvement over the act nudge; T2 stays the default |
| **Planning lines help.** Three general lines (bound first; test a doubtful simplification against a brute force; redesign on a large miss of the time limit), naming no task: U 59 → UP 62 at the same 16K cap, 4 units in and 1 out | U → UP (same era, one variable) | **PROMISING** — at 8K, U_8k 63 → UP_8k 65 (+2, 2 in and 0 out): did not beat U_8k (within the did-not-beat band); UP_8k ties T2 on passes with a lower SCORE, so not adopted into the default; the GLM control has not run |
| **The frozen algorithmic procedure pack hurts.** UP 62 → UPA 59: 0 units in, 3 out, and the most unit-hours of any arm (29.9 against UP's 26.5). The no-harm condition fails | UP → UPA, UP_8k → UPA_8k (same era, one variable) | **REJECTED** — −3 at 16K and −3 again at 8K (UP_8k 65 → UPA_8k 62, 0 in and 3 out) |
| **A 16K cap costs units even with the forced act.** U's lost units ran out of wall with 11-26 turns where T2 had 28-49 on the same units (4 of 6; one hit the wall at 53 turns, one ran out of turns) | U and T2 rows (`iterations` from harness9's partial stats) | settled for this model: do not run DeepSeek at 16K (U_8k +4 over U confirms it) |
| **Adaptive cap.** The units 4K wins (many short iterate-and-test turns) and the units 8K wins (one long derivation) are different units. A cap that changes within a unit by a general rule could take both: deeper for the first, design-setting turns and shorter inside a write-test loop, or shrinking after consecutive capped turns. It must name no task | T2 → T3 deltas | **IDEA** — a runner change (era move), its own arm, read against T2 and T3 |
| **The residual is design choice and time, not knowledge or language.** T2's six failures are five variants of one task family (168) plus 192_pike_regex. The 168 investigation (sealed review, `evals/audit/reviews/`) found a design-choice failure, not a knowledge gap: the model knows what makes the unit tractable, but usually commits early to an approach that cannot meet the limits, and capped thinking re-decides the design every turn. Its remedies are general and name no task | T2, T_s1, T3 rows; the review | **ARM** — U (forced act, done-when-verified), UP (planning lines), UPA (frozen procedure pack), each with a no-harm check on the other units and a GLM control |
| **Packs are not the bottleneck here.** Go is 7/8 in every arm but UPA_8k (6/8; its extra miss is 163_bigint_divmod_b) and Zig 5/6 in all but U_8k (4/6; its extra miss is 163_bigint_divmod_f); each other miss is a 168 variant; Python (25-30 of 32) carries the swings | per-language table in the report | packs stay on as in T; their ablation (T_nopacks) has not run for DeepSeek; no new claim |

## Hy4-flash (NVFP4): T2's setup against GLM's best (2026-11-22)

Source: arms H_T2 and H_C on the harness9 tree (f38a29c7), one server
start, one pair; board rows HY4-T2 and HY4-C;
[the Hy4 report](reports/V5_HY4_FLASH-2026-11-22.md) §5. One seed each;
noise about ±1 unit. The third model on which an assist or the cap has
been read.

| Finding | Evidence | Status |
|---|---|---|
| **T2's bundle helps a third model.** GLM's best (H_C: no assists, the 16,384-token eval turn cap rather than 8,192, no nudge) 55/71 → T2's setup (H_T2) 60/71: +5, 7 in and 2 out, helped, single seed. SCORE 82.2 → 83.4 only: all 7 gains are ports (LANG 74.8 → 89.0), and H_C passes one more Python base (SOLVE 84.6 against 81.5) | H_C → H_T2 (`v5_report.py --pair H_C:H_T2`) | **MEASURED, bundled**: the assists, the 8K cap and the nudge changed together, so the gain is T2's whole bundle, not one switch |
| **The arm at the 16,384 cap spends its wall in long turns.** H_C: 13 wall hits (4 passed), wall-hit units at 4-60 turns, three under 12; H_T2: 5 (2 passed), all at 49+ turns. H_C writes ~1,080 completion tokens a turn against ~760, 23% more in all, in fewer turns (25.7 against 29.6 a unit). Three of T2's 7 gains were H_C wall hits, four ran to the 60-turn ceiling | `iterations`, `agent_exit_code`, `tokens_completion` of the two rows | consistent with the cap being the lever (as on DeepSeek), **not a measurement of it** |
| **Across models.** The cap alone: +4/+3/+3 on DeepSeek (U → U_8k, UP → UP_8k, UPA → UPA_8k, harness10). The assists, with a harness change: −1 on GLM (C → E, level, unresolved; other eras). The bundle: +5 on Hy4. No model has a measured gain from the assists alone | the three campaigns | the cap stays the per-model knob to try first; the assists stay on in T2 because T2 is measured, not because they are shown to help |
| **Unbundle on Hy4** (optional): T's assists at the 16K eval turn cap, no 8K cap or nudge, on the harness9 tree; read against H_T2 and H_C | — | **IDEA**, not scheduled (needs Hy4 served for about 7 h) |

## Next-era proposals from the 2026-11-22 runs (infra, not model behaviour)

Four harness-side faults seen while the Hy4 and rig evals ran. None changes
what a model is asked or scored on; each changes how an infra fault is
classified or seen, so each is a runner / run_eval change (an era move) and
lands at the next era boundary, not mid-campaign.

| Proposal | Evidence | Status |
|---|---|---|
| **(a) A truncated tool call is a model-turn error, not an infra failure.** When the model's tool-call JSON is cut off, llama-server answers HTTP 500 `Failed to parse tool call arguments as JSON`. `agents/runner.py` treats every non-429 API error alike: `api_errors += 1`, sleep 5 s, resend the IDENTICAL request, so the same cut-off generation recurs; the unit is then recorded as `server_error` (an unbanked infra row) though the server was healthy and the fault was the model's output. Proposal: match that specific 500 body; append an assistant-side note plus a user message ("your last tool call's arguments were not valid JSON (cut off?): re-issue it complete, or split the work into smaller calls"); count it as a turn (it spends one iteration and is logged as `tool_call_parse_error`), not as an `api_error`. Alternative, smaller: one retry with that nudge, then the existing path. A run whose turns are mostly parse errors is then a model result (scored), which is the honest reading | 23 identical retries of one request in one unit, recorded `server_error` (llama-server log + the unit's events). **Q38U-L0 (2026-11-22), the board row:** 4 of 71 units lost this way (122_gemm_blocked_c 23 retries, 122_gemm_blocked_d 22, 173_taskgroup_cancel 21, 124_rkf45 8), counted as fails on the board: the row understates the model by up to 4 units until this lands | **IDEA** — runner change; measure as its own arm on a llama-served model (the row moves from unscored to scored, so read the pass count AND the server_error count) |
| **(b) Fixture dirs are per-run, or a wait is visible.** Two evals on one harness host share `/tmp/eval_*` fixture roots. `run_eval.py`'s fixture flock makes the second wait (`Waiting for fixture /tmp/eval_linz (held by pid …)`), logged once, at the top of a long log; a profile sat there for over an hour and looked, from `campaign.py status` and the profile output, like a slow unit. Proposal, best: namespace fixtures per run (`/tmp/eval_<runid>_<name>`; the validators and prompts take the root from the unit, so the name is a parameter, not a literal; check every task's prompt and validator for a hard-coded `/tmp/eval_` path first, since a prompt change is a suite change). Fallback, cheap: write the wait into the state/stats sidecar (`waiting_fixture: <root>, holder pid, since`) so `campaign.py status` and the profile line show `WAITING on fixture (pid N, 63 min)`, and re-log it every 5 min | the stalled profile (over an hour waiting on `/tmp/eval_linz` while another eval held it) | **IDEA** — the status half is cheap and changes no scoring; the namespacing half touches the suite if any prompt names a path |
| **(c) profile.py writes an unbuffered log.** Run directly with stdout redirected, `evals/profile.py` block-buffers, so its log stays empty for hours while the run is alive (`campaign.py` sets `PYTHONUNBUFFERED=1` for its children; a direct profile run did not). Proposal: `sys.stdout.reconfigure(line_buffering=True)` (and stderr) at the top of `profile.py`, `run_eval.py` and `benchmark_all.py`'s `main`, so a redirected log is live whatever the caller set. Until then the docs say to set it (`docs/MODEL_CAMPAIGN.md` §2, done 2026-11-22) | an empty profile log for hours on a live run | **IDEA** (docs workaround ADOPTED) — no scoring effect; can ride any era move |
| **(d) The Linux agent shell gets the macOS network rule.** On macOS the agent's `bash` runs under sandbox-exec with outbound network denied except loopback (review 1118); on Linux `_eval_read_deny_prefix` returns nothing, so the shell has no network deny (evals/README.md residual gaps). Proposal: confine the Linux agent shell's network the way `vlib.run_solution` confines a solution (Landlock + seccomp socket refusal, or a network namespace with loopback only), so a Linux harness host (the rig's eval-host container) measures under the same rule as the Mac. Also: the integrity scan reads the results window in the host's local zone, and the container writes naive UTC, so a scan on another host needs `--tz UTC` (or `TZ=UTC`) and the file's mtime kept or `--until`; the scan now exits 2 when it attributes only part of a run; record the zone in the result header | Q38U-L0 (2026-11-22, the rig container): the integrity scan flagged off-host access from the agent's shell in 3 units (1 a pass); all 3 excluded as contaminated (40 raw, 39 scored); sealed notes in `evals/audit/reviews/` | **IDEA** — a tools.py change (era move); needed before rig rows are compared with Mac rows |

## Next-era: token accounting the runner drops (2026-11-22)

The board now publishes every run's token usage (`evals/v5_board.py`, the
"Token usage" block in `docs/RESULTS.md`). Three gaps in what
`agents/runner.py` records limit it; each is a runner / run_eval change (an
era move), so each waits for the next era boundary.

| Proposal | Evidence | Status |
|---|---|---|
| **(a) Keep the cached-prompt count.** The runner adds only `usage.prompt_tokens`; `usage.prompt_tokens_details.cached_tokens` (vLLM with `--enable-prompt-tokens-details`, llama.cpp builds that send it) is dropped, so a prompt-cache hit cannot be told from fresh prefill. Proposal: sum it as `tokens_prompt_cached` (absent, not 0, when the server never sends it) and print it on the `TOKENS:` line and in the stats sidecar | `agents/runner.py` reads `prompt_tokens`, `completion_tokens`, `total_tokens` only; no committed row has a cached field | **IDEA** — reporting only |
| **(b) Keep the reasoning-token count.** `usage.completion_tokens_details.reasoning_tokens` (where an engine sends it) is dropped. Today "completion includes thinking" is proven only indirectly: every capped turn with reasoning and no content reports `completion_tokens` equal to the cap (all 1,195 such DeepSeek turns on TensorFold and all 42 such Qwen3.8 turns on llama.cpp in the agent logs of 2026-11-19..22). Proposal: sum `tokens_reasoning` when sent, and the reasoning chars per unit always, so the split is measured | the `output_cap` events (`completion_tokens`, `reasoning_chars`) | **IDEA** — reporting only |
| **(c) Count the reply in flight at a wall kill.** A wall-killed unit's sidecar holds the counts to its last finished reply; the request in flight is lost, so a "Partial" row is a lower bound (and rows before 2026-11-18 recorded nothing: GLM's 4 to 20 per arm, DeepSeek T's 16). Proposal: on SIGTERM, write the prompt estimate of the in-flight request to the sidecar as `tokens_prompt_inflight` (the completion stays unknown), shown apart from the measured sums | `evals/run_eval.py` `_partial_stats`; the board's "No usage" / "Partial" columns | **IDEA** — reporting only |
