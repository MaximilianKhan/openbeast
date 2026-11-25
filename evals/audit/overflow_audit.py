#!/usr/bin/env python3
"""Did a lost unit end on a context overflow? Proposes evals/boards/v5/overflow.json entries.

Max's rule (2026-11-25, docs/reports/DECISIONS-2026-11-25.md): a v5 unit that
ends on CONTEXT OVERFLOW is a model loss, never an infrastructure loss. run_eval
rows written before the rule say server_error whenever the run ended on API
errors, and the runner counts an overflow as an API error when its pattern
(agents/runner.py _CTX_OVERFLOW_RE) misses the engine's wording: Basalt's
"exceeds the context window" is one. This tool reads each infra-lost row's
transcript (agents/logs/agent-*.jsonl, matched by evals/audit/transcripts.py)
and says how the run ended:

  overflow       the runner stopped with context_overflow_unrecoverable, or an
                 error in the run's final error streak (the errors after the
                 last reply) carries an overflow wording (v5_board.OVERFLOW_RE:
                 llama.cpp, vLLM, TensorFold, Basalt, the runner)
  not_overflow   the final error streak has errors and none is an overflow
                 (the cause is named by class: a tool-call JSON parse error,
                 HTTP <code>, ...; never the error's text, which can quote the
                 model's solution)
  undetermined   no transcript, or no error at the end (a health-check
                 server_error): read the server log by hand

Usage (on the host that wrote the transcripts, or with a copy of them):
  python3 evals/audit/overflow_audit.py RESULTS.json [MORE.json ...] \\
      [--tree TREE] [--logs-dir DIR] [--tz UTC] [--until ISO] [--json OUT]

--tree is the checkout whose evals/tasks/ the run used (default: this repo);
--logs-dir defaults to TREE/agents/logs. The printed table is the audit; --json
writes the proposed overflow.json "audit" entries (add the server-log evidence
before committing them, then `python3 evals/v5_board.py --write` and --check).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVALS = os.path.dirname(HERE)
REPO = os.path.dirname(EVALS)
sys.path.insert(0, EVALS)
sys.path.insert(0, HERE)

import campaign  # noqa: E402
import transcripts as T  # noqa: E402
import v5_board  # noqa: E402

PARSE_RE = re.compile(r"Failed to parse tool call arguments as JSON")
CODE_RE = re.compile(r"Error code: (\d{3})")
# Events that mean the server answered (a reply): they end an error streak.
REPLY_EVENTS = ("response_model", "assistant")


def error_class(text: str) -> str:
    """A content-free label for one API error's text."""
    m = v5_board.OVERFLOW_RE.search(text or "")
    if m:
        return f"overflow: {m.group(0)}"
    if PARSE_RE.search(text or ""):
        return "tool-call JSON parse error (HTTP 500: the reply's call was cut off)"
    c = CODE_RE.search(text or "")
    if c:
        return f"HTTP {c.group(1)}"
    low = (text or "").lower()
    for k in ("timed out", "timeout", "connection", "refused", "reset"):
        if k in low:
            return f"transport: {k}"
    return "other API error"


def classify(events: list[dict]) -> dict:
    """How a transcript ended: {verdict, cause, errors, tail, tail_classes, unrecoverable}."""
    errors, tail, unrec = 0, [], False
    for e in events:
        t = e.get("type")
        if t == "error":
            errors += 1
            tail.append(error_class(str(e.get("error") or "")))
        elif t in REPLY_EVENTS:
            tail = []
        elif t == "context_overflow_unrecoverable":
            unrec = True
    classes: dict = {}
    for c in tail:
        classes[c] = classes.get(c, 0) + 1
    if unrec or any(c.startswith("overflow") for c in tail):
        verdict = "overflow"
        cause = ("the runner's context_overflow_unrecoverable stop" if unrec
                 else next(c for c in tail if c.startswith("overflow")))
    elif tail:
        verdict = "not_overflow"
        cause = max(classes, key=classes.get)
    else:
        verdict, cause = "undetermined", "no error at the end of the transcript"
    return {"verdict": verdict, "cause": cause, "errors": errors, "tail": len(tail),
            "tail_classes": classes, "unrecoverable": unrec}


def load_units(tree: str) -> dict:
    """{effective unit id: unit dict} from a checkout's evals/tasks/, read as
    data (another tree's run_eval is never imported; as integrity_scan.py)."""
    out = {}
    for p in sorted(glob.glob(os.path.join(tree, "evals", "tasks", "*.json"))):
        try:
            with open(p) as f:
                t = json.load(f)
        except (OSError, ValueError):
            continue
        if t.get("variants"):
            for v in t["variants"]:
                out[f"{t['id']}_{v['id']}"] = {**t, **v, "id": f"{t['id']}_{v['id']}"}
        elif t.get("id"):
            out[t["id"]] = t
    return out


def audit(files: list[str], tree: str, logs_dir: str, until: float | None = None,
          labels: frozenset = v5_board.OVERFLOW_AUDITED) -> list[dict]:
    """One proposed ledger entry per infra row (labels) of the files."""
    units = load_units(tree)
    out = []
    for f in files:
        since, end = T.results_window(f, until)
        _, rows = campaign.load_rows(f)
        lost = {u: r for u, r in rows.items() if v5_board.infra_reason(r) in labels}
        if not lost:
            continue
        idx = T.index_logs(logs_dir, since, end)
        paths = T.match_transcripts({u: units.get(u, {}).get("task") for u in lost}, idx)
        for u, r in sorted(lost.items()):
            p = paths.get(u)
            c = classify(T.read_events(p)) if p else {
                "verdict": "undetermined", "cause": "no transcript matched", "errors": None,
                "tail": None, "tail_classes": {}, "unrecoverable": False}
            row_errs = r.get("api_errors")
            agree = c["errors"] is None or row_errs is None or c["errors"] == row_errs
            ev = (f"transcript {os.path.basename(p)}: {c['errors']} API errors (row: {row_errs}), "
                  f"final streak {c['tail']}: " + ", ".join(f"{n}x {k}" for k, n in c["tail_classes"].items())
                  + ("; context_overflow_unrecoverable" if c["unrecoverable"] else "")
                  ) if p else "no transcript matched the row (window or tree?)"
            if not agree:
                ev += "; the transcript's error count differs from the row's: check the match"
            out.append({"file": os.path.basename(f), "unit": u, "label": v5_board.infra_reason(r),
                        "verdict": c["verdict"] if agree else "undetermined",
                        "cause": c["cause"], "evidence": ev})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", help="run_eval results files")
    ap.add_argument("--tree", default=REPO, help="the checkout the run used (its evals/tasks/)")
    ap.add_argument("--logs-dir", help="the transcripts (default TREE/agents/logs)")
    ap.add_argument("--tz", help="zone of naive timestamps (the writing host's, e.g. UTC)")
    ap.add_argument("--until", help="the run's end (ISO), when the results file records none")
    ap.add_argument("--json", help="write the proposed overflow.json audit entries here")
    a = ap.parse_args(argv)
    T.set_default_tz(a.tz)
    tree = os.path.abspath(a.tree)
    entries = audit(a.files, tree, a.logs_dir or os.path.join(tree, "agents", "logs"),
                    T.parse_time(a.until) if a.until else None)
    print("file | unit | label | verdict | cause")
    for e in entries:
        print(f"{e['file']} | {e['unit']} | {e['label']} | {e['verdict']} | {e['cause']}")
    n = {v: sum(e["verdict"] == v for e in entries) for v in v5_board.OVERFLOW_VERDICTS}
    print(f"{len(entries)} rows: " + ", ".join(f"{k} {v}" for k, v in n.items()))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
