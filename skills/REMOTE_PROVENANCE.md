# Remote Skill Provenance Ledger

Every skill in this directory that was sourced from outside our repo gets
one entry below. The ledger is the *only* record that connects a SKILL.md
on disk back to its upstream origin — so it's the only way we'd notice if
a "trusted" skill turned out to be a poisoning vector.

See the **"Selectively pull skills from browse.sh"** entry in
[`../docs/TODO.md`](../docs/TODO.md) for the full review gate. Short
version: per-skill human review, sandbox probe, hash pin, no auto-pull.

## How to add an entry

When importing (or refreshing) a remote skill, append a new row to the
table below and stage it in the same commit as the SKILL.md changes.
**No row, no skill** — a remote skill landing without a ledger entry is
a tree-state bug, not a forgivable oversight.

Compute the hash with:

```bash
sha256sum skills/<name>/SKILL.md | cut -d' ' -f1
```

## Required fields

| Field | What goes here |
|---|---|
| `Skill` | Local directory name under `skills/` (e.g. `rust-borrow-patterns`) |
| `Source URL` | Exact upstream URL — pin a permalink (commit/tag), not a moving `main` |
| `Upstream rev` | Commit SHA / version tag at import time |
| `SHA-256` | sha256 of the *imported* SKILL.md body as it sits on disk after our rewrite |
| `Imported` | ISO date (YYYY-MM-DD) of the import |
| `Reviewed by` | Initials of the human who ran the review + sandbox probe |
| `Rewrite notes` | One line on what we changed during the strip/rewrite pass (tool names remapped, paths generalized, dangerous instructions removed, etc.) — empty if mirrored verbatim, which should be rare |

## Ledger

| Skill | Source URL | Upstream rev | SHA-256 | Imported | Reviewed by | Rewrite notes |
|---|---|---|---|---|---|---|
| _(none yet — first import will land here)_ | | | | | | |

## Refresh policy

A refresh = a fresh import. If you pull an updated SKILL.md from
upstream:

1. Diff the new upstream body against the version pinned by our current
   hash. Read every line of the diff.
2. Re-run the sandbox probe (load into a throwaway session, check for
   `bash`/`fetch`/credential-touching instructions).
3. Re-apply our rewrite (tool names, paths, attribution).
4. **Replace** the ledger row — do not append a second row for the same
   skill. Bump the `Upstream rev`, `SHA-256`, `Imported`, and `Reviewed
   by` fields, and rewrite the notes line to describe what changed
   relative to the prior import.

## Removal

If a skill gets pulled (because upstream went dark, we lost trust, or
it was superseded by an in-house version), delete both the
`skills/<name>/` directory and its ledger row in the same commit. The
ledger is meant to mirror the live catalog, not preserve history —
`git log` is the audit trail.
