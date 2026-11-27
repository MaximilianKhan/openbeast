#!/usr/bin/env python3
"""The 27B decision engine (plan revision 2026-09-30): the rig's own primary
as a SUBSTITUTE engine for router.spawn_intent, the primary_use rules that
keep it honest, the /slots busy skip and LLAMA_API_KEY via key_env.
Hermetic: the stub scorer on an ephemeral port plays the primary
(INFERENCE_URL points at it) — nothing touches :8080.
The Open-Jev-27B head adapter is tested in test_instinct_openjev.py."""
from __future__ import annotations

import json
import os
import time

import pytest

import _instinct_helpers as H
from instinct.config import load_config
from instinct.engines import build_engine
from instinct.service import Instinct
from instinct.spec import SpecError, load_spec, parse_spec

DID = "router.spawn_intent"
GOOD = H.spec_text(DID)


def _spec_with(**policy_lines) -> str:
    text = GOOD.replace('primary_use      = "substitute"', '').replace(
        'substitutes      = "agents/router.py:_classify"', '')
    extra = "".join(f"{k} = {v}\n" for k, v in policy_lines.items())
    return text.replace('deadline_ms      = 600\n', 'deadline_ms      = 600\n' + extra)


def _parse(text: str):
    import tomllib
    return parse_spec(tomllib.loads(text), expect_id=DID)


# ─── spec: primary_use ─────────────────────────────────────────────────────────

def test_primary_use_defaults_to_none_and_async_only_is_an_alias():
    assert _parse(_spec_with()).policy.primary_use == "none"
    s = _parse(_spec_with(async_only="true"))
    assert s.policy.primary_use == "async" and s.policy.async_only is True


@pytest.mark.parametrize("lines,needle", [
    ({"primary_use": '"always"'}, "primary_use must be one of"),
    ({"primary_use": '"substitute"'}, "needs policy.substitutes"),
    ({"primary_use": '"async"', "substitutes": '"agents/router.py:_classify"'},
     "only meaningful with primary_use"),
    ({"primary_use": '"substitute"', "async_only": "true",
      "substitutes": '"x.py:f"'}, "contradicts"),
    ({"primary_use": '"substitute"', "substitutes": "3"}, "must be a string"),
])
def test_primary_use_is_validated(lines, needle):
    with pytest.raises(SpecError, match=needle):
        _parse(_spec_with(**lines))


def test_shipped_spawn_intent_substitutes_the_classify():
    s = load_spec(H.DECISIONS / f"{DID}.toml")
    assert s.policy.primary_use == "substitute" and s.chain[0] == "rig-27b"
    assert s.chain.index("rig-27b") < s.chain.index("rig-cpu")   # the 0.6B is fallback


# ─── config: key_env, busy_skip, mis_delimiter ─────────────────────────────────

def _cfg(tmp_path, engines, env=None):
    return load_config(H.write_config(tmp_path, engines), env=env or {})


def test_key_env_rules(tmp_path):
    env = {"INFERENCE_URL": "http://127.0.0.1:59999"}
    ok = H.llama_binding("http://127.0.0.1:59999", allow_primary=True, key_env="LLAMA_API_KEY")
    cfg = _cfg(tmp_path, {"p": ok}, env)
    assert "p" in cfg.engines                                    # control
    for extra, why in (({"key_env": "HOME"}, "key_env must be one of"),
                       ({"key_file": "k"}, "not both")):
        cfg = _cfg(tmp_path, {"p": {**ok, **extra}}, env)
        assert why in cfg.engine_errors.get("p", ""), extra
    cfg = _cfg(tmp_path, {"r": H.llama_binding("http://10.0.0.5:9000", key_env="LLAMA_API_KEY")})
    assert "loopback-only" in cfg.engine_errors["r"]             # the stack key stays home


def test_busy_skip_only_on_a_primary_binding(tmp_path):
    cfg = _cfg(tmp_path, {"x": H.llama_binding("http://127.0.0.1:1", busy_skip=True)})
    assert "busy_skip is only for a primary" in cfg.engine_errors["x"]
    cfg = _cfg(tmp_path, {"x": H.llama_binding("http://127.0.0.1:1", busy_skip="yes")})
    assert "bool" in cfg.engine_errors["x"]


def test_mis_needs_a_delimiter(tmp_path):
    """A-instinct-9: exec=mis with no delimiter could not escape it in item
    text (plan F4), so the binding is refused."""
    b = H.sglang_binding("http://127.0.0.1:30010")
    b.pop("mis_delimiter")
    cfg = _cfg(tmp_path, {"s": b})
    assert "mis_delimiter" in cfg.engine_errors["s"]
    cfg = _cfg(tmp_path, {"s": H.sglang_binding("http://127.0.0.1:30010")})
    assert "s" in cfg.engines                                    # control


# ─── the service: runtime enforcement + busy skip ──────────────────────────────

def _primary_cfg(tmp_path, url, *, use="substitute", chain='["prim", "rules"]',
                 mode="shadow", **bkw):
    text = GOOD.replace('chain = ["rig-27b", "rig-cpu", "linear", "rules"]', f"chain = {chain}")
    text = text.replace('mode             = "shadow"', f'mode             = "{mode}"')
    if use != "substitute":
        text = text.replace('primary_use      = "substitute"', f'primary_use      = "{use}"')
        text = text.replace('substitutes      = "agents/router.py:_classify"', '')
    b = H.llama_binding(url, allow_primary=True, busy_skip=True, **bkw)
    p = H.write_config(tmp_path, {"prim": b}, extra_decisions={DID: text})
    return load_config(p, env={"INFERENCE_URL": url})


async def _decide(inst, text, **kw):
    return await inst.decide({"contract": "instinct/1", "decision": DID,
                              "inputs": {"user_turn": text}, **kw})


def test_substitute_primary_only_scores_hinted_turns(tmp_path):
    """The primary may only answer a request whose caller would run the
    substituted classify (baseline 'hint'); anything else skips it."""
    async def body(url):
        inst = Instinct(_primary_cfg(tmp_path, url), repo_root=tmp_path)
        await inst.start()
        assert inst.chains[DID] == ["prim", "rules"]
        hint = await _decide(inst, "spawn a background agent to port it", baseline="hint")
        nohint = await _decide(inst, "what is 17 times 23", baseline="nohint")
        none = await _decide(inst, "what is 17 times 23")
        await inst.aclose()
        return hint, nohint, none
    with H.stub_server() as (url, _):
        hint, nohint, none = H.run(body(url))
    assert hint["cascade"][0]["engine"] == "prim" and "probabilities" in hint["cascade"][0]
    for r in (nohint, none):
        assert r["cascade"][0] == {"engine": "prim", "action": "skipped",
                                   "reason": "primary_not_substitute", "ms": 0.0}


def test_async_primary_is_skipped_when_the_caller_can_act(tmp_path):
    """primary_use = async: only nobody-awaits calls (ceiling shadow) reach it."""
    async def body(url):
        inst = Instinct(_primary_cfg(tmp_path, url, use="async"), repo_root=tmp_path)
        await inst.start()
        acting = await _decide(inst, "what is 17 times 23", ceiling="enforce")
        shadow = await _decide(inst, "what is 17 times 23", ceiling="shadow")
        await inst.aclose()
        return acting, shadow
    with H.stub_server() as (url, _):
        acting, shadow = H.run(body(url))
    assert acting["cascade"][0]["reason"] == "primary_async_only"
    assert shadow["cascade"][0]["engine"] == "prim" and "label" in shadow["cascade"][0]


def test_busy_primary_falls_through_fast_without_polluting_p95(tmp_path):
    """A slot serving a user: the primary is skipped in milliseconds (never
    queued behind the turn), no latency sample is recorded, the next engine
    answers, and it is not counted as an engine failure (auto-demotion)."""
    async def body(url, stub):
        cfg = _primary_cfg(tmp_path, url, chain='["prim", "stub", "rules"]')
        cfg.engines["stub"] = cfg.engines["prim"].__class__(
            name="stub", adapter="llamacpp_logprobs", url=url, model="stub-lexicon",
            model_sha256="stub", n_probs=20, timeout_ms=1500)
        inst = Instinct(cfg, repo_root=tmp_path)
        await inst.start()
        n_before = len(inst.engines["prim"].latencies)
        stub.faults["busy"] = True
        r = await _decide(inst, "spawn a background agent to port it", baseline="hint")
        n_after = len(inst.engines["prim"].latencies)
        await inst.aclose()
        return r, n_before, n_after, inst
    with H.stub_server() as (url, stub):
        r, n_before, n_after, inst = H.run(body(url, stub))
    first = r["cascade"][0]
    assert first["engine"] == "prim" and first["action"] == "skipped"
    assert first["reason"] == "engine_busy" and first["ms"] < 50
    assert n_after == n_before                                   # no latency sample
    assert r["cascade"][1]["engine"] == "stub"                   # the fallback answered
    assert list(inst.autodemoter.outcomes[DID]) == [False]       # not an engine fault


# ─── R-instinct-1/2: the shadow cost and who a no-act row is credited to ───────

def _two_tier(tmp_path, purl, curl, chain='["prim", "cpu", "rules"]', mode="shadow"):
    text = GOOD.replace('chain = ["rig-27b", "rig-cpu", "linear", "rules"]', f"chain = {chain}")
    text = text.replace('mode             = "shadow"', f'mode             = "{mode}"')
    b = H.llama_binding(purl, allow_primary=True, busy_skip=True)
    c = H.llama_binding(curl)
    p = H.write_config(tmp_path, {"prim": b, "cpu": c}, extra_decisions={DID: text})
    return load_config(p, env={"INFERENCE_URL": purl})


def _ledger_rows(tmp_path):
    return [json.loads(line) for p in (tmp_path / "ledger").glob("*.jsonl")
            for line in p.read_text().splitlines() if line.strip()]


def test_shadow_router_waits_for_the_primary_only(tmp_path):
    """R-instinct-1: a shadow decide from the router (return_after=primary)
    answers once the walk has left the primary; the slow fallback behind it
    is still measured, in the background, under the same trace_id. Before
    the fix the router waited ~600 ms for the whole chain on every hinted
    turn."""
    log = tmp_path / "prim.jsonl"

    async def body(purl, curl):
        inst = Instinct(_two_tier(tmp_path, purl, curl), repo_root=tmp_path)
        await inst.start()
        n0 = sum(1 for c in H.read_calls(log) if c.get("path") == "/completion")
        t = time.perf_counter()
        r = await _decide(inst, "spawn a background agent to port the tests",
                          ceiling="shadow", baseline="hint", deadline_ms=600,
                          return_after="primary")
        dt = (time.perf_counter() - t) * 1000
        n1 = sum(1 for c in H.read_calls(log) if c.get("path") == "/completion")
        pending = len(inst._bg)
        await inst.drain()
        await inst.aclose()
        return r, dt, n1 - n0, pending
    with H.stub_server(call_log=str(log)) as (purl, _), \
            H.stub_server({"slow": "400"}) as (curl, _):
        r, dt, prim_calls, pending = H.run(body(purl, curl))
    assert dt < 300, f"router waited {dt:.0f} ms for the fallback"
    assert r["partial"] is True and r["enforce"] is False
    assert r["fallback"]["reason"] == "shadow_pending"
    assert [c["engine"] for c in r["cascade"]] == ["prim"]
    assert r["engine"]["id"] == "prim"
    assert prim_calls == 1 and pending == 1
    rows = [x for x in _ledger_rows(tmp_path) if x.get("kind") == "decide"]
    assert [x["trace_id"] for x in rows] == [r["trace_id"]]          # one row, full walk
    assert [c["engine"] for c in rows[0]["cascade"]][:2] == ["prim", "cpu"]
    assert rows[0]["engine"]["id"] == "prim"


def test_background_walk_gets_its_own_budget(tmp_path):
    """Review finding (beast-instinct): the background half of a shadow walk
    kept the caller's deadline, so after the 27B used it (here: timed out)
    the 0.6B was skipped as `deadline` and never measured on that row.
    Nobody waits on it any more: it runs on its own timeout_ms."""
    text = GOOD.replace('chain = ["rig-27b", "rig-cpu", "linear", "rules"]',
                        'chain = ["prim", "cpu", "rules"]')
    async def body(purl, pstub, curl):
        p = H.write_config(tmp_path, {"prim": H.llama_binding(purl, allow_primary=True),
                                      "cpu": H.llama_binding(curl)},
                           extra_decisions={DID: text})
        inst = Instinct(load_config(p, env={"INFERENCE_URL": purl}), repo_root=tmp_path)
        await inst.reload()
        for n in ("prim", "cpu"):
            inst.states[n].healthy = True
            inst.states[n].last_probe = inst.clock()
            inst.engines[n].latencies.extend([10.0] * 25)
        pstub.faults["slow"] = 400          # tokenize + completion > the 600 ms deadline
        r = await _decide(inst, "spawn a background agent to port the tests",
                          ceiling="shadow", baseline="hint", deadline_ms=600,
                          return_after="primary")
        await inst.drain()
        await inst.aclose()
        return r
    with H.stub_server() as (purl, pstub), H.stub_server() as (curl, _):
        r = H.run(body(purl, pstub, curl))
    assert r["partial"] is True
    rows = [x for x in _ledger_rows(tmp_path) if x.get("kind") == "decide"]
    assert len(rows) == 1
    cas = {c["engine"]: c for c in rows[0]["cascade"]}
    assert cas["prim"]["reason"] == "engine_timeout"
    assert cas["cpu"]["action"] not in ("skipped", "fallback"), cas["cpu"]
    assert "label" in cas["cpu"]


def test_return_after_is_ignored_when_the_target_can_act(tmp_path):
    """A caller that may act on the verdict always gets the whole answer."""
    async def body(purl, curl):
        inst = Instinct(_two_tier(tmp_path, purl, curl, mode="enforce"),
                        repo_root=tmp_path)
        await inst.start()
        assert inst.specs[DID].policy.mode == "enforce"
        from instinct.engines import ScoreRes, ScoreRow

        async def prim_fails(req):
            # no usable row from the 27B (mass off the labels), so the walk
            # must go on to the fallback — synchronously, not "partial"
            # (a 27B VERDICT would end the walk at prim by itself)
            return ScoreRes(rows=[ScoreRow(q={"spawn": 0.9, "inline": 0.1},
                                           label_mass=0.01)], engine_ms=5)
        inst.engines["prim"].score = prim_fails
        r = await _decide(inst, "spawn a background agent to port the tests",
                          ceiling="enforce", baseline="hint", return_after="primary")
        await inst.aclose()
        return r
    with H.stub_server() as (purl, _), H.stub_server() as (curl, _):
        r = H.run(body(purl, curl))
    assert "partial" not in r
    assert "cpu" in [c["engine"] for c in r["cascade"]]


def test_shadow_without_return_after_still_walks_everything(tmp_path):
    async def body(purl, curl):
        inst = Instinct(_two_tier(tmp_path, purl, curl), repo_root=tmp_path)
        await inst.start()
        r = await _decide(inst, "spawn a background agent to port the tests",
                          ceiling="shadow", baseline="hint")
        await inst.aclose()
        return r
    with H.stub_server() as (purl, _), H.stub_server() as (curl, _):
        r = H.run(body(purl, curl))
    assert "partial" not in r
    assert [c["engine"] for c in r["cascade"]][:2] == ["prim", "cpu"]


def test_no_act_row_is_credited_to_the_highest_tier(tmp_path):
    """R-instinct-2: nobody acts (the 27B is uncalibrated, so it abstains):
    the row's engine/label/hash is the 27B's — the first probabilistic
    engine in chain order — never the 0.6B fallback or linear behind it."""
    async def body(purl, curl):
        inst = Instinct(_two_tier(tmp_path, purl, curl), repo_root=tmp_path)
        await inst.start()
        r = await _decide(inst, "spawn a background agent to port the tests",
                          ceiling="shadow", baseline="hint")
        await inst.aclose()
        return r
    with H.stub_server() as (purl, _), H.stub_server() as (curl, _):
        r = H.run(body(purl, curl))
    acted = [c for c in r["cascade"] if c["action"] == "act"]
    assert acted == []
    assert r["engine"]["id"] == "prim"
    assert r["answer"]["label"] == r["cascade"][0]["label"]


def _promote_both(inst):
    """What a promotion of BOTH tiers yields: gate + calibration records."""
    for n in ("prim", "cpu"):
        inst.calib[(DID, n)] = {"T": 1.0, "thresholds": {}}
        inst.gates[(DID, n)] = True


def test_a_27b_non_act_is_never_overridden_by_the_fallback(tmp_path):
    """Review finding (beast-instinct): with both tiers promoted, a 27B that
    SCORED the row `spawn` used to fall through to the 0.6B, whose
    confident `inline` act was then enforced — the 20x-cost skip of a true
    spawn, and the router waited on the CPU scorer every such turn. The
    0.6B answers only when the 27B is skipped; a 27B verdict is final."""
    from instinct.engines import ScoreRes, ScoreRow
    cpu_calls = []

    async def body(purl, curl):
        inst = Instinct(_two_tier(tmp_path, purl, curl, mode="enforce"), repo_root=tmp_path)
        await inst.start()
        _promote_both(inst)

        async def cpu_score(req):
            cpu_calls.append(1)
            return ScoreRes(rows=[ScoreRow(q={"spawn": 0.02, "inline": 0.97},
                                           label_mass=0.99)], engine_ms=5)
        inst.engines["cpu"].score = cpu_score
        assert inst.lifecycle(DID, "prim", "enforce")[0] == "enforce"
        assert inst.lifecycle(DID, "cpu", "enforce")[0] == "enforce"
        r = await _decide(inst, "Spawn a background agent to port the whole utils/ module "
                          "to Go and report back when it is done.",
                          ceiling="enforce", baseline="hint", request_id="r1")
        # control: the primary skipped (no hint) -> the 0.6B does answer
        rc = await _decide(inst, "what is 17 times 23", ceiling="enforce",
                           baseline="nohint", request_id="r2")
        await inst.aclose()
        return r, rc
    with H.stub_server() as (purl, _), H.stub_server() as (curl, _):
        r, rc = H.run(body(purl, curl))
    assert r["cascade"][0]["engine"] == "prim" and r["cascade"][0]["label"] == "spawn"
    assert r["enforce"] is False and r["engine"]["id"] == "prim"
    assert r["answer"]["label"] == "spawn"
    assert [c["engine"] for c in r["cascade"]] == ["prim"]     # no wait on the CPU
    assert rc["cascade"][0]["reason"] == "primary_not_substitute"
    assert rc["engine"]["id"] == "cpu" and rc["enforce"] is True
    assert len(cpu_calls) == 1


@pytest.mark.parametrize("why", ["stale_probe", "no_gate", "no_calib"])
def test_a_27b_spawn_is_final_even_when_its_own_lifecycle_is_shadow(tmp_path, why):
    """Verifier finding (beast-instinct, on d89199d): the 27B's non-act was
    only terminal while ITS lifecycle reached the target. With a stale probe
    (busy-slot deferrals), or a gate/calibration record that no longer
    matches its hash (model swapped), the 27B still scored the row `spawn`
    at p=0.99 — and the 0.6B's `inline` act was enforced over it. A scored
    non-act from a full-model tier is terminal whatever its own lifecycle:
    that can only REDUCE enforcement (the fail-safe direction)."""
    from instinct.engines import ScoreRes, ScoreRow
    cpu_calls = []

    async def body(purl, curl):
        inst = Instinct(_two_tier(tmp_path, purl, curl, mode="enforce"), repo_root=tmp_path)
        await inst.start()
        _promote_both(inst)

        async def prim_score(req):
            return ScoreRes(rows=[ScoreRow(q={"spawn": 0.99, "inline": 0.01},
                                           label_mass=0.99)], engine_ms=5)

        async def cpu_score(req):
            cpu_calls.append(1)
            return ScoreRes(rows=[ScoreRow(q={"spawn": 0.02, "inline": 0.97},
                                           label_mass=0.99)], engine_ms=5)
        inst.engines["prim"].score = prim_score
        inst.engines["cpu"].score = cpu_score
        if why == "stale_probe":
            inst.states["prim"].last_probe = inst.clock() - 10**6
        elif why == "no_gate":
            inst.gates.pop((DID, "prim"))
        else:
            inst.calib.pop((DID, "prim"))
        assert inst.lifecycle(DID, "prim", "enforce")[0] == "shadow"
        assert inst.lifecycle(DID, "cpu", "enforce")[0] == "enforce"
        r = await _decide(inst, "Spawn a background agent to port utils to Go.",
                          ceiling="enforce", baseline="hint", request_id="r1")
        await inst.aclose()
        return r
    with H.stub_server() as (purl, _), H.stub_server() as (curl, _):
        r = H.run(body(purl, curl))
    assert [c["engine"] for c in r["cascade"]] == ["prim"], r["cascade"]
    assert r["enforce"] is False and r["engine"]["id"] == "prim"
    assert r["answer"]["label"] == "spawn"
    assert cpu_calls == []


def test_a_27b_that_could_not_judge_the_row_falls_to_the_fallback(tmp_path):
    """The other side of the rule: a 27B abstain that means the tier FAILED
    on this row (its probability mass left the label set) is not a verdict,
    so the 0.6B still answers."""
    from instinct.engines import ScoreRes, ScoreRow

    async def body(purl, curl):
        inst = Instinct(_two_tier(tmp_path, purl, curl, mode="enforce"), repo_root=tmp_path)
        await inst.start()
        _promote_both(inst)

        async def prim_score(req):
            return ScoreRes(rows=[ScoreRow(q={"spawn": 0.99, "inline": 0.01},
                                           label_mass=0.01)], engine_ms=5)

        async def cpu_score(req):
            return ScoreRes(rows=[ScoreRow(q={"spawn": 0.02, "inline": 0.97},
                                           label_mass=0.99)], engine_ms=5)
        inst.engines["prim"].score = prim_score
        inst.engines["cpu"].score = cpu_score
        r = await _decide(inst, "what is 17 times 23", ceiling="enforce",
                          baseline="hint", request_id="r1")
        await inst.aclose()
        return r
    with H.stub_server() as (purl, _), H.stub_server() as (curl, _):
        r = H.run(body(purl, curl))
    assert [c["engine"] for c in r["cascade"]][:2] == ["prim", "cpu"]
    assert r["cascade"][0]["label_mass"] == 0.01 and r["cascade"][1]["action"] == "act"
    assert r["engine"]["id"] == "cpu" and r["enforce"] is True


def test_shipped_chain_credits_rig_27b_when_nobody_acts():
    """The same rule against the SHIPPED chain order."""
    from instinct.service import Instinct as _I
    spec = load_spec(H.DECISIONS / f"{DID}.toml")
    assert spec.chain[0] == "rig-27b"

    class _E:
        def __init__(self, probs):
            self.caps = type("C", (), {"probs": probs})()

    class _O:
        def __init__(self, engine):
            self.engine, self.action = engine, "abstain"
    fake = _I.__new__(_I)
    fake.engines = {n: _E(n != "rules") for n in spec.chain}
    picked = _I._pick_final(fake, [_O(n) for n in spec.chain])
    assert picked.engine == "rig-27b"


def test_probe_is_deferred_while_the_primary_is_busy(tmp_path):
    async def body(url, stub):
        inst = Instinct(_primary_cfg(tmp_path, url), repo_root=tmp_path)
        await inst.start()
        st = inst.states["prim"]
        before = (st.healthy, st.last_probe)
        stub.faults["busy"] = True
        await inst.probe_all()
        after = (st.healthy, st.last_probe)
        await inst.aclose()
        return before, after
    with H.stub_server() as (url, stub):
        before, after = H.run(body(url, stub))
    assert before[0] is True and after == before                # untouched, not failed


def test_a_deferred_probe_retries_soon_not_a_full_interval_later(tmp_path):
    """Agents keeping the -np 1 slot busy must not leave the 27B unprobed
    for probe_interval_s (300 s) per miss."""
    from instinct.service import PROBE_BUSY_RETRY_S

    async def body(url, stub):
        inst = Instinct(_primary_cfg(tmp_path, url), repo_root=tmp_path)
        await inst.start()
        idle = inst.next_probe_delay()
        stub.faults["busy"] = True
        await inst.probe_all()
        busy = inst.next_probe_delay()
        stub.faults["busy"] = False
        await inst.probe_all()
        again = inst.next_probe_delay()
        await inst.aclose()
        return idle, busy, again
    with H.stub_server() as (url, stub):
        idle, busy, again = H.run(body(url, stub))
    assert idle == 300 and again == 300
    assert busy == PROBE_BUSY_RETRY_S < 300


def test_a_busy_primary_retries_only_itself(tmp_path):
    """Review finding (beast-instinct): a deferred primary probe shortened the
    WHOLE probe cadence to 15 s, so the CPU scorer's full conformance probe
    (~14 completions) re-ran every 15 s exactly while it served decisions.
    The short retry now re-probes the deferred engine only."""
    clog, plog = tmp_path / "cpu.jsonl", tmp_path / "prim.jsonl"

    def completions(log):
        return sum(1 for c in H.read_calls(log) if c.get("path") == "/completion")

    async def body(purl, pstub, curl):
        inst = Instinct(_two_tier(tmp_path, purl, curl), repo_root=tmp_path)
        await inst.start()
        pstub.faults["busy"] = True
        await inst.probe_all(due_only=True)        # nothing due yet: no probe at all
        n_cpu = completions(clog)
        await inst.probe_all()                     # full: prim deferred, cpu probed
        assert inst.probe_deferred and completions(clog) > n_cpu
        n_cpu = completions(clog)
        pstub.faults["busy"] = False
        n_prim = completions(plog)
        await inst.probe_all(due_only=True)        # the 15 s retry
        retried = completions(plog) - n_prim
        cpu_again = completions(clog) - n_cpu
        await inst.aclose()
        return retried, cpu_again, inst.probe_deferred
    with H.stub_server(call_log=str(plog)) as (purl, pstub), \
            H.stub_server(call_log=str(clog)) as (curl, _):
        retried, cpu_again, deferred = H.run(body(purl, pstub, curl))
    assert retried > 0 and not deferred           # the primary was re-probed
    assert cpu_again == 0                         # the CPU scorer was not


def test_reload_waits_for_an_in_flight_probe(tmp_path):
    """Review finding (beast-instinct): a SIGHUP reload ran concurrently with
    the periodic probe_all, whose re-attach then replaced the new registry's
    locks with the old one's. They are serialized now."""
    import asyncio

    async def body(url):
        inst = Instinct(_primary_cfg(tmp_path, url), repo_root=tmp_path)
        await inst.start()
        eng = inst.engines["prim"]
        gate = asyncio.Event()
        real = eng.probe

        async def slow_probe(specs):
            await gate.wait()
            return await real(specs)
        eng.probe = slow_probe
        p = asyncio.create_task(inst.probe_all())
        await asyncio.sleep(0.05)
        r = asyncio.create_task(inst.reload())
        await asyncio.sleep(0.2)
        reload_ran_during_probe = r.done()
        gate.set()
        await asyncio.gather(p, r)
        await inst.aclose()
        return reload_ran_during_probe
    with H.stub_server() as (url, _):
        assert H.run(body(url)) is False


def test_probe_repairs_a_missing_lock(tmp_path):
    """A decision with NO lock entry (added by a reload whose map was lost)
    is re-attached by the next probe, not left engine_unavailable forever."""
    async def body(url):
        inst = Instinct(_primary_cfg(tmp_path, url), repo_root=tmp_path)
        await inst.start()
        inst.states["prim"].locks.pop(DID)
        await inst.probe_all()
        lk = inst.states["prim"].locks.get(DID)
        await inst.aclose()
        return lk
    with H.stub_server() as (url, _):
        lk = H.run(body(url))
    assert lk is not None and lk.ok


def test_key_env_sends_the_stack_key_and_nothing_when_unset(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    with H.stub_server(call_log=str(log)) as (url, _):
        b = _primary_cfg(tmp_path, url, key_env="LLAMA_API_KEY").engines["prim"]
        eng = build_engine(b)

        async def go():
            monkeypatch.setenv("LLAMA_API_KEY", "s3cret-stack-key")
            await eng.probe([])
            monkeypatch.delenv("LLAMA_API_KEY")
            await eng.probe([])
            await eng.aclose()
        H.run(go())
    calls = H.read_calls(log)
    half = len(calls) // 2
    assert calls and all(c["auth"] == "Bearer s3cret-stack-key" for c in calls[:half])
    assert all(c["auth"] is None for c in calls[half:])


def test_probe_on_the_primary_uses_fewer_replays(tmp_path):
    log = tmp_path / "calls.jsonl"
    with H.stub_server(call_log=str(log)) as (url, _):
        b = _primary_cfg(tmp_path, url).engines["prim"]
        eng = build_engine(b)

        async def go():
            res = await eng.probe([])
            await eng.aclose()
            return res
        res = H.run(go())
    assert res.ok
    completions = [c for c in H.read_calls(log) if c["path"] == "/completion"]
    assert len(completions) == 2 + 3     # known answers + REPLAY_N_PRIMARY, not 2 + 10


@pytest.mark.linux_only(reason="GNU stat -c")
def test_instinct_sh_hands_the_primary_key_to_the_service_env_only(tmp_path):
    """`instinct.sh up` resolves LLAMA_API_KEY like conf.sh (OPENBEAST_API_KEY
    first) and gives it to the service through its ENVIRONMENT: present in
    /proc/<pid>/environ, absent from every argv."""
    import socket
    import subprocess
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfgp = H.write_config(tmp_path, {}, decisions=[DID])
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("INSTINCT") and k not in ("LLAMA_API_KEY", "OPENBEAST_API_KEY")}
    env.update({"INSTINCT_CONFIG": str(cfgp), "INSTINCT_PORT": str(port),
                "INSTINCT_RUN_DIR": str(tmp_path / "run"),
                "OPENBEAST_API_KEY": "stack-key-7f3a9c"})
    sh = H.REPO / "scripts" / "instinct.sh"
    r = subprocess.run(["bash", str(sh), "up"], env=env, capture_output=True, text=True,
                       timeout=60)
    try:
        assert r.returncode == 0, r.stderr + r.stdout
        pid = int((tmp_path / "run" / "instinct.pid").read_text())
        environ = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
        assert b"LLAMA_API_KEY=stack-key-7f3a9c" in environ
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        assert "stack-key-7f3a9c" not in ps
    finally:
        subprocess.run(["bash", str(sh), "down"], env=env, capture_output=True, timeout=60)
