#!/usr/bin/env python3
"""beast-island (agents/island.py) — the runtime offline mode.

What is pinned here, and why each one matters during an outage:

  - the state machine: auto islands only after the grace (a blip must not
    flip it), rejoins after REJOIN_OK good probes in a row (one lucky packet
    must not either), and force never rejoins;
  - the launch decision: no route at launch → islanded at once, and the
    state says so (start.sh keys Open WebUI's OFFLINE_MODE off it);
  - staleness: a state file whose heartbeat stopped reads as NOT islanded —
    a dead watcher must never leave web tools refusing on a healthy network;
  - the refusal reaches the model through the REAL tool functions
    (mcp_server.fetch / web_search, which the :3001 tool server serves too)
    without the network call being attempted at all;
  - probes are real TCP: a listening loopback port is "up", a closed one is
    "down";
  - conf.sh resolves ISLAND / ISLAND_GRACE the way the docs say, and
    start.sh accepts --offline.

Run: pytest tests/test_island.py
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agents"))

import island  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Probe:
    """Scripted probe results; records how often it was asked."""
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.results.pop(0)


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path))
    return tmp_path


def _watcher(mode, probe, clock, grace=90, interval=15):
    logs = []
    w = island.Watcher(mode, grace, interval, probe_fn=probe, clock=clock,
                       log=logs.append)
    return w, logs


# ── state machine ───────────────────────────────────────────────────────────

def test_auto_islands_only_after_grace(run_dir, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(island, "_now", clock)
    w, logs = _watcher("auto", Probe(True, False, False, False), clock)
    assert w.init() == "online"
    clock.t += 15; w.tick()                      # first failure: grace starts
    assert w.state == "online"
    clock.t += 60; w.tick()                      # 60s down < 90s grace
    assert w.state == "online" and not island.islanded()
    clock.t += 30; w.tick()                      # 90s down: islanded
    assert w.state == "island" and island.islanded()
    assert "no internet route for 90s" in w.reason
    assert any("ISLAND MODE" in m for m in logs)


def test_blip_resets_the_grace(run_dir, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(island, "_now", clock)
    w, _ = _watcher("auto", Probe(True, False, True, False, False), clock)
    w.init()
    clock.t += 60; w.tick()      # down
    clock.t += 15; w.tick()      # back up: grace resets
    clock.t += 15; w.tick()      # down again (new grace starts here)
    clock.t += 60; w.tick()      # 60s into the NEW outage
    assert w.state == "online"


def test_rejoin_needs_consecutive_good_probes(run_dir, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(island, "_now", clock)
    w, logs = _watcher("auto", Probe(False, True, False, True, True), clock)
    assert w.init() == "island"                  # no route at launch
    clock.t += 15; w.tick()                      # one good probe
    assert w.state == "island"
    clock.t += 15; w.tick()                      # streak broken
    clock.t += 15; w.tick()
    assert w.state == "island"
    clock.t += 15; w.tick()                      # two in a row: rejoin
    assert w.state == "online" and not island.islanded()
    # Launched islanded, so WebUI is still in OFFLINE_MODE — the log says so.
    assert any("Open WebUI was started in offline mode" in m for m in logs)


def test_force_never_rejoins(run_dir, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(island, "_now", clock)
    probe = Probe(True, True, True)
    w, logs = _watcher("force", probe, clock)
    assert w.init() == "island"
    assert probe.calls == 0                      # forced: no launch probe
    for _ in range(3):
        clock.t += 15; w.tick()
    assert w.state == "island" and island.islanded()
    assert any("staying islanded" in m for m in logs)


def test_launch_offline_records_launched_islanded(run_dir, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(island, "_now", clock)
    w, _ = _watcher("auto", Probe(False), clock)
    w.init()
    raw = (run_dir / "island.json").read_text()
    # conf.sh greps for exactly this spelling to keep WebUI's OFFLINE_MODE
    # consistent across healthcheck restarts — pin the format.
    assert '"launched_islanded": true' in raw
    assert json.loads(raw)["state"] == "island"


def test_resume_continues_from_init(run_dir, monkeypatch):
    """init and watch are separate processes; watch must not reset to
    online and wait out a fresh grace."""
    clock = Clock()
    monkeypatch.setattr(island, "_now", clock)
    w1, _ = _watcher("auto", Probe(False), clock)
    w1.init()
    w2, _ = _watcher("auto", Probe(False), clock)
    w2.resume(json.loads((run_dir / "island.json").read_text()))
    clock.t += 15; w2.tick()
    assert w2.state == "island" and w2.launched_islanded


def test_bad_mode_rejected():
    with pytest.raises(ValueError):
        island.Watcher("off", 90, 15, probe_fn=lambda: True)


# ── staleness: a dead watcher must not keep refusing ────────────────────────

def test_stale_state_reads_as_not_islanded(run_dir, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(island, "_now", clock)
    w, _ = _watcher("force", Probe(), clock)
    w.init()
    assert island.islanded()
    clock.t += w.stale_after() + 1               # heartbeat stopped
    assert island.read_state() is None
    assert not island.islanded()
    assert island.web_refusal("fetch") is None


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"state": "island"}',
                                     '{"state": "island", "updated_at": "x", "stale_after": 60}'])
def test_malformed_state_reads_as_not_islanded(run_dir, content):
    (run_dir / "island.json").write_text(content)
    assert not island.islanded()


def test_missing_state_reads_as_not_islanded(run_dir):
    assert island.read_state() is None
    assert island.web_refusal("web_search") is None


# ── the refusal reaches the model through the real tools ────────────────────

def _islanded_now(run_dir):
    st = {"state": "island", "mode": "force", "reason": "forced at launch",
          "since_iso": "2026-10-28T03:00:00", "updated_at": time.time(),
          "stale_after": 90}
    (run_dir / "island.json").write_text(json.dumps(st))


def test_web_tools_refuse_without_touching_the_network(run_dir, monkeypatch):
    import mcp_server

    def boom(*a, **k):
        raise AssertionError("network tool called in island mode")
    monkeypatch.setattr(mcp_server._tools, "fetch", boom)
    monkeypatch.setattr(mcp_server._tools, "web_search", boom)
    _islanded_now(run_dir)
    for out in (mcp_server.fetch("https://example.com"),
                mcp_server.web_search("anything")):
        assert "ISLAND MODE" in out
        assert "do not retry" in out
        assert "Everything local still works" in out


def test_web_tools_pass_through_when_online(run_dir, monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server._tools, "fetch", lambda url, n: f"fetched {url}")
    monkeypatch.setattr(mcp_server._tools, "web_search", lambda q, *a: f"results {q}")
    assert mcp_server.fetch("https://example.com") == "fetched https://example.com"
    assert mcp_server.web_search("q") == "results q"


def test_other_tools_unaffected_in_island_mode(run_dir, monkeypatch, tmp_path):
    import mcp_server
    _islanded_now(run_dir)
    f = tmp_path / "note.txt"
    f.write_text("still here\n")
    assert "still here" in mcp_server.read_file(str(f))


# ── probing is real TCP ─────────────────────────────────────────────────────

def test_probe_up_on_a_listening_port():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0)); srv.listen(1)
    try:
        port = srv.getsockname()[1]
        assert island.probe([("127.0.0.1", port)], timeout=1)
    finally:
        srv.close()


def test_probe_down_on_a_closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                     # nothing listens there now
    assert not island.probe([("127.0.0.1", port)], timeout=1)


def test_probe_any_target_suffices():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0)); srv.listen(1)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); dead = s.getsockname()[1]; s.close()
    try:
        assert island.probe([("127.0.0.1", dead), ("127.0.0.1", srv.getsockname()[1])], timeout=1)
    finally:
        srv.close()


@pytest.mark.parametrize("spec,want", [
    ("1.1.1.1:443 9.9.9.9:53", [("1.1.1.1", 443), ("9.9.9.9", 53)]),
    ("1.1.1.1:443,8.8.8.8:53", [("1.1.1.1", 443), ("8.8.8.8", 53)]),
    ("[2606:4700::1111]:443", [("2606:4700::1111", 443)]),
    ("junk 1.1.1.1:99999 1.1.1.1:443", [("1.1.1.1", 443)]),
])
def test_parse_probes(spec, want):
    assert island.parse_probes(spec) == want


def test_parse_probes_fallback():
    assert island.parse_probes("junk") == island.parse_probes(island.DEFAULT_PROBES)
    assert island.parse_probes("junk", fallback=False) == []


# ── CLI exit codes (start.sh and healthcheck.sh branch on them) ─────────────

def _cli(run_dir, *args):
    env = dict(os.environ, OPENBEAST_RUN_DIR=str(run_dir))
    return subprocess.run([sys.executable, str(REPO / "agents" / "island.py"), *args],
                          capture_output=True, text=True, env=env, timeout=30)


def test_cli_status_and_clear(run_dir):
    r = _cli(run_dir, "status")
    assert r.returncode == 0 and "not watching" in r.stdout
    _islanded_now(run_dir)
    r = _cli(run_dir, "status")
    assert r.returncode == 3 and r.stdout.startswith("ISLAND MODE")
    assert _cli(run_dir, "clear").returncode == 0
    assert not (run_dir / "island.json").exists()


def test_cli_init_force_and_auto_offline(run_dir):
    r = _cli(run_dir, "init", "--mode", "force")
    assert r.returncode == 3
    # A closed loopback port as the only probe = "no route" deterministically.
    s = socket.socket(); s.bind(("127.0.0.1", 0)); dead = s.getsockname()[1]; s.close()
    r = _cli(run_dir, "init", "--mode", "auto", "--probes", f"127.0.0.1:{dead}", "--timeout", "1")
    assert r.returncode == 3
    assert json.loads((run_dir / "island.json").read_text())["reason"] == "no internet route at launch"


# ── conf.sh and start.sh wiring ─────────────────────────────────────────────

def _conf(env_extra, expr):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENBEAST_")}
    env.update(REPO_DIR=str(REPO), OB_CONF_READONLY="1", OB_CONF_LINTED="1",
               OPENBEAST_RUN_DIR="/nonexistent-island-test")
    env.update(env_extra)
    r = subprocess.run(["bash", "-c", f'source scripts/lib/conf.sh >/dev/null; {expr}'],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.mark.parametrize("raw,want", [
    ("", "auto"), ("auto", "auto"), ("force", "force"), ("true", "force"),
    ("ON", "force"), ("off", "off"), ("0", "off"), ("off  # comment", "off"),
    ("bogus", "auto"),
])
def test_conf_island_mode(raw, want):
    assert _conf({"OPENBEAST_ISLAND": raw} if raw else {}, 'echo "$ISLAND"') == want


def test_conf_island_grace_and_webui_offline():
    assert _conf({}, 'echo "$ISLAND_GRACE $OPENBEAST_WEBUI_OFFLINE"') == "90 false"
    assert _conf({"OPENBEAST_ISLAND_GRACE": "30"}, 'echo "$ISLAND_GRACE"') == "30"
    assert _conf({"OPENBEAST_ISLAND_GRACE": "soon"}, 'echo "$ISLAND_GRACE"') == "90"
    assert _conf({"OPENBEAST_ISLAND": "force"}, 'echo "$OPENBEAST_WEBUI_OFFLINE"') == "true"


def test_conf_webui_offline_follows_the_running_stack(tmp_path):
    """healthcheck.sh --restart re-runs compose; it must keep the WebUI mode
    the stack launched with, which it learns from the state file."""
    (tmp_path / "island.json").write_text('{\n "launched_islanded": true\n}\n')
    env = {"OPENBEAST_RUN_DIR": str(tmp_path)}
    out = _conf(env, 'echo "$OPENBEAST_WEBUI_OFFLINE"')
    assert out == "true"


def test_start_sh_accepts_offline():
    r = subprocess.run(["bash", str(REPO / "start.sh"), "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "--offline" in r.stdout
    src = (REPO / "start.sh").read_text()
    assert "--offline|offline) export OPENBEAST_ISLAND=force" in src


def test_compose_wires_webui_offline_mode():
    assert "OFFLINE_MODE=${OPENBEAST_WEBUI_OFFLINE:-false}" in (REPO / "docker-compose.yml").read_text()


def test_island_files_are_outside_the_eval_era():
    """beast-island must not roll the eval era: none of its code may live in
    the files evals/cache.py hashes."""
    sys.path.insert(0, str(REPO / "evals"))
    import cache
    hashed = {p.name for p in cache.CONTEXT_FILES}
    assert "island.py" not in hashed and "mcp_server.py" not in hashed
    assert "island" not in (REPO / "agents" / "tools.py").read_text()
