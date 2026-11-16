"""Stopping the stdio MCP server mid-tool-call must stop the tool command.

Review finding (mcp-tools-sandbox): run_reaped starts every command in its
own session and only the caller's proc.wait enforced its timeout. runner.py
kills in-flight groups on SIGTERM, mcp_server.py did not (atexit does not
run on a default SIGTERM death), so an OpenCode teardown that terminated
the stdio server mid-`bash` left the command running under init forever.

Drives the real agents/mcp_server.py over stdio JSON-RPC: a bash call that
records its pid and sleeps, then SIGTERM/SIGHUP to the server.

Run: python3 -m pytest tests/test_mcp_server_stop.py -q
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytest.importorskip("mcp")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _gone(pid: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while _alive(pid):
        if time.time() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _send(proc, obj):
    proc.stdin.write((json.dumps(obj) + "\n").encode())
    proc.stdin.flush()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_signal_kills_in_flight_bash_child(tmp_path, sig):
    pidfile = tmp_path / "pid"
    env = dict(os.environ, HOME=str(tmp_path))
    srv = subprocess.Popen(
        [sys.executable, str(ROOT / "agents" / "mcp_server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env, cwd=str(tmp_path))
    pid = None
    try:
        _send(srv, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "capabilities": {},
                               "clientInfo": {"name": "t", "version": "0"}}})
        srv.stdout.readline()  # initialize result
        _send(srv, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(srv, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "bash", "arguments": {
                        "command": f"echo $$ > {pidfile}; exec sleep 300",
                        "timeout": 600}}})
        deadline = time.time() + 20
        while time.time() < deadline:
            if pidfile.exists() and pidfile.read_text().strip():
                pid = int(pidfile.read_text().strip())
                break
            time.sleep(0.05)
        assert pid, "bash call never started"
        srv.send_signal(sig)
        srv.wait(timeout=15)
        assert _gone(pid), f"bash child {pid} outlived the MCP server"
    finally:
        if srv.poll() is None:
            srv.kill()
            srv.wait()
        if pid and _alive(pid):
            os.kill(pid, signal.SIGKILL)
