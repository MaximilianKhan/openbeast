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
  # The password travels via env + stdin, never argv: an argv JSON blob is
  # world-readable in /proc/*/cmdline while curl runs, and printf-splicing
  # broke on passwords containing '"' or '\'. /proc/<pid>/environ is
  # owner-only, and python json.dumps escapes correctly.
  SIGNIN_EMAIL="$1" SIGNIN_PASSWORD="$2" python3 -c \
    'import json,os; print(json.dumps({"email": os.environ["SIGNIN_EMAIL"], "password": os.environ["SIGNIN_PASSWORD"]}))' \
    | curl -s "$WEBUI_URL/api/v1/auths/signin" \
        -H "Content-Type: application/json" -d @- \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null \
    || true
}

TOKEN=$(_signin "admin@localhost" "")
if [[ -z "$TOKEN" && -n "$WEBUI_ADMIN_EMAIL" ]]; then
  TOKEN=$(_signin "$WEBUI_ADMIN_EMAIL" "$WEBUI_ADMIN_PASSWORD")
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
fi

AUTH="Authorization: Bearer ${TOKEN:-}"

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
if [[ "$TOKEN_OK" != "1" ]]; then
  echo "  Skipping tool-server reconciliation (no admin token — see warning above)."
else
echo "  Reconciling RBAC tool-server connections..."
# Both connections target the ONE identity tool server (:3001,
# agents/openapi_tools.py). RBAC Phase 2 keys (conf.sh) differentiate them:
# connection 1 carries the admin key (all tools), connection 2 the guest key
# (server enforces web_search/fetch only for it). Keys absent = both
# connections keyless, server open — Phase-1 single-user behavior.
curl -s -H "$AUTH" "$WEBUI_URL/api/v1/configs/tool_servers" 2>/dev/null \
  | MCPO_URL="$MCPO_URL" python3 -c "
import sys, os, json
MCPO = os.environ['MCPO_URL']
ADMIN_KEY = os.environ.get('OPENBEAST_MCPO_ADMIN_KEY', '').strip()
GUEST_KEY = os.environ.get('OPENBEAST_MCPO_GUEST_KEY', '').strip()
keyed = bool(ADMIN_KEY and GUEST_KEY)
data = json.load(sys.stdin)
conns = [c for c in data.get('TOOL_SERVER_CONNECTIONS', [])
         if not (c.get('info', {}).get('id') in ('1', '2', 'local-tools'))]
priv = {'url': MCPO, 'path': 'openapi.json', 'type': 'openapi',
        'auth_type': 'bearer' if keyed else 'none',
        'headers': None, 'key': ADMIN_KEY if keyed else '',
        'config': {'enable': True, 'function_name_filter_list': '!web_search,!fetch', 'access_grants': []},
        'spec_type': 'url', 'spec': '',
        'info': {'id': '1', 'name': 'Local Tools (privileged)',
                 'description': 'bash, file r/w/edit, grep, agents, skills — admin-only'}}
web = {'url': MCPO, 'path': 'openapi.json', 'type': 'openapi',
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
  echo "  Tool server configured (Phase 2: admin + guest profiles keyed, one server :3001)."
else
  echo "  Tool server configured (privileged=admin-only, web_search+fetch=all users)."
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
SEARXNG_QUERY_URL="${SEARXNG_URL:-http://localhost:8888}/search?q=<query>"
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

# Model tool wiring uses these two connection ids.
TOOL_REFS='["server:1","server:2"]'

# Resolve the MCPO server's id so models can reference it in meta.toolIds
# ("server:<id>") — that's what attaches the tools to every chat by default
# instead of requiring the per-conversation ＋-menu toggle.
# --- 2. Set native function calling for all models ---
# Poll until Open WebUI has detected models from llama.cpp (bounded: up to
# 30s, 1s interval, proceed on the first non-empty list). A blind sleep
# either wasted time or — on a slow first scan — missed the models entirely.
#
# Without an admin token WebUI's /api/models returns 401, so fall back to
# asking the inference endpoint itself for the model ids — the same list WebUI
# would discover. The existing-setting probe ('|<fc>') is only an optimization
# to skip already-configured models; the DB step below is idempotent anyway.
MODELS=""
for _i in $(seq 1 30); do
  if [[ "$TOKEN_OK" == "1" ]]; then
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
  else
    MODELS=$(curl -s -m 5 ${LLAMA_API_KEY:+-H "Authorization: Bearer $LLAMA_API_KEY"} \
        "${OPENBEAST_MODEL_URL:-http://localhost:8080/v1}/models" 2>/dev/null \
      | python3 -c "
import sys, json
for m in json.load(sys.stdin).get('data', []):
    mid = m.get('id', '')
    if mid and 'arena' not in mid:
        print(f'{mid}|')
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

  # Deactivate rows for models we are no longer serving.
  #
  # Nothing ever cleaned these up, so they accumulate one per model ever loaded
  # — and llama.cpp's single-model server ignores the `model` field entirely, so
  # picking a dead entry does NOT error: you get the loaded model's answers
  # under the wrong name, at the wrong advertised context, recorded that way in
  # the transcript. Fast boot makes this concrete by design, briefly serving a
  # bootstrap bridge whose row would otherwise linger as a selectable option.
  ACTIVE_IDS=$(printf '%s\n' "$MODELS" | cut -d'|' -f1 | paste -sd'\n' -)
  docker exec -i -e ACTIVE_IDS="$ACTIVE_IDS" open-webui python3 -c "
import sqlite3, os
db = sqlite3.connect('/app/backend/data/webui.db')
active = {m for m in os.environ.get('ACTIVE_IDS', '').split('\n') if m.strip()}
if active:   # never blank the list on a failed/empty model probe
    rows = db.execute('SELECT id FROM model WHERE is_active=1').fetchall()
    stale = [r[0] for r in rows if r[0] not in active]
    for mid in stale:
        db.execute('UPDATE model SET is_active=0 WHERE id=?', (mid,))
    if stale:
        db.commit()
        print('    Deactivated %d stale model entr%s (no longer served).'
              % (len(stale), 'y' if len(stale) == 1 else 'ies'))
" 2>/dev/null || true
fi

# Open WebUI caches its persisted config at startup, so anything we wrote
# straight to the DB (the model connection, the web-search settings) only takes
# effect after a restart. All config above is persisted and survives it. (No-op
# on a fresh install where the env already seeded the right values, so both
# flags stay 0.)
if [[ "${CONN_CHANGED:-0}" == "1" || "${WEBSEARCH_CHANGED:-0}" == "1" ]]; then
  echo "  Restarting Open WebUI to load the new configuration..."
  docker restart open-webui >/dev/null 2>&1 || true
fi

echo "Open WebUI configured."
