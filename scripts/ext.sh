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

_usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

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
    [[ -d "$REPO_DIR/extensions/$name" ]] || { echo "No such extension: $name (see: ext.sh list)" >&2; exit 1; }
    [[ -f "$REPO_DIR/extensions/$name/manifest" ]] || { echo "Extension '$name' has no manifest — refusing." >&2; exit 1; }
    if ob_ext_is_enabled "$name"; then echo "'$name' already enabled."; exit 0; fi
    _write_extensions "$(printf '%s %s' "${EXTENSIONS:-}" "$name")"
    echo "Enabled '$name'. Restart to activate:  ./stop.sh && ./start.sh -d"
    ;;
  disable)
    name="${2:?usage: ext.sh disable <name>}"
    _write_extensions "$(echo " ${EXTENSIONS:-} " | sed "s/ ${name} / /g")"
    echo "Disabled '$name'. Restart to deactivate:  ./stop.sh && ./start.sh -d"
    ;;
  status)
    echo "Enabled extensions:"
    en=0
    while IFS= read -r name; do
      [[ -z "$name" ]] && continue; en=1
      kind="$(ob_ext_meta "$name" KIND 2>/dev/null || echo '?')"
      run="stopped"
      if [[ "$kind" == process ]] && [[ -f "$REPO_DIR/.run/ext-$name.pid" ]] \
         && kill -0 "$(cat "$REPO_DIR/.run/ext-$name.pid" 2>/dev/null)" 2>/dev/null; then run="running"; fi
      [[ "$kind" == compose ]] && run="(compose — see docker ps)"
      printf '  %-16s %-8s %s\n' "$name" "$kind" "$run"
    done < <(ob_ext_enabled)
    [[ $en -eq 1 ]] || echo "  (none enabled)"
    ;;
  -h|--help|help) _usage ;;
  *) echo "Unknown command: $cmd" >&2; _usage >&2; exit 2 ;;
esac
