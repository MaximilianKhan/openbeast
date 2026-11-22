#!/bin/bash
# Hy4-flash (NVFP4) on vLLM TP2: spark-hy4-stage.sh, hy4-vllm/{config,start,
# stop,memguard}.sh and the unit file. Hermetic: docker, ssh, systemctl,
# systemd-run, hf, rsync, ss, ip and ibdev2netdev are recording stubs over a
# scratch HOME (the "worker" is a second scratch HOME the ssh stub maps paths
# to); nothing reaches a Spark, the network, docker or a GPU. Portable (macOS
# bash 3.2 and Linux).
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$REPO_DIR/scripts/backends/spark-hy4-stage.sh"
KIT="$REPO_DIR/scripts/backends/hy4-vllm"
PASS=0 FAIL=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
echo "=== spark-hy4 (Hy4-flash vLLM TP2 staging + serving) tests ==="

T="$(cd "$(mktemp -d)" && pwd -P)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home/.config/openbeast" "$T/whome" "$T/dh" "$T/dw" "$T/sys" "$T/fx"
export HOME="$T/home" T LOG="$T/log"
touch "$LOG"
echo "SECRETKEY0123456789abcdef" > "$HOME/.config/openbeast/tensorfold-api-key"

# --- stubs ---------------------------------------------------------------------------
# docker: state under $DOCKER_STATE (head: $T/dh; the ssh stub sets $T/dw for the worker)
cat > "$T/bin/docker" <<'S'
#!/bin/bash
D=${DOCKER_STATE:-$T/dh}; W=$(basename "$D")
key() { printf '%s' "$1" | tr '/:@' '___'; }
op=$1; shift
case "$op" in
  image) shift; f=""; [[ "$1" == -f ]] && { f=$2; shift 2; }
         [[ -f "$D/img-$(key "$1")" ]] || exit 1; [[ -n "$f" ]] && cat "$D/img-$(key "$1")"; exit 0 ;;
  pull) echo "$W pull $*" >> "$LOG"; echo "sha256:base" > "$D/img-$(key "$1")" ;;
  build) echo "$W build $*" >> "$LOG"; t=""; while (( $# )); do [[ "$1" == -t ]] && t=$2; shift; done
         echo "sha256:built" > "$D/img-$(key "$t")" ;;
  save) echo "$W save $*" >> "$LOG"; echo "$3 $(cat "$D/img-$(key "$3")")" > "$2" ;;
  load) echo "$W load $*" >> "$LOG"; if [[ "${1:-}" == -i ]]; then c=$(cat "$2"); else c=$(cat); fi
        set -- $c; echo "$2" > "$D/img-$(key "$1")"; echo "Loaded image: $1" ;;
  run) n=""; a=("$@"); for ((i = 0; i < ${#a[@]}; i++)); do [[ "${a[i]}" == --name ]] && n=${a[i+1]}; done
       echo "$W run $* KEYENV=${VLLM_API_KEY:+set}" >> "$LOG"; echo "$W run" >> "$T/order"
       [[ -f "$D/fail-run" ]] && exit 1; echo running > "$D/ctr-$n" ;;
  rm) echo "$W rm $*" >> "$LOG"; rm -f "$D/ctr-${!#}" ;;
  stop) echo "$W stop $*" >> "$LOG"; [[ -f "$D/ctr-${!#}" ]] && echo stopped > "$D/ctr-${!#}" ;;
  kill) echo "$W kill $*" >> "$LOG"; [[ -f "$D/ctr-${!#}" ]] && echo stopped > "$D/ctr-${!#}" ;;
  container) [[ -f "$D/ctr-${!#}" ]] ;;
  inspect) c="$D/ctr-${!#}"; [[ -f "$c" ]] || exit 1
           if [[ -f "$D/dies" ]]; then n=$(cat "$D/dies"); if (( n <= 0 )); then echo stopped > "$c"; else echo $((n - 1)) > "$D/dies"; fi; fi
           if [[ "$(cat "$c")" == running ]]; then r=true; e=0; else r=false; e=1; fi
           [[ "$2" == *ExitCode* ]] && echo "$r $e" || echo "$r" ;;
  logs) exit 0 ;;
esac
S
# ssh [-o X]... HOST CMD...: CMD runs locally as the worker (paths under $T/home -> $T/whome)
cat > "$T/bin/ssh" <<'S'
#!/bin/bash
while [[ "$1" == -o ]]; do shift 2; done
host=$1; shift
echo "ssh $host" >> "$T/ssh.log"
[[ -f "$T/ssh-down" ]] && exit 255
cmd="$*"; cmd=${cmd//$T\/home/$T\/whome}
HOME="$T/whome" DOCKER_STATE="$T/dw" bash -c "$cmd"
S
cat > "$T/bin/systemctl" <<'S'
#!/bin/bash
[[ "$1" == --user ]] && shift
op=$1; shift
[[ "${1:-}" == --quiet ]] && shift
case "$op" in
  is-active) [[ -f "$T/sys/$1.active" ]] ;;
  is-enabled) echo static ;;
  daemon-reload) echo "systemctl daemon-reload" >> "$LOG" ;;
  *) echo "systemctl $op $*" >> "$LOG" ;;
esac
S
cat > "$T/bin/systemd-run" <<'S'
#!/bin/bash
echo "systemd-run $*" >> "$T/scope.log"
while [[ "$1" != -- ]]; do shift; done; shift
exec "$@"
S
# hf download REPO --revision REV --local-dir DIR: the fixture revision's files
cat > "$T/bin/hf" <<'S'
#!/bin/bash
echo "hf $*" >> "$LOG"
d=""; while (( $# )); do [[ "$1" == --local-dir ]] && d=$2; shift; done
mkdir -p "$d" && cp -R "$T/fx/." "$d/"
S
# rsync ... SRC/ DST/: copies; a HOST:path destination lands under the worker's HOME
cat > "$T/bin/rsync" <<'S'
#!/bin/bash
echo "rsync $*" >> "$LOG"
src=${@: -2:1} dst=${!#}
case "$dst" in *:*) dst=${dst#*:}; dst=${dst//$T\/home/$T\/whome} ;; esac
mkdir -p "$dst" && cp -R "$src." "$dst"
S
cat > "$T/bin/ss" <<'S'
#!/bin/bash
[[ -f "$T/port-busy" ]] && echo "LISTEN 0 128 127.0.0.1:8000 0.0.0.0:*"
exit 0
S
cat > "$T/bin/ip" <<'S'
#!/bin/bash
echo "10.100.216.2 dev enp1s0f0np0 src 10.100.216.1 uid 1000 \\    cache"
S
cat > "$T/bin/ibdev2netdev" <<'S'
#!/bin/bash
printf '%s\n' "rocep1s0f0 port 1 ==> enp1s0f0np0 (Up)" "rocep1s0f1 port 1 ==> enp1s0f1np1 (Down)" \
  "roceP2p1s0f0 port 1 ==> enP2p1s0f0np0 (Up)" "roceP2p1s0f1 port 1 ==> enP2p1s0f1np1 (Down)"
S
chmod +x "$T"/bin/*
export PATH="$T/bin:$PATH"

# the fixture revision: two small files, one of them "LFS"
printf '{"model_type":"hy_v4_flash"}' > "$T/fx/config.json"
head -c 4096 /dev/urandom > "$T/fx/model-00001-of-00001.safetensors"
fsz() { wc -c < "$1" | tr -d ' '; }
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -c1-64; }
printf '# fixture\n%s abc config.json\n%s lfs model-00001-of-00001.safetensors\n' "$(fsz "$T/fx/config.json")" \
  "$(fsz "$T/fx/model-00001-of-00001.safetensors")" > "$T/FILES.hub"
printf '%s  model-00001-of-00001.safetensors\n' "$(sha "$T/fx/model-00001-of-00001.safetensors")" > "$T/SUMS.hub"
export HY4_FILES_HUB="$T/FILES.hub" HY4_SUMS_HUB="$T/SUMS.hub" HY4_MIN_AVAIL_WAIT_S=0 HY4_MEMGUARD_GIB=0 HY4_STALL_POLL=0.05
MD="$HOME/Documents/atakans-Hy4-flash-NVFP4"
stage() { bash "$STAGE" "$@" > "$T/out" 2>&1; }
out() { cat "$T/out"; }

# --- 1. the pins -----------------------------------------------------------------------
# shellcheck source=../scripts/backends/hy4-vllm/config.sh
cfg() { bash -c "source '$KIT/config.sh'; $1"; }
if [[ "$(cfg 'hy4_image_hash "$HY4_HERE/image"')" == "$(cfg 'echo $HY4_IMAGE_HASH')" ]]; then
  pass "image/ hashes to HY4_IMAGE_HASH (config.sh): the image tag follows the vendored files"
else fail "image hash: $(cfg 'hy4_image_hash "$HY4_HERE/image"') vs $(cfg 'echo $HY4_IMAGE_HASH') (update HY4_IMAGE_HASH and image/CREDITS.md)"; fi
base=$(cfg 'echo $HY4_BASE_IMAGE')
if grep -qx "FROM $base" "$KIT/image/Dockerfile" && [[ "$base" == *@sha256:5e2c1badbca60595d6591c60feb4b8547f59da96e7a91710d40239250e721aba ]]; then
  pass "the Dockerfile FROMs the pinned arm64 digest, the one the stage step pulls"
else fail "base digest: $base vs $(grep ^FROM "$KIT/image/Dockerfile")"; fi
bad=0
for f in Dockerfile attention.py dfly_config.py hy_v4_attention.py mla_attention.py model.py sm120.py LICENSE.dgx-spark-recipes; do
  grep -q "\`$f\` | \`$(sha "$KIT/image/$f")\`" "$KIT/image/CREDITS.md" || { bad=1; echo "    $f differs from CREDITS.md"; }
done
(( bad == 0 )) && pass "every vendored file matches its sha256 in image/CREDITS.md (recipe e764efa, MIT + Apache-2.0)" || fail "vendored files"
if [[ "$(grep -c . "$KIT/SHA256SUMS.hub-lfs")" == 20 && "$(grep -c 'model-000[0-9][0-9]-of-00014.safetensors$' "$KIT/SHA256SUMS.hub-lfs")" == 14 ]] \
   && ! grep -qvE '^[0-9a-f]{64}  [^ ].*$' "$KIT/SHA256SUMS.hub-lfs" \
   && [[ "$(grep -vc '^#' "$KIT/FILES.hub")" == 76 && "$(awk '!/^#/ { s += $1 } END { print s }' "$KIT/FILES.hub")" == 164691130443 ]] \
   && [[ "$(awk '$2 == "lfs"' "$KIT/FILES.hub" | wc -l | tr -d ' ')" == 20 ]]; then
  pass "the Hub lists: 20 LFS sha256 (all 14 shards), 76 files, 164,691,130,443 bytes at 30f201ac"
else fail "hub lists"; fi
if [[ "$(cfg 'echo $HY4_REVISION')" == 30f201acf0b4f9ba178817bcbc04ccfa3ec473f9 && "$(cfg 'echo $HY4_SERVED')" == Hy4-flash-NVFP4 ]]; then
  pass "revision 30f201ac (the recipe's), served id Hy4-flash-NVFP4"
else fail "pins"; fi
U="$KIT/hy4-vllm-inference.service"
if ! grep -q '^\[Install\]' "$U" && grep -qx 'Restart=no' "$U" && grep -q '^ExecStart=/usr/bin/bash %h/openbeast-recipes/hy4-vllm/start.sh$' "$U" \
   && grep -q '^ExecStop=.*/stop.sh$' "$U" && grep -q '^Conflicts=deepseek-v41-inference.service spark-hydra-inference.service$' "$U"; then
  pass "unit: no [Install] (cannot be enabled, never at boot), Restart=no, conflicts with both models' units"
else fail "unit file"; fi

# settings: hy4-vllm.env is parsed (never sourced); the pins cannot be overridden from it
printf 'HY4_PORT=8011  # x\nHY4_REVISION=deadbeef\nHY4_SPEC="off"\n$(touch %s/pwned)\n' "$T" > "$HOME/.config/openbeast/hy4-vllm.env"
if [[ "$(cfg 'echo $HY4_PORT $HY4_REVISION $HY4_SPEC')" == "8011 30f201acf0b4f9ba178817bcbc04ccfa3ec473f9 off" && ! -e "$T/pwned" ]] \
   && [[ "$(HY4_PORT=8022 cfg 'echo $HY4_PORT')" == 8022 ]]; then
  pass "hy4-vllm.env: parsed not sourced, the environment wins, pins (revision) are not settings"
else fail "conf: $(cfg 'echo $HY4_PORT $HY4_REVISION $HY4_SPEC')"; fi
rm -f "$HOME/.config/openbeast/hy4-vllm.env"

# the kept EXL3 pack: refused as any path this code writes
if cfg 'hy4_protected ~/Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw/x' && ! cfg 'hy4_protected ~/Documents/atakans-Hy4-flash-NVFP4' \
   && ! HY4_MODEL_DIR="$HOME/Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw" bash "$STAGE" --run --only weights > "$T/out" 2>&1 \
   && grep -q "is kept" "$T/out" && [[ ! -e "$HOME/Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw" ]] \
   && ! HY4_MODEL_DIR="$HOME/Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw" bash "$KIT/start.sh" --print > "$T/out" 2>&1 && grep -q "is kept" "$T/out"; then
  pass "Documents/LibertAIDAI-Hy4-flash-EXL3-4bpw (kept): every stage step and start refuse it as a target"
else fail "protected: $(out)"; fi
if ! cat "$STAGE" "$KIT/start.sh" "$KIT/stop.sh" "$KIT/config.sh" | grep -vE '^[[:space:]]*#' | grep -v 'never --delete' \
     | grep -E 'rm -r|rm -fr|--delete|docker image rm|docker rmi|prune'; then
  pass "no delete anywhere: no rm -r, no rsync --delete, no image removal or prune (stop.sh removes only containers)"
else fail "a delete in the hy4 scripts"; fi

# --- 2. staging --------------------------------------------------------------------------
: > "$LOG"
if stage && grep -q '^Plan (dry run' "$T/out" && grep -q 'staged: NO' "$T/out" && [[ ! -e "$HOME/Documents" && ! -s "$LOG" ]]; then
  pass "default is --dry-run: the plan and the --check report, nothing written, nothing run"
else fail "dry run: $(out) / $(cat "$LOG")"; fi

# weights + verify: hf in the capped scope, sizes + sha256, then the marker
: > "$T/scope.log"
if stage --run --only weights,verify && grep -q "^hf download atakans/Hy4-flash-NVFP4 --revision 30f201acf0b4f9ba178817bcbc04ccfa3ec473f9 --local-dir $MD\$" "$LOG" \
   && grep -q -- '--scope --quiet -p MemoryMax=8G -p MemoryHigh=6G -- .*/hf download' "$T/scope.log" \
   && grep -q '^revision=30f201acf0b4f9ba178817bcbc04ccfa3ec473f9$' "$MD/.hy4-verified" && cmp -s "$T/SUMS.hub" "$MD/SHA256SUMS.hub-lfs"; then
  pass "weights + verify: hf download at the pin inside MemoryMax=8G/MemoryHigh=6G, Hub sizes and LFS sha256 checked, .hy4-verified"
else fail "weights/verify: $(out) / $(cat "$T/scope.log")"; fi
# a second run skips the download
: > "$LOG"
if stage --run --only weights && grep -q 'already verified' "$T/out" && ! grep -q '^hf ' "$LOG"; then pass "weights: skipped once verified (resumable)"
else fail "weights rerun: $(out)"; fi
# a corrupted shard: verify refuses and writes no marker
rm -f "$MD/.hy4-verified"; printf 'X' | dd of="$MD/model-00001-of-00001.safetensors" bs=1 seek=10 conv=notrunc 2>/dev/null
if ! stage --run --only verify && grep -q 'sha256 mismatch' "$T/out" && [[ ! -e "$MD/.hy4-verified" ]]; then
  pass "verify: a flipped byte is a sha256 mismatch against the Hub; no .hy4-verified"
else fail "verify corrupt: $(out)"; fi
cp "$T/fx/model-00001-of-00001.safetensors" "$MD/"; rm -f "$MD/config.json"
if ! stage --run --only verify && grep -q 'does not match the revision' "$T/out"; then pass "verify: a missing file is refused before hashing"
else fail "verify missing: $(out)"; fi

# the stall watchdog: a download that stops growing is killed (with its children) and
# started again (hf resumes); one that never grows is given up on after the restarts
cat > "$T/bin/hf-slow" <<'S'
#!/bin/bash
n=$(( $(cat "$T/hf-slow.n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$T/hf-slow.n"
echo "hf-slow $n" >> "$LOG"
d=""; while (( $# )); do [[ "$1" == --local-dir ]] && d=$2; shift; done
mkdir -p "$d"
if [[ -f "$T/hf-stall-always" ]] || (( n == 1 )); then
  printf x > "$d/.partial-$n"; sleep 30 & echo $! > "$T/hf-slow.child"; wait; exit 0
fi
cp -R "$T/fx/." "$d/"
S
chmod +x "$T/bin/hf-slow"
WD="$T/stallmd"; : > "$LOG"
wd() { HY4_HF="$T/bin/hf-slow" HY4_MODEL_DIR="$WD" HY4_STALL_SECS=1 HY4_STALL_POLL=0.2 HY4_STALL_MB=1 bash "$STAGE" "$@" > "$T/out" 2>&1; }
if wd --run --only weights,verify && [[ "$(grep -c '^hf-slow' "$LOG")" == 2 ]] \
   && grep -q 'weights: stalled: 0 MB in 1 s (under 1 MB): killed; restart 1 of 5' "$T/out" \
   && grep -q '^revision=' "$WD/.hy4-verified" && ! kill -0 "$(cat "$T/hf-slow.child")" 2>/dev/null; then
  pass "stall watchdog: under HY4_STALL_MB in HY4_STALL_SECS kills the download and its children, restarts it (logged), then verifies"
else fail "stall watchdog: $(out) / $(cat "$LOG")"; fi
WD="$T/stallmd2"; : > "$LOG"; rm -f "$T/hf-slow.n"; touch "$T/hf-stall-always"
t0=$SECONDS
if ! HY4_STALL_RESTARTS=2 wd --run --only weights && [[ "$(grep -c '^hf-slow' "$LOG")" == 3 ]] \
   && grep -q 'restart 2 of 2' "$T/out" && grep -q 'stalled 3 times .*gave up. Re-run later (it resumes)' "$T/out" \
   && [[ ! -e "$WD/.hy4-verified" ]] && (( SECONDS - t0 < 20 )) && ! kill -0 "$(cat "$T/hf-slow.child")" 2>/dev/null; then
  pass "stall watchdog: gives up after HY4_STALL_RESTARTS restarts with the resume hint, no download left running"
else fail "stall watchdog give-up: $(out) / $(cat "$LOG")"; fi
rm -f "$T/hf-stall-always"
if ! HY4_STALL_MB=lots bash "$STAGE" --check > "$T/out" 2>&1 && grep -q 'HY4_STALL_MB=lots: a whole number' "$T/out"; then
  pass "stall watchdog: a non-numeric HY4_STALL_* setting is refused"
else fail "stall setting: $(out)"; fi
cp "$T/fx/config.json" "$MD/"; stage --run --only verify

# offline: --weights-from a drive's copy
mkdir -p "$T/drive"; cp -R "$T/fx/." "$T/drive/"
HOME2="$T/home2"; mkdir -p "$HOME2"
if HOME="$HOME2" bash "$STAGE" --run --only weights,verify --weights-from "$T/drive" > "$T/out" 2>&1 \
   && grep -q "rsync -a --partial --exclude .hy4-verified $T/drive/ $HOME2/Documents/atakans-Hy4-flash-NVFP4/" "$LOG" \
   && [[ -f "$HOME2/Documents/atakans-Hy4-flash-NVFP4/.hy4-verified" ]]; then
  pass "offline weights: --weights-from DIR copies the drive's copy (capped) and verifies it against the vendored Hub sums"
else fail "weights-from: $(out)"; fi

# mirror: rsync to the worker, capped on both ends, never --delete; verified there
: > "$LOG"
if stage --run --only mirror && grep -q "^rsync -a --partial --exclude .hy4-verified --exclude .cache/ -e ssh -o BatchMode=yes --rsync-path=nice -n 10 systemd-run --user --scope --quiet -p MemoryMax=8G -p MemoryHigh=6G -- rsync $MD/ spark-2:$MD/\$" "$LOG" \
   && ! grep -q -- '--delete' "$LOG" && grep -q '^revision=30f201ac' "$T/whome/Documents/atakans-Hy4-flash-NVFP4/.hy4-verified" \
   && grep -q '^host=spark-2$' "$T/whome/Documents/atakans-Hy4-flash-NVFP4/.hy4-verified"; then
  pass "mirror: rsync over the link, the far rsync in a capped scope too, no --delete; sizes + sha256 on spark-2, its own marker"
else fail "mirror: $(out) / $(cat "$LOG")"; fi

# image: refused while a model serves (the daemon is outside any scope), unless --while-serving
touch "$T/sys/deepseek-v41-inference.service.active"; : > "$LOG"
if ! stage --run --only image && grep -q 'deepseek-v41-inference.service is active' "$T/out" && ! grep -q 'pull\|build' "$LOG"; then
  pass "image: refused while DeepSeek is active (no pull, no build)"
else fail "image guard: $(out)"; fi
IMG=$(cfg 'echo $HY4_IMAGE')
if stage --run --only image --while-serving && grep -q "^dh pull vllm/vllm-openai@sha256:5e2c1bad" "$LOG" \
   && grep -q "^dh build -t $IMG --label openbeast.hy4.recipe=atakann/dgx-spark-recipes@e764efa3efed3e21fe543c3fffc927eb6a00bff8 .* $KIT/image\$" "$LOG"; then
  pass "image: pull BY DIGEST, then build the vendored Dockerfile as $IMG (labelled with the recipe commit)"
else fail "image: $(out) / $(cat "$LOG")"; fi
rm -f "$T/sys/deepseek-v41-inference.service.active"

# save + ship: the tar, its sha256 and MANIFEST; docker load on the worker; ids equal
TARF="$HOME/Documents/hy4-vllm-image/${IMG//[:\/]/_}.tar"
if stage --run --only save,ship && [[ -f "$TARF" && -f "$TARF.sha256" ]] && grep -q '^image_id=sha256:built$' "$HOME/Documents/hy4-vllm-image/MANIFEST" \
   && grep -q '^base=vllm/vllm-openai@sha256:5e2c1bad' "$HOME/Documents/hy4-vllm-image/MANIFEST" && grep -q '^dw load' "$LOG" \
   && [[ "$(DOCKER_STATE="$T/dw" docker image inspect -f x "$IMG")" == sha256:built ]]; then
  pass "save + ship: $TARF (+ sha256, MANIFEST with the base digest), loaded on spark-2 with the same image id"
else fail "save/ship: $(out)"; fi

# --check: everything staged except the unit and the runtime -> exit 1; after install -> 0
if ! stage --check && grep -q 'unit  *not installed' "$T/out" && grep -q 'weights spark-2  *verified' "$T/out" && grep -q 'staged: NO' "$T/out"; then
  pass "--check: reports each piece; not staged until the unit is installed"
else fail "check before install: $(out)"; fi
: > "$LOG"
if stage install && [[ -x "$HOME/openbeast-recipes/hy4-vllm/start.sh" && -f "$HOME/openbeast-recipes/hy4-vllm/config.sh" ]] \
   && cmp -s "$U" "$HOME/.config/systemd/user/hy4-vllm-inference.service" && grep -q 'daemon-reload' "$LOG" \
   && ! grep -q 'enable' "$LOG" && grep -q 'NOT enabled' "$T/out"; then
  pass "install: the runtime and the unit in place, daemon-reload, nothing enabled"
else fail "install: $(out) / $(cat "$LOG")"; fi
if stage --check && grep -q 'staged: yes' "$T/out" && grep -q 'runtime  *.* = this checkout' "$T/out"; then pass "--check: exit 0 once everything is staged"
else fail "check after: $(out)"; fi
if HOME="$T/home3" bash "$STAGE" install --dry-run > "$T/out" 2>&1 && [[ ! -e "$T/home3" ]] && grep -q 'NOT enabled' "$T/out"; then
  pass "install --dry-run: prints, writes nothing"
else fail "install dry: $(out)"; fi

# offline image restore: --image-from TAR, checked against its .sha256
rm -f "$T"/dh/img-*; cp "$TARF" "$T/restore.tar"; cp "$TARF.sha256" "$T/restore.tar.sha256"
if stage --run --only image --image-from "$T/restore.tar" && [[ "$(docker image inspect -f x "$IMG")" == sha256:built ]]; then
  pass "offline: --image-from TAR (sha256 checked) restores the image without a registry"
else fail "image-from: $(out)"; fi
rm -f "$T"/dh/img-*; echo "0000000000000000000000000000000000000000000000000000000000000000  x" > "$T/restore.tar.sha256"
if ! stage --run --only image --image-from "$T/restore.tar" && grep -q 'does not match' "$T/out"; then pass "offline: a tar that does not match its .sha256 is refused"
else fail "image-from bad sum: $(out)"; fi
stage --run --only image --image-from "$TARF"

# --- 3. start.sh --print: the exact commands -------------------------------------------
export HY4_HEAD_LINK="enp1s0f0np0 10.100.216.1 rocep1s0f0,roceP2p1s0f0" HY4_WORKER_LINK="enp1s0f1np1 10.100.216.2 rocep1s0f1,roceP2p1s0f1"
printf 'MemTotal: 127598412 kB\nMemAvailable: 120000000 kB\n' > "$T/meminfo"; export HY4_MEMINFO="$T/meminfo" HY4_WORKER_MEMINFO="$T/meminfo"
RT="$HOME/openbeast-recipes/hy4-vllm"
P=$(bash "$RT/start.sh" --print 2>&1); rc=$?
W=$(grep -A1 '^# worker' <<<"$P" | tail -1); H=$(grep -A1 '^# head' <<<"$P" | tail -1)
W=${W//\\/}; H=${H//\\/}   # printf %q's backslashes (bash 3.2 and 5 quote differently)
W=${W% }; H=${H% }
flags='/model --served-model-name Hy4-flash-NVFP4 --tensor-parallel-size 2 --enable-expert-parallel --dtype bfloat16 --max-model-len 262144 --max-num-seqs 4 --max-num-batched-tokens 8192 --kv-cache-memory-bytes 6G --gpu-memory-utilization 0.85 --speculative-config {"method":"dfly","num_speculative_tokens":5} --limit-mm-per-prompt {"image":1,"video":0} --reasoning-parser hy_v4 --tool-call-parser hy_v4 --enable-auto-tool-choice --nnodes 2 --master-addr 10.100.216.1 --master-port 29501'
if [[ $rc == 0 ]] && [[ "$W" == *"$flags --node-rank 1 --headless" ]] && [[ "$H" == *"$flags --node-rank 0 --host 127.0.0.1 --port 8000" ]]; then
  pass "--print: the recipe's flags on both ranks, worker headless rank 1, head rank 0 on 127.0.0.1:8000"
else fail "print flags (rc $rc): $P"; fi
if [[ "$W" == *"-e NCCL_SOCKET_IFNAME=enp1s0f1np1 -e GLOO_SOCKET_IFNAME=enp1s0f1np1 -e TP_SOCKET_IFNAME=enp1s0f1np1 -e NCCL_IB_HCA=rocep1s0f1,roceP2p1s0f1 -e VLLM_HOST_IP=10.100.216.2"* ]] \
   && [[ "$H" == *"-e NCCL_SOCKET_IFNAME=enp1s0f0np0 -e GLOO_SOCKET_IFNAME=enp1s0f0np0 -e TP_SOCKET_IFNAME=enp1s0f0np0 -e NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0 -e VLLM_HOST_IP=10.100.216.1"* ]] \
   && [[ "$H" == *"-e NCCL_IB_GID_INDEX=3"* && "$H" == *"-e HF_HUB_OFFLINE=1"* && "$H" == *"--memory 112g"* && "$H" == *"-e VLLM_API_KEY $IMG"* ]] \
   && [[ "$H" == *"-v $HOME/.cache/hy4-vllm/$(cfg 'echo $HY4_IMAGE_HASH'):/cache"* && "$H" == *"-v $MD:/model:ro"* ]] && ! grep -q SECRETKEY <<<"$P"; then
  pass "--print: NCCL/Gloo on each Spark's own QSFP netdev and both rails, GID 3, VLLM_HOST_IP on the link, offline, cgroup 112g, autotune cache per image, key by name only"
else fail "print env: $P"; fi
if [[ "$(HY4_SPEC=off bash "$RT/start.sh" --print 2>&1)" != *speculative* ]]; then pass "HY4_SPEC=off: served without the DFly drafter"
else fail "spec off"; fi

# discovery: hy4_link from ip route + ibdev2netdev (the link's netdev, every Up RoCE device)
if [[ "$(cfg 'hy4_link 10.100.216.2')" == "enp1s0f0np0 10.100.216.1 rocep1s0f0,roceP2p1s0f0" ]]; then
  pass "hy4_link: netdev and address of the route to the peer, the Up RoCE devices as NCCL_IB_HCA"
else fail "hy4_link: $(cfg 'hy4_link 10.100.216.2')"; fi

# --- 4. start.sh refusals (before anything starts) ---------------------------------------
: > "$LOG"
startrc() { FOREGROUND=0 bash "$RT/start.sh" > "$T/out" 2>&1; }
printf 'MemTotal: 127598412 kB\nMemAvailable: 90000000 kB\n' > "$T/meminfo-low"
if ! HY4_MEMINFO="$T/meminfo-low" startrc && grep -q 'memory: only 85.8 .* GiB available (head, worker), 106 GiB needed' "$T/out" && ! grep -q ' run ' "$LOG"; then
  pass "memory guard: 85.8 GiB available < 106 (0.85 x MemTotal + 2): refused, nothing started"
else fail "memory refusal: $(out)"; fi
if ! HY4_WORKER_MEMINFO="$T/meminfo-low" startrc && grep -q 'memory: only 114.4 85.8 GiB' "$T/out"; then pass "memory guard: the worker's memory counts too"
else fail "worker memory: $(out)"; fi
touch "$T/port-busy"
if ! startrc && grep -q 'already listens on :8000' "$T/out" && ! grep -q ' run ' "$LOG"; then pass "something on :8000 (another model): refused"
else fail "port busy: $(out)"; fi
rm -f "$T/port-busy"
mv "$T/whome/Documents/atakans-Hy4-flash-NVFP4/.hy4-verified" "$T/v.bak"
if ! startrc && grep -q 'spark-2: .* not verified' "$T/out"; then pass "weights not verified on spark-2: refused"
else fail "worker unverified: $(out)"; fi
mv "$T/v.bak" "$T/whome/Documents/atakans-Hy4-flash-NVFP4/.hy4-verified"
rm -f "$T"/dw/img-*
if ! startrc && grep -q 'no image .* on spark-2' "$T/out"; then pass "image missing on spark-2: refused"
else fail "worker image: $(out)"; fi
cp "$T"/dh/img-* "$T/dw/"

# --- 5. start.sh for real (stubs): worker first, key never in argv; foreground supervision
: > "$LOG"; : > "$T/order"
if startrc && [[ "$(paste -sd, "$T/order")" == "dw run,dh run" ]] && [[ -f "$T/dh/ctr-hy4-vllm" && -f "$T/dw/ctr-hy4-vllm" ]] \
   && grep -q '^dh run .* KEYENV=set$' "$LOG" && grep -q '^dw run .* KEYENV=$' "$LOG" && ! grep -q SECRETKEY "$LOG"; then
  pass "start: stale containers removed, rank 1 on spark-2 first, then rank 0; VLLM_API_KEY reaches docker by environment only"
else fail "start: $(out) / $(cat "$LOG")"; fi
# foreground: rank 0 dies after two polls -> both stopped, non-zero exit
echo 2 > "$T/dh/dies"; : > "$LOG"
if ! HY4_POLL_S=0 FOREGROUND=1 bash "$RT/start.sh" > "$T/out" 2>&1 && grep -q 'rank 0 ended' "$T/out" && grep -q '^dw stop ' "$LOG" \
   && [[ ! -f "$T/dw/ctr-hy4-vllm" ]]; then
  pass "foreground: when rank 0 ends, rank 1 is stopped and the unit's start.sh exits non-zero (Restart=no)"
else fail "foreground: $(out) / $(cat "$LOG")"; fi
rm -f "$T/dh/dies"
# foreground: rank 1 gone -> rank 0 stopped
echo 0 > "$T/dw/dies"; : > "$LOG"
if ! HY4_POLL_S=0 HY4_MAX_POLLS=20 bash "$RT/start.sh" > "$T/out" 2>&1 && grep -q 'rank 1 on spark-2 is not running' "$T/out" && grep -q '^dh stop ' "$LOG"; then
  pass "foreground: when rank 1 ends, rank 0 is stopped"
else fail "foreground rank 1: $(out) / $(cat "$LOG")"; fi
rm -f "$T/dw/dies"

# --- 6. stop.sh: both ranks, idempotent --------------------------------------------------
startrc; : > "$LOG"
if bash "$RT/stop.sh" > "$T/out" 2>&1 && [[ ! -f "$T/dh/ctr-hy4-vllm" && ! -f "$T/dw/ctr-hy4-vllm" ]] && grep -q '^dh stop -t 60 hy4-vllm' "$LOG" && grep -q '^dw stop -t 60 hy4-vllm' "$LOG" \
   && bash "$RT/stop.sh" > /dev/null 2>&1; then
  pass "stop: rank 0 then rank 1, removed; a second stop is a no-op (exit 0)"
else fail "stop: $(out) / $(cat "$LOG")"; fi
touch "$T/ssh-down"
if ! bash "$RT/stop.sh" > "$T/out" 2>&1 && grep -q 'could not stop rank 1' "$T/out"; then pass "stop: an unreachable worker is reported, non-zero"
else fail "stop ssh down: $(out)"; fi
rm -f "$T/ssh-down"

# --- 7. memguard -----------------------------------------------------------------------
echo running > "$T/dh/ctr-mg"
printf 'MemAvailable: 1048576 kB\n' > "$T/mi-low"   # 1.0 GiB: under the 2.5 floor
: > "$LOG"
if MEMGUARD_MEMINFO="$T/mi-low" MEMGUARD_TICK=0.01 MEMGUARD_MAX_TICKS=200 bash "$KIT/memguard.sh" mg 2.5 0.05 0.5 "$T/mg.log" \
   && grep -q '^dh kill mg' "$LOG" && grep -q 'killed mg' "$T/mg.log"; then pass "memguard: under the floor for HOLD seconds: the container is killed"
else fail "memguard kill: $(cat "$T/mg.log")"; fi
echo running > "$T/dh/ctr-mg"; : > "$LOG"
printf 'MemAvailable: 10485760 kB\n' > "$T/mi-ok"
if MEMGUARD_MEMINFO="$T/mi-ok" MEMGUARD_TICK=0.01 MEMGUARD_MAX_TICKS=50 bash "$KIT/memguard.sh" mg 2.5 0.05 0.5 "$T/mg.log" && ! grep -q kill "$LOG"; then
  pass "memguard: 10 GiB available: no kill"
else fail "memguard no kill"; fi
rm -f "$T/dh/ctr-mg"
if MEMGUARD_MEMINFO="$T/mi-low" MEMGUARD_TICK=0.01 MEMGUARD_MAX_TICKS=50 bash "$KIT/memguard.sh" mg 2.5 0.05 0.5 "$T/mg.log" && grep -q 'mg is not running: exiting' "$T/mg.log"; then
  pass "memguard: exits by itself once the container is gone"
else fail "memguard exit"; fi

# --- 8. shell hygiene ------------------------------------------------------------------
if bash -n "$STAGE" "$KIT/start.sh" "$KIT/stop.sh" "$KIT/memguard.sh" "$KIT/config.sh"; then pass "bash -n"; else fail "bash -n"; fi
if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck -S warning -x "$STAGE" "$KIT/start.sh" "$KIT/stop.sh" "$KIT/memguard.sh" >/dev/null; then pass "shellcheck -S warning"
  else fail "shellcheck: $(shellcheck -S warning -x "$STAGE" "$KIT/start.sh" "$KIT/stop.sh" "$KIT/memguard.sh" 2>&1 | head -20)"; fi
fi

echo "=== $PASS passed, $FAIL failed ==="
(( FAIL == 0 ))
