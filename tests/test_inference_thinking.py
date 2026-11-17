#!/usr/bin/env python3
"""INFERENCE_THINKING — the serving host's thinking default.

beast-gate (agents/edge.py) sets chat_template_kwargs.enable_thinking on a
chat request that names no thinking control; anything the client said wins;
a request carrying X-OpenBeast-Gate-Defaults: off (every eval request the
runner sends) is forwarded exactly as sent. The runner's own (non-eval)
agents follow the key; eval runs never do.

Run: pytest tests/test_inference_thinking.py
"""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest
from starlette.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_edge import DEVICE_KEY, _local_headers, _registry, _stub_upstream  # noqa: E402

AUTH = {"Authorization": f"Bearer {DEVICE_KEY}"}
CHAT = {"model": "glm", "messages": [{"role": "user", "content": "hi"}]}


def _gate(tmp_path, monkeypatch, thinking):
    monkeypatch.setenv("OPENBEAST_REPO_DIR", str(tmp_path))
    monkeypatch.delenv("OPENBEAST_EDGE_ALLOW_ANON", raising=False)
    monkeypatch.delenv("OPENBEAST_API_KEY", raising=False)
    monkeypatch.setenv("OPENBEAST_EDGE_MAX_INFLIGHT", "2")
    if thinking is None:
        monkeypatch.delenv("OPENBEAST_INFERENCE_THINKING", raising=False)
    else:
        monkeypatch.setenv("OPENBEAST_INFERENCE_THINKING", thinking)
    import edge
    importlib.reload(edge)
    _registry(tmp_path)
    return edge


def _forward(edge, body, path="/v1/chat/completions", headers=None):
    captured = {}
    _stub_upstream(edge, captured)
    with TestClient(edge.app) as c:
        r = c.post(path, content=json.dumps(body).encode(),
                   headers={**AUTH, "Content-Type": "application/json", **(headers or {})})
    assert r.status_code == 200, r.text
    return json.loads(captured["content"]), captured["headers"]


# --- the pure rule ---------------------------------------------------------

@pytest.mark.parametrize("default,flag", [("on", True), ("off", False)])
def test_absent_gets_the_default(default, flag):
    import edge
    body = {"messages": []}
    assert edge._apply_thinking_default(body, default) is True
    assert body["chat_template_kwargs"] == {"enable_thinking": flag}


@pytest.mark.parametrize("default", [None, "", "maybe"])
def test_no_default_touches_nothing(default):
    import edge
    body = {"messages": []}
    assert edge._apply_thinking_default(body, default) is False
    assert body == {"messages": []}


@pytest.mark.parametrize("body", [
    {"chat_template_kwargs": {"enable_thinking": False}},
    {"chat_template_kwargs": {"enable_thinking": True}},
    {"chat_template_kwargs": {"thinking": False}},
    {"enable_thinking": False},
    {"thinking": {"type": "disabled"}},
    {"reasoning": {"enabled": False}},
    {"reasoning_effort": "low"},
    {"reasoning_budget_tokens": 0},
    {"thinking_budget": 128},
    {"chat_template_kwargs": "not an object"},
])
def test_an_explicit_choice_wins(body):
    import edge
    before = json.loads(json.dumps(body))
    assert edge._apply_thinking_default(body, "on") is False
    assert body == before


def test_other_template_kwargs_are_kept():
    import edge
    body = {"chat_template_kwargs": {"foo": 1}}
    assert edge._apply_thinking_default(body, "on") is True
    assert body["chat_template_kwargs"] == {"foo": 1, "enable_thinking": True}


def test_null_template_kwargs_reads_as_absent():
    import edge
    body = {"chat_template_kwargs": None}
    assert edge._apply_thinking_default(body, "on") is True
    assert body["chat_template_kwargs"] == {"enable_thinking": True}


@pytest.mark.parametrize("raw,want", [("on", "on"), ("ON", "on"), (" off ", "off"),
                                      ("on  # spark-1", "on"), ("", None),
                                      ("true", None), ("yes", None)])
def test_env_parse(raw, want):
    import edge
    assert edge.thinking_default({"OPENBEAST_INFERENCE_THINKING": raw}) == want


# --- through the gate ------------------------------------------------------

def test_gate_injects_when_on(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "on")
    sent, _ = _forward(edge, CHAT)
    assert sent["chat_template_kwargs"] == {"enable_thinking": True}
    assert sent["messages"] == CHAT["messages"]


def test_gate_leaves_explicit_false_alone(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "on")
    body = {**CHAT, "chat_template_kwargs": {"enable_thinking": False}}
    sent, _ = _forward(edge, body)
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}


def test_gate_unset_forwards_the_body_unchanged(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, None)
    sent, _ = _forward(edge, CHAT)
    assert sent == CHAT


def test_eval_opt_out_header_is_honoured_and_not_forwarded(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "on")
    sent, headers = _forward(edge, CHAT, headers={"X-OpenBeast-Gate-Defaults": "off"})
    assert sent == CHAT                     # exactly as the eval runner sent it
    assert not any(k.lower() == "x-openbeast-gate-defaults" for k in headers)


def test_completions_endpoint_is_never_touched(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "on")
    body = {"model": "glm", "prompt": "hi"}
    sent, _ = _forward(edge, body, path="/v1/completions")
    assert sent == body


def test_local_health_reports_the_default(tmp_path, monkeypatch):
    edge = _gate(tmp_path, monkeypatch, "on")
    _stub_upstream(edge, {})
    with TestClient(edge.app) as c:
        local = c.get("/gate/health", headers=_local_headers(edge)).json()
        remote = c.get("/gate/health").json()
    assert local["thinking_default"] == "on"
    assert "thinking_default" not in remote


# --- the runner ------------------------------------------------------------

import runner  # noqa: E402
import tools  # noqa: E402
from test_runner_v2 import _Client, _resp  # noqa: E402


class _CapturingClient(_Client):
    def __init__(self, script):
        super().__init__(script)
        self.kwargs = []
        self.ctor = {}

    def create(self, model, messages, tools, temperature, **kw):
        self.kwargs.append(kw)
        return super().create(model, messages, tools, temperature, **kw)


@pytest.fixture
def run(tmp_path, monkeypatch):
    tools.reset_plan()
    monkeypatch.setattr(runner, "_CONF_PATH", tmp_path / "openbeast.conf")
    for k in ("OPENBEAST_TASK_PATHS", "OPENBEAST_AGENT_THINKING", "OPENBEAST_EVAL",
              "OPENBEAST_INFERENCE_THINKING", "OPENBEAST_AGENT_MAX_TOKENS",
              "OPENBEAST_REASONING_BUDGET", "REASONING_BUDGET"):
        monkeypatch.delenv(k, raising=False)

    def _go():
        client = _CapturingClient([_resp("task_done", {"summary": "ok"})])

        def _ctor(**kw):
            client.ctor = kw
            return client
        monkeypatch.setattr(runner, "OpenAI", _ctor)
        monkeypatch.setattr(runner.time, "sleep", lambda s: None)
        runner.run_agent("task", max_iter=2, log_file=str(tmp_path / "r.jsonl"),
                         system_prompt="s", workdir=str(tmp_path))
        return client
    yield _go
    tools.reset_plan()


def _think(client):
    return [kw.get("extra_body", {}).get("chat_template_kwargs", {}).get("enable_thinking")
            for kw in client.kwargs]


def test_eval_request_carries_the_opt_out_and_no_thinking_param(run, monkeypatch):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_INFERENCE_THINKING", "on")   # must be ignored
    client = run()
    assert client.ctor["default_headers"] == {"X-OpenBeast-Gate-Defaults": "off"}
    assert _think(client) == [None]          # body as before: the server's default


def test_eval_ignores_the_conf_key_too(run, monkeypatch, tmp_path):
    (tmp_path / "openbeast.conf").write_text("INFERENCE_THINKING=on\n")
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    assert _think(run()) == [None]


@pytest.mark.parametrize("value,flag", [("on", True), ("off", False)])
def test_eval_explicit_switch_still_sent(run, monkeypatch, value, flag):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", value)
    assert _think(run()) == [flag]


def test_non_eval_follows_the_host_default(run, monkeypatch, tmp_path):
    (tmp_path / "openbeast.conf").write_text("INFERENCE_THINKING=on\n")
    client = run()
    assert _think(client) == [True]
    assert "default_headers" not in client.ctor


def test_non_eval_env_beats_conf_and_agent_switch_beats_both(run, monkeypatch, tmp_path):
    (tmp_path / "openbeast.conf").write_text("INFERENCE_THINKING=on\n")
    monkeypatch.setenv("OPENBEAST_INFERENCE_THINKING", "off")
    assert _think(run()) == [False]
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "on")
    assert _think(run()) == [True]


def test_non_eval_unset_sends_nothing(run):
    assert _think(run()) == [None]


def test_agent_max_tokens_conf_caps_non_eval_only(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_CONF_PATH", tmp_path / "openbeast.conf")
    for k in ("OPENBEAST_AGENT_MAX_TOKENS", "OPENBEAST_EVAL", "OPENBEAST_AGENT_THINKING",
              "OPENBEAST_REASONING_BUDGET", "REASONING_BUDGET"):
        monkeypatch.delenv(k, raising=False)
    assert runner._max_completion_tokens() == 20480 + 12288
    (tmp_path / "openbeast.conf").write_text("AGENT_MAX_TOKENS=16384  # arm C\n")
    assert runner._max_completion_tokens() == 16384
    monkeypatch.setenv("OPENBEAST_AGENT_MAX_TOKENS", "9000")
    assert runner._max_completion_tokens() == 9000
    monkeypatch.delenv("OPENBEAST_AGENT_MAX_TOKENS")
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    assert runner._max_completion_tokens() == 8192     # eval caps are fixed
