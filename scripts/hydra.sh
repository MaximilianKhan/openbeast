#!/bin/bash
# beast-hydra operator CLI (docs/BEAST_HYDRA_PLAN.md §6.9).
#
#   scripts/hydra.sh check [path]                 validate hydra.toml (or the implicit config)
#   scripts/hydra.sh status [--json]              nodes, deployments, routes
#   scripts/hydra.sh explain '<json>' | -f FILE [-H 'Header: v']...   dry-run a routing decision
#   scripts/hydra.sh reload                       validate + swap (a bad file keeps the old config)
#   scripts/hydra.sh drain <node> | undrain <node>
#   scripts/hydra.sh decisions [n]                recent decision traces
#   scripts/hydra.sh metrics                      Prometheus text
#   scripts/hydra.sh tail [-f] [n]                pretty-print the audit
#   scripts/hydra.sh add-node <id> --url U --engine E [--key-file F] [--slots N]
#                            [--profile P --deployment D]     probe it, print a TOML stanza
#   scripts/hydra.sh conformance <deployment> [--heavy]       -> .run/conformance/<deployment>/
#   scripts/hydra.sh pin-smoke [deployment|all]   1-token + streaming chat per deployment via /pin
#   scripts/hydra.sh sim [args]                   the simulated fleet (scripts/hydra-sim.sh)
#
# Admin calls present the per-start local token (.run/hydra-local.token) —
# loopback alone proves nothing behind `tailscale serve`. Inference calls
# present LLAMA_API_KEY (or the file in HYDRA_KEY_FILE). No secret is ever
# put on argv: curl gets headers through lib/curl_auth.sh, python by env.
# add-node never edits hydra.toml; it prints the stanza to paste.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${OPENBEAST_HYDRA_NO_CONF:-}" && -f "$REPO_DIR/openbeast.conf" && -f "$REPO_DIR/scripts/lib/conf.sh" ]]; then
  # shellcheck source=lib/conf.sh
  source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null
fi
# shellcheck source=lib/curl_auth.sh
source "$REPO_DIR/scripts/lib/curl_auth.sh"

RUN_DIR="${OPENBEAST_HYDRA_RUN_DIR:-${OPENBEAST_RUN_DIR:-$REPO_DIR/.run}}"
HYDRA_URL="${OPENBEAST_HYDRA_URL:-http://127.0.0.1:${OPENBEAST_HYDRA_PORT:-${HYDRA_PORT:-8095}}}"
HYDRA_URL="${HYDRA_URL%/}"
PY="${PYTHON:-python3}"

# Python formatters, kept out of bash quoting (single-quoted heredocs).
read -r -d '' _STATUS_FMT <<'PYEOF' || true
import json, sys
s = json.load(sys.stdin)
if "error" in s:
    sys.exit("hydra.sh: " + s["error"].get("message", "refused"))
print(f"beast-hydra  config {s['config_hash']} ({s['config_source']})  up {s['uptime_s']}s  "
      f"default route: {s['default_route']}")
if s.get("last_reload_error"):
    print(f"  LAST RELOAD REFUSED: {s['last_reload_error']}")
for w in s.get("warnings") or []:
    print(f"  warning: {w}")
print("\nNODES")
for n, v in s["nodes"].items():
    lp = v.get("last_probe") or {}
    extra = ("" if v["enabled"] else "  DISABLED") + (f"  DRAINED({v['drained']})" if v["drained"] else "")
    print(f"  {n:<14} {v['engine']:<10} {v['host']:<22} key:{v['key']:<4} "
          f"{v['inflight']}/{v['slots']}  probe:{lp.get('result', '-')}{extra}")
print("\nDEPLOYMENTS")
for d, v in s["deployments"].items():
    ok = "routable" if v["routable"] else "NOT routable"
    det = f"  ({v['detail']})" if v.get("detail") else ""
    print(f"  {d:<28} {v['state']:<11} breaker:{v['breaker']:<9} {v['inflight']}/{v['slots']}  "
          f"conf:{v['conformance']:<7} {ok}{det}")
print("\nROUTES")
for r, v in s["routes"].items():
    ok = "ok" if v["routable"] else "NONE ROUTABLE"
    print(f"  {r:<16} {ok:<14} groups:{v['candidates_per_group']}")
PYEOF

read -r -d '' _TAIL_FMT <<'PYEOF' || true
import json, sys
for line in sys.stdin:
    try:
        r = json.loads(line)
    except ValueError:
        continue
    att = ",".join(f"{a['d']}:{a['status']}" for a in r.get("attempts") or [])
    rules = f" rules={','.join(r['rules'])}" if r.get("rules") else ""
    dev = f" dev={r['device']}" if r.get("device") else ""
    print(f"{r['ts']} {r.get('request_id', '?'):<16} {str(r.get('requested')):<18} -> "
          f"{str(r.get('route')):<12} {str(r.get('deployment')):<24} {r.get('status')} "
          f"{str(r.get('outcome')):<26} {r.get('ms')}ms  [{att}]{rules}{dev}", flush=True)
PYEOF

die() { printf 'hydra.sh: %s\n' "$*" >&2; exit 1; }

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; }

_local_token() {
  local f="$RUN_DIR/hydra-local.token"
  [[ -r "$f" ]] || die "no $f — is beast-hydra running? (start.sh with HYDRA=true)"
  cat "$f"
}

_inbound_key() {
  if [[ -n "${HYDRA_KEY_FILE:-}" ]]; then
    [[ -r "$HYDRA_KEY_FILE" ]] || die "HYDRA_KEY_FILE=$HYDRA_KEY_FILE is not readable"
    tr -d '[:space:]' < "$HYDRA_KEY_FILE"
  else
    printf '%s' "${LLAMA_API_KEY:-${OPENBEAST_API_KEY:-}}"
  fi
}

# _admin METHOD PATH [curl args…] — prints the body; exit 1 on no answer.
_admin() {
  local m="$1" p="$2" tok
  shift 2
  tok="$(_local_token)"
  ob_curl_hdr "X-OpenBeast-Local: $tok" -sS -m 15 -X "$m" "$@" "$HYDRA_URL$p" \
    || die "beast-hydra did not answer at $HYDRA_URL"
}

_pretty() { "$PY" -m json.tool; }

cmd_check() {
  exec "$PY" "$REPO_DIR/agents/hydra.py" --check "$@"
}

cmd_status() {
  local json=0
  [[ "${1:-}" == "--json" ]] && json=1
  local body
  body="$(_admin GET /hydra/status)"
  if [[ $json -eq 1 ]]; then printf '%s\n' "$body"; return; fi
  printf '%s' "$body" | "$PY" -c "$_STATUS_FMT"
}


cmd_explain() {
  local doc="" hdrs=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f) [[ -r "${2:-}" ]] || die "explain -f: cannot read ${2:-}"; doc="$(cat "$2")"; shift 2 ;;
      -H) hdrs+=("${2:-}"); shift 2 ;;
      *)  doc="$1"; shift ;;
    esac
  done
  [[ -n "$doc" ]] || die "explain '<json request body>' | -f FILE [-H 'Header: v']"
  local body
  body="$(HYDRA_EXPLAIN_DOC="$doc" "$PY" -c '
import json, os, sys
try:
    d = json.loads(os.environ["HYDRA_EXPLAIN_DOC"])
except ValueError as e:
    sys.exit(f"hydra.sh explain: not JSON ({e})")
h = d.setdefault("headers", {})
for line in sys.argv[1:]:
    k, _, v = line.partition(":")
    h[k.strip()] = v.strip()
print(json.dumps(d))
' "${hdrs[@]+"${hdrs[@]}"}")" || exit 1
  printf '%s' "$body" | _admin POST /hydra/explain -H "Content-Type: application/json" --data-binary @- | _pretty
}

cmd_reload() {
  _admin POST /hydra/reload | _pretty
}

cmd_drain() {
  [[ -n "${2:-}" ]] || die "$1 <node>"
  _admin POST "/hydra/$1/$2" | _pretty
}

cmd_decisions() {
  _admin GET "/hydra/decisions?n=${1:-20}" | _pretty
}

cmd_metrics() {
  _admin GET /hydra/metrics
}

cmd_tail() {
  local follow=0 n=20 audit
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f) follow=1; shift ;;
      *)  n="$1"; shift ;;
    esac
  done
  [[ "$n" =~ ^[0-9]+$ ]] || die "tail [-f] [n]"
  audit="${HYDRA_AUDIT:-}"
  if [[ -z "$audit" ]]; then
    audit="$(_admin GET /hydra/status 2>/dev/null | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("audit",""))' 2>/dev/null || true)"
  fi
  [[ -n "$audit" ]] || audit="$REPO_DIR/.run/hydra-audit.jsonl"
  [[ -r "$audit" ]] || die "no audit at $audit yet"
  if [[ $follow -eq 1 ]]; then
    tail -n "$n" -F "$audit" | "$PY" -u -c "$_TAIL_FMT"
  else
    tail -n "$n" "$audit" | "$PY" -c "$_TAIL_FMT"
  fi
}

cmd_add_node() {
  local id="${1:-}" url="" engine="" key_file="" slots="" profile="" dep=""
  [[ -n "$id" && "$id" != --* ]] || die "add-node <id> --url U --engine E [--key-file F] [--slots N] [--profile P --deployment D]"
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --url) url="${2:-}"; shift 2 ;;
      --engine) engine="${2:-}"; shift 2 ;;
      --key-file) key_file="${2:-}"; shift 2 ;;
      --slots) slots="${2:-}"; shift 2 ;;
      --profile) profile="${2:-}"; shift 2 ;;
      --deployment) dep="${2:-}"; shift 2 ;;
      *) die "add-node: unknown option $1" ;;
    esac
  done
  [[ -n "$url" && -n "$engine" ]] || die "add-node needs --url and --engine"
  HN_ID="$id" HN_URL="$url" HN_ENGINE="$engine" HN_KEY_FILE="$key_file" HN_SLOTS="$slots" \
  HN_PROFILE="$profile" HN_DEP="$dep" "$PY" - "$REPO_DIR" <<'PYEOF'
import json, os, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.join(sys.argv[1], "agents"))
import hydra_core as core
e = os.environ
nid, url, engine = e["HN_ID"], e["HN_URL"].rstrip("/"), e["HN_ENGINE"]
problems = []
if not core.NODE_ID_RE.match(nid):
    problems.append(f"node id must match {core.NODE_ID_RE.pattern}")
if engine not in core.ENGINES:
    problems.append(f"--engine must be one of {', '.join(core.ENGINES)}")
host, cls = core.host_class(url)
if cls in ("invalid", "public"):
    problems.append(f"--url {url}: {cls} (nodes must be loopback, RFC1918 or tailnet)")
key = None
if e.get("HN_KEY_FILE"):
    if engine == "tensorfold":
        problems.append("TensorFold has no auth: never give it a key (firewall it to the rig)")
    else:
        problems += core._key_file_errors("--key-file", e["HN_KEY_FILE"], core.REPO)
        if not problems:
            key = open(os.path.expanduser(e["HN_KEY_FILE"])).read().strip() or None
if problems:
    sys.exit("hydra.sh add-node: " + "; ".join(problems))

def get(path, with_key):
    req = urllib.request.Request(url + path)
    if with_key and key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as x:
        return x.code, x.read()
    except OSError as x:
        return None, str(x).encode()

st, body = get("/health", False)
ready = core.engine_ready(engine, st, body)
print(f"# probe: GET {url}/health -> {st} => {ready}", file=sys.stderr)
st, body = get("/v1/models", True)
served = []
if st == 200:
    try:
        served = [m.get("id") for m in json.loads(body).get("data", [])]
    except ValueError:
        pass
print(f"# probe: GET {url}/v1/models ({'with' if key else 'no'} key) -> {st} {served}", file=sys.stderr)
if st in (401, 403):
    print("# WARNING: the node refused the key — hydra would mark it AUTH_FAILED", file=sys.stderr)
lines = [f"[nodes.{nid}]", f'url         = "{url}"', f'engine      = "{engine}"']
if e.get("HN_KEY_FILE") and engine != "tensorfold":
    lines.append(f'key_file    = "{e["HN_KEY_FILE"]}"')
elif engine not in ("tensorfold",) and cls != "loopback":
    print("# WARNING: a remote node with no key", file=sys.stderr)
if e.get("HN_SLOTS"):
    lines.append(f"slots       = {int(e['HN_SLOTS'])}")
dep = e.get("HN_DEP")
if dep or e.get("HN_PROFILE"):
    dep = dep or f"{(e.get('HN_PROFILE') or 'model')}@{nid}"
    lines += ["", f'[deployments."{dep}"]', f'node     = "{nid}"']
    if e.get("HN_PROFILE"):
        lines.append(f'profile  = "{e["HN_PROFILE"]}"')
    else:
        up = served[0] if served else "SERVED-ID"
        lines += [f'upstream = "{up}"', "ctx      = 0                  # set the real context length"]
    lines.append("caps     = []                 # e.g. [\"tools\", \"json_schema\"]; run conformance")
print("\n".join(lines))
print("# paste into hydra.toml, add the deployment to a route, then: scripts/hydra.sh check && "
      "scripts/hydra.sh reload", file=sys.stderr)
sys.exit(0 if ready == "ready" else 3)
PYEOF
}

cmd_conformance() {
  local dep="${1:-}"
  [[ -n "$dep" ]] || die "conformance <deployment> [--heavy]"
  shift
  local cfg="${OPENBEAST_HYDRA_CONFIG:-${HYDRA_CONFIG:-$REPO_DIR/hydra.toml}}" fields
  fields="$(HC_CFG="$cfg" HC_DEP="$dep" "$PY" - "$REPO_DIR" <<'PYEOF'
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "agents"))
import hydra_core as core
from pathlib import Path
p = Path(os.environ["HC_CFG"])
try:
    cfg = core.load_config(p) if p.exists() else core.implicit_config()
except core.ConfigError as e:
    sys.exit("hydra.sh conformance: config invalid: " + "; ".join(e.errors))
d = cfg.deployments.get(os.environ["HC_DEP"])
if d is None:
    sys.exit(f"hydra.sh conformance: no deployment {os.environ['HC_DEP']!r} in {cfg.source}")
n = cfg.nodes[d.node]
backend = {"openai": "vllm"}.get(n.engine, n.engine)
print("\x1f".join([n.url, backend, d.upstream, n.key_env or "", n.key_file or ""]))
PYEOF
)" || exit 1
  local url backend model key_env key_file
  # \x1f, not a tab: tab is IFS whitespace, so an empty key_env would collapse
  IFS=$'\x1f' read -r url backend model key_env key_file <<< "$fields"
  local args=(--url "$url" --backend "$backend" --model "$model" --out "$RUN_DIR/conformance/$dep")
  [[ -n "$key_file" ]] && args+=(--key-file "$key_file")
  # conformance.py directly, not conformance.sh: the wrapper sources conf.sh,
  # which would put the RIG's LLAMA_API_KEY back in the environment and send
  # it to this node. Exactly the node's key (by env, child only, never argv),
  # or its --key-file, or nothing.
  local node_key=""
  [[ -n "$key_env" && "$backend" != "tensorfold" ]] && node_key="${!key_env:-}"
  LLAMA_API_KEY="$node_key" OPENBEAST_API_KEY="" \
    exec "$PY" "$REPO_DIR/scripts/backends/pylib/conformance.py" "${args[@]}" "$@"
}

cmd_pin_smoke() {
  local which="${1:-all}" deps
  deps="$(_admin GET /hydra/status | "$PY" -c 'import json,sys; print("\n".join(json.load(sys.stdin)["deployments"]))')"
  [[ "$which" == "all" ]] || deps="$which"
  HYDRA_INBOUND="$(_inbound_key)" HYDRA_URL="$HYDRA_URL" HYDRA_DEPS="$deps" "$PY" - <<'PYEOF'
import json, os, sys, time, urllib.error, urllib.request
base, key = os.environ["HYDRA_URL"], os.environ.get("HYDRA_INBOUND", "")
bad = 0
for d in [x for x in os.environ["HYDRA_DEPS"].split("\n") if x]:
    for stream in (False, True):
        body = {"model": d, "max_tokens": 1 if not stream else 8, "stream": stream,
                "messages": [{"role": "user", "content": "Say OK."}]}
        req = urllib.request.Request(f"{base}/pin/{d}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                first = r.read(1)
                ttft = (time.monotonic() - t0) * 1000
                rest = r.read()
                st, h = r.status, r.headers
                ok = st == 200 and (not stream or b"[DONE]" in rest)
        except urllib.error.HTTPError as e:
            st, h, ok, ttft = e.code, e.headers, False, (time.monotonic() - t0) * 1000
        except OSError as e:
            print(f"  {d:<28} {'stream' if stream else 'plain ':<6} FAILED {e}")
            bad += 1
            continue
        bad += 0 if ok else 1
        print(f"  {d:<28} {'stream' if stream else 'plain ':<6} {st} ttft={ttft:7.1f}ms "
              f"node={h.get('X-Hydra-Node')} engine={h.get('X-Hydra-Engine')} "
              f"upstream={h.get('X-Hydra-Upstream-Model')} {'ok' if ok else 'FAIL'}")
sys.exit(1 if bad else 0)
PYEOF
}

main() {
  local cmd="${1:-help}"
  shift || true
  case "$cmd" in
    check)       cmd_check "$@" ;;
    status)      cmd_status "$@" ;;
    explain)     cmd_explain "$@" ;;
    reload)      cmd_reload ;;
    drain|undrain) cmd_drain "$cmd" "$@" ;;
    decisions)   cmd_decisions "$@" ;;
    metrics)     cmd_metrics ;;
    tail)        cmd_tail "$@" ;;
    add-node)    cmd_add_node "$@" ;;
    conformance) cmd_conformance "$@" ;;
    pin-smoke)   cmd_pin_smoke "$@" ;;
    sim)         exec "$REPO_DIR/scripts/hydra-sim.sh" "$@" ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
  esac
}

main "$@"
