#!/usr/bin/env python3
"""Integrity scan of a reference attempt: did any solver agent read what it
must not (the validators, refs, tests, other runs' transcripts, the ledger
or stashed solutions), run the grader, or use the web?

  python3 evals/reference/integrity_scan.py TRANSCRIPT_DIR \\
      --forbid-from <run-dir>/args.json [--forbid DIR ...] [--json out.json]

TRANSCRIPT_DIR holds the Workflow run's subagent transcripts: for Claude
Code, ~/.claude/projects/<project>/<session>/subagents/workflows/<run id>/
(agent-<id>.jsonl plus agent-<id>.meta.json, whose description is the
agent's label, "solve:<unit>" for a solver). Only solver agents are scanned;
the grader's whole job is the grade command.

Every tool call's input is checked, after removing the unit's own
`reference_attempt.py setup ...` command:
  forbidden   an absolute path under a forbidden directory
  relative    evals/, tests/ or agents/logs/ as a relative path (the solver
              starts in the repo, so these resolve there): flagged for a look
  grade       a `reference_attempt.py grade` call
  web         WebFetch / WebSearch
Exit 1 when anything is flagged. A clean scan is part of the reference row's
claim; a flagged unit is re-run from scratch or dropped, never kept.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

SETUP = re.compile(r"\S*reference_attempt\.py\s+setup\b[^\n;&|]*")
RELATIVE = re.compile(r"(?<![\w/.~-])(?:\./)?(evals|tests|agents/logs)/")
GRADE = re.compile(r"reference_attempt\.py\s+grade\b")
WEB_TOOLS = {"WebFetch", "WebSearch"}
CD = re.compile(r"(?:^|[\s;&|(\"])cd\s+[\"']?([^\s\"';&|)]+)")
SCRATCH_ROOTS = ("/tmp/", "/private/tmp/", "/var/folders/")


def tool_uses(path: str):
    """(id, name, input) of every tool_use in a Claude Code transcript, once each."""
    seen = set()
    with open(path) as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            msg = ev.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tid = c.get("id")
                    if tid in seen:
                        continue
                    seen.add(tid)
                    yield tid, c.get("name"), c.get("input") or {}


def scan_agent(path: str, forbidden: list[str]) -> list[dict]:
    hits = []
    for _tid, name, inp in tool_uses(path):
        text = json.dumps(inp)
        if name in WEB_TOOLS:
            hits.append({"kind": "web", "tool": name, "input": text[:300]})
            continue
        if GRADE.search(text):
            hits.append({"kind": "grade", "tool": name, "input": text[:300]})
        rest = SETUP.sub(" ", text)
        for d in forbidden:
            root = d.rstrip("/")
            if (root + "/") in rest or re.search(re.escape(root) + r"(?![\w.-])", rest):
                hits.append({"kind": "forbidden", "tool": name, "path": d, "input": text[:300]})
                break
        else:
            m = RELATIVE.search(rest)
            # `cd /tmp/eval_x && cat tests/helpers.py` reads the fixture's own
            # tests/: a relative path is only suspect when the command did not
            # move into a scratch or fixture directory first.
            cds = CD.findall(rest)
            if m and cds and all(c.startswith(SCRATCH_ROOTS) for c in cds):
                m = None
            if m:
                hits.append({"kind": "relative", "tool": name, "path": m.group(0),
                             "input": text[:300]})
    return hits


def label_of(jsonl: str) -> str:
    meta = jsonl[:-len(".jsonl")] + ".meta.json"
    try:
        with open(meta) as f:
            return json.load(f).get("description") or ""
    except (OSError, ValueError):
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("transcripts", help="the Workflow run's subagent transcript directory")
    ap.add_argument("--forbid-from", help="the run's args.json (prepare.py): its forbidden list")
    ap.add_argument("--forbid", action="append", default=[])
    ap.add_argument("--all-agents", action="store_true", help="scan every agent, not only solve:*")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    forbidden = list(a.forbid)
    if a.forbid_from:
        with open(a.forbid_from) as f:
            forbidden += json.load(f).get("forbidden") or []
    forbidden = sorted({os.path.abspath(os.path.expanduser(p)) for p in forbidden})
    if not forbidden:
        ap.error("nothing forbidden: pass --forbid-from args.json or --forbid DIR")
    files = sorted(glob.glob(os.path.join(a.transcripts, "agent-*.jsonl")))
    report = {}
    scanned = 0
    for p in files:
        label = label_of(p)
        if not a.all_agents and not label.startswith("solve:"):
            continue
        scanned += 1
        hits = scan_agent(p, forbidden)
        if hits:
            report[label or os.path.basename(p)] = hits
    print(f"scanned {scanned} solver transcripts in {a.transcripts}")
    for label, hits in sorted(report.items()):
        print(f"FLAG {label}:")
        for h in hits[:10]:
            print(f"   {h['kind']:<9} {h.get('tool')}: {h.get('path', '')} {h['input'][:160]}")
    print("CLEAN" if not report else f"{len(report)} solver(s) flagged")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"scanned": scanned, "flagged": report, "forbidden": forbidden}, f, indent=1)
    if scanned == 0:
        print("nothing scanned: is this the right directory (agent-*.jsonl + .meta.json)?")
        return 2
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
