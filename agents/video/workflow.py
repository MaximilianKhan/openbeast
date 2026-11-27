"""beast-video: turn a job's parameters into a ComfyUI workflow (API format).

The model is MiniMax H3 (Sulphur-3 is a fine-tune of it): one denoiser that
produces video and a stereo sound track together, a Qwen3-VL text encoder and
two decoders. ComfyUI supports it natively (comfy_extras/nodes_minimax_h3.py);
this module only wires its stock nodes. The graph is Sulphur's own example
workflow (S3_ComfyUI.zip) without the optional corrector, with the empty
latent swapped for the image-to-video node so a first and a last frame can be
given. With neither frame that node is plain text-to-video.

Pure functions, no I/O: the server validates, this builds.
"""

from __future__ import annotations

FPS = 24
#: frame counts the model accepts: 17k + 5 (the node snaps up to this grid)
FRAME_STEP, FRAME_BASE = 17, 5
MIN_SECONDS, MAX_SECONDS = 1.0, 15.0     # trained range ends near 362 frames
SIZE_STEP = 32
MIN_SIDE, MAX_SIDE = 256, 1536
MAX_PIXELS = 1344 * 768                  # the model's native canvas

#: (label, width, height). 16:9 first, then portrait and square.
SIZE_PRESETS = [
    ("672 × 384 (fast)", 672, 384),
    ("960 × 544", 960, 544),
    ("1344 × 768 (native)", 1344, 768),
    ("384 × 672 portrait", 384, 672),
    ("544 × 960 portrait", 544, 960),
    ("768 × 1344 portrait", 768, 1344),
    ("640 × 640 square", 640, 640),
]

SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "res_multistep", "lcm"]
SCHEDULERS = ["simple", "normal", "beta", "sgm_uniform"]

DEFAULTS = {
    "prompt": "",
    "negative": "",
    "width": 672,
    "height": 384,
    "seconds": 5.0,
    "steps": 20,
    "cfg": 1.0,                 # Sulphur-3 is guidance-distilled: 1 = one pass per step
    "shift_video": 12.0,
    "shift_audio": 3.0,
    "sampler": "euler",
    "scheduler": "simple",
    "seed": -1,                 # -1 = pick one
    "model": "",                # "" = the server's default denoiser
}


class ParamError(ValueError):
    """A job parameter is missing or out of range; the message is for the user."""


def frames_for(seconds: float) -> int:
    """The smallest valid frame count covering `seconds` at 24 fps."""
    want = max(FRAME_BASE, int(round(float(seconds) * FPS)))
    k = -(-(want - FRAME_BASE) // FRAME_STEP)          # ceil
    return FRAME_BASE + FRAME_STEP * max(0, k)


def _num(params: dict, key: str, kind, lo, hi):
    raw = params.get(key, DEFAULTS[key])
    if raw is None or raw == "":
        raw = DEFAULTS[key]
    try:
        val = kind(raw)
    except (TypeError, ValueError):
        raise ParamError(f"{key} must be a number") from None
    if val != val or not (lo <= val <= hi):            # NaN, or out of range
        raise ParamError(f"{key} must be between {lo} and {hi}")
    return val


def clean_params(params: dict, models: list[str] | None = None,
                 default_model: str = "") -> dict:
    """Validated, complete parameters. Raises ParamError naming the field."""
    out: dict = {}
    out["prompt"] = str(params.get("prompt") or "").strip()
    out["negative"] = str(params.get("negative") or "").strip()
    if len(out["prompt"]) > 8000 or len(out["negative"]) > 8000:
        raise ParamError("the prompt is too long (8,000 characters at most)")

    w = _num(params, "width", int, MIN_SIDE, MAX_SIDE)
    h = _num(params, "height", int, MIN_SIDE, MAX_SIDE)
    if w % SIZE_STEP or h % SIZE_STEP:
        raise ParamError(f"width and height must be multiples of {SIZE_STEP}")
    if w * h > MAX_PIXELS:
        raise ParamError(f"{w} × {h} is larger than the model's 1344 × 768 canvas")
    out["width"], out["height"] = w, h

    out["seconds"] = _num(params, "seconds", float, MIN_SECONDS, MAX_SECONDS)
    out["frames"] = frames_for(out["seconds"])
    out["steps"] = _num(params, "steps", int, 1, 80)
    out["cfg"] = _num(params, "cfg", float, 1.0, 15.0)
    out["shift_video"] = _num(params, "shift_video", float, 0.01, 100.0)
    out["shift_audio"] = _num(params, "shift_audio", float, 0.01, 100.0)

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
                         else "no denoiser model is installed on the rig")
    out["model"] = model
    return out


def build(params: dict, *, text_encoder: str, video_vae: str, audio_vae: str,
          first_frame: str | None = None, last_frame: str | None = None,
          prefix: str = "beast-video/clip") -> dict:
    """The ComfyUI prompt for one job.

    `params` is clean_params() output with a concrete seed. `first_frame` /
    `last_frame` are file names already in ComfyUI's input folder. The result
    has one output node, "save".
    """
    g: dict = {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": params["model"], "weight_dtype": "default"}},
        "shift": {"class_type": "MiniMaxH3SigmaShift",
                  "inputs": {"model": ["unet", 0],
                             "shift_video": params["shift_video"],
                             "shift_audio": params["shift_audio"]}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": text_encoder, "type": "minimax", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": video_vae}},
        "avae": {"class_type": "VAELoader", "inputs": {"vae_name": audio_vae}},
        "cond": {"class_type": "MiniMaxH3ImageToVideo",
                 "inputs": {"clip": ["clip", 0], "vae": ["vae", 0],
                            "prompt": params["prompt"],
                            "width": params["width"], "height": params["height"],
                            "length": params["frames"]}},
        # Ignored at cfg 1 (one pass per step), used above it.
        "neg": {"class_type": "CLIPTextEncode",
                "inputs": {"clip": ["clip", 0], "text": params["negative"]}},
        "guider": {"class_type": "CFGGuider",
                   "inputs": {"model": ["shift", 0], "positive": ["cond", 0],
                              "negative": ["neg", 0], "cfg": params["cfg"]}},
        "sigmas": {"class_type": "BasicScheduler",
                   "inputs": {"model": ["shift", 0], "scheduler": params["scheduler"],
                              "steps": params["steps"], "denoise": 1.0}},
        "sampler": {"class_type": "KSamplerSelect",
                    "inputs": {"sampler_name": params["sampler"]}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": params["seed"]}},
        "sample": {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["noise", 0], "guider": ["guider", 0],
                              "sampler": ["sampler", 0], "sigmas": ["sigmas", 0],
                              "latent_image": ["cond", 1]}},
        "frames": {"class_type": "VAEDecode",
                   "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "audio": {"class_type": "VAEDecodeAudio",
                  "inputs": {"samples": ["sample", 0], "vae": ["avae", 0]}},
        "video": {"class_type": "CreateVideo",
                  "inputs": {"images": ["frames", 0], "audio": ["audio", 0], "fps": float(FPS)}},
        "save": {"class_type": "SaveVideo",
                 "inputs": {"video": ["video", 0], "filename_prefix": prefix,
                            "format": "mp4", "format.codec": "h264"}},
    }
    if first_frame:
        g["first"] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
        g["cond"]["inputs"]["first_frame"] = ["first", 0]
    if last_frame:
        g["last"] = {"class_type": "LoadImage", "inputs": {"image": last_frame}}
        g["cond"]["inputs"]["last_frame"] = ["last", 0]
    return g
