"""beast-recipe (scripts/beast-recipe.sh -> scripts/backends/pylib/recipe.py): the recipe grammar, the
fit maths against the own-recipes investigation's table, engine selection, the verdicts of the
committed worked examples, the golden Hy4 vLLM launch (the flags rendered from the recipe equal
scripts/backends/hy4-vllm/config.sh's hy4_serve_args), init through the stub Hub, and every acting
command's dry-run and --run against recording stubs. Nothing here reaches a live system: ssh, docker,
systemctl, systemd-run, sudo, curl and beast-spark are stubs first on PATH, and a dry-run must not
call any of them.
"""
from __future__ import annotations

import ast
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
# first: onboarding_helpers puts scripts/backends/pylib on sys.path
from onboarding_helpers import (  # noqa: I001 (sys.path first)
    PYLIB,
    QWEN_TEMPLATE,
    REPO,
    SHA,
    Hub,
    clean_env,
    dense,
    make_ckpt,
    st_bytes,
)
import hfapi  # noqa: E402
import recipe  # noqa: E402

RECIPES = REPO / "recipes"
CLI = REPO / "scripts" / "beast-recipe.sh"
GIB = 1024 ** 3
STUBBED = ("ssh", "docker", "systemctl", "systemd-run", "sudo", "curl", "beast-spark", "rsync")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    clean_env(monkeypatch)
    for k in ("BEAST_RECIPES_DIR", "BEAST_RECIPE_STATE_DIR", "BEAST_RECIPE_DATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BEAST_RECIPE_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def data():
    return recipe.load_data()


@pytest.fixture
def stubs(tmp_path):
    """Recording stubs for every command that could touch a live host; returns (bin dir, log path)."""
    b = tmp_path / "stubbin"
    b.mkdir()
    log = tmp_path / "calls.log"
    for name in STUBBED:
        f = b / name
        f.write_text("#!/bin/bash\n"
                     f'printf "%s\\n" "{name} $*" >> "{log}"\n'
                     'case "$*" in\n'
                     '  *is-active*deepseek-v41-inference*) [[ -f "$STUB_DS_ACTIVE" ]] && exit 0; exit 3 ;;\n'
                     '  *is-active*hy4-vllm-inference*) [[ -f "$STUB_HY4_ACTIVE" ]] && exit 0; exit 3 ;;\n'
                     '  *is-active*beast-recipe-*) [[ -n "$STUB_RECIPE_STATE" ]] && { echo "$STUB_RECIPE_STATE"; exit 3; }; exit 3 ;;\n'
                     '  *is-active*) exit 3 ;;\n'
                     '  *list-units*) [[ -n "$STUB_LIST_UNITS" ]] && echo "$STUB_LIST_UNITS"; exit 0 ;;\n'
                     '  *--version*) echo "tensorfold-native ${STUB_TF_VERSION:-1.2.10}" ;;\n'
                     'esac\n'
                     'cat > /dev/null 2>&1 < /dev/stdin || true\n'
                     "exit 0\n")
        f.chmod(0o755)
    return b, log


def cli(args, stubs=None, env=None, cwd=None):
    e = dict(os.environ)
    if stubs:
        e["PATH"] = f"{stubs[0]}{os.pathsep}{e['PATH']}"
    e.update(env or {})
    return subprocess.run(["bash", str(CLI), *args], capture_output=True, text=True, env=e, cwd=cwd or REPO,
                          timeout=120)


def calls(stubs) -> list[str]:
    return stubs[1].read_text().splitlines() if stubs[1].exists() else []


def mk(tmp_path, body: str, name: str = "t") -> recipe.Recipe:
    d = tmp_path / "recipes" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "recipe.env").write_text(body)
    return recipe.load_recipe(str(d))


# --------------------------------------------------------------------------- the grammar

def test_parse_values_quotes_and_provenance(tmp_path):
    r = mk(tmp_path, "# c\nSOURCE=acme/x  # HUB: given\nREVISION=" + SHA + "\n"
                     "SPEC_CONFIG='{\"method\":\"dfly\",\"num_speculative_tokens\":5}'  # VERIFIED: config.sh\n"
                     "TITLE='a b c'\nBPW=4.0  # READ: bits\n")
    assert r.get("SOURCE") == "acme/x" and r.provenance("SOURCE") == "HUB: given"
    assert r.get("SPEC_CONFIG") == '{"method":"dfly","num_speculative_tokens":5}'
    assert r.provenance("SPEC_CONFIG").startswith("VERIFIED") and r.get("TITLE") == "a b c"
    assert r.num("BPW") == 4.0 and r.name == "t"


@pytest.mark.parametrize("body,err", [
    ("NOPE=1\n", "unknown key"),
    ("BPW=4\nBPW=5\n", "set twice"),
    ("export BPW=4\n", "export"),
    ("BPW=four\n", "not a valid float"),
    ("SOURCE=$(touch /tmp/x)\n", "not a valid source"),
    ("TITLE='open\n", "unbalanced"),
    ("WEIGHTS_DIR=relative/dir\n", "not a valid path"),
])
def test_parse_refuses(tmp_path, body, err):
    with pytest.raises(recipe.RecipeError, match=err):
        mk(tmp_path, body)


def test_render_never_writes_an_injected_line(tmp_path):
    text = recipe.render_recipe(["head\nBPW=99"], [("MODEL_TYPE", "x\nENGINE=evil", "READ", "n\nTP=9"),
                                                   ("TITLE", "fine", "DEFAULT", "")])
    r = recipe.parse_recipe(text, tmp_path / "x" / "recipe.env")
    assert "ENGINE" not in r.values and "BPW" not in r.values and r.get("TITLE") == "fine"
    assert "MODEL_TYPE" not in r.values and "not a valid word: VERIFY" in text


def test_python39_compatible():
    src = (PYLIB / "recipe.py").read_text()
    ast.parse(src, feature_version=(3, 9))
    assert "match " not in [ln.strip()[:6] for ln in src.splitlines()]
    # 3.11+ / 3.10+ spellings a linter likes to "upgrade" to (the Sparks run 3.9's stdlib)
    for bad in ("datetime.UTC", "_dt.UTC", "strict=True", "tomllib", "from typing import Self"):
        assert bad not in src, bad


# --------------------------------------------------------------------------- fit maths

# (checkpoint GiB, TP, the investigation's per-rank W in section 4.2, tolerance)
TABLE = [
    (196.1, 2, 98.9, 0.2), (196.1, 4, 50.0, 0.5), (163.6, 2, 82.4, 0.1), (163.6, 3, 55.0, 0.5),
    (163.6, 4, 42.0, 0.5), (153.3, 2, 77.3, 0.1), (153.3, 3, 52.0, 0.5), (153.3, 4, 39.0, 0.5),
]


@pytest.mark.parametrize("total,tp,want,tol", TABLE)
def test_per_rank_weights_match_the_investigation(data, total, tp, want, tol):
    got = recipe.per_rank_gib(total, tp, data["hardware"]["spark"]["replicated_share"])
    assert abs(got - want) <= tol, (total, tp, got)


@pytest.mark.parametrize("mt,bpw,tp,want", [
    ("deepseek_v41", 4.0, 2, 135), ("deepseek_v41", 4.0, 3, 91), ("deepseek_v41", 4.0, 4, 68),
    ("deepseek_v41", 5.0, 4, 85), ("deepseek_v41", 6.0, 4, 101), ("deepseek_v41", 3.5, 2, 119),
    ("glm5_next", 5.0, 2, 102), ("glm5_next", 5.0, 3, 68), ("glm5_next", 6.0, 3, 81), ("glm5_next", 6.0, 4, 61),
])
def test_bpw_projection_matches_the_investigation(tmp_path, data, mt, bpw, tp, want):
    r = mk(tmp_path, f"MODEL_TYPE={mt}\nFORMAT=exl3\nBPW={bpw}\n")
    total, how = recipe.weights_gib(r, data)
    assert "ESTIMATED" in how
    w = recipe.per_rank_gib(total, tp, data["hardware"]["spark"]["replicated_share"])
    assert abs(w - want) <= 1.0, (mt, bpw, tp, w)


def test_fit_rule_and_budget(tmp_path, data):
    # DeepSeek 2.9 at TP2: 98.8 GiB a rank is TIGHT by the intake rule, and the budget agrees
    # (98.8 + 1.8 KV + 12.2 reserve + 6 OS leaves ~2.9 GiB), as it serves today
    r = mk(tmp_path, f"MODEL_TYPE=deepseek_v41\nFORMAT=exl3\nWEIGHT_BYTES={int(196.1 * GIB)}\nKV_POOL_GIB=1.8\n"
                     "MEMORY_RESERVE_GIB=12.2\n")
    row = recipe.fit_row(r, data, {}, 2, "tensorfold")
    assert row["rule"] == "tight" and row["fit"] == "tight" and 2 < row["headroom_gib"] < 3.5
    assert recipe.fit_row(r, data, {}, 1, "tensorfold")["fit"] == "does not fit"
    # vLLM: weights + KV + reserve must sit inside --gpu-memory-utilization x MemTotal
    v = mk(tmp_path, f"WEIGHT_BYTES={int(153.3 * GIB)}\nKV_CACHE_BYTES=6G\nGPU_MEM_UTIL=0.85\n", "v")
    assert recipe.fit_row(v, data, {}, 2, "vllm")["fit"] == "fits"
    assert recipe.fit_row(v, data, {}, 1, "vllm")["fit"] == "does not fit"


def test_resident_bytes_beat_the_file_size(tmp_path, data):
    # Hy4 EXL3: 203 GiB of files, 85.38 GiB resident (Engram streams from disk), from the data file
    r = mk(tmp_path, f"MODEL_TYPE=hy_v4_flash\nFORMAT=exl3\nWEIGHT_BYTES={int(203.1 * GIB)}\n")
    total, how = recipe.weights_gib(r, data)
    assert abs(total - 85.38) < 0.01 and "streams from disk" in how


# --------------------------------------------------------------------------- engine selection

def _r(tmp_path, data, body, sparks=2, name="t"):
    r = mk(tmp_path, body, name)
    return recipe.plan(r, data, sparks=sparks)


def test_glm_4bit_on_native_and_5bit_needs_1_3_3(tmp_path, data):
    glm = ("MODEL_TYPE=glm5_next\nFORMAT=exl3\nEXL3_CODEBOOK=mcg\nEXL3_SCOPE=glm53_routed_experts_only\n")
    p = _r(tmp_path, data, glm + f"BPW=4\nWEIGHT_BYTES={int(163.6 * GIB)}\n")
    assert p["verdict"] == "SERVABLE-NOW" and p["engine"]["id"] == "tf-native-1.2.10" and p["engine"]["n"] == 2
    p5 = _r(tmp_path, data, glm + "BPW=5\n", sparks=3, name="g5")
    alts = {a["engine"]: a for a in p5["alternatives"]}
    assert not alts["tf-native-1.2.10"]["reads"] and "bits 5" in alts["tf-native-1.2.10"]["why"]
    assert alts["tf-native-1.3.3"]["reads"] and p5["engine"]["id"] == "tf-native-1.3.3"
    assert p5["verdict"] == "SERVABLE-WITH-WORK" and p5["engine"]["n"] == 3      # 68 GiB a rank at TP3
    assert {"engine-stage", "topology"} <= {g["id"] for g in p5["gaps"] if g["blocking"]}
    assert alts["tf-native-1.3.4"]["verdict"] == "BLOCKED" and "not released" in alts["tf-native-1.3.4"]["why"]
    p5_2 = _r(tmp_path, data, glm + "BPW=5\n", sparks=2, name="g52")
    assert p5_2["verdict"] == "NEEDS-MORE-HARDWARE" and p5_2["engine"]["n"] == 3   # 102 GiB at TP2: no


def test_vllm_cannot_read_exl3_and_needs_divisible_heads(tmp_path, data):
    p = _r(tmp_path, data, "MODEL_TYPE=glm5_next\nFORMAT=exl3\nBPW=4\nEXL3_CODEBOOK=mcg\n")
    v = next(a for a in p["alternatives"] if a["engine"] == "vllm-ngc")
    assert not v["reads"] and "vLLM cannot load" in v["why"]
    r = mk(tmp_path, f"ARCHITECTURE=LlamaForCausalLM\nMODEL_TYPE=llama\nFORMAT=bf16\nWEIGHT_BYTES={140 * GIB}\n"
                     "ATTENTION_HEADS=64\nKV_HEADS=8\n", "llama")
    e = recipe.engine_by_id(data, "vllm-ngc")
    path = recipe.evaluate_engine(r, data, e, 2)
    assert [n for n, x in path["rows"].items() if x.get("allowed")] == [1, 2, 4, 8]
    assert path["verdict"] == "SERVABLE-WITH-WORK" and path["n"] == 2
    assert [g["id"] for g in path["rows"][2]["gaps"] if g["blocking"]] == ["vllm-image"]


def test_qwen_exl3_runs_on_one_rank_only(tmp_path, data):
    p = _r(tmp_path, data, f"MODEL_TYPE=qwen3_5\nFORMAT=exl3\nBPW=4\nEXL3_CODEBOOK=mul1\nWEIGHT_BYTES={15 * GIB}\n")
    assert p["engine"]["id"] == "tf-native-1.2.10" and p["engine"]["n"] == 1 and p["verdict"] == "SERVABLE-NOW"
    assert p["parallelism"]["allowed"] == [1]
    # the mcg codebook: only 1.3.3+ reads it
    p2 = _r(tmp_path, data, f"MODEL_TYPE=qwen3_5\nFORMAT=exl3\nBPW=4\nEXL3_CODEBOOK=mcg\nWEIGHT_BYTES={15 * GIB}\n",
            name="q2")
    assert p2["engine"]["id"] == "tf-native-1.3.3" and p2["verdict"] == "SERVABLE-WITH-WORK"


def test_unknown_architecture_is_blocked(tmp_path, data):
    p = _r(tmp_path, data, f"ARCHITECTURE=BrandNewForCausalLM\nMODEL_TYPE=brand_new\nFORMAT=exl3\nBPW=4\n"
                           f"WEIGHT_BYTES={50 * GIB}\n")
    assert p["verdict"] == "BLOCKED" and all(not a["reads"] for a in p["alternatives"])


def test_too_big_for_eight_sparks_is_blocked(tmp_path, data):
    p = _r(tmp_path, data, "MODEL_TYPE=glm5_next\nFORMAT=exl3\nBPW=4\nEXL3_CODEBOOK=mcg\n"
                           f"WEIGHT_BYTES={2000 * GIB}\n")
    assert p["verdict"] == "BLOCKED"


def test_pinned_engine_reports_a_better_path(tmp_path, data):
    p = _r(tmp_path, data, f"MODEL_TYPE=qwen3_5\nFORMAT=exl3\nBPW=4\nEXL3_CODEBOOK=mul1\nWEIGHT_BYTES={15 * GIB}\n"
                           "ENGINE=tf-native-1.3.3\n", sparks=1)
    assert p["engine"]["id"] == "tf-native-1.3.3" and p["verdict"] == "SERVABLE-WITH-WORK"
    assert p["better_path"] == {"engine": "tf-native-1.2.10", "verdict": "SERVABLE-NOW", "n": 1}
    with pytest.raises(recipe.RecipeError, match="no such engine"):
        _r(tmp_path, data, "MODEL_TYPE=qwen3_5\nFORMAT=exl3\nENGINE=nope\n", name="bad")


def test_licence_review_and_to_quantize_gaps(tmp_path, data):
    p = _r(tmp_path, data, "MODEL_TYPE=deepseek_v41\nFORMAT=exl3\nBPW=2.9\nEXL3_CODEBOOK=mul1\n"
                           f"WEIGHT_BYTES={int(196.1 * GIB)}\nLICENCES='weights=cc-by-nc-nd-4.0'\n"
                           "WEIGHTS_STATUS=to-quantize\n")
    ids = {g["id"]: g for g in p["gaps"]}
    assert "licence-review" in ids and not ids["licence-review"]["blocking"]
    assert ids["quantize"]["blocking"] and p["verdict"] == "SERVABLE-WITH-WORK"


# --------------------------------------------------------------------------- the worked examples

EXPECTED = {
    "hy4-flash-nvfp4-vllm-tp2": ("SERVABLE-NOW", "vllm-hy4", 2),
    "hy4-flash-exl3-tensorfold-tp2": ("SERVABLE-WITH-WORK", "tf-native-1.3.3", 2),
    "glm53-flash-exl3-native-tp2": ("SERVABLE-NOW", "tf-native-1.2.10", 2),
    "deepseek-v41-flash-exl3-2.9-tp2": ("SERVABLE-NOW", "tf-fork-dsv41", 2),
    "deepseek-v41-flash-exl3-4.0": ("NEEDS-MORE-HARDWARE", "tf-fork-dsv41", 4),
    "qwen38-27b-exl3-4.0-1x": ("SERVABLE-WITH-WORK", "tf-native-1.3.3", 1),
    "minimax-m31-flash-exl3-3.06-tp2": ("SERVABLE-NOW", "tf-native-1.2.10", 2),
}


def test_every_committed_recipe_parses_and_is_listed():
    have = {p.parent.name for p in RECIPES.glob("*/recipe.env")}
    assert have == set(EXPECTED)
    for n in have:
        r = recipe.load_recipe(n)
        assert r.get("TITLE") and r.get("MODEL_TYPE") and r.get("SERVED_NAME")
        for k in r.values:
            assert r.provenance(k), f"{n}: {k} has no provenance comment"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_worked_example_verdicts(data, name):
    v, eng, n = EXPECTED[name]
    p = recipe.plan(recipe.load_recipe(name), data)
    assert (p["verdict"], p["engine"]["id"], p["engine"]["n"]) == (v, eng, n)
    json.dumps(p, default=str)                                      # --json serialises
    txt = recipe.render_plan(p)
    for phase in ("engine:", "stage:", "up:", "check:", "adopt:", "rollback:"):
        assert phase in txt


def test_deepseek_4_needs_four_sparks_and_says_why(data):
    p = recipe.plan(recipe.load_recipe("deepseek-v41-flash-exl3-4.0"), data)
    why = "\n".join(p["hardware_why"])
    assert "2 Sparks: 134.7 GiB" in why and "3 Sparks" in why and "does not run this family at 3" in why
    assert "4 Sparks: 68.4 GiB a rank, fits" in why
    ids = [g["id"] for g in p["gaps"]]
    assert {"quantize", "image-build", "topology"} <= set(ids)


def test_hy4_tensorfold_plan_lists_the_engine_work(data):
    p = recipe.plan(recipe.load_recipe("hy4-flash-exl3-tensorfold-tp2"), data)
    work = [g["id"] for g in p["gaps"] if g["blocking"]]
    assert work[:3] == ["engine-stage", "family-tp", "server-wrap"] or set(work) >= {"engine-stage", "family-tp", "server-wrap"}
    assert {"hy4-generator-build", "hy4-generator-qual", "hy4-dfly", "hy4-parsers", "hy4-exl3-reader"} <= set(work)
    assert "slab" in p["engine"]["launch_note"] and "1.3.3 (#4259" in p["engine"]["rank_reason"]
    assert p["baseline"] == {"from": "hy4-flash-nvfp4-vllm-tp2", "toks_1": 31.4, "toks_4": 77.8}
    assert p["fit"][2]["w_gib"] == 43.0 and p["fit"][1]["fit"] == "fits"
    assert 20 <= p["effort_days"][0] and p["effort_days"][1] <= 120
    # the kept pack is read, never written: no download or mirror step names it
    stage = "\n".join(p["steps"]["stage"])
    assert "weights-download" not in stage and "rsync" not in stage and "sha256sum -c" in stage


def test_glm_recipe_drafter_is_the_apache_revision_and_profile_validates(data):
    r = recipe.load_recipe("glm53-flash-exl3-native-tp2")
    assert r.get("DRAFTER").endswith("@6cc6c481eee930a5139619ed919f78726371adbc")
    p = recipe.choose(r, data, 2)["chosen"]
    prof = recipe.render_profile(r, p)
    assert "SOURCE=/home/max/openbeast-models/glm53-flash-ablit-exl3" in prof and "\nREVISION=" not in prof
    assert "DRAFTER_REVISION=6cc6c481eee930a5139619ed919f78726371adbc" in prof and "TENSOR_PARALLEL_SIZE=2" in prof
    lock = json.loads((REPO / "scripts/backends/models/recipe-glm53-flash-exl3-native-tp2.lock").read_text())
    old = json.loads((REPO / "scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.lock").read_text())
    assert lock["profile"] == "recipe-glm53-flash-exl3-native-tp2" and lock["artifacts"] == old["artifacts"]
    unit = recipe.render_unit(r, p)
    assert "\n[Install]" not in unit and "Restart=no" in unit
    assert "spark-cluster.sh start --profile recipe-glm53-flash-exl3-native-tp2 --engine tensorfold" in unit


def test_hy4_nvfp4_sums_are_the_kits():
    mine = (RECIPES / "hy4-flash-nvfp4-vllm-tp2" / "SHA256SUMS").read_text().splitlines()
    kit = (REPO / "scripts/backends/hy4-vllm/SHA256SUMS.hub-lfs").read_text().splitlines()
    strip = lambda xs: sorted(x for x in xs if x and not x.startswith("#"))
    assert strip(mine) == strip(kit)


# --------------------------------------------------------------------------- the golden Hy4 launch

def _hy4_config(tmp_path) -> dict:
    """hy4_serve_args and the pins, from scripts/backends/hy4-vllm/config.sh itself (no settings file)."""
    script = (f'export HOME={tmp_path} HY4_CONF={tmp_path}/none.env; source scripts/backends/hy4-vllm/config.sh; '
              'hy4_serve_args; printf "%s\\0" "${HY4_ARGS[@]}"; printf "\\1"; '
              'printf "%s\\0" "$HY4_REPO" "$HY4_REVISION" "$HY4_IMAGE" "$HY4_BASE_IMAGE" "$HY4_SERVED" "$HY4_PORT" '
              '"$HY4_MODEL_DIR" "$(hy4_min_avail /dev/null)" "$HY4_GMU"')
    env = {k: v for k, v in os.environ.items() if not k.startswith("HY4_")}
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=REPO, env=env, check=True).stdout
    args, pins = out.split("\1")
    keys = ("repo", "revision", "image", "base", "served", "port", "model_dir", "min_avail", "gmu")
    return {"args": args.split("\0")[:-1], **dict(zip(keys, pins.split("\0")))}


def test_golden_hy4_vllm_launch_matches_config_sh(tmp_path):
    g = _hy4_config(tmp_path)
    r = recipe.load_recipe("hy4-flash-nvfp4-vllm-tp2")
    assert recipe.vllm_serve_args(r) == g["args"]
    # the exact served config the review asked for
    a = g["args"]
    for flag, val in (("--tensor-parallel-size", "2"), ("--max-model-len", "262144"), ("--max-num-seqs", "4"),
                      ("--max-num-batched-tokens", "8192"), ("--gpu-memory-utilization", "0.85"),
                      ("--kv-cache-memory-bytes", "6G"), ("--reasoning-parser", "hy_v4"), ("--tool-call-parser", "hy_v4"),
                      ("--speculative-config", '{"method":"dfly","num_speculative_tokens":5}')):
        assert a[a.index(flag) + 1] == val
    assert "--enable-expert-parallel" in a and "--enable-auto-tool-choice" in a
    # and the pins
    assert (r.get("SOURCE"), r.get("REVISION"), r.get("IMAGE"), r.get("BASE_IMAGE"), r.get("SERVED_NAME"), r.get("PORT")) \
        == (g["repo"], g["revision"], g["image"], g["base"], g["served"], g["port"])
    assert r.get("WEIGHTS_DIR").replace("~", str(tmp_path), 1) == g["model_dir"]
    assert r.get("GPU_MEM_UTIL") == g["gmu"]


def test_golden_hy4_spec_off_drops_the_drafter(tmp_path):
    r = mk(tmp_path, (RECIPES / "hy4-flash-nvfp4-vllm-tp2" / "recipe.env").read_text()
           .replace("SPEC_CONFIG='{\"method\":\"dfly\",\"num_speculative_tokens\":5}'", "SPEC_CONFIG=off"))
    assert "--speculative-config" not in recipe.vllm_serve_args(r)


# --------------------------------------------------------------------------- init through the stub Hub

GLM_CFG = {"architectures": ["Glm5NextForConditionalGeneration"], "model_type": "glm5_next", "torch_dtype": "bfloat16",
           "num_hidden_layers": 4, "num_attention_heads": 64, "num_key_value_heads": 64, "hidden_size": 256,
           "kv_lora_rank": 512, "qk_rope_head_dim": 0, "max_position_embeddings": 1048576, "n_routed_experts": 288,
           "num_experts_per_tok": 8, "num_nextn_predict_layers": 1,
           "quantization_config": {"quant_method": "exl3", "bits": 4, "codebook": "mcg", "scope": "glm53_routed_experts_only"}}


@pytest.fixture
def hub():
    w = st_bytes({"model.embed_tokens.weight": ("BF16", [128, 64]), "lm_head.weight": ("BF16", [128, 64])})
    h = Hub({"acme/GLM-Test-EXL3": {"sha": SHA, "files": {
        "config.json": json.dumps(GLM_CFG).encode(),
        "tokenizer_config.json": json.dumps({"chat_template": "{% for m in messages %}{{ m.content }}<tool_call>"
                                                              "<arg_key>k</arg_key>{% endfor %}{{ tools }}"}).encode(),
        "README.md": b"---\nlicense: mit\nbase_model: x\n---\n# card\n",
        "model-00001-of-00001.safetensors": w}}})
    yield h, w
    h.close()


def test_init_from_the_stub_hub(tmp_path, monkeypatch, hub, data):
    h, w = hub
    monkeypatch.setenv("HF_ENDPOINT", h.url)
    monkeypatch.setenv("BEAST_RECIPES_DIR", str(tmp_path / "recipes"))
    d, r, _warnings = recipe.init(f"{h.url}/acme/GLM-Test-EXL3/tree/{SHA}".replace(h.url, "https://huggingface.co"),
                                 "glm-test", 2, "auto", data, False)
    assert r.get("SOURCE") == "acme/GLM-Test-EXL3" and r.get("REVISION") == SHA
    assert (r.get("MODEL_TYPE"), r.get("FORMAT"), r.get("BPW"), r.get("EXL3_CODEBOOK"), r.get("EXL3_SCOPE")) == \
        ("glm5_next", "exl3", "4", "mcg", "glm53_routed_experts_only")
    assert r.get("WEIGHT_BYTES") == str(len(w)) and r.get("LICENCES") == "weights=mit"
    assert r.get("DRAFTER") == "incoai/GLM-5.3-Flash-DFlash2@6cc6c481eee930a5139619ed919f78726371adbc"
    assert r.provenance("MODEL_TYPE").startswith("READ") and r.provenance("REVISION").startswith("HUB")
    assert r.get("TOOL_PARSER") == "glm47"
    sums = (d / "SHA256SUMS").read_text()
    assert f"{hashlib.sha256(w).hexdigest()}  model-00001-of-00001.safetensors" in sums
    assert not h.downloads() or all("config" in p or "README" in p or "tokenizer" in p or p.endswith(".safetensors")
                                    for p in h.downloads())
    assert any(rng for _, _, rng in h.log), "the safetensors header must be read with a Range request"
    with pytest.raises(recipe.RecipeError, match="exists"):
        recipe.init(f"acme/GLM-Test-EXL3@{SHA}", "glm-test", 2, "auto", data, False)
    p = recipe.plan(r, data)
    assert p["verdict"] == "SERVABLE-NOW" and p["engine"]["id"] == "tf-native-1.2.10"


def test_init_unpinned_warns_and_offline_refuses(tmp_path, monkeypatch, hub, data):
    h, _ = hub
    monkeypatch.setenv("HF_ENDPOINT", h.url)
    monkeypatch.setenv("BEAST_RECIPES_DIR", str(tmp_path / "recipes"))
    _d, r, warnings = recipe.init("acme/GLM-Test-EXL3", "u", 2, "auto", data, False)
    assert r.get("REVISION") == SHA and any("no commit given" in x for x in warnings)
    assert "resolved from main" in r.provenance("REVISION")
    monkeypatch.setenv("OFFLINE", "true")
    with pytest.raises(hfapi.Offline):
        recipe.init(f"acme/GLM-Test-EXL3@{SHA}", "o", 2, "auto", data, False)


def test_init_local_dir_and_names(tmp_path, monkeypatch, data):
    monkeypatch.setenv("BEAST_RECIPES_DIR", str(tmp_path / "recipes"))
    ck = make_ckpt(tmp_path / "Qwen-Local", dense("Qwen3ForCausalLM", "qwen3"), QWEN_TEMPLATE)
    d, r, _ = recipe.init(str(ck), None, 1, "auto", data, False)
    assert d.name == "qwen-local-1x" and r.get("SOURCE") == str(ck.resolve()) and r.get("WEIGHTS_STATUS") == "local"
    assert r.get("FORMAT") == "bf16" and r.get("TOOL_PARSER") == "qwen3_xml" and not (d / "SHA256SUMS").exists()
    with pytest.raises(recipe.RecipeError, match="--name"):
        recipe.init(str(ck), "Bad Name", 1, "auto", data, False)
    with pytest.raises(recipe.RecipeError, match="sub-folder"):
        recipe.resolve_source("https://huggingface.co/acme/x/tree/main/sub")


def test_init_cli_json(tmp_path, monkeypatch, hub):
    h, _ = hub
    out = cli(["init", f"acme/GLM-Test-EXL3@{SHA}", "--name", "c", "--json"],
              env={"HF_ENDPOINT": h.url, "BEAST_RECIPES_DIR": str(tmp_path / "recipes")})
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout)
    assert doc["plan"]["verdict"] == "SERVABLE-NOW" and (tmp_path / "recipes/c/recipe.env").is_file()


# --------------------------------------------------------------------------- dry-runs touch nothing

@pytest.mark.parametrize("args", [
    ["plan", "glm53-flash-exl3-native-tp2"], ["plan", "deepseek-v41-flash-exl3-4.0", "--sparks", "1,2,3,4"],
    ["engine", "glm53-flash-exl3-native-tp2"], ["engine", "hy4-flash-exl3-tensorfold-tp2"],
    ["engine", "hy4-flash-nvfp4-vllm-tp2"], ["stage", "glm53-flash-exl3-native-tp2"],
    ["stage", "hy4-flash-nvfp4-vllm-tp2"], ["stage", "deepseek-v41-flash-exl3-2.9-tp2"],
    ["stage", "deepseek-v41-flash-exl3-4.0"], ["stage", "minimax-m31-flash-exl3-3.06-tp2"],
    ["up", "glm53-flash-exl3-native-tp2"], ["up", "hy4-flash-nvfp4-vllm-tp2"], ["up", "deepseek-v41-flash-exl3-2.9-tp2"],
    ["down", "glm53-flash-exl3-native-tp2"], ["check", "hy4-flash-nvfp4-vllm-tp2", "--bench"],
    ["adopt", "glm53-flash-exl3-native-tp2"], ["adopt", "hy4-flash-nvfp4-vllm-tp2"], ["list"],
    ["show", "glm53-flash-exl3-native-tp2"], ["show", "--keys"],
])
def test_dry_runs_call_nothing(stubs, tmp_path, args):
    before = {p: p.stat().st_mtime for p in (REPO / "scripts/backends/models").glob("*")}
    out = cli(args, stubs)
    assert out.returncode == 0, out.stderr
    assert calls(stubs) == [], f"{args}: a dry-run called {calls(stubs)}"
    assert {p: p.stat().st_mtime for p in (REPO / "scripts/backends/models").glob("*")} == before
    assert not (tmp_path / "state").exists()


def test_dry_run_outputs_per_backend():
    d2 = cli(["up", "glm53-flash-exl3-native-tp2"]).stdout
    assert "BACKEND=tensorfold" in d2 and "spark-cluster.sh start --profile recipe-glm53-flash-exl3-native-tp2" in d2
    assert "\n[Install]" not in d2 and "--write-profile writes it" in d2
    hy4 = cli(["up", "hy4-flash-nvfp4-vllm-tp2"]).stdout
    assert "hy4-vllm/start.sh --print" in hy4 and "systemctl --user start hy4-vllm-inference.service" in hy4
    mia = cli(["up", "deepseek-v41-flash-exl3-2.9-tp2"]).stdout
    assert "systemctl --user start deepseek-v41-inference.service" in mia
    st = cli(["stage", "hy4-flash-nvfp4-vllm-tp2"]).stdout
    assert "spark-hy4-stage.sh --run --only weights,verify,mirror" in st
    mm = cli(["stage", "minimax-m31-flash-exl3-3.06-tp2"]).stdout
    assert "model-fetch.sh --profile recipe-minimax-m31-flash-exl3-3.06-tp2" in mm and "sync-weights" in mm
    q = cli(["stage", "deepseek-v41-flash-exl3-4.0"]).stdout
    assert "convert.py" in q and "spark-model-intake.sh" in q
    eng = cli(["engine", "hy4-flash-exl3-tensorfold-tp2"]).stdout
    assert "engine.sh stage tensorfold --version 1.3.3" in eng and "install tensorfold --version 1.3.3 --from" in eng
    assert "[spark-2] cd ~/openbeast && scripts/engine.sh install" in eng and "mkdir -p recipes/" in eng
    up_note = cli(["up", "hy4-flash-exl3-tensorfold-tp2"]).stdout
    assert "SERVABLE-WITH-WORK: `up` acts only on SERVABLE-NOW" in up_note


# --------------------------------------------------------------------------- --run against stubs

def test_engine_run_checks_every_node(stubs, tmp_path):
    home = tmp_path / "home"
    (home / ".local/bin").mkdir(parents=True)
    tf = home / ".local/bin/tensorfold"
    tf.write_text("#!/bin/bash\necho tensorfold-native 1.2.10\n")
    tf.chmod(0o755)
    out = cli(["engine", "glm53-flash-exl3-native-tp2", "--run"], stubs, env={"HOME": str(home)})
    assert out.returncode == 0, out.stdout + out.stderr
    c = calls(stubs)
    assert c == ["ssh -o BatchMode=yes -o ConnectTimeout=10 -- spark-2 ~/.local/bin/tensorfold --version"], c
    assert out.stdout.count("-> ok") == 2
    bad = cli(["engine", "hy4-flash-exl3-tensorfold-tp2", "--run"], stubs, env={"HOME": str(home)})
    assert bad.returncode == 1 and "MISSING" in bad.stdout and "(stage step; --stage runs it)" in bad.stdout


def test_up_run_refuses_while_another_model_serves(stubs, tmp_path):
    flag = tmp_path / "ds-active"
    flag.write_text("")
    home = tmp_path / "home"
    out = cli(["up", "glm53-flash-exl3-native-tp2", "--run"], stubs,
              env={"STUB_DS_ACTIVE": str(flag), "HOME": str(home), "SB_LOCK": str(tmp_path / "lock")})
    assert out.returncode == 1 and "deepseek-v41-inference.service active: one model at a time" in out.stderr
    assert "NOT started" in out.stderr and "beast-spark glm53-flash-exl3-native-tp2" in out.stderr
    # the unit is installed (no [Install]: enables nothing), so adopt + beast-spark can switch to it
    unit = home / ".config/systemd/user/beast-recipe-glm53-flash-exl3-native-tp2.service"
    assert unit.is_file() and "\n[Install]" not in unit.read_text()
    assert "systemctl --user daemon-reload" in calls(stubs)
    assert not any(" start " in c for c in calls(stubs))
    out2 = cli(["up", "hy4-flash-exl3-tensorfold-tp2", "--run"], stubs)
    assert out2.returncode == 1 and "acts only on SERVABLE-NOW" in out2.stderr


def test_up_run_on_the_serving_unit_does_nothing(stubs, tmp_path):
    flag = tmp_path / "hy4-active"
    flag.write_text("")
    out = cli(["up", "hy4-flash-nvfp4-vllm-tp2", "--run"], stubs,
              env={"STUB_HY4_ACTIVE": str(flag), "SB_LOCK": str(tmp_path / "lock")})
    assert out.returncode == 0 and "already active: nothing installed or started" in out.stdout
    assert all(c.startswith(("systemctl --user is-active", "systemctl --user list-units", "docker ps"))
               for c in calls(stubs)), calls(stubs)


def test_stage_run_refuses_a_non_commercial_licence(stubs, tmp_path):
    d = tmp_path / "recipes" / "nc"
    d.mkdir(parents=True)
    (d / "recipe.env").write_text("TITLE=nc\nSOURCE=acme/x\nREVISION=" + SHA + "\nMODEL_TYPE=glm5_next\nFORMAT=exl3\n"
                                  "BPW=4\nEXL3_CODEBOOK=mcg\nSERVED_NAME=x\nLICENCES='weights=cc-by-nc-nd-4.0'\n")
    out = cli(["stage", "nc", "--run"], stubs, env={"BEAST_RECIPES_DIR": str(tmp_path / "recipes")})
    assert out.returncode == 1 and "--accept-nc" in out.stderr and calls(stubs) == []


def test_stage_run_verifies_on_every_node_with_the_sums_on_stdin(stubs, tmp_path):
    d = tmp_path / "recipes" / "loc"
    d.mkdir(parents=True)
    wd = tmp_path / "w"
    wd.mkdir()
    (wd / "a.safetensors").write_bytes(b"abc")
    (d / "recipe.env").write_text(
        f"TITLE=l\nSOURCE=acme/x\nREVISION={SHA}\nWEIGHTS_STATUS=local\nWEIGHTS_DIR={wd}\nMODEL_TYPE=deepseek_v41\n"
        f"FORMAT=exl3\nBPW=2.9\nEXL3_CODEBOOK=mul1\nWEIGHT_BYTES={int(196.1 * GIB)}\nENGINE=tf-fork-dsv41\n"
        "LAUNCHER=mia-unit:deepseek-v41-inference\nSERVED_NAME=x\nKV_POOL_GIB=1.8\nMEMORY_RESERVE_GIB=12.2\n")
    (d / "SHA256SUMS").write_text(f"# c\n{hashlib.sha256(b'abc').hexdigest()}  a.safetensors\n")
    out = cli(["stage", "loc", "--run"], stubs, env={"BEAST_RECIPES_DIR": str(tmp_path / "recipes")})
    assert out.returncode == 0, out.stdout + out.stderr
    c = calls(stubs)
    assert any(x.startswith("systemd-run --user --scope -p MemoryMax=8G") and "sha256sum -c --quiet -" in x for x in c)
    assert any(x.startswith("ssh -o BatchMode=yes") and "spark-2" in x and "sha256sum -c" in x for x in c)
    assert not any("rsync" in x or "weights-download" in x for x in c), c


def _home_tf(tmp_path, version="1.2.10"):
    home = tmp_path / "home"
    (home / ".local/bin").mkdir(parents=True, exist_ok=True)
    tf = home / ".local/bin/tensorfold"
    tf.write_text(f"#!/bin/bash\necho tensorfold-native {version}\n")
    tf.chmod(0o755)
    return home


ENGINE_CHECK = "ssh -o BatchMode=yes -o ConnectTimeout=10 -- spark-2 ~/.local/bin/tensorfold --version"


def test_adopt_and_rollback_run(stubs, tmp_path):
    state = tmp_path / "state"
    home = _home_tf(tmp_path)
    out = cli(["adopt", "glm53-flash-exl3-native-tp2", "--run"], stubs,
              env={"BEAST_RECIPE_STATE_DIR": str(state), "HOME": str(home)})
    assert out.returncode == 0, out.stderr
    entry = (state / "glm53-flash-exl3-native-tp2.env").read_text()
    assert "UNIT=beast-recipe-glm53-flash-exl3-native-tp2.service" in entry and "SERVED=glm-5.3-flash-ablit" in entry
    assert "TENSORFOLD_VERSION=1.2.10" in entry                  # beast-spark checks it before stopping anything
    # adopt checks the engine on every node, installs the unit (the path beast-spark NAME needs), daemon-reloads
    assert (home / ".config/systemd/user/beast-recipe-glm53-flash-exl3-native-tp2.service").is_file()
    assert "BACKEND=tensorfold" in entry and calls(stubs) == [ENGINE_CHECK, "systemctl --user daemon-reload"]
    open(stubs[1], "w").close()
    # the entry is what spark-beast.sh's recipe_units accepts
    sb = subprocess.run(["bash", "-c", ('set -euo pipefail; eval "$(sed -n -e "/^recipe_value()/p" -e "/^recipe_units()/,/^}/p" '
                         f'{REPO}/scripts/backends/spark-beast.sh)"; HY4_WAIT_S=1; RECIPES_D={state}; recipe_units')],
                        capture_output=True, text=True)
    assert sb.stdout.split() == ["glm53-flash-exl3-native-tp2", "beast-recipe-glm53-flash-exl3-native-tp2.service",
                                 "glm-5.3-flash-ablit", "tensorfold", "2400"], sb.stderr
    no = cli(["rollback", "--run"], stubs, env={"BEAST_RECIPE_STATE_DIR": str(state)})
    assert no.returncode == 1 and "no previous selection" in no.stderr
    (state / ".previous").write_text("PREVIOUS=ds\nRECIPE=glm53-flash-exl3-native-tp2\n")
    dry = cli(["rollback"], stubs, env={"BEAST_RECIPE_STATE_DIR": str(state)})
    assert "beast-spark ds" in dry.stdout and calls(stubs) == []
    ok = cli(["rollback", "--run"], stubs, env={"BEAST_RECIPE_STATE_DIR": str(state)})
    assert ok.returncode == 0 and calls(stubs) == ["beast-spark ds"]


def test_adopt_write_family_is_valid_intake_data(tmp_path, monkeypatch, stubs):
    fam = tmp_path / "fam.json"
    shutil.copy(REPO / "scripts/backends/data/spark-families.json", fam)
    monkeypatch.setattr(recipe, "FAMILIES_FILE", fam)
    before = fam.read_text()
    assert recipe.main(["adopt", "minimax-m31-flash-exl3-3.06-tp2", "--write-family"]) == 0
    assert fam.read_text() == before                       # without --run nothing is written
    monkeypatch.setenv("HOME", str(_home_tf(tmp_path)))
    monkeypatch.setenv("PATH", f"{stubs[0]}{os.pathsep}{os.environ['PATH']}")
    rc = recipe.main(["adopt", "minimax-m31-flash-exl3-3.06-tp2", "--write-family", "--run"])
    assert rc == 0
    reg = json.loads(fam.read_text())
    e = next(f for f in reg["families"] if f["name"] == "minimax-m31-flash-exl3-3.06-tp2")
    assert e["model_types"] == ["minimax_m3_vl"] and e["tp"] == 2 and e["unit"].startswith("beast-recipe-")
    import model_intake
    model_intake.load_registry(fam)


# --------------------------------------------------------------------------- check and bench

class _Api(http.server.BaseHTTPRequestHandler):
    served = "demo"

    def log_message(self, *a):
        pass

    def do_GET(self):
        b = json.dumps({"data": [{"id": self.served}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(5):
            self.wfile.write(f'data: {json.dumps({"choices": [{"delta": {"content": "x"}}]})}\n\n'.encode())
            self.wfile.flush()
        self.wfile.write(f'data: {json.dumps({"choices": [], "usage": {"completion_tokens": 5}})}\n\n'.encode())
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture
def api():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_check_run_refuses_the_wrong_model(stubs, api, tmp_path):
    key = tmp_path / "key"
    key.write_text("SECRETKEY1234567890\n")
    out = cli(["check", "glm53-flash-exl3-native-tp2", "--run", "--url", api, "--key-file", str(key)], stubs)
    assert out.returncode == 1 and "serves demo, not glm-5.3-flash-ablit" in out.stderr
    assert "SECRETKEY" not in out.stdout + out.stderr


def test_bench_one_and_four_streams(api):
    b = recipe.bench(api, "k", "demo", max_tokens=16, runs=3, streams=4)
    assert b["toks_1"] and b["toks_agg"] and b["streams"] == 4 and len(b["runs_1"]) == 3
    assert sum(x["completion_tokens"] for x in b["runs_n"]) == 20


# --------------------------------------------------------------------------- the data and the shell

def test_engine_matrix_is_sourced_and_consistent(data):
    ids = [e["id"] for e in data["engines"]]
    assert len(ids) == len(set(ids))
    for e in data["engines"]:
        for k in ("id", "title", "kind", "status", "launcher", "checked", "families", "licence"):
            assert k in e, (e["id"], k)
        assert "2026-" in e["checked"]
        assert e["status"] in ("installed", "available", "unreleased", "not-installed")
        for mt, f in e["families"].items():
            assert f["formats"] and f["ranks"] and f.get("rank_reason"), (e["id"], mt)
            for g in (f.get("with_work") or {}).get("gaps", []):
                assert g in data["gaps"], (e["id"], mt, g)
            assert f.get("serves", True) in (True, "generator-only", "reference", "source-build"), (e["id"], mt)
    recipe.validate_data(data)
    for g in data["gaps"].values():
        assert g["title"] and g["effort"] and len(g["days"]) == 2 and g["days"][0] <= g["days"][1]
    for mt, ds in data["drafters"].items():
        for d in ds:
            if d.get("repo"):
                assert len(d["revision"]) == 40 and d["licence"] in ("apache-2.0", "mit")


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck():
    files = ["scripts/beast-recipe.sh", "scripts/backends/beast-recipe.sh", "scripts/backends/spark-beast.sh"]
    out = subprocess.run(["shellcheck", "-S", "warning", "-x", "-P", "SCRIPTDIR", *files], cwd=REPO,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_help_for_every_command():
    for c in ("init", "plan", "engine", "stage", "up", "down", "check", "adopt", "rollback", "list", "show"):
        out = cli([c, "--help"])
        assert out.returncode == 0 and "--json" in out.stdout, c
    top = cli(["--help"])
    assert top.returncode == 0 and "beast-recipe init" in top.stdout


@pytest.mark.parametrize("name", ["glm53-flash-exl3-native-tp2", "minimax-m31-flash-exl3-3.06-tp2", "qwen38-27b-exl3-4.0-1x"])
def test_committed_profiles_and_units_are_the_recipes_render(data, name):
    """A D2 recipe serves through scripts/backends/models/<PROFILE>.env (every Spark reads it from its
    own checkout): the committed file must be exactly what the recipe renders, or they have drifted."""
    r = recipe.load_recipe(name)
    p = recipe.choose(r, data, int(r.num("SPARKS")))["chosen"]
    prof = REPO / "scripts/backends/models" / f"{recipe.profile_name(r)}.env"
    assert prof.read_text() == recipe.render_profile(r, p), f"re-render: beast-recipe up {name} --write-profile --force"
    unit = r.dir / recipe.unit_of(r, recipe.launcher_of(r, p))
    assert unit.read_text() == recipe.render_unit(r, p)


# --------------------------------------------------------------------------- review fixes (PR #274)

def test_an_unreleased_engine_never_stages_and_blocks(tmp_path, data):
    e = recipe.engine_by_id(data, "tf-native-1.3.4")
    assert e["status"] == "unreleased"
    r = mk(tmp_path, f"MODEL_TYPE=qwen3_5\nFORMAT=exl3\nBPW=4\nEXL3_CODEBOOK=mul1\nWEIGHT_BYTES={15 * GIB}\n"
                     "ENGINE=tf-native-1.3.4\nSPARKS=1\nLAUNCHER=d2-tensorfold\n")
    p = recipe.plan(r, data)
    assert p["verdict"] == "BLOCKED" and "not released upstream" in p["engine"]["why"]
    assert "upstream-release" in [g["id"] for g in p["gaps"]] and "engine-stage" not in [g["id"] for g in p["gaps"]]
    eng = "\n".join(p["steps"]["engine"])
    assert "engine.sh stage" not in eng and "engine.sh install" not in eng and "gap: upstream-release" in eng
    # auto never picks it over the released 1.3.3
    auto = mk(tmp_path, f"MODEL_TYPE=qwen3_5\nFORMAT=exl3\nBPW=4\nEXL3_CODEBOOK=mcg\nWEIGHT_BYTES={15 * GIB}\n", "a")
    assert recipe.plan(auto, data, sparks=1)["engine"]["id"] == "tf-native-1.3.3"


def test_glm_profile_passes_the_native_kv_cache_and_reserve_and_the_plan_says_what_is_budget(data):
    r = recipe.load_recipe("glm53-flash-exl3-native-tp2")
    p = recipe.choose(r, data, 2)["chosen"]
    prof = recipe.render_profile(r, p)
    # native TensorFold: --kv-cache bf16|fp8 (TENSORFOLD_KV_CACHE); --kv-dtype is the 0.x flag it refuses
    assert "TENSORFOLD_KV_CACHE=fp8" in prof and "kv-dtype" not in prof
    assert "TENSORFOLD_MEMORY_RESERVE_GIB=17.5" in prof and "TENSORFOLD_REQUIRES=1.2.10" in prof
    pl = recipe.plan(r, data)
    kv = pl["kv"]["passed"]
    assert "--kv-cache fp8 is passed" in kv and "TENSORFOLD_MEMORY_RESERVE_GIB=17.5 is set" in kv
    assert "engine-sized, not capped" in kv and "engine-sized" in pl["fit"][2]["kv_from"]
    assert pl["fit"][2]["reserve_gib"] == 17.5                     # the larger, verified local.sh value
    text = recipe.render_plan(pl)
    assert "KV: engine-sized, not capped" in text
    assert "tight budget" in text and "nothing enforces" in text   # 5.7 GiB of headroom, not enforced
    assert "one tensorfold per Spark" in text


def test_no_native_profile_carries_kv_dtype(data):
    for name in ("glm53-flash-exl3-native-tp2", "minimax-m31-flash-exl3-3.06-tp2", "qwen38-27b-exl3-4.0-1x",
                 "hy4-flash-exl3-tensorfold-tp2"):
        r = recipe.load_recipe(name)
        p = recipe.choose(r, data, int(r.num("SPARKS") or 2))["chosen"]
        assert p["kind"] == "tensorfold-native"
        prof = recipe.render_profile(r, p)
        assert "kv-dtype" not in prof and "EXTRA_ARGS" not in prof, name
        assert ("TENSORFOLD_KV_CACHE=fp8" in prof) == (r.get("KV_DTYPE") == "fp8"), name   # bf16 = the default: no flag
        assert f"TENSORFOLD_REQUIRES={p['version']}" in prof
    for f in (recipe.BACKENDS / "models").glob("recipe-*.env"):
        assert "kv-dtype" not in f.read_text(), f
    # the committed profiles are what the recipes render now
    for name in ("glm53-flash-exl3-native-tp2", "minimax-m31-flash-exl3-3.06-tp2", "qwen38-27b-exl3-4.0-1x"):
        r = recipe.load_recipe(name)
        p = recipe.choose(r, data, int(r.num("SPARKS") or 2))["chosen"]
        assert (recipe.BACKENDS / "models" / f"{recipe.profile_name(r)}.env").read_text() == recipe.render_profile(r, p)


def test_native_kv_cache_refuses_a_dtype_it_has_not(tmp_path, data):
    r = mk(tmp_path, f"MODEL_TYPE=qwen3_5\nFORMAT=exl3\nBPW=4\nEXL3_CODEBOOK=mul1\nWEIGHT_BYTES={15 * GIB}\n"
                     "ENGINE=tf-native-1.2.10\nSPARKS=1\nLAUNCHER=d2-tensorfold\nKV_DTYPE=int8\n"
                     "SOURCE=org/m\nREVISION=" + "a" * 40 + "\nSERVED_NAME=m\n")
    p = recipe.choose(r, data, 1)["chosen"]
    with pytest.raises(recipe.RecipeError, match="--kv-cache takes bf16 or fp8"):
        recipe.render_profile(r, p)


def test_profile_native_keys_validate(tmp_path):
    import obprofile
    base = "BACKEND=tensorfold\nSOURCE=org/m\nREVISION=" + "a" * 40 + "\nSERVED_MODEL_NAME=m\n"
    for bad, msg in (("TENSORFOLD_KV_CACHE=int8\n", "bf16 or fp8"), ("TENSORFOLD_MEMORY_RESERVE_GIB=x\n", "GiB"),
                     ("TENSORFOLD_REQUIRES=latest\n", "release version")):
        prof = obprofile.Profile(name="t", path=tmp_path / "t.env", values=obprofile.parse_text(base + bad, "t"))
        with pytest.raises(obprofile.ProfileError, match=msg):
            obprofile.validate(prof, "tensorfold")
    vl = "BACKEND=vllm\nSOURCE=org/m\nREVISION=" + "a" * 40 + "\nSERVED_MODEL_NAME=m\nTENSORFOLD_KV_CACHE=fp8\n"
    with pytest.raises(obprofile.ProfileError, match="TensorFold setting"):
        obprofile.validate(obprofile.Profile(name="t", path=tmp_path / "t.env", values=obprofile.parse_text(vl, "t")), "vllm")


def test_one_spark_plan_has_one_spark_wording(data):
    pl = recipe.plan(recipe.load_recipe("qwen38-27b-exl3-4.0-1x"), data)
    g = next(x for x in pl["gaps"] if x["id"] == "d2-first-serve")
    assert "one Spark" in g["title"] and "pair-down" not in g["effort"]
    assert "on the pair" not in pl["engine"]["unverified"]
    pair = recipe.plan(recipe.load_recipe("glm53-flash-exl3-native-tp2"), data)
    assert "pair-down" in next(x for x in pair["gaps"] if x["id"] == "d2-first-serve")["effort"]


def test_adopt_run_refuses_a_plan_that_is_not_servable_now(stubs, tmp_path):
    state = tmp_path / "state"
    home = _home_tf(tmp_path)
    out = cli(["adopt", "qwen38-27b-exl3-4.0-1x", "--run"], stubs,
              env={"BEAST_RECIPE_STATE_DIR": str(state), "HOME": str(home)})
    assert out.returncode == 1 and "SERVABLE-WITH-WORK" in out.stderr and "adopt --run acts only on SERVABLE-NOW" in out.stderr
    assert not state.exists() and calls(stubs) == []
    assert not (home / ".config/systemd/user/beast-recipe-qwen38-27b-exl3-4.0-1x.service").exists()


def test_adopt_run_refuses_an_engine_not_as_planned(stubs, tmp_path):
    state = tmp_path / "state"
    home = _home_tf(tmp_path, "1.3.3")                             # the head runs 1.3.3, GLM is planned on 1.2.10
    out = cli(["adopt", "glm53-flash-exl3-native-tp2", "--run"], stubs,
              env={"BEAST_RECIPE_STATE_DIR": str(state), "HOME": str(home)})
    assert out.returncode == 1 and "not installed as planned" in out.stderr and "local:" in out.stderr
    assert not state.exists() and "systemctl --user daemon-reload" not in calls(stubs)


def test_adopt_run_writes_the_recipe_cluster_file_off_the_pair(stubs, tmp_path, monkeypatch):
    rd = tmp_path / "recipes"
    shutil.copytree(RECIPES / "qwen38-27b-exl3-4.0-1x", rd / "q1")
    (rd / "q1" / "recipe.env").write_text((rd / "q1" / "recipe.env").read_text()
                                          .replace("ENGINE=tf-native-1.3.3", "ENGINE=tf-native-1.2.10"))
    (rd / "q1" / "cluster.env").unlink(missing_ok=True)
    home = _home_tf(tmp_path)
    cf = tmp_path / "cluster.env"
    cf.write_text('CLUSTER_NODES="local spark-2"\nCLUSTER_REPO=~/openbeast\n')
    env = {"BEAST_RECIPE_STATE_DIR": str(tmp_path / "state"), "HOME": str(home), "BEAST_RECIPES_DIR": str(rd)}
    r = recipe.load_recipe(str(rd / "q1"))
    p = recipe.choose(r, recipe.load_data(), 1)["chosen"]
    (rd / "q1" / recipe.unit_of(r, recipe.launcher_of(r, p))).write_text(recipe.render_unit(r, p))
    out = cli(["adopt", "q1", "--run", "--cluster", str(cf)], stubs, env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (rd / "q1" / "cluster.env").read_text().count('CLUSTER_NODES="local"') == 1


def test_down_on_a_mismatched_mia_unit_stops_nothing(stubs, tmp_path):
    out = cli(["down", "deepseek-v41-flash-exl3-4.0"], stubs)
    assert out.returncode == 0 and "nothing is stopped" in out.stdout and calls(stubs) == []
    cf = tmp_path / "cluster.env"
    cf.write_text('CLUSTER_NODES="local spark-2 spark-3 spark-4"\n')
    run = cli(["down", "deepseek-v41-flash-exl3-4.0", "--run", "--cluster", str(cf)], stubs)
    assert run.returncode == 1 and "nothing is stopped" in run.stderr
    assert not any("stop" in c for c in calls(stubs)), calls(stubs)


def test_stage_intake_is_light_while_serving():
    assert recipe.is_light_step("cd ~/openbeast && scripts/backends/spark-model-intake.sh ~/Documents/x   # read-only")
    assert not recipe.is_light_step("cd ~/openbeast && scripts/backends/spark-model-intake.sh --checksums ~/x")
    assert not recipe.is_light_step("scripts/backends/model-fetch.sh --profile p")
    r = recipe.load_recipe("deepseek-v41-flash-exl3-2.9-tp2")
    data = recipe.load_data()
    p = recipe.choose(r, data, 2)["chosen"]
    steps = recipe.stage_steps(r, p, recipe.engine_by_id(data, p["engine"]), ["local", "spark-2"])
    assert steps and all(recipe.is_light_step(c) for _s, _n, c in steps), steps


def test_stage_renders_a_missing_d2_profile_first(tmp_path, monkeypatch, data):
    rd = tmp_path / "recipes"
    shutil.copytree(RECIPES / "minimax-m31-flash-exl3-3.06-tp2", rd / "mm2")
    (rd / "mm2" / "recipe.env").write_text((rd / "mm2" / "recipe.env").read_text()
                                           .replace("PROFILE=recipe-minimax-m31-flash-exl3-3.06-tp2", "PROFILE=recipe-mm2"))
    for f in (rd / "mm2").glob("*.service"):
        f.unlink()
    backends = tmp_path / "backends"
    (backends / "models").mkdir(parents=True)
    monkeypatch.setenv("BEAST_RECIPES_DIR", str(rd))
    monkeypatch.setattr(recipe, "BACKENDS", backends)
    r = recipe.load_recipe("mm2")
    p = recipe.choose(r, data, 2)["chosen"]
    steps = recipe.stage_steps(r, p, recipe.engine_by_id(data, p["engine"]), ["local", "spark-2"])
    assert steps[0][0] == "profile" and "--write-profile" in steps[0][2]
    assert recipe.main(["stage", "mm2", "--run", "--only", "profile"]) == 0
    assert (backends / "models" / "recipe-mm2.env").is_file() and (rd / "mm2" / "beast-recipe-mm2.service").is_file()


def test_plan_retarget_says_the_acting_commands_follow_recipe_env():
    out = cli(["plan", "glm53-flash-exl3-native-tp2", "--sparks", "4"])
    # the chosen TP stays 2 = recipe.env's SPARKS: nothing to change there
    assert out.returncode == 0 and "set SPARKS=" not in out.stdout and "nothing to change there" in out.stdout
    one = cli(["plan", "glm53-flash-exl3-native-tp2", "--sparks", "3"])
    assert "set SPARKS=" not in one.stdout
    q = cli(["plan", "qwen38-27b-exl3-4.0-1x", "--sparks", "2"])
    assert q.returncode == 0
    # TP2 is servable now on 4 Sparks; TP3 and TP4 need the cabling and fabric work (gap topology)
    assert "VERDICT   SERVABLE-NOW at TP2" in out.stdout
    p = recipe.plan(recipe.load_recipe("glm53-flash-exl3-native-tp2"), recipe.load_data(), sparks=3)
    e = recipe.evaluate_engine(recipe.load_recipe("glm53-flash-exl3-native-tp2"), recipe.load_data(),
                               recipe.engine_by_id(recipe.load_data(), "tf-native-1.2.10"), 3)
    assert e["rows"][3]["verdict"] == "SERVABLE-WITH-WORK" and "topology" in [g["id"] for g in e["rows"][3]["gaps"]]
    assert p["engine"]["n"] == 2


def test_nodes_follow_the_recipe_size_and_refuse_bad_targets(tmp_path):
    cf = tmp_path / "cluster.env"
    cf.write_text('CLUSTER_NODES="local max@10.100.216.2"\nCLUSTER_REPO=~/openbeast\n')
    assert recipe.nodes(1, cf)[0] == ["local"]
    assert recipe.nodes(2, cf)[0] == ["local", "max@10.100.216.2"]
    assert recipe.nodes(4, cf)[0] == ["local", "max@10.100.216.2", "<rank2>", "<rank3>"]
    for bad in ("local spark-2;id", "local -oProxyCommand=x", "spark-2 local"):
        cf.write_text(f'CLUSTER_NODES="{bad}"\n')
        with pytest.raises(recipe.RecipeError):
            recipe.nodes(2, cf)


def test_a_one_spark_recipe_runs_on_its_own_cluster_file(tmp_path, data, stubs):
    cf = tmp_path / "cluster.env"
    cf.write_text('CLUSTER_NODES="local max@10.100.216.2"\n')
    r = recipe.load_recipe("qwen38-27b-exl3-4.0-1x")
    p = recipe.choose(r, data, 1)["chosen"]
    unit = recipe.render_unit(r, p)
    assert "start --cluster %h/openbeast/recipes/qwen38-27b-exl3-4.0-1x/cluster.env" in unit
    assert "stop --cluster %h/openbeast/recipes/qwen38-27b-exl3-4.0-1x/cluster.env" in unit
    st = cli(["stage", "qwen38-27b-exl3-4.0-1x", "--cluster", str(cf)], stubs).stdout
    assert "on local" in st and "max@10.100.216.2" not in st and "sync-weights" not in st
    # a TP4 recipe with two listed nodes: --run refused before anything runs
    out = cli(["stage", "deepseek-v41-flash-exl3-4.0", "--run", "--force", "--cluster", str(cf)], stubs)
    assert out.returncode == 1 and "add the nodes to CLUSTER_NODES" in out.stderr and calls(stubs) == []


def test_init_hub_errors_are_one_line(tmp_path):
    out = cli(["init", "acme/x"], env={"OFFLINE": "true", "BEAST_RECIPES_DIR": str(tmp_path)})
    assert out.returncode == 1 and out.stderr.startswith("beast-recipe:") and "Traceback" not in out.stderr
    assert "works offline" in out.stderr


def test_deepseek_4_does_not_hand_over_to_the_tp2_unit(stubs, tmp_path, data):
    p = recipe.plan(recipe.load_recipe("deepseek-v41-flash-exl3-4.0"), data)
    up, adopt = "\n".join(p["steps"]["up"]), "\n".join(p["steps"]["adopt"])
    assert "gap: launcher" in up and "gap: launcher" in adopt and "beast-spark ds" not in up + adopt
    flag = tmp_path / "ds"
    flag.write_text("")
    cf = tmp_path / "cluster.env"
    cf.write_text('CLUSTER_NODES="local spark-2 spark-3 spark-4"\n')
    out = cli(["up", "deepseek-v41-flash-exl3-4.0", "--run", "--force", "--cluster", str(cf)], stubs,
              env={"STUB_DS_ACTIVE": str(flag)})
    assert out.returncode == 1 and "nothing to start" in out.stderr
    # the 2.9 recipe is the unit's own: it still hands over
    assert "beast-spark ds" in "\n".join(recipe.plan(recipe.load_recipe("deepseek-v41-flash-exl3-2.9-tp2"), data)["steps"]["adopt"])


def test_busy_sees_starting_and_unadopted_recipe_units(stubs, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{stubs[0]}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_LIST_UNITS", "beast-recipe-x.service loaded activating start x")
    monkeypatch.setenv("STUB_RECIPE_STATE", "activating")
    assert recipe.any_inference_active(recipe.Runner(True)) == ["beast-recipe-x.service"]


def test_stage_and_engine_refuse_while_serving(monkeypatch):
    monkeypatch.setattr(recipe, "any_inference_active", lambda runner: ["deepseek-v41-inference.service"])
    with pytest.raises(recipe.RecipeError, match="--while-serving"):
        recipe.cmd_stage(recipe.main.__globals__["argparse"].Namespace(
            recipe="minimax-m31-flash-exl3-3.06-tp2", run=True, only=None, accept_nc=False, force=False,
            while_serving=False, cluster=None))
    rc = recipe.main(["engine", "hy4-flash-exl3-tensorfold-tp2", "--run", "--stage"])
    assert rc == 1


def test_stage_only_rejects_unknown_steps_and_kept_packs(stubs, tmp_path):
    out = cli(["stage", "qwen38-27b-exl3-4.0-1x", "--only", "image"], stubs)
    assert out.returncode == 1 and "unknown step(s) image" in out.stderr
    d = tmp_path / "recipes" / "evil"
    d.mkdir(parents=True)
    body = (f"TITLE=e\nSOURCE=LibertAIDAI/Hy4-flash-EXL3-4bpw\nREVISION={SHA}\nWEIGHTS_STATUS=published\nSPARKS=2\n"
            "MODEL_TYPE=hy_v4_flash\nFORMAT=exl3\nBPW=4\nLAUNCHER=mia-unit:x\nSERVED_NAME=e\nLICENCES=weights=mit\n")
    for wd in ("/home/max/Documents", "~/Documents/weights", "~/Documents/weights/../x"):
        (d / "recipe.env").write_text(body + f"WEIGHTS_DIR={wd}\n")
        dry = cli(["stage", "evil"], stubs, env={"BEAST_RECIPES_DIR": str(tmp_path / "recipes")})
        if ".." in wd:
            assert dry.returncode == 1 and "no . or .. segments" in dry.stderr
            continue
        assert dry.returncode == 0 and "weights-download" not in dry.stdout and "rsync" not in dry.stdout, dry.stdout


def test_licence_classifier_is_token_anchored():
    assert recipe.licence_flags("weights=mit; engine=apache-2.0 (fork, patches, kernel kit)") == (False, False)
    assert recipe.licence_flags("weights=cc-by-nc-nd-4.0") == (True, True)
    assert recipe.licence_flags("weights=other (minimax-community)") == (True, False)
    assert recipe.licence_flags("") == (True, False)


@pytest.mark.parametrize("mutate,err", [
    (lambda d: d["engines"][0].__setitem__("stage_gap", "engine_stage"), "stage_gap"),
    (lambda d: d["engines"][0]["families"]["minimax_m3_vl"].__setitem__("ranks", "2"), "ranks"),
    (lambda d: d["engines"][0]["families"]["qwen3_5"]["ranks_by_format"].__setitem__("gguf", [1]), "ranks_by_format"),
    (lambda d: d["engines"][1]["families"]["hy_v4_flash"]["extra_gaps"][0].pop("days"), "extra_gaps"),
    (lambda d: d["engines"][1]["families"]["hy_v4_flash"].__setitem__("serves", "generator_only"), "serves"),
])
def test_a_malformed_matrix_fails_loudly(tmp_path, mutate, err):
    d = json.loads((REPO / "scripts/backends/data/recipe-engines.json").read_text())
    mutate(d)
    f = tmp_path / "m.json"
    f.write_text(json.dumps(d))
    with pytest.raises(recipe.RecipeError, match=err):
        recipe.load_data(f)
    with pytest.raises(recipe.RecipeError, match="unknown gap id"):
        recipe._gap(recipe.load_data(), "nope")


def test_native_1_2_10_carries_every_recorded_family(data):
    reg = json.loads((REPO / "scripts/backends/data/spark-families.json").read_text())
    se = next(x for x in reg["engines"] if x["name"] == "native-1.2.10")
    fams = recipe.engine_by_id(data, "tf-native-1.2.10")["families"]
    for mt, formats in se["families"].items():
        assert mt in fams, mt
        assert set(formats) <= set(fams[mt]["formats"]), mt
        for fmt in formats:
            mine = (fams[mt].get("ranks_by_format") or {}).get(fmt) or fams[mt]["ranks"]
            theirs = se["ranks"].get(f"{mt}/{fmt}") or se["ranks"].get(mt) or [1, 2]
            assert sorted(mine) == sorted(theirs), (mt, fmt, mine, theirs)
    # an unrecorded native family is unknown, not refused
    path = recipe.evaluate_engine(recipe.parse_recipe("MODEL_TYPE=brand_new\nFORMAT=bf16\n", Path("/x/t/recipe.env")),
                                  data, recipe.engine_by_id(data, "tf-native-1.2.10"), 1)
    assert path["why"].startswith("UNRECORDED")


@pytest.mark.parametrize("fmt", ["", "modelopt-ggml"])
def test_vllm_needs_a_known_quantization(tmp_path, data, fmt):
    r = mk(tmp_path, "ARCHITECTURE=Qwen3_5ForConditionalGeneration\nMODEL_TYPE=qwen3_5\n"
                     + (f"FORMAT={fmt}\n" if fmt else "") + f"WEIGHT_BYTES={15 * GIB}\nATTENTION_HEADS=24\n")
    path = recipe.evaluate_engine(r, data, recipe.engine_by_id(data, "vllm-ngc"), 1)
    assert not path["reads"] and path["verdict"] == "BLOCKED"


def test_auto_prefers_a_serving_engine_over_a_reference_one(tmp_path, data):
    body = (RECIPES / "hy4-flash-exl3-tensorfold-tp2" / "recipe.env").read_text().replace(
        "ENGINE=tf-native-1.3.3", "ENGINE=auto")
    r = mk(tmp_path, body, "hy4auto")
    p = recipe.plan(r, data)
    assert p["engine"]["id"] == "tf-native-1.3.3"
    ww = [a["engine"] for a in p["alternatives"] if a["verdict"] == "SERVABLE-WITH-WORK"]
    assert ww[-1] == "exllamav3"
    ex = next(a for a in p["alternatives"] if a["engine"] == "exllamav3")
    assert "reference-only" in ex["gaps"]


def test_an_unknown_family_gets_the_family_gap(tmp_path, data):
    p = _r(tmp_path, data, f"MODEL_TYPE=foo_new\nFORMAT=exl3\nBPW=4\nWEIGHT_BYTES={15 * GIB}\n", sparks=1)
    assert p["verdict"] == "BLOCKED" and "family" in [g["id"] for g in p["gaps"]] and p["effort_days"][0] >= 20
    txt = recipe.render_plan(p)
    assert "TPNone" not in txt and "None GiB" not in txt and "BLOCKED: these steps will not serve it" in txt


def test_init_refuses_a_reserved_name_and_reads_a_dspark_folder(tmp_path, monkeypatch, data):
    monkeypatch.setenv("BEAST_RECIPES_DIR", str(tmp_path / "recipes"))
    ck = make_ckpt(tmp_path / "Qwen-Local", dense("Qwen3ForCausalLM", "qwen3"), QWEN_TEMPLATE)
    with pytest.raises(recipe.RecipeError, match="beast-spark command"):
        recipe.init(str(ck), "hy4", 1, "auto", data, False)
    assert recipe.detect_drafter({}, {}, data, "minimax_m3_vl", ["dspark/config.json", "dspark/model-dspark-00001.safetensors"])[0] \
        == "in-checkpoint:dspark"
    assert any(d.get("method") == "dspark" for d in data["drafters"]["minimax_m3_vl"])


def test_rollback_from_nothing_is_down_and_down_returns_its_code(stubs, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / ".previous").write_text("PREVIOUS=none\nRECIPE=x\n")
    ok = cli(["rollback", "--run"], stubs, env={"BEAST_RECIPE_STATE_DIR": str(state)})
    assert ok.returncode == 0 and calls(stubs) == ["beast-spark down"]
