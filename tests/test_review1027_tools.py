"""Tool fixes from the 2026-10-27 review (agents/tools.py, eval-era file).

R1-02  list_files never follows a directory symlink and is bounded.
R1-03  edit_file refuses files over read_file's size cap.
R1-06  the bash tool kills everything the command backgrounded, redirected
       or nohup'd jobs included, as its description says.
R1-08  fetch returns `Error:` on a malformed URL or max_length, never raises.
R6-11  under eval, the file tools refuse OPENBEAST_EVAL_DENY_ROOTS.
"""

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

import tools  # noqa: E402


# --------------------------------------------------------------------------
# R1-08
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://example.com:99999/", "http://[::1/",
                                 "http://a:b/"])
def test_fetch_malformed_url_is_an_error_string(url):
    out = tools.fetch(url)
    assert out.startswith("Error: invalid URL")


@pytest.mark.parametrize("bad", ["abc", None, float("inf")])
def test_fetch_bad_max_length_is_an_error_string(bad, monkeypatch):
    # The URL check passes without DNS for a literal public IP.
    monkeypatch.setattr(tools, "_fetch_body",
                        lambda *a, **k: pytest.fail("must not fetch"))
    out = tools.fetch("http://93.184.215.14/", max_length=bad)
    assert out.startswith("Error: max_length")


