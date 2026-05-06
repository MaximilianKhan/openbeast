# Eval suite

159 self-contained coding tasks for benchmarking local LLMs. Each task has a
deterministic validation script that returns exit 0 on success, non-zero on
failure. The harness runs the agent against every task, scores the result, and
ranks models in a leaderboard keyed by (host_id, model_slug).

```bash
python3 evals/run_eval.py --list                     # list every task
python3 evals/run_eval.py                            # run all 159 against the live server on :8080
python3 evals/run_eval.py --tasks 145,146            # run a subset
python3 evals/benchmark_all.py                       # full sweep across configured models
python3 evals/scoring.py --rebuild                   # rescore all eval-*.json files into leaderboard.json
python3 evals/scoring.py --by-category               # per-category drilldown table
python3 evals/scoring.py --by-language               # per-language drilldown (only meaningful once variants land)
python3 evals/scoring.py --compare-hosts             # side-by-side across machines
```

## Distribution

**Base totals:** 40 easy · 53 medium · 66 hard · 12 categories · 159 base task IDs

**Effective totals after variant rollout:** 197 test units (146 single-variant
legacy + 51 variant entries from 13 base tasks). Total weighted points are
preserved at 251.5 (a hard task with 4 variants scores 4 × 0.5 = 2.0 — same as
a hard single-variant task).

Difficulty weights (used in accuracy scoring): easy=1, medium=1.5, hard=2 — for
multi-variant tasks, divided by variant count.
Time budgets (used in speed scoring): easy=30s, medium=90s, hard=300s.

### Multi-language variants

13 of the 159 base tasks have Python / Go / C / C++ variants. Each variant is
its own scored test unit; the leaderboard reports per-language accuracy via
`scoring.py --by-language`.

| Task | # variants | Languages |
|---|---:|---|
| 19_three_way_quicksort | 4 | Py / Go / C / C++ |
| 31_is_power_of_two | 4 | Py / Go / C / C++ |
| 51_toposort | 4 | Py / Go / C / C++ |
| 52_unionfind | 4 | Py / Go / C / C++ |
| 61_extgcd | 4 | Py / Go / C / C++ |
| 65_miller_rabin | 4 | Py / Go / C / C++ |
| 73_count_vowels | 4 | Py / Go / C / C++ |
| 74_palindrome | 4 | Py / Go / C / C++ |
| 122_gemm_blocked | 3 | Go / C / C++ (perf-flavored — no Python) |
| 148_convex_hull | 4 | Py / Go / C / C++ |
| 155_tonelli_shanks | 4 | Py / Go / C / C++ |
| 158_karatsuba_bytes | 4 | Py / Go / C / C++ |
| 159_ntt_convolution | 4 | Py / Go / C / C++ |

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

#### Algorithms & DS (22)
- **Computational geometry** (1) — `148_convex_hull` *(hard)*
- **Graph algorithms** (3) — toposort, union-find, A* *(all medium)*
- **Hashing structures** (2) — LRU cache, bloom filter
- **Linear data structures** (3) — reverse list, find max, priority queue
- **Parsing** (1) — `14_expression_parser`
- **Recursion / interpretation** (2) — flatten JSON, brainfuck interpreter
- **Regex** (1) — `26_ipv4_regex` *(hard)*
- **Sorting** (1) — `19_three_way_quicksort`
- **String algorithms** (4) — count vowels, palindrome, **Aho-Corasick**, **suffix automaton**
- **Trees** (4) — BST, trie, **segment tree (lazy)**, **persistent BST**

#### Concurrency & Systems (16)
- **Async patterns** (4) — retry, debouncer, single-flight, **coroutine scheduler**
- **Concurrent data structures** (4) — threadsafe LRU, bounded queue, **MS-style FIFO**, **Chase-Lev deque**
- **Networking / state machines** (1) — TCP state machine
- **Race condition fixes** (1) — concurrent counter
- **Synchronization primitives** (4) — thread-safe counter, reentrant lock, **RW-lock writer-pref**, **lease mutex**
- **Systems APIs** (2) — env var, file SHA-256

#### Distributed / SysDesign (12)
- **Causal ordering** (1) — vector clock
- **Consistent hashing** (1)
- **Cryptographic primitives** (1) — SHA-256
- **Distributed coordination** (2) — distributed lock, two-phase commit
- **Encoding & serialization** (1) — base64
- **Identifiers** (1) — UUID4
- **Rate limiting** (2) — token bucket, sliding window
- **Replication & consistency** (1) — quorum KV (Dynamo-style)
- **Service patterns** (2) — circuit breaker, exponential backoff

#### LLM / ML (9)
- **Activations** (3) — softmax (stable), ReLU, sigmoid
- **Attention** (1) — scaled dot-product
- **Caching** (1) — KV cache *(hard)*
- **Norms** (1) — RMSNorm *(hard)*
- **Position embeddings** (1) — RoPE *(hard)*
- **Similarity metrics** (2) — cosine, Manhattan

#### Mathematical Finance (16)
- **Credit risk** (1) — CDS hazard rate
- **Exotic derivatives** (1) — Asian call (MC)
- **Fixed income** (2) — bond pricing, yield curve
- **Option pricing** (3) — Black-Scholes, binomial, American option
- **Risk metrics** (3) — min-variance portfolio, **VaR/CVaR**, Sharpe/Sortino *(all hard)*
- **Term structure models** (1) — Vasicek
- **Time value of money** (5) — compound interest, IRR, simple interest, FV annuity, APR/APY

#### Performance & HW Opt (11)
- **Asymptotic refactoring** (1) — quadratic → log-linear
- **Bit-twiddling** (3) — branchless min, popcount, XOR swap
- **Cache locality** (2) — blocked transpose, **blocked GEMM**
- **Loop optimization** (1) — Horner's method
- **RISC-V assembly** (3) — array sum, factorial, bit-reverse
- **Vectorization** (1) — SAXPY

#### Physics (12)
- **3D rotation / geometry** (1) — Rodrigues
- **Classical mechanics** (4) — projectile, damped oscillator, KE, Hooke's law
- **N-body / orbital mechanics** (1)
- **ODE numerical integration** (1) — RKF45
- **PDE / wave propagation** (1) — FDTD wave
- **Quantum mechanics** (1) — superposition
- **Solid-state physics** (1) — vdW ferroelectric
- **Thermodynamics** (2) — temperature conversion, Wien's law

#### Probability & Stats (10)
- **Combinatorial probability** (1) — birthday paradox
- **Descriptive statistics** (3) — z-score, descriptive, sample variance
- **Inference** (1) — Bayesian A/B
- **Monte Carlo simulation** (1) — π estimation
- **Optimal stopping** (1) — secretary problem
- **Psychometrics** (2) — Cronbach's α, d'
- **Stochastic processes** (1) — Markov steady-state

#### Pure & Abstract Math (22)
- **Bit operations** (1) — power-of-two detection
- **Iterative numerical methods** (3) — Newton-Raphson, iterative refinement, conjugate gradient
- **Linear algebra** (5) — dot product, det, power iteration, SVD, **2D Gauss reduction**
- **Number theory** (8) — extgcd, CRT, Miller-Rabin, factorial, GF(2⁸), Pollard rho, BSGS, **Tonelli-Shanks**
- **Polynomial algebra** (5) — FFT, Durand-Kerner, **Berlekamp-Massey**, **Karatsuba (bytes)**, **NTT**

#### SWE / DevOps (15)
- **APIs / web** (2) — todo endpoint, API versioning
- **Bash / scripting** (2) — bash script, deploy with rollback
- **CLI tools** (1)
- **Data engineering** (3) — transform, CSV→SQLite, schema migration
- **Debugging** (3) — grep & fix, runtime error, memory leak
- **File operations** (2) — create, edit
- **Refactoring** (1) — multi-file refactor
- **Testing** (1) — write tests

#### Security (13)
- **Auth & access control** (1) — login rate limit
- **Cipher implementation** (3) — AES key schedule, RSA, UOV
- **Cryptographic primitives** (3) — PBKDF2, HMAC verify (timing-safe), HMAC from scratch
- **Input validation** (1) — email regex
- **Side-channel safety** (1) — constant-time compare
- **Token management** (3) — secrets token, JWT, password reset
- **Vulnerability remediation** (1) — SQL injection patches

#### Signal Processing & DSP (1)
- **Frequency-domain analysis** (1) — FFT-based analysis

> **Bold** = added in the post-2026-05-06 hardening pass (tasks 145–159).

## Task JSON schema

Single-variant (legacy form, used by 144 of 159 tasks):

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
num_variants`. Hard task with 4 variants → each is worth 0.5 (= 2.0 / 4). 3/4
passing → 1.5 earned.

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

## Scoring

```
weight(task)   = DIFFICULTY_WEIGHTS[diff] / max(1, variant_count)
accuracy       = 100 × Σ(weight × passed) / Σ(weight)
speed_factor   = max(0, 1 − elapsed / time_budget)   # per passed task
speed          = 100 × mean(speed_factor)            # informational
composite      = 0.75 × accuracy + 0.25 × speed      # informational
```

Ranking is by **accuracy** first, then total pass count, then hard pass count,
then speed. Tokens are tracked but not part of the rank — they're a separate
column in the leaderboard so you can see how chatty a model is on the way to
the same answer.

## Result file format

Each run produces `evals/results/eval-{model_slug}-{timestamp}.json` with:

```json
{
  "timestamp": "...",
  "model": "...", "model_slug": "...", "base_url": "...",
  "gpu": {"name": "...", "host_id": "...", "gpu_count": 1, "gpus": [...]},
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
  "summary": {"total": 159, "passed": 152, "failed": 7}
}
```

`base_id`, `variant_id`, `language`, `variant_count` are present only for
flattened variant entries. Token fields are 0 if the model server didn't
return a `usage` block (some legacy llama.cpp builds).

## Adding a task

1. Pick the next free integer id (currently 160+).
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
