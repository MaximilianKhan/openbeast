"""Per-model profiles (scripts/backends/models/<name>.env, pylib/obprofile.py):
the parser refuses what would serve something other than what you meant —
branch/tag revisions, remote code without an acknowledgement bound to the
revision, unknown or repeated keys, flags that bypass a named key — and
treats every value as data, never code. Hermetic: tmp files only.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
# first: onboarding_helpers puts scripts/backends/pylib on sys.path
from onboarding_helpers import PYLIB, SHA, SHA2, clean_env, write_profile  # noqa: E402
import obprofile  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    clean_env(monkeypatch)


def load_body(tmp_path, body: str, backend=None, name="p"):
    return obprofile.load(str(write_profile(tmp_path / "pp", name, body)), backend)


BASE = f"BACKEND=vllm\nSOURCE=acme/Brand-New\nREVISION={SHA}\nSERVED_MODEL_NAME=x\n"


@pytest.mark.parametrize("rev", ["main", "v1.0", SHA[:7], SHA.upper(), SHA + "0"])
def test_profile_refuses_non_sha_revisions(tmp_path, rev):
    with pytest.raises(obprofile.ProfileError, match="REVISION"):
        load_body(tmp_path, BASE.replace(SHA, rev))


def test_profile_refuses_hub_source_without_revision(tmp_path):
    with pytest.raises(obprofile.ProfileError, match="REVISION is not set"):
        load_body(tmp_path, BASE.replace(f"REVISION={SHA}\n", ""))


def test_profile_trust_remote_code_needs_ack_bound_to_revision(tmp_path):
    with pytest.raises(obprofile.ProfileError, match="executes Python shipped in the model repo"):
        load_body(tmp_path, BASE + "TRUST_REMOTE_CODE=true\n")
    with pytest.raises(obprofile.ProfileError, match="TRUST_REMOTE_CODE_ACK"):
        load_body(tmp_path, BASE + f"TRUST_REMOTE_CODE=true\nTRUST_REMOTE_CODE_ACK={SHA2}\n")
    p = load_body(tmp_path, BASE + f"TRUST_REMOTE_CODE=true\nTRUST_REMOTE_CODE_ACK={SHA}\n")
    assert p.trust_remote_code


@pytest.mark.parametrize("body,msg", [
    (BASE + "MODLE=typo\n", "unknown key MODLE"),
    (BASE + "SERVED_MODEL_NAME=y\n", "set twice"),
    (BASE + "export TOOL_CALL_PARSER=hermes\n", "'export' is shell"),
    (BASE + "echo hi\n", "not KEY=VALUE"),
    (BASE + 'EXTRA_ARGS=["--trust-remote-code"]\n', "may not contain --trust-remote-code"),
    (BASE + 'EXTRA_ARGS=["--api-key","k"]\n', "may not contain --api-key"),
    (BASE + "EXTRA_ARGS=--foo --bar\n", "JSON array"),
    (BASE + "TENSOR_PARALLEL_SIZE=4\n", "TENSOR_PARALLEL_SIZE"),
    (BASE + "GPU_MEMORY_UTILIZATION=0.99\n", "GPU_MEMORY_UTILIZATION"),
    (BASE + "TOOL_CALL_PARSER=a;rm -rf\n", "characters no vLLM name has"),
    (BASE + "CHAT_TEMPLATE=missing.jinja\n", "does not exist"),
    (BASE + 'SPECULATIVE_CONFIG=[1]\n', "JSON object"),
    (BASE + "DRAFTER_SOURCE=acme/d\n", "DRAFTER_REVISION"),
    (BASE.replace("SOURCE=acme/Brand-New", "SOURCE=relative/dir/x"), "neither owner/name"),
    (BASE + 'TOOL_CALL_PARSER="unbalanced\n', "unbalanced quote"),
])
def test_profile_junk_is_refused(tmp_path, body, msg):
    with pytest.raises(obprofile.ProfileError, match=msg):
        load_body(tmp_path, body)


def test_profile_backend_specific_keys(tmp_path):
    tf = f"BACKEND=tensorfold\nSOURCE=acme/X\nREVISION={SHA}\nSERVED_MODEL_NAME=x\n"
    with pytest.raises(obprofile.ProfileError, match="TensorFold has no equivalent"):
        load_body(tmp_path, tf + "TOOL_CALL_PARSER=hermes\n")
    with pytest.raises(obprofile.ProfileError, match="TensorFold setting"):
        load_body(tmp_path, BASE + "TENSORFOLD_PARALLEL=4\n")
    with pytest.raises(obprofile.ProfileError, match="launcher cannot serve it"):
        load_body(tmp_path, tf, backend="vllm")
    assert load_body(tmp_path, tf + "TENSORFOLD_PARALLEL=4\n", backend="tensorfold").backend == "tensorfold"


def test_profile_values_are_data_not_code(tmp_path):
    canary = tmp_path / "PWNED"
    body = BASE.replace("SERVED_MODEL_NAME=x", f"SERVED_MODEL_NAME=$(touch {canary}); `touch {canary}`")
    p = load_body(tmp_path, body)
    assert p.get("SERVED_MODEL_NAME").startswith("$(touch") and not canary.exists()
    out = subprocess.run([sys.executable, str(PYLIB / "obprofile.py"), "resolve", str(p.path)],
                         capture_output=True, timeout=30)
    pairs = out.stdout.split(b"\0")
    assert out.returncode == 0 and b"SERVED_MODEL_NAME" in pairs and not canary.exists()
    assert pairs[pairs.index(b"SERVED_MODEL_NAME") + 1].startswith(b"$(touch")


def test_profile_grammar_quotes_comments_extra_args(tmp_path):
    p = load_body(tmp_path, BASE + "MAX_MODEL_LEN=65536   # fits\n"
                  "EXTRA_ARGS='[\"--kv-cache-dtype\",\"fp8\"]'\nTOOL_CALL_PARSER=  # none yet\n")
    assert p.get("MAX_MODEL_LEN") == "65536" and p.extra_args == ["--kv-cache-dtype", "fp8"]
    assert p.get("TOOL_CALL_PARSER") == ""


def test_profile_unregistered_parser_warns_not_refuses(tmp_path):
    p = load_body(tmp_path, BASE + "TOOL_CALL_PARSER=future_parser_v9\n")
    assert any("not registered in vLLM" in w for w in p.warnings)


def test_profile_local_source_needs_no_revision(tmp_path):
    d = tmp_path / "ckpt"
    d.mkdir()
    p = load_body(tmp_path, f"BACKEND=vllm\nSOURCE={d}\nSERVED_MODEL_NAME=x\n")
    assert not p.is_hf


def test_shipped_profiles_and_template():
    for f in sorted((PYLIB.parent / "models").glob("*.env")):
        if f.stem == "TEMPLATE":
            text = f.read_text()
            keys = {ln.split("=", 1)[0] for ln in text.splitlines() if "=" in ln and not ln.startswith("#")}
            assert keys == set(obprofile.KEYS), "TEMPLATE.env documents exactly the known keys"
            continue
        obprofile.load(str(f))
