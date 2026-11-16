#!/usr/bin/env bash
# WRONG (localisation hedged): correct fix, but the message lists every
# commit of the window bisect could not test instead of the one culprit.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
cands=$(git rev-list --reverse main | sed -n '17,23p' | tr '\n' ' ')
python3 "$H/_fix.py" numcmp
git commit -q -am "Fix numeric prerelease precedence

The regression came in with one of: $cands"
