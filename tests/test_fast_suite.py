"""Pinned fast-suite (evals/suites/v4-fast.json) integrity + imputation
tests, and the coverage floor every suite (pin or v5 manifest) must meet.

The pin was named v5-fast until the v5 era break; v5 is now a real suite
(evals/suites/v5.json, never imputed) and the pin is a subset of the v4
universe (task files 01..162) only.

The fast suite's fidelity contract: a v4-fast run scored as
(measured units + imputed complement) equals the full-v4 capability
EXACTLY for any model whose outcomes match the pin's assumptions.
These tests pin the machinery that keeps that contract honest:

  1. The pin partitions the CURRENT effective unit set exactly
     (units + assumed_passed + assumed_failed == the v4 universe, no overlap).
  2. Declared counts match the lists (drift between metadata and content
     would silently mis-describe the suite).
  3. Tripwires are a subset of the measured units.
  4. impute_suite_tasks reconstructs the full-suite capability exactly
     when reality matches the assumptions, and real measurements always
     beat assumptions when both exist.
  5. load_suite hard-fails on pin/tasks drift instead of mis-scoring.
  6. --suite and --tasks are mutually exclusive.

No evals/results/ files are needed — everything checks against the pin
and evals/tasks/ specs, so this runs in CI.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import run_eval  # noqa: E402
import scoring  # noqa: E402

PIN_NAME = "v4-fast"
PIN_PATH = ROOT / "evals" / "suites" / f"{PIN_NAME}.json"


@pytest.fixture(scope="module")
def pin() -> dict:
    with open(PIN_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def all_units() -> list[dict]:
    """The pin's universe: the v4 task files. New v5 task files (163+) are
    outside it by construction."""
    with open(PIN_PATH) as f:
        pin = json.load(f)
    tasks = run_eval.load_tasks(None)
    universe = run_eval.suite_universe(pin, tasks)
    return [t for t in tasks if t["id"] in universe]


def test_pin_partitions_unit_set_exactly(pin, all_units):
    units = set(pin["units"])
    ap = set(pin["assumed_passed"])
    af = set(pin["assumed_failed"])
    actual = {t["id"] for t in all_units}
    assert units | ap | af == actual, "pin does not cover the current unit set"
    assert not units & ap and not units & af and not ap & af, "pin sets overlap"
    assert len(pin["units"]) == len(units), "duplicate ids in units"


def test_declared_counts_match_content(pin):
    c = pin["counts"]
    assert c["units"] == len(pin["units"])
    assert c["tripwires"] == len(pin["tripwires"])
    assert c["assumed_passed"] == len(pin["assumed_passed"])
    assert c["assumed_failed"] == len(pin["assumed_failed"])
    assert c["discriminating"] == c["units"] - c["tripwires"]


def test_tripwires_are_measured_units(pin):
    assert set(pin["tripwires"]) <= set(pin["units"])


def test_imputation_reconstructs_full_capability_exactly(pin, all_units):
    """Simulate a model matching the pin's assumptions: passes everything
    except 30 arbitrary measured units. Full-list capability must equal
    imputed capability from the measured subset alone — exactly."""
    fail = set(sorted(pin["units"])[::4][:30])
    full = [{"id": t["id"], "base_id": t.get("base_id"),
             "variant_count": t.get("variant_count", 1),
             "difficulty": t.get("difficulty", "medium"),
             "passed": t["id"] not in fail and t["id"] not in set(pin["assumed_failed"])}
            for t in all_units]
    measured = [t for t in full if t["id"] in set(pin["units"])]
    _s, _l, cap_full = scoring.compute_solve_breadth(full)
    _s, _l, cap_imputed = scoring.compute_solve_breadth(
        scoring.impute_suite_tasks(measured, pin))
    assert abs(cap_full - cap_imputed) < 1e-9


def test_measured_rows_beat_assumptions(pin):
    """A measured row for a unit in assumed_passed must not be duplicated
    or overridden by the assumption."""
    uid = pin["assumed_passed"][0]
    measured = [{"id": uid, "base_id": None, "variant_count": 1,
                 "difficulty": "easy", "passed": False}]
    out = scoring.impute_suite_tasks(measured, pin)
    rows = [t for t in out if t["id"] == uid]
    assert len(rows) == 1 and rows[0]["passed"] is False


def test_impute_unknown_unit_raises(pin):
    bad = dict(pin, assumed_passed=pin["assumed_passed"] + ["999_not_a_task"])
    with pytest.raises(ValueError, match="drift"):
        scoring.impute_suite_tasks([], bad)


def test_load_suite_detects_drift(tmp_path, monkeypatch, pin):
    incomplete = dict(pin, units=pin["units"][:-1])  # drop one unit
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "broken.json").write_text(json.dumps(incomplete))
    monkeypatch.setattr(run_eval, "SUITES_DIR", str(suites))
    with pytest.raises(SystemExit, match="drifted"):
        run_eval.load_suite("broken")


def test_load_suite_unknown_name(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "SUITES_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="unknown suite"):
        run_eval.load_suite("nope")


def test_suite_and_tasks_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_eval.run_eval(task_filter=["01_create_file"], suite=PIN_NAME,
                          cache_only=True, model_name="x")


def test_pinned_suite_loads_cleanly(pin):
    loaded = run_eval.load_suite(PIN_NAME)
    assert loaded["counts"]["units"] == len(pin["units"])


def test_old_pin_name_still_loads(pin, capsys):
    loaded = run_eval.load_suite("v5-fast")
    assert loaded["counts"]["units"] == len(pin["units"])
    assert "renamed" in capsys.readouterr().out


def test_v4_universe_ignores_new_task_files():
    """A v5 task file (id >= 163) is not drift of the frozen v4 pin."""
    fake = [{"id": "20_priority_queue_a", "base_id": "20_priority_queue"},
            {"id": "163_bigint_divmod_f", "base_id": "163_bigint_divmod"},
            {"id": "173_asyncio"}]
    assert run_eval.suite_universe({"base_suite_version": "v4"}, fake) == {"20_priority_queue_a"}
    assert len(run_eval.suite_universe({}, fake)) == 3


# --- coverage floor (brief §6.2): no pin may leave a cell empty ---------------

def test_pin_coverage_floor_refuses_an_empty_cell(pin, all_units, tmp_path, monkeypatch):
    import make_fast_suite as mfs
    # The shipped v4 pin measures nothing in Signal Processing (and only
    # tripwires in SWE): exactly the gap the floor exists to refuse.
    gaps = mfs.pin_coverage_gaps(pin, {"cell_min": 1}, all_units)
    assert any(g.startswith("Signal Processing & DSP x python") for g in gaps)
    # A pin that declares the floor and leaves a cell empty does not load.
    floored = dict(pin, coverage_floor={"cell_min": 1})
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "floored.json").write_text(json.dumps(floored))
    monkeypatch.setattr(run_eval, "SUITES_DIR", str(suites))
    with pytest.raises(SystemExit, match="coverage"):
        run_eval.load_suite("floored")


def test_pin_coverage_floor_passes_when_every_cell_is_measured(all_units):
    import make_fast_suite as mfs
    every = {"units": [t["id"] for t in all_units], "assumed_passed": [], "assumed_failed": []}
    assert mfs.pin_coverage_gaps(every, {"cell_min": 1}, all_units) == []


def test_v5_manifest_meets_its_coverage_floor():
    import scoring
    import suite_manifest as sm
    man = sm.load_manifest("v5")                      # refuses on any gap
    assert len(sm.scored_units(man)) == scoring.SUITE_EXPECTED_UNITS["v5"]
    # v5.0 floor: every category and every language, not every cell (the
    # families that filled the compiled cells of five categories are the
    # v5.1 backlog; evals/README.md).
    floor = man["coverage_floor"]
    assert floor["categories"] == 12 and floor["languages"] == 6
    assert floor["category_min"] >= 1 and floor["language_min"] >= 1
    cells = sm.coverage_cells(man["units"])
    cats = {c for c, _ in cells}
    assert len(cats) == 12
    for lang in sm.SIX_LANGS:
        assert sum(n for (c, l), n in cells.items() if l == lang) >= floor["language_min"], lang
    for cat in cats:
        assert sum(n for (c, l), n in cells.items() if c == cat) >= floor["category_min"], cat


def test_manifest_with_an_empty_cell_is_refused(tmp_path, monkeypatch):
    import suite_manifest as sm
    with open(sm.manifest_path("v5")) as f:
        man = json.load(f)
    # Drop every zig unit: the language floor is broken.
    no_zig = dict(man, units=[u for u in man["units"] if u["lang"] != "zig"])
    assert any(e.startswith("coverage floor: zig: 0 scored unit(s)")
               for e in sm.validate_manifest(no_zig)), sm.validate_manifest(no_zig)
    # A full-grid floor still refuses any empty (category, language) cell.
    grid = dict(man, coverage_floor={"cell_min": 1, "categories": 12, "languages": 6})
    dsp = [u for u in grid["units"]
           if not (u["category"] == "Signal Processing & DSP" and u["lang"] == "zig")]
    assert any("Signal Processing & DSP x zig" in e
               for e in sm.validate_manifest(dict(grid, units=dsp)))
    monkeypatch.setattr(sm, "SUITES_DIR", str(tmp_path))
    (tmp_path / "broken.json").write_text(json.dumps(no_zig))
    with pytest.raises(SystemExit, match="coverage floor"):
        sm.load_manifest("broken")


def test_make_fast_suite_generate_refuses_a_gappy_pin(all_units, monkeypatch, capsys, tmp_path):
    import make_fast_suite as mfs
    refs, _ = _loo_refs(all_units)
    monkeypatch.setattr(mfs, "load_reference_runs", lambda exclude: refs)
    out = tmp_path / "pin.json"
    monkeypatch.setattr(mfs, "SUITE_PATH", str(out))      # never the real pin
    # Other test modules reload run_eval against a temp TASKS_DIR; the gap
    # check must see the real task files.
    monkeypatch.setitem(sys.modules, "run_eval", run_eval)
    monkeypatch.setattr(run_eval, "TASKS_DIR", str(ROOT / "evals" / "tasks"))
    before = PIN_PATH.read_bytes()
    monkeypatch.setattr(sys, "argv", ["make_fast_suite.py", "--generate"])
    assert mfs.main() == 1
    assert "REFUSED" in capsys.readouterr().out
    assert not out.exists() and PIN_PATH.read_bytes() == before


# --- --check-run pre-flight (LANG_AWARENESS_PLAN.md §7) ----------------------

def _fake_full_run(pin, tmp_path, flip=()):
    """A synthetic full-run results file matching the pin's assumptions,
    with `flip` unit outcomes inverted."""
    rows = []
    for uid in pin["units"] + pin["assumed_passed"]:
        rows.append({"id": uid, "passed": uid not in flip})
    for uid in pin["assumed_failed"]:
        rows.append({"id": uid, "passed": uid in flip})
    p = tmp_path / "eval-fake.json"
    p.write_text(json.dumps({"model_slug": "fake", "tasks": rows}))
    return str(p)


def _run_check(path):
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "evals" / "make_fast_suite.py"),
                        "--check-run", path], capture_output=True, text=True)
    return r.returncode, r.stdout


def test_check_run_clean_exits_zero(pin, tmp_path):
    rc, out = _run_check(_fake_full_run(pin, tmp_path))
    assert rc == 0 and "clean" in out


def test_check_run_flags_violated_assumed_passed(pin, tmp_path):
    bad = pin["assumed_passed"][0]
    rc, out = _run_check(_fake_full_run(pin, tmp_path, flip=(bad,)))
    assert rc == 1 and bad in out


def test_check_run_flags_violated_assumed_failed(pin, tmp_path):
    bad = pin["assumed_failed"][0]
    rc, out = _run_check(_fake_full_run(pin, tmp_path, flip=(bad,)))
    assert rc == 1 and bad in out


# --- leave-one-model-out (review 2026-10-09, evals F11) ----------------------
# verify()'s identity is true by construction: the assumed lists ARE the
# units every reference run agrees on. Only a model the pin was not built
# from can contradict them. Reference runs are built here from the task
# specs; evals/results is never read.

def _ref(all_units, fails=(), slow=()):
    rows = [{"id": t["id"], "base_id": t.get("base_id"),
             "variant_count": t.get("variant_count", 1),
             "difficulty": t.get("difficulty", "medium"),
             "language": t.get("language", "python"),
             "passed": t["id"] not in fails,
             "elapsed_seconds": 999.0 if t["id"] in slow else 1.0}
            for t in all_units]
    return ("synthetic.json", {"tasks": rows})


def _loo_refs(all_units):
    """Five models: two pairs that each share a failure (so leaving one out
    still shows that unit as discriminating), and `odd`, the only model to
    fail u3 — a unit too slow to be picked as a cheap tripwire."""
    singles = [t["id"] for t in all_units
               if "base_id" not in t and t.get("difficulty") == "medium"]
    u1, u2, u3 = singles[0], singles[1], singles[-1]
    refs = {"a1": _ref(all_units, {u1}, {u3}), "a2": _ref(all_units, {u1}, {u3}),
            "b1": _ref(all_units, {u2}, {u3}), "b2": _ref(all_units, {u2}, {u3}),
            "odd": _ref(all_units, {u3}, {u3})}
    return refs, u3


def test_leave_one_out_finds_what_the_identity_cannot(all_units, capsys):
    import make_fast_suite as mfs
    refs, u3 = _loo_refs(all_units)
    in_sample_pin = mfs.suite_from_refs(refs, set())
    before = PIN_PATH.read_bytes()
    assert mfs.verify(refs, in_sample_pin) is True     # the identity holds for all five
    out = capsys.readouterr().out
    assert "identity: HOLDS for all reference models" in out
    assert "Leave-one-model-out" in out and "agreement: 4/5 exact" in out
    assert "UNDETECTED" in out and "tripped NO tripwire" in out
    assert PIN_PATH.read_bytes() == before, "verify must never rewrite the pinned suite"

    rows = {r["slug"]: r for r in mfs.leave_one_out(refs)}
    odd = rows["odd"]
    assert odd["wrong_assumed"] == [u3] and odd["tripwires_failed"] == []
    assert odd["imputed"] > odd["full"]                # the imputed score overstates
    # Negative control: a model whose failures another reference model
    # shares is reproduced exactly out of sample.
    for slug in ("a1", "a2", "b1", "b2"):
        assert rows[slug]["wrong_assumed"] == [] and abs(rows[slug]["delta"]) < 1e-9


def test_leave_one_out_needs_three_reference_runs(all_units, capsys):
    import make_fast_suite as mfs
    refs, _ = _loo_refs(all_units)
    two = {k: refs[k] for k in ("a1", "b1")}
    assert mfs.leave_one_out(two) == []
    mfs.print_leave_one_out([])
    assert "skipped: needs >=3 reference runs" in capsys.readouterr().out


def test_generate_is_unchanged_by_the_refactor(all_units, monkeypatch):
    """generate() must build exactly the pin suite_from_refs builds from the
    same runs (the verify-mode drift check regenerates through it)."""
    import make_fast_suite as mfs
    refs, _ = _loo_refs(all_units)
    monkeypatch.setattr(mfs, "load_reference_runs", lambda exclude: refs)
    a, b = mfs.generate(set()), mfs.suite_from_refs(refs, set())
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b and a["counts"]["discriminating"] == 3


def test_check_run_rejects_partial_run(pin, tmp_path):
    p = tmp_path / "partial.json"
    p.write_text(json.dumps({"model_slug": "fake",
                             "tasks": [{"id": u, "passed": True} for u in pin["units"]]}))
    rc, out = _run_check(str(p))
    assert rc == 1 and "NOT A FULL RUN" in out
