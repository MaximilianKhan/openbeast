"""`rules`: today's deterministic behaviour, as an engine (plan §5.6).

It is the universal fallback and the incumbent every other engine must beat.
It returns one-hot rows, has no probabilities (caps.probs = False), and so can
never be calibrated — I6: it can never be in enforce for a decision whose
policy thresholds a probability.

router_hints   a COPY of router._HINTS (agents/router.py). The copy is on
               purpose — agents/router.py must not become an import of
               instinct's engine layer — and tests/test_instinct_boundaries.py
               asserts the pattern strings are equal so the two cannot drift.
               No hint -> one-hot `inline` (today: pass straight through).
               Hint    -> `defer` (today: the legacy generative classify runs);
                          for evaluation the row is one-hot `spawn`, i.e. the
                          regex read as a classifier.
hydra_static   mechanical vision / long_context from the request facts; bulk
               for non-stream batch clients; else code_agent when tools are
               present, else chat.
"""
from __future__ import annotations

import re
import time

from . import Caps, Engine, ScoreReq, ScoreRes, ScoreRow

ROUTER_HINTS_PATTERN = (
    r"\b(agents?|background|spawn|launch|kick[ -]?off|autonomous|delegate|"
    r"in parallel|meanwhile|report back|check back|don'?t (wait|block))\b")
ROUTER_HINTS = re.compile(ROUTER_HINTS_PATTERN, re.IGNORECASE)

DEFAULT_LONG_CONTEXT_TOKENS = 32000


def _one_hot(names: list[str], hot: str) -> dict[str, float]:
    return {n: (1.0 if n == hot else 0.0) for n in names}


def mechanical_label(spec, inputs: dict) -> str | None:
    """The label a decision's rules FORCE from facts, or None (hydra_static)."""
    if spec.rule_set != "hydra_static":
        return None
    names = spec.label_names
    if "vision" in names and inputs.get("has_images") is True:
        return "vision"
    limit = spec.rule_params.get("long_context_tokens", DEFAULT_LONG_CONTEXT_TOKENS)
    est = inputs.get("est_prompt_tokens")
    if "long_context" in names and isinstance(est, int) and est > limit:
        return "long_context"
    return None


class RulesEngine(Engine):
    adapter = "rules"
    caps = Caps(probs=False, label_mass=False, needs_render=False, max_items=256)

    async def score(self, req: ScoreReq) -> ScoreRes:
        t0 = time.perf_counter()
        spec = req.spec
        names = spec.label_names
        rows: list[ScoreRow] = []
        if spec.type == "rank":
            rows = [ScoreRow(q=None, defer=True) for _ in (req.items or [])]
        elif spec.rule_set == "router_hints":
            text = " ".join(str(v) for v in req.inputs.values() if isinstance(v, str))
            if ROUTER_HINTS.search(text):
                hot = "spawn" if "spawn" in names else names[0]
                rows = [ScoreRow(q=_one_hot(names, hot), defer=True)]
            else:
                hot = "inline" if "inline" in names else names[-1]
                rows = [ScoreRow(q=_one_hot(names, hot))]
        elif spec.rule_set == "hydra_static":
            mech = mechanical_label(spec, req.inputs)
            inp = req.inputs
            if mech:
                hot = mech
            elif ("bulk" in names and inp.get("stream") is False
                  and inp.get("client_class") == "batch"):
                hot = "bulk"
            elif "code_agent" in names and inp.get("has_tools") is True:
                hot = "code_agent"
            else:
                hot = "chat" if "chat" in names else names[0]
            rows = [ScoreRow(q=_one_hot(names, hot), mechanical=mech)]
        else:
            rows = [ScoreRow(q=None, defer=True)]
        return ScoreRes(rows=rows, exec_used=None,
                        engine_ms=(time.perf_counter() - t0) * 1000, model_id="rules/1")
