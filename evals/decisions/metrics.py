"""Decision-quality metrics, stdlib only (plan §5.8).

Everything a gate record cites is computed here:
  accuracy, macro-F1 (each with a Wilson / bootstrap 95% CI), NLL, Brier,
  ECE (15 equal-mass bins; 5 and the tag "indicative" when n < 150) with a
  reliability table, AURC (risk-coverage), exact two-sided McNemar on paired
  correctness, percentile bootstrap CIs and a paired bootstrap ΔNLL.
Rows are dicts {"y": true label, "p": {label: prob} | None}. A row with p=None
(an abstaining incumbent) counts as WRONG for accuracy and gets probability
1/K for the proper scoring rules — it never silently disappears.
"""
from __future__ import annotations

import math
import random
from typing import Callable, Sequence

EPS = 1e-12


def _p(row: dict, labels: Sequence[str]) -> dict[str, float]:
    p = row.get("p")
    if not p:
        return {lb: 1.0 / len(labels) for lb in labels}
    return p


def pred(row: dict) -> str | None:
    p = row.get("p")
    if not p:
        return None
    best, bv = None, -1.0
    for k, v in p.items():
        if v > bv:
            best, bv = k, v
    return best


def correct(rows: list[dict]) -> list[bool]:
    return [pred(r) == r["y"] for r in rows]


def accuracy(rows: list[dict]) -> float:
    c = correct(rows)
    return sum(c) / len(c) if c else float("nan")


def macro_f1(rows: list[dict], labels: Sequence[str]) -> float:
    f1s = []
    for lb in labels:
        tp = sum(1 for r in rows if pred(r) == lb and r["y"] == lb)
        fp = sum(1 for r in rows if pred(r) == lb and r["y"] != lb)
        fn = sum(1 for r in rows if pred(r) != lb and r["y"] == lb)
        if tp + fp + fn == 0:
            continue
        f1s.append(2 * tp / (2 * tp + fp + fn))
    return sum(f1s) / len(f1s) if f1s else float("nan")


def nll(rows: list[dict], labels: Sequence[str]) -> float:
    if not rows:
        return float("nan")
    return sum(-math.log(max(_p(r, labels).get(r["y"], 0.0), EPS)) for r in rows) / len(rows)


def row_nll(r: dict, labels: Sequence[str]) -> float:
    return -math.log(max(_p(r, labels).get(r["y"], 0.0), EPS))


def brier(rows: list[dict], labels: Sequence[str]) -> float:
    """Multi-class Brier: Σ_k (p_k - 1[y=k])^2, averaged."""
    if not rows:
        return float("nan")
    tot = 0.0
    for r in rows:
        p = _p(r, labels)
        tot += sum((p.get(lb, 0.0) - (1.0 if r["y"] == lb else 0.0)) ** 2 for lb in labels)
    return tot / len(rows)


def ece(rows: list[dict], labels: Sequence[str], bins: int | None = None) -> dict:
    """Top-label ECE over equal-MASS bins. Returns {ece, bins, indicative, table}
    where table rows are [bin_lo, bin_hi, mean_conf, acc, n]."""
    n = len(rows)
    if n == 0:
        return {"ece": float("nan"), "bins": 0, "indicative": True, "table": []}
    if bins is None:
        bins = 15 if n >= 150 else 5
    bins = max(1, min(bins, n))
    pts = sorted(((max(_p(r, labels).values()), pred(r) == r["y"]) for r in rows),
                 key=lambda t: t[0])
    table, total = [], 0.0
    for b in range(bins):
        lo_i, hi_i = (b * n) // bins, ((b + 1) * n) // bins
        chunk = pts[lo_i:hi_i]
        if not chunk:
            continue
        conf = sum(c for c, _ in chunk) / len(chunk)
        acc = sum(1 for _, ok in chunk if ok) / len(chunk)
        total += len(chunk) / n * abs(conf - acc)
        table.append([round(chunk[0][0], 6), round(chunk[-1][0], 6), round(conf, 6),
                      round(acc, 6), len(chunk)])
    return {"ece": total, "bins": bins, "indicative": n < 150, "table": table}


def aurc(rows: list[dict], labels: Sequence[str]) -> dict:
    """Area under the risk-coverage curve: sort by confidence (desc), risk at
    each coverage k/n is the error rate of the k most confident rows."""
    n = len(rows)
    if n == 0:
        return {"aurc": float("nan"), "curve": []}
    pts = sorted(((max(_p(r, labels).values()), pred(r) == r["y"]) for r in rows),
                 key=lambda t: -t[0])
    errs, area, curve = 0, 0.0, []
    for k, (_, ok) in enumerate(pts, 1):
        errs += 0 if ok else 1
        risk = errs / k
        area += risk
        if k in (max(1, n // 10), n // 4, n // 2, (3 * n) // 4, n):
            curve.append([round(k / n, 4), round(risk, 4)])
    return {"aurc": area / n, "curve": curve}


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(a_correct: Sequence[bool], b_correct: Sequence[bool]) -> dict:
    """Exact two-sided McNemar on paired correctness. b = discordant rows where
    A is right and B wrong; c = the reverse. p = 2·P(X <= min(b,c)),
    X ~ Binom(b+c, 0.5), capped at 1."""
    if len(a_correct) != len(b_correct):
        raise ValueError("paired samples must be the same length")
    b = sum(1 for x, y in zip(a_correct, b_correct) if x and not y)
    c = sum(1 for x, y in zip(a_correct, b_correct) if y and not x)
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "p": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return {"b": b, "c": c, "p": min(1.0, 2 * tail)}


def bootstrap_ci(values: Sequence, stat: Callable[[list], float], *, n_boot: int = 2000,
                 seed: int = 20260930, alpha: float = 0.05) -> tuple[float, float]:
    vals = list(values)
    if not vals:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        s = [vals[rng.randrange(len(vals))] for _ in vals]
        v = stat(s)
        if not math.isnan(v):
            stats.append(v)
    if not stats:
        return (float("nan"), float("nan"))
    stats.sort()
    lo = stats[int((alpha / 2) * (len(stats) - 1))]
    hi = stats[int((1 - alpha / 2) * (len(stats) - 1))]
    return (lo, hi)


def paired_bootstrap_delta(a: Sequence[float], b: Sequence[float], **kw) -> dict:
    """Mean(a - b) with a percentile bootstrap CI over paired rows."""
    if len(a) != len(b):
        raise ValueError("paired samples must be the same length")
    d = [x - y for x, y in zip(a, b)]
    if not d:
        return {"delta": float("nan"), "ci95": [float("nan"), float("nan")]}
    lo, hi = bootstrap_ci(d, lambda s: sum(s) / len(s), **kw)
    return {"delta": sum(d) / len(d), "ci95": [lo, hi]}


def percentile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    i = min(len(s) - 1, max(0, int(math.ceil(q * len(s))) - 1))
    return s[i]
