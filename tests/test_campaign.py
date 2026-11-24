"""evals/campaign.py: the chained harness campaign (docs/MODEL_CAMPAIGN.md).

Everything runs against a FAKE run_eval in a throwaway tree, so no model,
server or toolchain is needed. Pinned here:

  1. The shipped campaign file loads and resolves: each arm is its base's
     flags and env plus its own change (A..F of the GLM night).
  2. `run` chains the arms in order, one run_eval process each, with a
     distinct --model-name per arm and the arm's env.
  3. Decisions: thinking goes into E only when C beat B, on the units both
     scored; the decision is recorded once and reused on resume.
  4. Infra rows are refused: interpreter startup, no output in < 2 s, signal
     deaths, server errors and budget skips are lost, never scored; a wall
     timeout (-1) and every pass are real verdicts.
  5. rerun-lost writes a sub-manifest of exactly the lost units (coverage
     floor zero, suite field kept), reruns them with the arm's own flags, and
     the merged rows score the rerun.
  6. A unit an audit called TRUE_FAIL is never re-rolled.
  7. The report renders from fixture results files: totals, the reference
     row, flips, the noise line, the wall-scaled reading.
  8. An arm whose flag the tree lacks is refused, not run without it.
  9. The thinking-first campaign (the default): the composite runs first,
     each ablation is the composite minus exactly its component, the
     no-thinking arms run last, packs v3 wherever packs are on, a reasoning
     budget declared per arm (the runner's thinking cap when thinking), the
     run order is configurable, and --dry-run prints an ETA per arm.
 10. The decision rules: components within the seed noise are dropped,
     thinking is kept only beyond it and priced; the report renders the
     recommended config and the thinking-vs-no-thinking section.
"""
import copy
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import campaign as C  # noqa: E402

UNITS = ["1_alpha", "2_beta_a", "2_beta_b", "3_gamma"]

FAKE_RUN_EVAL = textwrap.dedent(r'''
    import argparse, json, os, re, sys, time
    ap = argparse.ArgumentParser()
    for f in ("--suite", "--model-name", "--base-url", "--api-key-env", "--reasoning-budget",
              "--turns-per-wall-s", "--thinking", "--seed"):
        ap.add_argument(f)
    if not os.environ.get("FAKE_NO_WALL_SCALE"):
        ap.add_argument("--wall-scale")
    ap.add_argument("--jobs"); ap.add_argument("--slots")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    tree = os.getcwd()
    man = json.load(open(os.path.join(tree, "evals", "suites", a.suite + ".json")))
    arm = a.model_name.split()[-1]
    plan = json.load(open(os.environ["FAKE_PLAN"])).get(arm, {})
    with open(os.environ["FAKE_CALLS"], "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "arm": arm, "suite": a.suite,
                            "env": {k: os.environ.get(k) for k in (
                                "BEAST_ASSIST", "BEAST_PACKS", "OPENBEAST_AGENT_ENV_BLOCK",
                                "OPENBEAST_DIAG_EDIT_HINT", "OPENBEAST_AGENT_THINKING")}}) + "\n")
    rows = []
    first = not a.suite.startswith("_lost-")
    for u in man["units"]:
        uid = u["id"]
        if first and uid in plan.get("missing", []):
            continue
        if first and uid in plan.get("infra", []):
            rows.append({"id": uid, "passed": False, "elapsed_seconds": 0.0, "agent_exit_code": 1,
                         "tokens_completion": 0, "iterations": None, "max_iter": 40,
                         "validation_output": "Could not find platform independent libraries <prefix>",
                         "language": u["lang"], "role": "scored"})
            continue
        ok = uid in plan.get("pass", [])
        rows.append({"id": uid, "passed": ok, "elapsed_seconds": plan.get("secs", {}).get(uid, 100.0),
                     "agent_exit_code": 0, "tokens_completion": 500, "iterations": 10, "max_iter": 40,
                     "validation_output": "OK" if ok else "FAIL: wrong", "language": u["lang"],
                     "role": "scored"})
    slug = re.sub(r"[^a-z0-9]+", "-", a.model_name.lower()).strip("-")
    os.makedirs(os.path.join(tree, "evals", "results"), exist_ok=True)
    path = os.path.join(tree, "evals", "results", f"eval-{slug}-{time.time_ns()}.json")
    head = {"timestamp": "2026-11-16T10:00:00", "model": a.model_name, "jobs": int(a.jobs or 1),
            "suite_manifest": a.suite}
    if a.__dict__.get("wall_scale"):
        head["wall_scale"] = float(a.wall_scale)
    head["tasks"] = rows
    head["summary"] = {"total": len(rows), "passed": sum(r["passed"] for r in rows)}
    json.dump(head, open(path, "w"))
    print("Saved to: " + path)
    sys.exit(1 if any(not r["passed"] for r in rows) else 0)
''')


@pytest.fixture
def tree(tmp_path, monkeypatch):
    t = tmp_path / "tree"
    (t / "evals" / "suites").mkdir(parents=True)
    (t / "agents").mkdir()
    (t / "evals" / "run_eval.py").write_text(FAKE_RUN_EVAL)
    (t / "agents" / "runner.py").write_text(
        "# OPENBEAST_DIAG_EDIT_HINT OPENBEAST_AGENT_VERIFY_DONE OPENBEAST_PACK_VARIANT "
        "OPENBEAST_AGENT_ENV_BLOCK OPENBEAST_AGENT_WALL_WARN\n")
    langs = {"1_alpha": "python", "2_beta_a": "python", "2_beta_b": "go", "3_gamma": "zig"}
    man = {"suite": "v5", "format": 1, "description": "fake",
           "coverage_floor": {"cell_min": 1, "categories": 1, "languages": 1},
           "units": [{"id": u, "task": int(u.split("_")[0]), "lang": langs[u], "role": "scored",
                      "max_wall_s": 600} for u in UNITS]}
    (t / "evals" / "suites" / "v5.json").write_text(json.dumps(man))
    monkeypatch.setenv("FAKE_CALLS", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FAKE_PLAN", str(tmp_path / "plan.json"))
    monkeypatch.setattr(C, "interpreter_problem", lambda *a, **k: None)
    C._HELP_CACHE.clear()
    return t


def plan(tmp_path, p):
    (tmp_path / "plan.json").write_text(json.dumps(p))


def calls(tmp_path):
    f = tmp_path / "calls.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def run(tree, *extra):
    return C.main(["run", "--campaign", "v5-harness", "--model-name", "Fake-1",
                   "--base-url", "http://127.0.0.1:9/v1", "--jobs", "4", "--slots", "4",
                   "--tree", str(tree), "--python", sys.executable, "--skip-preflight", *extra])


def state(tree):
    return C.load_state(os.path.join(tree, "evals", "results", "campaigns", "v5-harness-fake-1"))


# --- 1. the campaign file ---------------------------------------------------------------

def test_shipped_campaign_resolves_each_arm_as_base_plus_one_change():
    camp = C.load_campaign("v5-harness")
    assert [a["id"] for a in camp["arms"]] == ["A", "B", "C", "D", "E", "F"]
    b = C.resolve_arm(camp, "B")
    assert b["args"][-2:] == ["--turns-per-wall-s", "15"]
    c = C.resolve_arm(camp, "C")
    assert "--thinking" in c["args"] and c["wall_scale"] == 2.0
    assert "--wall-scale" in c["requires"]["flags"]
    d = C.resolve_arm(camp, "D")
    assert d["env"]["BEAST_ASSIST"] == "1" and d["env"]["OPENBEAST_DIAG_EDIT_HINT"] == "1"
    assert "--thinking" not in d["args"]                       # D builds on B, not C
    e_on = C.resolve_arm(camp, "E", {"thinking": "then"})
    e_off = C.resolve_arm(camp, "E", {"thinking": "else"})
    assert e_on["env"]["BEAST_PACKS"] == "1" and e_on["env"]["BEAST_ASSIST"] == "1"
    assert "--thinking" in e_on["args"] and e_on["wall_scale"] == 2.0
    assert "--thinking" not in e_off["args"] and e_off["wall_scale"] == 1.0
    f = C.resolve_arm(camp, "F")
    assert f["args"][-2:] == ["--seed", "1"] and f["env"]["BEAST_ASSIST"] == "0"
    with pytest.raises(SystemExit):
        C.resolve_arm(camp, "E")                               # decision not made yet


def test_campaign_file_refuses_flags_campaign_py_owns(tmp_path):
    bad = {"campaign": "x", "common": {"args": ["--suite", "v5"]},
           "arms": [{"id": "A", "args": ["--model-name", "y"]},
                    {"id": "B", "base": "Z"}]}
    errs = C.validate_campaign(bad)
    assert any("--model-name" in e for e in errs)
    assert any("base Z" in e for e in errs)


# --- 2./3. chaining and decisions ----------------------------------------------------------

def test_run_chains_every_arm_in_order_with_its_flags(tree, tmp_path):
    plan(tmp_path, {"A": {"pass": ["1_alpha"]}, "B": {"pass": ["1_alpha", "3_gamma"]},
                    "C": {"pass": ["1_alpha", "2_beta_a", "3_gamma"]},
                    "D": {"pass": UNITS}, "E": {"pass": UNITS}, "F": {"pass": ["1_alpha"]}})
    assert run(tree) == 0
    cs = calls(tmp_path)
    assert [c["arm"] for c in cs] == ["A", "B", "C", "D", "E", "F"]
    names = [c["argv"][c["argv"].index("--model-name") + 1] for c in cs]
    assert len(set(names)) == 6 and names[0] == "Fake-1 v5-harness A"
    by = {c["arm"]: c for c in cs}
    assert "--turns-per-wall-s" not in by["A"]["argv"]
    assert "--turns-per-wall-s" in by["F"]["argv"] and "--seed" in by["F"]["argv"]
    assert by["D"]["env"]["BEAST_ASSIST"] == "1" and by["D"]["env"]["BEAST_PACKS"] == "0"
    assert by["E"]["env"]["BEAST_PACKS"] == "1" and by["E"]["env"]["OPENBEAST_AGENT_ENV_BLOCK"] == "1"
    # C (3) beat B (2): E thinks, at wall x2
    assert "--thinking" in by["E"]["argv"] and "--wall-scale" in by["E"]["argv"]
    st = state(tree)
    assert st["arms"]["E"]["decisions_taken"] == {"thinking": "then"}
    assert all(st["arms"][x]["status"] == "done" for x in "ABCDEF")
    assert st["arms"]["D"]["score"]["passed"] == 4
    # resumable: a second run skips every finished arm
    assert run(tree) == 0
    assert len(calls(tmp_path)) == 6


def test_thinking_stays_off_when_c_does_not_beat_b(tree, tmp_path):
    plan(tmp_path, {"B": {"pass": ["1_alpha", "3_gamma"]}, "C": {"pass": ["1_alpha", "2_beta_a"]}})
    assert run(tree) == 0
    e = [c for c in calls(tmp_path) if c["arm"] == "E"][0]
    assert "--thinking" not in e["argv"]
    assert state(tree)["arms"]["E"]["decisions_taken"] == {"thinking": "else"}


def test_decide_compares_on_the_units_both_arms_scored():
    rule = {"compare": ["C", "B"], "op": ">"}
    ok = {"passed": True, "tokens_completion": 1, "elapsed_seconds": 9}
    bad = {"passed": False, "tokens_completion": 1, "elapsed_seconds": 9}
    lost = {"passed": False, "tokens_completion": 0, "elapsed_seconds": 0.0}
    b = {"u1": ok, "u2": bad, "u3": ok}
    c = {"u1": ok, "u2": ok, "u3": lost}          # u3 lost in C: compared on u1, u2 only
    branch, why = C.decide(rule, {"C": c, "B": b})
    assert branch == "then" and "2 units" in why
    assert C.decide(rule, {"C": {}, "B": b})[0] == "else"


def test_from_and_only_select_arms(tree, tmp_path):
    plan(tmp_path, {})
    assert run(tree, "--only", "B") == 0
    assert [c["arm"] for c in calls(tmp_path)] == ["B"]
    assert run(tree, "--from", "E", "--set-decision", "thinking=else") == 0
    assert [c["arm"] for c in calls(tmp_path)] == ["B", "E", "F"]


def test_an_arm_the_tree_cannot_run_is_refused(tree, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_NO_WALL_SCALE", "1")
    plan(tmp_path, {"B": {"pass": ["1_alpha"]}})
    assert run(tree) == 0
    arms = [c["arm"] for c in calls(tmp_path)]
    assert "C" not in arms
    st = state(tree)
    assert st["arms"]["C"]["status"] == "unsupported"
    assert any("--wall-scale" in m for m in st["arms"]["C"]["unsupported"])
    # E's decision had no C to compare: else branch, no thinking, no --wall-scale
    assert st["arms"]["E"]["decisions_taken"] == {"thinking": "else"}


def test_a_broken_interpreter_stops_the_chain(tree, tmp_path, monkeypatch):
    plan(tmp_path, {})
    monkeypatch.setattr(C, "interpreter_problem", lambda *a, **k: "cannot import openai")
    assert run(tree) == 3
    assert calls(tmp_path) == []


# --- 4. infra rows ------------------------------------------------------------------------

@pytest.mark.parametrize("row,why", [
    ({"passed": False, "elapsed_seconds": 0.0, "tokens_completion": 0,
      "validation_output": "Could not find platform independent libraries <prefix>"},
     "interpreter_startup"),
    ({"passed": False, "elapsed_seconds": 640, "tokens_completion": 11000,
      "validation_output": "Could not find platform independent libraries <prefix>"},
     "interpreter_startup"),                      # the validator's python broke, not the agent
    ({"passed": False, "elapsed_seconds": 1.2, "tokens_completion": 0, "validation_output": ""},
     "no_output_fast"),
    ({"passed": False, "elapsed_seconds": 300, "tokens_completion": 900, "agent_exit_code": -9},
     "signal_death"),
    ({"passed": False, "elapsed_seconds": 300, "tokens_completion": 900, "api_errors": 2},
     "server_error"),
    ({"passed": False, "reason": "budget_skip"}, "budget_skip"),
    ({"passed": False, "elapsed_seconds": 1800, "tokens_completion": 9000, "agent_exit_code": -1},
     None),                                       # the wall ran out: a verdict
    ({"passed": True, "elapsed_seconds": 0.5, "tokens_completion": 0}, None),
    ({"passed": False, "elapsed_seconds": 200, "tokens_completion": 5000,
      "validation_output": "FAIL: wrong answer"}, None),
])
def test_infra_rows_are_never_scored(row, why):
    assert C.infra_reason(row) == why
    sc = C.score({"u": row})
    assert sc["scorable"] == (0 if why else 1)


def test_a_later_infra_row_never_replaces_a_real_verdict(tmp_path):
    good = {"id": "u", "passed": False, "elapsed_seconds": 90, "tokens_completion": 10}
    bad = {"id": "u", "passed": False, "elapsed_seconds": 0.0, "tokens_completion": 0}
    p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    p1.write_text(json.dumps({"tasks": [good]}))
    p2.write_text(json.dumps({"tasks": [bad]}))
    assert C.merge_rows([str(p1), str(p2)])["u"]["elapsed_seconds"] == 90
    assert C.merge_rows([str(p2), str(p1)])["u"]["elapsed_seconds"] == 90


# --- 5. rerun-lost ---------------------------------------------------------------------------

def test_lost_units_rerun_through_a_sub_manifest(tree, tmp_path):
    plan(tmp_path, {"A": {"pass": ["1_alpha", "2_beta_b"], "infra": ["2_beta_b", "3_gamma"]}})
    assert run(tree, "--only", "A") == 0
    cs = calls(tmp_path)
    assert [c["suite"] for c in cs] == ["v5", "_lost-v5-harness-fake-1-A-r1"]
    rerun_argv = cs[1]["argv"]
    assert rerun_argv.count("--suite") == 1
    assert rerun_argv[rerun_argv.index("--model-name") + 1] == "Fake-1 v5-harness A"
    sub = json.loads((tree / "evals" / "suites" / "_lost-v5-harness-fake-1-A-r1.json").read_text())
    assert [u["id"] for u in sub["units"]] == ["2_beta_b", "3_gamma"]
    assert sub["suite"] == "v5" and set(sub["coverage_floor"].values()) == {0}
    st = state(tree)
    a = st["arms"]["A"]
    assert a["status"] == "done" and a["lost"] == {}
    assert a["score"] == C.score(C.arm_rows(a)) and a["score"]["passed"] == 2   # 1_alpha + rerun 2_beta_b
    assert a["score"]["scorable"] == 4


def test_rerun_lost_command_and_missing_units(tree, tmp_path):
    camp = C.load_campaign("v5-harness")
    camp["rerun_lost"] = {"rounds": 0, "max_lost_fraction": 1.0}
    path = tmp_path / "c.json"
    path.write_text(json.dumps({k: v for k, v in camp.items() if k != "_path"}))
    plan(tmp_path, {"A": {"pass": ["1_alpha"], "missing": ["3_gamma"]}})
    base = ["--campaign", str(path), "--model-name", "Fake-1", "--tree", str(tree),
            "--python", sys.executable]
    assert C.main(["run", *base, "--base-url", "http://127.0.0.1:9/v1", "--jobs", "4",
                   "--skip-preflight", "--only", "A"]) == 0
    sdir = os.path.join(tree, "evals", "results", "campaigns", "v5-harness-fake-1")
    assert C.load_state(sdir)["arms"]["A"]["lost"] == {"3_gamma": "missing"}
    assert C.main(["rerun-lost", *base, "--arm", "A"]) == 0
    assert calls(tmp_path)[-1]["suite"] == "_lost-v5-harness-fake-1-A-r1"
    assert C.load_state(sdir)["arms"]["A"]["lost"] == {}


def test_a_true_fail_is_never_re_rolled(tree, tmp_path):
    plan(tmp_path, {"A": {"pass": []}})
    assert run(tree, "--only", "A") == 0
    verdicts = tmp_path / "audit.json"
    verdicts.write_text(json.dumps([{"task": "3", "audit": {"units": [
        {"unit": "3_gamma", "verdict": "HARNESS"}], "systemic_note": ""},
        "challenge": {"units": [{"unit": "3_gamma", "agree": False, "final_verdict": "TRUE_FAIL",
                                 "reasoning": "model bug"}]}}]))
    base = ["rerun-lost", "--campaign", "v5-harness", "--model-name", "Fake-1", "--tree", str(tree),
            "--python", sys.executable, "--arm", "A"]
    with pytest.raises(SystemExit, match="TRUE_FAIL"):
        C.main([*base, "--units", "3_gamma", "--verdicts", str(verdicts)])
    with pytest.raises(SystemExit, match="--verdicts"):
        C.main([*base, "--units", "3_gamma"])
    n = len(calls(tmp_path))
    assert C.main([*base, "--units", "2_beta_a", "--verdicts", str(verdicts)]) == 0
    assert len(calls(tmp_path)) == n + 1


@pytest.mark.parametrize("status", ["running", "incomplete"])
def test_an_interrupted_arm_resumes_by_rerunning_only_what_is_missing(tree, tmp_path, status):
    plan(tmp_path, {"A": {"pass": UNITS}})
    sdir = os.path.join(tree, "evals", "results", "campaigns", "v5-harness-fake-1")
    os.makedirs(os.path.join(tree, "evals", "results"), exist_ok=True)
    partial = os.path.join(tree, "evals", "results", "eval-fake-1-v5-harness-a-1.json")
    json.dump({"timestamp": "2026-11-16T10:00:00", "tasks": [
        {"id": "1_alpha", "passed": True, "elapsed_seconds": 50, "tokens_completion": 9}]},
        open(partial, "w"))
    C.save_state(sdir, {"version": 1, "campaign": "v5-harness", "model_name": "Fake-1",
                        "base_url": "x", "jobs": 4, "tree": {"commit": None}, "baselines": {},
                        "arms": {"A": {"status": status, "results": [partial]}}})
    assert run(tree, "--only", "A") == 0
    cs = calls(tmp_path)
    assert len(cs) == 1 and cs[0]["suite"].startswith("_lost-")
    sub = json.loads((tree / "evals" / "suites" / f"{cs[0]['suite']}.json").read_text())
    assert [u["id"] for u in sub["units"]] == ["2_beta_a", "2_beta_b", "3_gamma"]
    assert C.load_state(sdir)["arms"]["A"]["score"]["passed"] == 4


# --- 7. the report ------------------------------------------------------------------------------

def _results(path, rows, **head):
    d = {"timestamp": "2026-11-16T10:00:00", "jobs": 4, **head, "tasks": rows}
    path.write_text(json.dumps(d))
    return str(path)


def _row(uid, ok, secs=100.0, lang="python", **kw):
    return {"id": uid, "passed": ok, "elapsed_seconds": secs, "tokens_completion": 10,
            "iterations": 10, "max_iter": 40, "language": lang, **kw}


def test_report_renders_from_fixture_results(tmp_path):
    camp = C.load_campaign("v5-harness")
    l0 = _results(tmp_path / "l0.json", [_row("u1", False), _row("u2", True), _row("u3", False),
                                         _row("u4", False, lang="zig")])
    a = _results(tmp_path / "a.json", [_row("u1", True), _row("u2", True), _row("u3", False),
                                       _row("u4", False, lang="zig")])
    b = _results(tmp_path / "b.json", [_row("u1", True), _row("u2", False), _row("u3", True),
                                       {"id": "u4", "passed": False, "elapsed_seconds": 0.0,
                                        "tokens_completion": 0, "language": "zig"}])
    c = _results(tmp_path / "c.json", [_row("u1", True, secs=500), _row("u2", True, secs=1300),
                                       _row("u3", True, secs=900), _row("u4", True, secs=100)],
                 wall_scale=2.0)
    f = _results(tmp_path / "f.json", [_row("u1", True), _row("u2", True), _row("u3", False),
                                       _row("u4", False, lang="zig")])
    st = {"campaign": "v5-harness", "model_name": "Fake-1", "base_url": "http://x", "jobs": 4,
          "arms": {
              "A": {"status": "done", "label": "runner v2", "results": [a],
                    "tree": {"commit": "abc", "era": "e1"}, "started_at": 0, "ended_at": 3600},
              "B": {"status": "done", "label": "+ turns", "results": [b], "lost": {"u4": "no_output_fast"},
                    "tree": {"commit": "abc", "era": "e1"}},
              "C": {"status": "done", "label": "+ think", "results": [c], "wall_scale": 2.0,
                    "decisions_taken": {}, "tree": {"commit": "abc", "era": "e1"}},
              "E": {"status": "unsupported", "unsupported": ["no harness file reads X"]},
              "F": {"status": "done", "label": "seed", "results": [f], "tree": {"era": "e1"}},
          }}
    ref = {u: {"unit": u, "passed": True, "over_budget": u == "u2", "elapsed_s": 100} for u in
           ("u1", "u2", "u3", "u4")}
    md = C.render_report(camp, st, {"L0": C.merge_rows([l0])}, ref,
                         walls={"u1": 300, "u2": 600, "u3": 600, "u4": 300}, total_units=4)
    assert "| **reference** |" in md and "**4** (3 at 1x wall)" in md
    assert "| L0 | baseline | 1 | 4 |" in md
    assert "| **A** done | runner v2 | **2** | 4 | 0 | 50 |" in md
    assert "| **B** done | + wall-scaled turns | **2** | 3 | 1 |" in md       # u4 lost, not failed
    assert "| L0 → A | 4 | 1 | 2 | 1 | 0 | +1 |" in md
    assert "| A → B | 3 | 2 | 2 | 1 | 1 | +0 |" in md
    assert "**A → B** rescued: `u3`; regressed: `u2`" in md
    assert "**F vs B**" in md and "2 of 3 units flip" in md
    # C at the original wall: walls x contention 2.0 -> u1 600, u2 1200, u3 1200, u4 600
    assert "| C | 2 | 4 | 3 | `u2` |" in md
    assert "arm **E** was not run" in md
    assert "| zig |" in md and "`u4` (no_output_fast)" in md


def test_report_command_end_to_end(tree, tmp_path, capsys):
    plan(tmp_path, {"A": {"pass": ["1_alpha"]}, "B": {"pass": ["1_alpha", "3_gamma"]}})
    assert run(tree, "--only", "A,B") == 0
    l0 = _results(tmp_path / "l0.json", [_row(u, False) for u in UNITS])
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps({"unit": u, "passed": True}) for u in UNITS) + "\n")
    out = tmp_path / "rep.md"
    assert C.main(["report", "--campaign", "v5-harness", "--model-name", "Fake-1",
                   "--tree", str(tree), "--baseline", f"L0={l0}",
                   "--reference-ledger", str(ledger), "--out", str(out)]) == 0
    md = out.read_text()
    assert "| L0 → B | 4 | 0 | 2 | 2 | 0 | +2 |" in md
    assert "| A → B |" in md and "**reference**" in md


def test_sign_test():
    assert C.sign_test_p(0, 0) == 1.0
    assert abs(C.sign_test_p(10, 0) - 2 / 1024) < 1e-12
    assert C.sign_test_p(3, 3) == 1.0


def test_preflight_runs_the_host_check_hook_and_refuses_on_failure(tree, tmp_path):
    (tree / "scripts").mkdir()
    hook = tree / "scripts" / "eval-host.sh"
    hook.write_text("#!/bin/sh\necho 'zig 0.15.1 != 0.16.0'\nexit 1\n")
    hook.chmod(0o755)
    camp = C.load_campaign("v5-harness")

    class A:
        base_url, jobs, served_model, model_name, api_key_env = "http://127.0.0.1:9/v1", 1, None, "m", None
    problems = C.preflight(A, camp, sys.executable, str(tree))
    assert any("scripts/eval-host.sh check" in p and "0.15.1" in p for p in problems)
    plan(tmp_path, {})      # and `run` without --skip-preflight refuses before any arm
    rc = C.main(["run", "--campaign", "v5-harness", "--model-name", "Fake-1",
                 "--base-url", "http://127.0.0.1:9/v1", "--tree", str(tree),
                 "--python", sys.executable])
    assert rc == 2 and calls(tmp_path) == []


# --- 9. the thinking-first campaign (the default) ---------------------------------------------

TF = "v5-thinking-first"
TF_ARMS = ["T", "T_noturns", "T_nopacks", "T_noassist", "T_noenv", "T_s1", "N", "N0"]
COMPOSITE_ENV = {"BEAST_ASSIST": "1", "OPENBEAST_DIAGNOSTICS": "1", "OPENBEAST_DIAG_EDIT_HINT": "1",
                 "OPENBEAST_AGENT_VERIFY_DONE": "1", "BEAST_PACKS": "1", "OPENBEAST_PACKS": "1",
                 "OPENBEAST_PACK_VARIANT": "v3", "OPENBEAST_AGENT_ENV_BLOCK": "1",
                 "OPENBEAST_AGENT_WALL_WARN": "1"}


def _flagmap(args):
    return {f: v for f, v in C._arg_pairs(args)}


def test_thinking_first_is_the_default_campaign_and_runs_best_first():
    assert C.DEFAULT_CAMPAIGN == TF
    camp = C.load_campaign(TF)
    assert C.run_order(camp) == TF_ARMS                        # composite first, no-thinking last
    assert C.run_order(camp, include=["T_s2"]) == TF_ARMS + ["T_s2"]
    assert C.run_order(camp, prefer=["T", "T_s1"])[:3] == ["T", "T_s1", "T_noturns"]
    with pytest.raises(SystemExit):
        C.run_order(camp, prefer=["nope"])


def test_composite_carries_every_component_with_thinking_at_wall_x2():
    camp = C.load_campaign(TF)
    t = C.resolve_arm(camp, "T")
    assert {k: t["env"][k] for k in COMPOSITE_ENV} == COMPOSITE_ENV
    assert t["env"]["BEAST_ESCALATE"] == "0"
    fl = _flagmap(t["args"])
    assert fl["--turns-per-wall-s"] == "15" and fl["--thinking"] == "on"
    assert t["wall_scale"] == 2.0 and t["thinking"] == "on"


def test_each_ablation_is_the_composite_minus_exactly_its_component():
    camp = C.load_campaign(TF)
    t = C.resolve_arm(camp, "T")
    removed = {"T_noturns": ({"--turns-per-wall-s"}, set()),
               "T_nopacks": (set(), {"BEAST_PACKS", "OPENBEAST_PACKS", "OPENBEAST_PACK_VARIANT"}),
               "T_noassist": (set(), {"BEAST_ASSIST", "OPENBEAST_DIAGNOSTICS",
                                      "OPENBEAST_DIAG_EDIT_HINT", "OPENBEAST_AGENT_VERIFY_DONE"}),
               "T_noenv": (set(), {"OPENBEAST_AGENT_ENV_BLOCK", "OPENBEAST_AGENT_WALL_WARN"})}
    tf = _flagmap(t["args"])
    for aid, (flags, envs) in removed.items():
        x = C.resolve_arm(camp, aid)
        xf = _flagmap(x["args"])
        assert xf == {k: v for k, v in tf.items() if k not in flags}, aid
        changed = {k for k in t["env"] if t["env"][k] != x["env"][k]}
        assert changed == envs, aid
        assert all(x["env"][k] in ("0", "") for k in envs), aid
        assert x["thinking"] == "on" and x["wall_scale"] == 2.0, aid   # every ablation thinks
    assert set(camp["recommend"]["ablations"]) == set(removed)


def test_no_thinking_counterparts_and_the_seed_arms():
    camp = C.load_campaign(TF)
    n = C.resolve_arm(camp, "N")
    assert n["thinking"] == "off" and n["wall_scale"] == 1.0
    assert "--wall-scale" not in n["args"]
    assert {k: n["env"][k] for k in COMPOSITE_ENV} == COMPOSITE_ENV        # the full harness
    assert _flagmap(n["args"])["--turns-per-wall-s"] == "15"
    n0 = C.resolve_arm(camp, "N0")                                         # = 2026-11-16 arm B
    assert _flagmap(n0["args"]) == {"--suite": "v5", "--no-cache": None, "--reasoning-budget": "0",
                                    "--turns-per-wall-s": "15", "--thinking": "off"}
    assert {k for k, v in n0["env"].items() if v not in ("0", "")} == {"OPENBEAST_EVAL_ENV_ERA"}
    for s, seed in (("T_s1", "1"), ("T_s2", "2")):
        x = C.resolve_arm(camp, s)
        assert _flagmap(x["args"])["--seed"] == seed and x["env"] == C.resolve_arm(camp, "T")["env"]
        assert C.arm_spec(camp, s)["noise_of"] == "T"
    assert C.arm_spec(camp, "T_s2").get("optional") is True


def _runner_thinking_cap():
    import re
    src = (ROOT / "agents" / "runner.py").read_text()
    return re.search(r"^_EVAL_TURN_TOKENS_THINKING = (\d+)", src, re.M).group(1)


@pytest.mark.parametrize("name,variant", [(TF, "v3"), ("v5-harness", "v2")])
def test_reasoning_budget_declared_per_arm_and_pack_variant_wherever_packs_are_on(name, variant):
    """A thinking arm declares the eval turn cap that bounds its thinking
    (never 0 = thinking off), a no-thinking arm 0; every packs-on arm names
    its pack variant explicitly (the variant defaults to the v1 pack): the
    thinking-first campaign measures v3 (adds the std.posix.write claim, #194),
    v5-harness keeps v2 so the 2026-11-16 arm E stays reproducible."""
    camp = C.load_campaign(name)
    cap = _runner_thinking_cap()
    specs = []
    for a in camp["arms"]:
        branches = [{d: b for d in a.get("apply_decisions") or []} for b in ("then", "else")]
        specs += [(a["id"], C.resolve_arm(camp, a["id"], br)) for br in branches]
    for aid, sp in specs:
        rb = C.arg_value(sp["args"], "--reasoning-budget")
        # an arm that sets its own turn cap (T2: 8192) declares that cap
        own = sp["env"].get("OPENBEAST_AGENT_MAX_TOKENS") or None
        assert rb == ((own or cap) if sp["thinking"] == "on" else "0"), aid
        if sp["env"].get("BEAST_PACKS") == "1":
            assert sp["env"].get("OPENBEAST_PACK_VARIANT") == variant, aid
            assert "OPENBEAST_PACK_VARIANT" in sp["requires"]["env"], aid


def test_merge_args_adds_switches_and_drops_flags():
    base = ["--suite", "v5", "--no-cache", "--reasoning-budget", "0", "--wall-scale=2"]
    assert C.merge_args(base, ["--reasoning-budget", "16384", "--seed", "1"]) == \
        ["--suite", "v5", "--no-cache", "--reasoning-budget", "16384", "--wall-scale", "2",
         "--seed", "1"]
    assert C.merge_args(base, None, ["--wall-scale", "--no-cache"]) == \
        ["--suite", "v5", "--reasoning-budget", "0"]
    assert C.arg_value(["--x", "-1", "--x", "3"], "--x") == "3"


def test_campaign_file_refuses_bad_orders_and_recommend_blocks():
    camp = {k: v for k, v in C.load_campaign(TF).items() if k != "_path"}
    bad = json.loads(json.dumps(camp))
    bad["order"] = ["T", "T", "ZZ"]
    errs = C.validate_campaign(bad)
    assert any("listed twice" in e for e in errs) and any("no arm 'ZZ'" in e for e in errs)
    bad = json.loads(json.dumps(camp))
    bad["recommend"]["ablations"].append("N0")                  # N0 is not "T minus one"
    assert any("must build on the composite" in e for e in C.validate_campaign(bad))
    bad = json.loads(json.dumps(camp))
    bad["arms"][0]["time_x"] = 0
    assert any("time_x" in e for e in C.validate_campaign(bad))
    # a decision must not be taken before the arms it compares have run
    old = C.load_campaign("v5-harness")
    with pytest.raises(SystemExit, match="must run earlier"):
        C.run_order(old, prefer=["E"])


def run_tf(tree, *extra):
    return C.main(["run", "--model-name", "Fake-1", "--base-url", "http://127.0.0.1:9/v1",
                   "--jobs", "4", "--slots", "4", "--tree", str(tree), "--python", sys.executable,
                   "--skip-preflight", *extra])


def test_run_chains_the_thinking_first_arms_in_order(tree, tmp_path):
    plan(tmp_path, {"T": {"pass": UNITS}})
    assert run_tf(tree) == 0                                    # --campaign defaults to TF
    cs = calls(tmp_path)
    assert [c["arm"] for c in cs] == TF_ARMS
    by = {c["arm"]: c for c in cs}
    assert by["T"]["argv"][by["T"]["argv"].index("--model-name") + 1] == \
        "Fake-1 v5-thinking-first T"
    assert "--wall-scale" in by["T"]["argv"] and "--wall-scale" not in by["N"]["argv"]
    assert by["T_nopacks"]["env"]["BEAST_PACKS"] == "0" and by["T"]["env"]["BEAST_PACKS"] == "1"
    st = C.load_state(os.path.join(tree, "evals", "results", "campaigns", "v5-thinking-first-fake-1"))
    assert st["arms"]["T"]["score"]["passed"] == 4
    assert run_tf(tree, "--include", "T_s2") == 0               # only the optional arm is new
    assert [c["arm"] for c in calls(tmp_path)][len(TF_ARMS):] == ["T_s2"]


def test_dry_run_prints_an_eta_per_arm(tree, tmp_path, capsys):
    assert run_tf(tree, "--dry-run") == 0
    out = capsys.readouterr().out
    assert calls(tmp_path) == []
    assert "# arm T (" in out and "#   ETA ~5.8 h (time x2.3 of 2.5 h)" in out     # 2.5 h x 2.3
    assert "ETA ~2.5 h (time x1 of 2.5 h)" in out                                  # N0
    assert "# total ETA ~39.0 h" in out and "optional, not in this chain: T_s2" in out
    assert run_tf(tree, "--dry-run", "--base-hours", "2", "--only", "T") == 0
    assert "ETA ~4.6 h" in capsys.readouterr().out
    C.main(["run", "--model-name", "Fake-1", "--base-url", "http://127.0.0.1:9/v1", "--jobs", "2",
            "--tree", str(tree), "--python", sys.executable, "--dry-run", "--only", "N0"])
    # not linear: per-stream decode speeds up as streams drop (contention_scale)
    assert "ETA ~3.3 h (time x1 of 2.5 h, x1.33 for --jobs 2 vs 4)" in capsys.readouterr().out


def test_eta_base_calibrates_from_finished_arms():
    camp = C.load_campaign(TF)
    st = {"arms": {"T": {"status": "done", "started_at": 0, "ended_at": 2.3 * 3 * 3600},
                   "N0": {"status": "done", "started_at": 0, "ended_at": 3 * 3600},
                   "N": {"status": "running", "started_at": 0}}}
    h, why = C.eta_base_h(camp, st, None)
    assert abs(h - 3.0) < 1e-9 and "calibrated from 2" in why
    assert C.eta_base_h(camp, st, 1.5) == (1.5, "--base-hours")
    assert C.eta_base_h(camp, {}, None)[0] == 2.5


# --- 10. decision rules and the thinking-first report --------------------------------------------

def _tf_state(tmp_path, arm_rows):
    camp = C.load_campaign(TF)
    arms = {}
    for aid, (rows, ws) in arm_rows.items():
        p = _results(tmp_path / f"{aid}.json", rows, wall_scale=ws)
        arms[aid] = {"status": "done", "label": aid, "results": [p], "wall_scale": ws,
                     "args": C.resolve_arm(camp, aid)["args"], "tree": {"era": "e1"},
                     "started_at": 0, "ended_at": 3600 * (2.3 if ws > 1 else 1)}
    return {"campaign": TF, "model_name": "Fake-1", "base_url": "http://x", "jobs": 4, "arms": arms}


def _units_rows(n_pass, n=10, secs=100.0, toks=10):
    rows = [_row(f"u{i}", i < n_pass, secs=secs) for i in range(n)]
    for r in rows:
        r["tokens_completion"] = toks
    return rows


def test_decision_rules_drop_what_does_not_help_and_price_thinking(tmp_path):
    camp = C.load_campaign(TF)
    seed = _units_rows(8, secs=500, toks=4000)
    seed[0]["passed"], seed[9]["passed"] = False, True          # 2 flips -> margin ceil(sqrt 2) = 2
    r = {
        "T": (_units_rows(8, secs=500, toks=4000), 2.0),
        "T_noturns": (_units_rows(4, secs=400, toks=3000), 2.0),   # -4: turns help
        "T_nopacks": (_units_rows(7, secs=500, toks=4000), 2.0),   # -1: within noise
        "T_noassist": (_units_rows(8, secs=500, toks=4000), 2.0),  #  0: drop
        "T_s1": (seed, 2.0),
        "N": (_units_rows(3, secs=200, toks=1000), 1.0),           # -5: thinking helps
    }
    st = _tf_state(tmp_path, r)
    rows = {a: C.arm_rows(st["arms"][a]) for a in r}
    rec = C.recommend(camp, st, rows)
    assert rec["margin"] == 2 and "T_s1 vs T: 2 of 10 units flip" in rec["noise"]
    v = {c["arm"]: c["verdict"] for c in rec["components"]}
    assert v == {"T_noturns": "keep", "T_nopacks": "drop", "T_noassist": "drop", "T_noenv": "keep"}
    assert "not measured" in next(c for c in rec["components"] if c["arm"] == "T_noenv")["why"]
    assert rec["thinking"]["verdict"] == "on" and rec["thinking"]["net"] == 5
    assert rec["thinking"]["cost"]["s_ratio"] == 2.5 and rec["thinking"]["cost"]["tokens_ratio"] == 4.0
    sp = rec["spec"]
    assert sp["env"]["BEAST_PACKS"] == "0" and sp["env"]["BEAST_ASSIST"] == "0"
    assert sp["env"]["OPENBEAST_AGENT_ENV_BLOCK"] == "1" and sp["thinking"] == "on"
    assert C.arg_value(sp["args"], "--turns-per-wall-s") == "15"

    md = C.render_report(camp, st, {}, None, walls={f"u{i}": 300 for i in range(10)}, total_units=10)
    section = md.split("## Recommended daily config")[1].split("\n## ")[0]
    assert "**Thinking on**; keep: wall-scaled turns, env block + wall warnings; " \
           "drop: packs v2 + host pack, diagnostics + edit hint + verify-done." in section
    assert "| packs v2 + host pack (T_nopacks) | +1 | 10 |" in section and "**drop**" in section
    assert "Thinking's measured cost (10 common units): 200 s → 500 s per unit (x2.50), " \
           "1000 → 4000 completion tokens per unit (x4.00)." in section
    assert "--thinking on" in section and "BEAST_PACKS=1" not in section
    # T at base wall: 300 s x contention 2.0 = 600 s >= 500 s -> all 8 passes inside
    assert "## Thinking vs no thinking" in md
    assert "| T | on | 2 | 8 | 10 | 8 | 500 | 4000 | 2.30 |" in md
    assert "| N | off | 1 | 3 | 10 | 3 | 200 | 1000 | 1.00 |" in md
    assert "- **N** (thinking off) → **T** (thinking on), 10 common units: 3 → 8 passed" in md


def test_no_thinking_is_recommended_when_thinking_is_within_noise(tmp_path):
    camp = C.load_campaign(TF)
    r = {"T": (_units_rows(6, secs=900, toks=5000), 2.0),
         "N": (_units_rows(5, secs=300, toks=1500), 1.0),
         "N0": (_units_rows(4, secs=250, toks=1200), 1.0)}
    st = _tf_state(tmp_path, r)
    rows = {a: C.arm_rows(st["arms"][a]) for a in r}
    rec = C.recommend(camp, st, rows)
    assert rec["margin"] == 3 and "noise_fallback" in rec["noise"]       # no seed arm measured
    assert rec["thinking"]["verdict"] == "off"
    assert rec["spec"]["thinking"] == "off" and rec["spec"]["wall_scale"] == 1.0
    assert C.arg_value(rec["spec"]["args"], "--reasoning-budget") == "0"
    md = C.render_report(camp, st, {}, None, walls=None)
    assert "**Thinking off**" in md
    assert "| N0 → N |" in md and "| N0 → T |" in md                  # the extra contrasts
    assert "| T | on | 2 | 6 | 10 | - |" in md                         # no unit walls


def test_report_without_the_composite_says_so(tmp_path):
    camp = C.load_campaign(TF)
    st = _tf_state(tmp_path, {"N0": (_units_rows(4), 1.0)})
    md = C.render_report(camp, st, {}, None, walls=None)
    assert "The composite arm `T` has not been measured yet" in md
    assert "## Thinking vs no thinking" not in md


def test_preflight_refuses_an_arm_without_a_finite_reasoning_budget(tree):
    camp = C.load_campaign(TF)
    camp = json.loads(json.dumps({k: v for k, v in camp.items() if k != "_path"}))
    camp["preflight"] = {}
    C.arm_spec(camp, "N0")["args"] += ["--reasoning-budget", "-1"]

    class A:
        base_url, jobs, served_model, model_name, api_key_env = "http://127.0.0.1:9/v1", 1, None, "m", None
    problems = C.preflight(A, camp, sys.executable, str(tree), ["T", "N0"])
    assert any("arm N0 declares no finite --reasoning-budget" in p for p in problems)
    assert not any("arm T " in p for p in problems)


# --- 11. an arm's own concurrency (T_j2, 2026-11-18) -------------------------------------------


def test_jobs_time_scale_follows_contention_not_a_line():
    assert C.jobs_time_scale(4, 4) == 1.0
    assert abs(C.jobs_time_scale(4, 2) - 4 / 3) < 1e-9        # 2.0 vs 1.5 units per time unit
    assert abs(C.jobs_time_scale(4, 1) - 2.0) < 1e-9
    assert C.jobs_time_scale(0, 2) == 1.0


def test_t_j2_is_t_exactly_at_two_jobs_and_off_the_default_chain():
    camp = C.load_campaign(TF)
    t, j2 = C.resolve_arm(camp, "T"), C.resolve_arm(camp, "T_j2")
    assert j2["args"] == t["args"] and j2["env"] == t["env"] and j2["requires"] == t["requires"]
    assert (j2["jobs"], j2["slots"]) == (2, 2) and (t["jobs"], t["slots"]) == (None, None)
    spec = C.arm_spec(camp, "T_j2")
    assert spec["base"] == "T" and spec["optional"] is True
    assert spec["time_x"] == C.arm_spec(camp, "T")["time_x"]   # same tokens per unit
    assert "T_j2" not in C.run_order(camp)
    assert C.run_order(camp, include=["T_j2"])[-1] == "T_j2"


def test_campaign_file_refuses_a_bad_arm_concurrency():
    camp = {k: v for k, v in C.load_campaign(TF).items() if k != "_path"}
    for bad_val in (0, "2", True, 1.5):
        bad = json.loads(json.dumps(camp))
        C.arm_spec(bad, "T_j2")["jobs"] = bad_val
        assert any("T_j2: jobs must be a positive integer" in e for e in C.validate_campaign(bad))


def test_an_arms_own_jobs_win_over_the_cli_in_argv_state_and_reruns(tree, tmp_path):
    plan(tmp_path, {"T_j2": {"pass": ["1_alpha"], "infra": ["3_gamma"]}})
    assert run_tf(tree, "--only", "T_j2") == 0                 # the CLI says --jobs 4 --slots 4
    cs = calls(tmp_path)
    assert [c["arm"] for c in cs] == ["T_j2", "T_j2"]           # main run + the lost-unit rerun
    for c in cs:
        fl = _flagmap(c["argv"])
        assert c["argv"].count("--jobs") == 1 and fl["--jobs"] == "2" and fl["--slots"] == "2"
    st = C.load_state(os.path.join(tree, "evals", "results", "campaigns", "v5-thinking-first-fake-1"))
    a = st["arms"]["T_j2"]
    assert (a["jobs"], a["slots"]) == (2, 2) and st["jobs"] == 4
    assert a["reruns"][0]["jobs"] == 2 and a["reruns"][0]["slots"] == 2
    assert C.arm_jobs(st, "T_j2") == 2
    # the results file run_eval wrote records the --jobs it ran at
    assert json.load(open(a["results"][0]))["jobs"] == 2
    # rerun-lost uses the arm's jobs too, whatever the CLI says
    assert C.main(["rerun-lost", "--model-name", "Fake-1", "--tree", str(tree), "--python",
                   sys.executable, "--jobs", "4", "--arm", "T_j2", "--units", "1_alpha",
                   "--force-units"]) == 0
    assert _flagmap(calls(tmp_path)[-1]["argv"])["--jobs"] == "2"


def test_dry_run_shows_the_arms_jobs_and_scales_its_eta(tree, tmp_path, capsys):
    assert run_tf(tree, "--dry-run", "--only", "T,T_j2") == 0
    out = capsys.readouterr().out
    assert calls(tmp_path) == []
    t_block, j2_block = out.split("# arm T_j2 (")
    assert "#   jobs 4, slots 4\n" in t_block and "ETA ~5.8 h (time x2.3 of 2.5 h)" in t_block
    assert "#   jobs 2, slots 2: the arm's own (the CLI said --jobs 4 --slots 4)" in j2_block
    assert "ETA ~7.7 h (time x2.3 of 2.5 h, x1.33 for --jobs 2 vs 4)" in j2_block
    cmd = j2_block.splitlines()[3]
    assert "--jobs 2 --slots 2" in cmd and "--jobs 4" not in cmd


def test_eta_calibration_brings_an_arm_back_to_the_reference_jobs():
    camp = C.load_campaign(TF)
    # T at 4 jobs took 2.3 x 3 h; T_j2 (same time_x) at 2 jobs took 4/3 of that:
    # both calibrate the same 3.0 h base.
    st = {"jobs": 4, "arms": {"T": {"status": "done", "started_at": 0, "ended_at": 2.3 * 3 * 3600},
                              "T_j2": {"status": "done", "started_at": 0, "jobs": 2,
                                       "ended_at": 2.3 * 3 * 3600 * 4 / 3}}}
    h, _ = C.eta_base_h(camp, st, None)
    assert abs(h - 3.0) < 1e-9
    eta, why = C.arm_eta_h(camp, "T_j2", h, 2)
    assert abs(eta - 9.2) < 1e-9 and "x1.33 for --jobs 2 vs 4" in why


# --- 11. per-model composite: DeepSeek-V4.1-Flash (Mia EXL3) starts from T2 -------------------
#
# T2 (T + 8192-token per-turn cap + act nudge) scored 65/71 against T's 61/71
# (board row DS-T2, 2026-11-19), so it is DeepSeek's best-first composite:
# recommend.composite_by_model. T itself stays as it is (the GLM history).

DS = "DeepSeek-V4.1-Flash"


def test_deepseek_composite_is_t2_every_other_model_keeps_t():
    camp = C.load_campaign(TF)
    assert camp["recommend"]["composite"] == "T"
    for name in (DS, "deepseek-v4.1-flash", "DeepSeek-V4.1-Flash (Mia EXL3)"):
        assert C.composite_for(camp, name)[0] == "T2", name
    for name in ("GLM-5.3-Flash", "Fake-1", "DeepSeek-V4.1-Flashy", None):
        assert C.composite_for(camp, name) == ("T", "recommend.composite"), name
    t2 = C.resolve_arm(camp, "T2")
    assert t2["env"]["OPENBEAST_AGENT_MAX_TOKENS"] == "8192"
    assert t2["env"]["OPENBEAST_AGENT_ACT_NUDGE"] == "1"
    assert {k: t2["env"][k] for k in COMPOSITE_ENV} == COMPOSITE_ENV      # T plus two switches
    assert C.arm_spec(camp, "T2")["base"] == "T"


def test_a_deepseek_chain_runs_t2_first_and_others_are_unchanged():
    camp = C.load_campaign(TF)
    assert C.run_order(camp, model_name=DS) == ["T2"] + TF_ARMS
    assert C.run_order(camp, model_name="Fake-1") == TF_ARMS
    assert C.run_order(camp) == TF_ARMS
    # an explicit --order still decides; T2 stays in the chain
    o = C.run_order(camp, prefer=["T"], model_name=DS)
    assert o[0] == "T" and "T2" in o and len(o) == len(set(o))


def test_campaign_file_refuses_a_bad_per_model_composite():
    camp = {k: v for k, v in C.load_campaign(TF).items() if k != "_path"}
    bad = json.loads(json.dumps(camp))
    bad["recommend"]["composite_by_model"] = {DS: "ZZ"}
    assert any("no arm 'ZZ'" in e for e in C.validate_campaign(bad))
    bad["recommend"]["composite_by_model"] = {DS: "N0"}                 # not built on T
    assert any("must build on the composite T" in e for e in C.validate_campaign(bad))
    assert C.validate_campaign(camp) == []


def test_run_starts_a_deepseek_chain_with_t2(tree, tmp_path):
    # T2 needs a harness9 tree: the fake runner names its switches
    rp = tree / "agents" / "runner.py"
    rp.write_text(rp.read_text() + "# OPENBEAST_AGENT_ACT_NUDGE OPENBEAST_AGENT_STATS_FILE\n")
    plan(tmp_path, {"T2": {"pass": UNITS}, "T": {"pass": UNITS[:3]}})
    assert C.main(["run", "--model-name", DS, "--base-url", "http://127.0.0.1:9/v1",
                   "--jobs", "4", "--slots", "4", "--tree", str(tree), "--python", sys.executable,
                   "--skip-preflight"]) == 0
    assert [c["arm"] for c in calls(tmp_path)] == ["T2"] + TF_ARMS


def test_deepseek_recommendation_starts_from_t2(tmp_path):
    camp = C.load_campaign(TF)
    seed = _units_rows(8, secs=500, toks=4000)
    seed[0]["passed"], seed[9]["passed"] = False, True          # T_s1 vs T: margin 2
    r = {"T": (_units_rows(8, secs=500, toks=4000), 2.0),
         "T2": (_units_rows(9, secs=400, toks=3000), 2.0),
         "T_nopacks": (_units_rows(8, secs=500, toks=4000), 2.0),   # 0 vs T: drop
         "T_noturns": (_units_rows(4, secs=400, toks=3000), 2.0),   # -4 vs T: keep
         "T_s1": (seed, 2.0),
         "N": (_units_rows(3, secs=200, toks=1000), 1.0)}
    st = _tf_state(tmp_path, r)
    st["model_name"] = DS
    rows = {a: C.arm_rows(st["arms"][a]) for a in r}
    rec = C.recommend(camp, st, rows)
    assert rec["composite"] == "T2" and rec["campaign_composite"] == "T"
    assert "composite_by_model" in rec["composite_source"] and not rec["fallback"]
    assert rec["margin"] == 2 and "T_s1 vs T" in rec["noise"]     # T's seed arm stands in
    v = {c["arm"]: c for c in rec["components"]}
    assert v["T_nopacks"]["verdict"] == "drop" and v["T_nopacks"]["net"] == 0   # vs T, not T2
    assert v["T_nopacks"]["why"].endswith("(vs T)")
    assert v["T_noturns"]["verdict"] == "keep"
    assert rec["thinking"]["verdict"] == "on" and rec["thinking"]["net"] == 5  # N vs T
    sp = rec["spec"]
    assert sp["env"]["OPENBEAST_AGENT_MAX_TOKENS"] == "8192"              # T2's own switches
    assert sp["env"]["OPENBEAST_AGENT_ACT_NUDGE"] == "1"
    assert sp["env"]["BEAST_PACKS"] == "0"                               # the dropped component
    assert C.arg_value(sp["args"], "--reasoning-budget") == "8192"
    md = C.render_report(camp, st, {}, None, walls=None)
    section = md.split("## Recommended daily config")[1].split("\n## ")[0]
    assert f"Composite for {DS}: **T2**" in section and "not the campaign's T" in section
    assert "OPENBEAST_AGENT_ACT_NUDGE=1" in section


def test_deepseek_without_t2_falls_back_to_t_and_says_so(tmp_path):
    camp = C.load_campaign(TF)
    st = _tf_state(tmp_path, {"T": (_units_rows(8), 2.0), "N": (_units_rows(3), 1.0)})
    st["model_name"] = DS
    rows = {a: C.arm_rows(st["arms"][a]) for a in st["arms"]}
    rec = C.recommend(camp, st, rows)
    assert rec["composite"] == "T" and "T2" in rec["fallback"]
    md = C.render_report(camp, st, {}, None, walls=None)
    assert "is not measured yet: the campaign composite T stands in." in md
    # and GLM's report is exactly what it was: no per-model line at all
    st["model_name"] = "Fake-1"
    md = C.render_report(camp, st, {}, None, walls=None)
    assert "Composite for" not in md and "stands in" not in md


# --- 12. Hy4-flash (NVFP4): H_T2, the T2-equivalent composite against the Hy4 server ----------
#
# H_T2 is T2 exactly (same switches, same declared 8192 budget); it differs in the
# model only, so its row compares with DS-T2. Thinking on reaches Hy4 as its template's
# default reasoning_effort 'high' (the runner's enable_thinking is ignored there), so no
# era-hashed file changes. served_model keeps it off any other model's server.

HY4 = "Hy4-flash (NVFP4)"


def test_h_t2_is_t2_exactly_for_hy4():
    camp = C.load_campaign(TF)
    h, t2 = C.resolve_arm(camp, "H_T2"), C.resolve_arm(camp, "T2")
    assert h == t2                                              # args, env, requires, jobs, slots
    spec = C.arm_spec(camp, "H_T2")
    assert spec["base"] == "T2" and spec["optional"] is True
    assert spec["jobs"] == 4 and spec["slots"] == 4
    assert spec["model"] == HY4 and spec["served_model"] == "Hy4-flash-NVFP4"
    assert C.arg_value(h["args"], "--thinking") == "on"
    assert C.arg_value(h["args"], "--reasoning-budget") == "8192"
    note = spec["note"]
    assert "public-interest" in note and "DS-T2" in note and "reasoning_effort" in note
    assert "high" in note and "no_think" in note


def test_hy4_composite_is_h_t2_and_deepseek_keeps_t2():
    camp = C.load_campaign(TF)
    for name in (HY4, "Hy4-flash", "hy4-flash-nvfp4"):
        assert C.composite_for(camp, name)[0] == "H_T2", name
    assert C.composite_for(camp, DS)[0] == "T2"
    assert C.composite_for(camp, "Hy4")[0] == "T"               # a slug prefix is not enough
    assert C.run_order(camp, model_name=HY4)[0] == "H_T2"
    assert "H_T2" not in C.run_order(camp)                      # optional: never in the default chain
    assert "H_T2" not in C.run_order(camp, model_name=DS)


def test_an_arm_refuses_another_models_server():
    camp = C.load_campaign(TF)
    probs = C.served_model_mismatch(camp, ["H_T2"], "DeepSeek-V4.1-Flash-1017")
    assert len(probs) == 1 and "beast-spark hy4" in probs[0] and "Hy4-flash-NVFP4" in probs[0]
    assert C.served_model_mismatch(camp, ["H_T2"], "Hy4-flash-NVFP4") == []
    assert C.served_model_mismatch(camp, ["T2", "T"], "DeepSeek-V4.1-Flash-1017") == []   # no served_model: any server
    bad = {k: v for k, v in camp.items() if k != "_path"}
    bad = json.loads(json.dumps(bad))
    next(a for a in bad["arms"] if a["id"] == "H_T2")["served_model"] = ""
    assert any("served_model must be a non-empty string" in e for e in C.validate_campaign(bad))


def test_hy4_thinking_needs_no_era_change():
    """The claim H_T2 rests on: --thinking on sends enable_thinking=true (runner,
    unchanged), which Hy4's template ignores, defaulting to reasoning_effort high."""
    src = (ROOT / "agents" / "runner.py").read_text()
    assert '"enable_thinking": v == "on"' in src
    assert "reasoning_effort" not in src                        # the runner never names it: an era change would


# --- 13. RTX 5090 default (Qwen3.8 27B Uncensored MTP): Q_T2, T2 at one stream --------------
#
# Q_T2 is T2's switches exactly; only jobs/slots differ, because the rig's llama-server
# serves one stream. served_model keeps it off any other server.

Q38 = "Qwen3.8 27B Uncensored MTP Q5_K_M"


def test_q_t2_is_t2_at_one_stream():
    camp = C.load_campaign(TF)
    q, t2 = C.resolve_arm(camp, "Q_T2"), C.resolve_arm(camp, "T2")
    assert q["args"] == t2["args"] and q["env"] == t2["env"] and q.get("requires") == t2.get("requires")
    spec = C.arm_spec(camp, "Q_T2")
    assert spec["base"] == "T2" and spec["optional"] is True
    assert spec["jobs"] == 1 and spec["slots"] == 1
    assert spec["model"] == Q38 and spec["served_model"] == "Qwen3.8 27B Uncensored MTP Q5"
    assert C.arg_value(q["args"], "--reasoning-budget") == "8192"
    assert C.arg_value(q["args"], "--thinking") == "on"


def test_q38_composite_is_q_t2_and_others_keep_theirs():
    camp = C.load_campaign(TF)
    for name in (Q38, "Qwen3.8 27B Uncensored MTP"):
        assert C.composite_for(camp, name)[0] == "Q_T2", name
    assert C.composite_for(camp, DS)[0] == "T2"
    assert C.composite_for(camp, HY4)[0] == "H_T2"
    assert C.composite_for(camp, "Qwen3.8 27B")[0] == "T"        # another Qwen3.8 build is not this row
    assert "Q_T2" not in C.run_order(camp)
    assert C.served_model_mismatch(camp, ["Q_T2"], "DeepSeek-V4.1-Flash-1017")
    assert C.served_model_mismatch(camp, ["Q_T2"], "Qwen3.8 27B Uncensored MTP Q5") == []


# --- 14. Hy4-flash-abliterated (Mix-256G GGUF): HG_T2, H_T2's settings on the GGUF server ------

HG = "Hy4-flash-abliterated (Mix-256G GGUF)"


def test_hg_t2_is_h_t2_on_the_gguf_server():
    camp = C.load_campaign(TF)
    assert C.resolve_arm(camp, "HG_T2") == C.resolve_arm(camp, "H_T2")
    spec = C.arm_spec(camp, "HG_T2")
    assert spec["base"] == "T2" and spec["optional"] is True and spec["jobs"] == 4 and spec["slots"] == 4
    assert spec["model"] == HG and spec["served_model"] == "Hy4-flash-abliterated-Mix-256G"


def test_hg_t2_is_abandoned_so_it_is_no_model_composite():
    """HG_T2 was abandoned 2026-11-23 (16/71 units, serve-speed bound): the arm
    stays with its ABANDONED note, but no model's composite points at it."""
    camp = C.load_campaign(TF)
    assert "HG_T2" not in camp["recommend"]["composite_by_model"].values()
    assert "ABANDONED 2026-11-23" in C.arm_spec(camp, "HG_T2")["note"]
    assert C.composite_for(camp, HY4)[0] == "H_T2"
    assert C.composite_for(camp, "Hy4-flash")[0] == "H_T2"
    assert C.served_model_mismatch(camp, ["HG_T2"], "Hy4-flash-NVFP4")
    assert C.served_model_mismatch(camp, ["HG_T2"], "Hy4-flash-abliterated-Mix-256G") == []


# --- 15. composite_by_model: the longest matching key wins, in any file order ------

def _cbm_camp(cbm):
    camp = copy.deepcopy(C.load_campaign(TF))
    camp["recommend"]["composite_by_model"] = cbm
    return camp


def test_composite_for_takes_the_longest_key_whatever_the_order():
    fwd = _cbm_camp({"Hy4-flash-abliterated": "Q_T2", "Hy4-flash": "H_T2"})
    rev = _cbm_camp({"Hy4-flash": "H_T2", "Hy4-flash-abliterated": "Q_T2"})
    for camp in (fwd, rev):
        assert C.composite_for(camp, "Hy4-flash-abliterated (Mix-256G GGUF)") == \
            ("Q_T2", "recommend.composite_by_model['Hy4-flash-abliterated']")
        assert C.composite_for(camp, HY4)[0] == "H_T2"
        assert C.composite_for(camp, "Hy4")[0] == "T"
        assert C.composite_for(camp, None)[0] == "T"
        assert not [e for e in C.validate_campaign(camp) if "composite_by_model" in e]


def test_validate_refuses_two_keys_with_one_slug():
    camp = _cbm_camp({"Hy4-flash": "H_T2", "hy4 flash": "T2"})
    errs = [e for e in C.validate_campaign(camp) if "same model key" in e]
    assert errs and "hy4-flash" in errs[0]
