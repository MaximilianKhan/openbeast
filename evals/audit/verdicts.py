#!/usr/bin/env python3
"""Final verdicts of a failure audit, and what to do with each unit.

  python3 evals/audit/verdicts.py AUDIT.json [--out final.json] [--md table.md]

AUDIT.json is either the failure-audit Workflow's return value
([{task, audit: {units, systemic_note}, challenge: {units}}]) or the merged
{"audits": {unit: ...}, "challenges": {unit: ...}} shape of the 2026-11-16
GLM audit. The skeptic's final_verdict wins over the auditor's; where they
disagree the row says so.

What each verdict means for the run (docs/MODEL_CAMPAIGN.md §3):
  TRUE_FAIL       stands. Never re-rolled.
  PROMPT,
  VALIDATOR_BUG   fix the task / validator first (prove it again with
                  tests/prove_task.py; the fix moves the task hash), then
                  rerun the unit.
  HARNESS         fix the harness (an era move for runner/tools), then rerun.
  INFRA, UNCLEAR  rerun as is (indeterminate).
`campaign.py rerun-lost --units ... --verdicts final.json` refuses TRUE_FAIL.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys

ACTION = {
    "TRUE_FAIL": "stands (never re-rolled)",
    "PROMPT": "fix the task text, re-prove, rerun",
    "VALIDATOR_BUG": "fix the validator, re-prove, rerun",
    "HARNESS": "fix the harness, rerun",
    "INFRA": "rerun",
    "UNCLEAR": "rerun (indeterminate)",
}


def load(path: str):
    with open(path) as f:
        return json.load(f)


def final_verdicts(data) -> dict:
    """{unit: {verdict, auditor, skeptic, agree, confidence, root_cause,
    proposed_fix, task, systemic_note}}."""
    out: dict = {}
    if isinstance(data, dict) and isinstance(data.get("result"), list):
        data = data["result"]        # a saved Workflow run record
    if isinstance(data, dict) and ("audits" in data or "challenges" in data):
        aud = data.get("audits") or {}
        ch = data.get("challenges") or {}
        for u in sorted(set(aud) | set(ch)):
            a, c = aud.get(u) or {}, ch.get(u) or {}
            out[u] = _row(u, a, c, None, "")
        return out
    if isinstance(data, dict) and all(isinstance(v, dict) and "verdict" in v for v in data.values()):
        return data          # already final
    for g in data or []:
        if not g:
            continue
        aud = {x["unit"]: x for x in ((g.get("audit") or {}).get("units") or [])}
        ch = {x["unit"]: x for x in ((g.get("challenge") or {}).get("units") or [])}
        note = (g.get("audit") or {}).get("systemic_note", "")
        for u in sorted(set(aud) | set(ch)):
            out[u] = _row(u, aud.get(u) or {}, ch.get(u) or {}, g.get("task"), note)
    return out


def _row(u, a, c, task, note) -> dict:
    final = c.get("final_verdict") or a.get("verdict") or "UNCLEAR"
    return {"unit": u, "task": task, "verdict": final,
            "auditor": a.get("verdict"), "skeptic": c.get("final_verdict"),
            "agree": c.get("agree"), "confidence": a.get("confidence"),
            "root_cause": a.get("root_cause", ""), "proposed_fix": a.get("proposed_fix", ""),
            "skeptic_reasoning": c.get("reasoning", ""), "systemic_note": note,
            "action": ACTION.get(final, "rerun")}


def markdown(final: dict) -> str:
    counts = collections.Counter(v["verdict"] for v in final.values())
    lines = ["| Unit | Final | Auditor | Skeptic agrees | Action | Root cause |",
             "|---|---|---|---|---|---|"]
    for u, v in sorted(final.items()):
        rc = " ".join((v.get("root_cause") or "").split())[:160].replace("|", "\\|")
        lines.append(f"| `{u}` | **{v['verdict']}** | {v.get('auditor') or '-'} | "
                     f"{'yes' if v.get('agree') else ('no' if v.get('agree') is False else '-')} | "
                     f"{v['action']} | {rc} |")
    head = ", ".join(f"{k} {n}" for k, n in counts.most_common())
    rerun = sorted(u for u, v in final.items() if v["verdict"] != "TRUE_FAIL")
    return (f"{len(final)} audited units: {head}.\n\n" + "\n".join(lines) + "\n\n"
            f"Rerun candidates (after their fixes): {', '.join(rerun) or 'none'}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("audit")
    ap.add_argument("--out", help="write the final verdicts JSON here")
    ap.add_argument("--md", help="write the Markdown table here (default: stdout)")
    a = ap.parse_args(argv)
    final = final_verdicts(load(a.audit))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(final, f, indent=1)
    md = markdown(final)
    if a.md:
        with open(a.md, "w") as f:
            f.write(md)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
