"""Research-verdict tooling in scratch/ and research/lowrank/.../32-*/ —
the guards a paired capability verdict is read through (2026-09-29 review,
research-stats-1/-2/-4/-5).

Every case here BUILDS its own rows, agent logs and stub repo; nothing reads
evals/results/, agents/logs/ or the real GPU. Each test names the defect it
pins and asserts the negative control next to it.

  * row_validity: a harness death (setup_failed / server_unhealthy / no exit
    code + zero elapsed on a live row) was filed as a benign "cache hit", a
    truncated row was stamped clean, and EAGAIN / dead-server fails were not
    looked for at all.
  * tier3_verdict: a pair's contaminated rows now drop that unit from the
    pair; --raw reproduces the as-registered read.
  * patchup_replace: the header (summary, fast_suite) is re-derived from the
    rows, and an older .bak is never overwritten.
  * e32_cap_verdict: refuses a paired p-value when a row is incomplete and
    uses the shared classifier (a setup death makes the row INVALID).
  * greedy_floor.sh: both floor runs are --no-cache; --single-slot runs a
    -np 1 server with --jobs 1 and stops it by pid, never by name — and
    refuses to start when something already serves the port, aborting unless
    its own live pid answers with total_slots == 1.
  * row_validity: a live zero-token fail with a normal exit (a dead server)
    voids the row; a missing agent-log dir reads as "unchecked", not clean.
"""

import copy
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = ROOT / "scratch"
E32 = ROOT / "research/lowrank/experiments/32-t117-gsq-head-to-head/e32_cap_verdict.py"
sys.path.insert(0, str(SCRATCH))
sys.path.insert(0, str(ROOT / "evals"))

import row_validity  # noqa: E402
import tier3_verdict  # noqa: E402

PIN = json.loads((ROOT / "evals/suites/v5-fast.json").read_text())


def _row(uid, passed=True, **kw):
    r = {"id": uid, "name": uid, "difficulty": "easy", "passed": passed,
         "elapsed_seconds": 120.0, "agent_exit_code": 0,
         "validation_output": "OK" if passed else "FAIL: wrong output",
         "tokens_prompt": 1000 + len(uid), "tokens_completion": 500 + len(uid),
         "iterations": 5}
    r.update(kw)
    return r


def _full_row(passed_ids=None):
    """A 112-unit v5-fast results dict; every unit passes unless listed."""
    ids = list(PIN["units"])
    tasks = [_row(u, passed=(passed_ids is None or u in passed_ids)) for u in ids]
    return {"timestamp": "2026-09-20T10:00:00", "model": "Stub Model",
            "suite_selection": "v5-fast", "tasks": tasks,
            "summary": {"total": len(tasks), "passed": sum(t["passed"] for t in tasks),
                        "failed": sum(not t["passed"] for t in tasks)}}


SETUP_DEATH = {"passed": False, "reason": "setup_failed", "elapsed_seconds": 0,
               "agent_exit_code": None, "tokens_completion": None, "tokens_prompt": None,
               "validation_output": ""}


def _run_validity(tmp_path, d, *extra):
    p = tmp_path / "row.json"
    p.write_text(json.dumps(d))
    r = subprocess.run([sys.executable, str(SCRATCH / "row_validity.py"), str(p),
                        "--agent-logs", "none", *extra],
                       capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


# --- row_validity --------------------------------------------------------------

def test_clean_full_row_is_clean(tmp_path):
    rc, out = _run_validity(tmp_path, _full_row())
    assert rc == 0, out
    assert "row clean" in out


def test_setup_deaths_make_the_row_invalid_not_cached(tmp_path):
    d = _full_row()
    for t in d["tasks"][:3]:
        t.update(SETUP_DEATH)
    rc, out = _run_validity(tmp_path, d)
    assert rc == 1, out
    assert "ROW INVALID" in out and "3 harness/setup death" in out
    assert "cached" not in out            # the old classifier's mislabel


def test_shape_without_reason_is_still_a_harness_death():
    # rc None + elapsed 0 on a LIVE row: the old rule's "cached" bucket.
    x = _row("u", passed=False, agent_exit_code=None, elapsed_seconds=0,
             tokens_completion=0)
    assert row_validity.kind(x) == "infra"
    # negative control: a real replay keeps its rc/elapsed and says so.
    y = _row("u", passed=False, from_cache=True)
    assert row_validity.kind(y) == "cached"
    assert row_validity.kind(_row("u", passed=False, agent_exit_code=-1,
                                  tokens_completion=0)) == "timeout"
    assert row_validity.kind(_row("u", passed=False, agent_exit_code=-9,
                                  tokens_completion=0)) == "killed"


def test_dead_server_zero_token_fails_void_the_row(tmp_path):
    # A unit run against a dead/SIGKILLed llama-server: "Connection error." on
    # every iteration, exit 0, zero tokens. The first fix filed it as benign
    # "other" and printed "row clean" (review repro rr_deadserver.json).
    d = _full_row()
    for t in d["tasks"][-40:]:
        t.update(passed=False, agent_exit_code=0, elapsed_seconds=31, tokens_completion=0,
                 tokens_prompt=0, validation_output="unable to load x.zig: FileNotFound")
    rc, out = _run_validity(tmp_path, d)
    assert rc == 1, out
    assert "ROW INVALID" in out and "40 live zero-token fail" in out and "dead=40" in out
    assert "other" not in out
    assert row_validity.kind(d["tasks"][-1]) == "dead"
    assert d["tasks"][-1]["id"] in row_validity.contaminated_ids(d, None)
    # negative controls: an honest fail WITH tokens, a timeout (rc -1), and a
    # zero-token PASS are not dead-server rows.
    assert row_validity.kind(_row("u", passed=False, agent_exit_code=0)) == "other"
    assert row_validity.kind(_row("u", passed=False, agent_exit_code=-1,
                                  tokens_completion=0)) == "timeout"
    assert row_validity.kind(_row("u", passed=True, tokens_completion=0)) == "other"
    d2 = _full_row()
    for t in d2["tasks"][:3]:
        t.update(passed=False, agent_exit_code=0, tokens_completion=900)
    assert _run_validity(tmp_path, d2)[0] == 0


def test_missing_agent_log_dir_reads_as_unchecked_not_clean(tmp_path):
    assert row_validity.load_log_index(str(tmp_path / "nope")) is None
    p = tmp_path / "row.json"; p.write_text(json.dumps(_full_row()))
    r = subprocess.run([sys.executable, str(SCRATCH / "row_validity.py"), str(p),
                        "--agent-logs", str(tmp_path / "nope")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "api=n/a" in r.stdout and "API-error axis unchecked" in r.stdout
    # negative control: an existing (empty) dir IS a checked axis
    (tmp_path / "logs").mkdir()
    r = subprocess.run([sys.executable, str(SCRATCH / "row_validity.py"), str(p),
                        "--agent-logs", str(tmp_path / "logs")], capture_output=True, text=True)
    assert "api=0" in r.stdout and "API-error axis unchecked" not in r.stdout


def test_honest_timeouts_do_not_void_a_row(tmp_path):
    d = _full_row()
    for t in d["tasks"][:5]:
        t.update(passed=False, agent_exit_code=-1, tokens_completion=0, elapsed_seconds=2100)
    rc, out = _run_validity(tmp_path, d)
    assert rc == 0, out
    assert "5 timeout" in out


def test_truncated_row_is_incomplete(tmp_path):
    d = _full_row()
    d["tasks"] = d["tasks"][:40] + [_row("zz", passed=False, reason="server_unhealthy",
                                         agent_exit_code=None, elapsed_seconds=0,
                                         tokens_completion=0, tokens_prompt=0)]
    rc, out = _run_validity(tmp_path, d)
    assert rc == 1, out
    assert "ROW INCOMPLETE" in out and "41" in out and "112" in out
    # an explicit --expected-n that matches is the negative control
    d2 = _full_row()
    d2["tasks"] = d2["tasks"][:40]
    d2.pop("suite_selection")
    rc2, out2 = _run_validity(tmp_path, d2, "--expected-n", "40")
    assert rc2 == 0, out2


def test_eagain_validation_death_is_invalid(tmp_path):
    d = _full_row()
    d["tasks"][7].update(passed=False, validation_output=(
        "error: unable to spawn LLD: SystemResources\n"))
    rc, out = _run_validity(tmp_path, d)
    assert rc == 1 and "EAGAIN" in out, out
    # a PASS that merely mentions the phrase is not a death
    d = _full_row()
    d["tasks"][7]["validation_output"] = "OK (fork: retry once, then fine)"
    assert _run_validity(tmp_path, d)[0] == 0


def _write_log(dirpath, name, tc, tp, errors, finish="2026-09-20T10:30:00"):
    lines = [{"type": "start", "task": "x", "timestamp": "2026-09-20T10:20:00"}]
    lines += [{"type": "error", "error": "Connection error.", "timestamp": finish}] * errors
    lines.append({"type": "max_iterations", "iterations": 20, "tokens_prompt": tp,
                  "tokens_completion": tc, "timestamp": finish})
    (dirpath / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_dead_server_fail_is_invalid_via_agent_log(tmp_path):
    logs = tmp_path / "logs"; logs.mkdir()
    d = _full_row()
    bad = d["tasks"][3]
    bad.update(passed=False, tokens_completion=4321, tokens_prompt=98765)
    _write_log(logs, "agent-dead.jsonl", 4321, 98765, errors=6)
    ok = d["tasks"][4]
    ok.update(passed=False, tokens_completion=1111, tokens_prompt=2222)
    _write_log(logs, "agent-honest.jsonl", 1111, 2222, errors=0)
    p = tmp_path / "row.json"; p.write_text(json.dumps(d))
    r = subprocess.run([sys.executable, str(SCRATCH / "row_validity.py"), str(p),
                        "--agent-logs", str(logs)], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    assert "API/connection errors" in r.stdout and bad["id"] in r.stdout
    assert ok["id"] not in r.stdout.split("API/connection errors")[1]


def test_zero_token_rows_are_not_matched_by_window_alone(tmp_path):
    # (0, 0) is shared by every dead-on-arrival run; a live row with no
    # cached_at must not inherit some other run's connection errors.
    logs = tmp_path / "logs"; logs.mkdir()
    _write_log(logs, "agent-other.jsonl", 0, 0, errors=20)
    idx = row_validity.load_log_index(str(logs))
    x = _row("u", passed=False, agent_exit_code=-1, tokens_completion=0, tokens_prompt=0)
    assert row_validity.api_errors(x, idx) is None
    # negative control: a replay pinned by cached_at within the window IS matched
    y = dict(x, cached_at="2026-09-20T10:30:05")
    assert row_validity.api_errors(y, idx) is not None


# --- tier3_verdict --------------------------------------------------------------

UNITS = [f"{i:02d}_u_f" for i in range(30)]


def _cell(tmp_path, name, passed, packs, contaminated=(), logs=None, stamp="2026-09-20T09:00:00"):
    tasks = []
    for k, u in enumerate(UNITS):
        tc, tp = 10_000 + 37 * k + (7 if packs else 0) + (3 if name.endswith("b") else 0), 50_000 + k
        tasks.append(_row(u, passed=u in passed, language="zig",
                          tokens_completion=tc, tokens_prompt=tp))
        if logs is not None and u in contaminated:
            _write_log(logs, f"agent-{name}-{u}.jsonl", tc, tp, errors=8,
                       finish="2026-09-20T09:30:00")
    d = {"timestamp": stamp, "model": "Stub", "tasks": tasks,
         "harness": {"greedy": True, "packs": {"zig": "abcd1234"} if packs else {}}}
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(d))
    return str(p)


def test_tier3_drops_rows_the_server_died_under(tmp_path):
    logs = tmp_path / "logs"; logs.mkdir()
    # P1 passes u0..u11, P0 passes u12..u13 -> raw 12 rescues vs 2 regressions
    # per pair. In pair a, P0 u0..u5 "failed" because the server was dead.
    p1 = set(UNITS[:12]); p0 = set(UNITS[12:14])
    cells = {
        "P0a": _cell(tmp_path, "P0a", p0, False, contaminated=UNITS[:6], logs=logs),
        "P1a": _cell(tmp_path, "P1a", p1, True, logs=logs),
        "P0b": _cell(tmp_path, "P0b", p0 | set(UNITS[:6]), False, logs=logs),
        "P1b": _cell(tmp_path, "P1b", p1, True, logs=logs),
    }
    base = [sys.executable, str(SCRATCH / "tier3_verdict.py"), "--p0", cells["P0a"], cells["P0b"],
            "--p1", cells["P1a"], cells["P1b"], "--c0", cells["P0b"], "--c1", cells["P1b"],
            "--agent-logs", str(logs)]
    clean = subprocess.run(base, capture_output=True, text=True).stdout
    raw = subprocess.run(base + ["--raw"], capture_output=True, text=True).stdout
    assert "rescues b=18 regressions c=4" in raw          # 12+6 / 2+2
    assert "pair 0: units=24" in clean                     # 6 dropped
    assert "rescues b=12 regressions c=4" in clean         # 6+6 / 2+2
    assert "P0a: 00_u_f [API x8" in clean
    kept = subprocess.run(base + ["--keep", "P0a:00_u_f"], capture_output=True, text=True).stdout
    assert "rescues b=13 regressions c=4" in kept and "KEPT" in kept


def test_tier3_eagain_rows_drop_without_agent_logs(tmp_path):
    c0 = _cell(tmp_path, "C0", set(UNITS[:10]), False)
    c1p = tmp_path / "C1.json"
    d = json.loads(Path(_cell(tmp_path, "C1", set(), True)).read_text())
    for t in d["tasks"][:8]:
        t["validation_output"] = "thread constructor failed: Resource temporarily unavailable"
    c1p.write_text(json.dumps(d))
    g0 = tier3_verdict.load_cell(c0, "zig", None)
    g1 = tier3_verdict.load_cell(str(c1p), "zig", None)
    assert len(g1["contaminated"]) == 8
    raw = tier3_verdict.paired(g0, g1)
    clean = tier3_verdict.paired(tier3_verdict.drop_contaminated(g0, "C0", set()),
                                 tier3_verdict.drop_contaminated(g1, "C1", set()))
    assert len(raw["c"]) == 10 and len(clean["c"]) == 2


# --- patchup_replace --------------------------------------------------------------

def test_patchup_recomputes_header_and_keeps_old_backups(tmp_path):
    d = _full_row()
    trip = PIN["tripwires"][0]
    ti = next(i for i, t in enumerate(d["tasks"]) if t["id"] == trip)
    d["tasks"][ti].update(passed=False, agent_exit_code=None, elapsed_seconds=0)
    d["summary"] = {"total": 112, "passed": 111, "failed": 1}
    d["fast_suite"] = {"suite": "v5-fast", "capability_imputed": 1.0,
                       "tripwire_failures": [trip]}
    main = tmp_path / "main.json"; main.write_text(json.dumps(d))
    (tmp_path / "main.json.bak").write_text("ORIGINAL")
    rerun = tmp_path / "rerun.json"
    rerun.write_text(json.dumps({"tasks": [_row(trip, passed=True)]}))
    r = subprocess.run([sys.executable, str(SCRATCH / "patchup_replace.py"), str(main),
                        str(rerun), trip, "test"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(main.read_text())
    assert out["summary"] == {"total": 112, "passed": 112, "failed": 0}
    assert out["fast_suite"]["tripwire_failures"] == []
    assert out["fast_suite"]["capability_imputed"] > 1.0
    assert (tmp_path / "main.json.bak").read_text() == "ORIGINAL"
    assert json.loads((tmp_path / "main.json.bak.1").read_text())["summary"]["passed"] == 111
    # --recompute on an already-consistent file is a no-op on the header
    before = copy.deepcopy(out)
    subprocess.run([sys.executable, str(SCRATCH / "patchup_replace.py"), "--recompute",
                    str(main)], check=True, capture_output=True)
    after = json.loads(main.read_text())
    assert after["summary"] == before["summary"] and after["fast_suite"] == before["fast_suite"]


# --- e32_cap_verdict --------------------------------------------------------------

def _e32(tmp_path, a, b):
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a)); pb.write_text(json.dumps(b))
    env = dict(os.environ, OPENBEAST_ROOT=str(ROOT))
    return subprocess.run([sys.executable, str(E32), str(pa), str(pb), "--agent-logs", "none"],
                          capture_output=True, text=True, env=env)


def test_e32_refuses_paired_p_on_an_incomplete_row(tmp_path):
    a = _full_row(); a["model"] = "A"
    b = _full_row(); b["model"] = "B"; b["tasks"] = b["tasks"][:41]
    r = _e32(tmp_path, a, b)
    # a refused verdict is not a success for the campaign step recording rc
    assert r.returncode == 1, r.stdout + r.stderr
    assert "NO PAIRED VERDICT" in r.stdout and "p=" not in r.stdout.split("PAIRED")[1]
    # negative control: two complete, valid rows get their McNemar lines, rc 0
    b = _full_row(); b["model"] = "B"
    r = _e32(tmp_path, a, b)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NO PAIRED VERDICT" not in r.stdout and "ALL " in r.stdout


def test_e32_setup_death_is_invalid_not_a_cache_hit(tmp_path):
    a = _full_row(); a["model"] = "A"
    b = _full_row(); b["model"] = "B"
    for t in b["tasks"][:4]:
        t.update(SETUP_DEATH)
    r = _e32(tmp_path, a, b)
    assert "validity A=True B=False" in r.stdout, r.stdout
    assert r.returncode == 1, r.stdout + r.stderr      # an INVALID row fails the step
    assert "4 cached" not in r.stdout


# --- greedy_floor.sh --------------------------------------------------------------

@pytest.fixture
def floor_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "evals").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scratch").mkdir()
    rec = tmp_path / "calls.txt"
    stub_py = textwrap.dedent(f"""\
        import os, sys
        with open({str(rec)!r}, "a") as f:
            f.write(os.path.basename(sys.argv[0]) + " " + " ".join(sys.argv[1:])
                    + " GREEDY=" + os.environ.get("OPENBEAST_EVAL_GREEDY", "") + "\\n")
        """)
    (repo / "evals/benchmark_all.py").write_text(stub_py)
    (repo / "evals/run_eval.py").write_text(stub_py)
    serve = repo / "scripts/serve-qwen38-27b-uncensored-q5.sh"
    serve.write_text(f'#!/bin/bash\necho "serve $*" >> {rec}\necho $$ > {tmp_path}/serve.pid\n'
                     '[ -n "${SERVE_DIES:-}" ] && exit 1\nexec sleep 30\n')
    serve.chmod(0o755)
    shutil_copy = (SCRATCH / "greedy_floor.sh").read_text()
    (repo / "scratch/greedy_floor.sh").write_text(shutil_copy)
    bin_ = tmp_path / "bin"; bin_.mkdir()
    # curl stub: CURL_MODE=own (default) answers only while OUR stub server
    # lives; foreign = something already serves the port; after-serve = a
    # foreign server answers once ours was launched (and has died).
    # /props reports STUB_SLOTS (default 1).
    pidf = tmp_path / "serve.pid"
    curl = textwrap.dedent(f"""\
        url="${{@: -1}}"
        pid() {{ cat {pidf} 2>/dev/null; }}
        case "${{CURL_MODE:-own}}" in
          foreign) ;;
          after-serve)
            [ -f {pidf} ] || exit 7
            for _ in $(seq 1 100); do kill -0 "$(pid)" 2>/dev/null || break; sleep 0.05; done ;;
          own) [ -f {pidf} ] && kill -0 "$(pid)" 2>/dev/null || exit 7 ;;
        esac
        case "$url" in */props) echo "{{\\"total_slots\\": ${{STUB_SLOTS:-1}}}}" ;; esac
        exit 0""")
    for name, body in (("curl", curl), ("pkill", f'echo "pkill $*" >> {rec}')):
        f = bin_ / name
        f.write_text(f"#!/bin/bash\n{body}\n"); f.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", OB=str(repo))
    return repo, rec, env


def test_greedy_floor_default_runs_both_rows_live(floor_repo):
    repo, rec, env = floor_repo
    r = subprocess.run(["bash", str(repo / "scratch/greedy_floor.sh")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = [c for c in rec.read_text().splitlines() if c.startswith("benchmark_all")]
    assert len(calls) == 2
    assert all("--no-cache" in c and "--greedy" in c for c in calls)   # run 1 too


def test_greedy_floor_single_slot_is_single_slot(floor_repo):
    repo, rec, env = floor_repo
    r = subprocess.run(["bash", str(repo / "scratch/greedy_floor.sh"), "--single-slot"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = rec.read_text().splitlines()
    assert any(ln.startswith("serve ") and "-np 1" in ln and "-p 8080" in ln for ln in lines)
    runs = [c for c in lines if c.startswith("run_eval")]
    assert len(runs) == 2
    assert all("--jobs 1" in c and "--no-cache" in c and c.endswith("GREEDY=1") for c in runs)
    assert not any(ln.startswith("pkill") for ln in lines)     # stopped by pid only
    # the stub server (exec'd sleep, same pid) must not outlive the script
    pid = int((rec.parent / "serve.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _single(floor_repo, **env_over):
    repo, rec, env = floor_repo
    r = subprocess.run(["bash", str(repo / "scratch/greedy_floor.sh"), "--single-slot"],
                       env=dict(env, **env_over), capture_output=True, text=True, timeout=60)
    lines = rec.read_text().splitlines() if rec.exists() else []
    return r, lines


def test_greedy_floor_single_slot_refuses_a_server_already_up(floor_repo):
    # The stack up on :8080: the first cut's /health poll succeeded at once
    # and both "single-slot" rows ran against the -np 6 stack.
    r, lines = _single(floor_repo, CURL_MODE="foreign")
    assert r.returncode == 3 and "REFUSED" in r.stderr, r.stdout + r.stderr
    assert not any(ln.startswith(("serve ", "run_eval", "pkill")) for ln in lines)


def test_greedy_floor_single_slot_aborts_when_its_server_dies(floor_repo):
    # Ours dies on bind/VRAM while some other server answers the port.
    r, lines = _single(floor_repo, CURL_MODE="after-serve", SERVE_DIES="1")
    assert r.returncode == 1 and "SERVER FAILED" in r.stderr, r.stdout + r.stderr
    assert any(ln.startswith("serve ") for ln in lines)
    assert not any(ln.startswith("run_eval") for ln in lines)


def test_greedy_floor_single_slot_aborts_on_wrong_slot_count(floor_repo):
    r, lines = _single(floor_repo, STUB_SLOTS="6")
    assert r.returncode == 1 and "total_slots='6'" in r.stderr, r.stdout + r.stderr
    assert not any(ln.startswith("run_eval") for ln in lines)
    pid = int((floor_repo[1].parent / "serve.pid").read_text())
    with pytest.raises(ProcessLookupError):     # our server stopped, by pid
        os.kill(pid, 0)


def test_greedy_floor_rejects_unknown_mode(floor_repo):
    repo, rec, env = floor_repo
    r = subprocess.run(["bash", str(repo / "scratch/greedy_floor.sh"), "--bogus"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and not rec.exists()
