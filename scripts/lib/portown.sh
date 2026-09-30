#!/bin/bash
# Who holds a TCP listening port — for start.sh and scripts/healthcheck.sh,
# which used to call a service "ready" whenever SOMETHING answered its health
# route. Sourced, never executed; defines functions only (no `set --`, no
# shell-option changes, no output unless asked).
#
# Why it exists (2026-09-29 review, integration-ops-6): a freshly spawned
# chat/artifact server takes ~0.2 s to discover its port is taken and exit.
# The readiness loop checked `kill -0 $PID` (still alive), then curled the
# port — and the orphan or sibling-worktree server already holding it
# answered. start.sh printed "ready", recorded a pid that died a moment
# later, and the watchdog never repaired it because health stayed green. The
# answering server had a different store and locality token, so every
# artifact.sh write 404'd.
#
# Three sources, best first: `ss -p` (iproute2), `lsof`, then /proc/net/tcp*
# plus /proc/<pid>/fd socket inodes. Without any of them the answer is
# "unknown" and callers fall back to a delayed liveness check.

# ob_port_pids <port> — pids holding a LISTEN socket on <port> (any local
# address), one per line. Empty when nothing listens OR when the holder
# belongs to another user (unprivileged tools cannot name it). Return 2 when
# no source could be consulted at all.
ob_port_pids() {
  local port="${1:-}" out inodes ino fd p
  [[ "$port" =~ ^[0-9]+$ ]] || return 2
  if command -v ss >/dev/null 2>&1; then
    out="$(ss -Hltnp "sport = :$port" 2>/dev/null)" || out=""
    printf '%s\n' "$out" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u
    return 0
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u
    return 0
  fi
  [[ -r /proc/net/tcp ]] || return 2
  # /proc/net/tcp{,6}: local_address is HEX_IP:HEX_PORT, st 0A = LISTEN,
  # field 10 is the socket inode.
  inodes="$(awk -v p="$(printf '%04X' "$port")" \
    'FNR > 1 { split($2, a, ":"); if (a[2] == p && $4 == "0A") print $10 }' \
    /proc/net/tcp /proc/net/tcp6 2>/dev/null | sort -u)"
  [[ -n "$inodes" ]] || return 0
  for fd in /proc/[0-9]*/fd/*; do
    ino="$(readlink "$fd" 2>/dev/null)" || continue
    case "$ino" in socket:\[*\]) ;; *) continue ;; esac
    ino="${ino#socket:[}"; ino="${ino%]}"
    if printf '%s\n' "$inodes" | grep -qx "$ino"; then
      p="${fd#/proc/}"; printf '%s\n' "${p%%/*}"
    fi
  done | sort -u
  return 0
}

# ob_port_listening <port> — 0 when something LISTENs on <port>. Uses ss /
# /proc when available (they see a listener of another user too), else a
# loopback connect.
ob_port_listening() {
  local port="${1:-}"
  [[ "$port" =~ ^[0-9]+$ ]] || return 1
  if command -v ss >/dev/null 2>&1; then
    [[ -n "$(ss -Hltn "sport = :$port" 2>/dev/null)" ]]
    return
  fi
  if [[ -r /proc/net/tcp ]]; then
    awk -v p="$(printf '%04X' "$port")" \
      'FNR > 1 { split($2, a, ":"); if (a[2] == p && $4 == "0A") f = 1 } END { exit !f }' \
      /proc/net/tcp /proc/net/tcp6 2>/dev/null
    return
  fi
  (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null
}

# ob_pid_owns_port <pid> <port> — 0: <pid> holds the listener. 1: someone
# else does (or nothing does). 2: cannot tell on this box.
ob_pid_owns_port() {
  local pid="${1:-}" port="${2:-}" pids rc=0
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  pids="$(ob_port_pids "$port")" || rc=$?
  [[ $rc -eq 2 ]] && return 2
  printf '%s\n' "$pids" | grep -qx "$pid"
}
