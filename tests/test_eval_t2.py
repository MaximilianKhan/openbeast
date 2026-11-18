"""Arm T2 and the harness9 changes (2026-11-18, DeepSeek-V4.1-Flash (Mia EXL3) arm T).

Pinned here:

  1. The per-turn cap: OPENBEAST_AGENT_MAX_TOKENS=8192 is the max_tokens of
     every eval request, keyed (`cap8192`) and stamped on rows; a capped
     think in DeepSeek's shape (reasoning_content in model_extra, content
     None) still hands its tail back (_capped_think_nudge).
  2. The act nudge (OPENBEAST_AGENT_ACT_NUDGE=1): from the second capped
     no-tool-call turn in a row, and from the first stop with nothing in it;
     never on a final answer, never after a tool call reset the streak, and
     never with the switch off (byte-identical to before).
  3. Partial stats: the runner rewrites a sidecar every turn; run_eval reads
     it when the wall kills the agent, marks the row `stats_partial`, and a
     killed row is still a wall timeout, never a turn-cap ending.
  4. The eval fetch guard: the project's own repo on any host, any case,
     percent-encoded, through a redirect, and URL paths into evals/ answer
     trees, are refused under eval; nothing changes outside eval. The
     integrity scan flags the same URLs (`selfrepo`), with the same patterns.
  5. The campaign arm: T2 = T + the cap + the nudge, at T's jobs (4).
"""
import importlib
import json
import sys
import textwrap
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "evals"))

import runner  # noqa: E402
import tools  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("OPENBEAST_AGENT_ACT_NUDGE", "OPENBEAST_AGENT_MAX_TOKENS", "OPENBEAST_AGENT_STATS_FILE",
              "OPENBEAST_TASK_PATHS", "OPENBEAST_AGENT_WALL_WARN", "OPENBEAST_AGENT_ENV_BLOCK",
              "OPENBEAST_EVAL_WEB"):
        monkeypatch.delenv(k, raising=False)
    tools.reset_plan()
    yield
    tools.reset_plan()


class _Client:
    def __init__(self, script):
        self.script = list(script)
        self.calls, self.kwargs = [], []
        self.chat = self
        self.completions = self

    def create(self, model, messages, tools, temperature, **kw):
        self.calls.append([dict(m) for m in messages])
        self.kwargs.append(kw)
        return self.script.pop(0)


def _usage(completion):
    return type("U", (), {"prompt_tokens": 100, "completion_tokens": completion,
                          "total_tokens": 100 + completion})()


def _tool(name, args, id_="1"):
    fn = type("F", (), {"name": name, "arguments": json.dumps(args)})()
    tc = type("T", (), {"id": id_, "function": fn})()
    msg = type("M", (), {"content": "", "tool_calls": [tc]})()
    return type("R", (), {"choices": [type("C", (), {"message": msg, "finish_reason": "tool_calls"})()],
                          "usage": _usage(50), "model": "DeepSeek-V4.1-Flash-1017"})()


def _ds_capped(reasoning="step " * 3000, completion=8192):
    """A DeepSeek turn cut off mid-think: content None, no tool call, the
    reasoning only in model_extra (the openai SDK's unknown-field store)."""
    msg = type("M", (), {"content": None, "tool_calls": None,
                         "model_extra": {"reasoning_content": reasoning}})()
    return type("R", (), {"choices": [type("C", (), {"message": msg, "finish_reason": "length"})()],
                          "usage": _usage(completion), "model": "DeepSeek-V4.1-Flash-1017"})()


def _stop(content):
    msg = type("M", (), {"content": content, "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": msg, "finish_reason": "stop"})()],
                          "usage": _usage(20), "model": "DeepSeek-V4.1-Flash-1017"})()


def _run(tmp_path, monkeypatch, script, max_iter=6):
    client = _Client(script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    log = tmp_path / "run.jsonl"
    runner.run_agent("task", max_iter=max_iter, log_file=str(log), system_prompt="s",
                     workdir=str(tmp_path))
    return client, [json.loads(x) for x in log.read_text().splitlines()]


def _eval_thinking(monkeypatch, cap="8192"):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "on")
    if cap is not None:
        monkeypatch.setenv("OPENBEAST_AGENT_MAX_TOKENS", cap)


def _user_after(call):
    """The user messages after the task (the nudges), in order."""
    return [m["content"] for m in call[2:] if m.get("role") == "user"]


# --- 1. the 8192 cap and DeepSeek's capped think ---------------------------------------------


def test_the_8192_cap_is_sent_and_a_deepseek_capped_think_keeps_its_tail(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    reasoning = "early plan\n" + "x" * 9000 + "\nLAST-LINE: write rx.py with a Pike VM"
    client, events = _run(tmp_path, monkeypatch,
                          [_ds_capped(reasoning), _tool("task_done", {"summary": "ok"})], 3)
    assert all(kw["max_tokens"] == 8192 for kw in client.kwargs)
    nudge = _user_after(client.calls[1])[-1]
    assert "8,192-token output cap while you were still thinking" in nudge
    assert "LAST-LINE: write rx.py with a Pike VM" in nudge and "early plan" not in nudge
    cap = next(e for e in events if e["type"] == "output_cap")
    assert cap["max_tokens"] == 8192 and cap["reasoning_chars"] == len(reasoning)


def test_the_eval_default_cap_is_unchanged_without_the_switch(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch, cap=None)
    client, _ = _run(tmp_path, monkeypatch, [_tool("task_done", {"summary": "ok"})], 1)
    assert client.kwargs[0]["max_tokens"] == runner._EVAL_TURN_TOKENS_THINKING == 16384


# --- 2. the act nudge ----------------------------------------------------------------------------


def test_act_nudge_due_rules():
    due = runner.act_nudge_due
    assert not due("stop", "All done: rx.py written and tested.", 1)   # a final answer
    assert not due("stop", "All done.", 5)
    assert not due("length", None, 1)            # the first cap: the recovery's
    assert due("length", None, 2) and due("length", "", 3)
    assert due("stop", "", 1) and due("stop", None, 1)    # stopped with nothing
    assert due(None, None, 1) and due("content_filter", "", 1)


def test_act_nudge_fires_from_the_second_idle_capped_turn_192_shape(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    monkeypatch.setenv("OPENBEAST_AGENT_ACT_NUDGE", "1")
    client, events = _run(tmp_path, monkeypatch,
                          [_ds_capped(), _ds_capped(), _ds_capped(),
                           _tool("write_file", {"path": str(tmp_path / "rx.py"), "content": "x"}),
                           _ds_capped(), _tool("task_done", {"summary": "ok"})], 6)
    # turn 1 capped: the recovery nudge only
    after1 = _user_after(client.calls[1])
    assert len(after1) == 1 and "[act now]" not in after1[0]
    # turn 2 capped again: ONE user message, the recovery + the act nudge
    after2 = _user_after(client.calls[2])
    assert len(after2) == 2 and "while you were still thinking" in after2[1]
    assert "[act now] 2 turn(s) in a row (~16,384 tokens)" in after2[1]
    assert "Your next reply must be a tool call" in after2[1]
    assert "[act now] 3 turn(s)" in _user_after(client.calls[3])[-1]
    # a tool call resets the streak: the next capped turn is recovery only again
    assert "[act now]" not in _user_after(client.calls[5])[-1]
    nudges = [e for e in events if e["type"] == "act_nudge"]
    assert [e["idle_turns"] for e in nudges] == [2, 3]
    assert nudges[0]["finish_reason"] == "length"


def test_act_nudge_never_fires_on_a_final_answer_and_answers_an_empty_stop(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    monkeypatch.setenv("OPENBEAST_AGENT_ACT_NUDGE", "1")
    client, events = _run(tmp_path, monkeypatch,
                          [_stop("Done. The file is written."), _stop(""),
                           _tool("task_done", {"summary": "ok"})], 3)
    first = _user_after(client.calls[1])
    assert len(first) == 1 and "[act now]" not in first[0]
    assert first[0].startswith("If the task is complete, call the task_done tool with a summary.")
    second = _user_after(client.calls[2])[-1]
    assert second.startswith("[act now] 2 turn(s)") and "call task_done" in second
    assert [e["finish_reason"] for e in events if e["type"] == "act_nudge"] == ["stop"]


def test_act_nudge_off_is_the_old_behaviour(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    client, events = _run(tmp_path, monkeypatch,
                          [_ds_capped(), _ds_capped(), _stop(""),
                           _tool("task_done", {"summary": "ok"})], 4)
    every = json.dumps(client.calls)
    assert "[act now]" not in every and not [e for e in events if e["type"] == "act_nudge"]
    # an empty stop still gets the old task_done nudge
    assert _user_after(client.calls[3])[-1].startswith("If the task is complete")


# --- 3. partial stats on a wall kill --------------------------------------------------------------


def test_the_runner_writes_its_counters_to_the_sidecar_every_turn(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    side = tmp_path / "stats.json"
    monkeypatch.setenv("OPENBEAST_AGENT_STATS_FILE", str(side))
    seen = []
    orig = runner.write_stats_sidecar

    def spy(stats):
        orig(stats)
        seen.append(json.loads(side.read_text()))
    monkeypatch.setattr(runner, "write_stats_sidecar", spy)
    _run(tmp_path, monkeypatch, [_ds_capped(completion=8192), _ds_capped(completion=8192),
                                 _tool("task_done", {"summary": "ok"})], 3)
    # turn start (iteration N) then after the response (tokens), per turn
    assert [s["iterations"] for s in seen] == [1, 1, 2, 2, 3, 3]
    assert seen[1]["tokens_completion"] == 8192 and seen[3]["tokens_completion"] == 16384
    assert seen[3]["reasoning_turns"] == 2 and seen[3]["tokens_total"] == 16584
    assert seen[-1]["response_models"] == {"DeepSeek-V4.1-Flash-1017": 3}


def test_no_sidecar_without_the_env(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    _run(tmp_path, monkeypatch, [_tool("task_done", {"summary": "ok"})], 1)
    assert not list(tmp_path.glob("*.tmp"))


FAKE_RUNNER = textwrap.dedent('''
    import json, os, time
    p = os.environ["OPENBEAST_AGENT_STATS_FILE"]
    json.dump({"tokens_prompt": 900, "tokens_completion": 41000, "tokens_total": 41900,
               "iterations": 7, "reasoning_turns": 6, "compactions": 1}, open(p, "w"))
    print("[iter 7/60]", flush=True)
    time.sleep(30)   # the wall kills it
''')


def _run_eval(tmp_path, body=FAKE_RUNNER):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    run_eval = importlib.import_module("run_eval")
    fake = tmp_path / "fake_runner.py"
    fake.write_text(body)
    run_eval.RUNNER_PATH = str(fake)
    return run_eval


def test_a_wall_killed_agent_keeps_its_partial_stats(tmp_path, monkeypatch):
    import tempfile
    tdir = tmp_path / "tmp"
    tdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tdir))
    run_eval = _run_eval(tmp_path)
    res = run_eval.run_agent({"task": "t", "max_iter": 1}, "http://127.0.0.1:9/v1",
                             timeout_scale=1.5 / 60)            # a 1 s wall
    assert res["exit_code"] == -1 and res["stats_partial"] is True
    assert res["tokens"] == {"prompt": 900, "completion": 41000, "total": 41900}
    assert res["iterations"] == 7 and res["reasoning_turns"] == 6 and res["compactions"] == 1
    assert not list(tdir.glob("ob-agent-stats-*"))            # the sidecar is removed


def test_a_normal_exit_ignores_the_sidecar(tmp_path):
    run_eval = _run_eval(tmp_path, textwrap.dedent('''
        import json, os
        json.dump({"tokens_total": 999, "iterations": 9}, open(os.environ["OPENBEAST_AGENT_STATS_FILE"], "w"))
        print("[iter 2/5]")
        print("TOKENS: prompt=1 completion=2 total=3")
    '''))
    res = run_eval.run_agent({"task": "t", "max_iter": 5}, "http://127.0.0.1:9/v1")
    assert res["exit_code"] == 0 and "stats_partial" not in res
    assert res["tokens"] == {"prompt": 1, "completion": 2, "total": 3} and res["iterations"] == 2


def test_partial_stats_parse_is_defensive(tmp_path):
    run_eval = _run_eval(tmp_path)
    p = tmp_path / "s.json"
    assert run_eval._partial_stats(str(p), {"total": 0}) == {}            # missing
    p.write_text("{not json")
    assert run_eval._partial_stats(str(p), {"total": 0}) == {}
    p.write_text(json.dumps({"tokens_total": -1, "iterations": "7", "reasoning_turns": True}))
    assert run_eval._partial_stats(str(p), {"total": 0}) == {"stats_partial": True}
    p.write_text(json.dumps({"tokens_total": 10, "tokens_completion": 4, "iterations": 2}))
    assert run_eval._partial_stats(str(p), {"total": 0}) == {
        "stats_partial": True, "tokens": {"prompt": 0, "completion": 4, "total": 10}, "iterations": 2}
    # the flushed stdout already had a TOKENS line: that one stands
    assert run_eval._partial_stats(str(p), {"total": 5}) == {}


def test_a_killed_row_with_turns_is_a_wall_timeout_not_a_turn_cap_ending():
    import campaign as C
    rows = {"a": {"id": "a", "passed": False, "agent_exit_code": -1, "iterations": 60,
                  "max_iter": 60, "elapsed_seconds": 3600.0, "tokens_completion": 41000,
                  "stats_partial": True},
            "b": {"id": "b", "passed": False, "agent_exit_code": 0, "iterations": 60,
                  "max_iter": 60, "elapsed_seconds": 2000.0, "tokens_completion": 9000}}
    s = C.score(rows)
    assert s["turn_cap_ends"] == 1 and s["wall_timeouts"] == 1 and s["scorable"] == 2
    assert C.infra_reason(rows["a"]) is None           # still a verdict


def test_agent_switches_key_the_cap_and_the_nudge_only_when_set(tmp_path):
    run_eval = _run_eval(tmp_path)
    t = {"OPENBEAST_AGENT_THINKING": "on", "OPENBEAST_AGENT_VERIFY_DONE": "1",
         "OPENBEAST_DIAG_EDIT_HINT": "1", "OPENBEAST_AGENT_ENV_BLOCK": "1",
         "OPENBEAST_AGENT_WALL_WARN": "1"}
    comp, meta = run_eval.agent_switches(t)
    assert comp == "ag-think-on-vdone-ehint-envb-wwarn"        # T's key: unchanged
    assert "turn_cap" not in meta and "act_nudge" not in meta
    comp, meta = run_eval.agent_switches({**t, "OPENBEAST_AGENT_MAX_TOKENS": "8192",
                                          "OPENBEAST_AGENT_ACT_NUDGE": "1"})
    assert comp == "ag-think-on-vdone-ehint-envb-wwarn-cap8192-anudge"
    assert meta["turn_cap"] == 8192 and meta["act_nudge"] is True
    for junk in ("", "lots", " "):
        assert "turn_cap" not in run_eval.agent_switches({"OPENBEAST_AGENT_MAX_TOKENS": junk})[1]
    assert run_eval.agent_switches({"OPENBEAST_AGENT_ACT_NUDGE": "0"}) == (None, run_eval.agent_switches({})[1])


# --- 4. the eval fetch guard --------------------------------------------------------------------

OWN = [
    "https://github.com/MaximilianKhan/openbeast",
    "https://github.com/maximiliankhan/OPENBEAST/tree/main/evals/refs",
    "https://raw.githubusercontent.com/MaximilianKhan/openbeast/main/evals/refs/192_pike_regex/rx.py",
    "https://api.github.com/repos/MaximilianKhan/openbeast/contents/evals/refs",
    "https://codeload.github.com/MaximilianKhan/openbeast/zip/refs/heads/main",
    "https://cdn.jsdelivr.net/gh/MaximilianKhan/openbeast@main/evals/refs/x.py",
    "https://github.com/MaximilianKhan%2Fopenbeast",
    "https://example.com/?u=https%253A%252F%252Fgithub.com%252FMaximilianKhan%252Fopenbeast",
    "git@github.com:MaximilianKhan/openbeast.git",
    "https://mirror.example.org/some/fork/evals/refs/192_pike_regex/rx.py",
    "https://example.org/x/EVALS/Tasks/192.json",
]
FINE = [
    "https://raw.githubusercontent.com/python/cpython/3.12/Lib/asyncio/taskgroups.py",
    "https://github.com/MaximilianKhan/openbeast-website",
    "https://github.com/someone/openbeastly",
    "https://docs.python.org/3/library/re.html",
    "https://github.com/openai/evals/blob/main/README.md",
]


@pytest.mark.parametrize("url", OWN)
def test_eval_fetch_refuses_the_projects_own_repo_and_evals_paths(monkeypatch, url):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_EVAL_WEB", "record")         # the web on: the guard still holds
    assert tools._eval_url_refusal(url)
    monkeypatch.setattr(tools, "_fetch_body", lambda *a, **k: pytest.fail("fetched"))
    monkeypatch.setattr(tools, "_fetch_url_blocked", lambda u: pytest.fail("resolved"))
    out = tools.fetch(url)
    assert out.startswith("Error: refusing to fetch") and "off limits" in out


@pytest.mark.parametrize("url", FINE)
def test_eval_fetch_guard_lets_other_urls_through(monkeypatch, url):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    assert tools._eval_url_refusal(url) is None


def test_the_guard_is_eval_only_and_the_web_gate_still_comes_first(monkeypatch):
    monkeypatch.delenv("OPENBEAST_EVAL", raising=False)
    assert tools._eval_url_refusal(OWN[0]) is None
    monkeypatch.setenv("OPENBEAST_EVAL", "1")                  # web off (the default)
    assert "fetch is disabled while an eval unit is measured" in tools.fetch(OWN[0])


def test_a_redirect_into_the_repo_is_refused_under_eval(monkeypatch):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setattr(tools, "_fetch_check_deadline", lambda: None)
    h = tools._FetchRedirectHandler()
    with pytest.raises(urllib.error.URLError, match="refusing redirect"):
        h.redirect_request(None, None, 302, "Found", {},
                           "https://raw.githubusercontent.com/MaximilianKhan/openbeast/main/x")


def test_web_search_refuses_a_query_for_the_repo_under_eval(monkeypatch):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_EVAL_WEB", "record")
    monkeypatch.setattr(tools.urllib.request, "urlopen", lambda *a, **k: pytest.fail("searched"))
    assert tools.web_search("MaximilianKhan/openbeast evals refs pike").startswith("Error: refusing")


def _audit_scan():
    # by path: evals/reference/integrity_scan.py shares the module name
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_audit_integrity_scan_t2", ROOT / "evals" / "audit" / "integrity_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_integrity_scan_flags_the_same_urls_with_the_same_patterns():
    S = _audit_scan()
    assert S._SELF_REPO.pattern == tools._EVAL_SELF_REPO_RE.pattern
    assert S._URL_EVALS.pattern == tools._EVAL_URL_EVALS_RE.pattern
    for url in OWN[:8]:
        kinds, _ = S.call_kinds("fetch", {"url": url})
        assert kinds == {"web", "selfrepo"}, url
    kinds, _ = S.call_kinds("fetch", {"url": FINE[0]})
    assert kinds == {"web"}
    kinds, ev = S.call_kinds("bash", {"command": "curl -sL https://raw.githubusercontent.com/"
                                                 "MaximilianKhan/openbeast/main/evals/refs/a.py"})
    assert {"network", "selfrepo"} <= kinds and any("openbeast" in e for e in ev)
    # a read is contamination; a refused one is an attempt
    hit = {"status": "read", "kinds": ["selfrepo", "web"]}
    assert S.contaminated([hit]) and not S.contaminated([{**hit, "status": "refused"}])
    assert S.refused("Error: refusing to fetch https://github.com/MaximilianKhan/openbeast: x")
    # the web gate's own refusal is an attempt too (it read as "read" before)
    gate = ("Error: fetch is disabled while an eval unit is measured (OPENBEAST_EVAL=1); "
            "solve the task with local tools")
    assert S.refused(gate) and S.result_kinds(gate) == (set(), [])
    assert S.refused("Error: web_search is disabled while an eval unit is measured (x)")


# --- 5. the campaign arm --------------------------------------------------------------------------


def test_t2_is_t_plus_the_cap_and_the_nudge_at_ts_jobs():
    import campaign as C
    camp = C.load_campaign("v5-thinking-first")
    t, t2 = C.resolve_arm(camp, "T"), C.resolve_arm(camp, "T2")
    assert C.arg_value(t2["args"], "--reasoning-budget") == "8192"
    assert [x for x in t2["args"] if x not in ("8192", "16384")] == \
        [x for x in t["args"] if x not in ("8192", "16384")]
    changed = {k: t2["env"][k] for k in t2["env"] if t2["env"][k] != t["env"].get(k)}
    assert changed == {"OPENBEAST_AGENT_MAX_TOKENS": "8192", "OPENBEAST_AGENT_ACT_NUDGE": "1"}
    assert t["env"]["OPENBEAST_AGENT_MAX_TOKENS"] == "" and t["env"]["OPENBEAST_AGENT_ACT_NUDGE"] == "0"
    assert (t2["jobs"], t2["slots"]) == (4, 4)        # T's concurrency: same wall and tokens per unit as T
    assert t2["thinking"] == "on" and t2["wall_scale"] == 2.0
    assert "OPENBEAST_AGENT_ACT_NUDGE" in t2["requires"]["env"]
    spec = C.arm_spec(camp, "T2")
    assert spec["optional"] is True and "--jobs 4" in spec["note"] and "T2" not in C.run_order(camp)
    assert C.missing_requirements(str(ROOT), sys.executable, {"env": t2["requires"]["env"]}) == []
