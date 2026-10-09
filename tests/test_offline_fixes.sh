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
#  12  verify-weights.sh --file with a name the registry lacks is a failure
#  13  bootstrap.sh      a weight that failed its pin is never accepted later
# (8, Finder droppings, is python: tests/test_bundle_manifest.py and
#  tests/test_pydeps_lock.py.)

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
# HERMETIC: a rig with OPENBEAST_OFFLINE (or _PIP_STRICT, …) exported would
# otherwise steer the scripts under test — 14 failures, measured.
for _v in $(compgen -e | grep '^OPENBEAST_' || true); do unset "$_v"; done

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

# FREE SPACE is checked against the registry's byte size BEFORE the download
# (2026-10-09 review, UX-03): a box 5 GB short used to find out 15 GB in, from
# hf's raw "No space left on device". df is a stub that reports whatever the
# case says (and is the real df when the case says nothing).
REAL_DF="$(command -v df)"; export REAL_DF
cat > "$T/bin/df" <<'STUB'
#!/bin/bash
if [[ -f "$OB_STUB_STATE/df_free_kb" ]]; then
  echo "df $*" >> "$OB_STUB_STATE/df.log"
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nstub 99 0 %s 0%% /stub\n' "$(cat "$OB_STUB_STATE/df_free_kb")"
  exit 0
fi
exec "$REAL_DF" "$@"
STUB
chmod +x "$T/bin/df"
{
  printf '%s\t%s\t%s\t%s\t%s\n' "$_pln_sha" "20000000000"   "big.gguf"   "org/plain-GGUF" "-"
  printf '%s\t%s\t%s\t%s\t%s\n' "$_pln_sha" "${#_pln_body}" "roomy.gguf" "org/plain-GGUF" "-"
} >> "$SB/scripts/weights.registry"
: > "$T/state/hf.log"; : > "$T/state/df.log"; echo 15000000 > "$T/state/df_free_kb"     # ~15 GB free
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" big.gguf 2>&1)"; _rc=$?
if [[ $_rc -ne 0 && ! -s "$T/state/hf.log" && ! -e "$W/.fetch.big.gguf" ]] \
   && has "$_out" "need 20.0 GB in $W, have 15.4 GB free" && has "$_out" "WEIGHTS_DIR" && has "$_out" "openbeast.conf"; then
  pass "a 20 GB weight on a disk with 15 GB free is refused BEFORE the download, naming the directory and WEIGHTS_DIR"
else
  fail "disk check (rc=$_rc, hf called: $(cat "$T/state/hf.log")): $_out"
fi
if has "$(cat "$T/state/df.log")" "$W"; then
  pass "…and the free space measured is the weights directory's own filesystem"
else
  fail "df was not asked about $W: $(cat "$T/state/df.log")"
fi
# A resumed download does not need what its stage already holds: 20 GB wanted,
# ~15 GB free, but ~6 GB of it is already on disk in the stage.
mkdir -p "$W/.fetch.big.gguf"
printf '#!/bin/bash\nif [[ "$1" == "-sk" ]]; then printf "6000000\\t%%s\\n" "$2"; exit 0; fi\nexec /usr/bin/du "$@"\n' > "$T/bin/du"; chmod +x "$T/bin/du"
: > "$T/state/hf.log"
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" big.gguf 2>&1)"; _rc=$?
if [[ -s "$T/state/hf.log" ]] && ! has "$_out" "not enough disk"; then
  pass "a partial already in the stage counts toward the need (a resume is not refused for space it already used)"
else
  fail "resume vs disk check (rc=$_rc): $_out"
fi
rm -f "$T/bin/du"; rm -rf "$W/.fetch.big.gguf" "$W/big.gguf"
# NEGATIVE CONTROL: enough room -> downloaded and verified as before.
echo 1000 > "$T/state/df_free_kb"; : > "$T/state/hf.log"
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" roomy.gguf 2>&1)"; _rc=$?
if [[ $_rc -eq 0 && -f "$W/roomy.gguf" ]] && ! has "$_out" "not enough disk"; then
  pass "negative control: with room for it the weight is downloaded and verified"
else
  fail "disk check refused a weight that fits (rc=$_rc): $_out"
fi
# ...and a df that cannot answer never blocks a download.
printf '#!/bin/bash\nexit 1\n' > "$T/bin/df"; rm -f "$W/roomy.gguf"
_out="$(PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" roomy.gguf 2>&1)"; _rc=$?
if [[ $_rc -eq 0 && -f "$W/roomy.gguf" ]]; then
  pass "negative control: an unmeasurable disk does not block the download"
else
  fail "unmeasurable disk (rc=$_rc): $_out"
fi
rm -f "$T/bin/df" "$T/state/df_free_kb" "$W/roomy.gguf"

# ===========================================================================
echo ""
echo "12. verify-weights.sh --file NAME: a name with no registry row is a FAILURE:"
# ===========================================================================
# The sideload instructions tell operators to run exactly this. A wrong-case
# name used to hash nothing and print "0 failure(s)" with rc=0.
cp "$W/plain.gguf" "$W/PLAIN.GGUF"
_out="$("$SB/scripts/verify-weights.sh" --file PLAIN.GGUF 2>&1)"; _rc=$?
if [[ $_rc -ne 0 ]] && has "$_out" "NOT IN REGISTRY" && has "$_out" "Did you mean: plain.gguf" \
   && ! has "$_out" "0 failure(s)"; then
  pass "--file with a name the registry does not know exits non-zero (and suggests the case-fixed name)"
else
  fail "--file unknown name (rc=$_rc): $_out"
fi
_out="$("$SB/scripts/verify-weights.sh" --file no-such-model.gguf 2>&1)"; _rc=$?
if [[ $_rc -ne 0 ]] && has "$_out" "NOT IN REGISTRY" && ! has "$_out" "Did you mean"; then
  pass "--file with a name nothing resembles also exits non-zero"
else
  fail "--file unrelated name (rc=$_rc): $_out"
fi
# NEGATIVE CONTROL: the right name still verifies, rc=0, and really hashed.
_out="$("$SB/scripts/verify-weights.sh" --file plain.gguf 2>&1)"; _rc=$?
if [[ $_rc -eq 0 ]] && has "$_out" "OK       plain.gguf (size + sha256)" && has "$_out" "Verified 1 file(s)"; then
  pass "negative control: --file with the registry's name deep-verifies and exits 0"
else
  fail "--file known name (rc=$_rc): $_out"
fi
rm -f "$W/PLAIN.GGUF"
unset OPENBEAST_WEIGHTS_DIR

# ===========================================================================
echo ""
echo "13. bootstrap.sh — a weight that FAILED its pin is never accepted on a re-run:"
# ===========================================================================
# bootstrap.sh cannot be run whole; its weight step is lifted out between its
# own section markers, like the python step in 2+3. The stub hf (section 1)
# writes BYTES-FROM-<repo>, so the registry row decides pass or fail.
_wsec="$(sed -n '/^# ---- 4\. default model weight/,/^# ---- executable bits/p' "$REPO_DIR/bootstrap.sh" | sed '$d')"
if has "$_wsec" "WEIGHT_FILE=" && has "$_wsec" "weights.registry"; then
  pass "extracted bootstrap's weight step ($(wc -l <<< "$_wsec") lines)"
else
  fail "could not extract bootstrap's weight step — its section markers moved"
fi
{
  echo 'set -euo pipefail'
  echo 'step() { echo "==> $*"; }; ok() { echo "OK: $*"; }; warn() { echo "WARN: $*"; }'
  echo 'die() { echo "DIE: $*" >&2; exit 1; }; ob_offline() { return 1; }'
  echo "$_wsec"
  echo 'echo HARNESS-REACHED-END'
} > "$T/bootstrap_weight_step.sh"
_DW="Qwen3.8-27B-Uncensored-Q5_K_M.gguf"
_dw_body='BYTES-FROM-JonathanColetti/Qwen3.8-27B-Uncensored-GGUF'
_dw_sha="$(printf '%s' "$_dw_body" | sha256sum | awk '{print $1}')"
_dw_reg="$(cat "$SB/scripts/weights.registry")"
pin_default() {          # pin_default <sha>: the registry row for the default weight
  { printf '%s\n' "$_dw_reg"
    printf '%s\t%s\t%s\t%s\t%s\n' "$1" "${#_dw_body}" "$_DW" "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF" "-"
  } > "$SB/scripts/weights.registry"
}
WB13="$T/weights13"
run_wstep() { : > "$T/state/hf.log"; _out="$(env PATH="$T/bin:$PATH" REPO_DIR="$SB" OPENBEAST_WEIGHTS_DIR="$WB13" bash "$T/bootstrap_weight_step.sh" 2>&1)"; _rc=$?; }
n_hf() { count_lines "$T/state/hf.log" "local-dir="; }

# The upstream file was swapped: same size, different sha256.
rm -rf "$WB13"; pin_default "$(printf 'f%.0s' {1..64})"
run_wstep
if [[ $_rc -ne 0 && ! -e "$WB13/$_DW" && "$(n_hf)" == "1" ]] && has "$_out" "CHECKSUM MISMATCH"; then
  pass "a download that fails its pin dies and leaves NOTHING under the weight's name"
else
  fail "mismatched download (rc=$_rc, hf=$(n_hf), dir: $(ls -A "$WB13" 2>/dev/null)): $_out"
fi
# The re-run: this is where the old code printed "already downloaded", rc=0.
run_wstep
if [[ $_rc -ne 0 && ! -e "$WB13/$_DW" ]] && ! has "$_out" "already downloaded" && ! has "$_out" "HARNESS-REACHED-END"; then
  pass "...and the RE-RUN refuses again instead of accepting it as 'already downloaded'"
else
  fail "re-run after a mismatch (rc=$_rc): $_out"
fi
# A same-size wrong file already under the name (a pre-fix leftover, or a
# hand copy): hashed, refused, and NOT deleted (it may be the operator's).
mkdir -p "$WB13"; printf '%s' "${_dw_body//B/X}" > "$WB13/$_DW"
pin_default "$_dw_sha"
run_wstep
if [[ $_rc -ne 0 && -f "$WB13/$_DW" && "$(n_hf)" == "0" ]] && has "$_out" "NOT the weight OpenBeast pinned" \
   && has "$_out" "sha256 mismatch"; then
  pass "an existing file with the right name but the wrong bytes is REFUSED (hashed, not trusted by name)"
else
  fail "existing wrong weight (rc=$_rc, hf=$(n_hf)): $_out"
fi
# NEGATIVE CONTROL: a download that matches its pin lands, and the re-run
# verifies it without downloading again.
rm -rf "$WB13"
run_wstep
if [[ $_rc -eq 0 && "$(cat "$WB13/$_DW" 2>/dev/null)" == "$_dw_body" && "$(n_hf)" == "1" ]] \
   && has "$_out" "HARNESS-REACHED-END"; then
  pass "negative control: a download that matches its pin lands under its name"
else
  fail "good download (rc=$_rc, hf=$(n_hf)): $_out"
fi
run_wstep
if [[ $_rc -eq 0 && "$(n_hf)" == "0" ]] && has "$_out" "already downloaded, sha256 verified"; then
  pass "negative control: the re-run hashes the existing weight, accepts it, and does not re-download"
else
  fail "re-run on a good weight (rc=$_rc, hf=$(n_hf)): $_out"
fi
printf '%s\n' "$_dw_reg" > "$SB/scripts/weights.registry"

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
          novenvfail)
            # pip's OWN status 3 (status_codes.VIRTUALENV_NOT_FOUND) under
            # PIP_REQUIRE_VIRTUALENV=1 — no bytes were compared.
            echo "ERROR: Could not find an activated virtualenv (required)." >&2
            exit 3 ;;
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

# A NON-hash failure STOPS too, by default (2026-10-09 review, supply S2). "No
# matching distribution" is what a mirror that OMITS one locked file produces,
# and the old default answered it with the unpinned install from that mirror.
run_step compatfail
if [[ $_rc -ne 0 && "$(n_locked)" == "1" && "$(n_unpinned)" == "0" ]] && has "$_out" "OPENBEAST_PIP_STRICT=0" \
   && has "$_out" "No matching distribution" && ! has "$_out" "HARNESS-REACHED-END" && ! has "$_out" "HASH MISMATCH"; then
  pass "'No matching distribution' stops by default: no unpinned install, pip's report shown, the opt-out named"
else
  fail "non-hash failure fell back without being asked (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
run_step compatfail OPENBEAST_PIP_STRICT=1
if [[ $_rc -ne 0 && "$(n_unpinned)" == "0" ]]; then
  pass "OPENBEAST_PIP_STRICT=1 is the same as the default"
else
  fail "STRICT=1 semantics changed (rc=$_rc unpinned=$(n_unpinned)): $_out"
fi
# NEGATIVE CONTROL: the fallback still exists, behind an explicit opt-in.
run_step compatfail OPENBEAST_PIP_STRICT=0
if [[ $_rc -eq 0 && "$(n_locked)" == "1" && "$(n_unpinned)" == "1" ]] && has "$_out" "falling back" \
   && has "$_out" "HARNESS-REACHED-END" && ! has "$_out" "HASH MISMATCH"; then
  pass "negative control: OPENBEAST_PIP_STRICT=0 falls back to requirements.txt, loudly"
else
  fail "opt-in fallback (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
# ...and so does an INCOMPLETE closure, which pip words with "hashes"/"pinned"
# but which compares no bytes at all — it must not be mistaken for tampering.
run_step unpinnedfail OPENBEAST_PIP_STRICT=0
if [[ $_rc -eq 0 && "$(n_unpinned)" == "1" ]] && ! has "$_out" "HASH MISMATCH"; then
  pass "negative control: an incomplete closure ('must have their versions pinned') is not called tampering"
else
  fail "incomplete-closure case (rc=$_rc): $_out"
fi
# The opt-in never covers the one failure the lock exists to catch.
run_step hashfail OPENBEAST_PIP_STRICT=0
if [[ $_rc -ne 0 && "$(n_unpinned)" == "0" ]] && has "$_out" "HASH MISMATCH"; then
  pass "a HASH MISMATCH is fatal even under OPENBEAST_PIP_STRICT=0"
else
  fail "hash mismatch under STRICT=0 (rc=$_rc unpinned=$(n_unpinned)): $_out"
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
run_step ok OPENBEAST_PIP_STRICT=0
if [[ $_rc -eq 0 && "$(n_locked)" == "0" && "$(n_unpinned)" == "1" ]] && has "$_out" "STALE" \
   && has "$_out" "pins 2.0, the lock pins 1.0"; then
  pass "a STALE lock is never installed from: warned (with pydeps' reason); requirements.txt used under the opt-in"
else
  fail "stale lock (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
run_step ok
if [[ $_rc -ne 0 && "$(n_locked)" == "0" && "$(n_unpinned)" == "0" ]] && has "$_out" "does not match" \
   && has "$_out" "pins 2.0, the lock pins 1.0" && has "$_out" "OPENBEAST_PIP_STRICT=0"; then
  pass "a stale lock dies by default, before pip is run at all, with pydeps' reason and the opt-out"
else
  fail "stale, default (rc=$_rc locked=$(n_locked) unpinned=$(n_unpinned)): $_out"
fi
# A checkout with NO lock was the one road to the unpinned install left open.
mv "$SB/agents/requirements.lock" "$T/lock.aside"
run_step ok
if [[ $_rc -ne 0 && "$(n_unpinned)" == "0" ]] && has "$_out" "requirements.lock is missing"; then
  pass "a missing lock stops by default too, naming the file to restore"
else
  fail "missing lock (rc=$_rc unpinned=$(n_unpinned)): $_out"
fi
mv "$T/lock.aside" "$SB/agents/requirements.lock"
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
  # A pin the old hand-kept list did not know about (httpx), with the comment
  # that explains it: both must survive the rewrite.
  printf 'openai==1.0\n# httpx: direct runtime dep of router.py — keep\nhttpx==0.28.1\n' > "$SBU/agents/requirements.txt"
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
# The rewrite carries EVERY pin in the file forward, comments included — it
# used to regenerate the file from a five-name list, dropping httpx.
if grep -qx 'httpx==9.9.1' "$SBU/agents/requirements.txt" && grep -qx 'openai==9.9.1' "$SBU/agents/requirements.txt" \
   && grep -q '^# httpx: direct runtime dep' "$SBU/agents/requirements.txt"; then
  pass "a pin the old list never knew (httpx) is bumped in place, and its comment survives"
else
  fail "requirements.txt after --python: $(tr '\n' '|' < "$SBU/agents/requirements.txt")"
fi
touch "$T/state/lock_fails"; run_update_python; rm -f "$T/state/lock_fails"
if [[ $_rc -eq 0 ]] && has "$_out" "could NOT regenerate" && has "$_out" "./scripts/pydeps.sh lock"; then
  pass "when the relock fails it says the lock is now stale and prints the exact command"
else
  fail "relock-failure path (rc=$_rc): $_out"
fi
# NEGATIVE CONTROL: a bump that breaks our imports rolls back and never relocks.
touch "$T/state/import_breaks"; run_update_python; rm -f "$T/state/import_breaks"
if [[ $_rc -ne 0 && ! -s "$T/state/pydeps.log" ]] \
   && [[ "$(cat "$SBU/agents/requirements.txt")" == "$(printf 'openai==1.0\n# httpx: direct runtime dep of router.py — keep\nhttpx==0.28.1')" ]]; then
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
  info)    cat "$S/store_status"
           # A MEDIUM THAT ANSWERS TWICE: when armed, swap the file on the
           # "stick" right after install's verify pass (info is the first
           # thing install asks docker, before any load).
           if [[ -f "$S/swap_on_info" ]]; then
             cp "$S/swap_src" "$(cat "$S/swap_on_info")"; rm -f "$S/swap_on_info"
           fi ;;
  load)    cat > "$S/last_load"
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
if [[ "$(gzip -dc "$B/images/img.tar.gz")" == "$(cat "$T/state/last_load")" ]]; then
  pass "control: docker load received exactly the bundle's image bytes"
else
  fail "control: the stub did not record the loaded bytes: $(cat "$T/state/last_load")"
fi

# THE INSTALL-TIME HASH GATE: a payload modified after the build must stop
# install before ANYTHING is consumed — and it must be the verify gate that
# stops it (its words), not a later, partial check.
mk_bundle "$B" containerd "$REF_WEB" "$ID_REC"; reset_box "$CONTAINERD"
printf '%s\t%s\n' "$ID_REC" "$ID_REC" > "$T/state/load_adds"
echo "Loaded image ID: $ID_REC" > "$T/state/load_says"
echo "TAMPERED IMAGE" | gzip -n > "$B/images/img.tar.gz"
install_bundle "$B"
if [[ $_rc -ne 0 && "$(compose_web)" == "$REF_WEB" ]] && has "$_out" "does not match its manifest" \
   && [[ "$(count_lines "$T/state/docker.log" "docker load")" == "0" ]]; then
  pass "a payload changed after the build is refused by install's verify gate; nothing loaded, compose untouched"
else
  fail "tampered payload (rc=$_rc loads=$(count_lines "$T/state/docker.log" "docker load")): $_out"
fi

# TOCTOU: the medium answers the verify pass with the right bytes and the
# NEXT read with other bytes. install used to `gzip -dc` the file on the
# medium after verifying it, so the second answer was what got loaded.
mk_bundle "$B" containerd "$REF_WEB" "$ID_REC"; reset_box "$CONTAINERD"
printf '%s\t%s\n' "$ID_REC" "$ID_REC" > "$T/state/load_adds"
echo "Loaded image ID: $ID_REC" > "$T/state/load_says"
echo "EVIL IMAGE BYTES" | gzip -n > "$T/state/swap_src"
echo "$B/images/img.tar.gz" > "$T/state/swap_on_info"; : > "$T/state/last_load"
mkdir -p "$T/tmpx"          # a private TMPDIR, so the cleanup check below sees only this run
_out="$(cd "$T" && TMPDIR="$T/tmpx" PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" install "$B" 2>&1)"; _rc=$?
if [[ $_rc -ne 0 && ! -s "$T/state/last_load" && "$(compose_web)" == "$REF_WEB" ]] \
   && [[ "$(count_lines "$T/state/docker.log" "docker load")" == "0" ]] && has "$_out" "changed after it was verified"; then
  pass "an image swapped on the medium AFTER verification is caught on the private copy; nothing is loaded"
else
  fail "image TOCTOU (rc=$_rc, loaded: $(cat "$T/state/last_load")): $_out"
fi
rm -f "$T/state/swap_on_info"
if [[ -z "$(ls -A "$T/tmpx")" ]]; then
  pass "the private staging dir is removed on the way out, even on a refusal"
else
  fail "private staging dir left behind: $(ls -A "$T/tmpx")"
fi

# ...and the same for the llama.cpp SOURCE tarball, which bootstrap then
# compiles and runs. The swap happens when install makes its extraction dir,
# i.e. after verification and immediately before extraction.
SRCB="$T/usb/srcbundle"; _commit="$(printf 'ab%.0s' {1..20})"
mk_srcbundle() {
  rm -rf "$SRCB" "$T/srcgood"; mkdir -p "$SRCB/source" "$T/srcgood/llama.cpp"
  echo "real source" > "$T/srcgood/llama.cpp/GOOD.txt"
  tar -czf "$SRCB/source/llama.cpp-${_commit:0:12}.tar.gz" -C "$T/srcgood" llama.cpp
  "$REAL_PY" "$SB/scripts/lib/bundle_manifest.py" write "$SRCB" --built-at t --repo-commit c \
      --component source:source --meta "source:{\"commit\": \"$_commit\"}" >/dev/null
  rm -rf "$T/srcevil"; mkdir -p "$T/srcevil/llama.cpp"; echo "attacker source" > "$T/srcevil/llama.cpp/EVIL.txt"
  tar -czf "$T/state/swap_src" -C "$T/srcevil" llama.cpp
}
mkdir -p "$T/bin_mt"; cat > "$T/bin_mt/mktemp" <<'STUB'
#!/bin/bash
if [[ "$*" == *ob-src-* && -f "$OB_STUB_STATE/swap_on_mktemp" ]]; then
  cp "$OB_STUB_STATE/swap_src" "$(cat "$OB_STUB_STATE/swap_on_mktemp")"; rm -f "$OB_STUB_STATE/swap_on_mktemp"
fi
exec "$REAL_MKTEMP" "$@"
STUB
chmod +x "$T/bin_mt/mktemp"; REAL_MKTEMP="$(command -v mktemp)"; export REAL_MKTEMP
mk_srcbundle; rm -rf "$SB/llama.cpp"
echo "$SRCB/source/llama.cpp-${_commit:0:12}.tar.gz" > "$T/state/swap_on_mktemp"
_out="$(cd "$T" && PATH="$T/bin_mt:$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" install "$SRCB" 2>&1)"; _rc=$?
if [[ -f "$SB/llama.cpp/GOOD.txt" && ! -e "$SB/llama.cpp/EVIL.txt" && ! -f "$T/state/swap_on_mktemp" ]]; then
  pass "a source tarball swapped on the medium after verification is NOT what gets extracted (the verified copy is)"
else
  fail "source TOCTOU (rc=$_rc, tree: $(ls "$SB/llama.cpp" 2>/dev/null), swap fired: $([[ -f "$T/state/swap_on_mktemp" ]] && echo no || echo yes)): $_out"
fi
# NEGATIVE CONTROL: an honest source bundle still extracts.
rm -rf "$SB/llama.cpp"; mk_srcbundle
_out="$(cd "$T" && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SB/scripts/bundle.sh" install "$SRCB" 2>&1)"; _rc=$?
if [[ $_rc -eq 0 && -f "$SB/llama.cpp/GOOD.txt" ]] && has "$_out" "extracted llama.cpp-${_commit:0:12}.tar.gz"; then
  pass "negative control: an untouched source bundle extracts as before"
else
  fail "honest source bundle (rc=$_rc): $_out"
fi
rm -rf "$SB/llama.cpp" "$SRCB"

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

# A STALE .pre-bundle. It is written once and never refreshed; the previous-id
# lookup used the LINE INDEX in it, so once compose gained a service above
# `web` (a git pull), a second bundle rewrote TWO services to one image and
# still printed "every image resolves locally", rc=0.
ID_NEW="sha256:$(printf '7%.0s' {1..64})"
mk_bundle "$B" classic "$REF_WEB" "$ID_REC"; reset_box "$CLASSIC"
printf '%s\t%s\n' "$ID_REC" "$ID_REC" > "$T/state/load_adds"
echo "Loaded image ID: $ID_REC" > "$T/state/load_says"
install_bundle "$B"                                   # bundle 1: web -> ID_REC
REF_CACHE="reg.example/cache:1@sha256:$(printf 'c%.0s' {1..64})"
printf 'services:\n  cache:\n    image: %s\n  web:\n    image: %s\n  search:\n    image: %s\n' \
  "$REF_CACHE" "$ID_REC" "$REF_SEARCH" > "$SB/docker-compose.yml"   # compose moved on
printf '%s\t%s\n' "$REF_CACHE" "sha256:$(printf 'd%.0s' {1..64})" >> "$T/state/ids"
mk_bundle "$B" classic "$REF_WEB" "$ID_NEW"
printf '%s\t%s\n' "$ID_NEW" "$ID_NEW" > "$T/state/load_adds"
echo "Loaded image ID: $ID_NEW" > "$T/state/load_says"
install_bundle "$B"                                   # bundle 2: web -> ID_NEW
_cache_now="$(sed -n '/^  cache:/,/^  web:/s/^ *image: *//p' "$SB/docker-compose.yml")"
if [[ $_rc -eq 0 && "$(compose_web)" == "$ID_NEW" && "$_cache_now" == "$REF_CACHE" ]]; then
  pass "a second bundle rewrites ITS service by name — a service added above it is untouched"
else
  fail "stale .pre-bundle (rc=$_rc web=$(compose_web) cache=$_cache_now): $_out"
fi
# ...and a RE-RUN of the same bundle is the documented recovery path, not
# "image pins differ".
install_bundle "$B"
if [[ $_rc -eq 0 && "$(compose_web)" == "$ID_NEW" && "$_cache_now" == "$REF_CACHE" ]] \
   && ! has "$_out" "image pins differ"; then
  pass "re-running an install is idempotent (control: compose unchanged)"
else
  fail "re-run of an installed bundle (rc=$_rc): $_out"
fi
# --identity with no --key would be silently ignored: same family as --key "".
install_bundle "$B" --identity someone@example.com
if [[ $_rc -ne 0 ]] && has "$_out" "--identity needs --key"; then
  pass "--identity without --key is an error, not an ignored flag"
else
  fail "--identity without --key (rc=$_rc): $_out"
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

# EXTENSION FRAGMENTS (extensions/*/compose.yaml — the ntfy push server):
# pinned by digest like the core, and the bundle used to read the core file
# only, so an offline box could never enable ntfy. Build now carries every
# fragment's image, and install rewrites the fragment that runs it.
REF_NT="reg.example/ntfy:v2.28.0@sha256:$(printf '4%.0s' {1..64})"
ID_NT="sha256:$(printf 'f%.0s' {1..64})"
mkdir -p "$SBB/extensions/ntfy"
printf 'services:\n  ntfy:\n    image: %s\n' "$REF_NT" > "$SBB/extensions/ntfy/compose.yaml"
reset_box "$CONTAINERD"
printf '%s\t%s\n%s\t%s\n' "$REF_WEB" "$ID_REC" "$REF_NT" "$ID_NT" >> "$T/state/ids"
_out="$(cd "$T/usb" && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" "$SBB/scripts/bundle.sh" build "$T/usb/out-ext" --no-source 2>&1)"; _rc=$?
_refs="$("$REAL_PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(" ".join(sorted(i["ref"] for c in d["components"] if c["kind"]=="images" for i in c["images"])))' "$T/usb/out-ext/MANIFEST.json" 2>&1)"
if [[ $_rc -eq 0 && "$_refs" == *"$REF_NT"* && "$_refs" == *"$REF_WEB"* ]]; then
  pass "build carries an extension fragment's digest-pinned image beside the core's"
else
  fail "extension image in the bundle (rc=$_rc refs='$_refs'): $_out"
fi
rm -rf "$SBB/extensions" "$T/usb/out-ext"
# Install: the fragment that runs the image is rewritten (and backed up); the
# core compose, which does not run it, is left alone.
mk_bundle "$B" containerd "$REF_NT" "$ID_NT"; reset_box "$CONTAINERD"
printf '%s\t%s\n' "$ID_NT" "$ID_NT" > "$T/state/load_adds"
echo "Loaded image ID: $ID_NT" > "$T/state/load_says"
printf '%s\t%s\n' "$REF_WEB" "$ID_REC" >> "$T/state/ids"     # the core is present
mkdir -p "$SB/extensions/ntfy" "$SB/extensions/idle"
printf 'services:\n  ntfy:\n    # pinned: %s\n    image: %s\n' "$REF_NT" "$REF_NT" > "$SB/extensions/ntfy/compose.yaml"
# A DISABLED extension whose image this box lacks: not start.sh's problem.
printf 'services:\n  idle:\n    image: reg.example/idle@sha256:%s\n' "$(printf '5%.0s' {1..64})" > "$SB/extensions/idle/compose.yaml"
install_bundle "$B"
if [[ $_rc -eq 0 ]] && has "$_out" "every image docker-compose.yml references resolves locally" \
   && has "$_out" "extensions/ntfy/compose.yaml now reference(s) images by CONTENT ID"; then
  pass "…a disabled extension's absent image is not reported missing; the rewrite warning names the fragment"
else
  fail "disabled-extension missing check (rc=$_rc): $_out"
fi
if [[ $_rc -eq 0 ]] && grep -qx "    image: $ID_NT" "$SB/extensions/ntfy/compose.yaml" \
   && grep -qx "    # pinned: $REF_NT" "$SB/extensions/ntfy/compose.yaml" \
   && grep -qx "    image: $REF_NT" "$SB/extensions/ntfy/compose.yaml.pre-bundle" \
   && [[ "$(compose_web)" == "$REF_WEB" && ! -e "$SB/docker-compose.yml.pre-bundle" ]]; then
  pass "install rewrites the extension fragment that runs the image (backup kept); the core compose is untouched"
else
  fail "extension install (rc=$_rc): $(tr '\n' '|' < "$SB/extensions/ntfy/compose.yaml") :: $_out"
fi
# ...but an ENABLED extension whose image is absent is (the control).
_out="$(cd "$T" && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$REAL_PY" OPENBEAST_EXTENSIONS=idle "$SB/scripts/bundle.sh" install "$B" 2>&1)"; _rc=$?
if [[ $_rc -ne 0 ]] && has "$_out" "reg.example/idle@sha256:"; then
  pass "…an ENABLED extension's absent image is reported missing (control)"
else
  fail "enabled-extension missing check (rc=$_rc): $_out"
fi
# NEGATIVE CONTROL: with no fragment running it, the same image is the LOUD
# "no image: line" refusal, as for any image this checkout does not run.
rm -rf "$SB/extensions"
mk_bundle "$B" containerd "$REF_NT" "$ID_NT"; reset_box "$CONTAINERD"
printf '%s\t%s\n' "$ID_NT" "$ID_NT" > "$T/state/load_adds"
echo "Loaded image ID: $ID_NT" > "$T/state/load_says"
install_bundle "$B"
if [[ $_rc -ne 0 ]] && has "$_out" "has no \`image:\` line for it"; then
  pass "negative control: without the fragment, an extension image is refused as unmatched"
else
  fail "no-fragment control (rc=$_rc): $_out"
fi

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
echo "14. pydeps.sh install --from — the LOCK must be vouched for, not carried:"
# ===========================================================================
# The attack: the stick carries a malicious wheel AND a lock naming its hash,
# and the operator copies both over (the old printed instructions said to).
# verify + audit + --require-hashes all passed, because every check was
# against the lock that came with the payload.
PR="$T/pyrepo"; rm -rf "$PR"; mkdir -p "$PR/scripts/lib" "$PR/agents" "$T/usb/wh14"
install -m 755 "$REPO_DIR/scripts/pydeps.sh" "$PR/scripts/pydeps.sh"
install -m 644 "$REPO_DIR/scripts/lib/pydeps_lock.py" "$PR/scripts/lib/pydeps_lock.py"
echo 'foo==1.0' > "$PR/agents/requirements.txt"
echo "GOOD WHEEL" > "$T/usb/wh14/foo-1.0-py3-none-any.whl"
echo "HF WHEEL" > "$T/usb/wh14/huggingface_hub-1.0-py3-none-any.whl"
mk_lock14() {            # mk_lock14 <foo wheel>: a lock that names exactly the wheelhouse
  printf 'foo==1.0 \\\n    --hash=sha256:%s\nhuggingface_hub==1.0 \\\n    --hash=sha256:%s\n' \
    "$(sha_of "$1")" "$(sha_of "$T/usb/wh14/huggingface_hub-1.0-py3-none-any.whl")" > "$PR/agents/requirements.lock"
}
mk_lock14 "$T/usb/wh14/foo-1.0-py3-none-any.whl"
_good_lock_sha="$(sha_of "$PR/agents/requirements.lock")"
pyd() { echo "${PIPMODE:-ok}" > "$T/state/pip_mode"; : > "$T/state/pip.log"
        _out="$(cd "$T/usb" && env OPENBEAST_PYTHON="$T/bin/python3" "$@" 2>&1)"; _rc=$?; }
n_pip() { count_lines "$T/state/pip.log" "pip install"; }

# Not a git checkout, nothing vouched: refused before pip runs.
pyd "$PR/scripts/pydeps.sh" install --from ./wh14
if [[ $_rc -ne 0 && "$(n_pip)" == "0" ]] && has "$_out" "cannot vouch for" && has "$_out" "$_good_lock_sha"; then
  pass "an unvouched lock (no git, no --lock-sha256) is refused before pip runs, and its hash is shown"
else
  fail "unvouched lock (rc=$_rc pip=$(n_pip)): $_out"
fi
pyd "$PR/scripts/pydeps.sh" install --from ./wh14 --lock-sha256 "$(printf '0%.0s' {1..64})"
if [[ $_rc -ne 0 && "$(n_pip)" == "0" ]] && has "$_out" "not the lock you vouched for"; then
  pass "a --lock-sha256 that does not match the lock is refused"
else
  fail "wrong --lock-sha256 (rc=$_rc pip=$(n_pip)): $_out"
fi
# NEGATIVE CONTROLS: the right hash, by flag or by env, installs offline.
pyd "$PR/scripts/pydeps.sh" install --from ./wh14 --lock-sha256 "$_good_lock_sha"
if [[ $_rc -eq 0 && "$(n_pip)" == "1" ]] && [[ "$(count_lines "$T/state/pip.log" "--no-index")" == "1" ]]; then
  pass "negative control: the vouched hash installs, with --no-index"
else
  fail "vouched install (rc=$_rc pip=$(n_pip)): $_out"
fi
pyd OPENBEAST_LOCK_SHA256="$_good_lock_sha" "$PR/scripts/pydeps.sh" install --from ./wh14
if [[ $_rc -eq 0 && "$(n_pip)" == "1" ]]; then
  pass "negative control: OPENBEAST_LOCK_SHA256 vouches the same way (what bootstrap's offline path uses)"
else
  fail "env-vouched install (rc=$_rc): $_out"
fi
# THE GIT CASE: the committed lock vouches for itself; a lock swapped in
# from the stick does not.
if command -v git >/dev/null 2>&1; then
  git -C "$PR" init -q && git -C "$PR" add -A \
    && git -C "$PR" -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -qm init
  pyd "$PR/scripts/pydeps.sh" install --from ./wh14
  if [[ $_rc -eq 0 && "$(n_pip)" == "1" ]] && has "$_out" "committed at"; then
    pass "negative control: in a git checkout, the COMMITTED lock needs no hash"
  else
    fail "committed lock (rc=$_rc pip=$(n_pip)): $_out"
  fi
  # The finding's exact attack: evil wheel + a lock naming it, both from the stick.
  echo "EVIL WHEEL" > "$T/usb/wh14/foo-1.0-py3-none-any.whl"
  mk_lock14 "$T/usb/wh14/foo-1.0-py3-none-any.whl"
  pyd "$PR/scripts/pydeps.sh" install --from ./wh14
  if [[ $_rc -ne 0 && "$(n_pip)" == "0" ]] && has "$_out" "cannot vouch for"; then
    pass "a lock swapped in from the stick (naming a substituted wheel) is REFUSED — the stick is not the trust root"
  else
    fail "swapped lock accepted (rc=$_rc pip=$(n_pip)): $_out"
  fi
  # control: the same pair DOES pass every check the old code ran.
  _out="$("$REAL_PY" "$PR/scripts/lib/pydeps_lock.py" verify --lock "$PR/agents/requirements.lock" \
            --req "$PR/agents/requirements.txt" --extra huggingface_hub 2>&1)"; _rc=$?
  _out2="$("$REAL_PY" "$PR/scripts/lib/pydeps_lock.py" audit --lock "$PR/agents/requirements.lock" --dir "$T/usb/wh14" 2>&1)"; _rc2=$?
  if [[ $_rc -eq 0 && $_rc2 -eq 0 ]]; then
    pass "control: the swapped lock + evil wheel pass verify AND audit, so only the voucher stands between"
  else
    fail "control: the swapped pair did not pass verify/audit (rc=$_rc/$_rc2) — the case is wrong: $_out $_out2"
  fi
  git -C "$PR" checkout -q -- agents/requirements.lock
  echo "GOOD WHEEL" > "$T/usb/wh14/foo-1.0-py3-none-any.whl"
else
  echo "  SKIP: git not installed (the committed-lock cases)"
fi

# EXIT 3 = HASH MISMATCH, the status callers must never fall back from.
PIPMODE=hashfail pyd "$PR/scripts/pydeps.sh" install --from ./wh14 --lock-sha256 "$_good_lock_sha"
if [[ $_rc -eq 3 ]] && has "$_out" "HASH MISMATCH" && has "$_out" "THESE PACKAGES DO NOT MATCH"; then
  pass "a pip hash mismatch exits 3 (pip's own report still shown)"
else
  fail "hash mismatch exit status (rc=$_rc): $_out"
fi
PIPMODE=compatfail pyd "$PR/scripts/pydeps.sh" install --lock-sha256 "$_good_lock_sha"
if [[ $_rc -ne 0 && $_rc -ne 3 ]] && ! has "$_out" "HASH MISMATCH"; then
  pass "negative control: a compatibility failure is non-zero but NOT 3 (a caller may degrade from it)"
else
  fail "compat failure exit status (rc=$_rc): $_out"
fi
PIPMODE=unpinnedfail pyd "$PR/scripts/pydeps.sh" install
if [[ $_rc -ne 0 && $_rc -ne 3 ]]; then
  pass "negative control: an incomplete closure ('must have their versions pinned') is not called tampering"
else
  fail "unpinned closure exit status (rc=$_rc): $_out"
fi
PIPMODE=novenvfail pyd "$PR/scripts/pydeps.sh" install --lock-sha256 "$_good_lock_sha"
if [[ $_rc -ne 0 && $_rc -ne 3 ]] && ! has "$_out" "HASH MISMATCH" && has "$_out" "activated virtualenv"; then
  pass "pip's OWN exit 3 (no virtualenv, PIP_REQUIRE_VIRTUALENV) is not passed through as 'hash mismatch'"
else
  fail "pip exit 3 leaked through as our mismatch status (rc=$_rc): $_out"
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
  # THE CHEAPEST DOWNGRADE: rebuild the manifest over your own payload and
  # DELETE the signature. With --key, authenticity was asked for, so a
  # missing .sig must be a failure — for verify AND install.
  rm -f "$WB/MANIFEST.json.sig"
  for _cmd in verify install; do
    bsh "$_cmd" ./wbundle --key ./allowed
    if [[ $_rc -ne 0 ]] && has "$_out" "carries no MANIFEST.json.sig" && ! has "$_out" "signature verified"; then
      pass "$_cmd --key on a bundle whose signature was DELETED is refused"
    else
      fail "$_cmd --key with no .sig (rc=$_rc): $_out"
    fi
  done
  [[ ! -e "$WD/w.gguf" ]] && pass "...and that install copied nothing" \
    || fail "install --key with no .sig still installed a weight"
  # NEGATIVE CONTROL: without --key the same unsigned bundle is integrity-only
  # (rc=0, said out loud) — so it is --key that makes the missing .sig fatal.
  bsh verify ./wbundle
  if [[ $_rc -eq 0 ]] && has "$_out" "unsigned bundle"; then
    pass "negative control: without --key an unsigned bundle verifies on integrity alone, and says so"
  else
    fail "unsigned verify without --key (rc=$_rc): $_out"
  fi
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

  # --- the lock voucher: only a VERIFIED signature may vouch for the lock ---
  # SB is not a git checkout, so pydeps accepts its lock only with a hash —
  # and bundle.sh must hand one over only when the manifest recording it was
  # signature-checked. (Stub python3: pip is recorded, never run.)
  WHB="$T/usb/whbundle"; rm -rf "$WHB"; mkdir -p "$WHB/wheels" "$WHB/meta"
  cp "$T/usb/wh14/"*.whl "$WHB/wheels/"
  cp "$PR/agents/requirements.lock" "$SB/agents/requirements.lock"
  cp "$PR/agents/requirements.lock" "$WHB/meta/requirements.lock"
  echo 'foo==1.0' > "$SB/agents/requirements.txt"
  "$REAL_PY" "$SB/scripts/lib/bundle_manifest.py" write "$WHB" --built-at t --repo-commit c \
      --component wheels:wheels --component meta:meta >/dev/null
  bsh sign ./whbundle --key "$T/key"
  bshp() { echo ok > "$T/state/pip_mode"; : > "$T/state/pip.log"
           _out="$(cd "$T/usb" && PATH="$T/bin:$PATH" OPENBEAST_PYTHON="$T/bin/python3" "$SB/scripts/bundle.sh" "$@" 2>&1)"; _rc=$?; }
  bshp install ./whbundle --key ./allowed
  if [[ $_rc -eq 0 && "$(n_pip)" == "1" ]] && has "$_out" "signature verified" \
     && has "$_out" "the lock matches the sha256 it was vouched for"; then
    pass "a SIGNED bundle vouches for the lock (its manifest's hash is handed to pydeps)"
  else
    fail "signed wheels bundle (rc=$_rc pip=$(n_pip)): $_out"
  fi
  bshp install ./whbundle
  if [[ $_rc -ne 0 && "$(n_pip)" == "0" ]] && has "$_out" "cannot vouch for"; then
    pass "without --key the same bundle vouches for NOTHING — the stick cannot vouch for itself"
  else
    fail "unsigned-trust wheels install (rc=$_rc pip=$(n_pip)): $_out"
  fi
fi

# ===========================================================================
echo ""
echo "15. bootstrap.sh — llama.cpp is fetched at the PINNED commit:"
# ===========================================================================
# The engine was the one artifact taken as "whatever upstream master is
# today". bootstrap's build step is lifted out between its section markers and
# run against a git stub that records every call and keeps "HEAD" in a file,
# and a cmake stub that "builds" llama-server. Nothing is cloned or compiled.
_lsec="$(sed -n '/^# ---- 2\. build llama\.cpp/,/^# Persist the resolved backend/p' "$REPO_DIR/bootstrap.sh" | sed '$d')"
if has "$_lsec" "llama.cpp.ref" && has "$_lsec" "cmake --build"; then
  pass "extracted bootstrap's llama.cpp step ($(wc -l <<< "$_lsec") lines)"
else
  fail "could not extract bootstrap's llama.cpp step — its section markers moved"
fi
LR="$T/repo_llama"; mkdir -p "$LR/scripts" "$T/binl"
{
  echo 'set -euo pipefail'
  echo 'step() { echo "==> $*"; }; ok() { echo "OK: $*"; }; warn() { echo "WARN: $*"; }'
  echo 'die() { echo "DIE: $*" >&2; exit 1; }; ob_offline() { return 1; }'
  echo 'OB_BACKEND=cpu; GPU_BACKEND=cpu; ob_cmake_flags() { echo ""; }'
  echo "$_lsec"
  echo 'echo HARNESS-REACHED-END'
} > "$T/bootstrap_llama_step.sh"
cat > "$T/binl/git" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"; D="$OB_LLAMA_DIR"
echo "git $*" >> "$S/gitl.log"
case " $* " in
  *" init "*)            mkdir -p "$D/.git" ;;
  *" remote get-url "*)  [[ -f "$D/.git/origin" ]] || exit 1 ;;
  *" remote add "*)      : > "$D/.git/origin" ;;
  *" fetch "*)           [[ ! -f "$S/l_fetch_fails" ]] || { echo "fatal: unable to access: Could not resolve host" >&2; exit 128; }
                         echo "${@: -1}" > "$D/.git/FETCHED" ;;
  *" checkout "*)        # what the remote handed over becomes HEAD (a lying remote hands over l_served)
                         if [[ -f "$S/l_served" ]]; then cp "$S/l_served" "$D/.git/HEADSHA"; else cp "$D/.git/FETCHED" "$D/.git/HEADSHA"; fi
                         : > "$D/CMakeLists.txt" ;;
  *" rev-parse "*)       [[ -f "$D/.git/HEADSHA" ]] || exit 1; cat "$D/.git/HEADSHA" ;;
  *" clone "*)           mkdir -p "$D/.git"; echo "$(printf 'f%.0s' {1..40})" > "$D/.git/HEADSHA"; : > "$D/CMakeLists.txt" ;;
esac
exit 0
STUB
cat > "$T/binl/cmake" <<'STUB'
#!/bin/bash
echo "cmake $*" >> "$OB_STUB_STATE/gitl.log"
if [[ " $* " == *" --build "* ]]; then
  mkdir -p "$OB_LLAMA_DIR/build/bin"; printf '#!/bin/bash\n' > "$OB_LLAMA_DIR/build/bin/llama-server"; chmod +x "$OB_LLAMA_DIR/build/bin/llama-server"
fi
exit 0
STUB
chmod +x "$T/binl/git" "$T/binl/cmake"
_PIN="$(printf '7%.0s' {1..40})"; _OTHER="$(printf '8%.0s' {1..40})"
llama_fresh() { rm -rf "$LR/llama.cpp" "$T/state"/l_*; printf '# why\nLLAMA_CPP_REF=%s\n' "$_PIN" > "$LR/scripts/llama.cpp.ref"; }
run_lstep() { : > "$T/state/gitl.log"; _out="$(env PATH="$T/binl:$PATH" REPO_DIR="$LR" OB_LLAMA_DIR="$LR/llama.cpp" bash "$T/bootstrap_llama_step.sh" 2>&1)"; _rc=$?; }
n_git() { count_lines "$T/state/gitl.log" "$1"; }

llama_fresh; run_lstep
if [[ $_rc -eq 0 && "$(n_git " fetch -q --depth 1 origin $_PIN")" == "1" && "$(n_git " clone ")" == "0" ]] \
   && [[ "$(cat "$LR/llama.cpp/.git/HEADSHA")" == "$_PIN" ]] && has "$_out" "pinned commit ${_PIN:0:12}" \
   && has "$_out" "HARNESS-REACHED-END"; then
  pass "a fresh install fetches exactly the commit in scripts/llama.cpp.ref — never a clone of master"
else
  fail "pinned fetch (rc=$_rc fetch=$(n_git ' fetch ') clone=$(n_git ' clone ')): $_out :: $(cat "$T/state/gitl.log")"
fi
if [[ "$(n_git " checkout -q -B master FETCH_HEAD")" == "1" ]]; then
  pass "…onto a branch named master, so update.sh --llama can pull it (a detached HEAD means 'hand-pinned' there)"
else
  fail "pinned checkout is not on a master branch: $(cat "$T/state/gitl.log")"
fi
# A remote that hands over a DIFFERENT commit than the one asked for.
llama_fresh; echo "$_OTHER" > "$T/state/l_served"; run_lstep
if [[ $_rc -ne 0 && "$(n_git "cmake ")" == "0" ]] && has "$_out" "could not fetch llama.cpp at the pinned commit"; then
  pass "a checkout that is not the pinned commit is fatal BEFORE anything is compiled"
else
  fail "wrong commit served (rc=$_rc cmake=$(n_git 'cmake ')): $_out"
fi
# An interrupted fetch leaves a .git with no commit; the re-run must finish it.
llama_fresh; : > "$T/state/l_fetch_fails"; run_lstep
if [[ $_rc -ne 0 && -d "$LR/llama.cpp/.git" && "$(n_git "cmake ")" == "0" ]] && has "$_out" "Could not resolve host"; then
  pass "a failed fetch dies with git's own message and compiles nothing"
else
  fail "failed fetch (rc=$_rc): $_out"
fi
rm -f "$T/state/l_fetch_fails"; run_lstep
if [[ $_rc -eq 0 && "$(n_git " fetch -q --depth 1 origin $_PIN")" == "1" && "$(n_git " init ")" == "0" ]] \
   && [[ "$(cat "$LR/llama.cpp/.git/HEADSHA")" == "$_PIN" ]]; then
  pass "re-running after that failure finishes the pinned fetch (a commit-less .git is not mistaken for a clone)"
else
  fail "resume after a failed fetch (rc=$_rc): $_out :: $(cat "$T/state/gitl.log")"
fi
# An EXISTING clone somewhere else is somebody's tree: built as it stands.
llama_fresh; mkdir -p "$LR/llama.cpp/.git"; echo "$_OTHER" > "$LR/llama.cpp/.git/HEADSHA"; : > "$LR/llama.cpp/CMakeLists.txt"; run_lstep
if [[ $_rc -eq 0 && "$(n_git " fetch ")" == "0" && "$(n_git " checkout ")" == "0" ]] \
   && has "$_out" "not the pinned ${_PIN:0:12}" && [[ "$(cat "$LR/llama.cpp/.git/HEADSHA")" == "$_OTHER" ]]; then
  pass "an existing clone at another commit is never moved: built as-is, with a warning naming both commits"
else
  fail "existing clone (rc=$_rc): $_out :: $(cat "$T/state/gitl.log")"
fi
# NEGATIVE CONTROL: no usable pin = the old behaviour, and it must be LOUD.
llama_fresh; echo 'LLAMA_CPP_REF=master' > "$LR/scripts/llama.cpp.ref"; run_lstep
if [[ $_rc -eq 0 && "$(n_git " clone --depth 1 ")" == "1" && "$(n_git " fetch ")" == "0" ]] && has "$_out" "NO llama.cpp PIN"; then
  pass "negative control: a pin that is not a 40-hex commit (a branch name) is not used — unpinned clone, loud warning"
else
  fail "unusable pin (rc=$_rc): $_out :: $(cat "$T/state/gitl.log")"
fi
# The committed pin itself: one full commit id, nothing movable.
if [[ "$(grep -cE '^LLAMA_CPP_REF=[0-9a-f]{40}$' "$REPO_DIR/scripts/llama.cpp.ref")" == "1" \
      && "$(grep -c '^LLAMA_CPP_REF=' "$REPO_DIR/scripts/llama.cpp.ref")" == "1" ]]; then
  pass "scripts/llama.cpp.ref commits exactly one 40-hex LLAMA_CPP_REF"
else
  fail "scripts/llama.cpp.ref does not hold exactly one 40-hex LLAMA_CPP_REF"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
