#!/bin/bash
# SSD/NVMe wear tracking (scripts/ssd-wear.sh) — structure + behavior tests.
#
# Usage: ./tests/test_ssd_wear.sh
#
# Nothing here needs smartmontools, root, or a real drive: a STUB smartctl is
# put on PATH returning canned smartctl -j -a fixtures, and the state file is
# redirected into a throwaway dir. The real .run/ssd-wear.json is never touched
# and no device is opened.
#
# The load-bearing properties under test:
#   • the NVMe data-unit constant is 512,000 bytes, not 512 (a 1000x error in
#     the alarming direction, and the classic bug in wear scripts)
#   • one datapoint must NOT produce a projection — a garbage number here is
#     worse than no number
#   • history stays bounded but keeps its oldest anchor, so the GB/day window
#     grows instead of collapsing
#   • the state file is 0600 (it is drive telemetry keyed to this machine)
#   • --json emits valid JSON even when smartctl is missing, and the script
#     never fails the caller

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_DIR/scripts/ssd-wear.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== ssd-wear.sh (drive wear tracking) tests ==="
echo ""

# --- 1. The script itself ---------------------------------------------------
echo "Script:"
if [[ -x "$SCRIPT" ]]; then
  pass "scripts/ssd-wear.sh exists and is executable"
else
  fail "scripts/ssd-wear.sh missing or not executable"
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  exit 1
fi
if bash -n "$SCRIPT" 2>/dev/null; then
  pass "scripts/ssd-wear.sh passes bash -n"
else
  fail "scripts/ssd-wear.sh has a syntax error"
fi
if head -1 "$SCRIPT" | grep -q '/usr/bin/env bash'; then
  pass "uses #!/usr/bin/env bash"
else
  fail "shebang is not #!/usr/bin/env bash"
fi
if grep -q 'set -euo pipefail' "$SCRIPT"; then
  pass "runs under set -euo pipefail"
else
  fail "missing set -euo pipefail"
fi
# Read-only discipline: no self-tests, no attribute writes, ever.
if grep -qE 'smartctl[^|;&]*(-t |--test|--set|-C |--smart=|--offlineauto)' "$SCRIPT"; then
  fail "invokes a smartctl subcommand that writes/tests device state"
else
  pass "smartctl is only ever invoked read-only (-j -a)"
fi
# The state file must be written atomically by python, never by a bash redirect
# (a truncated history file is worse than no history file).
if grep -q 'os.replace' "$SCRIPT" && ! grep -qE '>[[:space:]]*"\$STATE_FILE"' "$SCRIPT"; then
  pass "state file is written atomically by python (os.replace), never by a bash redirect"
else
  fail "state file write is not an atomic python os.replace"
fi
if grep -q 'os.fchmod(fd, 0o600)' "$SCRIPT"; then
  pass "state file is created 0600 before any content is written"
else
  fail "state file is not chmod-ed before writing"
fi
if grep -q '512_000\|512000' "$SCRIPT"; then
  pass "NVMe data-unit constant (512,000 bytes) is present"
else
  fail "NVMe data-unit constant missing — data_units_written math cannot be right"
fi
# Serial numbers are identifiers and must never be printed or persisted.
if grep -q 'serial_number' "$SCRIPT" && grep -q 'sha256' "$SCRIPT"; then
  pass "serial is hashed, never emitted"
else
  fail "serial handling looks wrong (expected a hash, never the raw serial)"
fi

echo ""

# --- 2. Sandbox + stub smartctl ---------------------------------------------
TMPROOT="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-wear-test.XXXXXX")"
cleanup() { rm -rf "$TMPROOT"; }
trap cleanup EXIT

BIN="$TMPROOT/bin"
mkdir -p "$BIN" "$TMPROOT/state"
STATE="$TMPROOT/state/ssd-wear.json"

# Fixture: an NVMe drive with EXACTLY 1,000,000 data units written.
#   1,000,000 units * 512,000 bytes = 512,000,000,000 bytes = 512.0 GB
# If a script uses 512 bytes per unit it reports 0.5 GB instead — the whole
# point of this fixture.
make_stub() { # make_stub <data_units_written> <percentage_used>
  cat > "$BIN/smartctl" <<STUB
#!/bin/bash
# stub smartctl — prints a canned NVMe health log, opens no device.
for a in "\$@"; do [[ "\$a" == "--version" ]] && { echo "smartctl 7.5 (stub)"; exit 0; }; done
cat <<'JSON'
{
  "json_format_version": [1, 0],
  "smartctl": {"version": [7, 5], "exit_status": 0},
  "device": {"name": "/dev/nvme0n1", "type": "nvme", "protocol": "NVMe"},
  "model_name": "STUB NVMe 2TB",
  "serial_number": "STUBSERIAL123456",
  "smart_status": {"passed": true},
  "nvme_smart_health_information_log": {
    "critical_warning": 0,
    "percentage_used": $2,
    "data_units_read": 2000000,
    "data_units_written": $1,
    "media_errors": 0,
    "unsafe_shutdowns": 7,
    "power_on_hours": 4321
  }
}
JSON
STUB
  chmod +x "$BIN/smartctl"
}

run_wear() { # run_wear <args...> ; stub on PATH, state redirected
  PATH="$BIN:$PATH" OPENBEAST_WEAR_STATE="$STATE" \
  OPENBEAST_WEAR_MIN_INTERVAL="${MIN_INTERVAL:-0}" \
  OPENBEAST_WEAR_MIN_SPAN="${MIN_SPAN:-3600}" \
  OPENBEAST_WEAR_HISTORY="${HISTORY:-64}" \
  "$SCRIPT" "$@" 2>"$TMPROOT/stderr"
}

# --- 3. Flags ---------------------------------------------------------------
echo "Flags:"
if PATH="$BIN:$PATH" "$SCRIPT" --help 2>/dev/null | grep -q -- '--snapshot'; then
  pass "--help documents the modes"
else
  fail "--help does not document the modes"
fi
make_stub 1000000 3
set +e
PATH="$BIN:$PATH" OPENBEAST_WEAR_STATE="$STATE" "$SCRIPT" --bogus >/dev/null 2>&1
rc=$?
set -e
if [[ $rc -eq 2 ]]; then
  pass "unknown flag exits 2 (usage error)"
else
  fail "unknown flag exited $rc (expected 2)"
fi

echo ""

# --- 4. First run: math, no projection, file mode ---------------------------
echo "First datapoint:"
rm -f "$STATE"
set +e
out="$(run_wear)"
rc=$?
set -e
if [[ $rc -eq 0 ]]; then
  pass "human report exits 0"
else
  fail "human report exited $rc"
fi
# 1,000,000 data units == 512.0 GB. Any "0.5 GB" here is the 512-byte bug.
if echo "$out" | grep -q '512\.0 GB'; then
  pass "1,000,000 data units == 512.0 GB (1 unit = 512,000 bytes)"
else
  fail "data-unit math wrong — expected '512.0 GB', got: $(echo "$out" | grep -i written || echo none)"
fi
if echo "$out" | grep -qi 'run again later\|need another datapoint\|not enough history'; then
  pass "a single datapoint asks for another instead of projecting"
else
  fail "single-datapoint run did not say it needs more history"
fi
if ! echo "$out" | grep -qi '100% life used in'; then
  pass "no projection printed from one datapoint"
else
  fail "printed a projection from a single datapoint (garbage number)"
fi
if echo "$out" | grep -q 'STUBSERIAL123456'; then
  fail "the drive serial was printed"
else
  pass "the drive serial is not printed"
fi
if [[ -f "$STATE" ]]; then
  mode="$(stat -c '%a' "$STATE" 2>/dev/null || stat -f '%Lp' "$STATE")"
  if [[ "$mode" == "600" ]]; then
    pass "state file is mode 0600"
  else
    fail "state file is mode $mode (expected 600)"
  fi
  if ! grep -q 'STUBSERIAL123456' "$STATE"; then
    pass "the raw serial is not persisted to the state file"
  else
    fail "the raw serial leaked into the state file"
  fi
else
  fail "no state file written at $STATE"
fi

echo ""

# --- 5. --json --------------------------------------------------------------
echo "--json:"
set +e
js="$(run_wear --json)"
rc=$?
set -e
if [[ $rc -eq 0 ]] && echo "$js" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
  pass "--json emits valid JSON"
else
  fail "--json did not emit valid JSON"
fi
if echo "$js" | python3 -c '
import json,sys
d = json.load(sys.stdin)
devs = d["devices"]
assert devs, "no devices"
w = devs[0]["bytes_written"]
assert w == 512_000_000_000, f"bytes_written={w}"
assert devs[0]["percentage_used"] == 3
assert "serial" not in json.dumps(d).lower() or devs[0].get("id")
assert all("serial_number" not in k for k in devs[0])
' 2>/dev/null; then
  pass "--json reports bytes_written = 512,000,000,000 and percentage_used"
else
  fail "--json payload wrong (data-unit math or fields)"
fi

echo ""

# --- 6. Snapshot mode, deltas, projection -----------------------------------
echo "Snapshot + delta:"
set +e
snap="$(run_wear --snapshot)"
rc=$?
set -e
if [[ $rc -eq 0 ]] && [[ "$(echo "$snap" | wc -l)" -le 2 ]]; then
  pass "--snapshot exits 0 and stays quiet"
else
  fail "--snapshot was noisy or failed (rc=$rc)"
fi

# Backdate the anchor point 10 days and add a second, larger reading: now the
# tool has a real window and MUST produce a rate + projection.
rm -f "$STATE"
make_stub 1000000 3
run_wear --snapshot >/dev/null
python3 - "$STATE" <<'PY'
import json, sys, time
p = sys.argv[1]
s = json.load(open(p))
for e in s["devices"].values():
    e["points"][0]["t"] = time.time() - 10 * 86400
json.dump(s, open(p, "w"))
PY
# +100,000 data units = 100,000 * 512,000 B = 51.2 GB, over 10 days = 5.12 GB/day
make_stub 1100000 3
set +e
out2="$(run_wear)"
set -e
if echo "$out2" | grep -q '51\.2 GB over 10\.00 d' && echo "$out2" | grep -q '5\.1 GB/day'; then
  pass "delta rate correct (51.2 GB over 10 d = 5.1 GB/day)"
else
  fail "delta rate wrong: $(echo "$out2" | grep -i 'recent rate' || echo none)"
fi
if echo "$out2" | grep -qi '100% life used in'; then
  pass "projects a lifetime once there are two spaced datapoints"
else
  fail "no projection despite a 10-day window: $(echo "$out2" | grep -i projection || echo none)"
fi

# A counter that jumps BACKWARDS means a swapped drive / reset counters: the
# tool must drop the stale history rather than report a negative rate.
make_stub 10 3
set +e
out3="$(run_wear)"
set -e
if echo "$out3" | grep -q -- '-'  && echo "$out3" | grep -qi 'GB/day'; then
  fail "reported a rate after the write counter went backwards"
else
  pass "a backwards write counter resets history instead of reporting nonsense"
fi

# A healthy, readable drive must report status=ok — but a drive we could NOT
# read must stay "unknown", never a green ok.
if echo "$js" | python3 -c '
import json,sys
d = json.load(sys.stdin)
sys.exit(0 if d["devices"][0]["status"] == "ok" and d["status"] == "ok" else 1)' 2>/dev/null; then
  pass "a healthy readable drive reports status=ok"
else
  fail "healthy drive did not report status=ok"
fi

echo ""

# --- 6b. Concern thresholds (what doctor will key on) -----------------------
echo "Thresholds:"
rm -f "$STATE"
make_stub 1000000 85          # >= 80% life used
set +e
worn="$(run_wear --json)"
set -e
if echo "$worn" | python3 -c '
import json,sys
d = json.load(sys.stdin)
sys.exit(0 if d["status"] == "warn" and any("85%" in c for c in d["concerns"]) else 1)' 2>/dev/null; then
  pass "percentage_used >= 80 raises a concern and flips status=warn"
else
  fail "an 85%-worn drive did not raise a concern"
fi

# media_errors > 0 must warn even on an otherwise pristine drive.
rm -f "$STATE"
make_stub 1000000 1
sed -i 's/"media_errors": 0/"media_errors": 4/' "$BIN/smartctl"
set +e
errs="$(run_wear --json)"
set -e
if echo "$errs" | python3 -c '
import json,sys
d = json.load(sys.stdin)
sys.exit(0 if d["status"] == "warn" and any("media" in c for c in d["concerns"]) else 1)' 2>/dev/null; then
  pass "media_errors > 0 raises a concern"
else
  fail "media errors did not raise a concern"
fi

echo ""

# --- 6c. SATA/ATA fallback --------------------------------------------------
# Not every OpenBeast box is all-NVMe. The ATA path reads a different table
# with different units: life used comes from a REMAINING-life attribute
# (used = 100 - value) and writes come from Total_LBAs_Written * the logical
# block size — 2,000,000,000 LBAs * 512 B = 1.024 TB.
echo "SATA/ATA fallback:"
rm -f "$STATE"
cat > "$BIN/smartctl" <<'STUB'
#!/bin/bash
cat <<'JSON'
{
  "json_format_version": [1, 0],
  "smartctl": {"exit_status": 0},
  "device": {"name": "/dev/sda", "type": "sat", "protocol": "ATA"},
  "model_name": "STUB SATA SSD 1TB",
  "serial_number": "SATASERIAL9",
  "smart_status": {"passed": true},
  "logical_block_size": 512,
  "ata_smart_attributes": {"table": [
    {"id": 5,   "name": "Reallocated_Sector_Ct", "value": 100, "raw": {"value": 0}},
    {"id": 9,   "name": "Power_On_Hours",        "value": 99,  "raw": {"value": 12345}},
    {"id": 177, "name": "Wear_Leveling_Count",   "value": 94,  "raw": {"value": 120}},
    {"id": 241, "name": "Total_LBAs_Written",    "value": 99,  "raw": {"value": 2000000000}}
  ]}
}
JSON
STUB
chmod +x "$BIN/smartctl"
set +e
ata="$(run_wear --json)"
set -e
if echo "$ata" | python3 -c '
import json,sys
d = json.load(sys.stdin)
r = d["devices"][0]
assert r["type"] == "ata", r["type"]
assert r["percentage_used"] == 6, r["percentage_used"]          # 100 - 94
assert r["bytes_written"] == 1_024_000_000_000, r["bytes_written"]  # 2e9 LBAs * 512
assert r["power_on_hours"] == 12345
assert r["media_errors"] == 0
' 2>/dev/null; then
  pass "ATA table parsed: life used 6%, Total_LBAs_Written * 512 = 1.024 TB"
else
  fail "ATA fallback parsed wrong: $(echo "$ata" | python3 -c 'import json,sys; print(json.load(sys.stdin)["devices"][0])' 2>/dev/null)"
fi
# A drive with no recognizable write counter must say so, not report zero.
cat > "$BIN/smartctl" <<'STUB'
#!/bin/bash
cat <<'JSON'
{"json_format_version":[1,0],"smartctl":{"exit_status":0},
 "device":{"name":"/dev/sda","type":"sat"},"model_name":"STUB ODD SATA",
 "ata_smart_attributes":{"table":[{"id":9,"name":"Power_On_Hours","value":99,"raw":{"value":10}}]}}
JSON
STUB
chmod +x "$BIN/smartctl"
rm -f "$STATE"
set +e
odd="$(run_wear)"
rc=$?
set -e
if [[ $rc -eq 0 ]] && echo "$odd" | grep -qi 'unavailable\|does not report'; then
  pass "a drive with no write counter says so instead of reporting 0"
else
  fail "missing write counter was not reported honestly (rc=$rc)"
fi

echo ""

# --- 6d. --doctor line protocol ---------------------------------------------
# doctor.sh consumes "status|message|fix" lines with a read loop, so the shape
# is a contract: exactly one line per device, only ok/warn (advisory tooling
# must never contribute a FAIL to doctor's verdict), and no human chatter on
# stdout to corrupt the loop.
echo "--doctor:"
rm -f "$STATE"
make_stub 1000000 3
set +e
doc="$(run_wear --doctor)"
rc=$?
set -e
if [[ $rc -eq 0 && "$(echo "$doc" | wc -l)" -eq 1 ]] && echo "$doc" | grep -q '^ok|/dev/'; then
  pass "--doctor emits one ok|message|fix line per device"
else
  fail "--doctor line shape wrong: $doc"
fi
if echo "$doc" | grep -q '512\.0 GB written'; then
  pass "--doctor line carries the write total"
else
  fail "--doctor line lost the write total: $doc"
fi
rm -f "$STATE"
make_stub 1000000 91
set +e
docw="$(run_wear --doctor)"
set -e
if echo "$docw" | grep -q '^warn|' && echo "$docw" | grep -q 'ssd-wear.sh$'; then
  pass "--doctor warns on a worn drive and carries a fix hint"
else
  fail "--doctor did not warn on a 91%-worn drive: $docw"
fi
if echo "$docw" | grep -qc '^fail|' >/dev/null && echo "$docw" | grep -q '^fail|'; then
  fail "--doctor emitted a fail| line (advisory tooling must never FAIL doctor)"
else
  pass "--doctor never emits fail| (advisory, never a gate)"
fi
export OPENBEAST_SMARTCTL="$TMPROOT/definitely-not-installed"
set +e
docm="$(OPENBEAST_WEAR_STATE="$STATE" "$SCRIPT" --doctor 2>/dev/null)"
rc=$?
set -e
unset OPENBEAST_SMARTCTL
if [[ $rc -eq 0 ]] && echo "$docm" | grep -q '^warn|.*smartctl'; then
  pass "--doctor degrades to a single warn| row when smartctl is absent"
else
  fail "--doctor degraded badly with no smartctl (rc=$rc): $docm"
fi

echo ""

# --- 7. History is bounded --------------------------------------------------
echo "History:"
rm -f "$STATE"
HISTORY=8
for i in $(seq 1 20); do
  make_stub $((1000000 + i * 1000)) 3
  run_wear --snapshot >/dev/null
done
unset HISTORY
n="$(python3 -c '
import json,sys
s = json.load(open(sys.argv[1]))
print(max(len(e["points"]) for e in s["devices"].values()))
' "$STATE")"
if [[ "$n" -le 8 ]]; then
  pass "history bounded to the configured maximum ($n <= 8 points)"
else
  fail "history grew unbounded ($n points)"
fi
# The oldest point is an anchor: pruning must drop from the middle, or the
# GB/day window collapses to the last few runs.
if python3 -c '
import json,sys
s = json.load(open(sys.argv[1]))
e = next(iter(s["devices"].values()))
pts = e["points"]
# first snapshot of the loop was 1,001,000 data units; stored in BYTES
sys.exit(0 if pts[0]["w"] == 1_001_000 * 512_000 else 1)
' "$STATE" 2>/dev/null; then
  pass "the oldest datapoint is retained as an anchor when pruning"
else
  fail "pruning discarded the oldest anchor point"
fi

echo ""

# --- 8. Degrade gracefully --------------------------------------------------
echo "Degradation:"
# smartctl absent entirely: advisory tooling must still exit 0 AND --json must
# still be parseable, or every caller that pipes us breaks. Point the tool at a
# binary that does not exist rather than emptying PATH — an empty PATH would
# also hide df/awk/mktemp and prove nothing.
export OPENBEAST_SMARTCTL="$TMPROOT/definitely-not-installed"
set +e
noout="$(OPENBEAST_WEAR_STATE="$STATE" "$SCRIPT" 2>&1)"
rc=$?
set -e
if [[ $rc -eq 0 ]]; then
  pass "missing smartctl exits 0 (advisory, never a gate)"
else
  fail "missing smartctl exited $rc"
fi
if echo "$noout" | grep -q 'smartmontools'; then
  pass "missing smartctl names the package to install"
else
  fail "missing smartctl does not say what to install"
fi
set +e
nojson="$(OPENBEAST_WEAR_STATE="$STATE" "$SCRIPT" --json 2>/dev/null)"
rc=$?
set -e
if [[ $rc -eq 0 ]] && echo "$nojson" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["status"]=="unknown" else 1)' 2>/dev/null; then
  pass "--json still emits valid JSON with status=unknown when smartctl is absent"
else
  fail "--json broke when smartctl was absent"
fi

unset OPENBEAST_SMARTCTL

# Permission denied: exit 0, and say how to fix it.
cat > "$BIN/smartctl" <<'STUB'
#!/bin/bash
echo '{"json_format_version":[1,0],"smartctl":{"exit_status":2,"messages":[{"string":"Smartctl open device: /dev/nvme0n1 failed: Permission denied","severity":"error"}]}}'
exit 2
STUB
chmod +x "$BIN/smartctl"
set +e
denied="$(PATH="$BIN:$PATH" OPENBEAST_WEAR_STATE="$STATE" "$SCRIPT" 2>&1)"
rc=$?
set -e
if [[ $rc -eq 0 ]] && echo "$denied" | grep -qi 'root'; then
  pass "permission denied exits 0 and explains that smartctl needs root"
else
  fail "permission-denied path wrong (rc=$rc)"
fi
if echo "$denied" | grep -q 'sudo -n\|NOPASSWD'; then
  pass "permission denied suggests a non-interactive sudo route"
else
  fail "permission denied gives no unattended fix"
fi

# An unsupported device (valid smartctl, no SMART data) must not stop the run.
cat > "$BIN/smartctl" <<'STUB'
#!/bin/bash
echo '{"json_format_version":[1,0],"smartctl":{"exit_status":4,"messages":[{"string":"Unknown USB bridge","severity":"error"}]}}'
exit 4
STUB
chmod +x "$BIN/smartctl"
set +e
PATH="$BIN:$PATH" OPENBEAST_WEAR_STATE="$STATE" "$SCRIPT" >/dev/null 2>&1
rc=$?
set -e
if [[ $rc -eq 0 ]]; then
  pass "an unsupported device does not fail the run"
else
  fail "unsupported device exited $rc"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
