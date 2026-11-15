#!/usr/bin/env python3
"""Seal proof for 192_pike_regex: the identifier lint is defense in depth, so
show the rest of the validator holds without it. Runs the task's validator
with the lint switched off against make_cheats.py's re-bypass cheats and
asserts each fails for the intended reason; also runs ref.py the same way
(must pass) and probe_files.py (proves rx.py cannot open files).

usage: prove_seal.py [python]   (default: this interpreter)"""
import json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(HERE, '..', '..', 'tasks', '192_pike_regex.json')
PY = sys.argv[1] if len(sys.argv) > 1 else sys.executable
D = '/tmp/eval_pike192'

script = json.load(open(TASK))['validation']['script']
LINT = 'hit = sorted(names & BANNED)'
assert script.count(LINT) == 1
nolint = script.replace(LINT, 'hit = []  # seal proof: lint off')

ARMS = {
    'ref.py': None,
    'cheat_bypass_longest.py': r'^pathological case \d+: mismatch$',
    'cheat_bypass_lazy.py': r'^pathological case \d+: mismatch$',
    'cheat_bypass_dollar.py': r'^pathological case \d+: mismatch$',
    'cheat_bypass_word.py': r'^(search|pathological) case \d+: mismatch$',
    'cheat_bypass_pure.py': r'^pathological case \d+: timed out',
    'probe_files.py': r'^search case 0: mismatch$',
}
bad = 0
for arm, want in ARMS.items():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    shutil.copy(os.path.join(HERE, arm), os.path.join(D, 'rx.py'))
    t0 = time.time()
    p = subprocess.run([PY, '-c', nolint], capture_output=True, text=True, timeout=60)
    dt = time.time() - t0
    tail = (p.stdout.strip().splitlines() or [''])[-1]
    ok = p.returncode == 0 if want is None else (p.returncode != 0 and re.search(want, tail))
    bad += not ok
    print('%s %-26s rc=%d %5.2fs  %s' % ('OK ' if ok else 'BAD', arm, p.returncode, dt, tail))
shutil.rmtree(D, ignore_errors=True)
print('seal:', 'PROVEN' if not bad else 'NOT PROVEN (%d bad arm(s))' % bad)
sys.exit(1 if bad else 0)
