#!/bin/bash
# beast-recipe: build, plan and apply serving recipes for the DGX Spark pair
# (and 1-8 Spark clusters). The top-level entry point; the work is in
# scripts/backends/beast-recipe.sh -> scripts/backends/pylib/recipe.py.
# docs/BEAST_RECIPE.md
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/backends/beast-recipe.sh" "$@"
