#!/bin/bash
# beast-ark shell-level checks (scripts/ark.sh and a drive's own ./ark wrapper).
# docs/BEAST_ARK.md. Hermetic: no network, no real downloads.
#
#   1. scripts/ark.sh: help, lint of the real catalog, plan, list, exit codes
#      (2 usage / not-a-drive, 3 offline refusal)
#   2. the drive wrapper on a BARE box: with no python on PATH it unpacks the
#      CPython archived on the drive — after checking it against SHA256SUMS —
#      and REFUSES a tampered one
#
# Run: bash tests/test_ark.sh
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARK="$REPO_DIR/scripts/ark.sh"
PASS=0 FAIL=0
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

ok()   { PASS=$((PASS + 1)); echo "  ok   $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL $1"; }
check() { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }

echo "--- scripts/ark.sh"
out="$(bash "$ARK" --help 2>&1)"; rc=$?
check "--help exits 0 and lists the subcommands" test $rc -eq 0 -a -n "$(echo "$out" | grep -E 'fetch|restore|serve' | head -1)"
out="$(bash "$ARK" lint 2>&1)"; rc=$?
check "the real catalog lints clean" test $rc -eq 0 -a -n "$(echo "$out" | grep '0 problem(s)')"
out="$(bash "$ARK" plan --profile both 2>&1)"; rc=$?
check "plan prints full and core" test $rc -eq 0 -a -n "$(echo "$out" | grep 'profile core')"
out="$(bash "$ARK" list --category languages 2>&1)"; rc=$?
check "list filters by category" test $rc -eq 0 -a -n "$(echo "$out" | grep -E '^ [* ] zig ')"
bash "$ARK" plan --only no-such-entry >/dev/null 2>&1; rc=$?
check "an unknown entry is a usage error (2)" test $rc -eq 2
bash "$ARK" verify "$T/nothing" >/dev/null 2>&1; rc=$?
check "verify of a non-drive is exit 2" test $rc -eq 2
ARK_FORCE_OFFLINE=1 bash "$ARK" fetch --to "$T/d" --only jq >/dev/null 2>"$T/err"; rc=$?
check "fetch refuses offline with exit 3" test $rc -eq 3 -a -n "$(grep 'needs the network' "$T/err")"
check "...and created nothing" test ! -e "$T/d/ark.lock"
out="$(OPENBEAST_PYTHON=/nonexistent bash "$ARK" lint 2>&1)"
check "ark.sh falls back from a bad OPENBEAST_PYTHON to python3" test -n "$(echo "$out" | grep '^lint ')"

echo "--- the drive's ./ark on a box with no python"
REALPY="$(command -v python3)"
D="$T/drive"
mkdir -p "$D/files/cpython-3.14/3.14.10/fake"
# A drive made by fetch (so the wrapper and tool are real), then a stand-in
# "CPython" whose python3 is a shim to the real interpreter.
cat > "$T/catalog.toml" <<'EOF'
[ark]
schema = 1
EOF
"$REALPY" -I "$REPO_DIR/agents/ark/cli.py" --catalog "$T/catalog.toml" fetch --to "$D" >/dev/null 2>&1
check "fetch of an empty catalog still lays out the drive" test -x "$D/ark" -a -f "$D/tool/agents/ark/cli.py"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) plat=linux-x86_64 ;; Linux-aarch64|Linux-arm64) plat=linux-aarch64 ;; *) plat=darwin-arm64 ;;
esac
mkdir -p "$T/py/python/bin" "$D/files/cpython-3.14/3.14.10/$plat"
printf '#!/bin/sh\nexec %s "$@"\n' "$REALPY" > "$T/py/python/bin/python3"
chmod +x "$T/py/python/bin/python3"
tgz="files/cpython-3.14/3.14.10/$plat/cpython-3.14.10+20261110-test-install_only.tar.gz"
tar -czf "$D/$tgz" -C "$T/py" python
if command -v sha256sum >/dev/null 2>&1; then h="$(sha256sum "$D/$tgz" | awk '{print $1}')"; else h="$(shasum -a 256 "$D/$tgz" | awk '{print $1}')"; fi
echo "$h  $tgz" >> "$D/SHA256SUMS"
# a PATH with the POSIX tools the wrapper needs and NO python
mkdir -p "$T/bin"
for t in sh uname ls sort tail grep awk tar gzip mkdir cat dirname sha256sum shasum perl env head; do
  p="$(command -v "$t" 2>/dev/null)" && ln -sf "$p" "$T/bin/$t"
done
# shasum on macOS is a perl script; it needs perl, linked above
HOME="$T/home" PATH="$T/bin" "$T/bin/sh" "$D/ark" verify "$D" >"$T/o" 2>&1; rc=$?
check "with no python, ./ark unpacks the archived CPython and runs" test $rc -eq 0 -a -n "$(grep 'archived CPython' "$T/o")"
check "...into ~/.local/opt/ark-python" test -x "$T/home/.local/opt/ark-python/python/bin/python3"
rm -rf "$T/home"
printf 'tampered' >> "$D/$tgz"
HOME="$T/home" PATH="$T/bin" "$T/bin/sh" "$D/ark" verify "$D" >"$T/o" 2>&1; rc=$?
check "a tampered archived CPython is REFUSED" test $rc -ne 0 -a -n "$(grep 'REFUSED' "$T/o")"
check "...and never unpacked" test ! -e "$T/home/.local/opt/ark-python/python"

echo ""
echo "beast-ark shell tests: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
