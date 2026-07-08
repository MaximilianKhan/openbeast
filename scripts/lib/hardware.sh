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
      echo "  bootstrap builds llama.cpp with CUDA only; AMD needs a ROCm/HIP"
      echo "  build (-DGGML_HIP=ON) — see docs/HARDWARE_PROFILES.md. The"
      echo "  serve scripts work unchanged once llama-server is HIP-built."
      ;;
    intel)
      echo "  Intel Arc GPU detected (${OB_GPU_NAME:-unknown})."
      echo "  llama.cpp supports it via SYCL (-DGGML_SYCL=ON); untested by"
      echo "  OpenBeast — see docs/HARDWARE_PROFILES.md."
      ;;
    none)
      echo "  No supported GPU detected — CPU-only llama.cpp works but is"
      echo "  10-50x slower; the 27B default is impractical without a GPU."
      ;;
  esac
}
