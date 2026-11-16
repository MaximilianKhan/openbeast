#!/usr/bin/env python3
"""Runner v2 (2026-11-16): harness frictions from the GLM-5.3-Flash v5 audit.

An audit of the 45 failed v5 units found 43 genuine model failures and a set
of harness frictions that cost the model turns. Each change is pinned here:

  (a) platform line: the system prompt names the tool host's OS and the bash
      tool's shell; the bash tool prefers /bin/bash, falls back to /bin/sh
  (b) turn budget: stated up front, plus a notice in the last 3 turns
  (c) update_plan with an unchanged plan gets a nudge (and still costs its turn)
  (d) repeat guard: a 3rd identical call+result gets a nudge
  (e) a bash timeout always says it timed out, with the limit

Pure unit tests: a scripted fake client, no llama-server.
Run: python3 -m pytest tests/test_runner_v2.py -q
"""

import json
import os
import signal
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import runner  # noqa: E402
import tools  # noqa: E402

HAS_BASH = os.access("/bin/bash", os.X_OK)


@pytest.fixture(autouse=True)
def _fresh_plan():
    tools.reset_plan()
    yield
    tools.reset_plan()


class _Client:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, model, messages, tools, temperature, **kw):
        self.calls.append([dict(m) for m in messages])
        return self.script.pop(0)


def _resp(name, args, id_="1"):
    raw = args if isinstance(args, str) else json.dumps(args)
    fn = type("F", (), {"name": name, "arguments": raw})()
    tc = type("T", (), {"id": id_, "function": fn})()
    msg = type("M", (), {"content": "", "tool_calls": [tc]})()
    return type("R", (), {
        "choices": [type("C", (), {"message": msg, "finish_reason": "tool_calls"})()],
        "usage": None})()


def _run(tmp_path, monkeypatch, script, max_iter, system_prompt="s"):
    monkeypatch.delenv("OPENBEAST_TASK_PATHS", raising=False)
    client = _Client(script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    log = tmp_path / "run.jsonl"
    out = runner.run_agent("task", max_iter=max_iter, log_file=str(log),
                           system_prompt=system_prompt, workdir=str(tmp_path))
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    return out, client, events


def _tool_results(call):
    return [m["content"] for m in call if m.get("role") == "tool"]


# ---------------------------------------------------------------------------
# (a) platform line + the bash tool's shell
# ---------------------------------------------------------------------------

def test_platform_line_darwin_names_bsd_userland():
    line = runner.platform_line("Darwin", "/bin/bash")
    assert line.startswith("Tool host: Darwin")
    assert "BSD userland" in line and "`sed -i ''`" in line
    assert "no GNU `timeout`" in line and "bash 3.2" in line
    assert "`/bin/bash -c`" in line
    assert line.count("\n") == 1          # one line


def test_platform_line_linux_and_sh_fallback():
    line = runner.platform_line("Linux", "/bin/sh")
    assert line == "Tool host: Linux. The bash tool runs each command with `/bin/sh -c`.\n"
    assert "BSD" not in line
    # bash 3.2 is a macOS fact only, and only when bash is the shell.
    assert "3.2" not in runner.platform_line("Darwin", "/bin/sh")


def test_platform_line_defaults_to_this_host_and_tools_shell():
    import platform
    line = runner.platform_line()
    assert line.startswith(f"Tool host: {platform.system()}")
    assert f"`{tools.BASH_SHELL} -c`" in line


def test_system_prompt_carries_platform_and_turn_budget():
    prompt = runner.build_system_prompt(max_iter=15)
    assert runner.platform_line() in prompt
    assert "you have 15 tool turns" in prompt
    # No budget line when none is known (a caller building the prompt alone).
    assert "Turn budget" not in runner.build_system_prompt()


def test_prompt_files_are_untouched_by_runner_v2():
    # Owner's rule: the new text lives in runner.py only.
    for f in ("system-prompt.md", "system-prompt-tools.md"):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            text = open(p, encoding="utf-8").read()
            assert "Tool host:" not in text and "Turn budget:" not in text


def test_bash_shell_prefers_bash_and_falls_back(monkeypatch):
    monkeypatch.setattr(tools.os, "access", lambda p, m: False)
    assert tools._bash_shell() == "/bin/sh"
    monkeypatch.setattr(tools.os, "access", lambda p, m: p == "/bin/bash")
    assert tools._bash_shell() == "/bin/bash"


def test_bash_schema_names_the_shell():
    desc = next(s["function"]["description"] for s in tools.TOOL_SCHEMAS
                if s["function"]["name"] == "bash")
    assert f"({tools.BASH_SHELL} -c)" in desc


@pytest.mark.skipif(not HAS_BASH, reason="no /bin/bash on this host")
def test_bash_tool_runs_bash_syntax(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    monkeypatch.setattr(tools, "BASH_SHELL", "/bin/bash")
    # Process substitution and [[ ]]: the audit's sh failures.
    out = tools.bash("diff <(echo a) <(echo a) && [[ x == x ]] && echo ok")
    assert out == "ok\n"
    assert tools.bash('echo "$0"').strip() == "/bin/bash"


def test_bash_tool_sh_path_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    monkeypatch.setattr(tools, "BASH_SHELL", "/bin/sh")
    assert tools.bash('echo "$0"').strip() == "/bin/sh"
    assert tools.bash("echo hi | tr a-z A-Z") == "HI\n"


@pytest.mark.skipif(not HAS_BASH, reason="no /bin/bash on this host")
@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/sh"])
def test_wrapper_path_uses_the_same_shell(tmp_path, monkeypatch, shell):
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    monkeypatch.setattr(tools, "BASH_SHELL", shell)
    monkeypatch.setenv("OPENBEAST_BASH_WRAPPER", "env")
    seen = []
    real = tools.run_reaped

    def spy(command, timeout, **kw):
        seen.append(command)
        return real(command, timeout, **kw)
    monkeypatch.setattr(tools, "run_reaped", spy)
    out = tools.bash("echo wrapped")
    assert out == "wrapped\n"
    assert seen[0].startswith(f"env {shell} -c ")


@pytest.mark.skipif(not HAS_BASH, reason="no /bin/bash on this host")
def test_bash_env_is_not_sourced(tmp_path, monkeypatch):
    rc = tmp_path / "rc.sh"
    rc.write_text("echo SOURCED\n")
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    monkeypatch.setattr(tools, "BASH_SHELL", "/bin/bash")
    monkeypatch.setenv("BASH_ENV", str(rc))
    assert tools.bash("echo run") == "run\n"


def test_run_agent_default_prompt_states_budget_and_host(tmp_path, monkeypatch):
    _, client, _ = _run(tmp_path, monkeypatch,
                        [_resp("task_done", {"summary": "ok"})],
                        max_iter=7, system_prompt=None)
    system = client.calls[0][0]["content"]
    assert "you have 7 tool turns" in system
    assert "Tool host:" in system


# ---------------------------------------------------------------------------
# (b) remaining-turns notice
# ---------------------------------------------------------------------------

def test_turns_notice_only_in_the_last_three():
    assert runner.turns_notice(1, 10) == ""
    assert runner.turns_notice(7, 10) == ""          # 4 left
    assert "3 turns left" in runner.turns_notice(8, 10)
    assert "2 turns left" in runner.turns_notice(9, 10)
    assert "LAST turn" in runner.turns_notice(10, 10)
    assert runner.turns_notice(11, 10) == ""


def test_turns_notice_rides_transiently_in_the_request(tmp_path, monkeypatch):
    script = [_resp("bash", {"command": f"echo {i}"}, str(i)) for i in range(5)]
    _, client, _ = _run(tmp_path, monkeypatch, script, max_iter=5)

    def notices(call):
        return [m["content"] for m in call if "[turn budget" in str(m.get("content"))]
    assert notices(client.calls[0]) == [] and notices(client.calls[1]) == []
    assert "3 turns left" in notices(client.calls[2])[0]
    assert "2 turns left" in notices(client.calls[3])[0]
    assert "LAST turn" in notices(client.calls[4])[0]
    # Never stored: each request carries exactly one notice, the current one.
    assert all(len(notices(c)) == 1 for c in client.calls[2:])


def test_turns_notice_joins_the_plan_block(tmp_path, monkeypatch):
    plan = {"steps": [{"step": "fix it", "status": "in_progress"}]}
    _, client, _ = _run(tmp_path, monkeypatch,
                        [_resp("update_plan", plan), _resp("bash", {"command": "true"})],
                        max_iter=2)
    last = client.calls[1][-1]
    assert last["role"] == "user"
    assert last["content"].startswith("[plan — 0/1 done")
    assert last["content"].endswith("call task_done now if the work is done]")


# ---------------------------------------------------------------------------
# (c) unchanged update_plan
# ---------------------------------------------------------------------------

def test_update_plan_unchanged_gets_a_nudge():
    steps = [{"step": "a", "status": "done"}, {"step": "b", "status": "in_progress"}]
    assert tools.update_plan(steps).startswith("Plan updated")
    out = tools.update_plan([dict(s) for s in steps], explanation="again")
    assert out.startswith("Plan unchanged")
    assert "work on the in_progress step (b)" in out
    assert tools.get_plan() == steps
    # Aliases normalise first: "active" == "in_progress" is still unchanged.
    out = tools.update_plan([{"step": "a", "status": "completed"},
                             {"step": "b", "status": "active"}])
    assert out.startswith("Plan unchanged")
    # A real status change is an ordinary update.
    out = tools.update_plan([{"step": "a", "status": "done"},
                             {"step": "b", "status": "done"}])
    assert out.startswith("Plan updated: 2/2 done")


def test_unchanged_plan_still_costs_its_turn(tmp_path, monkeypatch):
    plan = {"steps": [{"step": "x", "status": "in_progress"}]}
    script = [_resp("update_plan", plan, str(i)) for i in range(3)]
    out, client, events = _run(tmp_path, monkeypatch, script, max_iter=3)
    assert len(client.calls) == 3 and out == "(max iterations reached)"
    results = [e["result"] for e in events if e["type"] == "tool_call"]
    assert results[0].startswith("Plan updated")
    assert all(r.startswith("Plan unchanged") for r in results[1:])


def test_instructions_no_longer_ask_to_keep_the_plan_current():
    assert "keep it current" not in runner._AGENT_INSTRUCTIONS
    assert "never re-send it unchanged" in runner._AGENT_INSTRUCTIONS
    tools.update_plan(["a"])
    assert "keep it current" not in tools.plan_block()


# ---------------------------------------------------------------------------
# (d) repeat guard
# ---------------------------------------------------------------------------

def test_third_identical_call_gets_the_nudge(tmp_path, monkeypatch):
    script = [_resp("bash", {"command": "echo same"}, str(i)) for i in range(4)]
    _, client, events = _run(tmp_path, monkeypatch, script, max_iter=4)
    results = [e["result"] for e in events if e["type"] == "tool_call"]
    assert "seen this exact output" not in results[0] + results[1]
    assert results[2].endswith(
        "[You have seen this exact output 3 times; act on it "
        "(edit, run the tests, or call task_done)]")
    assert "4 times" in results[3]
    assert results[2].startswith("same\n")
    guards = [e for e in events if e["type"] == "repeat_guard"]
    assert [g["repeats"] for g in guards] == [3, 4]
    # The model sees it in the tool turn itself.
    assert "3 times" in _tool_results(client.calls[3])[-1]


def test_repeat_guard_needs_same_args_and_same_result(tmp_path, monkeypatch):
    f = tmp_path / "f.txt"
    f.write_text("v1\n")
    calls = [
        _resp("read_file", {"path": str(f)}, "1"),
        _resp("read_file", {"path": str(f), "offset": 1}, "2"),   # other args
        _resp("bash", {"command": f"echo v2 > {f}"}, "3"),
        _resp("read_file", {"path": str(f)}, "4"),                 # other result
        _resp("read_file", {"path": str(f)}, "5"),
    ]
    _, _, events = _run(tmp_path, monkeypatch, calls, max_iter=5)
    assert not [e for e in events if e["type"] == "repeat_guard"]


def test_repeat_guard_key_ignores_arg_order():
    assert runner._call_key("bash", {"command": "ls", "timeout": 5}) == \
        runner._call_key("bash", {"timeout": 5, "command": "ls"})
    assert runner._call_key("bash", {"command": "ls"}) != \
        runner._call_key("read_file", {"command": "ls"})


def test_task_done_is_exempt_from_the_repeat_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(tmp_path / "x.py")]))
    client = _Client([_resp("task_done", {"summary": "d"}, str(i)) for i in range(3)])
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    log = tmp_path / "run.jsonl"
    runner.run_agent("task", max_iter=3, log_file=str(log), system_prompt="s",
                     workdir=str(tmp_path))
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert not [e for e in events if e["type"] == "repeat_guard"]


# ---------------------------------------------------------------------------
# (e) timeout messages
# ---------------------------------------------------------------------------

def test_timeout_message_states_the_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    out = tools.bash("echo started; sleep 30", timeout=1)
    assert out.startswith("Error: command timed out after 1s (the bash tool's timeout")
    assert "started" in out    # partial output kept


def test_darwin_eperm_on_a_zombie_group_still_reads_as_timeout(tmp_path, monkeypatch):
    """Darwin's killpg answers EPERM when only zombies remain in the group.
    That escaped _killpg and the tool said "[Errno 1] Operation not
    permitted" for a command it had just killed on timeout."""
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    real = os.killpg

    def killpg(pid, sig):
        if sig == signal.SIGKILL:
            try:
                real(pid, sig)
            except ProcessLookupError:
                pass
            raise PermissionError(1, "Operation not permitted")
        return real(pid, sig)
    monkeypatch.setattr(tools.os, "killpg", killpg)
    out = tools.bash("sleep 30", timeout=1)
    assert out.startswith("Error: command timed out after 1s"), out
    assert "Operation not permitted" not in out


def test_any_error_past_the_deadline_is_reported_as_timeout(monkeypatch):
    clock = iter([100.0, 103.0])
    monkeypatch.setattr(tools.time, "monotonic", lambda: next(clock))

    def boom(*a, **k):
        raise OSError(1, "Operation not permitted")
    monkeypatch.setattr(tools, "run_reaped", boom)
    out = tools.bash("sleep 9", timeout=2)
    assert out.startswith("Error: command timed out after 2s")


def test_error_before_the_deadline_keeps_its_own_text(monkeypatch):
    def boom(*a, **k):
        raise OSError(2, "No such file or directory")
    monkeypatch.setattr(tools, "run_reaped", boom)
    out = tools.bash("x", timeout=120)
    assert out.startswith("Error: [Errno 2]") and "timed out" not in out


# ---------------------------------------------------------------------------
# (f) opt-in per-request thinking toggle (OPENBEAST_AGENT_THINKING)
# ---------------------------------------------------------------------------

class _KwClient(_Client):
    def __init__(self, script):
        super().__init__(script)
        self.kwargs = []

    def create(self, model, messages, tools, temperature, **kw):
        self.kwargs.append(kw)
        return super().create(model, messages, tools, temperature, **kw)


def _run_kw(tmp_path, monkeypatch, script, max_iter=2):
    monkeypatch.delenv("OPENBEAST_TASK_PATHS", raising=False)
    client = _KwClient(script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    runner.run_agent("task", max_iter=max_iter, log_file=str(tmp_path / "r.jsonl"),
                     system_prompt="s", workdir=str(tmp_path))
    return client


@pytest.mark.parametrize("value,flag", [("on", True), ("off", False), ("ON", True)])
def test_thinking_toggle_sends_enable_thinking(tmp_path, monkeypatch, value, flag):
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", value)
    client = _run_kw(tmp_path, monkeypatch,
                     [_resp("bash", {"command": "true"}, "1"),
                      _resp("task_done", {"summary": "ok"}, "2")])
    assert len(client.kwargs) == 2     # on EVERY request
    for kw in client.kwargs:
        assert kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": flag}}


@pytest.mark.parametrize("value", [None, "", "maybe"])
def test_thinking_unset_sends_nothing(tmp_path, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("OPENBEAST_AGENT_THINKING", raising=False)
    else:
        monkeypatch.setenv("OPENBEAST_AGENT_THINKING", value)
    monkeypatch.setenv("OPENBEAST_EVAL", "1")      # uncapped: no max_tokens either
    client = _run_kw(tmp_path, monkeypatch, [_resp("task_done", {"summary": "ok"})])
    assert client.kwargs == [{}]                    # byte-identical request


def test_thinking_without_a_cap_is_not_mistaken_for_truncation(tmp_path, monkeypatch):
    # finish_reason "length" is OUR cap only when max_tokens was sent: the
    # toggle's kwargs must not make an uncapped turn look truncated.
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "on")
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    msg = type("M", (), {"content": "x" * 5000, "tool_calls": None})()
    resp = type("R", (), {"choices": [type("C", (), {
        "message": msg, "finish_reason": "length"})()], "usage": None})()
    client = _run_kw(tmp_path, monkeypatch, [resp, _resp("task_done", {"summary": "ok"})])
    assert client.calls[1][2]["content"] == "x" * 5000
    assert "output cap" not in json.dumps(client.calls[1])


def test_reasoning_content_is_not_fed_back(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "on")
    first = _resp("bash", {"command": "true"}, "1")
    first.choices[0].message.reasoning_content = "SECRET-CHAIN-OF-THOUGHT"
    client = _run_kw(tmp_path, monkeypatch,
                     [first, _resp("task_done", {"summary": "ok"}, "2")])
    assert "SECRET-CHAIN-OF-THOUGHT" not in json.dumps(client.calls[1], default=str)
    assert not any("reasoning_content" in m for m in client.calls[1])


def test_run_eval_passes_the_thinking_switch_to_the_runner(monkeypatch):
    """`OPENBEAST_AGENT_THINKING=on python evals/run_eval.py ...` must reach
    the runner: run_eval copies the parent env into the child."""
    import importlib.util
    sys.path.insert(0, os.path.join(ROOT, "evals"))
    # A private copy: other tests replace run_eval.run_agent globally.
    spec = importlib.util.spec_from_file_location(
        "run_eval_thinking_probe", os.path.join(ROOT, "evals", "run_eval.py"))
    re_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(re_mod)
    captured = {}

    class _FakeProc:
        returncode = 0
        pid = 424243

        def communicate(self, timeout=None):
            return ("TOKENS: prompt=1 completion=1 total=2\n", "")

        def kill(self):
            pass

    def fake_popen(cmd, **kw):
        if "--max-iter" in cmd:     # the runner; not the leftover sweep's `ps`
            captured["env"] = kw["env"]
        return _FakeProc()
    monkeypatch.setattr(re_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "on")
    re_mod.run_agent({"task": "write a haiku", "max_iter": 1}, "http://127.0.0.1:8080/v1")
    assert captured["env"]["OPENBEAST_AGENT_THINKING"] == "on"
