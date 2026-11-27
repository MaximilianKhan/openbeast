"""Review 1118: eval/harness integrity fixes other than the sandbox itself
(that is tests/test_eval_seal.py).

  4. evals/audit/integrity_scan.py flags network commands (curl / wget / git
     clone of a remote / github.com), /tmp/claude-* in any spelling, and write
     content that makes a build read a forbidden absolute path.
  6. A failed row is infra-lost to API errors only when they ENDED the run
     (api_errors_tail > 0); a recovered error leaves a verdict, as a pass with
     one always did. Rows without the field (every published one) keep the
     old rule.
  7. The served model: run_eval refuses to start unless /v1/models lists it,
     re-checks it around every unit, and stops the run as an infra stop
     (nothing banked, a pass discarded) when the server swaps models.
  8. A declared reasoning budget sends thinking explicitly.
  9. The admission probe requires 200 from every request.
 10. campaign.py forwards SIGTERM/SIGHUP to its run_eval child, records the
     child in state.json, and refuses to start while that child lives.
 11. eval-host.sh check fails a user-writable tool outside the prefix and an
     OPENBEAST_EVAL_PY outside it.
"""
from __future__ import annotations

import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "evals" / "audit"))
sys.path.insert(0, str(ROOT / "agents"))

import campaign as C  # noqa: E402


def _load_scan():
    # by path: evals/reference/integrity_scan.py shares the module name
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_audit_integrity_scan_1118", ROOT / "evals" / "audit" / "integrity_scan.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


S = _load_scan()


# --- 4. the integrity scan ---------------------------------------------------------------

def _transcript(path: Path, calls) -> None:
    with open(path, "w") as f:
        f.write(json.dumps({"type": "start", "task": "t", "model": "m"}) + "\n")
        for i, (name, args, result) in enumerate(calls, 1):
            f.write(json.dumps({"type": "iteration", "number": i}) + "\n")
            f.write(json.dumps({"type": "tool_call", "name": name, "args": args,
                                "result": result}) + "\n")


@pytest.mark.parametrize("cmd", [
    "curl -sS https://example.com",
    "git clone https://github.com/MaximilianKhan/openbeast /tmp/eval_x/o",
    "wget -qO- raw.githubusercontent.com/MaximilianKhan/openbeast/main/evals/refs/x",
    "cd /tmp/eval_x && curl -o out.txt https://pypi.org/simple/numpy/",
    "git ls-remote git@github.com:MaximilianKhan/openbeast.git",
])
def test_scan_flags_off_host_network(cmd):
    kinds, ev = S.call_kinds("bash", {"command": cmd})
    assert "network" in kinds and ev, (cmd, kinds)


@pytest.mark.parametrize("cmd", [
    "curl -s http://127.0.0.1:8080/health",
    "curl -s localhost:5000/api -d @payload.json",
    "for i in 1 2 3; do curl -s http://[::1]:9000/; done",
    "git clone /tmp/eval_bisect/repo /tmp/eval_bisect/copy",
    "cd /tmp/eval_x && make && ./a.out",
])
def test_scan_ignores_loopback_and_local_work(cmd):
    assert "network" not in S.call_kinds("bash", {"command": cmd})[0]


def test_scan_flags_any_claude_scratch_spelling():
    assert "scratch" in S.call_kinds("bash", {"command": "ls /tmp/claude-$(id -u)/"})[0]
    assert "scratch" in S.call_kinds("bash", {"command": "find /private/tmp/claude-* -name x"})[0]


def test_scan_flags_write_content_that_embeds_a_forbidden_path():
    for name, args in [
        ("write_file", {"path": "/tmp/eval_g/g.c",
                        "content": '#include "/Users/x/openbeast/evals/refs/122/gemm.c"\n'}),
        ("write_file", {"path": "/tmp/eval_r/m.rs",
                        "content": 'const R: &str = include_str!("/tmp/eval_r/c/evals/refs/m.rs");'}),
        ("edit_file", {"path": "/tmp/eval_z/a.zig", "old_string": "x",
                       "new_string": 'const d = @embedFile("/srv/clone/evals/tasks/1.json");'}),
        ("write_file", {"path": "/tmp/eval_p/s.py",
                        "content": 'print(open("/Users/x/.claude/projects/j.jsonl").read())'}),
    ]:
        kinds, ev = S.call_kinds(name, args)
        assert "embed" in kinds and ev, (args, kinds)
    ok = S.call_kinds("write_file", {"path": "/tmp/eval_p/s.c",
                                     "content": '#include <stdio.h>\n#include "/usr/include/math.h"\n'
                                                'FILE *f = fopen("/tmp/eval_p/in.txt", "r");'})
    assert ok == (set(), [])


def test_scan_contamination_and_attempts(tmp_path):
    p = tmp_path / "agent-1.jsonl"
    _transcript(p, [
        ("bash", {"command": "git clone https://github.com/MaximilianKhan/openbeast /tmp/eval_x/o"},
         "fatal: unable to access 'https://github.com/MaximilianKhan/openbeast/': Failed to "
         "connect to github.com port 443 after 3 ms: Couldn't connect to server\n(exit code 128)"),
    ])
    hits = S.scan_transcript(str(p))
    # 2026-11-18: the repo's own URL is also `selfrepo`; a failed connection
    # is still an attempt
    assert hits[0]["kinds"] == ["network", "selfrepo"] and hits[0]["status"] == "refused"
    assert not S.contaminated(hits)                     # an attempt the sandbox stopped
    q = tmp_path / "agent-2.jsonl"
    _transcript(q, [
        ("bash", {"command": "curl -sS https://example.com/answer"}, "42\n"),
        ("write_file", {"path": "/tmp/eval_g/g.c",
                        "content": '#include "/srv/c/evals/refs/122/gemm.c"\n'}, "Wrote 40 bytes"),
        ("read_file", {"path": "/tmp/eval_x/o/evals/refs/122/gemm.c"}, "int x;"),
    ])
    hits = S.scan_transcript(str(q))
    assert {h["iteration"]: h["status"] for h in hits} == {1: "read", 2: "read", 3: "read"}
    assert "evals" in hits[2]["kinds"]                  # a cloned key inside a task dir
    assert S.contaminated(hits)


# --- 6. API errors that ended the run ------------------------------------------------------

def _row(**kw):
    return {"id": "u", "passed": False, "elapsed_seconds": 300.0, "agent_exit_code": 0,
            "tokens_completion": 900, "validation_output": "FAIL: wrong", **kw}


def test_only_errors_that_ended_the_run_lose_a_failed_row():
    assert C.infra_reason(_row(api_errors=3, api_errors_tail=0)) is None      # recovered: a verdict
    assert C.infra_reason(_row(api_errors=3, api_errors_tail=2)) == "server_error"
    # legacy rows (every published board row) keep the old any-error rule
    assert C.infra_reason(_row(api_errors=3)) == "server_error"
    assert C.infra_reason({**_row(api_errors=3, api_errors_tail=2), "passed": True}) is None


def test_cacheable_follows_the_same_rule():
    import run_eval as R
    assert R.cacheable_result(_row(api_errors=2, api_errors_tail=0))
    assert not R.cacheable_result(_row(api_errors=2, api_errors_tail=1))
    assert not R.cacheable_result(_row(api_errors=2))
    assert not R.cacheable_result(_row(reason="served_model_changed"))


def test_api_tail_parsed_from_the_runner_and_from_a_killed_runner():
    import run_eval as R
    assert R._parse_api_errors_tail("TOKENS: prompt=1 completion=2 total=3\nAPI_ERRORS: 4\n"
                                    "API_ERRORS_TAIL: 0\n") == 0
    assert R._parse_api_errors_tail("API_ERRORS: 4\nAPI_ERRORS_TAIL: 3\n") == 3
    # killed before the summary: does the last [iter block hold an API error?
    assert R._parse_api_errors_tail("\n[iter 7/40]\n  API error: Connection refused\n") == 1
    assert R._parse_api_errors_tail("\n[iter 6/40]\n  API error: x\n\n[iter 7/40]\n  bash ...\n") == 0
    assert R._parse_api_errors_tail("no iteration markers") is None
    assert R._parse_reasoning_turns("REASONING_TURNS: 12\n") == 12
    assert R._parse_response_models('RESPONSE_MODELS: {"ds": 9}\n') == {"ds": 9}


def test_runner_reports_the_tail_the_models_and_the_reasoning(capsys):
    import types
    import runner
    runner._reset_run_stats()
    runner._RUN_STATS["api_errors_tail"] = 2
    msg = types.SimpleNamespace(content="x", reasoning_content="thinking...", tool_calls=None)
    runner._note_response(types.SimpleNamespace(model="deepseek-v4.1-flash"), msg)
    runner._note_response(types.SimpleNamespace(model="deepseek-v4.1-flash"),
                          types.SimpleNamespace(content="y", tool_calls=None))
    runner._print_token_summary(1, 2, 3, 0, 2)
    out = capsys.readouterr().out
    assert "API_ERRORS: 2\nAPI_ERRORS_TAIL: 0\nREASONING_TURNS: 1\n" in out
    assert 'RESPONSE_MODELS: {"deepseek-v4.1-flash": 2}' in out


# --- 7. the served model -------------------------------------------------------------------

class FakeServer:
    """/v1/models serving `self.model`; switchable mid-run."""

    def __init__(self, model: str):
        self.model = model
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    body = json.dumps({"object": "list", "data": [
                        {"id": outer.model, "object": "model", "owned_by": "fake"}]}).encode()
                    self.send_response(200)
                else:
                    body = b"{}"
                    self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def server():
    s = FakeServer("deepseek-v4.1-flash")
    yield s
    s.close()


def test_guard_waits_for_the_model_then_gives_up(server):
    import run_eval as R
    g = R.ServedModelGuard(server.base_url, "deepseek-v4.1-flash", wait_s=0.6, poll_s=0.05,
                           memo_s=0.0)
    assert g.wait_ok(lambda *_: None) is None
    server.model = "glm-5.3-flash"
    why = g.wait_ok(lambda *_: None)
    assert why and "glm-5.3-flash" in why
    # it comes back while we wait: the run continues
    threading.Timer(0.2, lambda: setattr(server, "model", "deepseek-v4.1-flash")).start()
    server.model = "glm-5.3-flash"
    assert g.wait_ok(lambda *_: None) is None


@pytest.fixture
def rmod(tmp_path, monkeypatch):
    """run_eval in-process on two trivial tasks, nothing slow captured."""
    import importlib
    for flag in ("BEAST_PACKS", "OPENBEAST_PACKS", "BEAST_ASSIST", "OPENBEAST_DIAGNOSTICS",
                 "BEAST_ESCALATE", "OPENBEAST_ESCALATE", "OPENBEAST_EVAL_GREEDY",
                 "OPENBEAST_EVAL_ENV_ERA", "OPENBEAST_AGENT_THINKING", "OPENBEAST_INFERENCE_MODEL",
                 "OPENBEAST_EVAL_REASONING_BUDGET"):
        monkeypatch.delenv(flag, raising=False)
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    R = importlib.import_module("run_eval")
    (tmp_path / "tasks").mkdir()
    for tid in ("01_a", "02_b"):
        (tmp_path / "tasks" / f"{tid}.json").write_text(json.dumps({
            "id": tid, "name": tid, "difficulty": "easy", "task": f"t {tid}", "max_iter": 2,
            "validation": {"type": "bash", "script": "true"}}))
    monkeypatch.setattr(R, "TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setattr(R, "RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(R, "capture_server_config", lambda *a, **k: {})
    monkeypatch.setattr(R, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(R, "capture_inference_engine_info", lambda: {})
    monkeypatch.setenv("OPENBEAST_EVAL_MODEL_WAIT_S", "0.3")
    # the env var is popped by monkeypatch on teardown even when run_eval sets it
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "")
    return R


def _agent_result(stdout=""):
    return {"exit_code": 0, "elapsed_seconds": 1.0, "stdout": stdout, "stderr": "",
            "tokens": {"prompt": 1, "completion": 2, "total": 3}}


def test_run_refuses_to_start_when_the_served_model_is_not_listed(rmod, server, monkeypatch):
    monkeypatch.setenv("OPENBEAST_INFERENCE_MODEL", "glm-5.3-flash")
    ran = []
    monkeypatch.setattr(rmod, "run_agent", lambda *a, **k: ran.append(1) or _agent_result())
    with pytest.raises(SystemExit) as e:
        rmod.run_eval(base_url=server.base_url, model_name="DeepSeek T", use_cache=False)
    assert "not the served model 'glm-5.3-flash'" in str(e.value) and ran == []


def test_a_model_swap_mid_run_is_an_infra_stop_that_banks_nothing(rmod, server, monkeypatch):
    monkeypatch.setenv("OPENBEAST_INFERENCE_MODEL", "deepseek-v4.1-flash")
    calls = []

    def agent(*a, **k):
        calls.append(1)
        server.model = "glm-5.3-flash"     # the gate swaps models during the first unit
        return _agent_result("REASONING_TURNS: 3\nRESPONSE_MODELS: {\"deepseek-v4.1-flash\": 3}\n")

    monkeypatch.setattr(rmod, "run_agent", agent)
    res = rmod.run_eval(base_url=server.base_url, model_name="DeepSeek T", use_cache=False)
    assert len(calls) == 1                                  # the second unit never started
    rows = res["tasks"]
    assert len(rows) == 1
    r = rows[0]
    assert r["reason"] == "served_model_changed" and r["passed"] is False
    assert r["validated_pass"] is True                     # the validator passed it; discarded
    assert C.infra_reason(r) == "served_model_changed"     # lost, rerun, never scored
    assert not rmod.cacheable_result(r)
    assert res["harness"]["served_model"] == "deepseek-v4.1-flash"
    assert r["reasoning_turns"] == 3 and r["response_models"] == {"deepseek-v4.1-flash": 3}


def test_a_steady_model_runs_every_unit(rmod, server, monkeypatch):
    monkeypatch.setenv("OPENBEAST_INFERENCE_MODEL", "deepseek-v4.1-flash")
    monkeypatch.setattr(rmod, "run_agent", lambda *a, **k: _agent_result())
    res = rmod.run_eval(base_url=server.base_url, model_name="DeepSeek T", use_cache=False)
    assert [r["passed"] for r in res["tasks"]] == [True, True]
    assert not any(r.get("reason") for r in res["tasks"])


# --- 8. thinking is sent explicitly under a declared budget ---------------------------------

@pytest.mark.parametrize("rb,thinking,want,source", [
    ("0", "", "off", "from the declared reasoning budget 0"),
    ("16384", "", "on", "from the declared reasoning budget 16384"),
    ("0", "on", "on", "requested"),            # --thinking wins
])
def test_declared_budget_sends_thinking_explicitly(rmod, server, monkeypatch, rb, thinking,
                                                   want, source):
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", thinking)
    seen = []
    monkeypatch.setattr(rmod, "run_agent", lambda task, *a, **k: seen.append(
        (k.get("env_overlay") or {}).get("OPENBEAST_AGENT_THINKING")) or _agent_result())
    res = rmod.run_eval(base_url=server.base_url, model_name="DeepSeek", use_cache=False,
                        reasoning_budget=rb)
    assert res["harness"]["agent_switches"]["thinking"] == want
    assert res["harness"]["thinking_source"] == source
    assert seen and set(seen) == {want}
    assert {r["agent_thinking"] for r in res["tasks"]} == {want}
    # derived, never written back: the next in-process run starts clean
    assert os.environ.get("OPENBEAST_AGENT_THINKING") == thinking


# --- 9. the admission probe ------------------------------------------------------------------

def _profile():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ob_profile_1118", ROOT / "evals" / "profile.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Completions(http.server.BaseHTTPRequestHandler):
    status = 200
    seen: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        type(self).seen.append(json.loads(self.rfile.read(n) or b"{}").get("model"))
        self.send_response(type(self).status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.mark.parametrize("status,ok", [(200, True), (401, False), (404, False), (429, False)])
def test_admission_requires_200_and_sends_the_served_id(status, ok):
    P = _profile()
    _Completions.status = status
    _Completions.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Completions)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        out = P.admission_check(f"http://127.0.0.1:{srv.server_address[1]}/v1", 3,
                                "deepseek-v4.1-flash")
    finally:
        srv.shutdown()
    assert (out is None) == ok, out
    assert set(_Completions.seen) == {"deepseek-v4.1-flash"}


# --- 10. the campaign's run_eval child -------------------------------------------------------

FAKE_CHILD = r'''
import os, signal, sys, time
def term(s, f):
    open(os.environ["FAKE_MARKER"], "w").write("TERM")
    sys.stdout.flush()
    os._exit(143)
signal.signal(signal.SIGTERM, term)
print("Results: " + os.environ["FAKE_RESULTS"], flush=True)
time.sleep(60)
'''


def test_sigterm_is_forwarded_to_the_child_and_the_child_is_recorded(tmp_path):
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "run_eval.py").write_text(FAKE_CHILD)
    env = {**os.environ, "FAKE_MARKER": str(tmp_path / "marker"),
           "FAKE_RESULTS": str(tmp_path / "results.json")}
    events = []

    def on_child(info):
        events.append(info)
        if info and info.get("results"):
            # the campaign process is told to stop (a `kill`, a closed terminal)
            threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM)).start()

    before = signal.getsignal(signal.SIGTERM)
    t0 = time.time()
    rc, _ = C.run_eval_once(sys.executable, str(tmp_path), [], env,
                            str(tmp_path / "arm.log"), "x", on_child=on_child)
    assert rc == 143 and time.time() - t0 < 30
    assert (tmp_path / "marker").read_text() == "TERM"     # the child got it and stopped
    assert events[0]["pid"] and events[0]["results"] is None
    assert events[1]["results"] == str(tmp_path / "results.json")
    assert events[-1] is None                                # cleared once it exited
    assert signal.getsignal(signal.SIGTERM) == before       # the handler is restored
    assert rc in C.INTERRUPTED_RCS


def test_run_refuses_while_a_recorded_child_lives(tmp_path):
    sdir = tmp_path / "state"
    sdir.mkdir()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                              "evals/run_eval.py"])
    try:
        C.save_state(str(sdir), {"version": C.STATE_VERSION, "campaign": "v5-thinking-first",
                                 "model_name": "M", "base_url": "http://127.0.0.1:9/v1",
                                 "jobs": 1, "slots": None, "tree": None, "arms": {},
                                 "baselines": {},
                                 "child": {"pid": child.pid, "arm": "T",
                                           "results": "/x/eval-m-1.json"}})
        st = C.load_state(str(sdir))
        assert C.live_child(st)["pid"] == child.pid
        rc = C.main(["run", "--model-name", "M", "--base-url", "http://127.0.0.1:9/v1",
                     "--tree", str(ROOT), "--python", sys.executable, "--state-dir", str(sdir),
                     "--skip-preflight"])
        assert rc == 2
    finally:
        child.kill()
        child.wait()
    assert C.live_child(C.load_state(str(sdir))) is None    # dead: no longer blocks


# --- 11. eval-host.sh: nothing the harness runs lives outside the prefix -----------------------

def test_eval_host_check_fails_tools_and_python_outside_the_prefix(tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_eval_host import Host, _states   # the fake-release sandbox
    host = Host(tmp_path)
    # the right go, but a user-writable copy outside the prefix (a Homebrew go)
    host.fake_tool("go", 'echo "go version go1.23.3 linux/amd64"')
    r = host.run("check", "--only", "go")
    assert r.returncode == 1 and _states(r.stdout) == {"go": "wrong"}, r.stdout
    assert "outside the eval prefix" in r.stdout
    r = host.run("setup", "--only", "go")                   # setup moves it into the prefix
    assert r.returncode == 0, r.stdout + r.stderr
    assert (host.prefix / "go-1.23.3" / "bin" / "go").is_file()
    r = host.run("check", "--only", "go")
    assert r.returncode == 0, r.stdout
    # an OPENBEAST_EVAL_PY outside the prefix (the old Homebrew-based venv)
    stray = tmp_path / "oldvenv" / "bin" / "python"
    stray.parent.mkdir(parents=True)
    stray.write_text("#!/bin/sh\n")
    stray.chmod(0o755)
    r = host.run("check", "--only", "zig,go,rust,system")
    assert "outside" not in r.stdout or "env" not in r.stdout    # --only: tools only
    r = host.run("check", OPENBEAST_EVAL_PY=str(stray))
    assert r.returncode == 1
    assert any(ln.startswith("outside  env") for ln in r.stdout.splitlines()), r.stdout
    import eval_host
    assert "outside" in eval_host.UNFIXABLE
