#!/bin/bash
# OpenBeast — one-command bootstrap: git clone → working stack.
#
#   ./bootstrap.sh              # full setup, then offer to launch the stack
#   ./bootstrap.sh --preflight  # read-only environment check: runs every
#                               # prerequisite probe (toolchain, GPU, Docker,
#                               # disk space), prints a ✓/✗ summary, exits
#                               # 0 (ready) / 1 (missing prereqs). Installs,
#                               # builds, downloads and writes NOTHING.
#   ./bootstrap.sh --no-start   # set everything up, don't launch
#   ./bootstrap.sh --minimal    # Tier 0: build + one weight only, no Docker
#                               # frontends (just llama-server; see README)
#
# Idempotent: every step skips work that's already done, so re-running after
# a failure resumes rather than restarts. It installs the light things
# (Python packages) but only CHECKS the heavy system deps (NVIDIA driver,
# CUDA, Docker) and prints exact per-distro install commands if they're
# missing — bootstrapping a GPU driver unattended is not something you want
# a script doing behind your back.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

START_STACK="ask"
MINIMAL=0
PREFLIGHT=0
for arg in "$@"; do
  case "$arg" in
    --no-start)  START_STACK="no" ;;
    --minimal)   MINIMAL=1 ;;
    --preflight) PREFLIGHT=1 ;;
    -h|--help)   sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

# ---- pretty output ---------------------------------------------------------
c_bold=$'\033[1m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_rst=$'\033[0m'
step() { echo; echo "${c_bold}==> $*${c_rst}"; }
ok()   { echo "  ${c_grn}✓${c_rst} $*"; pf_record pass "$*"; }
warn() { echo "  ${c_ylw}!${c_rst} $*"; pf_record warn "$*"; }
die()  { echo "  ${c_red}✗ $*${c_rst}" >&2; exit 1; }

# ---- preflight bookkeeping --------------------------------------------------
# Under --preflight every ok/warn is also recorded as a table row; hard_fail
# flags the most recent row as a real failure (✗) and sets MISSING — in the
# normal flow it behaves exactly like the old inline `MISSING=1`.
PF_STATUS=(); PF_LABEL=()
pf_record() { # $1 = pass|warn, $2 = label
  [[ $PREFLIGHT -eq 1 ]] || return 0
  PF_STATUS+=("$1"); PF_LABEL+=("$2")
}
pf_mark_fail() {
  [[ $PREFLIGHT -eq 1 ]] || return 0
  local i=$(( ${#PF_STATUS[@]} - 1 ))
  [[ $i -ge 0 ]] && PF_STATUS[i]="fail"
  return 0
}
hard_fail() { MISSING=1; pf_mark_fail; }

# ---- distro detection ------------------------------------------------------
DISTRO="unknown"; PKG_HINT=""
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  case "${ID:-} ${ID_LIKE:-}" in
    *arch*)          DISTRO="arch" ;;
    *debian*|*ubuntu*) DISTRO="debian" ;;
    *fedora*|*rhel*) DISTRO="fedora" ;;
  esac
fi
pkg_install_hint() { # $1 = package concept: git|cmake|curl|toolchain|python|pip|...
  # Map the concept to the distro's real package name(s) first — a Debian
  # user must never be told to install Arch's base-devel (and vice versa).
  local pkg="$1"
  case "$DISTRO:$1" in
    arch:toolchain)   pkg="base-devel" ;;
    debian:toolchain) pkg="build-essential" ;;
    fedora:toolchain) pkg="gcc gcc-c++ make" ;;
    arch:python)      pkg="python" ;;
    debian:python)    pkg="python3" ;;
    fedora:python)    pkg="python3" ;;
    arch:pip)         pkg="python-pip" ;;
    debian:pip)       pkg="python3-pip" ;;
    fedora:pip)       pkg="python3-pip" ;;
  esac
  case "$DISTRO" in
    arch)   echo "sudo pacman -S --needed $pkg" ;;
    debian) echo "sudo apt-get install -y $pkg" ;;
    fedora) echo "sudo dnf install -y $pkg" ;;
    *)      echo "install '$pkg' with your package manager" ;;
  esac
}

# ---- 1. preflight: check the heavy deps, guide if missing ------------------
# All environment checks live in run_preflight so the normal bootstrap flow
# and --preflight run the IDENTICAL probes. Pure read-only: nothing here may
# install, write a file, or mkdir.
need() { # need <cmd> <concept-for-hint> <why>
  if command -v "$1" >/dev/null 2>&1; then ok "$1 present"; else
    warn "$1 missing — $3"; echo "      → $(pkg_install_hint "$2")"; hard_fail
  fi
}

run_preflight() {
  step "Preflight checks (distro: $DISTRO)"
  MISSING=0
  need git    git       "needed to fetch llama.cpp"
  need cmake  cmake     "needed to build llama.cpp"
  need make   toolchain "drives the llama.cpp build"
  need gcc    toolchain "C/C++ toolchain for the build"
  need curl   curl      "health probes + installers use it"
  need python3 python   "runs the MCP tool server + eval harness"
  # pip as a module — some distros ship python3 without it.
  if python3 -m pip --version >/dev/null 2>&1; then
    ok "python3 -m pip present"
  else
    warn "python3 -m pip missing — needed to install the Python deps"
    echo "      → $(pkg_install_hint pip)"
    hard_fail
  fi

  # GPU + driver — detect vendor/VRAM, print the profile recommendation, and
  # resolve the llama.cpp build backend (GPU_BACKEND in openbeast.conf, or
  # auto → vendor mapping). Reference profile is CUDA on a 5090; HIP/SYCL/CPU
  # are built but UNTESTED — see docs/HARDWARE_PROFILES.md.
  source "$REPO_DIR/scripts/lib/conf.sh"
  source "$REPO_DIR/scripts/lib/hardware.sh"
  ob_detect_gpu
  ob_resolve_backend
  case "$OB_GPU_VENDOR" in
    nvidia) ok "NVIDIA GPU: ${OB_GPU_NAME:-unknown} (${OB_VRAM_MB} MiB VRAM, ${OB_GPU_COUNT}x)" ;;
    amd)    ok "AMD GPU: ${OB_GPU_NAME:-unknown} (${OB_VRAM_MB} MiB VRAM, ${OB_GPU_COUNT}x)" ;;
    intel)  ok "Intel GPU: ${OB_GPU_NAME:-unknown}" ;;
    none)   warn "no supported GPU detected" ;;
  esac
  ob_profile_advice
  # Opinionated floor: detected GPUs under 11 GB VRAM are not supported —
  # see ob_vram_floor_check in scripts/lib/hardware.sh for the reasoning
  # and the OPENBEAST_FORCE_VRAM=1 escape hatch.
  if ! ob_vram_floor_check; then
    hard_fail
  fi
  ok "llama.cpp build backend: $OB_BACKEND (GPU_BACKEND=$GPU_BACKEND)"
  case "$OB_BACKEND" in
    hip|sycl) warn "the '$OB_BACKEND' backend is UNTESTED by OpenBeast — reference profile is CUDA/5090." ;;
    cpu)      warn "CPU-only build — inference will be 10-50x slower than on a GPU." ;;
  esac

  # Backend toolchain (nvcc / hipcc+rocminfo / icpx) — checked via the shared
  # lib so update.sh preflights the exact same way.
  if ob_backend_preflight; then
    case "$OB_BACKEND" in
      cuda) ok "CUDA toolkit: $(nvcc --version | grep -oE 'release [0-9.]+' | head -1)" ;;
      hip)  ok "ROCm toolchain present (hipcc + rocminfo)" ;;
      sycl) ok "oneAPI toolchain present (icpx)" ;;
      cpu)  ok "CPU backend — no GPU toolchain needed" ;;
    esac
  else
    warn "toolchain for the '$OB_BACKEND' backend is missing (details above)."
    hard_fail
  fi

  # Docker (only for the full stack; Tier 0 doesn't need it)
  if [[ $MINIMAL -eq 0 ]]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      ok "Docker daemon reachable"
      if docker compose version >/dev/null 2>&1; then
        ok "docker compose (v2 plugin) present"
      else
        warn "'docker compose' not working — the compose v2 PLUGIN is required"
        echo "      → install the docker-compose-plugin package (Debian/Ubuntu:"
        echo "        docker-compose-plugin; Arch: docker-compose; Fedora:"
        echo "        docker-compose-plugin). The legacy python docker-compose"
        echo "        v1 binary is NOT enough."
        hard_fail
      fi
    else
      warn "Docker not usable — needed for Open WebUI + SearXNG (skip with --minimal)."
      echo "      → install Docker, 'sudo systemctl enable --now docker', and add"
      echo "        yourself to the docker group: 'sudo usermod -aG docker \$USER'"
      echo "        (log out/in afterward), or run with --minimal for CLI-only."
      hard_fail
    fi
  fi
}

# ---- preflight-only extras ---------------------------------------------------
# Checks that only run under --preflight: they add information (disk space,
# kernel, docker-group diagnosis) without changing the normal bootstrap flow.
preflight_extras() {
  step "System"
  ok "Linux kernel $(uname -r) ($(uname -m)) — note: Docker + NVIDIA need a reasonably current kernel"

  # Docker present but daemon unreachable → say WHY (ownership vs daemon).
  if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
    if [[ -S /var/run/docker.sock ]]; then
      if id -nG 2>/dev/null | grep -qw docker; then
        warn "docker socket exists and you are in the 'docker' group, but the daemon isn't answering — is the service running? (sudo systemctl start docker)"
      else
        warn "docker socket exists but you are NOT in the 'docker' group — 'sudo usermod -aG docker \$USER' then log out/in"
      fi
    else
      warn "docker installed but /var/run/docker.sock is absent — daemon not running (sudo systemctl enable --now docker)"
    fi
  fi

  step "Weights disk space"
  # Resolve WEIGHTS_DIR exactly like the serve scripts do, but READ-ONLY:
  # lib/weights.sh exits 1 when the dir doesn't exist yet (normal on a fresh
  # box), so source it in a throwaway bash and capture the resolved path via
  # an EXIT trap. No mkdir, no conf writes.
  local resolved probe avail_gb
  resolved=$(REPO_DIR="$REPO_DIR" bash -c \
    'trap '\''printf "%s" "${WEIGHTS_DIR:-}"'\'' EXIT; source "$REPO_DIR/scripts/lib/weights.sh"' 2>/dev/null) || true
  if [[ -z "$resolved" ]]; then
    warn "could not resolve the weights directory (scripts/lib/weights.sh)"
    return 0
  fi
  resolved=$(realpath -m "$resolved" 2>/dev/null || echo "$resolved")
  # Walk up to the nearest EXISTING ancestor so df has something to measure
  # (the weights dir itself usually doesn't exist before bootstrap).
  probe="$resolved"
  while [[ ! -d "$probe" && "$probe" != "/" ]]; do probe="$(dirname "$probe")"; done
  avail_gb=$(df -Pk "$probe" 2>/dev/null | awk 'NR==2 {print int($4/1024/1024)}') || avail_gb=""
  if [[ -z "$avail_gb" ]]; then
    warn "weights dir resolves to $resolved but free space on $probe could not be measured"
  elif [[ "$avail_gb" -ge 25 ]]; then
    if [[ -d "$resolved" ]]; then
      ok "weights dir: $resolved — ${avail_gb} GB free (default 27B weight needs ~21 GB)"
    else
      ok "weights dir will be $resolved (created by bootstrap) — ${avail_gb} GB free (need ~25 GB)"
    fi
  else
    warn "weights dir $resolved has only ${avail_gb} GB free — the default 27B weight needs ~21 GB (~25 GB recommended). Point WEIGHTS_DIR at a bigger disk (openbeast.conf)."
  fi
}

pf_summary() {
  step "Preflight summary"
  local i n=${#PF_STATUS[@]} n_ok=0 n_warn=0 n_fail=0
  for ((i = 0; i < n; i++)); do
    case "${PF_STATUS[i]}" in
      pass) printf '  %s✓%s %s\n' "$c_grn" "$c_rst" "${PF_LABEL[i]}"; n_ok=$((n_ok + 1)) ;;
      warn) printf '  %s!%s %s\n' "$c_ylw" "$c_rst" "${PF_LABEL[i]}"; n_warn=$((n_warn + 1)) ;;
      fail) printf '  %s✗%s %s\n' "$c_red" "$c_rst" "${PF_LABEL[i]}"; n_fail=$((n_fail + 1)) ;;
    esac
  done
  echo
  echo "  ${n_ok} ok, ${n_warn} warnings, ${n_fail} failures"
  if [[ $n_fail -gt 0 ]]; then
    echo "  ${c_red}Fix the ✗ items above, then run ./bootstrap.sh${c_rst}"
  else
    echo "  ${c_grn}Environment looks ready — run ./bootstrap.sh to install.${c_rst}"
  fi
}

run_preflight

if [[ $PREFLIGHT -eq 1 ]]; then
  preflight_extras
  pf_summary
  exit "$MISSING"
fi

[[ $MISSING -eq 1 ]] && die "Missing prerequisites above. Install them and re-run ./bootstrap.sh"

# ---- 2. build llama.cpp (skip if already built) ---------------------------
step "llama.cpp (${OB_BACKEND^^} build)"
LLAMA_BIN="$REPO_DIR/llama.cpp/build/bin/llama-server"
if [[ -x "$LLAMA_BIN" ]]; then
  ok "already built ($LLAMA_BIN)"
else
  # Flags come from the shared lib (scripts/lib/hardware.sh) so bootstrap
  # and update.sh can never drift. cuda: -DGGML_CUDA=ON + detected arch
  # (the reference profile, unchanged); hip/sycl/cpu per the backend.
  CMAKE_FLAGS="$(ob_cmake_flags)" \
    || die "unknown GPU_BACKEND '$GPU_BACKEND' (valid: auto | cuda | hip | sycl | cpu)"
  ok "backend $OB_BACKEND → cmake flags: ${CMAKE_FLAGS:-none (CPU-only)}"
  [[ -d "$REPO_DIR/llama.cpp/.git" ]] || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$REPO_DIR/llama.cpp"
  # $CMAKE_FLAGS is deliberately unquoted — it's a flag list.
  cmake -S "$REPO_DIR/llama.cpp" -B "$REPO_DIR/llama.cpp/build" \
        $CMAKE_FLAGS -DCMAKE_BUILD_TYPE=Release
  cmake --build "$REPO_DIR/llama.cpp/build" --config Release -j"$(nproc)" --target llama-server
  [[ -x "$LLAMA_BIN" ]] && ok "built $LLAMA_BIN" || die "build did not produce llama-server"
fi

# Persist the resolved backend so scripts/update.sh --llama rebuilds with
# the SAME flavor instead of re-guessing (docs/HARDWARE_PROFILES.md Phase 1).
CONF_FILE="$REPO_DIR/openbeast.conf"
if [[ ! -f "$CONF_FILE" ]]; then
  {
    echo "# OpenBeast configuration — created by bootstrap.sh."
    echo "# All available keys: openbeast.conf.example"
    echo "GPU_BACKEND=$OB_BACKEND"
  } > "$CONF_FILE"
  ok "created openbeast.conf with GPU_BACKEND=$OB_BACKEND"
elif grep -qE '^[[:space:]]*GPU_BACKEND[[:space:]]*=' "$CONF_FILE"; then
  sed -i -E "s|^[[:space:]]*GPU_BACKEND[[:space:]]*=.*|GPU_BACKEND=$OB_BACKEND|" "$CONF_FILE"
  ok "updated GPU_BACKEND=$OB_BACKEND in openbeast.conf"
else
  echo "GPU_BACKEND=$OB_BACKEND" >> "$CONF_FILE"
  ok "persisted GPU_BACKEND=$OB_BACKEND in openbeast.conf"
fi

# ---- 3. Python dependencies ------------------------------------------------
step "Python dependencies"
PIP_FLAGS=""
# PEP-668 "externally managed" environments (Arch, newer Debian) reject a
# bare 'pip install --user'. Use --break-system-packages there; it only
# touches ~/.local, never system site-packages.
if python3 -c 'import sysconfig,os;p=sysconfig.get_path("stdlib");exit(0 if os.path.exists(os.path.join(p,"EXTERNALLY-MANAGED")) else 1)' 2>/dev/null; then
  PIP_FLAGS="--break-system-packages"
  warn "externally-managed Python → using --user --break-system-packages (~/.local only)"
fi
# huggingface_hub 1.x ships the `hf` CLI in the base package (the old [cli]
# extra now warns); -U pulls a current version so the base package is enough.
python3 -m pip install --user $PIP_FLAGS -q -U "huggingface_hub" -r "$REPO_DIR/agents/requirements.txt"
ok "installed huggingface_hub + $(tr '\n' ' ' < "$REPO_DIR/agents/requirements.txt")"
# hf / mcpo land in ~/.local/bin — make sure it's reachable for this run
export PATH="$HOME/.local/bin:$PATH"
command -v hf >/dev/null 2>&1 || command -v huggingface-cli >/dev/null 2>&1 \
  || warn "hf CLI not on PATH — add 'export PATH=\$HOME/.local/bin:\$PATH' to your shell rc"

# ---- 4. default model weight (skip if present) -----------------------------
step "Default model weight (~21 GB — the one big download)"
# Resolve the weights dir the same way the serve scripts do. On a fresh
# machine no weights dir exists anywhere and weights.sh would hard-exit with
# its "point OpenBeast at your weights" guidance — correct for serve scripts,
# fatal for the bootstrapper whose job is to create it. OPENBEAST_WEIGHTS_MKDIR
# tells the resolver to mkdir the RESOLVED dir instead (custom env/conf paths
# are still preferred as usual).
export OPENBEAST_WEIGHTS_MKDIR=1
source "$REPO_DIR/scripts/lib/weights.sh"
unset OPENBEAST_WEIGHTS_MKDIR
WEIGHT_FILE="Qwen3.6-27B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf"
HF_REPO="HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive"
if [[ -f "$WEIGHTS_DIR/$WEIGHT_FILE" ]]; then
  ok "already downloaded ($WEIGHTS_DIR/$WEIGHT_FILE)"
else
  warn "downloading the default 27B model — this is the long step, grab coffee."
  HF_BIN="$(command -v hf || command -v huggingface-cli || true)"
  [[ -n "$HF_BIN" ]] || die "hf CLI not found after install; add ~/.local/bin to PATH and re-run"
  "$HF_BIN" download "$HF_REPO" "$WEIGHT_FILE" --local-dir "$WEIGHTS_DIR"
  [[ -f "$WEIGHTS_DIR/$WEIGHT_FILE" ]] && ok "downloaded to $WEIGHTS_DIR" || die "download failed"
fi

# ---- executable bits -------------------------------------------------------
chmod +x "$REPO_DIR"/*.sh "$REPO_DIR"/scripts/*.sh "$REPO_DIR"/tests/*.sh 2>/dev/null || true

# ---- Tier 0 (minimal) exit -------------------------------------------------
if [[ $MINIMAL -eq 1 ]]; then
  step "${c_grn}Tier 0 ready${c_rst}"
  echo "  Start just the model server (no Docker, no auth, no tools):"
  echo "      ${c_bold}./scripts/serve-qwen-27b-uncensored-q5.sh${c_rst}"
  echo "  Then talk to it:"
  echo "      curl http://localhost:8080/v1/models"
  echo "      point any OpenAI-compatible client at http://localhost:8080/v1"
  exit 0
fi

# ---- 5. Docker images for the frontends ------------------------------------
step "Frontend images (Open WebUI + SearXNG)"
docker pull -q ghcr.io/open-webui/open-webui:main >/dev/null && ok "open-webui image ready" \
  || warn "open-webui image pull FAILED (network/registry?) — ./start.sh will retry the pull"
docker pull -q searxng/searxng:latest             >/dev/null && ok "searxng image ready" \
  || warn "searxng image pull FAILED (network/registry?) — ./start.sh will retry the pull"

# ---- OpenCode (optional terminal frontend) ---------------------------------
if ! command -v opencode >/dev/null 2>&1; then
  warn "OpenCode (terminal agent) not installed — optional."
  echo "      → install later with: curl -fsSL https://opencode.ai/install | bash"
fi

# ---- Done ------------------------------------------------------------------
step "${c_grn}${c_bold}OpenBeast is ready.${c_rst}"
echo "  On first launch the full stack comes up with ALL tools wired and no"
echo "  login wall (WEBUI_AUTH=false) — the complete demo experience. Add"
echo "  secure remote access anytime with ./scripts/setup-tailscale.sh"
echo "  (which turns on per-user login + RBAC)."
echo
echo "  Chat UI:   http://localhost:3000"
echo "  Model API: http://localhost:8080/v1   (OpenAI-compatible)"
echo "  Terminal:  run 'opencode' in any project directory"
echo

if [[ "$START_STACK" == "ask" && -t 0 ]]; then
  read -r -p "  Launch the stack now? [Y/n] " ans
  [[ "$ans" =~ ^[Nn] ]] && START_STACK="no" || START_STACK="yes"
fi
if [[ "$START_STACK" == "yes" ]]; then
  step "Launching the stack (Ctrl-C to stop; or ./stop.sh from another terminal)"
  exec "$REPO_DIR/start.sh"
else
  echo "  Start it whenever you're ready:  ${c_bold}./start.sh${c_rst}"
fi
