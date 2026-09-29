#!/bin/bash
# Launch ONE rank of a DGX Spark vLLM server for a MODEL PROFILE (tensor
# parallel 2 across both Sparks — native multi-node, no Ray — or TP 1 on one
# Spark). Runs ON A SPARK, not on the rig. docs/DGX_SPARK_PLAN.md
#
#   scripts/backends/vllm/spark-node.sh --profile NAME --rank 1   # on Spark 2 (TP 2 only)
#   scripts/backends/vllm/spark-node.sh --profile NAME --rank 0   # on Spark 1
#
# Options:
#   --profile NAME|PATH  required (or SPARK_PROFILE): scripts/backends/models/NAME.env —
#                   the model, its revision, parsers, context, memory, extra args
#   --rank 0|1      required. 0 = head: serves HTTP on SPARK_SERVE_HOST:PORT.
#                   1 = worker: --headless, no HTTP (TP 2 profiles only).
#   --head-addr IP  rank 0's address on the ConnectX link (SPARK_HEAD_IP)
#   --env FILE      HOST settings (default: scripts/backends/spark.env;
#                   template: spark.env.example next to it)
#   --print         print the exact docker command and exit; runs nothing
#   --stop          docker stop this rank's container
#
# The model comes ONLY from the profile. If model-fetch.sh has fetched and
# verified it into MODELS_DIR, that directory is mounted read-only and
# served; otherwise vLLM downloads the profile's SOURCE at its pinned
# REVISION (--revision/--tokenizer-revision) into HF_CACHE, unverified by us
# — the launcher says so. A lock mismatch refuses to start.
#
# The API key never touches argv: vLLM reads VLLM_API_KEY from its
# environment (vllm/envs.py; middleware/register.py prefers --api-key, which
# we never pass), docker forwards `-e VLLM_API_KEY` from THIS shell's
# environment, and this shell reads it from a 0600 file.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"

RANK="" HEAD_ADDR="" PROFILE="" ENV_FILE="" PRINT=0 STOP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rank)       RANK="${2:-}"; shift 2 ;;
    --head-addr)  HEAD_ADDR="${2:-}"; shift 2 ;;
    --profile)    PROFILE="${2:-}"; shift 2 ;;
    --env)        ENV_FILE="${2:-}"; shift 2 ;;
    --print)      PRINT=1; shift ;;
    --stop)       STOP=1; shift ;;
    --model)      echo "Error: --model is gone — the model comes from a profile: --profile NAME (scripts/backends/models/)" >&2; exit 2 ;;
    -h|--help)    sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
  SPARK_ALLOW_WILDCARD_BIND HF_CACHE MODELS_DIR SPARK_PROFILE VLLM_API_KEY_FILE SPARK_NO_API_KEY
sp_warn_legacy_model_keys "$ENV_FILE"
[[ -n "$HEAD_ADDR" ]] && SPARK_HEAD_IP="$HEAD_ADDR"
PROFILE="${PROFILE:-${SPARK_PROFILE:-}}"
if [[ -z "$PROFILE" ]]; then
  echo "Error: --profile NAME is required (scripts/backends/models/NAME.env; start from TEMPLATE.env or model-inspect.sh --write-profile)" >&2
  exit 2
fi
if ! sp_profile_load "$PROFILE" vllm; then
  echo "Refusing to start: profile '$PROFILE' is invalid (above)." >&2
  exit 1
fi
VLLM_MASTER_PORT="${VLLM_MASTER_PORT:-29501}"
SPARK_SERVE_PORT="${SPARK_SERVE_PORT:-8000}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
TP="$PF_TENSOR_PARALLEL_SIZE"
GMU="${PF_GPU_MEMORY_UTILIZATION:-0.80}"

sp_need VLLM_IMAGE "the vLLM container image (spark.env)"
[[ "$VLLM_MASTER_PORT" =~ ^[0-9]+$ ]] || sp_err "VLLM_MASTER_PORT='$VLLM_MASTER_PORT' is not a port"
[[ "$SPARK_SERVE_PORT" =~ ^[0-9]+$ ]] || sp_err "SPARK_SERVE_PORT='$SPARK_SERVE_PORT' is not a port"
if [[ "$TP" == 2 ]]; then
  sp_need SPARK_IFACE "the ConnectX-7 interface ibdev2netdev shows Up"
  sp_need SPARK_IB_HCA "the ConnectX-7 HCA ibdev2netdev shows Up"
  sp_need SPARK_HEAD_IP "rank 0's address on the ConnectX link (--head-addr)"
elif [[ "$RANK" == 1 ]]; then
  sp_err "profile '$PF_PROFILE_NAME' is TENSOR_PARALLEL_SIZE=1: it runs on ONE Spark — start --rank 0 only"
fi

# Where the weights come from: the verified local copy, else the Hub at the pin.
sp_profile_locate "$PROFILE" "${MODELS_DIR:-}"
MOUNTS=() SERVE_MODEL="" REV_ARGS=()
if [[ -n "$SP_MODEL_DIR" && $SP_LOCATE_RC -eq 0 ]]; then
  MOUNTS+=(-v "$SP_MODEL_DIR:/models/$PF_PROFILE_NAME:ro")
  SERVE_MODEL="/models/$PF_PROFILE_NAME"
elif [[ $SP_LOCATE_RC -eq 3 && "$PF_PROFILE_IS_HF" == 1 ]]; then
  echo "Warning: $PF_SOURCE@$PF_REVISION is not fetched into MODELS_DIR — vLLM will download it at the pinned revision, UNVERIFIED by us. Run model-fetch.sh --profile $PF_PROFILE_NAME (on both Sparks for TP 2) to verify and lock it." >&2
  SERVE_MODEL="$PF_SOURCE"
  REV_ARGS=(--revision "$PF_REVISION" --tokenizer-revision "$PF_REVISION")
  [[ "$PF_TRUST_REMOTE_CODE" == true ]] && REV_ARGS+=(--code-revision "$PF_REVISION")
else
  sp_err "profile '$PF_PROFILE_NAME': the model on disk does not match its lock (above) — run model-fetch.sh --profile $PF_PROFILE_NAME --verify"
fi
USE_KEY=0
if [[ "$RANK" == 0 ]]; then
  sp_check_bind "${SPARK_SERVE_HOST:-}"
  if sp_is_true "${SPARK_NO_API_KEY:-}"; then
    echo "Warning: SPARK_NO_API_KEY=true — vLLM will serve /v1 WITHOUT a key." >&2
  else
    sp_need VLLM_API_KEY_FILE "a 0600 file holding the API key (or SPARK_NO_API_KEY=true)"
    [[ -n "${VLLM_API_KEY_FILE:-}" ]] && sp_check_secret_file "$VLLM_API_KEY_FILE"
    USE_KEY=1
  fi
  [[ -n "$PF_TOOL_CALL_PARSER" ]] || echo "Warning: profile '$PF_PROFILE_NAME' has no TOOL_CALL_PARSER — tool calls will arrive as text and OpenBeast's tools will not work (model-inspect.sh suggests one)." >&2
fi
sp_image "${VLLM_IMAGE:-}" "${VLLM_IMAGE_DIGEST:-}" "$PRINT"
if [[ $SP_ERRORS -gt 0 ]]; then
  echo "Refusing to start: $SP_ERRORS setting(s) missing or unsafe (settings: $ENV_FILE, profile: $PF_PROFILE_PATH)." >&2
  exit 1
fi

CMD=(docker run -d --rm --name "$NAME"
  --gpus all --ipc=host --network host
  --ulimit memlock=-1 --ulimit nofile=1048576:1048576 --cap-add IPC_LOCK)
if [[ "$TP" == 2 ]]; then
  IF="$SPARK_IFACE"
  CMD+=(--device /dev/infiniband
    -e "NCCL_SOCKET_IFNAME=$IF" -e "GLOO_SOCKET_IFNAME=$IF" -e "TP_SOCKET_IFNAME=$IF"
    -e "UCX_NET_DEVICES=$IF" -e "OMPI_MCA_btl_tcp_if_include=$IF"
    -e "NCCL_IB_HCA=$SPARK_IB_HCA" -e NCCL_IB_DISABLE=0 -e NCCL_IGNORE_CPU_AFFINITY=1)
  [[ -n "${SPARK_NODE_IP:-}" ]] && CMD+=(-e "VLLM_HOST_IP=$SPARK_NODE_IP")
fi
# llama-server ignores the request's model id and the whole stack relies on
# that (opencode.json, setup-client.sh); vLLM 404s unknown ids unless told
# not to (vllm/entrypoints/serve/engine/serving.py).
CMD+=(-e VLLM_SKIP_MODEL_NAME_VALIDATION=1)
# Pass-through by NAME only (docker copies the value from this shell's env),
# so a token or a debug level never lands on argv.
[[ -n "${NCCL_DEBUG:-}" ]] && CMD+=(-e NCCL_DEBUG)
[[ -n "${HF_TOKEN:-}" ]] && CMD+=(-e HF_TOKEN)
[[ $USE_KEY -eq 1 ]] && CMD+=(-e VLLM_API_KEY)
CMD+=(-v "$HF_CACHE:/root/.cache/huggingface" ${MOUNTS[@]+"${MOUNTS[@]}"})
[[ -n "$PF_CHAT_TEMPLATE" ]] && CMD+=(-v "$PF_CHAT_TEMPLATE:/openbeast/chat_template.jinja:ro")
CMD+=(--entrypoint vllm "$SP_IMAGE_REF"
  serve "$SERVE_MODEL" ${REV_ARGS[@]+"${REV_ARGS[@]}"}
  --tensor-parallel-size "$TP" --gpu-memory-utilization "$GMU")
if [[ "$TP" == 2 ]]; then
  CMD+=(--nnodes 2 --node-rank "$RANK" --master-addr "$SPARK_HEAD_IP" --master-port "$VLLM_MASTER_PORT")
fi
[[ -n "$PF_MAX_MODEL_LEN" ]] && CMD+=(--max-model-len "$PF_MAX_MODEL_LEN")
[[ -n "$PF_DTYPE" ]] && CMD+=(--dtype "$PF_DTYPE")
[[ -n "$PF_QUANTIZATION" ]] && CMD+=(--quantization "$PF_QUANTIZATION")
[[ -n "$PF_MAX_NUM_SEQS" ]] && CMD+=(--max-num-seqs "$PF_MAX_NUM_SEQS")
[[ -n "$PF_SPECULATIVE_CONFIG" ]] && CMD+=(--speculative-config "$PF_SPECULATIVE_CONFIG")
# --trust-remote-code executes Python shipped in the model repo. obprofile.py
# lets it through only with TRUST_REMOTE_CODE_ACK equal to the pinned REVISION.
[[ "$PF_TRUST_REMOTE_CODE" == true ]] && CMD+=(--trust-remote-code)
CMD+=(${PF_EXTRA[@]+"${PF_EXTRA[@]}"})
if [[ "$RANK" == 0 ]]; then
  CMD+=(--host "$SPARK_SERVE_HOST" --port "$SPARK_SERVE_PORT"
    --served-model-name "$PF_SERVED_MODEL_NAME")
  [[ -n "$PF_CHAT_TEMPLATE" ]] && CMD+=(--chat-template /openbeast/chat_template.jinja)
  [[ -n "$PF_REASONING_PARSER" ]] && CMD+=(--reasoning-parser "$PF_REASONING_PARSER")
  [[ -n "$PF_TOOL_CALL_PARSER" ]] && CMD+=(--enable-auto-tool-choice --tool-call-parser "$PF_TOOL_CALL_PARSER")
else
  CMD+=(--headless)
fi

if [[ $PRINT -eq 1 ]]; then
  echo "# profile $PF_PROFILE_NAME: $PF_SOURCE${PF_REVISION:+@$PF_REVISION} (TP $TP)"
  [[ $USE_KEY -eq 1 ]] && echo "# VLLM_API_KEY is exported from $VLLM_API_KEY_FILE into docker's environment (not argv)"
  sp_print_cmd "${CMD[@]}"
  exit 0
fi

if [[ "$RANK" == 0 ]]; then
  if [[ "$TP" == 2 ]]; then
    echo "Rank 0 (head): serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT once rank 1 has joined."
  else
    echo "Serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT (one Spark, TP 1)."
  fi
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
  echo "Then, from the rig: scripts/backends/conformance.sh --url http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT --model '$PF_SERVED_MODEL_NAME'"
fi
