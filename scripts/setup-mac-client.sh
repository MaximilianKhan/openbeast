#!/usr/bin/env bash
# OpenBeast client mode — thin client on a laptop, the rig does the thinking.
# (docs/MAC_CLIENT_PLAN.md; macOS-first, fine on any Linux laptop.)
#
#   ./scripts/setup-mac-client.sh [--host <rig-fqdn>] [--no-search] [--uninstall]
#
# What you get: OpenCode on THIS machine with the full OpenBeast tool
# arsenal running LOCALLY (bash/edit_file act on this laptop's files, via a
# stdio MCP subprocess that dies with OpenCode — no daemon, no open port),
# while INFERENCE and WEB SEARCH come from your rig over the tailnet:
#
#   laptop: opencode + mcp_server.py ── HTTPS :8443 ─▶ rig llama-server
#   laptop: web_search ────────────── HTTPS :8889 ─▶ rig SearXNG
#            (rig side needs: ./scripts/setup-tailscale.sh --publish-searxng)
#
# Data flow, stated plainly: file contents the agent READS on this laptop
# are sent to the rig as model context — the model must see data to reason
# about it. Both machines are on your tailnet, so the promise is "nothing
# leaves your tailnet", not "nothing leaves this machine".
#
# Flags:
#   --host <fqdn>   rig's tailnet FQDN (default: auto-detect a peer named 'beast')
#   --no-search     skip SEARXNG_URL wiring (web_search disabled on the client)
#   --uninstall     remove ~/.openbeast-client, the env file, and our
#                   opencode.json entries
#
# Bash 3.2-compatible on purpose (stock macOS ships it): avoids all
# bash-4-only builtins; tests/test_scripts.sh §13 enforces this.
set -euo pipefail

CLIENT_DIR="$HOME/.openbeast-client"
ENV_FILE="$HOME/.openbeast-client.env"
OC_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
OC_CONFIG="$OC_CONFIG_DIR/opencode.json"
REPO_URL="https://github.com/MaximilianKhan/openbeast"

HOST_FQDN=""; NO_SEARCH=0; UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --host)       HOST_FQDN="${2:?--host needs a value}"; shift ;;
    --no-search)  NO_SEARCH=1 ;;
    --uninstall)  UNINSTALL=1 ;;
    -h|--help)    sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

# ---- uninstall --------------------------------------------------------------
if [ $UNINSTALL -eq 1 ]; then
  if [ -f "$OC_CONFIG" ]; then
    python3 - "$OC_CONFIG" <<'PYEOF'
import json, sys
path = sys.argv[1]
cfg = json.load(open(path))
removed = []
mcp = cfg.get("mcp", {})
lt = mcp.get("local-tools", {})
if ".openbeast-client" in " ".join(lt.get("command", [])):
    del mcp["local-tools"]; removed.append("mcp.local-tools")
prov = cfg.get("provider", {})
if prov.get("llama-cpp", {}).get("options", {}).get("baseURL", "").endswith(":8443/v1"):
    del prov["llama-cpp"]; removed.append("provider.llama-cpp")
json.dump(cfg, open(path, "w"), indent=2); open(path, "a").write("\n")
print("  removed from opencode.json: " + (", ".join(removed) or "nothing (no entries of ours)"))
PYEOF
  fi
  rm -rf "$CLIENT_DIR"
  rm -f "$ENV_FILE"
  echo "  removed $CLIENT_DIR and $ENV_FILE"
  echo "Client mode uninstalled."
  exit 0
fi

echo "=== OpenBeast client mode setup ==="

# ---- 1. preflight (read-only) ----------------------------------------------
fail=0
py_ok="$(python3 -c 'import sys; print("yes" if sys.version_info >= (3,10) else "no")' 2>/dev/null || echo no)"
[ "$py_ok" = "yes" ] && echo "  ✓ python3 ≥3.10" || { echo "  ✗ python3 ≥3.10 required"; fail=1; }
if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
  echo "  ✓ tailscale up"
else
  echo "  ✗ tailscale not running — install + sign in first (tailscale.com/download)"; fail=1
fi
command -v opencode >/dev/null 2>&1 && echo "  ✓ opencode" \
  || echo "  ! opencode not found — config will be written; install it from opencode.ai"

if [ -z "$HOST_FQDN" ] && [ $fail -eq 0 ]; then
  HOST_FQDN="$(tailscale status --json 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
for p in (d.get("Peer") or {}).values():
    dns = (p.get("DNSName") or "").rstrip(".")
    if dns.split(".")[0] == "beast":
        print(dns); break
' || true)"
  [ -n "$HOST_FQDN" ] && echo "  ✓ rig auto-detected: $HOST_FQDN" \
    || { echo "  ✗ no tailnet peer named 'beast' — pass --host <rig-fqdn>"; fail=1; }
fi
[ $fail -eq 0 ] || { echo "Preflight failed — nothing was changed."; exit 1; }

API_URL="https://$HOST_FQDN:8443/v1"
SEARCH_URL="https://$HOST_FQDN:8889"
if curl -s -m 5 "https://$HOST_FQDN:8443/health" >/dev/null 2>&1; then
  echo "  ✓ rig model API reachable ($API_URL)"
else
  echo "  ! rig model API not answering ($API_URL) — is the stack up? Wiring anyway."
fi
if [ $NO_SEARCH -eq 0 ]; then
  if curl -s -m 5 "$SEARCH_URL/search?q=test&format=json" 2>/dev/null | grep -q '"results"'; then
    echo "  ✓ rig search reachable ($SEARCH_URL)"
  else
    echo "  ! rig search not answering ($SEARCH_URL) — on the rig, run:"
    echo "      ./scripts/setup-tailscale.sh --publish-searxng"
    echo "    (wiring the URL anyway; web_search will work once published)"
  fi
fi

# ---- 2. slim checkout (or use the clone we're inside) -----------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/../agents/tools.py" ]; then
  CLIENT_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
  echo "  ✓ running inside a full clone — using it in place: $CLIENT_REPO"
else
  CLIENT_REPO="$CLIENT_DIR/repo"
  command -v git >/dev/null 2>&1 || { echo "  ✗ git required for the slim checkout"; exit 1; }
  if [ -d "$CLIENT_REPO/.git" ]; then
    git -C "$CLIENT_REPO" pull --ff-only >/dev/null 2>&1 || true
    echo "  ✓ slim checkout refreshed ($CLIENT_REPO)"
  else
    mkdir -p "$CLIENT_DIR"
    git clone --depth 1 --filter=blob:none --sparse "$REPO_URL" "$CLIENT_REPO" >/dev/null 2>&1
    git -C "$CLIENT_REPO" sparse-checkout set agents skills >/dev/null 2>&1
    echo "  ✓ slim checkout created (agents/ + skills/ → $CLIENT_REPO)"
  fi
fi

# ---- 3. isolated venv with the pinned deps ----------------------------------
VENV="$CLIENT_DIR/venv"
mkdir -p "$CLIENT_DIR"
[ -x "$VENV/bin/python3" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -r "$CLIENT_REPO/agents/requirements.txt"
"$VENV/bin/python3" -c "import mcp, openai" || { echo "  ✗ venv deps failed to import"; exit 1; }
echo "  ✓ venv ready ($VENV, pins from agents/requirements.txt)"

# ---- 4. env file ------------------------------------------------------------
umask 077
{
  echo "# OpenBeast client mode — written by setup-mac-client.sh (re-run to refresh)"
  echo "OPENBEAST_AGENT_INFERENCE_URL=$API_URL"
  [ $NO_SEARCH -eq 0 ] && echo "SEARXNG_URL=$SEARCH_URL"
  echo "# If the rig sets LLAMA_API_KEY, uncomment and mirror it here:"
  echo "#OPENAI_API_KEY="
} > "$ENV_FILE"
echo "  ✓ wrote $ENV_FILE"

# ---- 5. merge opencode.json (never clobber user config) ---------------------
mkdir -p "$OC_CONFIG_DIR"
[ -f "$OC_CONFIG" ] || echo '{}' > "$OC_CONFIG"
NO_SEARCH="$NO_SEARCH" python3 - "$OC_CONFIG" "$CLIENT_REPO" "$VENV" "$API_URL" "$SEARCH_URL" "$HOST_FQDN" <<'PYEOF'
import json, os, sys
oc_path, repo, venv, api_url, search_url, host = sys.argv[1:7]
no_search = os.environ.get("NO_SEARCH") == "1"
cfg = json.load(open(oc_path))
cfg.setdefault("$schema", "https://opencode.ai/config.json")

env = {"OPENBEAST_AGENT_INFERENCE_URL": api_url}
if not no_search:
    env["SEARXNG_URL"] = search_url
cfg.setdefault("mcp", {})["local-tools"] = {
    "type": "local",
    "command": [os.path.join(venv, "bin", "python3"),
                os.path.join(repo, "agents", "mcp_server.py")],
    "enabled": True,
    "environment": env,
}

# Model list: copy from the checkout's opencode.json (kept current with the
# rig's serve scripts); fall back to the default model alone.
models = {"qwen-27b-uncensored-q5": {"name": "Qwen3.6-27B Uncensored (default)"}}
try:
    models = json.load(open(os.path.join(repo, "opencode.json")))["provider"]["llama-cpp"]["models"]
except Exception:
    pass
cfg.setdefault("provider", {})["llama-cpp"] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": f"OpenBeast rig ({host})",
    "options": {"baseURL": api_url, "apiKey": "not-needed"},
    "models": models,
}
json.dump(cfg, open(oc_path, "w"), indent=2); open(oc_path, "a").write("\n")
print(f"  ✓ merged provider + MCP config into {oc_path}")
PYEOF

# ---- 6. report --------------------------------------------------------------
echo ""
echo "Client mode ready. Use it:"
echo "  cd <any project> && opencode     # pick a 'llama-cpp' model"
echo ""
echo "  • bash/read/write/edit act on THIS machine's files"
echo "  • the model runs on the rig ($HOST_FQDN) — start the one you want there first"
[ $NO_SEARCH -eq 0 ] && echo "  • web_search uses the rig's private SearXNG"
echo "  • quitting OpenCode reaps the tool subprocess — nothing keeps running"
echo ""
echo "Uninstall any time:  $0 --uninstall"
