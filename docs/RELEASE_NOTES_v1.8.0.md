# OpenBeast v1.8.0 — beast-island 🏝️, a gate for outside skills, and the 10-09 review

**The rig keeps thinking when the internet dies, outside skills come in only
through a gate, and about 170 review findings are closed.**

A power outage used to leave the rig half-alive even on a UPS:
- every `web_search` stalled on timeouts and the model retried;
- a boot with no internet hung while Open WebUI tried to update its
  embedding models;
- the watchdog restarted tailscaled every five minutes.

**beast-island** 🏝️ notices that the route is gone, tells the model so at
once, and keeps everything local serving. When the route comes back, so does
the rig, with no command.

Alongside it:
- **The remote-skill import gate** (`scripts/skill-import.sh`), with nine
  skills imported through it and kept off the always-on menu.
- **The 2026-10-09 review** of the whole repo.
- **The beast-rank paper**, published and then revised after a referee
  review and a numbers audit.

v1.7.0 → v1.8.0: 17 merged PRs (#127 – #146), 184 commits (160 non-merge),
337 files changed, +72,308 / −1,480 lines.

---

## ⚠️ Read before upgrading

1. **The eval era does NOT roll.** It stays `b5596c660b5ab819`: none of the
   six hashed files changed since v1.7.0. Rows from v1.6.0, v1.7.0 and v1.8.0
   pair legitimately.
2. **beast-island is on by default (`ISLAND=auto`).** If the rig has no route
   for `ISLAND_GRACE` seconds (90 by default), it islands. It rejoins after
   two good probes in a row.
   - `ISLAND=off` restores the old behaviour.
   - `ISLAND=force`, or `./start.sh -d --offline`, islands from launch.
3. **The imported skills are off the menu.** All nine set
   `prompt_index: false`: they cost the local model nothing and do not move
   the era. Nothing else about skills changes.
4. **One lock entry moves for a CVE:** `opentelemetry-api` 1.45.1 → 1.45.2
   (CVE-2026-126360), hashes from a fresh resolve. Nothing else in the lock
   moves.

Restart the stack after upgrading (`./stop.sh && ./start.sh -d`).

---

## beast-island 🏝️ — the rig keeps thinking when the internet dies (#144, #145, #146)

Named after grid *islanding*, where a microgrid disconnects and keeps
serving its local load from its own battery.

| Without | With |
|---|---|
| `web_search`/`fetch` stall on timeouts and the model retries | They answer at once: *ISLAND MODE … do not retry. Everything local still works* |
| A boot with no internet stalls on Open WebUI's embedding-model update | A launch with no route boots islanded: WebUI `OFFLINE_MODE`, compose `--pull never` |
| The watchdog marks Tailscale DOWN and restarts tailscaled every 5 min | Reported as *expected in island mode*; nothing restarted |

**How it decides.** A watcher probes IP literals, never hostnames, so a dead
DNS cannot fake a live route. The default probes are `1.1.1.1:443`,
`9.9.9.9:443` and `8.8.8.8:443`. It writes `.run/island.json`, mode 0600,
which everything else reads. `python3 agents/island.py status` shows the
state.

**The adversarial review (#145)** came in through four lenses: the state
machine, the shell wiring, security and integration, and docs against code.
Every finding was reproduced and fixed:
- An orphaned watcher no longer heartbeats forever and fights its successor.
  It exits when its parent is gone, and readers require a live
  `island.py`.
- A stop no longer resurrects the state file mid-probe.
- A bare IPv6 probe no longer parses to garbage that islanded healthy
  networks.
- The state file is treated as untrusted:
  - opened non-blocking and no-follow, regular files only;
  - read up to 64 KiB at most;
  - its `reason` field allow-listed;
  - `stale_after` capped at 600 s.
- `conf.sh` derives WebUI `OFFLINE_MODE` only from a live watcher, and now
  honours `OFFLINE=true` too.
- A failed init can no longer turn `--offline` into an online launch.

**Docs (#146):** [BEAST_ISLAND.md](BEAST_ISLAND.md). beast-island is also
taught everywhere an operator or agent looks: README, AGENTS, ARCHITECTURE,
FEATURES, INSTALL, EGRESS_PRIVACY and a TUTORIALS walk-through.

## Outside skills, through a gate (#127, #128)

A skill body goes straight into a sub-agent's system prompt, which makes a
remote skill a supply-chain input. `scripts/skill-import.sh` replaces the
manual review with four steps:
1. `fetch` a pinned commit and scan it;
2. `promote` it with a named reviewer;
3. `verify` the ledger against disk;
4. `attest` a human read.

- **It fails closed.** A missing or crashed scanner, an unrecognised report,
  or an incomplete analysis all refuse.
- **Findings are accepted by rule id, never by label.** The scanner labels
  even our own `security-audit` skill `DO_NOT_INSTALL`.
- **The ledger** (`skills/REMOTE_PROVENANCE.md`) carries a tree hash that
  doctor and the tests check.

Nine skills came in: five from obra/superpowers (MIT) and four from
anthropics/skills (Apache-2.0). One candidate, `web-artifacts-builder`, was
refused. An agent read them; the ledger says so until a human runs `attest`.

## The 2026-10-09 review (#134)

Eight independent reviewers covered network security, supply chain, ops,
eval integrity, docs, ease of use, performance, and tests and CI. They
produced about 170 findings. Eleven fixers worked on disjoint file sets and
verified each finding before fixing it. Highlights:

- **Security:**
  - The tool server (`:3001`) and the agent router pin `Host`. The router
    also refuses cross-site origins and spawns only from JSON bodies; both
    were reachable from a web page the operator visited.
  - Inference is never published keyless by accident.
  - Proxies take inline media only and are host-pinned.
- **Supply chain:** a pinned llama.cpp commit, and strictly hash-pinned
  installs.
- **Ops:** a RAM-sized prompt cache, `start.sh` preflights, and command words.
- **Evals:** an honest board that marks timeouts and partial rows
  (T/O, †/‡).

The fixes that would change the era are on a separate branch,
`eval/era-roll-v4.1`, and are not in this release.

## Research: the beast-rank paper (#132, #133, #136)

- **The paper:** `research/lowrank/paper/final/paper.pdf`, 24 pages, with
  full arXiv reference entries.
- **The findings:** `research/FINDINGS.md`, covering both programs
  (beast-rank and langaware) and what was not run.
- **Revision (#136):**
  - A **numbers audit** recomputed every load-bearing paired statistic from
    the per-chunk logs. Each matched to the printed digit, and 22 secondary
    errors were corrected.
  - A **referee review** (verdict: major revision) let the negative results
    stand and reworded the headline positives to match their evidence. The
    experiments it asked for are listed in `research/lowrank/TODO.md` and
    stated as limitations.
- **Formats:** a LaTeX export alongside the Typst build.

## Dependencies

- fastapi 0.146.0, openai 3.35.1, mcp 2.3.0 (#129 – #131, #139, #140).
- CI actions bumped (#137, #138).
- The CI audit tools were relocked for certifi PYSEC-2026-4320 (#142).
- opentelemetry-api 1.45.2 for CVE-2026-126360.

## Known open items

- **The nine imported skills await a human `attest`.**
- **The era-changing review fixes** (`eval/era-roll-v4.1`) need their own
  release and a re-measure.
- **beast-island probes IP literals only.** A network that blocks all three
  default probes reads as "no internet": set `ISLAND_PROBES` to targets your
  network allows.
