#!/usr/bin/env python3
"""A model campaign: chained harness arms from a declarative file
(docs/MODEL_CAMPAIGN.md is the runbook; evals/campaigns/*.json the files).

The 2026-11-16 GLM-5.3-Flash campaign was a hand-written shell chain
(arm A, then B, C, D, E, F, each one change on top of an earlier arm, each a
full v5 run under a distinct --model-name). This is that chain as code, so
the next model gets the same procedure:

  # every arm, in the file's order, back to back (resumable: re-run the
  # same command after an interruption; finished arms are skipped)
  python3 evals/campaign.py run --campaign v5-harness \\
      --model-name "DeepSeek-v4.1-Flash" \\
      --base-url https://spark-1.tail4109f9.ts.net:8443/v1 \\
      --api-key-env OPENBEAST_SPARK_API_KEY --jobs 4 --slots 4 \\
      --baseline L0=evals/results/eval-deepseek-v4-1-flash-L0-<ts>.json

  python3 evals/campaign.py status     --campaign v5-harness --model-name ...
  python3 evals/campaign.py rerun-lost --campaign v5-harness --model-name ... --arm B
  python3 evals/campaign.py report     --campaign v5-harness --model-name ... \\
      --reference-ledger evals/reference/runs/<run>/ledger.jsonl --out report.md
  python3 evals/campaign.py paths      --campaign v5-harness --model-name ... --arm B

What it enforces (each one a lesson from the GLM night):

  * Infra rows are never scored. A row whose agent never ran (the eval
    interpreter failed to start, "Could not find platform independent
    libraries"; or no tokens and < 2 s), a signal death, a server error, a
    budget skip: the unit is LOST, not failed. `rerun-lost` writes a
    sub-manifest of the lost units (coverage floor relaxed to zero) and
    reruns exactly those with the arm's own flags; the report merges the
    rows (a later infra row never replaces an earlier real verdict).
  * The eval interpreter is checked before every arm (a scratch venv whose
    python symlink was rewritten mid-run cost 22 units of arm B).
  * Arms run from a pinned tree: the commit is recorded when the campaign
    starts and an arm refuses to start if the tree moved (--arm-tree pins a
    different checkout for an arm that needs newer code, and the report
    shows each arm's commit and era).
  * An arm whose flag or switch the tree does not have is refused, not run
    silently without it ("requires" in the campaign file).
  * Decisions (e.g. "thinking on in E iff C beat B") are evaluated on the
    units both arms scored, recorded once, and reused on resume.
  * True fails are never re-rolled: `rerun-lost --units ... --verdicts`
    refuses a unit an audit called TRUE_FAIL.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EVALS_DIR)
CAMPAIGNS_DIR = os.path.join(EVALS_DIR, "campaigns")
STATE_VERSION = 1

# Rows that say nothing about the model: the unit is lost, not failed.
INFRA_REASONS = frozenset({
    "server_error", "env_error", "validator_timeout", "budget_skip",
    "skipped_cache_miss", "server_unhealthy", "low_disk", "setup_failed",
    "interpreter_error",     # run_eval's own name for it once eval-host provisioning (#189) is in
})
# A row the arm did not measure at all (arm scheduler): not lost, not scored.
UNMEASURED_REASONS = frozenset({"not_in_arm"})
# The eval interpreter dying before the agent's first token. 2026-11-16: the
# scratch venv's python symlink was rewritten under a running arm and 22 units
# "failed" in 0.0 s with this in their validation output.
INTERPRETER_STARTUP = re.compile(
    r"Could not find platform (?:in)?dependent libraries"
    r"|Fatal Python error: (?:init|_Py|Py_Initialize)"
    r"|Python path configuration:"
    r"|ModuleNotFoundError: No module named '(?:openai|mcp|httpx)'"
    r"|No module named '(?:openai|mcp|httpx)'"
    r"|bad interpreter|cannot execute binary file|dyld(?:\[\d+\])?: Library not loaded")
FAST_NO_OUTPUT_S = 2.0
TURN_CAP_SLACK = 0          # iterations >= max_iter - slack counts as a turn-cap ending


# --- the campaign file -------------------------------------------------------------

def campaign_path(name: str) -> str:
    if os.path.isfile(name):
        return name
    return os.path.join(CAMPAIGNS_DIR, f"{name}.json")


def validate_campaign(camp: dict) -> list[str]:
    errs = []
    arms = camp.get("arms") or []
    if not arms:
        errs.append("no arms")
    seen: list[str] = []
    decisions = camp.get("decisions") or {}
    for a in arms:
        aid = a.get("id")
        if not aid or not re.fullmatch(r"[A-Za-z0-9_]+", aid):
            errs.append(f"bad arm id {aid!r}")
            continue
        if aid in seen:
            errs.append(f"arm {aid} listed twice")
        base = a.get("base")
        if base and base not in seen:
            errs.append(f"arm {aid}: base {base} must be an earlier arm")
        for d in a.get("apply_decisions") or []:
            rule = decisions.get(d)
            if rule is None:
                errs.append(f"arm {aid}: unknown decision {d!r}")
                continue
            for x in rule.get("compare") or []:
                if x not in seen:
                    errs.append(f"arm {aid}: decision {d} compares {x}, which must run earlier")
        if a.get("noise_of") and a["noise_of"] not in seen:
            errs.append(f"arm {aid}: noise_of {a['noise_of']} must be an earlier arm")
        if not isinstance(a.get("args", []), list) or not isinstance(a.get("env", {}), dict):
            errs.append(f"arm {aid}: args must be a list and env an object")
        seen.append(aid)
    for name, rule in decisions.items():
        cmp_ = rule.get("compare") or []
        if len(cmp_) != 2:
            errs.append(f"decision {name}: compare needs exactly two arms")
        if rule.get("op", ">") not in (">", ">=", "<", "<="):
            errs.append(f"decision {name}: op must be one of > >= < <=")
    common_args = (camp.get("common") or {}).get("args") or []
    for bad in ("--model-name", "--base-url", "--api-key-env", "--jobs", "--slots", "--arms"):
        for a in [camp.get("common") or {}] + arms:
            if bad in (a.get("args") or []):
                errs.append(f"{bad} is set by campaign.py, not the campaign file"
                            + ("" if a is not camp.get("common") else " (common)"))
    if "--suite" not in common_args:
        errs.append("common.args must name the --suite (rerun-lost swaps it for a sub-manifest)")
    return errs


def load_campaign(name: str) -> dict:
    path = campaign_path(name)
    if not os.path.isfile(path):
        raise SystemExit(f"no campaign {name!r} ({path})")
    with open(path) as f:
        camp = json.load(f)
    errs = validate_campaign(camp)
    if errs:
        raise SystemExit(f"campaign {path} refused:\n  " + "\n  ".join(errs))
    camp["_path"] = os.path.abspath(path)
    return camp


def arm_spec(camp: dict, aid: str) -> dict:
    for a in camp["arms"]:
        if a["id"] == aid:
            return a
    raise SystemExit(f"no arm {aid!r} in campaign {camp.get('campaign')}; "
                     f"arms: {[a['id'] for a in camp['arms']]}")


def arm_chain(camp: dict, aid: str) -> list[dict]:
    """The arm and its bases, root first."""
    chain = []
    cur = arm_spec(camp, aid)
    while cur is not None:
        chain.append(cur)
        cur = arm_spec(camp, cur["base"]) if cur.get("base") else None
    return chain[::-1]


def resolve_arm(camp: dict, aid: str, decided: dict | None = None) -> dict:
    """argv (run_eval flags, without the per-campaign ones), env, requirements
    and wall scale of one arm: common + every base + the arm + the branches
    its decisions took. `decided` = {decision name: "then" | "else"}."""
    common = camp.get("common") or {}
    args = list(common.get("args") or [])
    env = dict(common.get("env") or {})
    req = {"flags": list((common.get("requires") or {}).get("flags") or []),
           "env": list((common.get("requires") or {}).get("env") or [])}
    for a in arm_chain(camp, aid):
        args += a.get("args") or []
        env.update(a.get("env") or {})
        r = a.get("requires") or {}
        req["flags"] += r.get("flags") or []
        req["env"] += r.get("env") or []
    for d in arm_spec(camp, aid).get("apply_decisions") or []:
        branch = (decided or {}).get(d)
        if branch not in ("then", "else"):
            raise SystemExit(f"arm {aid}: decision {d} has not been made")
        part = (camp["decisions"][d].get(branch) or {})
        args += part.get("args") or []
        env.update(part.get("env") or {})
        r = part.get("requires") or {}
        req["flags"] += r.get("flags") or []
        req["env"] += r.get("env") or []
    req = {k: sorted(set(v)) for k, v in req.items()}
    return {"args": args, "env": {k: str(v) for k, v in env.items()},
            "requires": req, "wall_scale": wall_scale_of(args)}


def wall_scale_of(args: list[str]) -> float:
    scale = 1.0
    for i, a in enumerate(args):
        if a == "--wall-scale" and i + 1 < len(args):
            scale = float(args[i + 1])
        elif a.startswith("--wall-scale="):
            scale = float(a.split("=", 1)[1])
    return scale


def suite_of(args: list[str]) -> str:
    suite = None
    for i, a in enumerate(args):
        if a == "--suite" and i + 1 < len(args):
            suite = args[i + 1]
        elif a.startswith("--suite="):
            suite = a.split("=", 1)[1]
    if not suite:
        raise SystemExit("the arm's args name no --suite")
    return suite


def with_suite(args: list[str], suite: str) -> list[str]:
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--suite":
            skip = True
            continue
        if a.startswith("--suite="):
            continue
        out.append(a)
    return out + ["--suite", suite]


def arm_model_name(camp: dict, model: str, aid: str) -> str:
    """Distinct per arm, so every arm's rows have their own slug and cache key."""
    return f"{model} {camp['campaign']} {aid}"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"


# --- rows ----------------------------------------------------------------------------

def infra_reason(row: dict) -> str | None:
    """Why a row must not be scored (the unit is lost), or None for a real
    verdict. A pass is always a real verdict."""
    if row.get("passed"):
        return None
    reason = row.get("reason")
    if reason in INFRA_REASONS:
        return reason
    toks = row.get("tokens_completion") or 0
    out = row.get("validation_output") or ""
    if not toks and INTERPRETER_STARTUP.search(out):
        return "interpreter_startup"
    try:
        elapsed = float(row.get("elapsed_seconds") or 0)
    except (TypeError, ValueError):
        elapsed = 0.0
    if not toks and elapsed < FAST_NO_OUTPUT_S:
        return "no_output_fast"
    code = row.get("agent_exit_code")
    # -1 is the harness wall timeout: the model ran out of its time, a
    # verdict. Below that the agent died to a signal (-9 OOM, -15 a stop).
    if isinstance(code, int) and code < -1:
        return "signal_death"
    if (row.get("api_errors") or 0) > 0:
        return "server_error"
    return None


def load_rows(path: str) -> tuple[dict, dict]:
    """(header, {unit id: row}) of a run_eval results file."""
    with open(path) as f:
        d = json.load(f)
    rows = {}
    for r in d.get("tasks") or []:
        if r.get("reason") in UNMEASURED_REASONS:
            continue
        rows[r["id"]] = r
    return {k: v for k, v in d.items() if k != "tasks"}, rows


def merge_rows(paths: list[str]) -> dict:
    """Rows of a main run and its reruns, later files winning, except that a
    later infra row never replaces an earlier real verdict."""
    merged: dict = {}
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        _, rows = load_rows(p)
        for uid, r in rows.items():
            old = merged.get(uid)
            if old is not None and infra_reason(r) and not infra_reason(old):
                continue
            merged[uid] = r
    return merged


def scorable(rows: dict) -> dict:
    return {u: r for u, r in rows.items()
            if not infra_reason(r) and (r.get("role") or "scored") == "scored"}


def lost_units(rows: dict, expected: list[str]) -> dict:
    """{unit: why} for every expected unit with no real verdict."""
    out = {}
    for u in expected:
        r = rows.get(u)
        if r is None:
            out[u] = "missing"
        else:
            why = infra_reason(r)
            if why:
                out[u] = why
    return out


def score(rows: dict) -> dict:
    s = scorable(rows)
    passed = sum(1 for r in s.values() if r.get("passed"))
    iters = [r["iterations"] for r in s.values() if isinstance(r.get("iterations"), (int, float))]
    secs = [float(r["elapsed_seconds"]) for r in s.values()
            if isinstance(r.get("elapsed_seconds"), (int, float))]
    capped = sum(1 for r in s.values()
                 if not r.get("passed") and isinstance(r.get("iterations"), int)
                 and isinstance(r.get("max_iter"), int)
                 and r["iterations"] >= r["max_iter"] - TURN_CAP_SLACK)
    timeouts = sum(1 for r in s.values() if not r.get("passed") and r.get("agent_exit_code") == -1)
    return {"passed": passed, "scorable": len(s),
            "mean_turns": (sum(iters) / len(iters)) if iters else None,
            "mean_s": (sum(secs) / len(secs)) if secs else None,
            "turn_cap_ends": capped, "wall_timeouts": timeouts}


def flips(a: dict, b: dict) -> dict:
    """Per-unit flips from a to b on the units both scored."""
    sa, sb = scorable(a), scorable(b)
    common = sorted(set(sa) & set(sb))
    rescued = [u for u in common if not sa[u].get("passed") and sb[u].get("passed")]
    regressed = [u for u in common if sa[u].get("passed") and not sb[u].get("passed")]
    return {"common": len(common), "rescued": rescued, "regressed": regressed,
            "net": len(rescued) - len(regressed),
            "a_passed": sum(1 for u in common if sa[u].get("passed")),
            "b_passed": sum(1 for u in common if sb[u].get("passed")),
            "p": sign_test_p(len(rescued), len(regressed))}


def sign_test_p(b: int, c: int) -> float:
    """Two-sided exact sign test on the discordant pairs (McNemar exact)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def decide(rule: dict, rows_by_arm: dict) -> tuple[str, str]:
    """("then" | "else", why) for a decision rule on measured arms."""
    x, y = rule["compare"]
    op = rule.get("op", ">")
    if not rows_by_arm.get(x) or not rows_by_arm.get(y):
        missing = [a for a in (x, y) if not rows_by_arm.get(a)]
        return "else", f"{', '.join(missing)} not measured: took the else branch"
    f = flips(rows_by_arm[y], rows_by_arm[x])        # y -> x
    px, py = f["b_passed"], f["a_passed"]
    margin = int(rule.get("margin") or 0)
    ok = {">": px > py + margin, ">=": px >= py + margin,
          "<": px < py - margin, "<=": px <= py - margin}[op]
    why = (f"{x} {px} vs {y} {py} passed on the {f['common']} units both scored "
           f"(op {op}{f' margin {margin}' if margin else ''})")
    return ("then" if ok else "else"), why


# --- the suite and its sub-manifests ----------------------------------------------------

def suite_units(tree: str, suite: str) -> list[dict]:
    path = os.path.join(tree, "evals", "suites", f"{suite}.json")
    with open(path) as f:
        man = json.load(f)
    return man.get("units") or []


def expected_units(tree: str, suite: str, scored_only: bool = False) -> list[str]:
    return [u["id"] for u in suite_units(tree, suite)
            if not scored_only or (u.get("role") or "scored") == "scored"]


def write_sub_manifest(tree: str, base_suite: str, units: list[str], name: str,
                       note: str) -> str:
    """A copy of the base manifest restricted to `units`, coverage floor
    relaxed to zero (a handful of lost units covers no floor). Its `suite`
    field stays the base suite's, so the results carry the same suite
    version. Untracked (.gitignore: evals/suites/_lost-*.json)."""
    src = os.path.join(tree, "evals", "suites", f"{base_suite}.json")
    with open(src) as f:
        man = json.load(f)
    want = set(units)
    man["units"] = [u for u in man.get("units") or [] if u["id"] in want]
    missing = want - {u["id"] for u in man["units"]}
    if missing:
        raise SystemExit(f"units not in {base_suite}: {sorted(missing)}")
    man["description"] = f"TEMP (untracked, campaign.py): {note}"
    floor = man.get("coverage_floor") or {}
    man["coverage_floor"] = {k: 0 for k in floor} or {"cell_min": 0, "categories": 0,
                                                       "languages": 0}
    dst = os.path.join(tree, "evals", "suites", f"{name}.json")
    with open(dst, "w") as f:
        json.dump(man, f, indent=1)
        f.write("\n")
    return name


# --- the tree and the interpreter ----------------------------------------------------------

def _run(cmd: list[str], cwd: str, env: dict | None = None, timeout: float = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 99, f"{type(e).__name__}: {e}"


def tree_info(tree: str, py: str) -> dict:
    rc, head = _run(["git", "rev-parse", "HEAD"], tree)
    rc2, dirty = _run(["git", "status", "--porcelain", "--untracked-files=no"], tree)
    rc3, era = _run([py, "-c", "import sys; sys.path.insert(0, 'evals'); import cache; "
                     "print(cache.context_hash())"], tree)
    return {"path": os.path.abspath(tree),
            "commit": head.strip() if rc == 0 else None,
            "dirty": bool(dirty.strip()) if rc2 == 0 else None,
            "era": era.strip().splitlines()[-1] if rc3 == 0 and era.strip() else None}


def interpreter_problem(py: str, tree: str, env: dict) -> str | None:
    """None when the eval interpreter starts and imports what the runner
    needs; else what broke. Checked before every arm and every rerun."""
    rc, out = _run([py, "-c", "import json, openai, sys; print(sys.version.split()[0])"], tree, env, 120)
    if rc != 0:
        return f"{py} cannot import openai (rc {rc}): {out.strip()[-400:]}"
    if INTERPRETER_STARTUP.search(out):
        return f"{py} starts with an interpreter error: {out.strip()[-400:]}"
    return None


_HELP_CACHE: dict = {}


def missing_requirements(tree: str, py: str, req: dict) -> list[str]:
    """Flags run_eval does not accept and env switches no harness file reads."""
    out = []
    if req.get("flags"):
        key = (tree, py)
        if key not in _HELP_CACHE:
            _HELP_CACHE[key] = _run([py, "evals/run_eval.py", "--help"], tree, None, 120)[1]
        help_text = _HELP_CACHE[key]
        out += [f"run_eval has no {f}" for f in req["flags"] if f not in help_text]
    if req.get("env"):
        text = ""
        for rel in ("agents/runner.py", "agents/tools.py", "evals/run_eval.py"):
            try:
                with open(os.path.join(tree, rel)) as f:
                    text += f.read()
            except OSError:
                pass
        out += [f"no harness file reads {v}" for v in req["env"] if v not in text]
    return out


# --- state -----------------------------------------------------------------------------------

def state_dir_for(tree: str, camp: dict, model: str) -> str:
    return os.path.join(tree, "evals", "results", "campaigns",
                        f"{camp['campaign']}-{slugify(model)}")


def load_state(sdir: str) -> dict | None:
    p = os.path.join(sdir, "state.json")
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        return json.load(f)


def save_state(sdir: str, st: dict) -> None:
    os.makedirs(sdir, exist_ok=True)
    p = os.path.join(sdir, "state.json")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, p)


def clog(sdir: str, msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "campaign.log"), "a") as f:
        f.write(line + "\n")


def arm_paths(arm_state: dict) -> list[str]:
    return list(arm_state.get("results") or []) + \
        [r["results"] for r in arm_state.get("reruns") or [] if r.get("results")]


def arm_rows(arm_state: dict | None) -> dict:
    return merge_rows(arm_paths(arm_state)) if arm_state else {}


# --- running run_eval ------------------------------------------------------------------------

SAVED = re.compile(r"^Saved to: (.+\.json)\s*$", re.M)


def run_eval_once(py: str, tree: str, argv: list[str], env: dict, log_path: str,
                  slug: str) -> tuple[int, list[str]]:
    """One run_eval process. Returns (rc, results files it wrote): the
    'Saved to:' lines, else (an interrupted run prints none) the newest
    eval-<slug>-*.json written since it started."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    start = time.time()
    with open(log_path, "a") as lf:
        offset = lf.tell()
        lf.write(f"\n# {datetime.now().isoformat()} {shlex.join([py, '-u', 'evals/run_eval.py', *argv])}\n")
        lf.flush()
        try:
            rc = subprocess.call([py, "-u", "evals/run_eval.py", *argv], cwd=tree, env=env,
                                 stdout=lf, stderr=subprocess.STDOUT)
        except KeyboardInterrupt:
            rc = 130
    with open(log_path) as f:
        f.seek(offset)
        text = f.read()
    paths = []
    for m in SAVED.finditer(text):
        p = m.group(1).strip()
        p = p if os.path.isabs(p) else os.path.join(tree, p)
        if p not in paths:
            paths.append(p)
    if not paths:
        cands = [p for p in glob.glob(os.path.join(tree, "evals", "results", f"eval-{slug}-*.json"))
                 if os.path.getmtime(p) >= start - 1]
        if cands:
            paths = [max(cands, key=os.path.getmtime)]
    return rc, paths


def base_env(camp: dict, spec: dict, a, served: str | None) -> dict:
    env = dict(os.environ)
    env.update(spec["env"])
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["OPENBEAST_EVAL_ENV_ERA"] = env.get("OPENBEAST_EVAL_ENV_ERA") or "1"
    if served and not env.get("OPENBEAST_INFERENCE_MODEL"):
        env["OPENBEAST_INFERENCE_MODEL"] = served
    return env


def campaign_argv(spec_args: list[str], a, model_name: str) -> list[str]:
    argv = list(spec_args) + ["--model-name", model_name, "--base-url", a.base_url,
                              "--jobs", str(a.jobs)]
    if a.slots:
        argv += ["--slots", str(a.slots)]
    if a.api_key_env:
        argv += ["--api-key-env", a.api_key_env]
    return argv


def do_rerun(camp: dict, st: dict, sdir: str, aid: str, units: list[str], a, py: str,
             served: str | None, why: str) -> int:
    """Rerun `units` of arm `aid` through a sub-manifest, same flags and env."""
    ast = st["arms"][aid]
    spec = resolve_arm(camp, aid, ast.get("decisions_taken"))
    tree = ast["tree"]["path"]
    base_suite = suite_of(spec["args"])
    k = len(ast.get("reruns") or []) + 1
    name = f"_lost-{camp['campaign']}-{slugify(a.model_name)}-{aid}-r{k}"
    env = base_env(camp, spec, a, served)
    model_name = arm_model_name(camp, a.model_name, aid)
    argv = campaign_argv(with_suite(spec["args"], name), a, model_name)
    if a.dry_run:
        print(shlex.join([py, "-u", "evals/run_eval.py", *argv]))
        return 0
    bad = interpreter_problem(py, tree, env)
    if bad:
        clog(sdir, f"ABORT rerun {aid}: eval interpreter broken: {bad}")
        return 3
    write_sub_manifest(tree, base_suite, units, name, f"{aid}: {why} ({len(units)} units)")
    clog(sdir, f"RERUN {aid} r{k}: {len(units)} units ({why}): {', '.join(units)}")
    rc, paths = run_eval_once(py, tree, argv, env, os.path.join(sdir, f"arm-{aid}-r{k}.log"),
                              slugify(model_name))
    ast.setdefault("reruns", []).append({"round": k, "suite": name, "units": units, "why": why,
                                         "rc": rc, "results": paths[0] if paths else None,
                                         "ended_at": time.time()})
    save_state(sdir, st)
    clog(sdir, f"END rerun {aid} r{k} rc={rc} -> {paths[0] if paths else 'no results'}")
    return 0


def finish_arm(camp: dict, st: dict, sdir: str, aid: str) -> dict:
    ast = st["arms"][aid]
    rows = arm_rows(ast)
    exp = expected_units(ast["tree"]["path"], suite_of(resolve_arm(
        camp, aid, ast.get("decisions_taken"))["args"]))
    lost = lost_units(rows, exp)
    ast["lost"] = lost
    ast["score"] = score(rows)
    return lost


def cmd_run(a, camp: dict) -> int:
    py = a.python
    tree = os.path.abspath(a.tree)
    sdir = a.state_dir or state_dir_for(tree, camp, a.model_name)
    st = load_state(sdir) or {
        "version": STATE_VERSION, "campaign": camp["campaign"], "campaign_file": camp["_path"],
        "model_name": a.model_name, "base_url": a.base_url, "jobs": a.jobs, "slots": a.slots,
        "created_at": time.time(), "tree": None, "arms": {}, "baselines": {}}
    for b in a.baseline or []:
        name, _, path = b.partition("=")
        st["baselines"][name] = os.path.abspath(path)
    order = [x["id"] for x in camp["arms"]]
    if a.only:
        todo = [arm_spec(camp, x)["id"] for x in a.only.split(",")]
    elif a.from_arm:
        todo = order[order.index(arm_spec(camp, a.from_arm)["id"]):]
    else:
        todo = order
    arm_trees = {}
    for x in a.arm_tree or []:
        k, _, v = x.partition("=")
        arm_trees[arm_spec(camp, k)["id"]] = os.path.abspath(v)

    served = a.served_model
    if not a.dry_run and not a.skip_preflight:
        problems = preflight(a, camp, py, tree)
        if problems:
            print("PREFLIGHT REFUSED:")
            for p in problems:
                print(f"  - {p}")
            print("(--skip-preflight to run anyway)")
            return 2
    if not a.dry_run and served is None and not a.skip_preflight:
        served = detect_served(a.base_url)
    served = served or st.get("served_model")
    if not a.dry_run:
        st["served_model"] = served
        if st["tree"] is None:
            st["tree"] = tree_info(tree, py)
        save_state(sdir, st)
        clog(sdir, f"CAMPAIGN {camp['campaign']} model={a.model_name!r} arms={todo} "
                   f"tree={tree} commit={(st['tree'] or {}).get('commit')}")

    for aid in todo:
        ast = st["arms"].get(aid) or {}
        if ast.get("status") == "done" and not a.only:
            if not a.dry_run:
                sc = ast.get("score") or {}
                clog(sdir, f"SKIP {aid}: done ({sc.get('passed')}/{sc.get('scorable')})")
            continue
        atree = arm_trees.get(aid) or (ast.get("tree") or {}).get("path") or tree
        # decisions: made once, reused on resume
        taken = dict(ast.get("decisions_taken") or {})
        for d in arm_spec(camp, aid).get("apply_decisions") or []:
            if d in taken:
                continue
            forced = dict(x.split("=", 1) for x in a.set_decision or [])
            if d in forced:
                taken[d] = forced[d]
                why = "set on the command line"
            else:
                rows_by = {x: arm_rows(st["arms"].get(x)) for x in camp["decisions"][d]["compare"]}
                taken[d], why = decide(camp["decisions"][d], rows_by)
            if not a.dry_run:
                clog(sdir, f"DECISION {d} for {aid}: {taken[d]} ({why})")
            ast.setdefault("decision_notes", {})[d] = why
        spec = resolve_arm(camp, aid, taken)
        model_name = arm_model_name(camp, a.model_name, aid)
        argv = campaign_argv(spec["args"], a, model_name)
        env = base_env(camp, spec, a, served)
        if a.dry_run:
            envs = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(spec["env"].items()))
            print(f"# arm {aid} ({arm_spec(camp, aid).get('label', '')}) tree={atree}")
            print(f"{envs} {shlex.join([py, '-u', 'evals/run_eval.py', *argv])}".strip())
            continue
        miss = missing_requirements(atree, py, spec["requires"])
        if miss:
            ast.update({"status": "unsupported", "unsupported": miss, "decisions_taken": taken})
            st["arms"][aid] = ast
            save_state(sdir, st)
            clog(sdir, f"REFUSED {aid}: the tree lacks {miss} (merge the PR that adds it, or "
                       f"--arm-tree {aid}=<checkout that has it>)")
            continue
        info = tree_info(atree, py)
        if info.get("dirty"):
            clog(sdir, f"WARNING {aid}: {atree} has uncommitted changes to tracked files; "
                       f"the arm's commit does not describe its code")
        same_tree = os.path.abspath(atree) == (st["tree"] or {}).get("path")
        if same_tree and st["tree"].get("commit") and info.get("commit") and \
                info["commit"] != st["tree"]["commit"] and not a.allow_tree_move:
            clog(sdir, f"ABORT {aid}: the tree moved since the campaign started "
                       f"({st['tree']['commit'][:10]} -> {info['commit'][:10]}); arms would mix "
                       f"code. --arm-tree {aid}=DIR pins a checkout, --allow-tree-move accepts it")
            return 4
        bad = interpreter_problem(py, atree, env)
        if bad:
            clog(sdir, f"ABORT {aid}: eval interpreter broken: {bad}")
            return 3
        resumed = ast.get("status") in ("running", "incomplete") and any(
            os.path.isfile(p) for p in ast.get("results") or [])
        if not resumed and (ast.get("results") or ast.get("reruns")):
            # A fresh run of an arm that ran before (--only): the old rows
            # are history, never merged into the new run's.
            ast.setdefault("history", []).append(
                {k: ast.get(k) for k in ("results", "reruns", "rc", "score", "lost",
                                         "started_at", "ended_at", "status", "tree")})
            for k in ("results", "reruns", "rc", "score", "lost", "started_at", "ended_at"):
                ast.pop(k, None)
        ast.update({"status": "running", "label": arm_spec(camp, aid).get("label"),
                    "model_name": model_name, "tree": info, "decisions_taken": taken,
                    "args": spec["args"], "env": spec["env"], "wall_scale": spec["wall_scale"]})
        ast.setdefault("started_at", time.time())
        st["arms"][aid] = ast
        save_state(sdir, st)
        if resumed:
            clog(sdir, f"RESUME {aid}: banked rows kept; the missing units rerun as lost")
        else:
            clog(sdir, f"START {aid} ({ast['label']}) tree={atree}")
            rc, paths = run_eval_once(py, atree, argv, env, os.path.join(sdir, f"arm-{aid}.log"),
                                      slugify(model_name))
            ast["rc"] = rc
            ast["results"] = paths
            save_state(sdir, st)
            sc = score(arm_rows(ast))
            clog(sdir, f"END {aid} rc={rc} {sc['passed']}/{sc['scorable']} scorable "
                       f"-> {paths[0] if paths else 'no results'}")
            if rc in (130, -2, -15, 143):
                clog(sdir, f"INTERRUPTED {aid}: run the same command again to resume "
                           f"(banked rows are kept, the rest rerun as lost)")
                return 130
            if rc not in (0, 1) and not paths:
                clog(sdir, f"ABORT {aid}: run_eval exited {rc} without a results file")
                return 5
        lost = finish_arm(camp, st, sdir, aid)
        for _ in range(int((camp.get("rerun_lost") or {}).get("rounds", 1))):
            if not lost:
                break
            rc = do_rerun(camp, st, sdir, aid, sorted(lost), a, py, served,
                          "lost to infra: " + ", ".join(sorted(set(lost.values()))))
            if rc:
                return rc
            lost = finish_arm(camp, st, sdir, aid)
        ast["ended_at"] = time.time()
        max_frac = float((camp.get("rerun_lost") or {}).get("max_lost_fraction", 0.05))
        n_exp = len(expected_units(atree, suite_of(spec["args"])))
        if lost and len(lost) > max_frac * n_exp:
            ast["status"] = "incomplete"
            save_state(sdir, st)
            clog(sdir, f"STOP after {aid}: {len(lost)}/{n_exp} units still lost "
                       f"({sorted(set(lost.values()))}); fix the cause, then "
                       f"`campaign.py rerun-lost --arm {aid}` and `run` again")
            return 6
        ast["status"] = "done"
        save_state(sdir, st)
        sc = ast["score"]
        clog(sdir, f"DONE {aid}: {sc['passed']}/{sc['scorable']} scorable"
                   + (f", {len(lost)} lost" if lost else ""))
    if not a.dry_run:
        clog(sdir, "CAMPAIGN CHAIN DONE" if all(
            (st["arms"].get(x) or {}).get("status") in ("done", "unsupported") for x in order)
            else f"chain stopped; state {sdir}/state.json")
    return 0


def detect_served(base_url: str) -> str | None:
    try:
        sys.path.insert(0, EVALS_DIR)
        sys.path.insert(0, os.path.join(REPO, "agents"))
        import run_eval  # noqa: PLC0415
        m = run_eval.detect_model(base_url)
        return None if m == "unknown" else m
    except Exception:  # noqa: BLE001
        return None


def preflight(a, camp: dict, py: str, tree: str) -> list[str]:
    """The prerequisites of docs/MODEL_CAMPAIGN.md §1 that can be checked
    from here: the host toolchains (evals/profile.py's preflight, or the
    provisioning script when the tree has one), and the gate admitting
    --jobs concurrent requests. Proof (tests/prove_task.py --suite) and the
    gate's read timeout are the runbook's to check."""
    problems = []
    hook = (camp.get("preflight") or {}).get("host_check")
    if hook and os.path.isfile(os.path.join(tree, hook.split()[0])):
        rc, out = _run(shlex.split(hook), tree, None, 600)
        if rc != 0:
            problems.append(f"`{hook}` failed (rc {rc}): {out.strip()[-600:]}")
    # profile.py's checks always: toolchains, reasoning budget, admission.
    sys.path.insert(0, EVALS_DIR)
    sys.path.insert(0, os.path.join(REPO, "agents"))
    # admission_check authenticates with OPENBEAST_API_KEY: without the key a
    # keyed gate answers 401, never 429, and the check would pass blind.
    saved_key = os.environ.get("OPENBEAST_API_KEY")
    if a.api_key_env and os.environ.get(a.api_key_env):
        os.environ["OPENBEAST_API_KEY"] = os.environ[a.api_key_env]
    try:
        import importlib.util  # noqa: PLC0415
        # evals/profile.py by path: the name `profile` may already be the
        # stdlib module in sys.modules.
        spec = importlib.util.spec_from_file_location(
            "_ob_profile", os.path.join(EVALS_DIR, "profile.py"))
        prof = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prof)
        rb = None
        args = (camp.get("common") or {}).get("args") or []
        if "--reasoning-budget" in args:
            rb = args[args.index("--reasoning-budget") + 1]
        problems += prof.preflight(a.base_url, rb, False)
        adm = prof.admission_check(a.base_url, a.jobs, a.served_model or a.model_name)
        if adm:
            problems.append(adm)
    except Exception as e:  # noqa: BLE001
        problems.append(f"preflight could not run ({type(e).__name__}: {e})")
    finally:
        if saved_key is None:
            os.environ.pop("OPENBEAST_API_KEY", None)
        else:
            os.environ["OPENBEAST_API_KEY"] = saved_key
    bad = interpreter_problem(py, tree, dict(os.environ))
    if bad:
        problems.append(f"eval interpreter: {bad}")
    return problems


def cmd_rerun_lost(a, camp: dict) -> int:
    tree = os.path.abspath(a.tree)
    sdir = a.state_dir or state_dir_for(tree, camp, a.model_name)
    st = load_state(sdir)
    if not st or a.arm not in st.get("arms", {}):
        raise SystemExit(f"no state for arm {a.arm} in {sdir}")
    a.base_url = a.base_url or st["base_url"]
    a.jobs = a.jobs or st.get("jobs") or 1
    a.slots = a.slots or st.get("slots")
    ast = st["arms"][a.arm]
    if a.units:
        units = [u.strip() for u in a.units.split(",") if u.strip()]
        why = "selected after audit"
        if a.verdicts:
            sys.path.insert(0, os.path.join(EVALS_DIR, "audit"))
            import verdicts as V  # noqa: PLC0415
            final = V.final_verdicts(V.load(a.verdicts))
            true_fails = [u for u in units if (final.get(u) or {}).get("verdict") == "TRUE_FAIL"]
            if true_fails:
                raise SystemExit(f"refused: {true_fails} were audited TRUE_FAIL; a true fail is "
                                 f"never re-rolled (docs/MODEL_CAMPAIGN.md §3)")
        elif not a.force_units:
            raise SystemExit("--units without --verdicts: pass the audit's verdicts file so a "
                             "TRUE_FAIL cannot be re-rolled, or --force-units if there was no audit")
    else:
        lost = finish_arm(camp, st, sdir, a.arm)
        units = sorted(lost)
        why = "lost to infra: " + ", ".join(sorted(set(lost.values()))) if lost else ""
    if not units:
        print(f"arm {a.arm}: nothing lost")
        return 0
    rc = do_rerun(camp, st, sdir, a.arm, units, a, a.python,
                  a.served_model or st.get("served_model"), why)
    if rc or a.dry_run:
        return rc
    lost = finish_arm(camp, st, sdir, a.arm)
    if ast.get("status") == "incomplete" and not lost:
        ast["status"] = "done"
    save_state(sdir, st)
    print(f"arm {a.arm}: {ast['score']['passed']}/{ast['score']['scorable']} scorable, "
          f"{len(lost)} still lost")
    return 0


def cmd_status(a, camp: dict) -> int:
    tree = os.path.abspath(a.tree)
    sdir = a.state_dir or state_dir_for(tree, camp, a.model_name)
    st = load_state(sdir)
    if not st:
        print(f"no campaign state in {sdir}")
        return 1
    print(f"campaign {st['campaign']}  model {st['model_name']}  state {sdir}/state.json")
    for arm in camp["arms"]:
        ast = st["arms"].get(arm["id"]) or {}
        sc = score(arm_rows(ast)) if ast else None
        cell = f"{sc['passed']}/{sc['scorable']}" if sc and sc["scorable"] else "-"
        print(f"  {arm['id']:<4} {ast.get('status', 'pending'):<12} {cell:<8} "
              f"lost {len(ast.get('lost') or {}):<3} {arm.get('label', '')}")
        for p in arm_paths(ast):
            print(f"         {p}")
    if a.json:
        print(json.dumps(st, indent=1))
    return 0


def cmd_paths(a, camp: dict) -> int:
    tree = os.path.abspath(a.tree)
    sdir = a.state_dir or state_dir_for(tree, camp, a.model_name)
    st = load_state(sdir) or {}
    if a.arm in (st.get("baselines") or {}):
        print(st["baselines"][a.arm])
        return 0
    ast = (st.get("arms") or {}).get(a.arm)
    if not ast:
        raise SystemExit(f"no arm {a.arm} in {sdir}")
    print(" ".join(arm_paths(ast)))
    return 0


# --- the report --------------------------------------------------------------------------------

def load_ledger(path: str) -> dict:
    """Reference ledger (evals/reference_attempt.py grade rows): last row per unit."""
    rows = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                rows[r["unit"]] = r
    return rows


def load_baseline(path: str) -> dict:
    """{name: rows} from a results file, or every arm of an evals/profile.py
    state file (L0, L1, L2)."""
    with open(path) as f:
        d = json.load(f)
    if "tasks" in d:
        return {"": merge_rows([path])}
    if isinstance(d.get("results"), dict):
        base = os.path.dirname(os.path.abspath(path))
        out = {}
        for arm, p in d["results"].items():
            p2 = p if os.path.isfile(p) else os.path.join(base, os.path.basename(p))
            if os.path.isfile(p2):
                out[arm] = merge_rows([p2])
        return out
    raise SystemExit(f"{path}: neither a results file nor a profile state")


def unit_base_walls(suite: str) -> dict:
    """{unit: agent wall before the --jobs scale and --wall-scale}, the way
    run_eval resolves it: the task's own max_wall_s, else the manifest's,
    else max_iter x 60."""
    sys.path.insert(0, EVALS_DIR)
    sys.path.insert(0, os.path.join(REPO, "agents"))
    import run_eval  # noqa: PLC0415
    import suite_manifest  # noqa: PLC0415
    tasks = run_eval.load_tasks(None)
    man = suite_manifest.load_manifest(suite, tasks=tasks, strict=False)
    by_id = {t["id"]: t for t in tasks}
    out = {}
    for uid, unit in (man.get("by_task") or {}).items():
        t = by_id.get(uid) or {}
        cap = t.get("max_wall_s") or unit.get("max_wall_s")
        out[uid] = float(cap) if cap else float((t.get("max_iter") or 15) * 60)
    return out


def contention_scale(jobs: int) -> float:
    # Mirrors run_eval.contention_scale (jobs=4 -> 2.0).
    return 1.0 if jobs <= 1 else 1.0 + (jobs - 1) / 3.0


def _fmt(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"


def _units(us: list[str], limit: int = 40) -> str:
    if not us:
        return "—"
    s = ", ".join(f"`{u}`" for u in us[:limit])
    return s + (f" (+{len(us) - limit} more)" if len(us) > limit else "")


def render_report(camp: dict, st: dict, baselines: dict, reference: dict | None,
                  walls: dict | None, total_units: int | None = None,
                  unit_langs: dict | None = None) -> str:
    arms = [x for x in camp["arms"] if (st["arms"].get(x["id"]) or {}).get("results")]
    rows = {x["id"]: arm_rows(st["arms"][x["id"]]) for x in arms}
    jobs = int(st.get("jobs") or 1)
    L = []
    L.append(f"# Campaign `{camp['campaign']}` — {st['model_name']}")
    L.append("")
    L.append(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by `evals/campaign.py report`. "
             f"Endpoint `{st.get('base_url')}`, `--jobs {jobs}`. "
             + (f"Suite units: {total_units}. " if total_units else "")
             + "A row the agent never ran (interpreter startup, no output in < 2 s, signal death, "
               "server error, budget skip) is **lost**, never scored; flips are counted on the "
               "units both sides scored.")
    L.append("")

    # Totals
    L.append("## Totals")
    L.append("")
    L.append("| Row | Change | Passed | Scorable | Lost | % of reference | Mean turns | Mean agent s "
             "| Turn-cap ends | Wall timeouts | Run h | Commit | Era |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    ref_pass = None
    if reference:
        ref_pass = sum(1 for r in reference.values() if r.get("passed"))
        strict = sum(1 for r in reference.values() if r.get("passed") and not r.get("over_budget"))
        L.append(f"| **reference** | Claude Opus 5.5, own tools, no turn cap | **{ref_pass}** "
                 f"({strict} at 1x wall) | {len(reference)} | "
                 f"{sum(1 for r in reference.values() if r.get('indeterminate'))} indeterminate | 100 | "
                 f"- | {_fmt(sum((r.get('elapsed_s') or 0) for r in reference.values()) / max(1, len(reference)), 0)} "
                 f"| - | - | - | - | - |")

    def pct(p):
        return _fmt(100.0 * p / ref_pass, 0) if ref_pass else "-"

    for name, brows in baselines.items():
        s = score(brows)
        lost = sum(1 for r in brows.values() if infra_reason(r))
        L.append(f"| {name} | baseline | {s['passed']} | {s['scorable']} | {lost} | {pct(s['passed'])} "
                 f"| {_fmt(s['mean_turns'])} | {_fmt(s['mean_s'], 0)} | {s['turn_cap_ends']} "
                 f"| {s['wall_timeouts']} | - | - | - |")
    for x in arms:
        ast = st["arms"][x["id"]]
        s = score(rows[x["id"]])
        hours = ((ast.get("ended_at") or 0) - (ast.get("started_at") or 0)) / 3600 \
            if ast.get("ended_at") else None
        tr = ast.get("tree") or {}
        L.append(f"| **{x['id']}** {ast.get('status', '')} | {x.get('label', '')} | **{s['passed']}** "
                 f"| {s['scorable']} | {len(ast.get('lost') or {})} | {pct(s['passed'])} "
                 f"| {_fmt(s['mean_turns'])} | {_fmt(s['mean_s'], 0)} | {s['turn_cap_ends']} "
                 f"| {s['wall_timeouts']} | {_fmt(hours, 2)} | `{(tr.get('commit') or '?')[:10]}` "
                 f"| `{tr.get('era') or '?'}` |")
    L.append("")
    eras = {(st["arms"][x["id"]].get("tree") or {}).get("era") for x in arms}
    if len(eras - {None}) > 1:
        L.append("> Arms ran in more than one eval era (a runner, tools or harness change "
                 "between them). Each arm is a full run of its own tree, so its total stands; "
                 "a flip between arms of different eras mixes the arm's change with the code "
                 "change.")
        L.append("")

    # Decisions
    dec = [(x["id"], d, b, (st["arms"][x["id"]].get("decision_notes") or {}).get(d, ""))
           for x in arms for d, b in (st["arms"][x["id"]].get("decisions_taken") or {}).items()]
    unsupported = [(aid, a2.get("unsupported")) for aid, a2 in st["arms"].items()
                   if a2.get("status") == "unsupported"]
    if dec or unsupported:
        L.append("## Decisions")
        L.append("")
        for aid, d, b, why in dec:
            L.append(f"- **{d}** (applied to {aid}): **{b}** — {why}")
        for aid, miss in unsupported:
            L.append(f"- arm **{aid}** was not run: {', '.join(miss or [])}")
        L.append("")

    # Noise
    noise = [x for x in arms if x.get("noise_of") and x["noise_of"] in rows]
    churn = None
    if noise:
        L.append("## Noise (the seed arm)")
        L.append("")
        for x in noise:
            f = flips(rows[x["noise_of"]], rows[x["id"]])
            churn = len(f["rescued"]) + len(f["regressed"])
            L.append(f"- **{x['id']} vs {x['noise_of']}** (same harness, another sampling seed): "
                     f"{churn} of {f['common']} units flip ({len(f['rescued'])} up, "
                     f"{len(f['regressed'])} down, net {f['net']:+d}). A single-draw arm "
                     f"delta of this size or smaller is not an effect; claim a rescue only "
                     f"when it holds across seeds.")
        L.append("")

    # Flips
    L.append("## Flips")
    L.append("")
    L.append("Rescued = failed on the left, passed on the right. p = two-sided exact sign "
             "test on the flips (a single draw per side: read it with the noise above).")
    L.append("")
    L.append("| Contrast | Common | Left | Right | Rescued | Regressed | Net | p |")
    L.append("|---|---|---|---|---|---|---|---|")
    pairs = []
    for x in arms:
        if x.get("base") and x["base"] in rows:
            pairs.append((x["base"], rows[x["base"]], x["id"], rows[x["id"]]))
        for bname, brows in baselines.items():
            pairs.append((bname, brows, x["id"], rows[x["id"]]))
    details = []
    for ln, lrows, rn, rrows in pairs:
        f = flips(lrows, rrows)
        L.append(f"| {ln} → {rn} | {f['common']} | {f['a_passed']} | {f['b_passed']} "
                 f"| {len(f['rescued'])} | {len(f['regressed'])} | {f['net']:+d} | {f['p']:.3f} |")
        details.append(f"**{ln} → {rn}** rescued: {_units(f['rescued'])}; "
                       f"regressed: {_units(f['regressed'])}")
    L.append("")
    for d in details:
        L.append(f"- {d}")
    L.append("")

    # Wall-scaled arms
    scaled = [x for x in arms if float(st["arms"][x["id"]].get("wall_scale") or 1.0) > 1.0]
    if scaled:
        L.append("## Wall-scaled arms at the original wall")
        L.append("")
        L.append("A wall-scaled arm had more time per unit and the same turns. Its passes "
                 "that finished inside the unscaled wall (the unit's wall x the `--jobs` "
                 "contention scale) are what the arm would have scored with the base arm's time.")
        L.append("")
        L.append("| Arm | Wall x | Passed | Passed within the original wall | Needed the extra time |")
        L.append("|---|---|---|---|---|")
        for x in scaled:
            ws = float(st["arms"][x["id"]]["wall_scale"])
            s = scorable(rows[x["id"]])
            passed = [u for u, r in s.items() if r.get("passed")]
            if walls:
                cs = contention_scale(jobs)
                inside = [u for u in passed if u in walls
                          and float(s[u].get("elapsed_seconds") or 0) <= walls[u] * cs]
                over = sorted(set(passed) - set(inside))
                L.append(f"| {x['id']} | {ws:g} | {len(passed)} | {len(inside)} | {_units(over, 20)} |")
            else:
                L.append(f"| {x['id']} | {ws:g} | {len(passed)} | (unit walls unavailable) | - |")
        L.append("")

    # Per language
    cols = list(baselines) + [x["id"] for x in arms]
    allrows = dict(baselines)
    allrows.update(rows)
    # A row's own language, else the manifest's (rows of non-variant units
    # carried none before G17).
    lang_of = dict(unit_langs or {})
    for rr in allrows.values():
        for u, r in rr.items():
            if r.get("language"):
                lang_of.setdefault(u, r["language"])

    def lang(u):
        return lang_of.get(u) or "?"

    langs = sorted({lang(u) for rr in allrows.values() for u in rr})
    if langs:
        L.append("## Per language (passed / scorable)")
        L.append("")
        L.append("| Language | " + " | ".join(cols) + (" | reference |" if reference else " |"))
        L.append("|---|" + "---|" * (len(cols) + (1 if reference else 0)))
        for lg in langs:
            cells = []
            for c in cols:
                s = scorable(allrows[c])
                ls = [r for u, r in s.items() if lang(u) == lg]
                cells.append(f"{sum(1 for r in ls if r.get('passed'))}/{len(ls)}")
            if reference:
                rl = [r for u, r in reference.items() if lang(u) == lg]
                cells.append(f"{sum(1 for r in rl if r.get('passed'))}/{len(rl)}")
            L.append(f"| {lg} | " + " | ".join(cells) + " |")
        L.append("")

    # Lost
    lost_any = [(x["id"], st["arms"][x["id"]].get("lost") or {}) for x in arms]
    lost_any = [(a2, lo) for a2, lo in lost_any if lo]
    if lost_any:
        L.append("## Still lost (not scored)")
        L.append("")
        for aid, lo in lost_any:
            L.append(f"- **{aid}**: " + ", ".join(f"`{u}` ({w})" for u, w in sorted(lo.items())))
        L.append("")
    reruns = [(x["id"], r) for x in arms for r in st["arms"][x["id"]].get("reruns") or []]
    if reruns:
        L.append("## Reruns")
        L.append("")
        for aid, r in reruns:
            L.append(f"- {aid} r{r['round']}: {len(r['units'])} units ({r['why']}) -> "
                     f"`{os.path.basename(r.get('results') or '-')}`")
        L.append("")
    return "\n".join(L) + "\n"


def cmd_report(a, camp: dict) -> int:
    tree = os.path.abspath(a.tree)
    sdir = a.state_dir or state_dir_for(tree, camp, a.model_name)
    st = load_state(sdir)
    if not st:
        raise SystemExit(f"no campaign state in {sdir}")
    blist = dict(st.get("baselines") or {})
    for b in a.baseline or []:
        name, _, path = b.partition("=")
        blist[name] = os.path.abspath(path)
    baselines = {}
    for name, path in blist.items():
        for sub, rows in load_baseline(path).items():
            baselines[sub or name] = rows
    reference = load_ledger(a.reference_ledger) if a.reference_ledger else None
    suite = suite_of((camp.get("common") or {}).get("args") or [])
    walls = None
    try:
        walls = unit_base_walls(suite)
    except BaseException as e:  # noqa: BLE001 (SystemExit from a manifest refusal too)
        print(f"(unit walls unavailable: {e})", file=sys.stderr)
    total, langs = None, {}
    try:
        units = suite_units(tree, suite)
        total = sum(1 for u in units if (u.get("role") or "scored") == "scored")
        langs = {u["id"]: u.get("lang") for u in units if u.get("lang")}
    except OSError:
        pass
    text = render_report(camp, st, baselines, reference, walls, total, langs)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


# --- CLI -----------------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, need_url=False):
        p.add_argument("--campaign", required=True, help="a name in evals/campaigns/ or a path")
        p.add_argument("--model-name", help="the model's name (arms add '<campaign> <arm>')")
        p.add_argument("--model", help="a benchmark_all MODELS slug: name, base URL, slots, key env")
        p.add_argument("--tree", default=REPO, help="the checkout arms run from (default: this one)")
        p.add_argument("--state-dir", help="default <tree>/evals/results/campaigns/<campaign>-<slug>")
        p.add_argument("--python", default=os.environ.get("OPENBEAST_EVAL_PY") or sys.executable,
                       help="the eval interpreter (default $OPENBEAST_EVAL_PY, else this one). "
                            "Use a durable venv, never a scratch one")
        p.add_argument("--base-url", required=False)
        p.add_argument("--api-key-env")
        p.add_argument("--jobs", type=int)
        p.add_argument("--slots", type=int)
        p.add_argument("--served-model", help="the id the server serves (default: /v1/models)")
        p.add_argument("--dry-run", action="store_true", help="print the run_eval commands, run nothing")

    p = sub.add_parser("run", help="run the arms back to back (resumable)")
    common(p)
    p.add_argument("--from", dest="from_arm", help="start at this arm")
    p.add_argument("--only", help="run only these arms (comma-separated), even if done")
    p.add_argument("--arm-tree", action="append", help="ARM=DIR: run this arm from another checkout")
    p.add_argument("--allow-tree-move", action="store_true")
    p.add_argument("--baseline", action="append", help="NAME=results.json or a profile state")
    p.add_argument("--set-decision", action="append", help="NAME=then|else: override a decision")
    p.add_argument("--skip-preflight", action="store_true")

    p = sub.add_parser("rerun-lost", help="rerun an arm's lost units (or audited ones)")
    common(p)
    p.add_argument("--arm", required=True)
    p.add_argument("--units", help="comma-separated units to rerun instead of the lost ones")
    p.add_argument("--verdicts", help="the audit's verdicts (evals/audit/verdicts.py input or output)")
    p.add_argument("--force-units", action="store_true")

    p = sub.add_parser("report", help="the Markdown comparison")
    common(p)
    p.add_argument("--baseline", action="append", help="NAME=results.json or a profile state")
    p.add_argument("--reference-ledger", help="evals/reference_attempt.py ledger (JSONL)")
    p.add_argument("--out")

    p = sub.add_parser("status", help="arm table")
    common(p)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("paths", help="an arm's results files (main run first, then reruns)")
    common(p)
    p.add_argument("--arm", required=True)

    a = ap.parse_args(argv)
    if a.model:
        sys.path.insert(0, EVALS_DIR)
        import benchmark_all  # noqa: PLC0415
        m = next((x for x in benchmark_all.MODELS if x["slug"] == a.model), None)
        if m is None:
            ap.error(f"no MODELS entry {a.model!r}")
        a.model_name = a.model_name or m["name"]
        a.base_url = a.base_url or m.get("base_url")
        a.slots = a.slots or m.get("slots")
        a.api_key_env = a.api_key_env or m.get("api_key_env")
    if not a.model_name:
        ap.error("--model-name (or --model) is required")
    camp = load_campaign(a.campaign)
    if a.cmd == "run":
        if not a.base_url:
            ap.error("run needs --base-url (or --model)")
        a.jobs = a.jobs or 1
        if a.api_key_env and not os.environ.get(a.api_key_env) and not a.dry_run:
            ap.error(f"--api-key-env {a.api_key_env}: that variable is empty")
        return cmd_run(a, camp)
    if a.cmd == "rerun-lost":
        return cmd_rerun_lost(a, camp)
    if a.cmd == "report":
        return cmd_report(a, camp)
    if a.cmd == "status":
        return cmd_status(a, camp)
    return cmd_paths(a, camp)


if __name__ == "__main__":
    sys.exit(main())
