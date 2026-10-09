"""R1 path guard tests (2026-09-10): warn on wrong-place writes, refuse
task_done while expected task files are missing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
import tools  # noqa: E402


def test_inert_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENBEAST_TASK_PATHS", raising=False)
    out = tools.write_file(str(tmp_path / "x.py"), "x = 1\n")
    assert "path check" not in out
    assert tools.task_done("done").startswith("TASK_DONE")


def test_warns_on_wrong_place_same_basename(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("OPENBEAST_TASK_PATHS",
                       json.dumps([str(tmp_path / "expected" / "sol.py")]))
    out = tools.write_file(str(tmp_path / "elsewhere" / "sol.py"), "x = 1\n")
    assert "path check" in out and "expected/sol.py" in out


def test_no_warning_at_expected_path(tmp_path, monkeypatch):
    import json
    target = tmp_path / "eval_x" / "sol.py"
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(target)]))
    out = tools.write_file(str(target), "x = 1\n")
    assert "path check" not in out


def test_task_done_refuses_on_missing_expected_file(tmp_path, monkeypatch):
    import json
    missing = tmp_path / "eval_y" / "answer.zig"
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(missing)]))
    out = tools.task_done("all good")
    assert out.startswith("NOT DONE") and "answer.zig" in out


def test_task_done_ignores_extensionless_artifacts(tmp_path, monkeypatch):
    import json
    src = tmp_path / "eval_z" / "prog.zig"
    src.parent.mkdir(parents=True)
    src.write_text("pub fn main() void {}\n")
    binary = tmp_path / "eval_z" / "prog"  # built by validation, not the agent
    monkeypatch.setenv("OPENBEAST_TASK_PATHS",
                       json.dumps([str(src), str(binary)]))
    assert tools.task_done("done").startswith("TASK_DONE")


def test_task_done_passes_when_files_exist(tmp_path, monkeypatch):
    import json
    f = tmp_path / "eval_w" / "s.py"
    f.parent.mkdir(parents=True)
    f.write_text("x = 1\n")
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(f)]))
    assert tools.task_done("done").startswith("TASK_DONE")


# ---------------------------------------------------------------------------
# Suite v4.1 (review 2026-10-09, evals F10): the refusal must REACH the model,
# the path list must come from the task text, and a wrong list must not be
# able to dead-end a unit.
# ---------------------------------------------------------------------------

import json  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(ROOT / "evals"))
import runner  # noqa: E402
import run_eval  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_refusal_budget(monkeypatch):
    monkeypatch.setattr(tools, "_task_done_refusals", 0, raising=False)


class _Msg:
    def __init__(self, tool_calls):
        self.content = ""
        self.tool_calls = tool_calls


class _TC:
    def __init__(self, id_, name, args):
        self.id = id_
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()


class _Fake:
    """Scripted model: each step is a list of (tool, args) calls."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.requests.append([dict(m) for m in kw["messages"]])
        calls = self.script.pop(0)
        msg = _Msg([_TC(f"c{len(self.requests)}-{i}", n, a) for i, (n, a) in enumerate(calls)])
        return type("R", (), {"usage": None, "choices": [
            type("C", (), {"message": msg, "finish_reason": "tool_calls"})()]})()


def _drive(tmp_path, monkeypatch, script, paths=None, max_iter=8):
    for k in ("OPENBEAST_EVAL", "OPENBEAST_EVAL_WALL_S", "OPENBEAST_TASK_PATHS",
              "OPENBEAST_AGENT_MAX_TOKENS", "OPENBEAST_REASONING_BUDGET", "REASONING_BUDGET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(runner, "_CONF_PATH", tmp_path / "openbeast.conf", raising=False)
    if paths is not None:
        monkeypatch.setenv("OPENBEAST_EVAL", "1")
        monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps(paths))
    fake = _Fake(script)
    monkeypatch.setattr(runner, "OpenAI", lambda **kw: fake)
    log = tmp_path / "run.jsonl"
    out = runner.run_agent("task", max_iter=max_iter, log_file=str(log),
                           system_prompt="s", workdir=str(tmp_path))
    events = [json.loads(ln) for ln in log.read_text().splitlines()]
    return out, fake, events


def _tool_results(request):
    return [m["content"] for m in request if m["role"] == "tool"]


def test_refusal_reaches_the_model_and_the_run_continues(tmp_path, monkeypatch):
    """The model wrote the file in the wrong place and said done. It must
    read the refusal, fix the path, and only then complete."""
    want = tmp_path / "eval_x" / "sol.zig"
    script = [
        [("write_file", {"path": str(tmp_path / "sol.zig"), "content": "x"})],
        [("task_done", {"summary": "first"})],
        [("write_file", {"path": str(want), "content": "x"})],
        [("task_done", {"summary": "second"})],
    ]
    out, fake, events = _drive(tmp_path, monkeypatch, script, paths=[str(want)])
    assert out == "second"
    assert len(fake.requests) == 4, "the run did not end on the refused call"
    refusal = _tool_results(fake.requests[2])[-1]
    assert refusal.startswith("NOT DONE") and "sol.zig" in refusal
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1 and done[0]["iterations"] == 4
    assert want.exists()


def test_a_wrong_path_list_cannot_dead_end_a_unit(tmp_path, monkeypatch):
    """The v4 list held paths that can never exist (`gemm.zig.` with the
    sentence's full stop). The guard refuses twice, then lets go."""
    impossible = str(tmp_path / "eval_gemm" / "gemm.zig.")
    script = [[("task_done", {"summary": f"try {k}"})] for k in range(1, 8)]
    out, fake, events = _drive(tmp_path, monkeypatch, script, paths=[impossible])
    assert out == "try 3"
    assert len(fake.requests) == 3, "two refusals, then completion: no dead end"
    assert [e["type"] for e in events].count("done") == 1


def test_refusals_are_bounded_per_run_not_per_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(tmp_path / "a.py")]))
    assert tools.task_done("x").startswith("NOT DONE")
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(tmp_path / "b.py")]))
    assert tools.task_done("x").startswith("NOT DONE")
    assert tools.task_done("x") == "TASK_DONE: x"


def test_without_the_guard_task_done_completes_on_the_first_call(tmp_path, monkeypatch):
    """Control (no OPENBEAST_TASK_PATHS — every non-eval run): unchanged."""
    out, fake, _ = _drive(tmp_path, monkeypatch, [[("task_done", {"summary": "fin"})]])
    assert out == "fin" and len(fake.requests) == 1


def test_a_malformed_task_done_still_ends_the_run_as_before(tmp_path, monkeypatch):
    """Control: only the guard's refusal keeps the loop going. A task_done
    with bad arguments ended the run before v4.1 and still does."""
    out, fake, _ = _drive(tmp_path, monkeypatch, [[("task_done", {"wrong": 1})]])
    assert len(fake.requests) == 1
    assert out.startswith("Error: bad tool call to task_done")


# --- the path list ----------------------------------------------------------

@pytest.fixture(scope="module")
def units():
    return {t["id"]: t for t in run_eval.load_tasks()}


def test_paths_come_from_the_task_text_without_sentence_punctuation():
    task = {"task": "Create /tmp/eval_gemm/gemm.zig. Then read (/tmp/eval_gemm/input.txt), "
                    "/tmp/eval_gemm/a.py; and /tmp/eval_gemm/b.py: done.",
            "setup": "mkdir -p /tmp/eval_gemm && touch /tmp/eval_gemm/fixture.bin",
            "validation": {"script": "./gemm > /tmp/eval_gemm/out.txt"},
            "cleanup": "rm -rf /tmp/eval_gemm"}
    assert run_eval.task_expected_paths(task) == [
        "/tmp/eval_gemm/a.py", "/tmp/eval_gemm/b.py",
        "/tmp/eval_gemm/gemm.zig", "/tmp/eval_gemm/input.txt"]


def test_the_units_the_review_named_have_usable_paths(units):
    for uid, src in (("122_gemm_blocked_a", "gemm.go"), ("122_gemm_blocked_e", "gemm.zig")):
        paths = run_eval.task_expected_paths(units[uid])
        assert f"/tmp/eval_gemm/{src}" in paths, paths
        assert not any(p.endswith(".") for p in paths)
    assert run_eval.task_expected_paths(units["04_write_tests"]) == [
        "/tmp/eval_calc/calc.py", "/tmp/eval_calc/test_calc.py"]
    # Validator-only outputs are gone.
    for uid in ("159_ntt_convolution_a", "158_karatsuba_bytes_f"):
        assert not any(p.endswith("out.txt") for p in run_eval.task_expected_paths(units[uid]))


def test_every_unit_has_a_clean_list_taken_from_its_text(units):
    assert len(units) == 291
    for uid, t in units.items():
        paths = run_eval.task_expected_paths(t)
        assert paths, f"{uid}: the eval marker L2 needs at least one path"
        for p in paths:
            assert p in t["task"], (uid, p)
            assert p[-1] not in ".,;:)", (uid, p)


def _sandboxed(script: str, root) -> str:
    return script.replace("/tmp/eval", f"{root}/eval")


def _guarded_missing(unit, root):
    return [p for p in (_sandboxed(q, root) for q in run_eval.task_expected_paths(unit))
            if "." in p.rsplit("/", 1)[-1] and not Path(p).exists()]


@pytest.mark.parametrize("uid,deliverable", [
    ("122_gemm_blocked_a", "eval_gemm/gemm.go"),
    ("122_gemm_blocked_e", "eval_gemm/gemm.zig"),
    ("04_write_tests", "eval_calc/test_calc.py"),
])
def test_named_units_complete_once_the_deliverable_exists(units, tmp_path, monkeypatch,
                                                          uid, deliverable):
    """Real spec, real setup (redirected into tmp_path), real guard: the only
    file it waits for is the one the task asks for."""
    unit = units[uid]
    subprocess.run(["bash", "-c", _sandboxed(unit["setup"], tmp_path)], check=True)
    assert _guarded_missing(unit, tmp_path) == [str(tmp_path / deliverable)]
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps(
        [_sandboxed(p, tmp_path) for p in run_eval.task_expected_paths(unit)]))
    assert tools.task_done("early").startswith("NOT DONE")
    (tmp_path / deliverable).write_text("// solution\n")
    assert tools.task_done("now") == "TASK_DONE: now"


def test_no_variant_unit_waits_for_anything_but_its_source_file(units, tmp_path):
    """All 185 variant units: after setup, the guard is missing exactly one
    file and it is the solution source the validator compiles."""
    sys.path.insert(0, str(ROOT / "tests"))
    import audit_variants
    seen = 0
    for uid, unit in sorted(units.items()):
        base = unit.get("base_id")
        if not base:
            continue
        stem, dest = audit_variants.TARGETS[base]
        root = tmp_path / uid
        root.mkdir()
        subprocess.run(["bash", "-c", _sandboxed(unit["setup"], root)], check=True,
                       capture_output=True)
        src = f"{dest}/{stem}.{audit_variants.LANG_EXT[unit['language']]}"
        assert _guarded_missing(unit, root) == [_sandboxed(src, root)], uid
        seen += 1
    assert seen == 185
