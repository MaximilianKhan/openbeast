"""evals/ablation.py — the paired-arm delta math (brief §5.2-§5.4).

Pinned: the exact McNemar p, the MDE table the brief computed (so the
`within_churn` flag means what the brief says), row validity (infra rows
never enter a pair), the Horvitz-Thompson regression estimate for a
stratified L2, sharing, pack strata, the cost-to-fix sign test, scores on
measured units, the provenance guard, and the pooled cross-model test.
"""

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import ablation as A  # noqa: E402


# --- exact tests ---------------------------------------------------------------

def test_mcnemar_exact_values():
    assert A.mcnemar_p(0, 0) == 1.0
    assert A.mcnemar_p(10, 2) == pytest.approx(2 * 79 / 4096)          # 0.0386
    assert A.mcnemar_p(2, 10) == A.mcnemar_p(10, 2)                    # two-sided
    assert A.mcnemar_p(9, 2) == pytest.approx(2 * 67 / 2048)           # 0.0654
    assert A.mcnemar_p(26, 2) < 1e-5                                   # Tier-3 pooled
    assert A.mcnemar_p(5, 5) == 1.0


@pytest.mark.parametrize("n,q,r,want", [
    (15, 0.30, 1, 7.5),      # zig scored
    (15, 0.30, 2, 4.25),
    (15, 0.30, 4, 3.0),
    (60, 0.12, 1, 9.0),      # go+c+cpp+rust
    (60, 0.12, 2, 5.25),
    (75, 0.15, 1, 11.0),     # all compiled
    (75, 0.15, 2, 6.25),
])
def test_mde_reproduces_the_brief_table(n, q, r, want):
    assert A.mde(n, q, r) == want


def test_mde_with_probes_is_about_8_5():
    assert 8.0 <= A.mde(22, 0.30, 1) <= 9.0


# --- fixtures ------------------------------------------------------------------

def _row(uid, passed, lang="zig", **kw):
    r = {"id": uid, "passed": passed, "language": lang, "agent_exit_code": 0,
         "tokens_completion": 1000, "iterations": 5, "difficulty": "hard",
         "base_id": uid.rsplit("_", 1)[0], "variant_count": 6}
    r.update(kw)
    return r


def _results(rows, arm, **harness):
    return {"model_slug": "m", "suite_version": "v5", "tasks": rows,
            "inference_engine": {"build": 1},
            "harness": {"arm": arm, "arm_label": arm, "era": "e1", "pair_id": "p1",
                        **harness}}


# --- validity + contrasts ----------------------------------------------------------

def test_infra_rows_never_enter_a_pair():
    a = _results([_row("u1_f", False), _row("u2_f", False), _row("u3_f", True),
                  _row("u4_f", False)], "L0")
    b = _results([_row("u1_f", True),
                  _row("u2_f", True, reason="server_error"),
                  _row("u3_f", False, agent_exit_code=-9),
                  _row("u4_f", False, tokens_completion=0)], "L1")
    pairs, dropped, _ = A.pair(a, b)
    assert [p[0] for p in pairs] == ["u1_f"]
    assert dict(dropped) == {"u2_f": "server_error", "u3_f": "signal death (exit -9)",
                             "u4_f": "zero-token fail (dead server)"}


def test_a_wall_timeout_is_a_verdict_not_an_infra_row():
    assert A.invalid_reason(_row("u", False, agent_exit_code=-1, tokens_completion=0)) is None


def test_contrast_counts_rescues_and_regressions_and_flags_churn():
    a_rows = [_row(f"u{i}_f", i < 5) for i in range(15)]        # 5/15 pass
    b_rows = [_row(f"u{i}_f", i < 10) for i in range(15)]       # 10/15: +5, 0 lost
    rep = A.report(_results(a_rows, "L0"), _results(b_rows, "L1"))
    zig = next(r for r in rep["contrasts"] if r["contrast"] == "zig")
    assert (zig["n"], zig["a_pass"], zig["b_pass"], zig["b"], zig["c"], zig["net"]) == \
        (15, 5, 10, 5, 0, 5)
    assert zig["p"] == pytest.approx(2 / 32)
    assert zig["mde"] == 7.5 and zig["within_churn"] is True
    assert zig["rescued"] == [f"u{i}_f" for i in range(5, 10)]
    # +9 on 15 is outside the churn floor
    b2 = [_row(f"u{i}_f", i < 14) for i in range(15)]
    z2 = next(r for r in A.report(_results(a_rows, "L0"), _results(b2, "L1"))["contrasts"]
              if r["contrast"] == "zig")
    assert z2["net"] == 9 and z2["within_churn"] is False and z2["p"] < 0.01


def test_pack_strata_are_reported_separately():
    a = [_row("163_x_f", False, stratum="heldout"), _row("159_y_f", False, stratum="insample"),
         _row("31_p_f", False, stratum="insample", role="probe")]
    b = [_row("163_x_f", True, stratum="heldout"), _row("159_y_f", True, stratum="insample"),
         _row("31_p_f", False, stratum="insample", role="probe")]
    rep = A.report(_results(a, "L0"), _results(b, "L1"))
    by = {r["contrast"]: r for r in rep["contrasts"]}
    assert (by["zig:heldout"]["n"], by["zig:heldout"]["b"]) == (1, 1)
    assert (by["zig:insample"]["n"], by["zig:insample"]["b"]) == (2, 1)


def test_shared_rows_are_concordant_and_skip_cost_to_fix():
    a = [_row("u1_a", True, lang="python")]
    b = [_row("u1_a", True, lang="python", shared_from="L0")]
    rep = A.report(_results(a, "L0"), _results(b, "L2"))
    py = next(r for r in rep["contrasts"] if r["contrast"] == "python")
    assert (py["b"], py["c"]) == (0, 0)
    assert rep["cost_to_fix"]["both_passed_measured"] == 0


def test_cost_to_fix_sign_test():
    a = [_row(f"u{i}_f", True, tokens_completion=5000, iterations=10) for i in range(8)]
    b = [_row(f"u{i}_f", True, tokens_completion=1000, iterations=4) for i in range(8)]
    ctf = A.report(_results(a, "L0"), _results(b, "L1"))["cost_to_fix"]
    assert ctf["tokens_completion"]["fewer_in_b"] == 8
    assert ctf["tokens_completion"]["median_delta"] == -4000
    assert ctf["tokens_completion"]["p"] == pytest.approx(2 / 256)
    # unrecorded tokens (wall timeout) never enter the statistic as a 0
    a[0]["agent_exit_code"] = -1
    ctf = A.report(_results(a, "L0"), _results(b, "L1"))["cost_to_fix"]
    assert ctf["tokens_completion"]["n"] == 7


# --- stratified L2: Horvitz-Thompson regressions -------------------------------

def test_stratified_c_is_horvitz_thompson():
    """L0: 6 fails + 12 passes. L2 reruns all 6 fails (4 rescued) and a
    third of the passes (4 of 12; 1 regressed). b = 4 exactly; each sampled
    regression stands for 1/pi = 3 regressions in the population."""
    f = 1 / 3
    a = [_row(f"f{i}_f", False) for i in range(6)] + [_row(f"p{i}_f", True) for i in range(12)]
    b = [_row(f"f{i}_f", i < 4) for i in range(6)]
    b += [_row(f"p{i}_f", i != 0) for i in range(4)]                       # p0 regressed
    b += [_row(f"p{i}_f", False, reason="not_in_arm") for i in range(4, 12)]
    pi = {f"f{i}_f": 1.0 for i in range(6)}
    pi.update({f"p{i}_f": f for i in range(4)})
    rep = A.report(_results(a, "L0"),
                   _results(b, "L2", arm_meta={"stratified": True, "pi": pi}))
    z = next(r for r in rep["contrasts"] if r["contrast"] == "zig")
    assert z["p_label"] == "stratified"
    assert (z["n"], z["sampled"], z["unsampled"]) == (18, 10, 8)
    assert z["b"] == 4 and z["c"] == 1
    assert z["c_hat"] == pytest.approx(3.0)
    assert z["c_hat_se"] == pytest.approx(math.sqrt((1 - f) / f ** 2), abs=0.01)
    assert z["net"] == pytest.approx(1.0)
    assert z["p"] == A.mcnemar_p(4, 3)
    assert not rep["dropped"]                     # unsampled is not "invalid"


def test_unstratified_not_in_arm_rows_are_dropped_not_counted():
    a = [_row("u1_f", True), _row("u2_f", True)]
    b = [_row("u1_f", True), _row("u2_f", False, reason="not_in_arm")]
    pairs, dropped, uns = A.pair(_results(a, "L0"), _results(b, "L2"))
    assert [p[0] for p in pairs] == ["u1_f"] and dropped == [("u2_f", "not_in_arm")] and not uns


# --- scores, provenance, exposure, pool --------------------------------------------

def test_scores_on_measured_units_only():
    rows = [_row("b1_a", True, lang="python", variant_count=2), _row("b1_f", False, variant_count=2),
            _row("s1", True, lang="python", variant_count=1, base_id=None),
            _row("p_f", False, role="probe"), _row("z_f", False, reason="budget_skip")]
    s = A.arm_scores(_results(rows, "L0"))
    assert s["n"] == 3                            # probe + budget skip excluded
    assert s["solve"] == 100.0 and s["lang"] == 50.0      # LANG over the k>1 base only


def test_provenance_guard(tmp_path, capsys):
    a = _results([_row("u1_f", True)], "L0")
    b = _results([_row("u1_f", True)], "L1", era="e2")
    assert A.provenance_mismatches(a, b) == ["harness.era: 'e1' vs 'e2'"]
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    assert A.main([str(pa), str(pb)]) == 1
    assert "NOT A CLEAN PAIR" in capsys.readouterr().out
    assert A.main([str(pa), str(pb), "--force"]) == 0


def test_exposure_rate():
    rows = [_row("u1_f", True), _row("u2_f", False), _row("u3_a", True, lang="python",
                                                           shared_from="L0")]
    b = _results(rows, "L2", diag_scope=["zig", "go"],
                 diag_exposure={"units_exposed": 1, "units_hint_fired": 1,
                                "units_card_fired": 0})
    e = A.exposure(b)
    assert e == {"units_exposed": 1, "units_run_in_scope": 2, "rate": 0.5,
                 "units_hint_fired": 1, "units_card_fired": 0}


def test_pooled_sign_test_across_models():
    def pr(b_n, c_n, model):
        a = [_row(f"u{i}_f", False) for i in range(b_n)] + \
            [_row(f"v{i}_f", True) for i in range(c_n)]
        b = [_row(f"u{i}_f", True) for i in range(b_n)] + \
            [_row(f"v{i}_f", False) for i in range(c_n)]
        ra, rb = _results(a, "L0"), _results(b, "L1")
        ra["model_slug"] = rb["model_slug"] = model
        return ra, rb
    res = A.pool([pr(5, 1, "m1"), pr(4, 0, "m2"), pr(3, 1, "m3")])
    assert (res["b"], res["c"], res["net"]) == (12, 2, 10)
    assert res["p"] == A.mcnemar_p(12, 2) and res["p"] < 0.05
    assert [s["model"] for s in res["strata"]] == ["m1", "m2", "m3"]
    # each model alone is within its churn floor; the pool is not
    assert all(A.mcnemar_p(s["b"], s["c"]) > 0.05 for s in res["strata"])


def test_profile_mode_reads_the_three_arms(tmp_path, capsys):
    paths = {}
    for arm, passed in (("L0", 3), ("L1", 6), ("L2", 7)):
        rows = [_row(f"u{i}_f", i < passed) for i in range(10)]
        p = tmp_path / f"{arm}.json"
        p.write_text(json.dumps(_results(rows, arm)))
        paths[arm] = str(p)
    st = tmp_path / "profile.json"
    st.write_text(json.dumps({"results": paths}))
    out = tmp_path / "rep.json"
    assert A.main(["--profile", str(st), "--json", str(out)]) == 0
    reps = json.loads(out.read_text())
    assert [(r["a"]["file_arm"], r["b"]["file_arm"]) for r in reps] == \
        [("L0", "L1"), ("L1", "L2"), ("L0", "L2")]


# --- integrity exclusions (evals/boards/v5/exclusions.json) -------------------------

def test_board_exclusions_drop_the_unit_as_contaminated():
    board = {"runs": [{"id": "R-L0", "files": ["eval-m-L0-1.json"]}]}
    excl = {"exclusions": [
        {"unit": "u2_f", "run": "R-L0", "reason": "contaminated: the scan flagged it"},
        {"unit": "u3_f", "file": "eval-m-L0-1.json", "reason": "withheld for another reason"},
        {"unit": "u1_f", "run": "OTHER", "reason": "contaminated: another run"},
        {"unit": "u4_f", "file": "eval-m-L1-1.json", "reason": "contaminated: another file"}]}
    a = _results([_row("u1_f", False), _row("u2_f", True), _row("u3_f", True),
                  _row("u4_f", False)], "L0")
    b = _results([_row("u1_f", True), _row("u2_f", True), _row("u3_f", True),
                  _row("u4_f", True)], "L1")
    A.apply_exclusions(a, "/x/eval-m-L0-1.json", excl, board)
    pairs, dropped, _ = A.pair(a, b)
    assert [p[0] for p in pairs] == ["u1_f", "u4_f"]
    assert dict(dropped) == {"u2_f": "contaminated", "u3_f": "excluded"}
    # a file no board row lists matches only file entries (and entries naming neither)
    b2 = _results([_row("u4_f", True), _row("u1_f", True)], "L1")
    A.apply_exclusions(b2, "eval-m-L1-1.json", excl, board)
    assert [t.get("reason") for t in b2["tasks"]] == ["contaminated", None]


def test_cli_drops_q38u_l0s_contaminated_unit(capsys):
    """The committed Q38U-L0 file withholds 163_bigint_divmod_e (contaminated):
    the L0 -> L2 contrast must not pair it."""
    d = ROOT / "evals" / "boards" / "v5"
    l0 = d / "eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L0-20261122-083815.json"
    l2 = d / "diagnostics" / "eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L2-20261122-192701.json"
    if not (l0.exists() and l2.exists()):
        pytest.skip("committed Q38U files not present")
    rep = A.report(A._load(str(l0)), A._load(str(l2)))
    assert ("163_bigint_divmod_e", "contaminated") in [tuple(x) for x in rep["dropped"]]
    rust = next(r for r in rep["contrasts"] if r["contrast"] == "rust")
    assert rust["n"] == 6
