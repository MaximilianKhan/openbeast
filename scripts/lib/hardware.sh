#!/bin/bash
# OpenBeast — GPU detection + hardware profile recommendation.
#
# Source this, then call ob_detect_gpu (sets the OB_GPU_* vars) and
# ob_profile_advice (prints a recommendation block for the detected card).
#
#   OB_GPU_VENDOR    nvidia | amd | intel | none
#   OB_GPU_NAME      marketing name of GPU 0 (best effort)
#   OB_GPU_COUNT     number of GPUs seen
#   OB_VRAM_MB       VRAM of the largest single GPU, in MiB (0 if unknown)
#   OB_VRAM_TOTAL_MB sum across GPUs, in MiB
#
# Build backend selection (Phase 1, docs/HARDWARE_PROFILES.md): after
# ob_detect_gpu, call ob_resolve_backend to map GPU_BACKEND (lib/conf.sh)
# plus the detected vendor to a concrete llama.cpp build flavor:
#
#   OB_BACKEND       cuda | hip | sycl | cpu
#
# ob_backend_preflight checks the backend's toolchain is installed, and
# ob_cmake_flags echoes the cmake flags for it — bootstrap.sh and
# scripts/update.sh both build through these so they can never drift.
#
# Every shipped serve script is measured and tuned on ONE RTX 5090 (32 GB) —
# that stays the reference profile and the default assumption. Everything
# below 30 GB gets *advisory* recommendations (clearly labeled unmeasured);
# nothing here changes launch behavior. The full per-tier config system is
# planned in docs/HARDWARE_PROFILES.md.
#
# Overrides:
#   OPENBEAST_ASSUME_5090=1   skip the advice block entirely (CI, headless)

ob_detect_gpu() {
  OB_GPU_VENDOR="none"; OB_GPU_NAME=""; OB_GPU_COUNT=0
  OB_VRAM_MB=0; OB_VRAM_TOTAL_MB=0

  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    OB_GPU_VENDOR="nvidia"
    OB_GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)
    local mems
    mems=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null || true)
    local m
    while read -r m; do
      [[ "$m" =~ ^[0-9]+$ ]] || continue
      OB_GPU_COUNT=$((OB_GPU_COUNT + 1))
      OB_VRAM_TOTAL_MB=$((OB_VRAM_TOTAL_MB + m))
      [[ $m -gt $OB_VRAM_MB ]] && OB_VRAM_MB=$m
    done <<< "$mems"
    return 0
  fi

  # AMD: amdgpu exposes VRAM in sysfs (bytes); rocm-smi optional.
  local card total_b
  for card in /sys/class/drm/card[0-9]*/device/mem_info_vram_total; do
    [[ -r "$card" ]] || continue
    total_b=$(cat "$card" 2>/dev/null || echo 0)
    [[ "$total_b" =~ ^[0-9]+$ && "$total_b" -gt 0 ]] || continue
    OB_GPU_VENDOR="amd"
    OB_GPU_COUNT=$((OB_GPU_COUNT + 1))
    local mb=$((total_b / 1024 / 1024))
    OB_VRAM_TOTAL_MB=$((OB_VRAM_TOTAL_MB + mb))
    [[ $mb -gt $OB_VRAM_MB ]] && OB_VRAM_MB=$mb
  done
  if [[ "$OB_GPU_VENDOR" == "amd" ]]; then
    OB_GPU_NAME=$(command -v rocm-smi >/dev/null 2>&1 \
      && rocm-smi --showproductname 2>/dev/null | grep -oE 'Card series:.*' | head -1 | sed 's/Card series:[[:space:]]*//' || true)
    return 0
  fi

  # Intel dGPU (Arc): i915/xe driver, no simple VRAM sysfs — flag vendor only.
  if command -v lspci >/dev/null 2>&1 \
     && lspci 2>/dev/null | grep -qiE 'VGA.*Intel.*(Arc|Battlemage|Alchemist)'; then
    OB_GPU_VENDOR="intel"
    OB_GPU_NAME=$(lspci 2>/dev/null | grep -iE 'VGA.*Intel' | head -1 | sed 's/.*: //')
    OB_GPU_COUNT=1
  fi
}

ob_profile_advice() {
  [[ "${OPENBEAST_ASSUME_5090:-0}" == "1" ]] && return 0

  case "$OB_GPU_VENDOR" in
    nvidia)
      if [[ $OB_GPU_COUNT -gt 1 ]]; then
        echo "  Multi-GPU detected (${OB_GPU_COUNT}x, ${OB_VRAM_TOTAL_MB} MiB total)."
        echo "  llama.cpp can split across cards (--tensor-split), but every"
        echo "  shipped config is single-5090-tuned. Multi-GPU profiles are"
        echo "  planned (docs/HARDWARE_PROFILES.md) — expect to hand-tune -c."
      elif [[ $OB_VRAM_MB -ge 30000 ]]; then
        echo "  ${OB_VRAM_MB} MiB VRAM — 5090-class (reference profile)."
        echo "  All shipped serve scripts apply as-is; contexts are measured."
      elif [[ $OB_VRAM_MB -ge 22000 ]]; then
        echo "  ${OB_VRAM_MB} MiB VRAM (3090/4090-class) — the shipped 27B"
        echo "  contexts are tuned for 32 GB and WILL OOM here. Unmeasured"
        echo "  starting point: the default model with '-c 131072' (128K),"
        echo "  or a Q4 quant. Watch 'nvidia-smi' and keep ~2 GB headroom"
        echo "  (VRAM tables: docs/REFERENCE.md)."
      elif [[ $OB_VRAM_MB -ge 15000 ]]; then
        echo "  ${OB_VRAM_MB} MiB VRAM (16 GB-class) — the 27B Q5 default"
        echo "  (~21 GB weights) does not fit. Use a Q4/Q3 quant of a ~14B"
        echo "  model or partial offload (-ngl). No measured profile yet."
      else
        echo "  ${OB_VRAM_MB} MiB VRAM — below the supported floor for the"
        echo "  shipped models. Small quants + short contexts only."
      fi
      ;;
    amd)
      echo "  AMD GPU detected (${OB_GPU_NAME:-unknown}, ${OB_VRAM_MB} MiB)."
      echo "  bootstrap builds llama.cpp with ROCm/HIP (-DGGML_HIP=ON) for it,"
      echo "  but this path is UNTESTED by OpenBeast — the reference profile"
      echo "  is CUDA on a 5090. See docs/HARDWARE_PROFILES.md."
      ;;
    intel)
      echo "  Intel Arc GPU detected (${OB_GPU_NAME:-unknown})."
      echo "  bootstrap builds llama.cpp with SYCL (-DGGML_SYCL=ON, oneAPI),"
      echo "  UNTESTED by OpenBeast — see docs/HARDWARE_PROFILES.md."
      ;;
    none)
      echo "  No supported GPU detected — CPU-only llama.cpp works but is"
      echo "  10-50x slower; the 27B default is impractical without a GPU."
      ;;
  esac
}

# Map GPU_BACKEND (lib/conf.sh; auto | cuda | hip | sycl | cpu) plus the
# detected vendor to a concrete build backend in OB_BACKEND. A non-auto
# GPU_BACKEND always wins over detection. Call ob_detect_gpu first.
ob_resolve_backend() {
  local requested="${GPU_BACKEND:-auto}"
  if [[ "$requested" == "auto" ]]; then
    case "${OB_GPU_VENDOR:-none}" in
      nvidia) OB_BACKEND="cuda" ;;
      amd)    OB_BACKEND="hip"  ;;
      intel)  OB_BACKEND="sycl" ;;
      *)      OB_BACKEND="cpu"  ;;
    esac
  else
    OB_BACKEND="$requested"
  fi
}

# Check the toolchain for OB_BACKEND is installed. Prints what's missing
# (with install hints) and returns 1 if anything is absent; silent and 0
# when the backend is ready to build.
ob_backend_preflight() {
  local missing=0 t d
  case "${OB_BACKEND:-cpu}" in
    cuda)
      if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/dev/null 2>&1; then
        echo "  nvidia-smi not working — the CUDA backend needs the NVIDIA driver."
        echo "      → install the proprietary NVIDIA driver for your distro"
        missing=1
      fi
      # CUDA toolkit (nvcc). Arch keeps it in /opt/cuda/bin, off PATH by default.
      if ! command -v nvcc >/dev/null 2>&1; then
        for d in /opt/cuda/bin /usr/local/cuda/bin; do
          [[ -x "$d/nvcc" ]] && export PATH="$d:$PATH"
        done
      fi
      if ! command -v nvcc >/dev/null 2>&1; then
        echo "  nvcc (CUDA toolkit) missing — needed to build llama.cpp with GPU support."
        echo "      → install 'cuda' with your package manager (Arch: adds /opt/cuda/bin)"
        missing=1
      fi
      ;;
    hip)
      for t in hipcc rocminfo; do
        command -v "$t" >/dev/null 2>&1 && continue
        echo "  $t missing — the HIP backend needs ROCm >= 6."
        echo "      → Arch: 'sudo pacman -S rocm-hip-sdk'; Debian/Ubuntu/Fedora: see"
        echo "        https://rocm.docs.amd.com for the per-distro install"
        missing=1
      done
      ;;
    sycl)
      if ! command -v icpx >/dev/null 2>&1; then
        echo "  icpx missing — the SYCL backend needs the Intel oneAPI DPC++ compiler."
        echo "      → install intel-oneapi-basekit, then 'source /opt/intel/oneapi/setvars.sh'"
        echo "        in this shell and re-run"
        missing=1
      fi
      ;;
    cpu)
      : # no toolchain beyond the base compiler (checked separately)
      ;;
    *)
      echo "  unknown GPU_BACKEND '${OB_BACKEND}' — valid: auto | cuda | hip | sycl | cpu"
      missing=1
      ;;
  esac
  return "$missing"
}

# Echo the llama.cpp cmake flags for OB_BACKEND (empty for cpu; status 1 for
# an unknown backend). The cuda branch is the reference profile and must
# stay behavior-identical to the original bootstrap flags: -DGGML_CUDA=ON
# -DCMAKE_CUDA_ARCHITECTURES=<detected, fallback 120>.
ob_cmake_flags() {
  case "${OB_BACKEND:-cpu}" in
    cuda)
      # || true: under pipefail a failing nvidia-smi would kill the caller
      # inside the substitution, never reaching the :-120 fallback.
      local cuda_arch
      cuda_arch=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d '.' || true)
      cuda_arch="${cuda_arch:-120}"
      echo "-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=$cuda_arch"
      ;;
    hip)
      # Auto-detect the gfx target so ROCm doesn't build for every arch
      # (gfx000 is the CPU agent — skip it). No target found → let llama.cpp
      # use its defaults.
      local gfx
      gfx=$(rocminfo 2>/dev/null | grep -oE 'gfx[0-9a-f]+' | grep -v '^gfx000$' | head -1 || true)
      if [[ -n "$gfx" ]]; then
        echo "-DGGML_HIP=ON -DAMDGPU_TARGETS=$gfx"
      else
        echo "-DGGML_HIP=ON"
      fi
      ;;
    sycl)
      echo "-DGGML_SYCL=ON -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx"
      ;;
    cpu)
      echo ""
      ;;
    *)
      return 1
      ;;
  esac
}
