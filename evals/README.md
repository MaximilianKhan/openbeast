# Eval suite

159 self-contained coding tasks for benchmarking local LLMs. Each task has a
deterministic validation script that returns exit 0 on success, non-zero on
failure. The harness runs the agent against every task, scores the result, and
ranks models in a leaderboard keyed by (host_id, model_slug).

```bash
python3 evals/run_eval.py --list                     # list every task
python3 evals/run_eval.py                            # run all 159 against the live server on :8080
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
validation, cleanup, max_iter), (b) the model changes, or (c) the agent
runtime context changes — namely `system-prompt.md`,
`system-prompt-tools.md`, or `opencode.json`. Timeouts (`agent_exit_code
== -1`) are NOT cached: those are environmental, not deterministic.

`--cache-only` mode is the fast-path for "rebuild the leaderboard from
prior runs" — no server start, no live calls, cache misses are recorded
as `skipped_cache_miss` for visibility.

Cache files: `evals/cache/{model_slug}.{task_id}.{spec_hash}.{ctx_hash}.json`.
Tiny (<1 KB each), atomically written via `tmp+rename`, gitignored.

## Tool-selection efficiency (per-model)

`evals/tool_efficiency.py` reads `agents/logs/agent-*.jsonl` and reports
how each model uses its tools. The leaderboard ranks accuracy + speed;
this view ranks *how* the model gets there. Same data the agent already
writes, no new measurement needed.

| Metric | What it means |
|---|---|
| `iters_avg` | Mean iteration count per task. Lower = better planning. |
| `tools/T` | Mean tool invocations per task. Higher might indicate thrashing. |
| `edit:wr` | `edit_file:write_file` ratio. Higher = more targeted; <1 = wasteful overwrites. |
| `bash/T` | Bash calls per task. Lower = more targeted use of dedicated tools. |
| `rd_dup` | `read_file` calls / unique paths read. 1.0 = no rereads; >3.0 = thrashing. |
| `agent` | `start_agent` / `start_skill_agent` / `list_skills` / `load_skill` calls. |

Aggregates across every `agents/logs/agent-*.jsonl` (top-level + sub-agent
runs). The model identifier is whatever the `start` record stored —
usually the llama-server `-a` alias (e.g. `qwen-27b-q5`), distinct from
the longer leaderboard `model_name` used elsewhere. For a multi-model
sweep, each model's logs separate naturally so the table becomes a real
cross-model comparison.

## Distribution

**Base totals:** 40 easy · 53 medium · 66 hard base tasks · 12 categories · 159 base task IDs

**Effective totals after v3.5 variant rollout (2026-05-07):** 323 test
units = 126 single-variant legacy + 197 variant entries across **33 base
tasks** with multi-language variants (py/go/c/cpp/rust/zig). Total weighted
points preserved (a hard task with 6 variants scores 6 × (2.0/6) = 2.0 —
same as a hard single-variant task).

Difficulty weights (used in accuracy scoring): easy=1, medium=1.5, hard=2 — for
multi-variant tasks, divided by variant count.
Time budgets (used in speed scoring): easy=30s, medium=90s, hard=300s.

### Multi-language variants

33 of the 159 base tasks have language variants. Each variant is its own
scored test unit; the leaderboard reports per-language accuracy via
`scoring.py --by-language`.

**Supported languages:** Python (`a`), Go (`b`), C (`c`), C++ (`d`),
Rust (`e`), Zig (`f`). Full 6-language coverage on most variant tasks.

**Variant ID convention:** stable letter suffix per language so adding new
languages doesn't renumber existing variants. (Exception: `122_gemm_blocked`
has no Python variant — perf-flavored task — so its IDs are a=Go, b=C,
c=C++, d=Rust, e=Zig.)

**Phase 4/4.5 rollout (2026-05-06) — 13 base tasks (77 variant entries):**

| Task | Variants | Languages |
|---|---:|---|
| 19_three_way_quicksort | 6 | Py / Go / C / C++ / Rust / Zig |
| 31_is_power_of_two | 6 | Py / Go / C / C++ / Rust / Zig |
| 51_toposort | 6 | Py / Go / C / C++ / Rust / Zig |
| 52_unionfind | 6 | Py / Go / C / C++ / Rust / Zig |
| 61_extgcd | 6 | Py / Go / C / C++ / Rust / Zig |
| 65_miller_rabin | 6 | Py / Go / C / C++ / Rust / Zig |
| 73_count_vowels | 6 | Py / Go / C / C++ / Rust / Zig |
| 74_palindrome | 6 | Py / Go / C / C++ / Rust / Zig |
| 122_gemm_blocked | 5 | Go / C / C++ / Rust / Zig (no Python — perf-flavored) |
| 148_convex_hull | 6 | Py / Go / C / C++ / Rust / Zig |
| 155_tonelli_shanks | 6 | Py / Go / C / C++ / Rust / Zig |
| 158_karatsuba_bytes | 6 | Py / Go / C / C++ / Rust / Zig |
| 159_ntt_convolution | 6 | Py / Go / C / C++ / Rust / Zig |

**Phase 5 rollout (v3.5, 2026-05-07) — 20 new variant base tasks (120 entries):**

| Tier | Tasks |
|---|---|
| Easy (5) | 32_dot_product · 71_reverse_list · 82_sigmoid · 92_popcount · 100_constant_time_compare |
| Medium (9) | 11_bst · 20_priority_queue · 54_astar · 38_monte_carlo_pi · 39_blocked_transpose · 36_black_scholes · 62_crt · 63_det · 136_gf256 |
| Hard (6) | 27_brainfuck_interpreter · 115_fft · 123_nbody · 127_aes_keysched · 47_branchless_min · 137_pollard_rho |

All 20 new tasks: full 6-lang coverage, 120/120 audited end-to-end via
`python3 tests/audit_variants.py`. Reference impls in `evals/refs/`.

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

## Scoring

```
weight(task)   = DIFFICULTY_WEIGHTS[diff] / max(1, variant_count)
accuracy       = 100 × Σ(weight × passed) / Σ(weight)
speed_factor   = max(0, 1 − elapsed / time_budget)   # per passed task
speed          = 100 × mean(speed_factor)            # separate signal, not folded in
```

Ranking is by **accuracy** first, then total pass count, then hard pass count,
then speed. Tokens (prompt + completion) and API-equivalent cost are tracked
separately so a chatty path to the same answer is visible — they are not part
of the rank. There is intentionally **no composite score**: speed and accuracy
trade off in opposite directions on this suite (the MoE 35B variants are
faster but trail the dense 27B models on accuracy), and a weighted average
hides that signal.

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
