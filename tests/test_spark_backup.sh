#!/bin/bash
# spark-backup.sh / spark-restore.sh: one Spark's OpenBeast overlay onto a
# drive, and back onto N nodes. Hermetic: the home directories are scratch
# trees, docker / findmnt / lsblk / zstd / nvidia-smi / ibdev2netdev /
# systemctl are recording fakes, and the "drive" is a scratch directory the
# fake findmnt calls ext4 on LUKS. The copy steps need GNU rsync >= 3 (Linux,
# or `brew install rsync` on a Mac); without it they are skipped and the
# planning, inventory and refusal tests still run.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SB="$REPO_DIR/scripts/backends/spark-backup.sh"
SR="$REPO_DIR/scripts/backends/spark-restore.sh"
PASS=0 FAIL=0 SKIP=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP: $*"; SKIP=$((SKIP + 1)); }
check() { local d="$1"; shift; if "$@"; then pass "$d"; else fail "$d"; fi; }
echo "=== spark-backup / spark-restore tests ==="

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
T="$(cd "$T" && pwd -P)"
mkdir -p "$T/bin" "$T/state" "$T/drive"
export FD_STATE="$T/state"
: > "$FD_STATE/docker.log"; : > "$FD_STATE/systemctl.log"

# ── GNU rsync >= 3 (for the copy steps) ──────────────────────────────────────
REAL_RSYNC=""
for c in rsync /opt/homebrew/bin/rsync /usr/local/bin/rsync; do
  if command -v "$c" >/dev/null 2>&1 && [[ "$("$c" --version 2>/dev/null)" =~ version\ 3\. ]]; then
    REAL_RSYNC="$(command -v "$c")"; break
  fi
done
# -A/-X need ACL/xattr support the scratch filesystem may lack (macOS, tmpfs):
# the wrapper drops them; hard links, modes, symlinks and the filter are real.
cat > "$T/bin/rsync" <<S
#!/bin/bash
a=(); for x in "\$@"; do [[ "\$x" == -aHAX ]] && x=-aH; a+=("\$x"); done
exec "$REAL_RSYNC" "\${a[@]}"
S

# ── fakes ────────────────────────────────────────────────────────────────────
cat > "$T/bin/findmnt" <<'S'
#!/bin/bash
echo "${FM_TARGET:-/media/max/obbackup} ${FM_SOURCE:-/dev/mapper/obbackup} ${FM_FSTYPE:-ext4}"
S
cat > "$T/bin/lsblk" <<'S'
#!/bin/bash
if [[ "${FM_CRYPT:-1}" == 1 ]]; then printf 'crypt\npart\ndisk\n'; else printf 'part\ndisk\n'; fi
S
cat > "$T/bin/zstd" <<'S'
#!/bin/bash
exec cat
S
cat > "$T/bin/nvidia-smi" <<'S'
#!/bin/bash
echo "NVIDIA GB10"
S
cat > "$T/bin/ibdev2netdev" <<'S'
#!/bin/bash
echo "rocep1s0f0 port 1 ==> enp1s0f0np0 (Down)"
echo "rocep1s0f1 port 1 ==> enp1s0f1np1 (Up)"
echo "roceP2p1s0f1 port 1 ==> enP2p1s0f1np1 (Up)"
S
cat > "$T/bin/systemctl" <<'S'
#!/bin/bash
echo "systemctl $*" >> "$FD_STATE/systemctl.log"
[[ "$*" == *list-unit-files* ]] && echo "openbeast.service enabled enabled"
exit 0
S
# docker: the source node has every image (with RepoDigests); a fresh node
# (FD_FRESH=1) has only what `docker load` put there, without RepoDigests.
cat > "$T/bin/docker" <<'S'
#!/bin/bash
echo "docker $*" >> "$FD_STATE/docker.log"
id_of() {
  case "$1" in
    tensorfold-dsv41:exl3-tp2-test) echo sha256:d048aaaa ;;
    tensorfold-glm53:v0.6.0) echo sha256:ead0bbbb ;;
    ghcr.io/open-webui/open-webui:main@sha256:5959|ghcr.io/open-webui/open-webui@sha256:5959) echo sha256:7573cccc ;;
    alpine:latest) echo sha256:abbcdddd ;;
    sha256:*) echo "$1" ;;
  esac
}
case "$1 $2" in
  "info --format") echo 29.6.2 ;;
  "image ls") printf 'tensorfold-dsv41:exl3-tp2-test\tsha256:d048aaaa\ntensorfold-glm53:v0.6.0\tsha256:ead0bbbb\nalpine:latest\tsha256:abbcdddd\nghcr.io/open-webui/open-webui:<none>\tsha256:7573cccc\n' ;;
  "image inspect")
    ref="${@: -1}"; i="$(id_of "$ref")"
    [[ -n "$i" ]] || exit 1
    if [[ "${FD_FRESH:-0}" == 1 ]]; then
      grep -qx "$i" "$FD_STATE/loaded" 2>/dev/null || exit 1
      [[ "$ref" == *@sha256:* ]] && exit 1     # a loaded image has no RepoDigest
    fi
    if [[ "$4" == "{{.Size}}" ]]; then echo 1000; else echo "$i"; fi ;;
  "volume inspect") [[ "${FD_FRESH:-0}" == 1 && ! -f "$FD_STATE/vol-created" ]] && exit 1; echo /var/lib/docker/volumes/x ;;
  "volume create") touch "$FD_STATE/vol-created" ;;
  "ps -q") echo c0ffee ;;
  "ps -a") echo "open-webui img Up" ;;
  "pause c0ffee"|"unpause c0ffee") echo "$1" >> "$FD_STATE/pauses" ;;
  "save "*) echo "IMAGE-TAR $2" ;;
  "load "|"load") data="$(cat)"; ref="${data#IMAGE-TAR }"; id_of "$ref" >> "$FD_STATE/loaded" ;;
  "run --rm")
    if [[ "$*" == *" tar cf - "* ]]; then echo "VOLUME-DATA"
    elif [[ "$*" == *" ls -A "* ]]; then :
    elif [[ "$*" == *"tar xf -"* ]]; then cat > "$FD_STATE/volume-imported"
    fi ;;
esac
exit 0
S
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH"
export DOCKER="$T/bin/docker" FINDMNT="$T/bin/findmnt" LSBLK="$T/bin/lsblk" ZSTD="$T/bin/zstd" \
       NVIDIA_SMI="$T/bin/nvidia-smi" IBDEV2NETDEV="$T/bin/ibdev2netdev" SYSTEMCTL="$T/bin/systemctl" \
       RSYNC="$T/bin/rsync" UNAME_M=aarch64 SPARK_BACKUP_HOSTNAME=spark-3dc6 SPARK_BACKUP_USER=max \
       SPARK_BACKUP_UID=1000 SPARK_BACKUP_GID=1000 SPARK_BACKUP_HELPER_IMAGE=alpine:latest

# ── the source node's home ────────────────────────────────────────────────────
H="$T/home-src"
mk() { mkdir -p "$(dirname "$H/$1")"; printf '%s\n' "${2:-x}" > "$H/$1"; }
mk openbeast/README.md readme
mk openbeast/.git/HEAD "ref: refs/heads/main"
mk openbeast/.run/chat.sock sock
mk openbeast/agents/__pycache__/x.pyc pyc
mk openbeast/openbeast.conf "TS_HOSTNAME=spark-1"; chmod 600 "$H/openbeast/openbeast.conf"
mkdir -p "$H/openbeast/scripts/backends"
printf 'TENSORFOLD_VERSION=1.2.7\nSPARK_IFACE=enp1s0f0np0,enP2p1s0f0np0\nSPARK_IB_HCA=rocep1s0f0,roceP2p1s0f0\nSPARK_HEAD_IP=10.100.216.1\nSPARK_NODE_IP=10.100.216.1\nMODELS_DIR=~/openbeast-models\n' > "$H/openbeast/scripts/backends/spark.env"
printf 'CLUSTER_NODES="local max@10.100.216.2"\nCLUSTER_NAME=spark-hydra\nCLUSTER_REPO=~/openbeast\n' > "$H/openbeast/scripts/backends/cluster.env"
printf 'services:\n  open-webui:\n    image: ghcr.io/open-webui/open-webui:main@sha256:5959\n' > "$H/openbeast/docker-compose.yml"
mk .config/openbeast/gate-keys/macbook.key k1; mk .config/openbeast/tensorfold-api-key k2; mk .config/openbeast/webui-max-password pw
chmod 600 "$H/.config/openbeast/gate-keys/macbook.key" "$H/.config/openbeast/tensorfold-api-key" "$H/.config/openbeast/webui-max-password"
mk .config/openbeast/hf-token tok; chmod 644 "$H/.config/openbeast/hf-token"     # wrong on purpose: inventory flags it
mk .config/systemd/user/openbeast.service unit
mkdir -p "$H/.config/systemd/user/default.target.wants"; ln -s ../openbeast.service "$H/.config/systemd/user/default.target.wants/openbeast.service"
mk .config/google-chrome/Cache junk
mk .ssh/authorized_keys ak; mk .ssh/id_ed25519_nvsync_cluster_assistant key; mk .ssh/known_hosts kh
printf 'Host spark-2\n  Hostname 10.100.216.2\n' > "$H/.ssh/config"; chmod 700 "$H/.ssh"; chmod 600 "$H/.ssh/"*
mk .bashrc rc; mk .local/bin/beast-spark sh; mk .local/opt/tensorfold-1.2.7/bin/tensorfold bin
mk openbeast-recipes/deepseek-prep/runtime/scripts/local.sh "WORKER=spark-2
FABRIC_PEER=10.100.216.2
MODEL_REVISION=04c13f12b83d57487912d87d0f1677b076810954"
head -c 300000 /dev/urandom > "$T/shard"
mkdir -p "$H/Documents/Pack" "$H/openbeast-models/deepseek-hf/hub/models--m--p/snapshots/04c13f12" "$H/.cache/huggingface/hub/models--g/blobs" "$H/.cache/huggingface/hub/models--g/snapshots/r"
cp "$T/shard" "$H/Documents/Pack/model-00001.safetensors"
ln "$H/Documents/Pack/model-00001.safetensors" "$H/openbeast-models/deepseek-hf/hub/models--m--p/snapshots/04c13f12/model-00001.safetensors"
ln "$H/Documents/Pack/model-00001.safetensors" "$H/.cache/huggingface/hub/models--g/blobs/abc"
ln -s ../../blobs/abc "$H/.cache/huggingface/hub/models--g/snapshots/r/model.safetensors"
mk openbeast-models/deepseek-hf/tensorfold-dsv41/04c13f12/rank0of2.bin r0
mk openbeast-models/deepseek-hf/tensorfold-dsv41/45ee5d0f/rank0of2.bin old-r0
mk openbeast-models/deepseek-hf/tensorfold-dsv41/kit-abe07c7d/kit.json kit
mk openbeast-models/deepseek-hf/worker-rank-cache-04c13f12/rank1of2.bin r1
mk openbeast-models/deepseek-hf/worker-rank-cache/rank1of2.bin old-r1
mk .cache/pip/wheel junk
mk stray/notes.txt "nobody claims me"
export SPARK_BACKUP_HOME="$H"
SHARD=300000

# ── inventory ────────────────────────────────────────────────────────────────
out="$(bash "$SB" inventory 2>&1)"; rc=$?
check "inventory: exits 0" test "$rc" -eq 0
check "inventory: the serving revision comes from the recipe's local.sh" grep -q "serving DeepSeek revision: 04c13f12" <<< "$out"
check "inventory: an unclaimed top-level entry is listed as UNCOVERED" grep -qE "^ +[0-9.]+ B +1  stray$" <<< "$out"
check "inventory: a group-readable secret is flagged" grep -q "readable by group/other.*hf-token" <<< "$out"
check "inventory: the old pack's rank file is 'rebuild', not backed up" grep -qE "rebuild +all .*tensorfold-dsv41/\*/" <<< "$out"
check "inventory: docker images and the WebUI volume are listed" grep -qE "volume head +present +openbeast_open-webui-data" <<< "$out"
json="$(bash "$SB" inventory --json)"
w="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["totals"]["weights"])' <<< "$json")"
check "inventory: a shard hard-linked into three trees is counted once ($w)" test "$w" -eq "$SHARD"

# ── run: refusals and dry run ────────────────────────────────────────────────
out="$(bash "$SB" run --to "$T/drive" 2>&1)"; rc=$?
check "run dry: exit 0 on a LUKS ext4 drive" test "$rc" -eq 0
check "run dry: says DRY RUN" grep -q "DRY RUN" <<< "$out"
check "run dry: writes nothing" test ! -e "$T/drive/openbeast-backup"
check "run dry: the filter keeps the serving rank file and the kit" grep -q '+ /openbeast-models/deepseek-hf/tensorfold-dsv41/04c13f12/\*\*\*' <<< "$out"
check "run dry: .run is excluded before the checkout is included" \
  python3 -c 'import sys; o=sys.stdin.read(); a=o.index("- /openbeast/.run/"); b=o.index("+ /openbeast/***"); sys.exit(0 if a < b else 1)' <<< "$out"
check "run dry: the compose image is saved by repo@digest" grep -q "open-webui@sha256:5959" <<< "$out"
out="$(FM_FSTYPE=exfat bash "$SB" run --to "$T/drive" --apply 2>&1)"; rc=$?
check "run: an exFAT drive is refused (exit $rc)" test "$rc" -ne 0
check "run: the refusal names hard links" grep -q "exfat: no owners, modes, hard links" <<< "$out"
check "run: a refused --apply writes nothing" test ! -e "$T/drive/openbeast-backup"
out="$(FM_CRYPT=0 bash "$SB" run --to "$T/drive" 2>&1)"; rc=$?
check "run: an unencrypted drive is a problem" grep -q "not on a dm-crypt (LUKS) device" <<< "$out"
out="$(FM_CRYPT=0 bash "$SB" run --to "$T/drive" --allow-unencrypted 2>&1)"; rc=$?
check "run: --allow-unencrypted clears it" test "$rc" -eq 0
out="$(FM_TARGET=/ bash "$SB" run --to "$T/drive" 2>&1)"
check "run: a destination on / is refused" grep -q "on the root filesystem" <<< "$out"

# ── fleet file validation (restore refuses bad fleets before anything) ───────
mkfleet() { printf 'FLEET_NAME=spark-hydra\nFLEET_USER=max\n%s\n' "$1" > "$T/fleet.env"; }
mkfleet $'NODE_0=spark-1 head 10.100.216.1\nNODE_1=spark-2 head 10.100.216.2'
mkdir -p "$T/fakegen"; echo '{"host":"x","uid":1000}' > "$T/fakegen/BACKUP.json"
out="$(bash "$SR" --from "$T/fakegen" --fleet "$T/fleet.env" --index 1 2>&1)"
check "fleet: two heads are refused" grep -q "NODE_0 must be the head and the only head" <<< "$out"
mkfleet $'NODE_0=spark-1 head 10.100.216.1\nNODE_1=spark-2 worker 10.100.216.1'
out="$(bash "$SR" --from "$T/fakegen" --fleet "$T/fleet.env" --index 1 2>&1)"
check "fleet: one fabric address on two nodes is refused" grep -q "given to two nodes" <<< "$out"
f=""; for i in 0 1 2 3 4 5 6 7 8; do r="worker"; [[ $i == 0 ]] && r="head"; f+="NODE_$i=spark-$((i+1)) $r 10.100.216.$((i+1))"$'\n'; done
mkfleet "$f"
out="$(bash "$SR" --from "$T/fakegen" --fleet "$T/fleet.env" --index 1 2>&1)"
check "fleet: nine nodes are refused (1 to 8)" grep -q "1 to 8" <<< "$out"

if [[ -z "$REAL_RSYNC" ]]; then
  skip "backup/verify/restore copies: no GNU rsync >= 3 here (brew install rsync)"
  echo; echo "=== spark-backup: $PASS passed, $FAIL failed, $SKIP skipped ==="
  [[ $FAIL -eq 0 ]]; exit
fi

# ── run --apply: generation 1 ────────────────────────────────────────────────
out="$(SPARK_BACKUP_NOW=20261118T000000Z bash "$SB" run --to "$T/drive" --apply 2>&1)"; rc=$?
G1="$T/drive/openbeast-backup/spark-3dc6/20261118T000000Z"
check "run: exit 0 ($rc)" test "$rc" -eq 0
[[ $rc -eq 0 ]] || echo "$out" | tail -20
check "run: the generation exists, no .partial left" test -f "$G1/BACKUP.json" -a ! -e "$G1.partial"
check "run: latest points at it" test "$(readlink "$T/drive/openbeast-backup/spark-3dc6/latest")" = 20261118T000000Z
inode() { python3 -c 'import os,sys; print(os.stat(sys.argv[1]).st_ino)' "$1"; }
check "run: Documents/ and the HF caches stay ONE file (hard links kept)" test \
  "$(inode "$G1/home/Documents/Pack/model-00001.safetensors")" = "$(inode "$G1/home/.cache/huggingface/hub/models--g/blobs/abc")" -a \
  "$(inode "$G1/home/Documents/Pack/model-00001.safetensors")" = "$(inode "$G1/home/openbeast-models/deepseek-hf/hub/models--m--p/snapshots/04c13f12/model-00001.safetensors")"
check "run: the HF snapshot symlink is kept as a symlink" test -L "$G1/home/.cache/huggingface/hub/models--g/snapshots/r/model.safetensors"
check "run: the serving pack's rank files are in" test -f "$G1/home/openbeast-models/deepseek-hf/tensorfold-dsv41/04c13f12/rank0of2.bin" -a -f "$G1/home/openbeast-models/deepseek-hf/worker-rank-cache-04c13f12/rank1of2.bin"
check "run: the old pack's rank files are not" test ! -e "$G1/home/openbeast-models/deepseek-hf/tensorfold-dsv41/45ee5d0f" -a ! -e "$G1/home/openbeast-models/deepseek-hf/worker-rank-cache"
check "run: sockets, pip cache, browser cache and unclaimed entries are not" test ! -e "$G1/home/openbeast/.run" -a ! -e "$G1/home/.cache/pip" -a ! -e "$G1/home/.config/google-chrome" -a ! -e "$G1/home/stray"
check "run: per-node files are kept for the record" test -f "$G1/home/openbeast/scripts/backends/spark.env" -a -f "$G1/home/.ssh/known_hosts"
check "run: secrets keep mode 0600" test "$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$G1/home/openbeast/openbeast.conf")" = 0o600
check "run: the uncommitted checkout comes whole (.git too)" test -f "$G1/home/openbeast/.git/HEAD"
check "run: the images are saved, the compose pin recorded" python3 - "$G1/docker/images.json" <<'P'
import json, sys
e = {x["ref"]: x for x in json.load(open(sys.argv[1]))}
assert "ghcr.io/open-webui/open-webui@sha256:5959" in e, e
assert e["ghcr.io/open-webui/open-webui@sha256:5959"]["compose_ref"].endswith(":main@sha256:5959")
assert "tensorfold-dsv41:exl3-tp2-test" in e and not any("pytorch" in k for k in e)
P
check "run: the volume container was paused, then unpaused" test "$(tr '\n' ' ' < "$FD_STATE/pauses")" = "pause unpause "
check "run: the volume export is in the generation" grep -q VOLUME-DATA "$G1/docker/volume-openbeast_open-webui-data.tar.zst"
check "run: SHA256SUMS checks with the stock tool" bash -c "cd '$G1' && { sha256sum -c --quiet SHA256SUMS 2>/dev/null || shasum -a 256 -c --quiet SHA256SUMS; }"
check "run: the items file travels with the generation" cmp -s "$G1/ITEMS" "$REPO_DIR/scripts/backends/data/spark-backup.items"

# ── verify ───────────────────────────────────────────────────────────────────
out="$(bash "$SB" verify "$G1" --deep 2>&1)"; rc=$?
check "verify --deep: intact (exit $rc)" test "$rc" -eq 0
cp "$G1/home/.bashrc" "$T/bashrc.save"; printf 'XX\n' > "$G1/home/.bashrc"   # same size, other bytes
out="$(bash "$SB" verify "$G1" 2>&1)"; rc=$?
check "verify (fast): a same-size edit passes the size check" test "$rc" -eq 0
out="$(bash "$SB" verify "$G1" --deep 2>&1)"; rc=$?
check "verify --deep: the edit is a sha256 mismatch" grep -q "sha256 mismatch: home/.bashrc" <<< "$out"
cp "$T/bashrc.save" "$G1/home/.bashrc"
printf 'changed\n' > "$H/.bashrc"
out="$(bash "$SB" verify "$G1" --against-live 2>&1)"
check "verify --against-live: a file changed on the live node is reported" grep -q "changed since backup: .bashrc" <<< "$out"

# ── generation 2: incremental ────────────────────────────────────────────────
out="$(SPARK_BACKUP_NOW=20261119T000000Z bash "$SB" run --to "$T/drive" --apply 2>&1)"; rc=$?
G2="$T/drive/openbeast-backup/spark-3dc6/20261119T000000Z"
check "run 2: exit 0" test "$rc" -eq 0
check "run 2: an unchanged shard is a hard link of generation 1's" test "$(inode "$G1/home/Documents/Pack/model-00001.safetensors")" = "$(inode "$G2/home/Documents/Pack/model-00001.safetensors")"
check "run 2: an unchanged image is linked, not saved again" test "$(inode "$G1/docker/tensorfold-dsv41_exl3-tp2-test.tar.zst")" = "$(inode "$G2/docker/tensorfold-dsv41_exl3-tp2-test.tar.zst")"
check "run 2: the changed file is a new copy" test "$(cat "$G2/home/.bashrc")" = changed
# a worker's own backup: config only (the head's backup holds the weights)
out="$(SPARK_BACKUP_HOSTNAME=spark-9f26 SPARK_BACKUP_NOW=20261119T010000Z bash "$SB" run --to "$T/drive" --config-only --apply 2>&1)"; rc=$?
GW="$T/drive/openbeast-backup/spark-9f26/20261119T010000Z"
check "run --config-only: exit 0, checkout and secrets in" test "$rc" -eq 0 -a -f "$GW/home/openbeast/README.md" -a -f "$GW/home/.config/openbeast/tensorfold-api-key"
check "run --config-only: no weights, rank files or images" test ! -e "$GW/home/Documents" -a ! -e "$GW/home/openbeast-models/deepseek-hf/worker-rank-cache-04c13f12" -a "$(cat "$GW/docker/images.json")" = "[]"
out="$(bash "$SB" list "$T/drive")"
check "list: both generations, latest marked" grep -q "20261119T000000Z  <- latest" <<< "$out"

# ── restore: a worker (index 1) on a fresh node ──────────────────────────────
export FD_FRESH=1 SPARK_RESTORE_HOME_CHECK=0
: > "$FD_STATE/loaded"; : > "$FD_STATE/docker.log"
printf 'FLEET_NAME=spark-hydra\nFLEET_USER=max\nNODE_0=spark-1 head 10.100.216.1,10.100.217.1\nNODE_1=spark-2 worker 10.100.216.2,10.100.217.2\n' > "$T/fleet2.env"
W="$T/home-w"; mkdir -p "$W/.ssh"; printf 'Host spark-1\n  ### CreatedBy: NVIDIA Sync\n  Hostname 10.100.216.1\n' > "$W/.ssh/config"
LATEST="$T/drive/openbeast-backup/spark-3dc6/latest"
out="$(SPARK_BACKUP_HOME="$W" bash "$SR" --from "$LATEST" --fleet "$T/fleet2.env" --index 1 2>&1)"; rc=$?
check "restore dry: exit 0" test "$rc" -eq 0
check "restore dry: writes nothing" test ! -e "$W/openbeast"
check "restore dry: prints the root steps for this node" grep -q "spark-root-setup.sh spark-2 --guard" <<< "$out"
out="$(SPARK_BACKUP_HOME="$W" bash "$SR" --from "$LATEST" --fleet "$T/fleet2.env" --index 1 --no-units 2>&1)"
check "restore dry: --no-units enables nothing (a scratch drill)" grep -q "enable (NOT start): none" <<< "$out"
out="$(SPARK_BACKUP_HOME="$W" bash "$SR" --from "$LATEST" --fleet "$T/fleet2.env" --index 1 --apply < /dev/null 2>&1)"; rc=$?
check "restore: --apply without --yes and no terminal is refused" test "$rc" -ne 0 -a ! -e "$W/openbeast"
out="$(SPARK_BACKUP_UID=1001 SPARK_BACKUP_HOME="$W" bash "$SR" --from "$LATEST" --fleet "$T/fleet2.env" --index 1 --apply --yes 2>&1)"; rc=$?
check "restore: another uid is refused" test "$rc" -ne 0 -a ! -e "$W/openbeast"
out="$(SPARK_BACKUP_HOME="$W" bash "$SR" --from "$LATEST" --fleet "$T/fleet2.env" --index 1 --apply --yes 2>&1)"; rc=$?
check "restore worker: exit 0 ($rc)" test "$rc" -eq 0
[[ $rc -eq 0 ]] || echo "$out" | tail -20
check "restore worker: the checkout and the shared key arrive" test -f "$W/openbeast/README.md" -a -f "$W/.config/openbeast/tensorfold-api-key"
check "restore worker: no head secrets (openbeast.conf, gate keys, WebUI password)" test ! -e "$W/openbeast/openbeast.conf" -a ! -e "$W/.config/openbeast/gate-keys" -a ! -e "$W/.config/openbeast/webui-max-password"
check "restore worker: no recipes and no rank-0 file" test ! -e "$W/openbeast-recipes" -a ! -e "$W/openbeast-models/deepseek-hf/tensorfold-dsv41/04c13f12"
check "restore worker: the rank-1 file and the kit arrive" test -f "$W/openbeast-models/deepseek-hf/worker-rank-cache-04c13f12/rank1of2.bin" -a -f "$W/openbeast-models/deepseek-hf/tensorfold-dsv41/kit-abe07c7d/kit.json"
check "restore worker: weights stay hard links on the node" test "$(inode "$W/Documents/Pack/model-00001.safetensors")" = "$(inode "$W/.cache/huggingface/hub/models--g/blobs/abc")"
check "restore worker: the head's boot links are not copied" test ! -e "$W/.config/systemd/user/default.target.wants"
check "restore worker: known_hosts is not copied" test ! -e "$W/.ssh/known_hosts"
se="$W/openbeast/scripts/backends/spark.env"
check "restore worker: spark.env has this node's address" grep -qx "SPARK_NODE_IP=10.100.216.2" "$se"
check "restore worker: spark.env keeps the head address and the pins" grep -qx "SPARK_HEAD_IP=10.100.216.1" "$se" && grep -qx "TENSORFOLD_VERSION=1.2.7" "$se"
check "restore worker: SPARK_IFACE / HCA from the ports Up HERE" grep -qx "SPARK_IFACE=enp1s0f1np1,enP2p1s0f1np1" "$se" && grep -qx "SPARK_IB_HCA=rocep1s0f1,roceP2p1s0f1" "$se"
check "restore worker: no cluster.env (head only)" test ! -e "$W/openbeast/scripts/backends/cluster.env"
check "restore worker: the model-link unit points at the head" grep -q "max@10.100.216.1$" "$W/.config/systemd/user/opencode-model-link.service"
check "restore worker: ssh config keeps NVIDIA Sync's entry and adds none twice" test "$(grep -c '^Host spark-1$' "$W/.ssh/config")" -eq 1
check "restore worker: ssh config gains the managed block (rail 2 alias)" grep -q "^Host 10.100.217.1$" "$W/.ssh/config"
check "restore worker: loads only the inference images" bash -c "grep -qx sha256:d048aaaa '$FD_STATE/loaded' && ! grep -q 7573cccc '$FD_STATE/loaded'"
check "restore worker: enables (never starts) its unit" bash -c "grep -q 'enable opencode-model-link.service' '$FD_STATE/systemctl.log' && ! grep -q ' start ' '$FD_STATE/systemctl.log'"
check "restore worker: no volume import" test ! -e "$FD_STATE/volume-imported"
out="$(SPARK_BACKUP_HOME="$W" bash "$SR" --from "$LATEST" --fleet "$T/fleet2.env" --index 1 --apply --yes 2>&1)"; rc=$?
check "restore worker: a second run is idempotent (images already here)" grep -q "tensorfold-dsv41:exl3-tp2-test: already here" <<< "$out"

# ── restore: the head of a 3-node fleet ──────────────────────────────────────
: > "$FD_STATE/loaded"; rm -f "$FD_STATE/vol-created" "$FD_STATE/volume-imported"
printf 'NODE_0=spark-7 head 10.100.216.11\nNODE_1=spark-8 worker 10.100.216.12\nNODE_2=spark-9 worker 10.100.216.13\n' > "$T/fleet3.env"
HD="$T/home-h"; mkdir -p "$HD"
out="$(SPARK_BACKUP_HOME="$HD" bash "$SR" --from "$LATEST" --fleet "$T/fleet3.env" --index 0 --apply --yes 2>&1)"; rc=$?
check "restore head: exit 0 ($rc)" test "$rc" -eq 0
[[ $rc -eq 0 ]] || echo "$out" | tail -20
check "restore head: cluster.env lists the fleet in rank order" grep -qx 'CLUSTER_NODES="local max@10.100.216.12 max@10.100.216.13"' "$HD/openbeast/scripts/backends/cluster.env"
check "restore head: openbeast.conf arrives 0600 with this node's tailnet name" bash -c "grep -qx TS_HOSTNAME=spark-7 '$HD/openbeast/openbeast.conf' && test \"\$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' '$HD/openbeast/openbeast.conf')\" = 0o600"
check "restore head: the gate keys arrive" test -f "$HD/.config/openbeast/gate-keys/macbook.key"
check "restore head: the TP-2 recipe is not rewired for 3 nodes, and says so" bash -c "grep -qx WORKER=spark-2 '$HD/$( echo openbeast-recipes/deepseek-prep/runtime/scripts/local.sh)' && grep -q 'TWO-Spark' <<< \"\$1\"" _ "$out"
check "restore head: the compose digest pin is rewritten to the loaded ID" grep -q "image: sha256:7573cccc" "$HD/openbeast/docker-compose.yml"
check "restore head: the original compose file is kept" grep -q "main@sha256:5959" "$HD/openbeast/docker-compose.yml.pre-restore"
check "restore head: the WebUI volume is imported into the new volume" grep -q VOLUME-DATA "$FD_STATE/volume-imported"
check "restore head: the serving rank-0 file arrives" test -f "$HD/openbeast-models/deepseek-hf/tensorfold-dsv41/04c13f12/rank0of2.bin"

echo; echo "=== spark-backup: $PASS passed, $FAIL failed, $SKIP skipped ==="
[[ $FAIL -eq 0 ]]
