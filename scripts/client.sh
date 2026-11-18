#!/usr/bin/env bash
# OpenBeast client CLI — the laptop-side companion to a rig's beast-slot.
# (docs/BEAST_SLOT.md; installed by scripts/setup-client.sh, which symlinks
# this to ~/.local/bin/openbeast-client when that dir exists.)
#
#   client.sh [status]           client doctor: env, venv, tailnet, rig
#                                endpoints, beast-slot view, opencode wiring
#   client.sh agent [args…]      run a CLI agent HERE against the rig's model
#                                (files/shell act on this machine)
#   client.sh search up|down     start/stop the local SearXNG container
#   client.sh refresh-config     re-sync the opencode model catalog only
#                                (no git pull, no dependency install)
#   client.sh autosync [install|uninstall|status|run]
#                                keep opencode in step with the rig: every 30 s
#                                (launchd on macOS, a systemd user timer on
#                                Linux) probe the active rig and re-sync the
#                                catalog when it switched models (the name and
#                                context window opencode shows). setup-client.sh
#                                installs it; status is the default
#   client.sh update [--no-pull] [--wheelhouse <dir>]
#                                refresh the checkout + pinned deps + the
#                                opencode model catalog (adds models the rig
#                                gained, drops ones it no longer serves).
#                                Offline: --no-pull after updating the checkout
#                                from a bundle, --wheelhouse (or
#                                $OPENBEAST_WHEELHOUSE) to install the pinned
#                                deps with no index contacted
#   client.sh rigs [--json] [--no-probe]
#                                every registered rig, reachability and health
#                                (each rig's :8444/api/slot), * = active
#   client.sh use <rig> [--force]
#                                switch the active rig: rewrites the env file
#                                and opencode.json atomically, refreshes the
#                                catalog. Refuses a rig that is down or rejects
#                                the key (nothing changed) unless --force
#   client.sh rig add <name> [--host <fqdn>] [--api-key-stdin | --key-file F]
#                            [--search rig|local|none] [--model ID]
#   client.sh rig remove <name> | rig detect
#                                manage the registry (keys: 0600 files, never
#                                argv); detect = register your tailnet's 'beast'
#   client.sh uninstall          remove client mode entirely
#
# Several rigs (docs/SPARK_HYDRA.md § F2): the registry is
# ${XDG_CONFIG_HOME:-~/.config}/openbeast/rigs.json, kept by
# scripts/lib/client_rigs.py. A single-rig install is migrated into it on
# first run. opencode gets one provider per rig (openbeast-<rig>) plus
# openbeast-rig, an alias of the active rig, and every sync re-copies the
# soul (system-prompt.md + system-prompt-tools.md) into its `instructions`.
#
# Deliberately does NOT source scripts/lib/conf.sh — that file is rig-shaped
# (it generates secrets into openbeast.conf). Client config lives in
# ~/.openbeast-client.env, written by setup-client.sh.
#
# Bash 3.2-compatible on purpose (stock macOS); §13 enforces this.
set -euo pipefail

# Resolve $0 through symlinks BEFORE deriving the repo root: setup-client.sh
# installs ~/.local/bin/openbeast-client as a bare symlink to this file, and an
# unresolved $0 would put REPO at ~/.local — breaking every subcommand that
# reaches into the checkout. `readlink -f` is GNU-only (stock macOS lacks it),
# so walk the chain by hand.
_self="$0"
while [ -L "$_self" ]; do
  _link="$(readlink "$_self")"
  case "$_link" in
    /*) _self="$_link" ;;
    *)  _self="$(dirname "$_self")/$_link" ;;
  esac
done
REPO="$(cd "$(dirname "$_self")/.." && pwd)"
CLIENT_DIR="$HOME/.openbeast-client"
ENV_FILE="$HOME/.openbeast-client.env"
VENV="$CLIENT_DIR/venv"
OC_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.json"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
fi


# macOS-safe resolution (see scripts/setup-client.sh for the full rationale):
# the Tailscale app hides its CLI inside the app bundle, and /usr/bin/python3
# from the Xcode CLT is often 3.9 — below our floor.
_find_ts() {
  for _t in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale \
            /opt/homebrew/bin/tailscale /usr/local/bin/tailscale; do
    if command -v "$_t" >/dev/null 2>&1; then command -v "$_t"; return 0; fi
    [ -x "$_t" ] && { printf '%s\n' "$_t"; return 0; }
  done
  return 1
}
TS_BIN="$(_find_ts || true)"
# Prefer the venv interpreter. setup-client.sh builds it with a vetted >=3.11
# one and rebuilds a venv an older install made with 3.10; `update` refuses
# such a venv (_venv_floor) rather than die inside pip.
if [ -x "$VENV/bin/python3" ]; then PY_BIN="$VENV/bin/python3"
else PY_BIN="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)"; fi

# The rig registry (several rigs, one active): scripts/lib/client_rigs.py.
RIGS_PY="$REPO/scripts/lib/client_rigs.py"
RIG_REGISTRY="${XDG_CONFIG_HOME:-$HOME/.config}/openbeast/rigs.json"
_rigs() { OB_TS_BIN="$TS_BIN" OB_REPO="$REPO" "$PY_BIN" "$RIGS_PY" "$@"; }

# Re-sync the opencode model catalog from the checkout into the installed
# config. WHY THIS EXISTS: setup-client.sh copies the model list ONCE at install
# time, and `update` used to pull source + deps and stop — so the catalog froze
# on install day. A model added to the rig never appeared in the client picker,
# and models whose weights were later deleted never left it. Both were live on
# 2026-08-19.
#
# Deliberately surgical: it replaces provider.openbeast-rig.models and nothing
# else. baseURL, apiKey, the openbeast-tools MCP block and any config of the
# user's own are left exactly as they were — this runs on every update, so it
# must never be able to clobber a working install (or leak the key by widening
# the file mode).
# autosync's job runs from a copy of the few files it reads, under
# $CLIENT_DIR/autosync, never from the checkout: macOS denies a launchd agent
# access to ~/Documents (where checkouts usually live) without Full Disk
# Access, and every run failed with "Operation not permitted" (2026-11-18).
# The copy mirrors the repo layout so OB_REPO can point at it. Refreshed by
# `autosync install` and by every catalog sync while autosync is installed.
AS_SNAP="$CLIENT_DIR/autosync"
AS_FILES="scripts/lib/client_rigs.py opencode.json system-prompt.md system-prompt-tools.md"
_as_snapshot() {
  mkdir -p "$AS_SNAP/scripts/lib" && chmod 700 "$CLIENT_DIR" "$AS_SNAP" 2>/dev/null
  for _f in $AS_FILES; do
    [ -f "$REPO/$_f" ] || continue
    cp "$REPO/$_f" "$AS_SNAP/$_f.new" && mv -f "$AS_SNAP/$_f.new" "$AS_SNAP/$_f" || return 1
  done
}

_refresh_oc_catalog() {
  [ -d "$AS_SNAP" ] && { _as_snapshot || echo "  ! could not refresh the autosync copy in $AS_SNAP"; }
  [ -f "$OC_CONFIG" ] || { echo "  ! no opencode config at $OC_CONFIG — run scripts/setup-client.sh"; return 0; }
  _rp="$REPO/scripts/lib/client_rigs.py"
  if [ ! -f "$_rp" ]; then
    echo "  ! $_rp missing (partial checkout?) — catalog unchanged"; return 0
  fi
  if [ -f "$RIG_REGISTRY" ]; then
    # Every registered rig is probed (in parallel); each gets its own
    # provider, openbeast-rig mirrors the active one (scripts/lib/client_rigs.py).
    _rigs sync || echo "  ! catalog refresh failed (config left untouched)"
  else
    # Driven on its own (no registry context): the openbeast-rig provider's
    # rows only, from its own baseURL/key; nothing but $OC_CONFIG is touched.
    "$PY_BIN" "$_rp" sync-legacy --oc "$OC_CONFIG" || echo "  ! catalog refresh failed (config left untouched)"
  fi
}

# autosync's job definitions carry paths ($HOME, the venv): escaped for the
# format they land in, so an & or < in a path cannot break the plist and a
# space or % cannot split or expand a systemd value.
_xml_esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g' -e "s/'/\&apos;/g"; }
_sd_q() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/%/%%/g')"; }
_sd_path() { printf '%s' "$1" | sed -e 's/%/%%/g'; }

CMD="${1:-status}"
[ $# -gt 0 ] && shift

# First run after the multi-rig update: fold the single-rig env file into the
# registry (named after its key file or host). Quiet, best-effort, never fatal.
case "$CMD" in
  -h|--help|help|uninstall) : ;;
  *) if [ -f "$ENV_FILE" ] && [ -f "$RIGS_PY" ] && [ ! -f "$RIG_REGISTRY" ]; then
       _rigs migrate 2>/dev/null || true
     fi ;;
esac

# Bearer via lib/curl_auth.sh: the device key rides a curl --config on an fd,
# never curl's argv (world-readable through ps on a shared box).
# shellcheck source=lib/curl_auth.sh
. "$REPO/scripts/lib/curl_auth.sh"

_curl_auth() {
  # _curl_auth <url> [extra curl args…] — bearer added when keyed.
  _u="$1"; shift
  ob_curl_bearer "${OPENBEAST_API_KEY:-}" -s -m 5 "$@" "$_u"
}

case "$CMD" in
  status)
    echo "=== OpenBeast client status ==="
    # Never installed is not "broken": with neither the env file nor the
    # client dir there is nothing to repair, and "venv broken — re-run" sent
    # people looking for a run they never made.
    if [ ! -f "$ENV_FILE" ] && [ ! -d "$CLIENT_DIR" ]; then
      echo "  ✗ client mode is not installed on this machine"
      echo "    (no $ENV_FILE, no $CLIENT_DIR)."
      echo "    Install it:  $REPO/scripts/setup-client.sh --host <rig-fqdn>"
      exit 1
    fi
    ok=0; bad=0
    if [ -f "$ENV_FILE" ]; then
      echo "  ✓ env file ($ENV_FILE)"; ok=$((ok+1))
    else
      echo "  ✗ no $ENV_FILE — run scripts/setup-client.sh"; bad=$((bad+1))
    fi
    # Installed and resolvable, without importing them: a full `import mcp,
    # openai` costs ~0.65 s on every doctor run. setup-client.sh does the full
    # import once, at install; here the dist metadata + import spec catch a
    # missing or half-removed package, which is what goes wrong in practice.
    if [ -x "$VENV/bin/python3" ] && "$VENV/bin/python3" -c '
import importlib.metadata as m, importlib.util as u, sys
for p in ("mcp", "openai"):
    m.version(p)
    if u.find_spec(p) is None:
        sys.exit(1)' 2>/dev/null; then
      echo "  ✓ venv has mcp + openai"; ok=$((ok+1))
    elif [ ! -x "$VENV/bin/python3" ]; then
      echo "  ✗ no venv at $VENV — run scripts/setup-client.sh"; bad=$((bad+1))
    else
      echo "  ✗ venv broken (mcp/openai missing) — re-run scripts/setup-client.sh"; bad=$((bad+1))
    fi
    # Which rig this client is pointed at (several may be registered).
    if [ -f "$RIGS_PY" ]; then
      _act="$(_rigs active 2>/dev/null || true)"
      _a1="$(printf '%s\n' "$_act" | sed -n '1p')"
      case "$_a1" in
        *'|'*'|'*)
          _an="${_a1%%|*}"; _rest="${_a1#*|}"; _ah="${_rest%%|*}"; _anum="${_rest#*|}"
          echo "  ✓ active rig: $_an ($_ah) — $_anum registered; list: openbeast-client rigs"; ok=$((ok+1))
          printf '%s\n' "$_act" | sed -n '2,$p' | sed 's/^/    /' ;;
      esac
    fi
    if [ -n "$TS_BIN" ] && "$TS_BIN" status >/dev/null 2>&1; then
      echo "  ✓ tailscale up"; ok=$((ok+1))
    else
      echo "  ✗ tailscale down — if it JUST dropped, a full-tunnel VPN"
      echo "    (NordVPN etc.) may have grabbed the default route: quit it,"
      echo "    then 'tailscale up' (docs/BEAST_SLOT.md § VPN collision)"
      bad=$((bad+1))
    fi
    BASE="${OPENBEAST_AGENT_INFERENCE_URL:-}"
    # Timeout for the remaining network probes. When the rig did not answer
    # at all, the slot and search probes each used to wait their full 5 s
    # (23 s to report a down rig): 2 s is plenty to tell "up" from "not".
    PROBE_T=5
    if [ -n "$BASE" ]; then
      HURL="$(echo "$BASE" | sed 's|/v1$||')/health"
      # ONE request for body and status. A failed probe used to ask the same
      # URL again (-m 8) only to read its status: 13 s on a blackholed host.
      # The STATUS is what names the failure: grepping bodies collapsed a
      # REVOKED device, a rig that is down, a rig still loading a 27B model
      # and a TLS/clock problem into "not answering"; beast-gate tells them
      # apart (401 vs 502 vs 504). curl prints 000 AND exits non-zero when it
      # cannot connect, so capture, ignore the exit status, and default.
      _ho="$(_curl_auth "$HURL" -w '\n%{http_code}' 2>/dev/null)" || true
      HCODE="${_ho##*$'\n'}"; HBODY="${_ho%$'\n'*}"
      [ "$HBODY" = "$_ho" ] && HBODY=""
      case "$HCODE" in [0-9][0-9][0-9]) : ;; *) HCODE=000 ;; esac
      [ "$HCODE" = "000" ] && PROBE_T=2
      if printf '%s' "$HBODY" | grep -q "ok"; then
        # /health is public even on a keyed server, so it proves reachability
        # but NOT that our key is accepted. Probe a protected endpoint too,
        # otherwise a wrong/revoked key looks perfectly healthy here and only
        # surfaces as a 401 mid-conversation.
        if _curl_auth "$BASE/models" | grep -q '"data"'; then
          echo "  ✓ rig model API reachable ($BASE)"; ok=$((ok+1))
        else
          echo "  ✗ rig reachable but /v1/models refused — wrong or missing"
          echo "    API key? Re-run: setup-client.sh --api-key-stdin (paste the key)"
          bad=$((bad+1))
        fi
      else
        # Say WHICH failure this is. "Not answering" was wrong for most of them.
        case "$HCODE" in
          401|403)
            echo "  ✗ rig rejected this device (401/403) — key wrong, or your"
            echo "    device was revoked. Ask the rig owner to re-enroll:"
            echo "    ./scripts/clients.sh enroll <name>, then setup-client.sh --api-key-stdin" ;;
          503)
            echo "  ! rig is up but still loading the model (503) — retry shortly" ;;
          502|504)
            echo "  ✗ rig's gate is up but llama-server is not answering behind it"
            echo "    (502/504) — the model may be restarting; check the rig" ;;
          404)
            echo "  ✗ endpoint reachable but /health is not there (404) — is"
            echo "    OPENBEAST_AGENT_INFERENCE_URL pointing at the right port?" ;;
          000)
            echo "  ✗ no response from $BASE — rig down, not on the tailnet, or"
            echo "    TLS failed (a large clock skew breaks it; check your date)" ;;
          *)
            echo "  ✗ rig model API not answering ($BASE)" ;;
        esac
        bad=$((bad+1))
      fi
    else
      echo "  ✗ OPENBEAST_AGENT_INFERENCE_URL not set"; bad=$((bad+1))
    fi
    if [ -n "${OPENBEAST_SLOT_URL:-}" ]; then
      # beast-slot contract v2 (docs/BEAST_SLOT.md). Line 1 is the summary;
      # any further lines are warnings, printed indented below it.
      # The STATUS is read too: a busy dashboard (503, documented as "retry,
      # not an outage"), a dashboard that is not running (502), a path that
      # is not published (404) and nothing answering in 5 s all used to print
      # the same "not published — run --publish-slot", the wrong fix for
      # three of them. A 503 is retried once, after its Retry-After.
      _slot_get() {
        _o="$(curl -s -m "$PROBE_T" -w '\n%{http_code}' "$OPENBEAST_SLOT_URL" 2>/dev/null)" || true
        SLOT_CODE="${_o##*$'\n'}"; SLOT_BODY="${_o%$'\n'*}"
        case "$SLOT_CODE" in [0-9][0-9][0-9]) : ;; *) SLOT_CODE=000 ;; esac
      }
      _slot_get
      if [ "$SLOT_CODE" = "503" ]; then sleep 1; _slot_get; fi
      SLOT="$(printf '%s' "$SLOT_BODY" | OB_OC_CONFIG="$OC_CONFIG" "$PY_BIN" -c '
import json, os, sys
CLIENT_V = 2   # the contract version THIS client understands
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(d, dict) or "beast_slot" not in d:
    sys.exit(0)
m, s = d.get("model") or {}, d.get("slots") or {}
c = d.get("capacity") or {}


def num(v):
    return "?" if v is None else v


def ctx(n):
    # 212992 -> "208K": the number people actually quote.
    if not n:
        return "?"
    return "%dK" % (n // 1024) if n >= 1024 else str(n)


# ctx_shared=True means that context is ONE pool across every slot, not a
# per-slot allowance — the trap v2 exists to close. False -> it multiplies.
budget = ctx(m.get("ctx"))
if c.get("ctx_shared") is True:
    budget += " shared"
elif c.get("ctx_shared") is False and c.get("ctx_total"):
    budget += " x%s (%s total)" % (num(s.get("total")), ctx(c["ctx_total"]))
print("%s | model %s | slots %s/%s busy | ctx %s | queued %s | %s | auth %s" % (
    "healthy" if d.get("healthy") else "UNHEALTHY",
    m.get("id") or "?", num(s.get("busy")), num(s.get("total")), budget,
    num(c.get("queue_deferred")), c.get("serving_profile") or "unknown",
    d.get("auth", "?")))
rig_v, min_c = d.get("beast_slot"), d.get("min_client")
if isinstance(min_c, int) and min_c > CLIENT_V:
    print("! rig needs beast-slot client v%d, this client speaks v%d —"
          " run: openbeast-client update" % (min_c, CLIENT_V))
elif isinstance(rig_v, int) and rig_v > CLIENT_V:
    print("! rig publishes beast-slot v%d, this client reads v%d — fields"
          " above are still correct, newer ones are ignored;"
          " openbeast-client update to see them" % (rig_v, CLIENT_V))
# An id-strict server (vLLM; hydra with unknown_model = "404") answers 404 to
# a model id it does not serve. llama-server ignores the id, so only a rig
# that names a non-llama backend is checked. The default comes from the
# static llama catalog whenever the live probe failed at install time.
backend = d.get("backend") or "llama"
served = m.get("id")
if backend != "llama" and served:
    try:
        cur = str(json.load(open(os.environ["OB_OC_CONFIG"])).get("model") or "")
    except Exception:
        cur = ""
    if cur.startswith("openbeast-rig/") and cur.split("/", 1)[1] != served:
        print("! opencode default %s is not the id this %s rig serves (%s) —"
              " an id-strict server 404s it; run: openbeast-client refresh-config"
              % (cur, backend, served))
' || true)"
      if [ -n "$SLOT" ]; then
        echo "  ✓ beast-slot: $(printf '%s\n' "$SLOT" | sed -n '1p')"; ok=$((ok+1))
        printf '%s\n' "$SLOT" | sed -n '2,$p' | sed 's/^/    /'
      else
        # Informational either way (the slot API is optional), but name the
        # failure that actually happened.
        case "$SLOT_CODE" in
          503)
            echo "  ! beast-slot busy (503 twice): the rig's dashboard is at its"
            echo "    connection cap. It is not an outage. Retry shortly" ;;
          502|504)
            echo "  ! beast-slot is published, but the dashboard behind it is not"
            echo "    running (HTTP $SLOT_CODE). On the rig: ./scripts/ext.sh enable dashboard,"
            echo "    then restart the stack" ;;
          404)
            echo "  ! beast-slot status not published (optional — rig:"
            echo "      ./scripts/setup-tailscale.sh --publish-slot)" ;;
          000)
            echo "  ! beast-slot did not answer within 5 s ($OPENBEAST_SLOT_URL)."
            echo "    Either :8444 is not published (rig: ./scripts/setup-tailscale.sh"
            echo "    --publish-slot), the rig is unreachable, or its dashboard is stalled" ;;
          200)
            echo "  ! beast-slot answered, but not with a beast-slot document"
            echo "    ($OPENBEAST_SLOT_URL). Is OPENBEAST_SLOT_URL the rig's :8444/api/slot?" ;;
          *)
            echo "  ! beast-slot answered HTTP $SLOT_CODE ($OPENBEAST_SLOT_URL)" ;;
        esac
      fi
    fi
    if [ -n "${SEARXNG_URL:-}" ]; then
      # No real search: `?q=test` sent a live query out to SearXNG's upstream
      # engines on every doctor run (0.8-1.2 s, and egress an islanded or
      # offline-first setup does not want). /healthz proves it is up; a
      # query-less `format=json` request proves JSON output is enabled, which
      # web_search needs: SearXNG answers 400 "No query" when it is, 403 when
      # search.formats lacks json — and contacts no engine either way.
      _sx_h="$(curl -s -m "$PROBE_T" "$SEARXNG_URL/healthz" 2>/dev/null)" || true
      _sx_f="$(curl -s -o /dev/null -w '%{http_code}' -m "$PROBE_T" "$SEARXNG_URL/search?format=json" 2>/dev/null)" || true
      if [ "$_sx_h" = "OK" ] && [ "$_sx_f" = "400" ]; then
        echo "  ✓ search reachable ($SEARXNG_URL)"; ok=$((ok+1))
      elif [ "$_sx_h" = "OK" ] && [ "$_sx_f" = "403" ]; then
        echo "  ✗ search is up but its JSON output is off — add json to search.formats"
        echo "    in SearXNG's settings.yml ($SEARXNG_URL)"; bad=$((bad+1))
      else
        echo "  ✗ search not answering ($SEARXNG_URL)"; bad=$((bad+1))
      fi
    else
      echo "  - search not wired (--no-search)"
    fi
    if [ -f "$OC_CONFIG" ] && grep -qE '"openbeast-tools"|"local-tools"' "$OC_CONFIG" \
       && grep -qE '"openbeast-rig"|"llama-cpp"' "$OC_CONFIG"; then
      # NOTE: this greps the config file; it cannot prove opencode/Bun actually
      # connects (that runtime resolves the provider itself, and a repo-local
      # ./opencode.json can shadow a same-id provider when run from that dir —
      # which is why the client provider id is the distinct "openbeast-rig").
      # A green line here means "wired", not "opencode reached the rig".
      echo "  ✓ opencode.json wired (openbeast-tools + openbeast-rig provider)"; ok=$((ok+1))
    else
      echo "  ✗ opencode.json not wired — re-run scripts/setup-client.sh"; bad=$((bad+1))
    fi
    # The soul (the rig's WebUI system prompt, via opencode `instructions`).
    # Informational: a session without it still works, it just is not OpenBeast.
    if [ -f "$OC_CONFIG" ] && [ -f "$RIGS_PY" ]; then
      _src=0
      _sl="$(OB_REPO="$REPO" "$PY_BIN" "$RIGS_PY" soul --check --oc "$OC_CONFIG" 2>/dev/null)" || _src=$?
      if [ "$_src" -eq 0 ] && [ -n "$_sl" ]; then
        echo "  ✓ $_sl"; ok=$((ok+1))
      elif [ -n "$_sl" ]; then
        echo "  $_sl"
      fi
    fi
    # Informational: without it opencode keeps a switched rig's old model.
    "$_self" autosync status 2>/dev/null | head -1 || true
    echo ""
    if [ $bad -eq 0 ]; then
      echo "client: $ok ok — ready. (cd <project> && opencode)"
    else
      echo "client: $ok ok, $bad failing."
      exit 1
    fi
    ;;

  agent)
    # CLI agent on THIS machine, thinking via the rig — without agent.sh or
    # conf.sh (rig-shaped). runner.py resolves OPENBEAST_API_KEY from env.
    [ -x "$VENV/bin/python3" ] || { echo "venv missing — run scripts/setup-client.sh" >&2; exit 1; }
    [ -n "${OPENBEAST_AGENT_INFERENCE_URL:-}" ] || { echo "OPENBEAST_AGENT_INFERENCE_URL not set — run scripts/setup-client.sh" >&2; exit 1; }
    # Tier-3 awareness pack on zig tasks, same as the rig's agent.sh
    # (agents/lang/pack_context.py). Tools run HERE, so the pack is this
    # checkout's agents/packs/ file checked against THIS machine's zig; a
    # slim checkout without it just gets no pack. bash-3.2-safe (macOS).
    _args=("$@")
    if [ -f "$REPO/agents/lang/pack_context.py" ]; then
      _packed=()
      while IFS= read -r -d '' _a; do _packed+=("$_a"); done \
        < <("$VENV/bin/python3" "$REPO/agents/lang/pack_context.py" argv -- ${_args[@]+"${_args[@]}"} || true)
      if [ "${#_packed[@]}" -gt 0 ] && [ "${#_packed[@]}" -ge "${#_args[@]}" ]; then
        _args=("${_packed[@]}")
      fi
    fi
    exec "$VENV/bin/python3" "$REPO/agents/runner.py" \
      --base-url "$OPENBEAST_AGENT_INFERENCE_URL" ${_args[@]+"${_args[@]}"}
    ;;

  search)
    SUB="${1:-}"
    COMPOSE_FILE="$REPO/scripts/client-searxng.compose.yml"
    if [ -z "${OPENBEAST_SEARXNG_SECRET:-}" ]; then
      echo "local search not configured — install with: setup-client.sh --local-search" >&2
      exit 1
    fi
    case "$SUB" in
      up)   OPENBEAST_SEARXNG_SECRET="$OPENBEAST_SEARXNG_SECRET" \
              docker compose -p openbeast-client -f "$COMPOSE_FILE" up -d ;;
      down) OPENBEAST_SEARXNG_SECRET="$OPENBEAST_SEARXNG_SECRET" \
              docker compose -p openbeast-client -f "$COMPOSE_FILE" down ;;
      *) echo "usage: client.sh search up|down" >&2; exit 2 ;;
    esac
    ;;

  update)
    # Pull whichever checkout we are ACTUALLY running from. This used to pull
    # only $CLIENT_DIR/repo (the slim-checkout install); for the in-place clone
    # the README documents it printed "pull it yourself" and then said "client
    # updated." regardless. So the remedy the version-skew warning in `status`
    # points users to did nothing, and reported success while doing it.
    # Offline route (docs/BEAST_APPS.md § Offline client): update the checkout
    # by other means (`git pull /media/usb/openbeast.bundle main`), then
    #   openbeast-client update --no-pull --wheelhouse /media/usb/wheels
    # which installs the same hash-pinned closure with no index contacted.
    _no_pull=0; _wheels="${OPENBEAST_WHEELHOUSE:-}"
    while [ $# -gt 0 ]; do
      case "$1" in
        --no-pull)   _no_pull=1 ;;
        --wheelhouse)
          [ $# -ge 2 ] && [ -n "$2" ] || { echo "--wheelhouse needs a directory" >&2; exit 2; }
          _wheels="$2"; shift ;;
        *) echo "usage: client.sh update [--no-pull] [--wheelhouse <dir>]" >&2; exit 2 ;;
      esac
      shift
    done
    # A venv an older install built with python 3.10 (accepted back then)
    # cannot take the lock (starlette, jiter, rpds-py need >=3.11): say so,
    # instead of letting pip die with a resolver error that reads like a
    # hostile mirror.
    if [ -x "$VENV/bin/python3" ] && ! "$VENV/bin/python3" -c \
         'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "  x $VENV is $("$VENV/bin/python3" -V 2>&1 | head -1); the pinned deps need Python 3.11+." >&2
      echo "    Re-run the installer, which rebuilds the venv with a vetted interpreter:" >&2
      echo "      $REPO/scripts/setup-client.sh --host <rig-fqdn>" >&2
      exit 1
    fi
    _pulled=0
    if [ "$_no_pull" -eq 1 ]; then
      echo "  - --no-pull: using the checkout as it is ($REPO)"
    elif [ -d "$REPO/.git" ]; then
      if git -C "$REPO" pull --ff-only; then
        _pulled=1
      else
        echo "  x git pull failed in $REPO (local changes, or not fast-forward)." >&2
        echo "    Resolve it there, then re-run: openbeast-client update" >&2
        exit 1
      fi
    else
      echo "  ! $REPO is not a git checkout, so the source cannot be updated." >&2
    fi
    # Same install as setup-client.sh step 3: the hash-pinned closure from
    # agents/requirements.lock via pydeps.sh. A plain `pip install -r
    # requirements.txt` here re-resolved every transitive dependency, unpinned
    # and unverified, into the venv that runs bash and the file tools — on the
    # first update after a hash-pinned install. pydeps.sh exit 3 is a HASH
    # MISMATCH: fatal, never a fallback. Any OTHER failure is fatal too unless
    # OPENBEAST_PIP_STRICT=0 is set explicitly: a hostile mirror does not
    # have to serve wrong bytes, it can just withhold one locked file ("No
    # matching distribution", exit 1) — and an automatic fallback would then
    # install the same names unverified, from that same mirror. The opt-out
    # is for a python the closure does not cover. (Bash 3.2-safe: this file
    # runs on macOS.)
    if [ -x "$VENV/bin/pip" ]; then
      _pd_rc=0
      if [ -x "$REPO/scripts/pydeps.sh" ] && [ -n "$_wheels" ]; then
        # --from must come first: pydeps.sh stops reading its own flags at -q
        OPENBEAST_PYTHON="$VENV/bin/python3" "$REPO/scripts/pydeps.sh" install --from "$_wheels" -q || _pd_rc=$?
      elif [ -x "$REPO/scripts/pydeps.sh" ]; then
        OPENBEAST_PYTHON="$VENV/bin/python3" "$REPO/scripts/pydeps.sh" install -q || _pd_rc=$?
      else
        _pd_rc=127
      fi
      if [ "$_pd_rc" -eq 3 ]; then
        echo "  x HASH MISMATCH installing the client's python deps (pip's report is above)." >&2
        echo "    The index served bytes that are NOT the ones agents/requirements.lock pins." >&2
        echo "    Refusing, and NOT falling back to requirements.txt (same packages, unverified)." >&2
        echo "    If a mirror or proxy is configured (pip config list, PIP_INDEX_URL), suspect it first." >&2
        exit 1
      elif [ "$_pd_rc" -ne 0 ]; then
        if [ "${OPENBEAST_PIP_STRICT:-1}" != "0" ]; then
          echo "  x the hash-pinned install failed, and NOT on a hash (pip's report is above)." >&2
          echo "    Refusing to fall back to agents/requirements.txt: that installs the same" >&2
          echo "    packages UNVERIFIED, which is what an index that withholds a locked file wants." >&2
          echo "    If a mirror or proxy is configured (pip config list, PIP_INDEX_URL), suspect it first." >&2
          echo "    If this python is simply not covered by the lock, opt out explicitly:" >&2
          echo "      OPENBEAST_PIP_STRICT=0 $0 update" >&2
          exit 1
        fi
        echo "  ! the hash-pinned install failed on this python, and NOT on a hash (see above) —" >&2
        echo "    OPENBEAST_PIP_STRICT=0: falling back to agents/requirements.txt, which pins" >&2
        echo "    VERSIONS but not content." >&2
        _fl=""; [ -n "$_wheels" ] && _fl="--no-index"
        if ! "$VENV/bin/pip" install -q ${_fl:+$_fl --find-links "$_wheels"} -r "$REPO/agents/requirements.txt"; then
          echo "  x dependency install failed." >&2
          exit 1
        fi
      fi
    fi
    # Always refresh, even when the pull was a no-op: the catalog can be stale
    # against an already-current checkout (e.g. the install predates a model
    # that the checkout has had for weeks).
    _refresh_oc_catalog
    if [ "$_pulled" -eq 1 ]; then
      echo "client updated (source + dependencies + opencode catalog)."
    else
      echo "dependencies reinstalled and catalog refreshed; SOURCE NOT UPDATED."
    fi
    ;;

  refresh-config)
    # Catalog re-sync WITHOUT touching the checkout or deps. `update` runs this
    # too; this is the standalone door for "the rig gained/lost a model and I
    # already have the source" (and what setup-client.sh calls on a fresh
    # install, so both paths produce an identical config).
    _refresh_oc_catalog
    ;;

  autosync)
    # The rig can switch models under a running client (on the Sparks:
    # beastglm / beastds). opencode only knows what opencode.json says, so
    # without this the picker kept the old model's name and context window
    # until someone ran `use` or refresh-config (seen 2026-11-18). The timer
    # runs `autosync run`: two small GETs to the active rig, and a full sync
    # only when what it serves changed. Offline rigs change nothing.
    _as_label="ai.openbeast.client-autosync"
    _as_plist="$HOME/Library/LaunchAgents/$_as_label.plist"
    _as_unit="openbeast-client-autosync"
    _as_udir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    _as_log="$CLIENT_DIR/autosync.log"
    _as_sub="${1:-status}"
    case "$_as_sub" in
      run)
        [ -f "$RIGS_PY" ] || exit 0
        _rigs sync --if-changed
        ;;
      install)
        mkdir -p "$CLIENT_DIR"
        _as_snapshot || { echo "  x could not copy the autosync files into $AS_SNAP" >&2; exit 1; }
        if [ "$(uname -s)" = Darwin ]; then
          mkdir -p "$(dirname "$_as_plist")"
          _x_py="$(_xml_esc "$PY_BIN")"; _x_snap="$(_xml_esc "$AS_SNAP")"
          _x_ts="$(_xml_esc "$TS_BIN")"; _x_log="$(_xml_esc "$_as_log")"
          cat > "$_as_plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$_as_label</string>
  <key>ProgramArguments</key>
  <array><string>$_x_py</string><string>$_x_snap/scripts/lib/client_rigs.py</string><string>sync</string><string>--if-changed</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>OB_REPO</key><string>$_x_snap</string><key>OB_TS_BIN</key><string>$_x_ts</string></dict>
  <key>StartInterval</key><integer>30</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$_x_log</string>
  <key>StandardErrorPath</key><string>$_x_log</string>
</dict>
</plist>
PLIST
          launchctl bootout "gui/$(id -u)/$_as_label" >/dev/null 2>&1 || true
          launchctl bootstrap "gui/$(id -u)" "$_as_plist"
          echo "  ok autosync: launchd agent $_as_label, every 30 s (log: $_as_log)"
        elif command -v systemctl >/dev/null 2>&1; then
          mkdir -p "$_as_udir"
          printf '[Unit]\nDescription=OpenBeast client: re-sync opencode when the rig switches models\n\n[Service]\nType=oneshot\nEnvironment=%s %s\nExecStart=%s %s sync --if-changed\nStandardOutput=append:%s\nStandardError=append:%s\n' \
            "$(_sd_q "OB_REPO=$AS_SNAP")" "$(_sd_q "OB_TS_BIN=$TS_BIN")" "$(_sd_q "$PY_BIN")" \
            "$(_sd_q "$AS_SNAP/scripts/lib/client_rigs.py")" "$(_sd_path "$_as_log")" "$(_sd_path "$_as_log")" \
            > "$_as_udir/$_as_unit.service"
          printf '[Unit]\nDescription=OpenBeast client autosync every 30 s\n\n[Timer]\nOnBootSec=30s\nOnUnitActiveSec=30s\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n' \
            > "$_as_udir/$_as_unit.timer"
          systemctl --user daemon-reload && systemctl --user enable --now "$_as_unit.timer" >/dev/null
          echo "  ok autosync: systemd user timer $_as_unit.timer, every 30 s (log: $_as_log)"
        else
          echo "  ! autosync: neither launchd nor systemd here; run 'openbeast-client refresh-config' after a rig switches models" >&2
          exit 1
        fi
        ;;
      uninstall)
        if [ "$(uname -s)" = Darwin ]; then
          launchctl bootout "gui/$(id -u)/$_as_label" >/dev/null 2>&1 || true
          rm -f "$_as_plist"
        elif command -v systemctl >/dev/null 2>&1; then
          systemctl --user disable --now "$_as_unit.timer" >/dev/null 2>&1 || true
          rm -f "$_as_udir/$_as_unit.service" "$_as_udir/$_as_unit.timer"
          systemctl --user daemon-reload >/dev/null 2>&1 || true
        fi
        rm -rf "$AS_SNAP"
        rm -f "$CLIENT_DIR/autosync.notice"
        echo "  ok autosync removed"
        ;;
      status)
        _as_on=0
        if [ "$(uname -s)" = Darwin ]; then
          if _as_p="$(launchctl print "gui/$(id -u)/$_as_label" 2>/dev/null)"; then
            _as_on=1
            echo "  ok autosync: on (launchd $_as_label, every 30 s)"
            _as_rc="$(printf '%s\n' "$_as_p" | sed -n 's/^[[:space:]]*last exit code = \(.*\)$/\1/p' | head -1)"
            case "$_as_rc" in
              "") : ;;
              0|"(never exited)") echo "  ok last run: exit ${_as_rc}" ;;
              *) echo "  ! last run: exit $_as_rc — see $_as_log" ;;
            esac
          else echo "  ! autosync: off — openbeast-client autosync install"; fi
        elif systemctl --user is-active --quiet "$_as_unit.timer" 2>/dev/null; then
          _as_on=1
          echo "  ok autosync: on ($_as_unit.timer, every 30 s)"
          _as_res="$(systemctl --user show -p Result --value "$_as_unit.service" 2>/dev/null || true)"
          _as_st="$(systemctl --user show -p ExecMainStatus --value "$_as_unit.service" 2>/dev/null || true)"
          case "$_as_res" in
            "") : ;;
            success) echo "  ok last run: success (exit ${_as_st:-0})" ;;
            *) echo "  ! last run: $_as_res (exit ${_as_st:-?}) — see $_as_log" ;;
          esac
        else echo "  ! autosync: off — openbeast-client autosync install"; fi
        # The job runs the COPY in $AS_SNAP: say when it lags this checkout.
        if [ "$_as_on" = 1 ] || [ -d "$AS_SNAP" ]; then
          _as_stale=""
          for _f in $AS_FILES; do
            [ -f "$REPO/$_f" ] || continue
            cmp -s "$REPO/$_f" "$AS_SNAP/$_f" || _as_stale="$_as_stale $_f"
          done
          if [ -n "$_as_stale" ]; then echo "  ! autosync copy in $AS_SNAP differs from this checkout:$_as_stale — openbeast-client autosync install"
          else echo "  ok autosync copy matches this checkout"; fi
        fi
        [ -s "$_as_log" ] && echo "  last: $(tail -1 "$_as_log")"
        ;;
      *) echo "usage: openbeast-client autosync [install|uninstall|status|run]" >&2; exit 2 ;;
    esac
    ;;

  rigs)
    [ -f "$RIGS_PY" ] || { echo "$RIGS_PY missing — openbeast-client update" >&2; exit 1; }
    _rigs list "$@"
    ;;

  use)
    [ $# -ge 1 ] || { echo "usage: openbeast-client use <rig> [--force]   (list: openbeast-client rigs)" >&2; exit 2; }
    [ -f "$RIGS_PY" ] || { echo "$RIGS_PY missing — openbeast-client update" >&2; exit 1; }
    _rigs use "$@"
    ;;

  rig)
    [ -f "$RIGS_PY" ] || { echo "$RIGS_PY missing — openbeast-client update" >&2; exit 1; }
    _sub="${1:-}"; [ $# -gt 0 ] && shift
    case "$_sub" in
      add|remove|detect) _rigs "$_sub" "$@" ;;
      *) echo "usage: openbeast-client rig add <name> [--host <fqdn>] [--api-key-stdin | --key-file F] [--search rig|local|none] [--model ID]" >&2
         echo "       openbeast-client rig remove <name> | rig detect" >&2
         exit 2 ;;
    esac
    ;;

  uninstall)
    exec "$REPO/scripts/setup-client.sh" --uninstall
    ;;

  -h|--help|help)
    sed -n '2,48p' "$_self" | sed 's/^# \{0,1\}//'
    ;;

  *)
    echo "unknown command: $CMD (status|rigs|use|rig|agent|search|update|refresh-config|autosync|uninstall)" >&2
    exit 2
    ;;
esac
