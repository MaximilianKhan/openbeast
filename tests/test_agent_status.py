#!/usr/bin/env python3
"""
Truth-table tests for agents/mcp_server.py::_classify_agent_status — the
single source of agent status strings shared by check_agent, list_agents,
and orphaned-log reporting — plus the byte-offset transcript reader
(`tail_transcript`) those same tools follow a live agent with.

Run: python -m pytest tests/test_agent_status.py -v
  or: python3 tests/test_agent_status.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

from mcp_server import _classify_agent_status, tail_transcript


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


class TestTailTranscript(unittest.TestCase):
    """A follow-mode read must never hand back half of an event.

    The offset cursor is the whole contract: whatever is returned, the next
    call starts where this one stopped. Emitting a partial line breaks it
    twice — the caller cannot parse what it got, and the remainder arrives
    later looking like a fresh event.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="openbeast-tail-test.")
        self.log = os.path.join(self.dir, "agent-x.jsonl")

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def _write(self, data: str):
        with open(self.log, "w") as fh:
            fh.write(data)

    def test_whole_lines_are_returned_and_the_offset_advances(self):
        self._write('{"a":1}\n{"b":2}\n')
        out = tail_transcript(self.log)
        self.assertEqual(out["content"], '{"a":1}\n{"b":2}\n')
        self.assertEqual(out["offset"], out["size"])
        self.assertFalse(out["long_line"])

    def test_oversized_line_is_read_whole_not_split(self):
        # 80 KB on one line, well past the 50 KB read window: an ordinary
        # large file-write tool call or a long assistant message does this.
        big = '{"type":"tool_result","result":"' + ("x" * 80_000) + '"}'
        self._write(big + "\n")
        out = tail_transcript(self.log)
        self.assertFalse(out["long_line"])
        self.assertTrue(out["content"].endswith('"}\n'))
        self.assertEqual(out["offset"], out["size"],
                         "the cursor must not stop inside a line")
        # The bytes are usable as events, not as a fragment.
        self.assertEqual(len(out["content"].splitlines()), 1)

    def test_line_past_the_ceiling_returns_nothing_and_says_why(self):
        self._write("y" * 300_000 + "\n")
        out = tail_transcript(self.log, 0, max_bytes=1_000,
                              max_line_bytes=10_000)
        self.assertTrue(out["long_line"])
        self.assertEqual(out["content"], "")
        self.assertEqual(out["offset"], 0,
                         "a refused read must not advance the cursor")

    def test_unterminated_tail_is_held_until_the_newline_lands(self):
        self._write('{"a":1}\n{"partial":')
        first = tail_transcript(self.log)
        self.assertEqual(first["content"], '{"a":1}\n')
        self.assertFalse(first["long_line"])
        with open(self.log, "a") as fh:
            fh.write('true}\n')
        second = tail_transcript(self.log, first["offset"])
        self.assertEqual(second["content"], '{"partial":true}\n',
                         "the held line must arrive whole, exactly once")
        self.assertEqual(second["offset"], second["size"])

    def test_a_shorter_file_resets_to_zero(self):
        self._write('{"a":1}\n')
        out = tail_transcript(self.log, 10_000)
        self.assertTrue(out["reset"])
        self.assertEqual(out["content"], '{"a":1}\n')


if __name__ == "__main__":
    unittest.main()
