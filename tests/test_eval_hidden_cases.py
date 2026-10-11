"""Variant units must not hand the agent the answer (review 2026-10-09,
evals F4).

In suite v4, 132 variant units wrote expected.txt into the agent's working
directory at setup and validated with one diff against it; a program that
prints the file passed 21 of the 22 Python variants. From v4.1
(evals/scripts/hide_expected.py) setup writes only the sample input, and
pre_validate — which the harness runs after the agent has exited — installs
the sample plus hidden cases and the matching expected output.

Every case runs the REAL spec with its /tmp/eval_* directory redirected
into tmp_path, so nothing here can touch a live eval's fixtures.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "tests"))

import run_eval  # noqa: E402
import audit_variants  # noqa: E402

TOOL = ROOT / "evals" / "scripts" / "hide_expected.py"
MARK = "OB_HIDDEN_EOF"


def _units():
    return {t["id"]: t for t in run_eval.load_tasks()}


UNITS = _units()
HIDDEN = sorted(u for u, t in UNITS.items() if MARK in (t.get("pre_validate") or ""))
PYTHON = [u for u in HIDDEN if UNITS[u]["language"] == "python"]


def _box(text: str, root: Path) -> str:
    return text.replace("/tmp/eval", f"{root}/eval")


def _sh(script: str, root: Path, timeout=60):
    return subprocess.run(["bash", "-c", _box(script, root)], capture_output=True,
                          text=True, timeout=timeout)


def _fixture_dir(unit, root: Path) -> Path:
    m = re.search(r"mkdir -p (/tmp/eval_\w+)", unit["setup"])
    return Path(_box(m.group(1), root))


def test_the_132_units_the_review_counted_are_all_covered():
    assert len(HIDDEN) == 132
    assert len({UNITS[u]["base_id"] for u in HIDDEN}) == 22
    assert len(PYTHON) == 22
    # And nothing else in the suite writes an expected.txt at setup.
    leaks = [u for u, t in UNITS.items()
             if re.search(r"/tmp/eval_\w+/expected\.txt", t.get("setup") or "")]
    assert leaks == []


@pytest.mark.parametrize("uid", HIDDEN)
def test_setup_shows_the_sample_only_and_validation_sees_more(uid, tmp_path):
    unit = UNITS[uid]
    d = _fixture_dir(unit, tmp_path)
    assert _sh(unit["setup"], tmp_path).returncode == 0
    assert not (d / "expected.txt").exists(), "the answer is in the agent's directory"
    sample = (d / "input.txt").read_text()
    assert sample.strip()

    _sh(unit["pre_validate"], tmp_path)
    combined = (d / "input.txt").read_text()
    expected = (d / "expected.txt").read_text()
    assert len(combined) > len(sample), "no hidden case was added"
    # The sample's cases are all still there, in order, ahead of the hidden
    # ones (a first-line case count, where there is one, has grown).
    headed = combined.split("\n", 1)[0] != sample.split("\n", 1)[0]
    assert (sample.split("\n", 1)[1] if headed else sample) in combined
    assert expected.strip()
    # The task text tells the model what the validator does.
    assert "additional hidden cases" in unit["task"]


def _validate(unit, root):
    _sh(unit["pre_validate"], root)
    return _sh(unit["validation"]["script"], root, timeout=120)


def _reference(uid) -> Path:
    stem, _ = audit_variants.TARGETS[UNITS[uid]["base_id"]]
    return audit_variants.REFS / f"{stem}.py"


@pytest.mark.parametrize("uid", PYTHON)
def test_literal_print_is_rejected_and_the_reference_passes(uid, tmp_path):
    unit = UNITS[uid]
    d = _fixture_dir(unit, tmp_path)
    ref = _reference(uid)
    target = d / ref.name

    # The cheat, at its strongest: the correct output for the input the agent
    # can see, printed verbatim (the review's cheat printed expected.txt,
    # which no longer exists to copy).
    assert _sh(unit["setup"], tmp_path).returncode == 0
    answer = subprocess.run([sys.executable, str(ref)], stdin=open(d / "input.txt"),
                            capture_output=True, text=True, timeout=60).stdout
    assert answer.strip()
    target.write_text(f"import sys\nsys.stdout.write({answer!r})\n")
    cheat = _validate(unit, tmp_path)
    assert cheat.returncode != 0, f"{uid}: a literal print passed"

    # Control: the reference solution, same fixture, passes.
    _sh(unit["cleanup"], tmp_path)
    assert _sh(unit["setup"], tmp_path).returncode == 0
    target.write_text(ref.read_text())
    good = _validate(unit, tmp_path)
    assert good.returncode == 0, f"{uid}: reference failed: {(good.stdout + good.stderr)[-300:]}"


def test_specs_are_what_the_generator_writes():
    """The 132 specs are generated, not hand-edited: the tool must be a
    no-op on the committed tasks (it recomputes every expected output from
    the Python references)."""
    r = subprocess.run([sys.executable, str(TOOL), "--check"], capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would change 0 of 22" in r.stdout


def test_a_change_to_the_hidden_cases_is_a_cache_miss():
    """pre_validate is part of the task hash (no key starts with `_`), so a
    change to the hidden cases is a cache miss for that unit."""
    import cache
    unit = dict(UNITS["11_bst_a"])
    before = cache.task_hash(unit)
    unit["pre_validate"] = unit["pre_validate"].replace("50 30 70", "50 30 71")
    assert cache.task_hash(unit) != before
