#!/usr/bin/env python3
"""
Unit tests for the agent-spawn router (agents/router.py) — no server needed.

Covers the pure/deterministic surface: the _HINTS prefilter (precision AND
recall lists), last-user-turn extraction across content shapes, the
synthetic OpenAI-shaped replies (non-stream + stream), classify fail-safe
behavior, the grammar schema contract, and the identity spawn gate
(_spawn_allowed — RBAC Phase 2).

Run: python -m pytest tests/test_router.py -v
  or: python3 tests/test_router.py
"""

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

import router


class TestHintsPrecision(unittest.TestCase):
    """The prefilter is tuned for PRECISION: normal coding chat must not
    trigger the ~500ms classify call; explicit delegation phrasing must."""

    NEGATIVES = [
        "can you handle this error yourself",
        "what does this function do",
        "fix the login page",
        "explain how python decorators work",
        "refactor this function to be cleaner",
        "why is my build failing",
    ]
    POSITIVES = [
        "spawn an agent to refactor the parser",
        "kick off a background job for this",
        "run it in the background",
        "launch an agent",
        "don't wait for me",
        "report back when done",
        "do these in parallel",
    ]

    def test_negatives_do_not_match(self):
        for text in self.NEGATIVES:
            self.assertIsNone(router._HINTS.search(text),
                              f"prefilter false-positive on: {text!r}")

    def test_positives_match(self):
        for text in self.POSITIVES:
            self.assertIsNotNone(router._HINTS.search(text),
                                 f"prefilter missed delegation phrasing: {text!r}")


class TestLastUserText(unittest.TestCase):
    def test_str_content(self):
        msgs = [{"role": "user", "content": "hello"}]
        self.assertEqual(router._last_user_text(msgs), "hello")

    def test_content_parts_list(self):
        msgs = [{"role": "user",
                 "content": [{"type": "text", "text": "part one"},
                             {"type": "text", "text": "part two"}]}]
        out = router._last_user_text(msgs)
        self.assertIn("part one", out)
        self.assertIn("part two", out)

    def test_no_user_turn_returns_empty(self):
        msgs = [{"role": "system", "content": "sys"},
                {"role": "assistant", "content": "hi"}]
        self.assertEqual(router._last_user_text(msgs), "")
        self.assertEqual(router._last_user_text([]), "")
        self.assertEqual(router._last_user_text(None), "")

    def test_non_dict_parts_tolerated(self):
        msgs = [{"role": "user",
                 "content": ["raw string part", {"type": "text", "text": "real"}, 42]}]
        out = router._last_user_text(msgs)  # must not raise
        self.assertIn("real", out)

    def test_picks_last_user_turn(self):
        msgs = [{"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"}]
        self.assertEqual(router._last_user_text(msgs), "second")


class TestSynthetic(unittest.TestCase):
    def test_synthetic_nonstream(self):
        resp = router._synthetic("test-model", "agent started", stream=False)
        body = json.loads(resp.body)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["model"], "test-model")
        choice = body["choices"][0]
        self.assertEqual(choice["message"]["role"], "assistant")
        self.assertEqual(choice["message"]["content"], "agent started")
        self.assertEqual(choice["finish_reason"], "stop")

    def test_synthetic_stream(self):
        resp = router._synthetic("test-model", "agent started", stream=True)

        async def collect():
            return [c async for c in resp.body_iterator]

        chunks = asyncio.run(collect())
        self.assertGreaterEqual(len(chunks), 3)
        # First chunk: delta carries role + content
        first = json.loads(chunks[0].removeprefix("data: "))
        self.assertEqual(first["object"], "chat.completion.chunk")
        delta = first["choices"][0]["delta"]
        self.assertEqual(delta["role"], "assistant")
        self.assertEqual(delta["content"], "agent started")
        # A later chunk carries finish_reason=stop
        done = json.loads(chunks[-2].removeprefix("data: "))
        self.assertEqual(done["choices"][0]["finish_reason"], "stop")
        # Terminates with the SSE sentinel
        self.assertEqual(chunks[-1].strip(), "data: [DONE]")


class TestClassifyFailSafe(unittest.TestCase):
    def test_classify_exception_means_no_spawn(self):
        """Any classify failure must fail SAFE (pass the turn through),
        never block or raise."""

        class BoomClient:
            async def post(self, *args, **kwargs):
                raise Exception("upstream exploded")

        result = asyncio.run(router._classify(BoomClient(), "spawn an agent please"))
        self.assertEqual(result, (False, "", "."))


class TestSpawnGate(unittest.TestCase):
    """Identity gate (_spawn_allowed): only admin-role turns may reach the
    classify/spawn path; guests pass through untouched; absent identity is
    fail-open unless OPENBEAST_ROUTER_REQUIRE_IDENTITY hardens it."""

    ROLE = "X-OpenWebUI-User-Role"

    def setUp(self):
        # Header mode, whatever the shell exported: a --with-jwt rig's
        # sourced conf sets OPENBEAST_IDENTITY_JWT_SECRET, which switches
        # router.JWT_SECRET to JWT mode at import time.
        self._old_secret = router.JWT_SECRET
        router.JWT_SECRET = ""

    def tearDown(self):
        router.JWT_SECRET = self._old_secret

    def test_admin_allowed(self):
        self.assertTrue(router._spawn_allowed({self.ROLE: "admin"}, require_identity=False))
        self.assertTrue(router._spawn_allowed({self.ROLE: "admin"}, require_identity=True))

    def test_non_admin_roles_denied(self):
        for role in ("user", "pending", ""):
            for require in (False, True):
                self.assertFalse(
                    router._spawn_allowed({self.ROLE: role}, require_identity=require),
                    f"role {role!r} (require_identity={require}) must not spawn")

    def test_absent_header_fail_open_by_default(self):
        self.assertTrue(router._spawn_allowed({}, require_identity=False))

    def test_absent_header_fail_closed_when_required(self):
        self.assertFalse(router._spawn_allowed({}, require_identity=True))

    def test_header_name_case_insensitive(self):
        self.assertTrue(router._spawn_allowed(
            {"x-openwebui-user-role": "admin"}, require_identity=True))
        self.assertFalse(router._spawn_allowed(
            {"X-OPENWEBUI-USER-ROLE": "user"}, require_identity=False))

    def test_role_value_case_insensitive_and_trimmed(self):
        self.assertTrue(router._spawn_allowed({self.ROLE: "Admin"}, require_identity=True))
        self.assertTrue(router._spawn_allowed({self.ROLE: " ADMIN "}, require_identity=True))

    def test_unrelated_headers_ignored(self):
        hdrs = {"Content-Type": "application/json", "X-OpenWebUI-User-Id": "abc"}
        self.assertTrue(router._spawn_allowed(hdrs, require_identity=False))
        self.assertFalse(router._spawn_allowed(hdrs, require_identity=True))

    def test_default_follows_module_env(self):
        # require_identity=None defers to router.REQUIRE_IDENTITY (env-derived).
        old = router.REQUIRE_IDENTITY
        try:
            router.REQUIRE_IDENTITY = True
            self.assertFalse(router._spawn_allowed({}))
            router.REQUIRE_IDENTITY = False
            self.assertTrue(router._spawn_allowed({}))
        finally:
            router.REQUIRE_IDENTITY = old


class TestSpawnGateJwtMode(unittest.TestCase):
    """Signed-identity mode (review identity-rbac-1). Open WebUI sends the role
    ONLY inside X-OpenWebUI-User-Jwt when FORWARD_USER_INFO_HEADER_JWT_SECRET
    is set, so a header-only gate read every guest turn as anonymous and let
    it spawn an agent with the admin key."""

    SECRET = "router-test-secret-at-least-32-bytes-long"

    def _mint(self, role="admin", secret=None, exp_delta=300, iss="open-webui"):
        import time

        import jwt as pyjwt
        now = int(time.time())
        return pyjwt.encode({"sub": "u-1", "role": role, "iss": iss,
                             "iat": now, "exp": now + exp_delta},
                            secret or self.SECRET, algorithm="HS256")

    def _allowed(self, headers, require_identity=False):
        return router._spawn_allowed(headers, require_identity=require_identity,
                                     jwt_secret=self.SECRET)

    def test_verified_admin_token_spawns(self):
        self.assertTrue(self._allowed({"X-OpenWebUI-User-Jwt": self._mint("admin")}))

    def test_verified_user_token_cannot_spawn(self):
        # The exact failure: a `user`-role guest turn in JWT mode.
        self.assertFalse(self._allowed({"X-OpenWebUI-User-Jwt": self._mint("user")}))

    def test_forged_expired_or_garbage_token_cannot_spawn(self):
        for tok in (self._mint("admin", secret="wrong-secret"),
                    self._mint("admin", exp_delta=-60),
                    self._mint("admin", iss="someone-else"),
                    "x.y.z"):
            self.assertFalse(self._allowed({"X-OpenWebUI-User-Jwt": tok}), tok)

    def test_plain_role_header_ignored_in_jwt_mode(self):
        # WebUI never sends it in this mode, so one that arrives is typed.
        self.assertFalse(self._allowed({"X-OpenWebUI-User-Role": "admin"},
                                       require_identity=True))

    def test_header_mode_still_reads_plain_role(self):
        # Negative control: with no secret the plain header is the identity.
        self.assertTrue(router._spawn_allowed({"X-OpenWebUI-User-Role": "admin"},
                                              require_identity=True, jwt_secret=""))


class TestRequireIdentityDefault(unittest.TestCase):
    """REQUIRE_IDENTITY hardens by itself on multi-user rigs. conf.sh always
    exports OPENBEAST_ROUTER_REQUIRE_IDENTITY=false, so an opt-in the operator
    has to remember is not a control."""

    VARS = ("OPENBEAST_ROUTER_REQUIRE_IDENTITY", "OPENBEAST_WEBUI_AUTH",
            "OPENBEAST_IDENTITY_JWT_SECRET")

    def _reload_with(self, **env):
        import importlib
        saved = {k: os.environ.pop(k, None) for k in self.VARS}
        os.environ.update(env)
        try:
            importlib.reload(router)
            return router.REQUIRE_IDENTITY
        finally:
            for k in self.VARS:
                os.environ.pop(k, None)
                if saved[k] is not None:
                    os.environ[k] = saved[k]
            importlib.reload(router)

    def test_single_user_rig_stays_fail_open(self):
        self.assertFalse(self._reload_with(OPENBEAST_ROUTER_REQUIRE_IDENTITY="false",
                                           OPENBEAST_WEBUI_AUTH="false"))

    def test_webui_auth_hardens(self):
        self.assertTrue(self._reload_with(OPENBEAST_ROUTER_REQUIRE_IDENTITY="false",
                                          OPENBEAST_WEBUI_AUTH="true"))

    def test_jwt_secret_hardens(self):
        self.assertTrue(self._reload_with(OPENBEAST_ROUTER_REQUIRE_IDENTITY="false",
                                          OPENBEAST_IDENTITY_JWT_SECRET="s3cret"))

    def test_explicit_true_still_honored(self):
        self.assertTrue(self._reload_with(OPENBEAST_ROUTER_REQUIRE_IDENTITY="true"))


class TestSpawnCarriesIdentity(unittest.TestCase):
    """The spawn call forwards the caller's identity (review identity-rbac-6),
    so the tool server shards and audits it under the real account instead
    of an anonymous admin-key call."""

    class Recorder:
        def __init__(self):
            self.headers = None

        async def post(self, url, json=None, headers=None, timeout=None):
            self.headers = headers

            class R:
                text = '"started agent 20260929-120000-deadbeef"'

                def json(self):
                    return "started agent 20260929-120000-deadbeef"
            return R()

    def test_header_mode_forwards_plain_identity(self):
        incoming = {"X-OpenWebUI-User-Id": "alice", "X-OpenWebUI-User-Role": "admin",
                    "X-OpenWebUI-User-Email": "a@example.com",
                    "X-OpenWebUI-Chat-Id": "c-9", "Content-Type": "application/json"}
        ident = router._identity_headers(incoming, jwt_secret="")
        rec = self.Recorder()
        agent_id, err = asyncio.run(router._spawn(rec, "a task", ".", ident))
        self.assertEqual(agent_id, "20260929-120000-deadbeef")
        self.assertEqual(rec.headers["x-openwebui-user-id"], "alice")
        self.assertEqual(rec.headers["x-openwebui-chat-id"], "c-9")
        self.assertNotIn("Content-Type", rec.headers)

    def test_non_ascii_identity_is_forwarded_as_the_sent_bytes(self):
        """httpx encodes str header values as ASCII; Starlette handed us
        latin-1-decoded str. A non-ASCII email must not break the spawn."""
        import httpx
        raw = "jos\u00e9@example.com".encode("utf-8")
        incoming = {"X-OpenWebUI-User-Id": "u-1",
                    "X-OpenWebUI-User-Email": raw.decode("latin-1"),
                    "X-OpenWebUI-User-Name": "\u2603"}   # not latin-1: dropped
        ident = router._identity_headers(incoming, jwt_secret="")
        self.assertEqual(ident["x-openwebui-user-email"], raw)
        self.assertEqual(ident["x-openwebui-user-id"], "u-1")
        self.assertNotIn("x-openwebui-user-name", ident)
        req = httpx.Request("POST", "http://tools/start_agent",
                            headers={**ident, **router.MCPO_HEADERS})
        self.assertEqual(req.headers.raw[[k for k, _ in req.headers.raw].index(
            b"x-openwebui-user-email")][1], raw)

    def test_jwt_mode_forwards_only_the_token(self):
        incoming = {"X-OpenWebUI-User-Jwt": "tok", "X-OpenWebUI-User-Id": "forged",
                    "X-OpenWebUI-Chat-Id": "c-9"}
        ident = router._identity_headers(incoming, jwt_secret="s")
        self.assertEqual(ident, {"x-openwebui-user-jwt": "tok",
                                 "x-openwebui-chat-id": "c-9"})

    def test_admin_key_cannot_be_overridden_by_caller(self):
        old = dict(router.MCPO_HEADERS)
        try:
            router.MCPO_HEADERS.clear()
            router.MCPO_HEADERS["Authorization"] = "Bearer admin"
            rec = self.Recorder()
            asyncio.run(router._spawn(rec, "a task", ".", {"Authorization": "Bearer x"}))
            self.assertEqual(rec.headers["Authorization"], "Bearer admin")
        finally:
            router.MCPO_HEADERS.clear()
            router.MCPO_HEADERS.update(old)


class TestSchema(unittest.TestCase):
    def test_schema_requires_all_fields(self):
        self.assertEqual(set(router._SCHEMA["required"]),
                         {"spawn", "task", "workdir"})
        self.assertEqual(set(router._SCHEMA["properties"].keys()),
                         {"spawn", "task", "workdir"})


class TestUpstreamAuth(unittest.TestCase):
    """Keyed llama-server: the router's own classify call must present the
    bearer (read at import from OPENBEAST_API_KEY); the proxy path forwards
    the client's Authorization header (not in _HOP_BY_HOP)."""

    def _reload_with(self, key):
        import importlib
        saved = os.environ.pop("OPENBEAST_API_KEY", None)
        if key is not None:
            os.environ["OPENBEAST_API_KEY"] = key
        try:
            importlib.reload(router)
            return dict(router.UPSTREAM_HEADERS)
        finally:
            os.environ.pop("OPENBEAST_API_KEY", None)
            if saved is not None:
                os.environ["OPENBEAST_API_KEY"] = saved
            importlib.reload(router)

    def test_no_key_no_header(self):
        self.assertEqual(self._reload_with(None), {})

    def test_key_becomes_bearer(self):
        hdrs = self._reload_with("rig-key")
        self.assertEqual(hdrs.get("Authorization"), "Bearer rig-key")

    def test_authorization_not_hop_by_hop(self):
        # The transparent proxy strips only _HOP_BY_HOP headers — the client's
        # own Authorization must survive the relay to upstream.
        self.assertNotIn("authorization", router._HOP_BY_HOP)

    def test_router_binds_loopback_only(self):
        # The spawn path is fail-open by default, so the router must never
        # honor BIND_HOST — a 0.0.0.0 stack would otherwise expose agent-spawn
        # to the whole LAN.
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "agents", "router.py")).read()
        run_line = [ln for ln in src.splitlines() if "uvicorn.run(" in ln]
        self.assertTrue(run_line, "uvicorn.run call not found")
        self.assertIn('host="127.0.0.1"', run_line[0])
        self.assertNotIn("OPENBEAST_BIND", run_line[0])


class _Recorder:
    """Stands in for the router's httpx.AsyncClient: records the classify
    POST and every proxied request; answers classify with spawn=false."""

    def __init__(self):
        self.posts = []
        self.sent = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": dict(headers or {})})

        class R:
            def json(self_inner):
                return {"choices": [{"message": {"content": '{"spawn": false, "task": "", "workdir": "."}'}}]}
        return R()

    def build_request(self, method, url, content=None, headers=None):
        req = {"method": method, "url": url, "content": content, "headers": dict(headers or {})}
        self.sent.append(req)
        return req

    async def send(self, req, stream=False):
        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}

            async def aiter_raw(self_inner):
                yield b'{"ok": true}'

            async def aclose(self_inner):
                pass
        return Resp()

    async def aclose(self):
        pass


class TestHydraWiring(unittest.TestCase):
    """beast-hydra (docs/BEAST_HYDRA_PLAN.md §6.7): ROUTER_CLASSIFY_MODEL and
    the X-Hydra-Caller token — each inert when unconfigured (byte-identical)
    and exactly as specified when configured."""

    def setUp(self):
        import tempfile
        from hydra_caller import CallerToken
        self._saved = (router.CLASSIFY_MODEL, router._HYDRA_CALLER)
        self.tmp = tempfile.mkdtemp()
        self.tok_path = os.path.join(self.tmp, "hydra-caller.token")
        fd = os.open(self.tok_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, b"c" * 64 + b"\n")
        os.close(fd)
        self.CallerToken = CallerToken

    def tearDown(self):
        import shutil
        router.CLASSIFY_MODEL, router._HYDRA_CALLER = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _classify(self):
        rec = _Recorder()
        asyncio.run(router._classify(rec, "spawn an agent to do x"))
        return rec.posts[0]

    def test_classify_has_no_model_by_default(self):
        router.CLASSIFY_MODEL, router._HYDRA_CALLER = "", self.CallerToken("")
        post = self._classify()
        self.assertNotIn("model", post["json"])
        self.assertEqual(post["headers"], dict(router.UPSTREAM_HEADERS))

    def test_classify_carries_the_model_when_set(self):
        router.CLASSIFY_MODEL = "classify"
        self.assertEqual(self._classify()["json"]["model"], "classify")

    def test_env_sets_the_classify_model(self):
        import importlib
        os.environ["ROUTER_CLASSIFY_MODEL"] = "classify"
        try:
            importlib.reload(router)
            self.assertEqual(router.CLASSIFY_MODEL, "classify")
        finally:
            os.environ.pop("ROUTER_CLASSIFY_MODEL", None)
            importlib.reload(router)
        self.assertEqual(router.CLASSIFY_MODEL, "")

    def test_caller_token_on_classify(self):
        router._HYDRA_CALLER = self.CallerToken(self.tok_path)
        self.assertEqual(self._classify()["headers"]["X-Hydra-Caller"], "c" * 64)

    def _proxy(self, headers):
        from starlette.testclient import TestClient
        rec = _Recorder()
        with TestClient(router.app) as c:
            router.app.state.client = rec
            r = c.post("/v1/models-probe", content=b'{"a":1}', headers=headers)
        self.assertEqual(r.status_code, 200)
        return rec.sent[0]

    def test_caller_token_replaces_a_callers_on_proxied_requests(self):
        router._HYDRA_CALLER = self.CallerToken(self.tok_path)
        sent = self._proxy({"X-Hydra-Caller": "forged", "X-OpenWebUI-User-Role": "admin"})
        vals = [v for k, v in sent["headers"].items() if k.lower() == "x-hydra-caller"]
        self.assertEqual(vals, ["c" * 64])
        self.assertEqual(sent["headers"].get("x-openwebui-user-role"), "admin")

    def test_unconfigured_proxy_adds_nothing(self):
        router._HYDRA_CALLER = self.CallerToken("")
        sent = self._proxy({"X-Test": "1"})
        self.assertFalse([k for k in sent["headers"] if k.lower() == "x-hydra-caller"])
        self.assertEqual(sent["content"], b'{"a":1}')

    def test_world_readable_token_is_never_sent(self):
        """Fail closed: a token other local users can read proves nothing."""
        os.chmod(self.tok_path, 0o644)
        router._HYDRA_CALLER = self.CallerToken(self.tok_path)
        self.assertNotIn("X-Hydra-Caller", self._classify()["headers"])
        os.chmod(self.tok_path, 0o600)                  # negative control
        router._HYDRA_CALLER = self.CallerToken(self.tok_path)
        self.assertIn("X-Hydra-Caller", self._classify()["headers"])


if __name__ == "__main__":
    unittest.main()
