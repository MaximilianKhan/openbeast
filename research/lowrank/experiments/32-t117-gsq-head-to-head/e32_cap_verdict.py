#!/usr/bin/env python3
"""E32 step (e) capability verdict — paired v5-fast rows, sanity-audited.

usage: e32_cap_verdict.py <rowA.json> <rowB.json> [--serve-logs /tmp/e32v2-serve-{alias}.log]
                          [--agent-logs <dir>|none]
Prints, per row: n/passed, imputed capability (harness scoring), tripwire
failures, the failure audit (killed / harness-death / EAGAIN / API-error
rows — the row-validity classifier), tokens, wall, per-language passes,
serve-log CUDA error count (validity stamp). Then paired McNemar A-vs-B on
common units (all / zig / non-zig) — REFUSED when either row is incomplete
(n != 112) — and the Q5-class reference band (capped-20480 uncensored 3.8
rows, 112 units). Reference frame + card claims quoted so the verdict reads
standalone.

Two copies exist and are kept BYTE-IDENTICAL: openbeast-research (canonical,
what the campaign runs) and openbeast research/lowrank/experiments/
32-t117-gsq-head-to-head/. Neither carries its own row classifier any more:
both import openbeast scratch/row_validity.py, so the "change both" rule that
let the two drift (2026-09-29 review, research-stats-1/-5) has nothing left
to guard.
"""
import glob, json, os, sys
from math import comb
OB = os.environ.get('OPENBEAST_ROOT', '/home/max/Documents/openbeast')
sys.path.insert(0, f'{OB}/evals')
sys.path.insert(0, f'{OB}/scratch')
import scoring  # noqa
try:
    import row_validity  # noqa  (the ONE classifier; see its docstring)
    row_validity.audit
except (SystemExit, AttributeError):
    # The pre-2026-09-29 row_validity.py was a script that ran (and exited)
    # on import and had no audit(); never fall back to a private copy.
    sys.exit(f"e32_cap_verdict: {OB}/scratch/row_validity.py predates the shared "
             "classifier (fix/review-research) — merge it first")
SUITE_N = 112

pin = json.load(open(f'{OB}/evals/suites/v5-fast.json'))
TRIP = set(pin['tripwires'])
langmap = {}
for p in glob.glob(f'{OB}/evals/tasks/*.json'):
    d = json.load(open(p)); vs = d.get('variants')
    if not vs: langmap[d['id']] = 'python'
    else:
        for v in vs: langmap[f"{d['id']}_{v['id']}"] = v.get('language', 'python')

def mcnemar(a, b, ids):
    x = [i for i in ids if a[i] and not b[i]]; y = [i for i in ids if b[i] and not a[i]]
    n = len(x) + len(y); k = min(len(x), len(y))
    p = min(1.0, sum(comb(n, j) for j in range(k + 1)) / 2 ** n * 2) if n else 1.0
    return x, y, p

def audit(f, serve_tpl, log_idx):
    d = json.load(open(f)); t = d['tasks']
    alias = d.get('model'); pm = {x['id']: bool(x.get('passed')) for x in t}
    fails = [x for x in t if not x.get('passed')]
    # Failure audit — row_validity.audit() classifies each failed row from
    # its own fields. Killed (signal death) counts past 10% of fails; a
    # harness/setup death, a fork/thread-EAGAIN validation death or a fail
    # whose agent log shows API/connection errors each count from ONE: none
    # of them is the model, and each is a discordant pair it did not earn.
    # (The old rule filed harness deaths under "cache hit" — exactly the
    # four OpenBLAS setup deaths on the 09-14 UD-IQ3 row.)
    va = row_validity.audit(d, log_idx, SUITE_N)
    trip = [x['id'] for x in fails if x['id'] in TRIP]
    cap = None
    if len(t) == 112:
        imp = scoring.impute_suite_tasks(t, pin); solve, lang, cap = scoring.compute_solve_breadth(imp)
    langs = {}
    for x in t:
        L = langmap.get(x['id'], '?'); langs.setdefault(L, [0, 0]); langs[L][1] += 1; langs[L][0] += int(bool(x.get('passed')))
    cuda = None
    if serve_tpl:
        lp = serve_tpl.format(alias=alias)
        if os.path.exists(lp): cuda = sum(1 for l in open(lp, errors='ignore') if 'CUDA error' in l or 'GGML_ASSERT' in l)
    tok = sum(x.get('tokens_completion') or 0 for x in t); wall = sum(x.get('elapsed_seconds') or 0 for x in t)
    print(f"ROW {alias}  file={os.path.basename(f)}")
    print(f"  n={len(t)} passed={sum(pm.values())}  imputed_capability={cap if cap is None else round(cap,2)}")
    print(f"  tripwire failures ({len(trip)}): {', '.join(trip) or '-'}")
    if va['benign']:
        print("  zero-token fails that are NOT crashes: "
              + ", ".join(f"{n} {k}" for k, n in sorted(va['benign'].items())))
    print(f"  killed zero-token fails: {len(va['killed'])}/{len(fails)}  harness deaths: {len(va['infra'])}  "
          f"EAGAIN: {len(va['eagain'])}  API-error fails: {len(va['api']) if log_idx is not None else 'n/a'}")
    for r in va['reasons']:
        print(f"    ← ROW INVALID: {r}")
    if not va['complete']:
        print(f"    ← ROW INCOMPLETE: n={len(t)}, the pin has {SUITE_N}")
    print(f"  serve-log CUDA errors: {cuda if cuda is not None else 'n/a'} {'← ROW INVALID' if cuda else ''}")
    print(f"  completion tokens={tok:,}  summed wall={wall/3600:.2f}h")
    print("  per-language: " + ", ".join(f"{L} {a}/{b}" for L, (a, b) in sorted(langs.items())))
    valid = (cuda in (None, 0)) and va['valid']
    return alias, pm, cap, valid, va['complete']

def main():
    # Drop flags AND their values. The old filter removed "--serve-logs" but
    # left its argument in the positional list, so the verdict printed both
    # rows and then died opening '/tmp/e32v2-serve-{alias}.log' as a results
    # file -- the output was correct, the exit code was 1, and the pipeline
    # step recorded a failure.
    argv = sys.argv[1:]
    serve_tpl = None
    logs = f'{OB}/agents/logs'
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == '--serve-logs':
            serve_tpl = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif argv[i] == '--agent-logs':
            logs = argv[i + 1] if i + 1 < len(argv) else 'none'
            i += 2
        elif argv[i].startswith('--'):
            i += 1
        else:
            args.append(argv[i]); i += 1
    log_idx = None if logs == 'none' else row_validity.load_log_index(logs)
    rows = [audit(f, serve_tpl, log_idx) for f in args]
    # reference band: capped-20480 uncensored 3.8 rows, 112 units, same pin
    refs = []
    for f in sorted(glob.glob(f'{OB}/evals/results/eval-qwen3-8-27b-uncensored-q5-k-m-*.json')):
        d = json.load(open(f))
        if len(d.get('tasks', [])) == 112 and str(d.get('server', {}).get('reasoning_budget')) == '20480':
            refs.append((os.path.basename(f), sum(1 for x in d['tasks'] if x.get('passed')), (d.get('fast_suite') or {}).get('capability_imputed')))
    print("\nREFERENCE BAND (Q5-class 3.8, capped 20480, 112 units):")
    for r in refs: print(f"  {r[0]}: passed {r[1]}, imputed {r[2]}")
    if refs: print(f"  band: passed {min(r[1] for r in refs)}-{max(r[1] for r in refs)}, imputed {min(r[2] for r in refs)}-{max(r[2] for r in refs)}")
    if len(rows) >= 2:
        (na, a, ca, va, fa), (nb, b, cb, vb, fb) = rows[0], rows[1]
        common = sorted(set(a) & set(b))
        print(f"\nPAIRED {na} (A) vs {nb} (B), {len(common)} common units  validity A={va} B={vb}"
              f"  complete A={fa} B={fb}")
        if not (fa and fb):
            # A truncated row pairs on whatever units survived — a p-value on
            # that subset reads like a verdict and is not one.
            print(f"  ✗ NO PAIRED VERDICT: a row is incomplete (needs {SUITE_N} units each) — rerun it")
            return
        for name, ids in (('ALL', common), ('zig', [u for u in common if langmap.get(u) == 'zig']), ('non-zig', [u for u in common if langmap.get(u) != 'zig'])):
            x, y, p = mcnemar(a, b, ids)
            print(f"  {name:8s} A-only {len(x):2d}  B-only {len(y):2d}  net(A-B) {len(x)-len(y):+d}  p={p:.3f}")
            if x: print(f"           A-only: {', '.join(x)}")
            if y: print(f"           B-only: {', '.join(y)}")
        if not (va and vb): print("  ⚠ at least one row INVALID — do not read the paired result as a capability verdict")
    print("\nCARD CLAIMS (ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF README, read 2026-09-11):")
    print("  IQ3_S 'task-lossless': AIME25 100.00 (=BF16), LCB v6 85.71 (=BF16), GPQA-D 89.39 (-0.51); vs UD-IQ3_S +3.33 AIME, +1.71 LCB, -0.51 GPQA")
    print("  IQ2_XS vs UD-IQ2_S at 8.4GB: +10.00 AIME25, +8.59 GPQA-D, +4.57 LCB v6")
    print("  Our instrument: v5-fast agentic-coding suite (112 pinned units, imputed to the 291-unit v4 scale), capped 20480, single slot, stock b10865.")
main()
