#!/usr/bin/env bash
# Stop Hy4-flash (vLLM TP2) on both Sparks: rank 0 here, then rank 1 on the
# worker; both containers removed. Idempotent (nothing running: exit 0). The
# unit's ExecStop; `beast-spark ds|glm|down` stop the unit, which runs this.
# Each memguard.sh exits by itself once its container is gone. Touches no
# weights, no image and no cache; writes only HY4_STOP_MARK (config.sh), which
# tells the unit's start.sh that the stop is deliberate (exit 0, not "failed").
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"
# shellcheck disable=SC2034  # hy4_log's prefix
HY4_TAG=hy4-stop
[[ "$HY4_CONTAINER" =~ ^[A-Za-z0-9._-]+$ && "$HY4_WORKER" =~ ^[A-Za-z0-9.@_-]+$ ]] || hy4_die "HY4_CONTAINER / HY4_WORKER have unexpected characters"
GRACE="${HY4_STOP_GRACE_S:-60}"
[[ "$GRACE" =~ ^[0-9]+$ ]] || GRACE=60
# First the mark: systemd runs this (ExecStop) while start.sh still supervises, and
# start.sh sees the ranks end before any SIGTERM reaches it; the mark tells it the
# end is deliberate, so it exits 0 and the unit ends "inactive", not "failed".
# It records when, and each rank's state BEFORE this stop ("rankN=<Running> <ExitCode>",
# "gone -" / "unknown -" when there is no container or no answer): start.sh polls rank 1
# only every third poll, so a rank that crashed just before a stop must still read as a
# crash (rankN=false ...), not as this stop. Written whole (temp file, then mv).
r0=$("$HY4_DOCKER" inspect -f '{{.State.Running}} {{.State.ExitCode}}' "$HY4_CONTAINER" 2>/dev/null || echo "gone -")
r1=$("$HY4_SSH" -o BatchMode=yes -o ConnectTimeout=10 "$HY4_WORKER" \
     "docker inspect -f '{{.State.Running}} {{.State.ExitCode}}' $HY4_CONTAINER 2>/dev/null || echo 'gone -'" 2>/dev/null || echo "unknown -")
if mkdir -p "$(dirname "$HY4_STOP_MARK")" 2>/dev/null &&
   printf '%s\nrank0=%s\nrank1=%s\n' "$(date +%s)" "${r0:-gone -}" "${r1:-unknown -}" > "$HY4_STOP_MARK.tmp.$$" 2>/dev/null &&
   mv -f "$HY4_STOP_MARK.tmp.$$" "$HY4_STOP_MARK" 2>/dev/null; then :
else
  rm -f "$HY4_STOP_MARK.tmp.$$" 2>/dev/null
  hy4_log "WARNING: could not write $HY4_STOP_MARK: the unit may end \"failed\" after this stop"
fi
rc=0
if "$HY4_DOCKER" container inspect "$HY4_CONTAINER" >/dev/null 2>&1; then
  hy4_log "stopping rank 0 ($HY4_CONTAINER)"
  "$HY4_DOCKER" stop -t "$GRACE" "$HY4_CONTAINER" >/dev/null 2>&1 || true
  "$HY4_DOCKER" rm -f "$HY4_CONTAINER" >/dev/null 2>&1 || rc=1
fi
if ! "$HY4_SSH" -o BatchMode=yes -o ConnectTimeout=10 "$HY4_WORKER" \
     "if docker container inspect $HY4_CONTAINER >/dev/null 2>&1; then docker stop -t $GRACE $HY4_CONTAINER >/dev/null 2>&1; docker rm -f $HY4_CONTAINER >/dev/null; fi"; then
  hy4_log "WARNING: could not stop rank 1 on $HY4_WORKER (ssh failed): ssh $HY4_WORKER docker rm -f $HY4_CONTAINER"
  rc=1
fi
exit "$rc"
