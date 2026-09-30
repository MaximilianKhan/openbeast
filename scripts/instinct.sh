#!/bin/bash
# beast-instinct — the decision plane's operator CLI (docs/BEAST_INSTINCT.md).
#
#   scripts/instinct.sh up                 start the service (127.0.0.1:8094); mints .run/instinct.key
#   scripts/instinct.sh down               stop the service (and the stub, if this script started it)
#   scripts/instinct.sh status             pid + /health + per-decision effective mode
#   scripts/instinct.sh stub [flags…]      start the hermetic stub scorer (:18082) — plumbing only
#   scripts/instinct.sh probe              SIGHUP (reload + conformance probe), then show engines
#   scripts/instinct.sh eval D --engine E [run.py args…]
#   scripts/instinct.sh calibrate D --engine E
#   scripts/instinct.sh gate D --engine E [--load-report F]
#   scripts/instinct.sh promote D --engine E   CHECKS a promotion; never edits a file
#   scripts/instinct.sh demote D [--reason R]  runtime override + SIGHUP (no commit needed)
#   scripts/instinct.sh undemote D
#   scripts/instinct.sh label D [--n 20]       label shadow rows (needs log_inputs=excerpt)
#   scripts/instinct.sh stats [--decision D] [--since 7d]
#   scripts/instinct.sh report D              NEXT (beast-artifact page) — not built yet
#
# Env: INSTINCT_CONFIG (default agents/instinct/instinct.toml), INSTINCT_PORT,
#      INSTINCT_ENGINE_OVERRIDE, INSTINCT_RUN_DIR (pid/log dir, default .run),
#      INSTINCT_STUB_PORT (default 18082).
# Security: loopback only; the key is minted 0600 and never placed on argv
# (curl reads it from a here-doc config — scripts/lib/curl_auth.sh); every
# kill is by pidfile AND a cmdline check, never by pattern.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/curl_auth.sh
source "$SCRIPT_DIR/lib/curl_auth.sh"
# shellcheck source=lib/portown.sh
source "$SCRIPT_DIR/lib/portown.sh"

RUN_DIR="${INSTINCT_RUN_DIR:-$REPO_DIR/.run}"
PIDFILE="$RUN_DIR/instinct.pid"
LOGFILE="$RUN_DIR/instinct.log"
STUB_PIDFILE="$RUN_DIR/instinct-stub.pid"
STUB_PORT="${INSTINCT_STUB_PORT:-18082}"
PY="${PYTHON:-python3}"
export PYTHONPATH="$REPO_DIR/agents${PYTHONPATH:+:$PYTHONPATH}"

die() { echo "instinct: $*" >&2; exit 1; }

load_cfg() {
  local out
  out="$("$PY" -m instinct.cli cfg)" || die "config error (INSTINCT_CONFIG=${INSTINCT_CONFIG:-default})"
  eval "$out"
}

pid_alive() {  # <pidfile> <cmdline-substring>
  local pf="$1" want="$2" pid
  [[ -f "$pf" ]] || return 1
  pid="$(cat "$pf" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qF -- "$want"
}

stop_pid() {  # <pidfile> <cmdline-substring> <name>
  local pf="$1" want="$2" name="$3" pid i
  if ! pid_alive "$pf" "$want"; then
    rm -f "$pf"
    echo "$name: not running"
    return 0
  fi
  pid="$(cat "$pf")"
  kill "$pid" 2>/dev/null || true
  for i in $(seq 1 50); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pf"
  echo "$name: stopped (pid $pid)"
}

mint_key() {
  local kf="$1"
  if [[ -f "$kf" ]]; then
    local mode
    mode="$(stat -c '%a' "$kf")"
    [[ "$mode" == "600" ]] || die "$kf is mode $mode — want 0600 (fix it; refusing to use it)"
    return 0
  fi
  mkdir -p "$(dirname "$kf")"
  ( umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$kf" )
  chmod 600 "$kf"
  echo "instinct: minted $kf (0600)"
}

# The PRIMARY's bearer key, for the rig-27b binding (key_env = LLAMA_API_KEY).
# Resolved the way scripts/lib/conf.sh resolves it — env OPENBEAST_API_KEY,
# else openbeast.conf's LLAMA_API_KEY (last assignment wins, trimmed, quotes
# dropped) — plus an already-exported LLAMA_API_KEY (start.sh's case). conf.sh
# itself is NOT sourced: it may write openbeast.conf (SEARXNG_SECRET). The key
# reaches the service through its ENVIRONMENT only, never argv or a log line.
primary_key() {
  local v="${OPENBEAST_API_KEY:-${LLAMA_API_KEY:-}}" conf="$REPO_DIR/openbeast.conf" line
  if [[ -z "$v" && -f "$conf" ]]; then
    line="$(grep -E '^[[:space:]]*LLAMA_API_KEY[[:space:]]*=' "$conf" | tail -n1 || true)"
    line="${line#*=}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    line="${line#\"}"; line="${line%\"}"
    line="${line#\'}"; line="${line%\'}"
    v="$line"
  fi
  printf '%s' "$v"
}

api() {  # <method> <path> [curl args…]
  local method="$1" path="$2" key
  shift 2
  key="$(cat "$INSTINCT_KEY_FILE")"
  ob_curl_bearer "$key" -sS -X "$method" --max-time 10 \
    "http://127.0.0.1:${INSTINCT_PORT}${path}" "$@"
}

cmd_up() {
  load_cfg
  mkdir -p "$RUN_DIR"
  if pid_alive "$PIDFILE" "instinct.server"; then
    echo "instinct: already running (pid $(cat "$PIDFILE"))"
    return 0
  fi
  mint_key "$INSTINCT_KEY_FILE"
  if ob_port_listening "$INSTINCT_PORT"; then
    die "port $INSTINCT_PORT is already held — refusing to start (pre-bind check)"
  fi
  ( umask 077; : >> "$LOGFILE" )
  local pkey
  pkey="$(primary_key)"
  if [[ -n "$pkey" ]]; then
    LLAMA_API_KEY="$pkey" nohup "$PY" -m instinct.server >>"$LOGFILE" 2>&1 < /dev/null &
  else
    env -u LLAMA_API_KEY nohup "$PY" -m instinct.server >>"$LOGFILE" 2>&1 < /dev/null &
  fi
  local pid=$!
  echo "$pid" > "$PIDFILE"
  local i
  for i in $(seq 1 100); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PIDFILE"
      tail -n 20 "$LOGFILE" >&2 || true
      die "service exited during startup (log: $LOGFILE)"
    fi
    if curl -fsS --max-time 1 "http://127.0.0.1:${INSTINCT_PORT}/health" >/dev/null 2>&1; then
      # Readiness only counts if OUR pid holds the port (a squatter could answer).
      local own=0
      ob_pid_owns_port "$pid" "$INSTINCT_PORT" || own=$?
      if [[ $own -eq 1 ]]; then
        kill "$pid" 2>/dev/null || true
        rm -f "$PIDFILE"
        die "another process answers on :$INSTINCT_PORT — refusing"
      fi
      echo "instinct: up on 127.0.0.1:${INSTINCT_PORT} (pid $pid)"
      return 0
    fi
    sleep 0.1
  done
  kill "$pid" 2>/dev/null || true
  rm -f "$PIDFILE"
  die "no /health within 10 s (log: $LOGFILE)"
}

cmd_down() {
  stop_pid "$PIDFILE" "instinct.server" "instinct"
  if [[ -f "$STUB_PIDFILE" ]]; then
    stop_pid "$STUB_PIDFILE" "stub_scorer.py" "instinct stub"
  fi
}

cmd_status() {
  load_cfg
  if pid_alive "$PIDFILE" "instinct.server"; then
    echo "instinct: running (pid $(cat "$PIDFILE"))"
  else
    echo "instinct: not running"
    return 1
  fi
  curl -fsS --max-time 2 "http://127.0.0.1:${INSTINCT_PORT}/health" >/dev/null \
    && echo "health: ok" || { echo "health: FAILING"; return 1; }
  api GET /v1/instinct/decisions | "$PY" -m json.tool
}

cmd_stub() {
  mkdir -p "$RUN_DIR"
  if pid_alive "$STUB_PIDFILE" "stub_scorer.py"; then
    echo "stub: already running (pid $(cat "$STUB_PIDFILE"))"
    return 0
  fi
  if ob_port_listening "$STUB_PORT"; then
    die "port $STUB_PORT is already held — refusing to start the stub"
  fi
  nohup "$PY" "$SCRIPT_DIR/instinct/stub_scorer.py" --port "$STUB_PORT" "$@" \
    >>"$RUN_DIR/instinct-stub.log" 2>&1 < /dev/null &
  echo "$!" > "$STUB_PIDFILE"
  local i
  for i in $(seq 1 50); do
    if curl -fsS --max-time 1 "http://127.0.0.1:${STUB_PORT}/health" >/dev/null 2>&1; then
      echo "stub: up on 127.0.0.1:${STUB_PORT} (pid $(cat "$STUB_PIDFILE"))"
      return 0
    fi
    sleep 0.1
  done
  die "stub did not come up"
}

hup() {
  if pid_alive "$PIDFILE" "instinct.server"; then
    kill -HUP "$(cat "$PIDFILE")"
    echo "instinct: sent SIGHUP (reload + probe)"
  else
    echo "instinct: not running (the override applies at next start)"
  fi
}

cmd_run_py() {  # <flag or ""> D --engine E [args…]
  local flag="$1" decision="${2:-}"
  shift 2 || true
  [[ -n "$decision" ]] || die "usage: instinct.sh ${flag:-eval} <decision> --engine <engine>"
  if [[ -n "$flag" ]]; then
    nice -n 19 "$PY" "$REPO_DIR/evals/decisions/run.py" --decision "$decision" "$flag" "$@"
  else
    nice -n 19 "$PY" "$REPO_DIR/evals/decisions/run.py" --decision "$decision" "$@"
  fi
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    stub) cmd_stub "$@" ;;
    probe) load_cfg; hup; sleep 1; api GET /v1/instinct/engines | "$PY" -m json.tool ;;
    eval) cmd_run_py "" "$@" ;;
    calibrate) cmd_run_py --calibrate "$@" ;;
    gate) cmd_run_py --gate "$@" ;;
    promote) "$PY" -m instinct.cli promote "$@" ;;
    demote) "$PY" -m instinct.cli demote "$@"; hup ;;
    undemote) "$PY" -m instinct.cli undemote "$@"; hup ;;
    label) "$PY" -m instinct.cli label "$@" ;;
    stats) "$PY" -m instinct.cli stats "$@" ;;
    report) echo "instinct: report is NEXT (a private beast-artifact page per decision)"; return 2 ;;
    ""|-h|--help|help) sed -n '2,25p' "$0" ;;
    *) die "unknown command '$cmd' (try --help)" ;;
  esac
}

main "$@"
