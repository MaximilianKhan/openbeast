"""beast-video (agents/video_server.py, agents/video/workflow.py).

The engine is a stand-in: nothing here needs a GPU, ComfyUI or the network.
"""
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agents"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import video_server as vs  # noqa: E402
from video import workflow as wf  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
MODELS = ["s3_w4a8.safetensors", "s3.safetensors"]


# ── the workflow ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds,frames", [(1, 39), (3, 73), (5, 124), (10, 243), (15, 362)])
def test_frames_land_on_the_models_grid(seconds, frames):
    assert wf.frames_for(seconds) == frames
    assert (frames - 5) % 17 == 0 and frames >= seconds * 24


def test_defaults_are_sulphurs_recommended_settings():
    p = wf.clean_params({"prompt": "x"}, models=MODELS, default_model=MODELS[0])
    assert (p["cfg"], p["shift_video"], p["shift_audio"], p["sampler"], p["scheduler"]) == \
        (1.0, 12.0, 3.0, "euler", "simple")
    assert (p["width"], p["height"], p["frames"], p["steps"]) == (672, 384, 124, 40)
    assert p["model"] == MODELS[0] and p["seed"] == -1


@pytest.mark.parametrize("bad,word", [
    ({"width": 700}, "multiples of 32"), ({"width": 1536, "height": 1536}, "larger than"),
    ({"width": 64}, "width"), ({"seconds": 60}, "seconds"), ({"steps": 0}, "steps"),
    ({"steps": "many"}, "steps"), ({"cfg": 0.5}, "cfg"), ({"sampler": "nope"}, "sampler"),
    ({"scheduler": "nope"}, "scheduler"), ({"seed": "abc"}, "seed"), ({"seed": -5}, "seed"),
    ({"model": "other.safetensors"}, "not installed"), ({"prompt": "x" * 9000}, "too long"),
    ({"seconds": float("nan")}, "seconds"),
])
def test_bad_parameters_are_refused_by_name(bad, word):
    with pytest.raises(wf.ParamError) as e:
        wf.clean_params(bad, models=MODELS, default_model=MODELS[0])
    assert word in str(e.value)


def _graph(**kw):
    p = wf.clean_params({"prompt": "a fox", "seed": 5}, models=MODELS, default_model=MODELS[0])
    return wf.build(p, text_encoder="te.safetensors", video_vae="v.safetensors",
                    audio_vae="a.safetensors", **kw)


def test_graph_is_wired_end_to_end():
    g = _graph()
    # every link points at a node that exists
    for node in g.values():
        for v in node["inputs"].values():
            if isinstance(v, list):
                assert v[0] in g, v
    assert g["unet"]["inputs"]["unet_name"] == MODELS[0]
    assert g["clip"]["inputs"]["type"] == "minimax"
    assert g["sample"]["inputs"]["latent_image"] == ["cond", 1]
    assert g["guider"]["inputs"]["model"] == ["shift", 0]
    assert g["video"]["inputs"]["fps"] == 24.0
    assert g["save"]["class_type"] == "SaveVideo" and g["save"]["inputs"]["format"] == "mp4"
    assert "first_frame" not in g["cond"]["inputs"] and "first" not in g   # text to video


def test_frames_are_wired_only_when_given():
    g = _graph(first_frame="a.png", last_frame="b.png")
    assert g["first"] == {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
    assert g["cond"]["inputs"]["first_frame"] == ["first", 0]
    assert g["cond"]["inputs"]["last_frame"] == ["last", 0]
    g = _graph(last_frame="b.png")
    assert "first" not in g and g["cond"]["inputs"]["last_frame"] == ["last", 0]


def test_model_list_offers_whole_models_only():
    names = ["s3.safetensors", "s3_w4a8.safetensors", "s3_raw_lora.safetensors",
             "s3_corrector.safetensors", "experimental/s3k8.safetensors", "s3_ref2va.safetensors",
             "notes.txt"]
    assert [n for n in names if vs.usable_model(n)] == ["s3.safetensors", "s3_w4a8.safetensors"]


@pytest.mark.parametrize("head,ext", [
    (b"\x89PNG\r\n\x1a\n....", "png"), (b"\xff\xd8\xff\xe0....", "jpg"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"), (b"GIF89a....", "gif"),
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", None), (b"<svg xmlns=", None), (b"#!/bin/sh\n", None),
])
def test_only_real_pictures_are_accepted(head, ext):
    assert vs.sniff_image(head) == ext


# ── the service, against a stand-in engine ───────────────────────────────────

class FakeEngine:
    def __init__(self, fail=None, hang=False):
        self.fail, self.hang = fail, hang
        self.uploaded, self.graphs, self.interrupted = [], [], 0
        self.up = True

    def alive(self):
        return {"devices": [{"name": "fake", "vram_total": 32 << 30, "vram_free": 30 << 30}]} if self.up else None

    def models(self):
        return list(MODELS) if self.up else []

    def upload_image(self, path, name):
        self.uploaded.append((Path(path).name, name))
        return name

    def submit(self, graph, client_id):
        if self.fail == "submit":
            raise vs.EngineError("the engine rejected the job: bad node")
        self.graphs.append(graph)
        return "p1"

    def progress_stream(self, client_id):
        return None

    def history(self, prompt_id):
        if self.hang:
            return None
        if self.fail == "render":
            return {"status": {"status_str": "error", "messages": [
                ["execution_error", {"exception_type": "OOM", "exception_message": "out of memory"}]]}}
        return {"status": {"status_str": "success", "completed": True},
                "outputs": {"save": {"images": [{"filename": "x_00001_.mp4",
                                                 "subfolder": "beast-video", "type": "output"}]}}}

    def interrupt(self):
        self.interrupted += 1

    def fetch(self, ref, dest):
        Path(dest).write_bytes(b"\0\0\0\x18ftypmp42" + b"v" * 5000)


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAST_VIDEO_DIR", str(tmp_path))
    made = {}

    def make(**kw):
        made["engine"] = FakeEngine(**kw)
        app = vs.create_app(vs.Settings(), engine=made["engine"])
        made["app"] = app
        return TestClient(app), made["engine"]
    yield make
    if "app" in made:
        made["app"].state.worker.stopping = True


def _wait(client, jid, want, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in want:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job stayed {j['status']}: {j}")


def _post(client, params=None, **files):
    return client.post("/api/jobs", data={"params": json.dumps(params or {})},
                       files={k: (f"{k}.png", v, "image/png") for k, v in files.items()} or None)


def test_a_picture_and_a_prompt_become_a_stored_clip(svc, tmp_path):
    client, engine = svc()
    r = _post(client, {"prompt": "the fox turns", "seconds": 3}, first=PNG)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued" and job["has_first"] and job["params"]["seed"] >= 0
    done = _wait(client, job["id"], {"done", "failed"})
    assert done["status"] == "done" and done["video"] == f"files/{job['id']}/clip.mp4"
    # the engine got OUR graph, with the uploaded picture as the first frame
    g = engine.graphs[0]
    assert g["first"]["inputs"]["image"] == engine.uploaded[0][1]
    assert g["cond"]["inputs"]["prompt"] == "the fox turns" and g["cond"]["inputs"]["length"] == 73
    # stored on disk with its settings; playable, seekable and downloadable
    d = tmp_path / "jobs" / job["id"]
    assert (d / "clip.mp4").is_file() and (d / "first.png").read_bytes() == PNG
    assert json.loads((d / "job.json").read_text())["params"]["seed"] == done["params"]["seed"]
    v = client.get(f"/files/{job['id']}/clip.mp4")
    assert v.status_code == 200 and v.headers["content-type"] == "video/mp4"
    assert "attachment" not in v.headers.get("content-disposition", "")
    part = client.get(f"/files/{job['id']}/clip.mp4", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and len(part.content) == 10
    dl = client.get(f"/files/{job['id']}/clip.mp4?download=1")
    assert f'beast-video-{job["id"]}.mp4' in dl.headers["content-disposition"]
    assert client.get(f"/files/{job['id']}/first").headers["content-type"] == "image/png"
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [job["id"]]


def test_text_only_is_text_to_video(svc):
    client, engine = svc()
    job = _post(client, {"prompt": "rain on a tin roof"}).json()
    _wait(client, job["id"], {"done"})
    assert "first" not in engine.graphs[0] and not engine.uploaded


@pytest.mark.parametrize("params,files,code,word", [
    ({}, {}, 400, "first frame, a prompt"),
    ({"prompt": "x", "width": 700}, {}, 400, "multiples of 32"),
    ({"prompt": "x"}, {"first": b"<svg onload=alert(1)>"}, 400, "not a PNG"),
    ({"prompt": "x", "model": "evil.safetensors"}, {}, 400, "not installed"),
])
def test_bad_submissions_leave_nothing_behind(svc, tmp_path, params, files, code, word):
    client, _ = svc()
    r = _post(client, params, **files)
    assert r.status_code == code and word in r.json()["detail"]
    assert not list((tmp_path / "jobs").iterdir())


def test_params_must_be_a_json_object(svc):
    client, _ = svc()
    assert client.post("/api/jobs", data={"params": "[1]"}).status_code == 400
    assert client.post("/api/jobs", data={"params": "{nope"}).status_code == 400


@pytest.mark.parametrize("fail,word", [("submit", "rejected"), ("render", "out of memory")])
def test_an_engine_failure_is_reported_on_the_job(svc, fail, word):
    client, _ = svc(fail=fail)
    job = _post(client, {"prompt": "x"}).json()
    done = _wait(client, job["id"], {"failed", "done"})
    assert done["status"] == "failed" and word in done["error"] and "video" not in done


def test_engine_down_fails_the_job_and_says_how_to_start_it(svc):
    client, engine = svc()
    engine.up = False
    assert client.get("/api/config").json()["engine"] is False
    # with no model list the service cannot check the name; the job then fails clearly
    job = _post(client, {"prompt": "x", "model": "s3_w4a8.safetensors"}).json()
    done = _wait(client, job["id"], {"failed"})
    assert "video.sh up" in done["error"]


def test_stop_interrupts_a_running_clip_and_delete_waits_for_it(svc, tmp_path):
    client, engine = svc(hang=True)
    job = _post(client, {"prompt": "x"}).json()
    _wait(client, job["id"], {"running"})
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 409
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert _wait(client, job["id"], {"cancelled"})["status"] == "cancelled"
    assert engine.interrupted >= 1
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert not (tmp_path / "jobs" / job["id"]).exists()


def test_one_clip_at_a_time_oldest_first(svc):
    client, engine = svc(hang=True)
    a = _post(client, {"prompt": "first"}).json()
    _wait(client, a["id"], {"running"})
    b = _post(client, {"prompt": "second"}).json()
    time.sleep(0.3)
    assert client.get(f"/api/jobs/{b['id']}").json()["status"] == "queued"
    assert len(engine.graphs) == 1
    # a waiting clip can be dropped without touching the one rendering
    assert client.post(f"/api/jobs/{b['id']}/cancel").json()["status"] == "cancelled"
    assert client.get(f"/api/jobs/{a['id']}").json()["status"] == "running"


@pytest.mark.parametrize("path", ["/api/jobs/nope", "/api/jobs/..%2F..%2Fetc", "/files/nope/clip.mp4",
                                  "/files/20260101-000000-abcdef/clip.mp4",
                                  "/files/20260101-000000-abcdef/job.json"])
def test_unknown_jobs_and_files_are_404(svc, path):
    client, _ = svc()
    assert client.get(path).status_code == 404


def test_a_job_cut_off_by_a_restart_is_marked_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAST_VIDEO_DIR", str(tmp_path))
    store = vs.Store(tmp_path / "jobs")
    store.save({"id": "20260101-000000-abcdef", "status": "running", "params": {}, "created": 1})
    app = vs.create_app(vs.Settings(), engine=FakeEngine(), start_worker=False)
    j = TestClient(app).get("/api/jobs/20260101-000000-abcdef").json()
    assert j["status"] == "failed" and "restarted" in j["error"]


def test_the_page_is_served_with_a_policy_and_no_outside_hosts(svc):
    client, _ = svc()
    r = client.get("/")
    assert r.status_code == 200 and "beast-video" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "http://" not in r.text and "https://" not in r.text      # works offline, on the tailnet
    cfg = client.get("/api/config").json()
    assert cfg["default_model"] == "s3_w4a8.safetensors" and cfg["limits"]["fps"] == 24


def test_the_browser_never_blocks_generate_by_itself():
    """The Advanced fields sit in a closed <details>. A number field there that
    fails the browser's own step check (12 with min 0.01, step 0.5) blocked
    the submit with nothing shown: no request, no message (seen 2026-11-27).
    The page validates in script and the server validates again."""
    import re
    page = (REPO / "agents" / "video" / "page.html").read_text()
    assert '<form id="form" novalidate>' in page
    for tag in re.findall(r'<input type="number"[^>]*>', page):
        m = {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', tag)}
        if "value" in m and m.get("step", "any") != "any":
            lo, step, val = float(m.get("min", 0)), float(m["step"]), float(m["value"])
            assert abs((val - lo) / step - round((val - lo) / step)) < 1e-9, tag


def test_a_first_frame_fills_the_custom_size():
    """Choosing a first frame sets Custom width and height to the largest size
    the model takes in the picture's shape, replacing what was there."""
    import json, re, shutil, subprocess
    page = (REPO / "agents" / "video" / "page.html").read_text()
    assert 'id="steps" min="1" max="80" step="1" value="40"' in page
    assert '$("width").value = fitted.width; $("height").value = fitted.height;' in page
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    fn = re.search(r"function fitSize\(.*?\n}\n", page, re.S).group(0)
    lim = {"size_step": wf.SIZE_STEP, "min_side": wf.MIN_SIDE, "max_side": wf.MAX_SIDE,
           "max_pixels": wf.MAX_PIXELS}
    cases = [(1094, 1437), (1088, 1446), (1028, 1530), (1209, 1301), (1086, 1448),
             (1920, 1080), (4000, 3000), (300, 300), (6000, 500)]
    js = fn + f"const lim={json.dumps(lim)};console.log(JSON.stringify({json.dumps(cases)}.map(([w,h])=>{{const f=fitSize(w,h,lim);return [f.width,f.height];}})))"
    got = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
    assert got[:4] == [[832, 1088], [864, 1152], [800, 1184], [960, 1024]]
    for (w, h), (a, b) in zip(cases, got):
        wf.clean_params({"width": a, "height": b})          # the server accepts every one
    assert got[5] == [1344, 768] or abs((got[5][0] / got[5][1]) / (16 / 9) - 1) < 0.011


def test_the_engines_copies_of_a_job_are_deleted(svc, tmp_path, monkeypatch):
    """ComfyUI keeps every picture it is handed and every clip it writes, and
    has no call to delete one: the drive filled with a second copy of each."""
    ein, eout = tmp_path / "engine-input", tmp_path / "engine-output" / "beast-video"
    ein.mkdir()
    eout.mkdir(parents=True)
    monkeypatch.setenv("BEAST_VIDEO_ENGINE_INPUT", str(ein))
    monkeypatch.setenv("BEAST_VIDEO_ENGINE_OUTPUT", str(eout.parent))
    old = "20260101-000000-abcdef"
    stale = [ein / f"beast-video-{old}-first.png", eout / f"{old}_00001_.mp4"]
    kept = [ein / "holiday.png", ein / f"beast-video-{old}-first.png.txt", eout / "smoke_00001_.mp4",
            eout.parent / f"{old}_00001_.mp4"]
    for f in stale + kept:
        f.write_bytes(b"x")
    (ein / "3d").mkdir()
    client, engine = svc()
    assert not any(f.exists() for f in stale)                 # left by an earlier run
    orig_upload, orig_fetch = engine.upload_image, engine.fetch

    def upload(path, name):
        (ein / name).write_bytes(b"x")
        return orig_upload(path, name)

    def fetch(ref, dest):
        (eout / f"{jid['id']}_00001_.mp4").write_bytes(b"x")
        orig_fetch(ref, dest)
    engine.upload_image, engine.fetch = upload, fetch
    jid = {}
    r = _post(client, {"prompt": "a"}, first=PNG)
    jid["id"] = r.json()["id"]
    _wait(client, jid["id"], {"done"})
    end = time.time() + 5
    while time.time() < end and (list(ein.glob("beast-video-*.png")) or list(eout.glob("2*_.mp4"))):
        time.sleep(0.05)
    assert sorted(p.name for p in ein.iterdir()) == ["3d", f"beast-video-{old}-first.png.txt", "holiday.png"]
    assert [p.name for p in eout.iterdir()] == ["smoke_00001_.mp4"]
    assert all(f.exists() for f in kept)
    assert client.get(f"/files/{jid['id']}/clip.mp4").status_code == 200


def test_the_launcher_points_the_service_at_the_engines_real_folders():
    """`A="$A/data" B="$A/engine-input" cmd` gives B the already-changed A.
    The service was started looking in data/engine-input, which does not
    exist, so nothing was deleted (seen on the rig, 2026-11-27)."""
    import subprocess
    sh = (REPO / "scripts" / "video.sh").read_text()
    start = sh.index('BEAST_VIDEO_ENGINE_INPUT="$BEAST_VIDEO_DIR/engine-input"')
    prefix = sh[start:sh.index("setsid nohup", start)]
    out = subprocess.run(["bash", "-c", 'BEAST_VIDEO_DIR=/clips PORT=1 ENGINE_PORT=2 BIND=x\n'
                          + prefix + "env"], capture_output=True, text=True, check=True).stdout
    env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    assert env["BEAST_VIDEO_ENGINE_INPUT"] == "/clips/engine-input"
    assert env["BEAST_VIDEO_ENGINE_OUTPUT"] == "/clips/engine-output"
    assert env["BEAST_VIDEO_DIR"] == "/clips/data"
    assert '--input-directory "$BEAST_VIDEO_DIR/engine-input"' in sh
    assert '--output-directory "$BEAST_VIDEO_DIR/engine-output"' in sh
