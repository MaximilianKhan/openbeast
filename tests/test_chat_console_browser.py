#!/usr/bin/env python3
"""beast-chat console in a real headless Chromium (review 2026-09-29).

Each case reproduces a console defect the review found in a browser — or
exercises a console feature — against a REAL agents/chat_server.py process
on an ephemeral port, with its ledger, run dir and transcripts in tmp. The
browser is driven over CDP with tests/chat_cdp.py (stdlib only).

SKIPS cleanly when no chromium / google-chrome binary is installed (set
OPENBEAST_TEST_CHROME to point at one). CI's ubuntu image has google-chrome.

Run: python3 -m pytest tests/test_chat_console_browser.py -q
"""
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (AGENTS, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sessions  # noqa: E402
from chat_cdp import Browser, find_chrome  # noqa: E402
from test_chat_review import _RealServer  # noqa: E402

pytestmark = pytest.mark.skipif(find_chrome() is None,
                                reason="no chromium/chrome binary on this box")

LOGIN = "max@example.com"
KEY = "k-browser-test-0123456789"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("chat-browser")
    old_dir = sessions.SESSIONS_DIR
    srv = _RealServer(tmp, {"OPENBEAST_CHAT_POLL_MS": "50",
                            "OPENBEAST_CHAT_HEARTBEAT_S": "2",
                            "OPENBEAST_INFERENCE_URL": "http://127.0.0.1:9",
                            "OPENBEAST_CHAT_SLOT_URL": "http://127.0.0.1:9/api/slot",
                            "OPENBEAST_CHAT_GPU_LEASE": str(tmp / "no.lease"),
                            "BEAST_ARTIFACT": "false"})
    sessions.SESSIONS_DIR = str(srv.sdir)
    (srv.run / "clients.json").write_text(json.dumps({"version": 1, "devices": [{
        "id": "browser", "label": "browser",
        "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
        "enrolled_at": "now", "revoked_at": None, "scopes": ["chat"]}]}))
    browser = Browser()
    procs = []
    w = {"srv": srv, "b": browser, "tmp": tmp, "procs": procs,
         "base": f"http://127.0.0.1:{srv.port}"}
    try:
        yield w
    finally:
        browser.close()
        srv.kill()
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait(timeout=10)
        for rec in sessions.list_sessions(limit=1000):
            pg = rec.get("pgid")
            if rec.get("state") == "running" and isinstance(pg, int) and pg > 1 \
                    and pg != os.getpgrp():
                try:
                    os.killpg(pg, signal.SIGKILL)
                except OSError:
                    pass
        sessions.SESSIONS_DIR = old_dir


def _live_pid(w):
    p = subprocess.Popen(["sleep", "300"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    w["procs"].append(p)
    return p.pid


def _session(w, kind="agent", lines=(), state="running", title=None):
    sid = sessions.new_id(kind)
    tdir = w["tmp"] / "transcripts"
    tdir.mkdir(exist_ok=True)
    path = tdir / f"{sid}.{'jsonl' if kind == 'agent' else 'log'}"
    with open(path, "w") as f:
        for ln in lines:
            f.write((json.dumps(ln) if isinstance(ln, dict) else str(ln)) + "\n")
    pid = _live_pid(w) if state == "running" else os.getpid()
    sessions.register(sid, kind=kind, title=title or f"{kind} {sid[-4:]}",
                      pid=pid, pgid=pid, workdir=str(w["tmp"]),
                      transcript=str(path))
    if state != "running":
        sessions.finalize(sid, state, summary="fixture")
    return sid, str(path)


AGENT = [{"type": "start", "task": "port the zig module"},
         {"type": "iteration", "number": 1},
         {"type": "assistant", "content": "Reading the file first."},
         {"type": "done", "summary": "ported", "iterations": 1}]


def _open(w, hash_="#/", login=True, key=None):
    b = w["b"]
    b.headers({"Tailscale-User-Login": LOGIN} if login else {})
    b.goto(w["base"] + "/manifest.webmanifest")     # same origin, no app
    b.eval("localStorage.clear()")
    if key:
        b.eval(f"localStorage.setItem('bc.devkey', {json.dumps(key)})")
    b.goto(w["base"] + "/" + hash_)


def _visible(sel):
    return (f"(function(){{var n=document.querySelector({json.dumps(sel)});"
            f"return !!n && getComputedStyle(n).display !== 'none';}})()")


# ---------------------------------------------------------------------------
# review findings
# ---------------------------------------------------------------------------

def test_a_device_key_only_viewer_sees_the_transcript(world):
    """chat-security-3 / chat-browser-10: EventSource could not send the
    key, so the stream was 404 and nothing rendered."""
    sid, _ = _session(world, "agent", AGENT, state="done")
    _open(world, f"#/s/{sid}", login=False, key=KEY)
    b = world["b"]
    b.until("document.querySelector('#stream .e-done')")
    b.until("document.querySelector('#conn').textContent === 'closed'")


def test_reopening_a_session_repaints_it_not_the_list(world):
    """chat-browser-1: back -> reopen showed the list cards + new events."""
    sid, _ = _session(world, "agent", AGENT, state="done")
    _open(world, "#/")
    b = world["b"]
    b.until("document.querySelectorAll('#view .card').length > 0")
    b.eval(f"location.hash = '#/s/{sid}'")
    b.until("document.querySelector('#stream .e-done')")
    b.eval("location.hash = '#/'")
    b.until("document.querySelectorAll('#view .card').length > 0")
    b.eval(f"location.hash = '#/s/{sid}'")
    b.until("document.querySelector('#stream .e-done')")
    assert b.eval("document.querySelectorAll('#view .card').length") == 0
    assert b.eval("document.querySelectorAll('#stream .e-assistant').length") == 2


def test_hidden_header_buttons_are_really_hidden(world):
    """chat-browser-2: .icobtn{display:flex} beat [hidden]."""
    _open(world, "#/")
    b = world["b"]
    b.until("document.querySelector('#view .card, #view .empty')")
    for sel in ("#back", "#stopBtn", "#replayBtn", "#pauseBtn", "#exportBtn"):
        assert not b.eval(_visible(sel)), sel
    assert b.eval(_visible("#newBtn"))                 # negative control


def test_a_job_has_no_composer(world):
    """chat-browser-4: the composer stayed enabled on a job."""
    sid, _ = _session(world, "job", ["tick 1", "tick 2"], state="running")
    _open(world, f"#/s/{sid}", key=KEY)
    b = world["b"]
    b.until("document.querySelectorAll('#stream div').length >= 2")
    assert b.eval("document.querySelector('#msg').disabled") is True
    assert "Jobs have no inbox" in b.eval("document.querySelector('#hint').textContent")


def test_a_runner_error_event_does_not_flip_the_pill(world):
    """chat-security-4: a frame named `error` fired EventSource.onerror."""
    lines = [{"type": "start", "task": "t"},
             {"type": "error", "error": "model endpoint hiccup"},
             {"type": "assistant", "content": "carrying on"}]
    sid, _ = _session(world, "agent", lines, state="running")
    _open(world, f"#/s/{sid}")
    b = world["b"]
    b.until("document.querySelector('#stream .e-error')")
    b.until("document.querySelectorAll('#stream .e-assistant').length >= 2")
    time.sleep(0.3)
    assert b.eval("document.querySelector('#conn').textContent") == "live"


def test_a_big_log_opens_fast_at_its_tail(world):
    """chat-lifecycle-console-scroll-thrash: a forced layout per line made a
    1 MB log take ~80 s; now it opens at the tail in a few seconds."""
    lines = [f"line {i:06d} " + "x" * 90 for i in range(12000)]   # ~1.2 MB
    sid, _ = _session(world, "job", lines, state="done")
    _open(world, f"#/s/{sid}")
    b = world["b"]
    t0 = time.monotonic()
    b.until("document.querySelector('#conn').textContent === 'closed'",
            timeout=25)
    assert time.monotonic() - t0 < 20
    text = b.eval("document.querySelector('#stream').textContent")
    assert "line 011999" in text
    assert "line 000000" not in text
    # It STARTED at the tail (the old console replayed from byte 0 and
    # trimmed), and it says so.
    assert "showing the last" in text and "tap ↻ to replay everything" in text


def test_enter_sends_and_the_echo_is_reconciled(world):
    """chat-browser-8 (Enter inserted a newline) and chat-browser-7 (the
    pending echo stayed forever beside the real steer)."""
    sid, path = _session(world, "agent", [{"type": "start", "task": "t"}],
                         state="running")
    _open(world, f"#/s/{sid}", key=KEY)
    b = world["b"]
    b.until("document.querySelector('#msg') && !document.querySelector('#msg').disabled")
    b.eval("document.querySelector('#msg').focus()")
    b.send("Input.insertText", {"text": "skip small files"})
    for t in ("keyDown", "keyUp"):
        b.send("Input.dispatchKeyEvent", {"type": t, "key": "Enter",
                                          "code": "Enter",
                                          "windowsVirtualKeyCode": 13})
    b.until("document.querySelectorAll('#stream .e-pending').length === 1")
    assert b.eval("document.querySelector('#msg').value") == ""
    inbox = [json.loads(x) for x in open(sessions.inbox_path(sid)) if x.strip()]
    assert inbox[-1]["text"] == "skip small files"
    with open(path, "a") as f:        # what the runner logs when it consumes it
        f.write(json.dumps({"type": "steer", "op": "say",
                            "text": "skip small files",
                            "from": inbox[-1]["from"]}) + "\n")
    b.until("document.querySelectorAll('#stream .e-steer').length === 1")
    assert b.eval("document.querySelectorAll('#stream .e-pending').length") == 0


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------

def test_new_session_sheet_confirms_the_exact_argv_then_starts(world):
    """F-C1: + -> Job -> preset -> Review shows the server's argv -> Start."""
    p = world["srv"].run / "chat-presets.json"
    p.write_text(json.dumps({"presets": [{"name": "hello", "title": "Hello",
                                          "cmd": "echo hello-from-preset",
                                          "workdir": str(world["tmp"])}]}))
    os.chmod(p, 0o600)
    _open(world, "#/", key=KEY)
    b = world["b"]
    b.until(_visible("#newBtn"))
    b.eval("document.querySelector('#newBtn').click()")
    b.eval("document.querySelector('#tabJob').click()")
    b.until("document.querySelector('#nsPresets .preset')")
    b.eval("document.querySelector('#nsPresets .preset').click()")
    b.eval("document.querySelector('#nsReview').click()")
    b.until(_visible("#confirmSheet"))
    assert b.eval("document.querySelector('#csArgv').textContent") == \
        "/bin/bash -lc 'echo hello-from-preset'"
    b.eval("document.querySelector('#csGo').click()")
    b.until("location.hash.indexOf('#/s/') === 0")
    b.until("(document.querySelector('#stream')||{}).textContent"
            ".indexOf('hello-from-preset') >= 0", timeout=20)


def test_links_are_anchors_and_markup_stays_text(world):
    """F-C5: https + artifact URLs become rel=noopener anchors; nothing in a
    transcript is ever parsed as HTML."""
    art = "http://127.0.0.1:3004/a/0f8fad5b-d9cb-469f-a165-70867728950e"
    lines = ["see https://example.com/report.",
             "<img src=x onerror=\"document.title='pwned'\">",
             f"published {art}",
             "javascript:alert(1) and http://plain.example/x"]
    sid, _ = _session(world, "job", lines, state="done")
    _open(world, f"#/s/{sid}")
    b = world["b"]
    b.until("document.querySelector('#conn').textContent === 'closed'")
    links = b.eval("Array.from(document.querySelectorAll('#stream a'))"
                   ".map(a => [a.getAttribute('href'), a.rel, a.className])")
    assert links == [["https://example.com/report", "noopener noreferrer", ""],
                     [art, "noopener noreferrer", "art"]]
    assert b.eval("document.querySelectorAll('#stream img').length") == 0
    assert b.eval("document.title") != "pwned"


def test_pause_button_queues_a_pause(world):
    """F-C2."""
    sid, _ = _session(world, "agent", [{"type": "start", "task": "t"}],
                      state="running")
    _open(world, f"#/s/{sid}", key=KEY)
    b = world["b"]
    b.until(_visible("#pauseBtn"))
    b.eval("document.querySelector('#pauseBtn').click()")
    deadline = time.monotonic() + 10
    ops = []
    while time.monotonic() < deadline:
        if os.path.exists(sessions.inbox_path(sid)):
            ops = [json.loads(x)["op"] for x in open(sessions.inbox_path(sid))
                   if x.strip()]
            if ops:
                break
        time.sleep(0.1)
    assert ops == ["pause"]


def test_export_says_why_when_beast_artifact_is_off(world):
    """F-C6: the 409 detail reaches the phone."""
    sid, _ = _session(world, "agent", AGENT, state="done")
    _open(world, f"#/s/{sid}", key=KEY)
    b = world["b"]
    b.until(_visible("#exportBtn"))
    b.eval("document.querySelector('#exportBtn').click()")
    b.until("document.querySelector('#toast').textContent.indexOf('BEAST_ARTIFACT') >= 0")


def test_rig_strip_and_pwa_shell(world):
    """F-C7 strip in the list header; F-C3 manifest + service worker."""
    _session(world, "job", ["x"], state="running")
    _open(world, "#/")
    b = world["b"]
    b.until(_visible("#rig"))
    txt = b.eval("document.querySelector('#rig').textContent")
    assert "running" in txt and "llama: down" in txt and "GPU: free" in txt
    m = b.send("Page.getAppManifest")
    assert not m.get("errors"), m.get("errors")
    assert "icon-192.png" in m.get("data", "")
    scope = b.until("navigator.serviceWorker.getRegistration()"
                    ".then(r => r && r.active ? r.scope : null)", timeout=15)
    assert scope.endswith("/")


def test_offline_keeps_the_list_and_says_how_old_it_is(world):
    """F-C3 offline banner."""
    _session(world, "job", ["x"], state="done")
    _open(world, "#/")
    b = world["b"]
    b.until("document.querySelectorAll('#view .card').length > 0")
    b.send("Network.emulateNetworkConditions", {
        "offline": True, "latency": 0, "downloadThroughput": -1,
        "uploadThroughput": -1})
    try:
        b.until(_visible("#offline"), timeout=12)
        assert "last updated" in b.eval("document.querySelector('#offline').textContent")
        assert b.eval("document.querySelectorAll('#view .card').length") > 0
    finally:
        b.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": 0, "downloadThroughput": -1,
            "uploadThroughput": -1})
