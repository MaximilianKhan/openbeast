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
#   ./bootstrap.sh --cpu        # build CPU-only ON PURPOSE (10-50x slower).
#                               # Without it a box with no working GPU is
#                               # refused, not quietly given a CPU build.
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
    # The env name, which wins over the conf key (scripts/lib/conf.sh): this
    # run is CPU-only whatever openbeast.conf says.
    --cpu)       export OPENBEAST_GPU_BACKEND=cpu ;;
    -h|--help)   sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
# Under --preflight every ok/warn is also recorded as a row; hard_fail flags
# the most recent row as a real failure (✗) and sets MISSING — in the normal
# flow it behaves exactly like the old inline `MISSING=1`.
#
# `hard_fail "<reason>"` records its OWN row instead. That is for a failure
# whose explanation was printed by someone else (scripts/lib/hardware.sh
# prints the VRAM-floor verdict itself): with no row of its own, the ✗ landed
# on whatever was recorded last — `✗ NVIDIA GPU: … (16376 MiB VRAM)`, a
# failure with no reason attached to it.
PF_STATUS=(); PF_LABEL=()
pf_record() { # $1 = pass|warn|fail, $2 = label
  [[ $PREFLIGHT -eq 1 ]] || return 0
  PF_STATUS+=("$1"); PF_LABEL+=("$2")
}
pf_mark_fail() {
  [[ $PREFLIGHT -eq 1 ]] || return 0
  local i=$(( ${#PF_STATUS[@]} - 1 ))
  [[ $i -ge 0 ]] && PF_STATUS[i]="fail"
  return 0
}
hard_fail() {
  MISSING=1
  if [[ $# -gt 0 ]]; then pf_record fail "$1"; else pf_mark_fail; fi
}

# ---- wrong-machine off-ramp -------------------------------------------------
# The rig is CUDA/Linux only, by design. But the README invites macOS and
# GPU-less users to install CLIENT mode, and `./bootstrap.sh` is the headline
# command they'll type first. Without this they hit a missing-C-toolchain error
# carrying a Linux package hint (macOS), or — worse on GPU-less Linux, where no
# VRAM floor rejection fires — a full llama.cpp build and a ~20 GB weight
# download for a CPU-only install nobody wants. Signpost instead of dead-end.
if [[ "$(uname -s)" == "Darwin" ]]; then
  cat >&2 <<'EOF'
This is macOS — OpenBeast's rig (server) is Linux + NVIDIA/CUDA only.

You almost certainly want CLIENT mode, which is fully supported here: it runs
OpenCode and the complete 18-tool arsenal on THIS Mac, against this Mac's own
files, and sends only inference to a rig over your tailnet. No GPU, no CUDA,
no model download.

    ./scripts/setup-client.sh --host <rig>.<tailnet>.ts.net

Details (including using a rig someone else hosts): docs/BEAST_SLOT.md
EOF
  exit 2
fi

# ---- not as root -------------------------------------------------------------
# Everything here installs into the invoking user's account: python packages
# under ~/.local, llama.cpp/ and the weights beside the checkout, a mode-600
# openbeast.conf. The usual reason for `sudo ./bootstrap.sh` is a docker
# permission error, and the result is a stack owned by root that the user's
# own ./start.sh can then neither read nor import. `id -u`, not $EUID, so the
# guard can be exercised without being root.
if [[ "$(id -u)" -eq 0 && "${OPENBEAST_ALLOW_ROOT:-0}" != "1" ]]; then
  cat >&2 <<'EOF'
Do not run bootstrap.sh as root (or with sudo): everything it installs goes
into the invoking user's account, and a root-owned install cannot be started
or updated by you afterwards. Nothing was installed.

If you reached for sudo because docker refused you, fix that instead:

    sudo usermod -aG docker $USER     # then log out and back in
    ./bootstrap.sh

(A container whose only user IS root: OPENBEAST_ALLOW_ROOT=1 ./bootstrap.sh)
EOF
  exit 2
fi

# The one weight bootstrap downloads. Up here, not in step 4, because the disk
# check in the preflight needs to know what it is making room for.
WEIGHT_FILE="Qwen3.8-27B-Uncensored-Q5_K_M.gguf"
HF_REPO="JonathanColetti/Qwen3.8-27B-Uncensored-GGUF"

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

# An NVIDIA card whose driver is not answering — the ordinary state after a
# kernel update, before the reboot or the DKMS rebuild. Prints the reason and
# returns 0 for that case only; a working driver, or no NVIDIA card at all,
# returns 1.
#
# This has to be asked BEFORE ob_detect_gpu's answer is believed. Detection
# tries nvidia-smi and, when it fails, simply moves on: a Ryzen iGPU beside
# the dead 4090 was reported as "AMD GPU, 2048 MiB, below the 24 GB floor,
# install rocm-hip-sdk" — three wrong diagnoses — and with no iGPU the box
# was "no GPU", which went on to a 10-40 minute CPU build, pinned
# GPU_BACKEND=cpu in openbeast.conf and pulled 20 GB of weights.
ob_nvidia_broken() {
  local out pci
  if command -v nvidia-smi >/dev/null 2>&1; then
    out="$(nvidia-smi 2>&1)" && return 1
    out="$(sed -n '/[^[:space:]]/{p;q;}' <<< "$out")"
    echo "nvidia-smi: ${out:-exited non-zero and said nothing}"
    return 0
  fi
  # No nvidia-smi at all, but the card is on the bus: the driver was never
  # installed (or was removed). Captured first, never `lspci | grep -q` — under
  # pipefail grep's early exit turns a MATCH into a failed pipeline.
  command -v lspci >/dev/null 2>&1 || return 1
  pci="$(lspci 2>/dev/null || true)"
  if grep -qiE '(VGA|3D|Display)[^:]*:.*NVIDIA' <<< "$pci"; then
    echo "nvidia-smi is not installed, so there is no NVIDIA driver"
    return 0
  fi
  return 1
}

# GPU + backend. Returns early (with the failure recorded) on the two cases
# where everything further down would be reasoning from a wrong premise.
preflight_gpu() {
  ob_detect_gpu
  ob_resolve_backend
  # Only when the backend is ours to choose (auto) or is CUDA: someone who
  # set GPU_BACKEND=hip|sycl|cpu has said which card they mean to use.
  local nv_why
  if [[ "$GPU_BACKEND" == "auto" || "$GPU_BACKEND" == "cuda" ]] && nv_why="$(ob_nvidia_broken)"; then
    warn "NVIDIA card found but the driver is not answering ($nv_why)"
    echo "      → fix the driver, then re-run. Nothing was built."
    echo "        After a kernel update that is usually a reboot; otherwise"
    echo "        reinstall the NVIDIA driver package for your distro and check"
    echo "        that 'nvidia-smi' prints your card."
    echo "      → not using an NVIDIA card on this box? Say which backend:"
    echo "        GPU_BACKEND=hip|sycl in openbeast.conf, or ./bootstrap.sh --cpu"
    hard_fail
    return 0
  fi
  case "$OB_GPU_VENDOR" in
    nvidia) ok "NVIDIA GPU: ${OB_GPU_NAME:-unknown} (${OB_VRAM_MB} MiB VRAM, ${OB_GPU_COUNT}x)" ;;
    amd)    ok "AMD GPU: ${OB_GPU_NAME:-unknown} (${OB_VRAM_MB} MiB VRAM, ${OB_GPU_COUNT}x)" ;;
    intel)  ok "Intel GPU: ${OB_GPU_NAME:-unknown}" ;;
    none)
      if [[ "$GPU_BACKEND" == "auto" ]]; then
        # A HARD FAIL. It was a warning, and nothing downstream stopped it
        # (ob_vram_floor_check returns early for vendor 'none'): auto resolved
        # to cpu, and the install went ahead with a build and a ~20 GB weight
        # for inference 10-50x slower than anyone wants. CPU-only is still
        # available — to someone who asks for it.
        warn "no supported GPU detected — not building a CPU-only stack unasked"
        echo "      → this box can't serve models, but it CAN be a client:"
        echo "        ./scripts/setup-client.sh --host <rig>.<tailnet>.ts.net"
        echo "        (full tool arsenal locally, inference on a rig — docs/BEAST_SLOT.md)"
        echo "      → to build CPU-only anyway (10-50x slower): ./bootstrap.sh --cpu"
        hard_fail
        return 0
      fi
      warn "no supported GPU detected (continuing: GPU_BACKEND=$GPU_BACKEND was set explicitly)"
      ;;
  esac
  ob_profile_advice
  # Opinionated floor: detected GPUs under 24 GB VRAM are not supported —
  # see ob_vram_floor_check in scripts/lib/hardware.sh for the reasoning
  # and the OPENBEAST_FORCE_VRAM=1 escape hatch.
  if ! ob_vram_floor_check; then
    hard_fail "${OB_GPU_NAME:-GPU}: ${OB_VRAM_MB} MiB VRAM is below the 24 GB floor — no setting fixes that on this card; OPENBEAST_FORCE_VRAM=1 proceeds unsupported"
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
    # The lib printed what is missing (and the fix) just above; the summary
    # needs the first of those lines, not "details above". Asked again in a
    # subshell because the real call must run HERE — it exports the PATH nvcc
    # was found on.
    local tc_why
    tc_why="$(ob_backend_preflight 2>&1 | sed -n '1p' || true)"
    tc_why="${tc_why#"${tc_why%%[![:space:]]*}"}"
    warn "toolchain for the '$OB_BACKEND' backend is missing: ${tc_why:-see above}"
    hard_fail
  fi
}

run_preflight() {
  step "Preflight checks (distro: $DISTRO)"
  MISSING=0
  need git    git       "needed to fetch llama.cpp"
  need cmake  cmake     "needed to build llama.cpp"
  need make   toolchain "drives the llama.cpp build"
  need gcc    toolchain "C compiler for the build"
  # llama.cpp is C++. gcc without g++ is an ordinary state (a minimal Debian
  # has it) and used to pass every check here, then fail inside cmake.
  need g++    toolchain "C++ compiler for the build (llama.cpp is C++)"
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
  #
  # --preflight promises to write NOTHING, and sourcing conf.sh was the one
  # thing that did: it generates SEARXNG_SECRET and creates openbeast.conf.
  # OB_CONF_READONLY=1 is conf.sh's switch for "resolve, do not persist".
  [[ $PREFLIGHT -eq 1 ]] && export OB_CONF_READONLY=1
  source "$REPO_DIR/scripts/lib/conf.sh"
  source "$REPO_DIR/scripts/lib/hardware.sh"
  preflight_gpu

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
# Checks that only run under --preflight: they add information (kernel,
# docker-group diagnosis) without changing the normal bootstrap flow.
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
}

# Room for the weight — in the NORMAL flow too, before the build. This check
# used to live in preflight_extras, i.e. only under --preflight: a real
# ./bootstrap.sh never looked, so a box with 15 GB free sat through the whole
# llama.cpp build and the pip install and then died mid-download on hf's raw
# "No space left on device". Sets PF_WEIGHT_PRESENT for the network verdict.
PF_WEIGHT_PRESENT=0
preflight_disk() {
  step "Weights disk space"
  # Resolve WEIGHTS_DIR exactly like the serve scripts do, but READ-ONLY:
  # lib/weights.sh exits 1 when the dir doesn't exist yet (normal on a fresh
  # box), so source it in a throwaway bash and capture the resolved path via
  # an EXIT trap. No mkdir, no conf writes.
  local resolved probe avail_kb avail_gb need_b need_gb
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
  avail_kb=$(df -Pk "$probe" 2>/dev/null | awk 'NR==2 {print $4}') || avail_kb=""
  [[ "$avail_kb" =~ ^[0-9]+$ ]] || avail_kb=""
  avail_gb=$(( ${avail_kb:-0} / 1024 / 1024 ))
  if [[ -f "$resolved/$WEIGHT_FILE" ]]; then
    PF_WEIGHT_PRESENT=1
    ok "weights dir: $resolved — the default weight is already there${avail_kb:+ (${avail_gb} GB free)}"
    return 0
  fi
  # The exact size, from the same registry row the download is verified
  # against — and the same rule scripts/fetch-weight.sh applies, so this
  # cannot pass a disk that the download step then refuses.
  need_b="$(awk -F'\t' -v f="$WEIGHT_FILE" '$3 == f {print $2}' "$REPO_DIR/scripts/weights.registry" 2>/dev/null || true)"
  [[ "$need_b" =~ ^[0-9]+$ ]] || need_b=20000000000
  need_gb="$(awk -v b="$need_b" 'BEGIN{printf "%.1f", b/1e9}')"
  if [[ -z "$avail_kb" ]]; then
    warn "weights dir resolves to $resolved but free space on $probe could not be measured (the default weight needs $need_gb GB)"
  elif [[ $(( avail_kb * 1024 )) -lt "$need_b" ]]; then
    warn "not enough disk for the default weight: need $need_gb GB in $resolved, have $(awk -v k="$avail_kb" 'BEGIN{printf "%.1f", k*1024/1e9}') GB free"
    echo "      → free some space there, or point WEIGHTS_DIR at a bigger disk:"
    echo "        WEIGHTS_DIR=/path/with/room in openbeast.conf"
    hard_fail
  elif [[ "$avail_gb" -ge 25 ]]; then
    if [[ -d "$resolved" ]]; then
      ok "weights dir: $resolved — ${avail_gb} GB free (default 27B weight needs $need_gb GB)"
    else
      ok "weights dir will be $resolved (created by bootstrap) — ${avail_gb} GB free (need ~25 GB)"
    fi
  else
    warn "weights dir $resolved has only ${avail_gb} GB free — the default 27B weight ($need_gb GB) fits, but ~25 GB is recommended. Point WEIGHTS_DIR at a bigger disk (openbeast.conf)."
  fi
}

# The network is a dependency like any other, and preflight never checked it.
# Eleven probes, all local, then the verdict "Environment looks ready — run
# ./bootstrap.sh to install" on a machine that dies ~2 seconds later at the
# git clone. `curl` was checked for PRESENCE and never used. An honest
# preflight says which hosts are unreachable and what that costs, so the
# operator finds out before the install does.
#
# A warning, not a failure: a box that already has llama.cpp/, its wheels and
# its weights can rebuild and serve perfectly well offline. What must not
# happen is a green verdict on a box that cannot finish a FIRST install.
#
# Run in the NORMAL flow too (it was --preflight only), where the question is
# sharper: is a host this run is about to need unreachable? See the verdict
# after the probes, below.
PF_NO_NET=0
PF_UNREACHABLE=()
# huggingface_hub honours HF_ENDPOINT (a mirror, an internal proxy), and so
# does scripts/fetch-weight.sh's own probe: ask the endpoint the download will
# actually use, or a box that reaches HF only through one is told "no route".
PF_HF_BASE="${HF_ENDPOINT:-https://huggingface.co}"; PF_HF_BASE="${PF_HF_BASE%/}"
PF_HF_HOST="${PF_HF_BASE#*://}"; PF_HF_HOST="${PF_HF_HOST%%/*}"
preflight_network() {
  # OFFLINE=true is the operator telling us there is no route. Probing anyway
  # costs three connect timeouts and produces a warning that reads like a
  # fault — the exact misdiagnosis this key exists to stop.
  if ob_offline; then
    ok "network: not probed (OFFLINE=true). An installed rig serves fine with
      no internet; what OFFLINE changes is that steps which cannot succeed are
      refused up front instead of stalling."
    PF_NO_NET=1
    return 0
  fi
  local unreachable=() url host
  for url in https://github.com https://pypi.org "$PF_HF_BASE"; do
    # -I, 6s, no retries: this is a reachability question, not a download.
    host="${url#*://}"; host="${host%%/*}"
    curl -fsS -I --max-time 6 --retry 0 -o /dev/null "$url" 2>/dev/null \
      || unreachable+=("$host")
  done
  if [[ ${#unreachable[@]} -eq 0 ]]; then
    ok "network: github.com, pypi.org and $PF_HF_HOST all reachable"
    return 0
  fi
  PF_NO_NET=1
  PF_UNREACHABLE=("${unreachable[@]}")
  warn "network: cannot reach ${unreachable[*]} — a FIRST install cannot complete"
  echo "      bootstrap needs all three: llama.cpp source (github), the python"
  echo "      wheels (pypi), and the ~20 GB default weight (huggingface), plus"
  echo "      two container images from ghcr.io/docker hub."
  echo "      An already-provisioned box can still rebuild and serve offline."
  echo "      Sideloading a single weight: ./scripts/fetch-weight.sh --list"
}

pf_summary() {
  step "Preflight summary"
  # COUNTS, THEN ONLY WHAT FAILED — each with its reason, once. This used to
  # replay every row: the whole run printed twice, the ✗ lines buried among
  # the ✓ ones, and a ✗ could carry a label with no reason in it. The fix for
  # each failure is printed where it was found, above.
  local i n=${#PF_STATUS[@]} n_ok=0 n_warn=0 n_fail=0
  for ((i = 0; i < n; i++)); do
    case "${PF_STATUS[i]}" in
      pass) n_ok=$((n_ok + 1)) ;;
      warn) n_warn=$((n_warn + 1)) ;;
      fail) n_fail=$((n_fail + 1)) ;;
    esac
  done
  echo "  ${n_ok} ok, ${n_warn} warnings, ${n_fail} failures"
  for ((i = 0; i < n; i++)); do
    [[ "${PF_STATUS[i]}" == "fail" ]] && printf '  %s✗%s %s\n' "$c_red" "$c_rst" "${PF_LABEL[i]}"
  done
  if [[ $n_fail -gt 0 ]]; then
    echo "  ${c_red}Fix the ✗ item(s) listed here — how is printed with each one, above —"
    echo "  then run ./bootstrap.sh${c_rst}"
    # A box with BOTH a local gap and no network used to hear only about the
    # local gap, so "fix these and install" was wrong twice over: you fix them
    # and still cannot fetch a single artifact. Say both.
    if [[ ${PF_NO_NET:-0} -eq 1 ]]; then
      echo "  ${c_red}…and the network is unreachable: a first install will fail"
      echo "  at the llama.cpp clone even once the ✗ items are fixed.${c_rst}"
    fi
  elif ob_offline; then
    # DELIBERATELY offline is not the same as unreachable, and saying
    # "not reachable" to an operator who configured OFFLINE=true reads as a
    # fault report about a decision they made.
    echo "  ${c_ylw}Local environment is ready. OFFLINE=true, so the four"
    echo "  fetches a first install needs are refused up front rather than"
    echo "  attempted: llama.cpp source, the python wheels, the weight, and"
    echo "  the two container images. Stage them once on a connected box —"
    echo "    ./scripts/pydeps.sh wheelhouse wheels"
    echo "    ./scripts/fetch-weight.sh --list"
    echo "  — and an existing install rebuilds and serves with no network at"
    echo "  all.${c_rst}"
  elif [[ ${PF_NO_NET:-0} -eq 1 ]]; then
    # Everything LOCAL is fine, so this is not a ✗ — but "looks ready" would
    # be a lie on a box that cannot fetch a single one of its four artifacts.
    echo "  ${c_ylw}Local environment is ready, but the network is not reachable."
    echo "  A first install will fail at the llama.cpp clone. An existing"
    echo "  install can still rebuild and serve."
    echo "  If that is permanent, set OFFLINE=true in openbeast.conf and the"
    echo "  installer will refuse those steps up front instead of stalling on"
    echo "  each one.${c_rst}"
  else
    echo "  ${c_grn}Environment looks ready — run ./bootstrap.sh to install.${c_rst}"
  fi
}

run_preflight
preflight_network
preflight_disk

if [[ $PREFLIGHT -eq 1 ]]; then
  preflight_extras
  pf_summary
  exit "$MISSING"
fi

[[ $MISSING -eq 1 ]] && die "Missing prerequisites above. Fix them and re-run ./bootstrap.sh — nothing was built or downloaded."

# The network, for a run that is about to USE it. --preflight reports an
# unreachable host as a warning (a provisioned box rebuilds and serves fine
# offline); here the question has an exact answer, so stop before the build
# rather than after it — the same 10-40 minutes the disk check saves.
if [[ ${#PF_UNREACHABLE[@]} -gt 0 ]]; then
  _ob_net_need=()
  if [[ " ${PF_UNREACHABLE[*]} " == *" github.com "* \
        && ! -x "$REPO_DIR/llama.cpp/build/bin/llama-server" \
        && ! -f "$REPO_DIR/llama.cpp/CMakeLists.txt" ]]; then
    _ob_net_need+=("github.com — the llama.cpp source is not here yet")
  fi
  if [[ " ${PF_UNREACHABLE[*]} " == *" $PF_HF_HOST "* && $PF_WEIGHT_PRESENT -eq 0 ]]; then
    _ob_net_need+=("$PF_HF_HOST — the default weight ($WEIGHT_FILE) is not here yet")
  fi
  if [[ ${#_ob_net_need[@]} -gt 0 ]]; then
    die "this install needs a host that cannot be reached:
$(printf '         %s\n' "${_ob_net_need[@]}")
       Nothing was built or downloaded. Check the connection (a VPN, a proxy,
       DNS) and re-run. On a network that is closed on purpose, set
       OFFLINE=true in openbeast.conf and bring the artifacts in:
         ./scripts/bundle.sh build ./bundle      (on a connected box)
         ./scripts/bundle.sh install ./bundle    (here)"
  fi
fi

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
  # The offline -DLLAMA_USE_PREBUILT_UI=OFF now comes from ob_cmake_flags
  # itself (scripts/lib/hardware.sh), so scripts/update.sh's rebuild gets it
  # too. It was inlined here, which meant the path advertised as the offline
  # work re-armed the 11-minute stall.
  if ob_offline; then
    warn "OFFLINE=true → building with -DLLAMA_USE_PREBUILT_UI=OFF (that
      fetch would stall ~11 min and then fail; the stack serves Open WebUI,
      not llama.cpp's bundled one)"
  fi
  ok "backend $OB_BACKEND → cmake flags: ${CMAKE_FLAGS:-none (CPU-only)}"
  # "CAN I BUILD?" not "is there a clone?". The guard used to test for
  # llama.cpp/.git, and `bundle.sh install` deliberately lays down a SOURCE
  # tree with no git history (a `git archive`, so the commit is recorded and
  # build/ is excluded). So the documented closed-network sequence — bundle
  # install, set OFFLINE=true, ./bootstrap.sh — died on "there is no
  # llama.cpp/ tree to build" with the tree sitting right there. Two features
  # of mine that were each tested alone and never together.
  #
  # WHICH llama.cpp. scripts/llama.cpp.ref pins the commit (its header says
  # why); a fresh install fetches exactly that one. An EXISTING clone is never
  # moved from here — it may be a tree someone is working in — so one that is
  # somewhere else is built as it stands, and says so.
  LLAMA_CPP_URL="https://github.com/ggml-org/llama.cpp.git"
  LLAMA_CPP_REF="$(sed -nE 's/^LLAMA_CPP_REF=([0-9a-f]{40})[[:space:]]*$/\1/p' \
                     "$REPO_DIR/scripts/llama.cpp.ref" 2>/dev/null | tail -n1 || true)"
  ob_fetch_llama_pin() {
    local d="$REPO_DIR/llama.cpp"
    # Built up step by step (a clone cannot be asked for a commit), and every
    # step is safe to repeat: an interrupted fetch leaves a .git with no
    # commit, which the next run must finish rather than mistake for a clone.
    [[ -d "$d/.git" ]] || git -c init.defaultBranch=master init -q "$d" || return 1
    git -C "$d" remote get-url origin >/dev/null 2>&1 \
      || git -C "$d" remote add -t master origin "$LLAMA_CPP_URL" || return 1
    git -C "$d" fetch -q --depth 1 origin "$LLAMA_CPP_REF" || return 1
    # On a branch named master that tracks origin's, exactly what the old
    # `clone --depth 1` left: scripts/update.sh --llama pulls into a branch and
    # reads a DETACHED head as "the user pinned this by hand, leave it".
    git -C "$d" checkout -q -B master FETCH_HEAD || return 1
    git -C "$d" config branch.master.remote origin
    git -C "$d" config branch.master.merge refs/heads/master
    [[ "$(git -C "$d" rev-parse HEAD 2>/dev/null)" == "$LLAMA_CPP_REF" ]]
  }
  _ob_llama_head=""
  [[ -d "$REPO_DIR/llama.cpp/.git" ]] \
    && _ob_llama_head="$(git -C "$REPO_DIR/llama.cpp" rev-parse -q --verify 'HEAD^{commit}' 2>/dev/null || true)"
  if [[ -n "$_ob_llama_head" ]]; then
    # a clone with a commit checked out: the normal re-run
    if [[ -z "$LLAMA_CPP_REF" || "$_ob_llama_head" == "$LLAMA_CPP_REF" ]]; then
      :
    else
      warn "llama.cpp/ is at ${_ob_llama_head:0:12}, not the pinned ${LLAMA_CPP_REF:0:12}
      (scripts/llama.cpp.ref) — building what is checked out; an existing
      clone is never moved from here. For the pinned engine instead:
        rm -rf llama.cpp && ./bootstrap.sh"
    fi
  elif [[ -f "$REPO_DIR/llama.cpp/CMakeLists.txt" ]]; then
    ok "llama.cpp SOURCE tree present without git history (a bundle install
      or a tarball) — building it as-is. NOTE: scripts/update.sh --llama wants
      a .git to pull into and will say so; the build path does not care."
  elif ob_offline; then
    die "OFFLINE=true and there is no llama.cpp source to build.
       The source is one of the four fetches a first install cannot do on a
       closed network. Bring it in with a bundle:
         connected box:  ./scripts/bundle.sh build ./bundle
         here:           ./scripts/bundle.sh install ./bundle
       or by hand: git clone --depth 1 https://github.com/ggml-org/llama.cpp.git
       into $REPO_DIR/llama.cpp (a source tarball is enough — this build
       needs the SOURCE, not the history). Then re-run."
  elif [[ -n "$LLAMA_CPP_REF" ]]; then
    ob_fetch_llama_pin || die "could not fetch llama.cpp at the pinned commit
       $LLAMA_CPP_REF (scripts/llama.cpp.ref) from
       $LLAMA_CPP_URL — git's message is above.
       Usually the network: check it and re-run ./bootstrap.sh. If
       upstream no longer has that commit, the pin needs moving:
       ./scripts/update.sh --llama on a box that builds, then commit the file."
    ok "llama.cpp at the pinned commit ${LLAMA_CPP_REF:0:12} (scripts/llama.cpp.ref)"
  else
    warn "NO llama.cpp PIN: scripts/llama.cpp.ref is missing or does not hold a
      40-hex LLAMA_CPP_REF, so this clones whatever upstream master is right
      now — unreviewed code, compiled and run as you. Restore the file
      (git checkout -- scripts/llama.cpp.ref) to build the engine OpenBeast
      was tested with."
    git clone --depth 1 "$LLAMA_CPP_URL" "$REPO_DIR/llama.cpp"
  fi
  # $CMAKE_FLAGS is deliberately unquoted — it's a flag list.
  cmake -S "$REPO_DIR/llama.cpp" -B "$REPO_DIR/llama.cpp/build" \
        $CMAKE_FLAGS -DCMAKE_BUILD_TYPE=Release
  cmake --build "$REPO_DIR/llama.cpp/build" --config Release -j"$(nproc)" --target llama-server
  [[ -x "$LLAMA_BIN" ]] && ok "built $LLAMA_BIN" || die "build did not produce llama-server"
fi

# Persist the resolved backend so scripts/update.sh --llama rebuilds with
# the SAME flavor instead of re-guessing (docs/HARDWARE_PROFILES.md Phase 1).
#
# NEVER `cpu` FROM AUTO-DETECTION. A pinned backend outlives the condition
# that produced it: "no GPU seen today" (a driver that was down) written as
# GPU_BACKEND=cpu kept the box on CPU after the driver was fixed, through
# every later bootstrap and update. The preflight now refuses that install
# outright; this guard is here so the pin cannot come back by another road.
# cpu is persisted only when it was ASKED for (--cpu / GPU_BACKEND=cpu).
CONF_FILE="$REPO_DIR/openbeast.conf"
if [[ "$OB_BACKEND" == "cpu" && "$GPU_BACKEND" != "cpu" ]]; then
  warn "not writing GPU_BACKEND=cpu to openbeast.conf: it came from detection,
      not from you. Detection runs again next time; to make CPU-only stick,
      set GPU_BACKEND=cpu there (or run ./bootstrap.sh --cpu)."
elif [[ ! -f "$CONF_FILE" ]]; then
  # Reached only when conf.sh had nothing to persist (it creates the file
  # itself when it generates SEARXNG_SECRET — which made the old version of
  # this branch dead code on an ordinary run, and its file world-readable on
  # the run where it was not). Mode 600 like conf.sh's: secrets land here.
  ( umask 077
    { echo "# OpenBeast configuration — created by bootstrap.sh."
      echo "# All available keys: openbeast.conf.example"
      echo "GPU_BACKEND=$OB_BACKEND"
    } > "$CONF_FILE" )
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
# SATISFACTION CHECK FIRST, and it costs no network. The old form was an
# unconditional `pip install -q -U "huggingface_hub" -r requirements.txt`, and
# the `-U` on an unpinned name forces a PyPI index query EVERY run — even on a
# box where every pin is already installed. Under `set -euo pipefail` that made
# this line fatal on a closed network, so an operator who pre-seeded ~/.local
# from a USB wheelhouse still could not get past step 3 of the only supported
# installer. It also fired AFTER the llama.cpp build had burned 10-40 minutes.
#
# Upgrades are not lost: scripts/update.sh --python owns them (it already runs
# `pip list --outdated` and reinstalls against requirements.txt), which is the
# right place for an upgrade — bootstrap's job is to make the box WORK.
ob_python_deps_satisfied() {
  python3 - "$REPO_DIR/agents/requirements.txt" <<'PYDEPS'
import importlib.metadata as md, re, sys
missing = []
for raw in open(sys.argv[1]):
    line = raw.split("#")[0].strip()
    if not line:
        continue
    m = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*(.+)$", line)
    if not m:                      # unpinned line: presence is all we can check
        name, want = re.split(r"[<>=!~\[]", line, 1)[0].strip(), None
    else:
        name, want = m.group(1), m.group(2).strip()
    try:
        got = md.version(name)
    except md.PackageNotFoundError:
        missing.append(f"{name} (not installed)")
        continue
    if want and got != want:
        missing.append(f"{name} (have {got}, need {want})")
# huggingface_hub is installed alongside the pins but is NOT pinned in the
# file; presence is the only claim we can make about it.
try:
    md.version("huggingface_hub")
except md.PackageNotFoundError:
    missing.append("huggingface_hub (not installed)")
if missing:
    print("; ".join(missing))
    sys.exit(1)
sys.exit(0)
PYDEPS
}
if _unsat="$(ob_python_deps_satisfied)"; then
  ok "python deps already satisfied — skipping the install (no index query)"
else
  warn "installing python deps: ${_unsat:-unknown}"
  # PREFER THE HASH-PINNED LOCK. requirements.txt pins 6 direct versions
  # and says nothing about the other 37 packages that actually get installed,
  # nor about their CONTENT. agents/requirements.lock pins the whole closure
  # by sha256, so pip refuses substituted bytes.
  #
  # THE UNPINNED FALLBACK IS OPT-IN (OPENBEAST_PIP_STRICT=0). It used to be the
  # default for every failure that was not a hash mismatch, on the reasoning
  # that an installer's job is to make the box work. But the adversary the
  # lock exists for does not have to serve WRONG bytes: a mirror or proxy that
  # simply omits one locked file gets "No matching distribution", and the old
  # answer to that was to install the same names, unverified, from that same
  # index. So a locked install that cannot complete now STOPS, with pip's
  # report and the way out; a python the closure genuinely cannot satisfy
  # says so with OPENBEAST_PIP_STRICT=0 and gets the old, loud fallback.
  _ob_lock="$REPO_DIR/agents/requirements.lock"
  _ob_fallback_ok=0
  [[ "${OPENBEAST_PIP_STRICT:-1}" == "0" ]] && _ob_fallback_ok=1
  _ob_locked_ok=0
  # OFFLINE: a wheelhouse is the only way this can work, so look for one and
  # say exactly how to make one if it is absent. Reaching for the index here
  # is the stall the operator already told us to avoid.
  if ob_offline; then
    for _wh in "$REPO_DIR/wheels" "$REPO_DIR/wheelhouse" "${OPENBEAST_WHEELHOUSE:-}"; do
      [[ -n "$_wh" && -d "$_wh" ]] || continue
      if "$REPO_DIR/scripts/pydeps.sh" install --from "$_wh"; then
        _ob_locked_ok=1
        ok "installed the hash-pinned closure from $_wh (no index contacted)"
        break
      fi
      warn "wheelhouse $_wh did not satisfy the lock — see the audit above"
    done
    [[ $_ob_locked_ok -eq 1 ]] || die "OFFLINE=true and no usable wheelhouse.
       Python packages are the second of the four fetches a closed network
       cannot do. Stage them once on a connected box:
         ./scripts/pydeps.sh wheelhouse wheels
       copy ./wheels here — NOT the lock — then re-run. Every wheel is
       hash-checked against THIS checkout's committed agents/requirements.lock,
       so the stick does not have to be trusted as long as the lock did not
       travel on it. (Not a git checkout? Set OPENBEAST_LOCK_SHA256 to the
       lock hash the wheelhouse command printed on the connected box.)"
  fi
  # IS THE LOCK CURRENT? Checked before it is trusted, offline and in well
  # under a second. requirements.txt moves without the lock in two ordinary
  # ways — a merged Dependabot bump, and scripts/update.sh --python on a box
  # that could not regenerate it — and this step then installed the OLD
  # versions from the lock, printed a green check, and did it again on every
  # later run, because the satisfaction check above (which reads
  # requirements.txt) could never pass. A stale lock is never installed from;
  # whether requirements.txt may be used instead is the same opt-in as below.
  _ob_lock_usable=0
  if [[ $_ob_locked_ok -eq 0 && -f "$_ob_lock" ]]; then
    if _ob_stale="$("$REPO_DIR/scripts/pydeps.sh" verify 2>&1)"; then
      _ob_lock_usable=1
    elif [[ $_ob_fallback_ok -eq 0 ]]; then
      die "agents/requirements.lock does not match agents/requirements.txt, so
       there is nothing hash-pinned to install from:
$(sed 's/^/         /' <<< "$_ob_stale")
       Regenerate it on a connected box:  ./scripts/pydeps.sh lock
       (after a 'git pull', check that both files came from the same commit:
       git status agents/). To install from requirements.txt anyway — versions
       pinned, content NOT verified — re-run with OPENBEAST_PIP_STRICT=0."
    else
      warn "agents/requirements.lock is STALE against agents/requirements.txt —
       NOT installing from it (that would put the OLD versions on this box):
$(sed 's/^/         /' <<< "$_ob_stale")
       OPENBEAST_PIP_STRICT=0, so using agents/requirements.txt, which pins
       VERSIONS but not content.
       Regenerate the lock:  ./scripts/pydeps.sh lock"
    fi
  fi
  if [[ $_ob_lock_usable -eq 1 ]]; then
    # pip's stderr is KEPT, because why it failed decides what happens next.
    _ob_pip_err="$(mktemp)"
    if python3 -m pip install --user $PIP_FLAGS -q --require-hashes -r "$_ob_lock" 2>"$_ob_pip_err"; then
      _ob_locked_ok=1
      rm -f "$_ob_pip_err"
      ok "installed the hash-pinned closure ($(grep -cE '^[A-Za-z0-9].*==' "$_ob_lock") packages, content verified)"
    else
      cat "$_ob_pip_err" >&2
      # A HASH MISMATCH IS NEVER A REASON TO FALL BACK. It is the one event
      # the lock exists to catch — an index or mirror served bytes that are
      # not the pinned ones — and the old code answered it by installing the
      # same names UNPINNED from the same index, under a warning that blamed
      # "this python". pip's words for it (pip/_internal/exceptions.py,
      # HashMismatch): the banner below, then "Expected sha256 … Got …".
      #
      # Deliberately NOT treated as tampering: "all requirements must have
      # their versions pinned" / "Hashes are required in --require-hashes
      # mode". Those mean the closure on THIS python needs a package the lock
      # does not name — the compatibility case the fallback is for — and no
      # bytes were compared at all.
      if grep -qE 'DO NOT MATCH THE HASHES|^[[:space:]]*Expected sha(256|384|512) |hash mismatch' "$_ob_pip_err"; then
        rm -f "$_ob_pip_err"
        die "HASH MISMATCH installing from agents/requirements.lock (pip's report
       is above). The index served bytes that are NOT the ones the lock pins.
       Refusing to continue, and NOT falling back to requirements.txt — that
       would install the same packages, unverified, from the same source.
       If a mirror or proxy is configured (pip config list, PIP_INDEX_URL),
       suspect it first. If you just regenerated or edited the lock, re-run
       ./scripts/pydeps.sh lock and try again."
      fi
      rm -f "$_ob_pip_err"
      if [[ $_ob_fallback_ok -eq 0 ]]; then
        die "the hash-pinned install from agents/requirements.lock failed (pip's
       report is above), and NOT on a hash. Stopping here rather than
       installing the same packages unverified from the same index.
         - network or index trouble (\"No matching distribution\", a timeout):
           check the index (pip config list, PIP_INDEX_URL) and re-run, or
           pre-stage a wheelhouse:
             connected box:  ./scripts/pydeps.sh wheelhouse wheels
             this box:       ./scripts/pydeps.sh install --from wheels
         - this python needs a package the lock does not name: regenerate
           the lock for it (./scripts/pydeps.sh lock)
         - or accept an install with versions pinned but content NOT
           verified:  OPENBEAST_PIP_STRICT=0 ./bootstrap.sh"
      fi
      warn "the hash-pinned install failed on this python, and NOT on a hash
       (pip's report is above) — OPENBEAST_PIP_STRICT=0, so falling back to
       agents/requirements.txt, which pins VERSIONS but not content.
       ./scripts/pydeps.sh lock regenerates the lock for this interpreter."
    fi
  fi
  if [[ $_ob_locked_ok -eq 0 ]]; then
    # Every road to this unpinned install is behind the opt-in. The one not
    # refused above is a checkout with no lock at all.
    [[ $_ob_fallback_ok -eq 1 ]] || die "agents/requirements.lock is missing, so
       there is nothing hash-pinned to install from. Restore it:
         git checkout -- agents/requirements.lock
       or accept versions pinned but content NOT verified:
         OPENBEAST_PIP_STRICT=0 ./bootstrap.sh"
    python3 -m pip install --user $PIP_FLAGS -q -U "huggingface_hub" -r "$REPO_DIR/agents/requirements.txt" \
      || die "pip install failed. On a closed network, pre-stage the wheels:
       on a connected box:  ./scripts/pydeps.sh wheelhouse wheels
       copy ./wheels here (NOT the lock — this checkout's is the trust root), then:
                            ./scripts/pydeps.sh install --from wheels"
    ok "installed huggingface_hub + $(tr '\n' ' ' < "$REPO_DIR/agents/requirements.txt")"
  fi
  # ASK AGAIN. "pip exited 0" and "the pins are installed" are different
  # claims, and only the second one is what this step is for: every green
  # check above was printed by the stale-lock bug too.
  #
  # A WARNING, not a die (fatal only under an EXPLICIT OPENBEAST_PIP_STRICT=1;
  # this is not the fallback question, which is strict by default). pip just
  # succeeded, so what is on the box is at worst a near-miss of the pins — and
  # a checker false-negative (a marker-gated pin, a distro package shadowing
  # --user) would otherwise turn every fresh install into a dead one. A stack
  # that runs on a near-miss beats a bootstrap that refuses to finish.
  if ! _unsat="$(ob_python_deps_satisfied)"; then
    _msg="python deps are STILL not satisfied after installing: ${_unsat:-unknown}
       The install reported success, so something else is putting a different
       version in front of it (a second site-packages? PYTHONPATH?), or the
       lock and agents/requirements.txt disagree: ./scripts/pydeps.sh verify"
    if [[ "${OPENBEAST_PIP_STRICT:-0}" == "1" ]]; then die "$_msg"; fi
    warn "$_msg"
  fi
fi
# hf / mcpo land in ~/.local/bin — make sure it's reachable for this run
export PATH="$HOME/.local/bin:$PATH"
command -v hf >/dev/null 2>&1 || command -v huggingface-cli >/dev/null 2>&1 \
  || warn "hf CLI not on PATH — add 'export PATH=\$HOME/.local/bin:\$PATH' to your shell rc"

# ---- 4. default model weight (skip if present) -----------------------------
step "Default model weight (~20 GB — the one big download)"
# Resolve the weights dir the same way the serve scripts do. On a fresh
# machine no weights dir exists anywhere and weights.sh would hard-exit with
# its "point OpenBeast at your weights" guidance — correct for serve scripts,
# fatal for the bootstrapper whose job is to create it. OPENBEAST_WEIGHTS_MKDIR
# tells the resolver to mkdir the RESOLVED dir instead (custom env/conf paths
# are still preferred as usual).
export OPENBEAST_WEIGHTS_MKDIR=1
source "$REPO_DIR/scripts/lib/weights.sh"
unset OPENBEAST_WEIGHTS_MKDIR
# (WEIGHT_FILE and HF_REPO are set at the top of this file.)
# Supply-chain pin for the one mandatory download, read from the weight
# registry (scripts/weights.registry — pins EVERY shipped GGUF, same
# discipline as the digest-pinned container images). A silent swap in the
# upstream HF repo fails loudly here instead of shipping onto every fresh
# install. Other models: scripts/verify-weights.sh after download.
WEIGHT_SHA256="$(awk -F'\t' -v f="$WEIGHT_FILE" '$3 == f {print $1}' "$REPO_DIR/scripts/weights.registry" 2>/dev/null || true)"
[[ -n "$WEIGHT_SHA256" ]] || die "scripts/weights.registry has no entry for $WEIGHT_FILE — restore it from git"
# PRESENT IS NOT VERIFIED. This used to print "already downloaded" for any
# file with the right name, and the download branch used to leave a weight
# that FAILED its pin under that very name (die, no rm). So re-running
# bootstrap after "checksum MISMATCH" — the ordinary reaction to a failed
# installer — turned the pin's rejection into acceptance, and start.sh (a
# size check only) then served the substituted file. The existing file is
# hashed on every run now (~1 min for 20 GB, inside a multi-minute
# bootstrap). A mismatch is fatal and NOT auto-deleted here: the file may be
# one the operator put there by hand.
if [[ -f "$WEIGHTS_DIR/$WEIGHT_FILE" ]]; then
  echo "  verifying the existing $WEIGHT_FILE against its pin (~1 min for 20 GB)..."
  if _vw="$("$REPO_DIR/scripts/verify-weights.sh" --file "$WEIGHT_FILE" 2>&1)"; then
    ok "already downloaded, sha256 verified ($WEIGHTS_DIR/$WEIGHT_FILE)"
  else
    die "$WEIGHTS_DIR/$WEIGHT_FILE is NOT the weight OpenBeast pinned:
$(sed 's/^/         /' <<< "$_vw")
       Refusing to continue with it. Delete it and re-run this script (the
       download is verified BEFORE it is given that name), or check the HF
       repo ($HF_REPO) and OpenBeast issues for a vetted update."
  fi
else
  ob_offline && die "OFFLINE=true and $WEIGHT_FILE is not in $WEIGHTS_DIR.
       The ~20 GB weight is the third of the four fetches a closed network
       cannot do, and the stack has nothing to serve without a weight. Copy
       one in and verify it:
         ./scripts/fetch-weight.sh --list      (on a connected box)
         copy the .gguf into $WEIGHTS_DIR
         ./scripts/verify-weights.sh           (checks size + sha256)
       Any registry weight works, not just this default — set SERVE_SCRIPT in
       openbeast.conf to match what you brought."
  warn "downloading the default 27B model — this is the long step, grab coffee."
  # fetch-weight.sh, not a second copy of it: it downloads into a staging dir
  # INSIDE the weights dir, verifies size + sha256 there, and DELETES a
  # mismatch — so a file that failed its pin never exists under the name a
  # serve script (or the check above) looks for. An interrupted download
  # keeps its partial, and the next run resumes it.
  "$REPO_DIR/scripts/fetch-weight.sh" "$WEIGHT_FILE" \
    || die "the default weight was not installed (fetch-weight.sh says why,
       above). Nothing that failed verification was left under $WEIGHT_FILE.
       Re-run this script to retry (an interrupted download resumes). If the
       pin failed, the upstream file changed since OpenBeast pinned it: check
       the HF repo ($HF_REPO) and OpenBeast issues for a vetted update."
  [[ -f "$WEIGHTS_DIR/$WEIGHT_FILE" ]] || die "download failed"
  ok "downloaded to $WEIGHTS_DIR (sha256 verified)"
fi

# ---- executable bits -------------------------------------------------------
chmod +x "$REPO_DIR"/*.sh "$REPO_DIR"/scripts/*.sh "$REPO_DIR"/tests/*.sh 2>/dev/null || true

# Where the 20 GB went, for the closing banner. The default (../weights, a
# SIBLING of the checkout) is not where anyone looks first, and the banner
# said nothing about it.
ob_weights_line() {
  local gb
  gb="$(stat -c%s "$WEIGHTS_DIR/$WEIGHT_FILE" 2>/dev/null | awk '{printf "%.1f GB", $1/1e9}' || true)"
  echo "  Weights:   $WEIGHTS_DIR/$WEIGHT_FILE${gb:+ ($gb)}"
  echo "             (the directory is WEIGHTS_DIR in openbeast.conf)"
}

# ---- Tier 0 (minimal) exit -------------------------------------------------
if [[ $MINIMAL -eq 1 ]]; then
  step "${c_grn}Tier 0 ready${c_rst}"
  echo "  Start just the model server (no Docker, no auth, no tools):"
  echo "      ${c_bold}./scripts/serve-qwen38-27b-uncensored-mtp-q5.sh${c_rst}"
  echo "  Then talk to it:"
  echo "      curl http://localhost:8080/v1/models"
  echo "      point any OpenAI-compatible client at http://localhost:8080/v1"
  ob_weights_line
  exit 0
fi

# ---- 5. Docker images for the frontends ------------------------------------
step "Frontend images (Open WebUI + SearXNG)"
# Pull the EXACT digest-pinned refs from docker-compose.yml — pulling the
# moving :main/:latest tags could report "image ready" for a different image
# than the one compose actually runs.
# OFFLINE: a registry pull is the FOURTH of the four fetches a closed network
# cannot do, and this loop had no guard — so bootstrap stalled on two pulls
# and then warned, in the one script that had just refused the other three by
# name. The claim "all four are refused up front" was false here, which is
# how a review found it.
if ob_offline; then
  # NOT `grep -c … || echo 0`: on zero matches grep prints "0" AND exits 1, so
  # that form yields "0\n0".
  _n_img=$(grep -cE '^\s+image:' "$REPO_DIR/docker-compose.yml" || true); _n_img=${_n_img:-0}
  warn "OFFLINE=true → not pulling the $_n_img frontend image(s); a registry
      pull cannot succeed here. Images already in the local store are used as
      they are. To bring them in from a connected box:
        connected:  ./scripts/bundle.sh build ./bundle
        here:       ./scripts/bundle.sh install ./bundle
      That loads them AND rewrites docker-compose.yml to reference them by
      content id, because a digest-pinned ref cannot be satisfied from a
      tarball. Without images the model API (:8080) and the tool server still
      work; Open WebUI and SearXNG do not."
else
while IFS= read -r image_ref; do
  short="${image_ref##*/}"; short="${short%%@*}"
  docker pull -q "$image_ref" >/dev/null && ok "$short image ready" \
    || warn "$short image pull FAILED (network/registry?) — ./start.sh will retry the pull"
done < <(grep -E '^\s+image:' "$REPO_DIR/docker-compose.yml" | awk '{print $2}')
fi

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
ob_weights_line
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
