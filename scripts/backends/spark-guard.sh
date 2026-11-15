#!/bin/bash
# spark-guard: keep the inference engines' UNAUTHENTICATED control ports on
# the ConnectX-7 fabric. One DGX Spark per run, with sudo. Idempotent.
# docs/SPARK_HYDRA.md "Firewall: spark-guard"
#
#   sudo scripts/backends/spark-guard.sh              # install / refresh the guard (persists across reboot)
#   sudo scripts/backends/spark-guard.sh --status     # what is loaded, what listens, ssh posture
#   sudo scripts/backends/spark-guard.sh --remove     # undo: table, unit and rules file gone
#        scripts/backends/spark-guard.sh --render     # print the ruleset it would load (no root)
#   sudo scripts/backends/spark-guard.sh --harden-ssh [--yes]   # OFF by default — read the warning first
#   sudo scripts/backends/spark-guard.sh --unharden-ssh         # undo --harden-ssh
#
#   --port N      also guard TCP port N (repeatable)
#   --iface IF    also treat IF as fabric (repeatable; default: every netdev ibdev2netdev lists)
#   --no-fabric   a single Spark with no ConnectX peer: the ports become loopback-only
#
# WHY. TensorFold's TCPStore (29551, +1), native TensorFold's NCCL_COMM_ID
# (29583, +1) and vLLM's torch.distributed store (29501) have no
# authentication, and rank 0 binds them on EVERY interface (`*:29551`). The
# NCCL bootstrap listeners bind the fabric address on ephemeral ports. ufw is
# installed but disabled on DGX OS, and even with ufw on, Tailscale's own
# netfilter chain (ts-input) accepts tailscale0 traffic before ufw's rules —
# so the tailnet (every enrolled laptop and phone) and the LAN could reach the
# live job's control plane. spark-guard drops, before any other filter runs:
#   * TCP to the guarded ports unless it arrives on loopback or a fabric netdev
#   * TCP to this Spark's fabric IPv4 addresses (the NCCL bootstrap listeners)
#     unless it arrives on loopback or a fabric netdev
# Everything else passes through untouched to ufw / Tailscale / Docker as before.
#
# HOW. One nftables table of its own, `inet openbeast_guard`, with an input
# base chain at priority filter-10, i.e. ahead of iptables-nft (ufw, Docker)
# and Tailscale (ts-input), which hook input at priority filter (0). A drop in
# any base chain is final; an accept here is not, so this table can only ever
# take traffic away. It never flushes the ruleset: Ubuntu's nftables.service
# (`/etc/nftables.conf` starts with `flush ruleset`) would wipe Docker's and
# Tailscale's rules, so it is NOT used. Persistence is a oneshot unit of its
# own that loads /etc/openbeast/spark-guard.nft before network-pre.target.
#
# Not affected: RoCE (RDMA bypasses netfilter), NCCL's TCP bootstrap and the
# rendezvous on the fabric, `tailscale serve` (:443, :8443-8447, :8889), ssh
# (22) from the LAN and the tailnet, Docker networking (forwarded traffic never
# hits input), and anything on loopback. Re-run after the fabric's addresses
# change (NVIDIA Sync re-addressing, a new switch).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# SPARK_GUARD_ROOT: a path prefix for every file this writes (tests only; it
# also lifts the root check, since nothing outside the prefix is written).
R="${SPARK_GUARD_ROOT:-}"
TABLE="openbeast_guard"
RULES="$R/etc/openbeast/spark-guard.nft"
UNIT_NAME="openbeast-spark-guard.service"
UNIT="$R/etc/systemd/system/$UNIT_NAME"
SSHD_DROPIN="$R/etc/ssh/sshd_config.d/00-openbeast-guard.conf"
SPARK_ENV="${SPARK_ENV:-$HERE/spark.env}"

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }
valid_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )); }
valid_iface() { [[ "$1" =~ ^[A-Za-z0-9._-]{1,15}$ ]]; }

ACTION=apply YES=0 NO_FABRIC=0
EXTRA_PORTS=() EXTRA_IFACES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)        ACTION=apply ;;
    --status)       ACTION=status ;;
    --remove)       ACTION=remove ;;
    --render)       ACTION=render ;;
    --harden-ssh)   ACTION=harden-ssh ;;
    --unharden-ssh) ACTION=unharden-ssh ;;
    --yes)          YES=1 ;;
    --no-fabric)    NO_FABRIC=1 ;;
    --port)         EXTRA_PORTS+=("${2:?--port needs a value}"); shift
                    valid_port "$1" || { echo "--port $1 is not a TCP port" >&2; exit 2; } ;;
    --iface)        EXTRA_IFACES+=("${2:?--iface needs a value}"); shift ;;
    -h|--help)      usage; exit 0 ;;
    *)              echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "spark-guard: $*" >&2; exit 1; }
need_root() {
  [[ -n "$R" || $EUID -eq 0 ]] || die "run with sudo (it loads nftables rules and writes /etc)"
}

# A port KEY from spark.env (parsed, never sourced): digits only, else empty.
env_port() {
  [[ -f "$SPARK_ENV" ]] || return 0
  local v
  v="$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*['\"]\{0,1\}\([0-9]\{1,5\}\)['\"]\{0,1\}[[:space:]]*\(#.*\)\{0,1\}$/\1/p" "$SPARK_ENV" | tail -1)"
  valid_port "${v:-x}" && echo "$v" || true
}

# ---------------------------------------------------------------- inputs
ports() {   # one port per line, sorted, unique
  local p list=(29501 29551 29552 29583 29584)
  for p in "$(env_port VLLM_MASTER_PORT)" "$(env_port TENSORFOLD_MASTER_PORT)"; do
    [[ -n "$p" ]] && list+=("$p" "$((p + 1))")
  done
  for p in "${EXTRA_PORTS[@]+"${EXTRA_PORTS[@]}"}"; do list+=("$p"); done
  printf '%s\n' "${list[@]}" | awk '$1 <= 65535' | sort -n -u
}

fabric_ifaces() {   # every netdev of a ConnectX port, Up or Down (a rail may come up later)
  local i
  if command -v ibdev2netdev >/dev/null 2>&1; then
    ibdev2netdev 2>/dev/null | awk '$4 == "==>" {print $5}'
  fi
  for i in "${EXTRA_IFACES[@]+"${EXTRA_IFACES[@]}"}"; do echo "$i"; done
}

fabric_addrs() {   # the fabric netdevs' IPv4 addresses (no prefix)
  local i
  for i in "$@"; do
    ip -4 -o addr show dev "$i" 2>/dev/null | awk '{print $4}' | cut -d/ -f1
  done | grep -E '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' | sort -u || true
}

# nft set body from lines: "a", "b"   (quoted for names, bare for numbers)
nft_set() {   # nft_set quote|bare <items...>
  local mode="$1" out="" x; shift
  for x in "$@"; do
    [[ "$mode" == quote ]] && x="\"$x\""
    out+="${out:+, }$x"
  done
  printf '%s' "$out"
}

# Collapse sorted ports into nft ranges: 29551 29552 -> 29551-29552.
port_ranges() {
  awk 'NR == 1 {s = e = $1; next}
       $1 == e + 1 {e = $1; next}
       {print (s == e ? s : s "-" e); s = e = $1}
       END {if (NR) print (s == e ? s : s "-" e)}'
}

render() {   # word lists are space-separated strings (bash 3.2 has no mapfile)
  local ifaces="" addrs="" ports_r="" i
  while IFS= read -r i; do
    [[ -n "$i" ]] || continue
    valid_iface "$i" || die "interface name '$i' is not a netdev name"
    ifaces+="${ifaces:+ }$i"
  done < <(fabric_ifaces | awk '!seen[$0]++')
  if [[ -z "$ifaces" && $NO_FABRIC -eq 0 ]]; then
    die "no ConnectX fabric interface found (ibdev2netdev lists none). On a single Spark pass --no-fabric (the ports become loopback-only); otherwise name the fabric with --iface IF"
  fi
  [[ $NO_FABRIC -eq 1 ]] && ifaces=""
  ports_r="$(ports | port_ranges | tr '\n' ' ')"; ports_r="${ports_r% }"
  # shellcheck disable=SC2086  # splitting the space-separated lists is the point
  if [[ -n "$ifaces" ]]; then addrs="$(fabric_addrs $ifaces | tr '\n' ' ')"; addrs="${addrs% }"; fi
  cat <<EOF
#!/usr/sbin/nft -f
# Generated by scripts/backends/spark-guard.sh on $(hostname 2>/dev/null || echo '?') — do not edit; re-run it.
# Loaded by $UNIT_NAME. Only this table is touched: the ruleset is never flushed.
# fabric: ${ifaces:-none}   fabric IPv4: ${addrs:-none}   guarded TCP: $ports_r
table inet $TABLE
delete table inet $TABLE
table inet $TABLE {
	chain input {
		# ahead of iptables-nft (ufw, Docker) and Tailscale's ts-input, which hook at priority filter
		type filter hook input priority filter - 10; policy accept;
		iifname "lo" accept
EOF
  # shellcheck disable=SC2086
  if [[ -n "$ifaces" ]]; then
    printf '\t\tiifname { %s } accept comment "ConnectX-7 fabric"\n' "$(nft_set quote $ifaces)"
  fi
  # shellcheck disable=SC2086
  printf '\t\ttcp dport { %s } counter drop comment "engine rendezvous: fabric and loopback only"\n' "$(nft_set bare $ports_r)"
  # shellcheck disable=SC2086
  if [[ -n "$addrs" ]]; then
    printf '\t\tip daddr { %s } meta l4proto tcp counter drop comment "fabric addresses (NCCL bootstrap): fabric only"\n' "$(nft_set bare $addrs)"
  fi
  printf '\t}\n}\n'
}

render_unit() {
  local nft="$1"
  cat <<EOF
# Installed by scripts/backends/spark-guard.sh — undo with: sudo spark-guard.sh --remove
[Unit]
Description=OpenBeast spark-guard (engine rendezvous ports: ConnectX fabric and loopback only)
DefaultDependencies=no
After=local-fs.target
Before=network-pre.target shutdown.target
Wants=network-pre.target
Conflicts=shutdown.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$nft -f /etc/openbeast/spark-guard.nft
ExecStop=$nft delete table inet $TABLE

[Install]
WantedBy=multi-user.target
EOF
}

# ---------------------------------------------------------------- ssh posture
ssh_user() { echo "${SUDO_USER:-${USER:-}}"; }
ssh_home() {   # under SPARK_GUARD_ROOT (tests) the home is $R/home/<user>
  if [[ -n "$R" ]]; then echo "$R/home/$1"; return; fi
  getent passwd "$1" 2>/dev/null | cut -d: -f6
}
ssh_key_count() {   # usable public-key lines in the user's authorized_keys
  local home="$1" f n=0 c
  for f in "$home/.ssh/authorized_keys" "$home/.ssh/authorized_keys2"; do
    [[ -f "$f" ]] || continue
    c="$(grep -cE '(^|[[:space:]])(ssh-(ed25519|rsa|dss)|ecdsa-sha2-[a-z0-9]+|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)[[:space:]]+[A-Za-z0-9+/=]{40,}' "$f" || true)"
    n=$((n + c))
  done
  echo "$n"
}
ssh_password_auth() {   # yes / no / unknown (sshd -T needs root)
  local v
  v="$(sshd -T 2>/dev/null | awk '$1 == "passwordauthentication" {print $2}')" || true
  echo "${v:-unknown}"
}

SSH_WARNING='
  !! --harden-ssh turns OFF password (and keyboard-interactive) login in sshd.
  !! If no key you actually hold is in authorized_keys, the next login fails
  !! and the only way back is the console (monitor + keyboard on the Spark).
  !!
  !! On spark-hydra: the Mac logs in with the NVIDIA Sync key
  !! (~/Library/Application Support/NVIDIA/Sync/config/nvsync.key) and spark-1
  !! reaches spark-2 with its own key — both are key logins and keep working.
  !!
  !! Before you run it: keep one ssh session to this Spark OPEN, run it there,
  !! then from a NEW terminal check `ssh <this spark> true` from the Mac (and
  !! `ssh max@10.100.216.2 true` from spark-1). If that fails, use the open
  !! session to run: sudo scripts/backends/spark-guard.sh --unharden-ssh'

ssh_status() {
  local u home keys pa
  u="$(ssh_user)"; home="$(ssh_home "$u")"
  keys="$( [[ -n "$home" ]] && ssh_key_count "$home" || echo 0)"
  pa="$(ssh_password_auth)"
  echo "sshd: password auth = $pa; $u has $keys key(s) in authorized_keys;" \
       "hardening drop-in $( [[ -f "$SSHD_DROPIN" ]] && echo "installed ($SSHD_DROPIN)" || echo "not installed")"
  if [[ "$pa" != no && ! -f "$SSHD_DROPIN" ]]; then
    if [[ "$keys" -gt 0 ]]; then
      echo "  password login is on and $u has a key — consider: sudo $0 --harden-ssh (read its warning first)"
    else
      echo "  password login is on and $u has NO key in authorized_keys — do not harden until a key login works"
    fi
  fi
}

harden_ssh() {
  need_root
  local u home keys
  u="$(ssh_user)"; [[ -n "$u" && "$u" != root ]] || die "run through sudo from your own account (the key check needs SUDO_USER)"
  home="$(ssh_home "$u")"; [[ -n "$home" ]] || die "no home directory for $u"
  keys="$(ssh_key_count "$home")"
  echo "$SSH_WARNING"
  echo
  [[ "$keys" -gt 0 ]] || die "$u has no public key in $home/.ssh/authorized_keys — refusing (a key login must work first)"
  echo "  $u has $keys key(s) in authorized_keys."
  [[ $YES -eq 1 ]] || die "not changed. Re-run with --harden-ssh --yes once you have read the above"
  mkdir -p "$(dirname "$SSHD_DROPIN")"
  cat > "$SSHD_DROPIN" <<'EOF'
# Installed by scripts/backends/spark-guard.sh --harden-ssh (undo: --unharden-ssh).
# 00- so it wins: sshd takes the FIRST value it reads, and sshd_config Includes this directory first.
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
  chmod 0644 "$SSHD_DROPIN"
  if ! sshd -t; then
    rm -f "$SSHD_DROPIN"
    die "sshd -t rejected the config — drop-in removed, nothing changed"
  fi
  systemctl reload ssh.service 2>/dev/null || systemctl reload-or-restart ssh.service
  local pa; pa="$(ssh_password_auth)"
  [[ "$pa" == no || "$pa" == unknown ]] || echo "  WARNING: sshd -T still reports passwordauthentication $pa (a Match block?)" >&2
  echo "  sshd: password login OFF. Open sessions are untouched. Now test a NEW key login before closing this one."
}

unharden_ssh() {
  need_root
  if [[ -f "$SSHD_DROPIN" ]]; then
    rm -f "$SSHD_DROPIN"
    sshd -t && { systemctl reload ssh.service 2>/dev/null || systemctl reload-or-restart ssh.service; }
    echo "  sshd: the spark-guard drop-in is gone; sshd's own defaults apply again"
  else
    echo "  sshd: no spark-guard drop-in installed — nothing to undo"
  fi
}

# ---------------------------------------------------------------- actions
listeners() {   # wildcard / fabric-bound TCP listeners, for --status
  command -v ss >/dev/null 2>&1 || return 0
  ss -ltnH 2>/dev/null | awk '{print $4}' | sort -u
}

status() {
  local nft; nft="$(command -v nft || true)"
  echo "== spark-guard on $(hostname 2>/dev/null || echo '?')"
  if [[ -f "$RULES" ]]; then echo "rules file: $RULES"; else echo "rules file: none (not installed)"; fi
  if command -v systemctl >/dev/null 2>&1; then
    local en ac
    en="$(systemctl is-enabled "$UNIT_NAME" 2>/dev/null || true)"
    ac="$(systemctl is-active "$UNIT_NAME" 2>/dev/null || true)"
    echo "unit: $UNIT_NAME ${en:-not-installed}, ${ac:-inactive}"
  fi
  if [[ -n "$nft" ]] && "$nft" list table inet "$TABLE" >/dev/null 2>&1; then
    echo "loaded: table inet $TABLE"
    "$nft" list table inet "$TABLE" | sed 's/^/  /'
  elif [[ $EUID -ne 0 ]]; then
    echo "loaded: unknown (listing nftables needs root: sudo $0 --status)"
  else
    echo "loaded: NO — table inet $TABLE is not in the kernel; the engine ports are NOT guarded"
  fi
  if command -v ufw >/dev/null 2>&1; then
    echo "ufw: $(ufw status 2>/dev/null | head -1 || echo 'unknown (needs root)')"
  fi
  echo "TCP listeners (what each could be reached on):"
  listeners | sed 's/^/  /'
  ssh_status
  echo "Verify from another tailnet device: nc -z -w 3 <this Spark's tailnet IP> 29551 must FAIL (when a rank is up)."
}

apply() {
  need_root
  local nft tmp
  nft="$(command -v nft)" || die "nft is missing: sudo apt-get install nftables (its nftables.service stays disabled — this script does not use it)"
  tmp="$(mktemp)"
  render > "$tmp" || { rm -f "$tmp"; exit 1; }
  "$nft" -c -f "$tmp" || die "nft rejected the rendered ruleset — nothing loaded (rendered: $tmp)"
  mkdir -p "$(dirname "$RULES")" "$(dirname "$UNIT")"
  install -m 0644 "$tmp" "$RULES"
  "$nft" -f "$RULES"
  render_unit "$nft" > "$tmp"
  install -m 0644 "$tmp" "$UNIT"
  rm -f "$tmp"
  systemctl daemon-reload
  systemctl enable "$UNIT_NAME" >/dev/null 2>&1 || systemctl enable "$UNIT_NAME"
  # RemainAfterExit: mark it active now without loading twice.
  systemctl start "$UNIT_NAME" || true
  echo "== spark-guard installed: $RULES, $UNIT_NAME (enabled; loads before the network at boot)"
  sed -n '4p' "$RULES"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: inactive'; then
    echo "  note: ufw is inactive. spark-guard does not need it and does not enable it."
  fi
  ssh_status
  echo "Verify from the Mac: nc -z -w 3 <this Spark's tailnet IP> 29551   # must FAIL while a rank listens"
}

remove() {
  need_root
  local nft; nft="$(command -v nft || true)"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
  fi
  if [[ -n "$nft" ]] && "$nft" list table inet "$TABLE" >/dev/null 2>&1; then
    "$nft" delete table inet "$TABLE"
  fi
  rm -f "$UNIT" "$RULES"
  rmdir "$(dirname "$RULES")" 2>/dev/null || true
  command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload || true
  echo "== spark-guard removed: the engine ports are unfiltered again (ufw/Tailscale rules untouched)"
  [[ -f "$SSHD_DROPIN" ]] && echo "  the sshd hardening stays; undo it separately with --unharden-ssh"
  return 0
}

case "$ACTION" in
  render)       render ;;
  apply)        apply ;;
  status)       status ;;
  remove)       remove ;;
  harden-ssh)   harden_ssh ;;
  unharden-ssh) unharden_ssh ;;
esac
