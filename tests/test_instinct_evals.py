#!/usr/bin/env python3
"""The decision-quality harness (evals/decisions/): metrics against hand-worked
values, dataset rules, and calibrate -> gate end to end on a throwaway set."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

import _instinct_helpers as H
import metrics as M
import run as RUN
from instinct.spec import load_spec

SPAWN = load_spec(H.DECISIONS / "router.spawn_intent.toml")
LABELS = ["spawn", "inline"]


def row(y, p_inline):
    return {"y": y, "p": {"inline": p_inline, "spawn": 1 - p_inline}}


# --- metrics ---------------------------------------------------------------------

def test_accuracy_f1_nll_brier():
    rows = [row("inline", 0.9), row("inline", 0.6), row("spawn", 0.2), row("spawn", 0.7)]
    assert M.accuracy(rows) == 0.75
    # inline: tp=2 fp=1 fn=0 -> 0.8 ; spawn: tp=1 fp=0 fn=1 -> 2/3
    assert M.macro_f1(rows, LABELS) == pytest.approx((0.8 + 2 / 3) / 2)
    want_nll = -(math.log(.9) + math.log(.6) + math.log(.8) + math.log(.3)) / 4
    assert M.nll(rows, LABELS) == pytest.approx(want_nll)
    want_brier = (2 * .1 ** 2 + 2 * .4 ** 2 + 2 * .2 ** 2 + 2 * .7 ** 2) / 4
    assert M.brier(rows, LABELS) == pytest.approx(want_brier)


def test_abstaining_row_counts_as_wrong_and_uniform():
    rows = [{"y": "inline", "p": None}]
    assert M.accuracy(rows) == 0.0
    assert M.nll(rows, LABELS) == pytest.approx(math.log(2))


def test_ece_perfect_and_bad():
    perfect = [row("inline", 1.0)] * 50 + [row("spawn", 0.0)] * 50
    assert M.ece(perfect, LABELS)["ece"] == pytest.approx(0.0)
    bad = [row("spawn", 0.99)] * 200         # 99% confident, always wrong
    e = M.ece(bad, LABELS)
    assert e["ece"] == pytest.approx(0.99) and e["bins"] == 15 and not e["indicative"]
    assert M.ece(bad[:100], LABELS)["bins"] == 5 and M.ece(bad[:100], LABELS)["indicative"]


def test_aurc():
    # confident rows right, unconfident wrong -> low AURC; reversed -> high
    good = [row("inline", 0.99)] * 5 + [row("inline", 0.4)] * 5
    bad = [row("spawn", 0.99)] * 5 + [row("spawn", 0.4)] * 5
    assert M.aurc(good, LABELS)["aurc"] < M.aurc(bad, LABELS)["aurc"]


def test_wilson_known_values():
    lo, hi = M.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert M.wilson(0, 0) == (0.0, 1.0)


def test_mcnemar_exact():
    a = [True] * 10 + [True] * 5
    b = [False] * 10 + [True] * 5
    r = M.mcnemar_exact(a, b)
    assert (r["b"], r["c"]) == (10, 0)
    assert r["p"] == pytest.approx(2 / 1024)
    assert M.mcnemar_exact(a, a)["p"] == 1.0
    r = M.mcnemar_exact([True, False, True], [False, True, False])   # b=2 c=1
    assert r["p"] == pytest.approx(1.0)


def test_bootstrap_deterministic_and_covers_mean():
    xs = [float(i) for i in range(100)]
    ci1 = M.bootstrap_ci(xs, lambda s: sum(s) / len(s), n_boot=500)
    ci2 = M.bootstrap_ci(xs, lambda s: sum(s) / len(s), n_boot=500)
    assert ci1 == ci2 and ci1[0] < 49.5 < ci1[1]
    d = M.paired_bootstrap_delta([1.0] * 20, [0.5] * 20, n_boot=200)
    assert d["delta"] == pytest.approx(0.5)


# --- datasets ----------------------------------------------------------------------

def _ds(tmp_path, splits: dict[str, list[dict]], manifest: str | None = None):
    d = tmp_path / "router.spawn_intent"
    d.mkdir(parents=True, exist_ok=True)
    for s, rows in splits.items():
        (d / f"{s}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if manifest is not None:
        (d / "MANIFEST.toml").write_text(manifest)
    return d


def r(i, text, y, source="handwritten", group=None):
    return {"id": f"r{i}", "input": {"user_turn": text}, "label": y, "source": source,
            "group": group or f"g{i}"}


def test_synthetic_rows_never_in_test_or_ood(tmp_path):
    for split in ("test", "ood"):
        d = _ds(tmp_path / split, {split: [r(1, "x", "inline", "synthetic")]})
        with pytest.raises(RUN.DatasetError, match="synthetic"):
            RUN.load_dataset(d, SPAWN)
    d = _ds(tmp_path / "ok", {"train": [r(1, "x", "inline", "synthetic")]})
    assert RUN.load_dataset(d, SPAWN)[0]["train"]            # control


def test_groups_never_span_splits(tmp_path):
    d = _ds(tmp_path, {"train": [r(1, "a", "inline", group="G")],
                       "test": [r(2, "b", "inline", group="G")]})
    with pytest.raises(RUN.DatasetError, match="group"):
        RUN.load_dataset(d, SPAWN)


def test_manifest_pins_are_checked(tmp_path):
    d = _ds(tmp_path, {"train": [r(1, "a", "inline")]},
            manifest='[files]\ntrain = "' + "0" * 64 + '"\n')
    with pytest.raises(RUN.DatasetError, match="sha256"):
        RUN.load_dataset(d, SPAWN)


def test_bad_label_or_input_rejected(tmp_path):
    with pytest.raises(RUN.DatasetError, match="label"):
        RUN.load_dataset(_ds(tmp_path / "a", {"train": [r(1, "a", "maybe")]}), SPAWN)
    bad = r(1, "a", "inline")
    bad["input"] = {"user_turn": 3}
    with pytest.raises(RUN.DatasetError):
        RUN.load_dataset(_ds(tmp_path / "b", {"train": [bad]}), SPAWN)


def test_seed_dataset_loads_and_is_honestly_labelled():
    d = H.REPO / "evals" / "decisions" / "router.spawn_intent"
    data, manifest = RUN.load_dataset(d, SPAWN)          # also verifies the sha pins
    assert manifest["status"] == "seed"
    assert all(x["source"] != "synthetic" for x in data["test"] + data["ood"])
    for s in ("train", "calib", "adversarial"):
        assert all(x["source"] == "synthetic" and "UNREVIEWED" in x["labeller"]
                   for x in data[s]), s
    adv_neg = [x for x in data["adversarial"] if x["label"] == "inline"]
    assert len(adv_neg) >= 32                              # plan §5.8 floor
    implicit = [x for s in ("train", "calib", "adversarial") for x in data[s]
                if x["label"] == "spawn" and "implicit" in x["group"]]
    from instinct.engines.rules import ROUTER_HINTS
    assert len(implicit) >= 30 and not any(ROUTER_HINTS.search(x["input"]["user_turn"])
                                           for x in implicit)


# --- run.py end to end ---------------------------------------------------------------

TRAIN = [("spawn a background agent to refactor the parser and report back", "spawn"),
         ("launch an agent in the background to port the module", "spawn"),
         ("kick off an autonomous agent to audit the repo", "spawn"),
         ("have a background agent migrate every test, don't block me", "spawn"),
         ("what does this function do", "inline"), ("fix the typo in the readme", "inline"),
         ("explain how decorators work", "inline"), ("what is 17 times 23", "inline")]


def _setup(tmp_path):
    rows = {"train": [r(i, t, y, "synthetic", "tr") for i, (t, y) in enumerate(TRAIN)],
            "calib": [r(100 + i, t + " please", y, "synthetic", "ca")
                      for i, (t, y) in enumerate(TRAIN)],
            "test": [r(200 + i, "could you " + t, y, "handwritten", "te")
                     for i, (t, y) in enumerate(TRAIN)]}
    data_dir = tmp_path / "data"
    _ds(data_dir, rows)
    cfgp = H.write_config(tmp_path, {}, decisions=["router.spawn_intent"])
    return cfgp, data_dir


def _run(cfgp, data_dir, tmp_path, *extra):
    argv = ["--decision", "router.spawn_intent", "--config", str(cfgp), "--data-dir",
            str(data_dir), "--out-dir", str(tmp_path / "out"), "--json", *extra]
    return RUN.main(argv)


def test_fit_calibrate_gate_end_to_end(tmp_path, capsys):
    cfgp, data_dir = _setup(tmp_path)
    assert _run(cfgp, data_dir, tmp_path, "--engine", "linear", "--fit-linear",
                "--calibrate") == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["calibrated"] is True and rep["splits"]["test"]["n"] == 8
    cal = json.loads(Path(rep["calibration"]["path"]).read_text())
    assert cal["decision_hash"] == rep["decision_hash"] and cal["model_file_sha256"]
    assert 0.05 <= cal["T"] <= 20
    # gate without a loadgen report must FAIL closed (latency criterion)
    assert _run(cfgp, data_dir, tmp_path, "--engine", "linear", "--gate") == 0
    rep = json.loads(capsys.readouterr().out)
    g = rep["gate"]
    assert g["passed"] is False
    lat = next(c for c in g["criteria"] if c["metric"] == "latency_p95_ms")
    assert lat["pass"] is False and lat["note"] == "no loadgen report"
    mc = next(c for c in g["criteria"] if c["metric"] == "mcnemar_p_vs")
    assert mc["pass"] is True and mc["note"].startswith("not_applicable")
    assert g["min_n_met"] is False                           # 8 rows < 200
    # an empty component split fails its criterion instead of being dropped
    ae = next(c for c in g["criteria"] if c["split"] == "test+ood+adversarial")
    assert ae["pass"] is False and ae["note"] == "empty split(s): ood,adversarial"
    integ = {i["check"]: i["ok"] for i in g["integrity"]}
    assert integ == {"dataset status is gated": False, "gate splits pinned in MANIFEST": False}
    rec = json.loads(Path(g["path"]).read_text())
    assert rec["calib_sha256"] == hashlib.sha256(
        Path(rep.get("calibration", {}).get("path") or
             cal_path(cfgp, rep["decision_hash"])).read_bytes()).hexdigest()
    run_dir = Path(rep["run_dir"]) if "run_dir" in rep else None
    assert run_dir is None or (run_dir / "samples.jsonl").exists()


def cal_path(cfgp, h):
    from instinct import calibrate as C
    from instinct.config import load_config
    return C.calib_path(load_config(cfgp, env={}).records_dir, "router.spawn_intent", h)


def test_gate_refuses_without_calibration(tmp_path):
    cfgp, data_dir = _setup(tmp_path)
    assert _run(cfgp, data_dir, tmp_path, "--engine", "linear", "--fit-linear") == 0
    with pytest.raises(SystemExit, match="calibration"):
        _run(cfgp, data_dir, tmp_path, "--engine", "linear", "--gate")


def test_rules_can_never_be_calibrated(tmp_path):
    cfgp, data_dir = _setup(tmp_path)
    with pytest.raises(SystemExit, match="I6"):
        _run(cfgp, data_dir, tmp_path, "--engine", "rules", "--calibrate")


def test_llm_engine_through_the_stub(tmp_path, capsys):
    cfgp, data_dir = _setup(tmp_path)
    with H.stub_server() as (url, _):
        cfgp = H.write_config(tmp_path, {"stub": H.llama_binding(url)},
                              decisions=["router.spawn_intent"])
        assert _run(cfgp, data_dir, tmp_path, "--engine", "stub", "--calibrate",
                    "--compare", "rules") == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["probe"]["ok"] and rep["calibrated"]
    assert "mcnemar" in rep["comparisons"]["rules"]["test"]
    assert len(rep["decision_hash"]) == 64


# --- the harness scores the population the service serves (review M4) -----------

TASK_INPUTS = {"prompt_head": "refactor this function", "est_prompt_tokens": 900,
               "has_images": False, "has_tools": True, "stream": True,
               "client_class": "ide"}


def test_harness_and_service_agree_on_mechanical_masking(tmp_path):
    from instinct.engines import LockResult, ScoreRes, ScoreRow
    from instinct.service import Instinct
    from instinct.config import load_config
    cfg = load_config(H.write_config(tmp_path, {}, decisions=["hydra.task_class"]), env={})
    spec = load_spec(H.DECISIONS / "hydra.task_class.toml")
    q = {"chat": 0.05, "code_agent": 0.75, "long_context": 0.1, "vision": 0.1, "bulk": 0.0}

    async def fixed(req):
        return ScoreRes(rows=[ScoreRow(q=dict(q), label_mass=1.0)])
    sc = RUN.Scorer(cfg, spec, "linear")
    sc.lock = LockResult(True)
    sc.engine.score = fixed
    rows = [{"id": "r1", "input": TASK_INPUTS, "label": "code_agent", "source": "handwritten"}]
    harness_p = RUN.apply_T(H.run(sc.score_rows(rows)), 1.0)[0]["p"]

    inst = Instinct(cfg)
    H.run(inst.reload())
    inst.calib[(spec.id, "linear")] = {"T": 1.0, "thresholds": {}}
    out = H.run(inst._evaluate(inst.specs[spec.id], "linear", H.run(fixed(None)), None))
    served = out.answer.probabilities
    assert served["vision"] == 0.0 and served["long_context"] == 0.0
    assert served["code_agent"] == pytest.approx(0.9375)
    assert harness_p == pytest.approx(served)


def test_mechanical_rows_are_not_part_of_the_judged_population():
    spec = load_spec(H.DECISIONS / "hydra.task_class.toml")
    rows = [{"id": "a", "input": dict(TASK_INPUTS), "label": "code_agent"},
            {"id": "b", "input": dict(TASK_INPUTS, has_images=True), "label": "vision"},
            {"id": "c", "input": dict(TASK_INPUTS, est_prompt_tokens=90000),
             "label": "long_context"}]
    kept, dropped = RUN.judged_rows(spec, {"test": rows, "calib": rows[:1]})
    assert [r["id"] for r in kept["test"]] == ["a"] and dropped == {"test": 2}
    assert RUN.judged_rows(SPAWN, {"test": rows}) == ({"test": rows}, {})   # no mechanical


def test_fitted_threshold_never_lowers_the_spec_floor():
    from instinct.core import effective_threshold
    assert SPAWN.policy.act["inline"] == 0.90
    assert effective_threshold(SPAWN, "inline", {"inline": 0.55}) == 0.90
    assert effective_threshold(SPAWN, "inline", {"inline": 0.97}) == 0.97
    assert effective_threshold(SPAWN, "inline", {"inline": None}) is None
    assert effective_threshold(SPAWN, "inline", None) == 0.90


def test_gate_integrity_needs_a_gated_pinned_set_and_a_probe():
    import types
    spec = SPAWN
    pins = {k: "x" for k in ("test", "ood", "adversarial")}
    llm = types.SimpleNamespace(adapter="llamacpp_logprobs",
                                probe=types.SimpleNamespace(ok=True))
    ok = RUN.gate_integrity(spec, {"status": "gated", "files": pins}, llm,
                            types.SimpleNamespace(no_probe=False))
    assert all(i["ok"] for i in ok)                          # control
    seed = RUN.gate_integrity(spec, {"status": "seed", "files": pins}, llm,
                              types.SimpleNamespace(no_probe=False))
    assert [i["ok"] for i in seed] == [False, True, True]
    unpinned = RUN.gate_integrity(spec, {"status": "gated", "files": {"test": "x"}}, llm,
                                  types.SimpleNamespace(no_probe=False))
    assert unpinned[1]["ok"] is False and unpinned[1]["detail"] == ["adversarial", "ood"]
    noprobe = RUN.gate_integrity(spec, {"status": "gated", "files": pins},
                                 types.SimpleNamespace(adapter="llamacpp_logprobs", probe=None),
                                 types.SimpleNamespace(no_probe=True))
    assert noprobe[2]["ok"] is False


# --- the gate's load report must be THIS subject's (B-instinct-04) ------------------

DID = "router.spawn_intent"


def _load(**kw):
    rep = {"decision": DID, "engine": "rig-27b", "decision_hash": "h" * 64,
           "intended_qps": 0.5, "p95_ms": 120.0,
           "points": [{"qps": 0.5, "sent": 100, "ok": 100, "errors": 0}]}
    rep.update(kw)
    return rep


def test_load_report_must_be_this_subjects_and_mostly_succeed():
    """B-instinct-04: a report for another decision/engine/hash, or one whose
    p95 covers only the survivors of a 30% error rate, must not pass."""
    assert RUN.load_report_problem(_load(), DID, "rig-27b", "h" * 64) is None   # control
    for rep, why in ((_load(engine="stub"), "not router.spawn_intent/rig-27b"),
                     (_load(decision="hydra.task_class"), "not router.spawn_intent"),
                     (_load(decision_hash="x" * 64), "another decision_hash"),
                     (_load(decision_hash=None), "no decision_hash"),
                     (_load(points=[{"qps": 0.5, "sent": 64, "errors": 22}]), "error rate"),
                     (_load(points=[]), "sent no calls")):
        assert why in (RUN.load_report_problem(rep, DID, "rig-27b", "h" * 64) or ""), rep
