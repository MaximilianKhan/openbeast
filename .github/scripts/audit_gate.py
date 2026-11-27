#!/usr/bin/env python3
"""Decide what a pip-audit finding means for THIS change.

    audit_gate.py --label NAME --head HEAD.json [--gate (--base BASE.json | --base-absent)]

HEAD.json / BASE.json are `pip-audit -f json` reports for one requirements
file at the PR head and at its base. A missing or unparsable report means the
audit did not run.

  --gate   the change touched this file: FAIL (exit 1) on any advisory in the
           head report that the base report does not already have — the change
           introduces it (a new package, a downgrade, a bump into a vulnerable
           release). Advisories the base already carries are reported, not
           failed: they are the world's news, not this change's doing.
  --fail-fixable  the scheduled audit: FAIL on any finding that has a fixed
           release (something to do); one with no fix anywhere is a warning,
           so an unfixable advisory does not leave the weekly run red forever.
  default  ADVISORY: report every finding as a warning annotation, exit 0.

WHY. The audit job used to be `continue-on-error: true` and called itself
"advisory", but branch protection requires its check run, and a job-level
continue-on-error only keeps the WORKFLOW run green — the job's own check run
still concludes `failure`. So one advisory published against any locked
package blocked every open PR (PR #141), while a PR that downgraded a
transitive package into a known CVE was not gated by anything required.
Now the check is red exactly when the change itself brings in a vulnerability.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def findings(path: str | None) -> dict | None:
    """{(package, advisory id): "package==version id (fix: …)"}; None when the
    report is missing or not a pip-audit report."""
    if not path or not os.path.isfile(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    deps = data.get("dependencies") if isinstance(data, dict) else data
    if not isinstance(deps, list):
        return None
    out = {}
    for d in deps:
        for v in d.get("vulns") or []:
            fix = ", ".join(v.get("fix_versions") or []) or "none"
            out[(_norm(d.get("name", "?")), v.get("id", "?"))] = (
                f"{d.get('name')}=={d.get('version')} {v.get('id')}"
                + (f" ({', '.join(v['aliases'])})" if v.get("aliases") else "")
                + f" — fix: {fix}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--base")
    ap.add_argument("--base-absent", action="store_true",
                    help="the file does not exist on the base branch (all of it is new)")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--fail-fixable", action="store_true")
    a = ap.parse_args(argv)
    if a.fail_fixable:
        head = findings(a.head)
        if head is None:
            print(f"::error::{a.label}: pip-audit produced no report (the audit did not run)")
            return 1
        fixable = sorted(k for k, line in head.items() if not line.endswith("fix: none"))
        for k in sorted(head):
            print(f"::{'error' if k in fixable else 'warning'}::{a.label}: {head[k]}"
                  + ("" if k in fixable else " (no fixed release yet)"))
        if not head:
            print(f"{a.label}: no known vulnerabilities")
        return 1 if fixable else 0

    head = findings(a.head)
    if head is None:
        level = "error" if a.gate else "warning"
        print(f"::{level}::{a.label}: pip-audit produced no report (the audit did not run)")
        return 1 if a.gate else 0
    base = None
    if a.gate:
        base = {} if a.base_absent else findings(a.base)
        if base is None:
            # The base audit did not run: "new" cannot be told from "old", and
            # a gate that cannot decide does not pass.
            print(f"::error::{a.label}: the base branch's copy could not be audited, so this "
                  "change's findings cannot be told apart from the base's (re-run the job)")
            return 1
    new = sorted(k for k in head if base is None or k not in base) if a.gate else []
    old = sorted(k for k in head if k not in new)

    for k in new:
        print(f"::error::{a.label}: this change introduces {head[k]}")
    for k in old:
        print(f"::warning::{a.label}: {head[k]}"
              + (" (already on the base branch — not this change's doing)" if a.gate else ""))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and head:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {a.label}: {len(head)} advisory finding(s)"
                     f"{f', {len(new)} introduced by this change' if a.gate else ''}\n\n")
            fh.writelines(f"- {'**NEW** ' if k in new else ''}{head[k]}\n" for k in sorted(head))
            fh.write("\n")
    if not head:
        print(f"{a.label}: no known vulnerabilities")
    elif not new:
        print(f"{a.label}: {len(head)} finding(s), none introduced by this change"
              + ("" if a.gate else " (advisory: this change does not touch the file)"))
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
