#!/bin/bash
# Configure Open WebUI for the OpenBeast stack.
# Idempotent — safe to run multiple times.
#
# Sets up:
#   1. MCPO tool server connection (OpenAPI on localhost:3001)
#   2. Native function calling for all detected models
#   3. System prompt from system-prompt.md
#
# Called automatically by start.sh after Open WebUI is ready.

set -euo pipefail

WEBUI_URL="${WEBUI_URL:-http://localhost:3000}"
MCPO_URL="${MCPO_URL:-http://localhost:3001}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/conf.sh"   # WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD

# Refresh the generated skill menu so the prompt always matches skills/
# (non-fatal: a broken generator must not block WebUI configuration).
python3 "$SCRIPT_DIR/generate-skill-index.py" >/dev/null 2>&1 \
  || echo "Warning: skill index regeneration failed — prompt may list stale skills" >&2

# Load system prompt: soul file + tool guidance (Open WebUI needs both)
SYSTEM_PROMPT=""
if [[ -f "$REPO_DIR/system-prompt.md" ]]; then
  SYSTEM_PROMPT=$(cat "$REPO_DIR/system-prompt.md")
fi
if [[ -f "$REPO_DIR/system-prompt-tools.md" ]]; then
  SYSTEM_PROMPT="$SYSTEM_PROMPT"$'\n\n'"$(cat "$REPO_DIR/system-prompt-tools.md")"
fi

echo "Configuring Open WebUI..."

# Wait for Open WebUI to be ready — bounded so a container that never comes
# up can't leave this loop orphaned forever (start.sh backgrounds us).
for _i in $(seq 1 180); do
  curl -s "$WEBUI_URL/api/version" > /dev/null 2>&1 && break
  if [[ $_i -eq 180 ]]; then
    echo "Error: Open WebUI not reachable after 180s — giving up." >&2
    echo "       Re-run ./scripts/configure-webui.sh once it's up." >&2
    exit 1
  fi
  sleep 1
done

# Get admin token. Two paths:
#   • WEBUI_AUTH=false (legacy): the default admin user signs in with an
#     empty password.
#   • WEBUI_AUTH=true (default since the Tailscale rollout): set
#     WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD in openbeast.conf to the
#     admin account you created on first visit.
_signin() {
  # || true: a non-JSON response (502 HTML, connection reset) makes the
  # python step exit 1; under pipefail that would silently kill the whole
  # script at the TOKEN=$(...) assignment instead of reaching the fallback
  # guidance below.
  curl -s "$WEBUI_URL/api/v1/auths/signin" \
    -H "Content-Type: application/json" \
    -d "$(printf '{"email":"%s","password":"%s"}' "$1" "$2")" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null \
    || true
}

TOKEN=$(_signin "admin@localhost" "")
if [[ -z "$TOKEN" && -n "$WEBUI_ADMIN_EMAIL" ]]; then
  TOKEN=$(_signin "$WEBUI_ADMIN_EMAIL" "$WEBUI_ADMIN_PASSWORD")
fi

if [[ -z "$TOKEN" ]]; then
  echo "Warning: Could not get admin token." >&2
  echo "  If you just enabled auth: create the admin account in the browser" >&2
  echo "  first, then put WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD in" >&2
  echo "  openbeast.conf and re-run this script. Or configure manually:" >&2
  echo "  1. Admin Settings → External Tools → Add: OpenAPI, $MCPO_URL" >&2
  echo "  2. Admin Settings → Models → [model] → Function Calling: Native" >&2
  exit 0
fi

AUTH="Authorization: Bearer $TOKEN"

# --- 0. Point WebUI's model connection at the OpenBeast endpoint ---
# Open WebUI PERSISTS the OpenAI connection in its DB and IGNORES the
# OPENAI_API_BASE_URL env var once the DB has a value. So the env in
# docker-compose only seeds a fresh install — for an existing WebUI we must set
# the DB connection ourselves. OPENBEAST_MODEL_URL (from lib/conf.sh) is the
# router (:8088) when AGENT_ROUTER=true, else llama-server (:8080). Idempotent.
CONN_CHANGED=0
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
    echo "    connection updated"; CONN_CHANGED=1
  else
    echo "    (could not set connection — WebUI may not be up yet)"
  fi
fi

# --- 1. Configure MCPO tool servers with RBAC (two connections, same MCPO) ---
# See docs/RBAC_PLAN.md. Two connections back the one MCPO instance:
#   id=1 "privileged"  filter !web_search,!fetch (15 OS-touching tools),
#                      admin-only (empty access_grants → non-admins denied;
#                      admins bypass)
#   id=2 "web"         filter web_search,fetch — public (everyone incl.
#                      guests). fetch is guest-safe since RBAC Phase 2: it
#                      refuses non-http(s) schemes and any host resolving to
#                      loopback/private/link-local space (SSRF-guarded in
#                      agents/tools.py), so it can't reach MCPO, metadata
#                      services, or the local disk.
# Models reference both (meta.toolIds). Open WebUI enforces per-connection
# access at tool-resolution time, so a `user`-role (family/guest) account
# resolves web_search + fetch ONLY — never bash/file/agent tools. `admin`
# accounts get all 15 via BYPASS_ADMIN_ACCESS_CONTROL (each tool lives on
# exactly one connection, so no duplicates). Idempotent: reconciles to this
# exact shape every run without clobbering unrelated connections.
echo "  Reconciling RBAC tool-server connections..."
# RBAC Phase 2 (opt-in): when BOTH per-profile keys are exported (conf.sh),
# connection 1 authenticates to the keyed admin instance and connection 2
# points at the guest instance (web tools only, its own key) — the WebUI
# grant filter is then no longer the only wall. Keys absent = Phase 1,
# byte-for-byte the previous single-instance shape.
MCPO_GUEST_URL="${MCPO_GUEST_URL:-http://localhost:${MCPO_GUEST_PORT:-3002}}"
curl -s -H "$AUTH" "$WEBUI_URL/api/v1/configs/tool_servers" 2>/dev/null \
  | MCPO_URL="$MCPO_URL" MCPO_GUEST_URL="$MCPO_GUEST_URL" python3 -c "
import sys, os, json
MCPO = os.environ['MCPO_URL']
GUEST_URL = os.environ['MCPO_GUEST_URL']
ADMIN_KEY = os.environ.get('OPENBEAST_MCPO_ADMIN_KEY', '').strip()
GUEST_KEY = os.environ.get('OPENBEAST_MCPO_GUEST_KEY', '').strip()
keyed = bool(ADMIN_KEY and GUEST_KEY)
data = json.load(sys.stdin)
conns = [c for c in data.get('TOOL_SERVER_CONNECTIONS', [])
         if not (c.get('url') in (MCPO, GUEST_URL) and c.get('info', {}).get('id') in ('1', '2', 'local-tools'))]
priv = {'url': MCPO, 'path': 'openapi.json', 'type': 'openapi',
        'auth_type': 'bearer' if keyed else 'none',
        'headers': None, 'key': ADMIN_KEY if keyed else '',
        'config': {'enable': True, 'function_name_filter_list': '!web_search,!fetch', 'access_grants': []},
        'spec_type': 'url', 'spec': '',
        'info': {'id': '1', 'name': 'Local Tools (privileged)',
                 'description': 'bash, file r/w/edit, grep, agents, skills — admin-only'}}
web = {'url': GUEST_URL if keyed else MCPO, 'path': 'openapi.json', 'type': 'openapi',
       'auth_type': 'bearer' if keyed else 'none',
       'headers': None, 'key': GUEST_KEY if keyed else '',
       'config': {'enable': True, 'function_name_filter_list': 'web_search,fetch',
                  'access_grants': [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]},
       'spec_type': 'url', 'spec': '',
       'info': {'id': '2', 'name': 'Web Search (all users)',
                'description': 'web_search via SearXNG + SSRF-guarded fetch — safe for guest accounts'}}
print(json.dumps({'TOOL_SERVER_CONNECTIONS': conns + [priv, web]}))
" | curl -s -H "$AUTH" -H "Content-Type: application/json" \
    "$WEBUI_URL/api/v1/configs/tool_servers" -X POST -d @- > /dev/null
if [[ -n "${OPENBEAST_MCPO_ADMIN_KEY:-}" && -n "${OPENBEAST_MCPO_GUEST_KEY:-}" ]]; then
  echo "  Tool servers configured (Phase 2: keyed admin :3001 + keyed guest :${MCPO_GUEST_PORT:-3002})."
else
  echo "  Tool servers configured (privileged=admin-only, web_search+fetch=all users)."
fi

# Model tool wiring uses these two connection ids.
TOOL_REFS='["server:1","server:2"]'

# Resolve the MCPO server's id so models can reference it in meta.toolIds
# ("server:<id>") — that's what attaches the tools to every chat by default
# instead of requiring the per-conversation ＋-menu toggle.
# --- 2. Set native function calling for all models ---
# Poll until Open WebUI has detected models from llama.cpp (bounded: up to
# 30s, 1s interval, proceed on the first non-empty list). A blind sleep
# either wasted time or — on a slow first scan — missed the models entirely.
MODELS=""
for _i in $(seq 1 30); do
  MODELS=$(curl -s -m 5 -H "$AUTH" "$WEBUI_URL/api/models" 2>/dev/null \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('data', []):
    mid = m.get('id', '')
    # Skip arena/internal models
    if mid and 'arena' not in mid:
        params = m.get('info', {}).get('params', {})
        fc = params.get('function_calling', '')
        print(f'{mid}|{fc}')
" 2>/dev/null || true)
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

  while IFS='|' read -r model_id fc_mode; do
    [[ -z "$model_id" ]] && continue
    echo "  Configuring $model_id..."
    docker exec -e MODEL_ID="$model_id" open-webui python3 -c "
import sqlite3, json, os, time

db = sqlite3.connect('/app/backend/data/webui.db')
# Passed via env, not interpolated into source — a model alias containing
# a quote must not become Python code.
model_id = os.environ['MODEL_ID']
# Both RBAC connections; per-user access control decides which resolve.
tool_refs = json.loads('$TOOL_REFS')

# Load system prompt
system_prompt = ''
prompt_path = '/tmp/system-prompt.txt'
if os.path.exists(prompt_path):
    with open(prompt_path) as f:
        system_prompt = f.read().strip()

row = db.execute('SELECT params, meta FROM model WHERE id=?', (model_id,)).fetchone()
if row:
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

    if changed:
        db.execute('UPDATE model SET params=?, meta=? WHERE id=?', (json.dumps(params), json.dumps(meta), model_id))
        db.commit()
        print('    Updated (native FC + system prompt + default tools).')
    else:
        print('    Already configured.')
else:
    # Model detected by API but not yet in DB — insert it
    params = json.dumps({'function_calling': 'native'})
    meta_dict = {'profile_image_url': '/static/favicon.png', 'description': None, 'capabilities': {'vision': True, 'citations': True}}
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
" 2>/dev/null \
      || echo "    Warning: failed to configure model '$model_id' (docker exec error) — re-run ./scripts/configure-webui.sh" >&2
  done <<< "$MODELS"
fi

# Open WebUI loads the OpenAI connection at startup, so a changed endpoint only
# takes effect after a restart. All config above is persisted in the DB and
# survives this restart. (No-op on a fresh install where the env already seeded
# the right URL, so CONN_CHANGED stays 0.)
if [[ "${CONN_CHANGED:-0}" == "1" ]]; then
  echo "  Restarting Open WebUI to load the new model connection..."
  docker restart open-webui >/dev/null 2>&1 || true
fi

echo "Open WebUI configured."
