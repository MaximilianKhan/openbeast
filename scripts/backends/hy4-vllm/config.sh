# shellcheck shell=bash disable=SC2034  # every setting here is read by the scripts that source it
# Hy4-flash (NVFP4) on vLLM, tensor parallel 2 over spark-1 (head) + spark-2
# (worker): the pins and settings start.sh, stop.sh and spark-hy4-stage.sh
# share. Sourced, never run. docs/SPARK_HYDRA.md "Hy4-flash (NVFP4, vLLM TP2): staged"
#
# A setting comes from the first of:
#   1. the environment (HY4_PORT=8001 ./start.sh)
#   2. ~/.config/openbeast/hy4-vllm.env: HY4_*=value lines, PARSED, never sourced
#   3. the defaults below
#
# The pins are constants, not settings: a new revision or a new image is a
# code change (and a new SHA256SUMS.hub-lfs / image hash), reviewed like one.

HY4_HERE="${HY4_HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# ---------------------------------------------------------------- the pins
# The checkpoint: atakann's NVFP4 build (experts NVFP4 by NVIDIA ModelOpt
# 0.48.0, Engram tables FP8, the DFly2 drafter in BF16), 164,691,130,443 bytes
# in 76 files (14 shards). 30f201ac is the revision the recipe below pins
# (Hub API, read 2026-11-19). main has moved one commit since (922cbf11, the
# model card only: GPQA and IFEval numbers); the weights did not change.
HY4_REPO=atakans/Hy4-flash-NVFP4
HY4_REVISION=30f201acf0b4f9ba178817bcbc04ccfa3ec473f9
# The recipe the image and the flags come from (MIT; image/CREDITS.md).
HY4_RECIPE_REPO=atakann/dgx-spark-recipes
HY4_RECIPE_COMMIT=e764efa3efed3e21fe543c3fffc927eb6a00bff8
# vllm/vllm-openai:hy4-flash, the arm64 image (Docker Hub, read 2026-11-19: tag
# index sha256:6b645bfb..., arm64 manifest below, 11.0 GB compressed). The
# recipe's Dockerfile FROMs this same digest, so the pull and the build agree.
HY4_BASE_IMAGE=vllm/vllm-openai@sha256:5e2c1badbca60595d6591c60feb4b8547f59da96e7a91710d40239250e721aba
HY4_BASE_INDEX=sha256:6b645bfbf4b89602d38f6e93c1c418516ec0bfbf54b64cd661b8075bfec01da1
# The image built from image/: <recipe commit 7>-<hy4_image_hash of image/>.
# tests/test_spark_hy4.sh fails when image/ changes and this does not.
HY4_IMAGE_HASH=b486c74ef8b6
HY4_IMAGE=openbeast/hy4-vllm:e764efa-$HY4_IMAGE_HASH
HY4_SERVED=Hy4-flash-NVFP4
# Never touched by anything here (Max, 2026-11-19: keep it). Every path this
# code writes, moves or deletes is checked against it (hy4_protected).
HY4_PROTECTED_NAME=LibertAIDAI-Hy4-flash-EXL3-4bpw

# ---------------------------------------------------------------- settings
_hy4_conf="${HY4_CONF:-$HOME/.config/openbeast/hy4-vllm.env}"
if [[ -r "$_hy4_conf" ]]; then
  while IFS= read -r _l || [[ -n "$_l" ]]; do
    [[ "$_l" =~ ^[[:space:]]*(HY4_[A-Z0-9_]+)[[:space:]]*=(.*)$ ]] || continue
    _k=${BASH_REMATCH[1]}; _v=${BASH_REMATCH[2]}
    case "$_k" in HY4_REPO|HY4_REVISION|HY4_RECIPE_*|HY4_BASE_*|HY4_IMAGE_HASH|HY4_PROTECTED_NAME|HY4_HERE|HY4_CONF) continue ;; esac
    if [[ "$_v" =~ ^[[:space:]]*\"([^\"]*)\"[[:space:]]*(#.*)?$ || "$_v" =~ ^[[:space:]]*\'([^\']*)\'[[:space:]]*(#.*)?$ ]]; then
      _v=${BASH_REMATCH[1]}
    else
      _v=${_v%%#*}; _v=${_v#"${_v%%[![:space:]]*}"}; _v=${_v%"${_v##*[![:space:]]}"}
    fi
    [[ -n "${!_k+set}" ]] || printf -v "$_k" '%s' "${_v/#\~\//$HOME/}"
  done < "$_hy4_conf"
fi
unset _l _k _v _hy4_conf

HY4_MODEL_DIR="${HY4_MODEL_DIR:-$HOME/Documents/atakans-Hy4-flash-NVFP4}"
HY4_IMAGE_DIR="${HY4_IMAGE_DIR:-$HOME/Documents/hy4-vllm-image}"     # the saved image tar (offline restore)
HY4_CACHE_DIR="${HY4_CACHE_DIR:-$HOME/.cache/hy4-vllm}"              # first-start autotune, per image
HY4_RUNTIME_DIR="${HY4_RUNTIME_DIR:-$HOME/openbeast-recipes/hy4-vllm}"  # where `install` puts start.sh/stop.sh
HY4_UNIT_DIR="${HY4_UNIT_DIR:-$HOME/.config/systemd/user}"
HY4_WORKER="${HY4_WORKER:-spark-2}"          # ssh target (key login, BatchMode); NVIDIA Sync's alias = 10.100.216.2
HY4_PEER="${HY4_PEER:-10.100.216.2}"         # the worker's address on the QSFP link, seen from the head
HY4_MASTER_PORT="${HY4_MASTER_PORT:-29501}"  # torch.distributed rendezvous: spark-guard keeps 29501-29502 on the fabric
HY4_HOST="${HY4_HOST:-127.0.0.1}"            # beast-gate (:8443 on the tailnet) proxies to it
HY4_PORT="${HY4_PORT:-8000}"
HY4_KEY_FILE="${HY4_KEY_FILE:-$HOME/.config/openbeast/tensorfold-api-key}"  # the key the gate already sends
HY4_CONTAINER="${HY4_CONTAINER:-hy4-vllm}"   # the same name on both Sparks
# The recipe's flags (recipes/llm/hy4-flash-nvfp4-vllm-tp2.yaml @ e764efa).
HY4_MAX_MODEL_LEN="${HY4_MAX_MODEL_LEN:-262144}"
HY4_KV_BYTES="${HY4_KV_BYTES:-6G}"           # 578k tokens of KV (CLAIMED), 262,144 a request
HY4_GMU="${HY4_GMU:-0.85}"
HY4_MAX_NUM_SEQS="${HY4_MAX_NUM_SEQS:-4}"    # = the campaign arm's --jobs 4 --slots 4
HY4_MAX_BATCHED="${HY4_MAX_BATCHED:-8192}"
# The model's own DFly drafter, 5 tokens: 17-38 tok/s against ~15 plain
# (CLAIMED). "off" serves without it.
_hy4_d='{"method":"dfly","num_speculative_tokens":5}'
HY4_SPEC="${HY4_SPEC:-$_hy4_d}"
_hy4_d='{"image":1,"video":0}'
HY4_MM_LIMIT="${HY4_MM_LIMIT:-$_hy4_d}"
unset _hy4_d
HY4_DOCKER_MEMORY="${HY4_DOCKER_MEMORY:-112g}"  # the recipe's cgroup cap per container
HY4_SHM="${HY4_SHM:-16g}"
HY4_GID_INDEX="${HY4_GID_INDEX:-3}"          # the RoCE v2 IPv4 GID on both Sparks (as the DeepSeek recipe runs)
HY4_EXTRA_ARGS="${HY4_EXTRA_ARGS:-}"         # more vllm serve flags, space-separated (no quotes inside)
# Memory: refuse to start unless each Spark has this much available. "auto":
# what vLLM itself asks for at start (--gpu-memory-utilization x MemTotal,
# rounded up) + 2 GiB: 106 GiB on a 121.7 GiB Spark. Waits up to
# HY4_MIN_AVAIL_WAIT_S for a just-stopped model's memory to come back.
HY4_MIN_AVAIL_GIB="${HY4_MIN_AVAIL_GIB:-auto}"
HY4_MIN_AVAIL_WAIT_S="${HY4_MIN_AVAIL_WAIT_S:-180}"
# memguard.sh on both Sparks while it serves (the DeepSeek recipe's levels):
# kill the rank's container when MemAvailable stays under the floor for HOLD
# seconds, or at once under the hard floor. 0 turns it off.
HY4_MEMGUARD_GIB="${HY4_MEMGUARD_GIB:-2.5}"
HY4_MEMGUARD_HOLD_S="${HY4_MEMGUARD_HOLD_S:-4}"
HY4_MEMGUARD_HARD_GIB="${HY4_MEMGUARD_HARD_GIB:-1}"
# Staging: the memory cap of every download / copy / verify process (a user
# scope: page cache it fills is charged to it and reclaimed there, not taken
# from a serving model). HY4_SCOPE=0 runs them uncapped.
HY4_SCOPE="${HY4_SCOPE:-1}"
HY4_SCOPE_MAX="${HY4_SCOPE_MAX:-8G}"
HY4_SCOPE_HIGH="${HY4_SCOPE_HIGH:-6G}"
# Test seams: the commands.
HY4_DOCKER="${HY4_DOCKER:-docker}"
HY4_SSH="${HY4_SSH:-ssh}"

hy4_log() { printf '[%s] %s\n' "${HY4_TAG:-hy4-vllm}" "$*" >&2; }
hy4_die() { hy4_log "ERROR: $*"; exit 1; }

# hy4_protected PATH: true when PATH is, or is inside, or contains the kept
# EXL3 pack. Every write / delete / prune path goes through this first.
hy4_protected() {
  local p="${1%/}"
  [[ "$p" == *"$HY4_PROTECTED_NAME"* ]] && return 0
  [[ -e "$p/$HY4_PROTECTED_NAME" ]] && return 0
  return 1
}
hy4_refuse_protected() {  # PATH WHAT
  if hy4_protected "$1"; then hy4_die "refusing to $2 $1: it is or holds ~/Documents/$HY4_PROTECTED_NAME, which is kept (Max, 2026-11-19)"; fi
}

hy4_sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -c1-64; }
# The image's content hash: sha256 of "<sha256>  <name>" lines of the files
# the Dockerfile uses (and the Dockerfile itself), sorted by name, 12 hex.
HY4_IMAGE_FILES="Dockerfile attention.py dfly_config.py hy_v4_attention.py mla_attention.py model.py sm120.py"
hy4_image_hash() {  # DIR
  local f
  for f in $HY4_IMAGE_FILES; do
    [[ -f "$1/$f" ]] || { echo "missing:$f"; return 1; }
    printf '%s  %s\n' "$(hy4_sha256 < "$1/$f")" "$f"
  done | LC_ALL=C sort -k2 | hy4_sha256 | cut -c1-12
}

# The serve flags both ranks get (rank and address flags are added per rank).
hy4_serve_args() {
  HY4_ARGS=(/model --served-model-name "$HY4_SERVED"
    --tensor-parallel-size 2 --enable-expert-parallel --dtype bfloat16
    --max-model-len "$HY4_MAX_MODEL_LEN" --max-num-seqs "$HY4_MAX_NUM_SEQS"
    --max-num-batched-tokens "$HY4_MAX_BATCHED"
    --kv-cache-memory-bytes "$HY4_KV_BYTES" --gpu-memory-utilization "$HY4_GMU")
  [[ "$HY4_SPEC" == off ]] || HY4_ARGS+=(--speculative-config "$HY4_SPEC")
  HY4_ARGS+=(--limit-mm-per-prompt "$HY4_MM_LIMIT"
    --reasoning-parser hy_v4 --tool-call-parser hy_v4 --enable-auto-tool-choice)
  if [[ -n "$HY4_EXTRA_ARGS" ]]; then
    local x
    # shellcheck disable=SC2206  # a flat flag list, validated to plain characters
    local extra=($HY4_EXTRA_ARGS)
    local re='^[A-Za-z0-9._:=,/{}"-]+$'
    for x in "${extra[@]}"; do [[ "$x" =~ $re ]] || hy4_die "HY4_EXTRA_ARGS: '$x' has unexpected characters"; done
    HY4_ARGS+=("${extra[@]}")
  fi
}

# hy4_check_settings: the values that reach docker / vllm argv.
hy4_check_settings() {
  [[ "$HY4_MASTER_PORT" =~ ^[0-9]+$ && "$HY4_PORT" =~ ^[0-9]+$ ]] || hy4_die "HY4_MASTER_PORT / HY4_PORT must be port numbers"
  [[ "$HY4_MAX_MODEL_LEN" =~ ^[0-9]+$ && "$HY4_MAX_NUM_SEQS" =~ ^[0-9]+$ && "$HY4_MAX_BATCHED" =~ ^[0-9]+$ ]] || hy4_die "HY4_MAX_MODEL_LEN / HY4_MAX_NUM_SEQS / HY4_MAX_BATCHED must be whole numbers"
  [[ "$HY4_KV_BYTES" =~ ^[0-9]+[KMGT]?$ ]] || hy4_die "HY4_KV_BYTES is a size like 6G, not $HY4_KV_BYTES"
  [[ "$HY4_GMU" =~ ^0\.[0-9]+$ ]] || hy4_die "HY4_GMU is a fraction like 0.85, not $HY4_GMU"
  local json_re='^[{][A-Za-z0-9_":,. -]+[}]$'
  [[ "$HY4_SPEC" == off || "$HY4_SPEC" =~ $json_re ]] || hy4_die "HY4_SPEC is off or a JSON object, not $HY4_SPEC"
  [[ "$HY4_MM_LIMIT" =~ $json_re ]] || hy4_die "HY4_MM_LIMIT is a JSON object, not $HY4_MM_LIMIT"
  [[ "$HY4_GID_INDEX" =~ ^[0-9]+$ ]] || hy4_die "HY4_GID_INDEX must be a number"
  [[ "$HY4_HOST" == 127.0.0.1 || "$HY4_HOST" == localhost || "${HY4_ALLOW_NONLOOPBACK:-0}" == 1 ]] ||
    hy4_die "HY4_HOST=$HY4_HOST: the server is served on loopback (beast-gate publishes it); HY4_ALLOW_NONLOOPBACK=1 overrides"
  [[ "$HY4_CONTAINER" =~ ^[A-Za-z0-9._-]+$ && "$HY4_WORKER" =~ ^[A-Za-z0-9.@_-]+$ ]] || hy4_die "HY4_CONTAINER / HY4_WORKER have unexpected characters"
  hy4_refuse_protected "$HY4_MODEL_DIR" "use as the NVFP4 model folder"
  hy4_refuse_protected "$HY4_CACHE_DIR" "use as the cache"
  hy4_refuse_protected "$HY4_IMAGE_DIR" "use as the image folder"
}

# hy4_link PEER: "<netdev> <ipv4> <hca[,hca]>" for the Spark it runs on: the
# netdev and source address of the route to PEER, that netdev's RoCE device
# first, then every other RoCE device ibdev2netdev lists Up (the second rail).
# Self-contained (sent to the worker with declare -f).
hy4_link() {
  local peer=$1 dev src hca hcas d s
  read -r dev src <<<"$(ip -4 -o route get "$peer" 2>/dev/null | awk '{for (i = 1; i < NF; i++) { if ($i == "dev") d = $(i + 1); if ($i == "src") s = $(i + 1) }} END { print d, s }')"
  [[ -n "$dev" && -n "$src" ]] || return 1
  hca=$(ls /sys/class/net/"$dev"/device/infiniband 2>/dev/null | head -1)
  hcas=$hca
  while read -r d _ _ _ _ s; do
    [[ "$s" == "(Up)" && -n "$d" && ",$hcas," != *",$d,"* ]] || continue
    hcas=${hcas:+$hcas,}$d
  done < <(ibdev2netdev 2>/dev/null)
  [[ -n "$hcas" ]] || return 1
  echo "$dev $src $hcas"
}

# hy4_mem_avail_gib [meminfo]: MemAvailable in GiB, one decimal.
hy4_mem_avail_gib() { awk '$1 == "MemAvailable:" { printf "%.1f\n", $2 / 1048576; f = 1 } END { exit !f }' "${1:-${HY4_MEMINFO:-/proc/meminfo}}"; }
hy4_mem_total_gib() { awk '$1 == "MemTotal:" { printf "%.1f\n", $2 / 1048576; f = 1 } END { exit !f }' "${1:-${HY4_MEMINFO:-/proc/meminfo}}"; }
# hy4_min_avail: HY4_MIN_AVAIL_GIB, "auto" resolved against this Spark's MemTotal.
hy4_min_avail() {
  if [[ "$HY4_MIN_AVAIL_GIB" == auto ]]; then
    local t; t=$(hy4_mem_total_gib) || { echo 106; return; }
    awk -v t="$t" -v g="$HY4_GMU" 'BEGIN { n = t * g; i = int(n); if (i < n) i++; print i + 2 }'
  else
    echo "$HY4_MIN_AVAIL_GIB"
  fi
}
