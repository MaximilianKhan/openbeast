"""benchmark_all's server lifecycle: stop by PID, refuse a foreign port,
don't wait on a dead serve script, don't cool off after no GPU work.

Review findings lifecycle-4 / efficiency-4. Every server here is a fake
serve script in a temp REPO_DIR on a private port; subprocess.run is
recorded so NOTHING in this file can ever issue a real `pkill` — even when
run against the old code as a negative control.
"""

import importlib
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def ba(tmp_path, monkeypatch):
    for mod in ("cache", "run_eval", "benchmark_all"):
        sys.modules.pop(mod, None)
    mod = importlib.import_module("benchmark_all")
    port = _free_port()
    monkeypatch.setattr(mod, "REPO_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(mod, "LLAMA_PORT", port)
    monkeypatch.setattr(mod, "LLAMA_HEALTH_URL", f"http://127.0.0.1:{port}/health")
    # The real gpu-lease.sh, but on a private, empty lease dir: FREE, and
    # never the rig's own .run/gpu.lease.
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("OPENBEAST_BENCH_UNDER_LEASE", raising=False)
    # The cool-off reads the card through this one function: unreadable by
    # default, so no test here runs nvidia-smi (the cool-off tests below
    # hand it their own readings).
    monkeypatch.setattr(mod, "_real_gpu_temperature_c", mod.gpu_temperature_c, raising=False)
    monkeypatch.setattr(mod, "gpu_temperature_c", lambda: None)
    monkeypatch.delenv("OPENBEAST_BENCH_COOLOFF_TEMP_C", raising=False)
    calls = []
    real_run = subprocess.run

    def recording_run(cmd, *a, **k):
        calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
        if cmd and cmd[0] in ("pkill", "killall"):
            return subprocess.CompletedProcess(cmd, 1, "", "")   # never for real
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(mod.subprocess, "run", recording_run)
    mod._test_calls = calls
    yield mod
    if getattr(mod, "_own_server", None) and mod._own_server.get("proc"):
        try:
            os.killpg(mod._own_server["proc"].pid, signal.SIGKILL)
        except OSError:
            pass


def _serve(tmp_path: Path, body: str) -> str:
    (tmp_path / "serve.sh").write_text("#!/bin/bash\n" + body + "\n")
    return "serve.sh"


def test_stop_targets_only_our_server(ba, tmp_path):
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                              "llama-server"], start_new_session=True)
    proc = None
    try:
        proc, _ = ba.start_model(_serve(tmp_path, "exec sleep 30"), "m")
        ba.stop_llama_server()
        assert proc.poll() is not None, "our server survived stop"
        assert decoy.poll() is None, "a foreign 'llama-server' process was killed"
        assert not any(c and c[0] in ("pkill", "killall") for c in ba._test_calls)
    finally:
        decoy.kill()
        decoy.wait()
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_stop_without_own_server_is_a_noop(ba):
    ba.stop_llama_server()
    assert not any(c and c[0] in ("pkill", "killall") for c in ba._test_calls)


def test_refuses_a_port_someone_else_serves(ba, tmp_path):
    held = socket.socket()
    held.bind(("127.0.0.1", ba.LLAMA_PORT))
    held.listen(1)
    try:
        with pytest.raises(ba.PortBusy):
            ba.start_model(_serve(tmp_path, "exec sleep 30"), "m")
        out = ba.benchmark_model({"name": "M", "slug": "m", "serve": "serve.sh"},
                                 None, None)
        assert "already serving" in out["error"] and out["gpu_work"] is False
    finally:
        held.close()


def test_health_wait_gives_up_when_the_serve_script_dies(ba, tmp_path):
    proc, _ = ba.start_model(_serve(tmp_path, "exit 3"), "m")
    t0 = time.time()
    assert ba.wait_for_health(timeout=30, proc=proc) is False
    assert time.time() - t0 < 5


def test_cooloff_only_after_live_gpu_work(ba, monkeypatch):
    slept = []
    monkeypatch.setattr(ba.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ba.scoring, "score_run", lambda r: {
        "capability": 0, "problem_solving": 0, "language_breadth": 0,
        "accuracy": 0, "speed": 0})
    outcomes = iter([
        {"slug": "a", "name": "A", "error": "model failed to become healthy",
         "gpu_work": False},
        {"slug": "b", "name": "B", "results": {"tasks": [{}], "summary": {
            "total": 1, "live_units": 0, "cache_hits": 1}}},
        {"slug": "c", "name": "C", "results": {"tasks": [{}], "summary": {
            "total": 1, "live_units": 1, "cache_hits": 0}}},
        {"slug": "d", "name": "D", "error": "eval crashed: boom"},   # unknown: cool
        {"slug": "e", "name": "E", "results": {"tasks": [{}], "summary": {"total": 1}}},
    ])
    monkeypatch.setattr(ba, "benchmark_model", lambda *a, **k: next(outcomes))
    models = [{"slug": s, "name": s.upper(), "serve": "x"} for s in "abcde"]
    ba.run_sweep(models, None, None, update_leaderboard=False)
    # a: no load; b: full cache replay; c: live; d: crashed mid-eval; e: last.
    assert slept == [ba.COOLOFF_SECONDS, ba.COOLOFF_SECONDS]


# --- the cool-off is gated on GPU temperature (review 2026-10-09, perf F8) ---
# A flat 600 s between models cost a 20-model sweep 190 idle minutes. Every
# reading here is handed in; `sleep` is recorded, never taken.

def _cooloff(ba, monkeypatch, readings):
    """Run cool_off() against a scripted thermometer. Returns (seconds the
    function reports, the sleeps it asked for, readings it consumed)."""
    slept, feed, used = [], iter(readings), []

    def read():
        used.append(next(feed))
        return used[-1]

    monkeypatch.setattr(ba.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ba, "gpu_temperature_c", read)
    return ba.cool_off(), slept, used


def test_cooloff_ends_when_the_gpu_has_cooled(ba, monkeypatch):
    # 74°C at the stop, then falling; at or below 50°C on the 6th poll (90 s).
    waited, slept, _ = _cooloff(ba, monkeypatch, [74, 70, 66, 61, 57, 53, 50, 48, 47])
    assert waited == 90 == sum(slept)
    assert set(slept) == {ba.COOLOFF_POLL_SECONDS}
    assert waited < ba.COOLOFF_SECONDS


def test_cooloff_never_shorter_than_the_floor(ba, monkeypatch):
    """An already-cool reading does not skip the break: the core sensor
    falls faster than what it does not report."""
    waited, slept, _ = _cooloff(ba, monkeypatch, [40] * 50)
    assert waited == ba.COOLOFF_MIN_SECONDS == sum(slept)


def test_cooloff_is_capped_at_the_old_fixed_wait(ba, monkeypatch):
    """Negative control for the gate: a card that never cools waits exactly
    the ceiling, not forever."""
    waited, slept, _ = _cooloff(ba, monkeypatch, [80] * 100)
    assert waited == ba.COOLOFF_SECONDS == sum(slept) == 600


def test_cooloff_keeps_the_fixed_wait_when_temperature_is_unreadable(ba, monkeypatch):
    waited, slept, used = _cooloff(ba, monkeypatch, [None])
    assert waited == 600 and slept == [600] and used == [None]


def test_cooloff_waits_out_the_rest_when_the_reading_is_lost(ba, monkeypatch):
    waited, slept, _ = _cooloff(ba, monkeypatch, [74, 70, None])
    assert waited == 600 and sum(slept) == 600      # 15 + 15 + the remaining 570
    assert slept == [15, 15, 570]


def test_cooloff_threshold_is_configurable_and_can_be_turned_off(ba, monkeypatch):
    monkeypatch.setenv("OPENBEAST_BENCH_COOLOFF_TEMP_C", "65")
    waited, _, _ = _cooloff(ba, monkeypatch, [74, 70, 66, 64, 63, 62, 61])
    assert waited == 60                              # cool at 45 s; the floor holds it to 60
    for off in ("off", "0"):
        monkeypatch.setenv("OPENBEAST_BENCH_COOLOFF_TEMP_C", off)
        waited, slept, used = _cooloff(ba, monkeypatch, [30] * 5)
        assert waited == 600 and slept == [600] and used == []   # the card is not even read
    assert ba.cooloff_target_c({"OPENBEAST_BENCH_COOLOFF_TEMP_C": "junk"}) == ba.COOLOFF_TEMP_C
    assert ba.cooloff_target_c({}) == ba.COOLOFF_TEMP_C


def test_gpu_temperature_parses_nvidia_smi_without_running_it(ba, monkeypatch):
    """The reader itself, against canned output: hottest card wins, and
    anything it cannot parse is 'unreadable', never 0°C."""
    real = ba._real_gpu_temperature_c      # the fixture stubbed the name on `ba`
    seen = []

    def fake(out="", rc=0, raises=None):
        def run(cmd, *a, **k):
            seen.append(cmd[0])
            if raises:
                raise raises
            return subprocess.CompletedProcess(cmd, rc, out, "")
        return run

    for out, rc, raises, want in (("62\n", 0, None, 62.0), ("55\n71\n48\n", 0, None, 71.0),
                                  ("[N/A]\n", 0, None, None), ("", 0, None, None),
                                  ("62\n", 9, None, None),
                                  ("", 0, FileNotFoundError("nvidia-smi"), None),
                                  ("", 0, subprocess.TimeoutExpired("nvidia-smi", 5), None)):
        monkeypatch.setattr(ba.subprocess, "run", fake(out, rc, raises))
        assert real() == want, (out, rc, raises)
    assert set(seen) == {"nvidia-smi"}


def test_sweep_cools_off_through_the_gate(ba, monkeypatch):
    """run_sweep must go through cool_off(): two live models, a cooled card,
    and the break between them is the floor, not 600 s."""
    slept = []
    monkeypatch.setattr(ba.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ba, "gpu_temperature_c", lambda: 41.0)
    monkeypatch.setattr(ba.scoring, "score_run", lambda r: {
        "capability": 0, "problem_solving": 0, "language_breadth": 0,
        "accuracy": 0, "speed": 0})
    monkeypatch.setattr(ba, "benchmark_model", lambda m, *a, **k: {
        "slug": m["slug"], "name": m["name"], "results": {"tasks": [{}], "summary": {
            "total": 1, "live_units": 1, "cache_hits": 0}}})
    models = [{"slug": s, "name": s.upper(), "serve": "x"} for s in "ab"]
    ba.run_sweep(models, None, None, update_leaderboard=False)
    assert sum(slept) == ba.COOLOFF_MIN_SECONDS     # one break, after `a` only


def test_low_disk_stops_the_sweep_without_a_cooloff(ba, monkeypatch):
    """A disk-floor abort holds for every model: the sweep stops rather than
    loading each remaining model to abort on its first unit after 600 s."""
    slept, ran = [], []
    monkeypatch.setattr(ba.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ba.scoring, "score_run", lambda r: {
        "capability": 0, "problem_solving": 0, "language_breadth": 0,
        "accuracy": 0, "speed": 0})
    tasks = [{"passed": True, "from_cache": True},
             {"passed": False, "reason": "low_disk"}]
    # run_eval's own count: the low_disk row never ran the agent.
    summary = {"total": 2, "cache_hits": 1, "live_units": 0}

    def fake(model, *a, **k):
        ran.append(model["slug"])
        return {"slug": model["slug"], "name": model["name"],
                "results": {"tasks": list(tasks), "summary": dict(summary)}}

    monkeypatch.setattr(ba, "benchmark_model", fake)
    models = [{"slug": s, "name": s.upper(), "serve": "x"} for s in "abc"]
    out = ba.run_sweep(models, None, None, update_leaderboard=False)
    assert ran == ["a"] and slept == []
    assert [s["slug"] for s in out["skipped"]] == ["b", "c"]


def test_live_units_exclude_rows_that_never_ran_the_agent(tmp_path, monkeypatch):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    import json as _json
    import shutil
    import collections
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    run_eval = importlib.import_module("run_eval")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_a.json").write_text(_json.dumps({
        "id": "01_a", "name": "a", "difficulty": "easy", "task": "a",
        "validation": {"type": "bash", "script": "true"}, "max_iter": 3}))
    run_eval.TASKS_DIR = str(tasks)
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    U = collections.namedtuple("U", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: U(10**12, 10**12 - 10**9, 10**9))
    monkeypatch.setenv("OPENBEAST_EVAL_MIN_FREE_GB", "5")
    for name in ("capture_server_config", "capture_gpu_info", "capture_inference_engine_info"):
        monkeypatch.setattr(run_eval, name, lambda *a, **k: {})
    res = run_eval.run_eval(model_name="m")
    assert res["tasks"][0]["reason"] == "low_disk"
    assert res["summary"]["live_units"] == 0


# ---------------------------------------------------------------------------
# The GPU lease (review lifecycle-4 residual): refuse someone else's lease
# before loading anything; run under one of our own; take a free one.
# ---------------------------------------------------------------------------

def _start_ticks(pid: int) -> str:
    with open(f"/proc/{pid}/stat") as fh:
        return fh.read().rsplit(") ", 1)[1].split()[19]


def _write_lease(run_dir: Path, pid: int) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "gpu.lease").write_text(
        f"pid={pid}\nstart={_start_ticks(pid)}\nlabel=other campaign\nsince=now\n")


@pytest.fixture
def stranger():
    """A live process that is NOT our ancestor — someone else's campaign."""
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    yield p
    p.kill()
    p.wait()


def test_refuses_to_load_while_someone_else_holds_the_lease(ba, tmp_path, stranger):
    _write_lease(tmp_path / "run", stranger.pid)
    marker = tmp_path / "loaded"
    _serve(tmp_path, f"touch {marker}; exec sleep 30")
    with pytest.raises(ba.GpuLeaseHeld, match="HELD by pid"):
        ba.start_model("serve.sh", "m")
    out = ba.benchmark_model({"name": "M", "slug": "m", "serve": "serve.sh"}, None, None)
    assert "HELD by pid" in out["error"] and out["gpu_work"] is False
    assert not ba.restart_server("serve.sh", "m", health_timeout=1)
    with pytest.raises(ba.GpuLeaseHeld):
        ba.ensure_gpu_lease(["--models", "m"], exec_fn=lambda *a: pytest.fail("exec'd"))
    time.sleep(0.3)
    assert not marker.exists(), "a model was loaded under someone else's lease"
    assert ba._own_server["proc"] is None


def test_a_lease_held_by_our_ancestor_is_ours(ba, tmp_path):
    """A campaign runs us under `gpu-lease.sh run`: its lease must not refuse
    its own sweep. (Holder = this test process, an ancestor of the check.)"""
    _write_lease(tmp_path / "run", os.getpid())
    assert ba.require_gpu_lease() == ba.LEASE_OURS
    ba.ensure_gpu_lease(["--models", "m"], exec_fn=lambda *a: pytest.fail("exec'd"))
    proc, _ = ba.start_model(_serve(tmp_path, "exec sleep 30"), "m")
    ba.stop_llama_server()
    assert proc.poll() is not None


def test_a_free_lease_is_taken_for_the_whole_sweep(ba, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", ["python3", "benchmark_all.py", "--models", "m"])
    monkeypatch.setattr(sys, "argv", ["benchmark_all.py", "--models", "m"])
    calls = []
    ba.ensure_gpu_lease(["--models", "m"],
                        exec_fn=lambda f, argv, env: calls.append((argv, env)))
    ((argv, env),) = calls
    assert argv[:3] == ["bash", ba.GPU_LEASE_SH, "run"]
    i = argv.index("--")
    assert argv[i + 1:] == [sys.executable, os.path.abspath(ba.__file__), "--models", "m"]
    assert env["OPENBEAST_BENCH_UNDER_LEASE"] == "1"
    assert env["OPENBEAST_RUN_DIR"] == str(tmp_path / "run")
    # The re-exec'd sweep that STILL reads FREE refuses instead of looping.
    monkeypatch.setenv("OPENBEAST_BENCH_UNDER_LEASE", "1")
    with pytest.raises(ba.GpuLeaseHeld, match="unleased"):
        ba.ensure_gpu_lease(["--models", "m"], exec_fn=lambda *a: pytest.fail("looped"))


def test_an_unreadable_lease_is_not_free(ba, tmp_path, monkeypatch):
    monkeypatch.setattr(ba, "GPU_LEASE_SH", str(tmp_path / "missing-gpu-lease.sh"))
    with pytest.raises(ba.GpuLeaseHeld, match="unknown is not free"):
        ba.start_model(_serve(tmp_path, "exec sleep 30"), "m")
    assert ba._own_server["proc"] is None


def test_a_worktree_asks_the_main_trees_lease(tmp_path, monkeypatch):
    """The lease file is <main tree>/.run/gpu.lease; a worktree's own copy of
    gpu-lease.sh would read <worktree>/.run and say FREE over a campaign."""
    for mod in ("cache", "run_eval", "benchmark_all"):
        sys.modules.pop(mod, None)
    ba = importlib.import_module("benchmark_all")
    main, wt = tmp_path / "main", tmp_path / "wt"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
    subprocess.run(git + ["init", "-q", str(main)], check=True)
    subprocess.run(git + ["-C", str(main), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True)
    subprocess.run(git + ["-C", str(main), "worktree", "add", "-q", str(wt)], check=True)
    monkeypatch.delenv("OPENBEAST_RUN_DIR", raising=False)
    monkeypatch.setattr(ba, "GPU_LEASE_SH", str(wt / "scripts" / "gpu-lease.sh"))
    assert ba._lease_env()["OPENBEAST_RUN_DIR"] == str(main / ".run")
    monkeypatch.setattr(ba, "GPU_LEASE_SH", str(main / "scripts" / "gpu-lease.sh"))
    assert ba._lease_env()["OPENBEAST_RUN_DIR"] == str(main / ".run")
    # An operator's explicit run dir wins.
    monkeypatch.setenv("OPENBEAST_RUN_DIR", "/elsewhere")
    assert ba._lease_env()["OPENBEAST_RUN_DIR"] == "/elsewhere"


def _exec_argv(ba, orig_argv, argv, monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", orig_argv)
    monkeypatch.setattr(sys, "argv", argv)
    calls = []
    ba.ensure_gpu_lease(argv[1:], exec_fn=lambda f, a, env: calls.append(a))
    (a,) = calls
    return a[a.index("--") + 1:]


def test_the_reexec_keeps_interpreter_flags(ba, monkeypatch):
    """`python3 -u benchmark_all.py > log`: the re-exec must stay unbuffered,
    or a SIGKILL/OOM loses the log's tail (review r2 evals2, major)."""
    me = os.path.abspath(ba.__file__)
    got = _exec_argv(ba, ["python3", "-u", "-X", "utf8", "evals/benchmark_all.py",
                          "--models", "m"],
                     ["evals/benchmark_all.py", "--models", "m"], monkeypatch)
    assert got == [sys.executable, "-u", "-X", "utf8", me, "--models", "m"]
    # Negative control: no flags in, none out.
    got = _exec_argv(ba, ["python3", "evals/benchmark_all.py", "--models", "m"],
                     ["evals/benchmark_all.py", "--models", "m"], monkeypatch)
    assert got == [sys.executable, me, "--models", "m"]
    # `python3 -u -m benchmark_all`: -m is not an option for a script path.
    got = _exec_argv(ba, ["python3", "-u", "-m", "benchmark_all", "--models", "m"],
                     [me, "--models", "m"], monkeypatch)
    assert got == [sys.executable, "-u", me, "--models", "m"]


def test_a_reexecd_sweep_refuses_to_load_once_the_lease_reads_free(ba, tmp_path,
                                                                     monkeypatch):
    """`gpu-lease.sh run` puts the sweep in its own process group, so a
    `kill -9` of the wrapper leaves the sweep alive with the lease FREE. It
    must not keep loading models on a card the lease calls free."""
    marker = tmp_path / "loaded"
    serve = _serve(tmp_path, f"touch {marker}; exec sleep 30")
    monkeypatch.setenv("OPENBEAST_BENCH_UNDER_LEASE", "1")
    with pytest.raises(ba.GpuLeaseHeld, match="unleased"):
        ba.start_model(serve, "m")
    assert not ba.restart_server(serve, "m", health_timeout=1)
    time.sleep(0.3)
    assert not marker.exists(), "a model was loaded with the lease wrapper gone"
    # ...while under a live wrapper (lease ours) it still loads.
    _write_lease(tmp_path / "run", os.getpid())
    proc, _ = ba.start_model(serve, "m")
    ba.stop_llama_server()
    assert proc.poll() is not None
    # Negative control: a plain (not re-exec'd) sweep on a free card loads.
    (tmp_path / "run" / "gpu.lease").unlink()
    monkeypatch.delenv("OPENBEAST_BENCH_UNDER_LEASE")
    proc, _ = ba.start_model(serve, "m")
    ba.stop_llama_server()
    assert proc.poll() is not None


def _fake_git(tmp_path: Path, body: str, monkeypatch) -> None:
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (bindir / "git").write_text("#!/bin/bash\n" + body + "\n")
    (bindir / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_old_git_echoing_an_unknown_flag_cannot_forge_the_run_dir(tmp_path, monkeypatch):
    """git < 2.31 echoes an unknown flag (--path-format=absolute) as output
    and exits 0. That must not become a relative `.run` (read against the
    caller's cwd) or a two-line path."""
    for mod in ("cache", "run_eval", "benchmark_all"):
        sys.modules.pop(mod, None)
    ba = importlib.import_module("benchmark_all")
    tree = tmp_path / "main"
    monkeypatch.setattr(ba, "GPU_LEASE_SH", str(tree / "scripts" / "gpu-lease.sh"))
    monkeypatch.delenv("OPENBEAST_RUN_DIR", raising=False)
    # Old git in the main tree: echoes unknown flags, prints the common dir
    # relative to -C.
    _fake_git(tmp_path, 'for a in "$@"; do [[ $a == --path-format=* ]] && echo "$a"; done\n'
                        'echo .git', monkeypatch)
    assert ba._lease_env()["OPENBEAST_RUN_DIR"] == str(tree / ".run")
    # Garbage (two lines) is refused, not pinned.
    (tmp_path / "fakebin" / "git").write_text("#!/bin/bash\necho junk\necho /x/.git\n")
    assert "OPENBEAST_RUN_DIR" not in ba._lease_env()
