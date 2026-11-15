"""The v5 suite contract (brief §3-§6): the manifest's shape and numbers,
unit resolution against task files, the zig tutoring ban, the harness-
aligned audit, JSON-declared refs, and an end-to-end manifest run through
run_eval (roles recorded, probes unscored, wall caps and the max_iter
ceiling applied).

Task files for the new bases (163+) are written separately from the
manifest; tests that need them skip with the list of what is missing,
and every unit that IS present is held to the rules.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "tests"))

import suite_manifest as M  # noqa: E402


@pytest.fixture(scope="module")
def man():
    return M.load_manifest("v5")


@pytest.fixture(scope="module")
def resolved():
    import run_eval
    tasks = run_eval.load_tasks(None)
    m = M.load_manifest("v5", tasks=tasks, strict=False)
    return m, {t["id"]: t for t in tasks}


# --- the manifest is the brief's unit list -----------------------------------

def test_manifest_numbers_match_the_brief(man):
    scored = M.scored_units(man)
    probes = [u for u in man["units"] if u["role"] == "probe"]
    assert len(scored) == 111 and len(probes) == 7
    assert len({u["task"] for u in scored}) == 39
    assert sum(u["src"] == "v4" for u in scored) == 50
    assert sum(u["src"] == "new" for u in scored) == 61
    assert sorted({u["task"] for u in scored if u["src"] == "new"}) == [
        163, 164, 165, 166, 167, 168, 169, 170, 171, 173, 175, 176, 178, 179, 180, 181,
        183, 186, 188, 190, 192, 195, 196]
    by_lang = {lang: sum(u["lang"] == lang for u in scored) for lang in M.SIX_LANGS}
    assert by_lang == {"python": 35, "go": 15, "c": 15, "cpp": 15, "rust": 15, "zig": 15}
    assert sum(u["lang"] == "shell" for u in scored) == 1


def test_arms_and_strata(man):
    assert man["arms"] == {"L0": {}, "L1": {"packs": True},
                           "L2": {"packs": True, "diag": True, "escalate": True}}
    assert all("L0" in u["arms"] for u in man["units"])
    l1 = M.arm_units(man, "L1")
    assert len(l1) == 22 and all(u["lang"] == "zig" for u in l1)       # 15 zig + 7 probes
    l2 = M.arm_units(man, "L2")
    assert len(l2) == 83 and not any(u["lang"] == "python" for u in l2)  # 76 + 7 probes
    held = [u for u in man["units"] if u["stratum"] == "heldout"]
    ins = [u for u in man["units"] if u["stratum"] == "insample"]
    assert sorted(u["task"] for u in held) == [163, 164, 165, 166, 167, 168, 169, 170]
    assert len(ins) == 14 and sum(u["role"] == "probe" for u in ins) == 7


def test_budget_matches_the_brief(man):
    b = man["budget"]
    assert b["ref_seq_h"] == 6.58 and b["wall_h"] == 4.0
    assert b["ref_seq_h_by_arm"] == {"L0": 3.69, "L1": 0.57, "L2": 2.32}
    hours = {a: round(sum(u["est_s"] for u in M.arm_units(man, a)) / 3600, 2)
             for a in ("L0", "L1", "L2")}
    assert hours == b["ref_seq_h_by_arm"]
    caps = {u["tier"]: u["max_wall_s"] for u in man["units"]}
    assert caps == {"easy": 300, "medium": 600, "hard": 900, "xl": 1500}


def test_resolution_by_task_number_and_language():
    tasks = [{"id": "163_some_slug_f", "base_id": "163_some_slug", "language": "zig"},
             {"id": "163_some_slug_a", "base_id": "163_some_slug", "language": "python"},
             {"id": "171_pipeline", "language": "go"},
             {"id": "179_bisect", "language": "bash"}]
    m = {"units": [{"id": "163_bigint_divmod_knuthD_f", "task": 163, "lang": "zig"},
                   {"id": "171_go_pipeline_race_deadlock", "task": 171, "lang": "go"},
                   {"id": "179_git_bisect_minimal_revert", "task": 179, "lang": "shell"},
                   {"id": "999_gone", "task": 999, "lang": "c"}]}
    out, problems = M.resolve_units(m, tasks)
    assert out == {"163_bigint_divmod_knuthD_f": "163_some_slug_f",
                   "171_go_pipeline_race_deadlock": "171_pipeline",
                   "179_git_bisect_minimal_revert": "179_bisect"}
    assert problems == ["999_gone: no task file provides task 999 in c"]


def test_present_units_agree_with_their_task_files(resolved):
    m, tasks = resolved
    import scoring
    meta = scoring._task_meta()
    bad = []
    for u in m["units"]:
        eff = m["resolved"].get(u["id"])
        if not eff:
            continue
        t = tasks[eff]
        cat = (meta.get(t.get("base_id") or t["id"]) or (None,))[0]
        if cat != u["category"]:
            bad.append(f"{u['id']}: category {cat!r} != manifest {u['category']!r}")
        if M.norm_lang(t.get("language")) != M.norm_lang(u["lang"]):
            bad.append(f"{u['id']}: language {t.get('language')} != {u['lang']}")
    assert not bad, bad


def test_every_manifest_unit_has_a_task_file(resolved):
    m, _ = resolved
    missing = m["resolution_problems"]
    if missing:
        pytest.skip(f"{len(missing)} v5 unit(s) have no task file in this checkout yet "
                    f"(written by the task authors): {missing[:4]}")


# --- authoring gate ----------------------------------------------------------

def test_v5_zig_prompts_teach_no_api(resolved):
    """The ban is enforced on every v5 zig unit that has been upgraded
    (declares its refs); units still carrying v4 text are listed."""
    m, tasks = resolved
    errors, pending = [], []
    for u in m["units"]:
        eff = m["resolved"].get(u["id"])
        if not eff or u["lang"] != "zig":
            continue
        hit = M.zig_tutoring_violations(tasks[eff].get("task", ""))
        if hit and tasks[eff].get("ref_stem"):
            errors.append((eff, hit))
        elif hit:
            pending.append(eff)
    assert not errors, errors
    if pending:
        pytest.skip(f"{len(pending)} retained zig unit(s) not yet upgraded: {pending}")


def test_ban_lint_flags_the_v4_recipe():
    assert M.zig_tutoring_violations("var fr = ...; const r = &fr.interface; init.io") == \
        ["&fr.interface", "init.io"]
    assert M.zig_tutoring_violations("Write /tmp/eval_x/x.zig for Zig 0.16; "
                                     "build: zig build-exe -O ReleaseFast x.zig") == []


def test_audit_timeouts_match_the_harness():
    import audit_variants as av
    import run_eval
    assert av.VALIDATION_TIMEOUT_S == run_eval.VALIDATION_TIMEOUT_S == 30
    src = (ROOT / "tests" / "audit_variants.py").read_text()
    assert "timeout=120" not in src
    assert "SPEC_REQUIREMENTS" not in src          # the v4 rule REQUIRED the tutoring


def test_refs_are_declared_in_the_task_json(tmp_path):
    import audit_variants as av
    t = {"id": "163_x_c", "base_id": "163_x", "language": "c",
         "ref_stem": "163_x/ref", "dest": "/tmp/eval_x/x.c"}
    ref, dest = av.ref_target(t)
    assert ref == str(av.REFS / "163_x" / "ref.c") and dest == "/tmp/eval_x/x.c"
    # the legacy hand map only for v4 tasks that declare nothing
    legacy = av.ref_target({"id": "92_popcount_c", "base_id": "92_popcount", "language": "c"})
    assert legacy == (str(av.REFS / "pop.c"), "/tmp/eval_pop/pop.c")
    assert av.ref_target({"id": "999_new_c", "language": "c"}) is None


def test_load_tasks_passes_optional_v5_fields_through(tmp_path, monkeypatch):
    import run_eval
    (tmp_path / "500_t.json").write_text(json.dumps({
        "id": "500_t", "difficulty": "hard", "task": "t", "est_s": 165, "max_wall_s": 900,
        "role": "scored", "validation": {"type": "bash", "script": "true"},
        "variants": [{"id": "a", "language": "python", "difficulty": "medium"},
                     {"id": "f", "language": "zig", "role": "probe"}]}))
    monkeypatch.setattr(run_eval, "TASKS_DIR", str(tmp_path))
    a, f = run_eval.load_tasks(None)
    assert (a["est_s"], a["max_wall_s"], a["role"], a["difficulty"]) == (165, 900, "scored", "medium")
    assert (f["role"], f["difficulty"]) == ("probe", "hard")


# --- an end-to-end manifest run -------------------------------------------------

def _mini_world(tmp_path):
    tasks_dir, suites, results = tmp_path / "tasks", tmp_path / "suites", tmp_path / "results"
    for d in (tasks_dir, suites, results):
        d.mkdir()
    (tasks_dir / "801_alpha.json").write_text(json.dumps({
        "id": "801_alpha", "difficulty": "hard", "category": "Algorithms & DS", "task": "a",
        "max_iter": 40, "validation": {"type": "bash", "script": "true"},
        "variants": [{"id": "a", "language": "python"}, {"id": "f", "language": "zig"}]}))
    (tasks_dir / "802_probe.json").write_text(json.dumps({
        "id": "802_probe", "difficulty": "easy", "category": "Algorithms & DS", "task": "p",
        "max_iter": 10, "validation": {"type": "bash", "script": "false"},
        "variants": [{"id": "f", "language": "zig"}]}))
    man = {"suite": "mini", "format": 1, "arms": {"L0": {}}, "board_arm": "L0",
           "coverage_floor": None, "max_iter_ceiling": 30,
           "units": [
               {"id": "801_whatever_a", "task": 801, "lang": "python", "category": "Algorithms & DS",
                "tier": "hard", "role": "scored", "est_s": 165, "max_wall_s": 900,
                "arms": ["L0"], "stratum": None},
               {"id": "801_whatever_f", "task": 801, "lang": "zig", "category": "Algorithms & DS",
                "tier": "hard", "role": "scored", "est_s": 165, "max_wall_s": 900,
                "arms": ["L0"], "stratum": "heldout"},
               {"id": "802_probe_f", "task": 802, "lang": "zig", "category": "Algorithms & DS",
                "tier": "easy", "role": "probe", "est_s": 22.4, "max_wall_s": 300,
                "arms": ["L0"], "stratum": "insample"}]}
    (suites / "mini.json").write_text(json.dumps(man))
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    run_eval = importlib.import_module("run_eval")
    run_eval.TASKS_DIR, run_eval.SUITES_DIR, run_eval.RESULTS_DIR = \
        str(tasks_dir), str(suites), str(results)
    return run_eval, suites


def test_a_manifest_run_records_roles_and_applies_caps(tmp_path, monkeypatch):
    run_eval, suites = _mini_world(tmp_path)
    monkeypatch.setattr(M, "SUITES_DIR", str(suites))
    seen = {}

    def agent(task, base_url, max_iter_override=None, timeout_scale=1.0,
              env_overlay=None, wall_s=None):
        seen[task["id"]] = (max_iter_override, wall_s)
        return {"exit_code": 0, "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
                "tokens": {"prompt": 1, "completion": 2, "total": 3}}
    run_eval.run_agent = agent
    out = run_eval.run_eval(model_name="m", use_cache=False, suite="mini")
    rows = {t["id"]: t for t in out["tasks"]}
    assert set(rows) == {"801_alpha_a", "801_alpha_f", "802_probe_f"}
    assert rows["802_probe_f"]["role"] == "probe" and rows["801_alpha_f"]["stratum"] == "heldout"
    assert seen["801_alpha_a"] == (30, 900.0)          # max_iter 40 clamped; wall cap
    assert seen["802_probe_f"] == (10, 300.0)
    assert out["suite_manifest"] == "mini" and out["suite_selection"] is None
    assert "fast_suite" not in out                     # never imputed
    import scoring
    entry = scoring.score_run(dict(out, suite_version="v5"))
    assert entry["tasks_total"] == 2 and entry["probe_units"] == 1
