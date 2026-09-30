"""Core math, identical for every engine (plan §5.5).

Engines hand back per-label FULL-vocabulary probabilities q_i (or logits for
classifier heads, or one-hot for rules). From those:

  label_mass = Σ q_i                       (null for heads / rules)
  raw_p_i    = q_i / Σ q
  p_i        = softmax(log q_i / T)        T from the calibration record —
                                           identical to SGLang's temperature
  p_top, margin = p1 - p2, shape = 1 - H(p)/ln K

and the policy action act | review | abstain (fallback is decided by the
caller of this module: errors, timeouts, deadline, overload).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from .spec import DecisionSpec

MODE_ORDER = {"off": 0, "shadow": 1, "canary": 2, "enforce": 3}


def normalize(q: dict[str, float]) -> dict[str, float]:
    s = sum(max(0.0, v) for v in q.values())
    if s <= 0 or not math.isfinite(s):
        raise ValueError("label scores sum to zero")
    return {k: max(0.0, v) / s for k, v in q.items()}


def softmax(z: dict[str, float], temperature: float = 1.0) -> dict[str, float]:
    if temperature <= 0:
        raise ValueError("temperature must be > 0")
    m = max(z.values())
    if not math.isfinite(m):
        raise ValueError("label scores sum to zero")
    e = {k: (math.exp((v - m) / temperature) if math.isfinite(v) else 0.0) for k, v in z.items()}
    s = sum(e.values())
    return {k: v / s for k, v in e.items()}


def apply_temperature(q: dict[str, float], temperature: float = 1.0) -> dict[str, float]:
    """p_i = softmax(log q_i / T). With T = 1 this is plain renormalization."""
    logs = {k: (math.log(v) if v > 0 else -math.inf) for k, v in q.items()}
    if all(v == -math.inf for v in logs.values()):
        raise ValueError("label scores sum to zero")
    return softmax(logs, temperature)


def confidence(p: dict[str, float]) -> dict[str, float]:
    vals = sorted(p.values(), reverse=True)
    p1 = vals[0]
    p2 = vals[1] if len(vals) > 1 else 0.0
    k = len(vals)
    h = -sum(v * math.log(v) for v in vals if v > 0)
    shape = 1.0 - (h / math.log(k)) if k > 1 else 1.0
    return {"p_top": p1, "margin": p1 - p2, "shape": max(0.0, min(1.0, shape))}


def argmax(p: dict[str, float]) -> str:
    # Stable: ties go to the label listed first.
    best, bv = None, -1.0
    for k, v in p.items():
        if v > bv:
            best, bv = k, v
    return best  # type: ignore[return-value]


def expected_value(spec: DecisionSpec, p: dict[str, float]) -> float | None:
    if spec.type != "score":
        return None
    total = 0.0
    for i, lb in enumerate(spec.labels):
        try:
            level = float(lb.text)
        except ValueError:
            level = float(i)
        total += level * p.get(lb.name, 0.0)
    return total


def canary_bucket(request_id: str | None, pct: int) -> bool:
    if pct <= 0 or not request_id:
        return False
    if pct >= 100:
        return True
    h = int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16)
    return (h % 100) < pct


def mask_mechanical(spec: DecisionSpec, q: dict[str, float] | None = None,
                    logits: dict[str, float] | None = None, fact: str | None = None
                    ) -> tuple[dict[str, float] | None, dict[str, float] | None]:
    """Zero every mechanical label except `fact` (the one the request's facts
    force, if any) BEFORE anything else sees the row. Mechanical facts are
    computed, never judged, so a model's mass on them is not an answer.

    This is the single definition the service AND the eval harness use, so
    the population that is calibrated and gated is the one that is served.
    (Zeroing before temperature equals zeroing after and renormalizing:
    p_i ∝ q_i^(1/T) either way.) If nothing is left the caller gets a
    ValueError from build_answer — the engine gave no usable answer."""
    if not spec.mechanical:
        return q, logits
    zero = {m for m in spec.mechanical if m != fact}
    if q is not None:
        q = {k: (0.0 if k in zero else v) for k, v in q.items()}
    if logits is not None:
        logits = {k: (-math.inf if k in zero else v) for k, v in logits.items()}
    return q, logits


@dataclass
class Answer:
    label: str | None
    probabilities: dict[str, float] | None
    raw_probabilities: dict[str, float] | None
    calibrated: bool
    confidence: dict[str, float] | None
    label_mass: float | None
    labels_truncated: list[str] = field(default_factory=list)
    expected_value: float | None = None
    mechanical: bool = False


def build_answer(spec: DecisionSpec, *, q: dict[str, float] | None = None,
                 logits: dict[str, float] | None = None, label_mass: float | None = None,
                 truncated: list[str] | None = None, temperature: float | None = None,
                 calibrated: bool = False, mechanical_zero: list[str] | None = None) -> Answer:
    """Turn one engine row into probabilities + confidence.

    `mechanical_zero` lists labels a model may never pick (the spec's
    mechanical labels when the facts do not hold): they are zeroed and the rest
    renormalized, because those facts are computed, never judged.
    """
    T = temperature if (temperature and calibrated) else 1.0
    if logits is not None:
        raw = softmax(logits)
        p = softmax(logits, T)
    elif q is not None:
        raw = normalize(q)
        p = apply_temperature(q, T)
    else:
        return Answer(None, None, None, False, None, label_mass, list(truncated or []))
    if mechanical_zero:
        keep = {k: v for k, v in p.items() if k not in mechanical_zero}
        if sum(keep.values()) > 0:
            p = {k: (keep[k] / sum(keep.values()) if k in keep else 0.0) for k in p}
    label = argmax(p)
    return Answer(label, p, raw, calibrated, confidence(p), label_mass,
                  list(truncated or []), expected_value(spec, p))


def effective_threshold(spec: DecisionSpec, label: str,
                        thresholds: dict[str, float | None] | None) -> float | None:
    """max(policy.act[label], fitted[label]); None when the fit said never."""
    floor = spec.policy.act.get(label)
    if thresholds and label in thresholds:
        fitted = thresholds[label]
        if fitted is None or floor is None:
            return None
        return max(floor, fitted)
    return floor


def decide_action(spec: DecisionSpec, ans: Answer, *, thresholds: dict[str, float] | None = None,
                  context_forbidden: bool = False, head_engine: bool = False,
                  ood: bool = False) -> tuple[str, str | None]:
    """(action, reason) for one answer. `thresholds` are the calibration
    record's fitted thresholds; they can only RAISE the spec's policy.act
    value (the reviewed floor — a fit on a small calib split must not quietly
    lower `inline = 0.90` to 0.55), a fitted None means "infeasible, never
    act", and they can never add a label — only labels in policy.act may act
    (I4)."""
    pol = spec.policy
    if context_forbidden:
        return "abstain", "eval_context"
    if ans.probabilities is None or ans.label is None:
        return "abstain", "uncalibrated"
    label = ans.label
    p_top = ans.confidence["p_top"] if ans.confidence else 0.0
    margin = ans.confidence["margin"] if ans.confidence else 0.0
    if label in pol.act:
        thr = effective_threshold(spec, label, thresholds)
        if thr is None:
            return "abstain", "below_threshold"
        if not ans.calibrated:
            return "abstain", "uncalibrated"
        if ans.labels_truncated:
            return "abstain", "labels_truncated"
        if ood:
            return "abstain", "ood_input"
        if not head_engine and (ans.label_mass is not None) and ans.label_mass < pol.min_label_mass:
            return "abstain", "low_label_mass"
        if p_top < thr or margin < pol.min_margin:
            return "abstain", "below_threshold"
        return "act", None
    if label in pol.review and ans.calibrated and p_top >= pol.review[label]:
        return "review", None
    return "abstain", "not_act_label"


def is_enforced(effective_mode: str, in_canary: bool, action: str) -> bool:
    return action == "act" and (effective_mode == "enforce"
                                or (effective_mode == "canary" and in_canary))


def min_mode(*modes: str) -> str:
    return min(modes, key=lambda m: MODE_ORDER[m])
