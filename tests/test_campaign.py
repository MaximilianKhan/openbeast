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
"""
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
        "# OPENBEAST_DIAG_EDIT_HINT OPENBEAST_AGENT_VERIFY_DONE "
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
