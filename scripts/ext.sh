#!/bin/bash
# OpenBeast extension manager (ODS-absorbed). Enable/disable optional services
# that attach to the stack without editing core files. See extensions/README.md.
#
#   ./scripts/ext.sh list                 # available extensions + enabled state
#   ./scripts/ext.sh enable  <name>       # add to openbeast.conf EXTENSIONS
#   ./scripts/ext.sh disable <name>       # remove from EXTENSIONS
#   ./scripts/ext.sh status               # what's enabled + running
#
# Enabling/disabling edits openbeast.conf and takes effect on the next
# ./start.sh (compose fragments merge, process extensions launch). A running
# stack is not touched until restart.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONF="$REPO_DIR/openbeast.conf"
source "$SCRIPT_DIR/lib/conf.sh"
source "$SCRIPT_DIR/lib/extensions.sh"
source "$SCRIPT_DIR/lib/proc.sh"      # ob_recorded_pid_ours, _ob_ere

_usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

# An extension name is a directory name and nothing else. It becomes a path
# (extensions/<name>/), a pidfile name (.run/ext-<name>.pid) and a word in a
# sed-rewritten conf line, so anything else broke one of the three:
# `enable dashboard/` (tab completion) passed the -d test, and start.sh then
# died under set -e writing .run/ext-dashboard/.pid — after the model had
# loaded; `disable dash/` became a sed syntax error that wrote EXTENSIONS=""
# and `disable '.*'` a regex that matched every name. Both reported success.
_EXT_NAME_RE='^[A-Za-z0-9][A-Za-z0-9_-]*$'
_valid_name() {
  [[ "$1" =~ $_EXT_NAME_RE ]] && return 0
  echo "Invalid extension name: '$1' (letters, digits, '-' and '_' only — the directory name under extensions/)" >&2
  return 1
}

# Rewrite the EXTENSIONS= line in openbeast.conf to the given space-separated
# list (creates conf / the line as needed, preserves mode 600).
_write_extensions() {
  local newlist="$1"
  newlist="$(echo "$newlist" | tr -s ' ' | sed 's/^ //;s/ $//')"
  if [[ ! -f "$CONF" ]]; then ( umask 077; : > "$CONF" ); fi
  if grep -qE '^[[:space:]]*EXTENSIONS[[:space:]]*=' "$CONF"; then
    sed -i -E "s|^[[:space:]]*EXTENSIONS[[:space:]]*=.*|EXTENSIONS=\"${newlist}\"|" "$CONF"
  else
    printf '\n# Enabled extensions (scripts/ext.sh). See extensions/README.md.\nEXTENSIONS="%s"\n' "$newlist" >> "$CONF"
  fi
  chmod 600 "$CONF" 2>/dev/null || true
}

cmd="${1:-list}"
case "$cmd" in
  list)
    echo "Available extensions (extensions/):"
    found=0
    while IFS= read -r name; do
      [[ -z "$name" ]] && continue
      found=1
      state="disabled"; ob_ext_is_enabled "$name" && state="ENABLED"
      kind="$(ob_ext_meta "$name" KIND 2>/dev/null || echo '?')"
      desc="$(ob_ext_meta "$name" DESCRIPTION 2>/dev/null || echo '')"
      printf '  %-16s [%-8s] %-8s %s\n' "$name" "$state" "$kind" "$desc"
    done < <(ob_ext_available)
    [[ $found -eq 1 ]] || echo "  (none — drop one under extensions/<name>/)"
    ;;
  enable)
    name="${2:?usage: ext.sh enable <name>}"
    _valid_name "$name" || exit 2
    [[ -d "$REPO_DIR/extensions/$name" ]] || { echo "No such extension: $name (see: ext.sh list)" >&2; exit 1; }
    [[ -f "$REPO_DIR/extensions/$name/manifest" ]] || { echo "Extension '$name' has no manifest — refusing." >&2; exit 1; }
    if ob_ext_is_enabled "$name"; then echo "'$name' already enabled."; exit 0; fi
    _write_extensions "$(printf '%s %s' "${EXTENSIONS:-}" "$name")"
    echo "Enabled '$name'. Restart to activate:  ./stop.sh && ./start.sh -d"
    ;;
  disable)
    name="${2:?usage: ext.sh disable <name>}"
    _valid_name "$name" || exit 2
    # Rebuilt word by word with an EXACT comparison — never a regex built
    # from user input. Invalid words already in the conf are dropped (they
    # could only ever break start.sh), with a note.
    keep=() found=0
    for w in ${EXTENSIONS:-}; do
      if [[ "$w" == "$name" ]]; then found=1; continue; fi
      if [[ ! "$w" =~ $_EXT_NAME_RE ]]; then
        echo "Note: dropping invalid entry '$w' from EXTENSIONS." >&2; continue
      fi
      keep+=("$w")
    done
    if [[ $found -eq 0 ]]; then
      echo "'$name' is not enabled (EXTENSIONS=\"${EXTENSIONS:-}\") — nothing changed."
      exit 0
    fi
    _write_extensions "${keep[*]+"${keep[*]}"}"
    echo "Disabled '$name'. Restart to deactivate:  ./stop.sh && ./start.sh -d"
    ;;
  status)
    echo "Enabled extensions:"
    en=0
    while IFS= read -r name; do
      [[ -z "$name" ]] && continue; en=1
      kind="$(ob_ext_meta "$name" KIND 2>/dev/null || echo '?')"
      run="stopped"
      # Identity, not liveness: a stale pidfile's number may be anyone's now.
      if [[ "$kind" == process ]] \
         && ob_recorded_pid_ours "$REPO_DIR/.run/ext-$name.pid" "$(_ob_ere "$REPO_DIR/extensions/$name/")"; then run="running"; fi
      [[ "$kind" == compose ]] && run="(compose — see docker ps)"
      printf '  %-16s %-8s %s\n' "$name" "$kind" "$run"
    done < <(ob_ext_enabled)
    [[ $en -eq 1 ]] || echo "  (none enabled)"
    ;;
  -h|--help|help) _usage ;;
  *) echo "Unknown command: $cmd" >&2; _usage >&2; exit 2 ;;
esac
