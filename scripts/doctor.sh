#!/bin/bash
# openbeast doctor — diagnose a CONFIGURED / RUNNING stack and print a
# fix-list. Where `bootstrap.sh --preflight` checks "can I install this box",
# doctor checks "is the box I installed healthy, secure, and consistent".
#
#   ./scripts/doctor.sh          # full report
#   ./scripts/doctor.sh --quiet  # only WARN/FAIL lines (for scripts/CI)
#
# Exit 0 = no failures (warnings allowed), 1 = at least one FAIL.
# Deliberately NOT set -e: every check must run even when earlier ones fail.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export REPO_DIR
source "$SCRIPT_DIR/lib/conf.sh"
source "$SCRIPT_DIR/lib/hardware.sh" 2>/dev/null || true

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

case "$BIND_HOST" in
  127.*|localhost|0.*) HEALTH_HOST="127.0.0.1" ;;
  *)                   HEALTH_HOST="$BIND_HOST" ;;
esac

PASS=0 WARN=0 FAIL=0
section() { [[ $QUIET -eq 1 ]] || printf '\n\033[1m%s\033[0m\n' "$1"; }
pass()    { [[ $QUIET -eq 1 ]] || echo "  ✓ $1"; PASS=$((PASS+1)); }
warn()    { echo "  ! $1"; [[ -n "${2:-}" ]] && echo "      → $2"; WARN=$((WARN+1)); }
fail()    { echo "  ✗ $1"; [[ -n "${2:-}" ]] && echo "      → fix: $2"; FAIL=$((FAIL+1)); }

# curl a health URL; $3 optional bearer key. Returns 0 if the body matches $2.
probe() { # probe <url> <match> [key]
  local auth=(); [[ -n "${3:-}" ]] && auth=(-H "Authorization: Bearer $3")
  curl -s --max-time 4 "${auth[@]}" "$1" 2>/dev/null | grep -qi "$2"
}

# ── Hardware ────────────────────────────────────────────────────────────────
section "Hardware"
if command -v ob_detect_gpu >/dev/null 2>&1; then
  ob_detect_gpu 2>/dev/null || true
  if [[ "${OB_GPU_VENDOR:-none}" == "none" ]]; then
    warn "no supported GPU detected" "CPU-only works but the 27B default is impractical"
  elif [[ "${OB_VRAM_MB:-0}" -gt 0 && "${OB_VRAM_MB:-0}" -lt 11000 ]]; then
    fail "GPU has ${OB_VRAM_MB} MiB VRAM — below the 11 GB floor" \
         "OpenBeast targets 1080 Ti / 2080 Ti class and up (docs/HARDWARE_PROFILES.md)"
  else
    pass "GPU: ${OB_GPU_NAME:-unknown} (${OB_VRAM_MB:-?} MiB VRAM)"
  fi
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  read -r used total < <(nvidia-smi --query-gpu=memory.used,memory.total \
    --format=csv,noheader,nounits 2>/dev/null | head -1 | tr ',' ' ')
  if [[ "${total:-0}" =~ ^[0-9]+$ && "${used:-0}" =~ ^[0-9]+$ ]]; then
    free=$((total - used))
    if [[ $free -lt 2048 ]]; then
      warn "only ${free} MiB VRAM headroom (<2048 MiB rule)" \
           "llama-server is static — close GPU-heavy desktop apps to reclaim it"
    else
      pass "VRAM headroom: ${free} MiB"
    fi
  fi
fi

# ── Disk ────────────────────────────────────────────────────────────────────
section "Disk"
_wdir=$( (source "$SCRIPT_DIR/lib/weights.sh" >/dev/null 2>&1 && echo "$WEIGHTS_DIR") || echo "$REPO_DIR/weights" )
for pair in "weights:$_wdir" "repo:$REPO_DIR"; do
  d="${pair#*:}"; [[ -d "$d" ]] || continue
  freeg=$(df -BG --output=avail "$d" 2>/dev/null | tail -1 | tr -dc '0-9')
  if [[ -n "$freeg" && "$freeg" -lt 10 ]]; then
    warn "${pair%%:*} mount ($d): ${freeg}G free" "downloads/sweeps may fail under 10G"
  else
    pass "${pair%%:*} mount: ${freeg:-?}G free"
  fi
done

# ── Config & secrets ────────────────────────────────────────────────────────
section "Config & secrets"
CONF="$REPO_DIR/openbeast.conf"
if [[ -f "$CONF" ]]; then
  mode=$(stat -c '%a' "$CONF" 2>/dev/null)
  if grep -qE '^[[:space:]]*(MCPO_.*_KEY|.*_SECRET|WEBUI_ADMIN_PASSWORD|LLAMA_API_KEY)=' "$CONF" \
     && [[ "$mode" != "600" ]]; then
    fail "openbeast.conf holds secrets but is mode $mode" "chmod 600 $CONF"
  else
    pass "openbeast.conf present (mode $mode)"
  fi
else
  pass "no openbeast.conf (single-user defaults — fine)"
fi
if [[ -d "$OPENBEAST_FILES_DIR" ]]; then
  fmode=$(stat -c '%a' "$OPENBEAST_FILES_DIR" 2>/dev/null)
  if [[ "${fmode: -2}" != "00" ]]; then
    warn "files workspace $OPENBEAST_FILES_DIR is group/world-accessible (mode $fmode)" \
         "chmod 700 $OPENBEAST_FILES_DIR"
  else
    pass "files workspace private (mode $fmode)"
  fi
fi
# Secrets must not have leaked into the systemd unit environment.
if systemctl --user show openbeast-stack -p Environment --value 2>/dev/null \
   | grep -qE '(KEY|SECRET|PASSWORD)='; then
  fail "a secret is exposed in the openbeast-stack unit environment" \
       "restart with ./stop.sh && ./start.sh -d (secrets are read from conf, not passed as env)"
else
  pass "no secrets in the systemd unit environment"
fi
if [[ "$BIND_HOST" == "0.0.0.0" || "$BIND_HOST" == "::" ]]; then
  warn "BIND_HOST=$BIND_HOST exposes the whole stack unauthenticated" \
       "prefer Tailscale (scripts/setup-tailscale.sh); set BIND_HOST=127.0.0.1"
else
  pass "bind host is loopback-scoped ($BIND_HOST)"
fi

# ── Pinned dependencies ─────────────────────────────────────────────────────
section "Pinned dependencies"
if command -v python3 >/dev/null 2>&1; then
  while IFS='==' read -r pkg ver; do
    [[ -z "$pkg" || "$pkg" == \#* ]] && continue
    ver="${ver#=}"
    have=$(python3 -m pip show "$pkg" 2>/dev/null | awk '/^Version:/{print $2}')
    if [[ -z "$have" ]]; then
      fail "$pkg not installed (pinned $ver)" "pip install --user -r agents/requirements.txt"
    elif [[ "$have" != "$ver" ]]; then
      warn "$pkg is $have, pinned $ver" "./scripts/update.sh --python (or reinstall the pin)"
    else
      pass "$pkg==$ver"
    fi
  done < <(grep -vE '^\s*#|^\s*$' "$REPO_DIR/agents/requirements.txt")
fi

# ── Docker ──────────────────────────────────────────────────────────────────
section "Docker"
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    pass "docker daemon reachable"
    if grep -q '@sha256:' "$REPO_DIR/docker-compose.yml"; then
      pass "compose images are digest-pinned"
    else
      warn "compose images are not digest-pinned" "supply-chain: pin by @sha256 (see update.sh --images)"
    fi
  else
    warn "docker installed but daemon not reachable" "start it, or you're on a --minimal (no-Docker) install"
  fi
else
  warn "docker not installed" "fine for --minimal installs; the full stack needs it"
fi

# ── Services ────────────────────────────────────────────────────────────────
section "Services"
probe "http://$HEALTH_HOST:8080/health" "ok" \
  && pass "llama.cpp server (:8080)" \
  || warn "llama.cpp server not responding (:8080)" "./start.sh -d, or ./scripts/healthcheck.sh --restart"

if probe "http://$HEALTH_HOST:3001/health" "ok"; then
  mode=$(curl -s --max-time 4 "http://$HEALTH_HOST:3001/health" 2>/dev/null)
  auth=$(echo "$mode" | grep -o '"auth":"[a-z]*"' | cut -d'"' -f4)
  idn=$(echo "$mode" | grep -o '"identity":"[a-z]*"' | cut -d'"' -f4)
  pass "identity tool server (:3001) — auth=${auth:-?}, identity=${idn:-?}"
  if [[ -n "${MCPO_ADMIN_KEY:-}" && -n "${MCPO_GUEST_KEY:-}" && "$auth" != "keyed" ]]; then
    fail "profile keys are configured but the server reports auth=$auth" \
         "restart the stack so the tool server picks up the keys"
  fi
else
  warn "identity tool server not responding (:3001)" "./scripts/healthcheck.sh --restart"
fi

probe "http://$HEALTH_HOST:3000/api/version" "version" \
  && pass "Open WebUI (:3000)" \
  || warn "Open WebUI not responding (:3000)" "docker compose up -d, or it's still booting"

# ── Verdict ─────────────────────────────────────────────────────────────────
[[ $QUIET -eq 1 ]] || echo ""
echo "doctor: ${PASS} ok, ${WARN} warning(s), ${FAIL} failure(s)"
[[ $FAIL -eq 0 ]]
