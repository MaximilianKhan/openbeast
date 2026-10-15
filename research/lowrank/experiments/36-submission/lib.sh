# E36 shared helpers. Source me; do not run.
# All scoring runs on the CURRENT build (b10865, post-#28068 GDN-norm fix).
# Every comparison in E36 is scored against a reference generated on the same
# build, so no row in an E36 table mixes graphs (recon-2026-09-08 §A2).
OB=/home/max/Documents/openbeast
L=$OB/research/lowrank
E36=$L/experiments/36-submission
D=$L/data
E27=$L/experiments/27-bf16-rederivation
BIN=$OB/llama.cpp/build/bin
PPL=$BIN/llama-perplexity
BF27=$OB/weights/research-staging/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf

# corpus name -> text file
corpus_file() {
  case "$1" in
    wiki) echo "$D/wikitext-2-raw/wiki.test.raw" ;;
    fw)   echo "$L/experiments/32-t117-gsq-head-to-head/fineweb-edu-control.txt" ;;
    code) echo "$L/experiments/17-task-conditioned/code-test.txt" ;;
    *) echo "unknown corpus $1" >&2; return 1 ;;
  esac
}

log() { echo "[$(date +%F' '%T)] $*"; }

# gen_ref <model> <corpus> <out.logits> <chunks> <ctx> <ngl> <logfile>
gen_ref() {
  local m="$1" c="$2" out="$3" n="$4" ctx="$5" ngl="$6" lg="$7"
  if [ -s "$out" ] && grep -q "Final estimate" "$lg" 2>/dev/null; then
    log "ref exists: $out"; return 0; fi
  log "gen ref $out ($c, $n chunks, ctx $ctx)"
  "$PPL" -m "$m" -f "$(corpus_file "$c")" --save-all-logits "$out" \
    -ngl "$ngl" -c "$ctx" --chunks "$n" --no-warmup > "$lg" 2>&1 \
    || { log "REF FAILED $out"; return 1; }
  log "ref done: $(grep -oE 'Final estimate: PPL = [0-9.]+ \+/- [0-9.]+' "$lg")"
}

# score <results.txt> <logdir> <tag> <corpus> <ref.logits> <ctx> <label> <model> [extra...]
# Resume-safe: skips when the log already has a KLD summary.
score() {
  local res="$1" ld="$2" tag="$3" c="$4" ref="$5" ctx="$6" label="$7" model="$8"; shift 8
  local lg="$ld/$tag-$label.log"
  if grep -q "Mean    KLD" "$lg" 2>/dev/null; then log "SKIP $tag $label"; return 0; fi
  [ -f "$model" ] || { log "MISSING model $model ($label)"; return 1; }
  "$PPL" -m "$model" "$@" -f "$(corpus_file "$c")" --kl-divergence \
    --kl-divergence-base "$ref" -ngl 99 -c "$ctx" --no-warmup > "$lg" 2>&1 \
    || { log "FAILED $tag $label"; return 1; }
  { printf "%s %s bytes=%s " "$tag" "$label" "$(stat -c%s "$model")"
    grep -E "Mean PPL\(Q\) " "$lg" | head -1 | tr -s ' ' | tr -d '\n'; printf " "
    grep -E "Mean    KLD" "$lg" | tr -s ' ' | tr -d '\n'; printf " "
    grep -E "Same top p" "$lg" | tail -1 | tr -s ' '; } >> "$res"
  log "$(tail -1 "$res")"
}
