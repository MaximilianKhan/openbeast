#!/usr/bin/env bash
# The offline bundle: build it connected, install it from a USB stick.
#
#   ./scripts/bundle.sh build <dir> [--with-weights[=A,B]] [--no-images]
#   ./scripts/bundle.sh show <dir>        what is in it
#   ./scripts/bundle.sh verify <dir>      re-hash everything it claims
#   ./scripts/bundle.sh install <dir>     place it, verifying as it goes
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
# The answer is not to give up content addressing. An image's ID *is* a
# content digest (of its config), it DOES survive save/load, and
# `docker compose` accepts `image: sha256:<id>` and resolves it locally with
# no pull — measured on 2026-09-15, not assumed. So: the manifest records the
# ID, install verifies the loaded image against it, and the compose reference
# is rewritten to that ID. The original docker-compose.yml is kept, so the
# rewrite is reversible.
#
# WEIGHTS ARE OPT-IN. A 20 GB copy is a different operation from a 60 MB one,
# and most transfers are a rebuild of a box that already has its weight. So
# `build` skips weights unless asked, and SAYS SO in the manifest rather than
# leaving a reader to discover it at install time.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
# shellcheck source=scripts/lib/conf.sh
source "$REPO_DIR/scripts/lib/conf.sh"
# WEIGHTS_DIR comes from the SHARED resolver (env -> openbeast.conf -> default),
# not from a local guess: weights are relocatable and a second answer here
# would put a bundle's weight somewhere the serve scripts do not look.
# shellcheck source=scripts/lib/weights.sh
source "$REPO_DIR/scripts/lib/weights.sh"

HELPER="$REPO_DIR/scripts/lib/bundle_manifest.py"
PY="${OPENBEAST_PYTHON:-python3}"

c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_red=$'\e[31m'; c_rst=$'\e[0m'
ok()   { echo "  ${c_grn}✓${c_rst} $*"; }
warn() { echo "  ${c_ylw}!${c_rst} $*"; }
die()  { echo "  ${c_red}✗${c_rst} $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

# The image refs the stack actually runs, read from compose rather than
# restated here: a second list would drift from the first.
_compose_images() {
  grep -oE '^\s*image:\s*\S+' "$REPO_DIR/docker-compose.yml" \
    | sed -E 's/^\s*image:\s*//' | sort -u
}

CMD="${1:-show}"
shift || true

case "$CMD" in
  build)
    DIR="${1:-}"; shift || true
    [[ -n "$DIR" ]] || die "build needs a target directory"
    WITH_WEIGHTS=""; DO_IMAGES=1; DO_SOURCE=1
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --with-weights)   WITH_WEIGHTS="__default__"; shift ;;
        --with-weights=*) WITH_WEIGHTS="${1#*=}"; shift ;;
        --no-images)      DO_IMAGES=0; shift ;;
        --no-source)      DO_SOURCE=0; shift ;;
        *) die "unknown flag $1" ;;
      esac
    done
    ob_offline && die "OFFLINE=true — a bundle is BUILT on a connected box.
       Run this where there is a network, then carry the directory here."
    mkdir -p "$DIR"
    DIR="$(cd "$DIR" && pwd)"
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
          METAS+=(--meta "images:$(printf '{"images": %s}' "$_img_json")")
        fi
      fi
    else
      SKIPPED+=(--skipped "container images (--no-images)")
    fi

    if [[ -n "$WITH_WEIGHTS" ]]; then
      step "weights"
      mkdir -p "$DIR/weights"
      _w_json="[]"
      # Which files: the named ones, or whatever the configured serve script
      # actually loads. Never "everything in weights/" — that is how a 400 GB
      # bundle happens by accident.
      if [[ "$WITH_WEIGHTS" == "__default__" ]]; then
        _names="$(grep -oE '[A-Za-z0-9._-]+\.gguf' "$REPO_DIR/scripts/$DEFAULT_SERVE_SCRIPT" 2>/dev/null | sort -u || true)"
        [[ -n "$_names" ]] || die "could not tell which weight $DEFAULT_SERVE_SCRIPT loads — pass --with-weights=<file.gguf>"
      else
        _names="$(printf '%s' "$WITH_WEIGHTS" | tr ',' '\n')"
      fi
      while IFS= read -r _wf; do
        [[ -n "$_wf" ]] || continue
        _src="$WEIGHTS_DIR/$_wf"
        [[ -f "$_src" ]] || { warn "$_wf not found at $_src — skipping"; \
                              SKIPPED+=(--skipped "weight $_wf (not on the build box)"); continue; }
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

  show)
    DIR="${1:?show needs a bundle directory}"
    "$PY" "$HELPER" show "$DIR"
    ;;

  verify)
    DIR="${1:?verify needs a bundle directory}"
    "$PY" "$HELPER" verify "$DIR"
    ;;

  install)
    DIR="${1:-}"
    [[ -n "$DIR" ]] || die "install needs a bundle directory"
    DIR="$(cd "$DIR" && pwd)"
    step "verifying the bundle before using any of it"
    "$PY" "$HELPER" verify "$DIR" \
      || die "the bundle does not match its manifest — refusing to install it.
       A directory that travelled is not trusted because it arrived."
    ok "every recorded file matches its sha256"

    # --- source ---------------------------------------------------------
    _tar="$(find "$DIR/source" -maxdepth 1 -name 'llama.cpp-*.tar.gz' 2>/dev/null | head -1 || true)"
    if [[ -n "$_tar" ]]; then
      step "llama.cpp source"
      if [[ -e "$REPO_DIR/llama.cpp" ]]; then
        warn "llama.cpp/ already exists — left alone (delete it first to replace)"
      else
        mkdir -p "$REPO_DIR/llama.cpp"
        tar -xzf "$_tar" -C "$REPO_DIR/llama.cpp"
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
    if [[ -d "$DIR/images" ]]; then
      step "container images"
      command -v docker >/dev/null 2>&1 || die "docker is not installed here"
      _rewrote=0
      while IFS=$'\t' read -r _file _ref _id; do
        [[ -n "$_file" ]] || continue
        echo "  loading $_file..."
        _loaded="$(gzip -dc "$DIR/$_file" | docker load 2>&1 || true)"
        _have="$(docker inspect --format '{{.Id}}' "$_id" 2>/dev/null || true)"
        [[ "$_have" == "$_id" ]] \
          || die "loaded $_file but image $_id is not present afterwards.
       docker said: $(head -1 <<< "$_loaded")
       The bundle's own hash verified, so this is docker's load, not the file."
        ok "$_ref -> ${_id:0:19}… present locally"
        # THE DIGEST REWRITE. compose pins by registry manifest digest, which
        # save/load cannot carry; the image ID is a content digest that
        # survives it and compose resolves locally. Keep the original file.
        if grep -qF "$_ref" "$REPO_DIR/docker-compose.yml"; then
          [[ -f "$REPO_DIR/docker-compose.yml.pre-bundle" ]] \
            || cp "$REPO_DIR/docker-compose.yml" "$REPO_DIR/docker-compose.yml.pre-bundle"
          # Literal replacement via python: an image ref contains / : @ and
          # sed would need escaping that is easy to get subtly wrong.
          OB_OLD="$_ref" OB_NEW="$_id" "$PY" - "$REPO_DIR/docker-compose.yml" <<'PYREW'
import os, sys
p = sys.argv[1]
old, new = os.environ["OB_OLD"], os.environ["OB_NEW"]
s = open(p, encoding="utf-8").read()
if old in s:
    open(p, "w", encoding="utf-8").write(s.replace(old, new))
    print(f"  rewrote {old} -> {new[:19]}…")
PYREW
          _rewrote=1
        fi
      done < <("$PY" -c '
import json, sys
doc = json.load(open(sys.argv[1] + "/MANIFEST.json"))
for comp in doc.get("components", []):
    if comp.get("kind") != "images":
        continue
    for img in comp.get("images", []):
        print("\t".join([img.get("file", ""), img.get("ref", ""), img.get("id", "")]))
' "$DIR")
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
      _wd="$WEIGHTS_DIR"
      mkdir -p "$_wd"
      for _w in "$DIR"/weights/*; do
        [[ -f "$_w" ]] || continue
        _n="$(basename "$_w")"
        if [[ -f "$_wd/$_n" ]]; then
          warn "$_n already in $_wd — left alone"
          continue
        fi
        echo "  copying $_n ($(du -h "$_w" | cut -f1))..."
        cp "$_w" "$_wd/$_n"
        # The manifest hash proved the TRANSFER. The registry hash proves it
        # is the weight OpenBeast pinned, which is a different claim.
        _reg="$(awk -F'\t' -v f="$_n" '$3 == f {print $1}' "$REPO_DIR/scripts/weights.registry" 2>/dev/null || true)"
        if [[ -n "$_reg" ]]; then
          _got="$(sha256sum "$_wd/$_n" | awk '{print $1}')"
          if [[ "$_got" == "$_reg" ]]; then
            ok "$_n (sha256 matches scripts/weights.registry)"
          else
            rm -f "$_wd/$_n"
            die "$_n does not match the registry sha256 — DELETED.
       expected $_reg
       got      $_got"
          fi
        else
          warn "$_n is not in scripts/weights.registry — copied, unverifiable
      against the registry (the bundle's own hash did verify the transfer)"
        fi
      done
    fi

    step "done"
    cat <<EOF
  Next, on this box:
    echo 'OFFLINE=true' >> openbeast.conf     # refuse the fetches, don't stall
    ./bootstrap.sh
    ./scripts/doctor.sh                       # reports offline self-sufficiency
EOF
    ;;

  -h|--help|help)
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
    ;;

  *)
    die "unknown command '$CMD' — build | show | verify | install"
    ;;
esac
