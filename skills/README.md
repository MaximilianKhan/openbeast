# Skills

Curated packages of instructions for specialized work — code review,
security audit, eval-task authoring, debugging methodology, deep counsel.

The model discovers them via MCP and loads them on demand. See
[`docs/SKILLS_PLAN.md`](../docs/SKILLS_PLAN.md) for the full design.

## Currently shipped (14 skills)

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

## How the model uses them

From any MCP-aware client (OpenCode, Open WebUI):

```
list_skills()                       → see what's available + descriptions
load_skill("code-review")           → read the full instructions inline
start_skill_agent("code-review",    → spawn a sub-agent with the skill activated
                  "review /tmp/changes.patch")
```

The agent decides when to invoke. Helpful prompts: "use a skill if relevant",
"is there a skill for this?", "spawn a {skill} agent on this".

## Adding a new skill

1. `mkdir -p skills/my-skill/`
2. Write `skills/my-skill/SKILL.md` with frontmatter + body (see schema below)
3. Run `./scripts/install-skills.sh` to verify it's discoverable
4. Restart the MCP server (or call `reload_skills()` from a client)

`tests/test_scripts.sh` validates that every `SKILL.md` parses cleanly and
has the required frontmatter fields.

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

## Repo vs global

Two locations are searched, repo first (wins on collision):

- `<repo>/skills/` — version-controlled, project-local (this directory)
- `~/.local/share/local-llm-skills/` — shared across all projects on this box

Global skills are managed via `./scripts/install-skills.sh`. Use `--link-all`
to symlink every repo skill globally; use `--link <name>` for one at a time.
