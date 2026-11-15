#!/usr/bin/env bash
# Publish a campaign verdict or leaderboard as a beast-artifact page, at a URL
# that stays the same every time that verdict is republished.
#
#   ./scripts/publish-verdict.sh <slug> <file.html|file.txt> [--label "L"] [--title "T"] [--strict]
#
#   <slug>   names the verdict, e.g. tier3-zig or leaderboard. The artifact id
#            is HMAC-SHA256(.run/verdict-id.key, "openbeast:verdict:<slug>")
#            shaped as a uuid, so each republish adds a VERSION to the same
#            page instead of a new URL — and nobody without the rig's 0600
#            key can compute the id and claim it before the rig's first
#            publish (it was a public uuid5 a WebUI user could squat).
#   <file>   an .html/.htm page as-is. Anything else (a .txt verdict, a log
#            excerpt) is wrapped in a minimal page, HTML-escaped inside <pre>.
#   --label  the version label. Default: "<git short sha> era=<eval era>", so
#            a verdict names the code and the eval era it was measured under.
#   --title  the page title (default: the file's own <title>, or "Verdict: <slug>").
#   --strict exit 3 (not 0) when the server REFUSES the publish — e.g. the
#            page at this id is owned by another login. Opt-in, so set -e
#            campaign callers keep the never-fail contract below.
#
# PRIVATE on first publish, owned by the rig (its admins can open it). Share
# one with ./scripts/artifact.sh visibility <id> tailnet; a share survives
# every later republish (this script never asks for a visibility, so the
# server keeps the page's own and prints no "visibility unchanged" warning).
#
# NEVER FAILS THE CALLER. This runs at the end of hours-long campaign stages
# under `set -e`: a page viewer that is off or down must cost one stderr line,
# never the stage. Exit status is 0 on every path, published or not (only an
# explicit --strict turns a server REFUSAL into exit 3).
# (Usage mistakes are reported the same way — one line, exit 0.)
#
# Hooking it in (a campaign's verdict step, after the verdict file exists):
#   ./scripts/publish-verdict.sh tier3-zig "$OUT/verdict.txt"
#   python3 evals/scoring.py --html "$OUT/board.html" \
#     && ./scripts/publish-verdict.sh leaderboard "$OUT/board.html"
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

_skip() { echo "publish-verdict: $* — not published" >&2; exit 0; }

slug="${1:-}"; src="${2:-}"
case "$slug" in
  -h|--help) sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
[[ -n "$slug" && -n "$src" ]] || _skip "usage: publish-verdict.sh <slug> <file.html|file.txt> [--label L] [--title T] [--strict]"
shift 2
# A slug is a name, not a sentence: it becomes the HMAC input of the id, the
# default title, and the log line — keep it to a filename-safe alphabet.
[[ "$slug" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ ]] \
  || _skip "slug '$slug' is not [A-Za-z0-9._-] (100 chars max)"
label=""; title=""; strict=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict)  strict=1; shift ;;
    --label)   [[ $# -ge 2 ]] || _skip "--label needs a value"; label="$2"; shift 2 ;;
    --label=*) label="${1#*=}"; shift ;;
    --title)   [[ $# -ge 2 ]] || _skip "--title needs a value"; title="$2"; shift 2 ;;
    --title=*) title="${1#*=}"; shift ;;
    *)         _skip "unknown option: $1" ;;
  esac
done
[[ -f "$src" && -r "$src" && -s "$src" ]] || _skip "$slug: no readable, non-empty file at $src"

# BEAST_ARTIFACT, env over conf, first token, the booleans lib/conf.sh
# accepts. Read directly: sourcing conf.sh has a side effect (it can write a
# generated secret into openbeast.conf) that a publish hook must not have.
_enabled="${OPENBEAST_BEAST_ARTIFACT:-}"
if [[ -z "$_enabled" && -f "$REPO_DIR/openbeast.conf" ]]; then
  _enabled="$(grep -E '^[[:space:]]*BEAST_ARTIFACT[[:space:]]*=' "$REPO_DIR/openbeast.conf" 2>/dev/null \
               | tail -n1 | cut -d= -f2-)"
fi
_enabled="${_enabled%%#*}"; _enabled="${_enabled//[\"\'[:space:]]/}"
case "$(printf '%s' "$_enabled" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on) ;;
  *) _skip "$slug: beast-artifact is off (BEAST_ARTIFACT=true in openbeast.conf turns it on)" ;;
esac

# The id: an HMAC under a rig-local 0600 key, created once (O_EXCL + link, so
# two concurrent first publishes agree on one key). A public uuid5 of the slug
# let any non-guest WebUI user — or a model steered in their chat — create
# the page at the canonical id before the rig did, after which the rig's
# publish was refused (review finding campaign-evals: verdict id squatting).
# Same trust model as .run/artifact-local.token: can read it == is the rig.
# No key and none creatable → do not publish at a guessable id; skip.
art_id="$(OB_KEY="$REPO_DIR/.run/verdict-id.key" python3 - "$slug" <<'PY' 2>/dev/null
import hashlib, hmac, os, secrets, sys, uuid
path = os.environ["OB_KEY"]
if not os.path.exists(path):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_hex(32) + "\n")
    try:
        os.link(tmp, path)          # fails if another publish won the race
    except FileExistsError:
        pass
    finally:
        os.unlink(tmp)
key = open(path).read().strip()
if len(key) < 32:
    sys.exit(1)
mac = hmac.new(key.encode(), ("openbeast:verdict:" + sys.argv[1]).encode(),
               hashlib.sha256).digest()
print(uuid.UUID(bytes=mac[:16], version=5))
PY
)" && [[ -n "$art_id" ]] \
  || _skip "$slug: could not read or create the verdict id key at .run/verdict-id.key"

if [[ -z "$label" ]]; then
  _sha="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo nogit)"
  _era="$("$SCRIPT_DIR/eval-era.sh" 2>/dev/null | head -n1)"
  label="$_sha era=${_era:-?}"
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-verdict.XXXXXX")" || _skip "$slug: no temp dir"
trap 'rm -rf "$TMP"' EXIT
page="$src"
case "$(printf '%s' "$src" | tr '[:upper:]' '[:lower:]')" in
  *.html|*.htm) ;;
  *)
    page="$TMP/verdict.html"
    OB_SRC="$src" OB_TITLE="${title:-Verdict: $slug}" OB_OUT="$page" python3 - <<'PY' \
      || _skip "$slug: could not wrap $src as a page"
import html, os
raw = open(os.environ["OB_SRC"], "rb").read().decode("utf-8", "replace")
t = html.escape(os.environ["OB_TITLE"])
doc = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
       "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
       "<title>%s</title>\n<style>\n"
       ":root{color-scheme:light dark;--bg:#fff;--fg:#1b1b1b}\n"
       "@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#e6e6e6}}\n"
       "body{margin:0;padding:16px;background:var(--bg);color:var(--fg);"
       "font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}\n"
       "h1{font-size:16px;margin:0 0 12px}\n"
       "pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}\n"
       "</style></head><body>\n<h1>%s</h1>\n<pre>%s</pre>\n</body></html>\n"
       % (t, t, html.escape(raw)))
open(os.environ["OB_OUT"], "w", encoding="utf-8").write(doc)
PY
    ;;
esac

args=(publish "$page" --id "$art_id" --label "$label")
[[ -n "$title" ]] && args+=(--title "$title")
out="$("$SCRIPT_DIR/artifact.sh" "${args[@]}" 2>&1)"
rc=$?
case "$rc" in
  0) printf '%s\n' "$out" ;;
  4) _skip "$slug: the artifact server is not answering (./start.sh --status)" ;;
  3)
    # The server said NO. On a verdict id that is (almost always) "this page
    # is owned by another login": say so loudly, name the id, and let a
    # --strict caller fail on it. Not one quiet line any more.
    echo "WARNING: publish-verdict: the server REFUSED the rig's publish of '$slug' to $art_id." >&2
    echo "         $(printf '%s' "$out" | grep -m1 -E 'ERROR' || printf '%s' "$out" | head -n1)" >&2
    echo "         If that page is owned by another login, it is not this rig's verdict:" >&2
    echo "         inspect it (./scripts/artifact.sh show $art_id) before trusting its URL." >&2
    [[ $strict -eq 1 ]] && exit 3
    exit 0 ;;
  *) _skip "$slug: artifact.sh exit $rc: $(printf '%s' "$out" | grep -m1 -E 'ERROR' || printf '%s' "$out" | head -n1)" ;;
esac
exit 0
