#!/bin/bash
# Configure Open WebUI for the local AI stack.
# Idempotent — safe to run multiple times.
#
# Sets up:
#   1. MCPO tool server connection (OpenAPI on localhost:3001)
#   2. Native function calling for all detected models
#
# Called automatically by start.sh after Open WebUI is ready.

set -euo pipefail

WEBUI_URL="${WEBUI_URL:-http://localhost:3000}"
MCPO_URL="${MCPO_URL:-http://localhost:3001}"

echo "Configuring Open WebUI..."

# Wait for Open WebUI to be ready
until curl -s "$WEBUI_URL/api/version" > /dev/null 2>&1; do
  sleep 1
done

# Get admin token (works when WEBUI_AUTH=false with default admin user)
TOKEN=$(curl -s "$WEBUI_URL/api/v1/auths/signin" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@localhost","password":""}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "Warning: Could not get admin token. Configure Open WebUI manually:" >&2
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
          "description": "bash, read_file, write_file, list_files, grep"
        }
      }]
    }' > /dev/null
  echo "  Tool server configured."
else
  echo "  Tool server already configured."
fi

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
  while IFS='|' read -r model_id fc_mode; do
    [[ -z "$model_id" ]] && continue
    if [[ "$fc_mode" != "native" ]]; then
      echo "  Setting native function calling for $model_id..."
      # Update via direct DB since the API model update requires the full model payload
      docker exec open-webui python3 -c "
import sqlite3, json
db = sqlite3.connect('/app/backend/data/webui.db')
row = db.execute('SELECT params FROM model WHERE id=?', ('$model_id',)).fetchone()
if row:
    params = json.loads(row[0]) if row[0] else {}
    params['function_calling'] = 'native'
    db.execute('UPDATE model SET params=? WHERE id=?', (json.dumps(params), '$model_id'))
    db.commit()
    print('    Done.')
else:
    # Model detected by API but not yet in DB — insert it
    import time
    params = json.dumps({'function_calling': 'native'})
    meta = json.dumps({'profile_image_url': '/static/favicon.png', 'description': None, 'capabilities': {'vision': True, 'citations': True}})
    now = int(time.time())
    db.execute(
        'INSERT INTO model (id, user_id, name, meta, params, created_at, updated_at, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        ('$model_id', 'system', '$model_id', meta, params, now, now, 1)
    )
    db.commit()
    print('    Created model entry with native FC.')
" 2>/dev/null
    else
      echo "  $model_id: native function calling already set."
    fi
  done <<< "$MODELS"
fi

echo "Open WebUI configured."
