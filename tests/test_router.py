#!/usr/bin/env python3
"""
Unit tests for the agent-spawn router (agents/router.py) — no server needed.

Covers the pure/deterministic surface: the _HINTS prefilter (precision AND
recall lists), last-user-turn extraction across content shapes, the
synthetic OpenAI-shaped replies (non-stream + stream), classify fail-safe
behavior, and the grammar schema contract.

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


class TestSchema(unittest.TestCase):
    def test_schema_requires_all_fields(self):
        self.assertEqual(set(router._SCHEMA["required"]),
                         {"spawn", "task", "workdir"})
        self.assertEqual(set(router._SCHEMA["properties"].keys()),
                         {"spawn", "task", "workdir"})


if __name__ == "__main__":
    unittest.main()
