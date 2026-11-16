#!/usr/bin/env python3
"""Fixture generator for 179_git_bisect_revert (embedded verbatim in setup).

Builds a 40-commit git repository of `semver.sh`, a Bash SemVer 2.0.0 tool,
in which commit 20 ("perf: ...") silently breaks numeric prerelease
precedence in ONE zero-context hunk while also switching every helper from
"print the result" to "set _SV_R".  The history is laid out so that every
lazy localisation strategy blames the wrong commit:

  * tests/run.sh is broken from commit 17 to 22 (it sources a file that does
    not exist) and reports success vacuously, so `git bisect run bash
    tests/run.sh` blames commit 23 ("test: fix helper path");
  * `compare` printed </=/> before commit 16, and `sort` only read stdin
    before commit 18, so a predicate written against HEAD's CLI flips on the
    first bisect step and blames an early commit;
  * commits 13 and 30 have prerelease-flavoured subjects (decoys).

Mechanically reversing the culprit hunk restores `c=$(_sv_numcmp ...)`, which
no longer prints anything after commit 20, so that "minimal revert" is wrong
too; the fix has to be written for the new calling convention.

Every byte (content, author, dates) is fixed, so the commit SHAs are the same
on every machine; setup asserts the final SHA it was built for.

usage: gen_repo.py DEST [EXPECTED_HEAD_SHA]       (Python >= 3.6, git >= 2.0)
"""
import os
import shutil
import subprocess
import sys
import tempfile

DEST = sys.argv[1]
EXPECT = sys.argv[2] if len(sys.argv) > 2 else None

# ---------------------------------------------------------------- semver.sh
HEADER = r'''#!/usr/bin/env bash
# semver.sh - a small SemVer 2.0.0 utility (https://semver.org)
#
# Numeric identifiers may have any number of digits.

LC_ALL=C
SEMVER_SH_VERSION=%s
'''

RE_V1 = r'''_SV_RE='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-([0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*))?$'
'''
RE_V2 = r'''_SV_RE='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-([0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*))?(\+([0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*))?$'
'''

PARSE_V1 = r'''# _sv_parse V - split V into _SV_MAJ _SV_MIN _SV_PAT _SV_PRE; fails if V is invalid
_sv_parse() {
    [[ $1 =~ $_SV_RE ]] || return 1
    _SV_MAJ=${BASH_REMATCH[1]}
    _SV_MIN=${BASH_REMATCH[2]}
    _SV_PAT=${BASH_REMATCH[3]}
    _SV_PRE=${BASH_REMATCH[5]}
}
'''
PARSE_V2 = r'''# _sv_parse V - split V into _SV_MAJ _SV_MIN _SV_PAT _SV_PRE _SV_BUILD; fails if V is invalid
_sv_parse() {
    [[ $1 =~ $_SV_RE ]] || return 1
    _SV_MAJ=${BASH_REMATCH[1]}
    _SV_MIN=${BASH_REMATCH[2]}
    _SV_PAT=${BASH_REMATCH[3]}
    _SV_PRE=${BASH_REMATCH[5]}
    _SV_BUILD=${BASH_REMATCH[8]}
}
'''
PARSE_V3 = r'''# _sv_parse V - split V into _SV_MAJ _SV_MIN _SV_PAT _SV_PRE _SV_BUILD; fails if V is invalid
_sv_parse() {
    [[ $1 =~ $_SV_RE ]] || return 1
    _SV_MAJ=${BASH_REMATCH[1]}
    _SV_MIN=${BASH_REMATCH[2]}
    _SV_PAT=${BASH_REMATCH[3]}
    _SV_PRE=${BASH_REMATCH[5]}
    _SV_BUILD=${BASH_REMATCH[8]}
    local id
    for id in ${_SV_PRE//./ }; do
        # numeric prerelease identifiers must not have leading zeros
        if [[ $id == 0?* && $id != *[!0-9]* ]]; then
            return 1
        fi
    done
    return 0
}
'''

NUMCMP_ECHO = r'''# _sv_numcmp A B - compare two decimal strings of any length; print -1, 0 or 1
_sv_numcmp() {
    if (( ${#1} < ${#2} )); then echo -1
    elif (( ${#1} > ${#2} )); then echo 1
    elif [[ $1 < $2 ]]; then echo -1
    elif [[ $1 > $2 ]]; then echo 1
    else echo 0
    fi
}
'''
NUMCMP_VAR = r'''# _sv_numcmp A B - compare two decimal strings of any length; set _SV_R to -1, 0 or 1
_sv_numcmp() {
    if (( ${#1} < ${#2} )); then _SV_R=-1
    elif (( ${#1} > ${#2} )); then _SV_R=1
    elif [[ $1 < $2 ]]; then _SV_R=-1
    elif [[ $1 > $2 ]]; then _SV_R=1
    else _SV_R=0
    fi
}
'''

CMPPRE_ECHO = r'''# _sv_cmp_pre A B - precedence of two prerelease strings (SemVer 2.0.0, item 11);
# an empty string (a release) ranks above any prerelease.  Prints -1, 0 or 1.
_sv_cmp_pre() {
    if [[ -z $1 && -z $2 ]]; then echo 0; return; fi
    if [[ -z $1 ]]; then echo 1; return; fi
    if [[ -z $2 ]]; then echo -1; return; fi
    local IFS=. x y c i=0
    local -a xs ys
    xs=($1)
    ys=($2)
    while (( i < ${#xs[@]} && i < ${#ys[@]} )); do
        x=${xs[i]}
        y=${ys[i]}
        if [[ $x != *[!0-9]* && $y != *[!0-9]* ]]; then
            c=$(_sv_numcmp "$x" "$y")
            if [[ $c != 0 ]]; then echo "$c"; return; fi
        elif [[ $x != *[!0-9]* ]]; then
            echo -1; return
        elif [[ $y != *[!0-9]* ]]; then
            echo 1; return
        elif [[ $x < $y ]]; then
            echo -1; return
        elif [[ $x > $y ]]; then
            echo 1; return
        fi
        i=$((i + 1))
    done
    _sv_numcmp ${#xs[@]} ${#ys[@]}
}
'''
# Commit 20.  The two lines after the "both numeric" test are the regression.
CMPPRE_VAR = r'''# _sv_cmp_pre A B - precedence of two prerelease strings (SemVer 2.0.0, item 11);
# an empty string (a release) ranks above any prerelease.  Sets _SV_R.
_sv_cmp_pre() {
    if [[ -z $1 && -z $2 ]]; then _SV_R=0; return 0; fi
    if [[ -z $1 ]]; then _SV_R=1; return 0; fi
    if [[ -z $2 ]]; then _SV_R=-1; return 0; fi
    local IFS=. x y i=0
    local -a xs ys
    xs=($1)
    ys=($2)
    while (( i < ${#xs[@]} && i < ${#ys[@]} )); do
        x=${xs[i]}
        y=${ys[i]}
        if [[ $x != *[!0-9]* && $y != *[!0-9]* ]]; then
            if [[ $x < $y ]]; then _SV_R=-1; return 0; fi
            if [[ $x > $y ]]; then _SV_R=1; return 0; fi
        elif [[ $x != *[!0-9]* ]]; then
            _SV_R=-1; return 0
        elif [[ $y != *[!0-9]* ]]; then
            _SV_R=1; return 0
        elif [[ $x < $y ]]; then
            _SV_R=-1; return 0
        elif [[ $x > $y ]]; then
            _SV_R=1; return 0
        fi
        i=$((i + 1))
    done
    _sv_numcmp ${#xs[@]} ${#ys[@]}
}
'''

CMP_V1 = r'''# _sv_cmp A B - print -1, 0 or 1 as A is lower, equal or higher than B
_sv_cmp() {
    local a1 a2 a3 c
    _sv_parse "$1" || return 2
    a1=$_SV_MAJ a2=$_SV_MIN a3=$_SV_PAT
    _sv_parse "$2" || return 2
    c=$(_sv_numcmp "$a1" "$_SV_MAJ"); if [[ $c != 0 ]]; then echo "$c"; return 0; fi
    c=$(_sv_numcmp "$a2" "$_SV_MIN"); if [[ $c != 0 ]]; then echo "$c"; return 0; fi
    _sv_numcmp "$a3" "$_SV_PAT"
}
'''
CMP_V2 = r'''# _sv_cmp A B - print -1, 0 or 1 as A is lower, equal or higher than B
_sv_cmp() {
    local a1 a2 a3 ap c
    _sv_parse "$1" || return 2
    a1=$_SV_MAJ a2=$_SV_MIN a3=$_SV_PAT ap=$_SV_PRE
    _sv_parse "$2" || return 2
    c=$(_sv_numcmp "$a1" "$_SV_MAJ"); if [[ $c != 0 ]]; then echo "$c"; return 0; fi
    c=$(_sv_numcmp "$a2" "$_SV_MIN"); if [[ $c != 0 ]]; then echo "$c"; return 0; fi
    c=$(_sv_numcmp "$a3" "$_SV_PAT"); if [[ $c != 0 ]]; then echo "$c"; return 0; fi
    _sv_cmp_pre "$ap" "$_SV_PRE"
}
'''
CMP_VAR = r'''# _sv_cmp A B - set _SV_R to -1, 0 or 1 as A is lower, equal or higher than B
_sv_cmp() {
    local a1 a2 a3 ap
    _sv_parse "$1" || return 2
    a1=$_SV_MAJ a2=$_SV_MIN a3=$_SV_PAT ap=$_SV_PRE
    _sv_parse "$2" || return 2
    _sv_numcmp "$a1" "$_SV_MAJ"; if (( _SV_R != 0 )); then return 0; fi
    _sv_numcmp "$a2" "$_SV_MIN"; if (( _SV_R != 0 )); then return 0; fi
    _sv_numcmp "$a3" "$_SV_PAT"; if (( _SV_R != 0 )); then return 0; fi
    _sv_cmp_pre "$ap" "$_SV_PRE"
}
'''

VALID = r'''# valid V - print V and succeed if it is a valid version, else fail silently
cmd_valid() {
    [[ $# -eq 1 ]] || { echo "usage: semver.sh valid V" >&2; return 2; }
    _sv_parse "$1" || return 1
    echo "$1"
}
'''

COMPARE_V1 = r'''# compare A B - print <, = or > as A has lower, equal or higher precedence than B
cmd_compare() {
    [[ $# -eq 2 ]] || { echo "usage: semver.sh compare A B" >&2; return 2; }
    local c
    c=$(_sv_cmp "$1" "$2") || { echo "semver.sh: invalid version" >&2; return 2; }
    case $c in
        -1) echo '<' ;;
        0) echo '=' ;;
        1) echo '>' ;;
    esac
}
'''
COMPARE_V2 = r'''# compare A B - print -1, 0 or 1 as A has lower, equal or higher precedence than B
cmd_compare() {
    [[ $# -eq 2 ]] || { echo "usage: semver.sh compare A B" >&2; return 2; }
    _sv_cmp "$1" "$2" || { echo "semver.sh: invalid version" >&2; return 2; }
}
'''
COMPARE_VAR = r'''# compare A B - print -1, 0 or 1 as A has lower, equal or higher precedence than B
cmd_compare() {
    [[ $# -eq 2 ]] || { echo "usage: semver.sh compare A B" >&2; return 2; }
    _sv_cmp "$1" "$2" || { echo "semver.sh: invalid version" >&2; return 2; }
    echo "$_SV_R"
}
'''

SORT_V1 = r'''# sort - read versions from stdin (one per line), print them by ascending
# precedence; versions of equal precedence keep their input order
cmd_sort() {
    if [[ $# -gt 0 ]]; then
        echo "usage: semver.sh sort < versions" >&2
        return 2
    fi
    local -a vs
    local v i j c n=0
    while IFS= read -r v || [[ -n $v ]]; do
        [[ -z $v ]] && continue
        _sv_parse "$v" || { echo "semver.sh: invalid version: $v" >&2; return 2; }
        vs[n]=$v
        n=$((n + 1))
    done
    for (( i = 1; i < n; i++ )); do
        v=${vs[i]}
        j=$((i - 1))
        while (( j >= 0 )); do
            c=$(_sv_cmp "${vs[j]}" "$v")
            [[ $c == 1 ]] || break
            vs[j+1]=${vs[j]}
            j=$((j - 1))
        done
        vs[j+1]=$v
    done
    for (( i = 0; i < n; i++ )); do
        echo "${vs[i]}"
    done
}
'''
SORT_V2 = r'''# sort [V...] - print the versions (arguments, or stdin one per line) by
# ascending precedence; versions of equal precedence keep their input order
cmd_sort() {
    local -a vs
    local v i j c n=0
    if [[ $# -gt 0 ]]; then
        for v in "$@"; do
            vs[n]=$v
            n=$((n + 1))
        done
    else
        while IFS= read -r v || [[ -n $v ]]; do
            [[ -z $v ]] && continue
            vs[n]=$v
            n=$((n + 1))
        done
    fi
    for (( i = 0; i < n; i++ )); do
        _sv_parse "${vs[i]}" || { echo "semver.sh: invalid version: ${vs[i]}" >&2; return 2; }
    done
    for (( i = 1; i < n; i++ )); do
        v=${vs[i]}
        j=$((i - 1))
        while (( j >= 0 )); do
            c=$(_sv_cmp "${vs[j]}" "$v")
            [[ $c == 1 ]] || break
            vs[j+1]=${vs[j]}
            j=$((j - 1))
        done
        vs[j+1]=$v
    done
    for (( i = 0; i < n; i++ )); do
        echo "${vs[i]}"
    done
}
'''
SORT_VAR = SORT_V2.replace("    local v i j c n=0\n", "    local v i j n=0\n").replace(
    '            c=$(_sv_cmp "${vs[j]}" "$v")\n            [[ $c == 1 ]] || break\n',
    '            _sv_cmp "${vs[j]}" "$v"\n            (( _SV_R == 1 )) || break\n')
SORT_V4 = r'''# sort [-r] [V...] - print the versions (arguments, or stdin one per line) by
# ascending precedence; versions of equal precedence keep their input order.
# -r prints the same list in reverse.
cmd_sort() {
    local -a vs
    local v i j n=0 rev=0
    if [[ ${1-} == -r ]]; then
        rev=1
        shift
    fi
    if [[ $# -gt 0 ]]; then
        for v in "$@"; do
            vs[n]=$v
            n=$((n + 1))
        done
    else
        while IFS= read -r v || [[ -n $v ]]; do
            [[ -z $v ]] && continue
            vs[n]=$v
            n=$((n + 1))
        done
    fi
    for (( i = 0; i < n; i++ )); do
        _sv_parse "${vs[i]}" || { echo "semver.sh: invalid version: ${vs[i]}" >&2; return 2; }
    done
    for (( i = 1; i < n; i++ )); do
        v=${vs[i]}
        j=$((i - 1))
        while (( j >= 0 )); do
            _sv_cmp "${vs[j]}" "$v"
            (( _SV_R == 1 )) || break
            vs[j+1]=${vs[j]}
            j=$((j - 1))
        done
        vs[j+1]=$v
    done
    if (( rev )); then
        for (( i = n - 1; i >= 0; i-- )); do
            echo "${vs[i]}"
        done
    else
        for (( i = 0; i < n; i++ )); do
            echo "${vs[i]}"
        done
    fi
}
'''

MAX_ECHO = r'''# max V... - print the version with the highest precedence (the first one on ties)
cmd_max() {
    [[ $# -ge 1 ]] || { echo "usage: semver.sh max V..." >&2; return 2; }
    local best=$1 v c
    _sv_parse "$best" || { echo "semver.sh: invalid version: $best" >&2; return 2; }
    shift
    for v in "$@"; do
        c=$(_sv_cmp "$v" "$best") || { echo "semver.sh: invalid version: $v" >&2; return 2; }
        [[ $c == 1 ]] && best=$v
    done
    echo "$best"
}
'''
MAX_VAR = r'''# max V... - print the version with the highest precedence (the first one on ties)
cmd_max() {
    [[ $# -ge 1 ]] || { echo "usage: semver.sh max V..." >&2; return 2; }
    local best=$1 v
    _sv_parse "$best" || { echo "semver.sh: invalid version: $best" >&2; return 2; }
    shift
    for v in "$@"; do
        _sv_cmp "$v" "$best" || { echo "semver.sh: invalid version: $v" >&2; return 2; }
        (( _SV_R == 1 )) && best=$v
    done
    echo "$best"
}
'''

BUMP_V1 = r'''# bump major|minor|patch V - print the next release; prerelease and build are dropped
cmd_bump() {
    [[ $# -eq 2 ]] || { echo "usage: semver.sh bump major|minor|patch V" >&2; return 2; }
    _sv_parse "$2" || return 2
    case $1 in
        major) echo "$((_SV_MAJ + 1)).0.0" ;;
        minor) echo "$_SV_MAJ.$((_SV_MIN + 1)).0" ;;
        patch) echo "$_SV_MAJ.$_SV_MIN.$((_SV_PAT + 1))" ;;
        *) echo "usage: semver.sh bump major|minor|patch V" >&2; return 2 ;;
    esac
}
'''
_BUMP_PRE = r'''        pre)
            if [[ -z $_SV_PRE ]]; then
                echo "$_SV_MAJ.$_SV_MIN.$((_SV_PAT + 1))-${id:+$id.}0"
            elif [[ -n $id && $_SV_PRE != "$id" && $_SV_PRE != "$id".* ]]; then
                echo "$_SV_MAJ.$_SV_MIN.$_SV_PAT-$id.0"
            else
                last=${_SV_PRE##*.}
                if [[ $last != *[!0-9]* ]]; then
                    echo "$_SV_MAJ.$_SV_MIN.$_SV_PAT-${_SV_PRE%"$last"}$((last + 1))"
                else
                    echo "$_SV_MAJ.$_SV_MIN.$_SV_PAT-$_SV_PRE.0"
                fi
            fi
            ;;
'''
BUMP_V2 = r'''# bump major|minor|patch V - print the next release; prerelease and build are dropped
# bump pre [ID] V           - print the next prerelease
cmd_bump() {
    local part=${1-} id= last
    if [[ $part == pre && $# -eq 3 ]]; then
        id=$2
        set -- "$1" "$3"
    fi
    [[ $# -eq 2 ]] || { echo "usage: semver.sh bump major|minor|patch|pre [ID] V" >&2; return 2; }
    _sv_parse "$2" || return 2
    case $part in
        major) echo "$((_SV_MAJ + 1)).0.0" ;;
        minor) echo "$_SV_MAJ.$((_SV_MIN + 1)).0" ;;
        patch) echo "$_SV_MAJ.$_SV_MIN.$((_SV_PAT + 1))" ;;
''' + _BUMP_PRE + r'''        *) echo "usage: semver.sh bump major|minor|patch|pre [ID] V" >&2; return 2 ;;
    esac
}
'''
BUMP_V3 = r'''# bump major|minor|patch V - print the next release; build metadata is dropped and
#                             a prerelease of that very release becomes the release
# bump pre [ID] V           - print the next prerelease
cmd_bump() {
    local part=${1-} id= last
    if [[ $part == pre && $# -eq 3 ]]; then
        id=$2
        set -- "$1" "$3"
    fi
    [[ $# -eq 2 ]] || { echo "usage: semver.sh bump major|minor|patch|pre [ID] V" >&2; return 2; }
    _sv_parse "$2" || return 2
    case $part in
        major)
            if [[ -n $_SV_PRE && $_SV_MIN == 0 && $_SV_PAT == 0 ]]; then echo "$_SV_MAJ.0.0"
            else echo "$((_SV_MAJ + 1)).0.0"; fi
            ;;
        minor)
            if [[ -n $_SV_PRE && $_SV_PAT == 0 ]]; then echo "$_SV_MAJ.$_SV_MIN.0"
            else echo "$_SV_MAJ.$((_SV_MIN + 1)).0"; fi
            ;;
        patch)
            if [[ -n $_SV_PRE ]]; then echo "$_SV_MAJ.$_SV_MIN.$_SV_PAT"
            else echo "$_SV_MAJ.$_SV_MIN.$((_SV_PAT + 1))"; fi
            ;;
''' + _BUMP_PRE + r'''        *) echo "usage: semver.sh bump major|minor|patch|pre [ID] V" >&2; return 2 ;;
    esac
}
'''
BUMP_V4 = BUMP_V3.replace('    _sv_parse "$2" || return 2\n',
                          '    _sv_parse "$2" || { echo "semver.sh: invalid version: $2" >&2; return 2; }\n')

STRIP_V1 = r'''# strip V - print V without its build metadata
cmd_strip() {
    [[ $# -eq 1 ]] || { echo "usage: semver.sh strip V" >&2; return 2; }
    _sv_parse "$1" || { echo "semver.sh: invalid version: $1" >&2; return 2; }
    echo "${1%%[-+]*}"
}
'''
STRIP_V2 = STRIP_V1.replace('echo "${1%%[-+]*}"', 'echo "${1%%+*}"')

DIFF = r'''# diff A B - print the most significant part that differs:
# major, minor, patch, prerelease or none (build metadata is ignored)
cmd_diff() {
    [[ $# -eq 2 ]] || { echo "usage: semver.sh diff A B" >&2; return 2; }
    local a1 a2 a3 ap
    _sv_parse "$1" || { echo "semver.sh: invalid version: $1" >&2; return 2; }
    a1=$_SV_MAJ a2=$_SV_MIN a3=$_SV_PAT ap=$_SV_PRE
    _sv_parse "$2" || { echo "semver.sh: invalid version: $2" >&2; return 2; }
    if [[ $a1 != "$_SV_MAJ" ]]; then echo major
    elif [[ $a2 != "$_SV_MIN" ]]; then echo minor
    elif [[ $a3 != "$_SV_PAT" ]]; then echo patch
    elif [[ $ap != "$_SV_PRE" ]]; then echo prerelease
    else echo none
    fi
}
'''

TEST_ECHO = r'''# _sv_test V OP W - succeed if "V OP W" holds; OP is one of > >= < <= =
_sv_test() {
    local c
    c=$(_sv_cmp "$1" "$3") || return 2
    case $2 in
        '>') [[ $c == 1 ]] ;;
        '>=') [[ $c != -1 ]] ;;
        '<') [[ $c == -1 ]] ;;
        '<=') [[ $c != 1 ]] ;;
        '=') [[ $c == 0 ]] ;;
        *) return 2 ;;
    esac
}
'''
TEST_VAR = r'''# _sv_test V OP W - succeed if "V OP W" holds; OP is one of > >= < <= =
_sv_test() {
    _sv_cmp "$1" "$3" || return 2
    case $2 in
        '>') (( _SV_R == 1 )) ;;
        '>=') (( _SV_R != -1 )) ;;
        '<') (( _SV_R == -1 )) ;;
        '<=') (( _SV_R != 1 )) ;;
        '=') (( _SV_R == 0 )) ;;
        *) return 2 ;;
    esac
}
'''

_COMP_HEAD = r'''# _sv_comparator V C - succeed if V satisfies the comparator C (e.g. ">=1.2.3")
_sv_comparator() {
    local op ver hi
    case $2 in
        '>='*|'<='*) op=${2:0:2} ver=${2:2} ;;
        '>'*|'<'*|'='*) op=${2:0:1} ver=${2:1} ;;
'''
_COMP_CARET = r'''        '^'*)
            ver=${2:1}
            _sv_parse "$ver" || return 2
            if (( _SV_MAJ > 0 )); then hi=$((_SV_MAJ + 1)).0.0
            elif (( _SV_MIN > 0 )); then hi=0.$((_SV_MIN + 1)).0
            else hi=0.0.$((_SV_PAT + 1))
            fi
            _sv_test "$1" '>=' "$ver" && _sv_test "$1" '<' "$hi"
            return
            ;;
'''
_COMP_TILDE = r'''        '~'*)
            ver=${2:1}
            _sv_parse "$ver" || return 2
            hi=$_SV_MAJ.$((_SV_MIN + 1)).0
            _sv_test "$1" '>=' "$ver" && _sv_test "$1" '<' "$hi"
            return
            ;;
'''
_COMP_X = r'''        '*'|x) return 0 ;;
        *.x)
            local re1='^(0|[1-9][0-9]*)$' re2='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
            ver=${2%.x}
            if [[ $ver =~ $re1 ]]; then
                _sv_test "$1" '>=' "$ver.0.0" && _sv_test "$1" '<' "$((ver + 1)).0.0"
            elif [[ $ver =~ $re2 ]]; then
                hi=${BASH_REMATCH[1]}.$((BASH_REMATCH[2] + 1)).0
                _sv_test "$1" '>=' "$ver.0" && _sv_test "$1" '<' "$hi"
            else
                return 2
            fi
            return
            ;;
'''
_COMP_TAIL = r'''        *) op='=' ver=$2 ;;
    esac
    _sv_parse "$ver" || return 2
    _sv_test "$1" "$op" "$ver"
}
'''
COMP_V1 = _COMP_HEAD + _COMP_TAIL
COMP_V2 = _COMP_HEAD + _COMP_CARET + _COMP_TAIL
COMP_V3 = _COMP_HEAD + _COMP_CARET + _COMP_TILDE + _COMP_TAIL
COMP_V4 = _COMP_HEAD + _COMP_CARET + _COMP_TILDE + _COMP_X + _COMP_TAIL

SATISFIES_V1 = r'''# _sv_satisfies V RANGE - RANGE is space-separated comparators that must all hold
_sv_satisfies() {
    local -a cs
    local c
    read -r -a cs <<< "$2"
    for c in "${cs[@]}"; do
        _sv_comparator "$1" "$c" || return 1
    done
    return 0
}
'''
SATISFIES_V2 = r'''# _sv_satisfies V RANGE - RANGE is one or more comparator sets joined by "||";
# a set is space-separated comparators that must all hold
_sv_satisfies() {
    local -a cs
    local c alt ok range=$2
    while :; do
        alt=${range%%'||'*}
        read -r -a cs <<< "$alt"
        ok=1
        for c in "${cs[@]}"; do
            _sv_comparator "$1" "$c" || { ok=0; break; }
        done
        (( ok )) && return 0
        [[ $range == *'||'* ]] || return 1
        range=${range#*'||'}
    done
}
'''

CMD_SATISFIES = r'''# satisfies V RANGE - print yes (exit 0) or no (exit 1)
cmd_satisfies() {
    [[ $# -eq 2 ]] || { echo "usage: semver.sh satisfies V RANGE" >&2; return 2; }
    _sv_parse "$1" || { echo "semver.sh: invalid version: $1" >&2; return 2; }
    if _sv_satisfies "$1" "$2"; then
        echo yes
    else
        echo no
        return 1
    fi
}
'''
CMD_FILTER = r'''# filter RANGE V... - print each V that satisfies RANGE, in input order
cmd_filter() {
    [[ $# -ge 1 ]] || { echo "usage: semver.sh filter RANGE V..." >&2; return 2; }
    local range=$1 v
    shift
    for v in "$@"; do
        _sv_parse "$v" || { echo "semver.sh: invalid version: $v" >&2; return 2; }
        _sv_satisfies "$v" "$range" && echo "$v"
    done
    return 0
}
'''

GUARD = r'''if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
'''

HELP = {
    "valid": "  valid V                  print V if it is a valid SemVer 2.0.0 version",
    "compare1": "  compare A B              print <, = or > (precedence of A against B)",
    "compare": "  compare A B              print -1, 0 or 1 (precedence of A against B)",
    "sort1": "  sort < FILE              sort versions read from stdin by precedence",
    "sort": "  sort [V...]              sort versions (arguments or stdin) by precedence",
    "sort4": "  sort [-r] [V...]         sort versions (arguments or stdin) by precedence",
    "bump1": "  bump major|minor|patch V print the next release",
    "bump": "  bump PART [ID] V         PART is major, minor, patch or pre",
    "max": "  max V...                 print the highest version",
    "satisfies": "  satisfies V RANGE        print yes or no; exit 0 if V is in RANGE",
    "strip": "  strip V                  drop the build metadata",
    "diff": "  diff A B                 print the most significant part that differs",
    "filter": "  filter RANGE V...        print the versions that are in RANGE",
}
ORDER = ["valid", "compare", "sort", "max", "bump", "strip", "diff", "satisfies", "filter"]


def usage_block(helps):
    return ("usage() {\n    cat <<'EOF'\nusage: semver.sh <command> [args]\n\ncommands:\n"
            + "\n".join(helps) + "\nEOF\n}\n")


def main_block(cmds, helpflag):
    lines = ["main() {", "    local cmd=${1-}", "    [[ $# -gt 0 ]] && shift", "    case $cmd in"]
    for c in cmds:
        lines.append("        %s) cmd_%s \"$@\" ;;" % (c, c))
    if helpflag:
        lines.append("        help|-h|--help) usage ;;")
    lines += ["        *) usage >&2; return 2 ;;", "    esac", "}"]
    return "\n".join(lines) + "\n"


BLOCK_ORDER = ["header", "re", "parse", "numcmp", "cmp_pre", "cmp", "valid", "compare",
               "sort", "max", "bump", "strip", "diff", "test", "comparator", "satisfies_fn",
               "satisfies", "filter", "usage", "main", "guard"]


class Script:
    def __init__(self):
        self.b = {}
        self.version = "0.1.0"
        self.cmds = []
        self.helps = {}
        self.helpflag = False

    def render(self):
        b = dict(self.b)
        b["header"] = HEADER % self.version
        b["usage"] = usage_block([self.helps[c] for c in ORDER if c in self.helps])
        b["main"] = main_block([c for c in ORDER if c in self.cmds], self.helpflag)
        b["guard"] = GUARD
        return "\n".join(b[k] for k in BLOCK_ORDER if k in b)


# ---------------------------------------------------------------- tests
T_PRELUDE_INLINE = r'''#!/usr/bin/env bash
# Test suite for semver.sh.  Run from anywhere: bash tests/run.sh  (exit 0 = pass)
cd "$(dirname "$0")/.." || exit 1
PASS=0
FAIL=0

# check NAME WANT CMD... - CMD's stdout must equal WANT
check() {
    local name=$1 want=$2 got
    shift 2
    got=$("$@" 2>/dev/null)
    if [[ $got == "$want" ]]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        echo "FAIL $name"
        echo "  want: $want"
        echo "  got:  $got"
    fi
}

# check_rc NAME RC CMD... - CMD must exit with status RC
check_rc() {
    local name=$1 want=$2 rc
    shift 2
    "$@" >/dev/null 2>&1
    rc=$?
    if [[ $rc == "$want" ]]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        echo "FAIL $name (exit $rc, want $want)"
    fi
}

sv() { bash ./semver.sh "$@"; }
svin() { local input=$1; shift; printf '%s\n' "$input" | bash ./semver.sh "$@"; }
'''
LIB = T_PRELUDE_INLINE.split("PASS=0\n", 1)[1]
LIB = "# Helpers for tests/run.sh\nPASS=0\n" + LIB
LIB_Q = LIB.replace('''        echo "FAIL $name"
        echo "  want: $want"
        echo "  got:  $got"''', '''        printf 'FAIL %s\\n  want: %q\\n  got:  %q\\n' "$name" "$want" "$got"''')
T_PRELUDE_LIB = r'''#!/usr/bin/env bash
# Test suite for semver.sh.  Run from anywhere: bash tests/run.sh  (exit 0 = pass)
cd "$(dirname "$0")/.." || exit 1
. tests/lib.sh
'''
T_FOOTER = r'''
echo "$PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
'''

S_VALID = r'''
# --- valid
check valid-basic 1.2.3 sv valid 1.2.3
check valid-pre 1.0.0-alpha.1 sv valid 1.0.0-alpha.1
check_rc valid-short 1 sv valid 1.2
check_rc valid-leading-zero 1 sv valid 01.2.3
check_rc valid-empty-pre 1 sv valid 1.2.3-
check_rc valid-junk 1 sv valid v1.2.3
'''
S_COMPARE1 = r'''
# --- compare
check cmp-major '<' sv compare 1.9.9 2.0.0
check cmp-minor '>' sv compare 1.10.0 1.9.0
check cmp-patch '<' sv compare 1.0.9 1.0.10
check cmp-equal '=' sv compare 3.4.5 3.4.5
check cmp-big '<' sv compare 1.0.99999999999999999999 1.0.100000000000000000000
check_rc cmp-invalid 2 sv compare 1.0 1.0.0
'''
S_PRE = r'''
# --- prerelease precedence (SemVer 2.0.0, item 11)
check pre-release-wins '<' sv compare 1.0.0-alpha 1.0.0
check pre-alpha-beta '<' sv compare 1.0.0-alpha 1.0.0-beta
check pre-num-alpha '<' sv compare 1.0.0-1 1.0.0-alpha
check pre-longer '<' sv compare 1.0.0-alpha 1.0.0-alpha.1
check pre-num '<' sv compare 1.0.0-rc.9 1.0.0-rc.10
check pre-num2 '>' sv compare 1.0.0-alpha.10 1.0.0-alpha.2
check pre-big '<' sv compare 1.0.0-rc.99999999999999999999 1.0.0-rc.100000000000000000000
check pre-ascii '<' sv compare 1.0.0-RC 1.0.0-rc
check pre-equal '=' sv compare 1.0.0-x.7.z 1.0.0-x.7.z
'''
S_SORT1 = r'''
# --- sort
check sort-basic $'1.0.0\n1.2.0\n2.0.0' svin $'2.0.0\n1.0.0\n1.2.0' sort
check sort-spec $'1.0.0-alpha\n1.0.0-alpha.1\n1.0.0-alpha.beta\n1.0.0-beta\n1.0.0-beta.2\n1.0.0-beta.11\n1.0.0-rc.1\n1.0.0' svin $'1.0.0\n1.0.0-beta.11\n1.0.0-alpha.beta\n1.0.0-rc.1\n1.0.0-alpha\n1.0.0-beta.2\n1.0.0-beta\n1.0.0-alpha.1' sort
check sort-rc $'1.0.0-rc.9\n1.0.0-rc.10' svin $'1.0.0-rc.10\n1.0.0-rc.9' sort
check_rc sort-invalid 2 svin $'1.0.0\nbogus' sort
'''
S_BUMP1 = r'''
# --- bump
check bump-major 2.0.0 sv bump major 1.4.7
check bump-minor 1.5.0 sv bump minor 1.4.7
check bump-patch 1.4.8 sv bump patch 1.4.7
check_rc bump-bad-part 2 sv bump micro 1.4.7
'''
S_BUILD = r'''
# --- build metadata
check build-valid 1.0.0+20130313144700 sv valid 1.0.0+20130313144700
check build-valid-pre 1.0.0-beta+exp.sha.5114f85 sv valid 1.0.0-beta+exp.sha.5114f85
check build-ignored '=' sv compare 1.0.0+build.1 1.0.0+build.2
check build-ignored-pre '<' sv compare 1.0.0-rc.1+b 1.0.0+a
check_rc build-empty 1 sv valid 1.0.0+
'''
S_MAX = r'''
# --- max
check max-basic 2.1.0 sv max 1.0.0 2.1.0 2.0.5
check max-pre 1.0.0 sv max 1.0.0-rc.2 1.0.0 1.0.0-rc.1
check max-tie 1.0.0+a sv max 1.0.0+a 1.0.0+b
check_rc max-invalid 2 sv max 1.0.0 nope
'''
S_LEADZERO = r'''
# --- leading zeros in numeric prerelease identifiers
check_rc lz-pre 1 sv valid 1.0.0-rc.01
check lz-alnum 1.0.0-0a sv valid 1.0.0-0a
check lz-zero 1.0.0-0 sv valid 1.0.0-0
'''
S_BUMPPRE = r'''
# --- bump pre
check bump-pre-new 1.2.4-0 sv bump pre 1.2.3
check bump-pre-id 1.2.4-rc.0 sv bump pre rc 1.2.3
check bump-pre-inc 1.2.3-rc.2 sv bump pre 1.2.3-rc.1
check bump-pre-inc-id 1.2.3-rc.2 sv bump pre rc 1.2.3-rc.1
check bump-pre-switch 1.2.3-beta.0 sv bump pre beta 1.2.3-alpha.4
check bump-pre-append 1.2.3-alpha.0 sv bump pre 1.2.3-alpha
'''
S_SORTARGS = r'''
# --- sort with arguments
check sort-args $'0.9.0\n1.0.0-rc.1\n1.0.0' sv sort 1.0.0 0.9.0 1.0.0-rc.1
check sort-stable $'1.0.0+b\n1.0.0+a' sv sort 1.0.0+b 1.0.0+a
check_rc sort-args-invalid 2 sv sort 1.0.0 1.0
'''
S_SAT1 = r'''
# --- satisfies
check sat-ge yes sv satisfies 1.2.3 '>=1.2.0'
check sat-lt no sv satisfies 2.0.0 '<2.0.0'
check sat-and yes sv satisfies 1.5.0 '>=1.2.0 <2.0.0'
check sat-and-no no sv satisfies 2.0.0 '>=1.2.0 <2.0.0'
check sat-eq yes sv satisfies 1.0.0 1.0.0
check sat-pre yes sv satisfies 1.0.0-rc.10 '>1.0.0-rc.9'
check_rc sat-rc 1 sv satisfies 1.0.0 '>1.0.0'
'''
S_CARET = r'''
# --- caret ranges
check caret-1 yes sv satisfies 1.9.9 '^1.2.3'
check caret-2 no sv satisfies 2.0.0 '^1.2.3'
check caret-0 no sv satisfies 0.3.0 '^0.2.3'
check caret-00 no sv satisfies 0.0.4 '^0.0.3'
'''
S_TILDE = r'''
# --- tilde ranges
check tilde-1 yes sv satisfies 1.2.9 '~1.2.3'
check tilde-2 no sv satisfies 1.3.0 '~1.2.3'
'''
S_OR = r'''
# --- || alternatives
check or-1 yes sv satisfies 3.1.0 '<1.0.0 || >=3.0.0'
check or-2 no sv satisfies 2.0.0 '<1.0.0 || >=3.0.0'
check or-3 yes sv satisfies 1.4.0 '^2.0.0 || ~1.4.0'
'''
S_HELP = r'''
# --- help
check_rc help-flag 0 sv --help
check_rc help-unknown 2 sv frobnicate
'''
S_BUMPREL = r'''
# --- bumping a prerelease releases it
check bump-rel-patch 1.2.3 sv bump patch 1.2.3-rc.1
check bump-rel-minor 1.3.0 sv bump minor 1.3.0-beta
check bump-rel-minor2 1.4.0 sv bump minor 1.3.1-beta
check bump-rel-major 2.0.0 sv bump major 2.0.0-alpha
'''
S_STRIP1 = r'''
# --- strip
check strip-build 1.2.3 sv strip 1.2.3+build.7
check strip-none 1.2.3 sv strip 1.2.3
'''
S_STRIP2 = r'''check strip-pre 1.2.3-rc.1 sv strip 1.2.3-rc.1+build.7
check strip-pre-only 1.2.3-rc.1 sv strip 1.2.3-rc.1
'''
S_XR = r'''
# --- x-ranges
check xr-star yes sv satisfies 9.9.9 '*'
check xr-major yes sv satisfies 1.9.0 '1.x'
check xr-major-no no sv satisfies 2.0.0 '1.x'
check xr-minor yes sv satisfies 1.2.7 '1.2.x'
check xr-minor-no no sv satisfies 1.3.0 '1.2.x'
'''
S_SORTR = r'''
# --- sort -r
check sort-r $'2.0.0\n1.0.0\n1.0.0-rc.1' sv sort -r 1.0.0 1.0.0-rc.1 2.0.0
check sort-r-stdin $'1.1.0\n1.0.0' svin $'1.0.0\n1.1.0' sort -r
'''
S_DIFF = r'''
# --- diff
check diff-major major sv diff 1.0.0 2.0.0
check diff-minor minor sv diff 1.1.0 1.2.0
check diff-patch patch sv diff 1.1.1 1.1.2
check diff-pre prerelease sv diff 1.0.0-rc.1 1.0.0
check diff-none none sv diff 1.0.0+a 1.0.0+b
'''
S_BUMPBAD = r'''
# --- bump rejects invalid input
check_rc bump-invalid 2 sv bump patch 1.2
'''
S_FILTER = r'''
# --- filter
check filter-basic $'1.2.0\n1.9.1' sv filter '^1.2.0' 1.2.0 2.0.0 1.9.1 1.1.9
check filter-none '' sv filter '>5.0.0' 1.0.0 2.0.0
check filter-pre $'1.0.0-rc.10\n1.0.0' sv filter '>1.0.0-rc.9' 1.0.0-rc.2 1.0.0-rc.10 1.0.0
check_rc filter-invalid 2 sv filter '*' 1.0.0 x.y.z
'''


class Tests:
    def __init__(self):
        self.prelude = T_PRELUDE_INLINE
        self.sections = []

    def render(self):
        return self.prelude + "".join(self.sections) + T_FOOTER


# ---------------------------------------------------------------- docs
README_1 = """# semver.sh

A small, dependency-free SemVer 2.0.0 utility written in Bash.

    bash semver.sh valid 1.2.3
"""
README_USAGE = """
## Usage

    bash semver.sh valid V          # print V if valid, exit 1 otherwise
    bash semver.sh compare A B      # precedence of A against B
    bash semver.sh sort < versions  # sort by precedence

Numeric identifiers (major, minor, patch and numeric prerelease identifiers)
may have any number of digits and are compared numerically.

## Tests

    bash tests/run.sh
"""
README_MORE = """
## Commands

| command | what it does |
|---|---|
| `valid V` | print V if it is valid |
| `compare A B` | print -1, 0 or 1 |
| `sort [-r] [V...]` | sort by precedence (arguments, or stdin) |
| `max V...` | highest version |
| `bump PART [ID] V` | next major/minor/patch/pre version |
| `satisfies V RANGE` | range test, see below |
| `filter RANGE V...` | keep the versions in RANGE |
| `strip V` | drop build metadata |
| `diff A B` | most significant differing part |
"""
README_RANGES = """
## Ranges

A RANGE is one or more comparator sets joined by `||`.  A set is a list of
space-separated comparators that must all hold.  A comparator is `>V`, `>=V`,
`<V`, `<=V`, `=V`, a bare `V` (same as `=V`), `^V`, `~V`, `M.x`, `M.m.x` or `*`.
"""
README_EXAMPLES = """
## Examples

    $ bash semver.sh diff 1.4.2 1.5.0
    minor
    $ bash semver.sh strip 2.0.0-rc.1+sha.5114f85
    2.0.0-rc.1
"""

LICENSE = """MIT License

Copyright (c) 2026 semver.sh contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
"""

CI_1 = """# CI for semver.sh
jobs:
  test:
    steps:
      - run: bash tests/run.sh
"""
CI_2 = """# CI for semver.sh
jobs:
  test:
    strategy:
      matrix:
        bash: ["3.2", "5.2"]
    steps:
      - run: bash tests/run.sh
"""
CI_3 = CI_2 + """  lint:
    steps:
      - run: shellcheck semver.sh tests/run.sh tests/lib.sh
"""

CHANGELOG_1 = """# Changelog

## 0.5.0
- `max` command
- build metadata is accepted and ignored for precedence
- `valid` rejects leading zeros in numeric prerelease identifiers

## 0.3.0
- `valid`, `compare`, `sort`
"""


def changelog_2():
    return CHANGELOG_1.replace("# Changelog\n", """# Changelog

## 0.9.0
- `compare` prints -1, 0 or 1 (it printed <, = or > before 0.6.0)
- `sort` takes versions as arguments as well as on stdin; `-r` reverses
- `satisfies` (comparators, `^`, `~`, x-ranges, `||`) and `filter`
- `bump pre`; bumping a prerelease releases it
- `strip`, `diff`, `--help`
""")


# ---------------------------------------------------------------- history
AUTHORS = [("Dana Whitfield", "dana@semver-sh.example"),
           ("Ravi Menon", "ravi@semver-sh.example"),
           ("Jo Okafor", "jo@semver-sh.example")]


def build():
    s = Script()
    t = Tests()
    files = {}
    commits = []  # (subject, body, author_idx, files snapshot, tag)

    def commit(subject, author=0, body="", tag=None):
        snap = dict(files)
        snap["semver.sh"] = s.render()
        if "tests/run.sh" in snap or t.sections:
            snap["tests/run.sh"] = t.render()
        commits.append((subject, body, author, snap, tag))

    # 1
    s.b["re"] = RE_V1
    s.b["parse"] = PARSE_V1
    s.b["valid"] = VALID
    s.cmds = ["valid"]
    s.helps = {"valid": HELP["valid"]}
    files["README.md"] = README_1
    files["LICENSE"] = LICENSE
    commit("Initial commit: semver.sh with a valid command")
    # 2
    s.b["numcmp"] = NUMCMP_ECHO
    s.b["cmp"] = CMP_V1
    s.b["compare"] = COMPARE_V1
    s.cmds.append("compare")
    s.helps["compare"] = HELP["compare1"]
    commit("Add compare command", 0,
           "Compares major, minor and patch numerically. Numbers are compared\n"
           "as decimal strings so they may be arbitrarily long.")
    # 3
    t.sections.append(S_VALID + S_COMPARE1)
    commit("Add a test suite", 1)
    # 4
    s.b["cmp_pre"] = CMPPRE_ECHO
    s.b["cmp"] = CMP_V2
    commit("compare: implement prerelease precedence", 0,
           "Follows item 11 of SemVer 2.0.0: identifiers are compared left to\n"
           "right, numeric ones numerically, alphanumeric ones in ASCII order,\n"
           "numeric below alphanumeric, and a longer list wins a tie.")
    # 5
    t.sections.append(S_PRE)
    commit("test: cover prerelease precedence", 1)
    # 6
    s.b["sort"] = SORT_V1
    s.cmds.append("sort")
    s.helps["sort"] = HELP["sort1"]
    t.sections.append(S_SORT1)
    commit("Add sort command", 0)
    # 7
    s.version = "0.3.0"
    files["README.md"] = README_1 + README_USAGE
    commit("Release 0.3.0", 0, tag="v0.3.0")
    # 8
    s.b["bump"] = BUMP_V1
    s.cmds.append("bump")
    s.helps["bump"] = HELP["bump1"]
    t.sections.append(S_BUMP1)
    commit("Add bump command", 2)
    # 9
    s.b["re"] = RE_V2
    s.b["parse"] = PARSE_V2
    t.sections.append(S_BUILD)
    commit("Accept build metadata and ignore it for precedence", 1)
    # 10
    files[".ci.yml"] = CI_1
    commit("ci: run the test suite", 2)
    # 11
    s.b["max"] = MAX_ECHO
    s.cmds.append("max")
    s.helps["max"] = HELP["max"]
    t.sections.append(S_MAX)
    commit("Add max command", 0)
    # 12
    files["README.md"] = README_1 + README_USAGE.replace(
        "## Tests", "`compare` prints `<`, `=` or `>`.\n\n## Tests")
    commit("docs: describe compare output", 2)
    # 13 (decoy subject)
    s.b["parse"] = PARSE_V3
    t.sections.append(S_LEADZERO)
    commit("valid: reject leading zeros in numeric prerelease identifiers", 1,
           "SemVer 2.0.0 item 9: numeric identifiers MUST NOT include leading\n"
           "zeroes. 1.0.0-rc.01 was accepted before.")
    # 14
    s.version = "0.5.0"
    files["CHANGELOG.md"] = CHANGELOG_1
    commit("Release 0.5.0", 0)
    # 15
    s.b["bump"] = BUMP_V2
    s.helps["bump"] = HELP["bump"]
    t.sections.append(S_BUMPPRE)
    commit("bump: add 'pre' to compute the next prerelease", 2)
    # 16
    s.b["compare"] = COMPARE_V2
    s.helps["compare"] = HELP["compare"]
    t.sections = [x.replace("check cmp-major '<'", "check cmp-major -1")
                  .replace("check cmp-minor '>'", "check cmp-minor 1")
                  .replace("check cmp-patch '<'", "check cmp-patch -1")
                  .replace("check cmp-equal '='", "check cmp-equal 0")
                  .replace("check cmp-big '<'", "check cmp-big -1")
                  .replace(" '<' sv compare", " -1 sv compare")
                  .replace(" '>' sv compare", " 1 sv compare")
                  .replace(" '=' sv compare", " 0 sv compare") for x in t.sections]
    files["README.md"] = README_1 + README_USAGE.replace(
        "## Tests", "`compare` prints `-1`, `0` or `1`.\n\n## Tests")
    commit("compare: print -1, 0 or 1 instead of <, = or >", 0,
           "Machine-friendly output; scripts can now do arithmetic on it.\n"
           "BREAKING: callers that matched on '<' must be updated.")
    # 17: break the runner (sources a file that is never created)
    t.prelude = T_PRELUDE_LIB
    files["tests/helpers.sh"] = LIB
    commit("test: move the helpers out of run.sh", 1)
    # 18
    s.b["sort"] = SORT_V2
    s.helps["sort"] = HELP["sort"]
    t.sections.append(S_SORTARGS)
    commit("sort: accept versions as arguments", 2,
           "Reading stdin still works when no argument is given.")
    # 19
    s.b["test"] = TEST_ECHO
    s.b["comparator"] = COMP_V1
    s.b["satisfies_fn"] = SATISFIES_V1
    s.b["satisfies"] = CMD_SATISFIES
    s.cmds.append("satisfies")
    s.helps["satisfies"] = HELP["satisfies"]
    t.sections.append(S_SAT1)
    commit("Add satisfies command with basic comparators", 0)
    # 20: THE CULPRIT
    s.b["numcmp"] = NUMCMP_VAR
    s.b["cmp_pre"] = CMPPRE_VAR
    s.b["cmp"] = CMP_VAR
    s.b["compare"] = COMPARE_VAR
    s.b["sort"] = SORT_VAR
    s.b["max"] = MAX_VAR
    s.b["test"] = TEST_VAR
    commit("perf: avoid command substitution in the comparison hot path", 1,
           "Every $(...) forks a subshell. sort is insertion sort, so it ran\n"
           "O(n^2) compares and each compare forked up to five subshells.\n"
           "Helpers now return their result in _SV_R instead of printing it;\n"
           "sorting 200 versions goes from 9.1 s to 0.4 s.")
    # 21
    s.b["comparator"] = COMP_V2
    t.sections.append(S_CARET)
    commit("satisfies: support caret ranges", 2)
    # 22
    s.b["comparator"] = COMP_V3
    t.sections.append(S_TILDE)
    commit("satisfies: support tilde ranges", 2)
    # 23: repair the runner
    del files["tests/helpers.sh"]
    files["tests/lib.sh"] = LIB
    commit("test: fix helper path", 1, "run.sh sources tests/lib.sh; the file was added under another name.")
    # 24
    s.b["satisfies_fn"] = SATISFIES_V2
    t.sections.append(S_OR)
    commit("satisfies: support || alternatives", 0)
    # 25
    s.helpflag = True
    t.sections.append(S_HELP)
    commit("Add help, -h and --help", 2)
    # 26
    files[".ci.yml"] = CI_2
    commit("ci: test on bash 3.2 and 5.2", 2)
    # 27
    s.b["bump"] = BUMP_V3
    t.sections.append(S_BUMPREL)
    commit("bump: bumping a prerelease releases it", 0,
           "1.2.3-rc.1 -> patch -> 1.2.3, as npm does.")
    # 28
    files["README.md"] = (README_1 + README_USAGE.replace(
        "## Tests", "`compare` prints `-1`, `0` or `1`.\n\n## Tests") + README_RANGES)
    commit("docs: document ranges", 1)
    # 29
    s.b["strip"] = STRIP_V1
    s.cmds.append("strip")
    s.helps["strip"] = HELP["strip"]
    t.sections.append(S_STRIP1)
    commit("Add strip command", 2)
    # 30 (decoy subject)
    s.b["strip"] = STRIP_V2
    t.sections.append(S_STRIP2)
    commit("fix: strip dropped the prerelease along with build metadata", 2)
    # 31
    s.b["comparator"] = COMP_V4
    t.sections.append(S_XR)
    commit("satisfies: support x-ranges and *", 0)
    # 32
    s.b["sort"] = SORT_V4
    s.helps["sort"] = HELP["sort4"]
    t.sections.append(S_SORTR)
    commit("sort: add -r for descending order", 1)
    # 33
    files["tests/lib.sh"] = LIB_Q
    commit("test: quote want/got in failure reports", 1)
    # 34
    s.b["diff"] = DIFF
    s.cmds.append("diff")
    s.helps["diff"] = HELP["diff"]
    t.sections.append(S_DIFF)
    commit("Add diff command", 0)
    # 35
    files[".ci.yml"] = CI_3
    commit("ci: add a shellcheck job", 2)
    # 36
    s.b["bump"] = BUMP_V4
    t.sections.append(S_BUMPBAD)
    commit("bump: explain invalid input", 2)
    # 37
    files["README.md"] = files["README.md"] + README_MORE + README_EXAMPLES
    commit("docs: command table and examples", 1)
    # 38
    s.b["filter"] = CMD_FILTER
    s.cmds.append("filter")
    s.helps["filter"] = HELP["filter"]
    t.sections.append(S_FILTER)
    commit("Add filter command", 0)
    # 39
    files["CHANGELOG.md"] = changelog_2()
    commit("docs: changelog for 0.9.0", 0)
    # 40
    s.version = "0.9.0"
    commit("Release 0.9.0", 0)
    assert len(commits) == 40, len(commits)
    return commits


def git(args, cwd, env, data=None):
    return subprocess.run(["git"] + args, cwd=cwd, env=env, check=True, input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          ).stdout.decode("utf-8", "replace").strip()


def clean_message(msg):
    """What `git commit -m` stores (cleanup=whitespace): trailing whitespace
    stripped per line, runs of blank lines collapsed, leading and trailing
    blank lines dropped, one final newline."""
    out = []
    for ln in msg.split("\n"):
        ln = ln.rstrip()
        if ln or (out and out[-1]):
            out.append(ln)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"


def fast_import_stream(commits):
    """The whole history as ONE `git fast-import` stream. Same trees, authors,
    dates and messages as a commit-per-step build, so the SHAs are identical
    (setup asserts the head SHA), in a handful of git spawns instead of ~90:
    each spawn of /usr/bin/git on macOS is an xcrun shim that costs ~36 ms."""
    out = []

    def data(text):
        raw = text.encode("utf-8")
        out.append(b"data %d\n" % len(raw) + raw + b"\n")

    for i, (subject, body, author, snap, tag) in enumerate(commits):
        name, email = AUTHORS[author]
        # 2026-03-02 09:00 UTC, then roughly a commit every 7 hours
        date = "%d +0000" % (1772442000 + i * 25200 + (i * 7919) % 3000)
        who = ("%s <%s> %s" % (name, email, date)).encode("utf-8")
        out.append(b"commit refs/heads/main\nmark :%d\n" % (i + 1))
        out.append(b"author " + who + b"\ncommitter " + who + b"\n")
        data(clean_message(subject + ("\n\n" + body if body else "")))
        if i:
            out.append(b"from :%d\n" % i)
        out.append(b"deleteall\n")
        for path in sorted(snap):
            out.append(b"M 100644 inline " + path.encode("utf-8") + b"\n")
            data(snap[path])
        out.append(b"\n")
        if tag:
            out.append(b"reset refs/tags/" + tag.encode("utf-8") + b"\nfrom :%d\n\n" % (i + 1))
    out.append(b"done\n")
    return b"".join(out)


def main():
    commits = build()
    home = tempfile.mkdtemp(prefix="gen179_")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"HOME": home, "XDG_CONFIG_HOME": home, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C", "TZ": "UTC"})
    if os.path.exists(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST)
    try:
        git(["init", "-q"], DEST, env)
        git(["symbolic-ref", "HEAD", "refs/heads/main"], DEST, env)
        for k, v in (("core.autocrlf", "false"), ("core.filemode", "true"),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git(["config", k, v], DEST, env)
        git(["fast-import", "--quiet", "--done"], DEST, env, data=fast_import_stream(commits))
        # index + work tree of the tip, as a commit-per-step build leaves them
        git(["reset", "-q", "--hard", "refs/heads/main"], DEST, env)
        # identity + no signing for the agent's own commit
        git(["config", "user.name", "Release Engineer"], DEST, env)
        git(["config", "user.email", "release@semver-sh.example"], DEST, env)
        head = git(["rev-parse", "HEAD"], DEST, env)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if EXPECT and head != EXPECT:
        sys.stderr.write("gen_repo: built %s, expected %s\n" % (head, EXPECT))
        sys.exit(1)
    print(head)


if __name__ == "__main__":
    main()
