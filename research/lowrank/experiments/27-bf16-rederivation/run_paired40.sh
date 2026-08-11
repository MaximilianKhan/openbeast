#!/usr/bin/env bash
# E27 / T1.16: paired per-chunk stats at n=40 (PROTOCOL §4) on the
# kld40-*.log rows. Pairs pre-registered in JOURNAL 2026-08-04 18:08.
set -euo pipefail
cd "$(dirname "$0")"
PS=../24-yaqa-lite/paired_stats.py
out=results-40ch-paired.txt
: > "$out"
pair() { # A B
  echo "== $1 vs $2 ==" | tee -a "$out"
  python3 "$PS" kld "kld40-$1.log" "kld40-$2.log" | tee -a "$out"
  echo | tee -a "$out"
}
pair MIXEDfc  CONTROL
pair MIXEDfc  Q3_K_S
pair CONTROL  Q3_K_S
pair Q3_K_S   Q3_K_M
# E13-27B legacy pair (Q6-derived provenance, cross-provenance caveat)
pair Q2Krr-legacy Q2Kbare-legacy
