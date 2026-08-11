#!/usr/bin/env bash
# T1.3 + T1.5 battery (pre-registered JOURNAL 2026-08-11): eight configs
# vs the 100-chunk BF16 truth. Sequential (GPU-serialized). Resume-safe:
# a config with an existing complete log ([100] row present) is skipped.
set -uo pipefail
cd "$(dirname "$0")"
run() { # label model [extra args]
  local label="$1"; shift
  if [ -f "kld100-$label.log" ] && grep -q "\[100\]" "kld100-$label.log"; then
    echo "SKIP $label (complete log exists)"; return
  fi
  echo "=== $label $(date +%H:%M:%S)"
  ./measure100.sh "$label" "$@" || echo "FAILED $label"
}
run MIXEDfc   h27bf16-MIXED.gguf --lora mixed-fc-r128q8-bf16.gguf
run MIXEDbare h27bf16-MIXED.gguf
run Q3_K_S    h27bf16-Q3_K_S.gguf
run IQ3_XS    h27bf16-IQ3_XS.gguf
run CONTROL   h27bf16-CONTROL.gguf
run Q3_K_M    h27bf16-Q3_K_M.gguf
run Q2Kbare-legacy ../04b-27b/heretic27b-Q2_K.gguf
run Q2Krr-legacy   ../13-rerounder/heretic27b-Q2K-rr.gguf
echo "BATTERY DONE $(date +%H:%M:%S)"
