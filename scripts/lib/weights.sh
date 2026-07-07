#!/bin/bash
# OpenBeast — weights directory resolver.
#
# Sourced by every model launch script to decide WHERE the .gguf weight files
# live. Weights are large (10s of GB each), so users should not have to copy
# them into the repo. This resolves a WEIGHTS_DIR from several sources so the
# same checkout works whether your weights sit on an NVMe, a USB drive, a NAS
# mount, or right next to the repo.
#
# Resolution order (first match wins):
#   1. $OPENBEAST_WEIGHTS_DIR       — env var, highest priority (per-shell override)
#   2. WEIGHTS_DIR= in openbeast.conf — repo-root config file (persistent, gitignored)
#   3. $REPO_DIR/weights            — legacy in-repo dir, IF it exists (back-compat)
#   4. $REPO_DIR/../weights         — default for a fresh clone: a sibling folder
#                                     next to the openbeast checkout
#
# Paths may use ~ and may be relative (resolved against the repo root).
#
# Requires REPO_DIR to be set before sourcing.

: "${REPO_DIR:?REPO_DIR must be set before sourcing lib/weights.sh}"

# Expand a leading ~ to $HOME (POSIX-safe; no eval).
_ob_expand_tilde() {
  case "$1" in
    "~")    printf '%s\n' "$HOME" ;;
    "~/"*)  printf '%s\n' "$HOME/${1#\~/}" ;;
    *)      printf '%s\n' "$1" ;;
  esac
}

# Read the WEIGHTS_DIR value from openbeast.conf, if present. Ignores comments
# and surrounding whitespace/quotes; last assignment wins.
_ob_read_conf() {
  local conf="$REPO_DIR/openbeast.conf"
  [[ -f "$conf" ]] || return 1
  local line
  line="$(grep -E '^[[:space:]]*WEIGHTS_DIR[[:space:]]*=' "$conf" | tail -n1)" || return 1
  [[ -n "$line" ]] || return 1
  # strip key, leading/trailing space, and matching quotes
  line="${line#*=}"
  line="${line#"${line%%[![:space:]]*}"}"   # ltrim
  line="${line%"${line##*[![:space:]]}"}"   # rtrim
  line="${line#\"}"; line="${line%\"}"
  line="${line#\'}"; line="${line%\'}"
  [[ -n "$line" ]] || return 1
  printf '%s\n' "$line"
}

_ob_resolve_weights_dir() {
  local dir=""
  if [[ -n "${OPENBEAST_WEIGHTS_DIR:-}" ]]; then
    dir="$OPENBEAST_WEIGHTS_DIR"
  elif dir="$(_ob_read_conf)"; then
    : # dir set from conf
  elif [[ -d "$REPO_DIR/weights" ]]; then
    dir="$REPO_DIR/weights"
  else
    dir="$REPO_DIR/../weights"
  fi

  dir="$(_ob_expand_tilde "$dir")"
  # Make relative paths relative to the repo root.
  case "$dir" in
    /*) ;;                       # already absolute
    *)  dir="$REPO_DIR/$dir" ;;
  esac
  printf '%s\n' "$dir"
}

WEIGHTS_DIR="$(_ob_resolve_weights_dir)"
export WEIGHTS_DIR

# Friendly guard: if the resolved dir is missing, tell the user exactly how to
# point OpenBeast at their weights instead of failing later with a cryptic
# "model not found" from llama.cpp.
if [[ ! -d "$WEIGHTS_DIR" ]]; then
  echo "Error: weights directory not found: $WEIGHTS_DIR" >&2
  echo "" >&2
  echo "Point OpenBeast at your weights in one of these ways:" >&2
  echo "  • export OPENBEAST_WEIGHTS_DIR=/path/to/weights" >&2
  echo "  • set WEIGHTS_DIR=/path/to/weights in $REPO_DIR/openbeast.conf" >&2
  echo "    (copy openbeast.conf.example to get started)" >&2
  echo "  • create the default sibling folder: $REPO_DIR/../weights" >&2
  exit 1
fi
