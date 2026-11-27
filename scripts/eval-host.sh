#!/usr/bin/env bash
# eval-host — the eval harness host's toolchains: check them, install them, export them.
#
#   ./scripts/eval-host.sh check [--prefix P] [--only a,b]
#         one line per tool: ok / missing / wrong / system / blocked; exit 1 on any problem
#         (a user-writable tool outside the prefix is `wrong`; an OPENBEAST_EVAL_PY
#         outside it is `outside`, review 1118)
#   ./scripts/eval-host.sh setup [--prefix P] [--only a,b] [--from DIR] [--wheelhouse DIR] [--offline]
#         install every missing or wrong tool into the prefix, then re-check (idempotent)
#   ./scripts/eval-host.sh env [--prefix P] [--json]
#         the exports: eval "$(./scripts/eval-host.sh env)"
#   ./scripts/eval-host.sh fetch --to DIR [--platform P|all] [--wheels]
#         download every pinned file into DIR (a flat mirror) for an offline setup
#
# Tools: zig, go, rust, python (the eval venv), and the system ones (gcc,
# g++, git). Pins, hashes and sources: evals/toolchains.lock. The prefix
# defaults to ~/.local/opt/openbeast-eval (OPENBEAST_EVAL_PREFIX), laid out as
#   bin/                  zig, go, gofmt (and rustc, cargo offline): links
#   zig-<v>/ go-<v>/      the unpacked releases
#   rustup/ cargo/        rustup and the pinned rust toolchain (online)
#   rust-<v>/             the standalone rust installer's tree (offline)
#   cpython-<v>/          the venv's base interpreter (python-build-standalone)
#   venv/                 the eval python: OPENBEAST_EVAL_PY = venv/bin/python
#   .downloads/           verified downloads, re-hashed before every use
#
# Every file is sha256-checked against the lock before it is unpacked; a
# mismatch deletes it and exits 3 (never retry that from another source
# without asking why). Nothing outside the prefix is written, and nothing
# runs as root: gcc, g++ and git are reported with the command to install
# them, never installed.
#
# Offline: --from DIR takes files from a beast-ark drive
# (DIR/files/<entry>/<version>/<platform>/<file>, BEAST_ARK.md §6) or from a
# flat mirror (DIR/<file>, what `fetch --to DIR` writes), and never touches
# the network. Rust then comes from the standalone installer; python
# packages from --wheelhouse DIR, or DIR/wheels-<platform> when `fetch
# --wheels` made one.
#
# Which copy counts is what the validators will pin (evals/vlib.py
# pin_toolchain): the first root-owned copy on PATH, else the first. A
# root-owned copy of the wrong version (an apt `go` in /usr/bin) outranks the
# prefix; `check` reports it as `blocked`, because only removing it helps.
#
# run_eval.py, profile.py and benchmark_all.py run `check` before a live run
# and `setup` when it fails (--no-provision or OPENBEAST_EVAL_NO_PROVISION=1
# stops that). docs/SPARK_HYDRA.md F6 is the runbook.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/usage.sh
source "$SCRIPT_DIR/lib/usage.sh"

LOCK="${OPENBEAST_EVAL_LOCK:-$REPO_DIR/evals/toolchains.lock}"
PREFIX="${OPENBEAST_EVAL_PREFIX:-$HOME/.local/opt/openbeast-eval}"
PYDEPS="${OPENBEAST_EVAL_PYDEPS:-$SCRIPT_DIR/pydeps.sh}"   # tests point this at a fake
ALL_TOOLS="zig go rust python system"

die() { echo "eval-host: $*" >&2; exit 1; }
say() { echo "eval-host: $*" >&2; }

# --- the lock ------------------------------------------------------------------

platform() {
  if [ -n "${OPENBEAST_EVAL_PLATFORM:-}" ]; then
    echo "$OPENBEAST_EVAL_PLATFORM"; return
  fi
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)                echo darwin-arm64 ;;
    Linux-aarch64|Linux-arm64)   echo linux-aarch64 ;;
    Linux-x86_64)                echo linux-x86_64 ;;
    *)                           echo "$(uname -s)-$(uname -m)" ;;
  esac
}

# row NAME [PLATFORM] -> sets R_VER R_SHA R_ARK R_URL R_FILE; 1 when there is no pin
row() {
  local line
  line="$(awk -v n="$1" -v p="${2:-$PLAT}" '$1 == n && $2 == p {print $3, $4, $5, $6; exit}' "$LOCK")"
  [ -n "$line" ] || return 1
  # shellcheck disable=SC2086  # the four fields, word-split on purpose
  set -- $line
  R_VER="$1"; R_SHA="$2"; R_ARK="$3"; R_URL="$4"; R_FILE="${4##*/}"
}

directive() {   # directive @name -> its value (the rest of the line)
  awk -v d="$1" '$1 == d {sub(/^[^ \t]+[ \t]+/, ""); print; exit}' "$LOCK"
}

lock_platforms() {
  awk '!/^[#@]/ && NF >= 6 {print $2}' "$LOCK" | sort -u
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    return 1
  fi
}

python_lock() {   # the @python-lock path, absolute
  local p
  p="$(directive @python-lock)"
  [ -n "$p" ] || p="agents/requirements.lock"
  case "$p" in /*) echo "$p" ;; *) echo "$REPO_DIR/$p" ;; esac
}

# --- what the validators will pin (mirrors evals/vlib.py _resolve_tool) --------

realpath_of() {
  local p="$1" t i=0 d
  while [ -L "$p" ] && [ "$i" -lt 40 ]; do
    t="$(readlink "$p")" || return 1
    case "$t" in /*) p="$t" ;; *) p="$(dirname "$p")/$t" ;; esac
    i=$((i + 1))
  done
  d="$(cd -P "$(dirname "$p")" 2>/dev/null && pwd)" || return 1
  echo "${d%/}/$(basename "$p")"
}

root_owned() {   # the file and every ancestor: owned by root, not group/other writable
  local p
  p="$(realpath_of "$1")" || return 1
  while :; do
    [ -n "$(find "$p" -maxdepth 0 -user 0 ! -perm -g+w ! -perm -o+w 2>/dev/null)" ] || return 1
    [ "$p" = "/" ] && return 0
    p="$(dirname "$p")"
  done
}

eval_path() {    # the PATH the harness will run with: the prefix's dirs first
  local out="" d
  for d in "$PREFIX/bin" "$PREFIX/cargo/bin"; do
    [ -d "$d" ] && out="$out$d:"
  done
  echo "$out$PATH"
}

# resolve NAME -> the copy vlib.pin_toolchain would pin on eval_path, or ""
resolve() {
  local rest d first=""
  rest="$(eval_path):"
  while [ -n "$rest" ]; do
    d="${rest%%:*}"; rest="${rest#*:}"
    [ -n "$d" ] || continue
    if [ -f "$d/$1" ] && [ -x "$d/$1" ]; then
      [ -n "$first" ] || first="$d/$1"
      if root_owned "$d/$1"; then echo "$d/$1"; return; fi
    fi
  done
  echo "$first"
}

run_eval_env() {   # run a probe with the env the harness will have
  if [ -d "$PREFIX/rustup" ]; then
    env PATH="$(eval_path)" RUSTUP_HOME="$PREFIX/rustup" CARGO_HOME="$PREFIX/cargo" \
        GOTOOLCHAIN=local "$@"
  else
    env PATH="$(eval_path)" GOTOOLCHAIN=local "$@"
  fi
}

# --- check ---------------------------------------------------------------------
# Each check_<tool> sets ST (ok missing wrong system blocked) and DETAIL.

in_prefix() {   # in_prefix PATH: does PATH resolve inside the prefix?
  local r pr
  r="$(realpath_of "$1" 2>/dev/null)" || return 1
  pr="$(cd -P "$PREFIX" 2>/dev/null && pwd)" || return 1
  case "$r" in "$pr"/*) return 0 ;; esac
  return 1
}

judge_version() {   # judge_version NAME PATH GOT WANT
  if [ "$3" = "$4" ] && ! root_owned "$2" && ! in_prefix "$2"; then
    # Review 1118: the right version, but a user-writable copy outside the
    # prefix (a Homebrew go): anything running as this user, a measured
    # agent included, can replace it under the validators. `setup` installs
    # the pinned copy into the prefix, which then comes first.
    ST=wrong; DETAIL="$2 is $3 but resolves outside the eval prefix $PREFIX (user-writable); setup installs it there"
  elif [ "$3" = "$4" ]; then
    ST=ok; DETAIL="$3 ($2)"
  elif root_owned "$2"; then
    ST=blocked
    DETAIL="$2 is $3, want $4: it is root-owned, so the validators pin it ahead of the prefix (evals/vlib.py); remove it or install $4 there"
  else
    ST=wrong; DETAIL="$2 is ${3:-unreadable}, want $4"
  fi
}

check_zig() {
  local p v
  row zig || { ST=system; DETAIL="no zig pin for platform $PLAT in $LOCK"; return; }
  p="$(resolve zig)"
  if [ -z "$p" ]; then ST=missing; DETAIL="zig $R_VER not on PATH"; return; fi
  v="$(run_eval_env "$p" version 2>/dev/null)"
  judge_version zig "$p" "$v" "$R_VER"
}

check_go() {
  local p v
  row go || { ST=system; DETAIL="no go pin for platform $PLAT in $LOCK"; return; }
  p="$(resolve go)"
  if [ -z "$p" ]; then ST=missing; DETAIL="go $R_VER not on PATH"; return; fi
  v="$(GOENV=off run_eval_env "$p" version 2>/dev/null | awk '{print $3; exit}')"
  judge_version go "$p" "${v#go}" "$R_VER"
}

check_rust() {
  local p v
  row rust || { ST=system; DETAIL="no rust pin for platform $PLAT in $LOCK"; return; }
  p="$(resolve rustc)"
  if [ -z "$p" ]; then ST=missing; DETAIL="rustc $R_VER not on PATH"; return; fi
  v="$(run_eval_env "$p" --version 2>/dev/null | awk '{print $2; exit}')"
  judge_version rustc "$p" "$v" "$R_VER"
}

PY_PROBE='import sys, encodings, json, ssl, hashlib, sqlite3
print("%d.%d.%d" % sys.version_info[:3])'

check_python() {
  local vpy v want mods err lock_sha stamp home base_real
  row cpython || { ST=system; DETAIL="no cpython pin for platform $PLAT in $LOCK"; return; }
  want="$R_VER"
  vpy="$PREFIX/venv/bin/python"
  if [ ! -e "$vpy" ] && [ ! -L "$vpy" ]; then
    ST=missing; DETAIL="no eval venv at $PREFIX/venv"; return
  fi
  err="$(mktemp)"
  v="$("$vpy" -I -c "$PY_PROBE" 2>"$err")"
  if [ -z "$v" ]; then
    ST=wrong
    DETAIL="$vpy does not start: $(awk 'NF {print; exit}' "$err")"
    rm -f "$err"; return
  fi
  if [ "$v" != "$want" ]; then
    rm -f "$err"; ST=wrong; DETAIL="$vpy is python $v, want $want"; return
  fi
  # The venv's base must BE the prefix's cpython (same file once resolved): a
  # base elsewhere (Homebrew) can be rewritten under the venv, the 2026-11-16 failure.
  home="$(awk -F' = ' '$1 == "home" {print $2; exit}' "$PREFIX/venv/pyvenv.cfg" 2>/dev/null)"
  base_real="$(realpath_of "$PREFIX/cpython-$want/bin/python${want%.*}" 2>/dev/null)"
  if [ -z "$home" ] || [ ! -e "$base_real" ] \
     || [ "$(realpath_of "$home/python${want%.*}" 2>/dev/null)" != "$base_real" ]; then
    rm -f "$err"; ST=wrong
    DETAIL="the venv's base interpreter (${home:-unknown}) is not the prefix's cpython-$want"; return
  fi
  mods="$(directive @python-imports)"
  if [ -n "$mods" ]; then
    # shellcheck disable=SC2086  # module names, word-split on purpose
    if ! "$vpy" -I -c "import $(echo $mods | sed 's/ /, /g')" 2>"$err"; then
      ST=wrong; DETAIL="packages do not import: $(awk 'NF {l=$0} END {print l}' "$err")"
      rm -f "$err"; return
    fi
  fi
  rm -f "$err"
  lock_sha="$(sha256_of "$(python_lock)")"
  stamp="$(cat "$PREFIX/venv/.openbeast-lock-sha256" 2>/dev/null)"
  if [ "$stamp" != "$lock_sha" ]; then
    ST=wrong; DETAIL="the venv was not built from the current $(directive @python-lock)"; return
  fi
  ST=ok; DETAIL="python $v ($vpy)"
}

system_hint() {
  case "$PLAT" in
    darwin-*) echo "xcode-select --install (the Command Line Tools)" ;;
    *)
      if command -v apt-get >/dev/null 2>&1; then
        echo "sudo apt install build-essential clang git"
      elif command -v dnf >/dev/null 2>&1; then
        echo "sudo dnf install gcc gcc-c++ clang git"
      else
        echo "install gcc, g++ and git with the distro's package manager"
      fi ;;
  esac
}

# check_system prints its own lines (one per tool) and sets SYS_BAD
check_system() {
  local t p v
  SYS_BAD=0
  for t in $(directive @system); do
    p="$(resolve "$t")"
    if [ -z "$p" ]; then
      report system "$t" "$t not found; install it: $(system_hint) (setup never installs system tools)"
      SYS_BAD=$((SYS_BAD + 1))
    else
      v="$("$p" --version 2>/dev/null | awk 'NF {print; exit}')"
      report ok "$t" "${v:-present} ($p)"
    fi
  done
}

report() { printf '%-8s %-8s %s\n' "$1" "$2" "$3"; }

wanted() {   # wanted TOOL: is it in --only (or is --only empty)
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; esac
  return 1
}

# Review 1118: the interpreter the harness was TOLD to use. run_eval applies
# the prefix's exports, but campaign.py's --python and every child inherit
# OPENBEAST_EVAL_PY from the operator's shell: one left pointing at another
# venv (the old ~/.local/opt/openbeast-eval-venv, Homebrew-based) runs the
# agents on an interpreter this check never looked at. setup cannot fix an
# environment variable, so this is reported as `outside`.
check_env() {
  local v="${OPENBEAST_EVAL_PY:-}"
  ENV_BAD=0
  [ -n "$v" ] || return 0
  if ! in_prefix "$v"; then
    report outside env "OPENBEAST_EVAL_PY=$v resolves outside the eval prefix $PREFIX: unset it, or eval \"\$(./scripts/eval-host.sh env)\""
    ENV_BAD=1
  fi
}

cmd_check() {
  local t bad=0
  for t in zig go rust python; do
    wanted "$t" || continue
    "check_$t"
    report "$ST" "$t" "$DETAIL"
    [ "$ST" = ok ] || bad=$((bad + 1))
  done
  if [ -z "$ONLY" ]; then
    check_env
    bad=$((bad + ENV_BAD))
  fi
  if wanted system; then
    check_system
    bad=$((bad + SYS_BAD))
  fi
  if [ "$bad" -eq 0 ]; then
    echo "eval host ready ($PLAT, prefix $PREFIX)"
    return 0
  fi
  echo "eval host: $bad problem(s) ($PLAT, prefix $PREFIX); ./scripts/eval-host.sh setup installs what it can"
  return 1
}

# --- obtaining a pinned file ------------------------------------------------------

# obtain NAME -> prints the path of a sha256-verified copy of NAME's file for
# $PLAT under $PREFIX/.downloads (from there, --from, or the network)
obtain() {
  local name="$1" dl="$PREFIX/.downloads" got src d
  row "$name" || die "no $name pin for platform $PLAT in $LOCK"
  mkdir -p "$dl" || die "cannot create $dl"
  got="$dl/$R_FILE"
  if [ -f "$got" ] && [ "$(sha256_of "$got")" = "$R_SHA" ]; then
    echo "$got"; return 0
  fi
  rm -f "$got"
  if [ -n "$FROM" ]; then
    src=""
    for d in "$FROM/files/$R_ARK/$R_VER/$PLAT/$R_FILE" "$FROM/$R_FILE" "$FROM/$PLAT/$R_FILE"; do
      if [ "$R_ARK" = "-" ] && [ "$d" = "$FROM/files/-/$R_VER/$PLAT/$R_FILE" ]; then continue; fi
      if [ -f "$d" ]; then src="$d"; break; fi
    done
    if [ -z "$src" ]; then
      say "$name $R_VER ($R_FILE) is not on $FROM"
      say "  looked for: $FROM/files/$R_ARK/$R_VER/$PLAT/$R_FILE and $FROM/$R_FILE"
      say "  make a mirror on a connected box: ./scripts/eval-host.sh fetch --to <dir> --platform $PLAT"
      return 1
    fi
    say "copying $name $R_VER from $src"
    cp "$src" "$got.part" || { rm -f "$got.part"; return 1; }
  elif [ "$OFFLINE" = 1 ]; then
    say "$name $R_VER is not in $dl and --offline forbids the network (give --from DIR)"
    return 1
  else
    download "$name" "$got.part" || return 1
  fi
  verify_file "$got.part" "$R_SHA" "$name $R_VER" || return 3
  mv -f "$got.part" "$got" || return 1
  echo "$got"
}

# download NAME OUT: R_* is NAME's row. The lock's `@mirrors NAME` bases are
# tried first (<base>/<file>), then the row's own URL. A source slower than
# 10 KB/s for a minute is dropped for the next one: on 2026-11-16 ziglang.org
# served zig at 5 KB/s. Every byte is sha256-checked afterwards, whatever served it.
download() {
  local name="$1" out="$2" u
  for u in $(awk -v n="$name" -v f="$R_FILE" \
               '$1 == "@mirrors" && $2 == n {for (i = 3; i <= NF; i++) print $i "/" f}' "$LOCK") \
           "$R_URL"; do
    say "downloading $name $R_VER: $u"
    if curl -fsSL --retry 2 --connect-timeout 30 --speed-limit 10240 --speed-time 60 \
         --proto '=https,file' --proto-redir '=https' -o "$out" "$u"; then
      return 0
    fi
    rm -f "$out"; say "  failed: $u"
  done
  say "download failed: $name $R_VER from every source"
  return 1
}

verify_file() {   # verify_file FILE SHA LABEL: delete FILE and say so on a mismatch
  local have
  have="$(sha256_of "$1")" || die "neither sha256sum nor shasum is installed"
  if [ "$have" != "$2" ]; then
    rm -f "$1"
    say "SHA256 MISMATCH for $3: deleted it."
    say "  expected $2"
    say "  got      $have"
    say "  Do not fall back to another copy without finding out why (a mirror, a proxy, a swapped drive)."
    return 1
  fi
}

# unpack FILE DEST: an archive with one top directory becomes DEST
unpack() {
  local stage="$PREFIX/.stage.$$" top n
  rm -rf "$stage"; mkdir -p "$stage" || return 1
  if ! tar -xf "$1" -C "$stage"; then
    rm -rf "$stage"; say "cannot unpack $1"; return 1
  fi
  n="$(find "$stage" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
  top="$(find "$stage" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  if [ "$n" != 1 ] || [ -z "$top" ]; then
    rm -rf "$stage"; say "$1 does not hold one top directory"; return 1
  fi
  rm -rf "$2"
  mv "$top" "$2" || { rm -rf "$stage"; return 1; }
  rm -rf "$stage"
}

link_bin() {   # link_bin TARGET-relative-to-prefix NAME
  mkdir -p "$PREFIX/bin" || return 1
  [ -x "$PREFIX/$1" ] || { say "$PREFIX/$1 is missing after the install"; return 1; }
  ln -sfn "../$1" "$PREFIX/bin/$2"
}

# --- installs ------------------------------------------------------------------------

install_zig() {
  local f
  row zig || return 1             # obtain runs in a subshell: R_* must be set here
  f="$(obtain zig)" || return $?
  unpack "$f" "$PREFIX/zig-$R_VER" || return 1
  link_bin "zig-$R_VER/zig" zig
}

install_go() {
  local f
  row go || return 1             # obtain runs in a subshell: R_* must be set here
  f="$(obtain go)" || return $?
  unpack "$f" "$PREFIX/go-$R_VER" || return 1
  link_bin "go-$R_VER/bin/go" go && link_bin "go-$R_VER/bin/gofmt" gofmt
}

install_rust() {
  local f want stage inst
  row rust || die "no rust pin for platform $PLAT"
  want="$R_VER"
  if [ -n "$FROM" ] || [ "$OFFLINE" = 1 ]; then
    # Offline: the standalone installer, the ark's file.
    f="$(obtain rust)" || return $?
    stage="$PREFIX/.stage-rust.$$"
    unpack "$f" "$stage" || return 1
    inst="$PREFIX/rust-$want"
    rm -rf "$inst"
    if ! sh "$stage/install.sh" --prefix="$inst" --disable-ldconfig >/dev/null; then
      rm -rf "$stage"; say "rust's install.sh failed"; return 1
    fi
    rm -rf "$stage"
    link_bin "rust-$want/bin/rustc" rustc && link_bin "rust-$want/bin/cargo" cargo
    return
  fi
  export RUSTUP_HOME="$PREFIX/rustup" CARGO_HOME="$PREFIX/cargo"
  if [ -x "$CARGO_HOME/bin/rustup" ]; then
    say "rustup present; installing toolchain $want"
    "$CARGO_HOME/bin/rustup" toolchain install "$want" --profile minimal >&2 || return 1
    "$CARGO_HOME/bin/rustup" default "$want" >&2 || return 1
    return
  fi
  f="$(obtain rustup-init)" || return $?
  chmod +x "$f" || return 1
  say "rustup-init: toolchain $want into $PREFIX (rustup/, cargo/)"
  RUSTUP_INIT_SKIP_PATH_CHECK=yes "$f" -y --no-modify-path --profile minimal \
    --default-toolchain "$want" >&2 || return 1
}

install_python() {
  local f base want vpy lock rc wh
  row cpython || die "no cpython pin for platform $PLAT"
  want="$R_VER"
  base="$PREFIX/cpython-$want"
  if ! "$base/bin/python3" -I -c "$PY_PROBE" >/dev/null 2>&1; then
    f="$(obtain cpython)" || return $?
    unpack "$f" "$base" || return 1
  fi
  vpy="$PREFIX/venv/bin/python"
  check_python
  case "$ST:$DETAIL" in
    missing:*|"wrong:$vpy does not start"*|wrong:*"is python"*|wrong:*"base interpreter"*)
      say "building the eval venv from $base"
      rm -rf "$PREFIX/venv"
      "$base/bin/python3" -m venv "$PREFIX/venv" >&2 || { say "venv creation failed"; return 1; } ;;
  esac
  lock="$(python_lock)"
  wh="$WHEELHOUSE"
  if [ -z "$wh" ] && [ -n "$FROM" ] && [ -d "$FROM/wheels-$PLAT" ]; then wh="$FROM/wheels-$PLAT"; fi
  if [ -z "$wh" ] && { [ -n "$FROM" ] || [ "$OFFLINE" = 1 ]; }; then
    say "offline, and no wheelhouse: give --wheelhouse DIR (./scripts/pydeps.sh wheelhouse on a connected box)"
    return 1
  fi
  say "installing $(directive @python-lock) into the eval venv (--require-hashes)"
  rc=0
  if [ -n "$wh" ]; then
    OPENBEAST_PYTHON="$vpy" "$PYDEPS" install --from "$wh" >&2 || rc=$?
  else
    OPENBEAST_PYTHON="$vpy" "$PYDEPS" install >&2 || rc=$?
  fi
  if [ "$rc" -eq 3 ]; then
    say "HASH MISMATCH installing python packages: refusing (scripts/pydeps.sh)"; return 3
  fi
  [ "$rc" -eq 0 ] || { say "the python package install failed (exit $rc)"; return 1; }
  sha256_of "$lock" > "$PREFIX/venv/.openbeast-lock-sha256"
}

# One setup at a time per prefix: two harnesses starting together would both
# unpack into the same directories.
LOCKDIR=""
take_lock() {
  local i=0 pid
  LOCKDIR="$PREFIX/.setup.lock"
  while ! mkdir "$LOCKDIR" 2>/dev/null; do
    pid="$(cat "$LOCKDIR/pid" 2>/dev/null)"
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      rm -rf "$LOCKDIR"; continue                  # its owner is gone
    fi
    [ "$i" -eq 0 ] && say "another setup (pid ${pid:-?}) holds $LOCKDIR; waiting"
    i=$((i + 1))
    [ "$i" -lt 1800 ] || die "gave up waiting for $LOCKDIR"
    sleep 1
  done
  echo $$ > "$LOCKDIR/pid"
  trap 'rm -rf "$LOCKDIR"' EXIT
}

cmd_setup() {
  local t failed="" rc
  case "$PLAT" in
    darwin-arm64|linux-aarch64|linux-x86_64) ;;
    *) die "no pins for platform $PLAT in $LOCK (darwin-arm64, linux-aarch64, linux-x86_64)" ;;
  esac
  mkdir -p "$PREFIX" || die "cannot create $PREFIX"
  take_lock
  for t in zig go rust python; do
    wanted "$t" || continue
    "check_$t"
    if [ "$ST" = ok ]; then
      say "$t: ok, nothing to do"
      continue
    fi
    if [ "$ST" = blocked ] || [ "$ST" = system ]; then
      say "$t: $DETAIL"; failed="$failed $t"; continue
    fi
    say "$t: $ST ($DETAIL): installing"
    rc=0
    "install_$t" || rc=$?
    if [ "$rc" -eq 3 ]; then
      say "$t: refused on a sha256 mismatch; stopping"; exit 3
    fi
    [ "$rc" -eq 0 ] || failed="$failed $t"
  done
  echo
  rc=0
  cmd_check || rc=$?
  [ -z "$failed" ] || say "could not install:$failed"
  return "$rc"
}

# --- env -------------------------------------------------------------------------------

q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
j() { printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }

cmd_env() {
  local dirs="" d json="${1:-}" first=1
  for d in "$PREFIX/bin" "$PREFIX/cargo/bin"; do
    [ -d "$d" ] && dirs="$dirs${dirs:+:}$d"
  done
  if [ "$json" = "--json" ]; then
    printf '{"prefix": %s, "path_prepend": [' "$(j "$PREFIX")"
    local IFS=:
    for d in $dirs; do
      [ "$first" = 1 ] || printf ', '
      first=0; printf '%s' "$(j "$d")"
    done
    unset IFS
    printf '], "env": {'
    printf '"OPENBEAST_EVAL_PREFIX": %s' "$(j "$PREFIX")"
    if [ -d "$PREFIX/rustup" ]; then
      printf ', "RUSTUP_HOME": %s, "CARGO_HOME": %s' "$(j "$PREFIX/rustup")" "$(j "$PREFIX/cargo")"
    fi
    if [ -x "$PREFIX/venv/bin/python" ]; then
      printf ', "OPENBEAST_EVAL_PY": %s' "$(j "$PREFIX/venv/bin/python")"
    fi
    printf '}}\n'
    return 0
  fi
  echo "# the eval harness host's toolchains (scripts/eval-host.sh; evals/toolchains.lock)"
  [ -n "$dirs" ] && echo "export PATH=$(q "$dirs"):\"\$PATH\""
  echo "export OPENBEAST_EVAL_PREFIX=$(q "$PREFIX")"
  if [ -d "$PREFIX/rustup" ]; then
    echo "export RUSTUP_HOME=$(q "$PREFIX/rustup") CARGO_HOME=$(q "$PREFIX/cargo")"
  fi
  if [ -x "$PREFIX/venv/bin/python" ]; then
    echo "export OPENBEAST_EVAL_PY=$(q "$PREFIX/venv/bin/python")"
  fi
  return 0
}

# --- fetch -------------------------------------------------------------------------------

cmd_fetch() {
  local to="" plats="" want_wheels=0 p n bad=0 got
  while [ $# -gt 0 ]; do
    case "$1" in
      --to) [ $# -ge 2 ] || die "--to needs a directory"; to="$2"; shift 2 ;;
      --platform) [ $# -ge 2 ] || die "--platform needs a value"; plats="$2"; shift 2 ;;
      --wheels) want_wheels=1; shift ;;
      *) die "fetch: unknown argument $1" ;;
    esac
  done
  [ -n "$to" ] || die "fetch --to DIR"
  mkdir -p "$to" || die "cannot create $to"
  to="$(cd "$to" && pwd)"
  [ -n "$plats" ] || plats="$PLAT"
  [ "$plats" = all ] && plats="$(lock_platforms | tr '\n' ' ')"
  for p in $(echo "$plats" | tr ',' ' '); do
    for n in $(awk -v p="$p" '!/^[#@]/ && $2 == p {print $1}' "$LOCK"); do
      row "$n" "$p" || continue
      got="$to/$R_FILE"
      if [ -f "$got" ] && [ "$(sha256_of "$got")" = "$R_SHA" ]; then
        say "$n $R_VER ($p): already in $to"; continue
      fi
      if ! download "$n" "$got.part"; then
        bad=$((bad + 1)); continue
      fi
      verify_file "$got.part" "$R_SHA" "$n $R_VER ($p)" || exit 3
      mv -f "$got.part" "$got"
    done
  done
  if [ "$want_wheels" = 1 ]; then
    [ -x "$PREFIX/venv/bin/python" ] || die "--wheels needs the eval venv (setup first): its python picks the wheels' tags"
    OPENBEAST_PYTHON="$PREFIX/venv/bin/python" "$PYDEPS" wheelhouse "$to/wheels-$PLAT" >&2 \
      || bad=$((bad + 1))
  fi
  [ "$bad" -eq 0 ] || die "$bad download(s) failed"
  echo "mirror ready: $to (setup --from $to on the offline box)"
}

# --- main ------------------------------------------------------------------------------------

CMD="${1:-}"
[ $# -gt 0 ] && shift
case "$CMD" in
  -h|--help|help|"") ob_usage "$0"; exit 0 ;;
esac
[ -f "$LOCK" ] || die "no lock at $LOCK"
PLAT="$(platform)"
FROM=""; WHEELHOUSE=""; ONLY=""; OFFLINE=0; JSON=""; REST=()
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) [ $# -ge 2 ] || die "--prefix needs a directory"; PREFIX="$2"; shift 2 ;;
    --from|--from-ark)
      [ $# -ge 2 ] && [ -d "$2" ] || die "$1 needs an existing directory (an ark drive or a mirror)"
      FROM="$(cd "$2" && pwd)"; shift 2 ;;
    --wheelhouse)
      [ $# -ge 2 ] && [ -d "$2" ] || die "--wheelhouse needs an existing directory"
      WHEELHOUSE="$(cd "$2" && pwd)"; shift 2 ;;
    --only) [ $# -ge 2 ] || die "--only needs a list"; ONLY="$2"; shift 2 ;;
    --offline) OFFLINE=1; shift ;;
    --json) JSON="--json"; shift ;;
    *) REST+=("$1"); shift ;;
  esac
done
case "$PREFIX" in /*) ;; *) PREFIX="$(pwd)/$PREFIX" ;; esac
if [ -n "$ONLY" ]; then
  for t in $(echo "$ONLY" | tr ',' ' '); do
    case " $ALL_TOOLS " in *" $t "*) ;; *) die "--only: unknown tool '$t' ($ALL_TOOLS)" ;; esac
  done
fi

case "$CMD" in
  check) [ ${#REST[@]} -eq 0 ] || die "check: unknown argument ${REST[0]}"; cmd_check ;;
  setup) [ ${#REST[@]} -eq 0 ] || die "setup: unknown argument ${REST[0]}"; cmd_setup ;;
  env)   [ ${#REST[@]} -eq 0 ] || die "env: unknown argument ${REST[0]}"; cmd_env "$JSON" ;;
  fetch) cmd_fetch ${REST[@]+"${REST[@]}"} ;;
  *) die "unknown command '$CMD' (check | setup | env | fetch; --help)" ;;
esac
