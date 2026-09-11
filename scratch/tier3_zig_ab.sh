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
#   decode  : --greedy (low-churn eval mode, own cache era) — churn floor ~0 on
#             zig for 3.8, so even the low end of the expected effect is
#             detectable.
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
STAMP="$(date +%Y%m%d-%H%M%S)"
MANIFEST="${MANIFEST:-$REPO/scratch/tier3_cells-$STAMP.txt}"

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

{
  echo "# tier3 zig mini-A/B cells — $STAMP"
  echo "# pack sha8=$PACK_SHA8 model=$MODEL champion=$CHAMPION jobs=$JOBS units=$N_UNITS"
  echo "# units=$ZIG_UNITS"
} | tee "$MANIFEST"

newest_result() { ls -t evals/results/eval-*.json | head -1; }

run_cell() {
  # run_cell <cell> <model-slug> <packs:0|1> [extra benchmark_all args...]
  local cell="$1" slug="$2" packs="$3"; shift 3
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
run_cell P0a "$MODEL" 0
run_cell P1a "$MODEL" 1
run_cell P0b "$MODEL" 0 --no-cache
run_cell P1b "$MODEL" 1 --no-cache
if [ "$SKIP_C0" != "1" ]; then run_cell C0 "$CHAMPION" 0; fi
run_cell C1 "$CHAMPION" 1

echo; echo "All cells done. Manifest: $MANIFEST"; echo
python3 scratch/tier3_verdict.py --manifest "$MANIFEST"
