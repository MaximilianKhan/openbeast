#!/bin/bash
# Engine adapter: vLLM (OpenAI server in a container). docs/ENGINES.md,
# contract in docs/ENGINES.md "Adapter contract". Sourced by lib/engine.sh.
#
# Two modes, never mixed:
#   unmanaged  (INFERENCE_MANAGED=false, the default) a vLLM someone else
#              runs — typically ranks across the Sparks, launched with
#              scripts/backends/vllm/spark-node.sh. This adapter only pins
#              the image those launchers use (spark.env VLLM_IMAGE/_DIGEST/_ID).
#   managed    (INFERENCE_MANAGED=true, one host, TP 1) start.sh launches
#              scripts/engine-serve.sh, which runs `eng_vllm_launch` below in
#              the FOREGROUND: one container, named per checkout, stopped by
#              the supervisor's trap, stop.sh, or a TERM to the launcher.
#
# Image: vllm/vllm-openai (ENGINE_VLLM_IMAGE). Release tags vX.Y.Z are
# multi-arch indexes (amd64 + arm64; v0.33.0 also as -x86_64 / -aarch64),
# CUDA 13 by default since v0.33.0 dropped cu129 (Docker Hub, read
# 2026-11-15). The pin is the INDEX digest the pull resolved, so a re-pushed
# tag can never reach this box.
#
# Pin: ENGINE_PIN_VLLM=<tag>@sha256:<digest> for a pull; the local image id
# rides in the history note (id=sha256:…), which is what a `docker load`ed
# image is found by when the registry digest did not survive save/load. An
# offline install (--from DIR) pins <tag>@<image id> instead: the only
# identity this box verified itself (eng_vllm_install).

eng_vllm_label()   { printf 'vLLM\n'; }
eng_vllm_hosts()   { printf 'rig spark\n'; }
eng_vllm_formats() { printf 'safetensors: BF16/FP8/NVFP4/AWQ/GPTQ (GGUF: plugin, experimental)\n'; }
eng_vllm_hydra_kind() { printf 'vllm\n'; }

_eng_vllm_repo() {
  local r="${OPENBEAST_ENGINE_VLLM_IMAGE:-$(ob_engine_conf_get ENGINE_VLLM_IMAGE)}"
  printf '%s\n' "${r:-vllm/vllm-openai}"
}
_eng_vllm_cname() { ob_engine_container_name vllm; }
_eng_vllm_docker() { "${OB_ENGINE_DOCKER:-docker}" "$@"; }

# _eng_vllm_tag <version> — the image tag for a release (v prefix added).
_eng_vllm_tag() { local v="$1"; [[ "$v" == v* ]] || v="v$v"; printf '%s\n' "$v"; }

# _eng_vllm_id_for <ref> — the image id recorded for a digest (history).
_eng_vllm_id_for() {
  ob_engine_history vllm | awk -F'\t' -v r="$1" '$5 == r && $6 ~ /id=sha256:/ {
    n = split($6, a, " "); for (i = 1; i <= n; i++) if (a[i] ~ /^id=/) id = substr(a[i], 4) }
    END { if (id != "") print id }'
}

# _eng_vllm_local_ref <digest> — something `docker run` resolves locally for
# that pin: repo@digest, else the recorded image id. 1 when neither exists.
_eng_vllm_local_ref() {
  local d="$1" repo id
  repo="$(_eng_vllm_repo)"
  if _eng_vllm_docker image inspect "$repo@$d" >/dev/null 2>&1; then printf '%s@%s\n' "$repo" "$d"; return 0; fi
  id="$(_eng_vllm_id_for "$d")"
  if [[ -n "$id" ]] && _eng_vllm_docker image inspect "$id" >/dev/null 2>&1; then printf '%s\n' "$id"; return 0; fi
  return 1
}

eng_vllm_installed() {
  local p; p="$(ob_engine_conf_get ENGINE_PIN_VLLM)"
  [[ -n "$p" ]] || return 1
  command -v "${OB_ENGINE_DOCKER:-docker}" >/dev/null 2>&1 || return 1
  _eng_vllm_local_ref "${p#*@}" >/dev/null || return 1
  printf '%s\n' "${p%%@*}"
}

eng_vllm_latest() {
  local t; t="$(ob_engine_github_latest vllm-project/vllm)" || return 1
  printf '%s -\n' "$(_eng_vllm_tag "$t")"
}

# _eng_vllm_pull <tag> <digest|""> — pull, then ENG_RESULT_REF = the
# repository digest, ENG_RESULT_NOTE = "id=<image id>".
_eng_vllm_pull() {
  local tag="$1" want="$2" repo ref got id
  repo="$(_eng_vllm_repo)"
  ref="$repo:$tag"; [[ -n "$want" ]] && ref="$repo@$want"
  ob_engine_offline && { echo "OFFLINE=true — cannot pull $ref; stage it on a connected box (engine.sh stage vllm ...) and install --from DIR" >&2; return 1; }
  _eng_vllm_docker pull "$ref" || { echo "docker pull $ref failed" >&2; return 1; }
  got="$(_eng_vllm_docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$ref" 2>/dev/null \
         | awk -v r="$repo@" 'index($0, r) == 1 {print substr($0, length(r) + 1); exit}')"
  [[ "$got" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "could not read the registry digest of $ref" >&2; return 1; }
  if [[ -n "$want" && "$got" != "$want" ]]; then
    echo "digest mismatch for $repo: asked $want, got $got — refusing" >&2; return 1
  fi
  id="$(_eng_vllm_docker image inspect --format '{{.Id}}' "$ref")"
  [[ -n "$want" ]] && _eng_vllm_docker tag "$ref" "$repo:$tag" >/dev/null 2>&1 || true
  ENG_RESULT_REF="$got"; ENG_RESULT_NOTE="id=$id"
}

# install <version> <from> <digest> — online: pull (by digest when given);
# offline: docker load a staged tarball whose sha256 and image id match
# its MANIFEST. Sets ENG_RESULT_VERSION / ENG_RESULT_REF / ENG_RESULT_NOTE.
#
# What an offline install can and cannot prove: the MANIFEST travels with
# the tarball, so its TAR_SHA256 and IMAGE_ID only show the folder is
# self-consistent, and its DIGEST (the registry index digest the staging box
# pulled) cannot be recomputed from a `docker save` tarball at all. The one
# thing this box establishes itself is the IMAGE ID: content-addressed, read
# back from docker after the load. So that is the pin
# (ENGINE_PIN_VLLM=<tag>@<image id>), and the staged registry digest goes in
# the history note as `staged-digest=… (unverified)` — never into the pin,
# where it would claim a provenance nothing here checked.
eng_vllm_install() {
  local v="$1" from="$2" want="${3:-}" tag m tar tarn sum id dig
  if [[ -n "$from" ]]; then
    m="$from/MANIFEST"
    [[ "$(ob_engine_manifest_get "$m" ENGINE)" == vllm ]] || { echo "$m is not a vLLM stage (engine.sh stage vllm ...)" >&2; return 1; }
    tag="$(ob_engine_manifest_get "$m" VERSION)"
    tarn="$(ob_engine_manifest_get "$m" TAR)"
    sum="$(ob_engine_manifest_get "$m" TAR_SHA256)"
    id="$(ob_engine_manifest_get "$m" IMAGE_ID)"
    dig="$(ob_engine_manifest_get "$m" DIGEST)"
    [[ -n "$tarn" && -n "$sum" && -n "$id" && -n "$dig" ]] || { echo "$m is incomplete (TAR, TAR_SHA256, IMAGE_ID, DIGEST)" >&2; return 1; }
    # Parsed, never trusted: each field has one shape, and anything else (a
    # path, a line break) is refused before it names a file or a pin.
    [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]{0,24}$ ]] || { echo "$m: VERSION '$tag' is not a vLLM release tag" >&2; return 1; }
    [[ "$tarn" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\.tar$ ]] || { echo "$m: TAR '$tarn' is not a file name in $from" >&2; return 1; }
    [[ "$sum" =~ ^[0-9a-f]{64}$ ]] || { echo "$m: TAR_SHA256 is not 64 hex" >&2; return 1; }
    [[ "$id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "$m: IMAGE_ID is not sha256:<64 hex>" >&2; return 1; }
    [[ "$dig" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "$m: DIGEST is not sha256:<64 hex>" >&2; return 1; }
    [[ -z "$v" || "$(_eng_vllm_tag "$v")" == "$tag" ]] || { echo "$from holds vLLM $tag, not $v" >&2; return 1; }
    tar="$from/$tarn"
    [[ -f "$tar" ]] || { echo "$m names $tarn, which is not in $from" >&2; return 1; }
    [[ -z "$want" || "$want" == "$dig" ]] || { echo "$from holds $dig, not the --digest $want" >&2; return 1; }
    [[ "$(ob_engine_sha256 "$tar")" == "$sum" ]] || { echo "checksum mismatch: $tar" >&2; return 1; }
    _eng_vllm_docker load -i "$tar" >/dev/null || { echo "docker load $tar failed" >&2; return 1; }
    [[ "$(_eng_vllm_docker image inspect --format '{{.Id}}' "$id" 2>/dev/null)" == "$id" ]] \
      || { echo "after docker load, image $id is not there — the tarball is not what MANIFEST says" >&2; return 1; }
    echo "verified: $tar (sha256) → image ${id:7:12}"
    echo "  pinned by its image id; the staged registry digest ${dig:7:12} is recorded as unverified (a docker save tarball cannot prove it)."
    ENG_RESULT_VERSION="$tag"; ENG_RESULT_REF="$id"; ENG_RESULT_NOTE="id=$id staged-digest=$dig (unverified)"
    return 0
  fi
  if [[ -z "$v" || "$v" == latest ]]; then
    tag="$(eng_vllm_latest)" || return 1; tag="${tag%% *}"
  else
    tag="$(_eng_vllm_tag "$v")"
  fi
  _eng_vllm_pull "$tag" "$want" || return 1
  ENG_RESULT_VERSION="$tag"
  echo "pulled $(_eng_vllm_repo):$tag @ $ENG_RESULT_REF"
}

# activate <version> <ref> — rollback to an image still on this box.
eng_vllm_activate() {
  local ref="$2"
  _eng_vllm_local_ref "$ref" >/dev/null || return 1
  ENG_RESULT_VERSION="$1"; ENG_RESULT_REF="$ref"
  ENG_RESULT_NOTE="id=$(_eng_vllm_id_for "$ref")"
}

# stage <version> <dir> — pull + docker save + MANIFEST, for install --from.
eng_vllm_stage() {
  local v="$1" dir="$2" tag t
  if [[ -z "$v" || "$v" == latest ]]; then tag="$(eng_vllm_latest)" || return 1; tag="${tag%% *}"
  else tag="$(_eng_vllm_tag "$v")"; fi
  _eng_vllm_pull "$tag" "${ENG_STAGE_DIGEST:-}" || return 1
  mkdir -p "$dir"
  t="vllm-openai-$tag-$(ob_engine_arch).tar"
  _eng_vllm_docker save -o "$dir/$t" "$(_eng_vllm_repo):$tag" || return 1
  printf 'ENGINE=vllm\nVERSION=%s\nIMAGE=%s\nDIGEST=%s\nIMAGE_ID=%s\nTAR=%s\nTAR_SHA256=%s\nARCH=%s\n' \
    "$tag" "$(_eng_vllm_repo)" "$ENG_RESULT_REF" "${ENG_RESULT_NOTE#id=}" "$t" "$(ob_engine_sha256 "$dir/$t")" "$(ob_engine_arch)" \
    > "$dir/MANIFEST"
  echo "staged vLLM $tag ($(ob_engine_arch)) in $dir — on the closed box: scripts/engine.sh install vllm --from $dir"
}

# spark_env <version> <ref> — the KEY=VALUE pairs the Spark launchers read.
# A pulled pin is a registry digest; a staged (--from) one is the image id
# this box read back from docker (eng_vllm_install). VLLM_IMAGE_ID is always
# written, because sp_image runs the ID whenever one is set ("the ID wins")
# and offline Sparks pin it (sync-image, H4): left alone, an old ID kept the
# ranks on the old image while the pin, history and `engine.sh status`
# named the new one.
#   - staged pin (the ref IS an image id): VLLM_IMAGE_ID=<ref>, the digest
#     cleared, so a stale digest can never stand next to it;
#   - pulled pin: VLLM_IMAGE_DIGEST=<ref> plus that same image's ID when this
#     install / activate knows it (ENG_RESULT_NOTE id=..., else the
#     history), and an empty ID otherwise, so the digest applies.
eng_vllm_spark_env() {
  local id=""
  case " ${ENG_RESULT_NOTE:-} " in *" id=sha256:"*) id="${ENG_RESULT_NOTE#*id=}"; id="${id%% *}" ;; esac
  [[ "$id" =~ ^sha256:[0-9a-f]{64}$ ]] || id="$(_eng_vllm_id_for "$2" 2>/dev/null || true)"
  [[ "$id" =~ ^sha256:[0-9a-f]{64}$ ]] || id=""
  if [[ -n "$2" && "$id" == "$2" ]]; then
    printf 'VLLM_IMAGE=%s:%s\nVLLM_IMAGE_ID=%s\nVLLM_IMAGE_DIGEST=\n' "$(_eng_vllm_repo)" "$1" "$2"
  else
    printf 'VLLM_IMAGE=%s:%s\nVLLM_IMAGE_DIGEST=%s\nVLLM_IMAGE_ID=%s\n' "$(_eng_vllm_repo)" "$1" "$2" "$id"
  fi
}

# ── managed, single host ───────────────────────────────────────────────────
# _eng_vllm_eager — --enforce-eager? ENGINE_VLLM_EAGER=true|false|auto.
# auto: on a GB10 (host class spark) for the sparse-MLA families whose
# CUDA-graph capture poisons the null KV block with NaN on SM120/121
# (vllm-project/vllm#57156, OPEN 2026-11-15: DeepSeek-V4.1; GLM-5.x has the
# same capture-time writer). Everything else keeps CUDA graphs.
_eng_vllm_eager() {
  local mode="${OPENBEAST_ENGINE_VLLM_EAGER:-$(ob_engine_conf_get ENGINE_VLLM_EAGER)}" mt="$1"
  case "${mode:-auto}" in
    true)  return 0 ;;
    false) return 1 ;;
  esac
  [[ "$(ob_engine_host_class)" == spark ]] || return 1
  case "$mt" in glm5*|deepseek_v4*|deepseek_v32*) return 0 ;; esac
  return 1
}

eng_vllm_profile_check() { # <profile> — usable for a managed single-host run
  ob_engine_profile_load "$1" vllm || return 1
  if [[ "$PF_TENSOR_PARALLEL_SIZE" != 1 ]]; then
    echo "profile '$1' is TENSOR_PARALLEL_SIZE=$PF_TENSOR_PARALLEL_SIZE — managed mode is one host, TP 1. Several Sparks: scripts/backends/spark-cluster.sh, then engine.sh use vllm --unmanaged --url http://<rank0>:8000 (127.0.0.1:8000 when rank 0 is this box)" >&2
    return 1
  fi
}

# launch — the managed server, in the foreground. ENG_PRINT=1: print the
# command and return. Reads INFERENCE_URL (bind host:port), ENGINE_PROFILE,
# LLAMA_API_KEY (→ VLLM_API_KEY, by name, never argv).
eng_vllm_launch() {
  local profile pin ref cname host port mt rc=0 child
  profile="${OPENBEAST_ENGINE_PROFILE:-$(ob_engine_conf_get ENGINE_PROFILE)}"
  [[ -n "$profile" ]] || { echo "ENGINE_PROFILE is not set — engine.sh use vllm --profile NAME" >&2; return 1; }
  eng_vllm_profile_check "$profile" || return 1
  sp_profile_locate "$profile" "$(ob_engine_models_dir)"
  if [[ $SP_LOCATE_RC -ne 0 || -z "$SP_MODEL_DIR" ]]; then
    echo "profile '$profile' is not fetched and verified in $(ob_engine_models_dir) — managed mode serves only a verified local copy: MODELS_DIR=$(ob_engine_models_dir) scripts/backends/model-fetch.sh --profile $profile" >&2
    [[ "${ENG_PRINT:-0}" == 1 ]] || return 1
    SP_MODEL_DIR="${SP_MODEL_DIR:-$(ob_engine_models_dir)/$PF_PROFILE_NAME}"
  fi
  pin="$(ob_engine_conf_get ENGINE_PIN_VLLM)"
  [[ -n "$pin" ]] || { echo "no vLLM pin — engine.sh install vllm" >&2; [[ "${ENG_PRINT:-0}" == 1 ]] || return 1; }
  ref="$(_eng_vllm_local_ref "${pin#*@}" 2>/dev/null)" || {
    echo "the pinned vLLM image ($pin) is not on this box — engine.sh install vllm --version ${pin%%@*}" >&2
    [[ "${ENG_PRINT:-0}" == 1 ]] || return 1
    if [[ -n "$pin" ]]; then ref="$(_eng_vllm_repo)@${pin#*@}"; else ref="$(_eng_vllm_repo):NOT-INSTALLED"; fi
  }
  read -r host port < <(ob_engine_url_hostport "${INFERENCE_URL:-http://127.0.0.1:8080}")
  cname="$(_eng_vllm_cname)"
  mt="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("model_type",""))' "$SP_MODEL_DIR/config.json" 2>/dev/null || true)"
  local -a CMD=(docker run --rm --name "$cname" --label "openbeast.engine=vllm" --label "openbeast.repo=$REPO_DIR"
    --gpus all --ipc=host --network host
    --ulimit memlock=-1 --ulimit nofile=1048576:1048576
    -e VLLM_SKIP_MODEL_NAME_VALIDATION=1 -e HF_HUB_OFFLINE=1)
  [[ -n "${LLAMA_API_KEY:-}" ]] && CMD+=(-e VLLM_API_KEY)
  CMD+=(-v "$SP_MODEL_DIR:/models/$PF_PROFILE_NAME:ro")
  [[ -n "$PF_CHAT_TEMPLATE" ]] && CMD+=(-v "$PF_CHAT_TEMPLATE:/openbeast/chat_template.jinja:ro")
  CMD+=(--entrypoint vllm "$ref" serve "/models/$PF_PROFILE_NAME"
    --host "$host" --port "$port" --served-model-name "$PF_SERVED_MODEL_NAME"
    --tensor-parallel-size 1 --gpu-memory-utilization "${PF_GPU_MEMORY_UTILIZATION:-0.80}")
  [[ -n "$PF_MAX_MODEL_LEN" ]] && CMD+=(--max-model-len "$PF_MAX_MODEL_LEN")
  [[ -n "$PF_DTYPE" ]] && CMD+=(--dtype "$PF_DTYPE")
  [[ -n "$PF_QUANTIZATION" ]] && CMD+=(--quantization "$PF_QUANTIZATION")
  [[ -n "$PF_MAX_NUM_SEQS" ]] && CMD+=(--max-num-seqs "$PF_MAX_NUM_SEQS")
  [[ -n "$PF_SPECULATIVE_CONFIG" ]] && CMD+=(--speculative-config "$PF_SPECULATIVE_CONFIG")
  [[ "$PF_TRUST_REMOTE_CODE" == true ]] && CMD+=(--trust-remote-code)
  [[ -n "$PF_CHAT_TEMPLATE" ]] && CMD+=(--chat-template /openbeast/chat_template.jinja)
  [[ -n "$PF_REASONING_PARSER" ]] && CMD+=(--reasoning-parser "$PF_REASONING_PARSER")
  [[ -n "$PF_TOOL_CALL_PARSER" ]] && CMD+=(--enable-auto-tool-choice --tool-call-parser "$PF_TOOL_CALL_PARSER")
  _eng_vllm_eager "$mt" && CMD+=(--enforce-eager)
  CMD+=(${PF_EXTRA[@]+"${PF_EXTRA[@]}"})
  CMD[0]="${OB_ENGINE_DOCKER:-docker}"
  if [[ "${ENG_PRINT:-0}" == 1 ]]; then
    sp_print_cmd "${CMD[@]}"
    return 0
  fi
  # A container of THIS checkout left by a SIGKILLed launcher holds the
  # port and the GPU: it is ours by name, so it goes.
  _eng_vllm_docker rm -f "$cname" >/dev/null 2>&1 || true
  [[ -n "${LLAMA_API_KEY:-}" ]] && export VLLM_API_KEY="$LLAMA_API_KEY"
  echo "vLLM ${pin%%@*}: $PF_PROFILE_NAME ($PF_SERVED_MODEL_NAME) on http://$host:$port — container $cname"
  "${CMD[@]}" &
  child=$!
  # docker run (no -t) proxies signals to the container, but a stop must not
  # depend on that: stop it by name, then reap the client.
  trap '_eng_vllm_docker stop -t 30 "'"$cname"'" >/dev/null 2>&1 || true; wait '"$child"' 2>/dev/null; exit 143' TERM INT
  wait "$child" || rc=$?
  return "$rc"
}

# owns_port <pid> <port> — ours: the launcher is alive and its container runs.
eng_vllm_owns_port() {
  kill -0 "$1" 2>/dev/null || return 1
  [[ "$(_eng_vllm_docker inspect -f '{{.State.Running}}' "$(_eng_vllm_cname)" 2>/dev/null)" == true ]]
}

# stop — the managed container of THIS checkout (stop.sh). Idempotent.
eng_vllm_stop() {
  local c; c="$(_eng_vllm_cname)"
  if _eng_vllm_docker inspect "$c" >/dev/null 2>&1; then
    _eng_vllm_docker stop -t 30 "$c" >/dev/null 2>&1 && echo "vLLM container $c stopped."
  fi
  return 0
}
