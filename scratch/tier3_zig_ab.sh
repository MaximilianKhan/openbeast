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
# BOUNDARY — never mid-cell; the file is consumed and the run exits 0. A stop
# file that is already there when a run STARTS is stale (touched during an
# earlier run's last cell): it is removed with a notice, never obeyed, and a
# run that completes consumes any stop file too.
# DRY_RUN=1 walks the cell logic without touching the GPU (for tests). It never
# writes $MANIFEST: it works on a throwaway copy and leaves the stop file alone.
# HARNESS HYGIENE (2026-09-30 double pass): one run per manifest (flock on
# $MANIFEST.lock); the era and the engine are re-checked before EVERY cell and
# a drift refuses (a verdict must not pair a cell across a git pull or a
# llama.cpp rebuild); the header records the engine, the weight pins and
# SKIP_C0 ("# runtime" line), and each results file must name this cell's
# model and exactly these units.
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

era_now() { python3 -c 'import sys; sys.path.insert(0, "evals"); import cache; print(cache.context_hash())'; }
# The llama-server binary's own build/commit (what produced the rows), the
# same identity run_eval stamps into every results file.
engine_now() {
  python3 -c 'import sys; sys.path.insert(0, "evals"); import run_eval
i = run_eval.capture_inference_engine_info()
print(i.get("build", "?") + "/" + i.get("commit", i.get("version_raw", "?")).replace(" ", "_"))'
}
# "<weights file>@<sha8 of its registry pin>" for a benchmark_all slug.
weights_pin() {
  python3 - "$1" <<'EOF'
import re, sys
slug = sys.argv[1]
m = re.search(r'"slug":\s*"%s",.*?"serve":\s*"([^"]+)"' % re.escape(slug),
              open("evals/benchmark_all.py").read(), re.S)
if not m:
    print("unknown"); sys.exit(0)
try:
    w = re.search(r'\$WEIGHTS_DIR/([A-Za-z0-9._-]+\.gguf)', open(m.group(1)).read())
except OSError:
    w = None
if not w:
    print("unknown"); sys.exit(0)
pin = "unpinned"
for ln in open("scripts/weights.registry"):
    f = ln.rstrip("\n").split("\t")
    if len(f) > 2 and f[2] == w.group(1):
        pin = f[0][:8]
print(f"{w.group(1)}@{pin}")
EOF
}

ERA="$(era_now)"
ENGINE="$(engine_now)"
HDR2="# pack sha8=$PACK_SHA8 model=$MODEL champion=$CHAMPION jobs=$JOBS units=$N_UNITS fresh=$FRESH era=$ERA"
HDR3="# units=$ZIG_UNITS"
HDR4="# runtime engine=$ENGINE weights=$(weights_pin "$MODEL"),$(weights_pin "$CHAMPION") skip_c0=$SKIP_C0"

# One run per manifest. Two resumes of one manifest ran every cell twice and
# could each record the other's results file.
if [ "$DRY_RUN" != "1" ]; then
  mkdir -p "$(dirname "$MANIFEST")"
  exec 9>"$MANIFEST.lock"
  if ! flock -n 9; then
    echo "REFUSED: another tier3 run holds $MANIFEST.lock — one run per manifest." >&2
    exit 2
  fi
fi

# DRY_RUN never writes the real manifest: it walks a throwaway copy. (A dry
# run once appended DRYRUN-<cell> rows to a real manifest; the next real
# resume skipped those cells and the verdict died on a missing file.)
REAL_MANIFEST="$MANIFEST"
if [ "$DRY_RUN" = "1" ]; then
  MANIFEST="$(mktemp "${TMPDIR:-/tmp}/tier3-dryrun.XXXXXX")"
  if [ -s "$REAL_MANIFEST" ]; then cp "$REAL_MANIFEST" "$MANIFEST"; fi
  echo "DRY RUN: working on a copy ($MANIFEST); $REAL_MANIFEST is not modified."
fi

# The cells this manifest really ran (a legacy DRYRUN-* row is not a cell).
done_cells() { awk '$1 ~ /^(P0a|P1a|P0b|P1b|C0|C1)$/ && $2 !~ /^DRYRUN-/ {print $1}' "$MANIFEST"; }

if [ -s "$MANIFEST" ]; then
  # Resume: the header must describe exactly this run.
  if [ "$(sed -n 2p "$MANIFEST")" != "$HDR2" ] || [ "$(sed -n 3p "$MANIFEST")" != "$HDR3" ]; then
    echo "RESUME REFUSED: $REAL_MANIFEST was made for a different run:" >&2
    echo "  it says:  $(sed -n 2p "$MANIFEST")" >&2
    echo "  this is:  $HDR2" >&2
    echo "  (or the unit list differs). Start a new manifest instead." >&2
    exit 2
  fi
  old4="$(grep -m1 '^# runtime ' "$MANIFEST" || true)"
  if [ -z "$old4" ]; then
    echo "WARNING: $REAL_MANIFEST predates the '# runtime' header — its engine, weights" >&2
    echo "  and SKIP_C0 were never recorded, so this resume cannot check them." >&2
  elif [ "$old4" != "$HDR4" ]; then
    echo "RESUME REFUSED: the engine, weights or SKIP_C0 differ from $REAL_MANIFEST:" >&2
    echo "  it says:  $old4" >&2
    echo "  this is:  $HDR4" >&2
    exit 2
  fi
  if grep -qE '^(P0a|P1a|P0b|P1b|C0|C1) DRYRUN-' "$MANIFEST"; then
    echo "WARNING: $REAL_MANIFEST holds DRYRUN-* rows from an old dry run; they are not cells and will run." >&2
  fi
  echo "Resuming $REAL_MANIFEST — done: $(done_cells | tr '\n' ' ')"
else
  mkdir -p "$(dirname "$MANIFEST")"
  { echo "# tier3 zig mini-A/B cells — $STAMP"; echo "$HDR2"; echo "$HDR3"; echo "$HDR4"; } | tee "$MANIFEST"
fi

# A stop file present at START is stale: nobody can have meant to stop a run
# that had not begun. Obeying it made the next campaign exit 0 having run
# nothing, and a wrapper read that as success.
if [ -e "$STOP_FILE" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    echo "(dry run) a stale stop file exists ($STOP_FILE); a real run would remove it."
  else
    echo "NOTICE: removing a stale stop file from an earlier run ($STOP_FILE)."
    echo "  Touch it again to stop THIS run at a cell boundary."
    rm -f "$STOP_FILE"
  fi
fi

newest_result() { ls -t evals/results/eval-*.json 2>/dev/null | head -1 || true; }

run_cell() {
  # run_cell <cell> <model-slug> <packs:0|1> [extra benchmark_all args...]
  local cell="$1" slug="$2" packs="$3"; shift 3
  if done_cells | grep -qx "$cell"; then echo "  skip $cell: already in the manifest"; return 0; fi
  if [ "$DRY_RUN" != "1" ] && [ -e "$STOP_FILE" ]; then
    rm -f "$STOP_FILE"
    echo; echo "STOPPED at the cell boundary before $cell (stop file). Resume with:"
    echo "  MANIFEST=$REAL_MANIFEST FRESH=$FRESH bash scratch/tier3_zig_ab.sh"
    exit 0
  fi
  # A git pull or a llama.cpp rebuild between cells would pair this cell with
  # the others across eras; the header only saw the state at chunk start.
  local era engine
  era="$(era_now)"; engine="$(engine_now)"
  if [ "$era" != "$ERA" ] || [ "$engine" != "$ENGINE" ]; then
    echo "REFUSED before $cell: the run drifted since it started" >&2
    echo "  era    $ERA -> $era" >&2
    echo "  engine $ENGINE -> $engine" >&2
    echo "  A verdict never pairs cells across eras. Start a new manifest." >&2
    exit 2
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
  python3 - "$after" "$packs" "$PACK_SHA8" "$slug" "$ZIG_UNITS" <<'EOF'
import json, re, sys
r = json.load(open(sys.argv[1])); h = r["harness"]
slug, units = sys.argv[4], sys.argv[5].split(",")
m = re.search(r'"slug":\s*"%s",\s*"name":\s*"([^"]+)"' % re.escape(slug),
              open("evals/benchmark_all.py").read())
assert m, f"{slug} is not in benchmark_all MODELS"
assert r.get("model") == m.group(1), \
    f"results model={r.get('model')!r}, cell model={m.group(1)!r}: not this cell's file"
got = sorted(t["id"] for t in r.get("tasks", []))
assert got == sorted(units), f"results units differ from this run's {len(units)} (got {len(got)})"
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

echo; echo "All cells done. Manifest: $REAL_MANIFEST"; echo
if [ "$DRY_RUN" = "1" ]; then rm -f "$MANIFEST"; exit 0; fi
# A stop requested during the last cell has nothing left to stop.
if [ -e "$STOP_FILE" ]; then rm -f "$STOP_FILE"; echo "(consumed a stop file touched during the last cell)"; fi
python3 scratch/tier3_verdict.py --manifest "$MANIFEST"
