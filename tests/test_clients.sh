#!/bin/bash
# Per-device enrollment/revocation CLI (scripts/clients.sh) — behavior tests.
#
# Usage: ./tests/test_clients.sh
#
# Everything runs against a THROWAWAY repo under $TMPDIR: the real
# .run/clients.json is never read or written, and no service is contacted.
# The CLI is exercised for real (not grepped), because the load-bearing
# properties here are runtime ones: the plaintext key must never reach the
# registry, the file must never be group/world-readable, and a rewrite must
# not drop fields a newer agents/edge.py wrote (last_seen, future fields).

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== clients.sh (per-device enrollment) tests ==="
echo ""

# --- 1. The script itself ---
echo "Script:"
if [[ -x "$REPO_DIR/scripts/clients.sh" ]]; then
  pass "scripts/clients.sh exists and is executable"
else
  fail "scripts/clients.sh missing or not executable"
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  exit 1
fi
if bash -n "$REPO_DIR/scripts/clients.sh" 2>/dev/null; then
  pass "scripts/clients.sh passes bash -n"
else
  fail "scripts/clients.sh has a syntax error"
fi
# Bash 3.2 (stock macOS) has no mapfile/readarray/associative arrays — the
# CLI ships to client Macs too.
if ! grep -qE '(^|[^[:alnum:]_])(mapfile|readarray)([^[:alnum:]_]|$)|declare[[:space:]]+-A' \
     "$REPO_DIR/scripts/clients.sh"; then
  pass "no bash-4-only constructs (mapfile/readarray/declare -A)"
else
  fail "clients.sh uses a bash-4-only construct (breaks stock macOS bash 3.2)"
fi

# --- 2. Isolated sandbox ---
TMPROOT="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-clients-test.XXXXXX")"
cleanup() { rm -rf "$TMPROOT"; }
trap cleanup EXIT

SANDBOX="$TMPROOT/repo"
mkdir -p "$SANDBOX/scripts/lib" "$TMPROOT/home"
cp "$REPO_DIR/scripts/clients.sh" "$SANDBOX/scripts/"
cp "$REPO_DIR"/scripts/lib/*.sh "$SANDBOX/scripts/lib/"
export HOME="$TMPROOT/home"          # conf.sh derives paths from $HOME
CLI="$SANDBOX/scripts/clients.sh"
REG="$SANDBOX/.run/clients.json"

_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
# Query the registry with python (never hand-parse JSON in the test either).
_q() { # _q <python-expr over `doc`>
  python3 -c '
import json, sys
doc = json.load(open(sys.argv[1]))
devs = doc["devices"]
def dev(i):
    return devs[i]
print(eval(sys.argv[2]))
' "$REG" "$1"
}
_sha() { printf '%s' "$1" | { sha256sum 2>/dev/null || shasum -a 256; } | awk '{print $1}'; }

# --- 3. Empty state ---
echo ""
echo "Empty state:"
if out="$("$CLI" list 2>&1)" && echo "$out" | grep -q "No devices enrolled yet"; then
  pass "list with no registry prints the friendly 'enroll one' hint"
else
  fail "list with no registry unfriendly: $out"
fi
if ! "$CLI" revoke ghost >/dev/null 2>&1; then
  pass "revoke with no registry exits non-zero"
else
  fail "revoke with no registry exited 0"
fi

# --- 4. enroll ---
echo ""
echo "enroll:"
ENROLL_OUT="$("$CLI" enroll laptop-air --label "Max's MacBook Air" --slot 0 --rate 60)"
KEY="$(printf '%s' "$ENROLL_OUT" | grep -Eo '[0-9a-f]{64}' | head -1)"
if [[ ${#KEY} -eq 64 ]]; then
  pass "enroll prints a 32-byte hex key"
else
  fail "enroll printed no 64-char hex key"
fi
if echo "$ENROLL_OUT" | grep -qi "not recoverable" \
   && echo "$ENROLL_OUT" | grep -q -- "./scripts/setup-client.sh --host" \
   && echo "$ENROLL_OUT" | grep -q -- "--api-key $KEY"; then
  pass "enroll warns the key is unrecoverable + prints the setup-client command"
else
  fail "enroll output missing the copy-now notice or the client command"
fi
if [[ -f "$REG" ]]; then
  pass "registry created at .run/clients.json"
else
  fail "registry not created"
fi
if [[ "$(_mode "$REG")" == "600" ]]; then
  pass "registry mode is 600"
else
  fail "registry mode is $(_mode "$REG"), want 600"
fi

# THE property: the plaintext key must exist nowhere on disk.
if ! grep -qF "$KEY" "$REG"; then
  pass "plaintext key is NOT in the registry"
else
  fail "PLAINTEXT KEY LEAKED INTO THE REGISTRY"
fi
if ! grep -rqF "$KEY" "$SANDBOX" 2>/dev/null; then
  pass "plaintext key is nowhere under the repo (no stray temp/log file)"
else
  fail "plaintext key found in a file under the sandbox repo"
fi
if [[ "$(_q 'dev(0)["key_sha256"]')" == "$(_sha "$KEY")" ]]; then
  pass "stored key_sha256 is the sha256 of the printed key"
else
  fail "stored hash does not match sha256(key)"
fi

# Contract shape.
MISSING="$(_q '",".join(k for k in ["id","label","key_sha256","enrolled_at","revoked_at","slot","rate_limit_per_min","last_seen"] if k not in dev(0))')"
if [[ -z "$MISSING" ]]; then
  pass "device row carries every field of the version-1 contract"
else
  fail "device row missing contract fields: $MISSING"
fi
if [[ "$(_q 'doc["version"]')" == "1" ]]; then
  pass "registry declares version 1"
else
  fail "registry version is $(_q 'doc["version"]'), want 1"
fi
if [[ "$(_q 'dev(0)["slot"]')" == "0" && "$(_q 'dev(0)["rate_limit_per_min"]')" == "60" ]]; then
  pass "--slot / --rate land as integers"
else
  fail "--slot / --rate not stored as given"
fi
if [[ "$(_q 'dev(0)["revoked_at"] is None')" == "True" && "$(_q 'dev(0)["last_seen"] is None')" == "True" ]]; then
  pass "revoked_at / last_seen start null"
else
  fail "revoked_at / last_seen not null on a fresh enroll"
fi

# --- 5. list / show never print key material ---
echo ""
echo "list / show:"
LIST_OUT="$("$CLI" list)"
if echo "$LIST_OUT" | grep -q "laptop-air" && echo "$LIST_OUT" | grep -q "Max's MacBook Air" \
   && echo "$LIST_OUT" | grep -q "active"; then
  pass "list shows the device, its label and 'active'"
else
  fail "list output missing the enrolled device"
fi
if ! echo "$LIST_OUT" | grep -qF "$KEY" && ! echo "$LIST_OUT" | grep -qF "$(_sha "$KEY")"; then
  pass "list prints neither the key nor the full hash"
else
  fail "list leaked key material"
fi
SHOW_OUT="$("$CLI" show laptop-air)"
if ! echo "$SHOW_OUT" | grep -qF "$KEY" && ! echo "$SHOW_OUT" | grep -qF "$(_sha "$KEY")" \
   && echo "$SHOW_OUT" | grep -q "laptop-air"; then
  pass "show prints the device without key material"
else
  fail "show leaked key material (or lost the device)"
fi
if "$CLI" list --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["devices"][0]["id"]=="laptop-air"; assert "key_sha256" not in d["devices"][0]; assert len(d["devices"][0]["key_sha256_prefix"])==8; assert d["devices"][0]["status"]=="active"' 2>/dev/null; then
  pass "list --json parses, is redacted to an 8-char hash prefix, carries status"
else
  fail "list --json malformed or unredacted"
fi
if "$CLI" show laptop-air --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["id"]=="laptop-air"; assert "key_sha256" not in d' 2>/dev/null; then
  pass "show --json parses and is redacted"
else
  fail "show --json malformed or unredacted"
fi

# --- 6. Duplicates and rotation ---
echo ""
echo "duplicate / rotate:"
HASH_BEFORE="$(_q 'dev(0)["key_sha256"]')"
ENROLLED_AT="$(_q 'dev(0)["enrolled_at"]')"
if ! DUP_OUT="$("$CLI" enroll laptop-air 2>&1)"; then
  pass "duplicate enroll is refused"
else
  fail "duplicate enroll succeeded: $DUP_OUT"
fi
if [[ "$(_q 'dev(0)["key_sha256"]')" == "$HASH_BEFORE" ]]; then
  pass "refused duplicate left the existing key untouched"
else
  fail "refused duplicate still mutated the registry"
fi
ROT_OUT="$("$CLI" enroll laptop-air --force)"
ROT_KEY="$(printf '%s' "$ROT_OUT" | grep -Eo '[0-9a-f]{64}' | head -1)"
if [[ ${#ROT_KEY} -eq 64 && "$ROT_KEY" != "$KEY" ]]; then
  pass "--force issues a NEW key"
else
  fail "--force did not issue a new key"
fi
if [[ "$(_q 'dev(0)["key_sha256"]')" == "$(_sha "$ROT_KEY")" ]]; then
  pass "--force stored the new key's hash"
else
  fail "--force did not store the new hash"
fi
if [[ "$(_q 'dev(0)["enrolled_at"]')" == "$ENROLLED_AT" \
   && "$(_q 'dev(0)["label"]')" == "Max's MacBook Air" \
   && "$(_q 'dev(0)["slot"]')" == "0" \
   && "$(_q 'len(devs)')" == "1" ]]; then
  pass "--force preserves enrolled_at, label, slot and does not duplicate the row"
else
  fail "--force clobbered enrolled_at/label/slot (or duplicated the device)"
fi
ALIAS_KEY="$("$CLI" rotate laptop-air | grep -Eo '[0-9a-f]{64}' | head -1)"
if [[ "$(_q 'dev(0)["key_sha256"]')" == "$(_sha "$ALIAS_KEY")" ]]; then
  pass "rotate is a working alias for enroll --force"
else
  fail "rotate alias did not rotate the key"
fi

# --- 7. Revocation ---
echo ""
echo "revoke / unrevoke:"
REV_OUT="$("$CLI" revoke laptop-air)"
if echo "$REV_OUT" | grep -qi "no restart"; then
  pass "revoke reminds that the gate hot-reloads (no restart)"
else
  fail "revoke output missing the hot-reload reminder"
fi
if [[ "$(_q 'dev(0)["revoked_at"] is not None')" == "True" ]] \
   && "$CLI" list | grep -q "REVOKED"; then
  pass "revoke sets revoked_at and list reports REVOKED"
else
  fail "revoke did not flip the status"
fi
if [[ "$(_q 'len(devs)')" == "1" ]]; then
  pass "revoked device STAYS in the registry (audit trail)"
else
  fail "revoke deleted the device row"
fi
if [[ "$(_q 'dev(0)["key_sha256"]')" == "$(_sha "$ALIAS_KEY")" ]]; then
  pass "revoke leaves the key hash intact"
else
  fail "revoke mutated the key hash"
fi
"$CLI" unrevoke laptop-air >/dev/null
if [[ "$(_q 'dev(0)["revoked_at"] is None')" == "True" ]] && "$CLI" list | grep -q "active"; then
  pass "unrevoke clears revoked_at"
else
  fail "unrevoke did not restore the device"
fi

# --- 8. Round-tripping fields this CLI does not know about ---
echo ""
echo "forward compatibility (edge.py writes here too):"
python3 -c '
import json, sys
p = sys.argv[1]
doc = json.load(open(p))
doc["devices"][0]["last_seen"] = "2026-07-30T19:00:00Z"
doc["devices"][0]["future_field"] = {"nested": ["a", 1]}
doc["future_top_level"] = "keep-me"
json.dump(doc, open(p, "w"), indent=2)
' "$REG"
"$CLI" revoke laptop-air >/dev/null
if [[ "$(_q 'dev(0)["last_seen"]')" == "2026-07-30T19:00:00Z" ]]; then
  pass "last_seen (written by edge.py) survives a CLI rewrite"
else
  fail "CLI rewrite DROPPED last_seen"
fi
if [[ "$(_q 'dev(0)["future_field"]["nested"]')" == "['a', 1]" ]]; then
  pass "unknown device field survives a CLI rewrite"
else
  fail "CLI rewrite dropped an unknown device field"
fi
if [[ "$(_q 'doc["future_top_level"]')" == "keep-me" ]]; then
  pass "unknown top-level field survives a CLI rewrite"
else
  fail "CLI rewrite dropped an unknown top-level field"
fi
if [[ "$(_mode "$REG")" == "600" ]]; then
  pass "registry is still mode 600 after a rewrite"
else
  fail "rewrite left the registry at mode $(_mode "$REG")"
fi
if [[ -z "$(find "$SANDBOX/.run" -name '.clients.json.*' 2>/dev/null)" ]]; then
  pass "atomic rewrite leaves no temp file behind"
else
  fail "temp file left in .run/ after a rewrite"
fi
"$CLI" unrevoke laptop-air >/dev/null

# --- 9. Multiple devices + independence ---
echo ""
echo "multi-device:"
SECOND_KEY="$("$CLI" enroll rig-mini --label "Mac mini" | grep -Eo '[0-9a-f]{64}' | head -1)"
if [[ "$(_q 'len(devs)')" == "2" ]] && [[ "$SECOND_KEY" != "$ALIAS_KEY" ]]; then
  pass "a second device enrolls with its own key"
else
  fail "second enroll did not add an independent device"
fi
"$CLI" revoke rig-mini >/dev/null
if [[ "$(_q '[d["revoked_at"] is None for d in devs]')" == "[True, False]" ]]; then
  pass "revoking one device does not touch the other"
else
  fail "revocation bled across devices"
fi

# --- 10. Input validation and destructive-op guards ---
echo ""
echo "guards:"
BADIDS_OK=1
for bad in "Bad_ID" "-leading" "has space" "way-too-long-device-identifier-abcdefghijklmnop" ""; do
  if "$CLI" enroll "$bad" >/dev/null 2>&1; then BADIDS_OK=0; fi
done
if [[ $BADIDS_OK -eq 1 ]]; then
  pass "invalid ids are rejected (case, leading dash, spaces, length, empty)"
else
  fail "an invalid id was accepted"
fi
if ! "$CLI" enroll ok-id --slot notanint >/dev/null 2>&1 && ! "$CLI" enroll ok-id --rate -3 >/dev/null 2>&1; then
  pass "non-integer --slot / --rate are rejected"
else
  fail "non-integer --slot / --rate accepted"
fi
if ! "$CLI" show nosuchdevice >/dev/null 2>&1 && ! "$CLI" revoke nosuchdevice >/dev/null 2>&1; then
  pass "operations on an unknown id exit non-zero"
else
  fail "unknown id treated as success"
fi
if ! RM_OUT="$("$CLI" remove rig-mini 2>&1)" && echo "$RM_OUT" | grep -qi "audit"; then
  pass "remove without --yes refuses and warns about the audit trail"
else
  fail "remove without --yes was not refused"
fi
if [[ "$(_q 'len(devs)')" == "2" ]]; then
  pass "refused remove left the device in place"
else
  fail "refused remove still deleted the row"
fi
"$CLI" remove rig-mini --yes >/dev/null
if [[ "$(_q 'len(devs)')" == "1" && "$(_q 'dev(0)["id"]')" == "laptop-air" ]]; then
  pass "remove --yes hard-deletes exactly that device"
else
  fail "remove --yes deleted the wrong row(s)"
fi

# --- 11. A corrupt registry is never clobbered ---
echo ""
echo "corrupt registry:"
cp "$REG" "$TMPROOT/good.json"
echo '{ this is not json' > "$REG"
if ! "$CLI" list >/dev/null 2>&1 && ! "$CLI" enroll another >/dev/null 2>&1; then
  pass "malformed registry makes the CLI fail loudly"
else
  fail "CLI carried on over a malformed registry"
fi
if [[ "$(cat "$REG")" == '{ this is not json' ]]; then
  pass "malformed registry is left untouched (never truncated/overwritten)"
else
  fail "CLI overwrote a malformed registry"
fi
cp "$TMPROOT/good.json" "$REG"

# --- Summary ---
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[[ $FAIL -eq 0 ]]
