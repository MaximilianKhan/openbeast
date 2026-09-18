# OpenBeast v1.5.0 — beast-lang 📚, the air-gap path 🔌, and a rig that measures itself 🧪

**The compiler gets a vote, the network cable becomes optional, and the
measurement stack stops trusting itself.**

v1.2.0 put the compiler *after* the model (a checker verdict on every write).
v1.5.0 puts it *before*: a language library where nothing is served that the
toolchain installed on this box did not confirm. Alongside it, the install path
that works from a USB stick, and the two small tools that make an eval row mean
what it says. Underneath, a full review of v1.3/v1.4 with the fixes it earned.

---

## beast-lang 📚 — the offline language library

A local model's knowledge of a language froze at its training cutoff; the
compiler on your rig did not. The suite's most expensive failure class is
exactly that gap (zig 0.16 has no `std.io`; the model was trained when it did).

beast-lang is four layers, and the rule that ties them together is **L1: the
installed toolchain is ground truth.**

| Layer | What it is | Command |
|---|---|---|
| **L0 corpus** | each language's reference documentation, acquired for offline use, with provenance (source URL, sha256, licence) | `scripts/lang-library.sh acquire` |
| **L1 introspection** | the toolchain asked what it *has*: zig's real `std` names, which C++ feature macros appear at which `-std=`, Python's stdlib module list, Go's package list, which Rust editions rustc accepts | `scripts/lang-introspect.sh` |
| **The verifier** | every migration claim compiled both ways on this rig — the OLD form must fail, the NEW form must compile — or it is not served | `scripts/lang-library.sh verify` |
| **Escalation** | a generated index from OLD fixtures' *real* diagnostics: a compile error selects the card that fixes it, and only when the identifiers on the line agree | `python3 agents/lang/escalate.py --check` |

Six languages: zig, C, C++, Python, Rust, Go. Two ways a model reaches it:

- **`language_reference(language, topic, error)`** — a new tool on the
  MCP/WebUI surface (admin profile; 17 → 18 tools). It answers from the
  verified corpus and **refuses to guess**: a miss says "no verified reference
  for …", an unknown language lists what this rig can serve.
- **Escalation inside beast-assist** — when the checker reports an error whose
  shape the index knows, the toolchain-confirmed fix rides in the same tool
  result. Opt-in (`BEAST_ESCALATE=1`), byte-identical off, and **held for its
  own A/B** (draft #90): it edits an eval-hashed file, so it lands at a
  campaign boundary and gets measured before its default moves.

**Phase 3 — synthesis** — is built and waiting for the GPU: a model drafts
candidate claims from the corpus, the *same* verifier accepts or rejects them
(model-written snippets are hostile input; the drivers carry a refusal scan,
scrubbed environments, process-group timeouts and memory caps), and accepted
claims land in a staging file that a separate `promote` step reviews. Nothing
model-written can reach a pack unverified. `scripts/lang-synthesize.sh`.

The matcher was measured before it shipped, and measured again by an
adversarial pass that found it attaching wrong cards to nine real zig
diagnostics; it now selects **nothing** when identifiers disagree, and the
index declares the fixtures it cannot reach. A false "confirmed" hint is worse
than none. → [`docs/BEAST_LANG_PLAN.md`](BEAST_LANG_PLAN.md)

## The air-gap path 🔌 — build it connected, install it from a stick

A closed-network review counted 114 places an install could break offline and
14 that fail to boot. An *installed* rig always served fine offline; installing
was the problem. Three features close it:

- **`OFFLINE=true`** in `openbeast.conf`: every install and update step that
  cannot succeed without the network **refuses instead of stalling**, and says
  which step and why.
- **A hash-pinned Python closure.** `agents/requirements.lock` pins every
  package and every wheel hash; bootstrap and CI install it with
  `--require-hashes`. `scripts/pydeps.sh` locks, verifies, builds a
  wheelhouse and installs from one. A genuine hash mismatch is now fatal — it
  used to fall back to an unpinned install, which is the one thing the lock
  exists to prevent — and a stale lock is refused rather than installing the
  old versions under a green check.
- **The signed bundle.** `scripts/bundle.sh build --with-weights` packs the
  container images (by content ID, which survives `docker save/load`), the
  wheelhouse, the weights and the source; `sign` signs the manifest with any
  ssh key; `verify --key` refuses a tampered one; `install` places everything,
  verifying each file before it lands. **Hashes are integrity; a signature is
  authenticity** — a rebuilt manifest passes hash verification with a
  malicious payload, which is why `sign` exists.

Also new: `scripts/fetch-weight.sh <name>` downloads one registry weight into a
staging directory and verifies it before it takes its final name (it could
previously overwrite a *different* weight whose remote filename collided).
→ [`docs/INSTALL.md`](INSTALL.md)

## beast-campaign 🧪 — measurement you can trust

Two tools, each born of a lost GPU-day:

- **`scripts/gpu-lease.sh`** — an advisory lease on the card (pid + start
  time, never pid alone). A campaign takes it; a build agent that consults it
  does not start compiling inside a measurement's window; the watchdog will
  not relaunch the stack's model into someone else's run, and `./stop.sh`
  will not reap it. `run` forwards SIGTERM to the command and keeps the lease
  until the command's whole process group is gone.
- **`scripts/eval-era.sh`** — the hash of the six files that define what an
  eval unit sees. Every result row carries it; rows from different eras are
  never paired. `--check <hash>` is what a campaign runs before it spends a
  GPU-minute.

→ [`docs/BEAST_CAMPAIGN_PLAN.md`](BEAST_CAMPAIGN_PLAN.md)

## The review 🔎 — what a real browser, a real SIGTERM and a real systemd unit found

A fresh read of v1.3/v1.4 after a 152-agent header-level pass, done the way a
user would use it. Everything below is fixed with a test that fails on the old
code:

- **beast-artifact: no supporting file ever loaded in a browser** (the
  sandbox's opaque origin made a page's own files cross-site, and
  `CORP: same-origin` refused them; `script-src` had no `'self'`). Fixed with a
  capability path, verified end to end in headless Chromium.
- **beast-artifact: every URL the model handed out was dead** — built from the
  OS hostname, not the tailnet name the certificate is issued for. Now read
  from `tailscale serve`, with an honest note when the port is not published.
- **The watchdog could shoot a loading model** and take the whole stack down
  with it; it killed by the bare name `llama-server`, which also reaps
  campaigns. Loading is now bounded-but-not-down; kills are by recorded pid or
  repo path only.
- **Stale pidfiles killed strangers** after a reboot (`stop.sh`, the watchdog).
  Every signal is identity-checked now (`scripts/lib/proc.sh`).
- **beast-chat** ignored SIGTERM while a phone was attached; a second start
  overwrote the live server's token; console-started jobs died on any
  `./stop.sh` and shared llama-server's memory cap (own scope, own cap now);
  an operator stop could be filed as `lost`.
- **`bundle.sh --key ""`** silently downgraded to unsigned; **bootstrap** fell
  back to unpinned on a hash mismatch; **`OFFLINE="true" # comment`** failed
  open; **every `/agents` Dependabot PR was permanently red** (the lock is now
  regenerated on the PR branch, and `scripts/land-dependabot.sh` lands them).
- **`healthcheck.sh` aborted whenever tailscaled was down** — the one case that
  block exists for — and a `set -u` slip of our own reached main for twenty
  minutes before the post-merge smoke test caught it. The suite now runs
  `healthcheck.sh` end to end.

Test count: 1041 → 1224 pytest, plus five standalone shell suites.

## Also in this release

- **The `beast-lang` skill** for cloud models working in this repo, and a
  `prompt_index: false` opt-out so a skill can exist without rolling the eval
  era by entering the always-on prompt menu (14 → 16 skills).
- `./start.sh doctor` notices an expiring certificate, `--preflight` no longer
  claims readiness it cannot know, eval rows record which llama.cpp produced
  them, and fast boot can no longer kill the stack.
- Dependencies: pyjwt 2.14, openai 3.13, uvicorn 0.53.

## Upgrading

```bash
git pull
./bootstrap.sh          # installs the hash-pinned closure; idempotent
./stop.sh && ./start.sh -d
```

Nothing new is on by default. `BEAST_CHAT`, `BEAST_ARTIFACT`, `BEAST_ASSIST`,
`OFFLINE` and `BEAST_ESCALATE` (pending #90) are all opt-in; a rig that leaves
them off behaves exactly as v1.4.0 did — minus the bugs above.

**Eval era:** unchanged at `3b7c2adb8da7968d`. None of this release touches
the six files that define what an eval unit sees; rows measured under v1.4.0
remain comparable. (Draft #90 is held precisely because it would.)
