#!/usr/bin/env bash
# OpenBeast — publish HTML as a page with a durable URL (beast-artifact).
#
#   ./scripts/artifact.sh publish <file.html> [--title "T"] [--description "D"]
#                                 [--favicon 📊] [--id ID] [--label "L"]
#                                 [--file published=source]... [--visibility private|tailnet]
#   ./scripts/artifact.sh list [--json]
#   ./scripts/artifact.sh show <id> [--json]
#   ./scripts/artifact.sh versions <id>
#   ./scripts/artifact.sh rollback <id> <n>
#   ./scripts/artifact.sh visibility <id> private|tailnet
#   ./scripts/artifact.sh remove <id> --yes
#
# This is the shell half of the tool surface the model gets as
# publish_artifact/list_artifacts (agents/mcp_server.py): the same store, the
# same URLs, reachable from campaign scripts, background agents (via their
# bash tool), and Max's own terminal without touching the runner registry.
# Design: docs/BEAST_ARTIFACT_PLAN.md.
#
# Talks to the artifact server on loopback (ARTIFACT_PORT, default 3004).
# WRITE verbs (POST/PATCH/DELETE) carry the proof-of-locality token from
# .run/artifact-local.token — the file is 0600, so "can read it" IS "is on
# this box" (the transport peer proves nothing: tailscale serve reverse-
# proxies every remote caller in from 127.0.0.1). The token is handed to curl
# through a 0600 --config file in this script's own 0700 tmpdir, NEVER as
# -H on the command line: argv is world-readable in /proc, so every uid on
# the box could lift the write credential out of `ps` during any call.
#
# Exit codes: 0 ok · 2 usage/validation · 3 the server said no (HTTP non-2xx)
#             4 nothing answering on the port, or no locality token
#             5 the request itself failed (curl transport error)
#
# Wire contract for --file (agents/artifact_server.py PublishBody): supporting
# files ride in the request body as
#   "files": {"<published path>": "<utf-8 text>" | {"b64": "<base64>"}}
# — the envelope exists because a version may legally carry binaries (images,
# fonts) that JSON cannot hold as text. The published path is the key; the
# source path on disk is the value side of --file published=source and never
# leaves this box.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
# Deliberately does NOT source lib/conf.sh — same reason as scripts/clients.sh:
# sourcing it has a SIDE EFFECT (the SearXNG-secret bootstrap creates and
# appends to openbeast.conf), and `artifact.sh list` must never mutate the
# rig's config. The one value needed here is read directly below, keeping the
# documented env-over-conf-over-default precedence.
RUN_DIR="$REPO_DIR/.run"
TOKEN_FILE="$RUN_DIR/artifact-local.token"

_usage() { sed -n '4,12p' "$0" | sed 's/^# \{0,1\}//'; }
_die() { echo "ERROR: $*" >&2; exit 2; }

# One KEY= value out of openbeast.conf; last assignment wins, quotes trimmed.
# Mirrors _ob_conf_value in lib/conf.sh without the sourcing side effect.
_conf_value() {
  local key="$1" conf="$REPO_DIR/openbeast.conf" line
  [[ -f "$conf" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$conf" 2>/dev/null | tail -n1)" || return 1
  [[ -n "$line" ]] || return 1
  line="${line#*=}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  line="${line#\"}"; line="${line%\"}"
  line="${line#\'}"; line="${line%\'}"
  [[ -n "$line" ]] || return 1
  printf '%s\n' "$line"
}

PORT="${OPENBEAST_ARTIFACT_PORT:-$(_conf_value ARTIFACT_PORT || echo 3004)}"
case "$PORT" in
  ''|*[!0-9]*) _die "ARTIFACT_PORT is not a number: '$PORT'" ;;
esac
BASE="http://127.0.0.1:$PORT"
TOKEN="$(cat "$TOKEN_FILE" 2>/dev/null || true)"

TMPDIR_RUN="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-artifact.XXXXXX")"   # 0700
trap 'rm -rf "$TMPDIR_RUN"' EXIT
BODY="$TMPDIR_RUN/body"
REQ="$TMPDIR_RUN/req.json"
CURL_CFG="$TMPDIR_RUN/curl.cfg"
CURL_ERR="$TMPDIR_RUN/curl.err"

_no_server() {
  echo "ERROR: beast-artifact is not answering on $BASE." >&2
  echo "" >&2
  echo "  Is it enabled?   grep BEAST_ARTIFACT $REPO_DIR/openbeast.conf" >&2
  echo "  Turn it on:      set BEAST_ARTIFACT=true in openbeast.conf, then" >&2
  echo "                   ./stop.sh && ./start.sh" >&2
  echo "  Already on?      ./start.sh --status   and   ./scripts/doctor.sh" >&2
  exit 4
}

_transport_failed() {  # _transport_failed <curl-exit-code>
  echo "ERROR: the request to $BASE failed (curl exit $1)." >&2
  if [[ -s "$CURL_ERR" ]]; then sed 's/^/       /' "$CURL_ERR" >&2; fi
  echo "" >&2
  echo "       The server is reachable — this is not 'the stack is down', so" >&2
  echo "       restarting it will not help. Check the message above." >&2
  exit 5
}

# The server's own id rule (agents/artifact.py:_ID_RE). Validated HERE so a
# typo'd id is a usage error naming the id, not a malformed URL that curl
# rejects and the old code reported as "beast-artifact is not answering —
# restart the stack".
_check_id() {
  local id="$1" what="$2"
  case "$id" in
    .|..) _die "$what: '$id' is not an artifact id" ;;
  esac
  if ! printf '%s' "$id" | grep -qE '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'; then
    _die "$what: '$id' is not an artifact id (letters, digits, . _ - only," \
         "64 chars max — it is the last part of the artifact URL)"
  fi
}

# Percent-encode for a path segment. _check_id already restricts the alphabet
# to characters that need no encoding; this is the belt to that suspenders, so
# no future caller can hand curl a URL it has to guess at.
_urlenc() {
  local s="$1" out="" i c
  for (( i = 0; i < ${#s}; i++ )); do
    c="${s:i:1}"
    case "$c" in
      [A-Za-z0-9._~-]) out="$out$c" ;;
      *)               out="$out$(printf '%%%02X' "'$c")" ;;
    esac
  done
  printf '%s' "$out"
}

# The locality token goes in a 0600 file curl reads with --config, never in
# argv: `ps` is world-readable, so -H "X-OpenBeast-Local: $TOKEN" broadcast
# the write credential to every uid on the box on every call — including the
# read-only ones, which do not need it at all.
_token_config() {
  [[ -s "$CURL_CFG" ]] && return 0
  ( umask 077; : > "$CURL_CFG" )
  printf 'header = "X-OpenBeast-Local: %s"\n' "$TOKEN" > "$CURL_CFG"
}

# Reads tolerate a missing token up front so the failure is the server's 404
# with its own explanation, not a local error about a file the user may not
# know exists. Writes keep the hard check.
_have_token() { [[ -n "$TOKEN" ]]; }

_need_token() {
  if [[ -n "$TOKEN" ]]; then return 0; fi
  echo "ERROR: no locality token at $TOKEN_FILE." >&2
  echo "       Publishing, rollback, visibility and remove are loopback-only" >&2
  echo "       and prove locality with that 0600 file — the server writes it" >&2
  echo "       at startup. Start the stack (./start.sh) and retry." >&2
  exit 4
}

# _api <METHOD> <path> [request-body-file] — response lands in $BODY, the HTTP
# status is echoed.
#
# Two transport outcomes, deliberately NOT merged: curl 7 (connection refused)
# and 28 (timeout) mean nothing is answering, which is the "start the stack"
# advice; every other curl failure (malformed URL, unreadable body file, a
# broken --config) is OUR bug or the caller's, and telling the operator to
# restart a healthy stack for it wastes their evening.
_api() {
  local method="$1" path="$2" reqfile="${3:-}" code rc=0
  local args
  args=(-s -S -m 60 -o "$BODY" -w '%{http_code}' -X "$method")
  # The token proves two different things and both are needed here.
  #
  # On a WRITE it is the write credential: the server refuses POST/PATCH/DELETE
  # from anything that cannot present it, which is what keeps a phone on the
  # tailnet able to view a page and never create or delete one.
  #
  # On a READ it is our IDENTITY. The server refuses anonymous callers outright
  # — a request with no tailnet login and no token is 404 on every route — so a
  # CLI GET that sent nothing would be indistinguishable from a stranger and
  # `artifact.sh list` would report an empty gallery on a rig full of pages.
  # (That rule exists because loopback is not a trust boundary against a
  # browser: any page the operator visits can reach 127.0.0.1.)
  #
  # Either way it travels in a 0600 --config file, never in argv.
  case "$method" in
    POST|PUT|PATCH|DELETE) _need_token ;;
    *) _have_token || true ;;
  esac
  if [[ -n "$TOKEN" ]]; then
    _token_config
    args+=(--config "$CURL_CFG")
  fi
  if [[ -n "$reqfile" ]]; then
    args+=(-H 'Content-Type: application/json' --data-binary "@$reqfile")
  fi
  : > "$CURL_ERR"
  code="$(curl "${args[@]}" "$BASE$path" 2>"$CURL_ERR")" || rc=$?
  if [[ $rc -ne 0 ]]; then
    case "$rc" in
      7|28) _no_server ;;
      *)    _transport_failed "$rc" ;;
    esac
  fi
  [[ -n "$code" && "$code" != "000" ]] || _no_server
  printf '%s' "$code"
}

# The `id` field out of the response in $BODY (empty if there is none).
_body_id() {
  OB_BODY="$BODY" python3 -c '
import json, os, sys
try:
    doc = json.load(open(os.environ["OB_BODY"]))
except Exception:
    sys.exit(0)
if isinstance(doc, dict):
    print(str(doc.get("id") or doc.get("artifact_id") or ""))' 2>/dev/null || true
}

# _check <status> <what> — exit with the server's own message on a non-2xx.
_check() {
  local code="$1" what="$2"
  case "$code" in
    2*) return 0 ;;
  esac
  local msg
  msg="$(OB_BODY="$BODY" python3 -c '
import json, os, sys
try:
    doc = json.load(open(os.environ["OB_BODY"]))
except Exception:
    sys.exit(0)
d = doc.get("detail", doc) if isinstance(doc, dict) else doc
if isinstance(d, dict):
    d = d.get("message") or d.get("error") or json.dumps(d)
print(str(d)[:500])' 2>/dev/null || true)"
  echo "ERROR: $what failed (HTTP $code)${msg:+: $msg}" >&2
  if [[ "$code" == "404" ]]; then
    echo "       A private artifact owned by someone else reports 404 too." >&2
  fi
  exit 3
}

# Render the response with python — bash never parses JSON here (clients.sh
# doctrine: one parser, no hand-rolled field extraction).
_render() { # _render <mode> [extra]
  OB_BODY="$BODY" OB_MODE="$1" OB_EXTRA="${2:-}" python3 - <<'PY'
import json, os, sys

mode = os.environ["OB_MODE"]
try:
    doc = json.load(open(os.environ["OB_BODY"]))
except Exception as e:
    sys.stderr.write("ERROR: the server returned something that is not JSON (%s)\n" % e)
    sys.exit(3)

if mode == "json":
    json.dump(doc, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    sys.exit(0)


def rows(d):
    if isinstance(d, list):
        return d
    for key in ("artifacts", "items", "results"):
        if isinstance(d.get(key), list):
            return d[key]
    return []


def nver(a):
    v = a.get("versions")
    if isinstance(v, list):
        return len(v)
    return v or a.get("version") or 1


def stamp(val):
    if not val:
        return "-"
    val = str(val)
    return val[:16].replace("T", " ") if len(val) >= 16 else val


def ident(a):
    return a.get("id") or a.get("artifact_id") or "?"


if mode == "list":
    items = rows(doc)
    if not items:
        print("No artifacts published yet.")
        print("  Publish one:  ./scripts/artifact.sh publish page.html --title \"My page\"")
        sys.exit(0)
    print("%-36s  %-30s  %4s  %-8s  %s" % ("ID", "TITLE", "VERS", "VIS", "UPDATED"))
    for a in items:
        title = str(a.get("title") or "(untitled)")
        if len(title) > 30:
            title = title[:29] + "…"
        print("%-36s  %-30s  %4s  %-8s  %s" % (
            ident(a), title, "v%s" % nver(a),
            a.get("visibility", "?"),
            stamp(a.get("updated_at") or a.get("created_at"))))
    print("")
    print("  Details: ./scripts/artifact.sh show <id>")
    sys.exit(0)

if mode == "show":
    a = doc.get("artifact", doc) if isinstance(doc, dict) else doc
    print("id:          %s" % ident(a))
    print("title:       %s" % (a.get("title") or "(untitled)"))
    if a.get("description"):
        print("description: %s" % a["description"])
    if a.get("favicon"):
        print("favicon:     %s" % a["favicon"])
    print("url:         %s" % (a.get("url") or ""))
    print("visibility:  %s" % a.get("visibility", "?"))
    print("owner:       %s" % (a.get("owner") or "-"))
    print("versions:    %s (current v%s)" % (nver(a), a.get("current") or nver(a)))
    print("created:     %s" % stamp(a.get("created_at")))
    if a.get("updated_at"):
        print("updated:     %s" % stamp(a["updated_at"]))
    sys.exit(0)

if mode == "versions":
    a = doc.get("artifact", doc) if isinstance(doc, dict) else doc
    vs = a.get("versions")
    if not isinstance(vs, list) or not vs:
        print("No version history recorded for %s." % ident(a))
        sys.exit(0)
    current = a.get("current")
    print("%-2s %-4s  %-16s  %10s  %s" % ("", "VER", "PUBLISHED", "BYTES", "LABEL"))
    for v in vs:
        if not isinstance(v, dict):
            continue
        n = v.get("n") or v.get("version")
        mark = "*" if current is not None and n == current else ""
        print("%-2s %-4s  %-16s  %10s  %s" % (
            mark, "v%s" % n, stamp(v.get("ts") or v.get("created_at")),
            v.get("bytes", "-"), v.get("label") or ""))
    print("")
    print("  * = the version served at the artifact's URL")
    print("  Roll back with: ./scripts/artifact.sh rollback %s <n>" % ident(a))
    sys.exit(0)

if mode == "published":
    print('Published "%s" → %s (v%s)' % (
        doc.get("title") or "(untitled)", doc.get("url") or "",
        doc.get("version") or 1))
    print("  id:    %s" % ident(doc))
    if doc.get("bytes") is not None:
        print("  bytes: %s" % doc["bytes"])
    print("")
    print("  Update it in place (same URL, new version):")
    print("    ./scripts/artifact.sh publish <file.html> --id %s" % ident(doc))
    sys.exit(0)

if mode == "patched":
    a = doc.get("artifact", doc) if isinstance(doc, dict) else doc
    print("%s: %s" % (ident(a), os.environ.get("OB_EXTRA", "updated")))
    if a.get("url"):
        print("  %s" % a["url"])
    sys.exit(0)

sys.stderr.write("internal: unknown render mode %r\n" % mode)
sys.exit(3)
PY
}

# ---------------------------------------------------------------------------
cmd="${1:-list}"
if [[ $# -gt 0 ]]; then shift; fi

case "$cmd" in
  publish)
    src="${1:-}"
    [[ -n "$src" ]] || _die "usage: artifact.sh publish <file.html> [--title \"T\"] [--id ID] ..."
    case "$src" in -*) _die "usage: artifact.sh publish <file.html> [options] — the file comes first" ;; esac
    shift
    [[ -f "$src" ]] || _die "no such file: $src"
    [[ -r "$src" ]] || _die "cannot read: $src"
    [[ -s "$src" ]] || _die "$src is empty — nothing to publish"
    title=""; description=""; favicon=""; art_id=""; label=""; visibility="private"
    # "given and empty" is not "not given": --description "" means CLEAR it.
    # The old code dropped every empty value on the floor and reported
    # success, so there was no way to unset a description at all.
    title_set=0; desc_set=0
    file_pairs=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --title)        [[ $# -ge 2 ]] || _die "--title needs a value"; title="$2"; title_set=1; shift 2 ;;
        --title=*)      title="${1#*=}"; title_set=1; shift ;;
        --description)  [[ $# -ge 2 ]] || _die "--description needs a value"; description="$2"; desc_set=1; shift 2 ;;
        --description=*) description="${1#*=}"; desc_set=1; shift ;;
        --favicon)      [[ $# -ge 2 ]] || _die "--favicon needs a value"; favicon="$2"; shift 2 ;;
        --favicon=*)    favicon="${1#*=}"; shift ;;
        --id)           [[ $# -ge 2 ]] || _die "--id needs a value"; art_id="$2"; shift 2 ;;
        --id=*)         art_id="${1#*=}"; shift ;;
        --label)        [[ $# -ge 2 ]] || _die "--label needs a value"; label="$2"; shift 2 ;;
        --label=*)      label="${1#*=}"; shift ;;
        --visibility)   [[ $# -ge 2 ]] || _die "--visibility needs a value"; visibility="$2"; shift 2 ;;
        --visibility=*) visibility="${1#*=}"; shift ;;
        --file)         [[ $# -ge 2 ]] || _die "--file needs published=source"; file_pairs+=("$2"); shift 2 ;;
        --file=*)       file_pairs+=("${1#*=}"); shift ;;
        *)              _die "unknown option for publish: $1" ;;
      esac
    done
    case "$visibility" in
      private|tailnet) ;;
      *) _die "--visibility takes 'private' or 'tailnet' (got: $visibility)" ;;
    esac
    [[ -n "$art_id" ]] && _check_id "$art_id" "--id"
    if [[ $title_set -eq 1 && -z "$title" ]]; then
      # A page always has a title (the store falls back to the file's own
      # <title>, then "Untitled"), so an empty one cannot be honored. Say so
      # instead of silently publishing under the old title.
      _die "--title cannot be empty: a page always has a title." \
           "Omit --title to keep the current one, or to let the file's" \
           "own <title> supply it."
    fi
    for _pair in ${file_pairs[@]+"${file_pairs[@]}"}; do
      case "$_pair" in
        *=*) ;;
        *)   _die "--file takes published=source (got: $_pair)" ;;
      esac
      _fpub="${_pair%%=*}"
      case "$_fpub" in
        # The published path is where the file is served NEXT TO the page, so
        # an absolute one is meaningless — and used to be silently stripped to
        # a relative one and published somewhere the caller never named, under
        # a success line. Caught here, before the token check, so it is a plain
        # usage error on a rig with no stack running.
        /*|\\*) _die "--file published path must be relative, not '$_fpub'" \
                   "— it is the path the page references (<script src=\"app.js\">)," \
                   "not a path on this box. Did you mean '${_fpub#/}'?" ;;
      esac
      _fsrc="${_pair#*=}"
      [[ -f "$_fsrc" && -r "$_fsrc" ]] || _die "--file source not readable: $_fsrc"
    done
    _need_token

    # Body assembly in python: it reads the page and every supporting file,
    # decides text-vs-base64 per file, and emits valid JSON. Bash never
    # escapes a byte of it.
    OB_SRC="$src" OB_TITLE="$title" OB_DESC="$description" OB_FAVICON="$favicon" \
    OB_ID="$art_id" OB_LABEL="$label" OB_VIS="$visibility" OB_OUT="$REQ" \
    python3 - ${file_pairs[@]+"${file_pairs[@]}"} <<'PY' || exit $?
import base64, json, os, sys

MAX_TEXT = 16 * 1024 * 1024
MAX_BIN = 15 * 1024 * 1024
MAX_FILES = 255
MAX_TOTAL = 64 * 1024 * 1024

raw = open(os.environ["OB_SRC"], "rb").read()
if len(raw) > MAX_TEXT:
    sys.stderr.write("ERROR: %s is %.1f MB — the per-page cap is 16 MB\n"
                     % (os.environ["OB_SRC"], len(raw) / 1048576.0))
    sys.exit(2)
try:
    html = raw.decode("utf-8")
except UnicodeDecodeError as e:
    sys.stderr.write("ERROR: %s is not valid UTF-8 (%s)\n" % (os.environ["OB_SRC"], e))
    sys.exit(2)

pairs = sys.argv[1:]
if len(pairs) > MAX_FILES:
    sys.stderr.write("ERROR: %d supporting files — the cap is %d\n" % (len(pairs), MAX_FILES))
    sys.exit(2)

files = {}
total = len(raw)
for pair in pairs:
    published, _, source = pair.partition("=")
    published = published.strip()
    if not published:
        sys.stderr.write("ERROR: --file %r has an empty published path\n" % pair)
        sys.exit(2)
    # The published path is where the file is served NEXT TO the page. An
    # absolute one used to be silently stripped to a relative one and
    # published somewhere the caller never asked for, under a success line.
    if published.startswith("/") or published.startswith("\\"):
        sys.stderr.write(
            "ERROR: --file published path must be relative, not %r — it is "
            "the path the\n       page references (<script src=\"app.js\">), "
            "not a path on this box.\n       Did you mean %r?\n"
            % (published, published.lstrip("/\\")))
        sys.exit(2)
    if published in files:
        sys.stderr.write("ERROR: --file publishes %r twice\n" % published)
        sys.exit(2)
    blob = open(source, "rb").read()
    total += len(blob)
    try:
        files[published] = blob.decode("utf-8")
        cap, kind = MAX_TEXT, "text"
    except UnicodeDecodeError:
        files[published] = {"b64": base64.b64encode(blob).decode("ascii")}
        cap, kind = MAX_BIN, "binary"
    if len(blob) > cap:
        sys.stderr.write("ERROR: %s is %.1f MB — the per-file %s cap is %d MB\n"
                         % (source, len(blob) / 1048576.0, kind, cap // 1048576))
        sys.exit(2)
if total > MAX_TOTAL:
    sys.stderr.write("ERROR: %.1f MB in this version — the cap is 64 MB\n"
                     % (total / 1048576.0))
    sys.exit(2)

body = {"html": html, "visibility": os.environ["OB_VIS"]}
for key, env in (("title", "OB_TITLE"), ("description", "OB_DESC"),
                 ("favicon", "OB_FAVICON"), ("artifact_id", "OB_ID"),
                 ("label", "OB_LABEL")):
    val = os.environ.get(env, "").strip()
    if val:
        body[key] = val
if files:
    body["files"] = files

with open(os.environ["OB_OUT"], "w") as fh:
    json.dump(body, fh)
PY
    code="$(_api POST /api/artifacts "$REQ")" || exit $?
    _check "$code" "publish"
    _render published
    # publish() ignores an empty description (it never clears metadata), so an
    # explicit --description "" is honored with the one call that does:
    # PATCH, the same route `visibility` uses.
    if [[ $desc_set -eq 1 && -z "$description" ]]; then
      _pub_id="$(_body_id)"
      if [[ -n "$_pub_id" ]]; then
        printf '{"description": ""}' > "$REQ"
        code="$(_api PATCH "/api/artifacts/$(_urlenc "$_pub_id")" "$REQ")" || exit $?
        _check "$code" "clearing the description"
        echo "  description: cleared"
      fi
    fi
    ;;

  list)
    json=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --json) json=1; shift ;;
        *)      _die "unknown option for list: $1" ;;
      esac
    done
    code="$(_api GET /api/artifacts)" || exit $?
    _check "$code" "list"
    if [[ $json -eq 1 ]]; then _render json; else _render list; fi
    ;;

  show)
    art_id="${1:-}"
    [[ -n "$art_id" ]] || _die "usage: artifact.sh show <id> [--json]"
    shift
    json=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --json) json=1; shift ;;
        *)      _die "unknown option for show: $1" ;;
      esac
    done
    _check_id "$art_id" "show"
    code="$(_api GET "/api/artifacts/$(_urlenc "$art_id")")" || exit $?
    _check "$code" "show"
    if [[ $json -eq 1 ]]; then _render json; else _render show; fi
    ;;

  versions)
    art_id="${1:-}"
    [[ -n "$art_id" ]] || _die "usage: artifact.sh versions <id>"
    shift
    [[ $# -eq 0 ]] || _die "unknown option for versions: $1"
    _check_id "$art_id" "versions"
    code="$(_api GET "/api/artifacts/$(_urlenc "$art_id")")" || exit $?
    _check "$code" "versions"
    _render versions
    ;;

  rollback)
    art_id="${1:-}"; n="${2:-}"
    [[ -n "$art_id" && -n "$n" ]] || _die "usage: artifact.sh rollback <id> <n>"
    shift 2
    [[ $# -eq 0 ]] || _die "unknown option for rollback: $1"
    case "$n" in
      ''|*[!0-9]*) _die "version must be a positive integer (got: $n)" ;;
    esac
    [[ "$n" -ge 1 ]] || _die "version must be >= 1 (got: $n)"
    _check_id "$art_id" "rollback"
    _need_token
    printf '{"current": %s}' "$n" > "$REQ"
    code="$(_api PATCH "/api/artifacts/$(_urlenc "$art_id")" "$REQ")" || exit $?
    _check "$code" "rollback"
    _render patched "now serving v$n"
    ;;

  visibility)
    art_id="${1:-}"; vis="${2:-}"
    [[ -n "$art_id" && -n "$vis" ]] || _die "usage: artifact.sh visibility <id> private|tailnet"
    shift 2
    [[ $# -eq 0 ]] || _die "unknown option for visibility: $1"
    case "$vis" in
      private|tailnet) ;;
      *) _die "visibility takes 'private' or 'tailnet' (got: $vis)" ;;
    esac
    _check_id "$art_id" "visibility"
    _need_token
    printf '{"visibility": "%s"}' "$vis" > "$REQ"
    code="$(_api PATCH "/api/artifacts/$(_urlenc "$art_id")" "$REQ")" || exit $?
    _check "$code" "visibility"
    _render patched "visibility = $vis"
    ;;

  remove)
    art_id="${1:-}"
    [[ -n "$art_id" ]] || _die "usage: artifact.sh remove <id> --yes"
    shift
    yes=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --yes) yes=1; shift ;;
        *)     _die "unknown option for remove: $1" ;;
      esac
    done
    if [[ $yes -ne 1 ]]; then
      echo "Refusing: 'remove' deletes artifact '$art_id' and EVERY version of it." >&2
      echo "Its URL dies with it — anyone holding the link gets a 404 forever." >&2
      echo "" >&2
      echo "  Hide it instead:  ./scripts/artifact.sh visibility $art_id private" >&2
      echo "  Really delete it: ./scripts/artifact.sh remove $art_id --yes" >&2
      exit 2
    fi
    _check_id "$art_id" "remove"
    _need_token
    code="$(_api DELETE "/api/artifacts/$(_urlenc "$art_id")")" || exit $?
    _check "$code" "remove"
    echo "Removed artifact $art_id (all versions)."
    ;;

  -h|--help|help) _usage ;;
  *) echo "Unknown command: $cmd" >&2; echo "" >&2; _usage >&2; exit 2 ;;
esac
