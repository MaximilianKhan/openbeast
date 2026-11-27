"""Review 2026-10-27 eval-harness fixes (R6-6, R6-8).

Every case builds its own input: no GPU, no real server, no real fixture
dir, no real lease. Each asserts the negative control too.
"""

import importlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
sys.path.insert(0, str(EVALS))
sys.path.insert(0, str(ROOT / "agents"))


def _fresh(mod):
    for m in ("cache", "run_eval", "benchmark_all", "scoring", "profile_mtp"):
        sys.modules.pop(m, None)
    return importlib.import_module(mod)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is dead for our purposes (/proc on Linux, ps where there is none).
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                            capture_output=True, text=True).stdout.strip()
        return bool(st) and not st.startswith("Z")


# ---------------------------------------------------------------------------
# R6-8: profile_mtp stops only its own server and asks the GPU lease.
# ---------------------------------------------------------------------------

@pytest.fixture
def pm(tmp_path, monkeypatch):
    mod = _fresh("profile_mtp")
    ba = mod._ba
    port = _free_port()
    monkeypatch.setattr(mod, "PORT", port)
    monkeypatch.setattr(ba, "LLAMA_PORT", port)
    monkeypatch.setattr(mod, "RESULTS", tmp_path)
    monkeypatch.setattr(mod, "WEIGHTS", str(tmp_path))
    (tmp_path / "w.gguf").write_bytes(b"x")
    serve = tmp_path / "serve.sh"
    serve.write_text("#!/bin/sh\nexec sleep 60\n")
    serve.chmod(0o755)
    monkeypatch.setattr(mod, "SERVE", serve)
    monkeypatch.setattr(ba, "gpu_lease_check", lambda: (ba.LEASE_OURS, "ours"))
    calls = []
    real_run = subprocess.run

    def recording_run(cmd, *a, **k):
        calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
        if cmd and cmd[0] in ("pkill", "killall"):
            return subprocess.CompletedProcess(cmd, 1, "", "")   # never for real
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", recording_run)
    ba._own_server["proc"] = None
    yield mod, calls
    p = ba._own_server["proc"]
    if p is not None and p.poll() is None:
        os.killpg(p.pid, signal.SIGKILL)


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_profile_mtp_stop_never_pkills(pm):
    mod, calls = pm
    bystander = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        mod.stop_server()                         # we started nothing: no-op
        assert not any(c and c[0] in ("pkill", "killall") for c in calls), calls
        proc = mod.start_server("w.gguf", 4096, 4, 0.0)
        assert _alive(proc.pid)                   # negative control
        mod.stop_server()
        assert proc.poll() is not None            # ours: stopped
        assert _alive(bystander.pid)              # not ours: untouched
        assert not any(c and c[0] in ("pkill", "killall") for c in calls), calls
    finally:
        bystander.kill()
        bystander.wait()


def test_profile_mtp_refuses_busy_port(pm):
    import socket
    mod, _ = pm
    s = socket.socket()
    s.bind(("127.0.0.1", mod.PORT))
    s.listen(16)                  # two probes connect and are never accepted
    try:
        with pytest.raises(mod.PortBusy):
            mod.start_server("w.gguf", 4096, 4, 0.0)
        # A busy port aborts the run instead of being recorded as a bad config.
        with pytest.raises(mod.PortBusy):
            mod.run_config("x", "w.gguf", 4096, 4, 0.0)
    finally:
        s.close()
    assert mod._ba._own_server["proc"] is None


def test_profile_mtp_refuses_held_lease(pm, monkeypatch):
    mod, _ = pm
    ba = mod._ba
    monkeypatch.setattr(ba, "gpu_lease_check", lambda: (ba.LEASE_HELD, "held by campaign"))
    with pytest.raises(mod.GpuLeaseHeld):
        mod.start_server("w.gguf", 4096, 4, 0.0)
    with pytest.raises(mod.GpuLeaseHeld):
        mod.ensure_gpu_lease([], exec_fn=lambda *a: pytest.fail("must not exec"))
    assert ba._own_server["proc"] is None


def test_profile_mtp_takes_free_lease(pm, monkeypatch, tmp_path):
    mod, _ = pm
    ba = mod._ba
    monkeypatch.setattr(ba, "gpu_lease_check", lambda: (ba.LEASE_FREE, "free"))
    monkeypatch.delenv(ba._REEXEC_MARK, raising=False)
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    seen = []
    mod.ensure_gpu_lease(["--models", "x"], exec_fn=lambda f, argv, env: seen.append((argv, env)))
    argv, env = seen[0]
    assert argv[:3] == ["bash", ba.GPU_LEASE_SH, "run"] and argv[-2:] == ["--models", "x"]
    assert env[ba._REEXEC_MARK] == "1"
    # Ours already: run on, no re-exec.
    monkeypatch.setattr(ba, "gpu_lease_check", lambda: (ba.LEASE_OURS, "ours"))
    mod.ensure_gpu_lease([], exec_fn=lambda *a: pytest.fail("must not exec"))
