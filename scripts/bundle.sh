#!/usr/bin/env bash
# The offline bundle: build it connected, install it from a USB stick.
#
#   ./scripts/bundle.sh build <dir> [--with-weights[=A,B]] [--no-images]
#   ./scripts/bundle.sh show <dir>        what is in it
#   ./scripts/bundle.sh verify <dir>      re-hash everything it claims
#   ./scripts/bundle.sh install <dir> [--key <allowed-signers>]
#   ./scripts/bundle.sh sign   <dir> --key <ssh-private-key> [--identity ID]
#   ./scripts/bundle.sh verify <dir> [--key <allowed-signers>] [--identity ID]
#
# WHAT IT SOLVES. The closed-network review found exactly four fetches a first
# install cannot do offline: llama.cpp source, the python wheels, the ~20 GB
# weight, and the two container images. `OFFLINE=true` makes the installer
# REFUSE those steps cleanly instead of stalling; this script is how you
# satisfy them.
#
# NOTHING HERE IS TRUSTED BECAUSE IT ARRIVED. Every file is recorded with its
# sha256 in MANIFEST.json, `verify` re-hashes before anything is used, and
# `install` refuses on a mismatch — the same rule as the python lock and
# fetch-weight.sh, which deletes a weight whose hash is wrong rather than
# leaving it to be used by accident.
#
# THE DIGEST TRAP, AND THE WAY OUT. docker-compose.yml pins images by registry
# MANIFEST digest (`repo@sha256:…`). `docker save`/`load` does not carry that
# digest — a loaded image has no RepoDigest at all — so a digest-pinned
# reference can NEVER be satisfied from a tarball. That is the trap the
# air-gap ranker caught, and it is why "just docker save/load" does not work.
#
# The answer is not to give up content addressing. An image's ID is a content
# digest, and `docker compose` accepts `image: sha256:<id>` and resolves it
# locally with no pull — measured on 2026-09-15, not assumed. So: the manifest
# records the ID, install checks the loaded image against it, and the compose
# reference is rewritten to the ID the load ACTUALLY produced. The original
# docker-compose.yml is kept, so the rewrite is reversible.
#
# WHICH digest the ID is DEPENDS ON THE IMAGE STORE — an earlier version of
# this header said "of its config" as if that were universal, and it is not:
#   classic store (overlay2)         .Id = the image CONFIG digest
#   containerd snapshotter           .Id = the registry INDEX/manifest digest
#     (the default on fresh Docker 29 installs, and what the reference box runs)
# Same image, two different IDs. A bundle built on one kind and installed on
# the other loaded fine and then died "image … is not present afterwards",
# blaming docker's load for what was a comparison between two different kinds
# of digest. So `build` records the store kind, and `install` compares IDs
# only when the kinds match. When they differ it says the ID check was
# skipped and why: the tarball's sha256 was already verified against the
# (signed) manifest, so integrity is not what is lost — only a redundant
# cross-check is.
#
# HASHES ARE INTEGRITY; A SIGNATURE IS AUTHENTICITY, and they answer different
# questions. MANIFEST.json proves the bundle did not change in transit. It
# proves nothing about WHO built it: anyone who can write to the stick can
# rebuild the manifest to match their own payload, and every hash would then
# verify perfectly. `sign` closes that, and `verify --key` is what makes it
# mean anything.
#
# ssh-keygen -Y, deliberately: it uses keys an operator already has, needs no
# CA and no PKI decision, and the allowed-signers file is the same format
# ssh/git already use. NO KEY MATERIAL LIVES IN THIS REPO — the operator
# supplies both halves. An unsigned bundle is not refused (integrity alone is
# still useful on a stick you carried yourself), but `verify` and `install`
# SAY it is unsigned rather than letting silence imply trust.
#
# WHICH COMPONENTS HAVE A SECOND LINE OF DEFENCE, measured by rebuilding the
# manifest around a malicious payload and watching what still caught it:
#   wheels   TWO independent checks. agents/requirements.lock lives in the
#            REPO, not the bundle, so an attacker who rewrites the manifest
#            cannot rewrite the lock — the swapped wheel's hash is in no lock
#            entry and pydeps refuses. Verified.
#   weights  TWO. scripts/weights.registry is also in the repo, and a weight
#            that does not match it is DELETED, not just rejected.
#   images   ONE. The image ID is recorded in the manifest, so a rebuilt
#            manifest can name the attacker's image and the load-time ID
#            check will agree with it.
#   source   ONE. Nothing offline can independently confirm a llama.cpp
#            tarball is the commit it claims to be.
# So the signature matters MOST for images and source. That is not a reason to
# skip it for the others — it is the reason it exists at all.
#
# WEIGHTS ARE OPT-IN. A 20 GB copy is a different operation from a 60 MB one,
# and most transfers are a rebuild of a box that already has its weight. So
# `build` skips weights unless asked, and SAYS SO in the manifest rather than
# leaving a reader to discover it at install time.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# WHERE THE OPERATOR IS STANDING, captured before the cd below. Every path
# argument used to be resolved AFTER it, so from /media/usb
#   /path/openbeast/scripts/bundle.sh install ./bundle
# looked in $REPO_DIR/bundle, and `build ./out` wrote the bundle into the repo
# instead of onto the stick it was meant for.
ORIG_PWD="$PWD"
cd "$REPO_DIR"
# shellcheck source=scripts/lib/conf.sh
source "$REPO_DIR/scripts/lib/conf.sh"
# WEIGHTS_DIR comes from the SHARED resolver (env -> openbeast.conf -> default),
# never a local guess: weights are relocatable and a second answer here would
# put a bundle's weight somewhere the serve scripts do not look.
#
# RESOLVED LAZILY, and that matters. scripts/lib/weights.sh `exit 1`s when the
# directory does not exist, and it is SOURCED — so sourcing it at the top made
# `bundle.sh show` and `bundle.sh verify` die about weights they never touch,
# on a box with no weights/ yet. That box is precisely the one this tool
# exists to set up, so the tool refused to run exactly where it was needed.
# Only the two branches that actually handle weights ask for it.
_need_weights_dir() {
  # install PLACES weights, so creating the directory is its job (the same
  # escape hatch bootstrap.sh uses). build READS them, so a missing directory
  # there is a real error and weights.sh says so.
  local mk="${1:-0}"
  # shellcheck source=scripts/lib/weights.sh
  OPENBEAST_WEIGHTS_MKDIR="$mk" source "$REPO_DIR/scripts/lib/weights.sh"
}

HELPER="$REPO_DIR/scripts/lib/bundle_manifest.py"
# The ssh-signature namespace. A signature is only valid for the namespace it
# was made in, so a signature an operator made over some other file for some
# other purpose can never be replayed as a bundle signature.
SIG_NS="openbeast-bundle"
PY="${OPENBEAST_PYTHON:-python3}"

c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_red=$'\e[31m'; c_rst=$'\e[0m'
ok()   { echo "  ${c_grn}✓${c_rst} $*"; }
warn() { echo "  ${c_ylw}!${c_rst} $*"; }
die()  { echo "  ${c_red}✗${c_rst} $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

# _from_caller <path>: a relative path means relative to where the operator
# ran the command, not to the repo this script cd'd into.
_from_caller() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *)  printf '%s\n' "$ORIG_PWD/$1" ;;
  esac
}

# _need_value <flag> <args-left> <value>: a value-taking flag with no value, or
# an EMPTY one, is an error. Two real failures hid here:
#   --key ""   KEY stayed empty, which is also the "no --key given" state, so
#              verification silently DOWNGRADED to integrity-only: a tampered
#              signed bundle verified rc=0 with a warning. An operator who
#              typed --key asked for authenticity (an unset $VAR is how the ""
#              gets there) and must never get less without an error.
#   --key      bare and last: `shift 2` fails under `set -e` and the script
#              exited 1 having printed nothing at all.
_need_value() {
  local flag="$1" left="$2" val="${3-}"
  if [[ "$left" -lt 2 || -z "$val" ]]; then
    die "$flag needs a value, and got none (an empty or unset variable?)"
  fi
}

# _image_store_kind: "containerd" | "classic" | "unknown". See the header:
# the two kinds report DIFFERENT digests as an image's .Id. The snapshotter
# announces itself in DriverStatus as `driver-type io.containerd.snapshotter.v1`
# (measured on Docker 29.7); a classic store has no such entry. The storage
# driver NAME is not the signal — `overlayfs` vs `overlay2` is one letter from
# a false answer, and a classic store can sit on btrfs or zfs.
_image_store_kind() {
  local st
  st="$(docker info --format '{{.DriverStatus}}' 2>/dev/null)" || { echo unknown; return 0; }
  if [[ "$st" == *io.containerd.snapshotter* ]]; then
    echo containerd
  else
    echo classic
  fi
}

# The image refs the stack actually runs, read from compose rather than
# restated here: a second list would drift from the first.
_compose_images() {
  grep -oE '^\s*image:\s*\S+' "$REPO_DIR/docker-compose.yml" \
    | sed -E 's/^\s*image:\s*//' | sort -u
}

# _check_signature <dir> <allowed-signers-or-empty> <identity-or-empty>
# Returns 0 when the bundle is acceptable to proceed with, and PRINTS what it
# concluded either way. Three outcomes, kept distinct on purpose:
#   signed + key given + good      -> authenticated
#   no key given                   -> integrity only, said out loud
#   key given + bad/missing sig    -> REFUSE (exit 1)
# The last one is the whole point: if an operator asked for authenticity, a
# missing signature must be a failure, not a shrug.
_check_signature() {
  # Same family as `--key ""`: an operator who names the signer they expect
  # has asked for a signature check, and without --key there is none — the
  # identity was silently ignored and the bundle "verified" on hashes alone.
  [[ -z "${3:-}" || -n "${2:-}" ]] || die "--identity needs --key <allowed-signers file>:
       without it nothing is verified and the identity would be ignored."
  local dir="$1" key="$2" ident="$3"
  local sig="$dir/MANIFEST.json.sig"
  if [[ -z "$key" ]]; then
    if [[ -f "$sig" ]]; then
      warn "this bundle IS signed, but no --key was given, so the signature
      was not checked. Hashes prove it did not change in transit; they prove
      nothing about who built it. Pass --key <allowed-signers> to check."
    else
      warn "unsigned bundle: hashes prove it did not change in transit, but
      anyone who can write to the medium could have rebuilt the manifest to
      match their own payload. ./scripts/bundle.sh sign closes that."
    fi
    return 0
  fi
  [[ -f "$key" ]] || die "allowed-signers file $key does not exist"
  command -v ssh-keygen >/dev/null 2>&1 || die "ssh-keygen is not installed"
  [[ -f "$sig" ]] || die "you passed --key, so authenticity was REQUIRED, and
       this bundle carries no MANIFEST.json.sig. Refusing: a missing signature
       is a failure here, not a shrug."
  local out rc=0
  # ALWAYS `-Y verify`. NEVER `find-principals` alone.
  #
  # This was a FAIL-OPEN and it shipped: `ssh-keygen -Y find-principals` only
  # matches the signature's embedded public key against the allowed-signers
  # file — it does NOT check the signature over the content. Measured:
  #   tampered manifest, find-principals -> rc=0 "builder"      (accepted!)
  #   tampered manifest, verify -I builder -> rc=255 "incorrect signature"
  # So the no---identity path — the DEFAULT when an operator passes --key
  # without naming a signer — accepted a manifest modified after signing,
  # in the one feature whose entire purpose is authenticity.
  #
  # find-principals is still the right tool for DISCOVERING which principal
  # signed, when the operator did not say. It just cannot be the only step:
  # discover, then verify against what was discovered.
  if [[ -z "$ident" ]]; then
    ident="$(ssh-keygen -Y find-principals -n "$SIG_NS" -f "$key" \
               -s "$sig" < "$dir/MANIFEST.json" 2>/dev/null | head -1 || true)"
    [[ -n "$ident" ]] || die "no key in $key matches the signature on this
       bundle. Either it was signed by someone not in your allowed-signers
       file, or the signature is not a bundle signature at all."
  fi
  out="$(ssh-keygen -Y verify -n "$SIG_NS" -f "$key" -I "$ident" \
           -s "$sig" < "$dir/MANIFEST.json" 2>&1)" || rc=$?
  if [[ $rc -ne 0 ]]; then
    die "the signature on MANIFEST.json is NOT valid for $key
       ssh-keygen said: $(head -1 <<< "$out")
       Refusing to install. Either the bundle was built by someone whose key
       is not in that file, or the manifest was modified after signing."
  fi
  ok "signature verified: $(head -1 <<< "$out")"
  return 0
}

CMD="${1:-show}"
shift || true

case "$CMD" in
  build)
    DIR="${1:-}"; shift || true
    [[ -n "$DIR" ]] || die "build needs a target directory"
    WITH_WEIGHTS=""; DO_IMAGES=1; DO_SOURCE=1; FORCE=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --with-weights)   WITH_WEIGHTS="__default__"; shift ;;
        --with-weights=*) WITH_WEIGHTS="${1#*=}"
                          # `--with-weights=$UNSET` used to mean "no weights",
                          # quietly: the opposite of what was typed.
                          [[ -n "$WITH_WEIGHTS" ]] || die "--with-weights= needs a file list (or drop the = for the default weight)"
                          shift ;;
        --no-images)      DO_IMAGES=0; shift ;;
        --no-source)      DO_SOURCE=0; shift ;;
        --force)          FORCE=1; shift ;;
        *) die "unknown flag $1" ;;
      esac
    done
    ob_offline && die "OFFLINE=true — a bundle is BUILT on a connected box.
       Run this where there is a network, then carry the directory here."
    DIR="$(_from_caller "$DIR")"
    mkdir -p "$DIR"
    DIR="$(cd "$DIR" && pwd)"
    # A DIRTY TARGET IS REFUSED. Rebuilding into a directory that already
    # holds a bundle left BOTH artifacts in place — two llama.cpp tarballs,
    # for instance — and the manifest then recorded both, so `verify` passed
    # clean while `install` picked one arbitrarily, possibly the stale commit
    # the manifest does not describe. --force cleans the directories this
    # command owns (and only those).
    _dirty=()
    for _c in wheels source images weights meta; do
      [[ -d "$DIR/$_c" ]] && [[ -n "$(ls -A "$DIR/$_c" 2>/dev/null)" ]] \
        && _dirty+=("$_c")
    done
    if [[ ${#_dirty[@]} -gt 0 ]]; then
      if [[ $FORCE -eq 1 ]]; then
        for _c in "${_dirty[@]}"; do rm -rf "$DIR/$_c"; done
        rm -f "$DIR/MANIFEST.json" "$DIR/MANIFEST.json.sig"
        warn "--force: cleared ${_dirty[*]} in $DIR before rebuilding"
      else
        die "$DIR already holds bundle content (${_dirty[*]}).
       Rebuilding into it would leave BOTH sets of artifacts, and a manifest
       that records two versions of the same component verifies clean while
       install picks one arbitrarily. Use a fresh directory, or --force to
       clear ${_dirty[*]} first."
      fi
    fi
    COMPONENTS=(); METAS=(); SKIPPED=()

    step "python wheels (the hash-pinned closure)"
    "$REPO_DIR/scripts/pydeps.sh" wheelhouse "$DIR/wheels" >/dev/null \
      || die "could not fill the wheelhouse — see the error above"
    ok "wheels/ filled and hash-checked against agents/requirements.lock"
    COMPONENTS+=(--component "wheels:wheels")
    # The lock travels WITH the wheels, but NOT inside wheels/ — that
    # directory is installed from with --find-links, and pydeps audits it for
    # files the lock does not name, so a copy of the lock in there fails its
    # own audit. (It did. That is how this comment exists.)
    mkdir -p "$DIR/meta"
    cp "$REPO_DIR/agents/requirements.lock" "$DIR/meta/requirements.lock"
    COMPONENTS+=(--component "meta:meta")

    if [[ $DO_SOURCE -eq 1 ]]; then
      step "llama.cpp source"
      if [[ -d "$REPO_DIR/llama.cpp/.git" ]]; then
        mkdir -p "$DIR/source"
        _sha="$(git -C "$REPO_DIR/llama.cpp" rev-parse HEAD)"
        # git archive, not a copy of the worktree: it is reproducible, it
        # excludes build/ (multi-GB of objects the target rebuilds anyway),
        # and it records exactly which commit was shipped.
        git -C "$REPO_DIR/llama.cpp" archive --format=tar "HEAD" \
          | gzip -n > "$DIR/source/llama.cpp-${_sha:0:12}.tar.gz"
        ok "source/llama.cpp-${_sha:0:12}.tar.gz (commit $_sha)"
        COMPONENTS+=(--component "source:source")
        METAS+=(--meta "source:$(printf '{"commit": "%s"}' "$_sha")")
      else
        warn "no llama.cpp/.git here — skipping the source component"
        SKIPPED+=(--skipped "llama.cpp source (no git clone on the build box)")
      fi
    else
      SKIPPED+=(--skipped "llama.cpp source (--no-source)")
    fi

    if [[ $DO_IMAGES -eq 1 ]]; then
      step "container images"
      if ! command -v docker >/dev/null 2>&1; then
        warn "docker is not installed here — skipping images"
        SKIPPED+=(--skipped "container images (no docker on the build box)")
      else
        mkdir -p "$DIR/images"
        _img_json="[]"
        _store="$(_image_store_kind)"
        ok "image store on this box: $_store (recorded — an image ID only
      compares across stores of the same kind)"
        while IFS= read -r _ref; do
          [[ -n "$_ref" ]] || continue
          _id="$(docker inspect --format '{{.Id}}' "$_ref" 2>/dev/null || true)"
          if [[ -z "$_id" ]]; then
            warn "$_ref is not in the local image store — pull it first"
            SKIPPED+=(--skipped "image $_ref (not present on the build box)")
            continue
          fi
          _safe="$(printf '%s' "$_ref" | tr '/:@' '___')"
          docker save "$_ref" | gzip -n > "$DIR/images/${_safe}.tar.gz" \
            || die "docker save failed for $_ref"
          ok "images/${_safe}.tar.gz  ($_ref -> ${_id:0:19}…)"
          _img_json="$(PY_REF="$_ref" PY_ID="$_id" PY_FILE="images/${_safe}.tar.gz" \
                       "$PY" -c 'import json,os,sys; a=json.loads(sys.argv[1]); a.append({"ref": os.environ["PY_REF"], "id": os.environ["PY_ID"], "file": os.environ["PY_FILE"]}); print(json.dumps(a))' "$_img_json")"
        done < <(_compose_images)
        if [[ "$_img_json" == "[]" ]]; then
          rmdir "$DIR/images" 2>/dev/null || true
        else
          COMPONENTS+=(--component "images:images")
          METAS+=(--meta "images:$(printf '{"images": %s, "image_store": "%s"}' "$_img_json" "$_store")")
        fi
      fi
    else
      SKIPPED+=(--skipped "container images (--no-images)")
    fi

    if [[ -n "$WITH_WEIGHTS" ]]; then
      step "weights"
      _need_weights_dir 0
      mkdir -p "$DIR/weights"
      _w_json="[]"
      # Which files: the named ones, or whatever the configured serve script
      # actually loads. Never "everything in weights/" — that is how a 400 GB
      # bundle happens by accident.
      if [[ "$WITH_WEIGHTS" == "__default__" ]]; then
        # NON-COMMENT LINES ONLY: serve scripts mention alternative and draft
        # models in comments, and scraping those shipped files nobody asked
        # for while the real one might be missing.
        _names="$(grep -vE '^[[:space:]]*#' "$REPO_DIR/scripts/$DEFAULT_SERVE_SCRIPT" 2>/dev/null \
                  | grep -oE '[A-Za-z0-9._-]+\.gguf' | sort -u || true)"
        [[ -n "$_names" ]] || die "could not tell which weight $DEFAULT_SERVE_SCRIPT loads — pass --with-weights=<file.gguf>"
      else
        _names="$(printf '%s' "$WITH_WEIGHTS" | tr ',' '\n')"
      fi
      # SHARDED WEIGHTS TRAVEL AS A SET. llama.cpp is given only the FIRST
      # shard on its command line and finds the siblings itself, so a serve
      # script names one file while the model is three — and this shipped
      # 1 of 3 with nothing saying so, producing a bundle that cannot load
      # the model it claims to carry. scripts/weights.registry has a real
      # 3-shard entry today.
      _expanded=""
      while IFS= read -r _nm; do
        [[ -n "$_nm" ]] || continue
        _expanded="$_expanded$_nm"$'\n'
        if [[ "$_nm" =~ ^(.*)-([0-9]{5})-of-([0-9]{5})\.gguf$ ]]; then
          _stem="${BASH_REMATCH[1]}"; _tot="${BASH_REMATCH[3]}"
          for _i in $(seq 1 "$((10#$_tot))"); do
            _expanded="$_expanded$(printf '%s-%05d-of-%s.gguf' "$_stem" "$_i" "$_tot")"$'\n'
          done
          warn "$_nm is shard $(printf '%d' "$((10#${BASH_REMATCH[2]}))") of $((10#$_tot)) — including the whole set"
        fi
      done <<< "$_names"
      _names="$(printf '%s' "$_expanded" | sort -u | sed '/^$/d')"
      while IFS= read -r _wf; do
        [[ -n "$_wf" ]] || continue
        _src="$WEIGHTS_DIR/$_wf"
        if [[ ! -f "$_src" ]]; then
          # A MISSING SHARD IS FATAL. Skipping one silently produces a bundle
          # that verifies perfectly and cannot load the model.
          if [[ "$_wf" =~ -[0-9]{5}-of-[0-9]{5}\.gguf$ ]]; then
            die "$_wf is part of a sharded weight and is not at $_src.
       A partial shard set cannot load, so this bundle would verify clean and
       be useless. Bring every shard, or pass --with-weights= without it."
          fi
          warn "$_wf not found at $_src — skipping"
          SKIPPED+=(--skipped "weight $_wf (not on the build box)")
          continue
        fi
        _reg="$(awk -F'\t' -v f="$_wf" '$3 == f {print $1}' "$REPO_DIR/scripts/weights.registry" 2>/dev/null || true)"
        echo "  copying $_wf ($(du -h "$_src" | cut -f1))..."
        cp "$_src" "$DIR/weights/$_wf"
        ok "weights/$_wf"
        _w_json="$(PY_F="$_wf" PY_REG="$_reg" "$PY" -c 'import json,os,sys; a=json.loads(sys.argv[1]); a.append({"file": os.environ["PY_F"], "registry_sha256": os.environ["PY_REG"]}); print(json.dumps(a))' "$_w_json")"
      done <<< "$_names"
      if [[ "$_w_json" == "[]" ]]; then
        rmdir "$DIR/weights" 2>/dev/null || true
      else
        COMPONENTS+=(--component "weights:weights")
        METAS+=(--meta "weights:$(printf '{"weights": %s}' "$_w_json")")
      fi
    else
      SKIPPED+=(--skipped "weights (not requested — pass --with-weights; a rebuild of a box that already has its weight does not need them)")
    fi

    step "manifest"
    # MANIFEST.json.sig is deliberately NOT recorded in the manifest: it is
    # made AFTER the manifest exists, and a manifest that claimed a hash for
    # its own signature could never be satisfied.
    rm -f "$DIR/MANIFEST.json.sig"
    _era="$(bash "$REPO_DIR/scripts/eval-era.sh" 2>/dev/null | grep -oE '[0-9a-f]{16}' | head -1 || true)"
    "$PY" "$HELPER" write "$DIR" \
      --built-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --built-on "$(uname -s) $(uname -m)" \
      --repo-commit "$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo unknown)" \
      --eval-era "${_era:-unknown}" \
      "${COMPONENTS[@]}" ${METAS[@]+"${METAS[@]}"} ${SKIPPED[@]+"${SKIPPED[@]}"} \
      || die "could not write the manifest"
    echo
    "$PY" "$HELPER" show "$DIR"
    cat <<EOF

Carry $DIR to the closed box (it may sit anywhere), then there:

  ./scripts/bundle.sh verify  /path/to/bundle     # re-hash everything
  ./scripts/bundle.sh install /path/to/bundle
  echo 'OFFLINE=true' >> openbeast.conf
  ./bootstrap.sh

EOF
    ;;

  sign)
    DIR="${1:-}"; shift || true
    [[ -n "$DIR" ]] || die "sign needs a bundle directory"
    DIR="$(_from_caller "$DIR")"
    KEY=""; IDENT=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --key)      _need_value --key "$#" "${2-}"; KEY="$(_from_caller "$2")"; shift 2 ;;
        --identity) _need_value --identity "$#" "${2-}"; IDENT="$2"; shift 2 ;;
        *) die "unknown flag $1" ;;
      esac
    done
    [[ -n "$KEY" ]] || die "sign needs --key <ssh-private-key>.
       No key material lives in this repo: you supply both halves. Any ssh
       key works — make one with
         ssh-keygen -t ed25519 -f ~/.ssh/openbeast-bundle -C 'openbeast bundles'"
    [[ -f "$KEY" ]] || die "$KEY does not exist"
    command -v ssh-keygen >/dev/null 2>&1 || die "ssh-keygen is not installed"
    _m="$DIR/MANIFEST.json"
    [[ -f "$_m" ]] || die "$_m is missing — this is not a bundle"
    # Sign the MANIFEST, not the files: the manifest already names every file
    # by sha256, so one signature over it covers the whole bundle and stays
    # cheap on a 20 GB weight.
    ssh-keygen -Y sign -n "$SIG_NS" -f "$KEY" "$_m" >/dev/null \
      || die "ssh-keygen could not sign $_m"
    ok "wrote $(basename "$_m").sig"
    _fp="$(ssh-keygen -lf "${KEY}.pub" 2>/dev/null | awk '{print $2}' || true)"
    cat <<EOF

  Signed with ${_fp:-that key}.
  On the closed box, verification needs an allowed-signers file — the same
  format ssh and git use, one line per key:

    echo '${IDENT:-builder} \$(cat ${KEY}.pub)' > ~/.config/openbeast/allowed-signers

  then:
    ./scripts/bundle.sh verify  $DIR --key ~/.config/openbeast/allowed-signers${IDENT:+ --identity $IDENT}
    ./scripts/bundle.sh install $DIR --key ~/.config/openbeast/allowed-signers${IDENT:+ --identity $IDENT}

EOF
    ;;

  show)
    DIR="${1:-}"
    [[ -n "$DIR" ]] || die "show needs a bundle directory"
    DIR="$(_from_caller "$DIR")"
    "$PY" "$HELPER" show "$DIR"
    ;;

  verify)
    DIR="${1:-}"; shift || true
    [[ -n "$DIR" ]] || die "verify needs a bundle directory"
    DIR="$(_from_caller "$DIR")"
    KEY=""; IDENT=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --key)      _need_value --key "$#" "${2-}"; KEY="$(_from_caller "$2")"; shift 2 ;;
        --identity) _need_value --identity "$#" "${2-}"; IDENT="$2"; shift 2 ;;
        *) die "unknown flag $1" ;;
      esac
    done
    # SIGNATURE FIRST. The manifest is what names every hash, so checking the
    # hashes before checking who signed the manifest is checking a document
    # against itself.
    _check_signature "$DIR" "$KEY" "$IDENT"
    "$PY" "$HELPER" verify "$DIR"
    ;;

  install)
    DIR="${1:-}"; shift || true
    [[ -n "$DIR" ]] || die "install needs a bundle directory"
    KEY=""; IDENT=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --key)      _need_value --key "$#" "${2-}"; KEY="$(_from_caller "$2")"; shift 2 ;;
        --identity) _need_value --identity "$#" "${2-}"; IDENT="$2"; shift 2 ;;
        *) die "unknown flag $1" ;;
      esac
    done
    DIR="$(_from_caller "$DIR")"
    [[ -d "$DIR" ]] || die "$DIR is not a directory"
    DIR="$(cd "$DIR" && pwd)"
    step "verifying the bundle before using any of it"
    _check_signature "$DIR" "$KEY" "$IDENT"
    "$PY" "$HELPER" verify "$DIR" \
      || die "the bundle does not match its manifest — refusing to install it.
       A directory that travelled is not trusted because it arrived."
    ok "every recorded file matches its sha256"

    # --- source ---------------------------------------------------------
    # BY THE RECORDED COMMIT, not `head -1`. If a directory ever holds two
    # tarballs, an arbitrary pick can install a revision the manifest does
    # not describe — and the manifest is what the signature covers.
    _want_commit="$("$PY" -c '
import json, sys
doc = json.load(open(sys.argv[1] + "/MANIFEST.json"))
for c in doc.get("components", []):
    if c.get("kind") == "source" and c.get("commit"):
        print(c["commit"]); break
' "$DIR" 2>/dev/null || true)"
    _tar=""
    if [[ -n "$_want_commit" ]]; then
      _tar="$(find "$DIR/source" -maxdepth 1 \
                -name "llama.cpp-${_want_commit:0:12}.tar.gz" 2>/dev/null | head -1 || true)"
      [[ -n "$_tar" ]] || die "the manifest records llama.cpp $_want_commit but
       source/llama.cpp-${_want_commit:0:12}.tar.gz is not in the bundle."
    elif [[ -d "$DIR/source" ]]; then
      # ONLY WHEN THERE IS A source/. `find` on a missing directory exits 1,
      # pipefail makes that the pipeline's status, and `set -e` then killed
      # the script ON THE ASSIGNMENT — silently, rc=1, straight after "every
      # recorded file matches". So a bundle built with --no-source (or on a
      # box with no llama.cpp clone) could not be installed at all, and said
      # nothing about why.
      _n_tars="$(find "$DIR/source" -maxdepth 1 -name 'llama.cpp-*.tar.gz' 2>/dev/null | wc -l)"
      [[ "$_n_tars" -le 1 ]] || die "$_n_tars source tarballs and no recorded
       commit to choose between them — refusing to guess. Rebuild the bundle."
      _tar="$(find "$DIR/source" -maxdepth 1 -name 'llama.cpp-*.tar.gz' 2>/dev/null | head -1 || true)"
    fi
    if [[ -n "$_tar" ]]; then
      step "llama.cpp source"
      if [[ -e "$REPO_DIR/llama.cpp" ]]; then
        warn "llama.cpp/ already exists — left alone (delete it first to replace)"
      else
        # EXTRACT ELSEWHERE, VALIDATE, THEN MOVE. The old form created
        # llama.cpp/ and then ran an UNCHECKED tar into it, so a corrupt or
        # hostile archive left a PARTIAL tree — and every later run saw
        # "already exists — left alone" and skipped it forever. The recovery
        # path was broken by the failure it was supposed to survive.
        #
        # Measured while reviewing this: GNU tar does contain the direct
        # escapes (it strips a leading `/`, refuses `..` members, refuses to
        # write THROUGH a symlink, and exits non-zero), so nothing escaped.
        # What it does do is CREATE a symlink pointing outside the tree, and a
        # later build step following one is not something to rely on tar for.
        # A real `git archive` of llama.cpp contains ZERO symlinks (checked),
        # so refusing them costs nothing and closes that.
        _stage="$(mktemp -d "${TMPDIR:-/tmp}/ob-src-XXXXXX")"
        if ! tar -xzf "$_tar" -C "$_stage"; then
          rm -rf "$_stage"
          die "the source archive did not extract cleanly (tar reported the
       error above). NOTHING was written to llama.cpp/, so re-running after
       fixing or rebuilding the bundle will work."
        fi
        _links="$(find "$_stage" -type l -printf '%p -> %l\n' 2>/dev/null | head -5 || true)"
        if [[ -n "$_links" ]]; then
          rm -rf "$_stage"
          die "the source archive contains symlinks, which a legitimate
       llama.cpp archive does not:
$(sed 's/^/         /' <<< "$_links")
       Refusing: a planted symlink redirects whatever the build writes next."
        fi
        mkdir -p "$(dirname "$REPO_DIR/llama.cpp")"
        # One entry or many at the top of the archive — move the tree itself.
        if [[ $(find "$_stage" -mindepth 1 -maxdepth 1 | wc -l) -eq 1 \
              && -d "$(find "$_stage" -mindepth 1 -maxdepth 1)" ]]; then
          mv "$(find "$_stage" -mindepth 1 -maxdepth 1)" "$REPO_DIR/llama.cpp"
        else
          mv "$_stage" "$REPO_DIR/llama.cpp"
          _stage=""
        fi
        [[ -z "$_stage" ]] || rm -rf "$_stage"
        ok "extracted $(basename "$_tar") into llama.cpp/"
        warn "this is a SOURCE snapshot, not a git clone: scripts/update.sh
      wants a .git to pull into, so it will refuse until one exists. The
      build path (./bootstrap.sh) does not care."
      fi
    fi

    # --- python ---------------------------------------------------------
    if [[ -d "$DIR/wheels" ]]; then
      step "python wheels"
      # THE LOCK THE WHEELS WERE RESOLVED FROM must be the lock this repo
      # pins. A bundle built from a different commit carries a different
      # closure, and installing it would put versions on the box that this
      # checkout does not claim to support — silently, because every
      # individual hash would still verify.
      if [[ -f "$DIR/meta/requirements.lock" ]]; then
        if ! cmp -s "$DIR/meta/requirements.lock" "$REPO_DIR/agents/requirements.lock"; then
          # Read the commit into a variable first so a corrupt or truncated
          # manifest degrades to "?" instead of leaving a blank where the one
          # fact the reader needs to act should be. (The inline substitution
          # worked; I misread my own grep output as a bug in it.)
          _b_commit="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]+"/MANIFEST.json")).get("repo_commit","?"))' "$DIR" 2>/dev/null || echo "?")"
          die "the bundle's lock differs from agents/requirements.lock here.
       The bundle was built at repo commit $_b_commit
       and this checkout pins something else. Installing anyway would put
       versions on this box that this checkout does not claim to support —
       every hash would still verify, which is exactly why it would be quiet.
       Check out the matching commit, or rebuild the bundle from this one."
        fi
        ok "the bundle's lock matches this checkout's"
      else
        warn "the bundle carries no copy of the lock — cannot confirm the
      wheels were resolved from the lock this checkout pins"
      fi
      "$REPO_DIR/scripts/pydeps.sh" install --from "$DIR/wheels" \
        || die "the wheelhouse did not satisfy agents/requirements.lock"
      ok "installed the hash-pinned closure with no index contacted"
    fi

    # --- images ---------------------------------------------------------
    _img_missing=()
    if [[ -d "$DIR/images" ]]; then
      step "container images"
      command -v docker >/dev/null 2>&1 || die "docker is not installed here"
      _rewrote=0
      # MATERIALISE THE LIST FIRST, and check that it succeeded.
      # This used to be `while read ... done < <(python ...)`: a process
      # substitution's exit status is not the loop's, so when the manifest
      # validation below rejected a hostile entry the loop simply read zero
      # lines and install reported SUCCESS. A validation that cannot fail the
      # thing it validates is decoration.
      _imglist="$(mktemp)"
      if ! "$PY" -c '
import json, os, sys
sys.path.insert(0, os.path.join(sys.argv[2], "scripts", "lib"))
import bundle_manifest as B
root = sys.argv[1]
doc = json.load(open(os.path.join(root, "MANIFEST.json")))
for comp in doc.get("components", []):
    if comp.get("kind") != "images":
        continue
    # ABSENT in a bundle built before the field existed -> "unknown", which the
    # shell side treats as "cannot tell", never as "matches".
    store = comp.get("image_store") or "unknown"
    if store not in ("containerd", "classic", "unknown"):
        sys.exit(f"manifest records an image store this tool does not know: {store!r}")
    for img in comp.get("images", []):
        f, ref, iid = img.get("file",""), img.get("ref",""), img.get("id","")
        # The FILE PATH COMES FROM THE MANIFEST — the very thing we are
        # deciding whether to trust — and it is handed to `gzip -dc`. Contain
        # it with the same primitive verify() uses, not a second weaker rule.
        try:
            B.safe_join(root, f)
        except B.BundleError as e:
            sys.exit(f"refusing this bundle: {e}")
        # An EMPTY id makes the load-time identity check pass vacuously:
        # `docker inspect ""` compared against `""` proves nothing.
        if not iid.startswith("sha256:") or len(iid) < 20:
            sys.exit(f"manifest records an unusable image id for {ref!r}: {iid!r}")
        if not ref:
            sys.exit(f"manifest records an image with no ref: {f!r}")
        print("\t".join([f, ref, iid, store]))
' "$DIR" "$REPO_DIR" > "$_imglist"; then
        rm -f "$_imglist"
        die "the bundle's image records are not usable (see above). Nothing
       was loaded and docker-compose.yml was not touched."
      fi
      _here_store="$(_image_store_kind)"
      while IFS=$'\t' read -r _file _ref _id _built_store; do
        [[ -n "$_file" ]] || continue
        echo "  loading $_file..."
        # The load's OWN exit status, not `|| true`: a failed load used to be
        # discovered one step later as "image is not present", minus the reason.
        _load_rc=0
        _loaded="$(gzip -dc "$DIR/$_file" | docker load 2>&1)" || _load_rc=$?
        [[ $_load_rc -eq 0 ]] || die "docker load failed for $_file (rc=$_load_rc).
       docker said: $(tail -n 1 <<< "$_loaded")
       The file's sha256 matched the manifest, so the bytes are the ones that
       were built — this is the daemon (disk space? permissions? not running?)."
        # WHAT WAS ACTUALLY LOADED, from docker's own words, resolved to the ID
        # THIS daemon gives it. `docker load` prints one of
        #   Loaded image: <name>            Loaded image ID: sha256:<id>
        # depending on whether the tarball carried a name. That ID — not the
        # one the build box wrote down — is what compose must be pointed at,
        # because the two differ across image-store kinds (see the header).
        _use_id=""
        while IFS= read -r _ln; do
          case "$_ln" in
            "Loaded image ID: "*) _cand="${_ln#Loaded image ID: }" ;;
            "Loaded image: "*)    _cand="${_ln#Loaded image: }" ;;
            *) continue ;;
          esac
          _use_id="$(docker inspect --format '{{.Id}}' "$_cand" 2>/dev/null || true)"
          [[ -z "$_use_id" ]] || break
        done <<< "$_loaded"
        _have="$(docker inspect --format '{{.Id}}' "$_id" 2>/dev/null || true)"
        if [[ "${_built_store:-unknown}" != "unknown" && "$_built_store" == "$_here_store" ]]; then
          # Same kind of store at both ends: the IDs are the same kind of
          # digest, so they must agree, and a disagreement is real.
          [[ "$_have" == "$_id" ]] \
            || die "loaded $_file, but the image ID recorded at build time is not in
       this daemon's store afterwards.
         recorded  $_id   (image store: $_built_store)
         loaded    ${_use_id:-nothing docker load named}   (image store: $_here_store)
       docker said: $(tail -n 1 <<< "$_loaded")
       Both boxes use the same kind of image store, so these should be equal.
       The tarball is not the image the manifest says it is — rebuild the bundle."
          _use_id="$_id"
          ok "$_ref -> ${_use_id:0:19}… present locally (ID matches the manifest)"
        elif [[ -n "$_have" && "$_have" == "$_id" ]]; then
          # Kinds differ or are unknown, but the recorded ID resolves anyway.
          _use_id="$_id"
          ok "$_ref -> ${_use_id:0:19}… present locally (ID matches the manifest)"
        else
          [[ -n "$_use_id" ]] || die "loaded $_file, but docker load named nothing this
       daemon can resolve, and the recorded ID $_id is not present either.
       docker said: $(tail -n 1 <<< "$_loaded")"
          if [[ "${_built_store:-unknown}" == "unknown" || "$_here_store" == "unknown" ]]; then
            _why="the image-store kind of one end is not known (built on:
      ${_built_store:-unknown} — a bundle from before that was recorded says
      unknown; this box: $_here_store), so a mismatch cannot be told apart from
      a store difference"
          else
            _why="the image stores differ (built on: $_built_store, this box:
      $_here_store) and the two kinds report different digests as an image's
      ID, so the recorded ${_id:0:19}… cannot match here by construction"
          fi
          warn "$_ref: image-ID verification SKIPPED — $_why.
      Integrity is NOT lost: $_file matched its sha256 in the manifest before
      it was loaded. Using what docker load reported: ${_use_id:0:19}…"
        fi
        # THE DIGEST REWRITE. compose pins by registry manifest digest, which
        # save/load cannot carry; the image ID is a content digest that
        # survives it and compose resolves locally. Keep the original file.
        #
        # ANCHORED to an `image:` line, not a bare substring search. A literal
        # replace of the ref anywhere in the file would also rewrite it inside
        # a comment, and a ref that happens to appear in two services would be
        # rewritten in both — correct only by accident.
        # MATCH THE REF **OR** AN ID THIS SERVICE WAS PREVIOUSLY REWRITTEN
        # TO. After the first install, compose holds `image: sha256:<old id>`
        # and no longer contains the ref at all — so a SECOND bundle (the
        # sanctioned way to update images offline) loaded and verified its new
        # image and then left compose pinned to the PREVIOUS one, silently.
        # The fix has to work from either starting state, so the search is
        # ref-or-any-sha256-image-line, and the replacement below rewrites the
        # line whose current value is either.
        # (`/` is NOT in the escape class below: it is not an ERE metacharacter,
        # and escaping it made GNU grep print "stray \ before /" on every
        # install, for every image.)
        _prev_id="$(OB_REF="$_ref" "$PY" - "$REPO_DIR/docker-compose.yml.pre-bundle" "$REPO_DIR/docker-compose.yml" <<'PYPREV'
import os, re, sys
ref = os.environ["OB_REF"]
# Which SERVICE held this ref originally? Its NAME tells us which image: line
# to look at now, even though that line's value has since become an id.
#
# By name, never by LINE INDEX. .pre-bundle is written once and never
# refreshed, so after any edit to compose (a `git pull` that adds a service
# above this one) the same index is a different service: install rewrote TWO
# services to one image, printed "every image resolves locally", and exited 0.
def images_by_service(path):
    out, svc = {}, None
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for line in lines:
        m = re.match(r"^  ([A-Za-z0-9._-]+):\s*(#.*)?$", line)
        if m:
            svc = m.group(1)
            continue
        st = line.strip()
        if svc and st.startswith("image:"):
            out.setdefault(svc, st[len("image:"):].strip())
    return out
orig = images_by_service(sys.argv[1])
cur = images_by_service(sys.argv[2])
for svc, image in orig.items():
    if image == ref and svc in cur:
        print(cur[svc])
        break
PYPREV
)" || _prev_id=""
        if grep -qE "^[[:space:]]*image:[[:space:]]*($(printf '%s' "$_ref" | sed 's/[][\.*^$(){}?+|]/\\&/g')|$(printf '%s' "${_prev_id:-__none__}" | sed 's/[][\.*^$(){}?+|]/\\&/g'))[[:space:]]*$" "$REPO_DIR/docker-compose.yml"; then
          [[ -f "$REPO_DIR/docker-compose.yml.pre-bundle" ]] \
            || cp "$REPO_DIR/docker-compose.yml" "$REPO_DIR/docker-compose.yml.pre-bundle"
          # Replacement via python, on the `image:` line only: an image ref
          # contains / : @ and sed would need escaping that is easy to get
          # subtly wrong.
          OB_OLD="$_ref" OB_PREV="${_prev_id:-}" OB_NEW="$_use_id" \
            "$PY" - "$REPO_DIR/docker-compose.yml" <<'PYREW'
import os, sys
p = sys.argv[1]
old, prev, new = (os.environ["OB_OLD"], os.environ.get("OB_PREV", ""),
                  os.environ["OB_NEW"])
# Either the original ref, or the id a previous install rewrote it to. Both
# are matched ONLY as the whole value of an `image:` line, so a ref mentioned
# in a comment or in prose is never touched.
targets = {t for t in (old, prev) if t}
out, n = [], 0
for line in open(p, encoding="utf-8").read().splitlines(keepends=True):
    st = line.strip()
    if st.startswith("image:") and st[len("image:"):].strip() in targets:
        out.append(line.replace(st[len("image:"):].strip(), new)); n += 1
    else:
        out.append(line)
if n:
    open(p, "w", encoding="utf-8").write("".join(out))
    print(f"  rewrote {n} image reference(s) -> {new[:19]}…")
else:
    sys.exit(f"could not find an image: line for {old!r} "
             f"(previously {prev!r}) — compose was NOT updated")
PYREW
          _rewrote=1
        elif grep -qE "^[[:space:]]*image:[[:space:]]*$(printf '%s' "$_use_id" | sed 's/[][\.*^$(){}?+|]/\\&/g')[[:space:]]*$" "$REPO_DIR/docker-compose.yml"; then
          # Already pointing at exactly this image: a RE-RUN, which is the
          # recovery path the weights step tells operators to take ("re-run
          # once there is room"). Dying here called that "image pins differ".
          ok "compose already uses this image — nothing to rewrite"
        else
          # THIS BRANCH DID NOT EXIST, and its absence was silent: the image
          # loaded, nothing in compose was rewritten, NOTHING WAS PRINTED,
          # install ended "done", and start.sh (--pull never when offline)
          # then failed "image not here" with no trail back to this step.
          rm -f "$_imglist"
          die "loaded $_ref, but docker-compose.yml has no \`image:\` line for it
       (nor for an ID a previous bundle install rewrote it to). The bundle was
       built from a checkout whose image pins differ from this one's, so the
       stack here would still ask for an image this bundle does not carry.
         this checkout wants:
$(_compose_images | sed 's/^/           /')
       Check out the commit the bundle was built from ($("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]+"/MANIFEST.json")).get("repo_commit","?"))' "$DIR" 2>/dev/null || echo "?")),
       or rebuild the bundle from this one."
        fi
      done < "$_imglist"
      rm -f "$_imglist"
      # THE CLAIM THAT MATTERS is not "each tarball loaded" but "everything
      # compose will ask for is here" — a bundle can legitimately carry a
      # subset (build skips an image the build box lacks). Checked against
      # compose as it NOW stands, and carried to the end rather than fatal
      # here, so a box that still needs its weight gets it on this run.
      while IFS= read -r _cref; do
        [[ -n "$_cref" ]] || continue
        docker image inspect "$_cref" >/dev/null 2>&1 || _img_missing+=("$_cref")
      done < <(_compose_images)
      if [[ ${#_img_missing[@]} -gt 0 ]]; then
        warn "docker-compose.yml references ${#_img_missing[@]} image(s) that are NOT in
      this box's image store, so ./start.sh cannot bring those services up
      offline (it does not pull):
$(printf '          %s\n' "${_img_missing[@]}")
      The bundle did not carry them — \`./scripts/bundle.sh show\` lists what
      it was built without. Pull them on the build box and rebuild."
      else
        ok "every image docker-compose.yml references resolves locally"
      fi
      if [[ $_rewrote -eq 1 ]]; then
        warn "docker-compose.yml now references images by CONTENT ID instead of
      registry digest, because save/load cannot carry a registry digest. The
      original is at docker-compose.yml.pre-bundle — restore it if this box
      ever gets a network back, so digest pinning resumes."
      fi
    fi

    # --- weights --------------------------------------------------------
    if [[ -d "$DIR/weights" ]]; then
      step "weights"
      _need_weights_dir 1
      _wd="$WEIGHTS_DIR"
      mkdir -p "$_wd"
      # FROM THE MANIFEST, not a glob of weights/. The manifest is what was
      # verified (and signed); `weights/*` is whatever is in the directory. It
      # also hands us each file's recorded sha256, which the copy is checked
      # against below.
      _wlist="$(mktemp)"
      if ! "$PY" -c '
import json, os, sys
sys.path.insert(0, os.path.join(sys.argv[2], "scripts", "lib"))
import bundle_manifest as B
root = sys.argv[1]
doc = json.load(open(os.path.join(root, "MANIFEST.json")))
for comp in doc.get("components", []):
    if comp.get("kind") != "weights":
        continue
    for rec in comp.get("files", []):
        rel, sha, size = rec.get("path", ""), rec.get("sha256", ""), rec.get("bytes")
        try:
            B.safe_join(root, rel)
        except B.BundleError as e:
            sys.exit(f"refusing this bundle: {e}")
        if len(sha) != 64 or not isinstance(size, int):
            sys.exit(f"manifest records no usable sha256/size for {rel!r}")
        print("\t".join([rel, sha, str(size)]))
' "$DIR" "$REPO_DIR" > "$_wlist"; then
        rm -f "$_wlist"
        die "the bundle's weight records are not usable (see above). No weight
       was copied."
      fi
      # A half-written copy must never be left under ANY name on a die or a
      # Ctrl-C: this trap removes the one in flight.
      _OB_PARTIAL=""
      trap '[[ -z "${_OB_PARTIAL:-}" ]] || rm -f -- "$_OB_PARTIAL"' EXIT
      trap 'exit 130' INT TERM
      while IFS=$'\t' read -r _rel _msha _mbytes; do
        [[ -n "$_rel" ]] || continue
        _w="$DIR/$_rel"
        _n="$(basename "$_rel")"
        _dest="$_wd/$_n"
        if [[ -f "$_dest" ]]; then
          # "already there — left alone" USED TO BE UNCONDITIONAL, and the copy
          # went straight to the final name. So an ENOSPC or a Ctrl-C left a
          # truncated 20 GB x.gguf that every re-run reported as fine, and
          # llama-server later failed on. Existing is not the same as correct:
          # size first (free), then sha256 (a minute, once).
          _have_bytes="$(stat -c%s "$_dest")"
          if [[ "$_have_bytes" == "$_mbytes" ]]; then
            echo "  $_n is already in $_wd — checking it (sha256)..."
            _have_sha="$(sha256sum "$_dest" | awk '{print $1}')"
          else
            _have_sha="(not hashed: $_have_bytes bytes, the manifest says $_mbytes)"
          fi
          if [[ "$_have_sha" == "$_msha" ]]; then
            ok "$_n already in $_wd and matches the bundle — left alone"
            continue
          fi
          warn "$_n exists in $_wd but is NOT the file this bundle carries
      (sha256 $_have_sha).
      A truncated earlier copy looks exactly like this. REPLACING it — the
      existing file is only removed once the new copy has verified."
        fi
        echo "  copying $_n ($(du -h "$_w" | cut -f1))..."
        # TO A .partial, VERIFIED, THEN RENAMED. The final name only ever
        # holds a file that matched its hash.
        _OB_PARTIAL="$_dest.partial"
        cp -- "$_w" "$_OB_PARTIAL" \
          || die "could not copy $_n into $_wd (disk full? $(df -h "$_wd" 2>/dev/null | awk 'NR==2{print $4 " free"}')).
       Nothing was left behind under the weight's name — re-run once there is room."
        _got="$(sha256sum "$_OB_PARTIAL" | awk '{print $1}')"
        [[ "$_got" == "$_msha" ]] || die "$_n changed while being copied into $_wd.
       manifest $_msha
       copy     $_got
       The copy was removed. A failing disk or stick is the usual cause."
        # The manifest hash proved the TRANSFER. The registry hash proves it
        # is the weight OpenBeast pinned, which is a different claim.
        _reg="$(awk -F'\t' -v f="$_n" '$3 == f {print $1}' "$REPO_DIR/scripts/weights.registry" 2>/dev/null || true)"
        if [[ -n "$_reg" && "$_reg" != "PENDING" && "$_got" != "$_reg" ]]; then
          die "$_n does not match the registry sha256 — NOT installed (the copy
       was removed; anything already at $_dest was not touched).
       expected $_reg
       got      $_got"
        fi
        mv -f -- "$_OB_PARTIAL" "$_dest" || die "could not move $_n into place in $_wd"
        _OB_PARTIAL=""
        if [[ -n "$_reg" && "$_reg" != "PENDING" ]]; then
          ok "$_n (sha256 matches the manifest AND scripts/weights.registry)"
        else
          warn "$_n is not pinned in scripts/weights.registry — copied, unverifiable
      against the registry (the bundle's own hash did verify the copy)"
        fi
      done < "$_wlist"
      rm -f "$_wlist"
    fi

    if [[ ${#_img_missing[@]} -gt 0 ]]; then
      step "installed, BUT ${#_img_missing[@]} image(s) compose needs are missing (listed above)"
      _final_rc=1
    else
      step "done"
      _final_rc=0
    fi
    cat <<EOF
  Next, on this box:
    echo 'OFFLINE=true' >> openbeast.conf     # refuse the fetches, don't stall
    ./bootstrap.sh
    ./scripts/doctor.sh                       # reports offline self-sufficiency
EOF
    exit "$_final_rc"
    ;;

  -h|--help|help)
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
    ;;

  *)
    die "unknown command '$CMD' — build | sign | show | verify | install"
    ;;
esac
