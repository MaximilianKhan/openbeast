#!/usr/bin/env python3
"""scripts/instinct.sh + scripts/serve-instinct-scorer.sh + the offline CLI.

Hermetic: every service runs from a tmp config on an EPHEMERAL port with a
tmp run dir; only processes this test started are stopped (by pidfile). The
scorer launcher is only driven into its refusal paths — no llama-server is
ever started."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time

import pytest

import _instinct_helpers as H

REPO = H.REPO
INSTINCT_SH = REPO / "scripts" / "instinct.sh"
SCORER_SH = REPO / "scripts" / "serve-instinct-scorer.sh"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def env_for(tmp_path, cfgp, port):
    e = {k: v for k, v in os.environ.items() if not k.startswith("INSTINCT")}
    e.update({"INSTINCT_CONFIG": str(cfgp), "INSTINCT_PORT": str(port),
              "INSTINCT_RUN_DIR": str(tmp_path / "run"), "INSTINCT_STUB_PORT": str(free_port())})
    return e


def scorer_env(tmp_path, **extra):
    """The scorer's refusals and --help must not depend on this machine having
    a weights directory (CI has none): point it at one that does not exist."""
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("INSTINCT") and k not in ("INFERENCE_URL",
                                                       "OPENBEAST_INFERENCE_URL")}
    e["OPENBEAST_WEIGHTS_DIR"] = str(tmp_path / "no-such-weights")
    e["INSTINCT_RUN_DIR"] = str(tmp_path)
    e.update(extra)
    return e


def sh(script, *args, env, timeout=60):
    return subprocess.run(["nice", "-n", "19", "bash", str(script), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_scripts_parse():
    for s in (INSTINCT_SH, SCORER_SH):
        assert subprocess.run(["bash", "-n", str(s)]).returncode == 0


def test_up_status_down_pidfile_hygiene(tmp_path):
    cfgp = H.write_config(tmp_path, {}, decisions=["router.spawn_intent"])
    os.unlink(tmp_path / "instinct.key")          # `up` must mint it
    port = free_port()
    env = env_for(tmp_path, cfgp, port)
    r = sh(INSTINCT_SH, "up", env=env)
    try:
        assert r.returncode == 0, r.stderr + r.stdout
        key = tmp_path / "instinct.key"
        assert oct(os.stat(key).st_mode & 0o777) == "0o600" and len(key.read_text()) >= 32
        pid = int((tmp_path / "run" / "instinct.pid").read_text())
        st = sh(INSTINCT_SH, "status", env=env)
        assert st.returncode == 0, st.stderr
        assert "router.spawn_intent" in st.stdout
        # the key never appears on any argv we spawned
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        assert key.read_text().strip() not in ps
        again = sh(INSTINCT_SH, "up", env=env)
        assert "already running" in again.stdout
    finally:
        d = sh(INSTINCT_SH, "down", env=env)
    assert d.returncode == 0 and "stopped" in d.stdout
    assert not (tmp_path / "run" / "instinct.pid").exists()
    deadline = time.time() + 5                    # a fixed 0.2 s lost to a loaded runner
    with pytest.raises(ProcessLookupError):
        while time.time() < deadline:
            os.kill(pid, 0)
            time.sleep(0.05)
    assert "not running" in sh(INSTINCT_SH, "down", env=env).stdout


def test_up_refuses_an_occupied_port(tmp_path):
    cfgp = H.write_config(tmp_path, {}, decisions=["router.spawn_intent"])
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        env = env_for(tmp_path, cfgp, s.getsockname()[1])
        r = sh(INSTINCT_SH, "up", env=env)
        assert r.returncode != 0 and "already held" in r.stderr
        assert not (tmp_path / "run" / "instinct.pid").exists()
    finally:
        s.close()


def test_up_refuses_a_world_readable_key(tmp_path):
    cfgp = H.write_config(tmp_path, {}, decisions=["router.spawn_intent"])
    os.chmod(tmp_path / "instinct.key", 0o644)
    r = sh(INSTINCT_SH, "up", env=env_for(tmp_path, cfgp, free_port()))
    assert r.returncode != 0 and "0600" in r.stderr


def test_scorer_refuses_occupied_port(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        env = scorer_env(tmp_path, INSTINCT_SCORER_PORT=str(s.getsockname()[1]),
                         INFERENCE_URL="http://127.0.0.1:1")
        r = sh(SCORER_SH, env=env)
        assert r.returncode == 2 and "already held" in r.stderr
    finally:
        s.close()


def test_scorer_refuses_the_primary_url(tmp_path):
    p = free_port()
    env = scorer_env(tmp_path, INSTINCT_SCORER_PORT=str(p),
                     INFERENCE_URL=f"http://localhost:{p}")
    r = sh(SCORER_SH, env=env)
    assert r.returncode == 2 and "primary" in r.stderr


def test_scorer_help_documents_the_cpu_contract(tmp_path):
    r = sh(SCORER_SH, "--help", env=scorer_env(tmp_path))
    assert r.returncode == 0
    assert "-ngl 0" in r.stdout or "CPU only" in r.stdout
    text = SCORER_SH.read_text()
    assert 'CUDA_VISIBLE_DEVICES=""' in text and "--api-key" not in text.replace(
        "--api-key` from", "")


def test_scorer_without_weights_dir_fails_on_the_model_not_earlier(tmp_path):
    """Control for the three refusal tests: with a free port and no weights
    dir, the script gets PAST the refusals and stops at the weights check."""
    env = scorer_env(tmp_path, INSTINCT_SCORER_PORT=str(free_port()),
                     INFERENCE_URL="http://127.0.0.1:1")
    r = sh(SCORER_SH, env=env)
    assert r.returncode != 0 and "no-such-weights" in r.stderr
    assert "already held" not in r.stderr and "primary" not in r.stderr


def test_stats_demote_promote_cli(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path, mode="shadow")
    env = {**os.environ, "PYTHONPATH": str(REPO / "agents")}

    def cli(*a):
        return subprocess.run(["python3", "-m", "instinct.cli", "--config", str(cfgp), *a],
                              env=env, capture_output=True, text=True, timeout=60)
    r = cli("demote", "router.spawn_intent", "--reason", "test")
    assert r.returncode == 0
    dem = tmp_path / "ledger" / "demoted.json"
    assert json.loads(dem.read_text())["router.spawn_intent"]["reason"] == "test"
    assert oct(os.stat(dem).st_mode & 0o777) == "0o600"
    assert cli("undemote", "router.spawn_intent").returncode == 0
    assert json.loads(dem.read_text()) == {}
    # promote only CHECKS: spec is in shadow and the gate is not committed -> not ready
    before = (tmp_path / "decisions" / "router.spawn_intent.toml").read_text()
    r = cli("promote", "router.spawn_intent", "--engine", "linear")
    assert r.returncode == 1 and "FAIL spec mode == enforce" in r.stdout
    assert (tmp_path / "decisions" / "router.spawn_intent.toml").read_text() == before
    r = cli("stats")
    assert r.returncode == 0 and "_demotions" in json.loads(r.stdout)


def test_label_writes_user_text_0600_and_gitignored(tmp_path, monkeypatch):
    import builtins

    from instinct import cli as CLI
    from instinct.config import load_config
    from instinct.ledger import Ledger
    cfgp = H.write_config(tmp_path, {}, decisions=["router.spawn_intent"])
    cfg = load_config(cfgp, env={})
    Ledger(cfg.ledger_dir).write({"ts": time.time(), "kind": "decide", "trace_id": "ins_a",
                                  "decision": "router.spawn_intent",
                                  "input_excerpt": {"user_turn": "private words"},
                                  "confidence": {"margin": 0.1}})
    # spawn/inline would take "s" from [s]kip, so keys are numbered: 2 = inline
    monkeypatch.setattr(builtins, "input", lambda prompt="": "2")
    assert CLI.main(["--config", str(cfgp), "label", "router.spawn_intent"]) == 0
    out = cfg.records_dir / "router.spawn_intent" / "shadow-labelled.jsonl"
    assert "private words" in out.read_text() and '"label": "inline"' in out.read_text()
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q",
                        "evals/decisions/router.spawn_intent/shadow-labelled.jsonl"])
    assert r.returncode == 0
    r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q",
                        "evals/decisions/router.spawn_intent/test.jsonl"])
    assert r.returncode == 1   # control: real splits stay tracked
