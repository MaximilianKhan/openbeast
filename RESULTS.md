# Eval Suite Results

Cross-system benchmark results for the 144-task eval suite (40 easy / 53 medium /
51 hard, across 12 categories). Each section below is one host system. Models
are ranked within their host by accuracy primary, speed tie-breaker.

```bash
python3 evals/scoring.py --compare-hosts                   # side-by-side per-model across systems
python3 evals/scoring.py --host "NVIDIA GeForce RTX 5090 ×1"   # filter to one host
python3 evals/scoring.py --by-category                     # per-category drilldown
```

---

## Host: NVIDIA GeForce RTX 5090 ×1

**Status:** ✅ Complete · sweep ran 2026-05-05 23:24 → 2026-05-06 06:45 PT (7h 21m)

### System fingerprint

| | |
|---|---|
| **GPU** | NVIDIA GeForce RTX 5090 (Blackwell architecture) |
| **GPU UUID** | `GPU-fef09a0c-f82c-f48a-5dd6-03942fdb2c66` |
| **VRAM** | 32,607 MiB total |
| **VBIOS** | 98.02.2E.40.E1 |
| **Driver** | 595.71.05 |
| **Compute capability** | 12.0 |
| **CPU** | AMD Ryzen 9 9950X3D (16-core) |
| **RAM** | 122 GiB |
| **OS** | Arch Linux (kernel 7.0.3-arch1-2) |
| **Inference engine** | llama.cpp built with CUDA, Blackwell-tuned |
| **Sweep wall-clock** | 26,489 s (7h 21m total, all 5 models succeeded) |

### Leaderboard

Ranking: accuracy primary, speed tie-breaker, hard-pass count tie-breaker beyond that.

| # | Model | Accuracy | Speed | Composite | Pass | Hard | Wall-time |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | **Qwen 35B-A3B Uncensored Q4_K_M** | **97.3** | 86.7 | **94.6** | **140/144** | **49/51** | 3,024 s |
| 2 | Qwen 27B Uncensored Q5_K_P | 96.4 | 72.5 | 90.4 | 139/144 | 49/51 | 5,819 s |
| 3 | Qwen 27B Q5_K_XL | 95.5 | 68.2 | 88.7 | 138/144 | 48/51 | 6,951 s |
| 4 | Gemma 4 31B-it Q5_K_XL | 94.6 | 57.4 | 85.3 | 137/144 | 47/51 | 7,647 s |
| 5 | Qwen 35B-A3B MoE Q4_K_M | 93.5 | 86.3 | 91.7 | 136/144 | 46/51 | 2,951 s |

### Difficulty breakdown

| Model | Easy | Medium | Hard |
|---|---:|---:|---:|
| Qwen 35B-A3B Uncensored Q4_K_M | 38/40 | 53/53 | 49/51 |
| Qwen 27B Uncensored Q5_K_P | 39/40 | 51/53 | 49/51 |
| Qwen 27B Q5_K_XL | 39/40 | 51/53 | 48/51 |
| Gemma 4 31B-it Q5_K_XL | 39/40 | 51/53 | 47/51 |
| Qwen 35B-A3B MoE Q4_K_M | 40/40 | 50/53 | 46/51 |

### Per-category accuracy (12 categories)

```
MODEL                      Algos&DS  Concur  Distrib   LLM/ML  MathFin  PerfHW   Phys  Prob&Stats   PureMath  SWE/DevOps  Security  DSP
35B Uncensored Q4_K_M       100.0    100.0    83.3    100.0    92.2    100.0   100.0    100.0       100.0       95.9     100.0   100.0
27B Uncensored Q5_K_P       100.0    100.0    94.4    100.0    92.2    100.0    89.2    100.0       100.0       93.9      92.5   100.0
27B Q5_K_XL                 100.0    100.0    83.3    100.0    92.2    100.0   100.0     87.1       100.0       93.9      92.5   100.0
Gemma 4 31B-it Q5_K_XL      100.0    100.0    83.3    100.0    92.2    100.0   100.0    100.0       100.0       87.8      80.0   100.0
35B MoE Q4_K_M              100.0    100.0    77.8     88.5    84.3     91.2    89.2    100.0       100.0       93.9     100.0   100.0
```

(Cells are accuracy %; column abbreviations: PureMath = Pure & Abstract Math, etc.)

### Failure pattern (which tasks tripped multiple models)

| # models failing | Task | Difficulty | Diagnosis |
|---:|---|---|---|
| 5/5 | `42_value_at_risk` | hard | The CVaR convention is ambiguous in practice — even with our spec clarification, the float-tolerance + ceil/floor edge case is brutal. Worth a tighter spec or split into VaR-only and CVaR-only tasks. |
| 4/5 | `121_quorum_kv` | hard | Dynamo-style quorum with read-repair: the timestamp + tie-break-by-node ordering trips models. Spec is correct; the task is genuinely hard. |
| 4/5 | `17_deploy_rollback` | medium | Bash deploy with healthcheck + rollback. Models struggle with stateful bash + cleanup ordering. |
| 4/5 | `85_base64` | easy | URL-safe base64 with padding stripped — surprising failures. Likely an ambiguity around accepting bytes vs. str inputs. |
| 2/5 | `108_hmac_verify`, `124_rkf45` | medium / hard | One-off model errors, not a task issue. |

### Notable observations

1. **The new uncensored 35B-A3B Q4_K_M is the runaway winner** — at 97.3 % accuracy it beats every other model AND its speed (86.7) is virtually tied with the standard MoE 35B (86.3, fastest by raw seconds). It's both more accurate AND nearly as fast as the second-fastest model. Composite 94.6 is 4 points clear of #2.

2. **Two-track speed regime.** Looking at wall-time: 35B-A3B variants finish in ~50 min (MoE only activates 3B params at a time → fast); the 27B and Gemma 31B dense models take 1.5–2 hr. The MoE architecture is the dominant lever for sweep throughput.

3. **Gemma is still the slowest by a wide margin** (7,647 s vs ~3,000 s for the MoEs) but ties the field on hard-task accuracy (47/51). Consistent with the prior 50-task observation: Gemma trades raw speed for careful answers.

4. **The 35B uncensored beats the 35B standard on accuracy** by 3.8 points despite identical architecture and same Q4_K_M quant. Interesting datapoint: an uncensored fine-tune at the same quant outperforms the original on most categories. The standard MoE specifically struggles on **LLM / ML** (88.5 % — the only model below 100 % there), **Performance & HW Opt** (91.2 % — only model below 100 %), and **Physics + Math Finance**. The uncensored variant matches or beats it on all 12 categories.

5. **Three universal-100 categories**: Algorithms & DS, Concurrency & Systems, Pure & Abstract Math, plus Signal Processing & DSP (1-task category). Every model hits 100 % on the first three. The harness will need harder additions in these categories before the next round to retain discrimination signal.

---

## Host: 2× NVIDIA GeForce RTX 3090 Ti (planned)

**Status:** _pending — Max will run this on the dual-3090 Ti rig._

### Anticipated workload differences vs. the 5090

The 2×3090 Ti host has **48 GB combined VRAM** (vs. 32 GB on the 5090), which
opens up:

1. **Models that don't fit on the 5090.** Qwen 70B-class quants (~40 GB at
   Q4_K_M) fit on 2×3090 Ti via tensor-parallel or layer-split; they don't fit
   on a single 5090. Add new serve scripts under `scripts/` (e.g.
   `serve-qwen-70b-q4.sh`) and entries in `evals/benchmark_all.py`'s `MODELS`
   list. The `host_id` auto-tag will keep the leaderboards distinct.

2. **Same models, different VRAM ceiling.** The 5 existing models all fit on
   one 3090 Ti (24 GB); using both GPUs via tensor-parallelism or layer-split
   may change throughput. Side-by-side `--compare-hosts` will surface the
   per-token speed delta directly.

### Recommended setup steps for the 3090 Ti host

1. `git clone` the repo to the new machine.
2. Build llama.cpp with CUDA for compute capability **8.6** (Ampere; 3090 Ti is
   GA102 — the 5090's Blackwell CUDA 12.0 build is incompatible).
3. Symlink the `weights/` directory or re-download the GGUFs.
4. (Optional) Add larger models that fit in 48 GB but not 32 GB.
5. Run `python3 evals/benchmark_all.py` — the host_id auto-detects as
   `NVIDIA GeForce RTX 3090 Ti ×2` and entries land alongside the 5090 ones.

### Comparison points to watch for

When both hosts have results, run `python3 evals/scoring.py --compare-hosts`
for the side-by-side table.

- **Per-token throughput.** 5090 has ~2× the FLOPs of a single 3090 Ti and
  ~1.8 × the memory bandwidth (~1.8 TB/s vs ~1 TB/s). Single-GPU inference on
  the 3090 Ti should be **slower per task**.
- **Tensor-parallelism overhead.** Splitting a model across two 3090 Tis adds
  PCIe sync cost. Whether 2×3090 Ti tensor-parallel beats 1×5090 single-GPU
  depends on the model size and inter-GPU bandwidth.
- **Accuracy parity.** Inference math is deterministic (same quant, same
  weights, same temperature); accuracy should be **identical** modulo
  random-seed effects in tasks that use `random.seed`. A non-zero accuracy
  delta on those tasks signals a non-determinism bug, not a hardware effect.

---

## Reproducing these results

```bash
# Full sweep (whatever GPU you have)
python3 evals/benchmark_all.py

# Single model
python3 evals/benchmark_all.py --models qwen-35b-a3b-uncensored-q4

# Subset of tasks
python3 evals/benchmark_all.py --tasks 21,22,23

# After results land:
python3 evals/scoring.py                                        # current host's leaderboard
python3 evals/scoring.py --by-category                          # per-category drilldown
python3 evals/scoring.py --compare-hosts                        # side-by-side across systems
python3 evals/scoring.py --host "NVIDIA GeForce RTX 5090 ×1"     # filter to one host
python3 evals/scoring.py --host "NVIDIA GeForce RTX 3090 Ti ×2" --by-category
```

The raw data is in `evals/results/` (per-run JSON, kept all) and
`evals/leaderboard.json` (latest score per `(host_id, model_slug)`).
