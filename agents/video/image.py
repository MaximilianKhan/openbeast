"""beast-image: turn a job's parameters into a ComfyUI workflow (API format).

The model is Qwen-Image 2.1 (ChatR Labs' Uncensored Omni is that model with an
adapter merged in): one denoiser for text-to-image and for editing from
reference pictures, a Qwen3-VL text encoder and a VAE. ComfyUI supports it
natively; this module only wires its stock nodes. The graph is the model's
own example workflows (ComfyUI_workflows/ in its repository): with reference
pictures it is their image-to-image graph, with the Turbo LoRA their 8-step
one.

Pure functions, no I/O: the server validates, this builds.
"""

from __future__ import annotations

from .workflow import ParamError

SIZE_STEP = 32
MIN_SIDE, MAX_SIDE = 256, 2048
MAX_PIXELS = 1728 * 1728                 # the largest size the model's authors show
FIT_PIXELS = 1024 * 1024                 # the size it was trained around; a reference is fitted to this
MAX_REFS = 2

#: (label, width, height)
SIZE_PRESETS = [
    ("1024 × 1024 square", 1024, 1024),
    ("768 × 1152 portrait", 768, 1152),
    ("1152 × 768 landscape", 1152, 768),
    ("832 × 1216 portrait", 832, 1216),
    ("1216 × 832 landscape", 1216, 832),
    ("768 × 1344 tall", 768, 1344),
    ("1344 × 768 wide", 1344, 768),
    ("1536 × 1536 large (slow)", 1536, 1536),
]

SAMPLERS = ["euler", "er_sde", "euler_ancestral", "dpmpp_2m", "res_multistep"]
SCHEDULERS = ["simple", "normal", "beta", "sgm_uniform"]

#: steps and guidance: the model's own, and with the 8-step Turbo LoRA
STANDARD = {"steps": 35, "cfg": 5.0}
TURBO = {"steps": 8, "cfg": 1.0}

DEFAULTS = {
    "prompt": "",
    "negative": "",
    "width": 1024,
    "height": 1024,
    "steps": STANDARD["steps"],
    "cfg": STANDARD["cfg"],
    "sampler": "euler",
    "scheduler": "simple",
    "seed": -1,                 # -1 = pick one
    "model": "",                # "" = the server's default denoiser
    "turbo": False,
}

#: the sigma shift of the example workflows (ModelSamplingFlux)
MAX_SHIFT, BASE_SHIFT = 0.6935483870967742, 0.5
#: reference pictures are shown to the text encoder at about this many pixels a side
REF_RESOLUTION = 1024


def _num(params: dict, key: str, kind, lo, hi, default):
    raw = params.get(key)
    if raw is None or raw == "":
        raw = default
    try:
        val = kind(raw)
    except (TypeError, ValueError):
        raise ParamError(f"{key} must be a number") from None
    if val != val or not (lo <= val <= hi):
        raise ParamError(f"{key} must be between {lo} and {hi}")
    return val


def clean_params(params: dict, models: list[str] | None = None,
                 default_model: str = "", turbo_available: bool = True) -> dict:
    """Validated, complete parameters. Raises ParamError naming the field."""
    out: dict = {"kind": "image"}
    out["prompt"] = str(params.get("prompt") or "").strip()
    out["negative"] = str(params.get("negative") or "").strip()
    if len(out["prompt"]) > 8000 or len(out["negative"]) > 8000:
        raise ParamError("the prompt is too long (8,000 characters at most)")

    w = _num(params, "width", int, MIN_SIDE, MAX_SIDE, DEFAULTS["width"])
    h = _num(params, "height", int, MIN_SIDE, MAX_SIDE, DEFAULTS["height"])
    if w % SIZE_STEP or h % SIZE_STEP:
        raise ParamError(f"width and height must be multiples of {SIZE_STEP}")
    if w * h > MAX_PIXELS:
        raise ParamError(f"{w} × {h} is larger than 1728 × 1728, the most this model is shown at")
    out["width"], out["height"] = w, h

    turbo = params.get("turbo", False)
    if not isinstance(turbo, bool):
        raise ParamError("turbo must be true or false")
    if turbo and not turbo_available:
        raise ParamError("the Turbo LoRA is not installed on the rig")
    out["turbo"] = turbo
    base = TURBO if turbo else STANDARD
    out["steps"] = _num(params, "steps", int, 1, 100, base["steps"])
    out["cfg"] = _num(params, "cfg", float, 1.0, 15.0, base["cfg"])

    out["sampler"] = str(params.get("sampler") or DEFAULTS["sampler"])
    if out["sampler"] not in SAMPLERS:
        raise ParamError(f"sampler must be one of: {', '.join(SAMPLERS)}")
    out["scheduler"] = str(params.get("scheduler") or DEFAULTS["scheduler"])
    if out["scheduler"] not in SCHEDULERS:
        raise ParamError(f"scheduler must be one of: {', '.join(SCHEDULERS)}")

    seed = params.get("seed", -1)
    try:
        seed = int(seed) if seed not in (None, "") else -1
    except (TypeError, ValueError):
        raise ParamError("seed must be a whole number") from None
    if seed < -1 or seed >= 2 ** 63:
        raise ParamError("seed must be between 0 and 2^63, or -1 for a random one")
    out["seed"] = seed

    model = str(params.get("model") or default_model or "")
    if models is not None and model not in models:
        raise ParamError("that model is not installed on the rig" if model
                         else "no image model is installed on the rig")
    out["model"] = model
    return out


def build(params: dict, *, text_encoder: str, vae: str, turbo_lora: str = "",
          refs: list[str] | tuple[str, ...] = (), prefix: str = "beast-video/image") -> dict:
    """The ComfyUI prompt for one picture.

    `params` is clean_params() output with a concrete seed. `refs` are file
    names already in ComfyUI's input folder: with any, the prompt edits or
    restages them; with none it is text-to-image. One output node, "save".
    """
    if len(refs) > MAX_REFS:
        raise ParamError(f"at most {MAX_REFS} reference pictures")
    if params.get("turbo") and not turbo_lora:
        raise ParamError("the Turbo LoRA is not installed on the rig")
    g: dict = {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": params["model"], "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": text_encoder, "type": "qwen_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        # The model's authors: an empty negative prompt turns guidance off, a
        # single space keeps it on.
        "cond": {"class_type": "TextEncodeQwenImage21",
                 "inputs": {"clip": ["clip", 0], "prompt": params["prompt"],
                            "negative_prompt": params["negative"] or " ",
                            "resolution": REF_RESOLUTION}},
        "latent": {"class_type": "EmptyLatentImage",
                   "inputs": {"width": params["width"], "height": params["height"],
                              "batch_size": 1}},
    }
    model = ["unet", 0]
    if params.get("turbo"):
        g["lora"] = {"class_type": "LoraLoaderModelOnly",
                     "inputs": {"model": model, "lora_name": turbo_lora, "strength_model": 1.0}}
        model = ["lora", 0]
    g["cache"] = {"class_type": "QwenImage21Cache",
                  "inputs": {"model": model, "device": "auto", "dtype": "default"}}
    g["shift"] = {"class_type": "ModelSamplingFlux",
                  "inputs": {"model": ["cache", 0], "max_shift": MAX_SHIFT,
                             "base_shift": BASE_SHIFT,
                             "width": params["width"], "height": params["height"]}}
    for i, name in enumerate(refs, 1):
        g[f"ref{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g[f"rgba{i}"] = {"class_type": "JoinImageWithAlpha",
                         "inputs": {"image": [f"ref{i}", 0], "alpha": [f"ref{i}", 1]}}
        g["cond"]["inputs"][f"images.image_{i}"] = [f"rgba{i}", 0]
    if refs:
        g["cond"]["inputs"]["vae"] = ["vae", 0]
    g["sample"] = {"class_type": "KSampler",
                   "inputs": {"model": ["shift", 0], "positive": ["cond", 0],
                              "negative": ["cond", 1], "latent_image": ["latent", 0],
                              "seed": params["seed"], "steps": params["steps"],
                              "cfg": params["cfg"], "sampler_name": params["sampler"],
                              "scheduler": params["scheduler"], "denoise": 1.0}}
    g["decode"] = {"class_type": "VAEDecode",
                   "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}}
    g["save"] = {"class_type": "SaveImage",
                 "inputs": {"images": ["decode", 0], "filename_prefix": prefix}}
    return g
