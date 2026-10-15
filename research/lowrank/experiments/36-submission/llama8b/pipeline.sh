#!/usr/bin/env bash
# E36-L: the non-Qwen model. Llama-3.1-8B base (NousResearch mirror, BF16
# GGUF converted here; PROVENANCE.md), run through the 27B protocol (E27):
# single-step quants from BF16, Gram+imatrix capture on BF16, ladder, MIXED
# base, same-byte CONTROL, fc r128 Q8 adapter, two re-round arms, scored on
# wiki/fw/code (100 chunks x 512) against BF16 logits on the current build.
#
# NOT RUN YET. Run under the lease, with nothing else on the card:
#   scripts/gpu-lease.sh run "E36-L llama8b" -- \
#     bash research/lowrank/experiments/36-submission/llama8b/pipeline.sh
# Resume-safe: every step skips when its output exists and is complete;
# re-invoke after any failure. CPU work is capped at 12 threads.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../lib.sh"          # OB L E36 D E27 BIN PPL, log gen_ref score

LL=$HERE                                   # scripts, logs, results
LD=$LL/logs; mkdir -p "$LD"
RES=$LL/results.txt
W=$OB/weights/research-staging/llama31-8b  # weights
BF=$W/Meta-Llama-3.1-8B-BF16.gguf
BF_SHA=a49f6a496573d6a70f0a4998944d366ec49914c9db8b551a009a581232a21238
RES_BIN=$OB/llama.cpp/build-research/bin   # b10865 + Gram patch
EX=$E27/e27_extract.py
RR=$L/experiments/13-rerounder/e13_reround.py
GW=$D/e36-llama8b-gram-wiki                # Grams + diag.imatrix.gguf
GM=$D/e36-llama8b-gram-mixed
IM=$GW/diag.imatrix.gguf                   # THE imatrix for every quant
N=100
export OMP_NUM_THREADS=12 OPENBLAS_NUM_THREADS=12 MKL_NUM_THREADS=12
NT=12

# ---- preconditions
case "$("$OB/scripts/gpu-lease.sh" check >/dev/null 2>&1; echo $?)" in
  0) ;;                                   # we hold the lease
  *) log "not under our GPU lease — run via scripts/gpu-lease.sh run"; exit 2 ;;
esac
[ -s "$BF" ] || { log "missing $BF"; exit 1; }
if [ ! -f "$LL/.bf16-verified" ]; then
  log "verifying BF16 sha256"
  echo "$BF_SHA  $BF" | sha256sum -c --quiet || { log "BF16 SHA MISMATCH"; exit 1; }
  touch "$LL/.bf16-verified"
fi
# peak footprint from scratch ~85 GB (README "Disk"); a resumed run needs less
FREE_GB=$(df -BG --output=avail "$OB" | tail -1 | tr -dc 0-9)
if [ ! -s "$W/llama8b-Q2K-rr-mixed.gguf" ] && [ "$FREE_GB" -lt 95 ]; then
  log "only ${FREE_GB} GB free, need ~85 GB + margin — refusing"; exit 1
fi

# ---- 1. BF16 reference logits, three corpora (GPU, 8B BF16 fits: -ngl 99)
for c in wiki fw code; do
  gen_ref "$BF" "$c" "$D/e36-llama8b-bf16ref-$c-$N.logits" $N 512 99 \
    "$LD/gen-ref-$c.log" || exit 1
done

# ---- 2. Gram + imatrix capture on the BF16 (GPU forward, CPU Gram
#         accumulation; E27 pattern: 48 chunks x 512, research build)
capture() { # outdir corpus-file tag
  local out=$1 lg=$LD/capture-$3.log
  if [ -f "$out/grams.json" ] && [ -s "$out/diag.imatrix.gguf" ] \
     && grep -q "imatrix" "$lg" 2>/dev/null; then log "gram exists: $out"; return 0; fi
  rm -rf "$out"; mkdir -p "$out"
  log "capture $3 from $2"
  LLAMA_IMATRIX_GRAM_DIR="$out" "$RES_BIN/llama-imatrix" -m "$BF" -f "$2" \
    --chunks 48 -ngl 99 -c 512 -o "$out/diag.imatrix.gguf" > "$lg" 2>&1 \
    || { log "CAPTURE FAILED $3"; return 1; }
  # 32 layers x {attn_k, attn_output, ffn_up, ffn_down} = 128 Grams
  local ng; ng=$(ls "$out"/*.gram.bin | wc -l)
  [ "$ng" -eq 128 ] || { log "CAPTURE $3: $ng gram files, expected 128"; return 1; }
  log "capture done: $ng grams"
}
capture "$GW" "$D/wikitext-2-raw/wiki.train.raw" wiki || exit 1
capture "$GM" "$D/mixed-calib2.txt"              mixed || exit 1

# ---- 3. quantizations, ONE step from BF16, wiki imatrix (CPU, seconds each)
# predicted tensor bytes (README byte table; bytes.py on an imatrix dry-run).
# File = tensor bytes + 7,835,808 header + the imatrix KVs (a few hundred B).
build() { # name type predicted-tensor-bytes [extra...]
  local name=$1 ty=$2 pred=$3; shift 3
  local out=$W/llama8b-$name.gguf
  if [ -s "$out" ]; then log "exists $out"; return 0; fi
  log "quantize $name ($ty)"
  "$BIN/llama-quantize" --imatrix "$IM" "$@" "$BF" "$out.part" "$ty" $NT \
    > "$LD/quantize-$name.log" 2>&1 || { log "QUANTIZE FAILED $name"; return 1; }
  mv "$out.part" "$out"
  local got hdr; got=$(stat -c%s "$out"); hdr=$((got - pred))
  if [ $hdr -lt 7835808 ] || [ $hdr -gt 7837000 ]; then
    log "BYTES OFF-PLAN $name: file $got, predicted tensors $pred (header $hdr)"
  else
    log "built $name: $got bytes (header $hdr, on plan)"
  fi
}
CONTROL_ARGS=()   # fixed rule, control_plan.py: attn_k -> q4_k at 21 of 32 layers
for li in 0 2 3 5 6 8 9 11 12 14 16 17 19 20 22 23 25 26 28 29 31; do
  CONTROL_ARGS+=(--tensor-type "blk\\.$li\\.attn_k=q4_k")
done
build Q2_K    Q2_K    3171295488 || exit 1
build Q3_K_S  Q3_K_S  3656663296 || exit 1
build Q3_K_M  Q3_K_M  4011081984 || exit 1
build IQ3_XXS IQ3_XXS 3267076352 || exit 1
build IQ3_XS  IQ3_XS  3510911232 || exit 1
build IQ2_M   IQ2_M   2940444928 || exit 1
build Q4_K_M  Q4_K_M  4912898304 || exit 1
build MIXED   Q2_K    3552977152 \
  --tensor-type 'ffn_gate=q3_k' --tensor-type 'ffn_up=q3_k' \
  --tensor-type 'ffn_down=q3_k' || exit 1
build CONTROL Q3_K_S  3668361472 "${CONTROL_ARGS[@]}" || exit 1

# ---- 4. fc adapter: r128, Q8_0, non-FFN (attn_q/k/v/output), wiki Grams
# e27_extract caches per-tensor factors keyed by NAME ONLY (E27 tool hazard):
# the cache is stamped with its inputs and wiped on any mismatch.
stamp() { # files... -> one line identifying the inputs
  for f in "$@"; do printf '%s:%s:%s ' "$f" "$(stat -c%s "$f")" "$(stat -c%Y "$f")"; done
}
guard_cache() { # dir stamp
  if [ -d "$1" ] && [ "$(cat "$1/STAMP" 2>/dev/null)" != "$2" ]; then
    log "cache $1 stamp mismatch — wiping"; rm -rf "$1"; fi
  mkdir -p "$1"; echo "$2" > "$1/STAMP"
}
FC=$W/llama8b-MIXED-fc-r128q8.gguf
if [ ! -s "$FC" ]; then
  CD=$W/fc-cache-MIXED-wiki
  guard_cache "$CD" "$(stamp "$BF" "$W/llama8b-MIXED.gguf" "$IM" "$GW/grams.json")"
  log "extract fc adapter"
  for _ in $(seq 1 20); do
    python3 "$EX" "$BF" "$W/llama8b-MIXED.gguf" "$IM" -o "$FC.part" \
      --rank 128 --rank-map ffn_gate=0,ffn_up=0,ffn_down=0 \
      --gram-dir "$GW" --q8-factors --cache-dir "$CD" --time-budget 1800 \
      >> "$LD/extract-fc.log" 2>&1
    rc=$?; [ $rc -eq 3 ] && continue; break
  done
  [ "$rc" -eq 0 ] && mv "$FC.part" "$FC" || { log "EXTRACT FAILED rc=$rc"; exit 1; }
  got=$(stat -c%s "$FC")
  [ "$got" -eq 115885248 ] && log "adapter $got bytes (on plan)" \
    || log "ADAPTER BYTES OFF-PLAN: $got vs 115885248"
  grep -c " fc captured" "$LD/extract-fc.log" | xargs -I{} log "fc-whitened tensors: {} (expect 128)"
fi

# ---- 5. re-round arms on the Q2_K base (CPU, ~1 h each), background lane
cpu_lane() {
  local q2=$W/llama8b-Q2_K.gguf
  for arm in wiki mixed; do
    local out=$W/llama8b-Q2K-rr-$arm.gguf g=$D/e36-llama8b-gram-$arm
    [ -s "$out" ] && { log "exists $out"; continue; }
    local cd=$W/codes-Q2K-$arm
    guard_cache "$cd" "$(stamp "$BF" "$q2" "$g/grams.json")"
    log "re-round $arm"
    python3 "$RR" "$BF" "$q2" "$g" -o "$out.part" --codes-dir "$cd" \
      > "$LD/reround-$arm.log" 2>&1 && mv "$out.part" "$out" \
      && rm -rf "$cd" || log "REROUND FAILED $arm"   # codes: 4.4 GB/arm
  done
  # E36-E free-grid arm: mixed Grams, the base file's (wiki) imatrix, as at 27B
  local fg=$W/llama8b-Q2K-freegrid-mixed.gguf fcd=$W/codes-freegrid-mixed
  if [ ! -s "$fg" ]; then
    guard_cache "$fcd" "$(stamp "$BF" "$q2" "$GM/grams.json" "$IM")"
    log "free-grid mixed"
    python3 "$E36/freegrid/e36_freegrid.py" "$BF" "$q2" "$GM" --imatrix "$IM" \
      --codes-dir "$fcd" -o "$fg.part" > "$LD/freegrid-mixed.log" 2>&1 \
      && mv "$fg.part" "$fg" && rm -rf "$fcd" || log "FREEGRID FAILED"
  fi
  log "CPU lane done"
}
cpu_lane >> "$LD/cpu-lane.log" 2>&1 &
CPU_PID=$!

# ---- 6. scoring (GPU) — everything but the re-round arms first
configs() { # label|model|extra
  echo "Q2_K|$W/llama8b-Q2_K.gguf|"
  echo "IQ2_M|$W/llama8b-IQ2_M.gguf|"
  echo "IQ3_XXS|$W/llama8b-IQ3_XXS.gguf|"
  echo "IQ3_XS|$W/llama8b-IQ3_XS.gguf|"
  echo "MIXEDbare|$W/llama8b-MIXED.gguf|"
  echo "Q3_K_S|$W/llama8b-Q3_K_S.gguf|"
  echo "CONTROL|$W/llama8b-CONTROL.gguf|"
  echo "MIXEDfc|$W/llama8b-MIXED.gguf|--lora $FC"
  echo "Q3_K_M|$W/llama8b-Q3_K_M.gguf|"
  echo "Q4_K_M|$W/llama8b-Q4_K_M.gguf|"
}
for c in wiki fw code; do
  ref="$D/e36-llama8b-bf16ref-$c-$N.logits"
  while IFS='|' read -r label model extra; do
    # shellcheck disable=SC2086
    score "$RES" "$LD" "$c" "$c" "$ref" 512 "$label" "$model" $extra
  done < <(configs)
done

wait "$CPU_PID"
for c in wiki fw code; do
  ref="$D/e36-llama8b-bf16ref-$c-$N.logits"
  score "$RES" "$LD" "$c" "$c" "$ref" 512 Q2Krr-wiki  "$W/llama8b-Q2K-rr-wiki.gguf"
  score "$RES" "$LD" "$c" "$c" "$ref" 512 Q2Krr-mixed "$W/llama8b-Q2K-rr-mixed.gguf"
  score "$RES" "$LD" "$c" "$c" "$ref" 512 Q2Kfreegrid-mixed "$W/llama8b-Q2K-freegrid-mixed.gguf"
done

# ---- 7. paired statistics (CPU) for the PLAN §A/§B pairs, per corpus
for c in wiki fw code; do
  python3 "$E36/e36_stats.py" "$LD" "$c" \
    MIXEDfc:Q3_K_S MIXEDfc:CONTROL MIXEDfc:MIXEDbare IQ3_XS:MIXEDfc \
    IQ3_XS:Q3_K_S CONTROL:Q3_K_S Q2Krr-wiki:Q2_K Q2Krr-mixed:Q2_K \
    Q2Krr-mixed:IQ2_M Q2Krr-mixed:IQ3_XXS Q2Kfreegrid-mixed:Q2Krr-mixed Q2Kfreegrid-mixed:Q2_K > "$LL/paired-$c.txt" 2>&1 \
    || log "STATS FAILED $c"
done
log "E36-L DONE"
