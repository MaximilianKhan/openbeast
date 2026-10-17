#!/usr/bin/env bash
# Read-only: for each OURS row of F still to come, wait for its server to be
# healthy and record GET /lora-adapters (proof the adapter is live) to logs-f/.
E36=/home/max/Documents/openbeast/research/lowrank/experiments/36-submission
for r in 2 3; do
  a=e36f-OURS-IQ3S-r$r; out=$E36/logs-f/lora-$a.json
  [ -s "$out" ] && continue
  until [ -f "$E36/logs-f/serve-$a.log" ] && curl -sf http://localhost:8080/health >/dev/null 2>&1 \
        && grep -q "$a" <(curl -sf http://localhost:8080/v1/models 2>/dev/null); do sleep 30; done
  curl -sf http://localhost:8080/lora-adapters > "$out"; echo "[$(date +%F' '%T)] $a: $(cat "$out")"
done
