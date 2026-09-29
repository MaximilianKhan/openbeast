"""capture_server_config describes the llama-server that serves the eval's
port — not whichever one pgrep lists first.

Review follow-up to eval-harness-2: with a second llama-server running (a
ChunkHound sidecar on :8081, a sibling worktree's measurement server), line
0 of `pgrep -ax llama-server` could be the other process, and its
--reasoning-budget became this run's cache era (.rbN). pgrep is stubbed and
/proc is a temp tree, except in the one test that uses a real listening
socket owned by this test process.
"""

import importlib
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

SIDECAR = "/opt/llama/llama-server -m /w/side.gguf --port 8081 --reasoning-budget 4096"
EVAL = "/opt/llama/llama-server -m /w/main.gguf --port 8080 --reasoning-budget 20480"


@pytest.fixture
def re_(monkeypatch):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    mod = importlib.import_module("run_eval")
    mod._pgrep_lines = []
    real_run = subprocess.run

    def fake_run(cmd, *a, **k):
        if list(cmd[:2]) == ["pgrep", "-ax"]:
            out = "".join(line + "\n" for line in mod._pgrep_lines)
            return subprocess.CompletedProcess(cmd, 0 if out else 1, out, "")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return mod


def _fake_proc(root: Path, listeners: dict[int, str], fds: dict[str, list[str]]):
    """listeners: port -> inode; fds: pid -> [inode, ...] it holds."""
    (root / "net").mkdir(parents=True)
    rows = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
            "retrnsmt   uid  timeout inode"]
    for i, (port, ino) in enumerate(listeners.items()):
        rows.append(f"   {i}: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 "
                    f"00:00000000 00000000  1000        0 {ino} 1 0000000000000000 100 0 0 10 0")
    # An ESTABLISHED client socket to the same port must not count.
    rows.append("   9: 0100007F:D431 0100007F:1F90 01 00000000:00000000 "
                "00:00000000 00000000  1000        0 999 1 0000000000000000 20 4 0 10 -1")
    (root / "net" / "tcp").write_text("\n".join(rows) + "\n")
    (root / "net" / "tcp6").write_text(rows[0] + "\n")
    for pid, inodes in fds.items():
        d = root / pid / "fd"
        d.mkdir(parents=True)
        os.symlink("/dev/null", d / "0")
        for n, ino in enumerate(inodes, start=3):
            os.symlink(f"socket:[{ino}]", d / str(n))


def test_picks_the_process_listening_on_the_eval_port(re_, tmp_path, monkeypatch):
    # The sidecar is listed FIRST — what line 0 used to describe.
    re_._pgrep_lines = [f"100 {SIDECAR}", f"200 {EVAL}"]
    _fake_proc(tmp_path, {8081: "111", 8080: "222"}, {"100": ["111"], "200": ["222", "999"]})
    monkeypatch.setattr(re_, "PROC_ROOT", str(tmp_path))
    info = re_.capture_server_config("http://localhost:8080/v1")
    assert info["reasoning_budget"] == "20480"
    assert info["model_path"] == "/w/main.gguf"
    side = re_.capture_server_config("http://127.0.0.1:8081/v1")
    assert side["reasoning_budget"] == "4096"


def test_socket_owner_beats_a_misleading_command_line(re_, tmp_path, monkeypatch):
    """A port set via LLAMA_ARG_PORT is not on the command line: the socket
    is the truth, the flag only the fallback."""
    re_._pgrep_lines = [f"100 {SIDECAR} --port 8080", "200 llama-server --reasoning-budget 512"]
    _fake_proc(tmp_path, {8080: "222"}, {"100": [], "200": ["222"]})
    monkeypatch.setattr(re_, "PROC_ROOT", str(tmp_path))
    assert re_.capture_server_config("http://localhost:8080/v1")["reasoning_budget"] == "512"


def test_falls_back_to_the_port_flag_when_fds_are_unreadable(re_, tmp_path, monkeypatch):
    # Another uid's server: no /proc/<pid>/fd to read. No --port = 8080.
    re_._pgrep_lines = [f"100 {SIDECAR}", "200 llama-server --reasoning-budget 20480"]
    _fake_proc(tmp_path, {8080: "222"}, {})
    monkeypatch.setattr(re_, "PROC_ROOT", str(tmp_path))
    assert re_.capture_server_config("http://localhost:8080/v1")["reasoning_budget"] == "20480"


def test_ambiguous_or_absent_is_empty_not_a_guess(re_, tmp_path, monkeypatch):
    _fake_proc(tmp_path, {}, {})
    monkeypatch.setattr(re_, "PROC_ROOT", str(tmp_path))
    re_._pgrep_lines = ["100 llama-server --reasoning-budget 1",
                        "200 llama-server --reasoning-budget 2"]      # both default 8080
    assert re_.capture_server_config("http://localhost:8080/v1") == {}
    re_._pgrep_lines = [f"100 {SIDECAR}"]                             # nobody on 8080
    assert re_.capture_server_config("http://localhost:8080/v1") == {}
    re_._pgrep_lines = []
    assert re_.capture_server_config("http://localhost:8080/v1") == {}
    # Negative control: one server on the port is found without any /proc.
    re_._pgrep_lines = [f"200 {EVAL}"]
    assert re_.capture_server_config("http://localhost:8080/v1")["reasoning_budget"] == "20480"


def test_a_remote_base_url_never_describes_a_local_server(re_, tmp_path, monkeypatch):
    re_._pgrep_lines = [f"200 {EVAL}"]
    _fake_proc(tmp_path, {8080: "222"}, {"200": ["222"]})
    monkeypatch.setattr(re_, "PROC_ROOT", str(tmp_path))
    assert re_.capture_server_config("http://192.0.2.10:8080/v1") == {}    # TEST-NET-1
    assert re_.capture_server_config("http://127.0.0.1:8080/v1")["reasoning_budget"] == "20480"


def test_real_proc_resolves_a_real_listening_socket(re_):
    """Against the real /proc: THIS process listens; the stub pgrep claims a
    stranger's command line is on that port. The socket wins."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        re_._pgrep_lines = [f"{os.getpid()} llama-server --port 1 --reasoning-budget 777",
                            f"999999999 llama-server --port {port} --reasoning-budget 1"]
        info = re_.capture_server_config(f"http://127.0.0.1:{port}/v1")
        assert info["reasoning_budget"] == "777"
    finally:
        s.close()


def test_run_eval_asks_about_its_own_base_url(re_, tmp_path, monkeypatch):
    import collections
    import json
    import shutil
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_a.json").write_text(json.dumps({
        "id": "01_a", "name": "a", "difficulty": "easy", "task": "a",
        "validation": {"type": "bash", "script": "true"}, "max_iter": 3}))
    monkeypatch.setattr(re_, "TASKS_DIR", str(tasks))
    monkeypatch.setattr(re_, "RESULTS_DIR", str(tmp_path / "results"))
    # The disk floor records the unit without running an agent.
    U = collections.namedtuple("U", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: U(10**12, 10**12 - 10**9, 10**9))
    monkeypatch.setenv("OPENBEAST_EVAL_MIN_FREE_GB", "5")
    asked = []
    monkeypatch.setattr(re_, "capture_server_config", lambda url: asked.append(url) or {})
    monkeypatch.setattr(re_, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(re_, "capture_inference_engine_info", lambda: {})
    re_.run_eval(model_name="m", base_url="http://127.0.0.1:18081/v1")
    assert asked == ["http://127.0.0.1:18081/v1"]
