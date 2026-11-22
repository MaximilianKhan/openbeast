#!/bin/bash
# beast-recipe: build, plan and apply serving recipes for the DGX Spark pair
# (and 1-8 Spark clusters). The top-level entry point; the work is in
# scripts/backends/beast-recipe.sh -> scripts/backends/pylib/recipe.py.
# docs/BEAST_RECIPE.md
# `beast-spark install` links it as ~/.local/bin/beast-recipe: follow the link to the checkout.
set -euo pipefail
src="${BASH_SOURCE[0]}"
while [[ -L "$src" ]]; do
  dir="$(cd "$(dirname "$src")" && pwd)"; src="$(readlink "$src")"; [[ "$src" == /* ]] || src="$dir/$src"
done
exec "$(cd "$(dirname "$src")" && pwd)/backends/beast-recipe.sh" "$@"
