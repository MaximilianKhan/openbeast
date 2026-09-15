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
# Writes carry the proof-of-locality token from .run/artifact-local.token —
# the file is 0600, so "can read it" IS "is on this box" (the transport peer
# proves nothing: tailscale serve reverse-proxies from 127.0.0.1).
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

TMPDIR_RUN="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-artifact.XXXXXX")"
trap 'rm -rf "$TMPDIR_RUN"' EXIT
BODY="$TMPDIR_RUN/body"
REQ="$TMPDIR_RUN/req.json"

_no_server() {
  echo "ERROR: beast-artifact is not answering on $BASE." >&2
  echo "" >&2
  echo "  Is it enabled?   grep BEAST_ARTIFACT $REPO_DIR/openbeast.conf" >&2
  echo "  Turn it on:      set BEAST_ARTIFACT=true in openbeast.conf, then" >&2
  echo "                   ./stop.sh && ./start.sh" >&2
  echo "  Already on?      ./start.sh --status   and   ./scripts/doctor.sh" >&2
  exit 4
}

_need_token() {
  if [[ -n "$TOKEN" ]]; then return 0; fi
  echo "ERROR: no locality token at $TOKEN_FILE." >&2
  echo "       Publishing, rollback, visibility and remove are loopback-only" >&2
  echo "       and prove locality with that 0600 file — the server writes it" >&2
  echo "       at startup. Start the stack (./start.sh) and retry." >&2
  exit 4
}

# _api <METHOD> <path> [request-body-file] — response lands in $BODY, the HTTP
# status is echoed. A refused connection is a missing server, not a 000 to
# parse downstream.
_api() {
  local method="$1" path="$2" reqfile="${3:-}" code
  local args
  args=(-s -S -m 60 -o "$BODY" -w '%{http_code}' -X "$method")
  if [[ -n "$TOKEN" ]]; then args+=(-H "X-OpenBeast-Local: $TOKEN"); fi
  if [[ -n "$reqfile" ]]; then
    args+=(-H 'Content-Type: application/json' --data-binary "@$reqfile")
  fi
  if ! code="$(curl "${args[@]}" "$BASE$path" 2>/dev/null)"; then
    _no_server
  fi
  [[ -n "$code" && "$code" != "000" ]] || _no_server
  printf '%s' "$code"
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
    file_pairs=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --title)        [[ $# -ge 2 ]] || _die "--title needs a value"; title="$2"; shift 2 ;;
        --title=*)      title="${1#*=}"; shift ;;
        --description)  [[ $# -ge 2 ]] || _die "--description needs a value"; description="$2"; shift 2 ;;
        --description=*) description="${1#*=}"; shift ;;
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
    for _pair in ${file_pairs[@]+"${file_pairs[@]}"}; do
      case "$_pair" in
        *=*) ;;
        *)   _die "--file takes published=source (got: $_pair)" ;;
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
    published = published.strip().lstrip("/")
    if not published:
        sys.stderr.write("ERROR: --file %r has an empty published path\n" % pair)
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
    code="$(_api GET "/api/artifacts/$art_id")" || exit $?
    _check "$code" "show"
    if [[ $json -eq 1 ]]; then _render json; else _render show; fi
    ;;

  versions)
    art_id="${1:-}"
    [[ -n "$art_id" ]] || _die "usage: artifact.sh versions <id>"
    shift
    [[ $# -eq 0 ]] || _die "unknown option for versions: $1"
    code="$(_api GET "/api/artifacts/$art_id")" || exit $?
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
    _need_token
    printf '{"current": %s}' "$n" > "$REQ"
    code="$(_api PATCH "/api/artifacts/$art_id" "$REQ")" || exit $?
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
    _need_token
    printf '{"visibility": "%s"}' "$vis" > "$REQ"
    code="$(_api PATCH "/api/artifacts/$art_id" "$REQ")" || exit $?
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
    _need_token
    code="$(_api DELETE "/api/artifacts/$art_id")" || exit $?
    _check "$code" "remove"
    echo "Removed artifact $art_id (all versions)."
    ;;

  -h|--help|help) _usage ;;
  *) echo "Unknown command: $cmd" >&2; echo "" >&2; _usage >&2; exit 2 ;;
esac
