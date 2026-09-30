"""`linear`: hashed n-gram logistic regression, pure stdlib (plan §5.6).

Tier 0 and the honest baseline: an LLM binding that cannot beat this with
significance does not ship. Features are char 3-5-grams plus word 1-2-grams
(plus typed facts such as has_tools=true), hashed with crc32 into 2^18
buckets, then a multinomial logistic regression with L2, trained by SGD in a
fixed, seeded order so a fit is reproducible byte for byte.

  q          the model's probabilities (label_mass = null: not a vocabulary)
  ood_score  fraction of the input's hashed features seen in training; below
             0.3 the row is flagged `ood` and can never act.

The model file lives at <records_dir>/<decision>/linear/<hash16>.json and is
written by `evals/decisions/run.py --fit-linear`. Its own sha256 is recorded
in the calibration record — retraining without recalibrating drops the
decision to shadow.
"""
from __future__ import annotations

import json
import math
import random
import re
import time
import zlib
from pathlib import Path
from typing import Any

from .. import calibrate
from ..render import linear_text
from ..spec import DecisionSpec, decision_hash
from . import Caps, Engine, LockResult, ProbeResult, ScoreReq, ScoreRes, ScoreRow

N_BUCKETS = 1 << 18
OOD_FLOOR = 0.3
FORMAT = "instinct-linear/1"
_WORD = re.compile(r"[a-z0-9_']+")


def features(spec: DecisionSpec, inputs: dict[str, Any], item: str | None = None,
             n_buckets: int = N_BUCKETS) -> dict[int, float]:
    text = linear_text(spec, inputs)
    if item is not None:
        text = text + "\n" + item
    low = " " + " ".join(text.lower().split()) + " "
    counts: dict[int, float] = {}

    def add(key: str) -> None:
        b = zlib.crc32(key.encode("utf-8")) % n_buckets
        counts[b] = counts.get(b, 0.0) + 1.0
    for n in (3, 4, 5):
        for i in range(max(0, len(low) - n + 1)):
            add("c:" + low[i:i + n])
    words = _WORD.findall(low)
    for w in words:
        add("w:" + w)
    for a, b in zip(words, words[1:]):
        add("b:" + a + " " + b)
    for name, ispec in spec.inputs.items():
        v = inputs.get(name)
        if ispec.type == "bool":
            add(f"f:{name}={'true' if v else 'false'}")
        elif ispec.type == "int" and isinstance(v, int):
            add(f"f:{name}~{int(math.log2(v + 1))}")
    add("f:bias")
    norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
    return {k: v / norm for k, v in counts.items()}


def _scores(model: dict, x: dict[int, float]) -> dict[str, float]:
    out = {}
    for lb in model["labels"]:
        w = model["_w"][lb]
        out[lb] = model["bias"][lb] + sum(w.get(k, 0.0) * v for k, v in x.items())
    return out


def _softmax(z: dict[str, float]) -> dict[str, float]:
    m = max(z.values())
    e = {k: math.exp(v - m) for k, v in z.items()}
    s = sum(e.values())
    return {k: v / s for k, v in e.items()}


def fit(spec: DecisionSpec, rows: list[dict], *, epochs: int = 40, lr: float = 0.5,
        l2: float = 1e-4, seed: int = 20260930) -> dict:
    """rows: [{"input": {...}, "label": str, "items"?: ...}] (non-rank)."""
    labels = spec.label_names
    data = []
    for r in rows:
        if r["label"] not in labels:
            raise ValueError(f"row {r.get('id')}: label {r['label']!r} not in {labels}")
        data.append((features(spec, r["input"]), r["label"]))
    model: dict[str, Any] = {"labels": labels, "bias": {lb: 0.0 for lb in labels},
                             "_w": {lb: {} for lb in labels}}
    rng = random.Random(seed)
    order = list(range(len(data)))
    for ep in range(epochs):
        rng.shuffle(order)
        step = lr / (1.0 + ep * 0.1)
        for i in order:
            x, y = data[i]
            p = _softmax(_scores(model, x))
            for lb in labels:
                g = p[lb] - (1.0 if lb == y else 0.0)
                w = model["_w"][lb]
                for k, v in x.items():
                    w[k] = w.get(k, 0.0) * (1.0 - step * l2) - step * g * v
                model["bias"][lb] -= step * g
    seen = sorted({k for x, _ in data for k in x})
    out = {
        "format": FORMAT, "decision": spec.id, "labels": labels, "n_buckets": N_BUCKETS,
        "bias": {k: round(v, 6) for k, v in model["bias"].items()},
        "weights": {lb: {str(k): round(v, 6) for k, v in sorted(w.items()) if abs(v) > 1e-7}
                    for lb, w in model["_w"].items()},
        "seen": seen, "n_train": len(data), "epochs": epochs, "lr": lr, "l2": l2, "seed": seed,
    }
    return out


def load_model(path: Path) -> dict:
    with open(path) as fh:
        m = json.load(fh)
    if m.get("format") != FORMAT:
        raise ValueError(f"{path}: not an {FORMAT} model")
    m["_w"] = {lb: {int(k): float(v) for k, v in w.items()} for lb, w in m["weights"].items()}
    m["_seen"] = set(m["seen"])
    return m


def predict(model: dict, spec: DecisionSpec, inputs: dict, item: str | None = None
            ) -> tuple[dict[str, float], float]:
    x = features(spec, inputs, item, model.get("n_buckets", N_BUCKETS))
    p = _softmax(_scores(model, x))
    real = [k for k in x if k != zlib.crc32(b"f:bias") % model.get("n_buckets", N_BUCKETS)]
    seen = sum(1 for k in real if k in model["_seen"])
    return p, (seen / len(real)) if real else 0.0


def model_hash(spec: DecisionSpec, binding_identity: dict) -> str:
    return decision_hash(spec, binding_identity, None)


class LinearEngine(Engine):
    adapter = "linear"
    caps = Caps(probs=True, label_mass=False, needs_render=False, max_items=256)

    def __init__(self, binding, **ctx):
        super().__init__(binding, **ctx)
        self.records_dir = Path(ctx.get("records_dir") or ".")
        self.models: dict[str, dict] = {}
        self.model_sha: dict[str, str] = {}

    def model_path(self, spec: DecisionSpec) -> Path:
        return calibrate.linear_model_path(self.records_dir, spec.id,
                                           model_hash(spec, self.hash_identity()))

    async def attach(self, specs: list[DecisionSpec]) -> dict[str, LockResult]:
        out = {}
        self.models.clear()
        self.model_sha.clear()
        for s in specs:
            p = self.model_path(s)
            try:
                m = load_model(p)
                if m.get("labels") != s.label_names or m.get("decision") != s.id:
                    raise ValueError("model labels/decision do not match the spec")
                self.models[s.id] = m
                self.model_sha[s.id] = calibrate.file_sha256(p)
                out[s.id] = LockResult(True)
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                out[s.id] = LockResult(False, reason=f"no_model: {exc.__class__.__name__}")
        return out

    async def probe(self, specs) -> ProbeResult:
        return ProbeResult(True, {"models": sorted(self.models)})

    async def score(self, req: ScoreReq) -> ScoreRes:
        t0 = time.perf_counter()
        m = self.models.get(req.spec.id)
        if m is None:
            from . import EngineError
            raise EngineError("no linear model for this decision")
        rows = []
        targets = req.items if req.spec.type == "rank" else [None]
        for it in targets:
            p, ood = predict(m, req.spec, req.inputs, it)
            rows.append(ScoreRow(q=p, label_mass=None, ood=ood < OOD_FLOOR))
        return ScoreRes(rows=rows, exec_used=None,
                        engine_ms=(time.perf_counter() - t0) * 1000, model_id="linear/1")
