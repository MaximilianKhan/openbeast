#!/usr/bin/env python3
"""The runner loop ends a run only on an ACCEPTED task_done.

tools.task_done can refuse ("NOT DONE — ..." from the R1 path guard), and a
local model can emit task_done with arguments that are not valid JSON or not
an object. Each of those must come back to the model as a correctable tool
turn — not end the run as "complete", and not crash it.

Pure unit tests: a scripted fake client, no llama-server.
Run: python3 -m pytest tests/test_runner_task_done.py -q
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import runner  # noqa: E402


class _Client:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, model, messages, tools, temperature, **kw):
        self.calls.append([dict(m) for m in messages])
        return self.script.pop(0)


def _resp(name, raw_args, id_="1"):
    fn = type("F", (), {"name": name, "arguments": raw_args})()
    tc = type("T", (), {"id": id_, "function": fn})()
    msg = type("M", (), {"content": "", "tool_calls": [tc]})()
    return type("R", (), {
        "choices": [type("C", (), {"message": msg, "finish_reason": "tool_calls"})()],
        "usage": None})()


def _run(tmp_path, monkeypatch, script, max_iter=4):
    client = _Client(script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    log = tmp_path / "run.jsonl"
    out = runner.run_agent("task", max_iter=max_iter, log_file=str(log),
                           system_prompt="s", workdir=str(tmp_path))
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    return out, client, events


def _tool_results(call):
    return [m["content"] for m in call if m.get("role") == "tool"]


def test_refused_task_done_gives_the_model_another_turn(tmp_path, monkeypatch, capsys):
    want = tmp_path / "out" / "answer.py"
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(want)]))

    def write_then_done():
        # Second response: the model fixes it — writes the file, then finishes.
        want.parent.mkdir(parents=True, exist_ok=True)
        want.write_text("x = 1\n")
        return _resp("task_done", '{"summary": "really done"}', "2")

    class _Script(list):
        def pop(self, i=0):
            item = super().pop(i)
            return item() if callable(item) else item

    client = _Client([])
    client.script = _Script([_resp("task_done", '{"summary": "done"}', "1"),
                             write_then_done])
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    log = tmp_path / "run.jsonl"
    out = runner.run_agent("task", max_iter=4, log_file=str(log),
                           system_prompt="s", workdir=str(tmp_path))
    assert out == "really done"
    assert len(client.calls) == 2
    assert any(r.startswith("NOT DONE") for r in _tool_results(client.calls[1]))
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1 and done[0]["summary"] == "really done"
    assert "Task complete (iteration 2)" in capsys.readouterr().out


def test_refused_task_done_never_reports_complete(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENBEAST_TASK_PATHS",
                       json.dumps([str(tmp_path / "never" / "answer.py")]))
    script = [_resp("task_done", '{"summary": "done"}', str(i)) for i in range(2)]
    out, client, events = _run(tmp_path, monkeypatch, script, max_iter=2)
    assert len(client.calls) == 2
    assert not any(e["type"] == "done" for e in events)
    assert "Task complete" not in capsys.readouterr().out
    assert out == "(max iterations reached)"


def test_invalid_json_task_done_is_a_correctable_turn(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENBEAST_TASK_PATHS", raising=False)
    script = [_resp("task_done", '{"summary": "unterminated', "1"),
              _resp("task_done", '{"summary": "fixed"}', "2")]
    out, client, events = _run(tmp_path, monkeypatch, script)
    assert out == "fixed"
    assert len(client.calls) == 2
    assert any("not valid JSON" in r for r in _tool_results(client.calls[1]))
    assert [e["summary"] for e in events if e["type"] == "done"] == ["fixed"]


def test_non_object_args_do_not_crash_the_run(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENBEAST_TASK_PATHS", raising=False)
    script = [_resp("task_done", "[]", "1"),
              _resp("bash", '"ls"', "2"),
              _resp("task_done", "null", "3"),
              _resp("task_done", "{}", "4"),         # missing summary
              _resp("task_done", '{"summary": "ok"}', "5")]
    out, client, events = _run(tmp_path, monkeypatch, script, max_iter=6)
    assert out == "ok"
    assert len(client.calls) == 5
    errs = _tool_results(client.calls[4])
    assert sum(r.startswith("Error: bad tool call") for r in errs) == 4
    assert [e["summary"] for e in events if e["type"] == "done"] == ["ok"]


def test_accepted_task_done_still_ends_the_run(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENBEAST_TASK_PATHS", raising=False)
    out, client, events = _run(tmp_path, monkeypatch,
                               [_resp("task_done", '{"summary": "fin"}')])
    assert out == "fin" and len(client.calls) == 1
    assert events[-1]["type"] == "done"
