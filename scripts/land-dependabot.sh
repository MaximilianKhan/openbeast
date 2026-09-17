#!/bin/bash
# Land Dependabot's /agents PRs, one at a time, end to end.
#
#   ./scripts/land-dependabot.sh            # every open Dependabot PR
#   ./scripts/land-dependabot.sh 86 88      # just these
#
# WHY A SCRIPT. agents/requirements.lock is a hash-pinned closure generated
# FROM requirements.txt, and Dependabot only edits the latter. The chain that
# actually lands one of its PRs is four steps, each of which waits on GitHub:
#
#   1. `@dependabot rebase`     — main moved (the previous PR just merged), and
#                                 branch protection requires an up-to-date branch
#   2. dependabot-relock.yml    — regenerates the lock and pushes it to the PR
#   3. APPROVE the held runs    — a GITHUB_TOKEN push creates the CI runs in
#                                 "action_required"; nothing runs until a
#                                 maintainer approves (measured 2026-09-17:
#                                 workflow_dispatch runs do NOT satisfy the PR's
#                                 required checks, approval does)
#   4. wait for CI, squash-merge
#
# Sequential ON PURPOSE: every one of these PRs touches the same two files, so
# each merge invalidates the next PR's lock and it has to go round again.
#
# Needs: gh (authenticated as a maintainer). Touches nothing local — it does
# NOT pip-install the new versions; ./bootstrap.sh (or update.sh --python)
# does that, on your schedule. Do not run that mid-campaign: openai is the
# eval client.
set -uo pipefail
# ONE AT A TIME. Two copies of this script (it happened on its first day: a
# second was started while the first was still inside a wait) both ask for a
# rebase and both approve runs, and the overlapping pushes CANCEL each other's
# CI — the PR then shows red checks that never actually ran.
if command -v flock >/dev/null 2>&1; then
  exec 8>"${TMPDIR:-/tmp}/openbeast-land-dependabot.lock"
  flock -n 8 || { echo "another land-dependabot.sh is already running" >&2; exit 1; }
fi
R="$(gh repo view --json nameWithOwner -q .nameWithOwner)" || { echo "gh is not authenticated" >&2; exit 1; }
if [[ $# -eq 0 ]]; then
  # shellcheck disable=SC2046
  set -- $(gh pr list --author "app/dependabot" --state open --json number -q '.[].number' | sort -n)
  [[ $# -gt 0 ]] || { echo "No open Dependabot PRs."; exit 0; }
fi
for pr in "$@"; do
  echo "=== PR $pr $(date +%T)"
  br="$(gh pr view "$pr" --json headRefName -q .headRefName)"
  gh pr comment "$pr" --body "@dependabot rebase" >/dev/null
  ok=0
  for i in $(seq 1 60); do            # up to 20 min for rebase + relock push
    sleep 20
    main="$(gh api repos/$R/commits/main -q .sha)"
    last="$(gh pr view "$pr" --json commits -q '.commits[-1].messageHeadline')"
    base_ok="$(gh api "repos/$R/compare/$main...$br" -q .behind_by 2>/dev/null || echo 1)"
    if [[ "$base_ok" == "0" && "$last" == deps:\ regenerate* ]]; then ok=1; break; fi
    # a bump whose lock needed no change never gets a relock commit: accept after the relock run succeeded
    if [[ "$base_ok" == "0" ]] && gh run list --workflow dependabot-relock.yml --branch "$br" --limit 1 --json conclusion,headSha -q '.[0].conclusion' | grep -qx success \
       && [[ "$(gh run list --workflow dependabot-relock.yml --branch "$br" --limit 1 --json headSha -q '.[0].headSha')" == "$(gh pr view "$pr" --json headRefOid -q .headRefOid)" ]]; then ok=1; break; fi
  done
  [[ $ok -eq 1 ]] || { echo "PR $pr: rebase/relock did not land in time — stopping"; exit 1; }
  sleep 15
  for id in $(gh run list --branch "$br" --status action_required --json databaseId,workflowName -q '.[] | select(.workflowName!="Dependabot relock") | .databaseId'); do
    gh api -X POST "repos/$R/actions/runs/$id/approve" >/dev/null && echo "approved run $id"
  done
  sleep 20
  gh pr checks "$pr" --watch --interval 30 | tail -4
  # A CANCELLED run is not a failed check (a late Dependabot force-push does
  # it): re-run those once before giving up.
  head="$(gh pr view "$pr" --json headRefOid -q .headRefOid)"
  redo="$(gh run list --branch "$br" --limit 12 --json databaseId,headSha,conclusion,workflowName \
           -q ".[] | select(.headSha==\"$head\" and .conclusion==\"cancelled\" and .workflowName!=\"Dependabot relock\") | .databaseId")"
  if [[ -n "$redo" ]]; then
    for id in $redo; do gh run rerun "$id" >/dev/null 2>&1 && echo "re-ran cancelled run $id"; done
    sleep 30
    gh pr checks "$pr" --watch --interval 30 | tail -4
  fi
  gh pr merge "$pr" --squash --delete-branch 2>&1 | tail -1
  echo "PR $pr -> $(gh pr view "$pr" --json state -q .state)"
  [[ "$(gh pr view "$pr" --json state -q .state)" == MERGED ]] || exit 1
done
echo "ALL DONE $(date +%T)"
