#!/bin/bash
# spark-deepseek-pack.sh: the pure pieces of the DeepSeek pack swap. Hermetic:
# a scratch local.sh and pack folder, no systemctl, ssh or docker. The swap
# itself (prepare, restart, verify, rollback) needs both Sparks; its run record
# is in docs/SPARK_HYDRA.md "DeepSeek packs: spark-deepseek-pack".
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SDP="$REPO_DIR/scripts/backends/spark-deepseek-pack.sh"
PASS=0 FAIL=0 SKIP=0
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
bash "$SDP" _render_local "$T/local.sh" org/a 1111111111111111111111111111111111111111 /hf/worker-rank-cache-11111111 /src/a > "$T/one" 2>&1
if [[ "$(head -3 "$T/one")" == $'WORKER=spark-2\nHF_CACHE=/hf\nCONTEXT=524288' ]] && grep -qx "$B" "$T/one" && grep -qx "$E" "$T/one" \
   && grep -qx 'MODEL_ID=org/a' "$T/one" && grep -qx 'MODEL_SRC=/src/a' "$T/one"; then
  pass "render: operator settings kept, managed block appended"
else fail "render append: $(cat "$T/one")"; fi

# --- render_local again: the block is replaced, never duplicated
bash "$SDP" _render_local "$T/one" org/b 2222222222222222222222222222222222222222 /hf/worker-rank-cache-22222222 /src/b > "$T/two" 2>&1
if [[ "$(grep -cx "$B" "$T/two")" == 1 && "$(grep -c '^MODEL_ID=' "$T/two")" == 1 ]] && grep -qx 'MODEL_ID=org/b' "$T/two" \
   && grep -qx 'WORKER_CACHE_DIR=/hf/worker-rank-cache-22222222' "$T/two" && ! grep -q 'org/a' "$T/two"; then
  pass "render: second swap replaces the block in place"
else fail "render replace: $(cat "$T/two")"; fi

# --- the result is valid bash that sets exactly the four variables
if out=$(bash -c "set -u; source '$T/two'; echo \"\$MODEL_ID \$MODEL_REVISION \$WORKER_CACHE_DIR \$MODEL_SRC\"" 2>&1) \
   && [[ "$out" == "org/b 2222222222222222222222222222222222222222 /hf/worker-rank-cache-22222222 /src/b" ]]; then
  pass "render: the managed local.sh sources cleanly"
else fail "render source: $out"; fi

# --- pack_rev: stable, size-sensitive, independent of non-shard files (GNU stat)
if stat -c %s "$T/local.sh" >/dev/null 2>&1; then
  mkdir -p "$T/p"; printf aaaa > "$T/p/model-00001-of-00002.safetensors"; printf bb > "$T/p/model-00002-of-00002.safetensors"
  r1=$(bash "$SDP" _pack_rev "$T/p"); echo notes > "$T/p/README.md"; r2=$(bash "$SDP" _pack_rev "$T/p")
  printf b >> "$T/p/model-00002-of-00002.safetensors"; r3=$(bash "$SDP" _pack_rev "$T/p")
  if [[ "$r1" =~ ^[0-9a-f]{40}$ && "$r1" == "$r2" && "$r1" != "$r3" ]]; then pass "pack_rev: 40 hex, ignores non-shards, changes with a shard's size"
  else fail "pack_rev: $r1 / $r2 / $r3"; fi
else skip "pack_rev needs GNU stat (runs on the Sparks)"; fi

# --- usage
if bash "$SDP" >/dev/null 2>&1; then fail "no subcommand should exit non-zero"; else pass "no subcommand: usage, non-zero exit"; fi

echo "=== $PASS passed, $FAIL failed, $SKIP skipped ==="
(( FAIL == 0 ))
