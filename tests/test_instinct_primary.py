#!/usr/bin/env python3
"""The 27B decision engine (plan revision 2026-09-30): the rig's own primary
as a SUBSTITUTE engine for router.spawn_intent, the primary_use rules that
keep it honest, the /slots busy skip and LLAMA_API_KEY via key_env.
Hermetic: the stub scorer on an ephemeral port plays the primary
(INFERENCE_URL points at it) — nothing touches :8080.
The Open-Jev-27B head adapter is tested in test_instinct_openjev.py."""
from __future__ import annotations

import os

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
