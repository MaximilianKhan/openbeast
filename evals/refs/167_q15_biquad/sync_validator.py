#!/usr/bin/env python3
"""Write validator.py.in into every variant of evals/tasks/167_q15_biquad.json.

The six variants run the same validator with @LANG@ filled in. Edit the
template, then run this; --check fails if the task JSON is stale
(tests/test_eval_v5_families_a.py runs it).

  python3 evals/refs/167_q15_biquad/sync_validator.py [--check]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tasks", "167_q15_biquad.json")


def build():
    with open(os.path.join(HERE, "validator.py.in")) as f:
        tmpl = f.read()
    with open(OUT) as f:
        task = json.load(f)
    for v in task["variants"]:
        v["validation"]["script"] = tmpl.replace("@LANG@", v["language"])
    return json.dumps(task, indent=1, ensure_ascii=False) + "\n"


def main():
    text = build()
    with open(OUT) as f:
        same = f.read() == text
    if "--check" in sys.argv:
        print("up to date" if same else "STALE: rerun sync_validator.py")
        return 0 if same else 1
    if not same:
        with open(OUT, "w") as f:
            f.write(text)
    print("wrote" if not same else "unchanged", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
