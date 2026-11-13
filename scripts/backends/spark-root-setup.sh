#!/bin/bash
# Root-only setup for ONE DGX Spark of the TensorFold pair. Run it once per
# Spark, as yourself through sudo (it needs SUDO_USER). Idempotent.
# docs/DGX_SPARK_PLAN.md §9
#
#   sudo scripts/backends/spark-root-setup.sh spark-1   # Spark 1: rank 0 + command center
#   sudo scripts/backends/spark-root-setup.sh spark-2   # Spark 2: rank 1, no services
#
# What it does, and nothing else:
#   1. libnccl2 pinned to a CUDA 13.0 build and held. The native TensorFold
#      binary dlopens libnccl.so.2 for its CUDA ranks (`tensorfold
#      capabilities --json` lists it as found:false without it). The driver is
#      R580 = CUDA 13.0, so the +cuda13.0 build, not apt's newest +cuda13.5.
#   2. loginctl enable-linger for SUDO_USER, so the rank services
#      (systemd --user) run without a login session and survive logout.
#   3. Tailscale from its signed apt repo, then `tailscale up` with the given
#      tailnet name (a login URL is printed on first run). --operator lets
#      SUDO_USER run `tailscale serve` without sudo.
#   4. ufw: lets the peer Spark reach this one on each ConnectX-7 link that is
#      Up (NCCL rendezvous, NCCL's own sockets and RoCE all need it; the cable
#      is point-to-point, so "from the peer" is everything on it).
#      --serve also opens the TensorFold port on tailscale0, only for a keyed
#      engine that must be reached directly. By default inference listens on
#      loopback behind the pair's (spark-hydra's) beast-gate, which needs no open port.
#      Nothing else in ufw is touched; status is printed before and after.
#
# It does not change netplan (NVIDIA Sync owns 99-nvidia-sync-cluster.yaml),
# the MTU, the driver or Docker.
set -euo pipefail

NCCL_VERSION="2.28.9-1+cuda13.0"
SERVE_PORT=8000
SERVE=no

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; }

TS_HOSTNAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --serve)    SERVE=yes ;;
    --port)     SERVE_PORT="${2:?--port needs a value}"; shift ;;
    -h|--help)  usage; exit 0 ;;
    -*)         echo "unknown option: $1" >&2; exit 2 ;;
    *)          TS_HOSTNAME="$1" ;;
  esac
  shift
done

[[ $EUID -eq 0 ]] || { echo "Run with sudo (it needs root and SUDO_USER)." >&2; exit 1; }
[[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]] || { echo "Run through sudo from your own account." >&2; exit 1; }
[[ "$TS_HOSTNAME" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { usage >&2; exit 2; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

step "1/4 libnccl2 $NCCL_VERSION"
if [[ "$(dpkg-query -W -f='${Version}' libnccl2 2>/dev/null || true)" == "$NCCL_VERSION" ]]; then
  echo "already installed"
else
  apt-get update -qq
  apt-mark unhold libnccl2 >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --allow-downgrades "libnccl2=$NCCL_VERSION"
fi
apt-mark hold libnccl2 >/dev/null
ldconfig -p | grep -q 'libnccl.so.2 ' && echo "libnccl.so.2 visible to the loader"

step "2/4 linger for $SUDO_USER"
loginctl enable-linger "$SUDO_USER"
loginctl show-user "$SUDO_USER" -p Linger

step "3/4 Tailscale as '$TS_HOSTNAME'"
if ! command -v tailscale >/dev/null; then
  # Tailscale's signed apt repo for this release (what install.sh sets up).
  . /etc/os-release
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.noarmor.gpg" \
    -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.tailscale-keyring.list" \
    -o /etc/apt/sources.list.d/tailscale.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tailscale
fi
systemctl enable --now tailscaled
tailscale up --hostname="$TS_HOSTNAME" --operator="$SUDO_USER"
echo "tailnet IPv4: $(tailscale ip -4)"

step "4/4 ufw"
ufw status verbose || true
command -v ibdev2netdev >/dev/null || { echo "ibdev2netdev missing — is this a DGX Spark?" >&2; exit 1; }
links=0
while read -r iface; do
  # NVIDIA Sync numbers each link /24 with .1 and .2 — the peer is the other one.
  addr="$(ip -4 -o addr show dev "$iface" | awk '{print $4}' | head -1)"
  [[ -n "$addr" ]] || { echo "  $iface is Up but has no IPv4 — skipped"; continue; }
  ip="${addr%/*}"; net="${ip%.*}"; last="${ip##*.}"
  case "$last" in
    1) peer="$net.2" ;;
    2) peer="$net.1" ;;
    *) echo "  $iface has $ip, not .1/.2 — set the rule by hand" >&2; continue ;;
  esac
  ping -c1 -W1 -I "$iface" "$peer" >/dev/null 2>&1 \
    || { echo "  $iface: peer $peer does not answer — rule not added" >&2; continue; }
  ufw allow in on "$iface" from "$peer" comment "openbeast: ConnectX-7 peer (NCCL / TensorFold ranks)"
  links=$((links + 1))
done < <(ibdev2netdev | awk '/\(Up\)/ {print $5}')
echo "  peer rules on $links link(s)"
if [[ "$SERVE" == yes ]]; then
  ufw allow in on tailscale0 to any port "$SERVE_PORT" proto tcp comment "openbeast: TensorFold API (keyed) on the tailnet"
fi
ufw status verbose

echo
echo "Done on $(hostname) as tailnet '$TS_HOSTNAME' ($(tailscale ip -4))."
