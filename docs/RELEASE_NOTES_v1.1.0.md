# OpenBeast v1.1.0 — beast-slot 🎰

**Your rig's intelligence, on every device you own.**

OpenBeast has always been about one thing: fill your hardware with the largest,
most capable model that fits, and never compromise. v1.1.0 makes that
intelligence portable without moving a single weight file.

The rig stays the fully-armed command center. It now *additionally* publishes
**beast-slot**: an inference endpoint plus a discovery API on your private
tailnet, which any Mac or Linux machine can consume while running the complete
OpenBeast tool arsenal **locally**.

The load-bearing fact: **tools execute where the process runs, not where the
model runs.** Your laptop runs `bash`, `grep` and file edits against its own
disk; only the thinking crosses the tailnet.

95 commits, 134 files, +15,708/−1,718 since v1.0.

---

## Install, in two roles

```bash
# the rig (Linux + NVIDIA)
./bootstrap.sh

# a client: any Mac or Linux laptop, no GPU, no weights
./scripts/setup-client.sh --host <rig>.<tailnet>.ts.net
openbeast-client status
```

You don't need both. You can install **only** the client if someone else hosts
the rig — a household with one GPU box, a small team, a friend with a spare
slot. See [`docs/BEAST_SLOT.md`](BEAST_SLOT.md), and read the trust model there
before joining a rig you don't own.

## beast-slot: client/server

- **`scripts/setup-client.sh`** turns any macOS or Linux machine into an
  OpenBeast client: isolated venv, slim checkout, non-clobbering OpenCode config
  merge, full uninstall.
- **`openbeast-client` CLI** with `status` (client doctor plus a live view of
  what the rig is actually serving), `agent` (autonomous agent on your laptop's
  files, thinking on the rig's GPU), `search up|down`, `update`, `uninstall`.
- **`/api/slot` discovery contract v2.** llama-server ignores the model name you
  request; beast-slot tells you the truth: the loaded model, its context window,
  slot availability, capacity, service health. Published opt-in via
  `setup-tailscale.sh --publish-slot`, which mounts *only* that path. The
  contract is slot-count-agnostic, so a future multi-slot profile needs no
  client change.
- **`--local-search`** runs SearXNG on the client instead of using the rig's.
- **Real API-key support.** `LLAMA_API_KEY` existed before but nothing sent it.
  The whole stack now presents the bearer when set: WebUI, healthcheck, agent
  runner, eval harness, router, dashboard, clients. Keys are endpoint-scoped, so
  an ambient key never travels to an arbitrary host a model might name.

## beast-gate: identity on the inference path

Sharing a rig exposes something most local-AI setups ignore: **llama.cpp has no
concept of a user.** One flat key, no per-caller accounting, opportunistic slot
assignment, an unbounded queue with no timeout or preemption. Publishing it puts
its *entire* route table on your network, including global model mutation and
the ability to read or cancel someone else's generation.

`agents/edge.py` is the missing hop. Opt-in (`EDGE_GATE=true`), off by default,
and it does not touch your local stack.

- **Per-device bearer keys**, hot-reloaded. Revoke a lost laptop and the next
  request from it fails within seconds, with no llama-server restart, so your KV
  cache and every live conversation survive.
- **A path allowlist.** Remote callers see the OpenAI routes and nothing else;
  `/lora-adapters`, `/slots`, `/props` return 404 rather than 403, so a caller
  learns nothing about what exists.
- **Tenancy hardening.** Client-supplied `id_slot` (unauthenticated upstream and
  a queue-jumping primitive) is stripped and re-injected only from the
  server-side map. Conversation ids are namespaced per device.
- **Admission control** the model server cannot provide: token bucket plus a
  concurrent-generation cap per device.
- **An audit trail worth the name.** One row per completion with a `request_id`
  echoed to the caller, real token usage, and an enrollment-stable `device_uid`
  so re-enrolling a name doesn't inherit its predecessor's history.

```bash
./scripts/clients.sh enroll laptop-air --label "MacBook Air"
./scripts/clients.sh revoke laptop-air
```

**If you are hosting for anyone but yourself, turn beast-gate on before you
publish.** Without it, `:8443` is raw llama-server.

## Model governance

- **Weight-registry enforcement.** Images are digest-pinned and Python deps
  hash-pinned; model weights were the one shipped artifact whose pin nothing
  checked at load time. `serve.sh` now validates the GGUF against
  `weights.registry`. `WEIGHT_ENFORCE=warn` by default, `strict` to enforce, and
  a strict refusal exits with a distinct code so model-rollback won't quietly
  substitute a different model.
- **Eval quality gate.** `doctor` warns when the model you made the default has
  no leaderboard row *on this host*. Promotion by evidence.

## Platform

- **Extension system** for hot-pluggable optional services, with a **status
  dashboard** as the first extension.
- **Fast-first-chat boot** plus **model-load rollback**.
- **Adaptive context**: `serve.sh` scales `-c` to the card instead of guessing.
- **Per-model reasoning config** to tame over-reasoning tunes.
- **`scripts/ssd-wear.sh`** reads SMART read-only and reports GB-written/day and
  projected days to 100% wear, resolving the drives actually backing your
  weights through `lsblk --inverse` so LUKS and LVM layouts work. It warns
  rather than staying silent when it cannot read SMART.
- **`openbeast doctor`**: a running-stack health, security and consistency
  report.

## Models and evals

Heretic v2 27B (two MTP variants, the fastest MTP in the lineup),
Fable-Fusion-711, NVFP4 models, and **scoring v2**, which ranks by capability
(problem-solving weighted with language breadth) rather than flat accuracy. True
sustained-decode throughput replaced tokens-per-wall-clock, and MTP draft depth
is profiled per model.

## Fixes worth knowing about

- **macOS tool calls actually work now.** `resource.prlimit` is Linux-only, so
  on a Mac every `bash`/`grep` tool call failed *and* leaked a child process.
  This is why the previous thin-client mode was never usable.
- **Open WebUI configuration no longer fails silently.** `configure-webui.sh`
  exited early whenever the admin sign-in failed, which happens permanently once
  you change your WebUI password. Everything downstream is written straight to
  the database and needs no token, so switching your default model left it with
  no model entry, no attached tools, and no way to make a tool call. It now
  degrades instead of bailing, and reconciles again after a fast-boot swap or a
  model-load rollback.
- **Open WebUI's built-in Web Search is wired to SearXNG.** It shipped disabled
  with no engine on every install, despite SearXNG running right there.
- **`fetch()` versus Tailscale, pinned.** Python changed its classification of
  CGNAT addresses across versions. Now pinned across interpreters *and* address
  families, including IPv4-in-IPv6 forms, opt-in together via
  `FETCH_ALLOW_TAILNET`.
- **`openbeast-client update`** pulls the checkout you are actually running
  from, instead of reporting success while doing nothing.
- **Client diagnostics tell you which failure you have.** A revoked device, a
  rig that is down, a rig still loading, and a TLS or clock problem used to
  print the same wrong message.
- **Wrong-machine off-ramps.** `bootstrap.sh` points macOS and GPU-less
  machines at client mode instead of failing on a missing C toolchain, or
  building llama.cpp and downloading ~20 GB for CPU-only inference.

## Security model, stated plainly

- Everything binds `127.0.0.1`. The tailnet is the perimeter, never the public
  internet. `tailscale funnel` is deliberately never used.
- beast-slot access grants **inference, not the rig's filesystem**. Tools
  execute in frontends on the calling machine, never inside llama-server.
- The data-flow promise is **"nothing leaves your tailnet"**, not "nothing
  leaves this machine". File contents your agent reads travel to the rig as
  model context, machine to machine over WireGuard.
- **Joining a rig you do not own is a shell-level trust decision.** The rig
  chooses your agent's tool calls, and neither `openbeast-client agent` nor
  OpenCode prompts before running one. Point a client at a rig only if you would
  give that person a shell. `docs/BEAST_SLOT.md` documents this and how to
  shrink the blast radius.

## How this was reviewed

Every feature went through adversarial review: independent reviewers hunting
defects, then a second pass whose job was to *refute* each finding before it
counted. Six rounds across the release.

The pattern worth reporting honestly is that the most valuable findings were
consistently in code written **as a fix**. A peer-address check that looked
correct and passed its tests protected nothing, because the documented
deployment puts a reverse proxy in front of it. A safety claim in the docs was
disproved by a reviewer who stood up a hostile rig and had it read a canary file
off the client. A crash diagnosis committed to the docs named the wrong
mechanism twice before the kernel log settled it. All corrected, in the open,
with the reasoning recorded in `docs/TODO.md`.

## Known issues

- **Intermittent llama-server abort under very long generations** (Xid 8, the
  NVIDIA RC watchdog). Environmental rather than a llama.cpp defect, upstream on
  `nvidia-open`, observed once in 2,323 tasks over 14 days. The supervisor
  recovers in about 8 seconds and you lose the in-flight turn. Mitigation and
  full analysis in `docs/TODO.md`.
- Windows and WSL2 are untested rather than unsupported.

## Upgrade

```bash
git pull && ./stop.sh && ./start.sh -d                          # rig
./scripts/setup-tailscale.sh --publish-searxng --publish-slot   # optional
./scripts/setup-client.sh                                       # each client
```

Full documentation: [`docs/BEAST_SLOT.md`](BEAST_SLOT.md)

---
*OpenBeast: maximum intelligence per hardware, no compromise. Apache-2.0.*
