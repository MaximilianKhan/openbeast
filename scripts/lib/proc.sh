#!/bin/bash
# Process helpers shared by stop.sh and scripts/healthcheck.sh — the two
# scripts that SIGNAL things, where "which process is this" is the whole
# question. Sourced, never executed; defines functions only (no `set --`, no
# shell-option changes, no output).

# Quote a literal for use inside an ERE. `pkill -f` / `pgrep -f` take an
# EXTENDED REGEX: a `+` in the repo path makes a path-anchored pattern fail to
# match its own process (the reap silently does nothing), and a `.` makes it
# match other paths (the sibling-worktree reap).
_ob_ere() { printf '%s' "$1" | sed -E 's/[][(){}.^$*+?|\]/\\&/g'; }

# ob_pid_matches <pid> <ERE> — alive AND its command line matches.
#
# A pidfile is a number on disk, and .run/ survives a reboot: after a power
# loss the recorded pid is plausibly alive again as SOMETHING ELSE of the same
# user, and `kill -0 $pid && kill $pid` then SIGTERMs a stranger — from the
# watchdog timer, with no human involved. start.sh has always refused to do
# that (_pid_alive); the two scripts that actually send the signals did not.
# Unreadable command line (exotic /proc) -> plain liveness, as start.sh does.
ob_pid_matches() {
  local pid="${1:-}" pat="${2:-}" cmd=""
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ "$pid" -gt 1 ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  if [[ -r "/proc/$pid/cmdline" ]]; then
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  else
    cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  fi
  [[ -z "$cmd" ]] && return 0
  [[ "$cmd" =~ $pat ]]
}

# ob_pid_age <pid> — seconds since the process started, or nothing.
ob_pid_age() {
  local age
  age="$(ps -o etimes= -p "${1:-0}" 2>/dev/null | tr -d ' ' || true)"
  [[ "$age" =~ ^[0-9]+$ ]] && printf '%s\n' "$age"
  return 0
}
