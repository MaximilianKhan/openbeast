#!/usr/bin/env python3
"""Generate evals/tasks/176_intermittent_500s.json from the sources here.

The task JSON is a build product. Sources:
  seed/            the shipped fixture: the buggy service, its green visible
                   suite, docs, and logs/shopd.log
  src/gen_log.py   regenerates seed/logs/shopd.log from seed/ (run on Python
                   3.12 for byte-identical tracebacks; other versions keep
                   the committed log)
  src/hidden_tests.py, src/validator.py
  ref/, ref_alt/, wrong_*/, hang/, leak/   prover overlays (see PROVE.json)

The fixture ships as one zlib'd tar in a heredoc: a Linux argv string is
capped at 128 KiB (MAX_ARG_STRLEN), and setup and validation each travel as
a single argv element.
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SEED = os.path.join(HERE, "seed")
SRC = os.path.join(HERE, "src")
D = "/tmp/eval_shopd500"
ARG_MAX = 120 * 1024


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def shipped_files():
    out = []
    for root, dirs, files in os.walk(SEED):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for fn in sorted(files):
            if fn.endswith(".pyc"):
                continue
            out.append(os.path.relpath(os.path.join(root, fn), SEED))
    return sorted(out)


# 1. the log is generated from the seed it ships with
if sys.version_info[:2] == (3, 12):
    subprocess.run([sys.executable, "-B", os.path.join(SRC, "gen_log.py"), SEED,
                    os.path.join(SEED, "logs", "shopd.log")], check=True)
else:
    print("note: Python %d.%d, keeping the committed logs/shopd.log" % sys.version_info[:2])

# 2. fixture payload: deterministic tar (sorted, fixed mtime/owner/mode)
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
    for rel in shipped_files():
        data = open(os.path.join(SEED, rel), "rb").read()
        ti = tarfile.TarInfo(rel)
        ti.size, ti.mtime, ti.mode, ti.uid, ti.gid = len(data), 1772485200, 0o644, 0, 0
        ti.uname = ti.gname = ""
        tar.addfile(ti, io.BytesIO(data))
payload = base64.b64encode(zlib.compress(buf.getvalue(), 9)).decode("ascii")
wrapped = "\n".join(payload[i:i + 100] for i in range(0, len(payload), 100))

setup = (
    "rm -rf %s && mkdir -p %s && python3 - %s <<'SHOPD_EOF'\n" % (D, D, D)
    + "import base64, io, sys, tarfile, zlib\n"
    + "blob = zlib.decompress(base64.b64decode(''.join('''\n" + wrapped + "\n'''.split())))\n"
    + "kw = {'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}\n"
    + "with tarfile.open(fileobj=io.BytesIO(blob), mode='r:') as t:\n"
    + "    t.extractall(sys.argv[1], **kw)\n"
    + "SHOPD_EOF\n"
)
assert len(setup.encode()) < ARG_MAX, "setup is %d bytes" % len(setup.encode())

# 3. validator: visible-test hashes + count, hidden tests
test_files = [r for r in shipped_files() if r.startswith("tests/") and r.endswith(".py")]
hashes = {r: hashlib.sha256(open(os.path.join(SEED, r), "rb").read()).hexdigest() for r in test_files}
sys.path.insert(0, SEED)
sys.path.insert(0, os.path.join(SEED, "tests"))
n_visible = unittest.defaultTestLoader.discover(os.path.join(SEED, "tests"),
                                                top_level_dir=os.path.join(SEED, "tests")).countTestCases()
hidden = read(SRC, "hidden_tests.py")
order = eval(hidden.split("\nORDER = ", 1)[1].strip())
validator = read(SRC, "validator.py")
for k, v in (("@@HASHES@@", json.dumps(hashes, indent=1, sort_keys=True)), ("@@N_VISIBLE@@", str(n_visible)),
             ("@@HIDDEN@@", repr(hidden)), ("@@ORDER@@", repr(order))):
    assert validator.count(k) == 1, k
    validator = validator.replace(k, v)
compile(validator, "validator", "exec")
assert len(validator.encode()) < ARG_MAX, "validator is %d bytes" % len(validator.encode())

TASK = f"""{D} holds shopd, an order and inventory service (Python 3.9+, standard library only): the shopd/ package (about 2.4 kLOC), its test suite in tests/ (green), its contract docs/API.md, operations notes docs/OPERATIONS.md, and logs/shopd.log, an excerpt of the production log. In production the service intermittently answers HTTP 500, and the excerpt contains every one of those incidents. Diagnose them from the log and the code, find the root causes, and fix them in shopd/.

Done means: the service meets docs/API.md. It never answers 500, and every response (status, body, headers) is the one docs/API.md specifies, whatever requests the App served before, and on whichever worker thread (docs/OPERATIONS.md describes the process model). Making an error go away is not a fix: a request the contract says succeeds must succeed with the documented response, and a request the contract says fails must fail with the documented 4xx error.

Grading: only the shopd/ package is graded; anything you write elsewhere is ignored. Every file in tests/ must stay byte-for-byte unchanged and the suite must stay green (run `python3 -m unittest discover -s tests` from {D}); you may add new test files. Hidden tests then build fresh Apps through the programmatic interface in docs/API.md section 1 (create_app with a FakeClock), replay the kinds of traffic the log shows, and check every response against docs/API.md. Keep that interface and the module layout unchanged. Standard library only; no network."""

task = {
    "id": "176_intermittent_500s",
    "name": "Intermittent 500s in a 25-file service, diagnose from logs",
    "difficulty": "hard",
    "tier": "xl",
    "category": "SWE / DevOps",
    "subcategory": "Production debugging from logs",
    "max_iter": 30,
    "est_s": 330,
    "max_wall_s": 1500,
    "role": "scored",
    "variants": [
        {
            "id": "a",
            "language": "python",
            "ref_stem": "176_intermittent_500s/ref",
            "dest": D,
            "setup": setup,
            "task": TASK,
            "validation": {"type": "python", "script": validator},
            "cleanup": f"rm -rf {D}",
        }
    ],
}

out = os.path.join(REPO, "evals", "tasks", "176_intermittent_500s.json")
with open(out, "w") as f:
    json.dump(task, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("wrote %s (setup %d B, validator %d B, %d visible tests, %d hidden tests, %d shipped files)"
      % (out, len(setup.encode()), len(validator.encode()), n_visible, len(order), len(shipped_files())))
