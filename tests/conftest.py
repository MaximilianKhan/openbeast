#!/usr/bin/env python3
"""Pytest fixtures shared across the test suite."""

import pytest


@pytest.fixture
def cache_dir(tmp_path):
    """Provide an isolated cache directory for each test."""
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture
def td(tmp_path):
    """Provide an isolated temp directory (alias used by health-recovery tests)."""
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_tool_audit(tmp_path, monkeypatch):
    """Every test gets its own tool-call audit file.

    agents/openapi_tools.py appends a row per call to the rig's REAL security
    audit trail (.run/tool-audit.jsonl) unless told otherwise, and the suite
    drives it with forged users, denied /bash probes and "../../etc" ids —
    thousands of fixture rows an operator could not tell from an attack. A
    test that wants to read the rows reads $OPENBEAST_TOOL_AUDIT_PATH.
    """
    monkeypatch.setenv("OPENBEAST_TOOL_AUDIT_PATH",
                       str(tmp_path / "tool-audit" / "tool-audit.jsonl"))
