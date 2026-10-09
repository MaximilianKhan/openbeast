"""A model turn longer than the client's read timeout (review 2026-10-09,
evals F2), and the runner half of F12.

Before suite v4.1 the runner's openai client had its default 600 s read
timeout, in wall seconds and not scaled by --jobs, and re-sent a timed-out
request. A long turn under --jobs 4 was thrown away, generated again from
scratch, and the unit hit the wall as a 0-token FAIL; when the agent
survived, the timeout counted as an API error and relabelled the unit
`server_error`.

Under OPENBEAST_EVAL the runner now bounds each request by what is left of
the unit's wall budget (OPENBEAST_EVAL_WALL_S, from run_eval), never
re-sends, counts a request timeout apart from API errors, and prints a
running TOKENS line. Outside eval nothing changes — every case here has
its non-eval control.

The fake-client cases build their own script; the two end-to-end cases run
the REAL runner against a local HTTP server that answers once and then
stalls, so the real openai client's timeout is what is measured. No GPU, no
model server.
"""

import http.server
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import openai
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "evals"))

import runner  # noqa: E402

RUNNER = str(ROOT / "agents" / "runner.py")


# --- scripted client -------------------------------------------------------

class _Msg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _TC:
    def __init__(self, id_, name, args):
        self.id = id_
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()


class _Resp:
    def __init__(self, msg, usage=None, finish="tool_calls"):
        self.choices = [type("C", (), {"message": msg, "finish_reason": finish})()]
        self.usage = (type("U", (), dict(zip(
            ("prompt_tokens", "completion_tokens", "total_tokens"), usage)))()
            if usage else None)


def _timeout_error():
    return openai.APITimeoutError(request=httpx.Request("POST", "http://x/v1/chat/completions"))


class _Fake:
    def __init__(self, script):
        self.script = list(script)
        self.kwargs = []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.kwargs.append(kw)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _bash_turn(k, usage=None):
    return _Resp(_Msg(tool_calls=[_TC(str(k), "list_files", {"pattern": "nothing-*"})]), usage)


def _done(usage=None):
    return _Resp(_Msg(tool_calls=[_TC("d", "task_done", {"summary": "fin"})]), usage)


def _run(tmp_path, monkeypatch, script, env, max_iter=6):
    for k in ("OPENBEAST_EVAL", "OPENBEAST_EVAL_WALL_S", "OPENBEAST_TASK_PATHS",
              "OPENBEAST_AGENT_MAX_TOKENS", "OPENBEAST_REASONING_BUDGET", "REASONING_BUDGET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(runner, "_CONF_PATH", tmp_path / "openbeast.conf", raising=False)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    ctor = {}
    fake = _Fake(script)

    def make(**kw):
        ctor.update(kw)
        return fake

    monkeypatch.setattr(runner, "OpenAI", make)
    out = runner.run_agent("task", max_iter=max_iter, log_file=str(tmp_path / "r.jsonl"),
                           system_prompt="s", workdir=str(tmp_path))
    return out, fake, ctor


EVAL = {"OPENBEAST_EVAL": "1", "OPENBEAST_EVAL_WALL_S": "900"}


def test_eval_client_never_resends_and_the_request_gets_the_wall_budget(tmp_path, monkeypatch):
    _, fake, ctor = _run(tmp_path, monkeypatch, [_bash_turn(1), _done()], EVAL)
    assert ctor["max_retries"] == 0
    timeouts = [kw["timeout"] for kw in fake.kwargs]
    # The whole 900 s budget, not the client's 600 s — and it only shrinks.
    assert 890 < timeouts[0] <= 900, timeouts
    assert timeouts[1] <= timeouts[0]


def test_outside_eval_the_client_is_untouched(tmp_path, monkeypatch):
    """Control: one quick retry and the client's own timeout, as before."""
    _, fake, ctor = _run(tmp_path, monkeypatch, [_bash_turn(1), _done()], {})
    assert ctor["max_retries"] == 1
    assert all("timeout" not in kw for kw in fake.kwargs)


def test_a_wall_budget_without_the_eval_marker_changes_nothing(tmp_path, monkeypatch):
    _, fake, ctor = _run(tmp_path, monkeypatch, [_done()], {"OPENBEAST_EVAL_WALL_S": "900"})
    assert ctor["max_retries"] == 1 and "timeout" not in fake.kwargs[0]


def test_eval_marker_without_a_budget_keeps_the_client_timeout(tmp_path, monkeypatch):
    _, fake, ctor = _run(tmp_path, monkeypatch, [_done()], {"OPENBEAST_EVAL": "1"})
    assert ctor["max_retries"] == 0 and "timeout" not in fake.kwargs[0]


def test_the_remaining_budget_shrinks_as_the_unit_runs(monkeypatch):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_EVAL_WALL_S", "100")
    assert runner._eval_deadline(now=1000.0) == 1100.0
    for bad in ("", "0", "-5", "soon"):
        monkeypatch.setenv("OPENBEAST_EVAL_WALL_S", bad)
        assert runner._eval_deadline(now=1000.0) is None, bad


def test_request_timeout_in_eval_is_counted_apart_and_not_resent(tmp_path, monkeypatch, capsys):
    # A budget already spent when the timeout lands, as it is for real.
    out, fake, _ = _run(tmp_path, monkeypatch,
                        [_bash_turn(1, usage=(1200, 340, 1540)), _timeout_error(), _done()],
                        {"OPENBEAST_EVAL": "1", "OPENBEAST_EVAL_WALL_S": "0.5"})
    text = capsys.readouterr().out
    assert len(fake.kwargs) == 2, "the timed-out request is not sent again"
    assert "REQUEST_TIMEOUTS: 1" in text
    assert "API_ERRORS: 0" in text
    assert "API error:" not in text, "run_eval's fallback count must not see a timeout"
    assert "TOKENS: prompt=1200 completion=340 total=1540" in text
    assert "wall budget ran out" in text
    events = [json.loads(ln) for ln in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert [e["type"] for e in events if e["type"] in ("request_timeout", "error")] == ["request_timeout"]


def test_request_timeout_outside_eval_is_an_api_error_as_before(tmp_path, monkeypatch, capsys):
    """Control: the loop counts it, waits and goes round again."""
    out, fake, _ = _run(tmp_path, monkeypatch, [_timeout_error(), _done()], {})
    text = capsys.readouterr().out
    assert out == "fin" and len(fake.kwargs) == 2
    assert "API_ERRORS: 1" in text and "API error:" in text
    assert "REQUEST_TIMEOUTS" not in text


def test_a_connection_error_in_eval_is_still_an_api_error(tmp_path, monkeypatch, capsys):
    """Control: only a TIMEOUT is the model's; a dead server stays infra."""
    err = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    out, fake, _ = _run(tmp_path, monkeypatch, [err, _done()], EVAL)
    text = capsys.readouterr().out
    assert out == "fin"
    assert "API_ERRORS: 1" in text and "REQUEST_TIMEOUTS: 0" in text


def test_running_token_line_per_turn_in_eval_only(tmp_path, monkeypatch, capsys):
    script = lambda: [_bash_turn(1, usage=(100, 10, 110)), _bash_turn(2, usage=(200, 20, 220)),
                      _done(usage=(300, 30, 330))]
    _run(tmp_path, monkeypatch, script(), EVAL)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("TOKENS:")]
    assert lines == ["TOKENS: prompt=100 completion=10 total=110",
                     "TOKENS: prompt=300 completion=30 total=330",
                     "TOKENS: prompt=600 completion=60 total=660",
                     "TOKENS: prompt=600 completion=60 total=660"]   # 3 running + the summary
    _run(tmp_path, monkeypatch, script(), {})
    out = capsys.readouterr().out
    assert [ln for ln in out.splitlines() if ln.startswith("TOKENS:")] == [
        "TOKENS: prompt=600 completion=60 total=660"]
    assert "REQUEST_TIMEOUTS" not in out


# --- the harness side ------------------------------------------------------

def _fresh(tmp_path):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    tasks = tmp_path / "tasks"
    tasks.mkdir(exist_ok=True)
    (tasks / "01_alpha.json").write_text(json.dumps({
        "id": "01_alpha", "name": "alpha", "difficulty": "easy",
        "task": "do alpha", "validation": {"type": "bash", "script": "false"},
        "max_iter": 3}))
    run_eval.TASKS_DIR = str(tasks)
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    return run_eval, cache


_AGENT = {"exit_code": 0, "elapsed_seconds": 1.0, "stdout": "", "stderr": "",
          "tokens": {"prompt": 10, "completion": 500, "total": 510},
          "iterations": 3, "compactions": 0, "api_errors": 0}


def _one_row(tmp_path, monkeypatch, agent):
    run_eval, cache = _fresh(tmp_path)
    monkeypatch.setattr(run_eval, "capture_server_config", lambda *a, **k: {})
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info", lambda: {})
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: dict(agent))
    row = run_eval.run_eval(model_name="m")["tasks"][0]
    return row, list(cache.CACHE_DIR.glob("*.json"))


def test_request_timeout_row_is_a_model_verdict_not_server_error(tmp_path, monkeypatch):
    row, cached = _one_row(tmp_path, monkeypatch, {**_AGENT, "request_timeouts": 1})
    assert row["passed"] is False
    assert "reason" not in row, "not server_error: it seats and pairs like any FAIL"
    assert row["request_timeouts"] == 1 and row["api_errors"] == 0
    assert not cached, "wall-clock dependent, like the exit -1 row it replaces"


def test_api_error_row_is_still_server_error(tmp_path, monkeypatch):
    """Control: the classification the timeout used to fall into."""
    row, cached = _one_row(tmp_path, monkeypatch, {**_AGENT, "api_errors": 1})
    assert row["reason"] == "server_error" and "request_timeouts" not in row
    assert not cached


def test_plain_fail_row_is_unmarked_and_banked(tmp_path, monkeypatch):
    row, cached = _one_row(tmp_path, monkeypatch, dict(_AGENT))
    assert "reason" not in row and "request_timeouts" not in row
    assert len(cached) == 1


def test_parse_request_timeouts_summary_line_or_events(tmp_path):
    run_eval, _ = _fresh(tmp_path)
    assert run_eval._parse_request_timeouts("x\nREQUEST_TIMEOUTS: 2\n") == 2
    # A runner killed before its summary: count the event lines.
    assert run_eval._parse_request_timeouts("  Request timed out: Request timed out.\n") == 1
    assert run_eval._parse_request_timeouts("  API error: Connection error.\n") == 0
    assert run_eval._parse_api_errors("  Request timed out: Request timed out.\n") == 0


def test_the_board_counts_a_request_timeout_row_as_killed():
    import scoring
    rows = [{"agent_exit_code": 0}, {"agent_exit_code": -1},
            {"agent_exit_code": 0, "request_timeouts": 1}]
    assert scoring.killed_units(rows) == 2


# --- end to end: the real runner, the real openai client -------------------

class _StallingServer:
    """An OpenAI-shaped endpoint: the first `answers` chat requests get a
    tool call with usage, every later one is held open until the test ends."""

    def __init__(self, answers):
        self.requests = 0
        self.release = threading.Event()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                outer.requests += 1
                if outer.requests > answers:
                    outer.release.wait(60)
                    return
                body = json.dumps({
                    "id": "c", "object": "chat.completion", "created": 0, "model": "m",
                    "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                        "role": "assistant", "content": None, "tool_calls": [{
                            "id": f"t{outer.requests}", "type": "function",
                            "function": {"name": "list_files",
                                         "arguments": json.dumps({"pattern": "none-*"})}}]}}],
                    "usage": {"prompt_tokens": 1200, "completion_tokens": 340,
                              "total_tokens": 1540}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def stalling():
    made = []

    def make(answers):
        made.append(_StallingServer(answers))
        return made[-1]

    yield make
    for s in made:
        s.close()


def _runner_env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENBEAST_")}
    env.update({"OPENAI_API_KEY": "x", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}, **extra)
    return env


def test_real_runner_stops_at_the_wall_budget_without_resending(tmp_path, stalling):
    srv = stalling(answers=1)
    t0 = time.monotonic()
    p = subprocess.run(
        [sys.executable, RUNNER, "--base-url", srv.url, "--max-iter", "5",
         "--workdir", str(tmp_path), "--log-dir", str(tmp_path / "logs"), "task"],
        capture_output=True, text=True, timeout=60,
        env=_runner_env(tmp_path, OPENBEAST_EVAL="1", OPENBEAST_EVAL_WALL_S="4"))
    took = time.monotonic() - t0
    assert p.returncode == 0, p.stderr[-500:]
    assert took < 20, f"stopped at the 4 s budget, not the client's 600 s ({took:.0f}s)"
    assert srv.requests == 2, "turn 1 answered, turn 2 timed out ONCE and was not re-sent"
    assert "REQUEST_TIMEOUTS: 1" in p.stdout and "API_ERRORS: 0" in p.stdout
    assert "TOKENS: prompt=1200 completion=340 total=1540" in p.stdout


def test_run_eval_wall_timeout_records_the_tokens_of_completed_turns(tmp_path, stalling):
    """The harness kills at the wall (exit -1). The row used to say tokens 0."""
    run_eval, _ = _fresh(tmp_path)
    srv = stalling(answers=1)
    # The real runner, with its transcript kept out of agents/logs.
    wrapper = tmp_path / "runner_here.py"
    wrapper.write_text(
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'agents')!r})\n"
        f"sys.argv[0] = {RUNNER!r}\n"
        f"sys.argv[1:1] = ['--log-dir', {str(tmp_path / 'logs')!r}]\n"
        f"runpy.run_path({RUNNER!r}, run_name='__main__')\n")
    run_eval.RUNNER_PATH = str(wrapper)
    old = dict(os.environ)
    try:
        for k in [k for k in os.environ if k.startswith("OPENBEAST_")]:
            del os.environ[k]
        os.environ.update(OPENAI_API_KEY="x", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
        # max_iter 2 x 60 s x (2/60) -> a 4 s wall budget.
        res = run_eval.run_agent({"task": "t", "max_iter": 2}, srv.url, timeout_scale=2 / 60)
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert srv.requests == 2
    assert res["tokens"] == {"prompt": 1200, "completion": 340, "total": 1540}
    assert res["exit_code"] in (-1, 0)
    if res["exit_code"] == 0:        # the runner's own deadline won the race
        assert res["request_timeouts"] == 1 and res["api_errors"] == 0


def test_run_eval_passes_the_scaled_wall_budget_to_the_runner(tmp_path):
    run_eval, _ = _fresh(tmp_path)
    fake = tmp_path / "fake_runner.py"
    fake.write_text("import os\nprint('WALL', os.environ.get('OPENBEAST_EVAL_WALL_S'), "
                    "os.environ.get('OPENBEAST_EVAL'))\n")
    run_eval.RUNNER_PATH = str(fake)
    res = run_eval.run_agent({"task": "t", "max_iter": 10}, "http://127.0.0.1:9/v1",
                             timeout_scale=2.0)
    assert "WALL 1200 1" in res["stdout"]        # 10 iterations x 60 s x 2.0
