# Eval Suite Results

Cross-system benchmark results. Each section below is one host system; models
are ranked within their host by accuracy primary, speed tie-breaker.

> **Suite version note.** The numbers below are from the **144-task suite**
> (40 easy / 53 medium / 51 hard). The suite was hardened in three steps
> after this sweep: (1) v3 — 4 spec/harness fixes + 15 new hard tasks →
> 159 base tasks; (2) v3 — 13 base tasks variant'd across 6 langs (77
> entries); (3) v3.5 (2026-05-07) — Zig spec defect fixed + 20 more base
> tasks variant'd (additional 120 entries). The live suite is now
> **~313 effective test units** across **33 variant base tasks** + 126
> single-variant legacy tasks, with token tracking and a result cache
> (`evals/cache/`) for retryable sweeps. Distribution table and
> methodology: [`evals/README.md`](../evals/README.md).

```bash
python3 evals/scoring.py --compare-hosts                   # side-by-side per-model across systems
python3 evals/scoring.py --host "NVIDIA GeForce RTX 5090 ×1"   # filter to one host
python3 evals/scoring.py --by-category                     # per-category drilldown
```

---

## Eval suite distribution

**159 base tasks** across **12 categories** with deterministic validation per
task. Difficulty split: **40 easy · 53 medium · 66 hard**. Difficulty weights
in scoring: easy=1, medium=1.5, hard=2 (per-variant weight = base / num
variants).

**33 of the 159 base tasks** have multi-language variants (Python / Go / C /
C++ / Rust / Zig — 6 languages) — see the variant rollout section at the
end. Effective test units after variants: **~313** (126 single-variant
legacy + 187 variant entries). Total weighted points are invariant —
variants split a single base task's weight, not multiply it.

### Category × difficulty

| Category | Easy | Medium | Hard | Total |
|---|---:|---:|---:|---:|
| Algorithms & DS | 4 | 11 | 7 | **22** |
| Concurrency & Systems | 4 | 4 | 8 | **16** |
| Distributed / SysDesign | 4 | 4 | 4 | **12** |
| LLM / ML | 4 | 2 | 3 | **9** |
| Mathematical Finance | 4 | 5 | 7 | **16** |
| Performance & HW Opt | 4 | 2 | 5 | **11** |
| Physics | 4 | 3 | 5 | **12** |
| Probability & Stats | 3 | 3 | 4 | **10** |
| Pure & Abstract Math | 3 | 7 | 12 | **22** |
| SWE / DevOps | 2 | 7 | 6 | **15** |
| Security | 4 | 4 | 5 | **13** |
| Signal Processing & DSP | 0 | 1 | 0 | **1** |
| **Totals** | **40** | **53** | **66** | **159** |

### Subcategory drilldown

| Category | Subcategory | Count |
|---|---|---:|
| Algorithms & DS | Computational geometry | 1 |
| Algorithms & DS | Graph algorithms | 3 |
| Algorithms & DS | Hashing structures | 2 |
| Algorithms & DS | Linear data structures | 3 |
| Algorithms & DS | Parsing | 1 |
| Algorithms & DS | Recursion / interpretation | 2 |
| Algorithms & DS | Regex | 1 |
| Algorithms & DS | Sorting | 1 |
| Algorithms & DS | String algorithms | 4 |
| Algorithms & DS | Trees | 4 |
| Concurrency & Systems | Async patterns | 4 |
| Concurrency & Systems | Concurrent data structures | 4 |
| Concurrency & Systems | Networking / state machines | 1 |
| Concurrency & Systems | Race condition fixes | 1 |
| Concurrency & Systems | Synchronization primitives | 4 |
| Concurrency & Systems | Systems APIs | 2 |
| Distributed / SysDesign | Causal ordering | 1 |
| Distributed / SysDesign | Consistent hashing | 1 |
| Distributed / SysDesign | Cryptographic primitives | 1 |
| Distributed / SysDesign | Distributed coordination | 2 |
| Distributed / SysDesign | Encoding & serialization | 1 |
| Distributed / SysDesign | Identifiers | 1 |
| Distributed / SysDesign | Rate limiting | 2 |
| Distributed / SysDesign | Replication & consistency | 1 |
| Distributed / SysDesign | Service patterns | 2 |
| LLM / ML | Activations | 3 |
| LLM / ML | Attention | 1 |
| LLM / ML | Caching | 1 |
| LLM / ML | Norms | 1 |
| LLM / ML | Position embeddings | 1 |
| LLM / ML | Similarity metrics | 2 |
| Mathematical Finance | Credit risk | 1 |
| Mathematical Finance | Exotic derivatives | 1 |
| Mathematical Finance | Fixed income | 2 |
| Mathematical Finance | Option pricing | 3 |
| Mathematical Finance | Risk metrics | 3 |
| Mathematical Finance | Term structure models | 1 |
| Mathematical Finance | Time value of money | 5 |
| Performance & HW Opt | Asymptotic refactoring | 1 |
| Performance & HW Opt | Bit-twiddling | 3 |
| Performance & HW Opt | Cache locality | 2 |
| Performance & HW Opt | Loop optimization | 1 |
| Performance & HW Opt | RISC-V assembly | 3 |
| Performance & HW Opt | Vectorization | 1 |
| Physics | 3D rotation / geometry | 1 |
| Physics | Classical mechanics | 4 |
| Physics | N-body / orbital mechanics | 1 |
| Physics | ODE numerical integration | 1 |
| Physics | PDE / wave propagation | 1 |
| Physics | Quantum mechanics | 1 |
| Physics | Solid-state physics | 1 |
| Physics | Thermodynamics | 2 |
| Probability & Stats | Combinatorial probability | 1 |
| Probability & Stats | Descriptive statistics | 3 |
| Probability & Stats | Inference | 1 |
| Probability & Stats | Monte Carlo simulation | 1 |
| Probability & Stats | Optimal stopping | 1 |
| Probability & Stats | Psychometrics | 2 |
| Probability & Stats | Stochastic processes | 1 |
| Pure & Abstract Math | Bit operations (math) | 1 |
| Pure & Abstract Math | Iterative numerical methods | 3 |
| Pure & Abstract Math | Linear algebra | 5 |
| Pure & Abstract Math | Number theory | 8 |
| Pure & Abstract Math | Polynomial algebra | 5 |
| SWE / DevOps | APIs / web | 2 |
| SWE / DevOps | Bash / scripting | 2 |
| SWE / DevOps | CLI tools | 1 |
| SWE / DevOps | Data engineering | 3 |
| SWE / DevOps | Debugging | 3 |
| SWE / DevOps | File operations | 2 |
| SWE / DevOps | Refactoring | 1 |
| SWE / DevOps | Testing | 1 |
| Security | Auth & access control | 1 |
| Security | Cipher implementation | 3 |
| Security | Cryptographic primitives | 3 |
| Security | Input validation | 1 |
| Security | Side-channel safety | 1 |
| Security | Token management | 3 |
| Security | Vulnerability remediation | 1 |
| Signal Processing & DSP | Frequency-domain analysis | 1 |

### Multi-language variants (13 base tasks → 77 variant entries)

| Task | # variants | Languages |
|---|---:|---|
| 19_three_way_quicksort | 6 | Py / Go / C / C++ / Rust / Zig |
| 31_is_power_of_two | 6 | Py / Go / C / C++ / Rust / Zig |
| 51_toposort | 6 | Py / Go / C / C++ / Rust / Zig |
| 52_unionfind | 6 | Py / Go / C / C++ / Rust / Zig |
| 61_extgcd | 6 | Py / Go / C / C++ / Rust / Zig |
| 65_miller_rabin | 6 | Py / Go / C / C++ / Rust / Zig |
| 73_count_vowels | 6 | Py / Go / C / C++ / Rust / Zig |
| 74_palindrome | 6 | Py / Go / C / C++ / Rust / Zig |
| 122_gemm_blocked | 5 | Go / C / C++ / Rust / Zig (perf-flavored — no Python) |
| 148_convex_hull | 6 | Py / Go / C / C++ / Rust / Zig |
| 155_tonelli_shanks | 6 | Py / Go / C / C++ / Rust / Zig |
| 158_karatsuba_bytes | 6 | Py / Go / C / C++ / Rust / Zig |
| 159_ntt_convolution | 6 | Py / Go / C / C++ / Rust / Zig |

Each variant is its own scored test unit. The leaderboard reports per-language
accuracy via `python3 evals/scoring.py --by-language`. For full schema /
methodology / pitfalls, see [`evals/README.md`](evals/README.md).

Effective test units after the rollout: **223** (146 single-variant legacy +
77 variant entries). All 77 variants verified end-to-end with reference
implementations (`python3 tests/audit_variants.py`).

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

## v3 smoke test — single-model run on the 197-task suite (2026-05-06)

**Status:** ✅ Complete · single-model smoke test on `Qwen 35B-A3B Uncensored Q4_K_M` · 11:27 → 14:54 PT (3h 27m wall-clock) · pre-overnight validation of v3 changes

This was a smoke test of the v3 suite (Phases 1-4 from `docs/WORK_PLAN.md`)
against the leaderboard winner before kicking off the full 5-model overnight
sweep. **Five high-value findings emerged**, summarized below.

### Headline scores

| | v1 (144 tasks) | v3 (197 effective units) | Δ |
|---|---:|---:|---|
| Accuracy | 97.30 | **91.75** | −5.55 |
| Speed | 86.7 | **86.43** | −0.27 |
| Composite | 94.6 | **90.42** | −4.18 |
| Pass | 140 / 144 | **177 / 197** (89.8%) | — |
| Hard pass | 49 / 51 | **65 / 80** | — |
| Tokens | — (not tracked in v1) | **9.81M total** (8.79M prompt / 1.02M completion) | — |
| Cost-equivalent (Sonnet 4.6) | — | **~$34** | — |

The accuracy drop is **expected, not a regression**: the v3 suite is harder
by design (15 hardening tasks added to de-saturate categories, plus 51
variant entries that surfaced compiled-language gaps that didn't exist in
the Python-only v1).

### Phase 1 spec/harness fixes — all 4 verified

Net **+4 passes** vs v1 from the targeted fixes:

| Task | v1 | v3 | Note |
|---|---|---|---|
| `42_value_at_risk` | ❌ failed | ✅ passed | numpy-substring lint fixed |
| `85_base64` | ❌ failed | ✅ passed | input-type ambiguity fixed |
| `121_quorum_kv` | ❌ failed | ✅ passed | return-value contract documented |
| `17_deploy_rollback` | ❌ failed | ✅ passed | `pre_validate` re-asserts fixtures |

### Per-language accuracy (first time we have this number)

```
python: 93.21%   (149 / 158)
   c++: 74.02%   ( 10 /  13)
     c: 69.29%   (  9 /  13)
    go: 66.93%   (  9 /  13)
```

**~26-point gap between Python and Go.** This is the first quantitative
measurement of the cross-language gap in our local model — and it's much
larger than expected. Even C++ (closer to Python in flexibility) sits 19
points below Python.

### Cross-language differentials (4 confirmed)

```
✅ 19_three_way_quicksort        PPPP    (clean)
⚠️ 31_is_power_of_two            PFFP    (Go + C fail — surprising on a trivial bit-trick task)
⚠️ 51_toposort                   PPFF    (C + C++ fail)
✅ 52_unionfind                  PPPP
✅ 61_extgcd                     PPPP
✅ 65_miller_rabin               PPPP
✅ 73_count_vowels               PPPP
✅ 74_palindrome                 PPPP
✅ 122_gemm_blocked              PPP-    (no Python variant by design)
✅ 148_convex_hull               PPPP
⚠️ 155_tonelli_shanks            PFFP    (Go + C fail; Go was a typed-int Lsh underflow)
⚠️ 158_karatsuba_bytes           PFPF    (Go + C++ fail — manual carry handling)
❌ 159_ntt_convolution           FFFF    (SPEC DEFECT — see below)
```

**Each differential has a different shape.** Not "Go is uniformly hard for
the model" — it's task-by-task language fragility. This validates the Phase 4
investment: the variant system surfaces information the Python-only test
literally cannot.

### One spec defect we missed: `159_ntt_convolution`

All 4 NTT variants failed — but with **different per-language failures** —
because the test fixtures include an empty-array case (`([], [1,2,3])`)
written as a blank line in `input.txt`. Naive line-by-line parsing across
all 4 languages handles the blank line differently. **Not a model failure;
a spec defect on our side.** Same class of bug we caught 4× in the v1
post-mortem; we missed this one during Phase 2 authoring.

Tracked in `docs/TODO.md` as a v3.5 prereq blocking the overnight sweep.

### Skills system: 0 of 197 task agents called any skill tool

`list_skills`, `load_skill`, and `start_skill_agent` were never invoked
during the smoke test. AGENTS.md alone isn't enough to nudge spontaneous
skill use on directive task prompts ("Create /tmp/eval_X/... with..."). This
is OK for evals — eval prompts are too directive for skill descriptions to
match — but informs the longer-term Phase 5 priority. (`opencode stats` did
show 1 `list_skills` call from interactive use, so the system works; it just
doesn't fire on eval-style prompts.)

### Per-category breakdown (re-saturation effects)

| Category | v1 | v3 | Change | Note |
|---|---:|---:|---|---|
| LLM / ML | 100.0 | 100.0 | flat | |
| Math Finance | 92.2 | 100.0 | +7.8 | the 42_value_at_risk fix landed here |
| SWE / DevOps | 95.9 | 100.0 | +4.1 | the 17_deploy_rollback fix landed here |
| Signal Proc & DSP | 100.0 | 100.0 | flat | (1 task) |
| Algorithms & DS | 100.0 | 97.83 | **−2.17** | hardening tasks de-saturated as designed |
| Concurrency & Sys | 100.0 | 92.31 | **−7.69** | chase-lev hardening worked |
| Security | 100.0 | 90.0 | −10.0 | UOV is a new fail (hardening) |
| Physics | 100.0 | 89.19 | −10.81 | rkf45 known-weak |
| Performance & HW | 100.0 | 88.24 | −11.76 | 22_optimize_quadratic regressed |
| Probability & Stats | 100.0 | 87.10 | −12.9 | bayesian_ab failed |
| Pure & Abstract Math | 100.0 | 82.67 | **−17.33** | tonelli/karat/ntt/berlekamp variants |
| Distributed / SysDesign | 83.3 | 80.56 | −2.74 | flat |

**Three previously-saturated categories de-saturated** (Algorithms & DS,
Concurrency & Systems, Pure & Abstract Math) — Phase 2 hardening worked as
designed. Pure & Abstract Math took the biggest hit because the variant
tasks (Tonelli-Shanks, Karatsuba, NTT, Berlekamp-Massey) cluster there.

### All 20 failures, classified

**Spec defects (4) — discount these:**
- `159_ntt_convolution` × 4 langs (input format ambiguity on empty case)

**Cross-language gaps (8) — variant-system findings:**
- `31_is_power_of_two_b/c` (Go, C — trivial task with language-specific traps)
- `51_toposort_c/d` (C, C++ — manual data-structure work)
- `155_tonelli_shanks_b/c` (Go, C)
- `158_karatsuba_bytes_b/d` (Go, C++ — byte-array carry handling)

**Phase 2 hardening discrimination (3) — these are the new tasks doing their job:**
- `124_rkf45` (max_iter, also v1 weakness)
- `152_chase_lev_deque` (Python — concurrent code)
- `156_berlekamp_massey` (subprocess timeout)
- `143_uov` (Unbalanced Oil-and-Vinegar crypto)

**Probable regressions (3) — flag for investigation in overnight sweep:**
- `127_aes_keysched` (passed v1, hit 30-min timeout in v3 — likely deep variance)
- `22_optimize_quadratic` (passed v1, failed v3)
- `113_bayesian_ab` (passed v1, failed v3)
- `118_distributed_lock` (passed v1, failed v3)
- `68_circuit_breaker` (passed v1, failed v3)

(The variance on these can only be characterized by the full 5-model
overnight sweep — single-run regressions can be noise.)

### Wall-clock recalibration

Original estimate: 70-80 min. Actual: **207 min (3h 27m)**.

The overshoot was driven by **four 18-30 min outlier tasks** that hit
`max_iter` or `subprocess timeout`:
- `124_rkf45`: 18 min
- `127_aes_keysched`: 30 min (hit run_eval's `max_iter * 60` subprocess timeout)
- `156_berlekamp_massey`: 30 min (same)
- `158_karatsuba_bytes_d` C++: 28 min

For the upcoming **overnight 5-model sweep**, recalibrate from 10-12h to
**12-14h** to account for similar outliers across all 5 models.

### Per-task token-cost distribution (first time we have this)

```
Distribution    Count    Total tokens    Avg
< 15K              43         400K        9.3K   (easy tasks)
15K – 30K          78       1,800K       23K     (medium tasks)
30K – 60K          35       1,500K       43K     (hard tasks)
60K – 200K          8         800K       100K    (heavy tasks: NTT, gemm c++)
200K+               4       1,500K       375K    (max_iter outliers)
```

The four max_iter outliers consumed **15% of all tokens** for **2% of the
tasks**. These are the highest-leverage candidates for either max_iter
budget tuning or task-spec sharpening.

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
