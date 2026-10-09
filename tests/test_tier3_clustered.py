"""Tier-3 verdict: the per-unit read of R1 and the non-inferiority read of the
champion guard (review 2026-10-09, evals F13 / F14).

  * F13 — R1 pooled the replicates of the SAME units as independent pairs
    (2 replicates x 30 units read as 60 pairs), and its "net >= 7" bar is a
    pooled count that grows with the replicate count.
  * F14 — the guard's rule (clean = p > 0.05 or net >= 0) is failure to
    reject: 0 rescues and 5 regressions is p = 0.0625 and reads CLEAN.

Both reads are printed BESIDE the registered ones; the R1, R3 and VERDICT
lines must not move. Every cell is built here in a temp dir.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = ROOT / "scratch"
sys.path.insert(0, str(SCRATCH))

import tier3_verdict  # noqa: E402

UNITS = [f"{i:02d}_u_f" for i in range(30)]


def _cell(tmp_path, name, passed, packs):
    tasks = [{"id": u, "name": u, "language": "zig", "passed": u in passed,
              "elapsed_seconds": 300.0, "agent_exit_code": 0,
              "validation_output": "OK" if u in passed else "FAIL: wrong output",
              "tokens_completion": 10_000, "tokens_prompt": 100_000, "iterations": 8}
             for u in UNITS]
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps({
        "timestamp": "2026-09-30T01:00:00", "model": "Stub", "tasks": tasks,
        "runtime": {"openbeast_commit": "c0ffee", "openbeast_dirty": False},
        "inference_engine": {"build": "10865", "commit": "d4389a4dd"},
        "harness": {"greedy": True, "packs": {"zig": "abcd1234"} if packs else {},
                    "env": {"weights": {"sha256": "w" * 64}}}}))
    return str(p)


def _verdict(*args):
    r = subprocess.run([sys.executable, str(SCRATCH / "tier3_verdict.py"),
                        "--agent-logs", "none", *args], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _line(out: str, start: str) -> str:
    (ln,) = [ln for ln in out.splitlines() if ln.strip().startswith(start)]
    return ln


# --- F13 ---------------------------------------------------------------------

def test_per_unit_sign_counts_each_unit_once():
    # u0 rescued in both replicates, u1 rescued once, u2 rescued then
    # regressed (net 0), u3 regressed once, u4 never discordant.
    ids = ["u0", "u1", "u2", "u3", "u4"]
    pairs = [{"ids": ids, "b": ["u0", "u1", "u2"], "c": []},
             {"ids": ids, "b": ["u0"], "c": ["u2", "u3"]}]
    pu = tier3_verdict.per_unit_sign(pairs)
    assert (pu["pos"], pu["neg"], pu["zero"], pu["units"]) == (2, 1, 2, 5)
    assert pu["p"] == tier3_verdict.mcnemar_exact(2, 1) == 1.0
    # The review's recomputation of the 2026-09-30 cells: 18 up, 1 down.
    assert abs(tier3_verdict.mcnemar_exact(18, 1) - 7.6e-5) < 1e-6


def test_replicated_rescues_are_not_double_counted(tmp_path):
    """The same 4 units rescued in both replicates: pooled b = 8 (p = 0.0078,
    SHIP), but it is 4 units, and 4 of 4 is p = 0.125."""
    base = set(UNITS[:10])
    cells = dict(p0a=_cell(tmp_path, "P0a", base, False),
                 p0b=_cell(tmp_path, "P0b", base, False),
                 p1a=_cell(tmp_path, "P1a", base | set(UNITS[10:14]), True),
                 p1b=_cell(tmp_path, "P1b", base | set(UNITS[10:14]), True),
                 c0=_cell(tmp_path, "C0", base, False), c1=_cell(tmp_path, "C1", base, True))
    out = _verdict("--p0", cells["p0a"], cells["p0b"], "--p1", cells["p1a"], cells["p1b"],
                   "--c0", cells["c0"], "--c1", cells["c1"])
    # The registered lines are what they were.
    assert "R1 PRIMARY  pooled McNemar: rescues b=8 regressions c=0 net=+8 p=0.0078" in out
    assert _line(out, "VERDICT:").startswith("VERDICT: SHIP")
    # Beside them: the clustered read and the bar per replicate.
    pu = _line(out, "per-unit")
    assert "each of 30 units counted once" in pu and "2 replicate(s)" in pu
    assert "net-positive=4 net-negative=0 zero=26" in pu and "sign p=0.1250" in pu
    per = _line(out, "net per replicate")
    assert "+4.0" in per and "pooled net +8 over 2" in per
    assert "net>=7 is pooled = 3.5 per replicate" in per


def test_one_replicate_reads_the_same_both_ways(tmp_path):
    """Negative control: with a single replicate there is nothing to
    cluster, so the per-unit test equals the pooled one."""
    base = set(UNITS[:10])
    out = _verdict("--p0", _cell(tmp_path, "P0", base, False),
                   "--p1", _cell(tmp_path, "P1", base | set(UNITS[10:18]), True))
    assert "pooled McNemar: rescues b=8 regressions c=0 net=+8 p=0.0078" in out
    pu = _line(out, "per-unit")
    assert "net-positive=8 net-negative=0 zero=22" in pu and "sign p=0.0078" in pu
    assert "net>=7 is pooled = 7.0 per replicate at 1 replicate(s)" in out


# --- F14 ---------------------------------------------------------------------

def _guard(tmp_path, c1_passed):
    base = set(UNITS[:10])
    return _verdict("--p0", _cell(tmp_path, "P0", base, False),
                    "--p1", _cell(tmp_path, "P1", base | set(UNITS[10:20]), True),
                    "--c0", _cell(tmp_path, "C0", set(UNITS[:20]), False),
                    "--c1", _cell(tmp_path, "C1", c1_passed, True))


def test_guard_trip_point_is_six_at_alpha_05():
    assert tier3_verdict.mcnemar_exact(0, 5) == 0.0625
    assert tier3_verdict.guard_trip_point() == 6
    assert tier3_verdict.guard_trip_point(alpha=0.10) == 5


def test_five_regressions_read_clean_but_fail_non_inferiority(tmp_path):
    out = _guard(tmp_path, set(UNITS[:15]))            # the champion loses 5 units
    r3 = _line(out, "R3 GUARD")
    # The registered rule and the verdict built on it are untouched...
    assert "rescues=0 regressions=5 net=-5 p=0.062 → CLEAN" in r3
    assert "guard=clean" in _line(out, "VERDICT:")
    assert _line(out, "VERDICT:").startswith("VERDICT: SHIP")
    # ...and the read beside it says what the guard is for.
    ni = _line(out, "non-inferiority read")
    assert "(net >= -2): net=-5 → FAILS" in ni
    assert "CLEAN until 6 regressions with no rescue" in ni and "p=0.0625" in ni


def test_non_inferiority_holds_at_the_margin_and_above(tmp_path):
    """Negative control: net -2 is inside the margin, and so is a gain."""
    out = _guard(tmp_path, set(UNITS[:18]))            # loses 2
    assert "(net >= -2): net=-2 → HOLDS" in _line(out, "non-inferiority read")
    assert "→ CLEAN" in _line(out, "R3 GUARD")
    out = _guard(tmp_path, set(UNITS[:25]))            # gains 5
    assert "net=+5 → HOLDS" in _line(out, "non-inferiority read")


def test_no_guard_cells_no_non_inferiority_line(tmp_path):
    base = set(UNITS[:10])
    out = _verdict("--p0", _cell(tmp_path, "P0", base, False),
                   "--p1", _cell(tmp_path, "P1", base | set(UNITS[10:20]), True))
    assert "non-inferiority" not in out and "R3 GUARD    NOT evaluated" in out
