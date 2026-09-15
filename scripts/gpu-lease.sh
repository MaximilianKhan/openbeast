#!/bin/bash
# An exclusive lease on the GPU, so two things cannot measure at once.
#
#   ./scripts/gpu-lease.sh run "T1.17 pair" -- bash scratch/campaign_master2.sh
#   ./scripts/gpu-lease.sh status
#   ./scripts/gpu-lease.sh acquire "manual work" && ... && ./scripts/gpu-lease.sh release
#
# WHY (docs/BEAST_CAMPAIGN_PLAN.md §1). Nothing on this box has ever claimed
# the GPU. The consequences are documented and expensive:
#   * 2026-09-14 — six parallel build agents ran inside a measurement's window
#     and exhausted OpenBLAS's thread budget, contaminating 5 eval units
#     asymmetrically, in the direction that flattered the result.
#   * 2026-09-15 — the operator took the card while a cell was still running;
#     it worked only because a human was watching and asked first.
#
# IDENTITY IS pid + START TIME, never pid alone. The kernel recycles pids, and
# this repo has already been bitten by that in the session ledger
# (agents/sessions.py) — a recycled pid made a lease look live, or worse, made
# a signal land on a stranger. Same rule here.
#
# A lease is ADVISORY, and deliberately so: it cannot stop a process that does
# not ask. What it can do is give everything that DOES ask a truthful answer,
# which is all the 09-14 contamination needed — the build agents did not
# ignore a lease, they had nothing to consult.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${OPENBEAST_RUN_DIR:-$SCRIPT_DIR/.run}"
LEASE="$RUN_DIR/gpu.lease"
VRAM_FLOOR_MIB="${OPENBEAST_LEASE_VRAM_FLOOR:-4000}"

say()  { printf '%s\n' "$*"; }
warn() { printf '! %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

# /proc/<pid>/stat field 22 — start time in clock ticks since boot. Cheap,
# and it is what makes a pid an identity rather than a number.
_pid_start() {
  local pid="$1"
  [[ -r "/proc/$pid/stat" ]] || return 1
  awk '{ n = split($0, a, ") "); split(a[n], f, " "); print f[20] }' \
    "/proc/$pid/stat" 2>/dev/null
}

_holder_alive() {                 # _holder_alive <pid> <start> -> 0 if the SAME process
  local pid="$1" want="$2" got
  [[ -n "$pid" && "$pid" -gt 1 ]] 2>/dev/null || return 1
  got="$(_pid_start "$pid")" || return 1
  [[ -n "$got" && "$got" == "$want" ]]
}

_read_lease() {                   # sets LH_PID LH_START LH_LABEL LH_SINCE
  LH_PID=""; LH_START=""; LH_LABEL=""; LH_SINCE=""
  [[ -f "$LEASE" ]] || return 1
  # shellcheck disable=SC2034
  while IFS='=' read -r k v; do
    case "$k" in
      pid)   LH_PID="$v" ;;
      start) LH_START="$v" ;;
      label) LH_LABEL="$v" ;;
      since) LH_SINCE="$v" ;;
    esac
  done < "$LEASE"
  [[ -n "$LH_PID" ]]
}

_vram_used() {
  nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null \
    | head -1 | tr -d ' ' || echo 0
}

cmd_status() {
  local used; used="$(_vram_used)"
  if _read_lease && _holder_alive "$LH_PID" "$LH_START"; then
    say "HELD by pid $LH_PID — ${LH_LABEL:-unlabelled}  (since ${LH_SINCE:-?})"
    say "  GPU: ${used:-?} MiB in use"
    return 0
  fi
  if [[ -f "$LEASE" ]]; then
    say "FREE (a stale lease from pid ${LH_PID:-?} is on disk; it will be taken over)"
  else
    say "FREE"
  fi
  say "  GPU: ${used:-?} MiB in use"
  # A free lease with the card full is worth saying out loud: something is
  # using the GPU without claiming it, which is exactly the 09-14 situation.
  if [[ "${used:-0}" -gt "$VRAM_FLOOR_MIB" ]] 2>/dev/null; then
    warn "no lease is held but ${used} MiB is allocated — something is using the"
    warn "  GPU without claiming it. Check: nvidia-smi --query-compute-apps=pid,used_memory,name --format=csv"
  fi
  return 0
}

# WHO holds a lease depends on how it was taken, and getting this wrong made
# `acquire` useless: it recorded THIS script's pid, which dies the moment the
# command returns, so the lease was stale before the caller ran a single line
# — while the usage example in this header promised otherwise.
#
#   acquire  -> the CALLER holds it ($PPID): an interactive shell, or the
#               script that invoked us. It outlives this process, which is
#               the entire point.
#   run      -> WE hold it ($$): this shell stays alive for the command's
#               duration and the EXIT trap releases it.
HOLDER_PID="$$"

cmd_acquire() {                   # cmd_acquire <label> [--wait SECONDS] [--force]
  local label="${1:-unlabelled}" wait_s=0 force=0
  HOLDER_PID="${PPID:-$$}"
  shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --wait)  wait_s="${2:?--wait needs seconds}"; shift 2 ;;
      --force) force=1; shift ;;
      *) die "unknown option: $1" ;;
    esac
  done
  mkdir -p "$RUN_DIR"
  local waited=0
  while :; do
    if _read_lease && _holder_alive "$LH_PID" "$LH_START"; then
      if [[ $force -eq 1 ]]; then
        warn "taking the lease from LIVE pid $LH_PID (${LH_LABEL:-unlabelled}) — --force"
        break
      fi
      if [[ "$waited" -ge "$wait_s" ]]; then
        say "GPU is held by pid $LH_PID — ${LH_LABEL:-unlabelled} (since ${LH_SINCE:-?})" >&2
        return 4
      fi
      sleep 5; waited=$((waited + 5)); continue
    fi
    [[ -f "$LEASE" ]] && [[ -n "${LH_PID:-}" ]] \
      && say "taking over a stale lease (pid $LH_PID is gone)"
    break
  done
  # Refuse to claim a card somebody else is quietly using: a lease that says
  # "mine" over 24 GB of someone else's weights is worse than no lease.
  local used; used="$(_vram_used)"
  if [[ $force -eq 0 && "${used:-0}" -gt "$VRAM_FLOOR_MIB" ]] 2>/dev/null; then
    say "refusing: ${used} MiB already allocated on the GPU (floor ${VRAM_FLOOR_MIB})." >&2
    say "  Nothing holds the lease, so this is an unclaimed user. Stop it, or --force." >&2
    return 5
  fi
  local start; start="$(_pid_start "$HOLDER_PID")" || start="?"
  local tmp="$LEASE.$$"
  {
    printf 'pid=%s\n' "$HOLDER_PID"
    printf 'start=%s\n' "$start"
    printf 'label=%s\n' "$label"
    printf 'since=%s\n' "$(date -Is)"
  } > "$tmp"
  mv "$tmp" "$LEASE"
  say "lease acquired by pid $HOLDER_PID — $label"
}

cmd_release() {
  if ! _read_lease; then say "no lease to release"; return 0; fi
  if [[ "$LH_PID" != "$$" && "$LH_PID" != "${PPID:-}" ]] \
     && _holder_alive "$LH_PID" "$LH_START"; then
    die "the lease is held by pid $LH_PID, not by you ($$/${PPID:-?}) — refusing.
       Stop that process, or take it with: $0 acquire <label> --force"
  fi
  rm -f "$LEASE"
  say "lease released"
}

cmd_run() {                       # cmd_run <label> -- cmd...
  local label="${1:?usage: run <label> -- cmd...}"; shift
  [[ "${1:-}" == "--" ]] || die "expected -- before the command"
  shift
  [[ $# -gt 0 ]] || die "no command given"
  # The lease is held by THIS shell, and the trap releases it on any exit —
  # including the SIGTERM an operator sends to stop a campaign.
  cmd_acquire "$label" || exit $?
  # cmd_acquire recorded the CALLER; for `run` the holder is this shell, which
  # lives exactly as long as the command does.
  HOLDER_PID="$$"
  local start; start="$(_pid_start $$)" || start="?"
  { printf 'pid=%s\n' "$$"; printf 'start=%s\n' "$start"
    printf 'label=%s\n' "$label"; printf 'since=%s\n' "$(date -Is)"; } > "$LEASE"
  trap 'rm -f "$LEASE"' EXIT INT TERM
  "$@"
}

case "${1:-}" in
  status)  cmd_status ;;
  acquire) shift; cmd_acquire "$@" ;;
  release) cmd_release ;;
  run)     shift; cmd_run "$@" ;;
  -h|--help|"") sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command: $1 (status | acquire | release | run)" ;;
esac
