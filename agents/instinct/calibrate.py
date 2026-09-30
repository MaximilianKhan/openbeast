"""Calibration: one-temperature fit, cost-matrix thresholds, record I/O.

Records (plan §5.7) live under <records_dir>/<decision>/:
  calib/<hash16>.json   written by `run.py --calibrate`
  gates/<hash16>.json   written by `run.py --gate`
Both are keyed by the first 16 hex of decision_hash, and both carry the full
hash — a record whose full hash differs from the live one never matches.

Threshold fitting (P3 graft): for each label in policy.act, choose the
threshold that minimizes expected cost on the calib split subject to the
policy's hard constraints; ties go to the smallest threshold. Cost model:
  * acting with label a on a row whose truth is t != a costs cost["t>a"]
    (default 1);
  * NOT acting on a row whose truth is t costs cost["t>abstain"] if given,
    else min over x != t of cost["t>x"] (default 1) — abstaining hands the
    row to the incumbent path, which is what "predicting the other label"
    means for a skip-only decision.
Constraints are evaluated on calib here and reported on test separately by
the harness; nothing is ever refit on test.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import core
from .spec import Constraint, DecisionSpec

EPS = 1e-12
T_LO, T_HI = 0.05, 20.0


def nll(probs: list[dict[str, float]], ys: list[str]) -> float:
    if not probs:
        return float("nan")
    return sum(-math.log(max(p.get(y, 0.0), EPS)) for p, y in zip(probs, ys)) / len(probs)


def golden_section(f: Callable[[float], float], lo: float, hi: float,
                   iters: int = 80, tol: float = 1e-7) -> float:
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(iters):
        if abs(b - a) < tol:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    return (a + b) / 2


def fit_temperature(qs: list[dict[str, float]], ys: list[str],
                    lo: float = T_LO, hi: float = T_HI) -> float:
    """Single temperature by golden-section search on NLL over [lo, hi]
    (searched in log T, where NLL is far better conditioned)."""
    pairs = [(q, y) for q, y in zip(qs, ys) if q]
    if not pairs:
        return 1.0

    def loss(logt: float) -> float:
        t = math.exp(logt)
        return nll([core.apply_temperature(q, t) for q, _ in pairs], [y for _, y in pairs])
    return math.exp(golden_section(loss, math.log(lo), math.log(hi)))


def _row_would_act(spec: DecisionSpec, row: dict, label: str, thr: float | None) -> bool:
    """Would this (already temperature-scaled) row act as `label` at `thr`?"""
    if thr is None or not row.get("p"):
        return False
    ans = core.Answer(label=core.argmax(row["p"]), probabilities=row["p"],
                      raw_probabilities=row["p"], calibrated=True,
                      confidence=core.confidence(row["p"]), label_mass=row.get("label_mass"),
                      labels_truncated=list(row.get("truncated") or []))
    if ans.label != label:
        return False
    action, _ = core.decide_action(spec, ans, thresholds={label: thr},
                                   ood=bool(row.get("ood")))
    return action == "act"


def act_stats(spec: DecisionSpec, rows: list[dict], label: str, thr: float | None) -> dict:
    """act_errors / act_coverage / act_precision for one label at one threshold.
    act_coverage = acted-as-label / rows whose truth is label."""
    acted = [r for r in rows if _row_would_act(spec, r, label, thr)]
    errors = sum(1 for r in acted if r["y"] != label)
    n_true = sum(1 for r in rows if r["y"] == label)
    correct = len(acted) - errors
    return {"acts": len(acted), "act_errors": errors,
            "act_coverage": (correct / n_true) if n_true else 0.0,
            "act_precision": (correct / len(acted)) if acted else 1.0}


def _cost(spec: DecisionSpec, t: str, pred: str) -> float:
    return float(spec.policy.cost.get(f"{t}>{pred}", 1.0))


def _miss_cost(spec: DecisionSpec, t: str) -> float:
    if f"{t}>abstain" in spec.policy.cost:
        return float(spec.policy.cost[f"{t}>abstain"])
    others = [x for x in spec.label_names if x != t]
    return min(_cost(spec, t, x) for x in others) if others else 1.0


def _constraint_ok(c: Constraint, stats: dict) -> bool:
    v = stats.get(c.metric)
    if v is None:
        return True  # not threshold-dependent: reported, not fitted
    return compare(v, c.op, c.value)


def compare(v: float, op: str, target: float) -> bool:
    return {"==": v == target, "<=": v <= target, ">=": v >= target,
            "<": v < target, ">": v > target}[op]


def fit_thresholds(spec: DecisionSpec, rows: list[dict]) -> tuple[dict[str, float | None], dict]:
    """rows: [{"y", "p" (calibrated), "label_mass", "truncated", "ood"?}]."""
    out: dict[str, float | None] = {}
    detail: dict[str, Any] = {}
    for label in spec.policy.act:
        cands = sorted({round(r["p"][label], 6) for r in rows
                        if r.get("p") and core.argmax(r["p"]) == label})
        mine = [c for c in spec.policy.hard_constraints
                if c.label == label and c.metric in ("act_errors", "act_coverage",
                                                     "act_precision")]
        best = None
        infeasible = 0
        for thr in cands + [None]:
            st = act_stats(spec, rows, label, thr)
            if not all(_constraint_ok(c, st) for c in mine):
                infeasible += 1
                continue
            cost = 0.0
            for r in rows:
                acted = _row_would_act(spec, r, label, thr)
                if acted:
                    cost += 0.0 if r["y"] == label else _cost(spec, r["y"], label)
                elif r["y"] == label:
                    cost += _miss_cost(spec, r["y"])
            key = (cost, thr if thr is not None else 2.0)
            if best is None or key < best[0]:
                best = (key, thr, st)
        if best is None:
            out[label] = None
            detail[label] = {"feasible": False}
        else:
            out[label] = best[1]
            detail[label] = {"feasible": True, "expected_cost": best[0][0],
                             "calib": best[2], "candidates_rejected": infeasible}
    return out, detail


# --- record I/O --------------------------------------------------------------

def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def calib_path(records_dir: Path, decision: str, dhash: str) -> Path:
    return Path(records_dir) / decision / "calib" / f"{dhash[:16]}.json"


def gate_path(records_dir: Path, decision: str, dhash: str) -> Path:
    return Path(records_dir) / decision / "gates" / f"{dhash[:16]}.json"


def linear_model_path(records_dir: Path, decision: str, dhash: str) -> Path:
    return Path(records_dir) / decision / "linear" / f"{dhash[:16]}.json"


def write_record(path: Path, record: dict) -> None:
    _atomic_write_json(path, record)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_record(path: Path, dhash: str) -> dict | None:
    """A record only counts if its full decision_hash matches."""
    try:
        with open(path) as fh:
            rec = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(rec, dict) or rec.get("decision_hash") != dhash:
        return None
    return rec
