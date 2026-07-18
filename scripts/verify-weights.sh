#!/bin/bash
# Verify downloaded model weights against the pinned registry
# (scripts/weights.registry) — the supply-chain anchor for the one class of
# artifact too big to vendor. Same discipline as the digest-pinned container
# images: every shipped GGUF has the exact sha256 + byte size validated on
# the reference box; a silent swap in an upstream HF repo fails loudly here.
#
# Usage:
#   ./scripts/verify-weights.sh              # quick: byte sizes of present files
#   ./scripts/verify-weights.sh --deep       # full sha256 of present files (~1 min per 20 GB)
#   ./scripts/verify-weights.sh --file NAME  # deep-verify one file
#
# Only files that exist locally are checked — not having downloaded a model
# is not a failure. Exit 1 on any size or hash mismatch.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REGISTRY="$SCRIPT_DIR/weights.registry"
# Resolve the weights dir WITHOUT lib/weights.sh's hard exit-on-missing: a
# not-yet-downloaded weights dir is "nothing to verify" here, not an error
# (that hard exit is right for serve scripts, wrong for an integrity check —
# and letting it propagate made doctor exit nonzero on a fresh/CI checkout,
# which the doctor test reads via pipefail as a failure). weights.sh exports
# WEIGHTS_DIR before its existence check, so a guarded subshell captures it.
WEIGHTS_DIR="$( (source "$SCRIPT_DIR/lib/weights.sh" >/dev/null 2>&1; printf '%s' "${WEIGHTS_DIR:-}") || true )"
[[ -n "$WEIGHTS_DIR" ]] || WEIGHTS_DIR="$REPO_DIR/weights"

DEEP=0; ONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep) DEEP=1 ;;
    --file) ONLY="${2:?--file needs a filename}"; DEEP=1; shift ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

[[ -f "$REGISTRY" ]] || { echo "Error: $REGISTRY missing" >&2; exit 1; }

# No weights directory yet = nothing downloaded = nothing to verify (exit 0).
# A specific --file request against a missing dir is still an error.
if [[ ! -d "$WEIGHTS_DIR" ]]; then
  if [[ -n "$ONLY" ]]; then
    echo "MISSING  $ONLY (no weights directory at $WEIGHTS_DIR)"; exit 1
  fi
  echo "No weights directory yet ($WEIGHTS_DIR) — nothing to verify."
  exit 0
fi

fails=0; checked=0; absent=0; pending=0
while IFS=$'\t' read -r sha bytes fname repo remote; do
  [[ -z "$sha" || "$sha" == \#* ]] && continue
  [[ -n "$ONLY" && "$fname" != "$ONLY" ]] && continue
  path="$WEIGHTS_DIR/$fname"
  # PENDING = a shipped serve script targets this weight, but it isn't hashed
  # yet (not downloaded on the reference box at authoring time). Not a failure;
  # once downloaded, replace the PENDING row with a real sha256 + byte size
  # (sha256sum the file). Until then there's nothing to verify against.
  if [[ "$sha" == "PENDING" ]]; then
    pending=$((pending + 1))
    if [[ -f "$path" ]]; then
      echo "PENDING  $fname downloaded but not yet pinned — pin it: sha256sum + size into scripts/weights.registry"
    fi
    [[ -n "$ONLY" ]] && { echo "PENDING  $fname has no pin yet (nothing to verify)"; exit 0; }
    continue
  fi
  if [[ ! -f "$path" ]]; then
    absent=$((absent + 1))
    [[ -n "$ONLY" ]] && { echo "MISSING  $fname (not downloaded — nothing to verify)"; exit 1; }
    continue
  fi
  checked=$((checked + 1))
  actual_bytes="$(stat -c '%s' "$path")"
  if [[ "$actual_bytes" != "$bytes" ]]; then
    echo "FAIL     $fname — size $actual_bytes, registry pins $bytes (truncated or swapped download; delete and re-fetch from $repo)"
    fails=$((fails + 1))
    continue
  fi
  if [[ $DEEP -eq 1 ]]; then
    actual_sha="$(sha256sum "$path" | awk '{print $1}')"
    if [[ "$actual_sha" != "$sha" ]]; then
      echo "FAIL     $fname — sha256 mismatch (registry pins ${sha:0:16}…, got ${actual_sha:0:16}…; upstream $repo changed the file — do NOT use it blindly)"
      fails=$((fails + 1))
      continue
    fi
    echo "OK       $fname (size + sha256)"
  else
    echo "OK       $fname (size; --deep for sha256)"
  fi
done < "$REGISTRY"

# Surface .gguf files the registry doesn't know — not an error (users bring
# their own models), but worth a line so a typo'd filename can't hide.
while IFS= read -r f; do
  base="$(basename "$f")"
  grep -qP "\t\Q$base\E\t" "$REGISTRY" || echo "info: $base present but not in the registry (user-supplied model? fine)"
done < <(find "$WEIGHTS_DIR" -maxdepth 1 -name '*.gguf' 2>/dev/null)

echo ""
echo "Verified $checked file(s), $absent registered model(s) not downloaded, ${pending} awaiting a pin, $fails failure(s)."
exit $(( fails > 0 ? 1 : 0 ))
