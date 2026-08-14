#!/usr/bin/env bash
# T1.3+T1.5: paired per-chunk stats at n=100 on kld100-*.log rows.
# Pairs pre-registered JOURNAL 2026-08-11 15:25.
set -euo pipefail
cd "$(dirname "$0")"
PS=../24-yaqa-lite/paired_stats.py
out=results-100ch-paired.txt
: > "$out"
pair() {
  echo "== $1 vs $2 ==" | tee -a "$out"
  python3 "$PS" kld "kld100-$1.log" "kld100-$2.log" | tee -a "$out"
  echo | tee -a "$out"
}
pair MIXEDfc  CONTROL
pair MIXEDfc  Q3_K_S
pair CONTROL  Q3_K_S
pair MIXEDfc  MIXEDbare
pair Q3_K_S   Q3_K_M
pair Q2Krr-legacy Q2Kbare-legacy
