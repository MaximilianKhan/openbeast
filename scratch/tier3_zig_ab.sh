#!/usr/bin/env bash
# =============================================================================
# Tier-3 zig-0.16 awareness pack — PRE-REGISTERED zig-only mini-A/B
# docs/LANG_AWARENESS_PLAN.md §5 (Tier 3) + §7 (A/B design),
# scratch/BEAST_ASSIST_ROADMAP-2026-09-10.md R2 / Do-Next 3.
#
# GPU job — Max-triggered only. Never run while another sweep holds the GPU
# (it starts/stops llama-server via benchmark_all.py). ~1-1.25 h GPU total.
#
# DESIGN (fixed before any cell runs):
#   units   : the zig variants of the pinned v5-fast suite (30 units), derived
#             at script time from evals/suites/v5-fast.json + evals/tasks/ so
#             the list can never be hand-typed stale. EXTRA_UNITS may add the
#             §7 pre-flight unit 158_karatsuba_bytes_f (assumed_failed).
#   decode  : --greedy (low-churn eval mode, own cache era). CORRECTED
#             2026-09-29: the design assumed a zig churn floor of ~0 under
#             greedy. It is not ~0 in this regime — greedy at --jobs 4 against
#             the -np 6 --kv-unified server is not single-slot, batch
#             composition moves the logits, and the 09-17 same-config
#             replicates flipped 9/30 (P0a vs P0b) and 8/30 (P1a vs P1b):
#             ~30%, as high as sampled mode. The exact McNemar holds whatever
#             the churn; the POWER premise does not — one 30-unit pair carries
#             ~9 null discordants (pair 1 alone p=0.057, pair 0 p=0.27).
#             Size a future arm on ~30%, or run single-slot
#             (scratch/greedy_floor.sh --single-slot measures that floor).
#   thinking: OPENBEAST_REASONING_BUDGET=20480 (per-request cap; the serve
#             scripts already default to it — exported so provenance and the
#             .rb20480 cache component are explicit).
#   diag    : OFF in every cell (BEAST_ASSIST=0). This isolates the PACK
#             effect; packs+assist interaction is a later arm, not this one.
#   jobs    : 4 (non-MTP configs, server -np clamps automatically).
#   cells   : P0a/P0b packs OFF ×2, P1a/P1b packs ON ×2 on the default
#             Qwen3.8-27B-Uncensored Q5 (non-MTP); replicate 'b' runs
#             --no-cache so it is a true second sample. C1 = champion
#             (Qwen 27B Q5_K_XL, slug qwen-27b-q5) packs ON guard cell;
#             C0 = champion packs OFF reference (SKIP_C0=1 to skip if a fresh
#             greedy zig baseline for the champion already exists — then pass
#             its results file to tier3_verdict.py --c0).
#   era     : ON cells carry cache component pack1-<sha8 of agents/packs/
#             zig-0.16.md>; the pack is drift-checked at run start (stamped
#             digest sha + installed zig version) and aborts on mismatch.
#
# PRE-REGISTERED READOUTS (scratch/tier3_verdict.py):
#   R1 primary  : pooled-replicate paired McNemar P1 vs P0 on zig units
#                 (pairs = (P0a,P1a),(P0b,P1b); b = rescues, c = regressions).
#   R2 co-primary: iterations-to-fix and completion-tokens-to-fix, paired on
#                 units passed in BOTH arms of a pair (thrash reduction is
#                 part of a genuine pack's effect — GitChameleon).
#   R3 guard    : champion C1 vs C0 McNemar — a feature that degrades the #1
#                 model users run does not ship (clean = p>0.05 or net>=0).
#   R4 audit    : every P1-only pass listed for pack-parroting review.
#   SHIP RULE (Clause 1): net rescues >= 7 AND p < 0.05 AND guard clean.
#   VALIDITY (added 2026-09-29, after the 09-17 run): the verdict drops rows
#   the server died under, EAGAIN validation deaths and harness deaths from
#   their pair — scratch/tier3-verdict-reaudit-2026-09-29.txt. P0a replays
#   the cache, so a rerun re-measures whatever the quarantine moved out.
#   Regardless of outcome: STOP after this arm (Clause 2 — nothing further is
#   provable on this suite).
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

MODEL="${MODEL:-qwen38-27b-uncensored-q5}"          # default 3.8 uncensored Q5 (non-MTP)
CHAMPION="${CHAMPION:-qwen-27b-q5}"                 # Qwen 27B Q5_K_XL (evals/benchmark_all.py MODELS)
JOBS="${JOBS:-4}"
EXTRA_UNITS="${EXTRA_UNITS:-}"                      # e.g. 158_karatsuba_bytes_f
SKIP_C0="${SKIP_C0:-0}"
# FRESH=1: every cell --no-cache (P0a included). The 09-17 P0a replayed the
# 09-15 rows — 7 of them banked while llama-server was dead — so pair 0 was
# neither same-day nor clean. Use FRESH=1 for the post-fix rerun.
FRESH="${FRESH:-0}"
STAMP="$(date +%Y%m%d-%H%M%S)"
MANIFEST="${MANIFEST:-$REPO/scratch/tier3_cells-$STAMP.txt}"
# CHUNKED RUNS (2026-09-29): the GPU comes back in 1-2 h windows, and a cell
# is ~70-95 min. MANIFEST=<an existing manifest> RESUMES it: cells already
# recorded there are skipped, and the run is refused unless the pack, model,
# champion, jobs, units, fresh flag and eval ERA all match its header (a
# verdict never pairs across eras). Touch $STOP_FILE to stop at the next CELL
# BOUNDARY — never mid-cell; the file is consumed and the run exits 0.
# DRY_RUN=1 walks the cell logic without touching the GPU (for tests).
STOP_FILE="${STOP_FILE:-$REPO/.run/tier3.stop}"
DRY_RUN="${DRY_RUN:-0}"

export OPENBEAST_REASONING_BUDGET=20480
export BEAST_ASSIST=0 OPENBEAST_DIAGNOSTICS=0       # packs-only isolation (see DESIGN)
unset BEAST_PACKS OPENBEAST_PACKS                    # each cell sets its own arm explicitly

# --- pack integrity before spending GPU --------------------------------------
python3 agents/packs/gen_zig_pack.py --check
PACK_SHA8="$(sha256sum agents/packs/zig-0.16.md | cut -c1-8)"

# --- derive the zig unit list from the pin + task specs ----------------------
ZIG_UNITS="$(python3 - <<'EOF'
import json, sys
sys.path.insert(0, "evals")
import run_eval
pin = json.load(open("evals/suites/v5-fast.json"))
units = run_eval.load_tasks(list(pin["units"]))
zig = [t["id"] for t in units if t.get("language") == "zig"]
assert len(zig) == 30, f"expected 30 zig units in v5-fast, got {len(zig)}"
print(",".join(zig))
EOF
)"
if [ -n "$EXTRA_UNITS" ]; then ZIG_UNITS="$ZIG_UNITS,$EXTRA_UNITS"; fi
N_UNITS="$(echo "$ZIG_UNITS" | tr ',' '\n' | wc -l)"

ERA="$(python3 -c 'import sys; sys.path.insert(0, "evals"); import cache; print(cache.context_hash())')"
HDR2="# pack sha8=$PACK_SHA8 model=$MODEL champion=$CHAMPION jobs=$JOBS units=$N_UNITS fresh=$FRESH era=$ERA"
HDR3="# units=$ZIG_UNITS"
if [ -s "$MANIFEST" ]; then
  # Resume: the header must describe exactly this run.
  if [ "$(sed -n 2p "$MANIFEST")" != "$HDR2" ] || [ "$(sed -n 3p "$MANIFEST")" != "$HDR3" ]; then
    echo "RESUME REFUSED: $MANIFEST was made for a different run:" >&2
    echo "  it says:  $(sed -n 2p "$MANIFEST")" >&2
    echo "  this is:  $HDR2" >&2
    echo "  (or the unit list differs). Start a new manifest instead." >&2
    exit 2
  fi
  echo "Resuming $MANIFEST — done: $(grep -oE '^(P0a|P1a|P0b|P1b|C0|C1) ' "$MANIFEST" | tr -d ' ' | tr '\n' ' ')"
else
  mkdir -p "$(dirname "$MANIFEST")"
  { echo "# tier3 zig mini-A/B cells — $STAMP"; echo "$HDR2"; echo "$HDR3"; } | tee "$MANIFEST"
fi

newest_result() { ls -t evals/results/eval-*.json | head -1; }

run_cell() {
  # run_cell <cell> <model-slug> <packs:0|1> [extra benchmark_all args...]
  local cell="$1" slug="$2" packs="$3"; shift 3
  if grep -q "^$cell " "$MANIFEST"; then echo "  skip $cell: already in the manifest"; return 0; fi
  if [ -e "$STOP_FILE" ]; then
    rm -f "$STOP_FILE"
    echo; echo "STOPPED at the cell boundary before $cell (stop file). Resume with:"
    echo "  MANIFEST=$MANIFEST FRESH=$FRESH bash scratch/tier3_zig_ab.sh"
    exit 0
  fi
  if [ "$DRY_RUN" = "1" ]; then echo "$cell DRYRUN-$cell" | tee -a "$MANIFEST"; return 0; fi
  local before; before="$(newest_result || true)"
  echo; echo "########## CELL $cell — $slug — packs=$packs $*"; echo
  if [ "$packs" = "1" ]; then
    BEAST_PACKS=1 python3 evals/benchmark_all.py --models "$slug" --tasks "$ZIG_UNITS" \
      --greedy --packs --jobs "$JOBS" --no-leaderboard "$@"
  else
    BEAST_PACKS=0 python3 evals/benchmark_all.py --models "$slug" --tasks "$ZIG_UNITS" \
      --greedy --jobs "$JOBS" --no-leaderboard "$@"
  fi
  local after; after="$(newest_result)"
  if [ "$after" = "$before" ]; then echo "cell $cell produced no results file" >&2; exit 1; fi
  # provenance sanity: the file must say what the cell says
  python3 - "$after" "$packs" "$PACK_SHA8" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1])); h = r["harness"]
want = {"zig": sys.argv[3]} if sys.argv[2] == "1" else {}
assert h.get("packs", {}) == want, f"harness.packs={h.get('packs')} want {want}"
assert h.get("greedy") is True, "cell did not run greedy"
assert h.get("diagnostics") is False, "diag leaked into a packs cell"
print(f"  ok: {sys.argv[1]} packs={h.get('packs')} greedy={h['greedy']} passed={r['summary']['passed']}/{r['summary']['total']}")
EOF
  echo "$cell $after" | tee -a "$MANIFEST"
}

# Order: interleave arms so a slow drift of the box (thermal, background
# load) cannot line up with one arm.
if [ "$FRESH" = "1" ]; then A_ARGS=(--no-cache); else A_ARGS=(); fi
run_cell P0a "$MODEL" 0 ${A_ARGS[@]+"${A_ARGS[@]}"}
run_cell P1a "$MODEL" 1 ${A_ARGS[@]+"${A_ARGS[@]}"}
run_cell P0b "$MODEL" 0 --no-cache
run_cell P1b "$MODEL" 1 --no-cache
if [ "$SKIP_C0" != "1" ]; then run_cell C0 "$CHAMPION" 0 ${A_ARGS[@]+"${A_ARGS[@]}"}; fi
run_cell C1 "$CHAMPION" 1 ${A_ARGS[@]+"${A_ARGS[@]}"}

echo; echo "All cells done. Manifest: $MANIFEST"; echo
[ "$DRY_RUN" = "1" ] && exit 0
python3 scratch/tier3_verdict.py --manifest "$MANIFEST"
