"""Shared fixtures for the instinct tests: an in-process stub scorer, a
throwaway config/registry, and small async helpers. Every test builds its
own case (ephemeral ports, tmp dirs) — nothing here touches .run/ or a real
engine port."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agents"))
sys.path.insert(0, str(REPO / "scripts" / "instinct"))
sys.path.insert(0, str(REPO / "evals" / "decisions"))

import stub_scorer  # noqa: E402

DECISIONS = REPO / "agents" / "instinct" / "decisions"


@contextlib.contextmanager
def stub_server(faults: dict | None = None, call_log: str | None = None):
    srv, stub = stub_scorer.serve("127.0.0.1", 0, faults or {}, call_log)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", stub
    finally:
        srv.shutdown()
        srv.server_close()


def read_calls(path) -> list[dict]:
    try:
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except FileNotFoundError:
        return []


def write_config(tmp: Path, engines: dict[str, dict], *, decisions: list[str] | None = None,
                 extra_decisions: dict[str, str] | None = None, service: dict | None = None,
                 records_dir: Path | None = None) -> Path:
    """A self-contained instinct.toml under tmp. `decisions` copies repo specs
    (by id); `extra_decisions` writes raw TOML text by id."""
    ddir = tmp / "decisions"
    ddir.mkdir(parents=True, exist_ok=True)
    for did in decisions or []:
        shutil.copy(DECISIONS / f"{did}.toml", ddir / f"{did}.toml")
    for did, text in (extra_decisions or {}).items():
        (ddir / f"{did}.toml").write_text(text)
    rec = records_dir or (tmp / "records")
    rec.mkdir(parents=True, exist_ok=True)
    svc = {"host": "127.0.0.1", "port": 0, "key_file": str(tmp / "instinct.key"),
           "ledger_dir": str(tmp / "ledger"), "decisions_dir": str(ddir),
           "records_dir": str(rec), "probe_interval_s": 0,
           "require_committed_gate": False}
    svc.update(service or {})
    lines = ["[service]"]
    for k, v in svc.items():
        lines.append(f"{k} = {json.dumps(v)}")
    for name, b in engines.items():
        lines.append(f'\n[engines."{name}"]')
        for k, v in b.items():
            lines.append(f"{k} = {json.dumps(v)}")
    p = tmp / "instinct.toml"
    p.write_text("\n".join(lines) + "\n")
    key = tmp / "instinct.key"
    if not key.exists():
        fd = os.open(key, os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, b"k" * 40)
        os.close(fd)
    return p


def llama_binding(url: str, **kw) -> dict:
    b = {"adapter": "llamacpp_logprobs", "url": url, "model": "stub-lexicon",
         "model_sha256": "stub", "n_probs": 20, "timeout_ms": 1500}
    b.update(kw)
    return b


def sglang_binding(url: str, **kw) -> dict:
    b = {"adapter": "sglang_score", "url": url, "model": "stub-lexicon",
         "model_sha256": "stub", "exec": "mis", "timeout_ms": 1500}
    b.update(kw)
    return b


SPAWN_TRAIN = [
    ("spawn a background agent to refactor the parser and report back", "spawn"),
    ("launch an agent in the background to port the module, check back later", "spawn"),
    ("kick off an autonomous agent to audit the repo while we talk", "spawn"),
    ("have a background agent migrate every test, don't block me", "spawn"),
    ("what does this function do", "inline"),
    ("fix the typo in the readme", "inline"),
    ("explain how decorators work", "inline"),
    ("what is 17 times 23", "inline"),
]


def promote_linear(tmp: Path, did: str = "router.spawn_intent", *, rows=None, T: float = 1.0,
                   thresholds: dict | None = None, mode: str = "enforce",
                   gate_passed: bool = True, gate_hash: str | None = None,
                   extra_engines: dict | None = None, chain: str = '["linear", "rules"]',
                   service: dict | None = None):
    """Build the full promotion path for the `linear` engine in tmp: a fitted
    model, a calibration record and a gate record keyed by the real
    decision_hash, and the spec at `mode`. Returns (config_path, dhash)."""
    from instinct import calibrate as C
    from instinct.config import load_config
    from instinct.engines import linear as L
    from instinct.spec import decision_hash, load_spec
    text = spec_text(did)
    text = text.replace('mode             = "shadow"', f'mode             = "{mode}"')
    text = text.replace('mode           = "shadow"', f'mode           = "{mode}"')
    import re as _re
    text = _re.sub(r'chain = \[.*?\]', f"chain = {chain}", text, count=1)
    cfgp = write_config(tmp, extra_engines or {}, extra_decisions={did: text}, service=service)
    cfg = load_config(cfgp, env={})
    spec = load_spec(Path(cfg.decisions_dir) / f"{did}.toml")
    train = rows or [{"input": {"user_turn": t}, "label": y} for t, y in SPAWN_TRAIN]
    model = L.fit(spec, train)
    h = decision_hash(spec, cfg.engines["linear"].hash_identity(), None)
    mpath = C.linear_model_path(cfg.records_dir, did, h)
    C.write_record(mpath, model)
    cpath = C.calib_path(cfg.records_dir, did, h)
    C.write_record(cpath, {"decision": did, "decision_hash": h, "engine": "linear", "T": T,
                           "thresholds": thresholds if thresholds is not None else
                           {k: 0.6 for k in spec.policy.act},
                           "model_file_sha256": C.file_sha256(mpath)})
    gh = gate_hash or h
    C.write_record(C.gate_path(cfg.records_dir, did, h),
                   {"decision": did, "decision_hash": gh, "passed": gate_passed,
                    "calib_sha256": C.file_sha256(cpath)})
    return cfgp, h


def run(coro):
    return asyncio.run(coro)


def spec_text(did: str) -> str:
    return (DECISIONS / f"{did}.toml").read_text()
