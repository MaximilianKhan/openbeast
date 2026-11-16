#!/bin/bash
# Root-only setup for ONE DGX Spark of the TensorFold pair. Run it once per
# Spark, as yourself through sudo (it needs SUDO_USER). Idempotent.
# docs/DGX_SPARK_PLAN.md §9
#
#   sudo scripts/backends/spark-root-setup.sh spark-1   # Spark 1: rank 0 + command center
#   sudo scripts/backends/spark-root-setup.sh spark-2   # Spark 2: rank 1, no services
#        scripts/backends/spark-root-setup.sh --check-debs DIR   # verify staged .debs (no root)
#
# What it does, and nothing else:
#   1. libnccl2 pinned to a CUDA 13.0 build and held. The native TensorFold
#      binary dlopens libnccl.so.2 for its CUDA ranks (`tensorfold
#      capabilities --json` lists it as found:false without it). The driver is
#      R580 = CUDA 13.0, so the +cuda13.0 build, not apt's newest +cuda13.5.
#   2. loginctl enable-linger for SUDO_USER, so the rank services
#      (systemd --user) run without a login session and survive logout.
#   3. Tailscale from its signed apt repo — the repo key is checked against
#      the fingerprint pinned below before apt trusts it, and the sources line
#      is written here, not fetched — then `tailscale up` with the given
#      tailnet name (a login URL is printed on first run). --operator lets
#      SUDO_USER run `tailscale serve` without sudo.
#   4. ufw allow rules for each ConnectX-7 link that is Up (NCCL rendezvous,
#      NCCL's own sockets and RoCE), from that link's own private subnet, for
#      the day ufw is enabled. ufw is INSTALLED BUT DISABLED on DGX OS, and
#      this script does not enable it (its default deny would also drop LAN
#      ssh); with ufw inactive those rules filter nothing, and it says so.
#      Even enabled, ufw does not cover the tailnet: Tailscale's ts-input
#      chain accepts tailscale0 traffic before ufw's rules run. What actually
#      keeps the engines' UNAUTHENTICATED rendezvous ports (29551, 29583,
#      29501) off the LAN and the tailnet is spark-guard.sh (an nftables
#      table that runs first); --guard runs it at the end.
#      --serve adds a ufw rule for the TensorFold port on tailscale0, only for
#      a keyed engine that must be reached directly. By default inference
#      listens on loopback behind the pair's (spark-hydra's) beast-gate.
#      Nothing else in ufw is touched; status is printed before and after.
#
# OFFLINE: --debs DIR installs libnccl2 and tailscale from .deb files staged
# there beforehand. On a Spark with internet (apt checked the repo signature
# when it downloaded them):
#   apt-get download libnccl2=2.28.9-1+cuda13.0 tailscale=1.104.7
# Both files must be exactly those versions, and each must match the sha256
# pinned below (from the signed repo index: `apt-cache show PKG=VER`) — a .deb
# given by path skips apt's signature check, so the pin is the only one.
# `--check-debs DIR` runs the same checks without root, before carrying it.
# --no-tailscale skips step 3 (a box with no tailnet to join).
#
# It does not change netplan (NVIDIA Sync owns 99-nvidia-sync-cluster.yaml),
# the MTU, the driver or Docker.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

NCCL_VERSION="2.28.9-1+cuda13.0"
# sha256 of libnccl2_2.28.9-1+cuda13.0_arm64.deb (NVIDIA's CUDA repo index, SHA256 field)
NCCL_DEB_SHA256="ee18cd8fd0ef1820979ab1613b6496e5e97f614d1212a7f47a780ce9868bcd2e"
# The offline tailscale .deb: bumping it means a new version AND its sha256 from
# `apt-cache show tailscale=<V> | grep SHA256` on a box with Tailscale's repo.
TS_DEB_VERSION="1.104.7"
TS_DEB_SHA256="eed8d2dd9cdc17b2c49581e3c9b5e3a951f3d8ef7202a42fbaf351ac6ec5039f"
# Tailscale's package signing key (primary key fingerprint), "Tailscale Inc.
# (Package repository signing key)", the key pkgs.tailscale.com serves.
TS_KEY_FPR="2596A99EAAB33821893C0A79458CA832957F5868"
TS_KEYRING=/usr/share/keyrings/tailscale-archive-keyring.gpg
SERVE_PORT=8000
SERVE=no

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "$*" >&2; exit 1; }
sha256_of() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | awk '{print $1}'; }

# deb_path DIR PKG VERSION: the one staged file for exactly PKG_VERSION_arm64
# (apt-get download writes '+' as-is or as %2b), else empty.
deb_path() {
  local dir="$1" pkg="$2" ver="$3" f
  for f in "$dir/${pkg}_${ver}_arm64.deb" "$dir/${pkg}_${ver//+/%2b}_arm64.deb" "$dir/${pkg}_${ver//+/%2B}_arm64.deb"; do
    [[ -f "$f" ]] && { echo "$f"; return 0; }
  done
  return 0
}

# check_deb FILE PKG VERSION SHA256: refuse anything but the pinned bytes.
check_deb() {
  local f="$1" pkg="$2" ver="$3" want="$4" got
  got="$(sha256_of "$f")"
  [[ "$got" == "$want" ]] || die "REFUSED $f: sha256 $got, pinned $want — not the $pkg $ver the signed repo published"
  if command -v dpkg-deb >/dev/null; then
    [[ "$(dpkg-deb -f "$f" Package)" == "$pkg" && "$(dpkg-deb -f "$f" Version)" == "$ver" ]] \
      || die "REFUSED $f: its control file is not $pkg $ver"
  fi
  echo "  verified $f ($pkg $ver, sha256 pinned)"
}

# staged_deb DIR PKG VERSION SHA256 -> path (verified), or dies naming what to stage.
staged_deb() {
  local dir="$1" pkg="$2" ver="$3" sha="$4" f other
  f="$(deb_path "$dir" "$pkg" "$ver")"
  if [[ -z "$f" ]]; then
    other="$(cd "$dir" 2>/dev/null && ls "${pkg}"_*_arm64.deb 2>/dev/null | head -3 | tr '\n' ' ' || true)"
    die "no ${pkg}_${ver}_arm64.deb in $dir${other:+ (found: ${other}- only the pinned version is installed)}. Stage it with: apt-get download $pkg=$ver"
  fi
  check_deb "$f" "$pkg" "$ver" "$sha" >&2
  echo "$f"
}

# ts_keyring_ok FILE: exactly one primary key, and it is Tailscale's.
ts_keyring_ok() {
  local f="$1" gh fprs
  command -v gpg >/dev/null || die "gpg is missing — cannot check Tailscale's repo key"
  gh="$(mktemp -d)"
  fprs="$(GNUPGHOME="$gh" gpg --batch --show-keys --with-colons "$f" 2>/dev/null \
           | awk -F: '$1 == "pub" {want = 1; next} $1 == "fpr" && want {print $10; want = 0}')" || true
  rm -rf "$gh"
  [[ "$fprs" == "$TS_KEY_FPR" ]] \
    || die "REFUSED $f: its primary key(s) [${fprs//$'\n'/ }] are not Tailscale's pinned $TS_KEY_FPR"
  echo "  Tailscale repo key verified ($TS_KEY_FPR)"
}

TS_HOSTNAME="" DEBS="" NO_TS=0 GUARD=0 CHECK_DEBS="" CHECK_KEYRING=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --serve)    SERVE=yes ;;
    --port)     SERVE_PORT="${2:?--port needs a value}"; shift ;;
    --debs)     DEBS="${2:?--debs needs a directory}"; shift ;;
    --check-debs)    CHECK_DEBS="${2:?--check-debs needs a directory}"; shift ;;
    --check-keyring) CHECK_KEYRING="${2:?--check-keyring needs a file}"; shift ;;
    --no-tailscale) NO_TS=1 ;;
    --guard)    GUARD=1 ;;
    -h|--help)  usage; exit 0 ;;
    -*)         echo "unknown option: $1" >&2; exit 2 ;;
    *)          TS_HOSTNAME="$1" ;;
  esac
  shift
done

# Verification-only modes: no root, nothing installed.
if [[ -n "$CHECK_DEBS" ]]; then
  staged_deb "$CHECK_DEBS" libnccl2 "$NCCL_VERSION" "$NCCL_DEB_SHA256" >/dev/null
  [[ $NO_TS -eq 1 ]] || staged_deb "$CHECK_DEBS" tailscale "$TS_DEB_VERSION" "$TS_DEB_SHA256" >/dev/null
  echo "OK: $CHECK_DEBS holds the pinned .debs"
  exit 0
fi
if [[ -n "$CHECK_KEYRING" ]]; then
  ts_keyring_ok "$CHECK_KEYRING"
  exit 0
fi

[[ $EUID -eq 0 ]] || { echo "Run with sudo (it needs root and SUDO_USER)." >&2; exit 1; }
[[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]] || { echo "Run through sudo from your own account." >&2; exit 1; }
[[ "$TS_HOSTNAME" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { usage >&2; exit 2; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# Offline: verify every staged .deb up front, before anything is installed.
NCCL_DEB="" TS_DEB=""
if [[ -n "$DEBS" ]]; then
  NCCL_DEB="$(staged_deb "$DEBS" libnccl2 "$NCCL_VERSION" "$NCCL_DEB_SHA256")"
  if [[ $NO_TS -eq 0 ]] && ! command -v tailscale >/dev/null; then
    TS_DEB="$(staged_deb "$DEBS" tailscale "$TS_DEB_VERSION" "$TS_DEB_SHA256")"
  fi
fi

step "1/4 libnccl2 $NCCL_VERSION"
if [[ "$(dpkg-query -W -f='${Version}' libnccl2 2>/dev/null || true)" == "$NCCL_VERSION" ]]; then
  echo "already installed"
else
  apt-mark unhold libnccl2 >/dev/null 2>&1 || true
  if [[ -n "$DEBS" ]]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --allow-downgrades --no-download "$NCCL_DEB"
  else
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --allow-downgrades "libnccl2=$NCCL_VERSION"
  fi
fi
apt-mark hold libnccl2 >/dev/null
ldconfig -p | grep -q 'libnccl.so.2 ' && echo "libnccl.so.2 visible to the loader"

step "2/4 linger for $SUDO_USER"
loginctl enable-linger "$SUDO_USER"
loginctl show-user "$SUDO_USER" -p Linger

step "3/4 Tailscale as '$TS_HOSTNAME'"
if [[ $NO_TS -eq 1 ]]; then
  echo "skipped (--no-tailscale)"
elif [[ -n "$TS_DEB" ]]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-download "$TS_DEB"
elif ! command -v tailscale >/dev/null; then
  # Tailscale's signed apt repo for this release. Unlike install.sh, the key
  # is checked against the pinned fingerprint BEFORE apt trusts it, and the
  # sources line (repo URL + signed-by) is ours, not a fetched file.
  . /etc/os-release
  [[ "${VERSION_CODENAME:-}" =~ ^[a-z]+$ ]] || die "unexpected VERSION_CODENAME '${VERSION_CODENAME:-}'"
  key="$(mktemp)"
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.noarmor.gpg" -o "$key"
  ( ts_keyring_ok "$key" ) || { rm -f "$key"; exit 1; }
  install -m 0644 "$key" "$TS_KEYRING"
  rm -f "$key"
  echo "deb [signed-by=$TS_KEYRING] https://pkgs.tailscale.com/stable/ubuntu ${VERSION_CODENAME} main" \
    > /etc/apt/sources.list.d/tailscale.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tailscale
fi
if [[ $NO_TS -eq 0 ]]; then
  systemctl enable --now tailscaled
  # Joining needs the coordination server; offline, the box keeps working on
  # the ConnectX link and the LAN, and joins on the first run with a route.
  tailscale up --hostname="$TS_HOSTNAME" --operator="$SUDO_USER" \
    || echo "  tailscale up failed (offline?) — re-run this script once there is a route" >&2
  echo "tailnet IPv4: $(tailscale ip -4 2>/dev/null || echo none)"
fi

step "4/4 ufw (fabric allow rules) and exposure"
UFW_STATE="not installed"
if command -v ufw >/dev/null; then
  UFW_STATE="$(ufw status 2>/dev/null | head -1 || true)"
  ufw status verbose || true
fi
command -v ibdev2netdev >/dev/null || { echo "ibdev2netdev missing — is this a DGX Spark?" >&2; exit 1; }
links=0
if command -v ufw >/dev/null; then
  while read -r iface; do
    addr="$(ip -4 -o addr show dev "$iface" | awk '{print $4}' | head -1)"
    [[ -n "$addr" ]] || { echo "  $iface is Up but has no IPv4 — skipped"; continue; }
    # The link's own network (e.g. 10.100.216.0/24). A /16 or wider on a
    # ConnectX port is not a private point-to-point or fabric subnet: refuse it
    # rather than open a big range.
    net="$(python3 -c 'import ipaddress,sys; n=ipaddress.ip_interface(sys.argv[1]).network; print(n if n.prefixlen >= 20 and n.is_private else "")' "$addr")"
    [[ -n "$net" ]] || { echo "  $iface has $addr — not a private /20-or-narrower fabric subnet; rule not added" >&2; continue; }
    ufw allow in on "$iface" from "$net" comment "openbeast: ConnectX-7 fabric $net (NCCL / TensorFold ranks)"
    links=$((links + 1))
  done < <(ibdev2netdev | awk '/\(Up\)/ {print $5}')
  echo "  ufw fabric rules on $links link(s)"
  if [[ "$SERVE" == yes ]]; then
    ufw allow in on tailscale0 to any port "$SERVE_PORT" proto tcp comment "openbeast: TensorFold API (keyed) on the tailnet"
  fi
  ufw status verbose || true
fi
if [[ "$UFW_STATE" != "Status: active" ]]; then
  serve_note=""
  [[ "$SERVE" == yes ]] && serve_note=$'\n  !! (--serve\'s tailnet rule is moot: that port was never filtered.)'
  cat >&2 <<EOF

  !! ufw is ${UFW_STATE#Status: } — the ufw rules above are recorded but FILTER NOTHING.
  !! Every wildcard listener on this Spark is reachable from the LAN and the
  !! tailnet, including the engines' UNAUTHENTICATED rendezvous ports
  !! (TensorFold 29551/29552, native 29583/29584, vLLM 29501/29502).$serve_note
  !! This script does not enable ufw (its default deny would also drop LAN ssh),
  !! and ufw would not cover the tailnet anyway (Tailscale's ts-input runs first).
  !! Close the engine ports, keeping the fabric, ssh and tailscale serve open:
  !!   sudo $HERE/spark-guard.sh            (or re-run this with --guard)
EOF
fi
if [[ $GUARD -eq 1 ]]; then
  step "spark-guard"
  "$HERE/spark-guard.sh"
fi

echo
echo "Done on $(hostname) as tailnet '$TS_HOSTNAME' ($(tailscale ip -4 2>/dev/null || echo 'not joined'))".
