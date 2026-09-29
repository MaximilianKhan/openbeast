"""Regression tests for the 2026-09-29 review's runner efficiency findings.

efficiency-2  proactive compaction has hysteresis (frees to a low-water mark)
efficiency-3  one oversized result is stubbed alone, not after the whole history
efficiency-5  agent completions carry max_tokens; the client does not triple
              a timed-out request

Every case builds its own history and a scripted fake client — no server,
no GPU, no real tool execution beyond a stubbed handler.
"""
import json
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import runner  # noqa: E402
import tools   # noqa: E402


# ---------------------------------------------------------------------------
# efficiency-3 — solo eviction of an oversized result
# ---------------------------------------------------------------------------

def _history(sizes: list[int]) -> tuple[list[dict], dict]:
    msgs = [{"role": "system", "content": "S" * 500}, {"role": "user", "content": "T" * 300}]
    idx = {}
    for k, size in enumerate(sizes, 1):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(k)}]})
        idx[len(msgs)] = k
        msgs.append({"role": "tool", "tool_call_id": str(k), "content": chr(64 + k) * size})
    return msgs, idx


def _tool_contents(msgs):
    return [m["content"] for m in msgs if m["role"] == "tool"]


def test_one_giant_result_is_stubbed_alone_and_history_survives():
    # 20 useful 8 KB results, then one 2M-char fetch; the ask exceeds every
    # older result combined. The old walk stubbed all 21.
    msgs, idx = _history([8000] * 20 + [2_000_000])
    n, freed = runner.compact_messages(msgs, 1_500_000, idx)
    assert n == 1 and freed >= 1_500_000
    contents = _tool_contents(msgs)
    assert contents[-1] == "[tool result elided: 2000000 chars, call #21]"
    assert all(len(c) == 8000 for c in contents[:-1]), "older results survive"


def test_giant_result_in_the_middle_spares_its_elders_too():
    msgs, idx = _history([1000, 1000, 50_000, 1000])
    n, _ = runner.compact_messages(msgs, 10_000, idx)
    assert n == 1
    contents = _tool_contents(msgs)
    assert contents[2].startswith("[tool result elided: 50000 chars")
    assert [len(c) for i, c in enumerate(contents) if i != 2] == [1000, 1000, 1000]


def test_solo_rule_never_reaches_past_the_plain_walk():
    # Negative control: the oldest results cover the ask, so the big NEWER
    # result is not touched — newest-survives is preserved.
    msgs, idx = _history([3000, 3000, 50_000])
    n, _ = runner.compact_messages(msgs, 4000, idx)
    assert n == 2
    contents = _tool_contents(msgs)
    assert contents[0].startswith("[tool result elided") and contents[1].startswith("[tool result elided")
    assert contents[2] == "C" * 50_000


def test_oldest_first_unchanged_when_no_single_result_covers():
    msgs, idx = _history([1000] * 5)
    n, freed = runner.compact_messages(msgs, 1500, idx)
    assert n == 2 and freed > 1500
    assert [c.startswith("[tool result elided") for c in _tool_contents(msgs)] == [
        True, True, False, False, False]


def test_solo_rule_does_not_pick_an_aged_operator_message():
    # A big aged operator message would cover the ask alone, but tool results
    # still go first — a directive outranks a transcript.
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            {"role": "user", "content": runner._STEER_PREFIX + "S" * 20_000},
            {"role": "tool", "content": "T" * 1000},
            {"role": "tool", "content": "U" * 1000}]
    n, _ = runner.compact_messages(msgs, 1500, steer_eligible={2})
    assert n == 2
    assert msgs[2]["content"].startswith(runner._STEER_PREFIX)


# ---------------------------------------------------------------------------
# efficiency-2 — hysteresis, measured through run_agent itself
# ---------------------------------------------------------------------------

def _long_run(tmp_path, monkeypatch, budget: int, iters: int, seed: int = 7):
    """Drive run_agent itself for `iters` turns of random 1-12 KB bash results
    under --context-budget; return (compaction events, chars asked per call)."""
    rng = random.Random(seed)
    monkeypatch.setattr(tools, "TOOL_HANDLERS", dict(
        tools.TOOL_HANDLERS, bash=lambda **kw: "r" * rng.randint(1000, 12_000)))
    monkeypatch.setattr(runner, "TOOL_HANDLERS", tools.TOOL_HANDLERS)
    asks = []
    real = runner.compact_messages

    def spy(messages, chars_to_free, *a, **kw):
        asks.append(chars_to_free)
        return real(messages, chars_to_free, *a, **kw)

    monkeypatch.setattr(runner, "compact_messages", spy)
    script = [_Resp(_Msg(tool_calls=[_TC(str(k), "bash", {"command": "x"})]))
              for k in range(iters)]
    script.append(_done())
    fake = _Fake(script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: fake)
    log = tmp_path / "r.jsonl"
    out = runner.run_agent("task", max_iter=iters + 1, log_file=str(log),
                           system_prompt="s" * 8000, context_budget=budget,
                           workdir=str(tmp_path))
    assert out == "fin"
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    return [e for e in events if e["type"] == "compaction"], asks


def test_budget_compaction_is_rare_not_every_other_turn(tmp_path, monkeypatch):
    comp, _ = _long_run(tmp_path, monkeypatch, budget=85_000, iters=120)
    # The review measured 62-71 compacting turns of 120 without hysteresis.
    assert comp, "the run did cross the trigger"
    assert len(comp) <= 20, len(comp)


def test_budget_compaction_frees_the_whole_low_water_gap(tmp_path, monkeypatch):
    budget = 20_000
    _, asks = _long_run(tmp_path, monkeypatch, budget=budget, iters=30)
    assert asks
    # Freeing only back under the 70% trigger asked for a few KB; hysteresis
    # asks for at least the 70%->50% gap every time (a literal, not the
    # constant, so the test cannot agree with a regressed constant).
    gap_chars = int(budget * 0.20) * runner._CHARS_PER_TOKEN
    assert all(a >= gap_chars for a in asks), asks


# ---------------------------------------------------------------------------
# efficiency-5 — max_tokens + client retries
# ---------------------------------------------------------------------------

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


class _Fake:
    def __init__(self, script):
        self.script = list(script)
        self.kwargs: list[dict] = []
        self.requests: list[list[dict]] = []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.kwargs.append(kw)
        self.requests.append([dict(m) for m in kw["messages"]])
        return self.script.pop(0)


def _done():
    return _Resp(_Msg(tool_calls=[_TC("d", "task_done", {"summary": "fin"})]))


def _run(tmp_path, monkeypatch, script, env=None):
    for k in ("OPENBEAST_EVAL", "OPENBEAST_AGENT_MAX_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    ctor = {}
    fake = _Fake(script)

    def factory(**kw):
        ctor.update(kw)
        return fake

    monkeypatch.setattr(runner, "OpenAI", factory)
    out = runner.run_agent("task", max_iter=6, log_file=str(tmp_path / "r.jsonl"),
                           system_prompt="sys", workdir=str(tmp_path))
    return out, fake, ctor


def test_agent_requests_carry_a_max_tokens_cap(tmp_path, monkeypatch):
    out, fake, _ = _run(tmp_path, monkeypatch, [_done()])
    assert out == "fin"
    assert fake.kwargs[0]["max_tokens"] == runner._DEFAULT_MAX_COMPLETION_TOKENS
    assert fake.kwargs[0]["max_tokens"] > 20480, "room for the full thinking budget"


def test_max_tokens_env_override_and_zero_means_uncapped(tmp_path, monkeypatch):
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()],
                      env={"OPENBEAST_AGENT_MAX_TOKENS": "4096"})
    assert fake.kwargs[0]["max_tokens"] == 4096
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()],
                      env={"OPENBEAST_AGENT_MAX_TOKENS": "0"})
    assert "max_tokens" not in fake.kwargs[0]


def test_eval_runs_stay_uncapped(tmp_path, monkeypatch):
    # Eval rows are bounded by run_eval's wall timeout; capping them would
    # change measured behaviour under unlimited-thinking configs.
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()], env={"OPENBEAST_EVAL": "1"})
    assert "max_tokens" not in fake.kwargs[0]


def test_client_does_not_silently_triple_a_timed_out_request(tmp_path, monkeypatch):
    _, _, ctor = _run(tmp_path, monkeypatch, [_done()])
    assert "max_retries" in ctor and ctor["max_retries"] <= 1


def test_truncated_turn_is_trimmed_and_nudged(tmp_path, monkeypatch):
    loop = "blah " * 20_000   # a degenerate repetition the cap cut off
    script = [_Resp(_Msg(content=loop), finish="length"), _done()]
    out, fake, _ = _run(tmp_path, monkeypatch, script)
    assert out == "fin"
    second = fake.requests[1]
    stored = [m for m in second if m["role"] == "assistant"][0]["content"]
    assert len(stored) < 5000, "the runaway text is not re-sent every turn"
    assert "output cap" in second[-1]["content"]


def test_invalid_env_value_falls_back_to_default(tmp_path, monkeypatch):
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()],
                      env={"OPENBEAST_AGENT_MAX_TOKENS": "lots"})
    assert fake.kwargs[0]["max_tokens"] == runner._DEFAULT_MAX_COMPLETION_TOKENS


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
