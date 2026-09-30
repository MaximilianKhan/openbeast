#!/bin/bash
# beast-instinct's TARGET decision model: Open-Jev-27B-v1.1, run LOCALLY on a
# DEDICATED GPU host — a DGX Spark, or the rig's 5090 once the Sparks carry
# generation. Never TypeSafe's hosted Jev API: nothing here leaves the host.
#
#   scripts/serve-openjev.sh up   [--gpu N] [--bind ADDR] [--port P] [--dry-run]
#                                 [--base uncensored | --base stock --validation-only]
#   scripts/serve-openjev.sh down | status
#
# What `up` runs (docs/BEAST_INSTINCT.md "Open-Jev host"):
#   1. the Open-Jev loader (jev.server) in a PINNED container — OPENJEV_IMAGE
#      must be <repo>@sha256:<64 hex>, built from scripts/instinct/openjev/
#      (torch + peft live there; the rig's agents/requirements lock gets none).
#      Published on this host's LOOPBACK only; weights mounted READ-ONLY;
#      HF_HUB_OFFLINE=1 — nothing is downloaded at run time;
#   2. scripts/instinct/openjev_gate.py on --bind:--port (default
#      127.0.0.1:8791): bearer key from a 0600 file (never argv), a path
#      allowlist, and /v1/identity — the pins verified below.
# Before starting, it VERIFIES the checkpoint (adapter + head sha256 from the
# Open-Jev-27B-v1.1 card, rev 28cf7306…) and refuses:
#   * an unpinned image; a reserved stack port (8080 3000 3001 3003 3004 8082
#     8088 8090 8094 8095 8443 8444) or a held one; --bind 0.0.0.0;
#   * a GPU llama-server on this host (the model needs a GPU of its own; set
#     OPENJEV_ALLOW_SHARED_HOST=1 only on a host whose llama-server is on
#     another GPU — [HW] the 5090 has no room for both). A CPU-only one —
#     the rig's instinct 0.6B fallback scorer (INSTINCT_SCORER=true), which
#     runs with CUDA_VISIBLE_DEVICES="" — does not count; a process whose
#     environment cannot be read does (fail safe);
#   * --base stock without --validation-only (all our models are uncensored;
#     the stock base exists only to A/B the adapter), and stock off loopback.
# The base: the UNCENSORED Qwen3.8-27B safetensors (JonathanColetti/
# Qwen3.8-27B-Uncensored @5bb7aa90…) by default. The package pins the STOCK
# base in model.json; a derived checkpoint under $RUN_DIR/openjev/ rewrites
# only that file (the package stays byte-identical). The adapter was TRAINED
# on the stock base — it only shadows until evals/decisions re-validates it on
# the uncensored one (plan revision 2026-09-30).
#
# Env: OPENJEV_IMAGE (required), OPENJEV_CHECKPOINT_DIR (default
#      $WEIGHTS_DIR/Open-Jev-27B-v1.1/package/checkpoint),
#      OPENJEV_HF_CACHE (default $WEIGHTS_DIR/hf-cache; an HF hub cache
#      holding the base revision),
#      OPENJEV_KEY_FILE (default $RUN_DIR/openjev.key, minted 0600 — copy it
#      0600 to the rig as .run/openjev.key), OPENJEV_RUN_DIR (default .run),
#      OPENJEV_DOCKER (docker|podman), OPENJEV_CONTAINER (openbeast-openjev),
#      OPENJEV_BASE_REPO / OPENJEV_BASE_REVISION (the uncensored base),
#      OPENJEV_ADAPTER_SHA256 / OPENJEV_HEAD_SHA256 (a RE-TRAINED head, plan
#      step b — the binding's head_sha256 must then match it too).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/portown.sh
source "$SCRIPT_DIR/lib/portown.sh"
# shellcheck source=lib/weights.sh
source "$SCRIPT_DIR/lib/weights.sh"

PACKAGE_REPO="ZefanCai/Open-Jev-27B-v1.1"
PACKAGE_REVISION="28cf73067d5b337860bbef3c85b8b82ba8730956"
STOCK_REPO="Qwen/Qwen3.8-27B"
STOCK_REVISION="1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"
UNC_REPO="${OPENJEV_BASE_REPO:-JonathanColetti/Qwen3.8-27B-Uncensored}"
UNC_REVISION="${OPENJEV_BASE_REVISION:-5bb7aa90f0efef548e87005b1fb7658e522b6b7f}"
ADAPTER_SHA="${OPENJEV_ADAPTER_SHA256:-1c857224bd3609c6a71eacf7f71dd021115fcc0f791936b1fc332e915b548a81}"
HEAD_SHA="${OPENJEV_HEAD_SHA256:-76e382f122abfa4e0c467d860a8d142d2fb6d2a98dc0ef9e19870bfc6eb296b4}"
RESERVED_PORTS=" 8080 3000 3001 3003 3004 8082 8088 8090 8094 8095 8443 8444 "

RUN_DIR="${OPENJEV_RUN_DIR:-$REPO_DIR/.run}"
KEY_FILE="${OPENJEV_KEY_FILE:-$RUN_DIR/openjev.key}"
DOCKER="${OPENJEV_DOCKER:-docker}"
NAME="${OPENJEV_CONTAINER:-openbeast-openjev}"
GATE_PIDFILE="$RUN_DIR/openjev-gate.pid"
GATE_LOG="$RUN_DIR/openjev-gate.log"
PY="${PYTHON:-python3}"

die() { echo "serve-openjev: $*" >&2; exit 2; }

usage() { sed -n '2,/^set -euo pipefail/{/^#/p}' "$0"; }

sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

# A llama-server that holds (or may hold) a GPU. The instinct fallback scorer
# runs CPU-only with CUDA_VISIBLE_DEVICES="" in its environment and does not
# count; any other one — or one whose environment cannot be read — does.
gpu_llama_server_running() {
  local proc="${OPENJEV_PROC_ROOT:-/proc}" pid pids
  pids="$(pgrep -x llama-server 2>/dev/null || true)"
  for pid in $pids; do
    if ! tr '\0' '\n' < "$proc/$pid/environ" 2>/dev/null \
         | grep -qx 'CUDA_VISIBLE_DEVICES='; then
      return 0
    fi
  done
  return 1
}

free_port() {
  "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

cmd_up() {
  local gpu="0" bind="127.0.0.1" port="8791" dry=0 base="uncensored" validation=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --gpu) gpu="${2:?}"; shift 2 ;;
      --bind) bind="${2:?}"; shift 2 ;;
      --port) port="${2:?}"; shift 2 ;;
      --base) base="${2:?}"; shift 2 ;;
      --validation-only) validation=1; shift ;;
      --dry-run) dry=1; shift ;;
      *) die "unknown flag $1 (try --help)" ;;
    esac
  done
  [[ "$gpu" =~ ^[0-9]+$ ]] || die "--gpu wants a GPU index"
  [[ "$port" =~ ^[0-9]+$ ]] || die "--port wants a number"
  [[ "$RESERVED_PORTS" == *" $port "* ]] && die "port $port belongs to the stack — refusing"
  case "$bind" in 0.0.0.0|::|"[::]"|"") die "--bind $bind publishes to every interface; name one address" ;; esac
  case "$base" in
    uncensored) base_repo="$UNC_REPO"; base_rev="$UNC_REVISION" ;;
    stock)
      [[ $validation -eq 1 ]] || die "--base stock is for the A/B validation only: add --validation-only (all our served models are uncensored)"
      case "$bind" in 127.*|localhost|::1) ;; *) die "--base stock stays on loopback" ;; esac
      base_repo="$STOCK_REPO"; base_rev="$STOCK_REVISION" ;;
    *) die "--base uncensored|stock" ;;
  esac
  local image="${OPENJEV_IMAGE:-}"
  [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || die "OPENJEV_IMAGE must be pinned: <repo>@sha256:<64 hex> (got '${image:-unset}')"
  # Defaults live under WEIGHTS_DIR like every other launcher's weights, so
  # a relocated weights drive works here too.
  local ckpt="${OPENJEV_CHECKPOINT_DIR:-$WEIGHTS_DIR/Open-Jev-27B-v1.1/package/checkpoint}"
  local hf="${OPENJEV_HF_CACHE:-$WEIGHTS_DIR/hf-cache}"
  [[ -n "$ckpt" && -d "$ckpt" ]] || die "OPENJEV_CHECKPOINT_DIR is not a directory (the package's package/checkpoint)"
  [[ -n "$hf" && -d "$hf" ]] || die "OPENJEV_HF_CACHE is not a directory (an HF hub cache holding $base_repo@$base_rev)"
  local f
  for f in adapter/adapter_model.safetensors adapter/adapter_config.json head.pt model.json temperature.json; do
    [[ -f "$ckpt/$f" ]] || die "checkpoint is missing $f"
  done
  local got_adapter got_head
  got_adapter="$(sha256_of "$ckpt/adapter/adapter_model.safetensors")"
  got_head="$(sha256_of "$ckpt/head.pt")"
  [[ "$got_adapter" == "$ADAPTER_SHA" ]] || die "adapter sha256 $got_adapter != pinned $ADAPTER_SHA"
  [[ "$got_head" == "$HEAD_SHA" ]] || die "head sha256 $got_head != pinned $HEAD_SHA"
  if [[ "${OPENJEV_ALLOW_SHARED_HOST:-0}" != "1" ]] && gpu_llama_server_running; then
    die "a GPU llama-server runs on this host — Open-Jev-27B needs a GPU of its own (a Spark, or the 5090 once the Sparks serve generation)"
  fi
  if [[ $dry -eq 0 ]] && ob_port_listening "$port"; then
    die "port $port is already held — refusing (pre-bind check)"
  fi

  mkdir -p "$RUN_DIR/openjev"
  local derived="$RUN_DIR/openjev/checkpoint-$base"
  rm -rf "$derived"; mkdir -p "$derived"
  # The package stays byte-identical: the derived dir LINKS its files (by
  # their in-container path) and rewrites only model.json's base.
  ln -s /ckpt-src/adapter "$derived/adapter"
  ln -s /ckpt-src/head.pt "$derived/head.pt"
  ln -s /ckpt-src/temperature.json "$derived/temperature.json"
  "$PY" - "$ckpt/model.json" "$derived/model.json" "$base_repo" "$base_rev" <<'PYEOF'
import json, sys
src, dst, repo, rev = sys.argv[1:5]
m = json.load(open(src))
m["model_id"], m["revision"] = repo, rev
json.dump(m, open(dst, "w"), indent=2, sort_keys=True)
PYEOF
  local digest="${image##*@}"
  "$PY" - "$RUN_DIR/openjev/identity.json" "$base_repo" "$base_rev" "$PACKAGE_REVISION" \
    "$got_head" "$got_adapter" "$digest" "$base" <<'PYEOF'
import json, sys
out, repo, rev, pkg, head, adapter, digest, base = sys.argv[1:9]
json.dump({"model": repo, "base_revision": rev, "adapter_revision": pkg, "head_sha256": head,
           "adapter_sha256": adapter, "loader_digest": digest, "base": base,
           "validation_only": base == "stock"}, open(out, "w"), indent=2, sort_keys=True)
PYEOF

  if [[ ! -f "$KEY_FILE" ]]; then
    ( umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$KEY_FILE" )
  fi
  [[ "$(stat -c '%a' "$KEY_FILE")" == "600" ]] || die "$KEY_FILE must be 0600"

  local inner
  inner="$(free_port)"
  local -a run=("$DOCKER" run -d --rm --name "$NAME" --gpus "device=$gpu"
    -p "127.0.0.1:$inner:8791" --read-only --tmpfs /tmp
    -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HF_HUB_CACHE=/hf -e HF_HUB_DISABLE_TELEMETRY=1
    -v "$hf:/hf:ro" -v "$ckpt:/ckpt-src:ro" -v "$derived:/ckpt:ro"
    "$image" python -m jev.server --checkpoint /ckpt --device cuda:0 --max-length 4096
    --batch-size 1 --no-prefix-cache --host 0.0.0.0 --port 8791)
  local -a gate=("$PY" "$SCRIPT_DIR/instinct/openjev_gate.py" --listen "$bind" --port "$port"
    --upstream "http://127.0.0.1:$inner" --key-file "$KEY_FILE"
    --identity "$RUN_DIR/openjev/identity.json")
  if [[ $dry -eq 1 ]]; then
    echo "base: $base_repo@$base_rev ($base)"
    echo "container: ${run[*]}"
    echo "gate: ${gate[*]}"
    return 0
  fi
  "${run[@]}" >/dev/null
  echo "serve-openjev: container $NAME started (loader on 127.0.0.1:$inner, GPU $gpu)"
  ( umask 077; : >> "$GATE_LOG" )
  nohup "${gate[@]}" >>"$GATE_LOG" 2>&1 < /dev/null &
  echo "$!" > "$GATE_PIDFILE"
  echo "serve-openjev: gate on $bind:$port (pid $(cat "$GATE_PIDFILE")); the loader may take minutes to load 54 GB"
}

cmd_down() {
  if [[ -f "$GATE_PIDFILE" ]]; then
    local pid
    pid="$(cat "$GATE_PIDFILE")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qF openjev_gate.py; then
      kill "$pid" 2>/dev/null || true
      echo "serve-openjev: gate stopped (pid $pid)"
    fi
    rm -f "$GATE_PIDFILE"
  fi
  "$DOCKER" stop "$NAME" >/dev/null 2>&1 && echo "serve-openjev: container $NAME stopped" || true
}

cmd_status() {
  local pid=""
  [[ -f "$GATE_PIDFILE" ]] && pid="$(cat "$GATE_PIDFILE")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    echo "gate: running (pid $pid)"
  else
    echo "gate: not running"
  fi
  "$DOCKER" ps --filter "name=^${NAME}$" --format '{{.Names}} {{.Status}}' 2>/dev/null || true
  [[ -f "$RUN_DIR/openjev/identity.json" ]] && cat "$RUN_DIR/openjev/identity.json"
  return 0
}

case "${1:-}" in
  up) shift; cmd_up "$@" ;;
  down) cmd_down ;;
  status) cmd_status ;;
  ""|-h|--help|help) usage ;;
  *) die "unknown command '$1' (try --help)" ;;
esac
