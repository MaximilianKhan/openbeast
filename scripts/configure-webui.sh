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

# Load system prompt: soul file + tool guidance (Open WebUI needs both)
SYSTEM_PROMPT=""
if [[ -f "$REPO_DIR/system-prompt.md" ]]; then
  SYSTEM_PROMPT=$(cat "$REPO_DIR/system-prompt.md")
fi
if [[ -f "$REPO_DIR/system-prompt-tools.md" ]]; then
  SYSTEM_PROMPT="$SYSTEM_PROMPT"$'\n\n'"$(cat "$REPO_DIR/system-prompt-tools.md")"
fi

echo "Configuring Open WebUI..."

# Wait for Open WebUI to be ready
until curl -s "$WEBUI_URL/api/version" > /dev/null 2>&1; do
  sleep 1
done

# Get admin token. Two paths:
#   • WEBUI_AUTH=false (legacy): the default admin user signs in with an
#     empty password.
#   • WEBUI_AUTH=true (default since the Tailscale rollout): set
#     WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD in openbeast.conf to the
#     admin account you created on first visit.
_signin() {
  curl -s "$WEBUI_URL/api/v1/auths/signin" \
    -H "Content-Type: application/json" \
    -d "$(printf '{"email":"%s","password":"%s"}' "$1" "$2")" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null
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

# --- 1. Configure MCPO tool server ---
# Check if already configured
EXISTING=$(curl -s -H "$AUTH" "$WEBUI_URL/api/v1/configs/tool_servers" 2>/dev/null)
HAS_MCPO=$(echo "$EXISTING" | python3 -c "
import sys, json
data = json.load(sys.stdin)
conns = data.get('TOOL_SERVER_CONNECTIONS', [])
print('yes' if any(c.get('url') == '$MCPO_URL' and c.get('type') == 'openapi' for c in conns) else 'no')
" 2>/dev/null)

if [[ "$HAS_MCPO" != "yes" ]]; then
  echo "  Adding MCPO tool server ($MCPO_URL)..."
  curl -s -H "$AUTH" -H "Content-Type: application/json" \
    "$WEBUI_URL/api/v1/configs/tool_servers" \
    -X POST \
    -d '{
      "TOOL_SERVER_CONNECTIONS": [{
        "url": "'"$MCPO_URL"'",
        "path": "openapi.json",
        "type": "openapi",
        "auth_type": "none",
        "headers": null,
        "key": "",
        "config": {
          "enable": true,
          "function_name_filter_list": "",
          "access_grants": []
        },
        "spec_type": "url",
        "spec": "",
        "info": {
          "id": "local-tools",
          "name": "Local Tools (MCPO)",
          "description": "bash, read/write/edit files, grep, fetch, agent management"
        }
      }]
    }' > /dev/null
  echo "  Tool server configured."
else
  echo "  Tool server already configured."
fi

# Resolve the MCPO server's id so models can reference it in meta.toolIds
# ("server:<id>") — that's what attaches the tools to every chat by default
# instead of requiring the per-conversation ＋-menu toggle.
MCPO_SERVER_ID=$(curl -s -H "$AUTH" "$WEBUI_URL/api/v1/configs/tool_servers" 2>/dev/null | python3 -c "
import sys, json
for c in json.load(sys.stdin).get('TOOL_SERVER_CONNECTIONS', []):
    if c.get('url') == '$MCPO_URL':
        print(c.get('info', {}).get('id', ''))
        break" 2>/dev/null)

# --- 2. Set native function calling for all models ---
# Wait briefly for Open WebUI to detect models from llama.cpp
sleep 3

MODELS=$(curl -s -H "$AUTH" "$WEBUI_URL/api/models" 2>/dev/null \
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
" 2>/dev/null)

if [[ -z "$MODELS" ]]; then
  echo "  No models detected yet. Re-run ./configure-webui.sh after first chat."
else
  # Write system prompt to a temp file for the DB update script to read
  PROMPT_FILE=""
  if [[ -n "$SYSTEM_PROMPT" ]]; then
    PROMPT_FILE=$(mktemp)
    echo "$SYSTEM_PROMPT" > "$PROMPT_FILE"
    docker cp "$PROMPT_FILE" open-webui:/tmp/system-prompt.txt > /dev/null 2>&1
    rm -f "$PROMPT_FILE"
  fi

  while IFS='|' read -r model_id fc_mode; do
    [[ -z "$model_id" ]] && continue
    echo "  Configuring $model_id..."
    docker exec open-webui python3 -c "
import sqlite3, json, os, time

db = sqlite3.connect('/app/backend/data/webui.db')
model_id = '$model_id'
tool_ref = 'server:$MCPO_SERVER_ID' if '$MCPO_SERVER_ID' else ''

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

    # Attach the MCPO tool server by default (no per-chat toggle needed)
    if tool_ref and tool_ref not in meta.get('toolIds', []):
        meta['toolIds'] = meta.get('toolIds', []) + [tool_ref]
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
    if tool_ref:
        meta_dict['toolIds'] = [tool_ref]
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
" 2>/dev/null
  done <<< "$MODELS"
fi

echo "Open WebUI configured."
