#!/usr/bin/env python3
"""Review 2026-10-27 — network-facing services (R2-4).

beast-chat's device-registry reader on wrong-shaped JSON. Each test builds
its own registry under tmp_path; nothing binds a port.

Run: python3 -m pytest tests/test_review1027_services.py -q
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "agents"))


# ---------------------------------------------------------------------------
# R2-4 — beast-chat's registry reader on wrong-shaped JSON
# ---------------------------------------------------------------------------

def _chat_server():
    import chat_server
    return chat_server


class TestR2_4ChatRegistryShape:
    KEY = "k" * 40

    def _good(self):
        import hashlib
        return {"version": 1, "devices": [
            {"id": "lap", "scopes": ["chat"],
             "key_sha256": hashlib.sha256(self.KEY.encode()).hexdigest()}]}

    @pytest.mark.parametrize("content", ["[]", "null", "5",
                                         '{"devices": 5}',
                                         '{"devices":[{"key_sha256":5}]}'])
    def test_wrong_shape_at_startup_does_not_raise(self, tmp_path, content):
        cs = _chat_server()
        p = tmp_path / "clients.json"
        p.write_text(content)
        reg = cs.DeviceRegistry(str(p))          # used to AttributeError
        assert reg.lookup(self.KEY) is None

    @pytest.mark.parametrize("content", ["[]", "null", '{"devices": 5}'])
    def test_wrong_shape_at_runtime_keeps_last_good_map(self, tmp_path,
                                                        content):
        cs = _chat_server()
        p = tmp_path / "clients.json"
        p.write_text(json.dumps(self._good()))
        reg = cs.DeviceRegistry(str(p))
        assert reg.lookup(self.KEY)["id"] == "lap"
        p.write_text(content + " " * 7)          # new size → new stamp
        assert reg.lookup(self.KEY)["id"] == "lap"   # no 500, last good map
        assert reg.configured is True

    def test_valid_file_unchanged(self, tmp_path):
        # Negative control: a good file still loads and a missing devices
        # key still reads as "no devices".
        cs = _chat_server()
        p = tmp_path / "clients.json"
        p.write_text(json.dumps(self._good()))
        assert cs.DeviceRegistry(str(p)).lookup(self.KEY)["id"] == "lap"
        p.write_text('{"version": 1}')
        assert cs.DeviceRegistry(str(p)).configured is False
