#!/bin/bash
# Start the full OpenBeast stack:
#   1. llama.cpp server (Qwen3.6-27B Uncensored Q5_K_P by default — uncensored fine-tune, 96.16%)
#   2. MCPO proxy (wraps MCP tools as OpenAPI on http://localhost:3001)
#   3. Open WebUI (http://localhost:3000)
#
# Usage:
#   ./start.sh                     # foreground (Ctrl+C stops the stack)
#   ./start.sh -d                  # background daemon: returns when ready,
#                                  #   stack keeps running; stop with ./stop.sh
#   ./start.sh --status            # what's running (pids); health details via
#                                  #   ./scripts/healthcheck.sh
#   ./start.sh doctor              # diagnose config/security/service health
#                                  #   (fix-list; also ./scripts/doctor.sh)
#   ./start.sh serve-qwen-27b-q5.sh    # specific model (combines with -d)
#
# Daemon mode runs inside a memory-capped systemd scope when available
# (MemoryMax=96G, swap 8G) so a runaway process can only take down the
# stack — never the box. On OOM the supervisor shuts down what remains
# gracefully. Logs: .run/stack.log; pidfiles: .run/*.pid.
#
# OpenCode connects to the MCP server via stdio (configured in opencode.json),
# so it doesn't need MCPO — just run `opencode` in any project.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
RUN_DIR="$REPO_DIR/.run"
SUP_PID_FILE="$RUN_DIR/supervisor.pid"

DAEMON=0; STATUS=0; DAEMONIZED=0; SERVE_SCRIPT=""
for arg in "$@"; do
  case "$arg" in
    -d|--daemon)   DAEMON=1 ;;
    --status)      STATUS=1 ;;
    doctor)        exec "$SCRIPT_DIR/scripts/doctor.sh" ;;   # health/consistency report
    --_daemonized) DAEMONIZED=1 ;;   # internal: this process IS the detached supervisor
    -h|--help)     sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)            echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
    *)             SERVE_SCRIPT="$arg" ;;
  esac
done
source "$SCRIPT_DIR/scripts/lib/proc.sh"   # ob_recorded_pid_ours, ob_pid_record, ob_ext_reap
_pid_alive() { # _pid_alive <pidfile> [cmdline-pattern]
  # Alive AND identity-checked: a stale pidfile whose PID was recycled by an
  # unrelated process must not count as "running". A pidfile written with
  # ob_pid_record carries the process's start time, which is exact; the
  # cmdline pattern is the fallback for one that does not (lib/proc.sh).
  ob_recorded_pid_ours "$1" "${2:-start\.sh|llama|mcpo|openapi_tools|router|chat_server}"
}
# Expected cmdline marker per pidfile (bash ERE).
_pid_pattern() {
  case "$1" in
    supervisor) echo 'start\.sh' ;;
    llama)      echo 'llama-server' ;;
    mcpo)       echo 'mcpo|openapi_tools\.py' ;;   # pidfile name kept; server replaced mcpo
    router)     echo 'router\.py' ;;
    edge)       echo 'edge\.py' ;;
    chat)       echo 'chat_server\.py' ;;
    artifact)   echo 'artifact_server\.py' ;;
    hydra)      echo 'agents/hydra\.py' ;;
    instinct)   echo 'instinct\.server' ;;
    instinct-scorer) echo 'serve-instinct-scorer\.sh|serve\.sh|llama-server' ;;
    *)          echo 'start\.sh|llama|mcpo|openapi_tools|router|edge|chat_server|artifact_server' ;;
  esac
}

if [[ $STATUS -eq 1 ]]; then
  echo "OpenBeast stack status:"
  # An unmanaged backend has no llama pid to report; "llama: not running"
  # read as an outage on a stack that is fine. Say what actually serves.
  _st_managed=1
  if [[ -f "$SCRIPT_DIR/scripts/lib/backend.sh" ]]; then
    source "$SCRIPT_DIR/scripts/lib/conf.sh" 2>/dev/null
    source "$SCRIPT_DIR/scripts/lib/backend.sh"
    ob_inference_managed || _st_managed=0
  fi
  _st_names=(supervisor llama mcpo router edge chat artifact)
  # The opt-in services get a row only when they are on, so a default stack
  # reports exactly what it always did.
  [[ "${HYDRA:-false}" == "true" ]] && _st_names+=(hydra)
  [[ "${INSTINCT_SCORER:-false}" == "true" ]] && _st_names+=(instinct-scorer)
  [[ "${INSTINCT:-false}" == "true" ]] && _st_names+=(instinct)
  for name in "${_st_names[@]}"; do
    if [[ $name == llama && $_st_managed -eq 0 ]]; then
      if ob_backend_ready "$INFERENCE_URL"; then
        echo "  inference: $(ob_backend_label) at $INFERENCE_URL — ready (not managed here)"
      else
        echo "  inference: $(ob_backend_label) at $INFERENCE_URL — NOT ready (not managed here)"
      fi
      continue
    fi
    f="$RUN_DIR/$name.pid"
    if _pid_alive "$f" "$(_pid_pattern "$name")"; then
      echo "  $name: running (pid $(cat "$f"))"
    else
      echo "  $name: not running"
    fi
  done
  if [[ "${HYDRA:-false}" == "true" ]] && declare -F ob_hydra_ready >/dev/null 2>&1; then
    if ob_hydra_ready "http://127.0.0.1:${HYDRA_PORT:-8095}"; then
      echo "  hydra health: ok (default route routable)"
    elif ob_hydra_answering "http://127.0.0.1:${HYDRA_PORT:-8095}"; then
      echo "  hydra health: up, NO routable default route — scripts/hydra.sh status"
    else
      echo "  hydra health: not answering on :${HYDRA_PORT:-8095}"
    fi
  fi
  echo ""
  echo "Service health: ./scripts/healthcheck.sh   Logs: .run/stack.log"
  exit 0
fi

# BIND_HOST (default 127.0.0.1 — loopback-only; remote devices come in via
# Tailscale Serve, see scripts/setup-tailscale.sh). lib/conf.sh also exports
# OPENBEAST_BIND / OPENBEAST_API_KEY for docker-compose interpolation, and
# resolves DEFAULT_SERVE_SCRIPT (conf SERVE_SCRIPT / env OPENBEAST_SERVE_SCRIPT).
source "$SCRIPT_DIR/scripts/lib/conf.sh"
source "$SCRIPT_DIR/scripts/lib/extensions.sh"   # optional-service system
source "$SCRIPT_DIR/scripts/lib/net.sh"          # ob_probe_host, ob_llama_ready
source "$SCRIPT_DIR/scripts/lib/backend.sh"      # ob_backend_ready, ob_inference_managed
source "$SCRIPT_DIR/scripts/lib/curl_auth.sh"    # ob_curl_hdr: tokens never on argv
source "$SCRIPT_DIR/scripts/lib/portown.sh"      # ob_port_listening, ob_pid_owns_port
SERVE_SCRIPT="${SERVE_SCRIPT:-$DEFAULT_SERVE_SCRIPT}"

# _spawn_ready <label> <pidname> <port> <health-url> <cmd…>
# Start one loopback helper server (beast-chat, beast-artifact) and wait for
# it. "Ready" means the health route answers AND the process we started is the
# one holding the port. The old loop checked only that our pid was alive and
# that SOMETHING answered: a spawn that loses the bind race is still alive for
# ~0.2 s, so an orphan or a sibling worktree's server on the same port made
# start.sh print "ready" and record a pid about to die (integration-ops-6).
#   0  ready, and the listener is ours                  (SPAWN_PID = its pid)
#   1  did not come up; SPAWN_PID is the pid if it is still alive, else empty
#      (and its pidfile is removed)
#   2  the port was already held — nothing spawned, no pidfile written
_spawn_ready() {
  local label="$1" pidname="$2" port="$3" url="$4" holder _i _h own
  shift 4
  SPAWN_PID=""
  if ob_port_listening "$port"; then
    holder="$(ob_port_pids "$port" 2>/dev/null | tr '\n' ' ' || true)"; holder="${holder% }"
    echo "WARNING: $label: port $port is already in use${holder:+ by pid $holder}, by a process" >&2
    echo "         this stack has no record of — a sibling worktree's server or an orphan of a" >&2
    echo "         killed stack. Not starting a second one (it could not bind). Stop that" >&2
    echo "         process, then: ./scripts/healthcheck.sh --restart" >&2
    return 2
  fi
  "$@" &
  SPAWN_PID=$!
  echo "$SPAWN_PID" > "$RUN_DIR/$pidname.pid"
  for _i in $(seq 1 20); do
    kill -0 "$SPAWN_PID" 2>/dev/null || break
    # -f plus a body match: a 400 used to count as "ready" (curl -s exits 0
    # on any HTTP status).
    _h="$(curl -fsS -m 2 "$url" 2>/dev/null || true)"
    if [[ "$_h" == *'"status"'* ]]; then
      own=0; ob_pid_owns_port "$SPAWN_PID" "$port" || own=$?
      if [[ $own -eq 2 ]]; then
        # No ss / lsof / /proc to ask: outlive the bind-failure window, then
        # require that our process is still there.
        sleep 1
        if kill -0 "$SPAWN_PID" 2>/dev/null; then own=0; else own=1; fi
      fi
      [[ $own -eq 0 ]] && return 0
      # Something answered, but it is not the process we started: ours is
      # losing (or has lost) the bind. Keep looking; kill -0 ends the loop.
    fi
    sleep 1
  done
  if ! kill -0 "$SPAWN_PID" 2>/dev/null; then
    if [[ -n "${_h:-}" ]]; then
      echo "WARNING: $label exited, and a DIFFERENT process answers on port $port — not ours," >&2
      echo "         so not reported as ready. Find it with: ss -ltnp 'sport = :$port'" >&2
    fi
    rm -f "$RUN_DIR/$pidname.pid"
    SPAWN_PID=""
  fi
  return 1
}

# INFERENCE_MANAGED=false (always, for vLLM / TensorFold): the inference
# server belongs to someone else — a cluster on other boxes. This stack
# launches, rolls back, supervises and kills NOTHING there; it waits for the
# server to answer at INFERENCE_URL and brings everything else up around it.
MANAGED=1
ob_inference_managed || MANAGED=0

if [[ $MANAGED -eq 1 && ! -x "$SCRIPT_DIR/scripts/$SERVE_SCRIPT" ]]; then
  echo "Error: scripts/$SERVE_SCRIPT not found or not executable" >&2
  exit 1
fi

# Probe where services actually listen: loopback answers for wildcard binds;
# a specific LAN/tailnet address must be probed directly; IPv6 comes back
# bracketed (lib/net.sh — the ONE mapping start/doctor/healthcheck share).
# Used by the daemon launcher's readiness probes AND the supervisor below.
HEALTH_HOST="$(ob_probe_host "$BIND_HOST")"
# INFERENCE_URL defaults to exactly http://$HEALTH_HOST:8080 (lib/conf.sh).
LLAMA_BASE="$INFERENCE_URL"
# Where the stack's own consumers (router, beast-gate) send inference:
# beast-hydra when HYDRA=true, else exactly LLAMA_BASE. LLAMA_BASE keeps
# meaning the local engine (readiness, KV warm-up, rollback).
# From THIS start's conf (HYDRA/HYDRA_URL), never an inherited export.
CONSUMER_BASE="$LLAMA_BASE"
[[ "${HYDRA:-false}" == "true" ]] && CONSUMER_BASE="$HYDRA_URL"

# beast-hydra: validate its config BEFORE anything is launched (both the
# daemon launcher and the supervisor pass through here). An invalid config
# is fatal — never silently bypass hydra, since every consumer now points at
# it. No hydra.toml = the implicit single-node config, validated the same way.
HYDRA_CLASSIFY_ROUTE=false
HYDRA_CHECK_SUMMARY=""
if [[ "${HYDRA:-false}" == "true" ]]; then
  _hy_check=(--check)
  [[ -f "$HYDRA_CONFIG" ]] && _hy_check+=("$HYDRA_CONFIG")
  if ! _hy_json="$(python3 "$SCRIPT_DIR/agents/hydra.py" "${_hy_check[@]}" --json 2>/dev/null)"; then
    echo "Error: HYDRA=true but the beast-hydra config does not validate:" >&2
    python3 "$SCRIPT_DIR/agents/hydra.py" "${_hy_check[@]}" >&2 || true
    echo "  Fix ${HYDRA_CONFIG} (scripts/hydra.sh check), or set HYDRA=false." >&2
    exit 1
  fi
  # `|| true`: a formatter hiccup must not abort a start whose config passed.
  read -r HYDRA_CLASSIFY_ROUTE HYDRA_CHECK_SUMMARY < <(printf '%s' "$_hy_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for w in d.get("warnings") or []:
    print("WARNING: beast-hydra: %s" % w, file=sys.stderr)
print("true" if d.get("classify_route") else "false",
      "%s node(s), %s route(s), config %s" % (d.get("nodes"), d.get("routes"), d.get("config")))
') || true
  [[ "$HYDRA_CLASSIFY_ROUTE" == "true" ]] || HYDRA_CLASSIFY_ROUTE=false
  unset _hy_json _hy_check
fi
# How long a model may take to LOAD before the load counts as failed (the
# watchdog's bound on "Loading model", healthcheck.sh, is the same knob).
LLAMA_LOAD_GRACE="${OPENBEAST_LLAMA_LOAD_GRACE:-900}"
[[ "$LLAMA_LOAD_GRACE" =~ ^[0-9]+$ ]] || LLAMA_LOAD_GRACE=900

# ---- log rotation: installed on the default path, not by a manual step ----
# stack.log and the audit trails grow without bound unless
# openbeast-logrotate.timer runs; for a long time it existed only behind a
# manual `./scripts/logrotate.sh --install` that nothing on the default path
# ran (review storage-04). So every start makes sure it is there: a no-op
# when it is already enabled, when there is no reachable systemd --user
# manager (macOS, a container, a CI runner), or with LOGROTATE_AUTOINSTALL=
# false in openbeast.conf. Never fatal — rotation is housekeeping, and a
# failed install must not keep the model from starting.
ensure_logrotate_timer() {
  [[ "${LOGROTATE_AUTOINSTALL:-true}" == "true" ]] || return 0
  [[ -x "$SCRIPT_DIR/scripts/logrotate.sh" ]] || return 0
  command -v systemctl >/dev/null 2>&1 || return 0
  systemctl --user is-enabled --quiet openbeast-logrotate.timer 2>/dev/null && return 0
  # Reachable user manager? (`show-environment` answers only when it is.)
  systemctl --user show-environment >/dev/null 2>&1 || return 0
  echo "Installing daily log rotation (openbeast-logrotate.timer; opt out: LOGROTATE_AUTOINSTALL=false)..."
  "$SCRIPT_DIR/scripts/logrotate.sh" --install 2>&1 | sed 's/^/  /' \
    || echo "  Warning: log rotation not installed — run ./scripts/logrotate.sh --install" >&2
  return 0
}
[[ $DAEMONIZED -eq 1 ]] || ensure_logrotate_timer

# ---- daemon launcher: spawn the detached supervisor, wait for readiness ----
if [[ $DAEMON -eq 1 ]]; then
  mkdir -p "$RUN_DIR"
  if _pid_alive "$SUP_PID_FILE" "$(_pid_pattern supervisor)"; then
    echo "Stack already running (supervisor pid $(cat "$SUP_PID_FILE"))." >&2
    echo "Check ./start.sh --status, or ./stop.sh first." >&2
    exit 1
  fi
  if [[ $MANAGED -eq 1 ]]; then
    echo "Starting OpenBeast in the background ($SERVE_SCRIPT)..."
  else
    echo "Starting OpenBeast in the background (inference: $(ob_backend_label) at $INFERENCE_URL, not managed here)..."
  fi
  if command -v systemd-run >/dev/null 2>&1 \
     && systemd-run --user --scope --quiet true 2>/dev/null; then
    # Transient service in a memory-capped cgroup: if anything in the stack
    # runs away, the kernel OOM-kills inside the scope; the box survives and
    # the supervisor's trap shuts the remainder down cleanly. The cap is
    # MEM_LIMIT_PCT% (default 75) of THIS machine's RAM, resolved fresh at
    # every launch — override via openbeast.conf or OPENBEAST_MEM_LIMIT_PCT.
    if ! [[ "$MEM_LIMIT_PCT" =~ ^[0-9]+$ ]] || [[ "$MEM_LIMIT_PCT" -lt 1 || "$MEM_LIMIT_PCT" -gt 100 ]]; then
      echo "Warning: MEM_LIMIT_PCT='$MEM_LIMIT_PCT' invalid (need 1-100) — using 75" >&2
      MEM_LIMIT_PCT=75
    fi
    MEM_TOTAL_KB=$(grep -m1 MemTotal /proc/meminfo | awk '{print $2}' || true)
    MEM_TOTAL_KB="${MEM_TOTAL_KB:-33554432}"   # fallback: assume 32 GB
    MEM_MAX_BYTES=$(( MEM_TOTAL_KB * 1024 / 100 * MEM_LIMIT_PCT ))
    MEM_MAX_GB=$(( MEM_MAX_BYTES / 1024 / 1024 / 1024 ))
    systemctl --user reset-failed openbeast-stack 2>/dev/null || true
    # Forward the caller's OPENBEAST_* overrides (plus WebUI admin creds) into
    # the transient unit — systemd-run starts from a CLEAN environment, so
    # without this `OPENBEAST_BIND=... ./start.sh -d` silently reverts to
    # conf/defaults inside the daemon.
    # SECRETS ARE FILTERED (any *KEY* / *PASSWORD* / *SECRET* var): unit env
    # is readable via `systemctl --user show -p Environment`, so keys must
    # not travel this way. The daemonized start.sh re-sources conf.sh, which
    # reads them from openbeast.conf (mode 600) directly — secret overrides
    # therefore belong in openbeast.conf, not per-shell env, when using -d.
    # *NOTIFY_URL* too: an ntfy topic URL is a bearer secret with an innocent
    # name (conf.sh already unexported it; this is the second lock).
    SETENV_ARGS=()
    while IFS= read -r _var; do
      if [[ -n "$_var" && "$_var" != *KEY* && "$_var" != *PASSWORD* && "$_var" != *SECRET* \
            && "$_var" != *NOTIFY_URL* ]]; then
        SETENV_ARGS+=(--setenv="${_var}=${!_var}")
      fi
    done < <(compgen -e | grep '^OPENBEAST_' || true)
    if [[ -n "${WEBUI_ADMIN_EMAIL:-}" ]]; then
      SETENV_ARGS+=(--setenv="WEBUI_ADMIN_EMAIL=${WEBUI_ADMIN_EMAIL}")
    fi
    systemd-run --user --quiet --collect --unit=openbeast-stack \
      -p MemoryMax="$MEM_MAX_BYTES" -p MemorySwapMax=8G \
      ${SETENV_ARGS[@]+"${SETENV_ARGS[@]}"} \
      "$SCRIPT_DIR/start.sh" --_daemonized "$SERVE_SCRIPT"
    echo "  (memory-capped scope 'openbeast-stack': ${MEM_LIMIT_PCT}% of RAM = ${MEM_MAX_GB}G, swap 8G)"
  else
    setsid nohup "$0" --_daemonized "$SERVE_SCRIPT" \
      >>"$RUN_DIR/stack.log" 2>&1 < /dev/null &
    echo "  (systemd-run unavailable — plain background process, no memory cap)"
  fi

  if [[ $MANAGED -eq 1 ]]; then
    echo "Waiting for the model to load (log: .run/stack.log)..."
  else
    echo "Waiting for $(ob_backend_label) at $INFERENCE_URL and the stack (log: .run/stack.log)..."
  fi
  # Deadline, not an iteration count: the supervisor gives a load up to
  # LLAMA_LOAD_GRACE, and a launcher that quits first reports a failure
  # while the model is still legitimately coming up.
  _ready_deadline=$(( SECONDS + LLAMA_LOAD_GRACE + 300 ))
  _launched_at=$SECONDS
  while (( SECONDS < _ready_deadline )); do
    # Readiness = llama + MCPO (+ router when enabled; it hard-binds
    # 127.0.0.1 — see agents/router.py — so probe it there like the
    # supervisor does). Without the router term "Stack is up" would print
    # before the supervisor's router gate has passed.
    ROUTER_READY=1
    if [[ "${AGENT_ROUTER:-false}" == "true" ]]; then
      curl -s -m 2 "http://127.0.0.1:${ROUTER_PORT}/health" >/dev/null 2>&1 || ROUTER_READY=0
    fi
    # Same reasoning for the gate: without this term, `-d` prints "Stack is
    # up" and exits 0 while the supervisor may still be failing its gate
    # check — the caller would think remote clients are served when they
    # are not. /gate/health needs no auth for liveness.
    EDGE_READY=1
    if [[ "${EDGE_GATE:-false}" == "true" ]]; then
      curl -s -m 2 "http://$HEALTH_HOST:${EDGE_PORT:-8090}/gate/health" >/dev/null 2>&1 || EDGE_READY=0
    fi
    # beast-hydra: every consumer points at it, so "up" needs its process
    # answering (a 503 "no routable default yet" still counts — the report
    # below says so; the supervisor gives it HYDRA_READY_GRACE to route).
    HYDRA_UP=1
    if [[ "${HYDRA:-false}" == "true" ]]; then
      ob_hydra_answering "$HYDRA_URL" || HYDRA_UP=0
    fi
    # ob_llama_ready, not `curl -s`: llama-server answers 503 "Loading
    # model" from the moment it binds, and curl -s exits 0 on a 503 — "Stack
    # is up" printed (and openbeast.service reported started) mid-load.
    # ob_backend_ready IS ob_llama_ready for llama; vLLM / TensorFold get
    # their own rule (lib/backend.sh).
    # Unmanaged: the supervisor brings the stack up even when the remote
    # server is still down after the grace (the Sparks may simply be off),
    # so the tool server answering is the "up" signal — and the report
    # below says plainly that inference is NOT ready, never that it is.
    INFER_READY=0
    ob_backend_ready "$LLAMA_BASE" && INFER_READY=1
    if [[ $INFER_READY -eq 1 || $MANAGED -eq 0 ]] \
       && curl -s -m 2 "http://$HEALTH_HOST:3001/health" >/dev/null 2>&1 \
       && [[ $ROUTER_READY -eq 1 ]] && [[ $EDGE_READY -eq 1 ]] && [[ $HYDRA_UP -eq 1 ]]; then
      echo ""
      if [[ $INFER_READY -eq 1 ]]; then
        echo "Stack is up:"
      else
        echo "Stack is up — but inference is NOT ready:"
      fi
      if [[ $MANAGED -eq 1 ]]; then
        echo "  Model server:  http://localhost:8080"
      elif [[ $INFER_READY -eq 1 ]]; then
        echo "  Model server:  $INFERENCE_URL ($(ob_backend_label), not managed here)"
      else
        echo "  Model server:  NOT READY at $INFERENCE_URL ($(ob_backend_label), not managed here)"
        echo "                 start it where it runs (docs/DGX_SPARK_PLAN.md); .run/stack.log"
        echo "                 logs the moment it becomes ready. Chat fails until then."
      fi
      echo "  MCPO tools:    http://localhost:3001 (OpenAPI docs at /docs)"
      # The WebUI container starts AFTER this readiness point (the
      # supervisor brings the frontend up once the model is serving), so
      # this line cannot claim it is up. Say what we can actually tell:
      # an unreachable docker daemon means it will not come up at all.
      if curl -s -m 2 -o /dev/null "http://$HEALTH_HOST:3000/health" 2>/dev/null; then
        echo "  Open WebUI:    http://localhost:3000"
      elif ! docker info >/dev/null 2>&1; then
        echo "  Open WebUI:    NOT STARTING — the docker daemon is not reachable by $(id -un)"
        echo "                 (is docker running? is $(id -un) in the docker group?)"
      else
        echo "  Open WebUI:    http://localhost:3000 (container still starting — ./start.sh --status)"
      fi
      if [[ "${AGENT_ROUTER:-false}" == "true" ]]; then
        echo "  Agent router:  http://localhost:${ROUTER_PORT} (frontends route through it)"
      fi
      if [[ "${EDGE_GATE:-false}" == "true" ]]; then
        echo "  beast-gate:    http://localhost:${EDGE_PORT:-8090} (remote clients arrive here)"
      fi
      if [[ "${HYDRA:-false}" == "true" ]]; then
        if ob_hydra_ready "$HYDRA_URL"; then
          echo "  beast-hydra:   $HYDRA_URL (${HYDRA_CHECK_SUMMARY:-?}) — consumers route through it"
        else
          echo "  beast-hydra:   $HYDRA_URL — up, but NO routable default route yet (scripts/hydra.sh status)"
        fi
      fi
      if [[ "${INSTINCT:-false}" == "true" ]]; then
        echo "  beast-instinct: http://127.0.0.1:${INSTINCT_PORT} (scripts/instinct.sh status)"
      fi
      echo "  Status:        ./start.sh --status    Stop: ./stop.sh"
      exit 0
    fi
    if (( SECONDS - _launched_at > 20 )) && ! _pid_alive "$SUP_PID_FILE" "$(_pid_pattern supervisor)"; then
      echo "Error: supervisor exited during startup. Last log lines:" >&2
      tail -20 "$RUN_DIR/stack.log" 2>/dev/null >&2 || true
      exit 1
    fi
    sleep 2
  done
  echo "Timed out after $(( LLAMA_LOAD_GRACE / 60 + 5 )) min — inspect ./start.sh --status and .run/stack.log" >&2
  if [[ $MANAGED -eq 0 ]] && ! ob_backend_ready "$LLAMA_BASE"; then
    echo "  The $(ob_backend_label) server at INFERENCE_URL=$INFERENCE_URL never became ready —" >&2
    echo "  it is not managed here: start it where it runs (docs/DGX_SPARK_PLAN.md)." >&2
  fi
  exit 1
fi

# ---- supervisor (foreground, or detached when --_daemonized) ---------------
mkdir -p "$RUN_DIR"
# A plain foreground `./start.sh` while a daemon stack is live would clobber
# its pidfiles and fight it for ports/VRAM. (The internal --_daemonized
# re-exec IS the supervisor being started, so it skips the guard.)
if [[ $DAEMONIZED -eq 0 ]] && _pid_alive "$SUP_PID_FILE" "$(_pid_pattern supervisor)"; then
  echo "Stack already running (supervisor pid $(cat "$SUP_PID_FILE"))." >&2
  echo "Check ./start.sh --status, or ./stop.sh first." >&2
  exit 1
fi
if [[ $DAEMONIZED -eq 1 ]]; then
  exec >>"$RUN_DIR/stack.log" 2>&1
  echo "=== OpenBeast supervisor start: $(date '+%Y-%m-%d %H:%M:%S') ($SERVE_SCRIPT) ==="
fi
# Transient systemd units (daemon mode) start with a minimal PATH that lacks
# ~/.local/bin, where pip --user puts mcpo. Harmless everywhere else.
export PATH="$HOME/.local/bin:$PATH"
# With its start time: 'start\.sh' in a command line is not an identity (any
# project's ./start.sh matches), and stop.sh SIGKILLs what this record names.
ob_pid_record "$SUP_PID_FILE" "$$"
# Starting on purpose ends a stop on purpose: the watchdog may recover this
# stack again (stop.sh wrote .run/stopped; healthcheck.sh honours it), and
# its relaunch budget starts fresh.
rm -f "$RUN_DIR/stopped" "$RUN_DIR/watchdog-relaunches"
# The supervisor giving up on a crash-looping model is a stop too: without
# the marker the watchdog relaunched it every five minutes, forever.
_mark_gave_up() {
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "supervisor gave up: $1" > "$RUN_DIR/stopped"
}
# Record which serve script this stack runs so healthcheck.sh --restart can
# relaunch the SAME model instead of assuming the default. An unmanaged stack
# runs none, and a stale record would have the dashboard describe a local
# launch path (capacity.ctx_shared) that is not in use.
if [[ $MANAGED -eq 1 ]]; then
  echo "$SERVE_SCRIPT" > "$RUN_DIR/serve-script"
else
  rm -f "$RUN_DIR/serve-script"
fi

# Fail fast on docker container-name conflicts BEFORE the multi-minute model
# load. Containers named ours but owned by another compose project (e.g.
# created before the repo was renamed) make `docker compose up` fail, and
# set -e would then tear the whole stack down mid-start.
for cname in open-webui searxng; do
  owner=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$cname" 2>/dev/null || true)
  if [[ -n "$owner" && "$owner" != "openbeast" ]]; then
    echo "Error: container '$cname' belongs to compose project '$owner', not 'openbeast'." >&2
    echo "  Its data lives in a docker volume and survives removal. Fix with:" >&2
    echo "    docker rm -f $cname     # then rerun ./start.sh" >&2
    echo "  If the old project had WebUI data, see docs/INSTALL.md troubleshooting" >&2
    echo "  ('renamed repo directory') for the volume-migration steps." >&2
    rm -f "$SUP_PID_FILE" "$RUN_DIR/supervisor.start"
    exit 1
  fi
done

# Cleanup on exit: stop MCPO and llama.cpp, drop pidfiles. Runs on Ctrl+C,
# ./stop.sh (SIGTERM), and after an OOM kill takes out llama-server.
# Idempotent: the TERM path exits, which fires the EXIT trap a second time.
CLEANED=0
STOPPING=0
# Did THIS start.sh spawn them? The [17] guard deliberately leaves a live
# chat/artifact server alone, which means their pidfiles belong to another
# process — and cleanup() was deleting those pidfiles anyway on exit,
# recreating the very no-recorded-pid state the guard exists to prevent.
CHAT_OWNED=0
ARTIFACT_OWNED=0

# _rm_own_pidfile <pidfile> <our-pid> — remove a pidfile (and its .start
# sidecar) only while it still names OUR child; see the note in cleanup().
_rm_own_pidfile() {
  [[ -n "${2:-}" && -f "$1" ]] || return 0
  [[ "$(cat "$1" 2>/dev/null || true)" == "$2" ]] && rm -f "$1" "${1%.pid}.start"
  return 0
}

cleanup() {
  [[ $CLEANED -eq 1 ]] && return 0
  CLEANED=1
  echo ""
  echo "Shutting down..."
  if [[ -n "${CONFIG_PID:-}" ]]; then
    kill "$CONFIG_PID" 2>/dev/null || true
  fi
  if [[ -n "${ROUTER_PID:-}" ]]; then
    kill "$ROUTER_PID" 2>/dev/null && echo "agent router stopped."
  fi
  if [[ -n "${EDGE_PID:-}" ]]; then
    kill "$EDGE_PID" 2>/dev/null && echo "beast-gate stopped."
  fi
  # beast-instinct: the service first (instinct.sh kills by pidfile AND
  # cmdline), then its CPU scorer — the reverse of the start order.
  if [[ ${INSTINCT_STARTED:-0} -eq 1 ]]; then
    "$SCRIPT_DIR/scripts/instinct.sh" down 2>/dev/null || true
  fi
  if [[ -n "${INSTINCT_SCORER_PID:-}" ]]; then
    kill "$INSTINCT_SCORER_PID" 2>/dev/null && echo "beast-instinct CPU scorer stopped."
    rm -f "$RUN_DIR/instinct-scorer.pid" "$RUN_DIR/instinct-scorer.start"
  fi
  if [[ -n "${HYDRA_PID:-}" ]]; then
    kill "$HYDRA_PID" 2>/dev/null && echo "beast-hydra stopped."
    # only while the file still names OUR hydra: healthcheck.sh --restart
    # records a replacement in the same file, and deleting that record
    # would orphan it (the next start then dies on "port already held")
    _rm_own_pidfile "$RUN_DIR/hydra.pid" "$HYDRA_PID"
  fi
  if [[ -n "${CHAT_PID:-}" ]]; then
    kill "$CHAT_PID" 2>/dev/null && echo "beast-chat console stopped."
  fi
  if [[ -n "${ARTIFACT_PID:-}" ]]; then
    kill "$ARTIFACT_PID" 2>/dev/null && echo "beast-artifact stopped."
  fi
  if [[ -n "${MCPO_PID:-}" ]]; then
    kill "$MCPO_PID" 2>/dev/null && echo "MCPO proxy stopped."
  fi
  if [[ -n "${LLAMA_PID:-}" ]]; then
    kill "$LLAMA_PID" 2>/dev/null && echo "llama.cpp server stopped."
  fi
  if [[ -n "${IDLE_PID:-}" ]]; then
    kill "$IDLE_PID" 2>/dev/null || true
  fi
  # Reap any process-kind extensions we launched — identity-checked. This
  # trap also fires on every early exit of a FRESH start, i.e. against
  # pidfiles a crashed run left behind, whose numbers may now be anyone's.
  for _pf in "$RUN_DIR"/ext-*.pid; do
    [[ -e "$_pf" ]] || continue
    _n="$(basename "$_pf" .pid)"
    ob_ext_reap "$_pf" "$REPO_DIR/extensions/${_n#ext-}"
  done
  rm -f "$RUN_DIR/supervisor.pid" "$RUN_DIR/supervisor.start" "$RUN_DIR/llama.pid" "$RUN_DIR/mcpo.pid" \
        "$RUN_DIR/router.pid" "$RUN_DIR/edge.pid"
  # ...but only the pidfiles of servers WE started. Removing a live server's
  # recorded pid is what makes an orphan unreapable, which is the whole point
  # of the [17] guard above.
  # ...and only while the file still names OUR child. healthcheck.sh --restart
  # replaces a crashed server and records the replacement's pid in the same
  # file; "we started one once" (CHAT_OWNED) is not "this pid is ours", and
  # deleting the replacement's record recreates the unreapable-orphan state.
  [[ ${CHAT_OWNED:-0} -eq 1 ]] && _rm_own_pidfile "$RUN_DIR/chat.pid" "${CHAT_PID:-}"
  [[ ${ARTIFACT_OWNED:-0} -eq 1 ]] && _rm_own_pidfile "$RUN_DIR/artifact.pid" "${ARTIFACT_PID:-}"
  return 0
}
trap cleanup EXIT
trap 'STOPPING=1; cleanup; exit 143' INT TERM

launch_llama() {
  echo "Starting llama.cpp server ($SERVE_SCRIPT)..."
  "$SCRIPT_DIR/scripts/$SERVE_SCRIPT" &
  LLAMA_PID=$!
  echo "$LLAMA_PID" > "$RUN_DIR/llama.pid"
}

# Returns 0 once llama-server is READY, 1 if the process dies first or the
# load outlives LLAMA_LOAD_GRACE.
#
# READY means 200 {"status":"ok"} (ob_llama_ready). This used to be
# `until curl -s .../health`, and llama-server binds its port BEFORE loading
# the model, answering 503 "Loading model" throughout — which curl -s calls
# success. So the model counted as healthy the moment the port bound:
# launch_and_wait recorded a model that then OOMed mid-load as LAST-GOOD
# (overwriting the real one, so MODEL_ROLLBACK could never fire), the KV
# warmer fired into the 503, and fast boot announced "Full model live"
# during the load.
#
# The deadline covers the other direction: a load wedged in CUDA or on a
# stalled read says "Loading model" forever and never dies, and this loop
# waited on it forever. Past the grace the load has FAILED: stop the process
# (it still holds the port and VRAM) so a rollback can have them.
wait_llama_health() {
  local t0=$SECONDS _i
  until ob_llama_ready "$LLAMA_BASE"; do
    kill -0 "$LLAMA_PID" 2>/dev/null || return 1
    if (( SECONDS - t0 >= LLAMA_LOAD_GRACE )); then
      echo "llama-server not healthy after ${LLAMA_LOAD_GRACE}s (OPENBEAST_LLAMA_LOAD_GRACE) — stopping it; the load has failed." >&2
      kill "$LLAMA_PID" 2>/dev/null || true
      for _i in $(seq 1 20); do kill -0 "$LLAMA_PID" 2>/dev/null || break; sleep 1; done
      kill -KILL "$LLAMA_PID" 2>/dev/null || true
      for _i in $(seq 1 5); do kill -0 "$LLAMA_PID" 2>/dev/null || break; sleep 1; done
      return 1
    fi
    sleep 1
  done
}

# Record the serve script that just proved healthy, so a future launch that
# fails can revert to it (model load-failure rollback, conf MODEL_ROLLBACK).
record_last_good() { printf '%s\n' "$1" > "$RUN_DIR/last-good-serve-script"; }

# Re-run WebUI model configuration after the SERVED MODEL CHANGES.
#
# configure-webui.sh is backgrounded early (below) and polls /v1/models for up
# to 30s. Under FAST_BOOT it therefore wins the race against the hot-swap and
# writes a permanent WebUI model row for the BOOTSTRAP BRIDGE — leaving the real
# model with no row, hence no meta.toolIds, hence no tools attached to any chat.
# Model-load rollback has the same shape: it switches serve scripts long after
# configure-webui.sh has exited. Both silently produce a toolless WebUI.
#
# Guarded on WebUI actually being reachable, which makes this a no-op during the
# initial launch (WebUI isn't up yet and the normal backgrounded run covers it)
# and safe to call from anywhere the model changes. Idempotent and backgrounded.
reconfigure_webui_for_model() {
  curl -s -m 3 "http://localhost:3000/api/version" >/dev/null 2>&1 || return 0
  "$SCRIPT_DIR/scripts/configure-webui.sh" >/dev/null 2>&1 &
}

# Launch the current $SERVE_SCRIPT and wait for health. On failure, if rollback
# is enabled and a different last-known-good exists, launch THAT instead and
# update $SERVE_SCRIPT + the restart record. Returns 0 if some model is serving
# (original or rollback), 1 if everything failed. Records last-good on success.
launch_and_wait() {
  launch_llama
  if wait_llama_health; then record_last_good "$SERVE_SCRIPT"; return 0; fi
  local failed="$SERVE_SCRIPT" lastgood
  # serve.sh exits 3 for a WEIGHT_ENFORCE=strict supply-chain refusal. Rolling
  # back there would silently serve a DIFFERENT model than the operator
  # configured — precisely what strict mode exists to prevent. Refuse loudly
  # instead; an unvetted weight is a decision for a human, not a fallback.
  if [[ -n "${LLAMA_PID:-}" ]] && ! kill -0 "$LLAMA_PID" 2>/dev/null; then
    wait "$LLAMA_PID" 2>/dev/null; local _rc=$?
    if [[ $_rc -eq 3 ]]; then
      echo "Refusing to roll back: '$failed' was rejected by the weight registry" >&2
      echo "  (WEIGHT_ENFORCE=strict). Serving a different model would defeat the check." >&2
      echo "  Fix the weight, re-pin it, or set WEIGHT_ENFORCE=warn in openbeast.conf." >&2
      return 1
    fi
  fi
  if [[ "${MODEL_ROLLBACK:-true}" == "true" && -f "$RUN_DIR/last-good-serve-script" ]]; then
    lastgood="$(head -n1 "$RUN_DIR/last-good-serve-script" 2>/dev/null || true)"
    if [[ -n "$lastgood" && "$lastgood" != "$failed" && -x "$SCRIPT_DIR/scripts/$lastgood" ]]; then
      echo "Rollback: '$failed' failed to load — reverting to last-known-good '$lastgood'." >&2
      SERVE_SCRIPT="$lastgood"
      echo "$SERVE_SCRIPT" > "$RUN_DIR/serve-script"
      launch_llama
      if wait_llama_health; then
        record_last_good "$SERVE_SCRIPT"
        echo "Rolled back to '$SERVE_SCRIPT'. Your configured model needs attention (VRAM? corrupt weight? run ./scripts/verify-weights.sh --deep)." >&2
        reconfigure_webui_for_model
        return 0
      fi
    fi
  fi
  return 1
}

warm_kv_cache() {
  # Warm the KV cache with the WebUI system prompt so the user's FIRST chat
  # doesn't pay the ~1s cold prompt-processing (a ~3000-token system prefix
  # processed from scratch). Best-effort; never blocks startup. The primed
  # prefix is reused by every subsequent same-prompt turn.
  [[ -f "$REPO_DIR/system-prompt.md" ]] || return 0
  # Build SYS byte-for-byte the way configure-webui.sh stores WebUI's system
  # prompt: $(cat) strips each file's trailing newline, joined by ONE blank
  # line, then .strip() below mirrors configure-webui's storage. This must
  # match token-for-token — a raw `cat f1 f2` gives a single '\n' at the file
  # boundary vs WebUI's '\n\n', so the primed prefix would diverge mid-prompt
  # and the first real chat still pays a partial reprocess (~57ms).
  ( SYS="$(cat "$REPO_DIR/system-prompt.md")"
    if [[ -f "$REPO_DIR/system-prompt-tools.md" ]]; then
      SYS="$SYS"$'\n\n'"$(cat "$REPO_DIR/system-prompt-tools.md")"
    fi
    python3 - "$SYS" "$LLAMA_BASE" <<'WARM' >/dev/null 2>&1 || true
import json, sys, urllib.request
body=json.dumps({"messages":[{"role":"system","content":sys.argv[1].strip()},
    {"role":"user","content":"hi"}],"max_tokens":1,"temperature":0,
    "chat_template_kwargs":{"enable_thinking":False}}).encode()
try:
    urllib.request.urlopen(urllib.request.Request(
        sys.argv[2] + "/v1/chat/completions", data=body,
        headers={"Content-Type":"application/json"}), timeout=60).read()
except Exception:
    pass
WARM
    echo "  (KV cache warmed with the system prompt)" ) &
}

# Unmanaged: wait for someone else's server, bounded like a load. It is not
# ours, so a server that never answers is reported, never killed or rolled
# back — and the message names INFERENCE_URL, because that is the only knob
# on this side.
# What "inference is ready" means to an unmanaged stack: the engine itself,
# or — under HYDRA=true, where every consumer goes through it — beast-hydra
# being able to route its default route (which also covers a fleet whose
# local engine is down but whose other nodes serve).
_infer_ready() {
  if [[ "${HYDRA:-false}" == "true" ]]; then
    ob_hydra_ready "$HYDRA_URL"
  else
    ob_backend_ready "$LLAMA_BASE"
  fi
}

# beast-hydra (opt-in, HYDRA=true): the inference router every consumer now
# talks to (docs/BEAST_HYDRA_PLAN.md §6.7). Launched BEFORE the inference
# wait so it watches the engine load, and so the unmanaged wait can ask it.
# Fatal on failure: WebUI, the router, the gate and spawned agents all point
# at it, so a stack without it has no inference at all.
launch_hydra() {
  [[ "${HYDRA:-false}" == "true" ]] || return 0
  local _i
  if ob_port_listening "$HYDRA_PORT"; then
    echo "Error: HYDRA=true but port $HYDRA_PORT is already held — an orphan beast-hydra of a" >&2
    echo "       killed stack, or a sibling worktree's. Every consumer would talk to a process" >&2
    echo "       this stack did not start. ./stop.sh, then retry (or set HYDRA_PORT)." >&2
    exit 1
  fi
  # The caller token: 32 random bytes, 0600, minted fresh per start. hydra
  # trusts forwarded identity headers only next to it (docs §6.8), and the
  # router / gate read it from OPENBEAST_HYDRA_CALLER_TOKEN_FILE (conf.sh).
  ( umask 077
    python3 -c 'import secrets; print(secrets.token_hex(32))' > "$HYDRA_CALLER_TOKEN_FILE.tmp"
    chmod 600 "$HYDRA_CALLER_TOKEN_FILE.tmp"
    mv -f "$HYDRA_CALLER_TOKEN_FILE.tmp" "$HYDRA_CALLER_TOKEN_FILE" )
  ( umask 077; : >> "$RUN_DIR/hydra.log" )
  echo "Starting beast-hydra on $HYDRA_URL (${HYDRA_CHECK_SUMMARY:-config ok})..."
  # INFERENCE_* and the key reach it through the ENVIRONMENT (conf.sh exports
  # them; LLAMA_API_KEY only when set), never argv. OPENBEAST_INFERENCE_MODEL
  # is the route id under hydra, so the engine's served id rides its own var.
  OPENBEAST_HYDRA_UPSTREAM_MODEL="${INFERENCE_MODEL:-}" \
  INFERENCE_SLOTS="${INFERENCE_SLOTS:-}" \
  OPENBEAST_HYDRA_RUN_DIR="$RUN_DIR" \
    python3 "$SCRIPT_DIR/agents/hydra.py" >> "$RUN_DIR/hydra.log" 2>&1 &
  HYDRA_PID=$!
  ob_pid_record "$RUN_DIR/hydra.pid" "$HYDRA_PID"
  for _i in $(seq 1 20); do
    if ! kill -0 "$HYDRA_PID" 2>/dev/null; then
      echo "Error: beast-hydra exited during startup. Last log lines (.run/hydra.log):" >&2
      tail -n 15 "$RUN_DIR/hydra.log" >&2 2>/dev/null || true
      rm -f "$RUN_DIR/hydra.pid"; HYDRA_PID=""
      exit 1
    fi
    ob_hydra_answering "$HYDRA_URL" && { echo "beast-hydra answering on $HYDRA_URL (pid $HYDRA_PID)"; return 0; }
    sleep 0.5
  done
  echo "Error: beast-hydra did not answer /health within 10s (.run/hydra.log)" >&2
  exit 1
}

# After the managed engine is ready: give hydra up to HYDRA_READY_GRACE to
# see it and route. Not fatal — the same stance as the unmanaged inference
# wait: say so loudly, bring everything else up.
wait_hydra_routable() {
  [[ "${HYDRA:-false}" == "true" ]] || return 0
  local t0=$SECONDS
  until ob_hydra_ready "$HYDRA_URL"; do
    if (( SECONDS - t0 >= HYDRA_READY_GRACE )); then
      echo "WARNING: beast-hydra has NO routable default route after ${HYDRA_READY_GRACE}s (HYDRA_READY_GRACE)." >&2
      echo "         Chat through it fails until it does. Inspect: scripts/hydra.sh status" >&2
      return 1
    fi
    sleep 1
  done
  echo "beast-hydra routing on $HYDRA_URL"
}

wait_backend_ready() {
  local t0=$SECONDS
  until _infer_ready; do
    if (( SECONDS - t0 >= LLAMA_LOAD_GRACE )); then
      echo "WARNING: inference backend NOT ready at $LLAMA_BASE after ${LLAMA_LOAD_GRACE}s (OPENBEAST_LLAMA_LOAD_GRACE)." >&2
      echo "         It is not managed by this stack (INFERENCE_BACKEND=$INFERENCE_BACKEND, INFERENCE_MANAGED=false):" >&2
      echo "         start it where it runs (docs/DGX_SPARK_PLAN.md), or fix INFERENCE_URL in openbeast.conf." >&2
      echo "         Bringing up tools, WebUI and search anyway; the supervisor logs when it becomes ready." >&2
      return 1
    fi
    sleep 2
  done
}

# Fast boot (opt-in, OPENBEAST_FAST_BOOT / conf FAST_BOOT — resolved by
# lib/conf.sh): serve the tiny Qwen3-0.6B bridge on :8080 first so chat is
# live in seconds, then hot-swap to the configured model once the stack is up
# and its weights are warmed. The tool server / WebUI point at :8080 and are
# model-agnostic, so only llama-server swaps. Default off = load the real
# model directly (behavior unchanged).
# Unmanaged: there is no local llama-server to bridge or swap.
if [[ $MANAGED -eq 0 && "${FAST_BOOT:-false}" == "true" ]]; then
  echo "Fast boot: $(ob_backend_na "FAST_BOOT")"
  FAST_BOOT=false
fi
BOOTSTRAP_SERVE="serve-bootstrap.sh"
REAL_SERVE_SCRIPT="$SERVE_SCRIPT"
FAST_BOOT_ACTIVE=0
if [[ "${FAST_BOOT:-false}" == "true" && "$SERVE_SCRIPT" != "$BOOTSTRAP_SERVE" \
      && -x "$SCRIPT_DIR/scripts/$BOOTSTRAP_SERVE" ]]; then
  # The bridge weight must actually BE there. Nothing installs it: bootstrap.sh
  # downloads only the default model, so on a fresh install the 0.6B bridge is
  # absent even though it is registry-pinned, conf-exposed and documented.
  # Before this check, FAST_BOOT=true on such a box took the WHOLE stack down
  # at the health wait below ("Error: bootstrap model failed to load"), which
  # pointed at llama-server instead of at a file that was never fetched.
  #
  # Fast boot is an OPTIMISATION. A missing optimisation must degrade to the
  # normal path, never fail the boot.
  _fb_w="$( (source "$SCRIPT_DIR/scripts/lib/weights.sh" >/dev/null 2>&1; printf '%s' "${WEIGHTS_DIR:-}") || true )"
  # `|| true`: under pipefail a grep that matches nothing fails the whole
  # substitution and set -e aborts start.sh — over an OPTIMISATION, which is
  # the exact outcome the paragraph above exists to prevent.
  _fb_g="$(grep -oE 'WEIGHTS_DIR/[A-Za-z0-9._-]+\.gguf' "$SCRIPT_DIR/scripts/$BOOTSTRAP_SERVE" \
           | head -1 | sed 's|WEIGHTS_DIR/||' || true)"
  if [[ -n "$_fb_w" && -n "$_fb_g" && ! -f "$_fb_w/$_fb_g" ]]; then
    echo "Fast boot is on but its bridge weight is missing: $_fb_w/$_fb_g" >&2
    echo "  Nothing downloads it — bootstrap.sh fetches only the default model." >&2
    echo "  Get it:  ./scripts/fetch-weight.sh $_fb_g" >&2
    echo "  Continuing WITHOUT fast boot; the configured model loads directly." >&2
  else
    FAST_BOOT_ACTIVE=1
    SERVE_SCRIPT="$BOOTSTRAP_SERVE"
    echo "Fast boot: bringing up the bootstrap model for instant chat; $REAL_SERVE_SCRIPT loads next."
  fi
fi

launch_hydra

if [[ $MANAGED -eq 0 ]]; then
  echo "Waiting for the $(ob_backend_label) server at $LLAMA_BASE (not managed here — nothing is launched)..."
  if [[ "${MODEL_ROLLBACK:-true}" == "true" ]]; then
    echo "  $(ob_backend_na "Model rollback")"
  fi
  # Not ready is NOT fatal: the remote server is someone else's to start,
  # and taking tools, WebUI and search down with it helps nobody.
  INFER_UP=0
  if wait_backend_ready; then
    INFER_UP=1
    echo "$(ob_backend_label) ready at $LLAMA_BASE"
  fi
elif [[ $FAST_BOOT_ACTIVE -eq 1 ]]; then
  echo "Waiting for llama.cpp server to be ready..."
  # Phase 1 is the tiny bridge — it IS the fallback, so no rollback/record here.
  launch_llama
  if ! wait_llama_health; then
    echo "Error: bootstrap model failed to load — see output above" >&2
    exit 1
  fi
else
  echo "Waiting for llama.cpp server to be ready..."
  # Real model, with load-failure rollback to the last-known-good.
  if ! launch_and_wait; then
    echo "Error: llama-server exited during startup — see its output above" >&2
    echo "       (missing weight file or VRAM OOM; no healthy model to roll back to)" >&2
    exit 1
  fi
fi
if [[ $MANAGED -eq 1 ]]; then
  echo "llama.cpp server ready on http://localhost:8080"
  wait_hydra_routable || true
fi

# Regenerate the skill menu BEFORE warming: configure-webui.sh (backgrounded
# later) regenerates it too, and warming against the pre-regen text would
# prime a prefix that diverges from the prompt WebUI actually stores.
# Non-fatal — a broken generator must not block startup.
python3 "$SCRIPT_DIR/scripts/generate-skill-index.py" >/dev/null 2>&1 || true

# Normal boot warms here; fast boot warms after the swap (below) so the primed
# prefix belongs to the REAL model, not the throwaway bridge.
if [[ $MANAGED -eq 0 ]]; then
  echo "  $(ob_backend_na "KV-cache warming")"
elif [[ $FAST_BOOT_ACTIVE -eq 0 ]]; then
  warm_kv_cache
fi

echo "Starting identity tool server (WebUI OpenAPI tools) on http://localhost:3001..."
python3 -c 'import fastapi, uvicorn' 2>/dev/null \
  || { echo "Error: fastapi/uvicorn missing (pip install --user -r agents/requirements.txt)" >&2; exit 1; }
# Private, persistent workspace for files the chat model writes via the direct
# tools (conf.sh exports OPENBEAST_FILES_DIR; the tool server shards it per
# user when identity headers are present). Created 0700 so generated
# reports/charts aren't world-readable the way the old /tmp default was —
# matters on a multi-user / tailnet-exposed box. An EXISTING dir's mode is
# the user's choice: leave it alone, just warn if it's open.
if [[ ! -d "$OPENBEAST_FILES_DIR" ]]; then
  mkdir -p "$OPENBEAST_FILES_DIR" && chmod 700 "$OPENBEAST_FILES_DIR"
else
  _files_mode="$(stat -c '%a' "$OPENBEAST_FILES_DIR" 2>/dev/null || echo '')"
  if [[ -n "$_files_mode" && "${_files_mode: -2}" != "00" ]]; then
    echo "Warning: $OPENBEAST_FILES_DIR is group/world-accessible (mode $_files_mode);" >&2
    echo "         chmod 700 it if the model's files should stay private." >&2
  fi
fi
# agents/openapi_tools.py replaced mcpo here (docs/archive/IDENTITY_TOOLS_PLAN.md):
# it reads the WebUI identity headers mcpo dropped (per-user workspace
# sharding + audit log), and enforces BOTH RBAC Phase 2 keys in one process
# — admin key = all tools, guest key = web_search/fetch only, no keys = open
# Phase-1 behavior. Keys come from conf.sh env (scripts/setup-mcpo-keys.sh).
if [[ -n "${OPENBEAST_MCPO_ADMIN_KEY:-}" && -n "${OPENBEAST_MCPO_GUEST_KEY:-}" ]]; then
  echo "  (RBAC Phase 2 keys active: admin + guest profiles on :3001)"
fi
python3 "$SCRIPT_DIR/agents/openapi_tools.py" &
MCPO_PID=$!
echo "$MCPO_PID" > "$RUN_DIR/mcpo.pid"
# Verify it actually serves — a blind sleep once masked a dead tool server.
MCPO_UP=0
for _i in $(seq 1 30); do
  if ! kill -0 "$MCPO_PID" 2>/dev/null; then
    echo "Error: tool server exited during startup — see output above" >&2
    exit 1
  fi
  curl -s -m 2 "http://$HEALTH_HOST:3001/health" >/dev/null 2>&1 && { MCPO_UP=1; break; }
  sleep 1
done
[[ $MCPO_UP -eq 1 ]] || { echo "Error: tool server not serving after 30s" >&2; exit 1; }
echo "Tool server ready on http://localhost:3001"

# beast-instinct (opt-in; docs/BEAST_INSTINCT_PLAN.md §5.9) — the decision
# plane. Its CPU scorer first (INSTINCT_SCORER=true: Qwen3-0.6B on
# 127.0.0.1:8082, CPU only, no GPU lease), then the service
# (INSTINCT=true: 127.0.0.1:INSTINCT_PORT). Deliberately NON-FATAL: every
# consumer fails OPEN to today's behaviour when instinct is absent, so an
# instinct that will not start is a warning, never a reason to take the
# stack down. Started before the router, which may consult it.
INSTINCT_STARTED=0
if [[ "${INSTINCT_SCORER:-false}" == "true" ]]; then
  _isc_port="${INSTINCT_SCORER_PORT:-8082}"
  if _pid_alive "$RUN_DIR/instinct-scorer.pid" "$(_pid_pattern instinct-scorer)"; then
    echo "beast-instinct scorer already running (pid $(cat "$RUN_DIR/instinct-scorer.pid")) — leaving it alone."
  else
    echo "Starting beast-instinct CPU scorer on http://127.0.0.1:${_isc_port}..."
    ( umask 077; : >> "$RUN_DIR/instinct-scorer.log" )
    "$SCRIPT_DIR/scripts/serve-instinct-scorer.sh" >> "$RUN_DIR/instinct-scorer.log" 2>&1 &
    INSTINCT_SCORER_PID=$!
    ob_pid_record "$RUN_DIR/instinct-scorer.pid" "$INSTINCT_SCORER_PID"
    _isc_up=0
    for _i in $(seq 1 60); do
      kill -0 "$INSTINCT_SCORER_PID" 2>/dev/null || break
      ob_llama_ready "http://127.0.0.1:${_isc_port}" && { _isc_up=1; break; }
      sleep 1
    done
    if [[ $_isc_up -eq 1 ]]; then
      echo "beast-instinct scorer ready on http://127.0.0.1:${_isc_port} (pid $INSTINCT_SCORER_PID)"
    elif ! kill -0 "$INSTINCT_SCORER_PID" 2>/dev/null; then
      echo "WARNING: the beast-instinct scorer exited during startup (.run/instinct-scorer.log);" >&2
      echo "         instinct falls back to its rules/linear engines." >&2
      tail -n 5 "$RUN_DIR/instinct-scorer.log" >&2 2>/dev/null || true
      rm -f "$RUN_DIR/instinct-scorer.pid"; INSTINCT_SCORER_PID=""
    else
      echo "WARNING: the beast-instinct scorer is not ready after 60s — still loading? (.run/instinct-scorer.log)" >&2
    fi
  fi
fi
if [[ "${INSTINCT:-false}" == "true" ]]; then
  echo "Starting beast-instinct on http://127.0.0.1:${INSTINCT_PORT}..."
  # instinct.sh owns the pre-bind check, the 0600 key and the pidfile. The
  # config loader lints engine URLs against the primary and hydra (I7).
  if INSTINCT_CONFIG="$INSTINCT_CONFIG" INSTINCT_PORT="$INSTINCT_PORT" \
     INSTINCT_RUN_DIR="$RUN_DIR" HYDRA_URL="${HYDRA_URL:-}" \
       "$SCRIPT_DIR/scripts/instinct.sh" up; then
    INSTINCT_STARTED=1
  else
    echo "WARNING: beast-instinct did not start (.run/instinct.log) — every consumer fails open" >&2
    echo "         to today's behaviour without it. Retry: scripts/instinct.sh up" >&2
  fi
fi

# Agent-spawn router (opt-in, AGENT_ROUTER=true). Sits on ROUTER_PORT in front
# of llama-server (8080); frontends point at it via OPENBEAST_MODEL_URL. Needs
# MCPO up (it spawns via MCPO). llama-server stays direct on 8080 so evals and
# spawned agents are never routed. See docs/RESEARCH_FINDINGS §8-11.
if [[ "${AGENT_ROUTER:-false}" == "true" ]]; then
  echo "Starting agent-spawn router on http://localhost:${ROUTER_PORT}..."
  # Upstreams on the PROBE host, not a hardcoded 127.0.0.1: llama-server and
  # the tool server bind BIND_HOST, and a socket bound to a specific LAN or
  # tailnet address refuses 127.0.0.1 — every routed request was a 502 while
  # every health probe (which did follow BIND_HOST) reported green.
  # Opt-in extras go into the ROUTER's environment only (never exported
  # stack-wide), and only when on — a default stack launches it exactly as
  # before (`env` with no assignments just execs python3, same pid):
  #   ROUTER_INSTINCT       the router's ceiling for router.spawn_intent
  #                         (+ where the instinct service answers);
  #   ROUTER_CLASSIFY_MODEL "classify" under HYDRA=true when hydra.toml has
  #                         that route, so the generative classify can be
  #                         placed off the one-slot primary. Without the route
  #                         it names HYDRA_DEFAULT_MODEL, the route agents use.
  _router_env=()
  if [[ "${ROUTER_INSTINCT:-off}" != "off" ]]; then
    _router_env+=(ROUTER_INSTINCT="$ROUTER_INSTINCT" INSTINCT_URL="http://127.0.0.1:${INSTINCT_PORT}")
    # The key file the service mints (its config's [service].key_file) —
    # the PATH only; the client reads the 0600 file itself.
    _ikf="$(PYTHONPATH="$SCRIPT_DIR/agents" INSTINCT_CONFIG="$INSTINCT_CONFIG" \
            python3 -m instinct.cli cfg 2>/dev/null | sed -n 's/^INSTINCT_KEY_FILE=//p' || true)"
    [[ -n "$_ikf" ]] && _router_env+=(INSTINCT_KEY_FILE="$_ikf")
    if [[ "${INSTINCT:-false}" != "true" ]]; then
      echo "  Note: ROUTER_INSTINCT=$ROUTER_INSTINCT but INSTINCT=false — the router fails open to" >&2
      echo "        today's path until an instinct service answers (scripts/instinct.sh up)." >&2
    fi
  fi
  if [[ "${HYDRA:-false}" == "true" && "$HYDRA_CLASSIFY_ROUTE" == "true" ]]; then
    _router_env+=(ROUTER_CLASSIFY_MODEL=classify)
  elif [[ "${HYDRA:-false}" == "true" ]]; then
    # Name the default route: a model-less classify body only works through
    # hydra's unknown_model = "default" fallback, and "404" would quietly
    # turn every classification (and so every spawn) into an error.
    _router_env+=(ROUTER_CLASSIFY_MODEL="$HYDRA_DEFAULT_MODEL")
  fi
  OPENBEAST_ROUTER_PORT="$ROUTER_PORT" \
  OPENBEAST_LLAMA_UPSTREAM="$CONSUMER_BASE" \
  OPENBEAST_MCPO_URL="http://$HEALTH_HOST:3001" \
    env ${_router_env[@]+"${_router_env[@]}"} python3 "$SCRIPT_DIR/agents/router.py" &
  ROUTER_PID=$!
  echo "$ROUTER_PID" > "$RUN_DIR/router.pid"
  ROUTER_UP=0
  for _i in $(seq 1 20); do
    if ! kill -0 "$ROUTER_PID" 2>/dev/null; then
      echo "Error: agent router exited during startup — see output above" >&2; exit 1
    fi
    curl -s -m 2 "http://127.0.0.1:${ROUTER_PORT}/health" >/dev/null 2>&1 && { ROUTER_UP=1; break; }
    sleep 1
  done
  [[ $ROUTER_UP -eq 1 ]] || { echo "Error: agent router not serving after 20s" >&2; exit 1; }
  echo "Agent router ready on http://localhost:${ROUTER_PORT} (frontends route through it)"
fi

# beast-gate (opt-in, EDGE_GATE=true) — the identity-aware inference edge for
# REMOTE clients (docs/BEAST_SLOT.md). Local frontends are unaffected: they
# keep talking to llama-server / the router on loopback. Publishing changes
# only on the tailnet side (setup-tailscale.sh points :8443 here).
if [[ "${EDGE_GATE:-false}" == "true" ]]; then
  echo "Starting beast-gate on http://localhost:${EDGE_PORT}..."
  # Upstream on the probe host for the same reason as the router's above.
  # Under HYDRA=true the gate's upstream is hydra (CONSUMER_BASE) and it
  # vouches for the device it authenticated with X-Hydra-Caller.
  OPENBEAST_REPO_DIR="$SCRIPT_DIR" \
  OPENBEAST_LLAMA_UPSTREAM="$CONSUMER_BASE" \
    python3 "$SCRIPT_DIR/agents/edge.py" &
  EDGE_PID=$!
  echo "$EDGE_PID" > "$RUN_DIR/edge.pid"
  EDGE_UP=0
  for _i in $(seq 1 20); do
    if ! kill -0 "$EDGE_PID" 2>/dev/null; then
      echo "Error: beast-gate exited during startup — see output above" >&2; exit 1
    fi
    curl -s -m 2 "http://$HEALTH_HOST:${EDGE_PORT}/gate/health" >/dev/null 2>&1 && { EDGE_UP=1; break; }
    sleep 1
  done
  [[ $EDGE_UP -eq 1 ]] || { echo "Error: beast-gate not serving after 20s" >&2; exit 1; }
  # `|| true`: under set -e a non-matching grep here would abort start.sh
  # AFTER the stack is already up, tearing down a healthy rig over a cosmetic
  # status line.
  # The gate's introspection detail needs proof-of-locality (the transport
  # peer is useless: tailscale serve proxies from 127.0.0.1). The token file
  # is 0600 and only readable on this box.
  _EDGE_TOK=$(cat "$RUN_DIR/edge-local.token" 2>/dev/null || true)
  # The header goes through ob_curl_hdr (curl --config on fd 3), never argv:
  # /proc/*/cmdline is world-readable, and this token unlocks /gate/*.
  _EDGE_AUTH=$(ob_curl_hdr "${_EDGE_TOK:+X-OpenBeast-Local: $_EDGE_TOK}" -s -m 2 "http://$HEALTH_HOST:${EDGE_PORT}/gate/health" 2>/dev/null | grep -o '"auth":"[a-z]*"' | cut -d'"' -f4 || true)
  echo "beast-gate ready on http://localhost:${EDGE_PORT} (auth=${_EDGE_AUTH:-?})"
  if [[ "$_EDGE_AUTH" == "closed" ]]; then
    echo "  No devices enrolled yet — remote clients will get 401 until:"
    echo "    ./scripts/clients.sh enroll <device-id>"
  fi
fi

# beast-chat (opt-in, BEAST_CHAT=true) — the operator console for this rig's
# own agent and job sessions (docs/BEAST_CHAT.md). Loopback like the tool
# server; setup-tailscale.sh --publish-chat is what makes it reachable from a
# phone. Deliberately NON-FATAL at every step below: the console is an
# observability surface, and a stack that refuses to boot because its window
# is cracked is worse than a stack with no window. Contrast the tool server,
# whose absence means the model has no tools at all.
if [[ "${BEAST_CHAT:-false}" == "true" ]]; then
  if [[ ! -f "$SCRIPT_DIR/agents/chat_server.py" ]]; then
    echo "Warning: BEAST_CHAT=true but agents/chat_server.py is missing — skipping." >&2
  else
    if _pid_alive "$RUN_DIR/chat.pid" "$(_pid_pattern chat)"; then
      # The same guard, for the same reason as beast-artifact below: this
      # branch overwrote chat.pid with an unbindable replacement in the
      # orphaned-stack state too. The review named the artifact branch; the
      # defect is the class, and fixing one of two identical holes is not
      # fixing it.
      echo "beast-chat already running (pid $(cat "$RUN_DIR/chat.pid")) — leaving it alone."
    else
      echo "Starting beast-chat console on http://localhost:${CHAT_PORT:-3003}..."
      # Not $HEALTH_HOST: chat_server binds OPENBEAST_CHAT_BIND (loopback)
      # whatever BIND_HOST says, so probing the LAN address reported a
      # healthy console as down.
      _chat_host="$(ob_probe_host "${OPENBEAST_CHAT_BIND:-127.0.0.1}")"    # set -u: normally unset
      _rc=0
      _spawn_ready beast-chat chat "${CHAT_PORT:-3003}" \
        "http://$_chat_host:${CHAT_PORT:-3003}/api/chat/health" \
        ob_exec_chat_server "$SCRIPT_DIR/agents/chat_server.py" || _rc=$?
      CHAT_PID="$SPAWN_PID"
      [[ -n "$CHAT_PID" ]] && CHAT_OWNED=1
      if [[ $_rc -eq 0 ]]; then
        echo "beast-chat ready on http://localhost:${CHAT_PORT:-3003}"
        if [[ -z "${CHAT_OPERATORS:-}" ]]; then
          echo "  CHAT_OPERATORS is empty — once published, EVERY tailnet login can"
          echo "  read every session. Set it in openbeast.conf to pin it to you."
        fi
      elif [[ $_rc -eq 1 ]]; then
        # A pid left on record after the process died is a number the kernel
        # will hand to something else — _spawn_ready removed it.
        echo "Warning: beast-chat not serving after 20s — the rest of the stack is fine." >&2
        echo "         Diagnose with: ./scripts/doctor.sh" >&2
      fi
    fi
  fi
fi

# beast-artifact (opt-in, BEAST_ARTIFACT=true) — the publish-and-view service
# for model- or script-authored HTML (docs/BEAST_ARTIFACT_PLAN.md). Binds
# BIND_HOST (loopback by default); setup-tailscale.sh --publish-artifact puts
# it on :8446 for phones. A login header counts only from a peer on this host
# (loopback, or the bind address itself — how tailscale serve reaches a LAN
# bind), so a LAN caller dialling the port directly is anonymous: 404.
# It SERVES the pages; it is not on the publish path for the tools —
# publish_artifact/list_artifacts call the store (agents/artifact.py) in
# process. scripts/artifact.sh is the one that speaks HTTP to it, with the
# proof-of-locality token. So a failure here costs viewing, not publishing.
if [[ "${BEAST_ARTIFACT:-false}" == "true" ]]; then
  # [17] GUARD THE SPAWN, not the delete. In the orphaned-stack state (the
  # supervisor was SIGKILLed, so its EXIT trap never reaped the children) a
  # fresh ./start.sh passed the supervisor guard and spawned a replacement
  # that CANNOT BIND the port — then wrote its pid over the pidfile and, when
  # the health probe failed, deleted the file, erasing the still-live server's
  # recorded pid. The cost is not cosmetic: scripts/healthcheck.sh --restart
  # (driven every 5 minutes by openbeast-watchdog.timer) then finds no pid,
  # never kills the wedged orphan, and loops on an unbindable replacement
  # forever. Refusing to spawn prevents the destroying write in the first
  # place, rather than trying to patch the erasure after it happened.
  if _pid_alive "$RUN_DIR/artifact.pid" "$(_pid_pattern artifact)"; then
    echo "beast-artifact already running (pid $(cat "$RUN_DIR/artifact.pid")) — leaving it alone."
  else
    echo "Starting beast-artifact on http://localhost:${ARTIFACT_PORT:-3004}..."
    _rc=0
    _spawn_ready beast-artifact artifact "${ARTIFACT_PORT:-3004}" \
      "http://$HEALTH_HOST:${ARTIFACT_PORT:-3004}/api/artifacts/health" \
      env OPENBEAST_REPO_DIR="$SCRIPT_DIR" OPENBEAST_ARTIFACT_PORT="${ARTIFACT_PORT:-3004}" \
        python3 "$SCRIPT_DIR/agents/artifact_server.py" || _rc=$?
    ARTIFACT_PID="$SPAWN_PID"
    [[ -n "$ARTIFACT_PID" ]] && ARTIFACT_OWNED=1
    if [[ $_rc -eq 0 ]]; then
      echo "beast-artifact ready on http://localhost:${ARTIFACT_PORT:-3004} (publish: ./scripts/artifact.sh publish <file.html>)"
    elif [[ $_rc -eq 1 ]]; then
      # WARNING, not fatal: this is an opt-in cosmetic service. Taking the whole
      # stack — the model included — down because a page viewer failed to bind
      # is a far worse outcome than not being able to open an artifact URL.
      echo "WARNING: beast-artifact did not come up (see its output above)." >&2
      echo "         The rest of the stack, llama-server included, is unaffected;" >&2
      echo "         artifact URLs will not serve until it starts. Publishing" >&2
      echo "         through the model's tools still works (in-process store)." >&2
      echo "         Retry it on its own:  ./scripts/healthcheck.sh --restart" >&2
      [[ -n "$ARTIFACT_PID" ]] && kill "$ARTIFACT_PID" 2>/dev/null || true
      rm -f "$RUN_DIR/artifact.pid"
      ARTIFACT_PID=""
    fi
  fi
fi

echo "Starting Open WebUI..."
# Boot race: as a user unit, openbeast.service can't order itself after the
# SYSTEM docker.service (user managers ignore system-unit deps), so at boot
# we may get here — with the model already loaded — before dockerd is up.
# Failing under set -e would tear the whole loaded stack down; wait instead.
if ! docker info >/dev/null 2>&1; then
  echo "  (docker daemon not ready — waiting up to 60s)"
  for _i in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
fi
# NOT fatal: by this point the model is loaded (multi-minute cost) and the
# tool server is serving. A frontend-only failure (docker wait above expired,
# registry hiccup on the pinned digest) must not tear the working stack down
# via set -e.
# Merge any enabled compose-kind extension fragments (scripts/lib/extensions.sh)
# alongside the core compose file so optional services come up with the stack.
COMPOSE_FILES=(-f "$SCRIPT_DIR/docker-compose.yml")
while IFS= read -r _cf; do [[ -n "$_cf" ]] && COMPOSE_FILES+=("$_cf"); done < <(ob_ext_compose_args)
# OFFLINE: compose's default pull policy reaches a registry whenever the local
# store lacks the pinned digest, which on a closed network is a stall followed
# by a confusing failure. `--pull never` turns that into an immediate, honest
# "the image is not here".
COMPOSE_UP=(up -d)
if ob_offline; then
  COMPOSE_UP+=(--pull never)
fi
if ! docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_UP[@]}"; then
  echo "Warning: frontend containers failed to start — model API (:8080) and tools (:3001) are still up. Retry with: docker compose up -d" >&2
  if ob_offline; then
    echo "         OFFLINE=true, so nothing was pulled. Images are the fourth of" >&2
    echo "         the four fetches a closed network cannot do. Move them with" >&2
    echo "         docker save/load from a connected box — and note the trap:" >&2
    echo "         docker-compose.yml pins by DIGEST, and a digest-pinned" >&2
    echo "         reference cannot be satisfied by a locally retagged image," >&2
    echo "         so the compose reference has to change too." >&2
  fi
fi

# Launch enabled process-kind extensions (each run.sh execs its server in the
# foreground; we background + pidfile it, and cleanup() reaps them on exit).
while IFS= read -r _ext; do
  [[ -z "$_ext" ]] && continue
  # A hand-edited EXTENSIONS="dashboard/" names a real directory, and the
  # pidfile write below then fails under set -e — tearing the stack down
  # after the model load. Skip the word, loudly (ext.sh validates the same).
  if [[ ! "$_ext" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
    echo "Warning: skipping invalid extension name '$_ext' in EXTENSIONS (fix it in openbeast.conf)." >&2
    continue
  fi
  # The [17] guard, for extensions: a live one (an orphan of a SIGKILLed
  # supervisor) keeps its port, so a replacement cannot bind — and writing
  # its pid over the record made the live one unreapable.
  if ob_recorded_pid_ours "$RUN_DIR/ext-$_ext.pid" "$(_ob_ere "$REPO_DIR/extensions/$_ext/")"; then
    echo "Extension $_ext already running (pid $(cat "$RUN_DIR/ext-$_ext.pid")) — leaving it alone."
    continue
  fi
  echo "Starting extension: $_ext"
  "$REPO_DIR/extensions/$_ext/run.sh" >>"$RUN_DIR/ext-$_ext.log" 2>&1 &
  ob_pid_record "$RUN_DIR/ext-$_ext.pid" "$!"
done < <(ob_ext_processes)

# Configure Open WebUI (tool server + native function calling) in background
"$SCRIPT_DIR/scripts/configure-webui.sh" &
CONFIG_PID=$!

echo ""
echo "Stack is running:"
if [[ $MANAGED -eq 1 ]]; then
  echo "  Model server:  http://localhost:8080"
elif [[ ${INFER_UP:-0} -eq 1 ]]; then
  echo "  Model server:  $LLAMA_BASE ($(ob_backend_label), not managed here)"
else
  echo "  Model server:  NOT READY at $LLAMA_BASE ($(ob_backend_label), not managed here)"
fi
echo "  MCPO tools:    http://localhost:3001 (OpenAPI docs at /docs)"
echo "  Open WebUI:    http://localhost:3000"
echo "  OpenCode:      run 'opencode' in any project directory"
if [[ "${HYDRA:-false}" == "true" ]]; then
  echo "  beast-hydra:   $HYDRA_URL (${HYDRA_CHECK_SUMMARY:-?}) — scripts/hydra.sh status"
fi
if [[ ${INSTINCT_STARTED:-0} -eq 1 ]]; then
  echo "  beast-instinct: http://127.0.0.1:${INSTINCT_PORT} (router ceiling: ${ROUTER_INSTINCT:-off}) — scripts/instinct.sh status"
fi
echo ""
if [[ $DAEMONIZED -eq 1 ]]; then
  echo "Running detached. Stop with ./stop.sh; status with ./start.sh --status."
else
  echo "Press Ctrl+C to stop the servers (containers keep running — cheap,"
  echo "and they auto-reconnect on the next start). Full stop: ./stop.sh."
fi

# ---- fast-boot swap: bridge -> real model -----------------------------------
# The stack is up on the bootstrap bridge and chat is live. Warm the real
# model's weights into the page cache (no VRAM cost) so its load is
# GPU-upload-bound (~30s) rather than disk+GPU (~1-2 min), shrinking the brief
# chat pause, then swap it in behind the same :8080. The record for
# healthcheck --restart is rewritten to the REAL script so a later auto-restart
# never resurrects the bridge.
if [[ $FAST_BOOT_ACTIVE -eq 1 ]]; then
  echo ""
  echo "Bootstrap model is live — chat now while $REAL_SERVE_SCRIPT loads in the background."
  ( _wd="$( (source "$SCRIPT_DIR/scripts/lib/weights.sh" >/dev/null 2>&1; printf '%s' "${WEIGHTS_DIR:-}") || true )"
    _gg="$(grep -oE 'WEIGHTS_DIR/[A-Za-z0-9._-]+\.gguf' "$SCRIPT_DIR/scripts/$REAL_SERVE_SCRIPT" | head -1 | sed 's|WEIGHTS_DIR/||')"
    [[ -n "$_wd" && -n "$_gg" && -f "$_wd/$_gg" ]] && cat "$_wd/$_gg" >/dev/null 2>&1 ) &
  _WARM_PID=$!
  for _i in $(seq 1 90); do kill -0 "$_WARM_PID" 2>/dev/null || break; sleep 1; done
  kill "$_WARM_PID" 2>/dev/null || true; wait "$_WARM_PID" 2>/dev/null || true
  echo "Swapping in $REAL_SERVE_SCRIPT (chat pauses ~30s during the model handoff)..."
  SERVE_SCRIPT="$REAL_SERVE_SCRIPT"
  echo "$SERVE_SCRIPT" > "$RUN_DIR/serve-script"
  kill "$LLAMA_PID" 2>/dev/null || true; wait "$LLAMA_PID" 2>/dev/null || true
  # Rollback applies here too: if the real model won't load, revert to
  # last-known-good rather than leaving the (now bridge-less) stack dead.
  if ! launch_and_wait; then
    echo "Error: $REAL_SERVE_SCRIPT failed to load during the fast-boot swap," >&2
    echo "       and no healthy model to roll back to. The bridge is already gone." >&2
    exit 1
  fi
  echo "Full model live: $SERVE_SCRIPT on http://localhost:8080."
  # The bridge, not this model, is what configure-webui.sh saw. Fix the rows.
  reconfigure_webui_for_model
  warm_kv_cache
  FAST_BOOT_ACTIVE=0
fi

# Unmanaged: nothing to relaunch. Stay up (the trap still owns shutdown)
# and log only TRANSITIONS of the remote server's readiness, so a Spark
# rebooting shows up in stack.log without a line every 30 seconds.
if [[ $MANAGED -eq 0 ]]; then
  _up="${INFER_UP:-1}"
  while true; do
    sleep 30 & IDLE_PID=$!
    wait "$IDLE_PID" || true
    IDLE_PID=""
    if _infer_ready; then
      [[ $_up -eq 0 ]] && echo "$(date '+%H:%M:%S') $(ob_backend_label) at $LLAMA_BASE is ready again."
      _up=1
    else
      [[ $_up -eq 1 ]] && echo "$(date '+%H:%M:%S') $(ob_backend_label) at $LLAMA_BASE is NOT ready — not managed here, so nothing is restarted."
      _up=0
    fi
  done
fi
# Supervise with bounded self-healing: an unexpected llama-server death
# (VRAM OOM, crash, a healthcheck --restart kill) gets up to 3 relaunches;
# staying healthy 5+ minutes refills the budget. ./stop.sh's TERM sets
# STOPPING via the trap, so a shutdown is never mistaken for a crash.
RESTARTS=0
while true; do
  # Stamped only once launch_and_wait has proved the model READY, so the
  # 5-minute refill below means "served for 5 minutes", never "spent 5
  # minutes loading and then died" (which relaunched a model that could
  # never finish loading, forever).
  LAUNCHED_AT=$SECONDS
  rc=0
  wait $LLAMA_PID || rc=$?
  [[ $STOPPING -eq 1 ]] && exit 0
  [[ $((SECONDS - LAUNCHED_AT)) -gt 300 ]] && RESTARTS=0
  if [[ $RESTARTS -ge 3 ]]; then
    echo "llama-server exited (status $rc) with the restart budget spent — stopping the stack."
    _mark_gave_up "llama-server exited $((RESTARTS + 1)) times (status $rc)"
    exit 1
  fi
  RESTARTS=$((RESTARTS + 1))
  echo "llama-server exited unexpectedly (status $rc) — relaunching ($RESTARTS/3) in 5s..."
  sleep 5
  # launch_and_wait rolls back to the last-known-good model if the current one
  # won't come back (e.g. a weight went missing under it) rather than dying.
  if ! launch_and_wait; then
    echo "Relaunched llama-server died before becoming healthy — stopping the stack." >&2
    _mark_gave_up "relaunched llama-server never became healthy"
    exit 1
  fi
  echo "llama-server healthy again after restart $RESTARTS ($SERVE_SCRIPT)."
done
