#!/bin/bash
# Engine adapter: Project Maya (github.com/mw00/project-maya, MIT), the
# single-author tiered-expert engine for big MoE models on one GPU. The
# contract is in docs/ENGINES.md "Adapter contract"; why it exists and how it
# is brought up is in docs/WEIGHT_STREAMING.md. Sourced by lib/engine.sh;
# functions only.
#
# OPT-IN AND GATED. Max's rule (2026-11-15): a single-author engine may sit
# beside llama.cpp only if it is measurably ~1.5x llama.cpp's decode tok/s on
# this rig. profile_check and launch refuse until scripts/engine-gate.sh has
# measured both on the same weights and written a passing verdict
# (lib/engine.sh ob_engine_gate_ok). llama.cpp stays the rig's default.
#
# SUPPLY CHAIN, the same rigor as install-native.sh, for a project that ships
# no release binaries (its tags carry no assets; it builds from source):
#   - the Maya source is a git commit id, reviewed per version in
#     _ENG_MAYA_REVIEWED below. An unreviewed tag is refused. --digest <40-hex>
#     installs another commit only when the operator names it ON THE COMMAND
#     LINE: a staged folder's MANIFEST never stands in for that (install
#     --from takes its VERSION and checks its COMMIT against the table).
#   - a version is v<N>.<N>[.<N>[.<N>]] and nothing else: it names the
#     install directory, so a `../` in one (from a MANIFEST, --version or the
#     history) is refused before any path is built.
#   - Maya's setup would download llama.cpp as a GitHub archive zip (not
#     byte-stable, no checksum) and pip-install its packages unpinned and
#     unhashed. Neither happens here: llama.cpp is checked out at the commit
#     that version's setup.py pins (verified by commit id), and the packages
#     come from scripts/engines/maya/requirements.lock with
#     `pip install --require-hashes`. Maya's stamp file then tells its setup
#     they are installed. Both facts are read from the cloned setup.py and
#     compared with this table first. If Maya changes either, the install
#     refuses until someone re-reviews it.
#   - every install has an offline path: `engine.sh stage maya --version V DIR`
#     on a connected box (bare git repos of both commits + the locked wheels),
#     `engine.sh install maya --from DIR` on the closed one.
# The compile itself (10-30 min, needs the GPU and the model's pack) is a
# separate, explicit step: scripts/engines/maya/prepare.sh <profile>.
#
# Pin: ENGINE_PIN_MAYA=<tag>@<40-hex commit> in openbeast.conf. Versions sit
# side by side in ~/.local/opt/maya-<tag>; ~/.local/opt/maya links the active
# one, so a rollback is a relink (instant, offline).
#
# Release facts (read 2026-11-15, gh api repos/mw00/project-maya/...):
# latest tag v1.1.65 (2026-11-16T00:04Z UTC), a lightweight tag on commit
# c81fab6c; no release assets. Its setup.py pins llama.cpp 3cf03257 and lists
# PY_PACKAGES as below; maya.py takes --llama-dir, --gguf-dir, --no-vision,
# --setup --yes --plain --no-start, --config, --host, --port; serve/server.py
# reads MAYA_API_KEY from the environment and answers /health {"status": "ok"}.
# The repo is also cited as thomas9120/project-maya: that is a fork of
# mw00/project-maya (GitHub's parent field), not the upstream.

eng_maya_label()   { printf 'Project Maya\n'; }
eng_maya_hosts()   { printf 'rig\n'; }
eng_maya_formats() { printf 'GGUF (GLM-5.3-Flash, Maya packs), EXL3\n'; }
eng_maya_hydra_kind() { printf 'openai\n'; }

# version <TAB> maya commit <TAB> llama.cpp commit its setup.py pins.
# Add a row only after reading that version's setup.py / maya.py diff.
_ENG_MAYA_REVIEWED_DEFAULT='v1.1.65	c81fab6c9d63cf69b928f92460e0a662b28aa9ec	3cf03257f219afbe7334045ff7c6a06ac68c627d'
# The exact PY_PACKAGES line of every reviewed version: the lock covers it.
_ENG_MAYA_PYPKG_LINE='PY_PACKAGES = ["numpy", "jinja2", "regex", "pyyaml", "tqdm", "requests", "cmake", "ninja", "pillow", "psutil", "jsonschema>=4.18,<5"]'
_ENG_MAYA_PYPKG_JSON='["numpy", "jinja2", "regex", "pyyaml", "tqdm", "requests", "cmake", "ninja", "pillow", "psutil", "jsonschema>=4.18,<5"]'

_eng_maya_reviewed() { printf '%s\n' "${OB_ENGINE_MAYA_REVIEWED:-$_ENG_MAYA_REVIEWED_DEFAULT}"; }
_eng_maya_url()   { printf '%s\n' "${OB_ENGINE_MAYA_URL:-https://github.com/mw00/project-maya.git}"; }
_eng_maya_llama_url() { printf '%s\n' "${OB_ENGINE_LLAMA_URL:-https://github.com/ggml-org/llama.cpp.git}"; }
_eng_maya_opt()   { printf '%s\n' "${OB_ENGINE_MAYA_OPT:-$HOME/.local/opt}"; }
# _eng_maya_version_ok <v> — a Maya release tag's shape (v1.1.65). Checked
# before anything builds a path from a version: _eng_maya_dir is rm -rf'd.
_eng_maya_version_ok() {
  [[ "$1" =~ ^v[0-9]{1,6}(\.[0-9]{1,6}){1,3}$ ]] && return 0
  echo "'$1' is not a Maya version (v<N>.<N>[.<N>[.<N>]]) — refusing" >&2
  return 1
}
_eng_maya_dir()   { printf '%s/maya-%s\n' "$(_eng_maya_opt)" "$1"; }
_eng_maya_active() { printf '%s/maya\n' "$(_eng_maya_opt)"; }
_eng_maya_lock()  { printf '%s\n' "$REPO_DIR/scripts/engines/maya/requirements.lock"; }
_eng_maya_profiles() { printf '%s\n' "${OB_ENGINE_MAYA_PROFILES:-$REPO_DIR/scripts/engines/maya/profiles}"; }
_eng_maya_state() { printf '%s/maya\n' "$OB_ENGINE_STATE_DIR"; }

# _eng_maya_row <version|commit> — "version maya-commit llama-commit" of a
# reviewed row, by tag or by maya commit.
_eng_maya_row() {
  _eng_maya_reviewed | awk -F'\t' -v k="$1" '$1 == k || $2 == k {print $1, $2, $3; exit}'
}

eng_maya_installed() {
  local a v sha
  a="$(_eng_maya_active)"
  [[ -L "$a" && -f "$a/maya.py" ]] || return 1
  sha="$(git -C "$a" rev-parse HEAD 2>/dev/null)" || return 1
  v="$(basename "$(readlink "$a")")"; v="${v#maya-}"
  printf '%s (%s)\n' "$v" "${sha:0:12}"
}

eng_maya_latest() {
  local t; t="$(ob_engine_github_latest mw00/project-maya)" || return 1
  printf '%s -\n' "$t"
}

# _eng_maya_resolve <version> <digest> — sets _MV _MC _ML (version, maya
# commit, llama commit) from the reviewed table; a --digest names an
# unreviewed commit explicitly (its llama commit is then read from setup.py).
_eng_maya_resolve() {
  local v="$1" dig="${2:-}" row
  _MV="" _MC="" _ML=""
  if [[ -z "$v" || "$v" == latest ]]; then
    v="$(eng_maya_latest)" || return 1; v="${v%% *}"
  fi
  _eng_maya_version_ok "$v" || return 1
  row="$(_eng_maya_row "$v")"
  if [[ -n "$row" ]]; then
    read -r _MV _MC _ML <<< "$row"
    if [[ -n "$dig" && "$dig" != "$_MC" ]]; then
      echo "maya $v is reviewed at ${_MC:0:12}; --digest ${dig:0:12} names another commit — refusing" >&2; return 1
    fi
    return 0
  fi
  if [[ "$dig" =~ ^[0-9a-f]{40}$ ]]; then
    _MV="$v" _MC="$dig" _ML=""
    echo "WARNING: maya $v is not in the reviewed table of scripts/engines/maya.sh; installing commit ${dig:0:12} because you named it." >&2
    return 0
  fi
  echo "maya $v is not a reviewed version (reviewed: $(_eng_maya_reviewed | cut -f1 | paste -sd' ' -)). Review its setup.py and maya.py, add a row to _ENG_MAYA_REVIEWED in scripts/engines/maya.sh, or name the commit: --digest <40-hex>." >&2
  return 1
}

# _eng_maya_fetch <dest-repo> <commit> <url> — a one-commit fetch, verified.
_eng_maya_fetch() {
  local d="$1" c="$2" u="$3"
  [[ -d "$d/.git" ]] || git init -q "$d" || return 1
  git -C "$d" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 fetch -q --depth 1 "$u" "$c" 2>/dev/null \
    || { echo "could not fetch ${c:0:12} from $u" >&2; return 1; }
  [[ "$(git -C "$d" rev-parse FETCH_HEAD)" == "$c" ]] || { echo "$u did not serve ${c:0:12} — refusing" >&2; return 1; }
  git -C "$d" checkout -q --detach "$c" || return 1
  [[ "$(git -C "$d" rev-parse HEAD)" == "$c" ]]
}

# _eng_maya_verify_setup <dir> <llama-commit|""> — the cloned setup.py pins the
# llama.cpp commit and the package list this adapter vets. Sets _ML when it
# was empty (a --digest install).
_eng_maya_verify_setup() {
  local d="$1" want="$2" got
  got="$(sed -nE 's/^LLAMA_CPP_COMMIT = "([0-9a-f]{40})"$/\1/p' "$d/setup.py" 2>/dev/null | head -n1)"
  [[ -n "$got" ]] || { echo "$d/setup.py names no LLAMA_CPP_COMMIT — not the Maya this adapter knows; refusing" >&2; return 1; }
  if [[ -n "$want" && "$got" != "$want" ]]; then
    echo "maya's setup.py pins llama.cpp ${got:0:12}, the reviewed table says ${want:0:12} — refusing" >&2; return 1
  fi
  _ML="$got"
  grep -qxF "$_ENG_MAYA_PYPKG_LINE" "$d/setup.py" \
    || { echo "maya's setup.py changed its Python package list; scripts/engines/maya/requirements.lock no longer covers it. Re-review and regenerate the lock. Refusing." >&2; return 1; }
}

# _eng_maya_venv <dir> <wheel-dir|""> — .venv with the hash-locked packages,
# and Maya's own stamp saying so (its setup then installs nothing).
_eng_maya_venv() {
  local d="$1" wh="$2" mk pip
  mk="${OB_ENGINE_MAYA_MKVENV:-python3 -m venv}"
  # shellcheck disable=SC2086  # a command line, split on purpose
  $mk "$d/.venv" || { echo "could not create $d/.venv (python3-venv installed?)" >&2; return 1; }
  pip="${OB_ENGINE_MAYA_PIP:-$d/.venv/bin/python -m pip}"
  local -a args=(install --require-hashes --no-deps --only-binary :all: --disable-pip-version-check -q -r "$(_eng_maya_lock)")
  [[ -n "$wh" ]] && args+=(--no-index --find-links "$wh")
  # shellcheck disable=SC2086
  $pip "${args[@]}" || { echo "pip --require-hashes failed: a package did not match scripts/engines/maya/requirements.lock (or the wheel dir lacks one)" >&2; return 1; }
  printf '%s\n' "$_ENG_MAYA_PYPKG_JSON" > "$d/.venv/.strata-pip.json"
}

# install <version> <from-dir> <digest> — clone + verify + llama.cpp + venv.
eng_maya_install() {
  local v="$1" from="$2" dig="$3" d tmp mu lu wh=""
  if [[ -n "$from" ]]; then
    [[ -f "$from/MANIFEST" && -d "$from/maya.git" && -d "$from/llama.cpp.git" ]] \
      || { echo "$from is not a staged Maya (made by: engine.sh stage maya ...)" >&2; return 1; }
    # The version may come from the folder; the commit never does. A
    # MANIFEST COMMIT standing in for --digest let a folder install any
    # commit it named, under a warning that said "because you named it".
    # So: a reviewed version (its commit is then checked against the
    # MANIFEST below), or the operator's own --digest.
    [[ -n "$v" ]] || v="$(ob_engine_manifest_get "$from/MANIFEST" VERSION)"
    [[ -n "$v" ]] || { echo "$from/MANIFEST names no VERSION — pass --version" >&2; return 1; }
  elif ob_engine_offline; then
    echo "OFFLINE=true — no download; stage Maya on a connected box (engine.sh stage maya --version V DIR) and install --from DIR" >&2
    return 1
  fi
  _eng_maya_resolve "$v" "$dig" || return 1
  if [[ -n "$from" ]]; then
    [[ "$(ob_engine_manifest_get "$from/MANIFEST" COMMIT)" == "$_MC" ]] \
      || { echo "$from/MANIFEST names commit $(ob_engine_manifest_get "$from/MANIFEST" COMMIT), not $_MC — refusing" >&2; return 1; }
    mu="file://$(cd "$from" && pwd)/maya.git"; lu="file://$(cd "$from" && pwd)/llama.cpp.git"
    [[ -d "$from/wheels" ]] && wh="$(cd "$from" && pwd)/wheels"
  else
    mu="$(_eng_maya_url)"; lu="$(_eng_maya_llama_url)"
  fi
  d="$(_eng_maya_dir "$_MV")"
  tmp="$d.partial.$$"
  rm -rf "$tmp"; mkdir -p "$(dirname "$d")"
  _eng_maya_fetch "$tmp" "$_MC" "$mu" || { rm -rf "$tmp"; return 1; }
  _eng_maya_verify_setup "$tmp" "$_ML" || { rm -rf "$tmp"; return 1; }
  # llama.cpp at the commit Maya pins, by commit id: maya.py --llama-dir and
  # its setup's own check both accept a checkout with ggml/ and gguf-py/.
  if [[ -z "$from" && -d "$REPO_DIR/llama.cpp/.git" ]] && git -C "$REPO_DIR/llama.cpp" cat-file -e "$_ML^{commit}" 2>/dev/null; then
    lu="file://$REPO_DIR/llama.cpp"
  fi
  _eng_maya_fetch "$tmp/third_party/llama.cpp" "$_ML" "$lu" || { rm -rf "$tmp"; return 1; }
  [[ -f "$tmp/third_party/llama.cpp/ggml/CMakeLists.txt" && -d "$tmp/third_party/llama.cpp/gguf-py" ]] \
    || { echo "llama.cpp ${_ML:0:12} has no ggml/CMakeLists.txt or gguf-py/ — not what Maya builds against" >&2; rm -rf "$tmp"; return 1; }
  _eng_maya_venv "$tmp" "$wh" || { rm -rf "$tmp"; return 1; }
  rm -rf "$d"; mv "$tmp" "$d"
  ln -sfn "$d" "$(_eng_maya_active)"
  ENG_RESULT_VERSION="$_MV"; ENG_RESULT_REF="$_MC"
  ENG_RESULT_NOTE="llama=${_ML:0:12} lock=$(ob_engine_sha256 "$(_eng_maya_lock)" | cut -c1-12)"
  echo "Project Maya $_MV (${_MC:0:12}) installed in $d — not compiled yet: scripts/engines/maya/prepare.sh <profile>."
  echo "  It is GATED: scripts/engine-gate.sh maya --profile <profile> --serve-script <same weights>.sh before use."
}

# activate <version> <ref> — relink a version still on disk, at its commit.
eng_maya_activate() {
  local d
  _eng_maya_version_ok "$1" || return 1
  d="$(_eng_maya_dir "$1")"
  [[ -f "$d/maya.py" && "$(git -C "$d" rev-parse HEAD 2>/dev/null)" == "$2" ]] || return 1
  ln -sfn "$d" "$(_eng_maya_active)"
  ENG_RESULT_VERSION="$1"; ENG_RESULT_REF="$2"; ENG_RESULT_NOTE="relinked"
}

# stage <version> <dir> — bare one-commit repos of Maya and its llama.cpp, the
# locked wheels (pip download --require-hashes), and a MANIFEST.
# ENG_STAGE_PLATFORM / MAYA_STAGE_PYTHON pick another box's wheels
# (e.g. manylinux_2_28_x86_64 / 3.12).
eng_maya_stage() {
  local v="$1" dir="$2" r
  _eng_maya_resolve "$v" "${ENG_STAGE_DIGEST:-}" || return 1
  mkdir -p "$dir"
  for r in maya.git llama.cpp.git; do rm -rf "${dir:?}/$r"; git init -q --bare "$dir/$r"; done
  git -C "$dir/maya.git" fetch -q --depth 1 "$(_eng_maya_url)" "$_MC" || { echo "could not fetch maya ${_MC:0:12}" >&2; return 1; }
  git -C "$dir/maya.git" update-ref refs/heads/ob-stage "$_MC"
  if [[ -z "$_ML" ]]; then
    _ML="$(git -C "$dir/maya.git" show "$_MC:setup.py" | sed -nE 's/^LLAMA_CPP_COMMIT = "([0-9a-f]{40})"$/\1/p' | head -n1)"
  fi
  git -C "$dir/llama.cpp.git" fetch -q --depth 1 "$(_eng_maya_llama_url)" "$_ML" || { echo "could not fetch llama.cpp ${_ML:0:12}" >&2; return 1; }
  git -C "$dir/llama.cpp.git" update-ref refs/heads/ob-stage "$_ML"
  local -a pl=()
  [[ -n "${ENG_STAGE_PLATFORM:-}" ]] && pl+=(--platform "$ENG_STAGE_PLATFORM")
  [[ -n "${MAYA_STAGE_PYTHON:-}" ]] && pl+=(--python-version "$MAYA_STAGE_PYTHON")
  # shellcheck disable=SC2086
  ${OB_ENGINE_MAYA_PIP:-python3 -m pip} download --require-hashes --no-deps --only-binary :all: -q \
    -r "$(_eng_maya_lock)" -d "$dir/wheels" ${pl[@]+"${pl[@]}"} || { echo "wheel download failed" >&2; return 1; }
  printf 'ENGINE=maya\nVERSION=%s\nCOMMIT=%s\nLLAMA_COMMIT=%s\nLOCK_SHA256=%s\n' \
    "$_MV" "$_MC" "$_ML" "$(ob_engine_sha256 "$(_eng_maya_lock)")" > "$dir/MANIFEST"
  echo "staged Project Maya $_MV (${_MC:0:12}, llama.cpp ${_ML:0:12}) in $dir — on the closed box: scripts/engine.sh install maya --from $dir"
}

# ── profiles: scripts/engines/maya/profiles/<name>.env (KEY=VALUE, parsed,
# never sourced). GGUF names shard 1 in WEIGHTS_DIR and must be pinned in
# scripts/weights.registry; the same files the llama.cpp big profile loads,
# so the gate compares engines, not weights. ───────────────────────────────
_eng_maya_profile_path() {
  local p="$1"
  [[ "$p" == */* ]] && { printf '%s\n' "$p"; return; }
  printf '%s/%s.env\n' "$(_eng_maya_profiles)" "${p%.env}"
}
_eng_maya_pf_get() { # <file> <KEY>
  local line
  line="$(grep -E "^$2=" "$1" 2>/dev/null | tail -n1 || true)"
  line="${line#*=}"; line="${line%\"}"; line="${line#\"}"
  printf '%s\n' "$line"
}

eng_maya_scope_file() { _eng_maya_profile_path "$1"; }

# _eng_maya_profile_load <profile> — a valid profile with pinned weights.
# Sets PF_PROFILE_NAME PF_SERVED_MODEL_NAME PF_GGUF PF_CONTEXT PF_RAM_HEADROOM_GB
# PF_VRAM_RESERVE_MIB.
_eng_maya_profile_load() {
  local f k
  f="$(_eng_maya_profile_path "$1")"
  [[ -f "$f" ]] || { echo "no Maya profile $f (ls $(_eng_maya_profiles))" >&2; return 1; }
  while IFS= read -r k; do
    case "$k" in GGUF|SERVED_MODEL_NAME|CONTEXT|RAM_HEADROOM_GB|VRAM_RESERVE_MIB) ;;
      *) echo "$f: unknown key '$k'" >&2; return 1 ;; esac
  done < <(sed -nE 's/^([A-Z][A-Z0-9_]*)=.*/\1/p' "$f")
  PF_PROFILE_NAME="$(basename "$f" .env)"
  PF_GGUF="$(_eng_maya_pf_get "$f" GGUF)"
  PF_SERVED_MODEL_NAME="$(_eng_maya_pf_get "$f" SERVED_MODEL_NAME)"
  PF_CONTEXT="$(_eng_maya_pf_get "$f" CONTEXT)"
  PF_RAM_HEADROOM_GB="$(_eng_maya_pf_get "$f" RAM_HEADROOM_GB)"
  PF_VRAM_RESERVE_MIB="$(_eng_maya_pf_get "$f" VRAM_RESERVE_MIB)"
  [[ "$PF_GGUF" =~ ^[A-Za-z0-9._-]+-00001-of-[0-9]{5}\.gguf$ || "$PF_GGUF" =~ ^[A-Za-z0-9._-]+\.gguf$ ]] \
    || { echo "$f: GGUF='$PF_GGUF' is not a weight file name" >&2; return 1; }
  [[ -n "$PF_SERVED_MODEL_NAME" ]] || { echo "$f: SERVED_MODEL_NAME is required" >&2; return 1; }
  [[ "$PF_CONTEXT" =~ ^(8192|32768|65536|131072)$ ]] || { echo "$f: CONTEXT must be one Maya accepts for GLM (8192, 32768, 65536, 131072)" >&2; return 1; }
  [[ "$PF_RAM_HEADROOM_GB" =~ ^[0-9]+$ && "$PF_RAM_HEADROOM_GB" -ge 6 ]] \
    || { echo "$f: RAM_HEADROOM_GB must be a whole number >= 6 (Maya pins all other free RAM)" >&2; return 1; }
  # No `{64}` interval: mawk 1.3.4-20200120 (Debian 12, Ubuntu 22.04) reads
  # the braces literally, so the pin never matched there.
  awk -F'\t' -v n="$PF_GGUF" '$0 !~ /^#/ && $3 == n && length($1) == 64 && $1 !~ /[^0-9a-f]/ {f = 1} END {exit !f}' \
    "$REPO_DIR/scripts/weights.registry" \
    || { echo "$f: $PF_GGUF has no sha256 pin in scripts/weights.registry" >&2; return 1; }
  [[ "$(ob_engine_host_class)" == rig ]] || { echo "Maya profiles run on the rig (this host is a $(ob_engine_host_class))" >&2; return 1; }
}

# profile_check <profile> — the profile is valid AND the engine gate is open.
eng_maya_profile_check() { _eng_maya_profile_load "$1" && ob_engine_gate_ok maya; }

# profile_locate <profile> — SP_LOCATE_RC 0 when every shard is in WEIGHTS_DIR.
eng_maya_profile_locate() {
  local w f n i
  SP_LOCATE_RC=3 SP_MODEL_DIR=""
  _eng_maya_profile_load "$1" >/dev/null || return 0
  w="$(_eng_maya_weights_dir)"
  f="$w/$PF_GGUF"
  if [[ "$PF_GGUF" =~ -00001-of-([0-9]{5})\.gguf$ ]]; then
    n=$((10#${BASH_REMATCH[1]}))
    for (( i = 1; i <= n; i++ )); do
      [[ -f "${f%-00001-of-*}-$(printf '%05d' "$i")-of-${BASH_REMATCH[1]}.gguf" ]] || return 0
    done
  else
    [[ -f "$f" ]] || return 0
  fi
  SP_LOCATE_RC=0 SP_MODEL_DIR="$w"
}
_eng_maya_weights_dir() {
  local w
  w="$( (source "$REPO_DIR/scripts/lib/weights.sh" >/dev/null 2>&1; printf '%s' "${WEIGHTS_DIR:-}") || true )"
  printf '%s\n' "${w:-$REPO_DIR/weights}"
}

# The config Maya's setup wrote for a profile (prepare.sh records it).
_eng_maya_config() { cat "$(_eng_maya_state)/$1.config" 2>/dev/null || true; }

# launch — foreground; maya.py ends its server and engine with it.
eng_maya_launch() {
  local profile home cfg run host port
  profile="${OPENBEAST_ENGINE_PROFILE:-$(ob_engine_conf_get ENGINE_PROFILE)}"
  [[ -n "$profile" ]] || { echo "ENGINE_PROFILE is not set — engine.sh use maya --profile NAME" >&2; return 1; }
  eng_maya_profile_check "$profile" || return 1
  home="$(_eng_maya_active)"
  cfg="$(_eng_maya_config "$PF_PROFILE_NAME")"
  if [[ "${ENG_PRINT:-0}" != 1 ]]; then
    [[ -f "$home/maya.py" ]] || { echo "Maya is not installed — engine.sh install maya" >&2; return 1; }
    [[ -f "$cfg" ]] || { echo "Maya has not been prepared for $PF_PROFILE_NAME — scripts/engines/maya/prepare.sh $PF_PROFILE_NAME" >&2; return 1; }
  fi
  read -r host port < <(ob_engine_url_hostport "${INFERENCE_URL:-http://127.0.0.1:8080}")
  run="${RUN_DIR:-$REPO_DIR/.run}/maya-$PF_PROFILE_NAME.json"
  local -a CMD=("$home/.venv/bin/python" "$home/maya.py" --config "$run" --plain --host "$host" --port "$port")
  local -a ENV=(MAYA_UPDATE_CHECK=0 HF_HUB_OFFLINE=1 "MAYA_GLM_RAM_HEADROOM_GB=$PF_RAM_HEADROOM_GB")
  if [[ "${ENG_PRINT:-0}" == 1 ]]; then
    local a out=""
    for a in env "${ENV[@]}" ${LLAMA_API_KEY:+"MAYA_API_KEY=<from LLAMA_API_KEY, environment only>"} "${CMD[@]}"; do
      out+="$(printf '%q' "$a") "
    done
    printf '%s\n' "${out% }"
    return 0
  fi
  # A run copy of the prepared config (0600): host and port of this stack.
  # The key never goes into it or onto argv; server.py reads MAYA_API_KEY.
  ( umask 077; mkdir -p "$(dirname "$run")"
    python3 - "$cfg" "$run.tmp" "$host" "$port" <<'PY' && mv -f "$run.tmp" "$run"
import json, sys
c = json.load(open(sys.argv[1]))
c["host"], c["port"] = sys.argv[3], int(sys.argv[4])
c.pop("api_key", None)
json.dump(c, open(sys.argv[2], "w"), indent=1)
PY
  ) || { echo "could not write $run from $cfg" >&2; return 1; }
  echo "Project Maya $(eng_maya_installed 2>/dev/null || echo '?'): $PF_PROFILE_NAME ($PF_SERVED_MODEL_NAME) on http://$host:$port"
  if [[ -n "${LLAMA_API_KEY:-}" ]]; then
    exec env "${ENV[@]}" MAYA_API_KEY="$LLAMA_API_KEY" "${CMD[@]}"
  fi
  exec env "${ENV[@]}" "${CMD[@]}"
}

# ready <url> — /health 200 with status "ok" and the model loaded.
eng_maya_ready() {
  local body
  body="$(curl -fsS -m 3 "${1%/}/health" 2>/dev/null)" || return 1
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get("status") == "ok" and not d.get("unloaded") else 1)' "$body" 2>/dev/null
}

# stop — the recorded managed launcher of THIS checkout, by pid + name.
eng_maya_stop() {
  local pf="${RUN_DIR:-$REPO_DIR/.run}/llama.pid" pid
  pid="$(cat "$pf" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  if ps -o args= -p "$pid" 2>/dev/null | grep -qE 'maya\.py|engine-serve\.sh'; then
    kill "$pid" 2>/dev/null && echo "Project Maya stopped (pid $pid)."
  fi
  return 0
}
