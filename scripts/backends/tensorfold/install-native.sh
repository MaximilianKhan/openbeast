#!/bin/bash
# Install a signed native TensorFold release (>= 1.0, the Zig binary) for this
# user: ~/.local/opt/tensorfold-<V>-<platform>, linked as ~/.local/bin/tensorfold.
# docs/SPARK_HYDRA.md
#
#   scripts/backends/tensorfold/install-native.sh                 # download + verify + install
#   scripts/backends/tensorfold/install-native.sh --stage DIR     # download + verify into DIR only (for offline)
#   scripts/backends/tensorfold/install-native.sh --from DIR      # OFFLINE: install from a staged DIR
#   ... --version 1.2.7 --platform linux-aarch64|linux-x86_64|macos-arm64
#
# Three checks, all required: the .sha256 must be exactly one line naming
# THIS archive (tensorfold-<V>-<platform>.tar.gz) and the archive must hash to
# it — the signed message carries no version of its own, so the filename is
# the binding, and an older release's signed .sha256 never vouches for a
# newer name; that .sha256 against the release's ed25519 signature
# (.sha256.sig) with the public key vendored next to this script
# (release-key.pem, from TensorFold's packaging/runtime-trust.json at
# 09a9eb33 — never fetched at install time); and every unpacked file against
# the archive's own SHA256SUMS. --stage verifies too, so a staged folder is
# known-good before it is carried over.
#
# --stage is for another box: it stages linux-aarch64 (the DGX Spark) unless
# --platform says otherwise. ed25519 needs OpenSSL >= 3 (`pkeyutl -rawin`):
# $OPENSSL, the openssl on PATH, or Homebrew's openssl@3 — macOS's LibreSSL
# and OpenSSL 1.1 cannot — else python3 with `cryptography`.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KEY="$HERE/release-key.pem"
VERSION=1.2.7
PLATFORM="" STAGE="" FROM=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)  VERSION="${2:?}"; shift 2 ;;
    --platform) PLATFORM="${2:?}"; shift 2 ;;
    --stage)    STAGE="${2:?}"; shift 2 ;;
    --from)     FROM="${2:?}"; shift 2 ;;
    -h|--help)  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[[ -z "$STAGE" || -z "$FROM" ]] || { echo "--stage and --from are exclusive" >&2; exit 2; }
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "--version must be X.Y.Z, got '$VERSION'" >&2; exit 2; }
if [[ -z "$PLATFORM" && -n "$STAGE" ]]; then
  PLATFORM=linux-aarch64      # staged for a Spark, whatever box does the staging
  echo "staging for $PLATFORM (the DGX Spark); --platform to stage another"
elif [[ -z "$PLATFORM" ]]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-aarch64|Linux-arm64) PLATFORM=linux-aarch64 ;;
    Linux-x86_64)              PLATFORM=linux-x86_64 ;;
    Darwin-arm64)              PLATFORM=macos-arm64 ;;
    *) echo "no TensorFold release for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
  esac
fi
case "$PLATFORM" in
  linux-aarch64|linux-x86_64|macos-arm64) ;;
  *) echo "--platform must be linux-aarch64, linux-x86_64 or macos-arm64" >&2; exit 2 ;;
esac
A="tensorfold-$VERSION-$PLATFORM.tar.gz"
URL="https://github.com/ashhart/TensorFold/releases/download/v$VERSION"

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# ed25519_verify MSG SIG: 0 = a verifier accepted the signature, 1 = every
# capable verifier rejected it, 2 = no ed25519-capable verifier here.
ed25519_verify() {
  local msg="$1" sig="$2" o seen=0 tried=" "
  for o in "${OPENSSL:-}" "$(command -v openssl || true)" /opt/homebrew/opt/openssl@3/bin/openssl \
           /usr/local/opt/openssl@3/bin/openssl; do
    [[ -n "$o" && -x "$o" && "$tried" != *" $o "* ]] || continue
    tried+="$o "
    "$o" pkeyutl -help 2>&1 | grep -q -- '-rawin' || continue
    seen=1
    "$o" pkeyutl -verify -pubin -inkey "$KEY" -rawin -in "$msg" -sigfile "$sig" >/dev/null 2>&1 && return 0
  done
  if python3 -I -c 'import cryptography.hazmat.primitives.asymmetric.ed25519' 2>/dev/null; then
    seen=1
    python3 -I - "$KEY" "$msg" "$sig" 2>/dev/null <<'PY' && return 0
import sys
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
key = load_pem_public_key(open(sys.argv[1], "rb").read())
if not isinstance(key, Ed25519PublicKey):
    sys.exit(1)
key.verify(open(sys.argv[3], "rb").read(), open(sys.argv[2], "rb").read())
PY
  fi
  [[ $seen -eq 1 ]] && return 1
  return 2
}

verify() {   # verify <dir>: its .sha256 names exactly $A, $A hashes to it, the signature covers it
  local d="$1" content want got signed rc=0
  content="$(cat "$d/$A.sha256")"
  want="${content%% *}"
  if ! [[ "$want" =~ ^[0-9a-f]{64}$ && ( "$content" == "$want  $A" || "$content" == "$want *$A" ) ]]; then
    echo "REFUSED: $d/$A.sha256 is not exactly one line '<sha256>  $A' — a checksum file for another archive or version is never accepted" >&2
    exit 1
  fi
  got="$(sha256 "$d/$A" | awk '{print $1}')"
  [[ "$got" == "$want" ]] || { echo "checksum mismatch: $d/$A is $got, its .sha256 says $want" >&2; exit 1; }
  signed="$(mktemp)"
  { printf 'tensorfold-native-checksum-v1\n'; cat "$d/$A.sha256"; } > "$signed"
  ed25519_verify "$signed" "$d/$A.sha256.sig" || rc=$?
  rm -f "$signed"
  case $rc in
    0) ;;
    1) echo "release signature does NOT verify for $A" >&2; exit 1 ;;
    *) echo "no ed25519 verifier here: needs OpenSSL >= 3 (macOS: brew install openssl@3, or OPENSSL=/path/to/openssl)" \
            "or python3 with 'cryptography' — LibreSSL and OpenSSL 1.1 cannot check ed25519" >&2
       exit 1 ;;
  esac
  echo "verified: $A (sha256 + release signature)"
}

if [[ -n "$FROM" ]]; then
  SRC="$(cd "$FROM" && pwd)"
else
  SRC="${STAGE:-$(mktemp -d)}"
  mkdir -p "$SRC"
  for f in "$A" "$A.sha256" "$A.sha256.sig"; do
    curl -fsSL -o "$SRC/$f" "$URL/$f"
  done
fi
for f in "$A" "$A.sha256" "$A.sha256.sig"; do
  [[ -f "$SRC/$f" ]] || { echo "missing $SRC/$f" >&2; exit 1; }
done
verify "$SRC"
if [[ -n "$STAGE" ]]; then
  echo "staged $PLATFORM in $SRC — carry the folder over and run: $0 --from <folder> --version $VERSION --platform $PLATFORM"
  exit 0
fi

OPT="$HOME/.local/opt"
mkdir -p "$OPT" "$HOME/.local/bin"
rm -rf "$OPT/tensorfold-$VERSION-$PLATFORM.partial"
mkdir "$OPT/tensorfold-$VERSION-$PLATFORM.partial"
tar -xzf "$SRC/$A" -C "$OPT/tensorfold-$VERSION-$PLATFORM.partial"
DIR="$OPT/tensorfold-$VERSION-$PLATFORM.partial/tensorfold-$VERSION-$PLATFORM"
[[ -d "$DIR" ]] || { echo "unexpected archive layout in $A" >&2; exit 1; }
(cd "$DIR" && sha256 -c --quiet SHA256SUMS) || { echo "an unpacked file does not match SHA256SUMS" >&2; exit 1; }
rm -rf "$OPT/tensorfold-$VERSION-$PLATFORM"
mv "$DIR" "$OPT/tensorfold-$VERSION-$PLATFORM"
rm -rf "$OPT/tensorfold-$VERSION-$PLATFORM.partial"
ln -sfn "$OPT/tensorfold-$VERSION-$PLATFORM" "$OPT/tensorfold"
ln -sfn "$OPT/tensorfold/bin/tensorfold-native" "$HOME/.local/bin/tensorfold"
"$HOME/.local/bin/tensorfold" --version
echo "installed: ~/.local/bin/tensorfold -> $OPT/tensorfold-$VERSION-$PLATFORM (set TENSORFOLD_VERSION=$VERSION in spark.env)"
