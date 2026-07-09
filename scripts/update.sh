#!/bin/bash
# OpenBeast — update every pulled-in open source component to latest.
#
#   ./scripts/update.sh              # update everything (asks nothing)
#   ./scripts/update.sh --llama      # only llama.cpp (pull + rebuild)
#   ./scripts/update.sh --images     # only container images (Open WebUI, SearXNG)
#   ./scripts/update.sh --python     # only Python deps (mcpo, mcp, openai, hf)
#   ./scripts/update.sh --opencode   # only OpenCode
#   ./scripts/update.sh --check      # show current vs available, change nothing
#
# Flags compose: `--llama --images` updates just those two. Full docs and
# per-component notes: docs/UPDATING.md.
#
# Idempotent and safe to re-run. A running stack keeps serving throughout —
# the llama-server binary is replaced on disk while the old inode keeps
# running; restart (./stop.sh && ./start.sh) to pick everything up.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Resolve user config BEFORE any `docker compose up` — conf.sh exports
# OPENBEAST_BIND / OPENBEAST_WEBUI_AUTH / OPENBEAST_API_KEY so recreated
# containers keep the user's auth and bind settings instead of reverting
# to compose defaults. It also resolves GPU_BACKEND (persisted there by
# bootstrap.sh) so update_llama rebuilds with the same backend flavor;
# hardware.sh provides the shared backend → cmake-flags mapping.
source "$REPO_DIR/scripts/lib/conf.sh"
source "$REPO_DIR/scripts/lib/hardware.sh"

c_bold=$'\033[1m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_rst=$'\033[0m'
step() { echo; echo "${c_bold}==> $*${c_rst}"; }
ok()   { echo "  ${c_grn}✓${c_rst} $*"; }
warn() { echo "  ${c_ylw}!${c_rst} $*"; }
die()  { echo "  ${c_red}✗ $*${c_rst}" >&2; exit 1; }

DO_LLAMA=0; DO_IMAGES=0; DO_PYTHON=0; DO_OPENCODE=0; CHECK_ONLY=0; ANY=0
for arg in "$@"; do
  case "$arg" in
    --llama)    DO_LLAMA=1;    ANY=1 ;;
    --images)   DO_IMAGES=1;   ANY=1 ;;
    --python)   DO_PYTHON=1;   ANY=1 ;;
    --opencode) DO_OPENCODE=1; ANY=1 ;;
    --check)    CHECK_ONLY=1 ;;
    -h|--help)  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done
# No component flags → all components.
if [[ $ANY -eq 0 ]]; then DO_LLAMA=1; DO_IMAGES=1; DO_PYTHON=1; DO_OPENCODE=1; fi

# ---- llama.cpp: git pull + CUDA rebuild ------------------------------------
update_llama() {
  step "llama.cpp"
  local src="$REPO_DIR/llama.cpp" build="$REPO_DIR/llama.cpp/build"
  [[ -d "$src/.git" ]] || die "llama.cpp/ is not a git clone — run ./bootstrap.sh first"

  local before after
  before=$(git -C "$src" rev-parse --short HEAD)
  if [[ $CHECK_ONLY -eq 1 ]]; then
    git -C "$src" fetch -q origin
    after=$(git -C "$src" rev-parse --short origin/master 2>/dev/null || git -C "$src" rev-parse --short origin/HEAD)
    local behind
    behind=$(git -C "$src" rev-list --count HEAD..origin/master 2>/dev/null || echo "?")
    ok "local $before, upstream $after ($behind commits behind)"
    return 0
  fi

  # A detached HEAD means the user pinned a known-good SHA (see
  # docs/UPDATING.md) — honor the pin: rebuild what's checked out, no pull.
  if git -C "$src" symbolic-ref -q HEAD >/dev/null; then
    git -C "$src" pull --ff-only origin master 2>/dev/null \
      || git -C "$src" pull --ff-only \
      || die "git pull failed — local changes in llama.cpp/? stash or reset them"
  else
    warn "detached HEAD (pinned checkout) — skipping pull, building as-is"
  fi
  after=$(git -C "$src" rev-parse --short HEAD)
  if [[ "$before" == "$after" && -x "$build/bin/llama-server" ]]; then
    ok "already up to date ($before) and built — skipping rebuild"
    return 0
  fi
  ok "updated $before → $after"

  # A build dir configured under an old repo path (the repo was moved or
  # renamed) has a stale CMake cache and every rebuild fails confusingly.
  # Detect and wipe rather than let cmake error out.
  # -xF: literal whole-line match — a repo path containing regex chars
  # (dots, brackets) must not silently change what this matches.
  if [[ -f "$build/CMakeCache.txt" ]] \
     && ! grep -qxF "CMAKE_HOME_DIRECTORY:INTERNAL=$src" "$build/CMakeCache.txt"; then
    warn "build/ was configured under a different path — wiping for a clean reconfigure"
    rm -rf "$build"
  fi

  # Rebuild with the SAME backend bootstrap used: GPU_BACKEND was persisted
  # in openbeast.conf and resolved by conf.sh; the flags come from the
  # shared lib (scripts/lib/hardware.sh) so bootstrap and update never drift.
  ob_detect_gpu
  ob_resolve_backend
  ob_backend_preflight \
    || die "toolchain for the '$OB_BACKEND' backend is missing (see above)"
  case "$OB_BACKEND" in
    hip|sycl) warn "'$OB_BACKEND' backend is UNTESTED by OpenBeast — reference profile is CUDA/5090" ;;
    cpu)      warn "CPU-only build — inference will be 10-50x slower than on a GPU" ;;
  esac
  local cmake_flags
  cmake_flags="$(ob_cmake_flags)" \
    || die "unknown GPU_BACKEND '$GPU_BACKEND' (valid: auto | cuda | hip | sycl | cpu)"
  ok "backend $OB_BACKEND → cmake flags: ${cmake_flags:-none (CPU-only)}"
  # $cmake_flags is deliberately unquoted — it's a flag list.
  cmake -S "$src" -B "$build" \
        $cmake_flags -DCMAKE_BUILD_TYPE=Release
  cmake --build "$build" --config Release -j"$(nproc)" --target llama-server
  [[ -x "$build/bin/llama-server" ]] || die "rebuild did not produce llama-server"
  ok "rebuilt llama-server ($after)"
  warn "a running llama-server keeps the OLD binary until restarted"
}

# ---- container images: Open WebUI + SearXNG --------------------------------
update_images() {
  step "Container images (Open WebUI + SearXNG)"
  command -v docker >/dev/null 2>&1 || { warn "docker not found — skipping images"; return 0; }
  if [[ $CHECK_ONLY -eq 1 ]]; then
    docker images --format '  {{.Repository}}:{{.Tag}}  {{.ID}}  created {{.CreatedSince}}' \
      | grep -E 'open-webui|searxng' || warn "images not pulled yet"
    return 0
  fi
  docker compose pull
  ok "images pulled"
  # Recreate only containers that are actually running on an old image;
  # a stopped stack stays stopped.
  if docker compose ps --status running --quiet 2>/dev/null | grep -q .; then
    docker compose up -d
    ok "running containers recreated on the new images"
  else
    ok "stack not running — new images take effect on next ./start.sh"
  fi
}

# ---- Python deps: mcpo, mcp SDK, openai, huggingface_hub -------------------
update_python() {
  step "Python packages (mcp, openai, fastapi, uvicorn, huggingface_hub)"
  if [[ $CHECK_ONLY -eq 1 ]]; then
    python3 -m pip list --user --outdated 2>/dev/null \
      | grep -Ei '^mcp |openai|fastapi|uvicorn|huggingface' || ok "all current"
    return 0
  fi
  # PEP-668 "externally managed" environments (Arch, newer Debian) reject a
  # bare 'pip install --user'. --break-system-packages only touches ~/.local.
  # (Keep in sync with the identical detection in bootstrap.sh.)
  local pip_flags=""
  if python3 -c 'import sysconfig,os;p=sysconfig.get_path("stdlib");exit(0 if os.path.exists(os.path.join(p,"EXTERNALLY-MANAGED")) else 1)' 2>/dev/null; then
    pip_flags="--break-system-packages"
  fi
  # requirements.txt pins exact versions (supply-chain anchoring); a plain
  # `-U -r` would just reinstall the pins. --python is the SANCTIONED bump
  # path: upgrade the packages themselves, then rewrite the pins to match
  # what's now installed, so the next fresh install gets what we validated.
  python3 -m pip install --user $pip_flags -q -U huggingface_hub openai mcp fastapi uvicorn
  {
    echo "# Pinned to the exact versions validated on the reference box (supply-chain"
    echo "# anchoring: an unpinned install would pull whatever PyPI has newest,"
    echo "# including a compromised release). scripts/update.sh --python bumps these."
    for _pkg in openai mcp fastapi uvicorn; do
      _ver=$(python3 -m pip show "$_pkg" 2>/dev/null | awk '/^Version:/{print $2}')
      [[ -n "$_ver" ]] && echo "${_pkg}==${_ver}"
    done
  } > "$REPO_DIR/agents/requirements.txt"
  ok "upgraded + pins rewritten: $(grep -v '^#' "$REPO_DIR/agents/requirements.txt" | tr '\n' ' ')"
  warn "a running MCPO/mcp_server keeps old code until restarted"
  warn "commit the requirements.txt pin bump after verifying the stack"
}

# ---- OpenCode ---------------------------------------------------------------
update_opencode() {
  step "OpenCode"
  if ! command -v opencode >/dev/null 2>&1; then
    warn "opencode not installed — skip (install: curl -fsSL https://opencode.ai/install | bash)"
    return 0
  fi
  if [[ $CHECK_ONLY -eq 1 ]]; then
    ok "installed: $(opencode --version 2>/dev/null || echo '?')"
    return 0
  fi
  if opencode upgrade; then
    ok "opencode now $(opencode --version 2>/dev/null || echo '?')"
  else
    warn "'opencode upgrade' failed — reinstall with: curl -fsSL https://opencode.ai/install | bash"
  fi
}

[[ $DO_LLAMA -eq 1 ]]    && update_llama
[[ $DO_IMAGES -eq 1 ]]   && update_images
[[ $DO_PYTHON -eq 1 ]]   && update_python
[[ $DO_OPENCODE -eq 1 ]] && update_opencode

step "${c_grn}Done${c_rst}"
if [[ $CHECK_ONLY -eq 0 ]]; then
  echo "  Restart the stack to run on the fresh versions:"
  echo "      ${c_bold}./stop.sh && ./start.sh${c_rst}"
  echo "  Model weights are versionless snapshots — re-download only when a"
  echo "  repo publishes improved quants (see docs/UPDATING.md)."
fi
