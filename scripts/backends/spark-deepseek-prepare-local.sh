#!/usr/bin/env bash
# CPU-only, offline preparation of a locally staged DeepSeek-V4.1-Flash EXL3 pack, generalized from prepare-stock-local.sh.
# The pack is chosen by MODEL_ID / MODEL_REVISION / WORKER_CACHE_DIR in runtime/scripts/local.sh (staged under
# $HF_CACHE/hub by spark-deepseek-pack.sh, which calls this). MODEL_SRC: the pack's folder, present at the same path
# on the worker (the mirror). The worker is the recipe's WORKER (runtime/scripts/local.sh: spark-2 on spark-hydra),
# reached with ssh -o BatchMode=yes (a missing key fails at once instead of prompting). Offline: the kernel kit must
# already be on disk (it is fetched once, online, by the recipe's prepare.sh). Runs against the host-local runtime at
# ~/openbeast-recipes/deepseek-prep (docs/SPARK_HYDRA.md).
set -euo pipefail
cd ~/openbeast-recipes/deepseek-prep/runtime
test "$(git rev-parse HEAD)" = c7f2b85100f0a7b099c92a4e21825e30d93c9c51
echo '60e106403db2e6e2b49ebd7009720fa53867c82876678d965655e9e9398ad070  scripts/prepare.sh' | sha256sum -c -
source scripts/config.sh
source scripts/nodes.sh
test "$ABLIT" = 0
W=$(worker_host 1)
[[ -n "$W" && "$W" != *"'"* ]] || { echo "spark-deepseek-prepare-local: WORKER is not set in runtime/scripts/local.sh" >&2; exit 1; }
SSH=(ssh -n -o BatchMode=yes)
test "$(docker image inspect -f '{{index .Config.Labels "tf.patches"}}' "$IMAGE")" = "$(image_hash)"
mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/prepare.lock"
flock -n 9
MODEL_SRC="${MODEL_SRC:-$HOME/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw}"
[[ "$MODEL_SRC" != *"'"* ]]
SNAP="$(model_cache_dir)/snapshots/$(snapshot_rev)"
test -s "$SNAP/model.safetensors.index.json"
in_hf() {
  docker run --rm --network none --memory=48g --memory-swap=48g --cpus=8 --user "$(id -u):$(id -g)" -e HOME=/tmp -e OMP_NUM_THREADS=4 -v "$HF_CACHE:/hf" "$@"
}
# Offline: never fetch the kit here (prepare.sh's step 4 would git-fetch it from the network when .git is missing).
if [[ ! -d "$HF_CACHE/$KIT_SUB/.git" ]]; then
  echo "spark-deepseek-prepare-local: the kernel kit $HF_CACHE/$KIT_SUB has no .git: it was never fetched, or was removed." >&2
  echo "  This tool is offline and does not fetch it. Restore it from a backup (openbeast-models/deepseek-hf/tensorfold-dsv41/kit-*/," >&2
  echo "  .git included; the worker's copy has no .git), or run the recipe's scripts/prepare.sh once with the network up. Nothing was changed." >&2
  exit 1
fi
# Reuse the exact pinned kit, manifest, token map, rank-0 and chat-template steps.
# shellcheck disable=SC1090  # steps 4-5 of the pinned prepare.sh (its sha256 is checked above)
source <(sed -n '/^# ---* 4\. the kit/,/^# ---* 6\. the worker/p' scripts/prepare.sh)
test -s "$DATA_DIR/dsv41_token_map.json"
test -s "$DATA_DIR/model/config.json"
# Prepare rank 1 on the head too; the worker has less memory available.
# Built here under the same name the worker uses (WORKER_CACHE_DIR must sit directly in HF_CACHE, same path on both).
rank_dir="$HF_CACHE/$(basename "$WORKER_CACHE_DIR")"
test "$WORKER_CACHE_DIR" = "$rank_dir"
mkdir -p "$rank_dir"
rank_hash=$(in_hf --entrypoint bash "$IMAGE" -c "tf-dsv41-rank-cache $SNAP_C /tmp/x 1 2 --index-only | sha256sum | cut -c1-20")
if [[ ! -s "$rank_dir/rank1of2-$rank_hash.bin" ]]; then
  in_hf --entrypoint tf-dsv41-rank-cache "$IMAGE" "$SNAP_C" "/hf/$(basename "$rank_dir")" 1 2
fi
test -s "$rank_dir/rank1of2-$rank_hash.bin"
"${SSH[@]}" "$W" "mkdir -p '$HF_CACHE/$DATA_SUB' '$HF_CACHE/$KIT_SUB' '$WORKER_CACHE_DIR'"
copy=(ionice -c3 nice -n 15 rsync -a --partial-dir=.rsync-partial -e "ssh -o BatchMode=yes")
"${copy[@]}" "$rank_dir/" "$W:$WORKER_CACHE_DIR/"
"${copy[@]}" --exclude rank-cache "$DATA_DIR/" "$W:$HF_CACHE/$DATA_SUB/"
"${copy[@]}" --exclude .git "$KIT_DIR/" "$W:$HF_CACHE/$KIT_SUB/"
# Reuse the mirrored Documents copy for the worker's checkpoint (hard links; spark-deepseek-pack.sh staged it already).
"${SSH[@]}" "$W" "mkdir -p '$SNAP' && cp -aln '$MODEL_SRC/.' '$SNAP/' && rm -f '$SNAP/.pack-fingerprint'"
for _ in $(seq 1 180); do
  [[ "$(image_ident "$IMAGE")" == "$(worker_image_ident 1 "$IMAGE")" ]] && break
  sleep 10
done
test "$(image_ident "$IMAGE")" = "$(worker_image_ident 1 "$IMAGE")"
worker_files_check 1
for _ in $(seq 1 360); do
  if [[ -s ../engram-worker-sha256.log ]] && [[ $(grep -c ': OK$' ../engram-worker-sha256.log) -eq 4 ]]; then break; fi
  sleep 10
done
test "$(grep -c ': OK$' ../engram-worker-sha256.log)" -eq 4
test "$(worker 1 "stat -c %s '$WORKER_ENGRAM_DIR/model-00047-of-00048.safetensors'")" = 101535150936
test "$(worker 1 "stat -c %s '$WORKER_ENGRAM_DIR/model-00048-of-00048.safetensors'")" = 101537926640
write_prepared_marker
DRY_RUN=1 ./start.sh > "../launch-dry-run-${MODEL_REVISION:0:8}.log"
