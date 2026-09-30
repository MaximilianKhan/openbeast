#!/usr/bin/env python3
"""CI workflow invariants that a YAML edit can silently break.

The full pytest step ignores every file a named pytest step already ran (so
~2 min of suites stop running twice). The two lists must agree: a file
ignored in the full run but named nowhere would never run in CI at all.

Run: python3 -m pytest tests/test_ci_workflow.py -q
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CI = REPO / ".github" / "workflows" / "ci.yml"


def _pytest_runs(text: str) -> list[str]:
    """Each `python -m pytest ...` command, a folded (>-) block joined."""
    runs, lines = [], text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("run: python -m pytest"):
            runs.append(s[len("run: "):])
        elif s == "run: >-":
            ind = len(ln) - len(ln.lstrip())
            block = []
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= ind:
                    break
                block.append(nxt.strip())
            cmd = " ".join(x for x in block if x)
            if cmd.startswith("python -m pytest"):
                runs.append(cmd)
    return runs


def split(text: str) -> tuple[set[str], set[str], list[str]]:
    """(files the named steps run, --ignore paths, --ignore-glob patterns)."""
    named, ignored, globs, full = set(), set(), [], 0
    for cmd in _pytest_runs(text):
        args = cmd.replace("'", "").split()[3:]
        if "tests/" in args:
            full += 1
            ignored |= {a.split("=", 1)[1] for a in args if a.startswith("--ignore=")}
            globs += [a.split("=", 1)[1] for a in args if a.startswith("--ignore-glob=")]
        else:
            for a in args:
                if a.startswith("tests/"):
                    hits = sorted(str(p.relative_to(REPO)) for p in REPO.glob(a))
                    assert hits, f"a named CI pytest step runs {a}, which matches no file"
                    named |= set(hits)
    assert full == 1, f"expected one full `pytest tests/` run, found {full}"
    return named, ignored, globs


def _ignored(path: str, ignored: set[str], globs: list[str]) -> bool:
    return path in ignored or any(fnmatch.fnmatch(path, g) for g in globs)


def test_the_full_run_skips_exactly_what_the_named_steps_ran():
    named, ignored, globs = split(CI.read_text())
    assert named, "no named pytest step found (parser drift?)"
    twice = sorted(p for p in named if not _ignored(p, ignored, globs))
    assert not twice, f"named AND in the full run (runs twice): {twice}"
    every = [str(p.relative_to(REPO)) for p in (REPO / "tests").glob("test_*.py")]
    never = sorted(p for p in every if _ignored(p, ignored, globs) and p not in named)
    assert not never, f"ignored by the full run and named nowhere (never runs in CI): {never}"
    assert all((REPO / p).exists() for p in ignored), "an --ignore names a file that is gone"


def test_the_check_catches_an_ignore_with_no_named_step():
    """Negative control on a built workflow: ignoring a suite nobody names."""
    fake = ("      - name: a\n        run: python -m pytest tests/test_tools.py -q\n"
            "      - name: full\n        run: >-\n          python -m pytest tests/ -q\n"
            "          --ignore=tests/test_tools.py --ignore=tests/test_edge.py\n")
    named, ignored, globs = split(fake)
    assert named == {"tests/test_tools.py"}
    assert ignored == {"tests/test_tools.py", "tests/test_edge.py"}
    never = [p for p in ignored if p not in named]
    assert never == ["tests/test_edge.py"]


def test_the_wiring_baseline_proof_is_pinned_and_required():
    """The byte-identity proof went dead once the wiring reached main (every
    PR base carried it, so no baseline ref was set). It is pinned to a
    pre-wiring release and must not skip."""
    text = CI.read_text()
    step = text[text.index("name: beast-hydra / beast-instinct stack wiring tests"):]
    step = step[:step.index("      - name:", 10)]
    assert re.search(r"^\s*ref=v\d+\.\d+\.\d+\s*$", step, re.M), "no pinned baseline ref"
    assert "WIRING_BASELINE_REQUIRED=1" in step
    assert "refs/tags/$ref" in step, "a depth-1 checkout has no tags: fetch the pinned one"


def test_the_i8_guard_gets_a_base():
    text = CI.read_text()
    assert "OPENBEAST_I8_BASE=$BASE_SHA" in text and "OPENBEAST_I8_BASE=none" in text
    assert text.index("OPENBEAST_I8_BASE=") < text.index("name: beast-hydra / beast-instinct tests")


def _wiring_step(text: str) -> str:
    step = text[text.index("name: beast-hydra / beast-instinct stack wiring tests"):]
    return step[:step.index("      - name:", 10)]


def _i8_base_depth(guard: str) -> int:
    """Commits in BASE..HEAD after the I8 deepen, then the wiring step's base
    fetch line `guard`, run in a depth-1 clone of a built repo."""
    import subprocess
    import tempfile

    def git(cwd, *a):
        return subprocess.run(["git", "-c", "protocol.file.allow=always", *a], cwd=cwd,
                              check=True, capture_output=True, text=True,
                              env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                                   "PATH": "/usr/bin:/bin", "HOME": tmp}).stdout.strip()

    with tempfile.TemporaryDirectory() as tmp:
        up = Path(tmp) / "up"
        up.mkdir()
        git(up, "init", "-q", "-b", "main")
        # A PR forked from an older main, while main moved on to `base`;
        # CI's HEAD is the refs/pull merge commit of the two.
        for i in range(3):
            git(up, "commit", "-q", "--allow-empty", "-m", f"old{i}")
        git(up, "checkout", "-q", "-b", "pr")
        for i in range(2):                        # the PR's own commits
            git(up, "commit", "-q", "--allow-empty", "-m", f"pr{i}")
        git(up, "checkout", "-q", "main")
        for i in range(3):                        # main after the fork
            git(up, "commit", "-q", "--allow-empty", "-m", f"main{i}")
        base = git(up, "rev-parse", "HEAD")
        git(up, "merge", "-q", "--no-ff", "--no-edit", "pr")
        head = git(up, "rev-parse", "HEAD")
        wt = Path(tmp) / "wt"
        git(tmp, "clone", "-q", "--depth=1", f"file://{up}", str(wt))
        git(wt, "fetch", "-q", "--no-tags", "--depth=200", "origin", base, head)
        subprocess.run(["bash", "-c", guard], cwd=wt, check=True, capture_output=True,
                       env={"BASE_SHA": base, "PATH": "/usr/bin:/bin", "HOME": tmp,
                            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow",
                            "GIT_CONFIG_VALUE_0": "always"})
        return len(git(wt, "log", "--format=%H", f"{base}..{head}").split())


def test_the_wiring_step_does_not_reshallow_the_i8_base():
    """The wiring step ran `git fetch --depth=1 origin $BASE_SHA` after I8
    deepened it, which made the base a shallow root again: I8's BASE..HEAD
    then held main's own history too, and could fail a PR over an old
    instinct commit on main."""
    line = next(ln.strip() for ln in _wiring_step(CI.read_text()).splitlines()
                if '"$BASE_SHA"' in ln and "fetch" in ln)
    assert _i8_base_depth(line) == 3, line  # pr0, pr1, the merge
    # negative control: the old unguarded fetch widens the range
    assert _i8_base_depth('git fetch --no-tags --depth=1 origin "$BASE_SHA"') > 3
