#!/bin/bash
# OpenBeast — one-command bootstrap: git clone → working stack.
#
#   ./bootstrap.sh              # full setup, then offer to launch the stack
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
for arg in "$@"; do
  case "$arg" in
    --no-start) START_STACK="no" ;;
    --minimal)  MINIMAL=1 ;;
    -h|--help)  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

# ---- pretty output ---------------------------------------------------------
c_bold=$'\033[1m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_rst=$'\033[0m'
step() { echo; echo "${c_bold}==> $*${c_rst}"; }
ok()   { echo "  ${c_grn}✓${c_rst} $*"; }
warn() { echo "  ${c_ylw}!${c_rst} $*"; }
die()  { echo "  ${c_red}✗ $*${c_rst}" >&2; exit 1; }

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
pkg_install_hint() { # $1 = generic package concept
  case "$DISTRO" in
    arch)   echo "sudo pacman -S --needed $1" ;;
    debian) echo "sudo apt-get install -y $1" ;;
    fedora) echo "sudo dnf install -y $1" ;;
    *)      echo "install '$1' with your package manager" ;;
  esac
}

# ---- 1. preflight: check the heavy deps, guide if missing ------------------
step "Preflight checks (distro: $DISTRO)"
MISSING=0
need() { # need <cmd> <concept-for-hint> <why>
  if command -v "$1" >/dev/null 2>&1; then ok "$1 present"; else
    warn "$1 missing — $3"; echo "      → $(pkg_install_hint "$2")"; MISSING=1
  fi
}
need git   git             "needed to fetch llama.cpp"
need cmake  cmake          "needed to build llama.cpp"
need gcc    "gcc base-devel" "C/C++ toolchain for the build"
need python3 python        "runs the MCP tool server + eval harness"

# GPU + driver
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
  ok "NVIDIA GPU: $GPU_NAME"
else
  warn "nvidia-smi not working — an NVIDIA GPU + driver is required."
  echo "      → install the proprietary NVIDIA driver for your distro, then re-run."
  MISSING=1
fi

# CUDA toolkit (nvcc). Arch keeps it in /opt/cuda/bin, off PATH by default.
if ! command -v nvcc >/dev/null 2>&1; then
  for d in /opt/cuda/bin /usr/local/cuda/bin; do
    [[ -x "$d/nvcc" ]] && export PATH="$d:$PATH"
  done
fi
if command -v nvcc >/dev/null 2>&1; then
  ok "CUDA toolkit: $(nvcc --version | grep -oE 'release [0-9.]+' | head -1)"
else
  warn "nvcc (CUDA toolkit) missing — needed to build llama.cpp with GPU support."
  echo "      → $(pkg_install_hint cuda)  (Arch: adds /opt/cuda/bin)"
  MISSING=1
fi

# Docker (only for the full stack; Tier 0 doesn't need it)
if [[ $MINIMAL -eq 0 ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker daemon reachable"
  else
    warn "Docker not usable — needed for Open WebUI + SearXNG (skip with --minimal)."
    echo "      → install Docker, 'sudo systemctl enable --now docker', and add"
    echo "        yourself to the docker group: 'sudo usermod -aG docker \$USER'"
    echo "        (log out/in afterward), or run with --minimal for CLI-only."
    MISSING=1
  fi
fi

[[ $MISSING -eq 1 ]] && die "Missing prerequisites above. Install them and re-run ./bootstrap.sh"

# ---- 2. build llama.cpp (skip if already built) ---------------------------
step "llama.cpp (CUDA build)"
LLAMA_BIN="$REPO_DIR/llama.cpp/build/bin/llama-server"
if [[ -x "$LLAMA_BIN" ]]; then
  ok "already built ($LLAMA_BIN)"
else
  # || true: under pipefail a failing nvidia-smi would kill the script
  # inside the substitution, never reaching the :-120 fallback.
  CUDA_ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d '.' || true)
  CUDA_ARCH="${CUDA_ARCH:-120}"
  ok "GPU compute capability → CMAKE_CUDA_ARCHITECTURES=$CUDA_ARCH"
  [[ -d "$REPO_DIR/llama.cpp/.git" ]] || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$REPO_DIR/llama.cpp"
  cmake -S "$REPO_DIR/llama.cpp" -B "$REPO_DIR/llama.cpp/build" \
        -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$REPO_DIR/llama.cpp/build" --config Release -j"$(nproc)" --target llama-server
  [[ -x "$LLAMA_BIN" ]] && ok "built $LLAMA_BIN" || die "build did not produce llama-server"
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
# Pre-create the default weights dir BEFORE sourcing the resolver: on a
# fresh clone no weights dir exists anywhere and weights.sh hard-exits with
# its "point OpenBeast at your weights" guidance — correct for serve scripts,
# fatal for the bootstrapper whose job is to create it. If the user already
# configured a custom dir (env/conf), the resolver prefers that as usual.
mkdir -p "$REPO_DIR/weights"
# Resolve the weights dir the same way the serve scripts do.
source "$REPO_DIR/scripts/lib/weights.sh"
: "${WEIGHTS_DIR:=$REPO_DIR/weights}"
mkdir -p "$WEIGHTS_DIR"
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
docker pull -q ghcr.io/open-webui/open-webui:main >/dev/null && ok "open-webui image ready"
docker pull -q searxng/searxng:latest             >/dev/null && ok "searxng image ready"

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
