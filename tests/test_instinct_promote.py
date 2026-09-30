#!/usr/bin/env python3
"""scripts/instinct.sh promote / demote / stats — regressions for the
2026-09-29 double-pass review (ids like A-instinct-4 name the finding): promote
applies the service's own rules and the shadow soak and survives a live LLM
engine; demote refuses an unknown id; auto-demotions are visible. Each test
builds its own case and fails on the code before its fix."""
from __future__ import annotations

import json
import re

import _instinct_helpers as H
from instinct.config import load_config
from instinct.service import Instinct

DID = "router.spawn_intent"


def _spawn_cfg(tmp_path, url, *, mode="shadow", chain='["stub", "rules"]', service=None):
    text = re.sub(r'chain = \[.*?\]', f"chain = {chain}", H.spec_text(DID), count=1)
    text = text.replace('mode             = "shadow"', f'mode             = "{mode}"')
    p = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                       extra_decisions={DID: text}, service=service)
    return load_config(p, env={})


async def _decide(inst, text, **kw):
    return await inst.decide({"contract": "instinct/1", "decision": DID,
                              "inputs": {"user_turn": text}, **kw})



async def _decide(inst, text, **kw):
    return await inst.decide({"contract": "instinct/1", "decision": DID,
                              "inputs": {"user_turn": text}, **kw})


# ─── promote / demote / stats (cli.py) ──────────────────────────────────────────

def _check(checks, prefix):
    hits = [ok for name, ok, _ in checks if name.startswith(prefix)]
    assert len(hits) == 1, [n for n, _, _ in checks]
    return hits[0]


def test_promote_with_a_live_llm_engine_does_not_crash(tmp_path):
    """B-instinct-02: two asyncio.run calls around one httpx client crashed
    'Event loop is closed' for every REACHABLE LLM engine."""
    from instinct.cli import promote_check
    with H.stub_server() as (url, _):
        cfg = _spawn_cfg(tmp_path, url)
        ok, checks = promote_check(cfg, DID, "stub", repo=tmp_path)
    assert ok is False                                  # shadow spec, no records
    assert _check(checks, "engine in chain + label lock") is True
    assert _check(checks, "engine conformance probe passes") is True


def test_promote_refuses_a_gate_for_a_stale_calibration(tmp_path):
    """A-instinct-5: promote said READY for a gate the service rejects (it was
    computed against an older calibration)."""
    from instinct import calibrate as C
    from instinct.cli import promote_check
    cfgp, h = H.promote_linear(tmp_path, mode="enforce")
    cfg = load_config(cfgp, env={})
    gp = C.gate_path(cfg.records_dir, DID, h)
    rec = json.loads(gp.read_text())
    ok, checks = promote_check(cfg, DID, "linear", repo=tmp_path)
    assert _check(checks, "gate record passed, for this hash AND this calibration") is True
    rec["calib_sha256"] = "0" * 64                       # a recalibration since the gate
    C.write_record(gp, rec)
    ok, checks = promote_check(cfg, DID, "linear", repo=tmp_path)
    assert ok is False
    assert _check(checks, "gate record passed, for this hash AND this calibration") is False
    assert _check(checks, "calibration record for this hash") is True


def test_promote_requires_the_shadow_soak(tmp_path):
    """A-instinct-4: gate.shadow {min_decisions, min_days} was parsed and
    never enforced — READY with zero shadow history."""
    from instinct.cli import promote_check
    cfgp, h = H.promote_linear(tmp_path, mode="enforce")
    cfg = load_config(cfgp, env={})
    ok, checks = promote_check(cfg, DID, "linear", repo=tmp_path)
    assert _check(checks, "shadow soak") is False       # 200 over 14 days, have none
    led = tmp_path / "ledger"
    led.mkdir(exist_ok=True)
    t0 = 1_780_000_000.0
    rows = [{"ts": t0 + i * (15 * 86400 / 199), "kind": "decide", "decision": DID,
             "cascade": [{"engine": "linear", "action": "act", "hash": h[:16]}]}
            for i in range(200)]
    other = {"ts": t0, "kind": "decide", "decision": DID,      # another hash: not ours
             "cascade": [{"engine": "linear", "action": "act", "hash": "f" * 16}]}
    (led / "decisions-2026-01-01.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows[:199] + [other]))
    ok, checks = promote_check(cfg, DID, "linear", repo=tmp_path)
    assert _check(checks, "shadow soak") is False       # 199 < 200
    (led / "decisions-2026-01-02.jsonl").write_text(json.dumps(rows[199]) + "\n")
    ok, checks = promote_check(cfg, DID, "linear", repo=tmp_path)
    assert _check(checks, "shadow soak") is True


def _cli(cfgp, *a):
    import os
    import subprocess
    env = {**os.environ, "PYTHONPATH": str(H.REPO / "agents")}
    return subprocess.run(["python3", "-m", "instinct.cli", "--config", str(cfgp), *a],
                          env=env, capture_output=True, text=True, timeout=60)


def test_demote_refuses_an_unknown_decision(tmp_path):
    """B-instinct-07: a typo in an emergency demote must not report success."""
    cfgp, _ = H.promote_linear(tmp_path, mode="shadow")
    r = _cli(cfgp, "demote", "router.spawn_intnet")
    assert r.returncode == 2 and "unknown decision" in r.stderr
    assert not (tmp_path / "ledger" / "demoted.json").exists()
    assert _cli(cfgp, "demote", "router.spawn_intnet", "--force").returncode == 0
    assert _cli(cfgp, "demote", DID).returncode == 0          # control


def test_auto_demotion_is_visible_named_and_undemotable(tmp_path):
    """B-instinct-06: stats shows auto-demotions, a demoted decision's answer
    says 'demoted', and undemote clears the persisted auto record."""
    cfgp, _ = H.promote_linear(tmp_path, mode="enforce")
    cfg = load_config(cfgp, env={})
    inst = Instinct(cfg, repo_root=tmp_path)
    H.run(inst.start())
    inst.autodemoter._demote(DID, "fallback_rate 9/100")
    r = H.run(_decide(inst, "what is 17 times 23"))
    H.run(inst.aclose())
    assert r["enforce"] is False and r["fallback"]["reason"] == "demoted"
    st = json.loads(_cli(cfgp, "stats").stdout)
    assert st["_auto_demotions"][DID]["reason"] == "fallback_rate 9/100"
    assert _cli(cfgp, "undemote", DID).returncode == 0
    st = json.loads(_cli(cfgp, "stats").stdout)
    assert st["_auto_demotions"] == {}
