# Sandboxing the agent bash tool (Sandlock)

Arsenal Phase 1 (docs/TOOL_ARSENAL_RESEARCH.md finding #3, ADOPT 3-0).
Kernel-level confinement — Landlock (filesystem/network/IPC) + seccomp-bpf
(syscall filtering) — for every shell command the model runs through the
`bash` tool. Unprivileged: no root, no containers, no images.

**Status: OPT-IN.** Installed by `scripts/setup-sandlock.sh`, enabled by one
env var, OFF by default until agent behavior under confinement is
eval-validated (see "Road to default").

## What it protects — and what it does not

OpenBeast already has a resource layer in `agents/tools.py` (`run_reaped`):
RLIMIT_AS caps child memory, killpg reaps the whole process group on
timeout, output is capped in the parent. Sandlock **composes with that
layer, it does not replace it** — it adds what rlimits cannot express:

| Layer | Stops | Does NOT stop |
|---|---|---|
| rlimit/killpg (always on) | memory bombs, orphaned process trees, output floods | file reads/writes anywhere, network anywhere |
| Sandlock (this doc) | reads/writes outside the allowlist (`~/.ssh`, `~/.gnupg`, `~/.aws`, the repo, the rest of `$HOME` are *invisible*), outbound network per policy, dangerous syscalls | resource exhaustion (rlimit's job), a model exfiltrating data it can *legitimately* read (workdir contents + open network = data can leave), prompt injection itself |

Honesty note: with the default profile's open outbound network, sandboxing
is a **filesystem** integrity/secrecy boundary, not an exfiltration
boundary. Tighten `[network] allow` to an endpoint list if exfiltration of
workdir contents is in your threat model.

## The policy (profile `openbeast`)

Canonical copy: `scripts/sandlock-profile-openbeast.toml`, installed to
`~/.config/sandlock/profiles/openbeast.toml`. Allowlist model — anything
unlisted is denied at the kernel:

- **Read-only:** `/usr /lib /lib64 /bin /sbin /etc /opt /proc /sys` +
  `/run/systemd/resolve` (DNS).
- **Read+write:** the per-call agent workdir (`AGENT_WORKDIR` /
  `OPENBEAST_FILES_DIR` — granted at runtime via the wrapper's `-w "$PWD"`,
  because `bash()` sets cwd to it), `/tmp`, `/var/tmp`, and device sinks
  (`/dev/null` etc. — without these, `>/dev/null` fails with EACCES).
- **Denied implicitly:** everything else. `~/.ssh` reads return ENOENT —
  Landlock *hides* the paths rather than erroring EPERM.
- **Network:** outbound open by default (agents pip-install/git-clone);
  `allow = []` flips to full deny — validated. Strict allowlist example is
  in the profile comments.
- **Limits:** only a process-count fence (256). Memory/CPU deliberately
  left to the rlimit layer.

## Enabling (2 lines)

```bash
./scripts/setup-sandlock.sh
export OPENBEAST_BASH_WRAPPER='sandlock run -p openbeast -w "$PWD" --'
```

Single quotes matter: `run_reaped` executes commands with `shell=True`, so
`$PWD` expands *inside the spawned shell*, whose cwd `bash()` has already
set to the agent workdir — that is how a static wrapper string grants a
per-call directory. Export it in the shell that launches `./start.sh` (the
agent server inherits it), or add the export line to `openbeast.conf`
(sourced shell). `tools.py` reads the var **per call**, so toggling it does
not require a server restart — but the env of an already-running server
can't be changed from outside; set it before launch.

**Verify it's active:** ask the agent to run `ls ~/.ssh` → confined output
is `No such file or directory`. Unconfined shows your keys.

**Disable:** unset `OPENBEAST_BASH_WRAPPER` (or comment the line) and
restart.

## Validation matrix (2026-07-08, this box: kernel 7.0.10-arch1-1, Landlock LSM active)

All tests run through the real `tools.py bash()` path with the wrapper set,
except where noted. Pinned build: **commit
`1cd6ba6518f614bf4db469f1b2d0416bc2f1cd54`, v0.8.4**.

| Test | Result |
|---|---|
| Baseline `echo hi` wrapped | PASS |
| Write + read in agent workdir (via `-w "$PWD"`) | PASS |
| Read `~/.ssh/*` | DENIED (ENOENT — hidden) |
| Write `~/testfile` | DENIED (EACCES), file not created |
| Write `/tmp` | PASS (allowed) |
| Outbound curl with `allow = []` | DENIED (DNS resolution fails) |
| Outbound curl with scoped `--net-allow example.com:443` + DNS | PASS |
| Exit-code propagation (`exit 42` → 42, `exit 7` via tools.py) | PASS |
| killpg semantics (`run_reaped` kills group → sandboxed `sleep` dies) | PASS¹ |
| Overhead, 100× `true` | ~1.2 ms/invocation supervisor mode, ~1.0 ms `--no-supervisor`, vs ~0.2 ms bare |

¹ Sandlock's child calls `setpgid(0,0)` — it *leaves* the wrapper's process
group. The test confirmed the child still dies within ~1 s of
`killpg(SIGKILL)` because sandlock's supervisor teardown takes it down.
The `run_reaped` no-orphans guarantee holds, but through sandlock's
supervisor rather than group membership — re-verify this on every version
bump.

Measured overhead (~1.2 ms/call) beats upstream's claimed ~5 ms and is
noise against model inference time.

## Security review (gate passed 2026-07-08)

Reviewed at commit `1cd6ba6` (10 commits past the recon-audited
`5c9bafe9`, all network-module refactors by the lead author; v0.8.4 both):

- **Build path clean:** plain `cargo build`; no curl|bash, no network
  fetches, no binary blobs. The only `build.rs` logic compiles a local
  test-helper C file. `Cargo.lock` committed with checksums (248 deps).
- **Network code accounted for:** all socket/connect code in non-test
  source is the documented feature set — the seccomp on-behalf
  connect/send engine (the sandbox mechanism itself) and the
  `transparent_proxy` module backing the opt-in `--http-allow`/
  `--http-inject-ca` HTTPS-MITM ACL. No telemetry, no phone-home.
- **Upstream caveats (unchanged from research):** young project (first
  commit 2026-03-14), bus factor ~1 (lead author 669/782 commits, single
  company), benchmarks first-party. `bollard` (Docker API) compiles into
  the CLI as dead weight via the OCI feature path; unused at runtime here.
  **Keep off:** `--http-inject-ca` (TLS MITM) — not in our profile.
- Consequence: commit is **pinned** in `setup-sandlock.sh`; version bumps
  require re-running this review + the validation matrix.

## Road to default

Confinement changes agent-visible behavior (hidden paths return ENOENT,
`/dev` surprises, denied writes). Before `OPENBEAST_BASH_WRAPPER` becomes
default-on, the eval suite must run green under the sandbox — same bar as
every other behavior-affecting change. TODO line for docs/TODO.md (Arsenal
section):

```
- [ ] Sandlock: run eval suite with OPENBEAST_BASH_WRAPPER set (profile
      `openbeast`); if green, flip to default-on in conf.sh. Until then:
      opt-in only. See docs/SANDBOXING.md.
```

Heavier tier (untrusted *fetched* code, full VM isolation): Firecracker,
deferred — finding #4 in docs/TOOL_ARSENAL_RESEARCH.md.
