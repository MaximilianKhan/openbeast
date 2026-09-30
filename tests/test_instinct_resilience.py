#!/usr/bin/env python3
"""What the service does when an engine misbehaves, and how the cascade picks
its answer (adversarial review of feat/instinct-core, B2/B3/M1/M2/M3/M6 and
m1/m7). Every case builds its own stub (in-process, ephemeral port) or an
httpx.MockTransport; each guard has a test that fails when it is removed."""
from __future__ import annotations

import itertools
import re
from pathlib import Path

import httpx
import pytest

import _instinct_helpers as H
from instinct import calibrate as C
from instinct.config import EngineBinding, load_config
from instinct.engines import EngineError, LockResult, build_engine
from instinct.engines._llm import token_ids
from instinct.engines.llamacpp import parse_top_logprobs
from instinct.render import Rendered
from instinct.service import Instinct

DID = "router.spawn_intent"


def _spec(chain: str, mode: str = "shadow") -> str:
    text = re.sub(r'chain = \[.*?\]', f"chain = {chain}", H.spec_text(DID), count=1)
    return text.replace('mode             = "shadow"', f'mode             = "{mode}"')


def _ledger_rows(cfg) -> int:
    return sum(1 for p in Path(cfg.ledger_dir).glob("decisions-*.jsonl") for _ in open(p))


async def _decide(inst, text, **kw):
    return await inst.decide({"contract": "instinct/1", "decision": DID,
                              "inputs": {"user_turn": text}, **kw})


def _gate(inst, cfg, engine, *, thresholds=None, label_mass_ref=None):
    """Calibration + passing gate record for `engine`'s live decision_hash."""
    h = inst.hashes[(DID, engine)]
    cp = C.calib_path(cfg.records_dir, DID, h)
    rec = {"decision": DID, "decision_hash": h, "T": 1.0,
           "thresholds": thresholds or {"inline": 0.5}}
    if label_mass_ref is not None:
        rec["label_mass_ref"] = {"p50": label_mass_ref}
    C.write_record(cp, rec)
    C.write_record(C.gate_path(cfg.records_dir, DID, h),
                   {"decision_hash": h, "passed": True, "calib_sha256": C.file_sha256(cp)})
    inst.refresh_records()


def _wrap(inst, name, url, completion):
    """Replace one engine's client with a MockTransport that forwards to the
    stub, except /completion, which `completion(n)` may answer instead."""
    real = inst.engines[name].client()
    n = itertools.count()

    async def handler(req: httpx.Request):
        if req.url.path == "/completion":
            alt = completion(next(n))
            if alt is not None:
                return alt
        r = await real.request(req.method, req.url.path, content=req.content,
                               headers=req.headers)
        return httpx.Response(r.status_code, content=r.content,
                              headers={"content-type": "application/json"})
    inst.engines[name]._client = httpx.AsyncClient(base_url=url,
                                                   transport=httpx.MockTransport(handler))


# --- B2: a malformed engine answer is a fallback, never a 500 -------------------

@pytest.mark.parametrize("payload", [
    {"completion_probabilities": [{"top_logprobs": 5}]},
    {"completion_probabilities": [{"top_logprobs": None}]},
    {"completion_probabilities": [{"top_logprobs": [5, 6]}]},
    {"completion_probabilities": [{"top_logprobs": [{"id": True, "logprob": -1}]}]},
    [1, 2],
])
def test_parse_top_logprobs_rejects_every_bad_shape(payload):
    with pytest.raises(EngineError):
        parse_top_logprobs(payload)


@pytest.mark.parametrize("payload", [
    {"completion_probabilities": [{"top_logprobs": 5}]},
    {"completion_probabilities": [{"top_logprobs": None}]},
    [1, 2],
])
def test_malformed_completion_falls_through_to_rules_with_one_ledger_row(tmp_path, payload):
    async def body(url):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                              extra_decisions={DID: _spec('["stub", "rules"]')})
        cfg = load_config(cfgp, env={})
        inst = Instinct(cfg)
        await inst.start()
        _wrap(inst, "stub", url, lambda n: httpx.Response(200, json=payload))
        before = _ledger_rows(cfg)
        r = await _decide(inst, "spawn a background agent please")
        await inst.aclose()
        return r, _ledger_rows(cfg) - before
    with H.stub_server() as (url, _):
        r, written = H.run(body(url))
    assert r["engine"]["id"] == "rules" and r["enforce"] is False
    stub = [c for c in r["cascade"] if c["engine"] == "stub"]
    assert stub and stub[0]["action"] == "fallback"
    assert written == 1     # I5 holds on the error path


def test_unexpected_engine_exception_is_contained_by_the_cascade(tmp_path, monkeypatch):
    """The per-engine catch-all: ANY exception from score() is a fallback."""
    class EngineBug(Exception):
        pass

    async def boom(req):
        raise EngineBug("engine bug")

    async def body(url):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                              extra_decisions={DID: _spec('["stub", "rules"]')})
        inst = Instinct(load_config(cfgp, env={}))
        await inst.start()
        monkeypatch.setattr(inst.engines["stub"], "score", boom)
        r = await _decide(inst, "hello")
        await inst.aclose()
        return r
    with H.stub_server() as (url, _):
        r = H.run(body(url))
    assert r["engine"]["id"] == "rules"
    assert "EngineBug" in r["cascade"][0]["error"]


# --- B3: an unexpected /tokenize shape disables a decision, not the service -----

@pytest.mark.parametrize("adapter", ["llamacpp_logprobs", "sglang_score"])
@pytest.mark.parametrize("tokens", [[{"token": 5}], [{"id": "x"}], [None], [True]])
def test_unexpected_tokenize_shape_never_takes_the_service_down(tmp_path, adapter, tokens):
    b = (H.llama_binding("http://127.0.0.1:1") if adapter == "llamacpp_logprobs"
         else H.sglang_binding("http://127.0.0.1:1", exec="sis"))
    cfgp = H.write_config(tmp_path, {"eng": b}, extra_decisions={DID: _spec('["eng", "rules"]')})

    def h(req):
        if req.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": tokens})
        return httpx.Response(404)
    inst = Instinct(load_config(cfgp, env={}), engine_ctx={"transport": httpx.MockTransport(h)})

    async def body():
        await inst.start()
        r = await _decide(inst, "hello")
        await inst.aclose()
        return r
    r = H.run(body())
    lk = inst.states["eng"].locks[DID]
    assert lk.ok is False and lk.reason.startswith("label_lock_failed")
    assert r["engine"]["id"] == "rules"
    assert r["cascade"][0] == {"engine": "eng", "action": "skipped",
                               "reason": "label_lock_failed", "ms": 0.0}


@pytest.mark.parametrize("toks", [[{"token": 5}], [{"id": "7"}], [True], [None], [1.5]])
def test_token_ids_rejects_unexpected_shapes(toks):
    with pytest.raises(EngineError):
        token_ids(toks)


def test_token_ids_accepts_both_documented_shapes():
    assert token_ids([1, {"id": 2, "piece": "x"}]) == [1, 2]


def test_lock_turns_any_tokenizer_exception_into_a_failed_lock():
    eng = build_engine(EngineBinding(name="e", **H.llama_binding("http://127.0.0.1:1")))

    class TokBug(Exception):
        pass

    async def tok(text):
        raise TokBug("surprise")
    eng._tokenize = tok
    from instinct.spec import load_spec
    lk = H.run(eng.lock(load_spec(H.DECISIONS / f"{DID}.toml")))
    assert lk.ok is False and "TokBug" in lk.reason


def test_attach_that_raises_is_a_failed_lock(tmp_path, monkeypatch):
    """Service-level containment, independent of the adapter's own catch."""
    cfgp = H.write_config(tmp_path, {"eng": H.llama_binding("http://127.0.0.1:1")},
                          extra_decisions={DID: _spec('["eng", "rules"]')})
    inst = Instinct(load_config(cfgp, env={}))

    async def boom(specs):
        raise KeyError("id")
    monkeypatch.setattr(inst.engines["eng"], "attach", boom)
    H.run(inst.reload())
    lk = inst.states["eng"].locks[DID]
    assert isinstance(lk, LockResult) and not lk.ok and "KeyError" in lk.reason
    assert DID in inst.specs
    H.run(inst.aclose())


# --- M1: an act that cannot be enforced does not end the cascade ----------------

def test_shadow_only_act_does_not_hide_a_gated_engine(tmp_path):
    async def body(url):
        cfgp, _ = H.promote_linear(tmp_path, gate_passed=False,
                                   chain='["linear", "stub", "rules"]',
                                   extra_engines={"stub": H.llama_binding(url)},
                                   thresholds={"inline": 0.5})
        cfg = load_config(cfgp, env={})
        inst = Instinct(cfg, repo_root=tmp_path)
        await inst.start()
        _gate(inst, cfg, "stub")
        assert inst.lifecycle(DID, "linear", "enforce") == ("shadow", "no_gate")
        assert inst.lifecycle(DID, "stub", "enforce") == ("enforce", None)
        r = await _decide(inst, "what is 17 times 23")
        # under a shadow ceiling the answer is still the first act (linear's),
        # but the walk goes on so shadow data measures every engine (A-2)
        rs = await _decide(inst, "what is 17 times 23", ceiling="shadow")
        await inst.aclose()
        return r, rs
    with H.stub_server() as (url, _):
        r, rs = H.run(body(url))
    assert [(c["engine"], c["action"]) for c in r["cascade"]] == [("linear", "act"),
                                                                  ("stub", "act")]
    assert r["cascade"][0]["mode"] == "shadow"
    assert r["enforce"] is True and r["engine"]["id"] == "stub"
    assert [c["engine"] for c in rs["cascade"]] == ["linear", "stub", "rules"]
    assert rs["engine"]["id"] == "linear" and rs["enforce"] is False
    assert rs["cascade"][1]["action"] == "act" and "probabilities" in rs["cascade"][1]


def test_unenforceable_act_is_still_the_would_answer(tmp_path):
    """No engine can enforce: the answer is the first act (what the service
    WOULD do), not a later abstain."""
    async def body(url):
        cfgp, _ = H.promote_linear(tmp_path, gate_passed=False,
                                   chain='["linear", "stub", "rules"]',
                                   extra_engines={"stub": H.llama_binding(url)},
                                   thresholds={"inline": 0.5})
        inst = Instinct(load_config(cfgp, env={}), repo_root=tmp_path)
        await inst.start()
        r = await _decide(inst, "what is 17 times 23")
        await inst.aclose()
        return r
    with H.stub_server() as (url, _):
        r = H.run(body(url))
    assert r["enforce"] is False and r["engine"]["id"] == "linear"
    assert r["would"]["action"] == "act" and r["fallback"]["reason"] == "no_gate"


# --- M2: shadow rows carry the model's distribution ------------------------------

def test_shadow_row_carries_the_llm_distribution_not_rules_one_hot(tmp_path):
    async def body(url):
        inst = Instinct(cfg)
        await inst.start()
        r = await _decide(inst, "spawn a background agent to port the tests and report back",
                          ceiling="shadow", baseline="hint")
        rn = await _decide(inst, "what is two plus two", ceiling="shadow", baseline="nohint")
        await inst.aclose()
        return r, rn
    with H.stub_server() as (url, _):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                              extra_decisions={DID: _spec('["stub", "rules"]')})
        cfg = load_config(cfgp, env={})
        r, rn = H.run(body(url))
    assert r["engine"]["id"] == "stub"
    probs = r["answer"]["probabilities"]
    assert 0.0 < probs["spawn"] < 1.0 and 0.0 < probs["inline"] < 1.0
    assert r["answer"]["label_mass"] == pytest.approx(0.97, abs=1e-6)
    entry = r["cascade"][0]
    assert entry["engine"] == "stub" and entry["label_mass"] == pytest.approx(0.97, abs=1e-6)
    assert set(entry["probabilities"]) == {"spawn", "inline"}
    rows = [__import__("json").loads(line) for p in Path(cfg.ledger_dir).glob("decisions-*")
            for line in open(p)]
    by_trace = {row["trace_id"]: row for row in rows}
    assert by_trace[r["trace_id"]]["label_mass"] == pytest.approx(0.97, abs=1e-6)
    # "nohint" is today's pass-through = inline, so agreement is measurable;
    # a hinted turn's legacy verdict is unknown here, so it stays null.
    assert by_trace[rn["trace_id"]]["agree"] is (rn["answer"]["label"] == "inline")
    assert by_trace[r["trace_id"]]["agree"] is None


def test_rules_answers_when_no_model_answered(tmp_path):
    cfgp = H.write_config(tmp_path, {"stub": H.llama_binding("http://127.0.0.1:1")},
                          extra_decisions={DID: _spec('["stub", "rules"]')})
    inst = Instinct(load_config(cfgp, env={}))

    async def body():
        await inst.start()
        r = await _decide(inst, "hello")
        await inst.aclose()
        return r
    r = H.run(body())
    assert r["engine"]["id"] == "rules" and r["answer"]["label"] == "inline"


# --- M3: auto-demotion sees failures rules rescued, and every row's mass --------

def test_rescued_engine_failures_auto_demote(tmp_path):
    async def body(url):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                              extra_decisions={DID: _spec('["stub", "rules"]', "enforce")})
        cfg = load_config(cfgp, env={})
        inst = Instinct(cfg)
        await inst.start()
        _gate(inst, cfg, "stub")
        _wrap(inst, "stub", url,
              lambda n: httpx.Response(500, json={}) if n % 5 == 0 else None)
        enforced = []
        for _ in range(120):
            enforced.append((await _decide(inst, "what is the capital of france"))["enforce"])
        await inst.aclose()
        return inst, enforced
    with H.stub_server() as (url, _):
        inst, enforced = H.run(body(url))
    assert inst.demotions.reason(DID) and "fallback_rate" in inst.demotions.reason(DID)
    assert any(enforced[:90]) and not any(enforced[-10:])


def test_label_mass_collapse_is_seen_on_rows_that_abstain(tmp_path):
    """low mass rows abstain (low_label_mass) — they must still be counted."""
    async def body(url):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                              extra_decisions={DID: _spec('["stub", "rules"]', "enforce")})
        cfg = load_config(cfgp, env={})
        inst = Instinct(cfg)
        await inst.start()   # the probe fails on low mass; force the engine up
        inst.states["stub"].healthy = True
        for _ in range(12):
            inst.engines["stub"].record_latency(1.0)
        _gate(inst, cfg, "stub", label_mass_ref=0.97)
        inst.autodemoter.mass_window = 20
        for _ in range(20):
            r = await _decide(inst, "what is the capital of france")
        await inst.aclose()
        return inst, r
    with H.stub_server({"low-mass": True}) as (url, _):
        inst, r = H.run(body(url))
    assert r["cascade"][0]["reason"] == "low_label_mass"
    assert "label_mass" in (inst.demotions.reason(DID) or "")


# --- M6: forced SIS is SIS on the wire ------------------------------------------

async def _score_close(eng, rendered):
    try:
        return await eng._score_rendered(rendered, {"yes": 1001, "no": 1002}, 2.0)
    finally:
        await eng.aclose()


def _sg(url, **kw):
    return build_engine(EngineBinding(name="sg", **H.sglang_binding(url, **kw)))


@pytest.mark.parametrize("forced,requests,items_each", [(None, 1, 3), ("sis", 3, 1)])
def test_forced_sis_sends_one_item_per_request(tmp_path, forced, requests, items_each):
    log = tmp_path / "calls.jsonl"
    with H.stub_server(call_log=str(log)) as (url, _):
        eng = _sg(url)
        eng.exec_forced = forced
        r = Rendered("rank", query="Is it true?\n", items=["a wet", "b cold", "c hot"])
        rows, exec_used, _ = H.run(_score_close(eng, r))
    calls = [c for c in H.read_calls(log) if c["path"] == "/v1/score"]
    assert len(rows) == 3 and len(calls) == requests
    assert all(len(c["body"]["items"]) == items_each for c in calls)
    assert exec_used == (forced or "mis")


def test_forced_sis_uses_the_probe_reference_server(tmp_path):
    log_main, log_ref = tmp_path / "main.jsonl", tmp_path / "ref.jsonl"
    with H.stub_server(call_log=str(log_main)) as (url, _), \
            H.stub_server(call_log=str(log_ref)) as (ref, _):
        eng = _sg(url, sis_url=ref)
        eng.exec_forced = "sis"
        H.run(_score_close(eng, Rendered("rank", query="q\n", items=["a", "b"])))
    assert not [c for c in H.read_calls(log_main) if c["path"] == "/v1/score"]
    assert len([c for c in H.read_calls(log_ref) if c["path"] == "/v1/score"]) == 2


# --- m1: one timeout does not bench an engine -----------------------------------

def test_one_outlier_does_not_set_early_p95():
    eng = build_engine(EngineBinding(name="e", **H.llama_binding("http://127.0.0.1:1",
                                                                 timeout_ms=1500)))
    assert eng.p95_ms() == 1500.0            # nothing measured yet
    for _ in range(12):
        eng.record_latency(5.0)
    eng.record_latency(2000.0)               # one timeout
    assert eng.p95_ms() == 5.0
    eng.record_latency(2000.0)               # a second one is a pattern
    assert eng.p95_ms() == 2000.0


# --- m7: a reload keeps auto-demotions unless the decision changed ---------------

def test_reload_keeps_auto_demotion_until_the_hash_changes(tmp_path):
    cfgp, _ = H.promote_linear(tmp_path)
    inst = Instinct(load_config(cfgp, env={}), repo_root=tmp_path)
    H.run(inst.start())
    inst.autodemoter._demote(DID, "fallback_rate 9/100")
    H.run(inst.reload())                       # e.g. SIGHUP from another decision's demote
    assert inst.demotions.reason(DID) == "auto:fallback_rate 9/100"
    # B-instinct-06: and a RESTART (a new process) does not re-arm it either
    again = Instinct(load_config(cfgp, env={}), repo_root=tmp_path)
    H.run(again.start())
    assert again.demotions.reason(DID) == "auto:fallback_rate 9/100"
    H.run(again.aclose())
    spec = tmp_path / "decisions" / f"{DID}.toml"
    spec.write_text(spec.read_text().replace("version     = 1", "version     = 2", 1)
                    .replace("version = 1", "version = 2", 1))
    H.run(inst.reload())
    assert inst.hashes[(DID, "linear")] is not None
    assert inst.demotions.reason(DID) is None
    H.run(inst.aclose())


# --- m5: periodic conformance off means an LLM engine can never enforce ---------

def test_probe_interval_zero_never_counts_as_fresh(tmp_path):
    async def body(url):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                              extra_decisions={DID: _spec('["stub", "rules"]', "enforce")},
                              service={"probe_interval_s": 0})
        cfg = load_config(cfgp, env={})
        inst = Instinct(cfg)
        await inst.start()
        _gate(inst, cfg, "stub")
        mode = inst.lifecycle(DID, "stub", "enforce")
        inst.cfg.probe_interval_s = 300            # control: periodic probing on
        mode_on = inst.lifecycle(DID, "stub", "enforce")
        await inst.aclose()
        return mode, mode_on
    with H.stub_server() as (url, _):
        mode, mode_on = H.run(body(url))
    assert mode == ("shadow", "conformance_failed") and mode_on == ("enforce", None)
