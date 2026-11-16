"""Pins for the review-1116 fixes to the v5 task families 163-179.

Each test rebuilds one reported attack or defect against the REAL validator
(the script embedded in the task JSON, with its fixed /tmp/eval_* path
swapped for a private tmp dir so the test never touches a live fixture) or
the real vlib / run_eval, and checks it is closed:

  163/167/168  a source that makes the COMPILER read the sealed reference
               (#include of an absolute path, a symlinked source); a hanging
               compiled solution fails on its warm-up, inside the harness's
               validator budget; 163's prompt states the budget it enforces
  173          the oracle absorbs its own undelivered parent.cancel()
  175          the incremental oracle equals the declarative rebuild; the
               argvcheat failure class covers Linux's "forked"
  176          shopd/ with a symlink (to a sealed file, to /dev/zero) is
               refused before anything is copied
  179          a promisor remote planted in .git/config never runs
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import run_eval
import vlib

DARWIN = sys.platform == "darwin"
CC = shutil.which("gcc") or shutil.which("cc")
GIT = shutil.which("git")
TASKS = ROOT / "evals" / "tasks"
REFS = ROOT / "evals" / "refs"


def _variant(task_file: str, vid: str | None = None) -> dict:
    d = json.loads((TASKS / task_file).read_text())
    if vid is None:
        return d
    (v,) = [v for v in d["variants"] if v["id"] == vid]
    return v


def _validate(script: str, fixed: str, private: Path) -> tuple[bool, str]:
    """Run a real validator with its fixed fixture dir replaced."""
    assert fixed in script
    return run_eval.run_validation({"validation": {"type": "python",
                                                   "script": script.replace(fixed, str(private))}})


# --- 163 / 167 / 168: the compiler is graded code's first reader ------------------

def test_task_jsons_match_their_validator_templates():
    for unit in ("163_bigint_divmod", "167_q15_biquad"):
        r = subprocess.run([sys.executable, str(REFS / unit / "sync_validator.py"), "--check"],
                           capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("lang,src", [
    ("c", '#include "/repo/evals/refs/167_q15_biquad/biquad.c"\n'),
    ("c", "#include <../../../repo/evals/refs/x.c>\n"),
    ("c", '#define P "/repo/evals/refs/x.c"\n#include P\n'),
    ("c", '#inc\\\nlude "/x"\n'),
    ("c", '#/* c */include "/x"\n'),
    ("c", '??=include "/x"\n'.replace("??=", "%:")),
    ("c", 'int main(void){return 0;}\n__asm__(".incbin \\"/x\\"");\n'),
    ("cpp", '#include_next "/x"\n'),
    ("rust", 'fn main(){ print!("{}", include_str!("/x")); }\n'),
    ("rust", 'macro_rules! m{($i:ident)=>{$i!("/x")}}\nfn main(){ m!(include_bytes); }\n'),
    ("zig", 'const s = @embedFile("/x");\n'),
    ("go", 'package main\n// #include "/x"\nimport "C"\nfunc main(){}\n'),
])
def test_lint_source_includes_refuses_reads_outside_the_build_dir(lang, src):
    assert vlib.lint_source_includes(src, lang), (lang, src)


@pytest.mark.parametrize("lang,src", [
    ("c", ('#include <stdio.h>\n#include "local.h"\n// #include "/x"\n'
           'int main(void){ puts("#include \\"/x\\""); return 0; }\n')),
    ("cpp", "#include <vector>\n#include <cstdint>\nint main(){}\n"),
    ("rust", "fn main(){ let include_count = 3; // include!(\"/x\")\n println!(\"{}\", include_count); }\n"),
    ("zig", 'const std = @import("std");\n'),
    ("go", 'package main\nimport "fmt"\nfunc main(){ fmt.Println("#include") }\n'),
])
def test_lint_source_includes_accepts_ordinary_sources(lang, src):
    assert vlib.lint_source_includes(src, lang) is None


def test_every_shipped_ref_and_wrong_solution_passes_the_include_lint():
    ext = {"c": "c", "cpp": "cpp", "rs": "rust", "zig": "zig", "go": "go"}
    for d in ("163_bigint_divmod", "167_q15_biquad", "168"):
        for f in sorted((REFS / d).glob("*.*")):
            e = f.suffix[1:]
            if e in ext and not f.name.startswith("leak."):
                assert vlib.lint_source_includes(f.read_text(), ext[e]) is None, f


def test_read_source_refuses_symlinks_fifos_and_oversize(tmp_path):
    real = tmp_path / "real.c"
    real.write_text("int main(){}\n")
    link = tmp_path / "link.c"
    link.symlink_to(real)
    fifo = tmp_path / "fifo.c"
    os.mkfifo(fifo)
    assert vlib.read_source(str(real)) == b"int main(){}\n"
    assert vlib.read_source(str(link)) is None
    assert vlib.read_source(str(fifo)) is None          # and it did not block
    assert vlib.read_source(str(real), cap=4) is None
    assert vlib.read_source(str(tmp_path / "missing.c")) is None


@pytest.mark.skipif(not (DARWIN and CC), reason="the compiler read-deny is sandbox-exec (macOS)")
def test_run_build_denies_the_compiler_a_sealed_file(tmp_path):
    sealed = tmp_path / "x" / "evals" / "refs" / "u"
    sealed.mkdir(parents=True)
    (sealed / "ref.c").write_text('int main(void){return 0;}\n')
    d = vlib.private_build_dir({"m.c": f'#include "{sealed}/ref.c"\n'}, base=str(tmp_path / "b"))
    r = vlib.run_build([CC, "-o", "m", "m.c"], cwd=d, env=vlib.build_env(), timeout=60)
    assert r.returncode != 0 and not os.path.exists(os.path.join(d, "m")), r.stdout + r.stderr
    # the same build of a plain source works under the same sandbox
    d2 = vlib.private_build_dir({"m.c": "int main(void){return 0;}\n"}, base=str(tmp_path / "b2"))
    r2 = vlib.run_build([CC, "-o", "m", "m.c"], cwd=d2, env=vlib.build_env(), timeout=60)
    assert r2.returncode == 0 and vlib.is_native_executable(os.path.join(d2, "m")), r2.stderr


@pytest.mark.skipif(not CC, reason="needs a C compiler")
def test_167_c_refuses_an_include_of_the_reference_and_a_symlinked_source(tmp_path):
    script = _variant("167_q15_biquad.json", "c")["validation"]["script"]
    fx = tmp_path / "fx"
    fx.mkdir()
    ref = REFS / "167_q15_biquad" / "biquad.c"
    (fx / "biquad.c").write_text(f'#include "{ref}"\n')
    ok, out = _validate(script, "/tmp/eval_q15biquad", fx)
    assert not ok and "forbidden in biquad.c" in out, out
    (fx / "biquad.c").unlink()
    (fx / "biquad.c").symlink_to(ref)
    ok, out = _validate(script, "/tmp/eval_q15biquad", fx)
    assert not ok and "must be a regular file" in out, out
    # the reference itself, as a plain file, still passes
    (fx / "biquad.c").unlink()
    shutil.copyfile(ref, fx / "biquad.c")
    ok, out = _validate(script, "/tmp/eval_q15biquad", fx)
    assert ok, out


HANG_C = "#include <unistd.h>\nint main(void){for(;;)sleep(100);}\n"


@pytest.mark.skipif(not CC, reason="needs a C compiler")
@pytest.mark.parametrize("task_file,fixed,name", [
    ("163_bigint_divmod.json", "/tmp/eval_divmod", "divmod.c"),
    ("168_linearizability_kv.json", "/tmp/eval_linz", "linz.c"),
])
def test_a_hanging_compiled_solution_fails_on_its_warm_up(tmp_path, task_file, fixed, name):
    # Before: the warm-up's verdict was ignored and its wall budget alone
    # (30 s / 20 s) used up the harness's 30 s validator budget, so a hang
    # came back as an infra validator_timeout and the unit was rerun.
    script = _variant(task_file, "c")["validation"]["script"]
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / name).write_text(HANG_C)
    t0 = time.monotonic()
    ok, out = _validate(script, fixed, fx)
    assert not ok and "warm-up" in out and "timed out" in out, out
    assert not run_eval.validator_timed_out(out), out
    assert time.monotonic() - t0 < run_eval.VALIDATION_TIMEOUT_S


def test_163_prompt_states_the_cpu_budget_its_validator_enforces():
    for v in _variant("163_bigint_divmod.json")["variants"]:
        assert "within 6 s of CPU time" in v["task"] and "within 10 s" not in v["task"]
        assert re.search(r"run_solution\(RUN, input=inp, cpu_s=6,", v["validation"]["script"])


# --- 173: the oracle's own cancel never leaks --------------------------------------

def _load(path: Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_173_ref_absorbs_its_own_undelivered_cancel():
    import asyncio
    ref = _load(REFS / "173_taskgroup_cancel" / "ref.py", "ref173")

    async def main():
        seen = []
        async with ref.TaskGroup() as g:           # body ends AT g.cancel()
            g.cancel()
        seen.append(asyncio.current_task().cancelling())
        try:
            await asyncio.sleep(0)
            seen.append("no leak")
        except asyncio.CancelledError:
            seen.append("LEAKED")
        try:
            async with ref.TaskGroup() as g:       # ... or raises right after it
                g.cancel()
                raise ValueError("v")
        except ExceptionGroup:
            pass
        try:
            await asyncio.sleep(0)
            seen.append("no leak")
        except asyncio.CancelledError:
            seen.append("LEAKED")
        return seen

    assert asyncio.run(main()) == [0, "no leak", "no leak"]


def test_173_generator_reaches_the_cancel_tail_path():
    h = {"__name__": "h173"}
    exec(compile((REFS / "173_taskgroup_cancel" / "harness.py").read_text(), "harness", "exec"), h)  # noqa: S102  (the validator does the same)
    sc = h["gen_scenarios"](0x7A5C6173, 3)
    assert len(sc) == 3 + h["N_TAIL"]
    tails = [s["main"]["steps"] for s in sc[3:]]
    assert any(json.dumps(t).count('["cancel_group"]]') for t in tails)   # body ends at cancel


# --- 176: the copy never follows a link ----------------------------------------------

def _shopd_fixture(tmp_path: Path) -> tuple[dict, Path]:
    task = dict(_variant("176_intermittent_500s.json", "a"))
    fx = tmp_path / "eval_shopd500"
    for k in ("setup", "cleanup"):
        task[k] = task[k].replace("/tmp/eval_shopd500", str(fx))
    assert run_eval.run_setup(task)
    return task, fx


@pytest.mark.parametrize("target", ["sealed", "devzero", "dir"])
def test_176_refuses_a_symlink_in_shopd(tmp_path, target):
    task, fx = _shopd_fixture(tmp_path)
    sealed = tmp_path / "x" / "evals" / "refs" / "u"
    sealed.mkdir(parents=True)
    (sealed / "fix.py").write_text("ORACLE = 1\n")
    link = fx / "shopd" / "zz_link.py"
    if target == "sealed":
        link.symlink_to(sealed / "fix.py")
    elif target == "devzero":
        link.symlink_to("/dev/zero")
    else:
        (fx / "shopd" / "zz_dir").symlink_to(sealed, target_is_directory=True)
    t0 = time.monotonic()
    ok, out = _validate(task["validation"]["script"], "/tmp/eval_shopd500", fx)
    assert not ok and "may hold only regular files and directories" in out, out
    assert time.monotonic() - t0 < 10


# --- 179: git never runs in the agent's repo -------------------------------------------

@pytest.mark.skipif(not GIT, reason="needs git")
def test_179_planted_promisor_remote_never_runs(tmp_path):
    task = dict(_variant("179_git_bisect_revert.json"))
    top = tmp_path / "eval_bisect"
    for k in ("setup", "cleanup"):
        task[k] = task[k].replace("/tmp/eval_bisect", str(top))
    assert run_eval.run_setup(task)
    repo = top / "semver"
    marker = tmp_path / "PWNED"
    script = (REFS / "179_git_bisect_revert" / "wrong_promisor.sh").read_text()
    script = script.replace("/tmp/eval_bisect", str(top))
    script = script.replace('H=$(cd "$(dirname "$0")" && pwd)', f"H='{REFS / '179_git_bisect_revert'}'")
    # the planted command also leaves a marker, so the test sees whether it ran
    assert script.count('; false #"') == 1
    script = script.replace('; false #"', f"; touch '{marker}'; false #\"")
    sol = tmp_path / "cheat.sh"
    sol.write_text(script)
    r = subprocess.run(["bash", str(sol)], cwd=repo, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    ok, out = _validate(task["validation"]["script"], "/tmp/eval_bisect/semver", repo)
    assert not ok and "the original history is gone" in out, out
    assert not marker.exists(), "the planted uploadpack ran"


