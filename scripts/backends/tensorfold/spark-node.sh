#!/bin/bash
# Launch ONE rank of a DGX Spark TensorFold server for a MODEL PROFILE (--tp N
# for N = 2..8 Sparks, one rank per Spark over NCCL, or --tp 1 on one Spark).
# Runs ON A SPARK, not on the rig. docs/DGX_SPARK_PLAN.md
#
# Two engines, picked by TENSORFOLD_ENGINE in spark.env:
#   native     (default) the signed TensorFold >= 1.0 release binary on the
#              host (install-native.sh), run as a systemd --user unit. Keyed
#              with --api-key-file. GLM-5.3-Flash places its ranks with
#              TF_GLM_RANK / TF_GLM_WORLD / NCCL_COMM_ID (any TP here),
#              MiniMax-M3.1-Flash with TF_MINIMAX_* (TP 2 only); other
#              families with --tp 2/4/8 / --rank / --master (Flash Next: 2
#              at most; Nemotron's TF_NEMOTRON_* is not wired: TP 1 only).
#   container  the frozen Python 0.x engine, pip-installed at a commit SHA
#              into NVIDIA's PyTorch container. No auth. Kept for profiles
#              that need it; everything below about TENSORFOLD_REF, the image
#              and the unauthenticated rendezvous is about this engine.
#
#   scripts/backends/tensorfold/spark-node.sh --profile NAME --rank R --master IP   # FIRST, R = 1..N-1, one per Spark
#   scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0 --master IP   # then on Spark 1
#
# Options:
#   --profile NAME|PATH  required (or SPARK_PROFILE): scripts/backends/models/NAME.env
#   --rank R        required, 0..N-1 for the profile's TENSOR_PARALLEL_SIZE=N
#                   (1..8). 0 serves HTTP on SPARK_SERVE_HOST:PORT; the rest
#                   are headless.
#   --master IP     rank 0's address on the ConnectX link (SPARK_HEAD_IP);
#                   required for TP > 1
#   --env FILE      HOST settings (default: scripts/backends/spark.env)
#   --print         print the exact command and exit; runs nothing
#   --stop          stop this rank: its native unit and/or container (one that
#                   is already gone is "not running", exit 0)
#
# The checkpoint MUST be fetched first (model-fetch.sh --profile NAME, on every
# Spark that runs a rank): TensorFold's own download takes the latest commit of a
# repo id with no way to pin a revision (hub.py pull → snapshot_download),
# so this launcher only ever hands it a verified, read-only local directory.
# A drafter is the profile's DRAFTER_SOURCE@DRAFTER_REVISION, fetched the same
# way. Without one, the CONTAINER engine passes --drafter none (never "auto":
# that picks up whatever unpinned draft model happens to sit in the HF cache).
# The NATIVE engine then passes no --drafter at all: tensorfold-native 1.2.7
# rejects `--drafter none` (and `auto`) for EVERY family during argument
# checks, before reading the model ("none is neither a directory nor a
# Hugging Face repo id", checked on spark-1 2026-11-16); a family with an MTP
# head drafts with it. The managed single-host launcher (scripts/engines/
# tensorfold.sh) follows the same rule. VERIFY ON HARDWARE that a plain
# native serve loads no cached drafter (HF_HUB_OFFLINE=1 does not hide a
# cached copy).
#
# Order matters: start ranks 1..N-1 FIRST, then rank 0 (TensorFold RUNBOOK.md,
# "two ranks"). Every rank needs the same checkpoint, drafter, --context and
# --parallel — one profile gives them that. After a mid-stream generation
# error on several ranks, restart ALL of them.
#
# SPARK_IFACE / SPARK_IB_HCA may be comma-separated lists (several rails, or a
# NIC per peer); they reach NCCL unchanged. From 3 ranks on, NCCL_CROSS_NIC=1
# (rails / NICs per peer). VERIFY ON HARDWARE: nothing past 2 ranks has run
# through this launcher; rails on separate subnets may also need
# NCCL_IB_SUBNET_AWARE_ROUTING=1, and a cabled 3-Spark ring cannot rendezvous
# here at all (one SPARK_HEAD_IP; spark-cluster.sh check flags it).
#
# SECURITY. TensorFold has NO API key and its rendezvous port (29551) is
# unauthenticated. rank 0's HTTP binds SPARK_SERVE_HOST: wildcards (any
# spelling) are refused, and a keyless server binds loopback only, behind
# beast-gate, unless SPARK_ALLOW_KEYLESS_BIND=true; the rendezvous must be firewalled to the peer's
# link address — the listener's bind address is not configurable (verify on
# hardware with `ss -ltnp | grep 29551`). Put beast-gate or a firewall in
# front of the HTTP port. Alpha software: TENSORFOLD_REF must be a full
# 40-hex commit SHA (a tag or branch can be moved under us, and pip installs
# whatever it names at start time; its dependencies need TENSORFOLD_IMAGE_PREBUILT
# or a hash-locked TENSORFOLD_PIP_LOCK), and --no-update-check stops the server
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
    -h|--help) sed -n '2,60p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         echo "Unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
if ! sp_rank_syntax "$RANK"; then
  echo "Error: --rank R is required: 0..N-1 for a TENSOR_PARALLEL_SIZE=N profile (start ranks 1..N-1 first)" >&2
  exit 2
fi
NAME="openbeast-tensorfold-rank$RANK"
if [[ $STOP -eq 1 ]]; then
  # Idempotent, and engine-blind: stops this rank's native unit and/or its
  # container, whichever exists. A rank already gone (a --collect unit, a --rm
  # container) is "not running", exit 0, so a cluster stop can walk every rank.
  _found=0
  if command -v systemctl >/dev/null 2>&1 \
     && systemctl --user list-units --all --plain --no-legend "$NAME.service" 2>/dev/null | grep -q .; then
    _found=1; systemctl --user stop "$NAME"; echo "Stopped unit $NAME."
  fi
  if command -v docker >/dev/null 2>&1 && docker container inspect "$NAME" >/dev/null 2>&1; then
    _found=1; docker stop "$NAME" >/dev/null; echo "Stopped container $NAME."
  fi
  [[ $_found -eq 1 ]] || echo "$NAME is not running (nothing to stop)."
  exit 0
fi

ENV_FILE="${ENV_FILE:-${SPARK_ENV:-$HERE/../spark.env}}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Note: no settings file at $ENV_FILE — using the environment only (template: $HERE/../spark.env.example)." >&2
fi
sp_load "$ENV_FILE" TENSORFOLD_ENGINE TENSORFOLD_BIN TENSORFOLD_VERSION TENSORFOLD_API_KEY_FILE \
  SPARK_NO_API_KEY TENSORFOLD_IMAGE TENSORFOLD_IMAGE_DIGEST TENSORFOLD_IMAGE_ID TENSORFOLD_REF \
  SPARK_IFACE SPARK_IB_HCA SPARK_HEAD_IP TENSORFOLD_MASTER_PORT SPARK_SERVE_HOST \
  SPARK_SERVE_PORT SPARK_ALLOW_WILDCARD_BIND SPARK_ALLOW_KEYLESS_BIND MODELS_DIR SPARK_PROFILE \
  TENSORFOLD_IMAGE_PREBUILT TENSORFOLD_PIP_LOCK TENSORFOLD_ALLOW_UNPINNED_DEPS
ENGINE="${TENSORFOLD_ENGINE:-native}"
case "$ENGINE" in
  native|container) ;;
  *) echo "Error: TENSORFOLD_ENGINE='$ENGINE' — native or container" >&2; exit 2 ;;
esac
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
if ! sp_check_rank "$RANK" "$TP"; then
  echo "Refusing to start: profile '$PROFILE' is invalid (above)." >&2
  exit 1
fi
[[ -n "$MASTER" ]] || MASTER="${SPARK_HEAD_IP:-}"
# 29551: the Python engine's TCPStore. 29583: native GLM's NCCL_COMM_ID port
# (its recipe's); each rank also keeps a TCP line to rank 0 on port + 1.
if [[ "$ENGINE" == native ]]; then
  TENSORFOLD_MASTER_PORT="${TENSORFOLD_MASTER_PORT:-29583}"
else
  TENSORFOLD_MASTER_PORT="${TENSORFOLD_MASTER_PORT:-29551}"
fi
SPARK_SERVE_PORT="${SPARK_SERVE_PORT:-8000}"

if (( TP > 1 )); then
  [[ -n "$MASTER" ]] || sp_err "--master is required: rank 0's address on the ConnectX link"
  sp_need SPARK_IFACE "the ConnectX-7 interface ibdev2netdev shows Up"
  sp_need SPARK_IB_HCA "the ConnectX-7 HCA ibdev2netdev shows Up"
fi
if [[ "$ENGINE" == native ]]; then
  TENSORFOLD_BIN="${TENSORFOLD_BIN:-$HOME/.local/bin/tensorfold}"
  sp_need TENSORFOLD_VERSION "the release install-native.sh installed (e.g. 1.2.10) — the launcher refuses any other binary"
  if [[ ! -x "$TENSORFOLD_BIN" ]]; then
    sp_err "no TensorFold binary at $TENSORFOLD_BIN — scripts/backends/tensorfold/install-native.sh"
  elif [[ -n "${TENSORFOLD_VERSION:-}" ]]; then
    # || true: a binary that cannot even run (missing library, wrong arch)
    # is reported as '?' below, not a silent exit under set -e/pipefail.
    _have="$( ("$TENSORFOLD_BIN" --version 2>/dev/null || true) | awk '{print $NF}')"
    [[ "$_have" == "$TENSORFOLD_VERSION" ]] \
      || sp_err "$TENSORFOLD_BIN is TensorFold '${_have:-?}', spark.env pins $TENSORFOLD_VERSION — every rank must run the same build"
    # A profile planned for one release (beast-recipe renders TENSORFOLD_REQUIRES)
    # never runs on another: ~/.local/bin/tensorfold is one link per Spark.
    if [[ -n "$PF_TENSORFOLD_REQUIRES" && "${_have:-}" != "$PF_TENSORFOLD_REQUIRES" ]]; then
      sp_err "profile '$PF_PROFILE_NAME' needs native TensorFold $PF_TENSORFOLD_REQUIRES, $TENSORFOLD_BIN is '${_have:-?}': scripts/engine.sh install tensorfold --version $PF_TENSORFOLD_REQUIRES (or engine.sh rollback tensorfold) on every Spark, and TENSORFOLD_VERSION=$PF_TENSORFOLD_REQUIRES in spark.env"
    fi
  fi
  # --kv-dtype is the 0.x Python engine's flag (bf16|int8|int4); native refuses it.
  for _a in ${PF_EXTRA[@]+"${PF_EXTRA[@]}"}; do
    [[ "$_a" == --kv-dtype || "$_a" == --kv-dtype=* || "$_a" == --kv_dtype* ]] \
      && sp_err "profile '$PF_PROFILE_NAME': EXTRA_ARGS has $_a, which native TensorFold refuses — set TENSORFOLD_KV_CACHE=bf16|fp8 (its --kv-cache) instead"
  done
  if [[ "$RANK" == 0 ]] && ! sp_is_true "${SPARK_NO_API_KEY:-}"; then
    sp_need TENSORFOLD_API_KEY_FILE "the 0600 file rank 0 serves with --api-key-file (the rig's LLAMA_API_KEY holds the same value)"
    [[ -n "${TENSORFOLD_API_KEY_FILE:-}" ]] && sp_check_secret_file "$TENSORFOLD_API_KEY_FILE"
  fi
else
  for _k in TENSORFOLD_KV_CACHE TENSORFOLD_MEMORY_RESERVE_GIB TENSORFOLD_REQUIRES TENSORFOLD_RANK_ENV; do
    _v="PF_$_k"
    [[ -n "${!_v:-}" ]] && sp_err "profile '$PF_PROFILE_NAME' sets $_k, a native TensorFold setting: TENSORFOLD_ENGINE=native serves it (the container engine has no equivalent)"
  done
  sp_need TENSORFOLD_IMAGE "NVIDIA's PyTorch container (spark.env)"
  sp_need TENSORFOLD_REF "the TensorFold commit to install, as a full 40-hex SHA"
  if [[ -n "${TENSORFOLD_REF:-}" && ! "$TENSORFOLD_REF" =~ ^[0-9a-f]{40}$ ]]; then
    sp_err "TENSORFOLD_REF='$TENSORFOLD_REF' is not a commit SHA — a tag or branch can move; use the full 40-hex commit (v0.5.0 = 9cd52ab4daba68ddd09be89be8f23ad43175e821)"
  fi
  # TENSORFOLD_REF pins TensorFold itself, not what pip resolves for its
  # dependencies — and the --rm container resolves them again on every start,
  # as root with host networking and every GPU. So the dependencies are pinned
  # too, one of three ways:
  #   prebuilt  TENSORFOLD_IMAGE_PREBUILT=true: the image (pinned by
  #             TENSORFOLD_IMAGE_ID / _DIGEST) already holds TensorFold and its
  #             dependencies; nothing is installed at start. The offline route.
  #   lock      TENSORFOLD_PIP_LOCK=FILE: a fully resolved, hash-locked
  #             requirements file (pip install --require-hashes --no-deps),
  #             then TensorFold at TENSORFOLD_REF with --no-deps.
  #   unpinned  TENSORFOLD_ALLOW_UNPINNED_DEPS=true: the old behaviour, PyPI's
  #             latest of each dependency at every start. Acknowledged only.
  DEPS=""
  if sp_is_true "${TENSORFOLD_IMAGE_PREBUILT:-}"; then
    DEPS=prebuilt
  elif [[ -n "${TENSORFOLD_PIP_LOCK:-}" ]]; then
    DEPS=lock
    if [[ ! -f "$TENSORFOLD_PIP_LOCK" ]]; then
      sp_err "TENSORFOLD_PIP_LOCK='$TENSORFOLD_PIP_LOCK' is not a file"
    elif ! grep -q -- '--hash=sha256:' "$TENSORFOLD_PIP_LOCK"; then
      sp_err "TENSORFOLD_PIP_LOCK='$TENSORFOLD_PIP_LOCK' has no --hash=sha256: pins (pip-compile --generate-hashes, or pip hash)"
    fi
  elif sp_is_true "${TENSORFOLD_ALLOW_UNPINNED_DEPS:-}"; then
    DEPS=unpinned
    echo "Warning: TENSORFOLD_ALLOW_UNPINNED_DEPS=true — pip resolves TensorFold's dependencies from PyPI, unpinned, at every start." >&2
  elif [[ $PRINT -eq 1 ]]; then
    DEPS=unpinned
    echo "Warning: TensorFold's dependencies are not pinned — a real run refuses this (TENSORFOLD_IMAGE_PREBUILT, TENSORFOLD_PIP_LOCK, or TENSORFOLD_ALLOW_UNPINNED_DEPS=true)." >&2
  else
    sp_err "the container engine pip-installs TensorFold's dependencies at every start, and only TensorFold itself is pinned: set TENSORFOLD_IMAGE_PREBUILT=true (an image that already holds them, pinned by TENSORFOLD_IMAGE_ID — also the offline route), or TENSORFOLD_PIP_LOCK=FILE (hash-locked requirements), or acknowledge with TENSORFOLD_ALLOW_UNPINNED_DEPS=true"
  fi
fi
[[ "$TENSORFOLD_MASTER_PORT" =~ ^[0-9]+$ ]] || sp_err "TENSORFOLD_MASTER_PORT='$TENSORFOLD_MASTER_PORT' is not a port"
(( TP > 1 )) && sp_check_guarded_port "$TENSORFOLD_MASTER_PORT"
[[ "$SPARK_SERVE_PORT" =~ ^[0-9]+$ ]] || sp_err "SPARK_SERVE_PORT='$SPARK_SERVE_PORT' is not a port"
# Keyless: the container engine never takes a key; native only without one.
_keyless=0
{ [[ "$ENGINE" == container ]] || sp_is_true "${SPARK_NO_API_KEY:-}"; } && _keyless=1
[[ "$RANK" == 0 ]] && sp_check_bind "${SPARK_SERVE_HOST:-}" "$_keyless"

sp_profile_locate "$PROFILE" "${MODELS_DIR:-}"
MOUNTS=() DRAFTER=none
if [[ $SP_LOCATE_RC -ne 0 || -z "$SP_MODEL_DIR" ]]; then
  if [[ $SP_LOCATE_RC -eq 3 ]]; then
    sp_err "profile '$PF_PROFILE_NAME' is not fetched — TensorFold cannot pin a revision itself, so it only serves a verified local copy: model-fetch.sh --profile $PF_PROFILE_NAME (on every Spark that runs a rank)"
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
[[ "$ENGINE" == container ]] && sp_image "${TENSORFOLD_IMAGE:-}" "${TENSORFOLD_IMAGE_DIGEST:-}" "$PRINT" "${TENSORFOLD_IMAGE_ID:-}"

# ── Can this ENGINE run this family at TP ranks, and how are they placed?
# The checkpoint's own model_type decides (config.json, or text_config's).
MODEL_TYPE="" PLACE=flags
if [[ -n "$SP_MODEL_DIR" ]]; then
  MODEL_TYPE="$(python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c.get("model_type") or (c.get("text_config") or {}).get("model_type") or "")' "$SP_MODEL_DIR/config.json" 2>/dev/null || true)"
fi
if [[ -n "$SP_MODEL_DIR" ]] && (( TP > 1 )); then
  if [[ "$ENGINE" == native ]]; then
    # tensorfold-native 1.2.7 (its own messages and `capabilities --json`):
    # --tp takes 1, 2, 4 or 8; GLM-5.3-Flash is placed by TF_GLM_RANK/WORLD
    # (at most 16 ranks), MiniMax-M3.1-Flash by TF_MINIMAX_RANK/WORLD at
    # exactly 2, Nemotron by TF_NEMOTRON_RANK/WORLD; Flash Next runs on one
    # GPU or --tp 2. Re-check these against a newer TENSORFOLD_VERSION.
    case "$MODEL_TYPE" in
      glm5_next) PLACE=TF_GLM ;;
      minimax_m3_vl)
        PLACE=TF_MINIMAX
        [[ "$TP" == 2 ]] || sp_err "profile '$PF_PROFILE_NAME' is TENSOR_PARALLEL_SIZE=$TP, but native TensorFold serves MiniMax-M3.1-Flash at TP 2 only (TF_MINIMAX_WORLD=2)" ;;
      nemotron_h)
        sp_err "native TensorFold places Nemotron's ranks with TF_NEMOTRON_RANK / TF_NEMOTRON_WORLD, which this launcher does not set yet — serve it at TENSOR_PARALLEL_SIZE=1" ;;
      qwen4_exp)
        [[ "$TP" == 2 ]] || sp_err "profile '$PF_PROFILE_NAME' is TENSOR_PARALLEL_SIZE=$TP, but native TensorFold runs Flash Next (qwen4_exp) on 1 or 2 ranks only" ;;
      *)
        case "$TP" in
          2|4|8) ;;
          *) sp_err "profile '$PF_PROFILE_NAME' is TENSOR_PARALLEL_SIZE=$TP: native TensorFold's --tp takes 1, 2, 4 or 8 (tensorfold capabilities --json); only a family it places by environment (GLM-5.3-Flash) runs at $TP" ;;
        esac ;;
    esac
  else
    # The container engine is the pip-installed 0.x at TENSORFOLD_REF;
    # data/tensorfold.json lists each family's ranks as read from one commit.
    _tf="$(python3 - "$HERE/../data/tensorfold.json" "$MODEL_TYPE" <<'PY' 2>/dev/null || true
import json, sys
d = json.load(open(sys.argv[1]))
fam = next((k for k, f in (d.get("families") or {}).items() if sys.argv[2] in f.get("model_types", [])), "")
tps = (d.get("families") or {}).get(fam, {}).get("tp") or []
print((d.get("_provenance") or {}).get("commit", ""), fam, ",".join(str(x) for x in tps))
PY
)"
    read -r _tfc _tff _tfs <<< "$_tf"
    if [[ -n "${_tfs:-}" && ",$_tfs," != *",$TP,"* ]]; then
      _why="TensorFold @${_tfc:0:7} runs family $_tff on ${_tfs//,/ or } rank(s), not $TP (data/tensorfold.json)"
      if [[ "${TENSORFOLD_REF:-}" == "$_tfc" ]]; then
        sp_err "profile '$PF_PROFILE_NAME' is TENSOR_PARALLEL_SIZE=$TP: $_why"
      else
        echo "Warning: TENSORFOLD_REF is not ${_tfc:0:7}, the commit data/tensorfold.json was read from — $_why there; check yours." >&2
      fi
    fi
  fi
fi
# A per-family engine variable (TENSORFOLD_RANK_ENV) is read by one family only: refuse it for
# another checkpoint rather than set a variable the engine silently ignores.
for _e in ${PF_RANK_ENV[@]+"${PF_RANK_ENV[@]}"}; do
  _fam="${_e%%:*}" _kv="${_e#*:}"
  [[ "$ENGINE" == native && "$MODEL_TYPE" != "$_fam" ]] \
    && sp_err "profile '$PF_PROFILE_NAME' sets ${_kv%%=*} (TENSORFOLD_RANK_ENV), which only native TensorFold's $_fam family reads; this checkpoint is model_type ${MODEL_TYPE:-unknown}"
done
if [[ $SP_ERRORS -gt 0 ]]; then
  echo "Refusing to start: $SP_ERRORS setting(s) missing or unsafe (settings: $ENV_FILE, profile: $PF_PROFILE_PATH)." >&2
  exit 1
fi

if [[ "$ENGINE" == native ]]; then
  # PLACE (above, from the checkpoint's model_type): GLM and MiniMax read
  # their rank from the environment, every other ranked family from flags.
  SERVE=(serve "$SP_MODEL_DIR" --no-update-check)
  [[ -n "$PF_DRAFTER_SOURCE" ]] && SERVE+=(--drafter "$SP_DRAFTER_DIR")
  [[ -n "$PF_TENSORFOLD_PARALLEL" ]] && SERVE+=(--parallel "$PF_TENSORFOLD_PARALLEL")
  [[ -n "$PF_MAX_MODEL_LEN" ]] && SERVE+=(--context "$PF_MAX_MODEL_LEN")
  [[ -n "$PF_TENSORFOLD_KV_CACHE" ]] && SERVE+=(--kv-cache "$PF_TENSORFOLD_KV_CACHE")
  SERVE+=(${PF_EXTRA[@]+"${PF_EXTRA[@]}"})
  RANKENV=()
  [[ -n "$PF_TENSORFOLD_MEMORY_RESERVE_GIB" ]] && RANKENV+=("TENSORFOLD_MEMORY_RESERVE_GIB=$PF_TENSORFOLD_MEMORY_RESERVE_GIB")
  # TENSORFOLD_RANK_ENV (obprofile's allow list; checked against the model_type above)
  for _e in ${PF_RANK_ENV[@]+"${PF_RANK_ENV[@]}"}; do RANKENV+=("${_e#*:}"); done
  if (( TP > 1 )); then
    case "$PLACE" in
      TF_GLM|TF_MINIMAX)
        RANKENV+=("${PLACE}_RANK=$RANK" "${PLACE}_WORLD=$TP" "NCCL_COMM_ID=$MASTER:$TENSORFOLD_MASTER_PORT") ;;
      *)
        SERVE+=(--tp "$TP" --rank "$RANK" --master "$MASTER" --master-port "$TENSORFOLD_MASTER_PORT") ;;
    esac
    # SPARK_IFACE / SPARK_IB_HCA may list several ConnectX rails, comma-separated.
    RANKENV+=("NCCL_SOCKET_IFNAME=$SPARK_IFACE" "NCCL_IB_HCA=$SPARK_IB_HCA" "NCCL_IB_DISABLE=0")
    (( TP >= 3 )) && RANKENV+=(NCCL_CROSS_NIC=1)
  fi
  # Rank 0 alone serves HTTP. VERIFY ON HARDWARE: native GLM compares the
  # ranks' settings before loading; if it names --host/--port/--name as a
  # difference, give ranks 1..N-1 the same ones.
  if [[ "$RANK" == 0 ]]; then
    SERVE+=(--name "$PF_SERVED_MODEL_NAME" --host "$SPARK_SERVE_HOST" --port "$SPARK_SERVE_PORT")
    sp_is_true "${SPARK_NO_API_KEY:-}" || SERVE+=(--api-key-file "$TENSORFOLD_API_KEY_FILE")
  fi
  [[ -n "${NCCL_DEBUG:-}" ]] && RANKENV+=("NCCL_DEBUG=$NCCL_DEBUG")
  # A transient systemd --user unit: logs in the journal, one --stop, and it
  # outlives this shell (linger is set by spark-root-setup.sh).
  # --expand-environment=no (systemd >= 254; the Sparks run 255): otherwise
  # the service manager rewrites ${VAR}, $VAR and $$ in every argument at
  # exec time, and the unit would not run the argv --print shows.
  CMD=(systemd-run --user --unit "$NAME" --collect --property=Restart=no --expand-environment=no
    --setenv=HF_HUB_OFFLINE=1)
  for _e in ${RANKENV[@]+"${RANKENV[@]}"}; do CMD+=("--setenv=$_e"); done
  CMD+=("$TENSORFOLD_BIN" "${SERVE[@]}")
  if [[ $PRINT -eq 1 ]]; then
    echo "# profile $PF_PROFILE_NAME: $PF_SOURCE${PF_REVISION:+@$PF_REVISION} (TP $TP, model_type ${MODEL_TYPE:-?}) from $SP_MODEL_DIR"
    echo "# native TensorFold $TENSORFOLD_VERSION at $TENSORFOLD_BIN"
    [[ "$RANK" == 0 ]] && echo "# rank 0 serves http://${SPARK_SERVE_HOST}:${SPARK_SERVE_PORT}"
    sp_print_cmd "${CMD[@]}"
    exit 0
  fi
  if systemctl --user is-active --quiet "$NAME"; then
    echo "Error: $NAME is already running — $0 --rank $RANK --stop first" >&2
    exit 1
  fi
  if [[ "$RANK" == 0 && "$TP" == 2 ]]; then
    echo "Rank 0: start rank 1 on the other Spark too; both wait for each other on $MASTER:$TENSORFOLD_MASTER_PORT."
  elif [[ "$RANK" == 0 ]] && (( TP > 2 )); then
    echo "Rank 0: start ranks 1..$((TP - 1)) on the other Sparks too; all $TP wait for each other on $MASTER:$TENSORFOLD_MASTER_PORT."
  fi
  [[ "$RANK" != 0 ]] && echo "Rank $RANK: rendezvous with rank 0 at $MASTER:$TENSORFOLD_MASTER_PORT."
  "${CMD[@]}"
  echo "Started $NAME. Logs: journalctl --user -fu $NAME   Stop: $0 --rank $RANK --stop"
  exit 0
fi

SERVE=(serve "/models/$PF_PROFILE_NAME" --tp "$TP" --rank "$RANK" --no-update-check --drafter "$DRAFTER")
(( TP > 1 )) && SERVE+=(--master "$MASTER" --master-port "$TENSORFOLD_MASTER_PORT")
[[ -n "$PF_TENSORFOLD_PARALLEL" ]] && SERVE+=(--parallel "$PF_TENSORFOLD_PARALLEL")
[[ -n "$PF_MAX_MODEL_LEN" ]] && SERVE+=(--context "$PF_MAX_MODEL_LEN")
SERVE+=(${PF_EXTRA[@]+"${PF_EXTRA[@]}"})
if [[ "$RANK" == 0 ]]; then
  SERVE+=(--name "$PF_SERVED_MODEL_NAME" --host "$SPARK_SERVE_HOST" --port "$SPARK_SERVE_PORT")
fi
# The container installs the pinned commit (per DEPS, above), then execs the
# server. Arguments travel as positional parameters, never spliced into the
# script text; $1 is always the git URL, so every mode shifts it the same way.
# shellcheck disable=SC2016  # expanded inside the container, not here
case "$DEPS" in
  prebuilt) INNER='shift && exec tensorfold "$@"' ;;
  lock)     INNER='python -m pip install --quiet --require-hashes --no-deps -r /openbeast/tensorfold-requirements.txt && python -m pip install --quiet --no-deps "$1" && shift && exec tensorfold "$@"'
            MOUNTS+=(-v "$TENSORFOLD_PIP_LOCK:/openbeast/tensorfold-requirements.txt:ro") ;;
  *)        INNER='python -m pip install --quiet "$1" && shift && exec tensorfold "$@"' ;;
esac
CMD=(docker run -d --rm --name "$NAME"
  --gpus all --ipc=host --network host --ulimit memlock=-1 --cap-add IPC_LOCK)
if (( TP > 1 )); then
  IF="$SPARK_IFACE"
  CMD+=(--device /dev/infiniband
    -e "NCCL_SOCKET_IFNAME=$IF" -e "GLOO_SOCKET_IFNAME=$IF" -e "TP_SOCKET_IFNAME=$IF"
    -e "UCX_NET_DEVICES=$IF" -e "NCCL_IB_HCA=$SPARK_IB_HCA")
  (( TP >= 3 )) && CMD+=(-e NCCL_CROSS_NIC=1)
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
  [[ "$RANK" == 0 ]] && (( TP > 1 )) && echo "# start $(sp_workers "$TP") FIRST; rank 0 serves http://${SPARK_SERVE_HOST}:${SPARK_SERVE_PORT} (NO API key — gate or firewall it)"
  [[ "$RANK" == 0 && "$TP" == 1 ]] && echo "# serves http://${SPARK_SERVE_HOST}:${SPARK_SERVE_PORT} (NO API key — gate or firewall it)"
  sp_print_cmd "${CMD[@]}"
  exit 0
fi

if [[ "$RANK" == 0 ]]; then
  if [[ "$TP" == 2 ]]; then
    echo "Rank 0: make sure rank 1 is already running. Serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT when both are up."
  elif (( TP > 2 )); then
    echo "Rank 0: make sure ranks 1..$((TP - 1)) are already running. Serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT when all $TP are up."
  fi
  [[ "$TP" == 1 ]] && echo "Serving http://$SPARK_SERVE_HOST:$SPARK_SERVE_PORT (one Spark, TP 1)."
  echo "  TensorFold has NO API key — keep this port behind beast-gate or a firewall."
else
  echo "Rank $RANK: waiting for rank 0 at $MASTER:$TENSORFOLD_MASTER_PORT (start rank 0 next)."
fi
if [[ "$TP" == 2 ]]; then
  echo "  Rendezvous $TENSORFOLD_MASTER_PORT is unauthenticated: firewall it to the peer's link address."
elif (( TP > 2 )); then
  echo "  Rendezvous $TENSORFOLD_MASTER_PORT is unauthenticated: firewall it to the peers' link addresses."
fi
mkdir -p "$HOME/.cache/tensorfold"
"${CMD[@]}"
echo "Started $NAME. Logs: docker logs -f $NAME   Stop: $0 --rank $RANK --stop"
echo "The first start compiles kernels for this GPU (sm_121) and can take a while."
