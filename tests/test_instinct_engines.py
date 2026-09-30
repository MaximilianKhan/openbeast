#!/usr/bin/env python3
"""Engine adapters against the hermetic stub scorer (plan §5.13
test_instinct_engines). Every stub records its calls; assertions read them."""
from __future__ import annotations

import os
import time

import pytest

import _instinct_helpers as H
from instinct.config import EngineBinding, load_config
from instinct.core import build_answer, decide_action
from instinct.engines import EngineError, ScoreReq, build_engine
from instinct.engines import linear as L
from instinct.engines import rules as R
from instinct.engines.sglang import parse_scores
from instinct.service import Instinct
from instinct.spec import load_spec

SPAWN = load_spec(H.DECISIONS / "router.spawn_intent.toml")
POOL = load_spec(H.DECISIONS / "hydra.pool_fit.toml")
TASK = load_spec(H.DECISIONS / "hydra.task_class.toml")


def _bind(url, adapter="llamacpp_logprobs", **kw):
    d = (H.llama_binding(url) if adapter == "llamacpp_logprobs" else H.sglang_binding(url))
    d.update(kw)
    return EngineBinding(name="e", **d)


async def _attach_score(eng, spec, inputs, items=None):
    locks = await eng.attach([spec])
    lk = locks[spec.id]
    assert lk.ok, lk.reason
    res = await eng.score(ScoreReq(spec=spec, inputs=inputs, items=items, label_ids=lk.ids,
                                   deadline_s=2.0))
    await eng.aclose()
    return lk, res


async def _probe_close(eng, specs=None):
    try:
        return await eng.probe([SPAWN] if specs is None else specs)
    finally:
        await eng.aclose()


# --- llama.cpp ------------------------------------------------------------------

def test_llamacpp_scores_and_locks(tmp_path):
    log = tmp_path / "calls.jsonl"
    with H.stub_server(call_log=str(log)) as (url, _):
        eng = build_engine(_bind(url))
        lk, res = H.run(_attach_score(eng, SPAWN, {"user_turn": "spawn a background agent to "
                                                                "port the tests, report back"}))
    assert len(set(lk.ids.values())) == 2
    row = res.rows[0]
    assert row.q["spawn"] > row.q["inline"]
    assert row.label_mass == pytest.approx(0.97, abs=1e-6)
    assert row.truncated == [] and res.exec_used == "sis"
    calls = H.read_calls(log)
    comp = [c for c in calls if c["path"] == "/completion"]
    assert comp and comp[-1]["body"]["temperature"] == -1 and comp[-1]["body"]["n_predict"] == 1
    assert comp[-1]["body"]["n_probs"] == 20 and comp[-1]["body"]["cache_prompt"] is True
    tok = [c for c in calls if c["path"] == "/tokenize"]
    assert tok and tok[0]["body"]["with_pieces"] is True and tok[0]["body"]["add_special"] is False


def test_llamacpp_dropped_label_is_truncated_and_never_acts():
    with H.stub_server({"drop-label": "yes"}) as (url, _):
        eng = build_engine(_bind(url))
        _, res = H.run(_attach_score(eng, SPAWN, {"user_turn": "what is 2+2"}))
    row = res.rows[0]
    assert row.truncated == ["spawn"]
    ans = build_answer(SPAWN, q=row.q, label_mass=row.label_mass, truncated=row.truncated,
                       calibrated=True, temperature=1.0)
    assert ans.label == "inline"
    assert decide_action(SPAWN, ans, thresholds={"inline": 0.0}) == ("abstain",
                                                                      "labels_truncated")


def test_multi_token_label_fails_lock_and_cascade_moves_on(tmp_path):
    with H.stub_server({"multi-token-label": "no"}) as (url, _):
        eng = build_engine(_bind(url))
        locks = H.run(eng.attach([SPAWN]))
        assert not locks[SPAWN.id].ok and "label_lock_failed" in locks[SPAWN.id].reason
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                              decisions=["router.spawn_intent"])

        async def go():
            inst = Instinct(load_config(cfgp, env={"INSTINCT_ENGINE_OVERRIDE": "stub"}))
            await inst.start()
            r = await inst.decide({"decision": "router.spawn_intent",
                                   "inputs": {"user_turn": "hello"}})
            await inst.aclose()
            return r
        r = H.run(go())
    stub_entry = next(c for c in r["cascade"] if c["engine"] == "stub")
    assert stub_entry["action"] == "skipped" and stub_entry["reason"] == "label_lock_failed"
    assert r["engine"]["id"] == "rules"          # next engine in the chain answered


def test_squatter_fails_identity_probe():
    with H.stub_server({"squatter": True}) as (url, _):
        eng = build_engine(_bind(url))
        res = H.run(_probe_close(eng))
    assert not res.ok and "identity" in res.reason
    with H.stub_server() as (url, _):             # control
        eng = build_engine(_bind(url))
        res = H.run(_probe_close(eng))
    assert res.ok, res.reason and not res.nondeterministic


@pytest.mark.parametrize("fault", [{"garbage": True}, {"invert": True}])
def test_garbage_or_inverted_model_fails_known_answer(fault):
    with H.stub_server(fault) as (url, _):
        eng = build_engine(_bind(url))
        res = H.run(_probe_close(eng))
    assert not res.ok and "known_answer" in res.reason


def test_low_mass_fails_known_answer():
    with H.stub_server({"low-mass": True}) as (url, _):
        res = H.run(_probe_close(build_engine(_bind(url))))
    assert not res.ok and "label_mass" in res.reason


def test_nondeterminism_is_flagged_not_failed():
    with H.stub_server({"nondeterministic": 0.8}) as (url, _):
        res = H.run(_probe_close(build_engine(_bind(url)), []))
    assert res.nondeterministic


def test_timeout_respects_deadline(tmp_path):
    with H.stub_server({"slow": 700}) as (url, _):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url, timeout_ms=150)},
                              decisions=["router.spawn_intent"])

        async def go():
            inst = Instinct(load_config(cfgp, env={"INSTINCT_ENGINE_OVERRIDE": "stub"}))
            await inst.reload()                     # locks (tokenize is slow too, fine)
            inst.states["stub"].healthy = True
            inst.states["stub"].last_probe = inst.clock()
            eng = inst.engines["stub"]
            eng.latencies.clear()
            eng.latencies.extend([10.0] * 25)       # measured p95 fits the deadline
            t0 = time.perf_counter()
            r = await inst.decide({"decision": "router.spawn_intent",
                                   "inputs": {"user_turn": "hello"}, "deadline_ms": 200})
            dt = time.perf_counter() - t0
            await inst.aclose()
            return r, dt
        r, dt = H.run(go())
    assert dt < 0.6
    stub_entry = next(c for c in r["cascade"] if c["engine"] == "stub")
    assert stub_entry["reason"] in ("engine_timeout", "label_lock_failed")


def test_engine_key_file_must_be_0600(tmp_path):
    kf = tmp_path / "k"
    kf.write_text("secret")
    os.chmod(kf, 0o644)
    with H.stub_server() as (url, _):
        async def go():
            eng = build_engine(_bind(url, key_file=str(kf)))
            bad = (await eng.attach([SPAWN]))[SPAWN.id]
            os.chmod(kf, 0o600)                     # control
            good = (await eng.attach([SPAWN]))[SPAWN.id]
            await eng.aclose()
            return bad, good
        bad, good = H.run(go())
    assert not bad.ok and "0600" in bad.reason
    assert good.ok


def test_engine_sends_bearer_key(tmp_path):
    kf = tmp_path / "k"
    fd = os.open(kf, os.O_WRONLY | os.O_CREAT, 0o600)
    os.write(fd, b"scorer-key")
    os.close(fd)
    seen = []
    with H.stub_server() as (url, stub):
        orig = stub.log

        def spy(path, body, fault):
            seen.append(path)
            orig(path, body, fault)
        stub.log = spy
        eng = build_engine(_bind(url, key_file=str(kf)))
        import httpx

        class Rec(httpx.AsyncHTTPTransport):
            async def handle_async_request(self, request):
                seen.append(request.headers.get("authorization"))
                return await super().handle_async_request(request)
        eng.ctx["transport"] = Rec()

        async def go():
            await eng.attach([SPAWN])
            await eng.aclose()
        H.run(go())
    assert "Bearer scorer-key" in seen


# --- SGLang -------------------------------------------------------------------------

def test_sglang_label_mass_is_sum_of_scores(tmp_path):
    log = tmp_path / "c.jsonl"
    with H.stub_server(call_log=str(log)) as (url, _):
        eng = build_engine(_bind(url, "sglang_score", exec="sis"))
        _, res = H.run(_attach_score(eng, SPAWN, {"user_turn": "hello"}))
    row = res.rows[0]
    assert row.label_mass == pytest.approx(sum(row.q.values()))
    assert row.label_mass == pytest.approx(0.97, abs=1e-6)
    body = [c for c in H.read_calls(log) if c["path"] == "/v1/score"][-1]["body"]
    assert body["apply_softmax"] is False and body["query"] == "" and len(body["items"]) == 1
    assert set(body) >= {"query", "items", "label_token_ids", "apply_softmax"}
    tok = [c for c in H.read_calls(log) if c["path"] == "/tokenize"][0]["body"]
    assert "text" in tok and tok["add_special_tokens"] is False


def test_sglang_rank_is_one_request_with_n_items(tmp_path):
    log = tmp_path / "c.jsonl"
    items = [f"pool {i}: a GPU pool" for i in range(7)]
    with H.stub_server(call_log=str(log)) as (url, _):
        eng = build_engine(_bind(url, "sglang_score"))
        _, res = H.run(_attach_score(eng, POOL, {"prompt_head": "fix my code"}, items))
    assert len(res.rows) == 7
    score_calls = [c for c in H.read_calls(log) if c["path"] == "/v1/score"]
    assert len(score_calls) == 1 and len(score_calls[0]["body"]["items"]) == 7
    assert score_calls[0]["body"]["query"].endswith("<pool>\n")


def test_mis_skew_fails_equivalence_and_forces_sis():
    with H.stub_server({"mis-skew": 0.2}) as (url, _):
        eng = build_engine(_bind(url, "sglang_score", exec="mis"))
        res = H.run(_probe_close(eng))
    assert res.ok and res.exec_forced == "sis"
    assert "FAILED" in res.checks["mis_equivalence"]
    with H.stub_server() as (url, _):             # control
        eng = build_engine(_bind(url, "sglang_score", exec="mis"))
        res = H.run(_probe_close(eng))
    assert res.ok and res.exec_forced is None and res.checks["mis_equivalence"] == "ok"


def test_forced_sis_changes_the_decision_hash(tmp_path):
    with H.stub_server({"mis-skew": 0.2}) as (url, _):
        cfgp = H.write_config(tmp_path, {"sg": H.sglang_binding(url)},
                              decisions=["router.spawn_intent"])

        async def go():
            inst = Instinct(load_config(cfgp, env={"INSTINCT_ENGINE_OVERRIDE": "sg"}))
            await inst.reload()
            before = inst.hashes[("router.spawn_intent", "sg")]
            await inst.probe_all()
            after = inst.hashes[("router.spawn_intent", "sg")]
            await inst.aclose()
            return before, after
        before, after = H.run(go())
    assert before and after and before != after


@pytest.mark.parametrize("resp,why", [
    ({"scores": [[2.5, -1.0]]}, "outside"),                 # logits, not probabilities
    ({"scores": [[0.5]]}, "wrong number"),
    ({"scores": [[[0.5, 0.2]]]}, "wrong number"),            # setwise 3-D
    ({"scores": [[0.8, 0.7]]}, "sum above"),
    ({"scores": [], "object": "scoring"}, "expected 1"),
    ({"scores": [[0.5, 0.1]], "object": "chat"}, "object"),
])
def test_sglang_rejects_malformed_scores(resp, why):
    with pytest.raises(EngineError, match=why):
        parse_scores(resp, 1, 2)


# --- in-process tiers ---------------------------------------------------------------

def test_linear_fit_is_deterministic_and_predicts():
    rows = [{"input": {"user_turn": t}, "label": y} for t, y in H.SPAWN_TRAIN]
    m1, m2 = L.fit(SPAWN, rows), L.fit(SPAWN, rows)
    assert m1 == m2
    loaded = dict(m1)
    loaded["_w"] = {lb: {int(k): v for k, v in w.items()} for lb, w in m1["weights"].items()}
    loaded["_seen"] = set(m1["seen"])
    p, ood = L.predict(loaded, SPAWN, {"user_turn": "spawn a background agent, report back"})
    assert p["spawn"] > 0.5 and ood > 0.3
    p2, ood2 = L.predict(loaded, SPAWN, {"user_turn": "zzqx vvkj qqqp"})
    assert ood2 < 0.3                          # unseen text is flagged OOD


def test_rules_router_hints():
    eng = build_engine(EngineBinding(name="rules", adapter="rules"))
    res = H.run(eng.score(ScoreReq(spec=SPAWN, inputs={"user_turn": "spawn an agent"})))
    assert res.rows[0].q == {"spawn": 1.0, "inline": 0.0} and res.rows[0].defer
    res = H.run(eng.score(ScoreReq(spec=SPAWN, inputs={"user_turn": "fix the login page"})))
    assert res.rows[0].q == {"spawn": 0.0, "inline": 1.0} and not res.rows[0].defer
    assert not eng.caps.probs                  # I6: never calibratable


@pytest.mark.parametrize("facts,label,mech", [
    ({"has_images": True, "est_prompt_tokens": 10}, "vision", "vision"),
    ({"est_prompt_tokens": 50000}, "long_context", "long_context"),
    ({"stream": False, "client_class": "batch"}, "bulk", None),
    ({"has_tools": True}, "code_agent", None),
    ({}, "chat", None),
])
def test_rules_hydra_static(facts, label, mech):
    inputs = {"prompt_head": "x", "est_prompt_tokens": 10, "has_images": False,
              "has_tools": False, "stream": True, "client_class": "interactive"}
    inputs.update(facts)
    eng = build_engine(EngineBinding(name="rules", adapter="rules"))
    res = H.run(eng.score(ScoreReq(spec=TASK, inputs=inputs)))
    row = res.rows[0]
    assert max(row.q, key=row.q.get) == label and row.mechanical == mech
    assert R.mechanical_label(TASK, inputs) == mech


@pytest.mark.parametrize("mode", ["empty", "prompt"])
def test_sglang_score_query_knob(tmp_path, mode):
    """VERIFY fallback for an SGLang that rejects query="": the whole prompt
    as the query plus one empty item. It changes the request, so the hash."""
    log = tmp_path / "calls.jsonl"
    with H.stub_server(call_log=str(log)) as (url, _):
        eng = build_engine(_bind(url, adapter="sglang_score", exec="sis", score_query=mode))
        H.run(_attach_score(eng, SPAWN, {"user_turn": "spawn a background agent"}))
    body = [c for c in H.read_calls(log) if c["path"] == "/v1/score"][-1]["body"]
    if mode == "empty":
        assert body["query"] == "" and len(body["items"]) == 1 and body["items"][0]
        assert "score_query" not in eng.hash_identity()
    else:
        assert body["query"] and body["items"] == [""]
        assert eng.hash_identity()["score_query"] == "prompt"


def test_score_query_is_linted(tmp_path):
    cfg = load_config(H.write_config(tmp_path, {
        "a": H.sglang_binding("http://127.0.0.1:30010", score_query="both"),
        "b": H.llama_binding("http://127.0.0.1:30011", score_query="prompt")}), env={})
    assert "empty|prompt" in cfg.engine_errors["a"] and "sglang" in cfg.engine_errors["b"]
