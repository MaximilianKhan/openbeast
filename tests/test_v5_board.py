#!/usr/bin/env python3
"""evals/v5_board.py: the v5 leaderboard (best configuration per model).

  - each model shows once per exact weights, with its best configuration by
    SCORE; two quantizations of one model are two rows
  - every run names a weights.json entry, which is validated and rendered
  - a repair run supersedes infrastructure rows and never a real verdict
  - a reference ledger is marked as a ceiling and never ranked
  - every run's token usage is totalled, per unit and per pass, with the
    units that recorded none counted
  - rendering is idempotent, and the committed docs match the generator
    (the CI drift gate)

Run: pytest tests/test_v5_board.py
"""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "evals"))

import v5_board

# A three-unit manifest: one two-language base (python + go) and one singleton.
MANIFEST = {
    "1_a_py": {"base": "task-1", "lang": "python", "difficulty": "hard", "k": 2},
    "1_a_go": {"base": "task-1", "lang": "go", "difficulty": "hard", "k": 2},
    "2_b": {"base": "task-2", "lang": "python", "difficulty": "medium", "k": 1},
}
CNFP = "Could not find platform independent libraries <prefix>"


def wentry(model, label, **kw):
    w = {"model": model, "label": label, "repo": f"org/{model.replace(' ', '-')}",
         "revision": "a" * 40, "revision_kind": "hub", "format": "EXL3", "bpw": 2.9,
         "variant": "v1", "abliterated": False, "engine": "TensorFold", "engine_version": "x",
         "drafter": None, "kv_dtype": "fp8", "hosts": "2× DGX Spark", "evidence": "test"}
    w.update(kw)
    return w


WEIGHTS = {
    "m-29": wentry("Model M", "EXL3 2.9 bpw · v1 · stock"),
    "n-4": wentry("Model N", "EXL3 4 bpw · ablit · abliterated", bpw=4, abliterated=True),
    "ref": wentry("Ref Model", "hosted", repo=None, revision=None, revision_kind="hosted",
                  format="hosted API", bpw=None),
}


def row(uid, passed, **kw):
    r = {"id": uid, "passed": passed, "role": "scored", "elapsed_seconds": 60.0,
         "tokens_completion": 100, "agent_exit_code": 0, "validation_output": "ok" if passed else "FAIL"}
    r.update(kw)
    return r


def results(tasks, ts="2026-11-16T10:00:00", **header):
    return {"timestamp": ts, "suite_version": "v5", "harness": {"arm": "L0", "board_arm": "L0"},
            "tasks": tasks, **header}


def write(d, name, obj):
    path = os.path.join(d, name)
    with open(path, "w") as f:
        if name.endswith(".jsonl"):
            f.write("\n".join(json.dumps(r) for r in obj) + "\n")
        else:
            json.dump(obj, f)
    return name


def board(tmp_path):
    d = str(tmp_path)
    runs = [
        {"id": "M-weak", "model": "Model M", "weights_id": "m-29", "config": "bare",
         "files": [write(d, "m1.json", results([row("1_a_py", True), row("1_a_go", False),
                                                 row("2_b", False)]))]},
        {"id": "M-strong", "model": "Model M", "weights_id": "m-29", "config": "thinking on",
         "files": [write(d, "m2.json", results([row("1_a_py", True), row("1_a_go", True),
                                                 row("2_b", True)], ts="2026-11-17T10:00:00",
                                                wall_scale=2.0))]},
        {"id": "N", "model": "Model N", "weights_id": "n-4", "config": "bare",
         "files": [write(d, "n.json", results([row("1_a_py", True), row("1_a_go", False),
                                                row("2_b", True)]))]},
        {"id": "REF", "model": "Ref Model", "weights_id": "ref", "config": "other scaffold", "reference": True,
         "files": [write(d, "ref.jsonl", [
             {"unit": u, "passed": True, "indeterminate": False, "elapsed_s": 30.0,
              "over_budget": u == "2_b", "graded_at": 1794860750.0} for u in MANIFEST])]},
    ]
    return {"runs": runs, "not_yet_run": ["Model Q"]}


def test_best_configuration_per_model(tmp_path):
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST, weights=WEIGHTS)
    best = {r["model"]: r for r in b["best"]}
    assert len(b["best"]) == 3                    # one row per model, not per run
    assert best["Model M"]["id"] == "M-strong"
    assert best["Model M"]["passed"] == 3 and best["Model M"]["score"] == 100.0
    assert best["Model M"]["experiment"] == ["wall ×2"]
    assert best["Model N"]["experiment"] == []
    # ranked: M (100) above N, reference last whatever its score
    assert [r["model"] for r in b["best"]] == ["Model M", "Model N", "Ref Model"]
    assert best["Model N"]["by_lang"] == {**{k: (0, 0) for k, _ in v5_board.LANGS},
                                          "python": (2, 2), "go": (0, 1)}


def test_repair_supersedes_infra_rows_only(tmp_path):
    d = str(tmp_path)
    main = write(d, "main.json", results([
        row("1_a_py", False, tokens_completion=0, elapsed_seconds=0.0, agent_exit_code=1,
            validation_output=CNFP),                         # agent never started
        row("1_a_go", False, validation_output=CNFP),         # validator never started
        row("2_b", False),                                    # a real fail
    ]))
    repair = write(d, "repair.json", results([row("1_a_py", True), row("1_a_go", True),
                                              row("2_b", True)], ts="2026-11-16T12:00:00"))
    _, rows = v5_board.merge_runs([os.path.join(d, main), os.path.join(d, repair)])
    assert rows["1_a_py"]["passed"] and rows["1_a_go"]["passed"]
    assert not rows["2_b"]["passed"]                         # never re-rolled
    # ...and an infra row arriving later never replaces a real verdict
    late = write(d, "late.json", results([row("2_b", False, reason="server_error")]))
    _, rows = v5_board.merge_runs([os.path.join(d, repair), os.path.join(d, late)])
    assert rows["2_b"]["passed"]


def test_unrepaired_lost_unit_counts_as_fail(tmp_path):
    d = str(tmp_path)
    f = write(d, "r.json", results([row("1_a_py", True),
                                    row("1_a_go", False, reason="validator_timeout")]))
    r = v5_board.score_run({"id": "x", "model": "m", "config": "c", "files": [f]},
                           MANIFEST, base_dir=d)
    assert r["passed"] == 1 and r["units"] == 3
    assert r["lost"] == {"1_a_go": "validator_timeout", "2_b": "missing"}
    assert "2 units lost" in v5_board._status(r)


def test_reference_is_marked_and_unranked(tmp_path):
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST, weights=WEIGHTS)
    ref = b["best"][-1]
    assert ref["reference"] and ref["passed"] == 3 and ref["passed_strict_wall"] == 2
    text = v5_board.render_board(b)
    lines = [ln for ln in text.splitlines() if ln.startswith("| ")]
    assert lines[1].startswith("| 1 | **Model M**") and lines[2].startswith("| 2 | **Model N**")
    assert lines[3].startswith("| ref | *Ref Model* (reference)")
    assert "reference ceiling, not ranked" in lines[3]
    assert lines[4].startswith("| — | Model Q |  |  | not yet run on v5")
    # every row has the header's column count
    assert len({ln.count("|") for ln in lines}) == 1
    assert "2/3 at the 1× wall" in v5_board.render_runs(b)


def test_token_stats(tmp_path):
    d = str(tmp_path)
    f = write(d, "t.json", results([
        row("1_a_py", True, tokens_prompt=9000, tokens_completion=1000),
        row("1_a_go", False, tokens_prompt=3000, tokens_completion=500, stats_partial=True),
        row("2_b", True, tokens_prompt=0, tokens_completion=0),      # wall-killed, no usage
    ]))
    r = v5_board.score_run({"id": "x", "model": "m", "config": "c", "files": [f]}, MANIFEST, base_dir=d)
    assert r["tokens"] == {"prompt": 12000, "completion": 1500, "total": 13500, "units_with_data": 2,
                           "missing": 1, "partial": 1, "per_unit": 6750, "per_pass": 6750}
    # no passes: no per-pass figure; a reference ledger has no usage at all
    assert v5_board.token_stats({}, MANIFEST, 0)["per_pass"] is None
    # an exact .5 rounds half up, as the website's Math.round re-derives it
    assert v5_board._round_half_up(1000025, 50) == 20001
    assert v5_board._round_half_up(5, 2) == 3 and v5_board._round_half_up(4, 2) == 2
    b = v5_board.build(board(tmp_path), base_dir=d, manifest=MANIFEST, weights=WEIGHTS)
    ref = {r["id"]: r for r in b["runs"]}["REF"]
    assert ref["tokens"]["missing"] == 3 and ref["tokens"]["total"] == 0
    text = v5_board.render_tokens(b)
    assert "| Ref Model | REF | — | — | — | 0/3 | — | 3/3 | — | 3 | 0 |" in text
    assert "| Model M | M-strong | 0 | 300 | 300 | 3/3 | 100 | 3/3 | 100 | 0 | 0 |" in text
    assert "Completion includes thinking" in text and "re-count" in text


def test_render_is_idempotent(tmp_path):
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST, weights=WEIGHTS)
    doc = "intro\n<!-- v5-board:start -->\nold\n<!-- v5-board:end -->\ntail\n"
    once = v5_board.splice(doc, "v5-board", v5_board.render_board(b))
    twice = v5_board.splice(once, "v5-board", v5_board.render_board(b))
    assert once == twice and once.startswith("intro\n") and once.endswith("tail\n")
    assert v5_board.splice("no markers", "v5-board", "x") is None


def test_update_docs_detects_and_fixes_drift(tmp_path):
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST, weights=WEIGHTS)
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    for rel in v5_board.DOCS:
        (root / rel).write_text("x\n<!-- v5-board:start -->\n<!-- v5-board:end -->\n")
    assert "docs/RESULTS.md: no v5-weights block" in " ".join(v5_board.update_docs(b, write=False, root=str(root)))
    (root / "docs" / "RESULTS.md").write_text("x\n<!-- v5-board:start -->\n<!-- v5-board:end -->\n"
                                              "<!-- v5-weights:start -->\n<!-- v5-weights:end -->\n")
    assert "docs/RESULTS.md: no v5-tokens block" in " ".join(v5_board.update_docs(b, write=False, root=str(root)))
    (root / "docs" / "RESULTS.md").write_text("x\n<!-- v5-board:start -->\n<!-- v5-board:end -->\n"
                                              "<!-- v5-tokens:start -->\n<!-- v5-tokens:end -->\n"
                                              "<!-- v5-weights:start -->\n<!-- v5-weights:end -->\n")
    assert sorted(v5_board.update_docs(b, write=True, root=str(root))) == sorted(v5_board.DOCS)
    assert v5_board.update_docs(b, write=False, root=str(root)) == []
    # links are relative to each doc
    assert "](evals/boards/v5/m2.json)" in (root / "README.md").read_text()
    assert "](../evals/boards/v5/m2.json)" in (root / "docs" / "RESULTS.md").read_text()
    assert "**Model N · EXL3 4 bpw · ablit · abliterated** (`n-4`)" in (root / "docs" / "RESULTS.md").read_text()


def test_add_run_copies_and_replaces(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    bdir = tmp_path / "board"
    bdir.mkdir()
    f = write(str(src), "eval-x.json", results([row("2_b", True)]))
    b = {"runs": [{"id": "X", "model": "m", "config": "old", "files": []}]}
    v5_board.add_run(b, "X", "m", "new", [str(src / f)], campaign_arm="arm E",
                     board_dir=str(bdir), weights_id="m-29")
    assert (bdir / "eval-x.json").exists()
    assert b["runs"] == [{"id": "X", "model": "m", "config": "new", "files": ["eval-x.json"],
                          "weights_id": "m-29", "campaign_arm": "arm E"}]


def test_exclusions_count_as_not_passed_and_footnote(tmp_path):
    excl = {"exclusions": [{"unit": "1_a_go", "run": "M-strong", "reason": "pending integrity review"},
                           {"unit": "2_b", "file": "n.json", "reason": "contaminated"}],
            "validator_notes": [{"unit": "2_b", "run": "M-weak", "note": "a validator false FAIL"}]}
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST, exclusions=excl,
                       weights=WEIGHTS)
    runs = {r["id"]: r for r in b["runs"]}
    assert runs["M-strong"]["passed"] == 2 and runs["M-strong"]["passed_raw"] == 3
    assert runs["M-strong"]["excluded"] == {"1_a_go": "pending integrity review"}
    assert runs["N"]["passed"] == 1 and runs["N"]["excluded"] == {"2_b": "contaminated"}
    assert runs["M-weak"]["passed"] == 1 and runs["M-weak"]["excluded"] == {}  # a note never adjusts
    assert runs["REF"]["excluded"] == {}                                    # run/file scoping
    text = v5_board.render_board(b)
    assert "**2/3**\\*" in text
    assert "\\* Model M, M-strong: 3/3 raw; 1 unit (1_a_go) excluded pending integrity review." in text
    runs_text = v5_board.render_runs(b)
    assert "2_b is a validator false FAIL; not adjusted" in runs_text
    # an absent exclusions file excludes nothing
    assert v5_board.load_exclusions(str(tmp_path / "none.json")) == {"exclusions": [], "validator_notes": []}


def test_committed_board_numbers():
    """The committed inputs reproduce the numbers the docs claim."""
    b = v5_board.build()
    runs = {r["id"]: r for r in b["runs"]}
    assert b["units"] == 71 and b["bases"] == 34
    assert {k: runs[k]["passed"] for k in runs} == {
        "GLM-L0": 26, "GLM-A": 31, "GLM-B": 36, "GLM-C": 51, "GLM-E": 50, "DS-T": 61, "DS-T2": 65, "DS-Ts1": 61, "DS-T3": 65,
        "DS-U": 59, "DS-UP": 62, "DS-UPA": 59, "DS-U8k": 63, "DS-UP8k": 65, "DS-UPA8k": 62, "HY4-T2": 60, "HY4-C": 55, "Q38U-L0": 39, "Q38U-T2": 51,
        "GLM-UP": 56, "INK-T2": 49, "INK-T2-S1": 51, "INK-T2-STOCK": 53, "INK-UP8K": 49, "INK-T3": 52, "INK-T2-SC117": 51, "OPUS-REF": 71}
    assert runs["GLM-C"]["passed_raw"] == 54  # 122 b/d/e excluded: contaminated (G25 scan)
    assert runs["GLM-E"]["passed_raw"] == 50  # scan clean; 122_c is a noted validator false FAIL
    assert runs["DS-T"]["passed_raw"] == 61  # scan clean (2026-11-18); first run under review 1118
    assert runs["DS-UP"]["passed_raw"] == 62  # main file (55 units) + resume file (16), scan clean 2026-11-21
    assert runs["HY4-T2"]["passed_raw"] == 60  # scan clean 2026-11-22; same harness9 tree as DS-T2
    assert runs["HY4-C"]["passed_raw"] == 55  # scan clean 2026-11-22; the ablation of HY4-T2, same tree
    assert round(runs["HY4-C"]["score"], 1) == 82.2  # HY4-T2 (83.4) stays Hy4's best row
    assert runs["Q38U-L0"]["passed_raw"] == 40  # 126 (a pass), 163_e, 192 excluded: the shell reached the network
    assert sorted(runs["Q38U-L0"]["excluded"]) == ["126_hmac_scratch", "163_bigint_divmod_e", "192_pike_regex"]
    assert runs["Q38U-T2"]["passed_raw"] == 54  # main file (71) + the r1 rerun of 7 lost units, harness9
    sc = runs["INK-T2-SC117"]  # SC117's abliterated pack, INK-T2-STOCK's arm exactly (2026-11-25)
    assert (sc["passed_raw"], round(sc["score"], 1), sc["lost"], sc["excluded"]) == (51, 75.3, {}, {})
    assert sc["weights_id"] == "inkling2-mini-sc117-abliterated-basalt-nvfp4-mx8"
    # 124 and 126 (passes) and 122_d (r1) excluded: the shell reached the network (scan 2026-11-23);
    # 123_nbody_b (a pass) and 168_c: package installs, found by the review-1124 rescan (2026-11-25)
    assert sorted(runs["Q38U-T2"]["excluded"]) == ["122_gemm_blocked_d", "123_nbody_b", "124_rkf45",
                                                   "126_hmac_scratch", "168_linearizability_kv_c"]
    assert list(runs["Q38U-T2"]["validator_notes"]) == ["122_gemm_blocked_d"]  # dlsym: verdict not measured
    assert round(runs["Q38U-T2"]["score"], 1) == 75.0
    # GLM-UP: one file, 0 lost; the first scan flagged 176_a (a fixture's own tests/), fixed: 0 contaminated
    assert (runs["GLM-UP"]["passed_raw"], runs["GLM-UP"]["excluded"]) == (56, {})
    assert round(runs["GLM-UP"]["score"], 1) == 81.9
    # INK-T2: one file, 0 lost, scan clean; 122_d stopped at the Linux dlsym check (replayed: PASS, not adjusted)
    assert (runs["INK-T2"]["passed_raw"], runs["INK-T2"]["excluded"]) == (49, {})
    assert list(runs["INK-T2"]["validator_notes"]) == ["122_gemm_blocked_d"]
    # SOLVE ties Q38U-T2 (72.31); LANG (89.09 vs 83.1) puts it above on SCORE with 2 fewer scored passes
    assert (round(runs["INK-T2"]["score"], 1), round(runs["INK-T2"]["solve"], 2)) == (76.5, round(runs["Q38U-T2"]["solve"], 2))
    # INK-T2-S1: INK-T2 under seed 1, a replicate (never ranked): +2 passes, the same SOLVE, LANG 96.4
    assert (runs["INK-T2-S1"]["passed_raw"], runs["INK-T2-S1"]["excluded"], runs["INK-T2-S1"]["replicate_of"]) == (51, {}, "INK-T2")
    assert (round(runs["INK-T2-S1"]["score"], 1), round(runs["INK-T2-S1"]["solve"], 2)) == (78.3, round(runs["INK-T2"]["solve"], 2))
    assert runs["DS-Ts1"]["replicate_of"] == "DS-T"
    # INK-T2-STOCK: Basalt's stock pack, its own (model, weights) row; scan clean, the same dlsym stop
    assert (runs["INK-T2-STOCK"]["passed_raw"], runs["INK-T2-STOCK"]["excluded"]) == (53, {})
    assert round(runs["INK-T2-STOCK"]["score"], 1) == 78.2
    for k in ("INK-T2-S1", "INK-T2-STOCK", "INK-UP8K", "INK-T3"):
        assert list(runs[k]["validator_notes"]) == ["122_gemm_blocked_d"]
    # INK-T3: I_T3 on the stock pack (turns + 16K cap + #632); main file + the campaign's rerun of its
    # 4 server_error rows, which overflow.json calls context overflows (Max's rule): counted fails that
    # the rerun never supersedes; below INK-T2-STOCK on SCORE, so the stock row stays shown and the default
    assert (runs["INK-T3"]["passed_raw"], runs["INK-T3"]["excluded"], runs["INK-T3"]["lost"]) == (52, {}, {})
    assert sorted(runs["INK-T3"]["overflow"]) == [
        "163_bigint_divmod_d", "176_intermittent_500s_a", "183_heston_little_trap", "192_pike_regex"]
    assert len(runs["INK-T3"]["files"]) == 2 and round(runs["INK-T3"]["score"], 1) == 77.4
    assert "INK-T3" not in {r["id"] for r in b["best"]}
    # INK-UP8K: UP_8k on the Derisked pack (harness10); 163_c excluded (a find / listed sealed
    # file names; a fail, so raw = scored); below INK-T2 on SCORE, so INK-T2 stays the pack's row
    assert (runs["INK-UP8K"]["passed_raw"], sorted(runs["INK-UP8K"]["excluded"])) == (49, ["163_bigint_divmod_c"])
    assert round(runs["INK-UP8K"]["score"], 1) == 75.1
    assert "INK-UP8K" not in {r["id"] for r in b["best"]} and "INK-T2" in {r["id"] for r in b["best"]}
    assert runs["OPUS-REF"]["passed_strict_wall"] == 68
    # the 5090's 4 units lost to llama-server's HTTP 500 on a truncated tool call
    assert runs["Q38U-L0"]["lost"] == {u: "server_error" for u in (
        "122_gemm_blocked_c", "122_gemm_blocked_d", "124_rkf45", "173_taskgroup_cancel")}
    # Q_T2: 7 lost in the main file, 4 recovered by the r1 rerun; the same 500 kept 3
    assert runs["Q38U-T2"]["lost"] == {u: "server_error" for u in (
        "168_linearizability_kv_f", "175_crdt_orset_rga", "190_autograd_mlp")}
    assert all(not r["lost"] for k, r in runs.items() if k not in ("Q38U-L0", "Q38U-T2"))
    best = {r["model"]: r["id"] for r in b["best"]}
    assert best == {"GLM-5.3-Flash": "GLM-UP", "DeepSeek-V4.1-Flash (Mia EXL3)": "DS-T2", "Claude Opus 5.5": "OPUS-REF",
                    "Hy4-flash (NVFP4)": "HY4-T2", "Qwen3.8 27B Uncensored MTP": "Q38U-T2",
                    "Inkling 2 Mini Derisked (Basalt NVFP4-MX8)": "INK-T2",
                    "Inkling 2 Mini (Basalt NVFP4-MX8)": "INK-T2-STOCK",
                    "Inkling 2 Mini SC117 abliterated (Basalt NVFP4-MX8)": "INK-T2-SC117"}
    # the ranking, by SCORE; the reference last and unranked
    assert [(r["id"], round(r["score"], 1)) for r in b["best"]] == [
        ("DS-T2", 94.9), ("HY4-T2", 83.4), ("GLM-UP", 81.9), ("INK-T2-STOCK", 78.2), ("INK-T2", 76.5),
        ("INK-T2-SC117", 75.3), ("Q38U-T2", 75.0), ("OPUS-REF", 100.0)]
    # INK-T2-S1 scores above INK-T2 (78.3) and still is not the Derisked row: a replicate never ranks
    assert "INK-T2-S1" not in [r["id"] for r in b["best"]]
    assert b["not_yet_run"] == ["Qwen3.6 27B (v4 #1)"]
    # every run names its exact weights; one weights entry per model today
    ds, glm = "dsv41f-mia-exl3-2.9bpw-eaq1.1-v2", "glm53f-mia-exl3-4bpw-tf-ablit"
    hy4, q38 = "hy4-flash-nvfp4-atakans", "qwen38-27b-uncensored-mtp-q5km"
    ink, inks = "inkling2-mini-blackfrost-derisked-basalt-nvfp4-mx8", "inkling2-mini-basalt-nvfp4-mx8-stock"
    insc = "inkling2-mini-sc117-abliterated-basalt-nvfp4-mx8"
    assert {k: runs[k]["weights_id"] for k in runs} == {
        **{k: ds for k in runs if k.startswith("DS-")}, **{k: glm for k in runs if k.startswith("GLM-")},
        "HY4-T2": hy4, "HY4-C": hy4, "Q38U-L0": q38, "Q38U-T2": q38, "INK-T2": ink, "INK-T2-S1": ink,
        "INK-T2-STOCK": inks, "INK-UP8K": ink, "INK-T3": inks, "INK-T2-SC117": insc,
        "OPUS-REF": "anthropic-api-claude-opus-5-5"}
    assert [r["weights_id"] for r in b["best"]] == [ds, hy4, glm, inks, ink, insc, q38, "anthropic-api-claude-opus-5-5"]
    sc = b["weights"][insc]   # built here: no Hub repo, pinned by file sha256, derived from SC117's pack + the release
    assert (sc["repo"], sc["revision_kind"], sc["abliterated"], sc["file_sha256"][:8], sc["derived_from"]["repo"]) == (
        None, "file-sha256", True, "6eaa50d3", "SC117/Inkling-2-Mini-abliterated-Strata")
    assert sc["base_model"]["revision"] == b["weights"][inks]["derived_from"]["revision"] == "b85c3cfa615fc413cc9af3379f491b963f3b586a"
    w = b["weights"][ds]
    assert (w["format"], w["bpw"], w["abliterated"], w["revision_kind"]) == ("EXL3", 2.9, False, "local")
    assert w["revision"] == "04c13f12b83d57487912d87d0f1677b076810954" and w["repo"] is None
    g = b["weights"][glm]
    assert (g["repo"], g["revision"], g["bpw"], g["abliterated"]) == (
        "Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit", "57edefd2f5d9b371c8345883304d5af68b52fa24", 4, True)
    h = b["weights"][hy4]
    assert (h["repo"], h["revision"], h["format"], h["abliterated"]) == (
        "atakans/Hy4-flash-NVFP4", "30f201acf0b4f9ba178817bcbc04ccfa3ec473f9", "NVFP4", False)
    q = b["weights"][q38]
    assert (q["revision_kind"], q["file_sha256"], q["abliterated"]) == (
        "file-sha256", "24780644a95f759a9aeeb228c3d852028f2fd40ce0b74d68134246ec4a959547", True)
    i = b["weights"][ink]  # converted on the rig: no Hub repo, pinned by the pack's sha256, with its source
    assert (i["revision_kind"], i["repo"], i["file_sha256"], i["abliterated"]) == (
        "file-sha256", None, "d2b01bcc1fd2dc51cbcdaecb512c56d7c5db75181fee2d9b6c944e76e217a427", True)
    assert i["derived_from"]["revision"] == "31ec2eddecfdf93c8626d9e4dfc0a2aca6070900"
    assert "400b411c58031d064812ea21a259b2381acdaa3e" in i["engine_version"]
    st = b["weights"][inks]  # Basalt's stock pack: on the Hub, pinned by its sha256 and the commit it came from
    assert (st["revision_kind"], st["repo"], st["revision"], st["file_sha256"], st["abliterated"]) == (
        "file-sha256", "jesdga/Inkling-2-Mini-Basalt", "c2afac3adb772e14d77b2ea2ac7068ad4460e932",
        "28ec70723feaa037942a02f55b9d5e898276f3e62a3b1fa083745f932a041b99", False)
    # the release revision, verified 2026-11-25: named in the pack's meta and rebuilt byte for byte from it
    assert st["base_model"]["revision"] == "b85c3cfa615fc413cc9af3379f491b963f3b586a" \
        and "400b411c58031d064812ea21a259b2381acdaa3e" in st["engine_version"]
    assert st["hub_url"] == ("https://huggingface.co/jesdga/Inkling-2-Mini-Basalt/blob/"
                             "c2afac3adb772e14d77b2ea2ac7068ad4460e932/Inkling-2-Mini-NVFP4-MX8.basalt")
    # token usage (prompt, completion, units without usage), exact
    assert {k: (runs[k]["tokens"]["prompt"], runs[k]["tokens"]["completion"], runs[k]["tokens"]["missing"])
            for k in runs} == {
        "GLM-L0": (16649273, 422241, 4), "GLM-A": (18725452, 448632, 2), "GLM-B": (55749046, 677770, 7),
        "GLM-C": (14274788, 1056366, 20), "GLM-E": (16568150, 1226455, 17),
        "DS-T": (16187228, 1232335, 16), "DS-T2": (28140397, 2636448, 0), "DS-Ts1": (18833187, 2868308, 0),
        "DS-T3": (38279928, 2260595, 0), "DS-U": (23588215, 2792411, 0), "DS-UP": (24624938, 2878231, 0),
        "DS-UPA": (28279120, 3264679, 0), "DS-U8k": (30429276, 2519544, 0), "DS-UP8k": (26876212, 2456141, 0),
        "DS-UPA8k": (27402927, 2520048, 0), "HY4-T2": (47921687, 1605430, 0), "HY4-C": (35144307, 1967131, 0),
        "Q38U-L0": (15413787, 3452557, 0), "Q38U-T2": (57838480, 4697822, 0), "GLM-UP": (17278440, 2144728, 0),
        "INK-T2": (70883493, 5367401, 0), "INK-T2-S1": (64747685, 4959068, 0),
        "INK-T2-STOCK": (64862866, 4900235, 0), "INK-UP8K": (73541527, 5300048, 0),
            "INK-T3": (203079166, 8478549, 0), "INK-T2-SC117": (62001879, 4367930, 0), "OPUS-REF": (0, 0, 71)}
    assert runs["DS-T2"]["tokens"]["per_pass"] == 473490 and runs["DS-T2"]["tokens"]["total"] == 30776845
    assert runs["HY4-T2"]["tokens"]["per_pass"] == 825452 and runs["HY4-T2"]["tokens"]["total"] == 49527117
    assert runs["HY4-C"]["tokens"]["per_pass"] == 674753 and runs["HY4-C"]["tokens"]["total"] == 37111438
    assert runs["Q38U-T2"]["tokens"]["per_pass"] == 1226202 and runs["Q38U-T2"]["tokens"]["total"] == 62536302
    assert runs["INK-T2"]["tokens"]["per_pass"] == 1556141 and runs["INK-T2"]["tokens"]["total"] == 76250894
    assert runs["INK-T2-S1"]["tokens"]["per_pass"] == 1366799 and runs["INK-T2-S1"]["tokens"]["total"] == 69706753
    assert runs["INK-T2-STOCK"]["tokens"]["per_pass"] == 1316285 and runs["INK-T2-STOCK"]["tokens"]["total"] == 69763101
    assert runs["INK-UP8K"]["tokens"]["per_pass"] == 1609012 and runs["INK-UP8K"]["tokens"]["total"] == 78841575


def test_two_quantizations_of_one_model_are_two_rows(tmp_path):
    """A higher-bpw build of Model M gets its own board row; it never replaces,
    merges with or hides the 2.9 bpw row, even when it scores lower."""
    bd = board(tmp_path)
    d = str(tmp_path)
    weights = {**WEIGHTS, "m-35": wentry("Model M", "EXL3 3.5 bpw · v1 · stock", bpw=3.5,
                                         revision="b" * 40, hosts="3× DGX Spark")}
    bd["runs"].append({"id": "M35", "model": "Model M", "weights_id": "m-35", "config": "bare",
                       "files": [write(d, "m35.json", results([row("1_a_py", True), row("1_a_go", True),
                                                               row("2_b", False)]))]})
    b = v5_board.build(bd, base_dir=d, manifest=MANIFEST, weights=weights)
    assert sorted((r["model"], r["weights_id"], r["id"]) for r in b["best"]) == [
        ("Model M", "m-29", "M-strong"), ("Model M", "m-35", "M35"),
        ("Model N", "n-4", "N"), ("Ref Model", "ref", "REF")]
    assert b["best"][0]["id"] == "M-strong" and b["best"][-1]["id"] == "REF"
    text = v5_board.render_board(b)
    assert "| 1 | **Model M** | EXL3 2.9 bpw · v1 · stock · [org/Model-M@aaaaaaaa](https://huggingface.co/org/Model-M/tree/" in text
    assert " | **Model M** | EXL3 3.5 bpw · v1 · stock · [org/Model-M@bbbbbbbb]" in text
    weights_md = v5_board.render_weights(b)
    assert "**Model M · EXL3 3.5 bpw · v1 · stock** (`m-35`)" in weights_md
    assert "| Board runs | M35 |" in weights_md and "| Board runs | M-weak, M-strong |" in weights_md


def test_a_seed_replicate_is_listed_but_never_ranked(tmp_path):
    """A seed repeat of a run (DS-Ts1, INK-T2-S1, 2026-11-24) names it with replicate_of: it is scored
    and listed with every run, and never chosen as the best row even when it scores higher, so the
    board never ranks the better of two draws of one setup."""
    bd = board(tmp_path)
    d = str(tmp_path)
    bd["runs"].append({"id": "M-weak-s1", "model": "Model M", "weights_id": "m-29", "config": "seed 1",
                       "replicate_of": "M-weak",
                       "files": [write(d, "mw1.json", results([row("1_a_py", True), row("1_a_go", True),
                                                               row("2_b", True)]))]})
    b = v5_board.build(bd, base_dir=d, manifest=MANIFEST, weights=WEIGHTS)
    runs = {r["id"]: r for r in b["runs"]}
    assert runs["M-weak-s1"]["passed"] == 3 and runs["M-weak-s1"]["replicate_of"] == "M-weak"
    assert {r["model"]: r["id"] for r in b["best"]}["Model M"] == "M-strong"   # not the 3/3 replicate
    assert "seed replicate of M-weak: never the ranked row" in v5_board.render_runs(b)
    # the replicate must name another run of the same model and weights, and an original
    for bad, why in (("nope", "is not another board run"), ("N", "needs the same model and weights"),
                     ("M-weak-s1", "is not another board run")):
        bd["runs"][-1]["replicate_of"] = bad
        assert any(why in p for p in v5_board.weights_problems(bd, WEIGHTS)), bad
    bd["runs"].append({**bd["runs"][-1], "id": "M-weak-s2", "replicate_of": "M-weak-s1"})
    bd["runs"][-2]["replicate_of"] = "M-weak"
    assert any("itself a replicate" in p for p in v5_board.weights_problems(bd, WEIGHTS))


def test_weights_are_validated(tmp_path):
    bd = board(tmp_path)
    d = str(tmp_path)
    bd["runs"][0].pop("weights_id")
    bd["runs"][2]["weights_id"] = "m-29"          # Model N on Model M's weights
    bad = {**WEIGHTS, "x": wentry("Model M", "EXL3 2.9 bpw · v1 · stock"),  # duplicate label
           "y": wentry("Model Y", "y", revision="04c13f12")}                 # short revision
    probs = v5_board.weights_problems(bd, bad)
    assert any("run M-weak: no weights_id" in p for p in probs)
    assert any("run N: weights m-29 belong to 'Model M'" in p for p in probs)
    assert any("two variants need two labels" in p for p in probs)
    assert any("weights y: a hub entry needs" in p for p in probs)
    try:
        v5_board.build(bd, base_dir=d, manifest=MANIFEST, weights=bad)
    except ValueError as e:
        assert "weights.json" in str(e)
    else:
        raise AssertionError("build accepted a run without weights")


def test_a_locally_built_file_is_pinned_by_sha256_with_its_source(tmp_path):
    """A pack converted on our own host (INK-T2's Basalt pack, 2026-11-24) is on no Hub repo: it is
    a file-sha256 entry with repo null, and must name what it was built from."""
    bd = board(tmp_path)
    d = str(tmp_path)
    sha = "d" * 64
    local = {**wentry("Model N", "pack · local · stock"), "repo": None, "revision": None,
             "revision_kind": "file-sha256", "file": "n.pack", "file_sha256": sha}
    probs = v5_board.weights_problems(bd, {**WEIGHTS, "n-local": local})
    assert any("n-local: a locally built file (repo null) needs derived_from" in p for p in probs)
    local["derived_from"] = {"repo": "org/Model-N-src", "revision": "c" * 40, "relation": "converted here"}
    assert not v5_board.weights_problems(bd, {**WEIGHTS, "n-local": local})
    bd["runs"][2]["weights_id"] = "n-local"
    b = v5_board.build(bd, base_dir=d, manifest=MANIFEST, weights={**WEIGHTS, "n-local": local})
    assert v5_board.hub_url(local) is None
    assert "pack · local · stock · [local file sha256 dddddddd](docs/RESULTS.md#weights-used)" in v5_board.render_board(b)
    md = v5_board.render_weights(b)
    assert f"local file `n.pack` (sha256 `{sha}`; built here, not on the Hub" in md
    assert "[`org/Model-N-src` @ `" + "c" * 40 + "`]" in md and "`None`" not in md


def test_shipped_defaults_are_validated_and_rendered(tmp_path):
    """board.json "defaults" (2026-11-24): the platform's shipped row, separate from the rank. It must
    name a real, ranked-kind run (no reference, no seed replicate), once per platform, dates in order."""
    bd = board(tmp_path)
    d = str(tmp_path)
    bd["runs"].append({"id": "M-weak-s1", "model": "Model M", "weights_id": "m-29", "config": "seed 1",
                       "replicate_of": "M-weak", "files": bd["runs"][0]["files"]})
    good = {"platform": "rig", "label": "1x GPU", "run": "M-weak", "agent_profile": "x-best", "since": "2026-11-24",
            "decision": "docs/d.md#1", "previous": [{"run": "N", "agent_profile": "y-best", "from": "2026-11-01",
                                                      "until": "2026-11-24", "role": "fallback"}]}
    bd["defaults"] = [good]
    assert v5_board.defaults_problems(bd) == []
    b = v5_board.build(bd, base_dir=d, manifest=MANIFEST, weights=WEIGHTS)
    md = v5_board.render_defaults(b)
    assert "| 1x GPU | **Model M** |" in md and "`M-weak`" in md and "`AGENT_PROFILE=x-best`" in md
    assert "2026-11-01 to 2026-11-24, now the fallback" in md and "[record](docs/d.md#1)" in md
    # the default is the rank's business only by coincidence: a weaker row may be the default
    assert {r["model"]: r["id"] for r in b["best"]}["Model M"] == "M-strong"
    for change, why in (({"run": "nope"}, "is not a board run"), ({"run": "M-weak-s1"}, "is a seed replicate"),
                        ({"since": "24 Nov"}, "is not YYYY-MM-DD"), ({"agent_profile": ""}, "missing agent_profile"),
                        ({"previous": [{**good["previous"][0], "until": "2026-11-30"}]}, "dates out of order"),
                        ({"previous": [{**good["previous"][0], "run": "M-weak"}]}, "is the current one")):
        bd["defaults"] = [{**good, **change}]
        assert any(why in p for p in v5_board.defaults_problems(bd)), change
    bd["defaults"] = [good, good]
    assert any("two defaults for one platform" in p for p in v5_board.defaults_problems(bd))
    try:
        v5_board.build(bd, base_dir=d, manifest=MANIFEST, weights=WEIGHTS)
    except ValueError:
        pass
    else:
        raise AssertionError("build accepted two defaults for one platform")


def test_committed_defaults():
    """The RTX 5090 ships INK-T2-STOCK since 2026-11-24 (Max), Q38U-T2 its fallback; the pair ships DS-T2."""
    bd = v5_board.load_board()
    assert v5_board.defaults_problems(bd) == []
    d = {x["platform"]: x for x in bd["defaults"]}
    assert (d["rtx5090"]["run"], d["rtx5090"]["since"], d["rtx5090"]["agent_profile"]) == ("INK-T2-STOCK", "2026-11-24", "inkling-best")
    assert [(p["run"], p["role"]) for p in d["rtx5090"]["previous"]] == [("Q38U-T2", "fallback")]
    assert (d["spark-pair"]["run"], d["spark-pair"]["agent_profile"]) == ("DS-T2", "deepseek-best")


def test_docs_are_current():
    """The CI drift gate: README.md and docs/RESULTS.md carry the generated blocks."""
    p = subprocess.run([sys.executable, os.path.join(REPO, "evals", "v5_board.py"), "--check"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


# --- context overflow is a model loss (Max's rule, 2026-11-25) ----------------------------

BASALT = ("engine reported request exceeds the context window: prompt (250000 tokens) + "
          "max tokens (16384) exceeds the context (262144)")


def _overflow_board(tmp_path):
    d = str(tmp_path)
    main = write(d, "main.json", results([
        row("1_a_py", True),
        row("1_a_go", False, reason="server_error", api_errors=5, api_errors_tail=5),
        row("2_b", False, reason="server_error", api_errors=3, api_errors_tail=3)]))
    repair = write(d, "repair.json", results([row("1_a_go", True), row("2_b", True)],
                                             ts="2026-11-16T12:00:00"))
    run = {"id": "M", "model": "Model M", "weights_id": "m-29", "config": "c", "files": [main, repair]}
    return d, run, {"runs": [run]}


def test_a_context_overflow_is_a_counted_fail_no_repair_supersedes(tmp_path):
    d, run, _ = _overflow_board(tmp_path)
    # no ledger: both server_error rows are lost, and the repair fills them
    r = v5_board.score_run(run, MANIFEST, base_dir=d, overflow=[])
    assert r["passed"] == 3 and r["overflow"] == {} and r["lost"] == {}
    led = [{"file": "main.json", "unit": "1_a_go", "label": "server_error", "verdict": "overflow",
            "evidence": "Basalt: exceeds the context window"},
           {"file": "main.json", "unit": "2_b", "label": "server_error", "verdict": "not_overflow",
            "evidence": "tool-call parse 500"}]
    r = v5_board.score_run(run, MANIFEST, base_dir=d, overflow=led)
    # 1_a_go ended on overflow: the model's fail, never re-rolled by the repair; 2_b is still repaired
    assert r["passed"] == 2 and r["overflow"] == {"1_a_go": "server_error"} and r["lost"] == {}
    _, rows = v5_board.merge_runs([os.path.join(d, f) for f in run["files"]], led)
    assert rows["1_a_go"]["reason"] == "context_overflow" and not rows["1_a_go"]["passed"]
    assert v5_board.infra_reason(rows["1_a_go"]) is None
    assert "1 unit ended on context overflow: model fail (overflow.json)" in v5_board._status(r)
    # unrepaired: an overflow stays in the denominator as a fail, not "lost"
    r1 = v5_board.score_run({**run, "files": run["files"][:1]}, MANIFEST, base_dir=d, overflow=led)
    assert r1["lost"] == {"2_b": "server_error"} and r1["overflow"] == {"1_a_go": "server_error"}
    # ...and the experiment status no longer counts it as an infrastructure row
    assert "infrastructure rows (1 server_error)" in r1["experiment"][0]


def test_overflow_problems_enforce_the_rule(tmp_path):
    d, _, bd = _overflow_board(tmp_path)
    probs = v5_board.overflow_problems(bd, [], base_dir=d)
    assert len(probs) == 2 and all("no overflow.json verdict" in p for p in probs)
    ok = [{"file": "main.json", "unit": u, "label": "server_error", "verdict": "not_overflow",
           "evidence": "e"} for u in ("1_a_go", "2_b")]
    assert v5_board.overflow_problems(bd, ok, base_dir=d) == []
    bad = ok + [{"file": "main.json", "unit": "1_a_py", "verdict": "maybe", "evidence": ""},
                {"file": "gone.json", "unit": "2_b", "verdict": "not_overflow", "evidence": "e"}]
    probs = v5_board.overflow_problems(bd, bad, base_dir=d)
    assert any("not one of" in p for p in probs) and any("no evidence" in p for p in probs)
    assert any("stale entry" in p for p in probs) and any("gone.json" in p for p in probs)
    # a row whose own text shows an overflow must be reclassified
    write(d, "main.json", results([row("1_a_py", True),
                                   row("1_a_go", False, reason="server_error", api_errors=5,
                                       last_api_error=BASALT),
                                   row("2_b", False, reason="server_error", api_errors=3)]))
    probs = v5_board.overflow_problems(bd, ok, base_dir=d)
    assert len(probs) == 1 and "1_a_go" in probs[0] and "reclassify" in probs[0]
    # diagnostics files are audited too
    os.makedirs(os.path.join(d, "diagnostics"))
    write(os.path.join(d, "diagnostics"), "eval-x.json",
          results([row("2_b", False, reason="server_error", api_errors=1)]))
    assert any("eval-x.json 2_b" in p for p in v5_board.overflow_problems(bd, ok, base_dir=d))


def test_overflow_wording_of_every_engine():
    for text in (BASALT,
                 "the request exceeds the available context size, try increasing it",  # llama.cpp
                 "{'type': 'exceed_context_size_error', 'n_prompt_tokens': 9, 'n_ctx': 8}",
                 "This model's maximum context length is 131072 tokens. However, ...",   # vLLM
                 "rendered prompt has 140,000 tokens; the server's 131,072-token context window",
                 "context overflow (nothing left to compact)"):
        assert v5_board.OVERFLOW_RE.search(text), text
    assert not v5_board.OVERFLOW_RE.search(
        "Error code: 500 - Failed to parse tool call arguments as JSON: parse error")


def test_committed_overflow_ledger_covers_every_server_error_row():
    """Every server_error row of every committed result file has a verdict, and
    every 'overflow' verdict shows on its run as a counted fail."""
    bd, led = v5_board.load_board(), v5_board.load_overflow()
    assert v5_board.overflow_problems(bd, led) == []
    b = v5_board.build()
    shown = {(r["id"], u) for r in b["runs"] for u in r["overflow"]}
    for e in led:
        if e["verdict"] != "overflow":
            continue
        for r in bd["runs"]:
            if e["file"] in r["files"]:
                assert (r["id"], e["unit"]) in shown or e["unit"] not in v5_board.load_manifest()
    # the 2026-11-25 audit: the 16 Qwen3.8 server_error rows were the truncated-call 500, not overflows
    assert sum(e["verdict"] == "not_overflow" and e["label"] == "server_error" for e in led) >= 16
