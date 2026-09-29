#!/bin/bash
# Uninstall the RIG. (A client removes itself with `openbeast-client uninstall`.)
#
#   ./scripts/uninstall.sh                 # DRY RUN: prints every step, touches nothing
#   ./scripts/uninstall.sh --go            # stop, unpublish, remove build/venv/runtime ephemera
#   ./scripts/uninstall.sh --go --purge-weights   # ...and the model weights (WEIGHTS_DIR)
#   ./scripts/uninstall.sh --go --purge-data      # ...and Open WebUI's volume + the workspace
#                                                 #    (chats, accounts, artifacts) + the session ledger
#   ./scripts/uninstall.sh --go --purge-state     # ...and all of .run/ (device registry, audit logs,
#                                                 #    artifact URL key, SSD wear history)
#   ./scripts/uninstall.sh --go --purge-conf      # ...and openbeast.conf (the per-install secrets)
#   ./scripts/uninstall.sh --go --purge-all       # every --purge-* above: a genuinely clean slate
#   ./scripts/uninstall.sh --go --purge-build     # remove llama.cpp/ even if it holds local work
#
# WHAT IS ALWAYS KEPT, unless its --purge flag says otherwise: the weights (the
# expensive part to re-download), openbeast.conf (a reinstall then picks up
# where you left off), Open WebUI's data volume (your chats and accounts), the
# workspace in FILES_DIR (what the model wrote for you, every published
# artifact), and the durable state in .run/ (enrolled devices, audit trails,
# the session ledger, the artifact raw-URL key). llama.cpp/ is also kept when
# it has local-only branches, stashes or uncommitted changes. The repo checkout
# itself is never deleted — `rm -rf` the directory yourself when you are done.
#
# The footprint this undoes is exactly what README § Uninstall lists: the
# running processes and containers, the tailscale serve mounts, the user
# systemd units, and llama.cpp/ venv/ .run/. Nothing OpenBeast installs lives
# anywhere else.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GO=0; PW=0; PD=0; PC=0; PS=0; PB=0
for a in "$@"; do
  case "$a" in
    --go) GO=1 ;;
    --purge-weights) PW=1 ;; --purge-data) PD=1 ;; --purge-conf) PC=1 ;;
    --purge-state) PS=1 ;; --purge-build) PB=1 ;;
    --purge-all) PW=1; PD=1; PC=1; PS=1 ;;
    -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (see --help)" >&2; exit 2 ;;
  esac
done

# ---- Where the data lives: resolved EXACTLY as the stack resolves it ---------
# lib/conf.sh is NOT sourced: it has a side effect (it generates and appends a
# SearXNG secret to openbeast.conf), which is the last thing an uninstall
# should do. lib/weights.sh has no such side effect, so WEIGHTS_DIR comes from
# it — env, then conf, then repo/weights if present, else ../weights, with ~
# expanded and relative paths anchored to the repo. It `exit 1`s when the dir
# is missing, so it runs in a subshell whose EXIT trap reports the resolved
# path either way (it is assigned before that check).
_weights_dir() {
  ( trap 'printf "%s" "${WEIGHTS_DIR:-}"' EXIT
    # shellcheck source=scripts/lib/weights.sh
    OPENBEAST_WEIGHTS_MKDIR=0 source "$SCRIPT_DIR/lib/weights.sh" ) 2>/dev/null
}
# The same parse as conf.sh's _ob_conf_value (trim + one pair of quotes; no
# inline comments), so a value means here what it means to the stack.
_conf_value() {
  local key="$1" conf="$REPO_DIR/openbeast.conf" line
  [[ -f "$conf" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$conf" 2>/dev/null | tail -n1)" || return 1
  [[ -n "$line" ]] || return 1
  line="${line#*=}"
  line="${line#"${line%%[![:space:]]*}"}"   # ltrim
  line="${line%"${line##*[![:space:]]}"}"   # rtrim
  line="${line#\"}"; line="${line%\"}"
  line="${line#\'}"; line="${line%\'}"
  [[ -n "$line" ]] && printf '%s\n' "$line"
}
# ~ expanded (the agents expanduser() FILES_DIR) and a relative path anchored
# to the repo — never to whatever directory this script was launched from.
_abs_path() {
  local p="$1"
  # shellcheck disable=SC2088  # matching a literal ~, not expanding one
  case "$p" in "~") p="$HOME" ;; "~/"*) p="$HOME/${p#\~/}" ;; esac
  case "$p" in /*) ;; *) p="$REPO_DIR/$p" ;; esac
  # Lexical normalisation only (-s): `..` and a trailing / go, but a symlinked
  # weights dir stays the link — rm -rf removes the link, not the NAS behind it.
  realpath -m -s -- "$p" 2>/dev/null || printf '%s\n' "$p"
}
WEIGHTS_DIR="$(_weights_dir)"
[[ -n "$WEIGHTS_DIR" ]] || WEIGHTS_DIR="$REPO_DIR/../weights"
WEIGHTS_DIR="$(_abs_path "$WEIGHTS_DIR")"
FILES_DIR="$(_abs_path "${OPENBEAST_FILES_DIR:-$(_conf_value FILES_DIR || echo "$HOME/openbeast-files")}")"
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

# Refuse an rm -rf whose target is not plainly a data directory: not absolute
# after resolution, the filesystem root, a system tree, $HOME, the checkout, or
# anything that CONTAINS $HOME or the checkout. A mistyped conf value
# (WEIGHTS_DIR=~ , =.. , =/usr) must cost a refusal, never a home directory.
_rm_target_ok() {
  local p real home repo
  real="$(realpath -m -- "$1" 2>/dev/null)" || return 1
  home="$(realpath -m -- "$HOME" 2>/dev/null)"; repo="$(realpath -m -- "$REPO_DIR" 2>/dev/null)"
  [[ "$real" == /?* ]] || return 1
  for p in "$home" "$repo"; do
    [[ -n "$p" && "$p/" == "$real/"* ]] && return 1   # equal to, or an ancestor of
  done
  case "$real" in
    /*/*) ;;                                           # at least two levels deep
    *) return 1 ;;
  esac
  case "$real/" in
    /bin/*|/boot/*|/dev/*|/etc/*|/lib/*|/lib32/*|/lib64/*|/proc/*|/run/*|/sbin/*|/sys/*|/usr/*) return 1 ;;
  esac
  return 0
}
# purge_dir <path> <description>: rm -rf behind the guard, and say so when the
# target is missing — a purge that silently does nothing reads as a success.
purge_dir() {
  local p="$1" desc="$2"
  if [[ ! -e "$p" && ! -L "$p" ]]; then
    say "  not found at $p — nothing to purge"
  elif ! _rm_target_ok "$p"; then
    say "  REFUSE rm -rf $p — resolves to $(realpath -m -- "$p" 2>/dev/null), which is not a"
    say "         data directory this script may delete (root, a system tree, \$HOME, the"
    say "         checkout, or a parent of one). Nothing removed; remove it by hand."
  else
    do_ "rm -rf $p  ($desc)" -- rm -rf -- "$p"
  fi
}

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
for u in openbeast.service openbeast-watchdog.timer openbeast-watchdog.service \
         openbeast-logrotate.timer openbeast-logrotate.service; do
  if [[ -e "$UNIT_DIR/$u" ]]; then
    do_ "systemctl --user disable --now $u" -- systemctl --user disable --now "$u"
    do_ "rm $UNIT_DIR/$u" -- rm -f "$UNIT_DIR/$u"
  fi
done
do_ "systemctl --user stop openbeast-stack (transient daemon scope, if any)" -- bash -c 'systemctl --user stop openbeast-stack 2>/dev/null; systemctl --user reset-failed openbeast-stack 2>/dev/null; true'
[[ $GO -eq 1 ]] && systemctl --user daemon-reload 2>/dev/null

step "4. Build, venv, runtime state"
# llama.cpp/ is a build tree — and, in this project's own workflow, where
# llama.cpp patches are developed on local branches. Local-only commits,
# stashes and uncommitted edits exist nowhere else, so they keep the tree
# unless --purge-build says otherwise. A tree git cannot inspect is kept too.
_llama_local_work() {               # prints what would be lost; rc 0 = there is some
  local d="$REPO_DIR/llama.cpp" out
  [[ -e "$d/.git" ]] || return 1    # not a clone (e.g. a bundle's source tarball)
  if ! out="$(git -C "$d" log --branches --not --remotes --oneline 2>/dev/null)"; then
    echo "git cannot read the clone, so local work cannot be ruled out"; return 0
  fi
  [[ -n "$out" ]] && echo "$(wc -l <<< "$out") commit(s) on local branches that no remote has"
  local st; st="$(git -C "$d" status --porcelain 2>/dev/null)" || st="?"
  [[ -n "$st" ]] && echo "uncommitted changes or untracked files"
  local sl; sl="$(git -C "$d" stash list 2>/dev/null)" || sl=""
  [[ -n "$sl" ]] && echo "$(wc -l <<< "$sl") stash entr(y/ies)"
  [[ -n "$out$st$sl" ]]
}
if [[ -e "$REPO_DIR/llama.cpp" ]]; then
  if _lw="$(_llama_local_work)" && [[ $PB -eq 0 ]]; then
    keep "llama.cpp/ — it holds local work that exists nowhere else:"
    while IFS= read -r _l; do say "           - $_l"; done <<< "$_lw"
    say "         (git -C llama.cpp log --branches --not --remotes shows it; push or"
    say "          export it, or add --purge-build to remove the tree anyway)"
  else
    do_ "rm -rf llama.cpp/" -- rm -rf -- "$REPO_DIR/llama.cpp"
  fi
fi
[[ -e "$REPO_DIR/venv" ]] && do_ "rm -rf venv/" -- rm -rf -- "$REPO_DIR/venv"
# .run/ mixes process ephemera with DURABLE state: clients.json (beast-gate's
# device registry — a key is shown once and cannot be recovered), the
# *-audit.jsonl trails, artifact-raw.key (every kept artifact's /raw URL is
# signed with it), sessions/ (the job/chat ledger), ssd-wear.json. Without
# --purge-state only the ephemera go — pidfiles, locks, per-process tokens,
# sockets, logs — and everything else is listed as kept.
RUN="$REPO_DIR/.run"
if [[ -d "$RUN" ]]; then
  if [[ $PS -eq 1 ]]; then
    do_ "rm -rf .run/  (--purge-state: device registry, audit logs, keys, ledger, everything)" -- rm -rf -- "$RUN"
  else
    _eph=(); _kept=()
    shopt -s nullglob dotglob
    for _f in "$RUN"/*; do
      _b="${_f##*/}"
      if [[ -f "$_f" || -S "$_f" ]]; then
        case "$_b" in
          *.pid|*.lock|*.token|*.sock|*.log|*.log.[0-9]*|serve-script) _eph+=("$_f"); continue ;;
        esac
      fi
      _kept+=("$_b")
    done
    shopt -u nullglob dotglob
    if [[ ${#_eph[@]} -gt 0 ]]; then
      do_ "rm .run/ ephemera: ${#_eph[@]} pidfile/lock/token/log file(s)" -- rm -f -- "${_eph[@]}"
    fi
    if [[ $PD -eq 1 && -d "$RUN/sessions" ]]; then
      do_ "rm -rf .run/sessions/  (the session ledger — --purge-data)" -- rm -rf -- "$RUN/sessions"
      _k2=(); for _b in "${_kept[@]}"; do [[ "$_b" == sessions ]] || _k2+=("$_b"); done
      _kept=("${_k2[@]}")
    fi
    if [[ ${#_kept[@]} -gt 0 ]]; then
      _named=()
      for _b in "${_kept[@]}"; do
        case "$_b" in
          clients.json|clients-lastseen.json|*-audit.jsonl*|artifact-raw.key|ssd-wear.json|sessions|chat-operators) _named+=("$_b") ;;
        esac
      done
      keep ".run/ durable state — ${#_kept[@]} entr(y/ies)${_named[*]:+, including: ${_named[*]}} — --purge-state removes it"
    fi
  fi
fi

step "5. Containers' images and data"
if command -v docker >/dev/null 2>&1; then
  # Only THIS compose project's volume: selected by the labels compose stamps
  # on it (the project is pinned to `openbeast` in docker-compose.yml), never
  # by a name pattern — another Open WebUI install's `<project>_open-webui-data`
  # is someone else's chats. Look-alikes are listed and kept.
  _ours="$(docker volume ls -q --filter label=com.docker.compose.project=openbeast \
             --filter label=com.docker.compose.volume=open-webui-data 2>/dev/null || true)"
  _alike="$(docker volume ls -q 2>/dev/null | grep -E 'open-webui-data$' || true)"
  if [[ $PD -eq 1 ]]; then
    for v in $_ours; do
      do_ "docker volume rm $v  (Open WebUI: chats, accounts, settings)" -- docker volume rm "$v"
    done
    [[ -n "$_ours" ]] || say "  no volume labelled com.docker.compose.project=openbeast — nothing to purge"
  else
    keep "Open WebUI's data volume (chats, accounts) — --purge-data removes it"
  fi
  for v in $_alike; do
    grep -qxF -- "$v" <<< "$_ours" && continue
    keep "docker volume $v — not labelled as this compose project's; never removed here"
    [[ "$v" == models_open-webui-data ]] && say "         (the pre-rename OpenBeast project was \`models\` — if this is yours: docker volume rm $v)"
  done
  say "  (container images are left for docker to prune: docker image prune -a)"
else
  say "  docker not installed — skipping"
fi

step "6. The workspace — $FILES_DIR"
if [[ $PD -eq 1 ]]; then
  purge_dir "$FILES_DIR" "per-user shards, artifacts"
else
  keep "$FILES_DIR (what the model wrote for you, every published page) — --purge-data removes it"
fi

step "7. Model weights — $WEIGHTS_DIR"
if [[ $PW -eq 1 ]]; then
  purge_dir "$WEIGHTS_DIR" "$(du -sh "$WEIGHTS_DIR" 2>/dev/null | cut -f1 || echo '?')"
else
  keep "$WEIGHTS_DIR ($(du -sh "$WEIGHTS_DIR" 2>/dev/null | cut -f1 || echo '?') — the expensive part to re-download) — --purge-weights removes it"
fi

step "8. openbeast.conf"
if [[ $PC -eq 1 ]]; then
  if [[ -f "$REPO_DIR/openbeast.conf" ]]; then
    do_ "rm openbeast.conf  (per-install secrets: SearXNG, JWT, API keys)" -- rm -f "$REPO_DIR/openbeast.conf"
  else
    say "  not found at $REPO_DIR/openbeast.conf — nothing to purge"
  fi
else
  keep "openbeast.conf (a reinstall picks up where you left off) — --purge-conf removes it"
fi

say ""
if [[ $GO -eq 1 ]]; then
  say "Done. The checkout at $REPO_DIR is yours to delete: rm -rf \"$REPO_DIR\""
else
  say "Nothing was removed. Add --go to execute, and --purge-weights / --purge-data / --purge-state / --purge-conf (or --purge-all) for the parts kept by default."
fi
