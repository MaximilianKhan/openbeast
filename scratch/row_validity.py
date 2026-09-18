#!/usr/bin/env python3
"""Live row validity stamp — the check e32_capability{2,3}.sh CLAIMED to do.

Those scripts' headers promised "any CUDA error in the serve log or >10%
zero-token failures marks the row INVALID". The CUDA half was written as

    crashes=$(grep -c "CUDA error" "$log" 2>/dev/null || echo 0)

which appends a second "0" whenever grep matches nothing (grep -c prints 0 AND
exits 1), so $crashes became "0\\n0" and the comparison died with
"[: 0\\n0: integer expected" — the guard never fired in either direction. The
zero-token half was never implemented at all. Both rows of the 2026-09-14 IQ3
pair therefore finished with no validity verdict, and the question "did this
10-hour arm survive?" was answered a day later, by hand, from a log.

Classifier kept deliberately identical to the canonical one in
openbeast-research .../32-t117-gsq-head-to-head/e32_cap_verdict.py::audit —
if you change one, change both. A zero-token FAIL is only a crash when the
agent was KILLED; cache hits and timeouts also bank zero tokens and are not
crashes (conflating them voided a good row once already).

usage: row_validity.py <results.json> [--serve-log <path>]
exit 0 = valid, 1 = INVALID, 2 = could not tell
"""
import json, os, sys

def kind(x):
    rc = x.get('agent_exit_code')
    if rc is None and (x.get('elapsed_seconds') or 0) == 0: return 'cached'
    if rc == -1: return 'timeout'
    if rc is not None and rc < 0: return 'killed'
    return 'other'

def main():
    if len(sys.argv) < 2:
        print("VALIDITY: no results file given"); return 2
    path = sys.argv[1]
    serve = None
    if '--serve-log' in sys.argv:
        i = sys.argv.index('--serve-log')
        serve = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    if not os.path.exists(path):
        print(f"VALIDITY: results file missing ({path})"); return 2
    d = json.load(open(path)); tasks = d.get('tasks') or []
    if not tasks:
        print(f"VALIDITY: no tasks in {os.path.basename(path)}"); return 2
    fails = [x for x in tasks if not x.get('passed')]
    ztok = [x for x in fails if (x.get('tokens_completion') or 0) == 0]
    killed = [x['id'] for x in ztok if kind(x) == 'killed']
    benign = [f"{sum(1 for y in ztok if kind(y) == k)} {k}"
              for k in ('cached', 'timeout', 'other')
              if any(kind(y) == k for y in ztok)]
    frac = (len(killed) / len(fails)) if fails else 0.0

    cuda = None
    if serve and os.path.exists(serve):
        with open(serve, errors='ignore') as fh:
            cuda = sum(1 for l in fh if 'CUDA error' in l or 'GGML_ASSERT' in l)

    reasons = []
    if fails and frac > 0.10:
        reasons.append(f"killed zero-token fails {len(killed)}/{len(fails)} = {frac:.0%} > 10%")
    if cuda:
        reasons.append(f"{cuda} CUDA error/GGML_ASSERT lines in serve log")

    print(f"VALIDITY {os.path.basename(path)}: n={len(tasks)} passed="
          f"{sum(1 for x in tasks if x.get('passed'))} fails={len(fails)} "
          f"killed={len(killed)}"
          + (f" (benign zero-token: {', '.join(benign)})" if benign else "")
          + f" cuda={cuda if cuda is not None else 'n/a'}")
    if reasons:
        print("VALIDITY: ROW INVALID — " + "; ".join(reasons))
        return 1
    print("VALIDITY: row clean"
          + ("" if cuda is not None else "  (serve log absent: CUDA axis unchecked)"))
    return 0

sys.exit(main())
