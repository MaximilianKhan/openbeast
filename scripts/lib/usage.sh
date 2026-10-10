#!/usr/bin/env bash
# OpenBeast — shared --help printer.
#
#   source "$SCRIPT_DIR/lib/usage.sh"
#   ob_usage "$0"
#
# Prints a script's header comment block: everything from line 2 (the line
# after the shebang) up to the first line that is not a comment, with the
# leading "# " removed. The header IS the help text, so the two cannot drift,
# and nothing depends on line numbers — the `sed -n 'A,Bp' "$0"` idiom this
# replaces leaked code lines or stopped mid-sentence whenever a header grew
# or shrank (2026-10-09 review, UX-21).
#
# No side effects when sourced; bash 3.2 and BSD awk safe (it ships to
# client Macs with the rest of scripts/lib).

ob_usage() { # ob_usage <script path>
  awk 'NR > 1 && !/^#/ {exit} NR > 1 {sub(/^# ?/, ""); print}' "$1"
}
