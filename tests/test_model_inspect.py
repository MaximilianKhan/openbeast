"""model-inspect.sh on checkpoints it has never seen: fixture directories in
the shapes that matter (Qwen / Llama 3 / Mistral templates, no tools, FP8,
NVFP4 ModelOpt + compressed-tensors, MLX, MoE, an architecture vLLM does not
know), the memory-fit estimate, --write-profile, and the Hub path through a
stub Hub (token from a 0600 file, OFFLINE, no token across hosts).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
# first: onboarding_helpers puts scripts/backends/pylib on sys.path
from onboarding_helpers import (  # noqa: E402
    LLAMA3_TEMPLATE, MISTRAL_TEMPLATE, PLAIN_TEMPLATE, PYLIB, QWEN_TEMPLATE, SHA, Hub, clean_env, dense,
    make_ckpt, make_remote,
)
import hfapi  # noqa: E402
import model_inspect  # noqa: E402
import obprofile  # noqa: E402
def run_inspect(d: Path) -> dict:
    return model_inspect.inspect(model_inspect.open_source(str(d)))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    clean_env(monkeypatch)


@pytest.fixture
def remote():
    hub = make_remote()
    yield hub
    hub.close()


def test_inspect_qwen_style_template_suggests_qwen3_parsers(tmp_path):
    r = run_inspect(make_ckpt(tmp_path / "q", dense("Qwen3ForCausalLM", "qwen3"), QWEN_TEMPLATE))
    t = r["chat_template"]
    assert t["renders_tools"] and "<tool_call>" in t["tool_markers"] and "<function=" in t["tool_markers"]
    assert "<think>" in t["reasoning_markers"] and t["thinking_toggle"] and t["opens_think_in_prompt"]
    tool = r["suggestions"]["tool_call_parser"][0]
    assert (tool["parser"], tool["confidence"]) == ("qwen3_xml", "high")
    reas = r["suggestions"]["reasoning_parser"][0]
    assert (reas["parser"], reas["confidence"]) == ("qwen3", "high")
    assert r["engines"]["vllm"]["status"] == "supported"
    assert r["engines"]["tensorfold"]["status"] == "unsupported"   # qwen3 (not qwen3_5) has no family


def test_inspect_llama3_python_tag(tmp_path):
    r = run_inspect(make_ckpt(tmp_path / "l", dense("LlamaForCausalLM", "llama"), LLAMA3_TEMPLATE))
    tool = r["suggestions"]["tool_call_parser"][0]
    assert tool["parser"] == "llama3_json" and tool["confidence"] == "high"
    assert r["suggestions"]["reasoning_parser"][0]["parser"] is None


def test_inspect_mistral_tool_calls_marker(tmp_path):
    r = run_inspect(make_ckpt(tmp_path / "m", dense("MistralForCausalLM", "mistral"), MISTRAL_TEMPLATE))
    assert r["suggestions"]["tool_call_parser"][0]["parser"] == "mistral"
    assert "[TOOL_CALLS]" in r["chat_template"]["tool_markers"]


def test_inspect_template_without_tools_says_no_parser(tmp_path):
    r = run_inspect(make_ckpt(tmp_path / "p", dense("LlamaForCausalLM", "llama"), PLAIN_TEMPLATE))
    s = r["suggestions"]["tool_call_parser"][0]
    assert s["parser"] is None and "never renders `tools`" in s["why"]
    assert r["chat_template"]["renders_tools"] is False


def test_inspect_chat_template_jinja_file_wins(tmp_path):
    d = make_ckpt(tmp_path / "j", dense("Qwen3ForCausalLM", "qwen3"), PLAIN_TEMPLATE,
                  extra={"chat_template.jinja": QWEN_TEMPLATE})
    r = run_inspect(d)
    assert r["chat_template"]["source"] == "chat_template.jinja"
    assert r["suggestions"]["tool_call_parser"][0]["parser"] == "qwen3_xml"


def test_inspect_fp8(tmp_path):
    cfg = dense("LlamaForCausalLM", "llama", quantization_config={
        "quant_method": "fp8", "weight_block_size": [128, 128], "activation_scheme": "dynamic"})
    r = run_inspect(make_ckpt(tmp_path / "f", cfg, LLAMA3_TEMPLATE))
    assert r["quantization"]["method"] == "fp8" and r["quantization"]["scheme"].startswith("FP8")
    assert r["engines"]["vllm"]["status"] == "supported"


def test_inspect_nvfp4_modelopt_and_compressed_tensors(tmp_path):
    cfg = dense("Qwen3ForCausalLM", "qwen3", quantization_config={
        "quant_method": "modelopt", "quant_algo": "MIXED_PRECISION",
        "config_groups": {"g0": {"weights": {"num_bits": 8, "type": "float"}},
                          "g1": {"weights": {"num_bits": 4, "type": "float", "group_size": 16}}}})
    tensors = {"w": ("U8", [64, 8]), "w_scale": ("F8_E4M3", [64, 1]), "e": ("BF16", [8, 8])}
    r = run_inspect(make_ckpt(tmp_path / "n", cfg, QWEN_TEMPLATE, tensors))
    q = r["quantization"]
    assert q["method"] == "modelopt" and "NVFP4" in q["scheme"] and "FP8" in q["scheme"]
    w = r["weights"]
    assert w["stored_elements_by_dtype"]["U8"] == 512
    assert w["logical_params_estimate"] == 512 * 2 + 64 + 64      # packed fp4 counted twice
    # older ModelOpt layout: hf_quant_config.json only
    d2 = make_ckpt(tmp_path / "n2", dense("Qwen3ForCausalLM", "qwen3"), QWEN_TEMPLATE,
                   extra={"hf_quant_config.json": {"quantization": {"quant_algo": "NVFP4"}}})
    assert "NVFP4" in run_inspect(d2)["quantization"]["scheme"]
    cfg3 = dense("Qwen3ForCausalLM", "qwen3", quantization_config={
        "quant_method": "compressed-tensors", "format": "nvfp4-pack-quantized",
        "config_groups": {"g": {"format": "nvfp4-pack-quantized", "weights": {"num_bits": 4, "type": "float"}}}})
    assert run_inspect(make_ckpt(tmp_path / "n3", cfg3, QWEN_TEMPLATE))["quantization"]["scheme"] == \
        "compressed-tensors NVFP4"


def test_inspect_moe(tmp_path):
    cfg = dense("Qwen3MoeForCausalLM", "qwen3_moe", num_experts=128, num_experts_per_tok=8)
    r = run_inspect(make_ckpt(tmp_path / "moe", cfg, QWEN_TEMPLATE))
    assert r["moe"]["experts"] == 128 and r["moe"]["active_per_token"] == 8


def test_inspect_unknown_architecture_is_unknown_to_vllm(tmp_path):
    r = run_inspect(make_ckpt(tmp_path / "u", dense("BrandNewForCausalLM", "brand_new"), QWEN_TEMPLATE))
    v = r["engines"]["vllm"]
    commit = json.loads((PYLIB.parent / "data" / "vllm.json").read_text())["_provenance"]["commit"]
    assert v["status"] == "unknown"
    assert f"unknown to vLLM @{commit[:12]}" in v["notes"][0]
    assert "newer vLLM" in v["notes"][0] and "trust_remote_code" in v["notes"][0]
    assert r["engines"]["tensorfold"]["status"] == "unsupported"


def test_inspect_mlx_checkpoint_engines(tmp_path):
    cfg = {"architectures": ["Qwen3_5ForConditionalGeneration"], "model_type": "qwen3_5",
           "quantization": {"bits": 4, "group_size": 64, "mode": "affine"},
           "text_config": {"model_type": "qwen3_5_text", "num_hidden_layers": 64, "num_attention_heads": 24,
                           "num_key_value_heads": 4, "head_dim": 256, "hidden_size": 5120,
                           "max_position_embeddings": 262144,
                           "layer_types": ["linear_attention"] * 3 + ["full_attention"]}}
    r = run_inspect(make_ckpt(tmp_path / "mlx", cfg, QWEN_TEMPLATE, {"w": ("U32", [16, 4])}))
    assert r["quantization"]["method"] == "mlx"
    assert r["engines"]["vllm"]["status"] == "unsupported"
    assert any("MLX" in n for n in r["engines"]["vllm"]["notes"])
    tf = r["engines"]["tensorfold"]
    assert tf["status"] == "supported" and tf["family"] == "qwen3_5" and tf["ranks"] == [1, 2]
    assert r["kv_cache"]["attention_layers"] == 1           # hybrid: only full-attention layers hold KV
    assert r["weights"]["logical_params_estimate"] == 64 * 8  # U32 packs 8 4-bit values


def test_inspect_tensorfold_nvfp4_is_one_rank(tmp_path):
    cfg = {"architectures": ["Qwen3_5ForConditionalGeneration"], "model_type": "qwen3_5",
           "quantization_config": {"quant_method": "modelopt", "quant_algo": "NVFP4"},
           "text_config": {"model_type": "qwen3_5_text", "num_hidden_layers": 4, "num_attention_heads": 4,
                           "num_key_value_heads": 2, "head_dim": 8}}
    tf = run_inspect(make_ckpt(tmp_path / "t", cfg, QWEN_TEMPLATE))["engines"]["tensorfold"]
    assert tf["status"] == "supported" and tf["ranks"] == [1]
    assert any("ONE rank only" in n for n in tf["notes"])


def test_fit_estimates():
    kv = {"bytes": 65536, "splits_across_tp": True}
    small = model_inspect.fit(30 * 10**9, kv, 262144, 0.80)
    assert small["tp1"]["verdict"] == "fits" and "ONE Spark" in small["verdict"]
    mid = model_inspect.fit(150 * 10**9, kv, 262144, 0.80)
    assert mid["tp1"]["verdict"] == "does not fit" and mid["tp2"]["verdict"] == "fits"
    assert "BOTH Sparks" in mid["verdict"]
    big = model_inspect.fit(230 * 10**9, kv, 262144, 0.80)
    assert big["tp2"]["verdict"] == "does not fit" and "does NOT fit" in big["verdict"]
    assert small["tp1"]["suggested_max_model_len"] <= 262144
    assert "128 GB" in small["assumptions"]["method"] or small["assumptions"]["spark_bytes"] == 128 * 10**9


def test_write_profile_draft_is_marked_and_parses(tmp_path):
    d = make_ckpt(tmp_path / "ck", dense("Qwen3ForCausalLM", "qwen3"), QWEN_TEMPLATE)
    prof = tmp_path / "profiles"
    rc = model_inspect.main([str(d), "--write-profile", "newmodel", "--models-dir", str(prof)])
    assert rc == 0
    text = (prof / "newmodel.env").read_text()
    assert "# VERIFY" in text and "TOOL_CALL_PARSER=qwen3_xml" in text and "REASONING_PARSER=qwen3" in text
    p = obprofile.load("newmodel", "vllm", prof)          # the draft is a valid profile as written
    assert p.source == str(d.resolve()) and p.get("TOOL_CALL_PARSER") == "qwen3_xml"
    assert model_inspect.main([str(d), "--write-profile", "newmodel", "--models-dir", str(prof)]) == 1  # no clobber


def test_inspect_cli_json(tmp_path):
    d = make_ckpt(tmp_path / "c", dense("MistralForCausalLM", "mistral"), MISTRAL_TEMPLATE)
    out = subprocess.run([sys.executable, str(PYLIB / "model_inspect.py"), str(d), "--json"],
                         capture_output=True, text=True, timeout=60, env={**os.environ, "OFFLINE": "true"})
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout)
    assert doc["architectures"] == ["MistralForCausalLM"] and doc["engines"]["vllm"]["status"] == "supported"


def test_vendored_lists_have_provenance():
    for name in ("vllm", "tensorfold"):
        d = json.loads((PYLIB.parent / "data" / f"{name}.json").read_text())
        assert len(d["_provenance"]["commit"]) == 40
    v = json.loads((PYLIB.parent / "data" / "vllm.json").read_text())
    assert {"hermes", "qwen3_xml", "mistral", "llama3_json"} <= set(v["tool_parsers"])
    assert {"qwen3", "deepseek_r1"} <= set(v["reasoning_parsers"])
    assert "LlamaForCausalLM" in v["generation_architectures"]


def test_inspect_through_hub_stub_with_token_file(tmp_path, remote, monkeypatch):
    tok = tmp_path / "tok"
    tok.write_text("hf_SECRET_123\n")
    tok.chmod(0o600)
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    monkeypatch.setenv("HF_TOKEN_FILE", str(tok))
    r = model_inspect.inspect(model_inspect.open_source(f"acme/Brand-New@{SHA}"))
    assert r["revision"] == SHA and r["architectures"] == ["BrandNewForCausalLM"]
    assert r["weights"]["stored_elements"] == 2 * 128 * 64          # read via Range from the header only
    assert any(rng for _, _, rng in remote.log), "safetensors header must be read with Range requests"
    assert all(auth == "Bearer hf_SECRET_123" for _, auth, _ in remote.log)
    # an unpinned spec resolves main to the SHA and says so
    r2 = model_inspect.inspect(model_inspect.open_source("acme/Brand-New"))
    assert r2["revision"] == SHA and any("no commit given" in w for w in r2["warnings"])
    tok.chmod(0o644)
    with pytest.raises(hfapi.HubError, match="chmod 600"):
        model_inspect.open_source(f"acme/Brand-New@{SHA}")


def test_offline_refuses_the_hub(remote, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    monkeypatch.setenv("OFFLINE", "true")
    with pytest.raises(hfapi.Offline):
        model_inspect.open_source(f"acme/Brand-New@{SHA}")
    assert remote.log == []


def test_token_not_forwarded_across_hosts(tmp_path, remote, monkeypatch):
    other = Hub({"acme/Brand-New": remote.repos["acme/Brand-New"]})
    try:
        remote.redirect = other.url                       # the "CDN": a different host:port
        monkeypatch.setenv("HF_ENDPOINT", remote.url)
        monkeypatch.setenv("HF_TOKEN", "hf_SECRET_123")
        assert json.loads(hfapi.read_text("acme/Brand-New", SHA, "config.json"))["model_type"] == "brand_new"
        assert any(a == "Bearer hf_SECRET_123" for _, a, _ in remote.log)
        assert other.log and all(a is None for _, a, _ in other.log)
    finally:
        other.close()


# --------------------------------------------------------------------------- hostile checkpoints → draft

EVIL_LINE = 'SPECULATIVE_CONFIG={"model":"attacker/drafter","num_speculative_tokens":3}'


def test_draft_profile_cannot_be_injected_by_the_checkpoint(tmp_path):
    """The review's evil/ checkpoint: a newline in model_type (and elsewhere) used to start a new
    KEY=VALUE line in the draft. Every checkpoint string now lands only in a sanitised comment."""
    cfg = dense("Qwen3ForCausalLM", "qwen3\n" + EVIL_LINE,
                max_position_embeddings="40960\nEXTRA_ARGS=[\"--trust-remote-code\"]")
    d = make_ckpt(tmp_path / "evil", cfg, "{% if tools %}<tool_call>{% endif %}<think> " + EVIL_LINE)
    (d / ".openbeast-model.json").write_text(json.dumps({"source": "acme/x\nTRUST_REMOTE_CODE=true",
                                                        "revision": "main"}))
    prof = tmp_path / "profiles"
    assert model_inspect.main([str(d), "--write-profile", "evil", "--models-dir", str(prof)]) == 0
    text = (prof / "evil.env").read_text()
    keys = set(obprofile.parse_text(text))
    assert keys == {"BACKEND", "SOURCE", "REVISION", "SERVED_MODEL_NAME", "TENSOR_PARALLEL_SIZE", "MAX_MODEL_LEN",
                    "DTYPE", "TOOL_CALL_PARSER", "REASONING_PARSER", "GPU_MEMORY_UTILIZATION", "MAX_NUM_SEQS",
                    "TRUST_REMOTE_CODE", "EXTRA_ARGS"}
    assert not any(line.startswith(("SPECULATIVE_CONFIG", "EXTRA_ARGS=[\"--trust")) for line in text.splitlines())
    p = obprofile.load(str(prof / "evil.env"), "vllm")
    assert p.source == str(d.resolve()) and p.get("REVISION") == "" and not p.trust_remote_code
    assert p.get("MAX_MODEL_LEN") == "" or p.get("MAX_MODEL_LEN").isdigit()
    r = model_inspect.inspect(model_inspect.open_source(str(d)))
    assert r["context"]["native"] is None, "a non-integer max_position_embeddings is not a context length"
    assert r["repo"] is None and any("malformed" in w for w in r["warnings"])


def test_write_profile_refuses_a_draft_that_parses_to_other_keys(tmp_path, monkeypatch, capsys):
    d = make_ckpt(tmp_path / "ck", dense("Qwen3ForCausalLM", "qwen3"), QWEN_TEMPLATE)
    real = model_inspect.draft_profile

    def smuggle(*a, **k):
        text, keys = real(*a, **k)
        return text + "TRUST_REMOTE_CODE_ACK=" + "0" * 40 + "\n", keys

    monkeypatch.setattr(model_inspect, "draft_profile", smuggle)
    prof = tmp_path / "profiles"
    assert model_inspect.main([str(d), "--write-profile", "x", "--models-dir", str(prof)]) == 1
    assert "unexpectedly" in capsys.readouterr().err and not (prof / "x.env").exists()
