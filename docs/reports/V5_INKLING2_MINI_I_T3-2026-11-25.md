# v5 report: Inkling 2 Mini on the RTX 5090, the improved arm I_T3 (2026-11-25)

> **Final for I_T3 (2026-11-25).** Arm `I_T3` of `v5-thinking-first` ran
> Inkling 2 Mini on Basalt's stock NVFP4-MX8 pack, one RTX 5090, from the
> rig's eval-host container: `I_T2_stock` (board row `INK-T2-STOCK`, 53/71,
> the RTX 5090 default) plus three changes at once: wall-bound turns
> (`--turns-per-wall-s 5`), a 16,384-token turn cap with `--answer-room 8192`,
> and Basalt PR #632's patch for upstream issue #588. It finished
> **52/71, SCORE 77.4**, 0 units lost, 0 excluded, 4 of its fails context
> overflows (a model loss by Max's rule of 2026-11-25). It is board row
> `INK-T3`: listed, not the stock pack's shown row (INK-T2-STOCK's 78.2 stays
> the best), and not the default. Design: the main report
> [V5_INKLING2_MINI_RTX5090-2026-11-24.md](V5_INKLING2_MINI_RTX5090-2026-11-24.md)
> §12 (this report is its §13); the #632 patch: [STRATA_INKLING_5090-2026-11-23.md](STRATA_INKLING_5090-2026-11-23.md)
> §16; decisions: [DECISIONS-2026-11-24.md](DECISIONS-2026-11-24.md) §11-§12 and
> [DECISIONS-2026-11-25.md](DECISIONS-2026-11-25.md) §4.

## Contents

1. [Summary](#1-summary)
2. [Sources and how to regenerate](#2-sources-and-how-to-regenerate)
3. [The row](#3-the-row)
4. [Tables (generated)](#4-tables-generated)
5. [Paired comparisons](#5-paired-comparisons)
6. [The overflows](#6-the-overflows)
7. [The three changes, read one by one](#7-the-three-changes-read-one-by-one)
8. [Per unit](#8-per-unit)
9. [Time, tokens and speed](#9-time-tokens-and-speed)
10. [Integrity and the dlsym stop](#10-integrity-and-the-dlsym-stop)
11. [Decisions and recommendations](#11-decisions-and-recommendations)
12. [Caveats](#12-caveats)

---

## 1. Summary

- **52/71 against INK-T2-STOCK's 53/71.** Paired over all 71 units: 2
  gained (`163_bigint_divmod_e`, `178_codebase_qa`), 3 lost
  (`158_karatsuba_bytes_c`, `163_bigint_divmod_a`, `196_polyphase_resampler`),
  net −1, exact McNemar p = 1.0. The minimum detectable effect over 71
  units is 7 units at Inkling's measured seed churn (about 3%) and 9.5 at the
  tool's default churn: **level, within noise, single seed.** The composite
  does not beat the plain stock row, so it changes nothing about the default.
- **Against Q38U-T2 (Qwen3.8, 52/71 scored):** over the 65 units both
  measured cleanly, 49 against 52, 2 gained and 5 lost, net −3, p = 0.45
  (INK-T2-STOCK there: −2, p 0.69). The aim of the arm was a lead on these
  paired units; it did not come. Within noise either way.
- **4 context overflows, the arm's own failure mode.** `163_bigint_divmod_d`,
  `176_intermittent_500s_a`, `183_heston_little_trap` and `192_pike_regex`
  outgrew Basalt's 262,144-token window (a prompt of 245,865 to 261,884 tokens
  plus the 16,384 the request reserves). The harness9 runner does not
  recognise Basalt's wording, so it never compacted and resent the request
  every 5 s until the turn limit (58 to 112 times). A fifth unit,
  `168_linearizability_kv_c`, overflowed at turn 49 and retried until its
  wall. No unit of the four 60-turn Inkling arms (284 units) overflowed;
  their largest prompt was 202,663 tokens. Under Max's rule the four
  `server_error` rows are counted fails, and the campaign's rerun of them
  (all four failed again without an overflow) supersedes nothing. **52 either
  way**, as expected.
- **The #632 patch did what it was built for**: 0 turns ended empty at the
  cap (INK-T2-STOCK: 105; the Derisked arms 167 to 226), and the server's
  "no answer" warning fired once in 3,205 requests (on a reply the client
  received as text). What remains is the model's: 93 turns ran to the cap
  writing text and no call (INK-T2-STOCK: 2), median about 23,000
  characters of the model talking through its design in the answer channel.
  The patch turned an empty turn into a visible one; it did not make the
  model act.
- **Wall-bound turns moved the binding limit, not the result.** The stock
  row's failures ended at the turn limit (17 of 18, at a median 28% of the
  wall). I_T3's 19 failures: 8 at the wall, 9 at the turn limit (4 of those
  the overflow retries; 3 of the other 5 spent their extra turns in loops of
  repeated calls), 2 stopped earlier. Two units needed more than 60 turns to
  pass (`163_bigint_divmod_e` 88, `178_codebase_qa` 69). The `168` family
  went 0/6 again, now at the wall.
- **The 16,384 cap halved the cut-call damage** (43 capped replies still
  carrying a call against 83; 33 truncated heredocs against 82) **and fed the
  overflows** (every request reserves 16,384 tokens, and full-cap replies
  grow the session faster).
- **Cost: about twice the time for the same count.** 8.7 unit-hours against
  4.3 (the main pass 8 h 42 min; the rerun 1 h 05 min more); 211.6 M tokens
  against 69.8 M (3.0×). Passing units kept their speed (median 39 s against
  44 s). Decode median 428 tok/s on replies of 256+ tokens against 467 (the
  prompts are longer: median 39.6 K tokens against 21.7 K; at 200 K+ tokens
  decode falls to a median 334 tok/s).
- **Integrity:** 0 contaminated (75 transcripts, 71 units; refused web
  calls only). The rescan of INK-T2-STOCK with the same scanner found one
  hit, a false positive (`nc` as a variable name in code), fixed in the
  scanner in this PR; INK-T2-STOCK stays 0 contaminated. `122_gemm_blocked_d`
  stopped at the Linux `dlsym` check again; replayed: PASS 3 of 3.
- **Decisions** (DECISIONS-2026-11-25 §4): the 5090 default stays stock
  (`INK-T2-STOCK`, Max 2026-11-24); `I_T3_t15` and `I_T3_8k` are not queued;
  the next Inkling step waits for the next-era overflow fix. **Recommendation
  (the profile is not changed here):** keep the #632 patch in the shipped
  default build (§11.2).

## 2. Sources and how to regenerate

Raw data on the rig (`ssh beast`; none of it is committed except the two
results files):

| What | Path |
|---|---|
| Results (main, 71 rows; committed verbatim) | `~/ob-ink3/.claude/worktrees/h9/evals/results/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-003039.json` (sha256 `f0f15c62…3dd3`) |
| Results (the campaign's rerun, 4 rows; committed verbatim) | `…/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-091311.json` (sha256 `f56d3466…d403`) |
| Diagnostics timing | `…/evals/results/diag-timing-20261125-003039.jsonl`, `…-091311.jsonl` |
| Transcripts (75) | `~/ob-ink3/.claude/worktrees/h9/agents/logs/agent-20261125-003040-*` to `agent-20261125-094812-*` |
| Campaign log (UTC) | `~/openbeast-runs/beast-inkling-I_T3-20261124-1630.out` |
| Campaign state | `~/ob-ink3/evals/results/campaigns/v5-thinking-first-inkling-2-mini-basalt-nvfp4-mx8-stock/` (`state.json`, `arm-I_T3.log`, `arm-I_T3-r1.log`, `campaign.log`) |
| Launcher and its log (PST) | `~/openbeast-runs/ink-I_T3-run.sh`, `~/openbeast-runs/ink-I_T3-run.out` |
| Basalt server log | `~/openbeast-runs/basalt-server-logs/basalt-I_T3-20261124-162910.log` (`docker logs --timestamps`, from the container's start 2026-11-25 00:29:14Z) |
| INK-T2-STOCK, for the comparisons | transcripts `~/ob-ink2/.claude/worktrees/h9/agents/logs/agent-20261124-140740-*` to `agent-20261124-182500-*`; server log `~/openbeast-runs/basalt-server-logs/basalt-20261124-060744-54e3597ff960.log` |
| The dlsym replay | `~/ob-cal/.claude/gemm-replay-ink-t3/` |

Timeline (UTC; PST = UTC−8): launcher start 00:29:10, gate passed 00:30:37
(the #588 probe: 4 of 4 replies made a call), `START I_T3` 00:30:39,
`END I_T3 rc=1 52/67 scorable` 09:13:10 (the 4 overflow rows read as lost by
the harness9 campaign), `RERUN r1` 09:13:11 to 10:18:12, `DONE I_T3:
52/71`, `RESTORE OK` 10:18:19 (the default stack, Qwen3.8, back on the
gate). The GPU was held 9 h 49 min. The design estimated 8-10 h
(UNVERIFIED) and the campaign printed ~11.5 h; the main pass took 8.7 h.

Regenerate, in a fresh clone of main:

```bash
# §4: the tables
python3 evals/v5_report.py --model "Inkling 2 Mini (Basalt NVFP4-MX8)" \
    --context INK-T2,INK-T2-S1,INK-UP8K,Q38U-T2,OPUS-REF \
    --pair I_T2_stock:I_T3 --pair Q38U-T2:I_T3 --pair INK-T2:I_T3 --pair INK-T2-S1:I_T3 \
    --pair INK-UP8K:I_T3 --pair Q38U-T2:I_T2_stock \
    --write docs/reports/V5_INKLING2_MINI_I_T3-2026-11-25.md
# §5: the paired tests (ablation.py applies exclusions.json and overflow.json)
B=evals/boards/v5
K=$B/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t2-stock-20261124-140740.json
T=$B/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-003039.json
python3 evals/ablation.py --force $K $T       # per language; --force: the arm labels differ
# against Q38U-T2: Q_T2's two files merged as the board merges them (main report §1's recipe, "$M"), then
python3 evals/ablation.py --force "$M"/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-115749.json $T
```

The "all units" rows of §5 are `ablation.contrast(pairs, "all",
ablation.CHURN["all"])` after `ablation.pair()` on the two files loaded
with `ablation._load`; the seed-churn MDE is `ablation.mde(n, 0.028)`.
Only the main file is passed: under `overflow.json` its four overflow rows
are `context_overflow` fails, which `pair()` keeps.

The transcript and server-log figures (§6, §7, §9) need the rig's data: copy
it read-only, then

```bash
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir <copy>/agents/logs --until I_T3=2026-11-25T10:18:12 \
    --json scan.json "I_T3=<main>,<rerun>@<git archive f38a29c7>"
python3 evals/audit/capped_turns.py scan.json --cap 16384        # the scanner's view: the r1 transcripts for the 4 rerun units
python3 evals/audit/basalt_log.py <server log> --since 2026-11-25T00:30:39 --until 2026-11-25T09:13:10 --cap 16384
python3 evals/audit/overflow_audit.py <main> <rerun> --tree <f38a29c7> --logs-dir <copy>/agents/logs --tz UTC
```

The board's view of the capped turns attributes the four overflow units to
their first-file transcripts (the sealed review,
`evals/audit/reviews/ink-t3-integrity-2026-11-25.md`, has the exact
commands and the swapped scan). The context-size and decode-by-context
figures of §9 parse the server log's `s1 done pp=N/Mc … tg=K X t/s … end=`
lines in the main window (rejected requests, `end=error`, excluded).

## 3. The row

| | INK-T3 (I_T3) | INK-T2-STOCK (I_T2_stock) |
|---|---|---|
| Passed | **52/71** | 53/71 |
| SCORE / SOLVE / LANG | 77.4 / 75.4 / 83.6 | 78.2 / 75.4 / 86.7 |
| Lost / excluded | 0 / 0 | 0 / 0 |
| Overflows (counted fails) | 4 (`overflow.json`) + 1 at the wall | 0 |
| Board | listed; not the stock pack's shown row; not the default | shown row (#4); the RTX 5090 default |
| Files | main + the campaign's rerun of 4 (supersedes nothing) | main |

SOLVE is the same (75.4: the same base tasks solved at least once); LANG
fell 3.1 points because the lost and gained variants sit in different
languages (Python −1 and C −1, Rust +1). The board chooses the best
configuration per (model, weights) by SCORE, so `INK-T2-STOCK` stays the
stock pack's row and `defaults` still names it for the RTX 5090.

## 4. Tables (generated)

<!-- v5-report:start -->
<!-- generated by evals/v5_report.py for Inkling 2 Mini (Basalt NVFP4-MX8): do not hand-edit -->

**Arms**

| Arm | Run | Weights | On the board | Era | Turn cap | Passed | SCORE | SOLVE | LANG |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| I_T2_stock | INK-T2-STOCK | Basalt NVFP4-MX8 pack · Basalt's official pack · stock | yes | 036638ddd68698ce | 8192 | 53/71 | 78.2 | 75.4 | 86.7 |
| I_T3 | INK-T3 | Basalt NVFP4-MX8 pack · Basalt's official pack · stock | yes | 036638ddd68698ce | 16384 | 52/71 | 77.4 | 75.4 | 83.6 |
| Claude Opus 5.5: OPUS-REF | OPUS-REF | Anthropic API · hosted, weights not public | yes | — | — | 71/71 | 100.0 | 100.0 | 100.0 |
| Qwen3.8 27B Uncensored MTP: Q_T2 | Q38U-T2 | GGUF Q5_K_M · JonathanColetti Uncensored (MTP head kept) · abliterated | yes | 036638ddd68698ce | 8192 | 52/71 (54 raw) | 75.6 | 72.3 | 85.6 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_T2 | INK-T2 | Basalt NVFP4-MX8 pack · Blackfrost Derisked · abliterated | yes | 036638ddd68698ce | 8192 | 49/71 | 76.5 | 72.3 | 89.1 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_T2_s1 | INK-T2-S1 | Basalt NVFP4-MX8 pack · Blackfrost Derisked · abliterated | yes | 036638ddd68698ce | 8192 | 51/71 | 78.3 | 72.3 | 96.4 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_UP8k | INK-UP8K | Basalt NVFP4-MX8 pack · Blackfrost Derisked · abliterated | yes | 9bcd5aa6030836d1 | 8192 | 49/71 | 75.1 | 69.2 | 92.7 |

**Per language** (passed / units of that language; an in-progress arm's passes are over the units done so far)

| Arm | Py | Go | C | C++ | Rust | Zig | Sh |
|---|---:|---:|---:|---:|---:|---:|---:|
| I_T2_stock | 24/32 | 6/8 | 7/8 | 6/8 | 5/8 | 4/6 | 1/1 |
| I_T3 | 23/32 | 6/8 | 6/8 | 6/8 | 6/8 | 4/6 | 1/1 |
| Claude Opus 5.5: OPUS-REF | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 |
| Qwen3.8 27B Uncensored MTP: Q_T2 | 23/32 | 7/8 | 6/8 | 6/8 | 6/8 | 3/6 | 1/1 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_T2 | 23/32 | 6/8 | 5/8 | 6/8 | 5/8 | 3/6 | 1/1 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_T2_s1 | 23/32 | 6/8 | 6/8 | 6/8 | 5/8 | 4/6 | 1/1 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_UP8k | 22/32 | 6/8 | 6/8 | 5/8 | 5/8 | 4/6 | 1/1 |

**Time and the wall** (wall hit = the harness killed the agent at the unit's wall, `agent_exit_code == -1`; turns are counted only where the row carries them, from harness9 on)

| Arm | Units done | Unit-hours | Mean min / unit | Wall hits | Passed at the wall | Failed at the wall | Turns of the wall-hit units |
|---|---:|---:|---:|---:|---:|---:|---|
| I_T2_stock | 71 | 4.3 | 3.6 | 0 | 0 | 0 | — |
| I_T3 | 71 | 8.7 | 7.3 | 8 | 0 | 8 | 42-113 |

**Units gained and lost** (arm against base, over the units both have a verdict for)

| Base → arm | Compared | Net | Gained | Lost |
|---|---:|---:|---|---|
| I_T2_stock → I_T3 | 71 | -1 | 163_bigint_divmod_e, 178_codebase_qa | 158_karatsuba_bytes_c, 163_bigint_divmod_a, 196_polyphase_resampler |
| Q38U-T2 → I_T3 | 68 | -1 | 122_gemm_blocked_b, 122_gemm_blocked_e, 124_rkf45, 126_hmac_scratch | 158_karatsuba_bytes_c, 163_bigint_divmod_a, 163_bigint_divmod_b, 176_intermittent_500s_a, 196_polyphase_resampler |
| INK-T2 → I_T3 | 71 | +3 | 122_gemm_blocked_e, 163_bigint_divmod_c, 163_bigint_divmod_e | — |
| INK-T2-S1 → I_T3 | 71 | +1 | 163_bigint_divmod_c, 163_bigint_divmod_e | 158_karatsuba_bytes_c |
| INK-UP8K → I_T3 | 71 | +3 | 158_karatsuba_bytes_d, 163_bigint_divmod_c, 163_bigint_divmod_e, 175_crdt_orset_rga, 178_codebase_qa | 158_karatsuba_bytes_c, 196_polyphase_resampler |
| Q38U-T2 → I_T2_stock | 68 | +0 | 122_gemm_blocked_b, 122_gemm_blocked_e, 124_rkf45, 126_hmac_scratch | 163_bigint_divmod_b, 163_bigint_divmod_e, 176_intermittent_500s_a, 178_codebase_qa |

**Units whose verdict differs across the arms** (P pass, F fail, · not run yet, L lost to infrastructure); every other unit has the same verdict in every arm

| Unit | I_T2_stock | I_T3 |
|---|:---:|:---:|
| 158_karatsuba_bytes_c | P | F |
| 163_bigint_divmod_a | P | F |
| 163_bigint_divmod_e | F | P |
| 178_codebase_qa | F | P |
| 196_polyphase_resampler | P | F |
<!-- v5-report:end -->

The generated "gained and lost" rows against Q38U-T2 count its three
excluded passes as Q38U-T2 fails, as the board does (68 units); §5 drops
every unit either row did not measure cleanly (65).

## 5. Paired comparisons

All units, exact McNemar, two-sided. MDE is `ablation.mde` at α 0.05: at the
tool's default churn for "all" (0.15) and at Inkling's measured seed churn
(I_T2 against I_T2_s1: 2 of 71 flipped, about 0.028).

| Base → I_T3 | n | Base | I_T3 | Gained | Lost | Net | p | MDE (0.15 / seed) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **INK-T2-STOCK** (same pack, harness, era, container; the treatment effect of the three changes) | 71 | 53 | 52 | 2 | 3 | −1 | 1.00 | 9.5 / 7 |
| **Q38U-T2** (65 units both measured cleanly) | 65 | 52 | 49 | 2 | 5 | −3 | 0.45 | 10 / 7 |
| INK-T2 (Derisked pack) | 71 | 49 | 52 | 3 | 0 | +3 | 0.25 | 9.5 / 7 |
| INK-T2-S1 (Derisked, seed 1) | 71 | 51 | 52 | 2 | 1 | +1 | 1.00 | 9.5 / 7 |
| INK-UP8K (Derisked, UP_8k; 163_bigint_divmod_c excluded there) | 70 | 49 | 51 | 4 | 2 | +2 | 0.69 | 9.5 / 7 |
| *for scale: Q38U-T2 → INK-T2-STOCK* | 65 | 52 | 50 | 2 | 4 | −2 | 0.69 | 10 / 7 |

- **Against INK-T2-STOCK:** gained `163_bigint_divmod_e` (Rust) and
  `178_codebase_qa` (Python); lost `158_karatsuba_bytes_c` (C),
  `163_bigint_divmod_a` and `196_polyphase_resampler` (Python). Per
  language every row is within churn (`ablation.py`: C −1, Python −1, Rust
  +1, the others 0). Cost on the 50 units both passed: I_T3 used more
  completion tokens on 31 and fewer on 19 (median +1,076, sign test p 0.12)
  and more turns on 26, fewer on 17 (p 0.22): no measurable difference in
  cost-to-fix.
- **Against Q38U-T2:** gained `122_gemm_blocked_b` and `122_gemm_blocked_e`
  (compiled); lost `158_karatsuba_bytes_c`, `163_bigint_divmod_a`,
  `163_bigint_divmod_b`, `176_intermittent_500s_a` (an overflow) and
  `196_polyphase_resampler`. Python −3 (p 0.25), compiled 0, Zig +1. On the
  46 units both passed, I_T3 used fewer completion tokens on 29 (median
  −933, p 0.10).
- **No row is a measured effect.** Every net is below its MDE; the one
  arm-level difference worth a sentence is that I_T3 is level with the stock
  row and does not reach Qwen3.8 on the paired units, which is what the
  design's own noise note said a single row could show (main report §12.4:
  "any single draw inside about ±7 of 53 is within noise").

## 6. The overflows

### 6.1 What happened

| Unit | Replies before | First error | Prompt then | Room left (of 16,384 asked) | Retries (row `api_errors`) | Retry time | End |
|---|---:|---|---:|---:|---:|---|---|
| 163_bigint_divmod_d | 85 | turn 86 | 261,884 | 252 | 95 | 8.0 of 26.5 min | turn limit 180 |
| 176_intermittent_500s_a | 242 | turn 243 | 245,865 | 16,271 | 58 | 4.9 of 18.3 min | turn limit 300 |
| 183_heston_little_trap | 106 | turn 107 | 247,259 | 14,877 | 74 | 6.2 of 20.8 min | turn limit 180 |
| 192_pike_regex | 68 | turn 69 | 249,367 | 12,769 | 112 | 9.4 of 23.6 min | turn limit 180 |
| 168_linearizability_kv_c | 48 | turn 49 | 251,439 | 10,697 | 50 (the row: 0, partial stats) | 4.1 of 30 min | wall (1,800 s) |

Basalt rejects a request whose prompt plus `max_tokens` exceeds the window
("requests are never truncated", HTTP 400: `request exceeds the context
window: prompt (N tokens) + max tokens (16384) exceeds the context (262144)`).
The server log has 389 such lines in the arm's window, every one matched to
within 1 ms by a transcript error (339 in the four ledger units, 50 in
`168_linearizability_kv_c`). `163_bigint_divmod_d` crossed on a full
16,384-token reply generated at a 245,429-token prompt.

### 6.2 Why the runner did not recover: the harness gap

`agents/runner.py` (harness9, era `036638dd`) compacts on an overflow only
when the error matches `_CTX_OVERFLOW_RE` (llama.cpp, vLLM and TensorFold
wordings). Basalt's "exceeds the context window" is not among them, so the
runner counted each rejection as an API error, slept 5 s and resent the
identical request; the turn limit or the wall ended the unit. No compaction
ran in any of the five (0 in the rows). The fix is next-era work because
`agents/runner.py` is era-hashed:
[HARNESS_NOTES.md "Next-era: context overflow is a model outcome"](../HARNESS_NOTES.md#next-era-context-overflow-is-a-model-outcome-2026-11-25)
proposal (a), recorded by #325: add Basalt's wording, and teach
`_overflow_tokens` Basalt's `prompt (N) … context (M)` order with the target
`n_ctx − max_tokens`. This arm is its evidence (the row there is updated).

Basalt's own error names a server-side alternative, `--fit-max-tokens`
(shorten `max_tokens` to the room left). It would have given four of the
five units 10,697 to 16,271 more tokens for one reply, and `163_d` 252. It
only delays the overflow (the next tool result fills the room), and it
changes what a request means (the cap silently shrinks), so it is noted as a
candidate in HARNESS_NOTES, not proposed as a fix.

### 6.3 Why I_T3 and none of the 60-turn arms

The 60-turn arms never came close enough. In INK-T2-STOCK's server window
the median prompt was 21.7 K tokens, 88 of 2,032 requests were over 100 K
and the largest 202,663. In I_T3's main window the median prompt was 39.6 K,
738 of 3,207 requests were over 100 K, 179 over 200 K and the largest that
ran 245,587. Three changes push the same way: three times the turns (a
session keeps every tool result unless compacted; `176_intermittent_500s_a`
reached 245 K at reply 242 with almost no capped replies), replies up to
twice as long that stay in the history (the answer part is kept; the
reasoning is not sent back), and a 16,384 reservation on every request
instead of 8,192. The overflow is a property of the composite, not of the
pack or the engine.

### 6.4 How the rule was applied

Max's rule (DECISIONS-2026-11-25 §1): an overflow is a model loss. The four
`server_error` rows have `overflow` verdicts with their evidence in
`evals/boards/v5/overflow.json`; `v5_board.py` relabels them
`context_overflow` before the merge, so the rerun file's rows for the same
units (four fails, none an overflow) supersede nothing, and `ablation.py`
pairs them as fails. `v5_board.py --check` passes. The count is 52 with or
without the rerun (the rerun passed none), as the campaign's own
`DONE I_T3: 52/71` said. `overflow_audit.py` matched each main row to the
rerun's transcript when given both files and the campaign's end; its
error-count guard caught it ("undetermined … check the match"), and this
PR ends each file's window at the next file's start.

## 7. The three changes, read one by one

The arm is bundled (main report §12.7): the changes cannot be separated
from one row. What follows are mechanism counts, not effects.

### 7.1 Wall-bound turns (`--turns-per-wall-s 5`: 180 turns at 900 s)

- **The binding limit moved, as designed.** INK-T2-STOCK: 17 of 18 failures
  ended at the turn limit (60/60, at a median 28% of the wall), 0 at the
  wall. I_T3: of 19 failures, **8 at the wall** (`163_bigint_divmod_f`, five
  of the six `168_linearizability_kv_*`, `190_autograd_mlp`,
  `195_riscv_insn_budget`; after 42 to 113 turns), **9 at the turn limit**
  (the 4 overflow retries, and `158_karatsuba_bytes_c`, `163_bigint_divmod_a`,
  `168_linearizability_kv_b`, `173_taskgroup_cancel`, `196_polyphase_resampler`
  at 180/180), **2 stopped earlier** (`122_gemm_blocked_d` at the `dlsym`
  check, turn 29; `163_bigint_divmod_b` declared done at turn 67).
- **Where it helped.** Two passes needed more than 60 turns:
  `163_bigint_divmod_e` (88 turns; a stock fail at 60/60) and
  `178_codebase_qa` (69; a stock fail at 60/60). Three units that passed in
  both arms took longer (`122_gemm_blocked_e` 151 turns against 53,
  `163_bigint_divmod_c` 62 against 57, `175_crdt_orset_rga` 180 against 59).
- **Where it did not.** Of the five genuine turn-limit failures, three
  spent the extra turns looping: `163_bigint_divmod_a` and
  `168_linearizability_kv_b` triggered the runner's repeat guard 138 and
  145 times; `158_karatsuba_bytes_c` made 178 shell calls, one script
  re-sent 78 times, with near-identical marker echoes in between (a stock
  pass in 21 turns). Two of the three losses against the stock row were the
  stock row's last-turn passes (`163_bigint_divmod_a`, `196_polyphase_resampler`
  at 60/60): main report §12.3's "the limit is where the coin lands"; with
  three times the turns these two did not land. The `168` family stays 0/6
  (now CPU budget at the wall), consistent with a design failure that turns
  do not fix (§12.3 there).
- **Cost.** Failing units ran a median 1,447 s (stock 495 s): 6.8 of the
  8.7 unit-hours.

### 7.2 The 16,384 cap with an 8,192 answer room

- **Cut calls and truncated writes roughly halved.** Replies that ended at
  the cap still carrying a tool call: 43 in 3,204 replies (INK-T2-STOCK: 83
  in 2,032); bash results reporting a here-document cut at end of file: 33
  (82). Capped replies overall: 136 (4.2% of replies) against 190 (9.4%).
- **It did not convert the units it targeted.** The stock row's heaviest
  cut-call units still failed: `173_taskgroup_cancel` (21 capped calls in
  stock, 0 in I_T3, now a crash at 180 turns), `163_bigint_divmod_b` (13,
  now 2), `192_pike_regex` (an overflow at reply 68, with 1 capped reply).
  `190_autograd_mlp` and `168_linearizability_kv_c` still had 12 and 15
  capped replies with a call.
- **It fed the overflows** (§6.3): the reservation and the longer replies.
- **Speed at the cap** is unchanged per token (503 tok/s median on replies
  at 16,384 against 512 at 8,192); a full-cap turn now takes about 33 s.

### 7.3 The #632 patch (upstream #588)

The hypothesis (main report §12.5 item 3; DECISIONS-2026-11-24 §11): with
the patch, a turn whose thinking hits the budget opens an answer block, so
no turn ends empty at the cap.

| | INK-T2-STOCK (unpatched, 8,192 cap) | **INK-T3 (patched, 16,384 cap)** |
|---|---:|---:|
| Replies | 2,032 | 3,204 |
| Ended at the cap | 190 | 136 |
| … empty (no text, no call) | **105** | **0** |
| … text only | 2 | **93** |
| … still carrying a call | 83 | 43 |
| Server "no answer" warnings (window) | 105 | 1 |
| Capped and not acting, share of replies | 5.3% | 2.9% |

- **Confirmed: the empty capped turn is gone.** 0 in 71 units, from 105
  (stock) and 167-226 (the Derisked arms). The server's one warning
  (`168_linearizability_kv_b`, turn 21) is a reply the client received as
  text: the transcript has its content and no call. The gate probe agreed
  (4 of 4 replies made a call).
- **What replaced it is still a non-acting turn.** 93 capped replies were
  text with no call, concentrated in failing units (`195_riscv_insn_budget`
  36, `163_bigint_divmod_a` 16, `168_linearizability_kv_a` 10,
  `168_linearizability_kv_c` 9, `168_linearizability_kv_b` 8). Their content
  is long (median about 23,000 characters across all 75 transcripts): the
  model continues its reasoning as answer text until the cap. This is the
  risk the patch review named (Strata §16.4: "after a wrap the model writes
  text first").
- **A side effect to watch:** an empty turn added nothing to the history; a
  text-only turn adds its content (about 6-8 K tokens). In the five overflow
  units this was small (`168_linearizability_kv_c` 9 such turns,
  `163_bigint_divmod_d` 1, `192_pike_regex` 1, the others 0), so it did not
  drive the overflows; tool results did.
- **What I_T3 cannot say:** the patch's effect on the score. It ran inside
  a composite with the turns and the cap, and the patch is policy (Max,
  2026-11-24), so it is not ablated. The mechanism evidence is clean; the
  outcome evidence is shared with the other two changes.

## 8. Per unit

Every unit that failed in I_T3 or INK-T2-STOCK. Cells: verdict class (for
I_T3, from the validator line: `cpu-budget`, `wrong-answer`,
`crash/exception`, `build-fail`, `missing/incomplete`, `dlsym-stop`) ·
turns used / turn limit (how it ended: `limit`, `wall`, or `stop` for an
earlier end) · share of the unit's wall. Capped: empty / text only / with a
call. The class rule is a simple one over the validator line and can
differ from main report §12.3's hand classes on the stock column; the stock
column here gives only the verdict.

| Unit | INK-T2-STOCK | capped | **INK-T3** | capped |
|---|---|---|---|---|
| 122_gemm_blocked_d | fail · 24/60 (stop) · 4% | 0/0/0 | dlsym-stop · 29/180 (stop) · 8% | 0/0/0 |
| 158_karatsuba_bytes_c | PASS · 21/60 · 8% | 0/0/1 | crash/exception · 180/180 (limit) · 20% | 0/0/0 |
| 163_bigint_divmod_a | PASS · 60/60 · 18% | 0/0/2 | wrong-answer · 180/180 (limit) · 80% | 0/16/0 |
| 163_bigint_divmod_b | fail · 60/60 (limit) · 36% | 0/0/13 | wrong-answer · 67/180 (stop) · 39% | 0/0/2 |
| 163_bigint_divmod_d | fail · 60/60 (limit) · 24% | 2/0/4 | **overflow** at reply 85 · 180/180 (limit, retries) · 88% | 0/1/9 |
| 163_bigint_divmod_e | fail · 60/60 (limit) · 15% | 0/0/2 | PASS · 88/180 · 14% | 0/0/0 |
| 163_bigint_divmod_f | fail · 60/60 (limit) · 25% | 7/0/6 | wrong-answer · 113/180 (wall) · 100% | 0/0/0 |
| 168_linearizability_kv_a | fail · 60/60 (limit) · 79% | 9/0/0 | cpu-budget · 50/180 (wall) · 100% | 0/10/0 |
| 168_linearizability_kv_b | fail · 60/60 (limit) · 15% | 0/0/0 | cpu-budget · 180/180 (limit) · 72% | 0/8/0 |
| 168_linearizability_kv_c | fail · 60/60 (limit) · 51% | 19/0/1 | build-fail · 98/180 (wall; **overflow** from turn 49) · 100% | 0/9/15 |
| 168_linearizability_kv_d | fail · 60/60 (limit) · 58% | 25/0/8 | cpu-budget · 56/180 (wall) · 100% | 0/1/0 |
| 168_linearizability_kv_e | fail · 60/60 (limit) · 50% | 3/1/3 | cpu-budget · 42/180 (wall) · 100% | 0/4/2 |
| 168_linearizability_kv_f | fail · 60/60 (limit) · 21% | 0/0/2 | cpu-budget · 62/180 (wall) · 100% | 0/1/0 |
| 173_taskgroup_cancel | fail · 60/60 (limit) · 35% | 0/0/21 | crash/exception · 180/180 (limit) · 22% | 0/0/0 |
| 176_intermittent_500s_a | fail · 100/100 (limit) · 12% | 0/0/0 | **overflow** at reply 242 · 300/300 (limit, retries) · 37% | 0/0/0 |
| 178_codebase_qa | fail · 60/60 (limit) · 15% | 0/0/0 | PASS · 69/180 · 42% | 0/0/0 |
| 183_heston_little_trap | fail · 60/60 (limit) · 35% | 15/0/6 | **overflow** at reply 106 · 180/180 (limit, retries) · 69% | 0/0/1 |
| 190_autograd_mlp | fail · 60/60 (limit) · 30% | 2/0/7 | wrong-answer · 73/180 (wall) · 100% | 0/0/12 |
| 192_pike_regex | fail · 60/60 (limit) · 13% | 3/0/0 | **overflow** at reply 68 · 180/180 (limit, retries) · 79% | 0/1/0 |
| 195_riscv_insn_budget | fail · 60/60 (limit) · 33% | 17/1/0 | missing/incomplete · 69/180 (wall) · 100% | 0/36/1 |
| 196_polyphase_resampler | PASS · 60/60 · 22% | 0/0/1 | cpu-budget · 180/180 (limit) · 15% | 0/0/0 |

Wall = the task's `max_wall_s` × 2 (the arm's `--wall-scale 2`); the turn
limit is that base wall over the arm's seconds per turn (15 for the stock
row, 5 here). The I_T3 capped counts are the board's view (the four
overflow units' first-file transcripts).

## 9. Time, tokens and speed

| | INK-T2-STOCK | INK-T3 |
|---|---:|---:|
| Unit-hours (Σ unit time) | 4.26 | 8.66 |
| … in failing units / passing units | 2.78 / 1.47 | 6.77 / 1.89 |
| Median unit time (all / pass / fail) | 122 / 44 / 495 s | 126 / 39 / 1,447 s |
| Mean minutes per unit | 3.6 | 7.3 |
| Units both passed (50): median, sum | 38 s, 74 min | 37 s, 97 min |
| Median turns of a pass | 10 | 10 |
| Completion tokens | 4.90 M | 8.48 M |
| Prompt tokens | 64.9 M | 203.1 M |
| Wall-clock of the arm | 4 h 18 min | 8 h 42 min (+ 1 h 05 min rerun) |
| Requests (server window) | 2,032 | 3,205 |
| Decode, replies of 256+ tokens: median (p10-p90) | 467 (363-599) tok/s | 428 (340-544) tok/s |
| Decode, replies at the cap: median | 512 tok/s (8,192) | 503 tok/s (16,384) |
| Aggregate decode (generated / decode seconds) | 444 tok/s | 428 tok/s |
| Drafts accepted (token-weighted) | 88.7% | 91.6% |
| Prompt served from cache | 96.5% | 90.9% |
| Prefill seconds | 304 | 2,047 |
| Median prompt per request | 21.7 K | 39.6 K |

Decode by prompt size, I_T3's main window (replies of 1,000+ tokens): under
50 K 436 tok/s (n 694), 50-100 K 479 (257), 100-200 K 393 (220), 200 K+ 334
(73). INK-T2-STOCK: 445, 496, 422 (30), and one reply over 200 K at 183.
The lower median is the longer sessions, not a slower serve: at the same
prompt sizes the two serves decode alike. The cache share is lower and the
prefill time 6.7 times higher; the cause was not investigated (long
sessions and wall kills are candidates).

Token figures are the rows' (the runner's `usage` sums, board merge); the
server log's own totals for the main window agree within 0.1% (8,484,542
generated, 203,158,812 prompt; the eight wall-killed rows carry partial
stats).

## 10. Integrity and the dlsym stop

- **Scan** (the review-1124 scanner, the sealed review
  `evals/audit/reviews/ink-t3-integrity-2026-11-25.md`): 75 transcripts,
  71 units, **0 contaminated**. Refused `web_search` / `fetch` attempts in
  seven units (the Algorithm D units `163_*`, `126_hmac_scratch`, Heston in
  `183`, the Pike VM in `192`'s rerun); no listing of an answer tree in any
  format, no shell command reached off the host.
- **INK-T2-STOCK rescanned** with the same scanner (to compare capped
  turns): one hit, `168_linearizability_kv_e`, kind `network`, evidence
  `nc =`: the Rust source inside a Python heredoc names a variable `nc`. A
  false positive of the review-1124 rule (`nc` followed by anything was
  read as a host). Fixed in `evals/audit/integrity_scan.py` (a host must be
  a name or an address), with a test; INK-T2-STOCK stays 0 contaminated.
  The other rig arms' rescan is still open (TODO).
- **`122_gemm_blocked_d`** stopped at the Linux `dlsym` check (turn 29), as
  in every Inkling and Qwen3.8 T2 arm. Rebuilt from the transcript and
  replayed on the rig (`~/ob-cal/.claude/gemm-replay-ink-t3/`): the original
  validator FAIL (`dlsym`), the proposed next-era rule **PASS in 3 of 3** (6
  hidden cases), the patched validator byte-identical to the earlier
  replays'. Validator note in `exclusions.json`; not adjusted (53/71 under
  the proposed rule).

## 11. Decisions and recommendations

### 11.1 Taken (DECISIONS-2026-11-25 §4)

1. **I_T3's 52/71 does not beat INK-T2-STOCK's 53/71; the RTX 5090 default
   stays stock** (board row `INK-T2-STOCK`, profile `inkling2-mini-stock`,
   `AGENT_PROFILE=inkling-best`; Max's decision of 2026-11-24). `INK-T3`
   is listed on the board and never shown as the pack's best.
2. **The follow-ups `I_T3_t15` and `I_T3_8k` are not queued.** They would
   take apart a composite that measured level with its base; each costs
   4-10 h of the rig's GPU, and the composite's distinctive cost (the
   overflows) comes from a harness gap that the next era closes.
3. **The next Inkling step waits for the next-era overflow fix**
   (HARNESS_NOTES proposal (a)): with compaction on Basalt, the five
   overflow units would compact instead of retrying, and a wall-bound arm
   becomes a fair test of more turns.

### 11.2 Recommended, not applied: keep #632 in the default build

The shipped 5090 default build is Basalt's pin plus the #632 patch
(`scripts/engines/basalt.sh`, the profile's `PATCHES` key; #319 made the
profile say so). No board row has measured that exact build at the
default's args (8,192 cap, `--answer-room auto`, 60 turns at 900 s); I_T3
measured the patch inside a composite. What I_T3 says about the patch:

- It removes the failure it targets, completely, at agent scale: 0 empty
  capped turns in 3,204 replies, against 105 to 226 per unpatched arm, with
  no other server-side change visible (the same decode speed at equal
  context, conformance PASS in Strata §16).
- What it leaves (text-only capped turns) is visible to the user and to the
  model's next turn, where an empty reply was not. For interactive use that
  is strictly better: a user sees what the model was doing.
- It costs nothing measurable here: the row is level with the unpatched
  stock row, inside noise, and the only new failure mode (overflow) traces
  to turns, cap and the runner, not to the patch.

**Recommendation: keep the patch in the default build**, and keep the
existing plan to measure the default exactly once the GPU is free (TODO
"Basalt after the switch": run the default as an arm before calling it
measured) and to drop the local patch when upstream merges #632. Max
decides; nothing in the profile changes in this PR.

## 12. Caveats

- **Single seed, bundled changes.** One draw of a three-change composite.
  The paired tests say "level with the stock row"; they cannot say which
  change helped or hurt by how much. The mechanism counts in §7 are counts.
- **The overflow tooling is after the fact.** The harness9 rows say
  `server_error`; the verdicts are in `overflow.json` with evidence. The
  next era records `context_overflow` at the source (HARNESS_NOTES (b)).
- **The rerun is not a measurement under the rule.** It is committed as
  the run's record. That it failed all four without an overflow says the
  overflows are not deterministic, not that the units were winnable.
- **Engine build differs from every other Inkling row** (the #632 patch),
  and the serve's `--answer-room 8192` differs from the T2 arms' `auto`;
  both are part of the composite and named in the row's host and config.
- **Clock-seeded,** like INK-T2 and INK-T2-STOCK; INK-T2-S1 is the only
  seeded Inkling draw.
