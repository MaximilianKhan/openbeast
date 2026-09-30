"""Fail-open instinct client (plan §5.10). The ONLY module callers import.

Every failure — service dead, slow, 4xx/5xx, malformed JSON, breaker open,
missing key — returns a Verdict with enforce=False, and the caller runs
today's code path byte for byte (I3). `gate(verdict, act, legacy)` makes that
the path of least resistance.

  * HTTP timeout = deadline_ms + 10 ms, and the whole call is wrapped in the
    same deadline;
  * breaker: 5 consecutive failures open it for 30 s (then one half-open
    trial);
  * key from .run/instinct.key (0600); absent -> permanently in fallback.
This module deliberately imports nothing else from the instinct package, so
a caller (router.py, hydra) pulls in no engine code.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

DEFAULT_URL = "http://127.0.0.1:8094"
DEFAULT_KEY = Path(__file__).resolve().parents[2] / ".run" / "instinct.key"
BREAKER_FAILS = 5
BREAKER_OPEN_S = 30.0


@dataclass(frozen=True)
class Verdict:
    enforce: bool
    label: str | None = None
    items: list | None = None
    action: str = "fallback"
    reason: str | None = None
    trace_id: str | None = None


def _fallback(reason: str, trace_id: str | None = None) -> Verdict:
    return Verdict(enforce=False, label=None, items=None, action="fallback", reason=reason,
                   trace_id=trace_id)


def _read_key(path: Path) -> str | None:
    try:
        st = os.stat(path)
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            return None
        key = Path(path).read_text().strip()
        return key or None
    except OSError:
        return None


def gate(verdict: Verdict, act: Callable[[], Any], legacy: Callable[[], Any]) -> Any:
    """Run `act` only when instinct is ENFORCING an act; otherwise `legacy`."""
    if isinstance(verdict, Verdict) and verdict.enforce is True and verdict.action == "act":
        return act()
    return legacy()


class InstinctClient:
    def __init__(self, url: str | None = None, key_file: str | Path | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url = (url or os.environ.get("INSTINCT_URL") or DEFAULT_URL).rstrip("/")
        self.key_file = Path(key_file or os.environ.get("INSTINCT_KEY_FILE") or DEFAULT_KEY)
        self.clock = clock
        self.transport = transport
        self.fails = 0
        self.open_until = 0.0

    # --- breaker ---
    def _breaker_open(self) -> bool:
        return self.fails >= BREAKER_FAILS and self.clock() < self.open_until

    def _fail(self) -> None:
        self.fails += 1
        if self.fails >= BREAKER_FAILS:
            self.open_until = self.clock() + BREAKER_OPEN_S

    def _ok(self) -> None:
        self.fails = 0
        self.open_until = 0.0

    async def _post(self, path: str, body: dict, deadline_ms: int) -> dict | None:
        key = _read_key(self.key_file)
        if key is None:
            return None
        timeout = (deadline_ms + 10) / 1000.0
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport,
                                     trust_env=False) as c:
            r = await asyncio.wait_for(
                c.post(self.url + path, content=json.dumps(body),
                       headers={"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"}),
                timeout=timeout)
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data

    async def decide(self, decision: str, inputs: dict, *, items: list | None = None,
                     baseline: str | None = None, ceiling: str = "enforce",
                     deadline_ms: int = 600, request_id: str | None = None,
                     context: dict | None = None) -> Verdict:
        if self._breaker_open():
            return _fallback("client_breaker_open")
        if _read_key(self.key_file) is None:
            return _fallback("client_no_key")
        body = {"contract": "instinct/1", "decision": decision, "inputs": inputs,
                "ceiling": ceiling, "deadline_ms": int(deadline_ms)}
        if items is not None:
            body["items"] = items
        if baseline is not None:
            body["baseline"] = baseline
        if request_id is not None:
            body["request_id"] = request_id
        if context is not None:
            body["context"] = context
        try:
            data = await self._post("/v1/instinct/decide", body, int(deadline_ms))
            if data is None:
                return _fallback("client_no_key")
            v = self._verdict(data, items)
        except Exception:  # fail open on ANYTHING
            self._fail()
            return _fallback("client_error")
        self._ok()
        return v

    @staticmethod
    def _verdict(data: dict, items_in: list | None) -> Verdict:
        if data.get("contract") != "instinct/1":
            raise ValueError("wrong contract")
        enforce, action = data.get("enforce"), data.get("action")
        if not isinstance(enforce, bool) or action not in ("act", "review", "abstain",
                                                            "fallback"):
            raise ValueError("malformed verdict")
        answer = data.get("answer") or {}
        label = answer.get("label") if isinstance(answer, dict) else None
        items = data.get("items")
        if items is not None:
            # I1, client side too: a rank result never names an id it was not sent.
            sent = {it.get("id") for it in (items_in or []) if isinstance(it, dict)}
            if not isinstance(items, list) or any(
                    not isinstance(i, dict) or i.get("id") not in sent for i in items):
                raise ValueError("rank ids outside the input")
        reason = (data.get("fallback") or {}).get("reason") if isinstance(
            data.get("fallback"), dict) else None
        return Verdict(enforce=enforce and action == "act", label=label, items=items,
                       action=action, reason=reason, trace_id=data.get("trace_id"))

    def decide_sync(self, decision: str, inputs: dict, **kw) -> Verdict:
        try:
            return asyncio.run(self.decide(decision, inputs, **kw))
        except RuntimeError:  # already inside a running loop
            return _fallback("client_sync_in_loop")

    async def route(self, features: dict, *, deadline_ms: int = 25,
                    request_id: str | None = None) -> dict | None:
        """instinct-route/1 for hydra. None on ANY failure (use static policy)."""
        if self._breaker_open() or _read_key(self.key_file) is None:
            return None
        body = {"contract": "instinct-route/1", "deadline_ms": int(deadline_ms),
                "features": features, "request_id": request_id}
        try:
            data = await self._post("/v1/instinct/route", body, int(deadline_ms))
            if not data or data.get("contract") != "instinct-route/1" or not isinstance(
                    data.get("enforce"), bool):
                raise ValueError("malformed route answer")
        except Exception:
            self._fail()
            return None
        self._ok()
        return data

    async def contracts(self, timeout_ms: int = 200) -> list[str]:
        """GET /v1/instinct/contract -> the contract list; [] on ANY failure."""
        key = _read_key(self.key_file)
        if key is None or self._breaker_open():
            return []
        try:
            async with httpx.AsyncClient(timeout=timeout_ms / 1000, transport=self.transport,
                                         trust_env=False) as c:
                r = await c.get(self.url + "/v1/instinct/contract",
                                headers={"Authorization": f"Bearer {key}"})
            data = r.json() if r.status_code == 200 else {}
            got = data.get("contracts") if isinstance(data, dict) else None
            return [x for x in got if isinstance(x, str)] if isinstance(got, list) else []
        except Exception:
            return []

    async def feedback(self, trace_id: str, **fields) -> bool:
        try:
            data = await self._post("/v1/instinct/feedback", {"trace_id": trace_id, **fields},
                                    500)
            return bool(data and data.get("ok"))
        except Exception:
            return False


_default: InstinctClient | None = None


def default_client() -> InstinctClient:
    global _default
    if _default is None:
        _default = InstinctClient()
    return _default


async def decide(decision: str, inputs: dict, **kw) -> Verdict:
    return await default_client().decide(decision, inputs, **kw)


AsyncAct = Callable[[], Awaitable[Any]]
