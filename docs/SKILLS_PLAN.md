# Skills for OpenBeast

**Status (2026-05-06):** ✅ Phases 1–4 implemented in this same session. ⏸️
Phase 5 (routing layer) deferred — not enough friction yet to justify the
work. This doc is the save state and the design rationale.

## What "skills" means here

Anthropic's Skills format (a folder per skill containing `SKILL.md` +
optional resources, loaded on-demand based on description matching) is the
shape we replicate. Local LLMs (Qwen, Gemma) don't natively understand
"skills" the way Claude does — but **the mechanism is just structured prompt
augmentation**, and we can give the model description-based progressive
disclosure via MCP tools.

A skill in this stack is:
1. A folder with `SKILL.md` (YAML-ish frontmatter + markdown instructions) and
   optional resources (checklists, formulas, examples)
2. Discoverable via the MCP server (`list_skills()`)
3. Loadable on-demand by the model (`load_skill(name)`)
4. Composable with long-running agents (`start_skill_agent(skill, task, ...)`)

## Where they live

**Hybrid with priority. Repo wins on collision.**

| Path | Purpose | Versioned | Sync mechanism |
|---|---|---|---|
| `/home/max/Documents/models/skills/` | Project-local skills (eval helpers, this stack's quirks) | yes (git) | clone the repo |
| `~/.local/share/local-llm-skills/` | Global skills (universal expertise) | optional (separate git) | manual or separate repo |

`scripts/install-skills.sh` creates the global dir and (optionally) symlinks
specific repo skills out as starting points.

## How discovery works

Both dirs are walked at MCP startup. For each subdirectory containing a
`SKILL.md`, the frontmatter is parsed. The `description` field is what the
model sees when it calls `list_skills()`. The full body is returned only on
`load_skill(name)` — progressive disclosure preserved.

## SKILL.md schema

```markdown
---
name: code-review
description: Multi-pass code review for correctness, security, performance, and idioms. Activate when the user asks for "review", "audit", "check this code", or post-PR review.
allowed_tools: [bash, read_file, edit_file, grep]
recommends_subagent: false
---

# Code Review

(Markdown body — instructions, checklists, examples, anti-patterns.)
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Stable identifier; used by `load_skill(name)` and `start_skill_agent(skill=name, ...)`. Must match the directory name. |
| `description` | yes | Short. What's it for, when to activate it. The model sees this to decide whether to load. |
| `allowed_tools` | optional | Recommended tool subset. Advisory in v1; could become enforced in `start_skill_agent`. |
| `recommends_subagent` | optional | If `true`, the skill is intended to be invoked via `start_skill_agent` (long-running specialized work) rather than inlined into the current conversation. |

## Three integration patterns

### Pattern A — MCP-loaded (✅ implemented)

Two MCP tools:
- `list_skills()` — returns `[{name, description, source}]` for all skills
- `load_skill(name)` — returns the full `SKILL.md` body

The agent's tool guidance mentions: "You have skills available; call
`list_skills()` to see them, `load_skill(name)` when one matches your task."
Model decides what to load. We pay system-prompt cost only for the index
(a few KB), not the content of every skill (potentially 100s of KB).

### Pattern B — System-prompt prepend (not used)

Concat all `SKILL.md` files into the system prompt at startup. Doesn't scale
beyond 2-3 always-on skills. Skipped.

### Pattern C — Routing layer (Phase 5, deferred)

Pre-flight pass classifies the user message against skill descriptions, then
auto-loads relevant skills before the main inference. Most "magical" UX,
biggest engineering lift. Defer until we feel friction.

## Skill-aware sub-agents (✅ implemented)

The killer integration. `start_skill_agent(skill, task, ...)`:

1. Resolves the skill (repo first, global fallback)
2. Reads `SKILL.md`, strips frontmatter
3. Wraps body in `=== ACTIVATED SKILL: {name} ===\n{body}\n=== END SKILL ===` framing
4. Passes the wrapped body as `--context` to a freshly-spawned `runner.py`
5. Returns the agent_id

The runner's existing `build_system_prompt()` already appends `--context` to
the system prompt under a "Background context from the caller:" header — so
the sub-agent gets the soul file + agent instructions + activated skill +
task. No runner changes needed.

**Concrete patterns this unlocks:**
1. **Parallel specialized review.** Main agent spawns 3 sub-agents:
   `start_skill_agent("security-audit", ...)`,
   `start_skill_agent("code-review", ...)`,
   `start_skill_agent("debugging-methodology", ...)`. They work concurrently;
   main agent gathers results.
2. **Eval task creation.** "Write a new task for X" → spawn sub-agent with
   `eval-task-author` skill. Sub-agent produces JSON + reference impl + verifies validation.
3. **Deep counsel.** When stuck, main agent spawns sub-agent with
   `deep-counsel` and the full problem context. Slow-mode reasoning unhindered
   by tool-call interruptions.
4. **Research delegation.** Long literature dives — sub-agent reads 20 papers
   in the background; main agent stays responsive.

## Initial skill set (✅ shipped)

| Skill | Why valuable here |
|---|---|
| **eval-task-author** | Encodes the 6 pitfalls from `evals/README.md` (numpy lint trap, fixture re-assertion, perf-gate calibration, "solve it yourself", etc.). Companion piece to the eval suite. |
| **eval-variant-porter** | Stdio-based variant pattern (4 languages, heredoc fixtures, build commands, validation template). Distilled from the Phase-4 work. |
| **code-review** | Multi-pass checklist: correctness, security, performance, idioms, tests. |
| **security-audit** | Focused security review: SQLi, XSS, auth, crypto, secrets, timing-safe compares. |
| **debugging-methodology** | Hypothesis-driven root-cause analysis. "Form hypothesis → design cheapest falsifying experiment → run → repeat." |
| **deep-counsel** | The slow-mode reasoning ritual from the soul file, made explicit and invocable. |

## Phase plan + status

| Phase | Status | Deliverable |
|---|---|---|
| 1. Skill discovery + load | ✅ | `list_skills()` and `load_skill(name)` MCP tools; `skills/` dir at repo base; `SKILL.md` schema; `system-prompt-tools.md` updated |
| 2. First real skill set | ✅ | 6 starter skills authored |
| 3. Skill-aware sub-agents | ✅ | `start_skill_agent` MCP tool |
| 4. Global skills + installer | ✅ | `scripts/install-skills.sh`; mcp_server.py walks both dirs |
| 5. Routing layer | ⏸️ deferred | Auto-select skills based on user query before inference |

## After implementation: how to use it

### From any MCP-aware client (OpenCode, Open WebUI)

The model sees `list_skills`, `load_skill`, `start_skill_agent` as tools. It
decides when to use them. No special invocation needed. Suggested user phrasing
to nudge the model toward skills: "use a skill if relevant", "is there a skill
for this?", "spawn a {skill} agent on this".

### From the agent runner (programmatic / `agent.sh`)

The standalone runner doesn't know about skills, but anything you pass via
`--context` becomes part of the system prompt. To activate a skill in a
non-MCP context:

```bash
python3 agents/runner.py \
  --context "$(cat skills/code-review/SKILL.md)" \
  "Review the diff in /tmp/work/changes.patch"
```

Or use `start_skill_agent` from inside an MCP-aware session, which orchestrates
this for you.

### Adding a new skill

1. `mkdir -p skills/my-skill`
2. Write `skills/my-skill/SKILL.md` with frontmatter + body
3. Restart the MCP server (the `list_skills` cache is built at startup —
   future improvement: hot reload)

`tests/test_scripts.sh` validates that every `SKILL.md` parses cleanly and has
the required frontmatter fields.

## Decision log

| Question | Decision | Why |
|---|---|---|
| YAML frontmatter parser? | Manual parser, no PyYAML dependency | The project tries to stay lean; PyYAML is an extra runtime dep just for a few-line frontmatter |
| Skill body in system prompt vs context? | Context (via runner's `--context` flag) | No runner changes needed. Existing framing ("Background context from the caller") is sufficient. |
| Cache skills at startup or read on every call? | Cache at startup | Tiny; trades freshness for simplicity. Hot-reload is a future improvement. |
| Enforce `allowed_tools`? | Advisory in v1 | Hard enforcement requires per-call tool filtering in the MCP layer; defer until we have a skill that genuinely needs it. |
| Routing layer (Phase 5)? | Defer | The model's already pretty good at calling `list_skills` when prompted. Real friction needed before adding a classifier. |

## Open follow-ups

- **Hot-reload skills.** Currently MCP server caches skills at startup. If you
  edit a skill, restart the MCP. Could add a `reload_skills()` MCP tool, or
  re-scan on each `list_skills()` call (cheap).
- **`allowed_tools` enforcement.** When `start_skill_agent` runs a sub-agent,
  could filter the tool list passed to runner. Needs runner-level support.
- **Skill versioning.** No story yet. If a skill changes incompatibly,
  consumers don't know.
- **Skill composition.** Can a skill depend on another skill? Currently no.
- **Skill marketplace / share.** A separate global-skills repo could be
  cloned to `~/.local/share/local-llm-skills/` so skills sync across machines.
