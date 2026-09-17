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

# This tool's JOB is to populate the weights directory, so it must work on a
# machine that does not have one yet — which is precisely the machine you
# reach for it on. lib/weights.sh is otherwise fatal when the dir is missing
# (a deliberate friendly guard for serve scripts), and it already ships the
# escape hatch bootstrap.sh uses for the same reason. Use it rather than
# inventing a second path resolver.
export OPENBEAST_WEIGHTS_MKDIR=1
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

  # --- download into a PRIVATE staging dir, never into WEIGHTS_DIR itself ----
  # THIS DESTROYED WEIGHTS. The download used to go `--local-dir "$WEIGHTS_DIR"`
  # under the REMOTE name and get renamed afterwards. But one row's remote name
  # can be ANOTHER row's local name, and in this registry it is:
  #   Qwen3.6-27B-MTP-UD-Q5_K_XL.gguf   remote: Qwen3.6-27B-UD-Q5_K_XL.gguf
  #   Qwen3.6-27B-UD-Q5_K_XL.gguf       (a separate, non-MTP weight)
  # (the 35B-A3B pair collides the same way). With the non-MTP file already on
  # disk, hf saw a same-named file with a different etag, re-fetched OVER it,
  # and the rename then moved it to the MTP name — 20 GB of the user's other
  # model gone, and its serve script broken until re-downloaded.
  #
  # So nothing is ever written to "$WEIGHTS_DIR/$remote". The stage is:
  #   - INSIDE $WEIGHTS_DIR, so it is the same filesystem and the final mv is
  #     an atomic rename, not a second 20 GB copy across devices;
  #   - keyed on the LOCAL name (unique in the registry), NOT on $$: hf keeps
  #     its resume state in <local-dir>/.cache/huggingface/*.incomplete, so a
  #     per-pid directory would throw away a half-finished 20 GB download on
  #     every retry. A stable name means re-running this RESUMES.
  # It is therefore kept when the DOWNLOAD fails or is interrupted (that is
  # the resume case) and removed on every other exit — success, a verify
  # failure, or a download that produced no file.
  local stage="$WEIGHTS_DIR/.fetch.$name" staged
  staged="$stage/$remote"
  mkdir -p "$stage" || die "cannot create the staging dir $stage"
  _FETCH_STAGE="$stage"; _FETCH_KEEP=1
  trap '[[ "${_FETCH_KEEP:-0}" -eq 1 ]] || rm -rf -- "${_FETCH_STAGE:-}"' EXIT

  say "downloading…"
  if ! "$hf" download "$repo" "$remote" --local-dir "$stage"; then
    die "download failed for $repo/$remote
       The partial download is kept in $stage —
       re-run this command to resume it, or delete that directory to start over."
  fi
  _FETCH_KEEP=0
  [[ -f "$staged" ]] || die "download reported success but $staged is not there"

  # --- verify IN THE STAGE, and DELETE on mismatch ---------------------------
  # Verified before it is given its final name, so a file that fails its pin
  # never exists under a name a serve script would load.
  if [[ "$sha" != "PENDING" ]]; then
    local got_bytes got_sha
    got_bytes="$(stat -c%s "$staged")"
    if [[ "$got_bytes" != "$bytes" ]]; then
      rm -f "$staged"
      die "SIZE MISMATCH for $name: got $got_bytes, pinned $bytes. Deleted — a corrupt
       weight on disk fails inside inference, which is far worse than a missing one."
    fi
    say "  verifying sha256…"
    got_sha="$(sha256sum "$staged" | awk '{print $1}')"
    if [[ "$got_sha" != "$sha" ]]; then
      rm -f "$staged"
      die "CHECKSUM MISMATCH for $name
       got    $got_sha
       pinned $sha
       Deleted. Either the upstream repo changed the file (vet it, then re-pin
       in $REGISTRY) or the transfer corrupted it (re-run this)."
    fi
  fi

  # -n: never replace a weight that appeared at $dest while we were downloading.
  [[ ! -e "$dest" ]] || die "$dest appeared during the download — left alone.
       Verify it:  ./scripts/verify-weights.sh --file $name"
  mv -n -- "$staged" "$dest" || die "could not move $staged -> $dest"
  [[ -f "$dest" ]] || die "could not move $staged -> $dest"

  if [[ "$sha" == "PENDING" ]]; then
    ok "downloaded ($(stat -c%s "$dest") bytes) — registry row is PENDING, nothing to verify"
    say "  Pin it:  sha256sum '$dest'  &&  stat -c%s '$dest'   then edit $REGISTRY"
    return 0
  fi
  ok "downloaded and verified against the pin"
}

case "${1:-}" in
  --list|-l)      cmd_list ;;
  -h|--help|"")   sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
  -*)             die "unknown option: $1" ;;
  *)              cmd_fetch "$1" ;;
esac
