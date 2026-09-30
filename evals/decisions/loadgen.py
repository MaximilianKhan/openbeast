#!/usr/bin/env python3
"""Open-loop Poisson load generator for decision engines (plan §5.8).

    python3 evals/decisions/loadgen.py --decision D --engine E --qps 0.5,1,2,5 \
        --duration 120 [--repeats 3] [--intended-qps 1] [--out load.json]
        [--with-primary-decode --primary-url URL]

Open loop means arrivals follow a Poisson process at the target rate whether
or not earlier requests finished — the sglang-benchmark discipline; a closed
loop hides queueing. Each (qps, repeat) point reports p50/p95/p99, errors and
the achieved rate; `p95_ms` at the top level is the WORST p95 across repeats
at the intended QPS, which is what `latency_p95_ms@load` gates read.

--with-primary-decode streams a long generation from the primary while the
load runs and records its tok/s (interference, both directions). It talks to
--primary-url only when given; never point it at a server you do not own.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import metrics as M  # noqa: E402
import run as R  # noqa: E402

from instinct.config import load_config  # noqa: E402
from instinct.engines import ScoreReq  # noqa: E402
from instinct.spec import load_spec  # noqa: E402


async def one_point(scorer, rows: list[dict], qps: float, duration: float, seed: int) -> dict:
    rng = random.Random(seed)
    lat: list[float] = []
    errors = 0
    tasks = []
    t_end = time.perf_counter() + duration
    n = 0

    async def fire(r):
        nonlocal errors
        t0 = time.perf_counter()
        try:
            await scorer.engine.score(ScoreReq(
                spec=scorer.spec, inputs=r["input"], label_ids=scorer.lock.ids if scorer.lock
                else None, deadline_s=scorer.engine.binding.timeout_ms / 1000))
            lat.append((time.perf_counter() - t0) * 1000)
        except Exception:
            errors += 1
    t_start = time.perf_counter()
    next_t = t_start
    while True:
        next_t += rng.expovariate(qps)
        if next_t > t_end:
            break
        await asyncio.sleep(max(0.0, next_t - time.perf_counter()))
        tasks.append(asyncio.create_task(fire(rows[n % len(rows)])))
        n += 1
    if tasks:
        await asyncio.gather(*tasks)
    wall = time.perf_counter() - t_start
    return {"qps": qps, "sent": n, "ok": len(lat), "errors": errors,
            "achieved_qps": n / wall if wall else 0.0,
            "p50_ms": M.percentile(lat, 0.5) if lat else None,
            "p95_ms": M.percentile(lat, 0.95) if lat else None,
            "p99_ms": M.percentile(lat, 0.99) if lat else None}


async def primary_decode(url: str, seconds: float) -> dict:
    import httpx
    toks, t0 = 0, time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=seconds + 30, trust_env=False) as c:
            async with c.stream("POST", url.rstrip("/") + "/completion", json={
                    "prompt": "Write a long story about a lighthouse keeper.",
                    "n_predict": 100000, "stream": True}) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        toks += 1
                    if time.perf_counter() - t0 > seconds:
                        break
    except Exception as exc:
        return {"error": exc.__class__.__name__}
    dt = time.perf_counter() - t0
    return {"tokens": toks, "seconds": dt, "tok_s": toks / dt if dt else None}


async def main_async(a) -> int:
    cfg = load_config(a.config)
    spec = load_spec(Path(cfg.decisions_dir) / f"{a.decision}.toml")
    data, _ = R.load_dataset(Path(a.data_dir) / a.decision, spec)
    rows = data.get(a.split) or data.get("test") or data.get("calib")
    if not rows:
        raise SystemExit("no rows to replay")
    scorer = R.Scorer(cfg, spec, a.engine)
    await scorer.attach(probe=False)
    qps_list = [float(x) for x in a.qps.split(",") if x]
    intended = float(a.intended_qps) if a.intended_qps else qps_list[0]
    points = []
    decode = None
    decode_task = None
    if a.with_primary_decode:
        if not a.primary_url:
            raise SystemExit("--with-primary-decode needs --primary-url")
        total = a.duration * len(qps_list) * a.repeats
        decode_task = asyncio.create_task(primary_decode(a.primary_url, total))
    for q in qps_list:
        for rep in range(a.repeats):
            points.append(await one_point(scorer, rows, q, a.duration, seed=1000 * rep + int(q * 10)))
    if decode_task:
        decode = await decode_task
    at = [p["p95_ms"] for p in points if p["qps"] == intended and p["p95_ms"] is not None]
    report = {"decision": spec.id, "engine": a.engine, "duration_s": a.duration,
              "repeats": a.repeats, "intended_qps": intended, "points": points,
              "p95_ms": max(at) if at else None,
              "p95_spread_ms": [min(at), max(at)] if at else None,
              "primary_decode": decode}
    await scorer.engine.aclose()
    text = json.dumps(report, indent=2)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="open-loop Poisson load for a decision engine")
    ap.add_argument("--decision", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--qps", default="0.5,1,2,5")
    ap.add_argument("--intended-qps")
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--split", default="test")
    ap.add_argument("--config", default=None)
    ap.add_argument("--data-dir", default=str(HERE))
    ap.add_argument("--out")
    ap.add_argument("--with-primary-decode", action="store_true")
    ap.add_argument("--primary-url")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
