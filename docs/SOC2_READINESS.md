# SOC 2 readiness: control mapping and gap list

**Status: self-assessment, not an audit.** Nothing here has been reviewed by a
CPA firm. This document exists so a security reviewer at a prospective customer
can see what OpenBeast provides, where the evidence lives, and what is honestly
missing. Every "provided" claim below names the file that implements it.

**Read the scope section first.** Most of what SOC 2 examines is a property of
*your organisation*, not of a piece of software. OpenBeast can satisfy the
technical controls in its own boundary. It cannot give you a SOC 2 report.

---

## 1. Scope and boundary

OpenBeast is **self-hosted software running on hardware the customer owns**.
There is no OpenBeast-operated service, no vendor-side data plane, and no
telemetry. That has three consequences an assessor should understand up front:

- **OpenBeast is not a subservice organisation.** There is no carve-out or
  inclusive method to apply to us, because no customer data reaches us. There is
  no SOC 2 report for OpenBeast to hand you, and any vendor questionnaire asking
  for one has mis-modelled the relationship. The correct analogue is an
  open-source component in your own environment, evidenced by the
  supply-chain controls in §5.
- **The system boundary is the customer's rig, its clients, and the tailnet
  between them.** All of it sits inside the customer's own control environment.
- **Confidentiality is architectural rather than configured.** There is no cloud
  code path to disable, so "data must not leave our environment" is enforced by
  the absence of a mechanism rather than by a setting somebody could flip.

**In-scope Trust Services Criteria:** Security (the common criteria) throughout,
Confidentiality where customer data is processed, and Availability only where
the customer commits to an SLA. Processing Integrity and Privacy are out of
scope for the software itself.

## 2. What OpenBeast provides, by criterion

Mapped to the 2017 TSC (with 2022 points of focus). "Provided" means implemented
and evidenced. "Partial" means real but incomplete, with the gap named.

### CC6.1 — Logical access, authorisation

| Mechanism | Where | State |
|---|---|---|
| Device authentication on the inference path: per-device bearer keys, fail-closed | `agents/edge.py` | Provided, **opt-in** (`EDGE_GATE`, default off) |
| Human authentication and RBAC tiers (admin vs guest), per-user file shards | `agents/openapi_tools.py` | Provided |
| Signed-JWT identity between WebUI and the tool server | `agents/openapi_tools.py` | Provided, opt-in |
| Network-layer device identity (WireGuard keys, ACLs) | Tailscale | Provided |
| Strict route allowlist for remote callers, 404 rather than 403 | `agents/edge.py` | Provided |
| All services bind loopback; no public ingress; `tailscale funnel` never used | `scripts/lib/conf.sh`, `scripts/setup-tailscale.sh` | Provided |

**Two identity layers, answering different questions.** The tool server
authenticates *the human* and resolves their RBAC tier and file shard.
beast-gate authenticates *the device*. An assessor should test both.

### CC6.2 / CC6.3 — Provisioning, deprovisioning, least privilege

| Mechanism | Where | State |
|---|---|---|
| Device enrollment, revocation, rotation | `scripts/clients.sh` | Provided |
| Revocation effective on the next request, no restart, hot-reloaded registry | `agents/edge.py` | Provided |
| Keys stored as SHA-256 only; plaintext shown once, never in argv | `scripts/clients.sh` | Provided |
| RBAC restricts guests to `web_search` and `fetch`, never shell or file tools | `scripts/configure-webui.sh`, `agents/openapi_tools.py` | Provided |

**Known limits an assessor will find.** A generation already streaming is not
terminated by revocation; it runs to completion. Enrollment is out-of-band (the
owner sends a key over a channel of their choosing), so there is no
self-service request-and-approve flow and no key expiry.

### CC6.6 / CC6.7 — Transmission, boundary protection

| Mechanism | Where | State |
|---|---|---|
| All transport encrypted (WireGuard, plus TLS for `tailscale serve`) | Tailscale | Provided |
| No public inbound surface whatsoever | architecture | Provided |
| SSRF-guarded `fetch`: resolve, vet, pin the IP, re-vet every redirect hop | `agents/tools.py` | Provided |
| CGNAT and IPv6-ULA classification pinned across interpreters | `agents/tools.py` | Provided |
| Secrets scrubbed from the shell tool's environment | `agents/tools.py` | Provided |
| Egress routing through a controlled exit node | [`EGRESS_PRIVACY.md`](EGRESS_PRIVACY.md) | Documented, customer-configured |

### CC7.2 / CC7.3 — Monitoring, accountability

| Mechanism | Where | State |
|---|---|---|
| Inference audit: one row per completion, device + enrollment-stable `device_uid`, token usage, `request_id` echoed to the caller | `agents/edge.py` → `.run/inference-audit.jsonl` | Provided, requires `EDGE_GATE=true` |
| Tool-call audit: who ran which tool, when | `agents/openapi_tools.py` → `.run/tool-audit.jsonl` | Provided |
| Prometheus metrics | `agents/edge.py`, `agents/openapi_tools.py` | Provided |
| Health and consistency reporting | `scripts/doctor.sh`, `scripts/healthcheck.sh` | Provided |
| Log rotation | `logrotate` config | Provided |

**The most important caveat in this document:** with `EDGE_GATE=false`, which is
the default, **there is no inference audit trail and no per-device identity at
all**. Any organisation intending to evidence CC7.2 must turn beast-gate on. See
the gap list.

### CC8.1 — Change management

| Mechanism | Where | State |
|---|---|---|
| CI on every push: tests, ShellCheck, Ruff, `pip-audit` | `.github/workflows/` | Provided |
| Branch protection, required status checks, secret scanning and push protection | GitHub repo settings | Provided |
| Dependabot | `.github/dependabot.yml` | Provided |
| Private vulnerability reporting and a documented disclosure policy | [`SECURITY.md`](../SECURITY.md) | Provided |

### CC9.2 — Vendor and supply chain

| Mechanism | Where | State |
|---|---|---|
| Container images digest-pinned | `docker-compose.yml` | Provided |
| Python dependencies hash-pinned, CVE-audited in CI | `agents/requirements.txt`, CI | Provided |
| **Model weights SHA-256 and size pinned, verified at load** | `scripts/weights.registry`, `scripts/serve.sh` | Provided (`WEIGHT_ENFORCE=warn` by default, `strict` available) |
| Third-party component inventory with licences | [`NOTICE`](../NOTICE), README credits | Provided |

Weight pinning is worth calling out to a reviewer. Model weights are the one
shipped artifact most stacks never verify, and an unverified GGUF is arbitrary
code-adjacent data with full influence over what your agent does.

### A1.x — Availability (only if you commit to an SLA)

Supervisor with health-monitored auto-restart and a refilling restart budget,
memory-capped systemd scope, model-load rollback, fast-boot bridge. Backup and
recovery are **the customer's responsibility**; OpenBeast ships no backup
tooling.

## 3. Where OpenBeast is unusually strong

Worth leading with in a diligence conversation:

1. **No cloud path exists.** Not "local by default with a cloud toggle." There
   is no code path to enable, which turns a policy commitment into an
   architectural one.
2. **Weight-level supply-chain integrity**, which almost nothing else in this
   category does.
3. **Two-layer identity** separating the human from the device, with an audit
   trail for each.
4. **Adversarial review as practice**, with findings and corrections recorded in
   the open in [`TODO.md`](TODO.md) rather than quietly fixed.

## 4. Gap list

Ranked by what a reviewer will find first. Tracked in [`TODO.md`](TODO.md).

| # | Gap | Criterion | Effort |
|---|---|---|---|
| 1 | **beast-gate is off by default**, so out of the box there is no per-device identity, no admission control and no inference audit. A compliance-oriented deployment needs it on, and nothing currently tells the operator that. | CC6.1, CC7.2 | S: a `doctor` warning plus a documented hardening profile |
| 2 | **`/api/slot` is unauthenticated.** A revoked device still reads the rig's model, capacity and service topology. | CC6.1 | S |
| 3 | **No usage accountability per principal.** beast-gate rate-limits requests but not tokens or wall-clock, so one device can consume unbounded compute and starve others. There is no quota and no usage report. | CC6.1, A1.1 | M |
| 4 | **Enrollment is out-of-band and keys never expire.** No request-and-approve flow, no expiry, no scheduled rotation. | CC6.2, CC6.3 | M |
| 5 | **Revocation does not terminate an in-flight stream.** | CC6.2 | S |
| 6 | **No decommissioning path.** There is no rig-side uninstall, so secure disposal is a manual checklist. | CC6.5 | M |
| 7 | **No formal SBOM.** Components are pinned and inventoried, but nothing emits CycloneDX or SPDX. | CC9.2 | S |
| 8 | **No log-integrity guarantees.** Audit logs are append-only JSONL on local disk, with no signing, no tamper evidence and no forwarding to an external SIEM. An assessor will ask whether an administrator could edit them. | CC7.2, CC7.3 | M |
| 9 | **No encryption at rest.** Prompts, transcripts and audit logs sit in plaintext on the host. Full-disk encryption is assumed but neither enforced nor checked. | CC6.1 (Confidentiality) | S to check, customer to implement |
| 10 | **No configuration baseline or drift detection.** `doctor` checks health and consistency, not conformance to a documented secure baseline. | CC7.1 | M |

## 5. What OpenBeast cannot give you

Stated plainly, because a vendor that implies otherwise is not worth trusting:

SOC 2 is an audit of an **organisation**, covering its people, policies and
operations. The majority of the common criteria (CC1 control environment, CC2
communication, CC3 risk assessment, CC4 monitoring activities, CC5 control
activities) are about governance and cannot be satisfied by software at all.

Deploying OpenBeast does not make you SOC 2 compliant. It gives you technical
controls that map cleanly onto CC6 through CC9, plus the evidence to demonstrate
them. Your auditor will still need your policies, your risk assessment, your
onboarding and offboarding records, and your vendor management programme.

## 6. Suggested hardening profile for a regulated deployment

Not yet shipped as a preset. Tracked as gap #1.

```bash
EDGE_GATE=true              # per-device identity, admission control, audit
WEIGHT_ENFORCE=strict       # refuse to serve an unpinned weight
AGENT_ROUTER=false          # smaller attack surface unless you need it
WEBUI_AUTH=true             # login wall on the browser UI
```

Plus, outside OpenBeast: full-disk encryption, `tailscale serve` restricted by
ACL to the devices that need each surface, audit logs forwarded off-box to
append-only storage, and documented enrollment and revocation procedures with
records kept.
