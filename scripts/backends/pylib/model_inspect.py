#!/usr/bin/env python3
"""Inspect a checkpoint you have never seen and say how to serve it on the Sparks.

    model-inspect.sh <owner/name@<40-hex sha>> | <owner/name> | <local dir>
                     [--json] [--write-profile NAME [--backend vllm|tensorfold] [--force]]
                     [--gpu-mem-util 0.80]

Reads ONLY metadata: config.json, generation_config.json, tokenizer_config.json
/ chat_template.jinja, hf_quant_config.json, the safetensors index and each
safetensors HEADER (a few KB via a Range request; never the weights). Works
offline on a local directory; against the Hub it is read-only and honours
HF_ENDPOINT / HF_TOKEN / HF_TOKEN_FILE / OFFLINE (see hfapi.py).

Everything it concludes is either READ from those files (architecture,
dtype, quantization, context, experts, template markers), LOOKED UP in the
vendored engine lists (data/vllm.json, data/tensorfold.json — each names the
engine commit it was read from), or an ESTIMATE that says so (memory fit,
parser suggestions). Suggestions are never applied silently: --write-profile
writes them into a draft with every uncertain line marked `# VERIFY`.
"""
from __future__ import annotations

import json
import math
import os
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hfapi  # noqa: E402
import obprofile  # noqa: E402

DATA = HERE.parent / "data"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

SPARK_BYTES = 128 * 10**9          # "128 GB unified memory" per DGX Spark (nominal, CPU + GPU share it)
RUNTIME_OVERHEAD = 5 * 10**9       # per rank: CUDA context, activations, CUDA graphs, sampler (assumption)
MIN_USEFUL_CTX = 32768             # below this much KV room a model "fits" but is not useful for agents
DTYPE_BYTES = {"F64": 8, "I64": 8, "U64": 8, "F32": 4, "I32": 4, "U32": 4, "F16": 2, "BF16": 2, "I16": 2,
               "U16": 2, "F8_E4M3": 1, "F8_E5M2": 1, "F8_E8M0": 1, "I8": 1, "U8": 1, "BOOL": 1, "F4": 0.5,
               "F6_E2M3": 0.75, "F6_E3M2": 0.75, "F8_E4M3FN": 1}
SMALL_FILES = ("config.json", "generation_config.json", "tokenizer_config.json", "chat_template.jinja",
               "chat_template.json", "hf_quant_config.json", "quantize_config.json", "quant_config.json",
               "model.safetensors.index.json", "params.json")
PICKLE_EXT = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")


# --------------------------------------------------------------------------- sources

@dataclass
class FileInfo:
    path: str
    size: int
    sha256: str | None = None


class Source:
    """A checkpoint's files: a local directory or a Hub repo pinned to a commit."""

    label: str = ""
    repo: str | None = None
    revision: str | None = None
    local: Path | None = None
    pinned_by_user: bool = True
    # Hub repos this directory says it is a byte-identical copy of (MIRROR.json): the tested-checkpoint
    # check matches them too, since TensorFold's own list names repos, never a local path.
    mirror_of: tuple[str, ...] = ()
    files: list[FileInfo]

    def read(self, path: str) -> str | None:
        raise NotImplementedError

    def header(self, path: str) -> dict | None:
        raise NotImplementedError


class LocalSource(Source):
    def __init__(self, d: Path):
        self.local = d.resolve()
        self.label = str(self.local)
        self.files = []
        for p in sorted(self.local.rglob("*")):
            rel = p.relative_to(self.local).as_posix()
            if p.is_file() and not rel.startswith(".") and "/." not in rel:
                self.files.append(FileInfo(rel, p.stat().st_size))
        self.marker_warning = None
        marker = self.local / ".openbeast-model.json"
        if marker.is_file():
            # model-fetch writes it, but it sits in a directory anyone may have copied from anywhere:
            # only a well-formed repo id and commit SHA are believed.
            try:
                m = json.loads(marker.read_text())
            except ValueError:
                m = None
            if isinstance(m, dict) and isinstance(m.get("source"), str) and isinstance(m.get("revision"), str) \
                    and obprofile.REPO_RE.match(m["source"]) and SHA_RE.match(m["revision"]):
                self.repo, self.revision = m["source"], m["revision"]
            else:
                self.marker_warning = f"{marker} is malformed (repo/revision) — ignored"
        mirror = self.local / "MIRROR.json"
        if mirror.is_file():
            # A mirror repo's own provenance file ({"this_repo": ..., "mirror_of": ..., "note": "Byte-identical
            # redistribution"}): it travels with the files, so a snapshot of the mirror carries it. It is the
            # directory's claim, not a hash check, and it only ever feeds an informational note.
            try:
                m = json.loads(mirror.read_text())
            except ValueError:
                m = None
            if isinstance(m, dict):
                self.mirror_of = tuple(m[k] for k in ("this_repo", "mirror_of")
                                       if isinstance(m.get(k), str) and obprofile.REPO_RE.match(m[k]))

    def read(self, path: str) -> str | None:
        p = self.local / path
        return p.read_text(errors="replace") if p.is_file() else None

    def header(self, path: str) -> dict | None:
        with open(self.local / path, "rb") as f:
            raw = f.read(8)
            if len(raw) < 8:
                return None
            (n,) = struct.unpack("<Q", raw)
            if n > 100 * 1024 * 1024:
                return None
            return json.loads(f.read(n))


class HubSource(Source):
    def __init__(self, repo: str, revision: str | None):
        self.repo = repo
        if revision is None:
            self.pinned_by_user = False
            revision = hfapi.resolve_sha(repo, "main")
        elif not SHA_RE.match(revision):
            self.pinned_by_user = False
            revision = hfapi.resolve_sha(repo, revision)
        self.revision = revision
        self.label = f"{repo}@{revision}"
        self.files = [FileInfo(e["path"], int(e.get("size") or 0), (e.get("lfs") or {}).get("oid"))
                      for e in hfapi.tree(repo, revision)]
        self._cache: dict[str, str | None] = {}

    def read(self, path: str) -> str | None:
        if path not in {f.path for f in self.files}:
            return None
        if path not in self._cache:
            self._cache[path] = hfapi.read_text(self.repo, self.revision, path)
        return self._cache[path]

    def header(self, path: str) -> dict | None:
        (n,) = struct.unpack("<Q", hfapi.read_range(self.repo, self.revision, path, 0, 7))
        if n > 100 * 1024 * 1024:
            return None
        return json.loads(hfapi.read_range(self.repo, self.revision, path, 8, 8 + n - 1))


def open_source(spec: str) -> Source:
    p = Path(os.path.expanduser(spec))
    if p.is_dir():
        return LocalSource(p)
    if spec.startswith(("/", ".", "~")):
        raise SystemExit(f"{spec}: no such directory")
    repo, _, rev = spec.partition("@")
    if not obprofile.REPO_RE.match(repo):
        raise SystemExit(f"{spec}: expected owner/name[@<40-hex sha>] or a local directory")
    return HubSource(repo, rev or None)


# --------------------------------------------------------------------------- analysis

def _load_json(src: Source, path: str) -> dict:
    t = src.read(path)
    if not t:
        return {}
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _text_cfg(cfg: dict) -> dict:
    """The language model's config: text_config / llm_config merged over the top level."""
    merged = dict(cfg)
    for key in ("text_config", "llm_config", "language_config"):
        sub = cfg.get(key)
        if isinstance(sub, dict):
            merged.update(sub)
    return merged


def quantization(cfg: dict, hf_quant: dict, quantize_cfg: dict) -> dict:
    """What the weights are stored as. Returns {method, scheme, bits, vllm, tensorfold, detail}."""
    q = None
    for s in (cfg, cfg.get("text_config") or {}):
        for key in ("quantization_config", "quantization"):
            if isinstance(s.get(key), dict) and s.get(key):
                q = s[key]
                break
        if q:
            break
    if q is None and isinstance(hf_quant.get("quantization"), dict):
        q = dict(hf_quant["quantization"], quant_method="modelopt")        # older ModelOpt layout
    if q is None and quantize_cfg.get("bits"):
        q = dict(quantize_cfg, quant_method=quantize_cfg.get("quant_method") or "gptq")
    if not q:
        dt = str(_text_cfg(cfg).get("torch_dtype") or _text_cfg(cfg).get("dtype") or cfg.get("torch_dtype") or "?")
        return {"method": None, "scheme": f"unquantized ({dt})", "bits": 16 if "16" in dt else None,
                "vllm": None, "tensorfold": None, "detail": {}}
    method = str(q.get("quant_method") or "").lower()
    if not method and "bits" in q:
        mode = str(q.get("mode") or "affine").lower()
        return {"method": "mlx" if mode == "affine" else f"mlx-{mode}",
                "scheme": f"MLX {mode} {q.get('bits')}-bit, groups of {q.get('group_size', 64)}",
                "bits": q.get("bits"), "vllm": None, "tensorfold": "mlx",
                "detail": {"bits": q.get("bits"), "group_size": q.get("group_size", 64), "mode": mode}}
    groups = q.get("config_groups") or {}
    weights = [(g.get("weights") or {}) for g in groups.values() if isinstance(g, dict)]
    formats = {str(q.get("format") or "")} | {str(g.get("format") or "") for g in groups.values()
                                              if isinstance(g, dict)}
    wbits = sorted({w.get("num_bits") for w in weights if w.get("num_bits")})
    wtypes = {str(w.get("type", "")) for w in weights}
    algo = str(q.get("quant_algo") or "").upper()
    scheme = method
    if method == "modelopt":
        parts = []
        if "NVFP4" in algo or (4 in wbits and "float" in wtypes):
            parts.append("NVFP4")
        if "FP8" in algo or (8 in wbits and "float" in wtypes):
            parts.append("FP8")
        if "MXFP8" in algo:
            parts.append("MXFP8")
        scheme = "ModelOpt " + (" + ".join(parts) or algo or "?") + (" (mixed precision)" if "MIXED" in algo else "")
    elif method == "compressed-tensors":
        f = ",".join(sorted(x for x in formats if x))
        if "nvfp4-pack-quantized" in formats:
            scheme = "compressed-tensors NVFP4"
        elif "float-quantized" in formats or ("float" in wtypes and 8 in wbits):
            scheme = "compressed-tensors FP8"
        elif "pack-quantized" in formats:
            scheme = f"compressed-tensors int{'/'.join(map(str, wbits)) or '?'} (weight-only)"
        elif "int-quantized" in formats:
            scheme = "compressed-tensors INT8 (W8A8)"
        else:
            scheme = f"compressed-tensors ({f or '?'})"
    elif method == "fp8":
        scheme = "FP8" + (f" block {q.get('weight_block_size')}" if q.get("weight_block_size") else "")
    elif method in ("awq", "gptq", "auto_gptq", "auto_awq"):
        scheme = f"{method.upper()} int{q.get('bits') or q.get('w_bit') or '?'}, group {q.get('group_size') or q.get('q_group_size') or '?'}"
    elif method == "mxfp4":
        scheme = "MXFP4"
    elif method == "bitsandbytes":
        scheme = "bitsandbytes " + ("4-bit" if q.get("load_in_4bit") else "8-bit" if q.get("load_in_8bit") else "?")
    elif method == "exl3":
        scheme = f"EXL3 {q.get('bits', '?')} bpw" + (f", {q['codebook']} codebook" if q.get("codebook") else "") \
            + (f", scope {q['scope']}" if q.get("scope") else "")
    detail = {"quant_algo": algo or None, "formats": sorted(x for x in formats if x), "weight_bits": wbits}
    if method == "exl3":
        # the fields an engine's EXL3 variant check reads (TensorFold glm5_next: bits, codebook, scope)
        detail["exl3"] = {k: q.get(k) for k in ("bits", "codebook", "scope", "head_bits", "version")}
    return {"method": method, "scheme": scheme, "bits": q.get("bits") or (wbits[0] if wbits else None),
            "vllm": method, "tensorfold": method, "detail": detail}


RANK_SLICED_RE = re.compile(r"\.rank\d+\.(trellis|suh|svh|mcg)$")


# Logical values per stored element, when packing is the only explanation.
def _packing(qmethod: str | None, dtype: str, qbits) -> int:
    if dtype == "U8" and qmethod in ("modelopt", "compressed-tensors", "mxfp4"):
        return 2
    if dtype in ("U32", "I32") and qmethod in ("mlx", "gptq", "awq", "auto_gptq", "auto_awq", "compressed-tensors"):
        try:
            return 32 // int(qbits or 4)
        except (TypeError, ValueError, ZeroDivisionError):
            return 8
    return 1


def weights(src: Source, q: dict, max_headers: int = 128) -> dict:
    st = [f for f in src.files if f.path.endswith(".safetensors")]
    pickles = [f.path for f in src.files if f.path.endswith(PICKLE_EXT)]
    ggufs = [f.path for f in src.files if f.path.endswith(".gguf")]
    total_bytes = sum(f.size for f in st)
    by_dtype: dict[str, int] = {}
    header_bytes = 0
    headers_read = 0
    header_error = None
    sliced = False
    if st and len(st) <= max_headers:
        try:
            for f in st:
                h = src.header(f.path) or {}
                headers_read += 1
                for name, t in h.items():
                    if name == "__metadata__" or not isinstance(t, dict):
                        continue
                    sliced = sliced or bool(RANK_SLICED_RE.search(name))
                    n = math.prod(t.get("shape") or [1])
                    by_dtype[t.get("dtype", "?")] = by_dtype.get(t.get("dtype", "?"), 0) + n
                    header_bytes += int(n * DTYPE_BYTES.get(t.get("dtype", ""), 2))
        except (hfapi.HubError, OSError, ValueError, struct.error) as e:
            header_error = str(e)[:200]
    stored = sum(by_dtype.values())
    logical = sum(n * _packing(q.get("method"), d, q.get("bits")) for d, n in by_dtype.items())
    index = _load_json(src, "model.safetensors.index.json")
    wmap = index.get("weight_map") if isinstance(index.get("weight_map"), dict) else {}
    return {
        "safetensors_files": len(st),
        "file_bytes": total_bytes,
        # header-derived bytes equal file bytes for a real checkpoint; either is the weight footprint
        "weight_bytes": total_bytes or header_bytes,
        "index_total_size": (index.get("metadata") or {}).get("total_size"),
        # EXL3 tensors stored per tensor-parallel rank (experts.N.proj.rank0.trellis): a runner-specific
        # layout (cbert33's "rank-sliced" TP2 copies) that readers of the plain names cannot load
        # (from the shard headers; the index too, when it is under hfapi's 16 MB read cap)
        "rank_sliced": sliced or any(RANK_SLICED_RE.search(k) for k in wmap),
        "stored_elements_by_dtype": by_dtype,
        "stored_elements": stored,
        "logical_params_estimate": logical,
        "headers_read": headers_read,
        "header_error": header_error,
        "pickle_files": pickles,
        "gguf_files": ggufs,
    }


def context(tc: dict) -> dict:
    native = tc.get("max_position_embeddings") or tc.get("max_sequence_length") or tc.get("seq_length") \
        or tc.get("n_positions") or tc.get("model_max_length")
    if not isinstance(native, int) or isinstance(native, bool) or native <= 0:
        native = None                               # a config value we cannot trust as a token count
    rope = tc.get("rope_scaling") or tc.get("rope_parameters") or None
    out = {"native": native, "rope": rope, "sliding_window": tc.get("sliding_window")}
    if isinstance(rope, dict):
        rtype = rope.get("rope_type") or rope.get("type")
        factor = rope.get("factor")
        orig = rope.get("original_max_position_embeddings")
        if rtype in ("yarn", "dynamic", "longrope", "llama3") and factor and orig:
            out["note"] = (f"rope {rtype} x{factor} over an original {orig}-token window: the config already "
                           f"declares {native}; going past it needs a rope override the model card must endorse")
        elif rtype in ("yarn",) and factor and native:
            out["extendable_to"] = int(native * factor)
            out["note"] = f"yarn x{factor} declared: some cards extend to {int(native * factor)} tokens — VERIFY"
    return out


def moe(tc: dict) -> dict | None:
    total = next((tc[k] for k in ("num_experts", "num_local_experts", "n_routed_experts", "moe_num_experts",
                                  "num_routed_experts") if isinstance(tc.get(k), int) and tc.get(k) > 1), None)
    if not total:
        return None
    active = next((tc[k] for k in ("num_experts_per_tok", "moe_topk", "top_k", "num_experts_per_token",
                                   "moe_k", "router_top_k") if isinstance(tc.get(k), int)), None)
    return {"experts": total, "active_per_token": active, "shared_experts": tc.get("n_shared_experts")
            or tc.get("num_shared_experts"), "note": "total params decide MEMORY; active params decide SPEED"}


def kv_per_token(tc: dict) -> dict:
    layers = tc.get("num_hidden_layers") or tc.get("n_layer") or tc.get("num_layers")
    if not layers:
        return {"bytes": None, "why": "num_hidden_layers not in config"}
    lt = tc.get("layer_types")
    attn = layers
    how = "every layer"
    if isinstance(lt, list) and lt:
        full = sum(1 for x in lt if str(x) in ("full_attention", "attention", "global", "full"))
        slide = sum(1 for x in lt if "sliding" in str(x) or "local" in str(x))
        attn = full + slide
        how = f"{full} full-attention layers of {len(lt)} (from layer_types)" + \
            (f" + {slide} sliding-window layers (bounded by the window; counted in full here)" if slide else "")
    elif tc.get("full_attention_interval"):
        attn = layers // int(tc["full_attention_interval"])
        how = f"1 in {tc['full_attention_interval']} layers (full_attention_interval)"
    if tc.get("kv_lora_rank"):
        per_layer = int(tc["kv_lora_rank"]) + int(tc.get("qk_rope_head_dim") or 0)
        b = attn * per_layer * 2
        return {"bytes": b, "attention_layers": attn, "why": f"MLA latent {per_layer} x {attn} layers x 2 B",
                "how": how, "splits_across_tp": False}
    heads = tc.get("num_attention_heads") or tc.get("n_head")
    kvh = tc.get("num_key_value_heads") or tc.get("num_kv_heads") or heads
    hd = tc.get("head_dim") or (tc.get("hidden_size") // heads if tc.get("hidden_size") and heads else None)
    if not (kvh and hd):
        return {"bytes": None, "why": "head counts not in config"}
    b = 2 * attn * int(kvh) * int(hd) * 2
    return {"bytes": b, "attention_layers": attn, "kv_heads": kvh, "head_dim": hd, "how": how,
            "why": f"2 (K,V) x {attn} layers x {kvh} kv-heads x {hd} dim x 2 B (bf16 KV; fp8 KV halves it)",
            "splits_across_tp": int(kvh) >= 2,
            "note": "linear-attention / SSM layers keep a fixed per-sequence state, not per-token KV"
            if attn < layers else None}


def fit(weight_bytes: int, kv: dict, native: int | None, util: float) -> dict:
    budget = SPARK_BYTES * util
    kvb = kv.get("bytes")
    out = {"assumptions": {
        "spark_bytes": SPARK_BYTES, "gpu_memory_utilization": util, "budget_per_spark": int(budget),
        "runtime_overhead_per_rank": RUNTIME_OVERHEAD, "tp2_weight_slack": 0.03,
        "min_useful_context": MIN_USEFUL_CTX,
        "method": ("weights = safetensors bytes (exact); per-rank need = weights/TP x (1 + slack for replicated "
                   "norms/embeddings) + a fixed runtime overhead; what is left of util x 128 GB is KV cache. "
                   "The OS lives in the same 128 GB — util above ~0.85 starves it.")}}
    for tp in (1, 2):
        need = weight_bytes / tp * (1.03 if tp == 2 else 1.0) + RUNTIME_OVERHEAD
        room = budget - need
        per_tok = (kvb / tp if kv.get("splits_across_tp") else kvb) if kvb else None
        tokens = int(room // per_tok) if per_tok and room > 0 else (0 if room <= 0 else None)
        if room <= 0:
            verdict = "does not fit"
        elif tokens is None:
            verdict = "fits (KV size unknown)"
        elif tokens < MIN_USEFUL_CTX:
            verdict = "tight"
        else:
            verdict = "fits"
        suggest = None
        if tokens:
            cap = min(tokens, native or tokens)
            suggest = max(4096, (cap // 8192) * 8192) if cap >= 8192 else cap
        out[f"tp{tp}"] = {"per_rank_need": int(need), "kv_room_per_rank": int(room), "kv_tokens_total": tokens,
                          "verdict": verdict, "suggested_max_model_len": suggest}
    one, two = out["tp1"]["verdict"], out["tp2"]["verdict"]
    if one == "fits":
        out["verdict"] = "fits on ONE Spark (TP=1); TP=2 only buys KV room / aggregate throughput"
    elif two in ("fits", "fits (KV size unknown)"):
        out["verdict"] = "needs BOTH Sparks (TP=2)" + (" — one Spark is tight" if one == "tight" else "")
    elif two == "tight":
        out["verdict"] = "only just fits on two Sparks: little KV room left — lower GPU util headroom or use a smaller quant"
    else:
        out["verdict"] = "does NOT fit on two Sparks at this quantization"
    return out


# --------------------------------------------------------------------------- chat template

def chat_template(src: Source, tok_cfg: dict) -> dict:
    names = []
    t = src.read("chat_template.jinja")
    if t:
        names.append("chat_template.jinja")
    else:
        ct = tok_cfg.get("chat_template")
        if isinstance(ct, str):
            t, names = ct, ["tokenizer_config.json"]
        elif isinstance(ct, list):
            by = {d.get("name"): d.get("template") for d in ct if isinstance(d, dict)}
            t = by.get("tool_use") or by.get("default") or next(iter(by.values()), None)
            names = [f"tokenizer_config.json[{'tool_use' if 'tool_use' in by else 'default'}]"]
        if not t:
            j = _load_json(src, "chat_template.json")
            t = j.get("chat_template") if isinstance(j.get("chat_template"), str) else None
            names = ["chat_template.json"] if t else []
    if not t:
        return {"present": False, "source": None}
    reasoning_markers = [m for m in ("<think>", "</think>", "<|begin_of_thought|>", "<seed:think>", "[THINK]",
                                     "<|channel|>analysis", "◁think▷", "<thinking>", "<|START_THINKING|>",
                                     "<reasoning>") if m in t]
    tool_markers = [m for m in ("<tool_call>", "<function=", "<parameter=", "<|python_tag|>", "[TOOL_CALLS]",
                                "<|tool_call_begin|>", "<|tool_calls_section_begin|>", "<｜tool▁calls▁begin｜>",
                                "<|channel|>", "<|call|>", "<arg_key>", "<minimax:tool_call>",
                                "<|action_start|>", "<seed:tool_call>", "<|tool_call|>", "functools[",
                                "<start_function_call>", "<|python_start|>", "<|START_ACTION|>", "<tool_calls>",
                                "<function_calls>", "<|tool_calls|>") if m in t]
    renders_tools = bool(re.search(r"\{[%{][^%}]*\btools\b", t))
    json_calls = renders_tools and bool(re.search(r"tool_call\.arguments\s*\|\s*tojson|\"name\"\s*:|"
                                                  r"'name'\s*:|\"parameters\"", t))
    gen = t.split("add_generation_prompt")[-1] if "add_generation_prompt" in t else ""
    return {
        "present": True, "source": names[0] if names else None, "length": len(t),
        "renders_tools": renders_tools,
        "tool_markers": tool_markers,
        "json_tool_calls": json_calls,
        "reasoning_markers": reasoning_markers,
        "thinking_toggle": "enable_thinking" in t or "thinking" in re.findall(r"\b(thinking)\b", t)[:1],
        "opens_think_in_prompt": "<think>" in gen,
        "renders_reasoning_content": "reasoning_content" in t,
        "tool_role": bool(re.search(r"['\"]tool['\"]", t)),
    }


# (vLLM parser, confidence, why) rules; checked against the vendored registry
def suggest_tool_parser(tpl: dict, model_type: str, registry: list[str]) -> list[dict]:
    m = set(tpl.get("tool_markers") or [])
    mt = (model_type or "").lower()
    out: list[tuple[str, str, str]] = []
    if not tpl.get("present"):
        return [{"parser": None, "confidence": "none", "why": "no chat template found — vLLM cannot render tools"}]
    if not tpl.get("renders_tools"):
        return [{"parser": None, "confidence": "high",
                 "why": "the chat template never renders `tools`: the model was not trained to see tool "
                        "schemas; OpenBeast's tools will not work with it as-is (a CHAT_TEMPLATE override that "
                        "adds tools is a research project, not a flag)"}]
    if "<tool_call>" in m and ("<function=" in m or "<parameter=" in m):
        out.append(("qwen3_xml", "high" if mt.startswith("qwen3") else "medium",
                    "<tool_call><function=…><parameter=…> XML (the Qwen3-Coder / Qwen3.x format)"))
        out.append(("qwen3_coder", "medium", "same XML format; at the vendored commit qwen3_coder and qwen3_xml "
                    "are ONE parser class (Qwen3EngineToolParser)"))
    if "<arg_key>" in m:
        out.append(("glm47" if "glm4" in mt or "glm5" in mt else "glm45", "high" if mt.startswith("glm") else "medium",
                    "<tool_call>name<arg_key>…<arg_value>… (GLM-4.5+ format)"))
    if "<minimax:tool_call>" in m:
        out.append(("minimax_m2", "high", "<minimax:tool_call> marker"))
    if "<seed:tool_call>" in m:
        out.append(("seed_oss", "high", "<seed:tool_call> marker"))
    if "<|tool_calls_section_begin|>" in m or "<|tool_call_begin|>" in m:
        out.append(("kimi_k2", "high" if "kimi" in mt else "medium", "<|tool_calls_section_begin|> (Kimi K2 format)"))
    if "<｜tool▁calls▁begin｜>" in m:
        p = "deepseek_v31" if "deepseek_v3" in mt else "deepseek_v3"
        out.append((p, "medium", "DeepSeek <｜tool▁calls▁begin｜> markers; v3 vs v3.1 differ in the call body — VERIFY"))
    if "<|channel|>" in m and "<|call|>" in m:
        out.append(("openai", "high", "harmony <|channel|>…<|call|> (gpt-oss)"))
    if "[TOOL_CALLS]" in m:
        out.append(("mistral", "high", "[TOOL_CALLS] marker (Mistral)"))
    if "<|python_start|>" in m:
        out.append(("llama4_pythonic", "medium", "<|python_start|> (Llama 4 pythonic calls)"))
    if "<|python_tag|>" in m:
        out.append(("llama3_json", "high" if "llama" in mt else "medium",
                    "<|python_tag|> + JSON calls (Llama 3.1/3.2/3.3 format)"))
    if "<|action_start|>" in m:
        out.append(("internlm", "high", "<|action_start|><|plugin|> (InternLM2)"))
    if "functools[" in m:
        out.append(("phi4_mini_json", "high", "functools[…] (Phi-4-mini)"))
    if "<start_function_call>" in m:
        out.append(("functiongemma", "high", "<start_function_call> (FunctionGemma)"))
    if "<|tool_call|>" in m and "granite" in mt:
        out.append(("granite", "high", "<|tool_call|> (Granite)"))
    if "<tool_call>" in m and not out:
        out.append(("hermes", "high" if tpl.get("json_tool_calls") else "medium",
                    "<tool_call>{json}</tool_call> (Hermes format — also Qwen2.5)"))
    if not out and mt.startswith("gemma4"):
        out.append(("gemma4", "low", "model_type gemma4; no known marker matched"))
    if not out and "llama" in mt and tpl.get("json_tool_calls"):
        out.append(("llama3_json", "low", "Llama model, tools rendered as JSON in plain content"))
    if not out:
        out.append((None, "low", "tools are rendered but no known call marker appears (JSON or pythonic calls "
                    "in plain content?) — try pythonic or llama3_json and let conformance.sh decide"))
    res = []
    for name, conf, why in out:
        entry = {"parser": name, "confidence": conf, "why": why}
        if name and registry and name not in registry:
            entry["confidence"] = "none"
            entry["why"] += " — but NOT registered in the vendored vLLM (needs a newer image)"
        res.append(entry)
    return res


REASONING_BY_TYPE = [
    ("qwen3", "qwen3"), ("deepseek_v4", "deepseek_v4"), ("deepseek_v3", "deepseek_v3"), ("deepseek", "deepseek_r1"),
    ("glm5", "glm47"), ("glm4", "glm45"), ("kimi_k3", "kimi_k3"), ("kimi", "kimi_k2"), ("minimax_m3", "minimax_m3"),
    ("minimax", "minimax_m2"), ("gpt_oss", "openai_gptoss"), ("seed_oss", "seed_oss"), ("ernie", "ernie45"),
    ("granite", "granite"), ("olmo3", "olmo3"), ("nemotron_h", "nemotron_v3"), ("step3", "step3"),
    ("hunyuan", "hunyuan_a13b"), ("mistral", "mistral"), ("gemma4", "gemma4"), ("mimo", "mimo"),
]


def suggest_reasoning_parser(tpl: dict, model_type: str, registry: list[str]) -> list[dict]:
    rm = set(tpl.get("reasoning_markers") or [])
    mt = (model_type or "").lower()
    out = []
    fam = next((p for prefix, p in REASONING_BY_TYPE if mt.startswith(prefix)), None)
    if "<|channel|>analysis" in rm:
        out.append(("openai_gptoss", "high", "harmony analysis channel"))
    elif "[THINK]" in rm:
        out.append(("mistral", "high" if "mistral" in mt else "medium", "[THINK] blocks (Magistral)"))
    elif "<seed:think>" in rm:
        out.append(("seed_oss", "high", "<seed:think>"))
    elif "◁think▷" in rm:
        out.append(("kimi_k2", "medium", "◁think▷ (Kimi)"))
    elif "<think>" in rm or "</think>" in rm:
        if fam:
            out.append((fam, "high", f"<think> blocks and model_type {model_type}"))
        else:
            out.append(("deepseek_r1", "medium" if tpl.get("opens_think_in_prompt") else "low",
                        "<think>…</think> with no family match; deepseek_r1 tolerates the opening tag living in "
                        "the prompt" + (" (this template opens it in the generation prompt)"
                                        if tpl.get("opens_think_in_prompt") else "")))
    elif fam and tpl.get("thinking_toggle"):
        out.append((fam, "low", f"template has a thinking toggle; model_type {model_type}"))
    if not out:
        return [{"parser": None, "confidence": "medium" if tpl.get("present") else "none",
                 "why": "no reasoning markers in the template: probably not a thinking model (conformance.sh "
                        "will show any inline <think> in content)"}]
    res = []
    for name, conf, why in out:
        entry = {"parser": name, "confidence": conf, "why": why}
        if registry and name not in registry:
            entry["confidence"] = "none"
            entry["why"] += " — but NOT registered in the vendored vLLM"
        res.append(entry)
    return res


# --------------------------------------------------------------------------- engine support

def _data(name: str) -> dict:
    try:
        return json.loads((DATA / f"{name}.json").read_text())
    except (OSError, ValueError):
        return {}


VLLM_QUANT_ALIASES = {"auto_gptq": "gptq", "auto_awq": "awq"}


def vllm_support(archs: list[str], q: dict, trust_remote_code: bool, auto_map: bool) -> dict:
    v = _data("vllm")
    commit = (v.get("_provenance") or {}).get("commit", "?")
    gen, pool, prev = set(v.get("generation_architectures", [])), set(v.get("pooling_architectures", [])), \
        v.get("previously_supported", {})
    status, notes = "unknown", []
    hit = [a for a in archs if a in gen]
    if hit:
        status = "supported"
        notes.append(f"{hit[0]} is in vLLM's model registry @{commit[:12]}")
    elif any(a in pool for a in archs):
        status = "unsupported"
        notes.append("registered only as a pooling/embedding model — no chat generation")
    elif any(a in prev for a in archs):
        a = next(a for a in archs if a in prev)
        status = "unsupported"
        notes.append(f"{a} was REMOVED from vLLM after {prev[a]}")
    else:
        notes.append(f"unknown to vLLM @{commit[:12]}: needs a newer vLLM image, the Transformers backend "
                     "(--model-impl transformers, if the image's transformers knows the model_type)"
                     + (", or trust_remote_code (the repo ships modeling code in auto_map: read it first)"
                        if auto_map else ", or trust_remote_code — but this repo ships no modeling code "
                        "(no auto_map), so only a newer vLLM will do"))
    qm = q.get("method")
    if qm:
        name = VLLM_QUANT_ALIASES.get(qm, qm)
        if qm.startswith("mlx"):
            status = "unsupported"
            notes.append("MLX-quantized weights: vLLM cannot load them (TensorFold or MLX can)")
        elif qm == "exl3":
            status = "unsupported"
            notes.append("EXL3 weights: vLLM cannot load them")
        elif name not in v.get("quantization_methods", []):
            if status == "supported":
                status = "unknown"
            notes.append(f"quant_method {qm!r} is not in vLLM's quantization list @{commit[:12]}")
    return {"engine": "vllm", "status": status, "notes": notes, "vendored_commit": commit}


def _exl3_norm(v):
    """TensorFold compares bits as int(v) (families/glm5_next/__init__.py:39): 4.0 and "4" are 4."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def tensorfold_support(cfg: dict, q: dict, source_label: str, tp: int | None = None, *,
                       also_known_as: tuple[str, ...] = (), mirror_of: tuple[str, ...] = (),
                       rank_sliced: bool = False) -> dict:
    t = _data("tensorfold")
    commit = (t.get("_provenance") or {}).get("commit", "?")
    mt = str(cfg.get("model_type") or (cfg.get("text_config") or {}).get("model_type") or "")
    fam_name, fam = next(((k, f) for k, f in (t.get("families") or {}).items() if mt in f.get("model_types", [])),
                         (None, None))
    if fam is None:
        return {"engine": "tensorfold", "status": "unsupported", "vendored_commit": commit, "family": None,
                "notes": [f"no TensorFold family claims model_type {mt!r} @{commit[:12]} (families: "
                          f"{', '.join(sorted(x for f in (t.get('families') or {}).values() for x in f['model_types']))})"]}
    notes = [f"family {fam_name} ({fam['title']})"]
    if "cuda" not in fam.get("backends", []):
        return {"engine": "tensorfold", "status": "unsupported", "family": fam_name, "vendored_commit": commit,
                "notes": notes + ["this family has no CUDA engine (Apple Silicon only)"]}
    status = "supported"
    qm = q.get("method") or None
    accepted = fam.get("cuda_quant_methods") or ["mlx"]
    if (qm or None) not in accepted:
        status = "unsupported"
        notes.append(f"CUDA engine reads {', '.join(str(a) for a in accepted)} weights; this checkpoint is "
                     f"{q.get('scheme')}")
    elif qm == "mlx":
        d = q.get("detail") or {}
        want = fam.get("cuda_mlx_quantization")
        if want and [d.get("bits"), d.get("group_size")] != list(want):
            status = "unsupported"
            notes.append(f"CUDA kernels read MLX {want[0]}-bit in groups of {want[1]}; this is "
                         f"{d.get('bits')}-bit groups of {d.get('group_size')}")
        elif fam.get("cuda_affine_bits") and (d.get("bits") not in fam["cuda_affine_bits"]
                                              or d.get("group_size") not in (fam.get("cuda_affine_groups") or [])):
            status = "unsupported"
            notes.append(f"CUDA reads MLX affine bits {fam['cuda_affine_bits']} in groups {fam['cuda_affine_groups']}")
    elif qm == "exl3":
        want = fam.get("cuda_exl3_variant")
        if isinstance(want, dict):
            found = (q.get("detail") or {}).get("exl3") or {}
            got = {k: found.get(k) for k in want}
            if {k: (_exl3_norm(v) if k == "bits" else v) for k, v in got.items()} != want:
                status = "unsupported"
                notes.append("the CUDA engine reads EXL3 only as " + ", ".join(f"{k} {v}" for k, v in want.items())
                             + "; this checkpoint has " + ", ".join(f"{k} {v}" for k, v in got.items())
                             + " (TensorFold refuses it at start)")
        if status == "supported" and rank_sliced:
            status = "unsupported"
            notes.append("rank-sliced EXL3 (experts.N.proj.rank0.trellis …): TensorFold reads the plain tensor "
                         "names, so this runner-specific layout does not load — use the unsliced original")
    elif qm in ("modelopt", "compressed-tensors"):
        bits = set((q.get("detail") or {}).get("weight_bits") or [])
        if bits - {4, 8}:
            status = "unsupported"
            notes.append("TensorFold reads only NVFP4 / FP8 ModelOpt or compressed-tensors weights")
    tps = list(fam.get("tp") or [])
    only2 = fam.get("tp2_quant_methods")
    if 2 in tps and only2 and (qm or None) not in only2:
        tps.remove(2)
        notes.append(f"this format runs on ONE rank only (two ranks need {', '.join(only2)} weights)")
    if tps:
        notes.append(f"ranks: {' or '.join(map(str, tps))}")
    notes.extend(fam.get("notes", []))
    tested = fam.get("tested_checkpoints", [])
    repo = source_label.split("@")[0]
    # a local directory is tested when model-fetch's marker (also_known_as) or its own MIRROR.json names a
    # tested repo: TensorFold's list is of repo ids, and a copy of one is the same checkpoint
    named = next((r for r in (repo, *also_known_as) if r in tested), None)
    via_mirror = None if named else next((r for r in mirror_of if r in tested), None)
    is_tested = bool(named or via_mirror)
    if status == "supported" and via_mirror:
        notes.append(f"tested checkpoint by its MIRROR.json: a byte-identical copy of {via_mirror} (the "
                     "directory's own claim — compare MANIFEST/SHA256SUMS hashes if it matters)")
    elif status == "supported" and not is_tested:
        notes.append("not a tested checkpoint: TensorFold serves it with an 'untested' note — exact to serial "
                     f"decoding, speed and quality unmeasured (tested: {', '.join(tested)})")
    if tp and tps and tp not in tps:
        status = "unsupported"
        notes.append(f"TP={tp} is not allowed for this family")
    return {"engine": "tensorfold", "status": status, "family": fam_name, "vendored_commit": commit,
            "notes": notes, "tested": is_tested, "ranks": tps or None}


# --------------------------------------------------------------------------- report

def inspect(src: Source, util: float = 0.80) -> dict:
    cfg = _load_json(src, "config.json")
    if not cfg:
        raise SystemExit(f"{src.label}: no config.json — not a transformers-style checkpoint "
                         "(GGUF / MLX-LM-only layouts are not servable by vLLM or TensorFold)")
    tc = _text_cfg(cfg)
    gen_cfg = _load_json(src, "generation_config.json")
    tok_cfg = _load_json(src, "tokenizer_config.json")
    q = quantization(cfg, _load_json(src, "hf_quant_config.json"), _load_json(src, "quantize_config.json"))
    w = weights(src, q)
    ctx = context(tc)
    kv = kv_per_token(tc)
    tpl = chat_template(src, tok_cfg)
    archs = list(cfg.get("architectures") or [])
    model_type = str(cfg.get("model_type") or "")
    text_type = str((cfg.get("text_config") or {}).get("model_type") or "")
    v = _data("vllm")
    auto_map = bool(cfg.get("auto_map") or tok_cfg.get("auto_map"))
    tool_s = suggest_tool_parser(tpl, text_type or model_type, v.get("tool_parsers", []))
    reas_s = suggest_reasoning_parser(tpl, text_type or model_type, v.get("reasoning_parsers", []))
    fits = fit(w["weight_bytes"], kv, ctx.get("native"), util)
    warnings = []
    if not src.pinned_by_user:
        warnings.append(f"no commit given: resolved to {src.revision} — pin THAT in the profile, never a branch")
    if w["pickle_files"]:
        warnings.append(f"pickled weights present ({', '.join(w['pickle_files'][:3])}…): model-fetch skips them "
                        "by default (unpickling runs code)")
    if auto_map:
        warnings.append("the repo ships custom modeling/tokenizer code (auto_map): trust_remote_code would RUN it")
    if getattr(src, "marker_warning", None):
        warnings.append(src.marker_warning)
    if not w["safetensors_files"]:
        warnings.append("no .safetensors files — vLLM/TensorFold want safetensors")
    return {
        "source": src.label, "repo": src.repo, "revision": src.revision, "local": str(src.local) if src.local else None,
        "architectures": archs, "model_type": model_type, "text_model_type": text_type or None,
        "dtype": tc.get("torch_dtype") or tc.get("dtype") or cfg.get("torch_dtype"),
        "quantization": q, "weights": w, "context": ctx, "moe": moe(tc), "kv_cache": kv, "fit": fits,
        "generation_defaults": {k: gen_cfg.get(k) for k in ("temperature", "top_p", "top_k", "min_p",
                                                             "repetition_penalty", "max_new_tokens") if k in gen_cfg},
        "chat_template": tpl, "auto_map": auto_map, "vision": bool(cfg.get("vision_config")),
        "suggestions": {"tool_call_parser": tool_s, "reasoning_parser": reas_s},
        "engines": {"vllm": vllm_support(archs, q, False, auto_map),
                    "tensorfold": tensorfold_support(
                        cfg, q, src.label, also_known_as=tuple(filter(None, [src.repo])),
                        mirror_of=tuple(src.mirror_of), rank_sliced=bool(w.get("rank_sliced")))},
        "warnings": warnings,
    }


def _gb(n) -> str:
    return "?" if n is None else f"{n / 1e9:.1f} GB"


def render(r: dict) -> str:
    L = []
    add = L.append
    add(f"Model      {r['source']}")
    add(f"Arch       {', '.join(r['architectures']) or '?'}   model_type {r['model_type']}"
        + (f" (text: {r['text_model_type']})" if r.get("text_model_type") else "")
        + ("   +vision" if r["vision"] else ""))
    q, w = r["quantization"], r["weights"]
    add(f"Weights    {q['scheme']}   {_gb(w['weight_bytes'])} in {w['safetensors_files']} safetensors file(s)")
    if w["stored_elements"]:
        dt = ", ".join(f"{k} {v / 1e9:.2f}B" for k, v in sorted(w["stored_elements_by_dtype"].items()))
        add(f"Params     ~{w['logical_params_estimate'] / 1e9:.1f}B logical (estimate: packed dtypes unpacked)"
            f"   stored elements: {dt}")
    c = r["context"]
    add(f"Context    native {c.get('native') or '?'}" + (f"   rope {json.dumps(c['rope'])[:80]}" if c.get("rope") else ""))
    if c.get("note"):
        add(f"           {c['note']}")
    if r["moe"]:
        m = r["moe"]
        add(f"MoE        {m['experts']} experts, {m['active_per_token']} active/token"
            + (f", {m['shared_experts']} shared" if m.get("shared_experts") else "") + f"  ({m['note']})")
    kv = r["kv_cache"]
    if kv.get("bytes"):
        add(f"KV cache   {kv['bytes'] / 1024:.0f} KiB/token — {kv['why']}; {kv.get('how', '')}")
    f = r["fit"]
    add(f"Fit        {f['verdict']}")
    for tp in ("tp1", "tp2"):
        x = f[tp]
        add(f"  {tp.upper()}: need {_gb(x['per_rank_need'])}/rank of {_gb(f['assumptions']['budget_per_spark'])} "
            f"(util {f['assumptions']['gpu_memory_utilization']}) → {x['verdict']}"
            + (f", KV room ~{x['kv_tokens_total']:,} tokens" if x.get("kv_tokens_total") else ""))
    add(f"  ({f['assumptions']['method']})")
    t = r["chat_template"]
    if t.get("present"):
        add(f"Template   {t['source']}: tools {'rendered' if t['renders_tools'] else 'NOT rendered'}; "
            f"tool markers {t['tool_markers'] or 'none'}; reasoning markers {t['reasoning_markers'] or 'none'}"
            + ("; thinking toggle" if t.get("thinking_toggle") else ""))
    else:
        add("Template   none found (vLLM needs CHAT_TEMPLATE)")
    for kind, label in (("tool_call_parser", "--tool-call-parser"), ("reasoning_parser", "--reasoning-parser")):
        for s in r["suggestions"][kind]:
            add(f"Suggest    {label} {s['parser'] or '(none)'}  [{s['confidence']}] — {s['why']}")
    for e in ("vllm", "tensorfold"):
        x = r["engines"][e]
        add(f"{e:10} {x['status'].upper()} (vendored @{x['vendored_commit'][:12]}) — " + "; ".join(x["notes"]))
    if r["generation_defaults"]:
        add(f"Sampling   generation_config defaults {json.dumps(r['generation_defaults'])} (vLLM applies these)")
    for wmsg in r["warnings"]:
        add(f"WARNING    {wmsg}")
    return "\n".join(L)


_UNSAFE = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")


def _comment(text) -> str:
    """Checkpoint-derived text for a comment: no control characters, so it can never start a line."""
    return _UNSAFE.sub(" ", str(text))[:200]


def draft_profile(r: dict, name: str, backend: str) -> tuple[str, list[str]]:
    """The draft profile text and the keys it sets. Every VALUE is either a constant or checked
    against the profile grammar (repo id, SHA, parser name, integer); anything the checkpoint said
    that fails the check is left empty and quoted — sanitised — in a comment instead."""
    best = lambda kind: next((s for s in r["suggestions"][kind] if s["parser"]), None)  # noqa: E731
    tool, reas = best("tool_call_parser"), best("reasoning_parser")
    f = r["fit"]
    # One Spark when it fits; also when the KV size is unknown but the weights alone take well under
    # half of one Spark's budget (a tiny model is not worth a ConnectX hop per token).
    one = f["tp1"]["verdict"]
    light = r["weights"]["weight_bytes"] < 0.5 * f["assumptions"]["budget_per_spark"]
    tp = 1 if one == "fits" or (one == "fits (KV size unknown)" and light) else 2
    tp_note = f"VERIFY: {_comment(f['verdict'])}" + (
        " — KV size unknown: TP chosen from the weights alone" if one == "fits (KV size unknown)" else "")
    native = r["context"].get("native")
    mml = f[f"tp{tp}"].get("suggested_max_model_len") or native or ""
    mml = str(mml) if isinstance(mml, int) and not isinstance(mml, bool) and mml > 0 else ""
    src = r["repo"] or r["local"] or ""
    if not (obprofile.REPO_RE.match(src) or (src.startswith("/") and not _UNSAFE.search(src)
                                              and " #" not in src)):
        src = ""
    rev = r["revision"] if isinstance(r["revision"], str) and SHA_RE.match(r["revision"] or "") else ""
    served = re.sub(r"[^A-Za-z0-9._-]+", "-", (src if obprofile.REPO_RE.match(src) else name).split("/")[-1])
    served = served.strip("-").lower() or name

    def parser_value(sugg, listed):
        v = (sugg or {}).get("parser") or ""
        return v if obprofile.PARSER_RE.match(v) and v in listed else ""

    names = _data("vllm")
    rows: list[tuple[str, str, str]] = [
        ("BACKEND", backend, ""),
        ("SOURCE", src, "" if src else "VERIFY: the checkpoint's own id was not a valid repo id or path"),
        ("REVISION", rev, "" if rev else "VERIFY: a local SOURCE may leave this empty"),
        ("SERVED_MODEL_NAME", served, "VERIFY: the id clients will send"),
        ("TENSOR_PARALLEL_SIZE", str(tp), tp_note),
        ("MAX_MODEL_LEN", mml, f"VERIFY: estimate from the fit model (native {_comment(native)})"),
    ]
    if backend == "vllm":
        tv = parser_value(tool, names.get("tool_parsers", []))
        rv = parser_value(reas, names.get("reasoning_parsers", []))
        rows += [
            ("DTYPE", "auto", ""),
            ("TOOL_CALL_PARSER", tv, f"VERIFY [{_comment((tool or {}).get('confidence', 'none'))}]: "
                                     f"{_comment((tool or {}).get('why', 'no suggestion'))[:120]}"),
            ("REASONING_PARSER", rv, f"VERIFY [{_comment((reas or {}).get('confidence', 'none'))}]: "
                                     f"{_comment((reas or {}).get('why', 'no suggestion'))[:120]}"),
            ("GPU_MEMORY_UTILIZATION", "0.80", "VERIFY ON HARDWARE: the OS shares the pool"),
            ("MAX_NUM_SEQS", "8", "VERIFY: = the rig's INFERENCE_SLOTS"),
            ("TRUST_REMOTE_CODE", "false", "VERIFY: the repo ships code (auto_map)" if r["auto_map"] else ""),
            ("EXTRA_ARGS", '["--enable-prefix-caching"]', ""),
        ]
    else:
        rows.append(("TENSORFOLD_PARALLEL", "auto", "VERIFY: auto = one request at a time on CUDA"))
    L = [f"# Draft profile written by model-inspect.sh from {_comment(r['source'])}.",
         "# Every line marked VERIFY is a suggestion, not a fact: check it, then delete the marker.",
         f"# Grammar and keys: models/TEMPLATE.env. Validate: scripts/backends/pylib/obprofile.py check {name}",
         ""]
    for key, value, note in rows:
        assert not _UNSAFE.search(value) and " #" not in value, key
        L.append(f"{key}={value}" + (f"          # {note}" if note else ""))
    if backend == "vllm":
        L.append('#SPECULATIVE_CONFIG={"method":"mtp","num_speculative_tokens":3}   # only if the model has MTP heads')
    else:
        tf = r["engines"]["tensorfold"]
        L.append(f"# TensorFold: {_comment(tf['status'])} — {_comment('; '.join(tf['notes']))[:300]}")
    return "\n".join(L) + "\n", [k for k, _, _ in rows]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="owner/name@<40-hex sha>, owner/name (resolves main, warns), or a local dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-profile", metavar="NAME")
    ap.add_argument("--backend", choices=("vllm", "tensorfold"))
    ap.add_argument("--force", action="store_true", help="overwrite an existing profile")
    ap.add_argument("--gpu-mem-util", type=float, default=0.80)
    ap.add_argument("--models-dir", type=Path, default=Path(os.environ.get("OPENBEAST_PROFILES_DIR")
                                                             or obprofile.MODELS))
    a = ap.parse_args(argv)
    try:
        src = open_source(a.source)
        r = inspect(src, a.gpu_mem_util)
    except hfapi.HubError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if a.write_profile:
        if not obprofile.NAME_RE.match(a.write_profile) or a.write_profile == "TEMPLATE":
            print(f"Error: profile name {a.write_profile!r}: letters, digits, . _ -", file=sys.stderr)
            return 2
        backend = a.backend or ("vllm" if r["engines"]["vllm"]["status"] == "supported"
                                else "tensorfold" if r["engines"]["tensorfold"]["status"] == "supported" else "vllm")
        out = a.models_dir / f"{a.write_profile}.env"
        if out.exists() and not a.force:
            print(f"Error: {out} exists (use --force to overwrite)", file=sys.stderr)
            return 1
        text, keys = draft_profile(r, a.write_profile, backend)
        # Belt and braces: the draft must parse to exactly the keys written, nothing smuggled in.
        try:
            parsed = obprofile.parse_text(text, f"{a.write_profile}.env")
        except obprofile.ProfileError as e:
            print(f"Error: the draft does not parse ({e}) — not written", file=sys.stderr)
            return 1
        if set(parsed) != set(keys):
            print(f"Error: the draft sets {sorted(set(parsed) ^ set(keys))} unexpectedly — not written",
                  file=sys.stderr)
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        r["profile_written"] = str(out)
    if a.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        print(render(r))
        if a.write_profile:
            print(f"\nDraft profile: {r['profile_written']} — resolve every '# VERIFY', then "
                  f"obprofile.py check {a.write_profile}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
