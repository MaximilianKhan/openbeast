#!/usr/bin/env python3
"""Prepare a failure audit of one run: the units file the auditors read, the
task groups, and the rendered auditor / skeptic prompts.

  python3 evals/audit/prepare.py RESULTS.json [MORE.json ...] --out-dir DIR \\
      --model "GLM-5.3-Flash" --serving "2x DGX Spark, TensorFold, thinking off" \\
      [--repo TREE] [--logs-dir TREE/agents/logs] \\
      [--env-sh ". ~/.config/openbeast/eval-env.sh"] [--python PY]

Several results files (a main run and its reruns) merge last-wins, except
that an infra row never replaces a real verdict (evals/campaign.py's rule).
Writes, under DIR:

  units.json    one row per unit: passed, transcript, validation_output,
                elapsed_s, wall_s, iterations, max_iter, tokens, task_file,
                dest, language, infra_reason
  groups.json   {base task: [failed unit ids]} (infra rows excluded: rerun
                those, do not audit them)
  args.json     the Workflow script's args: groups, and per group the
                rendered auditor prompt and the skeptic prompt (with a
                {{AUDIT_JSON}} slot the script fills with the auditor's output)
  prompts/      the same prompts as plain files, for any other agent runner
  work/         the auditors' scratch directory (replays go here)

Then run evals/audit/failure-audit.workflow.js with args.json (README.md),
and evals/audit/verdicts.py on its output.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVALS = os.path.dirname(HERE)
sys.path.insert(0, EVALS)
sys.path.insert(0, HERE)

import campaign  # noqa: E402
import transcripts as T  # noqa: E402

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def render(template: str, values: dict, keep=("AUDIT_JSON",)) -> str:
    def sub(m):
        k = m.group(1)
        if k in keep:
            return m.group(0)
        if k not in values:
            raise KeyError(f"template placeholder {{{{{k}}}}} has no value")
        return str(values[k])
    return PLACEHOLDER.sub(sub, template)


def template(name: str) -> str:
    with open(os.path.join(HERE, "prompts", f"{name}.md")) as f:
        return f.read()


def base_walls(repo: str, suite: str, tasks: dict) -> dict:
    try:
        import suite_manifest  # noqa: PLC0415 (the --repo checkout's, via load_task_texts)
        man = suite_manifest.load_manifest(suite, tasks=list(tasks.values()), strict=False)
        units = man.get("by_task") or {}
    except BaseException:  # noqa: BLE001
        units = {}
    out = {}
    for uid, t in tasks.items():
        cap = t.get("max_wall_s") or (units.get(uid) or {}).get("max_wall_s")
        out[uid] = float(cap) if cap else float((t.get("max_iter") or 15) * 60)
    return out


def group_of(uid: str) -> str:
    return uid.split("_", 1)[0]


def build(results: list[str], repo: str, logs_dir: str, tasks: dict, walls: dict) -> list[dict]:
    rows = campaign.merge_rows(results)
    # transcripts: each results file maps the units it holds, inside its own window
    paths: dict = {}
    for rp in results:
        since, until = T.results_window(rp)
        idx = T.index_logs(logs_dir, since, until)
        _, rrows = campaign.load_rows(rp)
        texts = {u: (tasks.get(u) or {}).get("task") for u in rrows}
        paths.update(T.match_transcripts(texts, idx))
    with open(results[0]) as f:
        head = json.load(f)
    jobs = int(head.get("jobs") or 1)
    scale = campaign.contention_scale(jobs) * float(head.get("wall_scale") or 1.0)
    out = []
    for uid in sorted(rows, key=lambda u: (int(group_of(u)) if group_of(u).isdigit() else 0, u)):
        r = rows[uid]
        t = tasks.get(uid) or {}
        out.append({
            "unit": uid, "passed": bool(r.get("passed")),
            "infra_reason": campaign.infra_reason(r),
            "transcript": paths.get(uid),
            "validation_output": (r.get("validation_output") or "")[-3000:],
            "elapsed_s": r.get("elapsed_seconds"),
            "wall_s": round(walls[uid] * scale, 1) if uid in walls else None,
            "iterations": r.get("iterations"), "max_iter": r.get("max_iter"),
            "tokens_total": r.get("tokens_total"), "agent_exit_code": r.get("agent_exit_code"),
            "language": r.get("language") or t.get("language"),
            "task_file": t.get("_path"), "dest": t.get("dest"),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("results", nargs="+")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--repo", default=os.path.dirname(EVALS),
                    help="the tree the run used (task text, validators, replay)")
    ap.add_argument("--logs-dir", help="default <repo>/agents/logs")
    ap.add_argument("--model", required=True)
    ap.add_argument("--serving", default="serving details not given",
                    help="where and how the model was served, e.g. '2x DGX Spark, TensorFold, thinking off'")
    ap.add_argument("--suite", help="default: the results header's suite_manifest")
    ap.add_argument("--python", default=os.environ.get("OPENBEAST_EVAL_PY") or sys.executable)
    ap.add_argument("--env-sh", default="",
                    help="shell prefix that puts the toolchains on PATH, e.g. "
                         "'. ~/.config/openbeast/eval-env.sh'")
    ap.add_argument("--toolchains", default="python 3.12 (the eval interpreter), go, a C/C++ "
                    "compiler, rustc, zig 0.16.0 (with --env-sh applied)")
    ap.add_argument("--readonly", action="append", default=[],
                    help="another path the auditors must not write (repeatable)")
    a = ap.parse_args(argv)

    repo = os.path.abspath(a.repo)
    out = os.path.abspath(a.out_dir)
    os.makedirs(os.path.join(out, "prompts"), exist_ok=True)
    os.makedirs(os.path.join(out, "work"), exist_ok=True)
    with open(a.results[0]) as f:
        head = json.load(f)
    suite = a.suite or head.get("suite_manifest") or head.get("suite_version") or "v5"
    tasks = T.load_task_texts(repo)
    walls = base_walls(repo, suite, tasks)
    units = build([os.path.abspath(p) for p in a.results], repo,
                  a.logs_dir or os.path.join(repo, "agents", "logs"), tasks, walls)
    units_file = os.path.join(out, "units.json")
    with open(units_file, "w") as f:
        json.dump(units, f, indent=1)
    groups: dict = {}
    for u in units:
        if not u["passed"] and not u["infra_reason"]:
            groups.setdefault(group_of(u["unit"]), []).append(u["unit"])
    with open(os.path.join(out, "groups.json"), "w") as f:
        json.dump(groups, f, indent=1)

    prefix = (a.env_sh.strip() + " && ") if a.env_sh.strip() else ""
    replay = f"{prefix}AUDIT_REPO={repo} {a.python} {os.path.join(HERE, 'replay.py')}"
    values = {
        "SUITE": suite, "MODEL": a.model, "SERVING": a.serving, "UNITS_FILE": units_file,
        "REPO": repo, "REPLAY_CMD": replay, "WORK_DIR": os.path.join(out, "work"),
        "TOOLCHAINS": a.toolchains,
        "CONTENTION": f"{campaign.contention_scale(int(head.get('jobs') or 1)):g}"
                      + (f" x the arm's --wall-scale {head['wall_scale']:g}"
                         if head.get("wall_scale") else ""),
        "READONLY_EXTRA": "".join(f", or in {p}" for p in a.readonly),
    }
    common = render(template("common"), values)
    values["COMMON"] = common
    args = {"groups": groups, "auditor": {}, "skeptic": {}}
    for task, us in groups.items():
        v = dict(values, TASK=task, UNITS=", ".join(us))
        args["auditor"][task] = render(template("auditor"), v)
        args["skeptic"][task] = render(template("skeptic"), v)
        for kind in ("auditor", "skeptic"):
            with open(os.path.join(out, "prompts", f"{task}.{kind}.md"), "w") as f:
                f.write(args[kind][task])
    with open(os.path.join(out, "args.json"), "w") as f:
        json.dump(args, f, indent=1)
    failed = sum(len(v) for v in groups.values())
    infra = [u["unit"] for u in units if u["infra_reason"]]
    nomatch = [u["unit"] for u in units if not u["passed"] and not u["transcript"]
               and not u["infra_reason"]]
    print(f"{len(units)} units: {sum(u['passed'] for u in units)} passed, {failed} failed "
          f"in {len(groups)} groups to audit, {len(infra)} infra (rerun, not audit)")
    if nomatch:
        print(f"WARNING: no transcript matched {nomatch} (check --logs-dir and --repo)")
    print(f"wrote {units_file}, {out}/groups.json, {out}/args.json, {out}/prompts/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
