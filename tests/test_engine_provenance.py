"""The eval row must record WHICH llama.cpp produced it.

Max, 2026-09-15: "No, I don't want to pin llama.cpp. I always want to use the
latest." That is a legitimate product choice — this repo has repeatedly needed
a fresh llama.cpp for a new model architecture. It does mean provenance is the
ONLY thing preserving reproducibility, so provenance has to actually work.

It did not. The parser knew one upstream format:
    old: "version: 8893 (6217b4958)"
and llama.cpp now prints:
    new: "version: 0.4.0-dev (build 10865, commit d4389a4dd)"
so `build` and `commit` were absent from every row — silently, because the
parse failure was a quiet `if m:`. What the row DID carry was `source_head`,
the llama.cpp source TREE's HEAD, which is not what the binary was built from:
on this rig the tree is 8e126574 while the running binary is build 10865 /
commit d4389a4dd. The field naming what produced a measurement was empty, and
the populated one named something else.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A PRIVATE copy of the module: other suites replace run_eval.run_agent
# globally and never put it back (see tests/test_steering.py's note).
_spec = importlib.util.spec_from_file_location(
    "run_eval_prov_probe", os.path.join(ROOT, "evals", "run_eval.py"))
run_eval = importlib.util.module_from_spec(_spec)
sys.path.insert(0, os.path.join(ROOT, "evals"))
_spec.loader.exec_module(run_eval)

NEW = ("version: 0.4.0-dev (build 10865, commit d4389a4dd)\n"
       "built with GNU 16.2.1 for Linux x86_64\n")
OLD = ("version: 8893 (6217b4958)\n"
       "built with GNU 15.2.1 for Linux x86_64\n")


def test_the_current_upstream_format_is_parsed():
    """The regression that started this. If it breaks again, every row loses
    the identity of the binary that produced it."""
    got = run_eval.parse_engine_version(NEW)
    assert got["build"] == "10865"
    assert got["commit"] == "d4389a4dd"
    assert got["compiler"] == "GNU 16.2.1"
    assert got["target"] == "Linux x86_64"
    assert "version_parse" not in got


def test_the_older_upstream_format_still_parses():
    """Old rows and older checkouts exist; dropping support would make THEM
    unreadable instead."""
    got = run_eval.parse_engine_version(OLD)
    assert got["build"] == "8893" and got["commit"] == "6217b4958"


def test_an_unknown_format_is_REPORTED_not_swallowed():
    """The actual lesson. A silent `if m:` is how this hid for months: two
    keys quietly missing is invisible, a key saying UNRECOGNISED is not."""
    got = run_eval.parse_engine_version("version: 2.0 rev=abc123 built-by-magic\n")
    assert got.get("version_parse", "").startswith("UNRECOGNISED")
    assert got["version_raw"] == "version: 2.0 rev=abc123 built-by-magic"
    assert "build" not in got and "commit" not in got


def test_no_version_line_at_all_is_also_reported():
    got = run_eval.parse_engine_version("some unrelated output\n")
    assert got["version_raw"] == "(no version line)"
    assert got.get("version_parse", "").startswith("UNRECOGNISED")


def test_the_live_binary_reports_build_and_commit_if_it_is_present():
    """Against the real llama-server when there is one. Skipped rather than
    failed where the engine is not built (CI)."""
    info = run_eval.capture_inference_engine_info()
    binary = info.get("binary", "")
    if not binary or not os.path.exists(binary):
        import pytest
        pytest.skip("llama-server not built here")
    assert info.get("build"), f"no build recorded: {info}"
    assert info.get("commit"), f"no commit recorded: {info}"
    # source_head may legitimately DIFFER from the binary's commit — that is
    # the whole point of recording both, and why recording only one was a bug.
    assert "name" in info and info["name"] == "llama.cpp"
