#!/bin/bash
# spark-deepseek-pack: swap the DeepSeek-V4.1-Flash EXL3 pack that spark-hydra
# serves, offline, with verification and automatic rollback. Run on spark-1
# (the head) as the account that owns the deployment.
# docs/SPARK_HYDRA.md "DeepSeek packs: spark-deepseek-pack"
#
#   scripts/backends/spark-deepseek-pack.sh list
#   scripts/backends/spark-deepseek-pack.sh use ~/Documents/<pack-folder> [--id ORG/NAME] [--bench]
#   scripts/backends/spark-deepseek-pack.sh use stock [--bench]       # Mia EAQ v1 2.9 bpw @ 45ee5d0f
#   scripts/backends/spark-deepseek-pack.sh use ... --dry-run         # print the plan, change nothing
#
# WHAT A PACK IS. Any EXL3 quantization of DeepSeek-V4.1-Flash-1017 with the
# same tensor layout as Mia's EAQ v1 2.9 bpw build (39 shards, the same
# model.safetensors.index.json keys), i.e. what Mia's two-Spark recipe at
# c7f2b85 loads. Engram's tables stay the base model's shards 47/48 and are not
# part of a pack. A folder with a different index is refused before anything
# stops: it is not a drop-in, it needs its own recipe.
#
# WHAT `use` DOES, in order (nothing serving changes before step 5):
#   1. Checks the folder: index + config present, index tensor keys equal to
#      the serving pack's, config.json architectures equal.
#   2. Mirrors it to spark-2 at the same path (rsync -aH, --link-dest against
#      the serving pack, so shards shared with it become hard links), unless
#      spark-2 already has an identical name+size listing.
#   3. Stages it on BOTH hosts as an HF-cache snapshot by hard link (no extra
#      disk): $HF_CACHE/hub/models--ORG--NAME/snapshots/REV. A local pack has
#      no Hub revision: REV is the sha1 of its shards' "size name" listing, so
#      the same folder always gets the same REV. If some snapshot in the cache
#      already has that REV, its ORG/NAME is reused.
#   4. Writes the managed block of runtime/scripts/local.sh (MODEL_ID,
#      MODEL_REVISION, WORKER_CACHE_DIR, MODEL_SRC); the old file is kept as
#      local.sh.prev. WORKER_CACHE_DIR is per revision (worker-rank-cache-REV8):
#      the engine names rank files by the tensor INDEX, not the weights, so
#      two packs with one layout produce the same rank1of2-<hash>.bin name, and
#      a shared directory would serve the old pack's rank 1 as "ready".
#   5. Stops deepseek-v41-inference (the rank-file builders need its memory),
#      runs spark-deepseek-prepare-local.sh (rank 0 and rank 1 files built on the head,
#      ~106 GB each, rank 1 rsynced to spark-2; ~15 min the first time, seconds
#      when they exist), starts the unit, waits for /v1/models.
#   6. Verifies: verify-chat.py (17 x 23 = 391) and scripts/backends/
#      conformance.sh. Any failure restores local.sh.prev, re-prepares the
#      previous pack (its files are still on disk: seconds) and restarts it.
#   7. --bench: tools/bench.py (prefill 2K/8K/32K, decode, 1/2/4 streams, the
#      fixed salt) into runs/packs/REV8.json, compared with the previous pack's
#      run when one exists. Thinking off, so the runs are comparable.
#
# The served model name stays DeepSeek-V4.1-Flash-1017 (SERVED_NAME), so the
# gate, the command center and enrolled clients need no change. GLM is the
# other model this pair serves: restore-glm.sh switches to it, activate-stock.sh
# back to whatever pack local.sh selects.
#
# Offline: nothing here reaches the network beyond the spark-1 <-> spark-2 link.
# Disk: a pack costs its folder (hard-linked into the cache) plus ~106 GB
# (rank 0) + ~106 GB (rank 1 build) on spark-1 and ~106 GB on spark-2. Old
# rank files are never deleted by this script.
set -euo pipefail

# Test seams.
PREP="${SDP_PREP:-$HOME/openbeast-recipes/deepseek-prep}"
RT="$PREP/runtime"
LOCAL_SH="${SDP_LOCAL_SH:-$RT/scripts/local.sh}"
WORKER="${SDP_WORKER:-spark-2}"
UNIT="${SDP_UNIT:-deepseek-v41-inference.service}"
OB="${SDP_OPENBEAST:-$HOME/openbeast}"
KEY_FILE="${SDP_KEY_FILE:-$HOME/.config/openbeast/tensorfold-api-key}"
URL="${SDP_URL:-http://127.0.0.1:8000}"
SERVED="${SDP_SERVED:-DeepSeek-V4.1-Flash-1017}"
PREPARE_SH="${SDP_PREPARE:-$(cd "$(dirname "$0")" && pwd)/spark-deepseek-prepare-local.sh}"
STOCK_SRC="$HOME/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw"
STOCK_ID=Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
STOCK_REV=45ee5d0fd106c94a3d77f721e09cbb2f50e5a16b
BEGIN='# >>> spark-deepseek-pack (managed: scripts/backends/spark-deepseek-pack.sh)'
END='# <<< spark-deepseek-pack'
export PATH="$HOME/.local/bin:$PATH"   # hf, for prepare.sh's sourced steps

log() { printf '[spark-deepseek-pack] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# sha1 of the shards' "size name" lines, sorted by name: a stable local revision.
pack_rev() {
  (cd "$1" && for f in model-*.safetensors; do stat -c '%s %n' "$f"; done | LC_ALL=C sort -k2 | sha1sum | cut -c1-40)
}
# name+size listing of every file, to compare a mirror without reading the data
pack_listing() { (cd "$1" && find . -type f -printf '%P %s\n' | LC_ALL=C sort | md5sum | cut -c1-32); }
index_keys() { python3 -I -c 'import json,sys; print("\n".join(sorted(json.load(open(sys.argv[1]))["weight_map"])))' "$1/model.safetensors.index.json"; }
archs() { python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("architectures"))' "$1/config.json"; }
hub_dir() { echo "$HF_CACHE/hub/models--${1//\//--}"; }

# Read what local.sh selects now (config.sh applies the defaults).
current() {
  # shellcheck disable=SC1091
  (cd "$RT" && source scripts/config.sh >/dev/null 2>&1 &&
   printf '%s\n%s\n%s\n%s\n%s\n' "$MODEL_ID" "$(snapshot_rev)" "$WORKER_CACHE_DIR" "${MODEL_SRC:-$STOCK_SRC}" "$HF_CACHE")
}

# local.sh with the managed block replaced (or appended); prints the new text.
render_local() {  # file id rev wcd src
  python3 -I - "$@" "$BEGIN" "$END" <<'PY'
import sys
path, mid, rev, wcd, src, begin, end = sys.argv[1:8]
lines = open(path).read().splitlines()
out, skip = [], False
for l in lines:
    if l == begin: skip = True; continue
    if l == end: skip = False; continue
    if not skip: out.append(l)
while out and out[-1] == "": out.pop()
out += ["", begin, f"MODEL_ID={mid}", f"MODEL_REVISION={rev}", f"WORKER_CACHE_DIR={wcd}", f"MODEL_SRC={src}", end]
print("\n".join(out))
PY
}

stage_snapshot() {  # host("" = here) src id rev
  local host=$1 src=$2 id=$3 rev=$4 d; d="$(hub_dir "$id")"
  local cmd="set -e; mkdir -p '$d/snapshots/$rev' '$d/refs'; [ -s '$d/refs/main' ] || printf %s '$rev' > '$d/refs/main'; cp -aln '$src/.' '$d/snapshots/$rev/' 2>/dev/null || cp -al --update=none '$src/.' '$d/snapshots/$rev/'"
  if [[ -z "$host" ]]; then bash -c "$cmd"; else ssh -n -o BatchMode=yes "$host" "$cmd"; fi
}

wait_ready() {
  local _
  for _ in $(seq 1 120); do
    curl -sf --max-time 3 -H "Authorization: Bearer $(cat "$KEY_FILE")" "$URL/v1/models" >/dev/null 2>&1 && return 0
    systemctl --user is-failed --quiet "$UNIT" && return 1
    sleep 5
  done
  return 1
}

prepare_and_start() {  # src
  systemctl --user stop "$UNIT"
  MODEL_SRC="$1" "$PREPARE_SH" || return 1
  systemctl --user start "$UNIT"
  wait_ready
}

verify() {  # outdir
  python3 "$PREP/verify-chat.py" "$URL" "$KEY_FILE" | tee "$1/canary.log" | grep -q 391 || return 1
  (cd "$OB" && scripts/backends/conformance.sh --url "$URL" --model "$SERVED" --backend tensorfold \
     --key-file "$KEY_FILE" --out "$1/conformance") | tee "$1/conformance.log" | grep -q '^VERDICT: PASS'
}

cmd_list() {
  local cur; mapfile -t cur < <(current)
  echo "serving (local.sh): ${cur[0]} @ ${cur[1]:0:8}   rank 1 cache: ${cur[2]}   source: ${cur[3]}"
  echo "unit: $(systemctl --user is-active "$UNIT" 2>/dev/null || true) / $(systemctl --user is-enabled "$UNIT" 2>/dev/null || true)"
  echo "staged packs in ${cur[4]}/hub:"
  local m s
  for m in "${cur[4]}"/hub/models--*; do
    [[ "$m" == *deepseek-ai--DeepSeek-V4.1-Flash-1017 ]] && continue   # Engram's base shards
    for s in "$m"/snapshots/*; do [[ -d "$s" ]] && echo "  $(basename "$m" | sed 's/^models--//; s/--/\//') @ $(basename "$s" | cut -c1-8)"; done
  done
  local prev=none
  if [[ -f "$LOCAL_SH.prev" ]]; then
    prev=$(grep -E '^MODEL_ID=' "$LOCAL_SH.prev" | tail -1 | cut -d= -f2- || true)
    [[ -n "$prev" ]] || prev="$STOCK_ID (the recipe default)"
  fi
  echo "previous (local.sh.prev): $prev"
}

cmd_use() {
  local src="" id="" bench=0 dry=0
  while (( $# )); do
    case "$1" in
      --id) id=$2; shift 2 ;;
      --bench) bench=1; shift ;;
      --dry-run) dry=1; shift ;;
      -*) die "unknown option $1" ;;
      *) src=$1; shift ;;
    esac
  done
  [[ -n "$src" ]] || die "use: a pack folder (or 'stock') is required"
  local cur; mapfile -t cur < <(current)
  HF_CACHE=${cur[4]}
  local rev wcd
  if [[ "$src" == stock ]]; then
    src=$STOCK_SRC; id=$STOCK_ID; rev=$STOCK_REV; wcd="$HF_CACHE/worker-rank-cache"
  else
    src=$(realpath "$src")
    [[ -s "$src/model.safetensors.index.json" && -s "$src/config.json" ]] || die "$src has no model.safetensors.index.json / config.json"
    rev=$(pack_rev "$src")
    if [[ -z "$id" ]]; then
      local hit; hit=$(compgen -G "$HF_CACHE/hub/models--*/snapshots/$rev" | head -1 || true)
      if [[ -n "$hit" ]]; then id=$(basename "$(dirname "$(dirname "$hit")")" | sed 's/^models--//; s/--/\//')
      else id="local/$(basename "$src")"; fi
    fi
    wcd="$HF_CACHE/worker-rank-cache-${rev:0:8}"
  fi
  [[ "$src" != *"'"* && "$id" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || die "unusable path or id: $src / $id"
  [[ "$id@$rev" != "${cur[0]}@${cur[1]}" ]] || { log "$id @ ${rev:0:8} is already the serving pack"; exit 0; }

  # 1. drop-in check against the serving pack
  local serving_snap; serving_snap="$(hub_dir "${cur[0]}")/snapshots/${cur[1]}"
  diff -q <(index_keys "$src") <(index_keys "$serving_snap") >/dev/null || die "$src: its tensor index differs from the serving pack's: not a drop-in"
  [[ "$(archs "$src")" == "$(archs "$serving_snap")" ]] || die "$src: config.json architectures differ from the serving pack's"
  log "pack: $id @ ${rev:0:8} from $src (index and architecture match the serving pack)"
  log "plan: mirror to $WORKER, stage in $HF_CACHE/hub on both, local.sh -> $id, rank 1 cache $wcd, prepare, restart, verify$( ((bench)) && echo ', bench')"
  (( dry )) && { render_local "$LOCAL_SH" "$id" "$rev" "$wcd" "$src" | tail -6; exit 0; }

  # 2. mirror
  if [[ "$(ssh -n -o BatchMode=yes "$WORKER" "cd '$src' 2>/dev/null && find . -type f -printf '%P %s\n' | LC_ALL=C sort | md5sum | cut -c1-32")" == "$(pack_listing "$src")" ]]; then
    log "$WORKER already holds an identical $src"
  else
    log "mirroring $src to $WORKER (shards shared with ${cur[3]} become hard links)"
    rsync -aH --partial --info=progress2 --link-dest="${cur[3]}" "$src/" "$WORKER:$src/"
  fi
  # 3. stage
  stage_snapshot "" "$src" "$id" "$rev"
  stage_snapshot "$WORKER" "$src" "$id" "$rev"
  # 4. local.sh
  local prev; mapfile -t prev < <(printf '%s\n' "${cur[@]}")
  cp -p "$LOCAL_SH" "$LOCAL_SH.prev"
  render_local "$LOCAL_SH" "$id" "$rev" "$wcd" "$src" > "$LOCAL_SH.new" && mv "$LOCAL_SH.new" "$LOCAL_SH"
  local out; out="$RT/runs/packs/${rev:0:8}-$(date +%Y%m%dT%H%M%S)"; mkdir -p "$out"
  # 5-6. prepare, start, verify; roll back on any failure
  if prepare_and_start "$src" > "$out/prepare.log" 2>&1 && verify "$out"; then
    log "serving $id @ ${rev:0:8}: canary and conformance passed ($out)"
  else
    log "FAILED (logs in $out): rolling back to ${prev[0]} @ ${prev[1]:0:8}"
    cp -p "$LOCAL_SH.prev" "$LOCAL_SH"
    prepare_and_start "${prev[3]}" > "$out/rollback.log" 2>&1 || die "rollback did not come up either: see $out/rollback.log and journalctl --user -u $UNIT"
    die "rolled back to ${prev[0]} @ ${prev[1]:0:8}, which is serving again"
  fi
  # 7. bench
  if (( bench )); then
    mkdir -p "$RT/runs/packs"
    (cd "$RT" && API_KEY_FILE=$KEY_FILE python3 -I tools/bench.py --url "$URL" --model "$SERVED" --suite prefill,decode,multi \
       --reps 2 --salt packs --label "$id@${rev:0:8}" --out "runs/packs/${rev:0:8}.json") > "$out/bench.log" 2>&1 || log "bench failed: $out/bench.log"
    if [[ -s "$RT/runs/packs/${prev[1]:0:8}.json" ]]; then
      (cd "$RT" && python3 -I tools/bench.py --compare "runs/packs/${prev[1]:0:8}.json" "runs/packs/${rev:0:8}.json")
    else
      tail -18 "$out/bench.log"
    fi
  fi
}

case "${1:-}" in
  list) cmd_list ;;
  use) shift; cmd_use "$@" ;;
  _render_local) shift; render_local "$@" ;;   # test seam
  _pack_rev) shift; pack_rev "$@" ;;           # test seam
  *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
