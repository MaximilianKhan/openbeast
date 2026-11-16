#!/usr/bin/env bash
# SLOW: right commit and region, but the numeric branch never terminates.
# The validator must fail it by its own per-run limit, not the harness's.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
bad=$(git log --format=%H -1 --grep='^perf: avoid command substitution')
python3 "$H/_fix.py" hang
git commit -q -am "Fix numeric prerelease precedence broken by $bad"
