#!/bin/bash
# Configure Open WebUI for the OpenBeast stack.
# Idempotent — safe to run multiple times.
#
# Sets up:
#   1. MCPO tool server connection (OpenAPI on localhost:3001)
#   2. Native function calling for all detected models
#   3. System prompt from system-prompt.md
#   4. The random seed filter (agents/webui_functions/random_seed.py), so
#      Regenerate differs on servers with a fixed default seed (TensorFold)
#   5. With INFERENCE_THINKING set, the thinking default filter
#      (agents/webui_functions/thinking_default.py), so chats that choose no
#      thinking mode get the serving host's default
#
# Called automatically by start.sh after Open WebUI is ready.
#
#   ./scripts/configure-webui.sh                          full configuration
#   ./scripts/configure-webui.sh --secure-default-admin   ONLY retire the
#       upstream default admin password (see _secure_default_admin below);
#       setup-tailscale.sh runs this the moment the WebUI goes tailnet-wide.
#   ./scripts/configure-webui.sh --check-default-admin    read-only probe
#       (doctor.sh): exit 1 when login is ON and admin@localhost still signs
#       in with upstream's default password, 0 when login is ON and it does
#       not, 4 when the running WebUI has login off (or /api/config does not
#       say) so the probe did not run, 3 when WebUI is unreachable, 5 when
#       the sign-in did not answer yes or no (429 rate limit, 5xx, timeout).

set -euo pipefail

MODE=full
case "${1:-}" in
  "") ;;
  --secure-default-admin) MODE=secure ;;
  --check-default-admin)  MODE=check ;;
  -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# --check-default-admin is a read-only probe: it must not be the thing that
# creates openbeast.conf (conf.sh mints the SearXNG secret unless told not to).
[[ "$MODE" == "check" ]] && export OB_CONF_READONLY=1
source "$SCRIPT_DIR/lib/conf.sh"   # WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD
# Every credential header below (the admin JWT, LLAMA_API_KEY) goes through
# ob_curl_hdr / ob_curl_bearer — curl --config on fd 3, never argv, because
# /proc/*/cmdline is world-readable and the admin JWT is bash-equivalent.
source "$SCRIPT_DIR/lib/curl_auth.sh"
CONF="$REPO_DIR/openbeast.conf"

# Open WebUI, the tool server and SearXNG all bind BIND_HOST, and WebUI runs
# with network_mode: host — so both this script's own calls AND the URLs it
# stores in WebUI's config must dial the probe host (lib/net.sh via conf.sh),
# not a hard-coded localhost that a socket bound to a specific LAN/tailnet
# address refuses. Loopback/wildcard binds keep the `localhost` spelling.
_OB_LOCAL_HOST="${OPENBEAST_PROBE_HOST:-127.0.0.1}"
[[ "$_OB_LOCAL_HOST" == "127.0.0.1" ]] && _OB_LOCAL_HOST=localhost
WEBUI_URL="${WEBUI_URL:-http://$_OB_LOCAL_HOST:3000}"
MCPO_URL="${MCPO_URL:-http://$_OB_LOCAL_HOST:3001}"

# Open WebUI's built-in default admin (open_webui/routers/auths.py, the
# WEBUI_AUTH == False branch of signin): with auth OFF, signing in as
# admin@localhost creates that account as ADMIN with the hardcoded password
# "admin" — and this script does exactly that on every auth-off start. When
# auth is later turned ON (setup-tailscale.sh), the account survives with the
# same well-known password, so any tailnet device could sign in as admin and
# reach bash through the privileged tool connection.
DEFAULT_ADMIN_EMAIL="admin@localhost"
DEFAULT_ADMIN_PASSWORD="admin"

if [[ "$MODE" == "full" ]]; then
# Refresh the generated skill menu so the prompt always matches skills/
# (non-fatal: a broken generator must not block WebUI configuration).
python3 "$SCRIPT_DIR/generate-skill-index.py" >/dev/null 2>&1 \
  || echo "Warning: skill index regeneration failed — prompt may list stale skills" >&2
fi

# Load system prompt: soul file + tool guidance (Open WebUI needs both)
SYSTEM_PROMPT=""
if [[ -f "$REPO_DIR/system-prompt.md" ]]; then
  SYSTEM_PROMPT=$(cat "$REPO_DIR/system-prompt.md")
fi
if [[ -f "$REPO_DIR/system-prompt-tools.md" ]]; then
  SYSTEM_PROMPT="$SYSTEM_PROMPT"$'\n\n'"$(cat "$REPO_DIR/system-prompt-tools.md")"
fi

[[ "$MODE" == "full" ]] && echo "Configuring Open WebUI..."

# Wait for Open WebUI to be ready — bounded so a container that never comes
# up can't leave this loop orphaned forever (start.sh backgrounds us).
# --secure-default-admin is called against a WebUI that is already up, so
# it gets a short bound instead of three minutes.
_WAIT_S=180
[[ "$MODE" == "secure" ]] && _WAIT_S=15
[[ "$MODE" == "check" ]] && _WAIT_S=2
for _i in $(seq 1 "$_WAIT_S"); do
  curl -s "$WEBUI_URL/api/version" > /dev/null 2>&1 && break
  if [[ $_i -eq $_WAIT_S ]]; then
    [[ "$MODE" == "check" ]] && exit 3
    echo "Error: Open WebUI not reachable after ${_WAIT_S}s — giving up." >&2
    echo "       Re-run ./scripts/configure-webui.sh once it's up." >&2
    exit 1
  fi
  sleep 1
done

# Get admin token. Two paths:
#   • WEBUI_AUTH=false (the default): signing in as admin@localhost works
#     with any password — upstream substitutes its built-in default and
#     creates that account as admin on an empty DB.
#   • WEBUI_AUTH=true (set by setup-tailscale.sh): WEBUI_ADMIN_EMAIL /
#     WEBUI_ADMIN_PASSWORD in openbeast.conf name an admin account. If the
#     built-in admin@localhost still has the default password, it is rotated
#     first and its new password becomes those two keys (see below).
# Prints the token and returns 0 when the sign-in worked; returns 1 when the
# WebUI REFUSED those credentials (HTTP 400/401/403 — upstream answers a wrong
# password with 400); returns 2 when it said neither (429 rate limit — 15
# sign-ins per 3 min per email — a 5xx while booting, a reset, a timeout).
# Callers asking "does the default password still work?" must treat 2 as
# "unknown", never as "no": reading every token-less answer as "no" let
# setup-tailscale publish :443 and doctor report the default as retired while
# admin/admin still worked. Call it as `tok=$(_signin …) || rc=$?` under set -e.
_signin() {
  # The password travels via env + stdin, never argv: an argv JSON blob is
  # world-readable in /proc/*/cmdline while curl runs, and printf-splicing
  # broke on passwords containing '"' or '\'. /proc/<pid>/environ is
  # owner-only, and python json.dumps escapes correctly.
  local out code tok
  out=$(SIGNIN_EMAIL="$1" SIGNIN_PASSWORD="$2" python3 -c \
    'import json,os; print(json.dumps({"email": os.environ["SIGNIN_EMAIL"], "password": os.environ["SIGNIN_PASSWORD"]}))' \
    | curl -s -m 10 -w '\n%{http_code}' "$WEBUI_URL/api/v1/auths/signin" \
        -H "Content-Type: application/json" -d @- 2>/dev/null) || true
  code=${out##*$'\n'}
  # A non-JSON body (502 HTML, reset) must not kill the script under pipefail.
  tok=$(printf '%s' "${out%$'\n'*}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null) || tok=""
  if [[ -n "$tok" ]]; then
    printf '%s\n' "$tok"
    return 0
  fi
  case "$code" in
    400|401|403) return 1 ;;
    *)           return 2 ;;
  esac
}

# Is the RUNNING WebUI enforcing logins? /api/config is public (the login
# page needs it) and carries features.auth. Prints true / false / unknown.
_live_auth() {
  curl -s -m 5 "$WEBUI_URL/api/config" 2>/dev/null \
    | python3 -c "
import sys, json
try:
    a = json.load(sys.stdin).get('features', {}).get('auth')
except Exception:
    a = None
print('unknown' if a is None else ('true' if a else 'false'))" 2>/dev/null \
    || echo unknown
}

# Set KEY=VALUE in openbeast.conf (replacing every existing assignment),
# keeping the file 0600 — it is where the stack's secrets live. The value
# travels via env, never argv.
_conf_set() {
  ( umask 077
    CONF_PATH="$CONF" CONF_KEY="$1" CONF_VAL="$2" python3 -c '
import os, re, tempfile
path, key, val = os.environ["CONF_PATH"], os.environ["CONF_KEY"], os.environ["CONF_VAL"]
try:
    lines = open(path).read().splitlines()
except FileNotFoundError:
    lines = []
pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
out, done = [], False
for ln in lines:
    if pat.match(ln):
        if not done:
            out.append(f"{key}={val}")
            done = True
        continue
    out.append(ln)
if not done:
    out.append(f"{key}={val}")
d = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".openbeast.conf.")
with os.fdopen(fd, "w") as f:
    f.write("\n".join(out) + "\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
' )
}

# Retire the upstream default admin password (review network-exposure-1).
# Only while the LIVE WebUI enforces auth: with auth off, upstream's signin
# checks the stored hash against its hardcoded default, so rotating then would
# break the running auth-off UI until the next restart. Returns 0 when the
# default no longer works (rotated now, or never there), 1 when it still does.
_default_unknown_warning() {
  echo "SECURITY WARNING: could not tell whether $DEFAULT_ADMIN_EMAIL still signs in with" >&2
  echo "  upstream's default password — the WebUI's sign-in answered neither yes nor no" >&2
  echo "  (rate-limited, erroring or not answering). Treating it as NOT retired." >&2
  echo "  Wait a few minutes (the sign-in limit is 15 per 3 min) and re-run." >&2
}

_secure_default_admin() {
  local tok newpw resp live _try rc=0
  # 'unknown' (timeout, non-JSON, no features.auth) is NOT 'auth off': it
  # used to return 0 here, so a slow /api/config on a busy first boot let the
  # well-known password survive the start meant to retire it, silently.
  live="$(_live_auth)"
  for _try in 1 2 3 4 5; do
    [[ "$live" != "unknown" ]] && break
    sleep 1
    live="$(_live_auth)"
  done
  if [[ "$live" == "unknown" ]]; then
    echo "SECURITY WARNING: could not read $WEBUI_URL/api/config, so whether WebUI" >&2
    echo "  login is on — and whether $DEFAULT_ADMIN_EMAIL still has upstream's default" >&2
    echo "  password — is UNVERIFIED. Re-run ./scripts/configure-webui.sh once WebUI" >&2
    echo "  answers, or check: ./scripts/configure-webui.sh --check-default-admin" >&2
    return 1
  fi
  [[ "$live" == "true" ]] || return 0
  tok=$(_signin "$DEFAULT_ADMIN_EMAIL" "$DEFAULT_ADMIN_PASSWORD") || rc=$?
  [[ $rc -eq 1 ]] && return 0      # default password refused: nothing to do
  if [[ $rc -ne 0 ]]; then
    _default_unknown_warning
    return 1
  fi
  newpw=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
  resp=$(OLD_PW="$DEFAULT_ADMIN_PASSWORD" NEW_PW="$newpw" python3 -c \
      'import json,os; print(json.dumps({"password": os.environ["OLD_PW"], "new_password": os.environ["NEW_PW"]}))' \
    | ob_curl_bearer "$tok" -s -m 10 "$WEBUI_URL/api/v1/auths/update/password" \
        -H "Content-Type: application/json" -d @- \
    2>/dev/null || true)
  if [[ "$resp" == "true" ]]; then
    # Record it where this script (and the operator) look for admin creds —
    # unless the conf already names a DIFFERENT admin account, whose
    # password must not be clobbered.
    if [[ -z "${WEBUI_ADMIN_EMAIL:-}" || "$WEBUI_ADMIN_EMAIL" == "$DEFAULT_ADMIN_EMAIL" ]]; then
      if _conf_set WEBUI_ADMIN_EMAIL "$DEFAULT_ADMIN_EMAIL" \
          && _conf_set WEBUI_ADMIN_PASSWORD "$newpw"; then
        WEBUI_ADMIN_EMAIL="$DEFAULT_ADMIN_EMAIL"; WEBUI_ADMIN_PASSWORD="$newpw"
        echo "SECURITY: the built-in WebUI admin ($DEFAULT_ADMIN_EMAIL) still had" >&2
        echo "  upstream's default password. Rotated it to a random one, saved as" >&2
        echo "  WEBUI_ADMIN_PASSWORD in openbeast.conf (0600)." >&2
        return 0
      fi
    elif _conf_set WEBUI_DEFAULT_ADMIN_PASSWORD "$newpw"; then
      echo "SECURITY: the built-in WebUI admin ($DEFAULT_ADMIN_EMAIL) still had" >&2
      echo "  upstream's default password. Rotated it to a random one, saved as" >&2
      echo "  WEBUI_DEFAULT_ADMIN_PASSWORD in openbeast.conf (0600). Your own" >&2
      echo "  admin ($WEBUI_ADMIN_EMAIL) is untouched; you can delete the" >&2
      echo "  built-in account in Admin Panel → Users." >&2
      return 0
    fi
    echo "SECURITY: rotated $DEFAULT_ADMIN_EMAIL's default password, but could not" >&2
    echo "  save the new one to $CONF — the account is now locked (safe)." >&2
    echo "  Reset it from Admin Panel → Users if you need it." >&2
    return 0
  fi
  # Rotation refused. Another run may have just rotated it (tokens are
  # revoked on a password change) — re-probe before crying wolf. Only a real
  # "wrong password" answer clears it.
  rc=0
  _signin "$DEFAULT_ADMIN_EMAIL" "$DEFAULT_ADMIN_PASSWORD" >/dev/null || rc=$?
  [[ $rc -eq 1 ]] && return 0
  echo "SECURITY WARNING: WebUI login is ON, but $DEFAULT_ADMIN_EMAIL still signs in" >&2
  echo "  as ADMIN with upstream's default password \"$DEFAULT_ADMIN_PASSWORD\", and rotating it" >&2
  echo "  failed. Anyone who can reach the WebUI can take admin (and bash)." >&2
  echo "  Fix now: sign in as $DEFAULT_ADMIN_EMAIL / $DEFAULT_ADMIN_PASSWORD → Settings → Account" >&2
  echo "  → change the password (or delete the account from Admin Panel → Users)." >&2
  return 1
}

if [[ "$MODE" == "check" ]]; then
  # Read-only: sign in with the default, change nothing. The password still
  # travels via env + stdin (_signin), never argv.
  # Login off / unknown: the question does not arise, and "0" would read as
  # "checked, and safe" — a claim nothing verified. Say "not probed" (4).
  [[ "$(_live_auth)" == "true" ]] || exit 4
  _rc=0
  _signin "$DEFAULT_ADMIN_EMAIL" "$DEFAULT_ADMIN_PASSWORD" >/dev/null || _rc=$?
  case $_rc in
    0) exit 1 ;;   # the default still works
    1) exit 0 ;;   # refused: retired
    *) exit 5 ;;   # no yes/no answer: not verified
  esac
fi

if [[ "$MODE" == "secure" ]]; then
  # Only a definite 'false' ends here; 'unknown' goes to the retry (and the
  # loud failure) in _secure_default_admin.
  if [[ "$(_live_auth)" == "false" ]]; then
    echo "WebUI login is not enforced by the running WebUI yet — nothing to rotate" >&2
    echo "  (the next start with WEBUI_AUTH=true retires the default password)." >&2
    exit 0   # --secure-default-admin: nothing to rotate yet
  fi
  _secure_default_admin || exit 1
  exit 0     # --secure-default-admin ends here; full mode never exits early
fi

# Auth ON: retire the default password before anything signs in with it.
# A failure warns loudly but does not stop the rest of the configuration.
_secure_default_admin || true

TOKEN=$(_signin "admin@localhost" "") || TOKEN=""
if [[ -z "$TOKEN" && -n "$WEBUI_ADMIN_EMAIL" ]]; then
  TOKEN=$(_signin "$WEBUI_ADMIN_EMAIL" "$WEBUI_ADMIN_PASSWORD") || TOKEN=""
fi

# Only the tool-server reconciliation actually needs the admin API; everything
# else here is written straight to WebUI's DB. This used to `exit 0` on a
# missing token, which meant that the moment an operator changed their WebUI
# password from the one in openbeast.conf — the normal case — the model
# connection, web search and per-model function calling silently stopped being
# configured on every startup. Degrade to skipping the one API-backed section.
TOKEN_OK=1
if [[ -z "$TOKEN" ]]; then
  TOKEN_OK=0
  echo "Warning: Could not get admin token — tool-server connections will be skipped." >&2
  echo "  Everything else (model connection, web search, native function calling)" >&2
  echo "  is applied directly and still works." >&2
  echo "  To restore full configuration: put the WebUI admin account's" >&2
  echo "  WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD in openbeast.conf and re-run." >&2
  echo "  Or configure manually:" >&2
  echo "  1. Admin Settings → External Tools → Add: OpenAPI, $MCPO_URL" >&2
  echo "  2. Admin Settings → Models → [model] → Function Calling: Native" >&2
  if [[ -n "${OPENBEAST_MCPO_ADMIN_KEY:-}${OPENBEAST_MCPO_GUEST_KEY:-}" ]]; then
    # Keys reach WebUI ONLY through this reconciliation. After a first
    # enable or --rotate, the server checks keys WebUI does not have.
    echo "ERROR: tool-server keys are set, but WebUI's stored connection keys could" >&2
    echo "  not be updated. If they were enabled or rotated since WebUI last saw them," >&2
    echo "  EVERY tool call from WebUI now fails (401/403) until this script runs" >&2
    echo "  with a working admin sign-in (WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD)." >&2
  fi
fi

AUTH="Authorization: Bearer ${TOKEN:-}"

# --- 0. Point WebUI's model connection at the OpenBeast endpoint ---
# Open WebUI PERSISTS the OpenAI connection in its DB and IGNORES the
# OPENAI_API_BASE_URL env var once the DB has a value. So the env in
# docker-compose only seeds a fresh install — for an existing WebUI we must set
# the DB connection ourselves. OPENBEAST_MODEL_URL (from lib/conf.sh) is the
# router (:8088) when AGENT_ROUTER=true, else llama-server (:8080). Idempotent.
if [[ -n "${OPENBEAST_MODEL_URL:-}" ]]; then
  echo "  Setting WebUI model connection → $OPENBEAST_MODEL_URL ..."
  if docker exec -e MODEL_URL="$OPENBEAST_MODEL_URL" open-webui python3 -c "
import sqlite3, json, os, sys
db = sqlite3.connect('/app/backend/data/webui.db')
url = os.environ['MODEL_URL']
row = db.execute(\"SELECT value FROM config WHERE key='openai.api_base_urls'\").fetchone()
cur = json.loads(row[0]) if row and row[0] else []
if cur != [url]:
    db.execute(\"UPDATE config SET value=? WHERE key='openai.api_base_urls'\", (json.dumps([url]),))
    db.commit()
    sys.exit(3)   # signal 'changed'
" 2>/dev/null; then
    echo "    already set"
  elif [[ $? -eq 3 ]]; then
    echo "    connection updated"
  else
    echo "    (could not set connection — WebUI may not be up yet)"
  fi
fi

# --- 1. Configure MCPO tool servers with RBAC (two connections, same MCPO) ---
# See docs/RBAC_PLAN.md. Two connections back the one MCPO instance:
#   id=1 "privileged"  filter !web_search,!fetch,!weather (the other 16),
#                      admin-only (empty access_grants → non-admins denied;
#                      admins bypass)
#   id=2 "web"         filter web_search,fetch,weather — public (everyone incl.
#                      guests). fetch is guest-safe since RBAC Phase 2: it
#                      refuses non-http(s) schemes and any host resolving to
#                      loopback/private/link-local space (SSRF-guarded in
#                      agents/tools.py), so it can't reach MCPO, metadata
#                      services, or the local disk.
# Models reference both (meta.toolIds). Open WebUI enforces per-connection
# access at tool-resolution time, so of OUR tools a `user`-role (family/guest)
# account resolves web_search + fetch + weather only — never bash/file/agent
# tools. (WebUI's own builtin tools are separate and per-model; section 2
# turns the clock/knowledge/chat-search ones off.) `admin`
# accounts get all 19 via BYPASS_ADMIN_ACCESS_CONTROL (each tool lives on
# exactly one connection, so no duplicates). Idempotent: reconciles to this
# exact shape every run without clobbering unrelated connections.
if [[ "$TOKEN_OK" != "1" ]]; then
  echo "  Skipping tool-server reconciliation (no admin token — see warning above)."
else
echo "  Reconciling RBAC tool-server connections..."
# Both connections target the ONE identity tool server (:3001,
# agents/openapi_tools.py). RBAC Phase 2 keys (conf.sh) differentiate them:
# connection 1 carries the admin key (all tools), connection 2 the guest key
# (server enforces GUEST_TOOLS — web_search/fetch/weather — only for it). The
# two filter lists below must partition the tools along GUEST_TOOLS
# (tests/test_weather.py pins it): a guest tool left off connection 2 is
# server-allowed but never offered to a non-admin account. Keys absent = both
# connections keyless, server open — Phase-1 single-user behavior.
# Guarded: under set -euo pipefail a non-JSON GET (a 502 page mid-boot) made
# python exit 1 and killed the whole run before sections 1b, 1c and 2.
if ! ob_curl_hdr "$AUTH" -s "$WEBUI_URL/api/v1/configs/tool_servers" 2>/dev/null \
  | MCPO_URL="$MCPO_URL" python3 -c "
import sys, os, json
MCPO = os.environ['MCPO_URL']
ADMIN_KEY = os.environ.get('OPENBEAST_MCPO_ADMIN_KEY', '').strip()
GUEST_KEY = os.environ.get('OPENBEAST_MCPO_GUEST_KEY', '').strip()
# Each connection carries ITS profile key whenever that key is set —
# independently, the same predicate the server uses (either key = keyed).
# Requiring both here sent NO key on either connection when only one was
# configured, so every call, admin included, got 401 from a keyed server.
data = json.load(sys.stdin)
conns = [c for c in data.get('TOOL_SERVER_CONNECTIONS', [])
         if not (c.get('info', {}).get('id') in ('1', '2', 'local-tools'))]
priv = {'url': MCPO, 'path': 'openapi.json', 'type': 'openapi',
        'auth_type': 'bearer' if ADMIN_KEY else 'none',
        'headers': None, 'key': ADMIN_KEY,
        'config': {'enable': True, 'function_name_filter_list': '!web_search,!fetch,!weather', 'access_grants': []},
        'spec_type': 'url', 'spec': '',
        'info': {'id': '1', 'name': 'Local Tools (privileged)',
                 'description': 'bash, file r/w/edit, grep, agents, skills — admin-only'}}
web = {'url': MCPO, 'path': 'openapi.json', 'type': 'openapi',
       'auth_type': 'bearer' if GUEST_KEY else 'none',
       'headers': None, 'key': GUEST_KEY,
       'config': {'enable': True, 'function_name_filter_list': 'web_search,fetch,weather',
                  'access_grants': [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]},
       'spec_type': 'url', 'spec': '',
       'info': {'id': '2', 'name': 'Web Search (all users)',
                'description': 'web_search via SearXNG, SSRF-guarded fetch, weather — safe for guest accounts'}}
if ADMIN_KEY and not GUEST_KEY:
    # No guest profile: the keyless connection 2 would 401 on the keyed
    # server for EVERYONE — admins lost web_search/fetch/weather, which live
    # only there. Connection 1 (admin key) carries every tool instead, and
    # connection 2 is switched off (non-admin accounts get no tools).
    priv['config']['function_name_filter_list'] = ''
    web['config']['enable'] = False
print(json.dumps({'TOOL_SERVER_CONNECTIONS': conns + [priv, web]}))
" | ob_curl_hdr "$AUTH" -s -H "Content-Type: application/json" \
    "$WEBUI_URL/api/v1/configs/tool_servers" -X POST -d @- > /dev/null
then
  echo "Warning: tool-server reconciliation failed (WebUI's tool_servers API did not" >&2
  echo "  answer JSON) — connections left as they were. Re-run ./scripts/configure-webui.sh." >&2
elif [[ -n "${OPENBEAST_MCPO_ADMIN_KEY:-}" && -n "${OPENBEAST_MCPO_GUEST_KEY:-}" ]]; then
  echo "  Tool server configured (Phase 2: admin + guest profiles keyed, one server :3001)."
elif [[ -n "${OPENBEAST_MCPO_ADMIN_KEY:-}" ]]; then
  echo "  Tool server configured with ONE profile key — the guest profile is disabled" >&2
  echo "  (the server fails closed): admins keep every tool on the privileged" >&2
  echo "  connection, non-admin accounts get none. Run scripts/setup-mcpo-keys.sh" >&2
  echo "  to generate both." >&2
elif [[ -n "${OPENBEAST_MCPO_GUEST_KEY:-}" ]]; then
  echo "  Tool server configured with ONE profile key — the admin profile is disabled" >&2
  echo "  (the server fails closed): every account, admins included, reaches only" >&2
  echo "  web_search/fetch/weather. Run scripts/setup-mcpo-keys.sh to generate both." >&2
else
  echo "  Tool server configured (privileged=admin-only, web_search+fetch+weather=all users)."
fi
fi   # TOKEN_OK

# --- 1b. Wire Open WebUI's built-in Web Search to the local SearXNG ---
# Distinct from the `web_search` TOOL configured above: this is the "Web
# Search" toggle in the chat composer, which Open WebUI implements itself
# (retrieval + citations) instead of via a tool call. It ships disabled with no
# engine set, so the toggle failed on every OpenBeast install even though we
# run SearXNG right there. Requires SearXNG to serve format=json — ours does
# (searxng/settings.yml `formats:`).
#
# Written straight to the DB rather than through the API because the API needs
# an admin token, and that sign-in fails the moment the operator changes their
# WebUI password from the one in openbeast.conf — which is the normal case.
SEARXNG_QUERY_URL="${SEARXNG_URL:-http://$_OB_LOCAL_HOST:8888}/search?q=<query>"
echo "  Wiring built-in Web Search → SearXNG ..."
if docker exec -e SXURL="$SEARXNG_QUERY_URL" open-webui python3 -c "
import sqlite3, json, os, sys, time
db = sqlite3.connect('/app/backend/data/webui.db')

def get(k, default=None):
    row = db.execute('SELECT value FROM config WHERE key=?', (k,)).fetchone()
    return json.loads(row[0]) if row else default

def put(k, v):
    db.execute('INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?) '
               'ON CONFLICT(key) DO UPDATE SET value=excluded.value, '
               'updated_at=excluded.updated_at',
               (k, json.dumps(v), int(time.time())))

want = {}

# CONFIGURE ONCE, THEN LEAVE IT ALONE. An earlier version asserted these values
# on every run, which meant an admin who turned Web Search off in the UI had it
# switched back on at the next start — forever, with no opt-out. We only fill in
# an engine that was never chosen; after that the operator owns the setting.
if not (get('web.search.engine') or '').strip():
    want['web.search.engine'] = 'searxng'
    want['web.search.searxng_query_url'] = os.environ['SXURL']
    want['web.search.enable'] = True
elif get('web.search.engine') == 'searxng':
    # One exception: the URL this script itself wrote. SearXNG binds BIND_HOST,
    # so after a bind change the stored http://<old probe host>:8888 is refused
    # and the composer's Web Search failed silently forever. Re-point a URL of
    # exactly that shape; any other URL is the operator's and stays.
    import re
    cur = get('web.search.searxng_query_url') or ''
    if cur != os.environ['SXURL'] and re.fullmatch(r'http://[^/]+:8888/search\?q=<query>', cur):
        want['web.search.searxng_query_url'] = os.environ['SXURL']

# Same restraint for Ollama, and only when the seeded default is the ONLY entry.
# Substring-matching the whole JSON list disabled real Ollama servers whenever a
# user had added one alongside the stale default, which is the common case.
urls = get('ollama.base_urls') or []
if urls and all('host.docker.internal' in u for u in urls) and get('ollama.enable') is True:
    want['ollama.enable'] = False

changed = [k for k, v in want.items() if get(k) != v]
for k in changed:
    put(k, want[k])
if changed:
    db.commit()
    sys.exit(3)   # signal 'changed'
" 2>/dev/null; then
  echo "    already configured (leaving operator settings alone)"
elif [[ $? -eq 3 ]]; then
  # No restart: this image reads config straight from the DB per request
  # (models/config.py — "Reads are direct DB lookups"), so a container bounce
  # would only kill in-flight streams for nothing.
  echo "    web search wired → $SEARXNG_QUERY_URL"
else
  echo "    (could not configure web search — WebUI may not be up yet)"
fi

# --- 1c. Background tasks: keep titles, turn off tags and follow-ups ---
# After every answer Open WebUI asks the model for a chat title, a set of tags
# and follow-up suggestions. On this rig's log (perf review 2026-10-09) those
# small requests generated more GPU-seconds than the chat turns they decorate,
# and on a single-slot server each one holds the slot for seconds after the
# answer. Titles are what the sidebar needs; the other two go.
#
# Written to the DB like 1b, and for the same two reasons: the env vars
# (ENABLE_TAGS_GENERATION / ENABLE_FOLLOW_UP_GENERATION) only seed a first
# boot, and the admin API needs a token this script often does not have. The
# server reads both keys from the DB on each task request (routers/tasks.py),
# so no restart is needed.
#
# Unlike 1b this is asserted on EVERY run — a WebUI update or a re-seeded
# volume would otherwise bring the cost back unnoticed — so the opt-out is in
# openbeast.conf, not the UI: WEBUI_BACKGROUND_TASKS=true leaves all of it
# exactly as Admin Settings → Interface has it.
if [[ "${WEBUI_BACKGROUND_TASKS:-false}" == "true" ]]; then
  echo "  Background tasks: left as WebUI has them (WEBUI_BACKGROUND_TASKS=true)."
else
  echo "  Turning off tag + follow-up generation (titles stay on) ..."
  if docker exec open-webui python3 -c "
import sqlite3, json, sys, time
db = sqlite3.connect('/app/backend/data/webui.db')

def get(k):
    row = db.execute('SELECT value FROM config WHERE key=?', (k,)).fetchone()
    return json.loads(row[0]) if row else None

# A missing row means the default, which is ON. task.title.enable is not ours.
changed = [k for k in ('task.tags.enable', 'task.follow_up.enable') if get(k) is not False]
for k in changed:
    db.execute('INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?) '
               'ON CONFLICT(key) DO UPDATE SET value=excluded.value, '
               'updated_at=excluded.updated_at',
               (k, json.dumps(False), int(time.time())))
if changed:
    db.commit()
    sys.exit(3)   # signal 'changed'
" 2>/dev/null; then
    echo "    already off"
  elif [[ $? -eq 3 ]]; then
    echo "    off (to keep them: WEBUI_BACKGROUND_TASKS=true in openbeast.conf, then re-enable in Admin Settings → Interface)"
  else
    echo "    (could not set them — WebUI may not be up yet)"
  fi
fi

# --- 1d. A fresh random seed per chat request (the random_seed filter) ---
# TensorFold (the spark-hydra GLM server) falls back to a FIXED seed when a
# request names none, so WebUI's Regenerate re-sent the same request and got
# the same answer back word for word (probed 2026-11-16: three unseeded
# requests at temperature 0.6, one text; seeds 1/2/3, three texts). The
# global Filter function agents/webui_functions/random_seed.py gives every
# chat request WITHOUT a seed a random one; a seed set in the chat's Controls
# or the model's params still wins and stays reproducible. On llama.cpp,
# which already draws a random seed per request, it changes nothing.
#
# Installed through WebUI's functions API (routers/functions.py of the pinned
# 0.13.3: GET /export, POST /create, /id/{id}/update, /id/{id}/toggle,
# /id/{id}/toggle/global), not the DB, because the API also compiles the
# module into WebUI's live function cache. Both toggles FLIP the flag, so
# each is called only when the flag is wrong. Idempotent: created once,
# updated only when the source changed. WEBUI_RANDOM_SEED=false switches an
# installed copy off (is_active=false) and installs nothing.
#
# --- 1e. The serving host's thinking default (the thinking_default filter) ---
# INFERENCE_THINKING=on|off (openbeast.conf) is the host's thinking default
# for chats that name none. beast-gate applies it to remote devices, but
# WebUI talks to the server on loopback, so the global Filter function
# agents/webui_functions/thinking_default.py applies it here: it sets
# chat_template_kwargs.enable_thinking on every chat request that does not
# already choose (the chat's Controls or the model's params still win).
# This script writes the conf value into the filter's DEFAULT_THINKING line
# before installing it, so the installed source tracks the conf. With
# INFERENCE_THINKING unset, an installed copy is switched off and nothing is
# installed. WebUI 0.13.3 shows the returned reasoning_content as a
# collapsed "Thought for N seconds" block.
#
# Both filters go through the same installer, from ONE export of WebUI's
# function list.
RANDOM_SEED_FN_ID="random_seed"
RANDOM_SEED_FN_SRC="$REPO_DIR/agents/webui_functions/random_seed.py"
THINKING_FN_ID="thinking_default"
THINKING_FN_SRC="$REPO_DIR/agents/webui_functions/thinking_default.py"
_fn_post() {   # _fn_post <path> [json on stdin] — prints the reply body
  ob_curl_hdr "$AUTH" -s -m 15 -X POST -H "Content-Type: application/json" \
    "$WEBUI_URL/api/v1/functions$1" "${@:2}" 2>/dev/null || true
}
_fn_is_fn() {  # _fn_is_fn <id> — does stdin hold a function object with that id?
  FN_ID="$1" python3 -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if isinstance(d, dict) and d.get("id") == os.environ["FN_ID"] else 1)' 2>/dev/null
}
# _fn_source <src> <subst> — the source to install: the file, with the line
# `NAME = ...` replaced by `NAME = "value"` when <subst> is NAME=value.
_fn_source() {
  FN_SRC="$1" FN_SUBST="$2" python3 -c '
import os, re, sys
src = open(os.environ["FN_SRC"]).read()
sub = os.environ.get("FN_SUBST", "")
if sub:
    name, _, val = sub.partition("=")
    src = re.sub(r"(?m)^" + re.escape(name) + r" = .*$",
                 lambda m: name + " = " + repr(val).replace(chr(39), chr(34)), src, count=1)
sys.stdout.write(src)'
}
# _configure_filter <id> <src> <name> <description> <want> <label> <off-note> [subst]
_configure_filter() {
  local fid="$1" fsrc="$2" fname="$3" fdesc="$4" want="$5" label="$6" offnote="$7" subst="${8:-}"
  local plan action content
  if [[ "$want" == "true" && ! -f "$fsrc" ]]; then
    echo "    Warning: $fsrc is missing — $label not installed." >&2
    return 0
  fi
  content=""
  if [[ "$want" == "true" ]]; then
    # The trailing x keeps the file's final newline ($(...) strips them),
    # so the content compares equal to what an earlier run installed.
    content="$(_fn_source "$fsrc" "$subst" && printf x)" || true
    if [[ "$content" != *x ]]; then
      echo "    Warning: could not read $fsrc — $label not installed." >&2
      return 0
    fi
    content="${content%x}"
  fi
  # The plan: one action per line, from the live state and the wanted one.
  plan=$(printf '%s' "$FN_EXPORT_JSON" | FN_ID="$fid" FN_CONTENT="$content" \
      WANT="$want" python3 -c '
import json, os, sys
try:
    fns = json.load(sys.stdin)
except Exception:
    print("unreadable"); sys.exit(0)
if not isinstance(fns, list):
    print("unreadable"); sys.exit(0)
fid, want = os.environ["FN_ID"], os.environ["WANT"] == "true"
cur = next((f for f in fns if isinstance(f, dict) and f.get("id") == fid), None)
if not want:
    if cur and cur.get("is_active"):
        print("toggle")
    sys.exit(0)
src = os.environ["FN_CONTENT"]
if cur is None:
    print("create"); print("toggle"); print("toggle/global")
    sys.exit(0)
if cur.get("content") != src:
    print("update")
if not cur.get("is_active"):
    print("toggle")
if not cur.get("is_global"):
    print("toggle/global")
' 2>/dev/null || echo unreadable)
  if [[ "$plan" == "unreadable" ]]; then
    echo "    Warning: WebUI's functions API did not answer a list — $label" >&2
    echo "    left as it was. (Functions disabled with ENABLE_FUNCTIONS=false?)" >&2
    return 0
  fi
  if [[ -z "$plan" ]]; then
    if [[ "$want" == "true" ]]; then
      echo "    already installed (active, global)"
    else
      echo "    off ($offnote)"
    fi
    return 0
  fi
  while IFS= read -r action; do
    [[ -z "$action" ]] && continue
    case "$action" in
      create|update)
        local path="/create"
        [[ "$action" == "update" ]] && path="/id/$fid/update"
        if ! FN_ID="$fid" FN_NAME="$fname" FN_DESC="$fdesc" FN_CONTENT="$content" python3 -c '
import json, os
print(json.dumps({"id": os.environ["FN_ID"], "name": os.environ["FN_NAME"],
                  "content": os.environ["FN_CONTENT"],
                  "meta": {"description": os.environ["FN_DESC"]}}))' \
            | _fn_post "$path" -d @- | _fn_is_fn "$fid"; then
          echo "    Warning: could not $action the $label through WebUI's API." >&2
          return 0
        fi ;;
      toggle|toggle/global)
        if ! _fn_post "/id/$fid/$action" | _fn_is_fn "$fid"; then
          echo "    Warning: could not $action the $label through WebUI's API." >&2
          return 0
        fi ;;
    esac
  done <<< "$plan"
  if [[ "$want" == "true" ]]; then
    echo "    $label installed/updated (active, global)"
  else
    echo "    $label switched off ($offnote)"
  fi
}
if [[ "$TOKEN_OK" != "1" ]]; then
  echo "  Skipping the random seed filter (no admin token — see warning above)."
  if [[ -n "${INFERENCE_THINKING:-}" ]]; then
    echo "  Skipping the thinking default filter (no admin token — see warning above)."
  fi
else
  FN_EXPORT_JSON=$(ob_curl_hdr "$AUTH" -s -m 15 "$WEBUI_URL/api/v1/functions/export" 2>/dev/null || true)
  echo "  Random seed filter (fresh seed per chat request, so Regenerate differs) ..."
  _want=true
  [[ "${WEBUI_RANDOM_SEED:-true}" == "true" ]] || _want=false
  _configure_filter "$RANDOM_SEED_FN_ID" "$RANDOM_SEED_FN_SRC" "Random Seed" \
    "A fresh random seed for every chat request that names none, so Regenerate gives a new answer (OpenBeast)" \
    "$_want" "random seed filter" "WEBUI_RANDOM_SEED=false"
  # Unset INFERENCE_THINKING: only an installed, active copy costs a call
  # (switching it off); a WebUI that never had it sees nothing new.
  _want=false
  [[ -n "${INFERENCE_THINKING:-}" ]] && _want=true
  if [[ "$_want" == "true" ]] || printf '%s' "$FN_EXPORT_JSON" | FN_ID="$THINKING_FN_ID" python3 -c '
import json, os, sys
try:
    fns = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if isinstance(fns, list) and any(isinstance(f, dict) and f.get("id") == os.environ["FN_ID"] for f in fns) else 1)' 2>/dev/null; then
    echo "  Thinking default filter (INFERENCE_THINKING=${INFERENCE_THINKING:-unset} for chats that choose none) ..."
    _configure_filter "$THINKING_FN_ID" "$THINKING_FN_SRC" "Thinking Default" \
      "The serving host's thinking default (INFERENCE_THINKING) for chats that do not choose one (OpenBeast)" \
      "$_want" "thinking default filter" "INFERENCE_THINKING unset" \
      "DEFAULT_THINKING=${INFERENCE_THINKING:-}"
  fi
  unset _want
fi

# Model tool wiring uses these two connection ids.
TOOL_REFS='["server:1","server:2"]'

# --- 2. Native function calling, system prompt and tools on SERVED models ---
# Poll until the served model list answers (bounded: up to 30s, 1s interval,
# proceed on the first non-empty list). A blind sleep either wasted time or —
# on a slow first scan — missed the models entirely.
#
# The list comes from the inference endpoint WebUI itself talks to — it is the
# truth about what is served. WebUI's /api/models (admin token only) is the
# fallback when that endpoint does not answer from here, and is not used
# first: it HIDES a served model whose override row is inactive (the very row
# this step must repair) and it LISTS workspace presets, which are not served
# models and are not ours to rewrite.
MODELS=""
for _i in $(seq 1 30); do
  MODELS=$(ob_curl_bearer "${LLAMA_API_KEY:-}" -s -m 5 \
      "${OPENBEAST_MODEL_URL:-http://localhost:8080/v1}/models" 2>/dev/null \
    | python3 -c "
import sys, json
for m in json.load(sys.stdin).get('data', []):
    mid = m.get('id', '')
    if mid and 'arena' not in mid:
        print(mid)
" 2>/dev/null || true)
  if [[ -z "$MODELS" && "$TOKEN_OK" == "1" ]]; then
    MODELS=$(ob_curl_hdr "$AUTH" -s -m 5 "$WEBUI_URL/api/models" 2>/dev/null \
      | python3 -c "
import sys, json
for m in json.load(sys.stdin).get('data', []):
    mid = m.get('id', '')
    # Skip arena/internal models and workspace presets (base_model_id set).
    if mid and 'arena' not in mid and not m.get('preset'):
        print(mid)
" 2>/dev/null || true)
  fi
  [[ -n "$MODELS" ]] && break
  sleep 1
done

if [[ -z "$MODELS" ]]; then
  echo "  No models detected yet. Re-run ./configure-webui.sh after first chat."
else
  # Write system prompt to a temp file for the DB update script to read
  PROMPT_FILE=""
  if [[ -n "$SYSTEM_PROMPT" ]]; then
    PROMPT_FILE=$(mktemp)
    echo "$SYSTEM_PROMPT" > "$PROMPT_FILE"
    docker cp "$PROMPT_FILE" open-webui:/tmp/system-prompt.txt > /dev/null 2>&1 || true
    rm -f "$PROMPT_FILE"
  fi

  while IFS= read -r model_id; do
    [[ -z "$model_id" ]] && continue
    echo "  Configuring $model_id..."
    docker exec -e MODEL_ID="$model_id" -e TOOL_REFS="$TOOL_REFS" open-webui python3 -c "
import sqlite3, json, os, time

db = sqlite3.connect('/app/backend/data/webui.db')
# Passed via env, not interpolated into source — a model alias containing
# a quote must not become Python code.
model_id = os.environ['MODEL_ID']
# Both RBAC connections; per-user access control decides which resolve.
tool_refs = json.loads(os.environ['TOOL_REFS'])

# Load system prompt
system_prompt = ''
prompt_path = '/tmp/system-prompt.txt'
if os.path.exists(prompt_path):
    with open(prompt_path) as f:
        system_prompt = f.read().strip()

# Under native function calling the pinned image injects its OWN builtin
# tools into every UI chat turn, for every role, unless meta.builtinTools
# turns a category off (utils/tools.py get_builtin_tools: each category
# defaults to on): the clock pair, 4-7 knowledge-base tools and the chat
# search pair — about 11 schemas on top of ours, every turn, guests included.
# Turned off ONCE, only when the row has no builtinTools yet, so a category
# an admin re-enables in Admin -> Models stays on.
BUILTIN_OFF = {'time': False, 'knowledge': False, 'chats': False}

cols = {r[1] for r in db.execute('PRAGMA table_info(model)')}
has_base = 'base_model_id' in cols
row = db.execute('SELECT params, meta, is_active%s FROM model WHERE id=?'
                 % (', base_model_id' if has_base else ''), (model_id,)).fetchone()
if row and has_base and row[3]:
    # A workspace preset (a user's persona on top of a base model) that
    # happens to share a served id. Its prompt and tools are its owner's.
    print('    Workspace preset — left as its owner configured it.')
elif row:
    params = json.loads(row[0]) if row[0] else {}
    meta = json.loads(row[1]) if row[1] else {}
    changed = False

    if params.get('function_calling') != 'native':
        params['function_calling'] = 'native'
        changed = True

    if system_prompt and params.get('system') != system_prompt:
        params['system'] = system_prompt
        changed = True

    # Attach both RBAC tool connections by default (no per-chat toggle).
    if tool_refs and meta.get('toolIds') != tool_refs:
        meta['toolIds'] = tool_refs
        changed = True

    # WebUI's own builtin tools: off once, then the admin's (see BUILTIN_OFF).
    if 'builtinTools' not in meta:
        meta['builtinTools'] = dict(BUILTIN_OFF)
        changed = True

    # This model is being SERVED: its override row must be active. An
    # inactive base-model row makes WebUI drop the served model from
    # /api/models entirely (utils/models.py), and the stale-row sweep this
    # script used to run deactivated it on every model switch, fast boot
    # and rollback — with nothing ever turning it back on.
    reactivated = not row[2]

    if changed or reactivated:
        db.execute('UPDATE model SET params=?, meta=?, is_active=1 WHERE id=?',
                   (json.dumps(params), json.dumps(meta), model_id))
        db.commit()
        print('    Updated (native FC + system prompt + default tools%s).'
              % (', re-activated' if reactivated else ''))
    else:
        print('    Already configured.')
else:
    # Model detected by API but not yet in DB — insert it
    params = json.dumps({'function_calling': 'native'})
    meta_dict = {'profile_image_url': '/static/favicon.png', 'description': None, 'capabilities': {'vision': True, 'citations': True},
                 'builtinTools': dict(BUILTIN_OFF)}
    if tool_refs:
        meta_dict['toolIds'] = tool_refs
    if system_prompt:
        params_dict = json.loads(params)
        params_dict['system'] = system_prompt
        params = json.dumps(params_dict)
    meta = json.dumps(meta_dict)
    now = int(time.time())
    db.execute(
        'INSERT INTO model (id, user_id, name, meta, params, created_at, updated_at, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (model_id, 'system', model_id, meta, params, now, now, 1)
    )
    db.commit()
    print('    Created model entry (native FC + system prompt + default tools).')

# Guest access. The pinned image shows a model with a DB row to a user-role
# account only through an access_grant row (utils/models.py
# get_filtered_models; the chat path checks the same grant), and this script
# writes the row with user_id 'system' and no grant — so the documented guest
# tier (docs/RBAC_PLAN.md) logged in to an empty model picker. Grant public
# read ONCE per model: the ids already seeded are recorded beside the DB, so an
# admin who later makes a model private in Admin -> Models keeps that choice.
if not (row and has_base and row[3]):
    try:
        import uuid
        mark = os.path.join(os.path.dirname('/app/backend/data/webui.db'),
                            '.openbeast-model-access.json')
        try:
            seeded = set(json.load(open(mark)))
        except Exception:
            seeded = set()
        if model_id not in seeded:
            db.execute(
                'INSERT OR IGNORE INTO access_grant (id, resource_type, resource_id, '
                'principal_type, principal_id, permission, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (str(uuid.uuid4()), 'model', model_id, 'user', '*', 'read', int(time.time())))
            db.commit()
            seeded.add(model_id)
            with open(mark + '.tmp', 'w') as f:
                json.dump(sorted(seeded), f)
            os.replace(mark + '.tmp', mark)
            print('    Granted read access to every account (guests included).')
    except sqlite3.OperationalError:
        pass   # an image without access_grant: models are not access-gated there
" 2>/dev/null \
      || echo "    Warning: failed to configure model '$model_id' (docker exec error) — re-run ./scripts/configure-webui.sh" >&2
  done <<< "$MODELS"

  # No stale-row sweep. Rows for models no longer served (an earlier model,
  # the fast-boot bridge) need no cleanup: WebUI only shows a base-model
  # override whose id the endpoint serves (utils/models.py looks each row up
  # in the served list), so they vanish from the picker on their own and come
  # back configured when that model is served again. The sweep that used to
  # run here set is_active=0 on them, which (a) hid the model for good once it
  # was served again — nothing reactivated it — and (b) disabled every
  # workspace preset on each tokenless start, since presets never appear in
  # the endpoint's list.
fi

# No restart at the end. The pinned image reads every config key straight from
# the DB per request (models/config.py: "Reads are direct DB lookups";
# routers/openai.py re-reads openai.api_base_urls for each call), so the rows
# written above are live as soon as they commit. A `docker restart open-webui`
# here only dropped every in-flight chat stream and took the UI down for its
# boot time.

echo "Open WebUI configured."
