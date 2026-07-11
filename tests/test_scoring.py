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
                     compute_solve_breadth, SOLVE_WEIGHT, LANG_WEIGHT)


class TestSuiteVersion(unittest.TestCase):
    def test_explicit_field_wins(self):
        self.assertEqual(
            _suite_version({"suite_version": "v9", "summary": {"total": 291}}),
            "v9")

    def test_291_units_is_v4(self):
        self.assertEqual(_suite_version({"summary": {"total": 291}}), "v4")

    def test_323_units_is_v3_5(self):
        self.assertEqual(_suite_version({"summary": {"total": 323}}), "v3.5")

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


if __name__ == "__main__":
    unittest.main()
