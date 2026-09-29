#!/bin/bash
# OpenBeast — curl with a credential header that never touches argv.
#
# `curl -H "Authorization: Bearer $KEY"` puts the key in curl's
# /proc/<pid>/cmdline, which every local uid can read (`ps -eo args`) unless
# /proc is mounted hidepid — and it is not, by default. artifact.sh and
# doctor.sh's chat/artifact probes already moved their tokens into a 0600
# `--config` file for exactly this reason; this is the same idiom as one
# shared helper, so no caller has to re-derive it.
#
# The header travels as a curl config line on file descriptor 3, fed from a
# here-document: the shell writes it itself, so the secret is never an
# argument of ANY process (the escaping `sed` below gets it on stdin), and
# there is nothing to clean up — bash backs a heredoc with a pipe, or with an
# already-unlinked 0600 temp file on older versions (macOS's 3.2).
# /dev/fd/N exists on Linux and macOS alike.
#
#   ob_curl_hdr    <header-line> [curl args…]   e.g. "X-OpenBeast-Local: $tok"
#   ob_curl_bearer <key>         [curl args…]   "Authorization: Bearer <key>"
#
# An EMPTY header / key runs plain `curl [curl args…]` — the unkeyed install.
# The caller's own stdin is left alone, so `-d @-` still works.
#
# Sourced by the rig (start.sh, doctor.sh, healthcheck.sh…) AND shipped to
# client machines (client.sh, setup-client.sh), so it is bash 3.2-compatible
# on purpose: no bash-4 builtins, no associative arrays. (`$(printf '\n')`
# would NOT work for the line-break check: command substitution strips the
# trailing newline, the pattern becomes ** and matches every header.)

# ob_curl_hdr <header-line> [curl args…]
ob_curl_hdr() {
  local _ob_h="$1" _ob_nl='
'
  shift
  if [ -z "$_ob_h" ]; then
    curl "$@"
    return
  fi
  # A CR/LF would end the config line and let the rest be read as further
  # curl options — refuse rather than inject.
  case "$_ob_h" in
    *"$_ob_nl"*|*"$(printf '\r')"*)
      echo "ob_curl_hdr: header contains a line break — refusing" >&2
      return 2 ;;
  esac
  # Curl config quoting: inside "…", backslash and double quote are escaped.
  _ob_h="$(printf '%s' "$_ob_h" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
  curl --config /dev/fd/3 "$@" 3<<EOF
header = "$_ob_h"
EOF
}

# ob_curl_bearer <key> [curl args…]
ob_curl_bearer() {
  local _ob_k="$1"
  shift
  if [ -z "$_ob_k" ]; then
    curl "$@"
  else
    ob_curl_hdr "Authorization: Bearer $_ob_k" "$@"
  fi
}
