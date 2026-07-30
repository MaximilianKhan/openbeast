#!/usr/bin/env python3
"""beast-slot /api/slot contract tests (docs/BEAST_SLOT.md).

The dashboard extension's slot_status() is the discovery surface clients
depend on: real loaded model, slots busy/total, context, health, auth mode.
These tests pin the JSON shape against canned upstream bodies so a
llama-server or dashboard refactor can't silently break remote clients.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "dashboard", os.path.join(REPO, "extensions", "dashboard", "dashboard.py"))
dashboard = importlib.util.module_from_spec(_SPEC)
sys.modules["dashboard"] = dashboard
_SPEC.loader.exec_module(dashboard)


_PROPS = {"total_slots": 1, "model_alias": "heretic-v2-27b-mtp-q6",
          "model_path": "/weights/x.gguf"}
# Current llama-server slot shape (is_processing); older builds used state.
_SLOTS_CURRENT = [{"id": 0, "n_ctx": 212992, "is_processing": True}]
_SLOTS_LEGACY = [{"id": 0, "n_ctx": 65536, "state": 1}]
_MODELS = {"data": [{"id": "heretic-v2-27b-mtp-q6"}]}


def _fake_get(responses):
    """responses: dict of url-suffix -> (status, body-dict-or-str)."""
    def get(url, timeout=2, auth=False):
        for suffix, (st, body) in responses.items():
            if url.endswith(suffix):
                return st, body if isinstance(body, str) else json.dumps(body)
        return None, ""
    return get


class TestSlotContract(unittest.TestCase):
    def setUp(self):
        self._real_get = dashboard._get

    def tearDown(self):
        dashboard._get = self._real_get

    def _status(self, responses):
        dashboard._get = _fake_get(responses)
        return dashboard.slot_status()

    def test_full_healthy_contract(self):
        out = self._status({
            "/health": (200, "ok"), "/v1/models": (200, _MODELS),
            "/props": (200, _PROPS), "/slots": (200, _SLOTS_CURRENT),
            "/api/version": (200, "version x"), "8888/": (200, ""),
        })
        self.assertEqual(out["beast_slot"], 1)
        self.assertTrue(out["healthy"])
        self.assertEqual(out["model"]["id"], "heretic-v2-27b-mtp-q6")
        self.assertEqual(out["model"]["ctx"], 212992)
        self.assertEqual(out["slots"], {"total": 1, "busy": 1})
        self.assertEqual(out["auth"], "open")
        # Contract keys are exactly these — clients parse this shape.
        self.assertEqual(
            set(out), {"beast_slot", "healthy", "model", "slots",
                       "services", "auth"})

    def test_legacy_state_field_counts_busy(self):
        out = self._status({
            "/health": (200, "ok"), "/props": (200, _PROPS),
            "/slots": (200, _SLOTS_LEGACY),
        })
        self.assertEqual(out["slots"]["busy"], 1)
        self.assertEqual(out["model"]["ctx"], 65536)

    def test_slots_endpoint_disabled_degrades_to_null(self):
        # --no-slots on the server: busy must be null, never a guess.
        out = self._status({
            "/health": (200, "ok"), "/v1/models": (200, _MODELS),
            "/props": (200, _PROPS), "/slots": (501, "disabled"),
        })
        self.assertIsNone(out["slots"]["busy"])
        self.assertEqual(out["slots"]["total"], 1)   # from /props

    def test_model_down_still_answers(self):
        out = self._status({})
        self.assertFalse(out["healthy"])
        self.assertIsNone(out["model"]["id"])
        self.assertIsNone(out["slots"]["total"])
        self.assertEqual(out["beast_slot"], 1)

    def test_no_prompt_or_key_material_in_output(self):
        os.environ["OPENBEAST_API_KEY"] = "super-sekrit"
        try:
            # _API_KEY is read at import; simulate a keyed dashboard.
            saved = dashboard._API_KEY
            dashboard._API_KEY = "super-sekrit"
            out = self._status({
                "/health": (200, "ok"), "/props": (200, _PROPS),
                "/slots": (200, _SLOTS_CURRENT),
            })
            self.assertEqual(out["auth"], "key")
            self.assertNotIn("super-sekrit", json.dumps(out))
        finally:
            dashboard._API_KEY = saved
            del os.environ["OPENBEAST_API_KEY"]

    def test_slot_count_agnostic(self):
        # Future multi-slot serving profile: same shape, bigger numbers.
        many = [{"id": i, "n_ctx": 58000,
                 "is_processing": i < 3} for i in range(6)]
        out = self._status({
            "/health": (200, "ok"),
            "/props": (200, {"total_slots": 6, "model_alias": "m"}),
            "/slots": (200, many),
        })
        self.assertEqual(out["slots"], {"total": 6, "busy": 3})


if __name__ == "__main__":
    unittest.main()
