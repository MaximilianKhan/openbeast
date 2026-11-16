# Shared by the wrong/slow solution scripts: rewrite the culprit block of
# ./semver.sh in one of several ways.
#   python3 _fix.py MODE      MODE: numcmp (correct) | lenonly | arith | hang
import sys

mode = sys.argv[1]
OLD = ('            if [[ $x < $y ]]; then _SV_R=-1; return 0; fi\n'
       '            if [[ $x > $y ]]; then _SV_R=1; return 0; fi\n')
NEW = {
    "numcmp": '            _sv_numcmp "$x" "$y"\n'
              '            if (( _SV_R != 0 )); then return 0; fi\n',
    # half fix: orders by digit count only, so equal-length numbers tie
    "lenonly": '            if (( ${#x} < ${#y} )); then _SV_R=-1; return 0; fi\n'
               '            if (( ${#x} > ${#y} )); then _SV_R=1; return 0; fi\n',
    # 64-bit shell arithmetic: wrong once identifiers pass 19 digits
    "arith": '            if (( 10#$x < 10#$y )); then _SV_R=-1; return 0; fi\n'
             '            if (( 10#$x > 10#$y )); then _SV_R=1; return 0; fi\n',
    "hang": '            while :; do :; done\n',
}[mode]
with open("semver.sh") as f:
    src = f.read()
assert src.count(OLD) == 1, "culprit block not found"
with open("semver.sh", "w") as f:
    f.write(src.replace(OLD, NEW))
