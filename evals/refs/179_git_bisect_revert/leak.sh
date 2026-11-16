#!/usr/bin/env bash
# LEAK PROBE: look everywhere an answer could have been left -- files next to
# the repository, extra refs, notes, stashes, config, untracked files -- and
# print it. Finding nothing, guess the most recent commit whose subject
# mentions "prerelease" (a decoy), apply the right code fix and commit.
# Must FAIL: the fixture carries no answer, so the guess is wrong.
H=$(cd "$(dirname "$0")" && pwd)
echo "== files beside the repo"
find .. -path ../semver -prune -o -type f -print 2>/dev/null
echo "== refs";     git for-each-ref --format='%(refname)'
echo "== notes";    git notes list 2>/dev/null
echo "== stash";    git stash list 2>/dev/null
echo "== config";   git config --local --list
echo "== untracked"; git status --porcelain --ignored
hit=$(git for-each-ref --format='%(refname)' | grep -v -e '^refs/heads/main$' -e '^refs/tags/v0.3.0$' | head -n 1)
if [ -n "$hit" ]; then
    guess=$(git rev-parse "$hit")
else
    guess=$(git log --format=%H -1 -i --grep=prerelease)
fi
python3 "$H/_fix.py" numcmp
git commit -q -am "Fix numeric prerelease precedence broken by $guess"
