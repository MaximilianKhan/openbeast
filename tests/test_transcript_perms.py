"""Agent transcripts are private: files 0600, directories we create 0700.

Review secrets-crypto-6 / chat-sessions-agent-transcripts-world-readable
(round 2): runner.log_event and mcp_server.start_agent appended with a plain
open() under the umask, so every agent transcript — full tool output — was
0644 in a 0755 agents/logs/. Every test pins umask 022 (the common default
that produced 0644) so the result never depends on the invoking shell.

Run: python3 -m pytest tests/test_transcript_perms.py -q
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

import mcp_server  # noqa: E402
import runner  # noqa: E402
import tools  # noqa: E402


def _mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture(autouse=True)
def _umask_022():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


class _Resp:
    def __init__(self):
        tc = type("TC", (), {
            "id": "1",
            "function": type("F", (), {"name": "task_done",
                                        "arguments": json.dumps({"summary": "ok"})})(),
        })()
        msg = type("M", (), {"content": "", "tool_calls": [tc]})()
        self.choices = [type("C", (), {"message": msg, "finish_reason": "tool_calls"})()]
        self.usage = None


class _Client:
    def __init__(self):
        self.chat = self
        self.completions = self

    def create(self, **kw):
        return _Resp()


@pytest.fixture()
def fake_model(monkeypatch):
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: _Client())


def test_runner_log_file_created_private(tmp_path, fake_model):
    log = tmp_path / "new" / "logs" / "run.jsonl"
    assert runner.run_agent("t", max_iter=2, log_file=str(log),
                            system_prompt="s", workdir=str(tmp_path)) == "ok"
    assert _mode(log) == 0o600
    assert _mode(log.parent) == 0o700            # created by us (the leaf;
    # os.makedirs gives intermediate parents the default mode)
    assert any(json.loads(ln)["type"] == "done" for ln in log.read_text().splitlines())


def test_runner_tightens_preexisting_world_readable_transcript(tmp_path, fake_model):
    log = tmp_path / "run.jsonl"
    log.write_text('{"type": "spawn"}\n')
    os.chmod(log, 0o644)
    runner.run_agent("t", max_iter=2, log_file=str(log), system_prompt="s",
                     workdir=str(tmp_path))
    assert _mode(log) == 0o600
    assert log.read_text().startswith('{"type": "spawn"}\n')   # appended, not truncated


def test_runner_caller_log_dir_is_not_chmodded(tmp_path, fake_model):
    # Negative control: a caller's existing --log-dir keeps its mode; only
    # the transcript inside it is private.
    d = tmp_path / "mine"
    d.mkdir()
    os.chmod(d, 0o755)
    runner.run_agent("t", max_iter=2, log_dir=str(d), system_prompt="s",
                     workdir=str(tmp_path))
    assert _mode(d) == 0o755
    (log,) = list(d.glob("agent-*.jsonl"))
    assert _mode(log) == 0o600


def test_runner_default_log_dir_is_tightened(tmp_path, fake_model, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    os.chmod(d, 0o755)
    monkeypatch.setattr(runner, "DEFAULT_LOG_DIR", str(d))
    runner.run_agent("t", max_iter=2, log_dir=str(d), system_prompt="s",
                     workdir=str(tmp_path))
    assert _mode(d) == 0o700


def test_mcp_start_agent_precreates_private_transcript(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    os.chmod(logs, 0o755)
    monkeypatch.setattr(mcp_server, "_LOG_DIR", str(logs))
    # Never make the pytest process itself non-dumpable, never spawn.
    monkeypatch.setattr(mcp_server._tools, "harden_process", lambda: False)

    def no_spawn(*a, **kw):
        raise OSError("stub: no spawn")
    monkeypatch.setattr(mcp_server.subprocess, "Popen", no_spawn)
    out = mcp_server.start_agent("noop", workdir=str(tmp_path))
    assert "Error starting agent" in out
    (log,) = list(logs.glob("agent-*.jsonl"))
    assert _mode(log) == 0o600
    assert _mode(logs) == 0o700
    assert json.loads(log.read_text().splitlines()[0])["type"] == "spawn"


def test_open_private_append_creates_private_and_appends(tmp_path):
    # Helper contract: creates 0600, tightens ours, appends.
    p = tmp_path / "x.jsonl"
    with tools.open_private_append(str(p)) as f:
        f.write("a\n")
    with tools.open_private_append(str(p)) as f:
        f.write("b\n")
    assert p.read_text() == "a\nb\n"
    assert _mode(p) == 0o600
