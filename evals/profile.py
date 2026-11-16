#!/usr/bin/env python3
"""A v5 profile of one model inside a wall-clock budget (brief §1, §5, §6.5).

  python3 evals/profile.py --model-name "GLM-5.3-Flash" \\
      --base-url https://spark-1.tail4109f9.ts.net:8443/v1 --api-key-env SPARK_KEY \\
      --slots 4 --jobs 4 --reasoning-budget 0 --budget-h 4

Phases, all in this one process (the arm scheduler inside run_eval keeps a
base group on one worker across arms, so arms never share /tmp/eval_* dirs):

  1. preflight   zig 0.16.0 (the pack's pin), escalate.gate_state() serves
                 zig, the validator toolchains on THIS host (tools run where
                 the harness runs, not where the model runs), a finite
                 reasoning budget (or thinking off), the era stamped, and
                 OPENBEAST_EVAL_ENV_ERA=1 so weights/engine/toolchains enter
                 the cache key.
  2. L0          the whole suite, bare: the board row. Measures
                 k = wall seconds / reference seconds (est_s) on this server.
  3. plan        cost each remaining arm as k x its est_s; if L1 + L2 do not
                 fit what is left, cut L2 in the fixed order (§5.4):
                 stratified L2 (every L0 failure + a seeded third of the L0
                 passes; regressions estimated by Horvitz-Thompson), then
                 drop the probes, then rust, then c. L0 and L1 are never cut.
  4. L1 + L2     one run_eval call, arms=[L1, L2], with the plan's unit sets.

A global deadline (start + --budget-h) rides through every phase: a unit
that would start after it is recorded `budget_skip` — never run, never
scored as a failure. The state file (evals/results/profile-<slug>-<ts>.json)
makes a profile resumable (--resume): finished phases are skipped and the
cache replays every banked unit of an interrupted one. The era is checked
at every phase boundary; a profile never pairs rows across eras.

At the end, `evals/ablation.py --profile <state>` prints the contrasts.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EVALS_DIR)
sys.path.insert(0, EVALS_DIR)
sys.path.insert(0, os.path.join(REPO, "agents"))

import suite_manifest  # noqa: E402

STATE_VERSION = 1
ZIG_PIN = "0.16.0"
HOST_TOOLS = ("go", "gcc", "g++", "rustc", "zig", "git")
CUT_LANG = {"drop_rust_l2": "rust", "drop_c_l2": "c"}


# --- planning (pure; tested without a model) ---------------------------------------

def measured_k(l0_rows: list[dict], est_by_id: dict, wall_s: float) -> float | None:
    """k = wall seconds per reference second, over the units L0 actually ran
    live (cache replays and budget skips cost nothing and say nothing)."""
    live = [r for r in l0_rows if not r.get("from_cache") and not r.get("shared_from")
            and r.get("reason") not in ("budget_skip", "not_in_arm", "skipped_cache_miss")]
    ref_s = sum(est_by_id.get(r["id"], 0) for r in live)
    return (wall_s / ref_s) if ref_s > 0 and wall_s > 0 else None


def stratified_sample(l0_rows: list[dict], candidates: set, frac: float, seed: str) -> dict:
    """{unit id: inclusion probability}: every L0 failure (pi 1) and a seeded
    `frac` of the L0 passes (pi = frac). Deterministic in `seed`."""
    rows = {r["id"]: r for r in l0_rows}
    fails = sorted(u for u in candidates if u in rows and not rows[u].get("passed"))
    passes = sorted(u for u in candidates if u in rows and rows[u].get("passed"))
    rng = random.Random(seed)
    k = round(len(passes) * frac)
    sampled = sorted(rng.sample(passes, k)) if passes else []
    pi = {u: 1.0 for u in fails}
    # Units L0 did not measure (budget skip) cannot be stratified: keep them.
    pi.update({u: 1.0 for u in candidates if u not in rows})
    pi.update({u: frac for u in sampled})
    return pi


def plan_arms(units: dict, l0_rows: list[dict], k: float, remaining_s: float,
              cut_order: list[str], frac: float, seed: str) -> dict:
    """The L1/L2 plan for the time left.

    units: {effective id: manifest unit}. Returns {l1, l2 (sets of ids),
    pi (stratified inclusion probabilities, {} if not stratified), cuts
    (applied, in order), cost_s {L1, L2}, fits}."""
    l1 = {u for u, m in units.items() if "L1" in m.get("arms", [])}
    l2 = {u for u, m in units.items() if "L2" in m.get("arms", [])}
    est = {u: float(m.get("est_s") or 0) for u, m in units.items()}

    def cost(ids):
        # The sample is drawn before costing: every unit left runs in full.
        return k * sum(est[u] for u in ids)

    pi: dict = {}
    cuts: list[str] = []
    c1 = cost(l1)
    c2 = cost(l2)
    for cut in cut_order:
        if c1 + c2 <= remaining_s:
            break
        if cut == "stratified_l2":
            pi = stratified_sample(l0_rows, l2, frac, seed)
            l2 = {u for u in l2 if u in pi}
        elif cut == "drop_probes_l2":
            l2 = {u for u in l2 if units[u].get("role") != "probe"}
        elif cut in CUT_LANG:
            l2 = {u for u in l2 if suite_manifest.norm_lang(units[u].get("lang")) != CUT_LANG[cut]}
        else:
            raise ValueError(f"unknown cut {cut!r}")
        pi = {u: p for u, p in pi.items() if u in l2}
        cuts.append(cut)
        c2 = cost(l2)
    return {"l1": l1, "l2": l2, "pi": pi, "cuts": cuts,
            "cost_s": {"L1": round(c1), "L2": round(c2)},
            "fits": c1 + c2 <= remaining_s}


# --- preflight --------------------------------------------------------------------------

def preflight(base_url: str, declared_rb: str | None, allow_uncapped: bool) -> list[str]:
    """Problems that make a profile's numbers untrustworthy ([] = go)."""
    problems = []
    for tool in HOST_TOOLS:
        if not shutil.which(tool):
            problems.append(f"{tool} is not on PATH on the harness host (validators "
                            f"and diagnostics run HERE, not where the model runs)")
    if shutil.which("zig"):
        try:
            v = subprocess.run(["zig", "version"], capture_output=True, text=True,
                               timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            v = "?"
        if v != ZIG_PIN:
            problems.append(f"zig {v} installed; the pack and the escalation index are "
                            f"stamped {ZIG_PIN}")
    try:
        from lang import escalate as E  # noqa: PLC0415
        gate = E.gate_state()
        if not (gate.get("zig") or {}).get("served"):
            problems.append(f"escalate.gate_state() does not serve zig here: {gate.get('zig')}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"escalate.gate_state() failed ({type(e).__name__}: {e})")
    import run_eval  # noqa: PLC0415
    info = run_eval.capture_server_config(base_url) or \
        run_eval.capture_remote_server_info(base_url, declared_rb)
    rb = str(info.get("reasoning_budget", "")).strip()
    if (not rb or rb == "-1") and not allow_uncapped:
        problems.append("the server's reasoning budget is uncapped or undeclared: a profile "
                        "needs a finite budget or thinking off (--reasoning-budget N|0); "
                        "uncapped Qwen3.8 took 36.55 h for no gain")
    return problems


def era_check(expected: str) -> str | None:
    import cache  # noqa: PLC0415
    cache._context_cache.clear()
    now = cache.context_hash()
    if now != expected:
        return f"the eval era moved mid-profile ({expected} -> {now}); rows would not pair"
    return None


# --- state ------------------------------------------------------------------------------

def save_state(path: str, st: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1, default=sorted)
    os.replace(tmp, path)


def _l0_complete(path: str | None) -> bool:
    if not path or not os.path.isfile(path):
        return False
    with open(path) as f:
        d = json.load(f)
    rows = d.get("tasks", [])
    return len(rows) == (d.get("summary") or {}).get("total") and not any(
        r.get("reason") in ("budget_skip", "skipped_cache_miss", "server_unhealthy",
                            "low_disk") for r in rows)


def run_profile(a, run_eval) -> int:
    import cache  # noqa: PLC0415
    tasks = run_eval.load_tasks(None)
    man = suite_manifest.load_manifest(a.suite, tasks=tasks, strict=not a.allow_missing)
    units = man["by_task"]                                   # effective id -> unit
    est = {u: float(m.get("est_s") or 0) for u, m in units.items()}

    if a.resume:
        with open(a.resume) as f:
            st = json.load(f)
        state_path = a.resume
        print(f"Resuming {state_path} (phase {st.get('phase')})")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = run_eval.slugify(a.model_name or run_eval.detect_model(a.base_url))
        os.makedirs(run_eval.RESULTS_DIR, exist_ok=True)
        state_path = os.path.join(run_eval.RESULTS_DIR, f"profile-{slug}-{stamp}.json")
        now = time.time()
        st = {"version": STATE_VERSION, "suite": a.suite, "model_slug": slug,
              "model_name": a.model_name, "base_url": a.base_url, "budget_h": a.budget_h,
              "started_at": now, "deadline": now + a.budget_h * 3600,
              "pair_id": f"{slug}-{stamp}", "era": cache.context_hash(),
              "phase": "start", "results": {}}
        save_state(state_path, st)
    if a.fresh_deadline:
        st["deadline"] = time.time() + a.budget_h * 3600
    deadline = st["deadline"]
    common = dict(base_url=a.base_url, model_name=a.model_name, jobs=a.jobs, slots=a.slots,
                  suite=a.suite, reasoning_budget=a.reasoning_budget, deadline=deadline,
                  pair_id=st["pair_id"], use_cache=not a.no_cache)

    # --- L0 -------------------------------------------------------------------
    if not _l0_complete(st["results"].get("L0")):
        bad = era_check(st["era"])
        if bad:
            raise SystemExit(bad)
        t0 = time.time()
        out = run_eval.run_eval(arms=["L0"], **common)
        st["results"]["L0"] = out["paths"]["L0"]
        st["l0_wall_s"] = round(time.time() - t0, 1)
        st["phase"] = "L0"
        save_state(state_path, st)
    with open(st["results"]["L0"]) as f:
        l0 = json.load(f)
    k = measured_k(l0["tasks"], est, st.get("l0_wall_s") or 0)
    st["k"] = k if k is not None else a.k_default
    st["k_measured"] = k is not None
    print(f"k = {st['k']:.3f} wall s per reference s"
          f"{'' if k is not None else ' (default: L0 ran nothing live)'}")

    # --- plan -----------------------------------------------------------------
    remaining = max(0.0, deadline - time.time())
    plan = plan_arms(units, l0["tasks"], st["k"], remaining, man.get("cut_order") or [],
                     float(man.get("stratified_pass_fraction") or 1 / 3), st["pair_id"])
    st["plan"] = {"l1": sorted(plan["l1"]), "l2": sorted(plan["l2"]), "pi": plan["pi"],
                  "cuts": plan["cuts"], "cost_s": plan["cost_s"], "fits": plan["fits"],
                  "remaining_s": round(remaining)}
    save_state(state_path, st)
    print(f"Plan: L1 {len(plan['l1'])} units (~{plan['cost_s']['L1'] / 60:.0f} min), "
          f"L2 {len(plan['l2'])} units (~{plan['cost_s']['L2'] / 60:.0f} min); "
          f"cuts {plan['cuts'] or 'none'}; {remaining / 60:.0f} min left"
          + ("" if plan["fits"] else " — STILL OVER: the deadline will budget_skip the tail"))
    if a.plan_only:
        print(f"State: {state_path}")
        return 0

    # --- L1 + L2 ----------------------------------------------------------------
    if st.get("phase") != "done":
        bad = era_check(st["era"])
        if bad:
            raise SystemExit(bad)
        meta = {"L2": {"stratified": bool(plan["pi"]), "pi": plan["pi"], "cuts": plan["cuts"]},
                "L1": {"cuts": []}}
        out = run_eval.run_eval(arms=["L1", "L2"], arm_units={"L1": plan["l1"], "L2": plan["l2"]},
                                arm_meta=meta, **common)
        st["results"].update(out["paths"])
        st["phase"] = "done"
        st["finished_at"] = time.time()
        save_state(state_path, st)

    import ablation  # noqa: PLC0415
    print()
    reports = []
    for x, y in (("L0", "L1"), ("L1", "L2"), ("L0", "L2")):
        with open(st["results"][x]) as fa, open(st["results"][y]) as fb:
            rep = ablation.report(json.load(fa), json.load(fb))
        reports.append(rep)
        print(ablation.render(rep))
        print()
    st["summary"] = [{"a": r["a"], "b": r["b"],
                      "contrasts": [{k2: c[k2] for k2 in ("contrast", "n", "b", "c", "net",
                                                          "p", "p_label", "within_churn")}
                                    for c in r["contrasts"]]} for r in reports]
    save_state(state_path, st)
    print(f"State: {state_path}\n(re-run the report: python3 evals/ablation.py --profile {state_path})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-name", help="model name (default: the server's /v1/models)")
    ap.add_argument("--model", help="a benchmark_all MODELS slug (sets name, base URL, "
                                    "slots and key env for remote entries)")
    ap.add_argument("--base-url", default="http://localhost:8080/v1")
    ap.add_argument("--api-key-env")
    ap.add_argument("--suite", default="v5")
    ap.add_argument("--budget-h", type=float, default=4.0)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--slots", type=int)
    ap.add_argument("--reasoning-budget",
                    help="the budget a non-llama server runs with (0 = thinking off)")
    ap.add_argument("--k-default", type=float, default=1.0,
                    help="k when L0 ran nothing live (a full cache replay)")
    ap.add_argument("--resume", help="a profile state file to continue")
    ap.add_argument("--fresh-deadline", action="store_true",
                    help="--resume with a new --budget-h window from now")
    ap.add_argument("--plan-only", action="store_true", help="stop after printing the plan")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--allow-missing", action="store_true",
                    help="run the manifest units that have task files, skip the rest")
    ap.add_argument("--allow-uncapped", action="store_true")
    ap.add_argument("--skip-preflight", action="store_true")
    a = ap.parse_args(argv)

    if a.model:
        import benchmark_all  # noqa: PLC0415
        m = next((x for x in benchmark_all.MODELS if x["slug"] == a.model), None)
        if m is None:
            ap.error(f"no MODELS entry {a.model!r}")
        a.model_name = a.model_name or m["name"]
        if m.get("base_url"):
            a.base_url = m["base_url"]
            a.slots = a.slots or m.get("slots")
            a.api_key_env = a.api_key_env or m.get("api_key_env")
            if a.reasoning_budget is None and m.get("reasoning_budget") is not None:
                a.reasoning_budget = str(m["reasoning_budget"])
    if a.api_key_env:
        key = os.environ.get(a.api_key_env, "")
        if not key:
            ap.error(f"--api-key-env {a.api_key_env}: that variable is empty")
        os.environ["OPENBEAST_API_KEY"] = key
        os.environ["OPENBEAST_AGENT_INFERENCE_URL"] = a.base_url
    os.environ["OPENBEAST_EVAL_ENV_ERA"] = "1"
    import run_eval  # noqa: PLC0415
    run_eval._install_signal_reaper()
    if not a.skip_preflight and not a.resume:
        problems = preflight(a.base_url, a.reasoning_budget, a.allow_uncapped)
        if problems:
            print("PREFLIGHT REFUSED:")
            for p in problems:
                print(f"  - {p}")
            print("(--skip-preflight to run anyway; the profile's numbers then carry the risk)")
            return 2
    return run_profile(a, run_eval)


if __name__ == "profile":
    # This file shadows the stdlib `profile` module for anything that runs
    # with evals/ on sys.path (the harness, most tests). cProfile does
    # `import profile as _pyprofile` and needs its Profile/_Utils/run: load
    # the real module and re-export what this one does not define, so both
    # keep working.
    import importlib.util as _ilu
    import sysconfig as _sc
    _spec = _ilu.spec_from_file_location(
        "_stdlib_profile", os.path.join(_sc.get_paths()["stdlib"], "profile.py"))
    if _spec and _spec.loader:
        _std = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_std)
        for _k, _v in vars(_std).items():
            if not _k.startswith("__") and _k not in globals():
                globals()[_k] = _v

if __name__ == "__main__":
    sys.exit(main())
