#!/bin/bash
# OpenBeast extension manager (ODS-absorbed). Enable/disable optional services
# that attach to the stack without editing core files. See extensions/README.md.
#
#   ./scripts/ext.sh list                 # available extensions + enabled state
#   ./scripts/ext.sh enable  <name>       # add to openbeast.conf EXTENSIONS
#   ./scripts/ext.sh disable <name>       # remove from EXTENSIONS
#   ./scripts/ext.sh status               # what's enabled + running
#   ./scripts/ext.sh restart <name>       # restart ONE process extension (e.g. the
#                                         # dashboard after a pull; starts it if it
#                                         # was stopped), nothing else: no model,
#                                         # gate or tools. Waits for its health route
#                                         # (manifest HEALTH_PATH) before saying so.
#
# Enabling/disabling edits openbeast.conf and takes effect on the next
# ./start.sh (compose fragments merge, process extensions launch). A running
# stack is not touched until restart.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONF="$REPO_DIR/openbeast.conf"
# --help is answered BEFORE lib/conf.sh is sourced: that file writes a
# generated secret into openbeast.conf, and asking for help on a fresh
# checkout must not create one.
case "${1:-}" in
  -h|--help|help)
    # shellcheck source=scripts/lib/usage.sh
    source "$SCRIPT_DIR/lib/usage.sh"
    ob_usage "$0"; exit 0 ;;
esac
source "$SCRIPT_DIR/lib/conf.sh"
source "$SCRIPT_DIR/lib/extensions.sh"
source "$SCRIPT_DIR/lib/proc.sh"      # ob_recorded_pid_ours, _ob_ere

# The header block above, whole (lib/usage.sh) — a fixed line range leaked
# `set -euo pipefail` and the SCRIPT_DIR assignment into the help.
_usage() {
  # shellcheck source=scripts/lib/usage.sh
  source "$SCRIPT_DIR/lib/usage.sh"
  ob_usage "$0"
}

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
# enable/disable rewrite openbeast.conf, so they start from the conf's OWN
# list. lib/conf.sh resolved EXTENSIONS as ${OPENBEAST_EXTENSIONS:-<conf>},
# and building the new line from that wrote a one-off env override into the
# conf for good, dropping what the conf listed. list/status keep the
# effective list: that is what a start would run.
if [[ "$cmd" == enable || "$cmd" == disable ]]; then
  if [[ -n "${OPENBEAST_EXTENSIONS:-}" ]]; then
    echo "Note: OPENBEAST_EXTENSIONS=\"$OPENBEAST_EXTENSIONS\" is set in this shell and overrides openbeast.conf until it is unset — editing the conf's own list." >&2
  fi
  EXTENSIONS="$(_ob_conf_value EXTENSIONS || true)"
fi
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
  restart)
    # One process-kind extension, alone: its recorded process (identity-
    # checked, as stop.sh reaps it) is stopped and run.sh relaunched with this
    # shell's conf.sh environment (the relaunch healthcheck.sh --restart
    # does). Nothing else is touched — not the model server, the gate, the
    # tool server or the inference unit — so it is safe while evals run.
    name="${2:?usage: ext.sh restart <name>}"
    _valid_name "$name" || exit 2
    [[ "$(ob_ext_meta "$name" KIND 2>/dev/null || true)" == process ]] \
      || { echo "'$name' is not a process extension (compose ones restart with the stack)." >&2; exit 1; }
    ob_ext_is_enabled "$name" || { echo "'$name' is not enabled (ext.sh enable $name)." >&2; exit 1; }
    if [[ -f "$REPO_DIR/.run/stopped" ]]; then
      echo "The stack was stopped on purpose (.run/stopped): start it with ./start.sh -d instead." >&2; exit 1
    fi
    pidf="$REPO_DIR/.run/ext-$name.pid"
    mkdir -p "$REPO_DIR/.run"
    old_pid=""
    if ob_recorded_pid_ours "$pidf" "$(_ob_ere "$REPO_DIR/extensions/$name/")"; then old_pid="$(cat "$pidf")"; fi
    ob_ext_reap "$pidf" "$REPO_DIR/extensions/$name"
    # Wait for the old process to be gone (it may flush state on SIGTERM:
    # the dashboard saves its power history), not a fixed second.
    if [[ -n "$old_pid" ]]; then
      for _ in $(seq 1 50); do kill -0 "$old_pid" 2>/dev/null || break; sleep 0.1; done
      kill -0 "$old_pid" 2>/dev/null && echo "Note: the old '$name' process ($old_pid) is still exiting." >&2
    fi
    log="$REPO_DIR/.run/ext-$name.log"
    printf '%s ext.sh restart %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$name" >> "$log"
    # Detached (setsid where it exists) so it outlives this shell; stop.sh
    # and the next start.sh find it through the pidfile like any other.
    if command -v setsid >/dev/null 2>&1; then
      setsid nohup "$REPO_DIR/extensions/$name/run.sh" >>"$log" 2>&1 </dev/null &
    else
      nohup "$REPO_DIR/extensions/$name/run.sh" >>"$log" 2>&1 </dev/null &
    fi
    ob_pid_record "$pidf" "$!"
    # Up = the new process is ours AND (when the manifest names one) its
    # health route answers, within ~10 s. HEALTH_PORT_VAR names the conf.sh
    # variable holding the port (DASHBOARD_PORT), HEALTH_PATH the route.
    hpath="$(ob_ext_meta "$name" HEALTH_PATH 2>/dev/null || true)"
    hvar="$(ob_ext_meta "$name" HEALTH_PORT_VAR 2>/dev/null || true)"
    hport=""
    if [[ "$hvar" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then hport="${!hvar:-}"; fi
    ok=0
    for _ in $(seq 1 20); do
      sleep 0.5
      ob_recorded_pid_ours "$pidf" "$(_ob_ere "$REPO_DIR/extensions/$name/")" || break
      if [[ -z "$hpath" || ! "$hport" =~ ^[0-9]+$ ]]; then ok=1; sleep 1.5; break; fi
      if curl -fsS -o /dev/null --max-time 2 "http://${OPENBEAST_PROBE_HOST:-127.0.0.1}:${hport}${hpath}" 2>/dev/null; then ok=1; break; fi
    done
    if [[ $ok -eq 1 ]] && ob_recorded_pid_ours "$pidf" "$(_ob_ere "$REPO_DIR/extensions/$name/")"; then
      echo "Restarted '$name' (pid $(cat "$pidf"))${hpath:+, answering ${hpath}}; log: .run/ext-$name.log"
    elif ob_recorded_pid_ours "$pidf" "$(_ob_ere "$REPO_DIR/extensions/$name/")"; then
      echo "'$name' is running (pid $(cat "$pidf")) but ${hpath} did not answer within 10 s — last lines of .run/ext-$name.log:" >&2
      tail -n 5 "$log" >&2; exit 1
    else
      echo "'$name' exited right after the restart — last lines of .run/ext-$name.log:" >&2
      tail -n 5 "$log" >&2; exit 1
    fi
    ;;
  -h|--help|help) _usage ;;
  *) echo "Unknown command: $cmd" >&2; _usage >&2; exit 2 ;;
esac
