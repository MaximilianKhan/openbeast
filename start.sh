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
_pid_alive() { # _pid_alive <pidfile> [cmdline-pattern]
  # Alive AND identity-checked: a stale pidfile whose PID was recycled by an
  # unrelated process must not count as "running". If /proc/<pid>/cmdline is
  # unreadable (exotic /proc, zombie) fall back to the plain liveness check.
  local pat="${2:-start\.sh|llama|mcpo|openapi_tools|router|chat_server}" pid cmd
  [[ -f "$1" ]] || return 1
  pid="$(cat "$1" 2>/dev/null)" && [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  if cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" && [[ -n "$cmd" ]]; then
    [[ "$cmd" =~ $pat ]] || return 1
  fi
  return 0
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
    *)          echo 'start\.sh|llama|mcpo|openapi_tools|router|edge|chat_server|artifact_server' ;;
  esac
}

if [[ $STATUS -eq 1 ]]; then
  echo "OpenBeast stack status:"
  for name in supervisor llama mcpo router edge chat artifact; do
    f="$RUN_DIR/$name.pid"
    if _pid_alive "$f" "$(_pid_pattern "$name")"; then
      echo "  $name: running (pid $(cat "$f"))"
    else
      echo "  $name: not running"
    fi
  done
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
SERVE_SCRIPT="${SERVE_SCRIPT:-$DEFAULT_SERVE_SCRIPT}"

if [[ ! -x "$SCRIPT_DIR/scripts/$SERVE_SCRIPT" ]]; then
  echo "Error: scripts/$SERVE_SCRIPT not found or not executable" >&2
  exit 1
fi

# Probe where services actually listen: loopback answers for loopback and
# wildcard binds; a specific LAN/tailnet address must be probed directly.
# Used by the daemon launcher's readiness probes AND the supervisor below.
case "$BIND_HOST" in
  127.*|localhost|0.*) HEALTH_HOST="127.0.0.1" ;;
  *)                   HEALTH_HOST="$BIND_HOST" ;;
esac

# ---- daemon launcher: spawn the detached supervisor, wait for readiness ----
if [[ $DAEMON -eq 1 ]]; then
  mkdir -p "$RUN_DIR"
  if _pid_alive "$SUP_PID_FILE" "$(_pid_pattern supervisor)"; then
    echo "Stack already running (supervisor pid $(cat "$SUP_PID_FILE"))." >&2
    echo "Check ./start.sh --status, or ./stop.sh first." >&2
    exit 1
  fi
  echo "Starting OpenBeast in the background ($SERVE_SCRIPT)..."
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
    SETENV_ARGS=()
    while IFS= read -r _var; do
      if [[ -n "$_var" && "$_var" != *KEY* && "$_var" != *PASSWORD* && "$_var" != *SECRET* ]]; then
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

  echo "Waiting for the model to load (log: .run/stack.log)..."
  for i in $(seq 1 300); do
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
    if curl -s -m 2 "http://$HEALTH_HOST:8080/health" >/dev/null 2>&1 \
       && curl -s -m 2 "http://$HEALTH_HOST:3001/health" >/dev/null 2>&1 \
       && [[ $ROUTER_READY -eq 1 ]] && [[ $EDGE_READY -eq 1 ]]; then
      echo ""
      echo "Stack is up:"
      echo "  Model server:  http://localhost:8080"
      echo "  MCPO tools:    http://localhost:3001 (OpenAPI docs at /docs)"
      echo "  Open WebUI:    http://localhost:3000"
      if [[ "${AGENT_ROUTER:-false}" == "true" ]]; then
        echo "  Agent router:  http://localhost:${ROUTER_PORT} (frontends route through it)"
      fi
      if [[ "${EDGE_GATE:-false}" == "true" ]]; then
        echo "  beast-gate:    http://localhost:${EDGE_PORT:-8090} (remote clients arrive here)"
      fi
      echo "  Status:        ./start.sh --status    Stop: ./stop.sh"
      exit 0
    fi
    if [[ $i -gt 10 ]] && ! _pid_alive "$SUP_PID_FILE" "$(_pid_pattern supervisor)"; then
      echo "Error: supervisor exited during startup. Last log lines:" >&2
      tail -20 "$RUN_DIR/stack.log" 2>/dev/null >&2 || true
      exit 1
    fi
    sleep 2
  done
  echo "Timed out after 10 min — inspect ./start.sh --status and .run/stack.log" >&2
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
echo $$ > "$SUP_PID_FILE"
# Record which serve script this stack runs so healthcheck.sh --restart can
# relaunch the SAME model instead of assuming the default.
echo "$SERVE_SCRIPT" > "$RUN_DIR/serve-script"

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
    rm -f "$SUP_PID_FILE"
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
  # Reap any process-kind extensions we launched.
  for _pf in "$RUN_DIR"/ext-*.pid; do
    [[ -e "$_pf" ]] || continue
    kill "$(cat "$_pf" 2>/dev/null)" 2>/dev/null && echo "extension stopped ($(basename "$_pf" .pid | sed 's/^ext-//'))."
    rm -f "$_pf"
  done
  rm -f "$RUN_DIR/supervisor.pid" "$RUN_DIR/llama.pid" "$RUN_DIR/mcpo.pid" \
        "$RUN_DIR/router.pid" "$RUN_DIR/edge.pid"
  # ...but only the pidfiles of servers WE started. Removing a live server's
  # recorded pid is what makes an orphan unreapable, which is the whole point
  # of the [17] guard above.
  # ...and only while the file still names OUR child. healthcheck.sh --restart
  # replaces a crashed server and records the replacement's pid in the same
  # file; "we started one once" (CHAT_OWNED) is not "this pid is ours", and
  # deleting the replacement's record recreates the unreapable-orphan state.
  _rm_own_pidfile() {                # _rm_own_pidfile <pidfile> <our-pid>
    [[ -n "${2:-}" && -f "$1" ]] || return 0
    [[ "$(cat "$1" 2>/dev/null || true)" == "$2" ]] && rm -f "$1"
    return 0
  }
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

wait_llama_health() { # returns 1 if the process dies before becoming healthy
  until curl -s "http://$HEALTH_HOST:8080/health" > /dev/null 2>&1; do
    kill -0 "$LLAMA_PID" 2>/dev/null || return 1
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
    python3 - "$SYS" <<'WARM' >/dev/null 2>&1 || true
import json, sys, urllib.request
body=json.dumps({"messages":[{"role":"system","content":sys.argv[1].strip()},
    {"role":"user","content":"hi"}],"max_tokens":1,"temperature":0,
    "chat_template_kwargs":{"enable_thinking":False}}).encode()
try:
    urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:8080/v1/chat/completions", data=body,
        headers={"Content-Type":"application/json"}), timeout=60).read()
except Exception:
    pass
WARM
    echo "  (KV cache warmed with the system prompt)" ) &
}

# Fast boot (opt-in, OPENBEAST_FAST_BOOT / conf FAST_BOOT — resolved by
# lib/conf.sh): serve the tiny Qwen3-0.6B bridge on :8080 first so chat is
# live in seconds, then hot-swap to the configured model once the stack is up
# and its weights are warmed. The tool server / WebUI point at :8080 and are
# model-agnostic, so only llama-server swaps. Default off = load the real
# model directly (behavior unchanged).
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

echo "Waiting for llama.cpp server to be ready..."
if [[ $FAST_BOOT_ACTIVE -eq 1 ]]; then
  # Phase 1 is the tiny bridge — it IS the fallback, so no rollback/record here.
  launch_llama
  if ! wait_llama_health; then
    echo "Error: bootstrap model failed to load — see output above" >&2
    exit 1
  fi
else
  # Real model, with load-failure rollback to the last-known-good.
  if ! launch_and_wait; then
    echo "Error: llama-server exited during startup — see its output above" >&2
    echo "       (missing weight file or VRAM OOM; no healthy model to roll back to)" >&2
    exit 1
  fi
fi
echo "llama.cpp server ready on http://localhost:8080"

# Regenerate the skill menu BEFORE warming: configure-webui.sh (backgrounded
# later) regenerates it too, and warming against the pre-regen text would
# prime a prefix that diverges from the prompt WebUI actually stores.
# Non-fatal — a broken generator must not block startup.
python3 "$SCRIPT_DIR/scripts/generate-skill-index.py" >/dev/null 2>&1 || true

# Normal boot warms here; fast boot warms after the swap (below) so the primed
# prefix belongs to the REAL model, not the throwaway bridge.
[[ $FAST_BOOT_ACTIVE -eq 0 ]] && warm_kv_cache

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

# Agent-spawn router (opt-in, AGENT_ROUTER=true). Sits on ROUTER_PORT in front
# of llama-server (8080); frontends point at it via OPENBEAST_MODEL_URL. Needs
# MCPO up (it spawns via MCPO). llama-server stays direct on 8080 so evals and
# spawned agents are never routed. See docs/RESEARCH_FINDINGS §8-11.
if [[ "${AGENT_ROUTER:-false}" == "true" ]]; then
  echo "Starting agent-spawn router on http://localhost:${ROUTER_PORT}..."
  OPENBEAST_ROUTER_PORT="$ROUTER_PORT" \
  OPENBEAST_LLAMA_UPSTREAM="http://127.0.0.1:8080" \
  OPENBEAST_MCPO_URL="http://127.0.0.1:3001" \
    python3 "$SCRIPT_DIR/agents/router.py" &
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
  OPENBEAST_REPO_DIR="$SCRIPT_DIR" \
  OPENBEAST_LLAMA_UPSTREAM="http://127.0.0.1:8080" \
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
  _EDGE_AUTH=$(curl -s -m 2 -H "X-OpenBeast-Local: ${_EDGE_TOK}" "http://$HEALTH_HOST:${EDGE_PORT}/gate/health" 2>/dev/null | grep -o '"auth":"[a-z]*"' | cut -d'"' -f4 || true)
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
      python3 "$SCRIPT_DIR/agents/chat_server.py" &
      CHAT_PID=$!
      echo "$CHAT_PID" > "$RUN_DIR/chat.pid"
      CHAT_OWNED=1
      CHAT_UP=0
      _chat_host="${OPENBEAST_CHAT_BIND:-127.0.0.1}"    # set -u: normally unset
      case "$_chat_host" in
        0.*|localhost|::) _chat_host="127.0.0.1" ;;
      esac
      for _i in $(seq 1 20); do
        kill -0 "$CHAT_PID" 2>/dev/null || break
        # Not $HEALTH_HOST: chat_server binds OPENBEAST_CHAT_BIND
        # (loopback) whatever BIND_HOST says, so probing the LAN address
        # reported a healthy console as down.
        _h="$(curl -fsS -m 2 "http://$_chat_host:${CHAT_PORT:-3003}/api/chat/health" 2>/dev/null || true)"
        [[ "$_h" == *'"status"'* ]] && { CHAT_UP=1; break; }
        sleep 1
      done
      if [[ $CHAT_UP -eq 1 ]]; then
        echo "beast-chat ready on http://localhost:${CHAT_PORT:-3003}"
        if [[ -z "${CHAT_OPERATORS:-}" ]]; then
          echo "  CHAT_OPERATORS is empty — once published, EVERY tailnet login can"
          echo "  read every session. Set it in openbeast.conf to pin it to you."
        fi
      else
        echo "Warning: beast-chat not serving after 20s — the rest of the stack is fine." >&2
        echo "         Diagnose with: ./scripts/doctor.sh" >&2
        # Mirror the artifact branch below: a pid left on record after the
        # process died is a number the kernel will hand to something else.
        if ! kill -0 "$CHAT_PID" 2>/dev/null; then
          rm -f "$RUN_DIR/chat.pid"
          CHAT_PID=""
        fi
      fi
    fi
  fi
fi

# beast-artifact (opt-in, BEAST_ARTIFACT=true) — the publish-and-view service
# for model- or script-authored HTML (docs/BEAST_ARTIFACT_PLAN.md). Loopback
# only; setup-tailscale.sh --publish-artifact puts it on :8446 for phones.
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
    OPENBEAST_REPO_DIR="$SCRIPT_DIR" \
    OPENBEAST_ARTIFACT_PORT="${ARTIFACT_PORT:-3004}" \
      python3 "$SCRIPT_DIR/agents/artifact_server.py" &
    ARTIFACT_PID=$!
    echo "$ARTIFACT_PID" > "$RUN_DIR/artifact.pid"
    ARTIFACT_OWNED=1
    ARTIFACT_UP=0
    for _i in $(seq 1 20); do
      kill -0 "$ARTIFACT_PID" 2>/dev/null || break
      # -f plus a body match: a 400 from the server used to count as "ready"
      # (curl -s exits 0 on any HTTP status).
      _h="$(curl -fsS -m 2 "http://$HEALTH_HOST:${ARTIFACT_PORT:-3004}/api/artifacts/health" 2>/dev/null || true)"
      [[ "$_h" == *'"status"'* ]] && { ARTIFACT_UP=1; break; }
      sleep 1
    done
    if [[ $ARTIFACT_UP -eq 1 ]]; then
      echo "beast-artifact ready on http://localhost:${ARTIFACT_PORT:-3004} (publish: ./scripts/artifact.sh publish <file.html>)"
    else
      # WARNING, not fatal: this is an opt-in cosmetic service. Taking the whole
      # stack — the model included — down because a page viewer failed to bind
      # is a far worse outcome than not being able to open an artifact URL.
      echo "WARNING: beast-artifact did not come up (see its output above)." >&2
      echo "         The rest of the stack, llama-server included, is unaffected;" >&2
      echo "         artifact URLs will not serve until it starts. Publishing" >&2
      echo "         through the model's tools still works (in-process store)." >&2
      echo "         Retry it on its own:  ./scripts/healthcheck.sh --restart" >&2
      kill "$ARTIFACT_PID" 2>/dev/null || true
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
  echo "Starting extension: $_ext"
  "$REPO_DIR/extensions/$_ext/run.sh" >>"$RUN_DIR/ext-$_ext.log" 2>&1 &
  echo "$!" > "$RUN_DIR/ext-$_ext.pid"
done < <(ob_ext_processes)

# Configure Open WebUI (tool server + native function calling) in background
"$SCRIPT_DIR/scripts/configure-webui.sh" &
CONFIG_PID=$!

echo ""
echo "Stack is running:"
echo "  Model server:  http://localhost:8080"
echo "  MCPO tools:    http://localhost:3001 (OpenAPI docs at /docs)"
echo "  Open WebUI:    http://localhost:3000"
echo "  OpenCode:      run 'opencode' in any project directory"
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

# Supervise with bounded self-healing: an unexpected llama-server death
# (VRAM OOM, crash, a healthcheck --restart kill) gets up to 3 relaunches;
# staying healthy 5+ minutes refills the budget. ./stop.sh's TERM sets
# STOPPING via the trap, so a shutdown is never mistaken for a crash.
RESTARTS=0
while true; do
  LAUNCHED_AT=$SECONDS
  rc=0
  wait $LLAMA_PID || rc=$?
  [[ $STOPPING -eq 1 ]] && exit 0
  [[ $((SECONDS - LAUNCHED_AT)) -gt 300 ]] && RESTARTS=0
  if [[ $RESTARTS -ge 3 ]]; then
    echo "llama-server exited (status $rc) with the restart budget spent — stopping the stack."
    exit 1
  fi
  RESTARTS=$((RESTARTS + 1))
  echo "llama-server exited unexpectedly (status $rc) — relaunching ($RESTARTS/3) in 5s..."
  sleep 5
  # launch_and_wait rolls back to the last-known-good model if the current one
  # won't come back (e.g. a weight went missing under it) rather than dying.
  if ! launch_and_wait; then
    echo "Relaunched llama-server died before becoming healthy — stopping the stack." >&2
    exit 1
  fi
  echo "llama-server healthy again after restart $RESTARTS ($SERVE_SCRIPT)."
done
