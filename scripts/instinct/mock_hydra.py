#!/usr/bin/env python3
"""A mock hydra that follows the instinct-route/1 obligations (plan §5.11).

Reference for hydra's real implementation and the fixture for
tests/test_instinct_contract.py. It shows the ONLY coupling hydra has to
instinct:

  1. hard filters first — identity, capability (vision), ctx_max >= est
     tokens, health; mechanical facts are never delegated;
  2. call /route only when >= 2 eligible pools remain AND
     GET /v1/instinct/contract lists "instinct-route/1";
  3. use the answer only when enforce == true (and action == "act");
     on timeout, non-2xx, abstain, fallback or enforce:false -> static policy;
  4. map class -> pool in HYDRA's config; capacity may override (logged);
  5. fully functional with instinct absent (dead port = static policy);
  6. never register an instinct engine URL as a pool (role="instinct-engine");
  7. feedback keyed by trace_id (optional).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents"))
from instinct.client import InstinctClient  # noqa: E402


@dataclass
class PoolDesc:
    id: str
    ctx_max: int
    vision: bool = False
    healthy: bool = True
    load: float = 0.0
    role: str = "generation"


@dataclass
class Pick:
    pool: str | None
    why: str
    trace_id: str | None = None
    instinct_used: bool = False
    log: list[str] = field(default_factory=list)


class MockHydra:
    def __init__(self, pools: list[PoolDesc], class_to_pool: dict[str, str],
                 instinct: InstinctClient | None = None, deadline_ms: int = 25):
        bad = [p.id for p in pools if p.role == "instinct-engine"]
        if bad:
            raise ValueError(f"refusing instinct-engine nodes as pools: {bad}")  # obligation 6
        self.pools = pools
        self.class_to_pool = class_to_pool
        self.instinct = instinct
        self.deadline_ms = deadline_ms
        self.route_calls = 0

    def eligible(self, features: dict) -> list[PoolDesc]:
        out = []
        for p in self.pools:
            if not p.healthy:
                continue
            if features.get("has_images") and not p.vision:
                continue
            if p.ctx_max < int(features.get("est_prompt_tokens", 0)):
                continue
            out.append(p)
        return out

    def static(self, elig: list[PoolDesc], why: str) -> Pick:
        best = min(elig, key=lambda p: (p.load, p.id))
        return Pick(best.id, why)

    async def pick(self, features: dict) -> Pick:
        elig = self.eligible(features)
        if not elig:
            return Pick(None, "no eligible pool")
        if len(elig) < 2 or self.instinct is None:
            return self.static(elig, "static: fewer than 2 eligible pools or no instinct")
        if "instinct-route/1" not in await self.instinct.contracts():
            return self.static(elig, "static: instinct-route/1 not offered")
        self.route_calls += 1
        ans = await self.instinct.route(features, deadline_ms=self.deadline_ms)
        if not ans or ans.get("enforce") is not True or ans.get("action") != "act":
            p = self.static(elig, "static: instinct did not enforce")
            p.trace_id = (ans or {}).get("trace_id")
            return p
        label = (ans.get("task_class") or {}).get("label")
        target = self.class_to_pool.get(label or "")
        names = {p.id for p in elig}
        if target not in names:
            p = self.static(elig, f"static: class {label!r} maps to no eligible pool")
            p.trace_id = ans.get("trace_id")
            return p
        chosen = next(p for p in elig if p.id == target)
        if chosen.load >= 0.95:
            p = self.static(elig, f"capacity override of class {label!r}")  # obligation 4
            p.trace_id = ans.get("trace_id")
            return p
        return Pick(target, f"instinct: task_class={label}", ans.get("trace_id"), True)
