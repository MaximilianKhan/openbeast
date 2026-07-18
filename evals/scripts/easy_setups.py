#!/usr/bin/env python3
"""Generate the JSON shape for the 5 easy variant tasks.

For each task, this writes to evals/tasks/{slug}.json a structure with
a `variants` array (a-f for python/go/c/cpp/rust/zig). The setup is
shared across variants (same input, same expected); the build/run
commands and forbidden-API lints differ per language."""
import json
import os

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# Common Zig idiom block — reused in every Zig variant task field
ZIG_IDIOMS = (
    "Use Zig 0.16+ idioms. Entrypoint: `pub fn main(init: std.process.Init) !void`. "
    "For stdio, the rich API (`takeDelimiter`, `appendRemainingUnlimited`, `print`, "
    "`writeAll`, `flush`) lives on the `std.Io.Reader`/`Writer` *interface*, NOT on "
    "the concrete `std.Io.File.Reader`/`Writer`. Reach the interface via `&fr.interface`. "
    "Canonical setup: `var in_buf: [4096]u8 = undefined; var fr = std.Io.File.stdin()"
    ".reader(init.io, &in_buf); const r = &fr.interface;` and the symmetric writer. "
    "Then `try r.takeDelimiter('\\n')` (returns `?[]u8`, null at EOF) for line-by-line, "
    "or `try r.appendRemainingUnlimited(init.arena.allocator(), &list)` to slurp. "
    "Always `try w.flush()` before return — output is buffered. "
    "Strict-default footguns (errors, not warnings, in 0.16): rename unused params to `_`, "
    "discard unused locals with `_ = x;`, prefer `const` over `var` when not mutated. "
    "Use `std.Io` (capital I), not `std.io`."
)

def variant_template(task_id, slug, name, difficulty, category, subcategory,
                     max_iter, dest_dir, file_stem, setup, common_io,
                     forbidden_per_lang):
    """Build the variants array. forbidden_per_lang: dict lang -> grep pattern (or None)."""
    variants = []

    py_lint = forbidden_per_lang.get("python")
    py_val = f"set -e; cd {dest_dir}"
    if py_lint:
        py_val += f"; ! grep -qE {json.dumps(py_lint)} {file_stem}.py || {{ echo 'forbidden api'; exit 1; }}"
    py_val += f"; python3 {file_stem}.py < input.txt > out.txt; diff -u expected.txt out.txt && echo OK"
    variants.append({
        "id": "a",
        "language": "python",
        "setup": setup,
        "task": f"Create {dest_dir}/{file_stem}.py — {common_io} Pure Python; no numpy.",
        "validation": {"type": "bash", "script": py_val},
        "cleanup": f"rm -rf {dest_dir}",
    })

    go_lint = forbidden_per_lang.get("go")
    go_val = f"set -e; cd {dest_dir}"
    if go_lint:
        go_val += f"; ! grep -qE {json.dumps(go_lint)} {file_stem}.go || {{ echo 'forbidden api'; exit 1; }}"
    go_val += f"; go build -o {file_stem} {file_stem}.go && ./{file_stem} < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"
    variants.append({
        "id": "b",
        "language": "go",
        "setup": setup,
        "task": f"Create {dest_dir}/{file_stem}.go (package main). {common_io} Compile with `go build -o {file_stem} {file_stem}.go`.",
        "validation": {"type": "bash", "script": go_val},
        "cleanup": f"rm -rf {dest_dir}",
    })

    c_lint = forbidden_per_lang.get("c")
    c_val = f"set -e; cd {dest_dir}"
    if c_lint:
        c_val += f"; ! grep -qE {json.dumps(c_lint)} {file_stem}.c || {{ echo 'forbidden api'; exit 1; }}"
    c_val += f"; gcc -O2 -std=c11 -Wall -Wextra -o {file_stem} {file_stem}.c -lm && ./{file_stem} < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"
    variants.append({
        "id": "c",
        "language": "c",
        "setup": setup,
        "task": f"Create {dest_dir}/{file_stem}.c — {common_io} Compile with `gcc -O2 -std=c11 -Wall -Wextra -o {file_stem} {file_stem}.c -lm`.",
        "validation": {"type": "bash", "script": c_val},
        "cleanup": f"rm -rf {dest_dir}",
    })

    cpp_lint = forbidden_per_lang.get("cpp")
    cpp_val = f"set -e; cd {dest_dir}"
    if cpp_lint:
        cpp_val += f"; ! grep -qE {json.dumps(cpp_lint)} {file_stem}.cpp || {{ echo 'forbidden api'; exit 1; }}"
    cpp_val += f"; g++ -O2 -std=c++17 -Wall -Wextra -o {file_stem} {file_stem}.cpp && ./{file_stem} < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"
    variants.append({
        "id": "d",
        "language": "cpp",
        "setup": setup,
        "task": f"Create {dest_dir}/{file_stem}.cpp — {common_io} Compile with `g++ -O2 -std=c++17 -Wall -Wextra -o {file_stem} {file_stem}.cpp`.",
        "validation": {"type": "bash", "script": cpp_val},
        "cleanup": f"rm -rf {dest_dir}",
    })

    rust_lint = forbidden_per_lang.get("rust")
    rust_val = f"set -e; cd {dest_dir}"
    if rust_lint:
        rust_val += f"; ! grep -qE {json.dumps(rust_lint)} {file_stem}.rs || {{ echo 'forbidden api'; exit 1; }}"
    rust_val += f"; rustc -O {file_stem}.rs -o {file_stem} 2>/dev/null && ./{file_stem} < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"
    variants.append({
        "id": "e",
        "language": "rust",
        "setup": setup,
        "task": f"Create {dest_dir}/{file_stem}.rs — {common_io} Idiomatic Rust 2021+; compile with `rustc -O {file_stem}.rs -o {file_stem}`.",
        "validation": {"type": "bash", "script": rust_val},
        "cleanup": f"rm -rf {dest_dir}",
    })

    zig_lint = forbidden_per_lang.get("zig")
    zig_val = f"set -e; cd {dest_dir}"
    if zig_lint:
        zig_val += f"; ! grep -qE {json.dumps(zig_lint)} {file_stem}.zig || {{ echo 'forbidden api'; exit 1; }}"
    zig_val += f"; zig build-exe -O ReleaseFast {file_stem}.zig && ./{file_stem} < input.txt > out.txt && diff -u expected.txt out.txt && echo OK"
    variants.append({
        "id": "f",
        "language": "zig",
        "setup": setup,
        "task": f"Create {dest_dir}/{file_stem}.zig — {common_io} {ZIG_IDIOMS} Compile with `zig build-exe -O ReleaseFast {file_stem}.zig`.",
        "validation": {"type": "bash", "script": zig_val},
        "cleanup": f"rm -rf {dest_dir}",
    })

    return {
        "id": task_id,
        "name": name,
        "difficulty": difficulty,
        "category": category,
        "subcategory": subcategory,
        "max_iter": max_iter,
        "variants": variants,
    }


# ---------------- 32_dot_product ----------------
dot_setup = (
    "mkdir -p /tmp/eval_dot && "
    "printf '5\\n3\\n1 2 3\\n4 5 6\\n0\\n\\n\\n4\\n1 -1 1 -1\\n5 5 5 5\\n5\\n10 20 30 40 50\\n1 2 3 4 5\\n3\\n1000000 1000000 1000000\\n1000000 1000000 1000000\\n' "
    "> /tmp/eval_dot/input.txt && "
    "printf '32\\n0\\n0\\n550\\n3000000000000\\n' > /tmp/eval_dot/expected.txt"
)
dot = variant_template(
    task_id="32_dot_product",
    slug="32_dot_product",
    name="Dot product of two integer vectors",
    difficulty="easy",
    category="Pure & Abstract Math",
    subcategory="Linear algebra",
    max_iter=10,
    dest_dir="/tmp/eval_dot",
    file_stem="dot",
    setup=dot_setup,
    common_io=(
        "Reads from stdin: line 1 is T (test case count). Each case: line A is N "
        "(>= 0). Line B is N space-separated integers (a) — empty line if N=0. "
        "Line C is N space-separated integers (b). For each case, output the integer "
        "dot product sum(a[i]*b[i]) on its own line. Use a 64-bit accumulator "
        "(values can exceed 2^32)."
    ),
    forbidden_per_lang={},
)

# ---------------- 71_reverse_list ----------------
rev_setup = (
    "mkdir -p /tmp/eval_rev && "
    "printf '5\\n5\\n1 2 3 4 5\\n0\\n\\n1\\n42\\n3\\n-1 0 1\\n7\\n10 20 30 40 50 60 70\\n' "
    "> /tmp/eval_rev/input.txt && "
    "printf '5 4 3 2 1\\n\\n42\\n1 0 -1\\n70 60 50 40 30 20 10\\n' > /tmp/eval_rev/expected.txt"
)
rev = variant_template(
    task_id="71_reverse_list",
    slug="71_reverse_list",
    name="Reverse a sequence (in-place, no stdlib reverse)",
    difficulty="easy",
    category="Algorithms & DS",
    subcategory="Linear data structures",
    max_iter=10,
    dest_dir="/tmp/eval_rev",
    file_stem="rev",
    setup=rev_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case: line A is N (>= 0). Line B is N "
        "space-separated integers (or empty if N=0). For each case, output the "
        "integers in REVERSED order, space-separated on one line (or empty line "
        "for N=0). Implement the reversal yourself — do NOT call any stdlib "
        "reverse helper."
    ),
    forbidden_per_lang={
        "python": r"\.reverse\(|reversed\(|\[::-1\]",
        "go": r"slices\.Reverse|sort\.Reverse",
        "cpp": r"std::reverse|reverse_iterator",
        "rust": r"\.reverse\(\)|\.rev\(\)",
        "zig": r"std\.mem\.reverse",
    },
)

# ---------------- 82_sigmoid ----------------
sig_setup = (
    "mkdir -p /tmp/eval_sig && "
    "printf '5\\n3\\n0 0 0\\n5\\n-2 -1 0 1 2\\n3\\n1000 -1000 0\\n4\\n10 -10 0.5 -0.5\\n2\\n100 -100\\n' "
    "> /tmp/eval_sig/input.txt && "
    "cat > /tmp/eval_sig/expected.txt <<'EOF'\n"
    "0.500000000 0.500000000 0.500000000\n"
    "0.119202922 0.268941421 0.500000000 0.731058579 0.880797078\n"
    "1.000000000 0.000000000 0.500000000\n"
    "0.999954602 0.000045398 0.622459331 0.377540669\n"
    "1.000000000 0.000000000\n"
    "EOF"
)
sig = variant_template(
    task_id="82_sigmoid",
    slug="82_sigmoid",
    name="Numerically stable sigmoid",
    difficulty="easy",
    category="LLM / ML",
    subcategory="Activations",
    max_iter=10,
    dest_dir="/tmp/eval_sig",
    file_stem="sig",
    setup=sig_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case: line A is N (>= 1). Line B is N "
        "space-separated floats. For each case, output the element-wise sigmoid "
        "σ(x) = 1/(1 + e^-x) for each value, formatted as `%.9f`, space-separated "
        "on one line. Use the numerically stable form: for x >= 0, 1/(1+exp(-x)); "
        "for x < 0, exp(x)/(1+exp(x)) — so sigmoid(1000) doesn't overflow and "
        "sigmoid(-1000) doesn't underflow to NaN."
    ),
    forbidden_per_lang={},
)

# ---------------- 92_popcount ----------------
pop_setup = (
    "mkdir -p /tmp/eval_pop && "
    "printf '10\\n0\\n1\\n2\\n3\\n7\\n255\\n4294967295\\n1024\\n12345\\n9223372036854775807\\n' "
    "> /tmp/eval_pop/input.txt && "
    "printf '0\\n1\\n1\\n2\\n3\\n8\\n32\\n1\\n6\\n63\\n' > /tmp/eval_pop/expected.txt"
)
pop = variant_template(
    task_id="92_popcount",
    slug="92_popcount",
    name="Population count (manual implementation)",
    difficulty="easy",
    category="Performance & HW Opt",
    subcategory="Bit-twiddling",
    max_iter=10,
    dest_dir="/tmp/eval_pop",
    file_stem="pop",
    setup=pop_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each next line is a non-negative integer "
        "(may be up to 64-bit unsigned, i.e. up to 2^63-1). For each, output the "
        "number of set bits on its own line. Implement popcount yourself — no "
        "language-builtin popcount intrinsic."
    ),
    forbidden_per_lang={
        "python": r"\.bit_count\(|bin\(.*\)\.count\(",
        "go": r"bits\.OnesCount",
        "c": r"__builtin_popcount",
        "cpp": r"__builtin_popcount|std::popcount",
        "rust": r"\.count_ones\(",
        "zig": r"@popCount",
    },
)

# ---------------- 100_constant_time_compare ----------------
ct_setup = (
    "mkdir -p /tmp/eval_ct && "
    "printf '6\\nhello\\nhello\\nhello\\nworld\\nabc\\nabd\\nabc\\nabcd\\n\\n\\n0123456789\\n0123456789\\n' "
    "> /tmp/eval_ct/input.txt && "
    "printf 'true\\nfalse\\nfalse\\nfalse\\ntrue\\ntrue\\n' > /tmp/eval_ct/expected.txt"
)
ct = variant_template(
    task_id="100_constant_time_compare",
    slug="100_constant_time_compare",
    name="Constant-time string equality",
    difficulty="easy",
    category="Security",
    subcategory="Side-channel safety",
    max_iter=10,
    dest_dir="/tmp/eval_ct",
    file_stem="ct",
    setup=ct_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case is two consecutive lines (a "
        "and b — strings, may be empty). For each case, output 'true' if a == b "
        "else 'false', on its own line. Implement a constant-time comparison: "
        "loop over all bytes (length-padded, length mismatch sets a 'differ' flag) "
        "and OR a XOR-accumulator. Do NOT short-circuit on first difference. "
        "Do NOT call any stdlib constant-time / timing-safe equality helper."
    ),
    forbidden_per_lang={
        "python": r"hmac\.compare_digest|secrets\.compare_digest",
        "go": r"subtle\.ConstantTime|crypto/subtle",
        "c": r"\bmemcmp\b|\bstrcmp\b",
        "cpp": r"\bmemcmp\b|\bstrcmp\b",
        "rust": r"subtle::|constant_time_eq",
        "zig": r"std\.crypto\.timing_safe|std\.crypto\.utils\.timingSafeEql",
    },
)


for task in [dot, rev, sig, pop, ct]:
    path = os.path.join(REPO, "evals/tasks", f"{task['id']}.json")
    with open(path, "w") as f:
        json.dump(task, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {path}  ({len(task['variants'])} variants)")
