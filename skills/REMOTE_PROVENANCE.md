# Remote Skill Provenance Ledger

Every skill in this directory that was sourced from outside our repo gets
one entry below. The ledger is the *only* record that connects a SKILL.md
on disk back to its upstream origin — so it's the only way we'd notice if
a "trusted" skill turned out to be a poisoning vector.

See the **"Selectively pull skills from browse.sh"** entry in
[`../docs/TODO.md`](../docs/TODO.md) for the full review gate. Short
version: per-skill human review, sandbox probe, hash pin, no auto-pull.

## How to add an entry

Use the tool; it does the fetch, the probe, the hashes and the row, and it
refuses to do any of them out of order:

```bash
./scripts/skill-import.sh install-scanner                 # once: pinned SkillSpector, own venv
./scripts/skill-import.sh fetch https://github.com/<owner>/<repo> \
    --rev <40-hex commit sha> --name <skill> [--path <dir in repo>]
#   → .run/skill-staging/<skill>/ plus the scan's findings. Nothing in skills/ yet.
#   Read every file. Rewrite the staged copy (tool names, paths, frontmatter name).
./scripts/skill-import.sh promote <skill> --reviewed-by <initials> --notes "what we changed"
#   → re-scans what you edited, copies it to skills/<skill>/, writes the row below.
git add skills/<skill> skills/REMOTE_PROVENANCE.md      # one commit
```

**No row, no skill** — a remote skill landing without a ledger entry is
a tree-state bug, not a forgivable oversight. `./scripts/skill-import.sh
verify` (also run by `./start.sh doctor` and `tests/test_scripts.sh`) fails
when a row and the files on disk disagree.

A scanner finding is accepted only by naming its rule id (`--accept TM1,PE3`),
and the accepted ids are written into the row's notes. The scan is static
(`--no-llm`), so a skill that *discusses* dangerous patterns is flagged like one
that uses them; reading the flagged lines is the reviewer's job.

A file the scanner could only **partly** inspect is open the same way, until
you name it: `--read-in-full SKILL.md,scripts/run.py` says "the scanner could
not finish this file, so I read all of it", and the row records it. A file it
did not inspect at all, a fatal exception or a failed analyzer cannot be named
away.

**Who signs.** `--reviewed-by MK` means a human read every file. An agent
asked to do an import signs as an agent (`--agent-read <agent> --ordered-by
MK`), and the `Reviewed by` cell reads `agent <agent> for MK`. That row is
weaker: the reader is the kind of system a poisoned skill is written to fool.
`verify` and `./start.sh doctor` count such rows. After reading the skill
yourself, `./scripts/skill-import.sh attest <skill> --reviewed-by MK` replaces
the cell; it refuses if the files no longer match the row.

The hashes, by hand:

```bash
sha256sum skills/<name>/SKILL.md | cut -d' ' -f1
(cd skills/<name> && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
```

## Required fields

| Field | What goes here |
|---|---|
| `Skill` | Local directory name under `skills/` (e.g. `rust-borrow-patterns`) |
| `Source URL` | Exact upstream URL — pin a permalink (commit/tag), not a moving `main` |
| `Upstream rev` | Commit SHA / version tag at import time |
| `SHA-256` | sha256 of the *imported* SKILL.md as it sits on disk after our rewrite |
| `Tree SHA-256` | one digest over every file in the skill directory (command above), so a changed helper script or reference file is caught too |
| `Imported` | ISO date (YYYY-MM-DD) of the import |
| `Reviewed by` | Initials of the human who read every file, or `agent <name> for <initials>` when an agent read it on that human's instruction and no human has attested yet |
| `Rewrite notes` | One line on what we changed during the strip/rewrite pass (tool names remapped, paths generalized, dangerous instructions removed, etc.). `promote` appends the scan it passed (scanner version, score, accepted rule ids, partly inspected files read in full) |

## Ledger

| Skill | Source URL | Upstream rev | SHA-256 | Tree SHA-256 | Imported | Reviewed by | Rewrite notes |
|---|---|---|---|---|---|---|---|
| `executing-plans` | https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/executing-plans | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | `be741e799e6b8b6184267eca15b7a813c813af52effbca6c2ddd19883f332d42` | `4045e731e14cc0ec06f9ed23b7c4edde2d5ecd5d07034f47351445a00fe67027` | 2026-10-02 | agent claude-opus-5-5 for MK | Removed scripts/task-start and scripts/task-done (they call scripts from a skill not imported) and wrote their steps as plain commands; mapped superpowers:* names to ours; workspace moved to .run/plans/; reworded the 'git clean' warning; added MIT LICENSE and provenance · scan: SkillSpector 2.12.0 static, score 0, CAUTION, scanner partial on SKILL.md (read in full) |
| `receiving-code-review` | https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/receiving-code-review | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | `f5f6a0c27a5cec34fd0c094838c95609eb220a1beb706058f60fd5ac887720bb` | `e9456cb4ffa2929ff71213cb00d9806df03cda067956eb2fe7683dfde3065c85` | 2026-10-02 | agent claude-opus-5-5 for MK | Body verbatim; added MIT LICENSE and a provenance section · scan: SkillSpector 2.12.0 static, score 0, CAUTION |
| `verification-before-completion` | https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/verification-before-completion | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | `e433276376b4df1e6547d0df28693f2a48f7ba495c1f6d1671dab70288111d9e` | `bca766c22ee3325e14a7483b29375fe5b5e61cc688245b365d2701e2d0ca442e` | 2026-10-02 | agent claude-opus-5-5 for MK | Body verbatim; added MIT LICENSE and a provenance section. EA2 is the red-flag list line 'About to commit/push/PR without verification' · scan: SkillSpector 2.12.0 static, score 7, CAUTION, accepted EA2 |
| `writing-plans` | https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/writing-plans | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | `be267f394afb5684d670d514fe45c3c1b6b273b6485e205b0b3be76d84d87131` | `13856d6d864c29a25f6c2814c698e1a7bf1155d27bef465f8e1a85a9690751cc` | 2026-10-02 | agent claude-opus-5-5 for MK | Mapped superpowers:* skill names to ours; plans saved under docs/plans/; handoff offers only executing-plans (subagent-driven-development not imported); added MIT LICENSE and provenance · scan: SkillSpector 2.12.0 static, score 0, CAUTION |

## Refresh policy

A refresh = a fresh import. If you pull an updated SKILL.md from
upstream:

1. Diff the new upstream body against the version pinned by our current
   hash. Read every line of the diff.
2. Re-run the probe: `skill-import.sh fetch ... --force` stages and scans the
   new revision, and `skill-import.sh diff <skill>` shows it against the live
   copy. Check for `bash`/`fetch`/credential-touching instructions.
3. Re-apply our rewrite (tool names, paths, attribution).
4. **Replace** the ledger row — do not append a second row for the same
   skill (`promote` does this). Bump the `Upstream rev`, `SHA-256`, `Imported`, and `Reviewed
   by` fields, and rewrite the notes line to describe what changed
   relative to the prior import.

## Removal

If a skill gets pulled (because upstream went dark, we lost trust, or
it was superseded by an in-house version), delete both the
`skills/<name>/` directory and its ledger row in the same commit. The
ledger is meant to mirror the live catalog, not preserve history —
`git log` is the audit trail.
