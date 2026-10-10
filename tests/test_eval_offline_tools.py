"""Eval units get no internet tools (review 2026-10-09, evals F6).

Until suite v4.1 an eval agent had live `fetch` and `web_search`: 176
successful fetches in the agent logs, and `web_search` working or not
depending on whether the stack happened to be up. Under OPENBEAST_EVAL the
runner now neither offers nor executes them and does not mention them.

Everything else must stay put: the registry in tools.py, the runner's
non-eval surface and prompt, and the 18-tool MCP surface.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

import runner  # noqa: E402
import tools   # noqa: E402

ONLINE = {"fetch", "web_search"}
ALL = ["bash", "read_file", "write_file", "list_files", "grep", "edit_file",
       "fetch", "web_search", "update_plan", "task_done"]


class _TC:
    def __init__(self, id_, name, args):
        self.id = id_
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()


class _Fake:
    def __init__(self, script):
        self.script = list(script)
        self.kwargs = []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.kwargs.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        calls = self.script.pop(0)
        msg = type("M", (), {"content": "", "tool_calls": [
            _TC(f"c{i}", n, a) for i, (n, a) in enumerate(calls)]})()
        return type("R", (), {"usage": None, "choices": [
            type("C", (), {"message": msg, "finish_reason": "tool_calls"})()]})()


def _run(tmp_path, monkeypatch, script, eval_mode, system_prompt=None):
    for k in ("OPENBEAST_EVAL", "OPENBEAST_EVAL_WALL_S", "OPENBEAST_TASK_PATHS",
              "OPENBEAST_AGENT_MAX_TOKENS", "OPENBEAST_REASONING_BUDGET", "REASONING_BUDGET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(runner, "_CONF_PATH", tmp_path / "openbeast.conf", raising=False)
    if eval_mode:
        monkeypatch.setenv("OPENBEAST_EVAL", "1")
    called = []

    def spy(name):
        def handler(**kw):
            called.append(name)
            return f"{name} ran"
        return handler

    monkeypatch.setattr(runner, "TOOL_HANDLERS",
                        dict(tools.TOOL_HANDLERS, fetch=spy("fetch"), web_search=spy("web_search")))
    fake = _Fake(script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: fake)
    out = runner.run_agent("task", max_iter=5, log_file=str(tmp_path / "r.jsonl"),
                           system_prompt=system_prompt, workdir=str(tmp_path))
    return out, fake, called


_SCRIPT = [[("fetch", {"url": "https://en.wikipedia.org/wiki/Fast_Fourier_transform"}),
            ("web_search", {"query": "zig 0.16 stdout writer"})],
           [("task_done", {"summary": "fin"})]]


def _offered(fake):
    return [s["function"]["name"] for s in fake.kwargs[0]["tools"]]


def test_eval_unit_is_not_offered_and_cannot_call_the_internet_tools(tmp_path, monkeypatch):
    out, fake, called = _run(tmp_path, monkeypatch, _SCRIPT, eval_mode=True)
    assert out == "fin"
    assert _offered(fake) == [n for n in ALL if n not in ONLINE]
    assert called == [], "a hallucinated call must not reach the handler either"
    results = [m["content"] for m in fake.kwargs[1]["messages"] if m["role"] == "tool"]
    assert len(results) == 2
    for r in results:
        assert r.startswith("Error: unknown tool")
        assert "fetch" not in r.split("Available tools:")[1]
        assert "web_search" not in r.split("Available tools:")[1]


def test_outside_eval_both_tools_are_offered_and_run(tmp_path, monkeypatch):
    """Control: the same script, no eval marker."""
    out, fake, called = _run(tmp_path, monkeypatch, _SCRIPT, eval_mode=False)
    assert out == "fin"
    assert _offered(fake) == ALL
    assert called == ["fetch", "web_search"]


def test_eval_system_prompt_does_not_mention_tools_it_does_not_have(tmp_path, monkeypatch):
    _, fake, _ = _run(tmp_path, monkeypatch, [[("task_done", {"summary": "fin"})]], eval_mode=True)
    system = fake.kwargs[0]["messages"][0]["content"]
    assert "web_search" not in system and "fetch" not in system
    # ...and it is otherwise the same instructions.
    for kept in ("bash         —", "update_plan  —", "Call task_done with a summary"):
        assert kept in system
    _, fake, _ = _run(tmp_path, monkeypatch, [[("task_done", {"summary": "fin"})]], eval_mode=False)
    system = fake.kwargs[0]["messages"][0]["content"]
    assert "web_search   —" in system and "fetch        —" in system


def test_every_online_mention_is_removed_exactly_once():
    """If someone rewords the instructions, the eval prompt must not be left
    advertising a tool the unit cannot call."""
    for online, _ in runner._ONLINE_INSTRUCTIONS:
        assert runner._AGENT_INSTRUCTIONS.count(online) == 1, online
    offline = runner._agent_instructions(offline=True)
    assert "fetch" not in offline and "web_search" not in offline
    assert runner._agent_instructions(offline=False) is runner._AGENT_INSTRUCTIONS


def test_the_registry_itself_is_unchanged(monkeypatch):
    names = [s["function"]["name"] for s in tools.TOOL_SCHEMAS]
    assert names == ALL
    assert set(tools.TOOL_HANDLERS) == set(ALL)
    # Even with the marker set, tools.py does not filter: other importers
    # (mcp_server, run_eval's parent process) see the whole registry.
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    assert runner._tool_surface(offline=False) == (tools.TOOL_SCHEMAS, runner.TOOL_HANDLERS)
    schemas, handlers = runner._tool_surface(offline=True)
    assert len(schemas) == 8 and not (ONLINE & set(handlers))


def test_the_mcp_surface_still_has_fetch_and_web_search():
    """The 18-tool count is pinned by tests/test_mcp_allowlist.py; this
    checks the two names from source so it needs no MCP runtime."""
    src = (ROOT / "agents" / "mcp_server.py").read_text()
    for name in ONLINE:
        assert f"def {name}(" in src, name
