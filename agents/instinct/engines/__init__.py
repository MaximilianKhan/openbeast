"""Engine protocol, capabilities, and the adapter registry (plan §5.6).

An engine turns a decision into per-label scores. It never decides anything:
the core math and the policy are identical for every engine, so swapping an
engine can only change the numbers, never the rules.

  attach(specs)  -> {decision_id: LockResult}   tokenizer label locks (§5.4)
  score(req)     -> ScoreRes                    one row per scored prefix
  probe(specs)   -> ProbeResult                 conformance (§5.7)
"""
from __future__ import annotations

import math
import os
import stat
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..config import EngineBinding
from ..render import Rendered
from ..spec import DecisionSpec


@dataclass(frozen=True)
class Caps:
    probs: bool            # returns full-vocab probabilities (or calibratable scores)
    label_mass: bool
    mis: bool = False
    setwise: bool = False
    head: bool = False
    deterministic: bool = True
    max_items: int = 16
    needs_render: bool = True


@dataclass
class LockResult:
    ok: bool
    ids: dict[str, int] | None = None
    reason: str | None = None


@dataclass
class ScoreReq:
    spec: DecisionSpec
    inputs: dict[str, Any]
    items: list[str] | None = None
    rendered: Rendered | None = None
    label_ids: dict[str, int] | None = None
    deadline_s: float = 1.0


@dataclass
class ScoreRow:
    q: dict[str, float] | None = None
    logits: dict[str, float] | None = None
    label_mass: float | None = None
    truncated: list[str] = field(default_factory=list)
    ood: bool = False
    defer: bool = False          # rules: "run the legacy path" (hint present)
    mechanical: str | None = None


@dataclass
class ScoreRes:
    rows: list[ScoreRow]
    exec_used: str | None = None
    engine_ms: float = 0.0
    model_id: str | None = None
    usage: dict | None = None


@dataclass
class ProbeResult:
    ok: bool
    checks: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    nondeterministic: bool = False
    exec_forced: str | None = None


class EngineError(RuntimeError):
    """The engine answered, but not with something we can trust (fallback)."""


def read_key_file(path: str) -> str:
    """Read a bearer key from a 0600 file. Fail closed on a group/world
    readable file — a key anyone on the box can read is not a key."""
    if not path:
        return ""
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise EngineError(f"key file {path} is group/world accessible (want 0600)")
    with open(path) as fh:
        return fh.read().strip()


class Engine:
    adapter = "base"
    caps = Caps(probs=False, label_mass=False)

    def __init__(self, binding: EngineBinding, **ctx):
        self.binding = binding
        self.id = binding.name
        self.ctx = ctx
        self.latencies: deque = deque(maxlen=200)

    @property
    def exec(self) -> str | None:
        return self.binding.exec if self.caps.needs_render else None

    def hash_identity(self) -> dict:
        return self.binding.hash_identity()

    async def attach(self, specs: list[DecisionSpec]) -> dict[str, LockResult]:
        return {s.id: LockResult(True) for s in specs}

    async def score(self, req: ScoreReq) -> ScoreRes:  # pragma: no cover - abstract
        raise NotImplementedError

    async def probe(self, specs: list[DecisionSpec]) -> ProbeResult:
        return ProbeResult(True, {"kind": "in-process"})

    async def aclose(self) -> None:
        return None

    def record_latency(self, ms: float) -> None:
        self.latencies.append(ms)

    def p95_ms(self) -> float:
        """Rolling p95 once 20 samples exist. Before that: the worst observed
        sample, ignoring ONE outlier once 10 samples exist (the conformance
        probe seeds ~12), or timeout_ms when fewer than 5 were measured.
        (Plan §5.5 says timeout_ms until 20 samples; taken literally that
        starves any engine whose timeout exceeds a decision's deadline — it
        would never be tried, so never measured.) Tolerating one outlier
        matters because a timeout records ~the deadline: with plain max, ONE
        timeout would skip the engine on every call until the next probe."""
        if not self.caps.needs_render:
            return 0.0
        n = len(self.latencies)
        if n < 20:
            if n < 5:
                return float(self.binding.timeout_ms)
            xs = sorted(self.latencies)
            return xs[-2] if n >= 10 else xs[-1]
        xs = sorted(self.latencies)
        return xs[min(len(xs) - 1, int(math.ceil(0.95 * len(xs))) - 1)]

    def p50_ms(self) -> float | None:
        if not self.latencies:
            return None
        xs = sorted(self.latencies)
        return xs[len(xs) // 2]


def build_engine(binding: EngineBinding, **ctx) -> Engine:
    from . import linear, llamacpp, rules, sglang
    adapters = {"rules": rules.RulesEngine, "linear": linear.LinearEngine,
                "llamacpp_logprobs": llamacpp.LlamaCppEngine,
                "sglang_score": sglang.SGLangEngine}
    try:
        cls = adapters[binding.adapter]
    except KeyError:
        raise EngineError(f"unknown adapter {binding.adapter!r}") from None
    return cls(binding, **ctx)
