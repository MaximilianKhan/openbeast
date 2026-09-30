"""`sglang_score`: SGLang /v1/score, SIS or MIS (plan §5.6, facts F1-F4).

Exact schema, SGLang main @ 3b537e96 (entrypoints/openai/protocol.py) [SRC]:
  ScoringRequest   query: str | list[int]; items: str | list[str] | list[list[int]];
                   label_token_ids: list[int] | list[list[int]]; apply_softmax: bool;
                   temperature: float (>0); return_token_logprobs; item_first;
                   return_pooled_hidden_states; score_extraction_token;
                   *_embed_overrides; model
  ScoringResponse  scores (2-D pointwise [rows][labels], 3-D setwise);
                   pooled_hidden_states; token_logprobs; model; usage;
                   object = "scoring"
F1: with apply_softmax:false, each score is exp(full-vocabulary logprob), so
    label_mass = Σ scores from ONE call; we renormalize ourselves.
F4: MIS packs query<d>item1<d>item2…<d>; item text must not contain the
    delimiter — the renderer escapes the binding's `mis_delimiter`.

Non-rank decisions send query = "" and items = [full prompt] (the documented
"complete prompt" convention; [HW] whether an empty query is accepted — the
binding can set tokenize_path etc., and the fallback is query = prompt with
one empty item). Rank decisions send ONE request: query = shared prefix,
items = per-item text. Tokenize: POST /tokenize {"text", "add_special_tokens":
false} -> {"tokens": [int]} — [HW] the request schema (the route exists, F3).
"""
from __future__ import annotations

import asyncio
import math

from ..render import Rendered, placeholder_inputs, render
from . import Caps, EngineError, ScoreRow
from ._llm import MIS_DELTA, MIS_PROBE_ITEMS, LLMEngine, generic_probe_spec, token_ids


def parse_scores(resp: dict, n_rows: int, n_labels: int) -> list[list[float]]:
    if not isinstance(resp, dict):
        raise EngineError("/v1/score: not an object")
    if resp.get("object", "scoring") != "scoring":
        raise EngineError("/v1/score: object is not 'scoring'")
    s = resp.get("scores")
    if not isinstance(s, list) or len(s) != n_rows:
        raise EngineError(f"/v1/score: expected {n_rows} score rows")
    out = []
    for row in s:
        if not isinstance(row, list) or len(row) != n_labels:
            raise EngineError("/v1/score: a row has the wrong number of labels "
                              "(setwise 3-D output is not a pointwise answer)")
        vals = []
        for v in row:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise EngineError("/v1/score: non-numeric score")
            if v < 0 or v > 1 + 1e-6:
                raise EngineError("/v1/score: score outside [0,1] (logits? check apply_softmax)")
            vals.append(float(v))
        if sum(vals) > 1 + 1e-3:
            raise EngineError("/v1/score: label scores sum above 1")
        out.append(vals)
    return out


class SGLangEngine(LLMEngine):
    adapter = "sglang_score"
    caps = Caps(probs=True, label_mass=True, mis=True, setwise=True, head=False,
                deterministic=False, max_items=64)

    def __init__(self, binding, **ctx):
        super().__init__(binding, **ctx)
        self.caps = Caps(probs=True, label_mass=True, mis=binding.exec == "mis",
                         setwise=True, deterministic=False, max_items=64)

    async def _tokenize(self, text: str) -> list[int]:
        path = self.binding.tokenize_path or "/tokenize"
        data = await self._post(path, {"text": text, "add_special_tokens": False})
        toks = None
        if isinstance(data, dict):
            toks = data.get("tokens", data.get("input_ids"))
        if not isinstance(toks, list):
            raise EngineError("/tokenize: no tokens list")
        return token_ids(toks)

    def _body(self, query, items: list[str], ids: list[int]) -> dict:
        body = {"query": query, "items": items, "label_token_ids": ids,
                "apply_softmax": False}
        if self.binding.model:
            body["model"] = self.binding.model
        return body

    async def _request(self, query, items: list[str], label_ids: dict[str, int],
                       timeout_s: float, url_override: str | None = None):
        names = list(label_ids)
        ids = [label_ids[n] for n in names]
        path = "/v1/score"
        if url_override:
            path = url_override.rstrip("/") + "/v1/score"
        resp = await self._post(path, self._body(query, items, ids), timeout_s)
        scores = parse_scores(resp, len(items), len(ids))
        rows = []
        for vals in scores:
            q = dict(zip(names, vals))
            rows.append(ScoreRow(q=q, label_mass=sum(vals), truncated=[]))
        return rows, resp.get("usage")

    async def _score_rendered(self, rendered: Rendered, label_ids: dict[str, int],
                              timeout_s: float):
        if rendered.items and self.exec_forced == "sis" and len(rendered.items) > 1:
            # SIS FORCED on a MIS server after the equivalence probe failed: a
            # batched request there IS MIS, so send one item per request, on
            # exactly the path the probe used as its SIS reference (sis_url
            # when set). What is ledgered and hashed as "sis" is then SIS.
            url = self.binding.sis_url or None
            parts = await asyncio.gather(*[
                self._request(rendered.query, [it], label_ids, timeout_s, url_override=url)
                for it in rendered.items])
            rows, usage = [r for rs, _ in parts for r in rs], None
        elif rendered.items:
            rows, usage = await self._request(rendered.query, rendered.items, label_ids,
                                              timeout_s)
        else:
            rows, usage = await self._request("", [rendered.prompt], label_ids, timeout_s)
        return rows, self.exec, usage

    async def _identity(self) -> str | None:
        data = await self._get("/v1/models")
        try:
            ids = [m["id"] for m in data["data"]]
        except (KeyError, TypeError):
            raise EngineError("/v1/models: unexpected shape") from None
        if self.binding.model and self.binding.model in ids:
            return self.binding.model
        return ids[0] if ids else None

    async def _mis_equivalence(self, checks: dict, timeout_s: float) -> str | None:
        """One 16-item request vs the same items one at a time (the SIS
        reference is `sis_url` when set — a second, non-MIS server)."""
        if self.binding.exec != "mis":
            return None
        spec = generic_probe_spec("qwen3-nothink/1")
        lk = await self.lock(spec)
        if not lk.ok:
            checks["mis_equivalence"] = f"skipped: {lk.reason}"
            return "sis"
        # A rank-shaped split of the generic prompt: query up to the data tag.
        r = render(spec, placeholder_inputs(spec))
        cut = r.prompt.index("<question>\n") + len("<question>\n")
        query = r.prompt[:cut]
        tail = r.prompt[cut:].split("\n</question>", 1)[1]
        items = [q + "\n</question>" + tail for q in MIS_PROBE_ITEMS]
        batch, _ = await self._request(query, items, lk.ids, timeout_s * 4)
        single = []
        for it in items:
            rows, _ = await self._request(query, [it], lk.ids, timeout_s,
                                          url_override=self.binding.sis_url or None)
            single.append(rows[0])

        def p(row):
            s = sum(row.q.values()) or 1.0
            return row.q["yes"] / s
        deltas = [abs(p(a) - p(b)) for a, b in zip(batch, single)]
        checks["mis_equivalence_max_delta"] = max(deltas)
        if max(deltas) > MIS_DELTA:
            checks["mis_equivalence"] = "FAILED: exec forced to sis"
            return "sis"
        checks["mis_equivalence"] = "ok"
        return None
