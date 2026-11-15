#!/usr/bin/env python3
"""Regenerate evals/tasks/183_heston_little_trap.json from the sources here.

  oracle.py          Lewis-form pricer with the little-trap char. function,
                     adaptive Gauss-Kronrod; embedded in the validator only
  validator_main.py  validator body: seeded case generator, vlib.run_solution run,
                     compare to the oracle at 1e-6 absolute
  heston.py          reference solution (P1/P2 form, composite Gauss-Legendre)
  wrong_*.py / slow.py / leak.py   prover arms, classes in prove.json

The agent-visible fixture dir holds only the 3-option sample.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
OUT = os.path.join(REPO, 'evals', 'tasks', '183_heston_little_trap.json')
DIR = '/tmp/eval_heston183'


def read(n):
    with open(os.path.join(HERE, n)) as f:
        return f.read()


SAMPLE_IN = ("3\n"
             "100 100 1 0 0 0.0175 1.5768 0.0398 0.5751 -0.5711\n"
             "120 126 0.05 0.03 0.01 0.09 2.0 0.06 0.8 -0.7\n"
             "80 72 2 0.04 0.02 0.05 1.2 0.07 0.4 -0.5\n")
SAMPLE_OUT = "5.7851554344\n0.9047208082\n15.9686989483\n"

TASK = f"""\
Create {DIR}/heston.py: a Python 3 program that prices European call options under the Heston stochastic-volatility model. Standard library only: it is run as `python3 -I -S heston.py` from {DIR}, so no third-party package (numpy, scipy, mpmath, ...) is importable. The source is linted: imports of third-party, ctypes, subprocess or multiprocessing modules and dynamic code (exec, eval, __import__, importlib) are rejected.

Model, under the pricing measure:
  dS_t = (r - q) S_t dt + sqrt(v_t) S_t dW1_t
  dv_t = kappa (theta - v_t) dt + sigma sqrt(v_t) dW2_t,   d<W1, W2>_t = rho dt
  S_0 = S0, v_0 = v0;  call price = exp(-r T) E[max(S_T - K, 0)].

Input on stdin: the first token is N (1 <= N <= 200), followed by N lines of ten whitespace-separated decimal numbers: S0 K T r q v0 kappa theta sigma rho.
Output on stdout: N lines, the call price of each input line, in order, as a decimal number. Nothing else.

Every hidden case lies in this domain: 50 <= S0 <= 150; 0.6*S0 <= K <= 1.6*S0; 0.02 <= T <= 30 (years; maturities up to and including 30 occur); -0.01 <= r <= 0.08; 0 <= q <= 0.05; 0.005 <= v0 <= 0.25; 0.1 <= kappa <= 5; 0.01 <= theta <= 0.25; 0.1 <= sigma <= 1.5; -0.95 <= rho <= 0. The Feller condition 2*kappa*theta >= sigma^2 does not necessarily hold.

Accuracy: each printed price must be within 1e-6 (absolute) of the exact model price; the validator compares against an independent high-precision reference. Speed: the hidden run is 140 options in one process (no child processes) and must use at most 8 seconds of CPU time in total.

Sample: {DIR}/sample_input.txt holds 3 options, {DIR}/sample_output.txt their prices to 10 decimals. Validation uses 140 hidden, seeded cases spread over the whole domain and reports only the index of the first failing case, so write your own tests.
"""

validator = 'ORACLE = ' + repr(read('oracle.py')) + '\n' + read('validator_main.py')

setup = (f"rm -rf {DIR} && mkdir -p {DIR} && "
         f"printf '{SAMPLE_IN}' > {DIR}/sample_input.txt && "
         f"printf '{SAMPLE_OUT}' > {DIR}/sample_output.txt").replace("\n", "\\n")

task = {
    'id': '183_heston_little_trap',
    'name': 'Heston European call pricer (long maturities)',
    'difficulty': 'hard',
    'category': 'Mathematical Finance',
    'subcategory': 'Stochastic volatility',
    'max_iter': 25,
    'est_s': 165,
    'max_wall_s': 900,
    'role': 'scored',
    'ref_stem': '183_heston_little_trap/heston',
    'dest': f'{DIR}/heston.py',
    'setup': setup,
    'task': TASK,
    'validation': {'type': 'python', 'script': validator},
    'cleanup': f'rm -rf {DIR}',
}

with open(OUT, 'w') as f:
    json.dump(task, f, indent=1)
    f.write('\n')
print('wrote', OUT, len(validator), 'bytes of validator')
