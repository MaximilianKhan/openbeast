"""The Basalt engine adapter (scripts/engines/basalt.sh), the RTX 5090 default.

No docker and no GPU needed: a fake docker client records every argv and
answers the two questions the adapter asks it (the network's NAT option and
the sandbox probe's output). What is held here is the Strata report §15.8
list: every hardening flag is on the server's command line and nothing
loosens it; the probe proves the SAME sandbox before the server starts and a
probe that reaches out stops the launch; the key travels as a 0600 file,
never on argv; the profile is exactly the measured INK-T2-STOCK serve and
its weights pin; the env image is pinned; the PR #632 patch hook applies a
present patch and only then. A real build and serve are checked on the rig
at the switch (docs/TODO.md).

Run: python3 -m pytest tests/test_engine_basalt.py -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADAPTER = ROOT / "scripts" / "engines" / "basalt.sh"
PROFILE = ROOT / "scripts" / "engines" / "basalt" / "profiles" / "inkling2-mini-stock.env"
DOCKERFILE = ROOT / "scripts" / "engines" / "basalt" / "Dockerfile"
REGISTRY = ROOT / "scripts" / "weights.registry"
WEIGHTS = ROOT / "evals" / "boards" / "v5" / "weights.json"
BOARD = ROOT / "evals" / "boards" / "v5" / "board.json"
BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")
COMMIT = "400b411c58031d064812ea21a259b2381acdaa3e"
PACK = "Inkling-2-Mini-NVFP4-MX8.basalt"
PACK_BYTES = 19606374189
IMAGE = "sha256:" + "ab" * 32
KEY = "s3cret-key-value"

PROBE_CLOSED = ("dns example.com FAIL gaierror\\ndns huggingface.co FAIL gaierror\\n"
                "dns github.com FAIL gaierror\\ntcp 1.1.1.1 443 FAIL TimeoutError\\n"
                "tcp 8.8.8.8 53 FAIL TimeoutError\\n")
PROBE_OPEN = ("dns example.com RESOLVED\\ndns huggingface.co FAIL gaierror\\n"
              "dns github.com FAIL gaierror\\ntcp 1.1.1.1 443 FAIL TimeoutError\\n"
              "tcp 8.8.8.8 53 FAIL TimeoutError\\n")


@pytest.fixture
def rig(tmp_path):
    """A fake installed build, a sparse pack at the pinned size, fake docker and nvidia-smi."""
    opt = tmp_path / "opt"
    build = opt / "basalt-v0.5.7"
    (build / "bin").mkdir(parents=True)
    (build / "bin" / "basalt").write_text("#!/bin/sh\n")
    (build / "bin" / "basalt").chmod(0o755)
    (build / "OPENBEAST_BUILD").write_text(
        f"VERSION=v0.5.7\nCOMMIT={COMMIT}\nPATCH_TAG=\nPATCHES=\nIMAGE_ID={IMAGE}\n")
    (opt / "basalt").symlink_to(build)
    w = tmp_path / "weights"
    w.mkdir()
    with open(w / PACK, "wb") as f:
        f.truncate(PACK_BYTES)            # sparse: the size check, not the bytes
    b = tmp_path / "bin"
    b.mkdir()
    (b / "nvidia-smi").write_text("#!/bin/sh\necho 12.0\n")
    log = tmp_path / "argv.log"
    state = tmp_path / "state"
    state.mkdir()
    # One argv per record, NUL-separated words, so a test can read exact words.
    (b / "docker").write_text(f"""#!/bin/bash
{{ for a in "$@"; do printf '%s\\0' "$a"; done; printf '\\n--END--\\n'; }} >> {log}
case "$*" in
  "network inspect -f"*) cat {state}/masq 2>/dev/null || echo false ;;
  "network inspect "*) [[ -f {state}/nonet ]] && exit 1; exit 0 ;;
  *"--entrypoint python3"*) printf "$(cat {state}/probe)" ;;
esac
exit 0
""")
    for f in b.iterdir():
        f.chmod(0o755)
    (state / "probe").write_text(PROBE_CLOSED)
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("OPENBEAST_")}
    env.update({
        "PATH": f"{b}:{env.get('PATH', '/usr/bin:/bin')}", "REPO_DIR": str(ROOT),
        "OB_ENGINE_HOST_CLASS": "rig", "OB_ENGINE_CONF": str(tmp_path / "openbeast.conf"),
        "OB_ENGINE_BASALT_OPT": str(opt), "OPENBEAST_WEIGHTS_DIR": str(w),
        "OB_ENGINE_DOCKER": str(b / "docker"), "RUN_DIR": str(tmp_path / "run"),
        "INFERENCE_URL": "http://127.0.0.1:8080", "OPENBEAST_ENGINE_STATE_DIR": str(tmp_path / "engines"),
    })
    return {"tmp": tmp_path, "env": env, "log": log, "state": state, "build": build, "weights": w}


def _sh(rig, script, **extra):
    env = dict(rig["env"], **extra)
    return subprocess.run([BASH, "-c", f'source "$REPO_DIR/scripts/lib/engine.sh"; {script}'],
                          capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)


def _calls(rig) -> list[list[str]]:
    if not rig["log"].exists():
        return []
    out = []
    for rec in rig["log"].read_text().split("\n--END--\n"):
        rec = rec.lstrip("\n")
        if rec:
            out.append(rec.split("\0")[:-1])
    return out


def _server(calls):
    runs = [c for c in calls if c[:1] == ["run"] and "--init" in c]
    assert len(runs) == 1, calls
    return runs[0]


def _probe(calls):
    runs = [c for c in calls if c[:1] == ["run"] and "python3" in c and "--entrypoint" in c]
    assert len(runs) == 1, calls
    return runs[0]


def _opt(argv, flag):
    """Every value of a flag (`--x v`)."""
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


HARDENING = [("--network", "openbeast-basalt-net"), ("--dns", "127.0.0.1"), ("--cap-drop", "ALL"),
             ("--security-opt", "no-new-privileges"), ("--pids-limit", "256"), ("--memory", "32g"),
             ("--memory-swap", "32g"), ("--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=8g"),
             ("-e", "HOME=/tmp")]
LOOSENERS = ("--privileged", "--cap-add", "--network=host", "--ipc=host", "--pid=host", "--userns=host",
             "seccomp=unconfined", "apparmor=unconfined", "--device", "--volumes-from")


def _assert_hardened(argv, uid=None):
    for flag, value in HARDENING:
        assert value in _opt(argv, flag), (flag, value)
    assert "--read-only" in argv
    assert _opt(argv, "--network") == ["openbeast-basalt-net"]       # one network, never host
    u = _opt(argv, "--user")
    assert len(u) == 1 and re.fullmatch(r"[1-9]\d*:[1-9]\d*", u[0]), u
    if uid is not None:
        assert u[0].startswith(f"{uid}:")
    for bad in LOOSENERS:
        assert not any(bad in a for a in argv), bad
    tmpfs = _opt(argv, "--tmpfs")
    assert all("noexec" in t and ",exec" not in t for t in tmpfs)


# --- the server's command line --------------------------------------------------------

def test_print_has_every_hardening_flag_and_the_measured_serve(rig):
    r = _sh(rig, "ENG_PRINT=1 ob_engine_call basalt launch", LLAMA_API_KEY=KEY)
    assert r.returncode == 0, r.stderr
    assert KEY not in r.stdout                                         # the key's VALUE never prints
    for s in ("--dns 127.0.0.1", "--cap-drop ALL", "no-new-privileges", "--read-only", "-p 127.0.0.1:8080:8080",
              "--pids-limit 256", "--memory 32g", "noexec", "--api-key-file /run/basalt.key",
              "--alias inkling-2-mini", "--parallel 1", "--max-context 262144", "--kv fp8", "--answer-room auto"):
        assert s in r.stdout.replace("\\,", ","), s


def test_launch_runs_the_probe_then_a_hardened_server(rig):
    r = _sh(rig, "ob_engine_call basalt launch", LLAMA_API_KEY=KEY)
    assert r.returncode == 0, r.stderr + r.stdout
    calls = _calls(rig)
    srv, probe = _server(calls), _probe(calls)
    assert calls.index(probe) < calls.index(srv)                       # proven before serving
    _assert_hardened(srv, os.getuid() or None)
    _assert_hardened(probe, os.getuid() or None)
    # The probe proves the server's sandbox: the same network and isolation words.
    for flag, _ in HARDENING:
        assert _opt(probe, flag) == _opt(srv, flag), flag
    # Port on loopback only; GPU by the toolkit; nothing else published.
    assert _opt(srv, "-p") == ["127.0.0.1:8080:8080"]
    assert _opt(srv, "--gpus") == ["device=0"]
    # Mounts: the build, the pack and the key, each read-only; no host home.
    vols = _opt(srv, "-v")
    assert len(vols) == 3 and all(v.endswith(":ro") for v in vols), vols
    home = os.path.expanduser("~")
    for v in vols:
        src = v.split(":")[0]
        assert src != home and not (home + "/").startswith(src.rstrip("/") + "/"), v
    assert any(v.endswith(f":/models/{PACK}:ro") for v in vols)
    assert f"{rig['build'].resolve()}:/opt/basalt:ro" in vols         # the resolved build, not the link
    # The image is the build's recorded env image, by id.
    assert IMAGE in srv and IMAGE in probe
    # The serve line is the measured one.
    tail = srv[srv.index(IMAGE) + 1:]
    assert tail[:2] == ["/opt/basalt/bin/basalt", "serve"]
    for flag, value in (("--model", f"/models/{PACK}"), ("--alias", "inkling-2-mini"), ("--parallel", "1"),
                        ("--max-context", "262144"), ("--kv", "fp8"), ("--answer-room", "auto"),
                        ("--api-key-file", "/run/basalt.key")):
        assert _opt(tail, flag) == [value], flag


def test_the_key_is_a_0600_file_never_argv(rig):
    r = _sh(rig, "ob_engine_call basalt launch", LLAMA_API_KEY=KEY)
    assert r.returncode == 0, r.stderr
    assert KEY not in rig["log"].read_text()
    keyf = rig["tmp"] / "run" / "basalt-api-key"
    assert keyf.read_text().strip() == KEY
    assert stat.S_IMODE(keyf.stat().st_mode) == 0o600


def test_no_key_means_no_key_mount(rig):
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode == 0, r.stderr
    srv = _server(_calls(rig))
    assert "--api-key-file" not in srv and not any("/run/basalt.key" in a for a in srv)


def test_a_probe_that_resolves_stops_the_launch(rig):
    (rig["state"] / "probe").write_text(PROBE_OPEN)
    r = _sh(rig, "ob_engine_call basalt launch", LLAMA_API_KEY=KEY)
    assert r.returncode != 0
    assert "REFUSING" in r.stderr and "RESOLVED" in r.stderr
    assert not [c for c in _calls(rig) if "--init" in c]               # the server never ran
    assert not (rig["tmp"] / "run" / "basalt-api-key").exists()


def test_a_short_probe_output_stops_the_launch(rig):
    (rig["state"] / "probe").write_text("dns example.com FAIL gaierror\\n")
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode != 0 and not [c for c in _calls(rig) if "--init" in c]


def test_the_network_is_created_without_nat(rig):
    (rig["state"] / "nonet").write_text("")
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode == 0, r.stderr
    create = [c for c in _calls(rig) if c[:2] == ["network", "create"]]
    assert len(create) == 1
    assert "com.docker.network.bridge.enable_ip_masquerade=false" in _opt(create[0], "-o")


def test_a_network_with_nat_is_refused(rig):
    (rig["state"] / "masq").write_text("true\n")
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode != 0 and "NAT" in r.stderr
    assert not [c for c in _calls(rig) if c[:1] == ["run"]]


@pytest.mark.parametrize("url", ["http://0.0.0.0:8080", "http://192.168.1.5:8080", "http://[::]:8080"])
def test_only_loopback_is_published(rig, url):
    r = _sh(rig, "ENG_PRINT=1 ob_engine_call basalt launch", INFERENCE_URL=url)
    assert r.returncode != 0 and "127.0.0.1 only" in r.stderr


def test_root_is_refused(rig):
    r = _sh(rig, "ENG_PRINT=1 ob_engine_call basalt launch", OB_ENGINE_BASALT_UID="0")
    assert r.returncode != 0 and "non-root" in r.stderr


@pytest.mark.parametrize("key,value", [("OPENBEAST_ENGINE_BASALT_PIDS", "0"), ("OPENBEAST_ENGINE_BASALT_PIDS", "-1"),
                                       ("OPENBEAST_ENGINE_BASALT_MEMORY", "0"), ("OPENBEAST_ENGINE_BASALT_MEMORY", "unlimited"),
                                       ("OPENBEAST_ENGINE_BASALT_GPUS", "all --privileged")])
def test_limits_cannot_be_switched_off(rig, key, value):
    r = _sh(rig, "ENG_PRINT=1 ob_engine_call basalt launch", **{key: value})
    assert r.returncode != 0


def test_a_wrong_size_pack_or_gpu_refuses(rig):
    with open(rig["weights"] / PACK, "wb") as f:
        f.truncate(PACK_BYTES - 1)
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode != 0 and "pinned size" in r.stderr
    with open(rig["weights"] / PACK, "wb") as f:
        f.truncate(PACK_BYTES)
    (rig["tmp"] / "bin" / "nvidia-smi").write_text("#!/bin/sh\necho 8.9\n")
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode != 0 and "sm_120" in r.stderr


def test_ready_needs_the_model_loaded():
    # serve/handler.py:415-421 at 400b411c: status ok while loading, loaded true once ready.
    text = ADAPTER.read_text()
    m = re.search(r'eng_basalt_ready\(\) \{.*?\n\}', text, re.S)
    assert m and 'd.get("loaded") is True' in m.group(0) and 'd.get("status") == "ok"' in m.group(0)


# --- install: pinned, offline-capable, patch hook ---------------------------------------

def test_unreviewed_versions_and_offline_downloads_are_refused(rig):
    r = _sh(rig, 'ob_engine_load basalt; eng_basalt_install v0.5.8 "" ""; echo rc=$?', OB_ENGINE_BASALT_URL="file:///nonexistent")
    assert "not a reviewed version" in r.stderr and "rc=1" in r.stdout
    r = _sh(rig, 'ob_engine_load basalt; eng_basalt_install v0.5.7 "" ""; echo rc=$?', OFFLINE="true")
    assert "OFFLINE=true" in r.stderr and "rc=1" in r.stdout
    r = _sh(rig, 'ob_engine_load basalt; eng_basalt_install "../x" "" ""; echo rc=$?')
    assert "not a Basalt version" in r.stderr


def test_the_pin_is_the_reviewed_commit():
    assert f"v0.5.7\t{COMMIT}" in ADAPTER.read_text()


def _patch_repo(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "service.py").write_text("answer_lead = None\n")
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-qm", "base"], check=True)
    (repo / "service.py").write_text("answer_lead = '<|answer|>'\n")
    diff = subprocess.run(["git", "-C", str(repo), "diff"], capture_output=True, text=True, check=True).stdout
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "."], check=True)
    return repo, diff


def test_patch_hook_applies_a_present_pr632_patch_and_records_it(rig):
    pdir = rig["tmp"] / "patches"
    pdir.mkdir()
    r = _sh(rig, "ob_engine_load basalt; _eng_basalt_patches", OB_ENGINE_BASALT_PATCH_DIR=str(pdir))
    assert r.returncode == 0 and r.stdout == ""                       # none present: the pin exactly
    repo, diff = _patch_repo(rig["tmp"])
    (pdir / "basalt-pr632-answer-lead.patch").write_text(diff)
    (pdir / "other.patch").write_text(diff)                            # only the pr632 glob counts
    r = _sh(rig, 'ob_engine_load basalt; P=(); while IFS= read -r x; do P+=("$x"); done < <(_eng_basalt_patches); printf "%s\\n" "${P[@]}"; '
                 f'_eng_basalt_apply "{repo}" "${{P[@]}}" && echo "BP=$_BP"; _eng_basalt_patch_tag "${{P[@]}}"',
            OB_ENGINE_BASALT_PATCH_DIR=str(pdir))
    assert r.returncode == 0, r.stderr
    assert "basalt-pr632-answer-lead.patch" in r.stdout and "other.patch" not in r.stdout.replace("pr632-answer-lead", "")
    assert re.search(r"BP=basalt-pr632-answer-lead\.patch=[0-9a-f]{64}", r.stdout)
    assert re.search(r"^pr632-[0-9a-f]{12}$", r.stdout, re.M)
    assert "<|answer|>" in (repo / "service.py").read_text()
    r = _sh(rig, "ob_engine_load basalt; _eng_basalt_patches", OB_ENGINE_BASALT_PATCH_DIR=str(pdir),
            OPENBEAST_ENGINE_BASALT_PATCHES="none")
    assert r.stdout == ""


def test_a_patch_that_does_not_apply_is_refused(rig):
    repo, diff = _patch_repo(rig["tmp"])
    bad = rig["tmp"] / "basalt-pr632-bad.patch"
    bad.write_text(diff.replace("answer_lead = None", "something_else = 1"))
    r = _sh(rig, f'ob_engine_load basalt; _eng_basalt_apply "{repo}" "{bad}"; echo rc=$?')
    assert "rc=1" in r.stdout and "does not apply" in r.stderr


def test_the_install_checks_the_image_holds_the_pinned_packages(rig):
    pins = re.search(r"_ENG_BASALT_PY_PINS='(.*?)'", ADAPTER.read_text(), re.S).group(1).split("\n")
    dpkg = "\\n".join(pins + ["python3-numpy=1:1.26.4+ds-6ubuntu1"])
    docker = rig["tmp"] / "bin" / "docker"
    docker.write_text(f"#!/bin/sh\nprintf '{dpkg}\\n'\n")
    r = _sh(rig, f"ob_engine_load basalt; _eng_basalt_image_check {IMAGE} && echo OK")
    assert "OK" in r.stdout, r.stderr
    docker.write_text("#!/bin/sh\nprintf 'python3=3.12.3-0ubuntu2.1\\npython3-jinja2=3.1.6-1\\n'\n")
    r = _sh(rig, f"ob_engine_load basalt; _eng_basalt_image_check {IMAGE} && echo OK")
    assert "OK" not in r.stdout and "python3-jinja2=3.1.2-1ubuntu1.3" in r.stderr


def test_dockerfile_pins_the_base_and_the_server_packages():
    text = DOCKERFILE.read_text()
    assert re.search(r"^ARG BASE=nvidia/cuda:13\.0\.2-devel-ubuntu24\.04@sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6$",
                     text, re.M)
    assert re.search(r"^ARG UBUNTU_SNAPSHOT=\d{8}T\d{6}Z$", text, re.M)
    pins = re.search(r"_ENG_BASALT_PY_PINS='(.*?)'", ADAPTER.read_text(), re.S).group(1).split("\n")
    for p in pins:
        assert re.search(rf"^\s+{re.escape(p)} \\$", text, re.M), p
    assert "pip" not in text and "requirements.txt" not in re.sub(r"#.*", "", text)


# --- the profile and its weights --------------------------------------------------------

def _pf():
    return dict(line.split("=", 1) for line in PROFILE.read_text().splitlines()
                if line and not line.startswith("#"))


def test_profile_is_the_measured_ink_t2_stock_serve():
    pf = _pf()
    w = json.load(open(WEIGHTS))["weights"]["inkling2-mini-basalt-nvfp4-mx8-stock"]
    assert pf["PACK"] == w["file"] == PACK
    assert pf["MEASURED_ROW"] == "INK-T2-STOCK"
    # weights.json's evidence records the args docker inspect read at the arm's start and end.
    assert ("--alias inkling-2-mini --parallel 1 --max-context 262144 --kv fp8" in w["evidence"])
    assert (pf["SERVED_MODEL_NAME"], pf["PARALLEL"], pf["MAX_CONTEXT"], pf["KV"]) == ("inkling-2-mini", "1", "262144", "fp8")
    assert pf["ANSWER_ROOM"] == "auto"                                  # Basalt's default, what the arm ran
    assert int(pf["MAX_CONTEXT"]) == w["context"] and int(pf["PARALLEL"]) == w["streams"]
    assert pf["AGENT_PROFILE"] == "inkling-best"
    run = next(r for r in json.load(open(BOARD))["runs"] if r["id"] == "INK-T2-STOCK")
    assert run["weights_id"] == "inkling2-mini-basalt-nvfp4-mx8-stock"


def test_the_pack_is_registry_pinned_like_weights_json():
    w = json.load(open(WEIGHTS))["weights"]["inkling2-mini-basalt-nvfp4-mx8-stock"]
    rows = [line.split("\t") for line in REGISTRY.read_text().splitlines()
            if line and not line.startswith("#") and line.split("\t")[2:3] == [PACK]]
    assert len(rows) == 1
    sha, size, _name, repo, remote = rows[0]
    assert sha == w["file_sha256"] and int(size) == w["file_bytes"] == PACK_BYTES
    assert repo == w["repo"] == "jesdga/Inkling-2-Mini-Basalt" and remote == "-"
    assert w["revision"].startswith("c2afac3a") and w["revision"][:8] in REGISTRY.read_text()


def test_profile_loads_and_locates(rig):
    r = _sh(rig, 'ob_engine_load basalt; eng_basalt_profile_locate inkling2-mini-stock; '
                 'echo "rc=$SP_LOCATE_RC sha=$PF_PACK_SHA256 bytes=$PF_PACK_BYTES row=$PF_MEASURED_ROW"')
    assert "rc=0" in r.stdout and f"bytes={PACK_BYTES}" in r.stdout and "row=INK-T2-STOCK" in r.stdout
    assert "sha=28ec70723feaa037" in r.stdout


def test_profile_rejects_unknown_keys_and_unpinned_packs(rig, tmp_path):
    bad = tmp_path / "bad.env"
    bad.write_text(PROFILE.read_text() + "EXTRA_ARGS=--host 0.0.0.0\n")
    r = _sh(rig, f'ob_engine_load basalt; eng_basalt_profile_check "{bad}"; echo rc=$?')
    assert "rc=1" in r.stdout and "unknown key" in r.stderr
    bad.write_text(PROFILE.read_text().replace(PACK, "Other.basalt"))
    r = _sh(rig, f'ob_engine_load basalt; eng_basalt_profile_check "{bad}"; echo rc=$?')
    assert "rc=1" in r.stdout and "no sha256 pin" in r.stderr


def test_the_default_profile_is_the_shipped_one(rig):
    r = _sh(rig, "ob_engine_call basalt default_profile")
    assert r.stdout.strip() == PROFILE.stem


# --- the default rule -------------------------------------------------------------------

@pytest.mark.parametrize("gpu,want", [("NVIDIA GeForce RTX 5090", "basalt"), ("NVIDIA GeForce RTX 4090", "llama"),
                                      ("", "llama")])
def test_the_rig_default_engine_follows_the_gpu(rig, gpu, want):
    r = _sh(rig, "ob_engine_default rig", OB_ENGINE_GPU_NAME=gpu or "none")
    assert r.stdout.strip() == want
    r = _sh(rig, "ob_engine_default spark", OB_ENGINE_GPU_NAME=gpu or "none")
    assert r.stdout.strip() == "tensorfold"


def test_board_default_names_this_profile_and_preset():
    d = {x["platform"]: x for x in json.load(open(BOARD))["defaults"]}
    assert d["rtx5090"]["run"] == _pf()["MEASURED_ROW"]
    assert d["rtx5090"]["agent_profile"] == _pf()["AGENT_PROFILE"]
    assert [p["run"] for p in d["rtx5090"]["previous"]] == ["Q38U-T2"]


def test_shellcheck_clean():
    sc = shutil.which("shellcheck")
    if not sc:
        pytest.skip("shellcheck not installed")
    r = subprocess.run([sc, "-x", str(ADAPTER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_use_default_on_a_5090_is_the_switch(rig):
    """`engine.sh use default` on an RTX 5090: basalt with its shipped profile, loopback, one slot."""
    env = dict(rig["env"], OB_ENGINE_GPU_NAME="NVIDIA GeForce RTX 5090")
    r = subprocess.run([BASH, str(ROOT / "scripts" / "engine.sh"), "use", "default", "--dry-run"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr
    for kv in ("INFERENCE_BACKEND=basalt", "INFERENCE_URL=http://127.0.0.1:8080", "INFERENCE_MANAGED=true",
               "ENGINE_PROFILE=inkling2-mini-stock", 'INFERENCE_MODEL="inkling-2-mini"', "INFERENCE_SLOTS=1"):
        assert f"set   {kv}" in r.stdout, kv
    assert "dry run" in r.stdout
    env["OB_ENGINE_GPU_NAME"] = "NVIDIA GeForce RTX 4090"
    r = subprocess.run([BASH, str(ROOT / "scripts" / "engine.sh"), "use", "default", "--dry-run"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
    assert "engine.sh use llama" in r.stdout
