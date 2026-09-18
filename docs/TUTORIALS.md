# Tutorials — the beast family, one walkthrough each

Every walkthrough below is copy-pasteable on a rig that has run
`./bootstrap.sh`. Each says what you should see, and each stops at the point
where the deeper document takes over. Nothing here is aspirational: every
command was run against the scripts as they ship in v1.5.0.

| Feature | Walkthrough | The full document |
|---|---|---|
| beast-assist 🔧 | [the compiler in the agent loop](#beast-assist--the-compiler-in-the-agent-loop) | [LANG_AWARENESS_PLAN.md](LANG_AWARENESS_PLAN.md) |
| beast-lang 📚 | [the offline language library](#beast-lang--the-offline-language-library) | [BEAST_LANG_PLAN.md](BEAST_LANG_PLAN.md) |
| beast-artifact 🎨 | [a URL for a page the model wrote](#beast-artifact--a-url-for-a-page-the-model-wrote) | [BEAST_ARTIFACT.md](BEAST_ARTIFACT.md) |
| beast-chat 📱 | [watch and steer the rig from a phone](#beast-chat--watch-and-steer-the-rig-from-a-phone) | [BEAST_CHAT.md](BEAST_CHAT.md) |
| beast-slot 🎰 | [a laptop that thinks on the rig](#beast-slot--a-laptop-that-thinks-on-the-rig) | [BEAST_SLOT.md](BEAST_SLOT.md) |
| beast-gate 🛡️ | [per-device keys for the inference port](#beast-gate--per-device-keys-for-the-inference-port) | [BEAST_SLOT.md § beast-gate](BEAST_SLOT.md) |
| Air-gap 🔌 | [install a rig that has no internet](#air-gap--install-a-rig-that-has-no-internet) | [INSTALL.md § Offline](INSTALL.md) |
| beast-campaign 🧪 | [measure something without lying to yourself](#beast-campaign--measure-something-without-lying-to-yourself) | [BEAST_CAMPAIGN_PLAN.md](BEAST_CAMPAIGN_PLAN.md) |

A convention used throughout: `openbeast.conf` keys are shown with `>>`, and
every change to it needs `./stop.sh && ./start.sh -d` to take effect unless
the walkthrough says otherwise.

---

## beast-assist — the compiler in the agent loop

**What you get:** every time an agent writes or edits a source file, the
language's real checker runs and its verdict is appended to the tool result.
The model finds out at write time that its stale-stdlib call does not
compile, instead of at the end.

```bash
echo 'BEAST_ASSIST=1' >> openbeast.conf
./stop.sh && ./start.sh -d
```

That is the whole setup. To see it work without waiting for a model, call the
primitive directly — this is exactly what the agent's `write_file` does:

```bash
mkdir -p /tmp/ba && cat > /tmp/ba/hello.zig <<'Z'
const std = @import("std");
pub fn main() !void {
    const out = std.io.getStdOut().writer();   // zig 0.11-0.15 idiom
    try out.print("hi\n", .{});
}
Z
BEAST_ASSIST=1 python3 -c "
import sys; sys.path.insert(0, 'agents'); import tools
print(tools._run_diagnostics('/tmp/ba/hello.zig'))"
```

You should see the block the model would see:

```
── diagnostics (zig) ──
/tmp/ba/hello.zig:3:21: error: root source file struct 'std' has no member named 'io'
…
hint: 'io' is not in std — did you mean Io?
fix: zig 0.16 has no std.io / std.fs.File: declare `pub fn main(init: std.process.Init) !void`, …
── 1 error ──
```

Three things to know:

- **Which languages:** zig (full Sema), rust, go (`go vet`), C/C++ (gcc/g++),
  python (`py_compile`), shell (`shellcheck`). A missing toolchain no-ops
  silently; a non-source file never touches a checker.
- **What it costs:** nothing when idle; when active, a checker run per write
  (typically 0.1–2 s, logged to `OPENBEAST_DIAG_TIMING_LOG` if set).
- **What it is worth:** measured, honestly, in
  [LANG_AWARENESS_PLAN.md](LANG_AWARENESS_PLAN.md): the mechanism replicates
  with zero regressions; the suite-level effect is small. It ships because it
  is free and harmless, and the default stays off until an arm measures a
  decisive effect.

Turn it off: `BEAST_ASSIST=0` (or remove the line) and restart.

---

## beast-lang — the offline language library

**What you get:** the installed toolchain becomes the ground truth for what a
language *has*, every migration claim in the corpus is compile-verified on
your box, and a model can ask (`language_reference`) or be told (the
escalation card) instead of guessing from its training cutoff.

### 1. See what the rig already knows

```bash
./scripts/lang-library.sh list      # the acquired corpus (L0), with provenance
./scripts/lang-library.sh verify    # compile every claim on THIS rig's toolchains
./scripts/lang-library.sh pack zig  # the ≤2k-token pack a model would receive
```

`verify` prints one line per claim and ends with a count like `VERIFIED=25`.
A claim is VERIFIED only if its OLD form **fails** and its NEW form
**compiles** here; anything else is labelled and not served.

### 2. Acquire the corpus (connected box, one-off)

```bash
./scripts/lang-library.sh acquire            # all wired languages
./scripts/lang-library.sh acquire python zig # or just these
./scripts/lang-library.sh check              # what is on disk vs its manifest
```

Every artifact lands under `../openbeast-lang-library/<lang>/` with a
provenance manifest (source URL resolved at fetch time, sha256, licence,
fetch date). Under `OFFLINE=true`, `acquire` refuses and the rest still works.

### 3. Ask it something

From chat or OpenCode (admin profile), the model has a `language_reference`
tool. From the shell, the same thing:

```bash
python3 -c "
import sys; sys.path.insert(0, 'agents'); import mcp_server as m
f = getattr(m.language_reference, 'fn', m.language_reference)
print(f('zig', error=\"main.zig:3:21: error: root source file struct 'std' has no member named 'io'\"))
print(f('python', topic='imp'))
print(f('cobol'))"
```

The first prints the confirmed card (`std.io` and `std.fs.File` are gone;
here is the 0.16 form). The second prints what the verified corpus says about
`imp`. The third says which languages this rig can serve and stops — **it
does not guess**; a miss is "no verified reference for …".

### 4. Let a model draft new claims (needs the GPU; the verifier decides)

```bash
./scripts/lang-synthesize.sh draft python --dry-run     # the prompts it WOULD send
OPENBEAST_LANG_SYNTH_URL=http://localhost:8080/v1 \
  ./scripts/gpu-lease.sh run "beast-lang P3 python" -- \
  ./scripts/lang-synthesize.sh draft python
./scripts/lang-synthesize.sh status                     # what awaits review
./scripts/lang-synthesize.sh promote <staging.json> --all
```

Drafts go through the real verifier and land in a **staging** file the
served packs never read; `promote` re-verifies and moves them. There is no
default endpoint on purpose, and it refuses while someone else holds the GPU
lease.

### 5. Which languages a deployment serves

`LANG_PACKS=auto` (the default) serves every language whose toolchain is
installed and whose claims verify; `LANG_PACKS=zig,python` narrows it;
`LANG_PACKS=off` disables packs. Inspect with `./scripts/lang-library.sh pack`.
The escalation card inside beast-assist (`BEAST_ESCALATE=1`) is the one part
still pending its A/B — see the plan's §10.

---

## beast-artifact — a URL for a page the model wrote

**What you get:** a report, a table, a dashboard or a small tool arrives as a
durable, versioned link on your tailnet instead of a wall of chat text.

```bash
echo 'BEAST_ARTIFACT=true' >> openbeast.conf
./stop.sh && ./start.sh -d
./scripts/setup-tailscale.sh --publish-artifact     # once; needs sudo
```

Publish something from the shell:

```bash
cat > /tmp/hello.html <<'H'
<title>Hello, beast</title>
<style>body{background:#111;color:#eee;font:16px system-ui;padding:2rem}</style>
<h1>It works</h1><p>Published from the shell.</p>
H
./scripts/artifact.sh publish /tmp/hello.html --title "Hello, beast"
```

```
Published "Hello, beast" → https://beast.tail1234.ts.net:8446/a/6f1c2a3e-…  (v1)
  id:    6f1c2a3e-…
```

Open that on your phone (the Tailscale app must be connected). Then:

```bash
./scripts/artifact.sh publish /tmp/hello.html --id <id> --label "second draft"   # same URL, v2
./scripts/artifact.sh versions <id>          # every version, which one is current
./scripts/artifact.sh rollback <id> 1        # move the URL back to v1
./scripts/artifact.sh visibility <id> tailnet   # let every tailnet login read it (default: only you)
./scripts/artifact.sh list
```

In chat, it is one sentence — *"write me a page comparing X and Y and
publish it"* — and the model answers with the link. Supporting files
(`--file app.js=dist/app.js`) are served next to the page. Until the port is
published, links are `http://localhost:3004/…` and the tool says so; a
browser cannot open the loopback viewer (it refuses anonymous callers).

What the page can and cannot do, and why it is treated as hostile:
[BEAST_ARTIFACT.md § The security posture](BEAST_ARTIFACT.md).

---

## beast-chat — watch and steer the rig from a phone

**What you get:** every agent and every long job on the rig becomes a
session with a live transcript you can attach to from a phone, send a message
to, and stop.

```bash
echo 'BEAST_CHAT=true'                 >> openbeast.conf
echo 'CHAT_OPERATORS=you@example.com'  >> openbeast.conf   # your tailnet login
./stop.sh && ./start.sh -d
./scripts/setup-tailscale.sh --publish-chat                # once; needs sudo
./scripts/clients.sh enroll phone --label "My phone" --scope chat
```

The last command prints a key **once** — paste it into the console on the
phone at `https://<rig>.<tailnet>.ts.net:8445` (reading needs only your
tailnet login; steering and stopping need the key). Add to Home Screen.

Now give it something to watch:

```bash
./scripts/job.sh run --title "long build" -- bash -c 'for i in $(seq 1 60); do echo tick $i; sleep 5; done'
./agent.sh "list the five largest files under /tmp and explain what they are"
```

Both appear in the console within a second. Attach to the agent, type
*"skip anything under 1 MB"* — it lands at the agent's next turn boundary,
and the transcript shows `steer` when it does. Stop the job from the phone;
the ledger records `stopped` and who did it.

From the rig, the same ledger:

```bash
./scripts/job.sh list                 # every job: state, age, title
./scripts/job.sh show <id>            # record + the last 20 log lines
./scripts/job.sh stop <id>            # SIGTERM the group, SIGKILL after 30 s
./scripts/doctor.sh | grep -i chat    # health row + auth posture
```

Sessions started from the console run in their own memory-capped scope, so
`./stop.sh` never takes them with the stack. What the stream guarantees
(reattach by byte offset, no gaps, no duplicates) and the auth model:
[BEAST_CHAT.md](BEAST_CHAT.md).

---

## beast-slot — a laptop that thinks on the rig

**What you get:** any Mac or Linux laptop runs OpenCode and the full 18-tool
arsenal against *its own* files; only inference crosses the tailnet.

On the rig, publish the two discovery surfaces once:

```bash
./scripts/setup-tailscale.sh --publish-slot --publish-searxng
```

On the laptop (Tailscale connected to the same tailnet):

```bash
git clone https://github.com/MaximilianKhan/openbeast && cd openbeast
./scripts/setup-client.sh --host beast.tail1234.ts.net
openbeast-client status
```

```
=== OpenBeast client status ===
  ✓ env file (~/.openbeast-client/env)
  ✓ venv imports mcp + openai
  ✓ tailscale up
  ✓ rig model API reachable (https://beast.tail1234.ts.net:8443/v1)
  …then the rig's actually-loaded model, context and busy slots from /api/slot
```

Then `opencode` in any project: `bash`, `grep` and file edits run on the
laptop; every token comes from the rig. `openbeast-client agent "…"` runs an
autonomous agent the same way. `--local-search` runs SearXNG on the laptop
instead; `openbeast-client uninstall` removes client mode.

Read the trust model before joining a rig you do not own — the rig's model
emits the tool calls your laptop executes: [BEAST_SLOT.md](BEAST_SLOT.md).

---

## beast-gate — per-device keys for the inference port

**What you get:** `:8443` stops being a straight map onto llama-server's whole
route table and becomes an identity-aware edge: per-device keys, an OpenAI
route allowlist, rate and in-flight caps, an inference audit trail.

```bash
echo 'EDGE_GATE=true' >> openbeast.conf
./stop.sh && ./start.sh -d
./scripts/clients.sh enroll laptop --label "Work laptop"     # prints the key once
./scripts/clients.sh list
```

On the laptop, hand the key to the client: `./scripts/setup-client.sh --host
<rig> --api-key <key>`. Revoke a device with `./scripts/clients.sh revoke
laptop`; the next request 401s. `./start.sh` prints the gate's auth mode
(`auth=closed` until the first device is enrolled), and
`.run/inference-audit.jsonl` records every inference call by device.

---

## Air-gap — install a rig that has no internet

**What you get:** a signed bundle built on a connected box that installs
everything on a rig that will never see the network.

On the connected box (same OS family and Python as the target):

```bash
./scripts/pydeps.sh verify                              # the lock matches requirements.txt
./scripts/bundle.sh build /media/usb/openbeast --with-weights
./scripts/bundle.sh sign  /media/usb/openbeast --key ~/.ssh/openbeast-bundle
./scripts/bundle.sh show  /media/usb/openbeast
```

On the rig:

```bash
# copy the checkout over (the bundle's source/ holds the llama.cpp tarball, not the repo)
./scripts/bundle.sh verify  /media/usb/openbeast --key allowed_signers --identity you@example.com
./scripts/bundle.sh install /media/usb/openbeast
echo 'OFFLINE=true' >> openbeast.conf
./bootstrap.sh
```

`verify` refuses a bundle whose manifest was rebuilt, not just one whose files
changed — hashes are integrity, the signature is authenticity. `OFFLINE=true`
makes every step that cannot succeed without the network refuse and say which,
instead of stalling on a connect timeout. `./scripts/doctor.sh` then reports
the rig's offline self-sufficiency. Details and what each step does
differently offline: [INSTALL.md § Offline](INSTALL.md).

---

## beast-campaign — measure something without lying to yourself

**What you get:** two small tools that make an eval row mean what it says.

```bash
./scripts/gpu-lease.sh status                    # FREE, or HELD by whom since when
./scripts/gpu-lease.sh run "my sweep" -- bash my-sweep.sh
./scripts/eval-era.sh                            # the hash of what an eval unit sees
./scripts/eval-era.sh --files                    # the six files behind it
./scripts/eval-era.sh --check 3b7c2adb8da7968d   # exit 1 if it moved
```

`run` holds the lease for as long as the command's whole process group lives,
forwards an operator's SIGTERM to the command, and releases when it is gone.
The watchdog and `./stop.sh` consult the lease before touching any
llama-server, so a measurement is never relaunched into or reaped by the
stack. A build agent that consults it does not compile inside a measurement's
window — the 2026-09-14 contamination is why this exists.

`eval-era.sh` names the era every result row carries. Rows from different eras
are never paired; a campaign runs `--check` before it spends a GPU-minute. The
shape of a campaign that uses both is `scratch/campaign_master3.sh`, and the
plan is [BEAST_CAMPAIGN_PLAN.md](BEAST_CAMPAIGN_PLAN.md).
