"""Shared machinery for LLM scoring adapters: label locks + conformance.

Label lock (plan §5.4), per decision and engine:
  ids_pre = tokenize(prefix); ids_ctx = tokenize(prefix + label_surface)
  assert ids_ctx[:len(ids_pre)] == ids_pre and exactly one id was added.
That one id is the lock; locks must be pairwise distinct; they enter
decision_hash. A failed lock disables that decision on this engine only.

Conformance probe (plan §5.7):
  identity     /props or /v1/models model id matches the binding (port squatter)
  tokenization the generic yes/no probe labels lock
  known answer "Is water wet?" -> yes, "Is fire cold?" -> no must separate by
               >= 0.5 with label_mass >= 0.6; plus each decision's
               [probe].known pairs (margin >= 0.5 for the expected label)
  replay       10 repeats: std(p) <= 0.02, else `nondeterministic` (a flag
               that widens fitted thresholds, not a failure)
  SIS/MIS      (sglang exec=mis) one 16-item request vs 16 single requests,
               |Δp| <= 0.05 per item, else exec is forced to sis
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from typing import Any

import httpx

from ..render import Rendered, placeholder_inputs, render
from ..spec import DecisionSpec, parse_spec
from . import (Engine, EngineBusy, EngineError, LockResult, ProbeResult, ScoreReq, ScoreRes,
               ScoreRow, read_key_file)

KNOWN_MARGIN = 0.5
KNOWN_MASS = 0.6
REPLAY_N = 10
REPLAY_N_PRIMARY = 3
REPLAY_STD = 0.02
MIS_DELTA = 0.05


def token_ids(toks: list) -> list[int]:
    """Token ids from a /tokenize `tokens` list: ints, or {"id": int, ...}.
    Any other shape is an EngineError — the /tokenize schema is an [HW]
    unknown on some engines, and an unexpected one must disable the decision
    on that engine, never raise out of attach/start."""
    out = []
    for t in toks:
        v = t.get("id") if isinstance(t, dict) else t
        if isinstance(v, bool) or not isinstance(v, int):
            raise EngineError("/tokenize: unexpected token entry")
        out.append(v)
    return out


def generic_probe_spec(fmt: str) -> DecisionSpec:
    return parse_spec({
        "id": "instinct.probe", "version": 1, "owner": "agents/instinct",
        "description": "generic known-answer probe", "type": "yes_no", "form": "pointwise",
        "labels": [{"name": "yes", "text": "yes"}, {"name": "no", "text": "no"}],
        "prompt": {"format": fmt,
                   "system": "Answer the question with exactly one word: yes or no.",
                   "template": "<question>\n{question}\n</question>\nAnswer yes or no."},
        "inputs": {"question": {"type": "text", "max_tokens": 64, "truncate": "head_tail:32:32"}},
        "engines": {"chain": ["probe"]},
        "policy": {"mode": "off", "act": {}, "min_label_mass": 0.0, "deadline_ms": 1000},
        "gate": {"criteria": [{"metric": "accuracy", "split": "test", "op": ">=", "value": 0}],
                 "min_n": {}, "shadow": {"min_decisions": 0, "min_days": 0}},
    })


GENERIC_YES = "Is water wet?"
GENERIC_NO = "Is fire cold?"
MIS_PROBE_ITEMS = [
    "Is water wet?", "Is fire cold?", "Is the sun hot?", "Is ice warm?",
    "Do fish swim?", "Can rocks sing?", "Is snow white?", "Is coal white?",
    "Do birds fly?", "Is night bright?", "Is sugar sweet?", "Is salt sweet?",
    "Do trees grow?", "Can stones swim?", "Is rain wet?", "Is sand liquid?",
]


class LLMEngine(Engine):
    """Subclasses implement _tokenize, _score_rendered and _identity."""

    def __init__(self, binding, **ctx):
        super().__init__(binding, **ctx)
        self._client: httpx.AsyncClient | None = None
        self._key_error: str | None = None
        self.exec_forced: str | None = None

    @property
    def exec(self) -> str | None:
        return self.exec_forced or self.binding.exec

    def hash_identity(self) -> dict:
        ident = self.binding.hash_identity()
        ident["exec"] = self.exec
        return ident

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.binding.key_env:
            # The primary's key (LLAMA_API_KEY) reaches the service through its
            # environment — instinct.sh resolves it the way conf.sh does and
            # never puts it on argv. Unset/empty = the primary needs no key.
            key = os.environ.get(self.binding.key_env, "").strip()
            if key:
                h["Authorization"] = f"Bearer {key}"
            return h
        if self.binding.key_file:
            try:
                key = read_key_file(self.binding.key_file)
            except (OSError, EngineError) as exc:
                self._key_error = str(exc)
                raise EngineError(f"engine key unavailable: {exc}") from None
            if key:
                h["Authorization"] = f"Bearer {key}"
        return h

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            transport = self.ctx.get("transport")
            self._client = httpx.AsyncClient(
                base_url=self.binding.url.rstrip("/"), transport=transport,
                timeout=httpx.Timeout(self.binding.timeout_ms / 1000.0),
                trust_env=False)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, body: dict, timeout_s: float | None = None) -> Any:
        try:
            r = await self.client().post(path, content=json.dumps(body), headers=self._headers(),
                                         timeout=timeout_s)
        except httpx.TimeoutException:
            raise asyncio.TimeoutError(f"{self.id}: {path} timed out") from None
        except httpx.HTTPError as exc:
            raise EngineError(f"{self.id}: {path}: {exc.__class__.__name__}") from None
        if r.status_code != 200:
            raise EngineError(f"{self.id}: {path} -> HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise EngineError(f"{self.id}: {path} returned non-JSON") from None

    async def _get(self, path: str, timeout_s: float | None = None) -> Any:
        try:
            kw = {"timeout": timeout_s} if timeout_s is not None else {}
            r = await self.client().get(path, headers=self._headers(), **kw)
        except httpx.TimeoutException:
            raise asyncio.TimeoutError(f"{self.id}: GET {path} timed out") from None
        except httpx.HTTPError as exc:
            raise EngineError(f"{self.id}: GET {path}: {exc.__class__.__name__}") from None
        if r.status_code != 200:
            raise EngineError(f"{self.id}: GET {path} -> HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise EngineError(f"{self.id}: GET {path} returned non-JSON") from None

    # --- subclass hooks ---
    async def _tokenize(self, text: str) -> list[int]:  # pragma: no cover
        raise NotImplementedError

    async def _pieces(self, text: str) -> list[str] | None:
        return None

    async def _score_rendered(self, rendered: Rendered, label_ids: dict[str, int],
                              timeout_s: float) -> tuple[list[ScoreRow], str, dict | None]:
        raise NotImplementedError  # pragma: no cover

    async def _identity(self) -> str | None:  # pragma: no cover
        raise NotImplementedError

    async def ensure_idle(self, timeout_s: float = 0.05) -> None:
        """busy_skip bindings only: raise EngineBusy when the engine's slot is
        serving someone. Other engines are never busy (they are ours)."""
        return None

    # --- label lock ---
    async def lock(self, spec: DecisionSpec) -> LockResult:
        try:
            items = ["hello"] if spec.type == "rank" else None
            r = render(spec, placeholder_inputs(spec), items,
                       mis_delimiter=self.binding.mis_delimiter)
            prefix = r.prefixes[0]
            ids_pre = await self._tokenize(prefix)
            locks: dict[str, int] = {}
            for lb in spec.labels:
                ids_ctx = await self._tokenize(prefix + r.label_surface[lb.name])
                if ids_ctx[:len(ids_pre)] != ids_pre:
                    return LockResult(False, reason=f"label_lock_failed: {lb.name} merges "
                                                    "with the prefix")
                if len(ids_ctx) - len(ids_pre) != 1:
                    return LockResult(False, reason=f"label_lock_failed: {lb.name} is "
                                                    f"{len(ids_ctx) - len(ids_pre)} tokens")
                locks[lb.name] = ids_ctx[-1]
            if len(set(locks.values())) != len(locks):
                return LockResult(False, reason="label_lock_failed: labels share a token")
            return LockResult(True, locks)
        except (EngineError, asyncio.TimeoutError, ValueError) as exc:
            return LockResult(False, reason=f"label_lock_failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - a lock failure disables ONE decision
            return LockResult(False, reason=f"label_lock_failed: {exc.__class__.__name__}: "
                                            f"{str(exc)[:120]}")

    async def attach(self, specs: list[DecisionSpec]) -> dict[str, LockResult]:
        return {s.id: await self.lock(s) for s in specs}

    # --- scoring ---
    async def _truncating_splitter(self, spec: DecisionSpec, inputs: dict):
        """Truncate by the ENGINE's tokenizer where it can give pieces."""
        cache: dict[str, list[str]] = {}
        for name, ispec in spec.inputs.items():
            if ispec.type == "text":
                v = inputs[name]
                pieces = await self._pieces(v)
                if pieces is not None and "".join(pieces) == v:
                    cache[v] = pieces
        if not cache:
            return None
        from ..render import whitespace_pieces
        return lambda text: cache.get(text) or whitespace_pieces(text)

    async def score(self, req: ScoreReq) -> ScoreRes:
        if not req.label_ids:
            raise EngineError(f"{self.id}: no label lock for {req.spec.id}")
        # Before anything else: a busy primary costs ~1 ms, not a deadline. The
        # check is not engine latency, and a busy call records none at all,
        # so p95 (the deadline-skip input) measures an idle slot only.
        await self.ensure_idle(min(0.05, max(0.001, req.deadline_s)))
        t0 = time.perf_counter()
        splitter = await self._truncating_splitter(req.spec, req.inputs)
        rendered = render(req.spec, req.inputs, req.items, splitter=splitter,
                          mis_delimiter=self.binding.mis_delimiter)
        rows, exec_used, usage = await self._score_rendered(rendered, req.label_ids,
                                                            max(0.001, req.deadline_s))
        ms = (time.perf_counter() - t0) * 1000
        self.record_latency(ms)
        return ScoreRes(rows=rows, exec_used=exec_used, engine_ms=ms,
                        model_id=self.binding.model or None, usage=usage)

    # --- conformance ---
    async def _p_yes(self, spec: DecisionSpec, locks: dict[str, int], question: str,
                     timeout_s: float) -> tuple[float, float | None]:
        r = render(spec, {"question": question})
        await self.ensure_idle()   # a probe never queues in front of a user
        t0 = time.perf_counter()
        rows, _, _ = await self._score_rendered(r, locks, timeout_s)
        # Probe calls seed the rolling latency window, so a fresh engine's p95
        # is measured rather than assumed to be its (larger) timeout.
        self.record_latency((time.perf_counter() - t0) * 1000)
        row = rows[0]
        if row.truncated:
            return 0.5, row.label_mass
        s = sum(row.q.values()) or 1.0
        return row.q["yes"] / s, row.label_mass

    async def probe(self, specs: list[DecisionSpec]) -> ProbeResult:
        checks: dict[str, Any] = {}
        t_s = self.binding.timeout_ms / 1000.0
        try:
            model_id = await self._identity()
            checks["identity"] = model_id
            if self.binding.model and model_id != self.binding.model:
                return ProbeResult(False, checks, f"identity: serving {model_id!r}, "
                                                  f"binding wants {self.binding.model!r}")
            fmts = sorted({s.prompt_format for s in specs}) or ["plain/1"]
            nondet = False
            for fmt in fmts:
                g = generic_probe_spec(fmt)
                lk = await self.lock(g)
                checks[f"lock[{fmt}]"] = lk.ok
                if not lk.ok:
                    return ProbeResult(False, checks, f"tokenization: {lk.reason}")
                py, my = await self._p_yes(g, lk.ids, GENERIC_YES, t_s)
                pn, mn = await self._p_yes(g, lk.ids, GENERIC_NO, t_s)
                checks[f"known[{fmt}]"] = {"p_yes_water": py, "p_yes_fire": pn,
                                           "mass": [my, mn]}
                if py - pn < KNOWN_MARGIN:
                    return ProbeResult(False, checks, "known_answer: no separation")
                if my is not None and mn is not None and min(my, mn) < KNOWN_MASS:
                    return ProbeResult(False, checks, "known_answer: label_mass below 0.6")
                # A primary gets 3 replays, not 10: every probe call on it
                # swaps the user's conversation out of its only slot.
                n_rep = REPLAY_N_PRIMARY if self.binding.busy_skip else REPLAY_N
                reps = [(await self._p_yes(g, lk.ids, GENERIC_YES, t_s))[0]
                        for _ in range(n_rep)]
                sd = statistics.pstdev(reps)
                checks[f"replay_std[{fmt}]"] = sd
                nondet = nondet or sd > REPLAY_STD
            for s in specs:
                if not s.probe_known:
                    continue
                lk = await self.lock(s)
                if not lk.ok:
                    continue  # reported per decision via attach
                for i, k in enumerate(s.probe_known):
                    r = render(s, k["inputs"])
                    await self.ensure_idle()
                    rows, _, _ = await self._score_rendered(r, lk.ids, t_s)
                    q = rows[0].q or {}
                    tot = sum(q.values()) or 1.0
                    p = {n: v / tot for n, v in q.items()}
                    other = max(v for n, v in p.items() if n != k["label"])
                    checks[f"known[{s.id}#{i}]"] = p
                    if p.get(k["label"], 0.0) - other < KNOWN_MARGIN:
                        return ProbeResult(False, checks, f"known_answer: {s.id} pair {i}")
            forced = await self._mis_equivalence(checks, t_s)
            return ProbeResult(True, checks, None, nondeterministic=nondet, exec_forced=forced)
        except EngineBusy:
            raise   # deferred, not failed: the service keeps the last result
        except (EngineError, asyncio.TimeoutError, KeyError, ValueError, TypeError,
                ZeroDivisionError, AttributeError, IndexError) as exc:
            return ProbeResult(False, checks, f"probe error: {exc.__class__.__name__}: {exc}")

    async def _mis_equivalence(self, checks: dict, timeout_s: float) -> str | None:
        return None


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")
