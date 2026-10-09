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
   && echo "$ENROLL_OUT" | grep -q -- "--api-key-stdin"; then
  pass "enroll warns the key is unrecoverable + prints the setup-client command"
else
  fail "enroll output missing the copy-now notice or the client command"
fi
# The printed command must never carry the key itself: pasted as-is it lands
# in shell history and in the client's world-readable /proc/<pid>/cmdline.
if echo "$ENROLL_OUT" | grep -q -- "--api-key $KEY"; then
  fail "enroll prints the key on a setup-client command line (--api-key <key>)"
else
  pass "enroll's setup-client command keeps the key off argv (--api-key-stdin)"
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
MISSING="$(_q '",".join(k for k in ["id","label","key_sha256","enrolled_at","revoked_at","slot","rate_limit_per_min","last_seen","scopes"] if k not in dev(0))')"
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
# TYPE, not just value: agents/edge.py does isinstance(slot, int) to decide
# whether to inject affinity, so a "0" string would silently disable slot
# pinning while this assertion still passed on the printed value alone.
if [[ "$(_q 'type(dev(0)["slot"]).__name__')" == "int" \
   && "$(_q 'type(dev(0)["rate_limit_per_min"]).__name__')" == "int" \
   && "$(_q 'dev(0)["slot"]')" == "0" \
   && "$(_q 'dev(0)["rate_limit_per_min"]')" == "60" ]]; then
  pass "--slot / --rate land as JSON integers (the type edge.py requires)"
else
  fail "--slot / --rate wrong value or type: slot=$(_q 'repr(dev(0)["slot"])') rate=$(_q 'repr(dev(0)["rate_limit_per_min"])')"
fi
# A JSON bool is an int in Python — edge.py must not treat true/false as a
# slot index, and the CLI must never write one.
if [[ "$(_q 'isinstance(dev(0)["slot"], bool)')" == "False" ]]; then
  pass "--slot is not a JSON boolean"
else
  fail "--slot stored as a boolean — edge.py would inject id_slot=True"
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

# --- 5b. Device scopes (beast-chat write auth, docs/BEAST_CHAT.md) ---
#
# A scope is the difference between a device that may WATCH the rig and one
# that may ACT on it (send a message to a live agent, stop it, start a new
# one — remote code execution). So the properties under test are: a grant is
# only ever explicit, absence is never a grant, and revoking one takes no
# re-enrollment.
echo ""
echo "scopes:"
# A device's scopes as a plain comma-joined string — comparing python reprs
# through two layers of shell quoting is how this section first went wrong.
_scopes() { _q '",".join([d for d in devs if d["id"]=="'"$1"'"][0].get("scopes") or [])'; }
# Devices enrolled before scopes existed have no `scopes` key at all. That row
# must round-trip AND read as "no scopes" — never as an unknown that some
# consumer decides to treat generously.
python3 - "$REG" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
doc["devices"].append({
    "id": "legacy-box", "label": "pre-scopes device",
    "key_sha256": "f" * 64, "enrolled_at": "2026-01-01T00:00:00Z",
    "revoked_at": None, "slot": None, "rate_limit_per_min": None,
    "last_seen": None,
})
json.dump(doc, open(sys.argv[1], "w"), indent=2)
PY
if "$CLI" show legacy-box | grep -qE '^[[:space:]]*scopes:[[:space:]]+-[[:space:]]*$'; then
  pass "a device with no scopes field reports no scopes"
else
  fail "a pre-scopes device did not report an empty scope list"
fi
if "$CLI" show legacy-box --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert not d.get("scopes")' 2>/dev/null; then
  pass "show --json reports no scopes for a pre-scopes device"
else
  fail "show --json invented scopes for a pre-scopes device"
fi

PHONE_OUT="$("$CLI" enroll phone --label "Max's phone" --scope chat)"
PHONE_KEY="$(printf '%s' "$PHONE_OUT" | grep -Eo '[0-9a-f]{64}' | head -1)"
if [[ "$(_scopes phone)" == "chat" ]]; then
  pass "enroll --scope chat persists the scope"
else
  fail "enroll --scope did not persist: $(_scopes phone)"
fi
# The key discipline must survive the new flag: the plaintext is shown on
# stdout at enroll time and NOWHERE else, ever again — not in the registry,
# not in a later `show`, not in a `scope` grant.
if echo "$PHONE_OUT" | grep -qi "not recoverable" \
   && ! grep -qF "$PHONE_KEY" "$REG" \
   && ! "$CLI" show phone | grep -qF "$PHONE_KEY" \
   && ! "$CLI" scope phone add extra 2>&1 | grep -qF "$PHONE_KEY"; then
  pass "the plaintext key is printed only at enroll — never by show or scope"
else
  fail "a scoped device's key was recoverable after enrollment"
fi
"$CLI" scope phone remove extra >/dev/null
if ! grep -rqF "$PHONE_KEY" "$SANDBOX" 2>/dev/null; then
  pass "a scoped device's plaintext key is still nowhere on disk"
else
  fail "PLAINTEXT KEY LEAKED after enrolling with a scope"
fi
if "$CLI" list | grep -q "chat"; then
  pass "list shows the SCOPES column"
else
  fail "list does not show scopes"
fi
if "$CLI" enroll multi --scope chat --scope admin >/dev/null \
   && [[ "$(_scopes multi)" == "chat,admin" ]]; then
  pass "--scope is repeatable and preserves order"
else
  fail "repeated --scope did not accumulate"
fi
if ! "$CLI" enroll bad-scope --scope "Chat Admin" >/dev/null 2>&1; then
  pass "an invalid scope name is refused at enroll"
else
  fail "an invalid scope name was accepted"
fi
if "$CLI" scope legacy-box add chat >/dev/null \
   && [[ "$(_scopes legacy-box)" == "chat" ]]; then
  pass "scope add grants a scope to a device that never had the field"
else
  fail "scope add did not grant the scope"
fi
if "$CLI" scope legacy-box add chat 2>&1 | grep -q "already has scope" \
   && [[ "$(_scopes legacy-box)" == "chat" ]]; then
  pass "granting an existing scope is an idempotent no-op"
else
  fail "a repeated scope add duplicated the entry"
fi
if "$CLI" scope legacy-box remove chat >/dev/null \
   && [[ -z "$(_scopes legacy-box)" ]]; then
  pass "scope remove revokes it"
else
  fail "scope remove did not revoke the scope"
fi
if "$CLI" scope legacy-box remove chat 2>&1 | grep -q "does not have scope"; then
  pass "removing an absent scope is an idempotent no-op"
else
  fail "removing an absent scope was not reported as a no-op"
fi
if ! "$CLI" scope legacy-box grant chat >/dev/null 2>&1 \
   && ! "$CLI" scope ghost-device add chat >/dev/null 2>&1; then
  pass "scope rejects a bad action and an unknown device"
else
  fail "scope accepted a bad action or an unknown device"
fi
# Rotating a scoped device must not silently drop its grants — the key
# changes, the authorization does not.
"$CLI" rotate phone >/dev/null
if [[ "$(_scopes phone)" == "chat" ]]; then
  pass "rotate preserves a device's scopes"
else
  fail "rotate dropped the device's scopes"
fi
if "$CLI" rotate phone --scope admin >/dev/null \
   && [[ "$(_scopes phone)" == "admin" ]]; then
  pass "rotate --scope REPLACES the list (the only way to say 'these and no others')"
else
  fail "rotate --scope did not replace the scope list"
fi
# Put the fixtures back so the sections below still see exactly one device.
"$CLI" remove phone --yes >/dev/null
"$CLI" remove multi --yes >/dev/null
"$CLI" remove legacy-box --yes >/dev/null
if [[ "$(_q 'len(devs)')" == "1" ]]; then
  pass "scope fixtures cleaned up; the original device is untouched"
else
  fail "scope fixtures left $(_q 'len(devs)') devices behind"
fi
if [[ "$(_q 'dev(0)["id"]')" == "laptop-air" && -z "$(_scopes laptop-air)" ]]; then
  pass "an unscoped device stayed unscoped through all of the above"
else
  fail "the unscoped device picked up a scope it was never granted"
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

# --- 12. `update` re-syncs the opencode catalog (2026-08-19) ---
# Regression: setup-client.sh copied the model list ONCE at install, and update
# only pulled source + deps — so the client picker froze on install day. A model
# added to the rig never appeared, and models whose weights were deleted never
# left. Both were live simultaneously. The refresh must also be SURGICAL: it
# runs on every update, so it can never clobber the baseURL, the key, the MCP
# block, or the user's own config, and must not widen a 0600 file.
echo ""
echo "opencode catalog refresh:"
OCDIR="$TMPROOT/oc"; mkdir -p "$OCDIR"; OCFG="$OCDIR/opencode.json"
python3 -c "
import json,sys
json.dump({'\$schema':'https://opencode.ai/config.json',
 'mcp':{'openbeast-tools':{'environment':{'OPENBEAST_API_KEY':'SEKRIT'}}},
 'provider':{'openbeast-rig':{'options':{'baseURL':'http://127.0.0.1:59999/v1','apiKey':'not-needed'},
   'models':{'ghost-deleted-model':{'name':'gone'}}}},
 'model':'openbeast-rig/ghost-deleted-model','theme':'user-choice'},
 open(sys.argv[1],'w'),indent=2)" "$OCFG"
chmod 600 "$OCFG"
# Drive the function in isolation (no git pull, no venv) against a dead rig, so
# the assertion is about the catalog merge and not about network reachability.
RF="$(sed -n '/^_refresh_oc_catalog() {/,/^}/p' "$REPO_DIR/scripts/client.sh")"
bash -c "OC_CONFIG='$OCFG'; REPO='$REPO_DIR'; PY_BIN=python3
$RF
_refresh_oc_catalog" >/dev/null 2>&1

_ocq() { python3 -c "
import json,sys
c=json.load(open('$OCFG')); print(eval(sys.argv[1]))" "$1"; }

if [[ "$(_ocq "'ghost-deleted-model' in c['provider']['openbeast-rig']['models']")" == "False" ]]; then
  pass "refresh drops catalog entries the checkout no longer ships"
else
  fail "a model absent from the checkout survived the refresh"
fi
if [[ "$(_ocq "'qwen38-27b-uncensored-mtp-q5' in c['provider']['openbeast-rig']['models']")" == "True" ]]; then
  pass "refresh picks up models added to the checkout since install"
else
  fail "refresh did not add the checkout's current models"
fi
if [[ "$(_ocq "c['mcp']['openbeast-tools']['environment']['OPENBEAST_API_KEY']")" == "SEKRIT" \
   && "$(_ocq "c['provider']['openbeast-rig']['options']['baseURL']")" == "http://127.0.0.1:59999/v1" \
   && "$(_ocq "c.get('theme')")" == "user-choice" ]]; then
  pass "refresh preserves baseURL, the API key, and unrelated user config"
else
  fail "refresh clobbered config it has no business touching"
fi
if [[ "$(stat -c%a "$OCFG" 2>/dev/null || stat -f%Lp "$OCFG")" == "600" ]]; then
  pass "refresh preserves a keyed config's 0600 mode"
else
  fail "refresh widened the file mode on a keyed config"
fi
if [[ "$(_ocq "c['model'].split('/')[1] in c['provider']['openbeast-rig']['models']")" == "True" ]]; then
  pass "refresh repoints a default that pointed at a now-gone model"
else
  fail "default model still points at an entry that no longer exists"
fi
# An unreadable checkout must leave the config alone rather than blank the list.
cp "$OCFG" "$TMPROOT/oc-before.json"
bash -c "OC_CONFIG='$OCFG'; REPO='/nonexistent-repo'; PY_BIN=python3
$RF
_refresh_oc_catalog" >/dev/null 2>&1
if diff -q "$OCFG" "$TMPROOT/oc-before.json" >/dev/null; then
  pass "unreadable checkout leaves the client catalog untouched"
else
  fail "a bad checkout path modified the client catalog"
fi
if grep -q '_refresh_oc_catalog' "$REPO_DIR/scripts/client.sh" \
   && sed -n '/^  update)/,/^    ;;/p' "$REPO_DIR/scripts/client.sh" | grep -q '_refresh_oc_catalog'; then
  pass "client.sh update actually calls the refresh"
else
  fail "client.sh update does not call _refresh_oc_catalog (the original bug)"
fi

# --- 2026-10-09 review fixes ---
# Each case gets its OWN throwaway repo, so the conf file under test is the
# only thing that differs and the sections above keep their fixtures.
_fresh_repo() { # _fresh_repo <name> -> prints the repo path
  local r="$TMPROOT/$1"
  mkdir -p "$r/scripts/lib"
  cp "$REPO_DIR/scripts/clients.sh" "$r/scripts/"
  cp "$REPO_DIR"/scripts/lib/*.sh "$r/scripts/lib/"
  printf '%s\n' "$r"
}
_has() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

echo ""
echo "gate-off warning (UX-07):"
# Device keys are enforced by beast-gate only. With EDGE_GATE off, enroll,
# rotate and revoke must say that the key/revocation does nothing.
G="$(_fresh_repo gateoff)"
unset OPENBEAST_EDGE_GATE OPENBEAST_EDGE_ALLOW_ANON
GW="EDGE_GATE is not true"
out="$("$G/scripts/clients.sh" enroll lap 2>&1)" || true
if _has "$out" "$GW" && _has "$out" "NOT enforced" && _has "$out" "setup-tailscale.sh"; then
  pass "enroll with no EDGE_GATE in conf warns that keys are not enforced"
else
  fail "enroll printed no gate-off warning"
fi
out="$("$G/scripts/clients.sh" rotate lap 2>&1)" || true
if _has "$out" "$GW"; then pass "rotate warns too"; else fail "rotate printed no gate-off warning"; fi
out="$("$G/scripts/clients.sh" revoke lap 2>&1)" || true
if _has "$out" "$GW" && _has "$out" "Revoked 'lap'"; then
  pass "revoke warns that the revocation is not enforced (and still records it)"
else
  fail "revoke printed no gate-off warning"
fi
echo 'EDGE_GATE=false' > "$G/openbeast.conf"
out="$("$G/scripts/clients.sh" rotate lap 2>&1)" || true
if _has "$out" "$GW"; then pass "explicit EDGE_GATE=false warns"; else fail "EDGE_GATE=false did not warn"; fi
# Negative controls: a gate that IS on must not cry wolf — in each spelling
# conf.sh accepts, and through the env override.
printf 'EDGE_GATE=false\nEDGE_GATE="true"   # per-device keys\n' > "$G/openbeast.conf"
out="$("$G/scripts/clients.sh" rotate lap 2>&1)" || true
out2="$("$G/scripts/clients.sh" revoke lap 2>&1)" || true
if ! _has "$out" "$GW" && ! _has "$out2" "$GW" && _has "$out" "Rotated the key"; then
  pass "EDGE_GATE=true (quoted, with a trailing comment, last line wins) is silent"
else
  fail "warned although EDGE_GATE=true"
fi
echo 'EDGE_GATE=false' > "$G/openbeast.conf"
out="$(OPENBEAST_EDGE_GATE=yes "$G/scripts/clients.sh" rotate lap 2>&1)" || true
if ! _has "$out" "$GW"; then
  pass "env OPENBEAST_EDGE_GATE overrides the conf file"
else
  fail "env override ignored"
fi
before="$(cat "$G/openbeast.conf")"
"$G/scripts/clients.sh" list >/dev/null 2>&1 || true
out="$("$G/scripts/clients.sh" list 2>&1)" || true
if [[ "$(cat "$G/openbeast.conf")" == "$before" ]] && ! _has "$out" "$GW"; then
  pass "reading the setting never writes openbeast.conf; list stays quiet"
else
  fail "clients.sh mutated openbeast.conf or list warned"
fi

echo ""
echo "remove that empties the registry (netsec S7):"
# An empty registry is "not configured" to beast-gate; with
# EDGE_ALLOW_ANON=true that is anonymous mode, so deleting the last device
# re-admits it. `remove` must say so — and only then.
A="$(_fresh_repo anon)"
AW="registry is now EMPTY and EDGE_ALLOW_ANON=true"
printf 'EDGE_GATE=true\nEDGE_ALLOW_ANON=true\n' > "$A/openbeast.conf"
"$A/scripts/clients.sh" enroll one >/dev/null 2>&1
"$A/scripts/clients.sh" enroll two >/dev/null 2>&1
out="$("$A/scripts/clients.sh" remove one --yes 2>&1)" || true
if ! _has "$out" "$AW" && _has "$out" "Removed 'one'"; then
  pass "removing one of two devices does not warn (the registry is not empty)"
else
  fail "warned although a device is still enrolled"
fi
out="$("$A/scripts/clients.sh" remove two --yes 2>&1)" || true
if _has "$out" "$AW" && _has "$out" "EDGE_ALLOW_ANON=false" && _has "$out" "'two'"; then
  pass "removing the LAST device under EDGE_ALLOW_ANON=true warns and names the fix"
else
  fail "emptied the registry under EDGE_ALLOW_ANON=true without a warning"
fi
printf 'EDGE_GATE=true\nEDGE_ALLOW_ANON=false\n' > "$A/openbeast.conf"
"$A/scripts/clients.sh" enroll three >/dev/null 2>&1
out="$("$A/scripts/clients.sh" remove three --yes 2>&1)" || true
if ! _has "$out" "$AW" && _has "$out" "Removed 'three'"; then
  pass "the fail-closed default (EDGE_ALLOW_ANON=false) empties without the warning"
else
  fail "warned although EDGE_ALLOW_ANON=false"
fi

echo ""
echo "registry write lock (supply S15):"
# load -> modify -> save without a lock lets an overlapping writer put a stale
# copy back (a revoke lost to a concurrent enroll). Hold the lock from here
# and prove a writer waits for it, then finishes once it is released.
L="$(_fresh_repo lock)"
"$L/scripts/clients.sh" enroll lap >/dev/null 2>&1
_revoked() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["devices"][0]["revoked_at"] is not None)' "$L/.run/clients.json"; }
python3 -c '
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
deadline = time.time() + 30
while not os.path.exists(sys.argv[3]) and time.time() < deadline:
    time.sleep(0.05)
' "$L/.run/clients.json.lock" "$TMPROOT/lock-held" "$TMPROOT/lock-release" &
HOLDER=$!
for _ in $(seq 1 100); do [[ -e "$TMPROOT/lock-held" ]] && break; sleep 0.05; done
"$L/scripts/clients.sh" revoke lap >"$TMPROOT/lock-revoke.out" 2>&1 &
WRITER=$!
sleep 1
if kill -0 "$WRITER" 2>/dev/null && [[ "$(_revoked)" == "False" ]]; then
  pass "a writer waits while another process holds the registry lock"
else
  fail "revoke wrote the registry while the lock was held elsewhere"
fi
# Negative control: readers take no lock, so list must not hang behind it.
if out="$(timeout 10 "$L/scripts/clients.sh" list 2>&1)" && _has "$out" "lap"; then
  pass "list does not wait for the lock (reads stay lock-free)"
else
  fail "list blocked on (or failed under) the writer lock"
fi
: > "$TMPROOT/lock-release"
wait "$HOLDER" 2>/dev/null || true
if wait "$WRITER" && [[ "$(_revoked)" == "True" ]]; then
  pass "the waiting writer completes once the lock is released"
else
  fail "the waiting revoke never landed after the lock was released"
fi
if [[ "$(_mode "$L/.run/clients.json.lock")" == "600" ]]; then
  pass "the lock file is 0600"
else
  fail "lock file mode is $(_mode "$L/.run/clients.json.lock")"
fi

echo ""
echo "client.sh live model row (ops F8):"
# The live row's KEY is what opencode sends as "model". It must be the id the
# rig serves — vLLM and hydra 404 an id they do not serve — not an invented
# one. A stub rig on an ephemeral loopback port serves a known id.
LIVE_DIR="$TMPROOT/live"; mkdir -p "$LIVE_DIR"
python3 - "$LIVE_DIR/port" <<'PY' &
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            body = {"data": [{"id": "qwen38-27b-nvfp4"}]}
        elif self.path == "/props":
            body = {"default_generation_settings": {"n_ctx": 131072}}
        else:
            self.send_error(404); return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def log_message(self, *a):
        pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1] + ".tmp", "w").write(str(srv.server_address[1]))
import os; os.replace(sys.argv[1] + ".tmp", sys.argv[1])
srv.serve_forever()
PY
LIVE_PID=$!
for _ in $(seq 1 100); do [[ -s "$LIVE_DIR/port" ]] && break; sleep 0.05; done
LIVE_PORT="$(cat "$LIVE_DIR/port")"
LCFG="$LIVE_DIR/opencode.json"
# An install made by the previous client: default and small_model on rig-live.
python3 -c "
import json, sys
json.dump({'model': 'openbeast-rig/rig-live', 'small_model': 'openbeast-rig/rig-live',
           'provider': {'openbeast-rig': {'options': {'baseURL': 'http://127.0.0.1:%s/v1' % sys.argv[2], 'apiKey': 'not-needed'},
                                          'models': {'rig-live': {'name': 'old  [live on rig]'}}}}},
          open(sys.argv[1], 'w'), indent=2)" "$LCFG" "$LIVE_PORT"
LIVE_OUT="$(bash -c "OC_CONFIG='$LCFG'; REPO='$REPO_DIR'; PY_BIN=python3
$RF
_refresh_oc_catalog" 2>&1)" || true
kill "$LIVE_PID" 2>/dev/null || true
wait "$LIVE_PID" 2>/dev/null || true
_lq() { python3 -c "
import json,sys
c=json.load(open('$LCFG')); m=c['provider']['openbeast-rig']['models']; print(eval(sys.argv[1]))" "$1"; }
if [[ "$(_lq "c['model']")" == "openbeast-rig/qwen38-27b-nvfp4" \
   && "$(_lq "'qwen38-27b-nvfp4' in m")" == "True" ]]; then
  pass "the live row and the default model carry the id the rig serves"
else
  fail "default is '$(_lq "c['model']")' — want openbeast-rig/qwen38-27b-nvfp4 :: $LIVE_OUT"
fi
if [[ "$(_lq "'rig-live' in m")" == "False" \
   && "$(_lq "c['small_model']")" == "openbeast-rig/qwen38-27b-nvfp4" ]]; then
  pass "no invented 'rig-live' id survives — not as a row, not in small_model"
else
  fail "rig-live survived: row=$(_lq "'rig-live' in m") small_model=$(_lq "c['small_model']")"
fi
if [[ "$(_lq "m['qwen38-27b-nvfp4']['limit']['context']")" == "131072" ]] \
   && _has "$LIVE_OUT" "rig is serving 'qwen38-27b-nvfp4'"; then
  pass "the live row still carries the rig's real n_ctx"
else
  fail "live row lost its context limit :: $LIVE_OUT"
fi
# Negative control: the dead-rig run above (section 9) must not have invented
# a live row either — every key is a catalog id.
if [[ "$(_ocq "'rig-live' in c['provider']['openbeast-rig']['models']")" == "False" ]]; then
  pass "control: an unreachable rig adds no live row at all"
else
  fail "a dead rig still produced a rig-live row"
fi

echo ""
# --- Summary ---
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[[ $FAIL -eq 0 ]]
