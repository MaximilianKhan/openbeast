#!/usr/bin/env bash
# beast-lang L1 — ask the INSTALLED toolchain what it supports.
#
#   ./scripts/lang-introspect.sh probe [lang...]    what the toolchain reports
#   ./scripts/lang-introspect.sh render [lang...]   the pack lines it becomes
#   ./scripts/lang-introspect.sh write [lang...]    persist for review
#   ./scripts/lang-introspect.sh check [lang...]    does a written file still match
#   ./scripts/lang-introspect.sh list               what can be probed here
#
# The GENERATED tier of docs/BEAST_LANG_PLAN.md §3: facts nobody authored,
# extracted mechanically, costing no fixture, no review and no GPU because the
# toolchain is the author. This is what makes beast-lang work for a language
# for which nobody wrote claims.
#
# NOTHING HERE IS ON THE SERVING PATH. A full probe of all six languages costs
# ~0.2s, so the pack path asks the toolchain live every time; these files are
# for a human to read and diff. That is why agents/lang/generated/ is
# gitignored: it is per-rig state, like the L0 corpus, not source.
#
# `check` answers the one question a written file can still get wrong: has it
# been EDITED? A generated file that no longer matches its own content hash is
# not generated any more, and saying so is the entire value of writing it down.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

CMD="${1:-list}"
shift || true

exec python3 - "$CMD" "$@" <<'PY'
import json
import sys

sys.path.insert(0, "agents")
from lang import drivers as D        # noqa: E402
from lang import introspect as I     # noqa: E402

cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
langs = sys.argv[2:] or sorted(I.PROBES)
bad = [x for x in langs if x not in I.PROBES]
if bad:
    print(f"no probe for: {', '.join(bad)} "
          f"(have: {', '.join(sorted(I.PROBES))})", file=sys.stderr)
    raise SystemExit(2)

rc = 0

if cmd == "list":
    print(f"{'lang':8} {'toolchain':42} probes")
    print("-" * 78)
    for lang in sorted(I.PROBES):
        rec = I.facts(lang)
        if rec is None:
            print(f"{lang:8} {'— not installed here':42} "
                  f"(claims stay CURATED, never auto-injected)")
            continue
        keys = ", ".join(sorted(rec["facts"]))
        print(f"{lang:8} {(rec['toolchain'] or '?')[:42]:42} {keys}")
    for lang in D.NO_TOOLCHAIN:
        print(f"{lang:8} {'— no toolchain on this rig':42} "
              f"(plan §4: pull-only, never auto-injected)")

elif cmd == "probe":
    for lang in langs:
        rec = I.facts(lang)
        if rec is None:
            print(f"{lang}: UNAVAILABLE on this machine", file=sys.stderr)
            rc = 1
            continue
        print(json.dumps(rec, indent=2, sort_keys=True))

elif cmd == "render":
    for lang in langs:
        lines = I.render(lang)
        chars = sum(len(x) for x in lines)
        print(f"=== {lang} — {len(lines)} line(s), {chars} chars "
              f"(budget {I.BUDGET_CHARS}) ===")
        for ln in lines:
            print(f"  - {ln}")
        if not lines:
            print("  (nothing this layer can say about it here)")
        print()

elif cmd == "write":
    for lang in langs:
        if I.facts(lang) is None:
            print(f"{lang}: UNAVAILABLE — not written", file=sys.stderr)
            rc = 1
            continue
        print(f"wrote {I.write(lang)}")

elif cmd == "check":
    worst = 0
    for lang in langs:
        state, detail = I.check(lang)
        print(f"{lang:8} {state:12} {detail}")
        # MISSING and UNAVAILABLE are not failures: nothing promised a file
        # exists, and a language this rig cannot build is a fact, not a fault.
        # STALE is a nudge. DRIFTED is the real one — a generated file that
        # was edited is no longer generated, and it is the only state that
        # should ever fail a pipeline.
        if state == "DRIFTED":
            worst = max(worst, 2)
        elif state == "STALE":
            worst = max(worst, 1)
    rc = 0 if worst < 2 else 1

else:
    print(__doc__ or "", file=sys.stderr)
    print(f"unknown command {cmd!r} — "
          f"probe | render | write | check | list", file=sys.stderr)
    rc = 2

raise SystemExit(rc)
PY
