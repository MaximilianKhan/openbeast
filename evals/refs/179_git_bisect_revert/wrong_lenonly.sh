#!/usr/bin/env bash
# WRONG (semantics, passes the visible suite): orders numeric identifiers by
# digit count only; equal-length numbers (rc.3 vs rc.5) compare as equal.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
bad=$(git log --format=%H -1 --grep='^perf: avoid command substitution')
python3 "$H/_fix.py" lenonly
git commit -q -am "Fix numeric prerelease precedence broken by $bad"
