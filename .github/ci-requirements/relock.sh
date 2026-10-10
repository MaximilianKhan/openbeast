#!/usr/bin/env bash
# Regenerate CI's own hash-pinned tool requirements (test.txt, lint.txt,
# audit.txt) from the exact versions in the matching *.in files.
#
#   ./.github/ci-requirements/relock.sh            # all three
#   ./.github/ci-requirements/relock.sh lint       # one
#
# WHY. The workflows installed pytest, ruff and pip-audit with a bare
# `pip install <name>`: whatever PyPI served that minute ran in every job,
# main included. These files pin each tool AND its closure to sha256s, and the
# jobs install them with --require-hashes.
#
# HOW. The same builder as agents/requirements.lock (scripts/lib/
# pydeps_lock.py): pip resolves the closure (wheels only — no build backend
# runs), then every file PyPI publishes for each pinned release is recorded,
# so the file installs on any runner image.
# ONLINE: needs the index and pypi.org's JSON API.
#
# IT MUST RESOLVE ON CI's PYTHON (3.12), not merely "for" it: pip evaluates
# environment markers on the interpreter it runs under, whatever
# --python-version says. Resolved on 3.14, audit.txt came out one package
# short (typing_extensions, needed below 3.13) and a 3.12 install refused it.
# Set OPENBEAST_CI_PYTHON to a 3.12 interpreter if `python3.12` is not on PATH.
#
# test.txt IS SPECIAL: the test jobs install agents/requirements.lock first,
# and pytest shares one dependency with it (packaging). A second pin of the
# same package would have to be kept in step by hand, so any package the
# agents lock already pins is LEFT OUT of test.txt, and the job installs it
# with --no-deps. tests/test_ci_workflow.py checks the two never overlap.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
PY="${OPENBEAST_PYTHON:-python3}"
CI_PY="${OPENBEAST_CI_PYTHON:-python3.12}"
_v="$("$CI_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
[[ "$_v" == "3.12" ]] || {
  echo "error: '$CI_PY' is python '${_v:-not found}', and these files must be resolved on 3.12 (CI's)." >&2
  echo "       Point OPENBEAST_CI_PYTHON at a python3.12 and re-run." >&2
  exit 1
}

[[ $# -gt 0 ]] || set -- test lint audit
for name in "$@"; do
  [[ -f "$HERE/$name.in" ]] || { echo "error: no $HERE/$name.in" >&2; exit 1; }
  tmp="$(mktemp)"
  "$PY" "$REPO_DIR/scripts/lib/pydeps_lock.py" build --lock "$tmp" --req "$HERE/$name.in" \
    --pip "$CI_PY -m pip" >/dev/null
  "$PY" - "$tmp" "$HERE/$name.txt" "$name" "$REPO_DIR/agents/requirements.lock" <<'PY'
import re, sys
src, dst, name, agents_lock = sys.argv[1:5]
norm = lambda s: re.sub(r"[-_.]+", "-", s).lower()
blocks, cur = [], None
for line in open(src, encoding="utf-8"):
    if line.startswith("#") or not line.strip():
        continue
    if not line.startswith(" "):
        cur = [line]
        blocks.append(cur)
    else:
        cur.append(line)
drop = set()
if name == "test":
    drop = {norm(m.group(1)) for m in
            re.finditer(r"(?m)^([A-Za-z0-9._-]+)==", open(agents_lock, encoding="utf-8").read())}
kept = [b for b in blocks if norm(b[0].split("==")[0]) not in drop]
left = sorted(b[0].split()[0] for b in blocks if b not in kept)
head = [f"# GENERATED — do not edit. Regenerate: ./.github/ci-requirements/relock.sh {name}",
        f"# The hash-pinned closure of .github/ci-requirements/{name}.in, resolved",
        "# on CI's python (3.12). Every file of each release is listed, so it installs on",
        "# any runner image; pip --require-hashes refuses any other content."]
if name == "test":
    head += ["#", "# Install AFTER agents/requirements.lock, with --no-deps: packages that lock",
             "# already pins are left out here (one pin per package): " + (", ".join(left) or "none")]
with open(dst, "w", encoding="utf-8") as fh:
    fh.write("\n".join(head) + "\n\n" + "".join("".join(b) for b in kept))
print(f"wrote {dst}: {len(kept)} package(s)" + (f", {len(left)} left to the agents lock" if left else ""))
PY
  rm -f "$tmp"
done
