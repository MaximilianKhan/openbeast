"""G24 (2026-11-17): the eval-harness defects a transcript audit of
GLM-5.3-Flash's v5 runs found (docs/HARNESS_NOTES.md G24-G27).

  1. run_eval kept a sentence-final period on the task's /tmp/eval* paths,
     so the done-check demanded `gemm.c.` (85 turns in arm C).
  2. The eval seal let the agent read ~/.claude, the harness source and
     evals/cache (arm C 122_b).
  3. The agent's `python3` was not the validators' python.
  4. evals/audit/integrity_scan.py finds such reads in transcripts.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "evals"))

import tools  # noqa: E402

DARWIN = sys.platform == "darwin"


def _run_eval():
    spec = importlib.util.spec_from_file_location("run_eval_g24", ROOT / "evals" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _unit(uid):
    re_ = _run_eval()
    return {t["id"]: t for t in re_.load_tasks(None)}[uid]


def _spec(t):
    return " ".join(str(t.get(k, "")) for k in ("task", "validation", "setup", "cleanup"))


# --- 1. the trailing-dot path bug ----------------------------------------------

@pytest.mark.parametrize("uid,want", [
    ("122_gemm_blocked_b", "/tmp/eval_gemm/gemm.c"),
    ("122_gemm_blocked_d", "/tmp/eval_gemm/gemm.rs"),
    ("163_bigint_divmod_a", "/tmp/eval_divmod"),
    ("167_q15_biquad_c", "/tmp/eval_q15biquad/sample_output.txt"),
    ("168_linearizability_kv_f", "/tmp/eval_linz/sample_output.txt"),
    ("178_codebase_qa", "/tmp/eval_codeqa/answers.json"),
])
def test_task_paths_drop_the_sentence_period(uid, want):
    re_ = _run_eval()
    t = _unit(uid)
    paths = re_.task_paths(_spec(t))
    assert want in paths
    assert not any(p.endswith(".") for p in paths), paths
    # the old extraction really did keep the period for this unit
    import re
    old = re.findall(r"/tmp/eval[\w\-./]*", _spec(t))
    assert any(p.endswith(".") for p in old)


def test_no_unit_in_the_suite_expects_a_dotted_path():
    re_ = _run_eval()
    for t in re_.load_tasks(None):
        for p in re_.task_paths(_spec(t)):
            assert not p.endswith("."), (t["id"], p)
        for root in re_.fixture_roots(t):
            assert not root.endswith("."), (t["id"], root)


def test_task_paths_keep_real_paths():
    re_ = _run_eval()
    text = ("Create /tmp/eval_x/a.py. Read /tmp/eval_x/in.txt and /tmp/eval_x/sub/ "
            "then /tmp/eval_x/b.tar.gz... Done: /tmp/eval_y")
    assert re_.task_paths(text) == ["/tmp/eval_x/a.py", "/tmp/eval_x/b.tar.gz",
                                    "/tmp/eval_x/in.txt", "/tmp/eval_x/sub/", "/tmp/eval_y"]


def test_run_agent_hands_the_agent_undotted_paths(monkeypatch):
    re_ = _run_eval()
    t = dict(_unit("122_gemm_blocked_c"), max_iter=2)
    captured = {}

    class _Proc:
        returncode = 0
        pid = 424245

        def communicate(self, timeout=None):
            return ("TOKENS: prompt=1 completion=1 total=2\n", "")

        def kill(self):
            pass

    def fake_popen(cmd, **kw):
        if "--max-iter" in cmd:
            captured["env"] = kw["env"]
        return _Proc()
    monkeypatch.setattr(re_.subprocess, "Popen", fake_popen)
    re_.run_agent(t, "http://127.0.0.1:9/v1", wall_s=60.0)
    paths = json.loads(captured["env"]["OPENBEAST_TASK_PATHS"])
    assert "/tmp/eval_gemm/gemm.cpp" in paths and "/tmp/eval_gemm/gemm.cpp." not in paths


def test_done_check_accepts_the_real_file(tmp_path, monkeypatch):
    real = tmp_path / "gemm.c"
    real.write_text("int main(void){return 0;}\n")
    # a stale env from an older harness: the tool layer strips it as well
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(real) + "."]))
    assert tools.task_done("done").startswith("TASK_DONE")


def test_harness_fingerprint_covers_task_paths():
    import cache
    assert "task_paths" in cache.HARNESS_FUNCTIONS
    assert "_FIXTURE_PATH" in cache.HARNESS_CONSTANTS


# --- 2. the seal: harness, results, transcripts, ~/.claude, scratch ------------

@pytest.fixture
def evalmode(monkeypatch):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    return monkeypatch


@pytest.mark.parametrize("rel", ["evals/cache.py", "evals/vlib.py", "evals/SUITE_VERSION",
                                 "evals/README.md", "tests/test_eval_seal.py"])
def test_read_file_refuses_the_harness_and_tests(evalmode, rel):
    out = tools.read_file(str(ROOT / rel))
    assert out.startswith("Error: refusing"), out[:120]
    assert "sealed while a unit is measured" in out and "/tmp/eval*" in out


def test_read_file_refuses_claude_home_and_scratch(evalmode, tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".claude" / "projects" / "journal.jsonl").write_text("SECRET-JOURNAL\n")
    evalmode.setenv("HOME", str(home))
    out = tools.read_file(str(home / ".claude" / "projects" / "journal.jsonl"))
    assert out.startswith("Error: refusing") and "SECRET" not in out
    assert tools.list_files(str(home / ".claude")).startswith("Error: refusing")
    assert tools._is_sealed("/private/tmp/claude-501/x/scratchpad/wt-bench2/evals/cache/u.json")
    assert tools._is_sealed("/tmp/claude-0/anything")


def test_other_clones_are_sealed_by_pattern(evalmode):
    for p in ("/srv/other-clone/evals/cache/x.json", "/srv/c/evals/results/eval-x.json",
              "/srv/c/agents/logs/agent-1.jsonl", "/srv/c/evals/vlib.py",
              "/srv/c/evals/reference/runs/r/ledger.jsonl"):
        assert tools._is_sealed(p), p


def test_task_dirs_tmpdir_and_toolchains_stay_readable(evalmode, tmp_path):
    for p in ("/tmp/eval_gemm/gemm.c", "/private/tmp/eval_x/evals/cache/fixture.json",
              "/usr/include/stdio.h", str(ROOT / "agents" / "runner.py")):
        assert not tools._is_sealed(os.path.realpath(p) if p.startswith("/tmp") else p), p
    evalmode.setenv("TMPDIR", "/private/tmp/claude-501/sandbox-tmp")
    assert not tools._is_sealed("/private/tmp/claude-501/sandbox-tmp/go-build123/x.o")
    f = tmp_path / "work.py"
    f.write_text("print('ok')\n")
    assert "print('ok')" in tools.read_file(str(f))


def test_no_new_seal_outside_eval(monkeypatch):
    monkeypatch.delenv("OPENBEAST_EVAL", raising=False)
    assert tools.read_file(str(ROOT / "evals" / "cache.py")).startswith("[")
    assert tools._eval_read_deny_prefix() == []


@pytest.mark.skipif(not DARWIN, reason="the kernel read-deny is sandbox-exec (macOS)")
def test_bash_read_of_the_harness_fails_cleanly(evalmode):
    evalmode.setenv("OB_G24_REPO", str(ROOT))
    out = tools.bash("cd $OB_G24_REPO && cat evals/SUITE_VERSION evals/cache.py 2>&1 | head -3")
    assert "Operation not permitted" in out
    assert "[eval sandbox:" in out and "sealed" in out
    assert "harness_fingerprint" not in out
    out = tools.bash("ls ~/.claude 2>&1 | head -3")
    assert "Operation not permitted" in out or "No such file" in out


@pytest.mark.skipif(not DARWIN, reason="the kernel read-deny is sandbox-exec (macOS)")
def test_bash_task_work_still_runs(evalmode, tmp_path):
    d = Path("/tmp") / f"eval_g24_{os.getpid()}"
    d.mkdir(exist_ok=True)
    try:
        out = tools.bash(f"cd {d} && printf 'int main(void){{return 0;}}' > a.c && "
                         f"cc -o a a.c && ./a && echo BUILT-OK && ls /usr/include/stdio.h")
        assert "BUILT-OK" in out and "stdio.h" in out and "[eval sandbox" not in out
        # runner.py (the harness source outside evals/) is not an answer key
        assert "python3" in tools.bash(f"head -1 {ROOT}/agents/runner.py")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_bash_names_a_sealed_path_refused_by_text(evalmode):
    out = tools.bash(f"cat {ROOT}/evals/cache.py")
    assert out.startswith("Error: refusing to run") and "sealed" in out


# --- 3. the agent's python3 is the validators' ---------------------------------

def _fake_venv(tmp_path):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    py = venv / "bin" / "python"
    py.write_text("#!/bin/sh\necho FAKE-EVAL-PY \"$@\"\n")
    py.chmod(0o755)
    (venv / "bin" / "python3.12").symlink_to(py)
    pip = venv / "bin" / "pip"
    pip.write_text("#!/bin/sh\necho FAKE-PIP\n")
    pip.chmod(0o755)
    return py


def test_eval_shell_python_is_the_eval_py(evalmode, tmp_path):
    py = _fake_venv(tmp_path)
    evalmode.setenv("OPENBEAST_EVAL_PY", str(py))
    out = tools.bash("python3 --version; python --version; python3.12 -V; command -v pip || true")
    assert out.count("FAKE-EVAL-PY") == 3, out
    assert "FAKE-PIP" not in out                    # only the python names
    assert tools.shell_path("/usr/bin:/bin").split(os.pathsep)[0] == tools._eval_python_dir()


def test_eval_prefix_is_the_fallback(evalmode, tmp_path):
    py = _fake_venv(tmp_path)
    evalmode.delenv("OPENBEAST_EVAL_PY", raising=False)
    evalmode.setenv("OPENBEAST_EVAL_PREFIX", str(tmp_path))
    assert tools._eval_python() == str(py)


def test_python_unchanged_outside_eval(monkeypatch, tmp_path):
    py = _fake_venv(tmp_path)
    monkeypatch.delenv("OPENBEAST_EVAL", raising=False)
    monkeypatch.setenv("OPENBEAST_EVAL_PY", str(py))
    assert "FAKE-EVAL-PY" not in tools.bash("python3 --version 2>&1 || true")
    assert tools.shell_path("/a:/b") == "/a:/b"


def test_no_eval_py_leaves_path_alone(evalmode):
    evalmode.delenv("OPENBEAST_EVAL_PY", raising=False)
    evalmode.delenv("OPENBEAST_EVAL_PREFIX", raising=False)
    assert tools.shell_path("/a:/b") == "/a:/b"


@pytest.mark.skipif(not DARWIN, reason="the venv write-deny is sandbox-exec (macOS)")
def test_eval_venv_is_read_only_to_the_agent(evalmode, tmp_path):
    py = _fake_venv(tmp_path)
    evalmode.setenv("OPENBEAST_EVAL_PY", str(py))
    out = tools.bash(f"touch {tmp_path}/venv/planted 2>&1")
    assert "Operation not permitted" in out and not (tmp_path / "venv" / "planted").exists()


def test_env_block_reports_the_eval_python(evalmode, tmp_path):
    import runner
    py = _fake_venv(tmp_path)
    evalmode.setenv("OPENBEAST_EVAL_PY", str(py))
    assert runner._probe_version(["python3", "--version"]).startswith("FAKE-EVAL-PY")


# --- 4. the transcript integrity scan --------------------------------------------

def _scan_mod():
    """evals/audit/integrity_scan.py by path: evals/reference has a module
    of the same name, which another test may have imported first."""
    sys.path.insert(0, str(ROOT / "evals" / "audit"))
    spec = importlib.util.spec_from_file_location(
        "audit_integrity_scan", ROOT / "evals" / "audit" / "integrity_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _transcript(path, task, calls):
    with open(path, "w") as f:
        f.write(json.dumps({"type": "start", "task": task, "timestamp": "2026-11-16T21:00:00"}) + "\n")
        for i, (name, args, result) in enumerate(calls, 1):
            f.write(json.dumps({"type": "iteration", "number": i}) + "\n")
            f.write(json.dumps({"type": "tool_call", "name": name, "args": args,
                                "result": result}) + "\n")


def test_integrity_scan_flags_the_122b_reads(tmp_path):
    S = _scan_mod()
    p = tmp_path / "agent-1.jsonl"
    _transcript(p, "t", [
        ("bash", {"command": "cd /tmp/eval_gemm && gcc -o gemm gemm.c"}, "ok"),
        ("bash", {"command": "ls ~/.claude/projects/x/subagents/workflows"}, "journal.jsonl"),
        ("bash", {"command": "cd ~/Documents/projects/openbeast/evals/cache\nls -t *122*"},
         "glm.122_gemm_blocked_b.json"),
        ("bash", {"command": "cd ~/Documents/projects/openbeast && sed -n 1,9p agents/tools.py"},
         "import os"),
        ("read_file", {"path": "/Users/x/openbeast/evals/vlib.py"}, "Error: refusing to access"),
        ("bash", {"command": "find / -name '*gemm*' 2>/dev/null"},
         "/Users/x/openbeast/evals/refs/122_gemm_blocked/gemm.c\n/tmp/eval_gemm/gemm.c"),
    ])
    hits = S.scan_transcript(str(p))
    kinds = {h["iteration"]: (set(h["kinds"]), h["status"]) for h in hits}
    assert 1 not in kinds
    assert kinds[2] == ({"claude"}, "read")
    assert kinds[3][0] == {"evals"}
    assert kinds[4][0] == {"source"}
    assert kinds[5] == ({"evals"}, "refused")
    assert kinds[6][0] == {"listed"}
    assert S.contaminated(hits)


def test_integrity_scan_ignores_toolchains_and_fixtures(tmp_path):
    S = _scan_mod()
    p = tmp_path / "agent-2.jsonl"
    z = "/private/tmp/claude-501/p/s/scratchpad/toolchains/zig-aarch64-macos-0.16.0/lib/std"
    _transcript(p, "t", [
        ("bash", {"command": f"grep -rn 'pub fn write' {z}/posix.zig"}, f"{z}/posix.zig:1: x"),
        ("bash", {"command": "cd /tmp/eval_codeqa && cat tests/test_x.py evals/a.json"}, "x"),
        ("read_file", {"path": "/tmp/eval_x/evals/cache/fixture.json"}, "x"),
        # a fixture's own tests/ through a relative cd (GLM-UP 176_intermittent_500s_a)
        ("bash", {"command": "cd /tmp/eval_shopd500 && (cd tests && shasum -a 256 *.py)"}, "x"),
    ])
    assert not S.contaminated(S.scan_transcript(str(p)))


def test_integrity_scan_relative_cd_after_leaving_tmp_still_counts(tmp_path):
    S = _scan_mod()
    p = tmp_path / "agent-3.jsonl"
    _transcript(p, "t", [
        ("bash", {"command": "cd /Users/x/openbeast && (cd tests && cat test_a.py)"}, "x"),
        ("bash", {"command": "cd ~ && cd openbeast && cat evals/vlib.py"}, "x"),
    ])
    kinds = {h["iteration"]: set(h["kinds"]) for h in S.scan_transcript(str(p))}
    assert "tests" in kinds[1]
    assert "evals" in kinds[2]


def _start(path, task, ts):
    path.write_text(json.dumps({"type": "start", "task": task, "timestamp": ts}) + "\n")


def _results(path, ts, units, mtime=None):
    path.write_text(json.dumps({"timestamp": ts, "tasks": [
        {"id": u, "passed": True, "reason": "ok"} for u in units]}))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_back_to_back_runs_score_each_unit_on_its_own_arm(tmp_path, monkeypatch):
    """The next arm starts seconds after the last save: its first units must
    not bleed into this arm's window (no forward slack past the end)."""
    S = _scan_mod()
    T = S.T
    monkeypatch.setattr(S, "load_units", lambda tree: {"a": {"task": "do a"}, "b": {"task": "do b"}})
    logs = tmp_path / "logs"
    logs.mkdir()
    _start(logs / "agent-1.jsonl", "do a", "2026-11-21T22:42:00")       # arm 1
    _start(logs / "agent-2.jsonl", "do b", "2026-11-22T04:40:00")       # arm 1
    _start(logs / "agent-3.jsonl", "do a", "2026-11-22T04:46:49")       # arm 2, 3 s later
    end1 = T._ts("2026-11-22T04:46:46")
    r1 = tmp_path / "r1.json"
    _results(r1, "2026-11-21T22:41:52", ["a", "b"], mtime=end1)
    arm = S.scan_arm("ARM1", [str(r1)], str(tmp_path), str(logs), False)
    scored = {r["unit"]: os.path.basename(r["transcript"])
              for rs in arm["units"].values() for r in rs if r["scored"]}
    assert scored == {"a": "agent-1.jsonl", "b": "agent-2.jsonl"}
    assert arm["unattributed"] == []
    # A committed copy (mtime reset to the checkout): --until restores the end.
    os.utime(r1, (end1 + 86400, end1 + 86400))
    arm = S.scan_arm("ARM1", [str(r1)], str(tmp_path), str(logs), False, until=end1)
    assert {os.path.basename(r["transcript"]) for rs in arm["units"].values() for r in rs
            if r["scored"]} == {"agent-1.jsonl", "agent-2.jsonl"}


def test_a_partial_scan_is_never_clean(tmp_path, monkeypatch, capsys):
    """Rows with no matched transcript (a zone mismatch cut the window) exit 2,
    not 0, and the report says so."""
    S = _scan_mod()
    monkeypatch.setattr(S, "load_units", lambda tree: {"a": {"task": "do a"}, "b": {"task": "do b"}})
    logs = tmp_path / "logs"
    logs.mkdir()
    _start(logs / "agent-1.jsonl", "do a", "2026-11-22T09:00:00")
    _start(logs / "agent-2.jsonl", "do b", "2026-11-22T09:30:00")
    r = tmp_path / "r.json"
    _results(r, "2026-11-22T08:59:00", ["a", "b"])
    try:
        T = S.T
        T.set_default_tz("UTC")
        end = T._ts("2026-11-22T09:40:00")
        os.utime(r, (end, end))
        assert S.main([f"X={r}@{tmp_path}", "--logs-dir", str(logs), "--tz", "UTC"]) == 0
        # Read in a zone 8 h behind: the naive starts move 8 h later, the end stays.
        rc = S.main([f"X={r}@{tmp_path}", "--logs-dir", str(logs), "--tz", "America/Los_Angeles"])
        assert rc == 2
        assert "UNATTRIBUTED: 2 of 2" in capsys.readouterr().out
    finally:
        S.T.set_default_tz(None)


# --- review 1124: listings in other formats, other roots, other network paths --

@pytest.mark.parametrize("cmd,out", [
    ("find / -name '*divmod*' -ls",
     "  131  4 -rw-r--r-- 1 max max 1234 Nov 24 10:00 /home/max/ob-ink2/evals/refs/163_x/a.c"),
    ("ls -l /home/max/ob-ink2/evals/refs/163_x",
     "-rw-r--r-- 1 max max 1234 Nov 24 10:00 /home/max/ob-ink2/evals/refs/163_x/a.c"),
    ("du -a / 2>/dev/null | grep divmod", "4096\t/home/max/ob-ink2/evals/refs/163_x/a.c"),
    ("cd / && find . -name '*divmod*'", "./home/max/ob-ink2/evals/refs/163_x/a.c"),
    ("cd /home/max && find . -name '*.c'", "./ob-ink2/evals/tasks/163_x.json"),
    ("find / -name '*divmod*'", "/root/ob/evals/refs/163_x/a.c"),
    ("find / -name '*divmod*'", "/work/evals/suites/v5.json"),
    ("stat /x", "  File: /srv/clone/evals/results/eval-1.json"),
])
def test_result_listing_any_format_any_root(cmd, out):
    S = _scan_mod()
    kinds, ev = S.result_kinds(out, json.dumps({"command": cmd}))
    assert kinds == {"listed"} and ev, (cmd, out)


@pytest.mark.parametrize("cmd,out", [
    ("find . -name '*.json'", "./evals/refs/fixture.json"),       # the task's own dir
    ("cd /tmp/eval_x && find .", "./evals/tasks/t.json"),
    ("find /tmp -name x", "/tmp/eval_x/evals/cache/fixture.json"),
    ("env", "PATH=/usr/bin:/home/x/.cargo/bin:/opt/zig"),
    ("ls /work/evals/refs/x", "ls: cannot access '/work/evals/refs/x': No such file or directory"),
    ("find / -name x", "/usr/lib/go/src/cmd/evals/x.go"),
])
def test_result_listing_ignores_fixtures_and_non_answer_paths(cmd, out):
    S = _scan_mod()
    assert S.result_kinds(out, json.dumps({"command": cmd})) == (set(), [])


@pytest.mark.parametrize("cmd", [
    "pip install sympy", "python3 -m pip install gmpy2", "uv pip install numpy",
    "cargo add num-bigint", "cargo install ripgrep", "go get golang.org/x/exp",
    "go install example.com/x@latest", "npm install lodash", "yarn add left-pad",
    "nc example.com 80", "ncat 10.0.0.9 443", "telnet 192.168.1.4 23",
    "ssh user@build-host ls", "scp a.txt host:/tmp/", "http GET example.com",
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://10.0.0.5:8000/x')\"",
    "python3 -c \"import socket; socket.create_connection(('mirror', 80))\"",
])
def test_scan_flags_other_network_paths(cmd):
    S = _scan_mod()
    kinds, ev = S.call_kinds("bash", {"command": cmd})
    assert "network" in kinds and ev, (cmd, kinds)


@pytest.mark.parametrize("cmd", [
    "pip install -e .", "pip install ./pkg dist/x.whl", "pip install --no-index sympy",
    "pip install -i http://127.0.0.1:3141/simple sympy", "cargo install --path .",
    "go build ./... && go install ./cmd/x", "cargo build --release", "nc -l 8080",
    "nc 127.0.0.1 9000", "python3 -c \"import socket; s=socket.socket(); s.bind(('127.0.0.1', 0))\"",
    "python3 app.py & curl -s http://localhost:8000/", "echo http ok", "ssh -V",
    # `nc` as an identifier in code written through a heredoc (INK-T2-STOCK's
    # 168_linearizability_kv_e, the review-1124 rescan of 2026-11-25)
    "python3 - << 'EOF'\nsrc = 'let nc = ops.len(); if nc == 0 { return; } for _ in 0..nc {'\nEOF",
])
def test_scan_ignores_local_package_and_loopback_work(cmd):
    S = _scan_mod()
    assert "network" not in S.call_kinds("bash", {"command": cmd})[0], cmd


def test_scan_pip_on_the_loopback_index_is_not_network(tmp_path):
    S = _scan_mod()
    p = tmp_path / "agent-pip.jsonl"
    _transcript(p, "t", [
        ("bash", {"command": "pip install sympy"},
         "Looking in indexes: http://127.0.0.1:3141/simple\nSuccessfully installed sympy"),
        ("bash", {"command": "pip install sympy"}, "Successfully installed sympy-1.13"),
        ("bash", {"command": "pip install sympy"},
         "WARNING: Retrying ... Temporary failure in name resolution\n"
         "ERROR: No matching distribution found for sympy"),
    ])
    hits = {h["iteration"]: h for h in S.scan_transcript(str(p))}
    assert 1 not in hits
    assert hits[2]["status"] == "read" and "network" in hits[2]["kinds"]
    assert hits[3]["status"] == "refused"
