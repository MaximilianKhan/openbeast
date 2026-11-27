#!/bin/bash
# The client runs from its own copy (~/.openbeast-client/repo), never from the
# checkout it was installed in: scripts/lib/client_copy.sh, the installer's
# step 2, and `openbeast-client relocate` / `update` for clients an older
# installer left in place.
#
# Usage: ./tests/test_client_copy.sh
#
# Everything runs in a throwaway home against throwaway git checkouts built
# from this tree's files. No network, no real $HOME, nothing dialed (the
# installer's curl and tailscale are stubs, as in test_setup_client.sh).
#
#   1  ob_copy_seed: a sparse copy of the checkout's HEAD commit, from a path
#      with a space, from a linked worktree; refresh; the refusals
#   2  setup-client.sh inside a clone installs to the copy and wires opencode
#      and the CLI to it; --in-place and a non-git tree keep the old shape
#   3  relocate: an in-place install moves once; never with uncommitted
#      changes, an --in-place marker, or someone else's tool server
#   4  the bridge: client_rigs.py's sync relocates for an OLD client.sh

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

echo "=== client copy tests ==="
command -v git >/dev/null 2>&1 || { echo "  SKIP: git not installed"; exit 0; }
REAL_PY=""
for _c in python3.13 python3.12 python3.11 python3; do
  command -v "$_c" >/dev/null 2>&1 || continue
  if "$_c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    REAL_PY="$("$_c" -c 'import sys; print(sys.executable)')"; break
  fi
done
[[ -n "$REAL_PY" ]] || { echo "  SKIP: no python >= 3.11 on PATH"; exit 0; }

T="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-client-copy-test.XXXXXX")"
T="$(cd "$T" && pwd -P)"
trap 'rm -rf "$T"' EXIT
BIN="$T/bin"; S="$T/stub"; mkdir -p "$BIN" "$S"
G() { git -c user.email=t@example.invalid -c user.name=test -c commit.gpgsign=false "$@"; }
_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
_phys() { (cd "$1" 2>/dev/null && pwd -P); }

# A checkout shaped like the real one, under a directory with a SPACE in its
# name. The installer, the client CLI and their libs are this tree's; pydeps
# is a stub (nothing is installed).
mk_checkout() {   # $1 = dir
  mkdir -p "$1/scripts/lib" "$1/agents" "$1/skills/demo" "$1/searxng" "$1/docs"
  install -m 755 "$REPO_DIR/scripts/setup-client.sh" "$REPO_DIR/scripts/client.sh" "$1/scripts/"
  install -m 644 "$REPO_DIR/scripts/lib/client_copy.sh" "$REPO_DIR/scripts/lib/curl_auth.sh" \
                 "$REPO_DIR/scripts/lib/client_rigs.py" "$1/scripts/lib/"
  install -m 644 "$REPO_DIR/system-prompt.md" "$REPO_DIR/system-prompt-tools.md" "$1/"
  printf '#!/bin/bash\nexit 0\n' > "$1/scripts/pydeps.sh"; chmod +x "$1/scripts/pydeps.sh"
  echo '# tools' > "$1/agents/tools.py"; echo '# server' > "$1/agents/mcp_server.py"
  echo 'openai==1.0' > "$1/agents/requirements.txt"
  echo 'demo' > "$1/skills/demo/SKILL.md"; echo 'x' > "$1/searxng/settings.yml"
  echo 'big' > "$1/docs/NOT_COPIED.md"
  echo '{"provider":{"llama-cpp":{"models":{"stub-model":{"name":"Stub"}}}}}' > "$1/opencode.json"
  G -C "$1" init -q -b main && G -C "$1" add -A && G -C "$1" commit -qm one
}
SRC="$T/my docs/openbeast"; mk_checkout "$SRC"
# shellcheck source=../scripts/lib/client_copy.sh
. "$REPO_DIR/scripts/lib/client_copy.sh"

# ---------------------------------------------------------------------------
echo ""
echo "1. ob_copy_seed:"
D="$T/seed/repo"
OUT="$(ob_copy_seed "$SRC" "$D" 2>&1)"; RC=$?
if [[ $RC -eq 0 ]] && has "$OUT" "client copy created" && [[ -f "$D/agents/mcp_server.py" ]] \
   && [[ -f "$D/skills/demo/SKILL.md" ]] && [[ -x "$D/scripts/client.sh" ]] && [[ -f "$D/searxng/settings.yml" ]] \
   && [[ -f "$D/opencode.json" ]] && [[ ! -e "$D/docs" ]] \
   && [[ "$(git -C "$D" rev-parse HEAD)" == "$(git -C "$SRC" rev-parse HEAD)" ]]; then
  pass "a sparse copy of HEAD (agents/ skills/ scripts/ searxng/ + root files, no docs/), from a path with a space"
else
  fail "seed (rc=$RC): $OUT $(ls "$D" 2>&1)"
fi
if [[ "$(git -C "$D" config --get remote.origin.url)" == "$SRC" ]] \
   && [[ "$(git -C "$D" config --get branch.main.merge)" == "refs/heads/main" ]] \
   && [[ ! -e "$D.new.$$" ]] && ! compgen -G "$T/seed/repo.new.*" >/dev/null; then
  pass "a clone with no upstream is the copy's origin (update can pull from it); no temp dir left"
else
  fail "origin: $(git -C "$D" config --get remote.origin.url)"
fi
G -C "$SRC" remote add origin "https://example.invalid/acme/openbeast.git"
echo '# two' >> "$SRC/agents/tools.py"; G -C "$SRC" commit -qam two
OUT="$(ob_copy_seed "$SRC" "$D" 2>&1)"; RC=$?
if [[ $RC -eq 0 ]] && has "$OUT" "client copy refreshed" && grep -q '# two' "$D/agents/tools.py" \
   && [[ "$(git -C "$D" config --get remote.origin.url)" == "https://example.invalid/acme/openbeast.git" ]]; then
  pass "a second seed moves the existing copy to the new commit; origin follows the clone's upstream"
else
  fail "refresh (rc=$RC): $OUT"
fi
echo 'mine' >> "$D/agents/tools.py"
_before="$(git -C "$D" rev-parse HEAD)"; echo '# three' >> "$SRC/agents/tools.py"; G -C "$SRC" commit -qam three
OUT="$(ob_copy_seed "$SRC" "$D" 2>&1)"; RC=$?
if [[ $RC -ne 0 ]] && has "$OUT" "has local changes" && grep -q '^mine$' "$D/agents/tools.py" \
   && [[ "$(git -C "$D" rev-parse HEAD)" == "$_before" ]]; then
  pass "a copy with local edits is refused and left exactly as it was"
else
  fail "dirty copy (rc=$RC): $OUT"
fi
mkdir -p "$T/plain/repo"; echo keep > "$T/plain/repo/file"
OUT="$(ob_copy_seed "$SRC" "$T/plain/repo" 2>&1)"; RC=$?
if [[ $RC -ne 0 ]] && has "$OUT" "not a git checkout" && [[ "$(cat "$T/plain/repo/file")" == keep ]]; then
  pass "a directory that is not our checkout is never overwritten"
else
  fail "plain dest (rc=$RC): $OUT"
fi
G -C "$SRC" worktree add -q "$T/wt" -b side HEAD~1 >/dev/null 2>&1
OUT="$(ob_copy_seed "$T/wt" "$T/wtcopy/repo" 2>&1)"; RC=$?
if [[ $RC -eq 0 ]] && [[ "$(git -C "$T/wtcopy/repo" rev-parse HEAD)" == "$(git -C "$T/wt" rev-parse HEAD)" ]] \
   && [[ "$(git -C "$T/wtcopy/repo" symbolic-ref --short HEAD)" == side ]] \
   && ob_copy_is_checkout "$T/wt" && ! ob_copy_is_checkout "$SRC/agents" && ! ob_copy_is_checkout "$T/plain/repo"; then
  pass "a linked worktree seeds ITS commit and branch; only the top of a work tree counts as a checkout"
else
  fail "worktree (rc=$RC): $OUT"
fi
G -C "$SRC" worktree remove --force "$T/wt" >/dev/null 2>&1
if [[ "$(uname -s)" == Darwin ]]; then
  if ( HOME=/Users/x; ob_copy_protected /Users/x/Documents/p/ob && ob_copy_protected /Users/x/Desktop \
       && ob_copy_protected "/Users/x/Library/Mobile Documents/c" && ob_copy_protected /Volumes/USB/ob \
       && ! ob_copy_protected /Users/x/Documentsx/ob && ! ob_copy_protected /Users/x/.openbeast-client/repo \
       && ! ob_copy_protected /Users/x/src/ob ); then
    pass "macOS: Documents / Desktop / iCloud / volumes are protected; a look-alike name and ~/.openbeast-client are not"
  else
    fail "ob_copy_protected on macOS"
  fi
else
  if ! ( HOME=/home/x; ob_copy_protected /home/x/Documents/ob ); then
    pass "off macOS nothing is protected"
  else
    fail "ob_copy_protected off macOS"
  fi
fi

# ---------------------------------------------------------------------------
# The installer, run for real with the outside world stubbed.
printf '#!/bin/bash\nexec "%s" "$@"\n' "$REAL_PY" > "$T/vetted-python"
cat > "$BIN/tailscale" <<'STUB'
#!/bin/bash
[[ "$*" == "status --json" ]] && echo '{"Self":{"DNSName":"me.tn.ts.net.","UserID":1},"Peer":{}}'
exit 0
STUB
cat > "$BIN/curl" <<'STUB'
#!/bin/bash
case " $* " in *" -w "*) printf '200' ;; esac
exit 0
STUB
printf '#!/bin/bash\n[[ "$1" == --version ]] && echo "opencode v2.3.2"\nexit 0\n' > "$BIN/opencode"
chmod +x "$T/vetted-python" "$BIN"/*
GIT_DIR_BIN="$(dirname "$(command -v git)")"
# install <home> <checkout> [args…]: sets OUT RC H OC
install_from() {
  H="$T/$1"; local co="$2"; shift 2
  mkdir -p "$H/.openbeast-client/venv/bin"
  # the venv's python: real, except that the pinned deps "import" (none are
  # installed here; pydeps.sh is a stub)
  printf '#!/bin/bash\ncase "${2:-}" in *"import mcp"*) exit 0 ;; esac\nexec "%s" "$@"\n' "$REAL_PY" \
    > "$H/.openbeast-client/venv/bin/python3"
  printf '#!/bin/bash\nexit 0\n' > "$H/.openbeast-client/venv/bin/pip"
  chmod +x "$H/.openbeast-client/venv/bin/python3" "$H/.openbeast-client/venv/bin/pip"
  OC="$H/.config/opencode/opencode.json"
  OUT="$(env -i HOME="$H" PATH="$BIN:$GIT_DIR_BIN:/usr/bin:/bin" OPENBEAST_PYTHON="$T/vetted-python" \
         OPENBEAST_AUTOSYNC=0 bash "$co/scripts/setup-client.sh" --host rig.example --no-search "$@" </dev/null 2>&1)"
  RC=$?
}
_tools_py() { "$REAL_PY" -c '
import json,sys
m=json.load(open(sys.argv[1])).get("mcp",{})
e=(m.get("servers") or {}).get("openbeast-tools") or m.get("openbeast-tools") or {}
print((e.get("command") or ["",""])[1])' "$1"; }

echo ""
echo "2. setup-client.sh from inside a clone:"
install_from h1 "$SRC"
C="$H/.openbeast-client/repo"
if [[ $RC -eq 0 ]] && has "$OUT" "client copy created" && has "$OUT" "Client mode ready" \
   && [[ "$(_tools_py "$OC")" == "$C/agents/mcp_server.py" ]] \
   && [[ "$(readlink "$H/.local/bin/openbeast-client")" == "$C/scripts/client.sh" ]] \
   && [[ ! -e "$H/.openbeast-client/in-place" ]] && ! grep -q "$SRC" "$OC"; then
  pass "the tool server and the CLI point into ~/.openbeast-client/repo; nothing in opencode.json names the checkout"
else
  fail "install to copy (rc=$RC, tools=$(_tools_py "$OC" 2>&1)): $OUT"
fi
if [[ -z "$(G -C "$SRC" status --porcelain)" ]]; then
  pass "the checkout the installer ran from is untouched"
else
  fail "checkout modified: $(G -C "$SRC" status --porcelain)"
fi
echo '# uncommitted' >> "$SRC/agents/tools.py"; echo '# four' >> "$SRC/agents/mcp_server.py"
G -C "$SRC" commit -qm four agents/mcp_server.py
install_from h1 "$SRC"
if [[ $RC -eq 0 ]] && has "$OUT" "client copy refreshed" && grep -q '# four' "$C/agents/mcp_server.py" \
   && ! grep -q '# uncommitted' "$C/agents/tools.py" \
   && has "$OUT" "uncommitted changes" && has "$OUT" "agents/tools.py" && has "$OUT" "--in-place"; then
  pass "a re-run moves the copy to the new commit, and names the uncommitted file it does not carry"
else
  fail "re-run (rc=$RC): $OUT"
fi
G -C "$SRC" checkout -q agents/tools.py
install_from h1 "$C"
if [[ $RC -eq 0 ]] && has "$OUT" "running from the client copy" && ! has "$OUT" "client copy created" \
   && [[ "$(_tools_py "$OC")" == "$C/agents/mcp_server.py" ]]; then
  pass "run from the copy itself (what update does), it is used as is"
else
  fail "run from copy (rc=$RC): $OUT"
fi
install_from h2 "$SRC" --in-place
if [[ $RC -eq 0 ]] && has "$OUT" "using it in place" && [[ "$(_tools_py "$OC")" == "$SRC/agents/mcp_server.py" ]] \
   && [[ "$(cat "$H/.openbeast-client/in-place")" == "$SRC" ]] && [[ ! -e "$H/.openbeast-client/repo" ]]; then
  pass "--in-place runs the checkout itself, makes no copy, and records the choice"
else
  fail "--in-place (rc=$RC): $OUT"
fi
install_from h2 "$SRC"
if [[ $RC -eq 0 ]] && [[ ! -e "$H/.openbeast-client/in-place" ]] \
   && [[ "$(_tools_py "$OC")" == "$H/.openbeast-client/repo/agents/mcp_server.py" ]]; then
  pass "a later plain install moves that client to the copy and drops the marker"
else
  fail "in-place then plain (rc=$RC): $OUT"
fi
NG="$T/nogit"; mkdir -p "$NG"; (cd "$SRC" && tar cf - --exclude .git .) | (cd "$NG" && tar xf -)
install_from h3 "$NG"
if [[ $RC -eq 0 ]] && has "$OUT" "not a git checkout — using it in place" \
   && [[ "$(_tools_py "$OC")" == "$NG/agents/mcp_server.py" ]]; then
  pass "a tree with no git history (an unpacked archive) still installs, in place"
else
  fail "non-git tree (rc=$RC): $OUT"
fi

# ---------------------------------------------------------------------------
echo ""
echo "3. relocate: a client an older installer left running in place:"
# What that install looks like: opencode runs the checkout's mcp_server.py and
# the CLI symlink points into the checkout.
old_install() {   # $1 = home name; sets H OC C
  H="$T/$1"; OC="$H/.config/opencode/opencode.json"; C="$H/.openbeast-client/repo"
  mkdir -p "$H/.openbeast-client/venv/bin" "$H/.local/bin" "$(dirname "$OC")"
  printf '#!/bin/bash\nexec "%s" "$@"\n' "$REAL_PY" > "$H/.openbeast-client/venv/bin/python3"
  chmod +x "$H/.openbeast-client/venv/bin/python3"
  cat > "$OC" <<JSON
{"mcp":{"servers":{"openbeast-tools":{"type":"local","command":["$H/.openbeast-client/venv/bin/python3","$SRC/agents/mcp_server.py"],"environment":{"OPENBEAST_API_KEY":"sekret"},"codemode":false},
 "mine":{"type":"local","command":["node","/x/server.js"]}}},"provider":{"openbeast-rig":{"options":{"apiKey":"sekret"}}},"theme":"mine"}
JSON
  chmod 600 "$OC"
  ln -sf "$SRC/scripts/client.sh" "$H/.local/bin/openbeast-client"
}
cli() { env -i HOME="$H" PATH="$BIN:$GIT_DIR_BIN:/usr/bin:/bin" bash "$SRC/scripts/client.sh" "$@" </dev/null 2>&1; }
old_install r1
OUT="$(cli relocate)"; RC=$?
if [[ $RC -eq 0 ]] && has "$OUT" "runs from its own copy now" && [[ "$(_tools_py "$OC")" == "$C/agents/mcp_server.py" ]] \
   && [[ "$(readlink "$H/.local/bin/openbeast-client")" == "$C/scripts/client.sh" ]] \
   && [[ "$(_mode "$OC")" == 600 ]] && grep -q '"theme": "mine"' "$OC" && grep -q '/x/server.js' "$OC" \
   && grep -q '"apiKey": "sekret"' "$OC" && [[ "$(git -C "$C" rev-parse HEAD)" == "$(git -C "$SRC" rev-parse HEAD)" ]]; then
  pass "relocate: copy made, openbeast-tools and the CLI repointed, the rest of opencode.json and its 0600 kept"
else
  fail "relocate (rc=$RC, mode=$(_mode "$OC")): $OUT $(cat "$OC")"
fi
_snap="$(cat "$OC")"
OUT="$(cli relocate)"
OUT2="$(env -i HOME="$H" PATH="$BIN:$GIT_DIR_BIN:/usr/bin:/bin" bash "$C/scripts/client.sh" relocate </dev/null 2>&1)"
if has "$OUT" "nothing to move" && has "$OUT2" "already runs from its own copy" && [[ "$(cat "$OC")" == "$_snap" ]]; then
  pass "a second relocate changes nothing, from the checkout or from the copy"
else
  fail "relocate twice: $OUT / $OUT2"
fi
old_install r2; echo '# wip' >> "$SRC/scripts/client.sh"
OUT="$(cli relocate --auto)"; RC=$?
if [[ $RC -eq 0 ]] && has "$OUT" "uncommitted changes" && [[ "$(_tools_py "$OC")" == "$SRC/agents/mcp_server.py" ]] \
   && [[ ! -e "$C" ]]; then
  pass "auto: a checkout with uncommitted changes stays in place, and says how to move or keep it"
else
  fail "auto dirty (rc=$RC): $OUT"
fi
OUT="$(cli relocate)"
if has "$OUT" "runs from its own copy now" && has "$OUT" "NOT in the copy" && has "$OUT" "scripts/client.sh" \
   && [[ "$(_tools_py "$OC")" == "$C/agents/mcp_server.py" ]]; then
  pass "asked for by name, it moves anyway and names what the copy lacks"
else
  fail "asked dirty: $OUT"
fi
G -C "$SRC" checkout -q scripts/client.sh
old_install r3; echo "$SRC" > "$H/.openbeast-client/in-place"
OUT="$(cli relocate --auto)"
if [[ -z "$OUT" ]] && [[ "$(_tools_py "$OC")" == "$SRC/agents/mcp_server.py" ]] && [[ ! -e "$C" ]]; then
  pass "auto: an --in-place install is left alone, silently"
else
  fail "auto in-place: $OUT"
fi
old_install r4
"$REAL_PY" - "$OC" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["mcp"]["servers"]["openbeast-tools"]["command"][1]="/elsewhere/openbeast/agents/mcp_server.py"
json.dump(d,open(p,"w"))
PY
OUT="$(cli relocate --auto)"
if [[ -z "$OUT" ]] && [[ "$(_tools_py "$OC")" == "/elsewhere/openbeast/agents/mcp_server.py" ]] && [[ ! -e "$C" ]]; then
  pass "auto: a tool server that runs from some OTHER checkout is not ours to move"
else
  fail "auto foreign: $OUT"
fi
old_install r5; rm -f "$OC"
OUT="$(cli relocate --auto)"
if [[ -z "$OUT" ]] && [[ ! -e "$C" ]]; then
  pass "auto: a checkout with no installed client (no opencode.json) does nothing"
else
  fail "auto no install: $OUT"
fi

# `update` is where existing installs meet this: it relocates, then carries on
# from the copy. (--no-pull: nothing is fetched; the provider has no baseURL,
# so the catalog refresh dials nothing.)
old_install r6
OUT="$(cli update --no-pull)"; RC=$?
if [[ $RC -eq 0 ]] && has "$OUT" "runs from its own copy now" && [[ "$(_tools_py "$OC")" == "$C/agents/mcp_server.py" ]] \
   && has "$OUT" "SOURCE NOT UPDATED"; then
  pass "update relocates an in-place client by itself, and finishes"
else
  fail "update (rc=$RC): $OUT"
fi
_snap="$(_tools_py "$OC")"
OUT="$(env -i HOME="$H" PATH="$BIN:$GIT_DIR_BIN:/usr/bin:/bin" bash "$H/.local/bin/openbeast-client" update --no-pull </dev/null 2>&1)"; RC=$?
if [[ $RC -eq 0 ]] && ! has "$OUT" "own copy" && [[ "$(_tools_py "$OC")" == "$_snap" ]]; then
  pass "the next update, through the repointed CLI, runs from the copy and says nothing about it"
else
  fail "second update (rc=$RC): $OUT"
fi

# ---------------------------------------------------------------------------
echo ""
echo "4. the bridge for an OLD client.sh (client_rigs.py asks the new one):"
old_install b1
hook() { env -i HOME="$H" PATH="$BIN:$GIT_DIR_BIN:/usr/bin:/bin" OB_REPO="$1" ${2:+"$2"} "$REAL_PY" -c '
import sys; sys.path.insert(0, sys.argv[1]); import client_rigs; client_rigs._relocate_in_place_client()' \
  "$SRC/scripts/lib" </dev/null 2>&1; }
OUT="$(hook "$SRC" OB_CLIENT_RELOCATES=1)"
if [[ -z "$OUT" ]] && [[ ! -e "$C" ]]; then
  pass "called under a client.sh that relocates by itself, the sync does not ask again"
else
  fail "bridge under new client.sh: $OUT"
fi
OUT="$(hook "$SRC")"
if has "$OUT" "runs from its own copy now" && [[ "$(_tools_py "$OC")" == "$C/agents/mcp_server.py" ]]; then
  pass "called by an old client.sh (no marker in the env), the sync relocates the client"
else
  fail "bridge: $OUT"
fi
_snap="$(cat "$OC")"
OUT="$(hook "$C")"; OUT2="$(hook "$H/.openbeast-client/autosync")"
if [[ -z "$OUT$OUT2" ]] && [[ "$(cat "$OC")" == "$_snap" ]]; then
  pass "from the copy, or from autosync's snapshot, the bridge does nothing"
else
  fail "bridge from copy: $OUT $OUT2"
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
