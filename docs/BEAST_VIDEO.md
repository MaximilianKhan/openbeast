# beast-video and beast-image

Video with sound from a picture and a prompt, on the RTX 5090 rig, in place of
an LLM. Open a page from any device on the tailnet, give it a first frame, a
prompt and a few settings, and submit. The clip is rendered and stored on the
rig, plays in the page, and downloads as an mp4.

The same page has a second mode, **beast-image**: a picture from a prompt, or
an edit of a picture you give it ([below](#beast-image)). Both modes share one
service, one engine and one queue.

**Status: an experiment** (2026-11-27; image mode 2026-11-28). It is not
wired into `start.sh`, `openbeast.conf` or the dashboard, and it has no
accounts: whoever can reach its port can use it. Keep it on a tailnet you own.

## What runs

```
browser ── tailnet ──▶ beast-video :3010 ──▶ ComfyUI 127.0.0.1:8188 ──▶ GPU
                           │
                           └── <clips>/data/jobs/<id>/{job.json, first.png, clip.mp4}
```

| Piece | What it is | Where |
|---|---|---|
| Model | Sulphur-3, a fine-tune of MiniMax H3. One denoiser makes the video and a stereo sound track together, at 24 fps | `SulphurAI-Sulphur-3/quantized/s3_w4a8.safetensors` (11 GB, the default) |
| Text encoder | Qwen3-VL 32B as repackaged for ComfyUI, NVFP4 | `Comfy-Org-MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` (16 GB) |
| Decoders | video and audio VAE | `unsloth-MiniMax-H3-GGUF/vae/` |
| Engine | ComfyUI, pinned to one commit (v0.46.0), in its own venv, headless on loopback | `~/beast-video/ComfyUI`, `~/beast-video/venv` |
| Service | `agents/video_server.py` and `agents/video/` (the page and the workflow builder) | this repo |
| Launcher | `scripts/video.sh` | this repo |

The model folders are read where they are under `/mnt/9100s4tb/weights`
(`BEAST_VIDEO_WEIGHTS`); nothing is copied. Clips go to
`/mnt/9100s4tb/beast-video/data/jobs` (`BEAST_VIDEO_DIR`).

The engine keeps its own copy of each picture it is handed and each clip it
writes (`engine-input/`, `engine-output/` beside `data/`). The service deletes
a job's copies when the job ends, and any left over when it starts, so the job
folder is the one place a clip lives.

ComfyUI is never published. Its API runs any workflow and its extensions run
code. beast-video builds one fixed graph from validated numbers and strings
(`agents/video/workflow.py`), and the only files it hands the engine are the
uploaded pictures, checked to be PNG, JPEG, WebP or GIF.

## Run it

On the rig, with the LLM stack stopped (`./stop.sh`): the video model needs
nearly the whole card, and `up` refuses while another process holds it.

```bash
scripts/video.sh install                     # once: engine, PyTorch, the service's venv
BEAST_VIDEO_BIND=tailnet scripts/video.sh up # start both; prints the address to open
scripts/video.sh status
scripts/video.sh down                        # stop both and free the card, then ./start.sh
```

The text encoder is the one file not already on the drive:

```bash
scripts/weights-download.sh --include 'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors' \
  https://huggingface.co/Comfy-Org/MiniMax-H3 /mnt/9100s4tb/weights/Comfy-Org-MiniMax-H3
```

Two ways to reach it from another device:

- `BEAST_VIDEO_BIND=tailnet`: the service listens on the rig's Tailscale
  address. `http://<rig>.<tailnet>.ts.net:3010/`, plain HTTP inside the
  tailnet's own encryption. Needs no sudo.
- Default bind (loopback) plus `scripts/video.sh publish`: tailnet-only HTTPS
  on `:8448` through `tailscale serve`. On a rig whose serve config already
  mounts a Unix socket (beast-artifact's does), Tailscale wants root for any
  change: run `sudo tailscale serve --bg --https=8448 http://127.0.0.1:3010`.

Offline: everything above except `install` and the one download works with no
internet. `install` needs GitHub and the PyTorch index once.

## The page

| Field | Default | Notes |
|---|---|---|
| First frame | none | The picture to animate. Choosing one fills Custom width and height (under Advanced) with the largest size the model takes in the picture's shape, every time, so the picture is scaled and not squeezed. Picking a Size preset afterwards overrides that. Empty means text-to-video. Paste works too |
| Last frame | none | Optional picture to end on |
| Prompt | empty | What happens, what the camera does, what it sounds like |
| Size | 672 × 384 | Up to 1344 × 768, the model's native canvas. Multiples of 32 |
| Length | 5 s | 1 to 15 s. Frame counts are 17k + 5: 5 s is 124 frames |
| Steps | 40 | 20 is Sulphur-3's own recommendation and takes half the time; 8 is a quick draft |
| Seed | random | Shown on the result; give it again to repeat a clip |
| Advanced | shift 12 / 3, guidance 1, euler, simple | Sulphur-3's recommended settings. It is guidance-distilled: above 1 the time doubles and the negative prompt applies. The model picker lists the other whole denoisers on the drive |

Clips render one at a time, oldest first. Each card has a player, the
settings, **Download**, **Use these settings**, **Stop** while it renders and
**Delete**.

Measured on the rig (RTX 5090, `s3_w4a8`, 672 × 384, from a first frame):

| Clip | Steps | Time |
|---|---|---|
| 3 s (73 frames) | 8 | 30 s |
| 5 s (124 frames) | 20 | 98 s, 118 s for the first clip after `up` (model load) |

## beast-image

The **Image** tab of the same page. The model is Qwen-Image 2.1 as ChatR Labs'
Uncensored Omni build: text-to-image, and editing or restaging from one or two
reference pictures.

| Piece | File | Where |
|---|---|---|
| Model | `qwen-image-2.1-step90000-BF16/diffusion_pytorch_model.safetensors` (14 GB, the largest build and the default). The picker also lists the FP8, INT8 ConvRot and NVFP4 `-comfy` builds | `chatrlabs-Qwen-Image-2.1-Uncensored-Omni-Quantized/` |
| Text encoder | `qwen3vl_8b_bf16.safetensors` (17.5 GB, the largest; the INT8 and W4A8 ones are beside it) | `Comfy-Org-Qwen-Image-2.1/text_encoders/` |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` | `Comfy-Org-Qwen-Image-2.1/vae/` |
| Turbo LoRA | `chatrlabs-omni-turbo-8step-fp32.safetensors` | `chatrlabs-Qwen-Image-2.1-Uncensored-Omni-LoRA-v0.1/` |

The model repository's GGUF builds need a third-party ComfyUI extension and
its torchao and bitsandbytes builds are for Diffusers; neither is offered.
`BEAST_IMAGE_MODEL`, `BEAST_IMAGE_TEXT_ENCODER`, `BEAST_IMAGE_VAE` and
`BEAST_IMAGE_TURBO_LORA` choose other files.

The files not already on the drive:

```bash
scripts/weights-download.sh --include 'text_encoders/qwen3vl_8b_*' --include 'vae/*' \
  https://huggingface.co/Comfy-Org/Qwen-Image-2.1 /mnt/9100s4tb/weights/Comfy-Org-Qwen-Image-2.1
scripts/weights-download.sh --include 'chatrlabs-omni-turbo-8step-fp32.safetensors' \
  https://huggingface.co/chatrlabs/Qwen-Image-2.1-Uncensored-Omni-LoRA-v0.1 \
  /mnt/9100s4tb/weights/chatrlabs-Qwen-Image-2.1-Uncensored-Omni-LoRA-v0.1
```

`scripts/video.sh up` rewrites the engine's model folder list each time, so a
restart of the engine (`down`, `up`) is what makes new folders visible.

| Field | Default | Notes |
|---|---|---|
| Reference picture | none | Optional. With one, the prompt says what to change or how to restage it. Choosing one fills Custom width and height: the picture's own size when the model takes it, about 1024 × 1024 worth for a small one, 1728 × 1728 worth at most |
| Second reference | none | Optional second picture for the prompt to draw on |
| Prompt | empty | Required |
| Size | 1024 × 1024 | Multiples of 32, up to 2048 a side and 1728 × 1728 in area |
| Steps | 35 | 8 with Turbo |
| Turbo | off | Adds the 8-step LoRA and sets guidance to 1 |
| Advanced | guidance 5, euler, simple | The model's example workflows. An empty negative prompt is sent as a single space, which keeps guidance on |

One mode at a time: the card holds one model, and the queue runs one job at a
time, so a picture queued behind a clip waits for it. The engine swaps models
by itself; the first job after a change of mode pays for the load.

Measured on the rig (RTX 5090, BF16 model, BF16 text encoder):

| Picture | Steps | Time |
|---|---|---|
| 1024 × 1024, Turbo | 8 | 4 s, 16 s for the first after `up` |
| 1024 × 1024, Turbo, from a reference | 8 | 7 s |
| 1024 × 1024 | 35 | 27 s |
| 1088 × 1440 | 35 | 40 s |
| 1536 × 1536 | 35 | 62 s |
| 1952 × 1472 | 35 | 80 s, 12 s with Turbo |
| a 3 s clip (8 steps) straight after a picture | | 37 s against 30 s: the model swap |

Through the API, an image job is `params` with `"kind": "image"` (and
`"turbo": true` if wanted); `first` and `last` carry the reference pictures.
The result is `files/<id>/image.png`.

## API

All paths are relative to the service root.

| Call | What |
|---|---|
| `GET api/config` | engine state, the card, models, size presets, defaults, limits; the same for image mode under `image` |
| `POST api/jobs` | multipart: `params` (a JSON object, the fields above) and optional `first`, `last` picture files. 201 with the job |
| `GET api/jobs`, `GET api/jobs/<id>` | jobs, newest first; `status` is `queued`, `running`, `done`, `failed` or `cancelled`, with `phase` and `progress` while running |
| `POST api/jobs/<id>/cancel` | stop a running clip or drop a waiting one |
| `DELETE api/jobs/<id>` | delete a job and its files (409 while it renders) |
| `GET files/<id>/clip.mp4` | the clip; byte ranges work; `?download=1` sends it as an attachment. A picture is `files/<id>/image.png` |

```bash
curl -X POST http://beast.<tailnet>.ts.net:3010/api/jobs \
  -F 'params={"prompt":"the fox turns and trots off, dry leaves underfoot","seconds":5}' \
  -F first=@fox.png
```

## Not done

- The reference-to-video variant (`s3_ref2va`) and Sulphur's optional
  corrector, which needs third-party ComfyUI code that has not been reviewed.
- Accounts, per-device keys, beast-slot and dashboard rows, a place in
  `start.sh` and the GPU lease (`scripts/gpu-lease.sh`).
- The engine's Python packages are not hash-pinned (`pip install` from the
  PyTorch index and ComfyUI's `requirements.txt` at the pinned commit).
- Tests cover the workflow builder and the service against a stand-in engine
  (`tests/test_video.py`). The real engine was exercised by hand.
