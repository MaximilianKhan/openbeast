---
name: git-discipline
description: Clean commits, atomic units, meaningful messages, no random staging. Activate any time the user is about to commit, push, or open a PR — local models often stage too aggressively or write messages that don't explain why. Frontier models do this naturally.
allowed_tools: [bash, read_file, grep]
recommends_subagent: false
---

# Git discipline

Every commit is documentation. Future you (or a teammate) will run `git blame`
on this code and need to know **why** you made the change. The commit message
is your one chance to explain it.

This skill enforces three things:

1. **Atomicity** — one commit, one logical change
2. **Hygiene** — review before staging, never blanket-stage
3. **Messages that explain why** — not just what

## Pre-commit checklist

Before staging anything, run:

```bash
git status                 # what's changed
git diff                   # what's the actual content of those changes
git diff --stat            # summary of files + line counts
```

Read the diff. Ask:
- **Is this all one logical change?** If you see two unrelated edits in
  different files, they should be two commits. Stage them separately.
- **Are there debugging artifacts?** `print(...)`, `console.log`, `// XXX`,
  commented-out blocks, scratch files. Either keep intentionally with a
  comment explaining why, or remove.
- **Are there secrets?** Search the diff for `key`, `token`, `password`,
  `secret`, `BEGIN PRIVATE`. Even one accidental commit is forever in git
  history.
- **Are there unintended files?** `.DS_Store`, `node_modules/`, `__pycache__/`,
  IDE files. They should be in `.gitignore` — if they're showing up, fix the
  ignore file.

## Staging discipline

**Default: never use `git add .` or `git add -A` blindly.** Both stage
everything, including stuff you didn't mean to stage.

**Preferred: `git add -p`** (interactive patch mode) lets you stage hunks
selectively. Use this whenever your diff has multiple logical pieces.

**When to stage by name:** `git add path/to/file.py` when you know exactly
what you want and the diff is clean.

**Acceptable shortcuts:** `git add -u` (stage modifications to tracked files
only — won't pull in random untracked junk). Use this when you've reviewed
the modifications and just want to skip retyping paths.

## The commit message

Format:

```
short summary in imperative mood, ≤72 chars

Optional longer body. Explain WHY this change is needed. Link to the
issue / ticket / discussion. Mention what alternatives you considered
and why you rejected them. If the change has surprising implications
for performance, security, or behavior — call them out here.

Refs: #123
```

### The summary line

- **Imperative mood**: "Add foo", "Fix bar", "Remove baz" — not "Added foo"
  or "Adds foo." Reads as if completing the sentence "If applied, this
  commit will ___."
- **≤72 characters**: GitHub truncates at 72 in many views. Discipline
  forces brevity.
- **Specific**: "Fix bug" is useless. "Fix off-by-one in pagination cursor"
  is searchable.

### The body (when warranted)

Skip for trivial changes (typo fix, obvious rename). Required for:
- Non-obvious changes (refactoring, performance, behavior changes)
- Anything that took non-trivial debugging to get right
- Anything reviewers will need context to evaluate

Body explains:
- **Why now?** What problem does this solve?
- **Why this approach?** What did you consider and reject?
- **What does it change beyond the obvious?** Side effects, perf
  implications, deprecations.
- **What does it NOT change?** Common reviewer concerns to head off.

### Examples

#### Bad

```
fix
```

```
update files
```

```
WIP — refactoring the auth module to use the new session model and also fix the bug in the password reset flow
```

#### Good

```
fix(auth): off-by-one in session expiry causing premature logouts

Sessions were expiring 1 second before their declared TTL because we were
using `<` instead of `<=` in the check. This manifested as users being
logged out exactly at the boundary, especially visible in tests.

Confirmed via test_session_expiry_boundary which now fails on main and
passes on this branch.

Refs: #4521
```

```
refactor(deploy): split rollback path into restore + verify

The previous rollback path did three things in one function (restore
state, run health check, exit). Splitting them lets the new
rolling-deploy code reuse the restore logic without inheriting the
exit behavior. No semantic change to the standalone deploy script.

Tested: existing deploy.sh integration tests pass; new
test_restore_without_verify covers the new code path.
```

## Atomic commits

One commit, one logical change. If you can't summarize the commit in one
sentence, it's probably more than one commit.

**Common multi-commit refactors:**

1. Mechanical rename (`s/oldName/newName/g`) — separate from semantic changes
2. Add the new helper, then convert callers (commit 1: add; commit 2: convert)
3. Move file, then change contents (commit 1: pure move; commit 2: edit)

This makes review and bisect trivial. A commit that mixes rename + behavior
change is a nightmare to review.

## What NOT to do

### Never amend / force-push commits that are already pushed and visible

If you've pushed to a shared branch (anything other than your personal
feature branch that nobody else uses), `--amend` and `--force-push` rewrite
history that other people have based work on. Their next pull breaks.

OK to amend: your local commits, before pushing.
OK to force-push: your personal feature branch (use `--force-with-lease`
which fails safely if someone else pushed).
Not OK: anything on `main`, `develop`, shared release branches.

### Never `git reset --hard` without checking

`reset --hard` discards uncommitted work permanently. Before running it,
double-check `git status` — anything modified that you wanted to keep?
If yes, `git stash` first.

### Never skip hooks (`--no-verify`) without a reason

Pre-commit hooks exist to catch real problems (lint, test, security scans).
If a hook is failing, fix the underlying issue. `--no-verify` is for
emergencies only, and even then with a follow-up commit that fixes
whatever you bypassed.

### Don't commit generated files

`node_modules/`, `__pycache__/`, build artifacts (`*.o`, `dist/`, `target/`),
IDE config (`.vscode/settings.json` if not project-shared) — none of these
belong in source. Add to `.gitignore`. If they slipped in, remove them with
`git rm --cached <path>` and commit the removal.

### Don't commit secrets — ever

If you find yourself typing an API key or password into a file you'll
commit: stop. Use environment variables, a secrets manager, or a `.env`
file in `.gitignore`. If you've already committed a secret, rotate it
immediately — git history is forever, even after deletion.

## Working with branches

### Naming

Pick one convention and stick with it:
- `<type>/<short-description>` — `feat/login-rate-limit`, `fix/session-expiry`
- `<username>/<branch>` — `max/session-expiry`
- `<ticket>/<short-description>` — `JIRA-1234/session-expiry`

Match the project's existing convention (look at recent branches: `git
branch -a | head -30`).

### When to branch off main vs develop vs another feature branch

- New feature / fix → branch off `main` (or `develop` if the project uses
  git-flow).
- Building on someone else's WIP → branch off their branch, but be aware
  you'll need to rebase if they force-push.
- Hotfix → branch off the production tag, not `main`.

### Keeping the branch up to date

```bash
git fetch origin
git rebase origin/main           # preferred — linear history
# or
git merge origin/main             # if the project uses merge commits
```

Rebase early and often. A 5-conflict rebase across one day's drift is
trivial; a 50-conflict rebase across two months is a nightmare.

## Pull requests

The PR description is your second chance at a commit message — but for the
whole change. Structure:

```markdown
## Summary
1-3 bullets, what this does at the highest level.

## Why
Link to issue / discussion. What problem does this solve? Why now?

## What changed
The non-obvious changes. Don't restate what's in the diff.

## Test plan
- [ ] Unit tests added for X
- [ ] Manually verified Y in dev
- [ ] Stress-tested Z under load

## Risks / known limitations
What could go wrong. What this PR doesn't address.

## Out of scope
What you considered but explicitly didn't change.
```

The reviewer should be able to skim the description and know whether to
approve, request changes, or ask questions — before reading a single line
of diff.

## Done criteria

A clean commit:
- [ ] One logical change (atomicity)
- [ ] Diff reviewed before staging (no surprises)
- [ ] Message summary is imperative, ≤72 chars, specific
- [ ] Body present if change is non-obvious; explains why
- [ ] No debugging artifacts, secrets, or unintended files
- [ ] Tests pass on this commit (not just "tests pass after the next 3 commits")
