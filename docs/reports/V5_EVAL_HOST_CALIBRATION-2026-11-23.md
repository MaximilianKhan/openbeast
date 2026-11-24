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
- **Verdict.** No host effect was detected beyond run-to-run variation,
  from one calibration arm (plus two partial passes). Two host effects are
  known. (1) A validator bug: Rust's std on Linux/glibc imports `dlsym`
  for any thread, and the `122_gemm_blocked` validator rejects any binary
  that imports `dlsym`, so a threaded Rust GEMM stops at the binary check
  on a Linux host, unscored. `122_gemm_blocked_d` stopped this way in all
  three attempts; replayed under the proposed rule, all three pass (§5),
  so the bug cost the calibration arm one unit (61/71 under the proposed
  rule). (2) The agent's shell has no network deny on Linux
  (HARNESS_NOTES next-era (d)); it cost board row `Q38U-L0` one excluded
  pass; the integrity scan flagged no shell network access in these passes. The other differences are on
  wall-bound hard units, most of which flip between attempts on the rig
  itself. The data cannot separate a host effect from bad draws on two of
  them, `173_taskgroup_cancel` and `183_heston_little_trap` (§4).
- **Consequence.** The rig container can be a harness host, with the two
  known effects above as caveats. Q_T2, the RTX 5090 full arm now running in the same container on
  the frozen harness9 tree, was affected: its r1 attempt at the unit
  stopped at the same check, and it too passes under the proposed rule
  (§5).

## 2. Setup

| | Board `DS-T2` | Calibration (this report) |
|---|---|---|
| Harness host | Mac (M1 Max), macOS 26.6.2 | the rig (Ryzen 9 9950X3D), eval-host container `8d1e28442569`, Linux 7.2.8, glibc 2.41 |
| Tree | harness9 `f38a29c7`, era `036638ddd68698ce` | the same (`~/ob-cal/.claude/worktrees/h9`) |
| Command | T2's: thinking on, 8,192-token turn cap, act nudge, packs v3, diagnostics + edit hint + verify-done, env block, wall warnings, wall ×2, one turn per 15 wall-seconds (`turns_per_wall_s` 15.0, `evals/run_eval.py` `wall_scaled_turns`), `--jobs 4 --slots 4` | the same (`~/openbeast-runs/cal-T2-cmd.txt` on the rig) |
| Server | the Spark pair, TensorFold, `DeepSeek-V4.1-Flash-1017`, reasoning budget 8,192 | the same, through the gate over the tailnet |
| Python | cpython 3.12.16 (pinned) | cpython 3.12.16 (pinned) |
| Toolchains | zig 0.16.0, rustc 1.100.0, go 1.23.3 (darwin/arm64); C/C++ compiler recorded as `gcc: ?` | zig 0.16.0, rustc 1.100.0, go 1.23.3 (linux/amd64); gcc 14.2.0 (Debian) |
| Host pack | Darwin facts (264 tokens) | Linux facts (56 tokens) |
| Agent shell network | denied by the macOS sandbox | **no deny** (Linux residual gap, HARNESS_NOTES next-era (d)) |
| Diagnostics latency | p50 295 ms, p95 729 ms (75 checks) | p50 64 ms, p95 220 ms (69 checks) |

Every difference that comes with the host, none of them a knob:

- **OS and CPU.** The rig is faster per core than the M1 Max, so the
  validators' CPU budgets do not disadvantage it.
- **The host pack.** It describes the host the agent's shell is on (that
  is its job): 264 tokens of Darwin facts against 56 of Linux.
- **The C/C++ compiler.** The rig's container has gcc 14.2.0; the Mac's
  result file records `gcc: ?` (its compiler version was not captured).
- **The network deny.** The macOS sandbox denies the agent's shell the
  network; on Linux there is no deny. The integrity scan flagged no shell
  network access in these three passes (§4), but `Q38U-L0` lost one pass
  to it.
- **The diagnostics latency.** The push-diagnostics checks ran about 4.6×
  faster at p50 on the rig (from each file's `harness.diag_latency_ms`).
- **The binary format.** ELF against Mach-O, which is where the `dlsym`
  bug (§5) comes from.

## 3. The three passes

| Pass | When (PST) | Units | Passed | File |
|---|---|---|---|---|
| Calibration (cal) | 2026-11-22 15:03 → 21:11 | 71 (full v5) | **60/71** | `eval-deepseek-v4-1-flash-v5-thinking-first-t2-20261122-230335.json` |
| First rerun (r1) | 2026-11-22 21:11 → 23:12 | the 7 units whose verdict differed from `DS-T2`, both directions | 2/7 | `…-t2-rerun-20261123-051155.json` |
| Redo of the fails (r2) | 2026-11-22 23:12 → 2026-11-23 03:57 | the 11 units the calibration failed | 5/11 | `…-t2-redo-fails-20261123-071205.json` |

**Contention during the calibration.** Max caused contention on the rig by
accident during the run. Separately, a 224 GB copy ran from 19:42 to
20:15. It went from the rig's SSD to spark-1, then from spark-1 to spark-2
over the fabric that DeepSeek's tensor parallelism uses. Four of the
calibration's fails were running during the copy window, by the run log's
timing (`~/openbeast-runs/cal-*.out` on the rig; the result files carry no
per-unit times): `168_linearizability_kv_e` and `190_autograd_mlp`, which
passed on the board (losses), and `168_linearizability_kv_f` and
`192_pike_regex`, which failed on the board too. Max judges the late fails
to be contention. r1 and r2 ran with neither.

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
7 flips. Excluding the host bug: 1 gained, 5 lost.

**Time and tokens** (rig / Mac, the calibration file against `DS-T2`'s,
from each row's `elapsed_seconds` and token counts). Over all 71 units the
time is level (sum 0.99, 24.4 h against 24.5 h) and tokens are 0.97, but
that figure is dominated by the 13 units that hit the 3,600 s wall on
either host, which take the same time by construction. On the 58 units
that finished under the wall on both hosts (agent exit 0 in both files):

| Measure (58 units) | Mac | Rig | Rig / Mac |
|---|---:|---:|---:|
| Time, sum | 11.52 h | 12.15 h | 1.05 |
| Time, median of per-unit ratios | | | 1.16 |
| Time, ratio of medians | 412 s | 344 s | 0.84 |
| Total tokens (prompt + completion) | | | 1.03 |
| Completion tokens | | | 0.99 |
| Completion tokens per wall-second | 32.2 | 30.3 | 0.94 |

So on the units that finish, the rig took somewhat longer per unit in
the typical case (the median unit ran 16% longer), while the two medians
point the other way (they are not the same unit). The token counts are
level, so the extra time is not extra work; it is consistent with the
contention in §3 and with the served model's speed over the tailnet, and
this one arm cannot separate the two. The completion rate is per unit
wall time, so it includes tool and validator time, not decode speed
alone.

**The noise reference.** DeepSeek T and T_s1 are the same arm with only
the seed changed, both run from the Mac. They score 61 and 61 with a churn
of 2 (1 gained, 1 lost). Every non-bug unit above is a wall-bound hard
unit. Seven of the eleven flipped verdict between attempts on the rig
itself, with no host change: `168_a`, `_c`, `_d`, `_e`, `_f`, `175` and
`190`. The other four are `173`, `183`, `168_b` and `192`. The last two
failed on the board too. `173` and `183` failed all three rig attempts,
and their board passes were both at the wall. The data do not separate a
host effect on those two from a run of bad draws, and n = 3 per unit
cannot. Nothing in their
failure lines is host-specific (behaviour and price mismatches, a CPU
budget the faster CPU does not tighten, a missing file).

**Integrity.** `evals/audit/integrity_scan.py` on the rig over all three
files (`--tz UTC`, original mtimes): 71 + 7 + 11 transcripts, 0
contaminated. The hits were attempts only: the refused `web_search` /
`fetch` tools, recorded as the scan's `web` kind. The scan's exact command
line was not recorded; the sealed review
(`evals/audit/reviews/ds-t2-cal-integrity-2026-11-23.md`) gives the rescan
command with `--until` per pass.

## 5. The host bug: `dlsym` in every threaded Rust binary on Linux

**Mechanism (verified 2026-11-23).** Rust's std on Linux/glibc looks up a
pthread symbol at run time with `dlsym` whenever a program spawns a thread
(the minimum-stack query). So any Rust binary that uses `std::thread`
imports `dlsym`. A one-line `std::thread::spawn(..).join()` built with the
container's rustc 1.100.0 shows `U dlsym@GLIBC_2.34` in `nm -D`. The same
holds with the rig's system rustc 1.99.1 on glibc 2.44. The same program
without a thread imports no `dlsym` (both import `dl_iterate_phdr`, which
the validator allows). `nm -D` lists `.dynsym` only; the validator's
ELF reader (`_elf`) reads `.symtab` as well, so a check by hand must too.

**The rule it trips.** The `122_gemm_blocked` validators (every variant,
in `evals/tasks/122_gemm_blocked.json`, `guard_binary`) fail any binary
whose undefined symbols match `_BAD_IMPORT`. That list includes `dlsym`.
The rule exists to stop a BLAS loaded through `dlopen`/`dlsym`, and the
task text allows threads. On macOS, Rust's std imports no `dlsym` for a
thread, so a threaded Rust binary passes the binary check there. (The
Mac's `DS-T2` passed the unit; whether its solution used threads was not
checked, and the same source was never built on both hosts.) On Linux, a
threaded Rust GEMM fails at the binary check, before any case runs. Only Rust is exposed: C and C++
threads (pthreads in glibc ≥ 2.34) and Go (`CGO_ENABLED=0`, static) import
no `dlsym`.

**What the three failures do and do not say.** The validator stopped at
the binary check, so the solution's correctness was never measured by the
run. The three rig attempts were unscored rather than wrong. The replay
below measured it.

**The replay under the proposed rule (2026-11-23): all three PASS, and
Q_T2's r1 attempt too.** Four `122_gemm_blocked_d` solutions had stopped
at the binary check: the calibration's three (cal, r1, r2) and Q_T2's r1
attempt ([Qwen3.8 report](V5_QWEN38_RTX5090-2026-11-22.md) §6). Each was
replayed on the rig, after Q_T2, with the rig otherwise idle except for
its daily Qwen3.8 serve.

| Attempt | Transcript (on the rig) | Final `gemm.rs` recovered from | sha256 (first 16) | Original rule | Proposed rule |
|---|---|---|---|---|---|
| cal | `~/ob-cal/.claude/worktrees/h9/agents/logs/agent-20261123-005732-859221fe.jsonl` | 2 `write_file` + 2 `edit_file` | `935d53e4a5a24b92` | FAIL, `imports dlsym` | **PASS**, 6 hidden cases (3 of 3 runs) |
| r1 | `…/agent-20261123-061158-c2800d7d.jsonl` | 1 `write_file` + 4 `edit_file` + 3 `sed -i` | `219083dc77b7fc38` | FAIL, `imports dlsym` | **PASS**, 6 hidden cases (3 of 3) |
| r2 | `…/agent-20261123-081207-77ab0c71.jsonl` | 1 `write_file` + 4 python `str.replace` heredocs | `edef0400ce2441f1` | FAIL, `imports dlsym` | **PASS**, 6 hidden cases (3 of 3) |
| Q_T2 r1 | `~/ob-q38/.claude/worktrees/h9/agents/logs/agent-20261123-231113-84abeed3.jsonl` | 1 `write_file` + 15 `edit_file` | `285e627eedfb2b4a` | FAIL, `imports dlsym` | **PASS**, 6 hidden cases (3 of 3) |

- **Recovering the source.** The `write_file` and `edit_file` calls on
  `/tmp/eval_gemm/gemm.rs` were replayed in transcript order with the
  harness's own semantics (`agents/tools.py`: an exact `old_string` that
  must occur once). Every `old_string` matched exactly once, and every
  post-edit window the tool printed matched the replayed file line for
  line (2, 4, 0 and 15 windows). The shell edits are deterministic and
  were reproduced by re-running exactly those commands in the eval-host
  container: r1's three GNU `sed -i` (transcript lines 25, 37, 44) and
  r2's four `python3 - <<'EOF'` heredocs of `str.replace` (lines 18, 21,
  24, 27; every pattern matched). r1's own later `sed -n '50,80p' gemm.rs`
  and `grep -n cp.add gemm.rs` outputs match the replay byte for byte.
  No other command wrote `gemm.rs` (the rest only built it, ran it, or
  wrote other files). The tool-write counts match each file's
  `harness.diag_exposure` (`writes` 4, 5, 1 and 16). Q_T2's sha256 is the
  one the sealed rebuild-and-check recorded (`285e627e…`). None was
  skipped.
- **The validator.** The harness9 tree's own lifecycle (`run_eval`:
  cleanup, setup, `run_pre_validate`, `run_validation` at timeout scale
  2.0, the runs' `wall_scale`, cleanup), run in
  `openbeast-eval-host:8d1e28442569` through
  `scripts/eval-host-container.sh run` (`nice -n 10`), so the hidden
  cases, the CPU budget (12 s, fixed) and the isolation are the unit's.
  The only change: the unit's validation script was replaced in memory by
  a copy whose `guard_binary` applies the proposed rule (HARNESS_NOTES
  next-era): in the Rust variant, on ELF, an undefined `dlsym` is allowed
  when no other undefined symbol matches `_BAD_IMPORT`. The `_BAD_DEFINED`
  check, the `NEEDED` check, the source lint and the `/proc/<pid>/maps`
  watch are unchanged. No task file and no era-hashed file was edited.
  The replay script, the patched validator copy and the four sources are
  on the rig in `~/ob-cal/.claude/gemm-replay/`.
- **The control.** With the unpatched validator, all four reproduce the
  recorded line (`the multiply must be your own code: the binary imports
  dlsym`), so the recovered sources build the binaries the runs judged.
  Under the proposed rule each printed `allowed dlsym` (the only matching
  import) and then `OK: 6 hidden cases`, in each of three runs; the
  N = 2048 case's seed and checked rows are fresh on every run.

**What it changes.** Replayed under the proposed rule: verdict PASS, for
all four. Nothing on the board moves: the calibration files are
diagnostics, and Q_T2's unit is excluded as contaminated (its
`validator_notes` entry now records the replay). For the calibration, the
`dlsym` bug cost the arm one unit: under the proposed rule cal is 61/71
(against `DS-T2`'s 65), r1 3/7 and r2 6/11. The files themselves are not
rewritten.

**Committed rows hit.** None of the committed v5 board rows. Every Spark
row ran from the Mac. The only other `dlopen`/`dlsym` failures on the
board are two GLM `122_gemm_blocked_c` rows, both on the pre-G26 source
lint's `dlopen` word, not on the binary check: `GLM-B`
(`eval-glm-5-3-flash-v2-turns-L0-20261116-160623.json`) and `GLM-E`
(`eval-glm-5-3-flash-v3-best-20261117-043643.json`). That lint also
matched comments. `GLM-E`'s was audited: a comment-only match, the
solution passes the fixed validator (evals/CHANGELOG.md G26), footnoted.
`GLM-B`'s was not audited, so whether its source used `dlopen` or only
named it in a comment is unknown.
The Linux-host files are another matter. Board row `Q38U-L0` did not reach
the binary check (its `122_gemm_blocked_d` row is `server_error`). Both of
the RTX 5090 profile's L2 diagnostics files (`…-L2-20261122-163812.json`,
`…-L2-20261122-192701.json`) record `122_gemm_blocked_d` as `the binary
imports dlsym`. Those are diagnostics, not board rows. In the
[Qwen3.8 report](V5_QWEN38_RTX5090-2026-11-22.md) §5 the unit counts only
in L2's raw Rust 5/8; the L0 → L2 Rust contrast drops its pair, because
L0's row is `server_error`.

**Q_T2.** Q_T2, the RTX 5090 default's T2 arm, runs in the same container
on the frozen harness9 tree, so it has the same rule. Its
`122_gemm_blocked_d` is affected only if its Rust GEMM uses threads. It
was: Q_T2's r1 attempt stopped at the same check, and it passes under the
proposed rule (the replay above).

**What is done about it.**

- **The fix waits for the next era.** Campaigns are running on the
  current tasks, so no task file or era-hashed file is edited now. The
  proposal is in [HARNESS_NOTES](../HARNESS_NOTES.md#next-era-the-122_gemm_blocked-dlsym-rule-on-linux-2026-11-23).
- **The board handles it with a validator note, by hand, per run.**
  `evals/boards/v5/exclusions.json` names entries by unit and run (board
  id or result file). It has no host- or reason-conditional rule, and Q_T2
  has no row or file yet, so no entry is added now. When a Linux-host run
  lands on the board with a Rust `122_gemm_blocked_*` row whose failure
  line is `the binary imports dlsym`, the verdict was not measured: the
  validator stopped at the binary check on std's `dlsym`. Prefer the
  replay (above), which gives a verdict. Without it, rebuild the binary (the
  validator built it in a private directory that no longer exists) and
  check both of its symbol tables before writing any note. The note says
  "verdict not measured", never "false FAIL": unlike `GLM-E`'s replayed
  `122_gemm_blocked_c`, nothing has shown this solution correct. A FAIL
  counts as not passed either way, so the score is not adjusted. Procedure
  in [MODEL_CAMPAIGN.md](../MODEL_CAMPAIGN.md#eval-host-in-a-container-linux).

## 6. Not done

- ~~A replay of the three Rust solutions under a corrected rule.~~ Done
  2026-11-23 after Q_T2 finished, with Q_T2's r1 attempt as a fourth: all
  four PASS under the proposed rule (§5, "The replay under the proposed
  rule").
- **A second full arm on the rig.** One calibration arm plus two partial
  passes is what was run. A clean full arm would give a like-for-like
  second figure against 65. It is not needed for the verdict, because every
  non-bug difference sits on a unit that flips on the rig itself.

## 7. Files

- `evals/boards/v5/diagnostics/eval-deepseek-v4-1-flash-v5-thinking-first-t2-20261122-230335.json`: the calibration arm.
- `evals/boards/v5/diagnostics/eval-deepseek-v4-1-flash-v5-thinking-first-t2-rerun-20261123-051155.json`: r1.
- `evals/boards/v5/diagnostics/eval-deepseek-v4-1-flash-v5-thinking-first-t2-redo-fails-20261123-071205.json`: r2.
- On the rig: the `dlsym` replay (script, patched validator copy, the four
  recovered sources) in `~/ob-cal/.claude/gemm-replay/`.
- On the rig: the transcripts in `~/ob-cal/.claude/worktrees/h9/agents/logs/`,
  the campaign log in `~/ob-cal/evals/results/campaigns/v5-thinking-first-deepseek-v4-1-flash/`,
  and the run logs in `~/openbeast-runs/cal-*.out`. These result files carry
  no `saved` end time, so a later scan needs `--until`, with the ends from
  §3 (UTC: 05:11, 07:12 and 11:57 on 2026-11-23).
