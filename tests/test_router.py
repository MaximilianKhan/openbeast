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


class TestSchema(unittest.TestCase):
    def test_schema_requires_all_fields(self):
        self.assertEqual(set(router._SCHEMA["required"]),
                         {"spawn", "task", "workdir"})
        self.assertEqual(set(router._SCHEMA["properties"].keys()),
                         {"spawn", "task", "workdir"})


if __name__ == "__main__":
    unittest.main()
