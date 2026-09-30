#!/usr/bin/env python3
"""scripts/serve-openjev.sh + scripts/instinct/openjev_gate.py — the LOCAL
Open-Jev-27B host. Hermetic: the launcher is driven only into its refusal and
--dry-run paths with a fake checkpoint (no docker, no GPU, no weights); the
gate fronts the stub scorer's Open-Jev endpoints on ephemeral ports."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import threading

import pytest

import _instinct_helpers as H
import openjev_gate
from instinct import calibrate as C
from instinct.config import load_config
from instinct.engines import ScoreReq, build_engine
from instinct.service import Instinct
from instinct.spec import load_spec

SH = H.REPO / "scripts" / "serve-openjev.sh"
PIN = "sha256:" + "a" * 64
DID = "router.spawn_intent"
GOOD = H.spec_text(DID)


async def _decide(inst, text, **kw):
    return await inst.decide({"contract": "instinct/1", "decision": DID,
                              "inputs": {"user_turn": text}, **kw})


def fake_checkpoint(tmp_path):
    ck = tmp_path / "pkg" / "checkpoint"
    (ck / "adapter").mkdir(parents=True)
    (ck / "adapter" / "adapter_model.safetensors").write_bytes(b"lora")
    (ck / "adapter" / "adapter_config.json").write_text("{}")
    (ck / "head.pt").write_bytes(b"head")
    (ck / "model.json").write_text(json.dumps({"model_id": "Qwen/Qwen3.8-27B",
                                               "revision": "1d4bf0f2", "max_length": 4096}))
    (ck / "temperature.json").write_text('{"temperature": 2.53}')
    hf = tmp_path / "hf"
    hf.mkdir()
    return ck, hf


def env_for(tmp_path, **extra):
    ck, hf = fake_checkpoint(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for tool in ("docker", "pgrep"):       # nothing real is ever started or inspected
        (bindir / tool).write_text("#!/bin/bash\nexit 1\n")
        (bindir / tool).chmod(0o755)
    e = {k: v for k, v in os.environ.items() if not k.startswith("OPENJEV")}
    e.update({"PATH": f"{bindir}:{os.environ['PATH']}",
              "OPENJEV_IMAGE": f"openbeast-openjev@{PIN}",
              "OPENJEV_CHECKPOINT_DIR": str(ck), "OPENJEV_HF_CACHE": str(hf),
              "OPENJEV_RUN_DIR": str(tmp_path / "run"),
              "OPENJEV_ADAPTER_SHA256": hashlib.sha256(b"lora").hexdigest(),
              "OPENJEV_HEAD_SHA256": hashlib.sha256(b"head").hexdigest()})
    e.update(extra)
    return e


def sh(*args, env):
    return subprocess.run(["bash", str(SH), *args], env=env, capture_output=True, text=True,
                          timeout=60)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_script_parses_and_documents_the_contract():
    assert subprocess.run(["bash", "-n", str(SH)]).returncode == 0
    r = subprocess.run(["bash", str(SH), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    for needle in ("DEDICATED GPU host", "Never TypeSafe's hosted Jev API", "READ-ONLY",
                   "HF_HUB_OFFLINE=1", "UNCENSORED", "validation-only"):
        assert needle in r.stdout, needle


def test_dry_run_plans_a_pinned_offline_readonly_container(tmp_path):
    env = env_for(tmp_path)
    r = sh("up", "--dry-run", "--port", str(free_port()), env=env)
    assert r.returncode == 0, r.stderr
    run = [ln for ln in r.stdout.splitlines() if ln.startswith("container: ")][0]
    for needle in (f"@{PIN}", "HF_HUB_OFFLINE=1", "-p 127.0.0.1:", ":/hf:ro", ":/ckpt-src:ro",
                   ":/ckpt:ro", "--read-only", "--no-prefix-cache", "--gpus device=0"):
        assert needle in run, needle
    key = (tmp_path / "run" / "openjev.key").read_text().strip()
    assert oct(os.stat(tmp_path / "run" / "openjev.key").st_mode & 0o777) == "0o600"
    assert key not in r.stdout                                   # never on argv
    derived = json.loads((tmp_path / "run" / "openjev" / "checkpoint-uncensored" /
                          "model.json").read_text())
    assert derived["model_id"] == "JonathanColetti/Qwen3.8-27B-Uncensored"
    assert derived["max_length"] == 4096                         # the rest is the package's
    ident = json.loads((tmp_path / "run" / "openjev" / "identity.json").read_text())
    assert ident["base"] == "uncensored" and ident["loader_digest"] == PIN
    assert ident["adapter_revision"] == "28cf73067d5b337860bbef3c85b8b82ba8730956"
    # the package itself is untouched
    assert json.loads(
        (tmp_path / "pkg" / "checkpoint" / "model.json").read_text())["model_id"] == \
        "Qwen/Qwen3.8-27B"


@pytest.mark.parametrize("extra_args,extra_env,why", [
    ((), {"OPENJEV_IMAGE": "openbeast-openjev:latest"}, "must be pinned"),
    (("--port", "8080"), {}, "belongs to the stack"),
    (("--port", "8094"), {}, "belongs to the stack"),
    (("--bind", "0.0.0.0"), {}, "every interface"),
    (("--base", "stock"), {}, "validation-only"),
    (("--base", "stock", "--validation-only", "--bind", "100.64.0.9"), {}, "loopback"),
    ((), {"OPENJEV_HEAD_SHA256": "b" * 64}, "head sha256"),
    ((), {"OPENJEV_CHECKPOINT_DIR": "/nonexistent"}, "not a directory"),
])
def test_refusals(tmp_path, extra_args, extra_env, why):
    env = env_for(tmp_path, **extra_env)
    r = sh("up", "--dry-run", *extra_args, env=env)
    assert r.returncode == 2 and why in r.stderr, r.stderr


def test_refuses_a_host_running_llama_server(tmp_path):
    env = env_for(tmp_path)
    pg = tmp_path / "bin" / "pgrep"
    pg.write_text('#!/bin/bash\n[[ "$*" == *llama-server* ]] && exit 0\nexit 1\n')
    r = sh("up", "--dry-run", env=env)
    assert r.returncode == 2 and "GPU of its own" in r.stderr
    env["OPENJEV_ALLOW_SHARED_HOST"] = "1"                       # an explicit override
    assert sh("up", "--dry-run", env=env).returncode == 0


def test_stock_base_for_validation_only(tmp_path):
    r = sh("up", "--dry-run", "--base", "stock", "--validation-only", env=env_for(tmp_path))
    assert r.returncode == 0 and "Qwen/Qwen3.8-27B@1d4bf0f2" in r.stdout
    ident = json.loads((tmp_path / "run" / "openjev" / "identity.json").read_text())
    assert ident["validation_only"] is True


# ─── the gate, end to end with the openjev_head adapter ────────────────────────

@pytest.fixture
def gate(tmp_path):
    key = tmp_path / "openjev.key"
    fd = os.open(key, os.O_WRONLY | os.O_CREAT, 0o600)
    os.write(fd, b"g" * 40)
    os.close(fd)
    with H.stub_server() as (up, _):
        ident = {"model": "stub-lexicon", "base_revision": "stub-base",
                 "adapter_revision": "stub-adapter", "head_sha256": "stub-head",
                 "loader_digest": PIN}
        srv = openjev_gate.serve("127.0.0.1", 0, openjev_gate.read_key(str(key)), up, ident)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            yield f"http://127.0.0.1:{srv.server_address[1]}", key
        finally:
            srv.shutdown()
            srv.server_close()


def _get(url, path, key=None, method="GET", body=None):
    import urllib.error
    import urllib.request
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def test_gate_needs_the_key_and_allows_only_three_routes(gate):
    url, keyf = gate
    key = keyf.read_text()
    assert _get(url, "/health")[0] == 200                         # no key needed
    assert _get(url, "/v1/identity")[0] == 401
    code, ident = _get(url, "/v1/identity", key)
    assert code == 200 and ident["head_sha256"] == "stub-head"
    body = json.dumps({"state": "x", "questions": {"d": {"type": "noul",
                                                          "instructions": "?"}}}).encode()
    assert _get(url, "/v1/systemone", None, "POST", body)[0] == 401
    assert _get(url, "/v1/systemone", key, "POST", body)[0] == 200
    for path in ("/v1/inference", "/api/jev", "/examples.json"):   # the loader's other doors
        assert _get(url, path, key, "POST" if path != "/examples.json" else "GET",
                    body)[0] == 404, path


def test_gate_refuses_a_readable_key_and_every_interface(tmp_path):
    k = tmp_path / "k"
    k.write_text("g" * 40)
    k.chmod(0o644)
    with pytest.raises(PermissionError):
        openjev_gate.read_key(str(k))
    with pytest.raises(SystemExit):
        openjev_gate.serve("0.0.0.0", 0, "g" * 40, "http://127.0.0.1:1", {})


def test_openjev_binding_scores_through_the_gate(tmp_path, gate):
    url, keyf = gate
    cfg = load_config(H.write_config(tmp_path, {"j": {
        "adapter": "openjev_head", "url": url, "key_file": str(keyf), "model": "stub-lexicon",
        "model_revision": "stub-base", "adapter_revision": "stub-adapter",
        "head_sha256": "stub-head", "loader_digest": PIN, "timeout_ms": 2000}}), env={})
    spec = load_spec(H.DECISIONS / "router.spawn_intent.toml")
    eng = build_engine(cfg.engines["j"])

    async def go():
        probe = await eng.probe([spec])
        lk = (await eng.attach([spec]))[spec.id]
        res = await eng.score(ScoreReq(spec=spec, inputs={
            "user_turn": "spawn a background agent to port it and report back"},
            label_ids=lk.ids, deadline_s=2.0))
        await eng.aclose()
        return probe, res
    probe, res = H.run(go())
    assert probe.ok, probe.reason
    assert res.rows[0].q["spawn"] > 0.9


def _cfg(tmp_path, engines, env=None):
    return load_config(H.write_config(tmp_path, engines), env=env or {})


# ─── the openjev_head adapter against the stub's Open-Jev endpoints ────────────

def _openjev(url, **kw):
    b = {"adapter": "openjev_head", "url": url, "model": "stub-lexicon",
         "model_revision": "stub-base", "adapter_revision": "stub-adapter",
         "head_sha256": "stub-head", "timeout_ms": 1500}
    b.update(kw)
    return b


def test_openjev_binding_needs_its_pins(tmp_path):
    cfg = _cfg(tmp_path, {"j": {k: v for k, v in _openjev("http://127.0.0.1:1").items()
                                if k != "head_sha256"}})
    assert "head_sha256" in cfg.engine_errors["j"]
    cfg = _cfg(tmp_path, {"j": _openjev("http://127.0.0.1:1", head_sha256="<sha>")})
    assert "placeholder" in cfg.engine_errors["j"]
    cfg = _cfg(tmp_path, {"j": _openjev("http://127.0.0.1:1")})
    assert "j" in cfg.engines
    ident = cfg.engines["j"].hash_identity()
    assert ident["adapter_revision"] == "stub-adapter" and ident["head_sha256"] == "stub-head"


def test_openjev_scores_yes_no_through_systemone(tmp_path):
    log = tmp_path / "calls.jsonl"
    spec = load_spec(H.DECISIONS / f"{DID}.toml")
    with H.stub_server(call_log=str(log)) as (url, _):
        cfg = _cfg(tmp_path, {"j": _openjev(url)})
        eng = build_engine(cfg.engines["j"])

        async def go():
            locks = await eng.attach([spec])
            lk = locks[DID]
            r1 = await eng.score(ScoreReq(spec=spec, inputs={
                "user_turn": "spawn a background agent to port it and report back"},
                label_ids=lk.ids, deadline_s=1.0))
            r2 = await eng.score(ScoreReq(spec=spec, inputs={"user_turn": "what is 2+2?"},
                                          label_ids=lk.ids, deadline_s=1.0))
            probe = await eng.probe([spec])
            await eng.aclose()
            return lk, r1, r2, probe
        lk, r1, r2, probe = H.run(go())
    assert lk.ok and lk.ids == {"spawn": 1, "inline": 0}
    assert r1.rows[0].q["spawn"] > 0.9 and r2.rows[0].q["inline"] > 0.5
    assert r1.rows[0].label_mass is None                        # a head has no vocabulary
    assert probe.ok, probe.reason
    body = [c for c in H.read_calls(log) if c["path"] == "/v1/systemone"][0]["body"]
    q = body["questions"]["d"]
    assert q["type"] == "noul" and q["instructions"] == spec.prompt_system
    assert "<user_turn>" in body["state"] and "<|im_start|>" not in body["state"]


def test_openjev_refuses_rank_and_a_foreign_head(tmp_path):
    pool = load_spec(H.DECISIONS / "hydra.pool_fit.toml")
    with H.stub_server() as (url, _):
        cfg = _cfg(tmp_path, {"j": _openjev(url, head_sha256="another-head")})
        eng = build_engine(cfg.engines["j"])

        async def go():
            locks = await eng.attach([pool])
            probe = await eng.probe([])
            await eng.aclose()
            return locks, probe
        locks, probe = H.run(go())
    assert not locks[pool.id].ok and "label_lock_failed" in locks[pool.id].reason
    assert not probe.ok and "identity" in probe.reason          # a different head


def test_openjev_decision_is_uncalibrated_until_validated(tmp_path):
    """Through the service: an openjev engine can shadow at once, but with no
    calibration + committed gate for its exact hash it can never enforce."""
    text = GOOD.replace('chain = ["rig-27b", "rig-cpu", "linear", "rules"]',
                        'chain = ["j", "rules"]').replace(
        'mode             = "shadow"', 'mode             = "enforce"')
    with H.stub_server() as (url, _):
        p = H.write_config(tmp_path, {"j": _openjev(url)}, extra_decisions={DID: text})
        cfg = load_config(p, env={})

        async def go():
            inst = Instinct(cfg, repo_root=tmp_path)
            await inst.start()
            r = await _decide(inst, "what is 2+2?", baseline="hint")
            h = inst.hashes[(DID, "j")]
            await inst.aclose()
            return r, h
        r, h = H.run(go())
    assert r["cascade"][0]["engine"] == "j" and r["enforce"] is False
    assert r["mode"] == "shadow" and h is not None
    assert not os.path.exists(C.calib_path(cfg.records_dir, DID, h))
