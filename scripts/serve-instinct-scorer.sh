#!/bin/bash
# beast-instinct CPU scorer: Qwen3-0.6B-Q8_0 on llama-server, 127.0.0.1:8082.
#
# P1 (plan §5.14 R1). Ships in P0, UNUSED until INSTINCT_SCORER=true is wired
# into start.sh. CPU only — CUDA_VISIBLE_DEVICES is emptied and -ngl 0, so it
# takes no VRAM from the primary. The weight is sha-pinned in
# scripts/weights.registry (9465e63a…, 639,446,688 bytes); serve.sh checks it.
#
#   -c 8192 -np 2   serve.sh adds --kv-unified, so BOTH slots see the full
#                   8192 tokens from ONE pool — never divide by -np.
#   -t              physical cores / 2 by default (INSTINCT_SCORER_THREADS);
#                   [HW] the ROUTER_SIDECAR_PLAN placement matrix decides.
#   key             .run/instinct-scorer.key (0600, minted here) reaches
#                   llama-server through its ENVIRONMENT (serve.sh exports
#                   LLAMA_API_KEY) — never argv.
#
# Refusals (fail closed):
#   * :8082 already held (pre-bind check — a squatter would BE the classifier);
#   * the scorer URL equals INFERENCE_URL (it would contend for the primary's
#     slot; the plan's launcher refuses that without --allow-primary, and this
#     one has no such flag — use a different port);
#   * a non-loopback host.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/weights.sh
source "$SCRIPT_DIR/lib/weights.sh"
# shellcheck source=lib/portown.sh
source "$SCRIPT_DIR/lib/portown.sh"

HOST="127.0.0.1"
PORT="${INSTINCT_SCORER_PORT:-8082}"
RUN_DIR="${INSTINCT_RUN_DIR:-$REPO_DIR/.run}"
KEY_FILE="${INSTINCT_SCORER_KEY_FILE:-$RUN_DIR/instinct-scorer.key}"
MODEL="${INSTINCT_SCORER_MODEL:-$WEIGHTS_DIR/Qwen3-0.6B-Q8_0.gguf}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  sed -n '2,24p' "$0"
  exit 0
fi

phys_cores() {
  local n
  n="$(lscpu -p=CORE 2>/dev/null | grep -v '^#' | sort -u | wc -l || true)"
  [[ "$n" =~ ^[0-9]+$ && "$n" -gt 0 ]] || n="$(nproc 2>/dev/null || echo 2)"
  echo "$n"
}
THREADS="${INSTINCT_SCORER_THREADS:-$(( $(phys_cores) / 2 ))}"
(( THREADS >= 1 )) || THREADS=1

# The scorer must never be the primary.
infer="${INFERENCE_URL:-${OPENBEAST_INFERENCE_URL:-http://127.0.0.1:8080}}"
infer_port="$(printf '%s' "$infer" | sed -nE 's#^https?://[^/:]+:([0-9]+).*#\1#p')"
infer_host="$(printf '%s' "$infer" | sed -nE 's#^https?://([^/:]+).*#\1#p')"
case "$infer_host" in localhost|127.0.0.1|::1|0.0.0.0) infer_host="127.0.0.1" ;; esac
if [[ "$infer_host" == "127.0.0.1" && "${infer_port:-}" == "$PORT" ]]; then
  echo "Error: scorer port $PORT is the primary INFERENCE_URL ($infer) — refusing" >&2
  exit 2
fi
if ob_port_listening "$PORT"; then
  echo "Error: port $PORT is already held — refusing (pre-bind check)" >&2
  exit 2
fi
[[ -f "$MODEL" ]] || { echo "Error: $MODEL not found — scripts/fetch-weight.sh Qwen3-0.6B-Q8_0.gguf" >&2; exit 1; }

mkdir -p "$RUN_DIR"
if [[ ! -f "$KEY_FILE" ]]; then
  ( umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$KEY_FILE" )
fi
[[ "$(stat -c '%a' "$KEY_FILE")" == "600" ]] || { echo "Error: $KEY_FILE must be 0600" >&2; exit 2; }

# serve.sh sources conf.sh, which takes LLAMA_API_KEY from OPENBEAST_API_KEY
# first — so the scorer's own key wins over the stack's, via the environment.
OPENBEAST_API_KEY="$(cat "$KEY_FILE")"
export OPENBEAST_API_KEY
export CUDA_VISIBLE_DEVICES=""
export OPENBEAST_AUTO_CONTEXT=0        # no VRAM-based scaling: this is a CPU server
export OPENBEAST_CONTEXT=8192
# Global reasoning overrides are for the primary; the scorer reads the answer
# boundary through /completion and never thinks.
unset REASONING REASONING_BUDGET

exec "$SCRIPT_DIR/serve.sh" \
  -m "$MODEL" \
  -c 8192 \
  -np 2 \
  -ngl 0 \
  -ctk f16 \
  --host "$HOST" \
  -p "$PORT" \
  -t "$THREADS" \
  "$@"
