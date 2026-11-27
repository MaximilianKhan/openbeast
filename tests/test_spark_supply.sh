#!/bin/bash
# The DGX Spark supply chain: install-native.sh (signed native TensorFold)
# and spark-root-setup.sh's offline .debs and Tailscale repo key. Hermetic:
# a throwaway ed25519 key signs fake releases, curl is a stub that serves
# them from a folder, and the .deb pins are checked against fake files.
# Nothing is downloaded or installed outside a scratch HOME.
#   IN_SRC=<file> runs the install-native cases against another copy (e.g.
#   `git show <old>:scripts/backends/tensorfold/install-native.sh`).
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IN_SRC="${IN_SRC:-$REPO_DIR/scripts/backends/tensorfold/install-native.sh}"
RS="$REPO_DIR/scripts/backends/spark-root-setup.sh"
PASS=0 FAIL=0 SKIP=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP: $*"; SKIP=$((SKIP + 1)); }
_has() { grep -qF -- "$2" <<< "$1"; }
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | awk '{print $1}'; }
echo "=== DGX Spark supply-chain tests ==="

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT

# ---------------------------------------------------------------- install-native
# A signer: OpenSSL >= 3, else python3 cryptography.
SSL=""
for o in "${OPENSSL:-}" "$(command -v openssl || true)" /opt/homebrew/opt/openssl@3/bin/openssl /usr/local/opt/openssl@3/bin/openssl; do
  [[ -n "$o" && -x "$o" ]] && "$o" pkeyutl -help 2>&1 | grep -q -- -rawin && { SSL="$o"; break; }
done
PYC=0; python3 -I -c 'import cryptography.hazmat.primitives.asymmetric.ed25519' 2>/dev/null && PYC=1
keygen() {   # keygen <priv.pem> <pub.pem>
  if [[ -n "$SSL" ]]; then
    "$SSL" genpkey -algorithm ed25519 -out "$1" 2>/dev/null && "$SSL" pkey -in "$1" -pubout -out "$2" 2>/dev/null
  else
    python3 -I - "$1" "$2" <<'PY'
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
k = Ed25519PrivateKey.generate()
open(sys.argv[1], "wb").write(k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
open(sys.argv[2], "wb").write(k.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo))
PY
  fi
}
sign() {   # sign <priv.pem> <sha256-file> <sig-out>: the release's signed message
  local m="$T/msg.$$"
  { printf 'tensorfold-native-checksum-v1\n'; cat "$2"; } > "$m"
  if [[ -n "$SSL" ]]; then
    "$SSL" pkeyutl -sign -inkey "$1" -rawin -in "$m" -out "$3"
  else
    python3 -I - "$1" "$m" "$3" <<'PY'
import sys
from cryptography.hazmat.primitives.serialization import load_pem_private_key
k = load_pem_private_key(open(sys.argv[1], "rb").read(), None)
open(sys.argv[3], "wb").write(k.sign(open(sys.argv[2], "rb").read()))
PY
  fi
  rm -f "$m"
}
release() {   # release <dir> <version> <platform> [payload]: archive + .sha256 + .sig
  local d="$1" v="$2" n="tensorfold-$2-$3"
  mkdir -p "$d" "$T/build/$n/bin"
  printf '#!/bin/sh\necho "tensorfold %s %s"\n' "$v" "${4:-genuine}" > "$T/build/$n/bin/tensorfold-native"
  chmod +x "$T/build/$n/bin/tensorfold-native"
  (cd "$T/build/$n" && { sha256sum bin/tensorfold-native 2>/dev/null || shasum -a 256 bin/tensorfold-native; } > SHA256SUMS)
  tar -czf "$d/$n.tar.gz" -C "$T/build" "$n"
  rm -rf "${T:?}/build"
  printf '%s  %s\n' "$(sha "$d/$n.tar.gz")" "$n.tar.gz" > "$d/$n.tar.gz.sha256"
  sign "$T/key.pem" "$d/$n.tar.gz.sha256" "$d/$n.tar.gz.sha256.sig"
}

if [[ -z "$SSL" && $PYC -eq 0 ]]; then
  skip "install-native: no ed25519 signer here (OpenSSL >= 3 or python3 cryptography)"
else
  mkdir -p "$T/tf" "$T/bin"
  cp "$IN_SRC" "$T/tf/install-native.sh"
  keygen "$T/key.pem" "$T/tf/release-key.pem"
  # curl -fsSL -o OUT URL: serve from $T/served/<version>/<file>, and log the URL.
  cat > "$T/bin/curl" <<'S'
#!/bin/bash
out="" url=""
while [[ $# -gt 0 ]]; do case "$1" in -o) out="$2"; shift 2 ;; -*) shift ;; *) url="$1"; shift ;; esac; done
echo "$url" >> "$IN_CURL_LOG"
v="${url%/*}"; v="${v##*/v}"
cp "$IN_SERVED/$v/${url##*/}" "$out" 2>/dev/null || exit 22
S
  chmod +x "$T/bin/curl"
  export IN_CURL_LOG="$T/curl.log" IN_SERVED="$T/served"
  _in() { OUT="$(HOME="$T/home" PATH="$T/bin:$PATH" bash "$T/tf/install-native.sh" "$@" 2>&1)"; RC=$?; }

  release "$T/good" 1.2.7 linux-aarch64
  _in --from "$T/good" --version 1.2.7 --platform linux-aarch64
  [[ $RC -eq 0 ]] && _has "$OUT" "tensorfold 1.2.7 genuine" && [[ -x "$T/home/.local/bin/tensorfold" ]] \
    && pass "a signed release installs from --from" || fail "good --from (rc=$RC): $OUT"

  # The attack: a genuinely signed .sha256 of an OLDER release (with its own
  # archive alongside, so `sha256 -c` passes) presented under the new name,
  # next to an archive nobody signed.
  A="$T/replay"; release "$A" 1.2.6 linux-aarch64
  release "$T/evil" 1.2.7 linux-aarch64 EVIL
  cp "$T/evil/tensorfold-1.2.7-linux-aarch64.tar.gz" "$A/"
  cp "$A/tensorfold-1.2.6-linux-aarch64.tar.gz.sha256" "$A/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256"
  cp "$A/tensorfold-1.2.6-linux-aarch64.tar.gz.sha256.sig" "$A/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256.sig"
  rm -rf "${T:?}/home"; _in --from "$A" --version 1.2.7 --platform linux-aarch64
  [[ $RC -ne 0 ]] && ! _has "$OUT" "EVIL" && [[ ! -e "$T/home/.local/bin/tensorfold" ]] && _has "$OUT" "REFUSED" \
    && pass "an older release's signed .sha256 never vouches for another archive" || fail "replay (rc=$RC): $OUT"

  # Two lines: the right archive's hash plus another — refused even if signed.
  M="$T/multi"; release "$M" 1.2.7 linux-aarch64
  printf '%s  other.tar.gz\n' "$(printf 0%.0s {1..64})" >> "$M/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256"
  sign "$T/key.pem" "$M/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256" "$M/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256.sig"
  rm -rf "${T:?}/home"; _in --from "$M" --version 1.2.7 --platform linux-aarch64
  [[ $RC -ne 0 ]] && _has "$OUT" "exactly one line" && pass "a .sha256 with more than the one archive line is refused" || fail "multi-line (rc=$RC): $OUT"

  B="$T/tampered"; release "$B" 1.2.7 linux-aarch64
  cp "$T/evil/tensorfold-1.2.7-linux-aarch64.tar.gz" "$B/"
  rm -rf "${T:?}/home"; _in --from "$B" --version 1.2.7 --platform linux-aarch64
  [[ $RC -ne 0 ]] && _has "$OUT" "checksum mismatch" && pass "an archive that does not hash to its .sha256 is refused" || fail "tampered (rc=$RC): $OUT"

  S="$T/badsig"; release "$S" 1.2.7 linux-aarch64
  keygen "$T/other.pem" "$T/other.pub"; sign "$T/other.pem" "$S/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256" "$S/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256.sig"
  rm -rf "${T:?}/home"; _in --from "$S" --version 1.2.7 --platform linux-aarch64
  [[ $RC -ne 0 ]] && _has "$OUT" "does NOT verify" && pass "a signature by another key is refused" || fail "bad sig (rc=$RC): $OUT"

  # --stage on any box (the Mac) stages the Spark's platform unless told otherwise,
  # at the installer's default release (the one the ark catalog pins, test_ark.py).
  DV="$(sed -n 's/^VERSION=//p' "$IN_SRC")"
  mkdir -p "$T/served/$DV"; release "$T/served/$DV" "$DV" linux-aarch64
  : > "$IN_CURL_LOG"; _in --stage "$T/staged"
  [[ $RC -eq 0 && -f "$T/staged/tensorfold-$DV-linux-aarch64.tar.gz" ]] && grep -q 'linux-aarch64.tar.gz$' "$IN_CURL_LOG" \
    && ! grep -q macos "$IN_CURL_LOG" && _has "$OUT" "staged linux-aarch64" \
    && pass "--stage with no --platform stages linux-aarch64 (the Spark), verified" || fail "stage (rc=$RC): $OUT"
  rm -rf "${T:?}/home"; _in --from "$T/staged" --platform linux-aarch64
  [[ $RC -eq 0 ]] && pass "the staged folder installs with --from (default release $DV)" || fail "staged --from (rc=$RC): $OUT"

  # Python fallback: an openssl without -rawin (OpenSSL 1.1 / LibreSSL) first on PATH.
  if [[ $PYC -eq 1 && ! -x /opt/homebrew/opt/openssl@3/bin/openssl && ! -x /usr/local/opt/openssl@3/bin/openssl ]]; then
    printf '#!/bin/sh\necho "Usage: pkeyutl [options]"\nexit 1\n' > "$T/bin/openssl"; chmod +x "$T/bin/openssl"
    rm -rf "${T:?}/home"; _in --from "$T/good" --version 1.2.7 --platform linux-aarch64
    [[ $RC -eq 0 ]] && pass "no OpenSSL 3: python cryptography verifies the signature" || fail "python fallback (rc=$RC): $OUT"
    rm -rf "${T:?}/home"; _in --from "$S" --version 1.2.7 --platform linux-aarch64
    [[ $RC -ne 0 ]] && _has "$OUT" "does NOT verify" && pass "the python fallback refuses a bad signature too" || fail "python bad sig (rc=$RC): $OUT"
    rm -f "$T/bin/openssl"
  else
    skip "python-cryptography fallback (needs cryptography and no Homebrew openssl@3 on this box)"
  fi
fi

# ---------------------------------------------------------------- spark-root-setup --debs
# Fake .debs and a copy of the script with the pins pointed at them: the real
# pins are NVIDIA's / Tailscale's repo-index hashes, which no fake can match.
D="$T/debs"; mkdir -p "$D"
printf 'not really libnccl2\n' > "$D/libnccl2_2.28.9-1+cuda13.0_arm64.deb"
printf 'not really tailscale\n' > "$D/tailscale_1.104.7_arm64.deb"
OUT="$(bash "$RS" --check-debs "$D" 2>&1)"; RC=$?
[[ $RC -ne 0 ]] && _has "$OUT" "REFUSED" && _has "$OUT" "pinned" \
  && pass "--check-debs refuses a .deb that is not the pinned bytes (real pins, no root)" || fail "real pins (rc=$RC): $OUT"
sed -e "s/^NCCL_DEB_SHA256=.*/NCCL_DEB_SHA256=\"$(sha "$D/libnccl2_2.28.9-1+cuda13.0_arm64.deb")\"/" \
    -e "s/^TS_DEB_SHA256=.*/TS_DEB_SHA256=\"$(sha "$D/tailscale_1.104.7_arm64.deb")\"/" "$RS" > "$T/rs.sh"
# dpkg-deb would (rightly) reject the fakes' control files: stub it to agree.
mkdir -p "$T/dbin"
cat > "$T/dbin/dpkg-deb" <<'S'
#!/bin/bash
case "$3" in
  Package) case "$2" in *libnccl2_*) echo libnccl2 ;; *tailscale_*) echo tailscale ;; esac ;;
  Version) case "$2" in *libnccl2_*) echo "2.28.9-1+cuda13.0" ;; *tailscale_*) echo "1.104.7" ;; esac ;;
esac
S
chmod +x "$T/dbin/dpkg-deb"
_rs() { OUT="$(PATH="$T/dbin:$PATH" bash "$T/rs.sh" "$@" 2>&1)"; RC=$?; }
_rs --check-debs "$D"
[[ $RC -eq 0 ]] && _has "$OUT" "OK:" && pass "--check-debs accepts the pinned bytes" || fail "pinned ok (rc=$RC): $OUT"
printf 'planted\n' > "$D/tailscale_9.99.0_arm64.deb"
_rs --check-debs "$D"
[[ $RC -eq 0 ]] && pass "a higher-versioned planted tailscale .deb is ignored (exact version, no sort -V)" || fail "planted higher (rc=$RC): $OUT"
rm "$D/tailscale_1.104.7_arm64.deb"
_rs --check-debs "$D"
[[ $RC -ne 0 ]] && _has "$OUT" "apt-get download tailscale=1.104.7" && _has "$OUT" "tailscale_9.99.0" \
  && pass "only another version staged: refused, naming the pinned one" || fail "wrong version (rc=$RC): $OUT"
printf 'not really tailscale\n' > "$D/tailscale_1.104.7_arm64.deb"
printf 'x' >> "$D/libnccl2_2.28.9-1+cuda13.0_arm64.deb"
_rs --check-debs "$D"
[[ $RC -ne 0 ]] && _has "$OUT" "REFUSED" && pass "one changed byte in libnccl2 is refused" || fail "nccl tamper (rc=$RC): $OUT"
rm "$D/libnccl2_2.28.9-1+cuda13.0_arm64.deb"
printf 'not really libnccl2\n' > "$D/libnccl2_2.28.9-1%2bcuda13.0_arm64.deb"
_rs --check-debs "$D"
[[ $RC -eq 0 ]] && pass "apt-get download's %2b spelling of '+' is the same file" || fail "%2b (rc=$RC): $OUT"
OUT="$(bash "$RS" spark-1 --debs "$D" 2>&1)"; RC=$?
if [[ $EUID -ne 0 ]]; then
  [[ $RC -ne 0 ]] && _has "$OUT" "sudo" && pass "the install path still needs root" || fail "non-root install (rc=$RC): $OUT"
fi

# ---------------------------------------------------------------- Tailscale repo key
KR="$REPO_DIR/tests/fixtures/tailscale-archive-keyring.gpg"
if command -v gpg >/dev/null; then
  OUT="$(bash "$RS" --check-keyring "$KR" 2>&1)"; RC=$?
  [[ $RC -eq 0 ]] && _has "$OUT" "2596A99EAAB33821893C0A79458CA832957F5868" \
    && pass "Tailscale's published keyring matches the pinned fingerprint" || fail "real keyring (rc=$RC): $OUT"
  head -c 400 "$KR" > "$T/trunc.gpg"
  OUT="$(bash "$RS" --check-keyring "$T/trunc.gpg" 2>&1)"; RC=$?
  [[ $RC -ne 0 ]] && _has "$OUT" "REFUSED" && pass "a damaged keyring is refused" || fail "truncated (rc=$RC): $OUT"
  export GNUPGHOME="$T/gnupg"; mkdir -m 700 "$GNUPGHOME"
  if gpg --batch --passphrase '' --quick-gen-key 'impostor <i@example.invalid>' ed25519 sign never >/dev/null 2>&1; then
    gpg --batch --export > "$T/impostor.gpg"
    OUT="$(bash "$RS" --check-keyring "$T/impostor.gpg" 2>&1)"; RC=$?
    [[ $RC -ne 0 ]] && _has "$OUT" "REFUSED" && pass "another key is refused" || fail "impostor (rc=$RC): $OUT"
    cat "$KR" "$T/impostor.gpg" > "$T/both.gpg"
    OUT="$(bash "$RS" --check-keyring "$T/both.gpg" 2>&1)"; RC=$?
    [[ $RC -ne 0 ]] && _has "$OUT" "REFUSED" && pass "Tailscale's key plus a second key is refused (apt would trust both)" || fail "both (rc=$RC): $OUT"
  else
    skip "gpg could not generate a throwaway key here"
  fi
  unset GNUPGHOME
else
  skip "no gpg: the Tailscale keyring check"
fi

# ---------------------------------------------------------------- ufw honesty (static)
grep -q '"Status: active"' "$RS" && grep -q 'FILTER NOTHING' "$RS" && grep -q 'spark-guard.sh' "$RS" \
  && pass "root setup says loudly when ufw is inactive and points to spark-guard.sh" || fail "ufw inactive warning missing"
! grep -q 'Only the fabric is opened, never the LAN or the tailnet' "$RS" \
  && pass "the header no longer promises a ufw that is not running" || fail "stale header promise"

echo ""
echo "spark supply chain: $PASS passed, $FAIL failed, $SKIP skipped"
[[ $FAIL -eq 0 ]]
