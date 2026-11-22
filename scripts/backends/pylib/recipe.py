#!/usr/bin/env python3
"""beast-recipe: OpenBeast's own serving recipes for DGX Sparks (1 to 8, one rank each).

    beast-recipe init  <hf-url | org/repo[@sha] | local-dir> [--sparks N] [--name NAME] [--engine ID] [--force] [--accept-nc]
    beast-recipe plan  <recipe> [--sparks 1,2,3,4] [--write]
    beast-recipe engine <recipe> [--run [--stage] [--while-serving]]   the engine on every Spark: check, or stage it
    beast-recipe stage  <recipe> [--run] [--only weights,verify,mirror,drafter,quantize,aux,rank-inputs,profile]
                                 [--accept-nc] [--force] [--while-serving]
    beast-recipe up     <recipe> [--run] [--write-profile] [--force] [--key-file F] [--wait S]
    beast-recipe down   <recipe> [--run]
    beast-recipe check  <recipe> [--run] [--bench] [--url URL] [--key-file F]
    beast-recipe adopt  <recipe> [--run] [--write-family]
    beast-recipe rollback [--run]
    beast-recipe list | show <recipe> | show --keys
    every command: --json, --help; every acting command prints what it would do unless --run
    (on the head, `beast-spark install` links this as ~/.local/bin/beast-recipe)

A RECIPE is a directory, recipes/<name>/, holding recipe.env (KEY=VALUE, PARSED, never sourced;
every value carries its provenance in a trailing comment), SHA256SUMS (the weights' Hub LFS hashes,
vendored so verification needs no network) and, after `plan --write`, plan.json. A recipe is DATA
plus vendored files; the pipeline is shared (docs/BEAST_RECIPE.md).

What it knows comes from three places, and it says which: the checkpoint's own files (READ, through
model_inspect.py: config.json, the quantization config, safetensors headers, the template), the
engine and family matrix scripts/backends/data/recipe-engines.json (DATA, every entry sourced and
dated), and our arithmetic (ESTIMATED). Nothing here starts, stops or downloads anything unless
--run is given, and nothing ever prints a key or token.

Stdlib only, python 3.9+: it runs on the Sparks' system python3, the rig and the Mac, offline.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKENDS = HERE.parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

import hfapi
import obprofile

DATA_FILE = Path(os.environ.get("BEAST_RECIPE_DATA") or BACKENDS / "data" / "recipe-engines.json")
FAMILIES_FILE = BACKENDS / "data" / "spark-families.json"
VLLM_FILE = BACKENDS / "data" / "vllm.json"
GIB = 1024 ** 3
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
UNSAFE = re.compile(r"[\x00-\x1f\x7f]")
VERDICTS = ("SERVABLE-NOW", "SERVABLE-WITH-WORK", "NEEDS-MORE-HARDWARE", "BLOCKED")
VERDICT_MEANING = {
    "SERVABLE-NOW": "an engine we already have serves it on this many Sparks and it fits: what is left is steps "
                    "(fetch, profile, start, check), no engineering",
    "SERVABLE-WITH-WORK": "it fits on this many Sparks, but an engine must be staged, built or extended first "
                          "(the gap list says what, and how long)",
    "NEEDS-MORE-HARDWARE": "no engine path fits on this many Sparks; one does on more (the plan says how many and why)",
    "BLOCKED": "no engine in the matrix reads it at any size we can build, or it does not fit even on 8 Sparks",
}
# Gaps that are always part of bringing a model up but never change the verdict: they are steps and
# measurements, not engineering. Everything else in a path's gap list is work.
NONBLOCKING_GAPS = {"glue", "d2-first-serve", "tuning", "drafter", "perf-patches", "parsers", "licence-review"}
PROV_TAGS = ("READ", "HUB", "DATA", "MEASURED", "VERIFIED", "CLAIMED", "ESTIMATED", "DEFAULT", "VERIFY", "USER")
# Never written into, moved or deleted by anything here (Max, 2026-11-19: the Hy4 EXL3 pack is kept;
# the same rule as hy4_protected in scripts/backends/hy4-vllm/config.sh). Reading it is fine.
PROTECTED_NAMES = ("LibertAIDAI-Hy4-flash-EXL3-4bpw",)
KNOWN_UNITS = {"ds": "deepseek-v41-inference.service", "glm": "spark-hydra-inference.service",
               "hy4": "hy4-vllm-inference.service"}
# What each Mia recipe unit actually serves: a recipe may hand over to one only when its plan is that
# (the 2.9 bpw -v2 pack at TP2 for DeepSeek; GLM's unit serves its own pack at TP2).
MIA_UNIT_SPEC = {
    "deepseek-v41-inference.service": {"tp": 2, "weights": "~/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw-v2"},
    "spark-hydra-inference.service": {"tp": 2, "weights": None},
}
DEFAULT_KEY_FILE = "~/.config/openbeast/tensorfold-api-key"
# beast-spark's own verbs: a recipe with one of these names could never be switched to by name
RESERVED_NAMES = {"up", "down", "glm", "ds", "deepseek", "hy4", "status", "aliases", "install", "recipe", "help"}
# a cluster.env node: an ssh target (user@host, alias, IP); never an option or shell
NODE_RE = re.compile(r"^[A-Za-z0-9_@][A-Za-z0-9._@-]*$")
STAGE_STEP_NAMES = ("profile", "weights", "verify", "mirror", "drafter", "quantize", "aux", "rank-inputs")
SERVES_VALUES = (True, "generator-only", "reference", "source-build")
ENGINE_STATUSES = ("installed", "available", "unreleased", "not-installed")
# the vLLM quantization method each checkpoint FORMAT needs (data/vllm.json quantization_methods)
VLLM_QUANT = {"modelopt-nvfp4": "modelopt_fp4", "modelopt-fp8": "modelopt", "modelopt-mixed": "modelopt_mixed",
              "fp8-block": "fp8_per_block", "fp8-block128": "fp8_per_block", "fp8": "fp8", "compressed-tensors": "compressed-tensors", "awq": "awq", "gptq": "gptq",
              "mxfp4": "mxfp4", "bf16": None, "fp16": None}


class RecipeError(ValueError):
    pass


# --------------------------------------------------------------------------- the recipe file

# key -> (kind, meaning). kind: the validator. Unknown keys are an error, like a profile's.
KEYS: dict[str, tuple[str, str]] = {
    # identity
    "TITLE": ("text", "one line: what this recipe serves, on what"),
    "SOURCE": ("source", "Hugging Face repo id, or an absolute local directory on the Sparks"),
    "REVISION": ("sha", "full 40-hex commit of SOURCE (required for a Hub SOURCE)"),
    "WEIGHTS_STATUS": ("word", "published | local | to-quantize (no pack exists yet: quantize from QUANTIZE_FROM)"),
    "QUANTIZE_FROM": ("text", "repo@sha of the BF16/FP8 source a to-quantize recipe is made from"),
    "WEIGHTS_DIR": ("path", "where the checkpoint lives on EVERY Spark (the same path on each)"),
    "LICENCES": ("text", "weights=..; drafter=..; engine=.. (init reads the model card's front matter)"),
    # what the checkpoint is (init reads these)
    "ARCHITECTURE": ("word", "config.json architectures[0]"),
    "MODEL_TYPE": ("word", "config.json model_type"),
    "FORMAT": ("word", ("the checkpoint format in TensorFold's vocabulary: exl3 | modelopt-nvfp4 | modelopt-fp8 | "
                       "fp8-block | bf16 | mlx-q4g64 | gguf | ...")),
    "BPW": ("float", "bits per weight (EXL3 bits or avg_bits; NVFP4 ~4.5; FP8 8; BF16 16)"),
    "EXL3_CODEBOOK": ("word", "EXL3 codebook: mul1 | mcg | 3inst"),
    "EXL3_SCOPE": ("word", "EXL3 quantization scope (GLM TR3: glm53_routed_experts_only)"),
    "WEIGHT_BYTES": ("int", "safetensors bytes of the checkpoint"),
    "RESIDENT_BYTES": ("int", "bytes resident in memory when part of the checkpoint streams from disk (Engram)"),
    "LAYERS": ("int", "num_hidden_layers"),
    "ATTENTION_HEADS": ("int", "num_attention_heads (vLLM's TP must divide it)"),
    "KV_HEADS": ("int", "num_key_value_heads (empty for MLA latents)"),
    "KV_BYTES_PER_TOKEN": ("int", "KV cache bytes per token at bf16 (model_inspect.kv_per_token)"),
    "NATIVE_CONTEXT": ("int", "max_position_embeddings"),
    "EXPERTS": ("int", "routed experts (MoE)"),
    "EXPERTS_ACTIVE": ("int", "experts per token"),
    # serving choices
    "SPARKS": ("int", "the cluster size this recipe targets: TP = Sparks, one rank each (1..8)"),
    "ENGINE": ("word", "an engine id from data/recipe-engines.json, or auto"),
    "LAUNCHER": ("word", "d2-tensorfold | d2-vllm | hy4-vllm | mia-unit:<unit> (default: the engine's)"),
    "PROFILE": ("name", "the scripts/backends/models/<PROFILE>.env a D2 launcher serves (rendered by up)"),
    "SERVED_NAME": ("text", "the id /v1/models lists"),
    "PORT": ("int", "rank 0's API port on loopback (beast-gate publishes it)"),
    "CONTEXT": ("int", "the context window to serve"),
    "PARALLEL": ("int", "concurrent streams"),
    "KV_DTYPE": ("word", "KV cache dtype: bf16 | fp8 | fp4-compressed | auto"),
    "KV_POOL_GIB": ("float", "KV pool per rank, GiB"),
    "MEMORY_RESERVE_GIB": ("float", "engine reserve / workspace per rank, GiB (never below the family's measured value)"),
    "MIN_AVAIL_GIB": ("float", "refuse to start unless every Spark has this much MemAvailable"),
    "DRAFTER": ("text", "none | mtp | copy | in-checkpoint:<method> | <repo>@<40-hex sha>"),
    "AUX": ("text", "auxiliary weights the family needs beside the pack (Engram tables etc.), repo@sha:files"),
    "RANK_INPUTS": ("text", "the command that prepares per-rank input files (DeepSeek rank cache), or none"),
    # vLLM serve flags (rendered into the launch; the Hy4 golden test pins them)
    "DTYPE": ("word", "vLLM --dtype"),
    "EXPERT_PARALLEL": ("bool", "vLLM --enable-expert-parallel"),
    "MAX_NUM_SEQS": ("int", "vLLM --max-num-seqs"),
    "MAX_BATCHED_TOKENS": ("int", "vLLM --max-num-batched-tokens"),
    "KV_CACHE_BYTES": ("size", "vLLM --kv-cache-memory-bytes (6G)"),
    "GPU_MEM_UTIL": ("float", "vLLM --gpu-memory-utilization"),
    "SPEC_CONFIG": ("json", "vLLM --speculative-config JSON object, or off"),
    "MM_LIMIT": ("json", "vLLM --limit-mm-per-prompt JSON object"),
    "TOOL_PARSER": ("word", "tool-call parser (vLLM --tool-call-parser)"),
    "REASONING_PARSER": ("word", "reasoning parser (vLLM --reasoning-parser)"),
    "AUTO_TOOL_CHOICE": ("bool", "vLLM --enable-auto-tool-choice"),
    "IMAGE": ("text", "the engine image the launch runs (docker tag)"),
    "BASE_IMAGE": ("text", "the base image by digest the IMAGE is built from"),
    # the baseline a candidate recipe must beat (check --bench compares)
    "BASELINE_FROM": ("name", "the recipe whose measured speed this one is compared with"),
    "BASELINE_TOKS_1": ("float", "the baseline's decode tok/s, 1 stream"),
    "BASELINE_TOKS_4": ("float", "the baseline's aggregate decode tok/s, 4 streams"),
}
VALIDATORS = {
    "text": re.compile(r"^[^\x00-\x1f\x7f\"']{0,400}$"),
    "word": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,99}$"),
    "name": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"),
    "sha": SHA_RE,
    "int": re.compile(r"^[0-9]{1,18}$"),
    "float": re.compile(r"^[0-9]{1,6}(\.[0-9]{1,4})?$"),
    "bool": re.compile(r"^(true|false)$"),
    "size": re.compile(r"^[0-9]+[KMGT]?$"),
    "json": re.compile(r"^(off|[{][A-Za-z0-9_\":,. -]{1,300}[}])$"),
    "path": re.compile(r"^(~/|/)[A-Za-z0-9._/@+-]{1,300}$"),
    "source": re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*|/[A-Za-z0-9._/@+-]{1,300})$"),
}
LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
PROV_RE = re.compile(r"^\s*#\s*(?:(" + "|".join(PROV_TAGS) + r")\b:?\s*)?(.*)$")


def _split_value(raw: str) -> tuple[str, str]:
    """(value, trailing comment) under the spark.env grammar: one layer of matching quotes, else the
    value ends at whitespace + '#'."""
    v = raw.strip()
    if v[:1] in ("'", '"'):
        q = v[0]
        end = v.find(q, 1)
        if end < 0:
            raise RecipeError(f"unbalanced quote in {raw.strip()!r}")
        rest = v[end + 1:].strip()
        if rest and not rest.startswith("#"):
            raise RecipeError(f"text after the closing quote in {raw.strip()!r}")
        return v[1:end], rest
    m = re.search(r"\s+#", v)
    if m:
        return v[:m.start()].strip(), v[m.start():].strip()
    if v.startswith("#"):
        return "", v
    return v, ""


class Recipe:
    def __init__(self, path: Path, values: dict[str, str], prov: dict[str, tuple[str, str]]):
        self.path = path
        self.dir = path.parent
        self.name = path.parent.name
        self.values = values
        self.prov = prov

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, "") or default

    def num(self, key: str, default: float | None = None) -> float | None:
        v = self.values.get(key, "")
        return float(v) if v else default

    def flag(self, key: str, default: bool = False) -> bool:
        v = self.values.get(key, "")
        return default if not v else v == "true"

    def provenance(self, key: str) -> str:
        tag, note = self.prov.get(key, ("", ""))
        return (tag + (": " if tag and note else "") + note).strip()


def parse_recipe(text: str, path: Path) -> Recipe:
    values: dict[str, str] = {}
    prov: dict[str, tuple[str, str]] = {}
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            raise RecipeError(f"{path}:{n}: 'export' is shell; a recipe is data: KEY=VALUE")
        m = LINE_RE.match(s)
        if not m:
            raise RecipeError(f"{path}:{n}: not KEY=VALUE: {s[:60]!r}")
        key, raw = m.group(1), m.group(2)
        if key not in KEYS:
            raise RecipeError(f"{path}:{n}: unknown key {key} (beast-recipe show --keys lists them)")
        if key in values:
            raise RecipeError(f"{path}:{n}: {key} is set twice")
        try:
            value, comment = _split_value(raw)
        except RecipeError as e:
            raise RecipeError(f"{path}:{n}: {e}") from None
        kind = KEYS[key][0]
        if value and not VALIDATORS[kind].match(value):
            raise RecipeError(f"{path}:{n}: {key}={value[:80]!r} is not a valid {kind}")
        if kind in ("path", "source") and value.startswith(("/", "~/")) and \
                any(seg in (".", "..") for seg in value.split("/")):
            raise RecipeError(f"{path}:{n}: {key}={value[:80]!r}: no . or .. segments in a path")
        values[key] = value
        pm = PROV_RE.match(comment) if comment else None
        prov[key] = ((pm.group(1) or ""), (pm.group(2) or "").strip()) if pm else ("", "")
    return Recipe(path, values, prov)


def recipes_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("BEAST_RECIPES_DIR") or str(REPO / "recipes")))


def load_recipe(spec: str) -> Recipe:
    p = Path(os.path.expanduser(spec))
    if p.is_dir():
        p = p / "recipe.env"
    elif not p.is_file():
        if not NAME_RE.match(spec):
            raise RecipeError(f"{spec!r}: a recipe name (lowercase letters, digits, . _ -) or a path")
        p = recipes_dir() / spec / "recipe.env"
    if not p.is_file():
        have = ", ".join(r.name for r in list_recipes()) or "none"
        raise RecipeError(f"no recipe at {p} (have: {have}; beast-recipe init <model> makes one)")
    r = parse_recipe(p.read_text(), p.resolve())
    if not NAME_RE.match(r.name):
        raise RecipeError(f"recipe directory name {r.name!r}: lowercase letters, digits, . _ -")
    return r


def list_recipes() -> list[Recipe]:
    out = []
    d = recipes_dir()
    if d.is_dir():
        for p in sorted(d.glob("*/recipe.env")):
            try:
                out.append(parse_recipe(p.read_text(), p.resolve()))
            except (RecipeError, OSError):
                continue
    return out


def _clean(text) -> str:
    return UNSAFE.sub(" ", str(text)).replace("'", "").replace('"', "")[:300]


def render_recipe(header: list[str], fields: list[tuple]) -> str:
    """fields: (KEY, value, TAG, note) or ("#", text) for a comment line. Values are validated here
    too, so a checkpoint can never write a line init did not mean to write."""
    out = [f"# {_clean(h)}" if h else "#" for h in header] + [""]
    for f in fields:
        if f[0] == "#":
            out.append(f"# {_clean(f[1])}" if f[1] else "")
            continue
        key, value, tag, note = f
        value = "" if value is None else str(value)
        if not value:
            if note and tag in ("VERIFY", "ESTIMATED"):
                out.append(f"# {key}= (not set: {_clean(note)})")
            continue
        kind = KEYS[key][0]
        if value and not VALIDATORS[kind].match(value):
            out.append(f"# {key}: the checkpoint said {_clean(value)[:80]!r}, which is not a valid {kind}: VERIFY")
            continue
        shown = f"'{value}'" if (" " in value or "#" in value or '"' in value) else value
        c = f"{tag}: {_clean(note)}" if note else tag
        out.append(f"{key}={shown}" + (f"  # {c}" if c else ""))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- data

def _inherit_families(d: dict, p: Path) -> None:
    """An engine's `inherit_families` names where the families it does not list come from: another
    engine of this file (a newer release carries the older one's families forward, CLAIMED until its
    own capabilities --json), or `spark-families.json:<engine>` (the capabilities snapshot the intake
    registry records). The engine's own entries always win."""
    for e in d["engines"]:
        src = e.get("inherit_families")
        if not src:
            continue
        fams = e.setdefault("families", {})
        if src.startswith("spark-families.json:"):
            name = src.split(":", 1)[1]
            try:
                reg = json.loads(FAMILIES_FILE.read_text())
            except (OSError, ValueError) as ex:
                raise RecipeError(f"{p}: {e['id']}: cannot read {FAMILIES_FILE}: {ex}") from None
            se = next((x for x in reg.get("engines", []) if x.get("name") == name), None)
            if se is None:
                raise RecipeError(f"{p}: {e['id']}: inherit_families {src}: no such engine")
            ranks = se.get("ranks") or {}
            for mt, formats in (se.get("families") or {}).items():
                if mt in fams or mt in (e.get("refuses") or {}):
                    continue
                r = ranks.get(mt)
                by_fmt = {f: ranks[f"{mt}/{f}"] for f in formats if f"{mt}/{f}" in ranks}
                fams[mt] = {"formats": list(formats), "ranks": list(r or [1, 2]),
                            "rank_reason": (f"{FAMILIES_FILE.name} {name} ({se.get('source', '')[:80]})" if r else
                                            f"UNVERIFIED: no rank count recorded for {mt}; [1, 2] by the registry's rule"),
                            "inherited": src}
                if by_fmt:
                    fams[mt]["ranks_by_format"] = by_fmt
        else:
            base = engine_by_id(d, src)
            if base is None:
                raise RecipeError(f"{p}: {e['id']}: inherit_families {src}: no such engine")
            for mt, f in (base.get("families") or {}).items():
                if mt in fams or mt in (e.get("refuses") or {}):
                    continue
                fams[mt] = dict(f, rank_reason=f"CLAIMED (carried from {src}; re-snapshot capabilities --json on "
                                                f"{e.get('version', e['id'])}): " + f.get("rank_reason", ""),
                                inherited=src)


def _ranks_ok(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 8
                                                   for n in v)


def validate_data(d: dict, p: Path | str = "engine matrix") -> None:
    """Fail loudly on an entry that would plan quietly wrong (a typo'd gap id, ranks as a string)."""
    gaps = d["gaps"]

    def bad(msg):
        raise RecipeError(f"{p}: {msg}")
    for gid, g in gaps.items():
        days = g.get("days")
        if not (isinstance(days, list) and len(days) == 2 and all(isinstance(x, (int, float)) for x in days)
                and days[0] <= days[1]):
            bad(f"gap {gid}: days must be [lo, hi]")
    for e in d["engines"]:
        eid = e.get("id", "?")
        if e.get("status") not in ENGINE_STATUSES:
            bad(f"{eid}: status {e.get('status')!r} is not one of {', '.join(ENGINE_STATUSES)}")
        if e.get("stage_gap") and e["stage_gap"] not in gaps:
            bad(f"{eid}: stage_gap {e['stage_gap']!r} is not a gap id")
        for mt, f in (e.get("families") or {}).items():
            if f.get("serves", True) not in SERVES_VALUES:
                bad(f"{eid}/{mt}: serves={f.get('serves')!r} is not one of true, generator-only, reference, source-build")
            if not _ranks_ok(f.get("ranks")):
                bad(f"{eid}/{mt}: ranks must be a non-empty list of 1..8")
            for fmt, rs in (f.get("ranks_by_format") or {}).items():
                if fmt not in f.get("formats", []) or not _ranks_ok(rs):
                    bad(f"{eid}/{mt}: ranks_by_format[{fmt}] must name a listed format and a list of 1..8")
            ww = f.get("with_work") or {}
            if ww and not (isinstance(ww.get("ranks", []), list) and all(isinstance(n, int) for n in ww.get("ranks", []))):
                bad(f"{eid}/{mt}: with_work.ranks must be a list of ints")
            for g in ww.get("gaps", []):
                if g not in gaps:
                    bad(f"{eid}/{mt}: with_work gap {g!r} is not a gap id")
            for x in f.get("extra_gaps", []):
                days = x.get("days")
                if not (x.get("id") and x.get("title") and isinstance(days, list) and len(days) == 2
                        and all(isinstance(v, (int, float)) for v in days) and days[0] <= days[1]):
                    bad(f"{eid}/{mt}: extra_gaps entries need id, title and days [lo, hi]")


def load_data(path: Path | None = None) -> dict:
    p = path or DATA_FILE
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise RecipeError(f"cannot read the engine matrix {p}: {e}") from None
    if not isinstance(d.get("engines"), list) or not isinstance(d.get("gaps"), dict):
        raise RecipeError(f"{p}: no engines / gaps (see docs/BEAST_RECIPE.md)")
    _inherit_families(d, p)
    validate_data(d, p)
    return d


def engine_by_id(data: dict, eid: str) -> dict | None:
    return next((e for e in data["engines"] if e["id"] == eid), None)


def _vllm_registry() -> dict:
    try:
        return json.loads(VLLM_FILE.read_text())
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- fit maths

def weights_gib(r: Recipe, data: dict) -> tuple[float | None, str]:
    """(resident checkpoint GiB, how it was found)."""
    if r.get("RESIDENT_BYTES"):
        return int(r.get("RESIDENT_BYTES")) / GIB, "RESIDENT_BYTES (" + (r.provenance("RESIDENT_BYTES") or "recipe") + ")"
    s = (data.get("sizing") or {}).get(r.get("MODEL_TYPE"))
    res = ((s or {}).get("resident_gib") or {}).get(r.get("FORMAT"))
    if res:
        return float(res), f"resident, the rest streams from disk (DATA sizing: {s['source']})"
    if r.get("WEIGHT_BYTES"):
        return int(r.get("WEIGHT_BYTES")) / GIB, "WEIGHT_BYTES (" + (r.provenance("WEIGHT_BYTES") or "recipe") + ")"
    if s and r.get("BPW") and "fixed_gib" in s and r.get("FORMAT") == "exl3":
        g = s["fixed_gib"] + s["per_bit_gib"] * float(r.get("BPW"))
        return g, f"ESTIMATED: {s['fixed_gib']} + {s['per_bit_gib']} x {r.get('BPW')} bpw (data sizing: {s['source']})"
    return None, "unknown: no WEIGHT_BYTES and no sizing rule for this model_type"


def project_bytes(r: Recipe, data: dict, bpw: float) -> tuple[float | None, str]:
    """Checkpoint GiB at another bpw: the family's sizing rule, else linear in bpw (slightly pessimistic:
    the BF16 / high-bit parts do not grow)."""
    s = (data.get("sizing") or {}).get(r.get("MODEL_TYPE"))
    if s and "fixed_gib" in s and r.get("FORMAT") == "exl3":       # the rules are EXL3 packs' (expert bits)
        return s["fixed_gib"] + s["per_bit_gib"] * bpw, "sizing rule"
    w, _ = weights_gib(r, data)
    b0 = r.num("BPW")
    if w is None or not b0:
        return None, "no size"
    return w * bpw / b0, "linear in bpw"


def per_rank_gib(total_gib: float, n: int, share: float) -> float:
    """W = checkpoint / TP x (1 + r (TP - 1)): every rank holds its 1/TP share plus a full copy of the
    replicated tensors (norms, router gates, unsharded biases), r = their share (0.008 on DeepSeek)."""
    return total_gib / n * (1 + share * (n - 1))


def _size_gib(v: str) -> float:
    m = re.match(r"^([0-9]+)([KMGT]?)$", v)
    if not m:
        return 0.0
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[m.group(2)]
    return int(m.group(1)) * mult / GIB


def kv_gib(r: Recipe, launch: dict, n: int) -> tuple[float, str]:
    if r.get("KV_POOL_GIB"):
        return float(r.get("KV_POOL_GIB")), "KV_POOL_GIB"
    if r.get("KV_CACHE_BYTES"):
        return _size_gib(r.get("KV_CACHE_BYTES")), "KV_CACHE_BYTES"
    if launch.get("kv_pool_gib") is not None:
        return float(launch["kv_pool_gib"]), "the family's launch value (DATA)"
    per_tok = r.num("KV_BYTES_PER_TOKEN")
    if per_tok:
        ctx = r.num("CONTEXT") or r.num("NATIVE_CONTEXT") or 131072
        streams = r.num("PARALLEL") or 4
        splits = r.num("KV_HEADS") and (r.num("KV_HEADS") or 0) >= 2
        g = per_tok * ctx * streams / (n if splits else 1) / GIB
        return min(g, 40.0), "ESTIMATED: KV_BYTES_PER_TOKEN x CONTEXT x PARALLEL" + (" / TP" if splits else "")
    return 8.0, "ESTIMATED: 8 GiB placeholder (KV size unknown)"


def fit_row(r: Recipe, data: dict, launch: dict, n: int, kind: str) -> dict:
    hw = data["hardware"]["spark"]
    total, _ = weights_gib(r, data)
    if total is None:
        return {"sparks": n, "w_gib": None, "rule": "unknown", "fit": "unknown"}
    w = per_rank_gib(total, n, hw["replicated_share"]) if n > 1 else total
    rule = "fits" if w <= hw["rank_weight_comfortable_gib"] else "tight" if w <= hw["rank_weight_limit_gib"] \
        else "does not fit"
    kv, kv_how = kv_gib(r, launch, n)
    reserve = r.num("MEMORY_RESERVE_GIB") or launch.get("memory_reserve_gib") or 5.0
    os_gib = hw["os_head_gib"]
    used = w + kv + reserve + os_gib
    headroom = hw["mem_total_gib"] - used
    budget = "fits" if headroom >= hw["headroom_comfortable_gib"] else "tight" if headroom >= 0 else "does not fit"
    row = {"sparks": n, "w_gib": round(w, 1), "rule": rule, "kv_gib": round(kv, 1), "kv_from": kv_how,
           "reserve_gib": round(float(reserve), 1), "os_gib": os_gib, "used_gib": round(used, 1),
           "headroom_gib": round(headroom, 1), "budget": budget}
    if kind == "vllm":
        gmu = r.num("GPU_MEM_UTIL") or 0.85
        cap = gmu * hw["mem_total_gib"]
        row["vllm_cap_gib"] = round(cap, 1)
        if w + kv + reserve > cap:
            budget = "does not fit"
            row["budget"] = budget
            row["why"] = f"weights + KV + reserve {w + kv + reserve:.1f} GiB > --gpu-memory-utilization {gmu} x MemTotal"
    order = ["fits", "tight", "does not fit"]
    row["fit"] = order[max(order.index(rule), order.index(budget))]
    return row


# --------------------------------------------------------------------------- engine selection

def _variant_mismatch(fam: dict, r: Recipe) -> list[str]:
    want = fam.get("exl3") or {}
    out = []
    if "bits" in want and r.get("BPW"):
        b = float(r.get("BPW"))
        if not (b.is_integer() and int(b) in want["bits"]):
            out.append(f"bits {r.get('BPW')} (reads {'/'.join(map(str, want['bits']))})")
    if "codebook" in want and r.get("EXL3_CODEBOOK") and r.get("EXL3_CODEBOOK") not in want["codebook"]:
        out.append(f"codebook {r.get('EXL3_CODEBOOK')} (reads {'/'.join(want['codebook'])})")
    if "scope" in want and r.get("EXL3_SCOPE") and r.get("EXL3_SCOPE") not in want["scope"]:
        out.append(f"scope {r.get('EXL3_SCOPE')} (reads {'/'.join(want['scope'])})")
    return out


def _vllm_family(e: dict, r: Recipe) -> tuple[dict | None, str]:
    reg = _vllm_registry()
    commit = ((reg.get("_provenance") or {}).get("commit") or "?")[:12]
    arch, fmt = r.get("ARCHITECTURE"), r.get("FORMAT")
    if fmt in ("exl3", "gguf") or fmt.startswith("mlx"):
        return None, f"{fmt} weights: vLLM cannot load them"
    if not fmt:
        return None, "FORMAT is empty: cannot tell which vLLM quantization this needs"
    if fmt not in VLLM_QUANT:
        return None, f"{fmt}: no vLLM quantization method maps to it"
    qm = VLLM_QUANT[fmt]
    if qm and qm not in set(reg.get("quantization_methods", [])):
        return None, f"{qm} is not in vLLM's quantization methods @{commit} (data/vllm.json)"
    if arch not in set(reg.get("generation_architectures", [])):
        return None, f"{arch or '?'} is not in vLLM's model registry @{commit} (data/vllm.json)"
    heads, kvh = int(r.num("ATTENTION_HEADS") or 0), int(r.num("KV_HEADS") or 0)
    ranks = [n for n in range(1, 9) if not heads or (heads % n == 0 and (not kvh or kvh % n == 0 or n % kvh == 0))]
    return ({"formats": [fmt], "ranks": ranks, "rank_reason": f"vLLM's TP must divide the {heads or '?'} attention "
             f"heads{f' and the {kvh} KV heads' if kvh else ''}: {', '.join(map(str, ranks))}",
             "launch": {"memory_reserve_gib": 5, "source": "ESTIMATED: a fixed 5 GiB vLLM runtime reserve"}}, "")


def family_for(e: dict, r: Recipe) -> tuple[dict | None, str]:
    """(the engine's family entry for this checkpoint, why not)."""
    mt, fmt = r.get("MODEL_TYPE"), r.get("FORMAT")
    if e.get("registry") == "vllm.json":
        return _vllm_family(e, r)
    if mt in (e.get("refuses") or {}):
        return None, f"refuses {mt}: {e['refuses'][mt]}"
    fam = (e.get("families") or {}).get(mt)
    if not fam:
        if e.get("kind") == "tensorfold-native" and mt:
            return None, (f"UNRECORDED: {mt} is not in this engine's recorded families (unknown, not refused: "
                          "re-snapshot tensorfold capabilities --json)")
        return None, f"no family for model_type {mt or '?'}"
    if fmt not in fam["formats"]:
        return None, f"reads {mt} only as {', '.join(fam['formats'])}; this is {fmt or '?'}"
    if fmt == "exl3":
        mism = _variant_mismatch(fam, r)
        if mism:
            return None, f"reads {mt} EXL3 only as " + "; ".join(mism) + (f" [{fam['exl3_note']}]" if fam.get("exl3_note") else "")
    return fam, ""


def _gap(data: dict, gid: str, extra: dict | None = None) -> dict:
    if gid not in data["gaps"]:
        raise RecipeError(f"engine matrix: unknown gap id {gid!r}")
    g = dict(data["gaps"][gid])
    g["id"] = gid
    if extra:
        g.update(extra)
    g["blocking"] = gid not in NONBLOCKING_GAPS
    return g


def evaluate_engine(r: Recipe, data: dict, e: dict, sparks: int) -> dict:
    """One engine as a path to serving this recipe: per cluster size, whether it is allowed, the gaps,
    the fit, and the resulting verdict."""
    fam, why = family_for(e, r)
    out = {"engine": e["id"], "title": e["title"], "kind": e["kind"], "status": e["status"], "reads": bool(fam),
           "version": e.get("version", "") if e["kind"] == "tensorfold-native" else "",
           "why": why, "launcher": e.get("launcher", "none"), "rows": {}, "verdict": "BLOCKED"}
    if not fam:
        return out
    fmt = r.get("FORMAT")
    serve_ranks = list((fam.get("ranks_by_format") or {}).get(fmt) or fam.get("ranks") or [])
    serves = fam.get("serves", True)
    base: list[dict] = []
    if e["status"] == "unreleased":
        base.append(_gap(data, "upstream-release", {"note": f"{e['id']} has no published release"}))
    elif e["status"] != "installed":
        base.append(_gap(data, e.get("stage_gap") or ("engine-stage" if e["kind"] == "tensorfold-native" else "image-build")))
    if e.get("launcher", "none") == "none" and serves != "reference":
        base.append(_gap(data, "launcher"))
    if r.get("WEIGHTS_STATUS") == "to-quantize":
        base.append(_gap(data, "quantize", {"note": f"from {r.get('QUANTIZE_FROM') or 'the BF16/FP8 source'}"}))
    extra = [dict(x, blocking=(x.get("days") or [0, 0])[1] > 0) for x in fam.get("extra_gaps", [])]
    ww = fam.get("with_work") or {}
    gen_gaps = []
    if serves == "generator-only":
        gen_gaps = [_gap(data, "server-wrap")]
    elif serves == "reference":
        gen_gaps = [_gap(data, "reference-only", {"note": "reference implementation only, no server for this family"})]
    elif serves == "source-build":
        gen_gaps = [_gap(data, g) for g in ww.get("gaps", ["image-build"])]
    elif serves is not True:            # validate_data refuses this; a backstop for hand-built data
        raise RecipeError(f"{e['id']}/{r.get('MODEL_TYPE')}: unknown serves {serves!r}")
    launch = fam.get("launch") or {}
    for n in range(1, data["hardware"]["spark"]["max_sparks"] + 1):
        # past two Sparks the cabling and fabric are work whatever the engine (hardware.topologies)
        topo = [_gap(data, "topology")] if n > 2 else []
        if serves is True and n in serve_ranks:
            gaps, how = list(base) + topo, "serves"
        elif serves is not True and n in serve_ranks:
            gaps, how = base + gen_gaps + extra + topo, str(serves)
        elif n in ww.get("ranks", []):
            gaps = base + [_gap(data, g) for g in ww.get("gaps", []) if g not in {x["id"] for x in base + gen_gaps}] \
                + gen_gaps + extra + topo
            how = "with work"
        else:
            out["rows"][n] = {"allowed": False}
            continue
        seen, uniq = set(), []
        for g in gaps:
            if g["id"] not in seen:
                seen.add(g["id"])
                uniq.append(g)
        row = fit_row(r, data, launch, n, "vllm" if e["kind"] == "vllm" else "tensorfold")
        blocking = [g for g in uniq if g["blocking"]]
        if row["fit"] in ("fits", "tight"):
            v = "SERVABLE-WITH-WORK" if blocking else "SERVABLE-NOW"
        else:
            v = "BLOCKED"
        out["rows"][n] = dict(row, allowed=True, how=how, gaps=uniq, verdict=v)
    ok_here = [n for n, x in out["rows"].items() if x.get("allowed") and n <= sparks and x["verdict"] != "BLOCKED"]
    ok_more = [n for n, x in out["rows"].items() if x.get("allowed") and n > sparks and x["verdict"] != "BLOCKED"]
    if ok_here:
        top = min(VERDICTS.index(out["rows"][n]["verdict"]) for n in ok_here)
        # the target size itself when it is as good as a smaller one (more KV room: the recipe's intent),
        # else the largest size under it that is
        same = [n for n in ok_here if VERDICTS.index(out["rows"][n]["verdict"]) == top]
        best = sparks if sparks in same else max(same)
        out["n"], out["verdict"] = best, out["rows"][best]["verdict"]
    elif ok_more:
        out["n"], out["verdict"] = min(ok_more), "NEEDS-MORE-HARDWARE"
    else:
        out["n"] = None
        out["verdict"] = "BLOCKED"
        allowed = [str(n) for n, x in out["rows"].items() if x.get("allowed")]
        out["why"] = ("allowed at " + ", ".join(allowed) + ", but it does not fit at any of them") if allowed \
            else "no rank count recorded for this family"
    if e["status"] == "unreleased":
        # nothing can be staged until upstream publishes it: never a plan to act on
        would = out["verdict"] + (f" at TP{out['n']}" if out.get("n") else "")
        out["verdict"] = "BLOCKED"
        out["why"] = (f"{e['id']} is not released upstream (gap upstream-release): pin a released version; "
                      f"once it is out: {would}")
    out["launch"] = launch
    out["launch_note"] = fam.get("launch_note", "")
    out["rank_reason"] = fam.get("rank_reason", "")
    out["note"] = ww.get("note", "")
    out["family"] = fam
    return out


def _days(gaps: list[dict]) -> tuple[float, float]:
    lo = sum((g.get("days") or [0, 0])[0] for g in gaps if g.get("blocking"))
    hi = sum((g.get("days") or [0, 0])[1] for g in gaps if g.get("blocking"))
    return lo, hi


def choose(r: Recipe, data: dict, sparks: int) -> dict:
    paths = [evaluate_engine(r, data, e, sparks) for e in data["engines"]]

    def key(p):
        gaps = p["rows"].get(p.get("n") or 0, {}).get("gaps") or []
        # an engine with no server path (no launcher, or a reference implementation) sorts after one that
        # serves; then the target's own size; then the least work; then what is installed
        no_server = p.get("launcher", "none") == "none" or (p.get("family") or {}).get("serves") == "reference"
        return (VERDICTS.index(p["verdict"]), 1 if no_server else 0, 0 if p.get("n") == sparks else 1,
                _days(gaps)[0], 0 if p["status"] == "installed" else 1)
    ranked = sorted(paths, key=key)
    pinned = r.get("ENGINE", "auto")
    if pinned != "auto":
        chosen = next((p for p in paths if p["engine"] == pinned), None)
        if chosen is None:
            raise RecipeError(f"ENGINE={pinned}: no such engine in {DATA_FILE.name} "
                              f"(have: {', '.join(e['id'] for e in data['engines'])})")
    else:
        chosen = ranked[0]
    return {"chosen": chosen, "paths": paths, "ranked": ranked, "pinned": pinned != "auto"}


def _row_gaps(p: dict) -> list[dict]:
    return p["rows"].get(p.get("n") or 0, {}).get("gaps") or []


def hardware_why(r: Recipe, data: dict, p: dict, sparks: int) -> list[str]:
    """Why each size up to the one that works does not (NEEDS-MORE-HARDWARE)."""
    out = []
    limit = (p.get("n") or data["hardware"]["spark"]["max_sparks"])
    for n in range(1, limit + 1):
        x = p["rows"].get(n) or {}
        if not x.get("allowed"):
            fr = fit_row(r, data, p.get("launch") or {}, n, "vllm" if p["kind"] == "vllm" else "tensorfold")
            out.append(f"{n} Spark{'s' if n > 1 else ''}: {fr.get('w_gib')} GiB a rank ({fr.get('fit')}), and "
                       f"{p['engine']} does not run this family at {n} rank{'s' if n > 1 else ''}")
        elif x["verdict"] == "BLOCKED":
            out.append(f"{n} Spark{'s' if n > 1 else ''}: {x['w_gib']} GiB of weights a rank "
                       f"({x['rule']}; headroom {x['headroom_gib']} GiB)")
        else:
            out.append(f"{n} Sparks: {x['w_gib']} GiB a rank, {x['fit']} ({x['verdict']})")
    return out


# --------------------------------------------------------------------------- the plan

def cluster_nodes(f: Path) -> list[str]:
    """CLUSTER_NODES of a cluster file, validated: rank 0 is `local`, every other node a plain ssh target
    (they reach ssh argv and rsync targets)."""
    for line in f.read_text().splitlines():
        m = re.match(r"^\s*CLUSTER_NODES\s*=\s*(.*)$", line)
        if m:
            v, _ = _split_value(m.group(1))
            ns = v.split()
            if ns and ns[0] != "local":
                raise RecipeError(f"{f}: CLUSTER_NODES rank 0 must be 'local'")
            for i, n in enumerate(ns[1:], 1):
                if n == "local" or not NODE_RE.match(n):
                    raise RecipeError(f"{f}: node {i} {n!r} is not a plain ssh target")
            return ns
    return []


def default_cluster_file() -> Path:
    return BACKENDS / "cluster.env"


def nodes(sparks: int, cluster_file: Path | None = None) -> tuple[list[str], str]:
    """The ssh targets in rank order (rank 0 = local) and where they came from: the first `sparks` nodes
    of the cluster file; placeholders (`<rankN>`, never runnable) for any it does not list."""
    f = cluster_file or default_cluster_file()
    if f.is_file():
        ns = cluster_nodes(f)
        if ns:
            if len(ns) >= sparks:
                return ns[:sparks], (str(f) if len(ns) == sparks else f"the first {sparks} of {len(ns)} nodes in {f}")
            return ns + [f"<rank{i}>" for i in range(len(ns), sparks)], \
                f"{f} lists {len(ns)} of {sparks} nodes: placeholders for the rest (add them to CLUSTER_NODES)"
    if sparks == 1:
        return ["local"], "default (one Spark)"
    if sparks == 2:
        return ["local", "spark-2"], "default (the pair: spark-2 is the worker's ssh alias on the QSFP link)"
    return ["local"] + [f"<rank{i}>" for i in range(1, sparks)], "placeholders: write scripts/backends/cluster.env"


def recipe_cluster(r: Recipe, n: int | None) -> str:
    """The --cluster a D2 recipe's spark-cluster.sh calls take: none on the pair (TP2: the pair's own
    cluster.env), else recipes/<name>/cluster.env, which stage/up --run write from the first N nodes of
    cluster.env (machine-local: *.env is not committed)."""
    return "" if (n or 2) == 2 else f"recipes/{r.name}/cluster.env"


def _cl(r: Recipe, n: int | None, prefix: str = "") -> str:
    c = recipe_cluster(r, n)
    return f" --cluster {prefix}{c}" if c else ""


def write_recipe_cluster(r: Recipe, n: int, cluster_file: Path | None = None) -> Path | None:
    """recipes/<name>/cluster.env: CLUSTER_NODES = the first n nodes of the cluster file, CLUSTER_REPO and
    CLUSTER_NAME copied. Refuses when the file lists fewer than n."""
    if not recipe_cluster(r, n):
        return None
    src = cluster_file or default_cluster_file()
    ns = cluster_nodes(src) if src.is_file() else []
    if n == 1 and not ns:
        ns = ["local"]
    if len(ns) < n:
        raise RecipeError(f"{r.name} needs {n} nodes; {src} lists {len(ns)}: add them to CLUSTER_NODES first")
    keep = [ln for ln in (src.read_text().splitlines() if src.is_file() else [])
            if re.match(r"^\s*CLUSTER_(REPO|NAME)\s*=", ln)]
    out = r.dir / "cluster.env"
    out.write_text(f"# beast-recipe {r.name}: the first {n} node(s) of {src} (machine-local, not committed)\n"
                   f"CLUSTER_NODES=\"{' '.join(ns[:n])}\"\n" + "".join(x + "\n" for x in keep))
    return out


def launcher_of(r: Recipe, p: dict) -> str:
    return r.get("LAUNCHER") or p.get("launcher") or "none"


def unit_of(r: Recipe, launcher: str) -> str:
    if launcher == "hy4-vllm":
        return KNOWN_UNITS["hy4"]
    if launcher.startswith("mia-unit:"):
        return launcher.split(":", 1)[1] + ".service"
    return f"beast-recipe-{r.name}.service"


def profile_name(r: Recipe) -> str:
    return r.get("PROFILE") or f"recipe-{r.name}"


def weights_dir(r: Recipe) -> str:
    if r.get("WEIGHTS_DIR"):
        return r.get("WEIGHTS_DIR")
    src = r.get("SOURCE")
    if src.startswith("/"):
        return src
    return f"~/Documents/weights/{src}" if src else "~/Documents/weights/<to-quantize>"


def drafter_spec(r: Recipe) -> tuple[str, str, str]:
    """(kind, repo, revision) of DRAFTER."""
    d = r.get("DRAFTER", "none")
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*)@([0-9a-f]{40})$", d)
    if m:
        return "repo", m.group(1), m.group(2)
    return d.split(":", 1)[0], "", ""


def hub_url(repo: str, rev: str) -> str:
    return f"https://huggingface.co/{repo}/tree/{rev}"


def q(*a: str) -> str:
    return " ".join(shlex.quote(str(x)) for x in a)


def at_repo(node: str, cmd: str) -> str:
    """A repo-relative command: on a worker, ssh starts in $HOME, so it runs from ~/openbeast."""
    return cmd if node == "local" else f"cd ~/openbeast && {cmd}"


def is_protected(wd: str) -> bool:
    """The kept-pack rule (hy4_protected): a path that names a kept pack, IS $HOME / ~/Documents /
    ~/Documents/weights (which hold one), or holds one locally."""
    raw = wd.rstrip("/")
    if re.match(r"^(~|/home/[^/]+|/Users/[^/]+|/root)(/Documents(/weights)?)?$", raw) or \
            raw == os.path.expanduser("~"):
        return True
    if any(n in wd for n in PROTECTED_NAMES):
        return True
    pth = os.path.normpath(os.path.expanduser(wd))
    return any(os.path.exists(os.path.join(pth, n)) for n in PROTECTED_NAMES)


def mia_unit_mismatch(r: Recipe, p: dict, unit: str) -> str | None:
    """Why a Mia recipe unit is NOT this recipe's launch (it serves another pack or rank count), or None."""
    s = MIA_UNIT_SPEC.get(unit)
    if not s:
        return None
    if (p.get("n") or 0) != s["tp"]:
        return f"{unit} runs TP{s['tp']}, the plan needs TP{p.get('n') or '-'}"
    if r.get("WEIGHTS_STATUS") == "to-quantize":
        return f"{unit} serves its own pack; this one is still to be quantized"
    if s["weights"] and weights_dir(r).rstrip("/") != s["weights"]:
        return f"{unit} serves {s['weights']}, not {weights_dir(r)}"
    return None


def qp(path: str) -> str:
    """A path for a remote shell: quoted, but a leading ~/ stays unquoted so it expands on that node."""
    return "~/" + shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)


SCOPE = "systemd-run --user --scope -p MemoryMax=8G -p MemoryHigh=6G"


def engine_steps(r: Recipe, p: dict, eng: dict, ns: list[str]) -> list[tuple[str, str]]:
    """(node, command) pairs: check, then stage when the engine is not on every Spark."""
    out: list[tuple[str, str]] = []
    kind, v = eng["kind"], eng.get("version", "")
    if kind == "tensorfold-native":
        for n in ns:
            out.append((n, f"~/.local/bin/tensorfold --version   # want tensorfold-native {v}"))
        if eng["status"] == "unreleased":
            out.append(("-", (f"tensorfold-native {v} is not released upstream: nothing to stage (gap: upstream-release; "
                              "pin a released version)")))
        elif eng["status"] != "installed":
            out.append(("connected box", (f"scripts/engine.sh stage tensorfold --version {v} ~/carry/tensorfold-{v}"
                                         "   # signed archive + MANIFEST, for the offline Sparks")))
            for n in ns:
                out.append((n, at_repo(n, f"scripts/engine.sh install tensorfold --version {v} --from ~/carry/tensorfold-{v}")
                            + "   # online: drop --from; rollback: scripts/engine.sh rollback tensorfold"))
            out.append((ns[0], (f"mkdir -p recipes/{r.name}/runs && ~/.local/bin/tensorfold capabilities --json > "
                                f"recipes/{r.name}/runs/capabilities-{v}.json"
                                "   # after the install; then update scripts/backends/data/recipe-engines.json from it")))
    elif eng.get("image"):
        img = r.get("IMAGE") or eng["image"]
        for n in ns:
            out.append((n, f"docker image inspect {q(img)} --format '{{{{.Id}}}}'"))
        if eng["status"] != "installed":
            if kind == "vllm" and launcher_of(r, p) == "d2-vllm":
                out.append((ns[0], f"docker pull {q(img)}   # then pin VLLM_IMAGE_DIGEST in spark.env (D2 reads it)"))
            else:
                out.append((ns[0], f"docker build -t {q(img)} <the recipe's image/ dir>   # base by digest, content-hashed tag"))
            out.append((ns[0], f"scripts/backends/spark-cluster.sh sync-image {q(img)}   # docker save | load over the link"))
            out.append((ns[0], (f"scripts/backends/spark-cluster.sh save-image {q(img)} ~/carry/{re.sub('[^A-Za-z0-9._-]', '_', img)}.tar.gz"
                               "   # the offline carry")))
    else:
        out.append(("-", f"no launcher for {eng['id']} in OpenBeast yet (gap: launcher)"))
    return out


def stage_steps(r: Recipe, p: dict, eng: dict, ns: list[str]) -> list[tuple[str, str, str]]:
    """(step, node, command)."""
    out: list[tuple[str, str, str]] = []
    launcher = launcher_of(r, p)
    wd = weights_dir(r)
    src, rev = r.get("SOURCE"), r.get("REVISION")
    if r.get("WEIGHTS_STATUS") == "to-quantize":
        out.append(("quantize", ns[0], "python convert.py -i <BF16/FP8 source dir> -o " + qp(wd) + " -w <work dir> -b "
                    + r.get("BPW", "?") + " -r   # exllamav3 1.6.7, in the layout the engine reads (gap: quantize; "
                    "a quantizing Spark serves nothing)"))
        out.append(("quantize", ns[0], f"scripts/backends/spark-model-intake.sh {qp(wd)}   # the reader's layout, before anything else"))
    if launcher.startswith("d2-"):
        prof = profile_name(r)
        sums = r.dir / "SHA256SUMS"
        cl = _cl(r, p.get("n"))
        out.append(("profile", ns[0], (f"# models/{prof}.env + recipes/{r.name}/{unit_of(r, launcher)}: stage --run renders "
                    f"them when missing (= beast-recipe up {r.name} --write-profile); commit them, workers verify from the same commit")))
        if cl:
            out.append(("profile", ns[0], (f"# recipes/{r.name}/cluster.env: stage --run writes it (the first {p.get('n')} "
                        "node(s) of cluster.env; machine-local)")))
        if r.get("WEIGHTS_STATUS") == "local":
            if sums.is_file():
                out.append(("verify", ns[0], (f"cd {qp(wd)} && {SCOPE} sha256sum -c --quiet -   # < recipes/{r.name}/SHA256SUMS; read-only: "
                            "the Hub's LFS hashes, before the lock trusts these bytes")))
                for n in ns[1:]:
                    out.append(("verify", n, f"cd {qp(wd)} && {SCOPE} sha256sum -c --quiet -   # < recipes/{r.name}/SHA256SUMS from the head; read-only"))
            out.append(("weights", ns[0], (f"scripts/backends/model-fetch.sh --profile {prof}   # a local SOURCE: records "
                        f"models/{prof}.lock on the first run (commit it), verifies against it after; reads the folder, never writes it")))
            for n in ns[1:]:
                out.append(("verify", n, (f"cd ~/openbeast && scripts/backends/model-fetch.sh --profile {prof} --verify   # the "
                            "worker's copy against the committed lock")))
            if len(ns) > 1:
                out.append(("mirror", ns[0], (f"# a worker that lacks the copy: scripts/backends/spark-cluster.sh sync-weights "
                            f"--profile {prof}{cl} (never onto a kept pack)")))
        else:
            out.append(("weights", ns[0], (f"scripts/backends/model-fetch.sh --profile {prof}   # fetch the pinned revision "
                        "(and the drafter) into MODELS_DIR, or verify a copy, and lock it")))
            if len(ns) > 1:
                out.append(("mirror", ns[0], (f"scripts/backends/spark-cluster.sh sync-weights --profile {prof}{cl}   # head -> every "
                            "worker over the fabric, verified against the head's lock")))
    elif launcher == "hy4-vllm":
        out.append(("weights", ns[0], ("scripts/backends/spark-hy4-stage.sh --run --only weights,verify,mirror   # capped "
                    "scope, stall watchdog, Hub LFS sums, rsync to spark-2 (never --delete)")))
        out.append(("verify", ns[0], "scripts/backends/spark-hy4-stage.sh --check"))
    else:
        local = r.get("WEIGHTS_STATUS") == "local"
        protected = is_protected(wd)
        sums = r.dir / "SHA256SUMS"
        if src and not src.startswith("/") and rev and not local and not protected:
            out.append(("weights", ns[0], (f"scripts/weights-download.sh {hub_url(src, rev)} {qp(wd)}   # every file "
                        "against the Hub's own hash; re-run resumes")))
        elif local or protected:
            out.append(("weights", ns[0], f"# {wd}: already on every Spark (WEIGHTS_STATUS=local"
                        + (", a kept pack: read, never written" if protected else "") + "): verify only"))
        if sums.is_file():
            out.append(("verify", ns[0], f"cd {qp(wd)} && {SCOPE} sha256sum -c --quiet -   # < recipes/{r.name}/SHA256SUMS; read-only"))
        elif local:
            for n in ns:
                out.append(("verify", n, (f"cd ~/openbeast && scripts/backends/spark-model-intake.sh {qp(wd)}   # read-only: "
                            "the family, index signature and pack fingerprint against data/spark-families.json")))
        for n in ns[1:]:
            if not (local or protected):
                out.append(("mirror", ns[0], (f"{SCOPE} rsync -a --partial --mkpath {qp(wd + '/')} {shlex.quote(n)}:{qp(wd + '/')}   # over the "
                            "QSFP link; never --delete")))
            if sums.is_file():
                out.append(("verify", n, f"cd {qp(wd)} && {SCOPE} sha256sum -c --quiet -   # < recipes/{r.name}/SHA256SUMS from the head; read-only"))
        kind, drepo, drev = drafter_spec(r)
        if kind == "repo":
            ddir = f"~/Documents/weights/{drepo}"
            out.append(("drafter", ns[0], f"scripts/weights-download.sh {hub_url(drepo, drev)} {qp(ddir)}"))
            for n in ns[1:]:
                out.append(("drafter", ns[0], f"{SCOPE} rsync -a --partial --mkpath {qp(ddir + '/')} {shlex.quote(n)}:{qp(ddir + '/')}"))
    if r.get("AUX"):
        out.append(("aux", ns[0], f"# AUX: {r.get('AUX')}"))
    if r.get("RANK_INPUTS") and r.get("RANK_INPUTS") != "none":
        out.append(("rank-inputs", ns[0], f"# RANK_INPUTS: {r.get('RANK_INPUTS')}"))
    return out


def vllm_serve_args(r: Recipe) -> list[str]:
    """The vLLM serve flags both ranks get (rank and address flags are added per rank), in the order
    scripts/backends/hy4-vllm/config.sh hy4_serve_args builds them (tests/test_beast_recipe.py pins it)."""
    a = ["/model", "--served-model-name", r.get("SERVED_NAME"),
         "--tensor-parallel-size", r.get("SPARKS", "2")]
    if r.flag("EXPERT_PARALLEL"):
        a.append("--enable-expert-parallel")
    if r.get("DTYPE"):
        a += ["--dtype", r.get("DTYPE")]
    if r.get("CONTEXT"):
        a += ["--max-model-len", r.get("CONTEXT")]
    if r.get("MAX_NUM_SEQS"):
        a += ["--max-num-seqs", r.get("MAX_NUM_SEQS")]
    if r.get("MAX_BATCHED_TOKENS"):
        a += ["--max-num-batched-tokens", r.get("MAX_BATCHED_TOKENS")]
    if r.get("KV_CACHE_BYTES"):
        a += ["--kv-cache-memory-bytes", r.get("KV_CACHE_BYTES")]
    if r.get("GPU_MEM_UTIL"):
        a += ["--gpu-memory-utilization", r.get("GPU_MEM_UTIL")]
    if r.get("SPEC_CONFIG") and r.get("SPEC_CONFIG") != "off":
        a += ["--speculative-config", r.get("SPEC_CONFIG")]
    if r.get("MM_LIMIT"):
        a += ["--limit-mm-per-prompt", r.get("MM_LIMIT")]
    if r.get("REASONING_PARSER"):
        a += ["--reasoning-parser", r.get("REASONING_PARSER")]
    if r.get("TOOL_PARSER"):
        a += ["--tool-call-parser", r.get("TOOL_PARSER")]
    if r.flag("AUTO_TOOL_CHOICE"):
        a.append("--enable-auto-tool-choice")
    return a


def render_profile(r: Recipe, p: dict) -> str:
    """The D2 profile (scripts/backends/models/<PROFILE>.env) the recipe serves through, validated by
    obprofile's own rules before it is shown or written."""
    launcher = launcher_of(r, p)
    backend = "vllm" if launcher == "d2-vllm" else "tensorfold"
    local = r.get("WEIGHTS_STATUS") == "local"
    source = weights_dir(r) if local else r.get("SOURCE")
    if local and not source.startswith("/"):
        raise RecipeError(f"WEIGHTS_DIR={source}: a local D2 SOURCE must be an absolute path (every Spark "
                          "reads the same committed profile)")
    lines = [f"# Rendered by beast-recipe from recipes/{r.name}/recipe.env.",
             "# Edit the recipe, not this file: beast-recipe up --write-profile renders it again.",
             f"BACKEND={backend}", f"SOURCE={source}"]
    if r.get("REVISION") and not local:
        lines.append(f"REVISION={r.get('REVISION')}")
    lines += [f"SERVED_MODEL_NAME={r.get('SERVED_NAME')}", f"TENSOR_PARALLEL_SIZE={p.get('n') or r.get('SPARKS', '2')}"]
    if r.get("CONTEXT"):
        lines.append(f"MAX_MODEL_LEN={r.get('CONTEXT')}")
    kind, drepo, drev = drafter_spec(r)
    if backend == "tensorfold":
        lines.append(f"TENSORFOLD_PARALLEL={r.get('PARALLEL') or 'auto'}")
        if kind == "repo":
            lines += [f"DRAFTER_SOURCE={drepo}", f"DRAFTER_REVISION={drev}"]
        # D2's tensorfold launcher is native TensorFold (every d2-tensorfold engine is tensorfold-native).
        # Its --kv-cache takes bf16 (the default) or fp8; --kv-dtype is the 0.x Python flag, refused by
        # native. spark-node.sh passes TENSORFOLD_KV_CACHE as --kv-cache, TENSORFOLD_MEMORY_RESERVE_GIB as
        # that environment variable on every rank, and refuses a binary other than TENSORFOLD_REQUIRES.
        # KV_POOL_GIB has no native flag: the engine sizes its KV pages to the room it finds.
        kvd = r.get("KV_DTYPE")
        if kvd == "fp8":
            lines.append("TENSORFOLD_KV_CACHE=fp8")
        elif kvd not in ("", "auto", "bf16"):
            raise RecipeError(f"KV_DTYPE={kvd}: native TensorFold's --kv-cache takes bf16 or fp8")
        if r.get("MEMORY_RESERVE_GIB"):
            lines.append(f"TENSORFOLD_MEMORY_RESERVE_GIB={r.get('MEMORY_RESERVE_GIB')}")
        v = (p.get("version") or "")
        if re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", v):
            lines.append(f"TENSORFOLD_REQUIRES={v}")
    else:
        for k, pk in (("TOOL_PARSER", "TOOL_CALL_PARSER"), ("REASONING_PARSER", "REASONING_PARSER"),
                      ("GPU_MEM_UTIL", "GPU_MEMORY_UTILIZATION"), ("MAX_NUM_SEQS", "MAX_NUM_SEQS")):
            if r.get(k):
                lines.append(f"{pk}={r.get(k)}")
        if r.get("SPEC_CONFIG") and r.get("SPEC_CONFIG") != "off":
            lines.append(f"SPECULATIVE_CONFIG={r.get('SPEC_CONFIG')}")
    text = "\n".join(lines) + "\n"
    prof = obprofile.Profile(name=profile_name(r), path=BACKENDS / "models" / f"{profile_name(r)}.env",
                             values=obprofile.parse_text(text, "rendered profile"))
    try:
        obprofile.validate(prof, backend)
    except obprofile.ProfileError as e:
        raise RecipeError(f"the rendered profile does not validate: {e}") from None
    return text


def render_unit(r: Recipe, p: dict) -> str:
    launcher = launcher_of(r, p)
    engine = "vllm" if launcher == "d2-vllm" else "tensorfold"
    prof = profile_name(r)
    if not p.get("n"):
        raise RecipeError(f"{r.name}: the plan has no rank count ({p.get('verdict')}): no unit to render")
    conflicts = " ".join(sorted(KNOWN_UNITS.values()))
    cl = _cl(r, p.get("n"), "%h/openbeast/")
    return f"""# {unit_of(r, launcher)}: rendered by beast-recipe from recipes/{r.name}/recipe.env.
# {r.get('TITLE') or r.name}
# STAGED, NOT A BOOT DEFAULT: there is no [Install] section, so it cannot be
# enabled and never starts at boot. Start it only through `beast-spark {r.name}`
# (after `beast-recipe adopt {r.name} --run`): it stops the model that serves,
# starts this, waits for {r.get('SERVED_NAME')} on /v1/models, sets openbeast.conf,
# and puts the previous model back if any of that fails.

[Unit]
Description=beast-recipe {r.name}: {r.get('SERVED_NAME')} on {p['engine']}, TP {p.get('n')} (staged; started only by beast-spark {r.name})
After=network-online.target
Wants=network-online.target
Conflicts={conflicts}
After={conflicts}

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=%h/openbeast
ExecStart=/usr/bin/bash %h/openbeast/scripts/backends/spark-cluster.sh start{cl} --profile {prof} --engine {engine}
ExecStop=/usr/bin/bash %h/openbeast/scripts/backends/spark-cluster.sh stop{cl} --engine {engine}
TimeoutStartSec=infinity
TimeoutStopSec=150
Restart=no
"""


def up_steps(r: Recipe, p: dict, ns: list[str]) -> list[tuple[str, str]]:
    launcher = launcher_of(r, p)
    unit = unit_of(r, launcher)
    if launcher.startswith("d2-"):
        prof = profile_name(r)
        cl = _cl(r, p.get("n"))
        return [(ns[0], f"scripts/backends/spark-cluster.sh check{cl}   # {len(ns)} node(s), fabric, same checkout"),
                (ns[0], (f"scripts/backends/spark-cluster.sh start{cl} --profile {prof} --engine "
                        f"{'vllm' if launcher == 'd2-vllm' else 'tensorfold'} --dry-run   # the exact rank commands")),
                (ns[0], (f"install -m 0644 {unit_src(r, unit)} ~/.config/systemd/user/{unit} && systemctl --user daemon-reload"
                         "   # installs only: no [Install], enables and starts nothing")),
                (ns[0], f"systemctl --user start {unit}   # only when nothing serves; on the pair: adopt, then beast-spark {r.name}")]
    if launcher == "hy4-vllm":
        return [(ns[0], "scripts/backends/hy4-vllm/start.sh --print   # both docker commands (golden-tested against this recipe)"),
                (ns[0], "scripts/backends/spark-hy4-stage.sh install   # the runtime copy + the unit; enables nothing"),
                (ns[0], f"systemctl --user start {unit}   # through beast-spark hy4")]
    if launcher.startswith("mia-unit:"):
        m = mia_unit_mismatch(r, p, unit)
        if m:
            return [("-", f"no unit for TP{p.get('n') or '-'} with this pack yet: {m} (gap: launcher)")]
        short = {v: k for k, v in KNOWN_UNITS.items()}.get(unit, "")
        return [(ns[0], f"systemctl --user start {unit}   # Mia's recipe unit" + (f"; through beast-spark {short}" if short else ""))]
    return [("-", f"no launcher for {p['engine']} (gap: launcher)")]


def unit_src(r: Recipe, unit: str) -> str:
    """The rendered unit's path for a command run from the repo (relative when the recipe is in it)."""
    f = r.dir / unit
    try:
        return str(f.relative_to(REPO))
    except ValueError:
        return shlex.quote(str(f))


def check_steps(r: Recipe, p: dict, url: str, key_file: str) -> list[str]:
    backend = "vllm" if p["kind"] == "vllm" else "tensorfold"
    return [(f"scripts/backends/conformance.sh --url {url} --model {q(r.get('SERVED_NAME'))} --backend {backend} "
            f"--key-file {key_file} --concurrency 4"),
            (f"beast-recipe check {r.name} --run --bench   # decode tok/s at 1 stream (median of 3) and 4 streams "
            f"aggregate, same probe for every recipe (scripts/tokps.py one_run)")]


def adopt_steps(r: Recipe, p: dict) -> list[str]:
    launcher = launcher_of(r, p)
    if launcher == "hy4-vllm":
        return ["already selectable: beast-spark hy4 (the hy4-vllm kit's unit)"]
    if launcher.startswith("mia-unit:"):
        m = mia_unit_mismatch(r, p, unit_of(r, launcher))
        if m:
            return [f"deferred: {m} (gap: launcher)"]
        short = {v: k for k, v in KNOWN_UNITS.items()}.get(unit_of(r, launcher), "")
        return [f"already selectable: beast-spark {short} (Mia's recipe unit)" if short else
                "deferred: a new Mia-recipe unit is not wired (gap: launcher)"]
    return [(f"beast-recipe adopt {r.name} --run   # installs the unit (enables nothing) and writes "
             f"~/.config/openbeast/recipes.d/{r.name}.env (beast-spark's registry)"),
            (f"beast-spark {r.name}   # the switch: stops the model that serves, starts {unit_of(r, launcher)}, waits for "
            f"{r.get('SERVED_NAME')}, INFERENCE_BACKEND/INFERENCE_MODEL in openbeast.conf, restarts the command center")]


def licence_flags(lic: str) -> tuple[bool, bool]:
    """(needs a licence review, non-commercial or no-derivatives): token-anchored, so 'nc' inside
    'licence' or 'nd' inside 'and' never matches. One classifier for init, plan and stage."""
    low = (lic or "").lower()
    nc = bool(re.search(r"(?<![a-z])n[cd](?![a-z])|non-?commercial", low))
    review = nc or bool(re.search(r"(?<![a-z])(other|custom|unknown)(?![a-z])", low)) or not low
    return review, nc


def plan(r: Recipe, data: dict, sparks: int | None = None, counts: list[int] | None = None,
         cluster_file: Path | None = None) -> dict:
    sparks = sparks or int(r.num("SPARKS") or 2)
    c = choose(r, data, sparks)
    p = c["chosen"]
    counts = sorted(set(counts or {1, 2, 3, 4, sparks}) | ({p["n"]} if p.get("n") else set()))
    eng = engine_by_id(data, p["engine"])
    ns, ns_from = nodes(p.get("n") or sparks, cluster_file)
    total, total_how = weights_gib(r, data)
    gaps = list(_row_gaps(p))
    launcher = launcher_of(r, p)
    # always-present steps and caveats
    if p["verdict"] in ("SERVABLE-NOW", "SERVABLE-WITH-WORK", "NEEDS-MORE-HARDWARE"):
        ids = {g["id"] for g in gaps}
        if launcher.startswith("d2-") and "d2-first-serve" not in ids:
            if (p.get("n") or sparks) == 1:
                gaps.append(_gap(data, "d2-first-serve", {
                    "title": "D2 (spark-cluster.sh + spark-node.sh) has never served on one Spark: the first native serve qualifies it",
                    "effort": "one model-down window of 0.5-1 day on that Spark (on the pair, the pair's model is down too)"}))
            else:
                gaps.append(_gap(data, "d2-first-serve"))
        for gid in ("glue", "tuning"):
            if gid not in ids:
                gaps.append(_gap(data, gid))
        if (p.get("n") or 0) > 2:
            gaps.append(_gap(data, "topology"))
    if not any(x["reads"] for x in c["paths"]):
        # no engine in the matrix reads this model_type: the work is a new family in an engine
        gaps.append(_gap(data, "family", {"note": f"model_type {r.get('MODEL_TYPE') or '?'}: no engine has a family for it"}))
    lic = r.get("LICENCES")
    if licence_flags(lic)[0]:
        gaps.append(_gap(data, "licence-review", {"note": lic or "no LICENCES line"}))
    seen, uniq = set(), []
    for g in gaps:
        if g["id"] not in seen:
            seen.add(g["id"])
            uniq.append(g)
    lo, hi = _days(uniq)
    better = None
    if c["pinned"]:
        top = c["ranked"][0]
        if VERDICTS.index(top["verdict"]) < VERDICTS.index(p["verdict"]):
            better = {"engine": top["engine"], "verdict": top["verdict"], "n": top.get("n")}
    fit = {n: fit_row(r, data, p.get("launch") or {}, n, "vllm" if p["kind"] == "vllm" else "tensorfold")
           for n in counts}
    native = p["kind"] == "tensorfold-native" and launcher.startswith("d2-")
    for n in counts:
        if native and fit[n].get("kv_from"):
            fit[n]["kv_from"] += "; engine-sized, not capped"
    for n in counts:
        x = p["rows"].get(n) or {}
        fit[n]["engine_allows"] = ("no" if not x.get("allowed") else "yes" if x.get("how") == "serves"
                                   else "with work")
    projections = []
    if r.get("BPW") and r.get("FORMAT") == "exl3":
        for b in (3.0, 4.0, 5.0, 6.0):
            g, how = project_bytes(r, data, b)
            if g:
                share = data["hardware"]["spark"]["replicated_share"]
                projections.append({"bpw": b, "checkpoint_gib": round(g, 1), "how": how,
                                    "per_rank": {n: round(per_rank_gib(g, n, share) if n > 1 else g, 1) for n in counts}})
    unverified = (eng or {}).get("unverified_on_pair", "")
    if (p.get("n") or sparks) == 1 and unverified:
        unverified = unverified.replace("D2 has never served on the pair", "D2 has never served on one Spark")
    caveats = []
    cn = p.get("n")
    if native and cn and fit.get(cn, {}).get("budget") == "tight":
        caveats.append(f"tight budget ({fit[cn]['headroom_gib']} GiB of headroom) that nothing enforces: native "
                       "TensorFold sizes its KV pages itself, so the first start line and MemAvailable on every rank "
                       "decide (step: tuning)")
    if native and p.get("version"):
        caveats.append(f"one tensorfold per Spark: the profile pins TENSORFOLD_REQUIRES={p['version']}, and spark-node.sh "
                       "refuses another binary. A recipe on another release needs scripts/engine.sh install/rollback "
                       f"tensorfold on every Spark (and TENSORFOLD_VERSION={p['version']} in spark.env) before its switch")
    k, drepo, drev = drafter_spec(r)
    drafters = list((data.get("drafters") or {}).get(r.get("MODEL_TYPE"), []))
    steps = {
        "engine": [f"[{n}] {cmd}" for n, cmd in (engine_steps(r, p, eng, ns) if eng else [])],
        "stage": [f"[{n}] ({s}) {cmd}" for s, n, cmd in stage_steps(r, p, eng or {}, ns)],
        "up": [f"[{n}] {cmd}" for n, cmd in up_steps(r, p, ns)],
        "check": check_steps(r, p, f"http://127.0.0.1:{r.get('PORT', '8000')}", DEFAULT_KEY_FILE),
        "adopt": adopt_steps(r, p),
        "rollback": ["beast-recipe rollback --run   # beast-spark <the previous selection> (ds, glm, hy4 or a recipe)"],
    }
    return {
        "recipe": r.name, "path": str(r.path), "title": r.get("TITLE"), "sparks": sparks,
        "model": {"source": r.get("SOURCE"), "revision": r.get("REVISION"), "status": r.get("WEIGHTS_STATUS", "published"),
                  "architecture": r.get("ARCHITECTURE"), "model_type": r.get("MODEL_TYPE"), "format": r.get("FORMAT"),
                  "bpw": r.get("BPW"), "codebook": r.get("EXL3_CODEBOOK"), "scope": r.get("EXL3_SCOPE"),
                  "checkpoint_gib": round(total, 1) if total else None, "size_from": total_how,
                  "heads": r.get("ATTENTION_HEADS"), "kv_heads": r.get("KV_HEADS"), "experts": r.get("EXPERTS"),
                  "active": r.get("EXPERTS_ACTIVE"), "licences": lic},
        "verdict": p["verdict"], "verdict_meaning": VERDICT_MEANING[p["verdict"]],
        "engine": {"id": p["engine"], "title": p["title"], "status": p["status"], "pinned": c["pinned"],
                   "launcher": launcher, "n": p.get("n"), "rank_reason": p.get("rank_reason"),
                   "note": p.get("note") or "", "unverified": unverified, "caveats": caveats,
                   "version": p.get("version") or "",
                   "why": p.get("why") or "", "launch_note": p.get("launch_note") or "", "reads": p["reads"]},
        "better_path": better,
        "hardware_why": hardware_why(r, data, p, sparks) if p["verdict"] == "NEEDS-MORE-HARDWARE" else [],
        "alternatives": [{"engine": x["engine"], "status": x["status"], "reads": x["reads"], "verdict": x["verdict"],
                          "n": x.get("n"), "why": x["why"],
                          "gaps": [g["id"] for g in _row_gaps(x) if g.get("blocking")]} for x in c["ranked"]],
        "fit": fit, "projections": projections,
        "parallelism": {"tp": p.get("n"), "ep": r.flag("EXPERT_PARALLEL"), "pp": 1,
                        "allowed": [n for n, x in p["rows"].items() if x.get("allowed")]},
        "drafter": {"chosen": r.get("DRAFTER", "none"), "kind": k, "repo": drepo, "revision": drev,
                    "options": drafters},
        "kv": {"dtype": r.get("KV_DTYPE") or (p.get("launch") or {}).get("kv_dtype") or "auto",
               "pool_gib": fit.get(p.get("n") or sparks, {}).get("kv_gib"),
               "source": (p.get("launch") or {}).get("source", ""),
               "passed": kv_passed(r, launcher)},
        "gaps": uniq, "effort_days": [round(lo, 1), round(hi, 1)],
        "nodes": ns, "nodes_from": ns_from, "steps": steps,
        "baseline": {"from": r.get("BASELINE_FROM"), "toks_1": r.num("BASELINE_TOKS_1"), "toks_4": r.num("BASELINE_TOKS_4")},
    }


def kv_passed(r: Recipe, launcher: str) -> str:
    """What of the KV / reserve budget actually reaches the engine at launch."""
    if launcher.startswith("d2-") and launcher != "d2-vllm":
        kvd = r.get("KV_DTYPE")
        res = r.get("MEMORY_RESERVE_GIB")
        return ("--kv-cache fp8 is passed (TENSORFOLD_KV_CACHE)" if kvd == "fp8" else
                "no --kv-cache: native TensorFold's default bf16 cache") + \
            (f"; TENSORFOLD_MEMORY_RESERVE_GIB={res} is set on every rank" if res else
             "; no reserve set: the engine's default (a tenth of the pool, at least 4 GiB)") + \
            "; the KV pool is engine-sized, not capped (native has no pool flag: it sizes its KV pages to the room it finds)"
    if launcher in ("hy4-vllm", "d2-vllm"):
        return "vLLM: --kv-cache-memory-bytes / --gpu-memory-utilization are the launch's; the reserve is a budget assumption"
    return "the launcher's own values (the plan budgets the recipe's)"


def render_plan(pl: dict) -> str:
    L: list[str] = []
    add = L.append
    m, e = pl["model"], pl["engine"]
    add(f"beast-recipe plan {pl['recipe']}   ({pl['title']})")
    add(f"  model     {m['source'] or '(none yet)'}" + (f"@{m['revision'][:8]}" if m["revision"] else "")
        + f"   {m['architecture']} / {m['model_type']}" + ("   [to quantize]" if m["status"] == "to-quantize" else ""))
    fmt = m["format"] + (f" {m['bpw']} bpw" if m["bpw"] else "") + (f", codebook {m['codebook']}" if m["codebook"] else "") \
        + (f", scope {m['scope']}" if m["scope"] else "")
    add(f"  format    {fmt}   {m['checkpoint_gib']} GiB resident ({m['size_from']})")
    if m["experts"]:
        add(f"  moe       {m['experts']} experts, {m['active']} active; {m['heads']} attention heads"
            + (f", {m['kv_heads']} KV heads" if m["kv_heads"] else ""))
    add(f"  target    {pl['sparks']} Spark{'s' if pl['sparks'] > 1 else ''}   engine {e['id']}"
        + (" (pinned)" if e["pinned"] else " (auto)") + f"   launcher {e['launcher']}")
    add("")
    add(f"VERDICT   {pl['verdict']}" + (f" at TP{e['n']}" if e["n"] else ""))
    add(f"          {pl['verdict_meaning']}")
    if e["why"]:
        add(f"          why: {e['why']}")
    if pl["better_path"]:
        b = pl["better_path"]
        add(f"          note: a better path exists: {b['engine']} is {b['verdict']}" + (f" at TP{b['n']}" if b["n"] else ""))
    for w in pl["hardware_why"]:
        add(f"          - {w}")
    if e["note"]:
        add(f"          upstream: {e['note']}")
    if e["unverified"]:
        add(f"          caveat: {e['unverified']}")
    for c in e.get("caveats") or []:
        add(f"          caveat: {c}")
    if e.get("launch_note"):
        add(f"          launch: {e['launch_note']}")
    if pl.get("retarget_note"):
        add(f"          {pl['retarget_note']}")
    add("")
    add("Fit per Spark  (W = checkpoint / TP x (1 + 0.008 (TP-1)); rule: W <= 92 fits, <= 100 tight; "
        "budget: W + KV + reserve + OS within 121.7 GiB, >= 6 GiB headroom; what of KV and reserve reaches the engine: the KV line below)")
    add("  Sparks  W/rank  rule          W + KV + reserve + OS = used    headroom  engine allows")
    for n, x in sorted(pl["fit"].items()):
        if x.get("w_gib") is None:
            add(f"  {n:<6}  ?       unknown")
            continue
        add(f"  {n:<6}  {x['w_gib']:<6}  {x['rule']:<12}  {x['w_gib']} + {x['kv_gib']} + {x['reserve_gib']} + "
            f"{x['os_gib']} = {x['used_gib']:<6}  {x['headroom_gib']:<8}  {x['engine_allows']}"
            + (f"   ({x['why']})" if x.get("why") else ""))
    if any("engine-sized" in (x.get("kv_from") or "") for x in pl["fit"].values()):
        add("  KV: engine-sized, not capped (native TensorFold has no KV-pool flag; the reserve is passed as "
            "TENSORFOLD_MEMORY_RESERVE_GIB)")
    if pl["projections"]:
        add("  other bpw (projected checkpoint GiB -> per rank):")
        for pr in pl["projections"]:
            add(f"    {pr['bpw']} bpw: {pr['checkpoint_gib']} GiB -> " +
                ", ".join(f"{n}: {v}" for n, v in pr["per_rank"].items()) + f"   ({pr['how']})")
    add("")
    add("Engines  (who reads it, at which sizes; best first)")
    for a in pl["alternatives"]:
        mark = "*" if a["engine"] == e["id"] else " "
        if not a["reads"]:
            add(f"  {mark} {a['engine']:<18} {a['status']:<13} no: {a['why']}")
        else:
            add(f"  {mark} {a['engine']:<18} {a['status']:<13} {a['verdict']}" + (f" at TP{a['n']}" if a["n"] else "")
                + (f"; work: {', '.join(a['gaps'])}" if a["gaps"] else "")
                + (f": {a['why']}" if a["verdict"] == "BLOCKED" and a["why"] else ""))
    if e["rank_reason"]:
        add(f"  ranks: {e['rank_reason']}")
    add("")
    par = pl["parallelism"]
    add(f"Parallelism  TP{par['tp'] or '-'}" + (" + expert parallel" if par["ep"] else "") + ", PP 1, one rank per Spark; "
        f"this engine runs it at {', '.join(map(str, par['allowed'])) or 'no size'}")
    d = pl["drafter"]
    add(f"Drafter      {d['chosen']}")
    for o in d["options"]:
        add(f"             option: {o['kind']}" + (f" {o['repo']}@{o['revision'][:8]}" if o.get("repo") else "")
            + (f" ({o['licence']})" if o.get("licence") else "") + f": {o.get('note', '')}")
    add(f"KV           {pl['kv']['dtype']}, {pl['kv']['pool_gib'] if pl['kv']['pool_gib'] is not None else '?'} GiB a rank   "
        f"({pl['kv']['source']})")
    add(f"             {pl['kv']['passed']}")
    add(f"Licences     {pl['model']['licences'] or 'unknown: read the model card'}")
    add(f"Nodes        {' '.join(pl['nodes'])}   ({pl['nodes_from']})")
    if pl["baseline"]["from"]:
        b = pl["baseline"]
        add(f"Baseline     {b['from']}: {b['toks_1']} tok/s at 1 stream, {b['toks_4']} aggregate at 4 (beat both; "
            "re-measure the baseline with `beast-recipe check --bench` so both use one probe)")
    add("")
    add(f"Gaps  (blocking work: {pl['effort_days'][0]}-{pl['effort_days'][1]} engineering days, ESTIMATED)")
    for g in sorted(pl["gaps"], key=lambda g: (not g["blocking"], g.get("rank", 99))):
        add(f"  {'WORK' if g['blocking'] else 'step'}  [{g['id']}] {g['title']}")
        add(f"        effort: {g['effort']}; who: {g['who']}" + (f"; {g['note']}" if g.get("note") else ""))
    add("")
    if pl["verdict"] == "BLOCKED":
        add("BLOCKED: these steps will not serve it until the gaps above close (docs/BEAST_RECIPE.md "
            "'When the verdict is BLOCKED')")
    add("End to end  (each command prints what it would do; add --run to act)")
    for phase in ("engine", "stage", "up", "check", "adopt", "rollback"):
        add(f"  {phase}:  beast-recipe {phase} {pl['recipe'] if phase != 'rollback' else ''}".rstrip())
        for s in pl["steps"][phase]:
            add(f"      {s}")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- init

def _front_matter(text: str | None) -> dict:
    out: dict[str, str] = {}
    if not text or not text.startswith("---"):
        return out
    for line in text.split("\n---", 1)[0].splitlines()[1:]:
        m = re.match(r"^([a-z_]+):\s*(\S.*)?$", line)
        if m and m.group(2):
            out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


def _bpw(fmt: str, q: dict, detail: dict) -> tuple[float | None, str]:
    if fmt == "exl3":
        b = (detail.get("exl3") or {}).get("bits") or q.get("bits")
        return (float(b), "READ: quantization_config.bits") if b else (None, "")
    if fmt == "modelopt-nvfp4":
        return 4.5, "ESTIMATED: NVFP4 = 4 bits + an FP8 scale per 16 values"
    if fmt.startswith(("modelopt-fp8", "fp8")):
        return 8.0, "READ: FP8"
    if fmt == "bf16":
        return 16.0, "READ: unquantized"
    if fmt.startswith("mlx-q"):
        m = re.match(r"mlx-q(\d+)", fmt)
        return (float(m.group(1)), "READ: MLX bits") if m else (None, "")
    return None, ""


def resolve_source(spec: str) -> str:
    """hf URL | org/repo[@rev] | local dir -> what model_inspect.open_source takes."""
    p = Path(os.path.expanduser(spec))
    if p.is_dir():
        return str(p)
    if "://" in spec or spec.split("/", 1)[0].lower() in ("huggingface.co", "hf.co", "www.huggingface.co"):
        import weights_download
        try:
            u = weights_download.parse_url(spec)
        except weights_download.DownloadError as e:
            raise RecipeError(str(e)) from None
        if u.get("subdir") or u.get("file"):
            raise RecipeError(f"{spec}: a recipe takes a whole repo, not a sub-folder or file")
        return u["repo"] + (f"@{u['revision']}" if u.get("revision") else "")
    return spec


def detect_drafter(cfg: dict, tc: dict, data: dict, mt: str, files: list[str] | None = None) -> tuple[str, str, list[str]]:
    """(DRAFTER value, provenance, notes)."""
    keys = set(cfg) | set(tc)
    notes = []
    if any(f.startswith("dspark/") and f.endswith((".safetensors", "config.json")) for f in (files or [])):
        notes.append("DSpark draft in the checkpoint's dspark/ folder")
        return "in-checkpoint:dspark", "READ: dspark/ folder", notes
    if any("dfly" in k for k in keys):
        n = tc.get("dfly_num_hidden_layers") or cfg.get("dfly_num_hidden_layers")
        notes.append(f"DFly drafter in the checkpoint (dfly_* config{f', {n} layers' if n else ''})")
        return "in-checkpoint:dfly", "READ: config.json dfly_* keys", notes
    if any("dspark" in k for k in keys):
        notes.append("DSpark draft blocks in the checkpoint (dspark_* config)")
        return "in-checkpoint:dspark", "READ: config.json dspark_* keys", notes
    pub = [d for d in (data.get("drafters") or {}).get(mt, []) if d.get("kind") == "dflash2"]
    mtp = int(tc.get("num_nextn_predict_layers") or cfg.get("num_nextn_predict_layers") or 0)
    if mtp:
        notes.append(f"{mtp} MTP layer(s) in the checkpoint (num_nextn_predict_layers)")
    if pub and pub[0].get("licence") in ("apache-2.0", "mit"):
        d = pub[0]
        return f"{d['repo']}@{d['revision']}", f"DATA: published DFlash2, {d['licence']}", notes
    if mtp:
        return "mtp", "READ: num_nextn_predict_layers", notes
    return "none", "DEFAULT: no drafter found; serve plain first, then measure", notes


def init(spec: str, name: str | None, sparks: int, engine: str, data: dict, force: bool,
         accept_nc: bool = False) -> tuple[Path, Recipe, list[str]]:
    import model_inspect
    target = resolve_source(spec)
    src = model_inspect.open_source(target)
    rep = model_inspect.inspect(src)
    cfg = model_inspect._load_json(src, "config.json")
    tc = model_inspect._text_cfg(cfg)
    import model_intake
    if src.local:
        qcfg = model_intake.quant_info(src.local, cfg)
    else:
        qcfg = next((dict(s["quantization_config"]) for s in (cfg, cfg.get("text_config") or {})
                     if isinstance(s.get("quantization_config"), dict)), {})
        if qcfg.get("bits") is None and qcfg.get("avg_bits") is not None:
            qcfg["bits"] = qcfg["avg_bits"]
    q = rep["quantization"]
    w = rep["weights"]
    fmt = "gguf" if (w["gguf_files"] and not w["safetensors_files"]) else model_intake.quant_format(qcfg or {}, q["scheme"])
    if fmt == "modelopt-fp8" and "NVFP4" in q["scheme"]:
        fmt = "modelopt-nvfp4"
    bpw, bpw_how = _bpw(fmt, qcfg or {}, q.get("detail") or {})
    exl3 = (q.get("detail") or {}).get("exl3") or {}
    readme = src.read("README.md") if any(f.path == "README.md" for f in src.files) else None
    fm = _front_matter(readme)
    lic = fm.get("license") or "unknown"
    if lic == "other" and fm.get("license_name"):
        lic = f"other ({fm['license_name']})"
    warnings = list(rep["warnings"])
    if licence_flags(lic)[1] and not accept_nc:
        warnings.append(f"licence {lic}: non-commercial or no-derivatives; stage refuses it without --accept-nc")
    repo = src.repo or ""
    base = re.sub(r"[^a-z0-9._-]+", "-", (repo.split("/")[-1] if repo else Path(str(src.local)).name).lower()).strip("-")
    name = name or f"{base}-{sparks}x"
    if not NAME_RE.match(name):
        raise RecipeError(f"--name {name!r}: lowercase letters, digits, . _ -")
    if name in RESERVED_NAMES:
        raise RecipeError(f"--name {name!r} is a beast-spark command: pick another name")
    d = recipes_dir() / name
    if (d / "recipe.env").exists() and not force:
        raise RecipeError(f"{d}/recipe.env exists (--force replaces it)")
    kv = rep["kv_cache"]
    moe = rep["moe"] or {}
    drafter, dprov, dnotes = detect_drafter(cfg, tc, data, rep["model_type"], [f.path for f in src.files])
    tools = next((s["parser"] for s in rep["suggestions"]["tool_call_parser"] if s["parser"]), "")
    reas = next((s["parser"] for s in rep["suggestions"]["reasoning_parser"] if s["parser"]), "")
    served = re.sub(r"[^A-Za-z0-9._-]+", "-", repo.split("/")[-1] if repo else name).strip("-") or name
    src_label = f"{repo}@{src.revision}" if repo else str(src.local)
    fields: list[tuple] = [
        ("#", "identity"),
        ("TITLE", _clean(f"{served} on {sparks} Spark{'s' if sparks > 1 else ''}"), "DEFAULT", "edit me"),
        ("SOURCE", repo or str(src.local), "HUB" if repo else "READ", "the model given to init"),
        ("REVISION", src.revision or "", "HUB" if repo else "", "resolved from main: pin THIS" if not src.pinned_by_user else "pinned"),
        ("WEIGHTS_STATUS", "published" if repo else "local", "DEFAULT", ""),
        ("WEIGHTS_DIR", (f"~/Documents/weights/{repo}" if repo else str(src.local)), "DEFAULT",
         "the same path on every Spark (a D2 launcher fetches into MODELS_DIR/<PROFILE> instead)"),
        ("LICENCES", _clean(f"weights={lic}"), "READ" if fm.get("license") else "VERIFY", "README.md front matter"),
        ("#", "what the checkpoint is (read by model_inspect.py)"),
        ("ARCHITECTURE", (rep["architectures"] or [""])[0], "READ", "config.json architectures"),
        ("MODEL_TYPE", rep["model_type"], "READ", "config.json model_type"),
        ("FORMAT", fmt, "READ", _clean(q["scheme"])),
        ("BPW", "" if bpw is None else (str(int(bpw)) if float(bpw).is_integer() else str(bpw)), bpw_how.split(":")[0],
         bpw_how.split(":", 1)[-1].strip()),
        ("EXL3_CODEBOOK", exl3.get("codebook") or "", "READ", "quantization_config.codebook"),
        ("EXL3_SCOPE", exl3.get("scope") or "", "READ", "quantization_config.scope"),
        ("WEIGHT_BYTES", str(w["weight_bytes"] or ""), "READ", f"{w['safetensors_files']} safetensors files"),
        ("LAYERS", str(tc.get("num_hidden_layers") or ""), "READ", "config.json"),
        ("ATTENTION_HEADS", str(kv.get("attention_heads") or tc.get("num_attention_heads") or ""), "READ", "config.json"),
        ("KV_HEADS", str(kv.get("kv_heads") or ""), "READ", "config.json"),
        ("KV_BYTES_PER_TOKEN", str(kv.get("bytes") or ""), "ESTIMATED", _clean(kv.get("why") or "")),
        ("NATIVE_CONTEXT", str(rep["context"].get("native") or ""), "READ", "config.json max_position_embeddings"),
        ("EXPERTS", str(moe.get("experts") or ""), "READ", "config.json"),
        ("EXPERTS_ACTIVE", str(moe.get("active_per_token") or ""), "READ", "config.json"),
        ("#", "serving choices (plan checks them; VERIFY on the first start)"),
        ("SPARKS", str(sparks), "USER", "--sparks"),
        ("ENGINE", engine, "USER" if engine != "auto" else "DEFAULT", "--engine" if engine != "auto" else "auto: plan picks the best path"),
        ("SERVED_NAME", served, "DEFAULT", "from the repo name"),
        ("PORT", "8000", "DEFAULT", "rank 0 on loopback; beast-gate publishes it"),
        ("CONTEXT", str(min(int(rep["context"].get("native") or 131072), 262144)), "VERIFY", "the start line of the engine says what it admits"),
        ("PARALLEL", "4", "DEFAULT", "= --jobs 4 --slots 4 of a campaign arm"),
        ("DRAFTER", drafter, dprov.split(":")[0], dprov.split(":", 1)[-1].strip()),
        ("TOOL_PARSER", tools or "", "ESTIMATED", "model_inspect suggestion from the template; conformance decides"),
        ("REASONING_PARSER", reas or "", "ESTIMATED", "model_inspect suggestion; conformance decides"),
    ]
    header = [f"beast-recipe {name}: written by beast-recipe init {_clean(spec)} on {_dt.date.today().isoformat()}.",
              f"From {_clean(src_label)}. Every value says where it came from (READ: the files of the checkpoint;",
              "HUB: the Hub API; DATA: data/recipe-engines.json; ESTIMATED; DEFAULT; VERIFY: check on hardware).",
              "Parsed, never sourced. docs/BEAST_RECIPE.md"]
    text = render_recipe(header, fields)
    d.mkdir(parents=True, exist_ok=True)
    (d / "recipe.env").write_text(text)
    sums = [f"{f.sha256}  {f.path}" for f in src.files if f.sha256 and re.match(r"^[0-9a-f]{64}$", f.sha256)
            and not UNSAFE.search(f.path) and ".." not in f.path.split("/")]
    if sums:
        (d / "SHA256SUMS").write_text(f"# {repo}@{src.revision}: the Hub's LFS sha256, read by beast-recipe init\n"
                                      + "\n".join(sorted(sums, key=lambda s: s.split("  ", 1)[1])) + "\n")
    r = parse_recipe(text, (d / "recipe.env").resolve())
    warnings += [f"drafter: {n}" for n in dnotes]
    if fmt == "gguf":
        warnings.append("GGUF only: no Spark engine here reads GGUF (llama.cpp serves it on the rig)")
    return d, r, warnings


# --------------------------------------------------------------------------- acting

class Runner:
    """Runs (node, command) pairs: `local` with bash, anything else over ssh (BatchMode). In dry-run it
    only prints. Tests put recording stubs for ssh, docker, systemctl first on PATH."""

    def __init__(self, run: bool, out=None):
        self.run = run
        self.out = out or sys.stdout
        self.log: list[tuple[str, str]] = []

    def sh(self, node: str, cmd: str, capture: bool = False, stdin: str | None = None) -> tuple[int, str]:
        self.log.append((node, cmd))
        if not self.run:
            print(f"  [{node}] {cmd}", file=self.out)
            return 0, ""
        if node.startswith("<") or node in ("-", "connected box"):
            print(f"  [{node}] SKIPPED (not runnable here): {cmd}", file=self.out)
            return 0, ""
        argv = ["bash", "-c", cmd] if node == "local" else \
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", node, cmd]
        print(f"  [{node}] {cmd}", file=self.out)
        p = subprocess.run(argv, input=stdin, text=True, capture_output=capture, cwd=str(REPO))
        return p.returncode, (p.stdout or "") if capture else ""


def workers_commit_guard(runner: Runner, nodes: list[str], force: bool, what: str) -> None:
    """Every worker's ~/openbeast must be on the head's commit before a --run step relies on it (worker
    steps run `cd ~/openbeast && ...`: the recipe files, profiles and locks are read from that checkout,
    as spark-cluster.sh check requires). A worker on another commit, or one whose commit cannot be read,
    stops the run before any step; --force overrides."""
    workers = [n for n in nodes if n != "local" and not n.startswith("<") and n not in ("-", "connected box")]
    if not workers or not runner.run:
        return
    p = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True)
    head = (p.stdout or "").strip()
    bad = []
    for n in workers:
        _code, out = runner.sh(n, "git -C ~/openbeast rev-parse HEAD", capture=True)
        got = (out or "").strip().splitlines()[-1:] or [""]
        if not head or got[0] != head:
            bad.append(f"{n} is on {got[0][:12] or 'an unreadable commit'}, the head on {head[:12] or '?'}")
    if bad and not force:
        raise RecipeError(f"{what} --run: the workers' checkouts differ from the head's: " + "; ".join(bad)
                          + ". Pull them first (ssh <worker> git -C ~/openbeast pull --ff-only), the head too; "
                            "--force overrides")
    for b in bad:
        print(f"  WARNING (--force): {b}")


def state_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("BEAST_RECIPE_STATE_DIR") or "~/.config/openbeast/recipes.d"))


BUSY_STATES = ("active", "activating", "reloading", "deactivating")


def any_inference_active(runner: Runner) -> list[str]:
    """Every inference unit that is active or on its way (activating counts: a starting model already
    holds memory), adopted or not: the known units, the registry's, and any beast-recipe-*.service
    systemd lists; plus a D2 container rank (openbeast-*-rank*) left running outside a unit."""
    units = list(KNOWN_UNITS.values())
    d = state_dir()
    if d.is_dir():
        for f in sorted(d.glob("*.env")):
            m = re.search(r"^UNIT=(beast-recipe-[A-Za-z0-9._-]+\.service)\s*$", f.read_text(), re.MULTILINE)
            if m:
                units.append(m.group(1))
    active = []
    try:
        lu = subprocess.run(["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend",
                             "beast-recipe-*.service"], capture_output=True, text=True, check=False).stdout
    except OSError:
        raise RecipeError("no systemctl here: --run runs on the head Spark") from None
    for line in lu.splitlines():
        f = line.split()
        if len(f) >= 3 and f[0].startswith("beast-recipe-") and f[0] not in units:
            units.append(f[0])
    for u in units:
        p = subprocess.run(["systemctl", "--user", "is-active", u], capture_output=True, text=True, check=False)
        if p.returncode == 0 or p.stdout.strip() in BUSY_STATES:
            active.append(u)
    try:
        dp = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=False,
                            timeout=20).stdout
        active += [n for n in dp.split() if n.startswith("openbeast-") and "-rank" in n]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return active


class model_lock:
    """beast-spark's switch lock (${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock, or SB_LOCK): one model
    switch at a time. Skipped when the caller already holds it (SPARK_MODEL_LOCK_HELD=1)."""

    def __init__(self, active: bool):
        self.active = active and os.environ.get("SPARK_MODEL_LOCK_HELD") != "1"
        self.fh = None

    def __enter__(self):
        if not self.active:
            return self
        import fcntl
        path = os.environ.get("SB_LOCK") or os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "spark-model.lock")
        self.fh = open(path, "a")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise RecipeError(f"another model switch is running (it holds {path}): wait for it, then retry") from None
        return self

    def __exit__(self, *a):
        if self.fh:
            self.fh.close()


def _serving_guard(args, what: str) -> None:
    """Heavy copies (docker build/pull/save/load, model-fetch, sync-weights) run outside any memory
    scope: refuse them while a model serves, like spark-hy4-stage.sh, unless --while-serving."""
    if args.run and not getattr(args, "while_serving", False):
        busy = any_inference_active(Runner(True))
        if busy:
            raise RecipeError(f"{what}: {', '.join(busy)} active. docker build/pull/save/load and the D2 "
                              "model-fetch/sync-weights run outside any memory scope, and the serving model's memguard "
                              "may kill its rank: run this between models, or pass --while-serving to accept that")


def _key(key_file: str) -> str:
    try:
        k = Path(os.path.expanduser(key_file)).read_text().strip()
    except OSError:
        return ""
    return k if re.match(r"^[A-Za-z0-9._~+/=-]{8,}$", k) else ""


def served_ids(url: str, key: str, timeout: float = 5) -> list[str]:
    req = urllib.request.Request(url.rstrip("/") + "/v1/models")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return [m.get("id", "") for m in (json.load(r).get("data") or [])]
    except (urllib.error.URLError, OSError, ValueError):
        return []


def bench(url: str, key: str, model: str, max_tokens: int = 512, runs: int = 3, streams: int = 4) -> dict:
    """1 stream (median of `runs` after a warm-up) and `streams` at once (aggregate completion tokens /
    the wall from the first request to the last token), with tokps.py's one_run: temperature 0, a fixed
    seed, a fixed rotation of prompts, decode = (tokens - 1) / (last - first token)."""
    sys.path.insert(0, str(REPO / "scripts"))
    import tokps
    base = url.rstrip("/")
    tokps.one_run(base, key, model, tokps.prompt_for("short", 0), max_tokens)       # warm-up, not counted
    singles = [tokps.one_run(base, key, model, tokps.prompt_for("short", i + 1), max_tokens) for i in range(runs)]
    rates = sorted(x["decode_tps"] for x in singles if x.get("decode_tps"))
    res: list[dict] = [{}] * streams
    t0 = time.monotonic()

    def go(i):
        res[i] = tokps.one_run(base, key, model, tokps.prompt_for("short", 10 + i), max_tokens)
    th = [threading.Thread(target=go, args=(i,)) for i in range(streams)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    wall = time.monotonic() - t0
    toks = sum(int(x.get("completion_tokens") or 0) for x in res)
    return {"model": model, "url": base, "max_tokens": max_tokens,
            "toks_1": rates[len(rates) // 2] if rates else None, "runs_1": singles,
            "toks_agg": round(toks / wall, 2) if wall > 0 else None, "streams": streams, "runs_n": res,
            "when": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}  # noqa: UP017 (3.9)


# --------------------------------------------------------------------------- commands

def _out(args, doc: dict, text: str) -> int:
    if args.json:
        print(json.dumps(doc, indent=1, sort_keys=True, default=str))
    else:
        sys.stdout.write(text)
    return 0


def _counts(s: str | None) -> list[int] | None:
    if not s:
        return None
    try:
        v = sorted({int(x) for x in s.split(",") if x.strip()})
    except ValueError:
        raise RecipeError(f"--sparks {s!r}: numbers 1..8, comma-separated") from None
    if not v or v[0] < 1 or v[-1] > 8:
        raise RecipeError("--sparks: 1..8")
    return v


def cmd_init(args) -> int:
    data = load_data()
    d, r, warnings = init(args.source, args.name, args.sparks, args.engine, data, args.force, args.accept_nc)
    pl = plan(r, data)
    doc = {"recipe": str(d), "warnings": warnings, "plan": pl}
    text = f"wrote {d}/recipe.env" + (" + SHA256SUMS" if (d / "SHA256SUMS").exists() else "") + "\n" + \
        "".join(f"  warning: {w}\n" for w in warnings) + "\n" + render_plan(pl)
    return _out(args, doc, text)


def cmd_plan(args) -> int:
    data = load_data()
    r = load_recipe(args.recipe)
    counts = _counts(args.sparks)
    target = max(counts) if counts and len(counts) == 1 else None
    pl = plan(r, data, sparks=target, counts=counts,
              cluster_file=Path(os.path.expanduser(args.cluster)) if args.cluster else None)
    cur = int(r.num("SPARKS") or 2)
    chosen_n = pl["engine"]["n"]
    if target and target != cur and chosen_n and chosen_n != cur:
        pl["retarget_note"] = (f"note: planned for {target} Sparks, the plan runs TP{chosen_n}, but recipes/{r.name}/recipe.env "
                               f"has SPARKS={cur}: set SPARKS={chosen_n} there before engine/stage/up (they act on recipe.env)")
    elif target and target != cur:
        pl["retarget_note"] = (f"note: planned for {target} Sparks, the chosen TP{chosen_n or '-'} equals recipe.env's "
                               f"SPARKS={cur}: nothing to change there; more ranks need the work the other sizes show "
                               "(the Fit and Gaps tables) first")
    if args.write:
        (r.dir / "plan.json").write_text(json.dumps({k: v for k, v in pl.items() if k != "path"}, indent=1,
                                                    sort_keys=True, default=str) + "\n")
    return _out(args, pl, render_plan(pl) + (f"\nwrote {r.dir}/plan.json\n" if args.write else ""))


def _ctx(args):
    data = load_data()
    r = load_recipe(args.recipe)
    cf = Path(os.path.expanduser(args.cluster)) if getattr(args, "cluster", None) else None
    pl = plan(r, data, cluster_file=cf)
    p = choose(r, data, pl["sparks"])["chosen"]
    eng = engine_by_id(data, p["engine"]) or {}
    ns, _ = nodes(p.get("n") or pl["sparks"], cf)
    if getattr(args, "run", False) and any(n.startswith("<") for n in ns):
        raise RecipeError(f"{r.name} needs {p.get('n') or pl['sparks']} Sparks; the cluster file lists fewer "
                          f"({' '.join(ns)}): add the nodes to CLUSTER_NODES before --run")
    return data, r, pl, p, eng, ns, cf


def write_profile(r: Recipe, p: dict, force: bool, cluster_file: Path | None = None) -> list[Path]:
    """Render and write the D2 profile (scripts/backends/models/<PROFILE>.env) and the unit (recipes/<name>/),
    and recipes/<name>/cluster.env off the pair. Refuses to replace a profile that exists and differs."""
    launcher = launcher_of(r, p)
    prof = render_profile(r, p)
    unit_text = render_unit(r, p)
    target = BACKENDS / "models" / f"{profile_name(r)}.env"
    if target.exists() and target.read_text() != prof and not force:
        raise RecipeError(f"{target} exists and differs (--force replaces it)")
    target.write_text(prof)
    u = r.dir / unit_of(r, launcher)
    u.write_text(unit_text)
    out = [target, u]
    c = write_recipe_cluster(r, p["n"], cluster_file)
    if c:
        out.append(c)
    return out


def install_unit(r: Recipe, p: dict) -> Path:
    """Copy the rendered unit into ~/.config/systemd/user and daemon-reload. Safe while a model serves:
    the unit has no [Install] section (enables and starts nothing) and a daemon-reload touches no unit."""
    src = r.dir / unit_of(r, launcher_of(r, p))
    if not src.is_file():
        raise RecipeError(f"{src} is missing: beast-recipe up {r.name} --write-profile first (commit it)")
    dst = Path.home() / ".config/systemd/user" / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    return dst


ENGINE_STAGE_MARKS = ("engine.sh", "docker build", "docker pull", "sync-image", "save-image", "tensorfold capabilities")


def version_ok(outp: str, want: str) -> bool:
    """True when `want` (no version pinned: always) is one whole token of a --version output
    (a leading v ignored), so 1.2.1 does not pass for an installed 1.2.10."""
    return not want or want in [t.lstrip("v") for t in outp.split()]


def engine_missing(r: Recipe, p: dict, eng: dict, ns: list[str]) -> list[str]:
    """Run the engine's check steps (tensorfold --version / docker image inspect) on every node; the
    nodes where the planned engine is not what is installed."""
    runner = Runner(True)
    bad = []
    for node, cmd in engine_steps(r, p, eng, ns):
        if node in ("-", "connected box") or node.startswith("<") or any(m in cmd for m in ENGINE_STAGE_MARKS):
            continue
        code, outp = runner.sh(node, cmd.split("   #")[0], capture=True)
        want = eng.get("version", "")
        if code != 0 or ("--version" in cmd and not version_ok(outp, want)):
            bad.append(f"{node}: {outp.strip()[:80] or 'exit ' + str(code)} (want {eng.get('id')})")
    return bad


def cmd_engine(args) -> int:
    _data, r, _pl, p, eng, ns, _cf = _ctx(args)
    runner = Runner(args.run)
    print(f"engine {eng.get('id')} ({eng.get('status')}) for {r.name}, on {' '.join(ns)}" +
          ("" if args.run else "   [dry-run: --run executes the checks; staging steps on a connected box are printed]"))
    if args.run and args.stage:
        _serving_guard(args, "engine --stage")
    rc = 0
    for node, cmd in engine_steps(r, p, eng, ns):
        if args.run and any(m in cmd for m in ENGINE_STAGE_MARKS) and not args.stage:
            print(f"  [{node}] (stage step; --stage runs it) {cmd}")
            continue
        code, outp = runner.sh(node, cmd.split("   #")[0], capture=True)
        if args.run and node not in ("-", "connected box") and not node.startswith("<"):
            want = eng.get("version", "")
            ok = code == 0 and (version_ok(outp, want) if "--version" in cmd else True)
            print(f"    -> {'ok' if ok else 'MISSING'}{(': ' + outp.strip()[:120]) if outp.strip() else ''}")
            rc = rc or (0 if ok else 1)
    return rc


PLACEHOLDER = re.compile(r"<[^<>]+>")


def is_light_step(cmd: str) -> bool:
    """A stage step that may run while a model serves: a note, a command inside the capped memory scope,
    or a read-only check (spark-model-intake.sh reads headers only, never weights without --checksums)."""
    c = cmd.split("   #")[0]
    return (cmd.startswith("#") or c.startswith(SCOPE) or f"{SCOPE} sha256sum" in c
            or ("spark-model-intake.sh" in c and "--checksums" not in c and "convert.py" not in c))


def cmd_stage(args) -> int:
    _data, r, pl, p, eng, ns, cf = _ctx(args)
    if args.run and licence_flags(r.get("LICENCES"))[1] and not args.accept_nc:
        raise RecipeError(f"LICENCES={r.get('LICENCES')}: a non-commercial or no-derivatives artefact; --accept-nc stages it")
    only = set((args.only or "").split(",")) - {""}
    bad = only - set(STAGE_STEP_NAMES)
    if bad:
        raise RecipeError(f"--only: unknown step(s) {', '.join(sorted(bad))}; known: {', '.join(STAGE_STEP_NAMES)} "
                          "(images are `engine`'s)")
    if args.run and not args.force and pl["verdict"] not in ("SERVABLE-NOW", "SERVABLE-WITH-WORK"):
        raise RecipeError(f"{r.name} is {pl['verdict']}: stage --run needs a servable plan (--force overrides)")
    if args.run and is_protected(weights_dir(r)) and r.get("WEIGHTS_STATUS") != "local":
        raise RecipeError(f"WEIGHTS_DIR={weights_dir(r)} is (or holds) a kept pack, and WEIGHTS_STATUS is not local: "
                          "nothing is ever downloaded or mirrored onto it (fix WEIGHTS_DIR)")
    steps = stage_steps(r, p, eng, ns)
    if args.run:
        heavy = [c for s_, _n, c in steps if (not only or s_ in only) and not is_light_step(c)]
        if heavy:
            _serving_guard(args, "stage")
    runner = Runner(args.run)
    print(f"stage {r.name} on {' '.join(ns)}" + ("" if args.run else "   [dry-run: --run executes]"))
    workers = sorted({n for s_, n, _c in steps if (not only or s_ in only) and n != "local"})
    workers_commit_guard(runner, workers, args.force, "stage")
    for step, node, cmd in steps:
        if only and step not in only:
            continue
        if step == "profile":
            target = BACKENDS / "models" / f"{profile_name(r)}.env"
            need = not target.exists() or (recipe_cluster(r, p.get("n")) and not (r.dir / "cluster.env").exists())
            if not need:
                if cmd.startswith("# models/"):
                    print(f"  [{node}] (profile) {target.relative_to(REPO) if REPO in target.parents else target} exists")
                continue
            if not cmd.startswith("# models/"):
                continue
            if args.run:
                for f in write_profile(r, p, args.force, cf):
                    print(f"  [{node}] (profile) wrote {f}")
                print("  commit the profile and the unit before staging the workers (they verify from the same commit)")
            else:
                print(f"  [{node}] (profile) --run would write {target} and the unit (beast-recipe up {r.name} --write-profile)")
            continue
        if cmd.startswith("#"):
            print(f"  [{node}] {cmd}")
            continue
        if args.run and (PLACEHOLDER.search(cmd.split("   #")[0]) or node.startswith("<")):
            print(f"  [{node}] MANUAL (a template: fill the placeholders and run it yourself): {cmd}")
            continue
        if "sha256sum -c" in cmd:
            sums = "".join(x + "\n" for x in (r.dir / "SHA256SUMS").read_text().splitlines() if x and not x.startswith("#"))
            code, _ = runner.sh(node, cmd.split("   #")[0], stdin=sums)
        else:
            code, _ = runner.sh(node, cmd.split("   #")[0])
        if code:
            print(f"  FAILED ({code}): stop here; re-running resumes")
            return code
    return 0


def cmd_up(args) -> int:
    _data, r, pl, p, _eng, ns, cf = _ctx(args)
    launcher = launcher_of(r, p)
    unit = unit_of(r, launcher)
    if pl["verdict"] not in ("SERVABLE-NOW",) and not args.force:
        msg = f"{r.name} is {pl['verdict']}: `up` acts only on SERVABLE-NOW (beast-recipe plan {r.name}; --force overrides)"
        if args.run:
            raise RecipeError(msg)
        print(f"note: {msg}")
    steps = up_steps(r, p, ns)
    if all(n == "-" for n, _c in steps):
        if args.run:
            raise RecipeError(f"{r.name}: {steps[0][1]} (nothing to start; --force does not change that)")
        for n, c in steps:
            print(f"  [{n}] {c}")
        return 0
    if launcher.startswith("d2-"):
        if args.write_profile:
            for f in write_profile(r, p, args.force, cf):
                print(f"wrote {f}")
        else:
            prof = render_profile(r, p)
            unit_text = render_unit(r, p)
            target = BACKENDS / "models" / f"{profile_name(r)}.env"
            print(f"--- {target} (--write-profile writes it)\n{prof}--- {unit}\n{unit_text}---")
    if not args.run:
        runner = Runner(False)
        for node, cmd in steps:
            runner.sh(node, cmd.split("   #")[0])
        return 0
    with model_lock(True):
        runner = Runner(True)
        busy = any_inference_active(Runner(True))
        if unit in busy:
            print(f"{unit} is already active: nothing installed or started")
            return 0
        refuse = (f"{', '.join(busy)} active: one model at a time on the pair; NOT started. Switch through beast-spark: "
                  f"beast-recipe adopt {r.name} --run, then beast-spark {r.name} (or beast-spark down first)")
        d2 = launcher.startswith("d2-")
        if busy and not d2:
            raise RecipeError(refuse)
        if busy:
            # D2 on a busy pair: install the unit (no [Install]: it enables and starts nothing, and a
            # daemon-reload touches no running unit), so adopt + beast-spark can switch to it; start nothing
            dst = install_unit(r, p)
            raise RecipeError(refuse.replace("NOT started.", f"installed {dst}, NOT started."))
        for node, cmd in steps:
            code, _ = runner.sh(node, cmd.split("   #")[0])
            if code:
                return code
    url, key = f"http://127.0.0.1:{r.get('PORT', '8000')}", _key(args.key_file)
    t = 0
    while r.get("SERVED_NAME") not in served_ids(url, key):
        if t >= args.wait:
            raise RecipeError(f"{r.get('SERVED_NAME')} is not on {url} after {args.wait}s: journalctl --user -u {unit}")
        time.sleep(5)
        t += 5
    print(f"serving {r.get('SERVED_NAME')} on {url}")
    return 0


def cmd_down(args) -> int:
    _data, r, _pl, p, _eng, ns, _cf = _ctx(args)
    launcher = launcher_of(r, p)
    unit = unit_of(r, launcher)
    if launcher.startswith("mia-unit:"):
        m = mia_unit_mismatch(r, p, unit)
        if m:
            msg = f"{r.name} has no unit of its own yet ({m}): {unit} is not this recipe's launch, so nothing is stopped"
            if args.run:
                raise RecipeError(msg + " (beast-spark down stops whatever serves)")
            print(f"note: {msg}")
            return 0
    print(f"note: on the pair, switch through beast-spark (beast-spark ds | glm | <recipe>): it also points "
          f"openbeast.conf back; this only stops {unit}")
    with model_lock(args.run):
        code, _ = Runner(args.run).sh(ns[0], f"systemctl --user stop {unit}")
    return code


def cmd_check(args) -> int:
    _data, r, _pl, p, _eng, ns, _cf = _ctx(args)
    url = args.url or f"http://127.0.0.1:{r.get('PORT', '8000')}"
    steps = check_steps(r, p, url, args.key_file)
    if not args.run:
        print(f"check {r.name}   [dry-run: --run executes]")
        print(f"  [{ns[0]}] {steps[0]}")
        if args.bench:
            print(f"  [{ns[0]}] bench: 1 warm-up, 3 single-stream runs (median), then 4 streams at once; max_tokens 512")
        return 0
    key = _key(args.key_file)
    ids = served_ids(url, key)
    if r.get("SERVED_NAME") not in ids:
        raise RecipeError(f"{url} serves {', '.join(ids) or 'nothing'}, not {r.get('SERVED_NAME')}")
    rc = subprocess.run(["bash", str(BACKENDS / "conformance.sh"), "--url", url, "--model", r.get("SERVED_NAME"),
                         "--backend", "vllm" if p["kind"] == "vllm" else "tensorfold", "--key-file",
                         os.path.expanduser(args.key_file), "--concurrency", "4"]).returncode
    if args.bench:
        b = bench(url, key, r.get("SERVED_NAME"))
        runs = r.dir / "runs"
        runs.mkdir(exist_ok=True)
        f = runs / f"bench-{time.strftime('%Y%m%dT%H%M%S')}.json"
        f.write_text(json.dumps(b, indent=1) + "\n")
        print(f"bench: {b['toks_1']} tok/s at 1 stream, {b['toks_agg']} aggregate at {b['streams']}  ({f})")
        if r.num("BASELINE_TOKS_1"):
            print(f"  against {r.get('BASELINE_FROM')}: {r.num('BASELINE_TOKS_1')} / {r.num('BASELINE_TOKS_4')} "
                  "(measured with another probe: re-measure it with this command before deciding)")
    return rc


def cmd_adopt(args) -> int:
    _data, r, pl, p, eng, ns, cf = _ctx(args)
    launcher = launcher_of(r, p)
    if r.name in RESERVED_NAMES:
        raise RecipeError(f"{r.name!r} is a beast-spark command: rename the recipe directory")
    for s_ in adopt_steps(r, p):
        print(f"  {s_}")
    fam = {"name": r.name, "title": r.get("TITLE"), "model_types": [r.get("MODEL_TYPE")],
           "architectures": [r.get("ARCHITECTURE")], "formats": [r.get("FORMAT")], "tp": p.get("n"),
           "recipe": f"recipes/{r.name} (beast-recipe)", "engine": p["engine"], "unit": unit_of(r, launcher).rsplit(".", 1)[0],
           "served_name": r.get("SERVED_NAME"), "rank_files": bool(r.get("RANK_INPUTS") not in ("", "none")),
           "swap": f"beast-recipe plan {r.name}; beast-spark {r.name}", "known_packs": {}, "pack_fingerprints": {},
           "quant": {}, "reference_dirs": [weights_dir(r)], "aux": [r.get("AUX")] if r.get("AUX") else []}
    if not launcher.startswith("d2-"):
        return 0
    if not re.match(r"^[A-Za-z0-9._-]+$", r.get("SERVED_NAME")):
        raise RecipeError("SERVED_NAME: required, letters, digits, . _ - only (beast-spark's registry ignores anything else)")
    tfv = p.get("version") or ""
    entry = (f"# beast-spark registry entry for recipe {r.name} (beast-recipe adopt; parsed, never sourced)\n"
             f"UNIT={unit_of(r, launcher)}\nSERVED={r.get('SERVED_NAME')}\n"
             f"BACKEND={'vllm' if launcher == 'd2-vllm' else 'tensorfold'}\nWAIT_S=2400\n"
             + (f"TENSORFOLD_VERSION={tfv}\n" if tfv else ""))
    f = state_dir() / f"{r.name}.env"
    if args.run:
        # the same gate as `up`: beast-spark <name> stops the model that serves, so only a plan that can
        # serve now, on the engine every Spark has, becomes selectable
        if pl["verdict"] != "SERVABLE-NOW" and not args.force:
            raise RecipeError(f"{r.name} is {pl['verdict']}: adopt --run acts only on SERVABLE-NOW, like up "
                              f"(beast-spark {r.name} would stop the serving model for a unit that cannot come up; "
                              "--force overrides)")
        bad = engine_missing(r, p, eng, ns) if eng else []
        if bad and not args.force:
            raise RecipeError(f"{eng.get('id')} is not installed as planned: " + "; ".join(bad)
                              + f" (beast-recipe engine {r.name} --run --stage; --force overrides)")
        workers_commit_guard(Runner(True), list(ns), args.force, "adopt")
    if args.write_family and args.run:
        reg = json.loads(FAMILIES_FILE.read_text())
        reg["families"] = [x for x in reg["families"] if x.get("name") != r.name] + [fam]
        FAMILIES_FILE.write_text(json.dumps(reg, indent=1, ensure_ascii=False) + "\n")
        print(f"  wrote the family {r.name} into {FAMILIES_FILE} (commit it)")
    else:
        print(f"  spark-families.json entry ({'--run ' if args.write_family else '--write-family --run '}adds it):\n"
              f"{json.dumps(fam, indent=1)}")
    if not args.run:
        print(f"  [dry-run] would install {unit_of(r, launcher)} into ~/.config/systemd/user (daemon-reload; enables "
              f"nothing) and write {f}:\n" + "".join(f"    {x}\n" for x in entry.splitlines()))
        return 0
    if recipe_cluster(r, p.get("n")) and not (r.dir / "cluster.env").is_file():
        c = write_recipe_cluster(r, p["n"], cf)
        print(f"  wrote {c} (the unit's --cluster; the first {p['n']} node(s) of the cluster file)")
    dst = install_unit(r, p)
    print(f"  installed {dst} (no [Install]: enables and starts nothing)")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(entry)
    print(f"  wrote {f}: beast-spark {r.name} switches to it")
    return 0


def cmd_rollback(args) -> int:
    f = state_dir() / ".previous"
    prev = ""
    if f.is_file():
        m = re.search(r"^PREVIOUS=([A-Za-z0-9._-]+)\s*$", f.read_text(), re.MULTILINE)
        prev = m.group(1) if m else ""
    if not prev:
        raise RecipeError(f"no previous selection recorded in {f} (beast-spark writes it when it switches to a recipe)")
    if prev == "none":
        print("nothing served before the last switch to a recipe: rollback is beast-spark down")
        prev = "down"
    code, _ = Runner(args.run).sh("local", f"beast-spark {prev}")
    return code


def cmd_list(args) -> int:
    data = load_data()
    rows = []
    for r in list_recipes():
        try:
            pl = plan(r, data)
            rows.append({"name": r.name, "title": r.get("TITLE"), "verdict": pl["verdict"], "engine": pl["engine"]["id"],
                         "tp": pl["engine"]["n"], "sparks": pl["sparks"]})
        except RecipeError as e:
            rows.append({"name": r.name, "title": r.get("TITLE"), "verdict": "ERROR", "error": str(e)})
    head = f"{'RECIPE':<34} {'VERDICT':<20} {'ENGINE':<18} TP/SPARKS  TITLE\n"
    text = (head + "".join(f"{x['name']:<34} {x['verdict']:<20} {x.get('engine', '-'):<18} "
                           f"{'TP' + str(x.get('tp') or '-') + '/' + str(x.get('sparks', '-')):<10} {x['title']}\n"
                           for x in rows)) if rows else "no recipes\n"
    return _out(args, {"recipes": rows, "dir": str(recipes_dir())}, text)


def cmd_show(args) -> int:
    if args.keys:
        return _out(args, {k: v[1] for k, v in KEYS.items()}, "".join(f"{k:<20} {v[1]}\n" for k, v in KEYS.items()))
    r = load_recipe(args.recipe)
    doc = {"name": r.name, "path": str(r.path), "values": r.values,
           "provenance": {k: r.provenance(k) for k in r.values}}
    lines = []
    for k, v in r.values.items():
        pv = r.provenance(k)
        if len(v) > 44:
            lines.append(f"  {k:<20} {v}\n  {'':<20} {'':<44}  # {pv}\n" if pv else f"  {k:<20} {v}\n")
        else:
            lines.append(f"  {k:<20} {v:<44}  {('# ' + pv) if pv else ''}".rstrip() + "\n")
    return _out(args, doc, f"{r.name}  ({r.path})\n" + "".join(lines))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="beast-recipe", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_, recipe=True, act=True):
        p = sub.add_parser(name, help=help_, description=help_)
        if recipe:
            p.add_argument("recipe", help="a recipe name (recipes/<name>) or its directory / recipe.env")
        p.add_argument("--json", action="store_true", help="machine-readable output (plan, list, show, init)")
        if act:
            g = p.add_mutually_exclusive_group()
            g.add_argument("--dry-run", action="store_true", help="print what would run (the default)")
            g.add_argument("--run", action="store_true", help="act")
            p.add_argument("--cluster", help="cluster.env (default scripts/backends/cluster.env); the recipe uses its "
                                             "first SPARKS nodes")
        return p

    engines_help = "an engine id from data/recipe-engines.json, or auto (plan picks the best path)"
    try:
        engines_help += ": " + ", ".join(e["id"] for e in json.loads(DATA_FILE.read_text())["engines"])
    except (OSError, ValueError, KeyError):
        pass
    p = sub.add_parser("init", help="inspect a model and write recipes/<name>/recipe.env",
                       description="inspect a model (metadata only) and write recipes/<name>/recipe.env, then print the plan")
    p.add_argument("source", help="https://huggingface.co/ORG/REPO[/tree/REV] | ORG/REPO[@sha] | a local directory")
    p.add_argument("--sparks", type=int, default=2, choices=range(1, 9), metavar="N", help="the target cluster size, 1..8")
    p.add_argument("--name", help="the recipe name (default <repo>-<N>x)")
    p.add_argument("--engine", default="auto", help=engines_help)
    p.add_argument("--force", action="store_true", help="replace an existing recipes/<name>/recipe.env")
    p.add_argument("--accept-nc", action="store_true", help="accept a non-commercial / no-derivatives licence")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p = add("plan", "fit, engine, parallelism, drafter, verdict, gaps and the end-to-end commands", act=False)
    p.add_argument("--sparks", help="cluster sizes to tabulate, e.g. 1,2,3,4 (one value also sets the target of the "
                                    "plan only: the acting commands follow SPARKS= in recipe.env)")
    p.add_argument("--write", action="store_true", help="write recipes/<name>/plan.json")
    p.add_argument("--cluster", help="cluster.env for the plan's nodes (default scripts/backends/cluster.env)")
    p = add("engine", "check the engine on every Spark; print (or --run --stage) the stage commands")
    p.add_argument("--stage", action="store_true", help="with --run: also run the install/build steps")
    p.add_argument("--while-serving", action="store_true", help="allow --stage while an inference unit is active")
    p = add("stage", "the D2 profile when missing, then weights (+ drafter, aux) on every Spark, verified (images: engine)")
    p.add_argument("--only", help=",".join(STAGE_STEP_NAMES))
    p.add_argument("--accept-nc", action="store_true", help="stage a non-commercial / no-derivatives artefact")
    p.add_argument("--force", action="store_true", help="stage a plan that is not servable; replace a differing profile")
    p.add_argument("--while-serving", action="store_true", help="allow downloads and copies while an inference unit is active")
    p = add("up", "render the profile + unit, install the unit and start it (never while another model serves)")
    p.add_argument("--write-profile", action="store_true", help="write scripts/backends/models/<PROFILE>.env and the unit")
    p.add_argument("--force", action="store_true", help="act on a plan that is not SERVABLE-NOW; replace a differing profile")
    p.add_argument("--key-file", default=DEFAULT_KEY_FILE, help="the API key file (read, never printed)")
    p.add_argument("--wait", type=int, default=2400, help="seconds to wait for the served id (default 2400)")
    add("down", "stop the recipe's unit")
    p = add("check", "conformance + (--bench) decode tok/s at 1 and 4 streams")
    p.add_argument("--bench", action="store_true", help="1 warm-up, 3 single-stream runs (median), 4 streams at once")
    p.add_argument("--url", help="the endpoint (default http://127.0.0.1:PORT)")
    p.add_argument("--key-file", default=DEFAULT_KEY_FILE, help="the API key file (read, never printed)")
    p = add("adopt", "make the recipe selectable: beast-spark <name> (installs the unit, writes the registry entry)")
    p.add_argument("--force", action="store_true", help="adopt a plan that is not SERVABLE-NOW, or an engine not as planned")
    p.add_argument("--write-family", action="store_true", help="with --run: add it to data/spark-families.json (for intake)")
    add("rollback", "back to the selection before the last switch to a recipe", recipe=False)
    add("list", "every recipe and its verdict", recipe=False, act=False)
    p = sub.add_parser("show", help="a recipe's values and where each came from",
                       description="a recipe's values and where each came from; --keys: every key a recipe may set")
    p.add_argument("recipe", nargs="?")
    p.add_argument("--keys", action="store_true", help="every key a recipe may set")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    if args.cmd == "show" and not args.keys and not args.recipe:
        ap.error("show: a recipe, or --keys")
    try:
        return {"init": cmd_init, "plan": cmd_plan, "engine": cmd_engine, "stage": cmd_stage, "up": cmd_up,
                "down": cmd_down, "check": cmd_check, "adopt": cmd_adopt, "rollback": cmd_rollback,
                "list": cmd_list, "show": cmd_show}[args.cmd](args)
    except (RecipeError, obprofile.ProfileError, hfapi.HubError) as e:
        msg = str(e)
        if isinstance(e, hfapi.Offline):
            msg += " (beast-recipe init <local dir> works offline)"
        print(f"beast-recipe: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
