# Harness prior art: how Droid lifts many models (2026-11-16)

**Question:** does Factory's Droid lift every model at once by hard-coding
per-language and per-scenario knowledge into its harness, injected on every
turn? **Answer: partly.** Droid does hard-code per-scenario text and per-model
text. No public source shows per-language text. Its per-scenario text is
injected when an event fires, not on every turn. The lift Factory attributes
to the harness comes from:

- tool design;
- environment discovery at startup;
- short timeouts with tracked background work;
- plan reminders;
- bounded-autonomy guards;
- a per-model adapter layer.

Language knowledge reaches Droid through the user's AGENTS.md and skills,
which load on demand.

Method: public sources only, read on 2026-11-16. These were Factory's posts and
docs, the open Codex source (commit `0637230e`), and Anthropic and LangChain
engineering posts. One more source was third-party: tweakdroid, a tool that
extracts the prompt blocks from the Droid 1.6.0 binary. Nobody ran Droid.

- **Documented** means a vendor post, vendor doc or source code says it.
- **Third-party** means the claim comes from tweakdroid or a write-up.
- **Inferred** is our reading.

All benchmark numbers are self-reported by the vendor unless noted otherwise.

## Verdict on the claim

| Part of the claim | Status | Evidence |
|---|---|---|
| Hard-coded per-**language** knowledge | **Not found.** | The Terminal-Bench post (2025-09-25) gives no per-language guidance. None of the roughly 60 prompt blocks that tweakdroid catalogues for Droid 1.6 is language-specific. Codex's base prompt (~3.4K words) has none either. |
| Hard-coded per-**scenario** knowledge | **Yes.** Most of it is event-triggered. | tweakdroid lists more than 40 injected `<system-reminder>`/notification blocks (open todo items at turn end, no todo list yet, spec mode, worktree, files changed externally, deferred tools, subagent list). Factory calls these "system notifications": "contextually-injected user-level messages providing critical, time-sensitive guidance", placed late in the context because models show "recency bias". |
| Hard-coded per-**model** knowledge | **Yes.** Documented. | "Droid tunes its prompts, tool descriptions, and tool-calling mode for each model it supports" (2026-10-29). Edit format and path style vary by provider (2025-09-25). tweakdroid sees provider variants of the identity and main prompts, three Gemini-only blocks, and registry flags (`no-comments`, `batchTodoWrite`). |
| "Used on every turn" | **Mostly no.** | Always on: a small system prompt (2,155 tokens), tool definitions (8,528 tokens; 2 sent up front, the rest deferred), a startup environment block (2,362 tokens). Everything else is injected on a trigger. |
| "Lifts every model at once" | **Partly.** Goal stated, not proven. | Factory: the goals "must hold across every model family", but changes that help one model and hurt another ship as options. Its own comparison page says system-level differences "do not isolate the effect of a particular prompt or tool". |

## 1. What Droid injects, and when

| Layer | What | When | Status |
|---|---|---|---|
| System prompt | Identity block, then a main prompt for interactive or `exec` mode. The main prompt holds sections on the harness, repositories, communication and todo planning. The prompt is ~1.6K → 2.2K tokens (0.170 → 0.250); its growth was user communication and environment handling (images, apps, personal data). | Session start; cached prefix | Documented (2026-10-29). Section names are third-party (tweakdroid). |
| Provider and model variants | Separate identity and main prompts per provider (Anthropic, OpenAI, Google). Gemini-only blocks on risk level, spec mode and tool preference. A "no comments" block for models tagged that way; a todo-batching block for models flagged `batchTodoWrite`. | Session start | Third-party (tweakdroid); consistent with the documented per-model tuning |
| Environment facts | The 2025 version listed available programming languages, git repo contents, environment variables and running processes. These were "shown as shell command output", which "helped the agent avoid reissuing similar commands". The 2026 version is a startup-env block with OS, date, model, language, directory and git info, and project and personal instructions. "Language" there may mean natural language; this is not determined. | First turn | Documented (2025-09-25); block contents third-party |
| Runtime awareness | Elapsed tool and session time is shown only once it passes set thresholds. | Threshold-triggered | Documented (2025-09-25) |
| Per-language guidance | None published. Languages appear only as a discovered fact ("available programming languages"). | none | Documented absence |
| Repo or project memory | Up to 80K characters of AGENTS.md / CLAUDE.md at startup. **Dynamic discovery**: reading, editing, listing or searching a directory pulls in that directory's AGENTS.md, up to a further 40K characters. Shell commands do not trigger it. There is no built-in cross-session memory in the CLI; memory is markdown files plus hooks. | Startup, plus each file-tool call | Documented (docs, read 2026-11-16) |
| Tool instructions | Rich tool descriptions with simplified schemas. "Complex schemas caused error rates to rise sharply." Rarely used tools are listed by name with a short description; the full schema loads on demand. | Startup index; schema on use | Documented (2025-09-25, 2026-05-20) |
| Planning | A TodoWrite tool. The plan reminder sits "at the end of the context window". There are reminders when no todo list exists yet and when a turn ends with open items. | Each step; at turn end | Documented (2025-09-25); reminder texts third-party |
| Verification | No published test-before-done step for Droid. Background services stay up "for checks". | none found | Documented absence (inferred: covered by the open-items reminder and the prompt) |
| Bounded autonomy | Three turns with no visible output stop the loop. Three identical tool calls trigger a nudge; six stop the loop. "When the agent gets stuck, always have a next step ready: nudge, retry, summarize, or stop." | Triggered | Documented (2026-10-29) |

## 2. Tool design

- **Few tools, simple schemas.** Error rates per tool call compound, so
  "reducing individual tool-call errors produced multiplicative gains" in task
  completion (2025-09-25). Droid 1.0 sends **2 tool definitions up front**
  (down from 11) and loads the rest on demand. Tools named in the post are
  Script, Read, Execute, ApplyPatch, TodoWrite, and one tool to check, wait on
  or stop a running program (2026-10-29).
- **Editing is per model.** One provider gets find-and-replace, another V4A
  diffs. Some models get relative paths, others absolute. An edit returns a
  short summary, not the whole file (2025-09-25, 2026-10-29).
- **Output.** "Printed output is capped in size, and the full results are
  logged where the model can search them later." No cap value is published.
  The proposal checklist says "Budget every token, and keep large tool results
  in files" (2026-10-29).
- **Long-running commands.**
  - 2025: short default timeouts "led to better average performance", with
    long ones opt-in. Background processes are opt-in, filtered and tracked
    for cleanup.
  - 2026: a program waits up to 5 minutes, then returns its output so far, or
    starts in the background and returns at once. "Droid wakes the model with
    a reminder when background work finishes, so no requests are spent
    polling."
- **Search.** Ripgrep (2025-09-25).
- **No LSP or diagnostics tool** appears in any public source. `droid doctor`
  is a setup checker, not a code checker.
- **Script mode** (Droid 1.0). The model writes JavaScript that makes many tool
  calls in a sandbox. It is on by default only for a list of frontier models;
  for Claude Opus 5.5 it cuts cost by 15%.
- **Line-numbered reads** cut Opus's cost 22% on one task but raised GLM's by
  8% and Kimi's by 32%. They ship as an opt-in. **This is the clearest public
  example of a per-model knob.**

## 3. Per-model adaptation versus one harness

Droid uses **one shared core with per-model adapters** (documented in
2025-09-25: "modular architecture with shared core components and
model-specific adaptations"). The adapters cover:

- prompt variants;
- tool descriptions;
- edit format;
- path style;
- tool-calling mode;
- registry flags.

BYOK models on any OpenAI-compatible endpoint (Ollama, vLLM) go through
`generic-chat-completion-api`. How a model outside the registry is tuned is
not published (inferred: provider fallback prompts and no registry flags).

**Codex** does the same in open source (`models-manager/src/model_info.rs` at
`0637230e`):

- a per-model `ModelInfo` with an instructions template, `shell_type`,
  `apply_patch_tool_type`, `truncation_policy`, a context window and a
  compaction limit;
- per-model prompt files (`gpt_5_codex_prompt.md`, `gpt-5.2-codex_prompt.md`, …);
- for an unknown slug, a fallback with "local model" messages, no apply_patch
  tool and a **10,000-byte** tool-output cap.

**LangChain:** "models require different prompting". Its Claude run scored
lower "because we didn't run the same Improvement Loop with Claude"
(2026-02-17).

## 4. Measured results

| Benchmark | Model | Harness → score | Source |
|---|---|---|---|
| Terminal-Bench Core v0.1.1 (80 tasks, 5 runs) | Opus 4.1 | Droid 58.75, Claude Code 43.2 | Factory, 2025-09-25 |
| " | GPT-5 | Droid 52.5, Codex CLI 42.8 | " |
| " | Sonnet 4 | Droid 50.5 | " |
| Terminal-Bench 4 (3 trials, Oct 23–28) | Opus 5.5 | Droid 65.2, Cursor 62.1, OpenCode 62.1, Devin 59.6, Claude Code 57.6 | Factory, 2026-10-29 |
| " | GPT-6.1 Sol | Droid 57.6, Codex 54.0, OpenCode 51.0, Devin 45.0 | " |
| Terminal-Bench 2.0 | Opus 4.6 | Claude Code 58.0, Terminus 2 62.9, Terminus-KIRA 74.7, Meta-Harness 76.4, ForgeCode 81.8 (Droid 69.9 per a Medium summary, not verified) | Vaughan, 2026-04-09; dev.to survey |
| Terminal-Bench 2.0 | gpt-5.2-codex | deepagents: 52.8 → 66.5 from **harness changes only** | LangChain, 2026-02-17 |
| TB 2.0, 20 models | n/a | Median gap between a model's best and worst harness: **13.6 points**. "Not a randomized ablation." | arXiv 2606.20683 via dev.to |
| aider (edit format) | Qwen2.5-Coder-32B | whole-file 16.4 vs diff 8.0 (provider also differed) | via dev.to |

Two caveats on the Factory numbers:

- Factory's comparison page is dated 2026-09-28 but cites an October study,
  so its date is unreliable.
- Factory publishes no ablation. **No source isolates which mechanism earns
  Droid's lead.**

**Stated principles** (Factory, 2026-10-29):

- Budget every token, and keep large tool results in files.
- Grow the request only by appending, so the prompt cache holds.
- Profile the hot path to inference.
- Track background work and wake the model when it finishes.
- Always have a next step ready when the agent is stuck.

## 5. Other harnesses

| Harness | Mechanism | Bears on | Source |
|---|---|---|---|
| LangChain deepagents | Pre-completion checklist that intercepts exit; startup map of cwd and installed tools such as Python; time-budget warnings; per-file edit-count loop detection; "reasoning sandwich" (xhigh-high-xhigh; xhigh throughout scored 53.9 on timeouts) | A unified harness, tuned on one model; "many principles generalize" | 2026-02-17 |
| Meta-Harness | A sandbox snapshot before the loop (cwd, files, languages, package managers, memory) "saves 2–5 early exploration turns". Gains are larger on weaker models. | Environment bootstrapping | arXiv 2603.28052 via Vaughan, 2026-04-09 |
| ForgeCode | Mandatory reviewer skill before submit; reordered and flattened JSON schemas; truncation reminders on partial reads | Verification, schemas | via Vaughan, 2026-04-09 |
| Codex CLI | An environment-context message (cwd, shell); AGENTS.md from root to cwd (32 KiB); per-model `ModelInfo`; byte-capped truncation | Per-model adapters | Source at `0637230e`; OpenAI 2026-01-23 (via summaries) |
| Claude Code | Tool output capped at 25,000 tokens; truncation should "steer agents with helpful instructions"; errors should give "specific and actionable improvements" | Output formatting | Anthropic, 2025-09-11 |
| Anthropic long-running | A start-of-session routine (pwd, git log, progress file); features marked passing "only … after careful testing" | Verification | Anthropic, 2025-11-26 |
| Warp | Periodic pty output to the agent; a `/plan` tool "to keep agents on track" | Long-running commands, planning | 2025-06-25 |

**Pattern across vendors (inferred):**

- What carries across models: environment discovery, verification at the
  exit, time and turn awareness, loop guards and output hygiene.
- What is tuned per model: edit format, prompt wording and tool-calling mode.
- No vendor publishes per-language static knowledge.

Their models are frontier models with current API knowledge. Our Zig 0.16
drift (G6) is a problem they do not have, so **packs are our own lever, not
one copied from Droid**. They should stay measured and conditional.

## 6. Mapping to OpenBeast

> **Note, 2026-11-18.** This table predates G22 and G23
> ([HARNESS_NOTES.md](../HARNESS_NOTES.md)), which this review prompted.
> "Environment bootstrap" now has an opt-in first-turn environment block
> (G22, `OPENBEAST_AGENT_ENV_BLOCK=1`), and "Turn and time awareness" an
> opt-in wall-clock notice at 50% and 80% of the wall (G23,
> `OPENBEAST_AGENT_WALL_WARN=1`). Both are off by default; arm E of the
> GLM build-up ran them together (MODEL_CAMPAIGN.md §8). The "Gap" column
> below is the state on 2026-11-16.

| Droid-style mechanism | OpenBeast today | Gap |
|---|---|---|
| Few tools, simple schemas | Runner prompt lists 10 tools (19 on the MCP/WebUI surface) | Small |
| Environment bootstrap as command output | `platform_line` (OS, shell, BSD caveats) only. `run_eval.toolchain_versions()` is collected for the results header, never shown to the agent. | **Large** |
| Turn and time awareness | Turn budget up front; remaining-turns notice at ≤ 3 (G1). No wall-clock notice. | Medium (wall time) |
| Long commands: partial output, then background, then wake | bash timeout 120 s; the whole group is killed at timeout; background processes die when the call returns | **Large** |
| Output capped, full output searchable | 50K characters inline, head and tail kept (~12K tokens); nothing saved to a file | Medium |
| Plan reminder at the end of the context; open items at turn end | Plan re-shown each turn (G2). No open-items check at `task_done`. | Small–medium |
| Exit verification | G9 opt-in nudge (`OPENBEAST_AGENT_VERIFY_DONE`); R1 path guard refuses `task_done` while expected files are missing | Measure, then default |
| Loop guards (3 → nudge, 6 → stop; 3 empty turns → stop) | 3 identical calls → nudge (G3); no hard stop; no per-file edit count | Small |
| Per-model adapter (edit format, prompt variant, read numbering) | One prompt and one `edit_file` for every model; per-model only through env (thinking, seed, max tokens) | Medium–large, costly |
| Event-triggered knowledge | Escalation cards on compiler errors; push diagnostics; G13 edit hint | Ahead of Droid here |
| Static per-language text | Zig 0.16 pack, always on in pack arms | Keep it conditional and measured |
| Project memory found on file touch | AGENTS.md matters to OpenCode and WebUI; evals run in `/tmp` | Not for evals |

## 7. Gaps, ranked by likely impact

"Era" marks a change to hashed runner or tool files, which needs a new board
row. Each gap is a candidate arm, not a commitment.

| # | Gap | Why (evidence) | Era |
|---|---|---|---|
| 1 | **Turn-0 environment block as command output.** Toolchain versions (`zig version`, `go version`, `cc --version`, `python3 -V`, `rustc -V`), workdir listing, which task paths already exist, available tools (rg, shellcheck). | G1: the turn cap ends most failures. Meta-Harness reports it saves 2–5 exploration turns, with larger gains on weaker models. Droid and LangChain both do it. The data is already in `run_eval.toolchain_versions()`. | Yes |
| 2 | **Wait, then background, then wake for long commands.** Return partial output at the timeout while the command keeps running under supervision. Add a status/wait/kill tool; inject a reminder when it finishes. Name the elapsed time. | Droid 2025 and 2026; Warp. Today a timeout throws away the work and the turn (G8 is related). Matters for builds and test suites on the Spark. | Yes |
| 3 | **Exit verification on by default, if G9 measures positive.** Extend it into a checklist: destination re-run, plan items open, expected files exist. | LangChain counts this as a main contributor to +13.7; ForgeCode makes it mandatory; Droid has the open-items reminder. G9 shows fixes left behind in scratch files. | Yes |
| 4 | **A per-model adapter registry** (`agents/model_profiles`). Edit tool variant (str_replace, whole-file for small files, patch), path style, prompt addenda, read line numbering. Ship with no-op defaults and turn knobs on per model only from measured arms. | Droid and Codex both have one. The aider data suggests diffs cost a 32B coder model about half its score. G13 shows rewrite churn under diagnostics. | Yes (each knob) |
| 5 | **Spill oversized output to a session file.** Cut the inline cap from 50K characters to about 8–10K (Codex falls back to 10KB), and append the path and a grep hint. | Factory principle; Codex fallback; DeepSeek idea #4. Large outputs slow a local 27B and push turns toward compaction. | Yes |
| 6 | **Time awareness.** A unit wall-clock line once elapsed time passes 50% and 80%, next to the turn notice. | Droid's threshold notices; LangChain's time-budget warning. G7 and G11 show turns lost to wall time, not to the turn count. | Yes |
| 7 | **Thinking by phase** (a local "reasoning sandwich"). Thinking on for the first planning turn and for the turns after a failed check; off for mechanical turns. | LangChain's sandwich; G5 (slips that thinking would catch). Keeps speed. | Yes |
| 8 | **Finish the loop guard.** Stop at 6 identical calls; count edits per file and nudge at N (LangChain); stop after 3 turns with no tool call. | Droid's thresholds; LangChain saw "10+ times" variations on one broken approach. | Yes |
| 9 | **Keep language knowledge triggered, not always on.** Prefer escalation cards and `language_reference` over growing packs. Add a pack only where the model's knowledge is shown to be stale (Zig 0.16), and keep it an arm. | No vendor uses static per-language text. Our Zig problem is drift that frontier models do not have. | Arm only |
| 10 | **AGENTS.md found on file touch for non-eval sessions** (beast-chat, background agents) | Droid's dynamic discovery, capped at 40K | No |

## Not determined

- Droid's actual tool description texts, and the startup-env block's exact
  fields. tweakdroid documents block names and opening lines, not full text.
  Whether "language" in that block means a programming language is unclear.
- How Droid tunes a BYOK local model (Qwen or GLM on llama.cpp) beyond the
  generic provider.
- Which mechanism produces Droid's lead. Factory publishes system-level
  comparisons only, and says these do not isolate any one prompt or tool.
- The Droid score on Terminal-Bench 2.0 (69.9 with Opus 4.6) comes from a
  secondary summary and was not checked on the leaderboard.

## Sources

- [Factory, "Droid: The #1 Software Development Agent on Terminal-Bench"](https://factory.com/news/terminal-bench), 2025-09-25
- [Factory, "Droid 1.0: Building a Frontier Harness"](https://factory.com/news/frontier-harness), 2026-10-29
- [Factory, "What is the best harness for AI coding?"](https://factory.com/articles/terminal-bench-agent-comparison), page dated 2026-09-28 (cites an October 2026 study)
- [Factory, "Deferred Context Engine"](https://factory.com/news/deferred-context-engine), 2026-05-20
- [Factory docs, AGENTS.md](https://docs.factory.com/harness/agents-md), read 2026-11-16; [memory management](https://docs.factory.ai/guides/power-user/memory-management); [BYOK](https://docs.factory.ai/model-independence/byok)
- [skrabe/tweakdroid](https://github.com/skrabe/tweakdroid) `src/index.mjs`, prompt catalog for Droid 1.6.0, pushed 2026-11-07 (third-party reverse-engineering)
- [openai/codex](https://github.com/openai/codex) at `0637230e` (2026-11-17 UTC): `codex-rs/models-manager/src/model_info.rs`, `codex-rs/models-manager/prompt.md`, `codex-rs/core/*_prompt.md`
- [OpenAI, "Unrolling the Codex agent loop"](https://openai.com/index/unrolling-the-codex-agent-loop), 2026-01-23 (read through secondary summaries)
- [LangChain, "Improving Deep Agents with harness engineering"](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering), 2026-02-17
- [Anthropic, "Writing effective tools for AI agents"](https://www.anthropic.com/engineering/writing-tools-for-agents), 2025-09-11
- [Anthropic, "Effective harnesses for long-running agents"](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), 2025-11-26
- [D. Vaughan, "Harness Performance on Terminal-Bench"](https://codex.danielvaughan.com/2026/04/09/harness-performance-terminal-bench/), 2026-04-09 (updated 2026-11-16), citing [Meta-Harness, arXiv 2603.28052](https://arxiv.org/abs/2603.28052)
- [dev.to, "Stop Comparing Models: Same Claude, 58% or 76%"](https://dev.to/gabrielanhaia/stop-comparing-models-same-claude-58-or-76-on-terminal-bench-depending-on-the-harness-16d1) (undated), citing arXiv 2606.20683 §7.4
- [Warp, "How we scored #1 on Terminal-Bench"](https://www.warp.dev/blog/terminal-bench), 2025-06-25 (read through excerpts)
- OpenBeast: `agents/runner.py`, `agents/tools.py`, `evals/run_eval.py`, `docs/HARNESS_NOTES.md` (G1–G18)
