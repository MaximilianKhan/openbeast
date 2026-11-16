#!/bin/bash
# spark-fabric-mtu: stage MTU 9000 on the ConnectX-7 fabric netdevs, for the
# NEXT reboot. One DGX Spark per run, with sudo; run it on EVERY Spark.
# docs/SPARK_HYDRA.md "B4. Fabric MTU 9000: spark-fabric-mtu"
#
#   sudo scripts/backends/spark-fabric-mtu.sh [--stage]     # write the netplan drop-in, `netplan generate` only
#        scripts/backends/spark-fabric-mtu.sh --dry-run     # print the drop-in and the actions; change nothing
#        scripts/backends/spark-fabric-mtu.sh --status      # netdev MTU, RoCE active_mtu, staged or not
#        scripts/backends/spark-fabric-mtu.sh --verify PEER_IP [PEER_IP...]   # after the reboot
#   sudo scripts/backends/spark-fabric-mtu.sh --unstage     # remove the drop-in (rollback, next reboot)
#
# WHY. The fabric came up at netdev MTU 1500, which gives RoCE active_mtu 1024.
# MTU 9000 gives active_mtu 4096: a quarter of the packets (and per-packet
# work) for the bulk transfers, i.e. the long-prompt prefill all-gathers and
# the weight-sync copies. TP-2 decode all-reduces are small and gain little.
#
# WHAT. One file, /etc/netplan/99-zz-openbeast-fabric-mtu.yaml (0600 root),
# that sets `mtu: 9000` on each ConnectX netdev netplan already manages. It
# sorts after NVIDIA Sync's 99-nvidia-sync-cluster.yaml, and netplan merges a
# later file's keys into the same interface, so the addresses Sync wrote stay
# as they are and that file is never edited. Then `netplan generate` checks the
# merged config and writes the boot-time backend files. It NEVER runs `netplan
# apply`: the live links keep MTU 1500 until the reboot, so the two ends of the
# cable never disagree while a job runs. Before and after, the generated
# configs are compared: the same interfaces, the same backend, MTU 9000 on
# each, or the drop-in is taken back out.
#
# Never touched: a netdev that carries a default route (the LAN NIC enP7s7 is
# not a ConnectX port anyway), a ConnectX port netplan does not manage, and
# every other file in /etc/netplan.
#
# The reboot must happen on BOTH Sparks. One end at 9000 and the other at 1500
# drops every frame over 1500 bytes on that cable: ping and ssh still work,
# NCCL's bulk transfers stall or hang. Offline: no network is needed.
set -euo pipefail

# Test seams. SPARK_FABRIC_ROOT prefixes every path this reads or writes and
# is passed to netplan as --root-dir; it also lifts the root check, since
# nothing outside the prefix is written. The commands are overridable too.
R="${SPARK_FABRIC_ROOT:-}"
NETPLAN_DIR="${SPARK_FABRIC_NETPLAN_DIR:-$R/etc/netplan}"
NM_DIR="${SPARK_FABRIC_NM_DIR:-$R/run/NetworkManager/system-connections}"
NETWORKD_DIR="${SPARK_FABRIC_NETWORKD_DIR:-$R/run/systemd/network}"
IBDEV2NETDEV="${IBDEV2NETDEV:-ibdev2netdev}"
NETPLAN="${NETPLAN:-netplan}"
IP="${IP:-ip}"
IBV_DEVINFO="${IBV_DEVINFO:-ibv_devinfo}"
PING="${PING:-ping}"

DROPIN_NAME="99-zz-openbeast-fabric-mtu.yaml"
DROPIN="$NETPLAN_DIR/$DROPIN_NAME"
MTU=9000
ROCE_MTU=4096
PING_SIZE=$((MTU - 28))     # 8972: 9000 minus the IPv4 (20) and ICMP (8) headers
HERE="$(cd "$(dirname "$0")" && pwd)"

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "spark-fabric-mtu: $*" >&2; exit 1; }
warn() { echo "  !! $*" >&2; }
valid_name() { [[ "$1" =~ ^[A-Za-z0-9._-]{1,15}$ ]]; }
valid_ipv4() { [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; }

ACTION=stage DRY=0
PEERS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)    ACTION=stage ;;
    --status)   ACTION=status ;;
    --unstage)  ACTION=unstage ;;
    --dry-run)  DRY=1 ;;
    --verify)   ACTION=verify ;;
    -h|--help)  usage; exit 0 ;;
    -*)         echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    *)          [[ "$ACTION" == verify ]] || { echo "unexpected argument: $1 (see --help)" >&2; exit 2; }
                valid_ipv4 "$1" || { echo "--verify: '$1' is not an IPv4 address" >&2; exit 2; }
                PEERS+=("$1") ;;
  esac
  shift
done
[[ "$ACTION" != verify || ${#PEERS[@]} -gt 0 ]] || {
  echo "--verify needs the peer's fabric IPv4 on each rail: on the peer, ip -br addr | grep -E '^enp|^enP2'" >&2; exit 2; }

need_root() {
  [[ -n "$R" || $EUID -eq 0 ]] || die "run with sudo (it writes /etc/netplan and runs netplan generate)"
}

# ---------------------------------------------------------------- inputs
# "ibdev netdev state" for every ConnectX RoCE port, Up or Down.
#   rocep1s0f0 port 1 ==> enp1s0f0np0 (Up)
fabric_ports() {
  local out ib dev st
  out="$("$IBDEV2NETDEV" 2>/dev/null)" || true
  while read -r ib dev st; do
    [[ -n "$ib" ]] || continue
    if valid_name "$ib" && valid_name "$dev"; then echo "$ib $dev $st"
    else warn "ignoring an odd ibdev2netdev line: $ib $dev"; fi
  done < <(awk '$4 == "==>" {st = $6; gsub(/[()]/, "", st); print $1, $5, st}' <<< "$out")
}

default_route_devs() {   # every netdev that carries a default route (v4 or v6, multipath too)
  { "$IP" -4 route show default 2>/dev/null || true; "$IP" -6 route show default 2>/dev/null || true; } \
    | awk '{for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1)}' | sort -u
}

link_mtu() { "$IP" -o link show dev "$1" 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "mtu") {print $(i + 1); exit}}' || true; }
active_mtu() { "$IBV_DEVINFO" -d "$1" 2>/dev/null | awk '$1 == "active_mtu:" {print $2; exit}' || true; }

# Which backend netplan rendered DEV's boot config for: NetworkManager,
# networkd, or none (netplan does not manage it).
gen_backend() {
  if [[ -e "$NM_DIR/netplan-$1.nmconnection" ]]; then echo NetworkManager
  elif [[ -e "$NETWORKD_DIR/10-netplan-$1.network" ]]; then echo networkd
  else echo none; fi
}
# The MTU in DEV's generated boot config, empty when it sets none.
gen_mtu() {
  local f
  for f in "$NM_DIR/netplan-$1.nmconnection" "$NETWORKD_DIR/10-netplan-$1.network" "$NETWORKD_DIR/10-netplan-$1.link"; do
    [[ -r "$f" ]] || continue
    sed -n -e 's/^mtu=\([0-9]\{1,5\}\)$/\1/p' -e 's/^MTUBytes=\([0-9]\{1,5\}\)$/\1/p' "$f" | head -1
  done | head -1
}
# Every interface netplan renders, as backend:id, sorted (an extra id here
# would mean the drop-in created a NEW interface instead of amending Sync's).
gen_ids() {
  { { ls "$NM_DIR" 2>/dev/null || true; } | sed -n 's/^netplan-\(.*\)\.nmconnection$/NetworkManager:\1/p'
    { ls "$NETWORKD_DIR" 2>/dev/null || true; } | sed -n 's/^10-netplan-\(.*\)\.network$/networkd:\1/p'; } | sort -u
}

run_generate() {
  if [[ -n "$R" ]]; then "$NETPLAN" generate --root-dir "$R"; else "$NETPLAN" generate; fi
}

render() {   # render DEV...
  cat <<EOF
# Written by openbeast scripts/backends/spark-fabric-mtu.sh --stage
# (docs/SPARK_HYDRA.md "B4. Fabric MTU 9000"). MTU $MTU on the ConnectX-7
# fabric netdevs, merged over NVIDIA Sync's 99-nvidia-sync-cluster.yaml.
# Takes effect on the next reboot, which must happen on EVERY Spark.
# Roll back: sudo scripts/backends/spark-fabric-mtu.sh --unstage, then reboot
# (or delete this file from a console and reboot).
network:
  version: 2
  ethernets:
EOF
  local d
  for d in "$@"; do printf '    %s:\n      mtu: %s\n' "$d" "$MTU"; done
}

# The devices to stage: every ConnectX netdev netplan manages, minus any that
# carries a default route. Dies when none is left.
stage_devs() {
  local ports defroute ib dev st backend n=0
  ports="$(fabric_ports)"
  [[ -n "$ports" ]] || die "ibdev2netdev lists no ConnectX RoCE port — is this a DGX Spark? Nothing staged."
  defroute="$(default_route_devs)"
  while read -r ib dev st; do
    if grep -qxF -- "$dev" <<< "$defroute"; then
      warn "$dev ($ib) carries a default route — it is not the point-to-point fabric; left alone"; continue
    fi
    backend="$(gen_backend "$dev")"
    if [[ "$backend" == none ]]; then
      warn "$dev ($ib, $st) has no netplan-generated config — netplan does not manage it; left alone"; continue
    fi
    echo "$dev"; n=$((n + 1))
  done <<< "$ports"
  [[ $n -gt 0 ]] || die "no ConnectX netdev is left to stage (see the warnings above). Nothing staged."
}

# ---------------------------------------------------------------- stage
do_stage() {
  [[ $DRY -eq 1 ]] || need_root
  local devs=() d yaml before_ids after_ids prev="" bad=0
  while read -r d; do devs+=("$d"); done < <(stage_devs)
  [[ ${#devs[@]} -gt 0 ]] || exit 1
  yaml="$(render "${devs[@]}")"

  if [[ $DRY -eq 1 ]]; then
    echo "== dry run: nothing is written"
    echo "would write $DROPIN (0600 root):"
    echo "$yaml" | sed 's/^/    /'
    echo "would run:   $NETPLAN generate${R:+ --root-dir $R}    (never netplan apply)"
    echo "would check: the generated configs keep the same interfaces and backend, with mtu=$MTU on: ${devs[*]}"
    echo "then:        reboot BOTH Sparks; afterwards $0 --verify <peer fabric IP on each rail>"
    exit 0
  fi

  echo "== staging MTU $MTU on: ${devs[*]}"
  local was
  for d in "${devs[@]}"; do
    was="$(gen_mtu "$d")"
    echo "   $d: backend $(gen_backend "$d"), boot config mtu ${was:-default (1500)}, live mtu $(link_mtu "$d")"
  done
  before_ids="$(gen_ids)"
  if [[ -e "$DROPIN" ]]; then prev="$(mktemp)"; cp -p "$DROPIN" "$prev"; fi

  # Restore the previous drop-in (or none) and re-generate, then die.
  rollback() {
    if [[ -n "$prev" ]]; then mv -f "$prev" "$DROPIN"; else rm -f "$DROPIN"; fi
    run_generate >/dev/null 2>&1 || warn "netplan generate failed during the rollback too — check $NETPLAN_DIR by hand"
    die "$1 — the drop-in was taken back out; the boot config is as it was."
  }

  mkdir -p "$NETPLAN_DIR"
  local tmp="$NETPLAN_DIR/.$DROPIN_NAME.tmp"   # not *.yaml, so netplan never reads a half-written file
  ( umask 077; printf '%s\n' "$yaml" > "$tmp" )
  chmod 0600 "$tmp"
  [[ $EUID -ne 0 ]] || chown 0:0 "$tmp"
  mv -f "$tmp" "$DROPIN"
  echo "   wrote $DROPIN (0600)"

  echo "== $NETPLAN generate (validates the merged config; the live links are not touched)"
  run_generate || rollback "netplan generate rejected the merged config"

  after_ids="$(gen_ids)"
  [[ "$before_ids" == "$after_ids" ]] || rollback "the generated interfaces changed (before: $(tr '\n' ' ' <<< "$before_ids"); after: $(tr '\n' ' ' <<< "$after_ids")) — the drop-in did not merge into the existing definitions"
  for d in "${devs[@]}"; do
    if [[ "$(gen_mtu "$d")" == "$MTU" ]]; then echo "   $d: boot config now mtu $MTU ($(gen_backend "$d"))"
    else echo "   $d: boot config does NOT carry mtu $MTU" >&2; bad=1; fi
  done
  [[ $bad -eq 0 ]] || rollback "netplan generate did not render mtu $MTU for every fabric netdev"
  [[ -z "$prev" ]] || rm -f "$prev"

  cat <<EOF

STAGED on $(hostname). Nothing changed on the live links (still MTU $(link_mtu "${devs[0]}")).
It takes effect on the NEXT REBOOT, and that reboot must happen on BOTH Sparks:
with one end of the cable at $MTU and the other at 1500, every frame over 1500
bytes is dropped — ping and ssh still work, NCCL's bulk transfers stall or hang.
  1. run this --stage on the other Spark too
  2. reboot both (sudo reboot); spark-hydra-inference comes back by itself
  3. $HERE/spark-fabric-mtu.sh --verify <peer fabric IP on each rail>
Roll back before the reboot (or after it, then reboot again):
  sudo $HERE/spark-fabric-mtu.sh --unstage
EOF
}

# ---------------------------------------------------------------- unstage
do_unstage() {
  [[ $DRY -eq 1 ]] || need_root
  if [[ ! -e "$DROPIN" ]]; then
    echo "nothing staged ($DROPIN does not exist)"; exit 0
  fi
  if [[ $DRY -eq 1 ]]; then
    echo "== dry run: would remove $DROPIN and run $NETPLAN generate${R:+ --root-dir $R}"; exit 0
  fi
  rm -f "$DROPIN"
  echo "removed $DROPIN"
  run_generate || die "netplan generate failed after removing the drop-in — check $NETPLAN_DIR"
  echo "netplan generate: ok"
  local ib dev st live
  while read -r ib dev st; do
    [[ -n "$dev" ]] || continue
    live="$(link_mtu "$dev")"
    if [[ "$live" == "$MTU" ]]; then
      warn "$dev is live at MTU $MTU: it returns to the default on the next reboot — reboot BOTH Sparks (or unstage on both first)"
    fi
  done < <(fabric_ports)
  echo "UNSTAGED. The fabric returns to the default MTU on the next reboot of BOTH Sparks."
}

# ---------------------------------------------------------------- status
do_status() {
  local ports staged=no ib dev st live roce boot inf
  ports="$(fabric_ports)"
  [[ -e "$DROPIN" ]] && staged=yes
  echo "drop-in: $DROPIN — $( [[ $staged == yes ]] && echo staged || echo 'not staged')"
  [[ -n "$ports" ]] || { echo "ibdev2netdev lists no ConnectX RoCE port"; return 0; }
  printf '  %-15s %-14s %-5s %-9s %-11s %-13s %s\n' NETDEV IBDEV STATE "MTU(now)" active_mtu "boot config" "in drop-in"
  while read -r ib dev st; do
    live="$(link_mtu "$dev")"; roce="$(active_mtu "$ib")"
    boot="$(gen_mtu "$dev")"
    if [[ "$(gen_backend "$dev")" == none ]]; then boot="unmanaged"
    elif [[ -z "$boot" ]]; then
      # 0600 files: without root an absent value may only mean unreadable.
      if [[ -r "$NM_DIR/netplan-$dev.nmconnection" || -r "$NETWORKD_DIR/10-netplan-$dev.network" ]]; then boot="default"; else boot="(sudo)"; fi
    fi
    inf=no
    if [[ $staged == yes ]]; then
      if [[ -r "$DROPIN" ]]; then
        if [[ "$(grep -c "^    $dev:\$" "$DROPIN" || true)" -gt 0 ]]; then inf=yes; fi
      else inf="(sudo)"; fi
    fi
    printf '  %-15s %-14s %-5s %-9s %-11s %-13s %s\n' "$dev" "$ib" "$st" "${live:-?}" "${roce:-?}" "$boot" "$inf"
  done <<< "$ports"
  if [[ $staged == yes ]]; then
    echo "staged: MTU $MTU takes effect on the next reboot of BOTH Sparks (then --verify)"
  else
    echo "not staged: sudo $HERE/spark-fabric-mtu.sh --stage"
  fi
}

# ---------------------------------------------------------------- verify
do_verify() {
  local ports defroute ib dev st live roce fail=0 up=() peer pdev covered c
  ports="$(fabric_ports)"
  [[ -n "$ports" ]] || die "ibdev2netdev lists no ConnectX RoCE port"
  defroute="$(default_route_devs)"
  echo "== the rails on $(hostname)"
  while read -r ib dev st; do
    [[ "$st" == Up ]] || { echo "   $dev ($ib): $st — skipped"; continue; }
    if grep -qxF -- "$dev" <<< "$defroute"; then echo "   $dev ($ib): carries the default route — not fabric, skipped"; continue; fi
    up+=("$dev")
    live="$(link_mtu "$dev")"; roce="$(active_mtu "$ib")"
    if [[ "$live" == "$MTU" ]]; then echo "   PASS $dev netdev mtu $live"
    else echo "   FAIL $dev netdev mtu ${live:-?}, want $MTU (staged on this Spark, and rebooted?)"; fail=1; fi
    if [[ "$roce" == "$ROCE_MTU" ]]; then echo "   PASS $ib active_mtu $roce"
    else echo "   FAIL $ib active_mtu ${roce:-?}, want $ROCE_MTU"; fail=1; fi
  done <<< "$ports"
  [[ ${#up[@]} -gt 0 ]] || { echo "   FAIL no ConnectX rail is Up"; fail=1; }

  echo "== $PING_SIZE-byte ping, do-not-fragment (a $MTU-byte frame), to the peer on each rail"
  covered=""
  for peer in "${PEERS[@]}"; do
    pdev="$("$IP" -o route get "$peer" 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}' || true)"
    c=0; for dev in "${up[@]+"${up[@]}"}"; do [[ "$dev" == "$pdev" ]] && c=1; done
    if [[ $c -eq 0 ]]; then
      echo "   FAIL $peer routes via '${pdev:-nothing}', not an Up fabric rail"; fail=1; continue
    fi
    covered="$covered $pdev"
    if "$PING" -M "do" -s "$PING_SIZE" -c 3 -W 2 -I "$pdev" "$peer" >/dev/null 2>&1; then
      echo "   PASS $peer over $pdev"
    else
      echo "   FAIL $peer over $pdev: no reply to a $MTU-byte frame (the peer still at 1500? reboot it too)"; fail=1
    fi
  done
  for dev in "${up[@]+"${up[@]}"}"; do
    [[ " $covered " == *" $dev "* ]] && continue
    echo "   FAIL no peer IP given on $dev ($( "$IP" -4 -o addr show dev "$dev" 2>/dev/null | awk '{print $4}' | head -1 || true)): pass the peer's address on that rail too"
    fail=1
  done

  if [[ $fail -eq 0 ]]; then
    echo "VERIFIED: MTU $MTU / RoCE $ROCE_MTU on every rail, jumbo frames reach the peer."
  else
    echo "NOT VERIFIED. Roll back: sudo $HERE/spark-fabric-mtu.sh --unstage on BOTH, then reboot both." >&2
    exit 1
  fi
}

case "$ACTION" in
  stage)   do_stage ;;
  unstage) do_unstage ;;
  status)  do_status ;;
  verify)  do_verify ;;
esac
