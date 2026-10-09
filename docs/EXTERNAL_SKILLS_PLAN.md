# External skills: what to take from the ecosystem, and the gate it comes through

**Status (2026-10-01):** step 1 of 5 is built. The import gate
(`scripts/skill-import.sh`) exists, is tested, and has been run against the
real scanner. **No external skill has been imported.** One candidate
(Humanizer) is staged for review in `.run/skill-staging/` and is not in the
tree.

This doc records a review of 15 widely used agent-skill repositories against
OpenBeast's mission, the verdict on each, and the design of the gate that any
of them must pass through. It complements [`SKILLS_PLAN.md`](SKILLS_PLAN.md)
(how skills work here) and the ledger
[`skills/REMOTE_PROVENANCE.md`](../skills/REMOTE_PROVENANCE.md).

## 1. Motivation

There is now a large public catalog of skills in the `SKILL.md` format we
already use. Some of it covers real gaps: we have no skill for authoring a
beast-artifact page, none for browser testing, none for writing a plan and
executing it, and the 24/7 long-horizon item in `TODO.md` has no design for
state that outlives a context window.

Two things stopped us from using any of it:

1. **The review gate was manual.** `TODO.md` ("Selectively pull skills") has
   required a pinned source, a probe, a content hash and a ledger row since
   2026-05. Nobody ran four manual steps, so the ledger stayed empty.
2. **Most of the catalog does not fit.** Nothing ever leaves hardware you own,
   an install works with the cable unplugged, and we measure what we ship.
   Several popular skills are thin wrappers over a cloud account.

## 2. Goals and non-goals

**Goals**

- One command per gate step: fetch a pinned commit, scan it, promote it with a
  named reviewer, verify it later.
- Fail closed: no scanner, a crashed scanner, or a report the gate does not
  recognise is a refusal.
- An import costs the local model nothing by default (`prompt_index: false`)
  and so does not roll the eval era.
- A recorded verdict for each of the 15 repositories, with the reason.

**Non-goals**

- No automatic "pull latest", no runtime skill fetching, no trust on first
  use. Unchanged from `TODO.md`.
- The gate does not replace the reviewer. `promote` refuses without a named
  reader, and records whether that reader was a human or an agent.
- No vendoring of any third-party tool into the stack's Python closure. The
  scanner lives in its own venv.
- No import is decided here. Each one is its own review and its own commit.

## 3. Four facts that decide fit

These come from our own records, not from the skills:

| Fact | Source | Consequence |
|---|---|---|
| Local models do not fire skills on their own: 0 of 197 task agents called one | `TODO.md`, 2026-05 smoke test | A skill only helps where the menu or the harness puts it in front of the model |
| Every always-on menu entry is paid for on every turn, and `system-prompt-tools.md` is era-hashed | `skills/README.md` | Imports default to `prompt_index: false` |
| Harness mechanisms work where prompt text does not (`update_plan` re-injection, push-diagnostics) | `agents/runner.py`, `LANG_AWARENESS_PLAN.md` | The most valuable things to take are patterns to build, not `SKILL.md` bodies |
| `skill(name)` returns only the `SKILL.md` body | `agents/mcp_server.py` | Skills that ship reference files or scripts need a loader change or a heavy rewrite |

## 4. Verdicts

Read from each repository's README, file tree and selected skill files on
2026-10-01. Only SkillSpector was installed and run. Star counts are as the
GitHub API reported them that day.

| Repo | Licence | Verdict | Where it would plug in |
|---|---|---|---|
| NVIDIA/SkillSpector | Apache-2.0 | **Adopted** (this change) | The probe step of the import gate |
| OthmanAdi/planning-with-files | MIT | **Adopt the pattern** | On-disk plan, findings and progress for long-horizon runs |
| Graphify-Labs/graphify | Apache-2.0 | **Adopt behind an A/B** | Local code graph for the "RAG for local codebases" item |
| obra/superpowers | MIT | **Import four skills** | The ones we lack; not the plugin |
| anthropics/skills | per skill | **Import selectively** | Artifact design, browser testing, skill authoring |
| tt-a1i/archify | MIT | Optional skill | Validated diagrams into `publish_artifact` |
| nextlevelbuilder/ui-ux-pro-max-skill | MIT | Optional skill | A local design database for artifact pages |
| blader/humanizer | MIT | Optional, first through the gate | Prose pass for release notes and docs |
| ayghri/i-have-adhd | MIT | Optional preset | An opt-in output style, never a default |
| mvanhorn/last30days-skill | MIT | Borrow the idea | Recency-scored research over our own `web_search` |
| yusufkaraaslan/Skill_Seekers | MIT | Operator tool only | Draft generator for beast-lang staging or offline reference packs |
| thedotmack/claude-mem | Apache-2.0 | Skip, borrow the design | Capture, compress, inject a small index |
| calesthio/OpenMontage | AGPL-3.0 | Skip | Licence, and cloud video APIs |
| coreyhaines31/marketingskills | MIT | Skip for the product | Off-mission |
| browser-act/skills | MIT | Skip | Closed CLI, an account, anti-bot evasion |

### The five worth integrating

**SkillSpector.** A static scanner for skills (prompt injection, exfiltration,
tool poisoning, supply chain) with a JSON report and stable exit codes. It is
the probe step of our gate; details and measured behaviour in §5 and §6.

**planning-with-files.** The idea is a plan that lives on disk, is re-injected
every turn, and can hold the agent's stop until it reports complete. We have
the in-context half already: `update_plan` is re-shown every turn, compaction
preserves it, and `--resume` replays it from the transcript. What is missing is
the on-disk half that survives across sessions and days, which is the open
"context/memory strategy across days" question in the 24/7 item. Port the
pattern into the runner and the session ledger; do not install the plugin.
Their evidence (3 of 3 blind A/B wins) is their own and small.

**Graphify.** Parses code with tree-sitter into a queryable graph, locally,
with no LLM and no telemetry; the optional pass over docs accepts any
OpenAI-compatible server. It would cover the "RAG pipeline for local codebases"
item without ChunkHound's CPU embedding sidecar. Wire it as a CLI the model
calls through `bash`, not as its seven-tool MCP server: our arsenal research
found large tool surfaces hostile to Qwen-class models. Two catches: the PyPI
name is `graphifyy`, so it needs hash-pinning; and the eval is saturated
outside zig, so showing a benefit needs a repo-navigation task set first.

**Superpowers.** We already cover TDD, debugging, review, spec extraction and
architecture proposals. The gaps are `verification-before-completion`,
`writing-plans` with `executing-plans`, `receiving-code-review` and
`writing-skills`. The plugin itself is not wanted: its brainstorming companion
loads a logo from the author's site as a usage beacon.

**Anthropic Skills.** `frontend-design` and `web-artifacts-builder` would
become the authoring skill beast-artifact lacks. `webapp-testing` is the
"Playwright as a skill" item already in `TODO.md`. `skill-creator` covers
evals for skills and description tuning, which bears on the open skill-fire
experiment. The `docx`, `pdf`, `pptx` and `xlsx` skills declare
`license: Proprietary` and cannot be vendored. The per-skill `LICENSE.txt` of
the others has not been read yet; do that before importing any.

### Why the last four are skipped

- **claude-mem:** the installer pushes a browser sign-in, some install paths
  default to hosted memory, and it brings Bun, Chroma and cloud sync. Same
  conclusion as the 2026-07 arsenal research: build our own.
- **OpenMontage:** AGPL-3.0 cannot be vendored into an Apache-2.0 repo, and its
  pipelines call cloud video generation.
- **marketingskills:** nothing to do with a local AI workstation.
- **BrowserAct:** the skill drives a closed CLI that needs an account and sells
  fingerprint spoofing, proxies and CAPTCHA solving.

## 5. Design of the gate

```
fetch <https url> --rev <40-hex sha> --name <skill> [--path <dir>]
   │   refuses: branch/tag/short sha, non-https, credentials in the URL,
   │   `..` in the path, OFFLINE=true, no scanner. All before git runs.
   ▼
.run/skill-staging/<skill>/  +  <skill>.provenance.json      (gitignored)
   │   scan (static) and print every finding. Nothing in skills/ yet.
   │   ── human: read every file, rewrite the staged copy ──
   ▼
promote <skill> --reviewed-by <initials> [--accept IDS] [--read-in-full FILES] [--notes "..."]
   │   (an agent: --agent-read <agent> --ordered-by <initials> in place of --reviewed-by)
   │   re-scans WHAT WAS EDITED; refuses on any open finding, any unnamed partly
   │   inspected file, or an incomplete scan;
   │   never overwrites a skill that has no ledger row (one of ours);
   │   adds `prompt_index: false` when the skill does not say
   ▼
skills/<skill>/  +  one row in skills/REMOTE_PROVENANCE.md
   ▼
verify            every row's SHA-256 and tree SHA-256 still match the disk
                  (run by `./start.sh doctor` and `tests/test_scripts.sh`)
```

**Interface.** `scripts/skill-import.sh` with `install-scanner`, `scanner`,
`fetch`, `scan`, `diff`, `promote`, `attest`, `verify`. Exit 0 pass, 1 could
not judge, 3 the gate said no.

**Policy: accept by rule id.** A finding is open until the reviewer names its
rule id (`--accept TM1,PE3`), and the accepted ids are written into the ledger
row. The scanner's own `SAFE / CAUTION / DO_NOT_INSTALL` label is shown but
does not decide; §6 is why.

**What blocks regardless.** The scanner missing, timing out or exiting with an
error; a report without the fields this gate reads; the scanner reporting its
own execution as unsuccessful, any file not inspected at all, a fatal analysis
exception, or a failed analyzer.

**Partly inspected files: open until named.** The scanner marks a file partly
inspected when one of its pattern analyzers gives up on it
(`static_parse_limit`, `obfuscated_instruction_text`, `manifest_parse_error`).
The first version of this gate refused on any such file. Measured on
2026-10-02, that rule refuses two of our own skills (`eval-variant-porter`,
`performance-optimization`) and 5 of the 10 external skills staged that day,
all on ordinary prose or short shell scripts. So it is handled like a finding:
the file is open until the reviewer names it (`--read-in-full SKILL.md`),
meaning "the scanner could not finish this file, so I read all of it", and the
row records it. If the report's count of partly inspected files and the files
it names disagree, the gate refuses.

**Who signs.** `--reviewed-by` takes a human's initials and means a human read
every file. An agent asked to run an import signs as an agent (`--agent-read
<agent> --ordered-by <initials>`); the cell then reads `agent <agent> for MK`,
`verify` and doctor count those rows, and `attest` lets a human take one over
after reading the skill, provided the files still match the row. An agent's
read is weaker than a human's: it is the kind of reader a poisoned skill is
written to fool. The row says which one happened.

**Two hashes.** The ledger pinned only `SKILL.md`. Skills that ship helper
scripts are the riskier kind, so the row now also carries a tree digest over
every file; `verify` checks both.

**Symlinks and size.** A skill containing a symlink or a non-regular file is
refused, as is one over 2000 files or 50 MiB.

**No config side effects.** The script does not source `lib/conf.sh` (that
appends a generated secret to `openbeast.conf`); it reads `OFFLINE` directly.

**Scanner install.** `install-scanner` puts SkillSpector, pinned to the v2.12.0
commit, in its own venv under `~/.local/share/openbeast/skillspector`.
`SKILL_SCANNER` overrides the binary.

## 6. What the real scanner did (2026-10-01, SkillSpector 2.12.0, `--no-llm`)

| Input | Result | Reading |
|---|---|---|
| A fixture that reads `~/.ssh/id_rsa` and posts it to a remote host | `DO_NOT_INSTALL`, score 65: P1, PE3 ×3, E1 | The scan catches the obvious attack |
| Our `skills/security-audit` | `DO_NOT_INSTALL`, score 54: TM1, PE3 ×2, OH3 | False positive: a security checklist names `shell=True` and `/etc/shadow` |
| Our `skills/code-review` | `CAUTION`, score 0, **zero findings** | `CAUTION` came from a backticked path that is not a bundled file |
| All 15 repo skills | 2 `SAFE`, 12 `CAUTION`, 1 `DO_NOT_INSTALL`; findings in 5 | About 1.5 s per skill |
| Humanizer at `225a6f39ac85` | `CAUTION`, score 49: AR2 ×2, RP1 ×4, LP3 | AR2 is the phrase "without warning" inside an example sentence; RP1 is unpinned `npx` in the README |

Three conclusions shaped the policy:

- Static-only precision is moderate, so a hard block on `DO_NOT_INSTALL` with
  no override would reject legitimate skills, including our own.
- `CAUTION` alone carries no signal, and a `partial` completeness status alone
  must not block.
- A finding's value is that it points the reviewer at a line. That is what the
  gate prints, and why acceptance is by rule id and not a blanket flag.

## 7. Egress and trust: what this adds

- **Skill contents stay local.** The gate always passes `--no-llm`; a test
  asserts it.
- **Dependency names do not.** SkillSpector's supply-chain check sends the
  package names and versions a skill declares to `api.osv.dev`, even with
  `--no-llm`. It falls back to a bundled list when OSV is unreachable. There is
  no flag to disable it. On an `OFFLINE=true` rig that request is attempted and
  fails; this has not been tested on an actually isolated box.
- **The scanner's own dependencies are not hash-pinned.** The SkillSpector
  commit is pinned; its transitive closure (langgraph, boto3, yara-python and
  others) is whatever pip resolves. It runs in its own venv and only when an
  operator imports a skill, but it is weaker than the stack's own lockfile.
- **`fetch` and `install-scanner` are refused under `OFFLINE=true`.** Stage on
  a connected box and carry `.run/skill-staging/` over.

## 8. Alternatives considered

- **Keep the gate manual.** Rejected: five months, zero imports, and a ledger
  whose only check was memory.
- **Trust the scanner's recommendation.** Rejected on the measurements in §6:
  it would block our own `security-audit` and pass nothing extra.
- **Run the scanner's LLM pass on the rig.** It would cut false positives and
  keep contents local (`SKILLSPECTOR_PROVIDER=openai_compatible` at `:8080`).
  Not built: it is untested against Qwen's structured output, and on the
  single-slot default it takes the slot. Worth measuring; see §9.
- **Write our own scanner.** Rejected for now: 71 maintained patterns across 17
  categories is more than we would write, and the tool is Apache-2.0.
- **Install the popular skills with `npx skills add`.** Rejected: it bypasses
  the pin, the scan, the review and the ledger, and most installers also add
  hooks or plugins.
- **Do nothing.** We keep 15 in-house skills and write each new one from
  scratch. Viable, and still the right answer for most of the catalog.

## 9. Risks and what is still open

- **A reviewer can accept everything.** `--accept` with every id is one
  command. The gate makes the review visible and recorded, not mandatory in
  spirit. The ledger row shows what was accepted.
- **Scanner upgrades change the report.** The gate refuses a report missing its
  fields and warns on a version other than 2.12.0. Bump `SCANNER_REV` and
  `SCANNER_VERSION` together after reading a real report.
- **Not in CI.** CI runs the stubbed tests and the ledger hash check. It does
  not run SkillSpector over `skills/`, because that would mean installing an
  unlocked dependency closure in CI. A baseline file plus a locked install
  would allow it.
- **`promote` copies the whole staged directory.** A skill fetched from a
  repository root brings the README, plugin manifests and CI files with it.
  Deleting what is not the skill is part of the rewrite.
- **Imports land in the repo only.** The global directory
  (`~/.local/share/local-llm-skills/`) is not covered by the gate or the
  ledger.
- **Reversibility.** Removing an import is deleting `skills/<name>/` and its
  row in one commit. Removing the gate is deleting three files and three hooks
  (doctor, `tests/test_scripts.sh`, `tests/run_tests.sh`).

## 10. Roadmap

| Step | State |
|---|---|
| 1. Import gate with SkillSpector as the probe | **Built** (this change) |
| 2. Humanizer through the gate as the first ledger row | Staged; needs a human read and a rewrite |
| 3. Four Superpowers skills, the Anthropic artifact and Playwright skills, all `prompt_index: false` | Not started |
| 4. On-disk plan for the long-horizon runner | Not started; needs its own proposal |
| 5. Graphify trial behind an A/B | Blocked on a repo-navigation task set |

Cross-cutting, unscheduled: let the loader expose a skill's reference files;
measure the scanner's LLM pass on the rig; a locked scanner install for CI.
