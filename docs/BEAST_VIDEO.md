# beast-video

Video with sound from a picture and a prompt, on the RTX 5090 rig, in place of
an LLM. Open a page from any device on the tailnet, give it a first frame, a
prompt and a few settings, and submit. The clip is rendered and stored on the
rig, plays in the page, and downloads as an mp4.

**Status: an experiment** (2026-11-27, branch `feat/beast-video`). It is not
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
| First frame | none | The picture to animate. It is stretched to the chosen size, so pick a size of the same shape (choosing a picture selects the closest preset). Empty means text-to-video. Paste works too |
| Last frame | none | Optional picture to end on |
| Prompt | empty | What happens, what the camera does, what it sounds like |
| Size | 672 × 384 | Up to 1344 × 768, the model's native canvas. Multiples of 32 |
| Length | 5 s | 1 to 15 s. Frame counts are 17k + 5: 5 s is 124 frames |
| Steps | 20 | 8 is a quick draft |
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

## API

All paths are relative to the service root.

| Call | What |
|---|---|
| `GET api/config` | engine state, the card, models, size presets, defaults, limits |
| `POST api/jobs` | multipart: `params` (a JSON object, the fields above) and optional `first`, `last` picture files. 201 with the job |
| `GET api/jobs`, `GET api/jobs/<id>` | jobs, newest first; `status` is `queued`, `running`, `done`, `failed` or `cancelled`, with `phase` and `progress` while running |
| `POST api/jobs/<id>/cancel` | stop a running clip or drop a waiting one |
| `DELETE api/jobs/<id>` | delete a job and its files (409 while it renders) |
| `GET files/<id>/clip.mp4` | the clip; byte ranges work; `?download=1` sends it as an attachment |

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
