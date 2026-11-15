#!/usr/bin/env python3
"""platform_docs — beast-docs on the MCP / WebUI surface (docs/BEAST_DOCS.md §5).

Same stub server and fixtures as tests/test_beast_docs.py. Pinned: search,
read, status and live through the tool; the input clip; admin-only on the
WebUI surface; and NOT in the runner's registry (agents/tools.py is the 27B
runner's tool list and is hashed into the eval era).

Run: python3 -m pytest tests/test_platform_docs_tool.py -q
"""
from __future__ import annotations

import os

from test_beast_docs import AGENTS, _sync, env, stub  # noqa: F401  (fixtures)


def test_platform_docs_tool(env):  # noqa: F811 — the fixture imported above
    import mcp_server
    _sync("swiftui")
    out = mcp_server.platform_docs(query="NavigationStack")
    assert "swiftui:/documentation/swiftui/navigationstack" in out
    out = mcp_server.platform_docs(doc="swiftui:/documentation/swiftui/navigationstack")
    assert out.startswith("Source: ") and "## Declaration" in out
    assert "beast-docs mirror:" in mcp_server.platform_docs()
    assert "No mirrored page" in mcp_server.platform_docs(doc="swiftui:/nope")
    assert len(mcp_server.platform_docs(query="x" * 100_000)) < 20_000
    out = mcp_server.platform_docs(doc="NavigationStack", source="swiftui", live=True)
    assert "LIVE · fetched" in out
    assert "platform_docs" in __import__("openapi_tools").TOOL_NAMES
    assert "platform_docs" not in __import__("openapi_tools").GUEST_TOOLS


def test_platform_docs_not_in_the_runner_registry():
    # agents/tools.py is hashed into the eval era and is the 27B runner's
    # registry: beast-docs must not appear in it.
    with open(os.path.join(AGENTS, "tools.py")) as fh:
        assert "platform_docs" not in fh.read()
    with open(os.path.join(AGENTS, "runner.py")) as fh:
        assert "platform_docs" not in fh.read()
