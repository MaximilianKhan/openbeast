#!/usr/bin/env bash
# beast-lang phase 3 — a model DRAFTS candidate claims; the toolchain decides.
#
#   ./scripts/lang-synthesize.sh draft <lang> --dry-run     the prompts it WOULD send
#   ./scripts/lang-synthesize.sh draft <lang> [--match RE] [--source FILE]...
#   ./scripts/lang-synthesize.sh status                     what awaits review
#   ./scripts/lang-synthesize.sh promote <staging.json> --all | --id ID...
#
# The ordering is the feature (docs/BEAST_LANG_PLAN.md §3, §7 P3): corpus ->
# prompt -> model -> strict parse -> the REAL verifier -> claims/staging/.
# Nothing a model wrote reaches a pack until a person has reviewed the staging
# file and run `promote`, which re-verifies and refuses the whole batch if any
# claim no longer passes.
#
# THIS IS A GPU JOB AND A CPU ONE. The model drafts on the card and every
# candidate is compiled. `draft` therefore asks scripts/gpu-lease.sh first and
# refuses while somebody else holds the lease (--ignore-lease overrides). Take
# the lease for the run, from the MAIN tree (the lease file lives in its .run/):
#
#   OPENBEAST_LANG_SYNTH_URL=http://<host>:<port>/v1 \
#     ./scripts/gpu-lease.sh run "beast-lang P3 python" -- \
#     ./scripts/lang-synthesize.sh draft python --match 'whatsnew/3\.1[2-4]'
#
# There is NO default endpoint, on purpose: an unset OPENBEAST_LANG_SYNTH_URL
# is a refusal, never a fallback to whatever this rig happens to be serving.
# All logic (and the reasons for it) lives in agents/lang/synthesize.py.
set -euo pipefail

# No `cd`: --source and the staging file are paths the OPERATOR typed,
# relative to where they are standing.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -eq 0 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
fi

exec python3 "$REPO_DIR/agents/lang/synthesize.py" "$@"
