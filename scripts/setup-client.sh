#!/usr/bin/env bash
# OpenBeast client mode — full local tool stack, the rig does the thinking.
# (docs/BEAST_SLOT.md; macOS + Linux. Windows/WSL2 untested.)
#
#   ./scripts/setup-client.sh [--host <rig-fqdn>] [--api-key <key>]
#                             [--no-search | --local-search] [--uninstall]
#
# What you get: OpenCode on THIS machine with the full OpenBeast tool
# arsenal running LOCALLY (bash/edit_file act on this machine's files, via a
# stdio MCP subprocess that dies with OpenCode — no daemon, no open port),
# while INFERENCE comes from your rig's beast-slot over the tailnet:
#
#   client: opencode + mcp_server.py ── HTTPS :8443 ─▶ rig llama-server
#   client: web_search ────────────── HTTPS :8889 ─▶ rig SearXNG (default)
#                              or ── 127.0.0.1:8888 ─▶ local SearXNG (--local-search)
#   client: client.sh status ──────── HTTPS :8444 ─▶ rig beast-slot API
#            (rig side: ./scripts/setup-tailscale.sh --publish-searxng --publish-slot)
#
# Data flow, stated plainly: file contents the agent READS on this machine
# are sent to the rig as model context — the model must see data to reason
# about it. Both machines are on your tailnet, so the promise is "nothing
# leaves your tailnet", not "nothing leaves this machine".
#
# Flags:
#   --host <fqdn>    rig's tailnet FQDN (default: auto-detect a peer named 'beast')
#   --api-key <key>  the rig's LLAMA_API_KEY (also read from $OPENBEAST_API_KEY);
#                    wired into the env file + opencode.json (chmod 600)
#   --no-search      skip search wiring (web_search disabled on the client)
#   --local-search   run SearXNG locally via Docker (bridge network — works on
#                    Docker Desktop) instead of using the rig's :8889
#   --uninstall      remove ~/.openbeast-client, the env file, the local
#                    SearXNG container, and our opencode.json entries
#
# After install, the client CLI lives at scripts/client.sh in the checkout
# (symlinked to ~/.local/bin/openbeast-client when that dir exists):
#   openbeast-client status|agent|search|update|uninstall
#
# Bash 3.2-compatible on purpose (stock macOS ships it): avoids all
# bash-4-only builtins; tests/test_scripts.sh §13 enforces this.
set -euo pipefail

CLIENT_DIR="$HOME/.openbeast-client"
ENV_FILE="$HOME/.openbeast-client.env"
OC_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
OC_CONFIG="$OC_CONFIG_DIR/opencode.json"
REPO_URL="https://github.com/MaximilianKhan/openbeast"

HOST_FQDN=""; NO_SEARCH=0; LOCAL_SEARCH=0; UNINSTALL=0
API_KEY="${OPENBEAST_API_KEY:-}"
# Re-running without --api-key must not silently de-key a keyed install:
# fall back to the key already stored by a previous run (same treatment the
# client SearXNG secret gets below). Pass --api-key "" to deliberately clear.
if [ -z "$API_KEY" ] && [ -f "$HOME/.openbeast-client.env" ]; then
  API_KEY="$(sed -n "s/^OPENBEAST_API_KEY='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" \
    "$HOME/.openbeast-client.env" 2>/dev/null | tail -1)"
fi
while [ $# -gt 0 ]; do
  case "$1" in
    --host)         HOST_FQDN="${2:?--host needs a value}"; shift ;;
    --api-key)      API_KEY="${2:?--api-key needs a value}"; shift ;;
    --no-search)    NO_SEARCH=1 ;;
    --local-search) LOCAL_SEARCH=1 ;;
    --uninstall)    UNINSTALL=1 ;;
    -h|--help)      sed -n '2,39p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
if [ $NO_SEARCH -eq 1 ] && [ $LOCAL_SEARCH -eq 1 ]; then
  echo "--no-search and --local-search are mutually exclusive" >&2; exit 2
fi

# ---- uninstall --------------------------------------------------------------
if [ $UNINSTALL -eq 1 ]; then
  # Local SearXNG container (best-effort — only if docker + compose file exist).
  if command -v docker >/dev/null 2>&1; then
    for _repo in "$CLIENT_DIR/repo" "$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"; do
      if [ -n "$_repo" ] && [ -f "$_repo/scripts/client-searxng.compose.yml" ]; then
        OPENBEAST_SEARXNG_SECRET="${OPENBEAST_SEARXNG_SECRET:-x}" \
          docker compose -p openbeast-client -f "$_repo/scripts/client-searxng.compose.yml" \
          down >/dev/null 2>&1 && echo "  stopped local SearXNG (openbeast-client)" || true
        break
      fi
    done
  fi
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
  [ -L "$HOME/.local/bin/openbeast-client" ] && rm -f "$HOME/.local/bin/openbeast-client" \
    && echo "  removed ~/.local/bin/openbeast-client"
  rm -rf "$CLIENT_DIR"
  rm -f "$ENV_FILE"
  echo "  removed $CLIENT_DIR and $ENV_FILE"
  echo "Client mode uninstalled."
  exit 0
fi

echo "=== OpenBeast client mode setup ==="

# ---- 1. preflight (read-only) ----------------------------------------------
fail=0
OS="$(uname -s)"
case "$OS" in
  Darwin) echo "  ✓ macOS" ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then
      echo "  ! WSL detected — untested territory, proceeding at your own risk"
    else
      echo "  ✓ Linux"
    fi ;;
  *) echo "  ! $OS — untested territory, proceeding at your own risk" ;;
esac
py_ok="$(python3 -c 'import sys; print("yes" if sys.version_info >= (3,10) else "no")' 2>/dev/null || echo no)"
[ "$py_ok" = "yes" ] && echo "  ✓ python3 ≥3.10" || { echo "  ✗ python3 ≥3.10 required"; fail=1; }
if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
  echo "  ✓ tailscale up"
else
  echo "  ✗ tailscale not running — install + sign in first (tailscale.com/download)"; fail=1
fi
command -v opencode >/dev/null 2>&1 && echo "  ✓ opencode" \
  || echo "  ! opencode not found — config will be written; install it from opencode.ai"
if [ $LOCAL_SEARCH -eq 1 ]; then
  if docker compose version >/dev/null 2>&1; then
    echo "  ✓ docker compose (for --local-search)"
  else
    echo "  ✗ --local-search needs Docker (Desktop on macOS) with the compose plugin"; fail=1
  fi
fi

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
SLOT_URL="https://$HOST_FQDN:8444/api/slot"
if [ $LOCAL_SEARCH -eq 1 ]; then
  SEARCH_URL="http://127.0.0.1:8888"
else
  SEARCH_URL="https://$HOST_FQDN:8889"
fi

# -f (fail on 4xx/5xx) + a body match: without them a tailscale-serve 502
# (published port, stack down) reports as "reachable".
if [ -n "$API_KEY" ]; then
  probe_ok="$(curl -fsS -m 5 -H "Authorization: Bearer $API_KEY" "https://$HOST_FQDN:8443/health" 2>/dev/null | grep -qi 'ok' && echo yes || echo no)"
else
  probe_ok="$(curl -fsS -m 5 "https://$HOST_FQDN:8443/health" 2>/dev/null | grep -qi 'ok' && echo yes || echo no)"
fi
[ "$probe_ok" = "yes" ] && echo "  ✓ rig model API reachable ($API_URL)" \
  || echo "  ! rig model API not answering ($API_URL) — is the stack up? Wiring anyway."
# beast-slot discovery (informational — tells you what the rig has loaded).
SLOT_INFO="$(curl -s -m 5 "$SLOT_URL" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    m, s = d.get("model") or {}, d.get("slots") or {}
    print("model %s, slots %s/%s busy, ctx %s" % (
        m.get("id") or "?", s.get("busy", "?"),
        s.get("total", "?"), m.get("ctx") or "?"))
except Exception:
    pass' || true)"
if [ -n "$SLOT_INFO" ]; then
  echo "  ✓ beast-slot: $SLOT_INFO"
else
  echo "  ! beast-slot status not published (optional) — on the rig:"
  echo "      ./scripts/setup-tailscale.sh --publish-slot"
fi
if [ $NO_SEARCH -eq 0 ] && [ $LOCAL_SEARCH -eq 0 ]; then
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
    git -C "$CLIENT_REPO" sparse-checkout set agents skills scripts searxng >/dev/null 2>&1 || true
    git -C "$CLIENT_REPO" pull --ff-only >/dev/null 2>&1 || true
    echo "  ✓ slim checkout refreshed ($CLIENT_REPO)"
  else
    mkdir -p "$CLIENT_DIR"
    git clone --depth 1 --filter=blob:none --sparse "$REPO_URL" "$CLIENT_REPO" >/dev/null 2>&1
    # scripts/ + searxng/ ride along for client.sh, the local-search compose
    # variant, and its settings.yml. conf.sh stays UNUSED on a client (it is
    # rig-shaped: it would generate a SearXNG secret into openbeast.conf).
    git -C "$CLIENT_REPO" sparse-checkout set agents skills scripts searxng >/dev/null 2>&1
    echo "  ✓ slim checkout created (agents/ skills/ scripts/ searxng/ → $CLIENT_REPO)"
  fi
fi

# ---- 3. isolated venv with the pinned deps ----------------------------------
VENV="$CLIENT_DIR/venv"
mkdir -p "$CLIENT_DIR"
[ -x "$VENV/bin/python3" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -r "$CLIENT_REPO/agents/requirements.txt"
"$VENV/bin/python3" -c "import mcp, openai" || { echo "  ✗ venv deps failed to import"; exit 1; }
echo "  ✓ venv ready ($VENV, pins from agents/requirements.txt)"

# ---- 4. env file (sourced by scripts/client.sh) -----------------------------
umask 077
SEARXNG_CLIENT_SECRET=""
if [ $LOCAL_SEARCH -eq 1 ]; then
  # Per-install secret, never shipped — same idiom as the rig's conf.sh.
  # Reuse an existing one across re-runs so the container keeps its sessions.
  if [ -f "$ENV_FILE" ]; then
    SEARXNG_CLIENT_SECRET="$(sed -n "s/^OPENBEAST_SEARXNG_SECRET='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" \
      "$ENV_FILE" 2>/dev/null | tail -1)"
  fi
  if [ -z "$SEARXNG_CLIENT_SECRET" ]; then
    SEARXNG_CLIENT_SECRET="$(openssl rand -hex 32 2>/dev/null \
      || od -vN32 -An -tx1 /dev/urandom | tr -d ' \n')"
  fi
fi
# Values are single-quoted: this file is SOURCED by client.sh, so an
# unquoted key containing shell metacharacters would execute or break every
# client command. (Embedded single quotes are escaped the POSIX way.)
_q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
{
  echo "# OpenBeast client mode — written by setup-client.sh (re-run to refresh)."
  echo "# Sourced by scripts/client.sh; OpenCode gets its copy via the"
  echo "# environment block in opencode.json."
  echo "OPENBEAST_AGENT_INFERENCE_URL=$(_q "$API_URL")"
  echo "OPENBEAST_SLOT_URL=$(_q "$SLOT_URL")"
  [ $NO_SEARCH -eq 0 ] && echo "SEARXNG_URL=$(_q "$SEARCH_URL")"
  if [ -n "$API_KEY" ]; then
    echo "OPENBEAST_API_KEY=$(_q "$API_KEY")"
    echo "OPENAI_API_KEY=$(_q "$API_KEY")"
  else
    echo "# If the rig sets LLAMA_API_KEY, re-run with --api-key <key>."
  fi
  [ -n "$SEARXNG_CLIENT_SECRET" ] && echo "OPENBEAST_SEARXNG_SECRET=$(_q "$SEARXNG_CLIENT_SECRET")"
} > "$ENV_FILE"
echo "  ✓ wrote $ENV_FILE"

# ---- 4b. local SearXNG (--local-search) -------------------------------------
if [ $LOCAL_SEARCH -eq 1 ]; then
  OPENBEAST_SEARXNG_SECRET="$SEARXNG_CLIENT_SECRET" \
    docker compose -p openbeast-client \
    -f "$CLIENT_REPO/scripts/client-searxng.compose.yml" up -d
  echo "  ✓ local SearXNG up (127.0.0.1:8888, project openbeast-client)"
fi

# ---- 5. merge opencode.json (never clobber user config) ---------------------
mkdir -p "$OC_CONFIG_DIR"
[ -f "$OC_CONFIG" ] || echo '{}' > "$OC_CONFIG"
NO_SEARCH="$NO_SEARCH" API_KEY="$API_KEY" \
  python3 - "$OC_CONFIG" "$CLIENT_REPO" "$VENV" "$API_URL" "$SEARCH_URL" "$HOST_FQDN" <<'PYEOF'
import json, os, sys
oc_path, repo, venv, api_url, search_url, host = sys.argv[1:7]
no_search = os.environ.get("NO_SEARCH") == "1"
api_key = os.environ.get("API_KEY") or ""
cfg = json.load(open(oc_path))
cfg.setdefault("$schema", "https://opencode.ai/config.json")

env = {"OPENBEAST_AGENT_INFERENCE_URL": api_url}
if not no_search:
    env["SEARXNG_URL"] = search_url
if api_key:
    # The MCP subprocess spawns agents that call the keyed rig; runner.py
    # resolves OPENBEAST_API_KEY from env. A literal (not {env:...}) is
    # deliberate: OpenCode 1.18.x doesn't substitute env refs in provider
    # apiKey (upstream #27853/#19946), and this file is chmod 600 below.
    env["OPENBEAST_API_KEY"] = api_key
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
    "options": {"baseURL": api_url, "apiKey": api_key or "not-needed"},
    "models": models,
}
json.dump(cfg, open(oc_path, "w"), indent=2); open(oc_path, "a").write("\n")
if api_key:
    os.chmod(oc_path, 0o600)
    print(f"  ✓ merged provider + MCP config into {oc_path} (chmod 600 — holds the key)")
else:
    print(f"  ✓ merged provider + MCP config into {oc_path}")
PYEOF

# ---- 5b. client CLI symlink -------------------------------------------------
if [ -d "$HOME/.local/bin" ]; then
  ln -sf "$CLIENT_REPO/scripts/client.sh" "$HOME/.local/bin/openbeast-client"
  echo "  ✓ symlinked ~/.local/bin/openbeast-client"
fi

# ---- 6. report --------------------------------------------------------------
echo ""
echo "Client mode ready. Use it:"
echo "  cd <any project> && opencode     # pick a 'llama-cpp' model"
echo "  $CLIENT_REPO/scripts/client.sh status   # client doctor + beast-slot view"
echo ""
echo "  • bash/read/write/edit act on THIS machine's files"
echo "  • the model runs on the rig ($HOST_FQDN) — start the one you want there first"
if [ $LOCAL_SEARCH -eq 1 ]; then
  echo "  • web_search uses the LOCAL SearXNG container (openbeast-client)"
elif [ $NO_SEARCH -eq 0 ]; then
  echo "  • web_search uses the rig's private SearXNG"
fi
[ -n "$API_KEY" ] && echo "  • keyed mode: requests carry the bearer; env + opencode.json are 0600"
echo "  • quitting OpenCode reaps the tool subprocess — nothing keeps running"
echo ""
echo "Uninstall any time:  $0 --uninstall"
