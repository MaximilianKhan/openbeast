"""tests/prove_task.py — the v5 authoring gate — proves a sound unit and
refuses each way a unit can be unsound (brief §6.3).

A synthetic Python unit in the v5 pattern (samples only on disk; a seeded
generator and the oracle inside the validator; a mismatch prints only the
case index; the validator's own per-run limit) is PROVEN. Then each defect
the gate exists for is introduced once and must turn the verdict:
a validator that accepts anything, a fixture that leaks the expected
output, a wrong solution that fails for a reason other than its declared
class, too few wrong solutions, and a timed unit with no slow solution.
"""

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import prove_task  # noqa: E402

VALIDATOR = r'''
import os, random, sys
import vlib
SOL = "@DIR@/sol.py"
if not os.path.exists(SOL):
    print("no solution"); sys.exit(1)
rng = random.Random(0x5EED)
cases = [(rng.randint(-10**6, 10**6), rng.randint(-10**6, 10**6)) for _ in range(200)]
cases += [(0, 0), (1, -1), (-7, 3)]
inp = "\n".join(f"{a} {b}" for a, b in cases) + "\n"
r = vlib.run_solution(vlib.python_cmd(SOL), input=inp, cpu_s=3, wall_s=3)
if r.verdict != "ok":
    print(f"solution timed out ({r.reason})"); sys.exit(1)
got = r.stdout.split()
if len(got) != len(cases):
    print(f"mismatch at case {min(len(got), len(cases))} (count)"); sys.exit(1)
for i, ((a, b), g) in enumerate(zip(cases, got)):
    if str(a * b + a) != g:
        print(f"mismatch at case {i}"); sys.exit(1)
print("OK")
'''

SOLS = {
    "ref.py": "import sys\nfor ln in sys.stdin:\n    a, b = map(int, ln.split())\n    print(a * b + a)\n",
    "wrong_noadd.py": "import sys\nfor ln in sys.stdin:\n    a, b = map(int, ln.split())\n    print(a * b)\n",
    "wrong_samples.py": "print(6)\nprint(0)\n",
    "slow.py": "import time\ntime.sleep(10)\n",
    "leak.py": ("import glob\nfor p in glob.glob('@DIR@/*'):\n"
                "    print(open(p).read())\n"),
}


def _world(tmp_path, *, validator=VALIDATOR, setup_extra="", expect=None, sols=None,
           timed=True):
    d = f"/tmp/eval_provetest_{os.getpid()}_{tmp_path.name[-12:]}"
    tasks = tmp_path / "tasks"
    refs = tmp_path / "refs" / "990_prove_demo"
    tasks.mkdir()
    refs.mkdir(parents=True)
    task = {
        "id": "990_prove_demo", "name": "prove demo", "difficulty": "medium",
        "category": "Algorithms & DS", "max_iter": 15,
        "task": f"Write {d}/sol.py: for each stdin line `a b` print a*b+a. "
                f"Samples: {d}/sample_input.txt, {d}/sample_output.txt.",
        "setup": (f"mkdir -p {d} && printf '2 2\\n0 5\\n' > {d}/sample_input.txt && "
                  f"printf '6\\n0\\n' > {d}/sample_output.txt{setup_extra}"),
        "validation": {"type": "python", "script": validator.replace("@DIR@", d)},
        "cleanup": f"rm -rf {d}",
        "ref_stem": "990_prove_demo/ref", "dest": f"{d}/sol.py",
    }
    (tasks / "990_prove_demo.json").write_text(json.dumps(task))
    for name, body in (SOLS if sols is None else sols).items():
        (refs / name).write_text(body.replace("@DIR@", d))
    (refs / "prove.json").write_text(json.dumps({
        "timed": timed,
        "expect": expect if expect is not None else
        {"wrong_noadd.py": r"mismatch at case \d+", "wrong_samples.py": r"mismatch at case"},
        "slow_expect": "solution timed out"}))
    return ["--tasks-dir", str(tasks), "--refs-dir", str(tmp_path / "refs"),
            "--unit", "990_prove_demo"]


def test_a_sound_unit_is_proven(tmp_path, capsys):
    assert prove_task.main(_world(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "990_prove_demo: PROVEN" in out
    assert out.count("OK  ref#") == 5                 # timed unit: 5/5
    for arm in ("wrong:wrong_noadd.py", "wrong:wrong_samples.py", "empty",
                "leak-probe", "slow", "ref@scale2.0"):
        assert f"OK  {arm}" in out, arm


def test_a_validator_that_accepts_anything_is_not_proven(tmp_path, capsys):
    rc = prove_task.main(_world(tmp_path, validator="print('OK')", timed=False))
    out = capsys.readouterr().out
    assert rc == 1 and "NOT PROVEN" in out
    assert "BAD empty" in out and "a wrong solution PASSED" in out


def test_a_leaked_expected_file_is_not_proven(tmp_path, capsys):
    leak = " && printf 'secret' > {d}/expected_output.txt"
    argv = _world(tmp_path, setup_extra="", timed=False)
    # rebuild with the leak, now that the dir name is known
    d = json.loads((tmp_path / "tasks" / "990_prove_demo.json").read_text())["dest"]
    d = os.path.dirname(d)
    p = tmp_path / "tasks" / "990_prove_demo.json"
    t = json.loads(p.read_text())
    t["setup"] += leak.format(d=d)
    p.write_text(json.dumps(t))
    assert prove_task.main(argv) == 1
    assert "oracle-like file(s) in the fixture dir" in capsys.readouterr().out


def test_a_wrong_solution_failing_for_another_reason_is_not_proven(tmp_path, capsys):
    rc = prove_task.main(_world(tmp_path, timed=False,
                                 expect={"wrong_noadd.py": "segmentation fault",
                                         "wrong_samples.py": "mismatch"}))
    out = capsys.readouterr().out
    assert rc == 1 and "failed for the wrong reason" in out


def test_an_undeclared_failure_class_is_not_proven_unless_lenient(tmp_path, capsys):
    argv = _world(tmp_path, expect={}, timed=False)
    assert prove_task.main(argv) == 1
    assert "failure class not declared" in capsys.readouterr().out
    assert prove_task.main(argv + ["--lenient"]) == 0


def test_one_wrong_solution_is_not_enough(tmp_path, capsys):
    sols = {k: v for k, v in SOLS.items() if k != "wrong_samples.py"}
    assert prove_task.main(_world(tmp_path, sols=sols, timed=False)) == 1
    assert "need >= 2" in capsys.readouterr().out


def test_a_timed_unit_needs_a_slow_solution(tmp_path, capsys):
    sols = {k: v for k, v in SOLS.items() if k != "slow.py"}
    assert prove_task.main(_world(tmp_path, sols=sols)) == 1
    assert "timed unit ships no slow solution" in capsys.readouterr().out


def test_a_stem_naming_a_file_sets_the_sibling_extension(tmp_path):
    """A Q&A unit's ref is a data file (178: arms/answers.json) in a python
    task: its wrong arms are found by the ref's extension, not the
    language's, and a .py beside them is not mistaken for one."""
    d = tmp_path / "refs" / "991_qa" / "arms"
    d.mkdir(parents=True)
    for name in ("answers.json", "wrong_a.json", "wrong_b.json", "wrong_x.py"):
        (d / name).write_text("{}")
    task = {"id": "991_qa", "language": "python", "dest": "/tmp/eval_qa/answers.json",
            "ref_stem": "991_qa/arms/answers.json",
            "prove": {"expect": {"wrong_a.json": "^FAIL"}}}
    _R, M = prove_task._load_harness(str(ROOT), None)
    spec = prove_task.discover(task, M, str(tmp_path / "refs"))
    assert spec["ref"] == str(d / "answers.json")
    assert [(os.path.basename(w), rx) for w, rx in spec["wrong"]] == \
        [("wrong_a.json", "^FAIL"), ("wrong_b.json", None)]


def test_lint_catches_coreutils_timeout_bits_and_zig_tutoring():
    sys.path.insert(0, str(ROOT / "evals"))
    import suite_manifest as M
    t = {"id": "x_f", "language": "zig", "setup": "timeout 5 ./x",
         "validation": {"type": "bash", "script": "timeout 10 ./x < in"},
         "task": "Use `&fr.interface` and pass init.io; #include <bits/stdc++.h>"}
    problems = prove_task.lint_unit(t, M, [])
    assert any("setup uses coreutils" in p for p in problems)
    assert any("validation uses coreutils" in p for p in problems)
    assert any("bits/stdc++.h" in p for p in problems)
    assert any("API tutoring" in p for p in problems)
    clean = {"id": "y", "language": "python", "setup": "mkdir -p /tmp/eval_y",
             "validation": {"type": "python", "script": "subprocess.run(x, timeout=vlib.t(5))"},
             "task": "Write /tmp/eval_y/y.py"}
    assert prove_task.lint_unit(clean, M, []) == []
    # A fixed limit that ignores OPENBEAST_TIMEOUT_SCALE, and a child python
    # whose source sits in its argv, are both refused.
    unscaled = dict(clean, validation={"type": "python", "script": "subprocess.run(x, timeout=5)"})
    assert any("OPENBEAST_TIMEOUT_SCALE" in p for p in prove_task.lint_unit(unscaled, M, []))
    dash_c = dict(clean, validation={"type": "python",
                                     "script": "subprocess.run([sys.executable, '-c', D])"})
    assert any("`-c`" in p for p in prove_task.lint_unit(dash_c, M, []))


def test_a_stem_naming_its_own_file_sets_the_sibling_extension(tmp_path):
    """An assembly unit (task language python, solution lib.s) declares
    `ref_stem: "<dir>/ref.s"`; its wrong/slow/leak siblings are .s too."""
    sys.path.insert(0, str(ROOT / "evals"))
    import suite_manifest as M
    d = tmp_path / "995_asm"
    d.mkdir()
    for n in ("ref.s", "wrong_a.s", "wrong_b.s", "slow_x.s", "leak_probe.s", "wrong_c.py"):
        (d / n).write_text("")
    (d / "prove.json").write_text(json.dumps({"expect": {"wrong_a.s": "x"}}))
    spec = prove_task.discover({"id": "995_asm", "ref_stem": "995_asm/ref.s",
                                "dest": "/tmp/eval_asm/lib.s"}, M, str(tmp_path))
    assert spec["ref"] == str(d / "ref.s")
    assert [os.path.basename(w) for w, _ in spec["wrong"]] == ["wrong_a.s", "wrong_b.s"]
    assert dict((os.path.basename(w), rx) for w, rx in spec["wrong"])["wrong_a.s"] == "x"
    assert os.path.basename(spec["slow"]) == "slow_x.s"
    assert os.path.basename(spec["leak"]) == "leak_probe.s"


# --- family formats: delegation, and PROVE.json on a case-sensitive host ------------

def test_family_formats_delegate_to_the_familys_own_prover(tmp_path):
    refs = tmp_path / "refs"
    (refs / "900_x" / "ref").mkdir(parents=True)          # an overlay DIRECTORY solution
    (refs / "900_x" / "prove.py").write_text("raise SystemExit(0)\n")
    (refs / "901_y").mkdir()
    (refs / "901_y" / "ref.sh").write_text("true\n")
    (refs / "901_y" / "prove.py").write_text("raise SystemExit(0)\n")
    (refs / "902_z").mkdir()
    (refs / "902_z" / "ref.py").write_text("")
    d = {"ref": str(refs / "900_x" / "ref.py"), "wrong": [("w", "x")]}
    assert prove_task.family_prover({"ref_stem": "900_x/ref"}, str(refs), d) \
        == str(refs / "900_x" / "prove.py")
    sh = {"ref": str(refs / "901_y" / "ref.sh"), "wrong": [("w", "x"), ("v", "y")]}
    assert prove_task.family_prover({"ref_stem": "901_y/ref", "prove": {"mode": "exec"}},
                                    str(refs), sh) == str(refs / "901_y" / "prove.py")
    assert prove_task.family_prover({"ref_stem": "901_y/ref"}, str(refs), sh) is None
    # no prove.py of its own: the generic prover runs (and refuses what it must)
    assert prove_task.family_prover({"ref_stem": "902_z/ref"}, str(refs),
                                    {"ref": str(refs / "902_z" / "ref.py"), "wrong": []}) is None


def test_upper_case_prove_json_is_read(tmp_path):
    sys.path.insert(0, str(ROOT / "evals"))
    import suite_manifest as M
    refs = tmp_path / "refs"
    (refs / "903_w").mkdir(parents=True)
    (refs / "903_w" / "ref.py").write_text("")
    (refs / "903_w" / "wrong_a.py").write_text("")
    (refs / "903_w" / "PROVE.json").write_text(json.dumps({"expect": {"wrong_a.py": "^boom"}}))
    spec = prove_task.discover({"id": "903_w", "ref_stem": "903_w/ref", "dest": "/tmp/x.py",
                                "language": "python"}, M, str(refs))
    assert spec["wrong"] == [(str(refs / "903_w" / "wrong_a.py"), "^boom")]


def test_a_suite_with_an_unresolved_unit_exits_nonzero(tmp_path, monkeypatch):
    """`--suite` printed manifest units with no task file as "not proven"
    and then forgot them: a partial manifest whose resolved units all
    proved exited 0 (review 1116). They now count, and land in --json."""
    manifest = {"units": [{"id": "good_py"}, {"id": "999_ghost_py"}],
                "resolved": {"good_py": "good_py"},
                "resolution_problems": ["999_ghost_py: no task file"]}

    class _R:
        @staticmethod
        def load_tasks(_x):
            return [{"id": "good_py"}]

    class _M:
        @staticmethod
        def load_manifest(_suite, tasks=None, strict=True):
            return manifest

    monkeypatch.setattr(prove_task, "_load_harness", lambda repo, td: (_R, _M))
    monkeypatch.setattr(prove_task, "discover", lambda task, M, refs: {"ref": "x"})
    monkeypatch.setattr(prove_task, "family_prover", lambda task, refs, spec: None)
    monkeypatch.setattr(prove_task, "prove", lambda *a, **k: (True, []))
    out = tmp_path / "v.json"
    assert prove_task.main(["--suite", "v5", "--json", str(out)]) == 1
    assert json.loads(out.read_text()) == {"999_ghost_py": "missing", "good_py": "PROVEN"}
    # a fully resolved suite still exits 0
    manifest.update(units=[{"id": "good_py"}], resolution_problems=[])
    assert prove_task.main(["--suite", "v5"]) == 0
