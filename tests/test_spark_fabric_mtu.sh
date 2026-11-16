#!/bin/bash
# spark-fabric-mtu.sh: MTU 9000 on the Sparks' ConnectX fabric, staged for the
# next reboot. Hermetic: ibdev2netdev, ip, netplan, ibv_devinfo and ping are
# recording fakes, and every file lives under a scratch SPARK_FABRIC_ROOT —
# nothing touches the host's netplan or links. The fake netplan renders the
# NetworkManager keyfiles the way NVIDIA Sync's config does on the pair. When a
# real `netplan` is installed (Ubuntu CI), the merge is also checked against
# it, under --root-dir, with a copy of Sync's layout.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FM="$REPO_DIR/scripts/backends/spark-fabric-mtu.sh"
PASS=0 FAIL=0 SKIP=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP: $*"; SKIP=$((SKIP + 1)); }
echo "=== spark-fabric-mtu tests ==="

REAL_NETPLAN="$(command -v netplan || true)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/root/etc/netplan" "$T/state"
export FM_STATE="$T/state" FM_LOG="$T/log" FM_ALL="$T/all.log"
: > "$FM_ALL"

# spark-1's layout (2026-11-16): two rails Up, their twins Down.
cat > "$T/bin/ibdev2netdev" <<'S'
#!/bin/bash
[[ "${FM_NO_FABRIC:-0}" == 1 ]] && exit 0
echo "rocep1s0f0 port 1 ==> enp1s0f0np0 (Up)"
[[ "${FM_ONE_PORT:-0}" == 1 ]] && exit 0
cat <<'E'
rocep1s0f1 port 1 ==> enp1s0f1np1 (Down)
roceP2p1s0f0 port 1 ==> enP2p1s0f0np0 (Up)
roceP2p1s0f1 port 1 ==> enP2p1s0f1np1 (Down)
E
S
# ip: default route on enP7s7 (the LAN NIC) unless FM_DEFROUTE names another;
# the live MTU per netdev from $FM_STATE/mtu.<dev> (1500 when absent).
cat > "$T/bin/ip" <<'S'
#!/bin/bash
echo "ip $*" >> "$FM_ALL"
case "$*" in
  "-4 route show default") echo "default via 192.168.40.1 dev ${FM_DEFROUTE:-enP7s7} proto dhcp src 192.168.40.153 metric 102" ;;
  "-6 route show default") ;;
  "-o link show dev "*) d="${@: -1}"; m=1500; [[ -f "$FM_STATE/mtu.$d" ]] && m="$(cat "$FM_STATE/mtu.$d")"
     echo "3: $d: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu $m qdisc mq state UP mode DEFAULT group default qlen 1000\\    link/ether 4c:bb:47:2e:3d:c7" ;;
  "-o route get "*) case "${@: -1}" in
       10.100.216.*) echo "${@: -1} dev enp1s0f0np0 src 10.100.216.1 uid 1000 \\    cache" ;;
       10.100.217.*) echo "${@: -1} dev enP2p1s0f0np0 src 10.100.217.1 uid 1000 \\    cache" ;;
       *)            echo "${@: -1} dev enP7s7 src 192.168.40.153 uid 1000 \\    cache" ;;
     esac ;;
  "-4 -o addr show dev enp1s0f0np0")   echo "3: enp1s0f0np0    inet 10.100.216.1/24 brd 10.100.216.255 scope global enp1s0f0np0" ;;
  "-4 -o addr show dev enP2p1s0f0np0") echo "5: enP2p1s0f0np0    inet 10.100.217.1/24 brd 10.100.217.255 scope global enP2p1s0f0np0" ;;
esac
exit 0
S
cat > "$T/bin/ibv_devinfo" <<'S'
#!/bin/bash
m=1024; [[ -f "$FM_STATE/roce.$2" ]] && m="$(cat "$FM_STATE/roce.$2")"
printf 'hca_id:\t%s\n\ttransport:\t\t\tInfiniBand (0)\n\tport:\t1\n\t\t\tstate:\t\t\tPORT_ACTIVE (4)\n\t\t\tmax_mtu:\t\t4096 (5)\n\t\t\tactive_mtu:\t\t%s (3)\n' "$2" "$m"
S
# netplan: renders a NetworkManager keyfile per managed netdev (FM_MANAGED,
# default all four), with mtu=9000 when the drop-in lists it — the shape
# NVIDIA Sync's renderer: NetworkManager config produces on the pair.
cat > "$T/bin/netplan" <<'S'
#!/bin/bash
echo "netplan $*" >> "$FM_LOG"; echo "netplan $*" >> "$FM_ALL"
[[ "$1" == generate ]] || exit 0
[[ "${FM_NETPLAN_FAIL:-0}" == 1 ]] && { echo "Error in network definition: fake rejection" >&2; exit 1; }
r=""; [[ "${2:-}" == --root-dir ]] && r="$3"
nm="$r/run/NetworkManager/system-connections"; mkdir -p "$nm"; rm -f "$nm"/netplan-*.nmconnection
drop="$r/etc/netplan/99-zz-openbeast-fabric-mtu.yaml"
for d in ${FM_MANAGED:-enp1s0f0np0 enp1s0f1np1 enP2p1s0f0np0 enP2p1s0f1np1}; do
  f="$nm/netplan-$d.nmconnection"
  printf '[connection]\nid=netplan-%s\ntype=ethernet\ninterface-name=%s\n\n[ethernet]\nwake-on-lan=0\n' "$d" "$d" > "$f"
  if [[ -f "$drop" && "${FM_NETPLAN_NOMTU:-0}" != 1 ]] && grep -qx "    $d:" "$drop"; then echo "mtu=9000" >> "$f"; fi
done
[[ "${FM_NETPLAN_NEWID:-0}" == 1 && -f "$drop" ]] && printf '[connection]\nid=netplan-x\n' > "$nm/netplan-fabric-x.nmconnection"
exit 0
S
cat > "$T/bin/ping" <<'S'
#!/bin/bash
echo "ping $*" >> "$FM_ALL"; echo "ping $*" >> "$FM_LOG"
[[ -f "$FM_STATE/jumbo-ok" ]]
S
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH"
R="$T/root"
DROP="$R/etc/netplan/99-zz-openbeast-fabric-mtu.yaml"
NMC="$R/run/NetworkManager/system-connections"
_run() { : > "$FM_LOG"; OUT="$(SPARK_FABRIC_ROOT="$R" bash "$FM" "$@" 2>&1)"; RC=$?; }
_has() { [[ "$(grep -cF -- "$2" <<< "$1" || true)" -gt 0 ]]; }
_perms() { ls -l "$1" | awk '{print $1}' | cut -c1-10; }
_reboot() { local d; for d in enp1s0f0np0 enP2p1s0f0np0; do echo 9000 > "$FM_STATE/mtu.$d"; done
            echo 4096 > "$FM_STATE/roce.rocep1s0f0"; echo 4096 > "$FM_STATE/roce.roceP2p1s0f0"; }
_unreboot() { rm -f "$FM_STATE"/mtu.* "$FM_STATE"/roce.* "$FM_STATE/jumbo-ok"; }
netplan generate --root-dir "$R"     # the booted box: Sync's config already rendered

# --- argument handling -------------------------------------------------------
_run --verify
[[ $RC -eq 2 ]] && _has "$OUT" "ip -br addr" && pass "--verify without a peer IP refuses, naming where the IPs come from" || fail "verify no ip (rc=$RC): $OUT"
_run --verify 10.100.216
[[ $RC -eq 2 ]] && pass "--verify refuses a non-IPv4 peer" || fail "verify bad ip (rc=$RC): $OUT"
_run --bogus
[[ $RC -eq 2 ]] && pass "an unknown option is refused" || fail "bogus (rc=$RC)"
_run --help
[[ $RC -eq 0 ]] && _has "$OUT" "--unstage" && pass "--help prints the usage" || fail "help: $OUT"

# --- dry run -----------------------------------------------------------------
_run --dry-run
[[ $RC -eq 0 && ! -e "$DROP" ]] && _has "$OUT" "dry run" && _has "$OUT" "    enP2p1s0f1np1:" && _has "$OUT" "mtu: 9000" \
  && [[ ! -s "$FM_LOG" ]] && pass "--dry-run prints the drop-in and the actions, writes nothing, runs no netplan" \
  || fail "dry run (rc=$RC): $OUT / $(cat "$FM_LOG")"
_has "$OUT" "never netplan apply" && pass "--dry-run says it never applies" || fail "dry run apply note: $OUT"

# --- status before staging -------------------------------------------------------
_run --status
[[ $RC -eq 0 ]] && _has "$OUT" "not staged" && grep -Eq "enp1s0f0np0 +rocep1s0f0 +Up +1500 +1024 +default +no" <<< "$OUT" \
  && grep -Eq "enP2p1s0f1np1 +roceP2p1s0f1 +Down " <<< "$OUT" \
  && pass "--status: netdev MTU 1500, RoCE active_mtu 1024, boot config default, not staged" || fail "status before (rc=$RC): $OUT"

# --- stage -------------------------------------------------------------------------
_run
[[ $RC -eq 0 && -f "$DROP" ]] && pass "--stage (the default) writes the drop-in" || fail "stage (rc=$RC): $OUT"
[[ "$(_perms "$DROP")" == "-rw-------" ]] && pass "the drop-in is 0600 (netplan warns on anything wider)" || fail "perms $(_perms "$DROP")"
want="$(printf 'network:\n  version: 2\n  ethernets:\n    enp1s0f0np0:\n      mtu: 9000\n    enp1s0f1np1:\n      mtu: 9000\n    enP2p1s0f0np0:\n      mtu: 9000\n    enP2p1s0f1np1:\n      mtu: 9000')"
[[ "$(grep -v '^#' "$DROP")" == "$want" ]] && pass "content: network {version 2, ethernets {<each ConnectX netdev>: {mtu: 9000}}} and nothing else" \
  || fail "content: $(cat "$DROP")"
[[ "$(grep -c "enP7s7" "$DROP" || true)" -eq 0 ]] && pass "the LAN NIC (enP7s7, not a ConnectX port) is not in it" || fail "LAN NIC in drop-in"
[[ "$(grep -c '^netplan generate --root-dir' "$FM_LOG" || true)" -eq 1 ]] && pass "it validates with netplan generate" || fail "generate: $(cat "$FM_LOG")"
[[ "$(grep -c "^mtu=9000$" "$NMC/netplan-enp1s0f0np0.nmconnection" || true)" -eq 1 ]] && _has "$OUT" "boot config now mtu 9000 (NetworkManager)" \
  && pass "it checks the generated boot config carries mtu=9000, backend unchanged" || fail "generated check: $OUT"
_has "$OUT" "NEXT REBOOT" && _has "$OUT" "BOTH Sparks" && _has "$OUT" "NCCL" && _has "$OUT" "--verify" \
  && pass "it says: next reboot, on BOTH Sparks (a mismatch stalls NCCL), then --verify" || fail "notice: $OUT"
_has "$OUT" "still MTU 1500" && pass "it says the live links are untouched" || fail "live note: $OUT"
cp "$DROP" "$T/first.yaml"
_run --stage
[[ $RC -eq 0 ]] && diff "$T/first.yaml" "$DROP" >/dev/null && pass "--stage is idempotent" || fail "restage (rc=$RC)"
ls -a "$R/etc/netplan" > "$T/ls"
[[ "$(grep -c '\.tmp' "$T/ls" || true)" -eq 0 && "$(grep -c . "$T/ls")" -eq 3 ]] && pass "no temp file left in /etc/netplan" || fail "leftovers: $(cat "$T/ls")"

# --- status after staging ---------------------------------------------------------
_run --status
[[ $RC -eq 0 ]] && grep -Eq "^drop-in: .* staged$" <<< "$OUT" && grep -Eq "enp1s0f0np0 +rocep1s0f0 +Up +1500 +1024 +9000 +yes" <<< "$OUT" \
  && _has "$OUT" "next reboot of BOTH" && pass "--status: staged, boot config 9000, live still 1500/1024 until the reboot" || fail "status staged: $OUT"

# --- verify ---------------------------------------------------------------------------
_run --verify 10.100.216.2 10.100.217.2
[[ $RC -ne 0 ]] && _has "$OUT" "FAIL enp1s0f0np0 netdev mtu 1500" && _has "$OUT" "FAIL rocep1s0f0 active_mtu 1024" && _has "$OUT" "NOT VERIFIED" \
  && pass "--verify before the reboot: fails on netdev MTU and active_mtu, exits non-zero" || fail "verify pre-reboot (rc=$RC): $OUT"
_reboot
_run --verify 10.100.216.2 10.100.217.2
[[ $RC -ne 0 ]] && _has "$OUT" "PASS rocep1s0f0 active_mtu 4096" && _has "$OUT" "FAIL 10.100.216.2 over enp1s0f0np0" \
  && pass "--verify with the peer still at 1500 (jumbo ping lost): non-zero" || fail "verify peer 1500 (rc=$RC): $OUT"
touch "$FM_STATE/jumbo-ok"
_run --verify 10.100.216.2
[[ $RC -ne 0 ]] && _has "$OUT" "no peer IP given on enP2p1s0f0np0 (10.100.217.1/24)" \
  && pass "--verify fails when a rail has no peer IP (each rail is checked)" || fail "verify uncovered (rc=$RC): $OUT"
_run --verify 192.168.40.151 10.100.216.2 10.100.217.2
[[ $RC -ne 0 ]] && _has "$OUT" "FAIL 192.168.40.151 routes via 'enP7s7'" \
  && pass "--verify refuses a peer that routes over the LAN, not the fabric" || fail "verify LAN peer (rc=$RC): $OUT"
_run --verify 10.100.216.2 10.100.217.2
[[ $RC -eq 0 ]] && _has "$OUT" "VERIFIED" \
  && [[ "$(grep -c -- '^ping -M do -s 8972 -c 3 -W 2 -I enp1s0f0np0 10.100.216.2$' "$FM_LOG" || true)" -eq 1 ]] \
  && [[ "$(grep -c -- '^ping -M do -s 8972 -c 3 -W 2 -I enP2p1s0f0np0 10.100.217.2$' "$FM_LOG" || true)" -eq 1 ]] \
  && pass "--verify after both reboots: 8972-byte DF ping on each rail, active_mtu 4096, exit 0" || fail "verify ok (rc=$RC): $OUT / $(cat "$FM_LOG")"
_run --status
grep -Eq "enp1s0f0np0 +rocep1s0f0 +Up +9000 +4096 +9000 +yes" <<< "$OUT" && pass "--status after the reboot: 9000 / 4096" || fail "status after reboot: $OUT"
_unreboot

# --- the default-route NIC is never staged -------------------------------------------
rm -f "$DROP"; netplan generate --root-dir "$R"
FM_DEFROUTE=enp1s0f0np0 _run --stage
[[ $RC -eq 0 && -f "$DROP" ]] && [[ "$(grep -c "enp1s0f0np0" "$DROP" || true)" -eq 0 ]] && _has "$OUT" "enp1s0f0np0 (rocep1s0f0) carries a default route" \
  && [[ "$(grep -c "enP2p1s0f0np0:" "$DROP" || true)" -eq 1 ]] \
  && pass "a ConnectX netdev carrying the default route is left out (loudly); the others are staged" || fail "defroute (rc=$RC): $OUT / $(cat "$DROP" 2>/dev/null)"
rm -f "$DROP"
FM_ONE_PORT=1 FM_DEFROUTE=enp1s0f0np0 _run --stage
[[ $RC -ne 0 && ! -e "$DROP" ]] && _has "$OUT" "Nothing staged" && pass "when the only ConnectX netdev carries the default route: refused, nothing written" \
  || fail "defroute only (rc=$RC): $OUT"
FM_NO_FABRIC=1 _run --stage
[[ $RC -ne 0 && ! -e "$DROP" ]] && _has "$OUT" "no ConnectX RoCE port" && pass "no ConnectX port at all: refused, nothing written" || fail "no fabric (rc=$RC): $OUT"
FM_MANAGED="enp1s0f0np0 enP2p1s0f0np0" netplan generate --root-dir "$R"
FM_MANAGED="enp1s0f0np0 enP2p1s0f0np0" _run --stage
[[ $RC -eq 0 ]] && [[ "$(grep -c "enp1s0f1np1" "$DROP" || true)" -eq 0 ]] && _has "$OUT" "netplan does not manage it" \
  && pass "a ConnectX netdev netplan does not manage is left alone (no new interface definition)" || fail "unmanaged (rc=$RC): $OUT"
rm -f "$DROP"; netplan generate --root-dir "$R"

# --- rollback when netplan disagrees ----------------------------------------------------
FM_NETPLAN_FAIL=1 _run --stage
[[ $RC -ne 0 && ! -e "$DROP" ]] && _has "$OUT" "taken back out" && pass "netplan generate rejecting it: the drop-in is removed again" || fail "generate fail (rc=$RC): $OUT"
FM_NETPLAN_NEWID=1 _run --stage
[[ $RC -ne 0 && ! -e "$DROP" ]] && _has "$OUT" "generated interfaces changed" && pass "a merge that adds a new interface (not Sync's) is rolled back" || fail "new id (rc=$RC): $OUT"
FM_NETPLAN_NOMTU=1 _run --stage
[[ $RC -ne 0 && ! -e "$DROP" ]] && _has "$OUT" "did not render mtu 9000" && pass "a generated config without mtu 9000 is rolled back" || fail "no mtu (rc=$RC): $OUT"
printf '# an older stage\nnetwork:\n  version: 2\n  ethernets:\n    enp1s0f0np0:\n      mtu: 9000\n' > "$DROP"; chmod 600 "$DROP"
cp "$DROP" "$T/older.yaml"; netplan generate --root-dir "$R"
FM_NETPLAN_NOMTU=1 _run --stage
[[ $RC -ne 0 ]] && diff "$T/older.yaml" "$DROP" >/dev/null && pass "a rollback restores the previous drop-in, not none" || fail "restore previous (rc=$RC): $OUT"

# --- unstage ------------------------------------------------------------------------------
_run --stage
_run --unstage
[[ $RC -eq 0 && ! -e "$DROP" ]] && [[ "$(grep -c '^netplan generate' "$FM_LOG" || true)" -eq 1 ]] \
  && [[ "$(grep -c '^mtu=' "$NMC/netplan-enp1s0f0np0.nmconnection" || true)" -eq 0 ]] && _has "$OUT" "next reboot of BOTH" \
  && pass "--unstage removes the drop-in and re-generates (default MTU at the next reboot)" || fail "unstage (rc=$RC): $OUT"
_run --unstage
[[ $RC -eq 0 ]] && _has "$OUT" "nothing staged" && pass "--unstage with nothing staged is a no-op" || fail "unstage twice (rc=$RC): $OUT"
_run --stage; _reboot
_run --unstage
_has "$OUT" "enp1s0f0np0 is live at MTU 9000" && pass "--unstage after the reboot warns that BOTH must reboot again" || fail "unstage live: $OUT"
_unreboot

# --- never `netplan apply`, never as non-root without the test root -----------------------
[[ "$(grep -c '^netplan apply' "$FM_ALL" || true)" -eq 0 && "$(grep -c '^netplan generate' "$FM_ALL" || true)" -gt 5 ]] \
  && pass "netplan apply was never called, in any mode" || fail "apply called: $(grep '^netplan' "$FM_ALL")"
[[ "$(grep -c 'apply' "$FM" || true)" -gt 0 && "$(grep -cE '"\$NETPLAN" +apply|netplan apply$' "$FM" || true)" -eq 0 ]] \
  && pass "the script has no code path that runs netplan apply" || fail "apply in source"
if [[ $EUID -ne 0 ]]; then
  OUT="$(bash "$FM" --stage 2>&1)"; RC=$?
  [[ $RC -ne 0 ]] && _has "$OUT" "sudo" && pass "--stage without root (and no test root) refuses" || fail "non-root (rc=$RC): $OUT"
  OUT="$(bash "$FM" --unstage 2>&1)"; RC=$?
  [[ $RC -ne 0 || "$OUT" == *"nothing staged"* ]] && pass "--unstage without root changes nothing" || fail "non-root unstage (rc=$RC): $OUT"
else
  skip "non-root refusal (running as root)"
fi

# --- the real netplan merge (Ubuntu CI) -------------------------------------------------------
# NVIDIA Sync's file on the pair is root-only; this copy has its shape:
# renderer NetworkManager, the four ConnectX netdevs keyed by name, addresses
# on the two cabled rails. The drop-in must merge into those definitions.
if [[ -n "$REAL_NETPLAN" ]]; then
  RR="$T/real"; mkdir -p "$RR/etc/netplan"
  cat > "$RR/etc/netplan/99-nvidia-sync-cluster.yaml" <<'Y'
network:
  version: 2
  renderer: NetworkManager
  ethernets:
    enp1s0f0np0:
      addresses: [10.100.216.1/24]
    enp1s0f1np1:
      dhcp4: false
    enP2p1s0f0np0:
      addresses: [10.100.217.1/24]
    enP2p1s0f1np1:
      dhcp4: false
Y
  printf 'network:\n  version: 2\n  renderer: NetworkManager\n' > "$RR/etc/netplan/00-installer-config.yaml"
  chmod 600 "$RR/etc/netplan/"*.yaml
  if "$REAL_NETPLAN" generate --root-dir "$RR" >"$T/real.log" 2>&1 && [[ -e "$RR/run/NetworkManager/system-connections/netplan-enp1s0f0np0.nmconnection" ]]; then
    OUT="$(NETPLAN="$REAL_NETPLAN" SPARK_FABRIC_ROOT="$RR" bash "$FM" --stage 2>&1)"; RC=$?
    K="$RR/run/NetworkManager/system-connections/netplan-enp1s0f0np0.nmconnection"
    if [[ $RC -eq 0 ]] && [[ "$(grep -c '^mtu=9000$' "$K" 2>/dev/null || true)" -eq 1 ]] && [[ "$(grep -c '10.100.216.1/24' "$K" 2>/dev/null || true)" -eq 1 ]]; then
      pass "real netplan: the drop-in merges into Sync's definitions (mtu=9000, the address kept, same interfaces)"
    else
      fail "real netplan merge (rc=$RC): $OUT / $(cat "$K" 2>/dev/null)"
    fi
    OUT="$(NETPLAN="$REAL_NETPLAN" SPARK_FABRIC_ROOT="$RR" bash "$FM" --unstage 2>&1)"; RC=$?
    [[ $RC -eq 0 ]] && [[ "$(grep -c '^mtu=' "$K" 2>/dev/null || true)" -eq 0 ]] && [[ "$(grep -c '10.100.216.1/24' "$K" 2>/dev/null || true)" -eq 1 ]] \
      && pass "real netplan: --unstage renders the original config again" || fail "real unstage (rc=$RC): $OUT / $(cat "$K" 2>/dev/null)"
  else
    skip "real netplan cannot render under --root-dir here: $(head -3 "$T/real.log")"
  fi
else
  skip "no real netplan on this host (Ubuntu CI checks the merge)"
fi

echo ""
echo "spark-fabric-mtu: $PASS passed, $FAIL failed, $SKIP skipped"
[[ $FAIL -eq 0 ]]
