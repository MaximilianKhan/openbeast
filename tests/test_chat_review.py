#!/usr/bin/env python3
"""beast-chat — regressions for the 2026-09-29 adversarial review, plus the
features that shipped with the fixes (new-session sheet API, pause/resume,
PWA routes, notifications, transcript export, rig status).

Every case builds its own fixture: a temp ledger, temp .run, stub HTTP
servers on ephemeral loopback ports. No real stack, no GPU, no model, and no
server is left running (every stub is shut down in a fixture finalizer).

Run: python3 -m pytest tests/test_chat_review.py -q
"""
import json
import os
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (AGENTS, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import chat_server  # noqa: E402
import sessions  # noqa: E402
# The shared fixture (temp ledger + run dir + client factory). Imported by
# name so pytest does not collect test_chat_server's tests a second time.
from test_chat_server import Rig, drain, parse_sse, wait_state  # noqa: E402,F401


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", [])
    return Rig(tmp_path, monkeypatch)


def _ops(sid):
    path = sessions.inbox_path(sid)
    if not os.path.exists(path):
        return []
    return [json.loads(x) for x in open(path).read().splitlines() if x.strip()]


# ---------------------------------------------------------------------------
# chat-security-1: /send must not ack what the agent will never receive
# ---------------------------------------------------------------------------

def test_send_refuses_a_message_longer_than_the_agent_receives(rig):
    sid = rig.session(kind="agent", state="running")
    text = "A" * 10000 + " FINAL INSTRUCTION: do not delete anything"
    r = rig.client.post(f"/api/chat/sessions/{sid}/send", json={"text": text},
                        headers=rig.local)
    assert r.status_code == 413, r.text
    assert str(sessions.OP_MAX_TEXT) in r.json()["detail"]
    assert _ops(sid) == [], "a refused message still reached the inbox"


def test_a_whitespace_padded_message_is_stored_as_checked(rig):
    """The stripped length fits, the raw length does not: the message must
    arrive WHOLE (stored stripped), not be acked and then clipped."""
    sid = rig.session(kind="agent", state="running")
    text = "\n" * 500 + "A" * 3900 + " FINAL INSTRUCTION: do not delete anything"
    assert len(text) > sessions.OP_MAX_TEXT >= len(text.strip())
    r = rig.client.post(f"/api/chat/sessions/{sid}/send", json={"text": text},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    ops, _ = sessions.read_new_ops(sid, 0)
    assert ops[0]["text"] == text.strip()
    assert ops[0]["text"].endswith("do not delete anything")


def test_send_at_exactly_the_cap_arrives_whole(rig):
    """Negative control: the largest accepted message is delivered intact."""
    sid = rig.session(kind="agent", state="running")
    text = "B" * (sessions.OP_MAX_TEXT - 5) + " END."
    r = rig.client.post(f"/api/chat/sessions/{sid}/send", json={"text": text},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    ops, _ = sessions.read_new_ops(sid, 0)
    assert ops[0]["text"] == text           # not clipped, no truncation mark


# ---------------------------------------------------------------------------
# chat-security-2 / -8: the audit trail is bounded, attributable and complete
# ---------------------------------------------------------------------------

def test_audit_clips_the_claimed_login(rig):
    rig.operators("max@example.com")
    r = rig.anon.get("/api/chat/sessions/x",
                     headers={"Tailscale-User-Login": "a" * 15000})
    assert r.status_code == 404
    raw = rig.audit_raw()
    assert len(raw) < 2000, f"one denial wrote {len(raw)} bytes"
    row = rig.audit_rows()[-1]
    assert len(row["login"]) <= chat_server.AUDIT_FIELD_MAX + 1


def test_denials_are_sampled_per_peer_not_written_forever(rig, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_AUDIT_DENIALS_PER_MIN", "5")
    anon = rig.anon
    for _ in range(50):
        assert anon.get("/api/chat/sessions").status_code == 404
    denials = [r for r in rig.audit_rows() if r["outcome"] == "http_404"]
    assert len(denials) == 5, len(denials)
    # Negative control: a VERIFIED caller's rows are never sampled away.
    for _ in range(20):
        assert rig.client.get("/api/chat/sessions").status_code == 200
    ok = [r for r in rig.audit_rows()
          if r["outcome"] == "ok" and r["route"] == "GET /api/chat/sessions"]
    assert len(ok) == 20


def test_audit_file_rotates_past_its_size_cap(rig, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_AUDIT_MAX_MB", "0.001")   # ~1 KB
    c = rig.client
    for _ in range(40):
        c.get("/api/chat/sessions")
    live = rig.run / "chat-audit.jsonl"
    old = rig.run / "chat-audit.jsonl.1"
    assert old.exists(), "no rotation happened"
    assert live.stat().st_size < 4096
    assert (old.stat().st_mode & 0o777) == 0o600
    assert (live.stat().st_mode & 0o777) == 0o600


def test_forged_login_denial_is_marked_unverified_with_its_peer(rig):
    rig.operators("max@example.com")
    r = rig.anon.get("/api/chat/sessions",
                     headers={"Tailscale-User-Login": "eve@example.com"})
    assert r.status_code == 404
    bad = [x for x in rig.audit_rows() if x["outcome"] == "http_404"][-1]
    assert bad["verified"] is False
    assert bad["login"] == "eve@example.com"
    assert bad["peer"] == "127.0.0.1"
    # Negative control: the listed login is verified.
    assert rig.client.get("/api/chat/sessions").status_code == 200
    good = [x for x in rig.audit_rows() if x["outcome"] == "ok"][-1]
    assert good["verified"] is True and good["peer"] == "127.0.0.1"


def test_escalation_signals_are_audited(rig, monkeypatch):
    """A stop's SIGTERM/SIGKILL used to leave only a ledger summary."""
    sid = rig.session(kind="job", state="running")
    sent = []
    monkeypatch.setattr(chat_server, "signal_session",
                        lambda rec, sig: sent.append(sig) or True)
    # The job never goes away (signal_session is a stub), so the escalation
    # runs its whole course: SIGTERM now, SIGKILL after (kill - term) = 1 s.
    monkeypatch.setenv("OPENBEAST_CHAT_STOP_TERM_S", "0")
    monkeypatch.setenv("OPENBEAST_CHAT_STOP_KILL_S", "1")
    r = rig.client.post(f"/api/chat/sessions/{sid}/stop", json={},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        esc = [x for x in rig.audit_rows() if x["route"] == "stop escalation"]
        if any(x.get("signal") == "SIGKILL" for x in esc):
            break
        time.sleep(0.05)
    esc = [x for x in rig.audit_rows() if x["route"] == "stop escalation"]
    assert any(x.get("signal") == "SIGKILL" and x["delivered"] for x in esc), esc
    assert all(x["login"] == "local" and x["session"] == sid for x in esc)


def test_stream_close_is_audited_with_bytes_read(rig):
    sid = rig.session(kind="agent", state="done")
    drain(rig.client, sid)
    deadline = time.monotonic() + 5
    rows = []
    while time.monotonic() < deadline and not rows:
        rows = [x for x in rig.audit_rows() if x["outcome"] == "stream_close"]
        time.sleep(0.02)
    assert rows and rows[-1]["session"] == sid
    size = os.path.getsize(sessions.get(sid)["transcript"])
    assert rows[-1]["offset"] == size and rows[-1]["bytes"] == size


# ---------------------------------------------------------------------------
# A stand-in runner: registers itself exactly like agents/runner.py does
# (--session-id, title = task[:200]) and dumps what it was handed.
# ---------------------------------------------------------------------------

FAKE_RUNNER = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["FAKE_AGENTS"])
import sessions
argv = sys.argv[1:]
with open(os.environ["FAKE_RUNNER_OUT"], "w") as f:
    json.dump({"argv": argv, "env": dict(os.environ)}, f)
sid = argv[argv.index("--session-id") + 1]
task = argv[-1]
sessions.register(sid, kind="agent", title=task[:200], pid=os.getpid(),
                  transcript=argv[argv.index("--log-file") + 1])
time.sleep(float(os.environ.get("FAKE_RUNNER_SLEEP", "0.3")))
sessions.finalize(sid, "done", summary="fake runner")
'''


@pytest.fixture()
def fake_runner(tmp_path, monkeypatch):
    script = tmp_path / "fake_runner.py"
    script.write_text(FAKE_RUNNER)
    out = tmp_path / "runner-seen.json"
    monkeypatch.setattr(chat_server, "RUNNER_PATH", str(script))
    monkeypatch.setenv("FAKE_AGENTS", AGENTS)
    monkeypatch.setenv("FAKE_RUNNER_OUT", str(out))

    def seen(timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if out.exists() and out.stat().st_size:
                try:
                    return json.loads(out.read_text())
                except ValueError:
                    pass
            time.sleep(0.05)
        raise AssertionError("the fake runner never ran")
    return seen


def _start(rig, **body):
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json=body)
    assert r.status_code == 201, r.text
    return r.json()["session"]["id"]


# ---------------------------------------------------------------------------
# chat-security-6: a task is never parsed as a runner flag
# ---------------------------------------------------------------------------

def test_a_task_that_looks_like_a_flag_stays_the_task(rig, fake_runner,
                                                      tmp_path):
    import argparse
    sid = _start(rig, kind="agent", task="--task-file=/etc/hostname",
                 workdir=str(tmp_path))
    argv = fake_runner()["argv"]
    assert argv[-2:] == ["--", "--task-file=/etc/hostname"]
    # The runner's own parser shape (task nargs='*', --task-file): the value
    # must land in `task`, not in the option it imitates.
    p = argparse.ArgumentParser()
    p.add_argument("task", nargs="*")
    p.add_argument("--task-file", "-f")
    p.add_argument("--session-id")
    p.add_argument("--steer", action="store_true")
    p.add_argument("--log-file")
    p.add_argument("--workdir")
    p.add_argument("--max-iter")
    ns = p.parse_args(argv)
    assert ns.task == ["--task-file=/etc/hostname"] and ns.task_file is None
    assert wait_state(sid, "done")


def test_a_runner_that_never_registers_is_filed_failed(rig, tmp_path,
                                                       monkeypatch):
    fake = tmp_path / "boom.py"
    fake.write_text("import sys\nsys.stdout.write('usage: ...')\nsys.exit(0)\n")
    monkeypatch.setattr(chat_server, "RUNNER_PATH", str(fake))
    real = chat_server.reap_session

    def fast(session_id, proc, **kw):
        kw["annotate_timeout"] = 0.3
        return real(session_id, proc, **kw)
    monkeypatch.setattr(chat_server, "reap_session", fast)
    sid = _start(rig, kind="agent", task="-h", workdir=str(tmp_path))
    rec = wait_state(sid, "failed", timeout=10)
    # exit 0 without ever registering is NOT done: nothing ran.
    assert rec and rec["state"] == "failed", sessions.get(sid)
    assert "before registering" in rec["summary"]


# ---------------------------------------------------------------------------
# chat-browser-9: a caller's title reaches the ledger
# ---------------------------------------------------------------------------

def test_a_given_title_reaches_the_ledger(rig, fake_runner, tmp_path,
                                          monkeypatch):
    monkeypatch.setenv("FAKE_RUNNER_SLEEP", "2")
    sid = _start(rig, kind="agent", task="Count turns forever (stub model).",
                 title="stub agent A (API)", workdir=str(tmp_path))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (sessions.get(sid) or {}).get("title") == "stub agent A (API)":
            break
        time.sleep(0.05)
    assert sessions.get(sid)["title"] == "stub agent A (API)"


def test_without_a_title_the_task_names_the_session(rig, fake_runner,
                                                    tmp_path):
    """Negative control: nothing is merged over the runner's own title."""
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "plain task", "workdir": str(tmp_path)})
    assert r.json()["session"]["title"] == "plain task"
    sid = r.json()["session"]["id"]
    assert wait_state(sid, "done")
    assert sessions.get(sid)["title"] == "plain task"


# ---------------------------------------------------------------------------
# chat-browser-5 / chat-security-7: steers and stops are attributed
# ---------------------------------------------------------------------------

def test_send_writes_the_field_the_runner_reads(rig):
    sid = rig.session(kind="agent", state="running")
    key = rig.enroll("phone", "k-phone", scopes=["chat"])
    r = rig.client.post(f"/api/chat/sessions/{sid}/send",
                        json={"text": "hello"}, headers=key)
    assert r.status_code == 200, r.text
    op = _ops(sid)[-1]
    # agents/runner.py _apply_steer_ops: sender = op.get("from")
    assert op["from"] == "max@example.com (phone)"
    assert op["by"] == "max@example.com" and op["device"] == "phone"


def test_stop_records_who_asked_on_the_record(rig, monkeypatch):
    monkeypatch.setattr(chat_server, "start_escalation", lambda *a, **k: None)
    sid = rig.session(kind="agent", state="running")
    key = rig.enroll("phone", "k-phone", scopes=["chat"])
    r = rig.client.post(f"/api/chat/sessions/{sid}/stop", json={},
                        headers=key)
    assert r.status_code == 200, r.text
    meta = sessions.get(sid)["meta"]
    assert meta["stop_requested_by"] == "max@example.com"
    assert meta["stop_requested_device"] == "phone"
    assert _ops(sid)[-1]["op"] == "stop"
    assert _ops(sid)[-1]["from"] == "max@example.com (phone)"


# ---------------------------------------------------------------------------
# chat-security-9: spawned sessions do not inherit the stack's secrets
# ---------------------------------------------------------------------------

def test_a_job_does_not_see_the_stack_secrets(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_IDENTITY_JWT_SECRET", "SUPERSECRET_JWT")
    monkeypatch.setenv("OPENBEAST_MCPO_ADMIN_KEY", "ADMINKEY123")
    monkeypatch.setenv("OPENBEAST_HARMLESS_SETTING", "visible-ok")
    sid = _start(rig, kind="job", cmd="env", workdir=str(tmp_path))
    rec = wait_state(sid, "done", "failed")
    assert rec and rec["state"] == "done", rec
    out = open(rec["transcript"]).read()
    assert "SUPERSECRET_JWT" not in out and "ADMINKEY123" not in out
    # Negative control: the env is not simply empty.
    assert "OPENBEAST_HARMLESS_SETTING=visible-ok" in out
    # F-C5: the job knows its session.
    assert f"OPENBEAST_SESSION_ID={sid}" in out


def test_an_agent_keeps_only_its_inference_key(rig, fake_runner, tmp_path,
                                               monkeypatch):
    monkeypatch.setenv("OPENBEAST_IDENTITY_JWT_SECRET", "SUPERSECRET_JWT")
    monkeypatch.setenv("OPENBEAST_API_KEY", "inference-key")
    sid = _start(rig, kind="agent", task="t", workdir=str(tmp_path))
    env = fake_runner()["env"]
    assert "OPENBEAST_IDENTITY_JWT_SECRET" not in env
    assert env.get("OPENBEAST_API_KEY") == "inference-key"
    assert env.get("OPENBEAST_SESSION_ID") == sid


# ---------------------------------------------------------------------------
# chat-lifecycle-api-job-restart-lost: an API job outlives the server that
# started it, and still records the truth
# ---------------------------------------------------------------------------

def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _RealServer:
    """agents/chat_server.py as a real process on an ephemeral port, with
    every directory in tmp. Killed by its recorded pid, never by pattern."""

    def __init__(self, tmp_path, extra_env=None):
        import subprocess
        self.port = _free_port()
        self.run = tmp_path / "srv-run"
        self.sdir = tmp_path / "srv-sessions"
        self.logs = tmp_path / "srv-logs"
        for d in (self.run, self.sdir, self.logs):
            d.mkdir(exist_ok=True)
        env = dict(os.environ)
        env.update({
            "OPENBEAST_CHAT_PORT": str(self.port),
            "OPENBEAST_CHAT_BIND": "127.0.0.1",
            "OPENBEAST_CHAT_RUN_DIR": str(self.run),
            "OPENBEAST_SESSIONS_DIR": str(self.sdir),
            "OPENBEAST_CHAT_LOG_DIR": str(self.logs),
            "OPENBEAST_CHAT_SCOPE": "off",
            "OPENBEAST_CHAT_POLL_MS": "50",
        })
        env.pop("OPENBEAST_CHAT_NOTIFY_URL", None)
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            ["nice", "-n", "19", sys.executable,
             os.path.join(AGENTS, "chat_server.py")],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                self.token = (self.run / "chat-local.token").read_text().strip()
                if self.request("GET", "/api/chat/health")[0] == 200:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        self.kill()
        raise AssertionError("chat_server did not come up")

    def request(self, method, path, body=None):
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
            headers={"X-OpenBeast-Local": getattr(self, "token", ""),
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, None

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()        # SIGKILL: no shutdown hooks, like a crash
        self.proc.wait(timeout=10)


def _raw_state(sdir, sid):
    try:
        return json.load(open(os.path.join(sdir, f"{sid}.json"))).get("state")
    except (OSError, ValueError):
        return None


def test_an_api_job_that_outlives_the_server_still_records_done(
        tmp_path, monkeypatch):
    srv = _RealServer(tmp_path)
    pgids = []
    try:
        code, d = srv.request("POST", "/api/chat/sessions", {
            "kind": "job", "cmd": "sleep 2; echo finished-ok",
            "workdir": str(tmp_path)})
        assert code == 201, d
        sid = d["session"]["id"]
        assert _raw_state(srv.sdir, sid) == "running"
        pgids.append(json.load(open(srv.sdir / f"{sid}.json"))["pgid"])
        # Stop one, too, and kill the server before it can escalate.
        code, d2 = srv.request("POST", "/api/chat/sessions", {
            "kind": "job", "cmd": "sleep 60", "workdir": str(tmp_path)})
        assert code == 201
        stop_id = d2["session"]["id"]
        pgids.append(json.load(open(srv.sdir / f"{stop_id}.json"))["pgid"])
        code, _ = srv.request("POST", f"/api/chat/sessions/{stop_id}/stop", {})
        assert code == 200
        srv.kill()                                   # the "restart"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if (_raw_state(srv.sdir, sid) != "running"
                    and _raw_state(srv.sdir, stop_id) != "running"):
                break
            time.sleep(0.1)
        assert _raw_state(srv.sdir, sid) == "done", \
            "an exit-0 job that outlived the server was not recorded done"
        assert _raw_state(srv.sdir, stop_id) == "stopped"
        log = open(json.load(open(srv.sdir / f"{sid}.json"))["transcript"]).read()
        assert "finished-ok" in log
    finally:
        srv.kill()
        import signal as _sig
        for pg in pgids:
            try:
                if int(pg) > 1:
                    os.killpg(int(pg), _sig.SIGKILL)
            except (OSError, ValueError, TypeError):
                pass


def test_secret_name_rule_matches_the_bash_tool(monkeypatch):
    """chat_server's fallback copy must agree with tools._scrubbed_env."""
    import tools
    names = ["OPENBEAST_API_KEY", "OPENBEAST_IDENTITY_JWT_SECRET",
             "WEBUI_ADMIN_PASSWORD", "LLAMA_API_KEY", "SEARXNG_SECRET",
             "OPENAI_API_KEY", "HF_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
             "ANTHROPIC_API_KEY", "OPENBEAST_CHAT_PORT", "HOME", "PATH",
             "MY_TOKEN", "OPENBEAST_SESSION_TOKEN", "LLAMA_PORT"]
    for n in names:
        monkeypatch.setenv(n, "v")
    kept = tools._scrubbed_env()
    for n in names:
        assert chat_server.is_secret_env_name(n) == (n not in kept), n


# ---------------------------------------------------------------------------
# chat-lifecycle-console-scroll-thrash / chat-browser-11 (server half): a
# fresh open can start at the tail instead of replaying megabytes
# ---------------------------------------------------------------------------

def _big_job(rig, n=5000):
    lines = [f"line {i:06d} " + "x" * 40 for i in range(n)]
    return rig.session(kind="job", state="done", lines=lines)


def test_tail_starts_on_a_whole_line_near_the_end(rig):
    sid = _big_job(rig)
    r = rig.client.get(f"/api/chat/sessions/{sid}/events?tail=1000")
    assert r.status_code == 200
    frames = parse_sse(r.text)
    hello = frames[0]["data"]
    logs = [f["data"]["line"] for f in frames if f["event"] == "log"]
    assert hello["skipped"] > 0 and hello["from"] == hello["skipped"]
    assert 10 <= len(logs) <= 25, len(logs)
    assert logs[-1].startswith("line 004999")
    assert all(x.startswith("line ") and len(x) == len(logs[-1]) for x in logs)
    assert frames[-1]["event"] == "end"


def test_tail_is_ignored_for_a_resume_or_an_explicit_from(rig):
    """Negative controls: a reconnect and from= keep their exact semantics."""
    sid = _big_job(rig, n=200)
    full = parse_sse(rig.client.get(
        f"/api/chat/sessions/{sid}/events?from=0&tail=100").text)
    assert len([f for f in full if f["event"] == "log"]) == 200
    assert full[0]["data"]["skipped"] == 0
    r = rig.client.get(f"/api/chat/sessions/{sid}/events?tail=100",
                       headers={"Last-Event-ID": "0"})
    got = parse_sse(r.text)
    assert len([f for f in got if f["event"] == "log"]) == 200


def test_tail_larger_than_the_file_replays_everything(rig):
    sid = _big_job(rig, n=50)
    got = parse_sse(rig.client.get(
        f"/api/chat/sessions/{sid}/events?tail=99999999").text)
    assert len([f for f in got if f["event"] == "log"]) == 50
    assert rig.client.get(
        f"/api/chat/sessions/{sid}/events?tail=-1").status_code == 400


# ---------------------------------------------------------------------------
# chat-security-5: the login header can be confined to a 0600 Unix socket
# ---------------------------------------------------------------------------

def test_login_from_unix_refuses_a_tcp_loopback_login(rig, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_LOGIN_FROM", "unix")
    r = rig.client.get("/api/chat/sessions")      # 127.0.0.1 + login header
    assert r.status_code == 404
    # ...while the locality token (a secret, not a claim) still works
    assert rig.anon.get("/api/chat/sessions",
                        headers=rig.local).status_code == 200


def test_default_mode_still_honours_a_loopback_login(rig):
    """Negative control: nothing changes unless the operator opts in."""
    assert rig.client.get("/api/chat/sessions").status_code == 200


def _uds_get(path, url, headers):
    import http.client
    import socket as _socket

    class UDSConn(http.client.HTTPConnection):
        def connect(self):
            self.sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect(path)
    c = UDSConn("beast.example.ts.net")
    c.request("GET", url, headers=headers)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def test_real_server_takes_logins_only_on_its_unix_socket(tmp_path):
    import stat as _st
    sock_path = str(tmp_path / "sockdir" / "chat.sock")
    srv = _RealServer(tmp_path, {"OPENBEAST_CHAT_SOCKET": sock_path,
                                 "OPENBEAST_CHAT_LOGIN_FROM": "unix"})
    try:
        st = os.stat(sock_path)
        assert _st.S_ISSOCK(st.st_mode) and (st.st_mode & 0o777) == 0o600
        login = {"Tailscale-User-Login": "max@example.com",
                 "Host": "beast.example.ts.net"}
        code, body = _uds_get(sock_path, "/api/chat/sessions", login)
        assert code == 200, body
        import urllib.error
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/chat/sessions",
            headers={"Tailscale-User-Login": "max@example.com"})
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("TCP loopback login was accepted")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.kill()
