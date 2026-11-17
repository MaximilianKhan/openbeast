#!/usr/bin/env bash
# client-e2e.sh — end-to-end check of THIS machine's client mode against the
# real active rig (docs/SPARK_HYDRA.md § F2 "Testing from the Mac").
#
#   scripts/client-e2e.sh [--rig <name>] [--expect-model <id>] [--expect-slots N]
#                         [--offline-rig <name>] [--skip <a|b|c|d|e|f>]... [--keep]
#
# A few short requests, each printed as one PASS / FAIL line:
#   a  openbeast-client status is green, names the active rig (and, when
#      given, the model and the slot count beast-slot reports)
#   f  opencode accepts the written config: `opencode models` lists the
#      rig's model under openbeast-rig/ and openbeast-<rig>/, and the row's
#      limit.context is the window beast-slot reports
#   b  a non-interactive `opencode run` in a fresh temp dir: the agent must
#      create a small python file with openbeast-tools_write_file and run it
#      with openbeast-tools_bash. The file has to exist HERE with the right
#      output: the tools ran on this machine, the thinking on the rig
#   c  web_search through the rig's SearXNG (the MCP tool, called directly)
#   d  `use <offline-rig>` is refused with nothing changed, `--force` switches
#      and status fails gracefully, `use <rig>` comes back green
#   e  the openbeast-tools MCP server lists its tools
#
# Step d SWITCHES the real client config and switches it back (a trap restores
# the original rig if the script dies midway); opencode.json is backed up
# first (timestamped, 0600). No key is ever printed. Exit status = failures.
#
# Bash 3.2-compatible on purpose (stock macOS).
set -uo pipefail

_self="$0"
while [ -L "$_self" ]; do
  _l="$(readlink "$_self")"
  case "$_l" in /*) _self="$_l" ;; *) _self="$(dirname "$_self")/$_l" ;; esac
done
REPO="$(cd "$(dirname "$_self")/.." && pwd)"
CLI="$REPO/scripts/client.sh"
CLIENT_DIR="$HOME/.openbeast-client"
VENV_PY="$CLIENT_DIR/venv/bin/python3"
ENV_FILE="$HOME/.openbeast-client.env"
OC_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.json"

RIG=""; EXPECT_MODEL=""; EXPECT_SLOTS=""; OFFLINE_RIG=""; SKIP=" "; KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rig)           RIG="${2:?}"; shift ;;
    --expect-model)  EXPECT_MODEL="${2:?}"; shift ;;
    --expect-slots)  EXPECT_SLOTS="${2:?}"; shift ;;
    --offline-rig)   OFFLINE_RIG="${2:?}"; shift ;;
    --skip)          SKIP="$SKIP${2:?} "; shift ;;
    --keep)          KEEP=1 ;;
    -h|--help)       sed -n '2,27p' "$_self" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

PASS=0; FAIL=0
pass() { echo "PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL  $1"; FAIL=$((FAIL + 1)); }
skipped() { case "$SKIP" in *" $1 "*) echo "SKIP  ($1) by request"; return 0 ;; esac; return 1; }
has() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

[ -x "$VENV_PY" ] || { echo "no client venv at $CLIENT_DIR — run scripts/setup-client.sh first" >&2; exit 2; }
WORK="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-client-e2e.XXXXXX")"
[ "$KEEP" = 1 ] || trap 'rm -rf "$WORK"' EXIT

_active() { "$CLI" rigs --json --no-probe 2>/dev/null | "$VENV_PY" -c 'import json,sys; print(json.load(sys.stdin)["active"])' 2>/dev/null; }
ORIG="$(_active)"
[ -n "$RIG" ] || RIG="$ORIG"
echo "=== OpenBeast client e2e — rig '$RIG' (active: '${ORIG:-none}') — $(date '+%Y-%m-%d %H:%M:%S') ==="
if [ -z "$ORIG" ]; then
  fail "no rig registry (openbeast-client rigs) — is client mode installed and current?"
  exit 1
fi
if [ "$ORIG" != "$RIG" ]; then
  _o="$("$CLI" use "$RIG" 2>&1)" || { fail "could not make '$RIG' active: $_o"; exit 1; }
  ORIG="$RIG"
fi

# What beast-slot says right now (public, keyless): the reference for a and f.
SLOT_URL="$(sed -n "s/^OPENBEAST_SLOT_URL='\(.*\)'$/\1/p" "$ENV_FILE" | tail -1)"
SLOT_JSON="$(curl -s -m 8 "$SLOT_URL" 2>/dev/null || true)"
_sq() { printf '%s' "$SLOT_JSON" | "$VENV_PY" -c "import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
$1" 2>/dev/null; }
SLOT_MODEL="$(_sq 'print((d.get("model") or {}).get("id") or "")')"
SLOT_CTX="$(_sq 'print((d.get("model") or {}).get("ctx") or "")')"
SLOT_TOTAL="$(_sq 'print((d.get("slots") or {}).get("total") or "")')"
MODEL="${EXPECT_MODEL:-$SLOT_MODEL}"
EXPECT_SLOTS="${EXPECT_SLOTS:-$SLOT_TOTAL}"   # status must agree with beast-slot at least

# ---- a. status ---------------------------------------------------------------
if ! skipped a; then
  _o="$("$CLI" status 2>&1)"; _rc=$?
  _why=""
  [ $_rc -eq 0 ] || _why="$_why exit $_rc;"
  has "$_o" "active rig: $RIG " || _why="$_why active rig is not '$RIG';"
  [ -z "$EXPECT_MODEL" ] || has "$_o" "model $EXPECT_MODEL " || _why="$_why model is not $EXPECT_MODEL;"
  if [ -n "$EXPECT_SLOTS" ]; then
    printf '%s\n' "$_o" | grep -qE "slots [0-9?]+/$EXPECT_SLOTS busy" || _why="$_why slots are not $EXPECT_SLOTS;"
  fi
  _line="$(printf '%s\n' "$_o" | grep 'beast-slot:' | sed 's/^ *✓ *//')"
  if [ -z "$_why" ]; then
    pass "(a) status green — active rig $RIG — $_line"
  else
    fail "(a) status:$_why"; printf '%s\n' "$_o" | sed 's/^/      /'
  fi
fi

# ---- f. opencode accepts the config, limits from beast-slot -------------------
if ! skipped f; then
  if ! command -v opencode >/dev/null 2>&1; then
    fail "(f) opencode is not installed"
  else
    # Through the background service (`--standalone` exits before providers
    # load in v2.0.25), after a reload so it reads the file as it is now.
    # `opencode debug config` is opencode's OWN parse of the file: the limit
    # read from it proves opencode accepted the row (apiKey prints as ***;
    # only the limit is extracted here).
    (cd "$WORK" && opencode reload >/dev/null 2>&1) || true
    _o="$(cd "$WORK" && opencode models 2>&1)"; _rc=$?
    _lim="$(cd "$WORK" && opencode debug config 2>/dev/null | "$VENV_PY" -c '
import json, sys
rig, model = sys.argv[1], sys.argv[2]
out = {}
for src in json.load(sys.stdin):
    info = src.get("info") or {}
    for key in ("providers", "provider"):
        for pid in ("openbeast-rig", "openbeast-" + rig):
            row = (((info.get(key) or {}).get(pid) or {}).get("models") or {}).get(model)
            if row and "limit" in row:
                out[pid] = row["limit"]
print(" ".join("%s=%s" % (k, json.dumps(v, sort_keys=True)) for k, v in sorted(out.items())) or "none")
' "$RIG" "$MODEL" 2>&1)"
    _why=""
    [ $_rc -eq 0 ] || _why="$_why opencode models exit $_rc;"
    has "$_o" "openbeast-rig/$MODEL" || _why="$_why openbeast-rig/$MODEL not listed;"
    has "$_o" "openbeast-$RIG/$MODEL" || _why="$_why openbeast-$RIG/$MODEL not listed;"
    if [ -n "$SLOT_CTX" ]; then
      has "$_lim" "openbeast-rig={\"context\": $SLOT_CTX," || _why="$_why opencode's limit.context is not beast-slot's $SLOT_CTX ($_lim);"
    fi
    has "$_lim" '"output": ' || _why="$_why no limit.output ($_lim);"
    if [ -z "$_why" ]; then
      pass "(f) opencode lists openbeast-rig/$MODEL + openbeast-$RIG/$MODEL; its parsed limit: $_lim"
    else
      fail "(f)$_why"
    fi
  fi
fi

# ---- b. opencode run: local tools, rig inference -----------------------------
if ! skipped b; then
  NONCE="$(od -vAn -N4 -tu4 /dev/urandom | tr -d ' \n')"
  A=$(( NONCE % 97 + 3 )); B=$(( NONCE % 89 + 5 ))
  WANT="OB-E2E $NONCE $((A * B))"
  PROJ="$WORK/proj"; mkdir -p "$PROJ"
  F="$PROJ/e2e_check.py"
  PROMPT="You are in the directory $PROJ on this machine. Do exactly this, using the openbeast-tools MCP tools: (1) call openbeast-tools_write_file to create $F containing a Python 3 program that prints the text OB-E2E, a space, the number $NONCE, a space, and the product of $A and $B computed by the program; (2) call openbeast-tools_bash to run: python3 $F ; (3) reply with only the line the program printed."
  _b_ok=0
  for _try in 1 2; do
    rm -f "$F"
    _t0=$(date +%s)
    (cd "$PROJ" && opencode run --standalone --auto --format json -m "openbeast-rig/$MODEL" "$PROMPT") \
      > "$WORK/run$_try.jsonl" 2> "$WORK/run$_try.err"
    _rc=$?; _secs=$(( $(date +%s) - _t0 ))
    _sum="$("$VENV_PY" - "$WORK/run$_try.jsonl" <<'PY'
import json, sys
tools, text, errs = [], [], []
for line in open(sys.argv[1]):
    try:
        e = json.loads(line)
    except Exception:
        continue
    p = e.get("part") or {}
    if e.get("type") == "tool_use":
        tools.append("%s:%s" % (p.get("tool"), (p.get("state") or {}).get("status")))
    elif e.get("type") == "text":
        text.append(p.get("text") or "")
    elif e.get("type") == "error":
        errs.append(json.dumps(e)[:300])
print("TOOLS " + " ".join(tools))
print("TEXT " + " ".join(t.strip() for t in text).replace("\n", " ")[:300])
for x in errs:
    print("ERROR " + x)
PY
)"
    _tools="$(printf '%s\n' "$_sum" | sed -n 's/^TOOLS //p')"
    _text="$(printf '%s\n' "$_sum" | sed -n 's/^TEXT //p')"
    _got=""; [ -f "$F" ] && _got="$(python3 "$F" 2>&1 | head -1)"
    _why=""
    [ $_rc -eq 0 ] || _why="$_why opencode exit $_rc;"
    [ -f "$F" ] || _why="$_why $F was not created;"
    [ "$_got" = "$WANT" ] || _why="$_why running it here printed '$_got', want '$WANT';"
    has "$_tools" "openbeast-tools_write_file:completed" || _why="$_why openbeast-tools_write_file not used;"
    has "$_tools" "openbeast-tools_bash:completed" || _why="$_why openbeast-tools_bash not used;"
    has "$_text" "$WANT" || _why="$_why the reply does not quote the output;"
    if [ -z "$_why" ]; then
      pass "(b) opencode run (${_secs}s, try $_try): $F created here, prints '$_got'; tools: $_tools"
      _b_ok=1; break
    fi
    echo "      try $_try:$_why"
    echo "      tools: $_tools"
    echo "      reply: $_text"
    printf '%s\n' "$_sum" | grep '^ERROR' | sed 's/^/      /'
  done
  [ $_b_ok = 1 ] || fail "(b) opencode run did not prove local tools + rig inference (logs: $WORK, --keep to inspect)"
fi

# ---- c + e. the MCP server: tool list, web_search ------------------------------
if ! skipped c || ! skipped e; then
  _o="$(set -a; . "$ENV_FILE"; set +a; DO_SEARCH="$(skipped c >/dev/null && echo 0 || echo 1)" \
        "$VENV_PY" - "$REPO/agents/mcp_server.py" <<'PY' 2>&1
import asyncio, json, os, sys, time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.executable, args=[sys.argv[1]], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print("TOOLS %d %s" % (len(tools), " ".join(tools)))
            if os.environ.get("DO_SEARCH") == "1":
                t0 = time.time()
                res = await s.call_tool("web_search", {"query": "OpenBeast local AI workstation llama.cpp", "max_results": 3})
                text = " ".join(getattr(c, "text", "") for c in res.content)
                n = text.count("http")
                print("SEARCH %d %.1f %s" % (n, time.time() - t0, text[:160].replace("\n", " ")))

asyncio.run(main())
PY
)"
  _tl="$(printf '%s\n' "$_o" | sed -n 's/^TOOLS //p')"
  if ! skipped e; then
    _n="${_tl%% *}"; _why=""
    for _t in bash read_file write_file edit_file list_files grep web_search fetch; do
      has " $_tl " " $_t " || _why="$_why $_t missing;"
    done
    if [ -n "$_tl" ] && [ "${_n:-0}" -ge 10 ] && [ -z "$_why" ]; then
      pass "(e) openbeast-tools MCP server lists $_n tools: ${_tl#* }"
    else
      fail "(e) MCP tool listing:$_why $(printf '%s' "$_o" | tail -3 | tr '\n' ' ')"
    fi
  fi
  if ! skipped c; then
    _sl="$(printf '%s\n' "$_o" | sed -n 's/^SEARCH //p')"
    _sx="$(sed -n "s/^SEARXNG_URL='\(.*\)'$/\1/p" "$ENV_FILE" | tail -1)"
    _hits="${_sl%% *}"
    if [ -n "$_sl" ] && [ "${_hits:-0}" -ge 1 ] && ! has "$_sl" "rror"; then
      _rest="${_sl#* }"
      pass "(c) web_search via ${_sx:-?}: $_hits result URLs in ${_rest%% *}s"
    else
      fail "(c) web_search via ${_sx:-unset}: ${_sl:-no answer} $(printf '%s' "$_o" | grep -v '^TOOLS' | tail -2 | tr '\n' ' ')"
    fi
  fi
fi

# ---- d. switching rigs ----------------------------------------------------------
if ! skipped d; then
  if [ -z "$OFFLINE_RIG" ]; then
    OFFLINE_RIG="$("$CLI" rigs --json --no-probe 2>/dev/null | "$VENV_PY" -c '
import json, sys
d = json.load(sys.stdin)
print(next((n for n in sorted(d["rigs"]) if n != d["active"]), ""))' 2>/dev/null)"
  fi
  if [ -z "$OFFLINE_RIG" ]; then
    fail "(d) only one rig is registered — add another (openbeast-client rig add) to test switching"
  else
    _bak="$OC_CONFIG.bak-e2e-$(date +%Y%m%d-%H%M%S)"
    ( umask 077; cp -p "$OC_CONFIG" "$_bak" ) && chmod 600 "$_bak"
    trap '[ "$(_active)" = "$ORIG" ] || "$CLI" use "$ORIG" --force >/dev/null 2>&1; [ "$KEEP" = 1 ] || rm -rf "$WORK"' EXIT
    _env0="$(cksum < "$ENV_FILE")"; _oc0="$(cksum < "$OC_CONFIG")"
    _o1="$("$CLI" use "$OFFLINE_RIG" 2>&1)"; _rc1=$?
    if [ $_rc1 -eq 0 ]; then
      # The other rig is UP: a real switch, then back.
      _o2="$("$CLI" status 2>&1)"; _rc2=$?
      _o3="$("$CLI" use "$ORIG" 2>&1)"; _rc3=$?
      _o4="$("$CLI" status 2>&1)"; _rc4=$?
      if [ $_rc2 -eq 0 ] && has "$_o2" "active rig: $OFFLINE_RIG " && [ $_rc3 -eq 0 ] && [ $_rc4 -eq 0 ] \
         && has "$_o4" "active rig: $ORIG "; then
        pass "(d) '$OFFLINE_RIG' is online: use $OFFLINE_RIG -> status green -> use $ORIG -> status green"
      else
        fail "(d) online switch: status rc $_rc2, use-back rc $_rc3, status rc $_rc4"
      fi
    else
      _why=""
      [ $_rc1 -eq 1 ] || _why="$_why use exit $_rc1 (want 1);"
      has "$_o1" "active rig unchanged: $ORIG" || _why="$_why no 'unchanged' line;"
      [ "$(cksum < "$ENV_FILE")" = "$_env0" ] || _why="$_why env file changed;"
      [ "$(cksum < "$OC_CONFIG")" = "$_oc0" ] || _why="$_why opencode.json changed;"
      _o2="$("$CLI" use "$OFFLINE_RIG" --force 2>&1)"; _rc2=$?
      _o3="$("$CLI" status 2>&1)"; _rc3=$?
      [ $_rc2 -eq 0 ] || _why="$_why use --force exit $_rc2;"
      has "$_o3" "active rig: $OFFLINE_RIG " || _why="$_why status does not name $OFFLINE_RIG;"
      [ $_rc3 -ne 0 ] || _why="$_why status green on an offline rig?;"
      _o4="$("$CLI" use "$ORIG" 2>&1)"; _rc4=$?
      _o5="$("$CLI" status 2>&1)"; _rc5=$?
      [ $_rc4 -eq 0 ] || _why="$_why use $ORIG exit $_rc4;"
      { [ $_rc5 -eq 0 ] && has "$_o5" "active rig: $ORIG "; } || _why="$_why status after coming back: rc $_rc5;"
      _reason="$(printf '%s\n' "$_o1" | sed -n '1p' | sed 's/^ *x *//')"
      if [ -z "$_why" ]; then
        pass "(d) use $OFFLINE_RIG refused, nothing changed ($_reason); --force switched, status failed gracefully (rc $_rc3); use $ORIG -> green"
      else
        fail "(d) switch:$_why"
        printf '%s\n%s\n%s\n' "$_o1" "$_o2" "$_o4" | sed 's/^/      /'
      fi
    fi
    echo "      (opencode.json backup: $_bak)"
  fi
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
exit "$FAIL"
