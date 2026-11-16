#!/usr/bin/env bash
# WRONG (semantics): right commit, right hunk, but reverses the hunk verbatim.
# That restores `c=$(_sv_numcmp ...)`, and since the same commit _sv_numcmp
# sets _SV_R instead of printing, so c is empty and compare goes wrong.
set -eu
bad=$(git log --format=%H -1 --grep='^perf: avoid command substitution')
git show -U0 --format= "$bad" -- semver.sh > ../culprit.diff
python3 - ../culprit.diff <<'EOF'
import re, sys
text = open(sys.argv[1]).read()
head, _, body = text.partition("\n@@ ")
hunks = ["@@ " + h for h in ("@@ " + body)[3:].split("\n@@ ")]
keep = [h for h in hunks if "if [[ $x < $y ]]; then _SV_R=-1" in h]
open(sys.argv[1], "w").write(head + "\n" + keep[0].rstrip("\n") + "\n")
EOF
git apply -R --unidiff-zero ../culprit.diff
rm -f ../culprit.diff
git commit -q -am "Revert the faulty hunk of $bad"
