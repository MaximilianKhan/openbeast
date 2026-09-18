# Security Policy

OpenBeast is a self-hosted AI workstation that deliberately gives a language
model shell, filesystem, and network tools. Security is a design pillar, not
an afterthought — and reports are very welcome.

## Reporting a vulnerability

**Please do not open a public issue for security reports.**

- Use GitHub's **private vulnerability reporting** ("Report a vulnerability"
  under the Security tab), or
- Email the maintainer (address on the GitHub profile) with `[openbeast
  security]` in the subject.

Include: what you found, a reproduction, and the impact as you understand
it. You'll get an acknowledgment within a few days; fixes for confirmed
issues are prioritized ahead of all feature work.

## Scope — what counts

The interesting attack surfaces, in rough priority order:

1. **RBAC / profile-key bypass** — a guest-keyed or unauthenticated caller
   reaching admin tools on the identity tool server (:3001), or WebUI
   grant-filter escapes.
2. **Identity forgery** — defeating the signed-JWT identity mode, or shard
   escape (one user reading/writing another user's workspace shard through
   the tool layer's *intended* paths).
3. **SSRF / fetch-guard bypass** — `fetch()` reaching loopback, private
   ranges, or metadata services (DNS rebinding is a *known, documented*
   limitation — bypasses beyond it still qualify).
4. **Write-guard bypass** — `write_file`/`edit_file` reaching protected
   credential/persistence paths (`.ssh`, `.gnupg`, shell rc files, ...)
   through symlinks, races, or encoding tricks.
5. **Container escape or privilege escalation** in the shipped compose
   posture (cap-dropped searxng, no-new-privileges).
6. **Supply chain** — the pinned deps, the update path, bootstrap.
7. **beast-gate bypass** (`agents/edge.py`, the inference edge, opt-in
   `EDGE_GATE=true`) — reaching an inference endpoint without an enrolled
   device key; escaping the **path allowlist** to touch a route that should
   404 for remote callers (`/lora-adapters`, `/slots`, `/props`,
   `/v1/stream/<id>`, `/infill`); getting a client-supplied `id_slot` or
   identity header through to llama-server; escaping the per-device
   `X-Conversation-Id` namespace to read or cancel another device's stream;
   defeating the per-device rate / in-flight caps; or forging, suppressing,
   or unboundedly growing `.run/inference-audit.jsonl`. A revoked device
   still being served counts, as does a device key leaking into logs,
   metrics labels, or upstream requests.
8. **beast-chat** (`agents/chat_server.py`, opt-in `BEAST_CHAT=true`,
   published at `:8445`) — the one published surface that accepts **writes**,
   and `POST /api/chat/sessions` starts an agent, which is remote code
   execution on the rig. Qualifying: reading a session, transcript or the
   health detail without an identity (tailnet login, chat-scoped device key,
   or the 0600 `.run/chat-local.token`); a login not on `CHAT_OPERATORS`
   reading anything; a write (say / stop / start) with no key, a revoked key
   or one lacking the `chat` scope; any response that distinguishes those
   cases (the uniform 404 exists to close a membership oracle); a forged
   `pid_start` or `cursor` in session `meta`; steering reaching an eval unit
   (`OPENBEAST_EVAL` / `OPENBEAST_TASK_PATHS` set, or no `--steer` on argv);
   a `Host` value outside the trusted-host allowlist reaching a route (DNS
   rebinding); or message text appearing in `.run/chat-audit.jsonl`.
9. **beast-artifact** (`agents/artifact_server.py`, opt-in
   `BEAST_ARTIFACT=true`, published at `:8446`) — the first published
   surface whose *content* is untrusted. Qualifying: a page escaping the
   opaque-origin `sandbox` iframe or the CSP on `/raw/` responses (storage,
   `fetch`, downloads, reaching the shell that frames it, a script from a
   non-pinned origin); a write (publish, rollback, visibility, remove)
   arriving from anywhere but loopback with the locality token; a login not
   on `ARTIFACT_OPERATORS` (or `CHAT_OPERATORS` as its fallback) reading a
   page, or a private page reaching a login other than its publisher; the
   supporting-file capability token (`/raw/<id>/v/<n>/~<token>/`, an HMAC
   keyed by `.run/artifact-raw.key`) being derivable by a page, or being
   accepted as an *identity*.
10. **The offline bundle** (`scripts/bundle.sh`) — `install` using any file
    whose sha256 is not what `MANIFEST.json` records; `verify --key`
    accepting a manifest whose signature does not verify against the
    allowed-signers file for the stated namespace (`openbeast-bundle`);
    `install` treating an *unsigned* bundle as anything but "unsigned, said
    out loud"; a bundled wheel or weight that the repo-side lock or
    `weights.registry` would reject being installed anyway; or a source
    archive with symlinks extracting outside `llama.cpp/`.

Out of scope: attacks requiring the attacker to already BE the admin Unix
user; the model "misbehaving" within the permissions it was legitimately
granted (that's the sandboxing roadmap, not a vulnerability); denial of
service against your own box.

## Threat model, briefly

Default deployment is loopback-only with Tailscale as the remote boundary
and per-profile keys + signed identity as the in-stack boundaries. The
kernel-level sandbox (Landlock/seccomp via Sandlock) is opt-in and
documented in `docs/SANDBOXING.md`; `docs/RBAC_PLAN.md` documents the
authorization model and its history honestly, including known gaps.

**Two identity layers, on different axes.** The `:3001` identity tool server
authenticates the **human** (WebUI user → RBAC tier → per-user file shard →
tool audit). beast-gate on `:8090` authenticates the **device** (enrolled key
→ rate limits → inference audit). A device key is not a WebUI role and grants
no tool access; a WebUI login is not a device key. Before beast-gate the
inference path had **no identity at all** — every user, laptop, and spawned
agent collapsed into one anonymous caller, because llama.cpp itself has no
concept of a user.

**Stated plainly, because it is the most common misreading:** publishing
`:8443` without the gate (`EDGE_GATE=false`, the default) exposes
llama-server's *entire* route table to the tailnet — not just chat. That is a
deliberate, documented default for a tailnet you fully own, and the tailnet
is the perimeter (`tailscale funnel` is never used, anywhere). It is **not**
a suitable posture for a tailnet containing devices or people you don't
control; that is what `EDGE_GATE=true` plus per-device enrollment is for. See
[`docs/BEAST_SLOT.md`](docs/BEAST_SLOT.md).

**Remote clients run their own tool stack.** An OpenBeast client executes
files, shell, and agents on the *client's* machine; the rig only generates
tokens. RBAC deliberately does not apply to that path (it is a single-user
device). The consequence worth internalizing: file contents a client agent
reads travel to the rig as model context, so the promise is *"nothing leaves
your tailnet"*, not *"nothing leaves this machine"*.

**Two more published surfaces, each with a third identity rule.** beast-chat
(`:8445`) and beast-artifact (`:8446`) are opt-in, bind loopback, are
published separately from `:8443`, and are deliberately *not* behind
beast-gate (the gate is inference-shaped; teaching it a per-path upstream
map for one consumer is more risk than a second port). Reads on both need a
tailnet identity their operator list allows — an unlisted login gets 404,
never 403. Anything that **changes state** needs more: on beast-artifact,
writes never leave loopback (the CLI proves locality with a 0600 token no
browser can read); on beast-chat, a write needs an enrolled device key
carrying the `chat` scope, because a proxy-injected login header is forgeable
by anything already on the box and starting an agent is a shell. Model-
authored pages are treated as hostile and render in an opaque-origin sandbox
under CSP. Details: [`docs/BEAST_CHAT.md`](docs/BEAST_CHAT.md),
[`docs/BEAST_ARTIFACT.md`](docs/BEAST_ARTIFACT.md).

**Supply chain, and what a hash does not prove.** Container images are
digest-pinned, every model weight is sha256-pinned in `scripts/weights.registry`,
and the Python closure is hash-pinned in `agents/requirements.lock`
(installed with `--require-hashes`; bootstrap never falls back to the
unpinned file on a hash mismatch). The offline bundle (`scripts/bundle.sh`)
carries all of that across an air gap with every file's sha256 in
`MANIFEST.json` — which proves **integrity** (the bundle did not change in
transit) and nothing about **authenticity**: whoever can write to the stick
can rebuild the manifest around their own payload and every hash then
verifies. That is why `sign` / `verify --key` exist (ssh-keygen -Y against an
operator-supplied allowed-signers file; no key material in the repo). Wheels
and weights have a second, repo-side check that a rebuilt manifest cannot
touch; the images and the llama.cpp source have only the signature, so an
unsigned bundle is accepted but *said* to be unsigned rather than trusted
silently.

## Supported versions

The latest tagged release plus `main`. (v1.0 went public 2026-07-13; the
pre-public "`main` only" policy no longer applies.)
