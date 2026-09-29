#!/bin/bash
# Launch ONE rank of a two-DGX-Spark vLLM server (tensor parallel 2, native
# multi-node — no Ray). Runs ON A SPARK, not on the rig. docs/DGX_SPARK_PLAN.md
#
#   scripts/backends/vllm/spark-node.sh --rank 1 [--head-addr IP]   # on Spark 2
#   scripts/backends/vllm/spark-node.sh --rank 0 [--head-addr IP]   # on Spark 1
#
# Options:
#   --rank 0|1        required. 0 = head: serves HTTP on SPARK_SERVE_HOST:PORT.
#                     1 = worker: --headless, no HTTP.
#   --head-addr IP    rank 0's address on the ConnectX link (SPARK_HEAD_IP)
#   --model ID        Hugging Face id or local dir (MODEL)
#   --env FILE        settings file (default: scripts/backends/spark.env;
#                     template: spark.env.example next to it)
#   --print           print the exact docker command and exit; runs nothing
#   --stop            docker stop this rank's container
#
# Recipe: vLLM's Qwen3.6-27B-on-DGX-Spark recipe (recipes.vllm.ai) and the
# multi-node docs (docs.vllm.ai serving/parallelism_scaling). The NVIDIA
# playbook uses Ray instead; native multiprocessing needs one container per
# node and nothing else. Every rank must use the same engine settings.
#
# The API key never touches argv: vLLM reads VLLM_API_KEY from its
# environment (vllm/envs.py; middleware/register.py prefers --api-key, which
# we never pass), docker forwards `-e VLLM_API_KEY` from THIS shell's
# environment, and this shell reads it from a 0600 file.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"

RANK="" HEAD_ADDR="" MODEL_ARG="" ENV_FILE="" PRINT=0 STOP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rank)       RANK="${2:-}"; shift 2 ;;
    --head-addr)  HEAD_ADDR="${2:-}"; shift 2 ;;
    --model)      MODEL_ARG="${2:-}"; shift 2 ;;
    --env)        ENV_FILE="${2:-}"; shift 2 ;;
    --print)      PRINT=1; shift ;;
    --stop)       STOP=1; shift ;;
    -h|--help)    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "Unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
case "$RANK" in
  0|1) ;;
  *) echo "Error: --rank 0|1 is required (0 = head, serves HTTP; 1 = worker)" >&2; exit 2 ;;
esac
NAME="openbeast-vllm-rank$RANK"
if [[ $STOP -eq 1 ]]; then
  exec docker stop "$NAME"
fi

ENV_FILE="${ENV_FILE:-${SPARK_ENV:-$HERE/../spark.env}}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Note: no settings file at $ENV_FILE — using the environment only (template: $HERE/../spark.env.example)." >&2
fi
sp_load "$ENV_FILE" VLLM_IMAGE VLLM_IMAGE_DIGEST SPARK_IFACE SPARK_IB_HCA \
  SPARK_HEAD_IP SPARK_NODE_IP VLLM_MASTER_PORT SPARK_SERVE_HOST SPARK_SERVE_PORT \
  SPARK_ALLOW_WILDCARD_BIND HF_CACHE MODEL SERVED_MODEL_NAME MAX_MODEL_LEN \
  GPU_MEMORY_UTILIZATION TENSOR_PARALLEL_SIZE MAX_NUM_SEQS REASONING_PARSER \
  TOOL_CALL_PARSER SPECULATIVE_CONFIG VLLM_EXTRA_ARGS VLLM_API_KEY_FILE SPARK_NO_API_KEY
[[ -n "$HEAD_ADDR" ]] && SPARK_HEAD_IP="$HEAD_ADDR"
[[ -n "$MODEL_ARG" ]] && MODEL="$MODEL_ARG"
VLLM_MASTER_PORT="${VLLM_MASTER_PORT:-29501}"
SPARK_SERVE_PORT="${SPARK_SERVE_PORT:-8000}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-2}"
REASONING_PARSER="${REASONING_PARSER:-qwen3}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-qwen3_xml}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"

sp_need VLLM_IMAGE "the vLLM container image (spark.env)"
sp_need MODEL "the model to serve (--model or spark.env MODEL)"
sp_need SPARK_IFACE "the ConnectX-7 interface ibdev2netdev shows Up"
sp_need SPARK_IB_HCA "the ConnectX-7 HCA ibdev2netdev shows Up"
sp_need SPARK_HEAD_IP "rank 0's address on the ConnectX link (--head-addr)"
sp_need MAX_MODEL_LEN "the context window; both ranks must agree"
sp_need GPU_MEMORY_UTILIZATION "the unified-memory fraction; both ranks must agree"
[[ "$TENSOR_PARALLEL_SIZE" == "2" ]] \
  || sp_err "TENSOR_PARALLEL_SIZE=$TENSOR_PARALLEL_SIZE — this scaffold is the two-Spark TP=2 layout"
[[ "$VLLM_MASTER_PORT" =~ ^[0-9]+$ ]] || sp_err "VLLM_MASTER_PORT='$VLLM_MASTER_PORT' is not a port"
[[ "$SPARK_SERVE_PORT" =~ ^[0-9]+$ ]] || sp_err "SPARK_SERVE_PORT='$SPARK_SERVE_PORT' is not a port"
USE_KEY=0
if [[ "$RANK" == 0 ]]; then
  sp_check_bind "${SPARK_SERVE_HOST:-}"
  sp_need SERVED_MODEL_NAME "the model id the rig's WebUI lists"
  if sp_is_true "${SPARK_NO_API_KEY:-}"; then
    echo "Warning: SPARK_NO_API_KEY=true — vLLM will serve /v1 WITHOUT a key." >&2
  else
    sp_need VLLM_API_KEY_FILE "a 0600 file holding the API key (or SPARK_NO_API_KEY=true)"
    [[ -n "${VLLM_API_KEY_FILE:-}" ]] && sp_check_secret_file "$VLLM_API_KEY_FILE"
    USE_KEY=1
  fi
fi
sp_image "${VLLM_IMAGE:-}" "${VLLM_IMAGE_DIGEST:-}" "$PRINT"
if [[ $SP_ERRORS -gt 0 ]]; then
  echo "Refusing to start: $SP_ERRORS setting(s) missing or unsafe (settings: $ENV_FILE)." >&2
  exit 1
fi

IF="$SPARK_IFACE"
CMD=(docker run -d --rm --name "$NAME"
  --gpus all --ipc=host --network host
  --ulimit memlock=-1 --ulimit nofile=1048576:1048576 --cap-add IPC_LOCK
  --device /dev/infiniband
  -e "NCCL_SOCKET_IFNAME=$IF" -e "GLOO_SOCKET_IFNAME=$IF" -e "TP_SOCKET_IFNAME=$IF"
  -e "UCX_NET_DEVICES=$IF" -e "OMPI_MCA_btl_tcp_if_include=$IF"
  -e "NCCL_IB_HCA=$SPARK_IB_HCA" -e NCCL_IB_DISABLE=0 -e NCCL_IGNORE_CPU_AFFINITY=1
  # llama-server ignores the request's model id and the whole stack relies
  # on that (opencode.json, setup-client.sh); vLLM 404s unknown ids unless
  # told not to (vllm/entrypoints/serve/engine/serving.py).
  -e VLLM_SKIP_MODEL_NAME_VALIDATION=1)
[[ -n "${SPARK_NODE_IP:-}" ]] && CMD+=(-e "VLLM_HOST_IP=$SPARK_NODE_IP")
# Pass-through by NAME only (docker copies the value from this shell's env),
# so a token or a debug level never lands on argv.
[[ -n "${NCCL_DEBUG:-}" ]] && CMD+=(-e NCCL_DEBUG)
[[ -n "${HF_TOKEN:-}" ]] && CMD+=(-e HF_TOKEN)
[[ $USE_KEY -eq 1 ]] && CMD+=(-e VLLM_API_KEY)
CMD+=(-v "$HF_CACHE:/root/.cache/huggingface"
  --entrypoint vllm "$SP_IMAGE_REF"
  serve "$MODEL"
  --tensor-parallel-size 2 --nnodes 2 --node-rank "$RANK"
  --master-addr "$SPARK_HEAD_IP" --master-port "$VLLM_MASTER_PORT"
  --max-model-len "$MAX_MODEL_LEN" --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION")
# No --trust-remote-code: it executes Python shipped in the model repo. The
# recipe passes it; add it to VLLM_EXTRA_ARGS only for a repo you have read.
[[ -n "${MAX_NUM_SEQS:-}" ]] && CMD+=(--max-num-seqs "$MAX_NUM_SEQS")
[[ -n "${SPECULATIVE_CONFIG:-}" ]] && CMD+=(--speculative-config "$SPECULATIVE_CONFIG")
if [[ -n "${VLLM_EXTRA_ARGS:-}" ]]; then
  read -r -a _extra <<< "$VLLM_EXTRA_ARGS"
  CMD+=("${_extra[@]}")
fi
if [[ "$RANK" == 0 ]]; then
  CMD+=(--host "$SPARK_SERVE_HOST" --port "$SPARK_SERVE_PORT"
    --served-model-name "$SERVED_MODEL_NAME"
    --reasoning-parser "$REASONING_PARSER"
    --enable-auto-tool-choice --tool-call-parser "$TOOL_CALL_PARSER")
else
  CMD+=(--headless)
fi

if [[ $PRINT -eq 1 ]]; then
  [[ $USE_KEY -eq 1 ]] && echo "# VLLM_API_KEY is exported from $VLLM_API_KEY_FILE into docker's environment (not argv)"
  sp_print_cmd "${CMD[@]}"
  exit 0
fi

if [[ "$RANK" == 0 ]]; then
  echo "Rank 0 (head): serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT once rank 1 has joined."
else
  echo "Rank 1 (worker, headless): joining $SPARK_HEAD_IP:$VLLM_MASTER_PORT."
fi
if [[ $USE_KEY -eq 1 ]]; then
  VLLM_API_KEY="$(cat "$VLLM_API_KEY_FILE")"
  export VLLM_API_KEY
fi
"${CMD[@]}"
echo "Started $NAME. Logs: docker logs -f $NAME   Stop: $0 --rank $RANK --stop"
if [[ "$RANK" == 0 ]]; then
  echo "Ready when: curl -fsS http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT/health  (200, empty body)"
fi
