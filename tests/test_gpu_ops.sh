#!/bin/bash
# GPU-ops review fixes (2026-09-29) — behavior tests for the tools that put a
# model on the card outside the stack: gpu-lease.sh, measure-vram.sh, the
# profile-*-mtp.sh sweeps, and update.sh's llama.cpp / image paths.
#
# Usage: bash tests/test_gpu_ops.sh
#
# HERMETIC. Everything runs against THROWAWAY copies of the scripts under
# $TMPDIR, with stub `nvidia-smi`, `curl`, `docker`, `git`, `cmake` and a stub
# serve script / llama-server on PATH. No port is bound, the GPU is never
# queried, and `pkill` / `pgrep` / `killall` are RECORDING stubs: if a script
# under test ever reaches for a kill-by-pattern again, the test sees the call
# instead of the rig losing its llama-server. Every process this suite starts
# is recorded by pid and reaped by pid on exit.
#
# Doctrine (as in the other suites): each test BUILDS ITS OWN CASE, the stubs
# RECORD what the script did, and each positive assertion has a NEGATIVE
# CONTROL beside it.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# OB_TEST_SRC: run the suite against another tree's scripts (e.g. an old
# revision from `git archive`) to see the regression cases FAIL there.
SRC="${OB_TEST_SRC:-$REPO_DIR}"
for _v in $(compgen -e | grep '^OPENBEAST_' || true); do unset "$_v"; done

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has()  { grep -qF -- "$2" <<< "$1"; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-gpu-ops-XXXXXX")"
PIDS=()
cleanup() {
  local p
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  rm -rf "$T"
}
trap cleanup EXIT

# Poll for a condition instead of sleeping and hoping — this suite runs under load.
wait_for() { local i; for i in $(seq 1 100); do eval "$1" && return 0; sleep 0.1; done; return 1; }

mkdir -p "$T/bin" "$T/state"
export OB_STUB_STATE="$T/state"
# nvidia-smi: a working driver, a 32 GB card, 1 GB in use.
cat > "$T/bin/nvidia-smi" <<'STUB'
#!/bin/bash
case " $* " in
  *memory.total*) echo 32607 ;;
  *memory.used*)  echo "$(cat "$OB_STUB_STATE/vram_used" 2>/dev/null || echo 1000)" ;;
esac
exit 0
STUB
for k in pkill pgrep killall; do
  printf '#!/bin/bash\necho "%s $*" >> "$OB_STUB_STATE/kills.log"\nexit 1\n' "$k" > "$T/bin/$k"
done
chmod +x "$T/bin/"*
: > "$T/state/kills.log"

# A sandbox repo: the REAL scripts under test, copied.
SB="$T/repo"
mkdir -p "$SB/scripts/lib" "$SB/.run"
install -m 755 "$SRC/scripts/gpu-lease.sh" "$SB/scripts/gpu-lease.sh"
GL="$SB/scripts/gpu-lease.sh"
export OPENBEAST_LEASE_VRAM_FLOOR=99999999
export PATH="$T/bin:$PATH"

echo "=== GPU-ops review fixes ==="

# ===========================================================================
echo ""
echo "gpu-lease.sh run — a --force takeover survives the first run's exit:"
# ===========================================================================
rm -f "$SB/.run/gpu.lease" "$T/state/go"
( "$GL" run "campaign A" -- bash -c 'while [[ ! -f "$1/go" ]]; do sleep 0.1; done' _ "$T/state" \
    > "$T/state/runA.out" 2>&1; echo "rc=$?" >> "$T/state/runA.out" ) &
PIDS+=($!)
wait_for 'grep -q "^label=campaign A$" "$SB/.run/gpu.lease" 2>/dev/null' \
  || fail "run A never took the lease"
# B takes the card by force. `acquire` records its CALLER, so the caller must
# outlive the command: a shell that execs into a long sleep keeps its pid AND
# its start time.
bash -c '"$1" acquire "manual B" --force >/dev/null 2>&1; exec sleep 30' _ "$GL" &
B_PID=$!; PIDS+=("$B_PID")
wait_for 'grep -q "^label=manual B$" "$SB/.run/gpu.lease" 2>/dev/null' \
  || fail "B never took the lease"
touch "$T/state/go"
wait_for 'grep -q "^rc=" "$T/state/runA.out"' || fail "run A never exited"
_st="$("$GL" status 2>/dev/null || true)"
if [[ "$_st" == "HELD by pid $B_PID"* ]] && has "$(cat "$T/state/runA.out")" "taken over"; then
  pass "run A's exit leaves B's lease in place (and says why)"
else
  fail "B's lease was deleted by run A's exit: status='$_st' out=$(tr '\n' ' ' < "$T/state/runA.out")"
fi
kill "$B_PID" 2>/dev/null || true
# NEGATIVE CONTROL: with no takeover, run still releases its own lease.
rm -f "$SB/.run/gpu.lease"
"$GL" run "plain" -- true >/dev/null 2>&1
_st="$("$GL" status 2>/dev/null || true)"
if [[ "$_st" == FREE* && ! -f "$SB/.run/gpu.lease" ]]; then
  pass "negative control: a run that was not taken over still releases its lease"
else
  fail "a plain run leaked its lease: $_st"
fi

# ===========================================================================
echo ""
echo "Summary: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
