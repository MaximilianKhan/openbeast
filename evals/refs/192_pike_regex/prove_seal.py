#!/usr/bin/env python3
"""Seal proof for 192_pike_regex. The identifier lint is defense in depth;
this shows each layer behind it holds on its own, through the harness's own
run_validation (vlib injected, script on stdin, scale exported).

  lint off            the re-restore cheats (make_cheats.py) fail to LOAD:
                      the runner took away the primitives a fresh _sre is
                      built from. probe_seal.py (ref plus every restore route
                      it can try, sabotaging its answers if one works) and
                      probe_files.py (ref that answers only if it can open a
                      file) must fail as declared; ref must pass.
  lint and seal off   the same cheats must still fail, on the WRAPPED sets:
                      re is exponential there, so the fallback engine answers
                      (and it is wrong) or re's finditer runs out of CPU.

usage: prove_seal.py [arm.py ...]   (default: every arm below)"""
import copy, json, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'evals'))
sys.path.insert(0, os.path.join(REPO, 'agents'))
import run_eval as R  # noqa: E402

TASK = json.load(open(os.path.join(REPO, 'evals', 'tasks', '192_pike_regex.json')))
LINT = 'hit = sorted(names & BANNED)'
SEAL = '_SEAL = True'
SCRIPT = TASK['validation']['script']
assert SCRIPT.count(LINT) == 1 and SCRIPT.count(SEAL) == 1


def variant(lint=True, seal=True):
    t = copy.deepcopy(TASK)
    s = SCRIPT
    if not lint:
        s = s.replace(LINT, 'hit = []  # seal proof: lint off')
    if not seal:
        s = s.replace(SEAL, '_SEAL = False  # seal proof: seal off')
    t['validation']['script'] = s
    return t


LOAD = r'^rx\.py failed to load: RuntimeError$'
WRAPPED_BAD = r'^wrapped (search|finditer) case \d+: (mismatch|timed out)'
# arm -> {mode: expected last line (None = must pass)}
ARMS = {
    'ref.py': {'nolint': None, 'open': None},
    'probe_seal.py': {'nolint': None},
    'probe_files.py': {'nolint': r'^search case 0: mismatch$'},
}
for _n in sorted(os.listdir(HERE)):
    if _n.startswith('wrong_bypass_') and _n.endswith('.py'):
        ARMS[_n] = {'nolint': LOAD, 'open': WRAPPED_BAD}
ARMS['wrong_bypass_fmt_iter_re.py']['open'] = r'^wrapped finditer case \d+: timed out'
MODES = {'nolint': variant(lint=False), 'open': variant(lint=False, seal=False)}


def run(task, sol):
    R.run_cleanup(task, log=lambda *_: None)
    R.run_setup(task, log=lambda *_: None)
    shutil.copy(os.path.join(HERE, sol), task['dest'])
    t0 = time.time()
    ok, out = R.run_validation(task, timeout_scale=1.0)
    dt = time.time() - t0
    R.run_cleanup(task, log=lambda *_: None)
    return ok, dt, ((out or '').strip().splitlines() or [''])[-1]


def main(arms):
    bad = 0
    for arm in arms:
        for mode, want in ARMS[arm].items():
            ok, dt, tail = run(MODES[mode], arm)
            good = ok if want is None else (not ok and re.search(want, tail) is not None)
            bad += not good
            print('%s %-36s %-6s passed=%-5s %5.2fs  %s'
                  % ('OK ' if good else 'BAD', arm, mode, ok, dt, tail[:100]), flush=True)
    print('seal:', 'PROVEN' if not bad else 'NOT PROVEN (%d bad arm(s))' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:] or list(ARMS)))
