#!/usr/bin/env python3
"""
Unit tests for evals/scoring.py — suite-version tagging, null-GPU
robustness, and the (host_id, model_slug) dedup key (pins the CRIT-2 fix:
a cache-only result file may store an explicit null gpu).

Run: python -m pytest tests/test_scoring.py -v
  or: python3 tests/test_scoring.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))

from scoring import (_suite_version, score_run, entry_dedup_key, entry_host_id,
                     compute_solve_breadth, SOLVE_WEIGHT, LANG_WEIGHT,
                     SUITE_EXPECTED_UNITS, imputation_allowed)

V4_UNITS = SUITE_EXPECTED_UNITS["v4"]
V35_UNITS = SUITE_EXPECTED_UNITS["v3.5"]


class TestSuiteVersion(unittest.TestCase):
    def test_explicit_field_wins(self):
        self.assertEqual(
            _suite_version({"suite_version": "v9", "summary": {"total": V4_UNITS}}),
            "v9")

    def test_v4_unit_count_is_v4(self):
        self.assertEqual(_suite_version({"summary": {"total": V4_UNITS}}), "v4")

    def test_v3_5_unit_count_is_v3_5(self):
        self.assertEqual(_suite_version({"summary": {"total": V35_UNITS}}), "v3.5")

    def test_v5_expects_the_manifests_scored_units(self):
        # Read from evals/suites/v5.json, never a literal: the v5.0 set is
        # the 15 accepted families plus the retained v4 definitions.
        import json as _json
        with open(os.path.join(os.path.dirname(__file__), "..", "evals", "suites",
                               "v5.json")) as f:
            units = _json.load(f)["units"]
        n = sum(u.get("role", "scored") == "scored" for u in units)
        self.assertEqual(SUITE_EXPECTED_UNITS["v5"], n)

    def test_v5_is_never_inferred_from_a_count(self):
        # v5 rows always record suite_version; a bare v5-sized count is not v5.
        n = SUITE_EXPECTED_UNITS["v5"]
        self.assertEqual(_suite_version({"summary": {"total": n}}), "legacy")

    def test_other_count_is_legacy(self):
        self.assertEqual(_suite_version({"summary": {"total": 144}}), "legacy")

    def test_missing_total_is_unknown(self):
        self.assertEqual(_suite_version({}), "unknown")
        self.assertEqual(_suite_version({"summary": {}}), "unknown")


class TestScoreRun(unittest.TestCase):
    def test_suite_version_passed_through(self):
        entry = score_run({"suite_version": "v4", "tasks": []})
        self.assertEqual(entry["suite_version"], "v4")

    def test_gpu_none_does_not_crash(self):
        # Cache-only result files store an explicit null for gpu/engine/runtime.
        entry = score_run({
            "model": "m", "model_slug": "m-slug",
            "gpu": None, "inference_engine": None, "runtime": None,
            "tasks": [{"id": "t1", "difficulty": "easy", "passed": True,
                       "elapsed_seconds": 1.0}],
        })
        self.assertEqual(entry["gpu"], {})
        self.assertEqual(entry["inference_engine"], {})
        self.assertEqual(entry["runtime"], {})
        self.assertEqual(entry["tasks_passed"], 1)


class TestDedupKey(unittest.TestCase):
    def test_host_id_plus_slug(self):
        entry = {"gpu": {"host_id": "beast-1"}, "model_slug": "qwen-27b"}
        self.assertEqual(entry_dedup_key(entry), ("beast-1", "qwen-27b"))

    def test_legacy_gpu_name_fallback(self):
        entry = {"gpu": {"name": "RTX 5090"}, "model_slug": "qwen-27b"}
        self.assertEqual(entry_dedup_key(entry), ("RTX 5090", "qwen-27b"))

    def test_null_gpu_does_not_crash(self):
        # The CRIT-2 fix: an explicit null gpu must resolve, not AttributeError.
        entry = {"gpu": None, "model_slug": "qwen-27b"}
        self.assertEqual(entry_dedup_key(entry), ("unknown-host", "qwen-27b"))
        self.assertEqual(entry_host_id({}), "unknown-host")

    def test_same_model_different_hosts_coexist(self):
        a = {"gpu": {"host_id": "h1"}, "model_slug": "m"}
        b = {"gpu": {"host_id": "h2"}, "model_slug": "m"}
        self.assertNotEqual(entry_dedup_key(a), entry_dedup_key(b))


class TestSolveBreadth(unittest.TestCase):
    """Scoring v2 — capability = 0.7*problem_solving + 0.3*language_breadth."""

    def test_singleton_solved_is_100(self):
        solve, lang, cap = compute_solve_breadth(
            [{"id": "x", "difficulty": "hard", "passed": True}])
        self.assertEqual((solve, lang, cap), (100.0, 100.0, 100.0))

    def test_variant_partial_solves_full_problem_credit(self):
        # A hard base task ported to 6 languages, 3 pass: problem is SOLVED
        # (>=1 lang) so solve=100; language breadth = 3/6 = 50.
        tasks = [{"id": f"b_{i}", "base_id": "b", "difficulty": "hard",
                  "variant_count": 6, "passed": i < 3} for i in range(6)]
        solve, lang, cap = compute_solve_breadth(tasks)
        self.assertEqual(solve, 100.0)
        self.assertEqual(lang, 50.0)
        self.assertAlmostEqual(cap, SOLVE_WEIGHT * 100 + LANG_WEIGHT * 50, places=2)

    def test_variant_unsolved_earns_nothing(self):
        tasks = [{"id": f"b_{i}", "base_id": "b", "difficulty": "hard",
                  "variant_count": 6, "passed": False} for i in range(6)]
        solve, lang, cap = compute_solve_breadth(tasks)
        self.assertEqual((solve, lang, cap), (0.0, 0.0, 0.0))

    def test_language_breadth_denominator_is_solved_problems_only(self):
        # solved hard singleton + entirely-unsolved hard 6-variant.
        tasks = [{"id": "s", "difficulty": "hard", "passed": True}]
        tasks += [{"id": f"b_{i}", "base_id": "b", "difficulty": "hard",
                   "variant_count": 6, "passed": False} for i in range(6)]
        solve, lang, cap = compute_solve_breadth(tasks)
        self.assertEqual(solve, 50.0)   # 1 of 2 base problems solved (equal wt)
        self.assertEqual(lang, 100.0)   # among solved, the singleton = 6/6-equiv
        self.assertAlmostEqual(cap, SOLVE_WEIGHT * 50 + LANG_WEIGHT * 100, places=2)

    def test_score_run_exposes_v2_fields(self):
        entry = score_run({"suite_version": "v4", "tasks": [
            {"id": "x", "difficulty": "easy", "passed": True}]})
        for k in ("capability", "problem_solving", "language_breadth",
                  "scoring_version"):
            self.assertIn(k, entry)



class TestV5Scoring(unittest.TestCase):
    """v5 rules (brief §6.6): LANG over k>1 bases only, probes and
    budget-skipped units never scored, no imputation for v5 or any arm."""

    def _multi(self, base, k, passed, diff="hard"):
        return [{"id": f"{base}_{i}", "base_id": base, "difficulty": diff,
                 "variant_count": k, "passed": i < passed} for i in range(k)]

    def test_lang_over_multi_language_bases_only(self):
        tasks = [{"id": "s", "difficulty": "hard", "passed": True}] + self._multi("b", 6, 3)
        _s, lang_v4, _c = compute_solve_breadth(tasks)
        _s, lang_v5, _c = compute_solve_breadth(tasks, lang_multi_only=True)
        self.assertEqual(lang_v4, 75.0)    # the singleton counted as 6/6
        self.assertEqual(lang_v5, 50.0)    # only the k=6 base: 3/6

    def test_lang_is_zero_with_no_solved_multi_base(self):
        tasks = [{"id": "s", "difficulty": "hard", "passed": True}] + self._multi("b", 6, 0)
        solve, lang, cap = compute_solve_breadth(tasks, lang_multi_only=True)
        self.assertEqual((solve, lang), (50.0, 0.0))
        self.assertAlmostEqual(cap, SOLVE_WEIGHT * 50, places=2)

    def test_score_run_applies_v5_rules_by_suite_version(self):
        tasks = [{"id": "s", "difficulty": "hard", "passed": True}] + self._multi("b", 6, 3)
        v4 = score_run({"suite_version": "v4", "tasks": tasks})
        v5 = score_run({"suite_version": "v5", "tasks": tasks})
        self.assertEqual(v4["language_breadth"], 75.0)
        self.assertEqual(v5["language_breadth"], 50.0)
        self.assertNotIn("lang_scope", v4)          # the v4 board is frozen
        self.assertIn("capability_category_balanced", v5)

    def test_probes_and_budget_skips_are_not_scored(self):
        tasks = self._multi("b", 6, 6)
        probe = {"id": "p_f", "difficulty": "easy", "passed": False, "role": "probe"}
        skip = {"id": "z", "difficulty": "hard", "passed": False, "reason": "budget_skip"}
        clean = score_run({"suite_version": "v5", "tasks": tasks})
        noisy = score_run({"suite_version": "v5", "tasks": tasks + [probe, skip]})
        for k in ("capability", "problem_solving", "language_breadth", "tasks_total"):
            self.assertEqual(clean[k], noisy[k], k)
        self.assertEqual((noisy["probe_units"], noisy["budget_skipped"]), (1, 1))
        # a budget-skipped run is partial: never seated
        self.assertTrue(any("budget-skipped" in r for r in noisy["ineligible_reasons"]))

    def test_no_imputation_for_v5_or_any_arm(self):
        self.assertTrue(imputation_allowed("v4"))
        self.assertFalse(imputation_allowed("v5"))
        for arm in ("L0", "L1", "L2"):
            self.assertFalse(imputation_allowed("v4", arm=arm))
            self.assertFalse(imputation_allowed("v5", arm=arm))

    def test_only_the_board_arm_is_eligible(self):
        tasks = self._multi("b", 6, 6)
        l0 = score_run({"suite_version": "v5", "tasks": tasks,
                        "harness": {"arm": "L0", "board_arm": "L0"}})
        l1 = score_run({"suite_version": "v5", "tasks": tasks,
                        "harness": {"arm": "L1", "board_arm": "L0"}})
        self.assertFalse(any(r.startswith("arm ") for r in l0["ineligible_reasons"]))
        self.assertIn("arm L1", l1["ineligible_reasons"])

def _cli(monkeypatch, tmp_path, *argv):
    import scoring
    monkeypatch.setattr(scoring, "RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(scoring, "LEADERBOARD_PATH", str(tmp_path / "leaderboard.json"))
    monkeypatch.setattr(sys, "argv", ["scoring.py", *argv])
    scoring.main()


def _one_run(tmp_path, model="FRESH-ROW"):
    """A results dir holding one seatable run (a suite with no pinned size)."""
    import json
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "eval-fresh-20261001-000000.json").write_text(json.dumps({
        "timestamp": "2026-10-01T00:00:00", "model": model, "model_slug": "fresh",
        "suite_version": "t1", "gpu": {"host_id": "rig"},
        "tasks": [{"id": "a", "difficulty": "easy", "passed": True}]}))


def test_rebuild_then_html_renders_the_rebuilt_board(monkeypatch, tmp_path, capsys):
    """`--rebuild --html` used to return before the rebuild and publish the
    stale leaderboard.json without a word."""
    import json
    _one_run(tmp_path)
    (tmp_path / "leaderboard.json").write_text(json.dumps({"entries": [
        {"model": "STALE-ROW", "suite_version": "v4", "tasks_total": V4_UNITS}]}))
    out = tmp_path / "board.html"
    _cli(monkeypatch, tmp_path, "--rebuild", "--html", str(out))
    said = capsys.readouterr().out
    assert "Rebuilt leaderboard" in said and "(1 entries)" in said, said
    page = out.read_text()
    assert "STALE-ROW" not in page and "FRESH-ROW" in page


def test_rebuild_with_no_result_files_refuses_and_keeps_the_board(monkeypatch, tmp_path):
    """evals/results is not in the repo, so a fresh clone or worktree has
    none: `--rebuild` there printed "Rebuilt leaderboard from 0 entries" and
    wrote an empty board over the committed one (review 2026-10-09, F9)."""
    import json
    import pytest
    board = json.dumps({"entries": [
        {"model": "SEATED-ROW", "suite_version": "v4", "tasks_total": V4_UNITS}]})
    for make_dir in (False, True):                 # no results dir; an empty one
        if make_dir:
            (tmp_path / "results").mkdir()
            (tmp_path / "results" / "server-m-20261001.log").write_text("not a results file\n")
        (tmp_path / "leaderboard.json").write_text(board)
        with pytest.raises(SystemExit) as ex:
            _cli(monkeypatch, tmp_path, "--rebuild")
        msg = str(ex.value)
        assert ex.value.code not in (0, None)
        assert "no eval-*.json result files" in msg and str(tmp_path / "results") in msg
        assert "unchanged" in msg
        assert (tmp_path / "leaderboard.json").read_text() == board
        assert not (tmp_path / "leaderboard.json.tmp").exists()


def test_rebuild_with_only_ineligible_runs_still_writes(monkeypatch, tmp_path, capsys):
    """Negative control: the refusal is for an ABSENT results set. Runs that
    are all ineligible are a real, if empty, answer."""
    import json
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "eval-x-20261001-000000.json").write_text(json.dumps({
        "model": "X", "model_slug": "x", "suite_version": "t1", "gpu": {"host_id": "rig"},
        "harness": {"greedy": True},
        "tasks": [{"id": "a", "difficulty": "easy", "passed": True}]}))
    (tmp_path / "leaderboard.json").write_text(json.dumps({"entries": [{"model": "OLD"}]}))
    _cli(monkeypatch, tmp_path, "--rebuild")
    assert "Rebuilt leaderboard from 0 entries" in capsys.readouterr().out
    assert json.loads((tmp_path / "leaderboard.json").read_text())["entries"] == []


def test_html_to_a_missing_directory_is_one_error_line(monkeypatch, tmp_path):
    import pytest
    with pytest.raises(SystemExit) as ex:
        _cli(monkeypatch, tmp_path, "--html", str(tmp_path / "nope" / "b.html"))
    assert "cannot write" in str(ex.value) and "nope" in str(ex.value)
    assert not (tmp_path / "nope").exists()


def test_rebuild_html_to_stdout_is_only_the_page(monkeypatch, tmp_path, capsys):
    """`--rebuild --html -` printed "Rebuilt leaderboard…" ahead of the page,
    so a piped board began with a line of text."""
    _one_run(tmp_path)
    _cli(monkeypatch, tmp_path, "--rebuild", "--html", "-")
    got = capsys.readouterr()
    assert got.out.lstrip().lower().startswith("<!doctype html"), got.out[:80]
    assert "Rebuilt leaderboard" in got.err


def test_html_still_wins_over_score(monkeypatch, tmp_path, capsys):
    (tmp_path / "results").mkdir()
    out = tmp_path / "board.html"
    _cli(monkeypatch, tmp_path, "--score", str(tmp_path / "absent.json"), "--html", str(out))
    assert out.exists() and "Wrote" in capsys.readouterr().out


if __name__ == "__main__":
    unittest.main()
