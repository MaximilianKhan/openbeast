#!/usr/bin/env python3
"""One-shot patch for the 13 Zig variant `task` fields.

ALREADY APPLIED. It is kept for provenance, so it defaults to a DRY RUN and
needs --apply to touch anything: it rewrites eval task fixtures, which are
measurement inputs, and it used to rewrite all 13 files unconditionally —
including the ones where no patch matched, so a second run was pure JSON
reformatting churn on the inputs of a live campaign. (Probing it with --help,
which it did not have, was enough to dirty six fixtures.)
"""
import argparse
import json
import os

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TASKS = [
    "19_three_way_quicksort",
    "31_is_power_of_two",
    "51_toposort",
    "52_unionfind",
    "61_extgcd",
    "65_miller_rabin",
    "73_count_vowels",
    "74_palindrome",
    "122_gemm_blocked",
    "148_convex_hull",
    "155_tonelli_shanks",
    "158_karatsuba_bytes",
    "159_ntt_convolution",
]

# This is the broken guidance in every Zig task field today
OLD_GUIDANCE = (
    "Use Zig 0.16+ idioms: `pub fn main(init: std.process.Init) !void`, "
    "`std.Io.File.stdin()` / `stdout()` for I/O, `takeDelimiter('\\n')` "
    "(returns optional, null at EOF) or `appendRemainingUnlimited` to slurp stdin."
)

# Replacement — teaches &fr.interface, init.io, strict-default footguns, std.Io casing
NEW_GUIDANCE = (
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

_ap = argparse.ArgumentParser(
    description=__doc__,
    formatter_class=argparse.RawDescriptionHelpFormatter)
_ap.add_argument("--apply", action="store_true",
                 help="actually rewrite the task files (default: dry run)")
ARGS = _ap.parse_args()

patches = 0
for slug in TASKS:
    path = os.path.join(REPO, "evals/tasks", slug + ".json")
    with open(path) as f:
        data = json.load(f)
        before = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    found_zig = False
    for v in data.get("variants", []):
        if v.get("language") != "zig":
            continue
        found_zig = True
        old = v["task"]
        if OLD_GUIDANCE not in old:
            print(f"WARN {slug}: old guidance not found verbatim in Zig variant {v['id']}")
            print(f"     task starts: {old[:120]}...")
            continue
        new = old.replace(OLD_GUIDANCE, NEW_GUIDANCE)
        v["task"] = new
        patches += 1
        print(f"OK   {slug} variant {v['id']}: patched")

    if not found_zig:
        print(f"SKIP {slug}: no zig variant found")
        continue

    # Write only when this file actually changed, and only under --apply.
    after = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if after == before:
        print(f"SAME {slug}: no change — not rewritten")
        continue
    if not ARGS.apply:
        print(f"WOULD write {slug} ({len(after) - len(before):+d} bytes)")
        continue
    with open(path, "w") as f:
        f.write(after)

print(f"\n{patches} variants patched.")
if not ARGS.apply:
    print("DRY RUN — nothing was written. Re-run with --apply to commit.")
