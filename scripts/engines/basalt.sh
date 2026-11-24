#!/bin/bash
# shellcheck disable=SC2034  # ENG_RESULT_*, PF_* and SP_* are read by lib/engine.sh and scripts/engine.sh
# Engine adapter: Basalt (github.com/jesdga95/basalt, MIT), the C++/CUDA
# engine with a Python stdlib OpenAI server that serves Inkling 2 Mini on the
# RTX 5090. The contract is in docs/ENGINES.md "Adapter contract"; why Basalt
# and what was reviewed: docs/reports/STRATA_INKLING_5090-2026-11-23.md §11
# and §15. Sourced by lib/engine.sh; functions only.
#
# THE RTX 5090 DEFAULT since 2026-11-24 (Max; DECISIONS-2026-11-24 §10): the
# stock Inkling 2 Mini pack, profile inkling2-mini-stock, the serve of board
# row INK-T2-STOCK. The runtime fallback of an unset conf stays llama.cpp
# (conf.sh); `engine.sh use default` on an RTX 5090 picks this engine
# (lib/engine.sh ob_engine_default) and this adapter's default profile.
#
# What closes the Strata report §15.8 "before default" items:
#   1. Source at a reviewed commit, built offline. Only a version in
#      _ENG_BASALT_REVIEWED is built (v0.5.7 = 400b411c…); --digest names
#      another commit only on the command line. The compile runs in the env
#      image with --network none. Offline: `engine.sh stage basalt` writes a
#      bare one-commit repo + the docker-saved env image + a MANIFEST with
#      their sha256 and image id; `install --from` re-verifies all of it. The
#      source is also in beast-ark (agents/ark/catalog.toml entry "basalt").
#   2. The #588 fix (upstream PR #632) as a local patch: every file
#      scripts/engines/patches/basalt-pr632-*.patch present in this checkout
#      is applied after the checkout (git apply --check first), and its name
#      and sha256 go into the build record and the history note. None present
#      = the reviewed pin exactly (the measured INK-T2-STOCK binary's source).
#      ENGINE_BASALT_PATCHES=none builds the pin even when a patch is there.
#   3. The Python side: no venv; the server runs the env image's distro
#      python3, whose six server packages are pinned to exact versions in
#      scripts/engines/basalt/Dockerfile (an Ubuntu snapshot fixes the rest)
#      and re-checked in the built image here (_ENG_BASALT_PY_PINS).
#   4. Hardened serve (eng_basalt_launch): a no-NAT bridge plus
#      --dns 127.0.0.1, PROVEN at every launch by a probe container that must
#      fail to resolve public names and fail to connect out (else no launch);
#      the port published on 127.0.0.1 only; read-only root, tree, weights
#      and key; no host home (HOME=/tmp); --cap-drop ALL; no-new-privileges;
#      a non-root uid; --pids-limit and --memory; /tmp noexec.
# Behind the gate like llama-server: the engine listens on INFERENCE_URL's
# port (loopback only, enforced), beast-gate / the router forward to it, and
# LLAMA_API_KEY reaches Basalt as a 0600 key file mounted read-only
# (--api-key-file), never on argv. No key = loopback-only and keyless, as a
# default llama-server.
#
# Pin: ENGINE_PIN_BASALT=<tag>@<40-hex commit>. Builds sit side by side in
# ~/.local/opt/basalt-<tag>[+<patch tag>]; ~/.local/opt/basalt links the
# active one, so a rollback is a relink (instant, offline). Each build dir
# holds OPENBEAST_BUILD (commit, patches, env image id, bin sha256).

eng_basalt_label()   { printf 'Basalt\n'; }
eng_basalt_hosts()   { printf 'rig\n'; }
eng_basalt_formats() { printf 'Basalt pack (.basalt: Inkling 2 Mini NVFP4-MX8), sm_120\n'; }
eng_basalt_hydra_kind() { printf 'openai\n'; }

# version <TAB> commit. Add a row only after a review like the Strata report §15.
_ENG_BASALT_REVIEWED_DEFAULT='v0.5.7	400b411c58031d064812ea21a259b2381acdaa3e'
# The server's Python packages, exact, as the measured image held them
# (Strata report §15.3, dpkg-query -W in ob-strata-env:2026-11-23).
_ENG_BASALT_PY_PINS='python3=3.12.3-0ubuntu2.1
python3-jinja2=3.1.2-1ubuntu1.3
python3-markupsafe=2.1.5-1build2
python3-regex=0.1.20221031-2build1
python3-psutil=5.9.8-2build2
python3-pil=10.2.0-1ubuntu1.5'
# The hardening values (eng_basalt_launch). Steady state of the measured
# serve, read with `docker stats` on 2026-11-24 during I_UP8k: 16 PIDs,
# 2.84 GiB. The limits leave 16x and ~11x of that; the load peak was not
# measured. Raised (never removed) through ENGINE_BASALT_PIDS / _MEMORY.
_ENG_BASALT_PIDS_DEFAULT=256
_ENG_BASALT_MEMORY_DEFAULT=32g
_ENG_BASALT_TMPFS='/tmp:rw,noexec,nosuid,nodev,size=8g'
_ENG_BASALT_NET='openbeast-basalt-net'

_eng_basalt_reviewed() { printf '%s\n' "${OB_ENGINE_BASALT_REVIEWED:-$_ENG_BASALT_REVIEWED_DEFAULT}"; }
_eng_basalt_url()   { printf '%s\n' "${OB_ENGINE_BASALT_URL:-https://github.com/jesdga95/basalt.git}"; }
_eng_basalt_opt()   { printf '%s\n' "${OB_ENGINE_BASALT_OPT:-$HOME/.local/opt}"; }
_eng_basalt_docker() { "${OB_ENGINE_DOCKER:-docker}" "$@"; }
_eng_basalt_dir()   { printf '%s/basalt-%s\n' "$(_eng_basalt_opt)" "$1"; }
_eng_basalt_active() { printf '%s/basalt\n' "$(_eng_basalt_opt)"; }
_eng_basalt_profiles() { printf '%s\n' "${OB_ENGINE_BASALT_PROFILES:-$REPO_DIR/scripts/engines/basalt/profiles}"; }
_eng_basalt_dockerfile() { printf '%s\n' "$REPO_DIR/scripts/engines/basalt/Dockerfile"; }
_eng_basalt_patch_dir() { printf '%s\n' "${OB_ENGINE_BASALT_PATCH_DIR:-$REPO_DIR/scripts/engines/patches}"; }
_eng_basalt_cname() { ob_engine_container_name basalt; }
_eng_basalt_conf() { # <KEY> — env OPENBEAST_<KEY>, else openbeast.conf
  local v="OPENBEAST_$1"
  printf '%s\n' "${!v:-$(ob_engine_conf_get "$1")}"
}
# The env image's tag follows the Dockerfile's bytes: a changed Dockerfile is a new image.
_eng_basalt_image_tag() {
  printf 'openbeast-basalt-env:%s\n' "$(ob_engine_sha256 "$(_eng_basalt_dockerfile)" | cut -c1-12)"
}

# _eng_basalt_version_ok <v> — a Basalt release tag's shape (v0.5.7). Checked
# before a version names a directory (one that rm -rf may touch).
_eng_basalt_version_ok() {
  [[ "$1" =~ ^v[0-9]{1,6}(\.[0-9]{1,6}){1,3}$ ]] && return 0
  echo "'$1' is not a Basalt version (v<N>.<N>[.<N>[.<N>]]) — refusing" >&2
  return 1
}

# _eng_basalt_row <version|commit> — "version commit" of a reviewed row.
_eng_basalt_row() {
  _eng_basalt_reviewed | awk -F'\t' -v k="$1" '$1 == k || $2 == k {print $1, $2; exit}'
}

# _eng_basalt_build_get <dir> <KEY> — a field of a build's OPENBEAST_BUILD.
_eng_basalt_build_get() { ob_engine_manifest_get "$1/OPENBEAST_BUILD" "$2"; }

eng_basalt_installed() {
  local a v c p
  a="$(_eng_basalt_active)"
  [[ -L "$a" && -f "$a/OPENBEAST_BUILD" && -x "$a/bin/basalt" ]] || return 1
  v="$(_eng_basalt_build_get "$a" VERSION)"; c="$(_eng_basalt_build_get "$a" COMMIT)"
  p="$(_eng_basalt_build_get "$a" PATCH_TAG)"
  [[ -n "$v" && "$c" =~ ^[0-9a-f]{40}$ ]] || return 1
  printf '%s (%s%s)\n' "$v" "${c:0:12}" "${p:+, +$p}"
}

eng_basalt_latest() {
  local t; t="$(ob_engine_github_latest jesdga95/basalt)" || return 1
  printf '%s -\n' "$t"
}

# _eng_basalt_resolve <version> <digest> — sets _BV _BC from the reviewed
# table; --digest names an unreviewed commit explicitly, on the command line.
_eng_basalt_resolve() {
  local v="$1" dig="${2:-}" row
  _BV="" _BC=""
  if [[ -z "$v" || "$v" == latest ]]; then
    v="$(_eng_basalt_reviewed | tail -n1 | cut -f1)"
  fi
  _eng_basalt_version_ok "$v" || return 1
  row="$(_eng_basalt_row "$v")"
  if [[ -n "$row" ]]; then
    read -r _BV _BC <<< "$row"
    if [[ -n "$dig" && "$dig" != "$_BC" ]]; then
      echo "basalt $v is reviewed at ${_BC:0:12}; --digest ${dig:0:12} names another commit — refusing" >&2; return 1
    fi
    return 0
  fi
  if [[ "$dig" =~ ^[0-9a-f]{40}$ ]]; then
    _BV="$v" _BC="$dig"
    echo "WARNING: basalt $v is not in the reviewed table of scripts/engines/basalt.sh; building commit ${dig:0:12} because you named it." >&2
    return 0
  fi
  echo "basalt $v is not a reviewed version (reviewed: $(_eng_basalt_reviewed | cut -f1 | paste -sd' ' -)). Review it (the Strata report §15 is the template), add a row to _ENG_BASALT_REVIEWED in scripts/engines/basalt.sh, or name the commit: --digest <40-hex>." >&2
  return 1
}

# _eng_basalt_fetch <dest> <commit> <url> — a one-commit checkout, verified by id.
_eng_basalt_fetch() {
  local d="$1" c="$2" u="$3"
  [[ -d "$d/.git" ]] || git init -q "$d" || return 1
  git -C "$d" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 fetch -q --depth 1 "$u" "$c" 2>/dev/null \
    || { echo "could not fetch ${c:0:12} from $u" >&2; return 1; }
  [[ "$(git -C "$d" rev-parse FETCH_HEAD)" == "$c" ]] || { echo "$u did not serve ${c:0:12} — refusing" >&2; return 1; }
  git -C "$d" checkout -q --detach "$c" || return 1
  [[ "$(git -C "$d" rev-parse HEAD)" == "$c" && -z "$(git -C "$d" status --porcelain)" ]]
}

# ── the #588 fix: the PR #632 patch hook ───────────────────────────────────
# _eng_basalt_patches — the patch files a build applies, sorted, one per line:
# scripts/engines/patches/basalt-pr632-*.patch when present, nothing under
# ENGINE_BASALT_PATCHES=none. From 2026-11-24 (Max) main carries
# basalt-pr632-55f9be17.patch (upstream PR #632 head 55f9be17, Python server
# only; review: docs/reports/STRATA_INKLING_5090-2026-11-23.md section 16),
# so a build is the reviewed pin plus it. A reviewed file in this checkout,
# never a download.
_eng_basalt_patches() {
  local mode f
  mode="$(_eng_basalt_conf ENGINE_BASALT_PATCHES)"
  [[ "${mode:-auto}" == none ]] && return 0
  [[ "${mode:-auto}" == auto ]] || { echo "ENGINE_BASALT_PATCHES='$mode' is not auto or none" >&2; return 1; }
  for f in "$(_eng_basalt_patch_dir)"/basalt-pr632-*.patch; do
    [[ -f "$f" ]] && printf '%s\n' "$f"
  done
  return 0
}

# _eng_basalt_patch_tag <files...> — "pr632-<12 hex of their sha256s>", or empty.
_eng_basalt_patch_tag() {
  [[ $# -gt 0 ]] || return 0
  local f all=""
  for f in "$@"; do all+="$(ob_engine_sha256 "$f")"; done
  printf 'pr632-%s\n' "$(printf '%s' "$all" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -c1-12)"
}

# _eng_basalt_apply <tree> <files...> — apply each patch, checked first. Sets _BP (record line).
_eng_basalt_apply() {
  local t="$1" f; shift
  _BP=""
  for f in "$@"; do
    git -C "$t" apply --check "$f" 2>/dev/null \
      || { echo "patch $(basename "$f") does not apply to $(git -C "$t" rev-parse --short=12 HEAD) — refusing (a patch is written for one commit)" >&2; return 1; }
    git -C "$t" apply "$f" || return 1
    _BP+="$(basename "$f")=$(ob_engine_sha256 "$f") "
    echo "applied $(basename "$f") (sha256 $(ob_engine_sha256 "$f" | cut -c1-12))"
  done
  _BP="${_BP% }"
}

# ── the env image ──────────────────────────────────────────────────────────
# _eng_basalt_image_check <image> — the server's packages are the pinned
# versions (the build record of the image, /usr/share/openbeast-basalt-env.dpkg).
_eng_basalt_image_check() {
  local img="$1" have want bad=""
  have="$(_eng_basalt_docker run --rm --network none --entrypoint cat "$img" /usr/share/openbeast-basalt-env.dpkg 2>/dev/null)" \
    || { echo "could not read the package list of $img" >&2; return 1; }
  while IFS= read -r want; do
    grep -qxF "$want" <<< "$have" || bad+=" $want"
  done <<< "$_ENG_BASALT_PY_PINS"
  [[ -z "$bad" ]] || { echo "the env image $img does not hold the pinned server packages:$bad — refusing" >&2; return 1; }
}

# _eng_basalt_image_build — build the env image (network: base pull + apt), check it. Sets _BI (image id).
_eng_basalt_image_build() {
  local tag
  tag="$(_eng_basalt_image_tag)"
  ob_engine_offline && { echo "OFFLINE=true — cannot build $tag (it needs apt); stage it on a connected box (engine.sh stage basalt) and install --from DIR" >&2; return 1; }
  _eng_basalt_docker build -t "$tag" -f "$(_eng_basalt_dockerfile)" "$(dirname "$(_eng_basalt_dockerfile)")" \
    || { echo "docker build of $tag failed" >&2; return 1; }
  _BI="$(_eng_basalt_docker image inspect --format '{{.Id}}' "$tag")" || return 1
  _eng_basalt_image_check "$_BI"
}

# _eng_basalt_image_load <tar> <sha256> <image id> — offline: the tarball is
# what the MANIFEST says, and docker reads back that image id. Sets _BI.
_eng_basalt_image_load() {
  local tar="$1" sum="$2" id="$3" got
  [[ "$(ob_engine_sha256 "$tar")" == "$sum" ]] || { echo "checksum mismatch: $tar" >&2; return 1; }
  _eng_basalt_docker load -i "$tar" >/dev/null || { echo "docker load $tar failed" >&2; return 1; }
  got="$(_eng_basalt_docker image inspect --format '{{.Id}}' "$id" 2>/dev/null || true)"
  [[ "$got" == "$id" ]] || { echo "after docker load, image $id is not on this box — the tarball is not what its MANIFEST says" >&2; return 1; }
  _BI="$id"
  _eng_basalt_image_check "$_BI"
}

# _eng_basalt_compile <tree> <image> — the measured build (rig
# scripts/build-basalt.sh, Strata report §11.1): Inkling only, sm_120,
# Release, no network, unprivileged. Basalt writes bin/ into the tree.
_eng_basalt_compile() {
  local t="$1" img="$2" uid gid
  uid="$(id -u)"; gid="$(id -g)"
  # shellcheck disable=SC2016  # $(nproc) expands in the container
  _eng_basalt_docker run --rm --network none --user "$uid:$gid" --cap-drop ALL --security-opt no-new-privileges \
    -e HOME=/tmp -v "$t:/src" -w /src "$img" bash -c '
      set -e
      cmake -G Ninja -S /src -B /src/build -DCMAKE_BUILD_TYPE=Release -DBASALT_MODELS=inkling2 -DCMAKE_CUDA_ARCHITECTURES=120
      cmake --build /src/build -j "$(nproc)"
      nvcc --version | tail -n 2' \
    || { echo "the Basalt build failed (log above)" >&2; return 1; }
  [[ -x "$t/bin/basalt" ]] || { echo "the build left no bin/basalt" >&2; return 1; }
}

# install <version> <from-dir> <digest> — checkout + verify + patches + env
# image + offline compile. Sets ENG_RESULT_*.
eng_basalt_install() {
  local v="$1" from="$2" dig="$3" d tmp url m tag="" note
  local -a patches=()
  if [[ -n "$from" ]]; then
    m="$from/MANIFEST"
    [[ "$(ob_engine_manifest_get "$m" ENGINE)" == basalt && -d "$from/basalt.git" ]] \
      || { echo "$from is not a staged Basalt (made by: engine.sh stage basalt ...)" >&2; return 1; }
    # The version may come from the folder; the commit never does (maya.sh's rule).
    [[ -n "$v" ]] || v="$(ob_engine_manifest_get "$m" VERSION)"
  elif ob_engine_offline; then
    echo "OFFLINE=true — no download; stage Basalt on a connected box (engine.sh stage basalt --version V DIR) and install --from DIR" >&2
    return 1
  fi
  _eng_basalt_resolve "$v" "$dig" || return 1
  while IFS= read -r m; do [[ -n "$m" ]] && patches+=("$m"); done < <(_eng_basalt_patches) || return 1
  tag="$(_eng_basalt_patch_tag ${patches[@]+"${patches[@]}"})"
  d="$(_eng_basalt_dir "$_BV${tag:++$tag}")"
  tmp="$d.partial.$$"
  rm -rf "$tmp"; mkdir -p "$(dirname "$d")"
  if [[ -n "$from" ]]; then
    m="$from/MANIFEST"
    [[ "$(ob_engine_manifest_get "$m" COMMIT)" == "$_BC" ]] \
      || { echo "$m names commit $(ob_engine_manifest_get "$m" COMMIT), not $_BC — refusing" >&2; return 1; }
    url="file://$(cd "$from" && pwd)/basalt.git"
  else
    url="$(_eng_basalt_url)"
  fi
  _eng_basalt_fetch "$tmp" "$_BC" "$url" || { rm -rf "$tmp"; return 1; }
  _BP=""
  if [[ ${#patches[@]} -gt 0 ]]; then
    _eng_basalt_apply "$tmp" "${patches[@]}" || { rm -rf "$tmp"; return 1; }
  fi
  if [[ -n "$from" ]]; then
    local itar isum iid
    itar="$(ob_engine_manifest_get "$m" IMAGE_TAR)"; isum="$(ob_engine_manifest_get "$m" IMAGE_TAR_SHA256)"
    iid="$(ob_engine_manifest_get "$m" IMAGE_ID)"
    [[ "$itar" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\.tar$ && "$isum" =~ ^[0-9a-f]{64}$ && "$iid" =~ ^sha256:[0-9a-f]{64}$ ]] \
      || { echo "$m: IMAGE_TAR / IMAGE_TAR_SHA256 / IMAGE_ID are missing or not their shape" >&2; rm -rf "$tmp"; return 1; }
    [[ -f "$from/$itar" ]] || { echo "$m names $itar, which is not in $from" >&2; rm -rf "$tmp"; return 1; }
    _eng_basalt_image_load "$from/$itar" "$isum" "$iid" || { rm -rf "$tmp"; return 1; }
  else
    _eng_basalt_image_build || { rm -rf "$tmp"; return 1; }
  fi
  _eng_basalt_compile "$tmp" "$_BI" || { rm -rf "$tmp"; return 1; }
  printf 'VERSION=%s\nCOMMIT=%s\nPATCH_TAG=%s\nPATCHES=%s\nIMAGE_ID=%s\nBIN_SHA256=%s\nDOCKERFILE_SHA256=%s\nBUILT=%s\n' \
    "$_BV" "$_BC" "$tag" "$_BP" "$_BI" "$(ob_engine_sha256 "$tmp/bin/basalt")" \
    "$(ob_engine_sha256 "$(_eng_basalt_dockerfile)")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$tmp/OPENBEAST_BUILD"
  if [[ -d "$d" ]] && _eng_basalt_running_from "$d"; then
    echo "Basalt $_BV is serving from $d — stop it first (./stop.sh), then reinstall" >&2
    rm -rf "$tmp"; return 1
  fi
  local old=""
  if [[ -e "$d" ]]; then old="$d.old.$$"; rm -rf "$old"; mv "$d" "$old" || { rm -rf "$tmp"; return 1; }; fi
  mv "$tmp" "$d" || { [[ -n "$old" ]] && mv "$old" "$d"; rm -rf "$tmp"; return 1; }
  ln -sfn "$d" "$(_eng_basalt_active)"
  [[ -n "$old" ]] && rm -rf "$old"
  note="image=$_BI bin=$(_eng_basalt_build_get "$d" BIN_SHA256 | cut -c1-12)${tag:+ patch=$tag}"
  ENG_RESULT_VERSION="$_BV"; ENG_RESULT_REF="$_BC"; ENG_RESULT_NOTE="$note"
  echo "Basalt $_BV (${_BC:0:12}${tag:+, +$tag}) built in $d; bin/basalt sha256 $(_eng_basalt_build_get "$d" BIN_SHA256 | cut -c1-16)…"
}

# _eng_basalt_running_from <dir> — this checkout's managed container serves that tree.
_eng_basalt_running_from() {
  local src
  src="$(_eng_basalt_docker inspect -f '{{range .Mounts}}{{if eq .Destination "/opt/basalt"}}{{.Source}}{{end}}{{end}}' "$(_eng_basalt_cname)" 2>/dev/null || true)"
  [[ -n "$src" && "$(cd "$src" 2>/dev/null && pwd -P)" == "$(cd "$1" 2>/dev/null && pwd -P)" ]]
}

# activate <version> <ref> — relink a build still on disk at that commit
# (the newest such build, patched or not, whose env image is still here).
eng_basalt_activate() {
  local v="$1" ref="$2" d
  _eng_basalt_version_ok "$v" || return 1
  for d in "$(_eng_basalt_dir "$v")" "$(_eng_basalt_dir "$v")"+*; do
    [[ -f "$d/OPENBEAST_BUILD" && -x "$d/bin/basalt" ]] || continue
    [[ "$(_eng_basalt_build_get "$d" COMMIT)" == "$ref" ]] || continue
    _eng_basalt_docker image inspect "$(_eng_basalt_build_get "$d" IMAGE_ID)" >/dev/null 2>&1 || continue
    ln -sfn "$d" "$(_eng_basalt_active)"
    ENG_RESULT_VERSION="$v"; ENG_RESULT_REF="$ref"; ENG_RESULT_NOTE="relinked $(basename "$d")"
    return 0
  done
  return 1
}

# stage <version> <dir> — a bare one-commit repo of the source, the env image
# (docker save) and a MANIFEST, for install --from on a closed box. The
# patches are not staged: they travel in the checkout.
eng_basalt_stage() {
  local v="$1" dir="$2" t
  _eng_basalt_resolve "$v" "${ENG_STAGE_DIGEST:-}" || return 1
  mkdir -p "$dir"
  rm -rf "${dir:?}/basalt.git"; git init -q --bare "$dir/basalt.git"
  git -C "$dir/basalt.git" fetch -q --depth 1 "$(_eng_basalt_url)" "$_BC" || { echo "could not fetch basalt ${_BC:0:12}" >&2; return 1; }
  [[ "$(git -C "$dir/basalt.git" rev-parse FETCH_HEAD)" == "$_BC" ]] || { echo "the remote did not serve ${_BC:0:12}" >&2; return 1; }
  git -C "$dir/basalt.git" update-ref refs/heads/ob-stage "$_BC"
  _eng_basalt_image_build || return 1
  t="openbeast-basalt-env-$(_eng_basalt_image_tag | cut -d: -f2)-$(ob_engine_arch).tar"
  _eng_basalt_docker save -o "$dir/$t" "$_BI" || return 1
  printf 'ENGINE=basalt\nVERSION=%s\nCOMMIT=%s\nIMAGE_TAR=%s\nIMAGE_TAR_SHA256=%s\nIMAGE_ID=%s\nDOCKERFILE_SHA256=%s\nARCH=%s\n' \
    "$_BV" "$_BC" "$t" "$(ob_engine_sha256 "$dir/$t")" "$_BI" "$(ob_engine_sha256 "$(_eng_basalt_dockerfile)")" "$(ob_engine_arch)" \
    > "$dir/MANIFEST"
  echo "staged Basalt $_BV (${_BC:0:12}) and its env image in $dir — on the closed box: scripts/engine.sh install basalt --from $dir"
  echo "  the weights travel separately: scripts/fetch-weight.sh Inkling-2-Mini-NVFP4-MX8.basalt (or copy it, then verify-weights.sh --file … --deep)"
}

# ── profiles: scripts/engines/basalt/profiles/<name>.env (KEY=VALUE, parsed,
# never sourced). PACK names a file in WEIGHTS_DIR pinned in
# scripts/weights.registry (scripts/verify-weights.sh checks it). ─────────────
_ENG_BASALT_PROFILE_DEFAULT=inkling2-mini-stock
eng_basalt_default_profile() { printf '%s\n' "$_ENG_BASALT_PROFILE_DEFAULT"; }

_eng_basalt_profile_path() {
  local p="$1"
  [[ "$p" == */* ]] && { printf '%s\n' "$p"; return; }
  printf '%s/%s.env\n' "$(_eng_basalt_profiles)" "${p%.env}"
}
_eng_basalt_pf_get() { # <file> <KEY>
  local line
  line="$(grep -E "^$2=" "$1" 2>/dev/null | tail -n1 || true)"
  line="${line#*=}"; line="${line%\"}"; line="${line#\"}"
  printf '%s\n' "$line"
}

# _eng_basalt_registry_row <file> — "sha256 bytes" of a registry pin, or fail.
_eng_basalt_registry_row() {
  awk -F'\t' -v n="$1" '$0 !~ /^#/ && $3 == n && length($1) == 64 && $1 !~ /[^0-9a-f]/ && $2 ~ /^[0-9]+$/ {print $1, $2; f = 1; exit} END {exit !f}' \
    "$REPO_DIR/scripts/weights.registry"
}

# _eng_basalt_profile_load <profile> — a valid profile with pinned weights.
# Sets PF_PROFILE_NAME PF_PACK PF_SERVED_MODEL_NAME PF_PARALLEL PF_MAX_CONTEXT
# PF_KV PF_ANSWER_ROOM PF_AGENT_PROFILE PF_MEASURED_ROW PF_PACK_SHA256 PF_PACK_BYTES.
_eng_basalt_profile_load() {
  local f k row
  f="$(_eng_basalt_profile_path "$1")"
  [[ -f "$f" ]] || { echo "no Basalt profile $f (ls $(_eng_basalt_profiles))" >&2; return 1; }
  while IFS= read -r k; do
    case "$k" in PACK|SERVED_MODEL_NAME|PARALLEL|MAX_CONTEXT|KV|ANSWER_ROOM|AGENT_PROFILE|MEASURED_ROW) ;;
      *) echo "$f: unknown key '$k'" >&2; return 1 ;; esac
  done < <(sed -nE 's/^([A-Z][A-Z0-9_]*)=.*/\1/p' "$f")
  PF_PROFILE_NAME="$(basename "$f" .env)"
  PF_PACK="$(_eng_basalt_pf_get "$f" PACK)"
  PF_SERVED_MODEL_NAME="$(_eng_basalt_pf_get "$f" SERVED_MODEL_NAME)"
  PF_PARALLEL="$(_eng_basalt_pf_get "$f" PARALLEL)"
  PF_MAX_CONTEXT="$(_eng_basalt_pf_get "$f" MAX_CONTEXT)"
  PF_KV="$(_eng_basalt_pf_get "$f" KV)"
  PF_ANSWER_ROOM="$(_eng_basalt_pf_get "$f" ANSWER_ROOM)"
  PF_AGENT_PROFILE="$(_eng_basalt_pf_get "$f" AGENT_PROFILE)"
  PF_MEASURED_ROW="$(_eng_basalt_pf_get "$f" MEASURED_ROW)"
  [[ "$PF_PACK" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.basalt$ ]] || { echo "$f: PACK='$PF_PACK' is not a .basalt file name" >&2; return 1; }
  [[ "$PF_SERVED_MODEL_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$ ]] || { echo "$f: SERVED_MODEL_NAME is required (letters, digits, . _ : -)" >&2; return 1; }
  [[ "$PF_PARALLEL" =~ ^[1-8]$ ]] || { echo "$f: PARALLEL must be 1-8" >&2; return 1; }
  [[ "$PF_MAX_CONTEXT" =~ ^[1-9][0-9]{3,6}$ ]] || { echo "$f: MAX_CONTEXT must be a token count" >&2; return 1; }
  [[ "$PF_KV" =~ ^(fp16|int8|fp8|nvfp4|q4_0|k8v4)$ ]] || { echo "$f: KV='$PF_KV' is not a Basalt --kv value" >&2; return 1; }
  [[ "$PF_ANSWER_ROOM" =~ ^(auto|[0-9]{1,6})$ ]] || { echo "$f: ANSWER_ROOM must be auto or a token count" >&2; return 1; }
  row="$(_eng_basalt_registry_row "$PF_PACK")" \
    || { echo "$f: $PF_PACK has no sha256 pin in scripts/weights.registry" >&2; return 1; }
  read -r PF_PACK_SHA256 PF_PACK_BYTES <<< "$row"
  [[ "$(ob_engine_host_class)" == rig ]] || { echo "Basalt profiles run on the rig (this host is a $(ob_engine_host_class))" >&2; return 1; }
}

eng_basalt_profile_check() { _eng_basalt_profile_load "$1"; }

_eng_basalt_weights_dir() {
  local w
  # shellcheck source=/dev/null
  w="$( (source "$REPO_DIR/scripts/lib/weights.sh" >/dev/null 2>&1; printf '%s' "${WEIGHTS_DIR:-}") || true )"
  printf '%s\n' "${w:-$REPO_DIR/weights}"
}

# profile_locate <profile> — SP_LOCATE_RC 0 when the pack is in WEIGHTS_DIR at
# its pinned size (the quick check; the sha256 is verify-weights.sh --deep).
eng_basalt_profile_locate() {
  local w sz
  SP_LOCATE_RC=3 SP_MODEL_DIR=""
  _eng_basalt_profile_load "$1" >/dev/null || return 0
  w="$(_eng_basalt_weights_dir)"
  [[ -f "$w/$PF_PACK" ]] || return 0
  sz="$(wc -c < "$w/$PF_PACK" 2>/dev/null | tr -d ' ')"
  [[ "$sz" == "$PF_PACK_BYTES" ]] || { echo "$w/$PF_PACK is $sz bytes, the registry pins $PF_PACK_BYTES — scripts/verify-weights.sh --file $PF_PACK --deep" >&2; return 0; }
  SP_LOCATE_RC=0 SP_MODEL_DIR="$w"
}

# ── the sandbox network, and the probe that proves it ──────────────────────
# _eng_basalt_net — a user bridge with no NAT (no route out); created once.
# An existing network of that name must be the same thing, or no launch.
_eng_basalt_net() {
  local n="$_ENG_BASALT_NET" masq
  if ! _eng_basalt_docker network inspect "$n" >/dev/null 2>&1; then
    _eng_basalt_docker network create --driver bridge \
      -o com.docker.network.bridge.enable_ip_masquerade=false \
      --label openbeast.engine=basalt "$n" >/dev/null \
      || { echo "could not create the docker network $n" >&2; return 1; }
  fi
  masq="$(_eng_basalt_docker network inspect -f '{{index .Options "com.docker.network.bridge.enable_ip_masquerade"}}' "$n" 2>/dev/null || true)"
  [[ "$masq" == false ]] || { echo "docker network $n has NAT (enable_ip_masquerade=${masq:-default true}) — remove it (docker network rm $n) and launch again" >&2; return 1; }
}

# The probe (python3 -I, stdlib): every line must say FAIL for the launch to go on.
_ENG_BASALT_PROBE='import socket
socket.setdefaulttimeout(3)
for n in ("example.com", "huggingface.co", "github.com"):
    try:
        socket.getaddrinfo(n, 443)
        print("dns", n, "RESOLVED")
    except Exception as e:
        print("dns", n, "FAIL", type(e).__name__)
for h, p in (("1.1.1.1", 443), ("8.8.8.8", 53)):
    try:
        socket.create_connection((h, p), 3).close()
        print("tcp", h, p, "CONNECTED")
    except Exception as e:
        print("tcp", h, p, "FAIL", type(e).__name__)'

# _eng_basalt_sandbox_args — the network and isolation flags the probe and the
# server share (so the probe proves the server's sandbox, not another one).
_eng_basalt_sandbox_args() {
  local uid gid pids mem
  uid="${OB_ENGINE_BASALT_UID:-$(id -u)}"; gid="${OB_ENGINE_BASALT_GID:-$(id -g)}"
  pids="$(_eng_basalt_conf ENGINE_BASALT_PIDS)"; pids="${pids:-$_ENG_BASALT_PIDS_DEFAULT}"
  mem="$(_eng_basalt_conf ENGINE_BASALT_MEMORY)"; mem="${mem:-$_ENG_BASALT_MEMORY_DEFAULT}"
  [[ "$uid" =~ ^[0-9]+$ && "$uid" != 0 && "$gid" =~ ^[0-9]+$ && "$gid" != 0 ]] \
    || { echo "Basalt runs as a non-root uid:gid (got $uid:$gid) — run the stack as a normal user" >&2; return 1; }
  [[ "$pids" =~ ^[1-9][0-9]{1,5}$ ]] || { echo "ENGINE_BASALT_PIDS='$pids' is not a limit (10-999999)" >&2; return 1; }
  [[ "$mem" =~ ^[1-9][0-9]{0,4}[mg]$ ]] || { echo "ENGINE_BASALT_MEMORY='$mem' is not a limit like 32g" >&2; return 1; }
  _BSB=(--network "$_ENG_BASALT_NET" --dns 127.0.0.1
    --read-only --tmpfs "$_ENG_BASALT_TMPFS"
    --user "$uid:$gid" --cap-drop ALL --security-opt no-new-privileges
    --pids-limit "$pids" --memory "$mem" --memory-swap "$mem"
    -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1)
}

# _eng_basalt_probe <image> — 0 only when nothing resolves and nothing connects.
_eng_basalt_probe() {
  local img="$1" out
  out="$(_eng_basalt_docker run --rm "${_BSB[@]}" --entrypoint python3 "$img" -I -c "$_ENG_BASALT_PROBE" 2>&1)" \
    || { echo "the sandbox probe did not run: $out" >&2; return 1; }
  if grep -qE 'RESOLVED|CONNECTED' <<< "$out" || [[ "$(grep -c ' FAIL ' <<< "$out")" -ne 5 ]]; then
    echo "REFUSING to serve: the Basalt sandbox is not closed (DNS or TCP reached out):" >&2
    printf '  %s\n' "$out" >&2
    return 1
  fi
  echo "sandbox probe: no DNS, no TCP out ($(tr '\n' ';' <<< "$out" | sed 's/;$//'))"
}

# ── managed, single host ───────────────────────────────────────────────────
# launch — foreground. ENG_PRINT=1 prints the server's docker command only.
eng_basalt_launch() {
  local profile home img host port cname keyf="" w pin child rc=0 gpus
  profile="${OPENBEAST_ENGINE_PROFILE:-$(ob_engine_conf_get ENGINE_PROFILE)}"
  profile="${profile:-$(eng_basalt_default_profile)}"
  eng_basalt_profile_check "$profile" || return 1
  read -r host port < <(ob_engine_url_hostport "${INFERENCE_URL:-http://127.0.0.1:8080}")
  # Port 127.0.0.1 only: a remote client reaches the model through beast-gate.
  case "$host" in
    127.0.0.1|localhost) ;;
    *) echo "Basalt is published on 127.0.0.1 only; INFERENCE_URL names '$host' — use http://127.0.0.1:$port (remote clients go through beast-gate, EDGE_GATE=true)" >&2
       return 1 ;;
  esac
  # The build the link names, resolved: the mount must not move under a relink.
  home="$(_eng_basalt_active)"
  [[ -d "$home" ]] && home="$(cd "$home" && pwd -P)"
  img="$(_eng_basalt_build_get "$home" IMAGE_ID 2>/dev/null || true)"
  pin="$(ob_engine_conf_get ENGINE_PIN_BASALT)"
  w="$(_eng_basalt_weights_dir)"
  if [[ "${ENG_PRINT:-0}" != 1 ]]; then
    [[ -x "$home/bin/basalt" && "$img" =~ ^sha256:[0-9a-f]{64}$ ]] \
      || { echo "Basalt is not installed — scripts/engine.sh install basalt" >&2; return 1; }
    if [[ -n "$pin" && "${pin#*@}" != "$(_eng_basalt_build_get "$home" COMMIT)" ]]; then
      echo "the active Basalt build is $(_eng_basalt_build_get "$home" COMMIT | cut -c1-12), the pin is ${pin#*@} — engine.sh install basalt --version ${pin%%@*} (or rollback)" >&2
      return 1
    fi
    eng_basalt_profile_locate "$profile"
    [[ $SP_LOCATE_RC -eq 0 ]] || { echo "the pack $PF_PACK is not in $w at its pinned size — scripts/fetch-weight.sh $PF_PACK" >&2; return 1; }
    local cc
    cc="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n1 | tr -d ' ')"
    [[ "$cc" == 12.0 ]] \
      || { echo "this Basalt build is sm_120 only (RTX 5090 class); this GPU reports compute capability '${cc:-none}'" >&2; return 1; }
  else
    img="${img:-openbeast-basalt-env:NOT-INSTALLED}"
  fi
  _eng_basalt_sandbox_args || return 1
  cname="$(_eng_basalt_cname)"
  gpus="$(_eng_basalt_conf ENGINE_BASALT_GPUS)"; gpus="${gpus:-device=0}"
  [[ "$gpus" =~ ^(all|device=[0-9]+)$ ]] || { echo "ENGINE_BASALT_GPUS='$gpus' is not 'device=N' or 'all'" >&2; return 1; }
  local -a CMD=(docker run --rm --init --name "$cname" --label "openbeast.engine=basalt" --label "openbeast.repo=$REPO_DIR"
    --gpus "$gpus" "${_BSB[@]}" -p "127.0.0.1:$port:$port"
    --ulimit memlock=-1 --shm-size 2g
    -v "$home:/opt/basalt:ro" -v "$w/$PF_PACK:/models/$PF_PACK:ro")
  if [[ -n "${LLAMA_API_KEY:-}" ]]; then
    keyf="${RUN_DIR:-$REPO_DIR/.run}/basalt-api-key"
    CMD+=(-v "$keyf:/run/basalt.key:ro")
  fi
  CMD+=(-w /opt/basalt "$img" /opt/basalt/bin/basalt serve --model "/models/$PF_PACK"
    --alias "$PF_SERVED_MODEL_NAME" --host 0.0.0.0 --port "$port"
    --parallel "$PF_PARALLEL" --max-context "$PF_MAX_CONTEXT" --kv "$PF_KV" --answer-room "$PF_ANSWER_ROOM")
  [[ -n "$keyf" ]] && CMD+=(--api-key-file /run/basalt.key)
  CMD[0]="${OB_ENGINE_DOCKER:-docker}"
  if [[ "${ENG_PRINT:-0}" == 1 ]]; then
    sp_print_cmd "${CMD[@]}" 2>/dev/null || { local a o=""; for a in "${CMD[@]}"; do o+="$(printf '%q' "$a") "; done; printf '%s\n' "${o% }"; }
    return 0
  fi
  _eng_basalt_net || return 1
  _eng_basalt_probe "$img" || return 1
  if [[ -n "$keyf" ]]; then
    ( umask 077; mkdir -p "$(dirname "$keyf")"; printf '%s\n' "$LLAMA_API_KEY" > "$keyf.tmp"; mv -f "$keyf.tmp" "$keyf" )
  fi
  # A container of THIS checkout left by a SIGKILLed launcher holds the port
  # and the GPU: it is ours by name, so it goes.
  _eng_basalt_docker rm -f "$cname" >/dev/null 2>&1 || true
  echo "Basalt $(eng_basalt_installed 2>/dev/null || echo '?'): $PF_PROFILE_NAME ($PF_SERVED_MODEL_NAME, row $PF_MEASURED_ROW) on http://127.0.0.1:$port — container $cname"
  "${CMD[@]}" &
  child=$!
  trap '_eng_basalt_docker stop -t 30 "'"$cname"'" >/dev/null 2>&1 || true; wait '"$child"' 2>/dev/null; exit 143' TERM INT
  wait "$child" || rc=$?
  return "$rc"
}

# ready <url> — /health 200, status ok, the model loaded (serve/handler.py:415-421 at 400b411c).
eng_basalt_ready() {
  local body
  body="$(curl -fsS -m 3 "${1%/}/health" 2>/dev/null)" || return 1
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get("status") == "ok" and d.get("loaded") is True and d.get("service") == "basalt" else 1)' "$body" 2>/dev/null
}

# owns_port <pid> <port> — ours: the launcher is alive and its container runs.
eng_basalt_owns_port() {
  kill -0 "$1" 2>/dev/null || return 1
  [[ "$(_eng_basalt_docker inspect -f '{{.State.Running}}' "$(_eng_basalt_cname)" 2>/dev/null)" == true ]]
}

# stop — the managed container of THIS checkout (stop.sh). Idempotent.
eng_basalt_stop() {
  local c; c="$(_eng_basalt_cname)"
  if _eng_basalt_docker inspect "$c" >/dev/null 2>&1; then
    _eng_basalt_docker stop -t 30 "$c" >/dev/null 2>&1 && echo "Basalt container $c stopped."
  fi
  return 0
}
