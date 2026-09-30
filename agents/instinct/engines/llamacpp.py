"""`llamacpp_logprobs`: answer-boundary label scoring on llama-server (plan §5.6).

Wire (llama.cpp tools/server/README.md at our pinned tree — [DOC]):
  POST /tokenize   {"content": s, "add_special": false, "with_pieces": true}
                   -> {"tokens": [{"id": int, "piece": str}, ...]}  (or [int])
  POST /completion {"prompt": text, "n_predict": 1, "n_probs": K,
                    "temperature": -1, "cache_prompt": true, "stream": false}
                   -> completion_probabilities[0].top_logprobs[] =
                      {id, token, logprob, bytes}
  temperature < 0 makes n_probs "a simple softmax of the logits without
  considering any other sampler settings" — the pre-sampler, full-vocabulary
  top-K, which is what q_label must be. [HW] the exact shape at temperature
  -1 is confirmed on the P1 box.

A label outside the top-K gets q = min(top-K q) — an UPPER bound — and is
listed in `truncated`, which blocks `act`. label_mass sums only the labels
that were present (a lower bound when one is missing).

Rank sends N concurrent requests sharing a prefix (cache_prompt reuses the
prefix KV); exec_used = "sis".

On the PRIMARY (allow_primary + busy_skip — the rig-27b binding) every call
first asks GET /slots: a slot with is_processing means a user's turn or an
agent owns the only (-np 1) slot, and the call raises EngineBusy at once
rather than queueing behind it. /slots is on by default in llama-server
(serve.sh never passes --no-slots). A /slots that times out counts as busy;
one the server does not offer (404/501) lets the call proceed under its
deadline — slow, never wrong. [HW] the /slots latency while decoding.
MTP does not touch the answer token's probabilities: n_predict 1 stops
before any draft ([DOC] server-context.cpp at the pinned tree; [HW] M5).
"""
from __future__ import annotations

import asyncio
import math
import os

from ..render import Rendered
from . import Caps, EngineBusy, EngineError, ScoreRow
from ._llm import LLMEngine, token_ids


def parse_top_logprobs(resp: dict) -> dict[int, float]:
    """{token_id: prob} from a /completion response, validated."""
    try:
        cp = resp["completion_probabilities"]
        entries = cp[0]["top_logprobs"]
    except (KeyError, IndexError, TypeError):
        raise EngineError("completion_probabilities[0].top_logprobs missing") from None
    # Every shape check lives here: whatever the engine sends back, a bad
    # answer is an EngineError (-> fallback), never an exception that escapes.
    if not isinstance(entries, list):
        raise EngineError("top_logprobs is not a list")
    out: dict[int, float] = {}
    for e in entries:
        if not isinstance(e, dict):
            raise EngineError("malformed top_logprobs entry")
        try:
            if isinstance(e["id"], bool) or isinstance(e["logprob"], bool):
                raise TypeError("bool")
            tid = int(e["id"])
            lp = float(e["logprob"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise EngineError("malformed top_logprobs entry") from None
        if not math.isfinite(lp) or lp > 1e-6:
            raise EngineError("top_logprobs carries a non-log-probability")
        out[tid] = math.exp(min(0.0, lp))
    if not out:
        raise EngineError("empty top_logprobs")
    if sum(out.values()) > 1.0 + 1e-3:
        raise EngineError("top-K probabilities sum above 1 (not a distribution)")
    return out


def rows_from_topk(topk: dict[int, float], label_ids: dict[str, int]) -> ScoreRow:
    floor = min(topk.values())
    q: dict[str, float] = {}
    truncated: list[str] = []
    mass = 0.0
    for name, tid in label_ids.items():
        if tid in topk:
            q[name] = topk[tid]
            mass += topk[tid]
        else:
            q[name] = floor
            truncated.append(name)
    return ScoreRow(q=q, label_mass=mass, truncated=truncated)


class LlamaCppEngine(LLMEngine):
    adapter = "llamacpp_logprobs"
    caps = Caps(probs=True, label_mass=True, mis=False, setwise=False, head=False,
                deterministic=True, max_items=64)

    async def _tokenize_raw(self, text: str) -> list:
        path = self.binding.tokenize_path or "/tokenize"
        data = await self._post(path, {"content": text, "add_special": False,
                                       "with_pieces": True})
        toks = data.get("tokens") if isinstance(data, dict) else None
        if not isinstance(toks, list):
            raise EngineError("/tokenize: no tokens list")
        return toks

    async def _tokenize(self, text: str) -> list[int]:
        return token_ids(await self._tokenize_raw(text))

    async def _pieces(self, text: str) -> list[str] | None:
        try:
            toks = await self._tokenize_raw(text)
        except (EngineError, asyncio.TimeoutError):
            return None
        pieces = [t.get("piece") for t in toks if isinstance(t, dict)]
        if len(pieces) != len(toks) or not all(isinstance(p, str) for p in pieces):
            return None
        return pieces

    async def _one(self, prompt: str, label_ids: dict[str, int], timeout_s: float) -> ScoreRow:
        body = {"prompt": prompt, "n_predict": 1, "n_probs": int(self.binding.n_probs),
                "temperature": -1, "cache_prompt": True, "stream": False}
        resp = await self._post("/completion", body, timeout_s)
        return rows_from_topk(parse_top_logprobs(resp), label_ids)

    async def _score_rendered(self, rendered: Rendered, label_ids: dict[str, int],
                              timeout_s: float):
        rows = await asyncio.gather(*[self._one(p, label_ids, timeout_s)
                                      for p in rendered.prefixes])
        return list(rows), "sis", None

    async def ensure_idle(self, timeout_s: float = 0.05) -> None:
        if not self.binding.busy_skip:
            return
        try:
            slots = await self._get("/slots", max(0.001, timeout_s))
        except asyncio.TimeoutError:
            raise EngineBusy(f"{self.id}: /slots did not answer in {timeout_s * 1000:.0f} ms")
        except EngineError as exc:
            if "HTTP 404" in str(exc) or "HTTP 501" in str(exc):
                return   # no /slots on this server: proceed under the deadline
            raise
        if not isinstance(slots, list):
            raise EngineError(f"{self.id}: /slots is not a list")
        for sl in slots:
            if not isinstance(sl, dict):
                raise EngineError(f"{self.id}: malformed /slots entry")
            if sl.get("is_processing") is True or sl.get("state") not in (None, 0):
                raise EngineBusy(f"{self.id}: slot {sl.get('id')} is processing")

    async def _identity(self) -> str | None:
        props = await self._get("/props")
        if not isinstance(props, dict):
            raise EngineError("/props: not an object")
        alias = props.get("model_alias")
        path = props.get("model_path") or ""
        base = os.path.basename(path) if isinstance(path, str) else ""
        if self.binding.model and self.binding.model in (alias, base):
            return self.binding.model
        return alias or base or None
