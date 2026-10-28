"""Tool fixes from the 2026-10-27 review (agents/tools.py, eval-era file).

R1-02  list_files never follows a directory symlink and is bounded.
R1-03  edit_file refuses files over read_file's size cap.
R1-06  the bash tool kills everything the command backgrounded, redirected
       or nohup'd jobs included, as its description says.
R1-08  fetch returns `Error:` on a malformed URL or max_length, never raises.
R6-11  under eval, the file tools refuse OPENBEAST_EVAL_DENY_ROOTS.
"""

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

import tools  # noqa: E402


# --------------------------------------------------------------------------
# R1-02
# --------------------------------------------------------------------------

def test_list_files_survives_a_symlink_cycle(tmp_path):
    cyc = tmp_path / "cyc"
    cyc.mkdir()
    (cyc / "f.txt").write_text("x")
    os.symlink(".", cyc / "a")
    os.symlink(".", cyc / "b")
    t = time.monotonic()
    out = tools.list_files(str(cyc))
    assert time.monotonic() - t < 5
    assert out.strip() == str(cyc / "f.txt")


def test_list_files_lists_symlinked_files_not_symlinked_dirs(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "r.py").write_text("")
    (tmp_path / "top.py").write_text("")
    os.symlink(tmp_path / "top.py", tmp_path / "link.py")
    os.symlink(tmp_path / "real", tmp_path / "dirlink")
    out = tools.list_files(str(tmp_path), "**/*.py").splitlines()
    assert out == sorted([str(tmp_path / "link.py"), str(tmp_path / "real" / "r.py"),
                          str(tmp_path / "top.py")])


@pytest.mark.parametrize("pattern", ["**/*", "*.py", "**/*.py", "a/*.py", "a/**/*.py",
                                     "*/*.py", ".*", ".hid/*", "**/.*", "[xa]*",
                                     "x.py", "../t/*.py", "nomatch*"])
def test_list_files_matches_glob_semantics(tmp_path, pattern):
    t = tmp_path / "t"
    for d in ("a/b", ".hid", "c"):
        (t / d).mkdir(parents=True, exist_ok=True)
    for f in ("x.py", "a/y.py", "a/b/z.py", ".dot.py", ".hid/h.py", "c/w.txt", "a/.q.py"):
        (t / f).write_text("")
    want = sorted(os.path.normpath(f) for f in glob.glob(str(t / pattern), recursive=True)
                  if os.path.isfile(f))
    got = tools.list_files(str(t), pattern)
    got = [] if got.startswith("No files") else got.splitlines()
    assert got == want


def test_list_files_cap_is_reported(tmp_path, monkeypatch):
    for i in range(30):
        (tmp_path / f"f{i:02}.txt").write_text("")
    monkeypatch.setattr(tools, "_LIST_SHOW", 5)
    monkeypatch.setattr(tools, "_LIST_MAX_ENTRIES", 10)
    out = tools.list_files(str(tmp_path))
    assert "[listing incomplete:" in out
    # Negative control: no note when nothing was cut.
    monkeypatch.setattr(tools, "_LIST_MAX_ENTRIES", 10_000)
    assert "[listing incomplete:" not in tools.list_files(str(tmp_path))


# --------------------------------------------------------------------------
# R1-03
# --------------------------------------------------------------------------

def test_edit_file_refuses_files_over_the_read_cap(tmp_path, monkeypatch):
    big = tmp_path / "big.bin"
    big.write_bytes(b"a" * 4096)
    monkeypatch.setattr(tools, "_MAX_READ_BYTES", 1024)
    out = tools.edit_file(str(big), "a", "b")
    assert out.startswith("Error: file too large")
    assert big.read_bytes() == b"a" * 4096
    small = tmp_path / "small.txt"
    small.write_text("hello")
    assert not tools.edit_file(str(small), "hello", "bye").startswith("Error")
    assert small.read_text() == "bye"


# --------------------------------------------------------------------------
# R1-06
# --------------------------------------------------------------------------

def _alive(pid: int) -> bool:
    # Not pgrep: conftest stubs it to "nothing found".
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _wait_dead(pid: int, secs: float = 3.0) -> bool:
    deadline = time.monotonic() + secs
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


@pytest.mark.parametrize("template", [
    "sleep 300 >/dev/null 2>&1 & echo $! > {pf}; echo started",
    "nohup sleep 300 >/dev/null 2>&1 & echo $! > {pf}; echo started",
    "sleep 300 & echo $! > {pf}; echo started",
])
def test_bash_kills_what_it_backgrounded(tmp_path, template):
    pf = tmp_path / "pid"
    t = time.monotonic()
    out = tools.bash(template.format(pf=pf), 20)
    took = time.monotonic() - t
    pid = int(pf.read_text())
    try:
        assert "started" in out
        assert took < 4, f"waited {took:.1f}s for the background job"
        assert _wait_dead(pid), "a backgrounded job outlived the bash call"
    finally:
        if _alive(pid):
            os.kill(pid, 9)


def test_run_reaped_default_keeps_old_contract(tmp_path):
    # Negative control. Only the bash tool reaps the group; other callers
    # (setup, validators) keep the old behaviour, so a fixture can still
    # start a detached job.
    pf = tmp_path / "pid"
    rc, _ = tools.run_reaped(f"sleep 300 >/dev/null 2>&1 & echo $! > {pf}", 10)
    pid = int(pf.read_text())
    try:
        assert rc == 0 and _alive(pid)
    finally:
        os.kill(pid, 9)


# --------------------------------------------------------------------------
# R1-08
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://example.com:99999/", "http://[::1/",
                                 "http://a:b/"])
def test_fetch_malformed_url_is_an_error_string(url):
    out = tools.fetch(url)
    assert out.startswith("Error: invalid URL")


@pytest.mark.parametrize("bad", ["abc", None, float("inf")])
def test_fetch_bad_max_length_is_an_error_string(bad, monkeypatch):
    # The URL check passes without DNS for a literal public IP.
    monkeypatch.setattr(tools, "_fetch_body",
                        lambda *a, **k: pytest.fail("must not fetch"))
    out = tools.fetch("http://93.184.215.14/", max_length=bad)
    assert out.startswith("Error: max_length")


# --------------------------------------------------------------------------
# R6-11
# --------------------------------------------------------------------------

@pytest.fixture
def denied(tmp_path, monkeypatch):
    logs = tmp_path / "repo" / "agents" / "logs"
    logs.mkdir(parents=True)
    (logs / "agent-1.jsonl").write_text('{"args": "def rkf45(f): pass"}\n')
    open_dir = tmp_path / "repo" / "agents"
    (open_dir / "ok.py").write_text("def rkf45(f): pass\n")
    monkeypatch.setenv("OPENBEAST_EVAL_DENY_ROOTS", json.dumps([str(logs)]))
    return tmp_path / "repo", logs


def test_eval_file_tools_refuse_denied_roots(denied, monkeypatch):
    repo, logs = denied
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    f = logs / "agent-1.jsonl"
    assert tools.read_file(str(f)).startswith("Error:") and "off limits" in tools.read_file(str(f))
    assert "off limits" in tools.list_files(str(logs))
    assert "off limits" in tools.grep("rkf45", str(logs))
    assert "off limits" in tools.edit_file(str(f), "pass", "x")
    assert "off limits" in tools.write_file(str(logs / "new.py"), "x")
    # From a parent: the denied subtree is invisible, the rest is not.
    listed = tools.list_files(str(repo))
    assert str(f) not in listed and str(repo / "agents" / "ok.py") in listed
    hits = tools.grep("rkf45", str(repo))
    assert str(f) not in hits and "ok.py" in hits
    # Through a symlink: realpath'd before the check.
    link = repo / "lnk"
    os.symlink(logs, link)
    assert "off limits" in tools.read_file(str(link / "agent-1.jsonl"))


def test_deny_roots_inert_outside_eval(denied, monkeypatch):
    repo, logs = denied
    monkeypatch.delenv("OPENBEAST_EVAL", raising=False)
    assert "def rkf45" in tools.read_file(str(logs / "agent-1.jsonl"))
    assert "agent-1.jsonl" in tools.grep("rkf45", str(repo))
