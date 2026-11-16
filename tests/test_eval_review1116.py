"""Review 1116, v5 families b: pins for the fixes to the U3 upgrades (36, 40,
122, 123), the new families 183/188, the kept-v4 substitutions and the
shared validator library.

Each test names the finding it pins. They run without a model and without a
compiler unless the test says so (those skip when the toolchain is absent).
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "tests"))

vlib = importlib.import_module("vlib")
run_eval = importlib.import_module("run_eval")
prove_task = importlib.import_module("prove_task")

DARWIN = sys.platform == "darwin"
SYS_CC = next((p for p in ("/usr/bin/cc", "/usr/bin/gcc") if os.path.isfile(p)
               and vlib._root_owned(p)), None)
GO = shutil.which("go")


# --- the process sampler on macOS (finding: `ps -A` forked every 50 ms poll) ---

@pytest.mark.skipif(not DARWIN, reason="libproc is the macOS sampler")
def test_libproc_table_agrees_with_ps():
    lp = vlib._libproc_table()
    assert lp, "libproc table unavailable on macOS"
    ps = vlib._ps_table()
    me = os.getpid()
    assert lp[me][:2] == ps[me][:2] == (os.getppid(), os.getpgrp())
    common = set(lp) & set(ps)
    # every process ps sees (all uids) is listed, with the same ppid and pgid
    assert len(common) >= 0.95 * len(ps)
    assert all(lp[p][:2] == ps[p][:2] for p in common)
    assert 1 in lp, "another uid's process (launchd) must be listed too"


@pytest.mark.skipif(not DARWIN, reason="libproc is the macOS sampler")
def test_libproc_cpu_is_seconds_not_mach_ticks():
    t0 = time.process_time()
    while time.process_time() - t0 < 0.3:
        pass
    got = vlib._libproc_table()[os.getpid()][2]
    assert abs(got - time.process_time()) < 0.1


@pytest.mark.skipif(not DARWIN, reason="libproc is the macOS sampler")
def test_processes_does_not_fork_ps_on_macos(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("processes() forked ps although libproc works")
    monkeypatch.setattr(vlib, "_ps_table", boom)
    assert os.getpid() in vlib.processes()
    r = vlib.run_solution(["/bin/sh", "-c", "exit 0"], cpu_s=5, allow_fork=True)
    assert r.verdict == "ok"


def test_processes_falls_back_to_ps_when_libproc_is_missing(monkeypatch):
    monkeypatch.setattr(vlib, "_LIBPROC", False)
    monkeypatch.setattr(vlib, "_LINUX", False)
    tab = vlib.processes()
    assert os.getpid() in tab


# --- toolchain capture (finding: U3 validators built with whatever compiler the
# agent planted; a fake gcc compiled the reference and an empty main passed) ---

def _exe(path, body):
    Path(path).write_text("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)


def test_tool_path_prefers_a_root_owned_copy_over_a_planted_one(tmp_path, monkeypatch):
    if not SYS_CC:
        pytest.skip("needs a root-owned /usr/bin/cc")
    planted = tmp_path / "bin"
    planted.mkdir()
    _exe(planted / "cc", "exit 0\n")
    monkeypatch.setenv("PATH", f"{planted}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setattr(vlib, "_TOOL_PINS", None)
    assert vlib.tool_path("cc") == "/usr/bin/cc"
    # under the harness the pin wins, whatever PATH says now
    monkeypatch.setattr(vlib, "_TOOL_PINS", {"cc": {"path": "/pinned/cc"}})
    assert vlib.tool_path("cc") == "/pinned/cc"


def test_check_toolchain_reports_shadowing_new_names_and_changed_trees(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _exe(a / "fakecc", "exit 0\n")
    path = str(a)
    pins = vlib.pin_toolchain(("fakecc", "fakerustc"), path=path)
    assert pins["fakecc"]["found"] == str(a / "fakecc") and pins["fakerustc"]["found"] is None
    assert vlib.check_toolchain(pins, path=path) == []
    # a copy planted ahead of the pinned one on PATH
    _exe(b / "fakecc", "exit 0\n")
    assert any("now resolves to" in p for p in vlib.check_toolchain(pins, path=f"{b}:{a}"))
    # a name that was absent when the harness started
    _exe(b / "fakerustc", "exit 0\n")
    assert any("appeared" in p for p in vlib.check_toolchain(pins, path=f"{a}:{b}"))
    # the pinned binary rewritten in place (same size even): ctime moves
    time.sleep(0.01)
    _exe(a / "fakecc", "exit 1\n")
    assert any("changed on disk" in p for p in vlib.check_toolchain(pins, path=path))


def test_run_validation_refuses_when_a_pinned_tool_is_shadowed(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _exe(a / "fakecc", "exit 0\n")
    base = os.environ.get("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("PATH", f"{a}{os.pathsep}{base}")
    pins = vlib.pin_toolchain(("fakecc",), path=os.environ["PATH"])
    monkeypatch.setattr(run_eval, "_TOOL_PINS", pins)
    task = {"id": "tc", "validation": {"type": "python", "script": "print('OK')"}}
    ok, out = run_eval.run_validation(task)
    assert ok, out
    _exe(b / "fakecc", "exit 0\n")
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{a}{os.pathsep}{base}")
    ok, out = run_eval.run_validation(task)
    assert not ok and out.startswith(run_eval.TOOLCHAIN_CHANGED), out
    # an infrastructure verdict: never banked as the model's failure
    assert run_eval.env_error_signature(out)


def test_validator_gets_the_pins_and_a_go_and_rustup_pinned_env(monkeypatch):
    pins = {"rustc": {"found": None, "path": "/x/sysroot/bin/rustc", "roots": [],
                      "digest": "", "rustup_toolchain": "stable-x"}}
    monkeypatch.setattr(run_eval, "_TOOL_PINS", pins)
    monkeypatch.delenv("RUSTUP_TOOLCHAIN", raising=False)
    monkeypatch.setenv("GOTOOLCHAIN", "auto")
    env = run_eval._validator_env(1.0)
    assert env["GOENV"] == "off" and env["GOTOOLCHAIN"] == "local"
    assert env["RUSTUP_TOOLCHAIN"] == "stable-x"
    monkeypatch.setattr(run_eval, "_toolchain_problems", lambda path: [])
    probe = "import vlib\nprint('PIN', vlib.tool_path('rustc'))\n"
    ok, out = run_eval.run_validation({"id": "p", "validation": {"type": "python",
                                                                  "script": probe}})
    assert ok and "PIN /x/sysroot/bin/rustc" in out, out


def test_build_env_path_is_root_owned_dirs_only(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/usr/bin")
    env = vlib.build_env()
    assert str(tmp_path) not in env["PATH"]
    for d in env["PATH"].split(os.pathsep):
        assert vlib._root_owned(d), d
    assert env["GOTOOLCHAIN"] == "local" and env["GOWORK"] == "off"


@pytest.mark.skipif(not SYS_CC, reason="needs a root-owned system C compiler")
def test_start_build_uses_a_private_copy_and_never_the_planted_compiler(tmp_path, monkeypatch):
    planted = tmp_path / "bin"
    planted.mkdir()
    marker = tmp_path / "planted-ran"
    _exe(planted / "cc", f"echo ran > {marker}\nexit 1\n")
    monkeypatch.setenv("PATH", f"{planted}{os.pathsep}{os.environ.get('PATH', '/usr/bin')}")
    monkeypatch.setattr(vlib, "_TOOL_PINS", None)
    b = vlib.start_build({"m.c": '#include <stdio.h>\nint main(void){puts("real");return 0;}\n'},
                         ["cc", "-O2", "-o", "m", "m.c"], "m")
    ok, log = b.finish(60)
    assert ok, log
    assert not marker.exists(), "the planted compiler ran"
    assert os.path.dirname(b.product) == b.dir
    assert subprocess.run([b.product], capture_output=True, text=True).stdout == "real\n"


def test_start_build_refuses_a_product_that_is_not_native(tmp_path, monkeypatch):
    fake = tmp_path / "fakecc"
    _exe(fake, 'printf "#!/bin/sh\\necho 0\\n" > m; chmod +x m\n')
    monkeypatch.setattr(vlib, "_TOOL_PINS", {"cc": {"path": str(fake)}})
    ok, log = vlib.start_build({"m.c": "x"}, ["cc", "-o", "m", "m.c"], "m").finish(30)
    assert ok is False and "not produce a native executable" in log


@pytest.mark.skipif(not GO, reason="needs the go toolchain")
def test_go_build_ignores_a_go_mod_planted_in_an_ancestor(tmp_path, monkeypatch):
    # The shared TMPDIR (or /tmp) is an ancestor of every private build dir;
    # a go.mod `go 1.99.0` there made `go build x.go` refuse every solution.
    (tmp_path / "go.mod").write_text("module planted\n\ngo 1.99.0\n")
    private = tmp_path / "ob-validator-x"
    (private / "build-cache").mkdir(parents=True)
    monkeypatch.setenv("OPENBEAST_BUILD_CACHE", str(private / "build-cache"))
    monkeypatch.setattr(vlib, "_TOOL_PINS", None)
    src = 'package main\nimport "fmt"\nfunc main(){ for i := range 2 { fmt.Println(i) } }\n'
    b = vlib.start_build({"m.go": src}, ["go", "build", "-o", "m", "m.go"], "m")
    assert b.dir.startswith(os.path.realpath(private)), b.dir
    ok, log = b.finish(120)
    assert ok, log
    assert subprocess.run([b.product], capture_output=True, text=True).stdout == "0\n1\n"


U3_COMPILED = {"36_black_scholes": "bcdef", "122_gemm_blocked": "abcde", "123_nbody": "bcdef"}


@pytest.mark.parametrize("task", sorted(U3_COMPILED))
def test_u3_validators_build_privately_with_the_pinned_toolchain(task):
    d = json.loads((ROOT / "evals" / "tasks" / f"{task}.json").read_text())
    for v in d["variants"]:
        s = v["validation"]["script"]
        assert "cwd=D" not in s, (task, v["id"])            # never build/run in the fixture
        assert "subprocess.Popen(BUILD" not in s, (task, v["id"])
        assert "vlib.start_build({SRC: source}, BUILD, EXE" in s, (task, v["id"])
        if v["language"] != "python":
            assert v["id"] in U3_COMPILED[task]
    if task == "122_gemm_blocked":
        s = d["variants"][0]["validation"]["script"]
        assert "vlib.tool_path(argv[0])" in s and "vlib.tool_path('clang')" in s
        assert "exe = RUN[0]" in s


def _synthetic_cc_task(tmp_path, validator_body):
    root = f"/tmp/eval_tc1116_{os.getpid()}"
    ref = tmp_path / "ref.c"
    ref.write_text('#include <stdio.h>\nint main(void){puts("42");return 0;}\n')
    task = {"id": "tc1116", "language": "c", "dest": f"{root}/m.c",
            "setup": f"mkdir -p {root}", "cleanup": f"rm -rf {root}",
            "validation": {"type": "python",
                           "script": f"D = {root!r}\n" + textwrap.dedent(validator_body)}}
    return task, {"ref": str(ref), "dest": task["dest"]}


_PRIVATE_BUILD = """
import os, sys, vlib
src = open(os.path.join(D, 'm.c'), 'rb').read()
b = vlib.start_build({'m.c': src}, ['cc', '-o', 'm', 'm.c'], 'm')
ok, log = b.finish(60)
if not ok:
    print('FAIL: compile failed'); sys.exit(1)
r = vlib.run_solution([b.product], cpu_s=5)
good = r.stdout.strip() == '42'
print('OK' if good else 'FAIL: wrong'); sys.exit(0 if good else 1)
"""
_FIXTURE_BUILD = """
import os, subprocess, sys, vlib
subprocess.run(['cc', '-o', 'm', 'm.c'], cwd=D, env=vlib.clean_env())
r = vlib.run_solution([os.path.join(D, 'm')], cpu_s=5, cwd=D)
good = r.stdout.strip() == '42'
print('OK' if good else 'FAIL: wrong'); sys.exit(0 if good else 1)
"""


def _arm_runner(task):
    def arm(label, sol, scale=1.0, plant=None):
        run_eval.run_cleanup(task, log=lambda *a: None)
        assert run_eval.run_setup(task, log=print)
        shutil.copy(sol, task["dest"])
        if plant:
            plant()
        ok, out = run_eval.run_validation(task)
        run_eval.run_cleanup(task, log=lambda *a: None)
        return ok, 0.0, out, []
    return arm


@pytest.mark.skipif(not SYS_CC, reason="needs a root-owned system C compiler")
def test_prover_toolchain_arm_catches_a_captured_build_and_passes_a_private_one(tmp_path,
                                                                                monkeypatch):
    pins = run_eval.toolchain_pins()
    # A validator that builds in the fixture with the compiler on PATH, with
    # the harness guard switched off: the planted cc compiles the reference.
    task, spec = _synthetic_cc_task(tmp_path, _FIXTURE_BUILD)
    monkeypatch.setattr(run_eval, "_toolchain_problems", lambda path: [])
    rows = list(prove_task.toolchain_arms(_arm_runner(task), task, spec, pins, "c"))
    assert rows[0][0] == "toolchain:path"
    assert rows[0][1] is True and rows[0][4], "the captured build should pass (the attack)"
    # vlib.start_build resists it on its own, guard still off ...
    task, spec = _synthetic_cc_task(tmp_path, _PRIVATE_BUILD)
    rows = list(prove_task.toolchain_arms(_arm_runner(task), task, spec, pins, "c"))
    assert rows[0][1] is False and not rows[0][4], rows[0][3]
    # ... and with the guard on, the harness refuses before the validator runs
    monkeypatch.undo()
    rows = list(prove_task.toolchain_arms(_arm_runner(task), task, spec, pins, "c"))
    assert rows[0][1] is False and not rows[0][4]
    assert rows[0][3].startswith(run_eval.TOOLCHAIN_CHANGED)


# --- 122's oracle (finding: strided float columns, 6.8 s per validation on GB10) ---

def _gemm_oracle(script: str) -> dict:
    a = script.index("from operator import mul")
    b = script.index("# CPU is the measured quantity")
    ns: dict = {}
    exec(compile(script[a:b], "<oracle>", "exec"), ns)
    return ns


def test_122_oracle_builds_columns_in_the_integer_domain_bit_identically():
    from operator import mul
    d = json.loads((ROOT / "evals" / "tasks" / "122_gemm_blocked.json").read_text())
    scripts = {v["validation"]["script"][v["validation"]["script"].index("from operator"):
                                         v["validation"]["script"].index("# CPU is the measured")]
               for v in d["variants"]}
    assert len(scripts) == 1, "every variant carries the same oracle"
    ns = _gemm_oracle(d["variants"][0]["validation"]["script"])
    src = scripts.pop()
    assert "bi = gm_seq(seed, n * n, n * n)" in src and "b[j::n]" not in src
    n, seed, rows = 257, 123456789, [0, 7, 128, 256]
    GP, gm_seq = ns["GP"], ns["gm_seq"]
    b = [v / GP for v in gm_seq(seed, n * n, n * n)]      # the old construction
    old_cols = [b[j::n] for j in range(n)]
    want = {}
    for i in rows:
        a = [v / GP for v in gm_seq(seed, i * n, n)]
        c = [sum(map(mul, a, col)) for col in old_cols]
        want[i] = (sum(v * v for v in c), max(c))
    assert ns["gm_rows"](n, seed, rows) == want           # exact, not approximate


# --- Python units import a stray reference copy (finding: 183, 188, 40, 36_a and
# 123_a blocked only listed paths; a copy outside evals/ was readable) ---------

def _task_unit(task_id: str, variant: str | None = None) -> dict:
    d = json.loads((ROOT / "evals" / "tasks" / f"{task_id}.json").read_text())
    if variant is None:
        return d
    v = next(x for x in d["variants"] if x["id"] == variant)
    t = {k: val for k, val in d.items() if k != "variants"}
    t.update(v)
    return t


def _validate_with(task: dict, solution_src: str) -> tuple[bool, str]:
    run_eval.run_cleanup(task, log=lambda *a: None)
    assert run_eval.run_setup(task, log=print)
    try:
        Path(task["dest"]).write_text(solution_src)
        return run_eval.run_validation(task)
    finally:
        run_eval.run_cleanup(task, log=lambda *a: None)


# (task, variant, ref file, the proxy's body after `import refcopy`)
_STRAY = [
    ("183_heston_little_trap", None, "183_heston_little_trap/heston.py", "refcopy.main()\n"),
    ("188_gmm_em", None, "188_gmm_em/gmm.py", "fit_gmm = refcopy.fit_gmm\n"),
    ("40_attention", None, "40_attention/attention.py", "attention = refcopy.attention\n"),
    ("36_black_scholes", "a", "36_black_scholes/bs.py", ""),
    ("123_nbody", "a", "123_nbody/nbody.py", ""),
]


@pytest.mark.parametrize("task_id,variant,ref,tail", _STRAY, ids=[s[0] for s in _STRAY])
def test_python_unit_cannot_import_a_stray_reference_copy(tmp_path, task_id, variant, ref, tail):
    task = _task_unit(task_id, variant)
    # the reference itself passes through the same path (the unit still works)
    ok, out = _validate_with(task, (ROOT / "evals" / "refs" / ref).read_text())
    if not ok and "could not confine" in out:
        pytest.skip("no confinement on this host (the unit refuses to run unconfined)")
    assert ok, out
    stray = tmp_path / "scratchpad" / "t183"            # a copy outside evals/
    stray.mkdir(parents=True)
    shutil.copy(ROOT / "evals" / "refs" / ref, stray / "refcopy.py")
    proxy = f"import sys\nsys.path.insert(0, {str(stray)!r})\nimport refcopy\n{tail}"
    ok, out = _validate_with(task, proxy)
    assert not ok, f"{task_id}: a stray reference copy was importable: {out}"


def test_40_numpy_bypass_is_refused_by_lint_and_by_the_confined_run(monkeypatch):
    task = _task_unit("40_attention")
    bypass = textwrap.dedent('''
        import site, sys
        sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != '_NoArrays']
        sys.path.extend(site.getsitepackages())
        _n = __import__('num' + 'py')

        def attention(Q, K, V):
            q, k, v = _n.array(Q), _n.array(K), _n.array(V)
            s = q @ k.T / _n.sqrt(q.shape[1])
            s = _n.exp(s - s.max(axis=1, keepdims=True))
            return (s / s.sum(axis=1, keepdims=True) @ v).tolist()
    ''')
    ok, out = _validate_with(task, bypass)
    assert not ok and "must not use numpy or torch" in out, out
    # Below the lint: the run itself. With the lint disabled, the solution
    # still cannot reach numpy (confined to the stdlib, audit hook on import).
    pytest.importorskip("numpy")
    script = task["validation"]["script"].replace("if _lint:", "if False:")
    assert script != task["validation"]["script"]
    unlinted = dict(task, validation={"type": "python", "script": script})
    ok, out = _validate_with(unlinted, bypass)
    assert not ok, out
    assert "OK:" not in out


# --- kept-v4 fixed wall gates (finding: `el < 1.5` ignored --scale, and the
# prover's lint could not see it) ----------------------------------------------

@pytest.mark.parametrize("src,want", [
    ("import time\nt0 = time.time()\nel = time.time() - t0\nassert el < 1.5\n", [1.5]),
    ("import time\nt0 = time.monotonic()\nassert time.monotonic() - t0 <= 2\n", [2.0]),
    ("import time\nt0 = time.perf_counter()\nd = time.perf_counter() - t0\nassert 3.0 > d\n", [3.0]),
    ("import time\nt0=time.monotonic()\ne=time.monotonic()-t0\nassert 0.18 <= e < 0.5\n", [0.5]),
    # scaled or not a time: clean
    ("import time\nt0 = time.time()\nel = time.time() - t0\nassert el < 1.5 * _SCALE\n", []),
    ("import time, vlib\nt0 = time.time()\nel = time.time() - t0\nassert el < vlib.t(1.5)\n", []),
    ("n = len(x)\nassert n < 5\n", []),
])
def test_prover_lint_sees_a_fixed_gate_on_a_measured_time(src, want):
    assert [lim for _l, lim in prove_task.unscaled_time_gates("python", src)] == want
    bash = "python3 - <<'VALIDATEEOF'\n" + src + "VALIDATEEOF\n"
    assert [lim for _l, lim in prove_task.unscaled_time_gates("bash", bash)] == want


def test_every_v5_unit_passes_the_isolation_lint():
    R, M = prove_task._load_harness(str(ROOT), None)
    tasks = {t["id"]: t for t in R.load_tasks(None)}
    man = M.load_manifest("v5", tasks=list(tasks.values()), strict=False)
    bad = {}
    for u in man["units"]:
        t = tasks[man["resolved"][u["id"]]]
        v = t.get("validation") or {}
        probs = prove_task.isolation_problems(v.get("type", "bash"), v.get("script", ""))
        if probs:
            bad[u["id"]] = probs
    assert not bad, bad


@pytest.mark.parametrize("task_id", ["146_aho_corasick", "147_persistent_bst",
                                     "153_coroutine_scheduler", "158_karatsuba_bytes",
                                     "159_ntt_convolution"])
def test_kept_v4_gates_read_the_scale_before_the_solution_runs(task_id):
    d = json.loads((ROOT / "evals" / "tasks" / f"{task_id}.json").read_text())
    vals = [v["validation"] for v in d.get("variants", []) if "validation" in v] \
        or [d["validation"]]
    for val in vals:
        s = val["script"]
        body = s if val.get("type") == "python" else s.split("\n", 1)[1]
        # first statement: an in-process v4 solution cannot raise the scale
        # through os.environ before the validator has read it
        assert body.startswith("import os as _os; _SCALE = max(1.0, float(_os.environ.get("
                               "'OPENBEAST_TIMEOUT_SCALE') or 1))"), task_id
        assert " * _SCALE" in s


def test_scaled_gate_follows_the_harness_scale(tmp_path):
    # 146's own gate line, run at scale 1 and 2 against a fixed 2 s "measurement"
    d = json.loads((ROOT / "evals" / "tasks" / "146_aho_corasick.json").read_text())
    s = d["validation"]["script"]
    prefix = s.split("\n", 1)[0]
    probe = prefix + "\nelapsed = 2.0\nprint('PASS' if elapsed < 1.5 * _SCALE else 'FAIL')\n"
    for scale, want in (("1", "FAIL"), ("2", "PASS")):
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                             env={**os.environ, "OPENBEAST_TIMEOUT_SCALE": scale}).stdout
        assert out.strip() == want, (scale, out)


# --- fixed fixture dirs across processes (finding: two provers of one unit
# deleted each other's solutions) ---------------------------------------------

_HOLDER = """
import sys, time
sys.path.insert(0, {evals!r}); sys.path.insert(0, {agents!r})
import run_eval
task = {{"id": "lk", "setup": "mkdir -p {root}", "cleanup": "rm -rf {root}"}}
with run_eval.fixture_lock(task):
    print("held", flush=True)
    time.sleep(1.5)
"""


def test_fixture_lock_makes_a_second_process_wait_and_say_so():
    root = f"/tmp/eval_lock1116_{os.getpid()}"
    task = {"id": "lk", "setup": f"mkdir -p {root}", "cleanup": f"rm -rf {root}"}
    assert run_eval.fixture_roots(task) == [root]
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER.format(evals=str(ROOT / "evals"),
                                              agents=str(ROOT / "agents"), root=root)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        said = []
        t0 = time.monotonic()
        with run_eval.fixture_lock(task, log=said.append):
            waited = time.monotonic() - t0
        assert waited > 0.8, waited
        assert said and f"Waiting for fixture {root}" in said[0] and str(holder.pid) in said[0]
    finally:
        holder.wait(10)
    # uncontended: no wait, no message; a task without fixtures locks nothing
    said = []
    with run_eval.fixture_lock(task, log=said.append) as lk:
        assert lk.held == [root]
    assert said == []
    with run_eval.fixture_lock({"id": "x", "setup": "true"}) as lk:
        assert lk.held == []


def test_fixture_lock_serialises_two_threads_of_one_process():
    import threading
    root = f"/tmp/eval_lock1116t_{os.getpid()}"
    task = {"id": "lk", "cleanup": f"rm -rf {root}"}
    inside, overlap = [], []

    def worker():
        with run_eval.fixture_lock(task, log=lambda *_: None):
            inside.append(1)
            if len(inside) > 1:
                overlap.append(1)
            time.sleep(0.2)
            inside.pop()
    ts = [threading.Thread(target=worker) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert not overlap


def test_prover_and_eval_take_the_fixture_lock():
    src = (ROOT / "tests" / "prove_task.py").read_text()
    assert "with R.fixture_lock(task" in src
    ev = (ROOT / "evals" / "run_eval.py").read_text()
    assert ev.count("with fixture_lock(task") == 2
