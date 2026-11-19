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
#   scripts/backends/spark-deepseek-pack.sh use ... --stop-glm        # GLM is serving: stop it for this swap
#
# ONLY WHILE DEEPSEEK IS THE MODEL. `use` swaps DeepSeek's pack; it is not a
# model switch. While GLM's spark-hydra-inference is active it refuses: run
# `beast-spark ds` (beastds) first, which stops GLM, selects a pack and
# activates DeepSeek, or pass --stop-glm to stop GLM explicitly (the boot
# selection and the command center then still point at GLM until `beast-spark
# ds`). `use` holds ${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock, shared with
# beast-spark, so two model or pack switches never overlap.
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
#      already has that REV, its ORG/NAME is reused. Two different packs can
#      share a size listing, so REV alone is not trusted: every snapshot that
#      already has REV must match the folder's content fingerprint (sha256 of
#      each shard's name, size and first and last MiB; cached in the folder as
#      .pack-fingerprint), checked before anything is copied, and a snapshot
#      directory that already holds a file is refused unless that file IS the
#      source's (same inode and size) instead of silently kept.
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
# gate, the command center and enrolled clients need no change. DeepSeek is the
# pair's default model since 2026-11-18 (DeepSeek-V4.1-Flash (Mia EXL3)); GLM is
# the other model this pair serves, staged as the alternative: `beast-spark glm`
# switches to it, `beast-spark ds` back to DeepSeek (spark-beast.sh; restore-glm.sh
# and activate-stock.sh are its internals).
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
GLM_UNIT="${SDP_GLM_UNIT:-spark-hydra-inference.service}"
LOCK="${SDP_LOCK:-${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock}"
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

# The model-switch lock beast-spark holds too; skipped when beast-spark (which
# already holds it) is the caller.
take_lock() {
  [[ "${SPARK_MODEL_LOCK_HELD:-}" == 1 ]] && return 0
  exec 8>>"$LOCK"
  flock -n 8 || die "another model or pack switch is running (it holds $LOCK): wait for it to finish, then retry"
  export SPARK_MODEL_LOCK_HELD=1
}

# GET $URL$1 with the key as a curl config line on fd 3 (scripts/lib/curl_auth.sh's
# idiom, inlined: this file is installed away from the checkout): never in argv.
api_get() {
  local k=""
  [[ -r "$KEY_FILE" ]] && k=$(<"$KEY_FILE")
  if [[ -z "$k" ]]; then curl -sf --max-time 3 "$URL$1"; return; fi
  [[ "$k" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || { log "the key in $KEY_FILE has unexpected characters: not sent"; return 1; }
  curl -sf --max-time 3 --config /dev/fd/3 "$URL$1" 3<<EOF
header = "Authorization: Bearer $k"
EOF
}

# sha1 of the shards' "size name" lines, sorted by name: a stable local revision.
pack_rev() {
  (cd "$1" && for f in model-*.safetensors; do echo "$(fsize "$f") $f"; done | LC_ALL=C sort -k2 | sha1sum | cut -c1-40)
}
# name+size listing of every file, to compare a mirror without reading the data
pack_listing() { (cd "$1" && find . -type f ! -name .pack-fingerprint -printf '%P %s\n' | LC_ALL=C sort | md5sum | cut -c1-32); }

# Content fingerprint: sha256 of one "name size sha256(first MiB) sha256(last MiB)"
# line per model-*.safetensors, sorted by name (C locale). Cheap (2 MiB a shard)
# and it tells apart two packs whose size listings, hence REVs, are equal.
# scripts/backends/pylib/model_intake.py pack_fingerprint() computes the same.
fsize() { stat -L -c %s "$1" 2>/dev/null || stat -L -f %z "$1"; }
sha256_hex() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -c1-64; }
fingerprint_of() {  # dir
  (cd "$1" && for f in model-*.safetensors; do
     [[ -f "$f" ]] || continue
     printf '%s %s %s %s\n' "$f" "$(fsize "$f")" "$(head -c 1048576 "$f" | sha256_hex)" "$(tail -c 1048576 "$f" | sha256_hex)"
   done | LC_ALL=C sort | sha256_hex)
}
# The pack folder's fingerprint, cached in DIR/.pack-fingerprint against its REV.
pack_fingerprint() {  # dir rev
  local c="$1/.pack-fingerprint" fp
  fp=$(sed -n "s/^rev=$2 fingerprint=\([0-9a-f]\{64\}\)\$/\1/p" "$c" 2>/dev/null | tail -1)
  if [[ -z "$fp" ]]; then
    fp=$(fingerprint_of "$1")
    { printf 'rev=%s fingerprint=%s\n' "$2" "$fp" > "$c.new" && mv -f "$c.new" "$c"; } 2>/dev/null || rm -f "$c.new" 2>/dev/null || true
  fi
  echo "$fp"
}
# Every snapshot in the cache that already has REV must hold this folder's content.
check_rev_collision() {  # src rev
  local s fp sfp
  fp=$(pack_fingerprint "$1" "$2")
  for s in "$HF_CACHE"/hub/models--*/snapshots/"$2"; do
    [[ -d "$s" ]] || continue
    sfp=$(fingerprint_of "$s")
    [[ "$sfp" == "$fp" ]] || die "$s has this folder's local revision ${2:0:8} (same shard names and sizes) but OTHER content (fingerprint ${sfp:0:12} vs ${fp:0:12} for $1). Two packs share one size listing: stage this one under its own revision by hand, or remove the stale snapshot. Nothing was changed."
  done
}
index_keys() { python3 -I -c 'import json,sys; print("\n".join(sorted(json.load(open(sys.argv[1]))["weight_map"])))' "$1/model.safetensors.index.json"; }
archs() { python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("architectures"))' "$1/config.json"; }
hub_dir() { echo "$HF_CACHE/hub/models--${1//\//--}"; }

# Read what local.sh selects now (config.sh applies the defaults). Line 6:
# "prepared" when the recipe's prepared marker equals prepared_state() (what
# start.sh would otherwise prepare again), "unprepared" when not; when
# prepared_state cannot be computed, the marker's model= field is compared.
current() {
  # shellcheck disable=SC1091
  (cd "$RT" && source scripts/config.sh >/dev/null 2>&1 || exit 1
   source scripts/nodes.sh >/dev/null 2>&1 || true
   m=$(cat "$PREPARED_MARKER" 2>/dev/null || true); p=unprepared
   if declare -F prepared_state >/dev/null && st=$(prepared_state 2>/dev/null) && [[ -n "$st" ]]; then
     if [[ "$st" == "$m" ]]; then p=prepared; fi
   elif [[ " $m " == *" model=$(printf %q "$MODEL_ID@$(snapshot_rev)") "* ]]; then
     p=prepared
   fi
   printf '%s\n%s\n%s\n%s\n%s\n%s\n' "$MODEL_ID" "$(snapshot_rev)" "$WORKER_CACHE_DIR" "${MODEL_SRC:-$STOCK_SRC}" "$HF_CACHE" "$p")
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

# Hard-link SRC into the snapshot. A file already in the snapshot must BE the
# source's file (same device, inode and size), or nothing is linked: a stale
# or foreign file is never silently kept (the old `cp -n`). Runs here or, as
# `bash -s`, on the worker.
# shellcheck disable=SC2016  # expanded by the bash that runs it
STAGE_SH='set -euo pipefail
src=$1 d=$2 rev=$3; snap="$d/snapshots/$rev"
id_of() { stat -L -c "%d:%i %s" "$1" 2>/dev/null || stat -L -f "%d:%i %z" "$1"; }
mkdir -p "$snap" "$d/refs"
[ -s "$d/refs/main" ] || printf %s "$rev" > "$d/refs/main"
bad=0
while IFS= read -r f; do
  t="$snap/$f"
  [ -e "$t" ] || [ -L "$t" ] || continue
  a=$(id_of "$src/$f"); b=$(id_of "$t")
  [ "$a" = "$b" ] || { echo "  $snap/$f is not $src/$f (inode/size $b, source $a)" >&2; bad=1; }
done < <(cd "$src" && find . \( -type f -o -type l \) ! -name .pack-fingerprint | sed "s|^\./||")
[ "$bad" = 0 ] || { echo "refusing to stage into $snap: it already holds other files (above). Remove that snapshot, or stage the pack under another revision" >&2; exit 3; }
while IFS= read -r f; do
  t="$snap/$f"
  [ -e "$t" ] || [ -L "$t" ] && continue
  mkdir -p "$(dirname "$t")"
  if [ -L "$src/$f" ]; then cp -P "$src/$f" "$t"; else ln "$src/$f" "$t"; fi
done < <(cd "$src" && find . \( -type f -o -type l \) ! -name .pack-fingerprint | sed "s|^\./||")'
stage_snapshot() {  # host("" = here) src id rev
  local host=$1 src=$2 id=$3 rev=$4 d; d="$(hub_dir "$id")"
  if [[ -z "$host" ]]; then bash -c "$STAGE_SH" stage "$src" "$d" "$rev"
  else ssh -o BatchMode=yes "$host" "bash -s -- $(printf '%q ' "$src" "$d" "$rev")" <<<"$STAGE_SH"; fi
}

# Until /v1/models lists $SERVED (not merely "something answers").
wait_ready() {
  local _
  for _ in $(seq 1 "${SDP_WAIT_TRIES:-120}"); do
    api_get /v1/models 2>/dev/null | python3 -I -c 'import json,sys; sys.exit(0 if sys.argv[1] in [m["id"] for m in json.load(sys.stdin)["data"]] else 1)' "$SERVED" 2>/dev/null && return 0
    systemctl --user is-failed --quiet "$UNIT" && return 1
    sleep "${SDP_WAIT_STEP:-5}"
  done
  return 1
}

prepare_and_start() {  # src [start: 1|0]
  systemctl --user stop "$UNIT"
  MODEL_SRC="$1" "$PREPARE_SH" || return 1
  [[ "${2:-1}" == 1 ]] || return 0
  systemctl --user start "$UNIT"
  wait_ready
}

# tee to the log, then grep the FILE: `| grep -q` in a pipeline under pipefail
# can kill tee with SIGPIPE and fail a run that passed.
verify() {  # outdir
  python3 "$PREP/verify-chat.py" "$URL" "$KEY_FILE" 2>&1 | tee "$1/canary.log" || return 1
  grep -q 391 "$1/canary.log" || return 1
  (cd "$OB" && scripts/backends/conformance.sh --url "$URL" --model "$SERVED" --backend tensorfold \
     --key-file "$KEY_FILE" --out "$1/conformance") | tee "$1/conformance.log" || return 1
  grep -q '^VERDICT: PASS' "$1/conformance.log"
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
  local src="" id="" bench=0 dry=0 stop_glm=0 stopped_glm=0
  while (( $# )); do
    case "$1" in
      --id) id=$2; shift 2 ;;
      --bench) bench=1; shift ;;
      --dry-run) dry=1; shift ;;
      --stop-glm) stop_glm=1; shift ;;
      -*) die "unknown option $1" ;;
      *) src=$1; shift ;;
    esac
  done
  [[ -n "$src" ]] || die "use: a pack folder (or 'stock') is required"
  # A pack swap, never a model switch: GLM serving means DeepSeek is not the model.
  if systemctl --user is-active --quiet "$GLM_UNIT"; then
    if (( dry )); then log "note: GLM ($GLM_UNIT) is active; the real run needs beast-spark ds first (or --stop-glm)"
    elif (( ! stop_glm )); then
      die "GLM ($GLM_UNIT) is serving: this tool swaps DeepSeek's pack only while DeepSeek is the model. Run 'beast-spark ds' (beastds) to switch to DeepSeek (set DS_PACK/DS_ID in ~/.config/openbeast/beast-spark.env for a pack other than DeepSeek-V4.1-Flash (Mia EXL3)), or pass --stop-glm to stop GLM for this swap"
    fi
  fi
  (( dry )) || take_lock
  local cur; mapfile -t cur < <(current)
  [[ ${#cur[@]} -ge 6 && -n "${cur[0]}" ]] || die "cannot read the recipe's selection ($RT/scripts/config.sh)"
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
  if [[ "$id@$rev" == "${cur[0]}@${cur[1]}" ]]; then
    [[ "${cur[5]}" != prepared ]] || { log "$id @ ${rev:0:8} is already the selected pack, and prepared"; exit 0; }
    log "$id @ ${rev:0:8} is selected but its prepared marker is stale (another pack, image or worker was prepared since): preparing it again"
  fi

  # 1. drop-in check against the serving pack
  local serving_snap; serving_snap="$(hub_dir "${cur[0]}")/snapshots/${cur[1]}"
  diff -q <(index_keys "$src") <(index_keys "$serving_snap") >/dev/null || die "$src: its tensor index differs from the serving pack's: not a drop-in"
  [[ "$(archs "$src")" == "$(archs "$serving_snap")" ]] || die "$src: config.json architectures differ from the serving pack's"
  check_rev_collision "$src" "$rev"
  log "pack: $id @ ${rev:0:8} from $src (index and architecture match the serving pack; content fingerprint checked)"
  log "plan: mirror to $WORKER, stage in $HF_CACHE/hub on both, local.sh -> $id, rank 1 cache $wcd, prepare, restart, verify$( ((bench)) && echo ', bench')"
  (( dry )) && { render_local "$LOCAL_SH" "$id" "$rev" "$wcd" "$src" | tail -6; exit 0; }

  # 2. mirror
  if [[ "$(ssh -n -o BatchMode=yes "$WORKER" "cd '$src' 2>/dev/null && find . -type f ! -name .pack-fingerprint -printf '%P %s\n' | LC_ALL=C sort | md5sum | cut -c1-32")" == "$(pack_listing "$src")" ]]; then
    log "$WORKER already holds an identical $src"
  else
    log "mirroring $src to $WORKER (shards shared with ${cur[3]} become hard links)"
    rsync -aH --partial --info=progress2 --exclude=.pack-fingerprint --link-dest="${cur[3]}" "$src/" "$WORKER:$src/"
  fi
  # 3. stage
  stage_snapshot "" "$src" "$id" "$rev"
  stage_snapshot "$WORKER" "$src" "$id" "$rev"
  # 4. local.sh (a signal from here to the verdict puts local.sh.prev back)
  local prev; mapfile -t prev < <(printf '%s\n' "${cur[@]}")
  cp -p "$LOCAL_SH" "$LOCAL_SH.prev"
  # fd 7: the terminal's stderr, for the trap (it may run inside prepare's redirect to prepare.log)
  exec 7>&2
  # shellcheck disable=SC2064  # LOCAL_SH and UNIT are fixed for this run
  trap "cp -p '$LOCAL_SH.prev' '$LOCAL_SH'; log 'interrupted: local.sh restored from local.sh.prev; $UNIT may be stopped. Re-run, or beast-spark ds / beast-spark glm' 2>&7; exit 130" INT TERM HUP
  render_local "$LOCAL_SH" "$id" "$rev" "$wcd" "$src" > "$LOCAL_SH.new" && mv "$LOCAL_SH.new" "$LOCAL_SH"
  local out; out="$RT/runs/packs/${rev:0:8}-$(date +%Y%m%dT%H%M%S)"; mkdir -p "$out"
  if systemctl --user is-active --quiet "$GLM_UNIT"; then   # --stop-glm (refused above otherwise)
    log "stopping GLM ($GLM_UNIT): --stop-glm"; systemctl --user stop "$GLM_UNIT"; stopped_glm=1
  fi
  # 5-6. prepare, start, verify; roll back on any failure
  if prepare_and_start "$src" > "$out/prepare.log" 2>&1 && verify "$out"; then
    trap - INT TERM HUP
    log "serving $id @ ${rev:0:8}: canary and conformance passed ($out)"
    (( ! stopped_glm )) || log "NOTE: GLM is stopped but still the boot selection, and the command center still points at it: run 'beast-spark ds' to make DeepSeek the selection (it skips the pack step when this pack is its DS_ID)"
  else
    log "FAILED (logs in $out): rolling back to ${prev[0]} @ ${prev[1]:0:8}"
    cp -p "$LOCAL_SH.prev" "$LOCAL_SH"
    if (( stopped_glm )); then
      prepare_and_start "${prev[3]}" 0 > "$out/rollback.log" 2>&1 || log "re-preparing ${prev[0]} failed: see $out/rollback.log"
      systemctl --user start "$GLM_UNIT"
      trap - INT TERM HUP
      die "rolled back local.sh to ${prev[0]} @ ${prev[1]:0:8} and restarted GLM (it was serving before --stop-glm)"
    fi
    prepare_and_start "${prev[3]}" > "$out/rollback.log" 2>&1 || die "rollback did not come up either: see $out/rollback.log and journalctl --user -u $UNIT"
    trap - INT TERM HUP
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
  _fingerprint) shift; fingerprint_of "$@" ;;  # test seam
  _stage) shift; HF_CACHE=$1; shift; stage_snapshot "" "$@" ;;   # test seam: HF_CACHE src id rev
  _wait_ready) wait_ready ;;                   # test seam
  _verify) shift; verify "$1" ;;               # test seam: outdir
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
