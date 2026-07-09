#!/usr/bin/env python3
"""Workspace write manifest (.manifest.jsonl in OPENBEAST_FILES_DIR).

Covers:
  - write_file / edit_file inside the workspace append a manifest entry
    (relative path, byte count, action)
  - writes OUTSIDE the workspace are not indexed
  - the manifest never indexes itself
  - no OPENBEAST_FILES_DIR = no manifest, no error (fail-soft contract)

Run: pytest tests/test_manifest.py
"""

import importlib
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import tools  # noqa: E402


def _setup(tmp, monkeypatch):
    monkeypatch.setenv("OPENBEAST_FILES_DIR", tmp)
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    importlib.reload(tools)


def _manifest(tmp):
    p = os.path.join(tmp, ".manifest.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_write_and_edit_are_indexed(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp, monkeypatch)
        assert "Wrote" in tools.write_file("report.md", "hello beast")
        assert "Edited" in tools.edit_file("report.md", "beast", "world")
        entries = _manifest(tmp)
        assert [e["action"] for e in entries] == ["write", "edit"]
        assert entries[0]["path"] == "report.md"
        assert entries[0]["bytes"] == len("hello beast")
        assert all("ts" in e for e in entries)


def test_subdir_paths_are_relative(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp, monkeypatch)
        tools.write_file("charts/q3/plot.txt", "data")
        entries = _manifest(tmp)
        assert entries[0]["path"] == os.path.join("charts", "q3", "plot.txt")


def test_outside_workspace_not_indexed(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
        _setup(tmp, monkeypatch)
        tools.write_file(os.path.join(other, "elsewhere.txt"), "x")
        assert _manifest(tmp) == []


def test_manifest_never_indexes_itself(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp, monkeypatch)
        forged = json.dumps({"action": "forged"}) + "\n"
        tools.write_file(".manifest.jsonl", forged)
        # The write itself lands (it's a normal file write) but appends no
        # additional entry describing itself — only the forged line remains.
        assert _manifest(tmp) == [{"action": "forged"}]


def test_no_workspace_env_is_failsoft(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.delenv("OPENBEAST_FILES_DIR", raising=False)
        monkeypatch.delenv("AGENT_WORKDIR", raising=False)
        importlib.reload(tools)
        target = os.path.join(tmp, "free.txt")
        assert "Wrote" in tools.write_file(target, "x")
        assert not os.path.exists(os.path.join(tmp, ".manifest.jsonl"))