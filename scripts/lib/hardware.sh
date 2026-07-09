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
        echo "  ${OB_VRAM_MB} MiB VRAM (3090/4090-class). serve.sh AUTO-SCALES"
        echo "  the shipped 27B context down to your card's KV budget (Phase 2)"
        echo "  — no OOM, no hand-tuning. The 27B Q5 (~21 GB) leaves little KV"
        echo "  room here, so a Q4 quant will give you far more context. Watch"
        echo "  'nvidia-smi'; override with OPENBEAST_CONTEXT=<n>."
      elif [[ $OB_VRAM_MB -ge 15000 ]]; then
        echo "  ${OB_VRAM_MB} MiB VRAM (16 GB-class) — the 27B Q5 default"
        echo "  (~21 GB weights) does not fit. Use a Q4/Q3 quant of a ~14B"
        echo "  model; serve.sh will auto-scale its context to fit (Phase 2)."
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

# The opinionated VRAM floor: 11 GB — the 1080 Ti / 2080 Ti class. OpenBeast
# exists to run the LARGEST models your hardware holds ("max intelligence,
# no compromise"); below 11 GB every shipped model needs quants/contexts so
# degraded the result isn't the product we test or stand behind. Cards at or
# above the floor are cheap and plentiful secondhand. Returns 1 (and prints
# the verdict) when a detected GPU with KNOWN VRAM is under the floor;
# unknown VRAM (0) and CPU-only setups pass through to their own warnings.
# Escape hatch for people who accept an unsupported setup:
# OPENBEAST_FORCE_VRAM=1.
OB_VRAM_FLOOR_MB=11000

# Scale a reference-card context down to a smaller card's KV budget.
# Args: <ref_context> <card_vram_mib> <weights_mib>. Echoes the context to
# use — == ref_context on reference-class (or unknown-VRAM) cards, so the
# measured values stand there. Pure integer math, no I/O, so it's unit-
# testable. Return codes: 0 = ok, 2 = weights don't even fit (echoes the 8192
# floor so the caller can warn and still attempt a launch).
OB_REF_VRAM_MIB=32607   # the card the shipped -c values were tuned on
ob_scale_context() {
  local ref_ctx="$1" vram="$2" weights="$3"
  local headroom=2048 desktop=1500
  if ! [[ "$vram" =~ ^[0-9]+$ ]] || [[ "$vram" -le 0 || "$vram" -ge $((OB_REF_VRAM_MIB - 1000)) ]]; then
    echo "$ref_ctx"; return 0            # reference-class or unknown: unchanged
  fi
  local kv_ref=$(( OB_REF_VRAM_MIB - headroom - desktop - weights ))
  local kv_here=$(( vram - headroom - desktop - weights ))
  if [[ $kv_ref -le 0 || $kv_here -le 0 ]]; then
    echo 8192; return 2                  # weights don't fit the card
  fi
  local n=$(( ref_ctx * kv_here / kv_ref ))
  n=$(( (n / 4096) * 4096 ))             # floor to a 4K multiple
  [[ $n -lt 8192 ]] && n=8192
  [[ $n -gt $ref_ctx ]] && n="$ref_ctx"
  echo "$n"
}

ob_vram_floor_check() {
  [[ "${OPENBEAST_FORCE_VRAM:-0}" == "1" ]] && return 0
  [[ "$OB_GPU_VENDOR" == "none" ]] && return 0
  [[ "${OB_VRAM_MB:-0}" -eq 0 ]] && return 0   # unknown VRAM: warn elsewhere
  if [[ "$OB_VRAM_MB" -lt "$OB_VRAM_FLOOR_MB" ]]; then
    echo "  ${OB_GPU_NAME:-GPU} has ${OB_VRAM_MB} MiB VRAM — below OpenBeast's"
    echo "  11 GB floor (1080 Ti / 2080 Ti class). This is an opinionated"
    echo "  distribution: we ship and test the largest models that earn their"
    echo "  VRAM, not survival configs for small cards. It IS possible to run"
    echo "  llama.cpp on less — that path just isn't OpenBeast, and we won't"
    echo "  pretend to support it. 11 GB+ cards are cheap and plentiful used."
    echo "  To proceed anyway, unsupported: OPENBEAST_FORCE_VRAM=1 ./bootstrap.sh"
    return 1
  fi
  return 0
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
      # CUDA toolkit (nvcc), off PATH by default on most distros:
      # Arch /opt/cuda, NVIDIA-installer /usr/local/cuda, Ubuntu/Debian
      # nvidia-cuda-toolkit /usr/lib/cuda, some vendor repos /opt/nvidia/cuda.
      if ! command -v nvcc >/dev/null 2>&1; then
        for d in /opt/cuda/bin /usr/local/cuda/bin /usr/lib/cuda/bin /opt/nvidia/cuda/bin; do
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
