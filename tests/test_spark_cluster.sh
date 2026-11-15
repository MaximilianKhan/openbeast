#!/bin/bash
# spark-cluster.sh: a DGX Spark cluster of 1 to 8 nodes, driven from rank 0.
# Hermetic: ssh is a stub that runs the remote command locally with NODE set,
# and every node probe (uname, hostname, nvidia-smi, dpkg-query, ibdev2netdev,
# ip, df, docker) is a stub that answers per NODE, so the suite gives the same
# answers on a Spark, on x86_64 CI and on a Mac. No network, no docker, no GPU.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SC="$REPO_DIR/scripts/backends/spark-cluster.sh"
PASS=0 FAIL=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
echo "=== spark-cluster tests ==="

T="$(cd "$(mktemp -d)" && pwd -P)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home"
# ssh <opts...> <target> <cmd>: the last two arguments are target and command.
cat > "$T/bin/ssh" <<'S'
#!/bin/bash
args=("$@"); n=${#args[@]}
target="${args[$((n - 2))]}"; cmd="${args[$((n - 1))]}"
echo "$target :: $cmd" >> "$SC_SSH_LOG"
NODE="$target" bash -c "$cmd"
S
# A DGX Spark answers aarch64 / its own hostname, whatever box runs the suite.
cat > "$T/bin/uname" <<'S'
#!/bin/bash
[[ "${1:-}" == -m ]] && { echo "${SC_ARCH:-aarch64}"; exit 0; }
echo Linux
S
cat > "$T/bin/hostname" <<'S'
#!/bin/bash
echo "spark-${NODE:-local}"
S
cat > "$T/bin/df" <<'S'
#!/bin/bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/nvme0n1p2 3800000000 1000000000 2800000000 27% /"
S
cat > "$T/bin/nvidia-smi" <<'S'
#!/bin/bash
echo "NVIDIA GB10"
S
cat > "$T/bin/dpkg-query" <<'S'
#!/bin/bash
echo "2.28.9-1+cuda13.0"
S
# Fabric per node: SC_FABRIC_<sanitised NODE or LOCAL> = "iface=cidr ..."
cat > "$T/bin/ibdev2netdev" <<'S'
#!/bin/bash
k="SC_FABRIC_$(printf '%s' "${NODE:-local}" | tr -c 'A-Za-z0-9' '_')"
for pair in ${!k:-}; do echo "roce${pair%%=*} port 1 ==> ${pair%%=*} (Up)"; done
S
cat > "$T/bin/ip" <<'S'
#!/bin/bash
# ip -4 -o addr show dev IFACE
iface="${@: -1}"; k="SC_FABRIC_$(printf '%s' "${NODE:-local}" | tr -c 'A-Za-z0-9' '_')"
for pair in ${!k:-}; do [[ "${pair%%=*}" == "$iface" ]] && echo "5: $iface    inet ${pair#*=} scope global $iface"; done
S
# docker, per NODE: $SC_DIMG/<node> holds that node's image id (absent = no
# image), <node>.digests its RepoDigests. `save` writes the id as the "image",
# `load` reads it back (SC_LOAD_AS overrides what lands: a corrupt transfer)
# and, like the classic overlay2 store, records NO RepoDigest.
cat > "$T/bin/docker" <<'S'
#!/bin/bash
n="${NODE:-local}"; st="$SC_DIMG"
echo "$n docker $*" >> "${SC_DOCKER_LOG:-/dev/null}"
case "$1" in
  info) exit 0 ;;
  image)
    [[ -f "$st/$n" ]] || { echo "Error: No such image" >&2; exit 1; }
    if [[ "$*" == *RepoDigests* ]]; then cat "$st/$n.digests" 2>/dev/null || echo; else cat "$st/$n"; fi
    exit 0 ;;
  save) echo "IMAGE $(cat "$st/$n")" ;;
  load) read -r _ id; echo "${SC_LOAD_AS:-$id}" > "$st/$n"; rm -f "$st/$n.digests"; echo "Loaded image ID: $id" ;;
esac
exit 0
S
chmod +x "$T/bin/"*
git -C "$T" init -q repo && git -C "$T/repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
export SC_SSH_LOG="$T/ssh.log" SC_DIMG="$T/dimg" PATH="$T/bin:$PATH"
mkdir -p "$SC_DIMG"
_run() { : > "$SC_SSH_LOG"; OUT="$(HOME="$T/home" bash "$SC" "$@" 2>&1)"; RC=$?; }
_has() { grep -qF -- "$2" <<< "$1"; }
_cluster() { printf 'CLUSTER_NODES="%s"\nCLUSTER_NAME=test\nCLUSTER_REPO=%s\n' "$1" "$T/repo" > "$T/cluster.env"; }

# Node counts: 1..8 accepted, 0 and 9 refused, rank 0 must be local.
_cluster ""; _run status --cluster "$T/cluster.env"
[[ $RC -ne 0 ]] && _has "$OUT" "1 to 8" && pass "an empty cluster is refused" || fail "empty cluster (rc=$RC): $OUT"
_cluster "local n1 n2 n3 n4 n5 n6 n7 n8"; _run status --cluster "$T/cluster.env"
[[ $RC -ne 0 ]] && _has "$OUT" "lists 9 node(s)" && pass "nine nodes are refused (one rank per Spark, 8 at most)" || fail "nine nodes (rc=$RC): $OUT"
_cluster "n0 local"; _run status --cluster "$T/cluster.env"
[[ $RC -ne 0 ]] && _has "$OUT" "rank 0 must be" && pass "rank 0 must be the local head" || fail "remote rank 0 (rc=$RC): $OUT"
_cluster "local n1 'x;rm'"; _run status --cluster "$T/cluster.env"
[[ $RC -ne 0 ]] && _has "$OUT" "is not a plain ssh target" && pass "a node entry that is not a plain ssh target is refused" || fail "hostile node entry (rc=$RC): $OUT"
_cluster "local n1 n2 n3 n4 n5 n6 n7"; _run status --cluster "$T/cluster.env"
[[ $RC -eq 0 ]] && [[ "$(grep -c '^  rank' <<< "$OUT")" == 8 ]] && pass "status covers all 8 ranks of an 8-node cluster" || fail "8-node status (rc=$RC): $OUT"

# check: the fabric pairing. A switch (one subnet) and a direct cable pass; a
# mirrored ring (one pair with no common subnet) fails and names the pair.
export SC_FABRIC_local="cx0=10.9.0.1/24" SC_FABRIC_n1="cx0=10.9.0.2/24" SC_FABRIC_n2="cx0=10.9.0.3/24" SC_FABRIC_n3="cx0=10.9.0.4/24"
_cluster "local n1 n2 n3"; _run check --cluster "$T/cluster.env"
[[ $RC -eq 0 ]] && _has "$OUT" "every pair of ranks shares a subnet" && _has "$OUT" "a RoCE-capable switch" \
  && pass "check: 4 nodes on one switch subnet pass" || fail "check 4-node switch (rc=$RC): $OUT"
export SC_FABRIC_local="a=10.1.0.1/24 b=10.3.0.1/24" SC_FABRIC_n1="a=10.1.0.2/24 b=10.2.0.1/24" SC_FABRIC_n2="a=10.2.0.2/24 b=10.3.0.2/24"
_cluster "local n1 n2"; _run check --cluster "$T/cluster.env"
[[ $RC -eq 0 ]] && _has "$OUT" "every pair of ranks shares a subnet" && pass "check: a correctly cabled 3-node ring passes" || fail "check ring (rc=$RC): $OUT"
export SC_FABRIC_n2="a=10.2.0.2/24 b=10.4.0.2/24"
_run check --cluster "$T/cluster.env"
[[ $RC -ne 0 ]] && _has "$OUT" "no common fabric subnet: 0-2" && pass "check: a mis-wired ring names the pair NCCL cannot connect" || fail "check mis-wired ring (rc=$RC): $OUT"
_cluster "local"; unset SC_FABRIC_local; _run check --cluster "$T/cluster.env"
[[ $RC -eq 0 ]] && _has "$OUT" "one Spark — no fabric needed" && pass "check: one Spark needs no fabric" || fail "check one node (rc=$RC): $OUT"

# start/stop: workers first on start (ranks N-1..1, then 0), rank 0 first on
# stop; every node runs its own spark-node.sh with its own rank.
_cluster "local n1"
_run start --cluster "$T/cluster.env" --profile glm53-flash-ablit-exl3-tensorfold --dry-run
if [[ $RC -eq 0 ]] && [[ "$(grep -o '^\[rank [0-9]' <<< "$OUT" | tr -d '[' | paste -sd, -)" == "rank 1,rank 0" ]] \
   && _has "$OUT" "scripts/backends/tensorfold/spark-node.sh --profile glm53-flash-ablit-exl3-tensorfold --rank 1"; then
  pass "start: the worker starts before the head, each with its own --rank"
else
  fail "start order (rc=$RC): $OUT"
fi
_run stop --cluster "$T/cluster.env" --profile glm53-flash-ablit-exl3-tensorfold --dry-run
[[ $RC -eq 0 ]] && [[ "$(grep -o '^\[rank [0-9]' <<< "$OUT" | tr -d '[' | paste -sd, -)" == "rank 0,rank 1" ]] && _has "$OUT" "--rank 0 --stop" \
  && pass "stop: the head stops first, then the workers" || fail "stop order (rc=$RC): $OUT"
_cluster "local n1 n2"
_run start --cluster "$T/cluster.env" --profile glm53-flash-ablit-exl3-tensorfold --dry-run
[[ $RC -ne 0 ]] && _has "$OUT" "TENSOR_PARALLEL_SIZE=2 but" && pass "a profile whose TP is not the node count is refused" || fail "TP mismatch (rc=$RC): $OUT"
_run start --cluster "$T/cluster.env" --dry-run
[[ $RC -ne 0 ]] && _has "$OUT" "--profile NAME is required" && pass "start needs a profile" || fail "start without profile (rc=$RC): $OUT"

# Real (not --dry-run) start/stop against stub launchers: a checkout whose
# spark-node.sh records "<node> <engine> <args>" and fails where told
# (SC_LAUNCH_FAIL="<node>:<start|stop> ...", node = local for rank 0).
mkdir -p "$T/srepo/scripts/backends/tensorfold" "$T/srepo/scripts/backends/vllm"
for _e in tensorfold vllm; do
  cat > "$T/srepo/scripts/backends/$_e/spark-node.sh" <<S
#!/bin/bash
n="\${NODE:-local}"; what=start; [[ " \$* " == *" --stop "* ]] && what=stop
echo "\$n $_e \$*" >> "\$SC_LAUNCH_LOG"
[[ " \${SC_LAUNCH_FAIL:-} " == *" \$n:\$what "* ]] && { echo "stub: \$n refuses to \$what" >&2; exit 1; }
exit 0
S
  chmod +x "$T/srepo/scripts/backends/$_e/spark-node.sh"
done
git -C "$T" init -q srepo && git -C "$T/srepo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
export SC_LAUNCH_LOG="$T/launch.log"
printf 'CLUSTER_NODES="local n1 n2 n3"\nCLUSTER_NAME=test\nCLUSTER_REPO=%s\n' "$T/srepo" > "$T/cluster4.env"
_REV=0123456789abcdef0123456789abcdef01234567
printf 'BACKEND=tensorfold\nSOURCE=acme/Some-Model-MLX\nREVISION=%s\nSERVED_MODEL_NAME=m\nTENSOR_PARALLEL_SIZE=4\n' "$_REV" > "$T/tf4.env"

# stop with rank 0 already gone: every other rank is still stopped, and the
# run names the rank that failed (exit 1).
: > "$SC_LAUNCH_LOG"
SC_LAUNCH_FAIL="local:stop" _run stop --cluster "$T/cluster4.env" --engine tensorfold
if [[ $RC -ne 0 ]] && [[ "$(grep -c -- '--stop' "$SC_LAUNCH_LOG")" == 4 ]] && grep -q "^n3 tensorfold --rank 3 --stop" "$SC_LAUNCH_LOG" \
   && _has "$OUT" "could not stop rank(s): 0"; then
  pass "stop: a rank that fails to stop does not stop the others; the failed rank is named"
else
  fail "stop with dead rank 0 (rc=$RC): $OUT / $(cat "$SC_LAUNCH_LOG")"
fi
# stop needs no profile and, without --engine, stops both engines' ranks.
: > "$SC_LAUNCH_LOG"; _run stop --cluster "$T/cluster4.env"
if [[ $RC -eq 0 ]] && grep -q "^n2 tensorfold --rank 2 --stop" "$SC_LAUNCH_LOG" && grep -q "^n2 vllm --rank 2 --stop" "$SC_LAUNCH_LOG"; then
  pass "stop: no --profile needed; without --engine both engines' ranks are stopped"
else
  fail "stop without profile/engine (rc=$RC): $OUT / $(cat "$SC_LAUNCH_LOG")"
fi
# start: rank 2 refuses after rank 3 is up -> rank 3 is stopped again, ranks
# 1 and 0 never start, exit 1.
: > "$SC_LAUNCH_LOG"
SC_LAUNCH_FAIL="n2:start" _run start --cluster "$T/cluster4.env" --profile "$T/tf4.env"
if [[ $RC -ne 0 ]] && grep -q "^n3 tensorfold --profile tf4 --rank 3$" "$SC_LAUNCH_LOG" \
   && grep -q "^n3 tensorfold --rank 3 --stop" "$SC_LAUNCH_LOG" \
   && ! grep -q "^n1 " "$SC_LAUNCH_LOG" && ! grep -q "^local " "$SC_LAUNCH_LOG" \
   && _has "$OUT" "rank 2 (n2) failed to start" && _has "$OUT" "rolling back rank(s) 3"; then
  pass "start: a rank that fails rolls back the ranks already started (no half-up cluster)"
else
  fail "start rollback (rc=$RC): $OUT / $(cat "$SC_LAUNCH_LOG")"
fi
: > "$SC_LAUNCH_LOG"
_run start --cluster "$T/cluster4.env" --profile "$T/tf4.env"
[[ $RC -eq 0 ]] && [[ "$(cut -d' ' -f1 "$SC_LAUNCH_LOG" | paste -sd, -)" == "n3,n2,n1,local" ]] && ! grep -q -- --stop "$SC_LAUNCH_LOG" \
  && pass "start: a clean start launches ranks 3,2,1 then 0 and stops nothing" || fail "clean start (rc=$RC): $OUT / $(cat "$SC_LAUNCH_LOG")"

# sync-image: a worker that already has the same image id is left alone; one
# that lacks it gets docker save | ssh docker load.
_cluster "local n1 n2"
export SC_DOCKER_LOG="$T/docker.log"; : > "$SC_DOCKER_LOG"
_ID="sha256:$(printf 'c%.0s' $(seq 1 64))"
echo "$_ID" > "$SC_DIMG/local"; echo "some/image@sha256:feed" > "$SC_DIMG/local.digests"
echo "$_ID" > "$SC_DIMG/n1"; echo "some/image@sha256:feed" > "$SC_DIMG/n1.digests"; rm -f "$SC_DIMG/n2" "$SC_DIMG/n2.digests"
_run sync-image --cluster "$T/cluster.env" some/image:tag
if [[ $RC -eq 0 ]] && _has "$OUT" "rank 1: has some/image:tag" && ! grep -q "^n1 docker load" "$SC_DOCKER_LOG" \
   && grep -q "^n2 docker load" "$SC_DOCKER_LOG" && [[ "$(cat "$SC_DIMG/n2")" == "$_ID" ]] \
   && _has "$OUT" "rank 2: loaded, identical (image id $_ID)"; then
  pass "sync-image: an identical image is not resent; a missing one goes docker save | ssh docker load and is re-checked"
else
  fail "sync-image send path (rc=$RC): $OUT / $(cat "$SC_DOCKER_LOG")"
fi
# docker load carries no RepoDigest: say so, and give the pin that works there.
if _has "$OUT" "rank 2: has no RepoDigest after docker load" && _has "$OUT" "VLLM_IMAGE_ID=$_ID" && _has "$OUT" "TENSORFOLD_IMAGE_ID=$_ID"; then
  pass "sync-image: says a loaded image has no RepoDigest and prints the *_IMAGE_ID pin the launchers run by"
else
  fail "sync-image RepoDigest note (rc=$RC): $OUT"
fi
rm -f "$SC_DIMG/n2"
SC_LOAD_AS="sha256:bad" _run sync-image --cluster "$T/cluster.env" some/image:tag
[[ $RC -ne 0 ]] && _has "$OUT" "rank 2: image id sha256:bad after load, head has $_ID" \
  && pass "sync-image: a load that lands a different image id fails" || fail "sync-image id mismatch (rc=$RC): $OUT"

# sync-weights: the HEAD's lock is the pin for every worker. Workers are on
# this filesystem (same paths), so rsync is a recording stub; what is tested
# is which lock each worker verifies against, and what gets copied.
cat > "$T/bin/rsync" <<'S'
#!/bin/bash
echo "rsync $*" >> "$SC_RSYNC_LOG"
S
chmod +x "$T/bin/rsync"
export SC_RSYNC_LOG="$T/rsync.log" MODELS_DIR="$T/models"
mkdir -p "$T/wrepo/scripts" "$T/hp" "$MODELS_DIR"
cp -R "$REPO_DIR/scripts/backends" "$T/wrepo/scripts/backends"
rm -f "$T/wrepo/scripts/backends/models/"*.lock
git -C "$T" init -q wrepo && git -C "$T/wrepo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
_DREV=89abcdef0123456789abcdef0123456789abcdef
printf 'BACKEND=tensorfold\nSOURCE=acme/Some-Model-MLX\nREVISION=%s\nSERVED_MODEL_NAME=m\nTENSOR_PARALLEL_SIZE=2\nDRAFTER_SOURCE=acme/Some-Drafter\nDRAFTER_REVISION=%s\n' "$_REV" "$_DREV" > "$T/hp/syn.env"
# A fetched model + drafter and the head's lock, as model-fetch.sh leaves them.
python3 - "$MODELS_DIR" "$T/hp/syn.lock" "model:syn:acme/Some-Model-MLX:$_REV" "drafter:syn.drafter:acme/Some-Drafter:$_DREV" <<'PY'
import hashlib, json, sys
from pathlib import Path
mdir, lock = Path(sys.argv[1]), Path(sys.argv[2])
arts = {}
for spec in sys.argv[3:]:
    key, d, repo, rev = spec.split(":")
    root = mdir / d
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps({"model_type": "brand_new"}))
    (root / "model.safetensors").write_bytes(b"\0" * 64)
    (root / ".openbeast-model.json").write_text(json.dumps({"source": repo, "revision": rev}))
    files = {f: {"size": (root / f).stat().st_size, "sha256": hashlib.sha256((root / f).read_bytes()).hexdigest(),
                 "hub_oid": None, "lfs": False} for f in ("config.json", "model.safetensors")}
    arts[key] = {"source": repo, "revision": rev, "dir": d, "files": files}
lock.write_text(json.dumps({"schema": 1, "artifacts": arts}))
PY
_WM="$T/wrepo/scripts/backends/models"
cp "$T/hp/syn.env" "$_WM/syn.env"
printf 'CLUSTER_NODES="local n1"\nCLUSTER_NAME=test\nCLUSTER_REPO=%s\n' "$T/wrepo" > "$T/cluster-w.env"
: > "$SC_RSYNC_LOG"
_run sync-weights --cluster "$T/cluster-w.env" --profile "$T/hp/syn.env"
if [[ $RC -eq 0 ]] && cmp -s "$T/hp/syn.lock" "$_WM/syn.lock" && _has "$OUT" "installed the head's lock" \
   && _has "$OUT" "every worker holds syn"; then
  pass "sync-weights: a worker with no lock of its own is verified against the HEAD's lock (installed there), not a fresh one"
else
  fail "sync-weights head lock (rc=$RC): $OUT"
fi
if grep -qF "$MODELS_DIR/syn/ n1:$MODELS_DIR/syn/" "$SC_RSYNC_LOG" && grep -qF "$MODELS_DIR/syn.drafter/ n1:$MODELS_DIR/syn.drafter/" "$SC_RSYNC_LOG"; then
  pass "sync-weights: copies the drafter directory too"
else
  fail "sync-weights drafter (rc=$RC): $OUT // $(cat "$SC_RSYNC_LOG")"
fi
# A worker whose own lock differs from the head's: refused before any copy.
printf '{"schema": 1, "artifacts": {}}\n' > "$_WM/syn.lock"; : > "$SC_RSYNC_LOG"
_run sync-weights --cluster "$T/cluster-w.env" --profile "$T/hp/syn.env"
[[ $RC -ne 0 ]] && _has "$OUT" "differs from the head's" && [[ ! -s "$SC_RSYNC_LOG" ]] \
  && pass "sync-weights: a worker whose lock differs from the head's is refused before the copy" || fail "sync-weights lock skew (rc=$RC): $OUT"
rm -f "$_WM/syn.lock"
# A worker whose profile differs (another checkout): refused before any copy.
echo "MAX_MODEL_LEN=4096" >> "$_WM/syn.env"; : > "$SC_RSYNC_LOG"
_run sync-weights --cluster "$T/cluster-w.env" --profile "$T/hp/syn.env"
[[ $RC -ne 0 ]] && _has "$OUT" "is missing or differs from the head's" && [[ ! -s "$SC_RSYNC_LOG" ]] \
  && pass "sync-weights: a worker whose profile differs from the head's is refused" || fail "sync-weights profile skew (rc=$RC): $OUT"
cp "$T/hp/syn.env" "$_WM/syn.env"
# A local-SOURCE profile with no lock on the head: there is nothing to verify
# a worker's copy against, so nothing is sent (no trust-on-first-use lock).
mkdir -p "$T/local-model"; echo '{"model_type": "brand_new"}' > "$T/local-model/config.json"
printf 'BACKEND=tensorfold\nSOURCE=%s\nSERVED_MODEL_NAME=m\nTENSOR_PARALLEL_SIZE=2\n' "$T/local-model" > "$T/hp/loc.env"
cp "$T/hp/loc.env" "$_WM/loc.env"; : > "$SC_RSYNC_LOG"
_run sync-weights --cluster "$T/cluster-w.env" --profile "$T/hp/loc.env"
[[ $RC -ne 0 ]] && _has "$OUT" "has no lock entry" && [[ ! -s "$SC_RSYNC_LOG" ]] && [[ ! -e "$_WM/loc.lock" ]] \
  && pass "sync-weights: a head with no lock for the profile is refused (no TOFU lock on the worker)" || fail "sync-weights unlocked local (rc=$RC): $OUT"

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
