# Contributing to OpenBeast

OpenBeast is a self-hosted AI workstation: llama.cpp serving, a 15-tool
agent arsenal (two surfaces — the identity tool server for the browser, the
MCP server for the terminal), browser + terminal frontends, RBAC, and a
137-task / 291-unit (v4) eval suite. Contributions welcome — here's how to
land one cleanly.

**It also splits across machines.** The **rig** runs the full stack; any
Mac/Linux box can install **client mode** (`scripts/setup-client.sh`), which
runs the same tool arsenal *locally* and sends only inference to the rig over
Tailscale — mediated by the versioned `/api/slot` discovery contract and,
optionally, **beast-gate** (`agents/edge.py`), the identity-aware inference
edge. So `agents/tools.py`, `agents/mcp_server.py`, and `scripts/client*.sh`
are **client-facing**: a change there ships to other people's laptops, not just
the rig. Read [`docs/BEAST_SLOT.md`](docs/BEAST_SLOT.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before touching them.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
Found a security issue? Do **not** open a public issue — see
[SECURITY.md](SECURITY.md).

## Dev setup

```bash
git clone https://github.com/MaximilianKhan/openbeast && cd openbeast
./bootstrap.sh --minimal     # build llama.cpp + Python deps (no Docker/weights needed for dev)
```

Most development doesn't need a GPU at all: the test suite is CPU-only.

## Before you open a PR

Run what CI runs, so nothing surprises you in review:

```bash
# Tests (the CI 'test' job)
./tests/run_tests.sh                 # structure checks + enrollment CLI + tool unit tests
python3 -m pytest tests/ -q          # full suite (~278 tests, CPU-only)

# Quality gates (the CI 'PR quality' jobs) — install once:
#   pip install --user ruff pip-audit shellcheck-py
ruff check agents/*.py evals/*.py tests/*.py --select E9,F   # syntax + undefined names
shellcheck -S error start.sh stop.sh bootstrap.sh agent.sh scripts/*.sh scripts/lib/*.sh tests/*.sh
pip-audit -r agents/requirements.txt --disable-pip --no-deps  # dependency CVEs
python3 evals/suite_stats.py --check  # docs cite the generated eval counts
```

Two workflows gate every PR (green is required):

- **`ci.yml`** — the full pytest + structure suite, the doc-count check,
  the variant-spec audit, and a repo-wide `bash -n` sweep.
- **`pr-quality.yml`** — ShellCheck (error severity), Ruff (E9,F), and
  pip-audit against the pinned deps.

Dependabot opens weekly PRs for dependency bumps; CI + pip-audit validate
them before a human merges.

House rules the suite enforces (so you don't discover them in review):

- **Shell**: `set -euo pipefail` on entry points; no command substitution
  of a fallible pipeline without `|| true`; no hardcoded `0.0.0.0` (bind
  comes from `lib/conf.sh`); weights paths go through `lib/weights.sh`.
- **Subprocesses in Python**: never bare `subprocess.run(shell=True,
  timeout=...)` — use `run_reaped` (whole-process-group kill, output
  capping, rlimits). History: an orphaned grandchild once ate 122 GB of
  RAM + all swap. See `docs/TODO.md` post-mortem.
- **Tool changes**: `agents/tools.py` is the single source of truth;
  `agents/mcp_server.py` only wraps it. Update `docs/TOOLS.md` in the
  same PR if the surface changes.
- **New config key**: it lands in **three** places in the *same* change —
  `scripts/lib/conf.sh` (the resolver, and the only authority on defaults),
  `openbeast.conf.example` (commented, with the env override named), and the
  configuration table in `docs/REFERENCE.md`. A key that exists in only one or
  two of those is how the table went twelve keys stale. Mind the export
  discipline already in `conf.sh`: only export when non-empty, because an
  exported empty string still reads as "set" downstream.
- **`/api/slot` is a versioned contract**, not an internal JSON blob —
  `tests/test_beast_slot.py` pins its field names and the `ctx_shared` /
  `ctx_total` capacity math. Adding a field is *additive*: bump `beast_slot`,
  leave `min_client` alone. **Renaming or repurposing** a field breaks every
  deployed client and forces an explicit decision to raise `min_client` —
  make that call in the PR description, not in a test edit. Same rule for
  `.run/clients.json`, the registry schema shared with `agents/edge.py`.
- **Skills**: after editing any `skills/*/SKILL.md`, run
  `python3 scripts/generate-skill-index.py` (CI fails on a stale index).

## Adding an eval task

Follow `skills/eval-task-author/SKILL.md` — it encodes the pitfalls that
actually burned us (substring lints, input-type ambiguity, unpinned
randomness). Every task needs a reference implementation that passes
validation and a wrong implementation that fails it. Multi-language
variants: `skills/eval-variant-porter/SKILL.md`, then
`python3 tests/audit_variants.py`.

Don't change existing task specs/validations casually: the leaderboard
in `docs/RESULTS.md` is only comparable across runs of identical tasks.
Spec changes ship in batches, with a note in `docs/RESULTS.md` that
prior scores predate them.

## Hardware profiles

Own a GPU tier we haven't measured (4090, 2x3090, AMD)? The most valuable
contribution available: run `scripts/measure-vram.sh` sweeps and submit
your measured contexts — see `docs/HARDWARE_PROFILES.md` Phase 2.

## Commit style

Imperative subject, body explains *why*. Small, atomic commits.
`skills/git-discipline/SKILL.md` is the house methodology.

## License

Apache-2.0. By contributing you agree your work lands under it (see
`LICENSE` and `NOTICE`).
