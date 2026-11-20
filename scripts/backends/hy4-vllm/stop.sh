#!/usr/bin/env bash
# Stop Hy4-flash (vLLM TP2) on both Sparks: rank 0 here, then rank 1 on the
# worker; both containers removed. Idempotent (nothing running: exit 0). The
# unit's ExecStop; `beast-spark ds|glm|down` stop the unit, which runs this.
# Each memguard.sh exits by itself once its container is gone. Touches no
# weights, no image and no cache.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"
# shellcheck disable=SC2034  # hy4_log's prefix
HY4_TAG=hy4-stop
[[ "$HY4_CONTAINER" =~ ^[A-Za-z0-9._-]+$ && "$HY4_WORKER" =~ ^[A-Za-z0-9.@_-]+$ ]] || hy4_die "HY4_CONTAINER / HY4_WORKER have unexpected characters"
GRACE="${HY4_STOP_GRACE_S:-60}"
[[ "$GRACE" =~ ^[0-9]+$ ]] || GRACE=60
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
