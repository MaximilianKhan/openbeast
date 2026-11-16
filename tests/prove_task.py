#!/usr/bin/env python3
"""Model-free proof that an eval unit is solvable AND correctly validated
(v5 authoring gate, brief §6.3).

It drives the harness's OWN lifecycle (run_eval.run_cleanup -> run_setup ->
[drop a solution at the unit's dest] -> run_pre_validate -> run_validation
-> run_cleanup), so the 30 s x scale validator timeout, the rlimits and the
reaping are exactly what a measured unit gets. No model is involved.

A unit is PROVEN only when every arm comes out as required:

  ref     the reference solution passes N/N (3; 5 when the unit is timed)
          — and its slowest run (the cold compile included) fits the 15 s
          validator budget at scale 1
  wrong   >= 2 plausible wrong solutions FAIL, and each failure's output
          matches its declared failure class (a regex): a wrong solution
          that dies on a lint or a compile error proves nothing about the
          semantic check it was written to probe
  empty   no solution at all fails
  leak    no oracle-like file (expected*, answer*, check*, oracle*, hidden*,
          solution*) sits in the unit's /tmp/eval_* dirs after setup or
          after validation; and a declared leak-probe solution (one that
          prints whatever fixtures it can find) fails
  slow    for a timed unit (or one that ships a slow solution): the slow
          solution fails, by the validator's OWN per-run limit — not the
          harness's validator_timeout — within the harness budget
  scale2  the reference passes again at --scale 2.0 (the --jobs 4 budget)
  toolchain  (compiled units) a toolchain planted AFTER the harness pinned
          its own is never run, and a solution that does not compile fails:
          toolchain:path puts a fake of every compiler first on PATH (each
          one compiles the REFERENCE in place of the solution, the attack
          that passed 36_c); toolchain:goenv (go) plants a go.mod `go 1.99.0`
          in the fixture dir, a go env `GOTOOLCHAIN=path` in $HOME and a fake
          `go1.99.0` on PATH
  lint    no coreutils `timeout` (absent on macOS), no <bits/stdc++.h>, no
          zig API tutoring in a zig prompt; and validator isolation: no child
          Python started with `-c SRC` (its argv is readable by the
          solution), and every fixed time limit scales with
          OPENBEAST_TIMEOUT_SCALE (vlib.t), which run_validation exports
          (1.0 for every arm here, 2.0 for the scale2 arm)

Where the solutions come from (in precedence order):
  1. flags: --ref FILE --dest PATH [--wrong FILE::REGEX]... [--slow FILE[::REGEX]]
     [--leak FILE]
  2. the unit's task JSON: `ref_stem` (path under evals/refs/, no extension;
     the language picks it: py go c cpp rs zig sh; or a stem that names an
     existing file, whose extension the siblings then share) and `dest` (where the task
     says the solution lives). Siblings of the ref in its directory:
       wrong_<name>.<ext>   (or <stem>.wrong_<name>.<ext>)   wrong solutions
       slow.<ext>           (or <stem>.slow.<ext>)           slow solution
       leak.<ext>           (or <stem>.leak.<ext>)           leak probe
     and the failure classes, from the task JSON's `prove` block and/or a
     prove.json next to the ref:
       {"expect": {"wrong_trunc32.c": "mismatch at case"},
        "slow_expect": "timed out", "timed": true,
        "units": {"<unit id>": {...per-unit override...}}}

usage:
  prove_task.py --unit 163_bigint_divmod_f            # discover from the JSON
  prove_task.py --unit 92_popcount_c --ref r.c --dest /tmp/eval_pop/pop.c \\
                --wrong w1.c::'mismatch' --wrong w2.c::'mismatch'
  prove_task.py --suite v5                            # every manifest unit
                                                      # that declares refs
exit 0 = every requested unit PROVEN.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG_EXT = {"python": "py", "go": "go", "c": "c", "cpp": "cpp", "rust": "rs",
            "zig": "zig", "shell": "sh"}
VALIDATOR_BUDGET_S = 15.0          # at scale 1, including a cold zig compile
COMPILED = frozenset({"go", "c", "cpp", "rust", "zig"})
NOT_SOURCE = "this is not a program in any language the validator compiles\n"
LEAK_NAME_RE = re.compile(r"^(expected|answer|check|oracle|hidden|solution)", re.I)
COREUTILS_TIMEOUT_RE = re.compile(r"(?:^|[\s;&|(`])timeout\s+-?[\w.]+\s", re.M)
BITS_RE = re.compile(r"#\s*include\s*<bits/stdc\+\+\.h>")
# A validator that starts a child Python with `-c SRC` puts SRC in that
# child's argv, where the solution can read it (`ps -o args= -p $PPID`).
PY_DASH_C_RE = re.compile(
    r"""(?:sys\.executable|['"](?:[\w./-]*/)?python3?(?:\.\d+)?['"])\s*,\s*"""
    r"""(?:['"]-[A-Za-z]+['"]\s*,\s*)*['"]-[A-Za-z]*c['"]""")
SHELL_PY_DASH_C_RE = re.compile(r"\bpython3?(?:\.\d+)?\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*c\s")
# A fixed time limit in a validator: timeout=10, RUN_TIMEOUT = 8, LIMIT_S = 4.
FIXED_LIMIT_RE = re.compile(
    r"(?:\btimeout\s*=\s*\d|\b[A-Z_]*(?:TIMEOUT|LIMIT|BUDGET)[A-Z_]*\s*=\s*\d)")
SCALE_REF_RE = re.compile(r"OPENBEAST_TIMEOUT_SCALE|\bvlib\b")


def _load_harness(repo: str, tasks_dir: str | None):
    sys.path.insert(0, os.path.join(repo, "evals"))
    sys.path.insert(0, os.path.join(repo, "agents"))
    import run_eval as R  # noqa: PLC0415
    import suite_manifest as M  # noqa: PLC0415
    if tasks_dir:
        R.TASKS_DIR = tasks_dir
    return R, M


def fixture_dirs(task: dict) -> list[str]:
    """The unit's /tmp/eval_* directories, as run_eval announces them."""
    text = " ".join(str(task.get(k, "")) for k in ("task", "validation", "setup", "cleanup"))
    dirs = set()
    for p in re.findall(r"/tmp/eval[\w\-.]*", text):
        dirs.add(p.rstrip("."))
    return sorted(dirs)


def leaked_files(task: dict) -> list[str]:
    out = []
    for d in fixture_dirs(task):
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            out += [os.path.join(root, f) for f in files if LEAK_NAME_RE.match(f)]
    return sorted(out)


def lint_unit(task: dict, M, refs: list[str]) -> list[str]:
    """Static fixture rules (brief §6.3). [] = clean."""
    problems = []
    lang = M.norm_lang(task.get("language"))
    for k in ("setup", "pre_validate", "cleanup"):
        if COREUTILS_TIMEOUT_RE.search(" " + str(task.get(k) or "")):
            problems.append(f"{k} uses coreutils `timeout` (absent on macOS)")
    v = task.get("validation") or {}
    vscript = v.get("script", "") or ""
    if v.get("type", "bash") == "bash" and COREUTILS_TIMEOUT_RE.search(" " + vscript):
        problems.append("validation uses coreutils `timeout` (absent on macOS)")
    problems += isolation_problems(v.get("type", "bash"), vscript)
    for k in ("task", "setup"):
        if BITS_RE.search(str(task.get(k) or "")):
            problems.append(f"{k} names <bits/stdc++.h> (fails on clang)")
    for r in refs:
        try:
            if BITS_RE.search(open(r, errors="replace").read()):
                problems.append(f"{os.path.basename(r)} includes <bits/stdc++.h>")
        except OSError:
            pass
    if lang == "zig":
        bad = M.zig_tutoring_violations(task.get("task", ""))
        if bad:
            problems.append(f"zig prompt carries API tutoring {bad} (banned in v5)")
    return problems


def isolation_problems(vtype: str, script: str) -> list[str]:
    """Validator-isolation lint (evals/README.md, v5 "Validator isolation").

    The harness feeds the validator on stdin and exports
    OPENBEAST_TIMEOUT_SCALE; these catch a validator that reopens a hole on
    its own: a child Python started with `-c SRC` (SRC lands in a readable
    argv), or a fixed time limit that ignores the scale."""
    out = []
    if vtype == "python" and PY_DASH_C_RE.search(script):
        out.append("validator starts a child python with `-c`: its source is readable "
                   "from argv; feed it on stdin to `python -I -S -`")
    if vtype != "python" and SHELL_PY_DASH_C_RE.search(script):
        out.append("validator runs `python -c`: its source is readable from argv; "
                   "use a quoted heredoc to `python3 -I -`")
    if FIXED_LIMIT_RE.search(script) and not SCALE_REF_RE.search(script):
        out.append("validator has a fixed time limit but never reads OPENBEAST_TIMEOUT_SCALE "
                   "(use vlib.t(seconds))")
    for line, limit in unscaled_time_gates(vtype, script):
        out.append(f"validator compares a measured time with a fixed {limit} s (line {line}) "
                   "that ignores OPENBEAST_TIMEOUT_SCALE (use vlib.t(seconds))")
    return out


_CLOCKS = {"time", "monotonic", "perf_counter", "process_time", "monotonic_ns",
           "perf_counter_ns", "time_ns"}
_HEREDOC_RE = re.compile(r"<<-?\s*'?\"?(\w+)'?\"?[^\n]*\n(.*?)\n\1\s*$", re.S | re.M)


def _python_sources(vtype: str, script: str) -> list[str]:
    """The Python a validator runs: the script itself, or for a shell
    validator the bodies of its heredocs (`python3 - <<'EOF' ... EOF`)."""
    if vtype == "python":
        return [script]
    return [m.group(2) for m in _HEREDOC_RE.finditer(script)]


def unscaled_time_gates(vtype: str, script: str) -> list[tuple[int, float]]:
    """(line, limit) for every comparison of a measured wall time with a bare
    number: `el = time.time() - t0; assert el < 1.5`. FIXED_LIMIT_RE only
    sees `timeout=N` / `*LIMIT = N` assignments, so these gates (158, 159,
    146, 147, 153 in v5.0) passed the lint while ignoring --scale (review
    1116). A scaled bound (`1.5 * scale`, vlib.t(1.5)) is not a bare number."""
    import ast  # noqa: PLC0415

    def is_clock(node) -> bool:
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        return ((isinstance(f, ast.Attribute) and f.attr in _CLOCKS)
                or (isinstance(f, ast.Name) and f.id in _CLOCKS))

    def is_delta(node, names) -> bool:
        if isinstance(node, ast.Name):
            return node.id in names
        return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub)
                and is_clock(node.left))

    def number(node):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            node = node.operand
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return float(node.value)
        return None

    found = []
    for src in _python_sources(vtype, script):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_delta(node.value, set()):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for i, op in enumerate(node.ops):
                a, b = operands[i], operands[i + 1]
                # an upper bound on a delta: delta < N, delta <= N, N > delta, N >= delta
                if isinstance(op, (ast.Lt, ast.LtE)) and is_delta(a, names) and number(b) is not None:
                    found.append((node.lineno, number(b)))
                elif isinstance(op, (ast.Gt, ast.GtE)) and is_delta(b, names) \
                        and number(a) is not None:
                    found.append((node.lineno, number(a)))
    return found


def _ext(task: dict, M) -> str:
    return LANG_EXT.get(M.norm_lang(task.get("language")), "py")


def discover(task: dict, M, refs_dir: str) -> dict:
    """Solutions + failure classes declared for a unit (see module doc)."""
    stem = task.get("ref_stem")
    dest = task.get("dest")
    if not stem or not dest:
        return {}
    ext = _ext(task, M)
    base = os.path.join(refs_dir, stem)
    ref = base if os.path.isfile(base) else f"{base}.{ext}"
    if ref == base and os.path.splitext(base)[1]:
        # A stem that names its own file (an answers.json for a Q&A task,
        # `195_x/ref.s` for an assembly unit whose task language is python)
        # sets the siblings' extension too, instead of the task's language.
        ext = os.path.splitext(base)[1][1:]
    d, b = os.path.dirname(ref), os.path.basename(stem).split(".")[0]
    decl: dict = {}
    # PROVE.json too: macOS matched it case-insensitively, Linux did not, so
    # 163's failure classes vanished on the rig and every wrong arm read as
    # "failure class not declared".
    for name in ("prove.json", "PROVE.json"):
        pj = os.path.join(d, name)
        if os.path.isfile(pj):
            with open(pj) as f:
                decl.update(json.load(f))
            break
    decl.update(task.get("prove") or {})
    per = (decl.get("units") or {}).get(task["id"]) or {}
    expect = dict(decl.get("expect") or {})
    expect.update(per.get("expect") or {})

    def sib(kind: str) -> list[str]:
        pats = [os.path.join(d, f"{kind}*.{ext}"), os.path.join(d, f"{b}.{kind}*.{ext}"),
                os.path.join(d, f"{b}_{kind}*.{ext}")]
        return sorted({p for pat in pats for p in glob.glob(pat)})

    wrong = []
    for w in sib("wrong"):
        name = os.path.basename(w)
        rx = expect.get(name) or expect.get(os.path.splitext(name)[0])
        wrong.append((w, rx))
    slow = sib("slow")
    leak = sib("leak")
    return {"ref": ref, "dest": dest, "wrong": wrong,
            "slow": slow[0] if slow else None,
            "slow_expect": per.get("slow_expect") or decl.get("slow_expect"),
            "leak": leak[0] if leak else None,
            "timed": bool(per.get("timed", decl.get("timed", False)))}


def family_prover(task: dict, refs_dir: str, spec: dict) -> str | None:
    """The family's own prover (evals/refs/<dir>/prove.py), for a unit whose
    arms this generic prover cannot run: a solution that is an overlay
    DIRECTORY (176), shell scripts executed inside the fixture (179, prove
    mode "exec"), or wrong solutions named in prove.json and kept in a bad/
    subdir (175). Each one drives the same run_eval lifecycle and the same
    arms (ref xN, wrongs with failure classes, empty, leak, slow, scale 2.0).
    None when the generic prover applies."""
    stem = task.get("ref_stem")
    if not stem:
        return None
    script = os.path.join(refs_dir, os.path.dirname(stem), "prove.py")
    if not os.path.isfile(script):
        return None
    ref = spec.get("ref") or ""
    if (not os.path.isfile(ref) or (task.get("prove") or {}).get("mode") == "exec"
            or not spec.get("wrong")):
        return script
    return None


def run_family_prover(script: str, repo: str) -> bool:
    import subprocess  # noqa: PLC0415
    print(f"(arms in a family format: delegating to {os.path.relpath(script, repo)})")
    r = subprocess.run([sys.executable, script], cwd=repo)
    return r.returncode == 0


def prove(R, M, task: dict, spec: dict, repeat: int | None = None,
          strict: bool = True, quiet: bool = False,
          scale: float = 1.0) -> tuple[bool, list[tuple]]:
    """Run every arm for one unit. Returns (proven, rows); a row is
    (verdict_ok, label, passed, seconds, detail)."""
    say = (lambda *_: None) if quiet else print
    silent = lambda *_: None  # noqa: E731
    rows: list[tuple] = []
    dest = spec["dest"]

    # Pin the validator toolchain now, before any arm plants anything.
    pins = R.toolchain_pins()

    def arm(label: str, sol: str | None, scale: float = 1.0, plant=None):
        # The unit's fixture roots are locked from the first cleanup to the
        # last, so a second prover or an eval of the same unit waits instead
        # of deleting this arm's solution mid-validation (review 1116).
        with R.fixture_lock(task, log=print):
            return _arm(sol, scale, plant)

    def _arm(sol: str | None, scale: float, plant):
        R.run_cleanup(task, log=silent)
        if not R.run_setup(task, log=say):
            return None, 0.0, "SETUP FAILED", []
        leaks = leaked_files(task)
        if sol:
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            shutil.copy(sol, dest)
        if plant:
            plant()
        R.run_pre_validate(task, log=silent)
        t0 = time.time()
        ok, out = R.run_validation(task, timeout_scale=scale)
        dt = time.time() - t0
        leaks += [p for p in leaked_files(task) if p not in leaks and p != dest]
        R.run_cleanup(task, log=silent)
        return ok, dt, (out or "").strip(), leaks

    def add(good: bool, label: str, ok, dt: float, detail: str):
        rows.append((good, label, ok, dt, detail))

    timed = spec.get("timed") or bool(spec.get("slow"))
    n = repeat or (5 if timed else 3)
    ref_times = []
    for i in range(n):
        ok, dt, out, leaks = arm(f"ref#{i + 1}", spec["ref"], scale=scale)
        ref_times.append(dt)
        add(ok is True, f"ref#{i + 1}", ok, dt, out.splitlines()[-1][:90] if out else "")
        if leaks:
            add(False, "leak:fixture", None, 0.0, f"oracle-like file(s) in the fixture dir: {leaks[:3]}")
    if ref_times and max(ref_times) > VALIDATOR_BUDGET_S * scale:
        add(False, "budget", None, max(ref_times),
            f"slowest ref validation {max(ref_times):.1f}s > {VALIDATOR_BUDGET_S:.0f}s at scale 1")

    wrong = spec.get("wrong") or []
    if len(wrong) < 2:
        add(False, "wrong:count", None, 0.0, f"{len(wrong)} wrong solution(s); need >= 2")
    for w, rx in wrong:
        ok, dt, out, _ = arm("wrong", w)
        label = f"wrong:{os.path.basename(w)}"
        if ok is not False:
            add(False, label, ok, dt, "a wrong solution PASSED")
        elif R.validator_timed_out(out):
            add(False, label, ok, dt, "failed only by the harness validator timeout")
        elif rx is None:
            add(not strict, label, ok, dt, "failure class not declared (prove.json expect)")
        elif not re.search(rx, out):
            add(False, label, ok, dt, f"failed for the wrong reason (want /{rx}/): "
                                      f"{out.splitlines()[-1][:70] if out else ''}")
        else:
            add(True, label, ok, dt, f"/{rx}/")

    ok, dt, out, _ = arm("empty", None)
    add(ok is False, "empty", ok, dt, out.splitlines()[-1][:90] if out else "")

    lang = M.norm_lang(task.get("language"))
    if lang in COMPILED:
        for label, ok, dt, out, ran in toolchain_arms(arm, task, spec, pins, lang):
            good = ok is False and not ran
            detail = (f"the planted toolchain RAN ({ran[0][:60]})" if ran
                      else (out.splitlines()[-1][:90] if out else ""))
            add(good, label, ok, dt, detail)

    if spec.get("leak"):
        ok, dt, out, _ = arm("leak-probe", spec["leak"])
        add(ok is False, "leak-probe", ok, dt, out.splitlines()[-1][:90] if out else "")

    if timed:
        if not spec.get("slow"):
            add(False, "slow", None, 0.0, "timed unit ships no slow solution")
        else:
            ok, dt, out, _ = arm("slow", spec["slow"])
            budget = R.VALIDATION_TIMEOUT_S
            good = ok is False and not R.validator_timed_out(out) and dt < budget
            rx = spec.get("slow_expect")
            if good and rx and not re.search(rx, out):
                good = False
                out = f"failed for the wrong reason (want /{rx}/): {out[-70:]}"
            add(good, "slow", ok, dt, out.splitlines()[-1][:90] if out else "")

    ok, dt, out, _ = arm("ref@scale2", spec["ref"], scale=2.0)
    add(ok is True, "ref@scale2.0", ok, dt, out.splitlines()[-1][:90] if out else "")

    for problem in lint_unit(task, M, [spec["ref"]] + [w for w, _ in wrong]):
        add(False, "lint", None, 0.0, problem)

    proven = all(r[0] for r in rows)
    for good, label, ok, dt, detail in rows:
        say(f"{'OK ' if good else 'BAD'} {label:30s} passed={ok!s:5s} {dt:5.2f}s  {detail}")
    say(f"{task['id']}: {'PROVEN' if proven else 'NOT PROVEN'}")
    return proven, rows


_FAKE_COMPILER = """#!/bin/sh
# A planted compiler (prove_task toolchain arm): it records that it ran and
# compiles the REFERENCE wherever the solution's file is named.
echo "$0 $*" >> {marker}
for a do
  case "$a" in
    *{dest_base}) set -- "$@" {ref} ;;
    *) set -- "$@" "$a" ;;
  esac
  shift
done
exec {real} "$@"
"""


def _write_fake(path: str, marker: str, dest: str, ref: str, real: str) -> None:
    import shlex  # noqa: PLC0415
    with open(path, "w") as f:
        f.write(_FAKE_COMPILER.format(marker=shlex.quote(marker),
                                      dest_base=os.path.basename(dest),
                                      ref=shlex.quote(os.path.abspath(ref)),
                                      real=shlex.quote(real)))
    os.chmod(path, 0o755)


def toolchain_arms(arm, task: dict, spec: dict, pins: dict, lang: str):
    """The toolchain-capture arms for a compiled unit (review 1116). Yields
    (label, passed, seconds, output, [lines the planted tools logged]).

    Each plant happens after the harness pinned its toolchain, as an agent's
    would. The solution dropped is not a program, so only a captured
    compiler (one that builds the reference instead) could make it pass."""
    import tempfile  # noqa: PLC0415
    dest, ref = spec["dest"], spec["ref"]
    real = {n: p["path"] for n, p in pins.items() if p.get("path")}
    with tempfile.TemporaryDirectory(prefix="prove-tc-") as tmp:
        sol = os.path.join(tmp, "not_a_program")
        with open(sol, "w") as f:
            f.write(NOT_SOURCE)
        marker = os.path.join(tmp, "ran.log")

        def ran() -> list[str]:
            try:
                with open(marker) as f:
                    return f.read().splitlines()
            except OSError:
                return []

        saved = {k: os.environ.get(k) for k in ("PATH", "HOME")}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        # 1. A fake of every pinned compiler, first on PATH.
        fake_bin = os.path.join(tmp, "bin")
        os.makedirs(fake_bin)
        for name, path in real.items():
            _write_fake(os.path.join(fake_bin, name), marker, dest, ref, path)

        def plant_path():
            os.environ["PATH"] = fake_bin + os.pathsep + saved["PATH"]
        try:
            ok, dt, out, _ = arm("toolchain:path", sol, plant=plant_path)
        finally:
            restore()
        yield "toolchain:path", ok, dt, out, ran()

        if lang != "go" or "go" not in real:
            return
        # 2. go.mod `go 1.99.0` + go env GOTOOLCHAIN=path + `go1.99.0` on PATH.
        open(marker, "w").close()
        home = os.path.join(tmp, "home")
        for cfg in (os.path.join(home, "Library", "Application Support", "go"),
                    os.path.join(home, ".config", "go")):
            os.makedirs(cfg)
            with open(os.path.join(cfg, "env"), "w") as f:
                f.write("GOTOOLCHAIN=path\n")
        switch_bin = os.path.join(tmp, "switch")
        os.makedirs(switch_bin)
        _write_fake(os.path.join(switch_bin, "go1.99.0"), marker, dest, ref, real["go"])

        def plant_goenv():
            for d in sorted({os.path.dirname(dest)} | set(fixture_dirs(task))):
                if os.path.isdir(d):
                    with open(os.path.join(d, "go.mod"), "w") as f:
                        f.write("module planted\n\ngo 1.99.0\n")
            os.environ["HOME"] = home
            os.environ["PATH"] = switch_bin + os.pathsep + saved["PATH"]
        try:
            ok, dt, out, _ = arm("toolchain:goenv", sol, plant=plant_goenv)
        finally:
            restore()
        yield "toolchain:goenv", ok, dt, out, ran()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--unit", action="append", default=[], help="unit id (repeatable)")
    ap.add_argument("--suite", help="prove every unit of a v5 manifest that declares refs")
    ap.add_argument("--tasks-dir")
    ap.add_argument("--refs-dir")
    ap.add_argument("--ref")
    ap.add_argument("--dest")
    ap.add_argument("--wrong", action="append", default=[], help="FILE::REGEX (failure class)")
    ap.add_argument("--slow", help="FILE[::REGEX]")
    ap.add_argument("--leak")
    ap.add_argument("--timed", action="store_true")
    ap.add_argument("--repeat", type=int)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="timeout scale for the ref arms (the scale-2.0 arm always runs)")
    ap.add_argument("--lenient", action="store_true",
                    help="accept a wrong solution with no declared failure class")
    ap.add_argument("--json", help="write per-unit verdicts here")
    a = ap.parse_args(argv)

    R, M = _load_harness(a.repo, a.tasks_dir)
    refs_dir = a.refs_dir or os.path.join(a.repo, "evals", "refs")
    tasks = {t["id"]: t for t in R.load_tasks(None)}
    unit_ids = list(a.unit)
    if a.suite:
        man = M.load_manifest(a.suite, tasks=list(tasks.values()), strict=False)
        unit_ids += [man["resolved"][u["id"]] for u in man["units"] if u["id"] in man["resolved"]]
        missing = man.get("resolution_problems") or []
        if missing:
            print(f"{len(missing)} manifest unit(s) have no task file yet (not proven):")
            for p in missing[:10]:
                print(f"  {p}")
    if not unit_ids:
        ap.error("give --unit or --suite")

    verdicts, worst = {}, 0
    for uid in unit_ids:
        task = tasks.get(uid)
        if task is None:
            print(f"{uid}: no such unit")
            verdicts[uid], worst = "missing", 1
            continue
        if a.ref:
            wrong = []
            for w in a.wrong:
                f, _, rx = w.partition("::")
                wrong.append((f, rx or None))
            slow, _, slow_rx = (a.slow or "").partition("::")
            spec = {"ref": a.ref, "dest": a.dest or task.get("dest"), "wrong": wrong,
                    "slow": slow or None, "slow_expect": slow_rx or None,
                    "leak": a.leak, "timed": a.timed}
            if not spec["dest"]:
                ap.error("--ref needs --dest (or a `dest` in the task JSON)")
        else:
            spec = discover(task, M, refs_dir)
            if not spec:
                print(f"{uid}: declares no ref_stem/dest — not proven")
                verdicts[uid], worst = "no refs declared", 1
                continue
        print(f"\n== {uid}")
        fam = None if a.ref else family_prover(task, refs_dir, spec)
        if fam:
            proven = run_family_prover(fam, a.repo)
        else:
            proven, rows = prove(R, M, task, spec, repeat=a.repeat, strict=not a.lenient,
                                 scale=a.scale)
        verdicts[uid] = "PROVEN" if proven else "NOT PROVEN"
        worst = worst or (0 if proven else 1)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(verdicts, f, indent=1)
    if len(unit_ids) > 1:
        n = sum(v == "PROVEN" for v in verdicts.values())
        print(f"\n{n}/{len(verdicts)} unit(s) PROVEN")
    return worst


if __name__ == "__main__":
    sys.exit(main())
