"""Router glue for `router.spawn_intent` (plan §5.9), kept OUT of router.py.

agents/router.py is shared wiring; this module holds everything instinct-
specific so the router edit is a handful of lines (see docs/BEAST_INSTINCT.md
"Router wiring"):

    _INSTINCT = RouterInstinct()          # reads ROUTER_INSTINCT: off|shadow|enforce
    ...
    if user_text and _spawn_allowed(request.headers):       # identity gate FIRST
        hinted = bool(_HINTS.search(user_text))
        turn = await _INSTINCT.consult(user_text, hinted)
        if turn.skip:
            return await _proxy_through(request, client, raw)
    if hinted:
        spawn, task, workdir = await _classify(client, user_text)   # unchanged
        _INSTINCT.classified(turn, spawn)                           # paired baseline
        ...

Properties (tests/test_router_instinct.py):
  * ROUTER_INSTINCT=off (the default) makes zero instinct calls;
  * only HINTED turns are scored by default — the turns the router classifies
    on the primary anyway. The decision's main engine IS that primary (the
    27B, `rig-27b`), and its only slot (-np 1) belongs to the user's turn: an
    unhinted turn could never have its call replaced, so it is
    never scored unless ROUTER_INSTINCT_SHADOW_UNHINTED=true (and even then
    the service skips the primary for it: `primary_not_substitute`);
  * a hinted turn's decide is AWAITED before the classify, in shadow as in
    enforce: it serializes with the classify on the primary instead of racing
    the user's turn for the slot (a fire-and-forget call could land in the
    slot first and make the turn wait). In shadow it is awaited only until
    the walk has LEFT the primary (return_after="primary"): the fallbacks
    behind it are measured in the background. Bounded by DEADLINE_MS, fails
    open; shadow is capped at SHADOW_SLOTS in flight and DROPS beyond that;
  * THE REAL COST (R-instinct-1): the 27B's scoring call is one prompt
    prefill on the primary. It replaces the classify only on an ENFORCED
    confident "inline"; on every other hinted turn (all of shadow, and every
    spawn / abstain / low-confidence verdict under enforce) it is ADDED
    before the classify — one extra primary call and its latency (<=
    DEADLINE_MS) per hinted turn;
  * shadow never changes the router's behaviour; the only effect enforce can
    have is to SKIP the generative classify — and only on a verdict that is
    enforce + act + label "inline" (I4). There is no code path from here to a
    spawn;
  * after the classify runs, its verdict goes back as feedback on the same
    trace_id (with a request_id, which also makes canary_pct usable): the
    paired classify-vs-instinct data the plan's P1 exit needs (A-instinct-7).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

from .client import InstinctClient, Verdict

DECISION = "router.spawn_intent"
MODES = ("off", "shadow", "enforce")
SHADOW_SLOTS = 2
FEEDBACK_SLOTS = 4
DEADLINE_MS = 600


@dataclass(frozen=True)
class Turn:
    skip: bool = False                 # True ONLY on an enforced confident "inline"
    trace_id: str | None = None        # the decide's trace (None: nothing was asked)
    request_id: str | None = None


NO_TURN = Turn()


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


class RouterInstinct:
    def __init__(self, mode: str | None = None, client: InstinctClient | None = None,
                 shadow_unhinted: bool | None = None):
        m = (mode if mode is not None else os.environ.get("ROUTER_INSTINCT", "off"))
        m = (m or "off").strip().lower()
        self.mode = m if m in MODES else "off"
        self.shadow_unhinted = (shadow_unhinted if shadow_unhinted is not None else
                                _truthy(os.environ.get("ROUTER_INSTINCT_SHADOW_UNHINTED")))
        self._client = client
        self.inflight = 0
        self.fb_inflight = 0
        self._tasks: set[asyncio.Task] = set()
        self.dropped = 0
        self.shadowed = 0

    @property
    def client(self) -> InstinctClient:
        if self._client is None:
            self._client = InstinctClient()
        return self._client

    def _spawn_task(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def shadow(self, user_text: str, baseline: str) -> bool:
        """Fire-and-forget shadow decide (unhinted opt-in only). Returns False
        when dropped."""
        if self.mode == "off":
            return False
        if self.inflight >= SHADOW_SLOTS:
            self.dropped += 1
            return False
        # The slot is taken synchronously, so a burst cannot over-subscribe it
        # (an asyncio.Semaphore would QUEUE the third call instead of dropping).
        self.inflight += 1

        async def run():
            try:
                await self.client.decide(DECISION, {"user_turn": user_text},
                                         ceiling="shadow", deadline_ms=DEADLINE_MS,
                                         baseline=baseline,
                                         context={"caller": "router", "eval": False})
            except Exception:
                pass
            finally:
                self.inflight -= 1
        self._spawn_task(run())
        self.shadowed += 1
        return True

    async def consult(self, user_text: str, hinted: bool) -> Turn:
        """Ask instinct about one admin turn. Turn.skip is True ONLY when
        instinct enforces a confident 'inline' on a hinted turn; everything
        else (including every failure) means: run today's path."""
        if self.mode == "off" or not user_text:
            return NO_TURN
        if not hinted:
            if self.shadow_unhinted:
                self.shadow(user_text, "nohint")
            return NO_TURN
        shadow = self.mode == "shadow"
        if shadow:
            if self.inflight >= SHADOW_SLOTS:
                self.dropped += 1
                return NO_TURN
            self.inflight += 1
        rid = "rt_" + uuid.uuid4().hex[:20]
        try:
            v: Verdict = await self.client.decide(
                DECISION, {"user_turn": user_text},
                ceiling="shadow" if shadow else "enforce", deadline_ms=DEADLINE_MS,
                baseline="hint", request_id=rid,
                context={"caller": "router", "eval": False},
                # The service honours this only below an actable target:
                # in shadow the turn waits for the primary engine alone
                # (the part that must serialize with the classify), never
                # for the fallbacks behind it (R-instinct-1).
                return_after="primary")
        except Exception:
            return NO_TURN
        finally:
            if shadow:
                self.inflight -= 1
                self.shadowed += 1
        skip = (not shadow and v.enforce is True and v.action == "act"
                and v.label == "inline")
        return Turn(skip=skip, trace_id=v.trace_id, request_id=rid)

    async def skip_classify(self, user_text: str, hinted: bool) -> bool:
        """Back-compat wrapper: consult() without the feedback handle."""
        return (await self.consult(user_text, hinted)).skip

    def classified(self, turn: Turn, spawn: bool) -> None:
        """Record the legacy classify's verdict against the decide that
        preceded it (fire-and-forget, bounded, never raises)."""
        if self.mode == "off" or not isinstance(turn, Turn) or not turn.trace_id:
            return
        if self.fb_inflight >= FEEDBACK_SLOTS:
            return
        self.fb_inflight += 1

        async def run():
            try:
                await self.client.feedback(
                    turn.trace_id, request_id=turn.request_id,
                    outcome={"source": "classify", "label": "spawn" if spawn else "inline"})
            except Exception:
                pass
            finally:
                self.fb_inflight -= 1
        try:
            self._spawn_task(run())
        except RuntimeError:
            self.fb_inflight -= 1

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
