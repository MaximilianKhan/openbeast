"""Tier-3 verdict tooling vs. the harness wall timeout (2026-09-30 review,
A-campaign-1/-4, B-campaign-6).

The fresh Tier-3 record said "0 timeouts". Two rows had hit the 2400 s wall
timeout (exit -1) and still PASSED, since their solutions were on disk before
a request hung. run_eval records such a row with tokens 0 and iterations
None. row_validity walked only the fails, so a timed-out PASS was invisible.
tier3_verdict's paired() skipped only None, so the recorded 0 went into R2 as
a real value (published prompt Δ -5189; the honest figure is -2163).

Every case builds its own rows. Nothing reads evals/results/ or agents/logs/.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = ROOT / "scratch"
sys.path.insert(0, str(SCRATCH))

import row_validity  # noqa: E402
import tier3_verdict  # noqa: E402

UNITS = [f"{i:02d}_u_f" for i in range(20)]
TIMEOUT = {"agent_exit_code": -1, "elapsed_seconds": 2400.1, "tokens_completion": 0,
           "tokens_prompt": 0, "iterations": None}


def _row(uid, passed, **kw):
    r = {"id": uid, "name": uid, "language": "zig", "passed": passed,
         "elapsed_seconds": 300.0, "agent_exit_code": 0,
         "validation_output": "OK" if passed else "FAIL: wrong output",
         "tokens_completion": 10_000, "tokens_prompt": 100_000, "iterations": 8}
    r.update(kw)
    return r


def _cell(tmp_path, name, passed, packs, overrides=None, commit="c0ffee", tokens=None):
    overrides = overrides or {}
    tasks = []
    for u in UNITS:
        tc, tp = tokens or (10_000, 100_000)
        tasks.append(_row(u, u in passed, **{"tokens_completion": tc, "tokens_prompt": tp,
                                             **overrides.get(u, {})}))
    d = {"timestamp": "2026-09-30T01:00:00", "model": "Stub", "tasks": tasks,
         "runtime": {"openbeast_commit": commit, "openbeast_dirty": False},
         "inference_engine": {"build": "10865", "commit": "d4389a4dd"},
         "harness": {"greedy": True, "packs": {"zig": "abcd1234"} if packs else {},
                     "env": {"weights": {"sha256": "w" * 64}}}}
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(d))
    return str(p)


def _run_verdict(*args, cwd=ROOT):
    return subprocess.run([sys.executable, str(SCRATCH / "tier3_verdict.py"), "--agent-logs", "none", *args],
                          capture_output=True, text=True, cwd=cwd)


def _verdict(*args):
    r = _run_verdict(*args)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


# --- row_validity -----------------------------------------------------------------

def test_timed_out_pass_is_listed_not_invisible(tmp_path):
    d = json.loads(Path(_cell(tmp_path, "x", set(UNITS[:10]), True,
                              overrides={UNITS[3]: TIMEOUT})).read_text())
    p = tmp_path / "row.json"
    p.write_text(json.dumps(d))
    r = subprocess.run([sys.executable, str(SCRATCH / "row_validity.py"), str(p), "--agent-logs", "none"],
                       capture_output=True, text=True)
    # still a clean row: the PASS validated and a timeout is not an infra death
    assert r.returncode == 0, r.stdout
    assert "timeouts=1" in r.stdout
    assert f"{UNITS[3]} [PASS]" in r.stdout and "tokens NOT recorded" in r.stdout
    assert row_validity.audit(d)["timeouts"] == {UNITS[3]: "PASS"}
    # negative control: without a timeout nothing is listed
    d2 = json.loads(Path(_cell(tmp_path, "y", set(UNITS[:10]), True)).read_text())
    assert row_validity.audit(d2)["timeouts"] == {}
    p.write_text(json.dumps(d2))
    r2 = subprocess.run([sys.executable, str(SCRATCH / "row_validity.py"), str(p), "--agent-logs", "none"],
                        capture_output=True, text=True)
    assert "timeouts=0" in r2.stdout and "wall timeout(s)" not in r2.stdout


def test_tokens_unrecorded():
    assert row_validity.tokens_unrecorded(_row("a", True, **TIMEOUT))
    assert row_validity.tokens_unrecorded(_row("a", False, **TIMEOUT))
    assert row_validity.tokens_unrecorded(_row("a", True, tokens_completion=0))
    assert not row_validity.tokens_unrecorded(_row("a", True))


# --- tier3_verdict ----------------------------------------------------------------

def test_timeout_row_never_enters_a_token_statistic(tmp_path):
    # Every P1 row spends 2,000 MORE prompt tokens than its P0 twin, except
    # one timed-out P1 PASS whose tokens read 0 (a -100,000 "saving").
    p0 = _cell(tmp_path, "P0", set(UNITS[:5]), False)
    p1 = _cell(tmp_path, "P1", set(UNITS[:5]) | {UNITS[7]}, True, tokens=(10_000, 102_000),
               overrides={UNITS[7]: TIMEOUT})
    g0 = tier3_verdict.load_cell(p0, "zig", None)
    g1 = tier3_verdict.load_cell(p1, "zig", None)
    pr = tier3_verdict.paired(g0, g1)
    assert pr["d_prompt"] == [2000] * 19          # the timeout pair is out
    assert all(d == 0 for d in pr["d_tok_all"]) and len(pr["d_tok_all"]) == 19
    assert pr["unrecorded"] == 0                   # a rescue is not a both-pass pair
    assert pr["b"] == [UNITS[7]]                   # the PASS still counts in R1
    assert g1["timeouts"] == {UNITS[7]: "PASS"}

    out = _verdict("--p0", p0, "--p1", p1)
    assert "mean Δ=+2000" in out and "-100000" not in out and "-3163" not in out
    assert "wall timeouts" in out and f"P1a {UNITS[7]} [PASS]" in out
    assert "sensitivity, wall-timeout units dropped from their pair: b=0 c=0 net=+0" in out
    # negative control: with no timeout there is no sensitivity line
    p1c = _cell(tmp_path, "P1c", set(UNITS[:5]) | {UNITS[7]}, True, tokens=(10_000, 102_000))
    assert "sensitivity" not in _verdict("--p0", p0, "--p1", p1c)


def test_provenance_warns_on_a_cross_commit_pair(tmp_path):
    p0 = _cell(tmp_path, "P0", set(UNITS[:5]), False, commit="aaaa1111")
    p1 = _cell(tmp_path, "P1", set(UNITS[:9]), True, commit="bbbb2222")
    out = _verdict("--p0", p0, "--p1", p1)
    assert "PROVENANCE  WARNING: commit differs across cells" in out
    p1s = _cell(tmp_path, "P1s", set(UNITS[:9]), True, commit="aaaa1111")
    out2 = _verdict("--p0", p0, "--p1", p1s)
    assert "WARNING" not in out2 and "consistent across cells" in out2


def test_heldout_units_are_read_alone_not_pooled(tmp_path):
    held = UNITS[15:]
    # in-sample: 3 rescues; held-out: 5 rescues
    p0 = _cell(tmp_path, "P0", set(), False)
    p1 = _cell(tmp_path, "P1", set(UNITS[:3]) | set(held), True)
    pooled = _verdict("--p0", p0, "--p1", p1)
    assert "rescues b=8 regressions c=0" in pooled
    out = _verdict("--p0", p0, "--p1", p1, "--heldout", ",".join(held))
    assert "rescues b=3 regressions c=0" in out                 # R1 is in-sample only
    assert "pair 0: units=15" in out
    assert "HELD-OUT    pooled one-sided exact sign test P1>P0: b=5 c=0 net=+5 p=0.0312" in out
    assert "P1 > P0 at α=0.05" in out
    suite = tmp_path / "zig-heldout.json"
    suite.write_text(json.dumps({"units": held}))
    assert "b=5 c=0" in _verdict("--p0", p0, "--p1", p1, "--heldout", str(suite))


def test_r2_excluded_counts_only_both_pass_pairs(tmp_path):
    # A timed-out rescue was never in R2's both-pass n; a timed-out both-pass
    # unit was. Only the latter may be reported as "excluded".
    p0 = _cell(tmp_path, "P0", set(UNITS[:5]), False)
    p1r = _cell(tmp_path, "P1r", set(UNITS[:5]) | {UNITS[7]}, True, overrides={UNITS[7]: TIMEOUT})
    out = _verdict("--p0", p0, "--p1", p1r)
    assert "R2 CO-PRIMARY (units passed in both arms, n=5):" in out
    assert "excluded" not in out
    p1b = _cell(tmp_path, "P1b", set(UNITS[:5]), True, overrides={UNITS[2]: TIMEOUT})
    g = tier3_verdict.paired(tier3_verdict.load_cell(p0, "zig", None),
                             tier3_verdict.load_cell(p1b, "zig", None))
    assert g["unrecorded"] == 1 and len(g["d_tok"]) == 4
    out2 = _verdict("--p0", p0, "--p1", p1b)
    assert "n=4; 1 pair(s) excluded" in out2


def test_heldout_missing_suite_file_refuses(tmp_path):
    held = UNITS[15:]
    p0 = _cell(tmp_path, "P0", set(), False)
    p1 = _cell(tmp_path, "P1", set(UNITS[:3]) | set(held), True)
    r = _run_verdict("--p0", p0, "--p1", p1, "--heldout", "evals/suites/no-such-heldout.json", cwd=tmp_path)
    assert r.returncode == 2, r.stdout
    assert "no such suite file" in r.stderr
    assert "in-sample only" not in r.stdout and "VERDICT" not in r.stdout
    # a suite json without a units list is refused too
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"tasks": held}))
    r2 = _run_verdict("--p0", p0, "--p1", p1, "--heldout", str(bad))
    assert r2.returncode == 2 and "no 'units' list" in r2.stderr
    # negative control: the same suite, present, runs
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"units": held}))
    assert _run_verdict("--p0", p0, "--p1", p1, "--heldout", str(good)).returncode == 0


def test_heldout_ids_matching_no_rows_refuse(tmp_path):
    p0 = _cell(tmp_path, "P0", set(), False)
    p1 = _cell(tmp_path, "P1", set(UNITS[:3]), True)
    r = _run_verdict("--p0", p0, "--p1", p1, "--heldout", "999_nope_f,998_nope_f")
    assert r.returncode == 2, r.stdout
    assert "none of which appear" in r.stderr and "VERDICT" not in r.stdout
    # partial match still runs, warns, and the banner counts only real units
    r2 = _run_verdict("--p0", p0, "--p1", p1, "--heldout", f"{UNITS[19]},999_nope_f")
    assert r2.returncode == 0
    assert "999_nope_f" in r2.stderr
    assert "HELD-OUT    1 unit(s) read separately below (1 named but absent)" in r2.stdout
