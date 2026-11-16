#!/usr/bin/env bash
# WRONG (history): correct fix, but folded into the tip with --amend.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
bad=$(git log --format=%H -1 --grep='^perf: avoid command substitution')
python3 "$H/_fix.py" numcmp
git commit -q -a --amend -m "Release 0.9.0 (fixes the regression from $bad)"
