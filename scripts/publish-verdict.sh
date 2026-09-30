#!/usr/bin/env bash
# Publish a campaign verdict or leaderboard as a beast-artifact page, at a URL
# that stays the same every time that verdict is republished.
#
#   ./scripts/publish-verdict.sh <slug> <file.html|file.txt> [--label "L"] [--title "T"]
#
#   <slug>   names the verdict, e.g. tier3-zig or leaderboard. The artifact id
#            is uuid5(NAMESPACE_URL, "openbeast:verdict:<slug>"), so each
#            republish adds a VERSION to the same page instead of a new URL.
#   <file>   an .html/.htm page as-is. Anything else (a .txt verdict, a log
#            excerpt) is wrapped in a minimal page, HTML-escaped inside <pre>.
#   --label  the version label. Default: "<git short sha> era=<eval era>", so
#            a verdict names the code and the eval era it was measured under.
#   --title  the page title (default: the file's own <title>, or "Verdict: <slug>").
#
# PRIVATE on first publish, owned by the rig (its admins can open it). Share
# one with ./scripts/artifact.sh visibility <id> tailnet; a share survives
# every later republish (this script never asks for a visibility, so the
# server keeps the page's own and prints no "visibility unchanged" warning).
#
# NEVER FAILS THE CALLER. This runs at the end of hours-long campaign stages
# under `set -e`: a page viewer that is off or down must cost one stderr line,
# never the stage. Exit status is 0 on every path, published or not.
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
  -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac
[[ -n "$slug" && -n "$src" ]] || _skip "usage: publish-verdict.sh <slug> <file.html|file.txt> [--label L] [--title T]"
shift 2
# A slug is a name, not a sentence: it becomes part of the uuid5 name, the
# default title, and the log line — keep it to a filename-safe alphabet.
[[ "$slug" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ ]] \
  || _skip "slug '$slug' is not [A-Za-z0-9._-] (100 chars max)"
label=""; title=""
while [[ $# -gt 0 ]]; do
  case "$1" in
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

art_id="$(python3 -c 'import sys, uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, "openbeast:verdict:" + sys.argv[1]))' "$slug" 2>/dev/null)" \
  || _skip "$slug: python3 could not derive the artifact id"

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
  *) _skip "$slug: artifact.sh exit $rc: $(printf '%s' "$out" | grep -m1 -E 'ERROR' || printf '%s' "$out" | head -n1)" ;;
esac
exit 0
