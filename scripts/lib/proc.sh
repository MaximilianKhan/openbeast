#!/bin/bash
# Process helpers shared by start.sh, stop.sh, scripts/healthcheck.sh and
# scripts/ext.sh — the scripts that SIGNAL things or trust a pidfile, where
# "which process is this" is the whole question. Sourced, never executed;
# defines functions only (no `set --`, no shell-option changes, no output).

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

# ob_pid_start <pid> — when the process started, as the kernel recorded it
# (/proc/<pid>/stat field 22, clock ticks since boot; `ps -o lstart=` where
# there is no /proc). Prints nothing for a pid that is gone. exec() does not
# change it, so the value read right after a fork identifies the server the
# child then becomes.
ob_pid_start() {
  local pid="${1:-}" stat f
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  if stat="$(cat "/proc/$pid/stat" 2>/dev/null)" && [[ -n "$stat" ]]; then
    # Field 2 (comm) may hold spaces and parens; the LAST ") " ends it, and
    # field 3 onwards follow — so field 22 is index 19 of the remainder.
    read -r -a f <<< "${stat##*) }"
    [[ -n "${f[19]:-}" ]] && printf '%s\n' "${f[19]}"
  else
    ps -o lstart= -p "$pid" 2>/dev/null | tr -s ' ' || true
  fi
  return 0
}

# ob_pid_record <pidfile> <pid> — write the pid AND its start time, the
# latter to <pidfile minus .pid>.start. The sidecar is written first, so a
# reader never pairs a new pid with an old start time.
ob_pid_record() {
  local f="$1" pid="$2"
  ob_pid_start "$pid" > "${f%.pid}.start" 2>/dev/null || true
  printf '%s\n' "$pid" > "$f"
}

# ob_recorded_pid_ours <pidfile> <ERE> — the recorded pid is alive AND is the
# very process that was recorded. With a start-time sidecar (ob_pid_record)
# that is exact: a recycled pid cannot also carry the recorded start time.
# A command-line ERE cannot be exact — "start\.sh" also matches another
# project's ./start.sh or `vim start.sh`, and after a reboot stop.sh sent
# such a process SIGTERM and then SIGKILL — so the ERE is only the fallback
# for a pidfile written before the sidecar existed.
ob_recorded_pid_ours() {
  local f="$1" pat="${2:-}" pid rec now
  pid="$(cat "$f" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ "$pid" -gt 1 ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  rec="$(cat "${f%.pid}.start" 2>/dev/null || true)"
  if [[ -n "$rec" ]]; then
    now="$(ob_pid_start "$pid")"
    if [[ -n "$now" ]]; then
      [[ "$now" == "$rec" ]]
      return
    fi
  fi
  ob_pid_matches "$pid" "$pat"
}

# ob_ext_reap <pidfile> <extension-dir> — stop a process-kind extension. By
# its recorded pid while that pid is still the extension (never a bare kill:
# .run/ survives a reboot, and the number may now belong to the user's
# editor); otherwise by a sweep anchored to an interpreter running a file IN
# that extension's directory — which finds an orphan the record lost, and
# cannot match `vim <dir>/x.py` or a sibling worktree. Removes the record.
ob_ext_reap() {
  local f="$1" dir="${2%/}" name pid pat
  name="$(basename "$f" .pid)"; name="${name#ext-}"
  pid="$(cat "$f" 2>/dev/null || true)"
  pat="^(([^ ]*/)?(python[0-9.]*|bash|sh|node) )?$(_ob_ere "$dir/")"
  if ob_recorded_pid_ours "$f" "$(_ob_ere "$dir/")"; then
    kill "$pid" 2>/dev/null && echo "extension stopped ($name)."
  elif pkill -f "$pat" 2>/dev/null; then
    echo "extension stopped ($name, by path; no live recorded pid)."
  fi
  rm -f "$f" "${f%.pid}.start"
  return 0
}
