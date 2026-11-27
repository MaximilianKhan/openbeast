#!/usr/bin/env python3
"""Model-free prover for 176_intermittent_500s (directory-overlay solutions).

Drives the harness's own lifecycle (run_eval.run_cleanup -> run_setup ->
[overlay] -> run_pre_validate -> run_validation -> run_cleanup), so the 30 s
x scale validator timeout, rlimits and reaping are what a live unit sees.
Arms come from PROVE.json next to this file:

  ref x repeat          must pass every time (flake check)
  pass[]                alternative correct fixes, must pass
  wrong[]               must fail, and with the asserted failure line
  empty                 the fixture as shipped, must fail as asserted
  leak                  harness-gaming probes, must fail as asserted
                        ([name, regex] or [name, regex, "ref"|"empty"]:
                        the layer it goes on, ref by default)
  slow                  slow or hanging fixes, must fail as asserted within
                        budget (one [name, regex] or a list of them)
  ref @ scale 2.0       must pass (--jobs contention)

Every arm must also finish within budget_s x scale.

The prover runs from a throwaway cwd (removed at exit), and after every arm
it looks for a planted module (PLANTED below) in its own cwd and in the cwd
of every ancestor process: an arm, or a cheat that beats a defence, must
leave nothing behind for a later python started there. A find is removed
and makes the run NOT PROVEN.

usage: prove.py [--repo REPO]      (run with the harness python, >= 3.10)
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
ap.add_argument("--repeat", type=int, default=None)
a = ap.parse_args()
P = json.load(open(os.path.join(HERE, "PROVE.json")))

sys.path.insert(0, os.path.join(a.repo, "evals"))
sys.path.insert(0, os.path.join(a.repo, "agents"))
import run_eval as R  # noqa: E402

(task,) = [t for t in R.load_tasks([P["unit"]]) if t["id"] == P["unit"]]

# Hold the unit's /tmp/eval_* roots for the whole proof: a sweep or another
# prover on this host must not rm -rf them under our arms, nor we under its
# units (run_eval.fixture_lock; the kernel drops it when this process exits).
try:
    _FIXTURE_LOCK = R.fixture_lock(task, wait=False).__enter__()
except R.FixtureBusy as _e:
    sys.exit("%s: NOT PROVEN (fixture busy: %s)" % (task["id"], _e))

# Names a plant arm (or a verifier's cheat) drops to hijack a later python.
PLANTED = ("urllib", "copy.py", "tarfile.py", "base64.py", "zlib.py", "io.py", "sitecustomize.py",
           "usercustomize.py")


def _pid_info(pid):
    """(ppid, cwd) of pid, or (None, None)."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/%d/stat" % pid) as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            return ppid, os.readlink("/proc/%d/cwd" % pid)
        except (OSError, ValueError, IndexError):
            return None, None
    if sys.platform != "darwin":
        return None, None
    import ctypes
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    bsd = ctypes.create_string_buffer(136)
    if lib.proc_pidinfo(pid, 3, ctypes.c_uint64(0), bsd, 136) <= 0:   # PROC_PIDTBSDINFO
        return None, None
    ppid = int.from_bytes(bsd.raw[16:20], "little")
    vp = ctypes.create_string_buffer(2352)
    if lib.proc_pidinfo(pid, 9, ctypes.c_uint64(0), vp, 2352) <= 0:   # PROC_PIDVNODEPATHINFO
        return ppid, None
    return ppid, vp.raw[152:152 + 1024].split(b"\0", 1)[0].decode() or None


def ancestor_cwds():
    out, pid, seen = [], os.getppid(), set()
    while pid and pid > 1 and pid not in seen and len(seen) < 32:
        seen.add(pid)
        ppid, cwd = _pid_info(pid)
        if cwd:
            out.append(cwd)
        pid = ppid
    return out


WORK = tempfile.mkdtemp(prefix="prove176-")
os.chdir(WORK)
# The only cwd an ancestor-planting arm (wrong_plant_ancestors) may write to.
open(os.path.join(WORK, ".prove176-cwd"), "w").close()
WATCH = sorted(set([WORK, HERE] + ancestor_cwds()))
# Only names that were NOT there before the run count as planted.
PRESENT = {(d, n) for d in WATCH for n in PLANTED if os.path.lexists(os.path.join(d, n))}


def setup_probe():
    """Whether the harness's next setup still builds the fixture (a plant
    left in the prover's cwd must not hijack it)."""
    R.run_cleanup(task, log=quiet)
    ok = R.run_setup(task, log=quiet) and os.path.isfile(os.path.join(DEST, "shopd", "pagination.py"))
    R.run_cleanup(task, log=quiet)
    return ok


def leftovers():
    """Planted names that appeared in a watched cwd; each is removed. On a
    host without the child sandbox (Linux) a plant in the prover's own cwd
    cannot be prevented and is only reported, provided setup_probe passed."""
    found = []
    for d in WATCH:
        for n in PLANTED:
            p = os.path.join(d, n)
            if (d, n) in PRESENT or not os.path.lexists(p):
                continue
            if not (d == WORK and sys.platform != "darwin"):
                found.append(p)
            else:
                print("    note: %s planted in the prover cwd (no sandbox on this host)" % n)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.unlink(p)
                except OSError:
                    pass
    return found
DEST = P["dest_dir"]
quiet = lambda *_: None  # noqa: E731


def overlay(name):
    src = os.path.join(HERE, name)
    assert os.path.isdir(src), src
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            s = os.path.join(root, fn)
            d = os.path.join(DEST, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(s, d)


def arm(layers, scale):
    R.run_cleanup(task, log=quiet)
    if not R.run_setup(task, log=print):
        return None, 0.0, "SETUP FAILED"
    for name in layers:
        overlay(name)
    R.run_pre_validate(task, log=quiet)
    t0 = time.time()
    ok, out = R.run_validation(task, timeout_scale=scale)
    dt = time.time() - t0
    R.run_cleanup(task, log=quiet)
    return ok, dt, out.strip()


ref = P["ref"]
plan = [("ref#%d" % (i + 1), [ref], True, None, 1.0) for i in range(a.repeat or P["repeat"])]
plan += [("pass:" + n, [n], True, rx, 1.0) for n, rx in P.get("pass", [])]
plan += [("wrong:" + n, [ref, n], False, rx, 1.0) for n, rx in P["wrong"]]
plan.append(("empty(as shipped)", [], False, P["empty"], 1.0))
def arms(v):
    return [v] if isinstance(v[0], str) else v


for lk in arms(P["leak"]):
    plan.append(("leak:" + lk[0], ([] if lk[2:] == ["empty"] else [ref]) + [lk[0]], False, lk[1], 1.0))
for sl in arms(P["slow"]):
    plan.append(("slow:" + sl[0], [ref, sl[0]], False, sl[1], 1.0))
plan.append(("ref@scale2.0", [ref], True, None, 2.0))

bad = 0
for label, layers, want, rx, scale in plan:
    ok, dt, out = arm(layers, scale)
    last = out.splitlines()[-1] if out else ""
    good, why = ok is want, ""
    if good and rx and not re.search(rx, last):
        good, why = False, " [wrong failure class; wanted /%s/]" % rx
    if good and R.validator_timed_out(out):
        good, why = False, " [validator_timeout]"
    if good and dt > P["budget_s"] * scale:
        good, why = False, " [over the %.0fs budget]" % (P["budget_s"] * scale)
    if not setup_probe():
        good, why = False, why + " [the next setup was hijacked]"
    left = leftovers()
    if left:
        good, why = False, why + " [left behind: %s]" % ", ".join(left)
    bad += not good
    print("%s %-34s passed=%-5s %5.2fs  %s%s" % ("OK " if good else "BAD", label, ok, dt, last[:110], why))
shutil.rmtree(WORK, ignore_errors=True)
print("watched %d cwd(s) for plants: %s" % (len(WATCH), ", ".join(WATCH)))
print("%s: %s" % (P["unit"], "PROVEN" if not bad else "NOT PROVEN (%d bad arm(s))" % bad))
sys.exit(1 if bad else 0)
