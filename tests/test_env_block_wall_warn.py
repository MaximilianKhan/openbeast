#!/usr/bin/env python3
"""G22 environment block and G23 wall-clock warnings (docs/HARNESS_NOTES.md).

  * OPENBEAST_AGENT_ENV_BLOCK=1: a probed environment block (OS, toolchain
    versions, CLI tools, workdir, the task's paths and /tmp/eval_* dirs) is
    appended to the task message before the first model call. Bounded, no
    environment values, off = byte-identical requests.
  * OPENBEAST_AGENT_WALL_WARN=1: with the unit's wall known (--wall-s or
    OPENBEAST_AGENT_WALL_S, which run_eval's run_agent sets to the timeout it
    kills at), one transient notice at the first turn past 50% and past 80%.

Pure unit tests: a scripted fake client and a fake clock, no llama-server.
Run: python3 -m pytest tests/test_env_block_wall_warn.py -q
"""

import importlib.util
import json
import os
import shutil
import sys
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import runner  # noqa: E402
import tools  # noqa: E402

SWITCHES = ("OPENBEAST_AGENT_ENV_BLOCK", "OPENBEAST_AGENT_WALL_WARN",
            "OPENBEAST_AGENT_WALL_S", "OPENBEAST_TASK_PATHS")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)
    tools.reset_plan()
    yield
    tools.reset_plan()


@pytest.fixture
def eval_dir():
    """A real /tmp/eval_* dir: the block lists only paths under that prefix."""
    d = f"/tmp/eval_g19test_{uuid.uuid4().hex[:8]}"
    os.makedirs(d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


class _Client:
    def __init__(self, script, on_create=None):
        self.script = list(script)
        self.calls = []
        self.kwargs = []
        self.chat = self
        self.completions = self
        self.on_create = on_create

    def create(self, model, messages, tools, temperature, **kw):
        self.calls.append([dict(m) for m in messages])
        self.kwargs.append(kw)
        if self.on_create:
            self.on_create(len(self.calls))
        return self.script.pop(0)


def _resp(name, args, id_="1"):
    fn = type("F", (), {"name": name, "arguments": json.dumps(args)})()
    tc = type("T", (), {"id": id_, "function": fn})()
    msg = type("M", (), {"content": "", "tool_calls": [tc]})()
    return type("R", (), {
        "choices": [type("C", (), {"message": msg, "finish_reason": "tool_calls"})()],
        "usage": None})()


def _run(tmp_path, monkeypatch, script, max_iter=3, on_create=None, wall_s=None):
    client = _Client(script, on_create)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: client)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    log = tmp_path / f"run-{uuid.uuid4().hex[:6]}.jsonl"
    runner.run_agent("task", max_iter=max_iter, log_file=str(log),
                     system_prompt="s", workdir=str(tmp_path), wall_s=wall_s)
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    return client, events


def _bash_script(n):
    return [_resp("bash", {"command": f"echo {i}"}, str(i)) for i in range(n)]


# ---------------------------------------------------------------------------
# G22 — the environment block
# ---------------------------------------------------------------------------

def test_env_block_content(eval_dir):
    os.makedirs(f"{eval_dir}/src/deep/deeper")
    with open(f"{eval_dir}/main.py", "w") as f:
        f.write("x" * 2048)
    open(f"{eval_dir}/src/a.go", "w").close()
    open(f"{eval_dir}/src/deep/deeper/hidden.txt", "w").close()
    block = runner.env_block("/tmp", [f"{eval_dir}/main.py", f"{eval_dir}/out.zig"],
                             system="Darwin", machine="arm64")
    lines = block.splitlines()
    assert lines[0].startswith("[environment — probed by the harness")
    assert "os: Darwin arm64" in lines
    assert any(ln.startswith("toolchains: ") for ln in lines)
    assert "python3" in block                     # the test itself runs on one
    assert any(ln.startswith("cli: ") for ln in lines)
    for tool in runner._ENV_CLI:
        assert tool in next(ln for ln in lines if ln.startswith("cli: "))
    assert "workdir: /tmp" in lines
    assert f"{eval_dir}/main.py (exists)" in block
    assert f"{eval_dir}/out.zig (missing)" in block
    assert f"  {eval_dir}/" in lines
    assert "    main.py 2.0K" in lines
    assert "    src/" in lines and "      a.go 0B" in lines
    assert "      deep/" in lines                    # depth 2 is shown...
    assert "deeper" not in block and "hidden.txt" not in block   # ...depth 3 not


def test_env_block_without_task_paths_has_no_listing(monkeypatch):
    block = runner.env_block("/somewhere")
    assert "workdir: /somewhere" in block
    assert "expected task paths" not in block and "task dirs" not in block


def test_env_block_is_bounded(eval_dir):
    for i in range(100):
        open(f"{eval_dir}/file_with_a_rather_long_name_number_{i:03d}.txt", "w").close()
    many = [f"{eval_dir}/expected_output_file_{i}.py" for i in range(40)]
    block = runner.env_block("/tmp", many)
    entries = [ln for ln in block.splitlines() if ln.startswith("    ")
               and not ln.lstrip().startswith("...")]
    assert len(entries) <= runner._ENV_LIST_MAX
    assert len(block) <= runner._ENV_BLOCK_MAX_CHARS        # ~300 tokens
    paths_line = next(ln for ln in block.splitlines()
                      if ln.startswith("expected task paths: "))
    named = paths_line.count("(missing)")
    assert 1 <= named <= runner._ENV_PATHS_MAX
    assert paths_line.endswith(f", +{40 - named} more")
    assert len(paths_line) <= runner._ENV_PATHS_CHARS + 20
    # A long path list never crowds out the listing.
    assert f"  {eval_dir}/" in block.splitlines()
    assert any(ln.startswith("    file_with") for ln in block.splitlines())


def test_env_block_worst_case_head_leaves_room_for_the_listing(eval_dir, monkeypatch):
    # Seven distinct, maximal version lines (a Linux host with gcc != clang)
    # plus a long path list must still leave the task-dir listing visible.
    monkeypatch.setattr(runner, "_probe_version",
                        lambda cmd: (f"{cmd[0]} version " + "9" * 80)[:50])
    for i in range(5):
        open(f"{eval_dir}/f{i}", "w").close()
    many = [f"{eval_dir}/expected_output_file_{i}.py" for i in range(40)]
    block = runner.env_block("/tmp", many)
    assert len(block) <= runner._ENV_BLOCK_MAX_CHARS
    assert f"  {eval_dir}/" in block.splitlines()
    assert "    f0 0B" in block.splitlines()


def test_env_block_lists_entries_up_to_the_cap(eval_dir):
    for i in range(40):
        open(f"{eval_dir}/f{i:02d}", "w").close()
    block = runner.env_block("/tmp", [f"{eval_dir}/f00"])
    entries = [ln for ln in block.splitlines() if ln.startswith("    f")]
    assert len(entries) == 30
    assert "... +10 more entries" in block


def test_env_block_has_no_env_values_or_home(eval_dir, monkeypatch):
    secrets_ = {"OPENBEAST_API_KEY": "sk-g19-secret-key-value",
                "OPENAI_API_KEY": "sk-another-g19-secret",
                "AWS_SECRET_ACCESS_KEY": "aws-g19-very-secret",
                "GITHUB_TOKEN": "ghp_g19tokenvalue"}
    for k, v in secrets_.items():
        monkeypatch.setenv(k, v)
    home = os.path.expanduser("~")
    with open(f"{eval_dir}/a.py", "w") as f:
        f.write("print(1)")
    # A path outside /tmp/eval_* is never listed, even if it is "expected".
    block = runner.env_block("/tmp", [f"{eval_dir}/a.py", f"{home}/.ssh/id_rsa"])
    for v in secrets_.values():
        assert v not in block
    # No environment value at all (bar values that are mere common words).
    for k, v in os.environ.items():
        if len(v) >= 8 and v not in ("/tmp", eval_dir) and not v.startswith("/tmp/eval"):
            if v == f"{home}/.ssh/id_rsa":
                continue
            assert v not in block, k
    listing = block.split("task dirs", 1)[-1]
    assert home not in listing
    assert not any(ln.strip().startswith(".ssh") for ln in block.splitlines())


def test_env_block_does_not_follow_symlinks(eval_dir, tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "private.txt").write_text("x")
    os.symlink(str(tmp_path / "outside"), f"{eval_dir}/link")
    block = runner.env_block("/tmp", [f"{eval_dir}/x"])
    assert "link -> (symlink)" in block
    assert "private.txt" not in block


def test_probe_timeout_reads_as_unknown(monkeypatch):
    import subprocess

    def slow(*a, **kw):
        assert kw["timeout"] <= 3.0
        raise subprocess.TimeoutExpired(a[0], kw["timeout"])
    monkeypatch.setattr(subprocess, "run", slow)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    assert runner._probe_version(["zig", "version"]) == "?"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert runner._probe_version(["zig", "version"]) is None


def test_env_block_off_requests_are_byte_identical(tmp_path, monkeypatch):
    base, _ = _run(tmp_path, monkeypatch, _bash_script(2), max_iter=2)
    monkeypatch.setenv("OPENBEAST_AGENT_ENV_BLOCK", "0")
    zero, _ = _run(tmp_path, monkeypatch, _bash_script(2), max_iter=2)
    assert base.calls == zero.calls and base.kwargs == zero.kwargs
    assert base.calls[0][1] == {"role": "user", "content": "task"}


def test_env_block_on_rides_the_task_message_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_ENV_BLOCK", "1")
    client, events = _run(tmp_path, monkeypatch, _bash_script(3), max_iter=3)
    first = client.calls[0][1]
    assert first["role"] == "user"
    assert first["content"].startswith("task\n\n[environment — probed")
    # Stored in the history (a stable fact): same message on every turn, once.
    for call in client.calls:
        assert call[1] == first
        assert sum("[environment —" in str(m.get("content")) for m in call) == 1
    ev = [e for e in events if e["type"] == "env_block"]
    assert len(ev) == 1 and ev[0]["chars"] <= runner._ENV_BLOCK_MAX_CHARS


# ---------------------------------------------------------------------------
# G23 — wall-clock warnings
# ---------------------------------------------------------------------------

def test_wall_notice_fires_once_at_each_mark():
    fired: set = set()
    seen = [runner.wall_notice(t, 1000, fired) for t in (0, 100, 499, 500, 600, 799, 800, 950, 999)]
    hits = [(t, n) for t, n in zip((0, 100, 499, 500, 600, 799, 800, 950, 999), seen) if n]
    assert [t for t, _ in hits] == [500, 800]
    assert hits[0][1] == ("[wall clock — 500 s of your 1000 s wall used; "
                          "finish and verify the destination now]")
    assert "800 s of your 1000 s" in hits[1][1]


def test_wall_notice_jump_past_both_marks_fires_once():
    fired: set = set()
    assert "850 s" in runner.wall_notice(850, 1000, fired)
    assert runner.wall_notice(900, 1000, fired) == ""


def test_wall_notice_without_a_wall_is_silent():
    assert runner.wall_notice(10_000, 0, set()) == ""


def test_wall_budget_resolution(monkeypatch):
    assert runner.wall_budget_s(None) == 0.0
    monkeypatch.setenv("OPENBEAST_AGENT_WALL_S", "900")
    assert runner.wall_budget_s(None) == 900.0
    assert runner.wall_budget_s(120) == 120.0          # --wall-s wins
    monkeypatch.setenv("OPENBEAST_AGENT_WALL_S", "junk")
    assert runner.wall_budget_s(None) == 0.0


def _fake_clock(monkeypatch, times):
    """The clock reads times[0] at the run's start and during turn 1, then
    times[n-1] during turn n (the client's create() advances it)."""
    state = {"k": 0}
    monkeypatch.setattr(runner, "_clock", lambda: times[min(state["k"], len(times) - 1)])

    def advance(n):
        state["k"] = n
    return advance


def _notices(call):
    return [m["content"] for m in call if "[wall clock" in str(m.get("content"))]


def test_wall_warnings_fire_exactly_once_each_and_stay_transient(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_WALL_WARN", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_WALL_S", "1000")
    # turns 1..6 begin at these seconds into the run
    advance = _fake_clock(monkeypatch, [0, 300, 550, 700, 820, 900])
    client, events = _run(tmp_path, monkeypatch, _bash_script(6), max_iter=6,
                          on_create=advance)
    per_turn = [_notices(c) for c in client.calls]
    assert [len(n) for n in per_turn] == [0, 0, 1, 0, 1, 0]
    assert per_turn[2][0].endswith("550 s of your 1000 s wall used; "
                                   "finish and verify the destination now]")
    assert "820 s of your 1000 s" in per_turn[4][0]
    # Never stored: a later request's history carries no earlier notice.
    assert not any(_notices(c) for c in client.calls[3:4] + client.calls[5:])
    assert [e["iteration"] for e in events if e["type"] == "wall_notice"] == [3, 5]


def test_wall_notice_sits_next_to_the_turns_notice(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_WALL_WARN", "1")
    advance = _fake_clock(monkeypatch, [0, 900, 900])
    client, _ = _run(tmp_path, monkeypatch, _bash_script(3), max_iter=3,
                     on_create=advance, wall_s=1000)
    assert not _notices(client.calls[0])
    last = client.calls[1][-1]
    assert last["role"] == "user"
    assert "[turn budget — 2 turns left" in last["content"]
    assert last["content"].endswith("finish and verify the destination now]")


def test_wall_warn_off_or_no_wall_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_WALL_S", "100")      # wall known, switch off
    advance = _fake_clock(monkeypatch, [0, 60, 90, 99])
    client, _ = _run(tmp_path, monkeypatch, _bash_script(3), on_create=advance)
    assert not any(_notices(c) for c in client.calls)
    monkeypatch.delenv("OPENBEAST_AGENT_WALL_S")
    monkeypatch.setenv("OPENBEAST_AGENT_WALL_WARN", "1")      # switch on, no wall
    advance = _fake_clock(monkeypatch, [0, 60, 90, 99])
    client, _ = _run(tmp_path, monkeypatch, _bash_script(3), on_create=advance)
    assert not any(_notices(c) for c in client.calls)


def test_runner_cli_wall_s_reaches_run_agent(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "run_agent", lambda **kw: seen.update(kw))
    monkeypatch.setattr(sys, "argv", ["runner.py", "--wall-s", "450", "do it"])
    runner.main()
    assert seen["wall_s"] == 450.0
    monkeypatch.setattr(sys, "argv", ["runner.py", "do it"])
    runner.main()
    assert seen["wall_s"] == 0          # unset: falls back to OPENBEAST_AGENT_WALL_S


# ---------------------------------------------------------------------------
# run_eval -> runner: the wall the agent is told is the wall it is killed at
# ---------------------------------------------------------------------------

def _private_run_eval():
    sys.path.insert(0, os.path.join(ROOT, "evals"))
    spec = importlib.util.spec_from_file_location(
        "run_eval_wall_probe", os.path.join(ROOT, "evals", "run_eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("kw,want", [
    ({"wall_s": 900.0, "timeout_scale": 1.5}, 1350),
    ({"wall_s": None, "timeout_scale": 1.0}, 4 * 60),     # v4 rule: max_iter * 60
])
def test_run_eval_passes_the_kill_wall_to_the_runner(monkeypatch, kw, want):
    re_mod = _private_run_eval()
    captured = {}

    class _FakeProc:
        returncode = 0
        pid = 424244

        def communicate(self, timeout=None):
            captured.setdefault("timeout", timeout)
            return ("TOKENS: prompt=1 completion=1 total=2\n", "")

        def kill(self):
            pass

    def fake_popen(cmd, **kwargs):
        if "--max-iter" in cmd:
            captured["env"] = kwargs["env"]
            captured["cmd"] = cmd
        return _FakeProc()
    monkeypatch.setattr(re_mod.subprocess, "Popen", fake_popen)
    re_mod.run_agent({"task": "t", "max_iter": 4}, "http://127.0.0.1:8080/v1", **kw)
    assert captured["timeout"] == want
    assert captured["env"]["OPENBEAST_AGENT_WALL_S"] == str(want)
    assert "--wall-s" not in captured["cmd"]            # argv unchanged
