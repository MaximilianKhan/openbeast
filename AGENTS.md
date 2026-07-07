# Agent Instructions for this Repository

You're working in **OpenBeast**, a fully local AI workstation — llama.cpp
serving Qwen and Gemma models, MCP-based tool server, OpenCode + Open WebUI as frontends,
a 159-task eval suite (40 easy / 53 medium / 66 hard across 12 categories),
and 14 curated skills for specialized work.

This file is auto-loaded as project-wide instructions. Read it once at the
start of a session.

## Use skills first

You have access to **14 curated skills** via three MCP tools:

- `list_skills()` — see all available skills (one line each)
- `load_skill(name)` — read the full skill body inline
- `start_skill_agent(skill, task, ...)` — spawn a sub-agent with the skill activated

**Before approaching any non-trivial task, call `list_skills`** and check
whether one matches. Skills encode hard-won lessons — don't reinvent them.

## Task → skill mapping

| Situation | Skill |
|---|---|
| Working in this repo (or any unfamiliar codebase) for the first time this session | `codebase-onboarding` |
| User request is vague / open-ended | `spec-extraction` |
| About to commit, push, or open a PR | `git-discipline` |
| Input is huge (10K+ lines, 30+ files, big PR, paper, transcript) | `long-context-synthesis` |
| Adding a feature with tests, in TDD style | `test-driven-development` |
| Non-trivial change: new subsystem, public API change, refactor crossing modules | `architecture-proposal` |
| User wants something faster / spotted a perf issue / hot path code | `performance-optimization` |
| Designing a new function signature / endpoint / library API | `api-design` |
| User asks for "review" / "audit" / "look this over" | `code-review` |
| Security review / vulnerability check / threat model | `security-audit` |
| Hard debugging — obvious fix didn't work | `debugging-methodology` |
| Intractable problem, exhausted obvious paths, "consult the council" | `deep-counsel` |
| User wants to add a new eval task to the suite | `eval-task-author` |
| User wants multi-language variants (Py/Go/C/C++) on an existing task | `eval-variant-porter` |

When multiple skills could apply, pick the one most-specific to the task.
You can also chain — e.g., `codebase-onboarding` → `spec-extraction` →
`architecture-proposal` → implementation, for a meaty change in a new repo.

For long-running specialized work (eval task authoring, deep counsel,
multi-pass review of a large PR), prefer `start_skill_agent` to delegate;
your main conversation stays responsive while the sub-agent grinds.

## Project conventions

### Default model

`Qwen3.6-27B Uncensored Q5_K_P` (HauhauCS Aggressive uncensored fine-tune; #2 on
the internal leaderboard at 96.16% on v3.5). Launched by `./start.sh` with no args.
The dense `Qwen3.6-27B Q5_K_XL` is the top raw scorer (97.85%) and the 35B-A3B MoEs
are faster — each is one arg away, e.g. `./start.sh serve-qwen-27b-q5.sh`.

### Where things live

| What | Where |
|---|---|
| Source: model serving, agents, MCP tools | `agents/`, `scripts/`, `start.sh`, `stop.sh` |
| Eval suite (159 tasks across 12 categories) | `evals/` — see `evals/README.md` |
| Skills (this system) | `skills/` — see `skills/README.md` |
| Tests | `tests/test_scripts.sh`, `tests/test_smoke.sh` |
| Documentation (technical) | `docs/INSTALL.md`, `docs/REFERENCE.md`, `docs/RESULTS.md`, `docs/SKILLS_PLAN.md`, `docs/TODO.md`, `docs/WORK_PLAN.md` |
| Documentation (overview / persona / tools) | `README.md`, `system-prompt.md`, `system-prompt-tools.md` |

### Eval suite

- 159 base tasks; 13 of them have multi-language variants (223 effective
  test units across Python / Go / C / C++ / Rust / Zig)
- Run a single model: `python3 evals/benchmark_all.py --models <slug>`
- Full sweep (5 models): `python3 evals/benchmark_all.py` (~10–12 hours
  overnight on the 5090)
- Score: `python3 evals/scoring.py --show` (with TOKENS column)
- Distribution + methodology: `evals/README.md`

### Skills

- Repo skills: `skills/<name>/SKILL.md` (this directory)
- Global skills: `~/.local/share/local-llm-skills/<name>/SKILL.md`
- Repo wins on name collision
- After editing a skill: call `reload_skills()` (no need to restart MCP)

### Commit and PR style

The `git-discipline` skill encodes the rules. Quick version:
- Atomic commits (one logical change per commit)
- Imperative subject ≤72 chars
- Body explains WHY, not WHAT
- Never `git add .` blindly; review staged diff first
- Never amend / force-push commits already pushed to shared branches

## When NOT to use skills

For trivial tasks — typo fixes, one-line changes, simple questions — skills
are overhead. Use them when the task involves design choices, multi-step
reasoning, or domain-specific discipline. The bar: would loading the skill
prevent a real failure mode? If yes, load it. If no, just do the task.

## Tool surface (17 MCP tools)

Beyond skills (`list_skills`, `load_skill`, `start_skill_agent`,
`reload_skills`), you have:
- File / code: `read_file`, `write_file`, `edit_file`, `list_files`, `grep`
- Shell: `bash`
- Web: `fetch`, `web_search` (via local SearXNG)
- Long-running agents: `start_agent`, `check_agent`, `tail_agent`,
  `list_agents`, `stop_agent`

Prefer `edit_file` over `write_file` for existing files. Prefer running code
over reasoning about what it should do.

## Don't reinvent

If you find yourself thinking "I should write some code to do X" — first
check if there's a skill for X, an existing helper in `agents/tools.py`, or
a script in `scripts/`. The repo has accumulated tooling; use it.
