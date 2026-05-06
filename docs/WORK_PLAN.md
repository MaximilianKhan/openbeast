# Eval suite v3 — fixes, hardening, and multi-language variants

**Status (2026-05-06):**
- ✅ Phase 1 — 4 spec/harness fixes landed and verified
- ✅ Phase 2 — 15 hardening tasks (145–159) landed and verified, plus cheat-resistance perf gates on 150/152
- ✅ Phase 3 — multi-language variant architecture in `run_eval.py` + `scoring.py`; backward-compat regression bit-identical
- ✅ Token tracking through the eval pipeline; `evals/README.md` distribution doc
- ✅ Phase 4 (substantial) — 13 tasks variant'd (51 variant entries) end-to-end verified
- ⏳ Phase 4 deferred — 5 tasks remain (53_bloom, 145, 146, 152, 153) — see deferred list at bottom
- ⏳ Validation sweep on winning model
- ⏳ Full 5-model sweep with the v3 suite

This document is the save state. A fresh session should be able to execute the
remaining work from this file alone.

**Phase 4 actual delivery (51 variant entries across 13 tasks):**

| Task | # variants | Languages |
|---|---:|---|
| 31_is_power_of_two | 4 | Py / Go / C / C++ |
| 73_count_vowels | 4 | Py / Go / C / C++ |
| 74_palindrome | 4 | Py / Go / C / C++ |
| 19_three_way_quicksort | 4 | Py / Go / C / C++ |
| 51_toposort | 4 | Py / Go / C / C++ |
| 52_unionfind | 4 | Py / Go / C / C++ |
| 61_extgcd | 4 | Py / Go / C / C++ |
| 65_miller_rabin | 4 | Py / Go / C / C++ |
| 122_gemm_blocked | 3 | Go / C / C++ (perf-flavored — no Python) |
| 148_convex_hull | 4 | Py / Go / C / C++ |
| 155_tonelli_shanks | 4 | Py / Go / C / C++ |
| 158_karatsuba_bytes | 4 | Py / Go / C / C++ |
| 159_ntt_convolution | 4 | Py / Go / C / C++ |

Total: **51 variant entries** + 146 single-variant legacy tasks = **197 effective
test units** across 159 base task IDs. Total weighted points still equal sum of
base difficulty weights (251.5) — variant scoring math is invariant.

All 51 variants verified end-to-end with reference implementations passing the
bash-only validation pipeline (compile if needed → run with stdin → diff against
expected output OR run `check.py` for non-deterministic outputs).

**Mission summary.** The 5090 sweep finished cleanly but the post-mortem
surfaced four spec/harness defects (4 of the 5 "all-models-failed" tasks were
not actually that hard) and three categories saturated at 100% across all
models. We fixed the defects, added 15 new hard-tier tasks, built the
multi-language variant infrastructure, and populated 13 of the 18 originally
planned tasks. The remaining 5 are deferred (see end of doc).

**Toolchain available:**
- gcc 16.1.1
- g++ 16.1.1
- go 1.26.2
- rustc (present, not used for now)

---

## Phase 1 — Spec & harness fixes

These are the four defects from the sweep post-mortem. Each is small and
should be done first so the next sweep starts from a clean baseline.

### 1.1 `42_value_at_risk` — broken validation lint

**Defect.** Validation has `assert 'numpy' not in src and 'scipy' not in src`,
which is a substring lint that trips on docstrings/comments. The spec text
itself says "(numpy/scipy 'linear' convention)" as a hint, so models cite that
phrase verbatim. All 5 models had mathematically correct implementations and
all 5 failed the lint.

**Fix.** In `evals/tasks/42_value_at_risk.json`, replace:
```python
assert 'numpy' not in src and 'scipy' not in src
```
with:
```python
assert 'import numpy' not in src and 'import scipy' not in src and 'from numpy' not in src and 'from scipy' not in src
```

Drop the parenthetical from the spec text too (`numpy/scipy 'linear'` →
`'linear'-style`) so the hint is still useful but doesn't bait models into
writing the trigger string.

**Expected lift:** task flips from 0/5 → 5/5. Median accuracy +1.4 points.

### 1.2 `85_base64` — input-type ambiguity

**Defect.** `b64_encode(data)` doesn't say whether `data` is `str` or `bytes`.
The validation passes a `str`. Models that follow the standard base64
convention (assume `bytes`) get `TypeError`. 4/5 fail.

**Fix.** In `evals/tasks/85_base64.json` task field, change `b64_encode(data)`
to `b64_encode(data: str)` (or explicitly say "data is a UTF-8 string"). Keep
validation as-is — it already passes a str.

**Expected lift:** 1/5 → ~5/5 on this task.

### 1.3 `121_quorum_kv` — missing return-value contract

**Defect.** Validation has `assert kv.write(...)` (truthy on success) and
`assert not kv.write(...)` (falsy on quorum-fail). The spec doesn't say what
write returns, so models default to implicit `None` and fail the very first
assertion. The only model that passed (Qwen 27B Uncensored) returned `value`
on success.

**Fix.** Append to the task field: "`write` returns `True` on success and
`False` if the quorum is not met. `read` returns the value or `None`."

**Expected lift:** moderate — won't go to 5/5 because the LWW tuple-comparison
subtlety still exists, but should net 1-2 more passes.

### 1.4 Harness — re-assert fixtures before validation

**Defect.** Agents have full bash access. On `17_deploy_rollback`, 4/5 models
overwrote the harness's `healthcheck.sh` during their own testing (replacing
it with `exit 0`). Validation then ran against the broken healthcheck and
the test logic short-circuited. The agents' final `deploy.sh` was actually
correct.

**Fix.** In `evals/run_eval.py`, run `setup` a second time right before
`run_validation`. Cheap, idempotent for well-written setup scripts (all
existing setups are mkdir + write fixture files). Implementation:

```python
# In run_eval(), after agent finishes:
agent_result = run_agent(task, base_url, max_iter_override)
print(f"  Agent finished in {agent_result['elapsed_seconds']}s")

# RE-ASSERT FIXTURES: agents may have corrupted them.
# Setup is idempotent (mkdir -p + writing fixture files) but does NOT touch
# any *.py / *.sh / *.go / *.c / *.cpp the agent wrote — those live in the
# task subdir alongside the fixtures, but setup only writes specific named
# files, so agent code survives. Validate this assumption per-task as we add
# new tasks.
run_setup(task)

passed, validation_output = run_validation(task)
```

**Caveat:** every existing setup script was reviewed; none overwrites a file
the agent might write. (Setups write `healthcheck.sh`, fixture data files,
sample inputs — never the file the agent is asked to create.) For variant
tasks below, the same rule applies: setup writes fixtures only, never the
solution file.

**Expected lift:** `17_deploy_rollback` goes from 1/5 → ~5/5.

### 1.5 Combined Phase-1 impact

Estimated leaderboard accuracy bump after just these four fixes:

| Model | Current | Estimated post-fix |
|---|---:|---:|
| 35B-A3B Uncensored | 97.3 | ~98.6 |
| 27B Uncensored | 96.4 | ~97.7 |
| 27B Q5_K_XL | 95.5 | ~96.8 |
| Gemma 31B-it | 94.6 | ~95.9 |
| 35B-A3B MoE | 93.5 | ~94.8 |

Spread is preserved (the fixes correct issues that hit all models roughly
equally), but the suite reflects model capability more honestly.

---

## Phase 2 — 15 hardening tasks for saturated categories

All three saturated categories sit at 100% across all 5 models. New hard-tier
tasks below are designed so that:

1. The "obvious" first attempt has a subtle bug that fails on edge cases.
2. Validation is deterministic — passes on a correct impl, fails on common
   wrong impls.
3. Difficulty is "hard" per existing convention (weight 2, budget 300s).

Task IDs are 145–159, picking up from the current 144.

### Algorithms & DS (5 tasks, IDs 145-149)

| ID | Name | Subcategory | Discrimination signal |
|---|---|---|---|
| 145 | Segment tree with lazy propagation | Trees | Range update + range query on 10⁵ elements. Lazy push-down timing is the trap; bugs surface only on overlapping queries. |
| 146 | Aho-Corasick multi-pattern matcher | String algorithms | Suffix-link construction. Naive impls work for 1 pattern, miss overlaps across patterns. |
| 147 | Persistent (immutable) red-black tree | Trees | Path-copy + rebalance under immutability. Default impl mutates and breaks the persistent contract. |
| 148 | Convex hull (Andrew's monotone chain) | **NEW: Computational geometry** | Cross-product orientation + collinear point handling. Tests float-vs-int discipline. |
| 149 | Suffix automaton (online construction) | String algorithms | Linear-time suffix-link maintenance. Hard data structure; few models internalize it. |

### Concurrency & Systems (5 tasks, IDs 150-154)

| ID | Name | Subcategory | Discrimination signal |
|---|---|---|---|
| 150 | Michael-Scott lock-free queue | Concurrent data structures | CAS-loop reasoning. Validation tests memory safety under contention with multiple producer/consumer threads. |
| 151 | Read-write lock with writer-preference | Synchronization primitives | Starvation correctness. Common bug: writers wait forever behind a stream of readers. |
| 152 | Chase-Lev work-stealing deque | Concurrent data structures | Owner pushes/pops one end, thieves steal from the other. Memory ordering subtlety. |
| 153 | Cooperative coroutine scheduler | Async patterns | 100-line scheduler over generators with yield-based I/O. |
| 154 | Lease-based distributed mutex | Synchronization primitives | Time-bounded lease + heartbeat. Crash-recovery via lease expiry. Spans into Distributed too. |

### Pure & Abstract Math (5 tasks, IDs 155-159)

| ID | Name | Subcategory | Discrimination signal |
|---|---|---|---|
| 155 | Tonelli-Shanks square root mod p | Number theory | Subtle for non-residues + when p ≡ 1 mod 4. Iteration-count bookkeeping. |
| 156 | Berlekamp-Massey LFSR recovery | Polynomial algebra | Given a sequence over a field, recover the minimal LFSR. Tests recurrence reasoning + finite-field discipline. |
| 157 | LLL lattice basis reduction (2D Gauss form) | Linear algebra | Real differentiator. Multiple correct outputs but Lovász & orthogonality invariants verifiable. |
| 158 | Karatsuba big-int multiplication on int8 arrays | Polynomial algebra | Forbids Python's native bigint. Divide-and-conquer + carry handling. |
| 159 | Number-theoretic transform (NTT) over a prime field | Polynomial algebra | Like FFT but exact. Modular arithmetic + bit-reversed butterfly. Even Opus stumbles. |

### Format consistency

Each new task follows the existing JSON schema (id, name, difficulty, setup,
task, validation, cleanup, max_iter, category, subcategory). Validation is
Python-only at first; multi-language variants are added in Phase 4.

`148_convex_hull` introduces a new subcategory **"Computational geometry"** in
the Algorithms & DS category. No code changes needed — `scoring.py` derives
subcategories from the task JSON.

---

## Phase 3 — Multi-language variant architecture

Goal: any task can have variants in Python, Go, C, and/or C++. Each variant
counts as a fractional unit of the same task. Example: a hard task (weight
2.0) with 4 variants → each variant is worth 0.5; passing 3/4 contributes
1.5 to the model's earned score.

### 3.1 Task JSON schema extension

Variants are embedded in the task JSON, keeping one task = one file
(save-state friendly). Backward compatible: tasks without a `variants` field
behave exactly as today (treated as a single Python variant).

```json
{
  "id": "145_segment_tree",
  "name": "Segment tree with lazy propagation",
  "difficulty": "hard",
  "category": "Algorithms & DS",
  "subcategory": "Trees",
  "max_iter": 30,
  "variants": [
    {
      "id": "a",
      "language": "python",
      "setup": "mkdir -p /tmp/eval_seg",
      "task": "Create /tmp/eval_seg/seg.py — a CLI program that reads from stdin: line 1 is N (array size) and Q (query count); line 2 is N integers; the next Q lines are operations 'U l r v' (range add) or 'Q l r' (range sum). Output one integer per Q operation, one per line.",
      "validation": {"type": "bash", "script": "cd /tmp/eval_seg && python3 seg.py < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"},
      "cleanup": "rm -rf /tmp/eval_seg"
    },
    {
      "id": "b",
      "language": "go",
      "setup": "mkdir -p /tmp/eval_seg",
      "task": "Create /tmp/eval_seg/seg.go with a `package main` that reads stdin and writes stdout in the same format as the Python variant.",
      "validation": {"type": "bash", "script": "cd /tmp/eval_seg && go build -o seg seg.go && ./seg < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"},
      "cleanup": "rm -rf /tmp/eval_seg"
    },
    {
      "id": "c",
      "language": "c",
      "setup": "mkdir -p /tmp/eval_seg",
      "task": "Create /tmp/eval_seg/seg.c — same I/O contract as the Python and Go variants. Must compile clean with `-Wall -Wextra -O2`.",
      "validation": {"type": "bash", "script": "cd /tmp/eval_seg && gcc -O2 -std=c11 -Wall -o seg seg.c && ./seg < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"},
      "cleanup": "rm -rf /tmp/eval_seg"
    },
    {
      "id": "d",
      "language": "cpp",
      "setup": "mkdir -p /tmp/eval_seg",
      "task": "Create /tmp/eval_seg/seg.cpp — same I/O contract.",
      "validation": {"type": "bash", "script": "cd /tmp/eval_seg && g++ -O2 -std=c++17 -Wall -o seg seg.cpp && ./seg < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"},
      "cleanup": "rm -rf /tmp/eval_seg"
    }
  ]
}
```

**Variant fields:**
- `id`: short ASCII suffix (`a`, `b`, `c`, `d`) — appended to base task id in results.
- `language`: `python` | `go` | `c` | `cpp` (free-form string; informational + can drive UI grouping).
- `setup`, `task`, `validation`, `cleanup`: same shape as a top-level task field; override the base values.

**Why I/O via stdin/stdout for variants:** language-agnostic. Avoids ABI
issues (e.g. linking a C library into a Python harness). The harness runs
the program; the model picks its own internal data structures. Function-
based tasks (e.g. `def historical_var(...)`) stay Python-only — they don't
become variant tasks.

### 3.2 Scoring math

```
For each variant in a task with N variants:
    variant_weight = DIFFICULTY_WEIGHTS[task.difficulty] / N

For tasks with no variants (legacy):
    weight = DIFFICULTY_WEIGHTS[task.difficulty]   (unchanged)
```

This preserves the maximum-points-per-task invariant. A hard task is worth
2.0 whether it has 1 Python implementation or 4 cross-language variants.

**Pass count.** Each variant counts as 1 toward `tasks_total` and contributes
1 to `tasks_passed` if it passes. So a hard task with 4 variants and 3
passes contributes 3 to `tasks_passed` and 4 to `tasks_total` — visible in
the leaderboard column as e.g. `145/156`.

**Hard pass count.** Counted at the variant level: 4-variant hard task with
3 passes contributes 3 hard passes.

**Speed.** Each variant has its own elapsed time; speed is averaged over all
passed variants exactly as today. Compile time counts (validation runs `go
build` / `gcc` etc.).

### 3.3 Result file extension

Per-task entries in the result JSON keep the current shape, but the `id`
field becomes `{base_id}_{variant_id}` (e.g. `145_segment_tree_a`). New
optional fields:

```json
{
  "id": "145_segment_tree_a",
  "base_id": "145_segment_tree",
  "variant_count": 4,
  "language": "python",
  "name": "Segment tree with lazy propagation",
  "difficulty": "hard",
  "passed": true,
  ...
}
```

`base_id` and `variant_count` are absent on legacy single-variant tasks
(treated as `variant_count: 1`).

### 3.4 Code changes required

| File | Change |
|---|---|
| `evals/run_eval.py` | `load_tasks` flattens variants into a list of (variant-as-task) entries. Each gets effective `id = base_id + '_' + variant_id`, plus `base_id`, `variant_count`, `language` fields. The setup/task/validation/cleanup come from the variant. |
| `evals/scoring.py` | `compute_accuracy`: weight per entry = `DIFFICULTY_WEIGHTS[diff] / max(1, variant_count or 1)`. `compute_speed` and category breakdown unchanged in shape — they iterate per-entry, which now means per-variant. |
| `evals/scoring.py` | New CLI flag `--by-language`: per-language accuracy breakdown (analogous to `--by-category`). |
| `evals/benchmark_all.py` | No change — it runs `run_eval` per model, which handles the rest. |
| `evals/run_eval.py` | Re-run setup before validation (Phase 1.4). |
| `RESULTS.md` | Document the variant convention; add a sample per-language table after the next sweep. |

### 3.5 Backward compatibility

- Existing 144 task JSON files: untouched. `variant_count` defaults to 1,
  weight unchanged, results structure unchanged.
- Existing result JSONs in `evals/results/`: still parseable. Old entries
  have no `base_id`/`variant_count`; scoring treats them as
  `variant_count = 1` via the `or 1` fallback.
- Existing leaderboard: untouched until next sweep / `--rebuild`.

---

## Phase 4 — Variant rollout

We're not converting all 144 tasks. We're picking ones where multi-language
genuinely tests language fluency, not Python idioms. ~10 existing + select
new hardening tasks.

### 4.1 Existing tasks getting variants

| Task | Difficulty | Languages | Why these |
|---|---|---|---|
| `19_three_way_quicksort` | medium | Py / Go / C / C++ | Pointer arithmetic + partition logic differs cleanly per language. |
| `31_is_power_of_two` | easy | Py / Go / C / C++ | Bit-tricks task, natural in low-level langs. |
| `51_toposort` | medium | Py / Go / C / C++ | Graph algorithm, every lang has a natural take. |
| `52_unionfind` | medium | Py / Go / C / C++ | DSU with path compression. |
| `53_bloom` | medium | Py / Go / C / C++ | Bit array + hashing. C variant tests `uint8_t[]` discipline. |
| `61_extgcd` | medium | Py / Go / C / C++ | Watch overflow in C/Go. |
| `65_miller_rabin` | medium | Py / Go / C / C++ | Modular exponentiation. C/C++ need 128-bit handling for the witness loop. |
| `73_count_vowels` | easy | Py / Go / C / C++ | Easy warmup; tests basic I/O fluency in each lang. |
| `74_palindrome` | easy | Py / Go / C / C++ | Same. |
| `122_gemm_blocked` | hard | C / C++ / Go | Performance-flavored — Python variant doesn't make sense. |

**Total:** 10 existing tasks → 38 variants (one task is C/C++/Go only).
Net suite size: 134 + 38 = 172 effective entries.

### 4.2 New hardening tasks getting variants

Out of the 15 new tasks (145–159), we add variants to those where
multi-language is meaningful and tractable to validate via stdio:

| Task | Languages | Notes |
|---|---|---|
| 145 segment tree lazy | Py / Go / C / C++ | Stdio-friendly. |
| 146 Aho-Corasick | Py / Go / C / C++ | Stdio-friendly (input: patterns + text). |
| 148 convex hull | Py / Go / C / C++ | Stdio-friendly. |
| 152 Chase-Lev deque | Py / Go / C++ | C is awkward for atomic generics. |
| 153 coroutine scheduler | Py / Go | Each lang has its own primitives; harness via stdio events. |
| 155 Tonelli-Shanks | Py / Go / C / C++ | Stdio-friendly. |
| 158 Karatsuba big-int | Py / Go / C / C++ | Stdio-friendly. |
| 159 NTT | Py / Go / C / C++ | Stdio-friendly. |

The other 7 new tasks (147 persistent rb-tree, 149 suffix automaton, 150
Michael-Scott queue, 151 RW-lock, 154 lease mutex, 156 Berlekamp-Massey,
157 LLL) stay Python-only at first — adding variants is a follow-up if/when
the suite needs it.

**Total new variants:** 8 tasks × ~3.5 langs ≈ 28 variant entries on top of
the 7 single-variant new tasks.

### 4.3 Suite size after Phase 4

- Existing: 144 tasks (134 single + 10 with 4 variants = 134 + 40 = 174 — wait, we set 73,74 are easy, and 122 is C/C++/Go, so 9×4 + 1×3 = 39).
- New: 15 tasks (7 single + 8 with variants ≈ 28) ≈ 35
- **Effective total: ~174 + 35 ≈ 209 variant-entries** spread across ~159
  base tasks. Sweep time scales linearly — expect ~10-12 hours on the 5090
  for a full sweep of all 5 models post-rollout (vs. 7h 21m today).

If sweep time becomes the bottleneck, we can drop variants on easy tasks
(73, 74, 31) — easy variants don't differentiate.

---

## Implementation order

Strict dependency ordering. Each phase commits separately so the
leaderboard / RESULTS.md story is auditable.

1. **Phase 1 fixes** (one commit):
   - Edit `42_value_at_risk.json`, `85_base64.json`, `121_quorum_kv.json`.
   - Edit `run_eval.py` to re-assert fixtures pre-validation.
   - Local smoke test: re-run those four tasks against the winning model
     and confirm pass/fail flips as expected.
2. **Phase 2 hardening tasks** (one commit):
   - Add 15 new task JSON files (145-159).
   - Test each validation script with `bash -n` / `ast.parse` (existing
     `tests/test_scripts.sh` guard catches syntax).
   - Solve each one ourselves before committing — confirm the spec is
     internally consistent (the lesson from 42_value_at_risk: always
     verify with a known-good impl).
3. **Phase 3 architecture** (one commit):
   - `run_eval.py`: variant flattening in `load_tasks`.
   - `scoring.py`: variant-aware weight calc + `--by-language` flag.
   - Backward compat regression test: re-score the existing
     `eval-*.json` files with `--rebuild` and confirm leaderboard is
     bit-identical.
4. **Phase 4 variants** (one commit per category, three commits):
   - 4a: existing 10 tasks get variants.
   - 4b: 8 new tasks get variants.
   - 4c: RESULTS.md update with the variant convention; awaits next sweep.
5. **Validation sweep** (no commit; just runs):
   - Run benchmark_all.py against the winning model only (Qwen 35B-A3B
     Uncensored Q4_K_M) on the new variant tasks. Confirms variant
     plumbing works end-to-end before Max blows 10+ hours on a full sweep.
6. **Full sweep** (Max-triggered):
   - Run benchmark_all.py over all 5 models. ~10-12h.
   - Update leaderboard.json and RESULTS.md.

## Phase 4 deferred items

The following 5 tasks were on the original Phase 4 plan but are deferred to a
follow-up session. Each has a specific reason that makes it heavier than the
13 we've already shipped.

| Task | Original plan | Why deferred |
|---|---|---|
| 53_bloom | Py / Go / C / C++ | Validation is probabilistic (false-positive rate < 5%). Stdio port requires generating 10 500 strings + verifying FP rate via inline checker — bloated fixtures. Doable in a focused follow-up session. |
| 145_segment_tree_lazy | Py / Go / C / C++ | Lazy-propagation segment tree requires ~150 lines of code per language (build/update/query/push-down). Heavy to write 4 reference impls plus the spec. Defer until needed. |
| 146_aho_corasick | Py / Go / C / C++ | Suffix-link automaton + multi-pattern matcher is ~200 lines per language. Heaviest data-structure port. Defer. |
| 152_chase_lev_deque | Py / Go / C++ | Concurrent code is hard to port — Python uses threading, Go uses goroutines + channels (different paradigm), C++ atomics. Spec needs language-specific framing. Defer. |
| 153_coroutine_scheduler | Py / Go | Generator-based scheduler is Pythonic; Go's natural equivalent is goroutines + channels — fundamentally different mechanism. Need to re-spec for Go. Defer. |

**Update 2026-05-06 (mid-smoke-test):** the Tonelli-Shanks Go failure during
the smoke test produced **the first concrete cross-language differential** in
this stack — the model wrote correct Python, C, and C++ implementations of
modular square root but failed Go after 25 iterations and 239K tokens,
specifically thrashing on Go's typed-int handling around `(*big.Int).Lsh(n,
uint(j))` when `j` could be negative. The algorithmic knowledge transferred
across three languages; the language-specific edge case did not.

This significantly raises the case for picking up the deferred items —
especially **145_segment_tree_lazy** and **146_aho_corasick**, which are
algorithmically dense in ways that exercise language-specific data-structure
idioms (slice aliasing in Go, manual memory in C, RAII vs raw pointers in
C++). They're exactly the right shape to produce more cross-language
differentials. See `docs/TODO.md` "Expand multi-language variant coverage" for
the post-sweep gating plan.

When picking these up, the patterns from the 13 completed tasks transfer
cleanly. The audit tool (`/tmp/audit_variants.py` — should be moved into the
repo as `evals/audit_variants.py` and updated with the new task entries) drops
each reference impl into the right path and runs validation; same flow for any
new variant.

## Open questions / future work (post-rollout)

- **Rust variants?** rustc is installed but not in scope. Add as a 5th
  variant if Rust becomes part of Max's daily-driver set.
- **Per-variant time budgets?** Compile-heavy languages (C++) may benefit
  from a slightly longer budget. Defer until we see the data — a fast
  compile shouldn't push us over 300s on hard tasks.
- **Variant timeout independence.** `subprocess.run(..., timeout=...)`
  in `run_agent` is per-variant. Total task time = sum of variant times.
  This is correct for our scoring math.
- **3090 Ti sweep:** Max will run this in ~1 month (per 2026-05-06 chat).
  All Phase 1-4 changes should be merged before then so cross-host
  comparison uses the same suite version.

## Decision log

| Question | Decision | Why |
|---|---|---|
| One file per task with embedded variants vs. one file per variant? | **Embedded** | Save-state friendly. One task = one file. |
| Function-call validation vs. stdio validation for variants? | **Stdio** | Language-agnostic, avoids ABI issues. Function-call tasks stay Python-only. |
| Which existing tasks get variants? | **10 selected** (see 4.1) | Genuine multi-language fit; avoid Python-idiom tasks. |
| Variant scoring: per-variant pass count or per-task? | **Per-variant** | Simplest math; preserves the max-points-per-task invariant. |
| Should easy tasks have variants? | **Some** (73, 74, 31) | Easy-tier variants are warmups in each language. |
| Add Rust variants? | **No** (for now) | Out of scope; 4-language matrix is already substantial. |

---

*End of plan. Implementation begins after Max's go-ahead.*
