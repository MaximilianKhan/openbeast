#!/usr/bin/env python3
"""Model-free proof of unit 175_crdt_orset_rga (the v5 authoring gate, brief 6.3).

Drives run_eval's own lifecycle (run_cleanup -> run_setup -> drop solution ->
run_pre_validate -> run_validation -> run_cleanup), so the 30 s x scale
validator timeout, rlimits and reaping are exactly what a live unit sees.
Arms come from prove.json:
  ref x repeat     must pass every time
  wrong/leak/slow  must fail with the validator's last line matching the
                   pinned regex (the intended failure class), inside budget
  empty            must fail with "missing ..."
  ref @ scale2     must pass

usage: python3 evals/refs/175/prove.py [--repeat N]     (python >= 3.10)
"""
import argparse, json, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "evals"))
sys.path.insert(0, os.path.join(REPO, "agents"))
import run_eval as R  # noqa: E402

M = json.load(open(os.path.join(HERE, "prove.json")))
ap = argparse.ArgumentParser()
ap.add_argument("--repeat", type=int, default=M["repeat"])
a = ap.parse_args()
quiet = lambda *_: None
(task,) = [t for t in R.load_tasks([M["unit"]]) if t["id"] == M["unit"]]

# Hold the unit's /tmp/eval_* roots for the whole proof: a sweep or another
# prover on this host must not rm -rf them under our arms, nor we under its
# units (run_eval.fixture_lock; the kernel drops it when this process exits).
try:
    _FIXTURE_LOCK = R.fixture_lock(task, wait=False).__enter__()
except R.FixtureBusy as _e:
    sys.exit("%s: NOT PROVEN (fixture busy: %s)" % (task["id"], _e))
assert task["dest"] == M["dest"] and task["ref_stem"] == "175/crdt"


def arm(sol, scale=1.0):
    R.run_cleanup(task, log=quiet)
    if not R.run_setup(task, log=print):
        return None, 0.0, "SETUP FAILED"
    if sol:
        shutil.copy(sol, M["dest"])
    R.run_pre_validate(task, log=quiet)
    t0 = time.time()
    ok, out = R.run_validation(task, timeout_scale=scale)
    dt = time.time() - t0
    R.run_cleanup(task, log=quiet)
    return ok, dt, (out.strip().splitlines() or [""])[-1][:90]


ref = os.path.join(HERE, M["ref"])
rows = []
for i in range(a.repeat):
    ok, dt, tail = arm(ref)
    rows.append((f"ref#{i + 1}", ok is True, ok, dt, tail))
for group in ("wrong", "leak", "slow"):
    for name, rx in M[group].items():
        ok, dt, tail = arm(os.path.join(HERE, "bad", name + ".py"))
        good = ok is False and re.search(rx, tail) is not None and dt < R.VALIDATION_TIMEOUT_S
        rows.append((f"{group}:{name}", good, ok, dt, tail))
ok, dt, tail = arm(None)
rows.append(("empty", ok is False and re.search(M["empty"], tail) is not None, ok, dt, tail))
ok, dt, tail = arm(ref, scale=M["scale2"])
rows.append((f"ref@scale{M['scale2']}", ok is True, ok, dt, tail))

bad = 0
for label, good, ok, dt, tail in rows:
    bad += not good
    print(f"{'OK ' if good else 'BAD'} {label:22s} passed={ok!s:5s} {dt:5.2f}s  {tail}")
print(f"{M['unit']}: {'PROVEN' if not bad else f'NOT PROVEN ({bad} bad arm(s))'}")
sys.exit(1 if bad else 0)
