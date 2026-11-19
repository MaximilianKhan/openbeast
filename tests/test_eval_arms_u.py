"""harness10 and the U arms (2026-11-19, DeepSeek-V4.1-Flash (Mia EXL3), the
168_linearizability_kv investigation).

Pinned here:

  1. The forced-act turn (OPENBEAST_AGENT_FORCE_ACT=1): after N (default 2)
     capped thinking turns in a row with no tool call, the NEXT request has
     enable_thinking=false and asks for decisions / rejected options, then a
     tool call; thinking is back on the turn after; events are logged; off,
     every request is as before.
  2. "Done when verified" (OPENBEAST_AGENT_DONE_VERIFIED=1): one prompt line,
     naming G9's re-run check when VERIFY_DONE is on.
  3. The planning lines (OPENBEAST_AGENT_ALGO_LINES=1).
  4. The algorithmic pack (OPENBEAST_ALGO_PACK=1): frozen bytes (the sha is
     pinned here), no noun from a v5 unit, appended to every unit's pack.
  5. The web tools are not advertised under eval with OPENBEAST_AGENT_HIDE_WEB=1.
  6. The reasoning text is logged (size-capped) and the integrity scan still
     reads the transcript.
  7. The keys and the campaign arms U, U_8k, UP, UPA.
"""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "evals"))

import runner  # noqa: E402
import tools  # noqa: E402

SWITCHES = ("OPENBEAST_AGENT_FORCE_ACT", "OPENBEAST_AGENT_FORCE_ACT_AFTER",
            "OPENBEAST_AGENT_DONE_VERIFIED", "OPENBEAST_AGENT_ALGO_LINES",
            "OPENBEAST_AGENT_HIDE_WEB", "OPENBEAST_ALGO_PACK",
            "OPENBEAST_AGENT_REASONING_LOG_CHARS", "OPENBEAST_AGENT_ACT_NUDGE",
            "OPENBEAST_AGENT_MAX_TOKENS", "OPENBEAST_AGENT_VERIFY_DONE", "OPENBEAST_EVAL_WEB",
            "OPENBEAST_AGENT_STATS_FILE", "OPENBEAST_TASK_PATHS", "OPENBEAST_AGENT_SEED",
            "OPENBEAST_AGENT_ENV_BLOCK", "OPENBEAST_AGENT_WALL_WARN")

#: The frozen algorithmic pack (agents/packs/algo-v1.md). Never edit this pin
#: to make a test pass: an edited procedure is a new file with a new pin.
ALGO_SHA256 = "6c5324007750c44091a03c98cfef813cac0f3e576f982beb8908c0732e2ac09d"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)
    tools.reset_plan()
    yield
    tools.reset_plan()


class _Client:
    def __init__(self, script):
        self.script = list(script)
        self.calls, self.kwargs, self.tools = [], [], []
        self.chat = self
        self.completions = self

    def create(self, model, messages, tools, temperature, **kw):
        self.calls.append([dict(m) for m in messages])
        self.kwargs.append(kw)
        self.tools.append([t["function"]["name"] for t in tools])
        return self.script.pop(0)


def _usage(completion):
    return type("U", (), {"prompt_tokens": 100, "completion_tokens": completion,
                          "total_tokens": 100 + completion})()


def _resp(msg, finish, completion=50):
    return type("R", (), {"choices": [type("C", (), {"message": msg, "finish_reason": finish})()],
                          "usage": _usage(completion), "model": "DeepSeek-V4.1-Flash-1017"})()


def _tool(name, args, content="", id_="1"):
    fn = type("F", (), {"name": name, "arguments": json.dumps(args)})()
    tc = type("T", (), {"id": id_, "function": fn})()
    return _resp(type("M", (), {"content": content, "tool_calls": [tc]})(), "tool_calls")


def _ds_capped(reasoning="global memo search " * 2000, completion=16384):
    """DeepSeek's capped think: content None, reasoning in model_extra."""
    msg = type("M", (), {"content": None, "tool_calls": None,
                         "model_extra": {"reasoning_content": reasoning}})()
    return _resp(msg, "length", completion)


def _stop(content):
    return _resp(type("M", (), {"content": content, "tool_calls": None})(), "stop", 20)


def _run(tmp_path, monkeypatch, script, max_iter=8, system_prompt="s"):
    client = _Client(script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    log = tmp_path / "run.jsonl"
    runner.run_agent("task", max_iter=max_iter, log_file=str(log), system_prompt=system_prompt,
                     workdir=str(tmp_path))
    return client, [json.loads(x) for x in log.read_text().splitlines()]


def _eval_thinking(monkeypatch, cap="16384"):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "on")
    if cap is not None:
        monkeypatch.setenv("OPENBEAST_AGENT_MAX_TOKENS", cap)


def _thinking(kw):
    return ((kw.get("extra_body") or {}).get("chat_template_kwargs") or {}).get("enable_thinking")


def _user_after(call):
    return [m["content"] for m in call[2:] if m.get("role") == "user"]


# --- 1. the forced-act turn ------------------------------------------------------------------------


def test_force_act_after_parsing(monkeypatch):
    assert runner.force_act_after() == 2
    for raw, want in (("1", 1), ("3", 3), ("0", 2), ("-1", 2), ("x", 2), (" 4 ", 4)):
        monkeypatch.setenv("OPENBEAST_AGENT_FORCE_ACT_AFTER", raw)
        assert runner.force_act_after() == want, raw


def test_no_think_kwargs_keeps_the_seed_and_other_fields():
    base = {"extra_body": {"chat_template_kwargs": {"enable_thinking": True, "x": 1}, "y": 2},
            "seed": 7}
    kw = runner._no_think_kwargs(base)
    assert kw == {"extra_body": {"chat_template_kwargs": {"enable_thinking": False, "x": 1},
                                 "y": 2}, "seed": 7}
    assert base["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True   # not mutated
    assert runner._no_think_kwargs({}) == {"extra_body": {"chat_template_kwargs":
                                                          {"enable_thinking": False}}}


def test_two_capped_thinks_force_one_thinking_off_turn_then_thinking_resumes(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    monkeypatch.setenv("OPENBEAST_AGENT_FORCE_ACT", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_SEED", "3")
    dest = tmp_path / "kv.py"
    tail = "\nLAST: decompose by key? rejected: cross-key ordering"
    client, events = _run(tmp_path, monkeypatch, [
        _ds_capped(), _ds_capped("plan " * 3000 + tail),
        _tool("write_file", {"path": str(dest), "content": "x"},
              content="Decisions so far: per-key search\nRejected options: none"),
        _ds_capped(), _tool("task_done", {"summary": "ok"})])
    assert [_thinking(kw) for kw in client.kwargs] == [True, True, False, True, True]
    assert all(kw["seed"] == 3 and kw["max_tokens"] == 16384 for kw in client.kwargs)
    # the forced request: ONE user turn, the recovery's tail + the act-turn note
    forced = _user_after(client.calls[2])[-1]
    assert "while you were still thinking" in forced and "LAST: decompose by key?" in forced
    assert "[act turn] 2 replies in a row" in forced and "thinking OFF" in forced
    assert "Decisions so far" in forced and "Rejected options" in forced
    # the forced reply's decisions stay in the history as content
    assert any(m.get("role") == "assistant" and "Decisions so far: per-key search"
               in (m.get("content") or "") for m in client.calls[3])
    # one capped turn after it: the streak restarted, no new force
    assert "[act turn]" not in _user_after(client.calls[4])[-1]
    fa = [e for e in events if e["type"] == "force_act"]
    assert len(fa) == 1 and fa[0]["capped_streak"] == 2 and fa[0]["after"] == 2
    res = [e for e in events if e["type"] == "force_act_result"]
    assert len(res) == 1 and res[0]["tool_calls"] == 1 and res[0]["iteration"] == 3
    assert res[0]["content_chars"] > 0 and res[0]["finish_reason"] == "tool_calls"


def test_a_tool_call_resets_the_streak_and_n_is_configurable(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    monkeypatch.setenv("OPENBEAST_AGENT_FORCE_ACT", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_FORCE_ACT_AFTER", "3")
    client, events = _run(tmp_path, monkeypatch, [
        _ds_capped(), _ds_capped(), _tool("bash", {"command": "true"}),
        _ds_capped(), _ds_capped(), _ds_capped(), _tool("task_done", {"summary": "ok"})])
    assert [_thinking(kw) for kw in client.kwargs] == [True] * 6 + [False]
    assert [e["iteration"] for e in events if e["type"] == "force_act"] == [6]


def test_a_forced_turn_that_does_not_act_does_not_count_and_thinking_resumes(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    monkeypatch.setenv("OPENBEAST_AGENT_FORCE_ACT", "1")
    client, events = _run(tmp_path, monkeypatch, [
        _ds_capped(), _ds_capped(), _stop("Decisions so far: x"),
        _ds_capped(), _ds_capped(), _tool("task_done", {"summary": "ok"})])
    assert [_thinking(kw) for kw in client.kwargs] == [True, True, False, True, True, False]
    assert len([e for e in events if e["type"] == "force_act"]) == 2


def test_force_act_replaces_the_act_nudge_when_both_are_on(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    monkeypatch.setenv("OPENBEAST_AGENT_FORCE_ACT", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_ACT_NUDGE", "1")
    client, events = _run(tmp_path, monkeypatch, [
        _ds_capped(), _ds_capped(), _tool("task_done", {"summary": "ok"})])
    forced = _user_after(client.calls[2])[-1]
    assert "[act turn]" in forced and "[act now]" not in forced
    assert not [e for e in events if e["type"] == "act_nudge"]


def test_force_act_off_sends_exactly_what_harness9_sent(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    script = [_ds_capped(), _ds_capped(), _ds_capped(), _tool("task_done", {"summary": "ok"})]
    client, events = _run(tmp_path, monkeypatch, script)
    assert [_thinking(kw) for kw in client.kwargs] == [True] * 4
    assert "[act turn]" not in json.dumps(client.calls)
    assert not [e for e in events if e["type"].startswith("force_act")]


def test_force_act_works_outside_eval_too(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_FORCE_ACT", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_MAX_TOKENS", "8192")
    client, _ = _run(tmp_path, monkeypatch, [
        _ds_capped(completion=8192), _ds_capped(completion=8192),
        _tool("task_done", {"summary": "ok"})])
    assert _thinking(client.kwargs[2]) is False


# --- 2/3. the prompt lines -------------------------------------------------------------------------


def test_prompt_is_unchanged_with_every_switch_off(monkeypatch):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    p = runner.build_system_prompt(max_iter=20)
    assert runner._AGENT_INSTRUCTIONS in p
    assert "Done when verified" not in p and "Planning:" not in p


def test_done_when_verified_line_and_its_g9_mention(monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_DONE_VERIFIED", "1")
    p = runner.build_system_prompt()
    assert "Done when verified: once your own checks pass" in p
    assert "brute force on small inputs" in p and "timing run at the stated input" in p
    assert "Do not keep polishing" in p and "asks you once to re-run" not in p
    monkeypatch.setenv("OPENBEAST_AGENT_VERIFY_DONE", "1")
    assert "task_done asks you once to re-run it" in runner.build_system_prompt()


def test_planning_lines(monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_ALGO_LINES", "1")
    p = runner.build_system_prompt()
    assert "Bound first: compute the worst-case state count" in p
    assert "check it against a brute force on many small random inputs before rejecting it" in p
    assert "misses the time limit by more than 5x, the algorithm is wrong" in p


# --- 4. the algorithmic pack -----------------------------------------------------------------------


def _algo_bytes():
    from lang import pack_context as PC
    return PC, (ROOT / "agents" / "packs" / PC.ALGO_PACK_FILE).read_bytes()


def test_the_algorithmic_pack_is_frozen():
    PC, data = _algo_bytes()
    assert PC.ALGO_PACK_FILE == "algo-v1.md"
    assert hashlib.sha256(data).hexdigest() == ALGO_SHA256 == PC.ALGO_PACK_SHA256
    assert PC.algo_pack_problem(data) is None
    assert "frozen" in PC.algo_pack_problem(data + b" ")
    assert ALGO_SHA256 in (ROOT / "evals" / "CHANGELOG.md").read_text()


def test_the_algorithmic_pack_names_no_v5_unit_and_is_about_400_tokens():
    _, data = _algo_bytes()
    text = data.decode().lower()
    suite = json.loads((ROOT / "evals" / "suites" / "v5.json").read_text())
    nouns = {w.lower() for u in suite["units"] for w in u["id"].split("_")[1:]
             if len(w) >= 3 and not w.isdigit()}
    # "state" (49_markov_steady_state) is the brief's own wording: a search
    # "memoised on the minimal state that determines the future".
    hits = (nouns & set(re.findall(r"[a-z0-9]+", text))) - {"state"}
    assert not hits, hits
    assert 300 <= len(data) / 4 <= 520             # ~400 tokens at 4 chars/token


def _packs_flag(monkeypatch, tmp_path, env):
    import run_eval
    monkeypatch.setattr(run_eval, "HOST_PACK_DIR", str(tmp_path / "hp"))
    return run_eval, run_eval.packs_flag(env)


def test_algo_pack_rides_every_unit_and_moves_only_its_own_component(tmp_path, monkeypatch):
    _, data = _algo_bytes()
    base = {"BEAST_PACKS": "1", "OPENBEAST_PACK_VARIANT": "v3"}
    R, (on0, comp0, meta0) = _packs_flag(monkeypatch, tmp_path, base)
    _, (on1, comp1, meta1) = _packs_flag(monkeypatch, tmp_path, {**base, "OPENBEAST_ALGO_PACK": "1"})
    assert on0 and on1 and comp0 != comp1 and "algo" not in meta0["sha"]
    assert meta1["sha"]["algo"] == ALGO_SHA256[:8]
    for k in meta0["sha"]:
        assert meta0["sha"][k] == meta1["sha"][k]
    # every unit's file ends with the pack: a zig unit and any other language
    zig = Path(meta1["unit_paths"]["zig"]).read_bytes()
    assert zig.endswith(data) and zig.startswith(Path(meta0["paths"]["zig"]).read_bytes()[:200])
    assert Path(meta1["common_path"]).read_bytes().endswith(data)
    ctx = R.ArmCtx.__new__(R.ArmCtx)
    ctx.packs_on, ctx.packs_meta = True, meta1
    assert ctx.pack_path({"language": "python"}) == meta1["common_path"]
    assert ctx.pack_path({"language": "zig"}) == meta1["unit_paths"]["zig"]
    ctx.packs_meta = meta0
    assert ctx.pack_path({"language": "python"}) == (meta0.get("host") or {}).get("path")


def test_algo_pack_without_packs_or_with_edited_bytes_stops_the_run(tmp_path, monkeypatch):
    R, _ = _packs_flag(monkeypatch, tmp_path, {})
    with pytest.raises(SystemExit, match="needs packs on"):
        R.packs_flag({"OPENBEAST_ALGO_PACK": "1"})
    assert R.packs_flag({"OPENBEAST_ALGO_PACK": "0"}) == (False, None, {})
    from lang import pack_context as PC
    monkeypatch.setattr(PC, "ALGO_PACK_SHA256", "0" * 64)
    with pytest.raises(SystemExit, match="frozen"):
        R.packs_flag({"BEAST_PACKS": "1", "OPENBEAST_ALGO_PACK": "1"})


# --- 5. the web tools under eval -------------------------------------------------------------------


def test_web_tools_hidden_only_under_eval_with_the_switch_and_web_off(monkeypatch):
    assert not tools.web_tools_hidden()
    monkeypatch.setenv("OPENBEAST_AGENT_HIDE_WEB", "1")
    assert not tools.web_tools_hidden()                      # not eval
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    assert tools.web_tools_hidden()
    names = [s["function"]["name"] for s in tools.visible_tool_schemas()]
    assert "fetch" not in names and "web_search" not in names and "task_done" in names
    p = runner.build_system_prompt()
    assert "web_search" not in p and "fetch" not in p
    assert "If stuck, test a smaller case or re-read the error" in p
    monkeypatch.setenv("OPENBEAST_EVAL_WEB", "record")       # web on: advertised again
    assert not tools.web_tools_hidden() and tools.visible_tool_schemas() is tools.TOOL_SCHEMAS
    assert "web_search" in runner.build_system_prompt()


def test_every_web_prompt_cut_matches_the_instructions():
    for old, _new in runner._WEB_PROMPT_CUTS:
        assert runner._AGENT_INSTRUCTIONS.count(old) == 1, old
    assert runner.agent_instructions() == runner._AGENT_INSTRUCTIONS


def test_the_request_leaves_out_the_web_tools(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    client, _ = _run(tmp_path, monkeypatch, [_tool("task_done", {"summary": "ok"})], 1)
    assert "web_search" in client.tools[0]
    monkeypatch.setenv("OPENBEAST_AGENT_HIDE_WEB", "1")
    client, _ = _run(tmp_path, monkeypatch, [_tool("task_done", {"summary": "ok"})], 1)
    assert "web_search" not in client.tools[0] and "fetch" not in client.tools[0]


# --- 6. the reasoning text in the transcript -------------------------------------------------------


def test_reasoning_log_text_caps_head_and_tail():
    assert runner.reasoning_log_text("abc", 10) == ("abc", False)
    text = "H" * 50 + "M" * 1000 + "T" * 150
    out, cut = runner.reasoning_log_text(text, 200)
    assert cut and out.startswith("H" * 50) and out.endswith("T" * 150)
    assert "1,000 chars of reasoning not logged" in out


def test_reasoning_text_is_logged_and_the_integrity_scan_still_reads_it(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    monkeypatch.setenv("OPENBEAST_AGENT_REASONING_LOG_CHARS", "4000")
    # a reasoning that NAMES the repo: thought is not a fetch, never a hit
    r1 = "think about github.com/MaximilianKhan/openbeast/evals/refs " + "z" * 9000
    _, events = _run(tmp_path, monkeypatch, [
        _ds_capped(r1), _tool("bash", {"command": "curl -s https://github.com/MaximilianKhan/openbeast"}),
        _tool("task_done", {"summary": "ok"})])
    rs = [e for e in events if e["type"] == "reasoning"]
    assert len(rs) == 1 and rs[0]["chars"] == len(r1) and rs[0]["capped"] is True
    assert rs[0]["truncated"] is True and len(rs[0]["text"]) < 4100
    assert rs[0]["text"].startswith("think about github.com")
    spec = importlib.util.spec_from_file_location(
        "_audit_integrity_scan_u", ROOT / "evals" / "audit" / "integrity_scan.py")
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    hits = S.scan_transcript(str(tmp_path / "run.jsonl"))
    assert [h["tool"] for h in hits] == ["bash"]          # the tool call, not the reasoning
    assert hits[0]["iteration"] == 2


def test_reasoning_log_off_with_zero(tmp_path, monkeypatch):
    _eval_thinking(monkeypatch)
    monkeypatch.setenv("OPENBEAST_AGENT_REASONING_LOG_CHARS", "0")
    _, events = _run(tmp_path, monkeypatch, [_ds_capped(), _tool("task_done", {"summary": "ok"})])
    assert not [e for e in events if e["type"] == "reasoning"]


# --- 7. keys and arms ------------------------------------------------------------------------------


def test_agent_switches_key_and_pin_the_harness10_switches():
    import run_eval
    comp, meta = run_eval.agent_switches({
        "OPENBEAST_AGENT_THINKING": "on", "OPENBEAST_AGENT_MAX_TOKENS": "16384",
        "OPENBEAST_AGENT_FORCE_ACT": "1", "OPENBEAST_AGENT_DONE_VERIFIED": "1",
        "OPENBEAST_AGENT_ALGO_LINES": "1", "OPENBEAST_AGENT_HIDE_WEB": "1"})
    assert comp == "ag-think-on-cap16384-fact2-dverif-alines-noweb"
    assert meta["force_act"] == 2 and meta["done_verified"] and meta["algo_lines"]
    assert meta["hide_web"]
    comp3, meta3 = run_eval.agent_switches({"OPENBEAST_AGENT_FORCE_ACT": "1",
                                            "OPENBEAST_AGENT_FORCE_ACT_AFTER": "3"})
    assert comp3 == "ag-fact3" and meta3["force_act"] == 3
    # the AFTER knob alone, or every switch at 0, keys nothing (legacy keys stay)
    off = {k: "0" for k in ("OPENBEAST_AGENT_FORCE_ACT", "OPENBEAST_AGENT_DONE_VERIFIED",
                            "OPENBEAST_AGENT_ALGO_LINES", "OPENBEAST_AGENT_HIDE_WEB")}
    assert run_eval.agent_switches({**off, "OPENBEAST_AGENT_FORCE_ACT_AFTER": "3"}) == \
        run_eval.agent_switches({})
    src = (ROOT / "evals" / "run_eval.py").read_text()
    for k in ("OPENBEAST_AGENT_FORCE_ACT", "OPENBEAST_AGENT_FORCE_ACT_AFTER",
              "OPENBEAST_AGENT_DONE_VERIFIED", "OPENBEAST_AGENT_ALGO_LINES",
              "OPENBEAST_AGENT_HIDE_WEB"):
        assert f'"{k}": ' in src.split("agent_env_pin = {")[1].split("}")[0], k


def test_the_harness_is_harness10():
    line = (ROOT / "evals" / "HARNESS_VERSION").read_text().strip().splitlines()[-1]
    assert line.split()[0] == "harness10"


def test_the_u_arms():
    import campaign as C
    camp = C.load_campaign("v5-thinking-first")
    assert C.validate_campaign({k: v for k, v in camp.items() if k != "_path"}) == []
    t2, u = C.resolve_arm(camp, "T2"), C.resolve_arm(camp, "U")
    changed = {k: u["env"][k] for k in u["env"] if u["env"][k] != t2["env"].get(k)}
    assert changed == {"OPENBEAST_AGENT_MAX_TOKENS": "16384", "OPENBEAST_AGENT_ACT_NUDGE": "0",
                       "OPENBEAST_AGENT_FORCE_ACT": "1", "OPENBEAST_AGENT_FORCE_ACT_AFTER": "2",
                       "OPENBEAST_AGENT_DONE_VERIFIED": "1", "OPENBEAST_AGENT_HIDE_WEB": "1"}
    assert C.arg_value(u["args"], "--reasoning-budget") == "16384"
    u8 = C.resolve_arm(camp, "U_8k")
    assert {k: u8["env"][k] for k in u8["env"] if u8["env"][k] != u["env"].get(k)} == \
        {"OPENBEAST_AGENT_MAX_TOKENS": "8192"}
    assert C.arg_value(u8["args"], "--reasoning-budget") == "8192"
    up, upa = C.resolve_arm(camp, "UP"), C.resolve_arm(camp, "UPA")
    assert {k: up["env"][k] for k in up["env"] if up["env"][k] != u["env"].get(k)} == \
        {"OPENBEAST_AGENT_ALGO_LINES": "1"}
    assert {k: upa["env"][k] for k in upa["env"] if upa["env"][k] != up["env"].get(k)} == \
        {"OPENBEAST_ALGO_PACK": "1"}
    assert upa["env"]["BEAST_PACKS"] == "1"                 # the pack rides the packs channel
    for a in ("U", "U_8k", "UP", "UPA"):
        r = C.resolve_arm(camp, a)
        spec = C.arm_spec(camp, a)
        assert spec["optional"] is True and a not in C.run_order(camp)
        assert (r["jobs"], r["slots"]) == (4, 4) and r["thinking"] == "on"
        assert spec["time_x"] == 2.3 and "Evidence" in C.arm_spec(camp, "U")["note"]
        assert C.missing_requirements(str(ROOT), sys.executable,
                                      {"env": r["requires"]["env"]}) == []
    assert "GLM-5.3-Flash" in C.arm_spec(camp, "UP")["note"]
    assert ALGO_SHA256 in C.arm_spec(camp, "UPA")["note"]
    # every harness10 switch is pinned off for every other arm
    for k in ("OPENBEAST_AGENT_FORCE_ACT", "OPENBEAST_AGENT_DONE_VERIFIED",
              "OPENBEAST_AGENT_ALGO_LINES", "OPENBEAST_AGENT_HIDE_WEB", "OPENBEAST_ALGO_PACK"):
        assert camp["common"]["env"][k] == "0" and t2["env"][k] == "0"
