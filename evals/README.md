# Eval suite

> **Suite version: v4 (current).** 137 base tasks / 291 effective units,
> hardened so a correct solution passes and every documented cheat is
> empirically rejected — see [`CHANGELOG.md`](CHANGELOG.md),
> [`../docs/EVAL_V4_PLAN.md`](../docs/EVAL_V4_PLAN.md), and the review that
> drove it, [`../docs/EVAL_REVIEW_2026-07-07.md`](../docs/EVAL_REVIEW_2026-07-07.md).
> The distribution tables below describe **v4**. For the model **leaderboard**
> (four models on v4 — three MTP builds + the dense Qwen 27B Q5_K_XL — and four
> non-MTP models still on legacy v3.5, pending the in-progress re-run), see the
> [main README](../README.md) and [`../docs/RESULTS.md`](../docs/RESULTS.md);
> v3.5 and v4 scores are **not directly comparable** (different task sets).

137 self-contained coding tasks (291 effective units with multi-language variants) for benchmarking local LLMs. Each task has a
deterministic validation script that returns exit 0 on success, non-zero on
failure. The harness runs the agent against every task, scores the result, and
ranks models in a leaderboard keyed by (host_id, model_slug).

```bash
python3 evals/run_eval.py --list                     # list every task
python3 evals/run_eval.py                            # run all tasks against the live server on :8080
python3 evals/run_eval.py --tasks 145,146            # run a subset
python3 evals/run_eval.py --no-cache                 # disable result cache (force live runs)
python3 evals/run_eval.py --cache-only --model-name MODEL  # replay cache only; cache misses → 'skipped_cache_miss'
python3 evals/benchmark_all.py                       # full sweep across configured models
python3 evals/benchmark_all.py --cache-only          # rebuild leaderboard from cache without ever starting a server
python3 evals/scoring.py --rebuild                   # rescore all eval-*.json files into leaderboard.json
python3 evals/scoring.py --by-category               # per-category drilldown table
python3 evals/scoring.py --by-language               # per-language drilldown
python3 evals/scoring.py --compare-hosts             # side-by-side across machines
python3 evals/cache_cli.py stats                     # cache: entries, total size, context hash
python3 evals/cache_cli.py list --model SLUG         # list cached entries for one model
python3 evals/cache_cli.py clear --model SLUG        # invalidate one model's cached results
python3 evals/tool_efficiency.py                     # per-model tool-use metrics from agents/logs
python3 evals/tool_efficiency.py --since 2026-05-07  # only logs since this date
python3 evals/tool_efficiency.py --model SLUG        # drill into one model's tool-call frequencies
```

## Result cache (durable, file-backed)

Reruns are slow when most of the suite hasn't changed. The cache short-
circuits any (model, task, agent_context) tuple whose inputs match a
prior live run. See `evals/cache.py` for the hash-key construction;
durability verified by `tests/test_cache.py` (cross-process round-trip,
atomic writes, corrupt-file safety).

Invalidates automatically when (a) any task spec changes (setup, task,
validation, cleanup, max_iter — keys starting with `_`, like the
runtime-injected `_path`, are stripped before hashing, so relocating the repo
does NOT bust the cache), (b) the model changes, (c) the **effective iteration
budget** changes (a `--max-iter 5` smoke run is a different experiment from the
default), (d) the eval **suite version** bumps (`evals/SUITE_VERSION`), or
(e) the agent runtime context changes — `system-prompt.md`,
`system-prompt-tools.md`, `opencode.json`, or the agent runtime itself
(`agents/runner.py`, `agents/tools.py`: a change to the loop or a tool's
behavior changes what a "cached result" means). Timeouts (`agent_exit_code
== -1`) are NOT cached: those are environmental, not deterministic.

`--cache-only` mode is the fast-path for "rebuild the leaderboard from
prior runs" — no server start, no live calls, cache misses are recorded
as `skipped_cache_miss` for visibility.

Cache files: `evals/cache/{model_slug}.{task_id}.{spec_hash}[.mi{N}].{ctx_hash}.json`
(the `.mi{N}` segment carries the effective max-iter when a run threads one).
Tiny (<1 KB each), atomically written via `tmp+rename`, gitignored.

## Tool-selection efficiency (per-model)

`evals/tool_efficiency.py` reads `agents/logs/agent-*.jsonl` and reports
how each model uses its tools. The leaderboard ranks capability (problem-
solving + language breadth); this view ranks *how* the model gets there. Same data the agent already
writes, no new measurement needed.

| Metric | What it means |
|---|---|
| `iters_avg` | Mean iteration count per task. Lower = better planning. |
| `tools/T` | Mean tool invocations per task. Higher might indicate thrashing. |
| `edit:wr` | `edit_file:write_file` ratio. Higher = more targeted; <1 = wasteful overwrites. |
| `bash/T` | Bash calls per task. Lower = more targeted use of dedicated tools. |
| `rd_dup` | Per-log average of (`read_file` calls / unique paths read). 1.0 = no rereads within a task; >2.0 = thrashing. |
| `agent` | `start_agent` / `start_skill_agent` / `skill` calls (plus legacy `list_skills`/`load_skill` in pre-2026-07-08 logs). |

Aggregates across every `agents/logs/agent-*.jsonl` (top-level + sub-agent
runs). The model identifier is whatever the `start` record stored —
usually the llama-server `-a` alias (e.g. `qwen-27b-q5`), distinct from
the longer leaderboard `model_name` used elsewhere. For a multi-model
sweep, each model's logs separate naturally so the table becomes a real
cross-model comparison.

## Distribution

**Base totals (v4):** 24 easy · 58 medium · 55 hard = **137 base task IDs**
across 12 categories. Flattening multi-language variants gives **291 effective
test units**: 106 single-variant tasks + 185 variant units from 31 variant
base tasks.

Difficulty weights (used in accuracy scoring): easy=1, medium=1.5, hard=2 — for
multi-variant tasks, divided by variant count. Total weighted points are
preserved (a hard task with 6 variants scores 6 × (2.0/6) = 2.0 — same as a
hard single-variant task).
Time budgets (used in speed scoring): easy=30s, medium=90s, hard=300s.

### Multi-language variants

**31 of the 137 base tasks** have language variants (v4). Each variant is its
own scored test unit; the leaderboard reports per-language accuracy via
`scoring.py --by-language`.

**Supported languages:** Python (`a`), Go (`b`), C (`c`), C++ (`d`),
Rust (`e`), Zig (`f`). Full 6-language coverage on 30 of the 31 variant tasks;
`122_gemm_blocked` is perf-flavored with no Python variant (Go / C / C++ /
Rust / Zig only).

**Variant ID convention:** stable letter suffix per language so adding new
languages doesn't renumber existing variants.

| Tier | Variant base tasks |
|---|---|
| Easy (8) | 31_is_power_of_two · 32_dot_product · 71_reverse_list · 73_count_vowels · 74_palindrome · 82_sigmoid · 92_popcount · 100_constant_time_compare |
| Medium (13) | 11_bst · 19_three_way_quicksort · 20_priority_queue · 36_black_scholes · 38_monte_carlo_pi · 51_toposort · 52_unionfind · 54_astar · 61_extgcd · 62_crt · 63_det · 65_miller_rabin · 136_gf256 |
| Hard (10) | 27_brainfuck_interpreter · 115_fft · 122_gemm_blocked *(5-lang)* · 123_nbody · 127_aes_keysched · 137_pollard_rho · 148_convex_hull · 155_tonelli_shanks · 158_karatsuba_bytes · 159_ntt_convolution |

All variant tasks are audited end-to-end via `python3 tests/audit_variants.py`;
reference impls live in `evals/refs/`.

**Build commands per language:**

| Lang | Build | Run |
|---|---|---|
| python | (none) | `python3 sol.py < input.txt` |
| go | `go build -o sol sol.go` | `./sol < input.txt` |
| c | `gcc -O2 -std=c11 -Wall -Wextra -o sol sol.c` | `./sol < input.txt` |
| cpp | `g++ -O2 -std=c++17 -Wall -Wextra -o sol sol.cpp` | `./sol < input.txt` |
| rust | `rustc -O sol.rs -o sol` | `./sol < input.txt` |
| zig | `zig build-exe -O ReleaseFast sol.zig` | `./sol < input.txt` |

> **Zig 0.16 idioms** for variant authors:
> - Entrypoint: `pub fn main(init: std.process.Init) !void`
> - I/O: `std.Io.File.stdin()` / `std.Io.File.stdout()` (the old `std.io`
>   namespace is gone)
> - Reading: `takeDelimiter('\n')` returns `?[]u8` (null at EOF), or use
>   `appendRemainingUnlimited(arena, &list)` to slurp all of stdin
> - Writing: write to the writer's interface, then `try sout.flush()`
> - Compile time: ~7-8s per file (cold). Acceptable for the audit; counts
>   in elapsed time during model runs.

### Category × difficulty (v4)

| Category | Easy | Medium | Hard | Total |
|---|---:|---:|---:|---:|
| Algorithms & DS | 3 | 11 | 7 | **21** |
| Concurrency & Systems | 1 | 5 | 7 | **13** |
| Distributed / SysDesign | 3 | 4 | 4 | **11** |
| LLM / ML | 3 | 2 | 2 | **7** |
| Mathematical Finance | 2 | 5 | 6 | **13** |
| Performance & HW Opt | 2 | 2 | 2 | **6** |
| Physics | 1 | 3 | 5 | **9** |
| Probability & Stats | 1 | 4 | 3 | **8** |
| Pure & Abstract Math | 2 | 7 | 11 | **20** |
| SWE / DevOps | 3 | 9 | 2 | **14** |
| Security | 3 | 3 | 5 | **11** |
| Signal Processing & DSP | 0 | 3 | 1 | **4** |
| **Totals** | **24** | **58** | **55** | **137** |

### Subcategory drilldown (v4)

Task slugs below are the id stems under `evals/tasks/` (integer prefix orders
execution). This section is generated from the task files — the authoritative
per-category counts also come from `scoring.py --by-category` after a run.

#### Algorithms & DS (21)
- **Computational geometry** (1) — convex_hull
- **Graph algorithms** (3) — toposort, unionfind, astar
- **Hashing structures** (2) — lru_cache, bloom
- **Linear data structures** (2) — priority_queue, reverse_list
- **Parsing** (1) — expression_parser
- **Recursion / interpretation** (2) — flatten_json, brainfuck_interpreter
- **Regex** (1) — ipv4_regex
- **Sorting** (1) — three_way_quicksort
- **String algorithms** (4) — count_vowels, palindrome, aho_corasick, suffix_automaton
- **Trees** (4) — bst, trie_autocomplete, segment_tree_lazy, persistent_bst

#### Concurrency & Systems (13)
- **Async patterns** (4) — retry, debouncer, singleflight, coroutine_scheduler
- **Concurrent data structures** (4) — threadsafe_lru, bounded_queue, concurrent_queue, chase_lev_deque
- **Networking / state machines** (1) — tcp_state_machine
- **Race condition fixes** (1) — race_condition
- **Synchronization primitives** (3) — reentrant_lock, rwlock_writer_pref, lease_mutex

#### Distributed / SysDesign (11)
- **Causal ordering** (1) — vector_clock
- **Consistent hashing** (1) — consistent_hash
- **Cryptographic primitives** (1) — sha256
- **Distributed coordination** (2) — distributed_lock, 2pc
- **Encoding & serialization** (1) — base64
- **Rate limiting** (2) — rate_limiter, sliding_window
- **Replication & consistency** (1) — quorum_kv
- **Service patterns** (2) — circuit_breaker, backoff

#### LLM / ML (7)
- **Activations** (2) — softmax_stable, sigmoid
- **Attention** (1) — attention
- **Caching** (1) — kv_cache
- **Norms** (1) — rmsnorm
- **Position embeddings** (1) — rope
- **Similarity metrics** (1) — cosine_similarity

#### Mathematical Finance (13)
- **Credit risk** (1) — cds_hazard
- **Exotic derivatives** (1) — asian_call
- **Fixed income** (2) — bond, yield_curve
- **Option pricing** (3) — black_scholes, binomial, american_option
- **Risk metrics** (3) — min_variance_portfolio, value_at_risk, sharpe_sortino
- **Term structure models** (1) — vasicek
- **Time value of money** (2) — compound_interest, irr

#### Performance & HW Opt (6)
- **Asymptotic refactoring** (1) — optimize_quadratic
- **Bit-twiddling** (1) — popcount
- **Cache locality** (1) — gemm_blocked
- **Loop optimization** (1) — horner
- **RISC-V assembly** (2) — riscv_array_sum, riscv_bitrev

#### Physics (9)
- **3D rotation / geometry** (1) — rodrigues
- **Classical mechanics** (2) — projectile, damped
- **N-body / orbital mechanics** (1) — nbody
- **ODE numerical integration** (1) — rkf45
- **PDE / wave propagation** (1) — wave_fdtd
- **Quantum mechanics** (1) — quantum_superposition
- **Solid-state physics** (1) — vdw_ferroelectric
- **Thermodynamics** (1) — wien

#### Probability & Stats (8)
- **Combinatorial probability** (1) — birthday_paradox
- **Descriptive statistics** (1) — descriptive_stats
- **Inference** (1) — bayesian_ab
- **Monte Carlo simulation** (1) — monte_carlo_pi
- **Optimal stopping** (1) — secretary_problem
- **Psychometrics** (2) — cronbach, dprime
- **Stochastic processes** (1) — markov_steady_state

#### Pure & Abstract Math (20)
- **Bit operations (math)** (1) — is_power_of_two
- **Iterative numerical methods** (2) — iter_refinement, newton_raphson
- **Linear algebra** (5) — dot_product, det, power_iter, svd, gauss_reduction
- **Number theory** (7) — extgcd, crt, miller_rabin, gf256, pollard_rho, bsgs, tonelli_shanks
- **Polynomial algebra** (5) — fft, durand_kerner, berlekamp_massey, karatsuba_bytes, ntt_convolution

#### SWE / DevOps (14)
- **APIs / web** (1) — api_endpoint
- **Bash / scripting** (2) — bash_script, deploy_rollback
- **CLI tools** (1) — cli_tool
- **Data engineering** (3) — data_transform, csv_to_sqlite, db_migration
- **Debugging** (3) — grep_and_fix, debug_runtime_error, memory_leak
- **File operations** (2) — create_file, edit_file
- **Refactoring** (1) — multi_file_refactor
- **Testing** (1) — write_tests

#### Security (11)
- **Auth & access control** (1) — login_ratelimit
- **Cipher implementation** (3) — aes_keysched, rsa, uov
- **Cryptographic primitives** (3) — pbkdf2, hmac_verify, hmac_scratch
- **Input validation** (1) — email_regex
- **Side-channel safety** (1) — constant_time_compare
- **Token management** (1) — jwt
- **Vulnerability remediation** (1) — sql_injection

#### Signal Processing & DSP (4)
- **FIR filter design** (1) — fir_lowpass
- **Frequency-domain analysis** (1) — dsp_freq_analysis
- **IIR / recursive filters** (1) — biquad_iir
- **Tone detection (Goertzel)** (1) — goertzel

## Task JSON schema

Single-variant (legacy form, used by 106 of the 137 v4 tasks):

```json
{
  "id": "145_segment_tree_lazy",
  "name": "Segment tree with lazy propagation",
  "difficulty": "hard",
  "category": "Algorithms & DS",
  "subcategory": "Trees",
  "max_iter": 30,
  "setup": "mkdir -p /tmp/eval_segtree",
  "task": "Create /tmp/eval_segtree/seg.py with class SegTree …",
  "validation": {"type": "python", "script": "import importlib.util; …; print('OK')"},
  "cleanup": "rm -rf /tmp/eval_segtree",
  "pre_validate": "..."
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Filename stem; integer prefix orders execution. |
| `name` | yes | Human-readable. |
| `difficulty` | yes | `easy` / `medium` / `hard`. Drives weight + time budget. |
| `category` / `subcategory` | yes | Used by `--by-category`. |
| `max_iter` | yes | Per-task agent iteration cap. Default fallback is 15. |
| `setup` | optional | Shell command run BEFORE the agent. Idempotent fixture creation. |
| `task` | yes | Prompt sent to the agent. |
| `validation` | yes | `type` is `python` or `bash`; `script` returns exit 0 on success. |
| `cleanup` | yes | Shell command run AFTER validation. Removes fixtures. |
| `pre_validate` | optional | Shell command run between agent and validation. **Re-asserts harness fixtures** that the agent may have corrupted during testing. Used by `17_deploy_rollback`. |

### Multi-language variants (Phase 3 schema, Phase 4 rollout)

A task may declare a `variants` array. Each variant becomes its own scored
entry with effective id `{base_id}_{variant_id}`, weight = `difficulty_weight /
num_variants`. Hard task with 6 variants → each is worth ~0.333 (= 2.0 / 6).
5/6 passing → 1.667 earned.

```json
{
  "id": "145_segment_tree_lazy",
  "name": "Segment tree with lazy propagation",
  "difficulty": "hard",
  "category": "Algorithms & DS",
  "subcategory": "Trees",
  "max_iter": 30,
  "variants": [
    {"id": "a", "language": "python", "setup": "...", "task": "Create /tmp/eval_segtree/seg.py …", "validation": {...}, "cleanup": "..."},
    {"id": "b", "language": "go",     "setup": "...", "task": "Create /tmp/eval_segtree/seg.go …", "validation": {...}, "cleanup": "..."},
    {"id": "c", "language": "c",      "setup": "...", "task": "...", "validation": {...}, "cleanup": "..."},
    {"id": "d", "language": "cpp",    "setup": "...", "task": "...", "validation": {...}, "cleanup": "..."}
  ]
}
```

Variant fields override the base. Filtering by base id selects all variants;
filtering by effective id selects one.

## Scoring (v2 — capability, since 2026-07-10)

The ranking metric is **CAPABILITY**, a problem-solving-led split of the old
fused accuracy. Base problems are grouped by `base_id`; a base problem of
difficulty weight `d` with `k` language variants of which `p` passed contributes:

```
# per BASE problem (d = DIFFICULTY_WEIGHTS[diff], k = variant_count, p = passed langs)
problem_solving  = 100 × Σ d·[p ≥ 1]      / Σ d               # solved in ≥1 language
language_breadth = 100 × Σ_{p≥1} d·(p/k)  / Σ_{p≥1} d         # ports passed, among solved
CAPABILITY       = 0.75 × problem_solving + 0.25 × language_breadth   # ← ranking key (SCORE)

# retained columns
accuracy    = 100 × Σ(d/k × passed) / Σ(d/k)   # legacy v1 metric (per-entry weighted pass rate)
spd (tok/s) = tokens_completion / elapsed_total_seconds   # effective decode throughput
speed       = 100 × mean(max(0, 1 − elapsed/time_budget)) over passed tasks  # legacy factor
```

**Why capability, not accuracy.** v1 required passing all 6 language ports for
full credit, so *solving* a problem in one language earned only 1/6 — fusing
problem-solving with language-porting and weighting them equally. **Porting a
working solution across languages is increasingly automatable** (LSP feedback,
MCP language servers, in-context translation), so v2 weights *solving* heavily
(0.75) and breadth lightly (0.25). Difficulty weights (1/1.5/2) and equal
language weighting are unchanged; singletons and full-6-language solves score
identically to v1 — only partial-language cases rebalance. v1 `accuracy` is kept
as a column. Full changelog in `scoring.py`'s module docstring; the switch is
narrated in [`docs/RESULTS.md`](../docs/RESULTS.md) "Scoring v2".

Ranking is by **capability** first, then problem_solving, then hard pass count,
then speed. Tokens and API-equivalent cost are tracked separately (not part of
the rank). Leaderboard readout columns: SOLVE / LANG / SCORE (all shown as %)
→ SPD (tok/s) → WALL → PASS. (The legacy v1 accuracy and per-tier pass rates
live in each entry's JSON `accuracy`/`breakdown` + `scoring.py --by-category`,
not the at-a-glance readout.)


## v4 leaderboard — per-language & aggregation analysis

*(Relocated from the primary README to keep it lean; full context for the capability leaderboard.)*

**v4 per-language accuracy** (difficulty-weighted % over the 31 variant tasks +
the Python-bucketed single-language tasks; same methodology as the v3.5 table
below, so the two are comparable. Bold = top, italic = floor per column):

| Model | Python | C | C++ | Go | Rust | Zig | Best at |
|---|---:|---:|---:|---:|---:|---:|---|
| Qwen 27B Q5_K_XL | **98.6** | 92.7 | _87.7_ | **97.9** | **100.0** | 60.5 | Python, Go, Rust |
| Qwen 27B MTP Q5_K_XL | 96.7 | **96.9** | **96.9** | 96.9 | 96.9 | **66.6** | C, C++, Zig |
| Qwen 35B-A3B MTP MoE Q4_K_M | 97.3 | _85.4_ | _87.7_ | _85.4_ | _95.8_ | _34.5_ | — |
| Qwopus 27B v2 MTP Q5_K_M | _95.2_ | _84.5_ | 91.9 | 96.9 | 96.9 | 44.7 | — |

Raw pass counts (difficulty-blind, `passed/count`) tell the plainer story:

| Model | Python | C | C++ | Go | Rust | Zig |
|---|---:|---:|---:|---:|---:|---:|
| Qwen 27B Q5_K_XL | 133/136 | 29/31 | 28/31 | 30/31 | **31/31** | 20/31 |
| Qwen 27B MTP Q5_K_XL | 133/136 | 30/31 | 30/31 | 30/31 | 30/31 | 20/31 |
| Qwen 35B-A3B MTP MoE Q4_K_M | 131/136 | 27/31 | 28/31 | 27/31 | 30/31 | 11/31 |
| Qwopus 27B v2 MTP Q5_K_M | 130/136 | 27/31 | 29/31 | 30/31 | 30/31 | 14/31 |

Three things fall out. **(1) Base ≈ MTP is a per-language dead heat** — identical
on Python (133/136) and Zig (20/31), never more than 2 units apart anywhere —
confirming MTP is lossless; the weighted table's cpp/rust swings are single-task
noise amplified by difficulty weighting. **(2) Zig is the discriminator.** Every
model clears 84–100 % on the five mainstream languages, so those columns barely
separate the field — but Zig fans out from **34.5 % to 66.6 %**.

**(3) ⚠️ The headline Acc is ~82 % a Python contest — mind the aggregation.** The
Python bucket (106 single-language tasks at full weight + Python's 1/6 share of
the 31 variants) carries **~82 % of the total weighted score**; the other five
languages *share the remaining ~18 %*. So the per-language table's six visually-
equal columns are nothing like equal in the leaderboard Acc, and **the table does
not proxy the ranking.** Concretely, for base vs. MTP the aggregations disagree:

| Aggregation | Base | MTP | Winner |
|---|---:|---:|:--|
| Overall Acc (leaderboard, Python-dominated) | 96.62 | 95.63 | Base +0.99 |
| Equal-language average (6 langs, equal) | 89.56 | 91.80 | **MTP +2.23** |
| Raw total pass | 271 | 273 | **MTP +2** |

Base holds #1 **only** on the Python-dominated overall metric — and there the two
*tie* on raw Python count (133/136 each); base merely cleared marginally harder
Python tasks. By the equal-language view (which is how we weight languages *within*
the per-language table) and by raw pass count, **MTP leads.** We are leaving the
ordering as-is for now, but the "base is #1" claim is an artifact of Python's
weight share, not a real quality edge — they are the same lossless weights.

The deeper lesson: difficulty re-weighting was tested (1-2-3) and is a **dead
lever** — every model's pass rate is flat across easy/medium/hard, so no ratio
reorders the board. Language variants are kept **equally weighted on purpose** to
show the true distribution of ability. The honest fix for the ~1-point spreads
here is **multi-run averaging with mean-variance** — a single run has real
run-to-run noise, and a 3× run would very likely put base and MTP inside each
other's error bars. That is acknowledged future work, not yet budgeted.

### v3.5 — per-language accuracy

**At-a-glance: per-language accuracy** (variant tasks, % accuracy; bold = top, italic = floor):

| Model | Python | C | C++ | Go | Rust | Zig | Best at |
|---|---:|---:|---:|---:|---:|---:|---|
| Qwen 27B Q5_K_XL | **99.9** | **93.2** | 90.3 | 93.2 | **96.1** | **66.9** | Python, C, Rust, Zig |
| Qwen 27B Uncensored Q5_K_P | 98.3 | 90.3 | **93.2** | **96.1** | 93.2 | 55.2 | C++, Go |
| Qwen 35B-A3B MoE Q4_K_M | 97.8 | _82.5_ | _83.5_ | 80.5 | _83.5_ | 40.9 | — |
| Gemma 4 31B-it Q5_K_XL | 94.3 | 88.3 | 86.4 | 94.2 | 91.2 | 54.5 | — |
| Qwen 35B-A3B Uncensored Q4_K_M | _94.2_ | _82.5_ | 85.4 | _78.6_ | **96.1** | _15.6_ | Rust |

The **Zig spread is enormous** (66.9 → 15.6) and the strongest discriminator on the suite. Python is saturated across the board, so pick a smaller, faster model if you only ship Python. **Use 27B Q5_K_XL for Python, C, and Zig**; **27B Uncensored for Go and C++**; the MoE variants are useful when raw speed matters more than top-end accuracy.

## How token tracking works

Tokens are recorded per task and summed per run. Three numbers per task:
`tokens_prompt`, `tokens_completion`, `tokens_total` (their sum). They come
from the standard OpenAI-compatible `usage` field that llama.cpp returns on
every API call. The agent runner accumulates them across iterations.

Understanding what each number means matters — especially for comparing
models, interpreting cost-equivalent estimates, and reasoning about thinking
behavior in Qwen-family models. The two numbers are not symmetrical and the
ratio is informative.

### Input (prompt_tokens) — everything the model PROCESSES on a single API call

For one API call, `prompt_tokens` is the total of:
- The system prompt (soul file + tool guidance) — a few thousand tokens
- The original user task
- *Every prior assistant message* (the conversation history)
- *Every prior tool call's arguments and result* — these dominate; a single
  `read_file` of a 500-line file is ~2-5K tokens
- Any chat-template scaffolding (turn markers, role tags)

### Output (completion_tokens) — everything the model GENERATES on that call

- The visible response text
- Any tool-call function arguments
- Any `<think>...</think>` reasoning block, if the model produced one (Qwen
  thinking-mode output is part of completion — see below)

### Why prompt >> completion in our totals (typically 13:1 or worse)

A multi-iteration agent **re-sends the entire conversation on every iteration**.
That means the prompt grows monotonically across iterations:

| Iteration | Prompt size (illustrative, 7-iter task) |
|---:|---:|
| 1 | ~5K (system + task) |
| 2 | ~10K (+ iter-1 stuff) |
| 3 | ~15K |
| ... | each round adds the prior round's output + tool results |
| 7 | ~30–50K |

Sum of all 7 prompts ≈ 160K. Sum of all 7 completions might be 5–10K. That's
where the 13:1 skew comes from — not from chatty output, but from monotonic
prompt accumulation. The KV cache makes this efficient at runtime (most of
the prefix is cached), but the API still counts every token in
`prompt_tokens` regardless of cache hits.

### Where thinking tokens land — and what Qwen does with them

Qwen3.6 (and Qwen3-A3B, both used in this stack) supports a thinking mode
where the model emits `<think>...</think>` blocks before its visible
response. Two things to know:

**1. Thinking tokens count toward `completion_tokens` of the iteration that
generated them.** They're produced just like any other output: GPU cycles
consumed, tokens generated, fully counted by the API. A task with
disproportionate completion tokens (10–15% of total instead of the typical
5–7%) is a signal the model thought a lot.

**2. Qwen's chat template STRIPS thinking from subsequent prompts.** This is
the critical design choice. Verified in this stack: the chat template
referenced by `/props` includes:

```
{%- if message.reasoning_content %}
{%- else %}
    {%- if '</think>' in content %}
       (strip block before reusing in next turn)
```

When iteration N's assistant message gets fed back into iteration N+1's
prompt, the `<think>` block is removed first. Confirmed in our agent logs —
assistant messages stored in JSONL contain only the visible response, no
`<think>` tags.

**Why this matters:** if thinking persisted into the next prompt, a 7-turn
conversation could compound 5–20× more context. Qwen's template handles this
correctly out of the box, which is why our prompt growth is "merely linear"
across iterations rather than exponential.

```
                                Counted in:
─────────────────────────────────────────────────────────
<think> output during iter N    →  completion_tokens(iter N)
visible response during iter N  →  completion_tokens(iter N)
tool call args during iter N    →  completion_tokens(iter N)
                                       │
                                       ▼
                In iter N+1's prompt:
                  <think>           →  STRIPPED, not in prompt_tokens
                  visible response  →  KEPT, in prompt_tokens
                  tool call args    →  KEPT
                  tool result       →  KEPT
```

So thinking is a per-iteration cost only. It doesn't echo forward.

### The cost-equivalent estimate caveat

When the leaderboard or a post-mortem quotes "$X of equivalent Sonnet/Opus
spend," that's `prompt × $input_rate + completion × $output_rate` — useful
for ranking but **not literally what you'd pay a frontier API**. Two reasons:

1. **Frontier APIs bill cached prefix at ~10% of fresh input rate.** Our
   `prompt_tokens` lumps fresh and cached together (llama.cpp's `usage`
   doesn't separate them). Most of our prompt is cache hits — sometimes 90%+
   of the total. A frontier API would charge that 90% at the discounted
   rate.

2. **Thinking tokens are billed differently across providers.** Anthropic
   counts them as output (same as us). Some other providers separate
   reasoning from completion in their billing. Worth checking the specific
   provider's policy if you're translating these numbers for a real
   migration estimate.

For comparing models *within our local stack* — which is the primary use of
the TOKENS column — these caveats don't matter. The accounting is consistent
across all models we benchmark, so the ranking is honest.

For external comparison, OpenCode's `opencode stats` command separates
fresh-input from cache-read explicitly and is the better source for accurate
cost translation.

### Summary

| What | Counts toward | Persists into next prompt? |
|---|---|---|
| System prompt processing | `prompt_tokens` (every iter) | Yes (cache-hit) |
| Prior assistant visible text | `prompt_tokens` (next iter) | Yes |
| Prior `<think>` blocks | `prompt_tokens` (next iter) | **No** — Qwen template strips |
| Prior tool call args + results | `prompt_tokens` (next iter) | Yes (often the biggest source) |
| Current iter's visible response | `completion_tokens` | (becomes input next iter) |
| Current iter's `<think>` block | `completion_tokens` | (stripped before next iter) |
| Current iter's tool call args | `completion_tokens` | (becomes input next iter) |

## Result file format

Each run produces `evals/results/eval-{model_slug}-{timestamp}.json` with:

```json
{
  "timestamp": "...",
  "model": "...", "model_slug": "...", "base_url": "...",
  "suite_version": "v4",
  "gpu": {"name": "...", "host_id": "...", "gpu_count": 1, "gpus": [...]},
  "inference_engine": {"name": "llama.cpp", "build": "...", "commit": "...", "compiler": "...", "source_head": "..."},
  "runtime": {"python": "3.13.x", "platform": "...", "openai": "...", "mcp": "..."},
  "tasks": [
    {
      "id": "145_segment_tree_lazy",
      "name": "...", "difficulty": "hard",
      "model": "...", "passed": true,
      "elapsed_seconds": 27.3,
      "agent_exit_code": 0,
      "validation_output": "OK",
      "tokens_prompt": 18432,
      "tokens_completion": 2104,
      "tokens_total": 20536,
      "base_id": "145_segment_tree_lazy",
      "variant_id": "a",
      "language": "python",
      "variant_count": 4
    }
  ],
  "summary": {"total": 291, "passed": 273, "failed": 18}
}
```

`base_id`, `variant_id`, `language`, `variant_count` are present only for
flattened variant entries. Token fields are 0 if the model server didn't
return a `usage` block (some legacy llama.cpp builds). `suite_version`,
`inference_engine`, and `runtime` are the provenance stamped on every row so
v3.5 and v4 scores never get confused and a result is traceable to the exact
llama.cpp build that produced it. Results are written incrementally
(`tmp`+`rename` after each task), so a crashed sweep keeps every completed
task. `--cache-only` runs record `gpu`/`inference_engine` as `{}` (no live
probe).

## Adding a task

1. Pick the next free integer id (currently 163+).
2. Create `evals/tasks/{id}_{slug}.json` following the schema above.
3. Solve the task yourself with a reference implementation. Run the validation
   against your reference. Confirm it passes — and confirm an OBVIOUSLY WRONG
   implementation fails (no false positives).
4. Bash-syntax check happens automatically: `tests/test_scripts.sh` parses
   every task JSON and validates Python validation scripts via `compile()`.
5. If your task seeds buggy code that the agent must fix in place (like
   `06_debug_runtime_error`), do NOT add `pre_validate` — re-running setup
   would clobber the agent's fix.
6. If your setup writes harness fixtures the agent must NOT modify (like
   `17_deploy_rollback`'s `healthcheck.sh`), DO add `pre_validate` re-asserting
   only those fixtures.

## Pitfalls (from real spec defects)

These are the lessons from the 2026-05-06 sweep post-mortem. Read them before
writing a task spec.

1. **Don't substring-lint forbidden libraries.** `assert 'numpy' not in src`
   trips on docstrings that cite the library by name. Use `assert 'import
   numpy' not in src and 'from numpy' not in src`.
2. **Specify input types explicitly.** `b64_encode(data)` with a `str` input
   passed by validation but a `bytes` parameter assumed by the model is a
   coin-flip. Annotate types in the spec.
3. **Specify return-value contracts.** If validation does `assert
   kv.write(...)`, the spec must say what `write` returns.
4. **Re-assert harness fixtures.** Agents have full bash and will overwrite
   fixture files during their own testing. Use `pre_validate` to restore them.
5. **Calibrate perf gates against the slowest valid impl.** A correct linked
   list passes at 50k items in 80ms; `list.pop(0)` at the same size takes 2.5s.
   That's a 30× gap and lets us write a tight `< 1.5s` assertion.
6. **Solve the task yourself.** Validation expectations (e.g. expected match
   list for Aho-Corasick) are easy to miscount by hand. Always run the actual
   algorithm before committing the test.
