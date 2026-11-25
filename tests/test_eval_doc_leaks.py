"""No sealed answer detail in the unsealed tree (review 1124).

The agent's eval sandbox seals a checkout's evals/, tests/ and agents/logs/,
but docs/, README.md and the rest stay readable while a unit is measured.
So two kinds of text must never appear outside the sealed trees:

  * the descriptive name of a negative-control reference
    (evals/refs/*/wrong_*.*, slow_*.*): the name states a failure mode
    (DECISIONS-2026-11-24 §14 once printed one);
  * the design a sealed review found decisive for a family (the 168
    investigation's design; V5_INKLING2 §12.3 once stated it).

Generic stems (leak, slow, validator, the unit's own module name) are not
hints and are not checked.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEALED = ("evals/", "tests/", "agents/logs/")
# Phrases that state the 168 family's decisive design (sealed review
# evals/audit/reviews/168-linearizability-deepseek-2026-11-19.md).
SEALED_PHRASES = [r"per-key (?:split|search|decomposition)", r"global (?:memoi[sz]ed )?search",
                  r"slow_global", r"decompose by key"]


def _unsealed_text_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        # no git (a worktree mounted into a container): walk the tree
        skip = (".git", "node_modules", ".claude", "__pycache__", ".venv")
        out = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
               if p.is_file() and not any(part in skip for part in p.relative_to(ROOT).parts)]
    for rel in out:
        if rel.startswith(SEALED):
            continue
        p = ROOT / rel
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2",
                                ".gz", ".zip", ".whl", ".bin"} or not p.is_file():
            continue
        try:
            yield rel, p.read_text(errors="ignore")
        except OSError:
            continue


def _control_stems():
    stems = set()
    for p in (ROOT / "evals" / "refs").rglob("*"):
        if p.is_file() and re.match(r"(?:wrong|slow)_\w+$", p.stem):
            stems.add(p.stem)
    return stems


def test_no_negative_control_name_outside_the_sealed_trees():
    stems = _control_stems()
    assert len(stems) > 10, "the refs tree moved: update this test"
    rx = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, stems))) + r")(?![\w-])")
    hits = [f"{rel}: {m.group(1)}" for rel, text in _unsealed_text_files()
            for m in rx.finditer(text)]
    assert not hits, "sealed reference names in unsealed files:\n" + "\n".join(hits[:20])


def test_no_sealed_168_design_outside_the_sealed_trees():
    rx = re.compile("|".join(SEALED_PHRASES), re.I)
    hits = [f"{rel}: {m.group(0)}" for rel, text in _unsealed_text_files()
            if rel.endswith(".md") for m in rx.finditer(text)]
    assert not hits, "a sealed design in unsealed docs:\n" + "\n".join(hits[:20])
