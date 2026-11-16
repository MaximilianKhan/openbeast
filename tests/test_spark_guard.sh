#!/bin/bash
# spark-guard.sh: the nftables guard that keeps the engines' unauthenticated
# rendezvous ports on the ConnectX fabric. Hermetic: ibdev2netdev, ip, nft,
# systemctl and sshd are recording stubs, and every file goes under a scratch
# SPARK_GUARD_ROOT — nothing touches the host's ruleset or /etc. When a real
# nft can check a ruleset (root, or passwordless sudo into a throwaway network
# namespace, as on CI) the rendered ruleset is also loaded THERE and listed.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SG="$REPO_DIR/scripts/backends/spark-guard.sh"
PASS=0 FAIL=0 SKIP=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP: $*"; SKIP=$((SKIP + 1)); }
echo "=== spark-guard tests ==="

REAL_NFT="$(command -v nft || true)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/root"
# spark-1's layout: two rails Up, their twins Down.
cat > "$T/bin/ibdev2netdev" <<'S'
#!/bin/bash
[[ "${SG_NO_FABRIC:-0}" == 1 ]] && exit 0
cat <<'E'
rocep1s0f0 port 1 ==> enp1s0f0np0 (Up)
rocep1s0f1 port 1 ==> enp1s0f1np1 (Down)
roceP2p1s0f0 port 1 ==> enP2p1s0f0np0 (Up)
roceP2p1s0f1 port 1 ==> enP2p1s0f1np1 (Down)
E
S
cat > "$T/bin/ip" <<'S'
#!/bin/bash
case "${@: -1}" in
  enp1s0f0np0)   echo "5: enp1s0f0np0    inet 10.100.216.1/24 brd 10.100.216.255 scope global enp1s0f0np0" ;;
  enP2p1s0f0np0) echo "7: enP2p1s0f0np0    inet 10.100.217.1/24 brd 10.100.217.255 scope global enP2p1s0f0np0" ;;
esac
S
cat > "$T/bin/nft" <<'S'
#!/bin/bash
echo "nft $*" >> "$SG_LOG"
[[ "$1" == -c && "${SG_NFT_REJECT:-0}" == 1 ]] && { echo "Error: syntax error" >&2; exit 1; }
[[ "$1 $2" == "list table" ]] && { [[ -f "$SG_LOADED" ]] || exit 1; cat "$SG_LOADED"; exit 0; }
[[ "$1" == -f ]] && cp "$2" "$SG_LOADED"
[[ "$1 $2" == "delete table" ]] && rm -f "$SG_LOADED"
exit 0
S
cat > "$T/bin/systemctl" <<'S'
#!/bin/bash
echo "systemctl $*" >> "$SG_LOG"; exit 0
S
cat > "$T/bin/sshd" <<'S'
#!/bin/bash
echo "sshd $*" >> "$SG_LOG"
[[ "$1" == -t && "${SG_SSHD_BAD:-0}" == 1 ]] && exit 1
[[ "$1" == -T ]] && { [[ -f "$SG_ROOT/etc/ssh/sshd_config.d/00-openbeast-guard.conf" ]] && echo "passwordauthentication no" || echo "passwordauthentication yes"; }
exit 0
S
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH" SG_LOG="$T/log" SG_LOADED="$T/loaded.nft" SG_ROOT="$T/root"
export SPARK_ENV="$T/spark.env"    # absent unless a test writes it
_run() { : > "$SG_LOG"; OUT="$(SPARK_GUARD_ROOT="$T/root" SUDO_USER=max bash "$SG" "$@" 2>&1)"; RC=$?; }
_has() { grep -qF -- "$2" <<< "$1"; }
_line() { grep -nF -- "$2" <<< "$1" | head -1 | cut -d: -f1; }

# --- render: the ruleset itself ------------------------------------------------
_run --render
R1="$OUT"
[[ $RC -eq 0 ]] && pass "--render works without root" || fail "--render rc=$RC: $OUT"
_has "$R1" "table inet openbeast_guard {" && _has "$R1" "type filter hook input priority filter - 10; policy accept;" \
  && pass "its own inet table, an input base chain at filter-10 (ahead of iptables-nft/ufw and ts-input)" \
  || fail "table/chain: $R1"
! grep -q 'flush ruleset' <<< "$R1" && _has "$R1" "delete table inet openbeast_guard" \
  && pass "never flushes the ruleset; replaces only its own table (Docker's and Tailscale's rules survive)" \
  || fail "flush/delete: $R1"
_has "$R1" 'iifname { "enp1s0f0np0", "enp1s0f1np1", "enP2p1s0f0np0", "enP2p1s0f1np1" } accept' \
  && pass "every ConnectX netdev is fabric, Up or Down (a rail may come up later)" || fail "fabric set: $R1"
_has "$R1" "tcp dport { 29501-29502, 29551-29552, 29583-29584 } counter drop" \
  && pass "vLLM 29501/+1, TensorFold 29551/+1 and native 29583/+1 are dropped off the fabric" || fail "ports: $R1"
_has "$R1" "# args: " && pass "the render records its command-line inputs (for --status to re-render)" || fail "args line: $R1"
_has "$R1" "ip daddr { 10.100.216.1, 10.100.217.1 } meta l4proto tcp counter drop" \
  && pass "TCP to the fabric addresses (NCCL bootstrap, ephemeral ports) is dropped off the fabric" || fail "daddr: $R1"
lo="$(_line "$R1" 'iifname "lo" accept')"; fab="$(_line "$R1" '} accept comment "ConnectX-7 fabric"')"
dp="$(_line "$R1" 'tcp dport')"; da="$(_line "$R1" 'ip daddr')"
[[ -n "$lo" && -n "$fab" && -n "$dp" && -n "$da" && $lo -lt $dp && $fab -lt $dp && $dp -lt $da ]] \
  && pass "loopback and fabric are accepted BEFORE the drops" || fail "rule order lo=$lo fab=$fab dport=$dp daddr=$da"
_untouched=1
for port in 22 443 8443 8444 8445 8446 8447 8889 3000 8000; do
  if grep -E "dport .*\\b$port\\b" <<< "$R1" >/dev/null; then fail "port $port must not be guarded"; _untouched=0; fi
done
[[ $_untouched -eq 1 ]] && pass "ssh, tailscale serve and the stack's ports are not touched"

SG_NO_FABRIC=1 _run --render
[[ $RC -ne 0 ]] && _has "$OUT" "--no-fabric" && pass "no ConnectX netdev: refused, naming --no-fabric" || fail "no fabric (rc=$RC): $OUT"
SG_NO_FABRIC=1 _run --render --no-fabric
[[ $RC -eq 0 ]] && ! _has "$OUT" "iifname {" && ! _has "$OUT" "ip daddr" && _has "$OUT" "tcp dport { 29501" \
  && pass "--no-fabric: the ports become loopback-only" || fail "--no-fabric (rc=$RC): $OUT"
SG_NO_FABRIC=1 _run --render --iface enp9s0
[[ $RC -eq 0 ]] && _has "$OUT" 'iifname { "enp9s0" } accept' && pass "--iface names a fabric netdev by hand" || fail "--iface (rc=$RC): $OUT"
_run --render --iface 'x"; accept #'
[[ $RC -ne 0 ]] && pass "an interface name that is not a netdev name is refused (no rule injection)" || fail "bad iface (rc=$RC): $OUT"
_run --render --port 70000
[[ $RC -eq 2 ]] && pass "--port outside 1..65535 is refused" || fail "bad port (rc=$RC): $OUT"
printf 'TENSORFOLD_MASTER_PORT=30010   # moved\nVLLM_MASTER_PORT="29600"\n' > "$SPARK_ENV"
_run --render --port 12345
[[ $RC -eq 0 ]] && _has "$OUT" "tcp dport { 12345, 29501-29502, 29551-29552, 29583-29584, 29600-29601, 30010-30011 }" \
  && pass "spark.env's VLLM/TENSORFOLD_MASTER_PORT (+1) and --port are guarded too" || fail "env ports (rc=$RC): $OUT"
rm -f "$SPARK_ENV"

# --- the real nft, when it may check a ruleset --------------------------------
printf '%s\n' "$R1" > "$T/r1.nft"
if [[ -n "$REAL_NFT" && $EUID -eq 0 ]]; then
  if "$REAL_NFT" -c -f "$T/r1.nft"; then pass "nft -c accepts the rendered ruleset"; else fail "nft -c rejected it"; fi
elif [[ -n "$REAL_NFT" ]] && command -v unshare >/dev/null && sudo -n true 2>/dev/null; then
  # A throwaway network namespace: the host's ruleset is never touched.
  if NS_OUT="$(sudo -n unshare -n sh -c "'$REAL_NFT' -f '$T/r1.nft' && '$REAL_NFT' -f '$T/r1.nft' && '$REAL_NFT' list table inet openbeast_guard && '$REAL_NFT' delete table inet openbeast_guard" 2>&1)" \
     && grep -q 'priority filter - 10' <<< "$NS_OUT" && grep -q '29551-29552' <<< "$NS_OUT"; then
    pass "nft loads the ruleset twice (idempotent), lists it and deletes it, in a scratch netns"
  else
    fail "nft in a scratch netns: $NS_OUT"
  fi
else
  skip "no root nft here (nft needs CAP_NET_ADMIN even for -c); CI checks it in a scratch netns"
fi

# --- apply / status / remove ----------------------------------------------------
_run
RULES="$T/root/etc/openbeast/spark-guard.nft" UNIT="$T/root/etc/systemd/system/openbeast-spark-guard.service"
[[ $RC -eq 0 && -f "$RULES" && -f "$UNIT" ]] && pass "apply writes the rules file and the unit" || fail "apply (rc=$RC): $OUT"
c="$(_line "$(cat "$SG_LOG")" "nft -c -f")"; f="$(_line "$(cat "$SG_LOG")" "nft -f $RULES")"
[[ -n "$c" && -n "$f" && $c -lt $f ]] && pass "the ruleset is checked (nft -c) before it is loaded" || fail "check/load order: $(cat "$SG_LOG")"
grep -q "ExecStart=.*nft -f /etc/openbeast/spark-guard.nft" "$UNIT" && grep -q "Before=network-pre.target" "$UNIT" \
  && grep -q "ExecStop=.*nft delete table inet openbeast_guard" "$UNIT" && grep -q "WantedBy=multi-user.target" "$UNIT" \
  && pass "the unit loads the file before the network comes up, and its stop deletes only the table" || fail "unit: $(cat "$UNIT")"
grep -q "systemctl enable openbeast-spark-guard.service" "$SG_LOG" && ! grep -q "nftables" "$SG_LOG" \
  && pass "persists through its own unit, never Ubuntu's nftables.service (which flushes the ruleset)" || fail "enable: $(cat "$SG_LOG")"
cp "$RULES" "$T/first.nft"
_run
[[ $RC -eq 0 ]] && diff <(sed 1,2d "$T/first.nft") <(sed 1,2d "$RULES") >/dev/null && pass "apply is idempotent" || fail "second apply (rc=$RC)"
_run --status
[[ $RC -eq 0 ]] && _has "$OUT" "loaded: table inet openbeast_guard" && _has "$OUT" "29551" && _has "$OUT" "nc -z" \
  && pass "--status shows the loaded table and how to verify from another device" || fail "status (rc=$RC): $OUT"
_has "$OUT" "rules: current" && ! _has "$OUT" "STALE" \
  && pass "--status re-renders and says the installed rules are current" || fail "status current: $OUT"
# The live drift (2026-11-16): spark.env moved a master port after the guard
# was installed, so the installed rules no longer covered it. --status must say so.
printf 'VLLM_MASTER_PORT=29700\n' > "$SPARK_ENV"
_run --status
_has "$OUT" "rules: STALE" && _has "$OUT" "sudo " && _has "$OUT" "now:" && _has "$OUT" "29700-29701" \
  && pass "--status flags rules that differ from what the current inputs render (STALE, with the diff)" || fail "status stale: $OUT"
rm -f "$SPARK_ENV"
SG_NO_FABRIC=1 _run --no-fabric --port 4242
SG_NO_FABRIC=1 _run --status
_has "$OUT" "rules: current" && pass "--status re-renders with the inputs the install recorded (--no-fabric --port)" || fail "status args: $OUT"
_run
SG_NFT_REJECT=1 _run
[[ $RC -ne 0 ]] && ! grep -q "nft -f $RULES" "$SG_LOG" && pass "a ruleset nft rejects is never loaded" || fail "reject (rc=$RC): $(cat "$SG_LOG")"
_run --remove
[[ $RC -eq 0 && ! -f "$RULES" && ! -f "$UNIT" ]] && grep -q "nft delete table inet openbeast_guard" "$SG_LOG" \
  && grep -q "systemctl disable --now openbeast-spark-guard.service" "$SG_LOG" \
  && pass "--remove deletes the table, the unit and the file" || fail "remove (rc=$RC): $OUT $(cat "$SG_LOG")"
_run --status
_has "$OUT" "rules file: none" && pass "--status after --remove says not installed" || fail "status after remove: $OUT"
OUT="$(SUDO_USER=max bash "$SG" --remove 2>&1)"; RC=$?
if [[ $EUID -ne 0 ]]; then
  [[ $RC -ne 0 ]] && _has "$OUT" "sudo" && pass "without root (and no test root) it refuses" || fail "non-root (rc=$RC): $OUT"
fi

# --- sshd hardening: off by default, refuses without a key ----------------------
DROP="$T/root/etc/ssh/sshd_config.d/00-openbeast-guard.conf"
_run
! grep -q "^sshd -t" "$SG_LOG" && [[ ! -f "$DROP" ]] && pass "apply never touches sshd" || fail "apply touched sshd"
_run --harden-ssh --yes
[[ $RC -ne 0 && ! -f "$DROP" ]] && _has "$OUT" "no public key" && _has "$OUT" "console" \
  && pass "--harden-ssh refuses when the user has no key (and says why loudly)" || fail "no key (rc=$RC): $OUT"
mkdir -p "$T/root/home/max/.ssh"
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq0lvzTyBz2nZJ7mN6pYk3m0Jb2s0aSXy0k8YyRb1Qy nvsync" > "$T/root/home/max/.ssh/authorized_keys"
_run --status
_has "$OUT" "password auth = yes" && _has "$OUT" "1 key(s)" && _has "$OUT" "consider: sudo" \
  && pass "--status reports password login and offers hardening only once a key exists" || fail "ssh status: $OUT"
_run --harden-ssh
[[ $RC -ne 0 && ! -f "$DROP" ]] && _has "$OUT" "--yes" && pass "--harden-ssh without --yes changes nothing" || fail "no --yes (rc=$RC): $OUT"
SG_SSHD_BAD=1 _run --harden-ssh --yes
[[ $RC -ne 0 && ! -f "$DROP" ]] && pass "sshd -t failing: the drop-in is removed again" || fail "sshd -t bad (rc=$RC): $OUT"
_run --harden-ssh --yes
[[ $RC -eq 0 && -f "$DROP" ]] && grep -qx "PasswordAuthentication no" "$DROP" && grep -qx "KbdInteractiveAuthentication no" "$DROP" \
  && grep -q "systemctl reload ssh.service" "$SG_LOG" && pass "--harden-ssh --yes: drop-in, sshd -t, reload" || fail "harden (rc=$RC): $OUT"
_run --remove
[[ -f "$DROP" ]] && _has "$OUT" "--unharden-ssh" && pass "--remove leaves the ssh hardening, naming --unharden-ssh" || fail "remove vs ssh: $OUT"
_run --unharden-ssh
[[ $RC -eq 0 && ! -f "$DROP" ]] && pass "--unharden-ssh undoes it" || fail "unharden (rc=$RC): $OUT"

echo ""
echo "spark-guard: $PASS passed, $FAIL failed, $SKIP skipped"
[[ $FAIL -eq 0 ]]
