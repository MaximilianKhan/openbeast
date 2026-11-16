#!/bin/bash
# Per-profile memory scope for `./start.sh -d` (docs/WEIGHT_STREAMING.md).
# Sourced by start.sh and tests; defines functions only (no output, no
# shell-option changes at source time).
#
# The daemon runs in a systemd scope capped at MEM_LIMIT_PCT of RAM (75 by
# default) so a runaway process OOMs the stack, never the box. A big-mode
# profile keeps ~95 GiB of MoE experts in host RAM, which no 75% scope of a
# 128 GB rig holds. So the scope is raised PER PROFILE, never globally: a
# serve script that needs more says so in one header line,
#
#   # openbeast-scope: mem-limit-pct=90 memlock=infinity host-class=rig min-mem-gib=118
#
# and only a launch of THAT script on a host of THAT class gets it. Every
# other launch keeps MEM_LIMIT_PCT (75). Keys:
#   mem-limit-pct  1-95   the scope's MemoryMax as a percent of MemTotal
#   memlock        infinity | -   LimitMEMLOCK for the scope (pinned RAM tiers)
#   host-class     rig | spark | mac   refuse on any other class
#   min-mem-gib    N      refuse when MemTotal is below N GiB (a 64 GB box
#                         cannot hold a big model, so it must not try)
# Unknown keys or malformed values are an error, never ignored.

# ob_scope_decl <file> — the declaration's key=value words, or nothing.
ob_scope_decl() {
  local f="$1" line
  [[ -f "$f" ]] || return 0
  line="$(grep -m1 -E '^#[[:space:]]*openbeast-scope:' "$f" 2>/dev/null || true)"
  [[ -n "$line" ]] || return 0
  printf '%s\n' "${line#*openbeast-scope:}"
}

# ob_mem_scope <serve-script> <host-class> <mem-total-kb> <default-pct> [env-pct]
#   Prints "PCT MEMLOCK SOURCE" (MEMLOCK is "infinity" or "-"; SOURCE is
#   "conf", "env" or "profile:<script name>") and returns 0.
#   Returns 2 with the reason on stderr when the profile refuses this host.
#   env-pct is OPENBEAST_MEM_LIMIT_PCT as the caller exported it: an explicit
#   per-launch override, which wins over the profile (the profile still
#   decides memlock and still refuses a wrong host).
ob_mem_scope() {
  local script="$1" cls="$2" kb="$3" def="$4" envp="${5:-}" name decl w k v
  local pct="" memlock="-" want_cls="" min_gib="" src="conf"
  name="$(basename "$script")"
  decl="$(ob_scope_decl "$script")"
  for w in $decl; do
    [[ "$w" == *=* ]] || { echo "$name: openbeast-scope word '$w' is not key=value" >&2; return 2; }
    k="${w%%=*}"; v="${w#*=}"
    case "$k" in
      mem-limit-pct)
        [[ "$v" =~ ^[0-9]+$ && "$v" -ge 1 && "$v" -le 95 ]] \
          || { echo "$name: openbeast-scope mem-limit-pct=$v (want 1-95)" >&2; return 2; }
        pct="$v" ;;
      memlock)
        [[ "$v" == infinity || "$v" == - ]] \
          || { echo "$name: openbeast-scope memlock=$v (want infinity or -)" >&2; return 2; }
        memlock="$v" ;;
      host-class)
        [[ "$v" =~ ^(rig|spark|mac)$ ]] \
          || { echo "$name: openbeast-scope host-class=$v (want rig, spark or mac)" >&2; return 2; }
        want_cls="$v" ;;
      min-mem-gib)
        [[ "$v" =~ ^[0-9]+$ ]] || { echo "$name: openbeast-scope min-mem-gib=$v (want a whole number)" >&2; return 2; }
        min_gib="$v" ;;
      *) echo "$name: unknown openbeast-scope key '$k'" >&2; return 2 ;;
    esac
  done
  if [[ -n "$want_cls" && "$cls" != "$want_cls" ]]; then
    echo "$name is a $want_cls-only profile (it raises the memory scope for a model that needs that box); this host is a '$cls'." >&2
    return 2
  fi
  if [[ -n "$min_gib" && "$kb" =~ ^[0-9]+$ && $(( kb / 1024 / 1024 )) -lt "$min_gib" ]]; then
    echo "$name needs at least $min_gib GiB of RAM; this host has $(( kb / 1024 / 1024 )) GiB. It would not fit: serve it from a bigger box." >&2
    return 2
  fi
  if [[ -n "$pct" ]]; then src="profile:$name"; else pct="$def"; fi
  if [[ -n "$envp" ]]; then pct="$envp"; src="env"; fi
  printf '%s %s %s\n' "$pct" "$memlock" "$src"
}

# ob_memlock_hard — the calling session's hard RLIMIT_MEMLOCK ("unlimited"
# or KiB). A user unit cannot raise its limit above the user manager's, so a
# LimitMEMLOCK=infinity request is only passed when this says unlimited.
ob_memlock_hard() { ulimit -H -l 2>/dev/null || echo 0; }
