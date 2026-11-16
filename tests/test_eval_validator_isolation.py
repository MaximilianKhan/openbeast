"""Validator isolation (v5): what a solution under test can see of its validator.

The v5 verifiers passed a dozen families without solving them, using three
harness-level holes. Each test below rebuilds one of those attacks against
the REAL run_eval.run_validation (no mocks) and shows it now fails:

  * argv: `python -c <script>` put the whole validator, oracle and seed
    included, in a command line any same-uid process can read
    (`ps -ww -o command= -p $PPID`, /proc/PPID/cmdline).
  * import path: `python -c` put the harness cwd on sys.path, so a planted
    urllib/parse.py ran inside the validator on a lazy import, and stayed
    there for every later unit.
  * scale: validators never learned the harness's timeout scale, so their
    fixed limits flaked under --jobs.

It also covers vlib.run_solution's CPU accounting for the whole process tree,
which the 122 gemm verifier beat with orphaned workers.
"""

import importlib
import os
import subprocess
import sys
import textwrap
import time
import urllib.parse  # noqa: F401  loaded before any test chdirs next to a plant
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "tests"))

run_eval = importlib.import_module("run_eval")
vlib = importlib.import_module("vlib")
prove_task = importlib.import_module("prove_task")

LINUX = sys.platform.startswith("linux")
DARWIN = sys.platform == "darwin"
ORACLE = "ORACLE-7f3a9c1e-" + "do-not-leak"

# A solution that reports every command line and environment it can read for
# its parent and grandparent: /proc on Linux, ps (KERN_PROCARGS2) elsewhere.
STEALER = textwrap.dedent(r'''
    import os, subprocess
    def ps(pid, fmt, extra=()):
        try:
            return subprocess.run(["ps", *extra, "-ww", "-o", fmt, "-p", str(pid)],
                                  capture_output=True, text=True, timeout=10).stdout
        except Exception as e:
            return f"<{e}>"
    def proc(pid, name):
        try:
            with open(f"/proc/{pid}/{name}", "rb") as f:
                return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError as e:
            return f"<{e}>"
    pid = os.getppid()
    seen = []
    for _ in range(2):
        seen += [ps(pid, "command="), ps(pid, "command=", ("-E",)),
                 ps(pid, "command=", ("e",))]
        if os.path.isdir("/proc"):
            seen += [proc(pid, "cmdline"), proc(pid, "environ")]
        try:
            pid = int(ps(pid, "ppid=").strip())
        except ValueError:
            break
    with open(os.environ["STEAL_OUT"], "w") as f:
        f.write("\n".join(seen))
''')


def _validate(script: str, vtype: str = "python", scale: float = 1.0):
    return run_eval.run_validation({"validation": {"type": vtype, "script": script}},
                                   timeout_scale=scale)


@pytest.fixture
def fx(tmp_path):
    d = tmp_path / "fx"
    d.mkdir()
    return d


# --- argv / environ --------------------------------------------------------------

def test_solution_cannot_read_the_oracle_from_its_parent(fx, monkeypatch):
    monkeypatch.setenv("OPENBEAST_API_KEY", "harness-secret-key-42")
    sol = fx / "steal.py"
    sol.write_text(STEALER)
    out = fx / "stolen.txt"
    script = textwrap.dedent(f'''
        import os, subprocess, sys
        ORACLE = {ORACLE!r}
        r = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(os.getpid())],
                           capture_output=True, text=True)
        print("ARGV", r.stdout.strip())
        env = dict(os.environ, STEAL_OUT={str(out)!r})
        subprocess.run(["python3", {str(sol)!r}], env=env, check=True)
        print("OK")
    ''')
    ok, output = _validate(script)
    assert ok, output
    stolen = out.read_text()
    assert stolen.strip(), "the stealer read nothing at all"
    assert ORACLE not in stolen
    assert "do-not-leak" not in stolen
    assert "harness-secret-key-42" not in stolen, "the validator env was not scrubbed"
    # The validator's own argv carries no script, only the stdin marker.
    argv_line = next(ln for ln in output.splitlines() if ln.startswith("ARGV"))
    assert argv_line.rstrip().endswith("-I -S -"), argv_line
    assert " -c " not in argv_line


def test_shell_validator_script_is_not_in_any_argv(fx):
    sol = fx / "steal.py"
    sol.write_text(STEALER)
    out = fx / "stolen.txt"
    script = (f"ORACLE='{ORACLE}'\n"
              f"STEAL_OUT={out} python3 {sol} || exit 1\n"
              "echo OK\n")
    ok, output = _validate(script, vtype="bash")
    assert ok, output
    stolen = out.read_text()
    assert stolen.strip()
    assert ORACLE not in stolen


def test_validator_does_not_inherit_the_harness_stdin_or_argv():
    script = textwrap.dedent('''
        import sys, os
        assert sys.argv == ["<validator>"], sys.argv
        assert sys.stdin.read() == "", "stdin still carries data"
        assert sys.flags.isolated and sys.flags.no_site
        print("OK")
    ''')
    ok, output = _validate(script)
    assert ok, output


# --- import path / cwd -------------------------------------------------------------

PLANT = textwrap.dedent('''
    print("PLANTED urllib.parse ran", flush=True)
    import os
    os._exit(0)  # the attacker's goal: exit 0, i.e. PASS
''')


def _plant(where: Path):
    pkg = where / "urllib"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(PLANT)
    (pkg / "parse.py").write_text(PLANT)


def test_planted_module_in_the_harness_cwd_is_not_imported(tmp_path, monkeypatch):
    harness_cwd = tmp_path / "harness"
    harness_cwd.mkdir()
    _plant(harness_cwd)
    monkeypatch.chdir(harness_cwd)
    script = textwrap.dedent('''
        import os, sys
        assert "urllib" not in sys.modules, "the probe needs a lazy import"
        def later():
            from urllib.parse import quote   # lazy, as in 176's hidden tests
            return quote("a b")
        assert later() == "a%20b"
        print("OK genuine", os.getcwd())
    ''')
    ok, output = _validate(script)
    assert ok, output
    assert "PLANTED" not in output
    assert "OK genuine" in output
    assert str(harness_cwd) not in output


def test_solution_planting_into_the_validator_cwd_is_not_imported(fx):
    # The solution inherits the validator's cwd (the validator does not pass
    # one) and plants urllib/ there; the validator imports urllib.parse AFTER
    # the solution ran. Then the next unit must not find the plant either.
    sol = fx / "planter.py"
    sol.write_text(textwrap.dedent(f'''
        import os
        os.makedirs("urllib", exist_ok=True)
        for name in ("__init__.py", "parse.py"):
            with open(os.path.join("urllib", name), "w") as f:
                f.write({PLANT!r})
        print(os.getcwd())
    '''))
    script = textwrap.dedent(f'''
        import os, subprocess, sys
        assert "urllib" not in sys.modules
        r = subprocess.run(["python3", {str(sol)!r}], capture_output=True, text=True)
        assert os.path.isdir("urllib"), "the planter did not run in the validator cwd"
        from urllib.parse import quote
        import urllib.parse
        assert quote("a b") == "a%20b"
        assert not urllib.parse.__file__.startswith(os.getcwd()), urllib.parse.__file__
        print("CWD", os.getcwd())
        print("OK")
    ''')
    ok, output = _validate(script)
    assert ok, output
    assert "PLANTED" not in output
    vcwd = next(ln.split(" ", 1)[1] for ln in output.splitlines() if ln.startswith("CWD"))
    assert not os.path.exists(vcwd), "the validator cwd (with the plant) outlived the unit"
    # The next unit runs in a different, empty cwd.
    ok2, out2 = _validate('import os; print("CWD", os.getcwd(), os.listdir("."))')
    assert ok2, out2
    assert vcwd not in out2 and "[]" in out2


def test_validator_cwd_is_private_and_fresh():
    ok, output = _validate(textwrap.dedent('''
        import os, stat
        cwd = os.getcwd()
        assert os.listdir(cwd) == []
        assert stat.S_IMODE(os.stat(cwd).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(os.path.dirname(cwd)).st_mode) == 0o700
        # The script file itself was unlinked before the validator started.
        assert os.listdir(os.path.dirname(cwd)) == ["cwd"], os.listdir(os.path.dirname(cwd))
        print("OK")
    '''))
    assert ok, output


# --- scale -------------------------------------------------------------------------

@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_timeout_scale_reaches_the_validator(scale):
    ok, output = _validate(textwrap.dedent('''
        import os, vlib
        print("ENV", os.environ["OPENBEAST_TIMEOUT_SCALE"])
        print("T10", vlib.t(10))
    '''), scale=scale)
    assert ok, output
    assert f"T10 {10 * scale}" in output
    assert f"ENV {scale:g}" in output


def test_shell_validator_sees_the_scale_too():
    ok, output = _validate('echo "S=$OPENBEAST_TIMEOUT_SCALE"', vtype="bash", scale=2.0)
    assert ok and "S=2" in output, output


def test_vlib_is_injected_not_importable_from_disk():
    ok, output = _validate(textwrap.dedent('''
        import sys, vlib
        assert vlib.__file__ == "<vlib>"
        assert not any("evals" in p for p in sys.path), sys.path
        print("OK")
    '''))
    assert ok, output


# --- vlib.run_solution: CPU across the whole tree ---------------------------------------

def _py(src: str) -> list:
    return [sys.executable, "-c", src]


def test_cpu_budget_kills_a_spin():
    r = vlib.run_solution(_py("while True: pass"), cpu_s=1)
    assert r.verdict == "cpu" and not r.ok, r
    assert r.wall_s < 10


def test_cpu_in_a_waited_child_is_counted():
    src = ("import subprocess, sys\n"
           "subprocess.run([sys.executable, '-c', 'while True: pass'])\n")
    r = vlib.run_solution(_py(src), cpu_s=1, allow_fork=True)
    assert r.verdict == "cpu", r


def test_orphaned_worker_is_refused_as_a_daemon():
    # 122's bypass: fork workers, let them be orphaned, never wait for them.
    src = ("import os, time\n"
           "if os.fork() == 0:\n"
           "    if os.fork() == 0:\n"
           "        t = time.time()\n"
           "        while time.time() - t < 5: pass\n"
           "        os._exit(0)\n"
           "    os._exit(0)\n"
           "os.wait()\n"
           "time.sleep(1.5)\n"
           "print('done')\n")
    r = vlib.run_solution(_py(src), cpu_s=10, allow_fork=True)
    assert r.verdict in ("daemon", "cpu"), r


@pytest.mark.skipif(not LINUX, reason="setsid double-fork tracking needs the Linux subreaper")
def test_setsid_double_fork_is_caught_on_linux():
    src = ("import os, time\n"
           "if os.fork() == 0:\n"
           "    if os.fork() == 0:\n"
           "        os.setsid()\n"
           "        t = time.time()\n"
           "        while time.time() - t < 5: pass\n"
           "        os._exit(0)\n"
           "    os._exit(0)\n"
           "os.wait()\n"
           "time.sleep(1.5)\n")
    r = vlib.run_solution(_py(src), cpu_s=10, allow_fork=True)
    assert r.verdict in ("daemon", "cpu"), r


def test_fork_is_refused_by_default():
    src = ("import os, time\n"
           "pid = os.fork()\n"
           "if pid == 0:\n"
           "    time.sleep(2); os._exit(0)\n"
           "os.waitpid(pid, 0)\n")
    r = vlib.run_solution(_py(src), cpu_s=5)
    assert not r.ok, r
    assert r.verdict == "forked" or r.returncode != 0, r


def test_background_survivor_is_a_daemon_and_is_killed(tmp_path):
    pidfile = tmp_path / "bg.pid"
    r = vlib.run_solution(["/bin/sh", "-c", f"sleep 30 & echo $! > {pidfile}; echo up"],
                          cpu_s=5, allow_fork=True)
    assert r.verdict == "daemon", r
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"background child {pid} survived run_solution")


def test_honest_solution_passes_and_gets_its_stdin():
    r = vlib.run_solution(_py("import sys; print(sum(map(int, sys.stdin.read().split())))"),
                          input="1 2 3\n", cpu_s=5)
    assert r.ok and r.stdout.strip() == "6", r


def test_wall_budget_scales(monkeypatch):
    monkeypatch.setenv("OPENBEAST_TIMEOUT_SCALE", "2")
    t0 = time.monotonic()
    r = vlib.run_solution(_py("import time; time.sleep(30)"), cpu_s=1, wall_s=0.5)
    assert r.verdict == "wall", r
    assert 0.9 <= time.monotonic() - t0 < 10


def test_solution_group_dies_when_the_validator_is_killed(tmp_path, monkeypatch):
    """A validator killed on the harness timeout must not leave its solution
    running into the next unit: vlib records the group, the harness kills it."""
    monkeypatch.setattr(run_eval, "VALIDATION_TIMEOUT_S", 3)
    pidfile = tmp_path / "sol.pid"
    script = textwrap.dedent(f'''
        import sys, vlib
        vlib.run_solution([sys.executable, "-c",
            "import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(60)"],
            cpu_s=100, wall_s=100)
    ''')
    ok, output = _validate(script)
    assert not ok and run_eval.validator_timed_out(output), output
    pid = int(pidfile.read_text())
    for _ in range(60):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"solution {pid} outlived its killed validator")


@pytest.mark.skipif(not DARWIN, reason="read seal is enforced by sandbox-exec on macOS")
def test_sealed_eval_paths_are_unreadable_to_the_solution(tmp_path):
    secret = tmp_path / "sealed" / "task.json"
    secret.parent.mkdir()
    secret.write_text(ORACLE)
    r = vlib.run_solution(_py(f"print(open({str(secret)!r}).read())"), cpu_s=5,
                          deny_read=[str(secret.parent)])
    assert ORACLE not in r.stdout and not r.ok, r


def test_python_validator_has_no_shell_parent_holding_the_script():
    # dash does not exec the last command of `sh -c`, so a surviving shell
    # (dumpable, unlike the validator) held the unlinked script as its stdin
    # and /proc/<sh>/fd/0 handed it to the solution. The validator's parent
    # must be the harness itself.
    task = {"id": "ppid", "validation": {"type": "python", "script": textwrap.dedent(f"""
        import os
        print("PPID", os.getppid(), {os.getpid()})
    """)}}
    ok, out = run_eval.run_validation(task)
    assert ok, out
    ppid, harness = out.split()[1:3]
    assert ppid == harness, out


# --- lint ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "import math, torch",
    "import os\nimport numpy.linalg as la",
    "from torch import nn",
    "x = __import__('torch')",
    "import importlib\nm = importlib.import_module('nu' + 'mpy')",
    "f = lambda: 0\nprint('{0.__globals__}'.format(f))",
    "getattr(__builtins__, 'ev' + 'al')('1')",
    "import sys\nm = sys.modules['os']",
    "exec('import torch')",
])
def test_lint_python_catches_the_known_dodges(src):
    assert vlib.lint_python(src, ban_modules=("numpy", "torch")), src


def test_lint_python_accepts_plain_code():
    src = textwrap.dedent('''
        import math, sys
        from collections import deque
        class Node:
            def __init__(self, v):
                self.v = v
            def __repr__(self):
                return f"Node({self.v})"
        print(f"{math.pi:.3f}", "{} {}".format(1, 2), deque([Node(1)]))
    ''')
    assert vlib.lint_python(src, ban_modules=("numpy", "torch")) == []


def test_lint_python_bans_named_calls():
    assert vlib.lint_python("print(divmod(7, 2))", ban_calls=("divmod",))
    assert vlib.lint_python("d = divmod\nprint(d(7, 2))", ban_calls=("divmod",))


# --- prover lint ----------------------------------------------------------------------

def test_prover_flags_dash_c_children_and_unscaled_limits():
    bad = ("import subprocess, sys\n"
           "subprocess.run([sys.executable, '-I', '-c', DRIVER], timeout=10)\n")
    problems = prove_task.isolation_problems("python", bad)
    assert any("-c" in p for p in problems)
    assert any("OPENBEAST_TIMEOUT_SCALE" in p for p in problems)
    good = ("import subprocess, sys, vlib\n"
            "subprocess.run([sys.executable, '-I', '-S', '-'], input=DRIVER, timeout=vlib.t(10))\n")
    assert prove_task.isolation_problems("python", good) == []
    assert prove_task.isolation_problems("bash", "cd /tmp/eval_x && python3 -c 'print(1)'")
    assert prove_task.isolation_problems("bash", "python3 -I - <<'EOF'\nprint(1)\nEOF\n") == []
