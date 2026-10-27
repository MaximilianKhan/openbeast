#!/usr/bin/env python3
"""Review 2026-10-27 regression for beast-instinct (R3-F9): the scorer
launcher's bind cannot be overridden by extra args. Driven only into its
refusal paths; no llama-server is ever started.

Run: python3 -m pytest tests/test_review1027_instinct.py -q
"""
from __future__ import annotations

import pytest

from test_instinct_cli import SCORER_SH, free_port, scorer_env, sh


@pytest.mark.parametrize("extra", [["--host", "0.0.0.0"], ["--host=0.0.0.0"],
                                   ["-p", "8080"], ["--port", "8080"], ["--port=8080"],
                                   ["--metrics", "--host", "0.0.0.0"]])
def test_scorer_refuses_bind_overrides(tmp_path, extra):
    env = scorer_env(tmp_path, INSTINCT_SCORER_PORT=str(free_port()),
                     INFERENCE_URL="http://127.0.0.1:1")
    r = sh(SCORER_SH, *extra, env=env)
    assert r.returncode == 2 and "refused" in r.stderr, (r.returncode, r.stderr)
    assert "no-such-weights" not in r.stderr          # refused BEFORE the weights check


def test_scorer_other_extra_args_still_pass(tmp_path):
    """Negative control: an unrelated extra arg gets past the refusals and
    stops at the (deliberately missing) weights directory."""
    env = scorer_env(tmp_path, INSTINCT_SCORER_PORT=str(free_port()),
                     INFERENCE_URL="http://127.0.0.1:1")
    r = sh(SCORER_SH, "--metrics", env=env)
    assert r.returncode != 0 and "no-such-weights" in r.stderr
    assert "refused" not in r.stderr
