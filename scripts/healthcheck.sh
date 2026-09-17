#!/bin/bash
# Health monitor for the OpenBeast stack.
#
# Checks all services and optionally restarts failed ones.
#
# Usage:
#   ./scripts/healthcheck.sh              # check and report
#   ./scripts/healthcheck.sh --restart    # check and restart failed services
#
# Exit codes:
#   0 — all healthy
#   1 — one or more services down

set -euo pipefail

# pkill -f takes an EXTENDED REGEX, not a literal. A repo path containing a
# regex metacharacter therefore breaks both directions: `+` makes the pattern
# fail to match its own process (the orphan reap silently does nothing), and
# `.` makes it match OTHER paths (the sibling-worktree reap this file's own
# comments call impossible). Measured both. Quote the path before using it as
# a pattern — all four call sites, not just the new one.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/conf.sh"
source "$SCRIPT_DIR/lib/proc.sh"      # _ob_ere, ob_pid_matches, ob_pid_age

LLAMA_URL="${LLAMA_URL:-http://localhost:8080}"
MCPO_URL="${MCPO_URL:-http://localhost:3001}"
WEBUI_URL="${WEBUI_URL:-http://localhost:3000}"
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"

# Where restarted services actually answer (same mapping start.sh uses):
# loopback for loopback/wildcard binds, the address itself otherwise.
case "$BIND_HOST" in
  127.*|localhost|0.*) HEALTH_HOST="127.0.0.1" ;;
  *)                   HEALTH_HOST="$BIND_HOST" ;;
esac

# beast-chat binds OPENBEAST_CHAT_BIND (loopback by default), NOT BIND_HOST.
case "${OPENBEAST_CHAT_BIND:-127.0.0.1}" in
  ''|0.*|localhost|::) CHAT_HEALTH_HOST="127.0.0.1" ;;
  *)                   CHAT_HEALTH_HOST="$OPENBEAST_CHAT_BIND" ;;
esac

RESTART=false
[[ "${1:-}" == "--restart" ]] && RESTART=true

HEALTHY=0
UNHEALTHY=0

check() {
  # check <name> <url> <match> [bearer-key] — key adds an Authorization
  # header (keyed MCPO instances answer 401 without it, RBAC Phase 2).
  local name="$1" url="$2" match="$3" key="${4:-}"
  local auth=()
  [[ -n "$key" ]] && auth=(-H "Authorization: Bearer $key")
  if curl -s --max-time 5 "${auth[@]}" "$url" 2>/dev/null | grep -qi "$match"; then
    echo "  OK   $name"
    HEALTHY=$((HEALTHY + 1))
    return 0
  else
    echo "  DOWN $name ($url)"
    UNHEALTHY=$((UNHEALTHY + 1))
    return 1
  fi
}

echo "Stack health check — $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# llama.cpp — bearer passed for keyed installs (LLAMA_API_KEY set); /health
# itself is public in llama-server but /slots below is not, and sending the
# key to a keyless server is harmless.
LLAMA_AUTH=()
[[ -n "${LLAMA_API_KEY:-}" ]] && LLAMA_AUTH=(-H "Authorization: Bearer $LLAMA_API_KEY")
LLAMA_BIN_ERE="$(_ob_ere "$REPO_DIR/llama.cpp/build/bin/llama-server")"

# Is llama-server still LOADING? Then it is not down, and killing it is the
# outage. /health answers 503 {"error":{"message":"Loading model"...}} for the
# whole load — minutes for a big model — and this script used to read that as
# DOWN and, under --restart, kill the server. openbeast-watchdog.timer fires
# every 5 minutes, so any ./stop.sh && ./start.sh had roughly a
# load-time-in-300 chance of being shot mid-load: launch_and_wait then fails
# and start.sh tears the WHOLE stack down. The few seconds before the port is
# bound are covered by the recorded pid's age instead of the body.
_LLAMA_ARGV0='(^|/)llama-server( |$)'   # argv[0], not "mentions llama-server"
_llama_loading() {
  local body pid age=""
  pid="$(cat "$REPO_DIR/.run/llama.pid" 2>/dev/null || true)"
  ob_pid_matches "$pid" "$_LLAMA_ARGV0" && age="$(ob_pid_age "$pid")"
  body="$(curl -s --max-time 5 "${LLAMA_AUTH[@]}" "$LLAMA_URL/health" 2>/dev/null || true)"
  if [[ "$body" == *"Loading model"* ]]; then
    # BOUNDED. A server wedged mid-load (a CUDA hang, a stalled weight read)
    # says "Loading model" forever, and nothing else in the stack bounds a
    # load either — so past the grace a recorded server that is STILL loading
    # is down. With no recorded pid there is no age to judge by: leave it.
    [[ -z "$age" || "$age" -lt "${OPENBEAST_LLAMA_LOAD_GRACE:-900}" ]]
    return
  fi
  # Answering at all (healthy OR some other error) is not "loading": only a
  # server that has not bound its port yet gets the benefit of its age.
  [[ -n "$body" ]] && return 1
  [[ -n "$age" && "$age" -lt "${OPENBEAST_LLAMA_LOAD_GRACE:-900}" ]]
}

# Kill THIS stack's llama-server and nothing else: the recorded pid when it
# still is one, else the path-anchored pattern. Never the bare name — that
# reaps a campaign's server and any sibling worktree's (it destroyed a live
# measurement run on this box on 2026-09-14).
_kill_own_llama() {
  local pid
  pid="$(cat "$REPO_DIR/.run/llama.pid" 2>/dev/null || true)"
  # The recorded pid is identified by NAME, not path: it is ours by virtue of
  # being recorded, and a serve script may exec a build outside the default
  # path. The pattern fallback has no such provenance, so it stays anchored.
  if ob_pid_matches "$pid" "$_LLAMA_ARGV0"; then
    kill "$pid" 2>/dev/null || true
  elif pgrep -f "$LLAMA_BIN_ERE" >/dev/null 2>&1; then
    pkill -f "$LLAMA_BIN_ERE" 2>/dev/null || true
  fi
}

# A live GPU lease means a campaign owns the card ON PURPOSE (the stack was
# stopped for it). Relaunching the stack's model into that is the watchdog
# contaminating — or OOMing — a measurement.
_gpu_leased() {
  local out
  [[ -x "$SCRIPT_DIR/gpu-lease.sh" ]] || return 1
  # Captured, not piped into `grep -q`: grep exits at the first match, the
  # writer takes SIGPIPE on its second line, and under pipefail a HELD lease
  # then reads as "not held" — the one answer this must never get wrong.
  out="$("$SCRIPT_DIR/gpu-lease.sh" status 2>/dev/null || true)"
  [[ "$out" == HELD* ]]
}

if _llama_loading; then
  echo "  LOAD llama.cpp server (model still loading — left alone)"
  LOADING=1
elif ! check "llama.cpp server" "$LLAMA_URL/health" "ok" "${LLAMA_API_KEY:-}"; then
  if $RESTART && _gpu_leased; then
    echo "       → the GPU lease is held ($("$SCRIPT_DIR/gpu-lease.sh" status 2>/dev/null | head -n1 || true))"
    echo "         not restarting llama.cpp into someone else's measurement."
  elif $RESTART; then
    # If a start.sh supervisor is alive, it owns llama-server: kill the
    # server and let the supervisor's self-healing loop relaunch it —
    # starting our own copy here would race it for the port and the VRAM.
    SUP_PID_FILE="$REPO_DIR/.run/supervisor.pid"
    if ob_pid_matches "$(cat "$SUP_PID_FILE" 2>/dev/null || true)" 'start\.sh'; then
      echo "       → supervisor alive: killing llama-server, letting it relaunch..."
      _kill_own_llama
      for i in $(seq 1 180); do
        if curl -s --max-time 2 "$LLAMA_URL/health" | grep -q "ok"; then
          echo "       → healthy after ${i}s (supervisor relaunched it)"
          break
        fi
        sleep 1
      done
    else
      echo "       → no supervisor: restarting llama.cpp directly..."
      # Path-anchored and ERE-quoted: never an unrelated llama-server from
      # another project on the same box.
      if pgrep -f "$LLAMA_BIN_ERE" >/dev/null 2>&1; then
        _kill_own_llama
        sleep 2
      fi
      # Relaunch the serve script the stack was STARTED with (.run/serve-script,
      # recorded by start.sh); fall back to the configured default.
      SERVE_SCRIPT_NAME="$DEFAULT_SERVE_SCRIPT"
      if [[ -f "$REPO_DIR/.run/serve-script" ]]; then
        _recorded="$(head -n1 "$REPO_DIR/.run/serve-script" 2>/dev/null || true)"
        if [[ -n "$_recorded" && -x "$SCRIPT_DIR/$_recorded" ]]; then
          SERVE_SCRIPT_NAME="$_recorded"
        fi
      fi
      echo "       → launching $SERVE_SCRIPT_NAME"
      "$SCRIPT_DIR/$SERVE_SCRIPT_NAME" &
      # Record the pid (serve.sh execs llama-server, so $! IS the server) —
      # without it, ./start.sh --status reports llama "not running" after a
      # watchdog restart. Mirrors the mcpo relaunch below.
      mkdir -p "$REPO_DIR/.run"
      echo "$!" > "$REPO_DIR/.run/llama.pid"
      echo "       → started (waiting for health...)"
      for i in $(seq 1 180); do
        if curl -s --max-time 2 "$LLAMA_URL/health" | grep -q "ok"; then
          echo "       → healthy after ${i}s"
          break
        fi
        sleep 1
      done
    fi
  fi
fi

# Identity tool server (agents/openapi_tools.py — replaced mcpo 2026-07-09).
# One process serves both RBAC profiles; /health is public, keys gate tools.
if ! check "Tool server" "$MCPO_URL/health" "ok"; then
  if $RESTART; then
    echo "       → restarting tool server..."
    pkill -f "$(_ob_ere "$REPO_DIR/agents/openapi_tools.py")" 2>/dev/null || true
    pkill -f "mcpo --port" 2>/dev/null || true   # legacy instances
    sleep 1
    # Mirror start.sh: the chat model's file workspace must exist and be
    # private before the tool server starts sharding into it.
    if [[ ! -d "$OPENBEAST_FILES_DIR" ]]; then
      mkdir -p "$OPENBEAST_FILES_DIR" && chmod 700 "$OPENBEAST_FILES_DIR"
    fi
    python3 "$REPO_DIR/agents/openapi_tools.py" &
    MCPO_NEW_PID=$!
    # Record the pid IMMEDIATELY — it's the live process either way, and a
    # slow-but-alive start must not leave the OLD pid on record (start.sh
    # --status would lie about what's running).
    mkdir -p "$REPO_DIR/.run"
    echo "$MCPO_NEW_PID" > "$REPO_DIR/.run/mcpo.pid"
    MCPO_OK=0
    for _i in $(seq 1 15); do
      if curl -s --max-time 2 "http://$HEALTH_HOST:3001/health" 2>/dev/null | grep -qi ok; then
        MCPO_OK=1
        break
      fi
      sleep 1
    done
    if [[ $MCPO_OK -eq 1 ]]; then
      echo "       → restarted (pid $MCPO_NEW_PID)"
    else
      echo "       → restart FAILED: tool server not serving after 15s (check its output above)"
    fi
  fi
fi

# Open WebUI
if ! check "Open WebUI" "$WEBUI_URL/api/version" "version"; then
  if $RESTART; then
    echo "       → restarting Open WebUI..."
    docker compose -f "$REPO_DIR/docker-compose.yml" up -d open-webui
    sleep 5
    echo "       → restarted"
  fi
fi

# SearXNG
if ! check "SearXNG" "$SEARXNG_URL" "searx"; then
  if $RESTART; then
    echo "       → restarting SearXNG..."
    docker compose -f "$REPO_DIR/docker-compose.yml" up -d searxng
    sleep 3
    echo "       → restarted"
  fi
fi

# beast-gate (opt-in) — the inference edge remote clients arrive through.
if [[ "${EDGE_GATE:-false}" == "true" ]]; then
  if ! check "beast-gate" "http://${HEALTH_HOST:-127.0.0.1}:${EDGE_PORT:-8090}/gate/health" "beast-gate"; then
    if $RESTART; then
      echo "       → restarting beast-gate..."
      pkill -f "$(_ob_ere "$REPO_DIR/agents/edge.py")" 2>/dev/null || true
      sleep 1
      # Record the pid like the llama/mcpo relaunch paths do — without it
      # ./start.sh --status reports the gate down after a watchdog restart,
      # and the supervisor is left holding a stale pid.
      OPENBEAST_REPO_DIR="$REPO_DIR" \
        OPENBEAST_LLAMA_UPSTREAM="http://127.0.0.1:8080" \
        python3 "$REPO_DIR/agents/edge.py" >/dev/null 2>&1 &
      mkdir -p "$REPO_DIR/.run"
      echo "$!" > "$REPO_DIR/.run/edge.pid"
      sleep 3
      echo "       → restarted"
    fi
  fi
fi

# beast-chat console (opt-in) — the surface a phone reaches the rig's own
# sessions through. Restart is safe to automate: it holds no model state and
# no client connections worth preserving (an SSE tail reattaches by offset,
# which is the whole point of the offset cursor).
if [[ "${BEAST_CHAT:-false}" == "true" ]]; then
  if ! check "beast-chat console" \
       "http://$CHAT_HEALTH_HOST:${CHAT_PORT:-3003}/api/chat/health" '"status":"ok"'; then
    if $RESTART; then
      echo "       → restarting beast-chat console..."
      # Kill by RECORDED PID first, exactly as the artifact path does: a
      # pattern kill on this box has already reaped a live measurement run
      # (2026-09-14). The path-qualified pkill stays as the fallback for a
      # console started outside start.sh, which records no pid.
      _chat_pid="$(cat "$REPO_DIR/.run/chat.pid" 2>/dev/null || true)"
      # IDENTITY-checked: .run/ survives a reboot, and a recycled pid is a
      # stranger (lib/proc.sh). A number that is no longer ours falls
      # through to the path-anchored reap.
      if ob_pid_matches "$_chat_pid" "$(_ob_ere "$REPO_DIR/agents/chat_server.py")"; then
        kill "$_chat_pid" 2>/dev/null || true
      else
        pkill -f "$(_ob_ere "$REPO_DIR/agents/chat_server.py")" 2>/dev/null || true
      fi
      sleep 1
      python3 "$REPO_DIR/agents/chat_server.py" >/dev/null 2>&1 &
      CHAT_NEW_PID=$!
      # Record the pid immediately, same reasoning as the llama/mcpo paths:
      # a slow-but-alive start must not leave a stale pid on record.
      mkdir -p "$REPO_DIR/.run"
      echo "$CHAT_NEW_PID" > "$REPO_DIR/.run/chat.pid"
      CHAT_OK=0
      for _i in $(seq 1 15); do
        if curl -s --max-time 2 "http://$CHAT_HEALTH_HOST:${CHAT_PORT:-3003}/api/chat/health" 2>/dev/null | grep -q '"status":"ok"'; then
          CHAT_OK=1
          break
        fi
        sleep 1
      done
      if [[ $CHAT_OK -eq 1 ]]; then
        echo "       → restarted (pid $CHAT_NEW_PID)"
      else
        echo "       → restart FAILED: beast-chat not serving after 15s"
      fi
    fi
  fi
fi

# beast-artifact (opt-in) — the page server for everything the model and the
# campaign scripts publish. doctor.sh sends the operator here ("run
# ./scripts/healthcheck.sh --restart") when artifact URLs stop answering, so
# without this branch that advice was a no-op: the doctor named a repair that
# did not exist.
if [[ "${BEAST_ARTIFACT:-false}" == "true" ]]; then
  ARTIFACT_HEALTH="http://${HEALTH_HOST:-127.0.0.1}:${ARTIFACT_PORT:-3004}/api/artifacts/health"
  if ! check "beast-artifact" "$ARTIFACT_HEALTH" "ok"; then
    if $RESTART; then
      echo "       → restarting beast-artifact..."
      # Kill by RECORDED PID, never by pattern. A `pkill -f artifact_server`
      # on this box would also reap a sibling worktree's server — and worse,
      # a mistyped pattern has already destroyed a live measurement run here
      # (2026-09-14). start.sh wrote the pid; if it is gone or stale we just
      # start a new one and let the old (dead) record be overwritten.
      _art_pid="$(cat "$REPO_DIR/.run/artifact.pid" 2>/dev/null || true)"
      if ob_pid_matches "$_art_pid" "$(_ob_ere "$REPO_DIR/agents/artifact_server.py")"; then
        kill "$_art_pid" 2>/dev/null || true
        sleep 1
      else
        # No recorded pid, but the health check failed — so if anything IS
        # holding :3004 it is an orphan we have no handle on, and every
        # subsequent cycle would spawn a replacement that cannot bind and
        # loop here forever. PATH-ANCHORED, which is what the rule above
        # actually forbids the bare form of: "$REPO_DIR/agents/..." cannot
        # match a sibling worktree's server, because its path differs. Same
        # form stop.sh uses for every service it reaps.
        pkill -f "$(_ob_ere "$REPO_DIR/agents/artifact_server.py")" 2>/dev/null || true
        sleep 1
      fi
      OPENBEAST_REPO_DIR="$REPO_DIR" \
        OPENBEAST_ARTIFACT_PORT="${ARTIFACT_PORT:-3004}" \
        python3 "$REPO_DIR/agents/artifact_server.py" >/dev/null 2>&1 &
      mkdir -p "$REPO_DIR/.run"
      echo "$!" > "$REPO_DIR/.run/artifact.pid"
      ARTIFACT_OK=0
      for _i in $(seq 1 15); do
        if curl -s --max-time 2 "$ARTIFACT_HEALTH" 2>/dev/null | grep -qi ok; then
          ARTIFACT_OK=1
          break
        fi
        sleep 1
      done
      if [[ $ARTIFACT_OK -eq 1 ]]; then
        echo "       → restarted"
      else
        echo "       → restart FAILED: beast-artifact not serving after 15s"
        echo "         (publishing still works — the tools use the store"
        echo "          in process; only viewing the URLs is down)"
      fi
    fi
  fi
fi

# beast-slot status API (dashboard extension) — only when enabled in conf.
if [[ " ${EXTENSIONS:-} " == *" dashboard "* || "${EXTENSIONS:-}" == "dashboard" ]]; then
  check "Dashboard (beast-slot)" "http://${HEALTH_HOST:-127.0.0.1}:3002/api/slot" "beast_slot" || true
fi

# Tailscale (remote access) — only checked when installed; the stack is
# fully functional without it, just localhost-only.
if command -v tailscale &>/dev/null; then
  TS_ONLINE=$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try: print('yes' if json.load(sys.stdin)['Self']['Online'] else 'no')
except Exception: print('no')
" 2>/dev/null)
  if [[ "$TS_ONLINE" == "yes" ]]; then
    echo "  OK   Tailscale (remote access)"
    HEALTHY=$((HEALTHY + 1))
  else
    echo "  DOWN Tailscale (remote access)"
    UNHEALTHY=$((UNHEALTHY + 1))
    if $RESTART; then
      # -n: never hang an unattended healthcheck on a sudo password prompt.
      if sudo -n true 2>/dev/null; then
        echo "       → restarting tailscaled..."
        sudo -n systemctl restart tailscaled
        sleep 3
      else
        echo "       → skipping tailscaled restart (needs passwordless sudo);"
        echo "         run manually: sudo systemctl restart tailscaled"
      fi
    fi
  fi
fi

# GPU VRAM usage
echo ""
if command -v nvidia-smi &>/dev/null; then
  VRAM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 || true)
  VRAM_TOTAL=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || true)
  if [[ -z "$VRAM_TOTAL" || ! "$VRAM_TOTAL" =~ ^[0-9]+$ || "$VRAM_TOTAL" -eq 0 \
        || -z "$VRAM_USED" || ! "$VRAM_USED" =~ ^[0-9]+$ ]]; then
    echo "  GPU VRAM: unavailable (nvidia-smi returned no data)"
    VRAM_PCT=0
  else
    VRAM_PCT=$((VRAM_USED * 100 / VRAM_TOTAL))
    echo "  GPU VRAM: ${VRAM_USED}/${VRAM_TOTAL} MiB (${VRAM_PCT}%)"
    # Absolute headroom is what actually matters (the 2 GB rule): serve
    # contexts are measured against a ~1.3 GB desktop baseline, and the
    # documented sustained-load crash zone starts around 2.1 GB free.
    # llama-server's allocation is static after startup — when headroom
    # shrinks, it's the DESKTOP (browser/terminal/compositor) that grew,
    # so name the graphics processes eating it.
    VRAM_FREE=$((VRAM_TOTAL - VRAM_USED))
    if [[ $VRAM_FREE -lt 2048 ]]; then
      echo "  WARNING: only ${VRAM_FREE} MiB VRAM headroom (<2048 MiB rule;"
      echo "           sustained-load crashes were measured near 2100 MiB free)."
      echo "           llama-server pre-allocates and does not grow — the usual"
      echo "           culprit is desktop growth. Current graphics processes:"
      nvidia-smi 2>/dev/null | awk '/Processes:/{p=1} p && / G /{print $5, $(NF-1)}' \
        | while read -r _gpid _gmem; do
            _gname=$(ps -o comm= -p "$_gpid" 2>/dev/null || echo "?")
            echo "             ${_gname} (pid ${_gpid}): ${_gmem}"
          done | head -6 || true
      echo "           Free it: close GPU-heavy apps, or restart heavy terminals/browsers."
    elif [[ $VRAM_PCT -gt 95 ]]; then
      echo "  WARNING: VRAM usage above 95% — risk of OOM"
    fi
  fi
fi

# Disk space — the two mounts that fill up in practice: the weights dir
# (model downloads) and the repo dir (logs, eval results, WebUI volume on
# the docker root). Warn below 10 GB free — one more quant download or a
# long sweep can eat that.
_weights_dir=$( (source "$SCRIPT_DIR/lib/weights.sh" >/dev/null 2>&1 && echo "$WEIGHTS_DIR") || echo "$REPO_DIR/weights" )
for _mount_label in "weights:$_weights_dir" "repo:$REPO_DIR"; do
  _label="${_mount_label%%:*}"; _dir="${_mount_label#*:}"
  [[ -d "$_dir" ]] || continue
  _free_gb=$(df -BG --output=avail "$_dir" 2>/dev/null | tail -1 | tr -dc '0-9' || echo "")
  if [[ -n "$_free_gb" && "$_free_gb" -lt 10 ]]; then
    echo "  WARNING: ${_free_gb}G free on the $_label mount ($_dir) — downloads/sweeps may fail"
  fi
done

# Slot utilization (/slots is key-protected when LLAMA_API_KEY is set)
SLOTS_JSON=$(curl -s --max-time 3 "${LLAMA_AUTH[@]}" "$LLAMA_URL/slots" 2>/dev/null || echo "[]")
ACTIVE_SLOTS=$(echo "$SLOTS_JSON" | python3 -c "
import sys, json
try:
    slots = json.load(sys.stdin)
    active = sum(1 for s in slots if s.get('state', 0) != 0)
    print(f'{active}/{len(slots)}')
except: print('?/?')
" 2>/dev/null)
echo "  Slots: $ACTIVE_SLOTS active"

# Summary
echo ""
TOTAL=$((HEALTHY + UNHEALTHY))
if [[ $UNHEALTHY -eq 0 && ${LOADING:-0} -eq 1 ]]; then
  echo "$TOTAL services healthy; llama.cpp is still LOADING its model (not counted)."
elif [[ $UNHEALTHY -eq 0 ]]; then
  echo "All $TOTAL services healthy."
else
  echo "$UNHEALTHY of $TOTAL services unhealthy."
fi

[[ $UNHEALTHY -eq 0 ]]
