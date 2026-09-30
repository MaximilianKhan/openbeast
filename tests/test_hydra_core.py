#!/usr/bin/env python3
"""beast-hydra core (agents/hydra_core.py): validation, readiness parity data,
features, resolution, rules, filters, selection, health model. Pure and fast —
no sockets, no subprocesses (docs/BEAST_HYDRA_PLAN.md §6.11).

Run: python3 -m pytest tests/test_hydra_core.py -q
"""
from __future__ import annotations

import copy
import json
import random
import tomllib
from pathlib import Path

import pytest

import sys

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agents"))
import hydra_core as core  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "hydra"


def base() -> dict:
    """A three-node fleet: the 1-slot rig, a vLLM pair, a 2-slot llama box."""
    return {
        "schema": 1,
        "hydra": {"probe_interval_s": 1, "down_after": 2, "up_after": 2},
        "nodes": {
            "rig": {"url": "http://127.0.0.1:8080", "engine": "llama", "slots": 1},
            "sparks": {"url": "http://10.0.0.5:8000", "engine": "vllm", "slots": 8, "key_env": "SPARK_KEY"},
            "ti": {"url": "http://100.64.1.2:8080", "engine": "llama", "slots": 2, "key_env": "TI_KEY"},
        },
        "deployments": {
            "unc@rig": {"node": "rig", "upstream": "qwen-unc", "ctx": 262144, "family": "unc",
                        "caps": ["tools", "json_schema", "grammar", "vision", "reasoning_budget", "id_slot"]},
            "nvfp4@sparks": {"node": "sparks", "upstream": "qwen3.8-27b-nvfp4", "ctx": 262144,
                             "family": "stock", "caps": ["tools", "json_schema", "reasoning_budget"],
                             "conformance": "off"},
            "moe@ti": {"node": "ti", "upstream": "moe", "ctx": 131072, "family": "moe",
                       "caps": ["tools", "json_schema", "grammar", "id_slot"], "conformance": "off"},
        },
        "routes": {
            "beast": {"targets": [{"d": "unc@rig", "priority": 0}, {"d": "nvfp4@sparks", "priority": 1}],
                      "aliases": ["qwen-27b-q5", "default"], "affinity": "session"},
            "beast:fast": {"targets": [{"d": "moe@ti", "priority": 0}, {"d": "nvfp4@sparks", "priority": 1},
                                       {"d": "unc@rig", "priority": 2}]},
            "beast:long": {"targets": [{"d": "nvfp4@sparks", "priority": 0}, {"d": "unc@rig", "priority": 0}],
                           "min_ctx": 200000},
            "beast:vision": {"targets": [{"d": "unc@rig"}], "require": ["vision"]},
            "beast:max": {"targets": [{"d": "unc@rig"}, {"d": "nvfp4@sparks", "priority": 1}], "spill": False},
        },
        "rules": [],
    }


def cfg_of(raw=None, env=None) -> core.Config:
    return core.validate(raw or base(), env or {})


def ready_state(cfg, seed=7) -> core.FleetState:
    st = core.FleetState(cfg, random.Random(seed))
    for hs in st.health.values():
        hs.h.state = core.READY
    return st


def feats(cfg, body=None, headers=None, path="/v1/chat/completions") -> core.Features:
    body = body if body is not None else {"model": "beast", "messages": [{"role": "user", "content": "hi"}]}
    return core.extract_features(path, body, headers or {}, cfg)


def decide(cfg, st, body=None, headers=None, caller=None, **kw):
    return core.decide(cfg, st, feats(cfg, body, headers), caller or core.Caller(), 1000.0,
                       local_minutes=kw.pop("local_minutes", 600), **kw)


def ids(dec):
    return [c.d.id for c in dec.attempts]


# ───────────────────────────── validation ─────────────────────────────

def test_example_config_validates(tmp_path, monkeypatch):
    home = tmp_path / "home"
    kd = home / ".config" / "openbeast" / "hydra"
    kd.mkdir(parents=True)
    for k in ("sparks", "ti"):
        (kd / f"{k}.key").write_text("secret\n")
        (kd / f"{k}.key").chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    cfg = core.load_config(REPO / "hydra.toml.example", {})
    assert set(cfg.routes) >= {"beast", "beast:max", "beast:fast", "beast:long", "beast:vision", "classify"}
    nv = cfg.deployments["qwen38-nvfp4@sparks"]
    assert nv.upstream == "qwen3.8-27b-nvfp4" and nv.ctx == 262144      # from the profile
    assert cfg.nodes["rig"].gpu_lease and cfg.nodes["rig"].loopback
    assert cfg.route_by_id_or_alias["qwen-27b-q5"].id == "beast"
    assert not cfg.nodes["sparks-tf"].enabled
    assert any("then.route with no when.model" in w for w in cfg.warnings)
    assert cfg.settings.instinct.url == "http://127.0.0.1:8094"


def _served_alias(script: str) -> str:
    import re
    m = re.search(r'^\s*-a "([^"]+)"', (REPO / "scripts" / script).read_text(), re.M)
    assert m, script
    return m.group(1)


def test_example_upstreams_are_the_ids_llama_server_actually_lists():
    # llama-server lists its -a alias as the /v1/models id (server-context.cpp:
    # model_name = *params_base.model_alias.begin()). An example upstream that
    # is a slug instead makes the rig MISMATCH forever once the runbook's
    # "name the rig deployment explicitly" step is followed.
    raw = tomllib.loads((REPO / "hydra.toml.example").read_text())
    deps = raw["deployments"]
    assert deps["qwen38-unc-q5@rig"]["upstream"] == _served_alias("serve-qwen38-27b-uncensored-mtp-q5.sh")
    assert deps["qwen36-a3b-q4@ti"]["upstream"] == _served_alias("serve-qwen-35b-a3b.sh")
    assert all(d.get("verify_upstream", True) for d in deps.values())


def test_implicit_config_from_env():
    cfg = core.implicit_config({"INFERENCE_URL": "http://127.0.0.1:8080", "INFERENCE_BACKEND": "llama",
                                "INFERENCE_SLOTS": "2"})
    n = cfg.nodes["rig"]
    assert (n.url, n.engine, n.slots, n.key_env) == ("http://127.0.0.1:8080", "llama", 2, "LLAMA_API_KEY")
    d = cfg.deployments["local@rig"]
    assert d.ctx == 0 and d.upstream == "local" and not d.verify_upstream
    assert set(d.caps) == set(core.CAPS)
    assert cfg.route_by_id_or_alias["qwen-27b-q5"].id == "beast"
    assert cfg.route_by_id_or_alias["local"].id == "beast"
    assert not n.gpu_lease, "implicit config must behave like today: no lease drain"


def test_implicit_vllm_has_no_llama_only_caps_and_no_key_on_tensorfold():
    v = core.implicit_config({"INFERENCE_URL": "http://10.0.0.5:8000", "INFERENCE_BACKEND": "vllm",
                              "INFERENCE_MODEL": "qwen3.8-27b-nvfp4"})
    d = v.deployments["local@rig"]
    assert "id_slot" not in d.caps and "grammar" not in d.caps and d.upstream == "qwen3.8-27b-nvfp4"
    t = core.implicit_config({"INFERENCE_URL": "http://10.0.0.5:8000", "INFERENCE_BACKEND": "tensorfold"})
    assert not t.nodes["rig"].has_key


def test_implicit_ignores_route_id_in_openbeast_inference_model():
    # conf.sh points OPENBEAST_INFERENCE_MODEL at `beast` under HYDRA=true
    cfg = core.implicit_config({"OPENBEAST_INFERENCE_MODEL": "beast"})
    assert cfg.deployments["local@rig"].upstream == "local"


def test_print_default_config_round_trips():
    raw = core.implicit_raw({"INFERENCE_SLOTS": "3"})
    text = core.to_toml(raw, header="# hi\n")
    assert tomllib.loads(text) == raw
    assert core.validate(tomllib.loads(text), {}).nodes["rig"].slots == 3


def _mut(fn):
    raw = base()
    fn(raw)
    return raw


REJECT = {
    "unknown_top_key": (lambda r: r.update(extra=1), "unknown top-level key"),
    "schema": (lambda r: r.update(schema=2), "schema must be 1"),
    "unknown_hydra_key": (lambda r: r["hydra"].update(bogus=1), "hydra: unknown key 'bogus'"),
    "unknown_node_key": (lambda r: r["nodes"]["rig"].update(port=1), "nodes.rig: unknown key 'port'"),
    "wrong_type": (lambda r: r["nodes"]["rig"].update(slots="1"), "nodes.rig.slots: expected int"),
    "bool_is_not_int": (lambda r: r["nodes"]["rig"].update(slots=True), "got bool"),
    "dangling_node": (lambda r: r["deployments"]["unc@rig"].update(node="nope"), "is not a configured node"),
    "dangling_target": (lambda r: r["routes"]["beast"]["targets"].append({"d": "ghost"}),
                        "is not a configured deployment"),
    "empty_targets": (lambda r: r["routes"]["beast"].update(targets=[]), "targets must be a non-empty list"),
    "missing_default_route": (lambda r: r["hydra"].update(default_route="nope"), "default_route 'nope'"),
    "alias_collides_with_deployment": (lambda r: r["routes"]["beast"]["aliases"].append("unc@rig"),
                                       "claimed by both"),
    "alias_collides_across_routes": (lambda r: r["routes"]["beast:fast"].update(aliases=["default"]),
                                     "claimed by both"),
    "key_on_tensorfold": (lambda r: r["nodes"].update(tf={"url": "http://10.0.0.9:8000", "engine": "tensorfold",
                                                          "key_env": "X"}), "TensorFold has no auth"),
    "id_slot_on_vllm": (lambda r: r["deployments"]["nvfp4@sparks"]["caps"].append("id_slot"), "llama.cpp-only"),
    "grammar_on_vllm": (lambda r: r["deployments"]["nvfp4@sparks"]["caps"].append("grammar"), "llama.cpp-only"),
    "unknown_cap": (lambda r: r["deployments"]["nvfp4@sparks"]["caps"].append("telepathy"), "unknown capability"),
    "exclusive_group": (lambda r: (r["nodes"]["sparks"].update(exclusive_group="pair"),
                                   r["nodes"].update(tf={"url": "http://10.0.0.5:8001", "engine": "tensorfold",
                                                         "exclusive_group": "pair"})), "at most one may be enabled"),
    "gpu_lease_remote": (lambda r: r["nodes"]["ti"].update(gpu_lease=True), "gpu_lease is only allowed"),
    "public_url": (lambda r: r["nodes"]["ti"].update(url="http://8.8.8.8:8080"), "is public"),
    "url_with_path": (lambda r: r["nodes"]["ti"].update(url="http://10.0.0.7:8080/v1"), "no path"),
    "budget_over_gate": (lambda r: r["hydra"].update(pre_commit_budget_s=600), "must be < the gate read"),
    "ttft_over_budget": (lambda r: (r["hydra"].update(pre_commit_budget_s=100),
                                    r["nodes"]["rig"].update(ttft_timeout_s=200)), "ttft_timeout_s 200"),
    "idle_over_gate": (lambda r: r["nodes"]["rig"].update(idle_timeout_s=700), "idle_timeout_s 700"),
    "rule_route_missing": (lambda r: r["rules"].append({"name": "x", "when": {"model": "beast"},
                                                         "then": {"route": "nope"}}), "then.route 'nope'"),
    "rule_bad_hours": (lambda r: r["rules"].append({"name": "x", "when": {"hours": "25:00-01:00"},
                                                     "then": {"require": ["tools"]}}), "HH:MM-HH:MM"),
    "rule_unknown_when": (lambda r: r["rules"].append({"name": "x", "when": {"weather": "sunny"},
                                                        "then": {"require": ["tools"]}}), "unknown key 'weather'"),
    "rule_dup_name": (lambda r: r["rules"].extend([{"name": "x", "when": {}, "then": {"require": ["tools"]}}] * 2),
                      "is not unique"),
    "rule_unknown_node": (lambda r: r["rules"].append({"name": "x", "when": {}, "then": {"ignore_nodes": ["zz"]}}),
                          "not a configured node"),
    "instinct_engine_role": (lambda r: r["nodes"]["ti"].update(role="instinct-engine"),
                             "refuses to route to an instinct engine"),
    "node_is_instinct_url": (lambda r: r["nodes"].update(ins={"url": "http://127.0.0.1:8094", "engine": "openai"}),
                             "is an instinct URL"),
    "node_is_instinct_engine_url": (lambda r: (r["hydra"].update(instinct={"engine_urls": ["http://127.0.0.1:8082"]}),
                                               r["nodes"].update(sc={"url": "http://127.0.0.1:8082",
                                                                     "engine": "llama"})), "is an instinct URL"),
    "instinct_url_not_loopback": (lambda r: r["hydra"].update(instinct={"url": "http://10.0.0.1:8094"}),
                                  "must be a loopback"),
    "bad_engine": (lambda r: r["nodes"]["rig"].update(engine="ollama"), "engine must be one of"),
    "bad_affinity": (lambda r: r["routes"]["beast"].update(affinity="forever"), "affinity must be"),
    "bad_max_attempts": (lambda r: r["routes"]["beast"].update(max_attempts=9), "max_attempts must be 1..6"),
    "key_env_and_file": (lambda r: r["nodes"]["ti"].update(key_file="/nonexistent"), "not both"),
}


@pytest.mark.parametrize("name", sorted(REJECT))
def test_validation_rejects(name):
    fn, needle = REJECT[name]
    with pytest.raises(core.ConfigError) as e:
        core.validate(_mut(fn), {})
    assert any(needle in x for x in e.value.errors), e.value.errors


def _wrong_type_cases():
    wrong = {int: "2", float: "2", str: [1], bool: "yes", list: 7, dict: 7}
    tables = [(("hydra",), core._HYDRA_TYPES), (("hydra", "breaker"), core._BREAKER_TYPES),
              (("hydra", "instinct"), core._INSTINCT_TYPES), (("nodes", "rig"), core._NODE_TYPES),
              (("deployments", "unc@rig"), core._DEP_TYPES), (("routes", "beast"), core._ROUTE_TYPES)]
    out = []
    for where, types in tables:
        for k, want in types.items():
            for bad in [wrong[t] for t in want] + ["x", 1.5, [1], {"a": 1}]:
                if not isinstance(bad, want) or (isinstance(bad, bool) and bool not in want):
                    out.append((where, k, bad))
    out += [(("hydra", "instinct"), "engine_urls", [1]), (("routes", "beast"), "targets", [1]),
            (("routes", "beast"), "targets", [{"d": "unc@rig", "priority": "0"}])]
    return out


@pytest.mark.parametrize("where,key,bad", _wrong_type_cases(), ids=lambda v: repr(v)[:30])
def test_a_wrong_type_anywhere_is_a_config_error_never_a_crash(where, key, bad):
    # /hydra/reload and --check only understand ConfigError: a TypeError
    # would 500 the reload and leave last_reload_error unset.
    raw = base()
    t = raw
    for w in where:
        t = t.setdefault(w, {})
    t[key] = bad
    with pytest.raises(core.ConfigError) as e:
        core.validate(raw, {})
    assert e.value.errors


def test_validation_reports_every_error_at_once():
    raw = _mut(lambda r: (r.update(extra=1), r["nodes"]["rig"].update(port=1)))
    with pytest.raises(core.ConfigError) as e:
        core.validate(raw, {})
    assert len(e.value.errors) >= 2


def test_gate_timeout_env_moves_the_budget_bound():
    raw = _mut(lambda r: r["hydra"].update(pre_commit_budget_s=580))
    core.validate(raw, {})
    with pytest.raises(core.ConfigError, match="gate read"):
        core.validate(raw, {"OPENBEAST_EDGE_READ_TIMEOUT": "300"})


def test_key_file_mode_and_presence(tmp_path):
    kf = tmp_path / "ti.key"
    kf.write_text("k")
    kf.chmod(0o644)
    raw = _mut(lambda r: (r["nodes"]["ti"].pop("key_env"), r["nodes"]["ti"].update(key_file=str(kf))))
    with pytest.raises(core.ConfigError, match="chmod 600"):
        core.validate(raw, {})
    kf.chmod(0o600)
    core.validate(raw, {})
    kf.unlink()
    with pytest.raises(core.ConfigError, match="not readable"):
        core.validate(raw, {})


WARN = {
    "remote_no_key": (lambda r: r["nodes"]["ti"].pop("key_env"), "remote llama node with no key"),
    "allow_public": (lambda r: r["nodes"]["ti"].update(url="http://8.8.8.8:8080", allow_public=True),
                     "allow_public = true"),
    "ctx_zero": (lambda r: r["deployments"]["moe@ti"].update(ctx=0), "ctx = 0"),
    "route_rule_unscoped": (lambda r: r["rules"].append({"name": "x", "when": {"has_images": True},
                                                          "then": {"route": "beast:vision"}}), "redirects EVERY"),
    "all_targets_disabled": (lambda r: r["deployments"]["unc@rig"].update(enabled=False), "every target is disabled"),
    "budget_590": (lambda r: r["hydra"].update(pre_commit_budget_s=595), ">= 590"),
    "short_name": (lambda r: r["nodes"]["ti"].update(url="http://spark-a:8000"), "short name"),
}


@pytest.mark.parametrize("name", sorted(WARN))
def test_validation_warns(name):
    fn, needle = WARN[name]
    cfg = core.validate(_mut(fn), {})
    assert any(needle in w for w in cfg.warnings), cfg.warnings


def test_hash_stable_under_reordering_and_changes_on_meaning(tmp_path):
    a = cfg_of()
    raw = base()
    raw["nodes"] = dict(reversed(list(raw["nodes"].items())))
    raw["deployments"]["unc@rig"]["caps"] = list(reversed(raw["deployments"]["unc@rig"]["caps"]))
    assert cfg_of(raw).hash == a.hash
    raw["routes"]["beast"]["spill"] = False
    assert cfg_of(raw).hash != a.hash
    # key CONTENTS never enter the hash (the path does)
    kf = tmp_path / "k"
    kf.write_text("one")
    kf.chmod(0o600)
    r2 = _mut(lambda r: (r["nodes"]["ti"].pop("key_env"), r["nodes"]["ti"].update(key_file=str(kf))))
    h1 = cfg_of(r2).hash
    kf.write_text("two")
    assert cfg_of(r2).hash == h1


def test_profile_derivation_and_conflicts():
    raw = _mut(lambda r: r["deployments"].update({"p@sparks": {"node": "sparks",
                                                                "profile": "qwen38-27b-nvfp4-vllm"}}))
    d = cfg_of(raw).deployments["p@sparks"]
    assert (d.upstream, d.ctx, d.profile) == ("qwen3.8-27b-nvfp4", 262144, "qwen38-27b-nvfp4-vllm")
    assert d.conformance == "required"             # non-loopback default
    bad = copy.deepcopy(raw)
    bad["deployments"]["p@sparks"]["upstream"] = "something-else"
    with pytest.raises(core.ConfigError, match="conflicts with profile SERVED_MODEL_NAME"):
        cfg_of(bad)
    bad = copy.deepcopy(raw)
    bad["deployments"]["p@sparks"]["ctx"] = 4096
    with pytest.raises(core.ConfigError, match="MAX_MODEL_LEN"):
        cfg_of(bad)
    bad = copy.deepcopy(raw)
    bad["deployments"]["p@sparks"]["node"] = "ti"
    with pytest.raises(core.ConfigError, match="BACKEND=vllm but node ti is engine=llama"):
        cfg_of(bad)


def test_profile_slots_mismatch_warns_only_when_slots_implicit():
    raw = _mut(lambda r: (r["nodes"]["sparks"].pop("slots"),
                          r["deployments"].update({"p@sparks": {"node": "sparks",
                                                                "profile": "qwen38-27b-nvfp4-vllm"}})))
    assert any("MAX_NUM_SEQS=8" in w for w in cfg_of(raw).warnings)


# ───────────────────────────── readiness + conformance ─────────────────────────────

READY_FIXTURES = sorted((FIX / "ready").glob("*.json"))


@pytest.mark.parametrize("path", READY_FIXTURES, ids=lambda p: p.stem)
@pytest.mark.parametrize("engine", ["llama", "vllm", "tensorfold"])
def test_engine_ready_matrix(path, engine):
    fx = json.loads(path.read_text())
    assert core.engine_ready(engine, fx["status"], fx["body"].encode()) == fx["expect"][engine]


def test_engine_ready_unreachable_and_generic():
    assert core.engine_ready("llama", None, None) == "down"
    assert core.engine_ready("openai", 200, b"") == "ready"
    assert core.engine_ready("openai", 500, b"") == "down"


def test_conformance_verdict():
    good = {"url": "http://10.0.0.5:8000", "ok": True, "results": [{"name": "tools", "status": "pass"}]}
    assert core.conformance_verdict(good, "http://10.0.0.5:8000") == ("pass", False)
    assert core.conformance_verdict(good, "http://10.0.0.6:8000") == ("fail", False)   # another box's report
    assert core.conformance_verdict(None, "http://10.0.0.5:8000") == ("missing", False)
    bad = dict(good, ok=False, results=[{"name": "tools", "status": "fail"}])
    assert core.conformance_verdict(bad, "http://10.0.0.5:8000") == ("fail", True)


# ───────────────────────────── features ─────────────────────────────

def test_feature_extraction():
    cfg = cfg_of()
    img = {"model": "beast", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "what"}, {"type": "image_url", "image_url": {"url": "data:,"}}]}]}
    assert feats(cfg, img).has_images
    assert feats(cfg, {"model": "beast", "messages": [], "tools": [{"type": "function"}]}).has_tools
    assert feats(cfg, {"model": "b", "response_format": {"type": "json_schema"}}).needs_json_schema
    assert feats(cfg, {"model": "b", "response_format": {"type": "json_object"}}).needs_json_schema
    assert not feats(cfg, {"model": "b", "response_format": {"type": "text"}}).needs_json_schema
    assert feats(cfg, {"model": "b", "grammar": "root ::= x"}).needs_grammar
    assert feats(cfg, {"model": "b", "reasoning_budget_tokens": 10}).needs_reasoning_budget
    assert feats(cfg, {"model": "b", "input": "x"}, path="/v1/embeddings").needs_embeddings
    short = feats(cfg, {"model": "b", "messages": [{"role": "user", "content": "x" * 100}]})
    long = feats(cfg, {"model": "b", "messages": [{"role": "user", "content": "x" * 1000}]})
    assert long.est_prompt_tokens > short.est_prompt_tokens >= 100 / 3
    assert feats(cfg, {"model": "b", "max_tokens": 77}).max_tokens == 77
    assert feats(cfg, {"model": "b", "max_completion_tokens": 55}).max_tokens == 55
    assert feats(cfg, {"model": "b"}).max_tokens == 4096


def test_session_key_precedence_and_prompt_head():
    cfg = cfg_of()
    body = {"model": "b", "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]}
    assert feats(cfg, body, {"x-conversation-id": "c1", "x-hydra-session": "s1"}).session_key == "c1"
    assert feats(cfg, body, {"x-hydra-session": "s1"}).session_key == "s1"
    h = feats(cfg, body).session_key
    assert h and len(h) == 16 and h == feats(cfg, body).session_key
    assert feats(cfg, {"model": "b"}).session_key is None
    big = {"model": "b", "messages": [{"role": "user", "content": "A" * 600 + "B" * 3000}]}
    ph = feats(cfg, big).prompt_head
    assert len(ph) == 2000 and ph.startswith("A" * 500) and ph.endswith("B" * 1500)


# ───────────────────────────── resolution ─────────────────────────────

def test_resolution():
    cfg = cfg_of()
    st = ready_state(cfg)
    d = decide(cfg, st, {"model": "moe@ti"})
    assert d.strict and ids(d) == ["moe@ti"] and d.route == "pin"
    d = decide(cfg, st, {"model": "beast"}, {"x-hydra-pin": "nvfp4@sparks"})
    assert d.strict and ids(d) == ["nvfp4@sparks"]
    d = decide(cfg, st, {"model": "beast"}, pin="moe@ti")
    assert ids(d) == ["moe@ti"]
    assert decide(cfg, st, {"model": "beast"}).route == "beast"
    assert decide(cfg, st, {"model": "qwen-27b-q5"}).route == "beast"
    d = decide(cfg, st, {"model": "gpt-4o"})
    assert d.route == "beast" and any("default_route" in n for n in d.trace.notes)
    d = decide(cfg, st, {"model": "beast"}, pin="ghost@x")
    assert d.status == 404 and d.error_type == "hydra_unknown_deployment"
    raw = base()
    raw["hydra"]["unknown_model"] = "404"
    cfg4 = cfg_of(raw)
    d = decide(cfg4, ready_state(cfg4), {"model": "gpt-4o"})
    assert d.status == 404 and d.error_type == "hydra_unknown_model"


def test_strict_never_substitutes():
    cfg = cfg_of()
    st = ready_state(cfg)
    st.health["unc@rig"].h.state = core.DOWN
    d = decide(cfg, st, {"model": "unc@rig"})
    assert d.status == 503 and d.error_type == "hydra_pinned_unavailable" and d.attempts == []
    d = decide(cfg, st, {"model": "nvfp4@sparks", "grammar": "x"})
    assert d.status == 422 and d.error_type == "hydra_pin_incompatible"
    # a pin skips rules entirely
    raw = base()
    raw["rules"] = [{"name": "all-fast", "when": {}, "then": {"route": "beast:fast"}}]
    cfg2 = cfg_of(raw)
    d = decide(cfg2, ready_state(cfg2), {"model": "unc@rig"})
    assert ids(d) == ["unc@rig"] and d.trace.rules == []
    # and the ctx filter: the engine's own overflow 400 must reach the caller
    huge = {"model": "moe@ti", "messages": [{"role": "user", "content": "x" * 3_000_000}]}
    assert ids(decide(cfg, ready_state(cfg), huge)) == ["moe@ti"]


# ───────────────────────────── rules ─────────────────────────────

def _rules(*rules):
    raw = base()
    raw["rules"] = list(rules)
    cfg = cfg_of(raw)
    return cfg, ready_state(cfg)


def test_first_setter_wins_and_route_rewrite_is_one_hop():
    cfg, st = _rules(
        {"name": "a", "when": {"model": "beast"}, "then": {"route": "beast:fast", "ignore_nodes": ["ti"]}},
        {"name": "b", "when": {"model": "beast"}, "then": {"route": "beast:long", "ignore_nodes": ["sparks"]}},
        {"name": "c", "when": {"model": "beast:fast"}, "then": {"route": "beast:vision"}})
    d = decide(cfg, st)
    assert d.route == "beast:fast", "the first rule to set route wins; no chaining through rule c"
    assert "moe@ti" not in ids(d) and "moe@ti" in d.trace.excluded
    assert d.trace.rules == ["a", "b"]


def test_hours_wraps_midnight():
    assert core.in_hours("22:00-06:00", 23 * 60) and core.in_hours("22:00-06:00", 60)
    assert not core.in_hours("22:00-06:00", 12 * 60)
    assert core.in_hours("09:00-17:00", 9 * 60) and not core.in_hours("09:00-17:00", 17 * 60)
    cfg, st = _rules({"name": "night", "when": {"model": "beast", "hours": "22:00-06:00"},
                      "then": {"route": "beast:fast"}})
    assert decide(cfg, st, local_minutes=23 * 60).route == "beast:fast"
    assert decide(cfg, st, local_minutes=12 * 60).route == "beast"


def test_device_and_role_need_a_trusted_caller():
    cfg, st = _rules({"name": "phone", "when": {"device": "max-phone"}, "then": {"route": "beast:fast"}},
                     {"name": "guest", "when": {"role": "user"}, "then": {"ignore_nodes": ["rig"]}})
    spoof = core.Caller(False, "max-phone", "user")
    assert decide(cfg, st, caller=spoof).route == "beast"
    ok = core.Caller(True, "max-phone", None)
    assert decide(cfg, st, caller=ok).route == "beast:fast"
    d = decide(cfg, st, caller=core.Caller(True, None, "user"))
    assert "unc@rig" not in ids(d) and "ignore_nodes" in d.trace.excluded["unc@rig"]


def test_prefer_moves_to_priority_minus_one():
    cfg, st = _rules({"name": "p", "when": {"model": "beast"}, "then": {"prefer": ["nvfp4@sparks"]}})
    assert ids(decide(cfg, st))[0] == "nvfp4@sparks"


def test_task_class_is_false_without_an_enforced_answer():
    cfg, st = _rules({"name": "bulk", "when": {"task_class": "bulk", "model": "beast"},
                      "then": {"route": "beast:fast"}})
    assert cfg.uses_task_class()
    assert decide(cfg, st).route == "beast"
    assert decide(cfg, st, task_class="chat").route == "beast"
    assert decide(cfg, st, task_class="bulk").route == "beast:fast"


def test_instinct_worthwhile_needs_a_rule_and_two_eligible():
    cfg = cfg_of()
    st = ready_state(cfg)
    assert not core.instinct_worthwhile(cfg, st, feats(cfg), core.Caller(), 1000.0)
    cfg, st = _rules({"name": "bulk", "when": {"task_class": "bulk", "model": "beast"},
                      "then": {"route": "beast:vision"}})
    assert core.instinct_worthwhile(cfg, st, feats(cfg), core.Caller(), 1000.0)
    # a pin never asks
    assert not core.instinct_worthwhile(cfg, st, feats(cfg, {"model": "unc@rig"}), core.Caller(), 1000.0)
    # only one eligible deployment anywhere → nothing to judge
    st.health["nvfp4@sparks"].h.state = core.DOWN
    assert not core.instinct_worthwhile(cfg, st, feats(cfg), core.Caller(), 1000.0)
    # rule scoped to another model → no call
    cfg, st = _rules({"name": "bulk", "when": {"task_class": "bulk", "model": "beast:fast"},
                      "then": {"route": "beast:vision"}})
    assert not core.instinct_worthwhile(cfg, st, feats(cfg), core.Caller(), 1000.0)


# ───────────────────────────── filters ─────────────────────────────

def test_filter_reasons():
    cfg = cfg_of()
    st = ready_state(cfg)
    st.health["unc@rig"].h.state = core.DOWN
    assert "DOWN" in decide(cfg, st).trace.excluded["unc@rig"]
    st = ready_state(cfg)
    hs = st.health["unc@rig"]
    for _ in range(5):
        hs.record_failure(1000.0)
    assert "breaker OPEN" in decide(cfg, st).trace.excluded["unc@rig"]
    st = ready_state(cfg)
    st.drained["rig"] = "lease"
    assert decide(cfg, st).trace.excluded["unc@rig"] == "node rig drained (lease)"
    st = ready_state(cfg)
    d = decide(cfg, st, {"model": "beast", "messages": [], "grammar": "g"})
    assert d.trace.excluded["nvfp4@sparks"] == "missing caps grammar"
    d = decide(cfg, st, {"model": "beast:long", "messages": []})
    assert "unc@rig" not in d.trace.excluded
    assert ids(d) and set(ids(d)) == {"nvfp4@sparks", "unc@rig"}
    raw = base()
    raw["deployments"]["nvfp4@sparks"]["ctx"] = 131072
    cfg2 = cfg_of(raw)
    d = decide(cfg2, ready_state(cfg2), {"model": "beast:long", "messages": []})
    assert d.trace.excluded["nvfp4@sparks"] == "ctx 131072 < route min_ctx 200000"


def test_ctx_fit_with_margin_and_zero_skip():
    raw = base()
    raw["deployments"]["unc@rig"]["ctx"] = 10000
    raw["deployments"]["nvfp4@sparks"]["ctx"] = 0          # unknown: filter off
    cfg = cfg_of(raw)
    st = ready_state(cfg)
    body = {"model": "beast", "max_tokens": 100, "messages": [{"role": "user", "content": "x" * 29000}]}
    d = decide(cfg, st, body)
    assert d.trace.excluded["unc@rig"].startswith("ctx 10000 (usable 9500) < need ")
    assert ids(d) == ["nvfp4@sparks"]


def test_ctx_last_resort_keeps_the_engine_overflow_path():
    raw = base()
    raw["deployments"]["unc@rig"]["ctx"] = 1000
    raw["deployments"]["nvfp4@sparks"]["ctx"] = 2000
    cfg = cfg_of(raw)
    d = decide(cfg, ready_state(cfg), {"model": "beast", "messages": [{"role": "user", "content": "x" * 30000}]})
    assert d.ok and ids(d) == ["nvfp4@sparks"]
    assert any("last resort" in n for n in d.trace.notes)
    # but a DOWN target is never the last resort
    st = ready_state(cfg)
    st.health["nvfp4@sparks"].h.state = core.DOWN
    d = decide(cfg, st, {"model": "beast", "messages": [{"role": "user", "content": "x" * 30000}]})
    assert ids(d) == ["unc@rig"]


def test_conformance_required_blocks_until_a_pass():
    raw = base()
    raw["deployments"]["nvfp4@sparks"]["conformance"] = "required"
    cfg = cfg_of(raw)
    st = ready_state(cfg)
    st.drained["rig"] = "manual"
    d = decide(cfg, st)
    assert d.status == 503 and "conformance required, report missing" in d.trace.excluded["nvfp4@sparks"]
    st.conformance["nvfp4@sparks"] = ("pass", False)
    assert ids(decide(cfg, st)) == ["nvfp4@sparks"]
    st.conformance["nvfp4@sparks"] = ("pass", True)            # tools failed → cap removed
    d = decide(cfg, st, {"model": "beast", "messages": [], "tools": [{"type": "function"}]})
    assert d.trace.excluded["nvfp4@sparks"] == "missing caps tools"


def test_only_nodes_and_disabled():
    cfg, st = _rules({"name": "o", "when": {"model": "beast:fast"}, "then": {"only_nodes": ["sparks"]}})
    d = decide(cfg, st, {"model": "beast:fast"})
    assert ids(d) == ["nvfp4@sparks"] and "only_nodes" in d.trace.excluded["moe@ti"]
    raw = base()
    raw["nodes"]["ti"]["enabled"] = False
    cfg = cfg_of(raw)
    assert decide(cfg, ready_state(cfg), {"model": "beast:fast"}).trace.excluded["moe@ti"] == "node ti disabled"


# ───────────────────────────── selection ─────────────────────────────

def test_group_zero_preferred_and_spill():
    cfg = cfg_of()
    st = ready_state(cfg)
    assert ids(decide(cfg, st)) == ["unc@rig", "nvfp4@sparks"]
    st.admit("unc@rig", "rig", 1000.0)                  # the 1-slot rig is busy
    d = decide(cfg, st)
    assert ids(d)[0] == "nvfp4@sparks" and any("spill" in n for n in d.trace.notes)


def test_no_spill_queues_on_group_zero():
    cfg = cfg_of()
    st = ready_state(cfg)
    st.admit("unc@rig", "rig", 1000.0)
    d = decide(cfg, st, {"model": "beast:max"})
    assert ids(d)[0] == "unc@rig" and any("spill=false" in n for n in d.trace.notes)


def test_all_saturated_goes_to_the_preferred_group():
    cfg = cfg_of()
    st = ready_state(cfg)
    st.admit("unc@rig", "rig", 1000.0)
    for _ in range(8):
        st.admit("nvfp4@sparks", "sparks", 1000.0)
    d = decide(cfg, st)
    assert ids(d)[0] == "unc@rig" and any("all saturated" in n for n in d.trace.notes)


def test_weights_least_load_and_seeded_tiebreak():
    cfg = cfg_of()
    st = ready_state(cfg)
    # beast:long: sparks (8 slots) vs rig (1 slot), both priority 0
    assert ids(decide(cfg, st, {"model": "beast:long"}))[0] == "nvfp4@sparks"
    raw = base()
    raw["routes"]["twin"] = {"targets": [{"d": "moe@ti"}, {"d": "unc@rig", "weight": 2.0}]}
    raw["nodes"]["rig"]["slots"] = 2
    cfg = cfg_of(raw)
    st = ready_state(cfg)
    assert ids(decide(cfg, st, {"model": "twin"}))[0] == "unc@rig"         # weight halves its load
    firsts = {ids(decide(cfg_of(base()), ready_state(cfg_of(base()), seed=s),
                         {"model": "beast:long", "messages": []}))[0] for s in range(3)}
    assert firsts == {"nvfp4@sparks"}
    raw = base()
    raw["routes"]["even"] = {"targets": [{"d": "moe@ti"}, {"d": "nvfp4@sparks"}]}
    raw["nodes"]["sparks"]["slots"] = 2
    cfg = cfg_of(raw)
    seen = {ids(decide(cfg, ready_state(cfg, seed=s), {"model": "even"}))[0] for s in range(20)}
    assert seen == {"moe@ti", "nvfp4@sparks"}, "equal load must tie-break randomly"
    assert len({ids(decide(cfg, ready_state(cfg, seed=5), {"model": "even"}))[0] for _ in range(5)}) == 1


def test_affinity_session_sticky_none_and_saturation_bypass():
    raw = base()
    raw["routes"]["even"] = {"targets": [{"d": "moe@ti"}, {"d": "nvfp4@sparks"}], "affinity": "session"}
    raw["routes"]["stick"] = {"targets": [{"d": "unc@rig"}, {"d": "nvfp4@sparks", "priority": 1}],
                              "affinity": "sticky"}
    raw["routes"]["free"] = {"targets": [{"d": "moe@ti"}, {"d": "nvfp4@sparks"}], "affinity": "none"}
    cfg = cfg_of(raw)
    st = ready_state(cfg)
    h = {"x-conversation-id": "conv"}
    st.affinity.put("conv", "nvfp4@sparks", 1000.0)
    st.admit("moe@ti", "ti", 1000.0)          # would otherwise make ti less attractive... make sparks worse:
    for _ in range(3):
        st.admit("nvfp4@sparks", "sparks", 1000.0)
    assert ids(decide(cfg, st, {"model": "even"}, h))[0] == "nvfp4@sparks"      # session hit in-group
    # sticky crosses groups: the conversation spilled to sparks, stays there while the rig is free
    st2 = ready_state(cfg)
    st2.affinity.put("conv", "nvfp4@sparks", 1000.0)
    assert ids(decide(cfg, st2, {"model": "stick"}, h))[0] == "nvfp4@sparks"
    for _ in range(8):
        st2.admit("nvfp4@sparks", "sparks", 1000.0)
    assert ids(decide(cfg, st2, {"model": "stick"}, h))[0] == "unc@rig"        # bypassed when saturated
    st3 = ready_state(cfg, seed=1)
    st3.affinity.put("conv", "nvfp4@sparks", 1000.0)
    for _ in range(4):
        st3.admit("nvfp4@sparks", "sparks", 1000.0)
    assert ids(decide(cfg, st3, {"model": "free"}, h))[0] == "moe@ti"          # none ignores it


def test_affinity_lru_ttl_and_eviction():
    a = core.AffinityLRU(max_items=2, ttl_s=10)
    a.put("a", "x", 0)
    a.put("b", "y", 0)
    assert a.get("a", 5) == "x"
    a.put("c", "z", 5)                        # evicts b (a was just used)
    assert a.get("b", 5) is None and a.get("a", 5) == "x" and len(a) == 2
    assert a.get("a", 100) is None            # expired


def test_same_family_and_max_attempts():
    raw = base()
    raw["routes"]["beast"]["same_family"] = True
    cfg = cfg_of(raw)
    d = decide(cfg, ready_state(cfg))
    assert ids(d) == ["unc@rig"] and d.trace.excluded["nvfp4@sparks"] == "family stock != unc"
    raw = base()
    raw["routes"]["beast:fast"]["max_attempts"] = 2
    cfg = cfg_of(raw)
    assert len(ids(decide(cfg, ready_state(cfg), {"model": "beast:fast"}))) == 2
    assert len(ids(decide(cfg_of(), ready_state(cfg_of()), {"model": "beast:fast"}))) == 3


# ───────────────────────────── health ─────────────────────────────

def _hs(**kw):
    s = core.Settings(down_after=2, up_after=2, breaker=core.Breaker(**kw) if kw else core.Breaker())
    return core.HealthState(s)


def test_hysteresis():
    hs = _hs()
    assert hs.on_probe("ready", 0) == core.UNKNOWN
    assert hs.on_probe("ready", 1) == core.READY
    assert hs.on_probe("down", 2) == core.READY
    assert hs.on_probe("ready", 3) == core.READY          # streak reset
    hs.on_probe("down", 4)
    assert hs.on_probe("down", 5) == core.DOWN
    assert hs.on_probe("ready", 6) == core.DOWN
    assert hs.on_probe("ready", 7) == core.READY


def test_loading_is_not_down_and_not_a_breaker_failure():
    hs = _hs()
    hs.on_probe("ready", 0)
    hs.on_probe("ready", 1)
    for t in range(2, 10):
        assert hs.on_probe("loading", t) == core.LOADING
    assert hs.h.breaker == core.CLOSED and hs.h.fails == 0
    assert hs.on_probe("ready", 11) == core.READY          # one ok probe ends LOADING
    assert hs.admit_reason(11) is None


def test_breaker_cycle_and_probes_never_close_it():
    hs = _hs(fail_threshold=3, open_s=30, success_threshold=2)
    hs.h.state = core.READY
    for _ in range(3):
        hs.record_failure(100)
    assert hs.breaker_state(100) == core.OPEN and "breaker OPEN" in hs.admit_reason(100)
    for t in range(101, 125):
        hs.on_probe("ready", t)                             # probes cannot close it
    assert hs.breaker_state(125) == core.OPEN
    assert hs.breaker_state(131) == core.HALF_OPEN and hs.admit_reason(131) is None
    hs.record_failure(132)                                  # trial failed → OPEN again
    assert hs.breaker_state(132) == core.OPEN
    assert hs.breaker_state(163) == core.HALF_OPEN
    hs.record_success(163)
    assert hs.breaker_state(163) == core.HALF_OPEN
    hs.record_success(164)
    assert hs.breaker_state(164) == core.CLOSED


def test_half_open_admits_exactly_one_trial():
    cfg = cfg_of()
    st = ready_state(cfg)
    hs = st.health["nvfp4@sparks"]
    for _ in range(5):
        hs.record_failure(0)
    now = 31.0
    assert hs.breaker_state(now) == core.HALF_OPEN
    adm = st.admit("nvfp4@sparks", "sparks", now)
    assert hs.admit_reason(now) == "breaker HALF_OPEN (trial in flight)"
    adm.release()
    adm.release()                                           # idempotent
    assert hs.admit_reason(now) is None and st.inflight("nvfp4@sparks") == 0


def test_try_admit_revets_a_stale_plan():
    # decide() plans failover up front; admission must re-check (plan §6.5).
    cfg = cfg_of()
    st = ready_state(cfg)
    hs = st.health["nvfp4@sparks"]
    for _ in range(5):
        hs.record_failure(0)
    now = 31.0
    first, why = st.try_admit("nvfp4@sparks", "sparks", now)
    assert first is not None and why is None and first.trial
    second, why = st.try_admit("nvfp4@sparks", "sparks", now)
    assert second is None and "HALF_OPEN" in why, "exactly one HALF_OPEN trial"
    first.release()
    assert st.try_admit("nvfp4@sparks", "sparks", now)[0] is not None
    st.health["moe@ti"].h.state = core.DOWN
    assert st.try_admit("moe@ti", "ti", now) == (None, "DOWN")
    st.drained = {"rig": "lease"}
    adm, why = st.try_admit("unc@rig", "rig", now)
    assert adm is None and "drained" in why and st.node_inflight("rig") == 0


def test_an_admission_outlives_a_reload_that_drops_its_deployment():
    cfg = cfg_of()
    st = ready_state(cfg)
    hs = st.health["nvfp4@sparks"]
    for _ in range(5):
        hs.record_failure(0)
    adm = st.admit("nvfp4@sparks", "sparks", 31.0)           # the HALF_OPEN trial
    raw = base()
    raw["deployments"]["nvfp4b@sparks"] = raw["deployments"].pop("nvfp4@sparks")
    for r in raw["routes"].values():
        r["targets"] = [dict(t, d="nvfp4b@sparks") if t["d"] == "nvfp4@sparks" else t for t in r["targets"]]
    st.adopt(cfg_of(raw))
    assert "nvfp4@sparks" not in st.health
    adm.hs.record_success(32.0)                              # no KeyError: the admission kept its state
    adm.release()
    assert st.node_inflight("sparks") == 0 and adm.hs.h.trial_inflight == 0
    orphan = st.admit("nvfp4@sparks", "sparks", 33.0)         # planned before the reload, admitted after
    orphan.release()
    assert st.node_inflight("sparks") == 0


def test_auth_and_mismatch_transitions():
    hs = _hs()
    hs.on_probe("ready", 0)
    hs.on_probe("ready", 1)
    assert hs.on_models("auth", 2) == core.AUTH_FAILED
    assert hs.on_probe("ready", 3) == core.AUTH_FAILED      # health alone cannot clear it
    assert hs.on_probe("loading", 4) == core.AUTH_FAILED
    hs.on_probe("ready", 5)                                 # models are only checked after a ready probe
    assert hs.on_models("ok", 5) == core.READY
    assert hs.on_models("mismatch", 6, "x not listed") == core.MISMATCH
    assert "MISMATCH" in hs.admit_reason(6)
    assert hs.on_models("error", 7) == core.MISMATCH        # no information, no change
    hs.request_state("auth", 8, "401")
    assert hs.h.state == core.AUTH_FAILED


def test_effective_ttft():
    n = core.Node(id="n", url="http://x:1", engine="vllm", ttft_timeout_s=60)
    assert core.effective_ttft(n, 100000, 580) == 60
    f = core.Node(id="n", url="http://x:1", engine="vllm", ttft_timeout_s=60, prefill_tps_floor=1000)
    assert core.effective_ttft(f, 100000, 580) == 160
    assert core.effective_ttft(f, 10_000_000, 580) == 580


# ───────────────────────────── body + wire ─────────────────────────────

BODIES = [
    {"model": "beast", "messages": [{"role": "user", "content": "hi"}]},
    {"model": "beast", "temperature": 0.3, "top_p": 0.9, "top_k": 20, "min_p": 0.0, "seed": 7,
     "presence_penalty": 1.5, "messages": []},
    {"model": "beast", "chat_template_kwargs": {"enable_thinking": False}, "messages": []},
    {"model": "beast", "reasoning_budget_tokens": 512, "messages": []},
    {"model": "beast", "stream": True, "stream_options": {"include_usage": True}, "messages": []},
    {"model": "beast", "max_tokens": 9, "stop": ["\n"], "n": 1, "logprobs": True, "top_logprobs": 3},
    {"model": "beast", "tools": [{"type": "function", "function": {"name": "bash", "parameters": {}}}],
     "tool_choice": "auto", "parallel_tool_calls": False, "messages": []},
    {"model": "beast", "messages": [{"role": "user", "content": [{"type": "image_url",
                                                                  "image_url": {"url": "data:image/png;base64,AA"}}]}]},
    {"model": "beast", "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}},
    {"model": "beast", "grammar": "root ::= \"a\"", "messages": []},
    {"model": "beast", "cache_prompt": True, "samplers": ["top_k"], "messages": []},
    {"model": "beast", "messages": [{"role": "user", "content": "ünïcødé ☃"}], "user": "u1"},
]


@pytest.mark.parametrize("i", range(len(BODIES)))
@pytest.mark.parametrize("dep", ["unc@rig", "nvfp4@sparks", "moe@ti"])
def test_body_fidelity(i, dep):
    cfg = cfg_of()
    d = cfg.deployments[dep]
    body = copy.deepcopy(BODIES[i])
    before = copy.deepcopy(body)
    out, edits = core.forward_body(body, d, cfg.nodes[d.node])
    assert body == before, "the caller's body object must not be mutated"
    assert out["model"] == d.upstream and edits == ["model"]
    assert {k: v for k, v in out.items() if k != "model"} == {k: v for k, v in before.items() if k != "model"}


def test_id_slot_rule():
    cfg = cfg_of()
    rig, ti, sp = (cfg.deployments[x] for x in ("unc@rig", "moe@ti", "nvfp4@sparks"))
    out, e = core.forward_body({"model": "qwen-unc", "id_slot": 0}, rig, cfg.nodes["rig"])
    assert out["id_slot"] == 0 and e == []
    out, e = core.forward_body({"model": "moe", "id_slot": 1}, ti, cfg.nodes["ti"])
    assert out["id_slot"] == 1
    out, e = core.forward_body({"model": "qwen-unc", "id_slot": 1}, rig, cfg.nodes["rig"])  # rig has 1 slot
    assert "id_slot" not in out and e == ["id_slot"]
    out, e = core.forward_body({"model": "x", "id_slot": 0}, sp, cfg.nodes["sparks"])
    assert "id_slot" not in out and e == ["model", "id_slot"]
    out, e = core.forward_body({"model": "moe", "id_slot": True}, ti, cfg.nodes["ti"])
    assert "id_slot" not in out


def test_sse_error_event_shape():
    ev = core.sse_error_event("sparks", "nvfp4@sparks", "rid1")
    assert ev.startswith(b"data: ") and ev.endswith(b"\n\n") and b"[DONE]" not in ev
    doc = json.loads(ev[6:])
    assert doc["error"]["type"] == "hydra_upstream_error"
    assert doc["error"]["code"] == "upstream_failed_midstream"
    assert doc["error"]["hydra_deployment"] == "nvfp4@sparks" and doc["error"]["request_id"] == "rid1"


def test_host_class():
    assert core.host_class("http://127.0.0.1:8080")[1] == "loopback"
    assert core.host_class("http://localhost:8080")[1] == "loopback"
    assert core.host_class("http://[::1]:8080")[1] == "loopback"
    assert core.host_class("http://100.101.102.103:8000")[1] == "tailnet"
    assert core.host_class("http://spark-a.tail1234.ts.net:8000")[1] == "tailnet"
    assert core.host_class("http://192.168.1.4:8000")[1] == "private"
    assert core.host_class("http://example.com:80")[1] == "public"
    assert core.host_class("ftp://10.0.0.1:1")[1] == "invalid"
    assert core.host_class("http://10.0.0.1:1/v1")[1] == "invalid"


def test_unknown_served_id_is_never_judged_but_a_known_one_is():
    raw = core.implicit_raw({})
    assert raw["deployments"]["local@rig"]["verify_upstream"] is False
    cfg = core.validate(raw, {})              # the file --print-default-config writes
    assert not cfg.deployments["local@rig"].verify_upstream
    assert any("verify_upstream = false" in w for w in cfg.warnings), "a disabled check must be visible"
    named = core.implicit_raw({"INFERENCE_MODEL": "qwen38-27b-uncensored-mtp-q5"})
    assert "verify_upstream" not in named["deployments"]["local@rig"]
    assert core.validate(named, {}).deployments["local@rig"].verify_upstream
    assert not core.implicit_config({}).warnings


def test_a_stale_models_ok_does_not_clear_a_newer_auth_failure():
    """A /v1/models check SENT before a request saw a 401 must not re-admit
    the node when its 200 lands afterwards (it did: a boot-time check cleared
    a fresh AUTH_FAILED). A check sent after the failure still recovers."""
    hs = _hs()
    hs.on_probe("ready", 1.0)
    hs.on_probe("ready", 2.0)
    hs.request_state("auth", 5.0, "HTTP 401 on a request")
    assert hs.h.state == core.AUTH_FAILED
    hs.on_models("ok", 6.0, started=4.0)          # in flight before the 401
    assert hs.h.state == core.AUTH_FAILED
    hs.on_models("ok", 7.0, started=5.5)          # sent after it: real recovery
    assert hs.h.state == core.READY
