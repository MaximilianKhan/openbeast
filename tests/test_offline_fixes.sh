#!/bin/bash
# Air-gap / offline review fixes (2026-09-17) — behavior tests.
#
# Usage: bash tests/test_offline_fixes.sh
#
# Everything runs against THROWAWAY copies of the scripts under $TMPDIR, with
# stub `hf`, `curl`, `docker`, `git`, `cp` and `python3 -m pip` on PATH. No
# weight is downloaded, no image is loaded, no package is installed, and the
# real repo, weights dir and docker daemon are never touched.
#
# The repo's test doctrine, followed here: a test BUILDS ITS OWN CASE (the
# stub is the input, and the stub RECORDS ITS CALLS so the assertion is about
# what the script did, not what it printed), and every positive assertion has
# a NEGATIVE CONTROL next to it, so a test that can only pass is visible.
#
#   1  fetch-weight.sh   a renamed row must never overwrite another weight
#   2  bootstrap.sh      a hash MISMATCH never falls back to the unpinned install
#   3  bootstrap.sh      a stale lock is detected before it is installed from
#      update.sh         --python regenerates the lock with the pins
#   4  bundle.sh         image IDs across image-store kinds
#   5  bundle.sh         weights: .partial + verify, and "exists" != "correct"
#   6  bundle.sh         a loaded image compose does not reference is LOUD
#   7  bundle.sh         --key "" / bare --key is an error, never a downgrade
#   9  bundle.sh/pydeps  relative paths resolve against the CALLER's cwd
#  10  bootstrap.sh      the `grep -c || echo 0` idiom
#  11  update.sh         a low-speed stall is a NETWORK fault
# (8, Finder droppings, is python: tests/test_bundle_manifest.py and
#  tests/test_pydeps_lock.py.)

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
# has <haystack> <needle> — fixed-string, and no pipe, so there is no
# `cmd | grep -q` exit status to misread under pipefail.
has()  { grep -qF -- "$2" <<< "$1"; }
# count_lines <file> <fixed-string>: never the `grep -c || echo 0` idiom.
count_lines() { local n; n="$(grep -cF -- "$2" "$1" 2>/dev/null || true)"; echo "${n:-0}"; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-offline-fixes-XXXXXX")"
trap 'rm -rf "$T"' EXIT
REAL_PY="$(command -v python3)"
REAL_CP="$(command -v cp)"
export REAL_PY REAL_CP

echo "=== offline / air-gap review fixes ==="

# ---------------------------------------------------------------------------
# A sandbox repo: the REAL scripts under test, copied; everything around them
# (registry, compose, requirements, lock) built by this test.
# ---------------------------------------------------------------------------
SB="$T/repo"
mkdir -p "$SB/scripts/lib" "$SB/agents" "$T/bin" "$T/state"
for f in fetch-weight.sh verify-weights.sh bundle.sh pydeps.sh update.sh; do
  install -m 755 "$REPO_DIR/scripts/$f" "$SB/scripts/$f"
done
for f in weights.sh conf.sh hardware.sh bundle_manifest.py pydeps_lock.py; do
  install -m 644 "$REPO_DIR/scripts/lib/$f" "$SB/scripts/lib/$f"
done
export OB_STUB_STATE="$T/state"
sha_of() { sha256sum "$1" | awk '{print $1}'; }

# ===========================================================================
echo ""
echo "1. fetch-weight.sh — staging dir, never \$WEIGHTS_DIR/<remote>:"
# ===========================================================================
W="$T/weights"; mkdir -p "$W"
export OPENBEAST_WEIGHTS_DIR="$W"

# The stub writes a file named <remote> into whatever --local-dir it is given
# — exactly what makes the real `hf` destructive when that dir is WEIGHTS_DIR.
cat > "$T/bin/hf" <<'STUB'
#!/bin/bash
# hf download <repo> <remote> --local-dir <dir>
repo="$2"; remote="$3"; dir="$5"
echo "local-dir=$dir remote=$remote" >> "$OB_STUB_STATE/hf.log"
mkdir -p "$dir/.cache/huggingface/download"
if [[ -f "$OB_STUB_STATE/hf_fail" ]]; then
  echo "half" > "$dir/.cache/huggingface/download/$remote.incomplete"
  echo "stub hf: connection dropped" >&2
  exit 1
fi
printf 'BYTES-FROM-%s' "$repo" > "$dir/$remote"
STUB
printf '#!/bin/bash\nexit 0\n' > "$T/bin/curl"          # "online"
chmod +x "$T/bin/hf" "$T/bin/curl"

# Registry rows that reproduce the real collision: the MTP row's REMOTE name
# is the non-MTP row's LOCAL name.
_mtp_body='BYTES-FROM-org/model-MTP-GGUF'
_mtp_sha="$(printf '%s' "$_mtp_body" | sha256sum | awk '{print $1}')"
_pln_body='BYTES-FROM-org/plain-GGUF'
_pln_sha="$(printf '%s' "$_pln_body" | sha256sum | awk '{print $1}')"
{
  printf '%s\t%s\t%s\t%s\t%s\n' "$_pln_sha" "${#_pln_body}" "model.gguf"     "org/model-GGUF"     "-"
  printf '%s\t%s\t%s\t%s\t%s\n' "$_mtp_sha" "${#_mtp_body}" "model-MTP.gguf" "org/model-MTP-GGUF" "model.gguf"
  printf '%s\t%s\t%s\t%s\t%s\n' "$_pln_sha" "${#_pln_body}" "plain.gguf"     "org/plain-GGUF"     "-"
  printf '%s\t%s\t%s\t%s\t%s\n' "$(printf 'x%.0s' {1..64} | tr x 0)" "${#_pln_body}" "badsum.gguf" "org/plain-GGUF" "-"
} > "$SB/scripts/weights.registry"

# The test has teeth only if the stub really IS destructive when aimed at the
# weights dir. Prove that first, on a scratch copy.
mkdir -p "$T/scratch"; echo "SENTINEL" > "$T/scratch/model.gguf"
"$T/bin/hf" download org/model-MTP-GGUF model.gguf --local-dir "$T/scratch" >/dev/null 2>&1
if [[ "$(cat "$T/scratch/model.gguf")" != "SENTINEL" ]]; then
  pass "control: the stub hf DOES overwrite a same-named file in its --local-dir"
else
  fail "control: the stub hf is not destructive, so this section proves nothing"
fi
: > "$T/state/hf.log"

printf 'THE USERS 20GB NON-MTP WEIGHT' > "$W/model.gguf"
_before="$(sha_of "$W/model.gguf")"
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" model-MTP.gguf 2>&1)"; _rc=$?
if [[ $_rc -eq 0 && "$(sha_of "$W/model.gguf")" == "$_before" ]]; then
  pass "fetching the renamed MTP row leaves the existing non-MTP weight byte-identical"
else
  fail "the non-MTP weight was clobbered (rc=$_rc): $(cat "$W/model.gguf" 2>/dev/null) :: $_out"
fi
if [[ -f "$W/model-MTP.gguf" && "$(cat "$W/model-MTP.gguf")" == "$_mtp_body" ]]; then
  pass "the new weight landed under its LOCAL name with the right content"
else
  fail "model-MTP.gguf did not land: $_out"
fi
_ld="$(sed -n 's/^local-dir=\(.*\) remote=.*/\1/p' "$T/state/hf.log" | head -n 1)"
if [[ -n "$_ld" && "$_ld" != "$W" && "$_ld" == "$W"/* ]]; then
  pass "hf was pointed at a staging dir INSIDE the weights dir (same fs), not at it: ${_ld#"$W"/}"
else
  fail "hf --local-dir was '$_ld' (weights dir: $W)"
fi
if [[ -z "$(find "$W" -maxdepth 1 -name '.fetch.*' 2>/dev/null)" ]]; then
  pass "the staging dir is gone after a successful fetch"
else
  fail "staging dir left behind: $(find "$W" -maxdepth 1 -name '.fetch.*')"
fi

# NEGATIVE CONTROL: a row whose remote name IS its local name still works.
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" plain.gguf 2>&1)"; _rc=$?
if [[ $_rc -eq 0 && "$(cat "$W/plain.gguf" 2>/dev/null)" == "$_pln_body" ]] && has "$_out" "verified against the pin"; then
  pass "negative control: a remote==local row downloads, verifies and lands"
else
  fail "remote==local row broke (rc=$_rc): $_out"
fi

# A checksum mismatch never reaches the final name, and the stage is cleaned.
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" badsum.gguf 2>&1)"; _rc=$?
if [[ $_rc -ne 0 && ! -e "$W/badsum.gguf" ]] && has "$_out" "CHECKSUM MISMATCH" \
   && [[ -z "$(find "$W" -maxdepth 1 -name '.fetch.*')" ]]; then
  pass "a checksum mismatch is refused, never exists under the final name, stage cleaned"
else
  fail "checksum mismatch handling (rc=$_rc): $_out :: $(ls -A "$W")"
fi

# RESUME: a FAILED download keeps its stage, and the retry reuses the SAME dir.
rm -f "$W/model-MTP.gguf"; : > "$T/state/hf.log"; touch "$T/state/hf_fail"
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" model-MTP.gguf 2>&1)"; _rc=$?
_stage1="$(sed -n 's/^local-dir=\(.*\) remote=.*/\1/p' "$T/state/hf.log" | head -n 1)"
if [[ $_rc -ne 0 && -f "$_stage1/.cache/huggingface/download/model.gguf.incomplete" ]] \
   && has "$_out" "re-run this command to resume"; then
  pass "a failed download KEEPS its partial (hf's resume state) and says how to resume"
else
  fail "failed download did not keep its stage (rc=$_rc, stage=$_stage1): $_out"
fi
rm -f "$T/state/hf_fail"
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" model-MTP.gguf 2>&1)"; _rc=$?
_stage2="$(sed -n 's/^local-dir=\(.*\) remote=.*/\1/p' "$T/state/hf.log" | tail -n 1)"
if [[ $_rc -eq 0 && "$_stage1" == "$_stage2" && ! -d "$_stage2" && -f "$W/model-MTP.gguf" ]] \
   && [[ "$(sha_of "$W/model.gguf")" == "$_before" ]]; then
  pass "the retry reuses the SAME staging dir (so hf can resume), then removes it"
else
  fail "retry: rc=$_rc stage1=$_stage1 stage2=$_stage2: $_out"
fi
unset OPENBEAST_WEIGHTS_DIR

# ===========================================================================
echo ""
echo "2+3. bootstrap.sh — hash mismatch is fatal; a stale lock is caught first:"
# ===========================================================================
# bootstrap.sh cannot be run (it builds llama.cpp). Its python step is lifted
# out verbatim between its own section markers and run in a harness that
# supplies the five things it uses from the rest of the file.
_sec="$(sed -n '/^# ---- 3\. Python dependencies/,/^# hf \/ mcpo land in/p' "$REPO_DIR/bootstrap.sh" | sed '$d')"
if has "$_sec" "require-hashes" && has "$_sec" "ob_python_deps_satisfied"; then
  pass "extracted bootstrap's python-deps step ($(wc -l <<< "$_sec") lines)"
else
  fail "could not extract bootstrap's python step — its section markers moved"
fi
{
  echo 'set -euo pipefail'
  echo 'step() { echo "==> $*"; }; ok() { echo "OK: $*"; }; warn() { echo "WARN: $*"; }'
  echo 'die() { echo "DIE: $*" >&2; exit 1; }; ob_offline() { return 1; }'
  echo "$_sec"
  echo 'echo HARNESS-REACHED-END'
} > "$T/bootstrap_py_step.sh"

# `python3` stub: intercepts `-m pip` and the satisfaction probe, records
# both, and passes EVERYTHING else (pydeps_lock.py, the PEP-668 probe) to the
# real interpreter. Mode comes from a file so each case sets its own.
cat > "$T/bin/python3" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
mode="$(cat "$S/pip_mode" 2>/dev/null || echo ok)"
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
  echo "pip ${*:3}" >> "$S/pip.log"
  case "${3:-}" in
    install)
      if [[ " $* " == *" --require-hashes "* ]]; then
        case "$mode" in
          hashfail)
            # pip's real text (pip/_internal/exceptions.py, HashMismatch)
            cat >&2 <<'PIPERR'
ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE. If you have updated the package versions, please update the hashes. Otherwise, examine the package contents carefully; someone may have tampered with them.
    foo==1.0 from https://mirror.example/foo-1.0-py3-none-any.whl:
        Expected sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
             Got        bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
PIPERR
            exit 1 ;;
          compatfail)
            echo "ERROR: Ignored the following versions that require a different python version: 1.0 Requires-Python >=3.99" >&2
            echo "ERROR: No matching distribution found for foo==1.0" >&2
            exit 1 ;;
          unpinnedfail)
            echo "ERROR: In --require-hashes mode, all requirements must have their versions pinned with ==. These do not:" >&2
            echo "    extra-dep>=2 (from foo==1.0)" >&2
            exit 1 ;;
        esac
      fi
      [[ "$mode" == "noeffect" ]] || echo installed > "$S/pip_installed"
      exit 0 ;;
  esac
  exit 0
fi
# bootstrap's ob_python_deps_satisfied: `python3 - <requirements.txt>` + heredoc
if [[ "${1:-}" == "-" && "${2:-}" == */agents/requirements.txt ]]; then
  cat >/dev/null
  echo "probe" >> "$S/pip.log"
  [[ -f "$S/pip_installed" ]] && exit 0
  echo "foo (not installed)"; exit 1
fi
exec "$REAL_PY" "$@"
STUB
chmod +x "$T/bin/python3"

_lock_with() {           # _lock_with <version>: a parseable one-package lock
  printf '%s==%s \\\n    --hash=sha256:%s\n' foo "$1" "$(printf 'a%.0s' {1..64})" \
    > "$SB/agents/requirements.lock"
  printf 'huggingface_hub==1.0 \\\n    --hash=sha256:%s\n' "$(printf 'b%.0s' {1..64})" \
    >> "$SB/agents/requirements.lock"
}
run_step() {             # run_step <mode> [ENV=VAL...] -> $_out/$_rc, pip.log reset
  local mode="$1"; shift
  echo "$mode" > "$T/state/pip_mode"; : > "$T/state/pip.log"; rm -f "$T/state/pip_installed"
  _out="$(env PATH="$T/bin:$PATH" REPO_DIR="$SB" "$@" bash "$T/bootstrap_py_step.sh" 2>&1)"; _rc=$?
}
n_locked()   { count_lines "$T/state/pip.log" "--require-hashes"; }
n_unpinned() { count_lines "$T/state/pip.log" "agents/requirements.txt"; }

echo 'foo==1.0' > "$SB/agents/requirements.txt"; _lock_with 1.0

run_step hashfail
if [[ $_rc -ne 0 ]] && has "$_out" "HASH MISMATCH" && [[ "$(n_locked)" == "1" && "$(n_unpinned)" == "0" ]]; then
  pass "a pip HASH MISMATCH dies, and the unpinned requirements.txt install is NEVER attempted"
else
  fail "hash mismatch (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
if has "$_out" "THESE PACKAGES DO NOT MATCH"; then
  pass "pip's own report is still shown to the operator (stderr was captured, not swallowed)"
else
  fail "pip's stderr was swallowed: $_out"
fi

# NEGATIVE CONTROL: a COMPAT failure still falls back, loudly.
run_step compatfail
if [[ $_rc -eq 0 && "$(n_locked)" == "1" && "$(n_unpinned)" == "1" ]] && has "$_out" "falling back" \
   && has "$_out" "HARNESS-REACHED-END" && ! has "$_out" "HASH MISMATCH"; then
  pass "negative control: 'No matching distribution' falls back to requirements.txt"
else
  fail "compat fallback (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
# ...and so does an INCOMPLETE closure, which pip words with "hashes"/"pinned"
# but which compares no bytes at all — it must not be mistaken for tampering.
run_step unpinnedfail
if [[ $_rc -eq 0 && "$(n_unpinned)" == "1" ]] && ! has "$_out" "HASH MISMATCH"; then
  pass "negative control: an incomplete closure ('must have their versions pinned') is not called tampering"
else
  fail "incomplete-closure case (rc=$_rc): $_out"
fi
run_step compatfail OPENBEAST_PIP_STRICT=1
if [[ $_rc -ne 0 && "$(n_unpinned)" == "0" ]] && has "$_out" "OPENBEAST_PIP_STRICT=1 forbids"; then
  pass "OPENBEAST_PIP_STRICT=1 still makes ANY locked-install failure fatal"
else
  fail "STRICT semantics changed (rc=$_rc unpinned=$(n_unpinned)): $_out"
fi

# NEGATIVE CONTROL for the stale-lock case: a CURRENT lock is installed from.
run_step ok
if [[ $_rc -eq 0 && "$(n_locked)" == "1" && "$(n_unpinned)" == "0" ]] && ! has "$_out" "STALE"; then
  pass "negative control: a current lock is installed from, with no fallback"
else
  fail "current lock (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
# STALE: requirements.txt moved (a merged Dependabot bump), the lock did not.
echo 'foo==2.0' > "$SB/agents/requirements.txt"
run_step ok
if [[ $_rc -eq 0 && "$(n_locked)" == "0" && "$(n_unpinned)" == "1" ]] && has "$_out" "STALE" \
   && has "$_out" "pins 2.0, the lock pins 1.0"; then
  pass "a STALE lock is never installed from: warned (with pydeps' reason), requirements.txt used"
else
  fail "stale lock (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
run_step ok OPENBEAST_PIP_STRICT=1
if [[ $_rc -ne 0 && "$(n_locked)" == "0" && "$(n_unpinned)" == "0" ]] && has "$_out" "does not match"; then
  pass "a stale lock under OPENBEAST_PIP_STRICT=1 dies before pip is run at all"
else
  fail "stale+strict (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
# pip "succeeds" but the pins are still not installed: no green check.
echo 'foo==1.0' > "$SB/agents/requirements.txt"
run_step noeffect
if [[ $_rc -eq 0 ]] && has "$_out" "STILL not satisfied" && has "$_out" "HARNESS-REACHED-END"; then
  pass "deps are re-checked AFTER the install; still-unsatisfied is a loud WARNING, and the install finishes"
else
  fail "post-install recheck (rc=$_rc): $_out"
fi
# ...and fatal only when the operator asked for strictness. (A checker
# false-negative must not turn every fresh install into a dead one.)
run_step noeffect OPENBEAST_PIP_STRICT=1
if [[ $_rc -ne 0 ]] && has "$_out" "STILL not satisfied" && ! has "$_out" "HARNESS-REACHED-END"; then
  pass "under OPENBEAST_PIP_STRICT=1 the same condition is fatal"
else
  fail "post-install recheck, strict (rc=$_rc): $_out"
fi

# --- update.sh --python regenerates the lock WITH the pins ------------------
cat > "$T/bin/py_update" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
  case "${3:-}" in
    show) echo "Version: 9.9.$(( $(cat "$S/upgraded" 2>/dev/null || echo 0) ))" ;;
    install) [[ " $* " == *" -U "* ]] && echo 1 > "$S/upgraded" ;;
  esac
  exit 0
fi
if [[ "${1:-}" == "-" ]]; then cat >/dev/null; [[ -f "$S/import_breaks" ]] && exit 1; exit 0; fi
exec "$REAL_PY" "$@"
STUB
chmod +x "$T/bin/py_update"
SBU="$T/repo_update"; mkdir -p "$SBU/scripts/lib" "$SBU/agents" "$T/binu"
install -m 755 "$REPO_DIR/scripts/update.sh" "$SBU/scripts/update.sh"
install -m 644 "$REPO_DIR/scripts/lib/conf.sh" "$REPO_DIR/scripts/lib/hardware.sh" "$SBU/scripts/lib/"
ln -s "$T/bin/py_update" "$T/binu/python3"
# The stub pydeps records WHAT requirements.txt SAID when `lock` was called:
# regenerating before the pins are rewritten would lock the OLD versions.
cat > "$SBU/scripts/pydeps.sh" <<'STUB'
#!/bin/bash
echo "pydeps $* :: $(grep -v '^#' "$(dirname "$0")/../agents/requirements.txt" | tr '\n' ' ')" >> "$OB_STUB_STATE/pydeps.log"
[[ -f "$OB_STUB_STATE/lock_fails" ]] && exit 1
exit 0
STUB
chmod +x "$SBU/scripts/pydeps.sh"
run_update_python() {
  printf 'openai==1.0\n' > "$SBU/agents/requirements.txt"
  : > "$T/state/pydeps.log"; rm -f "$T/state/upgraded"
  _out="$(PATH="$T/binu:$PATH" "$SBU/scripts/update.sh" --python 2>&1)"; _rc=$?
}
rm -f "$T/state/lock_fails" "$T/state/import_breaks"
run_update_python
if [[ $_rc -eq 0 ]] && has "$(cat "$T/state/pydeps.log")" "pydeps lock :: openai==9.9.1" \
   && has "$_out" "regenerated agents/requirements.lock"; then
  pass "update.sh --python runs 'pydeps.sh lock' AFTER the pins are rewritten"
else
  fail "update --python did not relock (rc=$_rc): $(cat "$T/state/pydeps.log") :: $_out"
fi
touch "$T/state/lock_fails"; run_update_python; rm -f "$T/state/lock_fails"
if [[ $_rc -eq 0 ]] && has "$_out" "could NOT regenerate" && has "$_out" "./scripts/pydeps.sh lock"; then
  pass "when the relock fails it says the lock is now stale and prints the exact command"
else
  fail "relock-failure path (rc=$_rc): $_out"
fi
# NEGATIVE CONTROL: a bump that breaks our imports rolls back and never relocks.
touch "$T/state/import_breaks"; run_update_python; rm -f "$T/state/import_breaks"
if [[ $_rc -ne 0 && ! -s "$T/state/pydeps.log" ]] && [[ "$(cat "$SBU/agents/requirements.txt")" == "openai==1.0" ]]; then
  pass "negative control: a rolled-back upgrade leaves pins AND lock alone"
else
  fail "rollback path relocked or rewrote pins (rc=$_rc): $(cat "$T/state/pydeps.log")"
fi

# ===========================================================================
echo ""
echo "11. update.sh — a low-speed stall is a NETWORK fault:"
# ===========================================================================
mkdir -p "$SBU/llama.cpp/.git" "$SBU/llama.cpp/build/bin"
printf '#!/bin/bash\n' > "$SBU/llama.cpp/build/bin/llama-server"; chmod +x "$SBU/llama.cpp/build/bin/llama-server"
cat > "$T/binu/git" <<'STUB'
#!/bin/bash
echo "git $*" >> "$OB_STUB_STATE/git.log"
case " $* " in
  *" rev-parse "*)    echo abc1234 ;;
  *" symbolic-ref "*) exit 0 ;;
  *" pull "*)         cat "$OB_STUB_STATE/git_pull_says" >&2; exit 1 ;;
esac
STUB
chmod +x "$T/binu/git"
run_update_llama() { : > "$T/state/git.log"; _out="$(PATH="$T/binu:$PATH" "$SBU/scripts/update.sh" --llama 2>&1)"; _rc=$?; }

# What git prints when http.lowSpeedLimit/Time trips mid-transfer (curl 28).
printf '%s\n' "error: RPC failed; curl 28 Operation too slow. Less than 1000 bytes/sec transferred the last 60 seconds" \
              "fatal: early EOF" > "$T/state/git_pull_says"
run_update_llama
if [[ $_rc -eq 0 ]] && has "$_out" "NOT a local problem" && ! has "$_out" "git pull failed"; then
  pass "'Operation too slow' (the stall this script's own lowSpeed settings produce) is a network fault"
else
  fail "stall misreported (rc=$_rc): $_out"
fi
printf '%s\n' "fatal: unable to access 'https://github.com/x/': Operation too slow. Less than 1000 bytes/sec transferred the last 60 seconds" > "$T/state/git_pull_says"
run_update_llama
if [[ $_rc -eq 0 ]] && has "$_out" "NOT a local problem"; then
  pass "...in its connect-phase wording too"
else
  fail "connect-phase stall misreported (rc=$_rc): $_out"
fi
# NEGATIVE CONTROL: a dirty worktree is still a LOCAL fault, in git's words.
printf '%s\n' "error: Your local changes to the following files would be overwritten by merge:" "  ggml.c" > "$T/state/git_pull_says"
run_update_llama
if [[ $_rc -ne 0 ]] && has "$_out" "git pull failed" && has "$_out" "Your local changes" && ! has "$_out" "NOT a local problem"; then
  pass "negative control: a dirty worktree is still reported as a local failure"
else
  fail "dirty worktree misclassified (rc=$_rc): $_out"
fi
if [[ "$(count_lines "$T/state/git.log" "http.lowSpeedTime=60")" -ge 1 && "$(count_lines "$T/state/git.log" "http.lowSpeedTime=15")" == "0" ]]; then
  pass "the pull is bounded at lowSpeedTime=60 (recorded by the stub), not 15"
else
  fail "lowSpeedTime: $(cat "$T/state/git.log")"
fi

# ===========================================================================
echo ""
echo "10. bootstrap.sh — the grep -c idiom:"
# ===========================================================================
_line="$(grep -E '^[[:space:]]*_n_img=' "$REPO_DIR/bootstrap.sh" | head -n 1 || true)"
mkdir -p "$T/g0" "$T/g2"
printf 'services:\n  web:\n    build: .\n' > "$T/g0/docker-compose.yml"
printf 'services:\n  a:\n    image: x\n  b:\n    image: y\n' > "$T/g2/docker-compose.yml"
_n0="$(REPO_DIR="$T/g0" bash -c "set -euo pipefail; $_line; printf '%s' \"\$_n_img\"" 2>&1)"
_n2="$(REPO_DIR="$T/g2" bash -c "set -euo pipefail; $_line; printf '%s' \"\$_n_img\"" 2>&1)"
if [[ -n "$_line" && "$_n0" == "0" && "$_n2" == "2" ]]; then
  pass "zero matches yields exactly '0' (and two yields '2') under set -euo pipefail"
else
  fail "_n_img: zero-match='$_n0' two-match='$_n2' line='$_line'"
fi
_old="$(REPO_DIR="$T/g0" bash -c 'n=$(grep -cE "^\s+image:" "$REPO_DIR/docker-compose.yml" || echo 0); printf "%s" "$n"')"
if [[ "$_old" == $'0\n0' ]]; then
  pass "control: the OLD idiom really does yield '0\\n0' on this case, so the case can tell"
else
  fail "control: old idiom yielded '$_old' — this case does not exercise the bug"
fi

# ===========================================================================
echo ""
echo "4+6. bundle.sh install — image IDs across store kinds, and unmatched refs:"
# ===========================================================================
# A stateful docker stub. `ids` maps a name-or-id to the ID this "daemon"
# reports for it; `load` prints what it is told to and adds what it is told to.
cat > "$T/bin/docker" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
echo "docker $*" >> "$S/docker.log"
lookup() { awk -F'\t' -v k="$1" '$1==k {print $2; f=1; exit} END {exit !f}' "$S/ids"; }
case "${1:-}" in
  info)    cat "$S/store_status" ;;
  load)    cat >/dev/null
           if [[ -f "$S/load_fail" ]]; then echo "write /var/lib/docker/tmp: no space left on device" >&2; exit 1; fi
           cat "$S/load_adds" >> "$S/ids"; cat "$S/load_says" ;;
  inspect) lookup "${@: -1}" ;;
  image)   lookup "${@: -1}" >/dev/null ;;
  save)    echo "FAKE IMAGE TAR $2" ;;
  *)       exit 0 ;;
esac
STUB
chmod +x "$T/bin/docker"
CONTAINERD='[[driver-type io.containerd.snapshotter.v1]]'
CLASSIC='[[Backing Filesystem extfs] [Supports d_type true] [Using metacopy false] [Native Overlay Diff true]]'
REF_WEB="reg.example/web:main@sha256:$(printf '1%.0s' {1..64})"
REF_SEARCH="reg.example/search@sha256:$(printf '2%.0s' {1..64})"
ID_REC="sha256:$(printf 'a%.0s' {1..64})"        # what the BUILD box recorded
ID_LOCAL="sha256:$(printf 'c%.0s' {1..64})"      # what THIS daemon calls the same image
ID_SEARCH="sha256:$(printf 'e%.0s' {1..64})"
: > "$SB/scripts/weights.registry"

mk_bundle() {            # mk_bundle <dir> <image_store-or-""> <ref> <recorded-id>
  local d="$1" store="$2" ref="$3" id="$4" meta
  rm -rf "$d"; mkdir -p "$d/images"
  echo "IMAGE BYTES" | gzip -n > "$d/images/img.tar.gz"
  meta="$(printf '{"images": [{"ref": "%s", "id": "%s", "file": "images/img.tar.gz"}]' "$ref" "$id")"
  [[ -z "$store" ]] || meta="$meta, \"image_store\": \"$store\""
  "$REAL_PY" "$SB/scripts/lib/bundle_manifest.py" write "$d" --built-at t --repo-commit cafe1234 \
      --component images:images --meta "images:$meta}" >/dev/null
}
reset_box() {            # reset_box <local store status> [search-present=1]
  printf '%s\n' "$1" > "$T/state/store_status"
  : > "$T/state/docker.log"; : > "$T/state/ids"; : > "$T/state/load_adds"; : > "$T/state/load_says"
  rm -f "$T/state/load_fail" "$SB/docker-compose.yml.pre-bundle"
  [[ "${2:-1}" == "1" ]] && printf '%s\t%s\n' "$REF_SEARCH" "$ID_SEARCH" >> "$T/state/ids"
  printf 'services:\n  web:\n    image: %s\n  search:\n    image: %s\n' "$REF_WEB" "$REF_SEARCH" > "$SB/docker-compose.yml"
}
install_bundle() { _out="$(cd "$T" && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" install "$@" 2>&1)"; _rc=$?; }
compose_web() { sed -n '/^  web:/,/^  search:/s/^ *image: *//p' "$SB/docker-compose.yml"; }
B="$T/usb/bundle"; mkdir -p "$T/usb"

# SAME kind at both ends: IDs must agree, and do.
mk_bundle "$B" containerd "$REF_WEB" "$ID_REC"; reset_box "$CONTAINERD"
printf '%s\t%s\n' "$ID_REC" "$ID_REC" > "$T/state/load_adds"
echo "Loaded image ID: $ID_REC" > "$T/state/load_says"
install_bundle "$B"
if [[ $_rc -eq 0 && "$(compose_web)" == "$ID_REC" ]] && has "$_out" "ID matches the manifest" \
   && has "$_out" "every image docker-compose.yml references resolves locally" \
   && ! has "$_out" "grep: warning"; then
  pass "same store kind: ID verified against the manifest, compose rewritten, all refs resolve"
else
  fail "same-store install (rc=$_rc web=$(compose_web)): $_out"
fi

# CROSS-STORE: built on containerd, installed on classic. The daemon gives the
# same image a DIFFERENT id. This used to die "is not present afterwards".
mk_bundle "$B" containerd "$REF_WEB" "$ID_REC"; reset_box "$CLASSIC"
printf '%s\t%s\n' "$ID_LOCAL" "$ID_LOCAL" > "$T/state/load_adds"
echo "Loaded image ID: $ID_LOCAL" > "$T/state/load_says"
install_bundle "$B"
if [[ $_rc -eq 0 && "$(compose_web)" == "$ID_LOCAL" ]] && has "$_out" "image-ID verification SKIPPED" \
   && has "$_out" "built on: containerd" && has "$_out" "sha256 in the manifest"; then
  pass "cross-store: installs, SAYS the ID check was skipped and why, compose -> what was ACTUALLY loaded"
else
  fail "cross-store install (rc=$_rc web=$(compose_web)): $_out"
fi
# ...and when docker names the image instead of printing an ID.
mk_bundle "$B" classic "$REF_WEB" "$ID_REC"; reset_box "$CONTAINERD"
printf '%s\t%s\n' "reg.example/web:main" "$ID_LOCAL" "$ID_LOCAL" "$ID_LOCAL" > "$T/state/load_adds"
echo "Loaded image: reg.example/web:main" > "$T/state/load_says"
install_bundle "$B"
if [[ $_rc -eq 0 && "$(compose_web)" == "$ID_LOCAL" ]]; then
  pass "cross-store, 'Loaded image: <name>' form: the name is resolved to this daemon's ID"
else
  fail "Loaded-image-name form (rc=$_rc web=$(compose_web)): $_out"
fi

# NEGATIVE CONTROL: same kind and the IDs DISAGREE -> that is real, refuse.
mk_bundle "$B" classic "$REF_WEB" "$ID_REC"; reset_box "$CLASSIC"
printf '%s\t%s\n' "$ID_LOCAL" "$ID_LOCAL" > "$T/state/load_adds"
echo "Loaded image ID: $ID_LOCAL" > "$T/state/load_says"
install_bundle "$B"
if [[ $_rc -ne 0 && "$(compose_web)" == "$REF_WEB" ]] && has "$_out" "these should be equal" \
   && ! has "$_out" "this is docker's load, not the file"; then
  pass "negative control: same store kind + different ID is still REFUSED, compose untouched"
else
  fail "same-store mismatch (rc=$_rc web=$(compose_web)): $_out"
fi

# BACKWARD COMPAT: a manifest from before image_store existed.
mk_bundle "$B" "" "$REF_WEB" "$ID_REC"; reset_box "$CONTAINERD"
printf '%s\t%s\n' "$ID_REC" "$ID_REC" > "$T/state/load_adds"
echo "Loaded image ID: $ID_REC" > "$T/state/load_says"
install_bundle "$B"
if [[ $_rc -eq 0 && "$(compose_web)" == "$ID_REC" ]] && has "$_out" "ID matches the manifest"; then
  pass "a manifest with no image_store field still installs and still verifies by ID when it can"
else
  fail "legacy manifest, matching id (rc=$_rc): $_out"
fi
mk_bundle "$B" "" "$REF_WEB" "$ID_REC"; reset_box "$CLASSIC"
printf '%s\t%s\n' "$ID_LOCAL" "$ID_LOCAL" > "$T/state/load_adds"
echo "Loaded image ID: $ID_LOCAL" > "$T/state/load_says"
install_bundle "$B"
if [[ $_rc -eq 0 && "$(compose_web)" == "$ID_LOCAL" ]] && has "$_out" "SKIPPED" && has "$_out" "not known"; then
  pass "...and when it cannot, says the store kind is unknown rather than claiming a mismatch"
else
  fail "legacy manifest, differing id (rc=$_rc): $_out"
fi

# A load that FAILS is reported as a failed load, with docker's reason.
mk_bundle "$B" classic "$REF_WEB" "$ID_REC"; reset_box "$CLASSIC"; touch "$T/state/load_fail"
install_bundle "$B"
if [[ $_rc -ne 0 ]] && has "$_out" "docker load failed" && has "$_out" "no space left on device"; then
  pass "a failed docker load is reported as one, with docker's own reason"
else
  fail "failed load (rc=$_rc): $_out"
fi

# 6: the loaded image's ref has NO image: line in this checkout's compose.
mk_bundle "$B" classic "reg.example/web:main@sha256:$(printf '9%.0s' {1..64})" "$ID_REC"; reset_box "$CLASSIC"
printf '%s\t%s\n' "$ID_REC" "$ID_REC" > "$T/state/load_adds"
echo "Loaded image ID: $ID_REC" > "$T/state/load_says"
_compose_before="$(sha_of "$SB/docker-compose.yml")"
install_bundle "$B"
if [[ $_rc -ne 0 ]] && has "$_out" "has no \`image:\` line for it" && has "$_out" "$REF_WEB" \
   && has "$_out" "cafe1234" && ! has "$_out" "==> done" \
   && [[ "$(sha_of "$SB/docker-compose.yml")" == "$_compose_before" ]]; then
  pass "a loaded image compose does not reference DIES, naming what this checkout wants and the build commit"
else
  fail "unmatched ref was silent (rc=$_rc): $_out"
fi
# 6: compose references an image that is not local and the bundle lacks it.
mk_bundle "$B" classic "$REF_WEB" "$ID_REC"; reset_box "$CLASSIC" 0
printf '%s\t%s\n' "$ID_REC" "$ID_REC" > "$T/state/load_adds"
echo "Loaded image ID: $ID_REC" > "$T/state/load_says"
install_bundle "$B"
if [[ $_rc -ne 0 ]] && has "$_out" "NOT in" && has "$_out" "$REF_SEARCH" && has "$_out" "installed, BUT 1 image(s)" \
   && [[ "$(compose_web)" == "$ID_REC" ]] && [[ "$(count_lines "$T/state/docker.log" "image inspect")" == "2" ]]; then
  pass "an image compose needs but nothing provides is LISTED and the exit status is non-zero"
else
  fail "unresolved compose image (rc=$_rc): $_out"
fi

# Build side: the store kind is detected and recorded (both kinds).
SBB="$T/repo_build"; mkdir -p "$SBB/scripts/lib" "$SBB/agents"
install -m 755 "$REPO_DIR/scripts/bundle.sh" "$SBB/scripts/bundle.sh"
install -m 644 "$REPO_DIR/scripts/lib/conf.sh" "$REPO_DIR/scripts/lib/bundle_manifest.py" "$SBB/scripts/lib/"
printf 'foo==1.0 \\\n    --hash=sha256:%s\n' "$(printf 'a%.0s' {1..64})" > "$SBB/agents/requirements.lock"
printf 'services:\n  web:\n    image: %s\n' "$REF_WEB" > "$SBB/docker-compose.yml"
cat > "$SBB/scripts/pydeps.sh" <<'STUB'
#!/bin/bash
[[ "$1" == "wheelhouse" ]] && { mkdir -p "$2"; echo WHEEL > "$2/foo-1.0-py3-none-any.whl"; }
exit 0
STUB
chmod +x "$SBB/scripts/pydeps.sh"
store_in() { "$REAL_PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); print([c.get("image_store") for c in d["components"] if c["kind"]=="images"][0])' "$1/MANIFEST.json" 2>&1; }
for _k in containerd classic; do
  reset_box "$([[ $_k == containerd ]] && echo "$CONTAINERD" || echo "$CLASSIC")"
  printf '%s\t%s\n' "$REF_WEB" "$ID_REC" >> "$T/state/ids"
  _out="$(cd "$T/usb" && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SBB/scripts/bundle.sh" build "./out-$_k" --no-source 2>&1)"; _rc=$?
  if [[ $_rc -eq 0 && "$(store_in "$T/usb/out-$_k")" == "$_k" ]]; then
    pass "build records image_store=$_k from docker info's DriverStatus"
  else
    fail "build on a $_k store (rc=$_rc, recorded '$(store_in "$T/usb/out-$_k")'): $_out"
  fi
done

# ===========================================================================
echo ""
echo "9. relative <dir> arguments resolve against the CALLER's cwd:"
# ===========================================================================
if [[ -f "$T/usb/out-classic/MANIFEST.json" && ! -e "$SBB/out-classic" ]]; then
  pass "'build ./out' from /media/usb-like cwd wrote the bundle THERE, not into the repo"
else
  fail "build ./out landed in the wrong place: $(ls "$SBB")"
fi
_out="$(cd "$T/usb" && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" verify ./bundle 2>&1)"; _rc=$?
if [[ $_rc -eq 0 && ! -e "$SB/bundle" ]] && has "$_out" "0 problem(s)"; then
  pass "'verify ./bundle' from another cwd finds the caller's ./bundle (the repo has none)"
else
  fail "verify ./bundle from another cwd (rc=$_rc): $_out"
fi
# NEGATIVE CONTROL: a relative path that exists NOWHERE still fails, and an
# absolute path still works.
_out="$(cd "$T/usb" && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" verify ./no-such 2>&1)"; _rc=$?
_out2="$(cd / && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" verify "$B" 2>&1)"; _rc2=$?
if [[ $_rc -ne 0 && $_rc2 -eq 0 ]]; then
  pass "negative control: a missing ./dir still fails; an absolute path still works"
else
  fail "rc(missing)=$_rc rc(absolute)=$_rc2: $_out :: $_out2"
fi
# pydeps.sh: audit ./wh from another cwd.
mkdir -p "$T/usb/wh"; echo WHEEL > "$T/usb/wh/foo-1.0-py3-none-any.whl"
printf 'foo==1.0 \\\n    --hash=sha256:%s\n' "$(sha_of "$T/usb/wh/foo-1.0-py3-none-any.whl")" > "$SB/agents/requirements.lock"
_out="$(cd "$T/usb" && OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/pydeps.sh" audit ./wh 2>&1)"; _rc=$?
if [[ $_rc -eq 0 ]] && has "$_out" "1 file(s) match the lock"; then
  pass "pydeps.sh 'audit ./wh' from another cwd audits the caller's ./wh"
else
  fail "pydeps audit ./wh (rc=$_rc): $_out"
fi
_out="$(cd "$T/usb" && OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/pydeps.sh" install --from 2>&1)"; _rc=$?
if [[ $_rc -ne 0 ]] && has "$_out" "--from needs a wheelhouse"; then
  pass "pydeps.sh: a bare --from is an error, not a silent fall-through to the index install"
else
  fail "bare --from (rc=$_rc): $_out"
fi

# ===========================================================================
echo ""
echo "5. bundle.sh install — weights: .partial + verify; 'exists' is not 'correct':"
# ===========================================================================
WB="$T/usb/wbundle"; WD="$T/wdest"
mk_wbundle() {
  rm -rf "$WB" "$WD"; mkdir -p "$WB/weights" "$WD"
  printf 'GGUF-GOOD-WEIGHT-CONTENT' > "$WB/weights/w.gguf"
  "$REAL_PY" "$SB/scripts/lib/bundle_manifest.py" write "$WB" --built-at t --repo-commit c \
      --component weights:weights >/dev/null
  printf '%s\t%s\t%s\t%s\t%s\n' "$(sha_of "$WB/weights/w.gguf")" 24 w.gguf org/w - > "$SB/scripts/weights.registry"
}
install_w() { _out="$(cd "$T" && PATH="${1:-$T/bin}:$PATH" OPENBEAST_WEIGHTS_DIR="$WD" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" install "$WB" 2>&1)"; _rc=$?; }
reset_box "$CLASSIC"; printf '%s\t%s\n' "$REF_WEB" "$ID_REC" >> "$T/state/ids"

mk_wbundle; install_w
if [[ $_rc -eq 0 ]] && cmp -s "$WB/weights/w.gguf" "$WD/w.gguf" && [[ ! -e "$WD/w.gguf.partial" ]] \
   && has "$_out" "matches the manifest AND scripts/weights.registry"; then
  pass "a fresh weight is copied, verified, renamed into place; no .partial left"
else
  fail "fresh weight install (rc=$_rc): $_out"
fi
# NEGATIVE CONTROL: an identical existing file really is left alone (same inode).
_ino="$(stat -c%i "$WD/w.gguf")"; install_w
if [[ $_rc -eq 0 && "$(stat -c%i "$WD/w.gguf")" == "$_ino" ]] && has "$_out" "matches the bundle — left alone"; then
  pass "negative control: an existing file that MATCHES is left alone (same inode), after being checked"
else
  fail "matching existing weight (rc=$_rc): $_out"
fi
# A TRUNCATED earlier copy: used to be "already in … left alone" forever.
printf 'GGUF-GOOD' > "$WD/w.gguf"; install_w
if [[ $_rc -eq 0 ]] && cmp -s "$WB/weights/w.gguf" "$WD/w.gguf" && has "$_out" "is NOT the file this bundle carries" \
   && has "$_out" "9 bytes, the manifest says 24"; then
  pass "a TRUNCATED existing weight is detected by size, said so, and replaced"
else
  fail "truncated existing weight (rc=$_rc): $_out"
fi
# Same size, different bytes: only the sha can tell.
printf 'GGUF-EVIL-WEIGHT-CONTENT' > "$WD/w.gguf"; install_w
if [[ $_rc -eq 0 ]] && cmp -s "$WB/weights/w.gguf" "$WD/w.gguf" && has "$_out" "is NOT the file this bundle carries"; then
  pass "a same-SIZE wrong weight is detected by sha256 and replaced"
else
  fail "same-size wrong weight (rc=$_rc): $_out"
fi
# ENOSPC mid-copy: a `cp` that writes half, then fails.
mkdir -p "$T/bin_cp"; cat > "$T/bin_cp/cp" <<'STUB'
#!/bin/bash
dest="${@: -1}"
if [[ "$dest" == *.partial ]]; then
  echo "cp ${*: -2}" >> "$OB_STUB_STATE/cp.log"
  printf 'GGUF-GO' > "$dest"; echo "cp: error writing '$dest': No space left on device" >&2; exit 1
fi
exec "$REAL_CP" "$@"
STUB
chmod +x "$T/bin_cp/cp"; : > "$T/state/cp.log"
rm -f "$WD/w.gguf"; install_w "$T/bin_cp:$T/bin"
if [[ $_rc -ne 0 && ! -e "$WD/w.gguf" && ! -e "$WD/w.gguf.partial" && -s "$T/state/cp.log" ]]; then
  pass "a copy that dies half-way leaves NOTHING under the weight's name and no .partial"
else
  fail "ENOSPC copy (rc=$_rc, dir: $(ls -A "$WD")): $_out"
fi
install_w
if [[ $_rc -eq 0 ]] && cmp -s "$WB/weights/w.gguf" "$WD/w.gguf"; then
  pass "...so the re-run simply works (the old code reported the stub as 'already in … left alone')"
else
  fail "re-run after ENOSPC (rc=$_rc): $_out"
fi
# The registry is still a second, independent check — and a loser never lands.
mk_wbundle; printf '%s\t%s\t%s\t%s\t%s\n' "$(printf 'd%.0s' {1..64})" 24 w.gguf org/w - > "$SB/scripts/weights.registry"
printf 'PRE-EXISTING' > "$WD/w.gguf"; install_w
if [[ $_rc -ne 0 && "$(cat "$WD/w.gguf")" == "PRE-EXISTING" && ! -e "$WD/w.gguf.partial" ]] \
   && has "$_out" "does not match the registry sha256"; then
  pass "a weight the REGISTRY rejects is never installed, and what was there is untouched"
else
  fail "registry mismatch (rc=$_rc, have: $(cat "$WD/w.gguf" 2>/dev/null)): $_out"
fi

# ===========================================================================
echo ""
echo "7. bundle.sh — an empty or bare --key is an error, never a downgrade:"
# ===========================================================================
if ! command -v ssh-keygen >/dev/null 2>&1; then
  echo "  SKIP: ssh-keygen not installed"
else
  mk_wbundle
  ssh-keygen -q -t ed25519 -N '' -C test -f "$T/key" >/dev/null 2>&1
  echo "builder $(cat "$T/key.pub")" > "$T/usb/allowed"
  bsh() { _out="$(cd "$T/usb" && PATH="$T/bin:$PATH" OPENBEAST_WEIGHTS_DIR="$WD" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" "$@" 2>&1)"; _rc=$?; }
  bsh sign ./wbundle --key "$T/key"
  [[ $_rc -eq 0 && -f "$WB/MANIFEST.json.sig" ]] && pass "signed the test bundle" || fail "sign (rc=$_rc): $_out"

  # NEGATIVE CONTROL first: the good path works — with a RELATIVE --key (9).
  bsh verify ./wbundle --key ./allowed
  if [[ $_rc -eq 0 ]] && has "$_out" "signature verified"; then
    pass "negative control: a valid key verifies (and a relative --key resolves from the caller's cwd)"
  else
    fail "valid signature (rc=$_rc): $_out"
  fi
  # TAMPER with the manifest only: every file hash still verifies, so the
  # signature is the ONLY thing that can notice.
  sed -i 's/"built_at": "t"/"built_at": "tampered"/' "$WB/MANIFEST.json"
  bsh verify ./wbundle
  if [[ $_rc -eq 0 ]]; then
    pass "control: without --key the tampered bundle verifies rc=0 (so --key is what stands between)"
  else
    fail "control: tampered bundle did not pass integrity-only verify (rc=$_rc) — the case is wrong: $_out"
  fi
  bsh verify ./wbundle --key ./allowed
  if [[ $_rc -ne 0 ]] && has "$_out" "NOT valid"; then
    pass "negative control: a tampered manifest is refused with a valid --key"
  else
    fail "tampered + valid key (rc=$_rc): $_out"
  fi
  _unset=""
  for _cmd in verify install; do
    bsh "$_cmd" ./wbundle --key "$_unset"
    if [[ $_rc -ne 0 ]] && has "$_out" "--key needs a value" && ! has "$_out" "verified"; then
      pass "$_cmd --key \"\" on a TAMPERED signed bundle is an ERROR (was: rc=0 + a warning)"
    else
      fail "$_cmd --key '' (rc=$_rc): $_out"
    fi
    bsh "$_cmd" ./wbundle --key
    if [[ $_rc -ne 0 ]] && has "$_out" "--key needs a value"; then
      pass "$_cmd with a bare trailing --key says so (was: silent exit 1 from 'shift 2')"
    else
      fail "$_cmd bare --key (rc=$_rc): '$_out'"
    fi
  done
  [[ ! -e "$WD/w.gguf" ]] && pass "...and install copied nothing on the way to refusing" \
    || fail "install --key '' still installed a weight"
  bsh verify ./wbundle --key ./allowed --identity
  if [[ $_rc -ne 0 ]] && has "$_out" "--identity needs a value"; then
    pass "a bare --identity is an error too"
  else
    fail "bare --identity (rc=$_rc): $_out"
  fi
  bsh sign ./wbundle --key
  if [[ $_rc -ne 0 ]] && has "$_out" "--key needs a value"; then
    pass "sign with a bare --key is an error with a message"
  else
    fail "sign bare --key (rc=$_rc): $_out"
  fi
  bsh build ./never --with-weights=
  if [[ $_rc -ne 0 && ! -e "$T/usb/never" ]] && has "$_out" "--with-weights= needs"; then
    pass "build --with-weights= (empty) is an error, not a bundle silently built without weights"
  else
    fail "build --with-weights= (rc=$_rc): $_out"
  fi
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
