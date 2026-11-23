# v5 calibration: the rig's eval-host container against the Mac harness host (2026-11-23)

> **Final (2026-11-23).** Diagnostics only: no board row. The three result
> files are in [`evals/boards/v5/diagnostics/`](../../evals/boards/v5/diagnostics/).

## 1. Summary

- **Question.** Can the RTX 5090 rig's eval-host container
  (`scripts/eval-host-container.sh`, image `openbeast-eval-host:8d1e28442569`,
  Linux) replace the Mac as the harness host?
- **Method.** Rerun one existing board row with only the harness host
  changed. The row is DeepSeek-V4.1-Flash arm T2 (board row `DS-T2`, 65/71,
  harness9 tree `f38a29c7`, era `036638dd`), run from the Mac on
  2026-11-19. The rerun used the same tree, the same command, the same
  Spark pair and the same served model (`DeepSeek-V4.1-Flash-1017`,
  TensorFold, thinking-first T2 switches).
- **Result.** The calibration arm passed 60/71. Two contention-free
  follow-ups reran the units that differed: 2/7 on the flipped units, and
  5/11 on every unit the calibration failed.
- **Verdict.** There is no host effect except one validator bug. Rust's std
  on Linux/glibc imports `dlsym`, and the `122_gemm_blocked` validator
  rejects any binary that imports `dlsym`. So on a Linux host, every
  threaded Rust GEMM is a false FAIL. `122_gemm_blocked_d` failed this way
  in all three attempts. The other differences are run-to-run variation on
  wall-bound hard units, which flip between attempts on the same host.
- **Consequence.** The rig container can be a harness host. Two caveats:
  the dlsym bug, and the Linux network-deny gap (HARNESS_NOTES next-era
  (d)). Q_T2, the RTX 5090 full arm now running in the same container on
  the frozen harness9 tree, is affected only if its Rust GEMM uses threads
  (§5).

## 2. Setup

| | Board `DS-T2` | Calibration (this report) |
|---|---|---|
| Harness host | Mac (M1 Max), macOS 26.6.2 | the rig (Ryzen 9 9950X3D), eval-host container `8d1e28442569`, Linux 7.2.8, glibc 2.41 |
| Tree | harness9 `f38a29c7`, era `036638ddd68698ce` | the same (`~/ob-cal/.claude/worktrees/h9`) |
| Command | T2's: thinking on, 8,192-token turn cap, act nudge, packs v3, diagnostics + edit hint + verify-done, env block, wall warnings, wall ×2, 15 turns per wall-second, `--jobs 4 --slots 4` | the same (`~/openbeast-runs/cal-T2-cmd.txt` on the rig) |
| Server | the Spark pair, TensorFold, `DeepSeek-V4.1-Flash-1017`, reasoning budget 8,192 | the same, through the gate over the tailnet |
| Python | cpython 3.12.16 (pinned) | cpython 3.12.16 (pinned) |
| Toolchains | zig 0.16.0, rustc 1.100.0, go 1.23.3 | the same pins; gcc 14.2.0 (Debian) |
| Host pack | Darwin facts (264 tokens) | Linux facts (56 tokens) |

Two differences come with the host, and neither is a knob. The host pack
describes the host the agent's shell is on (that is its job). The CPU is
the other one. The rig is faster per core than the M1 Max, so the
validators' CPU budgets do not disadvantage it.

## 3. The three passes

| Pass | When (PST) | Units | Passed | File |
|---|---|---|---|---|
| Calibration (cal) | 2026-11-22 15:03 → 21:11 | 71 (full v5) | **60/71** | `eval-deepseek-v4-1-flash-v5-thinking-first-t2-20261122-230335.json` |
| First rerun (r1) | 2026-11-22 21:11 → 23:12 | the 7 units whose verdict differed from `DS-T2`, both directions | 2/7 | `…-t2-rerun-20261123-051155.json` |
| Redo of the fails (r2) | 2026-11-22 23:12 → 2026-11-23 03:57 | the 11 units the calibration failed | 5/11 | `…-t2-redo-fails-20261123-071205.json` |

**Contention during the calibration.** Max caused contention on the rig by
accident during the run. Separately, a 224 GB copy ran from 19:42 to
20:15. It went from the rig's SSD to spark-1, then from spark-1 to spark-2
over the fabric that DeepSeek's tensor parallelism uses. Of the
calibration's losses, `168_linearizability_kv_e`, `168_linearizability_kv_f`,
`190_autograd_mlp` and `192_pike_regex` were running during the copy
window. Max judges the late losses to be contention. r1 and r2 ran with
neither.

**How r1 and r2 ran.** Both used T2's exact command against a temporary
subset of the v5 manifest. The subset kept each unit's own wall and turn
ceiling, and its coverage floor was 0. The subset was deleted after each
run. Each result file therefore records `suite_manifest` `v5-rerun` /
`v5-redo` and `openbeast_dirty: true`; the only dirty file was that subset
manifest. The scripts are `~/openbeast-runs/cal-rerun-flips.sh` and
`cal-redo-fails.sh` on the rig, and the logs are
`~/openbeast-runs/cal-*.out`.

**What r2 is evidence of.** A redo of the fails only can only add passes.
It is evidence about host parity (can the host pass these units at all?),
not a score. The calibration's own 60/71 is the like-for-like figure, and
it stays as recorded.

## 4. Per unit

Twelve units differ from the board in at least one attempt or failed in
the calibration. All 59 other units passed on both hosts. P = pass,
F = fail, — = not in that pass.

| Unit | Board `DS-T2` (Mac) | cal | r1 | r2 | Board elapsed | Reason (rig fails, validator's line) |
|---|---|---|---|---|---|---|
| `122_gemm_blocked_d` (rust) | P | F | F | F | 798 s | all three: `the binary imports dlsym` (**host bug**, §5) |
| `168_linearizability_kv_e` | P | F | P | F | 3,600 s (wall) | cal: exit 101; r2: wrong verdict |
| `173_taskgroup_cancel` | P | F | F | F | 3,600 s (wall) | behaviour mismatch (all three) |
| `175_crdt_orset_rga` | P | F | P | P | 3,600 s (wall) | cal: mismatch |
| `183_heston_little_trap` | P | F | F | F | 3,600 s (wall) | cal: price mismatch; r1: CPU budget; r2: no solution written |
| `190_autograd_mlp` | P | F | F | P | 3,034 s | cal: gradient missing; r1: forward mismatch |
| `168_linearizability_kv_c` | F | P | F | — | 3,600 s | r1: wrong verdict |
| `168_linearizability_kv_a` | F | F | — | P | 3,600 s | cal: CPU budget |
| `168_linearizability_kv_d` | F | F | — | P | 3,600 s | cal: no solution file |
| `168_linearizability_kv_f` | F | F | — | P | 3,600 s | cal: output over the cap |
| `168_linearizability_kv_b` | F | F | — | F | 3,600 s | cal: CPU budget; r2: wrong verdict |
| `192_pike_regex` | F | F | — | F | 3,600 s | search mismatch (both) |

The board's own fails on `168_linearizability_kv_a` to `_d` were the
validator's 12 s CPU budget on the hidden histories: the model's
algorithm, on the Mac as well. Four of the six board passes the
calibration lost (`168_e`, `173`, `175`, `183`) landed at exactly 3,600 s
on the board, which is the wall. They were last-minute passes on the Mac.
`190` ended at 3,034 s.

**Totals.** Calibration against the board: 1 gained (`168_c`), 6 lost,
7 flips. Excluding the host bug: 1 gained, 5 lost. Time per unit over the
71 units is level, rig/Mac = 0.99, and tokens are 0.97.

**The noise reference.** DeepSeek T and T_s1 are the same arm with only
the seed changed, both run from the Mac. They score 61 and 61 with a churn
of 2 (1 gained, 1 lost). Every non-bug unit above is a wall-bound hard
unit. Seven of the eleven flipped verdict between attempts on the rig
itself, with no host change: `168_a`, `_c`, `_d`, `_e`, `_f`, `175` and
`190`. The other four are `173`, `183`, `168_b` and `192`. The last two
failed on the board too. `173` and `183` failed all three rig attempts,
and their board passes were both at the wall. The data do not separate a host effect on those two
from a run of bad draws, and n = 3 per unit cannot. Nothing in their
failure lines is host-specific (behaviour and price mismatches, a CPU
budget the faster CPU does not tighten, a missing file).

**Integrity.** `evals/audit/integrity_scan.py` on the rig over all three
files (`--tz UTC`, original mtimes): 71 + 7 + 11 transcripts, 0
contaminated. The hits were attempts only: the refused `web_search` /
`fetch` tools, recorded as the scan's `web` kind.

## 5. The host bug: `dlsym` in every threaded Rust binary on Linux

**Mechanism (verified 2026-11-23).** Rust's std on Linux/glibc looks up a
pthread symbol at run time with `dlsym` whenever a program spawns a thread
(the minimum-stack query). So any Rust binary that uses `std::thread`
imports `dlsym`. A one-line `std::thread::spawn(..).join()` built with the
container's rustc 1.100.0 shows `U dlsym@GLIBC_2.34` in `nm -D`. The same
holds with the rig's system rustc 1.99.1 on glibc 2.44. The same program
without a thread imports no `dlsym` (both import `dl_iterate_phdr`, which
the validator allows).

**The rule it trips.** The `122_gemm_blocked` validators (every variant,
in `evals/tasks/122_gemm_blocked.json`, `guard_binary`) fail any binary
whose undefined symbols match `_BAD_IMPORT`. That list includes `dlsym`.
The rule exists to stop a BLAS loaded through `dlopen`/`dlsym`, and the
task text allows threads. On macOS the std thread path imports no `dlsym`
(the Mac's `DS-T2` passed the unit), so a threaded Rust GEMM passes there. On Linux, a threaded Rust GEMM fails at the
binary check, before any case runs. Only Rust is exposed: C and C++
threads (pthreads in glibc ≥ 2.34) and Go (`CGO_ENABLED=0`, static) import
no `dlsym`.

**What the three failures do and do not say.** The validator stopped at
the binary check, so the solution's correctness was never measured. The
three rig attempts are unscored rather than wrong. Whether they would have
passed is unknown until they are replayed under a corrected rule (not done:
§6).

**Committed rows hit.** None of the committed v5 board rows. Every Spark
row ran from the Mac. The only `dlopen`/`dlsym` failures on the board are
GLM's `122_gemm_blocked_c`, where the C++ source itself used `dlopen`
(genuine; `GLM-E`'s comment-only match is already footnoted).
The Linux-host files are another matter. Board row `Q38U-L0` did not reach
the binary check (no `gemm.rs` was written). Both of the RTX 5090
profile's L2 diagnostics files (`…-L2-20261122-163812.json`,
`…-L2-20261122-192701.json`) record `122_gemm_blocked_d` as `the binary
imports dlsym`. Those are diagnostics, not board rows, and the L0 → L2
contrast in the [Qwen3.8 report](V5_QWEN38_RTX5090-2026-11-22.md) §5
counts that unit as a fail in L2.

**Q_T2.** Q_T2, the RTX 5090 default's T2 arm, runs in the same container
on the frozen harness9 tree, so it has the same rule. Its
`122_gemm_blocked_d` is affected only if its Rust GEMM uses threads.

**What is done about it.**

- **The fix waits for the next era.** Campaigns are running on the
  current tasks, so no task file or era-hashed file is edited now. The
  proposal is in [HARNESS_NOTES](../HARNESS_NOTES.md#next-era-the-122_gemm_blocked-dlsym-rule-on-linux-2026-11-23).
- **The board handles it with a validator note, by hand, per run.**
  `evals/boards/v5/exclusions.json` names entries by unit and run (board
  id or result file). It has no host- or reason-conditional rule, and Q_T2
  has no row or file yet, so no entry is added now. When a Linux-host run
  lands on the board, check its `122_gemm_blocked_*` rust row. If the
  only failure line is `the binary imports dlsym`, and the binary imports
  no `dlopen` and no BLAS-like symbol, add a `validator_notes` entry
  naming that run. That is the precedent of `GLM-E`'s `122_gemm_blocked_c`
  (a validator false FAIL, footnoted and not adjusted). A FAIL counts as
  not passed either way, so a note and an exclusion give the same score;
  the note is the one the board uses for validator problems. Procedure in
  [MODEL_CAMPAIGN.md](../MODEL_CAMPAIGN.md#eval-host-in-a-container-linux).

## 6. Not done

- **A replay of the three Rust solutions under a corrected rule.** It would
  turn "unscored" into a verdict. It needs a scratch copy of the harness9
  tree with the proposed rule, run on the rig. It was not run because the
  N = 2048 case is a multi-threaded 12 s CPU job and Q_T2 was running on
  the same box. Two of the three final sources were edited through shell
  commands, so rebuild them from the agent workspace snapshot or the
  transcript with care. Do it after Q_T2 finishes.
- **A second full arm on the rig.** One calibration arm plus two partial
  passes is what was run. A clean full arm would give a like-for-like
  second figure against 65. It is not needed for the verdict, because every
  non-bug difference sits on a unit that flips on the rig itself.

## 7. Files

- `evals/boards/v5/diagnostics/eval-deepseek-v4-1-flash-v5-thinking-first-t2-20261122-230335.json`: the calibration arm.
- `evals/boards/v5/diagnostics/eval-deepseek-v4-1-flash-v5-thinking-first-t2-rerun-20261123-051155.json`: r1.
- `evals/boards/v5/diagnostics/eval-deepseek-v4-1-flash-v5-thinking-first-t2-redo-fails-20261123-071205.json`: r2.
- On the rig: the transcripts in `~/ob-cal/.claude/worktrees/h9/agents/logs/`,
  the campaign log in `~/ob-cal/evals/results/campaigns/v5-thinking-first-deepseek-v4-1-flash/`,
  and the run logs in `~/openbeast-runs/cal-*.out`. These result files carry
  no `saved` end time, so a later scan needs `--until`, with the ends from
  §3 (UTC: 05:11, 07:12 and 11:57 on 2026-11-23).
