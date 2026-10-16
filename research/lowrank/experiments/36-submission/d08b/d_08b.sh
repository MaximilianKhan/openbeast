#!/usr/bin/env bash
# E36-D at Qwen3.5-0.8B: D0 re-derivation gate, then T1.6 (control),
# T1.15 (sampling bound), T1.1 (calibration grid), T1.13 (equalization).
# PLAN.md §0 + §D (registered 2026-10-15 16:45). README.md in this dir maps
# every step to the plan and records the validation run.
#
# Shape (bc_queue.sh style): GPU steps run in this process in the
# foreground; every CPU build runs in ONE background lane that the GPU
# scorer waits on artifact by artifact. Resume-safe everywhere: builds go
# to *.part and are renamed on success, Grams are gated by gram_ok.py,
# scores skip when their log already holds a KLD summary.
#
#   GPU  refs x3 -> capture wiki -> [CPU lane starts] -> 7 more captures
#        -> score D0 -> D0 GATE -> score T1.6 / T1.13 / T1.15 / T1.1
#   CPU  D0 builds -> control -> T1.13 builds -> (waits for the captures)
#        -> T1.15 + T1.1 builds
#
# T1.6 needs a decision first (see README "T1.6"): the registered q4_k
# promotion list cannot reach the crown's bytes at 0.8B. Run with
#   T16_RECIPE=A  (registered list exhausted: 517,487,616 B, -1.12%)
#   T16_RECIPE=B  (list + q5_k second pass: 522,992,640 B, -0.065%)
# Unset -> T1.6 is skipped (logged as BLOCKED); everything else runs.
#
# Run under scripts/gpu-lease.sh. CPU lane: 12 threads.
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/../lib.sh"
DD=$E36/d08b
A=$DD/art                       # 0.8B artifacts (models, adapters, codes)
LD=$E36/logs-d
RES=$E36/results-d.txt
GROOT=$D
# CPU-only validation mode (README "Validation"): D08B_CPU_ONLY=<dir> runs
# the CPU lane alone against stand-in Grams symlinked under <dir>/grams,
# writing every artifact under <dir>. No GPU step runs in this mode.
VAL=${D08B_CPU_ONLY:-}
if [ -n "$VAL" ]; then A=$VAL/art; LD=$VAL/logs; RES=$VAL/results.txt; GROOT=$VAL/grams; fi
mkdir -p "$A" "$LD"
RES_BIN=$OB/llama.cpp/build-research/bin
BF08=$OB/weights/research-staging/Qwen3.5-0.8B-BF16.gguf
Q=$BIN/llama-quantize
X=$L/experiments/04-served-v0/extract_adapter.py
RR=$L/experiments/13-rerounder/e13_reround.py
ALT=$L/experiments/11-alternation/e11_alternate.py
EQ=$DD/e26_equalize_alpha.py
CAL=$DD/calib
PIN=(--tensor-type 'blk\.24\.=q5_k')   # 0.8B MTP/nextn layer (E22/E26/E34)
NT=12
NCH=40                                  # chunks: refs, captures (E22 budget)
GCOUNT=$((NCH * 512))                   # tokens per Gram
T16_RECIPE=${T16_RECIPE:-}
export PYTHONPATH=$OB/llama.cpp/gguf-py
MARK_OK=$LD/.captures-done; MARK_BAD=$LD/.captures-failed
rm -f "$MARK_OK" "$MARK_BAD"

# Gram dirs (16-char names where an imatrix path reaches a quantized file's
# header, so every Q2_K-family file has identical bytes — E26's parity trick)
G() { echo "$GROOT/e36-gram08b-$1"; }
IMW=$(G wiki)/diag.imatrix.gguf

# T1.6 control recipes (computed 2026-10-15 on CPU; README "T1.6").
# Start Q3_K_M (+ MTP pin, which precedes: first match wins). At 0.8B,
# Q3_K_M already holds attn_v, attn_output, attn_qkv, ffn_down at >= q4_k,
# so the registered list promotes attn_k, attn_q, attn_gate, ssm_out,
# ffn_gate, ffn_up (all Q3_K, whole kinds) and is then exhausted.
CTRL_A=(--tensor-type 'attn_k\.weight=q4_k' --tensor-type 'attn_q\.weight=q4_k'
        --tensor-type 'attn_gate\.weight=q4_k' --tensor-type 'ssm_out\.weight=q4_k'
        --tensor-type 'ffn_gate\.weight=q4_k' --tensor-type 'ffn_up\.weight=q4_k')
# predicted 517,487,616 B (crown 523,330,208 B: -1.117%, outside the 0.5% band)
CTRL_B=(--tensor-type 'attn_k\.weight=q5_k' --tensor-type 'attn_v\.weight=q5_k'
        --tensor-type 'attn_output\.weight=q5_k'
        --tensor-type 'blk\.2\.attn_qkv\.weight=q5_k' --tensor-type 'blk\.9\.attn_qkv\.weight=q5_k'
        --tensor-type 'blk\.16\.attn_qkv\.weight=q5_k' --tensor-type 'blk\.22\.attn_qkv\.weight=q5_k'
        --tensor-type 'attn_q\.weight=q4_k' --tensor-type 'attn_gate\.weight=q4_k'
        --tensor-type 'ssm_out\.weight=q4_k' --tensor-type 'ffn_gate\.weight=q4_k'
        --tensor-type 'ffn_up\.weight=q4_k')
# predicted 522,992,640 B (-0.0645%): A, then the same order again at q5_k
# for the q4_k kinds (attn_k, attn_v, attn_output whole; attn_qkv on 4 of
# its 16 q4_k layers, evenly spaced) — NOT the registered rule; README.
CROWN_BYTES=523330208   # Q2_K 436,408,832 + r128 Q8_0 adapter 86,921,376

# ---------------------------------------------------------------- checks
(cd "$L" && sha256sum --quiet -c "$CAL/SHA256SUMS") \
  || { log "calibration texts do not match $CAL/SHA256SUMS — abort"; exit 1; }
case "$T16_RECIPE" in A|B|"") ;; *) log "T16_RECIPE must be A, B or unset"; exit 1;; esac

# ---------------------------------------------------------------- GPU: refs
[ -n "$VAL" ] || {
for c in wiki fw code; do
  gen_ref "$BF08" "$c" "$D/e36-bf16ref08b-$c-$NCH.logits" $NCH 512 99 \
    "$LD/gen-ref08b-$c.log" || exit 1
done

# ---------------------------------------------------------------- GPU: captures
capture08() { # name text
  local out; out=$(G "$1"); local lg=$LD/capture-$1.log
  if python3 "$DD/gram_ok.py" "$out" "$GCOUNT" >/dev/null 2>&1; then
    log "gram exists: $out"; return 0; fi
  rm -rf "$out"; mkdir -p "$out"
  log "capture $1 from $2"
  LLAMA_IMATRIX_GRAM_DIR="$out" "$RES_BIN/llama-imatrix" -m "$BF08" -f "$2" \
    --chunks $NCH -ngl 99 -c 512 -o "$out/diag.imatrix.gguf" > "$lg" 2>&1 \
    || { log "CAPTURE FAILED $1"; return 1; }
  python3 "$DD/gram_ok.py" "$out" "$GCOUNT" --diag \
    || { log "GRAM GATE FAILED $1"; return 1; }
}
capture08 wiki "$D/wikitext-2-raw/wiki.train.raw" || exit 1
}

# ---------------------------------------------------------------- CPU lane
qz() { # out type [imatrix|-] [src] [extra tensor-type args...]
  local out=$1 ty=$2 im=$3 src=$4; shift 4
  [ -s "$out" ] && { log "exists $out"; return 0; }
  local ia=(); [ "$im" != - ] && ia=(--imatrix "$im")
  log "quantize $(basename "$out") ($ty)"
  CUDA_VISIBLE_DEVICES="" "$Q" "${ia[@]}" "${PIN[@]}" "$@" "$src" "$out.part" "$ty" $NT \
    > "$LD/quantize-$(basename "$out" .gguf).log" 2>&1 \
    && mv "$out.part" "$out" && log "  $(stat -c%s "$out") B" \
    || log "QUANTIZE FAILED $out"
}
fp_guard() { # codes-dir base gram-dir : wipe a codes cache made for other inputs
  local cd=$1 fp
  fp="$(sha256sum "$2" | cut -c1-16) $(readlink -f "$3") $(sha256sum "$3/grams.json" | cut -c1-16)"
  if [ -d "$cd" ] && [ "$(cat "$cd/fp.txt" 2>/dev/null)" != "$fp" ]; then
    log "codes cache $cd fingerprint mismatch — wiping"; rm -rf "$cd"; fi
  mkdir -p "$cd"; echo "$fp" > "$cd/fp.txt"
}
rr() { # out base gram-dir codes-dir   (E13 sweep, unchanged; BF16 reference)
  [ -s "$1" ] && { log "exists $1"; return 0; }
  fp_guard "$4" "$2" "$3"
  log "re-round $(basename "$1") with $(basename "$3")"
  python3 "$RR" "$BF08" "$2" "$3" -o "$1.part" --codes-dir "$4" \
    > "$LD/build-$(basename "$1" .gguf).log" 2>&1 \
    && mv "$1.part" "$1" || log "REROUND FAILED $1"
}
fc() { # out base gram-dir   (E22 recipe: extract_adapter.py, exact SVD, r128, Q8_0)
  [ -s "$1" ] && { log "exists $1"; return 0; }
  log "extract $(basename "$1") on $(basename "$2") with $(basename "$3")"
  python3 "$X" "$BF08" "$2" "$3/diag.imatrix.gguf" --gram-dir "$3" --rank 128 \
    --q8-factors -o "$1.part" > "$LD/build-$(basename "$1" .gguf).log" 2>&1 \
    && mv "$1.part" "$1" || log "EXTRACT FAILED $1"
}
alpha_of() { case "$1" in 025) echo 0.25;; 050) echo 0.5;; 075) echo 0.75;; 100) echo 1.0;; esac; }
eq_build() { # tag alpha-code(025|050|075|100) imatrix-mode(imx|noimx)
  local out=$A/q2k-eq-$1.gguf tmp=$A/tmp-bf16-eq-a$2.gguf
  [ -s "$out" ] && { log "exists $out"; return 0; }
  local gd; gd=$(G "a$2")                                 # e36-gram08b-a025 ...
  if [ ! -s "$tmp" ] || [ ! -s "$gd/diag.imatrix.gguf" ]; then
    mkdir -p "$gd"
    log "equalize alpha=$2"
    python3 "$EQ" "$BF08" "$tmp.part" "$IMW" "$gd/diag.imatrix.gguf.part" \
      --alpha "$(alpha_of "$2")" --gram-dir "$(G wiki)" \
      > "$LD/build-eq-a$2.log" 2>&1 \
      && mv "$tmp.part" "$tmp" && mv "$gd/diag.imatrix.gguf.part" "$gd/diag.imatrix.gguf" \
      || { log "EQUALIZE FAILED a$2"; return 1; }
  fi
  if [ "$3" = imx ]; then qz "$out" Q2_K "$gd/diag.imatrix.gguf" "$tmp"
  else qz "$out" Q2_K - "$tmp"; fi
}
cpu_lane() {
  export OMP_NUM_THREADS=$NT OPENBLAS_NUM_THREADS=$NT MKL_NUM_THREADS=$NT CUDA_VISIBLE_DEVICES=""
  local GW; GW=$(G wiki)
  # D0: ladder, one quantize step from BF16, wiki imatrix, MTP pin
  qz "$A/q2k.gguf"     Q2_K    "$IMW" "$BF08"
  qz "$A/iq3xxs.gguf"  IQ3_XXS "$IMW" "$BF08"
  qz "$A/q3km.gguf"    Q3_K_M  "$IMW" "$BF08"
  qz "$A/q4km.gguf"    Q4_K_M  "$IMW" "$BF08"
  qz "$A/iq2m.gguf"    IQ2_M   "$IMW" "$BF08"
  # D0: RR-wiki, fc, alternation x1 (E11: W' = W - C0 -> quantize -> C1)
  rr "$A/q2k-rr-wiki.gguf" "$A/q2k.gguf" "$GW" "$A/codes-rr-wiki"
  fc "$A/fc-wiki.gguf" "$A/q2k.gguf" "$GW"
  if [ ! -s "$A/q2k-alt1.gguf" ] && [ -s "$A/fc-wiki.gguf" ]; then
    [ -s "$A/tmp-wprime.gguf" ] || { log "alternation: W' = W - C0"
      python3 "$ALT" "$BF08" "$A/fc-wiki.gguf" -o "$A/tmp-wprime.gguf.part" \
        > "$LD/build-wprime.log" 2>&1 && mv "$A/tmp-wprime.gguf.part" "$A/tmp-wprime.gguf"; }
    qz "$A/q2k-alt1.gguf" Q2_K "$IMW" "$A/tmp-wprime.gguf"
  fi
  [ -s "$A/q2k-alt1.gguf" ] && rm -f "$A/tmp-wprime.gguf"
  fc "$A/fc-alt1-wiki.gguf" "$A/q2k-alt1.gguf" "$GW"
  if [ -s "$A/q2k-alt1.gguf" ] && [ -s "$A/fc-alt1-wiki.gguf" ]; then
    local cb=$(( $(stat -c%s "$A/q2k-alt1.gguf") + $(stat -c%s "$A/fc-alt1-wiki.gguf") ))
    log "crown bytes $cb (predicted $CROWN_BYTES)$([ $cb -eq $CROWN_BYTES ] || echo '  <-- MISMATCH')"
  fi
  # T1.6 control (one build)
  if [ "$T16_RECIPE" = A ]; then qz "$A/control-A.gguf" Q3_K_M "$IMW" "$BF08" "${CTRL_A[@]}"
  elif [ "$T16_RECIPE" = B ]; then qz "$A/control-B.gguf" Q3_K_M "$IMW" "$BF08" "${CTRL_B[@]}"
  else log "T1.6 BLOCKED: T16_RECIPE unset (README T1.6)"; fi
  # T1.13: imatrix-free pair, then alpha sweep with the (transformed) imatrix
  qz "$A/q2k-noimx.gguf" Q2_K - "$BF08"
  for a in 025 050 075 100; do eq_build "a$a" "$a" imx; done
  eq_build "a100-noimx" 100 noimx
  for a in 025 050 075 100; do
    [ -s "$A/q2k-eq-a$a.gguf" ] && { [ "$a" != 100 ] || [ -s "$A/q2k-eq-a100-noimx.gguf" ]; } \
      && rm -f "$A/tmp-bf16-eq-a$a.gguf"
  done
  for f in q2k-eq-a025 q2k-eq-a050 q2k-eq-a075 q2k-eq-a100 q2k-alt1 q2k-rr-wiki; do
    [ -s "$A/$f.gguf" ] && [ "$(stat -c%s "$A/$f.gguf")" != "$(stat -c%s "$A/q2k.gguf")" ] \
      && log "BYTES DIFFER: $f $(stat -c%s "$A/$f.gguf") vs Q2_K $(stat -c%s "$A/q2k.gguf")"
  done
  # T1.15 + T1.1 need the other captures
  log "CPU lane: waiting for the remaining captures"
  while [ ! -e "$MARK_OK" ]; do
    [ -e "$MARK_BAD" ] && { log "captures failed — CPU lane stops"; return 1; }
    kill -0 "$PARENT" 2>/dev/null || { log "parent gone — CPU lane stops"; return 1; }
    sleep 20
  done
  for s in A B C; do rr "$A/q2k-rr-third$s.gguf" "$A/q2k.gguf" "$(G "third$s")" "$A/codes-rr-third$s"; done
  for m in code fw mix2 m111; do rr "$A/q2k-rr-$m.gguf" "$A/q2k.gguf" "$(G "$m")" "$A/codes-rr-$m"; done
  for m in code fw mix2 m111; do fc "$A/fc-$m.gguf" "$A/q2k.gguf" "$(G "$m")"; done
  log "CPU lane done"
}
PARENT=$$
if [ -n "$VAL" ]; then
  touch "$MARK_OK"; log "CPU-only validation run -> $VAL"
  cpu_lane 2>&1 | tee "$LD/cpu-lane.log"; exit "${PIPESTATUS[0]}"
fi
cpu_lane > "$LD/cpu-lane.log" 2>&1 &
CPU_PID=$!
log "CPU lane pid $CPU_PID (log $LD/cpu-lane.log)"

# ---------------------------------------------------------------- GPU: other captures
ok=1
capture08 code   "$L/experiments/17-task-conditioned/code-train.txt" || ok=0
capture08 fw     "$CAL/fineweb-edu-calib.txt"     || ok=0
capture08 mix2   "$D/mixed-calib2.txt"            || ok=0
capture08 m111   "$CAL/mix111.txt"                || ok=0
capture08 thirdA "$CAL/wiki-train-third-a.txt"    || ok=0
capture08 thirdB "$CAL/wiki-train-third-b.txt"    || ok=0
capture08 thirdC "$CAL/wiki-train-third-c.txt"    || ok=0
if [ $ok = 1 ]; then touch "$MARK_OK"; else touch "$MARK_BAD"; log "a capture failed — T1.1/T1.15 builds will not run"; fi

# ---------------------------------------------------------------- GPU: scoring
wait_for() { # files... ; returns 1 if the CPU lane ended without building one
  local f
  for f in "$@"; do
    while [ ! -s "$f" ]; do
      if ! kill -0 "$CPU_PID" 2>/dev/null; then
        [ -s "$f" ] || { log "NOT BUILT: $f"; return 1; }
      fi
      sleep 15
    done
  done
}
s3() { # label model [--lora adapter]
  local label=$1 model=$2; shift 2
  local need=("$model"); [ $# -ge 2 ] && need+=("$2")
  wait_for "${need[@]}" || return 1
  for c in wiki fw code; do
    score "$RES" "$LD" "$c" "$c" "$D/e36-bf16ref08b-$c-$NCH.logits" 512 "$label" "$model" "$@"
  done
}
# D0
s3 Q2_K            "$A/q2k.gguf"
s3 IQ3_XXS         "$A/iq3xxs.gguf"
s3 Q3_K_M          "$A/q3km.gguf"
s3 Q4_K_M          "$A/q4km.gguf"
s3 IQ2_M           "$A/iq2m.gguf"
s3 Q2K-rr-wiki     "$A/q2k-rr-wiki.gguf"
s3 Q2K+fc-wiki     "$A/q2k.gguf"      --lora "$A/fc-wiki.gguf"
s3 Q2Kalt1+fc-wiki "$A/q2k-alt1.gguf" --lora "$A/fc-alt1-wiki.gguf"
python3 "$DD/d_stats.py" gate "$LD" wiki Q2K-rr-wiki Q2_K -2 | tee -a "$LD/gate.txt"
if [ "${PIPESTATUS[0]}" != 0 ]; then
  log "D0 GATE FAILED — ablations not scored (PLAN §D). CPU lane left to finish its builds."
  wait "$CPU_PID"; exit 2
fi
# T1.6
case "$T16_RECIPE" in
  A) s3 CONTROL-A "$A/control-A.gguf" ;;
  B) s3 CONTROL-B "$A/control-B.gguf" ;;
  *) log "T1.6 BLOCKED: set T16_RECIPE=A|B and rerun (resume-safe)" ;;
esac
# T1.13
s3 Q2_K-noimx          "$A/q2k-noimx.gguf"
s3 Q2K-eq-a1.00-noimx  "$A/q2k-eq-a100-noimx.gguf"
s3 Q2K-eq-a0.25        "$A/q2k-eq-a025.gguf"
s3 Q2K-eq-a0.50        "$A/q2k-eq-a050.gguf"
s3 Q2K-eq-a0.75        "$A/q2k-eq-a075.gguf"
s3 Q2K-eq-a1.00        "$A/q2k-eq-a100.gguf"
# T1.15 (wiki.train thirds) and T1.1 (calibration grid; the wiki arm is D0's
# Q2K-rr-wiki and Q2K+fc-wiki — same base, same Grams, not rebuilt)
s3 Q2K-rr-thirdA "$A/q2k-rr-thirdA.gguf"
s3 Q2K-rr-thirdB "$A/q2k-rr-thirdB.gguf"
s3 Q2K-rr-thirdC "$A/q2k-rr-thirdC.gguf"
for m in code fw mix2 m111; do s3 "Q2K-rr-$m" "$A/q2k-rr-$m.gguf"; done
for m in code fw mix2 m111; do s3 "Q2K+fc-$m" "$A/q2k.gguf" --lora "$A/fc-$m.gguf"; done
wait "$CPU_PID"

# ---------------------------------------------------------------- paired readouts (CPU)
P=$E36/paired-d.txt
{
  echo "# E36-D 0.8B paired readouts $(date +%F' '%T) (e36_stats.py: A - B; d_stats.py)"
  for c in wiki fw code; do
    pairs=(Q2K-rr-wiki:Q2_K Q2K+fc-wiki:Q2_K Q2Kalt1+fc-wiki:Q2K+fc-wiki
           Q2Kalt1+fc-wiki:Q3_K_M Q2Kalt1+fc-wiki:Q4_K_M Q2Kalt1+fc-wiki:IQ3_XXS
           Q2K-rr-wiki:IQ2_M Q2K-rr-wiki:IQ3_XXS)
    [ -n "$T16_RECIPE" ] && pairs+=("CONTROL-$T16_RECIPE:Q2Kalt1+fc-wiki"
                                     "CONTROL-$T16_RECIPE:Q2K+fc-wiki"
                                     "Q2K+fc-wiki:CONTROL-$T16_RECIPE")
    pairs+=(Q2K-eq-a1.00-noimx:Q2_K-noimx Q2K-eq-a0.25:Q2_K Q2K-eq-a0.50:Q2_K
            Q2K-eq-a0.75:Q2_K Q2K-eq-a1.00:Q2_K
            Q2K-rr-thirdA:Q2_K Q2K-rr-thirdB:Q2_K Q2K-rr-thirdC:Q2_K)
    for m in wiki code fw mix2 m111; do pairs+=("Q2K-rr-$m:Q2_K" "Q2K+fc-$m:Q2_K"); done
    have=()
    for p in "${pairs[@]}"; do
      [ -f "$LD/$c-${p%%:*}.log" ] && [ -f "$LD/$c-${p##*:}.log" ] && have+=("$p") \
        || echo "(missing log for $p)"
    done
    echo; echo "## $c"
    python3 "$E36/e36_stats.py" "$LD" "$c" "${have[@]}" 2>&1
    python3 "$DD/d_stats.py" spread "$LD" "$c" Q2K-rr-thirdA Q2K-rr-thirdB Q2K-rr-thirdC 2>&1
  done
} > "$P"
log "E36-D 0.8B DONE -> $RES, $P"
