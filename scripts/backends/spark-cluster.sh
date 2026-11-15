#!/bin/bash
# Run a DGX Spark cluster of 1 to 8 nodes from its head (rank 0).
# docs/SPARK_HYDRA.md ("Scaling: 1 to 8 Sparks")
#
#   scripts/backends/spark-cluster.sh check                      every node: reachable, GB10, NCCL, fabric, memory, disk
#   scripts/backends/spark-cluster.sh sync-weights --profile NAME   head -> workers over the fabric, then verify vs the lock
#   scripts/backends/spark-cluster.sh sync-image IMAGE           docker save | load to every worker that lacks it
#   scripts/backends/spark-cluster.sh save-image IMAGE FILE      write IMAGE to FILE (carry it to an offline cluster)
#   scripts/backends/spark-cluster.sh start --profile NAME [--engine tensorfold|vllm]
#   scripts/backends/spark-cluster.sh stop  [--engine tensorfold|vllm]   every rank, rank 0 first; no profile needed (default: both engines)
#   scripts/backends/spark-cluster.sh status
#   ... --cluster FILE (default scripts/backends/cluster.env)  --dry-run (print, run nothing)
#
# The nodes and their rank order come from cluster.env (CLUSTER_NODES, rank 0
# first, `local` = this machine). A profile's TENSOR_PARALLEL_SIZE must equal
# the node count: one rank per Spark. Each node launches its own rank with its
# own spark.env through scripts/backends/<engine>/spark-node.sh.
#
# OFFLINE: nothing here reaches the internet. Weights travel head -> worker
# with rsync and are checked against the profile's sha256 lock on arrival;
# images travel with docker save | docker load (or a file, save-image, for a
# cluster with no registry at all).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"

CMD="${1:-}"; [[ $# -gt 0 ]] && shift
CLUSTER_FILE="" PROFILE="" ENGINE=tensorfold ENGINE_SET=0 DRY=0 ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cluster) CLUSTER_FILE="${2:?}"; shift 2 ;;
    --profile) PROFILE="${2:?}"; shift 2 ;;
    --engine)  ENGINE="${2:?}"; ENGINE_SET=1; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
case "$CMD" in
  check|sync-weights|sync-image|save-image|start|stop|status) ;;
  ""|-h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "Unknown command: $CMD (see --help)" >&2; exit 2 ;;
esac
case "$ENGINE" in tensorfold|vllm) ;; *) echo "Error: --engine tensorfold|vllm" >&2; exit 2 ;; esac

CLUSTER_FILE="${CLUSTER_FILE:-$HERE/cluster.env}"
[[ -f "$CLUSTER_FILE" ]] || { echo "Error: no cluster file at $CLUSTER_FILE (copy $HERE/cluster.env.example)" >&2; exit 1; }
CLUSTER_NODES="" CLUSTER_NAME="" CLUSTER_REPO=""
sp_load "$CLUSTER_FILE" CLUSTER_NODES CLUSTER_NAME CLUSTER_REPO
read -r -a NODES <<< "$CLUSTER_NODES"
N=${#NODES[@]}
CLUSTER_NAME="${CLUSTER_NAME:-cluster}"
CLUSTER_REPO="${CLUSTER_REPO:-$HOME/openbeast}"
if (( N < 1 || N > 8 )); then
  echo "Error: CLUSTER_NODES lists $N node(s) — 1 to 8 (one rank per Spark)" >&2; exit 1
fi
[[ "${NODES[0]}" == local ]] || { echo "Error: rank 0 must be \`local\` — run this on the head" >&2; exit 1; }
for ((i = 1; i < N; i++)); do
  [[ "${NODES[$i]}" == local ]] && { echo "Error: only rank 0 may be \`local\` (entry $i)" >&2; exit 1; }
  [[ "${NODES[$i]}" =~ ^[A-Za-z0-9._@:-]+$ ]] || { echo "Error: node $i '${NODES[$i]}' is not a plain ssh target" >&2; exit 1; }
done

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
# on_node <rank> <command string> — run in the node's login shell. The command
# is built by the caller from %q-quoted pieces only.
on_node() {
  local r="$1" c="$2"
  if [[ $DRY -eq 1 ]]; then echo "[rank $r ${NODES[$r]}] $c"; return 0; fi
  if [[ "${NODES[$r]}" == local ]]; then bash -c "$c"; else ssh "${SSH_OPTS[@]}" "${NODES[$r]}" "$c"; fi
}
q() { printf '%q' "$1"; }
# shellcheck disable=SC2088  # a literal ~ on purpose: it expands on the NODE
REPO_Q="$(q "$CLUSTER_REPO")"; [[ "$CLUSTER_REPO" == "~/"* ]] && REPO_Q="~/$(q "${CLUSTER_REPO#\~/}")"

need_profile() {
  [[ -n "$PROFILE" ]] || { echo "Error: --profile NAME is required" >&2; exit 2; }
  sp_profile_load "$PROFILE" "$ENGINE" || { echo "Error: profile '$PROFILE' is invalid (above)" >&2; exit 1; }
  if [[ "$PF_TENSOR_PARALLEL_SIZE" != "$N" ]]; then
    echo "Error: profile '$PF_PROFILE_NAME' is TENSOR_PARALLEL_SIZE=$PF_TENSOR_PARALLEL_SIZE but $CLUSTER_FILE lists $N node(s) — one rank per Spark" >&2
    exit 1
  fi
}

case "$CMD" in
check)
  echo "== $CLUSTER_NAME: $N node(s)"
  case "$N" in
    1) echo "   topology: one Spark — no fabric needed" ;;
    2) echo "   topology: two Sparks — one direct QSFP cable (both rails used when addressed)" ;;
    3) echo "   topology: three Sparks — a cabled ring (port 0 -> next Spark's port 1), a subnet per cable, or a switch" ;;
    *) echo "   topology: $N Sparks — a RoCE-capable switch; every node on the fabric subnet(s)" ;;
  esac
  # One probe script for every node; it prints KEY=VALUE lines.
  PROBE='echo host=$(hostname); echo arch=$(uname -m)
echo gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
echo mem_avail_gib=$(awk "/MemAvailable/ {printf \"%.1f\", \$2/1048576}" /proc/meminfo)
echo nccl=$(dpkg-query -W -f="\${Version}" libnccl2 2>/dev/null)
echo docker=$(docker info >/dev/null 2>&1 && echo ok || echo no)
echo tensorfold=$( (~/.local/bin/tensorfold --version 2>/dev/null || true) | awk "{print \$NF}")
echo fabric=$(for i in $(ibdev2netdev 2>/dev/null | awk "/\\(Up\\)/ {print \$5}"); do ip -4 -o addr show dev $i | awk "{print \$4}"; done | paste -sd, -)
echo repo=$(test -d '"$REPO_Q"'/.git && git -C '"$REPO_Q"' rev-parse --short HEAD 2>/dev/null || echo missing)'
  FAIL=0
  declare -a FAB
  for ((r = 0; r < N; r++)); do
    if ! out="$(on_node "$r" "$PROBE" 2>&1)"; then
      echo "   rank $r ${NODES[$r]}: UNREACHABLE — $out"; FAIL=1; FAB[$r]=""; continue
    fi
    [[ $DRY -eq 1 ]] && { echo "$out"; continue; }
    get() { sed -n "s/^$1=//p" <<< "$out" | head -1; }
    FAB[$r]="$(get fabric)"
    line="   rank $r ${NODES[$r]}: $(get host) $(get arch) gpu=$(get gpu) mem_free=$(get mem_avail_gib)GiB nccl=$(get nccl) docker=$(get docker) tensorfold=$(get tensorfold) repo=$(get repo) fabric=${FAB[$r]:-none}"
    echo "$line"
    [[ "$(get arch)" == aarch64 ]] || { echo "     ! not aarch64 — is this a DGX Spark?"; FAIL=1; }
    [[ "$(get docker)" == ok ]] || { echo "     ! docker not reachable for this user"; FAIL=1; }
    [[ "$(get repo)" != missing ]] || { echo "     ! no OpenBeast checkout at $CLUSTER_REPO"; FAIL=1; }
    if (( N > 1 )); then
      [[ -n "$(get nccl)" ]] || { echo "     ! libnccl2 missing (spark-root-setup.sh)"; FAIL=1; }
      [[ -n "${FAB[$r]}" ]] || { echo "     ! no addressed ConnectX-7 link"; FAIL=1; }
    fi
  done
  # Every pair of ranks must share a fabric subnet: NCCL and RoCE connect
  # pairwise, and a pair with no common subnet cannot (a mirrored ring).
  if (( N > 1 && DRY == 0 )); then
    pairs="$(python3 - "${FAB[@]}" <<'PY'
import ipaddress, sys
nets = [{ipaddress.ip_interface(a).network for a in s.split(",") if a} for s in sys.argv[1:]]
bad = [f"{i}-{j}" for i in range(len(nets)) for j in range(i + 1, len(nets)) if not nets[i] & nets[j]]
print(" ".join(bad))
PY
)"
    if [[ -n "$pairs" ]]; then
      echo "   ! rank pairs with no common fabric subnet: $pairs (rewire, or address them on one subnet)"; FAIL=1
    else
      echo "   fabric: every pair of ranks shares a subnet"
    fi
  fi
  [[ $FAIL -eq 0 ]] && echo "== OK" || { echo "== problems above"; exit 1; }
  ;;

sync-weights)
  need_profile
  MODELS_DIR="${MODELS_DIR:-}"; sp_load "${SPARK_ENV:-$HERE/spark.env}" MODELS_DIR
  sp_profile_locate "$PROFILE" "${MODELS_DIR:-}"
  [[ $SP_LOCATE_RC -eq 0 && -n "$SP_MODEL_DIR" ]] || { echo "Error: '$PF_PROFILE_NAME' is not on the head, or does not match its lock (model-fetch.sh --profile $PF_PROFILE_NAME [--verify])" >&2; exit 1; }
  (( N > 1 )) || { echo "One node: nothing to sync."; exit 0; }
  echo "== $PF_PROFILE_NAME: $SP_MODEL_DIR -> ranks 1..$((N - 1))"
  pids=()
  for ((r = 1; r < N; r++)); do
    (
      on_node "$r" "mkdir -p $(q "$SP_MODEL_DIR")"
      if [[ $DRY -eq 1 ]]; then
        echo "[rank $r] rsync -a --partial $SP_MODEL_DIR/ ${NODES[$r]}:$SP_MODEL_DIR/"
      else
        rsync -a --partial -e "ssh ${SSH_OPTS[*]} -c aes128-gcm@openssh.com -o Compression=no" \
          "$SP_MODEL_DIR/" "${NODES[$r]}:$SP_MODEL_DIR/"
      fi
      # The lock travels with the repo checkout; verify there, on the worker.
      on_node "$r" "cd $REPO_Q && scripts/backends/model-fetch.sh --profile $(q "$PF_PROFILE_NAME") --verify"
    ) > >(sed "s/^/  [rank $r] /") 2>&1 &
    pids+=($!)
  done
  rc=0; for p in "${pids[@]}"; do wait "$p" || rc=1; done
  [[ $rc -eq 0 ]] && echo "== every worker holds $PF_PROFILE_NAME, verified against its lock" || { echo "== a worker failed (above)"; exit 1; }
  ;;

sync-image|save-image)
  IMAGE="${ARGS[0]:-}"; [[ -n "$IMAGE" ]] || { echo "Error: $CMD IMAGE" >&2; exit 2; }
  if [[ "$CMD" == save-image ]]; then
    FILE="${ARGS[1]:-}"; [[ -n "$FILE" ]] || { echo "Error: save-image IMAGE FILE" >&2; exit 2; }
    [[ $DRY -eq 1 ]] && { echo "docker save $IMAGE | gzip > $FILE"; exit 0; }
    docker save "$IMAGE" | gzip > "$FILE"
    echo "wrote $FILE ($(du -h "$FILE" | cut -f1)). On the offline head: gunzip -c $FILE | docker load; then $0 sync-image $IMAGE"
    exit 0
  fi
  want="$( [[ $DRY -eq 1 ]] && echo "<head image id>" || docker image inspect -f '{{.Id}}' "$IMAGE")"
  for ((r = 1; r < N; r++)); do
    have="$(on_node "$r" "docker image inspect -f '{{.Id}}' $(q "$IMAGE") 2>/dev/null || true")"
    if [[ "$have" == "$want" ]]; then echo "  rank $r: has $IMAGE ($want)"; continue; fi
    echo "  rank $r: sending $IMAGE"
    if [[ $DRY -eq 1 ]]; then echo "  docker save $IMAGE | ssh ${NODES[$r]} docker load"; continue; fi
    docker save "$IMAGE" | ssh "${SSH_OPTS[@]}" "${NODES[$r]}" "docker load" >/dev/null
    have="$(on_node "$r" "docker image inspect -f '{{.Id}}' $(q "$IMAGE")")"
    [[ "$have" == "$want" ]] || { echo "  rank $r: image id $have after load, head has $want" >&2; exit 1; }
    echo "  rank $r: loaded, identical"
  done
  ;;

start)
  need_profile
  LAUNCH="scripts/backends/$ENGINE/spark-node.sh --profile $(q "$PF_PROFILE_NAME")"
  # Workers first (TensorFold's container engine needs them waiting; vLLM
  # and native GLM ranks wait for each other either way), then rank 0. A
  # rank that fails stops the ranks already started: they would otherwise sit
  # at the rendezvous holding memory, waiting for ranks that never come.
  started=()
  for ((r = N - 1; r >= 0; r--)); do
    echo "== rank $r (${NODES[$r]}): start"
    if on_node "$r" "cd $REPO_Q && $LAUNCH --rank $r"; then
      started+=("$r"); continue
    fi
    echo "== rank $r (${NODES[$r]}) failed to start (above)" >&2
    left=()
    if [[ ${#started[@]} -gt 0 ]]; then
      echo "== rolling back rank(s) ${started[*]}, started by this run" >&2
      for s in "${started[@]}"; do
        on_node "$s" "cd $REPO_Q && scripts/backends/$ENGINE/spark-node.sh --rank $s --stop" || left+=("$s")
      done
    fi
    if [[ ${#left[@]} -gt 0 ]]; then
      echo "== STILL RUNNING: rank(s) ${left[*]} — stop them: $0 stop --cluster $CLUSTER_FILE --engine $ENGINE" >&2
    else
      echo "== nothing this run started is left running" >&2
    fi
    exit 1
  done
  echo "== started $N rank(s); rank 0 serves once every rank has loaded. Logs: each node's journalctl --user / docker logs"
  echo "   (a rank that dies after launching shows only in its own log: $0 status)"
  ;;

stop)
  # Every rank, rank 0 first, whatever happens to the others: a rank that is
  # already down (the usual reason to stop) must not keep the rest running.
  # No profile needed. Without --engine, both engines' ranks are stopped.
  if [[ $ENGINE_SET -eq 1 ]]; then
    engines=("$ENGINE")
  else
    engines=(tensorfold vllm)
  fi
  failed=()
  for ((r = 0; r < N; r++)); do
    echo "== rank $r (${NODES[$r]}): stop"
    c="cd $REPO_Q && rc=0"
    for e in "${engines[@]}"; do c+=" && { scripts/backends/$e/spark-node.sh --rank $r --stop || rc=1; }"; done
    on_node "$r" "$c; exit \$rc" || failed+=("$r")
  done
  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "== could not stop rank(s): ${failed[*]} (above) — the other ranks were stopped" >&2
    exit 1
  fi
  echo "== every rank is stopped"
  ;;

status)
  for ((r = 0; r < N; r++)); do
    out="$(on_node "$r" 'echo "$(hostname) mem_free=$(awk "/MemAvailable/ {printf \"%.1f\", \$2/1048576}" /proc/meminfo)GiB units=$(systemctl --user list-units --plain --no-legend "openbeast-*" 2>/dev/null | awk "{print \$1\":\"\$4}" | paste -sd, -) containers=$(docker ps --format "{{.Names}}" 2>/dev/null | grep -E "openbeast-|glm53" | paste -sd, -)"' 2>&1 || echo UNREACHABLE)"
    echo "  rank $r ${NODES[$r]}: $out"
  done
  ;;
esac
