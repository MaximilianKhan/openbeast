#!/usr/bin/env bash
# WRONG (localisation): `git bisect run bash tests/run.sh`. The suite passes
# vacuously while tests/lib.sh is missing (commits 17-22), so bisect blames
# commit 23 "test: fix helper path". The code fix itself is correct.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
git bisect start main v0.3.0 >/dev/null
git bisect run bash tests/run.sh >/dev/null 2>&1
bad=$(git rev-parse refs/bisect/bad)
git bisect reset >/dev/null 2>&1
python3 "$H/_fix.py" numcmp
git commit -q -am "Fix prerelease precedence regression from $bad"
