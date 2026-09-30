#!/usr/bin/env python3
"""Service-level regressions for the 2026-09-29 double-pass review of
beast-instinct (ids like A-instinct-6 name the finding): the shadow/enforce
concurrency pools, a reload during a call, an uncalibrated rank. Each test
builds its own case — stub scorer on an ephemeral port, tmp registry — and
fails on the code before its fix."""
from __future__ import annotations

import asyncio
import json
import re
import shutil

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


def test_shadow_work_never_queues_in_front_of_an_enforce_call(tmp_path):
    """A-instinct-6: 8 slow shadow decides must not eat an enforce-target
    call's deadline in a shared semaphore queue."""
    async def body(url, stub):
        cfg = _spawn_cfg(tmp_path, url, mode="enforce", service={"max_concurrency": 2})
        inst = Instinct(cfg, repo_root=tmp_path)
        await inst.start()
        stub.faults["slow"] = 120
        shadows = [asyncio.create_task(_decide(inst, f"what is {i} times 3", ceiling="shadow"))
                   for i in range(8)]
        await asyncio.sleep(0.02)
        r = await _decide(inst, "what is 17 times 23", ceiling="enforce")
        await asyncio.gather(*shadows)
        stub.faults.pop("slow")
        await inst.aclose()
        return r
    with H.stub_server() as (url, stub):
        r = H.run(body(url, stub))
    assert r["latency_ms"]["queue"] < 50, r["latency_ms"]
    assert r["cascade"][0]["engine"] == "stub" and r["cascade"][0]["action"] != "skipped"


def test_shadow_queue_counts_waiters(tmp_path):
    """shadow_queue bounds shadow work WAITING too: beyond it, a burst is
    dropped (overload), never queued."""
    async def body(url, stub):
        cfg = _spawn_cfg(tmp_path, url, service={"max_concurrency": 2, "shadow_queue": 3})
        inst = Instinct(cfg, repo_root=tmp_path)
        await inst.start()
        stub.faults["slow"] = 100
        rs = await asyncio.gather(*[_decide(inst, f"what is {i}", ceiling="shadow")
                                    for i in range(6)])
        stub.faults.pop("slow")
        await inst.aclose()
        return rs
    with H.stub_server() as (url, stub):
        rs = H.run(body(url, stub))
    reasons = [r["fallback"]["reason"] for r in rs]
    assert reasons.count("overload") == 3, reasons


def test_reload_that_removes_a_decision_mid_call_is_not_a_500(tmp_path):
    """B-instinct-05: a SIGHUP that removes (or breaks) a decision while a call
    is in flight must still answer that call, with exactly one ledger row."""
    async def body(url, stub):
        cfg = _spawn_cfg(tmp_path, url)
        inst = Instinct(cfg, repo_root=tmp_path)
        await inst.start()
        stub.faults["slow"] = 150
        task = asyncio.create_task(_decide(inst, "what is 17 times 23"))
        await asyncio.sleep(0.05)
        spec_file = tmp_path / "decisions" / f"{DID}.toml"
        shutil.move(spec_file, tmp_path / "moved.toml")
        await inst.reload()
        r = await task
        stub.faults.pop("slow")
        await inst.aclose()
        return r, DID in inst.specs
    with H.stub_server() as (url, stub):
        r, still = H.run(body(url, stub))
    assert still is False and r["decision"] == DID and r["contract"] == "instinct/1"
    rows = [json.loads(line) for p in (tmp_path / "ledger").glob("*.jsonl")
            for line in p.read_text().splitlines() if line.strip()]
    assert [row["trace_id"] for row in rows if row.get("kind") == "decide"] == [r["trace_id"]]


def test_uncalibrated_rank_never_acts(tmp_path):
    """B-instinct-08: an uncalibrated rank abstained only when an item's own
    reason said so; all-no_fit items aggregated to 'act'."""
    text = re.sub(r'chain = \[.*?\]', 'chain = ["s", "rules"]',
                  H.spec_text("hydra.pool_fit"), count=1)
    text = text.replace('mode           = "off"', 'mode           = "shadow"').replace(
        'deadline_ms    = 25', 'deadline_ms    = 2000')
    with H.stub_server() as (url, _):
        p = H.write_config(tmp_path, {"s": H.sglang_binding(url)},
                           extra_decisions={"hydra.pool_fit": text})
        cfg = load_config(p, env={})

        async def go():
            inst = Instinct(cfg, repo_root=tmp_path)
            await inst.start()
            r = await inst.decide({"contract": "instinct/1", "decision": "hydra.pool_fit",
                                   "inputs": {"prompt_head": "sort the coal and salt"},
                                   "items": [{"id": f"pool-{i}", "text": "coal salt stones"}
                                             for i in range(5)]})
            await inst.aclose()
            return r
        r = H.run(go())
    assert r["cascade"][0]["engine"] == "s"
    assert all(it["action"] != "act" for it in r["items"])      # every item is no_fit
    assert r["action"] == "abstain" and r["fallback"]["reason"] == "uncalibrated"
