"""beast-instinct HTTP service — 127.0.0.1:8094 (plan §5.10).

Every route except GET /health needs `Authorization: Bearer <.run/instinct.key>`
(0600; minted by `scripts/instinct.sh up`). The Host header is pinned with
agents/hostpolicy.py. Loopback only unless the config says allow_remote AND a
key is present — and the key is ALWAYS required: a missing or group/world
readable key file refuses to start (fail closed).

Status semantics (the client treats EVERY non-200 as fallback):
  400 malformed body / unknown field / wrong contract   401 bad key
  404 unknown decision                                   422 inputs violate the
  decision's input schema                                200 everything else —
  engine trouble is `action: "fallback"`, never an HTTP error.

Run:  PYTHONPATH=agents python3 -m instinct.server   (scripts/instinct.sh up)
SIGHUP reloads decisions, records and demotions.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import signal
import stat
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ConfigError, load_config
from .render import InputError
from .service import CONTRACTS, SERVICE_VERSION, Instinct, UnknownDecision

_AGENTS = str(Path(__file__).resolve().parents[1])
if _AGENTS not in sys.path:
    sys.path.insert(0, _AGENTS)
from hostpolicy import trusted_hosts  # noqa: E402

MAX_BODY = 256 * 1024
Mode = Literal["off", "shadow", "canary", "enforce"]
log = logging.getLogger("instinct")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Context(_Strict):
    caller: str | None = Field(default=None, max_length=64)
    eval: bool = False


class Item(_Strict):
    id: str = Field(max_length=256)
    text: str = Field(max_length=100_000)


class DecideReq(_Strict):
    contract: Literal["instinct/1"]
    decision: str = Field(max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    inputs: dict[str, Any]
    items: list[Item] | None = None
    baseline: str | None = Field(default=None, max_length=64)
    ceiling: Mode = "enforce"
    deadline_ms: int | None = Field(default=None, ge=1, le=60000)
    context: Context | None = None


class Features(_Strict):
    prompt_head: str = Field(max_length=8000)
    est_prompt_tokens: int = Field(ge=0)
    has_images: bool
    has_tools: bool
    stream: bool
    client_class: Literal["interactive", "agent", "batch"]


class Pool(_Strict):
    id: str = Field(max_length=128)
    descriptor: str = Field(max_length=4000)
    caps: dict[str, Any] = Field(default_factory=dict)


class RouteReq(_Strict):
    contract: Literal["instinct-route/1"]
    request_id: str | None = Field(default=None, max_length=128)
    deadline_ms: int | None = Field(default=None, ge=1, le=60000)
    features: Features
    pools: list[Pool] | None = None


class Outcome(_Strict):
    source: str = Field(max_length=64)
    label: str | None = Field(default=None, max_length=64)
    signal: float | None = None
    weight: float | None = None


class FeedbackReq(_Strict):
    trace_id: str = Field(max_length=64)
    request_id: str | None = Field(default=None, max_length=128)
    outcome: Outcome | None = None
    served_pool: str | None = Field(default=None, max_length=128)
    ttft_ms: float | None = None
    error: str | None = Field(default=None, max_length=512)


class ScoreDebugReq(_Strict):
    engine: str = Field(max_length=64)
    query: str = Field(max_length=100_000)
    items: list[str] = Field(default_factory=list, max_length=64)
    labels: dict[str, int]


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


async def _parse(request: Request, model: type[BaseModel]) -> BaseModel | JSONResponse:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _err(400, "body too large")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _err(400, "body is not JSON")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        return _err(400, "invalid request: " + "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]))


def read_service_key(path: Path) -> str:
    """The service key. Fail closed: missing, empty or non-0600 refuses."""
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path} must be 0600 (is {oct(st.st_mode & 0o777)})")
    key = Path(path).read_text().strip()
    if len(key) < 16:
        raise PermissionError(f"{path} holds no usable key")
    return key


def create_app(inst: Instinct, key: str, *, allowed_hosts: list[str] | None = None,
               debug_score: bool = False, probe_loop: bool = True) -> FastAPI:
    if not key:
        raise PermissionError("instinct refuses to serve without a key")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await inst.start()
        tasks = []
        loop = asyncio.get_running_loop()

        def _hup():
            tasks.append(loop.create_task(_reload()))

        async def _reload():
            await inst.reload()
            await inst.probe_all()
        try:
            loop.add_signal_handler(signal.SIGHUP, _hup)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

        async def _probe_forever():
            while inst.cfg.probe_interval_s > 0:
                await asyncio.sleep(inst.cfg.probe_interval_s)
                try:
                    await inst.probe_all()
                except Exception:  # never let the loop die
                    log.exception("probe loop")
        if probe_loop and inst.cfg.probe_interval_s > 0:
            tasks.append(loop.create_task(_probe_forever()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            try:
                loop.remove_signal_handler(signal.SIGHUP)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
            await inst.aclose()

    app = FastAPI(title="beast-instinct", version=SERVICE_VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.instinct = inst
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=allowed_hosts if allowed_hosts is not None
                       else trusted_hosts())
    want = f"Bearer {key}".encode()

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.url.path == "/health" and request.method == "GET":
            return await call_next(request)
        got = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(got, want):
            return _err(401, "unauthorized")
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/v1/instinct/decide")
    async def decide(request: Request):
        body = await _parse(request, DecideReq)
        if isinstance(body, JSONResponse):
            return body
        try:
            return await inst.decide(body.model_dump())
        except UnknownDecision:
            return _err(404, "unknown decision")
        except InputError as exc:
            return _err(422, str(exc))

    @app.post("/v1/instinct/route")
    async def route(request: Request):
        body = await _parse(request, RouteReq)
        if isinstance(body, JSONResponse):
            return body
        try:
            return await inst.route(body.model_dump())
        except InputError as exc:
            return _err(422, str(exc))

    @app.post("/v1/instinct/feedback")
    async def feedback(request: Request):
        body = await _parse(request, FeedbackReq)
        if isinstance(body, JSONResponse):
            return body
        return inst.feedback(body.model_dump(exclude_none=True))

    @app.get("/v1/instinct/decisions")
    async def decisions():
        return inst.decisions_view()

    @app.get("/v1/instinct/engines")
    async def engines():
        return inst.engines_view()

    @app.get("/v1/instinct/contract")
    async def contract():
        return {"contracts": list(CONTRACTS), "service_version": SERVICE_VERSION}

    @app.get("/v1/instinct/stats")
    async def stats(decision: str | None = None):
        return {"stats": inst.ledger.stats(decision)}

    @app.get("/metrics")
    async def metrics():
        return PlainTextResponse(inst.metrics_text(),
                                 media_type="text/plain; version=0.0.4")

    @app.post("/v1/instinct/score")
    async def score(request: Request):
        if not debug_score:
            return _err(404, "not found")
        body = await _parse(request, ScoreDebugReq)
        if isinstance(body, JSONResponse):
            return body
        try:
            return await inst.score_raw(body.engine, body.query, body.items, body.labels)
        except InputError as exc:
            return _err(422, str(exc))
        except Exception as exc:  # debug route: report, never crash
            return JSONResponse({"error": f"engine: {exc.__class__.__name__}"}, status_code=200)

    return app


def main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser(description="beast-instinct service")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s instinct %(message)s")
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"instinct: config error: {exc}", file=sys.stderr)
        return 2
    try:
        key = read_service_key(cfg.key_file)
    except (OSError, PermissionError) as exc:
        print(f"instinct: refusing to start: {exc}", file=sys.stderr)
        return 3
    for name, why in cfg.engine_errors.items():
        log.warning("engine binding %s refused: %s", name, why)
    inst = Instinct(cfg)
    debug = os.environ.get("INSTINCT_DEBUG_SCORE", "").strip().lower() == "true"
    app = create_app(inst, key, debug_score=debug)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
