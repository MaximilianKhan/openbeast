# Eval Suite Results

Cross-system benchmark results. Each section below is one host system. Models
are **ranked by accuracy** — speed and token cost are reported as separate
columns, not folded into a composite score.

> **Suite version.** Live results below are from the **v3.5 suite — 323
> effective test units** (159 base tasks · 33 variant'd across 6 languages ·
> 187 variant entries replacing 33 base entries). Difficulty split:
> 80 easy · 123 medium · 120 hard. Token tracking on every task; result
> cache at `evals/cache/` for retryable sweeps. Distribution table and
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

### Multi-language variants (33 base tasks → 187 variant entries; 13 listed below seeded the rollout, 20 more added in v3.5)

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

Effective test units after the v3.5 rollout: **323** (126 single-variant base
tasks + 187 variant entries; 10 multi-variant base tasks ship < 6 langs, hence
323 vs the 313 ceiling). All variants verified end-to-end with reference
implementations (`python3 tests/audit_variants.py`).

---

## Host: NVIDIA GeForce RTX 5090 ×1

**Status:** 4 of 5 models complete (v3.5 sweep ran 2026-05-08 04:13 → 21:42 PT;
Gemma 4 31B-it currently re-running after the original Gemma slot was killed
mid-run at task 8/323).

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
| **Inference engine** | llama.cpp **build 8893** · commit `6217b4958` · GNU 15.2.1 · Blackwell-tuned (CUDA arch 120) |
| **Engine source HEAD** | `2bacb1eb` (2026-05-05) — binary not yet rebuilt against current source |

### Leaderboard (v3.5 — 323 effective units)

**Ranking: accuracy.** Tie-breakers: total pass count → hard-pass count → speed.
Tokens are tracked separately so a chatty path to the same answer is visible.
**Cost** is the API-equivalent if these prompts had been served by Anthropic
Sonnet 4.6 ($3/M input, $15/M output, no caching) — a sense-of-scale baseline,
not an actual outlay (these all ran locally on the 5090).

| # | Model | Acc | Speed | Pass | Hard | Tokens | Cost ≈ | Wall |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | **Qwen 27B Q5_K_XL** | **97.85** | 53.74 | **301/323** | **114/120** | 17.24 M | $70.27 | 8h 50m |
| 2 | Qwen 27B Uncensored Q5_K_P | 96.16 | 57.29 | 298/323 | 110/120 | 17.97 M | $70.89 | 8h 24m |
| 3 | Qwen 35B-A3B MoE Q4_K_M | 93.74 | 74.30 | 278/323 | 97/120 | 26.70 M | $111.37 | 6h 53m |
| 4 | Gemma 4 31B-it Q5_K_XL | 92.39 | 41.58 | 288/323 | 104/120 | 12.52 M | $54.23 | 9h 53m |
| 5 | Qwen 35B-A3B Uncensored Q4_K_M | 90.33 | 79.92 | 271/323 | 93/120 | 26.95 M | $107.12 | 5h 44m |

**Sweep total** (5 models): 92.23 M prompt + 9.15 M completion = **101.38 M tokens**, ≈ **$413.88** API-equivalent, **39h 46m** GPU wall-time.

### Difficulty breakdown

| Model | Easy (80) | Medium (123) | Hard (120) |
|---|---:|---:|---:|
| Qwen 27B Q5_K_XL | 73/80 | 114/123 | 114/120 |
| Qwen 27B Uncensored Q5_K_P | 75/80 | 113/123 | 110/120 |
| Qwen 35B-A3B MoE Q4_K_M | 70/80 | 111/123 | 97/120 |
| Qwen 35B-A3B Uncensored Q4_K_M | 73/80 | 105/123 | 93/120 |

### Per-category accuracy (12 categories, accuracy %)

```
MODEL                          Algos&DS  Concur  Distrib  LLM/ML  MathFin  PerfHW   Phys  Prob&Stats  PureMath  SWE/DevOps  Security   DSP
Qwen 27B Q5_K_XL                  92.5   100.0    100.0   100.0    100.0    98.5  100.0       98.4      94.7       100.0      98.3  100.0
Qwen 27B Uncensored Q5_K_P        93.7   100.0     88.9    98.7     99.0    99.0   98.2       98.4      92.4       100.0      92.5  100.0
Qwen 35B-A3B MoE Q4_K_M           90.3   100.0    100.0    98.7    100.0    80.5   78.4       98.4      87.6       100.0     100.0  100.0
Qwen 35B-A3B Uncensored Q4_K_M    83.1    84.6     80.6    98.7     99.0    93.2   80.2       98.4      86.7       100.0      97.5  100.0
```

### Per-language accuracy (variant tasks, accuracy %)

```
MODEL                           python   c    cpp    go   rust   zig
Qwen 27B Q5_K_XL                  99.9  93.2  90.3  93.2  96.1  66.9
Qwen 27B Uncensored Q5_K_P        98.3  90.3  93.2  96.1  93.2  55.2
Qwen 35B-A3B MoE Q4_K_M           97.8  82.5  83.5  80.5  83.5  40.9
Qwen 35B-A3B Uncensored Q4_K_M    94.2  82.5  85.4  78.6  96.1  15.6
```

### Notable observations (v3.5 vs v1)

1. **Ranking flipped at the top.** Qwen **27B Q5_K_XL** is now #1 (97.85 %)
   — it was #3 (95.5 %) on the v1 144-task suite. The previous champion,
   35B-A3B Uncensored, fell from 97.3 % to **90.33 %** and is now last among
   the four completed models. Larger ≠ better on the harder variant-heavy
   suite.

2. **Zig is the differentiator.** Per-language gap on Zig variants is
   enormous: 27B Q5_K_XL hits 66.9 %, but 35B-A3B Uncensored manages only
   **15.6 %**. Zig was the v3.5 prerequisite (spec defect fixed pre-sweep);
   the language is now functioning as a strong discriminator.

3. **Speed/accuracy tradeoff is real and inverted from the headline.** The
   MoE 35B variants are 30–50 % faster (Speed 74–80 vs 53–57) but 4–7
   accuracy points behind the 27B dense models. If you only cared about
   speed, the MoE is your pick; if accuracy is the bar, the smaller dense
   model wins.

4. **MoE chattiness costs ~50 % more tokens.** Both 35B-A3B runs spent
   ~26 M tokens vs ~17 M for the 27B runs — and produced worse accuracy,
   meaning the extra completion tokens are not buying correctness on this
   suite. Cost-equivalent for the MoEs is ~$110 vs ~$70 for the duals.

5. **Three categories still saturated** at 100 %: Concurrency & Systems
   (top 3 models), SWE/DevOps (all 4), Signal Processing & DSP (all 4 — but
   it's a 1-task category, not informative). Concurrency was de-saturated
   in v3 then re-saturated by the 27B duals — worth more concurrency
   hardening before the next sweep.

6. **27B Uncensored leads on easy tasks** (75/80 vs 73/80 for the
   accuracy-leader 27B Q5_K_XL) but loses 4 hard tasks vs Q5_K_XL — the
   uncensored fine-tune is slightly more confident on small problems and
   slightly less reliable on the hardest ones.

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
| Pass | 140 / 144 | **177 / 197** (89.8%) | — |
| Hard pass | 49 / 51 | **65 / 80** | — |
| Tokens | — (not tracked in v1) | **9.81M total** (8.79M prompt / 1.02M completion) | — |
| Cost-equivalent (Sonnet 4.6, $3/$15 per M) | — | **~$41.67** | — |

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
