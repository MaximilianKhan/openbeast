#!/bin/bash
# Run a DGX Spark cluster of 1 to 8 nodes from its head (rank 0).
# docs/SPARK_HYDRA.md ("Scaling: 1 to 8 Sparks")
#
#   scripts/backends/spark-cluster.sh check                      every node: reachable, aarch64 + GB10, docker, NCCL, same
#                                                                checkout as rank 0, fabric pairing + SPARK_HEAD_IP reach;
#                                                                free memory and disk are shown, not judged
#   scripts/backends/spark-cluster.sh sync-weights --profile NAME   head -> workers over the fabric (model + drafter), verified vs the HEAD's lock
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
# own spark.env through scripts/backends/<engine>/spark-node.sh; start goes
# N-1..0 and stops the ranks it started if a later one fails. Only N = 1 and
# N = 2 have run on hardware; past 2 Sparks this is untested.
#
# OFFLINE: nothing here reaches the internet. Weights travel head -> worker
# with rsync and are checked against the head's sha256 lock on arrival;
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
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
case "$CMD" in
  check|sync-weights|sync-image|save-image|start|stop|status) ;;
  ""|-h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
    3) echo "   topology: three Sparks — a switch, or one subnet every Spark is on (NOT a cabled ring with a subnet per cable: the launchers rendezvous on one fabric address). Untested on hardware." ;;
    *) echo "   topology: $N Sparks — a RoCE-capable switch; every node on the fabric subnet(s). Untested on hardware." ;;
  esac
  # One probe script for every node; it prints KEY=VALUE lines. SPARK_HEAD_IP
  # and MODELS_DIR come from that node's own spark.env (parsed, never sourced).
  IFS= read -r -d '' PROBE <<'P' || true
kv() { awk -v k="$1" '$0 ~ "^[[:space:]]*" k "[[:space:]]*=" {v = substr($0, index($0, "=") + 1)} END {sub(/[[:space:]]+#.*/, "", v); gsub(/^[[:space:]]+|[[:space:]]+$/, "", v); gsub(/^["\047]|["\047]$/, "", v); print v}' @REPO@/scripts/backends/spark.env 2>/dev/null; }
echo host=$(hostname); echo arch=$(uname -m)
echo gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
echo mem_avail_gib=$(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null)
m=$(kv MODELS_DIR); case "$m" in "~"*) m="$HOME${m#"~"}" ;; esac
echo disk_free_gib=$(df -Pk "${m:-@REPO@}" 2>/dev/null | awk 'NR == 2 {printf "%.0f", $4/1048576}')
echo nccl=$(dpkg-query -W -f='${Version}' libnccl2 2>/dev/null)
echo docker=$(docker info >/dev/null 2>&1 && echo ok || echo no)
echo tensorfold=$( (~/.local/bin/tensorfold --version 2>/dev/null || true) | awk '{print $NF}')
echo fabric=$(for i in $(ibdev2netdev 2>/dev/null | awk '/\(Up\)/ {print $5}'); do ip -4 -o addr show dev $i | awk '{print $4}'; done | paste -sd, -)
echo head_ip=$(kv SPARK_HEAD_IP)
echo repo=$(test -d @REPO@/.git && git -C @REPO@ rev-parse --short HEAD 2>/dev/null || echo missing)
P
  PROBE="${PROBE//@REPO@/$REPO_Q}"
  FAIL=0
  declare -a FAB HIP
  REPO0=""
  for ((r = 0; r < N; r++)); do
    FAB[$r]="" HIP[$r]=""
    if ! out="$(on_node "$r" "$PROBE" 2>&1)"; then
      echo "   rank $r ${NODES[$r]}: UNREACHABLE — $out"; FAIL=1; continue
    fi
    [[ $DRY -eq 1 ]] && { echo "$out"; continue; }
    get() { sed -n "s/^$1=//p" <<< "$out" | head -1; }
    FAB[$r]="$(get fabric)" HIP[$r]="$(get head_ip)"
    line="   rank $r ${NODES[$r]}: $(get host) $(get arch) gpu=$(get gpu) mem_free=$(get mem_avail_gib)GiB disk_free=$(get disk_free_gib)GiB nccl=$(get nccl) docker=$(get docker) tensorfold=$(get tensorfold) repo=$(get repo) fabric=${FAB[$r]:-none}"
    echo "$line"
    [[ "$(get arch)" == aarch64 ]] || { echo "     ! not aarch64 — is this a DGX Spark?"; FAIL=1; }
    [[ "$(get gpu)" == *GB10* ]] || { echo "     ! GPU '$(get gpu)' is not a GB10 (nvidia-smi) — is this a DGX Spark?"; FAIL=1; }
    [[ "$(get docker)" == ok ]] || { echo "     ! docker not reachable for this user"; FAIL=1; }
    if [[ "$(get repo)" == missing ]]; then
      echo "     ! no OpenBeast checkout at $CLUSTER_REPO"; FAIL=1
    elif [[ $r -eq 0 ]]; then
      REPO0="$(get repo)"
    elif [[ -n "$REPO0" && "$(get repo)" != "$REPO0" ]]; then
      # start runs each node's OWN spark-node.sh and lock: a skewed checkout
      # runs another launcher against another lock.
      echo "     ! checkout is at $(get repo), rank 0's at $REPO0 — check out the same commit on every node"; FAIL=1
    fi
    if (( N > 1 )); then
      [[ -n "$(get nccl)" ]] || { echo "     ! libnccl2 missing (spark-root-setup.sh)"; FAIL=1; }
      [[ -n "${FAB[$r]}" ]] || { echo "     ! no addressed ConnectX-7 link"; FAIL=1; }
      [[ -n "${HIP[$r]}" ]] || { echo "     ! SPARK_HEAD_IP is not set in this node's spark.env (start needs it: rank 0's fabric address)"; FAIL=1; }
    fi
  done
  # The fabric, as the launchers use it. NCCL and RoCE connect pairwise, so
  # every pair of ranks must share a subnet (a mirrored ring has one that
  # does not). And the whole rendezvous — SPARK_HEAD_IP as NCCL_COMM_ID /
  # --master / --master-addr, and the bootstrap sockets pinned to SPARK_IFACE
  # — sits on ONE fabric address of rank 0: every rank must have a subnet
  # holding it. A cabled 3-Spark ring (a subnet per cable) fails that: no
  # address of rank 0 is on a subnet with both peers.
  if (( N > 1 && DRY == 0 )); then
    args=(); for ((r = 0; r < N; r++)); do args+=("${FAB[$r]}|${HIP[$r]}"); done
    python3 - "${args[@]}" <<'PY' || FAIL=1
import ipaddress, sys
ranks = [a.split("|", 1) for a in sys.argv[1:]]
ifs = [[ipaddress.ip_interface(x) for x in fab.split(",") if x] for fab, _ in ranks]
nets = [{i.network for i in f} for f in ifs]
bad = False
pairs = [f"{i}-{j}" for i in range(len(nets)) for j in range(i + 1, len(nets)) if not nets[i] & nets[j]]
if pairs:
    print(f"   ! rank pairs with no common fabric subnet: {' '.join(pairs)} (rewire, or address them on one subnet)")
    bad = True
else:
    print("   fabric: every pair of ranks shares a subnet")
heads = {h for _, h in ranks if h}
if len(heads) > 1:
    print(f"   ! the nodes' spark.env disagree on SPARK_HEAD_IP ({', '.join(sorted(heads))}): every rank must rendezvous at the same address")
    bad = True
for h in sorted(heads):
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        print(f"   ! SPARK_HEAD_IP {h!r} is not an IP address"); bad = True; continue
    if ip not in {i.ip for i in ifs[0]}:
        print(f"   ! SPARK_HEAD_IP {h} is not one of rank 0's fabric addresses ({', '.join(str(i.ip) for i in ifs[0]) or 'none'})")
        bad = True
        continue
    lost = [r for r in range(1, len(nets)) if not any(ip in n for n in nets[r])]
    for r in lost:
        print(f"   ! rank {r} cannot reach SPARK_HEAD_IP {h}: none of its fabric subnets holds it")
    if lost:
        bad = True
    else:
        print(f"   fabric: every rank reaches SPARK_HEAD_IP {h} on its own subnet")
if len(nets) >= 3 and not any(all(n in nets[r] for r in range(1, len(nets))) for n in nets[0]):
    print("   ! no fabric subnet of rank 0 reaches every rank (e.g. a cabled 3-Spark ring, a subnet per cable): "
          "spark-node.sh rendezvous on one fabric address, so this cannot start — use a switch or one shared "
          "subnet (the GLM recipe's start-tp3.sh handles a ring its own way)")
    bad = True
sys.exit(1 if bad else 0)
PY
  fi
  [[ $FAIL -eq 0 ]] && echo "== OK" || { echo "== problems above"; exit 1; }
  ;;

sync-weights)
  need_profile
  MODELS_DIR="${MODELS_DIR:-}"; sp_load "${SPARK_ENV:-$HERE/spark.env}" MODELS_DIR
  sp_profile_locate "$PROFILE" "${MODELS_DIR:-}"
  [[ $SP_LOCATE_RC -eq 0 && -n "$SP_MODEL_DIR" ]] || { echo "Error: '$PF_PROFILE_NAME' is not on the head, or does not match its lock (model-fetch.sh --profile $PF_PROFILE_NAME [--verify])" >&2; exit 1; }
  [[ -z "$PF_DRAFTER_SOURCE" || -n "$SP_DRAFTER_DIR" ]] || { echo "Error: the drafter of '$PF_PROFILE_NAME' is not on the head (model-fetch.sh --profile $PF_PROFILE_NAME)" >&2; exit 1; }
  # The HEAD's lock is the pin every worker's copy is checked against, so it
  # must pin every artifact. (--locate passes a local SOURCE with no lock at
  # all; a worker's --verify would then record a fresh trust-on-first-use
  # lock of whatever arrived and call it verified.)
  if ! python3 - "$PF_PROFILE_LOCK" model ${PF_DRAFTER_SOURCE:+drafter} <<'PY'
import json, sys
try:
    arts = json.load(open(sys.argv[1])).get("artifacts") or {}
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if all((arts.get(k) or {}).get("files") for k in sys.argv[2:]) else 1)
PY
  then
    echo "Error: $PF_PROFILE_LOCK has no lock entry for every artifact of '$PF_PROFILE_NAME' — run model-fetch.sh --profile $PF_PROFILE_NAME on the head first (it locks a local SOURCE on its first run), then sync" >&2
    exit 1
  fi
  (( N > 1 )) || { echo "One node: nothing to sync."; exit 0; }
  sha() { python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }
  PSHA="$(sha "$PF_PROFILE_PATH")" LSHA="$(sha "$PF_PROFILE_LOCK")"
  NAME_Q="$(q "$PF_PROFILE_NAME")"
  # Same parent for the model and the drafter (MODELS_DIR); none for a local SOURCE.
  MDIR=""
  if [[ -n "$SP_DRAFTER_DIR" ]]; then MDIR="$(dirname "$SP_DRAFTER_DIR")"; elif [[ "$PF_PROFILE_IS_HF" == 1 ]]; then MDIR="$(dirname "$SP_MODEL_DIR")"; fi
  DIRS=("$SP_MODEL_DIR"); [[ -n "$SP_DRAFTER_DIR" ]] && DIRS+=("$SP_DRAFTER_DIR")
  # The worker launches the profile by NAME from its own checkout, against its
  # own lock: both must be byte-identical to the head's. Checked before any
  # copy; a worker with no lock gets the head's after its hash is confirmed.
  # shellcheck disable=SC2016  # $p, $l, $1 expand on the worker
  H='h() { { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; } | cut -d" " -f1; }'
  PRE="cd $REPO_Q && p=scripts/backends/models/$NAME_Q.env l=scripts/backends/models/$NAME_Q.lock && $H"'
if [ ! -f "$p" ] || [ "$(h "$p")" != '"$PSHA"' ]; then echo "this node'"'"'s $p is missing or differs from the head'"'"'s — check out the head'"'"'s commit (spark-cluster.sh check compares them)"; exit 1; fi
if [ -f "$l" ] && [ "$(h "$l")" != '"$LSHA"' ]; then echo "this node'"'"'s $l differs from the head'"'"'s — the head'"'"'s lock is the pin: restore it from git, or remove it here to take the head'"'"'s"; exit 1; fi'
  POST="cd $REPO_Q && l=scripts/backends/models/$NAME_Q.lock && $H"'
if [ -f "$l" ]; then cat > /dev/null; else t="$l.partial.$$"; if cat > "$t" && [ "$(h "$t")" = '"$LSHA"' ]; then mv "$t" "$l"; echo "installed the head'"'"'s lock as $l"; else rm -f "$t"; echo "the head'"'"'s lock did not arrive intact"; exit 1; fi; fi
scripts/backends/model-fetch.sh --profile '"$NAME_Q ${MDIR:+--models-dir $(q "$MDIR")}"' --verify || exit 1
scripts/backends/model-fetch.sh --profile '"$NAME_Q"' --locate > /dev/null || { echo "verified where the head wrote it, but this node'"'"'s own MODELS_DIR (its spark.env) does not lead there, so its launcher will not find it — make MODELS_DIR match the head'"'"'s"; exit 1; }'
  echo "== $PF_PROFILE_NAME: ${DIRS[*]} -> ranks 1..$((N - 1)), checked against the head's lock ($PF_PROFILE_LOCK)"
  pids=()
  for ((r = 1; r < N; r++)); do
    (
      on_node "$r" "$PRE"
      for d in "${DIRS[@]}"; do
        on_node "$r" "mkdir -p $(q "$d")"
        if [[ $DRY -eq 1 ]]; then
          echo "[rank $r] rsync -a --partial $d/ ${NODES[$r]}:$d/"
        else
          rsync -a --partial -e "ssh ${SSH_OPTS[*]} -c aes128-gcm@openssh.com -o Compression=no" \
            "$d/" "${NODES[$r]}:$d/"
        fi
      done
      on_node "$r" "$POST" < "$PF_PROFILE_LOCK"
    ) > >(sed "s/^/  [rank $r] /") 2>&1 &
    pids+=($!)
  done
  rc=0; for p in "${pids[@]}"; do wait "$p" || rc=1; done
  [[ $rc -eq 0 ]] && echo "== every worker holds $PF_PROFILE_NAME, verified against the head's lock" || { echo "== a worker failed (above)"; exit 1; }
  ;;

sync-image|save-image)
  IMAGE="${ARGS[0]:-}"; [[ -n "$IMAGE" ]] || { echo "Error: $CMD IMAGE" >&2; exit 2; }
  if [[ "$CMD" == save-image ]]; then
    FILE="${ARGS[1]:-}"; [[ -n "$FILE" ]] || { echo "Error: save-image IMAGE FILE" >&2; exit 2; }
    [[ $DRY -eq 1 ]] && { echo "docker save $IMAGE | gzip > $FILE"; exit 0; }
    docker save "$IMAGE" | gzip > "$FILE"
    echo "wrote $FILE ($(du -h "$FILE" | cut -f1)). On the offline head: gunzip -c $FILE | docker load; then $0 sync-image $IMAGE"
    echo "  docker load keeps the image id but not the registry's RepoDigest: pin it by id (VLLM_IMAGE_ID / TENSORFOLD_IMAGE_ID = $(docker image inspect -f '{{.Id}}' "$IMAGE"))"
    exit 0
  fi
  # The image ID (sha256 of the image config, which names every layer) is
  # what survives docker save | load; a RepoDigest (the registry manifest's
  # hash, what *_IMAGE_DIGEST pins) does not on the classic overlay2 store.
  # So the workers are compared, and the launchers can be pinned, by ID.
  want="$( [[ $DRY -eq 1 ]] && echo "<head image id>" || docker image inspect -f '{{.Id}}' "$IMAGE")"
  nodigest=()
  for ((r = 1; r < N; r++)); do
    have="$(on_node "$r" "docker image inspect -f '{{.Id}}' $(q "$IMAGE") 2>/dev/null || true")"
    if [[ "$have" == "$want" ]]; then echo "  rank $r: has $IMAGE ($want)"; continue; fi
    echo "  rank $r: sending $IMAGE"
    if [[ $DRY -eq 1 ]]; then echo "  docker save $IMAGE | ssh ${NODES[$r]} docker load"; continue; fi
    docker save "$IMAGE" | ssh "${SSH_OPTS[@]}" "${NODES[$r]}" "docker load" >/dev/null
    have="$(on_node "$r" "docker image inspect -f '{{.Id}}' $(q "$IMAGE")")"
    [[ "$have" == "$want" ]] || { echo "  rank $r: image id $have after load, head has $want" >&2; exit 1; }
    echo "  rank $r: loaded, identical (image id $want)"
    digs="$(on_node "$r" "docker image inspect -f '{{join .RepoDigests \" \"}}' $(q "$IMAGE") 2>/dev/null || true")"
    if [[ -z "${digs// /}" ]]; then
      echo "  rank $r: has no RepoDigest after docker load — an *_IMAGE_DIGEST pin cannot resolve there offline (docker would try to pull)"
      nodigest+=("$r")
    fi
  done
  if [[ ${#nodigest[@]} -gt 0 || $DRY -eq 1 ]]; then
    echo "== pin the image by ID in every node's spark.env (runs that exact image, never pulls):"
    echo "     VLLM_IMAGE_ID=$want      (vLLM)"
    echo "     TENSORFOLD_IMAGE_ID=$want      (TensorFold container engine)"
  fi
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
