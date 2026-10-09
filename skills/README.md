# Skills

Curated packages of instructions for specialized work — code review,
security audit, eval-task authoring, debugging methodology, deep counsel.

The model discovers them via MCP and loads them on demand. See
[`docs/SKILLS_PLAN.md`](../docs/SKILLS_PLAN.md) for the full design.

## Currently shipped (15 skills)

### Universal-applicability (Tier 1)

| Skill | What it's for |
|---|---|
| `codebase-onboarding` | Orient before editing — README → structure → entry points → tests, *then* edit |
| `spec-extraction` | Extract a precise spec (inputs/outputs/edges/constraints/non-goals) from a vague request |
| `git-discipline` | Atomic commits, meaningful messages, no random staging |
| `long-context-synthesis` | Process huge inputs (10K+ line PRs, papers) via the chunk → summarize → synthesize loop |

### Specialized work (Tier 2)

| Skill | What it's for |
|---|---|
| `test-driven-development` | Real TDD — red, green, refactor; never skip seeing the test fail |
| `architecture-proposal` | Design doc before code for non-trivial changes (motivation → goals → design → alternatives → risks) |
| `performance-optimization` | Measure → profile → focused change → re-measure; never speculate |
| `api-design` | Function signature + types + error model + examples *first*; implement second |

### Review and analysis

| Skill | What it's for |
|---|---|
| `code-review` | Multi-pass review (correctness → security → perf → idioms → tests) |
| `security-audit` | Focused security audit across 8 categories with threat-model framing |
| `debugging-methodology` | Hypothesis-driven root-cause analysis; the `reproduce → hypothesize → falsify` loop |
| `deep-counsel` | Slow-mode reasoning ritual for intractable problems; the war council |

### Project-specific

| Skill | What it's for |
|---|---|
| `eval-task-author` | Authoring eval suite tasks; encodes the 6 pitfalls from past post-mortems |
| `eval-variant-porter` | Adding multi-language variants (Python/Go/C/C++/Rust/Zig) to existing tasks |
| `beast-lang` | The offline language library: look up what the *installed* compiler confirmed, add a verified claim, rebuild the escalation index, review model-drafted claims. Written for cloud models working in this repo — `prompt_index: false`, so it is not in the local model's always-on menu |

### Imported (9 skills, off the always-on menu)

These came from outside this repo through the import gate (below). Each is
pinned by hash in [`REMOTE_PROVENANCE.md`](REMOTE_PROVENANCE.md), carries its
upstream licence, and sets `prompt_index: false`: `skill()` lists it and
`skill(name)` loads it, but it costs a local model nothing per turn and leaves
the eval era alone. Every one has an "In OpenBeast" or "Provenance" section
saying what we changed.

| Skill | From | What it's for |
|---|---|---|
| `verification-before-completion` | obra/superpowers (MIT) | Run the check and read its output before saying anything is done, fixed or passing |
| `receiving-code-review` | obra/superpowers (MIT) | Taking review feedback: verify against the codebase, push back with reasons. Counterpart of `code-review` |
| `writing-plans` | obra/superpowers (MIT) | Turn a spec into an implementation plan of small tasks, each with its test. Follows `architecture-proposal` |
| `executing-plans` | obra/superpowers (MIT) | Run such a plan task by task with a ledger that survives compaction, and one whole-branch review at the end |
| `writing-skills` | obra/superpowers (MIT) | Write a skill the TDD way: watch an agent fail without it, write it against those failures, re-test |
| `skill-creator` | anthropics/skills (Apache-2.0) | Measure a skill: with-skill and baseline runs, graded assertions, a benchmark, a review page for a human |
| `frontend-design` | anthropics/skills (Apache-2.0) | Visual direction for a page that should not look templated; the authoring skill for beast-artifact pages |
| `webapp-testing` | anthropics/skills (Apache-2.0) | Drive a local web app with Python Playwright (needs its own venv; not in the stack's lockfile) |
| `mcp-builder` | anthropics/skills (Apache-2.0) | Building an MCP server in Python or TypeScript |

**Where they disagree with ours, or with each other:**

- `writing-skills` and `skill-creator` give opposite advice on two points. The
  first says a description states only *when* to use a skill and favours
  absolute rules; the second wants *what and when* and explained reasons. Our
  15 in-house descriptions follow the second style. Format is settled by the
  schema below; the rest is for measuring.
- `executing-plans` and `writing-plans` name skills we did not import
  (upstream's own TDD, debugging, review and branch-finishing skills). Those
  references now point at `test-driven-development`, `debugging-methodology`,
  `code-review` and `git-discipline`.
- `mcp-builder` recommends broad API coverage and service-prefixed tool names.
  Our tool server is deliberately small and unprefixed, because large tool
  surfaces hurt Qwen-class models. Its "In OpenBeast" section says which
  advice applies when.
- `skill(name)` returns only `SKILL.md`. The four imports that ship other
  files (`writing-skills`, `skill-creator`, `webapp-testing`, `mcp-builder`)
  tell the model to `read_file` them by repo path.

## How the model uses them

From any MCP-aware client (OpenCode, Open WebUI):

```
skill()                             → see what's available + descriptions
skill("code-review")                → read the full instructions inline
start_skill_agent("code-review",    → spawn a sub-agent with the skill activated
                  "review /tmp/changes.patch")
```

The agent decides when to invoke. Helpful prompts: "use a skill if relevant",
"is there a skill for this?", "spawn a {skill} agent on this".

## Adding a new skill

1. `mkdir -p skills/my-skill/`
2. Write `skills/my-skill/SKILL.md` with frontmatter + body (see schema below)
3. Run `./scripts/install-skills.sh` to verify it's discoverable
4. It's live immediately — `skill()` rescans the skills directory on every
   index call (no restart needed)
5. Decide whether it belongs in the **always-on menu**. `python3
   scripts/generate-skill-index.py` rewrites the skill list in
   `system-prompt-tools.md`, which every local-model turn pays for and which
   is one of the six era-hashed files — so adding a menu entry rolls the eval
   cache era (`./scripts/eval-era.sh`) and CI fails on a stale index. A skill
   written for cloud models working in this repo (like `beast-lang`) sets
   `prompt_index: false` in its frontmatter: it stays reachable by name
   through `skill("my-skill")` and `start_skill_agent`, costs the local model
   nothing, and leaves the era alone.

`tests/test_scripts.sh` validates that every `SKILL.md` parses cleanly and
has the required frontmatter fields.

## Importing a skill from outside this repo

Never by copying. A skill body goes straight into a sub-agent's system prompt,
so a remote one comes in through the gate, one skill at a time:

```bash
./scripts/skill-import.sh install-scanner       # once
./scripts/skill-import.sh fetch <https git url> --rev <40-hex sha> --name <skill> [--path <dir>]
# read every staged file, rewrite the copy in .run/skill-staging/<skill>/
./scripts/skill-import.sh promote <skill> --reviewed-by <initials> --notes "what we changed"
```

`fetch` pins a commit and scans it; `promote` re-scans what you edited, refuses
on any finding you have not accepted by rule id (and any file the scanner only
partly inspected that you have not named with `--read-in-full`), keeps the skill
off the always-on menu (`prompt_index: false`), and writes the row in
[`REMOTE_PROVENANCE.md`](REMOTE_PROVENANCE.md). `verify` (run by doctor and the
test suite) fails when a row and the files disagree. An agent doing an import
signs as an agent (`--agent-read <agent> --ordered-by <initials>`); a human
upgrades that row with `attest` after reading the skill. Design, measured scanner
behaviour and the verdicts on 15 popular skill repos:
[`docs/EXTERNAL_SKILLS_PLAN.md`](../docs/EXTERNAL_SKILLS_PLAN.md).

## SKILL.md schema

```markdown
---
name: my-skill
description: One-line description. What's it for, when to activate. The model sees this when deciding whether to load.
allowed_tools: [bash, read_file, edit_file, grep]
recommends_subagent: false
---

# My skill

(Markdown body — instructions, checklists, examples, anti-patterns.)
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Stable identifier; should match the directory name |
| `description` | yes | What and when. Keep it short. |
| `allowed_tools` | no | Recommended tool subset (advisory, not enforced in v1) |
| `recommends_subagent` | no | If `true`, prefer invoking via `start_skill_agent` for long-running work |
| `prompt_index` | no | `false` keeps the skill out of the always-on menu that `scripts/generate-skill-index.py` writes into `system-prompt-tools.md` — it stays reachable through `skill()` / `skill(name)`. For skills aimed at cloud models: the menu costs a local model tokens every turn, and that file is hashed into the eval cache era |

## Repo vs global

Two locations are searched, repo first (wins on collision):

- `<repo>/skills/` — version-controlled, project-local (this directory)
- `~/.local/share/local-llm-skills/` — shared across all projects on this box

Global skills are managed via `./scripts/install-skills.sh`. Use `--link-all`
to symlink every repo skill globally; use `--link <name>` for one at a time.
