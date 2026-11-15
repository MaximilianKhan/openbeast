#!/usr/bin/env bash
# WRONG (localisation): bisect with the bug report's literal command. `sort`
# only accepts arguments since commit 18, so the first midpoint before it
# reads as "bad" and bisect blames an early, unrelated commit.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
git bisect start main v0.3.0 >/dev/null
git bisect run sh -c 'test "$(bash semver.sh sort 1.0.0-rc.10 1.0.0-rc.9 </dev/null | head -n 1)" = 1.0.0-rc.9' >/dev/null 2>&1
bad=$(git rev-parse refs/bisect/bad)
git bisect reset >/dev/null 2>&1
python3 "$H/_fix.py" numcmp
git commit -q -am "Fix prerelease precedence regression from $bad"
