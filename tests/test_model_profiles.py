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


# --------------------------------------------------------------------------- EXTRA_ARGS policy
# vLLM's FlexibleArgumentParser maps "_" to "-" and argparse accepts any
# unambiguous prefix: every spelling below reaches the engine as the refused
# flag, so each must be refused.

TF_BASE = f"BACKEND=tensorfold\nSOURCE=acme/Brand-New\nREVISION={SHA}\nSERVED_MODEL_NAME=x\n"


def extra(tmp_path, args: list[str], base=BASE, ack=""):
    import json as _json
    body = base + f"EXTRA_ARGS={_json.dumps(args)}\n" + (f"EXTRA_ARGS_ACK={ack}\n" if ack else "")
    return load_body(tmp_path, body)


@pytest.mark.parametrize("args", [
    ["--trust_remote_code"], ["--Trust-Remote-Code"], ["--trust-remote"], ["--trust"],
    ["--code_revision", "main"], ["--code-rev", "main"], ["--api_key=sk-1"], ["--api-key", "sk-1"],
    ["--hf_token", "x"], ["--tokenizer", "attacker/tok"], ["--tokenizer-rev", "main"],
    ["--config", "/tmp/args.yaml"], ["--allowed-local-media-path", "/"], ["--allowed_local_media"],
    ["--middleware", "evil.mod"], ["--worker-cls", "evil.W"], ["--served-model", "y"],
    ["-tp", "4"], ["-O3"], ["--model=attacker/other"], ["--tool_call_parser", "x"],
])
def test_extra_args_spellings_of_refused_flags(tmp_path, args):
    with pytest.raises(obprofile.ProfileError, match="EXTRA_ARGS"):
        extra(tmp_path, args)
    with pytest.raises(obprofile.ProfileError, match="EXTRA_ARGS"):   # the ACK does not unlock these
        extra(tmp_path, args, ack=SHA)


def test_extra_args_allow_list_and_ack(tmp_path):
    p = extra(tmp_path, ["--enable-prefix-caching", "--kv_cache_dtype", "fp8", "--no-enable-prefix-caching",
                         "--max-num-batched-tokens=8192", "--seed", "-1"])
    assert p.extra_args[1] == "--kv_cache_dtype" and not p.warnings
    with pytest.raises(obprofile.ProfileError, match="not on the allow list"):
        extra(tmp_path, ["--enable-sleep-mode"])
    with pytest.raises(obprofile.ProfileError, match="abbreviation of"):
        extra(tmp_path, ["--enable-prefix"])
    with pytest.raises(obprofile.ProfileError, match="not a known flag"):
        extra(tmp_path, ["--frobnicate"])
    with pytest.raises(obprofile.ProfileError, match="EXTRA_ARGS_ACK"):
        extra(tmp_path, ["--enable-sleep-mode"], ack=SHA2)          # an ACK for another revision
    p = extra(tmp_path, ["--enable-sleep-mode"], ack=SHA)
    assert any("outside the allow list" in w for w in p.warnings)


@pytest.mark.parametrize("args", [["--rank", "1"], ["--master", "10.0.0.1"], ["--master_port=1"],
                                  ["--backend", "mlx"], ["--vision-urls"], ["--vision"], ["--tp", "1"],
                                  ["--host", "0.0.0.0"], ["--drafter", "attacker/d"], ["--snapshot-dir", "/"]])
def test_extra_args_tensorfold_launcher_owned(tmp_path, args):
    with pytest.raises(obprofile.ProfileError, match="EXTRA_ARGS"):
        extra(tmp_path, args, base=TF_BASE, ack=SHA)


def test_extra_args_tensorfold_allowed(tmp_path):
    p = extra(tmp_path, ["--kv-dtype", "int8", "--no-thinking", "--max_tokens", "8192"], base=TF_BASE)
    assert not p.warnings


def test_extra_args_fail_closed_without_vendored_data(tmp_path, monkeypatch):
    monkeypatch.setattr(obprofile, "DATA", tmp_path / "nowhere")
    with pytest.raises(obprofile.ProfileError, match="EXTRA_ARGS"):
        extra(tmp_path, ["--enable-prefix-caching"])


@pytest.mark.parametrize("spec,ok", [
    ('{"method":"mtp","num_speculative_tokens":3}', True),
    ('{"model":"attacker/drafter","num_speculative_tokens":3}', False),
    ('{"model":"acme/drafter","revision":"main","num_speculative_tokens":3}', False),
    (f'{{"model":"acme/drafter","revision":"{SHA}","num_speculative_tokens":3}}', True),
    (f'{{"model":"acme/drafter","revision":"{SHA}","code_revision":"main"}}', False),
])
def test_speculative_draft_model_must_be_pinned(tmp_path, spec, ok):
    body = BASE + f"SPECULATIVE_CONFIG={spec}\n"
    if ok:
        assert load_body(tmp_path, body).speculative
    else:
        with pytest.raises(obprofile.ProfileError, match="SPECULATIVE_CONFIG"):
            load_body(tmp_path, body)
