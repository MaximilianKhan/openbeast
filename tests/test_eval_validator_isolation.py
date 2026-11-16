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
        # The script file itself was unlinked before the validator started;
        # beside the cwd there is only the private build cache.
        assert sorted(os.listdir(os.path.dirname(cwd))) == ["build-cache", "cwd"], \
            os.listdir(os.path.dirname(cwd))
        print("OK")
    '''))
    assert ok, output


@pytest.mark.parametrize("phase", ["setup", "pre_validate", "cleanup"])
def test_fixture_commands_ignore_a_plant_in_the_harness_cwd(tmp_path, monkeypatch, phase):
    # 176, round 2: graded code found the harness cwd through the ppid chain
    # and wrote copy.py there; the next unit's setup (`python3 - <<EOF`, ''
    # on sys.path; tarfile imports copy) ran it, printed a forged OK and
    # never built its fixture.
    harness_cwd = tmp_path / "harness"
    harness_cwd.mkdir()
    (harness_cwd / "copy.py").write_text('print("PLANTED"); raise SystemExit(0)\n')
    out = tmp_path / "out.txt"
    monkeypatch.chdir(harness_cwd)
    cmd = ("python3 - %s <<'EOF'\n"
           "import os, sys, tarfile\n"
           "open(sys.argv[1], 'w').write(repr((os.getcwd(), sys.path[0], tarfile.copy.__file__,"
           " os.environ.get('PYTHONSAFEPATH'))))\n"
           "EOF\n") % out
    run = {"setup": lambda: run_eval.run_setup({"setup": cmd}, log=print),
           "pre_validate": lambda: run_eval.run_pre_validate({"pre_validate": cmd}, log=print),
           "cleanup": lambda: run_eval.run_cleanup({"cleanup": cmd}, log=print)}[phase]
    assert run() in (True, None)
    cwd, path0, copy_file, safe = eval(out.read_text())
    assert os.path.realpath(cwd) != os.path.realpath(harness_cwd)
    assert not copy_file.startswith(str(harness_cwd)), copy_file
    # PYTHONSAFEPATH needs 3.11+ (python3 on PATH may be older): the fresh
    # private cwd is what keeps the plant out on every version.
    assert safe == "1"
    assert path0 == "" or os.path.realpath(path0) != os.path.realpath(harness_cwd)
    assert not os.path.exists(cwd), "the fixture cwd outlived the command"


@pytest.mark.skipif(not DARWIN, reason="sandbox-exec is macOS-only")
def test_sandbox_allow_write_denies_writes_elsewhere(tmp_path):
    ok_dir, other = tmp_path / "ok", tmp_path / "other"
    ok_dir.mkdir()
    other.mkdir()
    prefix = vlib._sandbox_prefix(False, [], allow_write=[str(ok_dir)])
    assert prefix, "sandbox-exec unavailable"
    src = textwrap.dedent(f'''
        import os
        open({str(ok_dir / "a")!r}, "w").write("x")
        open("/dev/null", "w").write("x")
        try:
            open({str(other / "b")!r}, "w").write("x")
            print("WROTE")
        except OSError:
            print("DENIED")
    ''')
    r = subprocess.run(prefix + [sys.executable, "-I", "-c", src], capture_output=True, text=True,
                       timeout=60)
    assert r.stdout.strip() == "DENIED", (r.stdout, r.stderr)
    assert (ok_dir / "a").exists() and not (other / "b").exists()


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


# --- confine: a read ALLOWLIST on both platforms -------------------------------------
#
# The 190 verifier read the oracle from the unsealed git object store and,
# with anaconda's interpreter, imported torch from site-packages. confine=
# flips the seal into an allowlist: the stdlib, the work dir, nothing else.

def _confined_can_linux():
    if not LINUX:
        return True
    if vlib._landlock_abi() >= 1:
        return True
    r = subprocess.run(["unshare", "-Ur", "true"], capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(not (DARWIN or LINUX), reason="macOS or Linux only")
def test_confined_solution_reads_only_its_runtime_and_work_dir(tmp_path):
    if not _confined_can_linux():
        pytest.skip("no landlock and no user namespaces on this host")
    secret = tmp_path / "repo" / ".git" / "objects" / "ab" / "cdef"
    secret.parent.mkdir(parents=True)
    secret.write_text(ORACLE)
    work = tmp_path / "work"
    work.mkdir()
    (work / "in.txt").write_text("input")
    import sysconfig
    site = sysconfig.get_path("purelib")
    probe = textwrap.dedent(f"""
        import os, json, math, decimal
        got = {{}}
        for p in [{str(secret)!r}, {os.path.expanduser("~")!r}, {site!r}, "/etc/passwd"]:
            try:
                if os.path.isdir(p):
                    got[p] = len(os.listdir(p)) and "listed"
                else:
                    got[p] = open(p).read(40)
            except OSError:
                got[p] = None
        got["in"] = open("in.txt").read()
        open("out.txt", "w").write("ok")
        print(json.dumps(got))
    """)
    rt = vlib.python_runtime(sys.executable, ["json", "math", "decimal", "os"])
    assert not any("site-packages" in p.split(os.sep) for p in rt), rt
    r = vlib.run_solution([sys.executable, "-I", "-S", "-c", probe], cpu_s=10, cwd=str(work),
                          confine=[str(work)], read_only=rt)
    assert r.ok, (r, r.stderr[-800:])
    import json
    got = json.loads(r.stdout)
    assert got.pop("in") == "input"
    assert (work / "out.txt").read_text() == "ok"
    assert ORACLE not in r.stdout
    assert all(v in (None, 0) for v in got.values()), got


def test_confine_fails_closed_when_it_cannot_be_set_up(monkeypatch, tmp_path):
    monkeypatch.setattr(vlib, "_sandbox_ok", lambda: False)

    def broken(*a, **k):
        raise OSError("no landlock, no userns")
    monkeypatch.setattr(vlib, "_confine_linux", broken)
    r = vlib.run_solution(_py("print('ran')"), cpu_s=5, confine=[str(tmp_path)])
    assert r.verdict == "sandbox" and "ran" not in r.stdout, r


def test_seal_covers_every_checkout_and_the_git_store():
    # A solution run from a worktree read the main checkout's
    # evals/tasks/*.json, and the shared .git holds every branch's validators.
    seal = run_eval.validator_seal()
    try:
        common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute",
                                 "--git-common-dir"], capture_output=True, text=True)
    except OSError:
        pytest.skip("git is not installed")
    if common.returncode != 0:
        pytest.skip("not a git checkout")
    common = common.stdout.strip()
    main = os.path.dirname(common.rstrip(os.sep))
    assert common in seal
    for d in ("tasks", "refs", "suites"):
        assert os.path.join(main, "evals", d) in seal
        assert str(ROOT / "evals" / d) in seal
    assert run_eval._validator_env(1.0)["OPENBEAST_VALIDATOR_SEAL"] == os.pathsep.join(seal)


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


# =============================================================================
# v5 repair round 3: harness-wide confinement (brief §6.3, v5issues3/harness).
# The round-2 verifiers passed families without solving them by (1) reaching
# the network, (2) reading the validator's argv, (3) exec'ing python3 from a
# compiled variant, and (4) capturing the toolchain with a planted config.
# Each test below rebuilds one attack against the REAL vlib / run_eval.
# =============================================================================

import shutil

CLANG = shutil.which("clang") or shutil.which("cc")
GO = shutil.which("go")


def _run(src_argv, **kw):
    return vlib.run_solution(src_argv, **kw)


# --- 1. network denied -----------------------------------------------------------

@pytest.mark.skipif(not DARWIN, reason="network deny is enforced by sandbox-exec on macOS")
def test_solution_cannot_reach_the_network():
    src = ("import socket\n"
           "try:\n"
           "    socket.create_connection(('1.1.1.1', 80), 2); print('CONNECTED')\n"
           "except Exception as e:\n"
           "    print('BLOCKED', type(e).__name__)\n")
    r = vlib.run_solution(vlib.python_cmd("/dev/stdin"), input=src, cpu_s=5)
    assert "CONNECTED" not in r.stdout, r
    assert not r.ok or "BLOCKED" in r.stdout, r


# --- 2. cannot read the parent's argv/env ----------------------------------------

@pytest.mark.skipif(not DARWIN, reason="process-info deny is enforced by sandbox-exec on macOS")
def test_solution_cannot_read_parent_argv_via_run_solution():
    src = ("import os, subprocess\n"
           "r = subprocess.run(['ps', '-ww', '-o', 'command=', '-p', str(os.getppid())],\n"
           "                   capture_output=True, text=True)\n"
           "print('PS<' + r.stdout.strip() + '>')\n")
    r = vlib.run_solution(vlib.python_cmd("/dev/stdin"), input=src, cpu_s=5)
    # ps is denied process-info, so it returns nothing about the parent.
    assert "PS<>" in r.stdout or "python" not in r.stdout.lower(), r


# --- 3. a compiled variant cannot exec into python3 ------------------------------

_EXEC_C = r'''
#include <unistd.h>
#include <stdio.h>
int main(void) {
    char *a[] = {"python3", "-c", "print('SOLVED-BY-PYTHON')", 0};
    printf("BINARY-RAN\n"); fflush(stdout);
    execvp("python3", a);
    printf("EXEC-DENIED\n");
    return 3;
}
'''


@pytest.mark.skipif(not (DARWIN and CLANG), reason="needs clang on macOS for the exec sandbox")
def test_compiled_solution_cannot_exec_python3(tmp_path):
    src = tmp_path / "m.c"
    src.write_text(_EXEC_C)
    binp = tmp_path / "m"
    subprocess.run([CLANG, "-O2", "-o", str(binp), str(src)], check=True)
    assert vlib.is_native_executable(str(binp))
    r = vlib.run_solution([str(binp)], cpu_s=5)
    assert "BINARY-RAN" in r.stdout, r
    assert "SOLVED-BY-PYTHON" not in r.stdout, r  # the interpreter never ran
    assert not r.ok, r


def test_confine_exec_default_leaves_the_harness_interpreter_open():
    # A python solution still runs under the harness interpreter (which may
    # re-exec itself into its framework): confine_exec defaults off for it.
    r = vlib.run_solution(vlib.python_cmd("/dev/stdin"), input="print('PYOK')", cpu_s=5)
    assert r.ok and "PYOK" in r.stdout, r


# --- 4. planted toolchain config is ignored --------------------------------------

def test_private_build_dir_has_no_planted_config_ancestor(tmp_path):
    agent = tmp_path / "agent"
    (agent / ".cargo").mkdir(parents=True)
    (agent / ".cargo" / "config.toml").write_text("[build]\ntarget-dir='/evil'\n")
    (agent / "rust-toolchain.toml").write_text("[toolchain]\npath='/evil/tc'\n")
    # The agent plants in its own dir; the build dir is created elsewhere.
    d = vlib.private_build_dir({"main.rs": "fn main() {}"}, base=str(tmp_path / "build"))
    found = vlib.scan_toolchain_overrides(d, stop=str(tmp_path / "build"))
    assert found == [], found
    # And the planted files really are outside the build dir's ancestry.
    assert str(agent) not in d


def test_scan_toolchain_overrides_finds_a_planted_file(tmp_path):
    d = vlib.private_build_dir({"main.rs": "fn main() {}"}, base=str(tmp_path))
    (Path(d).parent / "rust-toolchain.toml").write_text("x")
    found = vlib.scan_toolchain_overrides(d, stop=str(tmp_path))
    assert any(p.endswith("rust-toolchain.toml") for p in found), found


def test_build_env_disables_go_env_and_clears_goflags(monkeypatch):
    monkeypatch.setenv("GOFLAGS", "-ldflags=-s")  # the 166 symbol-table strip
    monkeypatch.setenv("HOME", "/should/be/replaced")
    env = vlib.build_env()
    assert env["GOENV"] == "off"
    assert env["GOFLAGS"] == ""
    assert env["GOTOOLCHAIN"] in ("local", os.environ.get("GOTOOLCHAIN", "local"))
    assert env["HOME"] != "/should/be/replaced"
    assert os.path.isdir(env["HOME"])


@pytest.mark.skipif(not GO, reason="needs the go toolchain")
def test_go_build_ignores_a_planted_goflags_in_a_private_home(tmp_path):
    # With a $HOME/.../go/env carrying GOFLAGS=-ldflags=-s the symbol table
    # would be stripped; build_env's GOENV=off and cleared GOFLAGS stop that.
    d = vlib.private_build_dir({
        "go.mod": "module m\n\ngo 1.21\n",
        "main.go": 'package main\nimport "fmt"\nfunc main(){fmt.Println("hi")}\n',
    }, base=str(tmp_path / "b"))
    home = tmp_path / "home"
    cfg = home / "Library" / "Application Support" / "go"
    cfg.mkdir(parents=True)
    (cfg / "env").write_text("GOFLAGS=-ldflags=-s\n")
    env = vlib.build_env(home=str(home))
    binp = os.path.join(d, "prog")
    r = subprocess.run([vlib.tool_path("go"), "build", "-o", binp, "."],
                       cwd=d, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert vlib.is_native_executable(binp)
    syms = subprocess.run(["nm", binp], capture_output=True, text=True)
    # The symbol table survived: the planted strip was not honoured.
    assert "no symbols" not in (syms.stdout + syms.stderr).lower(), syms.stdout[:200]


# --- native-executable gate ------------------------------------------------------

def test_is_native_executable_rejects_a_script(tmp_path):
    s = tmp_path / "fake"
    s.write_text("#!/usr/bin/env python3\nprint('i am a script')\n")
    assert not vlib.is_native_executable(str(s))
    with pytest.raises(AssertionError):
        vlib.assert_native_executable(str(s))


@pytest.mark.skipif(not CLANG, reason="needs a C compiler")
def test_is_native_executable_accepts_a_real_binary(tmp_path):
    src = tmp_path / "m.c"
    src.write_text("int main(void){return 0;}")
    binp = tmp_path / "m"
    subprocess.run([CLANG, "-o", str(binp), str(src)], check=True)
    assert vlib.is_native_executable(str(binp))


# --- 5. the oracle seal is structural, not just textual --------------------------

def _seal_tools():
    sys.path.insert(0, str(ROOT / "agents"))
    return importlib.import_module("tools")


def test_seal_refuses_quote_split_and_git_blob_bypasses(monkeypatch):
    tools = _seal_tools()
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_EVAL_SEALED_DIRS",
                       os.pathsep.join(str(ROOT / "evals" / d) for d in ("tasks", "refs", "suites")))
    for cmd in ['cat evals/ta""sks/x.json',
                'grep seed evals/re""fs/y.py',
                'git show HEAD:evals/refs/z.py',
                'git grep gen_cases HEAD -- evals/tasks']:
        out = tools.bash(cmd)
        assert "refusing to run" in out, (cmd, out[:120])


@pytest.mark.skipif(not DARWIN, reason="read-deny is enforced by sandbox-exec on macOS")
def test_seal_blocks_a_relative_read_the_text_check_misses(tmp_path, monkeypatch):
    tools = _seal_tools()
    sealed = ROOT / "evals" / "tasks"
    sealed.mkdir(parents=True, exist_ok=True)
    probe = sealed / "zz_seal_probe.json"
    probe.write_text("ORACLE-SEED-0xDEADBEEF")
    try:
        monkeypatch.setenv("OPENBEAST_EVAL", "1")
        monkeypatch.setenv("OPENBEAST_EVAL_SEALED_DIRS",
                           os.pathsep.join(str(ROOT / "evals" / d) for d in ("tasks", "refs", "suites")))
        monkeypatch.setattr(tools, "_base_dir", lambda: str(ROOT))
        out = tools.bash("cd evals && cat tasks/zz_seal_probe.json 2>&1")
        assert "0xDEADBEEF" not in out, out
        assert "not permitted" in out.lower() or "refusing" in out.lower(), out
    finally:
        probe.unlink(missing_ok=True)


# --- (a) every build gets a private, throw-away cache --------------------------------
#
# 164's verifier linked 40 of 40 objects that sealed reference builds had left in
# ~/.cache/zig (`zig build-exe` keeps the whole compilation unit there), from
# inside the measured agent's bash. Each validation now gets its own caches in
# its private dir, and they are deleted with it.

_CACHE_PROBE = textwrap.dedent(r'''
    import os, sys
    import vlib
    root = os.environ.get("OPENBEAST_BUILD_CACHE") or ""
    bad = []
    if os.path.basename(root) != "build-cache":
        bad.append("root " + root)
    if not os.path.basename(os.path.dirname(root)).startswith("ob-validator-"):
        bad.append("parent " + root)
    b = vlib.build_env()
    for k in ("ZIG_GLOBAL_CACHE_DIR", "ZIG_LOCAL_CACHE_DIR", "GOCACHE", "GOMODCACHE",
              "CARGO_TARGET_DIR", "XDG_CACHE_HOME"):
        v = os.environ.get(k) or ""
        if not v.startswith(root + os.sep):
            bad.append(k)
            continue
        open(os.path.join(v, "obj.o"), "w").write("compiled-reference")
        if k != "XDG_CACHE_HOME" and b.get(k) != v:
            bad.append("build_env " + k)
    if os.environ.get("CCACHE_DISABLE") != "1" or os.environ.get("SCCACHE_DISABLE") != "1":
        bad.append("ccache")
    if os.environ.get("RUSTC_WRAPPER") or "RUSTC_WRAPPER" in b:
        bad.append("wrapper")
    if os.environ.get("GOPROXY") != "off":
        bad.append("goproxy")
    print("ROOT", root)
    print("BAD", bad)
    sys.exit(1 if bad else 0)
''')


def test_validation_builds_into_a_private_cache_that_is_deleted(monkeypatch, tmp_path):
    shared = tmp_path / "shared-cache"
    for k in ("ZIG_GLOBAL_CACHE_DIR", "ZIG_LOCAL_CACHE_DIR", "GOCACHE", "GOMODCACHE",
              "XDG_CACHE_HOME"):
        monkeypatch.setenv(k, str(shared / k))       # the user's shared caches
    monkeypatch.setenv("RUSTC_WRAPPER", "sccache")
    task = {"id": "cache", "validation": {"type": "python", "script": _CACHE_PROBE}}
    ok, out = run_eval.run_validation(task)
    assert ok, out
    root = out.split("ROOT", 1)[1].split()[0]
    # The cache, and everything a build left in it, is gone with the validator.
    assert not os.path.exists(root), root
    assert not os.path.exists(os.path.dirname(root))
    assert not shared.exists(), "the shared caches were never written"


def test_build_env_outside_the_harness_never_uses_the_shared_cache(monkeypatch):
    monkeypatch.delenv("OPENBEAST_BUILD_CACHE", raising=False)
    monkeypatch.setenv("ZIG_GLOBAL_CACHE_DIR", os.path.expanduser("~/.cache/zig"))
    env = vlib.build_env()
    for k in ("ZIG_GLOBAL_CACHE_DIR", "ZIG_LOCAL_CACHE_DIR", "GOCACHE", "GOMODCACHE",
              "CARGO_TARGET_DIR"):
        assert env[k].startswith(env["HOME"] + os.sep), (k, env[k])
    assert env["CCACHE_DISABLE"] == "1"


def test_private_cache_removal_survives_read_only_entries(tmp_path):
    # A Go module cache is read-only unless -modcacherw held; the removal must
    # not leave a compiled object behind because a directory was 0555.
    d = tmp_path / "c" / "mod" / "x@v1"
    d.mkdir(parents=True)
    (d / "obj.o").write_text("x")
    os.chmod(d / "obj.o", 0o444)
    os.chmod(d, 0o555)
    run_eval._rmtree_force(str(tmp_path / "c"))
    assert not (tmp_path / "c").exists()


@pytest.mark.skipif(not __import__("shutil").which("zig"), reason="zig not installed")
def test_a_zig_build_in_a_validator_leaves_nothing_in_the_user_cache(monkeypatch, tmp_path):
    user_cache = tmp_path / "user-zig-cache"
    monkeypatch.setenv("ZIG_GLOBAL_CACHE_DIR", str(user_cache))
    src = 'pub fn main() void {}\n'
    script = textwrap.dedent(f'''
        import os, subprocess, tempfile
        d = tempfile.mkdtemp()
        open(os.path.join(d, "m.zig"), "w").write({src!r})
        r = subprocess.run(["zig", "build-exe", "m.zig", "-femit-bin=" + os.path.join(d, "m")],
                           cwd=d, capture_output=True, text=True)
        print(r.returncode, r.stderr[-300:])
        objs = sum(len(f) for _, _, f in os.walk(os.environ["ZIG_GLOBAL_CACHE_DIR"]))
        print("OBJS", objs)
        raise SystemExit(r.returncode)
    ''')
    ok, out = run_eval.run_validation({"id": "z", "validation": {"type": "python",
                                                                "script": script}},
                                      timeout_scale=4.0)
    assert ok, out
    assert int(out.split("OBJS")[1].split()[0]) > 0, "the build used the private cache"
    assert not user_cache.exists() or not any(user_cache.rglob("*")), \
        "a validator's zig build wrote into the user's cache"


# --- (b) nothing the agent started survives into validation ---------------------------
#
# 190's verifier: run_agent only walked and killed the process tree on a timeout,
# and a daemon that detaches properly (double fork + setsid, stdio off the pipe)
# outlives the tool call, runs as our uid, and could rewrite the solution after the
# lint or burn the CPU a budgeted solution is measured against. Now every agent is
# watched (tag in env, tree polling, its groups and sessions, a cgroup on Linux) and
# swept before validation, and survivors keep the unit from being validated.

_DAEMON_PY = textwrap.dedent(r'''
    import os, sys
    pidfile, scrub = sys.argv[1], sys.argv[2] == "1"
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    fd = os.open(os.devnull, os.O_RDWR)
    for i in (0, 1, 2):
        os.dup2(fd, i)
    argv = ["/bin/sleep", "300"]
    open(pidfile + ".tmp", "w").write(str(os.getpid()))
    os.rename(pidfile + ".tmp", pidfile)
    if scrub:
        os.execve(argv[0], argv, {})        # env -i: no tag left to find
    os.execv(argv[0], argv)
''')


def _fake_runner(tmp_path, body: str) -> str:
    """A stand-in for agents/runner.py that starts daemons the way a model
    would: through the REAL tools.bash, under the eval marker run_agent sets
    (so on macOS each command runs in the eval sandbox, tag file included)."""
    (tmp_path / "daemon.py").write_text(_DAEMON_PY)
    p = tmp_path / "fake_runner.py"
    p.write_text(textwrap.dedent(f'''
        import os, sys, time
        sys.path.insert(0, {str(ROOT / "agents")!r})
        import tools
        def daemon(pidfile, scrub):
            out = tools.bash(f"{{sys.executable}} {str(tmp_path / 'daemon.py')} "
                             f"{{pidfile}} {{1 if scrub else 0}}", timeout=30)
            assert "refusing" not in out.lower(), out
    ''') + textwrap.dedent(body))
    return str(p)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return pid in run_eval._proc_snapshot()       # a zombie is not alive


def _wait_pidfile(p: Path, timeout=10.0) -> int:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            return int(p.read_text())
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError(f"no pid in {p}")


def _kill_pidfiles(*files):
    for f in files:
        try:
            os.kill(int(f.read_text()), 9)
        except (OSError, ValueError):
            pass


def test_detached_agent_daemons_are_killed_before_validation(monkeypatch, tmp_path):
    scrubbed, tagged = tmp_path / "scrubbed.pid", tmp_path / "tagged.pid"
    runner = _fake_runner(tmp_path, f'''
        daemon({str(scrubbed)!r}, scrub=True)   # setsid + double fork + env -i
        daemon({str(tagged)!r}, scrub=False)    # setsid + double fork
        time.sleep(1.0)                         # a model turn before the runner exits
        print("TOKENS: prompt=1 completion=1 total=2")
    ''')
    monkeypatch.setattr(run_eval, "RUNNER_PATH", runner)
    # An unrelated process of ours, started outside the agent: never touched.
    bystander = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
    try:
        r = run_eval.run_agent({"id": "leak", "task": "t", "max_iter": 1},
                               "http://127.0.0.1:9", wall_s=60)
        assert r["exit_code"] == 0, r
        pids = [_wait_pidfile(scrubbed), _wait_pidfile(tagged)]
        assert r["leftovers_killed"] >= 2, r
        assert not r.get("leftover_survivors"), r
        time.sleep(0.2)
        assert not any(_alive(p) for p in pids), pids
        assert bystander.poll() is None, "the sweep killed a process the agent never started"
    finally:
        bystander.kill()
        bystander.wait()
        _kill_pidfiles(scrubbed, tagged)


def test_daemons_are_swept_on_the_timeout_path_too(monkeypatch, tmp_path):
    pidf = tmp_path / "d.pid"
    runner = _fake_runner(tmp_path, f'''
        daemon({str(pidf)!r}, scrub=True)
        time.sleep(300)
    ''')
    monkeypatch.setattr(run_eval, "RUNNER_PATH", runner)
    try:
        r = run_eval.run_agent({"id": "leak", "task": "t", "max_iter": 1},
                               "http://127.0.0.1:9", wall_s=4)
        pid = _wait_pidfile(pidf)
        assert r["stdout"] == "(timed out)", r
        assert not r.get("leftover_survivors") and r["leftovers_killed"] >= 1, r
        time.sleep(0.2)
        assert not _alive(pid)
    finally:
        _kill_pidfiles(pidf)


@pytest.mark.skipif(not DARWIN, reason="the sandbox tag is macOS's mechanism")
def test_the_sandbox_tag_marks_only_the_agents_processes(tmp_path):
    w = run_eval.AgentWatch("t")
    try:
        assert w.tag_file and os.path.isfile(w.tag_file)
        assert run_eval._sandbox_tagged(os.getpid(), w.tag_file) == 0
        prof = f'(version 1)(allow default)(deny file-read* (literal "{w.tag_file}"))'
        inside = subprocess.Popen(["/usr/bin/sandbox-exec", "-p", prof, "/bin/sleep", "30"],
                                  start_new_session=True)
        outside = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        time.sleep(0.3)
        try:
            assert run_eval._sandbox_tagged(inside.pid, w.tag_file) == 1
            assert run_eval._sandbox_tagged(outside.pid, w.tag_file) == 0
            assert inside.pid in w.candidates() and outside.pid not in w.candidates()
            # A deleted tag file would make every process look denied: the
            # control check must then turn the sandbox test off, not kill all.
            os.unlink(w.tag_file)
            assert not w._sandbox_tag_usable()
            assert outside.pid not in w.candidates()
        finally:
            inside.kill(); outside.kill(); inside.wait(); outside.wait()
    finally:
        run_eval.sweep_agent_leftovers(w)
        assert not os.path.exists(os.path.dirname(w.tag_file))


def test_the_sweep_asserts_none_survive(monkeypatch):
    # A leftover that cannot be killed (another uid, D state) is reported,
    # never ignored, and the unit is not validated.
    w = run_eval.AgentWatch("t")
    monkeypatch.setattr(w, "candidates", lambda: {424242})
    monkeypatch.setattr(run_eval, "_SWEEP_ROUNDS", 2)
    killed, survivors = run_eval.sweep_agent_leftovers(w)
    assert survivors == [424242], (killed, survivors)


def test_a_unit_with_surviving_leftovers_is_not_validated(monkeypatch, tmp_path):
    import json
    validated = []
    monkeypatch.setattr(run_eval, "run_validation",
                        lambda *a, **k: validated.append(1) or (True, "OK"))
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: {
        "exit_code": 0, "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
        "tokens": {"prompt": 1, "completion": 1, "total": 2},
        "leftovers_killed": 0, "leftover_survivors": [4242]})
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "1_x.json").write_text(json.dumps({
        "id": "1_x", "name": "x", "difficulty": "easy", "task": "t", "max_iter": 1,
        "validation": {"type": "bash", "script": "true"}}))
    monkeypatch.setattr(run_eval, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(run_eval, "RESULTS_DIR", str(tmp_path / "results"))
    out = run_eval.run_eval(model_name="m", use_cache=False, task_filter=["1_x"])
    row = out["tasks"][0]
    assert not validated and not row["passed"], row
    assert "could not be killed" in row.get("validation_output", ""), row


@pytest.mark.skipif(not __import__("shutil").which("cc"), reason="no C compiler")
def test_exec_confinement_resolves_a_relative_argv_against_the_solutions_cwd(tmp_path):
    # Compiled validators run `./divmod` with cwd=/tmp/eval_x. exec_only named
    # that path resolved against the VALIDATOR's cwd, so the sandbox denied the
    # solution its own image and every compiled unit exited 71.
    (tmp_path / "p.c").write_text('#include <stdio.h>\nint main(void){puts("ran");return 0;}\n')
    subprocess.run(["cc", "-o", str(tmp_path / "p"), str(tmp_path / "p.c")], check=True)
    r = vlib.run_solution(["./p"], cpu_s=5, cwd=str(tmp_path))
    assert r.ok and "ran" in r.stdout, (r, r.stderr[-300:])
    assert vlib._resolve_exec("./p", str(tmp_path), {}) == os.path.realpath(tmp_path / "p")


@pytest.mark.skipif(not __import__("shutil").which("cc"), reason="no C compiler")
def test_exec_confinement_allows_an_env_launcher_and_its_one_target(tmp_path):
    # 122 runs `/usr/bin/env DYLD_INSERT_LIBRARIES=<monitor> ./gemm` (sandbox-exec
    # drops DYLD_*). Confining exec to env alone denied the solution itself.
    (tmp_path / "p.c").write_text('#include <stdio.h>\n#include <stdlib.h>\n'
                                  'int main(void){puts(getenv("OB_X"));return 0;}\n')
    subprocess.run(["cc", "-o", str(tmp_path / "p"), str(tmp_path / "p.c")], check=True)
    r = vlib.run_solution(["/usr/bin/env", "OB_X=seen", "./p"], cpu_s=5, cwd=str(tmp_path))
    assert r.ok and "seen" in r.stdout, (r, r.stderr[-300:])
    if DARWIN:
        # ...and still nothing else: the target may not exec a third image.
        (tmp_path / "q.c").write_text('#include <unistd.h>\n'
                                      'int main(void){execl("/bin/echo","echo","escaped",(char*)0);'
                                      'return 3;}\n')
        subprocess.run(["cc", "-o", str(tmp_path / "q"), str(tmp_path / "q.c")], check=True)
        r = vlib.run_solution(["/usr/bin/env", "./q"], cpu_s=5, cwd=str(tmp_path))
        assert "escaped" not in r.stdout, r
