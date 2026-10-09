#!/bin/bash
# Land Dependabot's /agents PRs, one at a time, end to end.
#
#   ./scripts/land-dependabot.sh            # every open Dependabot PR that
#                                           # touches agents/requirements.txt
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
#
# The lock lives in the checkout's own .run/ (0700, ours), opened for APPEND:
# it used to be `exec 8>/tmp/openbeast-land-dependabot.lock`, a fixed name in
# a world-writable directory opened with truncation — where
# fs.protected_symlinks is off, a symlink planted there gets its target
# emptied by whoever runs this.
if command -v flock >/dev/null 2>&1; then
  _run_dir="$(cd "$(dirname "$0")/.." && pwd)/.run"
  [[ -d "$_run_dir" ]] || (umask 077; mkdir -p "$_run_dir")
  exec 8>>"$_run_dir/land-dependabot.lock"
  flock -n 8 || { echo "another land-dependabot.sh is already running" >&2; exit 1; }
fi
R="$(gh repo view --json nameWithOwner -q .nameWithOwner)" || { echo "gh is not authenticated" >&2; exit 1; }
if [[ $# -eq 0 ]]; then
  # ONLY the PRs this chain can land: the ones that touch
  # agents/requirements.txt. A github-actions (or docker) bump never triggers
  # dependabot-relock.yml — it is path-filtered — so the wait below would
  # stall its full 20 minutes, exit 1, and block every pip PR queued behind it.
  _all="$(gh pr list --author "app/dependabot" --state open --json number -q '.[].number' | wc -l)"
  # shellcheck disable=SC2046
  set -- $(gh pr list --author "app/dependabot" --state open --json number,files \
             -q '.[] | select(any(.files[]?; .path == "agents/requirements.txt")) | .number' | sort -n)
  if [[ "$_all" -gt $# ]]; then
    echo "Skipping $((_all - $#)) Dependabot PR(s) that do not touch agents/requirements.txt"
    echo "  (no lock to regenerate — review and merge those by hand, or name them: $0 <PR>)."
  fi
  [[ $# -gt 0 ]] || { echo "No open Dependabot PRs that touch agents/requirements.txt."; exit 0; }
fi

# Watch a PR's checks to the end — tolerating a PR whose checks have not
# APPEARED yet. Right after a rebase/relock push (or an approval) GitHub has
# not created the check runs: `gh pr checks --watch` then prints "no checks
# reported" and returns at once, and the script went on to a merge that could
# only fail (2026-10-09: it gave up on the third PR of a batch while the
# relock workflow was still pending). Wait for them, bounded (10 min).
_watch_checks() { # _watch_checks <pr>
  local out n
  for n in $(seq 1 30); do
    out="$(gh pr checks "$1" --watch --interval 30 2>&1)"
    if [[ "$out" != *"no checks reported"* ]]; then
      printf '%s\n' "$out" | tail -4
      return 0
    fi
    [[ $n -eq 1 ]] && echo "PR $1: no checks reported yet — waiting for them to appear"
    sleep 20
  done
  echo "PR $1: no checks appeared within 10 min"
  return 1
}
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
  # APPROVE ONLY THIS REPO'S HELD RUNS FOR THIS PR'S HEAD COMMIT. This used
  # to be `gh run list --branch "$br" --status action_required`, and a branch
  # filter matches head_branch BY NAME: a fork PR from a branch named like
  # Dependabot's (the names are predictable) produces held runs that matched
  # too, and this maintainer-token loop approved them — bypassing GitHub's
  # "approve workflows from outside contributors" gate. The Actions API gives
  # each run's head_sha and head_repository; both must be ours.
  _head="$(gh pr view "$pr" --json headRefOid -q .headRefOid)"
  [[ "$_head" =~ ^[0-9a-f]{40}$ ]] || { echo "PR $pr: could not read its head commit — stopping"; exit 1; }
  for id in $(gh api "repos/$R/actions/runs?head_sha=$_head&status=action_required&per_page=100" \
      -q ".workflow_runs[] | select(.head_sha == \"$_head\" and .head_repository.full_name == \"$R\" and .event == \"pull_request\" and .name != \"Dependabot relock\") | .id"); do
    gh api -X POST "repos/$R/actions/runs/$id/approve" >/dev/null && echo "approved run $id"
  done
  sleep 20
  _watch_checks "$pr" || { echo "PR $pr: nothing to wait for — stopping"; exit 1; }
  # A CANCELLED run is not a failed check (a late Dependabot force-push does
  # it): re-run those once before giving up.
  head="$(gh pr view "$pr" --json headRefOid -q .headRefOid)"
  [[ "$head" =~ ^[0-9a-f]{40}$ ]] || { echo "PR $pr: could not read its head commit — stopping"; exit 1; }
  redo="$(gh run list --branch "$br" --limit 12 --json databaseId,headSha,conclusion,workflowName \
           -q ".[] | select(.headSha==\"$head\" and .conclusion==\"cancelled\" and .workflowName!=\"Dependabot relock\") | .databaseId")"
  if [[ -n "$redo" ]]; then
    for id in $redo; do gh run rerun "$id" >/dev/null 2>&1 && echo "re-ran cancelled run $id"; done
    sleep 30
    _watch_checks "$pr" || { echo "PR $pr: nothing to wait for — stopping"; exit 1; }
  elif [[ "$head" != "$_head" ]]; then
    # The head moved while we were waiting: the checks just watched belong to
    # a commit that is no longer the PR. Watch the one about to be merged.
    echo "PR $pr: head moved during the wait (${_head:0:12} -> ${head:0:12}) — watching the new commit"
    _watch_checks "$pr" || { echo "PR $pr: nothing to wait for — stopping"; exit 1; }
  fi
  # --match-head-commit: merge exactly the commit read above, whose checks
  # were watched after it was read. A push that lands between "checks green"
  # and this line makes GitHub REFUSE the merge instead of landing a commit
  # this script never looked at.
  gh pr merge "$pr" --squash --delete-branch --match-head-commit "$head" 2>&1 | tail -1
  echo "PR $pr -> $(gh pr view "$pr" --json state -q .state)"
  [[ "$(gh pr view "$pr" --json state -q .state)" == MERGED ]] || exit 1
done
echo "ALL DONE $(date +%T)"
