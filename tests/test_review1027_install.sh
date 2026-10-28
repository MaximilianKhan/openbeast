#!/bin/bash
# Review 2026-10-27 — install / weights / WebUI / doctor fixes. Behaviour tests.
#
# Usage: bash tests/test_review1027_install.sh
#
# Runs against THROWAWAY copies of the scripts under $TMPDIR, with stub `hf`,
# `curl` and `zig` on PATH that RECORD their calls, so assertions are about
# what the script did. Every positive assertion has a negative control.
#
#   1  fetch-weight.sh   OFFLINE=true never probes or downloads        (R5-08)
#   2  fetch-weight.sh   a PENDING row: loud UNVERIFIED, strict refuses (R5-09)
#   3  bootstrap.sh      OFFLINE sideload names verify-weights --file   (R5-07)
#   4  doctor.sh         warns when installed zig != the shipped pack's zig
#   5  doctor.sh         warns when tailscale < 1.98.9                 (R10-11)
#   6  doctor.sh         warns on a global skill outside the import gate (R9-05)

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
# HERMETIC: a rig with OPENBEAST_* exported would steer the scripts under test.
for _v in $(compgen -e | grep '^OPENBEAST_' || true); do unset "$_v"; done
unset OFFLINE WEIGHT_ENFORCE

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has()  { grep -qF -- "$2" <<< "$1"; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

SB="$T/repo"
mkdir -p "$SB/scripts/lib" "$T/bin" "$T/state"
for f in fetch-weight.sh verify-weights.sh; do
  install -m 755 "$REPO_DIR/scripts/$f" "$SB/scripts/$f"
done
install -m 644 "$REPO_DIR/scripts/lib/weights.sh" "$SB/scripts/lib/weights.sh"
export OB_STUB_STATE="$T/state"

cat > "$T/bin/hf" <<'STUB'
#!/bin/bash
# hf download <repo> <remote> --local-dir <dir>
echo "$*" >> "$OB_STUB_STATE/hf.log"
printf 'BYTES' > "$5/$3"
STUB
cat > "$T/bin/curl" <<'STUB'
#!/bin/bash
echo "$*" >> "$OB_STUB_STATE/curl.log"
exit 0
STUB
chmod +x "$T/bin/hf" "$T/bin/curl"

W="$T/weights"; mkdir -p "$W"
export OPENBEAST_WEIGHTS_DIR="$W"
_sha="$(printf 'BYTES' | sha256sum | awk '{print $1}')"
printf '%s\t5\tpinned.gguf\torg/pinned\t-\n' "$_sha" >  "$SB/scripts/weights.registry"
printf 'PENDING\t0\tpending.gguf\torg/pending\t-\n'   >> "$SB/scripts/weights.registry"

reset_state() { rm -f "$T/state/"*.log "$W"/*.gguf "$SB/openbeast.conf"; }
calls() { [[ -f "$T/state/$1.log" ]] && wc -l < "$T/state/$1.log" || echo 0; }
fw() { PATH="$T/bin:$PATH" "$SB/scripts/fetch-weight.sh" "$@" 2>&1; }

# ===========================================================================
echo ""
echo "1. fetch-weight.sh honours OFFLINE (R5-08):"
# ===========================================================================
reset_state
_out="$(OPENBEAST_OFFLINE=true fw pinned.gguf)"; _rc=$?
if [[ $_rc -eq 4 && "$(calls curl)" -eq 0 && "$(calls hf)" -eq 0 && ! -e "$W/pinned.gguf" ]]; then
  pass "OPENBEAST_OFFLINE=true: exit 4, no probe, no download"
else
  fail "OFFLINE env ignored (rc=$_rc curl=$(calls curl) hf=$(calls hf)): $_out"
fi
has "$_out" "verify-weights.sh --file pinned.gguf" \
  && pass "…and prints the sideload instructions" \
  || fail "no sideload help under OFFLINE: $_out"

reset_state
printf 'OFFLINE=true  # air-gapped\n' > "$SB/openbeast.conf"
_out="$(fw pinned.gguf)"; _rc=$?
[[ $_rc -eq 4 && "$(calls curl)" -eq 0 && "$(calls hf)" -eq 0 ]] \
  && pass "OFFLINE=true in openbeast.conf (with a trailing comment) is honoured too" \
  || fail "conf OFFLINE ignored (rc=$_rc curl=$(calls curl) hf=$(calls hf)): $_out"

reset_state   # negative control: online, the same row downloads and verifies
_out="$(fw pinned.gguf)"; _rc=$?
[[ $_rc -eq 0 && "$(calls hf)" -eq 1 && -f "$W/pinned.gguf" ]] \
  && pass "control: without OFFLINE the pinned row is downloaded and verified" \
  || fail "control: online fetch broke (rc=$_rc hf=$(calls hf)): $_out"

reset_state   # an already-present weight is still verifiable offline
printf 'BYTES' > "$W/pinned.gguf"
_out="$(OPENBEAST_OFFLINE=true fw pinned.gguf)"; _rc=$?
[[ $_rc -eq 0 && "$(calls curl)" -eq 0 ]] \
  && pass "OFFLINE: a weight already present is verified, not refused" \
  || fail "OFFLINE broke the already-present path (rc=$_rc): $_out"

# ===========================================================================
echo ""
echo "2. fetch-weight.sh and PENDING rows (R5-09):"
# ===========================================================================
reset_state
_out="$(fw pending.gguf)"; _rc=$?
if [[ $_rc -eq 0 && -f "$W/pending.gguf" ]] && has "$_out" "UNVERIFIED"; then
  pass "default (warn): a PENDING row downloads, and says UNVERIFIED"
else
  fail "PENDING under warn (rc=$_rc): $_out"
fi
has "$_out" "nothing to verify" \
  && fail "the old soft 'nothing to verify' wording is still the final line" \
  || pass "…the soft 'nothing to verify' line is gone"

reset_state
_out="$(OPENBEAST_WEIGHT_ENFORCE=strict fw pending.gguf)"; _rc=$?
if [[ $_rc -ne 0 && "$(calls hf)" -eq 0 && "$(calls curl)" -eq 0 && ! -e "$W/pending.gguf" ]] \
   && has "$_out" "WEIGHT_ENFORCE=strict"; then
  pass "WEIGHT_ENFORCE=strict refuses a PENDING row before any network/download"
else
  fail "strict let a PENDING row through (rc=$_rc hf=$(calls hf)): $_out"
fi

reset_state
printf 'WEIGHT_ENFORCE=strict\n' > "$SB/openbeast.conf"
_out="$(fw pending.gguf)"; _rc=$?
[[ $_rc -ne 0 && "$(calls hf)" -eq 0 ]] \
  && pass "WEIGHT_ENFORCE=strict from openbeast.conf refuses too" \
  || fail "conf strict ignored (rc=$_rc hf=$(calls hf)): $_out"

reset_state   # negative control: strict still fetches a PINNED row
printf 'WEIGHT_ENFORCE=strict\n' > "$SB/openbeast.conf"
_out="$(fw pinned.gguf)"; _rc=$?
[[ $_rc -eq 0 && -f "$W/pinned.gguf" ]] \
  && pass "control: strict still fetches a pinned row" \
  || fail "control: strict broke pinned fetches (rc=$_rc): $_out"

reset_state   # the env override beats the conf, as the refusal message promises
printf 'WEIGHT_ENFORCE=strict\n' > "$SB/openbeast.conf"
_out="$(OPENBEAST_WEIGHT_ENFORCE=warn fw pending.gguf)"; _rc=$?
[[ $_rc -eq 0 && -f "$W/pending.gguf" ]] \
  && pass "OPENBEAST_WEIGHT_ENFORCE=warn overrides a strict conf for one fetch" \
  || fail "the printed escape hatch does not work (rc=$_rc): $_out"

# ===========================================================================
echo ""
echo "3. bootstrap.sh OFFLINE sideload message (R5-07):"
# ===========================================================================
_blk="$(awk '/ob_offline && die "OFFLINE=true and \$WEIGHT_FILE/,/openbeast.conf to match/' "$REPO_DIR/bootstrap.sh")"
if [[ -n "$_blk" ]] && has "$_blk" "verify-weights.sh --file" ; then
  pass "the sideload message names verify-weights.sh --file (which hashes)"
else
  fail "the sideload message still names a size-only verify: $_blk"
fi

# ===========================================================================
echo ""
echo "4. doctor.sh — installed zig vs the shipped zig pack's version:"
# ===========================================================================
D="$T/doctor"
mkdir -p "$D/scripts/lib" "$D/.run" "$D/bin" "$D/home" "$D/agents/lang" "$D/agents/packs"
cp "$REPO_DIR/scripts/doctor.sh" "$REPO_DIR/scripts/healthcheck.sh" "$D/scripts/"
cp "$REPO_DIR"/scripts/lib/*.sh "$D/scripts/lib/"
cp "$REPO_DIR/agents/lang/__init__.py" "$REPO_DIR/agents/lang/pack_context.py" "$D/agents/lang/"
cp "$REPO_DIR/agents/packs/zig-0.16.md" "$D/agents/packs/"
: > "$D/openbeast.conf"
for c in curl docker tailscale nvidia-smi sudo systemctl systemd-run smartctl pkill pgrep; do
  printf '#!/bin/bash\nexit 1\n' > "$D/bin/$c"; chmod +x "$D/bin/$c"
done
_pack_ver="$(sed -nE 's/^\(2\) GENERATED signature digest — zig ([^ ]+) std.*/\1/p' "$D/agents/packs/zig-0.16.md" | head -1)"
doctor_with_zig() { # doctor_with_zig <version|none>
  rm -f "$D/bin/zig"
  [[ "$1" == none ]] || { printf '#!/bin/bash\necho %s\n' "$1" > "$D/bin/zig"; chmod +x "$D/bin/zig"; }
  env -i HOME="$D/home" PATH="$D/bin:/usr/bin:/bin" bash "$D/scripts/doctor.sh" 2>&1 || true
}
if [[ -z "$_pack_ver" ]]; then
  fail "could not read the shipped pack's zig version (header changed?)"
else
  _out="$(doctor_with_zig 9.99.0)"
  has "$_out" "installed zig 9.99.0 != zig $_pack_ver" \
    && pass "a different installed zig (9.99.0 vs $_pack_ver) is warned about" \
    || fail "no zig mismatch warning: $(grep -i zig <<< "$_out" | tr '\n' ' ')"
  printf 'LANG_PACK_CONTEXT=off\n' > "$D/openbeast.conf"
  _out="$(doctor_with_zig 9.99.0)"
  has "$_out" "installed zig 9.99.0 != zig $_pack_ver" \
    && pass "…still warned with LANG_PACK_CONTEXT=off (where the pack row goes quiet)" \
    || fail "the mismatch hides behind LANG_PACK_CONTEXT=off: $(grep -i zig <<< "$_out" | tr '\n' ' ')"
  : > "$D/openbeast.conf"
  _out="$(doctor_with_zig "$_pack_ver")"
  if has "$_out" "installed zig $_pack_ver matches the shipped zig pack" && ! has "$_out" "!= zig"; then
    pass "control: the matching zig passes"
  else
    fail "control: matching zig not passed: $(grep -i zig <<< "$_out" | tr '\n' ' ')"
  fi
  if [[ ! -x /usr/bin/zig ]]; then
    _out="$(doctor_with_zig none)"
    has "$_out" "zig not installed" && ! has "$_out" "!= zig" \
      && pass "no zig at all is informational, not a warning" \
      || fail "missing zig mis-reported: $(grep -i zig <<< "$_out" | tr '\n' ' ')"
  fi
fi

# ===========================================================================
echo ""
echo "5. doctor.sh — tailscale minimum version (1.98.9):"
# ===========================================================================
doctor_with_ts() { # doctor_with_ts <version-line>
  printf '#!/bin/bash\n[[ "$1" == version ]] && { echo "%s"; echo "  long version: x"; exit 0; }\nexit 1\n' "$1" > "$D/bin/tailscale"
  chmod +x "$D/bin/tailscale"
  env -i HOME="$D/home" PATH="$D/bin:/usr/bin:/bin" bash "$D/scripts/doctor.sh" 2>&1 || true
}
_out="$(doctor_with_ts 1.98.8)"
has "$_out" "tailscale 1.98.8 is older than 1.98.9" \
  && pass "1.98.8 is warned about" \
  || fail "old tailscale not warned: $(grep -i tailscale <<< "$_out" | tr '\n' ' ')"
_out="$(doctor_with_ts 1.104.2)"   # 104 > 98 numerically, not lexically
has "$_out" "tailscale 1.104.2 (≥ 1.98.9" && ! has "$_out" "older than" \
  && pass "control: 1.104.2 passes (numeric, not lexical, compare)" \
  || fail "1.104.2 mis-judged: $(grep -i tailscale <<< "$_out" | tr '\n' ' ')"
_out="$(doctor_with_ts 1.98.9)"
has "$_out" "tailscale 1.98.9 (≥" && pass "control: exactly 1.98.9 passes" \
  || fail "1.98.9 mis-judged: $(grep -i tailscale <<< "$_out" | tr '\n' ' ')"
printf '#!/bin/bash\nexit 1\n' > "$D/bin/tailscale"

# ===========================================================================
echo ""
echo "6. doctor.sh — global skills outside the import gate (R9-05):"
# ===========================================================================
_G="$D/home/.local/share/local-llm-skills"
mkdir -p "$D/skills/gated-one" "$D/skills/shadowed" "$_G/handmade" "$_G/shadowed"
echo "---" > "$D/skills/gated-one/SKILL.md"
echo "---" > "$D/skills/shadowed/SKILL.md"
echo "---" > "$_G/handmade/SKILL.md"
echo "---" > "$_G/shadowed/SKILL.md"
ln -s "$D/skills/gated-one" "$_G/gated-one"
doctor_plain() { env -i HOME="$D/home" PATH="$D/bin:/usr/bin:/bin" bash "$D/scripts/doctor.sh" 2>&1 || true; }
_out="$(doctor_plain)"
_row="$(grep "outside the import gate" <<< "$_out" || true)"
if has "$_row" "[global, unverified]: handmade" && ! has "$_row" "gated-one" && ! has "$_row" "shadowed"; then
  pass "a hand-copied global skill is warned about; a symlink into skills/ and a shadowed name are not"
else
  fail "global-skill row wrong: ${_row:-<none>}"
fi
rm -rf "$_G/handmade"
_out="$(doctor_plain)"
has "$_out" "global skills: none outside the import gate" && ! has "$_out" "served as [global" \
  && pass "control: only gated (symlinked) global skills pass" \
  || fail "control: gated-only global dir mis-reported: $(grep -i 'global skills' <<< "$_out" | tr '\n' ' ')"
rm -rf "$D/home/.local/share/local-llm-skills"

echo ""
echo "Results: $PASS passed, $FAIL failed"
exit $(( FAIL > 0 ? 1 : 0 ))
