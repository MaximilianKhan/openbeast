# Remote Access Plan — OpenBeast as a personal cloud

**Status: LIVE (2026-07-07) — implemented, deployed, and verified.**
Phases 1–2 live in `scripts/setup-tailscale.sh` (one script covers both),
Phase 3 (bind surface) via `scripts/lib/conf.sh` + `BIND_HOST`, Phase 4
polish in `scripts/healthcheck.sh`, README "Remote access", and INSTALL.md
§7 (the canonical setup walkthrough). Deployed to the tailnet as `beast`;
serve config and HTTPS certs confirmed working.

Decisions taken (the "open questions" below, answered 2026-07-07):
hostname = `beast`; no API key for now (tailnet device identity is the
boundary; `LLAMA_API_KEY` stays wired but off).

Field note from first deployment: `tailscale serve --https` blocks silently
until **MagicDNS + HTTPS Certificates** are enabled on the tailnet (admin
console → DNS). The setup script now pre-checks both, prints the exact
link, and waits visibly — don't reintroduce output-swallowing there.

Goal: reach the OpenBeast stack (chat UI, OpenAI-compatible API, tools) from
any device, at home or away, with real security — a local-first cloud
replacement. Phone on cellular in another country should open the WebUI as
easily as the desktop does.

---

## Where we are today (measured 2026-07-07)

| Service | Port | Binds | Auth |
|---|---|---|---|
| llama-server | 8080 | `0.0.0.0` | none |
| Open WebUI | 3000 | host network | `WEBUI_AUTH=false` |
| MCPO tools | 3001 | `0.0.0.0` | none |
| SearXNG | 8888 | `0.0.0.0` | none |

Everything is LAN-open with no authentication. Fine for a trusted home
network; catastrophic if a port ever gets forwarded. This plan adds remote
access *and* an actual security boundary at the same time.

---

## Technology decision

**Verdict: Tailscale now, with the config kept Headscale-compatible as the
fully-open-source escape hatch.**

| Option | Data plane | Control plane | Ops burden | Why / why not |
|---|---|---|---|---|
| **Tailscale** ✅ | WireGuard (E2E, keys never leave devices) | Proprietary SaaS (free: 3 users / 100 devices) | ~zero | MagicDNS names, automatic HTTPS certs via `tailscale serve`, NAT traversal that works on CGNAT cellular, first-class phone apps. 10-minute setup. |
| Headscale | WireGuard (same clients) | **Fully open source, self-hosted** | Needs a public VPS/domain for the coordination endpoint | The escape hatch: the *same* Tailscale clients re-point with `tailscale up --login-server https://…`. Adopt later if Tailscale's terms ever sour — nothing in this plan locks us in. |
| NetBird | WireGuard | Fully open source, self-hosted | Heaviest (management + signal + relay + IdP) | Great project, but self-hosting 4 services to avoid one SaaS control plane isn't worth it yet. |
| Plain WireGuard | WireGuard | none (static configs) | Manual keys/IPs per device; no NAT traversal without port-forward | Phones on CGNAT can't reach home without a forwarded port — exactly what we don't want. No DNS names. |
| ZeroTier | Custom protocol | BSL-licensed | low | Not WireGuard, license is not open source. |

Key honesty point: Tailscale's *coordination server* is proprietary, but the
traffic itself is end-to-end WireGuard — Tailscale-the-company never holds
private keys and cannot read tunnel traffic. Combined with the documented
Headscale migration path, this is the pragmatic open-adjacent choice.

---

## Target architecture

```
  phone (cellular) ──┐                        ┌─ https://beast.<tailnet>.ts.net
  laptop (cafe wifi) ─┤── tailnet (WireGuard) ─┤       → Open WebUI :3000
  desktop (home LAN) ─┘                        └─ https://beast.<tailnet>.ts.net:8443
                                                       → llama-server :8080 (API)
  Services rebind 0.0.0.0 → 127.0.0.1; the ONLY ways in are
  localhost and `tailscale serve` (TLS, tailnet-only, per-device identity).
  SearXNG + MCPO stay localhost-only (they serve the model, not humans).
```

- `tailscale serve` terminates TLS with automatic certs on the machine's
  MagicDNS name and is **tailnet-only by design** (its public cousin,
  `tailscale funnel`, is explicitly out of scope).
- Every connecting device is identified by its WireGuard key — that's the
  auth layer for the API. WebUI additionally gets `WEBUI_AUTH=true`.

## Implementation phases

### Phase 1 — join the tailnet (~15 min)
New `scripts/setup-tailscale.sh` (idempotent):
1. `sudo pacman -S --needed tailscale`
2. `sudo systemctl enable --now tailscaled`
3. `sudo tailscale up` → prints the login URL once; no-op when already up
4. Print MagicDNS hostname + `tailscale status`

### Phase 2 — expose the two human-facing services (~30 min)
New `scripts/tailscale-serve.sh` (idempotent, uses `--bg` persistent config):
- `tailscale serve --bg --https=443  http://127.0.0.1:3000`   (WebUI)
- `tailscale serve --bg --https=8443 http://127.0.0.1:8080`   (LLM API)
- MCPO/SearXNG: NOT exposed (model-internal plumbing).

### Phase 3 — shrink the bind surface (~1–2 h, the real work)
This is the breaking-change phase: LAN devices reach the stack via the
tailnet after this, not via raw LAN IPs.
- `openbeast.conf`: new `BIND_HOST` (default `127.0.0.1`; set `0.0.0.0` to
  restore old LAN-open behavior). Read via a small `scripts/lib/conf.sh`
  following the `weights.sh` resolver pattern.
- `scripts/serve.sh`: `HOST` default ← `BIND_HOST`.
- `start.sh`: `mcpo --host 127.0.0.1`.
- `docker-compose.yml`: `GRANIAN_HOST=127.0.0.1`; WebUI `HOST=127.0.0.1`,
  `WEBUI_AUTH=true` (first signup becomes admin — do it immediately).
- Optional defense-in-depth: `LLAMA_API_KEY` in conf → serve.sh appends
  `--api-key`; document the matching WebUI/opencode settings. Default off —
  the tailnet is the boundary; enable it if the tailnet ever gains users.
- `tests/test_scripts.sh`: assert serve.sh honors `BIND_HOST` and that no
  script hardcodes `--host 0.0.0.0`.

### Phase 4 — polish (~30–60 min)
- `scripts/healthcheck.sh`: add a `tailscale status --json` check
  (`.Self.Online == true`) with `--restart` support (`systemctl restart
  tailscaled`).
- README "Remote access" section + INSTALL.md steps.
- `opencode.json` documented remote variant: `baseURL:
  "https://beast.<tailnet>.ts.net:8443/v1"` — full coding agent against the
  home GPU from anywhere.
- Phone UX: install Tailscale app → sign in → open
  `https://beast.<tailnet>.ts.net` → "Add to Home Screen" (WebUI is a PWA).

Total effort: ~half a day including verification from a phone on cellular.

## Verification checklist
1. `curl https://beast.<tailnet>.ts.net:8443/v1/models` from a laptop OFF the
   home LAN (phone hotspot) — expect the model list.
2. Same URL with tailscale disconnected — expect timeout (proves boundary).
3. `nmap` from a LAN device not on the tailnet — 3000/8080/3001/8888 closed.
4. Phone PWA chat round-trip on cellular.
5. `./scripts/healthcheck.sh` all-green including the new tailscale row.

## Out of scope (deliberately)
- **`tailscale funnel`** (public internet exposure) — no. The tailnet is the
  perimeter; anonymous internet traffic never reaches an unauthenticated
  llama-server.
- **Headscale migration** — documented above as the escape hatch; not built
  until there's a reason.
- **Exit node / subnet routing** — orthogonal features; revisit if we want
  the GPU box to also be a travel VPN.

## Open questions for Max
1. Tailscale account: personal Google/GitHub SSO login is fine (free tier).
2. Hostname: `beast`? (becomes `beast.<tailnet>.ts.net` everywhere)
3. Should the eval/agent tooling on other machines get API-key auth from day
   one, or is tailnet device identity enough to start?
