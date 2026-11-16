#!/usr/bin/env python3
"""A lone UTF-16 surrogate in model output must not kill the run.

llama-server can emit a "\\ud800" JSON escape with no partner; json.loads
turns it into a str that UTF-8 cannot encode. It used to crash the runner
at print() and, once in the history, every later request body (httpx and
openai encode JSON as strict UTF-8), which retried every 5 s until
max_iter. The router's non-stream reply (starlette JSONResponse) had the
same defect.

Run: python3 -m pytest tests/test_runner_surrogates.py -q
"""

import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import runner  # noqa: E402

BAD = "model said \ud800 here"


class _StrictClient:
    """Encodes the payload the way httpx does: strict UTF-8."""

    def __init__(self, script):
        self.script = list(script)
        self.chat = self
        self.completions = self
        self.calls = 0

    def create(self, model, messages, tools, temperature, **kw):
        self.calls += 1
        json.dumps(messages, ensure_ascii=False).encode("utf-8")
        return self.script.pop(0)


def _resp(content, tool=None):
    tcs = None
    if tool:
        fn = type("F", (), {"name": tool[0], "arguments": tool[1]})()
        tcs = [type("T", (), {"id": "1", "function": fn})()]
    msg = type("M", (), {"content": content, "tool_calls": tcs})()
    return type("R", (), {
        "choices": [type("C", (), {"message": msg, "finish_reason": "stop"})()],
        "usage": None})()


def test_surrogate_in_model_output_neither_crashes_nor_wedges(tmp_path, monkeypatch):
    strict = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    monkeypatch.setattr(sys, "stdout", strict)
    client = _StrictClient([
        _resp(BAD),
        _resp("", ("task_done", '{"summary": "done \\ud800"}')),
    ])
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    log = tmp_path / "run.jsonl"
    runner.run_agent("task", max_iter=4, log_file=str(log),
                     system_prompt="s", workdir=str(tmp_path))
    strict.flush()
    printed = strict.buffer.getvalue().decode("utf-8")
    assert "model said ? here" in printed
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    kinds = [e["type"] for e in events]
    assert "surrogates_scrubbed" in kinds
    assert "error" not in kinds          # not counted as an API error
    assert client.calls == 2 + 1         # one scrub-retry, not a 5 s loop


def test_surrogate_in_a_plan_step_does_not_wedge_the_run(tmp_path, monkeypatch):
    # Review 2026-11-16: the plan lives in tools' ContextVar and is
    # re-injected into every request; scrubbing only `messages` left the
    # surrogate there, so every later request failed until max_iter.
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    plan_args = '{"steps":[{"step":"fix \\ud800 bug","status":"in_progress"}]}'
    client = _StrictClient(
        [_resp("", ("update_plan", plan_args))]
        + [_resp("", ("task_done", '{"summary": "ok"}'))] * 5)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    log = tmp_path / "run.jsonl"
    runner.run_agent("task", max_iter=6, log_file=str(log),
                     system_prompt="s", workdir=str(tmp_path))
    kinds = [json.loads(ln)["type"] for ln in log.read_text().splitlines()]
    assert "surrogates_scrubbed" in kinds
    assert "error" not in kinds, kinds
    assert client.calls == 3            # plan call, one scrub-retry, task_done
    assert runner.get_plan() in (None, [{"step": "fix ? bug", "status": "in_progress"}])


def test_unsurrogate_leaves_clean_text_alone():
    msgs = [{"role": "user", "content": "ok ✓"},
            {"role": "assistant", "content": "a\udfffb",
             "tool_calls": [{"function": {"arguments": "\ud83d"}}]}]
    assert runner._scrub_surrogates(msgs) == 2
    assert msgs[0]["content"] == "ok ✓"
    assert msgs[1]["content"] == "a?b"
    assert msgs[1]["tool_calls"][0]["function"]["arguments"] == "?"
    assert runner._scrub_surrogates(msgs) == 0


def test_router_reply_survives_a_surrogate_in_the_classified_task(monkeypatch):
    import asyncio
    router = pytest.importorskip("router")
    from starlette.testclient import TestClient
    from instinct.routerhook import RouterInstinct

    monkeypatch.setattr(router, "REQUIRE_IDENTITY", False)
    monkeypatch.setattr(router, "JWT_SECRET", "")
    monkeypatch.setattr(router, "_LLAMA_KEY", "")
    monkeypatch.setattr(router, "_INSTINCT", RouterInstinct("off"))
    sent = []

    async def classify(client, text):
        return True, "summarise the \ud800 logs", "."

    async def spawn(client, task, workdir, identity=None):
        json.dumps({"task": task}, ensure_ascii=False).encode("utf-8")  # as httpx
        sent.append(task)
        return "20261115-120000-abcdef12", None
    monkeypatch.setattr(router, "_classify", classify)
    monkeypatch.setattr(router, "_spawn", spawn)
    raw = json.dumps({"model": "m", "messages": [
        {"role": "user", "content": "spin up a background agent to summarise the logs"}]})
    with TestClient(router.app, base_url="http://127.0.0.1:8088",
                    raise_server_exceptions=False) as c:
        r = c.post("/v1/chat/completions", content=raw,
                   headers={"Content-Type": "application/json"})
    asyncio.run(router._INSTINCT.drain())
    assert r.status_code == 200
    assert sent == ["summarise the ? logs"]
    assert "summarise the ? logs" in r.json()["choices"][0]["message"]["content"]
