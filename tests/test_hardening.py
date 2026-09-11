"""2026-09-10 tools-hardening bundle tests (from the 9-agent SOTA review).

Run: python3 -m pytest tests/test_hardening.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
import tools  # noqa: E402


# --- bash fidelity ---------------------------------------------------------

def test_bash_tail_survives_truncation():
    # Verdict lives at the tail: a marker line printed AFTER 6MB of noise
    # must be visible in the result (head-only capture discarded it).
    out = tools.bash("python3 -c \"import sys; sys.stdout.write('x'*6_000_000); print(); print('VERDICT_LINE_AT_TAIL')\"", timeout=60)
    assert "VERDICT_LINE_AT_TAIL" in out
    assert "elided" in out or "truncated" in out


def test_bash_exit_code_visible_with_output():
    out = tools.bash("echo some output; exit 3", timeout=30)
    assert "some output" in out and "(exit code 3)" in out


def test_bash_zero_exit_no_noise():
    out = tools.bash("echo fine", timeout=30)
    assert "fine" in out and "exit code" not in out


def test_bash_timeout_returns_partial_output():
    out = tools.bash("echo BEFORE_THE_HANG; sleep 30", timeout=2)
    assert "timed out" in out and "BEFORE_THE_HANG" in out


# --- grep hardening --------------------------------------------------------

def test_grep_leading_dash_pattern_is_literal(tmp_path):
    (tmp_path / "a.txt").write_text("keep -v flags literal\n")
    out = tools.grep("-v", str(tmp_path))
    assert "keep -v flags literal" in out  # not option-injected invert-match


def test_grep_skips_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "blob").write_text("NEEDLE\n")
    (tmp_path / "src.txt").write_text("NEEDLE\n")
    out = tools.grep("NEEDLE", str(tmp_path))
    assert "src.txt" in out and ".git" not in out


def test_grep_pcre_hint_on_zero_matches(tmp_path):
    (tmp_path / "a.txt").write_text("abc 123\n")
    out = tools.grep(r"\d+", str(tmp_path))
    assert "POSIX ERE" in out


def test_grep_max_results_cap(tmp_path):
    (tmp_path / "many.txt").write_text("hit\n" * 50)
    out = tools.grep("hit", str(tmp_path), max_results=10)
    assert "more matching lines elided" in out


# --- read_file caps --------------------------------------------------------

def test_read_file_clamps_minified_line(tmp_path):
    p = tmp_path / "min.js"
    p.write_text("short\n" + "y" * 500_000 + "\nafter\n")
    out = tools.read_file(str(p))
    assert len(out) < 120_000 and "line truncated" in out


def test_read_file_offset_past_eof_errors(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("a\nb\n")
    out = tools.read_file(str(p), offset=99)
    assert out.startswith("Error:") and "past the end" in out


def test_read_file_resume_hint(tmp_path):
    p = tmp_path / "long.txt"
    p.write_text("line\n" * 700)
    out = tools.read_file(str(p), limit=500)
    # 1-based: after lines 1-500 the next page starts at 501.
    assert "offset=501" in out
    nxt = tools.read_file(str(p), offset=501, limit=500)
    assert "lines 501-700 of 700" in nxt


# --- zig has_main ----------------------------------------------------------

def test_zig_mainloop_is_not_main(tmp_path, monkeypatch):
    import shutil as _sh
    if not _sh.which("zig"):
        pytest.skip("zig not installed")
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    lib = tmp_path / "lib.zig"
    # library code with fn mainLoop but no pub fn main — must use ast-check,
    # not build-exe (which would fabricate a missing-main error)
    lib.write_text("const std = @import(\"std\");\npub fn mainLoop() void {}\n")
    out = tools.write_file(str(lib), lib.read_text())
    assert "no member named 'main'" not in out and "member named main" not in out


# --- write-path guard ------------------------------------------------------

def test_guard_blocks_git_hooks(tmp_path):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    out = tools.write_file(str(hooks / "pre-commit"), "#!/bin/sh\n")
    assert out.startswith("Error:") and "hook" in out


def test_guard_blocks_local_bin(monkeypatch):
    target = os.path.expanduser("~/.local/bin/definitely-a-test-shim")
    out = tools.write_file(target, "#!/bin/sh\n")
    assert out.startswith("Error:") and "persistence" in out
    assert not os.path.exists(target)


# --- env scrub -------------------------------------------------------------

def test_scrub_drops_openai_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("HARMLESS_VAR", "keep-me")
    env = tools._scrubbed_env()
    assert "OPENAI_API_KEY" not in env and env.get("HARMLESS_VAR") == "keep-me"


def test_scrub_drops_stack_token(monkeypatch):
    monkeypatch.setenv("OPENBEAST_SOME_TOKEN", "t")
    assert "OPENBEAST_SOME_TOKEN" not in tools._scrubbed_env()
