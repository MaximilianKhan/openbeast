"""The failure-audit and reference tooling (evals/audit/, evals/reference/):
verdict normalization, transcript matching, prompt rendering and the
reference integrity scan. No model, no toolchain."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals" / "audit"))
sys.path.insert(0, str(ROOT / "evals" / "reference"))

import integrity_scan  # noqa: E402
import transcripts  # noqa: E402
import verdicts  # noqa: E402


def test_final_verdict_is_the_skeptics_in_both_shapes():
    wf = [{"task": "36", "audit": {"units": [
        {"unit": "36_a", "verdict": "HARNESS", "root_cause": "no timeout cmd"},
        {"unit": "36_b", "verdict": "TRUE_FAIL", "root_cause": "bug"}], "systemic_note": "n"},
        "challenge": {"units": [{"unit": "36_a", "agree": False, "final_verdict": "TRUE_FAIL",
                                 "reasoning": "r"}]}}]
    f = verdicts.final_verdicts(wf)
    assert f["36_a"]["verdict"] == "TRUE_FAIL" and f["36_a"]["auditor"] == "HARNESS"
    assert f["36_b"]["verdict"] == "TRUE_FAIL" and f["36_b"]["task"] == "36"
    merged = {"audits": {"x": {"verdict": "INFRA"}}, "challenges": {"x": {"final_verdict": "INFRA",
                                                                          "agree": True}}}
    g = verdicts.final_verdicts(merged)
    assert g["x"]["verdict"] == "INFRA" and g["x"]["action"] == "rerun"
    assert verdicts.final_verdicts({"result": wf})["36_a"]["verdict"] == "TRUE_FAIL"
    assert verdicts.final_verdicts(f) == f                    # already final
    assert "Rerun candidates (after their fixes): none" in verdicts.markdown(f)


def test_transcripts_match_the_latest_run_of_each_unit_in_the_window(tmp_path):
    def log(name, ts, task):
        (tmp_path / name).write_text(json.dumps({"type": "start", "task": task,
                                                 "timestamp": ts}) + "\n")
    log("agent-1.jsonl", "2026-11-16T10:00:00", "Write  /tmp/eval_a/a.py\nnow")
    log("agent-2.jsonl", "2026-11-16T10:05:00", "Write /tmp/eval_a/a.py now")
    log("agent-3.jsonl", "2026-11-16T09:00:00", "Write /tmp/eval_b/b.go")    # before the window
    since = transcripts._ts("2026-11-16T09:59:00")
    idx = transcripts.index_logs(str(tmp_path), since, since + 3600)
    m = transcripts.match_transcripts({"a": "Write /tmp/eval_a/a.py now", "b": "Write /tmp/eval_b/b.go"},
                                      idx)
    assert m == {"a": str(tmp_path / "agent-2.jsonl")}


def test_prompt_templates_render_and_refuse_unknown_placeholders():
    sys.path.insert(0, str(ROOT / "evals"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("audit_prepare", ROOT / "evals" / "audit" / "prepare.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    out = prep.render("a {{X}} b {{AUDIT_JSON}}", {"X": "1"})
    assert out == "a 1 b {{AUDIT_JSON}}"
    with pytest.raises(KeyError):
        prep.render("{{MISSING}}", {})
    for name in ("common", "auditor", "skeptic"):
        assert "{{" in prep.template(name)


def _transcript(path, label, commands):
    lines = [{"message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}
        for i, (name, inp) in enumerate(commands)]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    path.with_name(path.name[:-len(".jsonl")] + ".meta.json").write_text(
        json.dumps({"description": label}))


def test_integrity_scan_flags_forbidden_reads_and_allows_the_setup(tmp_path):
    repo, run = "/r/openbeast", "/r/openbeast/evals/results/reference/x"
    forbid = [f"{repo}/evals", f"{repo}/tests", run]
    setup = f"cd {repo} && py evals/reference_attempt.py setup u1 --suite v5 --ledger {run}/ledger.jsonl"
    _transcript(tmp_path / "agent-ok.jsonl", "solve:u1", [
        ("Bash", {"command": setup}),
        ("Bash", {"command": "cd /tmp/eval_x && cat tests/helpers.py"}),
        ("Write", {"file_path": "/tmp/eval_x/a.py", "content": "x"})])
    _transcript(tmp_path / "agent-bad.jsonl", "solve:u2", [
        ("Read", {"file_path": f"{repo}/evals/tasks/2_x.json"}),
        ("Bash", {"command": "py evals/reference_attempt.py grade u2"}),
        ("WebFetch", {"url": "https://example.com"})])
    _transcript(tmp_path / "agent-grader.jsonl", "grade:u2", [
        ("Bash", {"command": "py evals/reference_attempt.py grade u2"})])
    assert integrity_scan.scan_agent(str(tmp_path / "agent-ok.jsonl"), forbid) == []
    kinds = {h["kind"] for h in integrity_scan.scan_agent(str(tmp_path / "agent-bad.jsonl"), forbid)}
    assert {"forbidden", "grade", "web"} <= kinds
    out = tmp_path / "scan.json"
    rc = integrity_scan.main([str(tmp_path), "--forbid", forbid[0], "--forbid", run,
                              "--json", str(out)])
    assert rc == 1
    rep = json.loads(out.read_text())
    assert rep["scanned"] == 2 and list(rep["flagged"]) == ["solve:u2"]
