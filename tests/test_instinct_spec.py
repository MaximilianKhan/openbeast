#!/usr/bin/env python3
"""Decision spec + binding config validation (plan §5.13 test_instinct_spec).
An invalid file invalidates ONLY its own decision / binding."""
from __future__ import annotations

import pytest

import _instinct_helpers as H
from instinct.config import ConfigError, effective_chain, is_local_host, load_config
from instinct.service import Instinct
from instinct.spec import SpecError, load_registry, load_spec, parse_constraint

GOOD = H.spec_text("router.spawn_intent")


def test_shipped_specs_load():
    specs, errors = load_registry(H.DECISIONS)
    assert errors == {}
    assert set(specs) == {"router.spawn_intent", "hydra.task_class", "hydra.pool_fit"}
    s = specs["router.spawn_intent"]
    assert s.policy.act == {"inline": 0.90}          # skip-only (I4)
    assert s.policy.mode == "shadow"
    assert s.chain == ["rig-27b", "rig-cpu", "linear", "rules"]   # a FULL model first
    assert s.policy.primary_use == "substitute"
    assert s.policy.substitutes == "agents/router.py:_classify"
    t = specs["hydra.task_class"]
    assert t.mechanical == ["vision", "long_context"]
    assert [lb.text for lb in t.labels] == ["A", "B", "C", "D", "E"]
    assert specs["hydra.pool_fit"].policy.mode == "off"


def _write(tmp_path, did, text):
    (tmp_path / f"{did}.toml").write_text(text)


@pytest.mark.parametrize("bad,needle", [
    (GOOD.replace('owner       = "agents/router.py"',
                  'owner       = "agents/router.py"\ncolour = "red"'), "unknown key"),
    (GOOD.replace('act              = { inline = 0.90 }', ''), "policy"),
    (GOOD.replace('act              = { inline = 0.90 }',
                  'act              = { maybe = 0.90 }'), "not a label"),
    (GOOD.replace('truncate   = "head_tail:200:1200"', 'truncate   = "tail:5"'), "head_tail"),
    (GOOD.replace('{user_turn}\n</user_turn>', 'x\n</user_turn>'), "slots"),
    (GOOD.replace('text = "no"', 'text = "yes"'), "duplicate text"),
    (GOOD.replace('type        = "yes_no"', 'type        = "classify"'), "reserved"),
    (GOOD.replace('metric = "ece"', 'metric = "vibes"'), "unknown metric"),
    (GOOD.replace('"act_errors[inline]@test+ood+adversarial == 0"',
                  '"act_errors[inline]@test ~ 0"'), "cannot parse"),
    (GOOD.replace('mode             = "shadow"', 'mode             = "yolo"'), "policy.mode"),
    (GOOD.replace('text = "yes"', 'text = " yes"'), "whitespace"),
])
def test_invalid_spec_isolated(tmp_path, bad, needle):
    _write(tmp_path, "router.spawn_intent", bad)
    _write(tmp_path, "hydra.task_class", H.spec_text("hydra.task_class"))
    specs, errors = load_registry(tmp_path)
    assert "router.spawn_intent" in errors and needle in errors["router.spawn_intent"]
    # control: the sibling still serves
    assert "hydra.task_class" in specs and "hydra.task_class" not in errors


def test_id_must_match_filename(tmp_path):
    _write(tmp_path, "router.other", GOOD)
    specs, errors = load_registry(tmp_path)
    assert "router.other" in errors and "filename" in errors["router.other"]


def test_rank_needs_item_slot(tmp_path):
    txt = H.spec_text("hydra.pool_fit").replace("<pool>\n{item}\n</pool>", "<pool>\n</pool>")
    _write(tmp_path, "hydra.pool_fit", txt)
    _, errors = load_registry(tmp_path)
    assert "{item}" in errors["hydra.pool_fit"]


def test_constraint_parser():
    c = parse_constraint("act_errors[inline]@test+ood+adversarial == 0", ["spawn", "inline"])
    assert (c.metric, c.label, c.splits, c.op, c.value) == (
        "act_errors", "inline", ("test", "ood", "adversarial"), "==", 0.0)
    with pytest.raises(SpecError):
        parse_constraint("act_errors@test == 0", ["spawn", "inline"])     # needs a label
    with pytest.raises(SpecError):
        parse_constraint("__import__('os')@test == 0", ["a", "b"])        # no eval, ever


# --- binding config ------------------------------------------------------------

def _cfg(tmp_path, engines, env=None, service=None):
    p = H.write_config(tmp_path, engines, decisions=["router.spawn_intent"], service=service)
    return load_config(p, env=env or {})


def test_unknown_binding_key_refuses_only_that_binding(tmp_path):
    cfg = _cfg(tmp_path, {"a": H.llama_binding("http://127.0.0.1:1", colour="red"),
                          "b": H.llama_binding("http://127.0.0.1:2")})
    assert "a" in cfg.engine_errors and "unknown key" in cfg.engine_errors["a"]
    assert "b" in cfg.engines


def test_primary_url_refused_unless_allowed(tmp_path):
    env = {"INFERENCE_URL": "http://localhost:59999"}
    cfg = _cfg(tmp_path, {"p": H.llama_binding("http://127.0.0.1:59999")}, env)
    assert "p" in cfg.engine_errors and "INFERENCE_URL" in cfg.engine_errors["p"]
    cfg = _cfg(tmp_path, {"p": H.llama_binding("http://127.0.0.1:59999", allow_primary=True)},
               env)
    assert "p" in cfg.engines   # control


@pytest.mark.parametrize("url", ["http://127.0.0.1:8095", "http://localhost:8443",
                                 "http://127.0.0.1:8088"])
def test_engine_never_routes_through_hydra_gate_or_router(tmp_path, url):
    """I7: instinct's engine traffic never goes through hydra or beast-gate."""
    cfg = _cfg(tmp_path, {"x": H.llama_binding(url)})
    assert "x" in cfg.engine_errors and "I7" in cfg.engine_errors["x"]


def test_the_gate_on_its_own_port_is_refused(tmp_path):
    """beast-gate listens on EDGE_PORT (8090); :8443 is only its tailnet
    front. An engine at 127.0.0.1:8090 went through the gate's audit and
    caps and passed the lint."""
    cfg = _cfg(tmp_path, {"x": H.llama_binding("http://127.0.0.1:8090")}, env={})
    assert "x" in cfg.engine_errors and "beast-gate" in cfg.engine_errors["x"]


@pytest.mark.parametrize("key,port,who", [
    ("OPENBEAST_EDGE_PORT", 8191, "beast-gate"), ("EDGE_PORT", 8192, "beast-gate"),
    ("OPENBEAST_ROUTER_PORT", 8098, "agent router"), ("ROUTER_PORT", 8099, "agent router"),
    ("HYDRA_PORT", 8196, "hydra"), ("OPENBEAST_HYDRA_PORT", 8197, "hydra")])
def test_a_moved_gate_router_or_hydra_port_is_still_refused(tmp_path, key, port, who):
    url = f"http://127.0.0.1:{port}"
    cfg = _cfg(tmp_path, {"x": H.llama_binding(url)}, env={key: str(port)})
    assert "x" in cfg.engine_errors and who in cfg.engine_errors["x"], cfg.engine_errors
    cfg = _cfg(tmp_path, {"x": H.llama_binding(url)}, env={})
    assert "x" in cfg.engines                                     # control: nothing lives there


def test_start_hands_instinct_the_live_router_and_gate_ports():
    """conf.sh's ROUTER_PORT is a plain shell variable (never exported), so
    unless start.sh passes it, a moved router port never reaches the lint."""
    src = (H.REPO / "start.sh").read_text()
    launch = src[src.index('Starting beast-instinct'):]
    launch = launch[:launch.index('instinct.sh" up')]
    assert 'OPENBEAST_ROUTER_PORT="${ROUTER_PORT' in launch
    assert 'OPENBEAST_EDGE_PORT="${EDGE_PORT' in launch


@pytest.mark.parametrize("url", [
    "http://127.0.0.2:8443", "http://[::ffff:127.0.0.1]:8095", "http://127.1:8095",
    "http://localhost.:8443", "http://0x7f.1:8088", "http://2130706433:8095",
    "http://100.64.0.7:8443",          # the rig's gate on its tailnet address is still the gate
])
def test_forbidden_ports_cannot_be_reached_by_another_spelling(tmp_path, url):
    cfg = _cfg(tmp_path, {"x": H.llama_binding(url)})
    assert "x" in cfg.engine_errors and "I7" in cfg.engine_errors["x"]


@pytest.mark.parametrize("host,local", [
    ("127.0.0.1", True), ("127.9.9.9", True), ("127.1", True), ("localhost.", True),
    ("::1", True), ("::ffff:127.0.0.1", True), ("0.0.0.0", True), ("a.localhost", True),
    ("10.0.0.5", False), ("example.com", False), ("::ffff:10.0.0.1", False)])
def test_is_local_host(host, local):
    assert is_local_host(host) is local


def test_primary_is_linted_without_any_env(tmp_path):
    """instinct.sh never sources conf.sh: the :8080 default must still hold."""
    for url in ("http://127.0.0.1:8080", "http://localhost:8080", "http://127.1:8080"):
        cfg = _cfg(tmp_path, {"p": H.llama_binding(url)}, env={})
        assert "INFERENCE_URL" in cfg.engine_errors.get("p", ""), url
    cfg = _cfg(tmp_path, {"p": H.llama_binding("http://127.0.0.1:8082")}, env={})
    assert "p" in cfg.engines   # control: the scorer port is fine


@pytest.mark.parametrize("val", ["no", "false", 0, 1])
def test_allow_primary_must_be_a_real_bool(tmp_path, val):
    env = {"INFERENCE_URL": "http://127.0.0.1:59999"}
    cfg = _cfg(tmp_path, {"p": H.llama_binding("http://127.0.0.1:59999", allow_primary=val)},
               env)
    assert "p" not in cfg.engines and "bool" in cfg.engine_errors["p"]


def test_sis_url_is_linted_and_pinned_to_the_engine_host(tmp_path):
    ok = H.sglang_binding("http://127.0.0.1:30010", sis_url="http://localhost:30011")
    cfg = _cfg(tmp_path, {"s": ok})
    assert "s" in cfg.engines   # control: same host, another port
    for bad, why in (("http://evil.example:30011", "same scheme and host"),
                     ("http://127.0.0.1:8443", "I7"),
                     ("http://127.0.0.1:8080", "INFERENCE_URL"),
                     ("ftp://127.0.0.1:30011", "http(s)")):
        cfg = _cfg(tmp_path, {"s": H.sglang_binding("http://127.0.0.1:30010", sis_url=bad)})
        assert why in cfg.engine_errors.get("s", ""), bad


def test_sis_url_never_receives_the_key_off_host(tmp_path):
    """End to end: the only binding that could send the bearer key to a third
    party is refused before an engine is ever built."""
    cfg = _cfg(tmp_path, {"s": H.sglang_binding("http://127.0.0.1:30010",
                                                sis_url="http://evil.example/")})
    assert "s" not in cfg.engines


def test_hydra_url_env_refused(tmp_path):
    cfg = _cfg(tmp_path, {"x": H.llama_binding("http://10.0.0.5:9000")},
               {"HYDRA_URL": "http://10.0.0.5:9000/"})
    assert "x" in cfg.engine_errors
    cfg = _cfg(tmp_path, {"x": H.llama_binding("http://10.0.0.5:9001")},
               {"HYDRA_URL": "http://10.0.0.5:9000/"})
    assert "x" in cfg.engines   # control


def test_placeholder_pins_refused(tmp_path):
    cfg = _cfg(tmp_path, {"x": H.sglang_binding("http://127.0.0.1:30010",
                                                model_sha256="", model_revision="<hf sha>")})
    assert "placeholder" in cfg.engine_errors["x"]


def test_non_loopback_host_refused(tmp_path):
    with pytest.raises(ConfigError):
        _cfg(tmp_path, {}, service={"host": "0.0.0.0"})


def test_shipped_config_refuses_unpinned_sglang():
    cfg = load_config(env={})
    assert "rig-sglang" in cfg.engine_errors       # placeholders until R2
    assert cfg.engines["rig-cpu"].model_sha256.startswith("9465e63a")
    p = cfg.engines["rig-27b"]                     # the primary 27B, substitute-only
    assert p.allow_primary and p.busy_skip and p.key_env == "LLAMA_API_KEY"
    assert p.model_sha256.startswith("24780644")   # Qwen3.8-27B-Uncensored-Q5_K_M
    assert cfg.port == 8094 and cfg.host == "127.0.0.1"


def test_engine_override(tmp_path):
    cfg = _cfg(tmp_path, {"stub": H.llama_binding("http://127.0.0.1:1")},
               {"INSTINCT_ENGINE_OVERRIDE": "stub"})
    assert effective_chain(["linear", "rig-cpu", "rules"], cfg) == ["linear", "stub", "rules"]
    with pytest.raises(ConfigError):
        _cfg(tmp_path, {}, {"INSTINCT_ENGINE_OVERRIDE": "linear"})


def test_allow_primary_binding_only_for_primary_use_decisions(tmp_path):
    env = {"INFERENCE_URL": "http://127.0.0.1:59999"}
    none = (GOOD.replace('"rig-27b"', '"prim"')
            .replace('primary_use      = "substitute"', '')
            .replace('substitutes      = "agents/router.py:_classify"', ''))
    p = H.write_config(tmp_path, {"prim": H.llama_binding("http://127.0.0.1:59999",
                                                          allow_primary=True)},
                       extra_decisions={"router.spawn_intent": none})
    inst = Instinct(load_config(p, env=env))
    H.run(inst.reload())
    assert "prim" not in inst.chains["router.spawn_intent"]
    assert "primary_use is none" in inst.spec_errors["router.spawn_intent#engines"]


def test_load_spec_roundtrip():
    s = load_spec(H.DECISIONS / "hydra.task_class.toml")
    assert s.rule_set == "hydra_static" and s.rule_params["long_context_tokens"] == 32000
