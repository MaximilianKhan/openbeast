#!/bin/bash
# spark-deepseek-pack.sh: the DeepSeek pack swap. Hermetic: a scratch local.sh,
# a fake recipe runtime, pack folders and HF cache in a scratch dir; systemctl,
# curl, ssh, rsync and flock are stubs. Covered: the managed local.sh block,
# the local revision, staging (hard links; a foreign file refused), the GLM
# refusal, the model-switch lock, "already serving" against the prepared
# marker, the REV-collision fingerprint, a signal while preparing, wait_ready's
# served id, and verify's logs. The real prepare/restart on both Sparks has its
# run record in docs/SPARK_HYDRA.md "DeepSeek packs: spark-deepseek-pack".
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SDP="$REPO_DIR/scripts/backends/spark-deepseek-pack.sh"
PASS=0 FAIL=0 SKIP=0
# The tool needs bash >= 4 (mapfile), as on the Sparks; macOS's /bin/bash is 3.2.
B4=""
for c in bash /opt/homebrew/bin/bash /usr/local/bin/bash; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c '(( BASH_VERSINFO[0] >= 4 ))' 2>/dev/null; then B4=$(command -v "$c"); break; fi
done
RUNB=${B4:-bash}
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP: $*"; SKIP=$((SKIP + 1)); }
echo "=== spark-deepseek-pack tests ==="

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export SDP_PREP="$T/prep"
B='# >>> spark-deepseek-pack (managed: scripts/backends/spark-deepseek-pack.sh)'
E='# <<< spark-deepseek-pack'

# --- render_local: append the managed block to a hand-written local.sh
printf 'WORKER=spark-2\nHF_CACHE=/hf\nCONTEXT=524288\n\n' > "$T/local.sh"
"$RUNB" "$SDP" _render_local "$T/local.sh" org/a 1111111111111111111111111111111111111111 /hf/worker-rank-cache-11111111 /src/a > "$T/one" 2>&1
if [[ "$(head -3 "$T/one")" == $'WORKER=spark-2\nHF_CACHE=/hf\nCONTEXT=524288' ]] && grep -qx "$B" "$T/one" && grep -qx "$E" "$T/one" \
   && grep -qx 'MODEL_ID=org/a' "$T/one" && grep -qx 'MODEL_SRC=/src/a' "$T/one"; then
  pass "render: operator settings kept, managed block appended"
else fail "render append: $(cat "$T/one")"; fi

# --- render_local again: the block is replaced, never duplicated
"$RUNB" "$SDP" _render_local "$T/one" org/b 2222222222222222222222222222222222222222 /hf/worker-rank-cache-22222222 /src/b > "$T/two" 2>&1
if [[ "$(grep -cx "$B" "$T/two")" == 1 && "$(grep -c '^MODEL_ID=' "$T/two")" == 1 ]] && grep -qx 'MODEL_ID=org/b' "$T/two" \
   && grep -qx 'WORKER_CACHE_DIR=/hf/worker-rank-cache-22222222' "$T/two" && ! grep -q 'org/a' "$T/two"; then
  pass "render: second swap replaces the block in place"
else fail "render replace: $(cat "$T/two")"; fi

# --- the serving settings outside the block (docs/reports/DEEPSEEK_SERVING_OPT-2026-11-25.md) survive a swap
printf 'WORKER=spark-2\nCONTEXT=524288\nPARALLEL=8\nSPILL_GIB=64\nexport TF_FILL_CHUNKS=1 TF_FILL_FIRST=1 TF_FILL_CUT_IN=8192\n' > "$T/serving.sh"
"$RUNB" "$SDP" _render_local "$T/serving.sh" org/a 1111111111111111111111111111111111111111 /hf/w-11111111 /src/a > "$T/s1" 2>&1
"$RUNB" "$SDP" _render_local "$T/s1" org/b 2222222222222222222222222222222222222222 /hf/w-22222222 /src/b > "$T/s2" 2>&1
if out=$(bash -c "set -u; source '$T/s2'; echo \"\$PARALLEL \$SPILL_GIB \$TF_FILL_CHUNKS \$TF_FILL_FIRST \$TF_FILL_CUT_IN \$MODEL_ID\"; env | grep -c '^TF_FILL_'" 2>&1) \
   && [[ "$out" == $'8 64 1 1 8192 org/b\n3' ]]; then
  pass "render: PARALLEL, SPILL_GIB and the exported TF_FILL_* lines survive two swaps"
else fail "render serving lines: $out"; fi

# --- the result is valid bash that sets exactly the four variables
if out=$(bash -c "set -u; source '$T/two'; echo \"\$MODEL_ID \$MODEL_REVISION \$WORKER_CACHE_DIR \$MODEL_SRC\"" 2>&1) \
   && [[ "$out" == "org/b 2222222222222222222222222222222222222222 /hf/worker-rank-cache-22222222 /src/b" ]]; then
  pass "render: the managed local.sh sources cleanly"
else fail "render source: $out"; fi

# --- pack_rev: stable, size-sensitive, independent of non-shard files (GNU or BSD stat)
mkdir -p "$T/p"; printf aaaa > "$T/p/model-00001-of-00002.safetensors"; printf bb > "$T/p/model-00002-of-00002.safetensors"
r1=$("$RUNB" "$SDP" _pack_rev "$T/p"); echo notes > "$T/p/README.md"; r2=$("$RUNB" "$SDP" _pack_rev "$T/p")
printf b >> "$T/p/model-00002-of-00002.safetensors"; r3=$("$RUNB" "$SDP" _pack_rev "$T/p")
if [[ "$r1" =~ ^[0-9a-f]{40}$ && "$r1" == "$r2" && "$r1" != "$r3" ]]; then pass "pack_rev: 40 hex, ignores non-shards, changes with a shard's size"
else fail "pack_rev: $r1 / $r2 / $r3"; fi
# the "size name" listing the already-staged REVs (04c13f12, 5d70e783) were computed from: unchanged
want=$(printf '4 model-00001-of-00002.safetensors\n3 model-00002-of-00002.safetensors\n' | sha1sum | cut -c1-40)
if [[ "$r3" == "$want" ]]; then pass "pack_rev: the same size listing as before (staged revisions stay valid)"
else fail "pack_rev listing: $r3 vs $want"; fi

# ─── the swap itself, hermetically: a fake recipe runtime, stubs for systemctl,
# curl, ssh, rsync and flock, two packs in a scratch HF cache ────────────────
ST="$T/st"; mkdir -p "$ST" "$T/bin" "$T/prep/runtime/scripts" "$T/hf/hub" "$T/docs"
export ST
: > "$ST/sc.log"
cat > "$T/bin/systemctl" <<'S'
#!/bin/bash
shift  # --user
op=$1; shift
case "$op" in
  is-active) [[ "$1" == --quiet ]] && shift; [[ -f "$ST/$1.active" ]] ;;
  is-enabled) [[ -f "$ST/$1.enabled" ]] && echo enabled || { echo disabled; exit 1; } ;;
  is-failed) exit 1 ;;
  start|stop) for u; do echo "$op $u" >> "$ST/sc.log"; done ;;
esac
S
# flock -n FD: busy while $ST/lock-busy exists (the stub stands in for util-linux on any OS)
cat > "$T/bin/flock" <<'S'
#!/bin/bash
[[ ! -e "$ST/lock-busy" ]]
S
# curl: logs its argv and what fd 3 carried; serves the ids in $ST/served
cat > "$T/bin/curl" <<'S'
#!/bin/bash
echo "$*" >> "$ST/curl.argv"
for a; do [[ "$a" == /dev/fd/3 ]] && cat /dev/fd/3 >> "$ST/curl.fd3"; done
[[ -s "$ST/served" ]] || exit 7
python3 -c 'import json,sys; print(json.dumps({"data":[{"id":i} for i in sys.argv[1:]]}))' $(cat "$ST/served")
S
# ssh [-n] [-o X]... HOST CMD...: runs CMD here (the "worker" shares this scratch tree)
cat > "$T/bin/ssh" <<'S'
#!/bin/bash
while [[ "$1" == -* ]]; do [[ "$1" == -o ]] && shift; shift; done
shift
exec bash -c "$*"
S
printf '#!/bin/bash\nexit 0\n' > "$T/bin/rsync"
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH"
RT="$T/prep/runtime"
# the recipe's config.sh, reduced to what spark-deepseek-pack.sh reads
cat > "$RT/scripts/config.sh" <<S
source scripts/local.sh
HF_CACHE=\${HF_CACHE:-$T/hf}
PREPARED_MARKER=$ST/prepared
snapshot_rev() { echo "\$MODEL_REVISION"; }
prepared_state() { printf 'model=%s@%s w=%s\n' "\$MODEL_ID" "\$(snapshot_rev)" "\$WORKER"; }
S
mkpack() {  # dir fill-byte [size of shard 2]
  mkdir -p "$1"
  printf '{"weight_map":{"a":"model-00001-of-00002.safetensors","b":"model-00002-of-00002.safetensors"}}' > "$1/model.safetensors.index.json"
  printf '{"architectures":["DeepseekV41ForCausalLM"]}' > "$1/config.json"
  head -c 2000 /dev/zero | tr '\0' "$2" > "$1/model-00001-of-00002.safetensors"
  head -c "${3:-3000}" /dev/zero | tr '\0' "$2" > "$1/model-00002-of-00002.safetensors"
}
mkpack "$T/docs/packA" a
REVA=$("$RUNB" "$SDP" _pack_rev "$T/docs/packA")
"$RUNB" "$SDP" _stage "$T/hf" "$T/docs/packA" org/a "$REVA" 2>"$T/err"; rc=$?
SNAPA="$T/hf/hub/models--org--a/snapshots/$REVA"
inode() { python3 -c 'import os,sys; print(os.stat(sys.argv[1]).st_ino)' "$1"; }
if [[ $rc == 0 && "$(inode "$SNAPA/model-00001-of-00002.safetensors")" == "$(inode "$T/docs/packA/model-00001-of-00002.safetensors")" ]]; then
  pass "stage: a new snapshot is hard links of the pack folder"
else fail "stage new (rc $rc): $(cat "$T/err")"; fi
"$RUNB" "$SDP" _stage "$T/hf" "$T/docs/packA" org/a "$REVA" 2>"$T/err"; rc=$?
if [[ $rc == 0 ]]; then pass "stage: again onto the same links is idempotent"; else fail "stage again (rc $rc): $(cat "$T/err")"; fi
# a snapshot dir holding a DIFFERENT file under a shard's name: refused, never silently kept
mkdir -p "$T/hf/hub/models--org--z/snapshots/$REVA"; printf 'stale' > "$T/hf/hub/models--org--z/snapshots/$REVA/model-00001-of-00002.safetensors"
"$RUNB" "$SDP" _stage "$T/hf" "$T/docs/packA" org/z "$REVA" 2>"$T/err"; rc=$?
if [[ $rc != 0 ]] && grep -q "refusing to stage" "$T/err" && [[ "$(cat "$T/hf/hub/models--org--z/snapshots/$REVA/model-00001-of-00002.safetensors")" == stale ]] \
   && [[ ! -e "$T/hf/hub/models--org--z/snapshots/$REVA/model-00002-of-00002.safetensors" ]]; then
  pass "stage: an existing file that is not the source's (inode/size) is refused, nothing linked"
else fail "stage foreign (rc $rc): $(cat "$T/err")"; fi
rm -rf "$T/hf/hub/models--org--z"

cat > "$RT/scripts/local.sh" <<S
WORKER=spark-2
HF_CACHE=$T/hf
MODEL_ID=org/a
MODEL_REVISION=$REVA
WORKER_CACHE_DIR=$T/hf/worker-rank-cache-${REVA:0:8}
MODEL_SRC=$T/docs/packA
S
cp "$RT/scripts/local.sh" "$T/local.orig"
export SDP_KEY_FILE="$T/key" SDP_LOCK="$T/model.lock" SDP_OPENBEAST="$T/ob" SDP_PREPARE="$T/no-prepare.sh"
printf '#!/bin/bash\necho "no prepare in tests" >&2; exit 1\n' > "$T/no-prepare.sh"; chmod +x "$T/no-prepare.sh"
echo SECRETKEY123 > "$T/key"
mkpack "$T/docs/packB" b 3500   # another size listing: another REV
use() { : > "$ST/sc.log"; out=$("$RUNB" "$SDP" use "$@" 2>&1); rc=$?; }

if [[ -n "$B4" ]]; then
# GLM serving: `use` refuses before anything changes, and names beast-spark ds
touch "$ST/spark-hydra-inference.service.active"
use "$T/docs/packB" --id org/b
if [[ $rc != 0 ]] && grep -q "beast-spark ds" <<<"$out" && grep -q -- "--stop-glm" <<<"$out" && [[ ! -s "$ST/sc.log" ]] \
   && cmp -s "$RT/scripts/local.sh" "$T/local.orig" && [[ ! -e "$T/hf/hub/models--org--b" ]]; then
  pass "use while GLM serves: refused (beast-spark ds, or --stop-glm), nothing stopped, staged or rewritten"
else fail "use with GLM (rc $rc): $out / $(cat "$ST/sc.log")"; fi
use "$T/docs/packB" --id org/b --dry-run
if [[ $rc == 0 ]] && grep -q "real run needs beast-spark ds first" <<<"$out" && grep -q "^MODEL_ID=org/b" <<<"$out"; then
  pass "use --dry-run while GLM serves: the plan, with the note"
else fail "dry with GLM (rc $rc): $out"; fi
rm -f "$ST/spark-hydra-inference.service.active"

# the selected pack, prepared: nothing to do
printf 'model=org/a@%s w=spark-2\n' "$REVA" > "$ST/prepared"
use "$T/docs/packA" --id org/a
if [[ $rc == 0 ]] && grep -q "already the selected pack, and prepared" <<<"$out" && [[ ! -s "$ST/sc.log" ]]; then
  pass "use of the selected, prepared pack: a no-op"
else fail "already serving (rc $rc): $out"; fi
# the selected pack, but the marker is another pack's: NOT "already serving"
printf 'model=org/b@%s w=spark-2\n' "$REVA" > "$ST/prepared"
use "$T/docs/packA" --id org/a --dry-run
if [[ $rc == 0 ]] && grep -q "prepared marker is stale" <<<"$out" && ! grep -q "already the selected pack" <<<"$out"; then
  pass "use of the selected pack with a stale prepared marker: prepared again, not skipped"
else fail "stale marker (rc $rc): $out"; fi

# a pack with packA's REV (same names and sizes) but other bytes: refused by its fingerprint
mkpack "$T/docs/packC" c
if [[ "$("$RUNB" "$SDP" _pack_rev "$T/docs/packC")" == "$REVA" ]]; then
  use "$T/docs/packC" --id org/c --dry-run
  if [[ $rc != 0 ]] && grep -q "OTHER content" <<<"$out" && [[ ! -e "$T/hf/hub/models--org--c" ]]; then
    pass "REV collision: same size listing, other content: refused by the content fingerprint"
  else fail "collision (rc $rc): $out"; fi
  if grep -q "^rev=$REVA fingerprint=[0-9a-f]\{64\}$" "$T/docs/packC/.pack-fingerprint"; then pass "fingerprint: cached in the pack folder as .pack-fingerprint"
  else fail "fingerprint cache: $(cat "$T/docs/packC/.pack-fingerprint" 2>&1)"; fi
else fail "packC should share packA's REV"; fi

# the model-switch lock (shared with beast-spark): a second switch is refused
touch "$ST/lock-busy"
use "$T/docs/packB" --id org/b
if [[ $rc != 0 ]] && grep -q "another model or pack switch is running" <<<"$out" && [[ ! -s "$ST/sc.log" ]]; then pass "use: refused while the model-switch lock is held"
else fail "lock (rc $rc): $out"; fi
use "$T/docs/packB" --id org/b --dry-run
if [[ $rc == 0 ]]; then pass "use --dry-run: takes no lock"; else fail "dry lock (rc $rc): $out"; fi
rm -f "$ST/lock-busy"

# a signal while preparing: local.sh.prev is put back
cat > "$T/prepare.sh" <<'S'
#!/bin/bash
kill -TERM "$PPID"; sleep 1; exit 1
S
chmod +x "$T/prepare.sh"
SDP_PREPARE="$T/prepare.sh" "$RUNB" "$SDP" use "$T/docs/packB" --id org/b > "$T/out" 2>&1; rc=$?
if [[ $rc == 130 ]] && grep -q "interrupted: local.sh restored" "$T/out" && cmp -s "$RT/scripts/local.sh" "$T/local.orig"; then
  pass "TERM during prepare: local.sh restored from local.sh.prev, exit 130"
else fail "signal (rc $rc): $(cat "$T/out") / $(cat "$RT/scripts/local.sh")"; fi
if [[ -e "$T/hf/hub/models--org--b/snapshots/$("$RUNB" "$SDP" _pack_rev "$T/docs/packB")/model-00001-of-00002.safetensors" && ! -e "$T/hf/hub/models--org--b/snapshots/$("$RUNB" "$SDP" _pack_rev "$T/docs/packB")/.pack-fingerprint" ]]; then
  pass "stage: the pack's .pack-fingerprint cache is not linked into the HF snapshot"
else fail "fingerprint in snapshot: $(ls -A "$T/hf/hub/models--org--b/snapshots/"*)"; fi

else skip "use: the swap steps need bash >= 4 (mapfile; brew install bash on a Mac)"; fi

# wait_ready: the served id must be DeepSeek's, and the key never rides in argv
: > "$ST/curl.argv"; : > "$ST/curl.fd3"
echo GLM-5.3-Flash-EXL3 > "$ST/served"
if ! SDP_WAIT_TRIES=2 SDP_WAIT_STEP=0 "$RUNB" "$SDP" _wait_ready; then pass "wait_ready: another model on :8000 is not ready"
else fail "wait_ready accepted GLM"; fi
echo DeepSeek-V4.1-Flash-1017 > "$ST/served"
if SDP_WAIT_TRIES=2 SDP_WAIT_STEP=0 "$RUNB" "$SDP" _wait_ready; then pass "wait_ready: DeepSeek-V4.1-Flash-1017 listed: ready"
else fail "wait_ready refused DeepSeek"; fi
if ! grep -q SECRETKEY123 "$ST/curl.argv" && grep -q 'Authorization: Bearer SECRETKEY123' "$ST/curl.fd3"; then
  pass "wait_ready: the API key goes through curl --config on fd 3, never argv"
else fail "key in argv: $(cat "$ST/curl.argv")"; fi

# verify: tee to a file, then grep the file (no `grep -q` in a pipeline under pipefail)
mkdir -p "$T/ob/scripts/backends" "$T/vout"
cat > "$T/prep/verify-chat.py" <<'S'
import sys
for i in range(20000): print("canary line")
print("17 x 23 = 391")
S
cat > "$T/ob/scripts/backends/conformance.sh" <<'S'
#!/bin/bash
for i in $(seq 1 20000); do echo "check $i ok"; done
echo "VERDICT: PASS"
for i in $(seq 1 20000); do echo "trailer $i"; done
S
chmod +x "$T/ob/scripts/backends/conformance.sh"
if "$RUNB" "$SDP" _verify "$T/vout" >/dev/null 2>&1 && grep -q 391 "$T/vout/canary.log" && grep -q '^VERDICT: PASS' "$T/vout/conformance.log"; then
  pass "verify: long canary and conformance output pass, both logged whole"
else fail "verify pass"; fi
sed -i.bak 's/= 391/= 390/' "$T/prep/verify-chat.py"
if ! "$RUNB" "$SDP" _verify "$T/vout" >/dev/null 2>&1; then pass "verify: a wrong canary answer fails"; else fail "verify accepted 390"; fi

# --- spark-deepseek-prepare-local.sh (pinned to the recipe commit and prepare.sh's sha256, so checked statically)
PL="$REPO_DIR/scripts/backends/spark-deepseek-prepare-local.sh"
code=$(grep -v '^[[:space:]]*#' "$PL")
if ! grep -q 'spark-2' <<<"$code" && grep -q 'W=$(worker_host 1)' <<<"$code"; then pass "prepare-local: the worker is the recipe's WORKER, not a hardcoded spark-2"
else fail "prepare-local worker: $(grep -n 'spark-2' "$PL")"; fi
if [[ -z "$(grep -E '(^|[^-])\bssh\b' <<<"$code" | grep -v 'BatchMode=yes' | grep -v '"\${SSH\[@\]}"')" ]] && grep -q 'SSH=(ssh -n -o BatchMode=yes)' <<<"$code" \
   && grep -q -- '-e "ssh -o BatchMode=yes"' <<<"$code"; then pass "prepare-local: every ssh (and rsync's) is BatchMode=yes"
else fail "prepare-local ssh: $(grep -n 'ssh' "$PL")"; fi
kit=$(grep -n 'KIT_SUB/.git' "$PL" | head -1 | cut -d: -f1); src4=$(grep -n "source <(sed -n '/^# ---\* 4" "$PL" | cut -d: -f1)
if [[ -n "$kit" && -n "$src4" && "$kit" -lt "$src4" ]] && grep -q 'This tool is offline and does not fetch it' "$PL"; then
  pass "prepare-local: a kit without .git fails with an offline message BEFORE prepare.sh's step 4 could fetch it"
else fail "prepare-local kit check: kit=$kit src4=$src4"; fi

# --- usage
if "$RUNB" "$SDP" >/dev/null 2>&1; then fail "no subcommand should exit non-zero"; else pass "no subcommand: usage, non-zero exit"; fi

echo "=== $PASS passed, $FAIL failed, $SKIP skipped ==="
(( FAIL == 0 ))
