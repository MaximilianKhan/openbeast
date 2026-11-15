#!/usr/bin/env python3
"""scripts/client.sh (the openbeast-client CLI) against a fake rig.

`status` and `refresh-config` run for real, with a throwaway HOME, against a
stdlib HTTP server on an ephemeral loopback port that plays the rig's model
API and its beast-slot endpoint. `tailscale` is a stub. Nothing outside the
temp dir is read or written except this checkout's own opencode.json, which
refresh-config reads as the static catalog.

  - the slot line names the failure it saw: busy (503, retried once), the
    dashboard down (502), not published (404), nothing answering (000)
  - on a rig whose backend is not llama, a default model id the rig does not
    serve is flagged, and refresh-config pins the id beast-slot reports when
    /v1/models cannot be read

Run: python3 -m pytest tests/test_client_cli.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(REPO, "scripts", "client.sh")

pytestmark = pytest.mark.skipif(not shutil.which("curl"), reason="curl not installed")

_SLOT_LLAMA = {"beast_slot": 2, "min_client": 1, "healthy": True,
               "model": {"id": "qwen", "ctx": 4096}, "slots": {"total": 1, "busy": 0},
               "capacity": {}, "services": {}, "auth": "open"}
_SLOT_VLLM = dict(_SLOT_LLAMA, backend="vllm", model={"id": "GLM-5.3-Flash", "ctx": 131072})


class FakeRig:
    """routes: path -> list of (status, body) answered in turn (last repeats)."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.hits = []
        rig = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                rig.hits.append(path)
                answers = rig.routes.get(path)
                if not answers:
                    st, body = 404, "nope"
                else:
                    st, body = answers.pop(0) if len(answers) > 1 else answers[0]
                data = (body if isinstance(body, str) else json.dumps(body)).encode()
                self.send_response(st)
                if st == 503:
                    self.send_header("Retry-After", "1")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _home(tmp_path, rig_url, slot_url, default="openbeast-rig/stub-static"):
    home = tmp_path / "home"
    (home / ".config" / "opencode").mkdir(parents=True)
    venv = home / ".openbeast-client" / "venv" / "bin"
    venv.mkdir(parents=True)
    os.symlink(sys.executable, venv / "python3")      # >=3.10, as setup-client vets
    (home / ".openbeast-client.env").write_text(
        f"OPENBEAST_AGENT_INFERENCE_URL='{rig_url}/v1'\nOPENBEAST_SLOT_URL='{slot_url}'\n")
    oc = home / ".config" / "opencode" / "opencode.json"
    oc.write_text(json.dumps({
        "model": default, "small_model": default,
        "mcp": {"openbeast-tools": {}},
        "provider": {"openbeast-rig": {"options": {"baseURL": f"{rig_url}/v1", "apiKey": "not-needed"},
                                       "models": {"stub-static": {"name": "Stub"}}}}}))
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "tailscale").write_text("#!/bin/sh\nexit 0\n")
    (stub / "tailscale").chmod(0o755)
    return home, oc, stub


def _run(home, stub, *args):
    env = {"HOME": str(home), "PATH": f"{stub}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    # /bin/bash first: on macOS that is the stock 3.2 this CLI must run on.
    bash = shutil.which("bash", path="/bin:/usr/bin") or "bash"
    r = subprocess.run([bash, CLIENT, *args], capture_output=True, text=True, env=env, timeout=60)
    return r.stdout + r.stderr


def _status_with_slot(tmp_path, slot_answers):
    rig = FakeRig({"/health": [(200, '{"status":"ok"}')],
                   "/v1/models": [(200, {"data": [{"id": "qwen"}]})],
                   "/api/slot": slot_answers})
    try:
        home, _, stub = _home(tmp_path, rig.url, rig.url + "/api/slot")
        return _run(home, stub, "status"), rig
    finally:
        rig.close()


def test_busy_dashboard_is_retried_and_named(tmp_path):
    out, rig = _status_with_slot(tmp_path, [(503, "busy\n")])
    assert "busy (503 twice)" in out and "not an outage" in out, out
    assert "not published" not in out, out
    assert rig.hits.count("/api/slot") == 2, rig.hits


def test_one_503_then_an_answer_is_a_green_line(tmp_path):
    out, _ = _status_with_slot(tmp_path, [(503, "busy\n"), (200, _SLOT_LLAMA)])
    assert "✓ beast-slot: healthy | model qwen" in out, out


def test_502_says_the_dashboard_is_down_not_unpublished(tmp_path):
    out, _ = _status_with_slot(tmp_path, [(502, "")])
    assert "dashboard behind it is not" in out and "ext.sh enable dashboard" in out, out
    assert "not published" not in out, out


def test_404_is_still_not_published(tmp_path):
    out, _ = _status_with_slot(tmp_path, [(404, "")])
    assert "beast-slot status not published" in out and "--publish-slot" in out, out


def test_nothing_answering_says_so(tmp_path):
    rig = FakeRig({"/health": [(200, "ok")], "/v1/models": [(200, {"data": [{"id": "q"}]})]})
    try:
        home, _, stub = _home(tmp_path, rig.url, f"http://127.0.0.1:{_closed_port()}/api/slot")
        out = _run(home, stub, "status")
    finally:
        rig.close()
    assert "did not answer within 5 s" in out, out


def test_non_llama_rig_flags_a_default_it_does_not_serve(tmp_path):
    out, _ = _status_with_slot(tmp_path, [(200, _SLOT_VLLM)])
    assert "opencode default openbeast-rig/stub-static is not the id this vllm rig serves" in out, out
    # control: a llama rig ignores the id, so nothing is flagged there
    out, _ = _status_with_slot(tmp_path / "b", [(200, _SLOT_LLAMA)])
    assert "is not the id" not in out, out


def test_refresh_pins_the_slot_id_when_models_cannot_be_read(tmp_path):
    # vLLM rig mid-restart: /v1/models 503, beast-slot still says what it serves.
    rig = FakeRig({"/v1/models": [(503, "")], "/api/slot": [(200, _SLOT_VLLM)]})
    try:
        home, oc, stub = _home(tmp_path, rig.url, rig.url + "/api/slot")
        out = _run(home, stub, "refresh-config")
    finally:
        rig.close()
    cfg = json.loads(oc.read_text())
    assert cfg["model"] == "openbeast-rig/GLM-5.3-Flash", (cfg["model"], out)
    rows = cfg["provider"]["openbeast-rig"]["models"]
    assert rows["GLM-5.3-Flash"]["limit"]["context"] == 131072
    static = [r["name"] for k, r in rows.items() if k != "GLM-5.3-Flash"]
    assert static and all(n.endswith("(llama-server rigs only)") for n in static), static


def test_refresh_on_a_llama_rig_is_unchanged(tmp_path):
    # control: llama ignores ids — no marking, and with /v1/models down the
    # slot id is used only as the live row, exactly like a /v1/models answer.
    rig = FakeRig({"/v1/models": [(200, {"data": [{"id": "qwen"}]})],
                   "/props": [(200, {"default_generation_settings": {"n_ctx": 4096}})],
                   "/api/slot": [(200, _SLOT_LLAMA)]})
    try:
        home, oc, stub = _home(tmp_path, rig.url, rig.url + "/api/slot")
        _run(home, stub, "refresh-config")
    finally:
        rig.close()
    cfg = json.loads(oc.read_text())
    assert cfg["model"] == "openbeast-rig/qwen"
    assert not any("llama-server rigs only" in r.get("name", "")
                   for r in cfg["provider"]["openbeast-rig"]["models"].values())
