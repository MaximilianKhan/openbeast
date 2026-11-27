#!/bin/bash
# Make a Basalt serve tree with OpenBeast's local patch for upstream #588 (PR #632), offline.
#
#   scripts/engines/basalt-apply-pr632.sh SRC_TREE DST_TREE
#
# SRC_TREE: the built Basalt v0.5.7 tree (a git checkout at 400b411c with bin/ from the offline build), e.g.
#           ~/openbeast-runs/inkling-perf-2026-11-23/build/basalt-tree. It is read, never changed.
# DST_TREE: a new directory (must not exist): a copy of SRC_TREE with the patch applied, e.g. .../build/basalt-tree-pr632.
#
# The patch touches only the Python server (serve/), so the engine binaries are copied as they are and must hash the
# same as in SRC_TREE (checked). No network: the patch file is in this repo
# (scripts/engines/patches/basalt-pr632-55f9be17.patch). Review and reasons: docs/reports/STRATA_INKLING_5090-2026-11-23.md
# section 16; DECISIONS-2026-11-24.md section 6. Writes DST_TREE/OPENBEAST-PATCH.txt (provenance and file hashes).
set -euo pipefail
PIN=400b411c58031d064812ea21a259b2381acdaa3e
HEAD_SHA=55f9be172f247e50b623295662e2a5babbfbeeaa
BODY_SHA256=af15ee07ecddababe54b57ae68b78b0d526602156332333ae50ba3f36748c0cd
HERE=$(cd "$(dirname "$0")" && pwd)
PATCH=$HERE/patches/basalt-pr632-55f9be17.patch
# The four files' blobs at the PR head (git rev-parse 55f9be17:<file>); the patched copy must match them exactly.
declare -A WANT=(
  [serve/models/__init__.py]=cbb44d7b2f611fd750fb68974df5d0bc526f131e
  [serve/models/inkling2.py]=ed946aab43f1d2b910a69f6fce37e9702abcec24
  [serve/service.py]=77f2753016a5a95bd4060970b1f016a99603cad6
  [serve/tests/test_inkling2_dialect.py]=528caf51e6d41ab4a5b01c06958264be4633322e
)
SRC=${1:?usage: $0 SRC_TREE DST_TREE}; DST=${2:?usage: $0 SRC_TREE DST_TREE}
die() { echo "basalt-apply-pr632: $*" >&2; exit 1; }
# The patch file first (it is checked whatever the trees are), then the trees.
got=$(sed -n '/^diff --git/,$p' "$PATCH" | sha256sum | cut -d' ' -f1)
[[ $got == "$BODY_SHA256" ]] || die "patch body sha256 $got, want $BODY_SHA256"
[[ -e $DST ]] && die "$DST exists; pick a new directory"
[[ $(git -C "$SRC" rev-parse HEAD) == "$PIN" ]] || die "$SRC is not at v0.5.7 ($PIN)"
[[ -z $(git -C "$SRC" status --porcelain --untracked-files=no) ]] || die "$SRC has tracked changes"
cp -a "$SRC" "$DST"
git -C "$DST" apply --check "$PATCH"
git -C "$DST" apply "$PATCH"
for f in "${!WANT[@]}"; do
  b=$(git -C "$DST" hash-object "$f")
  [[ $b == "${WANT[$f]}" ]] || die "$f is blob $b after the patch, want ${WANT[$f]} (PR head)"
done
changed=$(git -C "$DST" status --porcelain --untracked-files=no | awk '{print $2}' | sort | tr '\n' ' ')
[[ $changed == "serve/models/__init__.py serve/models/inkling2.py serve/service.py serve/tests/test_inkling2_dialect.py " ]] \
  || die "unexpected changed files: $changed"
for b in "$SRC"/bin/*; do
  [[ $(sha256sum < "$b") == $(sha256sum < "$DST/bin/$(basename "$b")") ]] || die "bin/$(basename "$b") differs from $SRC"
done
{
  echo "Basalt v0.5.7 ($PIN) + OpenBeast local patch: upstream PR #632 head $HEAD_SHA (fix for #588)"
  echo "patch: $PATCH (body sha256 $BODY_SHA256)"
  echo "made: $(date -u +%FT%TZ) from $SRC"
  echo "patched files (sha256):"
  (cd "$DST" && sha256sum serve/models/__init__.py serve/models/inkling2.py serve/service.py serve/tests/test_inkling2_dialect.py)
  echo "engine binaries (sha256, unchanged from $SRC):"
  (cd "$DST" && sha256sum bin/*)
} > "$DST/OPENBEAST-PATCH.txt"
cat "$DST/OPENBEAST-PATCH.txt"
