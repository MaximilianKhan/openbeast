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
