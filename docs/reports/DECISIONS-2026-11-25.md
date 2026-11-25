# Decision log, 2026-11-25

> One entry per decision taken on 2026-11-25 (PST, UTC-8; `Z` marks UTC),
> in the format of [DECISIONS-2026-11-24.md](DECISIONS-2026-11-24.md): the
> decision, who took it, the reason, the evidence, the alternatives and why
> they lost. PRs are in `MaximilianKhan/openbeast` unless marked "website"
> (`MaximilianKhan/openbeast-website`).

## Contents

1. [A context overflow is a model loss](#1-a-context-overflow-is-a-model-loss)
2. [The audit: no board unit was lost to an overflow](#2-the-audit-no-board-unit-was-lost-to-an-overflow)
3. [The rule is enforced by the board tooling, from a committed ledger](#3-the-rule-is-enforced-by-the-board-tooling-from-a-committed-ledger)

---

## 1. A context overflow is a model loss

**Decision.** A v5 unit that ends on CONTEXT OVERFLOW is a MODEL LOSS: a
fail that stays in the denominator, never an infrastructure loss. It applies
to every run on the v5 board, past and future, every engine, every arm.

**When / who.** Max, 2026-11-25. Applied the same day to every committed
result file (§2) and made mechanical in the board tooling (§3).

**Reason.**

- **The overflow is the model's.** The session that outgrew the window is
  the one the model built: how much it read, printed, re-read and thought.
  The window is part of the serving setup the row declares (weights.json
  `context`), the same for every unit of the run.
- **Scoring it as infra flatters the run.** An infra row is lost: the
  campaign reruns it, a repair file supersedes it on the board, and
  ablation.py drops it from its pairs. Each of those takes the unit out of
  the model's denominator or re-rolls it until it fits, which raises the
  score of exactly the runs whose sessions grow the most.
- **Consistent across arms.** An arm with a bigger output cap or more turns
  grows its context faster. If its overflows were lost while a smaller arm's
  ran to a counted fail, the comparison would favour the arm that overflowed.

**Alternatives.** (a) Keep overflow as `server_error` and rerun it: lost,
because a rerun of a session that overflowed tends to overflow again, and a
lucky rerun is selection, not measurement. (b) Exclude overflow units from
both arms of every comparison: lost, because it shrinks n for exactly the
units where the arms differ.

**What it changes in code** (PR below, §3): `scoring.MODEL_LOSS_REASONS =
{"context_overflow"}` (disjoint from `INFRA_REASONS`); `campaign.infra_reason`
and `ablation.invalid_reason` treat a `context_overflow` row as a model fail
even with API errors on it; `evals/boards/v5/overflow.json` records which
pre-rule `server_error` rows were overflows. The runner and run_eval changes
that would label an overflow at the source touch era-hashed code, so they
are next-era proposals ([HARNESS_NOTES.md](../HARNESS_NOTES.md#next-era-context-overflow-is-a-model-outcome-2026-11-25)):
add Basalt's wording to `_CTX_OVERFLOW_RE`, and record `context_overflow`
as its own reason.

---

## 2. The audit: no board unit was lost to an overflow

**Decision.** Every infrastructure row of every committed v5 result file
(the 25 board runs' files and `evals/boards/v5/diagnostics/`) was checked
for an overflow ending, from the row, its transcript and the server's log.
None was an overflow. **The board does not change**: every run's SOLVE,
LANG, SCORE, passes and rank are identical before and after (below).

**How it was checked.**

- **The row.** `api_errors`, `api_errors_tail`, `compactions`, `iterations`,
  the label and the validation output.
- **The transcript** (`agents/logs/agent-*.jsonl`, matched to the row by
  task text and window with `evals/audit/transcripts.py`). An overflow
  shows as an error event in any engine's wording (llama.cpp `exceeds the
  available context size` / `exceed_context_size`, vLLM `maximum context
  length`, TensorFold `-token context window`, Basalt `exceeds the context
  window`), or as the runner's `context_overflow_unrecoverable` stop, or as
  a compaction. `evals/audit/overflow_audit.py` reads the final error streak
  of each row's transcript.
- **The server log.** For the RTX 5090 rows, llama-server's log
  (`~/Documents/openbeast/.run/stack.log.1` on the rig, the session started
  2026-11-21 18:33 PST, `n_ctx_slot = 262144`), lined up with each
  transcript's error events (a constant +1.2 s offset; every error matched).

**The table.** Run ids are board.json's; the diagnostics file is the Qwen3.8
profile's first L2 pass. "Slot max" is the most tokens the server's slot
held when one of the unit's 500s fired, of 262,144.

| Run | File | Unit | Label | True cause | Evidence |
|---|---|---|---|---|---|
| Q38U-L0 | `…-L0-20261122-083815` | 122_gemm_blocked_c | server_error | truncated tool call (llama-server HTTP 500 JSON parse), not overflow | 23 errors (row 23), all the parse 500, after the last reply; each after a reply cut at exactly 16,384 tokens; slot max 21,669 |
| Q38U-L0 | same | 122_gemm_blocked_d | server_error | truncated tool call, not overflow | 22 (22); cap 16,384; slot max 23,184 |
| Q38U-L0 | same | 124_rkf45 | server_error | truncated tool call, not overflow | 8 (8); cap 16,384; slot max 36,435 |
| Q38U-L0 | same | 173_taskgroup_cancel | server_error | truncated tool call, not overflow | 21 (21); cap 16,384; slot max 25,726 |
| Q38U-T2 | `…-q-t2-20261123-115749` (main) | 122_gemm_blocked_d | server_error (repaired by r1) | truncated tool call, not overflow | 27 (27); cap 8,192; slot max 89,082 |
| Q38U-T2 | main | 168_linearizability_kv_f | server_error | truncated tool call, not overflow | 58 (58); cap 8,192; slot max 16,042 |
| Q38U-T2 | main | 173_taskgroup_cancel | server_error (repaired by r1) | truncated tool call, not overflow | 51 (51); cap 8,192; slot max 26,240 |
| Q38U-T2 | main | 175_crdt_orset_rga | server_error | truncated tool call, not overflow | 21 (21); cap 8,192; slot max 49,591 |
| Q38U-T2 | main | 190_autograd_mlp | server_error | truncated tool call, not overflow | 45 (45); cap 8,192; slot max 42,317 |
| Q38U-T2 | main | 195_riscv_insn_budget | server_error (repaired by r1) | truncated tool call, not overflow | 31 (31); cap 8,192; slot max 87,876 |
| Q38U-T2 | main | 196_polyphase_resampler | server_error (repaired by r1) | truncated tool call, not overflow | 38 (38); cap 8,192; slot max 57,321 |
| Q38U-T2 | `…-q-t2-20261123-231112` (r1) | 168_linearizability_kv_f | server_error, still lost | truncated tool call, not overflow | 52 (52); cap 8,192; slot max 26,787 |
| Q38U-T2 | r1 | 175_crdt_orset_rga | server_error, still lost | truncated tool call, not overflow | 34 (34); cap 8,192; slot max 43,179 |
| Q38U-T2 | r1 | 190_autograd_mlp | server_error, still lost | truncated tool call, not overflow | 1 (1), on the last turn; cap 8,192; slot max 72,049 |
| (diagnostics) | `…-L2-20261122-163812` | 122_gemm_blocked_b | server_error (rerun in the second L2 pass) | truncated tool call, not overflow | 21 (21); cap 16,384; slot max 26,336 |
| (diagnostics) | same | 163_bigint_divmod_d | server_error (rerun) | truncated tool call, not overflow | 2 (2); cap 16,384; slot max 41,981 |
| GLM-L0 | `eval-glm-5-3-flash-L0-20261116-060847` | 146_aho_corasick | validator_timeout (repaired by the second file) | the validator's 60 s timeout, not overflow | row: 0 API errors, 0 compactions, 30 iterations; transcript (Mac): a max_iterations stop, no error, no overflow stop |
| GLM-L0 | same | 158_karatsuba_bytes_c | validator_timeout (repaired) | the validator's 60 s timeout, not overflow | same |
| GLM-B | `eval-glm-5-3-flash-v2-turns-L0-20261116-160623` | 22 units (107_singleflight … 49_markov_steady_state) | interpreter_startup (repaired by the second file) | the 2026-11-16 venv break (the eval interpreter died at startup), not overflow | 20 rows 0 completion tokens, exit 1; 2 rows (173_taskgroup_cancel, 36_black_scholes_e) the validator's interpreter after the agent ran; validation output "Could not find platform independent libraries" (HARNESS_NOTES, the venv incident) |

Every other row of every committed file is a real verdict (no infra label).
The DeepSeek, Hy4, GLM-UP and Inkling board files have no infrastructure row
at all.

**The suspicion it answers.** A website reviewer suspected Q38U-T2's
175_crdt_orset_rga and 190_autograd_mlp were overflows. They were not: the
rows' `tokens_prompt` (1.04 M and 2.43 M) is the server's prompt usage summed
over every request of the unit, not one context, and the slot never held more
than 72,049 tokens at any of those units' errors. The earlier notes' "truncated
tool call" is right for all 14 Qwen3.8 rows: the reply hit the per-request
output cap mid tool call, llama-server could not parse the cut-off JSON and
answered 500, and the runner resent the identical request.

**Undetermined.** None.

**Not on the board yet: I_T3.** The same tool, run read-only on the rig over
I_T3's main file (`eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-003039.json`),
finds its 4 `server_error` rows were overflows: 163_bigint_divmod_d (95
errors), 176_intermittent_500s_a (58), 183_heston_little_trap (74) and
192_pike_regex (112), every error of each final streak Basalt's "exceeds the
context window". They are left to the I_T3 wrap-up: add the 4 entries
(verdict `overflow`, with the Basalt log line from the log saver) when the
run goes on the board. They then count as fails, and the campaign's rerun of
them (the second results file) does not replace them.

**Board delta.** Recomputed with `v5_board.py` before and after the ledger:
no run's SOLVE, LANG, SCORE, passes, `lost` set or rank moved, and the
generated blocks in README.md and docs/RESULTS.md are byte-identical. The
ranking stays DS-T2 94.9, HY4-T2 83.4, GLM-UP 81.9, INK-T2-STOCK 78.2, INK-T2
76.5, Q38U-T2 75.6 (OPUS-REF 100.0, reference). Q38U-L0 keeps its 4 lost
units and Q38U-T2 its 3: they are the truncated-call 500, an infra fault
under today's rules (HARNESS_NOTES 2026-11-22 proposal (a) would make it a
model turn).

---

## 3. The rule is enforced by the board tooling, from a committed ledger

**Decision.** `evals/boards/v5/overflow.json` holds one verdict per
infrastructure row (`file`, `unit`, `label`, `verdict` overflow /
not_overflow / undetermined, `cause`, `evidence`), and the tools apply it:

- `v5_board.py` relabels each `overflow` row `context_overflow` before it
  merges a run's files, so the unit is a counted fail, no repair row
  supersedes it, and the row's status says "N units ended on context
  overflow: model fails (overflow.json)". `v5_report.py` reads the same
  merge.
- `ablation.py` applies the same ledger before pairing, so an overflow is
  the model's fail in a pair, not a dropped `server_error`;
  `invalid_reason` passes any `context_overflow` row.
- `campaign.infra_reason` never calls a `context_overflow` row lost, so a
  next-era row labelled so at the source is never rerun.
- `v5_board.py --check` fails while any `server_error` row of a committed
  file (board runs and diagnostics) has no verdict, an entry is stale (names
  no committed file, no such unit, or a row that is not infra) or lacks
  evidence, or a row's own error fields (`stop_reason`, `last_api_error`;
  next era) show an overflow its verdict does not.
- `evals/audit/overflow_audit.py` proposes the entries from the transcripts;
  the server-log line is added by hand.

**Why a ledger and not a pattern on the row.** Today's rows carry no error
text (`api_errors` is a count), so the evidence lives in the transcripts and
the server logs on the hosts. A committed verdict per row keeps the board
reproducible offline and auditable, the same way `exclusions.json` carries
the integrity scans' verdicts.

**Tests.** `tests/test_v5_board.py` (an overflow is a counted fail that no
repair supersedes; the `--check` problems; every engine's wording; the
committed ledger covers every `server_error` row), `tests/test_ablation.py`
(an overflow stays in the pair), `tests/test_campaign.py` (never lost),
`tests/test_overflow_audit.py` (the transcript classification, end to end).
