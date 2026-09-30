#!/usr/bin/env python3
"""beast-chat features (2026-09-30): the new-session sheet's API (presets,
dry run, models), pause/resume, the PWA shell, push notifications, transcript
export to beast-artifact, and the rig status strip.

Everything builds its own world: a temp ledger and .run, stub HTTP servers on
ephemeral loopback ports (ntfy, beast-slot, llama health), and — for the
export round trip — a REAL agents/artifact_server.py process with its store
in tmp. Every server is stopped in a finalizer; nothing touches the real
stack, the GPU, or port 8080.

Run: python3 -m pytest tests/test_chat_features.py -q
"""
import json
import os
import struct
import subprocess
import sys
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (AGENTS, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import chat_server  # noqa: E402
import sessions  # noqa: E402
from test_chat_server import LISTED, Rig, wait_state  # noqa: E402


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", [])
    for name in ("OPENBEAST_CHAT_NOTIFY_URL", "OPENBEAST_CHAT_NOTIFY_ON",
                 "OPENBEAST_CHAT_NOTIFY_TOKEN_FILE", "BEAST_ARTIFACT",
                 "OPENBEAST_BEAST_ARTIFACT"):
        monkeypatch.delenv(name, raising=False)
    return Rig(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------
# A stub HTTP server that records what it was sent
# ---------------------------------------------------------------------------

class Stub:
    def __init__(self, status=200, body=b"{}", ctype="application/json"):
        self.calls = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def _answer(self):
                n = int(self.headers.get("Content-Length") or 0)
                stub.calls.append({"method": self.command, "path": self.path,
                                   "headers": dict(self.headers),
                                   "body": self.rfile.read(n) if n else b""})
                self.send_response(stub.status)
                self.send_header("Content-Type", stub.ctype)
                self.send_header("Content-Length", str(len(stub.body)))
                self.end_headers()
                self.wfile.write(stub.body)

            do_GET = do_POST = _answer

            def log_message(self, *a):
                pass

        self.status, self.body, self.ctype = status, body, ctype
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def stub():
    made = []

    def make(**kw):
        s = Stub(**kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def _ops(sid):
    path = sessions.inbox_path(sid)
    if not os.path.exists(path):
        return []
    return [json.loads(x) for x in open(path).read().splitlines() if x.strip()]


# ---------------------------------------------------------------------------
# F-C2 pause / resume
# ---------------------------------------------------------------------------

def test_pause_and_resume_write_the_ops_the_runner_understands(rig):
    sid = rig.session(kind="agent", state="running")
    for op in ("pause", "resume"):
        r = rig.client.post(f"/api/chat/sessions/{sid}/{op}", json={},
                            headers=rig.local)
        assert r.status_code == 200, r.text
        assert r.json()["op"] == op
    ops = _ops(sid)
    assert [o["op"] for o in ops] == ["pause", "resume"]
    # The runner's own fold (read-only use of the era-locked module): the
    # names must be the ones it acts on, not merely strings we like.
    import runner
    logged = []
    st = runner._apply_steer_ops(ops[:1], [], logged.append)
    assert st["paused"] is True
    st = runner._apply_steer_ops(ops[1:], [], logged.append, paused=True)
    assert st["paused"] is False
    assert all(e.get("from") == "local" for e in logged)


def test_pause_is_refused_for_jobs_and_finished_sessions(rig):
    job = rig.session(kind="job", state="running")
    r = rig.client.post(f"/api/chat/sessions/{job}/pause", json={},
                        headers=rig.local)
    assert r.status_code == 409 and "jobs cannot pause" in r.json()["detail"]
    done = rig.session(kind="agent", state="done")
    r = rig.client.post(f"/api/chat/sessions/{done}/resume", json={},
                        headers=rig.local)
    assert r.status_code == 409
    assert _ops(job) == [] and _ops(done) == []


def test_pause_needs_a_write_credential(rig):
    sid = rig.session(kind="agent", state="running")
    r = rig.client.post(f"/api/chat/sessions/{sid}/pause", json={})
    assert r.status_code == 404                    # login only: a reader
    assert _ops(sid) == []


# ---------------------------------------------------------------------------
# F-C1 presets, dry run, models
# ---------------------------------------------------------------------------

def _write_presets(rig, doc, mode=0o600):
    path = rig.run / "chat-presets.json"
    path.write_text(json.dumps(doc))
    os.chmod(path, mode)
    return path


def test_presets_are_listed_only_from_a_private_file(rig, tmp_path):
    doc = {"presets": [
        {"name": "hello", "title": "Say hello", "cmd": "echo hello-preset",
         "workdir": str(tmp_path), "description": "d"},
        {"name": "bad name!", "cmd": "x"},             # invalid: dropped
        {"name": "nocmd"},                             # invalid: dropped
    ]}
    _write_presets(rig, doc)
    d = rig.client.get("/api/chat/presets").json()
    assert [p["name"] for p in d["presets"]] == ["hello"]
    assert "problem" not in d
    # group/world-readable: ignored, with the reason
    _write_presets(rig, doc, mode=0o644)
    d = rig.client.get("/api/chat/presets").json()
    assert d["presets"] == [] and "0600" in d["problem"]


def test_a_symlinked_preset_file_is_refused(rig, tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"presets": [{"name": "x", "cmd": "id"}]}))
    os.chmod(real, 0o600)
    (rig.run / "chat-presets.json").symlink_to(real)
    d = rig.client.get("/api/chat/presets").json()
    assert d["presets"] == [] and "symlink" in d["problem"]


def test_starting_a_preset_runs_the_operators_command(rig, tmp_path):
    _write_presets(rig, {"presets": [
        {"name": "hello", "title": "Say hello", "cmd": "echo hello-preset",
         "workdir": str(tmp_path)}]})
    r = rig.client.post("/api/chat/sessions", headers=rig.local,
                        json={"preset": "hello"})
    assert r.status_code == 201, r.text
    sid = r.json()["session"]["id"]
    rec = wait_state(sid, "done", "failed")
    assert rec["state"] == "done" and rec["kind"] == "job"
    assert rec["title"] == "Say hello" and rec["workdir"] == str(tmp_path)
    assert "hello-preset" in open(rec["transcript"]).read()
    # a preset and a raw command together are refused; so is an unknown one
    assert rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "preset": "hello", "cmd": "id"}).status_code == 400
    assert rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "preset": "nope"}).status_code == 400


def test_dry_run_echoes_the_exact_argv_and_spawns_nothing(rig, tmp_path):
    before = set(os.listdir(rig.sdir))
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "-v fix the build", "max_iter": 7,
        "workdir": str(tmp_path), "dry_run": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["dry_run"] is True and d["kind"] == "agent"
    argv = d["argv"]
    assert argv[0] == sys.executable and argv[1] == chat_server.RUNNER_PATH
    assert argv[-2:] == ["--", "-v fix the build"]
    assert "--steer" in argv and argv[argv.index("--max-iter") + 1] == "7"
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "job", "cmd": "make test", "workdir": str(tmp_path),
        "dry_run": True})
    d = r.json()
    assert d["argv"] == ["/bin/bash", "-lc", "make test"]
    assert d["wrapper"] == "scripts/job.sh __supervise"
    time.sleep(0.3)
    assert set(os.listdir(rig.sdir)) == before, "a dry run spawned something"
    # A dry run needs the same write credential as a start.
    assert rig.client.post("/api/chat/sessions", json={
        "kind": "job", "cmd": "id", "dry_run": True}).status_code == 404


def test_start_refuses_a_command_changed_since_review(rig, tmp_path):
    """The confirm dialog pins the start: a preset edited on disk between
    Review and Start is refused, not silently run."""
    _write_presets(rig, {"presets": [
        {"name": "hello", "cmd": "echo reviewed", "workdir": str(tmp_path)}]})
    d = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "preset": "hello", "dry_run": True}).json()
    sha = d["plan_sha256"]
    # Same body reviewed twice: same digest, although each plan mints a new
    # session id (the per-start values are not part of it).
    assert rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "preset": "hello", "dry_run": True}).json()["plan_sha256"] == sha
    _write_presets(rig, {"presets": [
        {"name": "hello", "cmd": "echo SWAPPED", "workdir": str(tmp_path)}]})
    before = set(os.listdir(rig.sdir))
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "preset": "hello", "confirm_sha256": sha})
    assert r.status_code == 409 and "review again" in r.json()["detail"]
    time.sleep(0.2)
    assert set(os.listdir(rig.sdir)) == before, "a refused start spawned"
    # Negative control: the digest of what WOULD run now starts it.
    agent = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "x", "workdir": str(tmp_path),
        "dry_run": True}).json()
    assert agent["plan_sha256"] != sha
    now = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "preset": "hello", "dry_run": True}).json()["plan_sha256"]
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "preset": "hello", "confirm_sha256": now})
    assert r.status_code == 201, r.text
    wait_state(r.json()["session"]["id"], "done", "failed")


def test_models_come_from_beast_slot(rig, stub, monkeypatch):
    s = stub(body=json.dumps({"model": {"id": "qwen38-27b-q5",
                                        "ctx": 262144}}).encode())
    monkeypatch.setenv("OPENBEAST_CHAT_SLOT_URL", s.url + "/api/slot")
    d = rig.client.get("/api/chat/models").json()
    assert d == {"models": ["qwen38-27b-q5"], "default": "qwen38-27b-q5",
                 "source": "beast-slot"}
    # unreachable slot: an empty list, not an error
    monkeypatch.setenv("OPENBEAST_CHAT_SLOT_URL", "http://127.0.0.1:9/api/slot")
    assert rig.client.get("/api/chat/models").json()["models"] == []
    assert rig.anon.get("/api/chat/models").status_code == 404


# ---------------------------------------------------------------------------
# F-C3 PWA shell
# ---------------------------------------------------------------------------

def _png_size(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    length, tag = struct.unpack(">I4s", data[8:16])
    assert tag == b"IHDR"
    w, h = struct.unpack(">II", data[16:24])
    # the CRC of IHDR must be right, or iOS drops the icon
    crc = struct.unpack(">I", data[16 + length:20 + length])[0]
    assert crc == zlib.crc32(data[12:16 + length]) & 0xffffffff
    return w, h


def test_manifest_is_a_route_with_png_icons(rig):
    r = rig.anon.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["start_url"] == "/#/" and m["display"] == "standalone"
    pngs = [i for i in m["icons"] if i["type"] == "image/png"]
    assert {i["sizes"] for i in pngs} == {"192x192", "512x512"}
    for size in (180, 192, 512):
        r = rig.anon.get(f"/icon-{size}.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert _png_size(r.content) == (size, size)


def test_console_links_the_manifest_route_and_a_png_touch_icon(rig):
    r = rig.anon.get("/")
    html = r.text
    assert 'rel="manifest" href="/manifest.webmanifest"' in html
    assert 'rel="apple-touch-icon" href="/icon-180.png"' in html
    assert "data:application/json" not in html
    csp = r.headers["content-security-policy"]
    assert "manifest-src 'self';" in csp and "worker-src 'self'" in csp
    assert "data:" not in csp.split("manifest-src", 1)[1].split(";", 1)[0]


def test_service_worker_never_caches_the_api(rig):
    r = rig.anon.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    js = r.text
    assert "url.pathname.startsWith('/api/')) return" in js
    assert "text/event-stream" in js
    shell = json.loads(js.split("const SHELL = ", 1)[1].split(";", 1)[0])
    assert all(not p.startswith("/api") for p in shell)
    assert "/" in shell and "/icon-180.png" in shell


# ---------------------------------------------------------------------------
# F-C4 notifications
# ---------------------------------------------------------------------------

def _notifier(rig, url, **kw):
    kw.setdefault("state_path", str(rig.run / "notify-state.json"))
    kw.setdefault("public_url", "https://beast.example.ts.net:8445")
    kw.setdefault("min_interval", 0.0)
    return chat_server.Notifier(url, **kw)


def test_first_pass_records_history_without_notifying(rig, stub):
    s = stub()
    rig.session(kind="job", state="failed")
    n = _notifier(rig, s.url + "/topic")
    assert n.tick() == []
    assert s.calls == []
    st = json.loads((rig.run / "notify-state.json").read_text())
    assert (rig.run / "notify-state.json").stat().st_mode & 0o777 == 0o600
    assert list(st["sessions"].values()) == ["failed"]


def test_a_session_ending_fires_title_state_and_link_only(rig, stub, tmp_path):
    s = stub()
    token = tmp_path / "ntfy.token"
    token.write_text("tk_secret_123\n")
    sid = rig.session(kind="job", state="running", title="nightly build")
    rig.append(sid, "SECRET TRANSCRIPT LINE")
    n = _notifier(rig, s.url + "/beast", token_file=str(token))
    assert n.tick() == []                              # sees it running
    sessions.finalize(sid, "failed", summary="exit 2")
    assert n.tick() == [sid]
    call = s.calls[-1]
    body = call["body"].decode()
    assert call["path"] == "/beast" and call["method"] == "POST"
    assert "nightly build" in body and "failed" in body
    assert "SECRET TRANSCRIPT LINE" not in body and "exit 2" not in body
    h = {k.lower(): v for k, v in call["headers"].items()}
    assert h["title"] == "beast-chat: job failed"
    assert h["click"] == f"https://beast.example.ts.net:8445/#/s/{sid}"
    assert h["authorization"] == "Bearer tk_secret_123"
    assert h["priority"] == "high"
    assert n.tick() == []                              # once, not every pass


def _job_with_command(title, command):
    sid = sessions.new_id("job")
    sessions.register(sid, kind="job", title=title, pid=os.getpid(),
                      pgid=os.getpid(), meta={"command": command})
    return sid


def _notified_text(s):
    call = s.calls[-1]
    return call["body"].decode() + json.dumps(call["headers"])


def test_a_job_command_title_is_never_sent_to_the_notify_url(rig, stub):
    """A job's default title is its shell command; the notify URL is often
    public ntfy.sh. The command's secrets must not leave the rig."""
    s = stub()
    cmd = "HF_TOKEN=hf_abcdefSECRET123 python download.py"
    sid = _job_with_command(cmd[:80], cmd)
    n = _notifier(rig, s.url + "/t")
    n.tick()
    sessions.finalize(sid, "failed")
    assert n.tick() == [sid]
    text = _notified_text(s)
    assert "hf_abcdefSECRET123" not in text and "download.py" not in text
    assert sid[-8:] in text and "failed" in text
    # job.sh shape: argv list, title = first word
    sid2 = _job_with_command("curl", ["curl", "-H", "Authorization: Bearer "
                                      "abcdefgh12345678", "https://x"])
    n.tick()
    sessions.finalize(sid2, "failed")
    n.tick()
    assert "abcdefgh12345678" not in _notified_text(s)


def test_a_named_title_is_scrubbed_but_still_sent(rig, stub):
    s = stub()
    sid = _job_with_command("nightly build", "make all")   # negative control
    agent = sessions.new_id("agent")
    sessions.register(agent, kind="agent", pid=os.getpid(), pgid=os.getpid(),
                      title="deploy with API_KEY=sk-abc123def456 and "
                            "Authorization: Bearer tok_abcdefgh1234")
    n = _notifier(rig, s.url + "/t")
    n.tick()
    sessions.finalize(sid, "failed")
    sessions.finalize(agent, "failed")
    assert set(n.tick()) == {sid, agent}
    bodies = [c["body"].decode() + json.dumps(c["headers"]) for c in s.calls]
    assert any("nightly build" in b for b in bodies)
    joined = "".join(bodies)
    assert "sk-abc123def456" not in joined and "tok_abcdefgh1234" not in joined
    assert any("deploy with" in b for b in bodies)


def test_notify_on_filters_states(rig, stub):
    s = stub()
    sid = rig.session(kind="job", state="running")
    n = _notifier(rig, s.url + "/t", on=("failed",))
    n.tick()
    sessions.finalize(sid, "done")
    assert n.tick() == [] and s.calls == []


def test_a_job_that_ended_while_the_server_was_down_still_notifies(rig, stub):
    s = stub()
    sid = rig.session(kind="job", state="running")
    _notifier(rig, s.url + "/t").tick()                 # server 1 saw it run
    sessions.finalize(sid, "done")                      # ...then went down
    late = rig.session(kind="job", state="done")        # started+ended while down
    n2 = _notifier(rig, s.url + "/t")                   # server 2
    fired = n2.tick()
    assert sid in fired and late in fired


def test_a_burst_is_capped_with_one_summary(rig, stub):
    s = stub()
    ids = [rig.session(kind="job", state="running") for _ in range(13)]
    n = _notifier(rig, s.url + "/t", burst=10)
    n.tick()
    for sid in ids:
        sessions.finalize(sid, "failed")
    assert len(n.tick()) == 10
    assert len(s.calls) == 11
    assert "3 more" in s.calls[-1]["body"].decode()


def test_a_failed_send_is_retried_next_tick(rig, stub):
    """A-chat-2: the ntfy endpoint being briefly down must not lose the
    failed-job alert — the snapshot keeps it pending until it is delivered."""
    results = [False, True]
    sent = []

    def poster(body, headers):
        ok = results.pop(0) if results else True
        sent.append((body, ok))
        return ok

    sid = rig.session(kind="job", state="running", title="nightly")
    n = _notifier(rig, "http://127.0.0.1:9/t", poster=poster)
    assert n.tick() == []
    sessions.finalize(sid, "failed")
    assert n.tick() == []                     # endpoint down
    assert n.tick() == [sid]                  # retried once it is back
    assert [ok for _, ok in sent] == [False, True]
    assert n.tick() == [] and len(sent) == 2  # and only once after that


def test_an_undelivered_alert_is_given_up_after_the_max_age(rig, monkeypatch):
    sent = []
    sid = rig.session(kind="job", state="running")
    n = _notifier(rig, "http://127.0.0.1:9/t",
                  poster=lambda b, h: sent.append(b) and False)
    n.tick()
    sessions.finalize(sid, "failed")
    late = time.time() + chat_server.NOTIFY_RETRY_MAX_AGE + 60
    assert n.tick(now=late) == [] and len(sent) == 1
    assert n.tick(now=late + 1) == [] and len(sent) == 1


def test_a_failed_post_never_logs_the_token(rig, tmp_path, capsys):
    token = tmp_path / "tok"
    token.write_text("tk_do_not_print")
    n = _notifier(rig, "http://127.0.0.1:9/t", token_file=str(token))
    assert n.send(title="x", body="y") is False
    err = capsys.readouterr().err
    assert "failed" in err and "tk_do_not_print" not in err


def test_notify_test_endpoint(rig, stub, monkeypatch):
    r = rig.client.post("/api/chat/notify/test", json={}, headers=rig.local)
    assert r.status_code == 409 and "CHAT_NOTIFY_URL" in r.json()["detail"]
    s = stub()
    monkeypatch.setenv("OPENBEAST_CHAT_NOTIFY_URL", s.url + "/t")
    monkeypatch.setenv("OPENBEAST_CHAT_PUBLIC_URL", "https://b.example:8445")
    rig._app = None                                    # re-read the env
    assert rig.client.post("/api/chat/notify/test",
                           json={}).status_code == 404   # a reader cannot
    r = rig.client.post("/api/chat/notify/test", json={}, headers=rig.local)
    assert r.status_code == 200 and r.json()["sent"] is True
    assert s.calls[-1]["headers"]["Title"] == "beast-chat: test notification"


def test_notifier_is_off_without_a_url_or_with_a_bad_one(rig, monkeypatch):
    assert chat_server.Notifier.from_env(str(rig.run), 3003) is None
    monkeypatch.setenv("OPENBEAST_CHAT_NOTIFY_URL", "file:///etc/passwd")
    assert chat_server.Notifier.from_env(str(rig.run), 3003) is None


# ---------------------------------------------------------------------------
# F-C6 export a transcript as an artifact
# ---------------------------------------------------------------------------

HOSTILE = [
    {"type": "start", "task": "check the <b>build</b>"},
    {"type": "assistant", "content": "<script>alert('x')</script> done"},
    {"type": "tool_call", "name": "bash", "args": {"command": "env"},
     "result": "OPENBEAST_API_KEY=%(key)s\nDB_PASSWORD=hunter2\n"
               "Authorization: Bearer abcdefghijklmnop\nok"},
    {"type": "steer", "op": "say", "text": "go on", "from": "max (phone)"},
    {"type": "done", "summary": "all good", "iterations": 1},
]


def test_export_is_refused_while_beast_artifact_is_off(rig):
    sid = rig.session(kind="agent", state="done")
    r = rig.client.post(f"/api/chat/sessions/{sid}/export", json={},
                        headers=rig.local)
    assert r.status_code == 409 and "BEAST_ARTIFACT" in r.json()["detail"]


def test_export_needs_a_write_credential(rig, monkeypatch):
    monkeypatch.setenv("BEAST_ARTIFACT", "true")
    sid = rig.session(kind="agent", state="done")
    assert rig.client.post(f"/api/chat/sessions/{sid}/export",
                           json={}).status_code == 404


def test_rendered_export_is_escaped_and_scrubbed(rig, monkeypatch):
    monkeypatch.setenv("OPENBEAST_API_KEY", "sk-live-9f8e7d6c5b4a")
    lines = list(HOSTILE)
    lines[2] = dict(HOSTILE[2], result=HOSTILE[2]["result"] % {
        "key": "sk-live-9f8e7d6c5b4a"})
    sid = rig.session(kind="agent", state="done", lines=lines)
    page = chat_server.render_transcript_html(sessions.get(sid))
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "<b>build</b>" not in page
    assert "sk-live-9f8e7d6c5b4a" not in page
    assert "hunter2" not in page and "abcdefghijklmnop" not in page
    assert "max (phone) → agent" in page
    assert "<script" not in page.lower()        # nothing executable at all
    assert f'content="{sid}"' in page


@pytest.mark.parametrize("leak, secret", [
    ('{"api_key": "sk-abc123def456"}', "sk-abc123def456"),
    ('curl -H "X-OpenBeast-Device-Key: kdev_abcdef123456"', "kdev_abcdef123456"),
    ("X-OpenBeast-Local: 0123456789abcdef0123", "0123456789abcdef0123"),
    ("llama-server --api-key sk-abcdefgh123 -m x", "sk-abcdefgh123"),
    ("hf download --token=hf_zyxwvut987654", "hf_zyxwvut987654"),
    ("Authorization: token ghp_abcdef123456", "ghp_abcdef123456"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
])
def test_scrub_catches_quoted_hyphenated_flag_and_scheme_forms(leak, secret):
    out = chat_server.scrub_secrets(leak)
    assert secret not in out and "[redacted]" in out


def test_scrub_leaves_ordinary_text_alone():
    for text in ("--max-iter 200 --model qwen", "512 tokens per second",
                 '{"title": "nightly build"}', "keys are fine: yes",
                 "see https://example.com/a:b@c no creds", "task-runner ok",
                 "pip install scikit-learn", "desk-lamp-controller: on"):
        assert chat_server.scrub_secrets(text) == text


@pytest.fixture()
def fresh_literals(monkeypatch, tmp_path):
    """No cached rig literals, an empty run dir, and the cache reset after."""
    for var in ("OPENBEAST_RUN_DIR", "OPENBEAST_CHAT_RUN_DIR"):
        monkeypatch.setenv(var, str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    monkeypatch.setitem(chat_server._LITERALS_CACHE, "at", -1e9)
    yield tmp_path / "run"
    chat_server._LITERALS_CACHE["at"] = -1e9


@pytest.mark.parametrize("leak, secret", [
    ("origin https://max:ghp_OTHERTOKEN1234567890abcd@github.com/x.git (fetch)",
     "ghp_OTHERTOKEN1234567890abcd"),
    ("DATABASE postgres://user:hunter2pass@db/x", "hunter2pass"),
    ("curl -u admin:hunter2secret https://x", "hunter2secret"),
    ("token was sk-proj-abcdefghijklmnopqrstuvwxyz0123 ok",
     "sk-proj-abcdefghijklmnopqrstuvwxyz0123"),
    ("hf_AbCdEfGhIjKlMnOpQrStUvWx is the key", "hf_AbCdEfGhIjKlMnOpQrStUvWx"),
    ("aws AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n"
     "QUJDREVGR0hJSktMTU5PUA==\n-----END OPENSSH PRIVATE KEY-----\n",
     "b3BlbnNzaC1rZXktdjEAAAAA"),
])
def test_scrub_catches_url_creds_pem_and_prefixed_tokens(leak, secret,
                                                          fresh_literals):
    """B-chat-3: shapes the export used to publish verbatim."""
    out = chat_server.scrub_secrets(leak)
    assert secret not in out and "redacted" in out


def test_scrub_redacts_the_rig_tokens_and_the_notify_topic(fresh_literals,
                                                           monkeypatch,
                                                           tmp_path):
    """B-chat-3: the locality token, the raw-origin key and the ntfy topic
    have no secret-shaped NAME next to them — they are redacted by value."""
    run = fresh_literals
    (run / "chat-local.token").write_text("4d096154684c44ee81d6fe98f2352a78\n")
    (run / "artifact-raw.key").write_text("rawkey-0123456789abcdef\n")
    tok = tmp_path / "ntfy.token"
    tok.write_text("tk_ntfyTOKENvalue99\n")
    monkeypatch.setenv("OPENBEAST_CHAT_NOTIFY_URL",
                       "http://127.0.0.1:38715/mytopic-SECRET-TOPIC")
    monkeypatch.setenv("OPENBEAST_CHAT_NOTIFY_TOKEN_FILE", str(tok))
    text = ("localtoken 4d096154684c44ee81d6fe98f2352a78\n"
            "raw rawkey-0123456789abcdef\n"
            "topic http://127.0.0.1:38715/mytopic-SECRET-TOPIC\n"
            "ntfy subscribe mytopic-SECRET-TOPIC\n"
            "ntfy tk_ntfyTOKENvalue99\n")
    out = chat_server.scrub_secrets(text)
    for secret in ("4d096154684c44ee81d6fe98f2352a78", "rawkey-0123456789abcdef",
                   "mytopic-SECRET-TOPIC", "tk_ntfyTOKENvalue99"):
        assert secret not in out
    assert out.count("[redacted") == 5


@pytest.mark.parametrize("blob", [
    "-" * 40000,
    "a-" * 20000,
    "Ab_9-" * 40000,                                     # 200 KB base64url
    "x" * 100000 + "=" + "y" * 100000,
    "--" + "a-" * 20000 + " value",
])
def test_scrub_is_linear_on_hostile_runs(blob, fresh_literals):
    """B-chat-1: these each took 30+ s (quadratic) and froze the event loop
    long enough for the healthcheck to restart the server mid-export."""
    started = time.monotonic()
    chat_server.scrub_secrets(blob)
    assert time.monotonic() - started < 1.5


def test_scrub_still_redacts_after_a_benign_assignment(fresh_literals):
    """The name/value pass is one left-to-right scan: a value it skips must
    not hide a secret assignment inside it."""
    out = chat_server.scrub_secrets("x=API_KEY=sk_abcdef123 y=1")
    assert "sk_abcdef123" not in out and "y=1" in out
def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def artifact_server(tmp_path, monkeypatch):
    """The REAL agents/artifact_server.py, store and token in tmp."""
    yield from _artifact_server(tmp_path, monkeypatch)


@pytest.fixture()
def artifact_server_listed(tmp_path, monkeypatch):
    """The same, with an artifact operator allowlist whose FIRST entry is not
    the exporter — the rig-default owner a login-less publish would get."""
    yield from _artifact_server(
        tmp_path, monkeypatch,
        operators="alice@example.com," + LISTED,
        admins="root@example.com")


@pytest.fixture()
def artifact_server_default_admin(tmp_path, monkeypatch):
    """The allowlist above with NO ARTIFACT_ADMINS: its first entry (alice)
    is then the artifact admin (F-A1) and sees every page."""
    yield from _artifact_server(
        tmp_path, monkeypatch, operators="alice@example.com," + LISTED)


def _artifact_server(tmp_path, monkeypatch, operators=None, admins=None):
    port = _free_port()
    run = tmp_path / "art-run"
    files = tmp_path / "art-files"
    run.mkdir()
    files.mkdir()
    env = dict(os.environ)
    env.update({"OPENBEAST_ARTIFACT_PORT": str(port),
                "OPENBEAST_BIND": "127.0.0.1",
                "OPENBEAST_RUN_DIR": str(run),
                "OPENBEAST_FILES_DIR": str(files)})
    for k in ("OPENBEAST_ARTIFACT_OPERATORS", "OPENBEAST_CHAT_OPERATORS",
              "OPENBEAST_ARTIFACT_BASE_URL", "OPENBEAST_ARTIFACT_ADMINS"):
        env.pop(k, None)
    if operators:
        env["OPENBEAST_ARTIFACT_OPERATORS"] = operators
    if admins:
        env["OPENBEAST_ARTIFACT_ADMINS"] = admins
    proc = subprocess.Popen(["nice", "-n", "19", sys.executable,
                             os.path.join(AGENTS, "artifact_server.py")],
                            env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import urllib.request
        deadline = time.monotonic() + 20
        up = False
        while time.monotonic() < deadline and not up:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/artifacts/health",
                        timeout=2):
                    up = (run / "artifact-local.token").exists()
            except Exception:
                time.sleep(0.1)
        assert up, "artifact_server did not come up"
        monkeypatch.setenv("BEAST_ARTIFACT", "true")
        monkeypatch.setenv("OPENBEAST_ARTIFACT_PORT", str(port))
        monkeypatch.setenv("OPENBEAST_RUN_DIR", str(run))
        monkeypatch.setenv("OPENBEAST_BIND", "127.0.0.1")
        monkeypatch.setenv("OPENBEAST_FILES_DIR", str(files))
        yield {"port": port, "run": run, "files": files}
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_export_round_trips_through_the_real_artifact_server(
        rig, artifact_server, monkeypatch):
    monkeypatch.setenv("OPENBEAST_API_KEY", "sk-live-9f8e7d6c5b4a")
    lines = list(HOSTILE)
    lines[2] = dict(HOSTILE[2], result=HOSTILE[2]["result"] % {
        "key": "sk-live-9f8e7d6c5b4a"})
    sid = rig.session(kind="agent", state="done", lines=lines,
                      title="build check")
    r = rig.client.post(f"/api/chat/sessions/{sid}/export", json={},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    d = r.json()
    want = str(uuid.uuid5(uuid.NAMESPACE_URL, f"openbeast:chat-export:{sid}"))
    assert d["id"] == want and d["version"] == 1
    assert f"/a/{want}" in d["url"]
    # Stable id: a re-export is v2 at the same URL.
    r2 = rig.client.post(f"/api/chat/sessions/{sid}/export", json={},
                         headers=rig.local)
    assert r2.json()["id"] == want and r2.json()["version"] == 2
    import artifact
    meta = artifact.get_meta(want)
    assert meta["visibility"] == "private"
    page, _ = artifact.read_file(want, 1)
    page = page.decode()
    assert "sk-live-9f8e7d6c5b4a" not in page and "&lt;script&gt;" in page
    assert "<script>alert" not in page


def _get_page(port, aid, login):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{port}/a/{aid}",
                                 headers={"Tailscale-User-Login": login})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _export_as_phone(rig, sid):
    key = rig.enroll("phone", "k-phone-export", scopes=["chat"])
    r = rig.client.post(f"/api/chat/sessions/{sid}/export", json={},
                        headers={**key, "Tailscale-User-Login": LISTED})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_the_exporter_can_open_the_page_it_was_shown(rig, artifact_server):
    """No artifact allowlist: the page belongs to the login that pressed
    Export, not to 'local' — so that login opens it and nobody else does."""
    sid = rig.session(kind="agent", state="done", title="build check")
    aid = _export_as_phone(rig, sid)
    port = artifact_server["port"]
    assert _get_page(port, aid, LISTED) == 200
    assert _get_page(port, aid, "alice@example.com") == 404  # negative control
    import artifact
    assert artifact.get_meta(aid)["owner"] == LISTED


def test_the_exporter_owns_the_page_under_an_artifact_allowlist(
        rig, artifact_server_listed):
    """With an allowlist, the exporter (listed second) owns the page — not
    the rig, not the first entry. Since F-A1 the first entry is the default
    artifact ADMIN and sees every page, so this fixture names another admin
    to keep alice a plain operator, for whom another's private page is 404."""
    sid = rig.session(kind="agent", state="done", title="build check")
    aid = _export_as_phone(rig, sid)
    port = artifact_server_listed["port"]
    assert _get_page(port, aid, LISTED) == 200
    assert _get_page(port, aid, "alice@example.com") == 404


def test_the_default_artifact_admin_sees_an_exported_page(
        rig, artifact_server_default_admin):
    """Cross-track rule (integration 2026-09-30): with no ARTIFACT_ADMINS the
    first operator is the admin, so it opens the exporter's page too — and
    the exporter still owns it."""
    sid = rig.session(kind="agent", state="done", title="build check")
    aid = _export_as_phone(rig, sid)
    port = artifact_server_default_admin["port"]
    assert _get_page(port, aid, LISTED) == 200
    assert _get_page(port, aid, "alice@example.com") == 200
    assert _get_page(port, aid, "mallory@example.com") == 404   # unlisted


def test_a_second_principal_can_still_export_a_session(rig, artifact_server):
    """B-chat-4: the shared id belongs to whoever exported first; the other
    principal got a permanent 'HTTP 404'. It now gets a page of its own, and
    each principal's re-export stays a new version of ITS page."""
    import artifact
    sid = rig.session(kind="agent", state="done", title="build check")
    phone_aid = _export_as_phone(rig, sid)                 # phone first
    assert phone_aid == chat_server.export_artifact_id(sid)
    r = rig.client.post(f"/api/chat/sessions/{sid}/export", json={},
                        headers=rig.local)                 # then the rig
    assert r.status_code == 200, r.text
    rig_aid = r.json()["id"]
    assert rig_aid == chat_server.export_artifact_id(sid, "rig") != phone_aid
    assert artifact.get_meta(rig_aid)["owner"] == "rig"
    assert artifact.get_meta(phone_aid)["owner"] == LISTED
    r2 = rig.client.post(f"/api/chat/sessions/{sid}/export", json={},
                         headers=rig.local)
    assert (r2.json()["id"], r2.json()["version"]) == (rig_aid, 2)
    key = rig.enroll("phone2", "k-phone-export2", scopes=["chat"])
    r3 = rig.client.post(f"/api/chat/sessions/{sid}/export", json={},
                         headers={**key, "Tailscale-User-Login": LISTED})
    assert (r3.json()["id"], r3.json()["version"]) == (phone_aid, 2)


def test_only_a_verified_real_login_is_forwarded_as_owner():
    allow_all = lambda login: True  # noqa: E731
    f = chat_server.export_owner_login
    ok = {"login": LISTED, "verified": True, "local": False, "device": "phone"}
    assert f(ok, allow_all) == LISTED
    assert f(dict(ok, verified=False), allow_all) == ""
    assert f(dict(ok, local=True, login="local"), allow_all) == ""
    assert f(dict(ok, login="device:phone"), allow_all) == ""
    assert f(dict(ok, login="max@example.com\r\nX-OpenBeast-Local: t"),
             allow_all) == ""
    assert f(ok, lambda login: False) == ""       # refused by the chat list


def test_export_scrubs_the_store_title_too(rig, artifact_server):
    sid = rig.session(kind="job", state="done",
                      title="HF_TOKEN=hf_abcdefSECRET123 python dl.py")
    r = rig.client.post(f"/api/chat/sessions/{sid}/export", json={},
                        headers=rig.local)
    assert r.status_code == 200, r.text
    import artifact
    meta = artifact.get_meta(r.json()["id"])
    assert "hf_abcdefSECRET123" not in json.dumps(meta)
    assert "python dl.py" in meta["title"]          # the rest survives


# ---------------------------------------------------------------------------
# F-C7 rig status strip
# ---------------------------------------------------------------------------

def test_rig_status_reports_lease_llama_and_running(rig, stub, tmp_path,
                                                    monkeypatch):
    llama = stub(body=b'{"status":"ok"}')
    monkeypatch.setenv("OPENBEAST_INFERENCE_URL", llama.url)
    lease = tmp_path / "gpu.lease"
    me_start = sessions.pid_start_time(os.getpid())
    lease.write_text(f"pid={os.getpid()}\nstart={me_start}\n"
                     f"label=tier3 rerun\nsince=2026-09-29T21:04:53\n")
    monkeypatch.setenv("OPENBEAST_CHAT_GPU_LEASE", str(lease))
    rig.session(kind="job", state="running")
    d = rig.client.get("/api/chat/rig").json()
    assert d["gpu"]["state"] == "held" and d["gpu"]["label"] == "tier3 rerun"
    assert d["llama"]["up"] is True and d["running"] == 1
    assert llama.calls[-1]["path"] == "/health"
    assert rig.anon.get("/api/chat/rig").status_code == 404


def test_rig_status_stale_lease_and_down_llama(rig, tmp_path, monkeypatch):
    """Negative controls: a recycled/dead holder is not `held`; a dead
    inference port is `down`."""
    monkeypatch.setenv("OPENBEAST_INFERENCE_URL", "http://127.0.0.1:9")
    lease = tmp_path / "gpu.lease"
    lease.write_text(f"pid={os.getpid()}\nstart=1\nlabel=old\n")
    monkeypatch.setenv("OPENBEAST_CHAT_GPU_LEASE", str(lease))
    d = rig.client.get("/api/chat/rig").json()
    assert d["gpu"]["state"] == "stale" and d["llama"]["up"] is False
    monkeypatch.setenv("OPENBEAST_CHAT_GPU_LEASE", str(tmp_path / "none"))
    rig._app = None
    assert rig.client.get("/api/chat/rig").json()["gpu"] == {"state": "free"}


# ---------------------------------------------------------------------------
# Integration seam (2026-09-30): the notify secret stays in chat_server
# ---------------------------------------------------------------------------

_NOTIFY_ENV = {
    "OPENBEAST_CHAT_NOTIFY_URL": "http://127.0.0.1:3005/secret-topic-abc",
    "OPENBEAST_CHAT_NOTIFY_TOKEN_FILE": "/tmp/ntfy.token",
    "OPENBEAST_CHAT_NOTIFY_ON": "failed,done",
    "CHAT_NOTIFY_URL": "http://127.0.0.1:3005/secret-topic-abc",
}


def test_child_env_never_carries_the_notify_settings(monkeypatch):
    """The topic URL is the credential under ntfy's default access and its
    name matches none of KEY/SECRET/PASSWORD/TOKEN — the shared scrub keeps
    it. `keep` and `extra` cannot re-admit it either."""
    for k, v in _NOTIFY_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("OPENBEAST_KEEP_ME", "yes")          # negative control
    env = chat_server.child_env(
        {"OPENBEAST_SESSION_ID": "s1",
         "OPENBEAST_CHAT_NOTIFY_URL": "http://x/extra"},
        keep=("OPENBEAST_CHAT_NOTIFY_URL", "OPENBEAST_API_KEY"))
    for k in _NOTIFY_ENV:
        assert k not in env, k
    assert "secret-topic-abc" not in json.dumps(env)
    assert env["OPENBEAST_SESSION_ID"] == "s1"
    assert env["OPENBEAST_KEEP_ME"] == "yes"


def test_a_spawned_job_cannot_read_the_notify_url(rig, tmp_path, monkeypatch):
    """End to end through the real spawn path (job.sh __supervise): the job's
    own environment has its session id and none of the notify settings."""
    for k, v in _NOTIFY_ENV.items():
        monkeypatch.setenv(k, v)
    dump = tmp_path / "env.txt"
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "job", "title": "env", "cmd": f"env > {dump}",
        "workdir": str(tmp_path)})
    assert r.status_code == 201, r.text
    sid = r.json()["session"]["id"]
    assert wait_state(sid, "done"), sessions.get(sid)
    text = dump.read_text()
    assert f"OPENBEAST_SESSION_ID={sid}" in text        # the control
    assert "CHAT_NOTIFY" not in text
    assert "secret-topic-abc" not in text


def test_a_console_agent_uses_the_rigs_agent_inference_url(rig, monkeypatch):
    """conf.sh exports OPENBEAST_AGENT_INFERENCE_URL (from INFERENCE_URL or
    AGENT_INFERENCE_URL); mcp_server.start_agent honours it and the console
    spawn used to drop it, calling localhost:8080 on a vLLM/worker rig."""
    monkeypatch.setenv("OPENBEAST_AGENT_INFERENCE_URL", "http://10.0.0.9:8000/v1")
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "hello", "dry_run": True})
    assert r.status_code == 200, r.text
    argv = r.json()["argv"]
    assert argv[argv.index("--base-url") + 1] == "http://10.0.0.9:8000/v1"
    # the caller's explicit endpoint still wins
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "hello", "dry_run": True,
        "base_url": "http://127.0.0.1:7/v1"})
    argv = r.json()["argv"]
    assert argv[argv.index("--base-url") + 1] == "http://127.0.0.1:7/v1"
    # control: unset → no --base-url (the runner's own default)
    monkeypatch.delenv("OPENBEAST_AGENT_INFERENCE_URL")
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "hello", "dry_run": True})
    assert "--base-url" not in r.json()["argv"]
