# Production Roadmap — from personal rig to community pillar

Consolidated from a production review + docs audit + skills/tools analysis
(2026-07-07, Fable 5). The engineering underneath is strong; what's missing
is the *outer shell* of a public project and some surface simplification.

## A. Production review of the recent RBAC + remote-access work

**RBAC (guest tier): SOUND — verified at source, runtime, and config.**
- The real resolution path (`utils/tools.py`, model `toolIds` → `server:N`)
  calls `has_connection_access` and skips denied connections *before*
  applying the function filter. `server:1` reliably maps to the connection
  whose `info.id=="1"` (via its enumerate `idx` → `connections[idx]`).
- Proven with a throwaway guest account against the live code: guest →
  `['web_search']`, owner → all 17. Account removed. 69/69 script tests green.

**Two documented caveats (not bugs, but know them):**
1. **Depends on `BYPASS_ADMIN_ACCESS_CONTROL=True`** (Open WebUI default).
   The privileged connection is admin-only via *empty* `access_grants`,
   which relies on admins bypassing grant checks. If someone sets that env
   to `False`, admins also get denied the privileged tools (there's no
   explicit admin grant). Fine at the default; document before anyone flips it.
2. **Process supervision gap.** `llama-server` and `mcpo` currently run as
   **bare processes** (not under `start.sh`, after a mid-session restart).
   Docker services have `restart: unless-stopped` and survive reboot; the
   two bare processes do NOT. For a durable deployment, run the stack under
   `start.sh` (foreground supervisor) or, better, add **systemd units** so
   llama-server + mcpo restart on crash/boot like the containers do.

**Regression found and FIXED this session:** `WEBUI_AUTH=true` (introduced
in the Tailscale rollout) broke the *fresh-install* path — `configure-webui.sh`
couldn't authenticate before an admin account existed, silently shipping a
tool-less WebUI. Fixed: auth now defaults **false** (local-only, no login
wall, auto-configures); `setup-tailscale.sh` flips it **true** when the
WebUI goes tailnet-wide. Max's live remote instance pinned `true` in conf.

**Also fixed:** stale `/home/max/Documents/models` paths (rename fallout) in
`wrap_up*.sh`, `SKILLS_PLAN.md`, `eval-task-author/SKILL.md`; README
quick-start now exports the CUDA PATH and auto-detects GPU arch.

## B. Skills ↔ tools — the confusion is structural (Max's instinct was right)

The MCP surface is **17 tools, and 9 of them (53%) are meta-machinery**:
- 6 core: `bash`, `read_file`, `write_file`, `edit_file`, `list_files`, `grep`
- 2 web: `fetch`, `web_search`
- **5 agent-mgmt**: `start_agent`, `check_agent`, `tail_agent`, `stop_agent`, `list_agents`
- **4 skills**: `list_skills`, `load_skill`, `start_skill_agent`, `reload_skills`

Plus **14 skills** the model can only reach by proactively chaining
`list_skills` → `load_skill` → maybe `start_skill_agent`. On a 27B local
model this indirection **fires ~0% of the time** (already measured — TODO
"Skills don't fire spontaneously"). Frontier models with huge context can
afford blind multi-step discovery; ours can't. That's the confusion.

**Recommendations (ordered):**
1. **Shrink the always-on surface via the RBAC connections we just built.**
   Expose a lean *core* profile (the 8 file/exec/web tools) to normal chats
   and put agent-mgmt + skills behind an *advanced* connection the model
   only sees when needed. The two-connection RBAC machinery already exists —
   this reuses it for cognitive-load reduction, not just security.
2. **Make skills discoverable without a tool round-trip.** ✅ **Index half
   DONE** — `scripts/generate-skill-index.py` injects the compact skill
   index (name + one-line + trigger) into `system-prompt-tools.md`, run by
   `configure-webui.sh`, with a staleness check in `tests/test_scripts.sh`
   (CI). **Still open:** collapsing the 4 blind-discovery tools down to ONE
   `load_skill` tool + the visible menu. (The endgame is the deferred
   SKILLS_PLAN Phase-5 auto-router: a pre-flight classifier picks the
   skill; the model never has to.)
3. **Sharpen the tool-vs-skill mental model** in the prompt: *tools = actions
   (do a thing now); skills = methodologies (how to approach a class of
   problem, invoked at the START of an open-ended task).*
4. **Prune/merge overlapping skills.** 14 is a lot for a local router; several
   overlap (`code-review`↔`security-audit`; `architecture-proposal`↔
   `spec-extraction`↔`api-design`). A leaner, sharper set routes better.
5. **Collapse the 5 agent-mgmt tools** toward fewer verbs (e.g. one `agent`
   tool with an action arg), reducing schema clutter for weak selectors.

## C. Docs / packaging — the community-pillar gaps (from the audit)

Highest impact-to-effort, ordered:
1. **Add `LICENSE`** (S/high) — ✅ DONE 2026-07-07 (commit 5fb14e6, Apache-2.0). Was: currently ABSENT. Legal blocker to any use or
   contribution. Nothing else matters until this exists. *Needs Max: pick one
   (MIT or Apache-2.0 recommended for max adoption).*
2. **`bootstrap.sh` — one-command install** (L/high) — ✅ DONE 2026-07-07 (commit 5fb14e6). Collapse the 8 fragile
   manual steps into one: detect GPU arch + CUDA PATH, build llama.cpp (skip
   if built), pip (venv on Debian / `--break-system-packages` on Arch),
   download the default weight (skip if present), pull images, start, then
   guide admin-account creation + re-run `configure-webui.sh`. Every piece
   already exists in-repo; just stitch them.
3. **"Tier 0: just chat" minimal path** (S/high) — ✅ DONE 2026-07-07 (commit 5fb14e6, `./bootstrap.sh --minimal`) — `llama-server` + one
   weight + `curl localhost:8080`, no Docker/MCPO/auth. Let a newcomer
   succeed in 10 minutes; the full stack becomes the opt-in upgrade.
4. **Fix the front-door quick-start** (S/high) — done for CUDA PATH + arch;
   still: collapse the duplicate README/INSTALL quick-starts to one source,
   add the `hf` PATH note and the searxng pull.
5. **README hook + value prop** (M/high) — lead with a one-line pitch, a
   screenshot/GIF, and a "vs Ollama / LM Studio / text-generation-webui"
   table featuring the real differentiators: measured-VRAM-tuned contexts,
   a 300+-unit multi-language eval leaderboard, one-command secure remote
   access (Tailscale), family RBAC, and the agent+skills arsenal.
6. **Reconcile contradictory eval numbers** (M/med) — ✅ swept 2026-07-08:
   current-state docs now cite v4 (137 base / 291 units, 31 variant'd);
   was: effective units cited as 313 / 323 / 223; variants 33 vs 13;
   difficulty splits differ; models "5" vs "9". Still open: generate the
   counts from the suite, don't hand-write.
7. **`.github/` — CI + templates + CONTRIBUTING** (M/med). Tests exist
   (`tests/run_tests.sh`); wire them to run on push. Add issue/PR templates.
8. **Publish the repo + `v1.0` tag** (S/med) — replace `<repo-url>`
   (now github.com/MaximilianKhan/openbeast), cut a version.

## D. Future horizon — "Mark of the Beast" (clustered nodes)

Max's idea (2026-07-07), kept in the back pocket: a **Latin-named
multi-node mode** — OpenBeast across clustered machines (distributed serving
/ multiple GPUs / a fleet of nodes under one tailnet). "Mark of the Beast" as
the codename direction. Natural extensions when it comes time: llama.cpp RPC
backend for multi-host tensor split, a tailnet-native model router across
nodes, and the RBAC/remote-access layer already generalizes to a fleet.
Revisit after the single-node stack is a polished pillar.
