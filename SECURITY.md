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

## Supported versions

Pre-1.0-public: only the latest `main` is supported. Once the repo is
public and tagged releases exist, the latest release plus `main`.
