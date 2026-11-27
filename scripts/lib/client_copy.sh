#!/usr/bin/env bash
# The client's own copy of the checkout: $CLIENT_DIR/repo.
#
# Sourced by scripts/setup-client.sh (install) and scripts/client.sh
# (`relocate`, `update`, `status`). Bash 3.2-compatible, like both of them.
#
# WHY A COPY. The tool server (agents/mcp_server.py) is not started by the
# terminal the user installed from: opencode's background service starts it,
# and so does any other MCP host. On macOS a background process is denied
# ~/Documents, ~/Desktop, ~/Downloads, iCloud Drive and removable volumes
# unless it has its own privacy grant, and the terminal's grant does not
# cover it. A client installed in place from a clone under ~/Documents then
# loses its tools with
#   can't open file '…/agents/mcp_server.py': [Errno 1] Operation not permitted
# while every check run from the terminal passes (seen 2026-11-27; autosync
# hit the same wall on 2026-11-18, client.sh AS_SNAP). So the installed client
# never runs from the checkout it was installed from: it runs from a copy
# under $CLIENT_DIR, which no privacy rule covers.
#
# WHAT THE COPY IS. The same thing the no-clone install has always made: a
# sparse git checkout (agents/ skills/ scripts/ searxng/ + the root files) at
# $CLIENT_DIR/repo. Run from inside a clone, the installer seeds it from that
# clone's HEAD commit with no network, and points its `origin` at the clone's
# own upstream, so `openbeast-client update` pulls as before. One install
# shape, two ways to seed it.
#
# NOT COPIED: uncommitted changes. A developer who wants opencode to run the
# working tree itself installs with --in-place (recorded in
# $CLIENT_DIR/in-place, so `update` leaves it alone).

OB_COPY_SPARSE="agents skills scripts searxng"

# ob_copy_phys <dir>: the directory's physical path ("" when it is not one).
ob_copy_phys() { (cd "$1" 2>/dev/null && pwd -P) || true; }

# ob_copy_protected <path>: 0 when macOS privacy rules (TCC) deny the path to
# background processes. Always 1 off macOS.
ob_copy_protected() {
  [ "$(uname -s)" = "Darwin" ] || return 1
  case "$1/" in
    "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*) return 0 ;;
    "$HOME/Library/Mobile Documents/"*|"$HOME/Library/CloudStorage/"*) return 0 ;;
    /Volumes/*) return 0 ;;
  esac
  # ~/Documents reached through a symlinked or relocated home
  _ocp_home="$(ob_copy_phys "$HOME")"
  [ -n "$_ocp_home" ] && [ "$_ocp_home" != "$HOME" ] || return 1
  case "$1/" in
    "$_ocp_home/Documents/"*|"$_ocp_home/Desktop/"*|"$_ocp_home/Downloads/"*) return 0 ;;
    "$_ocp_home/Library/Mobile Documents/"*|"$_ocp_home/Library/CloudStorage/"*) return 0 ;;
  esac
  return 1
}

# ob_copy_is_checkout <dir>: 0 when <dir> is the top of a git work tree (a
# clone, a linked worktree, or one made from a bundle) and git is installed.
ob_copy_is_checkout() {
  command -v git >/dev/null 2>&1 || return 1
  _oci_top="$(git -C "$1" rev-parse --show-toplevel 2>/dev/null)" || return 1
  [ -n "$_oci_top" ] && [ "$(ob_copy_phys "$_oci_top")" = "$(ob_copy_phys "$1")" ]
}

# ob_copy_dirty <src>: the tracked files under the copied directories that
# differ from HEAD, one per line (what the copy will NOT carry).
ob_copy_dirty() {
  # shellcheck disable=SC2086
  git -C "$1" status --porcelain --untracked-files=no -- $OB_COPY_SPARSE 2>/dev/null \
    | sed 's/^...//'
}

# ob_copy_seed <src> <dest>: make <dest> a sparse checkout of <src>'s HEAD
# commit, or move an existing one to it. Nothing but <src> is read: no
# network. Prints one "  ✓ …" line on success; returns non-zero with a reason
# on stderr and leaves any existing <dest> as it was.
ob_copy_seed() {
  _ocs_src="$1"; _ocs_dest="$2"
  _ocs_head="$(git -C "$_ocs_src" rev-parse --verify -q 'HEAD^{commit}')" || {
    echo "    (no commit checked out in $_ocs_src)" >&2; return 1; }
  _ocs_br="$(git -C "$_ocs_src" symbolic-ref -q --short HEAD 2>/dev/null || true)"
  [ -n "$_ocs_br" ] || _ocs_br="main"
  # `origin` of the copy: the clone's own upstream, so `update` keeps pulling
  # from where the user's code comes from; a clone with none (made from a
  # bundle) is its own upstream.
  _ocs_url="$(git -C "$_ocs_src" config --get remote.origin.url 2>/dev/null || true)"
  case "$_ocs_url" in
    "")              _ocs_url="$_ocs_src" ;;
    /*|*://*|*@*:*)  : ;;
    *)               _ocs_url="$(ob_copy_phys "$_ocs_src/$_ocs_url")"
                     [ -n "$_ocs_url" ] || _ocs_url="$_ocs_src" ;;
  esac

  if [ -e "$_ocs_dest" ] && [ ! -d "$_ocs_dest/.git" ]; then
    echo "    ($_ocs_dest exists and is not a git checkout — not touching it)" >&2
    return 1
  fi
  if [ -d "$_ocs_dest/.git" ]; then
    # An existing copy: move it to this commit. Local edits there are someone's
    # work; do not throw them away.
    if [ -n "$(git -C "$_ocs_dest" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
      echo "    ($_ocs_dest has local changes — commit or discard them there first)" >&2
      return 1
    fi
    _ocs_work="$_ocs_dest"; _ocs_verb="refreshed"
  else
    # A new copy is built beside its final name and renamed into place, so a
    # failure half way never leaves a broken $CLIENT_DIR/repo behind.
    _ocs_work="$_ocs_dest.new.$$"; _ocs_verb="created"
    rm -rf "$_ocs_work"
    mkdir -p "$(dirname "$_ocs_dest")" || return 1
    git init -q "$_ocs_work" 2>/dev/null || { rm -rf "$_ocs_work"; return 1; }
    # shellcheck disable=SC2086
    git -C "$_ocs_work" sparse-checkout set $OB_COPY_SPARSE >/dev/null 2>&1 || true
  fi
  # --depth 1: one commit's files, not the clone's history (a few MB, not the
  # whole .git). A plain path, never file://: a path with a space or a `#`
  # would need escaping there.
  if ! git -C "$_ocs_work" fetch -q --no-tags --depth 1 "$_ocs_src" "$_ocs_head" 2>/dev/null \
     && ! git -C "$_ocs_work" fetch -q --no-tags "$_ocs_src" HEAD 2>/dev/null; then
    echo "    (could not read commit $_ocs_head from $_ocs_src)" >&2
    [ "$_ocs_verb" = "created" ] && rm -rf "$_ocs_work"
    return 1
  fi
  if ! git -C "$_ocs_work" checkout -q -B "$_ocs_br" "$_ocs_head" 2>/dev/null; then
    echo "    (could not check out $_ocs_head in $_ocs_work)" >&2
    [ "$_ocs_verb" = "created" ] && rm -rf "$_ocs_work"
    return 1
  fi
  git -C "$_ocs_work" remote remove origin >/dev/null 2>&1 || true
  git -C "$_ocs_work" remote add origin "$_ocs_url" >/dev/null 2>&1 || true
  git -C "$_ocs_work" config "branch.$_ocs_br.remote" origin
  git -C "$_ocs_work" config "branch.$_ocs_br.merge" "refs/heads/$_ocs_br"
  if [ "$_ocs_verb" = "created" ]; then
    mv "$_ocs_work" "$_ocs_dest" || { rm -rf "$_ocs_work"; return 1; }
  fi
  echo "  ✓ client copy $_ocs_verb: $_ocs_dest (commit $(printf '%s' "$_ocs_head" | cut -c1-8) of $_ocs_src)"
}

# ob_copy_rewire <python> <opencode.json> <old-repo> <new-repo>: point the
# openbeast-tools MCP entry (either opencode layout) at <new-repo>, when it
# runs <old-repo>/agents/mcp_server.py today. Nothing else in the file
# changes. Atomic, same mode. Prints "rewired" or "unchanged".
ob_copy_rewire() {
  "$1" - "$2" "$3" "$4" <<'PYEOF'
import json, os, sys, tempfile
path, old, new = sys.argv[1:4]
try:
    cfg = json.load(open(path))
except Exception:
    print("unchanged"); raise SystemExit(0)
def same(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False
old_py = os.path.join(old, "agents", "mcp_server.py")
new_py = os.path.join(new, "agents", "mcp_server.py")
mcp = cfg.get("mcp") if isinstance(cfg.get("mcp"), dict) else {}
entries = [mcp.get("openbeast-tools")]
if isinstance(mcp.get("servers"), dict):
    entries.append(mcp["servers"].get("openbeast-tools"))
changed = False
for e in entries:
    cmd = e.get("command") if isinstance(e, dict) else None
    if not isinstance(cmd, list):
        continue
    for i, arg in enumerate(cmd):
        if isinstance(arg, str) and arg != new_py and same(arg, old_py):
            cmd[i] = new_py
            changed = True
if not changed:
    print("unchanged"); raise SystemExit(0)
real = os.path.realpath(path)             # a dotfile manager's symlink stays one
fd, tmp = tempfile.mkstemp(prefix=".opencode.json.", dir=os.path.dirname(real))
try:
    os.fchmod(fd, os.stat(real).st_mode & 0o777)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, real)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
print("rewired")
PYEOF
}

# ob_copy_tools_repo <python> <opencode.json>: the checkout the installed
# openbeast-tools entry runs from ("" when there is no such entry).
ob_copy_tools_repo() {
  [ -f "$2" ] || return 0
  "$1" - "$2" <<'PYEOF' 2>/dev/null || true
import json, os, sys
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
mcp = cfg.get("mcp") if isinstance(cfg.get("mcp"), dict) else {}
entries = [mcp.get("openbeast-tools")]
if isinstance(mcp.get("servers"), dict):
    entries.insert(0, mcp["servers"].get("openbeast-tools"))
for e in entries:
    cmd = e.get("command") if isinstance(e, dict) else None
    for arg in cmd if isinstance(cmd, list) else []:
        if isinstance(arg, str) and arg.endswith(os.path.join("agents", "mcp_server.py")):
            print(os.path.dirname(os.path.dirname(arg)))
            raise SystemExit(0)
PYEOF
}
