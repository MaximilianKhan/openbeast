# OpenBeast research findings

Durable log of what we've learned. Newest sections appended over time.
Numbers are from the RTX 5090 (32 GB) reference box unless noted.

---

## 1. MTP speculative decoding is lossless (2026-07-08)

**Claim tested:** MTP (Multi-Token Prediction speculative decoding) produces
the same output distribution as the base model, just faster.

**Evidence:** on the v3.5 suite, Qwen 35B-A3B MoE scored 93.74 non-MTP vs
**93.76 MTP** — a 0.02 delta, i.e. statistically identical accuracy. The main
model verifies every drafted token, so accepted sequences match what the model
would have produced alone.

**Consequence:** the speculation knobs (`--spec-draft-n-max`,
`--spec-draft-p-min`) can be brute-forced for throughput with **zero accuracy
risk** and no eval suite — only the lossy knobs (weight quant, KV-cache quant,
context) change accuracy. This underpins the profiling work (§5).

## 2. MTP delivers a large single-stream speedup (2026-07-08)

Whole-suite average tok/s (v4 for MTP, v3.5 for the non-MTP baselines):

| Model | MTP tok/s | non-MTP tok/s | speedup |
|---|---:|---:|---:|
| Qwen 27B (dense) | 73.0 | 53.7 | **+36%** |
| Qwen 35B-A3B (MoE) | 83.0 | 74.3 | +12% |

Peak single-stream decode is much higher than the suite average (the profiler
measured 167 tok/s peak for the 27B MTP on a fast fixed workload vs 73 average
including the hard tasks). **MTP is a big win for single-stream / interactive
use at no accuracy cost.**

**Constraint:** MTP is pinned to `-np 1` upstream — concurrent requests
serialize, no parallelism. So MTP suits a solo/orchestrator role; parallel
worker fleets want plain non-MTP with high `-np`. (See docs/TODO.md multi-node
INVESTIGATION.)

## 3. v4 MTP leaderboard — the three-model result (2026-07-08)

Hardened v4 suite, 291 units, RTX 5090:

| Rank | Model | Acc (weighted) | Raw pass | Hard | tok/s | Wall | Zig |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | Qwen 27B MTP (dense) | **95.63%** | 273/291 | 98/104 | 73.0 | 3.8h | 66.6% |
| 2 | Qwen 35B-A3B MTP (MoE) | 93.76% | 254/291 | 85/104 | **83.0** | 4.3h | 34.5% |
| 3 | Qwopus 27B v2 MTP | 93.00% | 260/291 | 89/104 | 75.3 | 4.6h | 44.7% |

**Findings:**
- **The dense 27B wins outright** — highest accuracy AND second-fastest tok/s
  AND fastest wall-clock. Best all-around; the deployment default.
- **Qwopus ≈ MoE, a statistical dead-heat** (0.76 pts apart). By RAW pass
  count Qwopus wins (260 vs 254); by WEIGHTED accuracy the MoE edges it (93.76
  vs 93.00). See §4 for why these can disagree.
- **The "reasoning-enhanced" fine-tune does NOT beat its base.** Qwopus (SFT
  fine-tune of Qwen 27B, Trace Inversion from Claude Opus) scores ~2.6 pts
  under the dense 27B base on this coding/math benchmark. The fine-tune's
  value here is lateral, not upward.
- **MoE trades accuracy for speed:** the 35B-A3B is the fastest (83 tok/s) but
  weakest on the hard tier (85/104) — fewer active params show up on hard
  algorithms.

## 4. Raw pass-count and weighted accuracy can disagree (2026-07-08)

The leaderboard `accuracy` is **difficulty-weighted AND variant-fractional**:
a whole single-variant hard task (~2 pts) is worth ~6× one language-variant of
a 6-way task (~0.33 pts). So a model can pass MORE raw units yet earn FEWER
weighted points if its passes are spread across partially-completed variant
tasks. Concretely, Qwopus passed 89 hard units earning 98.2 pts, while the MoE
passed 85 hard units but earned 100.2 pts by completing higher-value whole
tasks. **Report both metrics; they answer different questions.**

## 5. Zig is the suite's strongest discriminator (2026-07-08)

Per-language accuracy on the v4 variant tasks, MTP models:

| Model | Zig | (other 5 langs) |
|---|---:|---|
| Qwen 27B MTP | 66.6% | ~93-97% |
| Qwopus 27B v2 MTP | 44.7% | ~90%+ |
| Qwen 35B-A3B MTP | 34.5% | ~85-97% |

Every model is near-saturated on Python/Go/C/C++/Rust and **collapses on Zig**
(a 30-point spread across models). Zig is niche and under-represented in
training data. It is the single most useful axis for ranking these models —
a single-language benchmark would have missed the entire distinction. The
dense 27B is best at Zig too; Qwopus's early apparent "Zig edge" over its base
washed out at full sample (though Qwopus does clearly beat the MoE on Zig).

## 6. Context configs (VRAM-measured, 32 GB card)

| Model | `-c` | Context | Why |
|---|---:|---:|---|
| Qwen 27B MTP Q5 | 294912 | 288K | dense Q5 heavy; n-max 8 draft buffers cost ~600 MiB extra |
| Qwopus 27B v2 MTP Q5 | 344064 | 336K | dense Q5, n-max 4 |
| Qwen 35B-A3B MTP Q4 | 524288 | 512K | Q4 MoE — lighter weights + cheaper KV → most room |

The eval tasks use tiny context (well under a few K tokens), so these ceilings
did NOT affect benchmark scores — they matter for deployment (how much
code/conversation you can hold), not for these results.

## 7. MTP throughput profiling — peak deployment configs (2026-07-08, in progress)

Sweeping the lossless speculation knobs to find peak tok/s per model
(evals/profile_mtp.py; plan docs/MTP_PROFILING_PLAN.md). HOLDS FIXED the lossy
knobs at each model's leaderboard config.

**Qwen 27B MTP — DONE. Already optimal.** Peak = n_max=8, p_min=0.0 at
**167.5 tok/s**, which is exactly its benchmark config. Deeper drafting (n12
125.9, n16 lower) and any acceptance gate (p_min 0.1→160.6, 0.25→152.9,
0.5→142.7) are all slower. No deployment change needed — confirms the
serve-script's empirical tuning. Full curve:

| n_max @ p0 | 2 | 4 | 6 | 8 | 12 | 16 |
|---|---:|---:|---:|---:|---:|---:|
| tok/s | 134.0 | 148.4 | 143.3 | **167.5** | 144.8 | 125.9 |

**Qwen 35B-A3B MTP — in progress.** Early: n_max=2 measured **335 tok/s** (MoE
is much faster than the dense 27B). Watching whether n_max=2 beats its
benchmark n_max=4 — if so, a free deployment speedup. [UPDATE PENDING]

**Qwopus 27B v2 MTP — pending.** [UPDATE PENDING]

## Suite provenance note

Every leaderboard row now records `suite_version` (v3.5 vs v4) + a runtime
block (python/openai/mcp) + full llama.cpp build/commit/source_head + GPU.
The v4 rows are the 3 MTP models; the other 5 are still v3.5 pending a rerun.
The v4 suite itself was rebuilt from a 117-defect review of v3.5 — see
docs/EVAL_REVIEW_2026-07-07.md and docs/EVAL_V4_PLAN.md.
