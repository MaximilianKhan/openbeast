#!/bin/bash
# beast-hydra simulator: three fake engines (llama rig 1 slot, vLLM "sparks"
# 8 slots, llama "ti" 2 slots) plus a real agents/hydra.py, all on ephemeral
# 127.0.0.1 ports with a generated hydra.toml and 0600 keys in a temp dir.
#
#   scripts/hydra-sim.sh                  # run the five plan scenarios (exit 0 = all pass)
#   scripts/hydra-sim.sh --hold           # keep it up; prints the env for hydra.sh / a client
#   scripts/hydra-sim.sh --hold --env-file F   # also write that env to F once ready
#
# Two processes only (the fleet+driver and hydra), no GPU, no docker, never
# port 8080 — safe beside a running campaign. Everything it starts, it stops
# by pid on exit (Ctrl-C / SIGTERM). Details: tests/fakes/hydra_sim.py.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${PYTHON:-python3}" "$REPO_DIR/tests/fakes/hydra_sim.py" "$@"
