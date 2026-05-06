---
name: eval-variant-porter
description: Add multi-language variants (Python / Go / C / C++) to an existing eval task. Activate when the user wants to make an eval task language-agnostic or extend the variant rollout. Companion to eval-task-author.
allowed_tools: [bash, read_file, write_file, edit_file, grep]
recommends_subagent: true
---

# Eval variant porter

Convert an existing single-variant Python task into a multi-variant task with
implementations in some subset of `python`, `go`, `c`, `cpp`. Each variant is
its own scored test unit; weight per variant = `difficulty_weight /
num_variants` (so total points per task stay constant).

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

### Validation script template

```bash
set -e
cd /tmp/eval_X
{build_command}            # no-op for python
./sol < input.txt > out.txt   # or python3 sol.py < input.txt > out.txt
diff -u expected.txt out.txt && echo OK
# OR for non-deterministic outputs:
python3 check.py
```

## JSON skeleton (4-language variant task)

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
    {"id": "b", "language": "go",  "setup": "...", "task": "Create /tmp/eval_X/sol.go (package main). Compile with `go build -o sol sol.go`. ...", "validation": {...}, "cleanup": "..."},
    {"id": "c", "language": "c",   "setup": "...", "task": "Create /tmp/eval_X/sol.c. Must compile cleanly with `gcc -O2 -std=c11 -Wall -Wextra -o sol sol.c`. ...", "validation": {...}, "cleanup": "..."},
    {"id": "d", "language": "cpp", "setup": "...", "task": "Create /tmp/eval_X/sol.cpp. Must compile cleanly with `g++ -O2 -std=c++17 -Wall -Wextra -o sol sol.cpp`. ...", "validation": {...}, "cleanup": "..."}
  ]
}
```

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

| Goal | Python | Go | C | C++ |
|---|---|---|---|---|
| No std sort | `! grep -qE '\bsorted\(\|\.sort\(' sol.py` | `! grep -qE 'sort\.(Ints\|Slice\|Sort)' sol.go` | `! grep -qE '\bqsort\s*\(' sol.c` | `! grep -qE 'std::(sort\|stable_sort\|nth_element)\b' sol.cpp` |
| No big-int lib | `! grep -qE '\.from_bytes\|\.to_bytes' sol.py` | `! grep -qE 'math/big' sol.go` | n/a | `! grep -qE '__int128\|<gmp' sol.cpp` |
| No numpy/scipy | `! grep -qE 'import (numpy\|scipy)\|from (numpy\|scipy)' sol.py` | n/a | n/a | n/a |

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

## Done criteria for a variant task

- [ ] All variants in the JSON (a, b, c, d as appropriate)
- [ ] Reference impl for each language in `/tmp/refs/{slug}.{ext}`
- [ ] All variants pass validation with their reference impls
- [ ] Forbidden-API lints catch obvious cheats in each language
- [ ] Per-variant runtime <2s with reference impl (compile + run + diff)
- [ ] `tests/test_scripts.sh` passes (validates each variant's setup/validation
      bash syntax)
