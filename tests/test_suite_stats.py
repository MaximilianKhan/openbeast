#!/usr/bin/env python3
"""Suite-stats generator checks + doc-drift guard.

  - collect() agrees with a naive recount of evals/tasks/*.json
  - headline docs (README.md, evals/README.md) cite the CURRENT base-task and
    effective-unit counts — the "313 / 323 / 223 units" drift class can't
    come back silently
  - every render mode produces output containing the headline numbers

Run: pytest tests/test_suite_stats.py
"""

import glob
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "evals"))

import suite_stats  # noqa: E402


def naive_counts():
    base = units = 0
    for path in glob.glob(os.path.join(REPO, "evals", "tasks", "*.json")):
        with open(path) as f:
            spec = json.load(f)
        base += 1
        units += len(spec["variants"]) if spec.get("variants") else 1
    return base, units


def test_collect_matches_naive_recount():
    s = suite_stats.collect()
    base, units = naive_counts()
    assert s["base_tasks"] == base
    assert s["effective_units"] == units
    assert sum(s["difficulty_units"].values()) == units
    assert sum(s["language_units"].values()) == units


def test_docs_cite_current_counts():
    """The real guard: --check exits 0 only when README + evals/README cite
    the current headline numbers."""
    r = subprocess.run(
        [sys.executable, os.path.join(REPO, "evals", "suite_stats.py"), "--check"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"doc drift detected:\n{r.stdout}{r.stderr}"


def test_render_modes_contain_headlines():
    s = suite_stats.collect()
    for text in (suite_stats.render_text(s), suite_stats.render_markdown(s)):
        assert str(s["base_tasks"]) in text
        assert str(s["effective_units"]) in text
