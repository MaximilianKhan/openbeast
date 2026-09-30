#!/usr/bin/env python3
"""Lifecycle: effective mode, gate matching, demotion, ceiling
(plan §5.13 test_instinct_lifecycle). Promotion is data, never a toggle."""
from __future__ import annotations

import json
import os
import subprocess

import pytest

import _instinct_helpers as H
from instinct import calibrate as C
from instinct.config import load_config
from instinct.lifecycle import (AutoDemoter, Demotions, LifecycleInputs, effective_mode,
                                gate_record_ok, git_committed)
from instinct.service import Instinct

INLINE = {"contract": "instinct/1", "decision": "router.spawn_intent",
          "inputs": {"user_turn": "what does this function do"}}


def li(**kw):
    base = dict(spec_mode="enforce", gate_ok=True, calib_ok=True, engine_healthy=True,
                probe_fresh=True, probabilistic=True, demoted=None, ceiling="enforce")
    base.update(kw)
    return LifecycleInputs(**base)


def test_all_green_enforces():
    assert effective_mode(li()) == ("enforce", None)


@pytest.mark.parametrize("kw,reason", [
    ({"gate_ok": False}, "no_gate"),
    ({"calib_ok": False}, "uncalibrated"),
    ({"engine_healthy": False}, "conformance_failed"),
    ({"probe_fresh": False}, "conformance_failed"),
    ({"demoted": "operator:x"}, "demoted"),
    ({"probabilistic": False}, "uncalibrated"),          # I6
    ({"ceiling": "shadow"}, "caller_ceiling"),
    ({"spec_mode": "shadow"}, "lifecycle_shadow"),
])
def test_each_cap_drops_to_shadow(kw, reason):
    assert effective_mode(li(**kw)) == ("shadow", reason)


def test_ceiling_off_and_canary():
    assert effective_mode(li(ceiling="off"))[0] == "off"
    assert effective_mode(li(spec_mode="canary")) == ("canary", None)
    assert effective_mode(li(spec_mode="canary", gate_ok=False)) == ("shadow", "no_gate")


def test_gate_record_matching():
    calib = {"_sha256": "c1"}
    good = {"decision_hash": "h", "passed": True, "calib_sha256": "c1"}
    assert gate_record_ok(good, "h", calib)
    assert not gate_record_ok({**good, "decision_hash": "other"}, "h", calib)
    assert not gate_record_ok({**good, "passed": False}, "h", calib)
    assert not gate_record_ok({**good, "calib_sha256": "c0"}, "h", calib)   # recalibrated since
    assert not gate_record_ok(good, "h", None)


def test_auto_demotion_on_fallback_rate(tmp_path):
    d = Demotions(tmp_path / "demoted.json")
    ad = AutoDemoter(d)
    for i in range(100):
        ad.observe("x", failed=(i >= 95), label_mass=None, mass_ref_p50=None)
    assert d.reason("x") is None          # control: exactly 5% is not > 5%
    ad.observe("x", failed=True, label_mass=None, mass_ref_p50=None)
    assert d.reason("x").startswith("auto:fallback_rate")


def test_auto_demotion_on_label_mass_collapse(tmp_path):
    d = Demotions(tmp_path / "demoted.json")
    ad = AutoDemoter(d)
    for _ in range(200):
        ad.observe("y", failed=False, label_mass=0.85, mass_ref_p50=0.9)
    assert d.reason("y") is None          # control: within 0.2 of the reference
    for _ in range(200):
        ad.observe("y", failed=False, label_mass=0.5, mass_ref_p50=0.9)
    assert "label_mass" in d.reason("y")


# --- the full promotion path, through the service ---------------------------------

def _decide(cfgp, req=INLINE, repo=None):
    async def go():
        inst = Instinct(load_config(cfgp, env={}), repo_root=repo or cfgp.parent)
        await inst.start()
        out = await inst.decide(dict(req))
        await inst.aclose()
        return out
    return H.run(go())


def test_gate_passed_and_calibrated_enforces(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path)
    r = _decide(cfgp)
    assert r["answer"]["label"] == "inline" and r["answer"]["calibrated"]
    assert r["mode"] == "enforce" and r["enforce"] is True and r["action"] == "act"


def test_target_enforce_without_gate_is_shadow(tmp_path):
    cfgp, h = H.promote_linear(tmp_path)
    C.gate_path(load_config(cfgp, env={}).records_dir, "router.spawn_intent", h).unlink()
    r = _decide(cfgp)
    assert (r["mode"], r["enforce"], r["fallback"]["reason"]) == ("shadow", False, "no_gate")
    assert r["would"] == {"label": "inline", "action": "act"}


def test_gate_for_a_different_hash_is_shadow(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path, gate_hash="f" * 64)
    r = _decide(cfgp)
    assert r["mode"] == "shadow" and not r["enforce"]


def test_failed_gate_is_shadow(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path, gate_passed=False)
    assert _decide(cfgp)["enforce"] is False


def test_retrained_linear_model_drops_calibration(tmp_path):
    cfgp, h = H.promote_linear(tmp_path)
    cfg = load_config(cfgp, env={})
    mp = C.linear_model_path(cfg.records_dir, "router.spawn_intent", h)
    m = json.loads(mp.read_text())
    m["bias"]["inline"] += 0.001            # "retrained" without recalibrating
    mp.write_text(json.dumps(m))
    r = _decide(cfgp)
    assert r["enforce"] is False and r["answer"]["calibrated"] is False


def test_caller_ceiling_beats_enforce(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path)
    r = _decide(cfgp, {**INLINE, "ceiling": "shadow"})
    assert (r["mode"], r["enforce"], r["fallback"]["reason"]) == (
        "shadow", False, "caller_ceiling")


def test_canary_is_deterministic_on_request_id(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path, mode="canary")
    text = (tmp_path / "decisions" / "router.spawn_intent.toml").read_text()
    (tmp_path / "decisions" / "router.spawn_intent.toml").write_text(
        text.replace("min_margin       = 0.0", "min_margin       = 0.0\ncanary_pct       = 50"))
    seen = {}
    for i in range(12):
        rid = f"req-{i}"
        a = _decide(cfgp, {**INLINE, "request_id": rid})
        b = _decide(cfgp, {**INLINE, "request_id": rid})
        assert a["enforce"] == b["enforce"]
        seen[a["enforce"]] = a
    assert set(seen) == {True, False}
    assert seen[False]["fallback"]["reason"] == "canary_out"


def test_operator_demotion_and_reload(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path)
    cfg = load_config(cfgp, env={})

    async def go():
        inst = Instinct(cfg, repo_root=tmp_path)
        await inst.start()
        a = await inst.decide(dict(INLINE))
        (tmp_path / "ledger").mkdir(exist_ok=True)
        (cfg.state_dir / "demoted.json").write_text(json.dumps(
            {"router.spawn_intent": {"reason": "test"}}))
        b = await inst.decide(dict(INLINE))       # not reloaded yet
        await inst.reload()                        # what SIGHUP does
        c = await inst.decide(dict(INLINE))
        await inst.aclose()
        return a, b, c
    a, b, c = H.run(go())
    assert a["enforce"] and b["enforce"]
    assert c["enforce"] is False and c["fallback"]["reason"] == "demoted"


def test_gate_must_be_committed_when_required(tmp_path):
    cfgp, h = H.promote_linear(tmp_path, service={"require_committed_gate": True})
    # not a git repo -> not committed -> shadow
    assert _decide(cfgp, repo=tmp_path)["enforce"] is False
    # commit it -> enforce
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "add", "records"], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "gate"], check=True, env=env)
    gp = C.gate_path(load_config(cfgp, env={}).records_dir, "router.spawn_intent", h)
    assert git_committed(gp, tmp_path)
    assert _decide(cfgp, repo=tmp_path)["enforce"] is True
    # a hand edit after the commit -> not committed any more
    rec = json.loads(gp.read_text())
    rec["note"] = "hand edited"
    gp.write_text(json.dumps(rec))
    assert not git_committed(gp, tmp_path)
    assert _decide(cfgp, repo=tmp_path)["enforce"] is False
