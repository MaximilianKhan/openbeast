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
| `frontend-design` | https://github.com/anthropics/skills/tree/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/frontend-design | `8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4` | `e07538ed069c829ebb4a75d15db6da56a332115f3eefa1f0fcdbd7fa96586616` | `e2e15777eeaa876b29f49ec172fef87d64d5775593dfd7d7ae4bc1f6e090efc3` | 2026-10-02 | agent claude-opus-5-5 for MK | Body verbatim; added an 'In OpenBeast' section (beast-artifact page rules, offline fonts) and provenance. AR2 = the copywriting line 'Errors don't apologize' · scan: SkillSpector 2.12.0 static, score 13, CAUTION, accepted AR2 |
| `mcp-builder` | https://github.com/anthropics/skills/tree/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/mcp-builder | `8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4` | `7b8a7e19fd1b3c49cf942540915c0c3303986489cfa6fde048b73021d028100c` | `cbdced72ddbe193f1caf69b1502ccb3fc25049beae792982f2eb0805140c7934` | 2026-10-27 | agent claude-opus-5-5 for MK | Removed scripts/ (evaluation harness calls Anthropic's hosted API) and the 'Running Evaluations' half of reference/evaluation.md; added an 'In OpenBeast' section; WebFetch → fetch; provenance. E1 = api.example.com in samples; PE3 = 'validate access tokens'; EA1 = 'should NOT call any tools that modify state'; RP1 = unpinned npx inspector (pin note added) 2026-10-27 refresh: supporting files read by the absolute skill dir from the skill() header (a relative skills/ path only works in the checkout); evaluation runs go through OpenCode, since runner.py cannot attach an MCP server. · scan: SkillSpector 2.12.0 static, score 83, DO_NOT_INSTALL, accepted AE1,E1,EA1,PE3,RP1, scanner partial on reference/node_mcp_server.md,reference/python_mcp_server.md (read in full) |
| `receiving-code-review` | https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/receiving-code-review | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | `f5f6a0c27a5cec34fd0c094838c95609eb220a1beb706058f60fd5ac887720bb` | `e9456cb4ffa2929ff71213cb00d9806df03cda067956eb2fe7683dfde3065c85` | 2026-10-02 | agent claude-opus-5-5 for MK | Body verbatim; added MIT LICENSE and a provenance section · scan: SkillSpector 2.12.0 static, score 0, CAUTION |
| `skill-creator` | https://github.com/anthropics/skills/tree/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/skill-creator | `8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4` | `8421a7ebfa300e01ebd8286c1c64754fb8eacc326772a3adc26f100f4e77103c` | `2b651ef1f2aca2b0f7c74103d56e9b2b18ee85a887df4e4da726cf5a4b429c63` | 2026-10-27 | agent claude-opus-5-5 for MK | Removed run_eval.py, improve_description.py, run_loop.py, generate_report.py, utils.py (claude CLI / hosted model), quick_validate.py and package_skill.py (reject our frontmatter), assets/, and the Claude.ai and Cowork sections; viewer.html no longer loads Google Fonts or the SheetJS CDN; generate_review.py no longer kills the process on its port; added 'In OpenBeast' and 'Triggering' sections; provenance. LP3 = no declared permissions 2026-10-27 refresh: supporting files read by the absolute skill dir from the skill() header; a block-scalar description is read as '>' (not empty) and test_scripts.sh now rejects it. · scan: SkillSpector 2.12.0 static, score 7, CAUTION, accepted LP3, scanner partial on eval-viewer/viewer.html (read in full) |
| `verification-before-completion` | https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/verification-before-completion | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | `e433276376b4df1e6547d0df28693f2a48f7ba495c1f6d1671dab70288111d9e` | `bca766c22ee3325e14a7483b29375fe5b5e61cc688245b365d2701e2d0ca442e` | 2026-10-02 | agent claude-opus-5-5 for MK | Body verbatim; added MIT LICENSE and a provenance section. EA2 is the red-flag list line 'About to commit/push/PR without verification' · scan: SkillSpector 2.12.0 static, score 7, CAUTION, accepted EA2 |
| `webapp-testing` | https://github.com/anthropics/skills/tree/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/webapp-testing | `8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4` | `7cf959d0d0b7de8a0f47e79a552a84285918664fe2d8b3b61899624544e7363b` | `8d3215ee55d7265d02c18a7d2c729ba09a7813170c20a35eb3c0e1d231ea6b30` | 2026-10-27 | agent claude-opus-5-5 for MK | Added an 'In OpenBeast' paragraph (Playwright needs its own venv; not in the lockfile) and a caution on with_server.py; example output paths /mnt/user-data/outputs → /tmp; provenance. TM1/AST4 = with_server.py runs the operator's server command with shell=True; LP3 = no declared permissions 2026-10-27 refresh: script paths resolved against the absolute skill dir from the skill() header, not the cwd. · scan: SkillSpector 2.12.0 static, score 64, DO_NOT_INSTALL, accepted AST4,LP3,TM1 |
| `writing-plans` | https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/writing-plans | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | `be267f394afb5684d670d514fe45c3c1b6b273b6485e205b0b3be76d84d87131` | `13856d6d864c29a25f6c2814c698e1a7bf1155d27bef465f8e1a85a9690751cc` | 2026-10-02 | agent claude-opus-5-5 for MK | Mapped superpowers:* skill names to ours; plans saved under docs/plans/; handoff offers only executing-plans (subagent-driven-development not imported); added MIT LICENSE and provenance · scan: SkillSpector 2.12.0 static, score 0, CAUTION |
| `writing-skills` | https://github.com/obra/superpowers/tree/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/writing-skills | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | `ee09079207f8ed7c0bd3e4e1a8d3b71f8ebfd215ace6dcbd4fc21cd0e8086c1c` | `7b3826658b16f7da71d3e862b649cd8e8cfef20df36555323e6b392a74761f79` | 2026-10-27 | agent claude-opus-5-5 for MK | Removed anthropic-best-practices.md (not MIT) and render-graphs.js; added an 'In OpenBeast' section; mapped skill names and paths (examples now use skills/ not ~/.claude/skills/); added MIT LICENSE and provenance. RA1 = the phrase 'write skill'; AS3 = a wc -w example path 2026-10-27 refresh: supporting files read by the absolute skill dir from the skill() header; a block-scalar description is read as '>' (not empty) and test_scripts.sh now rejects it. · scan: SkillSpector 2.12.0 static, score 82, DO_NOT_INSTALL, accepted AE1,AS3,RA1, scanner partial on SKILL.md,testing-skills-with-subagents.md (read in full) |

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
