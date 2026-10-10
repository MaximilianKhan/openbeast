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
#   6  extensions        every extensions/*/compose.yaml image is digest-pinned

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
# A NON-hash failure is fatal by default too (2026-10-09 review, supply S2): a
# mirror that WITHHOLDS a locked file makes pip exit 1, not 3, and falling
# back would install the same names unverified from that same mirror.
run_client 1
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]] && has "$_out" "Refusing to fall back" \
   && has "$_out" "OPENBEAST_PIP_STRICT=0" && ! has "$_out" "HARNESS-REACHED-END"; then
  pass "a non-hash failure does NOT fall back by default, and names the explicit opt-out"
else
  fail "client default-strict (rc=$_rc txt=$(n_txt)): $_out"
fi
run_client 1 OPENBEAST_PIP_STRICT=1
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]]; then
  pass "OPENBEAST_PIP_STRICT=1 (the old spelling of strict) is still fatal"
else
  fail "client strict (rc=$_rc txt=$(n_txt)): $_out"
fi
# NEGATIVE CONTROL: the explicit opt-out still degrades — loudly.
run_client 1 OPENBEAST_PIP_STRICT=0
if [[ $_rc -eq 0 && "$(n_txt)" == "1" ]] && has "$_out" "falling back" && has "$_out" "NOT hash-verified"; then
  pass "negative control: OPENBEAST_PIP_STRICT=0 falls back to requirements.txt, and says it is unverified"
else
  fail "client compat fallback (rc=$_rc txt=$(n_txt)): $_out"
fi
# The opt-out never reaches a hash mismatch.
run_client 3 OPENBEAST_PIP_STRICT=0
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]] && has "$_out" "HASH MISMATCH"; then
  pass "OPENBEAST_PIP_STRICT=0 does not turn a HASH MISMATCH into a fallback"
else
  fail "client hash mismatch under opt-out (rc=$_rc txt=$(n_txt)): $_out"
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
# A NON-hash failure (an index that omits one locked file says "No matching
# distribution") used to fall back to the unpinned install by default.
run_agent 1
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]] && has "$_out" "OPENBEAST_PIP_STRICT=0" && ! has "$_out" "RUNNER-STARTED"; then
  pass "agent.sh: a non-hash failure of the locked install STOPS by default (no unpinned install, no agent run)"
else
  fail "agent.sh fell back without being asked (rc=$_rc txt=$(n_txt)): $_out"
fi
run_agent 1 OPENBEAST_PIP_STRICT=1
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]]; then
  pass "agent.sh: OPENBEAST_PIP_STRICT=1 is the same as the default"
else
  fail "agent.sh STRICT=1 (rc=$_rc txt=$(n_txt)): $_out"
fi
run_agent 1 OPENBEAST_PIP_STRICT=0
if [[ $_rc -eq 0 && "$(n_txt)" == "1" ]] && has "$_out" "falling back" && has "$_out" "RUNNER-STARTED"; then
  pass "negative control: OPENBEAST_PIP_STRICT=0 opts in to the requirements.txt fallback, loudly"
else
  fail "agent.sh opt-in fallback (rc=$_rc txt=$(n_txt)): $_out"
fi
run_agent 3 OPENBEAST_PIP_STRICT=0
if [[ $_rc -ne 0 && "$(n_txt)" == "0" ]] && has "$_out" "HASH MISMATCH"; then
  pass "agent.sh: the opt-in never covers a HASH MISMATCH"
else
  fail "agent.sh hash mismatch under STRICT=0 (rc=$_rc txt=$(n_txt)): $_out"
fi

# ===========================================================================
echo ""
echo "3. land-dependabot.sh — approves only THIS repo's held runs for the PR head:"
# ===========================================================================
if ! command -v jq >/dev/null 2>&1; then
  echo "  SKIP: jq not installed (the gh stub evaluates -q filters with it)"
else
  LB="$T/bin_land"; mkdir -p "$LB"
  printf '#!/bin/bash\nexit 0\n' > "$LB/sleep"          # the script waits in 15-30 s steps
  # A gh that answers from fixtures and evaluates `-q` with real jq, so the
  # filter the SCRIPT wrote is the one that selects. The runs fixture has one
  # run that must be approved and four that must not:
  #   101  ours, head commit, CI                       -> approve
  #   102  a FORK, same branch NAME, its own commit    -> never
  #   103  a FORK that pushed the SAME commit          -> never
  #   104  ours, head commit, the relock workflow      -> never
  #   105  ours, an OLD commit on the branch           -> never
  cat > "$LB/gh" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
echo "gh $*" >> "$S/gh.log"
q=""; args=()
while [[ $# -gt 0 ]]; do
  case "$1" in -q) q="$2"; shift 2 ;; *) args+=("$1"); shift ;; esac
done
set -- "${args[@]}"
out() { if [[ -n "$q" ]]; then jq -r "$q"; else cat; fi; }
HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
runs() {   # every held run on the branch NAME, in Actions-API shape
cat <<JSON
{"workflow_runs": [
 {"id": 101, "name": "CI", "event": "pull_request", "head_branch": "dependabot/pip/agents/openai-9", "head_sha": "$HEAD", "head_repository": {"full_name": "me/openbeast"}},
 {"id": 102, "name": "CI", "event": "pull_request", "head_branch": "dependabot/pip/agents/openai-9", "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "head_repository": {"full_name": "evil/openbeast"}},
 {"id": 103, "name": "PR quality", "event": "pull_request", "head_branch": "dependabot/pip/agents/openai-9", "head_sha": "$HEAD", "head_repository": {"full_name": "evil/openbeast"}},
 {"id": 104, "name": "Dependabot relock", "event": "pull_request", "head_branch": "dependabot/pip/agents/openai-9", "head_sha": "$HEAD", "head_repository": {"full_name": "me/openbeast"}},
 {"id": 105, "name": "CI", "event": "pull_request", "head_branch": "dependabot/pip/agents/openai-9", "head_sha": "cccccccccccccccccccccccccccccccccccccccc", "head_repository": {"full_name": "me/openbeast"}}
]}
JSON
}
case "$*" in
  "repo view"*)                   echo '{"nameWithOwner": "me/openbeast"}' | out ;;
  "pr view 7 --json headRefName"*) echo '{"headRefName": "dependabot/pip/agents/openai-9"}' | out ;;
  "pr view 7 --json commits"*)    echo '{"commits": [{"messageHeadline": "deps: regenerate agents/requirements.lock"}]}' | out ;;
  "pr view 7 --json headRefOid"*) echo "{\"headRefOid\": \"$HEAD\"}" | out ;;
  "pr view 7 --json state"*)      echo '{"state": "MERGED"}' | out ;;
  "pr comment"*) echo ok ;;
  "pr merge"*)  echo "$*" >> "$S/merge.log"; echo ok ;;
  "pr checks"*)
      # $S/no_checks = how many more calls answer like a PR whose checks do
      # not exist yet (gh prints this and exits 1, --watch or not).
      n="$(cat "$S/no_checks" 2>/dev/null || echo 0)"
      echo x >> "$S/checks.calls"
      if [[ "$n" -gt 0 ]]; then
        echo $((n - 1)) > "$S/no_checks"
        echo "no checks reported on the 'dependabot/pip/agents/openai-9' branch"; exit 1
      fi
      echo "All checks were successful" ;;
  "pr list"*)
      # 7 bumps a pip requirement; 8 is a github-actions bump (never relocks).
      echo '[{"number": 8, "files": [{"path": ".github/workflows/ci.yml"}]},
             {"number": 7, "files": [{"path": "agents/requirements.txt"}]}]' | out ;;
  "api repos/me/openbeast/commits/main"*)  echo '{"sha": "m"}' | out ;;
  "api repos/me/openbeast/compare/"*)      echo '{"behind_by": 0}' | out ;;
  "api -X POST repos/me/openbeast/actions/runs/"*"/approve")
      id="${4#repos/me/openbeast/actions/runs/}"; echo "${id%/approve}" >> "$S/approved" ;;
  "api repos/me/openbeast/actions/runs?"*)
      # like GitHub: head_sha= filters by commit; nothing filters by repo
      sha="$(sed -n 's/.*head_sha=\([0-9a-f]*\).*/\1/p' <<< "$2")"
      runs | jq --arg s "$sha" '{workflow_runs: [.workflow_runs[] | select($s == "" or .head_sha == $s)]}' | out ;;
  "run list --branch"*"--status action_required"*)
      # the gh CLI's shape for the same runs (what the OLD code asked for)
      runs | jq '[.workflow_runs[] | {databaseId: .id, workflowName: .name, headSha: .head_sha}]' | out ;;
  "run list"*) echo '[]' | out ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
STUB
  chmod +x "$LB/gh" "$LB/sleep"
  # A sandbox copy: the script keeps its lock in <checkout>/.run, and the
  # real checkout's .run is not this test's to write.
  LR="$T/land_repo"; mkdir -p "$LR/scripts"
  install -m 755 "$REPO_DIR/scripts/land-dependabot.sh" "$LR/scripts/"
  land() { # land [args…] — sets _out/_rc; fresh logs each run
    : > "$T/state/gh.log"; : > "$T/state/approved"; : > "$T/state/merge.log"; : > "$T/state/checks.calls"
    _out="$(env PATH="$LB:$PATH" TMPDIR="$T/land_tmp" bash "$LR/scripts/land-dependabot.sh" "$@" 2>&1)"; _rc=$?
  }
  mkdir -p "$T/land_tmp"; echo 0 > "$T/state/no_checks"
  land 7
  _appr="$(sort -n "$T/state/approved" | tr '\n' ' ')"
  if [[ "$_appr" == "101 " ]]; then
    pass "only run 101 (this repo, the PR's head commit, not the relock) was approved"
  else
    fail "approved: '$_appr' (want only 101; 102/103 are a fork, 104 relock, 105 stale) :: $_out"
  fi
  # NEGATIVE CONTROL: the stub really does offer the fork's runs by branch
  # NAME — i.e. a branch-name filter WOULD have swept them in.
  _old="$(PATH="$LB:$PATH" gh run list --branch dependabot/pip/agents/openai-9 --status action_required \
          --json databaseId,workflowName -q '.[] | select(.workflowName!="Dependabot relock") | .databaseId' | tr '\n' ' ')"
  if [[ "$_old" == *102* && "$_old" == *103* ]]; then
    pass "negative control: a branch-name query returns the fork's runs (102, 103), so the case can tell"
  else
    fail "control: the fixture does not reproduce the fork case ('$_old')"
  fi
  if [[ $_rc -eq 0 ]] && has "$_out" "PR 7 -> MERGED" && ! has "$_out" "unexpected gh call"; then
    pass "the rest of the chain ran to the merge against the stub (no unexpected gh calls)"
  else
    fail "land-dependabot run (rc=$_rc): $_out"
  fi

  # --- 2026-10-09 review, supply S10 -------------------------------------
  HEAD7=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  if grep -q -- "pr merge 7 .*--match-head-commit $HEAD7" "$T/state/merge.log"; then
    pass "the merge is pinned to the head commit that was watched (--match-head-commit)"
  else
    fail "merge was not pinned to the watched commit: $(cat "$T/state/merge.log")"
  fi
  if [[ -f "$LR/.run/land-dependabot.lock" ]] && [[ -z "$(ls -A "$T/land_tmp")" ]]; then
    pass "the lock lives in the checkout's .run/, nothing is created under TMPDIR"
  else
    fail "lock placement: .run=$(ls "$LR/.run" 2>/dev/null | tr '\n' ' ') tmp=$(ls -A "$T/land_tmp" | tr '\n' ' ')"
  fi
  # Opened for append: whatever the lock path leads to is never truncated.
  # Planted at the new path AND at the old world-writable one.
  echo "precious" > "$T/state/victim"; echo "precious" > "$T/state/victim_tmp"
  mkdir -p "$LR/.run"; rm -f "$LR/.run/land-dependabot.lock"
  ln -s "$T/state/victim" "$LR/.run/land-dependabot.lock"
  ln -s "$T/state/victim_tmp" "$T/land_tmp/openbeast-land-dependabot.lock"
  land 7
  if [[ $_rc -eq 0 && "$(cat "$T/state/victim")" == "precious" && "$(cat "$T/state/victim_tmp")" == "precious" ]]; then
    pass "a symlink planted at the lock path (new or old) does not get its target truncated"
  else
    fail "lock open truncated its target (rc=$_rc): '$(cat "$T/state/victim")' / '$(cat "$T/state/victim_tmp")'"
  fi
  rm -f "$LR/.run/land-dependabot.lock" "$T/land_tmp/openbeast-land-dependabot.lock"
  # No arguments: only the PRs this chain can land.
  land
  if [[ $_rc -eq 0 ]] && has "$_out" "=== PR 7" && ! has "$_out" "=== PR 8" \
     && ! grep -q "pr comment 8\|pr merge 8" "$T/state/gh.log" && has "$_out" "Skipping 1 Dependabot PR"; then
    pass "no-arg mode lands PR 7 (agents/requirements.txt) and skips PR 8 (a workflow bump), saying so"
  else
    fail "no-arg selection (rc=$_rc): $_out"
  fi
  # Checks that have not appeared yet are waited for, not treated as done.
  echo 3 > "$T/state/no_checks"
  land 7
  if [[ $_rc -eq 0 ]] && has "$_out" "no checks reported yet" && has "$_out" "PR 7 -> MERGED" \
     && [[ "$(wc -l < "$T/state/checks.calls")" -eq 4 ]]; then
    pass "'no checks reported' is retried until the checks exist, then the PR lands"
  else
    fail "late-appearing checks (rc=$_rc, $(wc -l < "$T/state/checks.calls") checks calls): $_out"
  fi
  # NEGATIVE CONTROL: checks that never appear stop the run — bounded, and
  # WITHOUT attempting a merge.
  echo 1000 > "$T/state/no_checks"
  land 7
  if [[ $_rc -ne 0 ]] && has "$_out" "no checks appeared" && [[ ! -s "$T/state/merge.log" ]] \
     && [[ "$(wc -l < "$T/state/checks.calls")" -eq 30 ]]; then
    pass "control: checks that never appear give up after a bounded wait, with no merge"
  else
    fail "never-appearing checks (rc=$_rc, $(wc -l < "$T/state/checks.calls") calls, merge=$(cat "$T/state/merge.log")): $_out"
  fi
  echo 0 > "$T/state/no_checks"
fi

# ===========================================================================
echo ""
echo "4. workflows — actions pinned by SHA; the relock push job never resolves:"
# ===========================================================================
# check_workflows <dir>: prints one line per violation, nothing when clean.
# Run on the real workflows AND on a fixture shaped like the old relock job,
# so the checker is shown to be able to fail.
cat > "$T/check_workflows.py" <<'PY'
import glob, os, re, sys
import yaml
bad = []
for path in sorted(glob.glob(os.path.join(sys.argv[1], "*.yml"))):
    name = os.path.basename(path)
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        m = re.search(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", line)
        if not m or m.group(1).startswith("./"):
            continue
        ref = m.group(1).rsplit("@", 1)
        if len(ref) != 2 or not re.fullmatch(r"[0-9a-f]{40}", ref[1]):
            bad.append(f"{name}:{n}: {m.group(1)} is not pinned to a full commit SHA")
        elif not re.search(r"#\s*v\d", m.group(2)):
            bad.append(f"{name}:{n}: {m.group(1)} has no '# vX' tag comment")
    if name != "dependabot-relock.yml":
        continue
    jobs = yaml.safe_load(open(path, encoding="utf-8")).get("jobs", {})
    for jname, job in jobs.items():
        perms = job.get("permissions") or {}
        writes = any(v == "write" for v in perms.values()) if isinstance(perms, dict) else perms == "write-all"
        steps = job.get("steps", [])
        runs = "\n".join(str(s.get("run", "")) for s in steps)
        if writes and re.search(r"pydeps\.sh lock|pip install(?! -q --dry-run)|pip download", runs):
            bad.append(f"relock job {jname!r} has a write token AND runs the resolver / pip")
        if writes:
            for s in steps:
                env = s.get("env") or {}
                if "GH_TOKEN" in env or "GITHUB_TOKEN" in env:
                    r = str(s.get("run", ""))
                    if "push origin" not in r:
                        bad.append(f"relock job {jname!r}: a token reaches a step that does not push")
                    if re.search(r"\bgit (-c \S+ )*commit\b", r):
                        bad.append(f"relock job {jname!r}: the token is in the environment of `git commit`")
            for s in steps:
                r = str(s.get("run", ""))
                # `git [-c …, possibly over continued lines] commit`
                if re.search(r"\bgit\b(?:[^\n]*\\\n)*[^\n]*\bcommit\b", r) and (
                        "core.hooksPath=/dev/null" not in r or "--no-verify" not in r):
                    bad.append(f"relock job {jname!r}: git commit may run repository hooks")
            if not job.get("needs"):
                bad.append(f"relock job {jname!r} writes but does not depend on a separate resolve job")
print("\n".join(bad))
PY
if "$REAL_PY" -c 'import yaml' 2>/dev/null; then
  _v="$("$REAL_PY" "$T/check_workflows.py" "$REPO_DIR/.github/workflows" 2>&1)"
  if [[ -z "$_v" ]]; then
    pass "every workflow action is SHA-pinned (tag in a comment), and the relock push job never runs the resolver"
  else
    fail "workflow violations:
$_v"
  fi
  # NEGATIVE CONTROL: the shape the review flagged — one job, write token,
  # mutable tags, resolver and a token-carrying `git commit` together.
  mkdir -p "$T/wf_old"
  cat > "$T/wf_old/dependabot-relock.yml" <<'YML'
jobs:
  relock:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v7
      - run: ./scripts/pydeps.sh lock
      - env:
          GH_TOKEN: x
        run: |
          git commit -q -m relock
          git push origin HEAD
YML
  _v="$("$REAL_PY" "$T/check_workflows.py" "$T/wf_old" 2>&1)"
  if has "$_v" "not pinned to a full commit SHA" && has "$_v" "runs the resolver" \
     && has "$_v" "environment of \`git commit\`" && has "$_v" "may run repository hooks"; then
    pass "negative control: the old single-job relock shape trips every one of those checks"
  else
    fail "negative control: the checker passed the old relock shape: $_v"
  fi
elif [[ "${CI:-}" == "true" ]]; then
  fail "PyYAML not importable under CI=true: the workflow checks did not run"
else
  echo "  SKIP: PyYAML not importable (it is in agents/requirements.lock; CI has it)"
fi

# ===========================================================================
echo ""
echo "5. client SearXNG — pins the SAME image digest as the rig:"
# ===========================================================================
# update.sh --images rewrites docker-compose.yml only, so the client file
# drifted (252cfb5b vs the rig's 892cf809) with a comment claiming lockstep.
_rig="$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(searxng\/searxng[^[:space:]]*\).*/\1/p' "$REPO_DIR/docker-compose.yml")"
_cli="$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(searxng\/searxng[^[:space:]]*\).*/\1/p' "$REPO_DIR/scripts/client-searxng.compose.yml")"
if [[ "$_rig" == *@sha256:* && "$(wc -l <<< "$_rig")" == "1" ]]; then
  pass "control: the rig compose has exactly one digest-pinned searxng image (${_rig##*@sha256:})"
else
  fail "control: could not read one pinned searxng image from docker-compose.yml: '$_rig'"
fi
if [[ -n "$_cli" && "$_cli" == "$_rig" ]]; then
  pass "scripts/client-searxng.compose.yml pins the rig's exact searxng image"
else
  fail "client SearXNG pin drifted: client '$_cli' vs rig '$_rig' — mirror the rig's digest (docs/UPDATING.md)"
fi

# ===========================================================================
echo ""
echo "6. extension compose fragments — every image digest-pinned:"
# ===========================================================================
# bundle.sh carries them and update.sh --images bumps them (both now read
# extensions/*/compose.yaml); both assume a `<repo>:<tag>@sha256:<64 hex>`
# pin, and a bare tag would be pulled mutable by `docker compose up`.
_n=0; _bad=""
for _cf in "$REPO_DIR"/extensions/*/compose.yaml; do
  [[ -f "$_cf" ]] || continue
  while IFS= read -r _img; do
    _n=$((_n + 1))
    [[ "$_img" =~ ^[^[:space:]@]+:[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] || _bad+="${_cf#"$REPO_DIR"/}: $_img "
  done < <(sed -n 's/^[[:space:]]*image:[[:space:]]*\([^[:space:]#]*\).*/\1/p' "$_cf")
done
if [[ $_n -ge 1 && -z "$_bad" ]]; then
  pass "all $_n extension image(s) are pinned <repo>:<tag>@sha256:<digest>"
else
  fail "extension images not digest-pinned (checked $_n): $_bad"
fi
# NEGATIVE CONTROL: the same check trips on a bare tag and on a tagless digest.
for _img in "binwiederhier/ntfy:v2.28.0" "binwiederhier/ntfy@sha256:$(printf 'a%.0s' {1..64})"; do
  if [[ "$_img" =~ ^[^[:space:]@]+:[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]; then
    fail "negative control: '$_img' passed the pin check"
  else
    pass "negative control: '$_img' is rejected"
  fi
done

# ===========================================================================
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
