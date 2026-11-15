#!/usr/bin/env python3
"""Leak probe for 178_codebase_qa: an "agent" that only harvests.

Scans everything readable under the fixture dir for answer-like material
(files named like answers/expected/solution/oracle/check, or any JSON
object with q1..q12 keys) and submits whatever it finds as answers.json.
On a leak-free fixture it finds nothing and submits {}, which must fail.
"""

import json
import os
import re
import sys

D = sys.argv[1] if len(sys.argv) > 1 else "/tmp/eval_codeqa"
NAME = re.compile(r"answer|expect|solution|oracle|check|golden", re.I)
found = {}
for dirpath, _, files in os.walk(D):
    for f in files:
        p = os.path.join(dirpath, f)
        if f == "answers.json" and dirpath == D:
            continue
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if NAME.search(f):
            print("leak-probe: candidate", os.path.relpath(p, D))
        try:
            doc = json.loads(text)
        except ValueError:
            continue
        if isinstance(doc, dict) and any(f"q{i}" in doc for i in range(1, 13)):
            print("leak-probe: answer-shaped JSON at", os.path.relpath(p, D))
            found.update(doc)
with open(os.path.join(D, "answers.json"), "w") as fh:
    json.dump(found, fh)
print(f"leak-probe: submitted {len(found)} harvested answers")
