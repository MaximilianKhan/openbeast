"""Fixture roots another uid could have planted are refused, not reused.

Every fixture is a fixed name under world-writable /tmp and setup's
`mkdir -p` accepts an existing directory whoever made it, so another uid
could pre-create /tmp/eval_<x> 0777 with symlinks inside and have the
operator's setup write through them, or rewrite the solution before
validation (review finding campaign-evals: /tmp fixture squatting). A
different uid cannot be faked without root, so the uid half is covered by
the symlink and world-writable cases, which take the same refusal path.
"""

import importlib
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))


@pytest.fixture
def root():
    name = f"/tmp/eval_rootguard_{uuid.uuid4().hex[:8]}"
    yield name
    if os.path.islink(name):
        os.unlink(name)
    elif os.path.isdir(name):
        shutil.rmtree(name, ignore_errors=True)


def _task(root):
    return {"id": "t", "setup": f"mkdir -p {root} && printf x > {root}/in.txt",
            "task": f"fix {root}/sol.py", "validation": {"script": f"cat {root}/in.txt"}}


def _re():
    return importlib.import_module("run_eval")


def test_absent_and_own_dirs_are_fine(root):
    re_ = _re()
    assert re_.unsafe_fixture_roots(_task(root)) == []
    os.mkdir(root, 0o755)
    os.chmod(root, 0o755)
    assert re_.unsafe_fixture_roots(_task(root)) == []
    # Ubuntu's umask 0002: group-writable under OUR primary group is ours.
    os.chown(root, -1, os.getgid())
    os.chmod(root, 0o775)
    assert re_.unsafe_fixture_roots(_task(root)) == []


def test_world_writable_root_is_refused(root):
    os.mkdir(root)
    os.chmod(root, 0o777)
    bad = _re().unsafe_fixture_roots(_task(root))
    assert bad and root in bad[0] and "writable" in bad[0]


def test_symlinked_root_is_refused(root, tmp_path):
    os.symlink(tmp_path, root)
    bad = _re().unsafe_fixture_roots(_task(root))
    assert bad and "symlink" in bad[0]


def test_run_eval_never_runs_setup_into_a_planted_root(root, tmp_path, monkeypatch):
    """End to end through run_eval: setup and the agent never run against a
    planted root, and the unit is recorded as a setup failure (an infra
    reason — never banked, never scored as the model's FAIL)."""
    import json
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    re_ = importlib.import_module("run_eval")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_alpha.json").write_text(json.dumps({
        "id": "01_alpha", "name": "alpha", "difficulty": "easy", "max_iter": 3,
        **{k: v for k, v in _task(root).items() if k != "id"},
        "validation": {"type": "bash", "script": "false"}}))
    re_.TASKS_DIR, re_.RESULTS_DIR = str(tasks), str(tmp_path / "results")
    os.mkdir(root)
    os.chmod(root, 0o777)
    ran = []
    monkeypatch.setattr(re_, "run_setup", lambda task, log=print: ran.append("setup") or True)
    monkeypatch.setattr(re_, "run_agent", lambda *a, **k: ran.append("agent"))
    monkeypatch.setattr(re_, "capture_server_config", lambda *a, **k: {})
    monkeypatch.setattr(re_, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(re_, "capture_inference_engine_info", lambda: {})
    res = re_.run_eval(model_name="m")
    assert ran == []
    assert res["tasks"][0]["reason"] == "setup_failed"
    assert not (cache.CACHE_DIR.exists() and list(cache.CACHE_DIR.glob("*.json")))
