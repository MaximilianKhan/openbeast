#!/bin/bash
# Fetch ONE weight named in scripts/weights.registry, and verify it against
# its pin.
#
#   ./scripts/fetch-weight.sh Qwen3-0.6B-Q8_0.gguf
#   ./scripts/fetch-weight.sh --list
#
# WHY THIS EXISTS. bootstrap.sh downloads exactly one weight — the default
# model — and nothing could fetch any of the other 20-odd registry entries by
# name. That gap had a live consequence: scripts/serve-bootstrap.sh needs
# Qwen3-0.6B-Q8_0.gguf for the FAST_BOOT bridge, the file is registry-pinned
# (weights.registry), conf-exposed and documented in docs/REFERENCE.md — and
# no code path ever downloaded it. On any fresh install with FAST_BOOT=true,
# start.sh:415 exited 1 and the WHOLE stack failed to boot, with the
# diagnostic "bootstrap model failed to load" pointing at llama-server rather
# than at a file that was never fetched.
#
# Verification is not optional here. A weight that downloaded badly is worse
# than one that is absent: absent fails at load with a clear error, corrupt
# fails somewhere inside inference. So a size or sha256 mismatch DELETES the
# file rather than leaving it to be found later.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REGISTRY="$SCRIPT_DIR/weights.registry"

# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/weights.sh"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

[[ -f "$REGISTRY" ]] || die "$REGISTRY is missing — restore it from git"

cmd_list() {
  say "weight registry — $REGISTRY"
  say ""
  printf '  %-4s %-62s %10s  %s\n' "HAVE" "FILE" "SIZE" "HF REPO"
  while IFS=$'\t' read -r sha bytes name repo _remote; do
    [[ -z "${name:-}" || "$sha" == \#* ]] && continue
    # ASCII only: printf pads by BYTES, so a multi-byte dash skews the column.
    local_mark="no"
    [[ -f "$WEIGHTS_DIR/$name" ]] && local_mark="yes"
    size="?"
    [[ "${bytes:-0}" -gt 0 ]] 2>/dev/null && size="$(awk -v b="$bytes" 'BEGIN{printf "%.1f GB", b/1e9}')"
    printf '  %-4s %-62s %10s  %s\n' "$local_mark" "${name:0:62}" "$size" "$repo"
  done < <(grep -vE '^\s*#|^\s*$' "$REGISTRY")
  say ""
  say "weights dir: $WEIGHTS_DIR"
}

# --- online? Bounded, and it never blocks for long ------------------------
# A closed-network rig must get a SENTENCE, not a five-retry backoff chain
# ending in a python traceback. This is the whole difference between "no
# network, here is what to do" and hanging for minutes.
online() {
  curl -fsS --max-time 6 -o /dev/null "https://huggingface.co/api/whoami-v2" 2>/dev/null && return 0
  curl -fsS --max-time 6 -o /dev/null "https://huggingface.co" 2>/dev/null
}

sideload_help() {                     # sideload_help <name> <repo> <remote>
  local name="$1" repo="$2" remote="$3"
  say ""
  say "  No route to huggingface.co. This weight can still be sideloaded:"
  say "    1. on a connected machine:  hf download $repo $remote"
  say "    2. copy it to this box as:  $WEIGHTS_DIR/$name"
  say "    3. verify the pin:          ./scripts/verify-weights.sh --file $name"
  say ""
}

cmd_fetch() {
  local name="$1" row sha bytes repo remote dest
  row="$(awk -F'\t' -v f="$name" '$3 == f {print; exit}' "$REGISTRY")"
  [[ -n "$row" ]] || die "no registry entry for '$name' — try: $0 --list"
  IFS=$'\t' read -r sha bytes _name repo remote <<< "$row"
  [[ "$remote" == "-" || -z "$remote" ]] && remote="$name"
  dest="$WEIGHTS_DIR/$name"

  say "weight:  $name"
  say "repo:    $repo  (remote name: $remote)"
  say "dest:    $dest"
  if [[ "$sha" == "PENDING" ]]; then
    warn "registry row is PENDING — there is no hash to verify against yet"
  else
    say "pinned:  ${bytes} bytes, sha256 ${sha:0:16}…"
  fi
  say ""

  if [[ -f "$dest" ]]; then
    ok "already present"
    exec "$SCRIPT_DIR/verify-weights.sh" --file "$name"
  fi

  mkdir -p "$WEIGHTS_DIR" || die "cannot create $WEIGHTS_DIR"
  if ! online; then
    warn "offline"
    sideload_help "$name" "$repo" "$remote"
    exit 4
  fi

  local hf; hf="$(command -v hf || command -v huggingface-cli || true)"
  if [[ -z "$hf" ]]; then
    warn "the hf CLI is not installed (pip install --user huggingface_hub)"
    sideload_help "$name" "$repo" "$remote"
    exit 3
  fi

  say "downloading…"
  if ! "$hf" download "$repo" "$remote" --local-dir "$WEIGHTS_DIR"; then
    die "download failed for $repo/$remote"
  fi
  # The remote name may differ from the local one the serve scripts expect.
  if [[ "$remote" != "$name" && -f "$WEIGHTS_DIR/$remote" && ! -f "$dest" ]]; then
    mv "$WEIGHTS_DIR/$remote" "$dest" || die "could not rename $remote -> $name"
  fi
  [[ -f "$dest" ]] || die "download reported success but $dest is not there"

  # --- verify, and DELETE on mismatch ---------------------------------------
  if [[ "$sha" == "PENDING" ]]; then
    ok "downloaded ($(stat -c%s "$dest") bytes) — registry row is PENDING, nothing to verify"
    say "  Pin it:  sha256sum '$dest'  &&  stat -c%s '$dest'   then edit $REGISTRY"
    return 0
  fi
  local got_bytes got_sha
  got_bytes="$(stat -c%s "$dest")"
  if [[ "$got_bytes" != "$bytes" ]]; then
    rm -f "$dest"
    die "SIZE MISMATCH for $name: got $got_bytes, pinned $bytes. Deleted — a corrupt
       weight on disk fails inside inference, which is far worse than a missing one."
  fi
  say "  verifying sha256…"
  got_sha="$(sha256sum "$dest" | awk '{print $1}')"
  if [[ "$got_sha" != "$sha" ]]; then
    rm -f "$dest"
    die "CHECKSUM MISMATCH for $name
       got    $got_sha
       pinned $sha
       Deleted. Either the upstream repo changed the file (vet it, then re-pin
       in $REGISTRY) or the transfer corrupted it (re-run this)."
  fi
  ok "downloaded and verified against the pin"
}

case "${1:-}" in
  --list|-l)      cmd_list ;;
  -h|--help|"")   sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
  -*)             die "unknown option: $1" ;;
  *)              cmd_fetch "$1" ;;
esac
