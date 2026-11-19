# Contributing to OpenBeast

OpenBeast is a self-hosted AI workstation: llama.cpp serving, a 19-tool
agent arsenal (two surfaces — the identity tool server for the browser, the
MCP server for the terminal), browser + terminal frontends, RBAC, and an eval suite (the current one
is v5.0: 71 scored units over 34 base tasks; `python3 evals/suite_stats.py`
prints the counts). Contributions welcome — here's how to
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
./scripts/pydeps.sh install  # the hash-pinned Python deps
pip install pytest           # the only extra the CPU-only test suite needs
```

Most development doesn't need a GPU at all: the test suite is CPU-only.
`./bootstrap.sh --minimal` is not a dev setup: it skips only Docker, still
builds llama.cpp and downloads the ~20 GB default weight, refuses macOS and
hard-fails under 24 GB VRAM. Use it only on a rig.

## Before you open a PR

Run what CI runs, so nothing surprises you in review:

```bash
# Tests (the CI 'test' job)
./tests/run_tests.sh                 # the shell suites (scripts, clients, job.sh, artifact CLI,
                                     #   offline fixes, ssd-wear) + tool unit tests + full pytest
python3 -m pytest tests/ -q          # full suite (~5,100 tests, CPU-only)

# Quality gates (the CI 'PR quality' jobs) — install once:
#   pip install --user ruff pip-audit shellcheck-py
ruff check agents evals tests scripts extensions --select E9,F   # syntax + undefined names
shellcheck -S error start.sh stop.sh bootstrap.sh agent.sh scripts/*.sh scripts/lib/*.sh tests/*.sh extensions/*/*.sh
pip-audit -r agents/requirements.txt  --disable-pip --no-deps  # dependency CVEs — the direct pins
pip-audit -r agents/requirements.lock --disable-pip --no-deps  # …and the whole hash-pinned closure
./scripts/pydeps.sh verify            # the lock is current against requirements.txt
python3 evals/suite_stats.py --check  # docs cite the generated eval counts
```

Two workflows gate every PR when GitHub Actions is on (it is off for now;
see [CI is off](#ci-is-off-check-locally-before-merging) below):

- **`ci.yml`** — installs the hash-pinned closure (`pip --require-hashes -r
  agents/requirements.lock`) and checks the lock is current
  (`pydeps.sh verify`), then the full pytest + structure suite, the
  doc-count check, the variant-spec audit, and a repo-wide `bash -n` sweep.
- **`pr-quality.yml`** — ShellCheck (error severity), Ruff (E9,F), and
  pip-audit against both the direct pins and the lock.

A third, `dependabot-relock.yml`, runs only on Dependabot's PRs: it
regenerates `agents/requirements.lock` for the bumped `requirements.txt`
and pushes it to the PR branch, because Dependabot edits only the latter.
Dependabot opens weekly PRs; `./scripts/land-dependabot.sh` lands them one
at a time (rebase → relock → approve the held CI runs → merge). With no
arguments it takes only the PRs that touch `agents/requirements.txt`; pass a
PR number to land any other — see
[`docs/UPDATING.md`](docs/UPDATING.md). **Never bump `requirements.txt`
without the lock**: `update.sh --python` regenerates it, and CI goes red on a
commit that moves one without the other.

### CI is off: check locally before merging

**Since 2026-11-17, GitHub Actions is turned off for this repo (cost).** The
workflows above are still the definition of "green", but nothing runs them,
so the author runs them before merging. Until a free local runner replaces
this (`docs/TODO.md`, "A free local CI runner"):

1. **On the Mac (or any dev box), from the PR branch,** with a Python ≥ 3.10
   that has the locked deps (the eval venv works:
   `~/.local/opt/openbeast-eval-venv/bin/python`):

   ```bash
   PY=~/.local/opt/openbeast-eval-venv/bin/python
   $PY evals/suite_stats.py --check          # docs cite the generated eval counts
   $PY evals/v5_board.py --check             # RESULTS / README v5 board blocks are current
   $PY -m pytest tests/ -q                   # or, for a docs-only PR, at least the doc-pinning tests:
   $PY -m pytest -q tests/test_suite_stats.py tests/test_beast_docs.py \
       tests/test_beast_slot_ports_doc.py tests/test_v5_board.py
   ruff check agents evals tests scripts extensions --select E9,F
   ```

   For a docs change, also check that every `#anchor` link in the files you
   touched resolves (the doc-pinning tests do not cover every page).

2. **The Linux-only shell suites on spark-2, in a temp clone.** Several
   suites need GNU userland (`grep -z`, util-linux `flock`, GNU `sed`),
   `netplan`, or other things macOS lacks, and they must run from a real
   repository checkout, not a copied tree (`test_uninstall.sh` and others
   fail from a non-repo copy). spark-2 runs no OpenBeast service, so a
   scratch clone there disturbs nothing:

   ```bash
   ssh spark-2.<tailnet>.ts.net 'set -e; d=$(mktemp -d); trap "rm -rf $d" EXIT
     git clone -q --branch <pr-branch> https://github.com/MaximilianKhan/openbeast "$d/ob"
     cd "$d/ob" && ./tests/run_tests.sh'
   ```

   `tests/run_tests.sh` runs the shell suites the CI shell jobs ran, plus the
   tool tests and pytest. Never run it in spark-1's `~/openbeast`: that
   checkout is the live command center. Offline, clone from a bundle instead
   of GitHub.

3. **Merge with admin rights, results in the PR body.** Put what you ran and
   what it printed (pass counts, `OK` lines, any skip and why) in the PR
   description, then:

   ```bash
   gh pr merge <N> --merge --admin
   ```

   `--admin` is only for the missing status checks. It is not a licence to
   merge a red local run: a failing check is fixed, or the PR body says
   exactly why it is unrelated (with the same failure shown on `main`).

House rules the suite enforces (so you don't discover them in review):

- **Shell**: `set -euo pipefail` on entry points; no command substitution
  of a fallible pipeline without `|| true`; no hardcoded `0.0.0.0` (bind
  comes from `lib/conf.sh`); weights paths go through `lib/weights.sh`.
- **Subprocesses in Python**: never bare `subprocess.run(shell=True,
  timeout=...)` — use `run_reaped` (whole-process-group kill, output
  capping, rlimits). History: an orphaned grandchild once ate 122 GB of
  RAM + all swap. See `docs/TODO.md` post-mortem.
- **Tool changes**: `agents/tools.py` is the single source of truth for the
  primitives; `agents/mcp_server.py` wraps it and adds the MCP-only tools
  (skill, agent control, artifacts, `language_reference`, `platform_docs`).
  `docs/TOOLS.md` pins the count (19) and `tests/test_tools.py` asserts it, so a new tool is
  a doc change in the same PR. Note the two era-hashed files (`tools.py`,
  `runner.py`): touching either rolls the eval cache era (`scripts/eval-era.sh`),
  which is why the artifact and language tools live in `mcp_server.py`.
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
  `python3 scripts/generate-skill-index.py` (CI fails on a stale index). A
  new skill written here must also be named in `skills/IN_HOUSE_SKILLS.txt`,
  or `skill-import.sh verify` fails (see `skills/README.md`).

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
