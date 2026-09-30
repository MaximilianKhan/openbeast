"""Router glue for `router.spawn_intent` (plan §5.9), kept OUT of router.py.

agents/router.py is shared wiring; this module holds everything instinct-
specific so the router edit is a handful of lines (see docs/BEAST_INSTINCT.md
"Router wiring"):

    _INSTINCT = RouterInstinct()          # reads ROUTER_INSTINCT: off|shadow|enforce
    ...
    if user_text and _spawn_allowed(request.headers):       # identity gate FIRST
        hinted = bool(_HINTS.search(user_text))
        if await _INSTINCT.skip_classify(user_text, hinted):
            return await _proxy_through(request, client, raw)
        if hinted:
            spawn, task, workdir = await _classify(client, user_text)   # unchanged
            ...

Properties (tests/test_router_instinct.py):
  * ROUTER_INSTINCT=off (the default) makes zero instinct calls;
  * shadow never changes the router's behaviour, is fire-and-forget, bounded
    to 2 in flight, and DROPS work when full (never queues, never blocks);
  * the only effect enforce can have is to SKIP the generative classify —
    and only on a verdict that is enforce + act + label "inline" (I4). There
    is no code path from here to a spawn.
"""
from __future__ import annotations

import asyncio
import os

from .client import InstinctClient, Verdict

DECISION = "router.spawn_intent"
MODES = ("off", "shadow", "enforce")
SHADOW_SLOTS = 2
DEADLINE_MS = 600


class RouterInstinct:
    def __init__(self, mode: str | None = None, client: InstinctClient | None = None):
        m = (mode if mode is not None else os.environ.get("ROUTER_INSTINCT", "off"))
        m = (m or "off").strip().lower()
        self.mode = m if m in MODES else "off"
        self._client = client
        self.inflight = 0
        self._tasks: set[asyncio.Task] = set()
        self.dropped = 0
        self.shadowed = 0

    @property
    def client(self) -> InstinctClient:
        if self._client is None:
            self._client = InstinctClient()
        return self._client

    def shadow(self, user_text: str, baseline: str) -> bool:
        """Fire-and-forget shadow decide. Returns False when dropped."""
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
        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self.shadowed += 1
        return True

    async def skip_classify(self, user_text: str, hinted: bool) -> bool:
        """True ONLY when instinct enforces a confident 'inline' on a hinted
        turn; the caller then passes the turn straight through. Everything
        else (including every failure) returns False: run today's path."""
        if self.mode == "off" or not user_text:
            return False
        if not hinted or self.mode == "shadow":
            self.shadow(user_text, "hint" if hinted else "nohint")
            return False
        v: Verdict = await self.client.decide(
            DECISION, {"user_turn": user_text}, ceiling="enforce", deadline_ms=DEADLINE_MS,
            baseline="hint", context={"caller": "router", "eval": False})
        return v.enforce is True and v.action == "act" and v.label == "inline"

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
