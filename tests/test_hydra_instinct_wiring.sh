#!/bin/bash
# beast-hydra + beast-instinct STACK WIRING (docs/BEAST_HYDRA_PLAN.md §6.7,
# docs/BEAST_INSTINCT_PLAN.md §5.9): conf.sh, start.sh, stop.sh,
# healthcheck.sh. The Python halves live in test_router.py, test_edge.py,
# test_router_instinct.py, test_beast_slot.py and test_webui_default_admin.py.
#
# The load-bearing property: with HYDRA and INSTINCT unset/false the stack is
# BYTE-IDENTICAL to one without this wiring. Proved three ways here:
#   1. conf.sh derived values and the exported environment, per scenario,
#      against literal expectations (always runs);
#   2. when WIRING_BASELINE_REF names a git ref from before the wiring
#      (e.g. WIRING_BASELINE_REF=integ/chat-artifact-2026-09-30), conf.sh,
#      stop.sh and healthcheck.sh from that ref and from the worktree are run
#      side by side under `set -euo pipefail` and their outputs diffed
#      (CI pins v1.6.0 so the proof keeps running now the wiring is on main;
#      _BASELINE_ALLOW names the few unrelated exports added since);
#   3. stop.sh / healthcheck.sh with hydra off print no hydra/instinct line.
#
# Same rules as tests/test_lifecycle.sh: no GPU, no docker, no network, no
# real stack. curl is a stub that reaches ONLY this file's own fake servers
# (ephemeral loopback ports); pkill/pgrep only patterns anchored inside the
# sandbox. Every process started here is reaped by the EXIT trap.
#
# Usage: bash tests/test_hydra_instinct_wiring.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# The baseline proof must never skip silently (review minor 2): say which
# way it goes, and refuse a ref this clone cannot read.
if [[ -n "${WIRING_BASELINE_REF:-}" ]]; then
  if ! git -C "$REPO_DIR" rev-parse -q --verify "${WIRING_BASELINE_REF}^{commit}" >/dev/null; then
    echo "FAIL: WIRING_BASELINE_REF='$WIRING_BASELINE_REF' is not a commit in this clone" >&2
    exit 1
  fi
  if git -C "$REPO_DIR" show "${WIRING_BASELINE_REF}:scripts/lib/conf.sh" | grep -q 'HYDRA_READY_GRACE'; then
    echo "NOTE: $WIRING_BASELINE_REF already contains the wiring — the side-by-side diff shows only"
    echo "      that off-mode output did not change since it, not that it matches a pre-wiring stack."
  fi
elif [[ "${WIRING_BASELINE_REQUIRED:-0}" == "1" ]]; then
  echo "FAIL: WIRING_BASELINE_REQUIRED=1 but no WIRING_BASELINE_REF" >&2
  exit 1
else
  echo "SKIP: the side-by-side byte-identity diff (set WIRING_BASELINE_REF=<a pre-wiring ref>)"
fi

_T="$(mktemp -d "${TMPDIR:-/tmp}/obwiringXXXXXX")"
_PIDS=""
cleanup() {
  local p
  for p in $_PIDS; do kill "$p" 2>/dev/null || true; done
  rm -rf "$_T"
}
trap cleanup EXIT

# ── conf.sh ─────────────────────────────────────────────────────────────────
# A sandbox REPO_DIR holding lib/ (from the worktree, or from a git ref) and an
# openbeast.conf. _conf prints every exported variable (sorted) plus the
# derived shell values the rest of the stack reads.
_conf_box() { # _conf_box <dir> [git-ref]
  local d="$1" ref="${2:-}" f
  mkdir -p "$d/scripts/lib" "$d/home"
  for f in conf.sh net.sh backend.sh; do
    if [[ -n "$ref" ]]; then
      git -C "$REPO_DIR" show "$ref:scripts/lib/$f" > "$d/scripts/lib/$f"
    else
      cp "$REPO_DIR/scripts/lib/$f" "$d/scripts/lib/$f"
    fi
  done
}
DERIVED="MODEL_URL AGENT_INFERENCE_URL INFERENCE_URL INFERENCE_MODEL INFERENCE_BACKEND INFERENCE_MANAGED BIND_HOST AGENT_ROUTER ROUTER_PORT EDGE_GATE"
_conf() { # _conf <dir> <conf-text> [VAR=value ...]   (stdout: the dump; stderr kept)
  local d="$1" text="$2"; shift 2
  printf 'SEARXNG_SECRET=stub\n%s\n' "$text" > "$d/openbeast.conf"
  chmod 600 "$d/openbeast.conf"
  env -i HOME="$d/home" PATH="/usr/bin:/bin" "$@" bash -c '
    set -euo pipefail
    REPO_DIR="$1"
    source "$REPO_DIR/scripts/lib/conf.sh" 2>"$REPO_DIR/stderr"
    for v in $2; do printf "VAR %s=%s\n" "$v" "${!v-<unset>}"; done
    env | LC_ALL=C sort | grep -vE "^(PWD|SHLVL|_|OLDPWD)=" | sed "s/^/ENV /"
  ' _ "$d" "$DERIVED"
}
_get() { sed -n "s/^$1 $2=//p" <<< "$3"; }

echo "=== beast-hydra / beast-instinct wiring ==="
echo ""
echo "conf.sh — HYDRA/INSTINCT off exports nothing new and changes nothing:"
_N="$_T/new"; _conf_box "$_N"
SCENARIOS=(
  "default local llama|"
  "remote llama INFERENCE_URL|INFERENCE_URL=http://10.0.0.5:8080"
  "vLLM backend with a served id|INFERENCE_BACKEND=vllm
INFERENCE_URL=http://10.0.0.5:8000/v1
INFERENCE_MODEL=my-nvfp4  # trailing comment
INFERENCE_SLOTS=8"
  "router on|AGENT_ROUTER=true"
  "explicit HYDRA=false INSTINCT=false + gate + key|HYDRA=false
INSTINCT=false
INSTINCT_SCORER=false
ROUTER_INSTINCT=off
EDGE_GATE=true
LLAMA_API_KEY=k"
  "explicit AGENT_INFERENCE_URL on a wildcard bind|AGENT_INFERENCE_URL=https://worker.example:8443/v1
BIND_HOST=0.0.0.0
ALLOW_OPEN_TOOLS=true"
  "every hydra/instinct knob set, both switches off|HYDRA=no
HYDRA_PORT=9999
HYDRA_CONFIG=/elsewhere/hydra.toml
HYDRA_DEFAULT_MODEL=other
INSTINCT=off
INSTINCT_PORT=9998
ROUTER_INSTINCT=enforce"
)
for _sc in "${SCENARIOS[@]}"; do
  _name="${_sc%%|*}"; _text="${_sc#*|}"
  _out="$(_conf "$_N" "$_text")"
  _new_env="$(grep '^ENV ' <<< "$_out" | grep -E '^ENV (OPENBEAST_)?(HYDRA|INSTINCT|ROUTER_INSTINCT|CONSUMER_BASE)|^ENV OPENBEAST_(HYDRA|INSTINCT|CONSUMER)' || true)"
  if [[ -z "$_new_env" ]]; then
    pass "$_name: no HYDRA/INSTINCT/CONSUMER_BASE variable exported"
  else
    fail "$_name: exported $(tr '\n' ' ' <<< "$_new_env")"
  fi
done
# Literal expectations — the pre-hydra values (plan §6.11 test_scripts.sh list).
_o="$(_conf "$_N" "")"
[[ "$(_get VAR MODEL_URL "$_o")" == "http://localhost:8080/v1" \
   && "$(_get VAR AGENT_INFERENCE_URL "$_o")" == "" \
   && -z "$(_get ENV OPENBEAST_INFERENCE_MODEL "$_o")" \
   && -z "$(_get ENV OPENBEAST_AGENT_INFERENCE_URL "$_o")" ]] \
  && pass "default: MODEL_URL localhost:8080, no agent URL, OPENBEAST_INFERENCE_MODEL unset" \
  || fail "default derivations moved: $(grep -E 'MODEL_URL|AGENT_INF|INFERENCE_MODEL' <<< "$_o" | tr '\n' ' ')"
_o="$(_conf "$_N" "INFERENCE_URL=http://10.0.0.5:8080")"
[[ "$(_get VAR MODEL_URL "$_o")" == "http://10.0.0.5:8080/v1" \
   && "$(_get ENV OPENBEAST_AGENT_INFERENCE_URL "$_o")" == "http://10.0.0.5:8080/v1" ]] \
  && pass "remote INFERENCE_URL: MODEL_URL and agents follow it (unchanged)" \
  || fail "remote INFERENCE_URL derivations moved"
_o="$(_conf "$_N" $'INFERENCE_BACKEND=vllm\nINFERENCE_URL=http://10.0.0.5:8000\nINFERENCE_MODEL=my-nvfp4')"
[[ "$(_get ENV OPENBEAST_INFERENCE_MODEL "$_o")" == "my-nvfp4" \
   && -z "$(_get ENV OPENBEAST_HYDRA_UPSTREAM_MODEL "$_o")" ]] \
  && pass "vLLM: OPENBEAST_INFERENCE_MODEL is the served id (unchanged)" \
  || fail "vLLM: OPENBEAST_INFERENCE_MODEL='$(_get ENV OPENBEAST_INFERENCE_MODEL "$_o")'"
_o="$(_conf "$_N" "AGENT_ROUTER=true")"
[[ "$(_get VAR MODEL_URL "$_o")" == "http://localhost:8088/v1" ]] \
  && pass "router on: MODEL_URL is the router (unchanged)" || fail "router MODEL_URL moved"

# Side-by-side against the pre-wiring conf.sh, when a baseline ref is given.
if [[ -n "${WIRING_BASELINE_REF:-}" ]]; then
  echo ""
  echo "conf.sh — byte-identical to $WIRING_BASELINE_REF (set -euo pipefail, ${#SCENARIOS[@]} scenarios + env overrides):"
  _B="$_T/base"; _conf_box "$_B" "$WIRING_BASELINE_REF"
  # Exports added since the pinned pre-wiring baseline (CI pins v1.6.0) for
  # reasons that have nothing to do with hydra/instinct. Each entry is one
  # exact variable, never a family, so a leaked HYDRA/INSTINCT/CONSUMER line
  # can never hide behind it:
  #   OPENBEAST_CHAT_NOTIFY_ON, OPENBEAST_NTFY_PORT — beast-chat notify (#113)
  #   OB_CONF_LINTED — conf.sh's "unknown-key warnings already printed" mark
  #                    (review 2026-10-09, UX-13)
  _BASELINE_ALLOW='^ENV OPENBEAST_(CHAT_NOTIFY_ON|NTFY_PORT)=|^ENV OB_CONF_LINTED='
  if [[ -n "${WIRING_BASELINE_ALLOW:-}" ]]; then
    _BASELINE_ALLOW="$_BASELINE_ALLOW|$WIRING_BASELINE_ALLOW"
  fi
  _cmp() { # _cmp <label> <conf-text> [VAR=value ...]
    local label="$1" text="$2" a b
    shift 2
    # The two sandboxes differ only in their own path: normalise it away.
    a="$(_conf "$_B" "$text" "$@"; cat "$_B/stderr")"; a="${a//$_B/<BOX>}"
    b="$(_conf "$_N" "$text" "$@"; cat "$_N/stderr")"; b="${b//$_N/<BOX>}"
    a="$(grep -Ev "$_BASELINE_ALLOW" <<< "$a" || true)"
    b="$(grep -Ev "$_BASELINE_ALLOW" <<< "$b" || true)"
    if [[ "$a" == "$b" ]]; then
      pass "$label: identical exported env, derived values and warnings"
    else
      fail "$label: differs from $WIRING_BASELINE_REF:"
      diff <(printf '%s\n' "$a") <(printf '%s\n' "$b") | head -10 | sed 's/^/        /' || true
    fi
  }
  for _sc in "${SCENARIOS[@]}"; do _cmp "${_sc%%|*}" "${_sc#*|}"; done
  _cmp "env overrides OPENBEAST_HYDRA=false OPENBEAST_INSTINCT=0" "" \
       OPENBEAST_HYDRA=false OPENBEAST_INSTINCT=0 OPENBEAST_ROUTER_INSTINCT=shadow
  _cmp "vLLM via env only" "" OPENBEAST_INFERENCE_BACKEND=vllm \
       OPENBEAST_INFERENCE_URL=http://10.0.0.5:8000 OPENBEAST_INFERENCE_MODEL=m
fi

echo ""
echo "conf.sh — HYDRA=true derivations (plan §6.7):"
_o="$(_conf "$_N" "HYDRA=true")"
[[ "$(_get VAR MODEL_URL "$_o")" == "http://localhost:8095/v1" ]] \
  && pass "MODEL_URL -> http://localhost:8095/v1" || fail "MODEL_URL='$(_get VAR MODEL_URL "$_o")'"
[[ "$(_get ENV OPENBEAST_AGENT_INFERENCE_URL "$_o")" == "http://127.0.0.1:8095/v1" ]] \
  && pass "spawned agents -> hydra" || fail "AGENT_INFERENCE_URL='$(_get ENV OPENBEAST_AGENT_INFERENCE_URL "$_o")'"
[[ "$(_get ENV OPENBEAST_INFERENCE_MODEL "$_o")" == "beast" ]] \
  && pass "llama backend: OPENBEAST_INFERENCE_MODEL=beast (the route runner.py sends)" \
  || fail "OPENBEAST_INFERENCE_MODEL='$(_get ENV OPENBEAST_INFERENCE_MODEL "$_o")'"
[[ "$(_get ENV OPENBEAST_CONSUMER_BASE "$_o")" == "http://127.0.0.1:8095" \
   && "$(_get ENV OPENBEAST_HYDRA_URL "$_o")" == "http://127.0.0.1:8095" \
   && "$(_get ENV OPENBEAST_HYDRA_PORT "$_o")" == "8095" \
   && "$(_get ENV OPENBEAST_HYDRA_CONFIG "$_o")" == "$_N/hydra.toml" \
   && "$(_get ENV OPENBEAST_HYDRA_CALLER_TOKEN_FILE "$_o")" == "$_N/.run/hydra-caller.token" \
   && "$(_get VAR INFERENCE_URL "$_o")" == "http://127.0.0.1:8080" ]] \
  && pass "CONSUMER_BASE/HYDRA_URL/PORT/CONFIG/caller-token path exported; INFERENCE_URL still the engine" \
  || fail "hydra exports: $(grep -E 'HYDRA|CONSUMER' <<< "$_o" | tr '\n' ' ')"
_o="$(_conf "$_N" $'HYDRA=true\nAGENT_INFERENCE_URL=https://worker.example:8443/v1')"
[[ "$(_get ENV OPENBEAST_AGENT_INFERENCE_URL "$_o")" == "https://worker.example:8443/v1" ]] \
  && pass "an explicit AGENT_INFERENCE_URL still wins" || fail "explicit AGENT_INFERENCE_URL lost"
_o="$(_conf "$_N" $'HYDRA=true\nAGENT_ROUTER=true')"
[[ "$(_get VAR MODEL_URL "$_o")" == "http://localhost:8088/v1" ]] \
  && pass "router on: WebUI still dials the router (whose upstream is hydra)" || fail "router+hydra MODEL_URL"
_o="$(_conf "$_N" $'HYDRA=true\nHYDRA_PORT=9000 # comment\nHYDRA_CONFIG=conf/h.toml\nHYDRA_DEFAULT_MODEL=fleet')"
[[ "$(_get ENV OPENBEAST_HYDRA_URL "$_o")" == "http://127.0.0.1:9000" \
   && "$(_get ENV OPENBEAST_HYDRA_CONFIG "$_o")" == "$_N/conf/h.toml" \
   && "$(_get ENV OPENBEAST_INFERENCE_MODEL "$_o")" == "fleet" ]] \
  && pass "HYDRA_PORT/CONFIG (relative -> repo)/DEFAULT_MODEL honoured" || fail "hydra knobs: $(grep HYDRA <<< "$_o" | tr '\n' ' ')"
_o="$(_conf "$_N" $'HYDRA=true\nHYDRA_PORT=99999\nHYDRA_DEFAULT_MODEL=Bad Id')"
if [[ "$(_get ENV OPENBEAST_HYDRA_PORT "$_o")" == "8095" && "$(_get ENV OPENBEAST_INFERENCE_MODEL "$_o")" == "beast" ]] \
   && grep -q "HYDRA_PORT='99999' is not a port" "$_N/stderr" \
   && grep -q "HYDRA_DEFAULT_MODEL='Bad' is not a route id" "$_N/stderr"; then
  pass "a bad port / route id warns and falls back (8095 / beast)"
else
  fail "bad hydra values: $(cat "$_N/stderr") $(grep HYDRA <<< "$_o" | tr '\n' ' ')"
fi
# A conf-FILE HYDRA_DEFAULT_MODEL must reach hydra itself, not only the
# agents: hydra_core.implicit_raw reads OPENBEAST_HYDRA_DEFAULT_MODEL.
_o="$(_conf "$_N" $'HYDRA=true\nHYDRA_DEFAULT_MODEL=fleet')"
_dr="$(env -i HOME="$_N/home" PATH=/usr/bin:/bin bash -c 'set -euo pipefail; REPO_DIR="$1"
        source "$1/scripts/lib/conf.sh" 2>/dev/null
        nice -n 19 python3 -c "import os, sys; sys.path.insert(0, sys.argv[1]); import hydra_core as c
print(c.implicit_raw(dict(os.environ))[\"hydra\"][\"default_route\"])" "$2/agents"' _ "$_N" "$REPO_DIR" 2>/dev/null || true)"
if [[ "$(_get ENV OPENBEAST_HYDRA_DEFAULT_MODEL "$_o")" == "fleet" && "$_dr" == "fleet" ]]; then
  pass "HYDRA_DEFAULT_MODEL from openbeast.conf reaches hydra (its implicit default_route is 'fleet')"
else
  fail "HYDRA_DEFAULT_MODEL=fleet: exported='$(_get ENV OPENBEAST_HYDRA_DEFAULT_MODEL "$_o")' default_route='$_dr'"
fi
# HYDRA_READY_GRACE is read in (( )): a leading zero must not turn it octal.
_grace() { env -i HOME="$_N/home" PATH=/usr/bin:/bin OPENBEAST_HYDRA_READY_GRACE="$1" bash -c \
             'set -euo pipefail; REPO_DIR="$1"; source "$1/scripts/lib/conf.sh" 2>/dev/null; echo "$HYDRA_READY_GRACE"' _ "$_N"; }
if [[ "$(_grace 08)" == 8 && "$(_grace 010)" == 10 && "$(_grace 09)" == 9 && "$(_grace 'x')" == 60 \
      && "$(_grace '')" == 60 && "$(_grace 1234567)" == 60 ]]; then
  pass "HYDRA_READY_GRACE is base 10 (08 -> 8, 010 -> 10), junk -> 60"
else
  fail "HYDRA_READY_GRACE: 08=$(_grace 08) 010=$(_grace 010) x=$(_grace x)"
fi
# A shell that once sourced conf.sh with HYDRA=true keeps its exports; with
# HYDRA now off they must not point anything at a dead hydra.
_o="$(_conf "$_N" "HYDRA=false" OPENBEAST_CONSUMER_BASE=http://127.0.0.1:8095 \
        OPENBEAST_HYDRA_URL=http://127.0.0.1:8095 OPENBEAST_HYDRA_CALLER_TOKEN_FILE=/x/hydra-caller.token)"
if grep -qE '^ENV (OPENBEAST_CONSUMER_BASE|OPENBEAST_HYDRA_URL|OPENBEAST_HYDRA_CALLER_TOKEN_FILE)=' <<< "$_o"; then
  fail "HYDRA=false kept stale hydra exports: $(grep -E 'CONSUMER|HYDRA' <<< "$_o" | tr '\n' ' ')"
else
  pass "HYDRA=false drops stale CONSUMER_BASE / HYDRA_URL / caller-token exports"
fi
# The same shell sources conf.sh twice (the owner's "source conf.sh before
# docker compose", then ./start.sh -d later): HYDRA/INSTINCT flipped off, or
# HYDRA_PORT moved, in openbeast.conf in between. The first source's own
# OPENBEAST_HYDRA=true export used to read back as an operator override and
# keep hydra on. A value the operator exports himself still wins.
_resource() { # _resource <conf-1> <conf-2> [shell run between the two sources]
  env -i HOME="$_N/home" PATH=/usr/bin:/bin bash -c 'set -euo pipefail; REPO_DIR="$1"
    printf "SEARXNG_SECRET=stub\n%s\n" "$2" > "$1/openbeast.conf"
    source "$1/scripts/lib/conf.sh" 2>/dev/null
    printf "SEARXNG_SECRET=stub\n%s\n" "$3" > "$1/openbeast.conf"
    eval "$4"
    source "$1/scripts/lib/conf.sh" 2>/dev/null
    echo "H=$HYDRA I=$INSTINCT URL=${HYDRA_URL:-} CB=${OPENBEAST_CONSUMER_BASE-unset}" \
         "OH=${OPENBEAST_HYDRA-unset} OI=${OPENBEAST_INSTINCT-unset} OIP=${OPENBEAST_INSTINCT_PORT-unset}" \
         "IM=${OPENBEAST_INFERENCE_MODEL-unset} UP=${OPENBEAST_HYDRA_UPSTREAM_MODEL-unset}" \
         "D=$(compgen -e | grep -c "^OPENBEAST_DERIVED_" || true)"' _ "$_N" "$1" "$2" "${3:-}"
}
_V=$'INFERENCE_BACKEND=vllm\nINFERENCE_URL=http://10.0.0.5:8000\nINFERENCE_MODEL=m1'
_r="$(_resource $'HYDRA=true\nINSTINCT=true\n'"$_V" $'HYDRA=false\nINSTINCT=false\n'"$_V")"
[[ "$_r" == "H=false I=false URL= CB=unset OH=unset OI=unset OIP=unset IM=m1 UP=unset D=0" ]] \
  && pass "re-sourced after HYDRA/INSTINCT flip off: both off, no stale export, served id back" \
  || fail "flip off in one shell: $_r"
_r="$(_resource $'HYDRA=true\nHYDRA_PORT=9001' $'HYDRA=true\nHYDRA_PORT=9002')"
[[ "$_r" == "H=true I=false URL=http://127.0.0.1:9002 "* ]] \
  && pass "re-sourced after HYDRA_PORT moved: the conf's new port wins" || fail "port move: $_r"
_r="$(_resource $'HYDRA=true\nINSTINCT=true' $'HYDRA=true\nINSTINCT=true' 'export OPENBEAST_HYDRA=false')"
[[ "$_r" == "H=false I=true "* ]] \
  && pass "…an operator's own OPENBEAST_HYDRA=false in that shell still wins (control)" \
  || fail "operator override lost: $_r"
_r="$(env -i HOME="$_N/home" PATH=/usr/bin:/bin OPENBEAST_HYDRA=true bash -c 'set -euo pipefail; REPO_DIR="$1"
  printf "SEARXNG_SECRET=stub\nHYDRA=false\n" > "$1/openbeast.conf"
  source "$1/scripts/lib/conf.sh" 2>/dev/null; source "$1/scripts/lib/conf.sh" 2>/dev/null; echo "$HYDRA"' _ "$_N")"
[[ "$_r" == "true" ]] && pass "…and an env override set before the first source survives a re-source" \
  || fail "env override lost across a re-source: $_r"

# The operator's OWN env id (not the conf's): hydra on overwrites it with the
# route and kept no record, so flipping hydra off in that shell left agents
# sending `beast` to a bare vLLM, which 404s unknown ids.
_opm() { # _opm <shell run between the two sources>
  env -i HOME="$_N/home" PATH=/usr/bin:/bin OPENBEAST_INFERENCE_MODEL=opm bash -c 'set -euo pipefail; REPO_DIR="$1"
    _v=$'"'"'INFERENCE_BACKEND=vllm\nINFERENCE_URL=http://10.0.0.5:8000'"'"'
    printf "SEARXNG_SECRET=stub\nHYDRA=true\n%s\n" "$_v" > "$1/openbeast.conf"
    source "$1/scripts/lib/conf.sh" 2>/dev/null; a="$OPENBEAST_INFERENCE_MODEL/$OPENBEAST_HYDRA_UPSTREAM_MODEL"
    source "$1/scripts/lib/conf.sh" 2>/dev/null; a="$a $OPENBEAST_INFERENCE_MODEL/$OPENBEAST_HYDRA_UPSTREAM_MODEL"
    printf "SEARXNG_SECRET=stub\nHYDRA=false\n%s\n" "$_v" > "$1/openbeast.conf"
    eval "$2"
    source "$1/scripts/lib/conf.sh" 2>/dev/null
    echo "$a H=$HYDRA IM=${OPENBEAST_INFERENCE_MODEL-unset} UP=${OPENBEAST_HYDRA_UPSTREAM_MODEL-unset}" \
         "S=${OPENBEAST_HYDRA_OPERATOR_MODEL-unset}"' _ "$_N" "${1:-}"
}
_r="$(_opm)"
[[ "$_r" == "beast/opm beast/opm H=false IM=opm UP=unset S=unset" ]] \
  && pass "hydra flipped off in one shell: the operator's own env id comes back, not the route" \
  || fail "operator id after flip off: $_r"
_r="$(_opm 'export OPENBEAST_INFERENCE_MODEL=newer')"
[[ "$_r" == *" H=false IM=newer UP=unset S=unset" ]] \
  && pass "…but an id he exported since is his and is kept (control)" \
  || fail "operator's newer id lost: $_r"

# vLLM: the served id rides OPENBEAST_HYDRA_UPSTREAM_MODEL, and a child that
# re-sources conf.sh with the parent's exports (start.sh -d forwards every
# OPENBEAST_*) must recover it rather than mistake the route for it.
_o="$(_conf "$_N" $'HYDRA=true\nINFERENCE_BACKEND=vllm\nINFERENCE_URL=http://10.0.0.5:8000\nINFERENCE_MODEL=my-nvfp4')"
_child="$(_conf "$_N" $'HYDRA=true\nINFERENCE_BACKEND=vllm\nINFERENCE_URL=http://10.0.0.5:8000\nINFERENCE_MODEL=my-nvfp4' \
          OPENBEAST_INFERENCE_MODEL=beast OPENBEAST_HYDRA_UPSTREAM_MODEL=my-nvfp4)"
if [[ "$(_get ENV OPENBEAST_HYDRA_UPSTREAM_MODEL "$_o")" == "my-nvfp4" \
      && "$(_get ENV OPENBEAST_INFERENCE_MODEL "$_o")" == "beast" \
      && "$(_get ENV OPENBEAST_HYDRA_UPSTREAM_MODEL "$_child")" == "my-nvfp4" \
      && "$(_get VAR INFERENCE_MODEL "$_child")" == "my-nvfp4" ]]; then
  pass "vLLM under hydra: upstream id kept apart from the route, stable across a re-source"
else
  fail "upstream id: parent=$(_get ENV OPENBEAST_HYDRA_UPSTREAM_MODEL "$_o") child=$(_get VAR INFERENCE_MODEL "$_child")"
fi

echo ""
echo "conf.sh — INSTINCT keys:"
_o="$(_conf "$_N" $'INSTINCT=true\nINSTINCT_PORT=8194\nROUTER_INSTINCT=Shadow')"
[[ "$(_get ENV OPENBEAST_INSTINCT "$_o")" == "true" && "$(_get ENV OPENBEAST_INSTINCT_PORT "$_o")" == "8194" ]] \
  && pass "INSTINCT=true exports OPENBEAST_INSTINCT(_PORT) for the dashboard" || fail "instinct exports"
if grep -q '^ENV.*ROUTER_INSTINCT' <<< "$_o"; then
  fail "ROUTER_INSTINCT leaked into the exported environment"
else
  pass "ROUTER_INSTINCT is never exported (the router's own env only)"
fi
_ri() { env -i HOME="$_N/home" PATH=/usr/bin:/bin OPENBEAST_ROUTER_INSTINCT="$1" bash -c \
          'set -euo pipefail; REPO_DIR="$1"; source "$1/scripts/lib/conf.sh" 2>/dev/null; echo "$ROUTER_INSTINCT"' _ "$_N"; }
_conf "$_N" "" >/dev/null
if [[ "$(_ri Shadow)" == shadow && "$(_ri 'enforce # x')" == enforce && "$(_ri yolo)" == off && "$(_ri '')" == off ]]; then
  pass "ROUTER_INSTINCT: case/comment tolerant; unknown -> off (fail safe)"
else
  fail "ROUTER_INSTINCT parse: Shadow=$(_ri Shadow) yolo=$(_ri yolo)"
fi
_o="$(_conf "$_N" $'INSTINCT_CONFIG=~/ins.toml')"
_ic="$(env -i HOME="$_N/home" PATH=/usr/bin:/bin bash -c 'set -euo pipefail; REPO_DIR="$1"; source "$1/scripts/lib/conf.sh" 2>/dev/null; echo "$INSTINCT_CONFIG"' _ "$_N")"
[[ "$_ic" == "$_N/home/ins.toml" ]] && pass "INSTINCT_CONFIG expands ~" || fail "INSTINCT_CONFIG='$_ic'"

# ── start.sh / healthcheck.sh structure ─────────────────────────────────────
echo ""
echo "start.sh / healthcheck.sh wiring:"
if grep -A3 'OPENBEAST_ROUTER_PORT="$ROUTER_PORT"' "$REPO_DIR/start.sh" | grep -q 'OPENBEAST_LLAMA_UPSTREAM="$CONSUMER_BASE"' \
   && grep -B1 -A2 'OPENBEAST_REPO_DIR="$SCRIPT_DIR"' "$REPO_DIR/start.sh" | grep -q 'OPENBEAST_LLAMA_UPSTREAM="$CONSUMER_BASE"' \
   && ! grep -q 'OPENBEAST_LLAMA_UPSTREAM="$LLAMA_BASE"' "$REPO_DIR/start.sh"; then
  pass "router and gate launch with OPENBEAST_LLAMA_UPSTREAM=\$CONSUMER_BASE"
else
  fail "a start.sh consumer still points at LLAMA_BASE"
fi
if grep -q '^CONSUMER_BASE="$LLAMA_BASE"$' "$REPO_DIR/start.sh" \
   && grep -q '^\[\[ "${HYDRA:-false}" == "true" \]\] && CONSUMER_BASE="$HYDRA_URL"$' "$REPO_DIR/start.sh" \
   && ! grep -q 'OPENBEAST_CONSUMER_BASE' "$REPO_DIR/start.sh"; then
  pass "start.sh: CONSUMER_BASE is exactly LLAMA_BASE, or HYDRA_URL from THIS conf (never an inherited export)"
else
  fail "start.sh: CONSUMER_BASE derivation missing or reads an inherited export"
fi
if grep -q 'OPENBEAST_LLAMA_UPSTREAM="${CONSUMER_BASE:-$INFERENCE_URL}"' "$REPO_DIR/scripts/healthcheck.sh" \
   && ! grep -q 'OPENBEAST_LLAMA_UPSTREAM="$INFERENCE_URL"' "$REPO_DIR/scripts/healthcheck.sh"; then
  pass "healthcheck.sh: the gate relaunch no longer uses bare \$INFERENCE_URL (the :396 bug)"
else
  fail "healthcheck.sh gate relaunch still bypasses hydra"
fi
# hydra is launched BEFORE the inference wait (both paths), and the managed
# path then waits HYDRA_READY_GRACE for it to route.
_lh="$(grep -n '^launch_hydra$' "$REPO_DIR/start.sh" | head -1 | cut -d: -f1 || true)"
_wu="$(grep -n 'Waiting for the $(ob_backend_label) server at $LLAMA_BASE (not managed here' "$REPO_DIR/start.sh" | tail -1 | cut -d: -f1 || true)"
_lw="$(grep -n 'if ! launch_and_wait; then' "$REPO_DIR/start.sh" | head -1 | cut -d: -f1 || true)"
if [[ -n "$_lh" && -n "$_wu" && -n "$_lw" && $_lh -lt $_wu && $_lh -lt $_lw ]]; then
  pass "start.sh launches hydra before the managed and unmanaged inference waits"
else
  fail "start.sh: launch_hydra ($_lh) not before the waits ($_wu, $_lw)"
fi
if grep -q 'env ${_router_env\[@\]+"${_router_env\[@\]}"} python3 "$SCRIPT_DIR/agents/router.py"' "$REPO_DIR/start.sh" \
   && grep -q '_router_env+=(ROUTER_CLASSIFY_MODEL=classify)' "$REPO_DIR/start.sh" \
   && grep -q '_router_env+=(ROUTER_CLASSIFY_MODEL="$HYDRA_DEFAULT_MODEL")' "$REPO_DIR/start.sh"; then
  pass "router gets ROUTER_INSTINCT / ROUTER_CLASSIFY_MODEL in its own env only"
else
  fail "router env wiring missing"
fi
for _f in start.sh stop.sh scripts/healthcheck.sh scripts/doctor.sh scripts/setup-tailscale.sh scripts/lib/conf.sh scripts/lib/backend.sh; do
  if bash -n "$REPO_DIR/$_f" 2>/dev/null; then :; else fail "$_f: bash -n"; fi
done

# ── Sandboxed stop.sh / healthcheck.sh ──────────────────────────────────────
# A fake HTTP server: one process, several ephemeral listeners, each with its
# own behaviour (ok = 200 with a body every probe accepts; h200 / h503 = a
# hydra-shaped /health). Ports are written to a file.
cat > "$_T/fake.py" <<'PY'
import http.server, json, os, sys, threading
out = sys.argv[1]; modes = sys.argv[2:]
TOKEN_FILE = os.path.join(os.path.dirname(out), "hydra-local.token")
STATUS = {
    "nodes": {"rig": {"host": "127.0.0.1", "engine": "llama", "key": "none", "drained": None,
                      "slots": 1, "inflight": 0},
              "spark": {"host": "100.64.0.7", "engine": "vllm", "key": "set", "drained": None,
                        "slots": 8, "inflight": 0}},
    "deployments": {"local@rig": {"node": "rig", "state": "READY", "breaker": "closed", "inflight": 0,
                                  "slots": 1, "conformance": "n/a", "routable": True, "upstream": "local"},
                    "q@spark": {"node": "spark", "state": "MISMATCH", "breaker": "closed", "inflight": 0,
                                "slots": 8, "conformance": "missing", "routable": False,
                                "upstream": "qwen-typo"}},
    "routes": {"beast": {"routable": True, "candidates_per_group": [1]},
               "beast:max": {"routable": False, "candidates_per_group": [0]}}}
class H(http.server.BaseHTTPRequestHandler):
    mode = "ok"
    def log_message(self, *a): pass
    def do_GET(self):
        m = self.server.mode
        if m == "hydra" and self.path == "/hydra/status":
            try:
                ok = self.headers.get("X-OpenBeast-Local") == open(TOKEN_FILE).read().strip()
            except OSError:
                ok = False
            code, body = (200, STATUS) if ok else (403, {"error": {"message": "local token"}})
        elif m == "hydra":
            code, body = 200, {"status": "ok"}
        elif m == "h503":
            code, body = 503, {"status": "loading"}
        elif m == "h200":
            code, body = 200, {"status": "ok"}
        else:
            code, body = 200, {"status": "ok", "version": "x", "searx": "searx"}
        b = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
ports = []
for m in modes:
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); s.mode = m
    threading.Thread(target=s.serve_forever, daemon=True).start()
    ports.append(str(s.server_address[1]))
with open(out + ".tmp", "w") as f: f.write(" ".join(ports))
import os; os.replace(out + ".tmp", out)
threading.Event().wait(300)
PY
echo "local-tok-123" > "$_T/hydra-local.token"
nice -n 19 python3 "$_T/fake.py" "$_T/ports" ok h200 h503 hydra &
_PIDS="$_PIDS $!"
for _i in $(seq 1 50); do [[ -s "$_T/ports" ]] && break; sleep 0.1; done
read -r P_OK P_200 P_503 P_HY < "$_T/ports" || true   # no trailing newline
# A port nothing listens on: bind, note, close.
P_DEAD="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"

_box() { # _box <dir> [git-ref]   — stop.sh, healthcheck.sh, libs, stubs
  local d="$1" ref="${2:-}" c f
  mkdir -p "$d/scripts/lib" "$d/.run" "$d/bin" "$d/home" "$d/agents"
  for f in start.sh stop.sh scripts/healthcheck.sh scripts/doctor.sh scripts/instinct.sh scripts/lib/conf.sh scripts/lib/net.sh \
           scripts/lib/backend.sh scripts/lib/proc.sh scripts/lib/extensions.sh scripts/lib/curl_auth.sh \
           scripts/lib/portown.sh; do
    if [[ -n "$ref" ]]; then
      git -C "$REPO_DIR" show "$ref:$f" > "$d/$f" 2>/dev/null || cp "$REPO_DIR/$f" "$d/$f"
    else
      cp "$REPO_DIR/$f" "$d/$f"
    fi
  done
  cp -n "$REPO_DIR"/scripts/lib/*.sh "$d/scripts/lib/"     # the libs no case changes
  # doctor's "Remote skills" row (the import gate, 2026-10) reads the ledger
  # through skill-import.sh; a worktree box gets the real ones.
  if [[ -z "$ref" ]]; then
    cp "$REPO_DIR/scripts/skill-import.sh" "$d/scripts/"
    cp "$REPO_DIR/scripts/lib/skill_import.py" "$d/scripts/lib/"
    cp -r "$REPO_DIR/skills" "$d/skills"
  fi
  printf 'SEARXNG_SECRET=stub\n' > "$d/openbeast.conf"; chmod 600 "$d/openbeast.conf"
  for c in docker tailscale nvidia-smi sudo systemctl systemd-run smartctl; do
    printf '#!/bin/bash\nexit 1\n' > "$d/bin/$c"; chmod +x "$d/bin/$c"
  done
  for c in pkill pgrep; do
    printf '#!/bin/bash\nfor a in "$@"; do [[ "$a" == *%s* ]] && exec /usr/bin/%s "$@"; done\nexit 1\n' \
      "$d" "$c" > "$d/bin/$c"
    chmod +x "$d/bin/$c"
  done
  # curl reaches only this file's fake servers.
  cat > "$d/bin/curl" <<EOF
#!/bin/bash
for a in "\$@"; do
  case "\$a" in *127.0.0.1:$P_OK*|*127.0.0.1:$P_200*|*127.0.0.1:$P_503*|*127.0.0.1:$P_HY*|*127.0.0.1:$P_DEAD*) exec /usr/bin/curl "\$@" ;; esac
done
exit 7
EOF
  chmod +x "$d/bin/curl"
  # agents/*.py stubs: record how they were launched, then exit.
  for f in hydra edge; do
    cat > "$d/agents/$f.py" <<EOF
import os, sys, json
if "--check" in sys.argv and os.environ.get("STUB_CHECK_FAIL"):
    print(json.dumps({"ok": False, "errors": ["nodes.rig.url: stub says no"], "warnings": []}))
    sys.exit(1)
if "--check" in sys.argv:
    print(json.dumps({"ok": True, "config": "abc", "source": "stub", "warnings": ["stub warning"],
                      "nodes": 2, "deployments": 2, "routes": 2, "classify_route": False}))
    sys.exit(0)
with open("$d/.run/launched-$f", "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "upstream": os.environ.get("OPENBEAST_LLAMA_UPSTREAM"),
                         "caller": os.environ.get("OPENBEAST_HYDRA_CALLER_TOKEN_FILE")}) + "\n")
EOF
  done
}
_env_hc=(OPENBEAST_INFERENCE_URL="http://127.0.0.1:$P_DEAD" OPENBEAST_INFERENCE_MANAGED=false
         MCPO_URL="http://127.0.0.1:$P_OK" WEBUI_URL="http://127.0.0.1:$P_OK" SEARXNG_URL="http://127.0.0.1:$P_OK")
_run() { # _run <dir> <script> [args]   (extra env: RUN_ENV array)
  local d="$1"; shift
  env -i HOME="$d/home" PATH="$d/bin:/usr/bin:/bin" ${RUN_ENV[@]+"${RUN_ENV[@]}"} \
    timeout 90 nice -n 19 bash "$@" 2>&1 || true
}

echo ""
echo "stop.sh:"
_S="$_T/stop"; _box "$_S"
RUN_ENV=()
_o="$(_run "$_S" "$_S/stop.sh")"
if grep -qiE 'hydra|instinct' <<< "$_o"; then
  fail "stop.sh with hydra/instinct off mentions them: $(grep -iE 'hydra|instinct' <<< "$_o" | tr '\n' ' ')"
else
  pass "stop.sh with hydra/instinct off: not a word about them"
fi
# On: a fake hydra, a fake instinct service, a fake scorer and a stranger.
nice -n 19 python3 -c 'import time; time.sleep(120)' "$_S/agents/hydra.py" &
_hy=$!; _PIDS="$_PIDS $_hy"
nice -n 19 python3 -c 'import time; time.sleep(120)' instinct.server &
_in=$!; _PIDS="$_PIDS $_in"
echo "$_hy" > "$_S/.run/hydra.pid"; echo "$_in" > "$_S/.run/instinct.pid"
cp /dev/null "$_S/scripts/serve-instinct-scorer.sh"
printf 'import time\ntime.sleep(120)\n' > "$_S/scripts/serve-instinct-scorer.sh"
nice -n 19 python3 "$_S/scripts/serve-instinct-scorer.sh" &
_sc=$!; _PIDS="$_PIDS $_sc"
echo "$_sc" > "$_S/.run/instinct-scorer.pid"
sleep 0.3
RUN_ENV=(OPENBEAST_HYDRA=true OPENBEAST_INSTINCT=true OPENBEAST_INSTINCT_SCORER=true)
_o="$(_run "$_S" "$_S/stop.sh")"
sleep 0.5
if ! kill -0 "$_hy" 2>/dev/null && ! kill -0 "$_in" 2>/dev/null && ! kill -0 "$_sc" 2>/dev/null; then
  pass "stop.sh stops hydra, the instinct service and its scorer"
else
  fail "stop.sh left: hydra=$(kill -0 "$_hy" 2>/dev/null && echo alive) instinct=$(kill -0 "$_in" 2>/dev/null && echo alive) scorer=$(kill -0 "$_sc" 2>/dev/null && echo alive): $_o"
fi
if [[ ! -e "$_S/.run/hydra.pid" && ! -e "$_S/.run/instinct.pid" && ! -e "$_S/.run/instinct-scorer.pid" ]]; then
  pass "stop.sh removes hydra.pid / instinct.pid / instinct-scorer.pid"
else
  fail "pidfiles left: $(ls "$_S/.run")"
fi
# The scorer is killed by IDENTITY-checked pid only: a stranger on the record lives.
nice -n 19 python3 -c 'import time; time.sleep(120)' &
_st=$!; _PIDS="$_PIDS $_st"
echo "$_st" > "$_S/.run/instinct-scorer.pid"
RUN_ENV=(OPENBEAST_INSTINCT_SCORER=true)
_run "$_S" "$_S/stop.sh" >/dev/null
if kill -0 "$_st" 2>/dev/null; then
  pass "stop.sh leaves a stranger holding a stale instinct-scorer.pid alone"
else
  fail "stop.sh killed a stranger through instinct-scorer.pid"
fi

echo ""
echo "healthcheck.sh:"
_H="$_T/hc"; _box "$_H"
RUN_ENV=("${_env_hc[@]}")
_o="$(_run "$_H" "$_H/scripts/healthcheck.sh")"
if grep -qiE 'hydra|instinct' <<< "$_o"; then
  fail "healthcheck with hydra/instinct off mentions them"
else
  pass "healthcheck with hydra/instinct off: not a word about them"
fi
RUN_ENV=("${_env_hc[@]}" OPENBEAST_HYDRA=true OPENBEAST_HYDRA_PORT="$P_200")
_o="$(_run "$_H" "$_H/scripts/healthcheck.sh")"
grep -q "OK   beast-hydra (http://127.0.0.1:$P_200)" <<< "$_o" \
  && pass "hydra 200 -> OK" || fail "hydra 200: $(grep -i hydra <<< "$_o")"
rm -f "$_H/.run/launched-hydra"
RUN_ENV=("${_env_hc[@]}" OPENBEAST_HYDRA=true OPENBEAST_HYDRA_PORT="$P_503")
_o="$(_run "$_H" "$_H/scripts/healthcheck.sh" --restart)"
if grep -q "WARN beast-hydra" <<< "$_o" && [[ ! -e "$_H/.run/launched-hydra" ]]; then
  pass "hydra 503 -> WARN, and --restart does NOT relaunch it (a restart cannot fix a 503)"
else
  fail "hydra 503: $(grep -i hydra <<< "$_o" | tr '\n' ' ') launched=$(cat "$_H/.run/launched-hydra" 2>/dev/null)"
fi
RUN_ENV=("${_env_hc[@]}" OPENBEAST_HYDRA=true OPENBEAST_HYDRA_PORT="$P_DEAD")
_o="$(_run "$_H" "$_H/scripts/healthcheck.sh" --restart)"
if grep -q "DOWN beast-hydra" <<< "$_o" && [[ -s "$_H/.run/launched-hydra" ]] \
   && [[ "$(stat -c '%a' "$_H/.run/hydra-caller.token" 2>/dev/null)" == "600" ]]; then
  pass "hydra not answering -> DOWN, --restart relaunches it (caller token minted 0600 when missing)"
else
  fail "hydra down: $(grep -i hydra <<< "$_o" | tr '\n' ' ') launched=$(cat "$_H/.run/launched-hydra" 2>/dev/null)"
fi
# The :396 fix, behaviourally: a relaunched gate's upstream is hydra under
# HYDRA=true (it named INFERENCE_URL, bypassing hydra) — and exactly
# INFERENCE_URL without hydra (negative control).
rm -f "$_H/.run/launched-edge"
RUN_ENV=("${_env_hc[@]}" OPENBEAST_EDGE_GATE=true OPENBEAST_EDGE_PORT="$P_DEAD"
         OPENBEAST_HYDRA=true OPENBEAST_HYDRA_PORT="$P_200")
_run "$_H" "$_H/scripts/healthcheck.sh" --restart >/dev/null
if grep -q "\"upstream\": \"http://127.0.0.1:$P_200\"" "$_H/.run/launched-edge" 2>/dev/null \
   && grep -q "\"caller\": \"$_H/.run/hydra-caller.token\"" "$_H/.run/launched-edge"; then
  pass "a watchdog-relaunched gate's upstream is hydra, with the caller-token path"
else
  fail "relaunched gate: $(cat "$_H/.run/launched-edge" 2>/dev/null)"
fi
rm -f "$_H/.run/launched-edge"
RUN_ENV=("${_env_hc[@]}" OPENBEAST_EDGE_GATE=true OPENBEAST_EDGE_PORT="$P_DEAD")
_run "$_H" "$_H/scripts/healthcheck.sh" --restart >/dev/null
if grep -q "\"upstream\": \"http://127.0.0.1:$P_DEAD\", \"caller\": null" "$_H/.run/launched-edge" 2>/dev/null; then
  pass "without hydra the relaunched gate's upstream is INFERENCE_URL, no caller token (unchanged)"
else
  fail "relaunched gate without hydra: $(cat "$_H/.run/launched-edge" 2>/dev/null)"
fi
rm -f "$_H/.run/launched-edge"
RUN_ENV=("${_env_hc[@]}" OPENBEAST_EDGE_GATE=true OPENBEAST_EDGE_PORT="$P_DEAD" OPENBEAST_HYDRA=false
         OPENBEAST_CONSUMER_BASE="http://127.0.0.1:$P_200"
         OPENBEAST_HYDRA_CALLER_TOKEN_FILE="$_H/.run/hydra-caller.token")
_run "$_H" "$_H/scripts/healthcheck.sh" --restart >/dev/null
if grep -q "\"upstream\": \"http://127.0.0.1:$P_DEAD\", \"caller\": null" "$_H/.run/launched-edge" 2>/dev/null; then
  pass "HYDRA off + stale hydra exports: the relaunched gate still goes to INFERENCE_URL"
else
  fail "stale OPENBEAST_CONSUMER_BASE steered the relaunched gate: $(cat "$_H/.run/launched-edge" 2>/dev/null)"
fi

# ── ops F5 (2026-10-09): the agent router is supervised too ────────────────
# With AGENT_ROUTER=true WebUI's model endpoint is the router, and nothing
# watched it: healthcheck had no branch, so a dead router read "All N services
# healthy" and `--restart` (doctor's advice) did nothing.
echo ""
echo "healthcheck.sh — agent router:"
_RB="$_T/rt"; _box "$_RB"
P_RT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
# This box's curl may also reach the port the stub router will bind.
sed -i "s/\*127\.0\.0\.1:$P_DEAD\*)/*127.0.0.1:$P_DEAD*|*127.0.0.1:$P_RT*)/" "$_RB/bin/curl"
# agents/router.py stub: records the environment it was launched with; with
# STUB_ROUTER_SERVE it then binds its port and answers 502 — what the real
# router says while the model is down — for a minute.
cat > "$_RB/agents/router.py" <<EOF
import http.server, json, os, threading
keys = ("OPENBEAST_ROUTER_PORT", "OPENBEAST_LLAMA_UPSTREAM", "OPENBEAST_MCPO_URL", "ROUTER_INSTINCT",
        "INSTINCT_URL", "ROUTER_CLASSIFY_MODEL", "OPENBEAST_HYDRA_CALLER_TOKEN_FILE")
with open("$_RB/.run/launched-router", "a") as fh:
    fh.write(json.dumps({k: os.environ.get(k) for k in keys}) + "\n")
if os.environ.get("STUB_ROUTER_SERVE"):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(502); self.send_header("Content-Length", "0"); self.end_headers()
    s = http.server.HTTPServer(("127.0.0.1", int(os.environ["OPENBEAST_ROUTER_PORT"])), H)
    threading.Timer(60, lambda: os._exit(0)).start()
    s.serve_forever()
EOF
_rt_launched() { cat "$_RB/.run/launched-router" 2>/dev/null || true; }
RUN_ENV=("${_env_hc[@]}")
_o="$(_run "$_RB" "$_RB/scripts/healthcheck.sh" --restart)"
if ! grep -qi "router" <<< "$_o" && [[ -z "$(_rt_launched)" ]]; then
  pass "AGENT_ROUTER off: no router row, nothing launched (control)"
else
  fail "router branch ran with AGENT_ROUTER off: $(grep -i router <<< "$_o" | tr '\n' ' ')"
fi
# Alive = ANY HTTP answer (it forwards /health upstream: 503 while the model
# loads, its own 502 with the model down). Only silence is DOWN.
RUN_ENV=("${_env_hc[@]}" OPENBEAST_AGENT_ROUTER=true OPENBEAST_ROUTER_PORT="$P_503")
_o="$(_run "$_RB" "$_RB/scripts/healthcheck.sh" --restart)"
if grep -q "OK   Agent router (http://127.0.0.1:$P_503)" <<< "$_o" && [[ -z "$(_rt_launched)" ]]; then
  pass "a router answering 503 (model loading upstream) is alive: OK, and --restart leaves it alone"
else
  fail "router 503: $(grep -i router <<< "$_o" | tr '\n' ' ') launched=$(_rt_launched)"
fi
# Dead, report-only: DOWN and counted, nothing launched.
RUN_ENV=("${_env_hc[@]}" OPENBEAST_AGENT_ROUTER=true OPENBEAST_ROUTER_PORT="$P_RT")
_o="$(_run "$_RB" "$_RB/scripts/healthcheck.sh")"
if grep -q "DOWN Agent router (http://127.0.0.1:$P_RT)" <<< "$_o" && grep -qE '[1-9][0-9]* of [0-9]+ services unhealthy' <<< "$_o" \
   && [[ -z "$(_rt_launched)" ]]; then
  pass "a dead router is DOWN and counted unhealthy (was: 'All N services healthy'); without --restart nothing is launched"
else
  fail "dead router, report-only: $(grep -iE 'router|healthy' <<< "$_o" | tr '\n' ' ') launched=$(_rt_launched)"
fi
# Dead, --restart: relaunched with start.sh's environment, pid recorded.
RUN_ENV=("${_env_hc[@]}" OPENBEAST_AGENT_ROUTER=true OPENBEAST_ROUTER_PORT="$P_RT" STUB_ROUTER_SERVE=1)
_o="$(_run "$_RB" "$_RB/scripts/healthcheck.sh" --restart)"
_rt_pid="$(cat "$_RB/.run/router.pid" 2>/dev/null || true)"
[[ "$_rt_pid" =~ ^[0-9]+$ ]] && _PIDS="$_PIDS $_rt_pid"
if grep -q "→ restarted (pid $_rt_pid)" <<< "$_o" && kill -0 "$_rt_pid" 2>/dev/null; then
  pass "--restart relaunches a dead router and records its pid in .run/router.pid"
else
  fail "router relaunch: $(grep -A4 -i 'router' <<< "$_o" | tr '\n' ' ') pid='$_rt_pid'"
fi
if [[ "$(_rt_launched)" == "{\"OPENBEAST_ROUTER_PORT\": \"$P_RT\", \"OPENBEAST_LLAMA_UPSTREAM\": \"http://127.0.0.1:$P_DEAD\", \"OPENBEAST_MCPO_URL\": \"http://127.0.0.1:3001\", \"ROUTER_INSTINCT\": null, \"INSTINCT_URL\": null, \"ROUTER_CLASSIFY_MODEL\": null, \"OPENBEAST_HYDRA_CALLER_TOKEN_FILE\": null}" ]]; then
  pass "…with start.sh's environment: its port, INFERENCE_URL upstream, the tool server on the probe host, no opt-in extras"
else
  fail "relaunched router environment: $(_rt_launched)"
fi
grep -q "healthcheck --restart: relaunching agent router" "$_RB/.run/stack.log" 2>/dev/null \
  && pass "…its output goes to .run/stack.log, behind a dated marker" \
  || fail "the router relaunch left no marker in .run/stack.log"
RUN_ENV=("${_env_hc[@]}" OPENBEAST_AGENT_ROUTER=true OPENBEAST_ROUTER_PORT="$P_RT")
_o="$(_run "$_RB" "$_RB/scripts/healthcheck.sh")"
grep -q "OK   Agent router (http://127.0.0.1:$P_RT)" <<< "$_o" \
  && pass "…and the next check reads it OK (it answers 502: the model is down, the router is not)" \
  || fail "relaunched router not seen: $(grep -i router <<< "$_o" | tr '\n' ' ')"
[[ "$_rt_pid" =~ ^[0-9]+$ ]] && kill "$_rt_pid" 2>/dev/null || true
for _i in $(seq 1 30); do kill -0 "$_rt_pid" 2>/dev/null || break; sleep 0.1; done
# Under HYDRA=true the upstream is hydra, the classify body names a route, and
# the instinct ceiling rides along — the three extras start.sh adds.
rm -f "$_RB/.run/launched-router"
RUN_ENV=("${_env_hc[@]}" OPENBEAST_AGENT_ROUTER=true OPENBEAST_ROUTER_PORT="$P_RT"
         OPENBEAST_HYDRA=true OPENBEAST_HYDRA_PORT="$P_200" OPENBEAST_ROUTER_INSTINCT=shadow OPENBEAST_INSTINCT_PORT=9998)
_o="$(_run "$_RB" "$_RB/scripts/healthcheck.sh" --restart)"
if [[ "$(_rt_launched)" == "{\"OPENBEAST_ROUTER_PORT\": \"$P_RT\", \"OPENBEAST_LLAMA_UPSTREAM\": \"http://127.0.0.1:$P_200\", \"OPENBEAST_MCPO_URL\": \"http://127.0.0.1:3001\", \"ROUTER_INSTINCT\": \"shadow\", \"INSTINCT_URL\": \"http://127.0.0.1:9998\", \"ROUTER_CLASSIFY_MODEL\": \"beast\", \"OPENBEAST_HYDRA_CALLER_TOKEN_FILE\": \"$_RB/.run/hydra-caller.token\"}" ]]; then
  pass "under HYDRA=true the relaunched router's upstream is hydra, with the default route, caller token and instinct ceiling"
else
  fail "relaunched router under hydra: $(_rt_launched)"
fi
if grep -q "restart FAILED: the relaunched agent router exited" <<< "$_o" && [[ ! -e "$_RB/.run/router.pid" ]]; then
  pass "a relaunch that exits at once is reported FAILED and leaves no stale pid on record"
else
  fail "failed router relaunch not reported: $(grep -A3 -i 'agent router' <<< "$_o" | tr '\n' ' ')"
fi

echo ""
echo "start.sh:"
_A="$_T/start"; _box "$_A"
RUN_ENV=(OPENBEAST_HYDRA=true OPENBEAST_INFERENCE_MANAGED=false
         OPENBEAST_INFERENCE_URL="http://127.0.0.1:$P_DEAD" STUB_CHECK_FAIL=1)
_o="$(_run "$_A" "$_A/start.sh")"
if grep -q "beast-hydra config does not validate" <<< "$_o" \
   && ! grep -q '"argv": \[\]' "$_A/.run/launched-hydra" 2>/dev/null \
   && [[ ! -e "$_A/.run/supervisor.pid" && ! -e "$_A/.run/hydra.pid" ]]; then
  pass "HYDRA=true with an invalid config: start.sh exits before launching anything"
else
  fail "invalid hydra config: $(tail -5 <<< "$_o" | tr '\n' ' ') launched=$(cat "$_A/.run/launched-hydra" 2>/dev/null)"
fi
RUN_ENV=(OPENBEAST_INFERENCE_MANAGED=false OPENBEAST_INFERENCE_URL="http://127.0.0.1:$P_DEAD")
_o="$(_run "$_A" "$_A/start.sh" --status)"
if grep -qiE 'hydra|instinct' <<< "$_o"; then
  fail "--status with hydra/instinct off mentions them"
else
  pass "--status with hydra/instinct off: unchanged rows"
fi
RUN_ENV+=(OPENBEAST_HYDRA=true OPENBEAST_HYDRA_PORT="$P_503" OPENBEAST_INSTINCT=true)
_o="$(_run "$_A" "$_A/start.sh" --status)"
if grep -q '  hydra: not running' <<< "$_o" && grep -q '  instinct: not running' <<< "$_o" \
   && grep -q 'hydra health: up, NO routable default route' <<< "$_o"; then
  pass "--status with hydra/instinct on: their rows and hydra's routability"
else
  fail "--status rows: $(grep -iE 'hydra|instinct' <<< "$_o" | tr '\n' ' ')"
fi

# start.sh's own hydra functions, RUN (not grepped): launch_hydra against a
# stand-in that really binds HYDRA_PORT, cleanup() after a watchdog replaced
# it, and wait_hydra_routable under an octal-looking grace.
echo ""
echo "start.sh launch_hydra / cleanup / wait_hydra_routable, executed:"
_L="$_T/launch"; _box "$_L"
P_LH="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
cat > "$_L/bin/curl" <<EOF
#!/bin/bash
for a in "\$@"; do case "\$a" in *127.0.0.1:$P_LH*) exec /usr/bin/curl "\$@" ;; esac; done
exit 7
EOF
chmod +x "$_L/bin/curl"
cat > "$_L/agents/hydra.py" <<'PY'
import http.server, json, os, sys
run = os.environ["OPENBEAST_HYDRA_RUN_DIR"]
with open(os.path.join(run, "stub-hydra-%d" % os.getpid()), "w") as fh:
    json.dump({"argv": sys.argv[1:], "port": os.environ.get("OPENBEAST_HYDRA_PORT"),
               "default": os.environ.get("OPENBEAST_HYDRA_DEFAULT_MODEL")}, fh)
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = b'{"status":"ok"}'
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
http.server.HTTPServer(("127.0.0.1", int(os.environ["OPENBEAST_HYDRA_PORT"])), H).serve_forever()
PY
# _sx <driver>: start.sh's libs + its hydra functions, in the sandbox.
_sx() {
  env -i HOME="$_L/home" PATH="$_L/bin:/usr/bin:/bin" OPENBEAST_HYDRA=true OPENBEAST_HYDRA_PORT="$P_LH" \
    OPENBEAST_INFERENCE_MANAGED=false OPENBEAST_INFERENCE_URL="http://127.0.0.1:$P_DEAD" \
    OPENBEAST_HYDRA_READY_GRACE="${GRACE:-60}" \
    timeout 60 nice -n 19 bash -c '
      set -euo pipefail
      SCRIPT_DIR="$1"; REPO_DIR="$1"; RUN_DIR="$1/.run"
      for l in proc conf extensions net backend curl_auth portown; do source "$1/scripts/lib/$l.sh"; done
      eval "$(sed -n -e "/^_rm_own_pidfile() {/,/^}/p" -e "/^cleanup() {/,/^}/p" \
                     -e "/^launch_hydra() {/,/^}/p" -e "/^wait_hydra_routable() {/,/^}/p" "$1/start.sh")"
      CLEANED=0
      '"$1"'
    ' _ "$_L" 2>&1 || true
}
_o="$(_sx 'launch_hydra; echo "PID=$HYDRA_PID"')"
_hp="$(sed -n 's/^PID=//p' <<< "$_o")"
[[ -n "$_hp" ]] && _PIDS="$_PIDS $_hp"
_tok="$_L/.run/hydra-caller.token"
if [[ -n "$_hp" ]] && kill -0 "$_hp" 2>/dev/null && [[ "$(cat "$_L/.run/hydra.pid")" == "$_hp" ]] \
   && [[ "$(stat -c '%a' "$_tok")" == 600 && "$(wc -c < "$_tok")" -eq 65 ]] \
   && grep -q '"argv": \[\], "port": "'"$P_LH"'", "default": "beast"' "$_L/.run/stub-hydra-$_hp" \
   && grep -q "beast-hydra answering on http://127.0.0.1:$P_LH (pid $_hp)" <<< "$_o"; then
  pass "launch_hydra: starts hydra (no argv), records its pid, mints a 0600 caller token, waits for /health"
else
  fail "launch_hydra: $(tr '\n' ' ' <<< "$_o") pidfile=$(cat "$_L/.run/hydra.pid" 2>/dev/null) tok=$(stat -c '%a' "$_tok" 2>/dev/null)"
fi
_o="$(_sx 'launch_hydra; echo SECOND-STARTED')"
if grep -q "port $P_LH is already held" <<< "$_o" && ! grep -q SECOND-STARTED <<< "$_o"; then
  pass "launch_hydra refuses a port already held (an orphan or a sibling's hydra)"
else
  fail "second launch_hydra: $(tr '\n' ' ' <<< "$_o")"
fi
# The watchdog replaced our hydra: healthcheck --restart recorded the new pid.
kill "$_hp" 2>/dev/null || true
for _i in $(seq 1 50); do kill -0 "$_hp" 2>/dev/null || break; sleep 0.1; done
nice -n 19 python3 -c 'import time; time.sleep(120)' "$_L/agents/hydra.py" &
_rep=$!; _PIDS="$_PIDS $_rep"
echo "$_rep" > "$_L/.run/hydra.pid"; echo "replacement-start" > "$_L/.run/hydra.start"
_sx "HYDRA_PID=$_hp; cleanup" >/dev/null
if [[ "$(cat "$_L/.run/hydra.pid" 2>/dev/null)" == "$_rep" && -e "$_L/.run/hydra.start" ]] && kill -0 "$_rep" 2>/dev/null; then
  pass "cleanup keeps hydra.pid when the watchdog's replacement owns it (no unreapable orphan)"
else
  fail "cleanup deleted the replacement's hydra.pid ($(cat "$_L/.run/hydra.pid" 2>/dev/null)) or killed it"
fi
kill "$_rep" 2>/dev/null || true
# Negative control: the pidfile still names OUR hydra -> removed with its sidecar.
echo "$_hp" > "$_L/.run/hydra.pid"
_sx "HYDRA_PID=$_hp; cleanup" >/dev/null
if [[ ! -e "$_L/.run/hydra.pid" && ! -e "$_L/.run/hydra.start" ]]; then
  pass "cleanup removes hydra.pid + hydra.start when they are ours (control)"
else
  fail "cleanup left our own hydra.pid behind: $(ls "$_L/.run")"
fi
# wait_hydra_routable must time out under HYDRA_READY_GRACE=08 (octal-looking).
# sleep advances SECONDS instead of waiting, and nothing is routable.
_o="$(GRACE=08 _sx 'ob_hydra_ready() { return 1; }; sleep() { SECONDS=$((SECONDS + 1)); }
                    if wait_hydra_routable; then echo ROUTABLE; else echo "RC=$? after ${SECONDS}s"; fi')"
if grep -q "NO routable default route after 8s" <<< "$_o" && grep -q '^RC=1 after' <<< "$_o" \
   && ! grep -q 'value too great' <<< "$_o"; then
  pass "wait_hydra_routable: HYDRA_READY_GRACE=08 times out after 8s (not 'value too great for base')"
else
  fail "wait_hydra_routable with grace 08: $(tr '\n' ' ' <<< "$_o")"
fi

echo ""
echo "doctor.sh:"
_D="$_T/doctor"; _box "$_D"
mkdir -p "$_D/agents/instinct"
cp -r "$REPO_DIR/agents/instinct/." "$_D/agents/instinct/"
cp "$REPO_DIR/agents/hydra_core.py" "$_D/agents/"
# tailscale: a raw :8443 -> :8080 mount.
cat > "$_D/bin/tailscale" <<'EOF'
#!/bin/bash
if [[ "$1 $2" == "serve status" ]]; then
  printf 'https://beast.example.ts.net:8443 (tailnet only)\n|-- / proxy http://127.0.0.1:8080\n'
  exit 0
fi
exit 1
EOF
chmod +x "$_D/bin/tailscale"
RUN_ENV=("${_env_hc[@]}")
_o="$(_run "$_D" "$_D/scripts/doctor.sh")"
if grep -qiE 'hydra|instinct' <<< "$_o"; then
  fail "doctor with hydra/instinct off mentions them: $(grep -iE 'hydra|instinct' <<< "$_o" | head -3 | tr '\n' ' ')"
else
  pass "doctor with hydra/instinct off: not a word about them"
fi
cp "$_T/hydra-local.token" "$_D/.run/hydra-local.token"
RUN_ENV=("${_env_hc[@]}" OPENBEAST_HYDRA=true OPENBEAST_HYDRA_PORT="$P_HY"
         OPENBEAST_INSTINCT=true OPENBEAST_INSTINCT_PORT="$P_DEAD")
_o="$(_run "$_D" "$_D/scripts/doctor.sh")"
_want=(
  "✓ hydra config ok (stub: 2 node(s), 2 route(s))"
  "! hydra config: stub warning"
  "✓ beast-hydra (http://127.0.0.1:$P_HY) — default route routable"
  "✓ node spark: tailnet address, key set"
  "✓ deployment local@rig: READY (0/1, breaker closed)"
  "✗ deployment q@spark: served id 'qwen-typo' is not in the node's /v1/models"
  "✗ deployment q@spark: conformance missing (required)"
  "✓ route beast: routable"
  "✗ route beast:max: nothing routable"
  "✗ :8443 publishes a raw inference port while HYDRA=true"
  "! beast-instinct not responding (http://127.0.0.1:$P_DEAD)"
)
_miss=""
for _w in "${_want[@]}"; do grep -qF -- "$_w" <<< "$_o" || _miss="$_miss [$_w]"; done
if [[ -z "$_miss" ]]; then
  pass "doctor with hydra on: config, process, per-node/deployment/route rows, raw :8443 FAIL, instinct row"
else
  fail "doctor rows missing:$_miss"
fi
# The status rows ride the local token: a wrong one gets no per-deployment
# detail (negative control for the token actually being presented).
echo "wrong" > "$_D/.run/hydra-local.token"
_o="$(_run "$_D" "$_D/scripts/doctor.sh")"
if grep -qF "hydra status unreadable" <<< "$_o" && ! grep -qF "deployment local@rig" <<< "$_o"; then
  pass "doctor: /hydra/status detail needs the local token"
else
  fail "doctor read /hydra/status with a wrong token"
fi

# Side by side with the pre-wiring stop.sh / healthcheck.sh.
if [[ -n "${WIRING_BASELINE_REF:-}" ]]; then
  echo ""
  echo "stop.sh / healthcheck.sh / start.sh --status / doctor.sh — output identical to $WIRING_BASELINE_REF with hydra/instinct off:"
  _OB="$_T/oldbox"; _box "$_OB" "$WIRING_BASELINE_REF"
  _NB="$_T/newbox"; _box "$_NB"
  # One line added to healthcheck.sh and doctor.sh since the pinned baseline
  # for a reason that has nothing to do with hydra/instinct: the closing
  # next step (review 2026-10-09, UX-17) — "Next: <one fix>", or healthcheck's
  # "Stack is not running (…) — start it: ./start.sh -d" (unindented; the
  # stop.sh run just above left the box marked stopped). Exactly that line is
  # dropped; every row, count and verdict above it is still compared.
  # And one row was RENAMED in `start.sh --status` by the same review: the
  # identity tool server's row said "mcpo", a component removed in v1.0. The
  # baseline's label is mapped to the new one; its state is still compared.
  # (This comparison runs only with a baseline ref — in CI — so the rename
  # went through every local run green.)
  _norm() { sed -e "s#$1#<BOX>#g" -e 's/— [0-9-]* [0-9:]*$/— <DATE>/' -e 's/^  mcpo: /  tool server: /' \
              | grep -v -- '--restart: relaunching' \
              | grep -vE '^(Next: |Stack is not running)'; }
  RUN_ENV=()
  if diff <(_run "$_OB" "$_OB/stop.sh" | _norm "$_OB") <(_run "$_NB" "$_NB/stop.sh" | _norm "$_NB") >/dev/null; then
    pass "stop.sh: identical output"
  else
    fail "stop.sh output differs from $WIRING_BASELINE_REF"
  fi
  RUN_ENV=("${_env_hc[@]}" OPENBEAST_EDGE_GATE=true OPENBEAST_EDGE_PORT="$P_OK")
  if diff <(_run "$_OB" "$_OB/scripts/healthcheck.sh" | _norm "$_OB") \
          <(_run "$_NB" "$_NB/scripts/healthcheck.sh" | _norm "$_NB") >/dev/null; then
    pass "healthcheck.sh: identical output"
  else
    fail "healthcheck.sh output differs from $WIRING_BASELINE_REF:"
    diff <(_run "$_OB" "$_OB/scripts/healthcheck.sh" | _norm "$_OB") \
         <(_run "$_NB" "$_NB/scripts/healthcheck.sh" | _norm "$_NB") | head -10 | sed 's/^/        /' || true
  fi
  RUN_ENV=(OPENBEAST_INFERENCE_MANAGED=false OPENBEAST_INFERENCE_URL="http://127.0.0.1:$P_DEAD")
  if diff <(_run "$_OB" "$_OB/start.sh" --status | _norm "$_OB") \
          <(_run "$_NB" "$_NB/start.sh" --status | _norm "$_NB") >/dev/null; then
    pass "start.sh --status: identical output"
  else
    fail "start.sh --status output differs from $WIRING_BASELINE_REF:"
    diff <(_run "$_OB" "$_OB/start.sh" --status | _norm "$_OB") \
         <(_run "$_NB" "$_NB/start.sh" --status | _norm "$_NB") | head -10 | sed 's/^/        /' || true
  fi
  RUN_ENV=("${_env_hc[@]}" OPENBEAST_EDGE_GATE=true OPENBEAST_EDGE_PORT="$P_OK")
  # One section added to doctor since the pinned baseline for a reason that has
  # nothing to do with hydra/instinct: "Remote skills" (the skill import gate).
  # Exactly that section (header, its one passing row, the blank line) is
  # dropped, and with it the one extra "ok" in the summary; warnings and
  # failures are still compared, and so is every other line.
  _doc() { _norm "$1" | awk '
      /Remote skills/ { skip = 1; next }
      skip && /^[[:space:]]*$/ { skip = 0; next }
      skip && /✓ remote skills: / { next }
      { print }' | sed -E 's/^doctor: [0-9]+ ok,/doctor: <N> ok,/'; }
  # Since UX-17 doctor folds the per-service rows of a stack that is NOT
  # RUNNING into one line. This comparison is about the rows, so both boxes
  # get a live "supervisor" (the baseline doctor never reads the pidfile):
  # the stack then counts as running-but-unhealthy and every row is printed.
  bash -c 'sleep 120; :' start.sh &
  _fake_sup=$!; _PIDS="$_PIDS $_fake_sup"
  echo "$_fake_sup" > "$_OB/.run/supervisor.pid"
  echo "$_fake_sup" > "$_NB/.run/supervisor.pid"
  if diff <(_run "$_OB" "$_OB/scripts/doctor.sh" | _doc "$_OB") \
          <(_run "$_NB" "$_NB/scripts/doctor.sh" | _doc "$_NB") >/dev/null; then
    pass "doctor.sh: identical output"
  else
    fail "doctor.sh output differs from $WIRING_BASELINE_REF"
  fi
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
