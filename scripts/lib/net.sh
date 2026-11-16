#!/bin/bash
# Network helpers shared by start.sh, scripts/doctor.sh and
# scripts/healthcheck.sh — the three scripts that PROBE the stack's services.
# Sourced, never executed; defines functions only (no `set --`, no
# shell-option changes, no output).
#
# One mapping, one place. Each of the three used to carry its own `case` on
# BIND_HOST and they drifted: healthcheck learned `::`, start.sh and doctor.sh
# did not, so with BIND_HOST=:: every probe they built was `http://:::8080/…`
# (curl rc=3, "malformed") — start.sh then waited forever on a healthy model.

# ob_probe_host <bind-address> — the host a LOCAL client must dial to reach a
# service bound to <bind-address>, URL-ready (IPv6 literals come back
# bracketed). Wildcards map to loopback; a specific address — a LAN or tailnet
# IP, or a non-default loopback such as 127.0.0.2 — must be dialled as
# itself, because a socket bound to it refuses every other destination.
#   0.0.0.0 / empty / localhost -> 127.0.0.1
#   :: / [::]                   -> [::1]  (answers whether or not the kernel
#                                         made the socket v6-only; 127.0.0.1
#                                         only reaches a dual-stack one)
#   ::1, fd7a::5, [fd7a::5]     -> [::1], [fd7a::5], [fd7a::5]
#   192.168.1.50, 127.0.0.2     -> unchanged
ob_probe_host() {
  local h="${1:-}"
  h="${h#[}"; h="${h%]}"
  case "$h" in
    ""|0.0.0.0|localhost)       printf '127.0.0.1\n' ;;
    ::|::0|0:0:0:0:0:0:0:0)     printf '[::1]\n' ;;
    *:*)                        printf '[%s]\n' "$h" ;;
    *)                          printf '%s\n' "$h" ;;
  esac
}

# ob_llama_ready <base-url> — 0 only when llama-server is READY: HTTP 200 with
# a {"status":"ok"} body. llama-server binds its port BEFORE it loads the
# model and answers /health with 503 {"error":{"message":"Loading model"}}
# for the whole load; `curl -s` exits 0 on that 503, so a bare "did curl
# succeed" probe calls a model healthy the moment the port binds — minutes
# before it can serve, and whether or not the load then OOMs.
ob_llama_ready() {
  local body
  body="$(curl -fsS -m 3 "${1%/}/health" 2>/dev/null)" || return 1
  [[ "$body" =~ \"status\"[[:space:]]*:[[:space:]]*\"ok\" ]]
}

# ob_ts_8443_raw_to <port> — what `tailscale serve` publishes on :8443:
#   0  :8443 proxies straight to <port> (a RAW inference mount, no gate)
#   1  tailscale answered and it does not
#   2  no answer (not installed, not running, >3 s, unparseable)
# `serve --bg` mounts persist across reboots and restarts, so a raw :8443
# made while LLAMA_API_KEY was set outlives that key. serve.sh asks this
# before it starts a keyless llama-server: the inference counterpart of
# conf.sh's _ob_webui_on_443.
ob_ts_8443_raw_to() {
  local out
  command -v tailscale >/dev/null 2>&1 || return 2
  # Captured, not piped: under a caller's pipefail a failing tailscale would
  # override the parser's answer.
  if command -v timeout >/dev/null 2>&1; then
    out="$(timeout 3 tailscale serve status --json 2>/dev/null)" || return 2
  else
    out="$(tailscale serve status --json 2>/dev/null)" || return 2
  fi
  printf '%s' "$out" | python3 -c '
import json, re, sys
try:
    web = json.load(sys.stdin).get("Web") or {}
except Exception:
    sys.exit(2)
pat = re.compile(r"^https?://[^/]+:" + re.escape(sys.argv[1]) + r"/?$")
for hp, cfg in web.items():
    if hp.endswith(":8443") and any(pat.match(str((h or {}).get("Proxy", "")))
                                    for h in ((cfg or {}).get("Handlers") or {}).values()):
        sys.exit(0)
sys.exit(1)' "$1" 2>/dev/null
}
