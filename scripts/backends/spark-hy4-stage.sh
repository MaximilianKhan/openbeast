#!/usr/bin/env bash
# spark-hy4-stage: stage Hy4-flash (NVFP4) for vLLM TP2 on the spark-hydra
# pair, offline-capable, without disturbing the model that serves. Run on
# spark-1 (the head) from a checkout, as the deployment's account.
# docs/SPARK_HYDRA.md "Hy4-flash (NVFP4, vLLM TP2): staged"
#
#   scripts/backends/spark-hy4-stage.sh                    # = --dry-run: the plan, and what is staged
#   scripts/backends/spark-hy4-stage.sh --run              # every step below, in order (resumable)
#   scripts/backends/spark-hy4-stage.sh --run --only weights,verify    # some steps
#   scripts/backends/spark-hy4-stage.sh --check            # what is staged where; exit 0 only if all of it
#   scripts/backends/spark-hy4-stage.sh install [--dry-run]  # the runtime + the unit (enables nothing)
#
# Steps (--only takes these names):
#   weights  hf download atakans/Hy4-flash-NVFP4 at 30f201ac into
#            ~/Documents/atakans-Hy4-flash-NVFP4 (offline: --weights-from DIR copies a
#            drive's copy instead). A stall watchdog restarts a crawling download (it
#            resumes): under HY4_STALL_MB (50) MB on disk in HY4_STALL_SECS (180) s,
#            the download is killed and started again, up to HY4_STALL_RESTARTS (5) times
#   verify   every file of the revision present at its Hub size (FILES.hub), every LFS
#            file's sha256 equal to the Hub's (SHA256SUMS.hub-lfs, vendored beside this
#            script and written into the folder); then .hy4-verified
#   mirror   rsync the folder to spark-2 over the QSFP link (never --delete), verify it
#            there the same way, .hy4-verified there
#   image    docker pull the base image BY DIGEST, docker build the recipe image from
#            scripts/backends/hy4-vllm/image/ (offline: --image-from TAR loads a saved tar)
#   save     docker save the image to ~/Documents/hy4-vllm-image/<tag>.tar + .sha256 +
#            MANIFEST (the offline restore)
#   ship     docker load that tar on spark-2 (the saved bytes, over the link); the image
#            id there must equal the head's
# Every download, copy and checksum runs in a memory-capped user scope
# (systemd-run --user --scope -p MemoryMax=8G -p MemoryHigh=6G; the far end of
# the rsync too), so the page cache it fills is reclaimed inside it and never
# taken from a serving model's MemAvailable (spark-1 serves DeepSeek, whose
# memguard kills its rank under 2.5 GiB). The docker steps (image, save, ship)
# run in the docker daemon, outside any scope: they refuse while an inference
# unit is active unless --while-serving.
#
# Never touched, on either Spark: ~/Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw
# (the EXL3 pack, kept on purpose). No step deletes anything.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT="$HERE/hy4-vllm"
# shellcheck source=hy4-vllm/config.sh
source "$KIT/config.sh"
# shellcheck disable=SC2034  # hy4_log's prefix
HY4_TAG=spark-hy4-stage
log() { hy4_log "$@"; }
die() { hy4_die "$@"; }

STEPS_ALL="weights verify mirror image save ship"
MODE=plan DRY_GIVEN=0 ONLY="" WEIGHTS_FROM="" IMAGE_FROM="" WHILE_SERVING=0 SUB=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    install)         SUB=install; MODE=run ;;
    --dry-run)       MODE=plan; DRY_GIVEN=1 ;;
    --run)           MODE=run ;;
    --check|check)   MODE=check ;;
    --only)          ONLY="${2:?--only needs step names}"; shift ;;
    --weights-from)  WEIGHTS_FROM="${2:?--weights-from needs a folder}"; shift ;;
    --image-from)    IMAGE_FROM="${2:?--image-from needs a tar}"; shift ;;
    --while-serving) WHILE_SERVING=1 ;;
    -h|--help)       sed -n '2,39p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)               die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done
STEPS=${ONLY//,/ }; STEPS=${STEPS:-$STEPS_ALL}
for s in $STEPS; do [[ " $STEPS_ALL " == *" $s "* ]] || die "--only: no step '$s' (steps: $STEPS_ALL)"; done
want() { [[ " $STEPS " == *" $1 "* ]]; }

hy4_check_settings
on_worker() { "$HY4_SSH" -o BatchMode=yes -o ConnectTimeout=10 "$HY4_WORKER" "$@"; }
dk() { "$HY4_DOCKER" "$@"; }
q() { printf '%q ' "$@"; }
SYSTEMCTL="${HY4_SYSTEMCTL:-systemctl}"
HF="${HY4_HF:-$(command -v hf || echo "$HOME/.local/bin/hf")}"
TAR="$HY4_IMAGE_DIR/${HY4_IMAGE//[:\/]/_}.tar"
# the revision's file list and LFS sums (vendored; test seams)
FILES_HUB="${HY4_FILES_HUB:-$KIT/FILES.hub}"
SUMS_HUB="${HY4_SUMS_HUB:-$KIT/SHA256SUMS.hub-lfs}"
SCOPE=(systemd-run --user --scope --quiet -p "MemoryMax=$HY4_SCOPE_MAX" -p "MemoryHigh=$HY4_SCOPE_HIGH" --)
# capped CMD...: run CMD in the memory-capped scope (niced), or just niced with HY4_SCOPE=0
capped() {
  if [[ "$HY4_SCOPE" == 1 ]]; then nice -n 10 "${HY4_SYSTEMD_RUN:-${SCOPE[0]}}" "${SCOPE[@]:1}" "$@"
  else nice -n 10 "$@"; fi
}
# the same scope prefix for a command run on the worker (a string for ssh)
remote_capped() { local c; if [[ "$HY4_SCOPE" == 1 ]]; then c=$(q "${SCOPE[@]}"); echo "nice -n 10 ${c% }"; else echo "nice -n 10"; fi; }
serving_units() {
  local u out=""
  for u in deepseek-v41-inference.service spark-hydra-inference.service hy4-vllm-inference.service; do
    "$SYSTEMCTL" --user is-active --quiet "$u" 2>/dev/null && out+="$u "
  done
  echo "${out% }"
}

# ---- folder checks, self-contained (sent to the worker with declare -f)
# hy4_files_check DIR < FILES.hub: every listed file present at its size; prints "ok N" or the first problems.
hy4_files_check() {
  local d=$1 size path n=0 bad=0 got
  while read -r size _ path; do
    [[ -z "$size" || "$size" == \#* ]] && continue
    n=$((n + 1))
    got=$(stat -L -c %s "$d/$path" 2>/dev/null || stat -L -f %z "$d/$path" 2>/dev/null || echo missing)
    if [[ "$got" != "$size" ]]; then
      bad=$((bad + 1)); (( bad <= 5 )) && echo "bad $path: $got bytes, the Hub has $size"
    fi
  done
  (( bad == 0 )) && echo "ok $n" || { echo "bad $bad of $n files"; return 1; }
}
hy4_sums_check() {  # DIR: sha256 of every LFS file against SHA256SUMS.hub-lfs in DIR
  cd "$1" || return 1
  if command -v sha256sum >/dev/null 2>&1; then sha256sum -c --quiet SHA256SUMS.hub-lfs
  else shasum -a 256 -c --quiet SHA256SUMS.hub-lfs; fi
}
marker_text() {
  printf 'revision=%s\nrepo=%s\nverified=%s\nhost=%s\nsums=%s\n' "$HY4_REVISION" "$HY4_REPO" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$(hy4_sha256 < "$SUMS_HUB")"
}
verified_here() { grep -q "^revision=$HY4_REVISION\$" "$HY4_MODEL_DIR/.hy4-verified" 2>/dev/null; }
verified_there() { on_worker "grep -q '^revision=$HY4_REVISION\$' $(q "$HY4_MODEL_DIR/.hy4-verified")" 2>/dev/null; }
image_id() { dk image inspect -f '{{.Id}}' "$1" 2>/dev/null; }
image_id_there() { on_worker "docker image inspect -f '{{.Id}}' $(q "$1")" 2>/dev/null; }
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR == 2 { printf "%d\n", $4 / 1000000 }'; }

# ---- the download stall watchdog. A Hub download can crawl (~1 MB/s for hours) on one
# bad connection; a fresh one is fast at once, and hf download resumes. So: while the
# download runs, every HY4_STALL_SECS the bytes under its folder must have grown by
# HY4_STALL_MB, or it is killed and started again, up to HY4_STALL_RESTARTS times.
STALL_MB=${HY4_STALL_MB:-50} STALL_SECS=${HY4_STALL_SECS:-180} STALL_RESTARTS=${HY4_STALL_RESTARTS:-5}
STALL_POLL=${HY4_STALL_POLL:-10}   # seconds between liveness checks (a test seam)
for v in STALL_MB STALL_SECS STALL_RESTARTS; do
  [[ "${!v}" =~ ^[0-9]+$ ]] || die "HY4_${v}=${!v}: a whole number, please"
done
disk_kb() { du -sk "$1" 2>/dev/null | awk '{ print $1 + 0 }'; }
kill_tree() {  # PID: TERM it and every descendant, children first
  local c
  for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done
  kill -TERM "$1" 2>/dev/null || true
}
# watched_download DIR CMD...: run CMD (a resumable download into DIR) under the watchdog
watched_download() {
  local dir=$1 restarts=0 pid rc t0 b0 b stalled grown; shift
  while :; do
    "$@" & pid=$!
    # shellcheck disable=SC2064  # this pid, now
    trap "kill_tree $pid; exit 130" INT TERM
    stalled=0 t0=$SECONDS b0=$(disk_kb "$dir")
    while kill -0 "$pid" 2>/dev/null; do
      sleep "$STALL_POLL"
      kill -0 "$pid" 2>/dev/null || break
      (( STALL_SECS > 0 && SECONDS - t0 >= STALL_SECS )) || continue
      b=$(disk_kb "$dir")
      if (( b - b0 < STALL_MB * 1024 )); then stalled=1 grown=$(( (b - b0) / 1024 )); break; fi
      t0=$SECONDS b0=$b
    done
    if (( stalled == 0 )); then
      rc=0; wait "$pid" || rc=$?
      trap - INT TERM
      (( rc == 0 )) || die "weights: the download failed (exit $rc); re-run (it resumes), or offline: --weights-from DIR"
      return 0
    fi
    kill_tree "$pid"; wait "$pid" 2>/dev/null || true
    trap - INT TERM
    restarts=$((restarts + 1))
    if (( restarts > STALL_RESTARTS )); then
      die "weights: the download stalled $restarts times (each under $STALL_MB MB in ${STALL_SECS} s); gave up. Re-run later (it resumes), or offline: --weights-from DIR"
    fi
    log "weights: stalled: ${grown} MB in ${STALL_SECS} s (under $STALL_MB MB): killed; restart $restarts of $STALL_RESTARTS (it resumes)"
  done
}

# ---- the steps
step_weights() {
  hy4_refuse_protected "$HY4_MODEL_DIR" "download into"
  if verified_here; then log "weights: $HY4_MODEL_DIR is already verified at ${HY4_REVISION:0:8}: skipped"; return; fi
  mkdir -p "$HY4_MODEL_DIR"
  local free; free=$(free_gb "$HY4_MODEL_DIR"); [[ -z "$free" || "$free" -ge 170 ]] || die "weights: ${free} GB free under $HY4_MODEL_DIR, 165 GB needed (+ margin)"
  if [[ -n "$WEIGHTS_FROM" ]]; then
    hy4_refuse_protected "$WEIGHTS_FROM" "copy from"
    [[ -f "$WEIGHTS_FROM/config.json" ]] || die "--weights-from $WEIGHTS_FROM: no config.json there"
    log "weights: copying $WEIGHTS_FROM -> $HY4_MODEL_DIR (offline; capped scope)"
    capped "${HY4_RSYNC:-rsync}" -a --partial --exclude .hy4-verified "$WEIGHTS_FROM/" "$HY4_MODEL_DIR/"
  else
    [[ -x "$HF" ]] || command -v "$HF" >/dev/null 2>&1 || die "weights: no hf CLI ($HF); offline: --weights-from DIR"
    log "weights: $HF download $HY4_REPO --revision $HY4_REVISION --local-dir $HY4_MODEL_DIR (capped scope; resumable)"
    watched_download "$HY4_MODEL_DIR" capped "$HF" download "$HY4_REPO" --revision "$HY4_REVISION" --local-dir "$HY4_MODEL_DIR"
  fi
}
step_verify() {
  hy4_refuse_protected "$HY4_MODEL_DIR" "verify"
  [[ -d "$HY4_MODEL_DIR" ]] || die "verify: no $HY4_MODEL_DIR (run the weights step)"
  cp "$SUMS_HUB" "$HY4_MODEL_DIR/SHA256SUMS.hub-lfs"
  local r; r=$(hy4_files_check "$HY4_MODEL_DIR" < "$FILES_HUB") || die "verify: $HY4_MODEL_DIR does not match the revision's file list: $r"
  log "verify: $r files at the Hub's sizes; sha256 of the $(wc -l < "$SUMS_HUB" | tr -d ' ') LFS files (~165 GB, a few minutes)"
  capped bash -c "$(declare -f hy4_sums_check); hy4_sums_check $(q "$HY4_MODEL_DIR")" || die "verify: sha256 mismatch in $HY4_MODEL_DIR (above): re-run the weights step"
  marker_text "$(hostname)" > "$HY4_MODEL_DIR/.hy4-verified"
  log "verify: OK, $HY4_MODEL_DIR/.hy4-verified"
}
step_mirror() {
  verified_here || die "mirror: $HY4_MODEL_DIR is not verified here yet (verify step first)"
  if verified_there; then log "mirror: $HY4_WORKER already has it verified: skipped"; return; fi
  on_worker "mkdir -p $(q "$HY4_MODEL_DIR")" ||
    die "mirror: could not create $HY4_MODEL_DIR on $HY4_WORKER"
  log "mirror: rsync -> $HY4_WORKER:$HY4_MODEL_DIR (QSFP link; capped on both ends; never --delete)"
  capped "${HY4_RSYNC:-rsync}" -a --partial --exclude .hy4-verified --exclude .cache/ \
    -e "$HY4_SSH -o BatchMode=yes" --rsync-path="$(remote_capped) rsync" \
    "$HY4_MODEL_DIR/" "$HY4_WORKER:$HY4_MODEL_DIR/"
  local r
  r=$(on_worker "$(declare -f hy4_files_check); hy4_files_check $(q "$HY4_MODEL_DIR")" < "$FILES_HUB") ||
    die "mirror: $HY4_WORKER's copy does not match the file list: $r"
  log "mirror: $r files on $HY4_WORKER; sha256 there"
  on_worker "$(remote_capped) bash -c $(q "$(declare -f hy4_sums_check); hy4_sums_check $(q "$HY4_MODEL_DIR")")" ||
    die "mirror: sha256 mismatch on $HY4_WORKER: re-run the mirror step"
  marker_text "$HY4_WORKER" | on_worker "cat > $(q "$HY4_MODEL_DIR/.hy4-verified")"
  log "mirror: OK, verified on $HY4_WORKER"
}
docker_guard() {
  local s; s=$(serving_units)
  [[ -z "$s" || "$WHILE_SERVING" == 1 ]] && return 0
  die "$1: $s is active. The docker daemon's pull/build/save/load is outside any memory scope and the serving model's memguard may kill its rank: run this between models, or pass --while-serving to accept that"
}
step_image() {
  if [[ -n "$(image_id "$HY4_IMAGE")" ]]; then log "image: $HY4_IMAGE is here: skipped"; return; fi
  docker_guard image
  if [[ -n "$IMAGE_FROM" ]]; then
    [[ -f "$IMAGE_FROM" ]] || die "--image-from $IMAGE_FROM: no such file"
    if [[ -f "$IMAGE_FROM.sha256" ]]; then
      [[ "$(capped bash -c "$(declare -f hy4_sha256); hy4_sha256 < $(q "$IMAGE_FROM")")" == "$(cut -c1-64 "$IMAGE_FROM.sha256")" ]] ||
        die "--image-from: $IMAGE_FROM does not match $IMAGE_FROM.sha256"
    else log "image: WARNING no $IMAGE_FROM.sha256 beside the tar: loaded unverified"; fi
    log "image: docker load < $IMAGE_FROM (offline)"
    dk load -i "$IMAGE_FROM" >/dev/null
    [[ -n "$(image_id "$HY4_IMAGE")" ]] || die "image: $IMAGE_FROM did not contain $HY4_IMAGE"
    return
  fi
  [[ "$(hy4_image_hash "$KIT/image")" == "$HY4_IMAGE_HASH" ]] || die "image: $KIT/image does not hash to $HY4_IMAGE_HASH (config.sh): the vendored files changed"
  if [[ -z "$(image_id "$HY4_BASE_IMAGE")" ]]; then
    log "image: docker pull $HY4_BASE_IMAGE (arm64, ~11 GB)"
    dk pull "$HY4_BASE_IMAGE" >/dev/null
  fi
  log "image: docker build -t $HY4_IMAGE $KIT/image"
  dk build -t "$HY4_IMAGE" --label "openbeast.hy4.recipe=$HY4_RECIPE_REPO@$HY4_RECIPE_COMMIT" \
    --label "openbeast.hy4.base=$HY4_BASE_IMAGE" "$KIT/image"
}
step_save() {
  local id; id=$(image_id "$HY4_IMAGE"); [[ -n "$id" ]] || die "save: no image $HY4_IMAGE (image step first)"
  hy4_refuse_protected "$HY4_IMAGE_DIR" "write the image tar in"
  if [[ -f "$TAR" && -f "$TAR.sha256" && "$(sed -n 's/^image_id=//p' "$HY4_IMAGE_DIR/MANIFEST" 2>/dev/null)" == "$id" ]]; then
    log "save: $TAR already holds $id: skipped"; return
  fi
  docker_guard save
  mkdir -p "$HY4_IMAGE_DIR"
  log "save: docker save $HY4_IMAGE -> $TAR"
  dk save -o "$TAR.part" "$HY4_IMAGE"
  mv -f "$TAR.part" "$TAR"
  printf '%s  %s\n' "$(capped bash -c "$(declare -f hy4_sha256); hy4_sha256 < $(q "$TAR")")" "$(basename "$TAR")" > "$TAR.sha256"
  printf 'image=%s\nimage_id=%s\nbase=%s\nbase_index=%s\nrecipe=%s@%s\nimage_hash=%s\nsaved=%s\n' "$HY4_IMAGE" "$id" \
    "$HY4_BASE_IMAGE" "$HY4_BASE_INDEX" "$HY4_RECIPE_REPO" "$HY4_RECIPE_COMMIT" "$HY4_IMAGE_HASH" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$HY4_IMAGE_DIR/MANIFEST"
  log "save: OK ($(cut -c1-16 "$TAR.sha256")…)"
}
step_ship() {
  local id; id=$(image_id "$HY4_IMAGE"); [[ -n "$id" ]] || die "ship: no image $HY4_IMAGE here"
  if [[ "$(image_id_there "$HY4_IMAGE")" == "$id" ]]; then log "ship: $HY4_WORKER has $id: skipped"; return; fi
  [[ -f "$TAR" ]] || die "ship: no $TAR (save step first)"
  docker_guard ship
  log "ship: docker load on $HY4_WORKER < $TAR (over the link)"
  capped bash -c "$(q "$HY4_SSH") -o BatchMode=yes $(q "$HY4_WORKER") docker load < $(q "$TAR")" >/dev/null
  [[ "$(image_id_there "$HY4_IMAGE")" == "$id" ]] || die "ship: $HY4_WORKER's image id differs from the head's $id"
  log "ship: OK, $HY4_WORKER has $id"
}

# ---- --check
cmd_check() {
  local ok=0 n r id idw
  line() { printf '  %-22s %s\n' "$1" "$2"; }
  echo "Hy4-flash (NVFP4) staging: $HY4_REPO @ ${HY4_REVISION:0:8}, image $HY4_IMAGE"
  if verified_here; then line "weights here" "verified ($(sed -n 's/^verified=//p' "$HY4_MODEL_DIR/.hy4-verified")) $HY4_MODEL_DIR"
  elif [[ -d "$HY4_MODEL_DIR" ]]; then r=$(hy4_files_check "$HY4_MODEL_DIR" < "$FILES_HUB" | tail -1 || true); line "weights here" "NOT verified: $r"; ok=1
  else line "weights here" "missing ($HY4_MODEL_DIR)"; ok=1; fi
  if verified_there; then line "weights $HY4_WORKER" "verified"; else line "weights $HY4_WORKER" "missing or NOT verified"; ok=1; fi
  id=$(image_id "$HY4_IMAGE" || true)
  if [[ -n "$id" ]]; then line "image here" "$id"; else line "image here" "missing"; ok=1; fi
  idw=$(image_id_there "$HY4_IMAGE" || true)
  if [[ -n "$idw" && "$idw" == "$id" ]]; then line "image $HY4_WORKER" "$idw"
  else line "image $HY4_WORKER" "${idw:-missing}$([[ -n "$idw" ]] && echo ' (DIFFERS from the head)')"; ok=1; fi
  if [[ -f "$TAR" && -f "$TAR.sha256" ]]; then line "saved tar" "$TAR ($(cut -c1-16 "$TAR.sha256")…; id $(sed -n 's/^image_id=//p' "$HY4_IMAGE_DIR/MANIFEST" 2>/dev/null))"
  else line "saved tar" "missing ($TAR)"; ok=1; fi
  if [[ -f "$HY4_UNIT_DIR/hy4-vllm-inference.service" ]]; then
    line "unit" "installed, $("$SYSTEMCTL" --user is-enabled hy4-vllm-inference.service 2>/dev/null || true) / $("$SYSTEMCTL" --user is-active hy4-vllm-inference.service 2>/dev/null || true)"
  else line "unit" "not installed (spark-hy4-stage.sh install)"; ok=1; fi
  n=0
  for f in start.sh stop.sh config.sh memguard.sh; do cmp -s "$KIT/$f" "$HY4_RUNTIME_DIR/$f" || n=$((n + 1)); done
  if (( n == 0 )); then line "runtime" "$HY4_RUNTIME_DIR = this checkout"; else line "runtime" "$n file(s) differ from this checkout or are missing (install)"; ok=1; fi
  line "autotune cache" "$( [[ -d "$HY4_CACHE_DIR/$HY4_IMAGE_HASH" ]] && echo "$HY4_CACHE_DIR/$HY4_IMAGE_HASH" || echo "none yet (the first start fills it, ~10 min)")"
  line "kept (never touched)" "$HOME/Documents/$HY4_PROTECTED_NAME: here $([[ -d "$HOME/Documents/$HY4_PROTECTED_NAME" ]] && echo present || echo absent), $HY4_WORKER $(on_worker "test -d ~/Documents/$HY4_PROTECTED_NAME && echo present || echo absent" 2>/dev/null || echo unknown)"
  (( ok == 0 )) && echo "staged: yes (beast-spark hy4 starts it)" || echo "staged: NO (scripts/backends/spark-hy4-stage.sh --run)"
  return "$ok"
}

# ---- install: the runtime copy and the unit; enables nothing
cmd_install() {
  local f dry=$DRY_GIVEN
  for f in start.sh stop.sh config.sh memguard.sh SHA256SUMS.hub-lfs FILES.hub; do
    if (( dry )); then echo "install $KIT/$f -> $HY4_RUNTIME_DIR/$f"; continue; fi
    mkdir -p "$HY4_RUNTIME_DIR"
    if cmp -s "$KIT/$f" "$HY4_RUNTIME_DIR/$f"; then log "unchanged: $HY4_RUNTIME_DIR/$f"; continue; fi
    cp "$KIT/$f" "$HY4_RUNTIME_DIR/$f.new" && mv -f "$HY4_RUNTIME_DIR/$f.new" "$HY4_RUNTIME_DIR/$f"
    [[ "$f" == *.sh ]] && chmod 0755 "$HY4_RUNTIME_DIR/$f"
    log "installed: $HY4_RUNTIME_DIR/$f"
  done
  if (( dry )); then echo "install $KIT/hy4-vllm-inference.service -> $HY4_UNIT_DIR/ ; systemctl --user daemon-reload (NOT enabled)"; return; fi
  mkdir -p "$HY4_UNIT_DIR"
  if ! cmp -s "$KIT/hy4-vllm-inference.service" "$HY4_UNIT_DIR/hy4-vllm-inference.service"; then
    cp "$KIT/hy4-vllm-inference.service" "$HY4_UNIT_DIR/hy4-vllm-inference.service"
    log "installed: $HY4_UNIT_DIR/hy4-vllm-inference.service"
  fi
  "$SYSTEMCTL" --user daemon-reload
  log "the unit is installed and NOT enabled (it has no [Install] section: it never starts at boot). Start it with: beast-spark hy4"
}

plan() {
  echo "Plan (dry run; --run does it). Steps: $STEPS"
  want weights && echo "  weights  $( [[ -n "$WEIGHTS_FROM" ]] && echo "rsync -a $WEIGHTS_FROM/ $HY4_MODEL_DIR/" || echo "hf download $HY4_REPO --revision $HY4_REVISION --local-dir $HY4_MODEL_DIR") (in ${SCOPE[*]})"
  want verify  && echo "  verify   sizes of $(grep -vc '^#' "$FILES_HUB") files (FILES.hub), sha256 -c SHA256SUMS.hub-lfs ($(wc -l < "$SUMS_HUB" | tr -d ' ') LFS files), then .hy4-verified"
  want mirror  && echo "  mirror   rsync -a $HY4_MODEL_DIR/ $HY4_WORKER:$HY4_MODEL_DIR/ (capped both ends), verify there"
  want image   && echo "  image    $( [[ -n "$IMAGE_FROM" ]] && echo "docker load -i $IMAGE_FROM" || echo "docker pull $HY4_BASE_IMAGE; docker build -t $HY4_IMAGE $KIT/image")"
  want save    && echo "  save     docker save $HY4_IMAGE -> $TAR (+ .sha256, MANIFEST)"
  want ship    && echo "  ship     ssh $HY4_WORKER docker load < $TAR"
  local s; s=$(serving_units)
  [[ -n "$s" ]] && echo "  note     $s is active: image/save/ship refuse without --while-serving; weights/verify/mirror run capped"
  echo
  cmd_check || true
}

if [[ "$SUB" == install ]]; then cmd_install; exit 0; fi
case "$MODE" in
  plan) plan ;;
  check) cmd_check ;;
  run)
    for s in $STEPS_ALL; do want "$s" || continue; "step_$s"; done
    log "done: $STEPS"
    cmd_check || true ;;
esac
