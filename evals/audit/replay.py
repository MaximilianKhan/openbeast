#!/usr/bin/env python3
"""Grade a candidate solution for one unit exactly as run_eval does:
cleanup -> setup -> drop the solution at the task's dest -> pre_validate ->
validate at --scale (2.0 = the --jobs 4 contention scale) -> cleanup, under
the unit's fixture lock (it waits for any other user of the fixture).

  python3 evals/audit/replay.py <unit> <solution> [--dest PATH] [--scale 2.0] [--repo DIR]

<solution> is a file (copied to the task's dest) or a directory (copied over
the dest, for overlay-style units). Prints PASS/FAIL and the validator's
output tail; exits 0 on PASS, 1 on FAIL, 2 when setup fails.

The auditor uses it to answer "does the model's FINAL solution, rebuilt from
its transcript, reproduce the recorded failure?". --repo is the tree the
measured run used (default: this checkout, or $AUDIT_REPO): a validator fixed
since then judges differently. Run it with the eval interpreter and the
toolchains on PATH (python 3.12, go, rustc, zig 0.16.0, a C/C++ compiler).
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("unit")
    ap.add_argument("solution")
    ap.add_argument("--dest", help="where the solution goes (default: the task's dest)")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--repo", default=os.environ.get("AUDIT_REPO")
                    or os.path.dirname(os.path.dirname(HERE)))
    a = ap.parse_args(argv)
    sys.path[:0] = [os.path.join(a.repo, "evals"), os.path.join(a.repo, "agents")]
    import run_eval as R  # noqa: PLC0415

    tasks = {t["id"]: t for t in R.load_tasks(None)}
    task = tasks.get(a.unit)
    if task is None:
        print(f"no unit {a.unit} in {a.repo}", file=sys.stderr)
        return 2
    dest = a.dest or task.get("dest")
    if not dest:
        print(f"{a.unit} declares no dest: pass --dest", file=sys.stderr)
        return 2
    R.toolchain_pins()
    with R.fixture_lock(task, log=print):
        R.run_cleanup(task, log=lambda *_: None)
        if not R.run_setup(task, log=print):
            print("SETUP FAILED")
            return 2
        if os.path.isdir(a.solution):
            shutil.copytree(a.solution, dest, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(a.solution, dest)
        R.run_pre_validate(task, log=lambda *_: None)
        ok, out = R.run_validation(task, timeout_scale=a.scale)
        R.run_cleanup(task, log=lambda *_: None)
    print("PASS" if ok else "FAIL")
    print((out or "").strip()[-4000:])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
