#!/usr/bin/env python3
"""Writes evals/tasks/168_linearizability_kv.json from gen.py + cases.json.

The validator of every variant embeds gen.py verbatim, the 40 history specs
and their expected verdicts, regenerates the hidden input in memory, checks
its sha256 (fail closed on generator drift) and feeds it over stdin. Nothing
but the samples ever lands in /tmp/eval_linz.

Run: python3 build_task.py   (after build_cases.py)
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
D = "/tmp/eval_linz"

GEN = open(os.path.join(HERE, "gen.py")).read()
CASES = json.load(open(os.path.join(HERE, "cases.json")))

LANGS = [
    # id, language, ext, build argv (cwd = D), run argv, build line for the prompt
    ("a", "python", "py", None, None, "Run: python3 linz.py < input."),
    ("b", "go", "go", ["go", "build", "-o", "linz", "linz.go"], ["./linz"],
     "Single file, package main, standard library only. Build: go build -o linz linz.go"),
    ("c", "c", "c", ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-o", "linz", "linz.c"], ["./linz"],
     "Single file, C11, libc only. Build: gcc -O2 -std=c11 -Wall -Wextra -o linz linz.c"),
    ("d", "cpp", "cpp", ["g++", "-O2", "-std=c++17", "-Wall", "-Wextra", "-o", "linz", "linz.cpp"], ["./linz"],
     "Single file, C++17, standard library only (no <bits/stdc++.h>). Build: g++ -O2 -std=c++17 -Wall -Wextra -o linz linz.cpp"),
    ("e", "rust", "rs", ["rustc", "-O", "--edition", "2021", "-o", "linz", "linz.rs"], ["./linz"],
     "Single file, std only, no crates. Build: rustc -O --edition 2021 -o linz linz.rs"),
    ("f", "zig", "zig", ["zig", "build-exe", "-O", "ReleaseFast", "linz.zig", "-femit-bin=linz"], ["./linz"],
     "Single file, std only (compiler: zig 0.16.0). Build: zig build-exe -O ReleaseFast linz.zig -femit-bin=linz"),
]

SAMPLE_IN = """3
5
0 10 PUT 1 5
2 3 GET 1 NIL
4 12 GET 1 5
11 14 CAS 1 5 6 OK
15 16 GET 1 6
3
0 4 PUT 7 1
5 9 PUT 7 2
10 11 GET 7 1
4
1 2 PUT 3 8
0 ? DEL 3
3 ? GET 3
6 7 GET 3 NIL
"""
SAMPLE_OUT = "LINEARIZABLE\nVIOLATION\nLINEARIZABLE\n"

TASK = """Create {dest}: a linearizability checker for recorded histories of a concurrent key-value store.

STORE. Keys and values are integers. Initially every key is absent. Sequential semantics:
- PUT k v: sets k to v.
- GET k: returns the value of k, or NIL if k is absent.
- DEL k: makes k absent (no effect if already absent).
- CAS k x y: if k is present and its value equals x, sets k to y and returns OK; otherwise changes nothing and returns FAIL.

HISTORY. Every operation has an integer invocation time c, and either a response time r (c <= r) with its observed result, or no response at all (PENDING: the client crashed or timed out; its result is unknown and it may or may not have taken effect).

A history is LINEARIZABLE iff there exists a sequence L that contains every completed operation exactly once plus any subset (possibly empty) of the pending operations, each at most once, such that:
 (1) executing L in order, from the initial state, with the sequential semantics above gives every completed operation exactly its recorded result (pending operations' results are unconstrained); and
 (2) for any two operations A and B in L, if A has a response time and A.r < B.c, then A comes before B in L.
Times are compared strictly: when A.r == B.c the two operations are concurrent and may appear in either order. Otherwise the history is a VIOLATION.

INPUT (stdin). Line 1: T, the number of histories. Each history is a line N followed by N operation lines, in arbitrary order (not sorted by time). Tokens are separated by single spaces.
  c r PUT k v
  c r GET k v        (v is a value or the word NIL)
  c r DEL k
  c r CAS k x y RES  (RES is OK or FAIL)
A pending operation has the literal ? in place of r and no result token: "c ? PUT k v", "c ? GET k", "c ? DEL k", "c ? CAS k x y".
Bounds: 1 <= T <= 50; 1 <= N <= 2000; 0 <= c, r < 2^31; 0 <= k <= 999999; 1 <= v, x, y <= 10^9; at most 64 distinct keys and at most 48 pending operations per history; at any instant at most 12 completed operations are in progress (invoked and not yet responded).

OUTPUT (stdout). Exactly T lines, in input order: LINEARIZABLE or VIOLATION.

Samples: {d}/sample_input.txt -> {d}/sample_output.txt. The validator runs a hidden input of 40 histories (both verdicts, up to 2000 operations and dozens of pending operations each; about 0.8 MB) in a single process with a 6-second CPU limit, and every verdict must be exact.
{build}"""

VALIDATOR = r'''import hashlib, json, os, subprocess, sys
import vlib
# The harness feeds this script on stdin (python -I -S -) in a private cwd, so
# the oracle below is in no argv and no file. The solution runs in its own
# process group under vlib.run_solution: a CPU budget over its whole tree (load
# on the host does not eat it), fork refused, and every limit scaled by
# OPENBEAST_TIMEOUT_SCALE.
D = "/tmp/eval_linz"
LANG = @LANG@
SRC = @SRC@
BUILD = @BUILD@
CASES = json.loads(@CASES@)
EXPECT = @EXPECT@
SHA = @SHA@
CPU_S = 6          # solution CPU budget at scale 1 (refs: 0.01-0.5 s)
WALL_S = 15        # solution wall budget at scale 1 (the CPU budget is the gate)
BUILD_S = 20       # compiler wall budget at scale 1
GEN = @GEN@
g = {}
exec(compile(GEN, "gen168", "exec"), g)
src = os.path.join(D, SRC)
if not os.path.isfile(src):
    print("missing " + src); sys.exit(1)
env = vlib.clean_env()
if BUILD:
    try:
        os.remove(os.path.join(D, "linz"))
    except OSError:
        pass
    try:
        b = subprocess.run(BUILD, cwd=D, capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=vlib.t(BUILD_S))
    except subprocess.TimeoutExpired:
        print("build timed out (%ds)" % vlib.t(BUILD_S)); sys.exit(1)
    if b.returncode != 0 or not os.path.isfile(os.path.join(D, "linz")):
        print("build failed: " + (b.stderr or b.stdout)[-300:]); sys.exit(1)
    run = [os.path.join(D, "linz")]
    # Untimed warm-up exec of the fresh binary: the first exec on macOS pays
    # the Gatekeeper scan. Its output is ignored.
    vlib.run_solution(run, input="1\n1\n0 1 GET 1 NIL\n", cpu_s=2, wall_s=20, cwd=D, env=env)
else:
    run = vlib.python_cmd(src)
inp = g["build"]([s if isinstance(s, str) else tuple(s) for s in CASES])
if hashlib.sha256(inp.encode()).hexdigest() != SHA:
    print("internal: hidden-input generator drift"); sys.exit(2)
r = vlib.run_solution(run, input=inp, cpu_s=CPU_S, wall_s=WALL_S, cwd=D, env=env)
if r.verdict in ("cpu", "wall"):
    print("solution timed out (%s: %s) on the hidden histories" % (r.verdict, r.reason)); sys.exit(1)
if r.verdict != "ok":
    print("solution refused (%s: %s)" % (r.verdict, r.reason)); sys.exit(1)
if r.returncode != 0:
    print("solution exited with code %d" % r.returncode); sys.exit(1)
lines = r.stdout.split("\n")
if lines and lines[-1] == "":
    lines.pop()
got = [x.rstrip("\r") for x in lines]
want = ["LINEARIZABLE" if e == "L" else "VIOLATION" for e in EXPECT]
if len(got) != len(want):
    print("expected %d verdict lines, got %d" % (len(want), len(got))); sys.exit(1)
for i, (x, y) in enumerate(zip(got, want)):
    if x.strip() != y:
        print("wrong verdict at case %d" % i); sys.exit(1)
print("OK: %d/%d verdicts exact" % (len(want), len(want)))
'''


def shq(s):
    """printf-safe single-quoted shell literal"""
    return "'" + s.replace("\\", "\\\\").replace("%", "%%").replace("\n", "\\n").replace("'", "'\\''") + "'"


def main():
    setup = (f"rm -rf {D} && mkdir -p {D} && printf {shq(SAMPLE_IN)} > {D}/sample_input.txt"
             f" && printf {shq(SAMPLE_OUT)} > {D}/sample_output.txt")
    variants = []
    for vid, lang, ext, build, run, buildline in LANGS:
        dest = f"{D}/linz.{ext}"
        script = (VALIDATOR
                  .replace("@LANG@", repr(lang))
                  .replace("@SRC@", repr(f"linz.{ext}"))
                  .replace("@BUILD@", repr(build))
                  .replace("@CASES@", repr(json.dumps(CASES["specs"], separators=(",", ":"))))
                  .replace("@EXPECT@", repr(CASES["expect"]))
                  .replace("@SHA@", repr(CASES["sha256"]))
                  .replace("@GEN@", repr(GEN)))
        variants.append({
            "id": vid,
            "language": lang,
            "setup": setup,
            "task": TASK.format(dest=dest, d=D, build=buildline),
            "validation": {"type": "python", "script": script},
            "cleanup": f"rm -rf {D}",
            "ref_stem": "168/linz",
            "dest": dest,
            "est_s": 165,
            "max_wall_s": 900,
            "role": "scored",
            "difficulty": "hard",
        })
    task = {
        "id": "168_linearizability_kv",
        "name": "Linearizability checker over KV histories",
        "difficulty": "hard",
        "category": "Distributed / SysDesign",
        "subcategory": "Consistency checking",
        "max_iter": 25,
        "est_s": 165,
        "max_wall_s": 900,
        "role": "scored",
        "variants": variants,
    }
    out = os.path.join(REPO, "evals", "tasks", "168_linearizability_kv.json")
    with open(out, "w") as f:
        json.dump(task, f, indent=1)
        f.write("\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
