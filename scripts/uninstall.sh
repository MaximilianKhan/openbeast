#!/bin/bash
# Uninstall the RIG. (A client removes itself with `openbeast-client uninstall`.)
#
#   ./scripts/uninstall.sh                 # DRY RUN: prints every step, touches nothing
#   ./scripts/uninstall.sh --go            # stop, unpublish, remove build/venv/runtime state
#   ./scripts/uninstall.sh --go --purge-weights   # ...and the model weights (WEIGHTS_DIR)
#   ./scripts/uninstall.sh --go --purge-data      # ...and Open WebUI's volume + the workspace
#                                                 #    (chats, accounts, artifacts, sessions)
#   ./scripts/uninstall.sh --go --purge-conf      # ...and openbeast.conf (the per-install secrets)
#   ./scripts/uninstall.sh --go --purge-all       # every --purge-*: a genuinely clean slate
#
# WHAT IS ALWAYS KEPT, unless its --purge flag says otherwise: the weights (the
# expensive part to re-download), openbeast.conf (a reinstall then picks up
# where you left off), Open WebUI's data volume (your chats and accounts), and
# the workspace in FILES_DIR (what the model wrote for you, including every
# published artifact and the session ledger). The repo checkout itself is never
# deleted — `rm -rf` the directory yourself when you are done.
#
# The footprint this undoes is exactly what README § Uninstall lists: the
# running processes and containers, the tailscale serve mounts, the user
# systemd units, and llama.cpp/ venv/ .run/. Nothing OpenBeast installs lives
# anywhere else.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GO=0; PW=0; PD=0; PC=0
for a in "$@"; do
  case "$a" in
    --go) GO=1 ;;
    --purge-weights) PW=1 ;; --purge-data) PD=1 ;; --purge-conf) PC=1 ;;
    --purge-all) PW=1; PD=1; PC=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (see --help)" >&2; exit 2 ;;
  esac
done

# Config is read WITHOUT sourcing lib/conf.sh: sourcing it has a side effect
# (it generates and appends a SearXNG secret to openbeast.conf), which is the
# last thing an uninstall should do. One key is needed — where the weights are.
_conf_value() {
  local key="$1" conf="$REPO_DIR/openbeast.conf" line
  [[ -f "$conf" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$conf" 2>/dev/null | tail -n1)" || return 1
  line="${line#*=}"; line="${line//[\"\']/}"; line="${line%%#*}"; line="${line//[[:space:]]/}"
  [[ -n "$line" ]] && printf '%s\n' "$line"
}
WEIGHTS_DIR="${OPENBEAST_WEIGHTS_DIR:-$(_conf_value WEIGHTS_DIR || echo "$REPO_DIR/weights")}"
FILES_DIR="${OPENBEAST_FILES_DIR:-$(_conf_value FILES_DIR || echo "$HOME/openbeast-files")}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
do_() {                              # do_ <description> -- <command...>
  local desc="$1"; shift; [[ "${1:-}" == "--" ]] && shift
  if [[ $GO -eq 1 ]]; then
    printf '  %-6s %s\n' "do" "$desc"
    "$@" || printf '  %-6s %s (rc=%s — continuing)\n' "!" "$desc" "$?"
  else
    printf '  %-6s %s\n' "would" "$desc"
  fi
  return 0
}
keep() { printf '  %-6s %s\n' "keep" "$*"; }

[[ $GO -eq 1 ]] || say "DRY RUN — nothing below is executed. Re-run with --go."

step "1. Stop everything (services, containers, the daemon scope)"
if [[ -x "$REPO_DIR/stop.sh" ]]; then
  do_ "./stop.sh" -- "$REPO_DIR/stop.sh"
else
  say "  stop.sh missing — skipping"
fi

step "2. Unpublish from the tailnet"
if command -v tailscale >/dev/null 2>&1; then
  if tailscale serve status 2>/dev/null | grep -q 'proxy'; then
    do_ "sudo tailscale serve reset  (every mount: :443 chat, :8443 inference, :8444/:8445/:8446/:8889)" -- sudo tailscale serve reset
  else
    say "  nothing is published — skipping"
  fi
else
  say "  tailscale not installed — skipping"
fi

step "3. User systemd units"
for u in openbeast.service openbeast-watchdog.timer openbeast-watchdog.service; do
  if [[ -e "$UNIT_DIR/$u" ]]; then
    do_ "systemctl --user disable --now $u" -- systemctl --user disable --now "$u"
    do_ "rm $UNIT_DIR/$u" -- rm -f "$UNIT_DIR/$u"
  fi
done
do_ "systemctl --user stop openbeast-stack (transient daemon scope, if any)" -- bash -c 'systemctl --user stop openbeast-stack 2>/dev/null; systemctl --user reset-failed openbeast-stack 2>/dev/null; true'
[[ $GO -eq 1 ]] && systemctl --user daemon-reload 2>/dev/null

step "4. Build, venv, runtime state"
for d in llama.cpp venv .run; do
  [[ -e "$REPO_DIR/$d" ]] && do_ "rm -rf $d/" -- rm -rf "$REPO_DIR/$d"
done

step "5. Containers' images and data"
if command -v docker >/dev/null 2>&1; then
  if [[ $PD -eq 1 ]]; then
    for v in $(docker volume ls -q 2>/dev/null | grep -E '(^|_)open-webui-data$' || true); do
      do_ "docker volume rm $v  (Open WebUI: chats, accounts, settings)" -- docker volume rm "$v"
    done
  else
    keep "Open WebUI's data volume (chats, accounts) — --purge-data removes it"
  fi
  say "  (container images are left for docker to prune: docker image prune -a)"
else
  say "  docker not installed — skipping"
fi

step "6. The workspace — $FILES_DIR"
if [[ $PD -eq 1 ]]; then
  [[ -d "$FILES_DIR" ]] && do_ "rm -rf $FILES_DIR  (per-user shards, artifacts, sessions)" -- rm -rf "$FILES_DIR"
else
  keep "$FILES_DIR (what the model wrote for you, every published page) — --purge-data removes it"
fi

step "7. Model weights — $WEIGHTS_DIR"
if [[ $PW -eq 1 ]]; then
  [[ -d "$WEIGHTS_DIR" ]] && do_ "rm -rf $WEIGHTS_DIR  ($(du -sh "$WEIGHTS_DIR" 2>/dev/null | cut -f1 || echo '?'))" -- rm -rf "$WEIGHTS_DIR"
else
  keep "$WEIGHTS_DIR ($(du -sh "$WEIGHTS_DIR" 2>/dev/null | cut -f1 || echo '?') — the expensive part to re-download) — --purge-weights removes it"
fi

step "8. openbeast.conf"
if [[ $PC -eq 1 ]]; then
  [[ -f "$REPO_DIR/openbeast.conf" ]] && do_ "rm openbeast.conf  (per-install secrets: SearXNG, JWT, API keys)" -- rm -f "$REPO_DIR/openbeast.conf"
else
  keep "openbeast.conf (a reinstall picks up where you left off) — --purge-conf removes it"
fi

say ""
if [[ $GO -eq 1 ]]; then
  say "Done. The checkout at $REPO_DIR is yours to delete: rm -rf \"$REPO_DIR\""
else
  say "Nothing was removed. Add --go to execute, and --purge-weights / --purge-data / --purge-conf (or --purge-all) for the parts kept by default."
fi
