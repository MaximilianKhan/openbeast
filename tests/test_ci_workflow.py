#!/usr/bin/env python3
"""CI workflow and test-runner invariants that an edit can silently break.

The full pytest step ignores every file a named pytest step already ran (so
~2 min of suites stop running twice). The two lists must agree: a file
ignored in the full run but named nowhere would never run in CI at all.

Since 2026-10-09 the same question is asked of the SHELL suites (one with 56
checks on the skill-import gate ran locally and never in CI), and of the
workflow's shape: parallel jobs behind one required `test` job, tools and
zig installed by hash, no skip that exits 0, no "ALL PASSED" without pytest.

Run: python3 -m pytest tests/test_ci_workflow.py -q
"""
from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
QUALITY = WORKFLOWS / "pr-quality.yml"
RELOCK = WORKFLOWS / "dependabot-relock.yml"
CI_REQS = REPO / ".github" / "ci-requirements"
RUNNER = REPO / "tests" / "run_tests.sh"


def _jobs(path: Path) -> dict:
    return yaml.safe_load(path.read_text())["jobs"]


def _runs(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in job.get("steps", []))


def _pytest_runs(text: str) -> list[str]:
    """Each `python -m pytest ...` command, a folded (>-) block joined."""
    runs, lines = [], text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("run: python -m pytest"):
            runs.append(s[len("run: "):])
        elif s == "run: >-":
            ind = len(ln) - len(ln.lstrip())
            block = []
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= ind:
                    break
                block.append(nxt.strip())
            cmd = " ".join(x for x in block if x)
            if cmd.startswith("python -m pytest"):
                runs.append(cmd)
    return runs


def split(text: str) -> tuple[set[str], set[str], list[str]]:
    """(files the named steps run, --ignore paths, --ignore-glob patterns)."""
    named, ignored, globs, full = set(), set(), [], 0
    for cmd in _pytest_runs(text):
        args = cmd.replace("'", "").split()[3:]
        if "tests/" in args:
            full += 1
            ignored |= {a.split("=", 1)[1] for a in args if a.startswith("--ignore=")}
            globs += [a.split("=", 1)[1] for a in args if a.startswith("--ignore-glob=")]
        else:
            for a in args:
                if a.startswith("tests/"):
                    hits = sorted(str(p.relative_to(REPO)) for p in REPO.glob(a))
                    assert hits, f"a named CI pytest step runs {a}, which matches no file"
                    named |= set(hits)
    assert full == 1, f"expected one full `pytest tests/` run, found {full}"
    return named, ignored, globs


def _ignored(path: str, ignored: set[str], globs: list[str]) -> bool:
    return path in ignored or any(fnmatch.fnmatch(path, g) for g in globs)


def test_the_full_run_skips_exactly_what_the_named_steps_ran():
    named, ignored, globs = split(CI.read_text())
    assert named, "no named pytest step found (parser drift?)"
    twice = sorted(p for p in named if not _ignored(p, ignored, globs))
    assert not twice, f"named AND in the full run (runs twice): {twice}"
    every = [str(p.relative_to(REPO)) for p in (REPO / "tests").glob("test_*.py")]
    never = sorted(p for p in every if _ignored(p, ignored, globs) and p not in named)
    assert not never, f"ignored by the full run and named nowhere (never runs in CI): {never}"
    assert all((REPO / p).exists() for p in ignored), "an --ignore names a file that is gone"


def test_the_check_catches_an_ignore_with_no_named_step():
    """Negative control on a built workflow: ignoring a suite nobody names."""
    fake = ("      - name: a\n        run: python -m pytest tests/test_tools.py -q\n"
            "      - name: full\n        run: >-\n          python -m pytest tests/ -q\n"
            "          --ignore=tests/test_tools.py --ignore=tests/test_edge.py\n")
    named, ignored, globs = split(fake)
    assert named == {"tests/test_tools.py"}
    assert ignored == {"tests/test_tools.py", "tests/test_edge.py"}
    never = [p for p in ignored if p not in named]
    assert never == ["tests/test_edge.py"]


def test_the_wiring_baseline_proof_is_pinned_and_required():
    """The byte-identity proof went dead once the wiring reached main (every
    PR base carried it, so no baseline ref was set). It is pinned to a
    pre-wiring release and must not skip."""
    text = CI.read_text()
    step = text[text.index("name: beast-hydra / beast-instinct stack wiring tests"):]
    step = step[:step.index("      - name:", 10)]
    assert re.search(r"^\s*ref=v\d+\.\d+\.\d+\s*$", step, re.M), "no pinned baseline ref"
    assert "WIRING_BASELINE_REQUIRED=1" in step
    assert "refs/tags/$ref" in step, "a depth-1 checkout has no tags: fetch the pinned one"


def test_the_i8_guard_gets_a_base():
    text = CI.read_text()
    assert "OPENBEAST_I8_BASE=$BASE_SHA" in text and "OPENBEAST_I8_BASE=none" in text
    assert text.index("OPENBEAST_I8_BASE=") < text.index("name: beast-hydra / beast-instinct tests")
    # ...and in the SAME job: $GITHUB_ENV and the deepened history do not
    # cross jobs, so a guard in another job gives the test no base at all.
    owners = [name for name, job in _jobs(CI).items()
              if "tests/test_instinct_*.py" in _runs(job) and "--ignore" not in
              next(str(s.get("run", "")) for s in job["steps"] if "tests/test_instinct_*.py" in str(s.get("run", "")))]
    assert len(owners) == 1, owners
    names = [s.get("name") for s in _jobs(CI)[owners[0]]["steps"]]
    assert "Era-lock guard base (I8)" in names
    assert names.index("Era-lock guard base (I8)") < names.index("beast-hydra / beast-instinct tests")


def _wiring_step(text: str) -> str:
    step = text[text.index("name: beast-hydra / beast-instinct stack wiring tests"):]
    return step[:step.index("      - name:", 10)]


def _i8_base_depth(guard: str) -> int:
    """Commits in BASE..HEAD after the I8 deepen, then the wiring step's base
    fetch line `guard`, run in a depth-1 clone of a built repo."""
    import subprocess
    import tempfile

    def git(cwd, *a):
        return subprocess.run(["git", "-c", "protocol.file.allow=always", *a], cwd=cwd,
                              check=True, capture_output=True, text=True,
                              env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                                   "PATH": "/usr/bin:/bin", "HOME": tmp}).stdout.strip()

    with tempfile.TemporaryDirectory() as tmp:
        up = Path(tmp) / "up"
        up.mkdir()
        git(up, "init", "-q", "-b", "main")
        # A PR forked from an older main, while main moved on to `base`;
        # CI's HEAD is the refs/pull merge commit of the two.
        for i in range(3):
            git(up, "commit", "-q", "--allow-empty", "-m", f"old{i}")
        git(up, "checkout", "-q", "-b", "pr")
        for i in range(2):                        # the PR's own commits
            git(up, "commit", "-q", "--allow-empty", "-m", f"pr{i}")
        git(up, "checkout", "-q", "main")
        for i in range(3):                        # main after the fork
            git(up, "commit", "-q", "--allow-empty", "-m", f"main{i}")
        base = git(up, "rev-parse", "HEAD")
        git(up, "merge", "-q", "--no-ff", "--no-edit", "pr")
        head = git(up, "rev-parse", "HEAD")
        wt = Path(tmp) / "wt"
        git(tmp, "clone", "-q", "--depth=1", f"file://{up}", str(wt))
        git(wt, "fetch", "-q", "--no-tags", "--depth=200", "origin", base, head)
        subprocess.run(["bash", "-c", guard], cwd=wt, check=True, capture_output=True,
                       env={"BASE_SHA": base, "PATH": "/usr/bin:/bin", "HOME": tmp,
                            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow",
                            "GIT_CONFIG_VALUE_0": "always"})
        return len(git(wt, "log", "--format=%H", f"{base}..{head}").split())


def test_the_wiring_step_does_not_reshallow_the_i8_base():
    """The wiring step ran `git fetch --depth=1 origin $BASE_SHA` after I8
    deepened it, which made the base a shallow root again: I8's BASE..HEAD
    then held main's own history too, and could fail a PR over an old
    instinct commit on main."""
    line = next(ln.strip() for ln in _wiring_step(CI.read_text()).splitlines()
                if '"$BASE_SHA"' in ln and "fetch" in ln)
    assert _i8_base_depth(line) == 3, line  # pr0, pr1, the merge
    # negative control: the old unguarded fetch widens the range
    assert _i8_base_depth('git fetch --no-tags --depth=1 origin "$BASE_SHA"') > 3


# ---------------------------------------------------------------------------
# F1: every shell suite runs in CI and in tests/run_tests.sh
# ---------------------------------------------------------------------------

# Needs a live stack (llama-server, WebUI): run by hand after a boot.
NOT_IN_CI = {"test_smoke.sh"}


def unrun_shell_suites(suites: list[str], runner_text: str, test_scripts_text: str) -> list[str]:
    """Suites neither run by `runner_text` nor from inside test_scripts.sh
    (which both runners execute)."""
    def runs(text: str, suite: str) -> bool:
        return re.search(r"(?m)^[^#\n]*\bbash\b[^\n#]*tests/" + re.escape(suite) + r"\b", text) is not None

    def listed(text: str, suite: str) -> bool:      # run_tests.sh's "file|label" loop
        return re.search(r'(?m)^\s*"' + re.escape(suite) + r"\|", text) is not None

    return sorted(s for s in suites if s not in NOT_IN_CI
                  and not runs(runner_text, s) and not listed(runner_text, s)
                  and not runs(test_scripts_text, s))


def _shell_suites() -> list[str]:
    return sorted(p.name for p in (REPO / "tests").glob("test_*.sh"))


def test_every_shell_suite_runs_in_ci():
    ts = (REPO / "tests" / "test_scripts.sh").read_text()
    missing = unrun_shell_suites(_shell_suites(), CI.read_text(), ts)
    assert not missing, f"shell suites CI never runs: {missing} (add a `bash tests/<suite>` step)"


def test_every_shell_suite_runs_in_run_tests_sh():
    ts = (REPO / "tests" / "test_scripts.sh").read_text()
    missing = unrun_shell_suites(_shell_suites(), RUNNER.read_text(), ts)
    assert not missing, f"shell suites tests/run_tests.sh never runs: {missing}"


def test_the_shell_suite_check_catches_a_suite_nobody_runs():
    """Negative control on built text: one suite named, one only mentioned in
    a comment, one run from inside test_scripts.sh, one in the runner's list."""
    ci = ("      - run: bash tests/test_a.sh\n"
          "      # bash tests/test_b.sh is mentioned here and never run\n")
    ts = '_OUT="$(bash "$REPO_DIR/tests/test_c.sh" 2>&1)"\n'
    suites = ["test_a.sh", "test_b.sh", "test_c.sh", "test_d.sh", "test_smoke.sh"]
    assert unrun_shell_suites(suites, ci, ts) == ["test_b.sh", "test_d.sh"]
    assert unrun_shell_suites(suites, ci + '  "test_d.sh|label" \\\n', ts) == ["test_b.sh"]


# ---------------------------------------------------------------------------
# F27: parallel jobs behind the one required check
# ---------------------------------------------------------------------------

def _gate_script() -> str:
    (step,) = _jobs(CI)["test"]["steps"]
    return step["run"]


def test_the_required_check_is_a_gate_over_every_other_job():
    """Branch protection requires a check named exactly `test`. It must
    exist, wait for every other job, and run even when one of them failed —
    a job skipped because its `needs` failed reports as passed."""
    jobs = _jobs(CI)
    gate = jobs["test"]
    assert gate.get("name", "test") == "test"
    assert sorted(gate["needs"]) == sorted(j for j in jobs if j != "test")
    assert "always()" in str(gate.get("if"))
    assert len(jobs) >= 4, "the split is gone: CI is one serial job again"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed (the runner image has it)")
@pytest.mark.parametrize("results,rc", [
    ({"pytest": "success", "shell": "success"}, 0),
    ({"pytest": "success", "shell": "failure"}, 1),
    ({"pytest": "cancelled", "shell": "success"}, 1),
    ({"pytest": "success", "shell": "skipped"}, 1),
])
def test_the_gate_script_fails_unless_every_job_succeeded(results, rc):
    needs = {k: {"result": v, "outputs": {}} for k, v in results.items()}
    r = subprocess.run(["bash", "-eo", "pipefail", "-c", _gate_script()], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "RESULTS": json.dumps(needs)})
    assert r.returncode == rc, r.stdout + r.stderr
    if rc:
        bad = next(k for k, v in results.items() if v != "success")
        assert f"{bad}: {results[bad]}" in r.stdout


@pytest.mark.parametrize("wf", [CI, QUALITY], ids=lambda p: p.name)
def test_runs_on_main_are_not_cancelled(wf):
    cip = yaml.safe_load(wf.read_text())["concurrency"]["cancel-in-progress"]
    assert cip is not True and "pull_request" in str(cip), cip


def test_every_ci_pytest_call_has_a_per_test_timeout():
    runs = _pytest_runs(CI.read_text())
    assert runs and all(re.search(r"--timeout=\d+", r) for r in runs), runs
    assert re.search(r"(?mi)^pytest-timeout==", (CI_REQS / "test.txt").read_text())


# ---------------------------------------------------------------------------
# supply S16 / F6: what CI installs, it installs by hash
# ---------------------------------------------------------------------------

def _pins(path: Path) -> dict[str, tuple[str, int]]:
    """{normalised name: (version, number of sha256 hashes)} of a pip file."""
    out, cur = {}, None
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)==(\S+)", line)
        if m:
            cur = re.sub(r"[-_.]+", "-", m.group(1)).lower()
            out[cur] = (m.group(2), len(re.findall(r"--hash=sha256:[0-9a-f]{64}\b", line)))
        else:
            assert cur and line.startswith(" "), f"{path.name}: unexpected line {line!r}"
            n = len(re.findall(r"--hash=sha256:[0-9a-f]{64}\b", line))
            assert n, f"{path.name}: a continuation line that is not a sha256 hash: {line!r}"
            out[cur] = (out[cur][0], out[cur][1] + n)
    return out


@pytest.mark.parametrize("name", ["test", "lint", "audit"])
def test_ci_tool_requirements_are_exact_and_hashed(name):
    wanted = _pins(CI_REQS / f"{name}.in")
    assert wanted, f"{name}.in pins nothing"
    got = _pins(CI_REQS / f"{name}.txt")
    for pkg, (version, _n) in wanted.items():
        assert got.get(pkg, ("", 0))[0] == version, f"{name}.txt is stale for {pkg}: relock.sh {name}"
    assert all(n >= 1 for _v, n in got.values()), "a pin with no hash"


def test_ci_test_tools_do_not_repin_what_the_agents_lock_pins():
    """test.txt is installed with --no-deps on top of the agents lock; a
    package in both would be two pins to keep in step by hand."""
    both = set(_pins(CI_REQS / "test.txt")) & set(_pins(REPO / "agents" / "requirements.lock"))
    assert not both, f"in both test.txt and agents/requirements.lock: {sorted(both)}"


def unhashed_pip_installs(text: str) -> list[str]:
    """`pip install` command lines that would take unpinned content."""
    cmds = re.sub(r"\\\n\s*", " ", text)          # join continued lines
    return [ln.strip() for ln in cmds.splitlines()
            if re.search(r"\bpip3?\b.*\binstall\b", ln) and not ln.lstrip().startswith("#")
            and "--require-hashes" not in ln]


@pytest.mark.parametrize("wf", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_no_workflow_installs_an_unpinned_package(wf):
    assert unhashed_pip_installs(wf.read_text()) == []


def test_the_unpinned_install_check_can_fail():
    bad = ("        run: |\n          python -m pip install -q -U pip pytest\n"
           "          # pip install ruff  (a comment)\n"
           "          python3 -m pip install -q --dry-run \\\n            --require-hashes -r a.lock\n")
    assert unhashed_pip_installs(bad) == ["python -m pip install -q -U pip pytest"]


def test_zig_is_downloaded_from_ziglang_and_checked_before_it_is_unpacked():
    (step,) = [s for job in _jobs(CI).values() for s in job["steps"]
               if "zig" in str(s.get("name", "")).lower()]
    assert re.fullmatch(r"[0-9a-f]{64}", step["env"]["ZIG_SHA256"])
    run = step["run"]
    assert "https://ziglang.org/download/${ZIG_VERSION}/" in run
    assert run.index("sha256sum -c") < run.index("tar -x") < run.index("GITHUB_PATH")
    # the job that gets zig is the one that runs the zig-gated tests
    (owner,) = [n for n, job in _jobs(CI).items() if step in job["steps"]]
    assert "pytest --timeout=300 tests/ -q" in " ".join(_runs(_jobs(CI)[owner]).split())


# ---------------------------------------------------------------------------
# supply S1: a touched lock is re-resolved, and never by the job that can push
# ---------------------------------------------------------------------------

def test_a_changed_lock_is_checked_against_a_fresh_resolve():
    checkers = [n for n, job in _jobs(QUALITY).items() if "pydeps.sh check" in _runs(job)]
    assert checkers, "no PR-quality job runs ./scripts/pydeps.sh check"
    job = _jobs(QUALITY)[checkers[0]]
    assert "agents/requirements.lock agents/requirements.txt" in _runs(job)
    assert not job.get("continue-on-error")


def test_the_relock_is_rechecked_by_a_job_that_cannot_push():
    jobs = _jobs(RELOCK)
    checkers = [n for n, job in jobs.items() if "pydeps.sh check" in _runs(job)]
    assert len(checkers) == 1
    perms = jobs[checkers[0]].get("permissions") or {}
    assert all(v != "write" for v in perms.values()), "the re-resolving job holds a write token"
    writers = [n for n, job in jobs.items()
               if any(v == "write" for v in (job.get("permissions") or {}).values())]
    assert writers and all(checkers[0] in jobs[w]["needs"] for w in writers)


# ---------------------------------------------------------------------------
# F28: ShellCheck sees every shell file
# ---------------------------------------------------------------------------

def test_shellcheck_covers_every_tracked_shell_script():
    run = _jobs(QUALITY)["shellcheck"]["steps"][-1]["run"]
    cmd = re.sub(r"\\\n\s*", " ", run)
    (line,) = [ln for ln in cmd.splitlines() if ln.strip().startswith("shellcheck -S")]
    covered = set()
    for pat in line.split()[3:]:
        covered |= {os.path.relpath(p, REPO) for p in glob.glob(str(REPO / pat))}
    tracked = subprocess.run(["git", "ls-files", "*.sh"], cwd=REPO, capture_output=True, text=True)
    if tracked.returncode != 0 or not tracked.stdout.strip():
        pytest.skip("not a git checkout")
    # scratch/ and research/ hold one-off campaign scripts, never shipped.
    want = {p for p in tracked.stdout.split() if not p.startswith(("scratch/", "research/", "docs/"))}
    assert sorted(want - covered) == [], "shell scripts ShellCheck never reads"


# ---------------------------------------------------------------------------
# F18 / F19: no green without running
# ---------------------------------------------------------------------------

def _sandbox_runner(tmp_path, *, pytest_installed: bool) -> subprocess.CompletedProcess:
    """tests/run_tests.sh in a scratch tree whose shell suites all pass and
    whose python3 does or does not have pytest."""
    tests = tmp_path / "repo" / "tests"
    tests.mkdir(parents=True)
    shutil.copy(RUNNER, tests / "run_tests.sh")
    for name in set(re.findall(r"test_\w+\.sh", RUNNER.read_text())):
        (tests / name).write_text("#!/bin/bash\nexit 0\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python3").write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == "-c" && "$2" == "import pytest" ]]; then exit %d; fi\n'
        'echo "python3 $*" >> "%s"\nexit 0\n' % (0 if pytest_installed else 1, tmp_path / "py.log"))
    (bindir / "python3").chmod(0o755)
    return subprocess.run(["bash", str(tests / "run_tests.sh")], capture_output=True, text=True,
                          timeout=120, env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
                                            "TMPDIR": str(tmp_path)})


def test_run_tests_fails_when_pytest_is_missing(tmp_path):
    r = _sandbox_runner(tmp_path, pytest_installed=False)
    assert r.returncode == 1, r.stdout[-800:]
    assert "ALL TESTS PASSED" not in r.stdout
    assert "pytest is not installed" in r.stdout and "did NOT RUN" in r.stdout
    assert not (tmp_path / "py.log").exists(), "something python ran without pytest (the unittest fallback?)"


def test_run_tests_passes_with_pytest_and_green_suites(tmp_path):
    """Control: the same sandbox with pytest present is ALL TESTS PASSED."""
    r = _sandbox_runner(tmp_path, pytest_installed=True)
    assert r.returncode == 0 and "ALL TESTS PASSED" in r.stdout, r.stdout[-800:]
    assert "-m pytest" in (tmp_path / "py.log").read_text()


@pytest.mark.parametrize("suite", ["test_hydra_sim.sh", "test_hydra_ready_parity.sh"])
def test_a_suite_that_cannot_run_is_a_failure_under_ci(suite, tmp_path):
    """With a python3 that cannot do anything the suite needs, it skips:
    exit 0 for a developer, exit 1 when CI=true."""
    (tmp_path / "python3").write_text("#!/bin/sh\nexit 1\n")
    (tmp_path / "python3").chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}

    def run(**extra):
        return subprocess.run(["bash", str(REPO / "tests" / suite)], capture_output=True, text=True,
                              timeout=120, env={**env, **extra})
    local = run()
    assert local.returncode == 0 and "SKIP" in local.stdout, local.stdout + local.stderr
    ci = run(CI="true")
    assert ci.returncode == 1 and "skipped under CI=true" in ci.stderr, ci.stdout + ci.stderr


def test_the_variant_audit_reports_failures_to_its_caller(tmp_path, monkeypatch):
    """tests/audit_variants.py printed [FAIL] and exited 0, so its CI step
    could not go red. audit() now returns what failed (and __main__ exits 1
    on it unless --advisory)."""
    import importlib
    import sys
    monkeypatch.syspath_prepend(str(REPO / "tests"))
    av = importlib.import_module("audit_variants")
    monkeypatch.setitem(av.TARGETS, "115_fft", ("fft", str(tmp_path / "work")))

    def one(script):
        monkeypatch.setattr(av, "load_tasks", lambda ids: [
            {"id": "115_fft_x", "language": "python", "setup": "true", "task": "",
             "validation": {"script": script}, "cleanup": "true"}])
        return av.audit(["115_fft"])

    assert one("true") == []
    (bad,) = one("false")
    assert bad.startswith("[FAIL] 115_fft_x")
    src = (REPO / "tests" / "audit_variants.py").read_text()
    assert "sys.exit(0 if advisory else 1)" in src
    sys.modules.pop("audit_variants", None)
