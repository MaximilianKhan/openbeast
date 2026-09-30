#!/usr/bin/env bash
# Hermetic tests for scratch/tier3_zig_ab.sh — the Tier-3 campaign harness.
#
# The harness is copied into a throwaway repo whose evals/benchmark_all.py,
# run_eval.py, cache.py, pack generator and verdict are stubs, so nothing here
# starts llama-server, reads the GPU or touches a live port. The stub benchmark
# records every call and can, on its Nth call, touch the stop file, bump the
# era, rebuild the "engine", sleep, or write a results file for another model.
#
# Covers the 2026-09-30 double-pass findings on the harness:
#   A-campaign-2  a DRY_RUN never writes the real manifest
#   B-campaign-1  a stale stop file is cleared at start, and consumed at the end
#   B-campaign-3  one run per manifest (flock); results must name this cell's
#                 model and units
#   A-campaign-4  era/engine re-checked before every cell; the header records
#                 engine, weights and SKIP_C0; old headers still resume
#
# TIER3_SCRIPT=<path> runs the same checks against another copy of the script
# (used to confirm each check fails on the pre-fix harness).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${TIER3_SCRIPT:-$REPO_DIR/scratch/tier3_zig_ab.sh}"
PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

UNITS=""
for i in $(seq -w 1 30); do UNITS="$UNITS${UNITS:+,}u${i}_zig"; done

# make_repo <dir>: a stub repo the harness can run in.
make_repo() {
  local t="$1"
  mkdir -p "$t/scratch" "$t/agents/packs" "$t/evals/suites" "$t/evals/results" "$t/scripts" "$t/.run"
  cp "$SCRIPT" "$t/scratch/tier3_zig_ab.sh"
  echo "pack body" > "$t/agents/packs/zig-0.16.md"
  printf 'import sys\nsys.exit(0)\n' > "$t/agents/packs/gen_zig_pack.py"
  printf 'print("VERDICT: stub")\n' > "$t/scratch/tier3_verdict.py"
  echo "era-1" > "$t/era.txt"
  echo "100" > "$t/engine.txt"
  python3 - "$t" "$UNITS" <<'EOF'
import json, sys
t, units = sys.argv[1], sys.argv[2].split(",")
json.dump({"units": units}, open(f"{t}/evals/suites/v5-fast.json", "w"))
open(f"{t}/evals/cache.py", "w").write(
    "import os\n"
    "def context_hash():\n"
    "    return open(os.path.join(os.path.dirname(__file__), '..', 'era.txt')).read().strip()\n")
open(f"{t}/evals/run_eval.py", "w").write(
    "import os\n"
    "def load_tasks(ids):\n"
    "    return [{'id': i, 'language': 'zig'} for i in ids]\n"
    "def capture_inference_engine_info():\n"
    "    b = open(os.path.join(os.path.dirname(__file__), '..', 'engine.txt')).read().strip()\n"
    "    return {'build': b, 'commit': 'c0ffee'}\n")
open(f"{t}/scripts/serve-main.sh", "w").write('-m "$WEIGHTS_DIR/Main-Q5.gguf"\n')
open(f"{t}/scripts/serve-champ.sh", "w").write('-m "$WEIGHTS_DIR/Champ-Q5.gguf"\n')
open(f"{t}/scripts/weights.registry", "w").write(
    "aaaaaaaa11111111\t1\tMain-Q5.gguf\trepo/main\t-\n"
    "bbbbbbbb22222222\t1\tChamp-Q5.gguf\trepo/champ\t-\n")
# The stub benchmark. MODELS text matches the harness's regexes.
open(f"{t}/evals/benchmark_all.py", "w").write('''
MODELS = [
    {"slug": "main-q5",
     "name": "Main Q5",
     "serve": "scripts/serve-main.sh"},
    {"slug": "champ-q5",
     "name": "Champ Q5",
     "serve": "scripts/serve-champ.sh"},
]
import json, os, sys, time
if __name__ == "__main__":
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    a = sys.argv[1:]
    slug = a[a.index("--models") + 1]
    tasks = a[a.index("--tasks") + 1].split(",")
    calls = os.path.join(root, "calls")
    with open(calls, "a") as f:
        f.write(slug + "\\n")
    n = len(open(calls).read().split())
    env = os.environ
    time.sleep(float(env.get("FAKE_SLEEP", "0")))
    if env.get("FAKE_TOUCH_STOP_AT") == str(n):
        open(os.path.join(root, ".run", "tier3.stop"), "w").close()
    if env.get("FAKE_BUMP_ERA_AT") == str(n):
        open(os.path.join(root, "era.txt"), "w").write("era-2\\n")
    if env.get("FAKE_BUMP_ENGINE_AT") == str(n):
        open(os.path.join(root, "engine.txt"), "w").write("101\\n")
    name = {"main-q5": "Main Q5", "champ-q5": "Champ Q5"}[slug]
    name = env.get("FAKE_MODEL", name)
    packs = {"zig": env["FAKE_PACK8"]} if "--packs" in a else {}
    r = {"model": name,
         "harness": {"packs": packs, "greedy": True, "diagnostics": False},
         "tasks": [{"id": u, "passed": True} for u in tasks],
         "summary": {"passed": len(tasks), "total": len(tasks)}}
    out = os.path.join(root, "evals", "results", f"eval-{slug}-{os.getpid()}-{n}.json")
    json.dump(r, open(out, "w"))
''')
EOF
}

# run_h <repo> [VAR=val ...]: run the harness there; prints its output.
run_h() {
  local t="$1"; shift
  (cd "$t" && env MODEL=main-q5 CHAMPION=champ-q5 STOP_FILE="$t/.run/tier3.stop" \
     FAKE_PACK8="$(sha256sum "$t/agents/packs/zig-0.16.md" | cut -c1-8)" \
     TMPDIR="$t" "$@" bash scratch/tier3_zig_ab.sh) 2>&1
}
ncalls() { [ -f "$1/calls" ] && wc -l < "$1/calls" | tr -d ' ' || echo 0; }

echo "Tier-3 harness ($SCRIPT)"

# --- A-campaign-2: DRY_RUN never writes the real manifest --------------------
T="$ROOT/dry"; make_repo "$T"; M="$T/m.txt"
run_h "$T" MANIFEST="$M" FRESH=1 FAKE_TOUCH_STOP_AT=1 >/dev/null
before="$(sha256sum < "$M")"
out="$(run_h "$T" MANIFEST="$M" FRESH=1 DRY_RUN=1)"; rc=$?
if [ $rc -eq 0 ] && [ "$(sha256sum < "$M")" = "$before" ] && ! grep -q DRYRUN "$M"; then
  pass "a dry run leaves the real manifest byte-identical"
else
  fail "a dry run modified the real manifest (rc=$rc): $(grep -c DRYRUN "$M") DRYRUN rows"
fi
c0="$(ncalls "$T")"
run_h "$T" MANIFEST="$M" FRESH=1 >/dev/null
if [ "$(( $(ncalls "$T") - c0 ))" -eq 5 ]; then
  pass "the real resume after a dry run still runs the 5 remaining cells"
else
  fail "the real resume after a dry run ran $(( $(ncalls "$T") - c0 )) cells, want 5"
fi
# a legacy manifest that already holds DRYRUN rows: those cells still run
T="$ROOT/legacy-dry"; make_repo "$T"; M="$T/m.txt"
run_h "$T" MANIFEST="$M" FRESH=1 FAKE_TOUCH_STOP_AT=1 >/dev/null
printf 'P1a DRYRUN-P1a\nP0b DRYRUN-P0b\n' >> "$M"
c0="$(ncalls "$T")"
run_h "$T" MANIFEST="$M" FRESH=1 >/dev/null
if [ "$(( $(ncalls "$T") - c0 ))" -eq 5 ]; then
  pass "legacy DRYRUN-* rows are not counted as cells"
else
  fail "legacy DRYRUN-* rows skipped cells: ran $(( $(ncalls "$T") - c0 )), want 5"
fi

# --- B-campaign-1: stale stop files ------------------------------------------
T="$ROOT/stale"; make_repo "$T"; touch "$T/.run/tier3.stop"
out="$(run_h "$T" MANIFEST="$T/m.txt" FRESH=1)"; rc=$?
if [ $rc -eq 0 ] && [ "$(ncalls "$T")" -eq 6 ] && [ ! -e "$T/.run/tier3.stop" ]; then
  pass "a stop file present at start is removed, not obeyed (6 cells ran)"
else
  fail "a stale stop file stopped a new run (rc=$rc, cells=$(ncalls "$T"))"
fi
T="$ROOT/laststop"; make_repo "$T"
run_h "$T" MANIFEST="$T/m.txt" FRESH=1 FAKE_TOUCH_STOP_AT=6 >/dev/null
if [ ! -e "$T/.run/tier3.stop" ]; then
  pass "a stop touched during the last cell is consumed when the run ends"
else
  fail "a stop touched during the last cell survives into the next run"
fi
T="$ROOT/midstop"; make_repo "$T"
out="$(run_h "$T" MANIFEST="$T/m.txt" FRESH=1 FAKE_TOUCH_STOP_AT=2)"; rc=$?
if [ $rc -eq 0 ] && [ "$(ncalls "$T")" -eq 2 ] && grep -q "STOPPED at the cell boundary before P0b" <<<"$out"; then
  pass "a stop touched mid-run still stops at the next cell boundary"
else
  fail "a mid-run stop did not stop at the boundary (rc=$rc, cells=$(ncalls "$T"))"
fi

# --- B-campaign-3: one run per manifest; results must be this cell's ---------
T="$ROOT/lock"; make_repo "$T"; M="$T/m.txt"
run_h "$T" MANIFEST="$M" FRESH=1 FAKE_SLEEP=1 > "$T/a.out" & pa=$!
sleep 0.5
run_h "$T" MANIFEST="$M" FRESH=1 FAKE_SLEEP=1 > "$T/b.out"; rb=$?
wait $pa; ra=$?
if [ $ra -eq 0 ] && [ $rb -eq 2 ] && grep -q "one run per manifest" "$T/b.out" && [ "$(ncalls "$T")" -eq 6 ]; then
  pass "a second run on the same manifest is refused; each cell ran once"
else
  fail "two runs shared a manifest (rc a=$ra b=$rb, benchmark calls=$(ncalls "$T"), want 6)"
fi
T="$ROOT/wrongmodel"; make_repo "$T"
run_h "$T" MANIFEST="$T/m.txt" FRESH=1 FAKE_MODEL="Some Other Model" >/dev/null; rc=$?
if [ $rc -ne 0 ] && ! grep -q '^P0a ' "$T/m.txt"; then
  pass "a results file for another model is not recorded as the cell"
else
  fail "a results file for another model was recorded (rc=$rc)"
fi

# --- A-campaign-4: drift between cells, header provenance, old headers -------
T="$ROOT/era"; make_repo "$T"
out="$(run_h "$T" MANIFEST="$T/m.txt" FRESH=1 FAKE_BUMP_ERA_AT=1)"; rc=$?
if [ $rc -eq 2 ] && [ "$(ncalls "$T")" -eq 1 ] && grep -q "drifted" <<<"$out"; then
  pass "an era change between cells refuses the next cell"
else
  fail "an era change between cells was accepted (rc=$rc, cells=$(ncalls "$T"))"
fi
T="$ROOT/engine"; make_repo "$T"
out="$(run_h "$T" MANIFEST="$T/m.txt" FRESH=1 FAKE_BUMP_ENGINE_AT=2)"; rc=$?
if [ $rc -eq 2 ] && [ "$(ncalls "$T")" -eq 2 ]; then
  pass "a llama.cpp rebuild between cells refuses the next cell"
else
  fail "an engine change between cells was accepted (rc=$rc, cells=$(ncalls "$T"))"
fi
T="$ROOT/hdr"; make_repo "$T"; M="$T/m.txt"
run_h "$T" MANIFEST="$M" FRESH=1 FAKE_TOUCH_STOP_AT=1 >/dev/null
if grep -qx '# runtime engine=100/c0ffee weights=Main-Q5.gguf@aaaaaaaa,Champ-Q5.gguf@bbbbbbbb skip_c0=0' "$M"; then
  pass "the header records the engine, both weight pins and SKIP_C0"
else
  fail "the header lacks the runtime line: $(grep '^# runtime' "$M" || echo none)"
fi
out="$(run_h "$T" MANIFEST="$M" FRESH=1 SKIP_C0=1)"; rc=$?
if [ $rc -eq 2 ] && grep -q "RESUME REFUSED" <<<"$out"; then
  pass "resuming with a different SKIP_C0 is refused"
else
  fail "resuming with a different SKIP_C0 was accepted (rc=$rc)"
fi
# An older manifest (three header lines, no runtime line) still resumes.
T="$ROOT/oldhdr"; make_repo "$T"; M="$T/m.txt"
run_h "$T" MANIFEST="$M" FRESH=1 FAKE_TOUCH_STOP_AT=1 >/dev/null
sed -i '/^# runtime /d' "$M"
c0="$(ncalls "$T")"
out="$(run_h "$T" MANIFEST="$M" FRESH=1)"; rc=$?
if [ $rc -eq 0 ] && [ "$(( $(ncalls "$T") - c0 ))" -eq 5 ]; then
  pass "a manifest without the runtime line still resumes"
else
  fail "an older manifest no longer resumes (rc=$rc)"
fi
if grep -q "predates the '# runtime' header" <<<"$out"; then
  pass "resuming an older manifest warns that engine/weights/SKIP_C0 are unchecked"
else
  fail "resuming an older manifest gave no warning"
fi

echo
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
