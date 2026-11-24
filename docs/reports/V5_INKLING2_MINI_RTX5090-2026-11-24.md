# v5 report: Inkling 2 Mini on the RTX 5090, arm I_T2 and its ablations (2026-11-24)

> **Final for I_T2 (2026-11-24).** Arm `I_T2` of `v5-thinking-first` ran
> Inkling 2 Mini (Blackfrost's Derisked weights, converted to a Basalt
> NVFP4-MX8 pack) on Basalt v0.5.7, one RTX 5090, from the rig's eval-host
> container: T2's switches at one stream, as Q_T2 was for Qwen3.8. It
> finished with no unit lost, was scanned, and is on the board as row
> `INK-T2`. **Updated 2026-11-24 (afternoon) with two of its three ablation
> arms (§10):** `I_T2_s1` (the same arm under seed 1: 51/71, row
> `INK-T2-S1`, a seed replicate that never ranks) and `I_T2_stock`
> (Basalt's stock pack in place of the Derisked one: 53/71, row
> `INK-T2-STOCK`, SCORE 78.2, now #4 and the top RTX 5090 row). The third,
> `I_UP8k`, ended 14:28 PST at 49/71 (§10.9, read in §12.6). **The
> improved arm `I_T3`** (stock pack + wall-bound turns + a 16,384 cap with
> an 8,192 answer room + Basalt's #588 patch) is staged, not launched: §12. Engine choice and its review:
> [STRATA_INKLING_5090-2026-11-23.md](STRATA_INKLING_5090-2026-11-23.md)
> §11 and §15; decisions: [DECISIONS-2026-11-24.md](DECISIONS-2026-11-24.md).

**Regenerate the tables.** §4 comes from the committed result files
through [`evals/v5_report.py`](../../evals/v5_report.py), scored exactly as
[`evals/v5_board.py`](../../evals/v5_board.py) scores the board (unit ids
and verdicts only). The paired test is
[`evals/ablation.py`](../../evals/ablation.py). Offline, in a fresh clone:

```bash
python3 evals/v5_report.py --model "Inkling 2 Mini Derisked (Basalt NVFP4-MX8)" \
    --context Q38U-T2,DS-T2,HY4-T2,OPUS-REF --pair Q38U-T2:I_T2 \
    --write docs/reports/V5_INKLING2_MINI_RTX5090-2026-11-24.md
# the paired test against Q38U-T2: Q_T2's two files merged as the board merges them
# (the merge is written under the main file's name, so exclusions.json still applies)
B=evals/boards/v5; M=$(mktemp -d)
python3 - "$M" <<'PY'
import json, sys; sys.path.insert(0, "evals"); import v5_board
B = "evals/boards/v5/"; f = "eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-"
_, rows = v5_board.merge_runs([B + f + "20261123-115749.json", B + f + "20261123-231112.json"])
d = json.load(open(B + f + "20261123-115749.json")); d["tasks"] = list(rows.values())
json.dump(d, open(f"{sys.argv[1]}/{f}20261123-115749.json", "w"))
PY
python3 evals/ablation.py --force "$M"/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-115749.json \
    $B/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-20261124-045843.json
```

The ablations' tables (§4 now carries their rows and pairs; §10):

```bash
python3 evals/v5_report.py --model "Inkling 2 Mini Derisked (Basalt NVFP4-MX8)" \
    --context INK-T2-STOCK,Q38U-T2,DS-T2,HY4-T2,OPUS-REF --pair Q38U-T2:I_T2 --pair I_T2:I_T2_s1 \
    --pair I_T2:INK-T2-STOCK --pair I_T2_s1:INK-T2-STOCK --pair Q38U-T2:INK-T2-STOCK \
    --write docs/reports/V5_INKLING2_MINI_RTX5090-2026-11-24.md
I=$B/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-20261124-045843.json
S=$B/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-s1-20261124-095656.json
K=$B/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t2-stock-20261124-140740.json
# B and M as in the block above
python3 evals/ablation.py --force $I $S      # seed noise (the slugs differ, so --force)
python3 evals/ablation.py --force $I $K      # stock against Derisked; and $S $K
python3 evals/ablation.py --force "$M"/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-115749.json $K
python3 evals/ablation.py $B/eval-deepseek-v4-1-flash-v5-thinking-first-t-20261118-015132.json \
    $B/eval-deepseek-v4-1-flash-v5-thinking-first-t-s1-20261119-074954.json   # DeepSeek's seed pair
```

The Basalt speed and acceptance (§10.6) need the rig's saved server logs
(`~/openbeast-runs/basalt-server-logs/`, not committed):
`python3 evals/audit/basalt_log.py <log> --since <START> --until <END>`
with the arm's START and END lines of its `campaign.log` (UTC); the
commands are in the sealed review
`evals/audit/reviews/ink-t2-s1-stock-integrity-2026-11-24.md`.

`--force`: the pair is two models (and two engines), which the tool flags as
"not a clean pair". The era, arm, host, tree and stream count are the same.
The "all units" row in §5 uses the tool's own `contrast()` with its `all`
churn (0.15); the CLI prints the per-language and compiled rows.

The transcript counts (§3.4, §6) need the rig's transcripts, which are not
committed (`~/ob-ink/.claude/worktrees/h9/agents/logs/`, 71 files): run the
integrity scan with `--json` (command in the sealed review,
`evals/audit/reviews/ink-t2-integrity-2026-11-24.md`), then
`python3 evals/audit/capped_turns.py <scan.json>`.

---

## 1. Executive summary

**The ablations (added 2026-11-24 afternoon; §10):**

- **Seed noise is small in passes, large in SCORE.** `I_T2_s1` (I_T2 under
  seed 1) passed 51/71 against I_T2's 49: 2 of 71 units flipped, both
  gains, none lost (p 0.50). DeepSeek's seed pair (DS-T against DS-Ts1)
  also flipped 2 units, one each way. But SCORE moved +1.8 on Inkling and
  −2.8 on DeepSeek, both on the same SOLVE, through LANG alone: **a SCORE
  gap under about 3 points between two single-seed rows is inside one
  seed's noise.** INK-T2-S1 is recorded as a seed replicate and never
  ranks (a new board rule, `replicate_of`; DECISIONS-2026-11-24 §7).
- **Basalt's stock pack did better than the Derisked pack, within noise.**
  `I_T2_stock` passed **53/71 (SCORE 78.2, #4 on the board, the top
  RTX 5090 row)**. Paired against I_T2: 5 gained, 1 lost (+4, p 0.22);
  against I_T2_s1: 3 gained, 1 lost (+2, p 0.63). The three units the
  stock pack gained in both views are two Knuth long-division ports and
  one resampler, where the Derisked arms had empty capped turns. **105
  empty capped turns** (Basalt #588) against 203 and 167 for the two
  Derisked arms; the stock pack's KLD is half the Derisked one's (0.0128
  against 0.0263).
- **Stock against Q38U-T2: parity, not a lead.** Over the 65 units both
  measured cleanly, Qwen3.8 52, stock Inkling 50 (2 gained, 4 lost, −2,
  p 0.69); on the board's view (68 units) level. 53 against 52 scored
  passes and SCORE 78.2 against 75.6 are inside seed noise.
- **Decision unchanged: Qwen3.8 stays the RTX 5090 default** until the
  improved arm (stock pack + Basalt PR #632 as a local patch) and ideally a
  seed repeat of it confirm (§10.8; DECISIONS-2026-11-24 §6).
- **Integrity: both clean** (0 contaminated; attempts only, all refused;
  no shell route off the host). Both stopped `122_gemm_blocked_d` (Rust) at
  the Linux `dlsym` check; replayed under the proposed rule, both PASS (3 of
  3 runs each); notes, not adjustments.
- **The Basalt server log was kept this time** and agrees with the
  transcripts exactly (requests, cap endings, empty turns, token sums).
  Decode over all replies 484 tok/s (Derisked, seed 1) and 444 (stock);
  drafts accepted 90.2% and 88.7% (token-weighted) (§10.6).
- **`I_UP8k` is still running** (started 2026-11-24 18:25 UTC; §10.9).

**The first arm, I_T2:**

- **Inkling 2 Mini Derisked passed 49/71 (SCORE 76.5) under arm I_T2**,
  single seed, no unit lost, no unit excluded. **#4 on the v5 board by
  SCORE**, one place above the RTX 5090 default's Q38U-T2 (52/71 scored,
  SCORE 75.6, now #5). The two rows have the same SOLVE (72.3); Inkling's
  higher SCORE is all LANG, language breadth over multi-language tasks
  (89.1 against 85.6). As a fraction of the Claude Opus 5.5 reference
  (71/71, SCORE 100.0; a different scaffold, unranked) that is 76.5%.
- **Against Q38U-T2, paired: 6 behind, within noise.** Over the 65 units
  both rows measured cleanly (Q38U-T2's 3 lost units and 3 integrity
  exclusions dropped), Q38U-T2 passed 52 and Inkling 46: Inkling gained 1
  and lost 7, net −6, McNemar p 0.07, inside the minimum detectable effect
  of 10. On the board's view (Q38U-T2's excluded passes counted as
  fails, 68 units) it is −4. Same harness9 tree, era, arm, host,
  container and stream count; the model, its weights and the engine
  differ (§5).
- **Fast: 4.5 hours for the whole arm, against about 13.5 for Q_T2.** Median
  unit 47 s (21 s for the units it passed; mean 3.8 min), against Q38U-T2's
  254 s (mean 9.6 min). On the 205 replies that ran to exactly 8,192
  tokens the end-to-end rate, prefill included, was median 520 tok/s
  (p10 424, p90 581) against 142 for Q_T2 on llama.cpp (296 such
  replies), about 3.7 times as fast. No unit hit its wall: Inkling's
  failures end at the turn limit (21 units ended there, 3 of them passes)
  (§3.3).
- **Integrity: clean.** 71 transcripts, 71 units, 0 contaminated. Five
  refused `web_search` calls in four units (attempts); a wider hand check
  of every tool call for a shell route off the host (the Linux gap that
  cost the two Qwen rows six exclusions) found none (§3.5).
- **`122_gemm_blocked_d` (Rust) stopped at the Linux `dlsym` binary check:
  verdict not measured; replayed under the proposed Rust-only rule: PASS**
  (6 hidden cases, 3 of 3 runs; the unpatched validator reproduces the
  stop). Recorded as a `validator_notes` entry; not adjusted (it would be
  50/71) (§3.6).
- **Basalt caveats.** 203 turns ended empty at the 8,192 cap, no answer and
  no call (Basalt #588: the answer-room close does not force an answer on
  Inkling), in 21 units, 199 of them in 17 of the 22 failed units. Q_T2 on
  llama.cpp had 305 empty capped turns (its thinking may run to the whole
  cap), so this is not a Basalt-only failure mode, but on Basalt the close
  was meant to prevent it. Basalt's tool-call parsing is lenient; 88
  capped turns still carried a tool call (Q_T2: 5). The server log that
  would split either count by mechanism was not kept (§6).
- **Decision: Qwen3.8 stays the RTX 5090 default** (§7). Inkling's scored
  row does not beat Q38U-T2: 49 against 52 scored passes, 46 against 52 on
  the paired units, and the SCORE lead (0.9) is breadth, not more solved
  tasks. Basalt is also not ready to be a default (Strata report §15.8).
  The board's order is SCORE, so Inkling sits above Qwen there; the
  website follows the board (§7).

## 2. Setup

### 2.1 Weights and serving

| | |
|---|---|
| Weights | Basalt NVFP4-MX8 pack `Inkling-2-Mini-Derisked-NVFP4-MX8.basalt`, sha256 `d2b01bcc1fd2dc51cbcdaecb512c56d7c5db75181fee2d9b6c944e76e217a427`, 19,606,374,132 bytes, converted on the rig 2026-11-23 20:47 PST |
| Source | `Blackfrost-AI/Inkling-2-Mini-Derisked` @ `31ec2eddecfdf93c8626d9e4dfc0a2aca6070900`, a refusal-direction edit of `thinkingmachines/Inkling-2-Mini` (base revision unverified) |
| Converter | Basalt's `tools/convert/convert.py` at the engine pin, offline, CPU only, `--network none`, read-only (Strata report §11.1, §15.5) |
| Engine | Basalt v0.5.7 @ `400b411c58031d064812ea21a259b2381acdaa3e`, built from source offline; `bin/basalt` sha256 `5dc8a63c…`; image `ob-strata-env:2026-11-23` (`sha256:0c44ed98…`) |
| Serve | `basalt serve --alias inkling-2-mini --parallel 1 --max-context 262144 --kv fp8`, DSpark at the pack default, `--answer-room auto`: 6,144 thinking tokens of the runner's 8,192-token turn, then the thinking is closed |
| Sandbox | no TCP egress (DNS still resolved for this serve; fixed for the ablations), read-only root and weights, uid 1000, `--cap-drop ALL`, port on 127.0.0.1 only (Strata report §15.4) |
| Lifetime | container started 2026-11-23 20:57:51 PST, pack re-hashed at 20:57:58 (same sum), removed 2026-11-24 01:29:33 PST after the last unit; no restart policy; all 71 rows report model `inkling-2-mini` and 0 API errors |

The full record: the `inkling2-mini-blackfrost-derisked-basalt-nvfp4-mx8`
entry of [`evals/boards/v5/weights.json`](../../evals/boards/v5/weights.json),
rendered under [Weights used](../RESULTS.md#weights-used). A converted pack
is on no Hub repo, so the entry is pinned by the file's sha256 with its
source in `derived_from` (`evals/v5_board.py` now accepts and requires that
shape for a locally built file).

**Why this engine and pack** (DECISIONS-2026-11-23 §11): Basalt was the one
engine that returns thinking and tool calls in one turn on Inkling with
conformance PASS, and the fastest single stream (403 / 356 tok/s code /
prose on its bench, thinking off). The Derisked pack follows the
uncensored-preferred policy; its divergence from a near-reference (KLD
0.0263, top-1 92.93%) is about twice the stock pack's (0.0128). The stock
pack's row is the queued `I_T2_stock` control.

### 2.2 The harness

| | I_T2 (this row) | Q_T2 (Q38U-T2) |
|---|---|---|
| Tree | harness9 `f38a29c7`, era `036638ddd68698ce` | the same |
| Arm switches | thinking on, verify-done, edit hint, env block, wall warn, act nudge, 8,192 turn cap, packs v3, wall ×2, wall-scaled turns | the same |
| Streams | `--jobs 1 --slots 1` | the same |
| Harness host | the rig's eval-host container `openbeast-eval-host:8d1e28442569` | the same image |
| Engine | Basalt v0.5.7 + DSpark | llama.cpp `b1b770d90` + MTP |
| Thinking room per turn | 6,144 (then closed; §6.1) | up to the whole 8,192 |
| Run | 2026-11-24 04:58:42 to 09:29:33 UTC (4 h 31 min) | 2026-11-23 11:57 to 2026-11-24 01:26 UTC (main 11.2 h + r1 rerun 2.3 h) |

The results header records `inference_engine.name: llama.cpp` with the
harness tree's llama-server path: that field is the harness's default
label, not a reading of the server. `server.engine: basalt` and
`server.model_id: inkling-2-mini` come from the server's `/v1/models` and
are the record.

The rig's eval-host container was calibrated against the Mac harness host
on 2026-11-23 with DeepSeek on this same harness9 tree
([V5_EVAL_HOST_CALIBRATION-2026-11-23.md](V5_EVAL_HOST_CALIBRATION-2026-11-23.md)):
no host effect beyond run-to-run variation, plus the two known Linux
effects (the Rust `dlsym` check, §3.6; the agent shell's missing network
deny, §3.5). It carries to this model and engine by inference.

## 3. Result

### 3.1 The row

49/71 passed, 49 scored. SCORE 76.5 = 0.75 × SOLVE 72.3 + 0.25 × LANG
89.1. By language: Python 23/32, Go 6/8, C 5/8, C++ 6/8, Rust 5/8, Zig
3/6, shell 1/1. Failed (22): `122_gemm_blocked_d`, `122_gemm_blocked_e`,
`158_karatsuba_bytes_c`, `163_bigint_divmod_a` to `_f` (all six),
`168_linearizability_kv_a` to `_f` (all six), `173_taskgroup_cancel`,
`176_intermittent_500s_a`, `183_heston_little_trap`, `190_autograd_mlp`,
`192_pike_regex`, `195_riscv_insn_budget`, `196_polyphase_resampler`.
Two task families account for 12 of the 22: Knuth long division
(`163_bigint_divmod`, 0/6) and the linearizability checker
(`168_linearizability_kv`, 0/6; Q38U-T2 also passed none of the five it
measured). How they failed, from the validator lines: 8 at the
validator's CPU budget (6 s), 3 on wrong output, one missing file
(`divmod.zig`), one crash (the Zig GEMM, status −11), one at the `dlsym`
check, and 8 on single errors (a Python exception, a misuse case, a named
hidden test, a missing function, a trap).

### 3.2 Token usage

From the board (`v5_board.py --json`, the runner's usage sums):

| Row | Prompt | Completion | Total | Per scored pass |
|---|---:|---:|---:|---:|
| INK-T2 | 70,883,493 | 5,367,401 | 76,250,894 | 1,556,141 |
| Q38U-T2 | 57,838,480 | 4,697,822 | 62,536,302 | 1,202,621 |

Inkling read 23% more prompt and wrote 14% more completion tokens over the
arm. Each model counts with its own tokenizer, so totals compare only
roughly between models. On the 45 units both passed, Inkling used fewer
completion tokens on 31 and more on 14 (median −2,182, sign test p 0.016)
and fewer turns on 30, more on 10 (median −3, p 0.002): **where both
solve, Inkling solves in fewer turns and tokens.** Its extra total is in
the failures, which run to the turn limit (§3.3).

### 3.3 Time, the wall and the turn limit

| | INK-T2 | Q38U-T2 |
|---|---:|---:|
| Whole arm, wall clock | 4 h 31 min | ~13.5 h (11.2 + 2.3) |
| Σ unit time | 4.47 h | 10.84 h (68 units with a verdict) |
| Median unit | 46.9 s | 253.9 s |
| Mean unit (board) | 3.8 min | 9.6 min |
| Median passed unit | 21.3 s (49) | 124.9 s (54 raw passes) |
| Median failed unit | 521 s (22) | 1,704 s (14) |
| Units under 60 s | 37 of 71 | 14 of 68 |
| Units at the wall (`agent_exit_code −1`) | **0** | 9 (2 passed) |
| Units at the turn limit | **21** (3 passed) | 14 (5 passed) |
| End-to-end tok/s, replies of exactly 8,192 tokens | median 520 (p10 424, p90 581; n 205) | median 142 (p10 124, p90 166; n 296, main pass) |

Medians and sums are over each row's units with a verdict
(`elapsed_seconds`, the board's merge); the token rate is 8,192 over the
time from the turn's start to its response in the transcript, so prefill
and request overhead are inside it and it is a lower bound on decode
speed. Live reads of the Basalt log during the run (session notes) put
decode at about 530 to 680 tok/s per reply in agent turns; that log was
not kept (§6.3), so this report's own figures are the transcript ones. For scale, the
engine bench of 2026-11-23 measured 403 / 356 tok/s code / prose on short
thinking-off prompts (Strata report §11.2).

**The speed story, read honestly.** Basalt's single stream makes the whole
arm about three times shorter, and the units Inkling solves take seconds,
not minutes. It does not buy more passes here: no Inkling unit reached
its wall, so the extra time a wall-scaled run allows was never the limit.
The failures run out of turns (60 for most units, 40 or 100 for some),
often after many capped or empty turns (§6.1). A faster engine helps a
model that is close to an answer when time runs out; Inkling's failures
were not of that kind on this suite.

### 3.4 What the transcripts show per turn

`evals/audit/capped_turns.py` over the 71 attributed transcripts: 1,935
turns, 293 ended at the cap (`length`): 203 empty, 2 with some answer text,
88 still carrying one tool call. Per unit in §6.

### 3.5 Integrity

Scanned on 2026-11-24 from copies of the rig's files (`--tz UTC`,
`--until I_T2=2026-11-24T09:29:33` from the campaign log): 71 transcripts,
71 units, 0 unattributed, **0 contaminated**. Attempts only (refused by the
eval guard): `web_search` in `163_bigint_divmod_a` (1), `_d` (2), `_f` (1)
and `183_heston_little_trap` (1), all fails. The Linux agent shell has no
network deny (HARNESS_NOTES next-era (d)); in each Qwen row an agent
reached the network that way after a refusal. A hand pass over every tool
call (any non-loopback URL; curl, wget, nc, pip, git, cargo and go fetches,
Python sockets and URL readers, `/dev/tcp`) found nothing here. Sealed
notes: `evals/audit/reviews/ink-t2-integrity-2026-11-24.md`.

### 3.6 `122_gemm_blocked_d`: the `dlsym` check, replayed

The row reads `the multiply must be your own code: the binary imports
dlsym`: the Linux-host validator bug of the calibration report (§5 there).
Rust's std imports `dlsym` for any thread, and this solution uses
`std::thread::scope`. By the
[MODEL_CAMPAIGN procedure](../MODEL_CAMPAIGN.md#eval-host-in-a-container-linux),
step 1, the replay:

- The final `gemm.rs` was written by one quoted bash heredoc (transcript
  line 10) and never edited after; recovered exactly (2,333 characters,
  sha256 `899eaeaf…`), and the in-container rebuild gave the same sum.
- PR #303's replay script, restricted to this attempt in a new directory
  on the rig (`~/ob-cal/.claude/gemm-replay-ink/`), ran the harness9
  tree's own validation lifecycle in the eval-host container at timeout
  scale 2.0. Its patched validator is byte-identical to #303's.
- Unpatched validator: FAIL, `imports dlsym` (the recorded line).
  Proposed rule (Rust on ELF: `dlsym` allowed when no other undefined
  symbol matches `_BAD_IMPORT`): **PASS, 6 hidden cases, in 3 of 3 runs**.

`exclusions.json` `validator_notes`, run `INK-T2`: "a stop at the binary
check on std's dlsym (Linux host): verdict not measured; replayed under the
proposed next-era rule (docs/HARNESS_NOTES.md): verdict PASS, 6 hidden
cases". Not adjusted: the proposed rule is not the suite's rule until the
next era. Q38U-T2's own `122_gemm_blocked_d` stopped the same way and also
passes under the proposed rule, and it is excluded besides, so the unit is
dropped from the pair in §5 either way.

## 4. Tables (generated)

<!-- v5-report:start -->
<!-- generated by evals/v5_report.py for Inkling 2 Mini Derisked (Basalt NVFP4-MX8): do not hand-edit -->

**Arms**

| Arm | Run | Weights | On the board | Era | Turn cap | Passed | SCORE | SOLVE | LANG |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| I_T2 | INK-T2 | Basalt NVFP4-MX8 pack · Blackfrost Derisked · abliterated | yes | 036638ddd68698ce | 8192 | 49/71 | 76.5 | 72.3 | 89.1 |
| I_T2_s1 | INK-T2-S1 | Basalt NVFP4-MX8 pack · Blackfrost Derisked · abliterated | yes | 036638ddd68698ce | 8192 | 51/71 | 78.3 | 72.3 | 96.4 |
| Claude Opus 5.5: OPUS-REF | OPUS-REF | Anthropic API · hosted, weights not public | yes | — | — | 71/71 | 100.0 | 100.0 | 100.0 |
| DeepSeek-V4.1-Flash (Mia EXL3): T2 | DS-T2 | EXL3 2.9 bpw · Mia EAQ v1.1 -v2 · stock | yes | 036638ddd68698ce | 8192 | 65/71 | 94.9 | 96.9 | 88.9 |
| Hy4-flash (NVFP4): H_T2 | HY4-T2 | NVFP4 · atakans build · stock | yes | 036638ddd68698ce | 8192 | 60/71 | 83.4 | 81.5 | 89.0 |
| Qwen3.8 27B Uncensored MTP (the shipped default): Q_T2 | Q38U-T2 | GGUF Q5_K_M · JonathanColetti Uncensored (MTP head kept) · abliterated | yes | 036638ddd68698ce | 8192 | 52/71 (54 raw) | 75.6 | 72.3 | 85.6 |
| Inkling 2 Mini (Basalt NVFP4-MX8): I_T2_stock | INK-T2-STOCK | Basalt NVFP4-MX8 pack · Basalt's official pack · stock | yes | 036638ddd68698ce | 8192 | 53/71 | 78.2 | 75.4 | 86.7 |

**Per language** (passed / units of that language; an in-progress arm's passes are over the units done so far)

| Arm | Py | Go | C | C++ | Rust | Zig | Sh |
|---|---:|---:|---:|---:|---:|---:|---:|
| I_T2 | 23/32 | 6/8 | 5/8 | 6/8 | 5/8 | 3/6 | 1/1 |
| I_T2_s1 | 23/32 | 6/8 | 6/8 | 6/8 | 5/8 | 4/6 | 1/1 |
| Claude Opus 5.5: OPUS-REF | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 |
| DeepSeek-V4.1-Flash (Mia EXL3): T2 | 30/32 | 7/8 | 7/8 | 7/8 | 8/8 | 5/6 | 1/1 |
| Hy4-flash (NVFP4): H_T2 | 26/32 | 6/8 | 7/8 | 8/8 | 8/8 | 4/6 | 1/1 |
| Qwen3.8 27B Uncensored MTP (the shipped default): Q_T2 | 23/32 | 7/8 | 6/8 | 6/8 | 6/8 | 3/6 | 1/1 |
| Inkling 2 Mini (Basalt NVFP4-MX8): I_T2_stock | 24/32 | 6/8 | 7/8 | 6/8 | 5/8 | 4/6 | 1/1 |

**Time and the wall** (wall hit = the harness killed the agent at the unit's wall, `agent_exit_code == -1`; turns are counted only where the row carries them, from harness9 on)

| Arm | Units done | Unit-hours | Mean min / unit | Wall hits | Passed at the wall | Failed at the wall | Turns of the wall-hit units |
|---|---:|---:|---:|---:|---:|---:|---|
| I_T2 | 71 | 4.5 | 3.8 | 0 | 0 | 0 | — |
| I_T2_s1 | 71 | 4.1 | 3.5 | 0 | 0 | 0 | — |

**Units gained and lost** (arm against base, over the units both have a verdict for)

| Base → arm | Compared | Net | Gained | Lost |
|---|---:|---:|---|---|
| Q38U-T2 → I_T2 | 68 | -4 | 122_gemm_blocked_b, 124_rkf45, 126_hmac_scratch | 158_karatsuba_bytes_c, 163_bigint_divmod_a, 163_bigint_divmod_b, 163_bigint_divmod_c, 163_bigint_divmod_e, 176_intermittent_500s_a, 196_polyphase_resampler |
| I_T2 → I_T2_s1 | 71 | +2 | 122_gemm_blocked_e, 158_karatsuba_bytes_c | — |
| I_T2 → INK-T2-STOCK | 71 | +4 | 122_gemm_blocked_e, 158_karatsuba_bytes_c, 163_bigint_divmod_a, 163_bigint_divmod_c, 196_polyphase_resampler | 178_codebase_qa |
| I_T2_s1 → INK-T2-STOCK | 71 | +2 | 163_bigint_divmod_a, 163_bigint_divmod_c, 196_polyphase_resampler | 178_codebase_qa |
| Q38U-T2 → INK-T2-STOCK | 68 | +0 | 122_gemm_blocked_b, 122_gemm_blocked_e, 124_rkf45, 126_hmac_scratch | 163_bigint_divmod_b, 163_bigint_divmod_e, 176_intermittent_500s_a, 178_codebase_qa |

**Units whose verdict differs across the arms** (P pass, F fail, · not run yet, L lost to infrastructure); every other unit has the same verdict in every arm

| Unit | I_T2 | I_T2_s1 |
|---|:---:|:---:|
| 122_gemm_blocked_e | F | P |
| 158_karatsuba_bytes_c | F | P |
<!-- v5-report:end -->

The generated "units gained and lost" row counts Q38U-T2's two excluded
passes (`124_rkf45`, `126_hmac_scratch`) as Q38U-T2 fails, as the board
does, so it shows 68 units and −4. §5 drops every unit either row did not
measure cleanly.

## 5. Against Q38U-T2, paired

### 5.1 The pair

Same tree (`f38a29c7`), era, arm switches, container image, rig and one
stream. Different: the model and weights (Inkling 2 Mini Derisked, a
refusal-direction edit, as a Basalt NVFP4-MX8 pack, against Qwen3.8 27B
Uncensored, abliterated, as a Q5_K_M GGUF) and the engine (Basalt with
DSpark against llama.cpp with MTP), including the thinking room per turn
(6,144 against 8,192) and the tool-call parsers. So this is a
model-plus-engine comparison, the one that matters for the rig's default.
6 pairs dropped: Q38U-T2's 3 lost units (`168_linearizability_kv_f`,
`175_crdt_orset_rga`, `190_autograd_mlp`, `server_error`) and its 3
integrity exclusions (`122_gemm_blocked_d`, `124_rkf45`,
`126_hmac_scratch`). Inkling passed 3 of those 6 (`124`, `126`, `175`).

| Contrast | n | Q38U-T2 passed | INK-T2 passed | b (Inkling gained) | c (Inkling lost) | net | p | Within churn |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| all units | 65 | 52 | 46 | 1 | 7 | −6 | 0.070 | yes (MDE 10) |
| Python | 28 | 23 | 20 | 0 | 3 | −3 | 0.25 | yes |
| Go | 8 | 7 | 6 | 0 | 1 | −1 | 1.0 | yes |
| C | 8 | 6 | 5 | 1 | 2 | −1 | 1.0 | yes |
| C++ | 8 | 6 | 6 | 0 | 0 | 0 | 1.0 | yes |
| Rust | 7 | 6 | 5 | 0 | 1 | −1 | 1.0 | yes |
| Zig | 5 | 3 | 3 | 0 | 0 | 0 | 1.0 | yes |
| Shell | 1 | 1 | 1 | 0 | 0 | 0 | 1.0 | yes |
| compiled | 36 | 28 | 25 | 1 | 4 | −3 | 0.38 | yes (MDE 9) |
| Go + C + C++ + Rust | 31 | 25 | 22 | 1 | 4 | −3 | 0.38 | yes |

- Gained: `122_gemm_blocked_b` (C).
- Lost: `158_karatsuba_bytes_c` (C), `163_bigint_divmod_a` (Python),
  `_b` (Go), `_c` (C), `_e` (Rust), `176_intermittent_500s_a` (Python),
  `196_polyphase_resampler` (Python).
- **Reading.** Qwen3.8 leads by 6 on the units both measured, single seed,
  p 0.07: not significant at 0.05 and inside the minimum detectable
  effect, but every contrast leans the same way and one of seven is a
  gain. Four of the seven losses are one family, Knuth long division
  (`163_bigint_divmod`), where Inkling went 0/6 and Qwen3.8 4/6; three of
  those four losses carry empty capped turns (17, 4 and 2; `_c` none).
  The board's SCORE order (Inkling 76.5 above 75.6) and this paired
  reading disagree in sign: SOLVE is level, Inkling's LANG is higher
  because it solves more of the multi-language tasks in at least one more
  language, and Q38U-T2's row is depressed by its 3 lost and 3 excluded
  units. Neither difference is resolved by one seed; `I_T2_s1` gives the
  noise margin (§8). *(It did, §10.2: 2 units flipped between seeds, and
  SCORE moved 1.8 on them alone, twice the 0.9 here.)*

### 5.2 Cost on the units both solve

On the 45 units both passed: Inkling fewer completion tokens on 31, more
on 14 (median −2,182, p 0.016); fewer turns on 30, more on 10 (median −3,
p 0.002). The push-diagnostics checker fired in 22 of Inkling's units
(Q38U-T2: 37), p95 latency 465 ms (Q38U-T2: 220 ms): Inkling wrote more
files through bash heredocs, which the checker does not see.

### 5.3 Cross-model, the same arm

At T2 on harness9: DS-T2 65/71 and HY4-T2 60/71 (Spark pair, `--jobs 4`,
Mac host), Q38U-T2 52/71 and INK-T2 49/71 (RTX 5090, `--jobs 1`, rig
container). This places the model; it does not separate host, engine or
concurrency effects.

## 6. Basalt caveats

### 6.1 Empty capped turns (upstream #588)

Basalt closes Inkling's thinking at 6,144 tokens with the template's
`<|end_message|>` and leaves the model to open the answer; the model can
open a second thinking block instead, and with the budget spent it thinks
to the 8,192 cap and ends with no answer and no call (Strata report §15.6
item 1; PR #632 would open an answer block; not in the pin). The runner
logs such a reply as `output_cap` with `reasoning_chars` (its content was
empty). **203 empty capped turns in 21 units**: 199 in 17 of the 22 failed
units, and 1 each in 4 passes.

| Unit | Verdict | Turns | At the cap | Empty | With a call |
|---|---|---:|---:|---:|---:|
| 158_karatsuba_bytes_c | FAIL (lost vs Q) | 60 | 32 | 12 | 19 |
| 163_bigint_divmod_a | FAIL (lost vs Q) | 58 | 17 | 17 | 0 |
| 163_bigint_divmod_b | FAIL (lost vs Q) | 60 | 5 | 4 | 1 |
| 163_bigint_divmod_d | FAIL | 60 | 27 | 15 | 12 |
| 163_bigint_divmod_e | FAIL (lost vs Q) | 60 | 4 | 2 | 2 |
| 163_bigint_divmod_f | FAIL | 60 | 24 | 24 | 0 |
| 168_linearizability_kv_a | FAIL | 59 | 23 | 21 | 2 |
| 168_linearizability_kv_b | FAIL | 60 | 6 | 1 | 5 |
| 168_linearizability_kv_c | FAIL | 60 | 38 | 35 | 2 |
| 168_linearizability_kv_d | FAIL | 60 | 19 | 17 | 2 |
| 168_linearizability_kv_e | FAIL | 60 | 18 | 14 | 4 |
| 168_linearizability_kv_f | FAIL (Q lost) | 60 | 6 | 2 | 4 |
| 173_taskgroup_cancel | FAIL | 60 | 4 | 4 | 0 |
| 183_heston_little_trap | FAIL | 60 | 3 | 2 | 1 |
| 190_autograd_mlp | FAIL (Q lost) | 60 | 7 | 2 | 5 |
| 192_pike_regex | FAIL | 60 | 44 | 24 | 20 |
| 195_riscv_insn_budget | FAIL | 60 | 3 | 3 | 0 |
| 143_uov | PASS | 7 | 1 | 1 | 0 |
| 158_karatsuba_bytes_b | PASS | 9 | 1 | 1 | 0 |
| 167_q15_biquad_f | PASS | 40 | 1 | 1 | 0 |
| 175_crdt_orset_rga | PASS | 60 | 4 | 1 | 3 |

"lost vs Q": a unit Q38U-T2 passed and Inkling failed (§5.1); "Q lost":
Q38U-T2's row is `server_error`. The failed units with no empty turn:
`122_gemm_blocked_d` (the `dlsym` stop), `122_gemm_blocked_e`,
`163_bigint_divmod_c`, `176_intermittent_500s_a`, `196_polyphase_resampler`.
Two capped turns had some answer text (one each in
`158_karatsuba_bytes_c` and `168_linearizability_kv_c`).

**How much it cost is not measured.** Every affected failure is also a
unit that ran to its turn limit on a hard task family, so the empty turns
are a symptom of long struggles as much as a cause. For scale, the same
count on Q38U-T2 (llama.cpp, whose thinking may run to the whole cap,
counted over the board's rows): 305 empty capped turns in 41 units over
2,037 turns (15.0%), against Inkling's 203 over 1,935 (10.5%). An empty
turn costs a turn of the limit and up to 8,192 tokens of time. The
measurement is a re-run after the re-pin past #588 (Strata report §15.8
item 2).

### 6.2 Lenient tool-call parsing

Basalt turns a call the model writes as XML in its answer text
(`<invoke name=…>`) into a real tool call, and emits a call whose JSON is
whole when the cap cuts its end marker (`serve/models/inkling2.py`
`finish`/`_whole`, 240-250; Strata report §15.6 item 4). A stricter
parser, as on llama.cpp, would return neither as a call. In this row:

- **88 turns ended at the cap (`length`) and still carried one tool call**
  (74 `bash`, 14 `write_file`; most in `192_pike_regex` 20,
  `158_karatsuba_bytes_c` 19 and `163_bigint_divmod_d` 12). Such a call
  came out of a reply the cap cut: either its end marker was missing (the
  lenient `finish` path) or the model kept writing after a closed call.
  Q38U-T2 has 5 such turns.
- **The XML path cannot be counted from the transcripts**: the server
  strips the XML and returns an ordinary `tool_calls` entry, and the
  transcript does not record which parser produced it. No assistant text
  in the 71 transcripts contains `<invoke`, `<tool_call` or
  `<function_calls>`, so no XML call leaked through as text either.
- **Effect on the comparison:** these paths can only add tool calls that a
  stricter server would have dropped, so they can only help Inkling's row
  against Q38U-T2's. **Corrected in §12.3:** a call cut inside its
  arguments is also returned, closed into valid JSON, and the runner runs
  it (truncated heredocs, truncated files), so this path also hurts. They are what a Basalt user gets, so the row keeps
  them, named here.

### 6.3 The server log was not kept

`inkling-v5.sh stop` is `docker rm -f basalt-serve`, and the rig's Docker
logs to `json-file`, so the log went with the container at 01:29:33 PST.
It would have given the wrap count, the "no answer" lines (the #588
count's cross-check) and the decode rate per reply. The ablation queue
logs each arm's "no answer" count before its stop, but not the log itself
(TODO).

### 6.4 Basalt is not a default yet

The review's four items stand (Strata report §15.8): the DNS channel
(fixed in the ablations' serve script, to be proven on a live start), the
#588 re-pin, the Python side pinned, and an adapter in
`scripts/engines/`. Plus the server log kept per run.

## 7. The default decision

**Qwen3.8 27B Uncensored MTP with `qwen38-best` stays the RTX 5090's
default.** The rule set before the run: Qwen3.8 stays unless Inkling's
scored row beats Q38U-T2.

1. **The scored row does not beat it.** 49 scored passes against 52; on
   the 65 units both measured, 46 against 52 (1 gained, 7 lost, p 0.07).
   The SCORE lead, 76.5 against 75.6, is all breadth (SOLVE level at
   72.3), is smaller than one unit's swing, and comes partly from
   Q38U-T2's lost and excluded units. One seed each.
2. **The engine is not ready to ship.** Basalt v0.5.7 still has #588 in the
   pin, and §15.8 lists three more items before a default.
3. **What Inkling does win** is time: the arm in a third of the time, the
   units it solves in seconds and in fewer turns and tokens. That is worth
   keeping measured (the ablations), not a reason to switch.

Revisit when `I_T2_s1` gives the noise margin and `I_T2_stock` the edit's
effect, and again after a re-pin past #588. *(Revisited with both, §10.8:
the default stays.)*

**The board and the website.** The board ranks by SCORE, so INK-T2 is #4
and Q38U-T2 #5; that is the board's rule and stays. The website shows each
rig's top-ranked board row as its best model (website #43, Max
2026-11-24), never a hand-picked name, so after the sync Inkling is the
RTX 5090's best model there, with Qwen3.8 still the shipped default and the
paired reading beside it. Recorded in
[DECISIONS-2026-11-24.md](DECISIONS-2026-11-24.md).

## 8. The three ablations: how they were queued

Queued since 2026-11-23 21:46 PST (`~/openbeast-runs/ink-ablations-queue.sh`;
DECISIONS-2026-11-23 §12); the queue started at 2026-11-24 01:56 PST,
when the Maya benchmark released the GPU, with `I_T2_s1` (its serve passed
the check with the DNS fix on; the in-container DNS probe fails, as
intended). Each one stream, Basalt alias `inkling-2-mini`:

1. **`I_T2_s1`**: I_T2 + `--seed 1`, the noise margin (Basalt honours a
   request seed; I_T2 itself was a clock-seeded draw).
2. **`I_T2_stock`**: I_T2 on Basalt's stock pack, the control for the
   derisking edit.
3. **`I_UP8k`**: DeepSeek's UP_8k setup at one stream (harness10).

Each gets the same integrity scan, `capped_turns.py`, and the `dlsym`
replay for a stopped Rust GEMM. Their serves run with `--dns 127.0.0.1`.
**Results: §10** (I_T2_s1 and I_T2_stock done; I_UP8k running).

## 9. Caveats

- **Single seed**, one run; Basalt samples with a clock seed when none is
  sent, so I_T2 is one draw. *(Now two draws of the Derisked setup, §10.2;
  the stock pack is still one draw.)*
- **A model-plus-engine comparison.** Against Q38U-T2 the weights, the
  model, the engine, the thinking room per turn (6,144 against 8,192) and
  the tool-call parser all differ at once (§5.1, §6).
- **Tokens compare roughly**: two tokenizers, two engines' usage reports.
- **The Derisked pack is a third party's behavioural edit** of the base
  model (a refusal-direction edit; its README); it changes refusals by
  design and measurably moves the distribution (KLD 0.0263). Its effect on
  coding is what `I_T2_stock` measures.
- **Harness host calibrated by inference** (§2.2).
- **The empty-turn and lenient-call counts are transcript counts**, without
  the server log's cross-check (§6.3).

## 10. The ablations: seed noise and the stock pack (2026-11-24)

Two of the three queued arms finished (§8): `I_T2_s1` (2026-11-24 09:56:55
to 14:07:28 UTC, 4 h 11 min) and `I_T2_stock` (14:07:39 to 18:25:17 UTC,
4 h 18 min), back to back from `~/openbeast-runs/ink-ablations-queue.sh`.
Both are I_T2 exactly: the harness9 tree `f38a29c7` (a worktree of
`~/ob-ink2`, clean), the same arm switches and `--jobs 1 --slots 1`, the
same eval-host container image, Basalt `400b411c` with the same serve flags
(`--parallel 1 --max-context 262144 --kv fp8`, answer room auto). One thing
changes in each:

| Arm | Board row | What differs from I_T2 | Passed | SCORE | SOLVE | LANG |
|---|---|---|---:|---:|---:|---:|
| I_T2 | INK-T2 | — (clock seed) | 49/71 | 76.5 | 72.3 | 89.1 |
| I_T2_s1 | INK-T2-S1 | `--seed 1` on every request | 51/71 | 78.3 | 72.3 | 96.4 |
| I_T2_stock | INK-T2-STOCK | Basalt's stock pack (`c2afac3a`, sha256 `28ec7072…`), clock seed | 53/71 | 78.2 | 75.4 | 86.7 |
| Q_T2 (context) | Q38U-T2 | Qwen3.8 on llama.cpp | 52/71 | 75.6 | 72.3 | 85.6 |

Per language (Py, Go, C, C++, Rust, Zig, Sh): I_T2_s1 23/32, 6/8, 6/8,
6/8, 5/8, 4/6, 1/1; I_T2_stock 24/32, 6/8, 7/8, 6/8, 5/8, 4/6, 1/1 (§4).
No unit lost in either, none excluded, 0 API errors, every row served as
`inkling-2-mini`.

### 10.1 The serves, checked

From the queue log (`~/openbeast-runs/ink-ablations-queue.out`):

- **Pack.** `docker inspect basalt-serve` read at each start and again at
  each arm's end: `/models-derisked/Inkling-2-Mini-Derisked-NVFP4-MX8.basalt`
  for I_T2_s1 (container `131da797…`), `/models/Inkling-2-Mini-NVFP4-MX8.basalt`
  for I_T2_stock (`54e3597f…`); the alias is the same, so this is the
  check that the right pack served. Both files are read-only and were
  re-hashed on 2026-11-24 after the arms: `d2b01bcc…a427` and
  `28ec7072…1b99`, the registry's sums (`weights.json`).
- **DNS.** Both serves ran with `--dns 127.0.0.1`, and the in-container
  probe failed to resolve `example.com` and `huggingface.co`, as intended:
  the fix of the Strata review's DNS gap (§15.4 there), now proven on three
  live starts (I_UP8k's too).
- **Seed.** Before I_T2_s1 the queue sent the same prompt twice under
  seed 1 at temperature 0.6 (identical text) and twice unseeded
  (different): Basalt honours a request seed, so I_T2_s1 is a fixed draw
  and I_T2 (unseeded) a clock-seeded one. Every I_T2_s1 row records
  `agent_seed 1`.

### 10.2 Seed noise: I_T2 against I_T2_s1

`evals/ablation.py --force` (the slugs differ only by the arm suffix),
over all 71 units (no pair dropped):

| Contrast | n | I_T2 | I_T2_s1 | gained | lost | net | p |
|---|---:|---:|---:|---:|---:|---:|---:|
| all units | 71 | 49 | 51 | 2 | 0 | +2 | 0.50 |
| Python | 32 | 23 | 23 | 0 | 0 | 0 | 1.0 |
| C | 8 | 5 | 6 | 1 | 0 | +1 | 1.0 |
| Zig | 6 | 3 | 4 | 1 | 0 | +1 | 1.0 |
| Go, C++, Rust, Shell | 25 | 18 | 18 | 0 | 0 | 0 | 1.0 |

Gained under seed 1: `122_gemm_blocked_e` (Zig, Performance & HW Opt) and
`158_karatsuba_bytes_c` (C, Pure & Abstract Math). **Churn: 2 of 71
units (2.8%).** For comparison, DeepSeek's seed pair on the Spark pair
(DS-T against DS-Ts1, harness9, `--jobs 4`): 61 and 61, 1 gained and 1
lost, also 2 of 71. Two models, two engines, the same measured churn; both
far under the 15% "untreated churn" `ablation.py` assumes for its minimum
detectable effect (MDE 9.5 at n 71; at a 2.8% churn it would be 6.5).
One replicate pair per model: this is a floor, not a distribution.

**SCORE is noisier than the pass count.** Both seed pairs kept SOLVE
exactly (Inkling 72.3 and 72.3; DeepSeek 84.6 and 84.6) and moved LANG:
Inkling 89.1 to 96.4 (SCORE 76.5 to 78.3, +1.8), DeepSeek 100.0 to 88.9
(SCORE 88.5 to 85.7, −2.8). LANG counts, per multi-language base, the
languages solved, so one flipped port can move a base by a large step.
Read SCORE gaps under about 3 points between single-seed rows as unresolved:
that covers INK-T2 against Q38U-T2 (0.9), INK-T2-STOCK against INK-T2
(1.7) and INK-T2-STOCK against Q38U-T2 (2.6).

**On the board.** INK-T2-S1 scores above INK-T2 (78.3 against 76.5).
Ranking the better of two draws of one setup would give Inkling a
best-of-two its single-seed neighbours do not get, so the board now marks
a seed repeat with `replicate_of` and never ranks it (`evals/v5_board.py`;
DS-Ts1 is marked too, which changes nothing there: DS-T2 is DeepSeek's
row). INK-T2 stays the Derisked pack's ranked row; INK-T2-S1 is listed with
every run (DECISIONS-2026-11-24 §7).

Cost on the 49 units both seeds passed: fewer completion tokens under seed
1 on 26, more on 23 (median −89, p 0.78); iterations fewer on 15, more on
22 (p 0.32). No difference.

### 10.3 Stock against Derisked

The pair differs only in the weights: Basalt's own pack of Thinking
Machines' release (no behavioural edit; KLD 0.0128, top-1 94.93% against
UD-Q8_K_XL on our wikitext run) against Blackfrost's refusal-direction edit
converted with the same tool (Basalt's `convert.py`: ours at the engine pin,
the publisher's at a commit the pack does not record; KLD 0.0263, top-1
92.93%; Strata report §11.2). Paired, all 71 units, two views, since the Derisked side
has two draws:

| Pair | n | Derisked | stock | gained | lost | net | p | MDE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| I_T2 → I_T2_stock | 71 | 49 | 53 | 5 | 1 | +4 | 0.22 | 9.5 |
| I_T2_s1 → I_T2_stock | 71 | 51 | 53 | 3 | 1 | +2 | 0.63 | 9.5 |

Which units, by category and language:

| Unit | Category | Language | vs I_T2 | vs I_T2_s1 |
|---|---|---|---|---|
| `163_bigint_divmod_a` | Algorithms & DS | Python | gained | gained |
| `163_bigint_divmod_c` | Algorithms & DS | C | gained | gained |
| `196_polyphase_resampler` | Signal Processing & DSP | Python | gained | gained |
| `122_gemm_blocked_e` | Performance & HW Opt | Zig | gained | — (seed 1 passed it too) |
| `158_karatsuba_bytes_c` | Pure & Abstract Math | C | gained | — (seed 1 passed it too) |
| `178_codebase_qa` | SWE / DevOps | Python | lost | lost |

**Reading.** The stock pack is ahead in both views and the sign agrees,
but neither is significant and both are under the MDE even at the
measured 2.8% churn (6.5). Two of I_T2's five gains are units seed 1
alone also gained, so they are noise, not the edit; the three gained in
both views are the candidates for an edit effect: two ports of Knuth long
division (where the Derisked arms went 0/6 and 0/6, the stock arm 2/6) and
the polyphase resampler. Each of the three had empty capped turns in at
least one Derisked arm (`163_a` 17 and 10, `163_c` 19 under seed 1,
`196` 2 under seed 1) and none in the stock arm. The one loss
(`178_codebase_qa`) ran to its turn limit without writing its answer file.

**The empty capped turns** (Basalt #588; §6.1), per arm, from the
transcripts and, for the two ablations, confirmed by the server log
(§10.6):

| | I_T2 | I_T2_s1 | I_T2_stock |
|---|---:|---:|---:|
| Turns | 1,935 | 1,911 | 2,032 |
| Ended at the cap | 293 | 240 | 190 |
| **Empty at the cap** | **203** (10.5%) | **167** (8.7%) | **105** (5.2%) |
| Units with an empty turn | 21 | 17 | 12 |
| In failed units | 199, in 17 of 22 fails | 144, in 15 of 20 | 102, in 10 of 18 |
| At the cap with a tool call | 88 | 73 | 83 |

Per unit (P pass, F fail; the number is that unit's empty capped turns,
blank for none), every unit with one in any arm:

| Unit | I_T2 | I_T2_s1 | I_T2_stock |
|---|---:|---:|---:|
| 122_gemm_blocked_e | F | P | P 1 |
| 143_uov | P 1 | P | P |
| 158_karatsuba_bytes_b | P 1 | P | P |
| 158_karatsuba_bytes_c | F 12 | P | P |
| 163_bigint_divmod_a | F 17 | F 10 | P |
| 163_bigint_divmod_b | F 4 | F | F |
| 163_bigint_divmod_c | F | F 19 | P |
| 163_bigint_divmod_d | F 15 | F 38 | F 2 |
| 163_bigint_divmod_e | F 2 | F 2 | F |
| 163_bigint_divmod_f | F 24 | F | F 7 |
| 167_q15_biquad_f | P 1 | P | P |
| 168_linearizability_kv_a | F 21 | F 11 | F 9 |
| 168_linearizability_kv_b | F 1 | F 11 | F |
| 168_linearizability_kv_c | F 35 | F 12 | F 19 |
| 168_linearizability_kv_d | F 17 | F 1 | F 25 |
| 168_linearizability_kv_e | F 14 | F 14 | F 3 |
| 168_linearizability_kv_f | F 2 | F | F |
| 173_taskgroup_cancel | F 4 | F 1 | F |
| 175_crdt_orset_rga | P 1 | P 1 | P |
| 183_heston_little_trap | F 2 | F 3 | F 15 |
| 188_gmm_em | P | P 22 | P 2 |
| 190_autograd_mlp | F 2 | F 1 | F 2 |
| 192_pike_regex | F 24 | F 13 | F 3 |
| 195_riscv_insn_budget | F 3 | F 6 | F 17 |
| 196_polyphase_resampler | F | F 2 | P |
| **all units** | **203** in 21 units | **167** in 17 units | **105** in 12 units |

The stock pack ran out of room with no answer about half as often as the
Derisked pack (105 against 203 and 167), in fewer units. Two readings fit:
the edit makes the model think in longer loops (its distribution moved
twice as far), or the difference is which units each draw struggled on.
The counts fall on the same hard families in all three arms; the stock
arm's total is lower, but not on every unit (it is the highest of the three
on `168_linearizability_kv_d`, `183_heston_little_trap` and
`195_riscv_insn_budget`). Not separable without a seed repeat of the stock
arm. Either way it is the
same Basalt bug (#588), so the improved arm's #632 patch tests it directly.

**Cost.** On the 48 units both passed, the stock pack used more
completion tokens on 30 and fewer on 18 than I_T2 (median +1,596, p 0.11)
and more iterations on 28, fewer on 13 (median +1, p 0.028): where both
solve, the Derisked pack solves in slightly fewer turns. The stock arm's
median unit was 122 s (44 s for its passes) against 47 s for I_T2.

### 10.4 Stock against Q38U-T2, paired

As §5.1, Q_T2's two files merged as the board merges them; 6 pairs dropped
(Q38U-T2's 3 lost units and 3 integrity exclusions):

| Contrast | n | Q38U-T2 | INK-T2-STOCK | gained | lost | net | p | MDE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all units | 65 | 52 | 50 | 2 | 4 | −2 | 0.69 | 10 |
| Python | 28 | 23 | 21 | 0 | 2 | −2 | 0.50 | 8 |
| Go | 8 | 7 | 6 | 0 | 1 | −1 | 1.0 | 5.5 |
| C | 8 | 6 | 7 | 1 | 0 | +1 | 1.0 | 5.5 |
| C++ | 8 | 6 | 6 | 0 | 0 | 0 | 1.0 | 5.5 |
| Rust | 7 | 6 | 5 | 0 | 1 | −1 | 1.0 | 5.5 |
| Zig | 5 | 3 | 4 | 1 | 0 | +1 | 1.0 | — |
| compiled | 36 | 28 | 28 | 2 | 2 | 0 | 1.0 | 9 |

- Gained: `122_gemm_blocked_b` (C), `122_gemm_blocked_e` (Zig), both
  Performance & HW Opt.
- Lost: `163_bigint_divmod_b` (Go) and `_e` (Rust), Algorithms & DS;
  `176_intermittent_500s_a` and `178_codebase_qa` (Python), SWE / DevOps.
- **Board view** (`v5_report.py`, Q38U-T2's excluded passes as fails, 68
  units): net 0, 4 each way (§4). Under seed 1 the Derisked pack against
  Q38U-T2 was −4 over the 65 (2 gained, 6 lost, p 0.29); I_T2 was −6.
- Cost on the 48 units both passed: Inkling stock used fewer completion
  tokens on 33, more on 15 (median −1,713, p 0.013); turns level (p 0.35).

**Does Inkling now reach parity? Yes, and no more.** On the units both
measured cleanly the stock pack is 2 behind (p 0.69), on the board's view
level, on scored passes 1 ahead (53 against 52, a lead made of units
Q38U-T2 never measured: its 3 lost and 3 excluded units, of which the
stock arm passed 3). SCORE 78.2 against 75.6 is 2.6 points, inside the
seed noise of §10.2. The Derisked pack's gap closed from −6 (I_T2) through
−4 (seed 1) to −2 (stock): all three inside noise, all three behind on the
clean pairs.

### 10.5 Integrity and the dlsym stop

Sealed notes: `evals/audit/reviews/ink-t2-s1-stock-integrity-2026-11-24.md`.

- **I_T2_s1:** 71 transcripts, 71 units, 0 contaminated. Attempts only:
  five `web_search` calls and one `fetch` in four units (all fails), each
  refused by the eval guard. **I_T2_stock:** 71 units, 0 contaminated
  (the scan also saw I_T2_s1's last ten transcripts at the window's start
  and marked them unscored); five `web_search` calls in five units (one a
  pass, which went on without it), each refused.
- **The Linux shell gap**, checked by hand over all 1,713 and 1,872 tool
  calls (non-loopback URLs; curl, wget, nc, socat, pip, git, cargo and go
  fetches, Python sockets and URL readers, `/dev/tcp`): 0 real hits (one
  match was a Rust variable named `nc` inside a heredoc).
- **`122_gemm_blocked_d` (Rust)** stopped at the `dlsym` binary check in
  both arms. Each final `gemm.rs` was rebuilt from its transcript (one
  `write_file`, then Python edit snippets run from bash, re-run exactly,
  every printed output matching), and replayed with PR #303's script in a
  new directory on the rig (`~/ob-cal/.claude/gemm-replay-ink-ab/`):
  unpatched validator FAIL `imports dlsym` (the recorded line), proposed
  rule **PASS, 6 hidden cases, 3 of 3 runs, in both**. `validator_notes`
  entries for INK-T2-S1 and INK-T2-STOCK; not adjusted (52/71 and 54/71
  under the proposed rule).

### 10.6 Basalt live speed and draft acceptance (the saved server logs)

This time the server log was kept: `basalt-logsaver.sh` followed each
`basalt-serve` container with `docker logs -f --timestamps` from its
start (`~/openbeast-runs/basalt-server-logs/basalt-20261124-022403-131da79751c8.log`
for I_T2_s1, `…-060744-54e3597ff960.log` for I_T2_stock). Basalt writes
one line per request (`s1 done pp=… tg=N R t/s acc=A% dl=D … end=…`) and
a warning per reply that hit the cap still thinking. A new
`evals/audit/basalt_log.py` reads them over each arm's window (START and
END of `campaign.log`; the serve's probe requests before START are left
out).

**Cross-check first.** Over the window the log has exactly the
transcripts' turns (1,911 and 2,032 requests), their cap endings (240 and
190 `end=length`), their empty capped turns (167 and 105 "no answer"
warnings, also the counts the queue grepped before each stop), and the
runner's token sums to the token (completion 4,959,068 and 4,900,235;
prompt 64,747,685 and 64,862,866). So `capped_turns.py`'s transcript count
is confirmed, and I_T2's 203 (no log) stands on the same method.

| | I_T2_s1 (Derisked) | I_T2_stock |
|---|---:|---:|
| Requests | 1,911 | 2,032 |
| Generated tokens | 4,959,068 | 4,900,235 |
| Decode time (Σ tokens / rate) | 10,243 s (2.85 h) | 11,036 s (3.07 h) |
| Prefill time (Σ) | 281 s | 304 s |
| **Decode, all replies** (Σ tokens / Σ time) | **484 tok/s** | **444 tok/s** |
| Decode per reply ≥ 256 tokens: median (p10, p90; n) | 505 (393, 636; 1,304) | 467 (363, 599; 1,337) |
| Decode per reply at the 8,192 cap: median (p10, p90; n) | 554 (454, 615; 240) | 512 (413, 598; 190) |
| **Drafts accepted**, token-weighted mean | **90.2%** | **88.7%** |
| Drafts accepted per reply: median (p10, p90) | 90% (75, 99) | 89% (74, 98) |
| Tokens per verify window, token-weighted (median) | 5.08 (4.7) | 4.64 (4.4) |
| Replies with no draft (very short) | 33 | 59 |
| Prompt tokens served from cache | 96.6% | 96.5% |
| Prefill on ≥ 1,000 uncached tokens: median (p10, p90; n) | 12,052 tok/s (7,630, 16,785; 551) | 12,410 (8,230, 16,384; 517) |

These are the server's own per-request rates (decode only; prefill timed
separately), from a single stream on one RTX 5090. Decode was about 70% of
each arm's wall time. The end-to-end rate on replies of exactly 8,192
tokens, from the transcripts (prefill and request overhead included, as
§3.3 measured I_T2's 520): I_T2_s1 median 542 (p10 467, p90 600), I_T2_stock
510 (434, 594). For scale, the short-prompt engine bench of 2026-11-23:
403 / 356 tok/s code / prose (thinking off), and the session notes' live
read during I_T2: about 530 to 680 tok/s per reply.

**Reading.** The DSpark drafter accepts about nine in ten drafted tokens
on agent work, emitting about five tokens per verify step; that is where
Basalt's single-stream speed comes from. The Derisked arm decoded about 9%
faster than the stock arm with a slightly higher acceptance; the two arms
generated different replies, so this is not a controlled speed comparison
of the packs; the engine bench of 2026-11-23 also had the Derisked pack
faster, by 7% (code) and 16% (prose), thinking off, 403 / 356 against
376 / 307 tok/s (Strata report §11.2). The drafter is the checkpoint's own
in both packs.

### 10.7 What the two arms say together

1. **Noise floor (I_T2 vs I_T2_s1):** 2 of 71 units flip between seeds,
   as on DeepSeek; SCORE moves up to about 3 points on LANG alone.
2. **The derisking edit (stock vs Derisked):** +4 and +2 for the stock
   pack, 3 units consistent across both views, and about half the empty
   capped turns. Not significant; the direction favours the stock pack,
   with no sign that the edit helps coding.
3. **Parity with the default (stock vs Q38U-T2):** −2 on clean pairs, 0 on
   the board's view, +1 on scored passes, +2.6 SCORE. Level within noise.
4. **The board:** INK-T2-STOCK (78.2) is #4, above INK-T2 (76.5, #5) and
   Q38U-T2 (75.6, #6); INK-T2-S1 is listed, not ranked. INK-T2-STOCK is
   now the RTX 5090's top board row.

### 10.8 The default decision, revisited

> **Superseded 2026-11-24 (Max):** the stock Inkling 2 Mini on Basalt is the
> RTX 5090 default from that date, with Qwen3.8 as the fallback
> ([DECISIONS-2026-11-24.md §10](DECISIONS-2026-11-24.md#10-stock-inkling-2-mini-on-basalt-is-the-rtx-5090-default),
> the adapter `scripts/engines/basalt.sh`). The reasoning below is kept as
> written; it is the case against that decision records.

**Qwen3.8 27B Uncensored MTP with `qwen38-best` stays the RTX 5090
default** (DECISIONS-2026-11-24 §6):

1. **Parity is not a reason to switch.** By the letter of §7's rule the
   stock row edges Q38U-T2 (53 against 52 scored passes); the edge comes
   from units Q38U-T2 lost to infrastructure or had excluded, and on the
   65 units both measured cleanly Qwen3.8 is 2 ahead. Every gap here is
   inside the measured seed noise, and the stock arm has one seed.
2. **The engine still carries #588.** 105 empty capped turns in the stock
   arm is the same bug, smaller. Basalt PR #632 would open an answer block
   instead of leaving the model to; a local patch of it is being designed.
   The next arm is **stock + #632** (I_T2's switches, the stock pack), and
   ideally its seed repeat.
3. **Basalt's review items** (Strata report §15.8): the DNS fix is now
   proven on three live starts (§10.1) and the server log is kept by the
   queue's saver; still open: the #588 re-pin (or the patch), the Python
   side pinned, an adapter in `scripts/engines/`.

**Switch when** the improved arm's scored row beats Q38U-T2's and the
paired net over clean units is positive, confirmed by a seed repeat that
does not reverse it, and the §15.8 items are closed. The pack for that
arm is the stock one: the uncensored-preferred policy is a tie-breaker
between equals, and here the measured direction (passes, empty turns,
divergence) favours the stock pack; Derisked stays measured, not dropped.

### 10.9 I_UP8k

`I_UP8k` (DeepSeek's UP_8k setup at one stream, harness10 tree `d41c2c47`,
era `9bcd5aa6`, the Derisked pack) ran 2026-11-24 18:25:29 to 22:28:05 UTC
and passed **49/71**, the same count as `I_T2`. Read for the improved
arm's design in §12.6 (its forced-act turn made a call in 18 of 68 turns
on Inkling). Still to do for its row: the sealed integrity review (the
scan flags one transcript, §12.6), `basalt_log.py` on
`basalt-20261124-102527-c9c29fce36b3.log`, the `dlsym` replay, the board.

## 11. Sources

- Result file: [`evals/boards/v5/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-20261124-045843.json`](../../evals/boards/v5/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-20261124-045843.json) (board row `INK-T2`).
- Weights: [`evals/boards/v5/weights.json`](../../evals/boards/v5/weights.json), `inkling2-mini-blackfrost-derisked-basalt-nvfp4-mx8`.
- Validator note: [`evals/boards/v5/exclusions.json`](../../evals/boards/v5/exclusions.json), run `INK-T2`.
- Integrity review (sealed): `evals/audit/reviews/ink-t2-integrity-2026-11-24.md`.
- The counter: [`evals/audit/capped_turns.py`](../../evals/audit/capped_turns.py).
- On the rig: the transcripts `~/ob-ink/.claude/worktrees/h9/agents/logs/`
  (71 files, `agent-20261124-045843-*` to `agent-20261124-092926-*`); the
  campaign directory
  `~/ob-ink/evals/results/campaigns/v5-thinking-first-inkling-2-mini-derisked-basalt-nvfp4-mx8/`
  (`campaign.log`, `arm-I_T2.log`, `state.json`); the run logs
  `~/openbeast-runs/beast-inkling-I_T2-20261123-2058.out`,
  `~/openbeast-runs/ink-I_T2-run.out` (launch, exit, restore OK) and
  `~/openbeast-runs/ink-I_T2-switch-20261123-2057.log` (stop, serve start,
  pack hash); the pack and its sum
  `~/openbeast-runs/inkling-perf-2026-11-23/weights/basalt-blackfrost-derisked/`;
  the `dlsym` replay `~/ob-cal/.claude/gemm-replay-ink/`.
- Q38U-T2: [V5_QWEN38_RTX5090-2026-11-22.md](V5_QWEN38_RTX5090-2026-11-22.md) §6.
- **The ablations (§10):** result files
  [`…-i-t2-s1-20261124-095656.json`](../../evals/boards/v5/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-s1-20261124-095656.json)
  (row `INK-T2-S1`) and
  [`…-i-t2-stock-20261124-140740.json`](../../evals/boards/v5/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t2-stock-20261124-140740.json)
  (row `INK-T2-STOCK`), verbatim (sha256 `c30d78a3…` and `0e018365…`, as
  on the rig); weights `inkling2-mini-basalt-nvfp4-mx8-stock`; the sealed
  review `evals/audit/reviews/ink-t2-s1-stock-integrity-2026-11-24.md`; the
  log reader [`evals/audit/basalt_log.py`](../../evals/audit/basalt_log.py).
  On the rig: `~/ob-ink2/.claude/worktrees/h9/agents/logs/` (142
  transcripts, `agent-20261124-095657-*` to `agent-20261124-182329-*`), the
  campaign directories
  `~/ob-ink2/evals/results/campaigns/v5-thinking-first-inkling-2-mini-derisked-basalt-nvfp4-mx8/`
  (`arm-I_T2_s1.log`; shared with the running I_UP8k) and
  `…/v5-thinking-first-inkling-2-mini-basalt-nvfp4-mx8-stock/`, the queue
  and its log `~/openbeast-runs/ink-ablations-queue.{sh,out}`, the server
  logs `~/openbeast-runs/basalt-server-logs/`, the stock pack and its
  download log `~/openbeast-runs/inkling-perf-2026-11-23/{weights/basalt-Inkling-2-Mini-c2afac3a,logs/dl-all.log}`,
  the `dlsym` replay `~/ob-cal/.claude/gemm-replay-ink-ab/`.

## 12. The improved arm I_T3 (2026-11-24)

> **Added 2026-11-24 (evening).** Max: "if Inkling 2 Mini is truly this
> performant and fast, we can look at all our ablations and create an
> improved test run which will hopefully bring completion parity or above
> to Qwen 3.8". This section reads all four Inkling arms (`I_T2`,
> `I_T2_s1`, `I_T2_stock`, `I_UP8k`) for what failed and why, and designs
> arm `I_T3` on the stock pack (§10, DECISIONS-2026-11-24 §8). Decision
> records: [DECISIONS-2026-11-24.md](DECISIONS-2026-11-24.md) §11 (the #588
> patch) and §12 (`I_T3`). §10 has the seed-noise and stock readings; this
> section adds the per-unit failure mechanisms and `I_UP8k`.

### 12.1 Sources and how to regenerate

- Result files (rig): `I_T2` as in §11;
  `~/ob-ink2/.claude/worktrees/h9/evals/results/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-s1-20261124-095656.json`
  (51/71); `…/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t2-stock-20261124-140740.json`
  (53/71); `~/ob-ink2/evals/results/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-up8k-20261124-182529.json`
  (§12.6). Transcripts: `~/ob-ink2/.claude/worktrees/h9/agents/logs/`
  (`I_T2_s1` 09:56:56-14:07:28Z, `I_T2_stock` 14:07:40-18:25:17Z),
  `~/ob-ink2/agents/logs/` (`I_UP8k`).
- Server logs, kept this time (`~/openbeast-runs/basalt-logsaver.sh`, one
  file per container): `~/openbeast-runs/basalt-server-logs/basalt-20261124-022403-131da79751c8.log`
  (`I_T2_s1`), `…-060744-54e3597ff960.log` (`I_T2_stock`),
  `…-102527-c9c29fce36b3.log` (`I_UP8k`).
- Per arm, as for `I_T2` (§3.4): `integrity_scan.py --tz UTC --logs-dir
  <copy> --until <arm>=<END from the campaign log> --json scan.json
  "<arm>=<results>@<git archive f38a29c7>"`, then `capped_turns.py
  scan.json`. Scans: `I_T2_s1` and `I_T2_stock` 71 transcripts, 71 units,
  **0 contaminated**; refused `web_search` / `fetch` attempts only (Knuth's
  Algorithm D in `163_*`, Heston in `183`, CPython `re` in `192`), no
  shell route off the host found by the scanner.
- Paired tests: `evals/ablation.py --force A B` (per language) and its
  `mcnemar_p` / `mde` over all units; the Q38U-T2 pair uses the merged
  Q_T2 file of §1's recipe and drops the same 6 units as §5.
- The per-turn readings below (wall fraction, turn limit, cut calls,
  truncated heredocs, the server-log alignment) were made with short
  scripts over those copies: wall = the unit's `max_wall_s` × 2 (harness9
  tasks); turn limit = `iterations >= max_iter`; a cut call = a
  `tool_call` after a `response_model` with `finish_reason` `length`; a
  truncated heredoc = a bash result containing `delimited by
  end-of-file`; the alignment matches each transcript `response_model`
  to the server's `s1 done` line at or just before it (UTC, all 1,911
  of `I_T2_s1` matched).

### 12.2 The rows

| Arm | Pack | Passed | Empty capped (#588) | Cut calls | Truncated heredocs | Ended at the turn limit (passed) | Wall hits | Σ unit time |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| I_T2 | Derisked | 49/71 | 203 in 21 units | 88 | 51 | 21 (3) | 0 | 4.47 h |
| I_T2_s1 (`--seed 1`) | Derisked | 51/71 | 167 in 17 units | 73 | 56 | 21 (3) | 0 | 4.13 h |
| I_T2_stock | stock | **53/71** | 105 in 12 units | 83 | 82 | 21 (4) | 0 | 4.26 h |
| Q38U-T2 (for scale) | | 52/71 scored (54 raw) | 305 in 41 units | 5 | not counted | 14 (5) | 9 | 10.84 h |

Server logs (the `I_T2` log was lost, §6.3): `I_T2_s1` 1,883 replies, 463
budget wraps (258 then `stop`, 205 `length`), **167 "no answer"**, equal
to the transcripts' empty capped turns; decode median 499 tok/s (p10
387, p90 615) on replies of 1,000+ tokens, draft acceptance median 91%.
`I_T2_stock` 1,974 replies, 415 wraps, **105 "no answer"**, decode median
460 tok/s, acceptance 89%.

### 12.3 Failure taxonomy, per unit

Every unit that failed in at least one Inkling arm. Cells: verdict class
from the validator line · turns used / turn limit · share of the unit's
wall used · empty capped turns / cut calls. Classes: `cpu-budget` (the
validator's CPU budget), `wrong-answer`, `crash/exception`, `build-fail`,
`missing/incomplete` (missing file or function), `dlsym-stop` (§3.6).
"Levers": which change of §12.5 bears on the unit, from these numbers.

| Unit | Q38U-T2 | I_T2 | I_T2_s1 | I_T2_stock | Levers |
|---|---|---|---|---|---|
| 122_gemm_blocked_d | fail (excl.) | dlsym-stop · 9/60 · 2% · 0/0 | dlsym-stop · 12/60 · 2% · 0/0 | dlsym-stop · 24/60 · 4% · 0/0 | none (validator, next era) |
| 122_gemm_blocked_e | fail | crash/exception · 7/60 · 2% · 0/0 | PASS · 60/60 | PASS · 53/60 | turns |
| 158_karatsuba_bytes_c | PASS | wrong-answer · 60/60 · 23% · 12/19 | PASS · 27/60 | PASS · 21/60 | turns, cap, #588 |
| 163_bigint_divmod_a | PASS | cpu-budget · 58/60 · 44% · 17/0 | wrong-answer · 60/60 · 22% · 10/0 | PASS · 60/60 | turns, #588, design |
| 163_bigint_divmod_b | PASS | wrong-answer · 60/60 · 16% · 4/1 | cpu-budget · 60/60 · 17% · 0/1 | wrong-answer · 60/60 · 36% · 0/13 | turns, cap, design |
| 163_bigint_divmod_c | PASS | wrong-answer · 60/60 · 44% · 0/2 | cpu-budget · 60/60 · 31% · 19/2 | PASS · 57/60 | turns, #588, design |
| 163_bigint_divmod_d | fail | cpu-budget · 60/60 · 39% · 15/12 | missing/incomplete · 60/60 · 43% · 38/0 | wrong-answer · 60/60 · 24% · 2/4 | turns, cap, #588, design |
| 163_bigint_divmod_e | PASS | wrong-answer · 60/60 · 11% · 2/2 | wrong-answer · 60/60 · 24% · 2/3 | build-fail · 60/60 · 15% · 0/2 | turns, cap |
| 163_bigint_divmod_f | fail | missing/incomplete · 60/60 · 30% · 24/0 | build-fail · 60/60 · 22% · 0/8 | missing/incomplete · 60/60 · 25% · 7/6 | turns, cap, #588 |
| 168_linearizability_kv_a | fail | cpu-budget · 59/60 · 37% · 21/2 | cpu-budget · 60/60 · 49% · 11/2 | cpu-budget · 60/60 · 79% · 9/0 | turns, #588, design |
| 168_linearizability_kv_b | fail | cpu-budget · 60/60 · 25% · 1/5 | crash/exception · 60/60 · 51% · 11/4 | missing/incomplete · 60/60 · 15% · 0/0 | turns, cap, #588, design |
| 168_linearizability_kv_c | fail | cpu-budget · 60/60 · 65% · 35/2 | cpu-budget · 49/60 · 31% · 12/1 | cpu-budget · 60/60 · 51% · 19/1 | turns, #588, design |
| 168_linearizability_kv_d | fail | cpu-budget · 60/60 · 59% · 17/2 | cpu-budget · 60/60 · 28% · 1/1 | build-fail · 60/60 · 58% · 25/8 | turns, cap, #588, design |
| 168_linearizability_kv_e | fail | cpu-budget · 60/60 · 73% · 14/4 | build-fail · 60/60 · 58% · 14/5 | cpu-budget · 60/60 · 50% · 3/3 | turns, cap, #588, design |
| 168_linearizability_kv_f | fail (lost) | cpu-budget · 60/60 · 32% · 2/4 | crash/exception · 60/60 · 21% · 0/1 | build-fail · 60/60 · 21% · 0/2 | turns, cap, design |
| 173_taskgroup_cancel | fail | crash/exception · 60/60 · 28% · 4/0 | crash/exception · 60/60 · 19% · 1/0 | wrong-answer · 60/60 · 35% · 0/21 | turns, cap |
| 176_intermittent_500s_a | PASS | wrong-answer · 100/100 · 10% · 0/1 | wrong-answer · 100/100 · 8% · 0/0 | wrong-answer · 100/100 · 12% · 0/0 | turns |
| 178_codebase_qa | PASS | PASS · 60/60 | PASS · 60/60 | missing/incomplete · 60/60 · 15% · 0/0 | turns |
| 183_heston_little_trap | fail | crash/exception · 60/60 · 20% · 2/1 | crash/exception · 60/60 · 36% · 3/3 | crash/exception · 60/60 · 35% · 15/6 | turns, cap, #588 |
| 190_autograd_mlp | fail (lost) | wrong-answer · 60/60 · 16% · 2/5 | missing/incomplete · 60/60 · 27% · 1/2 | wrong-answer · 60/60 · 30% · 2/7 | turns, cap |
| 192_pike_regex | fail | missing/incomplete · 60/60 · 47% · 24/20 | missing/incomplete · 60/60 · 34% · 13/15 | missing/incomplete · 60/60 · 13% · 3/0 | turns, cap, #588 |
| 195_riscv_insn_budget | fail | crash/exception · 60/60 · 17% · 3/0 | wrong-answer · 60/60 · 32% · 6/6 | wrong-answer · 60/60 · 33% · 17/0 | turns, cap, #588 |
| 196_polyphase_resampler | PASS | wrong-answer · 60/60 · 15% · 0/0 | cpu-budget · 60/60 · 66% · 2/0 | PASS · 60/60 | turns, design |

Reading the table:

- **Every failure but one ran to the turn limit.** In all three arms the
  only failed unit that did not use (nearly) all its turns is
  `122_gemm_blocked_d`, the validator's `dlsym` stop (§3.6). 18 of
  `I_T2`'s 22 failures, 18 of `I_T2_s1`'s 20 and 17 of `I_T2_stock`'s 18
  ended exactly at the limit (the rest at 58-59 or after a crash in
  turn 7-12). They used a median 26% / 30% / 28% of their wall (max 73% /
  66% / 79%); median 8.7 / 8.9 / 8.2 s a turn. **No Inkling unit has hit
  its wall in 213 units.**
- **Passes land on the last turn.** 3 / 3 / 4 passes per arm ended at the
  turn limit (`167_q15_biquad_f` in all three, `175` and `178` in `I_T2`,
  `122_gemm_blocked_e` and `178` in `I_T2_s1`, `163_bigint_divmod_a`,
  `188_gmm_em` and `196` in `I_T2_stock`), and `178_codebase_qa`, a pass
  at the limit in both Derisked arms, failed at the limit on stock. The
  limit is where the coin lands.
- **Qwen3.8 spent its wall where it won.** On the six units Q38U-T2
  passes and every Derisked run failed (`163_a/b/c/e`, `176_a`, `196`),
  Q38U-T2 used 1,156-1,800 s (two at the wall); Inkling used 196-1,197 s
  of the same 1,800-3,000 s walls.
- **168 looks like a design failure, as on DeepSeek.** 14 of 18
  `168_linearizability_kv` attempts failed on the CPU budget or a crash,
  the other 4 on a build failure or a missing file. The CPU-budget timeout
  is the signature DeepSeek's 168 investigation traced to a global search
  where a per-key split is the tractable design (HARNESS_NOTES); Inkling's
  code was not read for this. Q38U-T2 passed none either.
  More turns or a bigger cap will not obviously fix it; the planning lines
  (UP) target it.
- **Cut calls are not harmless here.** Basalt returns a call that the cap
  cut, with its streamed arguments closed into valid JSON (`serve/models/inkling2.py`
  `finish`, 240-266; the non-streamed reply is collected from the same
  stream, `serve/api/openai.py` `openai_collect`; `service.py:1376` runs
  the parser with `stream_tools=True`). The runner executes it. So a long
  write cut by the cap runs as a truncated heredoc or writes a truncated
  file: 51 / 56 / 82 bash results say `here-document … delimited by
  end-of-file`, every one after a capped reply, and 82 of `I_T2`'s 88 cut
  calls had arguments over 4,000 characters. `192_pike_regex` in `I_T2`
  rewrote a ~630-line `rx.py` 15 times and every write was cut (the
  answer room after a wrap is 2,048 tokens); it ended "rx.py defines no
  search()". This corrects §6.2's "can only help Inkling's row": the
  lenient close helps when the cut is in the end marker and hurts when the
  cut is in the arguments.

### 12.4 Noise

`I_T2` → `I_T2_s1` (same pack, harness, serve; a seeded draw against a
clock-seeded one): **2 units flipped, both into `I_T2_s1`**
(`122_gemm_blocked_e`, `158_karatsuba_bytes_c`), 0 the other way: churn
about 3% (2/71), far below `ablation.py`'s default 15%. With that churn
its MDE over 71 units is **7** (`mde(71, 0.028)`); at the default churn,
10. An exact McNemar needs 6-0 for p < 0.05. So a single new row is
descriptive unless it gains about 7; parity with Q38U-T2 (+1 to +3 over
the Derisked rows) is inside the noise however it lands. The two
Derisked rows' 49 and 51 are one baseline band; the stock row's 53 is
`I_T3`'s base.

| Pair (all 71 units) | b (in) | c (out) | net | p |
|---|---:|---:|---:|---:|
| I_T2 → I_T2_s1 | 2 | 0 | +2 | 0.50 |
| I_T2 → I_T2_stock | 5 | 1 | +4 | 0.22 |
| I_T2_s1 → I_T2_stock | 3 | 1 | +2 | 0.63 |

| Against Q38U-T2 (the 65 units both measured, §5) | Q | Inkling | b | c | net | p |
|---|---:|---:|---:|---:|---:|---:|
| I_T2 | 52 | 46 | 1 | 7 | −6 | 0.07 |
| I_T2_s1 | 52 | 48 | 2 | 6 | −4 | 0.29 |
| I_T2_stock | 52 | 50 | 2 | 4 | −2 | 0.69 |

### 12.5 The levers, with evidence and an estimate

Estimates are the units a lever might move on this suite, from the
mechanism counts above; none is a measurement until an arm runs.

1. **Turns (wall-bound turns).** T2's `--turns-per-wall-s 15` gives a
   900 s unit 60 turns. For Inkling the turn limit, not the wall, ended
   every failure (§12.3). `run_eval.py`'s own design is that "the unit's
   wall budget … is the binding limit and the turn cap only stops a loop";
   at 15 s a turn it is not, for a model whose turns take 8-9 s.
   `--turns-per-wall-s 5` gives 180 turns at the same 1,800 s wall
   (Q38U-T2's wall). Precedent: GLM arm A → B (wall-scaled turns) +5,
   when GLM's failures "ended on the turn cap at 10-30% of their wall"
   (HARNESS_NOTES, Headroom). Targets: the 3-4 passes per arm decided on
   the last turn, and the units Qwen3.8 won by spending its wall
   (`163_a/b/c/e`, `176_a`, `196`). **Estimate +1 to +4.** Cost: failed
   units run to the wall; the arm grows from ~4.5 h to ~8-10 h, still
   under Q_T2's ~13.5 h. Passing units are as fast as before.
2. **Cap and answer room (16,384 cap, `--answer-room 8192`).** Thinking up
   to 8,192 (`I_T2`: 6,144, Q_T2: up to 8,192) and at least 8,192 tokens
   for the answer (`I_T2`: 2,048 after a wrap). Targets: the 73-88 cut
   calls and 51-82 truncated writes per arm (`192` above, `158_c` 19 cut
   calls, `163_d` 12, `173` 21 on stock), and fewer wraps. DeepSeek lost
   units at 16K (U → U_8k +4, HARNESS_NOTES) because a 16K turn took ~9
   minutes at ~30 tok/s and the wall bound; Inkling at ~460-500 tok/s
   writes 16K in ~33 s and never reached its wall. **Estimate 0 to +2**,
   mostly 192-like units; risk: a long empty turn (if #588 survived) now
   costs ~33 s instead of ~16.
3. **The #588 patch** (PR #632, DECISIONS §11; Strata report §16): 105-203
   empty capped turns per arm (5-10% of turns), concentrated in failures
   (`163_d` in `I_T2_s1`: 38 of 60 turns; `168_c` in `I_T2`: 35). With
   more turns this matters less for the turn budget, more for time.
   **Estimate 0 to +2.** Risk: after a wrap the model writes text first
   (§16.4 there).
4. **The stock pack.** `I_T2_stock` 53/71: +4 / +2 over the two Derisked
   rows (p 0.22 / 0.63), passing three units both Derisked draws failed
   (`163_a`, `163_c`, `196`, all Q38U-T2 wins), with 37-49% fewer empty
   capped turns (§10.3). Not significant; decided anyway (DECISIONS §8:
   the improved arm runs on stock, and stock is the 5090 default, §10).
   So `I_T3`'s base is `I_T2_stock`, and the pack is not one of its changes.
5. **Thinking effort `max`.** Not measured. The runner sends only
   `enable_thinking: true`; Basalt then applies the template's default
   `high` (0.9), and a server `--reasoning-effort max` would apply 0.99 to
   every thinking request (`serve/service.py` `thinking_kwargs`,
   1043-1049; `serve/frontend.py` 495-517, which drops `enable_thinking:
   true`). Longer thinking means more wraps and more #588 exposure on an
   arm whose failures are turn-bound, not think-bound. **Not included**;
   a follow-up after `I_T3` if its failures turn out think-bound.
6. **UP_8k's switches** (forced-act turn, planning lines,
   done-when-verified, no web tools; harness10): `I_UP8k`, §12.6.
7. **Lenient parsing.** Not a lever we can switch (a server property),
   but its sign is now known: it executes cut calls (§12.3). The cap and
   answer room (2) is how the arm limits the damage; a runner rule "do not
   execute a call from a reply that ended `length`" would end it, but that
   is a harness change (an era move), proposed for the next era (TODO).

### 12.6 I_UP8k: DeepSeek's UP_8k setup on Inkling

`I_UP8k` (harness10, era `9bcd5aa6`, Derisked pack, the same unpatched
Basalt; 18:25:29-22:28:05Z) passed **49/71**, the same count as `I_T2`:

| | I_UP8k |
|---|---|
| Passed | 49/71 (Py 22, Go 6, C 6, C++ 5, Rust 5, Zig 4, Sh 1) |
| Against I_T2 / I_T2_s1 / I_T2_stock (71 units) | +3 −3 (p 1.0) / +1 −3 (p 0.63) / +0 −4 (p 0.13) |
| Against Q38U-T2 (65 units) | 47 vs 52: 2 in, 7 out, −5 (p 0.18) |
| Failures at the turn limit | 21 of 22 (the other: `122_gemm_blocked_d`, `dlsym`); median 23% of the wall; 0 wall hits |
| Empty capped turns | 226 in 19 units (server: 443 wraps, **176 "no answer"**) |
| Capped turns with a call / truncated heredocs | 75 / 60 |
| **Forced-act turns** | **68, of which 18 made a tool call; 56 ended at the cap still thinking** (median 22,650 reasoning characters) |
| Decode | median 512 tok/s, acceptance 94% (server log) |
| Integrity | 71 transcripts, the scanner flags 1: `163_bigint_divmod_c` (FAIL) ran `find / -name "*.c" -path "*big*"`, which listed the reference file names under the mounted tree (`~/ob-ink2/evals/refs/163_bigint_divmod/`), then tried `cat` and `sed` on one; **both refused** by the eval guard, the content never read, the unit failed. Needs the sealed review before any board use; the listing exposure is a property of the eval-host container (the tree is mounted), noted for the next era |

**Reading.** UP_8k's switches did not help Inkling, and its central
mechanism does not work on this model and server. The forced-act turn
sends `enable_thinking: false`; Basalt maps that to `reasoning_effort:
none`, which is not a hard switch on Inkling ("at none the model still
writes a short thinking block", Basalt `docs/models/inkling2.md`; the
queue's own probe: 117 reasoning characters on a one-line question). On a
stalled agent turn it wrote 22K characters of thinking to the cap in 56
of 68 forced turns, and with thinking "off" the server applies no budget,
so there is no wrap to cut it. On DeepSeek every forced turn acted (24 of
24, HARNESS_NOTES). The planning lines did not move `168` (0/6, as every
Inkling arm). So nothing from UP_8k goes into `I_T3`.

### 12.7 The improved arm: I_T3

**Design.** `I_T3` = `I_T2_stock` (`I_T2`'s switches on the stock pack) with three changes, each aimed at a mechanism
§12.3 measured, and nothing else:

| | I_T2_stock (and I_T2) | **I_T3** | Why (evidence) |
|---|---|---|---|
| Turns | `--turns-per-wall-s 15` (60 turns at 900 s) | **`--turns-per-wall-s 5`** (180) | every failure ends on the turn limit at ~23-30% of the wall; passes land on the last turn (§12.3) |
| Per-turn cap | 8,192 (`OPENBEAST_AGENT_MAX_TOKENS`, declared `--reasoning-budget 8192`) | **16,384** | the cut-call damage: 73-88 cut calls and 51-82 truncated writes per arm (§12.3) |
| Answer room (serve) | `auto`: thinking ≤ 6,144, answer ≥ 2,048 | **`--answer-room 8192`**: thinking ≤ 8,192, answer ≥ 8,192 | the same, plus Q_T2's thinking ceiling (8,192) |
| Engine | Basalt v0.5.7 | **v0.5.7 + PR #632** (DECISIONS §11) | 105-226 empty capped turns per arm (§12.2, §12.6) |
| Wall | ×2 (1,800 s at 900 s) | same | Q_T2's; the fair budget between the two models |
| Pack | stock (I_T2: Derisked) | stock | DECISIONS §8 and §10; `I_T3` against `I_T2_stock` then differs in the three changes only |
| Everything else | T2's switches, harness9 `f38a29c7`, one stream, eval-host container | same | |

**Not included, and why.** UP_8k's switches (§12.6: no gain, the forced
act fails on Inkling). Effort `max` (unmeasured, more wraps; §12.5 item
5). The launcher's `PACK=derisked` runs the same arm on the Derisked pack
(its own model name) if Max wants that row too.

**Bundled.** Three changes in one arm, because Max asked for one improved
run and each change has its own mechanism evidence. Read as one setup:
`I_T3` against `I_T2_stock` (53; the same pack, harness, era and engine
pin) and against Q38U-T2 (52 scored; paired on the 65 units), under
DECISIONS §6's switch rule. The follow-ups that take it apart,
both defined in the campaign and not queued: `I_T3_t15` (I_T3 at 15 s a
turn: isolates the turns) and `I_T3_8k` (I_T3 at the 8,192 cap with
`--answer-room auto`: isolates the cap and answer room). The #588 patch is
policy from now on (Max), so it is not ablated.

**What to expect.** From §12.5: turns +1 to +4, cap 0 to +2, patch 0 to +2,
overlapping (the same units). From the stock base of 53, a point estimate
of 54-56: the scored count already matches Q38U-T2's, so what `I_T3` can
add is a lead on the paired units (stock is −2 there, §10.4); any single
draw inside about ±7 of 53 is within noise (§12.4). Time: ~8-10 h
(UNVERIFIED; the turn-limited failures, ~4 h of each arm, can now run to
the 1,800 s wall), against Q_T2's ~13.5 h; passing units keep their
seconds.

**Launch.** `~/openbeast-runs/ink-I_T3-run.sh` on the rig (TODO). It
refuses to start unless the queue is done, no eval runs, the trees, era,
image and the patched tree's hashes check out; then it owns the GPU
(restore on exit), serves the patched tree with `--answer-room 8192`,
gates on the serve's arguments, mount, DNS fix, a chat and a #588 probe
with 0 empty replies, runs the arm, and logs the server's wrap and
"no answer" counts.
