#!/bin/bash
# OpenBeast engine plane — choose, install, update and roll back the
# inference engine on this host. docs/ENGINES.md.
#
#   scripts/engine.sh list                      engines for this host class, installed / pinned / active
#   scripts/engine.sh status                    the active engine: version, endpoint readiness, model served
#   scripts/engine.sh use <engine|default> [--profile NAME] [--serve-script X]
#                         [--url URL] [--unmanaged] [--port N] [--dry-run] [--no-restart] [--force]
#                                               switch INFERENCE_BACKEND et al. in openbeast.conf;
#                                               a running stack is stopped first and started after.
#                                               --unmanaged: someone else runs the server at --url,
#                                               even on this box's loopback (rank 0 of a Spark pair)
#   scripts/engine.sh install <engine> [--version V] [--digest sha256:…] [--from DIR]
#                                               vllm --digest: the registry digest for a pull; with
#                                               --from, the IMAGE ID, checked after docker load
#   scripts/engine.sh update  <engine> [--from DIR]   install the latest release (pinned, verified)
#   scripts/engine.sh stage   <engine> [--version V] [--digest D] [--platform P] DIR
#                                               download + verify for a closed box (install --from DIR there)
#   scripts/engine.sh rollback <engine> [--dry-run]   back to the previous pin (offline when still on disk)
#   scripts/engine.sh history [engine]          every install / update / rollback / use
#
# Engines: llama (llama.cpp), vllm, tensorfold, maya (opt-in, gated on a
# measured 1.5x over llama.cpp: scripts/engine-gate.sh), basalt (Inkling 2
# Mini on an RTX 5090) — one adapter each in scripts/engines/; a new adapter
# file is a new engine. Defaults per host class: rig → basalt on an RTX 5090
# (profile inkling2-mini-stock), llama on any other rig GPU (llama is the
# 5090's fallback); spark (GB10) → tensorfold; ENGINE_DEFAULT overrides.
# Every install has an offline path (stage on a connected box, --from DIR on
# the closed one). Nothing here touches a server this stack does not manage.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$REPO_DIR/.run"
usage() { awk 'NR > 1 && !/^#/ {exit} NR > 1 {sub(/^# ?/, ""); print}' "$0"; }

CMD="${1:-}"; [[ $# -gt 0 ]] && shift
case "$CMD" in
  ""|-h|--help|help) usage; exit 0 ;;
  list|status|use|install|update|stage|rollback|history) ;;
  *) echo "Unknown command '$CMD' (see --help)" >&2; exit 2 ;;
esac

ENGINE="" VERSION="" DIGEST="" FROM="" PROFILE="" SERVE="" URL="" PORT="" PLATFORM=""
DRY=0 NORESTART=0 FORCE=0 UNMANAGED=0 DIR="" SPARK_ENV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)      VERSION="${2:?--version needs a value}"; shift 2 ;;
    --digest)       DIGEST="${2:?--digest needs a value}"; shift 2 ;;
    --from)         FROM="${2:?--from needs a directory}"; shift 2 ;;
    --profile)      PROFILE="${2:?--profile needs a name}"; shift 2 ;;
    --serve-script) SERVE="${2:?--serve-script needs a name}"; shift 2 ;;
    --url)          URL="${2:?--url needs a URL}"; shift 2 ;;
    --port)         PORT="${2:?--port needs a number}"; shift 2 ;;
    --platform)     PLATFORM="${2:?--platform needs a value}"; shift 2 ;;
    --spark-env)    SPARK_ENV="${2:?--spark-env needs a file}"; shift 2 ;;
    --dry-run|-n)   DRY=1; shift ;;
    --no-restart)   NORESTART=1; shift ;;
    --unmanaged)    UNMANAGED=1; shift ;;
    --force)        FORCE=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    -*)             echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    *)
      if [[ -z "$ENGINE" && "$CMD" != list && "$CMD" != status ]]; then ENGINE="$1"
      elif [[ "$CMD" == stage && -z "$DIR" ]]; then DIR="$1"
      else echo "Unexpected argument: $1 (see --help)" >&2; exit 2; fi
      shift ;;
  esac
done

# The environment this command was started with, by name: the restart in
# `use` hands start.sh exactly that (run_entry_env), never what conf.sh
# exported below from the conf as it was BEFORE the switch. Inherited, the
# old OPENBEAST_INFERENCE_BACKEND=llama made start.sh relaunch the old llama
# SERVE_SCRIPT under a basalt conf (rig, 2026-11-25). conf.sh no longer
# reads its own exports back as overrides either; this is the second lock,
# and it also keeps OB_CONF_READONLY (set just below) out of start.sh.
_OB_ENTRY_ENV=" $(compgen -e | tr '\n' ' ') "
export OB_CONF_READONLY=1
# shellcheck source=lib/conf.sh
source "$SCRIPT_DIR/lib/conf.sh"
# shellcheck source=lib/engine.sh
source "$SCRIPT_DIR/lib/engine.sh"
# shellcheck source=lib/proc.sh
source "$SCRIPT_DIR/lib/proc.sh"
[[ -f "$SCRIPT_DIR/lib/curl_auth.sh" ]] && source "$SCRIPT_DIR/lib/curl_auth.sh"
# One GPU detection per command, not one per question (history needs none).
[[ "$CMD" == history ]] || ob_engine_host_class_pin

die() { echo "Error: $*" >&2; exit 1; }
need_engine() {
  [[ -n "$ENGINE" ]] || die "which engine? one of: $(ob_engines)"
  [[ "$ENGINE" == default ]] && ENGINE="$(ob_engine_default)"
  [[ "$ENGINE" == llama.cpp || "$ENGINE" == llamacpp ]] && ENGINE=llama
  ob_engine_valid "$ENGINE" || die "no engine '$ENGINE' (have: $(ob_engines); adapters live in scripts/engines/)"
  ob_engine_load "$ENGINE"
}
runs_here() { # runs_here <engine> <class>
  local h; h=" $(ob_engine_call "$1" hosts 2>/dev/null || true) "
  [[ "$h" == *" $2 "* ]]
}
stack_running() { ob_recorded_pid_ours "$RUN_DIR/supervisor.pid" 'start\.sh' 2>/dev/null; }
stop_cmd()  { printf '%s\n' "${OB_ENGINE_STOP_CMD:-$REPO_DIR/stop.sh}"; }
# run_entry_env CMD... — CMD with the caller's environment: every variable
# exported since this script began (conf.sh's OPENBEAST_*, INFERENCE_*,
# OB_CONF_*) is dropped; what the caller exported (a real override, the
# systemd user session start.sh's memory-capped scope needs) stays.
run_entry_env() (
  local v
  for v in $(compgen -e); do
    [[ "$_OB_ENTRY_ENV" == *" $v "* ]] || unset "$v" 2>/dev/null || true
  done
  exec "$@"
)
start_cmd() { printf '%s\n' "${OB_ENGINE_START_CMD:-$REPO_DIR/start.sh -d}"; }

# spark_env_sync <engine> <version> <ref> — keep the Spark launchers'
# spark.env in step with the pin (only on a Spark, or with --spark-env).
spark_env_sync() {
  local e="$1" f kv
  f="${SPARK_ENV:-}"
  if [[ -z "$f" ]]; then
    [[ "$(ob_engine_host_class)" == spark ]] || return 0
    f="$REPO_DIR/scripts/backends/spark.env"
  fi
  [[ -f "$f" ]] || return 0
  ob_engine_has "$e" spark_env || return 0
  while IFS= read -r kv; do
    [[ -n "$kv" ]] || continue
    ob_engine_file_set "$f" "${kv%%=*}" "${kv#*=}" || die "could not write ${kv%%=*} to $f (above)"
    echo "  $f: ${kv%%=*}=${kv#*=}"
  done < <(ob_engine_call "$e" spark_env "$2" "$3")
}

# adopt_current <engine> — before install / update / use changes what runs,
# record the build this box has NOW when the history's latest entry for the
# engine is not it, so `engine.sh rollback` can always return to it. The rig
# (2026-11-17) ran a llama-server built by hand from a branch, outside this
# script: the history never heard of it, and rollback had nothing to go to.
# What is current: the adapter's `current` (llama: the commit the built
# binary reports), else the engine's pin. Never fatal: an unrecordable
# build is said, and the install goes on.
adopt_current() {
  local e="$1" cur="" v="" r="" last
  ob_engine_load "$e" 2>/dev/null || return 0
  if ob_engine_has "$e" current; then
    cur="$(ob_engine_call "$e" current || true)"
  else
    cur="$(ob_engine_pin "$e" 2>/dev/null || true)"
    [[ "$cur" == *@* ]] && cur="${cur%%@*}"$'\t'"${cur#*@}"
  fi
  IFS=$'\t' read -r v r _ <<< "$cur"
  [[ -n "$v" && -n "$r" ]] || return 0
  ob_engine_pin_valid "$v" "$r" 2>/dev/null || return 0
  last="$(ob_engine_history "$e" | awk -F'\t' '$3 ~ /^(install|update|rollback|adopt)$/ { r = $5 } END { print r }')"
  [[ "$last" == "$r" ]] && return 0
  ob_engine_history_add "$e" adopt "$v" "$r" "the build found running, not installed by engine.sh"
  echo "recorded the $e build this box has now ($v @ ${r:0:12}) in the history: engine.sh rollback $e returns to it"
}

# ── list ───────────────────────────────────────────────────────────────────
cmd_list() {
  local cls def act e inst pin here mark
  cls="$(ob_engine_host_class)"; def="$(ob_engine_default "$cls")"; act="$INFERENCE_BACKEND"
  echo "Host class: $cls ($(ob_engine_arch)) — OpenBeast default here: $def"
  printf '%-11s %-24s %-28s %-5s %-16s %s\n' ENGINE INSTALLED PIN HERE ACTIVE "WEIGHTS"
  for e in $(ob_engines); do
    ob_engine_load "$e" || continue
    inst="$(ob_engine_call "$e" installed 2>/dev/null || true)"
    pin="$(ob_engine_pin "$e" 2>/dev/null || true)"
    [[ -n "$pin" ]] && pin="${pin%%@*}@${pin#*@}" && [[ ${#pin} -gt 28 ]] && pin="${pin:0:25}..."
    here=no; runs_here "$e" "$cls" && here=yes
    mark=""
    if [[ "$e" == "$act" ]]; then
      if [[ "$INFERENCE_MANAGED" == true ]]; then mark="managed"; else mark="remote"; fi
    fi
    [[ "$e" == "$def" ]] && mark="${mark:+$mark,}default"
    printf '%-11s %-24s %-28s %-5s %-16s %s\n' "$e" "${inst:--}" "${pin:--}" "$here" "${mark:--}" \
      "$(ob_engine_call "$e" formats 2>/dev/null || true)"
  done
}

# ── status ─────────────────────────────────────────────────────────────────
cmd_status() {
  local e="$INFERENCE_BACKEND" inst pin models ready="NOT ready" prof
  ob_engine_load "$e" 2>/dev/null || true
  inst="$(ob_engine_call "$e" installed 2>/dev/null || true)"
  pin="$(ob_engine_pin "$e" 2>/dev/null || true)"
  prof="$(ob_engine_conf_get ENGINE_PROFILE)"
  echo "engine:    $e ($(ob_backend_label "$e")) — $([[ "$INFERENCE_MANAGED" == true ]] && echo "managed by this stack" || echo "not managed here (someone else runs it)")"
  echo "host:      $(ob_engine_host_class) ($(ob_engine_arch)); default here: $(ob_engine_default)"
  if [[ "$INFERENCE_MANAGED" == true ]]; then
    echo "installed: ${inst:-NOT INSTALLED (scripts/engine.sh install $e)}"
  fi
  echo "pin:       ${pin:-none}"
  if [[ "$e" == llama && "$INFERENCE_MANAGED" == true ]]; then
    echo "launcher:  scripts/${DEFAULT_SERVE_SCRIPT} (SERVE_SCRIPT)"
  elif [[ -n "$prof" && "$INFERENCE_MANAGED" == true ]]; then
    echo "profile:   $prof (ENGINE_PROFILE)"
  fi
  ob_backend_ready "$INFERENCE_URL" "$e" && ready=ready
  echo "endpoint:  $INFERENCE_URL — $ready"
  if [[ "$ready" == ready ]]; then
    models="$(ob_backend_models "$INFERENCE_URL" 2>/dev/null | head -3 | paste -sd, - || true)"
    echo "serving:   ${models:-? (/v1/models unreadable — LLAMA_API_KEY?)}"
  fi
  [[ -n "${INFERENCE_MODEL:-}" && "$e" != llama ]] && echo "model id:  $INFERENCE_MODEL (INFERENCE_MODEL — what the agent runner sends)"
  [[ "$ready" == ready ]]
}

# ── use ────────────────────────────────────────────────────────────────────
cmd_use() {
  need_engine
  local cls managed=1 port url sets=() unsets=() k running=0 slots="" served="" _c=()
  cls="$(ob_engine_host_class)"
  if [[ -n "$URL" ]]; then
    url="${URL%/}"; url="${url%/v1}"
    [[ "$url" =~ ^https?://[^/[:space:]]+$ ]] || die "--url '$URL' is not an http(s)://host[:port] base URL"
    ob_url_is_local "$url" "$OPENBEAST_PROBE_HOST" "$BIND_HOST" || managed=0
  fi
  # A loopback URL is not proof this stack runs the server: rank 0 of a Spark
  # pair serves on the head's 127.0.0.1 and is launched by spark-cluster.sh.
  if [[ $UNMANAGED -eq 1 ]]; then
    [[ -n "$URL" ]] || die "--unmanaged needs --url (where the server someone else runs answers)"
    managed=0
  fi
  runs_here "$ENGINE" "$cls" || [[ $managed -eq 0 ]] || [[ $FORCE -eq 1 ]] \
    || die "$ENGINE does not run on a '$cls' host (it runs on: $(ob_engine_call "$ENGINE" hosts)) — point at one elsewhere with --url, or --force"

  if [[ "$ENGINE" == llama ]]; then
    if [[ -n "$SERVE" ]]; then
      [[ -x "$REPO_DIR/scripts/$SERVE" ]] || die "scripts/$SERVE is not an executable serve script (ls scripts/serve-*.sh)"
    fi
    sets+=("INFERENCE_BACKEND=llama")
    if [[ $managed -eq 1 ]]; then
      # Back to the default rig, byte for byte: the keys the other engines
      # needed go, and conf.sh resolves llama on :8080, managed.
      unsets+=(INFERENCE_URL INFERENCE_MANAGED INFERENCE_MODEL INFERENCE_SLOTS ENGINE_PROFILE)
      [[ -n "$SERVE" ]] && sets+=("SERVE_SCRIPT=$SERVE")
    else
      sets+=("INFERENCE_URL=$url" "INFERENCE_MANAGED=false")
      unsets+=(ENGINE_PROFILE INFERENCE_MODEL)
    fi
  else
    [[ -z "$SERVE" ]] || die "--serve-script is a llama.cpp launcher; $ENGINE takes --profile NAME"
    PROFILE="${PROFILE:-$(ob_engine_conf_get ENGINE_PROFILE)}"
    # An adapter may name the profile it ships by default (basalt: the RTX
    # 5090's inkling2-mini-stock), so `use default` is one command.
    if [[ -z "$PROFILE" ]] && ob_engine_has "$ENGINE" default_profile; then
      PROFILE="$(ob_engine_call "$ENGINE" default_profile)"
    fi
    if [[ $managed -eq 1 ]]; then
      ob_engine_can_manage "$ENGINE" || die "the $ENGINE adapter has no single-host launcher — run it elsewhere and pass --url"
      [[ -n "$PROFILE" ]] || die "managed $ENGINE needs a model profile: --profile NAME (scripts/backends/models/, TENSOR_PARALLEL_SIZE=1) — or, for a server this stack does not launch (rank 0 of a Spark pair on this box), --unmanaged --url URL"
      ob_engine_call "$ENGINE" profile_check "$PROFILE" || die "profile '$PROFILE' cannot run managed here (above)"
      if [[ -z "$(ob_engine_call "$ENGINE" installed 2>/dev/null || true)" ]]; then
        [[ $FORCE -eq 1 ]] || die "$ENGINE is not installed here — scripts/engine.sh install $ENGINE (or --force)"
        echo "WARNING: $ENGINE is not installed — start.sh will fail to launch it until it is." >&2
      fi
      # An adapter with its own profile format (maya: GGUF weights from
      # weights.registry) says where its weights are; the rest are
      # scripts/backends/models/ profiles fetched by model-fetch.sh.
      if ob_engine_has "$ENGINE" profile_locate; then
        ob_engine_call "$ENGINE" profile_locate "$PROFILE"
        if [[ $SP_LOCATE_RC -ne 0 ]]; then
          [[ $FORCE -eq 1 ]] || die "profile '$PROFILE': its weights are not all on this box — scripts/fetch-weight.sh each shard (docs/WEIGHT_STREAMING.md), or --force"
          echo "WARNING: profile '$PROFILE': weights missing — start.sh will fail to serve it." >&2
        fi
      else
        sp_profile_locate "$PROFILE" "$(ob_engine_models_dir)" 2>/dev/null
        if [[ $SP_LOCATE_RC -ne 0 ]]; then
          [[ $FORCE -eq 1 ]] || die "profile '$PROFILE' is not fetched + verified in $(ob_engine_models_dir) — MODELS_DIR=$(ob_engine_models_dir) scripts/backends/model-fetch.sh --profile $PROFILE (or --force)"
          echo "WARNING: profile '$PROFILE' is not fetched — start.sh will refuse to serve it." >&2
        fi
      fi
      port="${PORT:-8080}"
      if [[ -n "$URL" ]]; then url="${URL%/}"; else url="http://127.0.0.1:$port"; fi
      if ob_engine_keyless_open "$url"; then
        echo "WARNING: $url is not loopback and LLAMA_API_KEY is empty: scripts/engine-serve.sh will REFUSE to launch $ENGINE keyless there (exit 3)." >&2
        echo "         Use --url http://127.0.0.1:$port, set LLAMA_API_KEY, or ALLOW_OPEN_INFERENCE=true in openbeast.conf to accept it." >&2
      fi
      sets+=("INFERENCE_BACKEND=$ENGINE" "INFERENCE_URL=$url" "INFERENCE_MANAGED=true" "ENGINE_PROFILE=$PROFILE")
    else
      sets+=("INFERENCE_BACKEND=$ENGINE" "INFERENCE_URL=$url" "INFERENCE_MANAGED=false")
      unsets+=(ENGINE_PROFILE)
      if [[ -n "$PROFILE" ]] && ob_engine_has "$ENGINE" profile_locate; then
        ob_engine_call "$ENGINE" profile_check "$PROFILE" || die "profile '$PROFILE' is invalid for $ENGINE (above)"
      elif [[ -n "$PROFILE" ]]; then
        ob_engine_profile_load "$PROFILE" "$ENGINE" || die "profile '$PROFILE' is invalid for $ENGINE (above)"
      fi
    fi
    if [[ -n "$PROFILE" ]]; then
      served="$PF_SERVED_MODEL_NAME"
      if [[ "$ENGINE" == vllm ]]; then slots="${PF_MAX_NUM_SEQS:-}"; else slots="${PF_TENSORFOLD_PARALLEL:-${PF_PARALLEL:-}}"; fi
      [[ "$slots" =~ ^[1-9][0-9]*$ ]] || slots=""
      [[ -n "$served" ]] && sets+=("INFERENCE_MODEL=\"$served\"")
      if [[ -n "$slots" ]]; then sets+=("INFERENCE_SLOTS=$slots"); else unsets+=(INFERENCE_SLOTS); fi
    fi
  fi
  stack_running && running=1

  echo "engine.sh use $ENGINE — $([[ $managed -eq 1 ]] && echo "managed on this box" || echo "at $url, not managed here")"
  [[ $running -eq 1 && $NORESTART -eq 0 ]] && echo "  1. stop the running stack:   $(stop_cmd)"
  echo "  $([[ $running -eq 1 && $NORESTART -eq 0 ]] && echo 2 || echo 1). openbeast.conf:"
  for k in ${sets[@]+"${sets[@]}"}; do echo "       set   $k"; done
  for k in ${unsets[@]+"${unsets[@]}"}; do
    [[ -n "$(ob_engine_conf_get "$k")" ]] && echo "       unset $k   (was $(ob_engine_conf_get "$k"))"
  done
  if [[ $running -eq 1 && $NORESTART -eq 0 ]]; then echo "  3. start it again:          $(start_cmd)"
  elif [[ $running -eq 1 ]]; then echo "  (--no-restart: the running stack keeps the OLD engine until ./stop.sh && ./start.sh -d)"
  fi
  if [[ $managed -eq 1 && "$ENGINE" != llama ]]; then
    echo "  launch command (scripts/engine-serve.sh --print):"
    # shellcheck disable=SC2034  # read by the adapter's launch
    ( INFERENCE_URL="$url"; OPENBEAST_ENGINE_PROFILE="$PROFILE"; ENG_PRINT=1 \
        ob_engine_call "$ENGINE" launch 2>/dev/null | sed 's/^/       /' ) || true
  fi
  if [[ $DRY -eq 1 ]]; then echo "(dry run — nothing changed)"; return 0; fi

  # What runs now, and what is about to, both get a rollback target.
  if [[ "$INFERENCE_MANAGED" == true && "$INFERENCE_BACKEND" != "$ENGINE" ]]; then
    adopt_current "$INFERENCE_BACKEND"
  fi
  adopt_current "$ENGINE"
  if [[ $running -eq 1 && $NORESTART -eq 0 ]]; then
    read -r -a _c <<< "$(stop_cmd)"
    OPENBEAST_STOP_REASON="scripts/engine.sh use $ENGINE" "${_c[@]}" || die "stop failed — openbeast.conf NOT changed"
  fi
  for k in ${sets[@]+"${sets[@]}"}; do ob_engine_conf_set "${k%%=*}" "${k#*=}"; done
  for k in ${unsets[@]+"${unsets[@]}"}; do ob_engine_conf_unset "$k"; done
  ob_engine_history_add "$ENGINE" use "$([[ $managed -eq 1 ]] && echo managed || echo remote)" "${url:-local}" "${PROFILE:-${SERVE:-}}"
  echo "openbeast.conf updated${_OB_ENGINE_CONF_BACKED_UP:+ (backup: $_OB_ENGINE_CONF_BACKED_UP)}."
  if [[ "$ENGINE" != llama && -n "$PROFILE" ]]; then
    echo "  Prove the model before trusting it: scripts/backends/conformance.sh --url $url --model '$served'"
    if [[ -n "${PF_AGENT_PROFILE:-}" && "$(ob_engine_conf_get AGENT_PROFILE)" != "$PF_AGENT_PROFILE" ]]; then
      echo "  Its measured agent setup is AGENT_PROFILE=$PF_AGENT_PROFILE (openbeast.conf now: $(ob_engine_conf_get AGENT_PROFILE || true)); set that line by hand (docs/MODELS.md)."
    fi
  fi
  if [[ $running -eq 1 && $NORESTART -eq 0 ]]; then
    read -r -a _c <<< "$(start_cmd)"
    run_entry_env "${_c[@]}"
  fi
}

# ── install / update / stage / rollback ────────────────────────────────────
_record() { # _record <action>
  # ob_engine_pin_set refuses a version or ref that is not a pin's shape
  # (lib/engine.sh ob_engine_pin_valid): they are written to openbeast.conf.
  ob_engine_pin_set "$ENGINE" "$ENG_RESULT_VERSION" "$ENG_RESULT_REF" "$1" "${ENG_RESULT_NOTE:-}${FROM:+ from=$FROM}" \
    || die "$ENGINE: the install produced a pin openbeast.conf must not hold (above) — the pin did not move"
  echo "pinned $ENGINE $ENG_RESULT_VERSION @ $ENG_RESULT_REF"
  spark_env_sync "$ENGINE" "$ENG_RESULT_VERSION" "$ENG_RESULT_REF"
  if [[ "$INFERENCE_BACKEND" == "$ENGINE" && "$INFERENCE_MANAGED" == true ]] && stack_running; then
    echo "  The running stack still serves the old build: ./stop.sh && ./start.sh -d"
  fi
}

cmd_install() { # cmd_install <install|update>
  need_engine
  local cur latest
  ENG_RESULT_VERSION="" ENG_RESULT_REF="" ENG_RESULT_NOTE=""
  if [[ "$1" == update ]]; then
    [[ -z "$VERSION" ]] || die "update takes no --version (that is: install $ENGINE --version $VERSION)"
    if [[ -z "$FROM" ]]; then
      latest="$(ob_engine_call "$ENGINE" latest)" || die "cannot resolve the latest $ENGINE release (offline? stage it elsewhere and: update $ENGINE --from DIR)"
      latest="${latest%% *}"
      cur="$(ob_engine_pin "$ENGINE")"; cur="${cur%%@*}"
      if [[ -n "$cur" && "$cur" == "$latest" && $FORCE -eq 0 ]]; then
        echo "$ENGINE is already pinned to the latest release ($latest). --force reinstalls it."
        return 0
      fi
      VERSION="$latest"
      echo "latest $ENGINE release: $latest (pinned now: ${cur:-none})"
    fi
  fi
  if [[ $DRY -eq 1 ]]; then
    echo "would install $ENGINE ${VERSION:-latest}${DIGEST:+ @ $DIGEST}${FROM:+ from $FROM} (dry run)"; return 0
  fi
  adopt_current "$ENGINE"
  ob_engine_call "$ENGINE" install "$VERSION" "$FROM" "$DIGEST" || die "$ENGINE install failed — the pin did not move"
  _record "$1"
}

cmd_stage() {
  need_engine
  [[ -n "$DIR" ]] || die "stage needs a target directory: engine.sh stage $ENGINE [--version V] DIR"
  # --digest / --platform win; without them, an exported ENG_STAGE_DIGEST /
  # ENG_STAGE_PLATFORM stands (the form docs/WEIGHT_STREAMING.md step 7
  # uses). A bare prefix assignment of the empty flag value clobbered it, so
  # Maya's wheels were silently staged for THIS box, not the closed one.
  ENG_STAGE_DIGEST="${DIGEST:-${ENG_STAGE_DIGEST:-}}" ENG_STAGE_PLATFORM="${PLATFORM:-${ENG_STAGE_PLATFORM:-}}" \
    ob_engine_call "$ENGINE" stage "$VERSION" "$DIR" || die "staging $ENGINE failed"
}

cmd_rollback() {
  need_engine
  local prev v r note
  prev="$(ob_engine_prev_pin "$ENGINE")"
  [[ -n "$prev" ]] || die "no earlier $ENGINE pin in $OB_ENGINE_STATE_DIR/history.tsv to roll back to"
  IFS=$'\t' read -r v r note <<< "$prev"
  echo "rolling $ENGINE back: $(ob_engine_pin "$ENGINE" || true) → $v@$r"
  [[ $DRY -eq 1 ]] && { echo "(dry run — nothing changed)"; return 0; }
  ENG_RESULT_VERSION="" ENG_RESULT_REF="" ENG_RESULT_NOTE=""
  if ! ob_engine_call "$ENGINE" activate "$v" "$r" "$note"; then
    echo "  $v is no longer on this box — reinstalling it (needs the network, or install --from DIR)."
    local fromdir=""
    if [[ "$ENGINE" == tensorfold && -d "$OB_ENGINE_STATE_DIR/cache" ]]; then
      fromdir="$(ls -d "$OB_ENGINE_STATE_DIR"/cache/tensorfold-"$v"-* 2>/dev/null | head -n1 || true)"
    fi
    local dig=""; [[ "$ENGINE" == vllm ]] && dig="$r"
    ob_engine_call "$ENGINE" install "$v" "$fromdir" "$dig" || die "rollback of $ENGINE to $v failed — the pin did not move"
  fi
  _record rollback
}

case "$CMD" in
  list)     cmd_list ;;
  status)   cmd_status ;;
  use)      cmd_use ;;
  install)  cmd_install install ;;
  update)   cmd_install update ;;
  stage)    cmd_stage ;;
  rollback) cmd_rollback ;;
  history)
    if [[ -n "$ENGINE" ]]; then ob_engine_history "$ENGINE"; else ob_engine_history; fi \
      | awk -F'\t' 'BEGIN { printf "%-19s  %-10s  %-8s  %-14s  %s\n", "TIME", "ENGINE", "ACTION", "VERSION", "REF / NOTE" }
                    { printf "%-19s  %-10s  %-8s  %-14s  %s %s\n", $1, $2, $3, $4, $5, $6 }' ;;
esac
