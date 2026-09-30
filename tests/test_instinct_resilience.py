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
