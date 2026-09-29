#!/bin/bash
# Rotate OpenBeast's unbounded-growth logs. The policy — which files, how big,
# how many kept — is scripts/logrotate-openbeast.conf; this script applies it
# without root.
#
#   ./scripts/logrotate.sh               # rotate now (what the daily timer runs)
#   ./scripts/logrotate.sh --install     # install + enable the daily systemd --user timer
#   ./scripts/logrotate.sh --uninstall   # disable + remove that timer
#   ./scripts/logrotate.sh --print-conf  # the config with this checkout's paths filled in
#
# With `logrotate` on PATH it runs `logrotate -s .run/logrotate.state` on the
# rendered config (the `su` line is dropped: it is only valid as root). Without
# it — logrotate is not installed everywhere — the conf's `size` and `rotate`
# are applied directly: a file over `size` is copied to .1 and truncated in
# place (copytruncate, so writers keep their fd), older copies shift up and
# are gzipped from .2 on (delaycompress), and at most `rotate` are kept.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$SCRIPT_DIR/logrotate-openbeast.conf"
RUN="$REPO_DIR/.run"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
LOGROTATE="${OPENBEAST_LOGROTATE:-logrotate}"   # tests point this at a stub / nothing

# The template with @REPO@/@USER@/@GROUP@ filled in. Paths are quoted so a
# checkout under a directory with a space still parses; substitution is done
# in bash, not sed, so no character in the path is special.
render() {
  local line user group
  user="$(id -un)"; group="$(id -gn)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" == @REPO@/* ]]; then
      printf '"%s"\n' "${line//@REPO@/$REPO_DIR}"
    elif [[ "$line" =~ ^[[:space:]]*su[[:space:]] ]]; then
      if [[ $EUID -eq 0 ]]; then
        line="${line//@USER@/$user}"; printf '%s\n' "${line//@GROUP@/$group}"
      fi
    else
      printf '%s\n' "$line"
    fi
  done < "$TEMPLATE"
}

# ---- fallback rotator (no logrotate binary) -----------------------------------
_directive() { awk -v k="$1" '$1==k {print $2; exit}' "$TEMPLATE"; }
_bytes() {                      # 50M → bytes (k/M/G, logrotate's units)
  local v="$1" n="${1%[kKmMgG]}"
  case "$v" in
    *[kK]) echo $((n * 1024)) ;; *[mM]) echo $((n * 1024 * 1024)) ;;
    *[gG]) echo $((n * 1024 * 1024 * 1024)) ;; *) echo "$n" ;;
  esac
}
rotate_one() {                  # rotate_one <file> <keep>
  local f="$1" keep="$2" i
  rm -f -- "$f.$keep" "$f.$keep.gz"
  for ((i = keep - 1; i >= 1; i--)); do
    [[ -e "$f.$i.gz" ]] && mv -f -- "$f.$i.gz" "$f.$((i + 1)).gz"
    if [[ -e "$f.$i" ]]; then
      mv -f -- "$f.$i" "$f.$((i + 1))"
      gzip -f -- "$f.$((i + 1))"
    fi
  done
  cp -p -- "$f" "$f.1"
  : > "$f"
}
rotate_builtin() {
  local size keep max f line
  size="$(_directive size)"; keep="$(_directive rotate)"
  max="$(_bytes "${size:-50M}")"; keep="${keep:-8}"
  while IFS= read -r line; do
    line="${line#\"}"; line="${line%\"}"
    for f in $line; do          # unquoted on purpose: the ext-*.log glob
      [[ -f "$f" ]] || continue
      [[ "$(stat -c %s -- "$f" 2>/dev/null || echo 0)" -ge "$max" ]] || continue
      rotate_one "$f" "$keep"
      echo "rotated $f"
    done
  done < <(render | grep '^"/')
}

run() {
  mkdir -p "$RUN"
  local conf="$RUN/logrotate.conf"
  (umask 077; render > "$conf")
  if command -v "$LOGROTATE" >/dev/null 2>&1; then
    "$LOGROTATE" -s "$RUN/logrotate.state" "$conf"
  else
    local IFS=$'\n'
    rotate_builtin
  fi
}

install_units() {
  command -v systemctl >/dev/null 2>&1 || {
    echo "systemctl not found — run ./scripts/logrotate.sh from cron instead (daily)." >&2; exit 1; }
  mkdir -p "$UNIT_DIR"
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '%s\n' "${line//@REPO@/$REPO_DIR}"
  done < "$SCRIPT_DIR/openbeast-logrotate.service" > "$UNIT_DIR/openbeast-logrotate.service"
  cp "$SCRIPT_DIR/openbeast-logrotate.timer" "$UNIT_DIR/openbeast-logrotate.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now openbeast-logrotate.timer
  command -v "$LOGROTATE" >/dev/null 2>&1 \
    || echo "note: logrotate is not installed — the timer uses the built-in size rotation"
  echo "installed: openbeast-logrotate.timer (daily; journalctl --user -u openbeast-logrotate)"
}

uninstall_units() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now openbeast-logrotate.timer 2>/dev/null || true
  fi
  rm -f "$UNIT_DIR/openbeast-logrotate.timer" "$UNIT_DIR/openbeast-logrotate.service"
  command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload 2>/dev/null
  echo "removed: openbeast-logrotate.timer"
}

case "${1:-}" in
  "") run ;;
  --install) install_units ;;
  --uninstall) uninstall_units ;;
  --print-conf) render ;;
  -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
esac
