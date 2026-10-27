#!/usr/bin/env python3
"""Review 2026-10-27 regressions for beast-lang (R3-F3, R3-F5, R3-F1/R7-01).

Run: python3 -m pytest tests/test_review1027_lang.py -q -rs
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agents"))

from lang import synthesize as S  # noqa: E402


# ── R3-F3: a worktree's synthesis must consult the MAIN tree's GPU lease ──

_STUB = ('#!/bin/bash\n'
         'RD="${OPENBEAST_RUN_DIR:-$(cd "$(dirname "$0")/.." && pwd)/.run}"\n'
         'if [ -f "$RD/gpu.lease" ]; then echo "HELD by pid 1 — the campaign"; else echo FREE; fi\n')


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


@pytest.fixture
def trees(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    monkeypatch.delenv("OPENBEAST_RUN_DIR", raising=False)
    main = tmp_path / "main"
    (main / "scripts").mkdir(parents=True)
    (main / "scripts" / "gpu-lease.sh").write_text(_STUB)
    _git("init", "-q", cwd=main)
    _git("add", "scripts/gpu-lease.sh", cwd=main)
    _git("commit", "-qm", "init", cwd=main)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), cwd=main)
    return main, wt


def test_worktree_reads_the_main_trees_lease(trees, monkeypatch):
    main, wt = trees
    monkeypatch.setattr(S, "_REPO", str(wt))
    assert S.lease_status() == "FREE"                 # negative control: nothing held
    (main / ".run").mkdir()
    (main / ".run" / "gpu.lease").write_text("1\n")
    assert S.lease_status().startswith("HELD"), "a worktree was told FREE while main holds the card"
    assert S.check_lease()[0] is False


def test_main_tree_still_reads_its_own_lease(trees, monkeypatch):
    main, _ = trees
    monkeypatch.setattr(S, "_REPO", str(main))
    (main / ".run").mkdir()
    (main / ".run" / "gpu.lease").write_text("1\n")
    assert S.lease_status().startswith("HELD")


def test_explicit_run_dir_wins(trees, monkeypatch, tmp_path):
    main, wt = trees
    monkeypatch.setattr(S, "_REPO", str(wt))
    (main / ".run").mkdir()
    (main / ".run" / "gpu.lease").write_text("1\n")
    other = tmp_path / "other-run"
    other.mkdir()
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(other))
    assert S.lease_status() == "FREE"


def test_unresolvable_worktree_is_unknown_not_free(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENBEAST_RUN_DIR", raising=False)
    wt = tmp_path / "broken"
    (wt / "scripts").mkdir(parents=True)
    (wt / "scripts" / "gpu-lease.sh").write_text(_STUB)
    (wt / ".git").write_text("gitdir: /nonexistent/worktrees/x\n")   # a dangling worktree link
    monkeypatch.setattr(S, "_REPO", str(wt))
    assert S.lease_status() == ""
    assert S.check_lease()[0] is False


# ── R3-F5: zig C translation (@cImport/@cDefine/@cUndef) is refused ──

def test_zig_cdefine_newline_include_is_refused():
    from lang import drivers as D
    z = D.driver_for("zig")
    for src in ('const c = @cImport({ @cDefine("A", "1\\n#include \\"/etc/hostname\\"\\n"); });\n_ = c;',
                'const c = @cImport({ @cUndef("A"); });\n_ = c;',
                'const c = @cImport(@cInclude("stdio.h"));\n_ = c;'):
        why = z.refusal(z.wrap(src))
        assert why and "refused" in why, (src, why)
    # negative controls: the words in a comment or a string are not a builtin
    for src in ('// @cImport is gone in 0.17\nconst x = 1;\n_ = x;',
                'const s = "@cDefine";\n_ = s;',
                'const b = @import("builtin");\n_ = b;'):
        assert z.refusal(z.wrap(src)) is None, src
