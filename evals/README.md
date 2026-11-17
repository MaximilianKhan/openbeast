# Eval suite

> **Suite version: v5 (current, since 2026-11-15); v5.0 is the shipped set.**
> The suite is the manifest [`suites/v5.json`](suites/v5.json): 71 scored
> units over 34 base tasks in all 12 categories and all six languages,
> profiled in three arms; a full profile is 3.73 reference hours, inside the
> 4-hour budget. Twelve designed families did not pass their adversarial
> review and are the [v5.1 backlog](#v51-backlog-the-12-families-that-did-not-ship).
> See [v5 — the current suite](#v5--the-current-suite). **v4 is frozen**: its
> board rows stay as history in the legacy section, its task files stay in
> `tasks/` so it can still be run and rescored, and nothing is compared across
> the two versions. The distribution tables further down describe the v4 task
> library.
>
> **v4** — 137 tasks / 291 units,
> hardened against the cheats documented in the 2026-07 review. That
> hardening is incomplete: see [Known validator defects (v4)](#known-validator-defects-v4)
> below; v5 closes them. History: [`CHANGELOG.md`](CHANGELOG.md),
> [`../docs/archive/EVAL_V4_PLAN.md`](../docs/archive/EVAL_V4_PLAN.md), and the review that
> drove it, [`../docs/archive/EVAL_REVIEW_2026-07-07.md`](../docs/archive/EVAL_REVIEW_2026-07-07.md).
> The distribution tables below describe **v4**. For the model **leaderboard**
> (nine models on v4; three legacy v3.5 rows kept for history, two of them for
> pruned models), see the
> [main README](../README.md) and [`../docs/RESULTS.md`](../docs/RESULTS.md);
> v3.5 and v4 scores are **not directly comparable** (different task sets).

The task library holds 151 base tasks (320 effective units): the 137 v4 tasks plus the 14 new v5.0 ones. Each task has a
validation script that returns exit 0 on success, non-zero on
failure. The harness runs the agent against every task, scores the result, and
ranks models in a leaderboard keyed by (host_id, model_slug).

The tasks ship their own fixtures and need no network. Under v4 a run was
not sealed: eval agents had live `fetch` and `web_search`, and could read
the task JSON (validator, oracle and all). Since v5 both are closed — see
the oracle seal under [v5](#v5--the-current-suite).

## Known validator defects (v4)

These were held for the next suite version, because fixing a validator or
the harness changes the era and invalidates every cached result. v5.0
closes all four for every unit it ships: 23 and 21 are not in the suite
(their intended replacements, 181, 180, 170 and 171, are in the v5.1
backlog), no shipped unit writes an expected file or `check.py` into the
agent's directory (new and upgraded units hide seeded tests inside the
validator; the v4 tasks kept as defined in v4 are only ones whose fixture
holds no oracle-like file, which the prover checks), and `fetch` /
`web_search` are off under eval.

- **`23_sql_injection` rejects the canonical fix.** The validator flags any
  `+` or f-string inside the `execute(...)` call, including in the parameter
  tuple. `cur.execute("... LIKE ?", ('%' + q + '%',))` fails; the same fix
  with the pattern assigned on the previous line passes, and so does SQL
  concatenated into a variable outside the call. Every v4 board row fails
  this task. It is a hard singleton, so each row carries about −0.68 SCORE
  from it.
- **`21_race_condition` can pass without synchronisation.** The validator
  checks only the final count of 10 threads × 2000 increments. Deleting the
  fixture's `time.sleep(0)` yield, with no lock added, passed 20 of 20
  validations.
- **Variant units expose their expected output.** 132 of the 185 variant
  units write `expected.txt` into the agent's working directory at setup and
  validate with a `diff` against it. A solution that prints the file's
  contents passed 21 of 22 sampled Python variants. Whether any banked
  solution does this has not been audited.
- **Eval agents have live `fetch` and `web_search`.** Agent logs show
  successful fetches of Wikipedia, GitHub and ziglang.org during eval units,
  and `web_search` availability differs between runs with the stack up and
  down. Neither is in the cache key or the result provenance.

```bash
python3 evals/run_eval.py --list                     # list every task file (v4 library + v5)
python3 evals/run_eval.py                            # run the current suite (the v5 manifest) against :8080
python3 evals/run_eval.py --all-tasks                # every task file instead (the v4 rule)
python3 evals/run_eval.py --arms L0,L1,L2 --jobs 4   # all three v5 arms in ONE process (eval-<slug>-<arm>-<ts>.json)
python3 evals/profile.py --budget-h 4 --jobs 4       # a full v5 profile inside 4 h (see the v5 section)
python3 evals/ablation.py --profile evals/results/profile-<slug>-<ts>.json   # the arm contrasts
python3 tests/prove_task.py --unit 163_x_f           # model-free proof that a unit is sound (authoring gate)
python3 evals/run_eval.py --tasks 145,146            # run a subset
python3 evals/run_eval.py --jobs 4                   # 4 parallel workers — needs a server with -np >= 4 (clamped to /props total_slots; MTP configs are -np 1)
python3 evals/run_eval.py --no-cache                 # disable result cache (force live runs)
python3 evals/run_eval.py --cache-only --model-name MODEL  # replay cache only; cache misses → 'skipped_cache_miss'
python3 evals/run_eval.py --suite v4-fast --jobs 4   # v4 pinned fast suite (was v5-fast) — v4 only, imputation-scored, never enters the leaderboard
python3 evals/benchmark_all.py                       # full sweep across configured models
python3 evals/benchmark_all.py --models X --suite v4-fast --jobs 4  # v4 fast comparison sweep (implies --no-leaderboard)
python3 evals/make_fast_suite.py                     # verify the v4-fast pin (identity + drift); --generate to re-pin
python3 evals/benchmark_all.py --cache-only          # replay every model from cache, no server (report only; never seated)
python3 evals/scoring.py --rebuild                   # rescore all eval-*.json files into leaderboard.json
python3 evals/scoring.py --by-category               # per-category drilldown table
python3 evals/scoring.py --by-language               # per-language drilldown
python3 evals/scoring.py --compare-hosts             # side-by-side across machines
python3 evals/cache_cli.py stats                     # cache: entries, total size, context hash
python3 evals/cache_cli.py list --model SLUG         # list cached entries for one model
python3 evals/cache_cli.py clear --model SLUG        # invalidate one model's cached results
python3 evals/tool_efficiency.py                     # per-model tool-use metrics from agents/logs
python3 evals/tool_efficiency.py --since 2026-05-07  # only logs since this date
python3 evals/tool_efficiency.py --model SLUG        # drill into one model's tool-call frequencies
```

A live `benchmark_all.py` sweep asks the GPU lease (`scripts/gpu-lease.sh
check`) before it loads anything: held by someone else, or unreadable, it
refuses (exit 4); free, it re-execs itself under `gpu-lease.sh run` so the
whole sweep holds the card; under a `run` that already wraps it (a
campaign), it proceeds. A sweep in a git worktree asks the main tree's
lease. Every model (re)start asks again.

**Between models the sweep waits for the card to cool, not for a fixed ten
minutes.** The cool-off ends when the hottest GPU reads 50 °C or lower, with
a 60 s floor and the old 600 s as the ceiling; when the temperature cannot
be read it is the fixed 600 s. `OPENBEAST_BENCH_COOLOFF_TEMP_C=<°C>` moves
the threshold and `off` (or `0`) restores the fixed wait. The 50 °C / 60 s
defaults are a judgement, not yet checked against a measured cool-down on
the reference card — set `off` for a sweep whose rows must be thermally
identical to older ones.

## v5 — the current suite

### v5.0: what shipped

**v5.0 is 71 scored units over 34 base tasks, in 12 categories and all six
languages, and a full three-arm profile is 3.73 reference hours: it fits
the 4-hour budget even run sequentially.** It is smaller than the design
(111 units over 39 bases, plus 7 zig probes): every new family went through
up to three adversarial rounds, in which an independent verifier tried to
pass each unit without solving it, and only the families that came out
clean shipped. The other twelve are the [v5.1 backlog](#v51-backlog-the-12-families-that-did-not-ship),
and v4 tasks keep their categories covered.

| Group | Bases | Units | What it is |
|---|---|---|---|
| **New, accepted** (14 families) | 14 | 29 | 163 bigint divmod (Knuth D), 167 Q15 biquad cascade, 168 linearizability checker: 6 languages each. 173 asyncio TaskGroup cancellation, 175 CRDT OR-Set + RGA, 176 intermittent 500s in a 25-file service, 178 codebase Q&A, 179 git bisect + minimal revert (shell), 183 Heston "little trap", 188 GMM EM with log-sum-exp, 190 reverse-mode autograd + MLP, 192 Pike-VM regex, 195 RISC-V routines under an instruction budget, 196 polyphase resampler 44.1 k → 48 k |
| **v4, upgraded** (U3, accepted) | 4 | 18 | 36 Black-Scholes (6L), 123 n-body (6L), 122 blocked GEMM (5L, no Python), 40 attention: seeded hidden tests in the validator, no expected files, no zig tutoring, a vendor-BLAS guard on 122 |
| **v4, kept as defined in v4** | 16 | 24 | 29_db_migration, 37_softmax_stable, 49_markov_steady_state, 107_singleflight, 109_jwt, 117_iter_refinement, 121_quorum_kv, 124_rkf45, 126_hmac_scratch, 128_rsa, 143_uov, 146_aho_corasick, 147_persistent_bst, 153_coroutine_scheduler, 158_karatsuba_bytes (python/go/c/cpp/rust), 159_ntt_convolution (python/go/c/cpp/rust) |

The third group keeps breadth where a family was rejected. Its tasks run
their **v4 validators** with two changes: `ref_stem` / `dest` were added to
their JSON so the prover can find a reference, and the wall-clock gates of
146, 147, 153, 158 and 159 scale with `OPENBEAST_TIMEOUT_SCALE` (review
1116: `el < 1.5` was fixed, so under `--jobs 4` or a loaded host a textbook
solution failed as "too slow"; at scale 1 every gate is the v4 number).
Each one is proven by `tests/prove_task.py` like every other unit. They
replace:

| Rejected | Category | Covered in v5.0 by |
|---|---|---|
| 164 Q4_K quantisation (6L) | LLM / ML | 190 autograd (new), 40 attention (U3), **37_softmax_stable** |
| 165 ChaCha20-Poly1305 (6L) | Security | **126_hmac_scratch** (a primitive from scratch), **128_rsa**, **143_uov** |
| 166 Welford/Chan moments (6L) | Probability & Stats | 188 GMM EM (new), **49_markov_steady_state** |
| 169 broken-build repair (6L) | SWE / DevOps | 176, 178, 179 (new), **29_db_migration** |
| 170 lock-free MPMC ring (4L) | Concurrency & Systems | 173 (new), **153_coroutine_scheduler** (152 Chase-Lev was the closer match, but its validator's fixed limits fail the lint; 153's own fixed wall gate passed only because the lint could not see it, and is scaled since review 1116) |
| 171 Go worker pool race + deadlock | Concurrency & Systems | **107_singleflight** (the same call-deduplication semantics, in Python) |
| 180 vuln hunt | Security | **143_uov**, **128_rsa**, **109_jwt** |
| 181 JWT verifier hardening | Security | **109_jwt** (HS256 sign/verify, padding, expiry) |
| 186 Robertson stiff kinetics | Physics | 123 n-body (U3, 6L), **124_rkf45** (adaptive step control) |
| U1: 117 / 158 / 159 rework | Pure & Abstract Math | the v4 definitions: **117_iter_refinement**, **158_karatsuba_bytes**, **159_ntt_convolution** (their zig variants carry banned tutoring and are out) |
| U2: 71 / 20 / 146 / 147 rework | Algorithms & DS | the v4 definitions of **146_aho_corasick** and **147_persistent_bst**; 71 and 20 are out (every v4 variant writes `expected.txt` into the fixture, which the prover's leak arm refuses), with 163 and 192 (new) carrying the category |
| U4: 143 / 152 / 153 / 121 / 128 rework + 7 zig probes | Security, Concurrency, Distributed | the v4 definitions of **143_uov**, **153_coroutine_scheduler**, **121_quorum_kv**, **128_rsa**; 152 is out (lint); the 7 zig probes are out (their v4 prompts carry the tutoring v5 bans), so v5.0 has no probe units |

Coverage after selection (scored units):

| Category | python | go | c | cpp | rust | zig | shell | Units | R-s h (L0) |
|---|---|---|---|---|---|---|---|---|---|
| Algorithms & DS | 4 | 1 | 1 | 1 | 1 | 1 | · | 9 | 0.39 |
| Concurrency & Systems | 3 | · | · | · | · | · | · | 3 | 0.10 |
| Distributed / SysDesign | 3 | 1 | 1 | 1 | 1 | 1 | · | 8 | 0.36 |
| LLM / ML | 3 | · | · | · | · | · | · | 3 | 0.08 |
| Mathematical Finance | 2 | 1 | 1 | 1 | 1 | 1 | · | 7 | 0.12 |
| Performance & HW Opt | 1 | 1 | 1 | 1 | 1 | 1 | · | 6 | 0.19 |
| Physics | 2 | 1 | 1 | 1 | 1 | 1 | · | 7 | 0.17 |
| Probability & Stats | 2 | · | · | · | · | · | · | 2 | 0.08 |
| Pure & Abstract Math | 3 | 2 | 2 | 2 | 2 | · | · | 11 | 0.36 |
| SWE / DevOps | 3 | · | · | · | · | · | 1 | 4 | 0.22 |
| Security | 4 | · | · | · | · | · | · | 4 | 0.12 |
| Signal Processing & DSP | 2 | 1 | 1 | 1 | 1 | 1 | · | 7 | 0.18 |
| **Total** | **32** | **8** | **8** | **8** | **8** | **6** | **1** | **71** | **2.37** |

Every one of the 72 (category, language) cells was covered in the design;
v5.0 covers 46 of them. All six languages and all 12 categories are
each covered, and the manifest's `coverage_floor` enforces exactly that
(`category_min: 1`, `language_min: 1`, `cell_min: 0`). The empty cells are
the compiled ports of categories whose multi-language family is in the
backlog (Security, SWE / DevOps, Concurrency, LLM / ML, Probability &
Stats, Pure & Abstract Math in zig). Restoring the full grid is the v5.1
gate.

### What changed from v4, and why

v4's 291 units mostly measured nothing: 92 Python singletons and most
variant families were saturated for every reference model, Algorithms +
Math were 54% of the units, SWE/DevOps and DSP had no discriminating unit
at all, and the validators had the four defects above. Worse for the
language-assistance work, **no zig arm was ever unassisted**: every v4 zig
prompt carried a ~250-token stdio recipe (`&fr.interface`, `init.io`,
`takeDelimiter`, flush), and the v4 spec lint *required* it.

| | v4 | v5.0 |
|---|---|---|
| Scored units / base tasks | 291 / 137 | 71 / 34 (no probe units) |
| New | — | 29 units on 14 bases (163-196), each through three adversarial rounds |
| Kept from v4 | — | 18 units upgraded (U3: 36, 40, 122, 123) + 24 units kept as defined in v4 |
| Coverage | SWE, DSP: 0 discriminating units | all 12 categories, all 6 languages, 46 of 72 cells; Algorithms + Math 28% of units |
| Tests | visible fixtures, `expected.txt` / `check.py` in the agent's dir (173 units) | new and upgraded units: seeded hidden tests inside the validator, fed over stdin; a mismatch prints only the case index. Kept-v4 units: none of the leaking ones (71, 20 and every multi-language v4 family that writes `expected.txt` were left out) |
| Zig prompts | API recipe required | API tutoring **banned**: the contract (Zig 0.16, entry point, compile command) only |
| Answer key | readable by the agent | sealed: `evals/tasks`, `evals/refs`, `evals/suites` refused under `OPENBEAST_EVAL=1` |
| Web under eval | live, unrecorded | off (`OPENBEAST_EVAL_WEB=record` re-enables and logs) |
| Agent wall | `max_iter x 60 s x scale` (80 min for a 40-iteration unit at `--jobs 4`) | per-unit `max_wall_s`: 300 / 600 / 900 / 1500 s by tier; max_iter ceiling 30 |
| After the agent | nothing (a detached daemon ran into validation) | every process the agent started is killed, and validation refuses to start if one survives |
| Builds | the user's shared caches | a private cache per validation, deleted after it |
| LANG | over every base (a solved singleton = 100) | over multi-language bases only (k > 1) |
| Imputation | v5-fast pin | none: retired for v5 and for every arm |

### The proof every shipped unit passed

Every unit is **proven without a model** (`tests/prove_task.py`), through
run_eval's own lifecycle (cleanup, setup, drop the solution, pre_validate,
`run_validation`, cleanup), so the timeouts, rlimits, sandbox and reaping
are exactly what a measured unit gets:

| Arm | Required |
|---|---|
| ref | the reference passes 3/3 (5/5 when timed), and its slowest validation, cold compile included, fits the 15 s budget at scale 1 |
| wrong | at least two plausible wrong solutions FAIL, each with its declared failure class (a regex on the validator's output): failing on a lint or a compile error proves nothing about the check it was written to probe |
| empty | no solution at all fails |
| leak | no oracle-like file (expected*, answer*, check*, oracle*, hidden*, solution*) sits in the fixture dir after setup or validation, and a leak probe (a solution that prints whatever it can find) fails |
| slow | for a timed unit, a slow solution fails by the validator's OWN limit, not the harness timeout |
| scale2 | the reference passes again at `--scale 2.0` (the `--jobs 4` budget) |
| lint | no coreutils `timeout`, no `<bits/stdc++.h>`, no zig API tutoring, no `python -c SRC` child, no fixed limit that ignores `OPENBEAST_TIMEOUT_SCALE`: neither a `timeout=N` / `*_LIMIT = N` constant nor a measured wall time compared with a bare number (`el < 1.5`, also inside a shell validator's Python heredoc) |
| toolchain | (compiled units) a toolchain planted after the harness pinned its own never runs, and a solution that is not a program fails: fakes of every compiler first on PATH, each compiling the reference instead (`toolchain:path`); for Go, a go.mod `go 1.99.0` in the fixture, a go env `GOTOOLCHAIN=path` in `$HOME` and a fake `go1.99.0` on PATH (`toolchain:goenv`) |

Refs, wrong solutions and failure classes live in `evals/refs/<task_id>/`
(`prove.json` or `PROVE.json`), declared in the task JSON as `ref_stem` /
`dest`. Three families keep arms the generic gate cannot run (176: the
solution is an overlay directory; 179: shell scripts executed in the
fixture repository; 175: wrongs named in `prove.json`, kept in `bad/`);
for those, `prove_task.py` runs the family's own `evals/refs/<dir>/prove.py`,
which drives the same lifecycle and the same arms.

**The v5.0 proof run** (2026-11-15, the Mac harness host, zig 0.16.0,
rustc, go, clang, under load): **71 of 71 units PROVEN** in one pass after the last harness change (three prover streams in parallel, one family per stream at a time). Every reference passed 3/3 or 5/5 and again at scale 2.0, every wrong solution failed with its declared class, and no fixture leaked. The tightest margins: the cold zig compile is ~8 s of every zig validation, and the compiled 158 references use 12.4-13.8 s of the 15 s budget (158_b, Go, is closest).

On top of the prover, each **new** family (and U3) passed an adversarial
verifier, in up to three rounds: a separate session that tried to pass every unit
without solving it (reading the oracle through argv, the environment, git
objects, other worktrees and caches; planting modules and toolchain
configs; exec'ing into an interpreter; detaching daemons; patching the
validator's interpreter), re-tested the previous round's fixes, and re-ran
the prover for determinism. The kept-v4 units did not get that review in
their v4 form; see [residual gaps](#residual-gaps-known-documented-not-closed-in-v50).
That verdict held **on the macOS harness host with a clean host**: review
1116 then passed U3 units through a compiler planted on PATH (36_c with an
empty `main`), imported a stray copy of a reference from outside `evals/`
(183, 188, 40, 36_a, 123_a; on Linux any copy), and bypassed 40's numpy
ban. Those are closed below (toolchain pins, `confine=` for every Python
unit of these families, 40's lint and audit hook); the units were re-proven
with the new `toolchain` arm.

### The manifest

`suites/v5.json` carries per unit: `role` (all `scored` in v5.0), `src`
(`new` or `v4`), `est_s` (reference seconds — a time class for scheduling,
never a weight), `max_wall_s`, `arms`, and the pack-contrast `stratum`
(heldout = the 3 new zig units, insample = the 3 retained zig
variants). Units resolve to task files by id or by (task number,
language). `coverage_floor` refuses a manifest that drops a category or a
language (`category_min`, `language_min`); `cell_min` (0 in v5.0) is the
full-grid floor v5.1 restores. `scoring.SUITE_EXPECTED_UNITS["v5"]` is read
from the manifest, so the partial-run guard cannot drift from it.

```bash
python3 evals/suite_manifest.py            # summary, floor check, every unit resolves
python3 tests/prove_task.py --suite v5     # prove every unit of the manifest
python3 evals/suite_stats.py --check       # the docs cite the shipped numbers
```

### The arm matrix

| Arm | Treatment | Units it RUNS | Shared from L0 | On the board |
|---|---|---|---|---|
| **L0** bare | none (and no zig tutoring in any prompt) | all 71 | — | **yes**: the headline SCORE |
| **L1** prod | `--packs` (zig pack + the probed host pack) | the 6 zig units | on a host whose host pack is empty, every non-zig unit (its request is byte-identical); where it is not empty (the Mac harness host), none: the host pack changes every request, so non-zig units are `not_in_arm` in L1 | no |
| **L2** full | packs + push diagnostics + escalation | the 39 compiled + shell units | the 32 Python units (diagnostics are keyed per unit; Python is outside the assist scope) | no; shown next to L0 as `+assist` only |

Contrasts: **L1-L0 on zig** is the pack (reported per stratum: held-out vs
in-sample); **L2-L1 on zig** is diagnostics + escalation given the pack;
**L2-L0 on go/c/cpp/rust** is diagnostics (escalation is near-inert for cpp
under `-std=c++17`). The full 2x2(xesc) decomposition is an on-demand
campaign, not part of a profile. Profiles use the headline decoding, never
`--greedy` (greedy still churned ~30% on zig at `--jobs 4`).

All arms run **in one process** (`run_eval.py --arms L0,L1,L2`): a base
group (variants share `/tmp/eval_*`) stays on one worker across arms, with a
unit's arms back to back, so two arms never overwrite each other's fixture
dirs and server drift hits both. Each arm writes
`eval-<slug>-<arm>-<ts>.json` with `harness.arm` and a shared
`harness.pair_id`. A pair whose request equals L0's (same cache key) copies
L0's row (`shared_from`); the arm label is never part of a cache key.

**The host pack and the arm plan (2026-11-16).** Packs-on means language
pack + host pack (`agents/lang/host_pack.py`, probed from the tool host),
and the host pack goes to EVERY unit. Under `--arms` the manifest's plan
still decides which units an arm runs: the host pack reaches the 6 zig
units in L1 and the 39 compiled + shell units in L2, never the 32 Python
units. To measure it on every language, run a single packs-on run
(`BEAST_PACKS=1 run_eval.py --suite v5`, no `--arms`), which runs every
unit, and compare it with L0 rows of the same era and seeds.

**How small an effect one profile can see** (exact McNemar, alpha 0.05,
~50% power on expected counts, `evals/ablation.py mde()`; net units per
replicate at r = 1 / 2 / 4): zig, 6 units at 30% churn: not detectable
at r = 1, 4.0 at r = 2, 2.25 at r = 4; go+c+cpp+rust, 32 units at 12%:
8.0 / 4.5 / 3.0; all compiled, 38 units at 15%: 9.0 / 5.5 / 3.1. **v5.0
cannot resolve the pack effect in one profile**: the design's 15 zig units
plus 7 probes (n = 22) are 6 units without the rejected families and the
U4 probes, so the pack contrast needs replicates, campaigns, or the pooled
cross-model test (`ablation.py --pool`). `evals/ablation.py` flags any
`|net|` below the MDE as `within_churn`.

### Time: 3.73 reference hours for all three arms

Planning unit: **R-s**, seconds of the reference run (qwen-27b-mtp-q5-k-xl,
RTX 5090, `--jobs 1`). Per arm: **L0 2.37 h, L1 0.2 h, L2 1.16 h = 3.73
R-s h** for a full profile, **inside the 4-hour budget even sequentially**:

| Target | Conversion | Full-profile wall | Fits 4 h? |
|---|---|---|---|
| qwen-27b-MTP, `--jobs 1` | x1 | 3.73 h (L0 alone 2.37 h) | yes |
| qwen-27b-MTP, `--jobs 4` | /2.2 (measured) | ~1.7 h | yes |
| GLM-5.3-Flash, `--jobs 4` (spark pair, 4 streams) | x0.58 | ~2.16 h decode only, before prefill | yes, with margin for prefill |

The estimates are the design's rates (v4 tier rates, x1.6 for
discriminating and zig units, x0.66 for saturated ones; new units 82 /
165 / 330 R-s for medium / hard / XL), not measurements; the planner
measures instead of trusting them. Groups are scheduled **longest-first**
by `est_s x arms`; the longest group is now a 6-language hard family
(6 units x 165 R-s in L0, ~33 min across all three arms on one worker).

### How to run a 4-hour profile

```bash
# local llama-server on :8080 (finite reasoning budget in its serve script)
python3 evals/profile.py --budget-h 4 --jobs 4

# GLM-5.3-Flash on the spark pair (benchmark_all MODELS entry: URL, slots 4,
# thinking off declared; the device key comes from $OPENBEAST_SPARK_API_KEY)
python3 evals/profile.py --model glm-5.3-flash --budget-h 4 --jobs 4

python3 evals/profile.py --resume evals/results/profile-<slug>-<ts>.json   # after an interruption
python3 evals/ablation.py --profile evals/results/profile-<slug>-<ts>.json # the report again
```

1. **Preflight** refuses a profile whose numbers would be wrong: zig not
   0.16.0 (the pack's pin), the zig escalation index not served
   (`escalate.gate_state()`), a validator toolchain missing on the
   *harness* host (tools and validators run where the harness runs, not
   where the model runs), or an uncapped reasoning budget (uncapped
   Qwen3.8 took 36.55 h for no gain; GLM runs thinking off). It sets
   `OPENBEAST_EVAL_ENV_ERA=1` so weights, engine and toolchains enter the
   cache key.
2. **L0** runs the whole suite and measures **k** = wall s / R-s on this
   server (live units only).
3. **Plan**: cost L1 and L2 as k x est_s. If they do not fit what is left,
   cut L2 in this order, only as far as needed: (1) **stratified L2** —
   rerun every L0 failure and a seeded third of the L0 passes; rescues are
   exact, regressions are estimated by Horvitz-Thompson (each sampled pass
   weighted by 1/pi, pi = k/n for the k of n passes actually drawn, not the
   nominal third) and the p-value is labelled `stratified`; (2) drop the probes from L2; (3) drop rust from
   L2; (4) drop c from L2 (priority zig > go > cpp > c > rust). **L0 and L1
   are never cut.**
4. **L1 + L2** in one `run_eval` call with the plan's unit sets.

**The harness host.** Tools, validators and compilers run where the
harness runs, never on the Sparks. Before a profile or sweep of the pair, the
harness host needs zig **0.16.0** exactly, `rustc`, `go`, `gcc` / `g++`
(clang on the Mac), `git` and python 3.12 with `agents/requirements.lock`,
its own gate device key in `OPENBEAST_SPARK_API_KEY`, and the gate host
(spark-1) needs `EDGE_READ_TIMEOUT=3600`: a non-streaming generation longer
than the default 600 s is otherwise a gate 504, recorded as a server error.
Install them somewhere durable (`~/.local/opt`), not a temp dir: the harness
pins each compiler's install tree at start. Per-OS commands, online and
offline (beast-ark): [SPARK_HYDRA.md F6](../docs/SPARK_HYDRA.md#f6-the-eval-harness-host-benchmarking-the-pair).

**`--jobs` against beast-gate.** Every agent of a run shares one device
key, and the gate admits `EDGE_MAX_INFLIGHT` concurrent generations per
key; unset, that is the server's `INFERENCE_SLOTS` (4 on the spark pair),
else 2. `run_eval` reads that cap from the gate (a chat
request with `n=1024`, which the gate refuses with a 400 naming the cap
before forwarding anything; a non-gate endpoint never gets it) and clamps
`--jobs` to it, so a 4-slot profile of spark-1 runs 4 workers. A gate whose
cap is set below the slots clamps it further
(docs/SPARK_HYDRA.md, E2). The remote health check retries and counts a
429 as alive: one drained rate bucket or tailnet blip no longer aborts the
sweep.

A global deadline (start + `--budget-h`) rides through every phase: a unit
that would start after it is recorded **`budget_skip`** — never run, never
cached, never scored as a failure (a profile with skips is not seated). The
era is checked between phases; a profile never pairs rows across eras.
`--slots N` clamps `--jobs` for servers without llama.cpp's `/props`
(vLLM, TensorFold); for those, the reasoning budget is *declared*
(`--reasoning-budget`) and recorded as such, never read from `pgrep`.


### Scoring (v5)

Unchanged: CAPABILITY = 0.75 x SOLVE + 0.25 x LANG, difficulty weights
1 / 1.5 / 2 (`est_s` is not a weight). One change: **LANG is computed over
multi-language bases only (k > 1)** — v5.0 has 26 singleton bases, and
under the v4 rule each solved singleton scored LANG 100 by construction. A
category-balanced SCORE (mean of per-category capabilities) is reported as
a secondary readout only. A full v5.0 row covers 71 scored units;
`budget_skip` rows never enter a score.

### Validator isolation

The solution under test is a same-uid child of its validator. During the v5
authoring gate, adversarial verifiers passed units in 163, 164, 168, 173,
176, 181, 183, 188, 190, 20, 40, 121 and 122 without solving them, all
through the harness rather than through the tasks. Each row is closed in
the harness for every unit; the last five were closed for v5.0:

| Hole | Attack | Closed by |
|---|---|---|
| argv | `python -c <script>` put the whole validator (oracle, seed, keys) in a command line anyone can read: `ps -ww -o command= -p $PPID`, `/proc/PPID/cmdline` | `run_validation` feeds the script on **stdin**, from an unlinked file in a private 0700 dir. Python runs as `python -I -S -` and shell as `sh -c '<read stdin>; eval'` |
| import path | `-c` put `''`, the harness cwd, on `sys.path`. A planted `urllib/parse.py` ran on a lazy import (176) and **stayed for every later unit** | `-I` keeps cwd and PYTHON* off the path. The validator runs in a fresh, empty cwd that is removed after the unit |
| fixture cwd | Graded code finds the harness cwd through the ppid chain (libproc on macOS, `/proc` on Linux) and writes there. 176's setup is `python3 - <<EOF`, which puts `''` on `sys.path` and imports `tarfile`, which imports `copy`: a planted `copy.py` printed a forged OK and never built the **next** unit's fixture | `setup`, `pre_validate` and `cleanup` also run in a fresh, empty 0700 cwd (removed afterwards) with `PYTHONSAFEPATH=1`. On macOS a validator can also deny its children every write outside their own dirs: `vlib._sandbox_prefix(..., allow_write=[dir])` |
| environ / memory | `ps eww` and `/proc/PPID/environ` read the validator env; `/proc/PPID/mem` reads its memory | The environment is scrubbed down to PATH, HOME, locale, toolchain vars and `OPENBEAST_TIMEOUT_SCALE`. On Linux the validator is non-dumpable |
| scale | Fixed solution limits (4-10 s) ignored `--scale`, so references timed out under load and wrong solutions failed for the wrong class | `OPENBEAST_TIMEOUT_SCALE` is exported, and `vlib.t(s)` multiplies by it |
| hidden CPU | 122: double-forked, never-waited workers kept their CPU out of `RUSAGE_CHILDREN` (29 s of naive gemm was charged as 0.03 s) | `vlib.run_solution` counts the whole tree. Leftovers are refused as `daemon` |
| lingering solutions | a validator killed on timeout left its solution's group running into the next unit | vlib records each group it starts, and the harness SIGKILLs them after the validator exits |
| unsealed oracle copies | 190: the seal covered `evals/{tasks,refs,suites}` but not `.git`, other worktrees or clones, so a lint-escaped solution unpacked the task JSON from a git object and replayed the oracle. On Linux nothing was sealed | `run_solution(confine=..., read_only=...)`: a read allowlist (sandbox-exec / landlock / mount namespace) that fails closed, plus a per-run generator seed held only in validator memory |
| exec into an interpreter | 166 / U4: a compiled variant replaced its image with `execvp("python3", solver)` or `zig run <ref>`, running an unlinted interpreter; exec makes no child, so the fork rules missed it | `run_solution` confines a compiled unit's exec to its own binary (macOS `sandbox-exec`; Linux refuses when `/proc/<pid>/exe` changes). Verdict `exec` |
| network | a solution (or the agent via `fetch`) could reach `github.com` to pull the pushed reference, or phone home | `run_solution` denies `network*`; the agent's web tools are off under `OPENBEAST_EVAL` (or recorded) |
| read the validator argv | a confined solution could still `ps`/`/proc` the validator's argv/env (190) | `run_solution` refuses fork (no `ps`) and denies `process-info*` on other processes (self allowed, for a framework Python's re-exec). On macOS that does **not** cover argv/env: sysctl `kern.procargs2` stays readable for a same-uid process under every sandbox-exec rule tried, so the invariant is that no validator holds a secret in argv or env (run_eval feeds it on stdin, env scrubbed, seed in memory); `tests/test_eval_190_confinement.py` probes every ancestor for oracle text |
| read the reference across checkouts | U4: a solution globbed `/Users/*/.../worktrees/*/evals/refs/<unit>/*.py` and exec'd it | `run_solution` denies reads of any `evals/{refs,tasks,suites}` tree, in every checkout, not only this run's sealed paths |
| toolchain capture | 166 / 169: a planted `rust-toolchain.toml`, a `$HOME/.../go/env` with `GOFLAGS=-ldflags=-s`, or a `.cargo/config.toml` in an ancestor replaced the compiler or stripped the symbol table the validator reads. Review 1116: a `gcc` planted first on PATH (`/opt/homebrew/bin`, `~/.local/bin`, a conda bin: all user-writable and ahead of `/usr/bin`) compiled the reference and passed 36_c with an empty `main`; so did a go.mod `go 1.99.0` + a go env `GOTOOLCHAIN=path` + a `go1.99.0` on PATH for 36_b; 122's guard ran `otool`, `nm` and `clang` by bare name | **the harness pins the toolchain before any agent runs** (`run_eval.toolchain_pins` → `vlib.pin_toolchain`: each compiler and inspection tool resolved, a root-owned copy preferred, followed to the real binary behind a proxy, `GOROOT/bin/go` and the sysroot's `rustc`, and fingerprinted with its install tree, ctime included) and **refuses a validation** when a name now resolves elsewhere, appeared, or a pinned tree changed (`Toolchain changed since the harness started`, an env error, not banked). The validator gets the pins: `vlib.start_build(sources, argv, product)` compiles a private copy of only the source with the pinned compiler under `vlib.build_env` (private `HOME`, only root-owned dirs on PATH, `GOENV=off`, `GOFLAGS` cleared, `GOTOOLCHAIN=local`, `GOWORK=off`, GOPATH mode for a single-file Go build) and requires a native executable. Every validator also gets `GOENV=off`, `GOTOOLCHAIN=local` and the pinned rustup toolchain; `prove_task` checks it with the `toolchain` arms |
| compile-time build detection | 170: a solution read `@import("builtin").link_libcpp`, true only under `-fsanitize=thread`, and ran a different algorithm in the plain build | ban the distinguishing builtin fields, and validate behaviour in the PLAIN build too; `vlib.sanitizer_flags` builds the SAME source both ways |
| shell parent | `run_reaped` ran `python -I -S -` through `sh -c`, and dash (Debian's and Ubuntu's `/bin/sh`) stays alive as the parent. That shell is dumpable and holds the unlinked script as fd 0, so `/proc/<sh>/fd/0` gave a solution the whole validator | The list form is run as `exec python ...`, so the validator's parent is the harness, which is non-dumpable |
| other checkouts | A solution run from a worktree read `evals/tasks/40_attention.json` from the main checkout. The shared `.git` object store holds every branch's validators | `run_eval.validator_seal()` seals `evals/{tasks,refs,suites}` of every checkout in `git worktree list`, plus the git common dir (macOS; see below) |
| vendor BLAS | 122: a solution built A and B, then called `cblas_dgemm` from Accelerate or OpenBLAS (dlopen needs no link flag), and passed without writing a kernel | 122's validator lints the source, refuses a binary that links anything beyond the system runtime or imports a loader or a BLAS symbol, and watches the libraries the running process loads |
| shared build cache | 164: `zig build-exe` outside a build system leaves the whole compilation unit in `~/.cache/zig/tmp/<hash>/*.o`; a sealed prover build of the reference left 4 files, and from the measured agent's bash (which keeps `~/.cache` read-write) 40 of 40 cached objects linked into a passing solution | every validation gets a **private, throw-away build cache** in its 0700 dir: `ZIG_GLOBAL_CACHE_DIR`, `ZIG_LOCAL_CACHE_DIR`, `GOCACHE`, `GOMODCACHE` (with `GOPROXY=off`, `-modcacherw`), `CARGO_TARGET_DIR`, `XDG_CACHE_HOME`; `CCACHE_DISABLE=1`, `SCCACHE_DISABLE=1`, no `RUSTC_WRAPPER`. It is deleted with the validator (read-only module files included). `vlib.build_env` keeps it, and outside the harness puts caches under its private HOME, never `~/.cache` (`run_eval.build_cache_env`) |
| agent leftovers | 190 (harness-level): `run_agent` killed the agent's tree only on a timeout, and a daemon that detaches properly (double fork, `setsid`, stdio off the pipe) is reparented to init, outside every tree, group and session the harness knew. It runs as our uid into validation: it can rewrite the solution after the lint, read the validator's work dir through a shared `TMPDIR`, or eat the CPU a budgeted solution is measured against | **every process the agent started is killed before validation** (`run_eval.AgentWatch` + `sweep_agent_leftovers`): a per-agent tag in the env; on macOS a per-agent tag *file* that the eval bash sandbox denies, so `sandbox_check(pid, tag)` marks every process the shell started even after `env -i` (a sandbox can be neither left nor replaced); a thread that polls the agent's tree every 0.5 s and records each (pid, start time) it saw, with the runner made a child subreaper on Linux so orphans stay in its tree; the process groups and sessions those processes led, each with its leader's identity, matched only while provably the same group (leader alive with that identity, or leaderless and never seen empty: a reused id, common on macOS where a long unit wraps the 99999 pid space, is never killed; review 1116); and on Linux with a delegated cgroup v2 a child cgroup nothing can leave (`cgroup.kill`). The matches are frozen (`SIGSTOP`) and `SIGKILL`ed with their subtree for up to 6 rounds, then the sweep **asserts none survive**: a unit with survivors is not validated. Only processes of our uid that started after the agent can match, and the sandbox test is differential (tag denied, sibling file allowed), so app-sandboxed system agents are never touched |
| early exit | v4 python validators import the solution into their own interpreter, so `os._exit(0)` or `sys.exit(0)` at the solution's import ended the validator with status 0 before any check ran; it passed 143, 146, 152, 153, 121 and 128 | the bootstrap prints a per-run random **completion line** only when the validator ran to its end (or its OWN code raised a clean `SystemExit`; one raised in a frame of the solution does not count), and `run_validation` passes exit 0 only with that line present. The line is held only in a bootstrap frame, never a module global (until review 1116 `__main__._ob_n` let an in-process solution print it and `os._exit(0)`). This stops naive early exits; it does **not** stop an in-process solution that walks the interpreter's frames (see Residual gaps) |
| relative argv | the round-3 `exec_only` rule named `realpath(cmd[0])` resolved in the validator's cwd, but compiled validators run `./divmod` with `cwd=/tmp/eval_x`: the literal named a file that does not exist, and every compiled unit failed with "solution exited 71" | `vlib._resolve_exec` resolves a relative argv against the solution's cwd and a bare name through its PATH |
| the validator reads agent files with its own rights | review 1116: the VALIDATOR is not sandboxed, only its children are. 176's `copytree` followed a `shopd/x.py` symlink to the sealed reference fix (and a link to `/dev/zero` wrote without limit); 163/167/168 compiled `#include "<repo>/evals/refs/..."`; 179 ran `git` in the agent's repo, whose `.git/config` (a promisor remote's `uploadpack`) runs any command on a lazy fetch | read agent files with `O_NOFOLLOW`, regular files only, under a byte cap (`vlib.read_source`; 176 copies only regular files and dirs); compile through `vlib.build_private` (lint + private dir + `run_build` read-deny); 179 parses HEAD and refs from their files and copies only loose objects and pack/idx files into a bare repo it owns (`protocol.allow=never`, `GIT_NO_LAZY_FETCH`) |
| validator temp files | a validator SIGKILLed at the harness timeout never runs its `finally: rmtree`, and its `tempfile` dirs sat in the shared `TMPDIR` | `run_validation` points the validator's `TMPDIR` at `<private>/tmp`, removed with the private dir whatever happens |
| env launcher | 122 starts the solution as `/usr/bin/env DYLD_INSERT_LIBRARIES=<monitor> ./gemm` (sandbox-exec is SIP-protected and drops `DYLD_*`); confining exec to `/usr/bin/env` alone denied the solution itself | `exec_only` takes a list: for `/usr/bin/env` it is env plus the one program env runs, and still nothing else |

`evals/vlib.py` is the validator's standard library. The harness injects it
into the validator from source; it is never on an import path or in the cwd.

- `vlib.run_solution(cmd, input=, cpu_s=, wall_s=, allow_fork=False)` runs
  the solution in its own session and process group under a CPU budget for
  ALL descendants. That is exact through a delegated cgroup v2 when the host
  has one. Otherwise it is RLIMIT_CPU per process, plus wait4 rusage, plus a
  pgid/ancestry sampler. That sampler reads `/proc` on Linux, with a child
  subreaper so that orphans stay in view, and libproc in-process on macOS
  (it forked `ps -A` every 50 ms until review 1116: ~140 ms and ~0.4 CPU-s
  of overhead per run, a third of a core); `ps` is the fallback. The solution's own rusage
  always counts: it is reaped with `wait4(WNOHANG)`, never `Popen.poll()`,
  which discarded it, so CPU used after the last sample was lost and a
  fixed-CPU gate (122, 123, 36) passed an over-budget solution about 1 run
  in 12. The sampler wakes the moment the solution exits (kqueue on macOS,
  a pidfd on Linux) instead of sleeping out the poll. Fork is
  refused by default, and on macOS `sandbox-exec` enforces it. With
  `allow_fork=True`, orphans and survivors get `verdict="daemon"`. On macOS
  the confined solution also has **no network**, **no process-info on other
  processes**, **no signals to any process outside its own sandbox** (it can
  still kill itself and its own children; before review 1116 it could
  SIGKILL sibling units, their validators or the harness), and **no read** of any `evals/{refs,tasks,suites}` tree (plus
  `deny_read` and `OPENBEAST_VALIDATOR_SEAL`, which `run_eval.validator_seal()`
  builds from `evals/{tasks,refs,suites}` of every checkout in `git worktree
  list` plus the git common dir). A compiled unit's exec is
  confined to its own binary (`confine_exec`, auto for a non-interpreter
  `cmd[0]` with `allow_fork=False`), so it cannot exec into python3; the same
  is caught on Linux by a `/proc/<pid>/exe` change (`verdict="exec"`).
- **Confinement**: `confine=[work], read_only=vlib.python_runtime(exe, modules)` replaces
  the deny-list with an allowlist on both platforms. The solution may read
  only its interpreter's stdlib (never site-packages), the libraries it
  maps and `work`. It writes only `work` and gets no network and no
  process-info on other processes. macOS uses a
  deny-by-default sandbox-exec profile; Linux uses landlock, or a user +
  mount namespace with a fresh root (and an empty network namespace) on
  kernels before 5.13. Landlock has no UDP right and no TCP right before
  ABI 4 (kernel 6.7), so the landlock path also installs a seccomp filter
  that lets `socket()` create only AF_UNIX / AF_NETLINK sockets and refuses
  io_uring and foreign-ABI syscalls; until review 1116 a confined solution
  could send UDP (and below 6.7 open TCP) under this "no network" contract.
  An arch without a syscall table (anything but x86_64 / aarch64) fails
  closed. Without either
  it returns `verdict="sandbox"` and does not run the solution. 190 needs it:
  its verifier read the oracle out of the unsealed `.git` object store.
- **Builds**: `b = vlib.start_build({SRC: source}, BUILD, EXE)` then
  `ok, log = b.finish(vlib.t(25))` and run `b.product` (36, 122, 123 do).
  It is the pieces below in one call: `vlib.private_build_dir({relpath: src})`
  returns a fresh 0700 dir (under the harness, inside the validator's private
  dir) holding only the source, with no toolchain-config ancestor; build there
  under `vlib.build_env()` (clean private `HOME`, only root-owned dirs on
  `PATH`, `GOENV=off`, `GOFLAGS` cleared, `GOTOOLCHAIN=local`, `GOWORK=off`)
  with `vlib.tool_path("rustc")`, which returns the binary the harness pinned
  before the agent ran (outside the harness, the first root-owned copy on
  PATH). Then `vlib.assert_native_executable(out)`: a `#!`-script behind a
  planted compiler is not ELF/Mach-O. `vlib.scan_toolchain_overrides(dir)`
  lists any planted `rust-toolchain`/`.cargo/config`/`go.work`/`go env` in an
  ancestor. `vlib.sanitizer_flags("thread")` builds the same source with and
  without the sanitizer.
- **The compiler reads with the validator's rights.** `#include
  "<repo>/evals/refs/<unit>/ref.c"` (or Rust `include!`, an `.incbin`, a
  source symlinked to the reference) made the compiler read the sealed
  reference the agent cannot open, and the binary WAS the reference (163,
  167, 168). `vlib.build_private({src: bytes}, argv, lang=, timeout=)` lints
  the source (`vlib.lint_source_includes`: no include/import/embed of an
  absolute, `..` or macro-computed path, no `.incbin`, no Rust
  `include!`/`include_str!`/`include_bytes!`, no zig `@embedFile`/`@cImport`),
  copies it into a `private_build_dir` and compiles there under
  `vlib.build_env()` through `vlib.run_build`, which on macOS adds the
  solution's read-deny (every `evals/{refs,tasks,suites}` tree and the seal).
  Read the source with `vlib.read_source(path)`: a regular file only, never
  through a symlink.
- `vlib.t(s)` and `vlib.scale()` give the timeout scale. `vlib.python_cmd(p)`
  gives `[python, -I, -S, p]`. `vlib.processes()` is the process table
  (/proc on Linux, libproc on macOS, `ps` as the fallback). `vlib.lint_python(src, ban_modules=, ban_calls=)` is an AST lint for
  the known dodges.

**What a validator must do** (the full checklist is in
`skills/eval-task-author/SKILL.md`, "Validator isolation"):

- Run the solution in a **separate process** from anything that holds
  answers, and judge only its output. A scenario driver that has to import
  the solution holds no answers; it prints observations and the parent
  compares them. Never trust the solution's in-process state: it can rebind
  your functions and timers.
- Run it with `vlib.run_solution`, and scale every fixed limit (`vlib.t`).
- Start no `python -c SRC` child. Feed SRC on stdin to `python -I -S -`, or
  use a quoted heredoc to `python3 -I -` in bash.
- Lint with the AST (`vlib.lint_python`), not with a line regex, and check
  the whole output.

`tests/prove_task.py` lints validators for `-c` children and for fixed
limits that never read the scale, including a measured time compared with a
bare number (`prove_task.unscaled_time_gates`, which reads a shell
validator's Python heredocs too). The isolation itself is pinned by
`tests/test_eval_validator_isolation.py`.

### The isolation model, in one place

A measured unit has three actors, all running as the operator's uid on the
harness host, and the harness keeps each one away from what it must not
reach:

| Actor | Must not reach | How |
|---|---|---|
| **the agent** (runner + its tools) | the answer key; the web; anything after its turn ends | `OPENBEAST_EVAL=1`: the file tools refuse `evals/{tasks,refs,suites}`, and on macOS its bash runs under `sandbox-exec` with those trees denied in the kernel (quote-split and git-blob spellings are caught by the text check on both platforms); `fetch` / `web_search` refuse. When the runner exits, **the leftover sweep** kills everything it started, detached daemons included, and refuses to validate if anything survives |
| **the validator** | an attacker-controlled import path, cwd, argv or environment; a shared build cache | stdin-fed script, `python -I -S -` in an empty private cwd, scrubbed env, non-dumpable on Linux, **private build cache deleted afterwards**, a completion line that no out-of-process solution can print (an in-process one can, by frame introspection: Residual gaps) |
| **the solution** (run by the validator) | the oracle (validator memory, the answer key, git objects, other checkouts), the network, other processes, other programs | `vlib.run_solution`: own session and process group, CPU budget for the whole tree, fork refused by default, exec confined to its own image (or an env launcher and its one target), no network, no process-info on others, no signal to any process outside its sandbox (macOS `(deny signal (target others))`; Linux `confine=` through Landlock scoped signals, ABI >= 6), reads of every `evals/{refs,tasks,suites}` tree and the git store denied; `confine=` turns that into a read allowlist that fails closed (sandbox-exec, Landlock, or user + mount namespaces) |

Between units: fixture roots that another uid owns or can write are
refused; setup, pre_validate and cleanup run in a fresh private cwd; every
solution process group a validator recorded is SIGKILLed after it exits.

### Residual gaps (known, documented, not closed in v5.0)

- **v4-defined units are the weak link.** The units kept as defined in v4
  (the third group above) import the solution into the validator's own
  interpreter and do not use `vlib.run_solution`, so none of the solution
  confinement applies to them: an adversarial solution can read the
  reference or the task JSON at import time, or rebind the checks it is
  judged by. The completion line closes the trivial exit-0 cheat, and the
  agent itself still cannot read the answer key, but a solution that
  reads it at validation time is not stopped. Nor is a forged completion
  line: anything the validator can print, code running in its interpreter
  can print too, so a solution that walks `sys._getframe()` to the
  bootstrap frame, reads the line and calls `os._exit(0)` passes. No
  in-process scheme fixes that (a private fd or a closure is just as
  reachable); only running the solution out of process through
  `vlib.run_solution`, as every v5-authored unit does, does. Screening the rework
  branches' wrong solutions against these v4 validators found plausible
  ones they accept: on 158, Python big-int shortcuts (hex round trip,
  digit collapse), a zero-operand bug no input exercises, a 32-bit-limb
  schoolbook fast enough for the gate, and a solution that spawns another
  process; on 159, Kronecker substitution into one Python int and dropped
  trailing zero coefficients; on 143, a T that does not mix the variables;
  on 153, dropped children; on 128, a non-deterministic or lambda(n) key;
  on 109, `==` instead of `compare_digest` and the `now == exp` boundary;
  on 146/147/117/124/37/29/49, the gaps listed in each unit's
  `evals/refs/<id>/prove.json` note. Their rewrites are U1, U2 and U4 in
  the backlog.
- **The agent's file tools are not kernel-sealed** the way its bash is
  (164's verifier): `read_file` refuses only the sealed trees by path, not
  the rest of what the bash sandbox denies, so a stray validator copy left
  outside `evals/` (a scratch dir, a backup) is readable. Keep validator
  copies out of the agent's reach.
- **A compiled solution can swap its own image** (164): `exec_only`
  allows the solution's binary path, and the binary sits in a dir the
  solution can write, so it can replace itself and re-exec. Validators that
  build into the fixture dir are exposed; the fix (run from a read-only
  copy, or hash the image after the run) is a v5.1 harness item.
- **Linux confinement is the environment's job.** On macOS sandbox-exec
  enforces the solution rules, and `vlib.run_build` puts the compiler under
  the same read-deny; on Linux the compiler has none, so
  `vlib.lint_source_includes` is the only wall against a source that makes
  the compiler read the reference; on Linux vlib catches fork by sampling (verdict `forked`), exec
  (`/proc/<pid>/exe`) and, with `confine=`, reads (Landlock or namespaces),
  but the deny-list form (no `confine=`) has no kernel read-deny. Every
  Python unit of the new families and of U3 runs with `confine=` since
  review 1116; the kept-v4 Python units do not (they import the solution
  in-process, see above). On a Linux harness host the **agent's own bash**
  has no read-deny either (`agents/tools.py` seals it on macOS only), so a
  Linux host is not leak-proof against an agent that reads `evals/refs`
  during its turn; the documented harness host is the Mac. The
  leftover sweep on Linux is exact only with a delegated cgroup v2; without
  one it relies on the env tag, the tree poll (the runner is a subreaper, so
  an orphan stays in its tree while it lives) and groups/sessions. A daemon
  that scrubs its environment and is orphaned between two polls *after*
  the runner has exited is the one case it can miss there.
- **macOS env tag sees only non-platform binaries.** `KERN_PROCARGS2`
  returns argv without the environment for Apple-signed binaries
  (`/bin/sh`, `bash`, `sleep`, `nohup`), so on macOS the per-agent env tag
  finds a detached Homebrew python but not a detached `/bin/sh` daemon. The
  sandbox tag (the eval bash runs under `sandbox-exec`) is the real
  mechanism there; when it is unusable (no `sandbox-exec`, or the harness
  itself runs sandboxed) the sweep prints a warning, and a daemon that
  left the agent's tree, group and session between two 0.5 s polls can
  survive. The sweep polls once more right before it kills.
- **macOS timeout path.** Until review 1116 the wall-timeout kill walked
  only `/proc`, so on macOS it killed just the runner's group, and a tool
  child in its own session ran on through the 5 s pipe drain;
  `tests/test_eval_timeout_reap.py` failed on every Mac (CI runs Linux
  only). The walk now falls back to libproc and the watched tree is killed
  before the drain. The process snapshots are libproc in-process (~2 ms,
  no fork) instead of `ps -A ... lstart=` (~45 ms, twice a second per
  agent).
- **macOS argv/env of other processes** stay readable to a same-uid process
  through `kern.procargs2` under every sandbox rule tried, so no validator
  may hold a secret in argv or env (none does: the script arrives on stdin).
- **Sibling agents under `--jobs`.** The leftover sweep kills one unit's
  agent tree before ITS validation; the other units' agents are still
  running then, as the same uid, with bash. On macOS their sandbox only
  denies reads of the sealed eval trees, and on Linux they have none, so a
  careless `rm -rf /tmp/eval_*` or `pkill -f zig` in one unit can fail
  another unit's validation (a false FAIL, which the cache banks; it can
  never inflate a score). Closed in the harness: a forged entry in a
  validator's `pgids` file can no longer make the harness kill an older
  process (only groups with a member started after the validation began
  are killed). Not closed: the fixture files and the validator's private
  dir in the shared `TMPDIR` stay writable to a sibling agent. The fix is
  a write-deny in the agent's eval sandbox (`agents/tools.py`, which is in
  the era hash) plus a Linux equivalent; until then, a campaign that must
  be exact runs `--jobs 1`.
- **Shared fixture paths.** A family's units share one fixed `/tmp/eval_*`
  dir. Two harness processes on one host used to corrupt each other there
  (186, 190; review 1116: the 167_q15_biquad_a/_c references failed 8 runs
  of 12 side by side, and a prover during a sweep silently `rm -rf`'d the
  live unit's fixture of 173/175/178 against a GLM sweep; those FAILs were
  banked as model failures). Every harness now holds an exclusive advisory
  `flock` per fixture root for a unit's whole cleanup -> setup -> agent ->
  validate -> cleanup (`run_eval.fixture_lock`, lock files in
  `~/.cache/openbeast/fixture-locks/`, or `$OPENBEAST_FIXTURE_LOCK_DIR`; a
  private 0700 dir, refused with a warning and run unlocked otherwise). A
  sweep (`run_eval`, `profile.py`, `benchmark_all.py`) waits for a root
  another harness holds and says so at once; `tests/prove_task.py` plus the
  family provers of 175/176/179 refuse at once with the holder's pid
  ("fixture busy"). It also serialises two threads of one run whose units
  share a root. Not covered: tests that drive `run_setup` directly, and
  family provers outside 175/176/179; run those one at a time.
- **Toolchain pins are per harness process.** They are taken when the
  harness starts, so a plant left by an EARLIER run's agent is pinned as
  found, unless a root-owned copy of that name is on PATH (then that copy is
  pinned). Tools outside the pinned names and trees (a linker in a user dir
  that a compiler finds without PATH) are not fingerprinted. The kept-v4
  compiled units (158, 159 b-e) still build in their fixture dir with the
  bare name: the pin check and the Go/rustup env cover them, the private
  build dir does not. The U3 rust and zig variants share `start_build` with
  the proven go/c/cpp ones but were not re-proven in review 1116 (no rustc
  or zig on the harness host or the Sparks at the time).
- **Timing headroom.** The cold zig compile now happens in every
  validation (private cache): about 8 s of the 15 s validator budget on a
  loaded Mac; a Go unit rebuilds its standard library each time (158_b's
  validation is 13.5-13.8 s, almost all of it the oracle and the build).
  A slower harness host needs `--scale`; a std-only cache template would
  win the time back and is a v5.1 harness item (measured in review 1116:
  a cold Go validation spends ~2 s wall and ~5.5 CPU-s rebuilding std; a
  cloned `go build std` seed cuts it to ~0.6 s). It is deferred because the
  seed would be a shared, same-uid-writable input to every validator's
  build, which the agents (no write sandbox) could poison; it needs an
  integrity check or the agent write-deny first.

### v5.1 backlog: the 12 families that did not ship

Each family below went through three adversarial rounds (an independent
verifier tried to pass every unit without solving it, re-tested the
previous round's fixes, and re-ran the prover). These did not come out
clean, so v5.0 ships without their new versions. Their work is kept on the
`worktree-wf_a06b4539-9a5-<n>` branches named below; the verdicts are in
the round-3 review. A family moves into v5.1 when a fresh verifier finds
no open issue and the prover passes 3/3 at scale 1 and 2.0.

| Item | Branch | What it is | Open issues (round 3) |
|---|---|---|---|
| **164** Q4_K block quantise/dequantise, 6L | `-3` | llama.cpp Q4_K bit packing, scale/min sub-blocks | **Blocking, harness:** compiled references stayed in the shared zig cache (`~/.cache/zig/tmp/<hash>/*.o`); from the agent's bash, 40 of 40 cached objects linked and passed. Closed in v5.0 by the private build cache. **Blocking, compiled units:** `exec_only` allowed the solution's own path, and its cwd was writable, so a C solution unlinked its binary, wrote a stolen one to the same path and re-exec'd it (passed ref x3 and scale 2.0). Fix: run from a read-only copy, deny writes to the exe path, or hash the binary after the run. **Harness:** the agent's file tools are not kernel-sealed the way bash is (`read_file` returned stray validator copies the bash seal denies). |
| **165** ChaCha20-Poly1305 AEAD, 6L | `-4` | RFC 8439 compliance, constant-time tag compare | C/C++ call macOS corecrypto (`ccchacha20`, `ccpoly1305`) through hand-declared prototypes: the "no crypto library" rule bans only headers. The forced-tag table that proves "OPEN follows tag_eq" sits in plain text next to the running binary (C, Go) and inside the compiled sources (`@embedFile` in zig, `include_str!` in Rust, `#include "ctq.c"` in C/C++), so a solution reads the table and answers with a leaky compare. Zig reference's cold-compile wall is 79% of the 15 s budget. |
| **166** Welford + Chan parallel-merge moments, 6L | `-5` | numerically stable moments on cancellation data | Verification incomplete: the 3-run determinism and the scale 2.0 runs were not finished, and the round-2 issues and new bypass attempts were not re-tested independently (the family ships wrong_exec.*, plant arms for rust-toolchain and go env, and the ancestor scan, unverified). |
| **169** Broken-build repair (pyproject, go.mod, Make, CMake, Cargo, build.zig), 6L | `-8` | toolchain repair, the material for the diagnostics ablation | Adversarial step not done (stopped). C/C++: `check_flags` refuses `-w`, `-Wno-error*` and friends but not clang's `-Wno-everything`, which silences every warning so `-Werror` never fires. The -f flag denylist allows `-fmodules`, `-fmodule-file=` and similar; an allowlist is probably safer. |
| **170** Lock-free MPMC ring buffer, 4L (c, cpp, rust, zig) | `-9` | acquire/release ordering, ABA | **Blocking:** race-freedom is checked only in the TSan build and lock-freedom only in the plain build, so a ring that tells the builds apart is a spin-locked queue under TSan and a relaxed-only ring in the plain build (C passed 3/3 under Rosetta TSO; Rust and zig 2/4 on ARM). Zig lint still allows `@import("builtin")` fields that reveal the TSan build. Fix: run a progress case inside the TSan binary for C/Rust/zig, add the split cheats as wrong arms, allow only specific builtin fields. |
| **171** Fix a deadlock and a race in a Go worker pool | `-10` | two interacting bugs, goroutine leak | **Determinism:** the reference fails intermittently under load (halved `join` sleeps in H12-H14). Four mutants pass all 510 hidden runs: a waited-for genuine error that does not cancel the Map's other invocations; waiters watching the caller's ctx instead of the Map's; returning early on another Map's error while workers drain; a pre-cancelled ctx with every key cached returning values. `prove_task.py` ignored `repeat: 5` and `alt_refs`. |
| **180** Find unnamed vulns in an 8-file `http.server` app | `-16` | no hints: traversal, IDOR, SSRF, timing | No family fix in round 3. Still passing: a handler that skips `auth.py` and compares tokens with `==`; a planted `app/hmac.py` shadowing `hmac`; a local `compare_digest`; `startswith(root)` with no separator; laundering the token through a subscript. New: substring SSRF allowlist; a lambda default that escapes the taint check; an `app/auth/` package shadowing `auth.py`; `server.py` rebinding `auth.authenticate` at import. Structural fix: instrument the real server process from a validator-owned `python -I` bootstrap. |
| **181** JWT verifier hardening | `-17` | alg=none, RS/HS confusion, kid, leeway | **Blocking:** a stdlib-only cheat walks every `.git` under `$HOME`, reads the reference blob through the pack index and execs it (457/457, 3/3 at scale 1 and 2.0): the solution sandbox denied `evals/(refs|tasks|suites)` but not `.git/objects`. **Blocking:** the seal can be renamed away (`os.rename` of `evals/refs`, read, rename back). Non-blocking: the runner shares the solution's interpreter, so the "never raises another exception" contract can be laundered. |
| **186** Robertson stiff kinetics | `-19` | explicit methods exceed the RHS budget | Adversarial step not done (stopped by a safety check). The fixed fixture dir `/tmp/eval_rob186` makes two concurrent proofs corrupt each other. The Linux seccomp path was exercised only in an aarch64 container. |
| **U1** rework of 117 / 158 / 159 | `-25` | iterative refinement, Karatsuba, NTT (6L) | Verification incomplete: 12 of 13 units have one PROVEN run (158_f unfinished), no 3-run determinism, no separate scale 2.0 run, and the re-tests and new bypass attempts were not done (a tuned packed-limb or vectorised schoolbook meeting the native 158 CPU gates is the open question). Headroom on the 158 CPU gates is thin (C ref 47%, Go 42% of the gate). |
| **U2** rework of 71 / 20 / 146 / 147 | `-26` | reverse list, priority queue (6L), Aho-Corasick, persistent BST | 147 passes with a persistent chunked sorted list (no BST, no path copying) because the memory gate cannot tell O(sqrt n) from O(log n) copying at 8000 versions. 20: libc db(3) B-tree (C/C++ on macOS), Go `fmt` sorting map keys, Python `Counter.most_common()`, and no complexity gate at all. 71_d: `std::valarray` with a negative-stride `std::slice` reverses without any banned identifier. |
| **U4** rework of 143 / 152 / 153 / 121 / 128 and the 7 zig probes (31, 92, 52, 54, 61, 62, 63 `_f`) | `-28` | UOV, Chase-Lev, coroutine scheduler, quorum KV, RSA; the pack-contrast probes | 0 of 12 PROVEN: every lint arm fails (`-I -c` children; fixed limits that ignore the scale). No validator runs the solution through `vlib.run_solution`, so the round-3 sandbox never applies: a solution globs every worktree's `evals/refs` (and `evals/tasks/121_quorum_kv.json`, to cut out the validator's own model) and execs it; 152 and 153 pass by patching the shared interpreter or taking over the protocol fds; 143 passes `uov_rank2`; zig units call `system()` to build and exec the reference during the untimed warm-up, call compiler-rt `__popcountdi2`, or `@reduce` a bit vector. |

### The era break

`evals/SUITE_VERSION` is `v5`, which rolls the cache era (`ctxhash`) for
every row: no v4 cache entry replays into v5. The v4 board is frozen (shown
under "legacy suites"); v5 starts empty. The harness rides the same break,
pinned as **`harness7`** in `evals/HARNESS_VERSION` (`harness5` at the v5.0
release; the review-1116 fixes to the bootstrap, the leftover sweep, the
validator's private `TMPDIR`, the fixture lock, the toolchain pins and
`vlib.py` rolled the era once more, and G23 passing the wall to the agent made it `harness7`; see CHANGELOG): its fingerprint covers
the agent's wall cap, the validator's environment, bootstrap and private
build cache, the fixture cwd, the leftover sweep (`AgentWatch`,
`sweep_agent_leftovers`) and the toolchain pins (`toolchain_pins`,
`_toolchain_problems`), and `evals/vlib.py` is now hashed into the era
too, because run_eval injects it into every validator. `agents/tools.py`
(the seal, per-unit diagnostics, the agent's sandbox tag) is era-hashed as
before, with `agents/runner.py`. **Runner v2** (2026-11-16: platform line,
bash over sh, turn budget, unchanged-plan nudge, repeat guard, timeout
wording; see CHANGELOG) edited both, so it rolled the era again: rows from
before it do not replay into it. Expect **zig L0 to drop sharply** against v4 (the v4 off-arm passed
10-11/30 *with* the recipe in the prompt) and LANG with it: that is the
intended effect of an unassisted L0, not a regression.

## v4-fast — the pinned fast suite (v4 only, imputation-scored)

> Named `v5-fast` until the v5 era break (the old name still loads, with a
> note). It is a subset of the **v4** task universe (task files 01-162),
> imputes on the v4 scale, and is never used for v5 or for any arm.

`evals/suites/v4-fast.json` pins the 112 units that carry the suite's signal:
the **92 discriminating units** (passed by some but not all of the 9 reference
models) plus **20 cheap all-pass "tripwire" units** kept as regression
canaries. The other 179 units are saturated — every reference model passes
177 of them and none passes 2 — so a fast run doesn't run them, it **imputes**
them: scoring reconstructs the full 291-unit task list with the assumed
outcomes and computes the standard v2 capability metric on it.

**Fidelity contract:** for any model whose real outcomes match the
assumptions, the imputed score EQUALS the full-suite capability score
exactly — same number, same leaderboard scale. This is verified as an
identity on every reference run by `make_fast_suite.py`, which are the runs
the pin was built from, so the check holds by construction and says nothing
about other models. `make_fast_suite.py` (verify mode) therefore also prints
a **leave-one-model-out** table — re-derive the pin from eight reference
runs, impute the ninth — which is the honest estimate of the error on a
model the pin has not seen. On the nine reference runs only 1 of 9 comes
out exact; the mean error is **+0.87** points and the worst **+2.04** (the
35B-A3B MoE: 97.05 imputed against 95.01 real), always reading high, and 5
of the 8 misses fail **no** tripwire, so the guard below does not catch
them. The table is reported, not gated: the exit code and the pinned file
are unchanged. On the two non-reference full runs on disk, 2 units and
1 unit violated `assumed_passed` with no tripwire failing, and the imputed
score read high (98.48 against a true 98.43, and 98.42 against 98.38).
**Treat an imputed score as an upper bound outside the reference family**
(Qwen 27B–35B); for IQ3/IQ2 quants and other model classes no full run exists
to bound the error. Paired comparisons on the pinned units are unaffected.
(Raw subset scoring
was measured at τ = +0.810 against the full metric and rejected — it flips
near-tie ranks; see `docs/EVAL_FAST_SUITE_PROPOSAL.md`).

**The guard:** a model that fails any tripwire is breaking the saturation
assumption — the readout prints a loud warning and the imputed score must
not be trusted; run the full v4 suite instead. This is expected for weaker
or differently-shaped model classes (the reference family is Qwen 27B–35B).

Fast-suite runs record `suite_selection` in their results file, are refused
by the leaderboard partial-run guard by design, and reuse the same cache
keys as full runs — a fast run warms the cache for a later full run and
vice versa. Regenerating the pin (`--generate`) is a deliberate act done
when the reference-run set changes; the pin lists its source runs.

## Result cache (durable, file-backed)

Reruns are slow when most of the suite hasn't changed. The cache short-
circuits any (model, task, agent_context) tuple whose inputs match a
prior live run. See `evals/cache.py` for the hash-key construction;
durability verified by `tests/test_cache.py` (cross-process round-trip,
atomic writes, corrupt-file safety).

Invalidates automatically when (a) any task spec changes (setup, task,
validation, cleanup, max_iter — keys starting with `_`, like the
runtime-injected `_path`, are stripped before hashing, so relocating the repo
does NOT bust the cache), (b) the model changes, (c) the **effective iteration
budget** changes (a `--max-iter 5` smoke run is a different experiment from the
default), (d) the eval **suite version** bumps (`evals/SUITE_VERSION`), or
(e) the agent runtime context changes — `system-prompt.md`, the agent
runtime itself (`agents/runner.py`, `agents/tools.py`: a change to the loop
or a tool's behavior changes what a "cached result" means), or the harness
marker `evals/HARNESS_VERSION`, which pins run_eval's agent-shaping code (the
agent's argv, env and wall budget, fixture setup, the validator and its
timeout; `tests/test_cache.py` fails when that code changes without a bump).
`opencode.json` and `system-prompt-tools.md` are NOT hashed: the eval agent
reads neither, so an OpenCode catalog edit no longer cold-starts the cache. Timeouts (`agent_exit_code
== -1`) are NOT cached: those are environmental, not deterministic. Neither
are FAILs caused by infrastructure (`cacheable_result` in `run_eval.py`):
a unit whose runner reported `API_ERRORS: n` ≥ 1, or whose server failed
the health check right after the agent finished, is recorded with
`reason: server_error`; a unit whose validation output shows fork/thread
exhaustion (`Resource temporarily unavailable`, `SystemResources`,
`thread constructor failed`) or a full disk (`No space left on device`,
`NoSpaceLeft`) is recorded with `reason: env_error`; and a unit whose
**validator** ran out of its own time budget is recorded with
`reason: validator_timeout` — on a contended host that happens to a correct
solution, whose compile is most of the budget. (The validator's budget now
takes the same `timeout_scale` as the agent's wall budget, so a `--jobs N`
run no longer times validators out at the serial figure.) All three retry
live on the next run. A run containing `server_error` or `env_error` can't
enter the leaderboard until that rerun is done; a `validator_timeout` row is
**not** an infrastructure row for that purpose today — it still seats, and
still counts as a FAIL in paired verdicts (an open decision, `docs/TODO.md`).
An `env_error` or `validator_timeout` that repeats for the same cache key
(one shared strike counter, `OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER`, default
3) is the model's own program exhausting the machine or hanging, so it
banks as a plain FAIL (`env_error_repeats: N` /
`validator_timeout_repeats: N`) instead of rerunning forever. Cache entries
banked before 2026-10-09 are untouched: 17 of them hold a "Validation timed
out" FAIL and still replay as one until cleared with `cache_cli.py`.

**Each unit starts clean.** The harness runs a task's `cleanup` *before* its
`setup` as well as after validation, so a solution file left behind by a
killed run cannot validate for the next unit. Before each live
unit the harness also checks free space on the filesystems it writes to (the evals tree, `$HOME` for compiler caches,
`/tmp`). Below `OPENBEAST_EVAL_MIN_FREE_GB` (default 5; `0` disables) it records
that unit as `reason: low_disk` and stops starting units, so a disk filled
mid-sweep doesn't turn the rest of the run into ENOSPC failures;
`benchmark_all.py` then stops the sweep instead of loading the next model.

`--cache-only` mode replays banked verdicts into a results file — no
server start, no live calls, cache misses are recorded as
`skipped_cache_miss` for visibility. It has no live host (`gpu`/`server`
are null, `cache_only: true`), so the leaderboard refuses it: seated, it
was a second `unknown-host` row for the model. To rescore banked runs
after a scoring change, use `scoring.py --rebuild` — which refuses (exit 1,
`leaderboard.json` untouched) when it finds no readable `eval-*.json` at
all, instead of rebuilding to an empty board; results files that exist but
are all ineligible still rebuild to an empty one. With no server to read the
reasoning budget from, it replays the `.rbN` era of the model's newest
live results file (it prints which); `--reasoning-budget N` picks one
explicitly (`-1` = the uncapped legacy era). A replay with any cache miss
is also flagged `skipped_cache_miss`, so a replay against the wrong era
reads as one at a glance.

**The era hash, and the experiment flags that fork it.** Item (e) above is
one 16-hex hash over five files, and `./scripts/eval-era.sh` prints it
(`--files` lists them with their own hashes; `--check <hash>` exits 1 if
the tree has moved — a campaign asserts it at start and between stages, so
rows from different code are never compared as if they were the same).
Two experiment modes add their own era component on top, so their rows never
replay against, or as, a baseline: `benchmark_all.py --greedy` (greedy
decoding, the low-churn mode; env `OPENBEAST_EVAL_GREEDY=1` for `run_eval.py`,
which has no flag) and `--packs` on either runner (Tier-3 language awareness
packs — `agents/packs/<lang>.md` injected for tasks in that language, zig
only today, plus the probed host pack for every unit when it is non-empty;
a `pack1-<sha8>` era on the units that get either; env `BEAST_PACKS=1`). Both are
leaderboard-ineligible experiment rows, and that is enforced: `benchmark_all`
turns `--greedy`, `--packs` and beast-assist diagnostics into
`--no-leaderboard`, and `scoring.update_leaderboard` / `scoring.py --rebuild`
refuse any results file whose `harness` records one of those arms (or an
escalation component), a fast suite, or infrastructure rows
(`skipped_cache_miss`, `server_unhealthy`, `setup_failed`, `server_error`,
`env_error`, `low_disk`) — see `scoring.ineligibility_reasons`. A `--escalate` arm (beast-lang's
confirmed-fix card riding on beast-assist's diagnostic) is wired and opt-in
(`BEAST_ESCALATE=1` with `BEAST_ASSIST=1`), runs under its own `esc1-<sha8>`
era and is leaderboard-ineligible; its A/B has not run.

**Agent switches.** Six env switches change what the agent itself does:
`OPENBEAST_AGENT_THINKING=on|off` (`run_eval.py --thinking`),
`OPENBEAST_AGENT_SEED=<int>` (`--seed`), `OPENBEAST_AGENT_VERIFY_DONE=1`
(G9, a one-time "re-run your test against the destination" reply to
`task_done`), `OPENBEAST_DIAG_EDIT_HINT=1` (G13, failing diagnostics end
with "fix the first error with edit_file at line N"),
`OPENBEAST_AGENT_ENV_BLOCK=1` (G22, a probed environment block appended to
the task message: OS, toolchain versions, CLI tools, workdir, the task's
paths and `/tmp/eval_*` dirs) and `OPENBEAST_AGENT_WALL_WARN=1` (G23, one
transient notice at the first turn past 50% and past 80% of the unit's
wall, which `run_agent` passes as `OPENBEAST_AGENT_WALL_S`). They form one
key component, `ag-<parts>` (`run_eval.agent_switches`; `envb`, `wwarn` for
the last two), which is omitted when all are unset. They are stamped in
`harness.agent_switches` and on every row (`agent_thinking`, `agent_seed`,
plus `agent_verify_done`, `diag_edit_hint`, `agent_env_block`,
`agent_wall_warn` when set). So an arm under any of them never
replays, or banks into, default rows, and `--no-cache` is not needed. A
k-seed repeat (G12) is three runs: `--seed 1`, `--seed 2`, `--seed 3`.

**What the key does not see, and the opt-in env era.** The model is keyed by
its alias, not its bytes; the llama.cpp build, the KV/context serve flags and
the validator toolchains (zig/go/rustc/gcc, Python) aren't in the key either.
So a quant regenerated under the same alias, or a zig upgrade, replays the
old verdicts. Every live run now computes an `env1-<sha8>` fingerprint over
exactly those (`run_eval.env_fingerprint`: a weight pinned in
`scripts/weights.registry` counts as its sha256, an unpinned one as its size +
mtime, so the file is never hashed). The fingerprint is stamped in
`harness.env` / `harness.env_component` and on every live row (`env_fp`)
(the run's era hash is stamped beside it as `harness.era`, so rows compare
on the era rather than on the repo commit), and a
replayed row banked under a different or unrecorded environment is counted
(`summary.env_drift_replays`, printed as `ENV DRIFT`). It enters the cache
key only with **`OPENBEAST_EVAL_ENV_ERA=1`**, because turning that on starts
a new era for every row. Set it for campaigns that pair arms across days or
across a weight or toolchain change. It's off by default, so existing keys
stay reachable. `--cache-only` takes the fingerprint from the model's last
live results file.

Cache files: `evals/cache/{model_slug}.{task_id}.{spec_hash}[.mi{N}].{ctx_hash}.json`
(the `.mi{N}` segment carries the effective max-iter when a run threads one).
Tiny (<1 KB each), atomically written via `tmp+rename`, gitignored.

## Tool-selection efficiency (per-model)

`evals/tool_efficiency.py` reads `agents/logs/agent-*.jsonl` and reports
how each model uses its tools. The leaderboard ranks capability (problem-
solving + language breadth); this view ranks *how* the model gets there. Same data the agent already
writes, no new measurement needed.

| Metric | What it means |
|---|---|
| `iters_avg` | Mean iteration count per task. Lower = better planning. |
| `tools/T` | Mean tool invocations per task. Higher might indicate thrashing. |
| `edit:wr` | `edit_file:write_file` ratio. Higher = more targeted; <1 = wasteful overwrites. |
| `bash/T` | Bash calls per task. Lower = more targeted use of dedicated tools. |
| `rd_dup` | Per-log average of (`read_file` calls / unique paths read). 1.0 = no rereads within a task; >2.0 = thrashing. |
| `agent` | `start_agent` / `start_skill_agent` / `skill` calls (plus legacy `list_skills`/`load_skill` in pre-2026-07-08 logs). |

Aggregates across every `agents/logs/agent-*.jsonl` (top-level + sub-agent
runs). The model identifier is whatever the `start` record stored —
usually the llama-server `-a` alias (e.g. `qwen-27b-q5`), distinct from
the longer leaderboard `model_name` used elsewhere. For a multi-model
sweep, each model's logs separate naturally so the table becomes a real
cross-model comparison.

## Distribution

**Base totals (v4):** 24 easy · 58 medium · 55 hard = **137 base task IDs**
across 12 categories. Flattening multi-language variants gives **291 effective
test units**: 106 single-variant tasks + 185 variant units from 31 variant
base tasks.

Difficulty weights (used in accuracy scoring): easy=1, medium=1.5, hard=2 — for
multi-variant tasks, divided by variant count. Total weighted points are
preserved (a hard task with 6 variants scores 6 × (2.0/6) = 2.0 — same as a
hard single-variant task).
Time budgets (used in speed scoring): easy=30s, medium=90s, hard=300s.

### Multi-language variants

**31 of the 137 v4 tasks** have language variants. Each variant is its
own scored test unit; the leaderboard reports per-language accuracy via
`scoring.py --by-language`.

**Supported languages:** Python (`a`), Go (`b`), C (`c`), C++ (`d`),
Rust (`e`), Zig (`f`). Full 6-language coverage on 30 of the 31 variant tasks;
`122_gemm_blocked` is perf-flavored with no Python variant (Go / C / C++ /
Rust / Zig only).

**Variant ID convention:** stable letter suffix per language so adding new
languages doesn't renumber existing variants.

| Tier | Variant base tasks |
|---|---|
| Easy (8) | 31_is_power_of_two · 32_dot_product · 71_reverse_list · 73_count_vowels · 74_palindrome · 82_sigmoid · 92_popcount · 100_constant_time_compare |
| Medium (13) | 11_bst · 19_three_way_quicksort · 20_priority_queue · 36_black_scholes · 38_monte_carlo_pi · 51_toposort · 52_unionfind · 54_astar · 61_extgcd · 62_crt · 63_det · 65_miller_rabin · 136_gf256 |
| Hard (10) | 27_brainfuck_interpreter · 115_fft · 122_gemm_blocked *(5-lang)* · 123_nbody · 127_aes_keysched · 137_pollard_rho · 148_convex_hull · 155_tonelli_shanks · 158_karatsuba_bytes · 159_ntt_convolution |

All variant tasks are audited end-to-end via `python3 tests/audit_variants.py`;
reference impls live in `evals/refs/`.

**Build commands per language:**

| Lang | Build | Run |
|---|---|---|
| python | (none) | `python3 sol.py < input.txt` |
| go | `go build -o sol sol.go` | `./sol < input.txt` |
| c | `gcc -O2 -std=c11 -Wall -Wextra -o sol sol.c` | `./sol < input.txt` |
| cpp | `g++ -O2 -std=c++17 -Wall -Wextra -o sol sol.cpp` | `./sol < input.txt` |
| rust | `rustc -O sol.rs -o sol` | `./sol < input.txt` |
| zig | `zig build-exe -O ReleaseFast sol.zig` | `./sol < input.txt` |

> **Zig 0.16 idioms** for variant authors:
> - Entrypoint: `pub fn main(init: std.process.Init) !void`
> - I/O: `std.Io.File.stdin()` / `std.Io.File.stdout()` (the old `std.io`
>   namespace is gone)
> - Reading: `takeDelimiter('\n')` returns `?[]u8` (null at EOF), or use
>   `appendRemainingUnlimited(arena, &list)` to slurp all of stdin
> - Writing: write to the writer's interface, then `try sout.flush()`
> - Compile time: ~7-8s per file (cold). Acceptable for the audit; counts
>   in elapsed time during model runs.

### Category × difficulty (v4)

| Category | Easy | Medium | Hard | Total |
|---|---:|---:|---:|---:|
| Algorithms & DS | 3 | 11 | 7 | **21** |
| Concurrency & Systems | 1 | 5 | 7 | **13** |
| Distributed / SysDesign | 3 | 4 | 4 | **11** |
| LLM / ML | 3 | 2 | 2 | **7** |
| Mathematical Finance | 2 | 5 | 6 | **13** |
| Performance & HW Opt | 2 | 2 | 2 | **6** |
| Physics | 1 | 3 | 5 | **9** |
| Probability & Stats | 1 | 4 | 3 | **8** |
| Pure & Abstract Math | 2 | 7 | 11 | **20** |
| SWE / DevOps | 3 | 9 | 2 | **14** |
| Security | 3 | 3 | 5 | **11** |
| Signal Processing & DSP | 0 | 3 | 1 | **4** |
| **Totals** | **24** | **58** | **55** | **137** |

### Subcategory drilldown (v4)

Task slugs below are the id stems under `evals/tasks/` (integer prefix orders
execution). This section is generated from the task files — the authoritative
per-category counts also come from `scoring.py --by-category` after a run.

#### Algorithms & DS (21)
- **Computational geometry** (1) — convex_hull
- **Graph algorithms** (3) — toposort, unionfind, astar
- **Hashing structures** (2) — lru_cache, bloom
- **Linear data structures** (2) — priority_queue, reverse_list
- **Parsing** (1) — expression_parser
- **Recursion / interpretation** (2) — flatten_json, brainfuck_interpreter
- **Regex** (1) — ipv4_regex
- **Sorting** (1) — three_way_quicksort
- **String algorithms** (4) — count_vowels, palindrome, aho_corasick, suffix_automaton
- **Trees** (4) — bst, trie_autocomplete, segment_tree_lazy, persistent_bst

#### Concurrency & Systems (13)
- **Async patterns** (4) — retry, debouncer, singleflight, coroutine_scheduler
- **Concurrent data structures** (4) — threadsafe_lru, bounded_queue, concurrent_queue, chase_lev_deque
- **Networking / state machines** (1) — tcp_state_machine
- **Race condition fixes** (1) — race_condition
- **Synchronization primitives** (3) — reentrant_lock, rwlock_writer_pref, lease_mutex

#### Distributed / SysDesign (11)
- **Causal ordering** (1) — vector_clock
- **Consistent hashing** (1) — consistent_hash
- **Cryptographic primitives** (1) — sha256
- **Distributed coordination** (2) — distributed_lock, 2pc
- **Encoding & serialization** (1) — base64
- **Rate limiting** (2) — rate_limiter, sliding_window
- **Replication & consistency** (1) — quorum_kv
- **Service patterns** (2) — circuit_breaker, backoff

#### LLM / ML (7)
- **Activations** (2) — softmax_stable, sigmoid
- **Attention** (1) — attention
- **Caching** (1) — kv_cache
- **Norms** (1) — rmsnorm
- **Position embeddings** (1) — rope
- **Similarity metrics** (1) — cosine_similarity

#### Mathematical Finance (13)
- **Credit risk** (1) — cds_hazard
- **Exotic derivatives** (1) — asian_call
- **Fixed income** (2) — bond, yield_curve
- **Option pricing** (3) — black_scholes, binomial, american_option
- **Risk metrics** (3) — min_variance_portfolio, value_at_risk, sharpe_sortino
- **Term structure models** (1) — vasicek
- **Time value of money** (2) — compound_interest, irr

#### Performance & HW Opt (6)
- **Asymptotic refactoring** (1) — optimize_quadratic
- **Bit-twiddling** (1) — popcount
- **Cache locality** (1) — gemm_blocked
- **Loop optimization** (1) — horner
- **RISC-V assembly** (2) — riscv_array_sum, riscv_bitrev

#### Physics (9)
- **3D rotation / geometry** (1) — rodrigues
- **Classical mechanics** (2) — projectile, damped
- **N-body / orbital mechanics** (1) — nbody
- **ODE numerical integration** (1) — rkf45
- **PDE / wave propagation** (1) — wave_fdtd
- **Quantum mechanics** (1) — quantum_superposition
- **Solid-state physics** (1) — vdw_ferroelectric
- **Thermodynamics** (1) — wien

#### Probability & Stats (8)
- **Combinatorial probability** (1) — birthday_paradox
- **Descriptive statistics** (1) — descriptive_stats
- **Inference** (1) — bayesian_ab
- **Monte Carlo simulation** (1) — monte_carlo_pi
- **Optimal stopping** (1) — secretary_problem
- **Psychometrics** (2) — cronbach, dprime
- **Stochastic processes** (1) — markov_steady_state

#### Pure & Abstract Math (20)
- **Bit operations (math)** (1) — is_power_of_two
- **Iterative numerical methods** (2) — iter_refinement, newton_raphson
- **Linear algebra** (5) — dot_product, det, power_iter, svd, gauss_reduction
- **Number theory** (7) — extgcd, crt, miller_rabin, gf256, pollard_rho, bsgs, tonelli_shanks
- **Polynomial algebra** (5) — fft, durand_kerner, berlekamp_massey, karatsuba_bytes, ntt_convolution

#### SWE / DevOps (14)
- **APIs / web** (1) — api_endpoint
- **Bash / scripting** (2) — bash_script, deploy_rollback
- **CLI tools** (1) — cli_tool
- **Data engineering** (3) — data_transform, csv_to_sqlite, db_migration
- **Debugging** (3) — grep_and_fix, debug_runtime_error, memory_leak
- **File operations** (2) — create_file, edit_file
- **Refactoring** (1) — multi_file_refactor
- **Testing** (1) — write_tests

#### Security (11)
- **Auth & access control** (1) — login_ratelimit
- **Cipher implementation** (3) — aes_keysched, rsa, uov
- **Cryptographic primitives** (3) — pbkdf2, hmac_verify, hmac_scratch
- **Input validation** (1) — email_regex
- **Side-channel safety** (1) — constant_time_compare
- **Token management** (1) — jwt
- **Vulnerability remediation** (1) — sql_injection

#### Signal Processing & DSP (4)
- **FIR filter design** (1) — fir_lowpass
- **Frequency-domain analysis** (1) — dsp_freq_analysis
- **IIR / recursive filters** (1) — biquad_iir
- **Tone detection (Goertzel)** (1) — goertzel

## Task JSON schema

Single-variant (legacy form, used by 106 of the 137 v4 tasks):

```json
{
  "id": "145_segment_tree_lazy",
  "name": "Segment tree with lazy propagation",
  "difficulty": "hard",
  "category": "Algorithms & DS",
  "subcategory": "Trees",
  "max_iter": 30,
  "setup": "mkdir -p /tmp/eval_segtree",
  "task": "Create /tmp/eval_segtree/seg.py with class SegTree …",
  "validation": {"type": "python", "script": "import importlib.util; …; print('OK')"},
  "cleanup": "rm -rf /tmp/eval_segtree",
  "pre_validate": "..."
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Filename stem; integer prefix orders execution. |
| `name` | yes | Human-readable. |
| `difficulty` | yes | `easy` / `medium` / `hard`. Drives weight + time budget. |
| `category` / `subcategory` | yes | Used by `--by-category`. |
| `max_iter` | yes | Per-task agent iteration cap. Default fallback is 15. |
| `setup` | optional | Shell command run BEFORE the agent. Idempotent fixture creation. |
| `task` | yes | Prompt sent to the agent. |
| `validation` | yes | `type` is `python` or `bash`; `script` returns exit 0 on success. |
| `cleanup` | yes | Shell command run AFTER validation. Removes fixtures. |
| `pre_validate` | optional | Shell command run between agent and validation. **Re-asserts harness fixtures** that the agent may have corrupted during testing. Used by `17_deploy_rollback`. |

### Multi-language variants (Phase 3 schema, Phase 4 rollout)

A task may declare a `variants` array. Each variant becomes its own scored
entry with effective id `{base_id}_{variant_id}`, weight = `difficulty_weight /
num_variants`. Hard task with 6 variants → each is worth ~0.333 (= 2.0 / 6).
5/6 passing → 1.667 earned.

```json
{
  "id": "145_segment_tree_lazy",
  "name": "Segment tree with lazy propagation",
  "difficulty": "hard",
  "category": "Algorithms & DS",
  "subcategory": "Trees",
  "max_iter": 30,
  "variants": [
    {"id": "a", "language": "python", "setup": "...", "task": "Create /tmp/eval_segtree/seg.py …", "validation": {...}, "cleanup": "..."},
    {"id": "b", "language": "go",     "setup": "...", "task": "Create /tmp/eval_segtree/seg.go …", "validation": {...}, "cleanup": "..."},
    {"id": "c", "language": "c",      "setup": "...", "task": "...", "validation": {...}, "cleanup": "..."},
    {"id": "d", "language": "cpp",    "setup": "...", "task": "...", "validation": {...}, "cleanup": "..."}
  ]
}
```

Variant fields override the base. Filtering by base id selects all variants;
filtering by effective id selects one.

## Scoring (v2 — capability, since 2026-07-10)

The ranking metric is **CAPABILITY**, a problem-solving-led split of the old
fused accuracy. Base problems are grouped by `base_id`; a base problem of
difficulty weight `d` with `k` language variants of which `p` passed contributes:

```
# per BASE problem (d = DIFFICULTY_WEIGHTS[diff], k = variant_count, p = passed langs)
problem_solving  = 100 × Σ d·[p ≥ 1]      / Σ d               # solved in ≥1 language
language_breadth = 100 × Σ_{p≥1} d·(p/k)  / Σ_{p≥1} d         # ports passed, among solved
CAPABILITY       = 0.75 × problem_solving + 0.25 × language_breadth   # ← ranking key (SCORE)

# retained columns
accuracy    = 100 × Σ(d/k × passed) / Σ(d/k)   # legacy v1 metric (per-entry weighted pass rate)
spd (tok/s) = sustained DECODE rate, token-weighted from the server log's
              print_timing (the model's true generation speed, MTP included) —
              NOT tokens/wall, which is ~70% tool-exec + prefill. `~` prefix =
              isolated-benchmark estimate for runs that predate decode logging
              (2026-07-08); see DECODE_ESTIMATES + decode_from_server_log().
speed       = 100 × mean(max(0, 1 − elapsed/time_budget)) over passed tasks  # legacy factor
```

**Why capability, not accuracy.** v1 required passing all 6 language ports for
full credit, so *solving* a problem in one language earned only 1/6 — fusing
problem-solving with language-porting and weighting them equally. **Porting a
working solution across languages is increasingly automatable** (LSP feedback,
MCP language servers, in-context translation), so v2 weights *solving* heavily
(0.75) and breadth lightly (0.25). Difficulty weights (1/1.5/2) and equal
language weighting are unchanged; singletons and full-6-language solves score
identically to v1 — only partial-language cases rebalance. v1 `accuracy` is kept
as a column. Full changelog in `scoring.py`'s module docstring; the switch is
narrated in [`docs/RESULTS.md`](../docs/RESULTS.md) "Scoring v2".

Ranking is by **capability** first, then problem_solving, then hard pass count,
then speed. Tokens and API-equivalent cost are tracked separately (not part of
the rank). Leaderboard readout columns: SOLVE / LANG / SCORE (all shown as %)
→ SPD (sustained decode tok/s; ~ = estimate) → TOKENS (total prompt+completion) → WALL → PASS → T/O. (The
legacy v1 accuracy and per-tier pass rates live in each entry's JSON
`accuracy`/`breakdown` + `scoring.py --by-category`, not the at-a-glance readout.)

### Reading the leaderboard columns (detailed notes)

*(Relocated verbatim from the primary README to keep it lean.)*

**On SPD (sustained decode tok/s):** this is the model's *real* generation speed with MTP — measured from the server's per-token timing — **not** completion-tokens÷wall-clock. The latter is ~70% tool-execution (compiling/running code) + prefill, which made a 315 tok/s model read as "95"; it's a workload number, and WALL already captures total agentic time honestly.

**Why the `~` estimates (fallback, currently unused):** decode is matched to each run's tee'd server log by timestamp (`decode_from_server_log`), which finds a real log for **every** v4 run — so all current rows are server-measured. If a run ever has no decode log, its SPD falls back to an isolated-benchmark estimate shown with a leading `~` (or `—` if none is defined). Re-running with logging always replaces it with a measured value.

**T/O** is the number of units whose agent was killed — at the wall timeout or by a signal (agent exit < 0). Their pass/fail still counts exactly as before; the column is there because a row with several kills measured the clock as much as the model. A killed unit's tokens read 0 until the runner reports them on the way down.

**A stopped run leaves no agents behind.** SIGTERM or SIGINT to `run_eval.py`
or `benchmark_all.py` (`./scripts/job.sh stop`, Ctrl-C) kills every agent
process tree before the process exits, including one that was still inside
`Popen` when the signal landed. The check for a stop, the spawn and the
registration of the agent's group run under one lock (`_spawn_agent`), and
the handler waits on that lock before it reaps. A signal that interrupts the
main thread's own spawn is re-raised once the child is registered
(`tests/test_benchmark_signal_reap.py`).

**† and ‡ after a row are provenance marks, printed by `scoring.py --show` with one footnote line per marked row.** Each entry carries `provenance`: the repo commit and dirty flag, the engine build, the reasoning budget and `--jobs`. **†** = the row is missing at least one of those (it predates the stamp), so nothing can be said about its regime. **‡** = everything is recorded and something differs from row 1 — the footnote names what (for example `engine build 10254 (row 1: 9690); jobs 4 (row 1: unrecorded)`). No row is re-ranked or dropped for either mark; they say which rows are not like-for-like. On today's board rows 2 and 3 (Qwen3.8) are ‡ and the rest †.

*Qwen 27B Q5_K_XL ran `-np 6` with 100/291 units cache-resumed, so its Wall isn't comparable to the serial `-np 1` MTP rows.*

### NVFP4 — real target use case (why it ranks low but is NOT useless to benchmark)

NVFP4 is a COMPUTE-optimized, BATCHED-SERVING quant. Its win condition is concurrent / multi-request inference (`-np >= 4`, MTP off) and prefill, where Blackwell's native FP4 tensor cores dominate. Its intended home is datacenter serving (vLLM / TensorRT-LLM on B100/B200) handling many users at once. We measured the crossover directly: NVFP4-27B BEATS the K-quant by +22% at `-np 8` (its lead peaks ~np8 and converges by np16).

It LOSES the single-user, single-stream case (`-np 1`, chat/agent) because that regime is memory-BANDWIDTH-bound, not compute-bound — and this NVFP4 build is a LARGER file than the K-quant (only the FFN/expert weights are 4-bit; attention stays 8-bit FP8→Q8_0), so it reads more bytes per token and decodes slightly slower than a leaner Q4/Q5. This is physics, not misconfiguration: the 5090's FP4 cores ARE engaged (`BLACKWELL_NATIVE_FP4=1`, full ~307 tok/s decode); no driver/CUDA/flag change alters bandwidth-bound decode.

**Decision rule for OpenBeast:**
- Single-user local rig (this repo's default target): **K-quant MTP (Q5/Q4)**.
- Concurrent agent/worker FLEET endpoint (`docs/TODO.md` multi-node, non-MTP high `-np`): **NVFP4** is the candidate quant.

We still benchmark NVFP4 to (a) confirm it's capability-equivalent (so the fleet role doesn't cost quality) and (b) size that role — NOT because it's the interactive pick. Full analysis: [`docs/RESULTS.md`](../docs/RESULTS.md) "NVFP4".


## v4 leaderboard — per-language & aggregation analysis

*(Relocated from the primary README to keep it lean; full context for the capability leaderboard.)*

**v4 per-language accuracy** (difficulty-weighted % over the 31 variant tasks +
the Python-bucketed single-language tasks; same methodology as the v3.5 table
below. Bold = top, italic = floor per column):

| Model | Python | C | C++ | Go | Rust | Zig |
|---|---:|---:|---:|---:|---:|---:|
| Qwen 27B Q5_K_XL | **98.6** | 92.7 | 87.7 | **97.9** | **100.0** | 60.5 |
| Qwen3.8 27B Q5_K_XL | 97.6 | 87.7 | 92.7 | 95.8 | 92.7 | 30.3 |
| Qwen3.8 27B Uncensored Q5_K_M | 97.7 | 91.7 | 96.9 | 89.6 | 89.6 | _25.1_ |
| Qwen 27B MTP Q5_K_XL | 96.7 | **96.9** | 96.9 | 96.9 | 96.9 | 66.6 |
| Qwen 35B-A3B MTP MoE Q4_K_M | 97.3 | 85.4 | 87.7 | 85.4 | 95.8 | 34.5 |
| Qwopus 27B v2 MTP Q5_K_M | 95.2 | 84.5 | 91.9 | 96.9 | 96.9 | 44.7 |
| Qwen 35B-A3B NVFP4 MTP | 95.3 | 87.7 | 92.7 | _80.2_ | **100.0** | 32.4 |
| Qwen 27B NVFP4 MTP | 93.7 | 89.6 | **100.0** | 90.6 | 96.9 | **76.0** |
| Qwen 35B-A3B MoE Q4_K_M | _93.5_ | _83.5_ | 88.7 | _80.2_ | 97.9 | 38.6 |

Raw pass counts (difficulty-blind, `passed/count`) tell the plainer story:

| Model | Python | C | C++ | Go | Rust | Zig |
|---|---:|---:|---:|---:|---:|---:|
| Qwen 27B Q5_K_XL | 133/136 | 29/31 | 28/31 | 30/31 | 31/31 | 20/31 |
| Qwen3.8 27B Q5_K_XL | 133/136 | 28/31 | 29/31 | 30/31 | 29/31 | 9/31 |
| Qwen3.8 27B Uncensored Q5_K_M | 133/136 | 29/31 | 30/31 | 28/31 | 28/31 | 7/31 |
| Qwen 27B MTP Q5_K_XL | 133/136 | 30/31 | 30/31 | 30/31 | 30/31 | 20/31 |
| Qwen 35B-A3B MTP MoE Q4_K_M | 131/136 | 27/31 | 28/31 | 27/31 | 30/31 | 11/31 |
| Qwopus 27B v2 MTP Q5_K_M | 130/136 | 27/31 | 29/31 | 30/31 | 30/31 | 14/31 |
| Qwen 35B-A3B NVFP4 MTP | 128/136 | 28/31 | 29/31 | 25/31 | 31/31 | 11/31 |
| Qwen 27B NVFP4 MTP | 130/136 | 28/31 | 31/31 | 28/31 | 30/31 | 24/31 |
| Qwen 35B-A3B MoE Q4_K_M | 129/136 | 27/31 | 28/31 | 25/31 | 30/31 | 12/31 |

Both tables are regenerated from `python3 evals/scoring.py --by-language`
(2026-10-09) and cover all nine v4 rows. The Qwen3.8 rows come from a later
harness era with `--jobs 4`, so read their columns against each other, not
against the July rows.

Three things fall out. **(1) Base ≈ MTP is a per-language dead heat** — Qwen
27B Q5_K_XL and its MTP twin are identical
on Python (133/136) and Zig (20/31), never more than 2 units apart anywhere —
consistent with MTP being lossless; the weighted table's cpp/rust swings are single-task
noise amplified by difficulty weighting. **(2) Zig is the discriminator.** Every
model clears 80–100 % on the five mainstream languages, so those columns barely
separate the field — but Zig fans out from **25.1 % to 76.0 %**.

**(3) ⚠️ The headline Acc is ~82 % a Python contest — mind the aggregation.** The
Python bucket (106 single-language tasks at full weight + Python's 1/6 share of
the 31 variants) carries **~82 % of the total weighted score**; the other five
languages *share the remaining ~18 %*. So the per-language table's six visually-
equal columns are nothing like equal in the leaderboard Acc, and **the table does
not proxy the ranking.** Concretely, for base vs. MTP the aggregations disagree:

| Aggregation | Base | MTP | Winner |
|---|---:|---:|:--|
| Overall Acc (leaderboard, Python-dominated) | 96.62 | 95.63 | Base +0.99 |
| Equal-language average (6 langs, equal) | 89.56 | 91.80 | **MTP +2.23** |
| Raw total pass | 271 | 273 | **MTP +2** |

Base holds #1 **only** on the Python-dominated overall metric — and there the two
*tie* on raw Python count (133/136 each); base merely cleared marginally harder
Python tasks. By the equal-language view (which is how we weight languages *within*
the per-language table) and by raw pass count, **MTP leads.** We are leaving the
ordering as-is for now, but the "base is #1" claim is an artifact of Python's
weight share, not a real quality edge — they are the same lossless weights.

The deeper lesson: difficulty re-weighting was tested (1-2-3) and is a **dead
lever** — every model's pass rate is flat across easy/medium/hard, so no ratio
reorders the board. Language variants are kept **equally weighted on purpose** to
show the true distribution of ability. The honest fix for the ~1-point spreads
here is **multi-run averaging with mean-variance** — a single run has real
run-to-run noise, and a 3× run would very likely put base and MTP inside each
other's error bars. That is acknowledged future work, not yet budgeted.

### v3.5 — per-language accuracy

**At-a-glance: per-language accuracy** (variant tasks, % accuracy; bold = top, italic = floor):

| Model | Python | C | C++ | Go | Rust | Zig | Best at |
|---|---:|---:|---:|---:|---:|---:|---|
| Qwen 27B Q5_K_XL | **99.9** | **93.2** | 90.3 | 93.2 | **96.1** | **66.9** | Python, C, Rust, Zig |
| Qwen 27B Uncensored Q5_K_P | 98.3 | 90.3 | **93.2** | **96.1** | 93.2 | 55.2 | C++, Go |
| Qwen 35B-A3B MoE Q4_K_M | 97.8 | _82.5_ | _83.5_ | 80.5 | _83.5_ | 40.9 | — |
| Gemma 4 31B-it Q5_K_XL | 94.3 | 88.3 | 86.4 | 94.2 | 91.2 | 54.5 | — |
| Qwen 35B-A3B Uncensored Q4_K_M | _94.2_ | _82.5_ | 85.4 | _78.6_ | **96.1** | _15.6_ | Rust |

The **Zig spread is enormous** (66.9 → 15.6) and the strongest discriminator on the suite. Python is saturated across the board, so pick a smaller, faster model if you only ship Python. **Use 27B Q5_K_XL for Python, C, and Zig**; **27B Uncensored for Go and C++**; the MoE variants are useful when raw speed matters more than top-end accuracy.

## How token tracking works

Tokens are recorded per task and summed per run. Three numbers per task:
`tokens_prompt`, `tokens_completion`, `tokens_total` (their sum). They come
from the standard OpenAI-compatible `usage` field that llama.cpp returns on
every API call. The agent runner accumulates them across iterations.

Understanding what each number means matters — especially for comparing
models, interpreting cost-equivalent estimates, and reasoning about thinking
behavior in Qwen-family models. The two numbers are not symmetrical and the
ratio is informative.

### Input (prompt_tokens) — everything the model PROCESSES on a single API call

For one API call, `prompt_tokens` is the total of:
- The system prompt (soul file + tool guidance) — a few thousand tokens
- The original user task
- *Every prior assistant message* (the conversation history)
- *Every prior tool call's arguments and result* — these dominate; a single
  `read_file` of a 500-line file is ~2-5K tokens
- Any chat-template scaffolding (turn markers, role tags)

### Output (completion_tokens) — everything the model GENERATES on that call

- The visible response text
- Any tool-call function arguments
- Any `<think>...</think>` reasoning block, if the model produced one (Qwen
  thinking-mode output is part of completion — see below)

### Why prompt >> completion in our totals (typically 13:1 or worse)

A multi-iteration agent **re-sends the entire conversation on every iteration**.
That means the prompt grows monotonically across iterations:

| Iteration | Prompt size (illustrative, 7-iter task) |
|---:|---:|
| 1 | ~5K (system + task) |
| 2 | ~10K (+ iter-1 stuff) |
| 3 | ~15K |
| ... | each round adds the prior round's output + tool results |
| 7 | ~30–50K |

Sum of all 7 prompts ≈ 160K. Sum of all 7 completions might be 5–10K. That's
where the 13:1 skew comes from — not from chatty output, but from monotonic
prompt accumulation. The KV cache makes this efficient at runtime (most of
the prefix is cached), but the API still counts every token in
`prompt_tokens` regardless of cache hits.

### Where thinking tokens land — and what Qwen does with them

Qwen3.6 (and Qwen3-A3B, both used in this stack) supports a thinking mode
where the model emits `<think>...</think>` blocks before its visible
response. Two things to know:

**1. Thinking tokens count toward `completion_tokens` of the iteration that
generated them.** They're produced just like any other output: GPU cycles
consumed, tokens generated, fully counted by the API. A task with
disproportionate completion tokens (10–15% of total instead of the typical
5–7%) is a signal the model thought a lot.

**2. Qwen's chat template STRIPS thinking from subsequent prompts.** This is
the critical design choice. Verified in this stack: the chat template
referenced by `/props` includes:

```
{%- if message.reasoning_content %}
{%- else %}
    {%- if '</think>' in content %}
       (strip block before reusing in next turn)
```

When iteration N's assistant message gets fed back into iteration N+1's
prompt, the `<think>` block is removed first. Confirmed in our agent logs —
assistant messages stored in JSONL contain only the visible response, no
`<think>` tags.

**Why this matters:** if thinking persisted into the next prompt, a 7-turn
conversation could compound 5–20× more context. Qwen's template handles this
correctly out of the box, which is why our prompt growth is "merely linear"
across iterations rather than exponential.

```
                                Counted in:
─────────────────────────────────────────────────────────
<think> output during iter N    →  completion_tokens(iter N)
visible response during iter N  →  completion_tokens(iter N)
tool call args during iter N    →  completion_tokens(iter N)
                                       │
                                       ▼
                In iter N+1's prompt:
                  <think>           →  STRIPPED, not in prompt_tokens
                  visible response  →  KEPT, in prompt_tokens
                  tool call args    →  KEPT
                  tool result       →  KEPT
```

So thinking is a per-iteration cost only. It doesn't echo forward.

### The cost-equivalent estimate caveat

When the leaderboard or a post-mortem quotes "$X of equivalent Sonnet/Opus
spend," that's `prompt × $input_rate + completion × $output_rate` — useful
for ranking but **not literally what you'd pay a frontier API**. Two reasons:

1. **Frontier APIs bill cached prefix at ~10% of fresh input rate.** Our
   `prompt_tokens` lumps fresh and cached together (llama.cpp's `usage`
   doesn't separate them). Most of our prompt is cache hits — sometimes 90%+
   of the total. A frontier API would charge that 90% at the discounted
   rate.

2. **Thinking tokens are billed differently across providers.** Anthropic
   counts them as output (same as us). Some other providers separate
   reasoning from completion in their billing. Worth checking the specific
   provider's policy if you're translating these numbers for a real
   migration estimate.

For comparing models *within our local stack* — which is the primary use of
the TOKENS column — these caveats don't matter. The accounting is consistent
across all models we benchmark, so the ranking is honest.

For external comparison, OpenCode's `opencode stats` command separates
fresh-input from cache-read explicitly and is the better source for accurate
cost translation.

### Summary

| What | Counts toward | Persists into next prompt? |
|---|---|---|
| System prompt processing | `prompt_tokens` (every iter) | Yes (cache-hit) |
| Prior assistant visible text | `prompt_tokens` (next iter) | Yes |
| Prior `<think>` blocks | `prompt_tokens` (next iter) | **No** — Qwen template strips |
| Prior tool call args + results | `prompt_tokens` (next iter) | Yes (often the biggest source) |
| Current iter's visible response | `completion_tokens` | (becomes input next iter) |
| Current iter's `<think>` block | `completion_tokens` | (stripped before next iter) |
| Current iter's tool call args | `completion_tokens` | (becomes input next iter) |

## Result file format

Each run produces `evals/results/eval-{model_slug}-{timestamp}.json` with:

```json
{
  "timestamp": "...",
  "model": "...", "model_slug": "...", "base_url": "...",
  "suite_version": "v4",
  "gpu": {"name": "...", "host_id": "...", "gpu_count": 1, "gpus": [...]},
  "inference_engine": {"name": "llama.cpp", "build": "...", "commit": "...", "compiler": "...", "source_head": "..."},
  "runtime": {"python": "3.13.x", "platform": "...", "openai": "...", "mcp": "..."},
  "tasks": [
    {
      "id": "145_segment_tree_lazy",
      "name": "...", "difficulty": "hard",
      "model": "...", "passed": true,
      "elapsed_seconds": 27.3,
      "agent_exit_code": 0,
      "validation_output": "OK",
      "tokens_prompt": 18432,
      "tokens_completion": 2104,
      "tokens_total": 20536,
      "base_id": "145_segment_tree_lazy",
      "variant_id": "a",
      "language": "python",
      "variant_count": 4
    }
  ],
  "summary": {"total": 291, "passed": 273, "failed": 18}
}
```

`base_id`, `variant_id`, `language`, `variant_count` are present only for
flattened variant entries. Token fields are 0 if the model server didn't
return a `usage` block (some legacy llama.cpp builds). `suite_version`,
`inference_engine`, and `runtime` are the provenance stamped on every row so
v3.5 and v4 scores never get confused and a result is traceable to the exact
llama.cpp build that produced it. Results are written incrementally
(`tmp`+`rename` after each task), so a crashed sweep keeps every completed
task. `--cache-only` runs record `gpu`/`inference_engine`/`server` as `null`
(no live probe) and `cache_only: true`. `server` (whose `--reasoning-budget`
becomes the `.rbN` cache era) is the llama-server that LISTENs on the
`--base-url` port — resolved from the socket's inode in `/proc/net/tcp`,
else the `--port` on its command line — never simply the first
`pgrep` hit, which could be a sidecar on another port. A remote
`--base-url`, or two indistinguishable candidates, records `{}`. So does
a `--base-url` on a proxy port (beast-gate) in front of llama-server: with
no server config the cache era is the uncapped one even if the server runs
capped, so the run prints a WARNING — point `--base-url` at llama-server's
own port for a capped era.

## Adding a task

1. Pick the next free integer id (currently 163+).
2. Create `evals/tasks/{id}_{slug}.json` following the schema above.
3. Solve the task yourself with a reference implementation. Run the validation
   against your reference. Confirm it passes — and confirm an OBVIOUSLY WRONG
   implementation fails (no false positives).
4. Bash-syntax check happens automatically: `tests/test_scripts.sh` parses
   every task JSON and validates Python validation scripts via `compile()`.
5. If your task seeds buggy code that the agent must fix in place (like
   `06_debug_runtime_error`), do NOT add `pre_validate` — re-running setup
   would clobber the agent's fix.
6. If your setup writes harness fixtures the agent must NOT modify (like
   `17_deploy_rollback`'s `healthcheck.sh`), DO add `pre_validate` re-asserting
   only those fixtures.

## Pitfalls (from real spec defects)

These are the lessons from the 2026-05-06 sweep post-mortem. Read them before
writing a task spec.

1. **Don't substring-lint forbidden libraries.** `assert 'numpy' not in src`
   trips on docstrings that cite the library by name. Use `assert 'import
   numpy' not in src and 'from numpy' not in src`.
2. **Specify input types explicitly.** `b64_encode(data)` with a `str` input
   passed by validation but a `bytes` parameter assumed by the model is a
   coin-flip. Annotate types in the spec.
3. **Specify return-value contracts.** If validation does `assert
   kv.write(...)`, the spec must say what `write` returns.
4. **Re-assert harness fixtures.** Agents have full bash and will overwrite
   fixture files during their own testing. Use `pre_validate` to restore them.
5. **Calibrate perf gates against the slowest valid impl.** A correct linked
   list passes at 50k items in 80ms; `list.pop(0)` at the same size takes 2.5s.
   That's a 30× gap and lets us write a tight `< 1.5s` assertion.
6. **Solve the task yourself.** Validation expectations (e.g. expected match
   list for Aho-Corasick) are easy to miscount by hand. Always run the actual
   algorithm before committing the test.
