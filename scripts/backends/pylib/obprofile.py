#!/usr/bin/env python3
"""Per-model serving profiles: scripts/backends/models/<name>.env.

A profile is DATA. It is parsed here — never `source`d, never eval'd — with
the same grammar as spark.env / openbeast.conf (KEY=VALUE per line, one layer
of matching quotes stripped, `# comment` after an unquoted value ignored),
but stricter: an unknown key, a repeated key, or a line that is not
KEY=VALUE is an error, not a silent skip. A typo in a model profile must not
quietly serve a different model than the one you meant.

Every per-model fact the Spark launchers need lives in a profile, so nothing
in the launchers is wired to a particular model. See models/TEMPLATE.env.

CLI (used by the launchers and model-fetch.sh):
    obprofile.py check   <name|path> [--backend vllm|tensorfold]
    obprofile.py resolve <name|path> [--backend B]   # NUL-separated KEY\\0VALUE\\0 pairs
    obprofile.py show    <name|path>                 # human summary
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS = HERE.parent / "models"
DATA = HERE.parent / "data"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
PARSER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")

# key -> one-line meaning (also the allow list)
KEYS = {
    "BACKEND": "vllm | tensorfold",
    "SOURCE": "Hugging Face repo id (owner/name) or an absolute local directory",
    "REVISION": "full 40-hex commit SHA of SOURCE (required for a Hugging Face SOURCE)",
    "SERVED_MODEL_NAME": "the model id /v1/models lists and clients send",
    "TENSOR_PARALLEL_SIZE": "1 (one Spark) or 2 (both Sparks)",
    "MAX_MODEL_LEN": "context window in tokens (empty = the engine's default from config.json)",
    "DTYPE": "vLLM --dtype: auto | bfloat16 | float16 | float32",
    "QUANTIZATION": "vLLM --quantization (empty = from the checkpoint's quantization_config)",
    "TOOL_CALL_PARSER": "vLLM --tool-call-parser (empty = no tool calling)",
    "REASONING_PARSER": "vLLM --reasoning-parser (empty = reasoning stays in content)",
    "CHAT_TEMPLATE": "vLLM --chat-template: a file, relative to this profile or absolute",
    "TRUST_REMOTE_CODE": "true runs Python shipped in the model repo (default false)",
    "TRUST_REMOTE_CODE_ACK": "must equal REVISION for TRUST_REMOTE_CODE=true",
    "GPU_MEMORY_UTILIZATION": "vLLM fraction of the 128 GB unified pool (default 0.80)",
    "MAX_NUM_SEQS": "vLLM concurrent sequences (= the rig's INFERENCE_SLOTS)",
    "SPECULATIVE_CONFIG": "vLLM --speculative-config JSON object",
    "EXTRA_ARGS": "JSON array of extra engine arguments",
    "TENSORFOLD_PARALLEL": "TensorFold --parallel: auto | N",
    "DRAFTER_SOURCE": "TensorFold draft model repo id (fetched and pinned like SOURCE)",
    "DRAFTER_REVISION": "full 40-hex commit SHA of DRAFTER_SOURCE",
    "FETCH_INCLUDE": "JSON array of globs model-fetch downloads (default: everything)",
    "FETCH_EXCLUDE": "JSON array of globs model-fetch skips (added to the pickle/GGUF defaults)",
}
BOOL_TRUE = {"true", "yes", "1", "on"}
BOOL_FALSE = {"false", "no", "0", "off", ""}
# vLLM flags a profile may not smuggle in through EXTRA_ARGS: each one is
# either a secret on argv or bypasses a check made on a named key.
FORBIDDEN_EXTRA = {
    "--trust-remote-code": "use TRUST_REMOTE_CODE (+ the ACK) so the decision is explicit and pinned",
    "--api-key": "a key on argv is visible in ps and docker inspect; the launcher passes VLLM_API_KEY by environment",
    "--revision": "use REVISION", "--tokenizer-revision": "use REVISION", "--code-revision": "use REVISION",
    "--served-model-name": "use SERVED_MODEL_NAME", "--tensor-parallel-size": "use TENSOR_PARALLEL_SIZE",
    "--host": "the host settings live in spark.env", "--port": "the host settings live in spark.env",
    "--tool-call-parser": "use TOOL_CALL_PARSER", "--reasoning-parser": "use REASONING_PARSER",
    "--chat-template": "use CHAT_TEMPLATE", "--max-model-len": "use MAX_MODEL_LEN",
    "--tp": "use TENSOR_PARALLEL_SIZE", "--name": "use SERVED_MODEL_NAME", "--context": "use MAX_MODEL_LEN",
    "--drafter": "use DRAFTER_SOURCE/DRAFTER_REVISION", "--parallel": "use TENSORFOLD_PARALLEL",
}
VLLM_ONLY = ("DTYPE", "QUANTIZATION", "TOOL_CALL_PARSER", "REASONING_PARSER", "CHAT_TEMPLATE",
             "TRUST_REMOTE_CODE", "GPU_MEMORY_UTILIZATION", "MAX_NUM_SEQS", "SPECULATIVE_CONFIG")
TF_ONLY = ("TENSORFOLD_PARALLEL", "DRAFTER_SOURCE", "DRAFTER_REVISION")
# Files model-fetch never downloads unless FETCH_INCLUDE names them: pickled
# weights execute code on load, and GGUF/ONNX/"original" copies are dead weight
# for a safetensors engine.
DEFAULT_EXCLUDE = ["*.bin", "*.pt", "*.pth", "*.ckpt", "*.pkl", "*.pickle", "*.gguf", "*.onnx",
                   "*.onnx_data", "original/*", "*.msgpack", "*.h5"]


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    name: str
    path: Path
    values: dict[str, str]
    extra_args: list[str] = field(default_factory=list)
    fetch_include: list[str] = field(default_factory=list)
    fetch_exclude: list[str] = field(default_factory=list)
    speculative: dict | None = None
    warnings: list[str] = field(default_factory=list)

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, "") or default

    @property
    def backend(self) -> str:
        return self.get("BACKEND")

    @property
    def source(self) -> str:
        return self.get("SOURCE")

    @property
    def is_hf(self) -> bool:
        return bool(REPO_RE.match(self.source)) and not self.source.startswith("/")

    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(".lock")

    @property
    def trust_remote_code(self) -> bool:
        return self.get("TRUST_REMOTE_CODE").lower() in BOOL_TRUE

    @property
    def chat_template_path(self) -> Path | None:
        ct = self.get("CHAT_TEMPLATE")
        if not ct:
            return None
        p = Path(os.path.expanduser(ct))
        return p if p.is_absolute() else (self.path.parent / p).resolve()


def _unquote(raw: str) -> str:
    """spark.env / conf grammar: one layer of matching quotes, else strip a trailing `# comment`."""
    v = raw.strip()
    if v.startswith("#"):
        return ""                               # KEY=   # comment  → empty
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v and v[0] in "\"'":
        # an opening quote with trailing comment after the closing one: "x"  # note
        q = v[0]
        end = v.find(q, 1)
        if end > 0 and v[end + 1:].strip().startswith("#"):
            return v[1:end]
        raise ProfileError(f"unbalanced quote in value {raw.strip()!r}")
    v = re.sub(r"\s+#.*$", "", v)
    return v.strip()


def parse_text(text: str, where: str = "profile") -> dict[str, str]:
    out: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            raise ProfileError(f"{where}:{n}: 'export' is shell, and a profile is data — write KEY=VALUE")
        m = LINE_RE.match(s)
        if not m:
            raise ProfileError(f"{where}:{n}: not KEY=VALUE: {s[:60]!r}")
        key, raw = m.group(1), m.group(2)
        if key not in KEYS:
            raise ProfileError(f"{where}:{n}: unknown key {key} (known: {', '.join(sorted(KEYS))})")
        if key in out:
            raise ProfileError(f"{where}:{n}: {key} is set twice — which one did you mean?")
        value = _unquote(raw)
        if any(ord(c) < 32 for c in value):
            raise ProfileError(f"{where}:{n}: {key} contains a control character")
        out[key] = value
    return out


def locate(spec: str, models_dir: Path = MODELS) -> Path:
    if "/" in spec or spec.endswith(".env"):
        p = Path(os.path.expanduser(spec))
        if not p.is_file():
            raise ProfileError(f"profile file {spec} does not exist")
        return p.resolve()
    if not NAME_RE.match(spec) or spec == "TEMPLATE":
        raise ProfileError(f"profile name {spec!r}: letters, digits, . _ - (and not TEMPLATE); or pass a path")
    p = models_dir / f"{spec}.env"
    if not p.is_file():
        have = sorted(x.stem for x in models_dir.glob("*.env") if x.stem != "TEMPLATE") if models_dir.is_dir() else []
        raise ProfileError(f"no profile {p} (have: {', '.join(have) or 'none'}; start from {models_dir}/TEMPLATE.env "
                           f"or `model-inspect.sh <repo@sha> --write-profile {spec}`)")
    return p.resolve()


def _json_list(p: Profile, key: str) -> list[str]:
    raw = p.get(key)
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except ValueError as e:
        raise ProfileError(f"{key} must be a JSON array of strings, e.g. [\"--enable-prefix-caching\"] ({e})") from None
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ProfileError(f"{key} must be a JSON array of strings")
    if any(any(ord(c) < 32 for c in x) for x in v):
        raise ProfileError(f"{key} contains a control character")
    return v


def _vllm_names() -> dict:
    try:
        return json.loads((DATA / "vllm.json").read_text())
    except (OSError, ValueError):
        return {}


def validate(p: Profile, backend: str | None = None) -> Profile:
    errs: list[str] = []

    def err(msg: str) -> None:
        errs.append(msg)

    b = p.backend
    if b not in ("vllm", "tensorfold"):
        err(f"BACKEND={b!r} — vllm or tensorfold")
    elif backend and b != backend:
        err(f"this is a {b} profile; the {backend} launcher cannot serve it")
    src = p.source
    if not src:
        err("SOURCE is not set (a Hugging Face repo id or an absolute local directory)")
    elif src.startswith("/"):
        pass
    elif not REPO_RE.match(src):
        err(f"SOURCE={src!r} is neither owner/name nor an absolute path (a relative path is ambiguous)")
    rev = p.get("REVISION")
    if p.is_hf and not rev:
        err("REVISION is not set — pin the commit (a branch or tag can be re-pointed under you): "
            "model-inspect.sh prints it, or GET https://huggingface.co/api/models/<repo>/revision/main → .sha")
    if rev and not SHA_RE.match(rev):
        err(f"REVISION={rev!r} is not a full 40-hex commit SHA — branches and tags ('main', 'v1.0') can move; "
            "use the commit, lowercase")
    name = p.get("SERVED_MODEL_NAME")
    if not name:
        err("SERVED_MODEL_NAME is not set (the id /v1/models will list, e.g. 'org-model-nvfp4')")
    elif len(name) > 200:
        err("SERVED_MODEL_NAME is longer than 200 characters")
    tp = p.get("TENSOR_PARALLEL_SIZE", "2")
    if tp not in ("1", "2"):
        err(f"TENSOR_PARALLEL_SIZE={tp!r} — 1 (one Spark) or 2 (both)")
    for key in ("MAX_MODEL_LEN", "MAX_NUM_SEQS"):
        if p.get(key) and not re.match(r"^[1-9][0-9]*$", p.get(key)):
            err(f"{key}={p.get(key)!r} is not a positive integer")
    gmu = p.get("GPU_MEMORY_UTILIZATION")
    if gmu:
        try:
            f = float(gmu)
            if not 0.05 <= f <= 0.95:
                raise ValueError
        except ValueError:
            err(f"GPU_MEMORY_UTILIZATION={gmu!r} — a fraction between 0.05 and 0.95 of the unified pool "
                "(the OS and page cache live in the same 128 GB)")
    dtype = p.get("DTYPE")
    if dtype and dtype not in ("auto", "bfloat16", "float16", "float32", "half", "float"):
        err(f"DTYPE={dtype!r} — auto | bfloat16 | float16 | float32")
    names = _vllm_names()
    commit = (names.get("_provenance") or {}).get("commit", "?")[:12]
    for key, listed in (("TOOL_CALL_PARSER", "tool_parsers"), ("REASONING_PARSER", "reasoning_parsers"),
                        ("QUANTIZATION", "quantization_methods")):
        val = p.get(key)
        if not val:
            continue
        if not PARSER_RE.match(val):
            err(f"{key}={val!r} has characters no vLLM name has")
        elif names and val not in names.get(listed, []):
            p.warnings.append(f"{key}={val} is not registered in vLLM @{commit} (data/vllm.json) — fine only if "
                              "your image is newer; the server will refuse to start otherwise")
    if p.trust_remote_code:
        ack = p.get("TRUST_REMOTE_CODE_ACK")
        if not rev:
            err("TRUST_REMOTE_CODE=true needs a REVISION: the acknowledgement is bound to the exact code you read")
        elif ack != rev:
            err("TRUST_REMOTE_CODE=true executes Python shipped in the model repo, with the container's access to "
                "the GPU, the model files and the network. Read the repo's *.py at REVISION, then set "
                f"TRUST_REMOTE_CODE_ACK={rev} (the same SHA, so a revision bump voids it)")
    elif p.get("TRUST_REMOTE_CODE").lower() not in BOOL_FALSE:
        err(f"TRUST_REMOTE_CODE={p.get('TRUST_REMOTE_CODE')!r} — true or false")
    ct = p.chat_template_path
    if ct is not None and not ct.is_file():
        err(f"CHAT_TEMPLATE {ct} does not exist")
    if p.get("SPECULATIVE_CONFIG"):
        try:
            p.speculative = json.loads(p.get("SPECULATIVE_CONFIG"))
            if not isinstance(p.speculative, dict):
                raise ValueError("not an object")
        except ValueError as e:
            err(f"SPECULATIVE_CONFIG must be a JSON object ({e})")
    par = p.get("TENSORFOLD_PARALLEL")
    if par and not re.match(r"^(auto|[1-9][0-9]*)$", par):
        err(f"TENSORFOLD_PARALLEL={par!r} — auto or a positive integer")
    dsrc, drev = p.get("DRAFTER_SOURCE"), p.get("DRAFTER_REVISION")
    if dsrc and not REPO_RE.match(dsrc):
        err(f"DRAFTER_SOURCE={dsrc!r} is not owner/name")
    if dsrc and not SHA_RE.match(drev):
        err("DRAFTER_REVISION must be the draft model's full 40-hex commit SHA")
    if drev and not dsrc:
        err("DRAFTER_REVISION without DRAFTER_SOURCE")
    if b == "tensorfold":
        for key in VLLM_ONLY:
            if p.get(key) and not (key == "TRUST_REMOTE_CODE" and not p.trust_remote_code):
                err(f"{key} is a vLLM setting; TensorFold has no equivalent (it parses tools and reasoning itself "
                    "and has no chat-template override) — remove it from a tensorfold profile")
    elif b == "vllm":
        for key in TF_ONLY:
            if p.get(key):
                err(f"{key} is a TensorFold setting — remove it from a vllm profile")
    try:
        p.extra_args = _json_list(p, "EXTRA_ARGS")
        p.fetch_include = _json_list(p, "FETCH_INCLUDE")
        p.fetch_exclude = _json_list(p, "FETCH_EXCLUDE")
    except ProfileError as e:
        err(str(e))
    for a in p.extra_args:
        flag = a.split("=", 1)[0]
        if flag in FORBIDDEN_EXTRA:
            err(f"EXTRA_ARGS may not contain {flag}: {FORBIDDEN_EXTRA[flag]}")
    if errs:
        raise ProfileError("\n".join(f"{p.path.name}: {e}" for e in errs))
    return p


def load(spec: str, backend: str | None = None, models_dir: Path = MODELS) -> Profile:
    path = locate(spec, models_dir)
    values = parse_text(path.read_text(), str(path.name))
    name = path.stem
    if not NAME_RE.match(name):
        raise ProfileError(f"profile file name {path.name}: the stem must be letters, digits, . _ -")
    return validate(Profile(name=name, path=path, values=values), backend)


def resolved_pairs(p: Profile) -> list[tuple[str, str]]:
    out = [("PROFILE_NAME", p.name), ("PROFILE_PATH", str(p.path)), ("PROFILE_LOCK", str(p.lock_path)),
           ("PROFILE_IS_HF", "1" if p.is_hf else "0")]
    for key in KEYS:
        if key in ("EXTRA_ARGS", "FETCH_INCLUDE", "FETCH_EXCLUDE", "CHAT_TEMPLATE", "TENSOR_PARALLEL_SIZE",
                   "TRUST_REMOTE_CODE"):          # emitted normalised below
            continue
        out.append((key, p.get(key)))
    out.append(("TENSOR_PARALLEL_SIZE", p.get("TENSOR_PARALLEL_SIZE", "2")))
    out.append(("TRUST_REMOTE_CODE", "true" if p.trust_remote_code else "false"))
    ct = p.chat_template_path
    out.append(("CHAT_TEMPLATE", str(ct) if ct else ""))
    for a in p.extra_args:
        out.append(("EXTRA_ARG", a))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Validate / resolve an OpenBeast model profile")
    ap.add_argument("cmd", choices=("check", "resolve", "show"))
    ap.add_argument("profile")
    ap.add_argument("--backend", choices=("vllm", "tensorfold"))
    ap.add_argument("--models-dir", type=Path, default=Path(os.environ.get("OPENBEAST_PROFILES_DIR") or MODELS))
    a = ap.parse_args(argv)
    try:
        p = load(a.profile, a.backend, a.models_dir)
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    for w in p.warnings:
        print(f"Warning: {w}", file=sys.stderr)
    if a.cmd == "resolve":
        buf = sys.stdout.buffer
        for k, v in resolved_pairs(p):
            buf.write(k.encode() + b"\0" + v.encode() + b"\0")
        buf.flush()
    elif a.cmd == "show":
        for k, v in resolved_pairs(p):
            print(f"{k:24} {v}")
    else:
        print(f"OK {p.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
