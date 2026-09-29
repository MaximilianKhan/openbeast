#!/bin/bash
# Langaware: measure the churn floor ON GREEDY MODE (PR #56 low-churn eval
# era) — two untreated, same-day, LIVE v5-fast rows of the default model
# (uncensored 3.8 Q5_K_M, capped 20480). Flips between them = the floor.
#
# Both runs are --no-cache (2026-09-29 review, research-stats-2). Run 1 used
# to replay the cache, so on 09-17 its zig rows were the 09-15 rows — the
# same rows the Tier-3 cell P0a replayed — and the "same-day floor" was
# neither same-day nor independent of the verdict it was meant to calibrate.
#
# Two regimes, and they are NOT the same floor:
#   default        benchmark_all --jobs 4 against the serve script's
#                  -np 6 --kv-unified server — the regime the Tier-3 cells
#                  ran in. NOT single-slot: batch composition moves greedy
#                  logits, and the Tier-3 replicates measured ~30% zig churn
#                  in exactly this regime (P0a/P0b 9 of 30, P1a/P1b 8 of 30).
#                  "Greedy = near-zero churn" does not hold here.
#   --single-slot  the low-churn mode LANG_AWARENESS_PLAN §7 actually names:
#                  a -np 1 server started here and run_eval --jobs 1.
#                  REFUSES (exit 3) if anything already serves :$PORT —
#                  stop the stack first — and aborts unless /props of our
#                  own live pid reports total_slots == 1.
#                  Budget ~9-11 h PER ROW (the 09-14 IQ3 -np 1 rows took
#                  8.8 h and 11.0 h), ~20 h for the pair.
# The zig half of the default regime is already measured by the Tier-3
# replicates; run --single-slot if the question is "how low can churn go".
#
# GPU job — Max-triggered only; it starts/stops llama-server.
set -uo pipefail
OB="${OB:-$(cd "$(dirname "$0")/.." && pwd)}"; cd "$OB" || exit 1
MODE=default
case "${1:-}" in
  --single-slot) MODE=single-slot ;;
  "") ;;
  *) echo "usage: $0 [--single-slot]" >&2; exit 2 ;;
esac
# Durable logs: /tmp lost both capability rows' serve logs to the 09-15
# power-off, which made a 19-hour measurement unauditable after the fact.
L=$OB/scratch/logs/campaign; mkdir -p "$L"
source scripts/conf.sh 2>/dev/null || true
# openbeast.conf carries BEAST_ASSIST=1 (Max, 2026-09-10) — this is an UNTREATED measurement: never let it leak into the harness.
unset BEAST_ASSIST OPENBEAST_DIAGNOSTICS
export OPENBEAST_REASONING_BUDGET=20480
PORT="${FLOOR_PORT:-8080}"

if [ "$MODE" = default ]; then
  for n in 1 2; do
    echo "[$(date +%F' '%T)] greedy floor ($MODE: --jobs 4 vs -np 6) run $n (--no-cache)"
    python3 -u evals/benchmark_all.py --models qwen38-27b-uncensored-q5 --suite v5-fast \
      --greedy --jobs 4 --no-leaderboard --no-cache > "$L/greedy-floor-$n.log" 2>&1
    echo "[$(date +%F' '%T)] run $n rc=$?"
  done
  pkill -f '[l]lama-server' 2>/dev/null || true
  exit 0
fi

# --single-slot: our own -np 1 server, one unit at a time. The serve script
# execs serve.sh which execs llama-server, so $! is the server itself and it
# is stopped by that pid — never by a name pattern.
#
# The server answering :$PORT must be THAT pid, at -np 1 (2026-09-29 review
# of research-stats-2). The first cut polled /health before checking its own
# pid, so with the stack up (the default MTP -np 6 server, WebUI users on it)
# curl succeeded at once, our -np 1 server died on bind/VRAM, and both
# "single-slot" rows ran against the wrong server in the wrong regime. We do
# NOT stop someone else's server — refuse and say so.
AUTH=()
[ -n "${LLAMA_API_KEY:-}" ] && AUTH=(-H "Authorization: Bearer $LLAMA_API_KEY")
if curl -sf --max-time 3 "http://localhost:$PORT/health" >/dev/null 2>&1; then
  echo "[$(date +%F' '%T)] REFUSED: something already serves :$PORT — stop the stack" \
       "(./stop.sh) first; a single-slot floor must not run against it" >&2
  exit 3
fi
slog="$L/greedy-floor-single-serve.log"
echo "[$(date +%F' '%T)] greedy floor ($MODE: -np 1, --jobs 1) starting server on :$PORT, log $slog"
nohup scripts/serve-qwen38-27b-uncensored-q5.sh -np 1 -p "$PORT" > "$slog" 2>&1 &
SPID=$!
up=0
for _ in $(seq 1 150); do
  kill -0 "$SPID" 2>/dev/null || break          # ours died: nothing we poll is ours
  if curl -sf --max-time 3 "http://localhost:$PORT/health" >/dev/null 2>&1; then up=1; break; fi
  sleep 2
done
if [ "$up" = 1 ] && ! kill -0 "$SPID" 2>/dev/null; then up=0; fi
slots=""
if [ "$up" = 1 ]; then
  slots=$(curl -sf --max-time 3 "${AUTH[@]}" "http://localhost:$PORT/props" 2>/dev/null \
          | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total_slots",""))' 2>/dev/null)
fi
if [ "$up" != 1 ] || [ "$slots" != 1 ]; then
  echo "[$(date +%F' '%T)] SERVER FAILED (up=$up, total_slots='${slots}', want 1) — see $slog" >&2
  kill "$SPID" 2>/dev/null || true; wait "$SPID" 2>/dev/null || true; exit 1
fi
export OPENBEAST_EVAL_GREEDY=1
for n in 1 2; do
  if ! kill -0 "$SPID" 2>/dev/null; then
    echo "[$(date +%F' '%T)] SERVER DIED before run $n — see $slog; check run $((n - 1)) with row_validity.py" >&2
    exit 1
  fi
  echo "[$(date +%F' '%T)] greedy floor ($MODE) run $n (--no-cache)"
  python3 -u evals/run_eval.py --suite v5-fast --jobs 1 --no-cache \
    --base-url "http://localhost:$PORT/v1" > "$L/greedy-floor-single-$n.log" 2>&1
  echo "[$(date +%F' '%T)] run $n rc=$?"
done
kill "$SPID" 2>/dev/null || true
wait "$SPID" 2>/dev/null || true
