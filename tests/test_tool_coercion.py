"""Tool-argument coercion (agents/tools.py coerce_args).

Servers that do no schema coercion — TensorFold's CUDA XML parser, and vLLM
parsers that keep XML parameter values as text — deliver {"timeout": "30"}.
The runner calls TOOL_HANDLERS directly, so bash(timeout="30") used to fail
with a TypeError. coerce_args turns string values into the scalar type the
schema declares (integer / number / boolean), leaves anything unconvertible
for the tool to report, and never touches a string-typed parameter. The MCP
server and the identity tool server already coerce through their pydantic
argument models; a test pins that too.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import runner  # noqa: E402
import tools  # noqa: E402
from test_harness_agentics import _FakeClient, _Msg, _Resp, _TC  # noqa: E402


def test_string_timeout_is_coerced_and_bash_runs():
    args = tools.coerce_args("bash", {"command": "echo coerced-ok", "timeout": "30"})
    assert args == {"command": "echo coerced-ok", "timeout": 30}
    assert "coerced-ok" in tools.bash(**args)


def test_unconvertible_value_is_left_for_the_tool_to_report():
    args = tools.coerce_args("bash", {"command": "echo x", "timeout": "abc"})
    assert args["timeout"] == "abc"
    assert tools.bash(**args).startswith("Error")


def test_booleans_and_numbers():
    assert tools.coerce_args("edit_file", {"path": "p", "old_string": "a", "new_string": "b",
                                           "replace_all": "true"})["replace_all"] is True
    assert tools.coerce_args("edit_file", {"replace_all": "FALSE"})["replace_all"] is False
    assert tools.coerce_args("edit_file", {"replace_all": "yes"})["replace_all"] == "yes"
    assert tools.coerce_args("read_file", {"path": "f", "offset": " 12 ", "limit": "-1"}) == \
        {"path": "f", "offset": 12, "limit": -1}


def test_string_fields_and_non_strings_are_untouched():
    assert tools.coerce_args("bash", {"command": "30"}) == {"command": "30"}
    assert tools.coerce_args("read_file", {"path": "1"}) == {"path": "1"}
    assert tools.coerce_args("bash", {"command": "x", "timeout": 5}) == {"command": "x", "timeout": 5}
    assert tools.coerce_args("bash", {"command": "x", "extra": "7"}) == {"command": "x", "extra": "7"}
    assert tools.coerce_args("no_such_tool", {"a": "1"}) == {"a": "1"}
    assert tools.coerce_args("bash", ["not", "a", "dict"]) == ["not", "a", "dict"]


def test_runner_coerces_before_calling_the_tool(tmp_path, monkeypatch):
    seen = {}

    def fake_bash(command, timeout=120):
        seen["timeout"] = timeout
        return "ok"

    monkeypatch.setattr(runner, "TOOL_HANDLERS", dict(tools.TOOL_HANDLERS, bash=fake_bash))
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    script = [_Resp(_Msg(tool_calls=[_TC("1", "bash", {"command": "echo hi", "timeout": "30"})])),
              _Resp(_Msg(tool_calls=[_TC("2", "task_done", {"summary": "fin"})]))]
    fake = _FakeClient(n_ctx_chars=10**9, script=script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: fake)
    out = runner.run_agent("task", max_iter=4, log_file=str(tmp_path / "r.jsonl"), system_prompt="s",
                           workdir=str(tmp_path))
    assert out == "fin" and seen["timeout"] == 30
    logged = [json.loads(ln) for ln in (tmp_path / "r.jsonl").read_text().splitlines()]
    call = next(e for e in logged if e["type"] == "tool_call")
    assert call["args"]["timeout"] == "30", "the log keeps what the model actually sent"


def test_mcp_server_already_coerces_through_pydantic(monkeypatch):
    import mcp_server

    seen = {}

    def fake(command, timeout=120):
        seen["timeout"] = timeout
        return "ok"

    monkeypatch.setattr(mcp_server._tools, "bash", fake)
    asyncio.run(mcp_server.mcp.call_tool("bash", {"command": "echo hi", "timeout": "30"}))
    assert seen["timeout"] == 30
