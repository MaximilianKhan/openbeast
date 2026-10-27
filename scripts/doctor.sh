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
# doctor only READS. Without this, sourcing conf.sh on a fresh checkout minted
# a SearXNG secret and created openbeast.conf — and so did the scripts doctor
# runs underneath (configure-webui.sh --check-default-admin), hence exported.
export OB_CONF_READONLY=1
# conf.sh's unknown-key / bad-value findings are shown as rows under "Config &
# secrets" below, so its own one-time stderr copy is switched off.
export OB_CONF_LINTED=1
source "$SCRIPT_DIR/lib/conf.sh"
source "$SCRIPT_DIR/lib/hardware.sh" 2>/dev/null || true

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

source "$SCRIPT_DIR/lib/net.sh"   # ob_probe_host — the mapping start.sh and healthcheck.sh use
source "$SCRIPT_DIR/lib/backend.sh"   # ob_backend_ready, ob_inference_managed, ob_backend_na
source "$SCRIPT_DIR/lib/proc.sh"      # ob_recorded_pid_ours — is the supervisor alive
# ob_curl_hdr / ob_curl_bearer: every credential header below rides curl's
# --config on fd 3, never argv (`ps` / /proc/*/cmdline are world-readable).
source "$SCRIPT_DIR/lib/curl_auth.sh"
# Where the core services answer (they bind BIND_HOST). It used to be a
# private copy of the mapping with no `::` arm, so BIND_HOST=:: built
# http://:::8080 and every service read as down on a healthy stack.
HEALTH_HOST="$(ob_probe_host "$BIND_HOST")"
# beast-chat binds OPENBEAST_CHAT_BIND (loopback by default), NOT BIND_HOST —
# start.sh and healthcheck.sh probe it there; doctor probed BIND_HOST, and on
# a rig with a LAN BIND_HOST called a healthy console down (and a published
# :8445 a FAIL).
CHAT_HEALTH_HOST="$(ob_probe_host "${OPENBEAST_CHAT_BIND:-127.0.0.1}")"

PASS=0 WARN=0 FAIL=0
# The first failure's fix and the first warning's — the closing "Next:" line
# names ONE thing to do, so a long report still ends on an instruction.
# A row's second line is sometimes a command and sometimes an explanation
# ("CPU-only works but…", "OpenBeast targets 3090 / 4090 class and up");
# "Next:" wants the first that names something to RUN or SET, and keeps the
# first failure's explanation only as a last resort.
NEXT_FAIL="" NEXT_FAIL_ANY="" NEXT_WARN=""
_NEXT_CMD_RE='(\./|scripts/|chmod |sudo |docker compose|python3 |set [A-Z_]+=)'
section() { [[ $QUIET -eq 1 ]] || printf '\n\033[1m%s\033[0m\n' "$1"; }
pass()    { [[ $QUIET -eq 1 ]] || echo "  ✓ $1"; PASS=$((PASS+1)); }
warn()    { echo "  ! $1"; [[ -n "${2:-}" ]] && echo "      → $2"
            [[ -z "$NEXT_WARN" && "${2:-}" =~ $_NEXT_CMD_RE ]] && NEXT_WARN="$2"; WARN=$((WARN+1)); }
fail()    { echo "  ✗ $1"; [[ -n "${2:-}" ]] && echo "      → fix: $2"
            [[ -z "$NEXT_FAIL" && "${2:-}" =~ $_NEXT_CMD_RE ]] && NEXT_FAIL="$2"
            [[ -z "$NEXT_FAIL_ANY" && -n "${2:-}" ]] && NEXT_FAIL_ANY="$2"; FAIL=$((FAIL+1)); }
# Worth knowing, nothing to do: not counted, not shown under --quiet.
info()    { [[ $QUIET -eq 1 ]] || echo "  - $1"; }
# A llama-only row on a stack that does not run llama-server here: one line,
# counted as neither pass nor problem (lib/backend.sh ob_backend_na).
na()      { [[ $QUIET -eq 1 ]] || echo "  - $(ob_backend_na "$1")"; }
# The local-llama rows (weights, governance) apply only when this stack
# launches llama-server itself.
LOCAL_LLAMA=0
[[ "$INFERENCE_BACKEND" == "llama" ]] && ob_inference_managed && LOCAL_LLAMA=1

# curl a health URL; $3 optional bearer key. Returns 0 if the body matches $2.
probe() { # probe <url> <match> [key]
  ob_curl_bearer "${3:-}" -s --max-time 4 "$1" 2>/dev/null | grep -qi "$2"
}

# ── Hardware ────────────────────────────────────────────────────────────────
section "Hardware"
if command -v ob_detect_gpu >/dev/null 2>&1; then
  ob_detect_gpu 2>/dev/null || true
  if [[ "${OB_GPU_VENDOR:-none}" == "none" ]]; then
    warn "no supported GPU detected" "CPU-only works but the 27B default is impractical"
  elif [[ "${OB_VRAM_MB:-0}" -gt 0 && "${OB_VRAM_MB:-0}" -lt "${OB_VRAM_FLOOR_MB:-22000}" ]]; then
    fail "GPU has ${OB_VRAM_MB} MiB VRAM — below the 24 GB floor" \
         "OpenBeast targets 3090 / 4090 class and up (docs/HARDWARE_PROFILES.md)"
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
# Typos and values that cannot work (lib/conf.sh ob_conf_lint): a misspelt key
# is ignored in silence — `EDGE_GTAE=true` left the gate off — and a
# non-integer REASONING_BUDGET surfaced only as "llama-server exited".
# Warnings, never failures: the stack starts exactly as it would have.
_lint_n=0
while IFS= read -r _lint; do
  [[ -n "$_lint" ]] || continue
  _lint_n=$((_lint_n + 1))
  warn "$_lint" "edit openbeast.conf (every key and its values: openbeast.conf.example)"
done < <(ob_conf_lint 2>/dev/null || true)
if [[ $_lint_n -eq 0 && -f "$CONF" && -f "$REPO_DIR/openbeast.conf.example" ]]; then
  pass "openbeast.conf: no unknown keys, integer keys are integers"
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
# Loopback is decided by the one helper conf.sh uses (ob_bind_is_loopback):
# this row used to call ANY value other than 0.0.0.0/:: "loopback-scoped", so
# BIND_HOST=192.168.1.20 — every service on the LAN — printed a green check.
if ! ob_bind_is_loopback "$BIND_HOST"; then
  warn "BIND_HOST=$BIND_HOST is not loopback — the stack's services are reachable from that network" \
       "prefer Tailscale (scripts/setup-tailscale.sh); set BIND_HOST=127.0.0.1"
  if ob_tools_exposed_open; then
    if [[ "${ALLOW_OPEN_TOOLS:-false}" == "true" ]]; then
      warn "tool server keyless on a network bind (ALLOW_OPEN_TOOLS=true acknowledges it)" \
           "./scripts/setup-mcpo-keys.sh"
    else
      fail "tool server keyless on a network bind — anyone on that network can run shell commands" \
           "./scripts/setup-mcpo-keys.sh (or BIND_HOST=127.0.0.1)"
    fi
  fi
else
  pass "bind host is loopback-scoped ($BIND_HOST)"
fi

# ── Weight integrity (quick size check; sha256 is verify-weights.sh --deep) ─
# verify-weights exits 0 when weights match OR when none are downloaded yet
# (a fresh/minimal/CI checkout is not a failure), and nonzero ONLY on a real
# size mismatch — so its exit maps straight onto pass/fail here.
section "Weight registry"
if [[ $LOCAL_LLAMA -eq 0 ]]; then
  na "the GGUF weight registry / WEIGHT_ENFORCE"
elif [[ -f "$REPO_DIR/scripts/weights.registry" ]]; then
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
if [[ $LOCAL_LLAMA -eq 0 ]]; then
  na "served-weight pinning and the leaderboard gate"
elif [[ -n "$_gguf" && -f "$REPO_DIR/scripts/weights.registry" ]]; then
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
if [[ $LOCAL_LLAMA -eq 1 && -f "$REPO_DIR/evals/leaderboard.json" && -f "$REPO_DIR/evals/benchmark_all.py" ]]; then
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
# A model can hold a row per suite (v4 and v4.1): report the newest suite's.
mine = [e for e in rows if e.get("model_slug") == slug]
row = max(mine, key=lambda e: [int(x) for x in re.findall(r"\d+", str(e.get("suite_version", "")))],
          default=None)
if row:
    print("ok|%s|%s|%s" % (entry["name"], row.get("suite_version", "?"),
                           row.get("accuracy", "?")))
else:
    print("missing|%s|%s" % (entry["name"], entry["slug"]))
PYEOF
)"
  case "${_eval_out%%|*}" in
    ok)      pass "default model evaluated here ($(echo "$_eval_out" | cut -d'|' -f2), suite $(echo "$_eval_out" | cut -d'|' -f3))" ;;
    # Info, not a warning: evals/results/ is not checked in, so a fresh
    # install serves the shipped default with no local row, and the only way
    # to clear the warning was GPU-hours of benchmarking — a permanent "!"
    # nobody could act on, which teaches people to skim the rest.
    missing) info "default model '$(echo "$_eval_out" | cut -d'|' -f2)' has no leaderboard row on this host (optional, GPU-hours: python3 evals/benchmark_all.py --models $(echo "$_eval_out" | cut -d'|' -f3))" ;;
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
      # pydeps.sh, not a bare `pip install --user -r …`: that fails with
      # externally-managed-environment on Arch / Debian 12+ / Ubuntu 24.04
      # and installs outside the hash-pinned lock.
      fail "$pkg not installed (pinned $ver)" "./scripts/pydeps.sh install"
    elif [[ "$have" != "$ver" ]]; then
      warn "$pkg is $have, pinned $ver" "./scripts/update.sh --python (or reinstall the pin)"
    else
      pass "$pkg==$ver"
    fi
  done < <(grep -vE '^\s*#|^\s*$' "$REPO_DIR/agents/requirements.txt")
  # The four lines above are the DIRECT pins. The closure is 43 packages, and
  # the lock is what pins their content — so the question this row answers is
  # not "is the lock pretty" but "could this box reinstall exactly what it is
  # running, offline". A stale lock means it could not.
  if [[ -f "$REPO_DIR/agents/requirements.lock" ]]; then
    _lk="$(cd "$REPO_DIR" && ./scripts/pydeps.sh verify 2>&1 || true)"
    if grep -q '^agents/requirements.lock: OK' <<< "$_lk"; then
      pass "$(sed 's|agents/requirements.lock: OK — |hash-pinned lock: |' <<< "$_lk" | head -1)"
    else
      warn "the hash-pinned lock does not match agents/requirements.txt" \
           "./scripts/pydeps.sh lock   ($(sed -n '2p' <<< "$_lk" | sed 's/^\s*-\s*//'))"
    fi
  else
    warn "no agents/requirements.lock — only the 4 direct versions are pinned, and no content is" \
         "./scripts/pydeps.sh lock  (also what makes an offline install verifiable)"
  fi
fi

# ── Remote skills ───────────────────────────────────────────────────────────
# A skill body goes straight into a sub-agent's system prompt, so an imported
# one is pinned by hash like any other supply-chain input. This row is the
# hash check only (no scanner, no network): docs/EXTERNAL_SKILLS_PLAN.md.
section "Remote skills"
_sk="$("$SCRIPT_DIR/skill-import.sh" verify --quiet 2>&1)" && _sk_rc=0 || _sk_rc=$?
if [[ $_sk_rc -eq 0 ]]; then
  pass "$(tail -1 <<< "$_sk" | sed 's/^remote skills: OK — /remote skills: /')"
elif [[ $_sk_rc -eq 3 ]]; then
  # 3 is the gate's own "no": it looked, and a row and the files disagree.
  fail "an imported skill no longer matches skills/REMOTE_PROVENANCE.md" \
       "./scripts/skill-import.sh verify   (an edit to an imported skill is a re-import)"
else
  # Anything else means the check did not run (no script, no python, no
  # ledger). That is not evidence of tampering, so it is not reported as one.
  warn "remote-skill hash check could not run (exit $_sk_rc)" \
       "./scripts/skill-import.sh verify"
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
# IS THE STACK RUNNING AT ALL? Asked first, because on a stack that is simply
# stopped the rows below used to be a pile — llama, the tool server, WebUI and
# every opt-in service each "not responding", each with a different fix — and
# nowhere the one true sentence. "Not running" = no live supervisor AND
# neither core service this box runs answers (a watchdog-relaunched stack has
# no supervisor but does answer; an unmanaged backend is someone else's box
# and does not count either way). The probes are the rows' own, made once.
_sup_alive=0
ob_recorded_pid_ours "$REPO_DIR/.run/supervisor.pid" 'start\.sh' && _sup_alive=1
_llama_up=0
[[ $LOCAL_LLAMA -eq 1 ]] && probe "http://$HEALTH_HOST:8080/health" "ok" && _llama_up=1
_tools_up=0
probe "http://$HEALTH_HOST:3001/health" "ok" && _tools_up=1
STACK_DOWN=0
if [[ $_sup_alive -eq 0 && $_tools_up -eq 0 && ( $LOCAL_LLAMA -eq 0 || $_llama_up -eq 0 ) ]]; then
  STACK_DOWN=1
  # .run/stopped: "<timestamp> <who>" — ./stop.sh (or a script that calls it),
  # or the supervisor / watchdog giving up on a crash-looping model.
  _stopped="$(head -n1 "$REPO_DIR/.run/stopped" 2>/dev/null || true)"
  case "$_stopped" in
    "")  _down_why="" ;;
    *"gave up"*|*"watchdog:"*)
         _down_why=" (it gave up ${_stopped%% *}: ${_stopped#* } — see .run/stack.log)" ;;
    *)   _down_why=" (stopped on purpose ${_stopped%% *})" ;;
  esac
  # One line, cause and fix together; the per-service "not responding" rows
  # below are then left out — this line is all of them.
  echo "  ! Stack is not running${_down_why} — start it: ./start.sh -d"
  WARN=$((WARN+1))
fi
# down_row <message> <fix> — a service that does not answer: a warning on a
# running stack, nothing at all on a stopped one (the line above said it).
down_row() { [[ $STACK_DOWN -eq 1 ]] || warn "$1" "$2"; }
# INFERENCE_URL defaults to exactly http://$HEALTH_HOST:8080 (lib/conf.sh).
if [[ "$INFERENCE_BACKEND" != "llama" ]] || ! ob_inference_managed; then
  # Someone else's server (vLLM / TensorFold across the Sparks, or a
  # llama-server on another box): ask it the question ITS /health answers.
  if ob_backend_ready "$INFERENCE_URL"; then
    _be_models="$(ob_backend_models "$INFERENCE_URL" 2>/dev/null | head -3 | tr '\n' ' ' || true)"
    pass "$(ob_backend_label) at $INFERENCE_URL (not managed here) — serving: ${_be_models:-? (/v1/models unreadable — LLAMA_API_KEY?)}"
    # The id the agent runner sends (OPENBEAST_INFERENCE_MODEL). llama
    # ignores ids; vLLM 404s an unlisted one unless validation is skipped.
    if [[ "$INFERENCE_BACKEND" != "llama" ]]; then
      if [[ -z "${INFERENCE_MODEL:-}" ]]; then
        warn "INFERENCE_MODEL is not set — the agent runner sends a llama-era model id" \
             "scripts/backends/conformance.sh, then scripts/backends/use-model.sh --profile <name>"
      elif [[ -n "$_be_models" ]] && ! ob_backend_models "$INFERENCE_URL" 2>/dev/null | grep -qxF -- "$INFERENCE_MODEL"; then
        warn "INFERENCE_MODEL='$INFERENCE_MODEL' is not what $INFERENCE_URL lists (${_be_models% })" \
             "scripts/backends/use-model.sh --profile <the profile the Sparks serve>"
      else
        pass "INFERENCE_MODEL='$INFERENCE_MODEL' (the id the agent runner sends)"
      fi
    fi
  else
    warn "$(ob_backend_label) at $INFERENCE_URL is not ready (INFERENCE_BACKEND=$INFERENCE_BACKEND, not managed here)" \
         "start it where it runs (docs/DGX_SPARK_PLAN.md) — healthcheck --restart will not"
  fi
  # vLLM leaves /health and /metrics open even with --api-key, and TensorFold
  # has no key at all: the network path to it IS the access control.
  # ob_url_host strips an IPv6 literal's brackets: http://[::1]:8000 is
  # loopback (the old split left "::1]" and warned about it).
  _be_host="$(ob_url_host "$INFERENCE_URL")"
  if ! ob_bind_is_loopback "$_be_host"; then
    if [[ "$INFERENCE_BACKEND" == "tensorfold" ]]; then
      warn "TensorFold has no API key: anyone who can reach $INFERENCE_URL can use it" \
           "bind it to the ConnectX / tailnet address only and firewall it (docs/DGX_SPARK_PLAN.md § Security)"
    elif [[ -z "${LLAMA_API_KEY:-}" ]]; then
      warn "the inference server at $INFERENCE_URL is reached without a key (LLAMA_API_KEY empty)" \
           "start vLLM with an API key (spark.env VLLM_API_KEY_FILE) and set the same LLAMA_API_KEY here"
    fi
  fi
elif [[ $_llama_up -eq 1 ]]; then
  pass "llama.cpp server (:8080)"
  # The model is up — but can the FRONTEND reach it? Open WebUI dials
  # OPENBEAST_MODEL_URL (localhost), and a server bound to a specific LAN or
  # tailnet BIND_HOST refuses localhost: chat has no model while every probe
  # above, which follows BIND_HOST, reads green. Dial what WebUI dials.
  _mu="${OPENBEAST_MODEL_URL:-}"
  if [[ -n "$_mu" ]]; then
    _mcode="$(curl -s -o /dev/null -m 4 -w '%{http_code}' "${_mu%/}/models" 2>/dev/null || true)"
    if [[ -z "$_mcode" || "$_mcode" == "000" ]]; then
      if [[ "${AGENT_ROUTER:-false}" == "true" ]]; then
        # With the router on, this endpoint IS the router (it hard-binds
        # loopback, whatever BIND_HOST is), so silence here is a dead router
        # and nothing else. healthcheck.sh has a router branch that relaunches
        # it with start.sh's environment — this advice used to name a repair
        # that did not exist.
        fail "Open WebUI's model endpoint ($_mu) is the agent router, and it is not answering — chat has no model, though llama-server is up" \
             "./scripts/healthcheck.sh --restart (relaunches the router), or set AGENT_ROUTER=false and ./stop.sh && ./start.sh -d"
      elif [[ "$HEALTH_HOST" != "127.0.0.1" && "$HEALTH_HOST" != "[::1]" ]]; then
        fail "Open WebUI's model endpoint ($_mu) refuses connections: services bind only $BIND_HOST" \
             "set BIND_HOST=127.0.0.1 (remote access via Tailscale) or 0.0.0.0 — frontends dial localhost"
      else
        # Not the router (handled above): beast-hydra, whose own section below
        # says more, or an endpoint the running stack was not started with.
        _mfix="./stop.sh && ./start.sh -d (the running stack and openbeast.conf disagree on the endpoint)"
        [[ "${HYDRA:-false}" == "true" ]] && _mfix="./scripts/healthcheck.sh --restart (relaunches beast-hydra)"
        fail "Open WebUI's model endpoint ($_mu) is not answering, though llama-server is" "$_mfix"
      fi
    fi
  fi
else
  down_row "llama.cpp server not responding (:8080)" "./start.sh -d, or ./scripts/healthcheck.sh --restart"
fi

if [[ $_tools_up -eq 1 ]]; then
  mode=$(curl -s --max-time 4 "http://$HEALTH_HOST:3001/health" 2>/dev/null)
  auth=$(echo "$mode" | grep -o '"auth":"[a-z]*"' | cut -d'"' -f4)
  idn=$(echo "$mode" | grep -o '"identity":"[a-z]*"' | cut -d'"' -f4)
  pass "identity tool server (:3001) — auth=${auth:-?}, identity=${idn:-?}"
  if [[ -n "${MCPO_ADMIN_KEY:-}" && -n "${MCPO_GUEST_KEY:-}" && "$auth" != "keyed" ]]; then
    fail "profile keys are configured but the server reports auth=$auth" \
         "restart the stack so the tool server picks up the keys"
  fi
else
  down_row "identity tool server not responding (:3001)" "./scripts/healthcheck.sh --restart"
fi

_WEBUI_UP=0
if probe "http://$HEALTH_HOST:3000/api/version" "version"; then
  _WEBUI_UP=1
  pass "Open WebUI (:3000)"
else
  down_row "Open WebUI not responding (:3000)" "docker compose up -d, or it's still booting"
fi
# What the RUNNING WebUI enforces (features.auth on the public /api/config):
# true / false / unknown. The conf can say one thing while the container,
# started before the conf changed, still does the other.
_webui_live_auth() {
  curl -s --max-time 4 "http://$HEALTH_HOST:3000/api/config" 2>/dev/null \
    | python3 -c "
import sys, json
try:
    a = json.load(sys.stdin).get('features', {}).get('auth')
except Exception:
    a = None
print('unknown' if a is None else ('true' if a else 'false'))" 2>/dev/null \
    || true
}
# Upstream's built-in admin@localhost / "admin" (network-exposure-1): with
# login ON, a WebUI that still accepts it hands admin — and the privileged
# tool connection, i.e. bash — to anyone who can reach it. configure-webui.sh
# rotates it on start; this row catches a rig where that did not happen. The
# probe is configure-webui.sh's own (password via env + stdin, never argv).
if [[ $_WEBUI_UP -eq 1 && -x "$SCRIPT_DIR/configure-webui.sh" ]]; then
  WEBUI_URL="http://$HEALTH_HOST:3000" "$SCRIPT_DIR/configure-webui.sh" --check-default-admin >/dev/null 2>&1
  case $? in
    1) fail "WebUI login is on, but admin@localhost still signs in with upstream's default password" \
            "./scripts/configure-webui.sh --secure-default-admin (or change it in Settings → Account)" ;;
    0) pass "built-in WebUI admin does not accept the upstream default password" ;;
    # 4: login is off (or not reported) on the running WebUI, so the probe
    # did not sign in at all — no row, never a green check it did not earn.
    # 3/other: WebUI went away between the two probes — the row above covers it.
    *) ;;
  esac
fi

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
  _chat_tok="$(cat "$REPO_DIR/.run/chat-local.token" 2>/dev/null || true)"
  _chat=$(ob_curl_hdr "${_chat_tok:+X-OpenBeast-Local: $_chat_tok}" -s --max-time 4 \
            "http://$CHAT_HEALTH_HOST:${CHAT_PORT:-3003}/api/chat/health" 2>/dev/null)
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
    down_row "beast-chat enabled but not responding (:${CHAT_PORT:-3003})" \
         "./scripts/healthcheck.sh --restart"
  fi
fi

if [[ "${EDGE_GATE:-false}" == "true" ]]; then
  # Proof-of-locality header: the peer address can't distinguish local from
  # tailnet (tailscale serve proxies from loopback), so the gate keys this on
  # a 0600 token only readable on this box.
  _gate_tok=$(cat "$REPO_DIR/.run/edge-local.token" 2>/dev/null || true)
  _gate=$(ob_curl_hdr "${_gate_tok:+X-OpenBeast-Local: $_gate_tok}" -s --max-time 4 \
            "http://$HEALTH_HOST:${EDGE_PORT:-8090}/gate/health" 2>/dev/null)
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
    down_row "beast-gate not responding (:${EDGE_PORT:-8090})" "./scripts/healthcheck.sh --restart"
  fi
fi

# Devices enrolled, gate off: clients.sh wrote keys into .run/clients.json, but
# only beast-gate reads that file. Whatever answers :8443 then is llama-server
# itself, which never checks a device key — a revoked laptop is still served,
# and the operator who enrolled it believes otherwise. clients.sh warns at
# enroll time; this is the same fact for whoever was not watching then.
# The count is read here, not asked of the gate: the gate is not running.
if [[ "${EDGE_GATE:-false}" != "true" && -s "$REPO_DIR/.run/clients.json" ]]; then
  _n_enrolled="$(python3 -c 'import json, sys
try:
    d = json.load(open(sys.argv[1])).get("devices")
    print(len(d) if isinstance(d, (list, dict)) else 0)
except Exception:
    print(0)' "$REPO_DIR/.run/clients.json" 2>/dev/null || true)"
  if [[ "${_n_enrolled:-0}" =~ ^[0-9]+$ && "${_n_enrolled:-0}" -gt 0 ]]; then
    fail "$_n_enrolled device(s) enrolled but EDGE_GATE is not true — their keys are NOT enforced (a revoked or never-enrolled device is still served)" \
         "set EDGE_GATE=true in openbeast.conf, ./stop.sh && ./start.sh -d, then ./scripts/setup-tailscale.sh"
  fi
fi

if [[ "${BEAST_ARTIFACT:-false}" == "true" ]]; then
  # Health answers {"status":"ok"} and nothing else to an unauthenticated
  # caller — it is reachable by anything that can open the port, so it must
  # not report the store path or how many pages exist. doctor runs ON the rig,
  # so it presents the locality token to get the detailed body. Through a
  # 0600 --config file, never argv: `ps` is world-readable.
  _art_tok="$(cat "$REPO_DIR/.run/artifact-local.token" 2>/dev/null || true)"
  _art=$(ob_curl_hdr "${_art_tok:+X-OpenBeast-Local: $_art_tok}" -s --max-time 4 \
           "http://$HEALTH_HOST:${ARTIFACT_PORT:-3004}/api/artifacts/health" 2>/dev/null)
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
    down_row "beast-artifact not responding (:${ARTIFACT_PORT:-3004})" \
         "./scripts/healthcheck.sh --restart, or unset BEAST_ARTIFACT in openbeast.conf"
  fi
fi

# Whether an address is loopback, as ob_probe_host prints it.
_is_loop() { case "$1" in 127.*|"[::1]"|::1|localhost) return 0 ;; *) return 1 ;; esac; }

if [[ "${BEAST_ARTIFACT:-false}" == "true" ]]; then
  # An EMPTY allowlist is not "open": the rig's own publishes (artifact.sh,
  # the model's tools, campaign scripts) are owned by the principal "rig",
  # private is the default visibility, and a phone always presents its real
  # tailnet login — so on the default config every private page opens for
  # nobody from a phone, the operator included (integration-ops-1/-5). The
  # admin who may open rig pages is ARTIFACT_ADMINS, else the FIRST operator;
  # the server falls back to CHAT_OPERATORS, so any of the three counts.
  _art_ops="${OPENBEAST_ARTIFACT_OPERATORS:-}${OPENBEAST_CHAT_OPERATORS:-}"
  _art_ops+="${OPENBEAST_ARTIFACT_ADMINS:-$(_ob_conf_value ARTIFACT_ADMINS 2>/dev/null || true)}"
  if [[ -z "${_art_ops// /}" ]]; then
    warn "beast-artifact has no operator allowlist — private pages (the default) cannot be opened from a phone, by anyone" \
         "set ARTIFACT_OPERATORS=<your-tailnet-login> in openbeast.conf, then ./stop.sh && ./start.sh -d"
  fi
  # No bind caveat any more: a login header counts from loopback OR from a
  # peer whose address is the one the socket was accepted on — which is how
  # tailscale serve reaches a LAN BIND_HOST from this box (security-1). The
  # old "not honoured through :8446" WARN described a rule the server no
  # longer has.
fi
if [[ "${BEAST_CHAT:-false}" == "true" ]] && ! _is_loop "$CHAT_HEALTH_HOST"; then
  warn "beast-chat binds $CHAT_HEALTH_HOST (OPENBEAST_CHAT_BIND), not loopback — tailnet logins are not honoured through :8445" \
       "unset OPENBEAST_CHAT_BIND to restore login-gated reads"
fi

# ── Notifications (beast-chat → ntfy) ───────────────────────────────────────
# Only when configured or when the ntfy extension is on: a row for a feature
# nobody asked for is noise. The topic path is never printed — with ntfy's
# default open access the topic name IS the secret.
_ntfy_on=0
[[ " ${EXTENSIONS:-} " == *" ntfy "* ]] && _ntfy_on=1
if [[ -n "${CHAT_NOTIFY_URL:-}" || $_ntfy_on -eq 1 ]]; then
  section "Notifications"
  if [[ -z "${CHAT_NOTIFY_URL:-}" ]]; then
    warn "the ntfy extension is enabled but CHAT_NOTIFY_URL is empty — beast-chat sends no notifications" \
         "set CHAT_NOTIFY_URL=http://127.0.0.1:${NTFY_PORT:-3005}/<long-random-topic> in openbeast.conf"
  elif [[ ! "$CHAT_NOTIFY_URL" =~ ^(https?://[^/[:space:]]+)(/[^[:space:]]*)?$ ]]; then
    fail "CHAT_NOTIFY_URL is not an http(s) URL" \
         "CHAT_NOTIFY_URL=http://127.0.0.1:${NTFY_PORT:-3005}/<long-random-topic>"
  else
    _n_origin="${BASH_REMATCH[1]}"
    pass "notifications configured → $_n_origin (on: ${CHAT_NOTIFY_ON:-failed,lost,done})"
    if [[ "${BEAST_CHAT:-false}" != "true" ]]; then
      warn "CHAT_NOTIFY_URL is set but BEAST_CHAT is off — beast-chat is what sends notifications" \
           "set BEAST_CHAT=true in openbeast.conf, then ./stop.sh && ./start.sh -d"
    fi
    # Reachable = any HTTP answer. /v1/health is ntfy's; another endpoint
    # answers it with a 404, which still proves the host is up. GET only, no
    # token: this must never itself send a notification.
    _n_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "$_n_origin/v1/health" 2>/dev/null || true)"
    if [[ "$_n_code" =~ ^[1-5][0-9][0-9]$ ]]; then
      pass "notification endpoint reachable ($_n_origin, HTTP $_n_code)"
    elif [[ $_ntfy_on -eq 1 ]]; then
      warn "notification endpoint not reachable ($_n_origin) — notifications are being dropped" \
           "the ntfy extension starts with the stack: ./stop.sh && ./start.sh -d (docker logs ntfy)"
    else
      warn "notification endpoint not reachable ($_n_origin) — notifications are being dropped" \
           "check CHAT_NOTIFY_URL, or enable the bundled server: ./scripts/ext.sh enable ntfy"
    fi
  fi
  if [[ -n "${CHAT_NOTIFY_TOKEN_FILE:-}" ]]; then
    if [[ ! -f "$CHAT_NOTIFY_TOKEN_FILE" ]]; then
      warn "CHAT_NOTIFY_TOKEN_FILE does not exist ($CHAT_NOTIFY_TOKEN_FILE) — notifications go out unauthenticated" \
           "create it (0600) with the endpoint's token, or remove the key"
    else
      _n_mode="$(stat -c %a "$CHAT_NOTIFY_TOKEN_FILE" 2>/dev/null || stat -f %Lp "$CHAT_NOTIFY_TOKEN_FILE" 2>/dev/null || echo "")"
      if [[ "$_n_mode" =~ ^[0-7]+$ ]] && (( 8#$_n_mode & 8#077 )); then
        warn "CHAT_NOTIFY_TOKEN_FILE is readable by other users (mode $_n_mode)" "chmod 600 $CHAT_NOTIFY_TOKEN_FILE"
      else
        pass "notification token file is private (mode ${_n_mode:-?})"
      fi
    fi
  fi
  if ob_offline && [[ -n "${OPENBEAST_NTFY_UPSTREAM_BASE_URL:-}" ]]; then
    warn "OFFLINE=true but NTFY_UPSTREAM_BASE_URL is set — every notification sends a poll request off the box" \
         "unset NTFY_UPSTREAM_BASE_URL (iOS then shows messages only when the app is open)"
  fi
fi

# ── beast-hydra (HYDRA=true only) ───────────────────────────────────────────
# docs/BEAST_HYDRA_PLAN.md §6.7. Rows: config, process, gate → hydra, and
# per node / deployment / route from /hydra/status (admin: the per-start
# local token, through ob_curl_hdr — never argv). The formatters print
# `pass|msg|fix` rows; _doctor_rows dispatches them.
_doctor_rows() {
  local _st _msg _fix
  while IFS='|' read -r _st _msg _fix; do
    case "$_st" in
      pass) pass "$_msg" ;;
      warn) warn "$_msg" "$_fix" ;;
      fail) fail "$_msg" "$_fix" ;;
    esac
  done
}
if [[ "${HYDRA:-false}" == "true" ]]; then
  section "beast-hydra"
  read -r -d '' _HY_CHECK_FMT <<'PY' || true
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("fail|hydra config: agents/hydra.py --check gave no verdict|scripts/hydra.sh check")
    sys.exit()
if not d.get("ok"):
    for e in d.get("errors") or ["invalid"]:
        print("fail|hydra config: %s|fix hydra.toml (scripts/hydra.sh check)" % e)
else:
    for w in d.get("warnings") or []:
        print("warn|hydra config: %s|" % w)
    print("pass|hydra config ok (%s: %s node(s), %s route(s))|"
          % (d.get("source"), d.get("nodes"), d.get("routes")))
PY
  read -r -d '' _HY_STATUS_FMT <<'PY' || true
import json, sys
try:
    s = json.load(sys.stdin)
    from hydra_core import host_class
except Exception:
    print("warn|hydra status unreadable (/hydra/status)|scripts/hydra.sh status")
    sys.exit()
if not isinstance(s, dict) or "deployments" not in s:
    why = ((s.get("error") or {}).get("message") if isinstance(s, dict) else None) or "no status"
    print("warn|hydra status unreadable (/hydra/status: %s) — a stale .run/hydra-local.token?|scripts/hydra.sh status" % why)
    sys.exit()
for nid, n in (s.get("nodes") or {}).items():
    host = n.get("host") or "?"
    cls = host_class("http://" + (("[%s]" % host) if ":" in host else host))[1]
    if cls == "public":
        print("warn|node %s: public address %s (allow_public)|keep nodes on loopback, RFC1918 or the tailnet" % (nid, host))
    else:
        print("pass|node %s: %s address, key %s|" % (nid, cls, n.get("key")))
    if cls != "loopback" and n.get("key") == "none" and n.get("engine") != "tensorfold":
        print("warn|node %s: remote node with no key|set key_file (0600) in hydra.toml" % nid)
for did, d in (s.get("deployments") or {}).items():
    st = d.get("state")
    if st == "READY":
        print("pass|deployment %s: READY (%s/%s, breaker %s)|" % (did, d.get("inflight"), d.get("slots"), d.get("breaker")))
    elif st == "LOADING":
        print("warn|deployment %s: LOADING|" % did)
    elif st == "MISMATCH":
        print("fail|deployment %s: served id %r is not in the node's /v1/models|fix upstream in hydra.toml" % (did, d.get("upstream")))
    elif st == "AUTH_FAILED":
        print("fail|deployment %s: the node refused hydra's key (AUTH_FAILED)|check the node's key_file / key_env" % did)
    else:
        print("fail|deployment %s: %s %s|scripts/hydra.sh status" % (did, st, d.get("detail") or ""))
    c = d.get("conformance")
    if c in ("fail", "missing") and not d.get("routable"):
        print("fail|deployment %s: conformance %s (required)|scripts/hydra.sh conformance %s" % (did, c, did))
    elif c in ("fail", "missing", "stale"):
        print("warn|deployment %s: conformance %s|scripts/hydra.sh conformance %s" % (did, c, did))
for rid, r in (s.get("routes") or {}).items():
    groups = r.get("candidates_per_group") or []
    if not r.get("routable"):
        print("fail|route %s: nothing routable|scripts/hydra.sh status" % rid)
    elif len(groups) > 1 and not any(groups[:-1]):
        print("warn|route %s: only its last-priority group is routable|" % rid)
    else:
        print("pass|route %s: routable|" % rid)
PY
  _hy_args=(--check)
  [[ -f "$HYDRA_CONFIG" ]] && _hy_args+=("$HYDRA_CONFIG")
  _hy_check="$(python3 "$REPO_DIR/agents/hydra.py" "${_hy_args[@]}" --json 2>/dev/null || true)"
  _doctor_rows < <(printf '%s' "$_hy_check" | python3 -c "$_HY_CHECK_FMT" 2>/dev/null)
  _hy_url="${HYDRA_URL:-http://127.0.0.1:${HYDRA_PORT:-8095}}"
  _hy_code="$(curl -s -o /dev/null -m 4 -w '%{http_code}' "$_hy_url/health" 2>/dev/null || true)"
  case "$_hy_code" in
    200) pass "beast-hydra ($_hy_url) — default route routable" ;;
    [1-5][0-9][0-9]) warn "beast-hydra is up but has NO routable default route (HTTP $_hy_code)" \
                          "scripts/hydra.sh status names the down/loading nodes" ;;
    *)   fail "beast-hydra is not answering on $_hy_url — every consumer's inference goes through it" \
              "./scripts/healthcheck.sh --restart (or HYDRA=false to bypass it)" ;;
  esac
  if [[ "${EDGE_GATE:-false}" == "true" ]]; then
    _gate_tok=$(cat "$REPO_DIR/.run/edge-local.token" 2>/dev/null || true)
    _gate_up="$(ob_curl_hdr "${_gate_tok:+X-OpenBeast-Local: $_gate_tok}" -s --max-time 4 \
                  "http://$HEALTH_HOST:${EDGE_PORT:-8090}/gate/health" 2>/dev/null \
                | grep -o '"upstream":"[^"]*"' | cut -d'"' -f4 || true)"
    if [[ -z "$_gate_up" ]]; then
      warn "gate → hydra: beast-gate did not report its upstream" "./scripts/healthcheck.sh --restart"
    elif [[ "${_gate_up%/}" == "${_hy_url%/}" ]]; then
      pass "gate → hydra ($_gate_up)"
    else
      fail "beast-gate's upstream is $_gate_up, not hydra ($_hy_url) — remote clients bypass it" \
           "restart the gate so it picks up OPENBEAST_CONSUMER_BASE: ./stop.sh && ./start.sh -d"
    fi
  fi
  _hy_tok="$(cat "$REPO_DIR/.run/hydra-local.token" 2>/dev/null || true)"
  if [[ -n "$_hy_tok" && "$_hy_code" =~ ^[1-5][0-9][0-9]$ ]]; then
    _doctor_rows < <(ob_curl_hdr "X-OpenBeast-Local: $_hy_tok" -s -m 4 "$_hy_url/hydra/status" 2>/dev/null \
                     | PYTHONPATH="$REPO_DIR/agents" python3 -c "$_HY_STATUS_FMT" 2>/dev/null)
  fi
fi

# ── beast-instinct (INSTINCT=true only) ─────────────────────────────────────
# docs/BEAST_INSTINCT_PLAN.md §5.9: health, key-file mode, the scorer engine,
# and any decision whose TARGET is enforce while its effective mode is lower.
if [[ "${INSTINCT:-false}" == "true" ]]; then
  section "beast-instinct"
  read -r -d '' _IN_FMT <<'PY' || true
import json, os, sys
docs = [ln for ln in sys.stdin.read().split("\n") if ln.strip()]
try:
    engines, decisions = json.loads(docs[0]), json.loads(docs[1])
except Exception:
    print("warn|instinct engines/decisions unreadable (key?)|scripts/instinct.sh status")
    sys.exit()
if os.environ.get("INSTINCT_SCORER") == "true":
    port = ":" + os.environ.get("SCORER_PORT", "8082")
    hits = [e for e in engines.get("engines", []) if port in ((e.get("pins") or {}).get("url") or "")]
    if not hits:
        print("warn|no instinct engine binding points at the scorer (%s)|add one to the instinct config" % port)
    for e in hits:
        if e.get("healthy"):
            print("pass|instinct scorer engine %s healthy|" % e["id"])
        else:
            r = (e.get("probe") or {}).get("reason") or "not probed yet"
            print("warn|instinct scorer engine %s unhealthy: %s|scripts/instinct.sh probe" % (e["id"], r))
for name, err in (engines.get("invalid") or {}).items():
    print("warn|instinct engine %s refused: %s|" % (name, err))
for d in decisions.get("decisions", []):
    if d.get("target_mode") != "enforce":
        continue
    if any(e.get("effective_mode") == "enforce" for e in d.get("engines", [])):
        print("pass|decision %s: enforcing|" % d["id"])
    else:
        why = "; ".join("%s: %s" % (e["engine"], e.get("reason")) for e in d.get("engines", [])) or "no engines"
        print("warn|decision %s: target enforce, effective lower (%s)|docs/BEAST_INSTINCT.md (lifecycle)" % (d["id"], why))
PY
  _in_url="http://127.0.0.1:${INSTINCT_PORT:-8094}"
  _in_up=0
  if probe "$_in_url/health" "ok"; then
    _in_up=1
    pass "beast-instinct ($_in_url)"
  else
    warn "beast-instinct not responding ($_in_url) — consumers fail open to today's behaviour" \
         "scripts/instinct.sh up (or ./scripts/healthcheck.sh --restart)"
  fi
  _in_kf="$(PYTHONPATH="$REPO_DIR/agents" INSTINCT_CONFIG="${INSTINCT_CONFIG:-}" \
            python3 -m instinct.cli cfg 2>/dev/null | sed -n 's/^INSTINCT_KEY_FILE=//p' || true)"
  _in_kf="${_in_kf:-$REPO_DIR/.run/instinct.key}"
  if [[ ! -f "$_in_kf" ]]; then
    warn "instinct key $_in_kf does not exist yet" "scripts/instinct.sh up mints it (0600)"
  elif [[ "$(stat -c '%a' "$_in_kf" 2>/dev/null)" != "600" ]]; then
    fail "instinct key $_in_kf is mode $(stat -c '%a' "$_in_kf" 2>/dev/null) — the service refuses it" "chmod 600 $_in_kf"
  else
    pass "instinct key file is 0600"
  fi
  if [[ -f "$_in_kf" && $_in_up -eq 1 ]]; then
    _in_key="$(cat "$_in_kf" 2>/dev/null || true)"
    _doctor_rows < <( { ob_curl_bearer "$_in_key" -s -m 4 "$_in_url/v1/instinct/engines" 2>/dev/null; echo
                        ob_curl_bearer "$_in_key" -s -m 4 "$_in_url/v1/instinct/decisions" 2>/dev/null; echo; } \
                      | INSTINCT_SCORER="${INSTINCT_SCORER:-false}" \
                        SCORER_PORT="${INSTINCT_SCORER_PORT:-8082}" python3 -c "$_IN_FMT" 2>/dev/null)
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
      if curl -s --max-time 4 "http://$CHAT_HEALTH_HOST:${CHAT_PORT:-3003}/api/chat/health" 2>/dev/null | grep -qi '"status":"ok"'; then
        pass "beast-chat published on :8445 (tailnet-only)"
      else
        # Still a failure on a stopped stack — the mount is live and 502s —
        # but then the fix is to start it, not a per-service restart.
        _pfix="the console 502s from the phone — set BEAST_CHAT=true and restart, or ./scripts/setup-tailscale.sh --unpublish-chat"
        [[ $STACK_DOWN -eq 1 && "${BEAST_CHAT:-false}" == "true" ]] \
          && _pfix="./start.sh -d (the stack is not running; the console 502s from the phone until then), or ./scripts/setup-tailscale.sh --unpublish-chat"
        fail ":8445 is published but beast-chat is NOT responding" "$_pfix"
      fi
    elif [[ "${BEAST_CHAT:-false}" == "true" ]]; then
      warn "BEAST_CHAT=true but :8445 is not published — the console is loopback-only" \
           "./scripts/setup-tailscale.sh --publish-chat"
    fi
    # :8446 is beast-artifact. Every URL the model hands out is built from
    # this mount (agents/artifact.py reads `tailscale serve status`), so a
    # mount over a dead server means every link on the phone is a 502.
    if echo "$_serve" | grep -qE ':8446[^0-9]'; then
      if probe "http://$HEALTH_HOST:${ARTIFACT_PORT:-3004}/api/artifacts/health" '"status"'; then
        pass "beast-artifact published on :8446 (tailnet-only)"
      else
        _pfix="every artifact link 502s from the phone — set BEAST_ARTIFACT=true and ./scripts/healthcheck.sh --restart, or ./scripts/setup-tailscale.sh --unpublish-artifact"
        [[ $STACK_DOWN -eq 1 && "${BEAST_ARTIFACT:-false}" == "true" ]] \
          && _pfix="./start.sh -d (the stack is not running; every artifact link 502s from the phone until then), or ./scripts/setup-tailscale.sh --unpublish-artifact"
        fail ":8446 is published but beast-artifact is NOT responding" "$_pfix"
      fi
    elif [[ "${BEAST_ARTIFACT:-false}" == "true" ]]; then
      warn "BEAST_ARTIFACT=true but :8446 is not published — artifact links open only on this box" \
           "./scripts/setup-tailscale.sh --publish-artifact"
    fi
    # :8447 is the ntfy extension (the phone app's subscription).
    if echo "$_serve" | grep -qE ':8447[^0-9]'; then
      if probe "http://127.0.0.1:${NTFY_PORT:-3005}/v1/health" 'healthy'; then
        pass "ntfy published on :8447 (tailnet-only)"
      else
        fail ":8447 is published but ntfy is NOT responding" \
             "the phone app cannot subscribe — ./scripts/ext.sh enable ntfy && ./stop.sh && ./start.sh -d, or ./scripts/setup-tailscale.sh --unpublish-ntfy"
      fi
    elif [[ " ${EXTENSIONS:-} " == *" ntfy "* && -n "${CHAT_NOTIFY_URL:-}" ]]; then
      warn "the ntfy extension is on but :8447 is not published — the phone app cannot subscribe" \
           "./scripts/setup-tailscale.sh --publish-ntfy"
    fi
    # :443 is the WebUI. Published with login OFF, every tailnet device is the
    # default admin — with bash through the privileged tool connection
    # (network-exposure-3). The default :443 entry prints with NO port token
    # (https://<fqdn> …), an explicit one as :443. Judge both the conf and the
    # RUNNING container: a conf flipped to true is not in force until WebUI
    # restarts.
    if echo "$_serve" | grep -qE '^https://[^:/[:space:]]+(:443)?([[:space:]/]|$)'; then
      _live_auth="$(_webui_live_auth)"
      if [[ "${WEBUI_AUTH:-false}" != "true" && "${ALLOW_OPEN_WEBUI:-false}" == "true" ]]; then
        # Published open on purpose (setup-tailscale.sh --i-accept-open-webui
        # records ALLOW_OPEN_WEBUI=true). Say it every run; do not fail it.
        warn "the WebUI is published on :443 with login OFF (ALLOW_OPEN_WEBUI=true acknowledges it) — every tailnet device is admin" \
             "set WEBUI_AUTH=true and remove ALLOW_OPEN_WEBUI from openbeast.conf, then ./stop.sh && ./start.sh -d"
      elif [[ "${WEBUI_AUTH:-false}" != "true" ]]; then
        fail "the WebUI is published on :443 but WEBUI_AUTH is off — every tailnet device is admin (and has bash)" \
             "set WEBUI_AUTH=true in openbeast.conf and ./stop.sh && ./start.sh -d, or: sudo tailscale serve --https=443 off (if the open WebUI is intended: ./scripts/setup-tailscale.sh --i-accept-open-webui records ALLOW_OPEN_WEBUI=true)"
      elif [[ "$_live_auth" == "false" ]]; then
        fail "the WebUI is published on :443 and the RUNNING WebUI still has login off" \
             "WEBUI_AUTH=true is set but not live yet — ./stop.sh && ./start.sh -d"
      elif [[ "$_live_auth" == "true" ]]; then
        pass "WebUI published on :443 with login enforced (live)"
      else
        # WebUI down or unreadable: the conf says login is on, but nothing
        # confirms the running container agrees — a ✓ here would be a guess.
        warn "the WebUI is published on :443 but is not answering, so live login enforcement cannot be confirmed" \
             "WEBUI_AUTH=true is set; once Open WebUI is up (docker compose up -d), rerun doctor"
      fi
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
          _pfix="remote clients are getting 502 — ./scripts/healthcheck.sh --restart"
          [[ $STACK_DOWN -eq 1 ]] && _pfix="./start.sh -d (the stack is not running; remote clients get 502 until then)"
          fail ":8443 points at beast-gate but the gate is NOT responding" "$_pfix"
        fi
      elif [[ "${HYDRA:-false}" == "true" ]]; then
        # hydra holds node keys: raw publication would hand the tailnet an
        # inference port with no per-device identity in front of the fleet.
        fail ":8443 publishes a raw inference port while HYDRA=true" \
             "set EDGE_GATE=true and re-run ./scripts/setup-tailscale.sh (it refuses raw publication under hydra)"
      elif [[ "${EDGE_GATE:-false}" == "true" ]]; then
        warn "EDGE_GATE=true but :8443 still points at raw llama-server" \
             "re-run ./scripts/setup-tailscale.sh to repoint it at the gate"
      elif [[ -z "${LLAMA_API_KEY:-}" && "${ALLOW_OPEN_INFERENCE:-false}" == "true" ]]; then
        # Published keyless on purpose (setup-tailscale.sh
        # --i-accept-open-inference records ALLOW_OPEN_INFERENCE=true). Say it
        # every run; do not fail it — the :443 rule above, for inference.
        warn "raw llama-server is published on :8443 with no API key (ALLOW_OPEN_INFERENCE=true acknowledges it) — every tailnet device can use the GPU" \
             "fine on a personal tailnet you fully own; otherwise set EDGE_GATE=true (per-device keys, path allowlist, audit) and remove ALLOW_OPEN_INFERENCE from openbeast.conf — docs/BEAST_SLOT.md"
      elif [[ -z "${LLAMA_API_KEY:-}" ]]; then
        # setup-tailscale.sh no longer makes this mount without a yes, so it
        # is one an older run left: no gate, no key, nobody said "on purpose".
        fail "raw llama-server is published on :8443 with no API key and no beast-gate — every tailnet device (shared-in users included) can use the GPU and reach /slots, /props and /lora-adapters" \
             "set EDGE_GATE=true in openbeast.conf, ./stop.sh && ./start.sh -d, then ./scripts/setup-tailscale.sh (or set LLAMA_API_KEY; or take it down: sudo tailscale serve --https=8443 off; if it is intended: ./scripts/setup-tailscale.sh --i-accept-open-inference)"
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

# ── Log rotation ────────────────────────────────────────────────────────────
# stack.log and the audit trails grow without bound unless the daily
# openbeast-logrotate timer runs (storage-04). start.sh installs it; this row
# catches a rig where it is missing or was disabled. Skipped where there is
# no systemd user manager to ask (macOS, containers, CI).
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  section "Housekeeping"
  if systemctl --user is-enabled --quiet openbeast-logrotate.timer 2>/dev/null; then
    pass "log rotation timer enabled (openbeast-logrotate.timer)"
  else
    warn "log rotation timer is missing or disabled — stack.log and the audit logs grow without bound" \
         "./scripts/logrotate.sh --install"
  fi
fi

# ── Closed network ──────────────────────────────────────────────────────────
# Reported unconditionally, because the mode changes what several other rows
# MEAN. A cert-expiry warning on a box that can never reach the coordination
# server is not actionable the way it is on a connected one, and a reader who
# does not know OFFLINE is set will chase it.
if ob_offline; then
  pass "OFFLINE=true — install/update steps that need the internet are refused up front"
  # The one thing worth checking in this mode: can this box actually REBUILD
  # what it is running? That is the difference between "serves offline" (which
  # every installed rig does) and "can be maintained offline".
  _off_missing=()
  [[ -f "$REPO_DIR/agents/requirements.lock" ]] || _off_missing+=("agents/requirements.lock")
  # A wheelhouse that EXISTS is not a wheelhouse that WORKS: an empty
  # directory (or one built for another platform) made this row print a green
  # "all present" while a rebuild would fail. Ask pydeps whether it actually
  # covers the locked closure — the same check `install --from` gates on.
  _off_wh=""
  for _d in "$REPO_DIR/wheels" "$REPO_DIR/wheelhouse" "${OPENBEAST_WHEELHOUSE:-}"; do
    [[ -n "$_d" && -d "$_d" ]] || continue
    if (cd "$REPO_DIR" && ./scripts/pydeps.sh audit "$_d" >/dev/null 2>&1); then
      _off_wh="$_d"; break
    fi
    _off_missing+=("a COMPLETE wheelhouse ($_d exists but does not cover the lock)")
    break
  done
  [[ -n "$_off_wh" || ${#_off_missing[@]} -gt 0 ]] \
    || _off_missing+=("a wheelhouse (./wheels)")
  # llama.cpp needs SOURCE, not a clone — bundle.sh install lays down a
  # tarball with no .git, and that builds fine.
  if [[ ! -f "$REPO_DIR/llama.cpp/CMakeLists.txt" ]]; then
    _off_missing+=("llama.cpp source (llama.cpp/CMakeLists.txt)")
  fi
  if [[ ${#_off_missing[@]} -eq 0 ]]; then
    pass "offline self-sufficiency: source, lock and a wheelhouse that covers the lock ($_off_wh)"
  else
    warn "offline, but a REBUILD would need: ${_off_missing[*]}" \
         "serving is unaffected; stage them on a connected box (./scripts/bundle.sh build ./bundle)"
  fi
  # Compose-kind extensions (ntfy, …) are merged into the SAME `docker compose
  # up --pull never` as WebUI and SearXNG, so one missing extension image
  # aborts the whole frontend. bundle.sh carries every fragment's image and
  # install points the fragment at the loaded ID; a box installed without a
  # bundle has to move it by hand (extensions/<name>/README.md). Checked by
  # the line AS WRITTEN, because that is what compose resolves: a
  # repo:tag@sha256 pin passes only if the pinned reference itself inspects.
  # `docker save`/`load` drops the registry digest, so a hand-loaded repo:tag
  # next to a line that still pins a digest is exactly the broken case this
  # row exists for (it used to pass on the tag). A line with no digest (the
  # sha256:<id> the README and bundle.sh rewrite to) is checked as is, then
  # by its tag.
  if command -v docker >/dev/null 2>&1; then
    for _ext in ${EXTENSIONS:-}; do
      _ext_compose="$REPO_DIR/extensions/$_ext/compose.yaml"
      [[ -f "$_ext_compose" ]] || continue
      while IFS= read -r _img; do
        [[ -n "$_img" ]] || continue
        if docker image inspect "$_img" >/dev/null 2>&1 \
           || { [[ "$_img" != *@sha256:* ]] && docker image inspect "${_img%%@*}" >/dev/null 2>&1; }; then
          pass "offline: the $_ext extension's image is present (${_img%%@*})"
        elif docker image inspect "${_img%%@*}" >/dev/null 2>&1; then
          fail "offline: the $_ext extension's image ${_img%%@*} is here, but compose.yaml pins a digest it no longer carries (docker save/load drops it) — compose up --pull never fails, taking WebUI and SearXNG down with it" \
               "point extensions/$_ext/compose.yaml at the loaded image ID (image: sha256:<id>, extensions/$_ext/README.md), or install via ./scripts/bundle.sh"
        else
          fail "offline: the $_ext extension's image ${_img%%@*} is not on this box — compose up --pull never fails, taking WebUI and SearXNG down with it" \
               "carry it in a bundle (./scripts/bundle.sh build on a connected box, then install here), or by hand: extensions/$_ext/README.md — or ./scripts/ext.sh disable $_ext"
        fi
      done < <(grep -oE '^[[:space:]]*image:[[:space:]]*[^[:space:]]+' "$_ext_compose" \
                 | sed -E 's/^[[:space:]]*image:[[:space:]]*//' | sort -u)
    done
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

# ── zig awareness pack in production (agents/lang/pack_context.py) ─────────
# Agents on zig tasks get the Tier-3-measured pack as --context-file. "off" is
# a choice (LANG_PACK_CONTEXT=off); NOT SERVED means the switch is on but the
# pack is stale, edited since it was measured, or missing — worth a warning.
if [[ -f "$REPO_DIR/agents/lang/pack_context.py" ]]; then
  _pc_line="$(cd "$REPO_DIR" && timeout 30 python3 agents/lang/pack_context.py status 2>/dev/null | head -1 || true)"
  case "$_pc_line" in
    "zig pack: auto"*|"zig pack: off"*) pass "$_pc_line" ;;
    "zig pack: NOT SERVED"*)
      warn "$_pc_line" "LANG_PACK_CONTEXT in openbeast.conf; docs/LANG_AWARENESS_PLAN.md" ;;
    *) warn "zig pack: status unavailable (see: python3 agents/lang/pack_context.py status)" ;;
  esac
fi

# ── Verdict ─────────────────────────────────────────────────────────────────
[[ $QUIET -eq 1 ]] || echo ""
echo "doctor: ${PASS} ok, ${WARN} warning(s), ${FAIL} failure(s)"
# One next step: the first failure's fix that is a command; else, on a
# stopped stack, starting it (most other rows cannot be judged until it
# runs); else the first warning's; else a failure's explanation. Nothing to
# do → no line.
if [[ -n "$NEXT_FAIL" ]]; then
  echo "Next: $NEXT_FAIL"
elif [[ $STACK_DOWN -eq 1 ]]; then
  echo "Next: ./start.sh -d"
elif [[ -n "$NEXT_WARN" ]]; then
  echo "Next: $NEXT_WARN"
elif [[ -n "$NEXT_FAIL_ANY" ]]; then
  echo "Next: $NEXT_FAIL_ANY"
fi
[[ $FAIL -eq 0 ]]
