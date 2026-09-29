#!/bin/bash
# Launch ONE rank of a DGX Spark TensorFold server for a MODEL PROFILE (--tp 2,
# one rank per Spark over NCCL, or --tp 1 on one Spark). Runs ON A SPARK, not
# on the rig. docs/DGX_SPARK_PLAN.md
#
#   scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 1 --master IP   # FIRST, on Spark 2
#   scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0 --master IP   # then on Spark 1
#
# Options:
#   --profile NAME|PATH  required (or SPARK_PROFILE): scripts/backends/models/NAME.env
#   --rank 0|1      required. 0 serves HTTP on SPARK_SERVE_HOST:PORT.
#   --master IP     rank 0's address on the ConnectX link (SPARK_HEAD_IP);
#                   required for TP 2
#   --env FILE      HOST settings (default: scripts/backends/spark.env)
#   --print         print the exact docker command and exit; runs nothing
#   --stop          docker stop this rank's container
#
# The checkpoint MUST be fetched first (model-fetch.sh --profile NAME, on both
# Sparks for TP 2): TensorFold's own download takes the latest commit of a
# repo id with no way to pin a revision (hub.py pull → snapshot_download),
# so this launcher only ever hands it a verified, read-only local directory.
# A drafter is the profile's DRAFTER_SOURCE@DRAFTER_REVISION, fetched the same
# way; without one it passes --drafter none (never "auto": that picks up
# whatever unpinned draft model happens to sit in the HF cache).
#
# Order matters: start rank 1 FIRST, then rank 0 (TensorFold RUNBOOK.md,
# "two ranks"). Both ranks need the same checkpoint, drafter, --context and
# --parallel — one profile gives them that. After a mid-stream generation
# error on two ranks, restart BOTH.
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

RANK="" MASTER="" PROFILE="" ENV_FILE="" PRINT=0 STOP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rank)    RANK="${2:-}"; shift 2 ;;
    --master)  MASTER="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --env)     ENV_FILE="${2:-}"; shift 2 ;;
    --print)   PRINT=1; shift ;;
    --stop)    STOP=1; shift ;;
    --ckpt)    echo "Error: --ckpt is gone — the checkpoint comes from a profile: --profile NAME (scripts/backends/models/)" >&2; exit 2 ;;
    -h|--help) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
  SPARK_SERVE_PORT SPARK_ALLOW_WILDCARD_BIND MODELS_DIR SPARK_PROFILE
sp_warn_legacy_model_keys "$ENV_FILE"
PROFILE="${PROFILE:-${SPARK_PROFILE:-}}"
if [[ -z "$PROFILE" ]]; then
  echo "Error: --profile NAME is required (scripts/backends/models/NAME.env; start from TEMPLATE.env or model-inspect.sh --write-profile)" >&2
  exit 2
fi
if ! sp_profile_load "$PROFILE" tensorfold; then
  echo "Refusing to start: profile '$PROFILE' is invalid (above)." >&2
  exit 1
fi
TP="$PF_TENSOR_PARALLEL_SIZE"
[[ -n "$MASTER" ]] || MASTER="${SPARK_HEAD_IP:-}"
TENSORFOLD_MASTER_PORT="${TENSORFOLD_MASTER_PORT:-29551}"
SPARK_SERVE_PORT="${SPARK_SERVE_PORT:-8000}"

if [[ "$TP" == 2 ]]; then
  [[ -n "$MASTER" ]] || sp_err "--master is required: rank 0's address on the ConnectX link"
  sp_need SPARK_IFACE "the ConnectX-7 interface ibdev2netdev shows Up"
  sp_need SPARK_IB_HCA "the ConnectX-7 HCA ibdev2netdev shows Up"
elif [[ "$RANK" == 1 ]]; then
  sp_err "profile '$PF_PROFILE_NAME' is TENSOR_PARALLEL_SIZE=1: it runs on ONE Spark — start --rank 0 only"
fi
sp_need TENSORFOLD_IMAGE "NVIDIA's PyTorch container (spark.env)"
sp_need TENSORFOLD_REF "the TensorFold commit to install, as a full 40-hex SHA"
if [[ -n "${TENSORFOLD_REF:-}" && ! "$TENSORFOLD_REF" =~ ^[0-9a-f]{40}$ ]]; then
  sp_err "TENSORFOLD_REF='$TENSORFOLD_REF' is not a commit SHA — a tag or branch can move; use the full 40-hex commit (v0.3.7 = 6b2e4c40064b1e4a05965f61b19ce87b5e0265b3)"
fi
[[ "$TENSORFOLD_MASTER_PORT" =~ ^[0-9]+$ ]] || sp_err "TENSORFOLD_MASTER_PORT='$TENSORFOLD_MASTER_PORT' is not a port"
[[ "$SPARK_SERVE_PORT" =~ ^[0-9]+$ ]] || sp_err "SPARK_SERVE_PORT='$SPARK_SERVE_PORT' is not a port"
[[ "$RANK" == 0 ]] && sp_check_bind "${SPARK_SERVE_HOST:-}"

sp_profile_locate "$PROFILE" "${MODELS_DIR:-}"
MOUNTS=() DRAFTER=none
if [[ $SP_LOCATE_RC -ne 0 || -z "$SP_MODEL_DIR" ]]; then
  if [[ $SP_LOCATE_RC -eq 3 ]]; then
    sp_err "profile '$PF_PROFILE_NAME' is not fetched — TensorFold cannot pin a revision itself, so it only serves a verified local copy: model-fetch.sh --profile $PF_PROFILE_NAME (on both Sparks for TP 2)"
  else
    sp_err "profile '$PF_PROFILE_NAME': the model on disk does not match its lock (above) — model-fetch.sh --profile $PF_PROFILE_NAME --verify"
  fi
else
  MOUNTS+=(-v "$SP_MODEL_DIR:/models/$PF_PROFILE_NAME:ro")
  if [[ -n "$PF_DRAFTER_SOURCE" ]]; then
    MOUNTS+=(-v "$SP_DRAFTER_DIR:/models/$PF_PROFILE_NAME.drafter:ro")
    DRAFTER="/models/$PF_PROFILE_NAME.drafter"
  fi
fi
sp_image "${TENSORFOLD_IMAGE:-}" "${TENSORFOLD_IMAGE_DIGEST:-}" "$PRINT"
if [[ $SP_ERRORS -gt 0 ]]; then
  echo "Refusing to start: $SP_ERRORS setting(s) missing or unsafe (settings: $ENV_FILE, profile: $PF_PROFILE_PATH)." >&2
  exit 1
fi

SERVE=(serve "/models/$PF_PROFILE_NAME" --tp "$TP" --rank "$RANK" --no-update-check --drafter "$DRAFTER")
[[ "$TP" == 2 ]] && SERVE+=(--master "$MASTER" --master-port "$TENSORFOLD_MASTER_PORT")
[[ -n "$PF_TENSORFOLD_PARALLEL" ]] && SERVE+=(--parallel "$PF_TENSORFOLD_PARALLEL")
[[ -n "$PF_MAX_MODEL_LEN" ]] && SERVE+=(--context "$PF_MAX_MODEL_LEN")
SERVE+=(${PF_EXTRA[@]+"${PF_EXTRA[@]}"})
if [[ "$RANK" == 0 ]]; then
  SERVE+=(--name "$PF_SERVED_MODEL_NAME" --host "$SPARK_SERVE_HOST" --port "$SPARK_SERVE_PORT")
fi
# The container installs the pinned commit, then execs the server. Arguments
# travel as positional parameters, never spliced into the script text.
# shellcheck disable=SC2016  # expanded inside the container, not here
INNER='python -m pip install --quiet "$1" && shift && exec tensorfold "$@"'
CMD=(docker run -d --rm --name "$NAME"
  --gpus all --ipc=host --network host --ulimit memlock=-1 --cap-add IPC_LOCK)
if [[ "$TP" == 2 ]]; then
  IF="$SPARK_IFACE"
  CMD+=(--device /dev/infiniband
    -e "NCCL_SOCKET_IFNAME=$IF" -e "GLOO_SOCKET_IFNAME=$IF" -e "TP_SOCKET_IFNAME=$IF"
    -e "UCX_NET_DEVICES=$IF" -e "NCCL_IB_HCA=$SPARK_IB_HCA")
fi
[[ -n "${NCCL_DEBUG:-}" ]] && CMD+=(-e NCCL_DEBUG)
CMD+=("${MOUNTS[@]}"
  -v "$HOME/.cache/tensorfold:/root/.cache/tensorfold"
  -e HF_HUB_OFFLINE=1
  --entrypoint bash "$SP_IMAGE_REF"
  -c "$INNER" tensorfold-launch
  "git+https://github.com/ashhart/TensorFold.git@${TENSORFOLD_REF}"
  "${SERVE[@]}")

if [[ $PRINT -eq 1 ]]; then
  echo "# profile $PF_PROFILE_NAME: $PF_SOURCE${PF_REVISION:+@$PF_REVISION} (TP $TP) from $SP_MODEL_DIR"
  [[ "$RANK" == 0 && "$TP" == 2 ]] && echo "# start rank 1 FIRST; rank 0 serves http://${SPARK_SERVE_HOST}:${SPARK_SERVE_PORT} (NO API key — gate or firewall it)"
  [[ "$RANK" == 0 && "$TP" == 1 ]] && echo "# serves http://${SPARK_SERVE_HOST}:${SPARK_SERVE_PORT} (NO API key — gate or firewall it)"
  sp_print_cmd "${CMD[@]}"
  exit 0
fi

if [[ "$RANK" == 0 ]]; then
  [[ "$TP" == 2 ]] && echo "Rank 0: make sure rank 1 is already running. Serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT when both are up."
  [[ "$TP" == 1 ]] && echo "Serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT (one Spark, TP 1)."
  echo "  TensorFold has NO API key — keep this port behind beast-gate or a firewall."
else
  echo "Rank 1: waiting for rank 0 at $MASTER:$TENSORFOLD_MASTER_PORT (start rank 0 next)."
fi
[[ "$TP" == 2 ]] && echo "  Rendezvous $TENSORFOLD_MASTER_PORT is unauthenticated: firewall it to the peer's link address."
mkdir -p "$HOME/.cache/tensorfold"
"${CMD[@]}"
echo "Started $NAME. Logs: docker logs -f $NAME   Stop: $0 --rank $RANK --stop"
echo "The first start compiles kernels for this GPU (sm_121) and can take a while."
