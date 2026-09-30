"""`openjev_head`: Open-Jev-27B-v1.1's scalar decision head, run LOCALLY.

Open-Jev (ZefanCai/Open-Jev-27B-v1.1, Apache-2.0) is a LoRA (r8/α16 on
q,k,v,o_proj + the Gated-DeltaNet in_proj_qkv/out_proj) plus an FP32 scalar
head and a saved temperature, on the Qwen3.8-27B text backbone. It scores
caller-supplied candidates without generating: a yes/no ("noul") question
gets one probability, a choice question a distribution over its options.
It needs HF BF16 weights (~54 GB) and its own loader (torch + peft), so it
never runs inside the rig's llama.cpp stack: scripts/serve-openjev.sh starts
it on a DEDICATED GPU host (a Spark, or the 5090 once the Sparks carry
generation) in a pinned container, behind scripts/instinct/openjev_gate.py,
which adds the bearer key and the identity endpoint. Nothing here is cloud:
TypeSafe's hosted Jev API is not, and never will be, an engine.

Wire (the Open-Jev loader at the pinned commit, [DOC] jev/server.py, api.py):
  POST /v1/systemone {"state": <context>, "questions": {"d": {"type": "noul",
                      "instructions": <question>}}}
       -> {"answers": {"d": {"type": "noul", "noul": p_true}}}
  choice: {"type": "choice", "criteria": {label: description}}
       -> {"answers": {"d": {"probabilities": {label: p}}}}
  GET  /v1/identity (openjev_gate.py) -> {"model", "base_revision",
                      "adapter_revision", "head_sha256", "loader_digest"}
The probabilities already carry Open-Jev's saved temperature (2.534); our
calibration fits its own T on top, per decision_hash, as for every engine.

Mapping a decision: `state` = the filled, escaped template (the DATA), and
`instructions` = the spec's system text. yes_no decisions need label texts
"yes"/"no"; choice decisions send each label's desc (else its text). rank and
score decisions are refused at attach (label_lock_failed on this engine only).
There are no tokenizer label tokens: the "lock" is the fixed label->slot map,
and it still enters decision_hash.

[HW]/VERIFY: the adapter was trained on the STOCK Qwen/Qwen3.8-27B
(@1d4bf0f2…). abliteration edited o_proj (a LoRA target) and the residual
stream the head reads, so on the uncensored base it must be re-validated with
evals/decisions (plan revision 2026-09-30) before any calibration counts —
until then it can only shadow (no gate record = no enforce, by construction).
"""
from __future__ import annotations

import math

from ..render import Rendered
from ..spec import DecisionSpec
from . import Caps, EngineError, LockResult, ScoreReq, ScoreRes, ScoreRow
from ._llm import LLMEngine

QID = "d"


def label_slots(spec: DecisionSpec) -> dict[str, int] | None:
    """{label name: slot}; None when Open-Jev cannot answer this decision."""
    if spec.type == "yes_no":
        texts = {lb.name: lb.text.strip().lower() for lb in spec.labels}
        if sorted(texts.values()) != ["no", "yes"]:
            return None
        return {n: (1 if t == "yes" else 0) for n, t in texts.items()}
    if spec.type == "choice":
        return {lb.name: i for i, lb in enumerate(spec.labels)}
    return None


def _prob(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise EngineError("openjev: probability is not a number")
    v = float(v)
    if not math.isfinite(v) or v < 0.0 or v > 1.0 + 1e-6:
        raise EngineError("openjev: probability outside [0, 1]")
    return min(v, 1.0)


class OpenJevEngine(LLMEngine):
    adapter = "openjev_head"
    caps = Caps(probs=True, label_mass=False, mis=False, setwise=False, head=True,
                deterministic=True, max_items=1)

    def __init__(self, binding, **ctx):
        super().__init__(binding, **ctx)
        # label map -> (question type, choice criteria), filled by lock()
        self._kinds: dict[tuple, tuple[str, dict | None]] = {}

    # --- "lock": a fixed label -> slot map (no tokenizer labels exist) ---
    async def lock(self, spec: DecisionSpec) -> LockResult:
        slots = label_slots(spec)
        if slots is None:
            return LockResult(False, reason=f"label_lock_failed: openjev_head answers yes_no "
                                            f"(labels yes/no) and choice, not {spec.type}")
        crit = ({lb.name: (lb.desc or lb.text) for lb in spec.labels}
                if spec.type == "choice" else None)
        self._kinds[tuple(sorted(slots.items()))] = (
            "choice" if spec.type == "choice" else "noul", crit)
        return LockResult(True, slots)

    async def _tokenize(self, text: str) -> list[int]:  # pragma: no cover - never locked by tokens
        raise EngineError("openjev_head has no tokenizer endpoint")

    def _request(self, rendered: Rendered, label_ids: dict[str, int]) -> dict:
        if rendered.items or not rendered.body:
            raise EngineError("openjev_head scores one non-rank prompt per call")
        kind, crit = self._kinds.get(tuple(sorted(label_ids.items())), (None, None))
        if kind is None:
            raise EngineError("openjev_head: label map was never locked")
        q: dict = {"type": kind, "instructions": rendered.system}
        if crit is not None:
            q["criteria"] = crit
        return {"state": rendered.body, "questions": {QID: q}}

    async def _score_rendered(self, rendered: Rendered, label_ids: dict[str, int],
                              timeout_s: float):
        body = self._request(rendered, label_ids)
        resp = await self._post("/v1/systemone", body, timeout_s)
        try:
            ans = resp["answers"][QID]
        except (KeyError, TypeError):
            raise EngineError("openjev: answers.d missing") from None
        if not isinstance(ans, dict):
            raise EngineError("openjev: malformed answer")
        if body["questions"][QID]["type"] == "noul":
            p = _prob(ans.get("noul"))
            q = {n: (p if slot == 1 else 1.0 - p) for n, slot in label_ids.items()}
        else:
            probs = ans.get("probabilities")
            if not isinstance(probs, dict) or set(probs) != set(label_ids):
                raise EngineError("openjev: choice probabilities do not name the labels")
            q = {n: _prob(probs[n]) for n in label_ids}
        if sum(q.values()) <= 0:
            raise EngineError("openjev: all-zero distribution")
        # A head has no vocabulary, so no label_mass (decide_action skips the
        # mass check for head engines; nothing is truncated).
        return [ScoreRow(q=q, label_mass=None, truncated=[])], "sis", None

    async def score(self, req: ScoreReq) -> ScoreRes:
        if not req.label_ids:
            raise EngineError(f"{self.id}: no label map for {req.spec.id}")
        return await super().score(req)

    async def _identity(self) -> str | None:
        ident = await self._get("/v1/identity")
        if not isinstance(ident, dict):
            raise EngineError("/v1/identity: not an object")
        b = self.binding
        want = {"base_revision": b.model_revision, "adapter_revision": b.adapter_revision,
                "head_sha256": b.head_sha256}
        if b.loader_digest:
            want["loader_digest"] = b.loader_digest
        bad = [k for k, v in want.items() if v and ident.get(k) != v]
        if bad:
            # A different head/adapter/base under the same name is a different
            # engine: report it as an identity mismatch (the probe fails).
            return f"{ident.get('model')!r} with mismatched {','.join(bad)}"
        model = ident.get("model")
        return model if isinstance(model, str) else None
