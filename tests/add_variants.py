"""
Add Rust (e) and Zig (f) variants to the 13 multi-language eval tasks.
For 122_gemm_blocked, the convention shifts (no python or cpp 'd'): existing
a=go, b=c, c=cpp; we append d=rust, e=zig.
"""
import json
import os

TASKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals", "tasks")

# task_id -> {dir, bin, ext_py, has_validation_check_py, ...}
# We'll derive everything from an existing variant in the JSON itself.

# tasks needing rust+zig (no rust yet): 51, 52, 61, 65, 122, 158, 159
# tasks needing zig only (already have rust): 19, 31, 73, 74, 148, 155

# Per-task config: source filename for each language and how the validation
# differs from the existing 'd' (cpp) variant
TASK_CONFIG = {
    "19_three_way_quicksort": {
        "dir": "/tmp/eval_qs",
        "bin": "qs",
        "src_py": "qs.py",
        "src_go": "qs.go",
        "src_c": "qs.c",
        "src_cpp": "qs.cpp",
        "src_rs": "qs.rs",
        "src_zig": "qs.zig",
        "validation_type": "diff",
    },
    "31_is_power_of_two": {
        "dir": "/tmp/eval_pow2",
        "bin": "pow2",
        "src_py": "pow2.py",
        "src_go": "pow2.go",
        "src_c": "pow2.c",
        "src_cpp": "pow2.cpp",
        "src_rs": "pow2.rs",
        "src_zig": "pow2.zig",
        "validation_type": "diff",
    },
    "51_toposort": {
        "dir": "/tmp/eval_topo",
        "bin": "topo",
        "src_rs": "topo.rs",
        "src_zig": "topo.zig",
        "validation_type": "checkpy",
    },
    "52_unionfind": {
        "dir": "/tmp/eval_uf",
        "bin": "uf",
        "src_rs": "uf.rs",
        "src_zig": "uf.zig",
        "validation_type": "diff",
    },
    "61_extgcd": {
        "dir": "/tmp/eval_xgcd",
        "bin": "xgcd",
        "src_rs": "xgcd.rs",
        "src_zig": "xgcd.zig",
        "validation_type": "checkpy",
    },
    "65_miller_rabin": {
        "dir": "/tmp/eval_mr",
        "bin": "mr",
        "src_rs": "mr.rs",
        "src_zig": "mr.zig",
        "validation_type": "diff",
    },
    "73_count_vowels": {
        "dir": "/tmp/eval_vowels",
        "bin": "vowels",
        "src_rs": "vowels.rs",
        "src_zig": "vowels.zig",
        "validation_type": "diff",
    },
    "74_palindrome": {
        "dir": "/tmp/eval_pal",
        "bin": "pal",
        "src_rs": "pal.rs",
        "src_zig": "pal.zig",
        "validation_type": "diff",
    },
    "122_gemm_blocked": {
        "dir": "/tmp/eval_gemm",
        "bin": "gemm",
        "src_rs": "gemm.rs",
        "src_zig": "gemm.zig",
        "validation_type": "checkpy",
    },
    "148_convex_hull": {
        "dir": "/tmp/eval_hull",
        "bin": "hull",
        "src_rs": "hull.rs",
        "src_zig": "hull.zig",
        "validation_type": "diff",
    },
    "155_tonelli_shanks": {
        "dir": "/tmp/eval_tonelli",
        "bin": "ts",
        "src_rs": "ts.rs",
        "src_zig": "ts.zig",
        "validation_type": "checkpy",
    },
    "158_karatsuba_bytes": {
        "dir": "/tmp/eval_karat",
        "bin": "karat",
        "src_rs": "karat.rs",
        "src_zig": "karat.zig",
        "validation_type": "diff",
    },
    "159_ntt_convolution": {
        "dir": "/tmp/eval_ntt",
        "bin": "ntt",
        "src_rs": "ntt.rs",
        "src_zig": "ntt.zig",
        "validation_type": "diff",
    },
}

def make_validation(directory, bin_name, src_name, lang, vtype):
    """Build the bash validation script for a given language."""
    cd = f"cd {directory}"
    if lang == "rust":
        compile_cmd = f"rustc -O {src_name} -o {bin_name}"
    elif lang == "zig":
        # We use ReleaseFast for performance-sensitive tasks (gemm, karat, ntt);
        # debug build is fine elsewhere but ReleaseFast doesn't hurt.
        compile_cmd = f"zig build-exe -O ReleaseFast {src_name}"
    else:
        raise ValueError(lang)

    run_cmd = f"./{bin_name} < input.txt > out.txt"

    if vtype == "diff":
        check = "diff -u expected.txt out.txt && echo OK"
    else:
        check = "python3 check.py"

    return f"set -e; {cd} && {compile_cmd} && {run_cmd} && {check}"


def make_task_text(base_task, lang, src_name, bin_name, vtype):
    """Adapt the existing cpp variant 'task' field for the new language."""
    # Patterns we substitute. Rather than editing the existing text, we
    # write a simple, language-specific instruction that tells the model
    # what file to create and how it'll be built.
    if lang == "rust":
        return (
            f"Create {base_task['_dir']}/{src_name} — same I/O contract as the other "
            f"variants (see Python/Go/C/C++ siblings). Idiomatic Rust 2021+. "
            f"Will be compiled with `rustc -O {src_name} -o {bin_name}` and tested "
            f"by piping `input.txt` through stdin and checking against `expected.txt` "
            f"(or via a check.py validator)."
        )
    else:  # zig
        return (
            f"Create {base_task['_dir']}/{src_name} — same I/O contract as the other "
            f"variants. Use Zig 0.16+ idioms: `pub fn main(init: std.process.Init) !void`, "
            f"`std.Io.File.stdin()` / `stdout()` for I/O, `takeDelimiter('\\n')` (returns "
            f"optional, null at EOF) or `appendRemainingUnlimited` to slurp stdin. "
            f"Will be compiled with `zig build-exe -O ReleaseFast {src_name}` and tested "
            f"by piping `input.txt` through stdin and checking against `expected.txt` "
            f"(or via a check.py validator)."
        )


def main():
    for task_id, cfg in TASK_CONFIG.items():
        path = os.path.join(TASKS_DIR, f"{task_id}.json")
        with open(path) as f:
            t = json.load(f)
        existing_ids = {v["id"] for v in t["variants"]}
        existing_langs = {v.get("language") for v in t["variants"]}
        # Find the first variant to clone setup/cleanup from
        proto = t["variants"][0]
        # _dir hint for task text
        cfg["_dir"] = cfg["dir"]
        # Determine next IDs
        next_letters = "abcdefghijk"
        used = sorted(existing_ids)
        next_id_iter = (l for l in next_letters if l not in existing_ids)

        added = []
        if "rust" not in existing_langs:
            new_id = next(next_id_iter)
            entry = {
                "id": new_id,
                "language": "rust",
                "setup": proto["setup"],
                "task": make_task_text(cfg, "rust", cfg["src_rs"], cfg["bin"], cfg["validation_type"]),
                "validation": {
                    "type": "bash",
                    "script": make_validation(cfg["dir"], cfg["bin"], cfg["src_rs"], "rust", cfg["validation_type"])
                },
                "cleanup": proto["cleanup"],
            }
            t["variants"].append(entry)
            existing_ids.add(new_id)
            added.append(("rust", new_id))
        if "zig" not in existing_langs:
            new_id = next(next_id_iter)
            entry = {
                "id": new_id,
                "language": "zig",
                "setup": proto["setup"],
                "task": make_task_text(cfg, "zig", cfg["src_zig"], cfg["bin"], cfg["validation_type"]),
                "validation": {
                    "type": "bash",
                    "script": make_validation(cfg["dir"], cfg["bin"], cfg["src_zig"], "zig", cfg["validation_type"])
                },
                "cleanup": proto["cleanup"],
            }
            t["variants"].append(entry)
            existing_ids.add(new_id)
            added.append(("zig", new_id))

        if added:
            with open(path, 'w') as f:
                json.dump(t, f, indent=2)
                f.write("\n")
            print(f"{task_id}: added {added}")
        else:
            print(f"{task_id}: no change")

main()
