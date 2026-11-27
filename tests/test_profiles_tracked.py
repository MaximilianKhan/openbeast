"""Every serve profile the code relies on is committed, and no ignore rule can drop one.

PR #314 shipped scripts/engines/basalt/profiles/inkling2-mini-stock.env (the
RTX 5090 default) in its tests and docs, but .gitignore's "*.env" kept the
file itself out of the commit: the author's checkout passed, a clean main did
not. These checks fail in the author's checkout instead:

- each adapter's default profile (_ENG_<X>_PROFILE_DEFAULT) is in `git ls-files`;
- each profile path named in a tracked file is in `git ls-files`;
- no profile on disk is ignored, and a new file in any profile directory
  would not be (so a future ignore rule cannot silently drop one);
- the real secrets stay ignored.

Run: python3 -m pytest tests/test_profiles_tracked.py -q
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENGINES = ROOT / "scripts" / "engines"

# Directories whose *.env files are committed data (see .gitignore's negations).
PROFILE_GLOBS = ("scripts/engines/*/profiles/*.env", "scripts/backends/models/*.env", "recipes/*/recipe.env")
REF = re.compile(r"(scripts/engines/[A-Za-z0-9_-]+/profiles/[A-Za-z0-9][A-Za-z0-9._-]*\.env"
                 r"|scripts/backends/models/[A-Za-z0-9][A-Za-z0-9._-]*\.env"
                 r"|recipes/[A-Za-z0-9][A-Za-z0-9._-]*/recipe\.env)\b")

# Not "the code relies on it": tests name throwaway tmp profiles, reviews and
# reports are dated records (they may name a profile that was proposed, never
# shipped), evals/refs are eval fixtures. Upper-case names are doc placeholders.
SKIP = ("tests/", "docs/reviews/", "docs/reports/", "evals/refs/")
PLACEHOLDER = re.compile(r"/(?:[A-Z_]+)\.env$")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


@pytest.fixture(scope="module")
def tracked() -> set[str]:
    r = _git("ls-files", "-z")
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    return set(filter(None, r.stdout.split("\0")))


def _ignored(paths: list[str]) -> list[str]:
    """The subset of paths .gitignore excludes (git check-ignore --no-index: tracked or not)."""
    if not paths:
        return []
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--no-index", "--stdin"],
                       input="\n".join(paths) + "\n", capture_output=True, text=True)
    assert r.returncode in (0, 1), r.stderr
    return r.stdout.split()


def _adapter_defaults() -> list[str]:
    out = []
    for sh in sorted(ENGINES.glob("*.sh")):
        engine = sh.stem
        m = re.search(rf"^_ENG_{engine.upper()}_PROFILE_DEFAULT=([A-Za-z0-9._-]+)\s*$", sh.read_text(), re.M)
        if m:
            out.append(f"scripts/engines/{engine}/profiles/{m.group(1)}.env")
    return out


def test_basalt_default_is_found():
    # The scan above must see the case that motivated it.
    assert "scripts/engines/basalt/profiles/inkling2-mini-sc117.env" in _adapter_defaults()


def test_every_adapter_default_profile_is_tracked(tracked):
    missing = [p for p in _adapter_defaults() if p not in tracked]
    assert not missing, f"default profiles not committed (check .gitignore, then git add): {missing}"


def test_every_referenced_profile_is_tracked(tracked):
    refs: dict[str, str] = {}
    for f in sorted(tracked):
        if not f.endswith((".sh", ".py", ".md", ".json", ".toml", ".example", ".env", ".txt")) or f.startswith(SKIP):
            continue
        try:
            text = (ROOT / f).read_text(errors="replace")
        except (FileNotFoundError, IsADirectoryError):
            continue
        for m in REF.finditer(text):
            if not PLACEHOLDER.search(m.group(1)):
                refs.setdefault(m.group(1), f)
    assert "scripts/engines/basalt/profiles/inkling2-mini-stock.env" in refs     # the rollback, still named
    assert "scripts/engines/basalt/profiles/inkling2-mini-sc117.env" in refs     # the default since 2026-11-25
    missing = {p: by for p, by in refs.items() if p not in tracked}
    assert not missing, f"profiles named in tracked files but not committed (path: named in): {missing}"


def test_no_profile_on_disk_is_ignored():
    on_disk = sorted(str(p.relative_to(ROOT)) for g in PROFILE_GLOBS for p in ROOT.glob(g))
    assert on_disk
    assert not _ignored(on_disk), "ignored by .gitignore, so `git add -A` leaves them out"


def test_a_new_profile_would_not_be_ignored():
    engines = sorted(p.stem for p in ENGINES.glob("*.sh")) + ["some-new-engine"]
    probes = [f"scripts/engines/{e}/profiles/new-profile.env" for e in engines]
    probes += ["scripts/backends/models/new-model.env", "recipes/new-recipe/recipe.env"]
    assert not _ignored(probes)


@pytest.mark.parametrize("path", ["openbeast.conf", ".env", ".env.local", "scripts/.env", "scripts/secret.env",
                                  "scripts/engines/basalt/secret.env", ".run/dsv41-build.env"])
def test_secrets_stay_ignored(path):
    assert _ignored([path]) == [path]
