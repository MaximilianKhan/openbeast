#!/usr/bin/env python3
"""Harness agentics bundle (2026-09-11, tools SOTA review #6/#8/#9/#10).

Pure unit tests — no llama-server, no GPU:
  * update_plan: statuses, one-in_progress rule, tolerance, ContextVar
    state, plan_block rendering, runner re-injection + --resume replay
  * runner context-window management: overflow detection on the exact
    llama-server error shapes, eviction order/limits/protected messages,
    proactive budget compaction, telemetry line
  * edit_file teach-on-failure: whitespace near-match, pasted line-number
    prefixes, first-line-only hint, write_file fallback after 2 failures,
    post-edit window on success (bounded)
  * schema teaching: the facts local models act on are in the schema text
    (bash merged stderr / fresh shell / cap / background kill; fetch block
    list; read_file 1-based offset; web_search SEARXNG_URL + pageno/time_range)

Run: python3 -m pytest tests/test_harness_agentics.py -q
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import runner  # noqa: E402
import tools  # noqa: E402


def _schema(name: str) -> dict:
    return next(s["function"] for s in tools.TOOL_SCHEMAS if s["function"]["name"] == name)


# ---------------------------------------------------------------------------
# update_plan
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_plan():
    tools.reset_plan()
    tools._EDIT_FAILS.clear()
    yield
    tools.reset_plan()
    tools._EDIT_FAILS.clear()


def test_update_plan_registered_runner_only():
    names = [s["function"]["name"] for s in tools.TOOL_SCHEMAS]
    assert "update_plan" in names and "task_done" in names
    assert len(names) == 10
    assert tools.TOOL_HANDLERS["update_plan"] is tools.update_plan
    # Runner-only: the MCP/WebUI surfaces have no loop to re-inject into.
    import mcp_server
    assert "update_plan" not in mcp_server.mcp._tool_manager._tools


def test_update_plan_basic_ladder():
    out = tools.update_plan([
        {"step": "read the parser", "status": "done"},
        {"step": "fix tokenizer", "status": "in_progress"},
        {"step": "run tests", "status": "pending"},
        {"step": "docs", "status": "skipped"},
    ], explanation="tokenizer is the bug")
    assert out.startswith("Plan updated: 1/4 done; now: fix tokenizer (tokenizer is the bug)")
    assert "1. [x] read the parser" in out
    assert "2. [>] fix tokenizer" in out
    assert "3. [ ] run tests" in out
    assert "4. [-] docs" in out
    assert tools.get_plan()[1] == {"step": "fix tokenizer", "status": "in_progress"}


def test_update_plan_one_in_progress_rule():
    out = tools.update_plan([
        {"step": "a", "status": "in_progress"},
        {"step": "b", "status": "in_progress"},
    ])
    assert out.startswith("Error") and "only ONE step may be in_progress" in out
    assert tools.get_plan() is None  # rejected plans don't replace the state


def test_update_plan_tolerates_local_model_shapes():
    # bare strings, status aliases, JSON-encoded array, {"steps": [...]} wrapper
    assert "Plan updated: 0/2" in tools.update_plan(["first", "second"])
    out = tools.update_plan([{"step": "x", "status": "completed"},
                             {"step": "y", "status": "active"}])
    assert "1. [x] x" in out and "2. [>] y" in out
    out = tools.update_plan(json.dumps([{"step": "z", "status": "todo"}]))
    assert "1. [ ] z" in out
    out = tools.update_plan({"steps": [{"step": "w", "status": "pending"}]})
    assert "1. [ ] w" in out


def test_update_plan_rejects_bad_input_with_teaching():
    assert "non-empty array" in tools.update_plan([])
    assert "non-empty array" in tools.update_plan(None)
    assert "unknown status" in tools.update_plan([{"step": "a", "status": "later"}])
    assert "no 'step' text" in tools.update_plan([{"status": "pending"}])
    assert "too many steps" in tools.update_plan([f"s{i}" for i in range(21)])
    assert "must be a JSON array" in tools.update_plan("not json")


def test_update_plan_bounds_step_text():
    out = tools.update_plan([{"step": "x" * 500, "status": "pending"}])
    assert len(tools.get_plan()[0]["step"]) == tools._PLAN_STEP_CHARS
    assert "…" in out


def test_plan_block_compact_and_all_done_hint():
    assert tools.plan_block() == ""
    tools.update_plan([{"step": "a", "status": "done"}, {"step": "b", "status": "done"}])
    block = tools.plan_block()
    assert block.startswith("[plan — 2/2 done; keep it current with update_plan]")
    assert block.count("\n") == 2  # header + 2 rows: ~50 tokens, not a wall
    assert "all steps done; call task_done" in tools.update_plan(
        [{"step": "a", "status": "done"}])


def test_plan_state_is_a_contextvar():
    import contextvars
    tools.update_plan([{"step": "outer", "status": "in_progress"}])
    ctx = contextvars.copy_context()
    ctx.run(tools.update_plan, [{"step": "inner", "status": "pending"}])
    # The copied context's write didn't leak into ours.
    assert tools.get_plan()[0]["step"] == "outer"
    assert ctx.run(tools.get_plan)[0]["step"] == "inner"


def test_runner_reinjects_plan_transiently():
    tools.update_plan([{"step": "a", "status": "in_progress"}])
    plan = tools.plan_block()
    history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
               {"role": "tool", "tool_call_id": "1", "content": "ok"}]
    payload = runner._with_plan(history, plan)
    assert payload[-1] == {"role": "user", "content": plan}
    assert len(history) == 4  # never stored in the history
    # A trailing user nudge absorbs the block so roles keep alternating.
    nudged = history + [{"role": "user", "content": "nudge"}]
    payload = runner._with_plan(nudged, plan)
    assert payload[-1]["content"] == "nudge\n\n" + plan and len(payload) == 5
    # The original task (messages[1]) is never rewritten.
    payload = runner._with_plan(history[:2], plan)
    assert payload[1]["content"] == "task" and payload[-1]["content"] == plan
    assert runner._with_plan(history, "") is history


def test_resume_replays_update_plan(tmp_path):
    log = tmp_path / "agent.jsonl"
    events = [
        {"type": "start", "task": "do it"},
        {"type": "tool_call", "name": "update_plan",
         "args": {"steps": [{"step": "a", "status": "done"},
                            {"step": "b", "status": "in_progress"}]},
         "result": "Plan updated"},
        {"type": "tool_call", "name": "bash", "args": {"command": "ls"}, "result": "x"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    msgs = runner._rebuild_messages_from_log(str(log), "sys")
    assert msgs[1]["content"] == "do it" and len(msgs) == 4
    assert tools.plan_block().startswith("[plan — 1/2 done")


def test_update_plan_schema_teaches_when():
    fn = _schema("update_plan")
    d = fn["description"]
    assert "multi-step" in d and "in_progress" in d and "every turn" in d
    assert fn["parameters"]["required"] == ["steps"]
    item = fn["parameters"]["properties"]["steps"]["items"]
    assert item["properties"]["status"]["enum"] == ["pending", "in_progress", "done", "skipped"]
    assert "update_plan" in runner._AGENT_INSTRUCTIONS
    assert runner._tool_summary("update_plan", {"steps": [1, 2, 3], "explanation": "why"}) == "3 steps — why"


# ---------------------------------------------------------------------------
# runner context-window management
# ---------------------------------------------------------------------------

_SERVER_400 = ("Error code: 400 - {'error': {'code': 400, 'message': 'request (9000 tokens) "
               "exceeds the available context size (8192 tokens), try increasing it', 'type': "
               "'exceed_context_size_error', 'n_prompt_tokens': 9000, 'n_ctx': 8192}}")


@pytest.mark.parametrize("err", [
    _SERVER_400,
    "input (12000 tokens) is larger than the max context size (8192 tokens). skipping",
    "Context size has been exceeded.",
    "context shift is disabled",
    '{"error":{"type":"exceed_context_size_error","message":"..."}}',
])
def test_overflow_detection_on_server_shapes(err):
    assert runner._is_context_overflow(err)


@pytest.mark.parametrize("err", [
    "Connection error.", "Error code: 500 - {'error': {'message': 'slot unavailable'}}",
    "", "timed out",
])
def test_overflow_detection_negative(err):
    assert not runner._is_context_overflow(err)


def test_overflow_token_parse():
    assert runner._overflow_tokens(_SERVER_400) == (9000, 8192)
    assert runner._overflow_tokens(
        "input (12000 tokens) is larger than the max context size (8192 tokens)") == (12000, 8192)
    assert runner._overflow_tokens("Context size has been exceeded.") is None


def _history(n_tools: int, size: int = 1000) -> tuple[list[dict], dict]:
    msgs = [{"role": "system", "content": "S" * 5000}, {"role": "user", "content": "T" * 3000}]
    idx = {}
    for k in range(1, n_tools + 1):
        msgs.append({"role": "assistant", "content": "thinking", "tool_calls": [{"id": str(k)}]})
        idx[len(msgs)] = k
        msgs.append({"role": "tool", "tool_call_id": str(k), "content": chr(64 + k) * size})
    return msgs, idx


def test_compaction_evicts_oldest_first_and_spares_recent():
    msgs, idx = _history(5)
    n, freed = runner.compact_messages(msgs, 1500, idx)
    assert n == 2 and freed > 1500
    assert msgs[3]["content"] == "[tool result elided: 1000 chars, call #1]"
    assert msgs[5]["content"] == "[tool result elided: 1000 chars, call #2]"
    assert msgs[7]["content"].startswith("CCCC")
    # Protected: system prompt, task, assistant turns untouched.
    assert msgs[0]["content"] == "S" * 5000 and msgs[1]["content"] == "T" * 3000
    assert all(m["content"] == "thinking" for m in msgs if m["role"] == "assistant")


def test_compaction_recent_evicted_only_as_last_resort():
    msgs, idx = _history(3)
    # Older results can't cover the ask → the two newest go too, oldest first.
    n, freed = runner.compact_messages(msgs, 10_000, idx)
    assert n == 3
    assert [m["content"] for m in msgs if m["role"] == "tool"] == [
        f"[tool result elided: 1000 chars, call #{k}]" for k in (1, 2, 3)]
    # Nothing left: a second pass is a no-op, never a crash or a re-stub.
    assert runner.compact_messages(msgs, 10_000, idx) == (0, 0)


def test_compaction_skips_small_results():
    msgs, idx = _history(3, size=50)
    assert runner.compact_messages(msgs, 10_000, idx) == (0, 0)
    assert all(len(m["content"]) == 50 for m in msgs if m["role"] == "tool")


def test_estimate_tokens_counts_schemas_and_plan():
    msgs, _ = _history(2)
    base = runner.estimate_tokens(msgs)
    assert base > (5000 + 3000 + 2000) // 4  # schemas add on top
    assert runner.estimate_tokens(msgs, extra_chars=4000) == base + 1000


class _FakeClient:
    """Raises the server's overflow error until the prompt shrinks below n_ctx."""

    def __init__(self, n_ctx_chars: int, script: list):
        self.n_ctx_chars = n_ctx_chars
        self.script = list(script)
        self.calls: list[list[dict]] = []
        self.chat = self
        self.completions = self

    def create(self, model, messages, tools, temperature):
        self.calls.append([dict(m) for m in messages])
        size = sum(len(m.get("content") or "") for m in messages)
        if size > self.n_ctx_chars:
            raise RuntimeError(
                f"Error code: 400 - {{'error': {{'code': 400, 'message': 'request "
                f"({size // 4} tokens) exceeds the available context size "
                f"({self.n_ctx_chars // 4} tokens), try increasing it', 'type': "
                f"'exceed_context_size_error', 'n_prompt_tokens': {size // 4}, "
                f"'n_ctx': {self.n_ctx_chars // 4}}}}}")
        return self.script.pop(0)


class _Msg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _TC:
    def __init__(self, id_, name, args):
        self.id = id_
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()


class _Resp:
    def __init__(self, msg, finish="tool_calls"):
        self.choices = [type("C", (), {"message": msg, "finish_reason": finish})()]
        self.usage = None


def test_loop_compacts_on_overflow_instead_of_retrying(tmp_path, monkeypatch, capsys):
    big = "Z" * 4000
    monkeypatch.setattr(tools, "TOOL_HANDLERS", dict(tools.TOOL_HANDLERS, bash=lambda **kw: big))
    monkeypatch.setattr(runner, "TOOL_HANDLERS", tools.TOOL_HANDLERS)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    script = [
        _Resp(_Msg(tool_calls=[_TC("1", "bash", {"command": "a"})])),
        _Resp(_Msg(tool_calls=[_TC("2", "bash", {"command": "b"})])),
        _Resp(_Msg(tool_calls=[_TC("3", "bash", {"command": "c"})])),
        _Resp(_Msg(tool_calls=[_TC("4", "task_done", {"summary": "fin"})])),
    ]
    fake = _FakeClient(n_ctx_chars=10_000, script=script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: fake)
    log = tmp_path / "run.jsonl"
    out = runner.run_agent("task", max_iter=8, log_file=str(log), system_prompt="sys",
                           workdir=str(tmp_path))
    assert out == "fin"
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    comp = [e for e in events if e["type"] == "compaction"]
    assert comp and all(e["reason"] == "overflow" for e in comp)
    assert events[-1]["type"] == "done" and events[-1]["compactions"] == len(comp)
    # The retry after an overflow is NOT the identical payload.
    errs = [i for i, c in enumerate(fake.calls)
            if sum(len(m.get("content") or "") for m in c) > 10_000]
    assert errs and all(fake.calls[i + 1] != fake.calls[i] for i in errs)
    captured = capsys.readouterr()
    assert "[compaction] overflow" in captured.err
    assert "COMPACTIONS: " + str(len(comp)) in captured.out
    assert "TOKENS: prompt=" in captured.out


def test_loop_stops_when_nothing_left_to_compact(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    fake = _FakeClient(n_ctx_chars=100, script=[])  # system prompt alone overflows
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: fake)
    log = tmp_path / "run.jsonl"
    out = runner.run_agent("task", max_iter=50, log_file=str(log), system_prompt="s" * 500)
    assert out == "(max iterations reached)"
    assert len(fake.calls) == 1  # no 50 × identical retries
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert any(e["type"] == "context_overflow_unrecoverable" for e in events)
    assert "nothing left to compact" in capsys.readouterr().err


def test_loop_proactive_budget_compaction(tmp_path, monkeypatch, capsys):
    big = "Q" * 8000
    monkeypatch.setattr(tools, "TOOL_HANDLERS", dict(tools.TOOL_HANDLERS, bash=lambda **kw: big))
    monkeypatch.setattr(runner, "TOOL_HANDLERS", tools.TOOL_HANDLERS)
    script = [
        _Resp(_Msg(tool_calls=[_TC("1", "bash", {"command": "a"})])),
        _Resp(_Msg(tool_calls=[_TC("2", "bash", {"command": "b"})])),
        _Resp(_Msg(tool_calls=[_TC("3", "bash", {"command": "c"})])),
        _Resp(_Msg(tool_calls=[_TC("4", "task_done", {"summary": "fin"})])),
    ]
    fake = _FakeClient(n_ctx_chars=10**9, script=script)  # server never complains
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: fake)
    log = tmp_path / "run.jsonl"
    # Budget 5000 tokens → 70% = 3500 tokens = 14,000 chars; schemas ~2k tokens.
    out = runner.run_agent("task", max_iter=8, log_file=str(log), system_prompt="sys",
                           context_budget=5000, workdir=str(tmp_path))
    assert out == "fin"
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    comp = [e for e in events if e["type"] == "compaction"]
    assert comp and all(e["reason"] == "budget" for e in comp)
    assert "[compaction] budget" in capsys.readouterr().err
    # The stubs went to the server on the following call.
    assert any(any((m.get("content") or "").startswith("[tool result elided")
                   for m in c) for c in fake.calls)


def test_run_eval_parses_compactions():
    sys.path.insert(0, os.path.join(ROOT, "evals"))
    import run_eval
    assert run_eval._parse_compactions("TOKENS: prompt=1 completion=2 total=3\nCOMPACTIONS: 4\n") == 4
    assert run_eval._parse_compactions("TOKENS: prompt=1 completion=2 total=3\n") == 0


# ---------------------------------------------------------------------------
# edit_file teach-on-failure + post-edit window
# ---------------------------------------------------------------------------

SRC = "def hello():\n    print('hello')\n    return 1\n\n\ndef bye():\n\tprint('bye')\n"


@pytest.fixture
def src_file(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(SRC)
    return str(p)


def test_edit_whitespace_near_match_quotes_file_text(src_file):
    out = tools.edit_file(src_file, "def hello():\n  print('hello')\n  return 1", "x")
    assert out.startswith("Error: exact match not found")
    assert "near-match is at line 1" in out
    assert "whitespace" in out
    assert "line 2: file has \"    print('hello')\" but old_string has \"  print('hello')\"" in out
    assert "1\tdef hello():\n2\t    print('hello')\n3\t    return 1" in out
    assert open(src_file).read() == SRC  # nothing written


def test_edit_tabs_vs_spaces_named(src_file):
    out = tools.edit_file(src_file, "def bye():\n    print('bye')", "x")
    assert "near-match is at line 6" in out
    assert "file has \"\\tprint('bye')\"" in out


def test_edit_pasted_line_number_prefixes_detected(src_file):
    for prefix in ("{n}\t", "{n}: ", "{n}:"):
        old = "\n".join(prefix.format(n=i) + ln for i, ln in
                        enumerate(["def hello():", "    print('hello')"], 1))
        out = tools.edit_file(src_file, old, "x")
        assert "line-number prefixes" in out, prefix
        assert "near-match is at line 1" in out
        tools._EDIT_FAILS.clear()


def test_edit_first_line_only_hint(src_file):
    out = tools.edit_file(src_file, "def hello():\n    return 42", "x")
    assert "the first line was found but the following lines differ" in out
    assert "1\tdef hello():\n2\t    print('hello')" in out


def test_edit_no_match_at_all(src_file):
    out = tools.edit_file(src_file, "completely absent", "x")
    assert "not even a whitespace-insensitive match" in out
    assert "not found" in out


def test_edit_fallback_after_two_consecutive_failures(src_file):
    first = tools.edit_file(src_file, "nope", "x")
    assert "FALLBACK" not in first
    second = tools.edit_file(src_file, "nope again", "x")
    assert "failed edit #2 in a row" in second and "write_file the WHOLE file" in second
    third = tools.edit_file(src_file, "def hello():\n    return 1", "x")  # ambiguous/miss counts too
    assert "failed edit #3" in third
    # A success resets the counter.
    ok = tools.edit_file(src_file, "return 1", "return 2")
    assert ok.startswith("Edited")
    assert "FALLBACK" not in tools.edit_file(src_file, "nope", "x")


def test_edit_fail_counter_is_per_file(tmp_path, src_file):
    other = tmp_path / "o.py"
    other.write_text("a\n")
    tools.edit_file(src_file, "nope", "x")
    assert "FALLBACK" not in tools.edit_file(str(other), "nope", "x")


def test_edit_success_post_edit_window(src_file):
    out = tools.edit_file(src_file, "    return 1", "    x = 1\n    return x")
    assert out.startswith("Edited") and "replaced 1 line with 2 lines" in out
    assert "Post-edit window (lines 1-6):" in out
    assert "3\t    x = 1\n4\t    return x\n5\t\n6\t" in out
    assert "1\tdef hello():" in out


def test_edit_window_bounded(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("\n".join(f"L{i:03d}" for i in range(1, 101)) + "\n")
    new = "\n".join(f"N{i}" for i in range(40))
    out = tools.edit_file(str(p), "L050", new)
    assert "window capped" in out
    body = out.split("Post-edit window")[1]
    assert body.count("\n") <= tools._EDIT_WINDOW_MAX_LINES + 1
    # Near-match quote is bounded too.
    out = tools.edit_file(str(p), "\n".join(f" L{i:03d} " for i in range(1, 60)), "x")
    assert "more lines not shown" in out
    assert out.count("\n") < 40


def test_edit_replace_all_window(src_file):
    out = tools.edit_file(src_file, "print", "log", replace_all=True)
    assert out.startswith("Replaced 2 occurrences") and "Post-edit window" in out


def test_edit_schema_teaches_prefix_rule():
    d = _schema("edit_file")["description"]
    assert "WITHOUT the line-number prefixes" in d and "nearest matching lines" in d


# ---------------------------------------------------------------------------
# schema teaching pass (#6)
# ---------------------------------------------------------------------------

def test_bash_schema_states_the_facts():
    fn = _schema("bash")
    d = fn["description"]
    for fact in ("stderr are MERGED", "FRESH shell", "50 KB", "(exit code N)", "killed"):
        assert fact in d, fact
    assert "process group" in fn["parameters"]["properties"]["timeout"]["description"]


def test_fetch_schema_warns_about_blocked_hosts():
    d = _schema("fetch")["description"]
    for host in ("localhost", "192.168", "100.64", "curl"):
        assert host in d, host


def test_read_file_schema_and_code_agree_on_1_based(tmp_path):
    fn = _schema("read_file")
    assert "1-based" in fn["description"]
    off = fn["parameters"]["properties"]["offset"]
    assert off["default"] == 1 and "1-based" in off["description"]
    p = tmp_path / "f.txt"
    p.write_text("".join(f"line {i}\n" for i in range(1, 11)))
    out = tools.read_file(str(p), offset=4, limit=2)
    assert "lines 4-5 of 10" in out and "4\tline 4\n5\tline 5\n" in out
    assert "offset=6" in out  # resume hint is 1-based too
    assert "lines 1-3" in tools.read_file(str(p), offset=0, limit=3)
    err = tools.read_file(str(p), offset=11)
    assert err.startswith("Error") and "offset=1" in err  # last page 1-based
    # The grep tool's numbers feed straight in.
    g = tools.grep("line 7", str(p))
    assert ":7:" in g
    assert "7\tline 7" in tools.read_file(str(p), offset=7, limit=1)


def test_mcp_mirror_defaults_match():
    import mcp_server
    import inspect
    assert inspect.signature(mcp_server.read_file).parameters["offset"].default == 1
    assert "1-based" in mcp_server.read_file.__doc__
    assert "MERGED" in mcp_server.bash.__doc__
    assert "localhost" in mcp_server.fetch.__doc__
    ws = inspect.signature(mcp_server.web_search).parameters
    assert "pageno" in ws and "time_range" in ws


def test_web_search_schema_and_params(monkeypatch):
    fn = _schema("web_search")
    assert "SEARXNG_URL" in fn["description"] and "8888" in fn["description"]
    props = fn["parameters"]["properties"]
    assert props["pageno"]["default"] == 1
    assert props["time_range"]["enum"] == ["day", "month", "year"]
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"results": [{"title": "t", "url": "u", "content": "c"}]}).encode()

    def fake_open(req, timeout):
        seen["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_open)
    monkeypatch.setenv("SEARXNG_URL", "http://search.local:9999")
    out = tools.web_search("zig arraylist", pageno=3, time_range="month")
    assert out.startswith("Web search: zig arraylist")
    assert seen["url"].startswith("http://search.local:9999/search?")
    assert "pageno=3" in seen["url"] and "time_range=month" in seen["url"]
    # Invalid values are dropped, never forwarded.
    tools.web_search("q", pageno="x", time_range="decade")
    assert "pageno" not in seen["url"] and "time_range" not in seen["url"]
    tools.web_search("q")  # defaults add nothing
    assert "pageno" not in seen["url"]
