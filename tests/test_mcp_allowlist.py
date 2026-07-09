#!/usr/bin/env python3
"""RBAC Phase 2 — OPENBEAST_MCP_TOOLS registration allowlist.

The guest MCPO instance runs mcp_server.py with
OPENBEAST_MCP_TOOLS="web_search,fetch"; these tests prove the allowlist is
enforced at REGISTRATION time (denied tools don't exist on the server at
all), not merely hidden. Each case imports mcp_server in a subprocess so the
env var is read fresh.

Run: pytest tests/test_mcp_allowlist.py
"""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LIST_TOOLS = """
import sys, json, asyncio
sys.path.insert(0, {agents_dir!r})
import mcp_server
tools = asyncio.run(mcp_server.mcp.list_tools())
print(json.dumps(sorted(t.name for t in tools)))
"""


def registered_tools(env_value=None):
    env = os.environ.copy()
    env.pop("OPENBEAST_MCP_TOOLS", None)
    if env_value is not None:
        env["OPENBEAST_MCP_TOOLS"] = env_value
    code = _LIST_TOOLS.format(agents_dir=os.path.join(REPO, "agents"))
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_default_registers_all_tools():
    tools = registered_tools(None)
    assert len(tools) == 15, tools
    assert "bash" in tools and "web_search" in tools


def test_guest_allowlist_registers_only_web_tools():
    tools = registered_tools("web_search,fetch")
    assert tools == ["fetch", "web_search"], tools


def test_empty_allowlist_means_no_filtering():
    # Empty / whitespace value = unset (never "zero tools" by accident).
    tools = registered_tools("")
    assert len(tools) == 15, tools


def test_allowlist_tolerates_spaces():
    tools = registered_tools(" web_search , fetch ")
    assert tools == ["fetch", "web_search"], tools
