"""use-model.sh (rig side): records INFERENCE_MODEL only behind a passing
conformance report for that id at this INFERENCE_URL, keeps openbeast.conf's
mode, prints an opencode provider entry that references the key by env var
(never the key), and agents/runner.py's default model follows
OPENBEAST_INFERENCE_MODEL.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
# first: onboarding_helpers puts scripts/backends/pylib on sys.path
from onboarding_helpers import REPO, clean_env, hf_profile  # noqa: E402
import use_model  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    clean_env(monkeypatch)


def report(tmp_path, model="brand-new", ok=True, url="http://10.0.0.5:8000", mml=65536, name="latest") -> Path:
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps({"url": url, "ok": ok, "when": "20260929T000000Z",
                             "facts": {"model": model, "max_model_len": mml},
                             "results": [{"name": "tools", "status": "pass" if ok else "fail", "required": True}]}))
    return p


def test_use_model_records_inference_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENBEAST_INFERENCE_URL", "http://10.0.0.5:8000")
    monkeypatch.setenv("OPENBEAST_INFERENCE_BACKEND", "vllm")
    monkeypatch.setenv("LLAMA_API_KEY", "sk-SECRET-9")
    conf = tmp_path / "openbeast.conf"
    conf.write_text("INFERENCE_BACKEND=vllm\nINFERENCE_MODEL=old-one\nX=1\nINFERENCE_MODEL=older\n")
    conf.chmod(0o600)
    prof = hf_profile(tmp_path)
    rc = use_model.main(["--profile", str(prof), "--report", str(report(tmp_path)), "--conf", str(conf)])
    out = capsys.readouterr().out
    assert rc == 0
    lines = conf.read_text().splitlines()
    assert lines.count('INFERENCE_MODEL="brand-new"') == 1 and not any("old" in x for x in lines)
    assert "X=1" in lines and stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert '"baseURL": "http://10.0.0.5:8000/v1"' in out and "{env:OPENBEAST_API_KEY}" in out
    assert "sk-SECRET-9" not in out and '"context": 65536' in out


def test_use_model_refuses_without_a_matching_passing_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENBEAST_INFERENCE_URL", "http://10.0.0.5:8000")
    conf = tmp_path / "openbeast.conf"
    for rep, why in ((report(tmp_path, "m1", ok=False, name="r1"), "FAILED"),
                     (report(tmp_path, "other", name="r2"), "not 'm1'"),
                     (report(tmp_path, "m1", url="http://elsewhere:1", name="r3"), "INFERENCE_URL is"),
                     (tmp_path / "nope.json", "no conformance report")):
        assert use_model.main(["--model", "m1", "--report", str(rep), "--conf", str(conf)]) == 1
        assert why in capsys.readouterr().out
    assert not conf.exists()
    assert use_model.main(["--model", "m1", "--report", str(tmp_path / "nope.json"), "--conf", str(conf),
                           "--force"]) == 0
    assert 'INFERENCE_MODEL="m1"' in conf.read_text() and stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert use_model.main(["--model", 'bad"id', "--conf", str(conf), "--force"]) == 1


def test_use_model_tensorfold_snippet_has_no_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENBEAST_INFERENCE_URL", "http://10.0.0.5:8000")
    monkeypatch.setenv("OPENBEAST_INFERENCE_BACKEND", "tensorfold")
    monkeypatch.setenv("LLAMA_API_KEY", "sk-x")
    out_file = tmp_path / "oc.json"
    assert use_model.main(["--model", "brand-new", "--report", str(report(tmp_path)), "--conf",
                           str(tmp_path / "c.conf"), "--opencode-out", str(out_file)]) == 0
    snip = json.loads(out_file.read_text())
    opts = snip["provider"]["openbeast-inference"]["options"]
    assert "apiKey" not in opts and opts["baseURL"] == "http://10.0.0.5:8000/v1"


def test_runner_default_model_follows_env():
    code = "import runner; print(runner.DEFAULT_MODEL)"
    base = {k: v for k, v in os.environ.items() if k != "OPENBEAST_INFERENCE_MODEL"}
    try:
        import openai  # noqa: F401
    except ImportError:
        pytest.skip("runner.py needs openai")
    a = subprocess.run([sys.executable, "-c", code], cwd=REPO / "agents", capture_output=True, text=True,
                       timeout=60, env={**base, "OPENBEAST_INFERENCE_MODEL": "brand-new"})
    b = subprocess.run([sys.executable, "-c", code], cwd=REPO / "agents", capture_output=True, text=True,
                       timeout=60, env=base)
    assert a.stdout.strip().splitlines()[-1] == "brand-new", a.stderr
    assert b.stdout.strip().splitlines()[-1] == "qwen-27b-q5", b.stderr
