#!/usr/bin/env python3
"""A model campaign: chained harness arms from a declarative file
(docs/MODEL_CAMPAIGN.md is the runbook; evals/campaigns/*.json the files).

The default campaign is `v5-thinking-first`: the best-first composite (every
harness change believed to help, thinking on) runs first, so the headline row
exists early; then one-component-out ablations of it with thinking on, the
seed-noise arm, and last the no-thinking counterparts. `report` turns them
into a recommended daily config by written decision rules. `v5-harness` (the
2026-11-16 GLM build-up chain A-F) stays available under its name.

  # every arm, in the campaign's order, back to back (resumable: re-run the
  # same command after an interruption; finished arms are skipped)
  python3 evals/campaign.py run --model deepseek-v4.1-flash --jobs 4 --dry-run   # commands + ETA per arm
  python3 evals/campaign.py run --campaign v5-thinking-first \\
      --model-name "DeepSeek-v4.1-Flash" \\
      --base-url https://spark-1.tail4109f9.ts.net:8443/v1 \\
      --api-key-env OPENBEAST_SPARK_API_KEY --jobs 4 --slots 4 \\
      --baseline L0=evals/results/eval-deepseek-v4-1-flash-L0-<ts>.json

  python3 evals/campaign.py status     --model-name ...
  python3 evals/campaign.py rerun-lost --model-name ... --arm T
  python3 evals/campaign.py report     --model-name ... \\
      --reference-ledger evals/reference/runs/<run>/ledger.jsonl --out report.md
  python3 evals/campaign.py paths      --model-name ... --arm T

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
import signal
import subprocess
import sys
import time
from datetime import datetime

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EVALS_DIR)
CAMPAIGNS_DIR = os.path.join(EVALS_DIR, "campaigns")
DEFAULT_CAMPAIGN = "v5-thinking-first"
STATE_VERSION = 1

# Rows that say nothing about the model: the unit is lost, not failed.
INFRA_REASONS = frozenset({
    "server_error", "env_error", "validator_timeout", "budget_skip",
    "skipped_cache_miss", "server_unhealthy", "low_disk", "setup_failed",
    "interpreter_error",     # run_eval's own name for it once eval-host provisioning (#189) is in
    "served_model_changed",  # review 1118: the server stopped serving the measured model
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
            if d not in decisions:
                errs.append(f"arm {aid}: unknown decision {d!r}")
        if a.get("noise_of") and a["noise_of"] not in seen:
            errs.append(f"arm {aid}: noise_of {a['noise_of']} must be an earlier arm")
        if not isinstance(a.get("args", []), list) or not isinstance(a.get("env", {}), dict):
            errs.append(f"arm {aid}: args must be a list and env an object")
        if not isinstance(a.get("drop_args", []), list):
            errs.append(f"arm {aid}: drop_args must be a list of flags")
        tx = a.get("time_x")
        if tx is not None and (not isinstance(tx, (int, float)) or tx <= 0):
            errs.append(f"arm {aid}: time_x must be a positive number")
        for k in ("jobs", "slots"):
            v = a.get(k)
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
                errs.append(f"arm {aid}: {k} must be a positive integer")
        for k in ("model", "served_model"):
            v = a.get(k)
            if v is not None and (not isinstance(v, str) or not v.strip()):
                errs.append(f"arm {aid}: {k} must be a non-empty string")
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
    if errs:
        return errs
    errs += order_problems(camp, list(camp.get("order") or seen))
    rec = camp.get("recommend")
    if rec is not None:
        ids = set(seen)
        if not rec.get("composite"):
            errs.append("recommend needs a composite arm")
        for k in ("composite", "no_thinking"):
            if rec.get(k) and rec[k] not in ids:
                errs.append(f"recommend.{k}: no arm {rec[k]!r}")
        for x in rec.get("ablations") or []:
            if x not in ids:
                errs.append(f"recommend.ablations: no arm {x!r}")
            elif arm_spec(camp, x).get("base") != rec.get("composite"):
                errs.append(f"recommend.ablations: {x} must build on the composite "
                            f"{rec.get('composite')} (one component out of it)")
        if rec.get("no_thinking") in ids and \
                arm_spec(camp, rec["no_thinking"]).get("base") != rec.get("composite"):
            errs.append(f"recommend.no_thinking: {rec['no_thinking']} must build on the composite")
        cbm = rec.get("composite_by_model") or {}
        if not isinstance(cbm, dict):
            errs.append("recommend.composite_by_model must map a model name to an arm")
            cbm = {}
        slugs: dict = {}
        for name in cbm:
            k = slugify(name)
            if k in slugs:
                # composite_for takes the longest matching key; two keys with
                # one slug are the same length, so neither rule can choose.
                errs.append(f"recommend.composite_by_model: {slugs[k]!r} and {name!r} are the "
                            f"same model key ({k!r}); the longest-match rule cannot choose")
            slugs.setdefault(k, name)
        for name, aid in cbm.items():
            if aid not in ids:
                errs.append(f"recommend.composite_by_model[{name!r}]: no arm {aid!r}")
            elif rec.get("composite") not in [a["id"] for a in arm_chain(camp, aid)]:
                errs.append(f"recommend.composite_by_model[{name!r}]: {aid} must build on the "
                            f"composite {rec.get('composite')} (its ablations remove from it)")
    for pair in camp.get("contrasts") or []:
        if len(pair) != 2 or any(x not in seen for x in pair):
            errs.append(f"contrast {pair}: needs two known arms")
    return errs


def order_problems(camp: dict, order: list[str]) -> list[str]:
    """A run order is valid when every arm in it exists and appears once,
    and every decision an arm applies compares arms that run before it."""
    errs = []
    ids = [a.get("id") for a in camp.get("arms") or []]
    pos: dict = {}
    for i, x in enumerate(order):
        if x not in ids:
            errs.append(f"order: no arm {x!r}")
        elif x in pos:
            errs.append(f"order: {x} listed twice")
        pos.setdefault(x, i)
    decisions = camp.get("decisions") or {}
    for a in camp.get("arms") or []:
        if a.get("id") not in pos:
            continue
        for d in a.get("apply_decisions") or []:
            for x in (decisions.get(d) or {}).get("compare") or []:
                if x not in pos or pos[x] > pos[a["id"]]:
                    errs.append(f"arm {a['id']}: decision {d} compares {x}, "
                                f"which must run earlier")
    return errs


def composite_for(camp: dict, model_name: str | None) -> tuple[str | None, str]:
    """(arm, source): the best-first composite for a model. A model listed in
    recommend.composite_by_model gets its own arm (DeepSeek-V4.1-Flash: T2,
    65/71 against T's 61/71); every other model the campaign's composite. The
    match is on slugs: the key, or the key followed by more words
    ("DeepSeek-V4.1-Flash (Mia EXL3)" matches "DeepSeek-V4.1-Flash"). When
    several keys match ("Hy4-flash" and "Hy4-flash-abliterated" both match
    "Hy4-flash-abliterated (GGUF)"), the longest key wins, whatever the
    file's order; validate_campaign refuses two keys with the same slug."""
    rec = camp.get("recommend") or {}
    if not model_name:
        return rec.get("composite"), "recommend.composite"
    slug = slugify(model_name)
    best = None
    for name, aid in (rec.get("composite_by_model") or {}).items():
        key = slugify(name)
        if (slug == key or slug.startswith(key + "-")) and (best is None or len(key) > best[0]):
            best = (len(key), name, aid)
    if best:
        return best[2], f"recommend.composite_by_model[{best[1]!r}]"
    return rec.get("composite"), "recommend.composite"


def run_order(camp: dict, prefer: list[str] | None = None,
              include: list[str] | None = None,
              model_name: str | None = None) -> list[str]:
    """The arms `run` chains, in order: the campaign's `order` (else the
    file's), optional arms only when `include`d, and `prefer` (--order)
    moved to the front in its own order. A model with its own composite
    (recommend.composite_by_model) runs that arm too, first unless --order
    says otherwise: it is that model's best-first row. Refused if a decision
    would then be taken before the arms it compares have run."""
    mc, _src = composite_for(camp, model_name)
    if model_name and mc and mc != (camp.get("recommend") or {}).get("composite"):
        include = list(include or []) + [mc]
        if not prefer:
            prefer = [mc]
    want = {arm_spec(camp, x)["id"] for x in include or []}
    base = list(camp.get("order") or [a["id"] for a in camp["arms"]])
    base += [a["id"] for a in camp["arms"] if a["id"] in want and a["id"] not in base]
    base = [x for x in base if not arm_spec(camp, x).get("optional") or x in want]
    head = [arm_spec(camp, x)["id"] for x in prefer or []]
    order = head + [x for x in base if x not in head]
    errs = order_problems(camp, order)
    if errs:
        raise SystemExit("run order refused:\n  " + "\n  ".join(errs))
    return order


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


def _arg_pairs(args: list[str]) -> list[list]:
    """[[flag, value | None], ...]: `--x v` and `--x=v` are one pair; a flag
    followed by another flag (or nothing) has no value. A value may start
    with a single dash (`-1`)."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and "=" in a:
            f, _, v = a.partition("=")
            out.append([f, v])
            i += 1
        elif a.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            out.append([a, args[i + 1]])
            i += 2
        else:
            out.append([a, None])
            i += 1
    return out


def merge_args(base: list[str], add: list[str] | None = None,
               drop: list[str] | None = None) -> list[str]:
    """`base` without the `drop` flags (and their values), then `add`: a flag
    already present takes the new value in place, a new one is appended. So
    an arm can switch a flag (`--thinking off` over the composite's
    `--thinking on`) or remove one (`drop_args: ["--wall-scale"]`), not only
    add one."""
    dropped = set(drop or [])
    pairs = [p for p in _arg_pairs(list(base)) if p[0] not in dropped]
    for f, v in _arg_pairs(list(add or [])):
        hit = next((p for p in pairs if p[0] == f), None)
        if hit is not None:
            hit[1] = v
        else:
            pairs.append([f, v])
    out: list[str] = []
    for f, v in pairs:
        out += [f] if v is None else [f, v]
    return out


def arg_value(args: list[str], flag: str) -> str | None:
    """The value the last `flag` in `args` sets (argparse's last-wins)."""
    val = None
    for f, v in _arg_pairs(list(args or [])):
        if f == flag:
            val = v
    return val


def apply_delta(spec: dict, part: dict) -> None:
    """One layer (common, an arm, a decision branch) on top of a spec."""
    spec["args"] = merge_args(spec["args"], part.get("args"), part.get("drop_args"))
    # An arm's own concurrency (`"jobs": 2, "slots": 2`) wins over the CLI's
    # --jobs/--slots for that arm; an arm built on it inherits it.
    for k in ("jobs", "slots"):
        if part.get(k) is not None:
            spec[k] = int(part[k])
    spec["env"].update({k: str(v) for k, v in (part.get("env") or {}).items()})
    r = part.get("requires") or {}
    spec["requires"]["flags"] += r.get("flags") or []
    spec["requires"]["env"] += r.get("env") or []


def finish_spec(spec: dict) -> dict:
    spec["requires"] = {k: sorted(set(v)) for k, v in spec["requires"].items()}
    spec["wall_scale"] = wall_scale_of(spec["args"])
    spec["thinking"] = arg_value(spec["args"], "--thinking")
    return spec


def resolve_arm(camp: dict, aid: str, decided: dict | None = None) -> dict:
    """argv (run_eval flags, without the per-campaign ones), env, requirements
    and wall scale of one arm: common + every base + the arm + the branches
    its decisions took. `decided` = {decision name: "then" | "else"}. Each
    layer may add a flag, switch one (`args` naming a flag already set) or
    remove one (`drop_args`), and set env switches."""
    spec: dict = {"args": [], "env": {}, "requires": {"flags": [], "env": []},
                  "jobs": None, "slots": None}
    apply_delta(spec, camp.get("common") or {})
    for a in arm_chain(camp, aid):
        apply_delta(spec, a)
    for d in arm_spec(camp, aid).get("apply_decisions") or []:
        branch = (decided or {}).get(d)
        if branch not in ("then", "else"):
            raise SystemExit(f"arm {aid}: decision {d} has not been made")
        apply_delta(spec, camp["decisions"][d].get(branch) or {})
    return finish_spec(spec)


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
    if INTERPRETER_STARTUP.search(out):
        # The agent's interpreter (no tokens), or the VALIDATOR's own python
        # after the agent ran (tokens > 0): either way nothing judged the
        # solution. 2 of arm B's 24 units lost to the 2026-11-16 venv break
        # were the second kind and were scored as fails (#193 found it).
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
        # Review 1118: only errors that ENDED the run (api_errors_tail > 0)
        # lose the unit; one the run recovered from leaves a verdict, as a
        # pass with an error always was. Rows written before the field (every
        # published board row) keep the old any-error rule, so their scores
        # do not move.
        tail = row.get("api_errors_tail")
        if tail is None or tail > 0:
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
    # A wall-killed row (-1) is a wall timeout, never a turn-cap ending, even
    # when its partial stats (stats_partial, 2026-11-18) carry its turns.
    capped = sum(1 for r in s.values()
                 if not r.get("passed") and r.get("agent_exit_code") != -1
                 and isinstance(r.get("iterations"), int)
                 and isinstance(r.get("max_iter"), int)
                 and r["iterations"] >= r["max_iter"] - TURN_CAP_SLACK)
    timeouts = sum(1 for r in s.values() if not r.get("passed") and r.get("agent_exit_code") == -1)
    toks = [float(r["tokens_completion"]) for r in s.values()
            if isinstance(r.get("tokens_completion"), (int, float))]
    return {"passed": passed, "scorable": len(s),
            "mean_turns": (sum(iters) / len(iters)) if iters else None,
            "mean_s": (sum(secs) / len(secs)) if secs else None,
            "mean_tokens": (sum(toks) / len(toks)) if toks else None,
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


RESULTS_AT_START = re.compile(r"^Results(?: \[[^\]]*\])?: (.+\.json)\s*$", re.M)
INTERRUPTED_RCS = (130, -2, -15, 143, -1, 129)


def pid_alive(pid) -> bool:
    """Whether `pid` is a live process that is a run_eval (its command line
    names evals/run_eval.py: a reused pid is not ours)."""
    try:
        pid = int(pid)
        os.kill(pid, 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        pass
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True          # alive, and we cannot tell whose: assume ours
    return "run_eval.py" in out


def run_eval_once(py: str, tree: str, argv: list[str], env: dict, log_path: str,
                  slug: str, on_child=None) -> tuple[int, list[str]]:
    """One run_eval process. Returns (rc, results files it wrote): the
    'Saved to:' lines, else (an interrupted run prints none) the newest
    eval-<slug>-*.json written since it started.

    Review 1118: SIGTERM and SIGHUP to the campaign are forwarded to the
    child as SIGTERM (run_eval reaps its agents and exits 143), so a stopped
    campaign never leaves an orphaned run_eval banking rows behind it.
    `on_child(info)` is told the child's pid when it starts, its results
    path as soon as run_eval prints it, and None when it has exited (the
    caller records these in state.json)."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    start = time.time()
    cmd = [py, "-u", "evals/run_eval.py", *argv]
    with open(log_path, "a") as lf:
        offset = lf.tell()
        lf.write(f"\n# {datetime.now().isoformat()} {shlex.join(cmd)}\n")
        lf.flush()
        proc = subprocess.Popen(cmd, cwd=tree, env=env, stdout=lf, stderr=subprocess.STDOUT)
        info = {"pid": proc.pid, "started_at": start, "log": log_path, "results": None}
        if on_child:
            on_child(dict(info))

        def forward(signum, _frame):
            try:
                proc.send_signal(signal.SIGTERM)
            except OSError:
                pass

        saved = {}
        for sig in (signal.SIGTERM, signal.SIGHUP):
            try:
                saved[sig] = signal.signal(sig, forward)
            except (ValueError, OSError):
                pass        # not the main thread: nothing to forward from
        try:
            while True:
                try:
                    rc = proc.wait(timeout=2)
                    break
                except subprocess.TimeoutExpired:
                    pass
                except KeyboardInterrupt:
                    # Ctrl-C reached the child too (same process group).
                    try:
                        proc.wait(timeout=120)
                    except (subprocess.TimeoutExpired, KeyboardInterrupt):
                        proc.kill()
                    rc = 130
                    break
                if info["results"] is None and on_child:
                    try:
                        with open(log_path) as f:
                            f.seek(offset)
                            m = RESULTS_AT_START.search(f.read())
                    except OSError:
                        m = None
                    if m:
                        p = m.group(1).strip()
                        info["results"] = p if os.path.isabs(p) else os.path.join(tree, p)
                        on_child(dict(info))
        finally:
            for sig, h in saved.items():
                try:
                    signal.signal(sig, h)
                except (ValueError, OSError):
                    pass
            if on_child:
                on_child(None)
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


def child_recorder(sdir: str, st: dict, aid: str):
    """on_child for run_eval_once: the live child in state.json ("child")."""
    def rec(info):
        if info is None:
            st.pop("child", None)
        else:
            st["child"] = {**info, "arm": aid}
        save_state(sdir, st)
    return rec


def live_child(st: dict) -> dict | None:
    """The run_eval a previous campaign process started and that still runs."""
    ch = st.get("child") or {}
    return ch if ch.get("pid") and pid_alive(ch["pid"]) else None


def base_env(camp: dict, spec: dict, a, served: str | None) -> dict:
    env = dict(os.environ)
    env.update(spec["env"])
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["OPENBEAST_EVAL_ENV_ERA"] = env.get("OPENBEAST_EVAL_ENV_ERA") or "1"
    if served and not env.get("OPENBEAST_INFERENCE_MODEL"):
        env["OPENBEAST_INFERENCE_MODEL"] = served
    return env


def arm_concurrency(spec: dict, a) -> tuple[int, int | None, str]:
    """(jobs, slots, why) an arm runs at: the arm's own `jobs`/`slots` (the
    campaign file) win over the CLI's --jobs/--slots, which campaign_argv
    would otherwise append after the arm's args. `why` is "" when the CLI's
    values stand."""
    cli_jobs, cli_slots = int(a.jobs or 1), a.slots
    jobs = int(spec.get("jobs") or cli_jobs)
    slots = spec.get("slots") or cli_slots
    why = ""
    if spec.get("jobs") or spec.get("slots"):
        if (jobs, slots) != (cli_jobs, cli_slots):
            why = (f"the arm's own (the CLI said --jobs {cli_jobs}"
                   + (f" --slots {cli_slots}" if cli_slots else "") + ")")
    return jobs, slots, why


def campaign_argv(spec_args: list[str], a, model_name: str, jobs: int | None = None,
                  slots: int | None = None) -> list[str]:
    """run_eval's argv: the arm's flags, then the per-campaign ones. `jobs` /
    `slots` are the arm's (arm_concurrency), else the CLI's."""
    jobs = jobs or a.jobs
    slots = slots or a.slots
    argv = list(spec_args) + ["--model-name", model_name, "--base-url", a.base_url,
                              "--jobs", str(jobs)]
    if slots:
        argv += ["--slots", str(slots)]
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
    jobs, slots, _ = arm_concurrency(spec, a)
    argv = campaign_argv(with_suite(spec["args"], name), a, model_name, jobs, slots)
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
                              slugify(model_name), on_child=child_recorder(sdir, st, aid))
    ast.setdefault("reruns", []).append({"round": k, "suite": name, "units": units, "why": why,
                                         "jobs": jobs, "slots": slots,
                                         "rc": rc, "results": paths[0] if paths else None,
                                         "ended_at": time.time()})
    save_state(sdir, st)
    clog(sdir, f"END rerun {aid} r{k} rc={rc} -> {paths[0] if paths else 'no results'}")
    if rc in INTERRUPTED_RCS:
        clog(sdir, f"INTERRUPTED rerun {aid} r{k}: run the same command again to resume")
        return 130
    if rc not in (0, 1) and not paths:
        clog(sdir, f"ABORT rerun {aid} r{k}: run_eval exited {rc} without a results file "
                   f"(refused at start? see arm-{aid}-r{k}.log)")
        return 5
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


def _csv(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _fmt_h(h: float | None) -> str:
    return "unknown" if h is None else f"~{h:.1f} h"


def jobs_time_scale(ref_jobs: int, jobs: int) -> float:
    """How much longer an arm takes at `jobs` concurrent units than at
    `ref_jobs`. Not linear: per-stream decode slows with the number of
    streams, which is why run_eval widens every wall by contention_scale
    (jobs 4 -> 2.0x). A unit's time is token-bound and its wall moves with
    contention_scale, so a unit takes contention_scale(jobs) time units and
    the arm's throughput is jobs / contention_scale(jobs) units per time
    unit: 4 jobs -> 2.0, 2 -> 1.5, 1 -> 1.0. Measured on DeepSeek-V4.1-Flash
    (Mia EXL3) on the Spark pair (tools/bench.py): ~60 tok/s for one stream,
    ~45 each for two, ~30 each for four, the same ratios. So --jobs 2 takes
    1.33x the --jobs 4 hours, not 2x."""
    if not ref_jobs or not jobs:
        return 1.0
    return (ref_jobs / contention_scale(ref_jobs)) / (jobs / contention_scale(jobs))


def arm_jobs(st: dict | None, aid: str, default: int = 1) -> int:
    """The --jobs an arm ran at: its own record, else the campaign's, else
    `default`."""
    ast = ((st or {}).get("arms") or {}).get(aid) or {}
    return int(ast.get("jobs") or (st or {}).get("jobs") or default)


def eta_base_h(camp: dict, st: dict | None, override: float | None) -> tuple[float, str]:
    """Hours of the campaign's reference arm (time_x 1.0) at the campaign's
    eta.jobs: --base-hours, else calibrated from this campaign's finished
    arms (the median of their hours / time_x, each brought back to eta.jobs
    by jobs_time_scale), else the file's eta.base_h."""
    eta = camp.get("eta") or {}
    if override:
        return float(override), "--base-hours"
    ref_jobs = int(eta.get("jobs") or 0)
    cal = []
    for aid, ast in ((st or {}).get("arms") or {}).items():
        try:
            tx = arm_spec(camp, aid).get("time_x")
        except SystemExit:
            continue
        if tx and ast.get("status") == "done" and ast.get("ended_at") \
                and ast.get("started_at") is not None:
            js = jobs_time_scale(ref_jobs, arm_jobs(st, aid, ref_jobs)) if ref_jobs else 1.0
            cal.append((ast["ended_at"] - ast["started_at"]) / 3600 / float(tx) / js)
    if cal:
        cal.sort()
        mid = cal[len(cal) // 2] if len(cal) % 2 else (cal[len(cal) // 2 - 1] + cal[len(cal) // 2]) / 2
        return mid, f"calibrated from {len(cal)} finished arm(s) of this campaign"
    if eta.get("base_h"):
        return float(eta["base_h"]), f"the campaign file's eta.base_h: {eta.get('basis', '')}".rstrip(": ")
    return 0.0, "no eta.base_h in the campaign file"


def arm_eta_h(camp: dict, aid: str, base_h: float, jobs: int | None) -> tuple[float | None, str]:
    """(hours, why): base hours x the arm's time_x, scaled from the
    campaign's eta.jobs to the arm's jobs (its own `jobs`, else --jobs) by
    jobs_time_scale. A rough guide, not a promise."""
    tx = arm_spec(camp, aid).get("time_x")
    if tx is None or not base_h:
        return None, "no time_x" if tx is None else "no base hours (--base-hours H)"
    ref_jobs = int((camp.get("eta") or {}).get("jobs") or 0)
    scale = jobs_time_scale(ref_jobs, jobs) if ref_jobs and jobs else 1.0
    why = f"time x{float(tx):g} of {base_h:.1f} h"
    if abs(scale - 1.0) > 1e-9:
        why += f", x{scale:.3g} for --jobs {jobs} vs {ref_jobs}"
    return base_h * float(tx) * scale, why


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
    order = run_order(camp, _csv(a.order), _csv(a.include), a.model_name)
    if a.only:
        todo = [arm_spec(camp, x)["id"] for x in a.only.split(",")]
    elif a.from_arm:
        start = arm_spec(camp, a.from_arm)["id"]
        if start not in order:
            raise SystemExit(f"--from {start}: not in the run order {order} "
                             f"(an optional arm needs --include {start})")
        todo = order[order.index(start):]
    else:
        todo = order
    arm_trees = {}
    for x in a.arm_tree or []:
        k, _, v = x.partition("=")
        arm_trees[arm_spec(camp, k)["id"]] = os.path.abspath(v)
    base_h, base_src = eta_base_h(camp, st, a.base_hours)
    total_h, eta_arms = 0.0, []

    served = a.served_model
    if not a.dry_run:
        ch = live_child(st)
        if ch:
            print(f"REFUSED: a run_eval this campaign started is still running (pid {ch['pid']}, "
                  f"arm {ch.get('arm')}, results {ch.get('results') or 'not yet named'}). "
                  f"Wait for it, or stop it (kill -TERM {ch['pid']}), then run again.")
            return 2
    if not a.dry_run and not a.skip_preflight:
        if served is None:
            served = detect_served(a.base_url, a.api_key_env)
        problems = preflight(a, camp, py, tree, todo, served)
        if problems:
            print("PREFLIGHT REFUSED:")
            for p in problems:
                print(f"  - {p}")
            print("(--skip-preflight to run anyway)")
            return 2
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
        jobs, slots, conc_why = arm_concurrency(spec, a)
        argv = campaign_argv(spec["args"], a, model_name, jobs, slots)
        env = base_env(camp, spec, a, served)
        eta_h, eta_why = arm_eta_h(camp, aid, base_h, jobs)
        if a.dry_run:
            envs = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(spec["env"].items()))
            print(f"# arm {aid} ({arm_spec(camp, aid).get('label', '')}) tree={atree}")
            print(f"#   jobs {jobs}" + (f", slots {slots}" if slots else "")
                  + (f": {conc_why}" if conc_why else ""))
            print(f"#   ETA {_fmt_h(eta_h)} ({eta_why})")
            print(f"{envs} {shlex.join([py, '-u', 'evals/run_eval.py', *argv])}".strip())
            eta_arms.append(aid)
            total_h += eta_h or 0.0
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
                    "args": spec["args"], "env": spec["env"], "wall_scale": spec["wall_scale"],
                    "jobs": jobs, "slots": slots})
        ast.setdefault("started_at", time.time())
        st["arms"][aid] = ast
        save_state(sdir, st)
        if resumed:
            clog(sdir, f"RESUME {aid}: banked rows kept; the missing units rerun as lost")
        else:
            clog(sdir, f"START {aid} ({ast['label']}) tree={atree} --jobs {jobs}"
                       + (f" --slots {slots}" if slots else "")
                       + (f" ({conc_why})" if conc_why else "") + f" ETA {_fmt_h(eta_h)}")
            rc, paths = run_eval_once(py, atree, argv, env, os.path.join(sdir, f"arm-{aid}.log"),
                                      slugify(model_name), on_child=child_recorder(sdir, st, aid))
            ast["rc"] = rc
            ast["results"] = paths
            save_state(sdir, st)
            sc = score(arm_rows(ast))
            clog(sdir, f"END {aid} rc={rc} {sc['passed']}/{sc['scorable']} scorable "
                       f"-> {paths[0] if paths else 'no results'}")
            if rc in INTERRUPTED_RCS:
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
    if a.dry_run:
        print(f"# total ETA {_fmt_h(total_h)} for the arms not yet done ({', '.join(eta_arms)}); "
              f"base {base_h:g} h ({base_src})")
        opt = [x["id"] for x in camp["arms"] if x.get("optional") and x["id"] not in todo]
        if opt:
            print(f"# optional, not in this chain: {', '.join(opt)} (--include ARM to add)")
    if not a.dry_run:
        clog(sdir, "CAMPAIGN CHAIN DONE" if all(
            (st["arms"].get(x) or {}).get("status") in ("done", "unsupported") for x in order)
            else f"chain stopped; state {sdir}/state.json")
    return 0


class probe_auth:
    """While open: the gate key of --api-key-env as OPENBEAST_API_KEY, and
    the base URL as the endpoint it may go to (runner.resolve_api_key's key
    policy, which every run_eval probe follows), as run_eval itself sets
    them for --api-key-env. Restored on exit."""
    KEYS = ("OPENBEAST_API_KEY", "OPENBEAST_AGENT_INFERENCE_URL")

    def __init__(self, base_url: str, api_key_env: str | None):
        self.base_url, self.api_key_env = base_url, api_key_env

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.KEYS}
        if self.api_key_env and os.environ.get(self.api_key_env):
            os.environ["OPENBEAST_API_KEY"] = os.environ[self.api_key_env]
            os.environ["OPENBEAST_AGENT_INFERENCE_URL"] = self.base_url
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _run_eval_mod():
    sys.path.insert(0, EVALS_DIR)
    sys.path.insert(0, os.path.join(REPO, "agents"))
    import run_eval  # noqa: PLC0415
    return run_eval


def detect_served(base_url: str, api_key_env: str | None = None) -> str | None:
    try:
        with probe_auth(base_url, api_key_env):
            m = _run_eval_mod().detect_model(base_url)
        return None if m == "unknown" else m
    except Exception:  # noqa: BLE001
        return None


def served_model_mismatch(camp: dict, todo: list[str] | None, served: str | None) -> list[str]:
    """An arm that measures one model's server (`"served_model"`: H_T2 is
    Hy4-flash-NVFP4) refuses to run against another: a Hy4 row measured on
    DeepSeek would look valid and be wrong."""
    out = []
    for aid in todo or []:
        want = arm_spec(camp, aid).get("served_model")
        if want and served and want != served:
            out.append(f"arm {aid} measures {want}, but the server serves {served}: switch the "
                       f"pair first (beast-spark hy4 for Hy4-flash), or leave {aid} out")
    return out


def preflight(a, camp: dict, py: str, tree: str, todo: list[str] | None = None,
              served: str | None = None) -> list[str]:
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
    # Review 1118: the model the arms will request must be served.
    served = served or a.served_model
    if not served:
        problems.append(f"could not read the served model from {a.base_url}/models: pass "
                        f"--served-model <id> (the id the server serves)")
    else:
        try:
            with probe_auth(a.base_url, a.api_key_env):
                why = _run_eval_mod().served_model_problem(a.base_url, served)
            if why:
                problems.append(f"served model: {why}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"served model check could not run ({type(e).__name__}: {e})")
    problems += served_model_mismatch(camp, todo, served)
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
        # Every arm declares its reasoning budget (0 = thinking off; a
        # thinking arm declares the eval turn cap that bounds its thinking,
        # docs/MODEL_CAMPAIGN.md §7). profile.preflight refuses an undeclared
        # or uncapped one; it is checked with the first arm's, and every
        # other arm must declare one too.
        rbs = {}
        max_jobs = int(a.jobs or 1)
        for aid in todo or [x["id"] for x in camp["arms"]]:
            try:
                sp = resolve_arm(camp, aid, {d: "else" for d in camp.get("decisions") or {}})
            except SystemExit:
                continue
            rbs[aid] = arg_value(sp["args"], "--reasoning-budget")
            max_jobs = max(max_jobs, int(sp.get("jobs") or 0))
        for aid, rb in rbs.items():
            if rb in (None, "", "-1"):
                problems.append(f"arm {aid} declares no finite --reasoning-budget")
        rb = next(iter(rbs.values()), None)
        problems += prof.preflight(a.base_url, rb, False)
        # Review 1118: the probe requests the SERVED id (a gate or engine
        # that routes by model would answer the display name differently).
        adm = prof.admission_check(a.base_url, max_jobs, served or a.model_name)
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
        own = (f" [--jobs {ast['jobs']}]" if ast.get("jobs") and ast["jobs"] != st.get("jobs")
               else "")
        print(f"  {arm['id']:<4} {ast.get('status', 'pending'):<12} {cell:<8} "
              f"lost {len(ast.get('lost') or {}):<3} {arm.get('label', '')}{own}")
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


def passed_at_base_wall(rows: dict, walls: dict | None, jobs: int) -> list[str] | None:
    """The passes that finished inside the unwidened wall (the unit's wall x
    the --jobs contention scale): what a wall-scaled arm would have scored
    with its unscaled twin's time. None without unit walls."""
    if walls is None:
        return None
    cs = contention_scale(jobs)
    s = scorable(rows)
    return sorted(u for u, r in s.items() if r.get("passed") and u in walls
                  and float(r.get("elapsed_seconds") or 0) <= walls[u] * cs)


def paired_cost(a: dict, b: dict) -> dict:
    """Mean agent seconds and completion tokens per unit of a and b, on the
    units both scored, and b's ratio to a."""
    sa, sb = scorable(a), scorable(b)
    common = sorted(set(sa) & set(sb))

    def mean(rows, key):
        v = [float(rows[u][key]) for u in common if isinstance(rows[u].get(key), (int, float))]
        return sum(v) / len(v) if v else None
    out = {"common": len(common)}
    for k, key in (("s", "elapsed_seconds"), ("tokens", "tokens_completion")):
        x, y = mean(sa, key), mean(sb, key)
        out[f"a_{k}"], out[f"b_{k}"] = x, y
        out[f"{k}_ratio"] = (y / x) if x and y is not None else None
    return out


def arm_thinking(camp: dict, st: dict, aid: str) -> str | None:
    """"on" / "off" / None (the server's default) for an arm: the flags it
    ran with (state), else what the campaign file resolves it to."""
    ast = (st.get("arms") or {}).get(aid) or {}
    if ast.get("args"):
        return arg_value(ast["args"], "--thinking")
    try:
        return resolve_arm(camp, aid, ast.get("decisions_taken"))["thinking"]
    except SystemExit:
        return None


def noise_margin(camp: dict, rows: dict, composite: str,
                 fallback_of: str | None = None) -> tuple[int, str]:
    """(margin, why): the pass-count delta a single-draw contrast must exceed
    to count as an effect. The seed arms of the composite measure how many
    units flip between two draws of one config (churn c). Under no effect
    those flips land up or down with equal odds, so a contrast's net delta
    has a standard deviation of about sqrt(c): the margin is
    ceil(recommend.noise_sd x sqrt(c)), from the noisiest seed arm. Without a
    measured seed arm, recommend.noise_fallback. `fallback_of`: a per-model
    composite with no seed arm of its own borrows the seed arms of the
    campaign composite it builds on (the same harness, one draw apart)."""
    rec = camp.get("recommend") or {}
    sd = float(rec.get("noise_sd", 1.0))
    churns = []
    for of in [composite] + ([fallback_of] if fallback_of and fallback_of != composite else []):
        for x in camp["arms"]:
            if x.get("noise_of") == of and x["id"] in rows and of in rows:
                f = flips(rows[of], rows[x["id"]])
                churns.append((len(f["rescued"]) + len(f["regressed"]), x["id"], f["common"], of))
        if churns:
            break
    if not churns:
        m = int(rec.get("noise_fallback", 3))
        return m, (f"no seed arm of {composite} measured: margin {m} "
                   f"(recommend.noise_fallback), an assumption, not a measurement")
    c, aid, n, of = max(churns)
    m = math.ceil(sd * math.sqrt(c))
    return m, (f"{aid} vs {of}: {c} of {n} units flip between two draws; "
               f"margin ceil({sd:g} x sqrt({c})) = {m}")


def recommend(camp: dict, st: dict, rows: dict) -> dict | None:
    """The decision rules of docs/MODEL_CAMPAIGN.md §6, on measured arms:

      * a component whose removal does not cost more than the noise margin
        (composite passes - ablation passes <= margin, on the units both
        scored) is dropped from the recommended config; an unmeasured
        ablation keeps its component;
      * thinking stays on only if the composite beats its no-thinking twin by
        more than the margin; otherwise no-thinking is recommended for its
        latency. Either way the measured cost (time and tokens per unit) is
        reported.

    A model with its own composite (recommend.composite_by_model, e.g.
    DeepSeek-V4.1-Flash: T2) starts from that arm once it is measured; the
    ablations and the no-thinking twin still each compare with the arm they
    remove from (the campaign composite), and a dropped component is removed
    from the model's composite.

    Returns None without a `recommend` block or before the composite ran."""
    rec = camp.get("recommend")
    if not rec:
        return None
    base = rec.get("composite")
    comp, source = composite_for(camp, st.get("model_name"))
    fallback = ""
    if comp != base and comp not in rows:
        fallback = (f"{comp} ({source}) is not measured yet: the campaign composite "
                    f"{base} stands in")
        comp, source = base, "recommend.composite"
    if comp not in rows:
        return None
    margin, noise_why = noise_margin(camp, rows, comp, base)
    taken = (st["arms"].get(comp) or {}).get("decisions_taken") or {}
    spec = resolve_arm(camp, comp, taken)

    def against(x: str) -> str:
        """The arm `x` removes one thing from: its base, else the composite."""
        return arm_spec(camp, x).get("base") or comp

    components = []
    for x in rec.get("ablations") or []:
        label = arm_spec(camp, x).get("ablates") or x
        ref = against(x)
        if x not in rows or ref not in rows:
            components.append({"arm": x, "component": label, "verdict": "keep",
                               "why": "not measured: kept (the composite's default)"
                               if x not in rows else
                               f"{ref}, the arm it removes from, not measured: kept"})
            continue
        f = flips(rows[x], rows[ref])
        keep = f["net"] > margin
        components.append({
            "arm": x, "component": label, "verdict": "keep" if keep else "drop",
            "net": f["net"], "common": f["common"], "p": f["p"],
            "why": (f"without it {f['a_passed']}, with it {f['b_passed']} on {f['common']} "
                    f"common units: net {f['net']:+d} {'>' if keep else '<='} margin {margin}"
                    + ("" if ref == comp else f" (vs {ref})"))})
        if not keep:
            apply_delta(spec, arm_spec(camp, x))
    think = None
    nt = rec.get("no_thinking")
    if nt:
        ref = against(nt)
        if nt not in rows or ref not in rows:
            think = {"arm": nt, "verdict": "on",
                     "why": f"{nt} (no thinking) not measured: thinking stays on"}
        else:
            f = flips(rows[nt], rows[ref])
            cost = paired_cost(rows[nt], rows[ref])
            on = f["net"] > margin
            think = {"arm": nt, "verdict": "on" if on else "off", "net": f["net"],
                     "common": f["common"], "p": f["p"], "cost": cost,
                     "why": (f"thinking {f['b_passed']}, no thinking {f['a_passed']} on "
                             f"{f['common']} common units: net {f['net']:+d} "
                             f"{'>' if on else '<='} margin {margin}")}
            if not on:
                apply_delta(spec, arm_spec(camp, nt))
    finish_spec(spec)
    return {"composite": comp, "composite_source": source, "campaign_composite": base,
            "fallback": fallback, "margin": margin, "noise": noise_why,
            "components": components, "thinking": think, "spec": spec}


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
             f"Endpoint `{st.get('base_url')}`, `--jobs {jobs}`"
             + "".join(f", arm {x['id']} at `--jobs {arm_jobs(st, x['id'])}`" for x in arms
                       if arm_jobs(st, x["id"]) != jobs) + ". "
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

    # Recommended daily config (the decision rules)
    rec = recommend(camp, st, rows)
    if (camp.get("recommend") or {}).get("composite"):
        L.append("## Recommended daily config")
        L.append("")
        if rec is None:
            L.append(f"The composite arm `{camp['recommend']['composite']}` has not been "
                     f"measured yet: no recommendation.")
            L.append("")
        else:
            if rec["composite"] != rec["campaign_composite"]:
                L.append(f"Composite for {st['model_name']}: **{rec['composite']}** "
                         f"({rec['composite_source']}), not the campaign's "
                         f"{rec['campaign_composite']}. Each ablation is still measured against "
                         f"the arm it removes from ({rec['campaign_composite']}); a dropped "
                         f"component is removed from {rec['composite']}.")
                L.append("")
            elif rec.get("fallback"):
                L.append(f"{rec['fallback']}.")
                L.append("")
            th = rec["thinking"] or {}
            kept = [c["component"] for c in rec["components"] if c["verdict"] == "keep"]
            dropped = [c["component"] for c in rec["components"] if c["verdict"] == "drop"]
            L.append(f"**Thinking {th.get('verdict', 'on')}**; keep: "
                     f"{', '.join(kept) or 'nothing ablated'}; drop: {', '.join(dropped) or 'nothing'}.")
            L.append("")
            L.append(f"Noise margin: **{rec['margin']}** units ({rec['noise']}). A component "
                     f"stays only if removing it cost more than the margin; thinking stays on "
                     f"only if it beat its no-thinking twin by more than the margin.")
            L.append("")
            L.append("| Component (arm without it) | Net with it | Common | p | Verdict | Why |")
            L.append("|---|---|---|---|---|---|")
            for c in rec["components"]:
                L.append(f"| {c['component']} ({c['arm']}) | "
                         f"{'-' if 'net' not in c else format(c['net'], '+d')} "
                         f"| {c.get('common', '-')} "
                         f"| {'-' if 'p' not in c else format(c['p'], '.3f')} "
                         f"| **{c['verdict']}** | {c['why']} |")
            if th:
                L.append(f"| thinking ({th['arm']}) | "
                         f"{'-' if 'net' not in th else format(th['net'], '+d')} "
                         f"| {th.get('common', '-')} "
                         f"| {'-' if 'p' not in th else format(th['p'], '.3f')} "
                         f"| **{th['verdict']}** | {th['why']} |")
            L.append("")
            cost = th.get("cost")
            if cost:
                L.append(f"Thinking's measured cost ({cost['common']} common units): "
                         f"{_fmt(cost['a_s'], 0)} s → {_fmt(cost['b_s'], 0)} s per unit "
                         f"(x{_fmt(cost['s_ratio'], 2)}), {_fmt(cost['a_tokens'], 0)} → "
                         f"{_fmt(cost['b_tokens'], 0)} completion tokens per unit "
                         f"(x{_fmt(cost['tokens_ratio'], 2)}).")
                L.append("")
            if th.get("verdict") == "off" and rec["components"]:
                L.append("The component verdicts were measured with thinking on; without "
                         "thinking they hold only as far as the no-thinking composite "
                         "and baseline arms show.")
                L.append("")
            sp = rec["spec"]
            envs = " ".join(f"{k}={v}" for k, v in sorted(sp["env"].items()) if v not in ("", "0"))
            L.append("```")
            L.append(f"{envs} python3 evals/run_eval.py {shlex.join(sp['args'])}".strip())
            L.append("```")
            L.append("")
            L.append("`--suite`, `--no-cache` and `--wall-scale` are eval mechanics. The daily "
                     "config is the switches and the thinking toggle; the thinking table below "
                     "says how many passes fit inside the unwidened wall.")
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
    for ln, rn in camp.get("contrasts") or []:
        if ln in rows and rn in rows:
            pairs.append((ln, rows[ln], rn, rows[rn]))
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

    # Thinking vs no thinking
    think_of = {x["id"]: arm_thinking(camp, st, x["id"]) for x in arms}
    if any(v == "on" for v in think_of.values()):
        L.append("## Thinking vs no thinking")
        L.append("")
        L.append("Per arm, the price of its pass count. Mean completion tokens include the "
                 "reasoning tokens the server counted. *At base wall* = the passes that "
                 "finished inside the unwidened wall (the unit's wall x the `--jobs` "
                 "contention scale): for a wall-scaled thinking arm, what it scores with "
                 "its no-thinking twin's time.")
        L.append("")
        L.append("| Arm | Thinking | Wall x | Passed | Scorable | Passed at base wall "
                 "| Mean agent s / unit | Mean completion tokens / unit | Run h |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for x in arms:
            ast = st["arms"][x["id"]]
            s = score(rows[x["id"]])
            inside = passed_at_base_wall(rows[x["id"]], walls, arm_jobs(st, x["id"]))
            hours = ((ast.get("ended_at") or 0) - (ast.get("started_at") or 0)) / 3600 \
                if ast.get("ended_at") else None
            L.append(f"| {x['id']} | {think_of[x['id']] or 'server default'} "
                     f"| {float(ast.get('wall_scale') or 1.0):g} | {s['passed']} | {s['scorable']} "
                     f"| {len(inside) if inside is not None else '-'} | {_fmt(s['mean_s'], 0)} "
                     f"| {_fmt(s['mean_tokens'], 0)} | {_fmt(hours, 2)} |")
        L.append("")
        tpairs = [(ln, rn) for ln, _, rn, _ in pairs
                  if ln in rows and rn in rows and think_of.get(ln) != think_of.get(rn)]
        rec = camp.get("recommend") or {}
        if rec.get("no_thinking") in rows and rec.get("composite") in rows:
            p = (rec["no_thinking"], rec["composite"])
            tpairs = [p] + [q for q in tpairs if q != p and q != p[::-1]]
        for ln, rn in tpairs:
            f = flips(rows[ln], rows[rn])
            c = paired_cost(rows[ln], rows[rn])
            L.append(f"- **{ln}** (thinking {think_of.get(ln) or 'default'}) → **{rn}** "
                     f"(thinking {think_of.get(rn) or 'default'}), {f['common']} common units: "
                     f"{f['a_passed']} → {f['b_passed']} passed ({len(f['rescued'])} rescued, "
                     f"{len(f['regressed'])} regressed, p {f['p']:.3f}); per unit "
                     f"{_fmt(c['a_s'], 0)} s → {_fmt(c['b_s'], 0)} s "
                     f"(x{_fmt(c['s_ratio'], 2)}), {_fmt(c['a_tokens'], 0)} → "
                     f"{_fmt(c['b_tokens'], 0)} completion tokens (x{_fmt(c['tokens_ratio'], 2)})")
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
                cs = contention_scale(arm_jobs(st, x["id"]))
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
        p.add_argument("--campaign", default=DEFAULT_CAMPAIGN,
                       help=f"a name in evals/campaigns/ or a path (default {DEFAULT_CAMPAIGN}; "
                            f"v5-harness is the 2026-11-16 GLM build-up chain A-F)")
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
    p.add_argument("--order", help="ARM[,ARM]: run these arms first, in this order, then the "
                                   "rest in the campaign's order")
    p.add_argument("--include", help="ARM[,ARM]: add optional arms (e.g. a second seed) to the chain")
    p.add_argument("--base-hours", type=float,
                   help="hours of the campaign's reference arm (time_x 1.0) for this model, for "
                        "the ETAs (default: calibrated from finished arms, else eta.base_h)")

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
