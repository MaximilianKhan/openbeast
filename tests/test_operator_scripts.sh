#!/bin/bash
# Operator scripts — the 2026-10-09 review fixes, each run for real.
#
# Usage: ./tests/test_operator_scripts.sh
#
# Every script runs from a THROWAWAY copy of scripts/ under $TMPDIR with a
# throwaway HOME and stubbed externals (tailscale, sudo, gh, git, cargo…):
# the real openbeast.conf, the real tailnet, GitHub and the GPU are never
# touched, and nothing is installed.
#
#   1  setup-mcpo-keys.sh   --help / unknown options never run it; an EMPTY
#                           key is not "already set"            (UX-10, S7)
#   2  setup-sandlock.sh    private mktemp build dir, cargo --locked   (S8)
#   3  --help               verify-weights, instinct, ext, gpu-lease, bundle,
#                           clients: the whole header, no code   (UX-21/22)

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

echo "=== operator script tests ==="

T="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-operator-test.XXXXXX")"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/home"

# fresh_repo <name> — a sandbox checkout holding only scripts/ (+ lib/).
fresh_repo() {
  local r="$T/$1"
  mkdir -p "$r/scripts/lib"
  cp "$REPO_DIR"/scripts/*.sh "$r/scripts/"
  cp "$REPO_DIR"/scripts/lib/* "$r/scripts/lib/" 2>/dev/null
  printf '%s\n' "$r"
}
# run <cmd…> — sets OUT (stdout+stderr) and RC. No terminal on stdin.
run() { OUT="$("$@" </dev/null 2>&1)"; RC=$?; }
_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
_val() { sed -n "s/^$2=//p" "$1" | tail -n1; }   # _val <conf> <KEY>

# ---------------------------------------------------------------------------
echo ""
echo "1. setup-mcpo-keys.sh (UX-10, supply S7):"
K="$(fresh_repo keys)"
KEYS=(env -i HOME="$T/home" PATH="/usr/bin:/bin" bash "$K/scripts/setup-mcpo-keys.sh")
run "${KEYS[@]}" --help
if [[ $RC -eq 0 ]] && has "$OUT" "--rotate" && has "$OUT" "MCPO_GUEST_KEY" \
   && ! has "$OUT" "set -euo pipefail" && [[ ! -e "$K/openbeast.conf" ]]; then
  pass "--help prints the header and writes nothing (it used to generate both keys)"
else
  fail "--help (rc=$RC, conf exists: $([[ -e "$K/openbeast.conf" ]] && echo yes || echo no)): $OUT"
fi
run "${KEYS[@]}" --rotat
if [[ $RC -eq 2 ]] && has "$OUT" "Unknown option: --rotat" && has "$OUT" "--help" \
   && [[ ! -e "$K/openbeast.conf" ]]; then
  pass "an unknown option is refused (exit 2) before anything is written"
else
  fail "typo'd option (rc=$RC): $OUT"
fi
run "${KEYS[@]}" --with-jwt --bogus
if [[ $RC -eq 2 && ! -e "$K/openbeast.conf" ]]; then
  pass "…even when it follows a valid one"
else
  fail "valid + unknown option (rc=$RC): $OUT"
fi
# The S7 scenario: both lines uncommented from openbeast.conf.example, empty.
printf '# mine\nMCPO_ADMIN_KEY=\n  MCPO_GUEST_KEY = ""   \nOTHER=keep\n' > "$K/openbeast.conf"
run "${KEYS[@]}"
if [[ $RC -eq 0 ]] && [[ "$(_val "$K/openbeast.conf" MCPO_ADMIN_KEY | wc -c)" -eq 65 ]] \
   && [[ "$(_val "$K/openbeast.conf" MCPO_GUEST_KEY | wc -c)" -eq 65 ]] \
   && has "$OUT" "EMPTY" && ! has "$OUT" "already set"; then
  pass "an empty key line is NOT 'already set': both get a generated value"
else
  fail "empty keys (rc=$RC): $OUT :: $(tr '\n' '|' < "$K/openbeast.conf")"
fi
if [[ "$(grep -c 'MCPO_ADMIN_KEY' "$K/openbeast.conf")" == 1 ]] && grep -qx 'OTHER=keep' "$K/openbeast.conf" \
   && grep -qx '# mine' "$K/openbeast.conf" && [[ "$(_mode "$K/openbeast.conf")" == 600 ]]; then
  pass "…written in place (no duplicate line), other lines kept, conf 0600"
else
  fail "conf after filling empty keys: $(tr '\n' '|' < "$K/openbeast.conf")"
fi
# Negative control: real values are left alone without --rotate.
A1="$(_val "$K/openbeast.conf" MCPO_ADMIN_KEY)"
run "${KEYS[@]}"
if [[ $RC -eq 0 ]] && has "$OUT" "MCPO_ADMIN_KEY: already set" \
   && [[ "$(_val "$K/openbeast.conf" MCPO_ADMIN_KEY)" == "$A1" ]]; then
  pass "control: a key that HAS a value is left untouched and reported as set"
else
  fail "re-run clobbered or misreported a real key (rc=$RC): $OUT"
fi

# ---------------------------------------------------------------------------
echo ""
echo "2. setup-sandlock.sh build directory + --locked (supply S8):"
SL="$(fresh_repo sandlock)"
: > "$SL/scripts/sandlock-profile-openbeast.toml"
mkdir -p "$T/slbin" "$T/slhome" "$T/sltmp"
# git: `clone` records what the target directory looked like when it was
# handed over; nothing is fetched. cargo: records argv, "builds" a stub.
cat > "$T/slbin/git" <<EOF
#!/bin/bash
if [[ "\$1" == "clone" ]]; then
  d="\${@: -1}"
  { echo "dir=\$d"; [[ -d "\$d" ]] && echo "preexisting=yes mode=\$(stat -c %a "\$d")" || echo "preexisting=no"; } > "$T/git.log"
  mkdir -p "\$d"
fi
exit 0
EOF
cat > "$T/slbin/cargo" <<EOF
#!/bin/bash
echo "\$*" > "$T/cargo.log"
mkdir -p target/release
cat > target/release/sandlock <<'SB'
#!/bin/bash
case "\$1" in
  --version) echo "sandlock stub" ;;
  run) case "\$*" in *pwned*) exit 1 ;; *) echo sandbox-ok ;; esac ;;
esac
SB
chmod +x target/release/sandlock
EOF
printf '#!/bin/bash\necho "rustc stub"\n' > "$T/slbin/rustc"
printf '#!/bin/bash\necho 6.12.0-test\n' > "$T/slbin/uname"
chmod +x "$T/slbin"/*
run env -i HOME="$T/slhome" TMPDIR="$T/sltmp" PATH="$T/slbin:/usr/bin:/bin" bash "$SL/scripts/setup-sandlock.sh"
if has "$OUT" "Landlock is not in the active LSM list"; then
  # The kernel LSM list is read from /sys and cannot be stubbed; without
  # Landlock the script stops before the build. Fall back to the source.
  echo "  SKIP: this kernel has no Landlock — build path not runnable here"
  if grep -q 'BUILD_DIR="$(mktemp -d ' "$REPO_DIR/scripts/setup-sandlock.sh" \
     && grep -q 'cargo build --release --locked ' "$REPO_DIR/scripts/setup-sandlock.sh"; then
    pass "(static) the build dir comes from mktemp -d and cargo builds --locked"
  else
    fail "(static) setup-sandlock.sh lost mktemp -d or --locked"
  fi
else
  BD="$(sed -n 's/^dir=//p' "$T/git.log" 2>/dev/null)"
  if [[ $RC -eq 0 ]] && grep -q 'preexisting=yes mode=700' "$T/git.log" \
     && [[ "$BD" == "$T/sltmp/"* ]] && ! [[ "$BD" =~ sandlock-build-[0-9]+$ ]]; then
    pass "the clone target is a fresh 0700 mktemp directory, not a guessable /tmp/sandlock-build-\$\$"
  else
    fail "build dir (rc=$RC, $(tr '\n' ' ' < "$T/git.log" 2>/dev/null)): $OUT"
  fi
  if has " $(cat "$T/cargo.log" 2>/dev/null) " " --locked "; then
    pass "cargo builds with --locked (the reviewed commit's Cargo.lock decides the crates)"
  else
    fail "cargo argv: $(cat "$T/cargo.log" 2>/dev/null)"
  fi
  if [[ -x "$T/slhome/.local/bin/sandlock" && -n "$BD" && ! -e "$BD" ]] \
     && [[ -z "$(ls -A "$T/sltmp")" ]]; then
    pass "control: the binary is installed and the build directory is gone afterwards"
  else
    fail "after the build: bin=$([[ -x "$T/slhome/.local/bin/sandlock" ]] && echo yes || echo no) leftovers=$(ls -A "$T/sltmp" | tr '\n' ' ')"
  fi
fi

# ---------------------------------------------------------------------------
echo ""
echo "3. --help prints the whole header, and only the header (UX-21/22):"
# Each of these printed a fixed line range of itself: three leaked code
# (`set -euo pipefail`, SCRIPT_DIR=…), two stopped mid-header, clients.sh gave
# the synopsis alone. Each row names a phrase from the LAST line of the
# script's header — the part a fixed range cut off — so the check fails if
# the help is truncated, and the leak checks fail if it runs past the end.
HP="$(fresh_repo help)"
mkdir -p "$T/helphome"
while IFS='|' read -r _s _last; do
  run env -i HOME="$T/helphome" PATH="/usr/bin:/bin" bash "$HP/scripts/$_s" --help
  _want="$(awk 'NR > 1 && !/^#/ {exit} NR > 1 {sub(/^# ?/, ""); print}' "$REPO_DIR/scripts/$_s")"
  if [[ $RC -eq 0 && "$OUT" == "$_want" ]] && has "$OUT" "$_last" \
     && ! has "$OUT" "set -euo pipefail" && ! has "$OUT" "SCRIPT_DIR=" \
     && ! grep -q '^#' <<< "$OUT" && [[ "$(wc -l <<< "$OUT")" -ge 10 ]]; then
    pass "$_s --help: the full header ($(wc -l <<< "$OUT") lines), no code, no raw '#'"
  else
    fail "$_s --help (rc=$RC, $(wc -l <<< "$OUT") lines): $(head -n 3 <<< "$OUT") … $(tail -n 2 <<< "$OUT")"
  fi
done <<'ROWS'
verify-weights.sh|Exit 1 on any size or hash mismatch.
instinct.sh|never by pattern.
ext.sh|stack is not touched until restart.
gpu-lease.sh|they had nothing to consult.
bundle.sh|leaving a reader to discover it at install time.
clients.sh|never a grant.
ROWS
# --help changed nothing: no conf, no registry, no lease, no bundle.
if [[ ! -e "$HP/openbeast.conf" && ! -e "$HP/.run" && -z "$(ls -A "$T/helphome")" ]]; then
  pass "none of those --help runs wrote anything (no openbeast.conf, no .run/, empty HOME)"
else
  fail "--help left something behind: $(ls -A "$HP" "$T/helphome" | tr '\n' ' ')"
fi
# Negative control: the helper stops at the first non-comment line.
printf '#!/bin/bash\n# one\n#\n#   two\nset -e\n# not help\n' > "$T/usage-fixture.sh"
_got="$(bash -c 'source "$1"; ob_usage "$2"' _ "$REPO_DIR/scripts/lib/usage.sh" "$T/usage-fixture.sh")"
if [[ "$_got" == $'one\n\n  two' ]]; then
  pass "control: ob_usage prints the leading comment block only, markers stripped, indentation kept"
else
  fail "ob_usage fixture: [$_got]"
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
