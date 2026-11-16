#!/usr/bin/env bash
# WRONG (semantics): compares numeric identifiers with 64-bit shell
# arithmetic, which overflows past 19 digits.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
bad=$(git log --format=%H -1 --grep='^perf: avoid command substitution')
python3 "$H/_fix.py" arith
git commit -q -am "Fix numeric prerelease precedence broken by $bad"
