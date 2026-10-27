#!/usr/bin/env python3
"""Review 2026-10-27 regressions for beast-hydra (R3-F2, R3-F4, R3-F6).

Run: python3 -m pytest tests/test_review1027_hydra.py -q
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from test_hydra_proxy import (  # noqa: E402,F401  (fleet is a fixture)
    chat, core, fleet, hydra, pause_probes, post)


# ── R3-F2: a reload that moves a node must re-judge its conformance report ──

def _raw(url: str, tmp: Path) -> dict:
    return {"schema": 1, "hydra": {"audit": str(tmp / "a.jsonl"), "instinct": {"enabled": False}},
            "nodes": {"spark": {"url": url, "engine": "vllm", "slots": 1}},
            "deployments": {"m@spark": {"node": "spark", "upstream": "m", "ctx": 1000,
                                        "conformance": "required"}},
            "routes": {"beast": {"targets": [{"d": "m@spark"}]}}}


def test_reload_moving_a_node_rejudges_conformance(tmp_path):
    env = {"OPENBEAST_EDGE_READ_TIMEOUT": "600"}
    cfgp = tmp_path / "hydra.toml"
    cfgp.write_text(core.to_toml(_raw("http://100.64.0.1:8000", tmp_path)))
    hy = hydra.Hydra(core.load_config(cfgp, env), env=env, run=tmp_path / "run", cfg_path=cfgp)
    rep = tmp_path / "run" / "conformance" / "m@spark" / "latest.json"
    rep.parent.mkdir(parents=True)
    rep.write_text(json.dumps({"url": "http://100.64.0.1:8000", "ok": True, "results": []}))
    hy.load_conformance()
    assert hy.state.conformance["m@spark"][0] == "pass"      # negative control: same node passes

    cfgp.write_text(core.to_toml(_raw("http://100.64.0.2:9000", tmp_path)))
    assert hy.reload()["ok"]
    hy.load_conformance()                                     # report file untouched (same mtime)
    assert hy.state.conformance["m@spark"][0] != "pass", \
        "a report made against the old host:port still admits the moved node"

    # Moving it back makes the same report valid again.
    cfgp.write_text(core.to_toml(_raw("http://100.64.0.1:8000", tmp_path)))
    assert hy.reload()["ok"]
    hy.load_conformance()
    assert hy.state.conformance["m@spark"][0] == "pass"


# ── R3-F4: an engine 429 is passed through when nothing after it ran ──

def test_429_passes_through_when_later_targets_are_skipped(fleet):
    srv, rig, sparks, _ = fleet()
    pause_probes(srv)
    rig.set_fault("http_429", 1)
    real = srv.hy.state.try_admit

    def ta(d, node, now):
        if d == "nvfp4@sparks":
            return None, "breaker HALF_OPEN (trial in flight)"
        return real(d, node, now)
    srv.hy.state.try_admit = ta
    r = post(srv, chat(stream=False))
    assert r.status_code == 429, r.text
    assert r.headers.get("retry-after") == "1"
    att = r.headers["x-hydra-attempts"]
    assert att.startswith("unc@rig:429") and "nvfp4@sparks" in att


def test_429_still_fails_over_when_the_next_target_runs(fleet):
    """Negative control: a 429 with a runnable next target is NOT returned."""
    srv, rig, sparks, _ = fleet()
    pause_probes(srv)
    rig.set_fault("http_429", 1)
    r = post(srv, chat(stream=False))
    assert r.status_code == 200, r.text
    assert r.headers["x-hydra-deployment"] == "nvfp4@sparks"
