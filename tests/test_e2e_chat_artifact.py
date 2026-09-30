#!/usr/bin/env python3
"""beast-chat x beast-artifact, end to end, in a real headless Chromium.

The two consoles were reviewed and fixed as separate tracks (2026-09-30).
This walks the SEAMS between them the way an operator on a phone would, at
390x844, against REAL processes from this checkout:

  chat_server.py      ephemeral port, temp ledger/.run/logs
  artifact_server.py  ephemeral port, temp store/.run
  two identity proxies  play `tailscale serve` for one listed operator: they
                      strip any client Tailscale-User-Login, set their own and
                      dial from 127.0.0.1 (the chat one STREAMS, for SSE)
  a stub OpenAI server  the agent's model (never :8080): scripted tool calls
  a stub ntfy server  records every notification POST

  (a) the + sheet starts an agent and a preset job
  (b) the agent runs scripts/artifact.sh publish in its session; the artifact
      shell says "made by session <id>" and the console linkifies the URL
  (c) Export publishes the transcript; it opens for the operator, scrubbed
  (d) pause / resume / stop from the session header
  (e) each session that ends done/failed fires exactly ONE notification:
      title + state + deep link, never transcript text
  (f) the artifact Manage sheet with an 'artifact'-scoped device key: pin,
      tag, share, delete (and a chat-scoped key is refused)

SKIPS without chromium or fastapi/uvicorn/openai. Screenshots go to
$OPENBEAST_E2E_SHOTS when set (else the test's tmp dir), with results.json.
Everything is killed by pid; nothing touches the real stack or port 8080.

Run: nice -n 19 python3 -m pytest tests/test_e2e_chat_artifact.py -q
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (AGENTS, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _cdp_pipe import Chrome, find_chrome  # noqa: E402

try:
    import fastapi  # noqa: F401
    import openai  # noqa: F401
    import uvicorn  # noqa: F401
    _DEPS = True
except ImportError:
    _DEPS = False

pytestmark = [
    pytest.mark.skipif(find_chrome() is None, reason="no chromium/chrome binary"),
    pytest.mark.skipif(not _DEPS, reason="fastapi/uvicorn/openai not importable"),
]

OPERATOR = "op@example.com"
CHAT_KEY = "k-e2e-chat-0123456789abcdef"
ART_KEY = "k-e2e-artifact-0123456789abcdef"
SECRET = "hf_e2eSECRETvalue0123456789"          # printed by the agent's tool
JOB_OUT = "job-output-line-7f3a"                 # printed by the failing job
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
NICE = ["nice", "-n", "19"] + (["ionice", "-c3"] if os.path.exists("/usr/bin/ionice") else [])


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Thread:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------

def stub_ntfy():
    calls = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            calls.append({"path": self.path, "headers": dict(self.headers),
                          "body": self.rfile.read(n).decode("utf-8", "replace")})
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_PUT = do_POST

        def log_message(self, *a):
            pass

    t = _Thread(H)
    t.calls = calls
    return t


def stub_model(root: str, work: str):
    """An OpenAI-compatible /v1/chat/completions that scripts two agents:

    PUBLISH  1st turn: bash that writes a page and runs scripts/artifact.sh
             publish (and prints a secret-shaped line for the scrubber);
             2nd turn: says the URL and calls task_done.
    SLOW     a bash `sleep 1` every turn, forever — something to pause/stop.
    """
    publish_cmd = (
        f"cd {work} && printf '<!doctype html><title>E2E page</title>"
        f"<h1>made by an agent</h1>' > page.html && echo HF_TOKEN={SECRET} && "
        f"REPO_DIR={root} {REPO}/scripts/artifact.sh publish page.html "
        f"--title 'E2E page'")

    def reply(messages):
        task = next((m.get("content") or "" for m in messages
                     if m.get("role") == "user"), "")
        tools = [m for m in messages if m.get("role") == "tool"]
        if "SLOW" in task:
            time.sleep(0.3)
            return None, [("bash", {"command": "sleep 1"})]
        if not tools:
            return "Publishing the page now.", [("bash", {"command": publish_cmd})]
        text = " ".join(str(t.get("content") or "") for t in tools)
        m = re.search(r"https?://[^\s\"']+/a/" + UUID_RE, text)
        url = m.group(0) if m else "(no url)"
        return f"Published the page: {url}", [("task_done", {"summary": f"published {url}"})]

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                return self._send(200, {"data": [{"id": "stub-model", "object": "model"}]})
            return self._send(200, {"status": "ok"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            content, calls = reply(req.get("messages") or [])
            msg = {"role": "assistant", "content": content}
            if calls:
                msg["tool_calls"] = [
                    {"id": f"call_{i}_{int(time.time()*1000)}", "type": "function",
                     "function": {"name": nm, "arguments": json.dumps(args)}}
                    for i, (nm, args) in enumerate(calls)]
            self._send(200, {
                "id": "chatcmpl-e2e", "object": "chat.completion",
                "created": int(time.time()), "model": "stub-model",
                "choices": [{"index": 0, "message": msg,
                             "finish_reason": "tool_calls" if calls else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15}})

        def log_message(self, *a):
            pass

    return _Thread(H)


def identity_proxy(upstream_port: int, login: str):
    """`tailscale serve` for one login: strips any client identity header,
    sets its own, dials from 127.0.0.1 — and STREAMS bodies without a
    Content-Length (the console's SSE), which tests/_artifact_live's buffered
    proxy cannot."""

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            hdrs = {k: v for k, v in self.headers.items()
                    if k.lower() not in ("tailscale-user-login", "connection",
                                         "keep-alive", "accept-encoding",
                                         "content-length")}
            hdrs["Tailscale-User-Login"] = login
            if body is not None:
                hdrs["Content-Length"] = str(len(body))
            c = http.client.HTTPConnection("127.0.0.1", upstream_port, timeout=120)
            try:
                c.request(self.command, self.path, body=body, headers=hdrs)
                r = c.getresponse()
                length = r.getheader("Content-Length")
                self.send_response(r.status)
                for k, v in r.getheaders():
                    if k.lower() in ("transfer-encoding", "connection",
                                     "content-length", "keep-alive"):
                        continue
                    self.send_header(k, v)
                if length is not None:
                    data = r.read()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(data)
                    return
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                while True:
                    chunk = r.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                self.close_connection = True
            finally:
                c.close()

        do_GET = do_POST = do_PATCH = do_DELETE = do_HEAD = do_PUT = _do

        def log_message(self, *a):
            pass

    return _Thread(H)


# ---------------------------------------------------------------------------
# the world
# ---------------------------------------------------------------------------

def _wait_http(url, headers=None, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers=headers or {}), timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.1)
    return False


class World:
    def __init__(self, tmp, shots):
        self.tmp, self.shots = tmp, shots
        self.results: dict = {}
        self.procs: list[subprocess.Popen] = []
        self.root = os.path.join(tmp, "rig")
        self.run = os.path.join(self.root, ".run")
        self.work = os.path.join(self.root, "work")
        self.sdir = os.path.join(self.run, "sessions")
        for d in (self.run, self.work, self.sdir,
                  os.path.join(self.root, "files"), os.path.join(self.root, "logs")):
            os.makedirs(d, mode=0o700, exist_ok=True)
        open(os.path.join(self.root, "openbeast.conf"), "w").close()
        with open(os.path.join(self.run, "clients.json"), "w") as f:
            json.dump({"version": 1, "devices": [
                {"id": "phone-chat", "label": "phone chat", "enrolled_at": "now",
                 "revoked_at": None, "scopes": ["chat"],
                 "key_sha256": hashlib.sha256(CHAT_KEY.encode()).hexdigest()},
                {"id": "phone-art", "label": "phone artifact", "enrolled_at": "now",
                 "revoked_at": None, "scopes": ["artifact"],
                 "key_sha256": hashlib.sha256(ART_KEY.encode()).hexdigest()}]}, f)
        presets = os.path.join(self.run, "chat-presets.json")
        fd = os.open(presets, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"presets": [
                {"name": "e2e-fail", "title": "E2E failing job",
                 "cmd": f"echo {JOB_OUT}; sleep 1; exit 3", "workdir": self.work}]}, f)

        self.ntfy = stub_ntfy()
        self.model = stub_model(self.root, self.work)
        self.chat_port = _free_port()
        self.art_port = _free_port()
        self.chat_proxy = identity_proxy(self.chat_port, OPERATOR)
        self.art_proxy = identity_proxy(self.art_port, OPERATOR)

        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("OPENBEAST_", "BEAST_", "INFERENCE_",
                                     "CHAT_", "ARTIFACT_"))}
        common = {
            "OPENBEAST_RUN_DIR": self.run,
            "OPENBEAST_CONF": os.path.join(self.root, "openbeast.conf"),
            "OPENBEAST_FILES_DIR": os.path.join(self.root, "files"),
            "OPENBEAST_ARTIFACT_PORT": str(self.art_port),
            "OPENBEAST_ARTIFACT_OPERATORS": OPERATOR,
            "OPENBEAST_CHAT_OPERATORS": OPERATOR,
            "OPENBEAST_ARTIFACT_BASE_URL": self.art_proxy.url,
            "OPENBEAST_CHAT_BASE_URL": self.chat_proxy.url,
            "OPENBEAST_SESSIONS_DIR": self.sdir,
        }
        art_env = dict(base, **common, OPENBEAST_REPO_DIR=REPO)
        self.art = self._spawn([sys.executable, os.path.join(AGENTS, "artifact_server.py")],
                               art_env, "artifact.log")
        assert _wait_http(f"http://127.0.0.1:{self.art_port}/api/artifacts/health"), \
            self._log("artifact.log")
        chat_env = dict(base, **common, **{
            "OPENBEAST_CHAT_PORT": str(self.chat_port),
            "OPENBEAST_CHAT_BIND": "127.0.0.1",
            "OPENBEAST_CHAT_RUN_DIR": self.run,
            "OPENBEAST_CHAT_LOG_DIR": os.path.join(self.root, "logs"),
            "OPENBEAST_CHAT_SCOPE": "off",
            "OPENBEAST_CHAT_POLL_MS": "100",
            "OPENBEAST_CHAT_HEARTBEAT_S": "5",
            "OPENBEAST_CHAT_NOTIFY_URL": self.ntfy.url + "/e2e-topic",
            "OPENBEAST_CHAT_NOTIFY_PERIOD_S": "1",
            "OPENBEAST_CHAT_PUBLIC_URL": self.chat_proxy.url,
            "OPENBEAST_CHAT_GPU_LEASE": os.path.join(self.tmp, "no.lease"),
            "OPENBEAST_CHAT_SLOT_URL": self.model.url + "/api/slot",
            "OPENBEAST_INFERENCE_URL": self.model.url,
            "OPENBEAST_AGENT_INFERENCE_URL": self.model.url + "/v1",
            "BEAST_ARTIFACT": "true",
        })
        self.chat = self._spawn([sys.executable, os.path.join(AGENTS, "chat_server.py")],
                                chat_env, "chat.log")
        tok = os.path.join(self.run, "chat-local.token")
        end = time.monotonic() + 30
        while time.monotonic() < end and not os.path.exists(tok):
            time.sleep(0.1)
        self.chat_token = open(tok).read().strip()
        assert _wait_http(f"http://127.0.0.1:{self.chat_port}/api/chat/health"), \
            self._log("chat.log")
        self.chrome = Chrome()
        self.page = self.chrome.new_page(width=390, height=844)

    def _spawn(self, argv, env, log):
        f = open(os.path.join(self.tmp, log), "wb")
        p = subprocess.Popen(NICE + argv, env=env, stdin=subprocess.DEVNULL,
                             stdout=f, stderr=subprocess.STDOUT)
        self.procs.append(p)
        return p

    def _log(self, name):
        try:
            return open(os.path.join(self.tmp, name), errors="replace").read()[-3000:]
        except OSError:
            return ""

    # -- helpers -----------------------------------------------------------
    def chat_api(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.chat_port}{path}", data=data, method=method,
            headers={"X-OpenBeast-Local": self.chat_token,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, None

    def session(self, sid):
        return self.chat_api("GET", f"/api/chat/sessions/{sid}")[1] or {}

    def wait_state(self, sid, states, timeout=90):
        end = time.monotonic() + timeout
        rec = {}
        while time.monotonic() < end:
            rec = self.session(sid)
            s = (rec.get("session") or rec).get("state")
            if s in states:
                return s
            time.sleep(0.3)
        return (rec.get("session") or rec).get("state")

    def shot(self, name):
        data = self.chrome.send("Page.captureScreenshot", {"format": "png"},
                                session=self.page.sid)["data"]
        path = os.path.join(self.shots, f"{name}.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    def record(self, step, ok, shots, note=""):
        self.results[step] = {"pass": bool(ok), "screenshots": shots, "note": note}
        with open(os.path.join(self.shots, "results.json"), "w") as f:
            json.dump(self.results, f, indent=1)

    def close(self):
        try:
            self.chrome.close()
        except Exception:
            pass
        # Every session this run spawned: its own process group.
        for name in os.listdir(self.sdir):
            if not name.endswith(".json"):
                continue
            try:
                rec = json.load(open(os.path.join(self.sdir, name)))
            except (OSError, ValueError):
                continue
            pg = rec.get("pgid")
            if isinstance(pg, int) and pg > 1 and pg != os.getpgrp():
                try:
                    os.killpg(pg, signal.SIGKILL)
                except OSError:
                    pass
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=10)
        for t in (self.ntfy, self.model, self.chat_proxy, self.art_proxy):
            t.close()


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("e2e"))
    shots = os.environ.get("OPENBEAST_E2E_SHOTS") or os.path.join(tmp, "shots")
    os.makedirs(shots, exist_ok=True)
    world = World(tmp, shots)
    try:
        yield world
    finally:
        world.close()


def _q(sel):
    return json.dumps(sel)


def _click(w, sel):
    assert w.page.eval(f"(function(){{var n=document.querySelector({_q(sel)});"
                       f"if(!n) return false; n.click(); return true;}})()"), sel


def _set(w, sel, value):
    assert w.page.eval(f"(function(){{var n=document.querySelector({_q(sel)});"
                       f"if(!n) return false; n.value={json.dumps(value)};"
                       f"n.dispatchEvent(new Event('input'));return true;}})()"), sel


def _open_console(w, hash_="#/"):
    p = w.page
    p.goto(w.chat_proxy.url + "/manifest.webmanifest", settle=0.2)
    p.eval(f"localStorage.clear(); localStorage.setItem('bc.devkey', {json.dumps(CHAT_KEY)})")
    p.goto(w.chat_proxy.url + "/" + hash_, settle=0.5)


def _start_from_sheet(w, *, agent_task=None, preset=None):
    p = w.page
    _open_console(w)
    _click(w, "#newBtn")
    assert p.wait_for("!document.querySelector('#newSheet').hidden && "
                      "getComputedStyle(document.querySelector('#newSheet')).display !== 'none'", 10)
    if agent_task is not None:
        _click(w, "#tabAgent")
        _set(w, "#nsTask", agent_task)
        _set(w, "#nsWorkdir", w.work)
    else:
        _click(w, "#tabJob")
        assert p.wait_for("Array.from(document.querySelectorAll('#nsPresets .preset'))"
                          ".some(function(n){return n.textContent.indexOf('E2E failing job')>=0})", 10)
        p.eval("Array.from(document.querySelectorAll('#nsPresets .preset')).filter("
               "function(n){return n.textContent.indexOf('E2E failing job')>=0})[0].click()")
    shots = [w.shot(f"a-sheet-{'agent' if agent_task else 'job'}")]
    _click(w, "#nsReview")
    assert p.wait_for("document.querySelector('#csArgv').textContent.length > 0", 10)
    shots.append(w.shot(f"a-confirm-{'agent' if agent_task else 'job'}"))
    _click(w, "#csGo")
    sid = p.wait_for("(location.hash.match(/^#\\/s\\/(.+)$/)||[])[1] || ''", 20)
    assert sid, p.eval("document.querySelector('#toast').textContent")
    return sid, shots


# ---------------------------------------------------------------------------
# the walkthrough (in order; each step records pass/fail + screenshots)
# ---------------------------------------------------------------------------

def test_a_plus_sheet_starts_an_agent_and_a_preset_job(w):
    sid, shots = _start_from_sheet(
        w, agent_task="PUBLISH a page with scripts/artifact.sh and report its URL")
    w.agent_sid = sid
    jsid, jshots = _start_from_sheet(w, preset="e2e-fail")
    w.job_sid = jsid
    shots += jshots
    shots.append(w.shot("a-job-session"))
    agent = w.session(sid).get("session") or {}
    job = w.session(jsid).get("session") or {}
    assert agent.get("kind") == "agent", agent
    assert job.get("kind") == "job", job
    assert (job.get("title") or "") == "E2E failing job", job
    # Both carry the operator who started them.
    assert OPERATOR in json.dumps(agent.get("meta") or {}), agent
    w.record("a", True, shots, f"agent {sid}, job {jsid}")


def test_b_agent_publishes_and_the_url_is_linked_both_ways(w):
    p = w.page
    state = w.wait_state(w.agent_sid, ("done", "failed", "stopped", "lost"), 120)
    assert state == "done", (state, w._log("chat.log"))
    _open_console(w, f"#/s/{w.agent_sid}")
    href = p.wait_for("(function(){var a=document.querySelector('#stream a.art');"
                      "return a ? a.href : '';})()", 20)
    shots = [w.shot("b-console-linkified")]
    assert href and re.search(r"/a/" + UUID_RE, href), href
    assert href.startswith(w.art_proxy.url), href
    w.agent_art_url = href
    w.agent_art_id = re.search(UUID_RE, href).group(0)
    p.goto(href, settle=1.0)
    text = p.wait_for("document.body && document.body.innerText", 10) or ""
    shots.append(w.shot("b-artifact-shell"))
    link = p.eval("(function(){var a=Array.from(document.querySelectorAll('a'))"
                  ".filter(function(x){return /made by session/.test(x.textContent)})[0];"
                  "return a ? a.getAttribute('href') : '';})()") or ""
    assert f"made by session {w.agent_sid}" in text, text[:500]
    assert link.endswith(f"/#/s/{w.agent_sid}"), link
    # ...and readable on the phone: not clipped to "made b…" off the strip.
    assert p.eval("(function(){var a=document.querySelector('.sess');"
                  "var r=a.getBoundingClientRect();"
                  "return r.right <= innerWidth + 0.5 && a.scrollWidth <= a.clientWidth + 1;})()"), \
        "the 'made by session' link is clipped at 390px"
    # The framed page is sandboxed (no allow-same-origin), so the shell
    # cannot read it; fetch the exact URL the frame loads, as the operator.
    src = p.eval("document.querySelector('#frame').src")
    assert "/raw/" + w.agent_art_id in src, src
    with urllib.request.urlopen(src, timeout=10) as r:
        assert "made by an agent" in r.read().decode("utf-8", "replace")
    w.record("b", True, shots, f"artifact {w.agent_art_id} stamped with {w.agent_sid}")


def test_c_export_opens_for_the_operator_and_is_scrubbed(w):
    p = w.page
    _open_console(w, f"#/s/{w.agent_sid}")
    assert p.wait_for("!document.querySelector('#exportBtn').hidden", 15)
    _click(w, "#exportBtn")
    url = p.wait_for("(function(){var n=Array.from(document.querySelectorAll('.note a.art'))"
                     ".pop();return n ? n.href : '';})()", 30)
    shots = [w.shot("c-exported-note")]
    assert url and url.startswith(w.art_proxy.url), (
        url, p.eval("document.querySelector('#toast').textContent"))
    p.goto(url, settle=1.0)
    text = p.wait_for("document.body && document.body.innerText", 10) or ""
    shots.append(w.shot("c-export-shell"))
    assert "Transcript" in text and "Not Found" not in text, text[:300]
    src = p.eval("document.querySelector('#frame').src")
    with urllib.request.urlopen(src, timeout=10) as r:      # as the operator
        raw = r.read().decode("utf-8", "replace")
    assert "Publishing the page now." in raw          # it IS the transcript
    assert SECRET not in raw                          # ...scrubbed
    assert "[redacted" in raw
    w.record("c", True, shots, url)


def test_d_pause_resume_and_stop(w):
    p = w.page
    sid, shots = _start_from_sheet(w, agent_task="SLOW loop until stopped")
    w.slow_sid = sid
    assert p.wait_for("!document.querySelector('#pauseBtn').hidden", 30)
    time.sleep(1.5)
    _click(w, "#pauseBtn")
    assert p.wait_for("Array.from(document.querySelectorAll('#stream .note')).some("
                      "function(n){return /paused at iteration/.test(n.textContent)})", 30)
    assert p.wait_for("!document.querySelector('#resumeBtn').hidden", 30)
    shots.append(w.shot("d-paused"))
    _click(w, "#resumeBtn")
    assert p.wait_for("!document.querySelector('#pauseBtn').hidden", 30)
    shots.append(w.shot("d-resumed"))
    p.eval("window.confirm = function(){ return true; }")
    _click(w, "#stopBtn")
    state = w.wait_state(sid, ("stopped", "done", "failed", "lost"), 60)
    time.sleep(1.0)
    shots.append(w.shot("d-stopped"))
    assert state == "stopped", state
    w.record("d", True, shots, f"{sid} paused, resumed, stopped")


def test_e_one_notification_per_ended_session_and_no_transcript(w):
    assert w.wait_state(w.job_sid, ("failed",), 60) == "failed"
    # Let the notifier tick a few more times: no duplicates may appear.
    end = time.monotonic() + 20
    while time.monotonic() < end and len(w.ntfy.calls) < 2:
        time.sleep(0.5)
    time.sleep(4)
    calls = list(w.ntfy.calls)
    by_sid: dict = {}
    for c in calls:
        click = c["headers"].get("Click", "")
        m = re.search(r"#/s/(.+)$", click)
        by_sid.setdefault(m.group(1) if m else "?", []).append(c)
    agent = by_sid.get(w.agent_sid, [])
    job = by_sid.get(w.job_sid, [])
    assert len(agent) == 1, calls
    assert len(job) == 1, calls
    assert w.slow_sid not in by_sid, "a STOPPED session is not in NOTIFY_ON"
    assert agent[0]["headers"]["Title"] == "beast-chat: agent done"
    assert job[0]["headers"]["Title"] == "beast-chat: job failed"
    assert agent[0]["headers"]["Click"] == f"{w.chat_proxy.url}/#/s/{w.agent_sid}"
    assert "done" in agent[0]["body"] and "failed" in job[0]["body"]
    for c in calls:
        blob = json.dumps(c)
        for leak in (SECRET, JOB_OUT, "Publishing the page now", "Published the page"):
            assert leak not in blob, (leak, c)
    # The deep link opens that session in the console.
    w.page.goto(agent[0]["headers"]["Click"], settle=1.0)
    assert w.page.wait_for("!!document.querySelector('#stream .e-done')", 15)
    shot = w.shot("e-deep-link")
    with open(os.path.join(w.shots, "e-notifications.json"), "w") as f:
        json.dump(calls, f, indent=1)
    w.record("e", True, [shot, os.path.join(w.shots, "e-notifications.json")],
             f"{len(calls)} notification(s): agent done x1, job failed x1, stopped x0")


def test_f_manage_sheet_with_an_artifact_scoped_key(w):
    p = w.page
    url = w.agent_art_url
    aid = w.agent_art_id
    p.goto(url, settle=0.8)
    assert p.wait_for("!document.querySelector('#manage').hidden", 10), \
        "the operator (first operator = admin) may manage the rig's page"
    shots = []

    def with_key(key):
        p.eval(f"localStorage.setItem('openbeast.artifact.devkey', {json.dumps(key)})")

    def state():
        return json.loads(urllib.request.urlopen(urllib.request.Request(
            f"{w.art_proxy.url}/api/artifacts/{aid}"), timeout=10).read())

    # NEGATIVE CONTROL: the chat-scoped key cannot change a page.
    with_key(CHAT_KEY)
    _click(w, "#manage")
    _click(w, "#m-pin")
    assert p.wait_for("document.querySelector('#m-status').getAttribute('data-bad')==='1'", 10)
    shots.append(w.shot("f-chat-key-refused"))
    meta = state()
    assert not (meta.get("pinned") or (meta.get("meta") or {}).get("pinned"))

    with_key(ART_KEY)
    p.goto(url, settle=0.8)
    _click(w, "#manage")
    _click(w, "#m-pin")
    assert p.wait_for("!document.querySelector('#pinmark').hidden", 15)
    shots.append(w.shot("f-pinned"))

    _click(w, "#manage")
    _set(w, "#m-tags", "e2e, demo")
    _click(w, "#m-tags-save")
    assert p.wait_for("document.querySelector('#tagchips').textContent.indexOf('demo')>=0", 15)
    shots.append(w.shot("f-tagged"))

    _click(w, "#manage")
    p.eval("window.confirm = function(){ return true; }")
    _click(w, "#m-vis")
    assert p.wait_for("document.body.getAttribute('data-visibility')==='tailnet'", 15)
    shots.append(w.shot("f-shared"))

    _click(w, "#manage")
    p.eval("document.querySelector('#sheet details').open = true")
    _set(w, "#m-del-confirm", aid)
    shots.append(w.shot("f-delete-confirm"))
    _click(w, "#m-del")
    assert p.wait_for("location.pathname === '/'", 15)
    shots.append(w.shot("f-deleted-gallery"))
    try:
        urllib.request.urlopen(f"{w.art_proxy.url}/a/{aid}", timeout=10)
        gone = False
    except urllib.error.HTTPError as e:
        gone = e.code == 404
    assert gone
    w.record("f", True, shots, f"{aid}: chat key refused; pin, tag, share, delete ok")
