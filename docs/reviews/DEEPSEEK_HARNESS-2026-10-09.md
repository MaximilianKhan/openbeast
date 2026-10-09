# DeepSeek Harness: evaluation (2026-10-09)

**Decision (Max, 2026-10-09): not integrated.** Nothing in OpenBeast is
retired for it and it is not offered as a frontend. The mechanisms worth
porting are listed below as ideas, not commitments.

Subject: [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
(`dsh`) at commit `d7432673`, MIT. Five analysts read the source and the
OpenBeast tree; nobody ran dsh. Project-health figures are from the GitHub
and npm APIs on 2026-10-09. Token figures are estimates from dsh's generated
tool catalog.

## What it is

- DeepSeek's agent harness: a TypeScript monorepo (357 workspace packages,
  1,721 locked dependencies, Node 22.19+) with a browser UI, a desktop app
  (macOS and Windows only), a one-shot CLI, and JSON-RPC and ACP servers. It
  has no terminal UI.
- Everything is a plugin on a vendored fork of Cordis; a profile is a stack of
  YAML rows and any row can be patched.
- Built for DeepSeek's hosted API. Other endpoints go through a third-party
  wrapper (`pi-ai`) that dsh patches.
- Developer preview: eight weeks old, version 0.2.1-alpha.2, a release about
  every two days, no stable version, session format on its fourth generation.
- Single operator: no accounts, roles or notion of a serving rig.

## Why not

| Reason | Evidence |
|---|---|
| It replaces almost nothing | No equivalent of the device gate, `/api/slot`, client mode, the RBAC tool server, beast-artifact or `serve.sh`. Open WebUI cannot go (dsh is single-user). OpenCode is the only plausible swap and dsh has no terminal UI. |
| The runner cannot be swapped for evals | A new harness is a new era; no board row stays comparable. No seed or `top_p` control, no step cap, no cancel in its SDK protocol. |
| Heavier on a local 27B | 25–31 default tools (about 4.9K–7.9K tokens) against our runner's 10 (about 1.9K). Its compaction summary and title generation each take the single slot. It fans out up to 8 subagents with no request limiter. |
| Our configuration is where it breaks | Its discussion #3157: a hard five-minute wall kills local turns, reproduced on llama.cpp with Qwen 3.8 27B at 262K context; users patch compiled files. Other open reports: sessions that cannot resume, empty tool-call ids that brick a session, compaction that never fires on a guessed context window. |
| No recourse | Issues are disabled, outside pull requests are not accepted, staff commented on 50 of 9,144 discussions. |
| Security process | One CVSS 9.4 CVE (CVE-2026-82533, fixed in about three days), no security policy, unanswered requests for a disclosure channel. Its sandbox restricts writes only. |
| Telemetry and air-gap | Session upload to DeepSeek's collector is on by default (off with `DSH_TELEMETRY_DISABLED`); default web search is DeepSeek's hosted one; install is unpinned `npx`. |

Integration would have been one config patch (a `pi-ai` route to the rig, an
MCP client row launching `agents/mcp_server.py`, a skills directory), at the
cost of a Node runtime in the offline bundle, a doubled tool surface unless
its built-ins were disabled, and RBAC collapsing to admin over stdio MCP.

## Ideas worth porting

All are harness-side, so they work with a model that skips optional tool
calls. "Era" marks changes to the hashed runner or tool files.

| # | Mechanism | Why | Era |
|---|---|---|---|
| 1 | Host wake on completion: write a finished agent's or job's summary into the parent's inbox | Removes the dependence on the model polling `check_agent` | No |
| 2 | Goal-round driver: an outer loop re-prompting with objective, round n of max, and on-disk plan files, with wall and token ceilings and re-arm after restart | The missing core of the 24/7 roadmap item | No |
| 3 | Fresh-context rounds: each round starts clean and carries a capped report file forward | Keeps rounds under 40K tokens, where the 27B is fastest | No |
| 4 | Spill oversized tool results to a private per-session file, path appended | Lower inline cap, nothing lost | Yes |
| 5 | A cgroup scope per command (`systemd-run --user --scope`), group kill as fallback | Closes the `setsid` escape; `MemoryMax`/`TasksMax` replace process-count arithmetic | Yes |
| 6 | Usage-anchored token estimate | Fixes compaction timing on logs and code | Yes |
| 7 | Read-before-edit and stale-file guard | Stops edits to unseen or changed files | Yes |
| 8 | Repeat-call reminder at 3, 5 and 8 identical calls | Cheap loop breaking | Yes |
| 9 | Log the exact request header and hash that for the era | Catches config drift a source hash misses | No |
| 10 | Skill catalog injected once per session, `/skill-name` as a deterministic load | Addresses the near-zero skill firing rate | Non-eval only |

Smaller: widen the environment scrub to a name pattern; a launch-token to
signed-cookie exchange for beast-chat; a per-turn changed-files card in the
console; a `docs/postmortem/` with a "why every safety net missed it" section;
a local-model failure checklist from their bug reports (empty or duplicate
tool-call ids, malformed arguments, guessed context windows).

## When to look again

A stable release, a published security reporting channel, and #3157 fixed in
core. Until all three hold, treat it as a source of ideas only.

## Not determined

Whether Qwen tool calls and reasoning parse correctly through its `pi-ai`
route; whether a local-provider install is silent on the network; real install
size; whether the public security reports still hold at this commit. The only
benchmarks found are vendor-adjacent and cloud-only.

## Sources

- Repository, docs, postmortems, upgrade guides and discussions at `d7432673`
- [Discussion #3157](https://github.com/deepseek-ai/deepseek-harness/discussions/3157)
- [OX Security on CVE-2026-82533](https://www.ox.security/blog/cve-2026-82533-deepseek-harness-ai-agent-sandbox-escape/), 2026-09-08
- [Winder.ai, DeepSeek Harness vs OpenCode](https://winder.ai/deepseek-harness-vs-opencode/), 2026-09-20 (practitioner opinion; ran local weights)
- [Composio, DeepSeek Harness vs Claude Code](https://composio.dev/content/deepseek-harness-vs-claude-code), 2026-09-01 (vendor benchmark, cloud only)
