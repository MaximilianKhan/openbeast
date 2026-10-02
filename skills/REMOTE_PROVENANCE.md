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
| `Reviewed by` | Initials of the human who ran the review + sandbox probe |
| `Rewrite notes` | One line on what we changed during the strip/rewrite pass (tool names remapped, paths generalized, dangerous instructions removed, etc.). `promote` appends the scan it passed (scanner version, score, accepted rule ids) |

## Ledger

| Skill | Source URL | Upstream rev | SHA-256 | Tree SHA-256 | Imported | Reviewed by | Rewrite notes |
|---|---|---|---|---|---|---|---|
| _(none yet — first import will land here)_ | | | | | | | |

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
