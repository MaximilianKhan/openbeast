#!/usr/bin/env python3
"""beast-video — submit an image and a prompt, get a video clip (docs/BEAST_VIDEO.md).

A small web service for the RTX 5090 rig. It serves one page and a JSON API,
keeps a queue of jobs, runs them ONE AT A TIME on a ComfyUI engine that listens
on loopback, and stores every finished clip with its settings:

    browser ── tailnet HTTPS ──▶ beast-video :3010 ──▶ ComfyUI 127.0.0.1:8188 ──▶ GPU
                                     │
                                     └── $BEAST_VIDEO_DIR/jobs/<id>/{job.json, first.png, clip.mp4}

ComfyUI itself is never published: its API runs any workflow and its
extensions run code, so this service is the only thing `tailscale serve`
points at. This service builds one fixed graph (agents/video/workflow.py) from
validated numbers and strings, and the only files it hands the engine are the
uploaded pictures.

Who may use it: whoever can reach the port. It binds loopback and is meant to
be published tailnet-only, for one owner. It has no accounts and no keys; do
not publish it on a tailnet you share.

Environment (all optional):
  BEAST_VIDEO_DIR         where jobs and clips are kept   (default ~/beast-video/data)
  BEAST_VIDEO_COMFY       the engine's URL                (default http://127.0.0.1:8188)
  BEAST_VIDEO_PORT        this service's port             (default 3010)
  OPENBEAST_BIND          bind address                    (default 127.0.0.1)
  BEAST_VIDEO_MODEL       default denoiser file           (default s3_w4a8.safetensors)
  BEAST_VIDEO_TEXT_ENCODER / BEAST_VIDEO_VAE / BEAST_VIDEO_AUDIO_VAE
                          the other three model files (names as ComfyUI lists them)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import socket
import struct
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from video import workflow as wf  # noqa: E402

HERE = Path(__file__).resolve().parent
PAGE = HERE / "video" / "page.html"

MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_QUEUED = 20
JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
#: leading bytes of the picture formats ComfyUI's LoadImage reads
_IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"),
                (b"RIFF", "webp"), (b"GIF8", "gif"))


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


class Settings:
    def __init__(self) -> None:
        self.data = Path(_env("BEAST_VIDEO_DIR", str(Path.home() / "beast-video" / "data")))
        self.comfy = _env("BEAST_VIDEO_COMFY", "http://127.0.0.1:8188").rstrip("/")
        self.model = _env("BEAST_VIDEO_MODEL", "s3_w4a8.safetensors")
        self.text_encoder = _env("BEAST_VIDEO_TEXT_ENCODER",
                                 "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
        self.video_vae = _env("BEAST_VIDEO_VAE", "minimax_h3_video_vae_fp16.safetensors")
        self.audio_vae = _env("BEAST_VIDEO_AUDIO_VAE", "minimax_h3_audio_vae_fp32.safetensors")
        self.jobs = self.data / "jobs"


def sniff_image(head: bytes) -> str | None:
    """The file extension for a picture's first bytes, or None if it is not one."""
    for magic, ext in _IMAGE_MAGIC:
        if head.startswith(magic):
            if ext == "webp" and head[8:12] != b"WEBP":
                continue
            return ext
    return None


def usable_model(name: str) -> bool:
    """A denoiser the page should offer. ComfyUI lists every .safetensors under
    its diffusion_models folders, and a model repo also ships LoRAs,
    correctors and experiments there: only top-level, whole models count.
    The reference-to-video variant (ref2va) is a different graph, not built here."""
    low = name.lower()
    return ("/" not in name and "\\" not in name and low.endswith(".safetensors")
            and not any(w in low for w in ("lora", "corrector", "ref2va")))


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


# --------------------------------------------------------------------------
# The engine: ComfyUI over loopback HTTP, plus its websocket for step counts.
# --------------------------------------------------------------------------

class EngineError(RuntimeError):
    pass


class Engine:
    """Everything this service asks of ComfyUI. Tests replace it with a fake."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.http = httpx.Client(timeout=httpx.Timeout(30.0, read=120.0))

    def alive(self) -> dict | None:
        try:
            r = self.http.get(self.base + "/system_stats", timeout=4.0)
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    def models(self) -> list[str]:
        """Denoiser files ComfyUI can see (its diffusion_models folders)."""
        try:
            r = self.http.get(self.base + "/object_info/UNETLoader", timeout=20.0)
            names = r.json()["UNETLoader"]["input"]["required"]["unet_name"][0]
            if isinstance(names, str):          # newer shape: ["COMBO", {"options": [...]}]
                names = r.json()["UNETLoader"]["input"]["required"]["unet_name"][1]["options"]
            return [n for n in names if isinstance(n, str) and usable_model(n)]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            return []

    def upload_image(self, path: Path, name: str) -> str:
        with open(path, "rb") as fh:
            r = self.http.post(self.base + "/upload/image",
                               files={"image": (name, fh, "application/octet-stream")},
                               data={"overwrite": "true", "type": "input"})
        if r.status_code != 200:
            raise EngineError(f"the engine refused the picture (HTTP {r.status_code})")
        got = r.json()
        sub = got.get("subfolder") or ""
        return f"{sub}/{got['name']}" if sub else got["name"]

    def submit(self, graph: dict, client_id: str) -> str:
        r = self.http.post(self.base + "/prompt", json={"prompt": graph, "client_id": client_id})
        if r.status_code != 200:
            try:
                body = r.json()
                msg = (body.get("error") or {}).get("message") or ""
                for node, errs in (body.get("node_errors") or {}).items():
                    for e in errs.get("errors") or []:
                        msg += f" [{node}: {e.get('message')} {e.get('details') or ''}]"
            except ValueError:
                msg = r.text[:300]
            raise EngineError(f"the engine rejected the job: {msg.strip() or r.status_code}")
        return r.json()["prompt_id"]

    def history(self, prompt_id: str) -> dict | None:
        r = self.http.get(f"{self.base}/history/{prompt_id}")
        if r.status_code != 200:
            return None
        return r.json().get(prompt_id)

    def interrupt(self) -> None:
        try:
            self.http.post(self.base + "/interrupt", json={})
        except httpx.HTTPError:
            pass

    def fetch(self, ref: dict, dest: Path) -> None:
        q = urllib.parse.urlencode({"filename": ref["filename"],
                                    "subfolder": ref.get("subfolder") or "",
                                    "type": ref.get("type") or "output"})
        tmp = dest.with_suffix(dest.suffix + ".part")
        with self.http.stream("GET", f"{self.base}/view?{q}", timeout=600.0) as r:
            if r.status_code != 200:
                raise EngineError(f"could not read the finished clip (HTTP {r.status_code})")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        os.replace(tmp, dest)

    def progress_stream(self, client_id: str):
        """A ProgressSocket, or None when the websocket cannot be opened."""
        try:
            u = urllib.parse.urlparse(self.base)
            return ProgressSocket(u.hostname or "127.0.0.1", u.port or 80, client_id)
        except OSError:
            return None


class ProgressSocket:
    """Read-only websocket client for ComfyUI's /ws (stdlib only).

    ComfyUI reports sampling steps only there. This reads server text frames
    (never masked, RFC 6455 §5.1), answers pings, and ignores everything else.
    Loopback, plain ws://, one connection per job: a library would be more
    dependency than the 60 lines it replaces.
    """

    def __init__(self, host: str, port: int, client_id: str) -> None:
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.sock.sendall((
            f"GET /ws?clientId={client_id} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("websocket handshake closed")
            head += chunk
            if len(head) > 65536:
                raise OSError("websocket handshake too long")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError("websocket upgrade refused")
        want = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if want not in head:
            raise OSError("websocket accept key mismatch")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.sock.settimeout(1.0)

    def _fill(self) -> bool:
        """Read what has arrived. False when nothing came within the timeout."""
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return False
        if not chunk:
            raise OSError("websocket closed")
        self.buf += chunk
        return True

    def _frame(self) -> tuple[int, bytes] | None:
        """One whole frame off the front of the buffer, or None if it is not all here."""
        if len(self.buf) < 2:
            return None
        opcode, ln, at = self.buf[0] & 0x0F, self.buf[1] & 0x7F, 2
        if ln == 126:
            if len(self.buf) < 4:
                return None
            ln, at = struct.unpack(">H", self.buf[2:4])[0], 4
        elif ln == 127:
            if len(self.buf) < 10:
                return None
            ln, at = struct.unpack(">Q", self.buf[2:10])[0], 10
        if len(self.buf) < at + ln:
            return None
        payload, self.buf = self.buf[at:at + ln], self.buf[at + ln:]
        return opcode, payload

    def _send(self, opcode: int, payload: bytes = b"") -> None:
        mask = secrets.token_bytes(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload[:125]))
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(body)]) + mask + body)

    def read(self) -> dict | None:
        """The next JSON message, or None if none is complete within a second."""
        frame = self._frame()
        if frame is None:
            if not self._fill():
                return None
            frame = self._frame()
            if frame is None:
                return None
        opcode, payload = frame
        if opcode == 0x9:
            self._send(0xA, payload)
            return None
        if opcode == 0x8:
            raise OSError("websocket closed by the engine")
        if opcode != 0x1:                   # binary previews and the like
            return None
        try:
            return json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            return None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Jobs on disk, and the one worker that runs them.
# --------------------------------------------------------------------------

class Store:
    """jobs/<id>/job.json plus the job's files. The disk is the only state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, job_id: str) -> Path:
        if not JOB_ID_RE.match(job_id):
            raise HTTPException(404, "no such job")
        return self.root / job_id

    def save(self, job: dict) -> None:
        with self.lock:
            d = self.root / job["id"]
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "job.json.tmp"
            tmp.write_text(json.dumps(job, indent=2) + "\n")
            os.replace(tmp, d / "job.json")

    def load(self, job_id: str) -> dict | None:
        try:
            return json.loads((self.dir(job_id) / "job.json").read_text())
        except (OSError, ValueError):
            return None

    def all(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir(), reverse=True) if self.root.is_dir() else []:
            if d.is_dir() and JOB_ID_RE.match(d.name):
                try:
                    out.append(json.loads((d / "job.json").read_text()))
                except (OSError, ValueError):
                    continue
        return out

    def update(self, job_id: str, **changes) -> dict | None:
        with self.lock:
            job = self.load(job_id)
            if job is None:
                return None
            job.update(changes)
            self.save(job)
            return job

    def delete(self, job_id: str) -> None:
        with self.lock:
            shutil.rmtree(self.dir(job_id), ignore_errors=True)


def public(job: dict) -> dict:
    """What the page sees of a job."""
    out = {k: job.get(k) for k in ("id", "status", "params", "created", "started", "finished",
                                    "error", "progress", "phase", "seconds_taken", "has_first",
                                    "has_last", "bytes")}
    if job.get("status") == "done":
        out["video"] = f"files/{job['id']}/clip.mp4"
    return out


class Worker(threading.Thread):
    """Runs queued jobs oldest first, one at a time: the card holds one model."""

    def __init__(self, cfg: Settings, store: Store, engine: Engine) -> None:
        super().__init__(daemon=True, name="beast-video-worker")
        self.cfg, self.store, self.engine = cfg, store, engine
        self.wake = threading.Event()
        self.cancel: set[str] = set()
        self.running: str | None = None
        self.stopping = False

    def recover(self) -> None:
        """A job that was running when the service stopped did not finish."""
        for job in self.store.all():
            if job.get("status") == "running":
                self.store.update(job["id"], status="failed", finished=time.time(),
                                  error="the service restarted while this clip was rendering")

    def run(self) -> None:
        while not self.stopping:
            waiting = [j for j in self.store.all() if j.get("status") == "queued"]
            job = min(waiting, key=lambda j: (j.get("created") or 0, j["id"])) if waiting else None
            if job is None:
                self.wake.wait(2.0)
                self.wake.clear()
                continue
            self.running = job["id"]
            try:
                self.render(job)
            except Exception as e:  # noqa: BLE001 — one bad job must not stop the queue
                self.store.update(job["id"], status="failed", finished=time.time(),
                                  error=str(e)[:600] or e.__class__.__name__)
            finally:
                self.cancel.discard(job["id"])
                self.running = None

    def render(self, job: dict) -> None:
        jid, d = job["id"], self.store.dir(job["id"])
        if jid in self.cancel:
            self.store.update(jid, status="cancelled", finished=time.time())
            return
        if self.engine.alive() is None:
            raise EngineError("the video engine is not running on the rig (scripts/video.sh up)")
        started = time.time()
        self.store.update(jid, status="running", started=started, phase="loading the model",
                          progress=0.0)
        params = dict(job["params"])
        first = last = None
        for key, attr in (("first", "has_first"), ("last", "has_last")):
            if job.get(attr):
                src = next(d.glob(f"{key}.*"), None)
                if src is None:
                    raise EngineError(f"the {key} frame is missing from the job folder")
                name = self.engine.upload_image(src, f"beast-video-{jid}-{src.name}")
                first, last = (name, last) if key == "first" else (first, name)
        graph = wf.build(params, text_encoder=self.cfg.text_encoder, video_vae=self.cfg.video_vae,
                         audio_vae=self.cfg.audio_vae, first_frame=first, last_frame=last,
                         prefix=f"beast-video/{jid}")
        client_id = secrets.token_hex(8)
        stream = self.engine.progress_stream(client_id)
        try:
            prompt_id = self.engine.submit(graph, client_id)
            self.store.update(jid, prompt_id=prompt_id)
            result = self._wait(jid, prompt_id, stream)
        finally:
            if stream is not None:
                stream.close()
        if result is None:
            self.store.update(jid, status="cancelled", finished=time.time(), phase=None)
            return
        ref = None
        for out in (result.get("outputs") or {}).values():
            for group in out.values():
                for item in group if isinstance(group, list) else []:
                    if isinstance(item, dict) and str(item.get("filename", "")).endswith(".mp4"):
                        ref = item
        if ref is None:
            raise EngineError("the engine finished without producing a clip")
        self.store.update(jid, phase="saving the clip")
        clip = d / "clip.mp4"
        self.engine.fetch(ref, clip)
        done = time.time()
        self.store.update(jid, status="done", finished=done, phase=None, progress=1.0,
                          seconds_taken=round(done - started, 1), bytes=clip.stat().st_size)

    #: what the user reads while a node runs
    PHASES = {"clip": "reading the prompt", "cond": "reading the prompt and the picture",
              "neg": "reading the prompt", "unet": "loading the model",
              "sample": "rendering", "frames": "decoding the frames",
              "audio": "decoding the sound", "video": "writing the clip",
              "save": "writing the clip"}

    def _wait(self, jid: str, prompt_id: str, stream) -> dict | None:
        """Block until the engine finishes this prompt. None means cancelled."""
        last_poll = last_save = 0.0
        phase, progress = "loading the model", 0.0
        while True:
            if jid in self.cancel:
                self.engine.interrupt()
                return None
            msg = None
            if stream is not None:
                try:
                    msg = stream.read()
                except OSError:
                    stream = None
            else:
                time.sleep(1.0)
            if msg:
                data = msg.get("data") or {}
                if data.get("prompt_id") in (None, prompt_id):
                    if msg.get("type") == "executing" and data.get("node"):
                        phase = self.PHASES.get(str(data["node"]), phase)
                    elif msg.get("type") == "progress" and data.get("max"):
                        if phase == "rendering":
                            progress = min(0.99, float(data["value"]) / float(data["max"]))
            now = time.time()
            if now - last_save > 1.0:
                self.store.update(jid, phase=phase, progress=round(progress, 3))
                last_save = now
            if now - last_poll > 2.0:
                last_poll = now
                hist = self.engine.history(prompt_id)
                if hist:
                    status = hist.get("status") or {}
                    if status.get("status_str") == "error":
                        why = "the engine failed while rendering"
                        for kind, detail in status.get("messages") or []:
                            if kind == "execution_error":
                                why = (f"{detail.get('exception_type', 'error')}: "
                                       f"{str(detail.get('exception_message', '')).strip()[:400]}")
                            elif kind == "execution_interrupted":
                                return None
                        raise EngineError(why)
                    if status.get("completed") or hist.get("outputs"):
                        return hist


# --------------------------------------------------------------------------
# The web app.
# --------------------------------------------------------------------------

def create_app(cfg: Settings | None = None, engine: Engine | None = None,
               start_worker: bool = True) -> FastAPI:
    cfg = cfg or Settings()
    store = Store(cfg.jobs)
    engine = engine or Engine(cfg.comfy)
    worker = Worker(cfg, store, engine)
    worker.recover()
    if start_worker:
        worker.start()

    app = FastAPI(title="beast-video", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.worker, app.state.store, app.state.cfg = worker, store, cfg

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith("/files/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.get("/", response_class=HTMLResponse)
    def page():
        return HTMLResponse(PAGE.read_text(), headers={
            "Content-Security-Policy":
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
                "media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'"})

    @app.get("/api/config")
    def config():
        models = engine.models()
        stats = engine.alive()
        gpu = None
        if stats and stats.get("devices"):
            dev = stats["devices"][0]
            gpu = {"name": dev.get("name"), "vram_total": dev.get("vram_total"),
                   "vram_free": dev.get("vram_free")}
        default = cfg.model if cfg.model in models else (models[0] if models else "")
        return {"engine": stats is not None, "gpu": gpu, "models": models,
                "default_model": default, "defaults": wf.DEFAULTS,
                "sizes": [{"label": s[0], "width": s[1], "height": s[2]} for s in wf.SIZE_PRESETS],
                "samplers": wf.SAMPLERS, "schedulers": wf.SCHEDULERS,
                "limits": {"min_seconds": wf.MIN_SECONDS, "max_seconds": wf.MAX_SECONDS,
                           "fps": wf.FPS, "size_step": wf.SIZE_STEP, "min_side": wf.MIN_SIDE,
                           "max_side": wf.MAX_SIDE, "max_pixels": wf.MAX_PIXELS,
                           "max_image_mb": MAX_IMAGE_BYTES // (1024 * 1024)}}

    def _keep_image(upload: UploadFile | None, d: Path, stem: str) -> bool:
        if upload is None or not (upload.filename or ""):
            return False
        head = upload.file.read(16)
        if not head:
            return False
        ext = sniff_image(head)
        if ext is None:
            raise HTTPException(400, f"the {stem} frame is not a PNG, JPEG, WebP or GIF picture")
        size = len(head)
        with open(d / f"{stem}.{ext}", "wb") as fh:
            fh.write(head)
            while chunk := upload.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    fh.close()
                    raise HTTPException(413, f"the {stem} frame is larger than "
                                             f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB")
                fh.write(chunk)
        return True

    @app.post("/api/jobs")
    def submit(params: str = Form("{}"), first: UploadFile | None = File(None),
               last: UploadFile | None = File(None)):
        try:
            raw = json.loads(params)
            if not isinstance(raw, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "params must be a JSON object") from None
        models = engine.models()
        default = cfg.model if cfg.model in models else (models[0] if models else "")
        try:
            clean = wf.clean_params(raw, models=models or None, default_model=default)
        except wf.ParamError as e:
            raise HTTPException(400, str(e)) from None
        if sum(1 for j in store.all() if j.get("status") == "queued") >= MAX_QUEUED:
            raise HTTPException(429, f"{MAX_QUEUED} clips are already waiting; let some finish")
        if clean["seed"] < 0:
            clean["seed"] = random.SystemRandom().randrange(0, 2 ** 48)
        jid = new_job_id()
        d = store.root / jid
        d.mkdir(parents=True)
        try:
            has_first = _keep_image(first, d, "first")
            has_last = _keep_image(last, d, "last")
            if not has_first and not has_last and not clean["prompt"]:
                raise HTTPException(400, "give it a first frame, a prompt, or both")
        except HTTPException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        job = {"id": jid, "status": "queued", "params": clean, "created": time.time(),
               "has_first": has_first, "has_last": has_last, "progress": 0.0}
        store.save(job)
        worker.wake.set()
        return JSONResponse(public(job), status_code=201)

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": [public(j) for j in store.all()]}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = store.load(job_id)
        if j is None:
            raise HTTPException(404, "no such job")
        return public(j)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        j = store.load(job_id)
        if j is None:
            raise HTTPException(404, "no such job")
        if j["status"] == "queued":
            j = store.update(job_id, status="cancelled", finished=time.time())
        elif j["status"] == "running":
            worker.cancel.add(job_id)
        return public(j)

    @app.delete("/api/jobs/{job_id}")
    def delete(job_id: str):
        j = store.load(job_id)
        if j is None:
            raise HTTPException(404, "no such job")
        if j["status"] == "running":
            raise HTTPException(409, "this clip is rendering; stop it first")
        store.delete(job_id)
        return {"deleted": job_id}

    @app.api_route("/files/{job_id}/{name}", methods=["GET", "HEAD"])
    def files(job_id: str, name: str, download: int = 0):
        d = store.dir(job_id)
        if name == "clip.mp4":
            path, media = d / "clip.mp4", "video/mp4"
        elif name in ("first", "last"):
            path = next(d.glob(f"{name}.*"), d / "missing")
            media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
                     "gif": "image/gif"}.get(path.suffix.lstrip("."), "application/octet-stream")
        else:
            raise HTTPException(404, "no such file")
        if not path.is_file():
            raise HTTPException(404, "no such file")
        headers = {"Cache-Control": "private, max-age=3600"}
        if download:
            return FileResponse(path, media_type=media, headers=headers,
                                filename=f"beast-video-{job_id}{path.suffix}")
        return FileResponse(path, media_type=media, headers=headers)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "engine": engine.alive() is not None, "running": worker.running}

    return app


def main() -> None:
    import uvicorn
    host = _env("OPENBEAST_BIND", "127.0.0.1")
    port = int(_env("BEAST_VIDEO_PORT", "3010"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
