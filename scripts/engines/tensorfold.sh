#!/bin/bash
# Engine adapter: TensorFold, the signed native (>= 1.0, Zig) release.
# docs/ENGINES.md, contract in docs/ENGINES.md "Adapter contract". Sourced by
# lib/engine.sh.
#
# Install / verify is scripts/backends/tensorfold/install-native.sh (archive
# vs .sha256, .sha256 vs the release's ed25519 signature with the vendored
# key, every unpacked file vs SHA256SUMS). This adapter always STAGES into
# $OB_ENGINE_STATE_DIR/cache/ first and installs from there, so the archive
# sha256 is known for the pin and the verified folder is kept for an offline
# reinstall or for carrying to the next box.
#
# Versions sit side by side in ~/.local/opt/tensorfold-<V>-<platform>;
# ~/.local/bin/tensorfold links the active one. A rollback relinks (after
# re-checking SHA256SUMS) — instant and offline.
#
# Release facts (github.com/ashhart/TensorFold, read 2026-11-15): latest
# v1.2.9 (2026-11-15); linux-x86_64 and linux-aarch64 archives; CUDA fatbins
# for sm_80, sm_86, sm_120 (RTX 50 / RTX PRO Blackwell) and sm_121 (GB10),
# no sm_90; NVIDIA driver R580+; serves MLX-affine 4/6/8-bit safetensors,
# NVFP4 (ModelOpt), EXL3 (GLM-5.3-Flash) — not GGUF on CUDA.
#
# Two modes, as for vLLM: unmanaged (the Sparks' spark-node.sh runs it as a
# systemd --user unit; this adapter keeps spark.env's TENSORFOLD_VERSION in
# step) and managed single host (engine-serve.sh execs `tensorfold serve`, so
# the server IS start.sh's child and the usual pid checks apply).

eng_tensorfold_label()   { printf 'TensorFold\n'; }
eng_tensorfold_hosts()   { printf 'spark rig mac\n'; }
eng_tensorfold_formats() { printf 'MLX-affine 4/6/8-bit, NVFP4, EXL3 (no GGUF on CUDA)\n'; }
eng_tensorfold_hydra_kind() { printf 'tensorfold\n'; }

_eng_tf_installer() { printf '%s\n' "${OB_ENGINE_TF_INSTALLER:-$REPO_DIR/scripts/backends/tensorfold/install-native.sh}"; }
_eng_tf_home() { printf '%s\n' "${OB_ENGINE_TF_HOME:-$HOME}"; }
_eng_tf_bin() { printf '%s/.local/bin/tensorfold\n' "$(_eng_tf_home)"; }
_eng_tf_platform() {
  if [[ -n "${OB_ENGINE_TF_PLATFORM:-}" ]]; then printf '%s\n' "$OB_ENGINE_TF_PLATFORM"; return; fi
  case "$(uname -s)-$(ob_engine_arch)" in
    Linux-aarch64) printf 'linux-aarch64\n' ;;
    Linux-x86_64)  printf 'linux-x86_64\n' ;;
    Darwin-aarch64) printf 'macos-arm64\n' ;;
    *) printf 'unsupported\n' ;;
  esac
}
_eng_tf_ver() { local v="$1"; printf '%s\n' "${v#v}"; }

eng_tensorfold_installed() {
  local b v; b="$(_eng_tf_bin)"
  [[ -x "$b" ]] || return 1
  v="$("$b" --version 2>/dev/null | awk '{print $NF}')"
  [[ -n "$v" ]] || return 1
  printf '%s\n' "${v#v}"
}

eng_tensorfold_latest() {
  local t; t="$(ob_engine_github_latest ashhart/TensorFold)" || return 1
  printf '%s -\n' "$(_eng_tf_ver "$t")"
}

# _eng_tf_archive_sha <dir> <version> <platform> — the sha256 in the signed
# .sha256 file (first field).
_eng_tf_archive_sha() {
  awk '{print $1; exit}' "$1/tensorfold-$2-$3.tar.gz.sha256" 2>/dev/null
}

# install <version> <from> — stage (verify) into the cache, or take a staged
# DIR, then install-native.sh --from. Sets ENG_RESULT_*.
eng_tensorfold_install() {
  local v="$1" from="$2" plat dir sha
  plat="$(_eng_tf_platform)"
  [[ "$plat" != unsupported ]] || { echo "no TensorFold release for $(uname -s)-$(uname -m)" >&2; return 1; }
  if [[ -n "$from" && -z "$v" ]]; then
    v="$(ob_engine_manifest_get "$from/MANIFEST" VERSION)"
    [[ -n "$v" ]] || v="$(ls "$from" 2>/dev/null | sed -nE "s/^tensorfold-([0-9][0-9.]*)-$plat\.tar\.gz$/\1/p" | sort -V | tail -n1)"
  fi
  if [[ -z "$v" || "$v" == latest ]]; then
    v="$(eng_tensorfold_latest)" || return 1; v="${v%% *}"
  fi
  v="$(_eng_tf_ver "$v")"
  if [[ -n "$from" ]]; then
    dir="$(cd "$from" && pwd)"
  else
    ob_engine_offline && { echo "OFFLINE=true — no download; stage TensorFold $v on a connected box (engine.sh stage tensorfold --version $v DIR) and install --from DIR" >&2; return 1; }
    dir="$OB_ENGINE_STATE_DIR/cache/tensorfold-$v-$plat"
    rm -rf "$dir"
    HOME="$(_eng_tf_home)" "$(_eng_tf_installer)" --version "$v" --platform "$plat" --stage "$dir" || return 1
  fi
  HOME="$(_eng_tf_home)" "$(_eng_tf_installer)" --version "$v" --platform "$plat" --from "$dir" || return 1
  sha="$(_eng_tf_archive_sha "$dir" "$v" "$plat")"
  [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || { echo "no sha256 for tensorfold-$v-$plat in $dir" >&2; return 1; }
  ENG_RESULT_VERSION="$v"; ENG_RESULT_REF="sha256:$sha"; ENG_RESULT_NOTE="platform=$plat"
}

# activate <version> <ref> — relink an install still on disk, after
# re-checking its SHA256SUMS. 1 when it is gone (engine.sh then reinstalls).
eng_tensorfold_activate() {
  local v; v="$(_eng_tf_ver "$1")"
  local plat opt d have
  plat="$(_eng_tf_platform)"; opt="$(_eng_tf_home)/.local/opt"
  d="$opt/tensorfold-$v-$plat"
  [[ -d "$d" && -f "$d/SHA256SUMS" ]] || return 1
  if command -v sha256sum >/dev/null 2>&1; then (cd "$d" && sha256sum -c --quiet SHA256SUMS) || return 1
  else (cd "$d" && shasum -a 256 -c --quiet SHA256SUMS) || return 1; fi
  ln -sfn "$d" "$opt/tensorfold"
  ln -sfn "$opt/tensorfold/bin/tensorfold-native" "$(_eng_tf_bin)"
  have="$(eng_tensorfold_installed)"
  [[ "$have" == "$v" ]] || { echo "after relinking, tensorfold --version says '${have:-?}', not $v" >&2; return 1; }
  ENG_RESULT_VERSION="$v"; ENG_RESULT_REF="$2"; ENG_RESULT_NOTE="platform=$plat relinked"
}

# stage <version> <dir> — download + verify into DIR (install-native.sh
# --stage) plus a MANIFEST. ENG_STAGE_PLATFORM stages another box's archive.
eng_tensorfold_stage() {
  local v="$1" dir="$2" plat="${ENG_STAGE_PLATFORM:-$(_eng_tf_platform)}"
  if [[ -z "$v" || "$v" == latest ]]; then v="$(eng_tensorfold_latest)" || return 1; v="${v%% *}"; fi
  v="$(_eng_tf_ver "$v")"
  "$(_eng_tf_installer)" --version "$v" --platform "$plat" --stage "$dir" || return 1
  printf 'ENGINE=tensorfold\nVERSION=%s\nPLATFORM=%s\nARCHIVE_SHA256=%s\n' \
    "$v" "$plat" "$(_eng_tf_archive_sha "$dir" "$v" "$plat")" > "$dir/MANIFEST"
}

eng_tensorfold_spark_env() { printf 'TENSORFOLD_VERSION=%s\n' "$1"; }

# ── managed, single host ───────────────────────────────────────────────────
eng_tensorfold_profile_check() {
  ob_engine_profile_load "$1" tensorfold || return 1
  if [[ "$PF_TENSOR_PARALLEL_SIZE" != 1 ]]; then
    echo "profile '$1' is TENSOR_PARALLEL_SIZE=$PF_TENSOR_PARALLEL_SIZE — managed mode is one host, --tp 1. Several Sparks: scripts/backends/spark-cluster.sh, then engine.sh use tensorfold --unmanaged --url http://<rank0>:8000 (127.0.0.1:8000 when rank 0 is this box)" >&2
    return 1
  fi
}

# launch — exec `tensorfold serve` (foreground; the process start.sh
# supervises IS the server). The key, when LLAMA_API_KEY is set, goes to a
# 0600 file under .run/ for --api-key-file (never argv).
eng_tensorfold_launch() {
  local profile b host port keyf=""
  profile="${OPENBEAST_ENGINE_PROFILE:-$(ob_engine_conf_get ENGINE_PROFILE)}"
  [[ -n "$profile" ]] || { echo "ENGINE_PROFILE is not set — engine.sh use tensorfold --profile NAME" >&2; return 1; }
  eng_tensorfold_profile_check "$profile" || return 1
  sp_profile_locate "$profile" "$(ob_engine_models_dir)"
  if [[ $SP_LOCATE_RC -ne 0 || -z "$SP_MODEL_DIR" ]]; then
    echo "profile '$profile' is not fetched and verified in $(ob_engine_models_dir) — TensorFold cannot pin a revision itself: MODELS_DIR=$(ob_engine_models_dir) scripts/backends/model-fetch.sh --profile $profile" >&2
    [[ "${ENG_PRINT:-0}" == 1 ]] || return 1
    SP_MODEL_DIR="${SP_MODEL_DIR:-$(ob_engine_models_dir)/$PF_PROFILE_NAME}"
  fi
  b="$(_eng_tf_bin)"
  [[ -x "$b" || "${ENG_PRINT:-0}" == 1 ]] || { echo "no TensorFold at $b — engine.sh install tensorfold" >&2; return 1; }
  read -r host port < <(ob_engine_url_hostport "${INFERENCE_URL:-http://127.0.0.1:8080}")
  local -a CMD=("$b" serve "$SP_MODEL_DIR" --no-update-check
    --name "$PF_SERVED_MODEL_NAME" --host "$host" --port "$port")
  # No --drafter without a profile drafter: tensorfold-native (1.2.7, checked
  # on spark-1) rejects `--drafter none` before loading anything ("none is
  # neither a directory nor a Hugging Face repo id"); `none` is the frozen
  # container engine's spelling only. An MTP family then drafts with its own
  # head, exactly as the cluster launcher (spark-node.sh) serves it.
  [[ -n "$PF_DRAFTER_SOURCE" ]] && CMD+=(--drafter "$SP_DRAFTER_DIR")
  [[ -n "$PF_TENSORFOLD_PARALLEL" ]] && CMD+=(--parallel "$PF_TENSORFOLD_PARALLEL")
  [[ -n "$PF_MAX_MODEL_LEN" ]] && CMD+=(--context "$PF_MAX_MODEL_LEN")
  if [[ -n "${LLAMA_API_KEY:-}" ]]; then
    keyf="${RUN_DIR:-$REPO_DIR/.run}/tensorfold-api-key"
    CMD+=(--api-key-file "$keyf")
  fi
  CMD+=(${PF_EXTRA[@]+"${PF_EXTRA[@]}"})
  if [[ "${ENG_PRINT:-0}" == 1 ]]; then
    sp_print_cmd "${CMD[@]}"
    return 0
  fi
  if [[ -n "$keyf" ]]; then
    ( umask 077; mkdir -p "$(dirname "$keyf")"; printf '%s\n' "$LLAMA_API_KEY" > "$keyf.tmp"; mv -f "$keyf.tmp" "$keyf" )
  fi
  echo "TensorFold $(eng_tensorfold_installed 2>/dev/null || echo '?'): $PF_PROFILE_NAME ($PF_SERVED_MODEL_NAME) on http://$host:$port"
  HF_HUB_OFFLINE=1 exec "${CMD[@]}"
}

# stop — the recorded managed server of THIS checkout, by pid + name.
eng_tensorfold_stop() {
  local pf="${RUN_DIR:-$REPO_DIR/.run}/llama.pid" pid
  pid="$(cat "$pf" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  if ps -o args= -p "$pid" 2>/dev/null | grep -qE '(^|/)tensorfold(-native)?( |$)|engine-serve\.sh'; then
    kill "$pid" 2>/dev/null && echo "TensorFold server stopped (pid $pid)."
  fi
  return 0
}
