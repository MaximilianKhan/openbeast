#!/bin/bash
# Inference-backend adapter shared by start.sh, scripts/healthcheck.sh,
# scripts/doctor.sh and the llama-only tools (docs/DGX_SPARK_PLAN.md).
# Sourced, never executed; defines functions only (no `set --`, no
# shell-option changes, no output at source time).
#
# The stack talks OpenAI-compatible HTTP to ONE inference server. Three
# servers are understood, and they disagree on exactly the things a
# supervisor needs to know:
#
#   llama       llama-server. Binds its port BEFORE loading and answers
#               /health 503 {"error":{"message":"Loading model"}} for the
#               whole load, then 200 {"status":"ok"}. Ready = the body.
#   vllm        vLLM's OpenAI server. The HTTP port opens only after the
#               engine is built; /health is 200 with an EMPTY body, 503
#               only on EngineDeadError (vllm/entrypoints/serve/
#               instrumentator/health.py). Ready = HTTP 200.
#   tensorfold  TensorFold. The CUDA server also binds only after the engine
#               is built; /health is 200 {"ok": true} (src/tensorfold/cuda/
#               server.py do_GET); the MLX server answers {"status":"ok",...}.
#               Ready = HTTP 200 with either body.
#
# Before this adapter every probe asked the llama question, so a healthy
# vLLM (empty 200) read "not ready" forever and start.sh killed and rolled
# back a server it did not even own.
#
# INFERENCE_BACKEND / INFERENCE_URL / INFERENCE_MANAGED / INFERENCE_SLOTS are
# resolved by lib/conf.sh; every function here falls back to the llama
# defaults when they are unset, so a caller that did not source conf.sh keeps
# today's behaviour.

# ob_llama_ready lives in net.sh; pull it in when the caller has not.
if ! declare -F ob_llama_ready >/dev/null 2>&1 \
   && [[ -f "$(dirname "${BASH_SOURCE[0]}")/net.sh" ]]; then
  # shellcheck source=net.sh
  source "$(dirname "${BASH_SOURCE[0]}")/net.sh"
fi

OB_BACKENDS="llama vllm tensorfold"

# ob_backend_normalize <raw> — the ONE parser for INFERENCE_BACKEND. Prints
# llama|vllm|tensorfold. First token only, a trailing `#comment` and quotes
# dropped, case-insensitive (the _ob_bool rules). Empty → llama. Anything
# else → llama with a warning naming the value: a typo must fall back to the
# path that works on this box, never to a mode that stops launching a model.
ob_backend_normalize() {
  local raw="${1:-}" tok rest
  read -r tok rest <<< "$raw" || true
  tok="${tok%%#*}"
  tok="${tok//[\"\']/}"
  tok="$(printf '%s' "$tok" | tr 'A-Z' 'a-z')"
  case "$tok" in
    "")                   printf 'llama\n' ;;
    llama|llama.cpp|llamacpp|llama-server) printf 'llama\n' ;;
    vllm)                 printf 'vllm\n' ;;
    tensorfold)           printf 'tensorfold\n' ;;
    *)
      echo "WARNING: INFERENCE_BACKEND='$raw' is not one of: $OB_BACKENDS — using llama." >&2
      printf 'llama\n' ;;
  esac
}

# ob_backend_name — the resolved backend, llama when conf.sh did not run.
ob_backend_name() { printf '%s\n' "${INFERENCE_BACKEND:-llama}"; }

# ob_backend_label [backend] — human name for status lines.
ob_backend_label() {
  case "${1:-${INFERENCE_BACKEND:-llama}}" in
    vllm)       printf 'vLLM\n' ;;
    tensorfold) printf 'TensorFold\n' ;;
    *)          printf 'llama.cpp server\n' ;;
  esac
}

# ob_inference_managed — true when THIS stack launches, supervises, restarts
# and kills the inference server. False means someone else owns it (a vLLM or
# TensorFold cluster on other boxes): report on it, never touch it.
ob_inference_managed() { [[ "${INFERENCE_MANAGED:-true}" == "true" ]]; }

# ob_backend_ready <base-url> [backend] — 0 only when the server can serve.
# llama keeps ob_llama_ready's exact semantics (200 + {"status":"ok"}; a 503
# "Loading model" is NOT ready). vllm: any HTTP 200 (the body is empty).
# tensorfold: HTTP 200 whose body says ok. /health needs no key on any of the
# three (vLLM's --api-key guards /v1 only), so none is sent.
ob_backend_ready() {
  local url="${1%/}" backend="${2:-${INFERENCE_BACKEND:-llama}}" body
  case "$backend" in
    vllm)
      curl -fsS -m 3 -o /dev/null "$url/health" 2>/dev/null ;;
    tensorfold)
      body="$(curl -fsS -m 3 "$url/health" 2>/dev/null)" || return 1
      [[ "$body" =~ \"ok\"[[:space:]]*:[[:space:]]*true \
         || "$body" =~ \"status\"[[:space:]]*:[[:space:]]*\"ok\" ]] ;;
    *)
      ob_llama_ready "$url" ;;
  esac
}

# ob_backend_models <base-url> — the served model ids, one per line (empty
# output = none or unreachable; exit 1 when the request failed). Presents
# LLAMA_API_KEY when set, through lib/curl_auth.sh (never argv) when that is
# loaded — vLLM with --api-key guards /v1/models.
ob_backend_models() {
  local url="${1%/}" body
  if declare -F ob_curl_bearer >/dev/null 2>&1; then
    body="$(ob_curl_bearer "${LLAMA_API_KEY:-}" -fsS -m 5 "$url/v1/models" 2>/dev/null)" || return 1
  else
    body="$(curl -fsS -m 5 "$url/v1/models" 2>/dev/null)" || return 1
  fi
  printf '%s' "$body" | python3 -c '
import json, sys
try:
    for m in json.load(sys.stdin).get("data", []):
        if isinstance(m, dict) and m.get("id"):
            print(m["id"])
except Exception:
    sys.exit(1)
'
}

# ob_backend_na <what> — the one-line "not applicable" notice llama-only
# steps print instead of failing on a non-llama (or unowned) backend.
ob_backend_na() {
  local why
  if [[ "${INFERENCE_BACKEND:-llama}" != "llama" ]]; then
    why="INFERENCE_BACKEND=${INFERENCE_BACKEND}"
  else
    why="INFERENCE_MANAGED=false"
  fi
  printf '%s: not applicable for %s (the inference server at %s is not run by this stack).\n' \
    "$1" "$why" "${INFERENCE_URL:-?}"
}

# ob_llama_only <what> — for standalone llama-only tools (measure-vram, the
# MTP profilers) that do not source conf.sh: resolve INFERENCE_BACKEND
# (env → openbeast.conf → llama) and return 1 after printing the notice when
# it is not llama. Env beats conf, so `INFERENCE_BACKEND=llama <tool>` still
# runs the tool on a rig whose conf points at a remote backend.
ob_llama_only() {
  local raw="${INFERENCE_BACKEND:-${OPENBEAST_INFERENCE_BACKEND:-}}" conf line
  if [[ -z "$raw" ]]; then
    conf="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/openbeast.conf"
    if [[ -f "$conf" ]]; then
      line="$(grep -E '^[[:space:]]*INFERENCE_BACKEND[[:space:]]*=' "$conf" 2>/dev/null | tail -n1 || true)"
      raw="${line#*=}"
    fi
  fi
  local b; b="$(ob_backend_normalize "$raw" 2>/dev/null)"
  [[ "$b" == "llama" ]] && return 0
  printf '%s: not applicable for INFERENCE_BACKEND=%s — it drives a local llama-server.\n' "$1" "$b"
  printf '  (to run it anyway on this box: INFERENCE_BACKEND=llama %s)\n' "$1"
  return 1
}
