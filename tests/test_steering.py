#!/usr/bin/env python3
"""Runner steering + session ledger (beast-chat Phase 1).

Pure unit tests: no llama-server, no GPU, no network. The OpenAI client is
faked at `runner.OpenAI`, so the *whole* agent loop runs — turn boundaries,
transcript events, message list and return value are all the real code paths.

The load-bearing test in this file is the EVAL GUARD. `evals/run_eval.py`
spawns this runner with OPENBEAST_TASK_PATHS in the child env; an eval unit is
half of a paired A/B row. If a steering op could reach one, a ten-hour
measurement would be silently corrupted with nothing in the cache-key era to
show it. So: under eval mode, a PRE-PLANTED inbox is not read, not created,
not even stat()ed, and no ledger record appears.

Run: python3 -m pytest tests/test_steering.py -q
"""

import json
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import runner  # noqa: E402
import sessions  # noqa: E402
import tools  # noqa: E402


# ---------------------------------------------------------------------------
# Fake inference client — the repo's "fake the external, run the real code"
# style. Each scripted turn is (content, tool_calls, finish_reason, hook).
# ---------------------------------------------------------------------------

class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, idx, name, args):
        self.id = f"call_{idx}"
        self.type = "function"
        self.function = _Fn(name, json.dumps(args))


class _Message:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, message, finish_reason):
        self.choices = [_Choice(message, finish_reason)]
        self.usage = None


class FakeClient:
    """Replays a script of turns and records every request it was sent."""

    def __init__(self, script):
        self._script = list(script)
        self.requests = []           # list[list[dict]] — messages per call
        self.chat = self
        self.completions = self

    def create(self, *, model, messages, tools=None, temperature=None, **kw):
        self.requests.append([dict(m) for m in messages])
        if not self._script:
            # Script exhausted: end the run rather than loop to max_iter.
            turn = ("wrapping up", [("task_done", {"summary": "script end"})], None)
        else:
            turn = self._script.pop(0)
        content, calls, hook = turn
        if hook:
            hook()
        tool_calls = ([_ToolCall(i, n, a) for i, (n, a) in enumerate(calls)]
                      if calls else None)
        return _Response(_Message(content, tool_calls),
                         "tool_calls" if tool_calls else "stop")


def _done(summary="finished"):
    return (None, [("task_done", {"summary": summary})], None)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Throwaway ledger + workdir; plan state reset; steering env cleared."""
    monkeypatch.setattr(sessions, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(runner, "_PAUSE_POLL_S", 0.02)
    # setenv first so monkeypatch records the pre-test value — run_agent
    # writes os.environ["AGENT_WORKDIR"] directly and would otherwise leak.
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    monkeypatch.delenv("OPENBEAST_TASK_PATHS", raising=False)
    monkeypatch.delenv("OPENBEAST_BEAST_CHAT", raising=False)
    monkeypatch.delenv("OPENBEAST_EVAL_GREEDY", raising=False)
    tools.reset_plan()
    yield
    tools.reset_plan()


def _run(tmp_path, script, *, session_id=None, steer=False, log_name="run.jsonl",
         task="do the thing", **kw):
    client = FakeClient(script)
    log_path = str(tmp_path / log_name)
    orig = runner.OpenAI
    runner.OpenAI = lambda **_: client
    try:
        result = runner.run_agent(
            task=task, log_file=log_path, workdir=str(tmp_path),
            max_iter=kw.pop("max_iter", 6), session_id=session_id, steer=steer,
            **kw)
    finally:
        runner.OpenAI = orig
    return result, client, _events(log_path)


def _events(log_path):
    out = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _plant(session_id, ops):
    for op in ops:
        sessions.append_op(session_id, op)
    return sessions.inbox_path(session_id)


def _operator_lines(requests):
    """Operator messages in a request (a list of messages) or in all of them."""
    if requests and isinstance(requests[0], dict):
        requests = [requests]
    return [m["content"] for req in requests for m in req
            if m.get("role") == "user"
            and str(m.get("content", "")).startswith(runner._STEER_PREFIX)]


# ===========================================================================
# THE EVAL GUARD
# ===========================================================================

def test_eval_guard_returns_false_before_anything_else(monkeypatch):
    """Eval mode beats every other signal, including an explicit --steer."""
    monkeypatch.setenv("OPENBEAST_BEAST_CHAT", "true")
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", '["/tmp/eval-x/out.zig"]')
    assert runner._steering_enabled() is False
    assert runner._steering_enabled(explicit=True) is False

    monkeypatch.delenv("OPENBEAST_TASK_PATHS")
    assert runner._steering_enabled() is True
    assert runner._steering_enabled(explicit=True) is True


def test_eval_mode_ignores_a_pre_planted_inbox_entirely(tmp_path, monkeypatch):
    sid = "20260914-120000-deadbeef"
    inbox = _plant(sid, [{"op": "say", "text": "INJECTED", "from": "attacker"},
                         {"op": "stop"}])
    before = (os.path.getsize(inbox), os.stat(inbox).st_mtime_ns,
              open(inbox, "rb").read())

    # An eval task spec with no /tmp/eval paths still sets the var to "[]",
    # which is truthy as a string — the guard trips, task_done stays usable.
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", "[]")
    monkeypatch.setenv("OPENBEAST_BEAST_CHAT", "true")

    result, client, events = _run(tmp_path, [_done("eval row complete")],
                                  session_id=sid, steer=True)

    # The measurement ran exactly as it would have without beast-chat.
    assert result == "eval row complete"
    assert [e["type"] for e in events] == ["start", "iteration", "tool_call", "done"]
    assert not [e for e in events if e["type"] in ("steer", "paused")]
    assert _operator_lines(client.requests) == []

    # The inbox was not read, not consumed, not rewritten.
    assert (os.path.getsize(inbox), os.stat(inbox).st_mtime_ns,
            open(inbox, "rb").read()) == before
    # And no ledger record was created for the eval unit.
    assert sessions.get(sid) is None
    assert sessions.list_sessions() == []


def test_inert_by_default_without_beast_chat(tmp_path):
    """No conf, no flags: a planted inbox is ignored and no ledger appears."""
    sid = "20260914-120001-cafef00d"
    _plant(sid, [{"op": "say", "text": "INJECTED"}])

    _, client, steered = _run(tmp_path, [_done("ok")], log_name="a.jsonl")
    assert runner._steering_enabled() is False
    assert [e["type"] for e in steered] == ["start", "iteration", "tool_call", "done"]
    assert _operator_lines(client.requests) == []
    assert sessions.list_sessions() == []


def test_event_stream_is_identical_with_steering_on_but_no_ops(tmp_path):
    """The feature adds nothing to the transcript until an op arrives."""
    _, _, plain = _run(tmp_path, [_done("ok")], log_name="plain.jsonl")
    _, _, armed = _run(tmp_path, [_done("ok")], log_name="armed.jsonl",
                       session_id="20260914-120002-11111111")
    strip = lambda evs: [{k: v for k, v in e.items() if k != "timestamp"}
                         for e in evs]
    assert strip(plain) == strip(armed)


# ===========================================================================
# say
# ===========================================================================

def test_say_becomes_operator_message_and_steer_event(tmp_path):
    sid = "20260914-120010-aaaaaaaa"
    _plant(sid, [{"op": "say", "text": "use edit_file, not write_file",
                  "from": "maxjkh@gmail.com"}])

    result, client, events = _run(tmp_path, [_done("did as told")], session_id=sid)

    steers = [e for e in events if e["type"] == "steer"]
    assert len(steers) == 1
    assert steers[0]["op"] == "say"
    assert steers[0]["text"] == "use edit_file, not write_file"
    assert steers[0]["from"] == "maxjkh@gmail.com"
    # The steer event precedes the turn it steered.
    assert [e["type"] for e in events][:3] == ["start", "steer", "iteration"]

    # Stored in history (unlike the transient plan block), so the model
    # still sees it on every later turn.
    assert _operator_lines(client.requests) == [
        "[operator message] use edit_file, not write_file"]
    assert result == "did as told"


def test_say_sent_mid_turn_lands_at_the_next_turn_boundary(tmp_path):
    """Claude Code semantics: a message during a long tool call waits."""
    sid = "20260914-120011-bbbbbbbb"
    sessions.register(sid)          # so the inbox path exists conceptually

    script = [
        ("thinking", [("bash", {"command": "true"})],
         lambda: _plant(sid, [{"op": "say", "text": "stop guessing, run it"}])),
        _done("ran it"),
    ]
    _, client, events = _run(tmp_path, script, session_id=sid)

    # Turn 1's request predates the op; turn 2's carries it.
    assert _operator_lines(client.requests[0]) == []
    assert _operator_lines(client.requests[1]) == [
        "[operator message] stop guessing, run it"]

    types = [e["type"] for e in events]
    assert types.index("steer") > types.index("tool_call"), \
        "the op must land after the tool call it was sent during"


def test_say_is_remembered_across_later_turns(tmp_path):
    sid = "20260914-120012-cccccccc"
    _plant(sid, [{"op": "say", "text": "remember me"}])
    script = [("one", [("bash", {"command": "true"})], None),
              ("two", [("bash", {"command": "true"})], None),
              _done("ok")]
    _, client, _ = _run(tmp_path, script, session_id=sid)
    assert all(_operator_lines(req) == ["[operator message] remember me"]
               for req in client.requests)


def test_empty_and_unknown_ops_are_ignored_but_logged(tmp_path):
    sid = "20260914-120013-dddddddd"
    _plant(sid, [{"op": "say", "text": "   "},
                 {"op": "self_destruct"},
                 {"op": "SAY", "text": "case insensitive"},
                 {}])
    # A non-object line can only arrive from a foreign writer (append_op
    # refuses it); read_new_ops drops it before the runner ever sees it.
    with open(sessions.inbox_path(sid), "a") as f:
        f.write('["not", "an", "object"]\n')

    _, client, events = _run(tmp_path, [_done("ok")], session_id=sid)

    steers = [e for e in events if e["type"] == "steer"]
    assert [e.get("ignored") for e in steers] == [
        "empty text", "unknown op", None, "unknown op"]
    assert _operator_lines(client.requests) == ["[operator message] case insensitive"]


# ===========================================================================
# stop
# ===========================================================================

def test_stop_emits_done_with_the_right_summary_and_returns_cleanly(tmp_path):
    sid = "20260914-120020-eeeeeeee"
    _plant(sid, [{"op": "stop", "from": "maxjkh@gmail.com"}])

    result, client, events = _run(tmp_path, [_done("should never run")],
                                  session_id=sid)

    assert client.requests == [], "stop at the boundary takes no turn"
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["summary"] == "stopped by operator"
    assert done[0]["iterations"] == 0
    assert result == "stopped by operator"
    assert sessions.get(sid)["state"] == "stopped"


def test_stop_after_a_turn_finishes_that_turn_first(tmp_path):
    sid = "20260914-120021-ffffffff"
    script = [("working", [("bash", {"command": "true"})],
               lambda: _plant(sid, [{"op": "stop"}])),
              _done("never reached")]
    result, client, events = _run(tmp_path, script, session_id=sid)

    assert len(client.requests) == 1, "the in-flight turn completed"
    assert [e["type"] for e in events if e["type"] == "tool_call"], \
        "the tool call it was in the middle of still ran"
    assert result == "stopped by operator"
    assert [e for e in events if e["type"] == "done"][0]["iterations"] == 1


def test_stop_beats_a_pause_in_the_same_batch(tmp_path):
    sid = "20260914-120022-99999999"
    _plant(sid, [{"op": "pause"}, {"op": "stop"}])
    result, _, events = _run(tmp_path, [_done("x")], session_id=sid)
    assert result == "stopped by operator"
    assert not [e for e in events if e["type"] == "paused"], "never actually paused"


# ===========================================================================
# pause / resume
# ===========================================================================

def test_pause_blocks_at_the_boundary_until_resume(tmp_path):
    sid = "20260914-120030-88888888"
    _plant(sid, [{"op": "pause", "from": "max"}])

    released = threading.Event()

    def _resume_later():
        time.sleep(0.25)
        released.set()
        sessions.append_op(sid, {"op": "resume", "from": "max"})

    t = threading.Thread(target=_resume_later, daemon=True)
    started = time.monotonic()
    t.start()
    result, client, events = _run(tmp_path, [_done("resumed and finished")],
                                  session_id=sid)
    elapsed = time.monotonic() - started
    t.join(timeout=5)

    assert released.is_set(), "the run must not have proceeded before resume"
    assert elapsed >= 0.2
    assert result == "resumed and finished"
    assert len(client.requests) == 1, "exactly one turn, after the resume"

    paused = [e for e in events if e["type"] == "paused"]
    assert len(paused) == 1, "the paused event is emitted once, not per poll"
    assert paused[0]["iteration"] == 1
    assert [e["op"] for e in events if e["type"] == "steer"] == ["pause", "resume"]


def test_pause_can_be_released_by_stop(tmp_path):
    sid = "20260914-120031-77777777"
    _plant(sid, [{"op": "pause"}])
    threading.Timer(0.15, lambda: sessions.append_op(sid, {"op": "stop"})).start()
    result, client, _ = _run(tmp_path, [_done("x")], session_id=sid)
    assert result == "stopped by operator"
    assert client.requests == []


def test_apply_steer_ops_folds_a_batch_in_order():
    logged = []
    msgs = []
    act = runner._apply_steer_ops(
        [{"op": "pause"}, {"op": "say", "text": "hi"}, {"op": "resume"}],
        msgs, logged.append)
    assert act == {"stop": False, "paused": False, "said": 1}
    assert msgs == [{"role": "user", "content": "[operator message] hi"}]

    act = runner._apply_steer_ops([{"op": "resume"}, {"op": "pause"}], [],
                                  logged.append, paused=False)
    assert act["paused"] is True, "the last pause/resume in a batch wins"

    act = runner._apply_steer_ops([], [], logged.append, paused=True)
    assert act["paused"] is True, "an empty batch does not clear a pause"

    msgs = []
    act = runner._apply_steer_ops(["junk", None, 42], msgs, logged.append)
    assert act == {"stop": False, "paused": False, "said": 0} and msgs == []


# ===========================================================================
# Cursor + ledger
# ===========================================================================

def test_cursor_is_persisted_so_a_resumed_run_never_replays(tmp_path):
    sid = "20260914-120040-66666666"
    _plant(sid, [{"op": "say", "text": "first run only"}])

    _, client1, _ = _run(tmp_path, [_done("one")], session_id=sid,
                         log_name="one.jsonl")
    assert _operator_lines(client1.requests) == ["[operator message] first run only"]

    cursor = sessions.get(sid)["meta"]["cursor"]
    assert cursor == os.path.getsize(sessions.inbox_path(sid))

    # Same session id again (the --resume case): the old op must not reappear.
    _, client2, events2 = _run(tmp_path, [_done("two")], session_id=sid,
                               log_name="two.jsonl")
    assert _operator_lines(client2.requests) == []
    assert not [e for e in events2 if e["type"] == "steer"]


def test_ledger_lifecycle_start_to_done(tmp_path):
    sid = "20260914-120041-55555555"
    _, _, _ = _run(tmp_path, [_done("all green")], session_id=sid,
                   task="port the parser", model="qwen-27b-q5")

    rec = sessions.get(sid)
    assert rec["kind"] == "agent"
    assert rec["title"] == "port the parser"
    assert rec["state"] == "done"
    assert rec["summary"] == "all green"
    assert rec["model"] == "qwen-27b-q5"
    assert rec["transcript"].endswith("run.jsonl")
    assert rec["pid"] == os.getpid()
    assert rec["last_event"]


def test_session_id_is_derived_from_an_mcp_log_filename(tmp_path):
    assert runner._session_id_from_log("/x/logs/agent-20260914-1200-ab.jsonl") == \
        "20260914-1200-ab"
    assert runner._session_id_from_log("/x/logs/agent-.jsonl") is None
    assert runner._session_id_from_log("/x/logs/eval-unit.jsonl") is None

    _run(tmp_path, [_done("ok")], log_name="agent-mcpspawned01.jsonl", steer=True)
    assert sessions.get("mcpspawned01")["state"] == "done"


def test_max_iterations_finalizes_the_record(tmp_path):
    sid = "20260914-120042-44444444"
    script = [("looping", [("bash", {"command": "true"})], None)] * 2
    _run(tmp_path, script, session_id=sid, max_iter=2)
    rec = sessions.get(sid)
    assert rec["state"] == "done"
    assert "max iterations" in rec["summary"]


# ===========================================================================
# Resume replay
# ===========================================================================

def test_resume_replay_reconstructs_a_steered_conversation(tmp_path):
    log = tmp_path / "steered.jsonl"
    with open(log, "w") as f:
        for event in [
            {"type": "start", "task": "port the zig module"},
            {"type": "iteration", "number": 1},
            {"type": "assistant", "content": "I'll write it from scratch."},
            {"type": "tool_call", "name": "bash", "args": {}, "result": "ok"},
            {"type": "steer", "op": "say", "text": "port it, don't rewrite it",
             "from": "max"},
            {"type": "iteration", "number": 2},
            {"type": "assistant", "content": "Understood — porting."},
        ]:
            f.write(json.dumps(event) + "\n")

    msgs = runner._rebuild_messages_from_log(str(log), "SYS")
    assert [m["role"] for m in msgs] == [
        "system", "user", "assistant", "user", "user", "assistant"]
    assert msgs[3]["content"].startswith("[Previous tool call: bash]")
    assert msgs[4]["content"] == "[operator message] port it, don't rewrite it"
    assert msgs[5]["content"] == "Understood — porting."


def test_resume_replay_skips_non_say_and_empty_steer_events(tmp_path):
    log = tmp_path / "ops.jsonl"
    with open(log, "w") as f:
        for event in [
            {"type": "start", "task": "t"},
            {"type": "steer", "op": "pause"},
            {"type": "steer", "op": "resume"},
            {"type": "steer", "op": "say", "text": ""},
            {"type": "steer", "op": "say", "ignored": "empty text"},
        ]:
            f.write(json.dumps(event) + "\n")
    msgs = runner._rebuild_messages_from_log(str(log), "SYS")
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_full_resume_round_trip_through_the_runner(tmp_path):
    """Steer a run, then --resume it: the operator message comes back."""
    sid = "20260914-120050-33333333"
    _plant(sid, [{"op": "say", "text": "keep the API stable"}])
    _run(tmp_path, [("noted", [("bash", {"command": "true"})], None), _done("a")],
         session_id=sid, log_name="first.jsonl")

    _, client, _ = _run(tmp_path, [_done("b")], session_id=sid,
                        log_name="second.jsonl",
                        resume_from=str(tmp_path / "first.jsonl"))
    assert _operator_lines(client.requests[0]) == [
        "[operator message] keep the API stable"]
