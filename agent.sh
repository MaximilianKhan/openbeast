#!/bin/bash
# Run a local AI agent against a task.
#
# Usage:
#   ./agent.sh "add error handling to the API routes"
#   ./agent.sh -w ~/projects/myapp "write unit tests for auth.py"
#   ./agent.sh -f tasks/refactor-logging.md
#   ./agent.sh --max-iter 50 "fix the failing CI tests"
#
# The llama.cpp server must be running (./start.sh or any serve-*.sh script).

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Resolve stack config (exports OPENBEAST_AGENT_INFERENCE_URL when the
# distributed-agents worker endpoint is set via env or openbeast.conf).
source "$REPO_DIR/scripts/lib/conf.sh"

# Install deps if needed. --break-system-packages only where PEP-668
# requires it (Arch, newer Debian) — older pip errors on the unknown flag.
#
# FROM THE HASH-PINNED LOCK first (scripts/pydeps.sh picks --user and the
# PEP-668 flag itself), the same closure bootstrap installs: requirements.txt
# pins 6 versions and no content. pydeps exit 3 is a HASH MISMATCH — fatal,
# never answered with an unpinned install of the same names from the same
# index. Any other failure (stale lock, a python the lock does not cover, an
# index that omits a locked file) STOPS too: falling back to requirements.txt
# is opt-in, OPENBEAST_PIP_STRICT=0. It was the default, and "omit one file"
# is all a hostile mirror needed to turn a hash-pinned install into an
# unverified one.
if ! python3 -c "import openai" 2>/dev/null; then
  echo "Installing agent dependencies..."
  _pd_rc=0
  OPENBEAST_PYTHON=python3 "$REPO_DIR/scripts/pydeps.sh" install -q || _pd_rc=$?
  if [[ $_pd_rc -eq 3 ]]; then
    echo "HASH MISMATCH installing from agents/requirements.lock (pip's report is above)." >&2
    echo "Refusing — and NOT falling back to requirements.txt, which would install the same" >&2
    echo "packages unverified from the same source. Suspect a mirror/proxy (PIP_INDEX_URL) first." >&2
    exit 1
  elif [[ $_pd_rc -ne 0 ]]; then
    [[ "${OPENBEAST_PIP_STRICT:-1}" == "0" ]] || {
      echo "The hash-pinned install from agents/requirements.lock failed (see above), NOT on a hash." >&2
      echo "Stopping rather than installing the same packages unverified from the same index." >&2
      echo "  - check the lock and the index:  ./scripts/pydeps.sh verify ; pip config list" >&2
      echo "  - or install with versions pinned but content NOT verified:" >&2
      echo "      OPENBEAST_PIP_STRICT=0 ./agent.sh ..." >&2
      exit 1; }
    echo "warning: the hash-pinned install failed, NOT on a hash (see above) — OPENBEAST_PIP_STRICT=0," >&2
    echo "         so falling back to agents/requirements.txt, which pins VERSIONS but not content." >&2
    PIP_FLAGS=""
    if python3 -c 'import sysconfig,os;p=sysconfig.get_path("stdlib");exit(0 if os.path.exists(os.path.join(p,"EXTERNALLY-MANAGED")) else 1)' 2>/dev/null; then
      PIP_FLAGS="--break-system-packages"
    fi
    python3 -m pip install --user $PIP_FLAGS -q -r "$REPO_DIR/agents/requirements.txt"
  fi
fi

# Distributed agents (opt-in): default --base-url to the configured worker
# endpoint when set and the caller didn't pass one explicitly (explicit
# --base-url always wins). Local files, remote brains.
ARGS=("$@")
if [[ -n "${OPENBEAST_AGENT_INFERENCE_URL:-}" ]]; then
  has_base_url=false
  for arg in "$@"; do
    [[ "$arg" == "--base-url" || "$arg" == --base-url=* ]] && has_base_url=true
  done
  if [[ "$has_base_url" == false ]]; then
    ARGS+=(--base-url "$OPENBEAST_AGENT_INFERENCE_URL")
  fi
fi

# Tier-3 awareness pack (agents/lang/pack_context.py): a zig task gets
# agents/packs/zig-0.16.md as --context-file — the exact file and channel the
# A/B measured. The helper hands back the argv NUL-separated; an explicit
# --context-file of yours wins, LANG_PACK_CONTEXT=off disables it, and any
# helper failure leaves the argv exactly as you typed it.
if [[ -f "$REPO_DIR/agents/lang/pack_context.py" ]]; then
  _packed=()
  while IFS= read -r -d '' _a; do _packed+=("$_a"); done \
    < <(python3 "$REPO_DIR/agents/lang/pack_context.py" argv -- ${ARGS[@]+"${ARGS[@]}"} || true)
  if (( ${#_packed[@]} > 0 && ${#_packed[@]} >= ${#ARGS[@]} )); then
    ARGS=("${_packed[@]}")
  fi
fi

exec python3 "$REPO_DIR/agents/runner.py" ${ARGS[@]+"${ARGS[@]}"}
