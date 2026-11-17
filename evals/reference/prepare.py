#!/usr/bin/env python3
"""Prepare a reference attempt at a suite: the run directory (ledger,
per-unit state, stashed solutions) and the Workflow args with every solver
and grader prompt rendered.

  python3 evals/reference/prepare.py --suite v5 \\
      [--run-dir evals/results/reference/opus-5.5-v5-<ts>] \\
      [--env-sh ". ~/.config/openbeast/eval-env.sh"] [--python PY] \\
      [--units 36_black_scholes_a,...] [--forbid DIR ...]

Writes <run-dir>/args.json for evals/reference/reference.workflow.js and
prints the commands for the integrity scan and the report. The reference is
model-independent: one ledger per suite version (and per reference model)
serves every campaign's report, so this runs once per suite, not per model.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
EVALS = os.path.dirname(HERE)
REPO = os.path.dirname(EVALS)
PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def render(text: str, values: dict) -> str:
    def sub(m):
        if m.group(1) not in values:
            raise KeyError(f"template placeholder {m.group(0)} has no value")
        return str(values[m.group(1)])
    return PLACEHOLDER.sub(sub, text)


def template(name: str) -> str:
    with open(os.path.join(HERE, "prompts", f"{name}.md")) as f:
        return f.read()


def groups_of(suite: str, repo: str, only: list[str] | None) -> dict:
    with open(os.path.join(repo, "evals", "suites", f"{suite}.json")) as f:
        man = json.load(f)
    groups: dict = {}
    for u in man["units"]:
        if only and u["id"] not in only:
            continue
        groups.setdefault(str(u.get("task") or u["id"].split("_")[0]), []).append(u["id"])
    return groups


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--suite", default="v5")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--run-dir")
    ap.add_argument("--python", default=os.environ.get("OPENBEAST_EVAL_PY") or sys.executable)
    ap.add_argument("--env-sh", default="")
    ap.add_argument("--model", default="claude-opus-5-5", help="recorded on every ledger row")
    ap.add_argument("--scale", type=float, default=2.0,
                    help="validator timeout scale (2.0 = the --jobs 4 rows' scale)")
    ap.add_argument("--units", help="comma-separated subset")
    ap.add_argument("--forbid", action="append", default=[],
                    help="another directory the solver must not read (repeatable)")
    ap.add_argument("--toolchains", default="python 3.12 is the eval interpreter below; go, "
                    "rustc, zig 0.16.0 and clang/gcc are on PATH after the env prefix")
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.repo)
    run_dir = os.path.abspath(a.run_dir or os.path.join(
        repo, "evals", "results", "reference",
        f"{a.model}-{a.suite}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"))
    os.makedirs(run_dir, exist_ok=True)
    ledger = os.path.join(run_dir, "ledger.jsonl")
    prefix = f"cd {shlex.quote(repo)} && " + (a.env_sh.strip() + " && " if a.env_sh.strip() else "")
    forbidden = [os.path.join(repo, "evals"), os.path.join(repo, "tests"),
                 os.path.join(repo, "agents", "logs"), run_dir] + \
        [os.path.abspath(p) for p in a.forbid]
    only = [u.strip() for u in a.units.split(",")] if a.units else None
    groups = groups_of(a.suite, repo, only)
    common = (f"--suite {shlex.quote(a.suite)} --ledger {shlex.quote(ledger)}")
    args = {"groups": groups, "solver": {}, "grader": {}, "ledger": ledger, "repo": repo,
            "forbidden": forbidden}
    for us in groups.values():
        for u in us:
            v = {"UNIT": u, "TOOLCHAINS": f"{a.toolchains} (interpreter: {a.python})",
                 "FORBIDDEN": ", ".join(forbidden), "SCRATCH": f"/tmp/ref-scratch-{u}/",
                 "SETUP_CMD": f"{prefix}{a.python} evals/reference_attempt.py setup {u} {common}",
                 "GRADE_CMD": f"{prefix}{a.python} evals/reference_attempt.py grade {u} {common} "
                              f"--scale {a.scale:g} --model {shlex.quote(a.model)}"}
            args["solver"][u] = render(template("solver"), v)
            args["grader"][u] = render(template("grader"), v)
    with open(os.path.join(run_dir, "args.json"), "w") as f:
        json.dump(args, f, indent=1)
    n = sum(len(v) for v in groups.values())
    print(f"{n} units in {len(groups)} groups; run dir {run_dir}")
    print(f"  Workflow: evals/reference/reference.workflow.js with args {run_dir}/args.json")
    print(f"  Then:     python3 evals/reference/integrity_scan.py <the run's subagent transcript dir> "
          f"--forbid-from {run_dir}/args.json")
    print(f"  Ledger:   {ledger}  (campaign.py report --reference-ledger)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
