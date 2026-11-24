# v5 report: Inkling 2 Mini Derisked on the RTX 5090, arm I_T2 (2026-11-24)

> **Final for I_T2 (2026-11-24).** Arm `I_T2` of `v5-thinking-first` ran
> Inkling 2 Mini (Blackfrost's Derisked weights, converted to a Basalt
> NVFP4-MX8 pack) on Basalt v0.5.7, one RTX 5090, from the rig's eval-host
> container: T2's switches at one stream, as Q_T2 was for Qwen3.8. It
> finished with no unit lost, was scanned, and is on the board as row
> `INK-T2`. Three ablation arms (`I_T2_s1`, `I_T2_stock`, `I_UP8k`) are
> queued behind it (§8). Engine choice and its review:
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
| Claude Opus 5.5: OPUS-REF | OPUS-REF | Anthropic API · hosted, weights not public | yes | — | — | 71/71 | 100.0 | 100.0 | 100.0 |
| DeepSeek-V4.1-Flash (Mia EXL3): T2 | DS-T2 | EXL3 2.9 bpw · Mia EAQ v1.1 -v2 · stock | yes | 036638ddd68698ce | 8192 | 65/71 | 94.9 | 96.9 | 88.9 |
| Hy4-flash (NVFP4): H_T2 | HY4-T2 | NVFP4 · atakans build · stock | yes | 036638ddd68698ce | 8192 | 60/71 | 83.4 | 81.5 | 89.0 |
| Qwen3.8 27B Uncensored MTP (the shipped default): Q_T2 | Q38U-T2 | GGUF Q5_K_M · JonathanColetti Uncensored (MTP head kept) · abliterated | yes | 036638ddd68698ce | 8192 | 52/71 (54 raw) | 75.6 | 72.3 | 85.6 |

**Per language** (passed / units of that language; an in-progress arm's passes are over the units done so far)

| Arm | Py | Go | C | C++ | Rust | Zig | Sh |
|---|---:|---:|---:|---:|---:|---:|---:|
| I_T2 | 23/32 | 6/8 | 5/8 | 6/8 | 5/8 | 3/6 | 1/1 |
| Claude Opus 5.5: OPUS-REF | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 |
| DeepSeek-V4.1-Flash (Mia EXL3): T2 | 30/32 | 7/8 | 7/8 | 7/8 | 8/8 | 5/6 | 1/1 |
| Hy4-flash (NVFP4): H_T2 | 26/32 | 6/8 | 7/8 | 8/8 | 8/8 | 4/6 | 1/1 |
| Qwen3.8 27B Uncensored MTP (the shipped default): Q_T2 | 23/32 | 7/8 | 6/8 | 6/8 | 6/8 | 3/6 | 1/1 |

**Time and the wall** (wall hit = the harness killed the agent at the unit's wall, `agent_exit_code == -1`; turns are counted only where the row carries them, from harness9 on)

| Arm | Units done | Unit-hours | Mean min / unit | Wall hits | Passed at the wall | Failed at the wall | Turns of the wall-hit units |
|---|---:|---:|---:|---:|---:|---:|---|
| I_T2 | 71 | 4.5 | 3.8 | 0 | 0 | 0 | — |

**Units gained and lost** (arm against base, over the units both have a verdict for)

| Base → arm | Compared | Net | Gained | Lost |
|---|---:|---:|---|---|
| Q38U-T2 → I_T2 | 68 | -4 | 122_gemm_blocked_b, 124_rkf45, 126_hmac_scratch | 158_karatsuba_bytes_c, 163_bigint_divmod_a, 163_bigint_divmod_b, 163_bigint_divmod_c, 163_bigint_divmod_e, 176_intermittent_500s_a, 196_polyphase_resampler |
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
  noise margin (§8).

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
  against Q38U-T2's. They are what a Basalt user gets, so the row keeps
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
effect, and again after a re-pin past #588.

**The board and the website.** The board ranks by SCORE, so INK-T2 is #4
and Q38U-T2 #5; that is the board's rule and stays. The website shows each
rig's top-ranked board row as its best model (website #43, Max
2026-11-24), never a hand-picked name, so after the sync Inkling is the
RTX 5090's best model there, with Qwen3.8 still the shipped default and the
paired reading beside it. Recorded in
[DECISIONS-2026-11-24.md](DECISIONS-2026-11-24.md).

## 8. Next: three ablations queued

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

## 9. Caveats

- **Single seed**, one run; Basalt samples with a clock seed when none is
  sent, so I_T2 is one draw.
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

## 10. Sources

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
