#!/bin/bash
# beast-lang — acquire and verify the offline language library.
#
#   ./scripts/lang-library.sh acquire [lang ...]   fetch sources (default: all wired)
#   ./scripts/lang-library.sh check                verify what is on disk vs its manifest
#   ./scripts/lang-library.sh list                 what we hold, with provenance
#   ./scripts/lang-library.sh verify [lang]        compile every claim against
#                                                  the INSTALLED toolchains
#   ./scripts/lang-library.sh where                print the library root
#
# Design rules (docs/BEAST_LANG_PLAN.md):
#   * NEVER hardcode an artifact URL. Resolve through a version index or a
#     release API at acquisition time. Two of ten URLs assumed from memory
#     while writing the plan were already 404 — a hardcoded URL is a corpus
#     that goes stale silently, which is worse than no corpus.
#   * Every artifact gets a provenance manifest: where it came from, its
#     sha256, its license, when it was fetched, and which toolchain version it
#     describes. A summary we cannot trace is a summary we cannot trust.
#   * The library lives OUTSIDE git (licensing, §6) — cppreference is
#     CC-BY-SA and ISO drafts are not redistributable. We ship the acquirer.
#   * Idempotent and resumable: re-running skips what is already verified.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

LANG_DIR="${OPENBEAST_LANG_DIR:-$SCRIPT_DIR/../openbeast-lang-library}"
UA="openbeast-lang/1.0 (+https://github.com/MaximilianKhan/openbeast)"
WIRED=(zig cpp c go rust swift python)

say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || die "curl is required"

# --- provenance -------------------------------------------------------------
# One JSON file per artifact. Written only AFTER the bytes are on disk and
# hashed, so a manifest never describes something that is not there.
manifest_write() {            # manifest_write <dir> <url> <file> <license> <describes>
  local dir="$1" url="$2" file="$3" license="$4" describes="$5"
  local sha size
  sha="$(sha256sum "$file" | cut -d' ' -f1)"
  size="$(stat -c%s "$file")"
  python3 - "$dir/manifest.json" "$url" "$(basename "$file")" "$sha" "$size" \
           "$license" "$describes" <<'PY'
import json, os, subprocess, sys
path, url, name, sha, size, license_, describes = sys.argv[1:8]
doc = {}
if os.path.exists(path):
    try:
        doc = json.load(open(path))
    except Exception:
        doc = {}
doc.setdefault("artifacts", {})
doc["artifacts"][name] = {
    "source_url": url, "sha256": sha, "bytes": int(size),
    "license": license_, "describes": describes,
    "fetched_at": subprocess.run(["date", "-Is"], capture_output=True,
                                 text=True).stdout.strip(),
}
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(doc, fh, indent=2, sort_keys=True)
os.replace(tmp, path)
print(f"    manifest: {name} sha256={sha[:16]}… {int(size)/1e6:.1f} MB  [{license_}]")
PY
}

have() {                      # have <dir> <basename> — already fetched AND hash-verified?
  local dir="$1" name="$2"
  [[ -f "$dir/$name" && -f "$dir/manifest.json" ]] || return 1
  python3 - "$dir/manifest.json" "$name" "$dir/$name" <<'PY'
import hashlib, json, sys
man, name, path = sys.argv[1:4]
try:
    want = json.load(open(man))["artifacts"][name]["sha256"]
except Exception:
    sys.exit(1)
h = hashlib.sha256()
with open(path, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        h.update(chunk)
sys.exit(0 if h.hexdigest() == want else 1)
PY
}

fetch() {                     # fetch <url> <dest>
  local url="$1" dest="$2"
  curl -fsSL --retry 3 --retry-delay 2 --max-time 900 -A "$UA" \
       -o "$dest.part" "$url" || return 1
  mv "$dest.part" "$dest"
}

# --- resolvers: every one of these asks an index, none hardcodes an artifact -
resolve_github_release() {     # <owner/repo> [asset-regex] -> "tag<TAB>url"
  local repo="$1" want="${2:-}" body rc
  # The release JSON goes through a FILE, not a pipe: a heredoc-supplied
  # python script and piped stdin are the same file descriptor, so the
  # heredoc silently wins and the resolver reads an empty document
  # (shellcheck SC2259 caught this before it ever ran).
  body="$(mktemp)"
  if ! curl -fsSL --max-time 60 -A "$UA" \
         "https://api.github.com/repos/$repo/releases/latest" -o "$body" 2>/dev/null; then
    rm -f "$body"; return 1
  fi
  python3 - "$want" "$body" <<'PY'
import json, re, sys
want, path = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path))
except Exception:
    sys.exit(1)
tag = d.get("tag_name") or ""
if not want:
    print(f"{tag}\t{d.get('tarball_url','')}")
    sys.exit(0)
for a in d.get("assets", []):
    if re.search(want, a["name"]):
        print(f"{tag}\t{a['browser_download_url']}")
        sys.exit(0)
sys.exit(1)
PY
  rc=$?
  rm -f "$body"
  return $rc
}

# --- per-language acquisition ------------------------------------------------

acq_zig() {
  step "zig — release index (ziglang.org/download/index.json)"
  # WHY other versions: the installed compiler is the ground truth for what
  # compiles TODAY, but a migration map needs the surface that went away.
  # With 0.15 and 0.16 std sources both on disk, "what was removed" becomes
  # mechanically derivable instead of hand-curated — see lang_diff.py.
  local idx="$LANG_DIR/zig/index.json"
  mkdir -p "$LANG_DIR/zig"
  fetch "https://ziglang.org/download/index.json" "$idx" \
    || { warn "zig: index unreachable — skipping"; return 1; }
  local installed; installed="$(zig version 2>/dev/null || echo unknown)"
  say "    installed zig: $installed"
  python3 - "$idx" <<'PY' | while IFS=$'\t' read -r ver url; do
import json, sys
d = json.load(open(sys.argv[1]))
vers = [k for k in d if k != "master"]
def key(v):
    try: return tuple(int(x) for x in v.split("."))
    except Exception: return (0,)
# the two most recent tagged releases are what a migration map needs
for v in sorted(vers, key=key, reverse=True)[:2]:
    t = d[v].get("x86_64-linux") or {}
    if t.get("tarball"):
        print(f"{v}\t{t['tarball']}")
PY
    local d="$LANG_DIR/zig/$ver" name
    name="$(basename "$url")"
    mkdir -p "$d"
    if have "$d" "$name"; then say "    zig $ver: already held"; continue; fi
    say "    zig $ver: fetching $(basename "$url")"
    if fetch "$url" "$d/$name"; then
      manifest_write "$d" "$url" "$d/$name" "MIT (zig)" "zig $ver"
    else
      warn "zig $ver: fetch failed"
    fi
  done
}

acq_cpp() {
  step "C++ — cppreference offline archive (per-standard markers included)"
  local d="$LANG_DIR/cpp" res tag url
  mkdir -p "$d"
  res="$(resolve_github_release PeterFeicht/cppreference-doc 'cppreference-doc-.*\.tar\.xz$')" \
    || { warn "cpp: could not resolve a cppreference release — skipping"; return 1; }
  tag="${res%%$'\t'*}"; url="${res#*$'\t'}"
  local name; name="$(basename "$url")"
  if have "$d" "$name"; then say "    cppreference $tag: already held"; return 0; fi
  say "    cppreference $tag: fetching $name"
  fetch "$url" "$d/$name" || { warn "cpp: fetch failed"; return 1; }
  # CC-BY-SA: attribution and share-alike ride along with anything derived
  # from this, which is why the library is not committed. See plan §6.
  manifest_write "$d" "$url" "$d/$name" "CC-BY-SA-3.0 (cppreference)" \
    "C++98..C++26 (annotated per standard), tag $tag"
}

acq_c() {
  step "C — WG14 working draft (the free, citable artifact; ISO itself is paid)"
  local d="$LANG_DIR/c"
  mkdir -p "$d"
  local idx="$d/wg14-index.html"
  if ! fetch "https://www.open-std.org/jtc1/sc22/wg14/www/docs/" "$idx"; then
    warn "c: WG14 document index unreachable — skipping"; return 1
  fi
  # Selection lives in scripts/lib/wg14_pick.py, which explains why the
  # obvious heuristic (highest N-number) picks a two-page paper instead of
  # the standard, and refuses to return anything paper-sized.
  local pick url bytes name
  if ! pick="$(python3 "$SCRIPT_DIR/scripts/lib/wg14_pick.py" "$idx")"; then
    warn "c: no draft-sized candidate found — NOT shipping a paper as the standard"
    return 1
  fi
  url="${pick%%$'\t'*}"; bytes="${pick##*$'\t'}"
  name="$(basename "$url")"
  if have "$d" "$name"; then say "    C draft $name: already held"; return 0; fi
  say "    C draft: fetching $name ($((bytes / 1000000)) MB — largest of the newest 40)"
  fetch "$url" "$d/$name" || { warn "c: fetch failed"; return 1; }
  manifest_write "$d" "$url" "$d/$name" "ISO/IEC WG14 working draft (not the standard)" \
    "C working draft $name (selected by size; WG14 publishes no machine-readable title)"
}

acq_go() {
  step "go — spec from the go tree + the installed toolchain's own doc dir"
  local d="$LANG_DIR/go" v
  mkdir -p "$d"
  v="$(go version 2>/dev/null | awk '{print $3}')" || v="unknown"
  say "    installed: $v  GOROOT=$(go env GOROOT 2>/dev/null || echo '?')"
  local url="https://raw.githubusercontent.com/golang/go/master/doc/go_spec.html"
  if have "$d" go_spec.html; then say "    go spec: already held"; else
    fetch "$url" "$d/go_spec.html" || { warn "go: spec fetch failed"; return 1; }
    manifest_write "$d" "$url" "$d/go_spec.html" "BSD-3-Clause (Go project)" \
      "Go language spec (master; installed toolchain $v)"
  fi
}

acq_rust() {
  step "rust — the reference (git); std surface comes from introspection"
  # NOTE (plan §9 Q3): no rustup on this rig — Rust is the Arch system package
  # and `rust-docs` did not resolve as a package, so the prose corpus is the
  # reference repo and the API surface has to come from the installed rustc.
  local d="$LANG_DIR/rust" res tag url name
  mkdir -p "$d"
  say "    installed: $(rustc --version 2>/dev/null || echo 'rustc absent')"
  res="$(resolve_github_release rust-lang/reference)" \
    || { warn "rust: could not resolve the reference repo — skipping"; return 1; }
  tag="${res%%$'\t'*}"; url="${res#*$'\t'}"
  name="reference-$tag.tar.gz"
  if have "$d" "$name"; then say "    rust reference $tag: already held"; return 0; fi
  say "    rust reference: fetching $tag"
  fetch "$url" "$d/$name" || { warn "rust: fetch failed"; return 1; }
  manifest_write "$d" "$url" "$d/$name" "MIT OR Apache-2.0 (rust-lang)" \
    "Rust Reference $tag (editions 2015/2018/2021/2024)"
}

acq_swift() {
  step "swift — the book + evolution proposals (NO local toolchain: unverifiable)"
  # Plan §4: with no swiftc on this rig every Swift claim would be CURATED,
  # and CURATED lines are never auto-injected. We still acquire, so the
  # decision in §9 Q2 can be made against real bytes rather than a guess.
  local d="$LANG_DIR/swift" res tag url name
  mkdir -p "$d"
  command -v swiftc >/dev/null || warn "swift: no swiftc — anything derived stays UNVERIFIED"
  for repo in swiftlang/swift-book swiftlang/swift-evolution; do
    res="$(resolve_github_release "$repo")" || { warn "swift: $repo unresolved"; continue; }
    tag="${res%%$'\t'*}"; url="${res#*$'\t'}"
    name="$(basename "$repo")-${tag:-head}.tar.gz"
    if have "$d" "$name"; then say "    $repo ${tag:-head}: already held"; continue; fi
    say "    $repo: fetching ${tag:-head}"
    fetch "$url" "$d/$name" && manifest_write "$d" "$url" "$d/$name" \
      "Apache-2.0 (swiftlang)" "Swift ${tag:-head} (language modes 4/5/6)" \
      || warn "swift: $repo fetch failed"
  done
}

acq_python() {
  step "python — stdlib source is already local; record it and resolve the docs"
  local d="$LANG_DIR/python" v
  mkdir -p "$d"
  v="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  say "    installed: python $v (stdlib source on disk — the ground truth)"
  # The docs archive URL I assumed while writing the plan 404'd, so resolve it
  # from the download page instead of guessing the filename.
  local page="$d/download.html" url name
  if fetch "https://docs.python.org/$v/download.html" "$page"; then
    url="$(python3 - "$page" "$v" <<'PY'
import re, sys
html = open(sys.argv[1], errors="ignore").read()
m = re.search(r'href="(\S*?python-[\d.]+-docs-text\.tar\.bz2)"', html)
if m:
    u = m.group(1)
    print(u if u.startswith("http") else f"https://docs.python.org/{sys.argv[2]}/{u}")
PY
)"
  fi
  if [[ -z "${url:-}" ]]; then
    warn "python: could not resolve a docs archive from the download page — stdlib source only"
    return 0
  fi
  name="$(basename "$url")"
  if have "$d" "$name"; then say "    python docs $name: already held"; return 0; fi
  say "    python docs: fetching $name"
  fetch "$url" "$d/$name" || { warn "python: docs fetch failed"; return 0; }
  manifest_write "$d" "$url" "$d/$name" "PSF-2.0 (Python docs)" "Python $v documentation"
}

cmd_acquire() {
  local langs=("$@")
  [[ ${#langs[@]} -eq 0 ]] && langs=("${WIRED[@]}")
  mkdir -p "$LANG_DIR"
  say "library root: $LANG_DIR"
  local failed=()
  for l in "${langs[@]}"; do
    case "$l" in
      zig|cpp|c|go|rust|swift|python) "acq_$l" || failed+=("$l") ;;
      *) warn "unknown language: $l (wired: ${WIRED[*]})" ; failed+=("$l") ;;
    esac
  done
  step "done"
  cmd_list
  if [[ ${#failed[@]} -gt 0 ]]; then
    warn "incomplete: ${failed[*]} — rerun to resume (nothing already held is refetched)"
    return 1
  fi
}

cmd_check() {
  [[ -d "$LANG_DIR" ]] || die "no library at $LANG_DIR — run: $0 acquire"
  local bad=0
  while IFS= read -r man; do
    python3 - "$man" <<'PY' || bad=1
import hashlib, json, os, sys
man = sys.argv[1]
root = os.path.dirname(man)
doc = json.load(open(man))
rc = 0
for name, meta in sorted(doc.get("artifacts", {}).items()):
    p = os.path.join(root, name)
    if not os.path.exists(p):
        print(f"  MISSING  {p}"); rc = 1; continue
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    ok = h.hexdigest() == meta["sha256"]
    print(f"  {'OK      ' if ok else 'CORRUPT '} {os.path.relpath(p)}  [{meta['license']}]")
    if not ok:
        rc = 1
sys.exit(rc)
PY
  done < <(find "$LANG_DIR" -name manifest.json | sort)
  [[ $bad -eq 0 ]] && say "library verified." || die "library has missing or corrupt artifacts"
}

cmd_list() {
  [[ -d "$LANG_DIR" ]] || { say "no library yet at $LANG_DIR"; return 0; }
  python3 - "$LANG_DIR" <<'PY'
import json, os, sys
root = sys.argv[1]
rows, total = [], 0
for dirpath, _, files in os.walk(root):
    if "manifest.json" not in files:
        continue
    doc = json.load(open(os.path.join(dirpath, "manifest.json")))
    for name, m in sorted(doc.get("artifacts", {}).items()):
        rel = os.path.relpath(dirpath, root)
        rows.append((rel, name, m["bytes"], m["license"], m["describes"], m["fetched_at"][:10]))
        total += m["bytes"]
if not rows:
    print("library is empty"); raise SystemExit
w = max(len(r[0]) for r in rows)
print(f"\n{'lang/ver':<{w}}  {'artifact':<42}  {'size':>8}  license")
for rel, name, b, lic, desc, when in rows:
    print(f"{rel:<{w}}  {name[:42]:<42}  {b/1e6:7.1f}M  {lic}")
    print(f"{'':<{w}}  └─ {desc}  (fetched {when})")
print(f"\n{len(rows)} artifacts, {total/1e6:.1f} MB total, root={root}")
PY
}

cmd_verify() {
  # L1 of the design: documents PROPOSE, the toolchain CONFIRMS. This is the
  # gate every synthesized line has to pass before it may be auto-injected.
  local args=(--claims "$SCRIPT_DIR/agents/lang/claims")
  [[ -n "${1:-}" ]] && args+=(--lang "$1")
  [[ -f "$SCRIPT_DIR/agents/packs/zig-0.16.md" && "${1:-}" == "zig" ]] \
    && args+=(--pack "$SCRIPT_DIR/agents/packs/zig-0.16.md")
  python3 "$SCRIPT_DIR/agents/lang/verify.py" "${args[@]}"
}

case "${1:-}" in
  acquire) shift; cmd_acquire "$@" ;;
  verify)  shift; cmd_verify "${1:-}" ;;
  check)   cmd_check ;;
  list)    cmd_list ;;
  where)   say "$LANG_DIR" ;;
  -h|--help|"") sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command: $1 (try --help)" ;;
esac
