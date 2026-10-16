#!/usr/bin/env bash
# E36-D T1.9: recovery curve at 27B with the adapter recipe held fixed
# (PLAN.md §D, registered 2026-10-15 16:45). Replaces the confounded F7 curve.
#   bases  Q2_K (E27, exists), IQ2_XS, IQ1_S — one quantize step from BF16,
#          E27 imatrix (data/gram27b-bf16), MTP pin blk\.64\.=q5_k
#   fc     the SAME recipe on each base: e27_extract.py (fc whitening, rsvd,
#          float32, seed-fixed), r128, Q8_0, the E27 non-FFN target set
#          (--rank-map ffn_gate=0,ffn_up=0,ffn_down=0), Grams
#          data/gram27b-e36-wiki (captured by bc_queue.sh §B)
#   score  base and base+fc on wiki and fw vs the E36-A 100-chunk refs;
#          logs in logs-a/ (bc_queue.sh convention), rows in results-d27.txt
#   read   recovery = (KLD_base - KLD_base+fc) / KLD_base, paired bootstrap
#          95% CI (d_stats.py recovery) -> recovery-t19.txt
#
# Order: CPU lane (background) quantizes IQ2_XS and IQ1_S, then extracts the
# three adapters once the Grams verify; this process scores each artifact
# on the GPU as soon as it exists. Resume-safe. Run under
# scripts/gpu-lease.sh AFTER bc_queue.sh's captures (if the Grams are not
# there and no 27B capture is running, this script stops instead of
# holding the lease while waiting).
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/../lib.sh"
DD=$E36/d08b
LD=$E36/logs-a
RES=$E36/results-d27.txt
X27=$E27/e27_extract.py
IM27=$D/gram27b-bf16/diag.imatrix.gguf
GR=$D/gram27b-e36-wiki
GCOUNT=$((48 * 512))                       # bc_queue.sh capture: --chunks 48 -c 512
NT=12
PIN=(--tensor-type 'blk\.64\.=q5_k')
export PYTHONPATH=$OB/llama.cpp/gguf-py
mkdir -p "$LD"

for c in wiki fw; do
  [ -s "$D/e36-bf16ref27b-$c-100.logits" ] && grep -q "Final estimate" "$LD/gen-ref27b-$c.log" 2>/dev/null \
    || { log "missing E36-A reference for $c (run a_ladder27.sh first)"; exit 1; }
done

grams_ready() {
  python3 "$DD/gram_ok.py" "$GR" "$GCOUNT" >/dev/null 2>&1 \
    && grep -q "Final estimate" "$E36/logs-b/capture-gram27b-e36-wiki.log" 2>/dev/null
}
capture_running() { pgrep -f "llama-imatrix.*Qwen3.6-27B" >/dev/null; }
if ! grams_ready && ! capture_running; then
  log "Grams $GR not ready and no 27B capture running — run bc_queue.sh's captures first"; exit 1
fi

Q2K=$E27/h27bf16-Q2_K.gguf
IQ2XS=$E36/h27bf16-IQ2_XS.gguf
IQ1S=$E36/h27bf16-IQ1_S.gguf
AD() { echo "$E36/adapter-t19-fc-r128q8-$1.gguf"; }

cpu_lane() {
  export OMP_NUM_THREADS=$NT OPENBLAS_NUM_THREADS=$NT MKL_NUM_THREADS=$NT CUDA_VISIBLE_DEVICES=""
  local b ty out
  for ty in IQ2_XS IQ1_S; do
    out=$E36/h27bf16-$ty.gguf
    [ -s "$out" ] && { log "exists $out"; continue; }
    log "quantize $ty"
    "$BIN/llama-quantize" --imatrix "$IM27" "${PIN[@]}" "$BF27" "$out.part" "$ty" $NT \
      > "$LD/quantize-$ty.log" 2>&1 && mv "$out.part" "$out" \
      && log "  $(stat -c%s "$out") B" || log "QUANTIZE FAILED $ty"
  done
  log "waiting for Grams $GR"
  until grams_ready; do
    capture_running || { grams_ready && break; log "Grams never verified — CPU lane stops"; return 1; }
    sleep 60
  done
  for b in Q2_K IQ2_XS IQ1_S; do
    local base cache ad fp; ad=$(AD "$b")
    case $b in Q2_K) base=$Q2K;; IQ2_XS) base=$IQ2XS;; IQ1_S) base=$IQ1S;; esac
    [ -s "$ad" ] && { log "exists $ad"; continue; }
    [ -s "$base" ] || { log "no base $base"; continue; }
    # e27_extract caches by tensor name only (E27 README tool hazard):
    # one cache dir per base, fingerprinted here, wiped on mismatch.
    cache=$E36/fc-cache-t19-$b
    fp="$(readlink -f "$base") $(stat -c'%s %Y' "$base") $(readlink -f "$GR") $(stat -c'%s %Y' "$GR/grams.json")"
    if [ -d "$cache" ] && [ "$(cat "$cache/fp.txt" 2>/dev/null)" != "$fp" ]; then
      log "cache $cache fingerprint mismatch — wiping"; rm -rf "$cache"; fi
    mkdir -p "$cache"; echo "$fp" > "$cache/fp.txt"
    log "extract fc r128 Q8_0 non-FFN on $b"
    local rc=3
    while [ $rc = 3 ]; do
      python3 "$X27" "$BF27" "$base" "$GR/diag.imatrix.gguf" -o "$ad.part" --rank 128 \
        --rank-map ffn_gate=0,ffn_up=0,ffn_down=0 --gram-dir "$GR" --q8-factors \
        --cache-dir "$cache" >> "$LD/extract-t19-$b.log" 2>&1
      rc=$?
    done
    if [ $rc = 0 ]; then mv "$ad.part" "$ad"; log "  adapter $(stat -c%s "$ad") B"
    else log "EXTRACT FAILED $b (rc $rc)"; fi
  done
  log "CPU lane done"
}
cpu_lane > "$LD/cpu-lane-t19.log" 2>&1 &
CPU_PID=$!
log "T1.9 CPU lane pid $CPU_PID (log $LD/cpu-lane-t19.log)"

wait_for() {
  local f
  for f in "$@"; do
    while [ ! -s "$f" ]; do
      kill -0 "$CPU_PID" 2>/dev/null || { [ -s "$f" ] || { log "NOT BUILT: $f"; return 1; }; }
      sleep 30
    done
  done
}
row_from_log() { # tag label model : add a results row for a log scored elsewhere
  local lg=$LD/$1-$2.log
  grep -q "^$1 $2 " "$RES" 2>/dev/null && return 0
  grep -q "Mean    KLD" "$lg" 2>/dev/null || return 1
  { printf "%s %s bytes=%s " "$1" "$2" "$(stat -c%s "$3")"
    grep -E "Mean PPL\(Q\) " "$lg" | head -1 | tr -s ' ' | tr -d '\n'; printf " "
    grep -E "Mean    KLD" "$lg" | tr -s ' ' | tr -d '\n'; printf " "
    grep -E "Same top p" "$lg" | tail -1 | tr -s ' '; } >> "$RES"
}
s2() { # label model [--lora adapter]
  local label=$1 model=$2; shift 2
  local need=("$model"); [ $# -ge 2 ] && need+=("$2")
  wait_for "${need[@]}" || return 1
  for c in wiki fw; do
    score "$RES" "$LD" "$c" "$c" "$D/e36-bf16ref27b-$c-100.logits" 512 "$label" "$model" "$@"
    row_from_log "$c" "$label" "$model"
  done
}
# bare bases first (Q2_K is E36-A's row: same file, ref and build — its
# logs-a/{wiki,fw}-Q2_K.log are reused, never re-scored)
s2 Q2_K         "$Q2K"
s2 T19-IQ2_XS   "$IQ2XS"
s2 T19-IQ1_S    "$IQ1S"
s2 T19-Q2K+fc   "$Q2K"   --lora "$(AD Q2_K)"
s2 T19-IQ2XS+fc "$IQ2XS" --lora "$(AD IQ2_XS)"
s2 T19-IQ1S+fc  "$IQ1S"  --lora "$(AD IQ1_S)"
wait "$CPU_PID"

{
  echo "# E36-D T1.9 recovery at 27B $(date +%F' '%T)"
  for c in wiki fw; do
    for p in Q2_K:T19-Q2K+fc T19-IQ2_XS:T19-IQ2XS+fc T19-IQ1_S:T19-IQ1S+fc; do
      python3 "$DD/d_stats.py" recovery "$LD" "$c" "${p%%:*}" "${p##*:}" 2>&1
    done
    python3 "$E36/e36_stats.py" "$LD" "$c" T19-Q2K+fc:Q2_K T19-IQ2XS+fc:T19-IQ2_XS T19-IQ1S+fc:T19-IQ1_S 2>&1
  done
} > "$E36/recovery-t19.txt"
log "E36-D T1.9 DONE -> $RES, $E36/recovery-t19.txt"
