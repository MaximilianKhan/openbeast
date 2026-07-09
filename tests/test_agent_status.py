#!/usr/bin/env python3
"""
Truth-table tests for agents/mcp_server.py::_classify_agent_status — the
single source of agent status strings shared by check_agent, list_agents,
and orphaned-log reporting.

Run: python -m pytest tests/test_agent_status.py -v
  or: python3 tests/test_agent_status.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

from mcp_server import _classify_agent_status


DONE = [{"type": "done", "summary": "ok"}]
MAX_ITER = [{"type": "max_iterations"}]


class TestClassifyAgentStatus(unittest.TestCase):
    def test_done_means_completed(self):
        self.assertEqual(_classify_agent_status(alive=False, events=DONE), "completed")

    def test_done_wins_even_when_orphaned(self):
        # A terminal event is authoritative — a server restart afterwards
        # doesn't make the outcome unknown.
        self.assertEqual(
            _classify_agent_status(alive=False, events=DONE, orphaned=True),
            "completed")

    def test_max_iterations(self):
        self.assertEqual(
            _classify_agent_status(alive=False, events=MAX_ITER),
            "max_iterations_reached")

    def test_orphaned_without_terminal_event_is_unknown(self):
        self.assertEqual(
            _classify_agent_status(alive=False, events=[], orphaned=True),
            "unknown (server restarted)")

    def test_dead_with_returncode(self):
        self.assertEqual(
            _classify_agent_status(alive=False, events=[], returncode=0),
            "exited (code 0)")

    def test_dead_without_returncode(self):
        self.assertEqual(
            _classify_agent_status(alive=False, events=[], returncode=None),
            "exited")

    def test_alive_is_running(self):
        self.assertEqual(_classify_agent_status(alive=True, events=[]), "running")


if __name__ == "__main__":
    unittest.main()
