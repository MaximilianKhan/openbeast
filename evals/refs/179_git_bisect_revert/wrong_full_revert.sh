#!/usr/bin/env bash
# WRONG (minimality): finds the right commit, then reverts ALL of it,
# resolving the conflicts toward the revert. Undoes the whole perf change.
set -eu
bad=$(git log --format=%H -1 --grep='^perf: avoid command substitution')
git revert --no-edit -X theirs "$bad" >/dev/null 2>&1 || {
    git checkout --theirs -- . && git add -A && git -c core.editor=true revert --continue >/dev/null 2>&1
}
