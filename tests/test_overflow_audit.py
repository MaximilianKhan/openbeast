#!/usr/bin/env python3
"""evals/audit/overflow_audit.py: how an infra-lost unit's transcript ended.

  - an overflow wording (any engine, Basalt's included) in the final error
    streak, or the runner's context_overflow_unrecoverable stop: overflow
  - a final streak of other errors (the llama-server tool-call parse 500):
    not_overflow, named by class and never by the error's text
  - no error at the end: undetermined
  - end to end on a results file and a logs dir, the proposal is a valid
    overflow.json entry

Run: pytest tests/test_overflow_audit.py
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "evals", "audit"))
sys.path.insert(0, os.path.join(REPO, "evals"))

import overflow_audit as O  # noqa: E402

BASALT = ("Error code: 400 - engine reported request exceeds the context window: prompt "
          "(250000 tokens) + max tokens (16384) exceeds the context (262144)")
PARSE = ("Error code: 500 - Failed to parse tool call arguments as JSON: [json.exception.parse_error.101] "
         "parse error ... last read: 'secret solution text'")


def ev(*types_errors):
    out = []
    for t in types_errors:
        out.append({"type": "error", "error": t} if t not in ("response_model", "unrec")
                   else {"type": "context_overflow_unrecoverable"} if t == "unrec" else {"type": t})
    return out


def test_classify():
    c = O.classify(ev(PARSE, "response_model", BASALT, BASALT))
    assert (c["verdict"], c["errors"], c["tail"]) == ("overflow", 3, 2)
    assert c["cause"] == "overflow: exceeds the context window"
    c = O.classify(ev(BASALT, "response_model", PARSE, PARSE))
    assert c["verdict"] == "not_overflow" and c["cause"].startswith("tool-call JSON parse error")
    assert "secret" not in json.dumps(c)                 # never the error's text
    assert O.classify(ev("response_model", "unrec"))["verdict"] == "overflow"
    assert O.classify(ev(PARSE, "response_model"))["verdict"] == "undetermined"
    assert O.error_class("Error code: 503 - busy") == "HTTP 503"


def test_audit_end_to_end(tmp_path):
    tree = tmp_path / "tree"
    (tree / "evals" / "tasks").mkdir(parents=True)
    (tree / "evals" / "tasks" / "1_x.json").write_text(json.dumps({"id": "1_x", "task": "Do X."}))
    (tree / "evals" / "tasks" / "2_y.json").write_text(json.dumps({"id": "2_y", "task": "Do Y."}))
    logs = tmp_path / "logs"
    logs.mkdir()
    for name, task, errs in (("agent-20261125-010000-aaaaaaaa.jsonl", "Do X.", [BASALT] * 2),
                             ("agent-20261125-011000-bbbbbbbb.jsonl", "Do Y.", [PARSE] * 3)):
        lines = [{"type": "start", "task": task, "timestamp": "2026-11-25T01:00:00"},
                 {"type": "response_model"}] + [{"type": "error", "error": e} for e in errs]
        (logs / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    res = tmp_path / "eval-m-x-20261125-005900.json"
    res.write_text(json.dumps({"timestamp": "2026-11-25T00:59:00", "saved": "2026-11-25T02:00:00", "tasks": [
        {"id": "1_x", "passed": False, "reason": "server_error", "api_errors": 2, "tokens_completion": 9},
        {"id": "2_y", "passed": False, "reason": "server_error", "api_errors": 3, "tokens_completion": 9}]}))
    O.T.set_default_tz("UTC")
    try:
        got = {e["unit"]: e for e in O.audit([str(res)], str(tree), str(logs))}
    finally:
        O.T.set_default_tz(None)
    assert got["1_x"]["verdict"] == "overflow" and got["2_y"]["verdict"] == "not_overflow"
    assert got["1_x"]["file"] == res.name and got["1_x"]["label"] == "server_error"
    assert "2 API errors (row: 2)" in got["1_x"]["evidence"]


def test_audit_main_file_never_matches_its_reruns_transcript(tmp_path):
    """A main file and its repair file under one --until (the campaign's end):
    the main row is matched to its own transcript, not to the rerun's (I_T3)."""
    tree = tmp_path / "tree"
    (tree / "evals" / "tasks").mkdir(parents=True)
    (tree / "evals" / "tasks" / "1_x.json").write_text(json.dumps({"id": "1_x", "task": "Do X."}))
    logs = tmp_path / "logs"
    logs.mkdir()
    for name, ts, errs in (("agent-20261125-010000-aaaaaaaa.jsonl", "2026-11-25T01:00:00", [BASALT] * 2),
                           ("agent-20261125-030000-bbbbbbbb.jsonl", "2026-11-25T03:00:00", [])):
        lines = [{"type": "start", "task": "Do X.", "timestamp": ts},
                 {"type": "response_model"}] + [{"type": "error", "error": e} for e in errs]
        (logs / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    main = tmp_path / "eval-m-x-20261125-005900.json"
    main.write_text(json.dumps({"timestamp": "2026-11-25T00:59:00", "tasks": [
        {"id": "1_x", "passed": False, "reason": "server_error", "api_errors": 2, "tokens_completion": 9}]}))
    rerun = tmp_path / "eval-m-x-20261125-025900.json"
    rerun.write_text(json.dumps({"timestamp": "2026-11-25T02:59:00", "tasks": [
        {"id": "1_x", "passed": False, "api_errors": 0, "tokens_completion": 9}]}))
    O.T.set_default_tz("UTC")
    try:
        got = O.audit([str(main), str(rerun)], str(tree), str(logs),
                      until=O.T.parse_time("2026-11-25T04:00:00"))
    finally:
        O.T.set_default_tz(None)
    assert len(got) == 1 and got[0]["verdict"] == "overflow"
    assert "aaaaaaaa" in got[0]["evidence"]
