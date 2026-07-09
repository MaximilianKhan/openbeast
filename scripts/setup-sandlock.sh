#!/bin/bash
# OpenBeast — build + install Sandlock, the kernel-level sandbox for the
# agent bash tool (Arsenal Phase 1, docs/TOOL_ARSENAL_RESEARCH.md finding #3).
#
#   ./scripts/setup-sandlock.sh          # check, build, install, verify
#   ./scripts/setup-sandlock.sh --check  # only report kernel/toolchain support
#
# What it does (idempotent, safe to re-run):
#   1. Verifies Landlock is active in the kernel LSM list and the kernel is
#      6.12+ (Landlock ABI v6, needed for IPC scoping).
#   2. Clones sandlock at the PINNED, security-reviewed commit and builds
#      only the CLI crate (no OCI shim, no FFI lib, no Python/Go SDK).
#   3. Installs the binary to ~/.local/bin/sandlock (outside this repo).
#   4. Installs the OpenBeast policy profile to ~/.config/sandlock/profiles/.
#   5. Runs a smoke test (allowed command works, $HOME write is denied).
#   6. Prints the OPENBEAST_BASH_WRAPPER value to enable it (OPT-IN — this
#      script never enables the wrapper itself).
#
# Sandboxing stays OPT-IN until agent behavior under confinement is
# eval-validated (see docs/SANDBOXING.md, "Road to default").
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Pinned commit: security-reviewed + empirically validated 2026-07-08
# (v0.8.4). Bump ONLY after re-running the review + validation matrix in
# docs/SANDBOXING.md against the new commit.
SANDLOCK_REPO="https://github.com/multikernel/sandlock"
SANDLOCK_COMMIT="1cd6ba6518f614bf4db469f1b2d0416bc2f1cd54"

BIN_DIR="$HOME/.local/bin"
PROFILE_DIR="$HOME/.config/sandlock/profiles"
PROFILE_SRC="$REPO_DIR/scripts/sandlock-profile-openbeast.toml"
BUILD_DIR="${TMPDIR:-/tmp}/sandlock-build-$$"

info()  { echo "[setup-sandlock] $*"; }
fail()  { echo "[setup-sandlock] ERROR: $*" >&2; exit 1; }

# --- 1. Kernel support -------------------------------------------------------
check_support() {
  local lsm=""
  if [[ -r /sys/kernel/security/lsm ]]; then
    lsm="$(cat /sys/kernel/security/lsm)"
  fi
  if [[ "$lsm" != *landlock* ]]; then
    fail "Landlock is not in the active LSM list (${lsm:-unreadable}).
  Sandlock needs it. On most distros add 'lsm=landlock,...' to the kernel
  cmdline; Arch/recent kernels ship it enabled by default."
  fi
  info "Landlock active in LSM list: $lsm"

  local kver major minor
  kver="$(uname -r)"
  major="${kver%%.*}"; minor="${kver#*.}"; minor="${minor%%.*}"
  if (( major < 6 || (major == 6 && minor < 12) )); then
    fail "kernel $kver < 6.12 — Sandlock needs Landlock ABI v6 (6.12+)."
  fi
  info "kernel $kver >= 6.12: OK"

  command -v cargo >/dev/null 2>&1 \
    || fail "cargo not found — install Rust (e.g. 'pacman -S rust' or rustup)."
  info "rust toolchain: $(rustc --version 2>/dev/null || echo cargo present)"
}

check_support
if [[ "${1:-}" == "--check" ]]; then
  info "support check passed (nothing installed with --check)."
  exit 0
fi

# --- 2. Skip rebuild if the pinned build is already installed ----------------
installed_ok() {
  [[ -x "$BIN_DIR/sandlock" ]] || return 1
  [[ -f "$BIN_DIR/.sandlock-commit" ]] || return 1
  [[ "$(cat "$BIN_DIR/.sandlock-commit")" == "$SANDLOCK_COMMIT" ]] || return 1
}

if installed_ok; then
  info "sandlock at pinned commit already installed: $BIN_DIR/sandlock ($($BIN_DIR/sandlock --version))"
else
  info "cloning $SANDLOCK_REPO @ ${SANDLOCK_COMMIT:0:12}"
  rm -rf "$BUILD_DIR"
  git clone --quiet "$SANDLOCK_REPO" "$BUILD_DIR"
  git -C "$BUILD_DIR" checkout --quiet "$SANDLOCK_COMMIT" \
    || { rm -rf "$BUILD_DIR"; fail "pinned commit $SANDLOCK_COMMIT not found upstream — do NOT blindly bump; re-run the security review first."; }

  info "building sandlock-cli (release)..."
  (cd "$BUILD_DIR" && cargo build --release -p sandlock-cli --quiet)

  mkdir -p "$BIN_DIR"
  install -m755 "$BUILD_DIR/target/release/sandlock" "$BIN_DIR/sandlock"
  echo "$SANDLOCK_COMMIT" > "$BIN_DIR/.sandlock-commit"
  rm -rf "$BUILD_DIR"
  info "installed $BIN_DIR/sandlock ($($BIN_DIR/sandlock --version))"
fi

# --- 3. Install the OpenBeast profile ----------------------------------------
[[ -f "$PROFILE_SRC" ]] || fail "profile source missing: $PROFILE_SRC"
mkdir -p "$PROFILE_DIR"
if [[ -f "$PROFILE_DIR/openbeast.toml" ]] \
   && ! cmp -s "$PROFILE_SRC" "$PROFILE_DIR/openbeast.toml"; then
  cp "$PROFILE_DIR/openbeast.toml" "$PROFILE_DIR/openbeast.toml.bak"
  info "existing profile differed — backed up to openbeast.toml.bak"
fi
cp "$PROFILE_SRC" "$PROFILE_DIR/openbeast.toml"
info "profile installed: $PROFILE_DIR/openbeast.toml"

# --- 4. Smoke test ------------------------------------------------------------
SL="$BIN_DIR/sandlock"
out="$($SL run -p openbeast -w /tmp -- /bin/sh -c 'echo sandbox-ok' 2>&1)" \
  || fail "smoke test: allowed command failed: $out"
[[ "$out" == *sandbox-ok* ]] || fail "smoke test: unexpected output: $out"

if $SL run -p openbeast -w /tmp -- /bin/sh -c "echo pwned > $HOME/.sandlock-smoke-test" 2>/dev/null; then
  rm -f "$HOME/.sandlock-smoke-test"
  fail "smoke test: \$HOME write was NOT denied — confinement broken, do not enable."
fi
info "smoke test passed: allowed path works, \$HOME write denied."

# --- 5. How to enable (opt-in) -------------------------------------------------
cat <<'EOF'

Sandlock is installed but NOT enabled (opt-in by design).

To sandbox every model-issued bash command, set (note the SINGLE quotes —
$PWD must reach the runtime shell unexpanded; bash() runs each command with
cwd set to the agent workdir, so -w "$PWD" grants exactly that directory):

  export OPENBEAST_BASH_WRAPPER='sandlock run -p openbeast -w "$PWD" --'

Where to put it:
  - One-off / testing:  export it in the shell before ./start.sh
  - Persistent:         add the export line to openbeast.conf (it is a
                        sourced shell file; conf.sh env-first precedence
                        applies once conf.sh forwards it — see
                        docs/SANDBOXING.md "Enabling")

To verify it is active, ask the agent to run:  ls ~/.ssh
  -> confined:  "No such file or directory" (Landlock hides it)

Full policy, threat model, and validation matrix: docs/SANDBOXING.md
EOF
