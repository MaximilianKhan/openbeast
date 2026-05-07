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

**Variant ID convention** (stable letter suffix, used across all 13 variant
tasks in the suite): `a`=python, `b`=go, `c`=c, `d`=cpp, `e`=rust, `f`=zig.
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

```bash
set -e
cd /tmp/eval_X
{build_command}            # no-op for python
./sol < input.txt > out.txt   # or python3 sol.py < input.txt > out.txt
diff -u expected.txt out.txt && echo OK
# OR for non-deterministic outputs:
python3 check.py
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
    {"id": "f", "language": "zig",  "setup": "...", "task": "Create /tmp/eval_X/sol.zig. Zig 0.16+ idioms — see the canonical Zig section below for the full pattern, especially the `&fr.interface` indirection. Compile with `zig build-exe -O ReleaseFast sol.zig`. ...", "validation": {...}, "cleanup": "..."}
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
- **Zig 0.16** — see the dedicated section below. Quick checklist: `std.Io`
  (capital I), `&fr.interface` to reach the rich Reader/Writer API, `try
  w.flush()` before return, prefer `const` over `var`, rename unused params
  to `_`. Cold compile ~7-8s/file.

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

> **Why this section exists.** The original Zig variant rollout taught the
> model to call methods like `takeDelimiter` and `appendRemainingUnlimited`
> directly on `std.Io.File.Reader`. Those methods don't live there — they
> live on the underlying `std.Io.Reader` *interface*, accessed via
> `&fr.interface`. The omission scored 0/13 across both 27B models in the
> first v3 sweep. These templates are the verified fix. Use them in every
> Zig variant `task` field.

### Critical API rules

1. **Capital `Io`.** `std.Io`, not `std.io`. The lowercase namespace was
   removed in Zig 0.15+.
2. **The `.interface` indirection.** `std.Io.File.Reader` and
   `std.Io.File.Writer` are concrete file readers/writers with ~10 public
   methods. The rich API (65+ methods including `takeDelimiter`,
   `appendRemainingUnlimited`, `print`) lives on `std.Io.Reader` /
   `std.Io.Writer` (the interface). Get there via `const r = &fr.interface;`.
3. **`init.io` and `init.arena`.** The `Init` param exposes both. Pass
   `init.io` to `.reader()` / `.writer()`. Use `init.arena.allocator()` for
   transient heap allocations.
4. **Always `try w.flush()`** before return — output is buffered and
   silently dropped on exit if you don't.

### Strict-default footguns (these are *errors*, not warnings)

- **Unused function parameter.** Rename to `_`: `pub fn main(_: std.process.Init) !void { ... }` if you don't need `init`. (You'll need it for stdio though.)
- **Unused local constant.** Discard with `_ = unused_value;` or just don't bind it.
- **`var` never mutated.** Use `const` instead. Zig errors if a `var` is never reassigned.
- **Unused imports** of named items. Prefer `const std = @import("std");` and access fields qualified — `std.foo` doesn't trigger unused-import.

### Template 1 — Line-by-line stdin → stdout (most common)

```zig
const std = @import("std");

pub fn main(init: std.process.Init) !void {
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    while (try r.takeDelimiter('\n')) |line| {
        // Process `line` (a []u8 slice without the newline)
        try w.print("{s}\n", .{line});
    }
    try w.flush();
}
```

### Template 2 — Slurp all stdin → tokenize whitespace

```zig
const std = @import("std");

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");
    while (it.next()) |tok| {
        const n = try std.fmt.parseInt(i64, tok, 10);
        try w.print("{d}\n", .{n});
    }
    try w.flush();
}
```

Both templates verified to compile and run on `zig 0.16.0` (mise install).
Use Template 1 when input is line-oriented (one record per line); use
Template 2 when input is a stream of whitespace-separated tokens.

### Useful interface methods (on `std.Io.Reader` via `&fr.interface`)

- `takeDelimiter(byte) !?[]u8` — read up to and excluding delimiter; returns `null` at EOF
- `takeDelimiterExclusive(byte) ![]u8` — same but errors at EOF instead of returning null
- `appendRemainingUnlimited(allocator, *ArrayList(u8)) !void` — slurp all remaining bytes
- `takeByte() !u8` — single byte
- `readSliceAll([]u8) !void` — fill a fixed-size buffer

### Useful interface methods (on `std.Io.Writer` via `&fw.interface`)

- `print(fmt, args) !void` — formatted output (`{d}`, `{s}`, `{x}`, etc.)
- `writeAll([]const u8) !void` — write a slice
- `writeByte(u8) !void` — single byte
- `flush() !void` — **must call before return**

## Done criteria for a variant task

- [ ] All variants in the JSON (a–f as appropriate)
- [ ] Reference impl for each language in `evals/refs/{slug}.{ext}` (durable; was `/tmp/refs/` pre-2026-05-07)
- [ ] All variants pass validation with their reference impls — verified via
      `python3 tests/audit_variants.py {task_id}`
- [ ] Forbidden-API lints catch obvious cheats in each language
- [ ] Per-variant runtime <2s with reference impl (Zig <10s acceptable due to
      compile cost)
- [ ] `tests/test_scripts.sh` passes (validates each variant's setup/validation
      bash syntax)
