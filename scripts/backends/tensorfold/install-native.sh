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
# Three checks, all required: the archive against its .sha256, that .sha256
# against the release's ed25519 signature (.sha256.sig) with the public key
# vendored next to this script (release-key.pem, from TensorFold's
# packaging/runtime-trust.json at 09a9eb33 — never fetched at install time),
# and every unpacked file against the archive's own SHA256SUMS. --stage
# verifies too, so a staged folder is known-good before it is carried over.
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
    -h|--help)  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[[ -z "$STAGE" || -z "$FROM" ]] || { echo "--stage and --from are exclusive" >&2; exit 2; }
if [[ -z "$PLATFORM" ]]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-aarch64|Linux-arm64) PLATFORM=linux-aarch64 ;;
    Linux-x86_64)              PLATFORM=linux-x86_64 ;;
    Darwin-arm64)              PLATFORM=macos-arm64 ;;
    *) echo "no TensorFold release for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
  esac
fi
A="tensorfold-$VERSION-$PLATFORM.tar.gz"
URL="https://github.com/ashhart/TensorFold/releases/download/v$VERSION"

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

verify() {   # verify <dir>: archive vs .sha256, .sha256 vs the signature
  local d="$1" signed
  (cd "$d" && sha256 -c "$A.sha256") || { echo "checksum mismatch: $d/$A" >&2; exit 1; }
  signed="$(mktemp)"
  { printf 'tensorfold-native-checksum-v1\n'; cat "$d/$A.sha256"; } > "$signed"
  if ! openssl pkeyutl -verify -pubin -inkey "$KEY" -rawin -in "$signed" -sigfile "$d/$A.sha256.sig" >/dev/null 2>&1; then
    rm -f "$signed"
    echo "release signature does NOT verify for $A (needs OpenSSL >= 3.0; LibreSSL cannot check ed25519)" >&2
    exit 1
  fi
  rm -f "$signed"
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
  echo "staged in $SRC — carry the folder over and run: $0 --from <folder> --version $VERSION --platform $PLATFORM"
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
