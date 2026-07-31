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
#   --purge-logs     with --uninstall: ALSO delete agent transcripts from an
#                    in-place checkout (they hold prompts + file contents)
#
# After install, the client CLI lives at scripts/client.sh in the checkout and
# is symlinked to ~/.local/bin/openbeast-client (created if needed). If that
# dir is not on your PATH — the macOS default — the installer prints the exact
# line to add, and the full path always works:
#   openbeast-client status|agent|search|update|uninstall
#   <checkout>/scripts/client.sh status
#
# Bash 3.2-compatible on purpose (stock macOS ships it): avoids all
# bash-4-only builtins; tests/test_scripts.sh §13 enforces this.
set -euo pipefail

CLIENT_DIR="$HOME/.openbeast-client"
ENV_FILE="$HOME/.openbeast-client.env"
OC_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
OC_CONFIG="$OC_CONFIG_DIR/opencode.json"
REPO_URL="https://github.com/MaximilianKhan/openbeast"

HOST_FQDN=""; NO_SEARCH=0; LOCAL_SEARCH=0; UNINSTALL=0; PURGE_LOGS=0
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
    --api-key)      # arity check, NOT ${2:?...} — the header documents
                    # `--api-key ""` as the way to CLEAR a stored key, and a
                    # null-check would reject exactly that.
                    [ $# -ge 2 ] || { echo "--api-key needs a value (use \"\" to clear)" >&2; exit 2; }
                    API_KEY="$2"; shift ;;
    --no-search)    NO_SEARCH=1 ;;
    --local-search) LOCAL_SEARCH=1 ;;
    --uninstall)    UNINSTALL=1 ;;
    --purge-logs)   PURGE_LOGS=1 ;;
    -h|--help)      sed -n '2,39p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    status|agent|search|update)
      # Two scripts, confusingly similar names: setup-client.sh INSTALLS
      # (flags), client.sh OPERATES (subcommands). Sending someone who typed
      # a subcommand here away with "unknown option" is a dead end — point
      # them at the right script instead.
      _here="$(cd "$(dirname "$0")" && pwd)"
      echo "'$1' is a command for the client CLI, not the installer." >&2
      echo "  Run:  $_here/client.sh $*" >&2
      if [ -x "$HOME/.local/bin/openbeast-client" ]; then
        echo "  or:   openbeast-client $*" >&2
      fi
      exit 2 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
if [ $NO_SEARCH -eq 1 ] && [ $LOCAL_SEARCH -eq 1 ]; then
  echo "--no-search and --local-search are mutually exclusive" >&2; exit 2
fi


# --- interpreter + tailscale resolution (macOS-safe) -------------------------
# Resolved ONCE, before anything uses them — the uninstall path shells out to
# python too, so this cannot live inside preflight.
_find_py() {
  for _c in "${OPENBEAST_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3 python; do
    [ -n "$_c" ] || continue
    command -v "$_c" >/dev/null 2>&1 || continue
    if "$_c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      command -v "$_c"; return 0
    fi
  done
  return 1
}
_find_ts() {
  for _t in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale \
            /opt/homebrew/bin/tailscale /usr/local/bin/tailscale; do
    if command -v "$_t" >/dev/null 2>&1; then command -v "$_t"; return 0; fi
    [ -x "$_t" ] && { printf '%s\n' "$_t"; return 0; }
  done
  return 1
}
PY_BIN="$(_find_py || true)"
TS_BIN="$(_find_ts || true)"

# ---- uninstall --------------------------------------------------------------
if [ $UNINSTALL -eq 1 ]; then
  echo "=== OpenBeast client uninstall ==="
  _left_behind=""
  # Local SearXNG container (best-effort — only if docker + compose file exist).
  if command -v docker >/dev/null 2>&1; then
    for _repo in "$CLIENT_DIR/repo" "$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"; do
      if [ -n "$_repo" ] && [ -f "$_repo/scripts/client-searxng.compose.yml" ]; then
        OPENBEAST_SEARXNG_SECRET="${OPENBEAST_SEARXNG_SECRET:-x}" \
          docker compose -p openbeast-client -f "$_repo/scripts/client-searxng.compose.yml" \
          down >/dev/null 2>&1 && echo "  ✓ stopped local SearXNG (project openbeast-client)" || true
        break
      fi
    done
  fi
  if [ -f "$OC_CONFIG" ]; then
    "${PY_BIN:-python3}" - "$OC_CONFIG" <<'PYEOF'
import json, sys
path = sys.argv[1]
try:
    cfg = json.load(open(path))
except Exception as e:
    print("  ! could not parse %s (%s) — left untouched" % (path, e)); raise SystemExit
removed = []
mcp = cfg.get("mcp", {})
lt = mcp.get("local-tools", {})
# The venv always lives under ~/.openbeast-client, even for an in-place clone
# install, so this match holds for both install shapes.
if ".openbeast-client" in " ".join(lt.get("command", [])):
    del mcp["local-tools"]; removed.append("mcp.local-tools")
prov = cfg.get("provider", {})
if prov.get("llama-cpp", {}).get("options", {}).get("baseURL", "").endswith(":8443/v1"):
    del prov["llama-cpp"]; removed.append("provider.llama-cpp")
# Drop containers we emptied, so an uninstall does not leave "mcp": {} behind.
for k in ("mcp", "provider"):
    if k in cfg and not cfg[k]:
        del cfg[k]
json.dump(cfg, open(path, "w"), indent=2); open(path, "a").write("\n")
print("  ✓ opencode.json: removed " + (", ".join(removed) or "nothing (no entries of ours)"))
PYEOF
  fi
  if [ -L "$HOME/.local/bin/openbeast-client" ]; then
    rm -f "$HOME/.local/bin/openbeast-client"
    echo "  ✓ removed ~/.local/bin/openbeast-client"
  fi

  # Agent transcripts are the one artifact worth naming explicitly: they hold
  # full prompts AND the contents of every file an agent read on this machine.
  # For a slim install they sit under $CLIENT_DIR and vanish below. For an
  # IN-PLACE clone install they live in the user's own git checkout, where we
  # must not delete without being asked — but must not silently leave either.
  _checkout="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"
  _logdir="$_checkout/agents/logs"
  case "$_checkout" in
    "$CLIENT_DIR"*) : ;;   # slim install: removed with $CLIENT_DIR
    *)
      if [ -d "$_logdir" ]; then
        _n=$(find "$_logdir" -name 'agent-*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
        if [ "${_n:-0}" -gt 0 ]; then
          if [ "${PURGE_LOGS:-0}" = "1" ]; then
            # REFUSE to purge a RIG checkout. A client uninstall must never
            # destroy a server's own agent history: the two share a repo
            # layout, so --purge-logs pointed at a rig would delete months of
            # transcripts that have nothing to do with any client. Learned the
            # hard way — this exact mistake cost 6035 files during testing.
            if [ -f "$_checkout/openbeast.conf" ] || [ -d "$_checkout/weights" ] \
               || [ -e "$_checkout/.run/supervisor.pid" ]; then
              echo "  ! REFUSING to purge $_logdir"
              echo "    This checkout looks like a RIG install (openbeast.conf /"
              echo "    weights/ / a running supervisor). Those transcripts are the"
              echo "    server's, not a client's. Delete them yourself if you mean to."
              _left_behind="$_logdir"
            else
              rm -f "$_logdir"/agent-*.jsonl
              echo "  ✓ purged $_n agent transcript(s) from $_logdir"
            fi
          else
            _left_behind="$_logdir"
          fi
        fi
      fi ;;
  esac

  rm -rf "$CLIENT_DIR"
  rm -f "$ENV_FILE"
  echo "  ✓ removed $CLIENT_DIR and $ENV_FILE"
  if [ -n "$_left_behind" ]; then
    echo ""
    echo "  ! LEFT IN PLACE: $_left_behind"
    echo "    Agent transcripts contain full prompts and the contents of files"
    echo "    the agent read on this machine. They are inside your own git"
    echo "    checkout, so uninstall does not delete them by default."
    echo "    Remove them with:  $0 --uninstall --purge-logs"
  fi
  echo ""
  echo "Client mode uninstalled."
  echo "  Your checkout at $_checkout was NOT removed (it is yours — 'rm -rf' it if you want)."
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
# python3 on macOS is a minefield: /usr/bin/python3 from the Xcode Command
# Line Tools is frequently 3.9.6, which is BELOW our floor, while a perfectly
# good 3.11/3.12/3.13 sits alongside it under a versioned name. Hard-coding
# "python3" reported "not installed" on a Mac that had three usable
# interpreters. Search, and say what we found.
PY="$PY_BIN"
if [ -n "$PY" ]; then
  echo "  ✓ python $("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))') ($PY)"
else
  echo "  ✗ no python ≥3.10 found (checked python3.13/3.12/3.11/3.10/python3/python)"
  _seen="$(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:3])))' 2>/dev/null || true)"
  [ -n "$_seen" ] && echo "    python3 on PATH is $_seen — too old (macOS Xcode CLT ships 3.9)"
  echo "    macOS:  brew install python@3.12"
  echo "    or set OPENBEAST_PYTHON=/path/to/python3 and re-run"
  fail=1
fi

# The macOS Tailscale app does NOT put its CLI on PATH — the binary lives
# inside the app bundle. `command -v tailscale` therefore fails on a Mac that
# is connected and working perfectly. Look where it actually is.
TS="$TS_BIN"
if [ -n "$TS" ] && "$TS" status >/dev/null 2>&1; then
  echo "  ✓ tailscale up ($TS)"
elif [ -n "$TS" ]; then
  echo "  ✗ tailscale CLI found ($TS) but 'status' failed — is it signed in?"
  echo "    a full-tunnel VPN (NordVPN etc.) can also sever the tailnet"
  fail=1
else
  echo "  ✗ tailscale not found — install + sign in first (tailscale.com/download)"
  echo "    macOS: the App Store build hides the CLI in the app bundle;"
  echo "    this script checks there automatically, so a miss means it isn't installed"
  fail=1
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
  HOST_FQDN="$("$TS" status --json 2>/dev/null | "$PY" -c '
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
SLOT_INFO="$(curl -s -m 5 "$SLOT_URL" 2>/dev/null | "$PY" -c '
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
# Build the venv with the interpreter we VETTED, not whatever "python3"
# resolves to — otherwise a 3.9 on PATH silently creates a 3.9 venv.
[ -x "$VENV/bin/python3" ] || "$PY" -m venv "$VENV"
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

# ---- 5b. client CLI on PATH -------------------------------------------------
# Two Linux assumptions used to break this on macOS: ~/.local/bin usually does
# NOT exist there, and even when it does it is NOT on PATH by default. The old
# code silently skipped the symlink and then told the user to run
# `openbeast-client`, which of course was not a command.
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR" 2>/dev/null || true
CLI_LINKED=0
if [ -d "$BIN_DIR" ] && ln -sf "$CLIENT_REPO/scripts/client.sh" "$BIN_DIR/openbeast-client" 2>/dev/null; then
  CLI_LINKED=1
  case ":$PATH:" in
    *":$BIN_DIR:"*)
      echo "  ✓ openbeast-client on PATH ($BIN_DIR)" ;;
    *)
      # Name the RIGHT file for the user's shell. macOS defaults to zsh, whose
      # login shell reads ~/.zprofile; bash uses ~/.bash_profile.
      # Tilde is intentional: this string is DISPLAYED for the user to
      # copy-paste, and it expands in their shell, not in ours.
      # shellcheck disable=SC2088
      case "$(basename "${SHELL:-/bin/zsh}")" in
        zsh)  _rc="~/.zprofile" ;;
        bash) _rc="~/.bash_profile" ;;
        *)    _rc="your shell profile" ;;
      esac
      echo "  ! $BIN_DIR is not on your PATH — 'openbeast-client' won't resolve yet."
      echo "    Add it (then open a new terminal):"
      echo "      echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> $_rc"
      echo "    Or just use the full path, which always works:"
      echo "      $CLIENT_REPO/scripts/client.sh status" ;;
  esac
else
  echo "  ! could not create $BIN_DIR/openbeast-client — use the full path:"
  echo "      $CLIENT_REPO/scripts/client.sh status"
fi

# ---- 6. report --------------------------------------------------------------
echo ""
echo "Client mode ready. Use it:"
echo "  cd <any project> && opencode     # pick a 'llama-cpp' model"
if [ "${CLI_LINKED:-0}" = "1" ]; then
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) echo "  openbeast-client status          # client doctor + beast-slot view" ;;
    *)                      echo "  $CLIENT_REPO/scripts/client.sh status   # (until ~/.local/bin is on PATH)" ;;
  esac
else
  echo "  $CLIENT_REPO/scripts/client.sh status   # client doctor + beast-slot view"
fi
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
