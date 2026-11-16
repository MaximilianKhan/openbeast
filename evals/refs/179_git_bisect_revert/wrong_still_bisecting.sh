#!/usr/bin/env bash
# WRONG (state): correct culprit and fix, but commits without `git bisect
# reset`: the commit lands on a detached HEAD and main never moves.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
git bisect start main v0.3.0 >/dev/null
git bisect run sh -c 'first=$(printf "1.0.0-rc.10\n1.0.0-rc.9\n" | bash semver.sh sort | head -n 1); test "$first" = 1.0.0-rc.9' >/dev/null
bad=$(git rev-parse refs/bisect/bad)
git checkout -q "$(git rev-parse main)"
python3 "$H/_fix.py" numcmp
git commit -q -am "Fix numeric prerelease precedence broken by $bad"
