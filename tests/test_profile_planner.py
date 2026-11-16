"""evals/profile.py's budget planner (brief §1, §5.4), without a model.

Pinned: k is measured on L0's LIVE units only; L0 and L1 are never cut;
L2 is cut in the fixed order (stratified -> probes -> rust -> c) only as
far as the remaining time needs; the stratified sample keeps every L0
failure and a seeded third of the passes, deterministically; and a profile
run end to end (fake agent) writes three arm files a report can pair, with
budget_skip rows instead of failures once the deadline passes.
"""

import importlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


def _profile():
    spec = importlib.util.spec_from_file_location("evals_profile", ROOT / "evals" / "profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _profile()
CUTS = ["stratified_l2", "drop_probes_l2", "drop_rust_l2", "drop_c_l2"]


def _units():
    """6 zig (L0/L1/L2), 6 each go/c/rust (L0/L2), 6 python (L0 only),
    2 zig probes; 100 R-s each."""
    u = {}
    for lang in ("zig", "go", "c", "rust", "python"):
        for i in range(6):
            arms = ["L0"] + (["L1"] if lang == "zig" else []) + \
                (["L2"] if lang != "python" else [])
            u[f"{lang}{i}"] = {"lang": lang, "arms": arms, "est_s": 100, "role": "scored"}
    for i in range(2):
        u[f"probe{i}"] = {"lang": "zig", "arms": ["L0", "L1", "L2"], "est_s": 100,
                          "role": "probe"}
    return u


def _l0(units, fail=()):
    return [{"id": u, "passed": u not in fail} for u in units]


def test_k_counts_live_units_only():
    rows = [{"id": "a"}, {"id": "b", "from_cache": True}, {"id": "c", "reason": "budget_skip"}]
    assert P.measured_k(rows, {"a": 100, "b": 100, "c": 100}, 50.0) == 0.5
    assert P.measured_k([{"id": "b", "from_cache": True}], {"b": 100}, 50.0) is None


def test_everything_fits_means_no_cuts():
    u = _units()
    plan = P.plan_arms(u, _l0(u), k=1.0, remaining_s=10_000, cut_order=CUTS, frac=1 / 3,
                       seed="s")
    assert plan["cuts"] == [] and plan["fits"]
    assert len(plan["l1"]) == 8 and len(plan["l2"]) == 26     # 6 zig + 18 + 2 probes


def test_cuts_apply_in_order_and_only_as_far_as_needed():
    u = _units()
    fail = {"zig0", "go0", "c0", "rust0"}
    l0 = _l0(u, fail)
    # L1 800 s; full L2 2600 s. Budget 2000 s: stratified L2 ~ (4 fails +
    # third of 22 passes = 7) ... fits after the first cut.
    plan = P.plan_arms(u, l0, 1.0, 2000, CUTS, 1 / 3, "seed")
    assert plan["cuts"] == ["stratified_l2"] and plan["fits"]
    assert fail <= plan["l2"]                                   # every L0 failure reruns
    assert all(plan["pi"][x] == 1.0 for x in fail)
    sampled_passes = [x for x, p in plan["pi"].items() if p < 1]
    assert len(sampled_passes) == round(22 / 3)
    assert len(plan["l1"]) == 8                                  # L1 never cut
    # Tighter: probes, then rust, then c go too.
    tight = P.plan_arms(u, l0, 1.0, 800 + 300, CUTS, 1 / 3, "seed")
    assert tight["cuts"][:2] == ["stratified_l2", "drop_probes_l2"]
    assert not any(x.startswith("probe") for x in tight["l2"])
    assert len(tight["l1"]) == 8
    tighter = P.plan_arms(u, l0, 1.0, 800, CUTS, 1 / 3, "seed")
    assert tighter["cuts"] == CUTS and not tighter["fits"]
    assert not any(x.startswith(("rust", "c")) for x in tighter["l2"])
    assert any(x.startswith("zig") for x in tighter["l2"])       # zig > go > c > rust


def test_stratified_sample_is_seeded():
    u = _units()
    l0 = _l0(u)
    a = P.stratified_sample(l0, set(u), 1 / 3, "pair-1")
    b = P.stratified_sample(l0, set(u), 1 / 3, "pair-1")
    c = P.stratified_sample(l0, set(u), 1 / 3, "pair-2")
    assert a == b and a != c


def test_stratified_pi_is_the_true_inclusion_probability():
    """pi feeds the Horvitz-Thompson weights in ablation.py, so it must be
    k/n for the draw actually made, not the nominal frac (review 1116)."""
    for n, k in ((4, 1), (5, 2), (250, 83)):
        ids = [f"u{i}" for i in range(n)]
        rows = [{"id": u, "passed": True} for u in ids]
        pi = P.stratified_sample(rows, set(ids), 1 / 3, "s")
        sampled = [u for u, p in pi.items() if p < 1]
        assert len(sampled) == k
        assert all(abs(pi[u] - k / n) < 1e-12 for u in sampled), (n, pi)
    # unbiased: the HT total of an all-pass set recovers n exactly
    ids = [f"u{i}" for i in range(4)]
    pi = P.stratified_sample([{"id": u, "passed": True} for u in ids], set(ids), 1 / 3, "x")
    assert sum(1 / p for p in pi.values()) == 4


def test_a_profile_end_to_end_with_a_fake_agent(tmp_path, monkeypatch):
    tasks_dir, suites, results = tmp_path / "tasks", tmp_path / "suites", tmp_path / "results"
    for d in (tasks_dir, suites, results):
        d.mkdir()
    (tasks_dir / "801_t.json").write_text(json.dumps({
        "id": "801_t", "difficulty": "hard", "category": "Algorithms & DS", "task": "t",
        "max_iter": 5, "validation": {"type": "bash", "script": "true"},
        "variants": [{"id": "a", "language": "python"}, {"id": "b", "language": "go"}]}))
    man = {"suite": "minip", "format": 1, "board_arm": "L0",
           "arms": {"L0": {}, "L1": {"packs": False}, "L2": {"diag": True}},
           "cut_order": CUTS, "stratified_pass_fraction": 1 / 3, "max_iter_ceiling": 30,
           "units": [
               {"id": "801_t_a", "task": 801, "lang": "python", "category": "Algorithms & DS",
                "tier": "hard", "role": "scored", "est_s": 10, "max_wall_s": 900,
                "arms": ["L0"], "stratum": None},
               {"id": "801_t_b", "task": 801, "lang": "go", "category": "Algorithms & DS",
                "tier": "hard", "role": "scored", "est_s": 10, "max_wall_s": 900,
                "arms": ["L0", "L2"], "stratum": None}]}
    (suites / "minip.json").write_text(json.dumps(man))
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    run_eval = importlib.import_module("run_eval")
    run_eval.TASKS_DIR, run_eval.SUITES_DIR, run_eval.RESULTS_DIR = \
        str(tasks_dir), str(suites), str(results)
    import suite_manifest
    monkeypatch.setattr(suite_manifest, "SUITES_DIR", str(suites))
    monkeypatch.setenv("OPENBEAST_EVAL_ENV_ERA", "1")
    run_eval.run_agent = lambda task, *a, **kw: {
        "exit_code": 0, "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
        "tokens": {"prompt": 1, "completion": 2, "total": 3}}
    monkeypatch.setattr(run_eval, "detect_model", lambda url: "fake")

    class A:
        model_name, model, base_url, api_key_env = "fake", None, "http://127.0.0.1:9/v1", None
        suite, budget_h, jobs, slots, reasoning_budget = "minip", 1.0, 1, None, "4096"
        k_default, resume, fresh_deadline, plan_only = 1.0, None, False, False
        no_cache, allow_missing, allow_uncapped, skip_preflight = False, False, False, True
    assert P.run_profile(A, run_eval) == 0
    (state,) = list(results.glob("profile-fake-*.json"))
    st = json.loads(state.read_text())
    assert st["phase"] == "done" and set(st["results"]) == {"L0", "L1", "L2"}
    l2 = json.loads(Path(st["results"]["L2"]).read_text())
    rows = {t["id"]: t for t in l2["tasks"]}
    assert rows["801_t_a"].get("shared_from") == "L0"            # python: same request
    assert l2["harness"]["pair_id"] == st["pair_id"]
    # Resume after completion re-plans from the saved L0 and runs nothing new.
    A.resume = str(state)
    assert P.run_profile(A, run_eval) == 0
