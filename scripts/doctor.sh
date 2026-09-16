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

# ── Drive wear ──────────────────────────────────────────────────────────────
# NAND dies by WRITES, and agent workloads write hard (the 2026-07-07 OOM burned
# 187 GB of swap). Advisory only: ssd-wear.sh always exits 0 and degrades to a
# single warn row when smartctl is missing or unprivileged — it never FAILs.
if [[ -x "$SCRIPT_DIR/ssd-wear.sh" ]]; then
  section "Drive wear"
  while IFS='|' read -r _st _msg _fix; do
    [[ -z "$_st" ]] && continue
    case "$_st" in
      ok)   pass "$_msg" ;;
      warn) warn "$_msg" "$_fix" ;;
    esac
  done < <("$SCRIPT_DIR/ssd-wear.sh" --doctor 2>/dev/null || true)
fi

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

# ── Weight integrity (quick size check; sha256 is verify-weights.sh --deep) ─
# verify-weights exits 0 when weights match OR when none are downloaded yet
# (a fresh/minimal/CI checkout is not a failure), and nonzero ONLY on a real
# size mismatch — so its exit maps straight onto pass/fail here.
section "Weight registry"
if [[ -f "$REPO_DIR/scripts/weights.registry" ]]; then
  if _vw_out="$("$REPO_DIR/scripts/verify-weights.sh" 2>&1)"; then
    case "$_vw_out" in
      *"nothing to verify"*) pass "no weights downloaded yet (nothing to verify)" ;;
      *)                     pass "downloaded weights match their registry byte sizes" ;;
    esac
  else
    fail "a downloaded weight fails its registry size pin" \
         "./scripts/verify-weights.sh (then --deep to hash-verify)"
  fi
else
  warn "scripts/weights.registry missing" "restore it from git — it pins every shipped GGUF"
fi

# ── Model governance ────────────────────────────────────────────────────────
# Two questions an operator should be able to answer before trusting a rig:
# is the weight I serve the one that was vetted, and has the model I made the
# default actually earned it on THIS host?
section "Model governance"
_srv="$DEFAULT_SERVE_SCRIPT"
[[ -f "$REPO_DIR/.run/serve-script" ]] && _srv="$(head -n1 "$REPO_DIR/.run/serve-script" 2>/dev/null || echo "$_srv")"
_gguf="$(grep -oE '\$WEIGHTS_DIR/[^"]+\.gguf' "$REPO_DIR/scripts/$_srv" 2>/dev/null | head -1)"
_gguf="${_gguf##*/}"
if [[ -n "$_gguf" && -f "$REPO_DIR/scripts/weights.registry" ]]; then
  if awk -F'\t' -v n="$_gguf" '$0 !~ /^#/ && $3 == n {found=1} END{exit !found}' \
       "$REPO_DIR/scripts/weights.registry"; then
    case "${WEIGHT_ENFORCE:-warn}" in
      strict) pass "served weight is registry-pinned (WEIGHT_ENFORCE=strict)" ;;
      off)    warn "weight enforcement is off" "set WEIGHT_ENFORCE=warn (or strict) in openbeast.conf" ;;
      *)      pass "served weight is registry-pinned — safe to set WEIGHT_ENFORCE=strict" ;;
    esac
  else
    warn "served weight '$_gguf' is not in scripts/weights.registry" \
         "pin it (sha256 + bytes) before enabling WEIGHT_ENFORCE=strict"
  fi
fi
# Eval quality gate: promotion by evidence.
# THREE namespaces exist and conflating them produces permanent false alarms:
#   serve script  -a "Qwen3.8 27B Uncensored MTP Q5"   (display alias)
#   MODELS[].name "Qwen3.8 27B Uncensored MTP Q5_K_M"  -> slugified into the
#                                                        leaderboard's model_slug
#   MODELS[].slug "qwen38-27b-uncensored-mtp-q5"       -> what --models accepts
# The leaderboard key comes from MODELS[].name, NOT the serve alias, so
# resolve through benchmark_all.py's registry instead of guessing.
if [[ -f "$REPO_DIR/evals/leaderboard.json" && -f "$REPO_DIR/evals/benchmark_all.py" ]]; then
  _eval_out="$(OB_SRV="$_srv" python3 - "$REPO_DIR" <<'PYEOF' 2>/dev/null || true
import ast, json, os, re, sys
repo, srv = sys.argv[1], os.environ["OB_SRV"]
src = open(os.path.join(repo, "evals/benchmark_all.py")).read()
m = re.search(r"^MODELS = (\[.*?\n\])", src, re.S | re.M)
models = ast.literal_eval(m.group(1)) if m else []
entry = next((x for x in models
              if os.path.basename(x.get("serve", "")) == srv), None)
if entry is None:
    print("unregistered|" + srv); raise SystemExit
slug = re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-")
rows = json.load(open(os.path.join(repo, "evals/leaderboard.json"))).get("entries", [])
row = next((e for e in rows if e.get("model_slug") == slug), None)
if row:
    print("ok|%s|%s|%s" % (entry["name"], row.get("suite_version", "?"),
                           row.get("accuracy", "?")))
else:
    print("missing|%s|%s" % (entry["name"], entry["slug"]))
PYEOF
)"
  case "${_eval_out%%|*}" in
    ok)      pass "default model evaluated here ($(echo "$_eval_out" | cut -d'|' -f2), suite $(echo "$_eval_out" | cut -d'|' -f3))" ;;
    missing) warn "default model '$(echo "$_eval_out" | cut -d'|' -f2)' has NO leaderboard row on this host" \
                  "promotion by evidence: python3 evals/benchmark_all.py --models $(echo "$_eval_out" | cut -d'|' -f3) (GPU-hours), or accept it knowingly" ;;
    unregistered)
             warn "default serve script '$_srv' is not registered in evals/benchmark_all.py MODELS" \
                  "it cannot be benchmarked until added there — you are serving an unevaluated model knowingly" ;;
  esac
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

# beast-chat (opt-in) — the operator console for the rig's own sessions.
# Only checked when enabled: a row for a service nobody asked for is noise.
if [[ "${BEAST_CHAT:-false}" == "true" ]]; then
  # The health route answers an UNIDENTIFIED caller with exactly
  # {"status":"ok"} and nothing else — deliberately, so session counts and the
  # auth posture are not a free map of the rig. Probing it without a
  # credential therefore made the detail fields ALWAYS empty: every rig
  # printed "reads=?, ? running session(s)", and the no-allowlist warning
  # below could never fire because `$_chat_reads` was never populated. So
  # present the locality token, through a 0600 --config file and never argv
  # (`ps` is world-readable) — the same shape the beast-artifact row below
  # already uses.
  _chat_cfg=""
  _chat_tok="$(cat "$REPO_DIR/.run/chat-local.token" 2>/dev/null || true)"
  if [[ -n "$_chat_tok" ]]; then
    _chat_cfg="$(mktemp)"; chmod 600 "$_chat_cfg"
    printf 'header = "X-OpenBeast-Local: %s"\n' "$_chat_tok" > "$_chat_cfg"
  fi
  _chat=$(curl -s --max-time 4 ${_chat_cfg:+--config "$_chat_cfg"} \
            "http://$HEALTH_HOST:${CHAT_PORT:-3003}/api/chat/health" 2>/dev/null)
  [[ -n "$_chat_cfg" ]] && rm -f "$_chat_cfg"
  if echo "$_chat" | grep -qi '"status":"ok"'; then
    # [a-z-]: the value is a hyphenated word ("any-identified"), and a
    # [a-z]-only class silently matched nothing.
    _chat_reads=$(echo "$_chat" | grep -o '"reads":"[a-z-]*"' | cut -d'"' -f4)
    _chat_running=$(echo "$_chat" | grep -o '"running":-\?[0-9]*' | cut -d: -f2)
    pass "beast-chat console (:${CHAT_PORT:-3003}) — reads=${_chat_reads:-?}, ${_chat_running:-?} running session(s)"
    # An empty allowlist is not a failure (single-operator rig, tailnet you
    # own) but it IS the difference between "my phone" and "every device on
    # the tailnet", and only the operator can decide that. Say it out loud.
    # The server reports "any-identified" for an empty allowlist, not "open";
    # comparing against "open" meant this warning could never fire even once
    # the field was populated.
    if [[ "$_chat_reads" == "any-identified" || "$_chat_reads" == "open" ]]; then
      warn "beast-chat has no operator allowlist — every tailnet login can read every session" \
           "set CHAT_OPERATORS=<your-tailnet-login> in openbeast.conf (writes still need a chat-scoped key)"
    fi
  else
    warn "beast-chat enabled but not responding (:${CHAT_PORT:-3003})" \
         "./scripts/healthcheck.sh --restart"
  fi
fi

if [[ "${EDGE_GATE:-false}" == "true" ]]; then
  # Proof-of-locality header: the peer address can't distinguish local from
  # tailnet (tailscale serve proxies from loopback), so the gate keys this on
  # a 0600 token only readable on this box.
  _gate_tok=$(cat "$REPO_DIR/.run/edge-local.token" 2>/dev/null || true)
  _gate=$(curl -s --max-time 4 -H "X-OpenBeast-Local: ${_gate_tok}" "http://$HEALTH_HOST:${EDGE_PORT:-8090}/gate/health" 2>/dev/null)
  if [[ -n "$_gate" ]]; then
    _mode=$(echo "$_gate" | grep -o '"auth":"[a-z]*"' | cut -d'"' -f4)
    _ndev=$(echo "$_gate" | grep -o '"devices":[0-9]*' | cut -d: -f2)
    case "$_mode" in
      devices) pass "beast-gate (:${EDGE_PORT:-8090}) — ${_ndev:-?} enrolled device(s)" ;;
      anon)    warn "beast-gate is serving UNREGISTERED callers (EDGE_ALLOW_ANON=true)" \
                    "enroll devices and unset EDGE_ALLOW_ANON: ./scripts/clients.sh enroll <id>" ;;
      *)       warn "beast-gate is up but no devices are enrolled — remote clients get 401" \
                    "./scripts/clients.sh enroll <device-id>" ;;
    esac
  else
    warn "beast-gate not responding (:${EDGE_PORT:-8090})" "./scripts/healthcheck.sh --restart"
  fi
fi

if [[ "${BEAST_ARTIFACT:-false}" == "true" ]]; then
  # Health answers {"status":"ok"} and nothing else to an unauthenticated
  # caller — it is reachable by anything that can open the port, so it must
  # not report the store path or how many pages exist. doctor runs ON the rig,
  # so it presents the locality token to get the detailed body. Through a
  # 0600 --config file, never argv: `ps` is world-readable.
  _art_cfg=""
  _art_tok="$(cat "$REPO_DIR/.run/artifact-local.token" 2>/dev/null || true)"
  if [[ -n "$_art_tok" ]]; then
    _art_cfg="$(mktemp)"; chmod 600 "$_art_cfg"
    printf 'header = "X-OpenBeast-Local: %s"\n' "$_art_tok" > "$_art_cfg"
  fi
  _art=$(curl -s --max-time 4 ${_art_cfg:+--config "$_art_cfg"} \
           "http://$HEALTH_HOST:${ARTIFACT_PORT:-3004}/api/artifacts/health" 2>/dev/null)
  [[ -n "$_art_cfg" ]] && rm -f "$_art_cfg"
  if [[ -n "$_art" ]]; then
    _nart=$(echo "$_art" | grep -o '"artifacts":[0-9]*' | cut -d: -f2)
    if [[ -n "$_nart" ]]; then
      pass "beast-artifact (:${ARTIFACT_PORT:-3004}) — ${_nart} published page(s)"
    else
      # Alive, but it would not tell us the count: no token on disk, or the
      # server restarted and minted a new one after we read it.
      pass "beast-artifact (:${ARTIFACT_PORT:-3004}) — serving (page count needs .run/artifact-local.token)"
    fi
  else
    warn "beast-artifact not responding (:${ARTIFACT_PORT:-3004})" \
         "./scripts/healthcheck.sh --restart, or unset BEAST_ARTIFACT in openbeast.conf"
  fi
fi

# ── Published tailnet surfaces (beast-slot) ─────────────────────────────────
# Informational: what tailscale serve currently maps, and whether the raw
# inference endpoint is published without a bearer key. Keyless is the
# documented default on a personal tailnet — WARN, never FAIL.
if command -v tailscale >/dev/null 2>&1; then
  section "Tailnet surfaces"
  _serve=$(tailscale serve status 2>/dev/null || true)
  if [[ -n "$_serve" ]]; then
    # Print the mappings as tailscale reports them. Don't grep per-port: the
    # default :443 entry prints WITHOUT a port token, so a port-keyed loop
    # silently omits the WebUI.
    while IFS= read -r _line; do
      [[ -z "${_line// }" ]] && continue
      case "$_line" in
        https://*) _url="${_line%% *}" ;;
        *proxy*)   pass "published ${_url:-?} → ${_line##*proxy }" ;;
      esac
    done <<< "$_serve"
    # :8445 is beast-chat — a WRITE surface (send a message to a live agent,
    # stop it, start a new one), so a mount pointing at a dead process is
    # worth more than a shrug: the operator thinks they can reach their rig.
    if echo "$_serve" | grep -qE ':8445[^0-9]'; then
      if curl -s --max-time 4 "http://$HEALTH_HOST:${CHAT_PORT:-3003}/api/chat/health" 2>/dev/null | grep -qi '"status":"ok"'; then
        pass "beast-chat published on :8445 (tailnet-only)"
      else
        fail ":8445 is published but beast-chat is NOT responding" \
             "the console 502s from the phone — set BEAST_CHAT=true and restart, or ./scripts/setup-tailscale.sh --unpublish-chat"
      fi
    elif [[ "${BEAST_CHAT:-false}" == "true" ]]; then
      warn "BEAST_CHAT=true but :8445 is not published — the console is loopback-only" \
           "./scripts/setup-tailscale.sh --publish-chat"
    fi
    if echo "$_serve" | grep -qE ':8443[^0-9]'; then
      # What sits behind :8443 decides the real exposure. The gate allowlists
      # the OpenAI routes and keys per device; raw llama-server publishes its
      # entire route table (/lora-adapters, /slots, /v1/stream) to the tailnet.
      if echo "$_serve" | grep -A2 -E ":8443[^0-9]" | grep -q ":${EDGE_PORT:-8090}"; then
        # The mapping alone is not proof: if the gate process is down, :8443
        # is a 502 and reporting "per-device keys + audit" would be a
        # dangerously false green. Probe it.
        if curl -s --max-time 4 "http://$HEALTH_HOST:${EDGE_PORT:-8090}/gate/health" 2>/dev/null | grep -q "beast-gate"; then
          pass "inference published via beast-gate (per-device keys + audit)"
        else
          fail ":8443 points at beast-gate but the gate is NOT responding" \
               "remote clients are getting 502 — ./scripts/healthcheck.sh --restart"
        fi
      elif [[ "${EDGE_GATE:-false}" == "true" ]]; then
        warn "EDGE_GATE=true but :8443 still points at raw llama-server" \
             "re-run ./scripts/setup-tailscale.sh to repoint it at the gate"
      elif [[ -z "${LLAMA_API_KEY:-}" ]]; then
        warn "raw llama-server is published on :8443 with no API key" \
             "fine on a personal tailnet you fully own; otherwise set EDGE_GATE=true (per-device keys, path allowlist, audit) — docs/BEAST_SLOT.md"
      else
        warn "raw llama-server is published on :8443 (whole route table)" \
             "the shared key gates it but doesn't shrink it; EDGE_GATE=true allowlists just the OpenAI routes"
      fi
    fi
  else
    pass "no tailscale serve mappings (stack is localhost-only)"
  fi

  # ── Certificate expiry ──────────────────────────────────────────────────
  # HTTPS on every published port is a Let's Encrypt cert that tailscaled
  # renews THROUGH the coordination server. On a closed network that renewal
  # cannot happen, so a cached cert simply runs out — and nothing in the repo
  # noticed: there was no `tailscale cert` call anywhere and no expiry check
  # here. A surface that stops trusting itself in 90 days, silently, is worse
  # than one that was never published.
  #
  # Read from the LIVE port with openssl rather than tailscaled's cert store:
  # no root needed, and it checks what a client actually gets.
  if [[ -n "$_serve" ]] && command -v openssl >/dev/null 2>&1; then
    _fqdn="$(tailscale status --json 2>/dev/null \
             | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["Self"].get("DNSName","").rstrip("."))
except Exception: print("")' 2>/dev/null)"
    if [[ -n "$_fqdn" ]]; then
      # One port is enough: tailscaled serves the same cert on all of them.
      _port="$(grep -oE 'https://[^ ]+:([0-9]+)' <<< "$_serve" | head -1 | sed 's/.*://')"
      _port="${_port:-443}"
      _end="$(timeout 15 openssl s_client -connect "$_fqdn:$_port" -servername "$_fqdn" \
                </dev/null 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null \
              | cut -d= -f2)"
      if [[ -z "$_end" ]]; then
        warn "could not read the TLS certificate on $_fqdn:$_port" \
             "if the surfaces are up this is usually transient; re-run"
      else
        _left=$(( ( $(date -d "$_end" +%s 2>/dev/null || echo 0) - $(date +%s) ) / 86400 ))
        if [[ $_left -lt 0 ]]; then
          fail "TLS certificate for $_fqdn EXPIRED $(( -_left )) days ago" \
               "renewal needs the tailscale coordination server — reconnect, or see docs/REMOTE_ACCESS_PLAN.md"
        elif [[ $_left -le 30 ]]; then
          warn "TLS certificate for $_fqdn expires in $_left days (renews at 30 via the coordination server)" \
               "on a closed network that renewal cannot happen and the cert will simply run out"
        else
          pass "TLS certificate for $_fqdn valid for $_left more days"
        fi
      fi
    fi
  fi
fi

# ── beast-lang: what this rig can tell a model about a language ────────────
# Always shown, never a failure. The point of the row is that the answer is a
# property of the BOX, not of the repo: which packs a model gets here depends
# on which toolchains are installed, so "why did the model not know that"
# should be answerable without reading any Python.
if [[ -f "$REPO_DIR/agents/lang/packs.py" ]]; then
  _lang_out="$(cd "$REPO_DIR" && timeout 60 python3 agents/lang/packs.py 2>&1 || true)"
  _lang_active="$(sed -n 's/.*-> active: //p' <<< "$_lang_out" | head -1)"
  if [[ -n "$_lang_active" ]]; then
    pass "beast-lang packs active for: $_lang_active"
  elif grep -q "active:" <<< "$_lang_out"; then
    # Deliberately off, or no toolchain the rig can ask. Both are choices.
    warn "beast-lang has no active language packs" \
         "LANG_PACKS=auto in openbeast.conf, and install the toolchains you want asked"
  else
    warn "beast-lang did not report (see: python3 agents/lang/packs.py)" \
         "$(head -1 <<< "$_lang_out")"
  fi
  # A hand-edited GENERATED file is the one state worth failing on: it is no
  # longer generated, and it would be served as though a compiler had said it.
  if [[ -d "$REPO_DIR/agents/lang/generated" ]]; then
    _lang_chk="$(cd "$REPO_DIR" && timeout 60 ./scripts/lang-introspect.sh check 2>&1 || true)"
    if grep -q DRIFTED <<< "$_lang_chk"; then
      fail "a beast-lang generated fact file was EDITED by hand" \
           "it is no longer generated: ./scripts/lang-introspect.sh write <lang>"
    elif grep -q STALE <<< "$_lang_chk"; then
      warn "a beast-lang generated fact file predates the installed toolchain" \
           "./scripts/lang-introspect.sh write   (the serving path probes live, so this is cosmetic)"
    fi
  fi
fi

# ── Verdict ─────────────────────────────────────────────────────────────────
[[ $QUIET -eq 1 ]] || echo ""
echo "doctor: ${PASS} ok, ${WARN} warning(s), ${FAIL} failure(s)"
[[ $FAIL -eq 0 ]]
