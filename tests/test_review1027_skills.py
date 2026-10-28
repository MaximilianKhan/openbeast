"""Review 2026-10-27, skills tooling (R9-09, R9-10).

R9-09: generate-skill-index.py treated every argument except --check as "rewrite
system-prompt-tools.md in place" — `--help` or a typo like `--chek` rewrote an
era-hashed file. Each case runs a COPY of the script in a temp repo layout
(the script finds its repo from __file__), so the real prompt is never touched.

R9-10: the SKILL.md validator in tests/test_scripts.sh accepted
`description: >` (a YAML block scalar), which the line parsers read as the
bare `>`. The validator's own Python is extracted from test_scripts.sh and run
against a fixture skills/ tree, so this tests the shipped check, not a copy.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "scripts" / "generate-skill-index.py"
START = ("<!-- SKILL_INDEX_START (generated — edit skills/*/SKILL.md, then run "
         "scripts/generate-skill-index.py) -->")
END = "<!-- SKILL_INDEX_END -->"
BODY = "\n# body\n\n" + "Enough body text to pass the length check in the validator. " * 2


def _skill(skills: Path, name: str, desc_block: str, extra: str = "") -> None:
    d = skills / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n{desc_block}\n{extra}---\n{BODY}")


def _gen_repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(GEN, tmp_path / "scripts" / "generate-skill-index.py")
    skills = tmp_path / "skills"
    _skill(skills, "shown", "description: the shown skill, on the menu")
    _skill(skills, "hidden", "description: the hidden skill, off the menu",
           "prompt_index: false\n")
    # STALE on purpose: the block is empty, so a rewrite would change the file.
    (tmp_path / "system-prompt-tools.md").write_text(f"head\n{START}\n{END}\ntail\n")
    return tmp_path


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(repo / "scripts" / "generate-skill-index.py"), *args],
                          capture_output=True, text=True, timeout=60)


def test_help_prints_usage_and_never_writes(tmp_path):
    repo = _gen_repo(tmp_path)
    before = (repo / "system-prompt-tools.md").read_text()
    r = _run(repo, "--help")
    assert r.returncode == 0
    assert "--check" in r.stdout
    assert (repo / "system-prompt-tools.md").read_text() == before


def test_unknown_argument_exits_2_and_never_writes(tmp_path):
    repo = _gen_repo(tmp_path)
    before = (repo / "system-prompt-tools.md").read_text()
    r = _run(repo, "--chek")
    assert r.returncode == 2
    assert (repo / "system-prompt-tools.md").read_text() == before


def test_check_on_stale_exits_1_and_never_writes(tmp_path):
    repo = _gen_repo(tmp_path)
    before = (repo / "system-prompt-tools.md").read_text()
    assert _run(repo, "--check").returncode == 1
    assert (repo / "system-prompt-tools.md").read_text() == before


def test_bare_run_rewrites_and_counts_only_menu_rows(tmp_path):
    """Negative control for the two above: no argument still rewrites, and the
    message counts the rows written (1), not every SKILL.md (2)."""
    repo = _gen_repo(tmp_path)
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    text = (repo / "system-prompt-tools.md").read_text()
    assert "`shown`" in text and "hidden" not in text
    assert "(1 skills on the menu)" in r.stdout
    assert _run(repo, "--check").returncode == 0


# --- R9-10: the SKILL.md validator in tests/test_scripts.sh -----------------

def _validator_src() -> str:
    text = (ROOT / "tests" / "test_scripts.sh").read_text()
    m = re.search(r"SKILL_CHECK=\$\(python3 - <<'PY'\n(.*?)\nPY\n", text, re.S)
    assert m, "the SKILL.md validator block moved — update this test"
    return m.group(1)


def _validate(repo: Path) -> list[str]:
    r = subprocess.run([sys.executable, "-c", _validator_src()], capture_output=True,
                       text=True, env={**os.environ, "REPO_DIR": str(repo)}, timeout=60)
    assert r.returncode == 0, r.stderr
    return [ln[len("ERROR:"):] for ln in r.stdout.splitlines() if ln.startswith("ERROR:")]


def test_validator_rejects_a_block_scalar_description(tmp_path):
    _skill(tmp_path / "skills", "folded",
           "description: >\n  Use when: building things that need a long description")
    errs = _validate(tmp_path)
    assert any("folded" in e and "block scalar" in e for e in errs), errs


def test_validator_rejects_a_too_short_description(tmp_path):
    _skill(tmp_path / "skills", "terse", "description: tiny")
    errs = _validate(tmp_path)
    assert any("terse" in e and "too short" in e for e in errs), errs


def test_validator_accepts_a_one_line_description(tmp_path):
    _skill(tmp_path / "skills", "good", "description: a real one-line description of when to use it")
    assert _validate(tmp_path) == []


def test_validator_passes_every_shipped_skill():
    assert _validate(ROOT) == []
