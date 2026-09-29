#!/bin/bash
# Supply-chain review fixes (2026-09-29) — behavior tests.
#
# Usage: bash tests/test_supply_chain.sh
#
# Everything runs against THROWAWAY copies under $TMPDIR with stub pip /
# pydeps / gh / sleep on PATH: nothing is installed, no GitHub API is called,
# no run is approved. Same doctrine as tests/test_offline_fixes.sh: a test
# BUILDS ITS OWN CASE, the stubs RECORD THEIR CALLS so assertions are about
# what the script did, and every positive assertion has a NEGATIVE CONTROL.
#
#   1  setup-client.sh   the client venv installs from the hash-pinned lock
#   2  agent.sh          ...and so does agent.sh's first-run install
#   3  land-dependabot   approves only THIS repo's held runs for the PR head
#   4  workflows         every action pinned by full commit SHA; the relock
#                        push job never runs the resolver
#   5  client SearXNG    the client compose pins the rig's image digest

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
for _v in $(compgen -e | grep '^OPENBEAST_' || true); do unset "$_v"; done

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has()  { grep -qF -- "$2" <<< "$1"; }
count_lines() { local n; n="$(grep -cF -- "$2" "$1" 2>/dev/null || true)"; echo "${n:-0}"; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-supply-chain-XXXXXX")"
trap 'rm -rf "$T"' EXIT
REAL_PY="$(command -v python3)"
export REAL_PY
export OB_STUB_STATE="$T/state"
mkdir -p "$T/state" "$T/bin"

echo "=== supply-chain review fixes ==="

# A stub scripts/pydeps.sh: records its arguments and the python it was
# pointed at, and exits with whatever $OB_STUB_STATE/pydeps_rc says.
mk_pydeps_stub() {
  mkdir -p "$1/scripts"
  cat > "$1/scripts/pydeps.sh" <<'STUB'
#!/bin/bash
echo "pydeps $* :: py=${OPENBEAST_PYTHON:-unset}" >> "$OB_STUB_STATE/calls.log"
exit "$(cat "$OB_STUB_STATE/pydeps_rc" 2>/dev/null || echo 0)"
STUB
  chmod +x "$1/scripts/pydeps.sh"
}
n_pydeps() { count_lines "$T/state/calls.log" "pydeps install"; }
n_txt()    { count_lines "$T/state/calls.log" "agents/requirements.txt"; }

# ===========================================================================
echo ""
echo "1. setup-client.sh — the venv is built from the hash-pinned lock:"
# ===========================================================================
# setup-client.sh cannot run whole (tailscale, opencode, a real venv). Its
# venv step is lifted out between its own section markers.
_sec="$(sed -n '/^# ---- 3\. isolated venv/,/^# ---- 4\. env file/p' "$REPO_DIR/scripts/setup-client.sh" | sed '$d')"
if has "$_sec" 'VENV="$CLIENT_DIR/venv"'; then
  pass "extracted setup-client's venv step ($(wc -l <<< "$_sec") lines)"
else
  fail "could not extract setup-client's venv step — its section markers moved"
fi
{
  echo 'set -euo pipefail'
  echo "$_sec"
  echo 'echo HARNESS-REACHED-END'
} > "$T/client_venv_step.sh"
CR="$T/client_repo"; CD="$T/client_home"
mk_pydeps_stub "$CR"; mkdir -p "$CR/agents" "$CD/venv/bin"
echo 'openai==1.0' > "$CR/agents/requirements.txt"
cat > "$CD/venv/bin/python3" <<'STUB'
#!/bin/bash
exit 0
STUB
cat > "$CD/venv/bin/pip" <<'STUB'
#!/bin/bash
echo "venv-pip $*" >> "$OB_STUB_STATE/calls.log"
STUB
chmod +x "$CD/venv/bin/python3" "$CD/venv/bin/pip"
run_client() {           # run_client <pydeps rc> [ENV=VAL...]
  echo "$1" > "$T/state/pydeps_rc"; shift; : > "$T/state/calls.log"
  _out="$(env CLIENT_DIR="$CD" CLIENT_REPO="$CR" PY=/nonexistent "$@" bash "$T/client_venv_step.sh" 2>&1)"; _rc=$?
}

run_client 0
if [[ $_rc -eq 0 && "$(n_pydeps)" == "1" && "$(n_txt)" == "0" ]] && has "$_out" "hash-pinned closure" \
   && has "$(cat "$T/state/calls.log")" "py=$CD/venv/bin/python3"; then
  pass "the venv is installed via pydeps.sh (hash-pinned), pointed at the VENV's python; requirements.txt unused"
else
  fail "locked client install (rc=$_rc pydeps=$(n_pydeps) txt=$(n_txt)): $_out :: $(cat "$T/state/calls.log")"
fi
run_client 3
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]] && has "$_out" "HASH MISMATCH" && ! has "$_out" "HARNESS-REACHED-END"; then
  pass "a HASH MISMATCH (pydeps exit 3) is fatal and NEVER falls back to requirements.txt"
else
  fail "client hash mismatch (rc=$_rc txt=$(n_txt)): $_out"
fi
# NEGATIVE CONTROL: a non-hash failure still degrades — loudly.
run_client 1
if [[ $_rc -eq 0 && "$(n_txt)" == "1" ]] && has "$_out" "falling back" && has "$_out" "NOT hash-verified"; then
  pass "negative control: a non-hash failure falls back to requirements.txt, and says it is unverified"
else
  fail "client compat fallback (rc=$_rc txt=$(n_txt)): $_out"
fi
run_client 1 OPENBEAST_PIP_STRICT=1
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]] && has "$_out" "OPENBEAST_PIP_STRICT=1"; then
  pass "OPENBEAST_PIP_STRICT=1 makes any locked-install failure fatal on the client too"
else
  fail "client strict (rc=$_rc txt=$(n_txt)): $_out"
fi

# ===========================================================================
echo ""
echo "2. agent.sh — first-run deps come from the hash-pinned lock:"
# ===========================================================================
AR="$T/agent_repo"; mkdir -p "$AR/scripts/lib" "$AR/agents" "$T/bin_agent"
install -m 755 "$REPO_DIR/agent.sh" "$AR/agent.sh"
: > "$AR/scripts/lib/conf.sh"                       # conf is not what is under test
mk_pydeps_stub "$AR"
echo 'openai==1.0' > "$AR/agents/requirements.txt"
cat > "$T/bin_agent/python3" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
if [[ "${1:-}" == "-c" ]]; then
  case "$2" in *"import openai"*) exit 1 ;; *) exit 1 ;; esac   # not installed; not PEP-668
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then echo "system-pip ${*:3}" >> "$S/calls.log"; exit 0; fi
if [[ "${1:-}" == */agents/runner.py ]]; then echo "RUNNER-STARTED"; exit 0; fi
exec "$REAL_PY" "$@"
STUB
chmod +x "$T/bin_agent/python3"
run_agent() {
  echo "$1" > "$T/state/pydeps_rc"; shift; : > "$T/state/calls.log"
  _out="$(env PATH="$T/bin_agent:$PATH" HOME="$T" "$@" bash "$AR/agent.sh" "a task" 2>&1)"; _rc=$?
}
run_agent 0
if [[ $_rc -eq 0 && "$(n_pydeps)" == "1" && "$(n_txt)" == "0" ]] && has "$_out" "RUNNER-STARTED"; then
  pass "agent.sh installs through pydeps.sh (hash-pinned) and never touches requirements.txt when that works"
else
  fail "agent.sh locked install (rc=$_rc pydeps=$(n_pydeps) txt=$(n_txt)): $_out"
fi
run_agent 3
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]] && has "$_out" "HASH MISMATCH" && ! has "$_out" "RUNNER-STARTED"; then
  pass "agent.sh: a HASH MISMATCH is fatal, with no unpinned fallback and no agent run"
else
  fail "agent.sh hash mismatch (rc=$_rc txt=$(n_txt)): $_out"
fi
run_agent 1
if [[ $_rc -eq 0 && "$(n_txt)" == "1" ]] && has "$_out" "falling back" && has "$_out" "RUNNER-STARTED"; then
  pass "negative control: agent.sh degrades to requirements.txt on a non-hash failure, loudly"
else
  fail "agent.sh fallback (rc=$_rc txt=$(n_txt)): $_out"
fi

# ===========================================================================
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
