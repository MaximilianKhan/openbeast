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

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
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
cat > "$T/bin/docker" <<'S'
#!/bin/bash
echo "docker $*" >> "${SC_DOCKER_LOG:-/dev/null}"
case "$1" in info) exit 0 ;; image) echo "sha256:${SC_IMAGE_ID:-aaa}"; exit 0 ;; esac
exit 0
S
chmod +x "$T/bin/"*
git -C "$T" init -q repo && git -C "$T/repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
export SC_SSH_LOG="$T/ssh.log" PATH="$T/bin:$PATH"
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

# sync-image: a worker that already has the same image id is left alone; one
# that lacks it gets docker save | ssh docker load.
_cluster "local n1"
export SC_DOCKER_LOG="$T/docker.log"; : > "$SC_DOCKER_LOG"
_run sync-image --cluster "$T/cluster.env" some/image:tag
[[ $RC -eq 0 ]] && _has "$OUT" "rank 1: has some/image:tag" && ! grep -q "docker save" "$SC_DOCKER_LOG" \
  && pass "sync-image: an identical image is not resent" || fail "sync-image same id (rc=$RC): $OUT"

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
