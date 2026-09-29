#!/bin/bash
# Launch ONE rank of a two-DGX-Spark TensorFold server (--tp 2, one rank per
# Spark over NCCL). Runs ON A SPARK, not on the rig. docs/DGX_SPARK_PLAN.md
#
#   scripts/backends/tensorfold/spark-node.sh --rank 1 --master IP   # FIRST, on Spark 2
#   scripts/backends/tensorfold/spark-node.sh --rank 0 --master IP   # then on Spark 1
#
# Options:
#   --rank 0|1      required. 0 serves HTTP on SPARK_SERVE_HOST:PORT.
#   --master IP     required: rank 0's address on the ConnectX link
#                   (SPARK_HEAD_IP in spark.env)
#   --ckpt ID       checkpoint (TENSORFOLD_CKPT); both ranks the same
#   --env FILE      settings file (default: scripts/backends/spark.env)
#   --print         print the exact docker command and exit; runs nothing
#   --stop          docker stop this rank's container
#
# Order matters: start rank 1 FIRST, then rank 0 (TensorFold RUNBOOK.md,
# "two ranks"). Both ranks need the same checkpoint, drafter, --context and
# --parallel. After a mid-stream generation error on two ranks, restart BOTH.
#
# SECURITY. TensorFold has NO API key and its rendezvous port (29551) is
# unauthenticated. rank 0's HTTP binds SPARK_SERVE_HOST (a specific address;
# wildcards refused), and the rendezvous must be firewalled to the peer's
# link address — the listener's bind address is not configurable (verify on
# hardware with `ss -ltnp | grep 29551`). Put beast-gate or a firewall in
# front of the HTTP port. Alpha software: TENSORFOLD_REF must be a full
# 40-hex commit SHA (a tag or branch can be moved under us, and pip installs
# whatever it names at start time), and --no-update-check stops the server
# phoning GitHub for newer releases.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"

RANK="" MASTER="" CKPT_ARG="" ENV_FILE="" PRINT=0 STOP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rank)    RANK="${2:-}"; shift 2 ;;
    --master)  MASTER="${2:-}"; shift 2 ;;
    --ckpt)    CKPT_ARG="${2:-}"; shift 2 ;;
    --env)     ENV_FILE="${2:-}"; shift 2 ;;
    --print)   PRINT=1; shift ;;
    --stop)    STOP=1; shift ;;
    -h|--help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         echo "Unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
case "$RANK" in
  0|1) ;;
  *) echo "Error: --rank 0|1 is required (start rank 1 first)" >&2; exit 2 ;;
esac
NAME="openbeast-tensorfold-rank$RANK"
if [[ $STOP -eq 1 ]]; then
  exec docker stop "$NAME"
fi

ENV_FILE="${ENV_FILE:-${SPARK_ENV:-$HERE/../spark.env}}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Note: no settings file at $ENV_FILE — using the environment only (template: $HERE/../spark.env.example)." >&2
fi
sp_load "$ENV_FILE" TENSORFOLD_IMAGE TENSORFOLD_IMAGE_DIGEST TENSORFOLD_REF \
  SPARK_IFACE SPARK_IB_HCA SPARK_HEAD_IP TENSORFOLD_MASTER_PORT SPARK_SERVE_HOST \
  SPARK_SERVE_PORT SPARK_ALLOW_WILDCARD_BIND HF_CACHE TENSORFOLD_CKPT \
  TENSORFOLD_NAME TENSORFOLD_PARALLEL TENSORFOLD_CONTEXT
[[ -n "$MASTER" ]] || MASTER="${SPARK_HEAD_IP:-}"
[[ -n "$CKPT_ARG" ]] && TENSORFOLD_CKPT="$CKPT_ARG"
TENSORFOLD_MASTER_PORT="${TENSORFOLD_MASTER_PORT:-29551}"
SPARK_SERVE_PORT="${SPARK_SERVE_PORT:-8000}"
TENSORFOLD_NAME="${TENSORFOLD_NAME:-local-model}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"

[[ -n "$MASTER" ]] || sp_err "--master is required: rank 0's address on the ConnectX link"
sp_need TENSORFOLD_IMAGE "NVIDIA's PyTorch container (spark.env)"
sp_need TENSORFOLD_REF "the TensorFold commit to install, as a full 40-hex SHA"
sp_need TENSORFOLD_CKPT "the checkpoint (--ckpt); both ranks must match"
sp_need SPARK_IFACE "the ConnectX-7 interface ibdev2netdev shows Up"
sp_need SPARK_IB_HCA "the ConnectX-7 HCA ibdev2netdev shows Up"
if [[ -n "${TENSORFOLD_REF:-}" && ! "$TENSORFOLD_REF" =~ ^[0-9a-f]{40}$ ]]; then
  sp_err "TENSORFOLD_REF='$TENSORFOLD_REF' is not a commit SHA — a tag or branch can move; use the full 40-hex commit (v0.3.7 = 6b2e4c40064b1e4a05965f61b19ce87b5e0265b3)"
fi
[[ "$TENSORFOLD_MASTER_PORT" =~ ^[0-9]+$ ]] || sp_err "TENSORFOLD_MASTER_PORT='$TENSORFOLD_MASTER_PORT' is not a port"
[[ "$SPARK_SERVE_PORT" =~ ^[0-9]+$ ]] || sp_err "SPARK_SERVE_PORT='$SPARK_SERVE_PORT' is not a port"
if [[ -n "${TENSORFOLD_PARALLEL:-}" && ! "$TENSORFOLD_PARALLEL" =~ ^(auto|[1-9][0-9]*)$ ]]; then
  sp_err "TENSORFOLD_PARALLEL='$TENSORFOLD_PARALLEL' — auto or a positive integer"
fi
if [[ -n "${TENSORFOLD_CONTEXT:-}" && ! "$TENSORFOLD_CONTEXT" =~ ^[0-9]+$ ]]; then
  sp_err "TENSORFOLD_CONTEXT='$TENSORFOLD_CONTEXT' is not a token count"
fi
[[ "$RANK" == 0 ]] && sp_check_bind "${SPARK_SERVE_HOST:-}"
sp_image "${TENSORFOLD_IMAGE:-}" "${TENSORFOLD_IMAGE_DIGEST:-}" "$PRINT"
if [[ $SP_ERRORS -gt 0 ]]; then
  echo "Refusing to start: $SP_ERRORS setting(s) missing or unsafe (settings: $ENV_FILE)." >&2
  exit 1
fi

IF="$SPARK_IFACE"
SERVE=(serve "$TENSORFOLD_CKPT" --tp 2 --rank "$RANK"
  --master "$MASTER" --master-port "$TENSORFOLD_MASTER_PORT" --no-update-check)
[[ -n "${TENSORFOLD_PARALLEL:-}" ]] && SERVE+=(--parallel "$TENSORFOLD_PARALLEL")
[[ -n "${TENSORFOLD_CONTEXT:-}" ]] && SERVE+=(--context "$TENSORFOLD_CONTEXT")
if [[ "$RANK" == 0 ]]; then
  SERVE+=(--name "$TENSORFOLD_NAME" --host "$SPARK_SERVE_HOST" --port "$SPARK_SERVE_PORT")
fi
# The container installs the pinned tag, then execs the server. Arguments
# travel as positional parameters, never spliced into the script text.
# shellcheck disable=SC2016  # expanded inside the container, not here
INNER='python -m pip install --quiet "$1" && shift && exec tensorfold "$@"'
CMD=(docker run -d --rm --name "$NAME"
  --gpus all --ipc=host --network host
  --device /dev/infiniband --ulimit memlock=-1 --cap-add IPC_LOCK
  -e "NCCL_SOCKET_IFNAME=$IF" -e "GLOO_SOCKET_IFNAME=$IF" -e "TP_SOCKET_IFNAME=$IF"
  -e "UCX_NET_DEVICES=$IF" -e "NCCL_IB_HCA=$SPARK_IB_HCA")
[[ -n "${NCCL_DEBUG:-}" ]] && CMD+=(-e NCCL_DEBUG)
[[ -n "${HF_TOKEN:-}" ]] && CMD+=(-e HF_TOKEN)
CMD+=(-v "$HF_CACHE:/root/.cache/huggingface"
  -v "$HOME/.cache/tensorfold:/root/.cache/tensorfold"
  --entrypoint bash "$SP_IMAGE_REF"
  -c "$INNER" tensorfold-launch
  "git+https://github.com/ashhart/TensorFold.git@${TENSORFOLD_REF}"
  "${SERVE[@]}")

if [[ $PRINT -eq 1 ]]; then
  [[ "$RANK" == 0 ]] && echo "# start rank 1 FIRST; rank 0 serves http://${SPARK_SERVE_HOST}:${SPARK_SERVE_PORT} (NO API key — gate or firewall it)"
  sp_print_cmd "${CMD[@]}"
  exit 0
fi

if [[ "$RANK" == 0 ]]; then
  echo "Rank 0: make sure rank 1 is already running. Serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT when both are up."
  echo "  TensorFold has NO API key — keep this port behind beast-gate or a firewall."
else
  echo "Rank 1: waiting for rank 0 at $MASTER:$TENSORFOLD_MASTER_PORT (start rank 0 next)."
fi
echo "  Rendezvous $TENSORFOLD_MASTER_PORT is unauthenticated: firewall it to the peer's link address."
mkdir -p "$HOME/.cache/tensorfold"
"${CMD[@]}"
echo "Started $NAME. Logs: docker logs -f $NAME   Stop: $0 --rank $RANK --stop"
echo "The first start compiles kernels for this GPU (sm_121) and can take a while."
