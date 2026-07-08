#!/usr/bin/env python3
"""Pytest fixtures shared across the test suite."""

import pytest
from pathlib import Path


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
