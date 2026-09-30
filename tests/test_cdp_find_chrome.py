#!/usr/bin/env python3
"""Both browser helpers pick the Chrome binary the same way.

tests/_cdp_pipe.py (artifact + e2e browser suites) read CHROME_BIN and
tests/chat_cdp.py (chat console) read OPENBEAST_TEST_CHROME, so setting one
steered only half the browser suites and the other half skipped quietly.

Run: python3 -m pytest tests/test_cdp_find_chrome.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _cdp_pipe  # noqa: E402
import chat_cdp  # noqa: E402


@pytest.fixture
def fake_chrome(tmp_path, monkeypatch):
    exe = tmp_path / "my-chrome"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.delenv("OPENBEAST_TEST_CHROME", raising=False)
    return exe


@pytest.mark.parametrize("var", ["CHROME_BIN", "OPENBEAST_TEST_CHROME"])
def test_either_variable_steers_every_browser_suite(fake_chrome, monkeypatch, var):
    monkeypatch.setenv(var, str(fake_chrome))
    assert _cdp_pipe.find_chrome() == str(fake_chrome)
    assert chat_cdp.find_chrome() == str(fake_chrome)


def test_a_non_executable_override_is_not_used(fake_chrome, monkeypatch):
    fake_chrome.chmod(0o644)
    monkeypatch.setenv("CHROME_BIN", str(fake_chrome))
    monkeypatch.setenv("PATH", str(fake_chrome.parent / "empty"))
    assert _cdp_pipe.find_chrome() is None and chat_cdp.find_chrome() is None
