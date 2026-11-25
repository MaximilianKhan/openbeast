#!/usr/bin/env bash
# eval-host-container — the eval harness host as a container (Linux, docker).
#
#   ./scripts/eval-host-container.sh build [--no-cache]
#         build the image from evals/host/Containerfile; its tag is a hash of
#         the pins (toolchains.lock, requirements.lock, Containerfile), so a
#         changed pin means a new image
#   ./scripts/eval-host-container.sh save [FILE]      the image to FILE.tar.gz (+ .sha256)
#   ./scripts/eval-host-container.sh load FILE        sha256-check FILE, then docker load it
#   ./scripts/eval-host-container.sh check            eval-host.sh check + the integrity
#         probe (evals/host/probe.py: root-owned pins, a planted compiler refused,
#         landlock + seccomp confinement), inside the container; exit 1 on any problem
#   ./scripts/eval-host-container.sh shell            an interactive shell inside
#   ./scripts/eval-host-container.sh run [--pass NAME]... -- CMD...
#         run CMD inside, from the checkout, e.g.
#           run -- python3 evals/profile.py --model <slug> ...
#           run -- python3 evals/campaign.py run --model <slug> ...
#         (a leading python3/python runs the eval venv's interpreter,
#          /opt/openbeast-eval/venv/bin/python: the image's bare cpython has
#          no agent dependencies)
#
# WHY. The harness runs agents' tools, compilers and validators where it runs.
# evals/vlib.py pins root-owned compilers first, so a host whose distro ships
# another go or rustc in /usr/bin (the 5090 rig: Arch) is `blocked` by
# eval-host.sh check. In the image the pinned toolchains of
# evals/toolchains.lock ARE the root-owned ones (/opt/openbeast-eval), and
# no other go, rustc or zig exists. docs/MODEL_CAMPAIGN.md "Eval host in a
# container (Linux)".
#
# HOW IT RUNS. The checkout this script lives in is mounted read-write at
# its own path (and, for a git worktree, the main repository's .git dir too,
# so git works), so the harness version is the mounted tree's. Host
# networking: the model URL is whatever it is on the host (the rig's own
# model through beast-gate is http://127.0.0.1:8091; the Sparks over the
# tailnet). The harness runs as the non-root user `eval` with the operator's
# uid/gid (set at build), no capabilities, no-new-privileges, docker's
# default seccomp profile, and --init to reap orphans. No --privileged and
# no --security-opt that loosens anything: landlock and the harness's own
# seccomp filter work unprivileged (`check` proves it).
#
# SECRETS. Only variable NAMES are passed (`docker run -e NAME`: the value
# comes from this environment and never appears in an argv): every exported
# *_API_KEY, plus each --pass NAME. Nothing reads openbeast.conf.
#
# OFFLINE. `save` writes a gzipped `docker save` and its sha256; `load`
# checks the sha256 before loading. That tarball is the offline path: build
# once on a connected box, carry it, load it.
#
# Environment: OPENBEAST_EVAL_IMAGE (an image to use instead of the computed
# tag), OPENBEAST_EVAL_CONTAINER_CPUS (docker --cpus, e.g. 12; unset = no
# limit), DOCKER (the client, default docker).
set -euo pipefail
EVAL_VENV_PY=/opt/openbeast-eval/venv/bin/python   # OPENBEAST_EVAL_PY in the image (evals/host/Containerfile)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
# shellcheck source=lib/usage.sh
source "$SCRIPT_DIR/lib/usage.sh"

DOCKER="${DOCKER:-docker}"
NAME="openbeast-eval-host"
# What the image is built from (staged into a private build context).
INPUTS=(evals/host/Containerfile evals/toolchains.lock agents/requirements.lock
        agents/requirements.txt scripts/eval-host.sh scripts/pydeps.sh
        scripts/lib/usage.sh scripts/lib/pydeps_lock.py)

die() { echo "eval-host-container: $*" >&2; exit 1; }
say() { echo "eval-host-container: $*" >&2; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# The tag: a hash of every build input and of the uid/gid baked in.
image_tag() {
  local f
  {
    for f in "${INPUTS[@]}"; do printf '%s %s\n' "$f" "$(sha256_of "$REPO_DIR/$f")"; done
    printf 'uid %s gid %s\n' "$(id -u)" "$(id -g)"
  } | { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } \
    | cut -c1-12
}

IMAGE="${OPENBEAST_EVAL_IMAGE:-$NAME:$(image_tag)}"

need_docker() {
  command -v "$DOCKER" >/dev/null 2>&1 || die "$DOCKER is not installed"
  "$DOCKER" info >/dev/null 2>&1 \
    || die "cannot reach the docker daemon as $(id -un) (is the user in the docker group?)"
}

need_image() {
  "$DOCKER" image inspect "$IMAGE" >/dev/null 2>&1 && return 0
  die "no image $IMAGE (the pins changed, or it was never built): $0 build, or $0 load FILE"
}

cmd_build() {
  local ctx f rc=0
  need_docker
  [ "$(uname -s)" = Linux ] || say "warning: built for a Linux host; on $(uname -s) the harness's Linux confinement runs in a VM"
  ctx="$(mktemp -d)"
  for f in "${INPUTS[@]}"; do
    mkdir -p "$ctx/$(dirname "$f")"; cp "$REPO_DIR/$f" "$ctx/$f"
  done
  say "building $IMAGE (context: the ${#INPUTS[@]} pinned inputs only)"
  nice -n 10 "$DOCKER" build -f "$ctx/evals/host/Containerfile" -t "$IMAGE" \
    --build-arg "EVAL_UID=$(id -u)" --build-arg "EVAL_GID=$(id -g)" \
    --label "openbeast.lock-sha256=$(sha256_of "$REPO_DIR/evals/toolchains.lock")" \
    --label "openbeast.requirements-sha256=$(sha256_of "$REPO_DIR/agents/requirements.lock")" \
    --label "openbeast.git=$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
    "$@" "$ctx" || rc=$?
  rm -rf "$ctx"
  [ "$rc" -eq 0 ] || die "build failed (exit $rc)"
  "$DOCKER" image inspect "$IMAGE" --format "built {{.Id}} ({{.Size}} bytes)" >&2
}

cmd_save() {
  local out="${1:-$PWD/${IMAGE//[:\/]/-}.tar.gz}"
  need_docker; need_image
  say "saving $IMAGE to $out"
  "$DOCKER" save "$IMAGE" | gzip -1 > "$out.part"
  mv -f "$out.part" "$out"
  printf '%s  %s\n' "$(sha256_of "$out")" "$(basename "$out")" > "$out.sha256"
  echo "$out ($(du -h "$out" | awk '{print $1}')); sha256 in $out.sha256"
  echo "offline box: $0 load $(basename "$out")"
}

cmd_load() {
  local f="${1:-}" want have
  if [ -z "$f" ] || [ ! -f "$f" ]; then die "load FILE (a tarball from \`save\`)"; fi
  need_docker
  if [ -f "$f.sha256" ]; then
    want="$(awk '{print $1; exit}' "$f.sha256")"; have="$(sha256_of "$f")"
    [ "$want" = "$have" ] || die "SHA256 MISMATCH for $f: expected $want, got $have; not loading"
    say "sha256 ok ($have)"
  else
    say "warning: no $f.sha256 next to it; loading unverified"
  fi
  gzip -dc "$f" | "$DOCKER" load
}

# docker run argv for this checkout
run_args() {
  local common v
  RUN=(run --rm --init --network host
       --cap-drop ALL --security-opt no-new-privileges
       --label openbeast.eval-host=1
       -v "$REPO_DIR:$REPO_DIR" -w "$REPO_DIR")
  # A git worktree's .git is a file pointing into the main repository: mount
  # that too (same path), or every `git rev-parse` the harness runs fails.
  common="$(git -C "$REPO_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [ -n "$common" ] && [ -d "$common" ]; then
    case "$common" in "$REPO_DIR"/*) ;; *) RUN+=(-v "$common:$common") ;; esac
  fi
  [ -z "${OPENBEAST_EVAL_CONTAINER_CPUS:-}" ] || RUN+=(--cpus "$OPENBEAST_EVAL_CONTAINER_CPUS")
  [ -t 0 ] && [ -t 1 ] && RUN+=(-it)
  # names only: docker takes each value from this environment
  for v in $(compgen -e | grep -E '_API_KEY$' || true) "${PASS[@]+"${PASS[@]}"}"; do
    RUN+=(-e "$v")
  done
  for v in OPENBEAST_TIMEOUT_SCALE OPENBEAST_EVAL_ENV_ERA; do
    [ -z "${!v:-}" ] || RUN+=(-e "$v")
  done
}

in_container() {
  need_docker; need_image; run_args
  "$DOCKER" "${RUN[@]}" "$IMAGE" "$@"
}

cmd_check() {
  local rc=0
  # shellcheck disable=SC2016  # expanded inside the container
  in_container bash -c '
    set -u
    rc=0
    echo "== scripts/eval-host.sh check"
    bash scripts/eval-host.sh check || rc=1
    echo "== evals/host/probe.py (integrity)"
    "$OPENBEAST_EVAL_PY" -I evals/host/probe.py || rc=1
    exit $rc' || rc=$?
  [ "$rc" -eq 0 ] && echo "eval host container ready ($IMAGE)"
  return "$rc"
}

PASS=()
CMD="${1:-}"
[ $# -gt 0 ] && shift
case "$CMD" in
  -h|--help|help|"") ob_usage "$0"; exit 0 ;;
  build) cmd_build "$@" ;;
  save)  cmd_save "$@" ;;
  load)  cmd_load "$@" ;;
  check) cmd_check ;;
  tag)   echo "$IMAGE" ;;
  shell) in_container bash ;;
  run)
    while [ $# -gt 0 ]; do
      case "$1" in
        --pass) [ $# -ge 2 ] || die "--pass needs a variable name"
                [[ "$2" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "--pass takes a variable NAME, not $2"
                PASS+=("$2"); shift 2 ;;
        --) shift; break ;;
        *) break ;;
      esac
    done
    [ $# -gt 0 ] || die "run -- CMD..."
    # A bare python3/python is the image's pinned cpython, which has no agent
    # dependencies: the profile preflight refuses it ("No module named
    # 'openai'"). The harness runs under the eval venv, so map it there.
    case "$1" in python|python3|python3.12) set -- "$EVAL_VENV_PY" "${@:2}" ;; esac
    in_container "$@" ;;
  *) die "unknown command '$CMD' (build | save | load | check | shell | run | tag; --help)" ;;
esac
