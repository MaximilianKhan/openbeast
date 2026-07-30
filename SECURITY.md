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

## Supported versions

The latest tagged release plus `main`. (v1.0 went public 2026-07-13; the
pre-public "`main` only" policy no longer applies.)
