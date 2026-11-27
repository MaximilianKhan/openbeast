#!/bin/bash
# scripts/setup-tailscale.sh --publish-slot: the :8444 mount never falls open.
#
# Usage: ./tests/test_publish_slot.sh
#
# The two functions behind --publish-slot (_ts_has_set_path,
# _publish_slot_mount) are lifted out of the script and run against a stub
# `tailscale` / `ts_admin` — the rest of setup-tailscale.sh needs systemd,
# sudo and a live tailnet, and none of that decides which mount is made.
#
#   - a build WITH --set-path mounts /api/slot only; if that mount FAILS
#     (tailscaled hiccup, cancelled sudo prompt) nothing wider is mounted
#     (it used to fall back to the whole dashboard on any failure)
#   - only a build with no --set-path at all gets the whole-dashboard mount
#   - re-running on a :8444 that still has a "/" (whole dashboard) handler
#     removes it: tailscale keeps one handler per path, so adding /api/slot
#     alone used to leave the dashboard published (2026-11-16 review)

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_DIR/scripts/setup-tailscale.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== setup-tailscale.sh --publish-slot tests ==="

T="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-publish-slot.XXXXXX")"
trap 'rm -rf "$T"' EXIT

# Lift the function definitions (each runs from its name to the closing `}`).
awk '/^_ts_has_set_path\(\) \{/,/^}/' "$SRC" > "$T/fns.sh"
awk '/^_publish_slot_mount\(\) \{/,/^}/' "$SRC" >> "$T/fns.sh"
if ! grep -q '_publish_slot_mount "\$UP_HOST"' "$SRC" || [[ "$(grep -c '^}' "$T/fns.sh")" != 2 ]]; then
  fail "could not find _ts_has_set_path/_publish_slot_mount (or the --publish-slot call) in setup-tailscale.sh"
  echo "Results: $PASS passed, $FAIL failed"; exit 1
fi

# run <help-text-has-set-path 0|1> <set-path-mount-rc> [pre-existing handlers]
# Sets OUT, RC; $T/mounts records every ts_admin serve mount (not the
# reset), and $T/handlers models tailscale's per-path :8444 handler table.
run() {
  : > "$T/mounts"
  printf '%s' "${3:-}" > "$T/handlers"
  OUT="$(HAS="$1" MOUNT_RC="$2" MOUNTS="$T/mounts" HANDLERS="$T/handlers" bash -c '
    set -euo pipefail
    tailscale() {
      [[ "$*" == "serve --help" ]] || return 0
      echo "USAGE"; echo "  tailscale serve [flags] <target>"
      [[ "$HAS" == 1 ]] && echo "  --set-path value"
      echo "  --bg"; for _i in $(seq 1 400); do echo "  filler line $_i"; done
      return 0
    }
    ts_admin() {
      case "$*" in
        "serve --https=8444 off") : > "$HANDLERS"; return 0 ;;
      esac
      echo "$*" >> "$MOUNTS"
      case "$*" in
        *--set-path=*)
          [[ "$MOUNT_RC" == 0 ]] || return "$MOUNT_RC"
          echo "/api/slot" >> "$HANDLERS" ;;
        *) echo "/" >> "$HANDLERS" ;;
      esac
      return 0
    }
    . "$1"
    _publish_slot_mount 127.0.0.1
    echo AFTER
  ' _ "$T/fns.sh" 2>&1)"
  RC=$?
}

run 1 0
if [[ $RC -eq 0 ]] && grep -q -- '--set-path=/api/slot http://127.0.0.1:3002/api/slot' "$T/mounts" \
   && [[ "$(wc -l < "$T/mounts")" -eq 1 ]] && [[ "$OUT" == *":8444/api/slot"* ]]; then
  pass "a build with --set-path mounts /api/slot only"
else
  fail "set-path mount (rc=$RC, mounts=$(tr '\n' ';' < "$T/mounts")): $OUT"
fi

run 1 1
if [[ $RC -ne 0 ]] && ! grep -q 'http://127.0.0.1:3002$' "$T/mounts" && [[ "$OUT" != *AFTER* ]] \
   && [[ "$OUT" == *"NOT falling back"* ]]; then
  pass "a FAILED path-scoped mount is an error — the whole dashboard is never mounted"
else
  fail "failed set-path mount fell open (rc=$RC, mounts=$(tr '\n' ';' < "$T/mounts")): $OUT"
fi

run 1 0 $'/\n'
if [[ $RC -eq 0 ]] && [[ "$(cat "$T/handlers")" == "/api/slot" ]]; then
  pass "re-running over a stale whole-dashboard \"/\" handler leaves /api/slot alone"
else
  fail "a stale \"/\" handler on :8444 survived --publish-slot (handlers=$(tr '\n' ';' < "$T/handlers")): $OUT"
fi

run 1 1 $'/\n'
if [[ $RC -ne 0 ]] && [[ ! -s "$T/handlers" ]]; then
  pass "a failed re-mount leaves :8444 unpublished, not the stale whole dashboard"
else
  fail "a failed re-mount left :8444 handlers=$(tr '\n' ';' < "$T/handlers"): $OUT"
fi

run 0 0
if [[ $RC -eq 0 ]] && grep -q 'http://127.0.0.1:3002$' "$T/mounts" && ! grep -q -- '--set-path' "$T/mounts" \
   && [[ "$OUT" == *"has no --set-path"* ]]; then
  pass "control: only a build with no --set-path gets the whole-dashboard mount, and says so"
else
  fail "no-set-path fallback (rc=$RC, mounts=$(tr '\n' ';' < "$T/mounts")): $OUT"
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
