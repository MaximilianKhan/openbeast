#!/usr/bin/env bash
# E27: paired per-chunk stats (PROTOCOL §4) for every comparison that
# matters. Reuses E24's paired_stats.py on the kld-*.log per-chunk rows.
set -euo pipefail
cd "$(dirname "$0")"
PS=../24-yaqa-lite/paired_stats.py
out=results-paired.txt
: > "$out"
pair() { # A B
  echo "== $1 vs $2 ==" | tee -a "$out"
  python3 "$PS" kld "kld-$1.log" "kld-$2.log" | tee -a "$out"
  echo | tee -a "$out"
}
# bare ladder, adjacent rungs by bytes
pair Q2_K     IQ3_XXS
pair IQ3_XXS  IQ3_XS
pair IQ3_XS   Q3_K_S
pair Q3_K_S   Q3_K_M
pair Q3_K_M   Q4_K_M
# flagship re-derivation
pair MIXEDfc  Q3_K_S
pair MIXEDfc  CONTROL
pair MIXEDfc  MIXEDbare
pair MIXEDbare Q3_K_S
pair CONTROL  Q3_K_S
pair MIXEDfc  IQ3_XS
