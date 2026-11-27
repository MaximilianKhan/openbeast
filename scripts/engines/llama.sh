#!/bin/bash
# Engine adapter: llama.cpp (llama-server). docs/ENGINES.md, contract in
# docs/ENGINES.md "Adapter contract". Sourced by lib/engine.sh; functions only.
#
# llama.cpp is the one engine start.sh has always managed itself, through
# scripts/serve-*.sh — so this adapter has NO launch/stop: the llama path
# through start.sh / stop.sh / healthcheck.sh stays exactly what it was.
# What it adds is the version plane: install / update / stage / rollback a
# pinned llama.cpp commit, built by scripts/update.sh (its lease check, its
# bin/ snapshot + restore on a failed build, its GPU_BACKEND flags).
#
# Pin: scripts/llama.cpp.ref (LLAMA_CPP_REF=<40-hex>), the file bootstrap.sh
# builds from. A release is a tag bNNNN; the pin is always the commit it
# names (a tag can be moved; a commit id is the content hash).

eng_llama_label()   { printf 'llama.cpp\n'; }
eng_llama_hosts()   { printf 'rig spark mac\n'; }
eng_llama_formats() { printf 'GGUF\n'; }
eng_llama_hydra_kind() { printf 'llama\n'; }

_eng_llama_src()  { printf '%s\n' "${OB_ENGINE_LLAMA_SRC:-$REPO_DIR/llama.cpp}"; }
_eng_llama_url()  { printf '%s\n' "${OB_ENGINE_LLAMA_URL:-https://github.com/ggml-org/llama.cpp.git}"; }
_eng_llama_ref_file() { printf '%s\n' "$REPO_DIR/scripts/llama.cpp.ref"; }

_eng_llama_pinned_sha() {
  sed -nE 's/^LLAMA_CPP_REF=([0-9a-f]{40})[[:space:]]*$/\1/p' "$(_eng_llama_ref_file)" 2>/dev/null | tail -n1 || true
}


# Release tags upstream (read 2026-11-15): stable releases are semver
# (v1.0.0, GitHub's "latest", 2026-11-13); every CI build is still tagged
# bNNNN and marked pre-release (b12422 the newest). Both are accepted; a
# release's tag is never what gets pinned — the commit it names is.
_ENG_LLAMA_TAG_RE='^(b[0-9]+|v[0-9]+\.[0-9]+\.[0-9]+)$'

# _eng_llama_name <sha> — the release tag that names this commit in the
# local clone (a vX.Y.Z before a bNNNN), else its 12-hex prefix.
_eng_llama_name() {
  local sha="$1" src t
  src="$(_eng_llama_src)"
  t="$(git -C "$src" tag --points-at "$sha" 2>/dev/null | grep -E "$_ENG_LLAMA_TAG_RE" \
       | awk '{print (/^v/ ? "1 " : "0 ") $0}' | sort -k1,1n -k2,2V | tail -n1 | cut -d' ' -f2 || true)"
  printf '%s\n' "${t:-${sha:0:12}}"
}

# installed: what llama.cpp/ is checked out at, when llama-server is built.
eng_llama_installed() {
  local src sha
  src="$(_eng_llama_src)"
  [[ -d "$src/.git" || -f "$src/.git" ]] || return 1
  [[ -x "$src/build/bin/llama-server" ]] || return 1
  sha="$(git -C "$src" rev-parse HEAD 2>/dev/null)" || return 1
  printf '%s (%s)\n' "$(_eng_llama_name "$sha")" "${sha:0:12}"
}

# current — "version<TAB>commit" of the llama-server built in llama.cpp/
# now, for engine.sh to record before it replaces it (a build made by hand,
# from a branch, was missing from the history, so rollback had nothing to
# return to — the rig, 2026-11-17). The binary says which commit it was
# built from ("version: 1.0.0-dev (build 12359, commit b1b770d90)", older
# "version: 4567 (abcdef12)"), which can differ from HEAD when the tree was
# moved after the build; without that, HEAD.
eng_llama_current() {
  local src bin out short sha
  src="$(_eng_llama_src)"; bin="$src/build/bin/llama-server"
  [[ -x "$bin" ]] || return 1
  [[ -d "$src/.git" || -f "$src/.git" ]] || return 1
  if command -v timeout >/dev/null 2>&1; then
    out="$(timeout 30 "$bin" --version 2>&1 < /dev/null || true)"
  else
    out="$("$bin" --version 2>&1 < /dev/null || true)"
  fi
  short="$(sed -nE 's/^version: .*commit ([0-9a-f]{7,40})\).*/\1/p; s/^version: [0-9]+ \(([0-9a-f]{7,40})\).*/\1/p' <<< "$out" | head -n1)"
  if [[ -n "$short" ]]; then
    sha="$(git -C "$src" rev-parse -q --verify "$short^{commit}" 2>/dev/null || true)"
    [[ -n "$sha" ]] || { echo "the built llama-server reports commit $short, which llama.cpp/ does not hold — keep a copy of llama.cpp/build/bin by hand to go back to it" >&2; return 1; }
  else
    sha="$(git -C "$src" rev-parse HEAD 2>/dev/null)" || return 1
  fi
  printf '%s\t%s\n' "$(_eng_llama_name "$sha")" "$sha"
}

eng_llama_pin() {
  local sha; sha="$(_eng_llama_pinned_sha)"
  [[ -n "$sha" ]] || return 0
  printf '%s@%s\n' "$(_eng_llama_name "$sha")" "$sha"
}

# _eng_llama_ls_remote — every release tag upstream, "tag commit" per line
# (an annotated tag's peeled ^{} commit wins over the tag object).
_eng_llama_ls_remote() {
  local out
  # From / : ls-remote needs no repository, and a broken one around the
  # caller's cwd (a stale worktree pointer) would fail it.
  out="$(git -C / ls-remote --tags "$(_eng_llama_url)" 2>/dev/null)" || {
    echo "cannot reach $(_eng_llama_url)" >&2; return 1; }
  awk -v re="$_ENG_LLAMA_TAG_RE" '
    { t = $2; sub("^refs/tags/", "", t); peeled = sub(/\^\{\}$/, "", t)
      if (t !~ re) next
      if (peeled || !(t in sha)) sha[t] = $1 }
    END { for (t in sha) print t, sha[t] }' <<< "$out"
}

# latest [stable|build] — "tag commit": the highest vX.Y.Z (stable; falls
# back to the highest bNNNN where no semver tag exists), or with "build" the
# highest bNNNN.
eng_llama_latest() {
  local kind="${1:-stable}" tags line
  if ob_engine_offline; then
    echo "OFFLINE=true — cannot list upstream llama.cpp tags; pass --version <tag|commit> --from DIR." >&2
    return 1
  fi
  tags="$(_eng_llama_ls_remote)" || return 1
  line=""
  [[ "$kind" == stable ]] && line="$(grep -E '^v' <<< "$tags" | sort -V | tail -n1 || true)"
  [[ -n "$line" ]] || line="$(grep -E '^b' <<< "$tags" | sort -V | tail -n1 || true)"
  [[ -n "$line" ]] || { echo "no release tags at $(_eng_llama_url)" >&2; return 1; }
  printf '%s\n' "$line"
}

# _eng_llama_resolve <version> — the commit for a tag | 40-hex | latest |
# latest-build.
_eng_llama_resolve() {
  local v="$1" src out sha
  src="$(_eng_llama_src)"
  if [[ "$v" =~ ^[0-9a-f]{40}$ ]]; then printf '%s\n' "$v"; return 0; fi
  if [[ -z "$v" || "$v" == latest || "$v" == latest-build ]]; then
    out="$(eng_llama_latest "$([[ "$v" == latest-build ]] && echo build || echo stable)")" || return 1
    printf '%s\n' "${out#* }"; return 0
  fi
  [[ "$v" =~ $_ENG_LLAMA_TAG_RE ]] || { echo "llama.cpp versions are release tags (v1.0.0, b12422), 40-hex commits, latest or latest-build — not '$v'" >&2; return 1; }
  sha="$(git -C "$src" rev-parse -q --verify "refs/tags/$v^{commit}" 2>/dev/null || true)"
  if [[ -z "$sha" ]]; then
    ob_engine_offline && { echo "tag $v is not in llama.cpp/ and OFFLINE=true — use --from DIR (engine.sh stage llama --version $v DIR on a connected box)" >&2; return 1; }
    out="$(_eng_llama_ls_remote)" || return 1
    sha="$(awk -v t="$v" '$1 == t {print $2}' <<< "$out")"
  fi
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "no llama.cpp tag $v upstream" >&2; return 1; }
  printf '%s\n' "$sha"
}


# _eng_llama_fetch <commit> <from-dir|""> — get the commit into llama.cpp/.
_eng_llama_fetch() {
  local sha="$1" from="$2" src head
  src="$(_eng_llama_src)"
  if [[ -n "$from" ]]; then
    [[ -d "$from/llama.cpp.git" ]] || { echo "$from has no llama.cpp.git (made by: engine.sh stage llama ...)" >&2; return 1; }
    head="$(ob_engine_manifest_get "$from/MANIFEST" COMMIT)"
    [[ "$head" == "$sha" ]] || { echo "$from/MANIFEST names commit ${head:-none}, not $sha — refusing" >&2; return 1; }
    git -C "$src" fetch -q --depth 1 "file://$(cd "$from" && pwd)/llama.cpp.git" ob-stage || return 1
    [[ "$(git -C "$src" rev-parse FETCH_HEAD)" == "$sha" ]] \
      || { echo "the staged llama.cpp.git does not hold $sha — refusing" >&2; return 1; }
    _eng_llama_unshallow_local "$src" "$sha"
    return 0
  fi
  # NOT --depth 1: llama.cpp's build number is its commit count, and a
  # shallow fetch made it 1 — the UI assets were then asked for as "b1", an
  # incompatible "latest" came back and cmake failed (the rig, 2026-11-17).
  # A shallow clone (bootstrap.sh, or this script before) is deepened once.
  local deep=() shallow
  shallow="$(git -C "$src" rev-parse --is-shallow-repository 2>/dev/null || true)"
  [[ "$shallow" == true ]] && deep=(--unshallow)
  if git -C "$src" cat-file -e "$sha^{commit}" 2>/dev/null; then
    # Here already. Deepen a shallow clone while the network is up (best
    # effort: the build falls back to a bNNNN tag, lib/hardware.sh).
    if [[ "$shallow" == true ]] && ! ob_engine_offline; then
      git -C "$src" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 fetch -q --unshallow "$(_eng_llama_url)" "$sha" 2>/dev/null \
        || echo "note: could not deepen the shallow llama.cpp/ (its build number may be wrong: git -C llama.cpp fetch --unshallow)" >&2
    fi
    return 0
  fi
  ob_engine_offline && { echo "commit ${sha:0:12} is not in llama.cpp/ and OFFLINE=true — use --from DIR" >&2; return 1; }
  git -C "$src" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 fetch -q ${deep[@]+"${deep[@]}"} "$(_eng_llama_url)" "$sha" \
    || { echo "could not fetch llama.cpp ${sha:0:12} from $(_eng_llama_url)" >&2; return 1; }
}

# _eng_llama_unshallow_local <src> <sha> — undo the cut a --depth 1 fetch
# from a staged folder leaves. The staged repo is one commit deep, so git
# writes <sha> into .git/shallow ("no parents here") even when this clone
# holds every parent, and from then on the clone counted <sha> as commit #1:
# the build number came out b1 (the cmake UI fetch failure of 2026-11-17)
# and the whole clone read as shallow. When every parent of <sha> is present,
# the cut is false, so <sha> leaves .git/shallow (the file goes when empty).
# A parent that is missing keeps the cut: then the history really is short.
_eng_llama_unshallow_local() {
  local src="$1" sha="$2" gd p parents ok=1 tmp
  gd="$(git -C "$src" rev-parse --absolute-git-dir 2>/dev/null)" || return 0
  [[ -f "$gd/shallow" ]] && grep -qxF "$sha" "$gd/shallow" || return 0
  parents="$(git -C "$src" cat-file -p "$sha" 2>/dev/null | sed -nE 's/^parent ([0-9a-f]{40})$/\1/p')"
  [[ -n "$parents" ]] || return 0
  for p in $parents; do git -C "$src" cat-file -e "$p^{commit}" 2>/dev/null || { ok=0; break; }; done
  [[ $ok -eq 1 ]] || return 0
  tmp="$(mktemp "$gd/shallow.XXXXXX")" || return 0
  grep -vxF "$sha" "$gd/shallow" > "$tmp" || true
  if [[ -s "$tmp" ]]; then mv -f "$tmp" "$gd/shallow"; else rm -f "$tmp" "$gd/shallow"; fi
}

# _eng_llama_build_number_of <sha> — the build number (bNNNN) of a commit:
# its count in a clone whose history is whole, else a bNNNN tag naming it
# (local, then upstream's). Empty when unknown. Written to a stage MANIFEST,
# because the staged repo is one commit deep and cannot count.
_eng_llama_build_number_of() {
  local sha="$1" src n=""
  src="$(_eng_llama_src)"
  if [[ -d "$src/.git" || -f "$src/.git" ]] && git -C "$src" cat-file -e "$sha^{commit}" 2>/dev/null; then
    if [[ "$(git -C "$src" rev-parse --is-shallow-repository 2>/dev/null)" == false ]]; then
      n="$(git -C "$src" rev-list --count "$sha" 2>/dev/null || true)"
    fi
    [[ "$n" =~ ^[1-9][0-9]*$ ]] \
      || n="$(git -C "$src" tag --points-at "$sha" 2>/dev/null | sed -nE 's/^b([0-9]+)$/\1/p' | sort -n | tail -n1 || true)"
  fi
  if [[ ! "$n" =~ ^[1-9][0-9]*$ ]] && ! ob_engine_offline; then
    n="$(_eng_llama_ls_remote 2>/dev/null | awk -v s="$sha" '$2 == s && $1 ~ /^b[0-9]+$/ { print substr($1, 2) }' | sort -n | tail -n1 || true)"
  fi
  [[ "$n" =~ ^[1-9][0-9]*$ ]] && printf '%s\n' "$n"
  return 0
}

_eng_llama_write_pin() {
  local sha="$1" f; f="$(_eng_llama_ref_file)"
  if [[ -f "$f" ]] && grep -qE '^LLAMA_CPP_REF=' "$f"; then
    local tmp; tmp="$(mktemp "$f.XXXXXX")"
    sed -E "s|^LLAMA_CPP_REF=.*|LLAMA_CPP_REF=$sha|" "$f" > "$tmp" && mv -f "$tmp" "$f"
  else
    printf 'LLAMA_CPP_REF=%s\n' "$sha" >> "$f"
  fi
}

# install <version> <from> — check out the commit and build it with
# update.sh --llama --force (a detached HEAD is built as-is; a failed build
# restores bin/ — then the checkout goes back too, and the pin stays put).
# Sets ENG_RESULT_VERSION / ENG_RESULT_REF.
eng_llama_install() {
  local v="$1" from="$2" src sha prev build
  src="$(_eng_llama_src)"
  [[ -d "$src/.git" || -f "$src/.git" ]] || { echo "llama.cpp/ is not a git clone — run ./bootstrap.sh first (it builds the pinned commit)" >&2; return 1; }
  if [[ -n "$from" && -z "$v" ]]; then v="$(ob_engine_manifest_get "$from/MANIFEST" COMMIT)"; fi
  sha="$(_eng_llama_resolve "$v")" || return 1
  _eng_llama_fetch "$sha" "$from" || return 1
  if [[ -n "$(git -C "$src" status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
    echo "llama.cpp/ has local changes — not switching commits under them (git -C llama.cpp status)" >&2
    return 1
  fi
  prev="$(git -C "$src" rev-parse HEAD)"
  git -C "$src" checkout -q --detach "$sha" || return 1
  # A staged repo is one commit deep, so the build cannot count its way to
  # the build number: the connected box wrote it into the MANIFEST. Tag it
  # too, so a later update.sh --llama --force rebuild finds it again.
  local bn="" own=""
  if [[ -n "$from" ]]; then
    bn="$(ob_engine_manifest_get "$from/MANIFEST" BUILD_NUMBER)"
    [[ "$bn" =~ ^[1-9][0-9]*$ ]] || bn=""
    # Where this clone's own history is whole (the cut undone above), its
    # count is the truth and the MANIFEST's number only a claim.
    if [[ "$(git -C "$src" rev-parse --is-shallow-repository 2>/dev/null)" == false ]]; then
      own="$(git -C "$src" rev-list --count "$sha" 2>/dev/null || true)"
      if [[ "$own" =~ ^[1-9][0-9]*$ ]]; then
        [[ -z "$bn" || "$bn" == "$own" ]] || echo "note: $from/MANIFEST says BUILD_NUMBER=$bn, this clone counts $own for ${sha:0:12} — using $own" >&2
        bn="$own"
      fi
    fi
    if ! ob_engine_offline; then
      echo "note: the build fetches llama.cpp's prebuilt Web UI (assets of b${bn:-?}) over the network. On a closed box set OFFLINE=true: the build then skips it (-DLLAMA_USE_PREBUILT_UI=OFF) instead of stalling ~11 minutes and failing." >&2
    fi
    if [[ -n "$bn" ]] && ! git -C "$src" rev-parse -q --verify "refs/tags/b$bn" >/dev/null 2>&1; then
      git -C "$src" tag "b$bn" "$sha" 2>/dev/null || true
    fi
  fi
  build="${OB_ENGINE_LLAMA_BUILD:-$REPO_DIR/scripts/update.sh --llama --force}"
  # shellcheck disable=SC2086  # a command line, split on purpose
  if ! OB_LLAMA_BUILD_NUMBER="$bn" $build; then
    git -C "$src" checkout -q --detach "$prev" 2>/dev/null || true
    echo "llama.cpp build of ${sha:0:12} FAILED — llama.cpp/ is back at ${prev:0:12}; the pin did not move." >&2
    return 1
  fi
  _eng_llama_write_pin "$sha"
  # Name the commit locally (a shallow fetch by commit brings no tags), so
  # list/status/doctor say v1.0.0 rather than a hash. Never MOVE a local tag:
  # one that already names another commit means upstream re-pointed it.
  if [[ "$v" =~ $_ENG_LLAMA_TAG_RE ]]; then
    local had; had="$(git -C "$src" rev-parse -q --verify "refs/tags/$v^{commit}" 2>/dev/null || true)"
    if [[ -z "$had" ]]; then git -C "$src" tag "$v" "$sha" 2>/dev/null || true
    elif [[ "$had" != "$sha" ]]; then
      echo "WARNING: llama.cpp/ already has a tag $v at ${had:0:12}, upstream says ${sha:0:12} — a moved tag; pinned the commit, left the tag." >&2
    fi
  fi
  ENG_RESULT_VERSION="$(_eng_llama_name "$sha")"
  ENG_RESULT_REF="$sha"
  if [[ "$ENG_RESULT_VERSION" == "${sha:0:12}" && "$v" =~ $_ENG_LLAMA_TAG_RE ]]; then
    ENG_RESULT_VERSION="$v"   # a shallow fetch by commit brings no tags along
  fi
  echo "llama.cpp $ENG_RESULT_VERSION (${sha:0:12}) built; scripts/llama.cpp.ref now pins it — review and commit that file."
  echo "  A running llama-server keeps the old binary until ./stop.sh && ./start.sh."
}

# activate <version> <ref> — rollback: the same as installing that commit
# (its objects are already local, so this works offline).
eng_llama_activate() { eng_llama_install "$2" ""; }

# stage <version> <dir> — a bare, one-commit repo + MANIFEST to carry to a
# closed box: engine.sh install llama --from DIR there.
eng_llama_stage() {
  local v="$1" dir="$2" src sha name
  src="$(_eng_llama_src)"
  sha="$(_eng_llama_resolve "$v")" || return 1
  mkdir -p "$dir"
  rm -rf "$dir/llama.cpp.git"
  git init -q --bare "$dir/llama.cpp.git"
  if [[ -d "$src/.git" || -f "$src/.git" ]] && git -C "$src" cat-file -e "$sha^{commit}" 2>/dev/null; then
    git -C "$dir/llama.cpp.git" fetch -q --depth 1 "file://$(cd "$src" && pwd)" "$sha" 2>/dev/null \
      || git -C "$dir/llama.cpp.git" fetch -q --depth 1 "$(_eng_llama_url)" "$sha"
  else
    git -C "$dir/llama.cpp.git" fetch -q --depth 1 "$(_eng_llama_url)" "$sha"
  fi || { echo "could not fetch ${sha:0:12} into $dir" >&2; return 1; }
  git -C "$dir/llama.cpp.git" update-ref refs/heads/ob-stage "$sha"
  name="$v"; [[ -z "$name" || "$name" == latest || "$name" =~ ^[0-9a-f]{40}$ ]] && name="$(_eng_llama_name "$sha")"
  local bn; bn="$(_eng_llama_build_number_of "$sha")"
  printf 'ENGINE=llama\nVERSION=%s\nCOMMIT=%s\n' "$name" "$sha" > "$dir/MANIFEST"
  [[ -n "$bn" ]] && printf 'BUILD_NUMBER=%s\n' "$bn" >> "$dir/MANIFEST"
  echo "staged llama.cpp $name (${sha:0:12}${bn:+, build b$bn}) in $dir — on the closed box: scripts/engine.sh install llama --from $dir"
  [[ -n "$bn" ]] || echo "  WARNING: no build number known for ${sha:0:12} (no bNNNN tag, no whole history): the closed box's build will ask for the wrong UI assets." >&2
}
