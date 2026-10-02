#!/usr/bin/env bash
# Import a remote skill through the gate: pinned commit, scan, human review,
# ledger row. Nothing reaches skills/ any other way.
#
#   ./scripts/skill-import.sh install-scanner            the pinned SkillSpector, own venv
#   ./scripts/skill-import.sh scanner                    which scanner would run
#   ./scripts/skill-import.sh fetch <https-git-url> --rev <40-hex sha> --name <skill> [--path <dir>]
#                                                        stage into .run/skill-staging/ and scan
#   ./scripts/skill-import.sh scan <dir> [--accept IDS]  scan any skill directory
#   ./scripts/skill-import.sh diff <skill>               live vs staged (a refresh)
#   ./scripts/skill-import.sh promote <skill> --reviewed-by <initials> [--accept IDS]
#                                    [--read-in-full FILES] [--notes "..."]
#                                                        re-scan, copy to skills/, write the ledger row
#        (an agent signs as itself: --agent-read <agent> --ordered-by <initials>, not --reviewed-by)
#   ./scripts/skill-import.sh attest <skill> --reviewed-by <initials>
#                                                        a human signs an agent-read row after reading it
#   ./scripts/skill-import.sh verify [--quiet]           every ledger row still matches the disk
#
# Exit: 0 pass · 1 could not judge (no scanner, bad input) · 3 the gate said no.
#
# WHY. A skill is authoritative context: start_skill_agent puts its body in a
# sub-agent's system prompt, so a poisoned SKILL.md is a prompt injection the
# runner cannot defend against. The review gate in docs/TODO.md ("Selectively
# pull skills") has always required a pinned source, a probe, a content hash
# and a ledger row; this is the tool that makes those four steps one command
# each instead of four things to remember. The fifth step, reading the skill
# end to end, is not automated: `promote` refuses without a named reader, and
# the ledger says whether that reader was a human or an agent.
#
# Deliberately does NOT source lib/conf.sh: that generates a SearXNG secret and
# appends it to openbeast.conf, and `verify` runs from doctor and the test
# suite. The one setting needed (OFFLINE) is read without side effects.
#
# Design, measured scanner behaviour and what is still open:
# docs/EXTERNAL_SKILLS_PLAN.md. The ledger: skills/REMOTE_PROVENANCE.md.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${OPENBEAST_PYTHON:-python3}"

if [[ $# -eq 0 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,/^# Exit:/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
fi

exec "$PY" "$REPO_DIR/scripts/lib/skill_import.py" "$@"
