#!/usr/bin/env bash
# Reference solution for 179_git_bisect_revert (run with cwd = the repository).
#
# Bisect on a predicate that means the same thing at every commit since
# v0.3.0: `sort` on stdin. (The argument form of sort and the output format
# of compare both changed later, and tests/run.sh passes vacuously in
# commits 17-22, so those predicates blame the wrong commit.) Then rewrite
# only the culprit block for the _SV_R calling convention the same commit
# introduced -- reversing the hunk verbatim would restore a $(...) call to a
# helper that no longer prints anything.
set -eu
git bisect start main v0.3.0 >/dev/null
git bisect run sh -c 'first=$(printf "1.0.0-rc.10\n1.0.0-rc.9\n" | bash semver.sh sort | head -n 1); test "$first" = 1.0.0-rc.9' >/dev/null
bad=$(git rev-parse refs/bisect/bad)
git bisect reset >/dev/null 2>&1
sed -e 's/^            if \[\[ \$x < \$y \]\]; then _SV_R=-1; return 0; fi$/            _sv_numcmp "$x" "$y"/' \
    -e 's/^            if \[\[ \$x > \$y \]\]; then _SV_R=1; return 0; fi$/            if (( _SV_R != 0 )); then return 0; fi/' \
    semver.sh > semver.sh.new
mv semver.sh.new semver.sh
bash tests/run.sh >/dev/null
git commit -q -am "Fix numeric prerelease precedence

Commit $bad inlined the numeric prerelease identifier
comparison as a string comparison, so 1.0.0-rc.10 sorted before
1.0.0-rc.9. Compare the identifiers with _sv_numcmp again (it sets _SV_R
since that commit)."
