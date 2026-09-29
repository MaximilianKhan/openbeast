#!/usr/bin/env bash
# Python dependency provenance: the hash-pinned lock, and the wheelhouse that
# makes a closed-network install possible.
#
#   ./scripts/pydeps.sh lock                    regenerate agents/requirements.lock
#   ./scripts/pydeps.sh verify                  offline: is the lock sane and current
#   ./scripts/pydeps.sh check                   online: re-resolve, is the lock STALE
#   ./scripts/pydeps.sh wheelhouse <dir>        fill a dir with the exact artifacts
#   ./scripts/pydeps.sh audit <dir>             every file in <dir> must be in the lock
#   ./scripts/pydeps.sh install [--from <dir> [--lock-sha256 <hex>]]
#                                               install with --require-hashes
#                                               (exit 3 = HASH MISMATCH: never
#                                               fall back from that)
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
# every artifact is verified against the lock at both ends. The LOCK itself
# never travels with the wheels: `install --from` accepts only the lock
# committed in this checkout, or one whose sha256 the operator (or a signed
# bundle manifest) vouches for with --lock-sha256.
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
# Captured BEFORE the cd: a <dir> argument is relative to where the operator
# is standing. Resolved after it, `/path/openbeast/scripts/pydeps.sh wheelhouse
# ./wheels` run from /media/usb filled $REPO_DIR/wheels instead of the stick,
# and `install --from ./wheels` looked for the wheelhouse inside the repo.
ORIG_PWD="$PWD"
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

_from_caller() {                 # a relative path is relative to the CALLER
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *)  printf '%s\n' "$ORIG_PWD/$1" ;;
  esac
}

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
    DIR="$(_from_caller "${1:-wheels}")"; shift || true
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
    _lock_sha="$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$LOCK")"
    _commit="$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo "not a git checkout")"
    cat <<EOF

$DIR holds $_n file(s), every one of them named by $LOCK.
Copy the DIRECTORY to the closed box — NOT the lock. The lock is what every
wheel is checked against, so it must not travel on the same stick: the
closed box uses the lock in its own checkout of the same commit.

  lock sha256  $_lock_sha
  commit       $_commit

Then, on the closed box (a git checkout at that commit needs nothing more;
any other checkout passes the hash above, read off THIS screen):

  ./scripts/pydeps.sh install --from $DIR [--lock-sha256 $_lock_sha]

For a DIFFERENT target than this box, add pip's platform flags, e.g.
  ./scripts/pydeps.sh wheelhouse wheels-mac \\
      --only-binary :all: --platform macosx_11_0_arm64 --python-version 3.12
(macOS x86_64 cannot be covered by wheels alone — see the header.)
EOF
    ;;

  audit)
    DIR="$(_from_caller "${1:-wheels}")"
    [[ -d "$DIR" ]] || die "$DIR is not a directory"
    [[ -f "$LOCK" ]] || die "$LOCK does not exist"
    "$PY" "$HELPER" audit --lock "$LOCK" --dir "$DIR"
    ;;

  install)
    # EXIT STATUS: 0 installed; 3 = pip reported a HASH MISMATCH (the one
    # failure a caller must NEVER answer by falling back to an unpinned
    # install — the index served bytes the lock does not pin); any other
    # non-zero = the lock could not be used here (stale, incomplete for this
    # python, no network…), which a caller MAY degrade from, loudly.
    FROM=""; LOCK_SHA="${OPENBEAST_LOCK_SHA256:-}"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        # A bare or empty --from must not fall through to the INDEX install
        # below: the operator asked for "no index contacted".
        --from) [[ $# -ge 2 && -n "$2" ]] || die "--from needs a wheelhouse directory"
                FROM="$(_from_caller "$2")"; shift 2 ;;
        --lock-sha256)
                [[ $# -ge 2 && -n "$2" ]] || die "--lock-sha256 needs the lock's sha256"
                LOCK_SHA="$2"; shift 2 ;;
        *) break ;;
      esac
    done
    [[ -f "$LOCK" ]] || die "$LOCK does not exist"
    # WHO VOUCHES FOR THE LOCK, on the wheelhouse path. Everything below
    # checks wheels AGAINST the lock, so the lock is the trust root — and
    # this path used to accept whatever lock was on disk, while the printed
    # instructions said to copy it over from the same USB stick as the wheels
    # ("the stick does not have to be trusted"). A stick carrying a malicious
    # wheel AND a lock naming its hash passed verify, audit and
    # --require-hashes. So the lock must be vouched for by something that did
    # not travel with the wheels:
    #   --lock-sha256 / OPENBEAST_LOCK_SHA256  the hash as printed by
    #       `wheelhouse` on the connected box, or recorded in a SIGNED bundle
    #       manifest (bundle.sh install passes it after checking the signature)
    #   otherwise, the lock as COMMITTED in this git checkout (HEAD)
    # The index path is unaffected: the index is not the thing carrying the lock.
    if [[ -n "$FROM" ]]; then
      _have_sha="$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$LOCK")"
      if [[ -n "$LOCK_SHA" ]]; then
        [[ "$_have_sha" == "$LOCK_SHA" ]] || die "$LOCK is not the lock you vouched for.
       expected sha256 $LOCK_SHA
       this lock       $_have_sha
       Refusing to install: the lock is what every wheel is checked against."
        echo "the lock matches the sha256 it was vouched for (${LOCK_SHA:0:16}…)"
      elif git -C "$REPO_DIR" show "HEAD:./$LOCK" 2>/dev/null | cmp -s - "$LOCK"; then
        echo "the lock is the one committed at $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo HEAD)"
      else
        die "cannot vouch for $LOCK, so a wheelhouse cannot be checked against it.
       It is not the lock committed in this git checkout (modified, untracked,
       or this is not a git checkout at all), and no --lock-sha256 was given.
       A lock that travelled with the wheels would let whoever wrote the
       wheels also write the hashes they are checked against.
         - restore the committed lock:   git checkout -- $LOCK
         - or pass the hash that \`./scripts/pydeps.sh wheelhouse\` printed on
           the connected box:  ./scripts/pydeps.sh install --from <dir> --lock-sha256 <hex>
           (or OPENBEAST_LOCK_SHA256=<hex>). This lock is sha256 $_have_sha"
      fi
    fi
    # Verify before installing. The whole value of a lock is that it is
    # checked, and a stale one would install a version the repo does not
    # claim to support.
    "$PY" "$HELPER" verify --lock "$LOCK" --req "$REQ" "${EXTRA[@]}" \
      || die "the lock does not match $REQ — refusing to install from it"
    _pip_args=()
    if [[ -n "$FROM" ]]; then
      [[ -d "$FROM" ]] || die "$FROM is not a directory"
      "$PY" "$HELPER" audit --lock "$LOCK" --dir "$FROM" \
        || die "$FROM contains files the lock does not name — refusing to install"
      echo "installing from $FROM (no index will be contacted)"
      _pip_args=(--no-index --find-links "$FROM")
    else
      echo "installing from the index, hash-checked against $LOCK"
    fi
    # pip's stderr is KEPT and passed on, because what it says decides the
    # exit status: pip's words for tampering (pip/_internal/exceptions.py,
    # HashMismatch) are the banner and "Expected sha256 … Got …". NOT
    # tampering: "all requirements must have their versions pinned" / "Hashes
    # are required" — the closure on THIS python needs a package the lock
    # does not name, and no bytes were compared at all.
    _err="$(mktemp)"
    _rc=0
    # shellcheck disable=SC2046  # flags are intentionally word-split
    "$PY" -m pip install $(_pip_flags) --require-hashes \
      ${_pip_args[@]+"${_pip_args[@]}"} -r "$LOCK" "$@" 2>"$_err" || _rc=$?
    cat "$_err" >&2
    if [[ $_rc -ne 0 ]] && grep -qE 'DO NOT MATCH THE HASHES|^[[:space:]]*Expected sha(256|384|512) |hash mismatch' "$_err"; then
      rm -f "$_err"
      echo "error: HASH MISMATCH — pip was served bytes that are NOT the ones $LOCK pins.
       Do not fall back to an unpinned install: it would fetch the same names,
       unverified, from the same source. Suspect a mirror or proxy first
       (pip config list, PIP_INDEX_URL)." >&2
      exit 3
    fi
    rm -f "$_err"
    exit "$_rc"
    ;;

  -h|--help|help)
    sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
    ;;

  *)
    die "unknown command '$CMD' — lock | verify | check | wheelhouse | audit | install"
    ;;
esac
