---
name: eval-variant-porter
description: Add multi-language variants (Python / Go / C / C++ / Rust / Zig — 6 supported languages) to an existing eval task. Activate when the user wants to make an eval task language-agnostic or extend the variant rollout. Companion to eval-task-author.
allowed_tools: [bash, read_file, write_file, edit_file, grep]
recommends_subagent: true
---

# Eval variant porter

Convert an existing single-variant Python task into a multi-variant task with
implementations in some subset of `python`, `go`, `c`, `cpp`, `rust`, `zig`.
Each variant is its own scored test unit; weight per variant =
`difficulty_weight / num_variants` (so total points per task stay constant).

**Variant ID convention** (stable letter suffix, used by every variant task in
`evals/tasks/`: 35 on 2026-11-19): `a`=python, `b`=go, `c`=c, `d`=cpp, `e`=rust, `f`=zig.
Stable letters mean adding new languages doesn't renumber existing variants.
The exception is `122_gemm_blocked`, which has no Python (perf-flavored), so
its IDs are `a`=go, `b`=c, `c`=cpp, `d`=rust, `e`=zig.

## Architecture

A variant task uses the `variants` array in the JSON. Each variant has its own
`setup`, `task`, `validation`, `cleanup`. Top-level fields are: `id`, `name`,
`difficulty`, `category`, `subcategory`, `max_iter`. The flattening happens in
`evals/run_eval.py:load_tasks` — each variant becomes an entry with effective
id `{base_id}_{variant_id}`.

## When to add variants

Good candidates:
- Algorithm/data-structure tasks (sort, hash, graph, math)
- Tasks where the language genuinely matters (perf-sensitive, low-level)
- Tasks with deterministic, testable I/O contracts

Bad candidates (keep Python-only):
- Tasks that depend on Python idioms (decorators, dunders, threading.RLock)
- Probabilistic tests (Bloom filter false-positive rates) — porting is
  doable but each variant needs its own probabilistic check; high lift
- Concurrency tasks where Python uses threads, Go uses goroutines/channels,
  and C++ uses atomics — fundamentally different paradigms; the spec needs
  language-specific framing

## The standard pattern

**Stdio is the common ground.** Don't try to share a Python checker via FFI.
Each variant produces a program that reads stdin and writes stdout; validation
diffs against a fixture file (or runs `python3 check.py` for non-deterministic
outputs).

### Per-language build & run

| Lang | Build command | Run command |
|---|---|---|
| python | (none) | `python3 sol.py < input.txt > out.txt` |
| go | `go build -o sol sol.go` | `./sol < input.txt > out.txt` |
| c | `gcc -O2 -std=c11 -Wall -Wextra -o sol sol.c` | `./sol < input.txt > out.txt` |
| cpp | `g++ -O2 -std=c++17 -Wall -Wextra -o sol sol.cpp` | `./sol < input.txt > out.txt` |
| rust | `rustc -O sol.rs -o sol` | `./sol < input.txt > out.txt` |
| zig | `zig build-exe -O ReleaseFast sol.zig` | `./sol < input.txt > out.txt` |

### Validation script template

> **v5 rule (the suite since the v5 era break).** The template below is the
> v4 shape and is kept only to read v4 tasks. A v5 unit never puts the
> answer on disk: 120 v4 units left `expected.txt` and 53 left `check.py`
> in the agent's working dir, and a probe that only `cat`s them PASSED. In
> v5, setup writes 2-5 clearly labelled samples (`sample_input.txt`,
> `sample_output.txt`) and nothing else; the validator is `type: python`,
> holds a seeded generator and the oracle, compiles once, feeds the hidden
> cases over stdin with `subprocess.run(input=..., timeout=...)`, prints only
> the failing case's INDEX on a mismatch, and stays under 15 s at scale 1
> including a cold zig compile. No coreutils `timeout` (absent on macOS), no
> `<bits/stdc++.h>`. Prove it with `tests/prove_task.py` (below).

```bash
set -e
cd /tmp/eval_X
{build_command}            # no-op for python
./sol < input.txt > out.txt   # or python3 sol.py < input.txt > out.txt
diff -u expected.txt out.txt && echo OK      # v4 only: v5 bans this
# OR for non-deterministic outputs:
python3 check.py                              # v4 only: v5 bans this
```

## JSON skeleton (6-language variant task)

```json
{
  "id": "NNN_my_task",
  "name": "...",
  "difficulty": "medium",
  "category": "...",
  "subcategory": "...",
  "max_iter": 20,
  "variants": [
    {
      "id": "a", "language": "python",
      "setup": "mkdir -p /tmp/eval_X && printf '...' > /tmp/eval_X/input.txt && printf '...' > /tmp/eval_X/expected.txt",
      "task": "Create /tmp/eval_X/sol.py — reads from stdin: ... Output: ...",
      "validation": {"type": "bash", "script": "set -e; cd /tmp/eval_X && python3 sol.py < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"},
      "cleanup": "rm -rf /tmp/eval_X"
    },
    {"id": "b", "language": "go",   "setup": "...", "task": "Create /tmp/eval_X/sol.go (package main). Compile with `go build -o sol sol.go`. ...", "validation": {...}, "cleanup": "..."},
    {"id": "c", "language": "c",    "setup": "...", "task": "Create /tmp/eval_X/sol.c. Must compile cleanly with `gcc -O2 -std=c11 -Wall -Wextra -o sol sol.c`. ...", "validation": {...}, "cleanup": "..."},
    {"id": "d", "language": "cpp",  "setup": "...", "task": "Create /tmp/eval_X/sol.cpp. Must compile cleanly with `g++ -O2 -std=c++17 -Wall -Wextra -o sol sol.cpp`. ...", "validation": {...}, "cleanup": "..."},
    {"id": "e", "language": "rust", "setup": "...", "task": "Create /tmp/eval_X/sol.rs. Idiomatic Rust 2021+; compile with `rustc -O sol.rs -o sol`. ...", "validation": {...}, "cleanup": "..."},
    {"id": "f", "language": "zig",  "setup": "...", "task": "Create /tmp/eval_X/sol.zig for Zig 0.16 (entry point `pub fn main(init: std.process.Init) !void`). It is built with `zig build-exe -O ReleaseFast sol.zig`. ...", "validation": {...}, "cleanup": "..."}
  ]
}
```

**The zig `task` states the contract and teaches no API.** v4 prompts
carried the stdio recipe (see the sealed authoring recipe), so every "unassisted" zig arm was tutored — the packs-OFF arm of the
Tier-3 A/B passed 10-11/30 *with* the recipe in its prompt. v5 bans those
phrases in a zig `task` (`tests/audit_variants.py` ZIG TUTORING BAN LINT,
enforced on every v5 unit by `tests/test_v5_suite.py`). The version, the
entry point the validator needs, and the exact compile command are the
spec; everything else about the stdlib is the model's job, or the
treatment arms' (`--packs`, `BEAST_ASSIST`, `--escalate`). The canonical
patterns below are for writing the REFERENCE solution, never the prompt.

**Declare the refs in the JSON.** Each unit (base or variant) names its
reference as `"ref_stem": "<dir under evals/refs/>/ref"` (no extension; the
language picks `py go c cpp rs zig sh`) and `"dest"`: the path the task
says the solution lives at. Wrong solutions (`wrong_<name>.<ext>`, >= 2), a
slow solution for a timed unit (`slow.<ext>`), an optional leak probe
(`leak.<ext>`) and their failure classes (`prove.json`:
`{"expect": {"wrong_<name>.<ext>": "<regex>"}, "slow_expect": "...",
"timed": true}`) sit next to it in `evals/refs/<task_id>/`. The old hand
map in `audit_variants.py` (LEGACY_TARGETS) is for v4 tasks only.

The `setup` should be **identical across all variants** of a task — same input,
same expected output. Only the build/run commands and language-specific
forbidden-API lints differ.

## Heredoc fixtures (when printf isn't enough)

For test data that needs Python to generate (random arrays, expected
convolutions, etc.):

```bash
mkdir -p /tmp/eval_X && cat > /tmp/eval_X/_gen.py <<'PYEOF'
import random
random.seed(42)
# generate input.txt and expected.txt
open('/tmp/eval_X/input.txt', 'w').write(...)
open('/tmp/eval_X/expected.txt', 'w').write(...)
PYEOF
python3 /tmp/eval_X/_gen.py
```

In JSON, the heredoc body has literal `\n` characters (which JSON encodes as
`\\n` in the source, decoded to real newlines). Bash heredoc-quoted with
`<<'PYEOF'` (note the quotes) prevents shell expansion of `$` etc. inside.

**Trap to avoid:** `python3 -c "...\n..."` does NOT work — Python's `-c`
parses literal backslash-n as a syntax error. Use heredocs.

## Forbidden-API lints per language

| Goal | Python | Go | C | C++ | Rust | Zig |
|---|---|---|---|---|---|---|
| No std sort | `! grep -qE '\bsorted\(\|\.sort\(' sol.py` | `! grep -qE 'sort\.(Ints\|Slice\|Sort)' sol.go` | `! grep -qE '\bqsort\s*\(' sol.c` | `! grep -qE 'std::(sort\|stable_sort\|nth_element)\b' sol.cpp` | `! grep -qE '\.sort(_unstable)?\(' sol.rs` | `! grep -qE 'std\.mem\.sort\b\|std\.sort\.' sol.zig` |
| No big-int lib | `! grep -qE '\.from_bytes\|\.to_bytes' sol.py` | `! grep -qE 'math/big' sol.go` | n/a | `! grep -qE '__int128\|<gmp' sol.cpp` | `! grep -qE 'num_bigint\|num::BigInt' sol.rs` | `! grep -qE 'std\.math\.big\b' sol.zig` |
| No numpy/scipy | `! grep -qE 'import (numpy\|scipy)\|from (numpy\|scipy)' sol.py` | n/a | n/a | n/a | n/a | n/a |

**Always use `import` checks**, not bare substring. Models cite library names in
docstrings legitimately. See `eval-task-author` SKILL pitfall #1.

## Per-language reference impl tips

- **Python**: `sys.stdin.read().split()` for whitespace-separated integers.
  `print('\n'.join(...))` faster than print-per-line.
- **Go**: `bufio.Scanner.Split(bufio.ScanWords)` for whitespace-token reads.
  Use `bufio.NewWriter(os.Stdout); defer w.Flush()`. For large inputs:
  `scanner.Buffer(make([]byte, 1<<20), 1<<20)`.
- **C**: `scanf("%d", &x)` is fine for integers. Use `fgets` for line-by-line.
  `printf("%s\n", ok ? "true" : "false")`. Free your mallocs.
- **C++**: `std::ios_base::sync_with_stdio(false);` at the top of main for fast
  I/O. `std::cin >> x` for whitespace-separated. `'\n'` not `std::endl`.
- **Rust**: `io::stdin().read_to_string(&mut s).unwrap()` then
  `s.split_ascii_whitespace()`. `BufWriter::new(stdout.lock())` for fast
  output. `u128` for modular-multiply overflow, `i64`/`u64` otherwise.
- **Zig 0.16** — see the sealed authoring recipe (section below points to it).
  Cold compile ~7-8s/file.

## Common gotchas

- **Integer overflow.** C and Go default to `int` which may be 32-bit. For
  Bezout coefficients, modular arithmetic, large convolutions: use `long long`
  / `int64` / `uint64_t` / `__int128`.
- **Whitespace in output.** `diff -u` is strict. End every output line with
  `\n` exactly once. Be careful with trailing whitespace.
- **C trailing newline.** `printf("...\n")` at end of program; without it some
  diffs flag a missing final newline.
- **Go time-to-compile.** First `go build` in a fresh dir takes ~1s. This adds
  to the validation runtime — don't set perf gates so tight that compilation
  alone fails.
- **Zig time-to-compile.** Cold `zig build-exe` is **~7-8s** per file — much
  larger than other compilers. Adjust perf-gate thresholds for Zig variants
  accordingly, and remember that across a 5-model overnight sweep the Zig
  compile cost adds up (~9 min total for 13 variant tasks × 5 models).

## Zig 0.16 canonical patterns

The Zig 0.16 stdio/API recipe for writing reference solutions is sealed in
[`evals/audit/authoring/zig-0.16-reference-recipe.md`](../../evals/audit/authoring/zig-0.16-reference-recipe.md):
v5 forbids that wording in task prompts as tutoring, and this skill is readable
by the measured agent. Read it there when authoring a Zig reference.

## Done criteria for a variant task

- [ ] All variants in the JSON (a–f as appropriate)
- [ ] Reference impl for each language under `evals/refs/{task_id}/`, declared
      per unit as `ref_stem` + `dest` (v4 tasks: `evals/refs/{slug}.{ext}`)
- [ ] **PROVEN** by the model-free gate, every variant:
      `python3 tests/prove_task.py --unit {task_id}_{v}` — ref N/N (5/5 if
      timed), >= 2 wrong solutions failing for their declared class, empty
      fails, no oracle file in the fixture dir, slow solution fails by the
      validator's own limit, ref passes again at `--scale 2.0`
- [ ] No banned zig tutoring in any zig `task` (`&fr.interface`, `init.io`,
      `takeDelimiter`)
- [ ] All variants pass validation with their reference impls — quick smoke:
      `python3 tests/audit_variants.py {task_id}`
- [ ] Forbidden-API lints catch obvious cheats in each language
- [ ] Per-variant runtime <2s with reference impl (Zig <10s acceptable due to
      compile cost)
- [ ] `tests/test_scripts.sh` passes (validates each variant's setup/validation
      bash syntax)
