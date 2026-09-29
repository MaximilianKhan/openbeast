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
