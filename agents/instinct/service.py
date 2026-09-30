"""The instinct orchestrator: registry + engines + cascade + lifecycle + ledger.

Both the HTTP service (server.py) and the in-process library API
(`instinct.decide`) go through `Instinct.decide`, so there is exactly one
implementation of the rules that matter:

  * the caller cannot set the mode; it may pass a CEILING (min wins);
  * `enforce` is true only when the effective mode allows it AND the action
    is `act` — it is the only field a caller acts on;
  * only labels in policy.act can act (I4); an engine without probabilities
    can never be calibrated, so never enforce (I6);
  * every call writes exactly one ledger row (I5), whatever happened;
  * engine trouble is never an error to the caller — it is action=fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import calibrate
from .config import REPO_ROOT, ServiceConfig, effective_chain
from .core import (MODE_ORDER, Answer, build_answer, canary_bucket, decide_action, is_enforced,
                   min_mode)
from .engines import Engine, EngineError, LockResult, ProbeResult, ScoreReq, build_engine
from .engines.rules import mechanical_label
from .ledger import Ledger, excerpt, input_sha256
from .lifecycle import (AutoDemoter, Demotions, LifecycleInputs, effective_mode,
                        gate_record_ok, git_committed)
from .render import InputError, validate_inputs
from .spec import DecisionSpec, decision_hash, load_registry

SERVICE_VERSION = "0.1.0"
CONTRACTS = ["instinct/1", "instinct-route/1"]
TASK_CLASS = "hydra.task_class"

FALLBACK_REASONS = (
    "lifecycle_shadow", "lifecycle_off", "caller_ceiling", "canary_out", "uncalibrated",
    "no_gate", "engine_unavailable", "engine_timeout", "deadline", "overload",
    "label_lock_failed", "labels_truncated", "low_label_mass", "below_threshold",
    "not_act_label", "registry_invalid", "conformance_failed", "demoted", "eval_context",
    "ood_input")


class UnknownDecision(LookupError):
    pass


@dataclass
class EngineState:
    healthy: bool = False
    last_probe: float | None = None
    probe: ProbeResult | None = None
    locks: dict[str, LockResult] = field(default_factory=dict)


@dataclass
class Outcome:
    engine: str
    answer: Answer | None
    action: str
    reason: str | None
    items: list[dict] | None = None
    exec_used: str | None = None
    engine_ms: float = 0.0


def baseline_label(spec: DecisionSpec, baseline: Any) -> str | None:
    """The label today's behaviour chose, when the caller's baseline names one.
    The router sends "nohint"/"hint": no hint IS today's pass-through, i.e.
    `inline` (exactly what the router_hints rules engine answers); a hinted
    turn runs the legacy generative classify, whose verdict is unknown here
    (it arrives later through /feedback), so it has no label."""
    if not isinstance(baseline, str):
        return None
    if baseline in spec.label_names:
        return baseline
    if spec.rule_set == "router_hints" and baseline == "nohint" and "inline" in spec.label_names:
        return "inline"
    return None


def new_trace_id() -> str:
    return "ins_" + uuid.uuid4().hex[:20]


class Instinct:
    def __init__(self, cfg: ServiceConfig, *, engine_ctx: dict | None = None,
                 clock=time.monotonic, wall=time.time, repo_root: Path = REPO_ROOT):
        self.cfg = cfg
        self.clock, self.wall = clock, wall
        self.repo_root = Path(repo_root)
        self.ledger = Ledger(cfg.ledger_dir, retention_days=cfg.retention_days, clock=wall)
        self.demotions = Demotions(Path(cfg.state_dir) / "demoted.json")
        self.autodemoter = AutoDemoter(self.demotions)
        ctx = dict(engine_ctx or {})
        ctx.setdefault("records_dir", cfg.records_dir)
        self.engines: dict[str, Engine] = {n: build_engine(b, **ctx)
                                           for n, b in cfg.engines.items()}
        self.states: dict[str, EngineState] = {n: EngineState() for n in self.engines}
        self.specs: dict[str, DecisionSpec] = {}
        self.spec_errors: dict[str, str] = {}
        self.chains: dict[str, list[str]] = {}
        self.hashes: dict[tuple[str, str], str | None] = {}
        self.calib: dict[tuple[str, str], dict | None] = {}
        self.gates: dict[tuple[str, str], bool] = {}
        self._sem: asyncio.Semaphore | None = None
        self.shadow_inflight = 0

    # ------------------------------------------------------------------ setup
    async def start(self) -> None:
        await self.reload()
        await self.probe_all()

    async def aclose(self) -> None:
        for e in self.engines.values():
            await e.aclose()

    async def reload(self) -> None:
        specs, errors = load_registry(self.cfg.decisions_dir)
        chains: dict[str, list[str]] = {}
        for did, spec in list(specs.items()):
            chain = effective_chain(spec.chain, self.cfg)
            missing = [e for e in chain if e not in self.engines]
            if missing:
                bad = {m: self.cfg.engine_errors.get(m, "not configured") for m in missing}
                # A missing engine drops out of the chain; a decision with no
                # engine left is invalid. Both are visible on /decisions.
                chain = [e for e in chain if e in self.engines]
                errors.setdefault(f"{did}#engines", json.dumps(bad, sort_keys=True))
            for e in chain:
                b = self.cfg.engines[e]
                if b.allow_primary and not spec.policy.async_only:
                    chain = [x for x in chain if x != e]
                    errors.setdefault(f"{did}#engines", f"{e} is the primary; decision "
                                      "is not async_only")
            if not chain:
                errors[did] = "no usable engine in chain"
                specs.pop(did)
                continue
            chains[did] = chain
        self.specs, self.spec_errors, self.chains = specs, errors, chains
        self.demotions.load()
        self.demotions.clear_auto()
        for name, eng in self.engines.items():
            relevant = [s for s in specs.values() if name in chains.get(s.id, [])]
            locks = await self._attach(eng, relevant) if relevant else {}
            self.states[name].locks = locks
            if eng.caps.needs_render:
                self._write_locks(name, locks)
            if not eng.caps.needs_render:
                self.states[name].healthy = True
        self.refresh_records()

    @staticmethod
    async def _attach(eng: Engine, specs: list[DecisionSpec]) -> dict[str, LockResult]:
        """Engine.attach, contained: whatever an engine does wrong (an
        unexpected /tokenize shape, a squatter), the result is a failed lock
        for THIS engine's decisions — never an exception out of start/reload."""
        try:
            locks = await eng.attach(specs)
            if not isinstance(locks, dict):
                raise TypeError("attach returned no lock map")
        except Exception as exc:  # noqa: BLE001
            why = f"label_lock_failed: attach crashed: {exc.__class__.__name__}"
            return {s.id: LockResult(False, reason=why) for s in specs}
        return {s.id: locks.get(s.id) or LockResult(False, reason="label_lock_failed: no lock")
                for s in specs}

    def _write_locks(self, engine: str, locks: dict[str, LockResult]) -> None:
        d = Path(self.cfg.state_dir) / "locks"
        try:
            d.mkdir(parents=True, exist_ok=True)
            for did, lk in locks.items():
                p = d / f"{did}@{engine}.json"
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as fh:
                    json.dump({"decision": did, "engine": engine, "ok": lk.ok,
                               "ids": lk.ids, "reason": lk.reason}, fh, sort_keys=True)
        except OSError:
            pass

    def engine_hash(self, did: str, name: str) -> str | None:
        spec, eng = self.specs[did], self.engines[name]
        lk = self.states[name].locks.get(did)
        if eng.caps.needs_render:
            if lk is None or not lk.ok:
                return None
            return decision_hash(spec, eng.hash_identity(), lk.ids)
        return decision_hash(spec, eng.hash_identity(), None)

    def refresh_records(self) -> None:
        self.hashes.clear()
        self.calib.clear()
        self.gates.clear()
        for did, chain in self.chains.items():
            for name in chain:
                h = self.engine_hash(did, name)
                self.hashes[(did, name)] = h
                if h is None:
                    self.calib[(did, name)] = None
                    self.gates[(did, name)] = False
                    continue
                cpath = calibrate.calib_path(self.cfg.records_dir, did, h)
                crec = calibrate.load_record(cpath, h)
                if crec is not None:
                    crec["_sha256"] = calibrate.file_sha256(cpath)
                    eng = self.engines[name]
                    if eng.adapter == "linear":
                        # retraining without recalibrating must not stay calibrated
                        want = crec.get("model_file_sha256")
                        if not want or want != getattr(eng, "model_sha", {}).get(did):
                            crec = None
                    if crec is not None and not isinstance(crec.get("T"), (int, float)):
                        crec = None
                self.calib[(did, name)] = crec
                gpath = calibrate.gate_path(self.cfg.records_dir, did, h)
                grec = calibrate.load_record(gpath, h)
                ok = gate_record_ok(grec, h, crec)
                if ok and self.cfg.require_committed_gate:
                    ok = git_committed(gpath, self.repo_root)
                self.gates[(did, name)] = ok

    async def probe_all(self) -> None:
        changed = False
        for name, eng in self.engines.items():
            relevant = [s for s in self.specs.values() if name in self.chains.get(s.id, [])]
            if not eng.caps.needs_render:
                self.states[name].healthy = True
                self.states[name].last_probe = self.clock()
                continue
            if not relevant:
                continue
            try:
                res = await eng.probe(relevant)
            except Exception as exc:  # a probe must never take the service down
                res = ProbeResult(False, {}, f"probe crashed: {exc.__class__.__name__}")
            st = self.states[name]
            st.probe, st.healthy, st.last_probe = res, res.ok, self.clock()
            forced = res.exec_forced if res.ok else None
            if getattr(eng, "exec_forced", None) != forced and hasattr(eng, "exec_forced"):
                eng.exec_forced = forced
                changed = True
            if res.ok and any(not lk.ok for lk in st.locks.values()):
                st.locks = await self._attach(eng, relevant)
                self._write_locks(name, st.locks)
                changed = True
        if changed:
            self.refresh_records()

    # ---------------------------------------------------------------- helpers
    def _sem_get(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.cfg.max_concurrency))
        return self._sem

    def _probe_fresh(self, name: str) -> bool:
        st = self.states[name]
        if not self.engines[name].caps.needs_render:
            return True
        if st.last_probe is None:
            return False
        interval = self.cfg.probe_interval_s
        return interval <= 0 or (self.clock() - st.last_probe) <= 2 * interval

    def lifecycle(self, did: str, name: str, ceiling: str) -> tuple[str, str | None]:
        spec, eng = self.specs[did], self.engines[name]
        return effective_mode(LifecycleInputs(
            spec_mode=spec.policy.mode,
            gate_ok=self.gates.get((did, name), False),
            calib_ok=self.calib.get((did, name)) is not None,
            engine_healthy=self.states[name].healthy,
            probe_fresh=self._probe_fresh(name),
            probabilistic=eng.caps.probs,
            demoted=self.demotions.reason(did),
            ceiling=ceiling))

    def _thresholds(self, did: str, name: str, crec: dict | None) -> dict | None:
        if not crec:
            return None
        th = dict(crec.get("thresholds") or {})
        st = self.states[name]
        if st.probe is not None and st.probe.nondeterministic:
            sd = max((v for k, v in st.probe.checks.items() if k.startswith("replay_std")
                      and isinstance(v, (int, float))), default=0.0)
            th = {k: (min(1.0, v + 2 * sd) if isinstance(v, (int, float)) else v)
                  for k, v in th.items()}
        return th

    # ---------------------------------------------------------------- cascade
    async def _evaluate(self, spec: DecisionSpec, name: str, res, items_in) -> Outcome:
        eng = self.engines[name]
        crec = self.calib.get((spec.id, name))
        calibrated = bool(crec) and eng.caps.probs
        T = float(crec["T"]) if calibrated else None
        thresholds = self._thresholds(spec.id, name, crec) if calibrated else None

        def one(row) -> tuple[Answer, str, str | None]:
            mech_zero = None
            if spec.mechanical:
                mech_zero = [m for m in spec.mechanical if m != row.mechanical]
            ok_cal = calibrated and not row.defer
            try:
                ans = build_answer(spec, q=row.q, logits=row.logits, label_mass=row.label_mass,
                                   truncated=row.truncated, temperature=T, calibrated=ok_cal,
                                   mechanical_zero=mech_zero)
            except ValueError:
                raise EngineError("engine returned an all-zero label distribution") from None
            ans.mechanical = bool(row.mechanical)
            action, reason = decide_action(spec, ans, thresholds=thresholds,
                                           head_engine=eng.caps.head, ood=row.ood)
            return ans, action, reason

        if spec.type != "rank":
            ans, action, reason = one(res.rows[0])
            return Outcome(name, ans, action, reason, None, res.exec_used, res.engine_ms)
        if len(res.rows) != len(items_in):
            raise EngineError("rank: engine returned the wrong number of rows")
        out_items, reasons = [], []
        pos = spec.positive
        for it, row in zip(items_in, res.rows):
            ans, action, reason = one(row)
            p = ans.probabilities.get(pos) if ans.probabilities else None
            out_items.append({"id": it["id"], "p": p, "label_mass": row.label_mass,
                              "action": action})
            if reason in ("uncalibrated", "labels_truncated", "low_label_mass", "ood_input"):
                reasons.append(reason)
        if any(i["p"] is None for i in out_items):
            action, reason = "abstain", "uncalibrated"
        elif reasons:
            action, reason = "abstain", reasons[0]
        else:
            action, reason = "act", None
        return Outcome(name, None, action, reason, out_items, res.exec_used, res.engine_ms)

    async def _cascade(self, spec: DecisionSpec, inputs: dict, items_in: list[dict] | None,
                       deadline_at: float, ceiling: str = "enforce"
                       ) -> tuple[Outcome | None, list[dict], str | None]:
        """Walk the chain under the deadline (plan §5.5).

        The walk stops at the first `act` whose engine's lifecycle reaches the
        request's target mode (min of spec mode and ceiling): an `act` from an
        engine that could only ever shadow (no gate yet — `linear` as the
        McNemar baseline, typically) must not hide a later engine that can
        enforce. With no such act, the answer is the first act (it is what
        the service WOULD have done), else the last result from a
        probabilistic engine, else `rules` — so shadow rows carry the model's
        distribution, not rules' one-hot. Every attempted engine's own view
        (label, probabilities, label_mass) is kept in its cascade entry.
        """
        cascade: list[dict] = []
        final: Outcome | None = None
        results: list[Outcome] = []
        target = min_mode(spec.policy.mode, ceiling)
        last_err: str | None = None
        chain = list(self.chains[spec.id])
        if spec.mechanical and mechanical_label(spec, inputs):
            # Mechanical facts are computed, never judged: only rules answer.
            chain = [e for e in chain if self.engines[e].adapter == "rules"]
            if not chain and "rules" in self.engines:
                chain = ["rules"]
        texts = [it["text"] for it in items_in] if items_in is not None else None
        for name in chain:
            eng, st = self.engines[name], self.states[name]
            is_rules = eng.adapter == "rules"
            remaining_ms = (deadline_at - self.clock()) * 1000
            lk = st.locks.get(spec.id)
            if eng.adapter != "rules" and (lk is None or not lk.ok):
                why = ("label_lock_failed" if lk is not None and lk.reason
                       and lk.reason.startswith("label_lock_failed") else "engine_unavailable")
                cascade.append({"engine": name, "action": "skipped", "reason": why, "ms": 0.0})
                last_err = last_err or why
                continue
            if not is_rules and not st.healthy:
                cascade.append({"engine": name, "action": "skipped",
                                "reason": "engine_unavailable", "ms": 0.0})
                last_err = last_err or "engine_unavailable"
                continue
            if not is_rules and remaining_ms < eng.p95_ms():
                cascade.append({"engine": name, "action": "skipped", "reason": "deadline",
                                "ms": 0.0})
                last_err = last_err or "deadline"
                continue
            t0 = self.clock()
            try:
                timeout = max(0.001, remaining_ms / 1000) if not is_rules else None
                req = ScoreReq(spec=spec, inputs=inputs, items=texts,
                               label_ids=lk.ids if lk else None,
                               deadline_s=max(0.001, remaining_ms / 1000))
                res = await asyncio.wait_for(eng.score(req), timeout=timeout)
                out = await self._evaluate(spec, name, res, items_in)
            except asyncio.TimeoutError:
                ms = (self.clock() - t0) * 1000
                eng.record_latency(ms)
                cascade.append({"engine": name, "action": "fallback",
                                "reason": "engine_timeout", "ms": round(ms, 3)})
                last_err = "engine_timeout"
                continue
            except (EngineError, InputError, OSError) as exc:
                cascade.append({"engine": name, "action": "fallback",
                                "reason": "engine_unavailable", "ms": round(
                                    (self.clock() - t0) * 1000, 3),
                                "error": str(exc)[:200]})
                last_err = "engine_unavailable"
                continue
            except Exception as exc:  # noqa: BLE001 - engine trouble is a fallback, never a 500
                cascade.append({"engine": name, "action": "fallback",
                                "reason": "engine_unavailable", "ms": round(
                                    (self.clock() - t0) * 1000, 3),
                                "error": f"{exc.__class__.__name__}: {str(exc)[:160]}"})
                last_err = "engine_unavailable"
                continue
            entry = {"engine": name, "action": out.action, "reason": out.reason,
                     "ms": round(out.engine_ms, 3)}
            if out.answer is not None and out.answer.confidence:
                # per-engine view for shadow analysis (the final answer may be
                # a later engine's)
                entry["label"] = out.answer.label
                entry["p_top"] = round(out.answer.confidence["p_top"], 6)
                if eng.caps.probs:
                    entry["probabilities"] = {k: round(v, 6) for k, v in
                                              (out.answer.probabilities or {}).items()}
                    entry["label_mass"] = out.answer.label_mass
            cascade.append(entry)
            results.append(out)
            if out.action == "act":
                mode, _ = self.lifecycle(spec.id, name, ceiling)
                entry["mode"] = mode
                if MODE_ORDER[mode] >= MODE_ORDER[target]:
                    final = out
                    break
        if final is None and results:
            acts = [o for o in results if o.action == "act"]
            probs = [o for o in results if self.engines[o.engine].caps.probs]
            final = acts[0] if acts else (probs[-1] if probs else results[-1])
        return final, cascade, last_err

    # ----------------------------------------------------------------- decide
    def _validate_items(self, spec: DecisionSpec, items: Any) -> list[dict] | None:
        if spec.type != "rank":
            if items is not None:
                raise InputError("items are only valid for rank decisions")
            return None
        if not isinstance(items, list) or not items:
            raise InputError("rank decisions need a non-empty items list")
        if len(items) > spec.rank_max_items:
            raise InputError(f"at most {spec.rank_max_items} items")
        ids = set()
        out = []
        for it in items:
            if not isinstance(it, dict) or set(it) - {"id", "text"} or not isinstance(
                    it.get("id"), str) or not isinstance(it.get("text"), str):
                raise InputError("items must be [{id: str, text: str}]")
            if it["id"] in ids:
                raise InputError(f"duplicate item id {it['id']!r}")
            ids.add(it["id"])
            out.append({"id": it["id"], "text": it["text"]})
        return out

    async def decide(self, req: dict[str, Any]) -> dict[str, Any]:
        t_start = self.clock()
        did = req["decision"]
        spec = self.specs.get(did)
        trace_id = new_trace_id()
        ceiling = req.get("ceiling") or "enforce"
        context = req.get("context") or {}
        if spec is None:
            if did in self.spec_errors:
                return self._respond(None, req, trace_id, t_start, outcome=None, cascade=[],
                                     mode=min_mode("shadow", ceiling), reason="registry_invalid")
            raise UnknownDecision(did)
        inputs = validate_inputs(spec, req.get("inputs"))
        items_in = self._validate_items(spec, req.get("items"))
        deadline_ms = min(int(req.get("deadline_ms") or spec.policy.deadline_ms),
                          spec.policy.deadline_ms)
        target = min_mode(spec.policy.mode, ceiling)
        common = dict(spec=spec, req=req, trace_id=trace_id, t_start=t_start, inputs=inputs,
                      items_in=items_in, deadline_ms=deadline_ms)
        if any(context.get(c) is True for c in spec.forbidden_contexts):
            return self._respond(outcome=None, cascade=[], mode=target, reason="eval_context",
                                 **common)
        if spec.policy.mode == "off":
            return self._respond(outcome=None, cascade=[], mode="off", reason="lifecycle_off",
                                 **common)
        shadowish = target in ("off", "shadow")
        if shadowish and self.shadow_inflight >= self.cfg.shadow_queue:
            return self._respond(outcome=None, cascade=[], mode=target, reason="overload",
                                 **common)
        deadline_at = t_start + deadline_ms / 1000
        sem = self._sem_get()
        try:
            await asyncio.wait_for(sem.acquire(), timeout=max(0.0, deadline_at - self.clock()))
        except asyncio.TimeoutError:
            return self._respond(outcome=None, cascade=[], mode=target, reason="overload",
                                 **common)
        t_queue = self.clock()
        if shadowish:
            self.shadow_inflight += 1
        try:
            outcome, cascade, err = await self._cascade(spec, inputs, items_in, deadline_at,
                                                        ceiling)
        finally:
            sem.release()
            if shadowish:
                self.shadow_inflight -= 1
        if outcome is None:
            return self._respond(outcome=None, cascade=cascade, mode=target,
                                 reason=err or "engine_unavailable",
                                 queue_ms=(t_queue - t_start) * 1000, **common)
        mode, mode_reason = self.lifecycle(spec.id, outcome.engine, ceiling)
        return self._respond(outcome=outcome, cascade=cascade, mode=mode, reason=mode_reason,
                             queue_ms=(t_queue - t_start) * 1000, **common)

    def _respond(self, spec: DecisionSpec | None, req: dict, trace_id: str, t_start: float, *,
                 outcome: Outcome | None, cascade: list[dict], mode: str, reason: str | None,
                 inputs: dict | None = None, items_in: list | None = None,
                 deadline_ms: int | None = None, queue_ms: float = 0.0) -> dict:
        did = req["decision"]
        request_id = req.get("request_id")
        if outcome is None:
            action, enforce = "fallback", False
            fb_reason = reason or "engine_unavailable"
        else:
            action = outcome.action
            in_canary = canary_bucket(request_id, spec.policy.canary_pct) if spec else False
            enforce = is_enforced(mode, in_canary, action)
            if enforce:
                fb_reason = None
            elif action != "act":
                fb_reason = outcome.reason or "below_threshold"
            elif mode == "canary" and not in_canary:
                fb_reason = "canary_out"
            else:
                fb_reason = reason or "lifecycle_shadow"
        ans = outcome.answer if outcome else None
        eng_name = outcome.engine if outcome else None
        dhash = self.hashes.get((did, eng_name)) if eng_name else None
        eng_obj = None
        if eng_name:
            e = self.engines[eng_name]
            b = e.binding
            lk = self.states[eng_name].locks.get(did)
            eng_obj = {"id": eng_name, "adapter": e.adapter, "model": b.model or None,
                       "model_sha256": (b.model_sha256 or None) if e.caps.needs_render else None,
                       "exec": outcome.exec_used,
                       "label_token_ids": (lk.ids if (lk and lk.ids) else None)}
        answer = None
        if spec is not None and ans is not None:
            answer = {"type": spec.type, "label": ans.label,
                      "probabilities": ans.probabilities,
                      "raw_probabilities": ans.raw_probabilities,
                      "calibrated": ans.calibrated, "confidence": ans.confidence,
                      "label_mass": ans.label_mass, "labels_truncated": ans.labels_truncated,
                      "expected_value": ans.expected_value}
        total_ms = (self.clock() - t_start) * 1000
        resp = {
            "contract": "instinct/1",
            "decision": did,
            "decision_version": spec.version if spec else None,
            "decision_hash": dhash,
            "trace_id": trace_id, "request_id": request_id,
            "mode": mode, "enforce": enforce, "action": action,
            "answer": answer,
            "items": outcome.items if outcome else None,
            "would": {"label": ans.label if ans else None,
                      "action": action} if outcome else None,
            "fallback": {"used": not enforce, "reason": fb_reason},
            "engine": eng_obj,
            "cascade": cascade,
            "latency_ms": {"queue": round(queue_ms, 3),
                           "engine": round(sum(c.get("ms", 0.0) for c in cascade), 3),
                           "total": round(total_ms, 3)},
        }
        self._ledger(resp, spec, req, inputs, items_in, deadline_ms, ans)
        return resp

    def _ledger(self, resp: dict, spec: DecisionSpec | None, req: dict, inputs, items_in,
                deadline_ms, ans: Answer | None) -> None:
        baseline = req.get("baseline")
        agree = None
        blabel = baseline_label(spec, baseline) if spec is not None else None
        if ans is not None and blabel is not None:
            agree = ans.label == blabel
        row = {
            "ts": self.wall(), "kind": "decide", "trace_id": resp["trace_id"],
            "request_id": resp["request_id"],
            "caller": (req.get("context") or {}).get("caller"),
            "decision": resp["decision"], "version": resp["decision_version"],
            "decision_hash": (resp["decision_hash"] or "")[:16] or None,
            "mode": resp["mode"], "enforce": resp["enforce"], "action": resp["action"],
            "label": ans.label if ans else None,
            "probabilities": ans.probabilities if ans else None,
            "raw_probabilities": ans.raw_probabilities if ans else None,
            "label_mass": ans.label_mass if ans else None,
            "confidence": ans.confidence if ans else None,
            "truncated": ans.labels_truncated if ans else [],
            "calibrated": ans.calibrated if ans else False,
            "fallback_reason": resp["fallback"]["reason"],
            "engine": resp["engine"], "exec": (resp["engine"] or {}).get("exec"),
            "model_sha256": (resp["engine"] or {}).get("model_sha256"),
            "cascade": resp["cascade"], "latency_ms": resp["latency_ms"],
            "deadline_ms": deadline_ms, "baseline": baseline, "agree": agree,
            "input_sha256": input_sha256(req.get("inputs"), req.get("items")),
        }
        if resp["items"] is not None:
            row["items"] = resp["items"]
        mode = (spec.log_inputs if spec and spec.log_inputs else self.cfg.log_inputs)
        if mode == "excerpt" and isinstance(req.get("inputs"), dict):
            row["input_excerpt"] = excerpt(req["inputs"])
        elif mode == "full":
            row["input_full"] = {"inputs": req.get("inputs"), "items": req.get("items")}
        try:
            self.ledger.write(row)
        except OSError:
            pass  # a full disk must not turn into a caller-visible error
        if spec is not None:
            crec = self.calib.get((spec.id, (resp["engine"] or {}).get("id") or ""))
            ref = ((crec or {}).get("label_mass_ref") or {}).get("p50")
            self.autodemoter.observe(
                spec.id, failed=resp["action"] == "fallback" and resp["fallback"]["reason"] in (
                    "engine_timeout", "engine_unavailable", "deadline", "overload"),
                label_mass=row["label_mass"], mass_ref_p50=ref)

    # ------------------------------------------------------------------ route
    async def route(self, req: dict[str, Any]) -> dict[str, Any]:
        t0 = self.clock()
        f = req["features"]
        spec = self.specs.get(TASK_CLASS)
        base = {"contract": "instinct-route/1", "request_id": req.get("request_id"),
                "pool_fit": None}
        if spec is None:
            trace = new_trace_id()
            self.ledger.write({"ts": self.wall(), "kind": "route", "trace_id": trace,
                               "request_id": req.get("request_id"), "decision": TASK_CLASS,
                               "action": "fallback", "fallback_reason": "registry_invalid",
                               "mode": "off", "enforce": False})
            return {**base, "trace_id": trace, "action": "fallback", "enforce": False,
                    "mode": "off", "task_class": None, "reason": "registry_invalid",
                    "latency_ms": round((self.clock() - t0) * 1000, 3)}
        inputs = {k: f[k] for k in spec.inputs if k in f}
        d = await self.decide({"contract": "instinct/1", "decision": TASK_CLASS,
                               "request_id": req.get("request_id"), "inputs": inputs,
                               "ceiling": "enforce", "deadline_ms": req.get("deadline_ms"),
                               "context": {"caller": "hydra", "eval": False}})
        ans = d["answer"]
        task_class = None
        if ans is not None:
            mech = bool(spec.mechanical) and mechanical_label(spec, inputs) is not None
            task_class = {"label": ans["label"], "probabilities": ans["probabilities"],
                          "calibrated": ans["calibrated"], "mechanical": mech,
                          "decision_hash": d["decision_hash"]}
        action = d["action"] if d["action"] in ("act", "abstain", "fallback") else "abstain"
        return {**base, "trace_id": d["trace_id"], "action": action, "enforce": d["enforce"],
                "mode": d["mode"], "task_class": task_class,
                "reason": None if d["enforce"] else d["fallback"]["reason"],
                "latency_ms": round((self.clock() - t0) * 1000, 3)}

    # ------------------------------------------------------------------ views
    def decisions_view(self) -> dict:
        out = []
        for did, spec in sorted(self.specs.items()):
            engines = []
            for name in self.chains[did]:
                mode, reason = self.lifecycle(did, name, "enforce")
                lk = self.states[name].locks.get(did)
                h = self.hashes.get((did, name))
                engines.append({"engine": name, "decision_hash": h[:16] if h else None,
                                "lock_ok": (lk.ok if lk else self.engines[name].adapter ==
                                            "rules"),
                                "lock_reason": lk.reason if lk else None,
                                "calibrated": self.calib.get((did, name)) is not None,
                                "gate_passed": self.gates.get((did, name), False),
                                "healthy": self.states[name].healthy,
                                "effective_mode": mode, "reason": reason})
            out.append({"id": did, "version": spec.version, "type": spec.type,
                        "target_mode": spec.policy.mode, "chain": self.chains[did],
                        "demoted": self.demotions.reason(did), "engines": engines})
        return {"decisions": out, "invalid": dict(sorted(self.spec_errors.items()))}

    def engines_view(self) -> dict:
        out = []
        for name, e in sorted(self.engines.items()):
            st, b = self.states[name], e.binding
            out.append({
                "id": name, "adapter": e.adapter, "caps": e.caps.__dict__,
                "healthy": st.healthy,
                "last_probe_age_s": (round(self.clock() - st.last_probe, 3)
                                     if st.last_probe is not None else None),
                "probe": ({"ok": st.probe.ok, "reason": st.probe.reason,
                           "nondeterministic": st.probe.nondeterministic,
                           "exec_forced": st.probe.exec_forced}
                          if st.probe else None),
                "p50_ms": e.p50_ms(), "p95_ms": e.p95_ms(),
                "pins": {"url": b.url or None, "model": b.model or None,
                         "model_sha256": b.model_sha256 or None,
                         "model_revision": b.model_revision or None,
                         "image_digest": b.image_digest or None,
                         "sglang_commit": b.sglang_commit or None,
                         "exec": getattr(e, "exec", None)}})
        return {"engines": out, "invalid": dict(sorted(self.cfg.engine_errors.items()))}

    def metrics_text(self) -> str:
        eff = {}
        cal = {}
        for did in self.specs:
            eff[did] = any(self.lifecycle(did, n, "enforce")[0] == "enforce"
                           for n in self.chains[did])
            cal[did] = any(self.calib.get((did, n)) is not None for n in self.chains[did])
        return self.ledger.prometheus(
            engines_up={n: st.healthy for n, st in self.states.items()},
            calibrated=cal, effective_enforce=eff)

    async def score_raw(self, engine: str, query: str, items: list[str],
                        label_ids: dict[str, int]) -> dict:
        """Debug only (INSTINCT_DEBUG_SCORE=true): raw rows, no policy."""
        eng = self.engines.get(engine)
        if eng is None or not eng.caps.needs_render:
            raise InputError("debug score needs an LLM engine")
        from .render import Rendered
        r = Rendered("raw", query=query, items=list(items)) if items else Rendered(
            "raw", prompt=query)
        rows, exec_used, usage = await eng._score_rendered(r, label_ids,
                                                           eng.binding.timeout_ms / 1000)
        out = {"engine": engine, "exec": exec_used,
               "rows": [{"q": r.q, "label_mass": r.label_mass, "truncated": r.truncated}
                        for r in rows]}
        self.ledger.write({"ts": self.wall(), "kind": "raw", "engine": {"id": engine},
                           "decision": None, "action": None, "mode": None,
                           "input_sha256": input_sha256(query, items)})
        return out

    def feedback(self, body: dict) -> dict:
        row = {"ts": self.wall(), **body}
        self.ledger.feedback(row)
        return {"ok": True}
