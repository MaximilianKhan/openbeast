#!/usr/bin/env bash
# memguard for the Hy4-flash vLLM rank on one DGX Spark (the DeepSeek
# recipe's levels and rule, reimplemented: its scripts/memguard.sh). A Spark's
# GPU shares its 121 GiB with the host: a rank that takes all of it does not
# get a clean CUDA out-of-memory error, the machine stops answering. This
# kills the container (docker kill, no grace) before that:
#   - MemAvailable under HARD GiB in two samples in a row (0.25 s apart): at once
#   - MemAvailable under FLOOR GiB for HOLD seconds without once recovering
# It exits by itself when the container is no longer running. Self-contained:
# start.sh copies it to the worker's ~/.cache/hy4-vllm/.
#
#   memguard.sh <container> <floor GiB> <hold s> <hard GiB> <log file>
# Test seams: MEMGUARD_MEMINFO, MEMGUARD_DOCKER, MEMGUARD_TICK (seconds), MEMGUARD_MAX_TICKS.
set -u
c=${1:?container} floor=${2:?floor GiB} hold=${3:?hold s} hard=${4:?hard GiB} log=${5:?log file}
num='^[0-9]+(\.[0-9]+)?$'
[[ "$floor" =~ $num && "$hold" =~ $num && "$hard" =~ $num ]] || { echo "memguard: floor, hold and hard floor are numbers" >&2; exit 2; }
docker=${MEMGUARD_DOCKER:-docker} tick=${MEMGUARD_TICK:-0.25} max=${MEMGUARD_MAX_TICKS:-0}
mkdir -p "$(dirname "$log")"
to20() { if command -v timeout >/dev/null 2>&1; then timeout 20 "$@"; else "$@"; fi; }
stamp() { date '+%Y-%m-%d %H:%M:%S'; }
avail_kb() { awk '$1 == "MemAvailable:" { print $2; exit }' "${MEMGUARD_MEMINFO:-/proc/meminfo}"; }
floor_kb=$(awk -v g="$floor" 'BEGIN { printf "%d", g * 1048576 }')
hard_kb=$(awk -v g="$hard" 'BEGIN { printf "%d", g * 1048576 }')
hold_ticks=$(awk -v h="$hold" -v t="$tick" 'BEGIN { n = h / t; printf "%d", (n < 1 ? 1 : n) }')
echo "$(stamp) memguard: watching $c, floor $floor GiB for $hold s, hard floor $hard GiB" >> "$log"
under=0 hard_low=0 n=0
while sleep "$tick"; do
  n=$((n + 1))
  (( max > 0 && n > max )) && break
  # every ~5 s: is the container still there? (start.sh starts the guard after docker run)
  if (( n % 20 == 1 )); then
    [[ "$("$docker" inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" == true ]] || { echo "$(stamp) memguard: $c is not running: exiting" >> "$log"; exit 0; }
  fi
  a=$(avail_kb); [[ "$a" =~ ^[0-9]+$ ]] || continue
  if (( a >= floor_kb )); then
    (( under )) && echo "$(stamp) memguard: under the $floor GiB floor for $under tick(s), recovered: no kill" >> "$log"
    under=0 hard_low=0; continue
  fi
  under=$((under + 1))
  if (( a < hard_kb )); then hard_low=$((hard_low + 1)); else hard_low=0; fi
  (( hard_low >= 2 || under >= hold_ticks )) || continue
  # kill first, log afterwards (on a thrashing machine every command costs time)
  if to20 "$docker" kill "$c" >/dev/null 2>&1; then
    echo "$(stamp) memguard: MemAvailable $((a / 1024)) MiB, under the $floor GiB floor for $hold s or the $hard GiB hard floor: killed $c" >> "$log"
    exit 0
  fi
  under=0 hard_low=0
done
exit 0
