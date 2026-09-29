#!/usr/bin/env python3
"""Regenerate scripts/backends/data/{vllm,tensorfold}.json from engine SOURCE.

model-inspect.sh answers "can this engine serve that checkpoint, and which
parsers does it want?" offline, from these two files. They are vendored, not
fetched at inspect time, so the answer is reproducible and names the exact
engine commit it is true for. Nothing here imports vLLM or TensorFold: each
source file is parsed with `ast` and only literal names are read.

    python3 scripts/backends/pylib/vendor_lists.py vllm --commit <40-hex>
    python3 scripts/backends/pylib/vendor_lists.py tensorfold --commit <40-hex> \
        [--src /path/to/a/TensorFold/checkout/at/that/commit]

vLLM files are read from raw.githubusercontent.com at the commit. TensorFold
is read from --src when given (it must BE that commit; the caller checks),
otherwise from GitHub at the commit. A branch or tag is refused: the point of
the file is to say which code the lists came from.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

VLLM_REPO = "vllm-project/vllm"
VLLM_FILES = {
    "tool_parsers": "vllm/tool_parsers/__init__.py",
    "reasoning_parsers": "vllm/reasoning/__init__.py",
    "registry": "vllm/model_executor/models/registry.py",
    "quantization": "vllm/model_executor/layers/quantization/__init__.py",
    "engine_args": "vllm/engine/arg_utils.py",
    "frontend_args": "vllm/entrypoints/launchers/cli_args.py",
}

# EXTRA_ARGS policy (obprofile.py). vLLM's FlexibleArgumentParser maps "_"
# to "-" and argparse accepts any unambiguous prefix, so a deny list of
# spellings can always be walked around. Instead: an ALLOW list of flags that
# only tune performance, scheduling or logging (nothing that names a file, a
# repo, a URL, a class to import, a secret, or a setting the launcher or a
# profile key owns), checked against the flags that exist at the vendored
# commit. Anything else needs EXTRA_ARGS_ACK=<REVISION>. The NEVER list is
# refused even then: secrets, pins, launcher-owned topology, code-import hooks.
VLLM_EXTRA_ALLOW = [
    "enable-prefix-caching", "prefix-caching-hash-algo", "kv-cache-dtype", "max-num-batched-tokens",
    "long-prefill-token-threshold", "enable-chunked-prefill", "disable-chunked-mm-input", "async-scheduling",
    "block-size", "cpu-offload-gb", "kv-cache-memory-bytes", "enforce-eager", "max-logprobs",
    "disable-log-stats", "enable-log-requests", "max-log-len", "uvicorn-log-level", "disable-uvicorn-access-log",
    "seed", "limit-mm-per-prompt", "mm-processor-cache-gb", "enable-expert-parallel", "cudagraph-capture-sizes",
    "max-cudagraph-capture-size", "disable-cascade-attn", "disable-sliding-window", "scheduling-policy",
    "default-chat-template-kwargs", "enable-force-include-usage", "enable-prompt-tokens-details",
    "exclude-tools-when-tool-choice-none", "load-format", "attention-backend",
]
VLLM_EXTRA_NEVER = [
    "api-key", "hf-token", "trust-remote-code", "revision", "code-revision", "tokenizer-revision", "tokenizer",
    "model", "model-weights", "served-model-name", "host", "port", "uds", "tensor-parallel-size",
    "pipeline-parallel-size", "nnodes", "node-rank", "master-addr", "master-port", "headless", "chat-template",
    "tool-call-parser", "reasoning-parser", "enable-auto-tool-choice", "max-model-len", "config", "middleware",
    "tool-parser-plugin", "reasoning-parser-plugin", "worker-cls", "scheduler-cls", "io-processor-plugin",
    "allowed-local-media-path", "allowed-media-domains", "download-dir", "hf-config-path", "hf-overrides",
    "model-loader-extra-config", "speculative-config", "gpu-memory-utilization", "max-num-seqs", "dtype",
    "quantization", "trust-request-chat-template", "lora-modules", "ssl-keyfile", "ssl-certfile",
    "ssl-ca-certs", "allowed-origins", "allow-credentials", "root-path", "data-parallel-address",
]
TF_EXTRA_ALLOW = [
    "max-tokens", "temperature", "top-p", "top-k", "thinking", "no-thinking", "reasoning-effort",
    "thinking-budget", "no-drafts", "drafter-bits", "mtp-drafts", "mtp-confidence", "lane-kernels",
    "prompt-cache-gib", "checkpoint-slots", "spill-gib", "max-snapshots", "decode-share", "mlx-cache-gib",
    "kv-dtype",
]
TF_EXTRA_NEVER = [
    "tp", "rank", "master", "master-port", "name", "host", "port", "drafter", "parallel", "context",
    "backend", "vision-urls", "snapshot-dir", "no-update-check", "ssd-experts", "ple-on-ssd",
]
TF_REPO = "ashhart/TensorFold"


def _raw(repo: str, commit: str, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - fixed https host
        return r.read().decode()


def _dict_keys(src: str, name: str) -> list[str]:
    """Keys of a module-level `name = {...}` dict literal (string keys only)."""
    for node in ast.parse(src).body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == name and isinstance(value, ast.Dict):
            return [k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    raise SystemExit(f"{name} not found as a dict literal — the upstream layout changed; update vendor_lists.py")


def _literal_strings(src: str, name: str) -> list[str]:
    """String arguments of a module-level `name = Literal[...]`."""
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            sub = node.value
            if isinstance(sub, ast.Subscript):
                elts = sub.slice.elts if isinstance(sub.slice, ast.Tuple) else [sub.slice]
                return [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    raise SystemExit(f"{name} not found as a Literal[...] — the upstream layout changed; update vendor_lists.py")


def _flag_strings(src: str) -> set[str]:
    """Every "--long-flag" string literal plus every class field (as --field-name)."""
    flags = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and re.match(r"^--[a-z0-9][a-z0-9-]*$", node.value):
            flags.add(node.value[2:])
        elif isinstance(node, ast.ClassDef):
            for x in node.body:
                if isinstance(x, ast.AnnAssign) and isinstance(x.target, ast.Name) \
                        and not x.target.id.startswith("_"):
                    flags.add(x.target.id.replace("_", "-"))
    return flags


def _policy(engine: str, known: set[str], allow: list[str], never: list[str]) -> dict:
    missing = [f for f in allow if f not in known and not (f.startswith("no-") and f[3:] in known)]
    if missing:
        print(f"{engine}: allow-listed flags absent at this commit, dropped: {missing}", file=sys.stderr)
    return {"serve_flags": sorted(known),
            "extra_args_allow": sorted(f for f in allow if f not in missing),
            "extra_args_never": sorted(never)}


def vendor_vllm(commit: str) -> dict:
    src = {k: _raw(VLLM_REPO, commit, p) for k, p in VLLM_FILES.items()}
    reg = src["registry"]
    generation = sorted(set(_dict_keys(reg, "_TEXT_GENERATION_MODELS")
                            + _dict_keys(reg, "_MULTIMODAL_MODELS")
                            + _dict_keys(reg, "_TRANSFORMERS_SUPPORTED_MODELS")))
    other = sorted(set(_dict_keys(reg, "_EMBEDDING_MODELS") + _dict_keys(reg, "_LATE_INTERACTION_MODELS")
                       + _dict_keys(reg, "_REWARD_MODELS") + _dict_keys(reg, "_TOKEN_CLASSIFICATION_MODELS")
                       + _dict_keys(reg, "_SEQUENCE_CLASSIFICATION_MODELS")))
    return {
        "_provenance": {
            "engine": "vLLM",
            "repo": f"https://github.com/{VLLM_REPO}",
            "commit": commit,
            "files": VLLM_FILES,
            "how": "scripts/backends/pylib/vendor_lists.py vllm (ast: dict keys, Literal args, flag literals and "
                   "config-class fields; nothing imported). extra_args_allow/never are OpenBeast policy "
                   "(vendor_lists.py), checked against serve_flags",
            "note": "A release image (NGC vllm:26.05, vllm-openai:*) may predate or postdate this commit. "
                    "Treat 'unknown' as 'unknown to THIS commit', not 'impossible'.",
        },
        "tool_parsers": sorted(_dict_keys(src["tool_parsers"], "_TOOL_PARSERS_TO_REGISTER")),
        "reasoning_parsers": sorted(_dict_keys(src["reasoning_parsers"], "_REASONING_PARSERS_TO_REGISTER")),
        "generation_architectures": generation,
        "pooling_architectures": other,
        "speculative_architectures": sorted(_dict_keys(reg, "_SPECULATIVE_DECODING_MODELS")),
        "previously_supported": {k: v for k, v in _prev(reg).items()},
        "quantization_methods": sorted(_literal_strings(src["quantization"], "QuantizationMethods")),
        **_policy("vllm", _flag_strings(src["engine_args"]) | _flag_strings(src["frontend_args"]),
                  VLLM_EXTRA_ALLOW, VLLM_EXTRA_NEVER),
    }


def _prev(reg: str) -> dict[str, str]:
    for node in ast.parse(reg).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_PREVIOUSLY_SUPPORTED_MODELS"
                                                 for t in node.targets):
            return {k.value: v.value for k, v in zip(node.value.keys, node.value.values)
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
    return {}


# --------------------------------------------------------------------------- TensorFold

# Facts that live in engine code paths (raise statements), not in module
# constants: read by hand at the pinned commit, cited by file:line. Refresh by
# re-reading those lines when the commit moves.
TF_CURATED = {
    "qwen3_5": {"tp": [1, 2], "tp2_quant_methods": ["mlx"], "notes": [
        "NVFP4 and EXL3 checkpoints run on ONE GPU; two ranks need the MLX affine checkpoint "
        "(families/qwen3_5/cuda/engine.py:37)"]},
    "qwen3_5_moe": {"tp": [1], "notes": [
        "one GPU only, one request at a time (families/qwen3_5_moe/__init__.py:36-38)"]},
    "qwen4_exp": {"tp": [1, 2], "tp2_quant_methods": ["mlx"], "notes": [
        "NVFP4 and EXL3 run on one GPU (families/qwen4_exp/cuda/engine.py:33)",
        "--parallel >1 is rejected with --tp 2 (families/qwen4_exp/cuda/engine.py:47)"]},
    "glm5_next": {"tp": [2], "notes": [
        "requires --tp 2: one GPU per machine (families/glm5_next/__init__.py:179-181)"]},
    "nemotron_h": {"tp": [1, 2], "notes": [
        "one or two ranks (families/nemotron_h/cuda/app.py:33-34); serial requests"]},
}


def _module_consts(tree: ast.Module) -> dict:
    out: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value
    return out


def _eval(node: ast.AST, consts: dict, imported: dict):
    """literal_eval that also resolves module-level names and tuple slices of them."""
    if isinstance(node, ast.Name):
        if node.id in consts:
            return _eval(consts[node.id], consts, imported)
        if node.id in imported:
            return imported[node.id]
        raise ValueError(node.id)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_eval(e, consts, imported) for e in node.elts)
    if isinstance(node, ast.Dict):
        return {_eval(k, consts, imported): _eval(v, consts, imported) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, consts, imported)
        if isinstance(node.slice, ast.Slice):
            lo = _eval(node.slice.lower, consts, imported) if node.slice.lower else None
            hi = _eval(node.slice.upper, consts, imported) if node.slice.upper else None
            return base[lo:hi]
        return base[_eval(node.slice, consts, imported)]
    return ast.literal_eval(node)


def vendor_tensorfold(commit: str, src_dir: Path | None) -> dict:
    def read(rel: str) -> str:
        if src_dir is not None:
            return (src_dir / rel).read_text()
        return _raw(TF_REPO, commit, rel)

    fam_root = "src/tensorfold/families"
    if src_dir is not None:
        names = sorted(p.name for p in (src_dir / fam_root).iterdir()
                       if p.is_dir() and (p / "__init__.py").is_file())
    else:
        api = f"https://api.github.com/repos/{TF_REPO}/contents/{fam_root}?ref={commit}"
        with urllib.request.urlopen(api, timeout=60) as r:  # noqa: S310
            names = sorted(e["name"] for e in json.load(r) if e["type"] == "dir")
    families = {}
    for name in names:
        rel = f"{fam_root}/{name}/__init__.py"
        text = read(rel)
        tree = ast.parse(text)
        consts = _module_consts(tree)
        imported: dict = {}
        defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tensorfold.families."):
                sub = node.module.replace(".", "/")
                try:
                    subtree = ast.parse(read(f"src/{sub}.py"))
                except (OSError, FileNotFoundError, urllib.error.URLError):
                    continue
                subconsts = _module_consts(subtree)
                for alias in node.names:
                    if alias.name in subconsts:
                        try:
                            imported[alias.asname or alias.name] = _eval(subconsts[alias.name], subconsts, {})
                        except ValueError:
                            pass
                    subdefs = {n.name for n in subtree.body if isinstance(n, ast.FunctionDef)}
                    if alias.name in subdefs:
                        defs.add(alias.asname or alias.name)

        def get(key, default=None):
            if key not in consts:
                return default
            try:
                return _eval(consts[key], consts, imported)
            except ValueError:
                return default

        model_types = list(get("MODEL_TYPES", ()) or ())
        if not model_types:
            continue
        qm = get("QUANT_METHODS", {}) or {}
        cuda = "cuda_engine" in defs
        entry = {
            "title": get("TITLE", name),
            "model_types": model_types,
            "backends": [b for b, ok in (("mlx", "load" in defs), ("cuda", cuda)) if ok],
            # families/__init__.py readable_quants: QUANT_METHODS[cuda] or ("mlx",)
            "cuda_quant_methods": list(qm.get("cuda", ("mlx",))) if cuda else [],
            "cuda_mlx_quantization": list(get("CUDA_QUANTIZATION")) if get("CUDA_QUANTIZATION") else None,
            "cuda_affine_bits": list(get("CUDA_AFFINE_BITS") or ()) or None,
            "cuda_affine_groups": list(get("CUDA_AFFINE_GROUPS") or ()) or None,
            "family_checks_mlx_widths": "check_quantization" in defs,
            "tested_checkpoints": list(get("MODELS", ()) or ()),
            "drafter": get("DRAFTER"),
            "source": rel,
        }
        entry.update(TF_CURATED.get(name, {}))
        families[name] = entry
    cli = read("src/tensorfold/cli.py")
    tf_flags = {n.value[2:] for n in ast.walk(ast.parse(cli))
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.match(r"^--[a-z0-9-]+$", n.value)}
    tf_flags.add("no-thinking")                     # --thinking is a BooleanOptionalAction
    return {
        **_policy("tensorfold", tf_flags, TF_EXTRA_ALLOW, TF_EXTRA_NEVER),
        "_provenance": {
            "engine": "TensorFold",
            "repo": f"https://github.com/{TF_REPO}",
            "commit": commit,
            "how": "scripts/backends/pylib/vendor_lists.py tensorfold (ast over src/tensorfold/families/*/__init__.py)",
            "gate": ("TensorFold serves a checkpoint only if config.json's model_type (or text_config.model_type) "
                     "is claimed by a family package's MODEL_TYPES (families.detect), the family has a cuda_engine "
                     "(cli._backend), and the checkpoint's quant method is in the family's QUANT_METHODS['cuda'] "
                     "(default ('mlx',)) — MLX affine weights must also match CUDA_QUANTIZATION (bits, group) or the "
                     "family's own check_quantization; modelopt/compressed-tensors must be NVFP4/FP8 "
                     "(cuda/nvfp4/format.py require_config). Checkpoints outside MODELS run with an 'untested' note. "
                     "Source: src/tensorfold/families/__init__.py, src/tensorfold/cli.py:406-445."),
            "curated": "tp, tp2_quant_methods and notes are hand-read from the cited lines, not parsed; "
                       "extra_args_allow/never are OpenBeast policy (vendor_lists.py), checked against "
                       "serve_flags parsed from src/tensorfold/cli.py",
        },
        "families": families,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("engine", choices=("vllm", "tensorfold"))
    ap.add_argument("--commit", required=True, help="full 40-hex commit SHA of the engine source")
    ap.add_argument("--src", type=Path, help="tensorfold: a local checkout AT --commit")
    ap.add_argument("--out", type=Path, help="default scripts/backends/data/<engine>.json")
    a = ap.parse_args(argv)
    if not SHA_RE.match(a.commit):
        print(f"--commit must be a full 40-hex commit SHA, not {a.commit!r} (a branch or tag moves)", file=sys.stderr)
        return 2
    data = vendor_vllm(a.commit) if a.engine == "vllm" else vendor_tensorfold(a.commit, a.src)
    out = a.out or DATA / f"{a.engine}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
