#!/usr/bin/env bash
# Start Hy4-flash (NVFP4) on vLLM, tensor parallel 2 with expert parallel:
# rank 1 on spark-2 (headless, over ssh) first, then rank 0 here on spark-1
# with the API on 127.0.0.1:8000. Run on spark-1 as the deployment's account,
# normally by the unit hy4-vllm-inference.service, which `beast-spark hy4`
# starts (never at boot: the unit has no [Install] section).
# docs/SPARK_HYDRA.md "Hy4-flash (NVFP4, vLLM TP2): measured"
#
#   start.sh             foreground (the unit): stays attached to rank 0's log,
#                        stops both ranks and exits non-zero when either ends
#                        (a crash: the unit shows "failed"); exits 0 when they end
#                        because stop.sh stopped them (HY4_STOP_MARK) or on SIGTERM/INT
#   start.sh --print     print the checks' results and both docker commands; start nothing
#   FOREGROUND=0 start.sh   start both ranks and return
#
# Refuses before anything starts: the weights or the image missing on either
# Spark, the weights not verified (spark-hy4-stage.sh writes .hy4-verified),
# something already listening on the port, no QSFP link, or less memory
# available than vLLM asks for (HY4_MIN_AVAIL_GIB, config.sh) on either Spark
# after HY4_MIN_AVAIL_WAIT_S. Offline: nothing reaches beyond the link
# (HF_HUB_OFFLINE=1 in both containers).
#
# The API key never reaches argv: it is exported as VLLM_API_KEY and docker
# copies it by name (-e VLLM_API_KEY), as scripts/backends/vllm/spark-node.sh does.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"
# shellcheck disable=SC2034  # hy4_log's prefix
HY4_TAG=hy4-start
log() { hy4_log "$@"; }
die() { hy4_die "$@"; }

PRINT=0
case "${1:-}" in
  --print|--dry-run) PRINT=1 ;;
  "") ;;
  -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown argument: $1 (see --help)" ;;
esac
FOREGROUND="${FOREGROUND:-1}"

hy4_check_settings
on_worker() { "$HY4_SSH" -o BatchMode=yes -o ConnectTimeout=10 "$HY4_WORKER" "$@"; }
dk() { "$HY4_DOCKER" "$@"; }
q() { printf '%q ' "$@"; }

# ---- 1. staged on both Sparks?
MARK="$HY4_MODEL_DIR/.hy4-verified"
[[ -f "$HY4_MODEL_DIR/config.json" ]] || die "no model at $HY4_MODEL_DIR: stage it first (scripts/backends/spark-hy4-stage.sh --run)"
grep -q "^revision=$HY4_REVISION\$" "$MARK" 2>/dev/null ||
  die "$HY4_MODEL_DIR is not verified at $HY4_REVISION ($MARK): scripts/backends/spark-hy4-stage.sh --run --only verify"
on_worker "grep -q '^revision=$HY4_REVISION\$' $(q "$MARK")" ||
  die "$HY4_WORKER: $HY4_MODEL_DIR is missing or not verified at $HY4_REVISION: scripts/backends/spark-hy4-stage.sh --run --only mirror"
dk image inspect "$HY4_IMAGE" >/dev/null 2>&1 || die "no image $HY4_IMAGE here: scripts/backends/spark-hy4-stage.sh --run --only image (or --image-from TAR offline)"
on_worker "docker image inspect $(q "$HY4_IMAGE") >/dev/null 2>&1" ||
  die "no image $HY4_IMAGE on $HY4_WORKER: scripts/backends/spark-hy4-stage.sh --run --only ship"
[[ -r "$HY4_KEY_FILE" ]] || die "no API key file $HY4_KEY_FILE (the key the gate sends; or set HY4_KEY_FILE)"
KEY=$(<"$HY4_KEY_FILE")
[[ "$KEY" =~ ^[A-Za-z0-9._~+/=-]{16,}$ ]] || die "the key in $HY4_KEY_FILE is empty, short or has unexpected characters"

# ---- 2. nothing else on the port (one model at a time: beast-spark hy4 stops the others first)
port_busy() {
  command -v ss >/dev/null 2>&1 || return 1
  [[ -n "$(ss -Hltn "sport = :$HY4_PORT" 2>/dev/null)" ]]
}
if (( ! PRINT )) && port_busy; then
  die "something already listens on :$HY4_PORT (another model?): stop it first (beast-spark hy4 does), then start this"
fi

# ---- 3. the QSFP link on each Spark: "<netdev> <ipv4> <hca,hca>"
read -r H_DEV H_IP H_HCAS <<<"${HY4_HEAD_LINK:-$(hy4_link "$HY4_PEER" || true)}"
[[ -n "${H_HCAS:-}" ]] || die "no QSFP link to $HY4_PEER here (ip route get $HY4_PEER; ibdev2netdev): is the cable up? (HY4_PEER sets the worker's link address)"
read -r W_DEV W_IP W_HCAS <<<"${HY4_WORKER_LINK:-$(on_worker "$(declare -f hy4_link); hy4_link $H_IP" || true)}"
[[ -n "${W_HCAS:-}" ]] || die "no QSFP link back to $H_IP on $HY4_WORKER"
log "link: head $H_DEV $H_IP ($H_HCAS), worker $W_DEV $W_IP ($W_HCAS), rendezvous $H_IP:$HY4_MASTER_PORT"

# ---- 4. memory on both Sparks (a stopped model's memory takes a while to come back)
NEED=$(hy4_min_avail)
mem_ok() {  # prints "here there"; true when both are at least NEED
  local here there
  here=$(hy4_mem_avail_gib || echo 0)
  there=$(on_worker "awk '\$1 == \"MemAvailable:\" { printf \"%.1f\\n\", \$2 / 1048576 }' $(q "${HY4_WORKER_MEMINFO:-/proc/meminfo}")" 2>/dev/null || echo 0)
  echo "$here ${there:-0}"
  awk -v a="$here" -v b="${there:-0}" -v n="$NEED" 'BEGIN { exit !(a >= n && b >= n) }'
}
if [[ "$NEED" != 0 ]]; then
  t=0
  until m=$(mem_ok); do
    if (( PRINT )); then break; fi
    (( t >= HY4_MIN_AVAIL_WAIT_S )) &&
      die "memory: only $m GiB available (head, worker), $NEED GiB needed (vLLM's --gpu-memory-utilization $HY4_GMU of the Spark, + 2): stop the other model and anything else on the GPU; HY4_MIN_AVAIL_GIB=0 skips this, and a rank that takes all the memory freezes the Spark"
    (( t % 30 == 0 )) && log "memory: $m GiB available (head, worker), waiting for $NEED"
    sleep 5; t=$((t + 5))
  done
  log "memory: $m GiB available (head, worker), $NEED GiB needed to start"
fi

# ---- 5. the two docker commands
CACHE="$HY4_CACHE_DIR/$HY4_IMAGE_HASH"
hy4_serve_args
common=(--init --name "$HY4_CONTAINER" --label openbeast.hy4=1 --label "openbeast.hy4.revision=$HY4_REVISION"
  --gpus all --ipc host --network host --shm-size "$HY4_SHM" --memory "$HY4_DOCKER_MEMORY"
  --device /dev/infiniband --cap-add IPC_LOCK --ulimit memlock=-1:-1 --ulimit nofile=1048576:1048576
  -e NCCL_IB_DISABLE=0 -e NCCL_IGNORE_CPU_AFFINITY=1 -e "NCCL_IB_GID_INDEX=$HY4_GID_INDEX"
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1
  -e FLASHINFER_WORKSPACE_BASE=/cache -e MAX_JOBS=4
  -v "$HY4_MODEL_DIR:/model:ro" -v "$CACHE:/cache")
[[ -n "${NCCL_DEBUG:-}" ]] && common+=(-e "NCCL_DEBUG=$NCCL_DEBUG")
# NCCL / Gloo / torch on the QSFP rail, vLLM's own sockets on the rank's link address
W_ENV=(-e "NCCL_SOCKET_IFNAME=$W_DEV" -e "GLOO_SOCKET_IFNAME=$W_DEV" -e "TP_SOCKET_IFNAME=$W_DEV" -e "NCCL_IB_HCA=$W_HCAS" -e "VLLM_HOST_IP=$W_IP")
H_ENV=(-e "NCCL_SOCKET_IFNAME=$H_DEV" -e "GLOO_SOCKET_IFNAME=$H_DEV" -e "TP_SOCKET_IFNAME=$H_DEV" -e "NCCL_IB_HCA=$H_HCAS" -e "VLLM_HOST_IP=$H_IP")
dist=(--nnodes 2 --master-addr "$H_IP" --master-port "$HY4_MASTER_PORT")
WORKER_CMD=(docker run -d "${common[@]}" "${W_ENV[@]}" "$HY4_IMAGE" "${HY4_ARGS[@]}" "${dist[@]}" --node-rank 1 --headless)
HEAD_CMD=("$HY4_DOCKER" run -d "${common[@]}" "${H_ENV[@]}" -e VLLM_API_KEY "$HY4_IMAGE" "${HY4_ARGS[@]}" "${dist[@]}"
  --node-rank 0 --host "$HY4_HOST" --port "$HY4_PORT")

if (( PRINT )); then
  echo "# worker ($HY4_WORKER), first:"
  echo "mkdir -p $(q "$CACHE") && $(q "${WORKER_CMD[@]}")"
  echo "# head (here), then; VLLM_API_KEY comes from $HY4_KEY_FILE through the environment, never argv:"
  echo "mkdir -p $(q "$CACHE") && $(q "${HEAD_CMD[@]}")"
  exit 0
fi

# ---- 6. start: stale containers gone, worker first, then the head
rm -f "$HY4_STOP_MARK"   # a stop before this start is not this run's stop
dk rm -f "$HY4_CONTAINER" >/dev/null 2>&1 || true
on_worker "docker rm -f $(q "$HY4_CONTAINER") >/dev/null 2>&1 || true"
hy4_refuse_protected "$CACHE" "write the cache in"
mkdir -p "$CACHE"
log "starting rank 1 on $HY4_WORKER (headless)"
on_worker "mkdir -p $(q "$CACHE") && $(q "${WORKER_CMD[@]}") >/dev/null" || die "rank 1 did not start on $HY4_WORKER"
log "starting rank 0 here, serving $HY4_SERVED on http://$HY4_HOST:$HY4_PORT once rank 1 has joined (first start: ~10 min of kernel autotune, kept in $CACHE)"
export VLLM_API_KEY="$KEY"
if ! "${HEAD_CMD[@]}" >/dev/null; then
  on_worker "docker rm -f $(q "$HY4_CONTAINER")" >/dev/null 2>&1 || true
  die "rank 0 did not start"
fi
unset VLLM_API_KEY KEY

# ---- 7. memguard on both Sparks (they exit when the container ends)
if awk -v g="$HY4_MEMGUARD_GIB" 'BEGIN { exit !(g > 0) }'; then
  MG_LOG="$HY4_CACHE_DIR/memguard.log"
  nohup bash "$HERE/memguard.sh" "$HY4_CONTAINER" "$HY4_MEMGUARD_GIB" "$HY4_MEMGUARD_HOLD_S" "$HY4_MEMGUARD_HARD_GIB" "$MG_LOG" \
    </dev/null >/dev/null 2>&1 &
  on_worker "mkdir -p $(q "$HY4_CACHE_DIR") && cat > $(q "$HY4_CACHE_DIR/memguard.sh")" < "$HERE/memguard.sh" &&
    on_worker "nohup bash $(q "$HY4_CACHE_DIR/memguard.sh") $(q "$HY4_CONTAINER" "$HY4_MEMGUARD_GIB" "$HY4_MEMGUARD_HOLD_S" "$HY4_MEMGUARD_HARD_GIB" "$MG_LOG") </dev/null >/dev/null 2>&1 &" ||
    log "WARNING: memguard did not start on $HY4_WORKER"
  log "memguard: floor $HY4_MEMGUARD_GIB GiB on both Sparks ($MG_LOG)"
fi

(( FOREGROUND )) || { log "started (FOREGROUND=0): logs: docker logs -f $HY4_CONTAINER; stop: $HERE/stop.sh"; exit 0; }

# ---- 8. foreground: rank 0's log to the journal; either rank ending ends both
stop_both() { bash "$HERE/stop.sh" >/dev/null 2>&1 || true; }
# stop.sh (the unit's ExecStop, or by hand) marks a deliberate stop before it stops
# the ranks; systemd sends SIGTERM only after ExecStop returns, so the loop below
# sees the ranks end first and must not call that a crash.
stopping() { [[ -f "$HY4_STOP_MARK" ]]; }
LOGPID=""
# (`|| true`: under set -e a failed kill, the log follower already gone, made this exit 1)
trap 'trap - TERM INT; [[ -z "$LOGPID" ]] || kill "$LOGPID" 2>/dev/null || true; stop_both; exit 0' TERM INT
dk logs -f "$HY4_CONTAINER" 2>&1 & LOGPID=$!
rc=0 n=0
while :; do
  sleep "${HY4_POLL_S:-10}"
  n=$((n + 1))
  st=$(dk inspect -f '{{.State.Running}} {{.State.ExitCode}}' "$HY4_CONTAINER" 2>/dev/null || echo "gone -")
  if [[ "$st" != true* ]]; then
    if stopping; then log "rank 0 ended ($st): stopped on purpose (stop.sh)"; rc=0; break; fi
    log "rank 0 ended ($st): stopping rank 1"; rc=1; break
  fi
  if (( n % 3 == 0 )); then
    ws=$(on_worker "docker inspect -f '{{.State.Running}}' $(q "$HY4_CONTAINER")" 2>/dev/null || echo unknown)
    if [[ "$ws" == false || "$ws" == unknown ]]; then
      # unknown: ssh failed; one more look before ending a healthy pair over a blip
      ws=$(on_worker "docker inspect -f '{{.State.Running}}' $(q "$HY4_CONTAINER")" 2>/dev/null || echo unknown)
      if [[ "$ws" != true ]]; then
        if stopping; then log "rank 1 on $HY4_WORKER ended ($ws): stopped on purpose (stop.sh)"; rc=0; break; fi
        log "rank 1 on $HY4_WORKER is not running ($ws): stopping rank 0"; rc=1; break
      fi
    fi
  fi
  [[ -n "${HY4_MAX_POLLS:-}" ]] && (( n >= HY4_MAX_POLLS )) && break
done
kill "$LOGPID" 2>/dev/null || true
trap - TERM INT
(( rc == 0 )) || stop_both
exit "$rc"
