#!/usr/bin/env bash
# Python dependency provenance: the hash-pinned lock, and the wheelhouse that
# makes a closed-network install possible.
#
#   ./scripts/pydeps.sh lock                    regenerate agents/requirements.lock
#   ./scripts/pydeps.sh verify                  offline: is the lock sane and current
#   ./scripts/pydeps.sh check                   online: re-resolve, is the lock STALE
#   ./scripts/pydeps.sh wheelhouse <dir>        fill a dir with the exact artifacts
#   ./scripts/pydeps.sh audit <dir>             every file in <dir> must be in the lock
#   ./scripts/pydeps.sh install [--from <dir>]  install with --require-hashes
#
# WHY. agents/requirements.txt pins 6 direct versions with `==`. That says
# nothing about the other 37 packages that actually get installed, and it pins
# no CONTENT: `openai==3.9.0` accepts whatever bytes an index serves under
# that name. The lock pins the whole closure to sha256s, so pip refuses
# anything else — and it is what lets a USB wheelhouse be trusted on a box
# that can never reach PyPI to check.
#
# THE AIR-GAP CASE IS THE POINT (docs/TODO.md, closed-network review). An
# installed rig serves fine offline; INSTALLING is what breaks. `wheelhouse`
# on a connected box plus `install --from` on the closed one is that path, and
# every artifact is verified against the lock at both ends.
#
# PLATFORM COVERAGE, measured not assumed:
#   linux x86_64, python 3.12 and 3.14   all 43 packages  ✓
#   macOS arm64 (Apple Silicon)          all 43 packages  ✓
#   macOS x86_64 (Intel)                 cffi 2.1.1 ships no wheel for it, so
#                                        pip must build it from the sdist
#                                        (which the lock also pins) and that
#                                        needs a compiler. Not a lock defect;
#                                        stated so nobody debugs it twice.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

LOCK="agents/requirements.lock"
REQ="agents/requirements.txt"
HELPER="scripts/lib/pydeps_lock.py"
# huggingface_hub is installed by bootstrap BY NAME and is deliberately not in
# requirements.txt (the `hf` CLI moved between majors). The lock must still
# pin it, or a locked install would be missing the one tool that fetches
# weights — so it is passed as an explicit extra everywhere.
EXTRA=(--extra huggingface_hub)

PY="${OPENBEAST_PYTHON:-python3}"

die() { echo "error: $*" >&2; exit 1; }

_pip_flags() {
  # INSIDE A VENV, --user is not merely unnecessary — pip REFUSES it ("User
  # site-packages are not visible in this virtualenv"), and a venv is exactly
  # where scripts/setup-client.sh installs. So the venv case comes first.
  if "$PY" -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)' 2>/dev/null; then
    echo ""                      # a venv IS the isolation; nothing to add
    return
  fi
  # PEP-668. The EXACT test bootstrap.sh uses (bootstrap.sh:389), not a
  # lookalike: two different answers to "is this python externally managed"
  # is how one installer works and the other does not on the same box.
  # --break-system-packages with --user still only touches ~/.local.
  if "$PY" -c 'import sysconfig,os;p=sysconfig.get_path("stdlib");exit(0 if os.path.exists(os.path.join(p,"EXTERNALLY-MANAGED")) else 1)' 2>/dev/null; then
    echo "--user --break-system-packages"
  else
    echo "--user"
  fi
}

CMD="${1:-verify}"
shift || true

case "$CMD" in
  lock)
    [[ -f "$HELPER" ]] || die "$HELPER is missing"
    "$PY" "$HELPER" build --lock "$LOCK" --req "$REQ" "${EXTRA[@]}" "$@"
    # A lock we cannot verify immediately after writing is not a lock.
    "$PY" "$HELPER" verify --lock "$LOCK" --req "$REQ" "${EXTRA[@]}"
    ;;

  verify)
    [[ -f "$LOCK" ]] || die "$LOCK does not exist — ./scripts/pydeps.sh lock"
    "$PY" "$HELPER" verify --lock "$LOCK" --req "$REQ" "${EXTRA[@]}"
    ;;

  check)
    # ONLINE. `verify` proves the lock is self-consistent and covers every
    # direct pin; only a resolver can prove it is not missing a package that
    # a bumped dependency now needs. Re-resolve into a temp file and diff the
    # pins — the hashes are expected to be identical, so any difference is a
    # stale lock, not noise.
    [[ -f "$LOCK" ]] || die "$LOCK does not exist"
    _tmp="$(mktemp -d)"
    trap 'rm -rf "$_tmp"' EXIT
    if ! "$PY" "$HELPER" build --lock "$_tmp/fresh.lock" --req "$REQ" "${EXTRA[@]}" >/dev/null; then
      die "could not re-resolve (network? index?) — the committed lock is unchanged"
    fi
    # Compare the PINS, not the comment header: the header records the
    # resolver's own version, which moves for reasons that are not a stale
    # lock and would make this check cry wolf on every pip upgrade.
    _strip() { grep -vE '^\s*#' "$1" | grep -vE '^\s*$'; }
    if diff -u <(_strip "$LOCK") <(_strip "$_tmp/fresh.lock") > "$_tmp/diff"; then
      echo "$LOCK: CURRENT — a fresh resolve produces the same pins and hashes"
    else
      echo "$LOCK: STALE — a fresh resolve differs:"
      sed -n '1,60p' "$_tmp/diff"
      echo
      echo "regenerate with: ./scripts/pydeps.sh lock"
      exit 1
    fi
    ;;

  wheelhouse)
    DIR="${1:-wheels}"; shift || true
    [[ -f "$LOCK" ]] || die "$LOCK does not exist — ./scripts/pydeps.sh lock"
    mkdir -p "$DIR"
    # --require-hashes on the DOWNLOAD, so the wheelhouse is verified as it is
    # built rather than trusted and audited later. `audit` still exists,
    # because a directory that travels on a USB stick can change after it was
    # filled.
    echo "filling $DIR from $LOCK (hash-checked on arrival)..."
    "$PY" -m pip download --require-hashes -r "$LOCK" -d "$DIR" "$@" \
      || die "download failed — nothing in $DIR should be trusted; see the error above"
    "$PY" "$HELPER" audit --lock "$LOCK" --dir "$DIR"
    _n=$(find "$DIR" -maxdepth 1 -type f | wc -l)
    cat <<EOF

$DIR holds $_n file(s), every one of them named by $LOCK.
Copy the directory AND $LOCK to the closed box, then:

  ./scripts/pydeps.sh install --from $DIR

For a DIFFERENT target than this box, add pip's platform flags, e.g.
  ./scripts/pydeps.sh wheelhouse wheels-mac \\
      --only-binary :all: --platform macosx_11_0_arm64 --python-version 3.12
(macOS x86_64 cannot be covered by wheels alone — see the header.)
EOF
    ;;

  audit)
    DIR="${1:-wheels}"
    [[ -d "$DIR" ]] || die "$DIR is not a directory"
    [[ -f "$LOCK" ]] || die "$LOCK does not exist"
    "$PY" "$HELPER" audit --lock "$LOCK" --dir "$DIR"
    ;;

  install)
    FROM=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --from) FROM="${2:-}"; shift 2 ;;
        *) break ;;
      esac
    done
    [[ -f "$LOCK" ]] || die "$LOCK does not exist"
    # Verify before installing. The whole value of a lock is that it is
    # checked, and a stale one would install a version the repo does not
    # claim to support.
    "$PY" "$HELPER" verify --lock "$LOCK" --req "$REQ" "${EXTRA[@]}" \
      || die "the lock does not match $REQ — refusing to install from it"
    # shellcheck disable=SC2046  # flags are intentionally word-split
    if [[ -n "$FROM" ]]; then
      [[ -d "$FROM" ]] || die "$FROM is not a directory"
      "$PY" "$HELPER" audit --lock "$LOCK" --dir "$FROM" \
        || die "$FROM contains files the lock does not name — refusing to install"
      echo "installing from $FROM (no index will be contacted)"
      "$PY" -m pip install $(_pip_flags) --require-hashes \
        --no-index --find-links "$FROM" -r "$LOCK" "$@"
    else
      echo "installing from the index, hash-checked against $LOCK"
      "$PY" -m pip install $(_pip_flags) --require-hashes -r "$LOCK" "$@"
    fi
    ;;

  -h|--help|help)
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
    ;;

  *)
    die "unknown command '$CMD' — lock | verify | check | wheelhouse | audit | install"
    ;;
esac
