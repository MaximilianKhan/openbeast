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

def _long_run(tmp_path, monkeypatch, budget: int, iters: int, seed: int = 7,
              giant_at: int | None = None, sink: list | None = None):
    """Drive run_agent itself for `iters` turns of random 1-12 KB bash results
    under --context-budget; return (compaction events, chars asked per call).
    `giant_at` makes that call (1-based) return 2M chars instead; `sink`
    receives the fake client, whose .requests are the payloads sent."""
    rng = random.Random(seed)
    calls = [0]

    def bash(**kw):
        calls[0] += 1
        if calls[0] == giant_at:
            return "G" * 2_000_000
        return "r" * rng.randint(1000, 12_000)

    monkeypatch.setattr(tools, "TOOL_HANDLERS", dict(tools.TOOL_HANDLERS, bash=bash))
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
    if sink is not None:
        sink.append(fake)
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


def _live_results(request):
    return [m["content"] for m in request if m["role"] == "tool"
            and not m["content"].startswith(runner._STUB_PREFIX)]


@pytest.mark.parametrize("giant_at", [40, 60, 90])
def test_giant_result_after_hysteresis_spares_the_history(tmp_path, monkeypatch, giant_at):
    # The two fixes together: past the first compaction the history sits
    # between the 50% low-water mark and the 70% trigger, so the proactive ask
    # is the giant result PLUS that gap — no single result covers it. The old
    # "solo only if it covers the whole ask" rule never fired there and the
    # oldest-first walk stubbed every older result again (review fixup:
    # 432/550 simulated positions wiped the whole history).
    sink: list = []
    comp, _ = _long_run(tmp_path, monkeypatch, budget=85_000, iters=giant_at + 1,
                        giant_at=giant_at, sink=sink)
    reqs = sink[0].requests
    assert len(comp) >= 2, "the history had compacted before the giant arrived"
    # Proactive compaction runs before the request is sent, so the giant is
    # never live in a payload: compare the request before it arrived with the
    # first one after.
    older = len(_live_results(reqs[giant_at - 1]))
    after = _live_results(reqs[giant_at])
    assert comp[-1]["evicted"] == 1, "the giant alone was stubbed"
    assert not any(c.startswith("G") for c in after), "the giant itself is stubbed"
    assert older >= 10
    # Stubbing the giant alone is back under the trigger: no older result is
    # spent on the 70%->50% gap (round 2; round 1 still lost a quarter).
    assert len(after) == older, (older, len(after))


def test_small_results_still_compact_oldest_first_through_run_agent(tmp_path, monkeypatch):
    # Negative control: no giant, so every compaction still stubs a prefix of
    # the live results (the oldest), never a newer one ahead of an older one.
    sink: list = []
    _long_run(tmp_path, monkeypatch, budget=20_000, iters=30, sink=sink)
    for req in sink[0].requests:
        tools_seen = [m["content"] for m in req if m["role"] == "tool"]
        flags = [c.startswith(runner._STUB_PREFIX) for c in tools_seen]
        assert flags == sorted(flags, reverse=True), flags


# ---------------------------------------------------------------------------
# efficiency-3, round 2 — hysteresis must not buy low-water with history
#
# The round-1 prefix rule stubbed the giant first, then kept walking
# oldest-first to reach the 50% low-water mark: 8 of 29 older results still
# went (the reviewer's interplay.py). Stubbing the giant alone is already
# back under the trigger; the gap is taken only from the result that is
# cheap to lose.
# ---------------------------------------------------------------------------

def _live(msgs):
    return sum(1 for m in msgs if m["role"] == "tool"
               and not m["content"].startswith(runner._STUB_PREFIX))


def _add_result(msgs, idx, k, size, fill="r"):
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(k)}]})
    idx[len(msgs)] = k
    msgs.append({"role": "tool", "content": fill * size})


def _proactive(msgs, idx, budget):
    """Exactly what run_agent's proactive branch does before a request."""
    asks = runner.proactive_asks(runner.estimate_tokens(msgs), budget)
    if asks:
        return runner.compact_messages(msgs, asks[1], idx, must_free=asks[0])
    return None


def _grow(budget, fill, seed):
    rng = random.Random(seed)
    msgs = [{"role": "system", "content": "s" * 8000}, {"role": "user", "content": "task"}]
    idx: dict = {}
    k = 0
    while runner.estimate_tokens(msgs) < fill * budget:
        k += 1
        _add_result(msgs, idx, k, rng.randint(1000, 12_000))
        _proactive(msgs, idx, budget)
    return msgs, idx, k


@pytest.mark.parametrize("size", [2_000_000, 200_000])
def test_giant_at_60_percent_leaves_every_older_result(size):
    budget = 85_000
    msgs, idx, k = _grow(budget, 0.60, seed=3)
    before = _live(msgs)
    assert before >= 10
    _add_result(msgs, idx, k + 1, size, fill="G")
    asks = runner.proactive_asks(runner.estimate_tokens(msgs), budget)
    assert asks and asks[1] > asks[0]
    n, _ = runner.compact_messages(msgs, asks[1], idx, must_free=asks[0])
    assert n == 1
    assert msgs[-1]["content"].startswith(runner._STUB_PREFIX)
    assert _live(msgs) == before, "no older result is lost to the low-water gap"
    assert runner.estimate_tokens(msgs) <= int(budget * runner._COMPACT_FRACTION)


def test_giant_sweep_never_costs_an_older_result():
    # The reviewer's sweep, driven through the runner's own asks: a 2M
    # result injected after every turn of five random histories.
    import copy
    budget = 85_000
    lost = total = worst = 0
    for seed in range(5):
        rng = random.Random(seed)
        msgs = [{"role": "system", "content": "s" * 8000}, {"role": "user", "content": "task"}]
        idx: dict = {}
        for k in range(1, 121):
            _add_result(msgs, idx, k, rng.randint(1000, 12_000))
            _proactive(msgs, idx, budget)
            if k < 10:
                continue
            m2, ix2 = copy.deepcopy(msgs), dict(idx)
            before = _live(m2)
            _add_result(m2, ix2, k + 1, 2_000_000, fill="G")
            _proactive(m2, ix2, budget)
            total += 1
            d = before - _live(m2)
            lost += d
            worst = max(worst, d)
            # Whatever the trigger needed, the context is back under it.
            assert runner.estimate_tokens(m2) <= int(budget * runner._COMPACT_FRACTION)
    assert total == 555
    # Only when the history sat within a stub's width of the trigger does
    # the trigger itself need one more result (2 of 555 positions); round 1
    # spent ~8 older results per position on the low-water gap.
    assert worst <= 1, worst
    assert lost <= total // 100, lost


def test_ordinary_result_never_takes_the_solo_branch():
    # Negative control: 12 KB is the largest result but below half the
    # hysteresis gap, so the walk stays oldest-first down to low water — a
    # recent result is not stubbed ahead of older ones for being the biggest.
    msgs, idx = _history([6000] * 10 + [12_000])
    runner.compact_messages(msgs, 29_000, idx, must_free=1000)
    contents = _tool_contents(msgs)
    assert [c.startswith(runner._STUB_PREFIX) for c in contents] == [True] * 5 + [False] * 6


def test_large_result_among_one_liners_is_not_oversized():
    # Gap bar: 8 KB is 25x the median `ls` line but only a sliver of a big
    # budget's gap, so it is not singled out.
    msgs, idx = _history([300] * 200 + [8000])
    runner.compact_messages(msgs, 30_000, idx, must_free=1000)
    contents = _tool_contents(msgs)
    assert contents[-1] == chr(64 + 201) * 8000
    assert contents[0].startswith(runner._STUB_PREFIX)


def test_large_result_among_large_results_is_not_oversized():
    # Median bar: 30 KB clears half the gap but is only 2.5x the typical
    # result, so ordinary oldest-first compaction still applies.
    msgs, idx = _history([12_000] * 10 + [30_000])
    runner.compact_messages(msgs, 40_000, idx, must_free=1000)
    contents = _tool_contents(msgs)
    assert contents[-1] == chr(64 + 11) * 30_000
    assert contents[0].startswith(runner._STUB_PREFIX)


def test_stubbed_results_still_count_toward_the_median():
    # The bar must not sink as compaction thins the live history: 50 stubbed
    # 12 KB results + 20 live 1 KB ones; a 20 KB result is not 4x typical of
    # this run, though it is 20x the results still live.
    msgs, idx = _history([12_000] * 50 + [1000] * 20 + [20_000])
    for m in msgs[2:102]:
        if m["role"] == "tool":
            m["content"] = runner._stub(m["content"], 0)
    runner.compact_messages(msgs, 15_000, idx, must_free=1000)
    contents = _tool_contents(msgs)
    assert contents[50].startswith(runner._STUB_PREFIX)     # oldest live first
    assert contents[-1] == chr(64 + 71) * 20_000


def _older_lost(msgs, before_flags):
    now = [m["content"].startswith(runner._STUB_PREFIX)
           for m in msgs if m["role"] == "tool"][:len(before_flags)]
    return sum(1 for b, n in zip(before_flags, now) if n and not b)


@pytest.mark.parametrize("size", [50_000, 100_000, 150_000, 200_000])
def test_realistic_giant_sweep_spares_older_history(size):
    # Round 3: round 2 only looked for the giant inside the oldest-first
    # plan, which a 50K-200K result (the new fetch cap's band) usually never
    # reaches — 100K cost ~17 older results per position, same as before.
    # Here: whenever stubbing the giant alone gets back under the trigger,
    # not one older result may go.
    import copy
    budget = 85_000
    positions = covered = lost_total = 0
    for seed in range(5):
        rng = random.Random(seed)
        msgs = [{"role": "system", "content": "s" * 8000}, {"role": "user", "content": "task"}]
        idx: dict = {}
        for k in range(1, 121):
            _add_result(msgs, idx, k, rng.randint(1000, 12_000))
            _proactive(msgs, idx, budget)
            if k < 10:
                continue
            m2, ix2 = copy.deepcopy(msgs), dict(idx)
            flags = [m["content"].startswith(runner._STUB_PREFIX)
                     for m in m2 if m["role"] == "tool"]
            _add_result(m2, ix2, k + 1, size, fill="G")
            asks = runner.proactive_asks(runner.estimate_tokens(m2), budget)
            _proactive(m2, ix2, budget)
            positions += 1
            lost = _older_lost(m2, flags)
            lost_total += lost
            if asks and size - 100 >= asks[0]:
                covered += 1
                assert lost == 0, (seed, k, asks)
            if asks:
                assert runner.estimate_tokens(m2) <= int(budget * runner._COMPACT_FRACTION)
    assert positions == 555
    assert covered > 0
    # The reviewer measured 7.1 / 17.0 / 22.8 per position on this sweep.
    assert lost_total <= positions // 100, lost_total


def test_small_budget_giant_does_not_wipe_the_history():
    # The reviewer's second case: budget 32768, 30 x 2000-char history, one
    # 55K result — the old walk stubbed all 30 older results AND the giant.
    msgs, idx = _history([2000] * 30)
    msgs[0]["content"] = "s" * 1500
    _add_result(msgs, idx, 31, 55_000, fill="G")
    asks = runner.proactive_asks(runner.estimate_tokens(msgs), 32_768)
    assert asks
    n, _ = runner.compact_messages(msgs, asks[1], idx, must_free=asks[0])
    assert n == 1
    assert msgs[-1]["content"].startswith(runner._STUB_PREFIX)
    assert all(len(c) == 2000 for c in _tool_contents(msgs)[:-1])


def test_giant_that_cannot_reach_the_trigger_alone_still_walks_on():
    # When the giant alone is NOT back under the trigger, older results must
    # still go (being stuck beats keeping them) — oldest first, but only as
    # many as the trigger needs, not the low-water gap.
    msgs, idx = _history([8000] * 5 + [100_000])
    n, freed = runner.compact_messages(msgs, 140_000, idx, must_free=130_000)
    assert freed >= 130_000
    assert n == 5
    contents = _tool_contents(msgs)
    assert contents[-1].startswith(runner._STUB_PREFIX)
    assert contents[4] == "E" * 8000                   # the newest older one survives
    assert all(c.startswith(runner._STUB_PREFIX) for c in contents[:4])


def test_must_free_none_is_the_old_contract():
    # Overflow path and older callers: the whole ask is required.
    a, ia = _history([8000] * 20 + [2_000_000])
    b, ib = _history([8000] * 20 + [2_000_000])
    assert (runner.compact_messages(a, 2_100_000, ia)
            == runner.compact_messages(b, 2_100_000, ib, must_free=None))
    assert a == b


def test_proactive_asks_shape():
    trig = int(85_000 * 0.70)
    assert runner.proactive_asks(trig, 85_000) is None         # at the trigger
    assert runner.proactive_asks(10, 0) is None
    must, ask = runner.proactive_asks(60_000, 85_000)
    assert must == (60_000 - trig) * 4
    assert ask == (60_000 - 42_500) * 4


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
    for k in ("OPENBEAST_EVAL", "OPENBEAST_AGENT_MAX_TOKENS",
              "OPENBEAST_REASONING_BUDGET", "REASONING_BUDGET"):
        monkeypatch.delenv(k, raising=False)
    # Never the real rig's openbeast.conf; tests that want one write it here.
    monkeypatch.setattr(runner, "_CONF_PATH", tmp_path / "openbeast.conf", raising=False)
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


def test_unlimited_reasoning_budget_means_no_cap(tmp_path, monkeypatch):
    # REASONING_BUDGET=-1 is a documented "unlimited thinking" setting; a
    # fixed 32K cap would cut every long thought off mid-way.
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()],
                      env={"OPENBEAST_REASONING_BUDGET": "-1"})
    assert "max_tokens" not in fake.kwargs[0]


def test_larger_reasoning_budget_raises_the_cap(tmp_path, monkeypatch):
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()],
                      env={"OPENBEAST_REASONING_BUDGET": "65536"})
    assert fake.kwargs[0]["max_tokens"] == 65536 + 12288


def test_reasoning_budget_is_read_from_openbeast_conf(tmp_path, monkeypatch):
    # The runner is spawned without conf.sh sourced, so the conf file is read
    # directly; the LAST assignment wins, as in scripts/lib/conf.sh.
    (tmp_path / "openbeast.conf").write_text(
        "# REASONING_BUDGET=1\nREASONING_BUDGET=4096\nREASONING_BUDGET=\"-1\"\n")
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()])
    assert "max_tokens" not in fake.kwargs[0]
    (tmp_path / "openbeast.conf").write_text("REASONING_BUDGET=4096\n")
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()])
    assert fake.kwargs[0]["max_tokens"] == 4096 + 12288


def test_explicit_agent_cap_beats_the_reasoning_budget(tmp_path, monkeypatch):
    _, fake, _ = _run(tmp_path, monkeypatch, [_done()],
                      env={"OPENBEAST_REASONING_BUDGET": "-1",
                           "OPENBEAST_AGENT_MAX_TOKENS": "8000"})
    assert fake.kwargs[0]["max_tokens"] == 8000


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
