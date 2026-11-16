#!/usr/bin/env python3
"""beast-gate (agents/edge.py) — the 2026-11-15 adversarial-review findings.

One class per finding. Each builds its own registry and stub upstream under
tmp_path (the helpers are test_edge.py's); nothing here opens a real port.

Run: python3 -m pytest tests/test_edge_review1115.py -q
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys

from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_edge as _te  # noqa: E402
from test_edge import (DEVICE_KEY, _FakeResponse,  # noqa: E402
                       _laptop_bucket_app, _local_headers, _registry)

edge = _te.edge          # the fixture: a fresh module on a scratch REPO_DIR

HDR = {"Authorization": f"Bearer {DEVICE_KEY}"}
CHAT = "/v1/chat/completions"


def _anon_edge(tmp_path, monkeypatch):
    """The module with EDGE_ALLOW_ANON=true (config is read at import)."""
    monkeypatch.setenv("OPENBEAST_REPO_DIR", str(tmp_path))
    monkeypatch.setenv("OPENBEAST_EDGE_ALLOW_ANON", "true")
    import edge as _edge
    importlib.reload(_edge)
    return _edge


def _stub(e, *, health_status=200, health_text='{"status":"ok"}',
          health_exc=None, resp=None):
    """A stub upstream whose /health and completion reply are configurable."""
    class _Client:
        def build_request(self, method, url, content=None, headers=None):
            return object()

        async def send(self, req, stream=False):
            return resp() if resp else _FakeResponse()

        async def get(self, url, timeout=None):
            if health_exc:
                raise health_exc

            class R:
                status_code = health_status
                text = health_text
            return R()

        async def aclose(self):
            pass

    @e.asynccontextmanager
    async def _lifespan(a):
        a.state.client = _Client()
        a.state.registry = e.Registry()
        a.state.limiter = e.Limiter()
        yield

    e.app.router.lifespan_context = _lifespan


# A real engine /health body (tensorfold on spark-1, values illustrative).
_RICH_HEALTH = json.dumps({
    "backend": "tensorfold", "requests_total": 53,
    "prompt_tokens_total": 499347, "completion_tokens_total": 4137,
    "cached_tokens_total": 427136, "kept_prompts": 32,
    "streams": {"decoding": 0}})


class TestHealthIsNormalized:
    """sec: the proxied /health relayed the upstream's JSON verbatim, handing
    every enrolled device rig-wide cross-tenant usage counters."""

    def test_rich_upstream_body_is_not_echoed(self, edge, tmp_path):
        _registry(tmp_path)
        _stub(edge, health_text=_RICH_HEALTH)
        with TestClient(edge.app) as c:
            r = c.get("/health", headers=HDR)
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        for leak in ("requests_total", "cached_tokens_total", "tensorfold",
                     "streams", "kept_prompts"):
            assert leak not in r.text

    def test_loading_upstream_keeps_its_status_but_not_its_body(self, edge,
                                                                 tmp_path):
        # 503 "still loading" is what setup-client.sh tells apart from 502.
        _registry(tmp_path)
        _stub(edge, health_status=503,
              health_text='{"error":{"code":503,"message":"Loading model",'
                          '"requests_total":7}}')
        with TestClient(edge.app) as c:
            r = c.get("/health", headers=HDR)
        assert r.status_code == 503
        assert "requests_total" not in r.text and "Loading" not in r.text
        # client.sh greps the body for "ok": a not-ready rig must not match.
        assert "ok" not in r.text

    def test_unreachable_upstream_does_not_name_it(self, edge, tmp_path):
        _registry(tmp_path)
        _stub(edge, health_exc=edge.httpx.ConnectError(
            "All connection attempts failed for http://127.0.0.1:8000"))
        with TestClient(edge.app) as c:
            r = c.get("/health", headers=HDR)
        assert r.status_code == 502
        assert "127.0.0.1" not in r.text and "ok" not in r.text


NEW_KEY = "c" * 64


def _reenroll(tmp_path, enrolled_at="2026-08-15T09:00:00Z", nonce=None):
    """What `clients.sh remove laptop --yes && enroll laptop` leaves behind:
    the same id, a new key, a new enrollment."""
    dev = {"id": "laptop", "label": "New holder",
           "key_sha256": hashlib.sha256(NEW_KEY.encode()).hexdigest(),
           "enrolled_at": enrolled_at, "revoked_at": None}
    if nonce:
        dev["enroll_nonce"] = nonce
    (tmp_path / ".run" / "clients.json").write_text(
        json.dumps({"version": 1, "devices": [dev]}))


class TestReenrolledIdIsAFreshDevice:
    """sec + cbp: per-device metrics were keyed by the reusable id, so a
    re-enrolled id read its predecessor's usage; same-second re-enrolls
    shared a device_uid; last-seen throttling followed the id."""

    def test_new_holder_does_not_read_predecessor_metrics(self, edge,
                                                          tmp_path):
        _registry(tmp_path)
        _stub(edge)
        with TestClient(edge.app) as c:
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 200
            _reenroll(tmp_path)
            new = {"Authorization": f"Bearer {NEW_KEY}"}
            mine = c.get("/gate/metrics", headers=new).text
            rig = c.get("/gate/metrics", headers=_local_headers(edge)).text
            # The new holder's own traffic does show up for it.
            c.post(CHAT, json={"messages": []}, headers=new)
            after = c.get("/gate/metrics", headers=new).text
        assert 'openbeast_edge_prompt_tokens_total{device="laptop"}' not in mine
        assert "openbeast_edge_requests_total{" not in mine
        # The rig still sees the old enrollment's usage, under the id.
        assert 'openbeast_edge_prompt_tokens_total{device="laptop"} 10' in rig
        assert 'openbeast_edge_prompt_tokens_total{device="laptop"} 10' in after

    def test_rig_view_sums_enrollments_without_duplicate_series(self, edge,
                                                                tmp_path):
        _registry(tmp_path)
        _stub(edge)
        with TestClient(edge.app) as c:
            c.post(CHAT, json={"messages": []}, headers=HDR)
            _reenroll(tmp_path)
            c.post(CHAT, json={"messages": []},
                   headers={"Authorization": f"Bearer {NEW_KEY}"})
            rig = c.get("/gate/metrics", headers=_local_headers(edge)).text
        lines = [ln for ln in rig.splitlines()
                 if ln.startswith('openbeast_edge_prompt_tokens_total{')]
        assert lines == [
            'openbeast_edge_prompt_tokens_total{device="laptop"} 20']

    def test_same_second_reenroll_gets_a_distinct_uid(self, edge):
        ts = "2026-08-15T09:00:00Z"
        a = edge.device_uid({"id": "laptop", "enrolled_at": ts,
                             "enroll_nonce": "1111"})
        b = edge.device_uid({"id": "laptop", "enrolled_at": ts,
                             "enroll_nonce": "2222"})
        assert a != b
        # Rows written before the nonce keep the uid they always had.
        assert edge.device_uid({"id": "laptop", "enrolled_at": ts}) == \
            hashlib.sha256(f"laptop:{ts}".encode()).hexdigest()[:12]

    def test_reenrolled_id_records_last_seen_at_once(self, edge, tmp_path):
        _registry(tmp_path)
        _stub(edge)
        seen = tmp_path / ".run" / "clients-lastseen.json"
        with TestClient(edge.app) as c:
            c.post(CHAT, json={"messages": []}, headers=HDR)
            assert seen.exists()
            seen.unlink()                 # what `clients.sh remove` prunes
            _reenroll(tmp_path)
            c.post(CHAT, json={"messages": []},
                   headers={"Authorization": f"Bearer {NEW_KEY}"})
        assert seen.exists() and "laptop" in json.loads(seen.read_text()), \
            "the new enrollment's first use was throttled away"


class _CorsResponse(_FakeResponse):
    """llama-server reflects the caller's Origin back."""

    def __init__(self):
        super().__init__()
        self.headers = {"content-type": "application/json",
                        "access-control-allow-origin": "https://evil.example",
                        "Access-Control-Allow-Credentials": "true"}


class TestBrowserPagesCannotDriveAnon:
    """sec: in EDGE_ALLOW_ANON mode a web page's preflight-free text/plain
    POST was served, and the relayed ACAO header let it read the reply."""

    def _post(self, e, **headers):
        _stub(e, resp=_CorsResponse)
        with TestClient(e.app) as c:
            return c.post(CHAT, content=b'{"messages":[]}', headers=headers)

    def test_cross_site_text_plain_post_is_refused(self, tmp_path,
                                                    monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        r = self._post(e, **{"Content-Type": "text/plain",
                             "Origin": "https://evil.example"})
        assert r.status_code == 403, r.text

    def test_sec_fetch_site_cross_site_is_refused(self, tmp_path,
                                                   monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        r = self._post(e, **{"Content-Type": "text/plain",
                             "Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403

    def test_non_browser_anon_caller_still_served(self, tmp_path,
                                                  monkeypatch):
        # Negative control: curl / an SDK sends neither header.
        e = _anon_edge(tmp_path, monkeypatch)
        r = self._post(e, **{"Content-Type": "application/json"})
        assert r.status_code == 200

    def test_access_control_headers_are_not_relayed(self, edge, tmp_path):
        _registry(tmp_path)
        r = self._post(edge, Authorization=f"Bearer {DEVICE_KEY}")
        assert r.status_code == 200
        assert not [k for k in r.headers
                    if k.lower().startswith("access-control-")]


class TestAnonDoesNotReopenAfterEnrollment:
    """sec + cbp: "ignored for good once the file exists" was really "while
    the file exists" — moving a corrupt registry aside (the gate's own
    repair advice) or deleting it served every caller as anon again."""

    def _keyless(self, c):
        return c.post(CHAT, json={"messages": []})

    def test_moving_the_registry_aside_stays_closed(self, tmp_path,
                                                    monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        path = _registry(tmp_path)
        _stub(e)
        with TestClient(e.app) as c:
            assert self._keyless(c).status_code == 401
            os.rename(path, str(path) + ".bad")
            r = self._keyless(c)
            assert r.status_code == 401 and "no_registry" in r.text
            # A device key that used to work is no anon pass either.
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 401
            body = c.get("/gate/health", headers=_local_headers(e)).json()
        assert body["auth"] == "closed"

    def test_deleted_registry_stays_closed_across_a_restart(self, tmp_path,
                                                            monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        path = _registry(tmp_path)
        e.Registry()                 # a gate saw it (writes the marker)
        path.unlink()
        _stub(e)
        with TestClient(e.app) as c:      # fresh process state
            assert self._keyless(c).status_code == 401
        assert e._auth_mode(e.Registry()) == "closed"

    def test_unstatable_registry_is_closed(self, tmp_path, monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        reg_path = str(tmp_path / ".run" / "clients.json")
        real_stat = os.stat

        def _stat(p, *a, **k):
            if str(p) == reg_path:
                raise PermissionError(13, "Permission denied", p)
            return real_stat(p, *a, **k)

        monkeypatch.setattr(e.os, "stat", _stat)
        (tmp_path / ".run").mkdir(exist_ok=True)
        assert e._auth_mode(e.Registry(reg_path)) == "closed"

    def test_a_rig_that_never_had_a_registry_is_still_anon(self, tmp_path,
                                                           monkeypatch):
        # Negative control: the opt-in still works for a fresh rig.
        e = _anon_edge(tmp_path, monkeypatch)
        _stub(e)
        with TestClient(e.app) as c:
            assert self._keyless(c).status_code == 200


class TestModelListIsNotAGeneration:
    """cbp: GET /v1/models took an in-flight unit, so it 429'd ("device
    already has 2 generations in flight") while two streams ran — and
    client.sh read that as a wrong key."""

    def test_models_served_while_inflight_cap_is_full(self, edge, tmp_path):
        _registry(tmp_path)
        _stub(edge)
        with TestClient(edge.app) as c:
            c.get("/v1/models", headers=HDR)          # creates the bucket
            b = _laptop_bucket_app(edge, c.app)
            b.inflight = b.max_inflight              # two streams running
            r = c.get("/v1/models", headers=HDR)
            assert r.status_code == 200, r.text
            # It neither took nor released a unit of the running streams'.
            assert b.inflight == b.max_inflight
            # Negative control: a generation IS refused at the cap.
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 429

    def test_models_still_pays_the_rate_limit(self, edge, tmp_path):
        _registry(tmp_path, rate=1)
        _stub(edge)
        with TestClient(edge.app) as c:
            codes = [c.get("/v1/models", headers=HDR).status_code
                     for _ in range(3)]
        assert codes[0] == 200 and 429 in codes


def _audit_rows(tmp_path) -> list:
    p = tmp_path / ".run" / "inference-audit.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] \
        if p.exists() else []


class TestEveryReplyCarriesItsRequestId:
    """cbp: X-OpenBeast-Request-Id was echoed only on success, the revoked
    401 and the 499; every other refusal had an audit row the caller could
    not name. Both 413 paths were neither audited nor counted."""

    def _rid_matches_audit(self, tmp_path, r, outcome):
        rid = r.headers.get("x-openbeast-request-id")
        assert rid, f"{r.status_code} reply carries no request id"
        rows = [x for x in _audit_rows(tmp_path) if x["request_id"] == rid]
        assert rows and rows[-1]["outcome"] == outcome, rows

    def test_rate_limited_429(self, edge, tmp_path):
        _registry(tmp_path, rate=1)
        _stub(edge)
        with TestClient(edge.app) as c:
            c.post(CHAT, json={"messages": []}, headers=HDR)
            r = c.post(CHAT, json={"messages": []}, headers=HDR)
        assert r.status_code == 429
        self._rid_matches_audit(tmp_path, r, "rate_limited")

    def test_bad_body_400(self, edge, tmp_path):
        _registry(tmp_path)
        _stub(edge)
        with TestClient(edge.app) as c:
            r = c.post(CHAT, content=b"{not json", headers=HDR)
        assert r.status_code == 400
        self._rid_matches_audit(tmp_path, r, "bad_request")

    def test_upstream_error_502(self, edge, tmp_path):
        _registry(tmp_path)

        def _boom():
            raise edge.httpx.ConnectError("refused")
        _stub(edge, resp=_boom)
        with TestClient(edge.app) as c:
            r = c.post(CHAT, json={"messages": []}, headers=HDR)
        assert r.status_code == 502
        self._rid_matches_audit(tmp_path, r, "upstream_error")

    def test_oversize_body_413_is_audited_and_counted(self, edge, tmp_path,
                                                      monkeypatch):
        _registry(tmp_path)
        _stub(edge)
        monkeypatch.setattr(edge, "MAX_BODY_BYTES", 64)
        big = json.dumps({"messages": [{"role": "user",
                                        "content": "x" * 200}]}).encode()

        def _chunks():
            yield big        # no Content-Length: the streamed cap fires
        with TestClient(edge.app) as c:
            r1 = c.post(CHAT, content=big, headers=HDR)
            r2 = c.post(CHAT, content=_chunks(), headers=HDR)
            m = c.get("/gate/metrics", headers=_local_headers(edge)).text
            b = _laptop_bucket_app(edge, c.app)
        for r in (r1, r2):
            assert r.status_code == 413
            self._rid_matches_audit(tmp_path, r, "too_large")
        assert 'openbeast_edge_denied_total{reason="too_large"} 2' in m
        assert b.inflight == 0
