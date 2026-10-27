#!/usr/bin/env python3
"""Review 2026-10-27 — tool-server fixes (mcp_server / openapi_tools / router).

  R1-04  publish_artifact's workspace is never the process cwd
  R1-05  the unauthenticated MCP HTTP transport refuses a non-loopback bind
  R1-07  the workdir-escape and shard-creation refusals reach the audit trail
  R1-09  the router does not 500 on a non-list / non-dict `messages`
  R1-10  stop_agent stops a ledger-only agent (and only a provably-ours one)
  R1-11  the MCP grep wrapper forwards every parameter tools.grep takes
  R1-12  the audit args digest is keyed by a per-install secret
  R9-02  skill(name) / start_skill_agent never serve a stale or deleted body
  R9-05  a hand-installed global skill is labelled unverified
  R7-02  the runner withholds the ambient key from a foreign host even when a
         worker URL IS configured (test-only; runner.py unchanged)
  R7-06  a signed identity token without `exp` is refused (test-only)

Everything is in-process or a stubbed subprocess: no stack port is bound.

Run: python3 -m pytest tests/test_review1027_toolserver.py -q
"""
import asyncio
import hashlib
import importlib
import inspect
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
sys.path.insert(0, AGENTS)

import mcp_server  # noqa: E402
import openapi_tools  # noqa: E402
import sessions  # noqa: E402
import tools as _tools  # noqa: E402


# ── R1-11 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["bash", "read_file", "write_file",
                                  "edit_file", "list_files", "grep", "fetch",
                                  "web_search"])
def test_r1_11_mcp_wrappers_match_tools_signatures(name):
    wrap = inspect.signature(getattr(mcp_server, name)).parameters
    impl = inspect.signature(getattr(_tools, name)).parameters
    assert list(wrap) == list(impl)
    for pname in impl:
        assert wrap[pname].default == impl[pname].default, pname


def test_r1_11_grep_ignore_case_and_context_reach_tools(tmp_path):
    (tmp_path / "f.txt").write_text("before\nHELLO world\nafter\n")
    out = mcp_server.grep("hello", str(tmp_path), "", context_lines=1,
                          ignore_case=True)
    assert "HELLO world" in out and "before" in out
    assert mcp_server.grep("hello", str(tmp_path)) .startswith("(no matches)")


