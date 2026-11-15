#!/bin/bash
# OpenBeast — the weight files a llama.cpp launch loads, and the registry
# check on them (WEIGHT_ENFORCE). Sourced by scripts/serve.sh and
# scripts/run.sh. Defines functions only; nothing runs at source time.

# ob_file_bytes <path> — the size of the file a path names, FOLLOWING
# symlinks. `stat -c%s link` is the length of the link itself (the ~25 bytes
# of its target path), so a weight symlinked in from a Hugging Face blob
# store or a NAS was "31 bytes": a false registry mismatch (exit 3 under
# strict) and weights_mib=1 for the context scaler. Echoes 0 when unreadable.
ob_file_bytes() {
  local b
  b="$(stat -L -c%s "$1" 2>/dev/null)" || b=""
  [[ "$b" =~ ^[0-9]+$ ]] || b="$(wc -c < "$1" 2>/dev/null | tr -d ' ')" || b=""
  [[ "$b" =~ ^[0-9]+$ ]] && echo "$b" || echo 0
}

# ob_weight_files <model> [llama args...] — every weight file the launch
# loads, one per line:
#   * the -m file, or, when it is a split GGUF (NAME-0000K-of-0000N.gguf),
#     all N shards: llama.cpp opens one and follows the split metadata to its
#     siblings by name, in the same directory;
#   * every --mmproj / -mm and -md / --model-draft file in the args.
# Only the -m file was checked before: strict mode loaded shards 2..N, the
# vision projector and the draft model however they had been altered.
ob_weight_files() {
  local model="$1" re='^(.*)-([0-9]{5})-of-([0-9]{5})\.gguf$' i n
  shift
  if [[ "$model" =~ $re ]]; then
    local prefix="${BASH_REMATCH[1]}" total="${BASH_REMATCH[3]}"
    n=$((10#$total))
    for (( i = 1; i <= n; i++ )); do
      printf '%s-%05d-of-%s.gguf\n' "$prefix" "$i" "$total"
    done
  else
    printf '%s\n' "$model"
  fi
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -mm|--mmproj|-md|--model-draft)
        [[ $# -ge 2 ]] && printf '%s\n' "$2"
        shift; [[ $# -gt 0 ]] && shift ;;
      --mmproj=*|--model-draft=*) printf '%s\n' "${1#*=}"; shift ;;
      *) shift ;;
    esac
  done
}

# ob_weight_set_mib <file>... — the summed size, in MiB rounded up, of the
# files that exist (ob_file_bytes, so symlinks count as what they point at).
ob_weight_set_mib() {
  local f total=0
  for f in "$@"; do
    [[ -f "$f" ]] && total=$(( total + $(ob_file_bytes "$f") ))
  done
  echo $(( (total + 1048575) / 1048576 ))
}

# ob_weight_enforce <registry> <mode> <file>... — WEIGHT_ENFORCE for every
# file given (files not on disk are skipped: llama-server reports those).
# Container images are digest-pinned and Python deps hash-pinned; weights are
# the one shipped artifact too big to vendor, so scripts/weights.registry pins
# each by sha256 + byte size. Size-only by design — a sha256 of 20 GB on every
# launch would add minutes; `./scripts/verify-weights.sh --deep` is the hash
# check.
#   warn    (the default) print a WARNING per problem, return 0. A hard
#           refusal cannot start the stack, and someone serving a
#           legitimately unlisted local GGUF should not be locked out by an
#           upgrade.
#   strict  print every problem, return 3 when there is one.
#   off     check nothing.
# A problem is: no registry row; a PENDING row (bytes 0 — nothing to check
# the file against, so strict cannot vouch for it); or a size mismatch.
ob_weight_enforce() {
  local reg="$1" mode="$2"
  shift 2
  case "$mode" in
    warn|strict|off) ;;
    *) echo "WARNING: WEIGHT_ENFORCE='$mode' is not warn|strict|off — using warn" >&2
       mode="warn" ;;
  esac
  [[ "$mode" == "off" || ! -f "$reg" ]] && return 0
  local f name row want sha have msg hint bad=0
  for f in "$@"; do
    [[ -f "$f" ]] || continue
    name="$(basename "$f")"
    row="$(awk -F'\t' -v n="$name" '$0 !~ /^#/ && $3 == n {print; exit}' "$reg" || true)"
    msg=""
    if [[ -z "$row" ]]; then
      msg="weight '$name' is NOT in scripts/weights.registry (unpinned)"
      hint="add a row (sha256<TAB>bytes<TAB>filename<TAB>repo<TAB>remote) or set WEIGHT_ENFORCE=off"
    else
      sha="$(printf '%s' "$row" | cut -f1)"
      want="$(printf '%s' "$row" | cut -f2)"
      if [[ "$want" == "0" || "$sha" == "PENDING" ]]; then
        msg="weight '$name' is pinned PENDING in scripts/weights.registry (no size or sha256 to check it against)"
        hint="pin it after vetting: sha256sum + stat -L -c%s into scripts/weights.registry"
      else
        have="$(ob_file_bytes "$f")"
        if [[ "$have" != "$want" ]]; then
          msg="weight '$name' is ${have} bytes, registry pins ${want}"
          hint="re-download, or re-pin only after vetting: ./scripts/verify-weights.sh --deep"
        fi
      fi
    fi
    [[ -n "$msg" ]] || continue
    if [[ "$mode" == "strict" ]]; then
      echo "Error: $msg" >&2
      echo "       $hint" >&2
      bad=1
    else
      echo "WARNING: $msg" >&2
      echo "         $hint" >&2
    fi
  done
  if [[ $bad -eq 1 ]]; then
    echo "       (WEIGHT_ENFORCE=strict — set warn/off in openbeast.conf to proceed)" >&2
    # Exit 3, not 1: start.sh's MODEL_ROLLBACK cannot distinguish a crash
    # from a deliberate supply-chain refusal, and would "recover" by silently
    # serving a DIFFERENT model — the opposite of what strict mode is for. A
    # distinct code lets the supervisor refuse to roll back.
    echo "       (exit 3 = supply-chain refusal; rollback must NOT substitute another model)" >&2
    return 3
  fi
  return 0
}
