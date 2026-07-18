> **⚠️ ARCHIVED — historical record.** Describes work that has shipped or been
> superseded; kept for provenance, not current docs. Live state:
> [archive index](README.md) · [`../TODO.md`](../TODO.md) · [`../REFERENCE.md`](../REFERENCE.md).

# MTP throughput profiling — plan & config ledger

**Goal (Max, 2026-07-08):** since MTP is lossless (verified: 35B-A3B MTP
93.76 vs non-MTP 93.74), brute-force the speculation knobs to find the
**peak tokens/sec deployment config** for each MTP model — no accuracy
re-validation needed. The configs we discover are the *practical deployment*
configs; the *leaderboard* configs are what produced the published scores.
**Keep the two distinct.**

## The bright line (why this is safe)

Sweep ONLY the output-neutral speculation knobs — the main model verifies
every drafted token, so these change throughput, never output:
- `--spec-draft-n-max` — draft depth (the dominant lever)
- `--spec-draft-n-min` — minimum draft length
- `--spec-draft-p-min` — acceptance probability floor

HOLD FIXED (lossy — NOT covered by the lossless guarantee; changing them
needs a full v4 eval sweep to trust): weights/quant, KV-cache quant (`q4_0`),
context length, `-ngl`, `-np`. The profiler pins all of these to each
model's leaderboard config below.

## Leaderboard configs (the EXACT flags that produced the v4 scores)

Recorded so the published numbers are always reproducible and never confused
with the deployment configs we're about to discover.

| Model | weight | `-c` | `-np` | KV | `--spec-type` | `n-max` | `p-min` |
|---|---|---:|---:|---|---|---:|---:|
| qwen-27b-mtp-q5 | Qwen3.6-27B-MTP-UD-Q5_K_XL | 294912 | 1 | q4_0 | draft-mtp | 8 | 0.0 |
| qwen-35b-a3b-mtp | Qwen3.6-35B-A3B-MTP-UD-Q4_K_M | 524288 | 1 | q4_0 | draft-mtp | 4 | 0.0 |
| qwopus-27b-v2-mtp-q5 | Qwopus3.6-27B-v2-MTP-Q5_K_M | 344064 | 1 | q4_0 | draft-mtp | 4 | 0.0 |

All also: `-ngl 99`, `--kv-unified`. Published v4 speeds at these configs:
27B MTP 72.96 tok/s, 35B-A3B MTP 83.05 tok/s (Qwopus pending).

## The experiment

`evals/profile_mtp.py`:
- **Phase 1** — sweep `n-max ∈ {2,4,6,8,12,16}` at `p-min=0.0`; find peak depth.
- **Phase 2** — at the peak depth, sweep `p-min ∈ {0.1,0.25,0.5}`.
- ~11 configs/model. Each config restarts the server with that config and
  measures generation tok/s over a fixed temp=0 coding workload (4 prompts ×
  512 tokens, warmup + 2 runs). ~11 × ~100s ≈ 20 min/model, ~1h total.
- Refuses to run while `openbeast-mtp-sweep` is active (GPU exclusivity).

Objective note: this measures **single-stream** generation tok/s (interactive
latency), the right metric for a personal workstation and consistent with the
MTP `-np 1` constraint. If we ever serve multiple concurrent users, re-profile
for aggregate throughput (that's an `-np`/batch sweep, a different optimum).

## Running it (AFTER the sweep finishes)

    python3 evals/profile_mtp.py                 # all 3 MTP models
    python3 evals/profile_mtp.py --models qwopus-27b-v2-mtp-q5

Prefer a memory-capped scope, same discipline as the sweep:

    systemd-run --user --unit=openbeast-mtp-profile --collect \
      -p MemoryMax=92G -p MemorySwapMax=8G -p WorkingDirectory="$PWD" \
      bash -lc 'python3 -u evals/profile_mtp.py > .run/mtp-profile.log 2>&1'

Output → `evals/results/mtp_profile_<stamp>.json` + a summary table
(baseline vs optimal tok/s, best config, % gain).

## After results land

1. Eyeball the table. If a model's optimal differs from its leaderboard
   `n-max`/`p-min`, that's a free deployment speedup (same outputs).
2. Create *deployment* serve variants (e.g. `serve-*-mtp-fast.sh`) or a
   `DEPLOY_SPEC_*` override — do NOT overwrite the benchmark serve scripts,
   so the leaderboard stays reproducible.
3. Optional: one full v4 confirmation run at the winning config to publish
   "identical accuracy, +N% tok/s" — the unquestionable claim.
