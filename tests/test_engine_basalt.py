"""The Basalt engine adapter (scripts/engines/basalt.sh), the RTX 5090 default.

No docker and no GPU needed: a fake docker client records every argv and
answers what the adapter asks it (the network's NAT option, bridge name and
gateway, the sandbox probe's output, and for the offline install: load, tag,
image inspect, the package list and a stand-in compile). What is held here is
the Strata report §15.8 list: every hardening flag is on the server's command
line and nothing loosens it; the probe checks the SAME sandbox before the
server starts (names, TCP out, raw DNS to the gateway / docker0 / 1.1.1.1)
and a probe that reaches out stops the launch; the key travels as a 0600
file, never on argv; the profile carries INK-T2-STOCK's serve args and names
the patched build it serves (PATCHES), and the launch refuses another build;
the env image is pinned; only reviewed PR #632 patches (name, commit, body
hash) are applied; install --from verifies the staged folder and refuses a
mismatch or a tagged tarball; rollback picks the right one of two builds at
one commit. A real build and serve are checked on the rig at the switch
(docs/TODO.md).

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
# The RTX 5090 default since 2026-11-25 (DECISIONS-2026-11-25 §7): SC117's abliterated pack; the stock profile above
# stays (the rollback) and most launch tests below pin it with OPENBEAST_ENGINE_PROFILE or use it by name.
SC117_PROFILE = ROOT / "scripts" / "engines" / "basalt" / "profiles" / "inkling2-mini-sc117.env"
SC117_PACK = "Inkling-2-Mini-SC117-abliterated-NVFP4-MX8.basalt"
SC117_BYTES = 19606374187
PACK_BYTES = 19606374189
IMAGE = "sha256:" + "ab" * 32
KEY = "s3cret-key-value"

GATEWAY = "172.30.0.1"
SHIPPED_PATCH = ROOT / "scripts" / "engines" / "patches" / "basalt-pr632-55f9be17.patch"
_TAIL = ("tcp 1.1.1.1 443 FAIL TimeoutError\\ntcp 8.8.8.8 53 FAIL TimeoutError\\n"
         f"tcp {GATEWAY} 22 FAIL TimeoutError\\nudp53 {GATEWAY} 53 FAIL TimeoutError\\n"
         "udp53 172.17.0.1 53 FAIL TimeoutError\\nudp53 1.1.1.1 53 FAIL TimeoutError\\n")
PROBE_CLOSED = ("dns example.com FAIL gaierror\\ndns huggingface.co FAIL gaierror\\n"
                "dns github.com FAIL gaierror\\n" + _TAIL)
PROBE_OPEN = ("dns example.com RESOLVED\\ndns huggingface.co FAIL gaierror\\n"
              "dns github.com FAIL gaierror\\n" + _TAIL)
# The review's finding: the host resolver on docker0 answers a raw query from the bridge.
PROBE_RAW_DNS = PROBE_CLOSED.replace("udp53 172.17.0.1 53 FAIL TimeoutError", "udp53 172.17.0.1 53 ANSWERED")


def _sha(path):
    return subprocess.run(["shasum", "-a", "256", str(path)], capture_output=True, text=True,
                          check=True).stdout.split()[0] if shutil.which("shasum") else \
        subprocess.run(["sha256sum", str(path)], capture_output=True, text=True, check=True).stdout.split()[0]


@pytest.fixture
def rig(tmp_path):
    """A fake installed build, a sparse pack at the pinned size, fake docker and nvidia-smi."""
    opt = tmp_path / "opt"
    build = opt / "basalt-v0.5.7"
    (build / "bin").mkdir(parents=True)
    (build / "bin" / "basalt").write_text("#!/bin/sh\n")
    (build / "bin" / "basalt").chmod(0o755)
    # The default profile serves the pin + the shipped PR #632 patch (PATCHES=…), so the fake build carries it.
    (build / "OPENBEAST_BUILD").write_text(
        f"VERSION=v0.5.7\nCOMMIT={COMMIT}\nPATCH_TAG=pr632-0123456789ab\n"
        f"PATCHES={SHIPPED_PATCH.name}={_sha(SHIPPED_PATCH)}\nIMAGE_ID={IMAGE}\n")
    (opt / "basalt").symlink_to(build)
    w = tmp_path / "weights"
    w.mkdir()
    with open(w / PACK, "wb") as f:
        f.truncate(PACK_BYTES)            # sparse: the size check, not the bytes
    with open(w / SC117_PACK, "wb") as f:
        f.truncate(SC117_BYTES)
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
  "network inspect -f"*enable_ip_masquerade*) cat {state}/masq 2>/dev/null || echo false ;;
  "network inspect -f"*bridge.name*) cat {state}/bridge 2>/dev/null || echo ob-basalt0 ;;
  "network inspect -f"*Gateway*) echo "{GATEWAY} " ;;
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
    assert any(v.endswith(f":/models/{SC117_PACK}:ro") for v in vols)    # the default profile's pack
    assert f"{rig['build'].resolve()}:/opt/basalt:ro" in vols         # the resolved build, not the link
    # The image is the build's recorded env image, by id.
    assert IMAGE in srv and IMAGE in probe
    # The serve line is the measured one.
    tail = srv[srv.index(IMAGE) + 1:]
    assert tail[:2] == ["/opt/basalt/bin/basalt", "serve"]
    for flag, value in (("--model", f"/models/{SC117_PACK}"), ("--alias", "inkling-2-mini"), ("--parallel", "1"),
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
    with open(rig["weights"] / SC117_PACK, "wb") as f:          # the default profile's pack
        f.truncate(SC117_BYTES - 1)
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode != 0 and "pinned size" in r.stderr
    with open(rig["weights"] / SC117_PACK, "wb") as f:
        f.truncate(SC117_BYTES)
    with open(rig["weights"] / PACK, "wb") as f:                # the stock profile checks its own pack
        f.truncate(PACK_BYTES - 1)
    r = _sh(rig, "ob_engine_call basalt launch", OPENBEAST_ENGINE_PROFILE="inkling2-mini-stock")
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


def _head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def _body_sha(text):
    import hashlib
    i = re.search(r"^diff --git", text, re.M).start()                   # sed -n '/^diff --git/,$p'
    return hashlib.sha256(text[i:].encode()).hexdigest()


def test_patch_hook_applies_a_present_pr632_patch_and_records_it(rig):
    pdir = rig["tmp"] / "patches"
    pdir.mkdir()
    r = _sh(rig, "ob_engine_load basalt; _eng_basalt_patches", OB_ENGINE_BASALT_PATCH_DIR=str(pdir))
    assert r.returncode == 0 and r.stdout == ""                       # none present: the pin exactly
    repo, diff = _patch_repo(rig["tmp"])
    (pdir / "basalt-pr632-answer-lead.patch").write_text(diff)
    (pdir / "other.patch").write_text(diff)                            # only the pr632 glob counts
    table = f"basalt-pr632-answer-lead.patch\t{_head(repo)}\t{_body_sha(diff)}"
    r = _sh(rig, 'ob_engine_load basalt; P=(); while IFS= read -r x; do P+=("$x"); done < <(_eng_basalt_patches); printf "%s\\n" "${P[@]}"; '
                 f'_eng_basalt_apply "{repo}" "${{P[@]}}" && echo "BP=$_BP"; _eng_basalt_patch_tag "${{P[@]}}"',
            OB_ENGINE_BASALT_PATCH_DIR=str(pdir), OB_ENGINE_BASALT_PATCHES_REVIEWED=table)
    assert r.returncode == 0, r.stderr
    assert "basalt-pr632-answer-lead.patch" in r.stdout and "other.patch" not in r.stdout.replace("pr632-answer-lead", "")
    assert re.search(r"BP=basalt-pr632-answer-lead\.patch=[0-9a-f]{64}", r.stdout)
    assert re.search(r"^pr632-[0-9a-f]{12}$", r.stdout, re.M)
    assert "<|answer|>" in (repo / "service.py").read_text()
    r = _sh(rig, "ob_engine_load basalt; _eng_basalt_patches", OB_ENGINE_BASALT_PATCH_DIR=str(pdir),
            OPENBEAST_ENGINE_BASALT_PATCHES="none", OB_ENGINE_BASALT_PATCHES_REVIEWED=table)
    assert r.stdout == ""


def test_the_shipped_patch_is_the_reviewed_one(rig):
    """main's patch file hashes as the table pins (the same body sha256 basalt-apply-pr632.sh checks)."""
    r = _sh(rig, "ob_engine_load basalt; _eng_basalt_patches; echo rc=$?")
    assert "rc=0" in r.stdout and str(SHIPPED_PATCH) in r.stdout, r.stderr
    assert _body_sha(SHIPPED_PATCH.read_text()) == "af15ee07ecddababe54b57ae68b78b0d526602156332333ae50ba3f36748c0cd"
    helper = (ROOT / "scripts" / "engines" / "basalt-apply-pr632.sh").read_text()
    assert "BODY_SHA256=af15ee07ecddababe54b57ae68b78b0d526602156332333ae50ba3f36748c0cd" in helper
    assert f"basalt-pr632-55f9be17.patch\t{COMMIT}\taf15ee07" in ADAPTER.read_text()


def test_an_unreviewed_or_edited_patch_stops_the_build(rig):
    """security-04: a basalt-pr632-*.patch that is not a reviewed row (an untracked file, an edit) is refused."""
    pdir = rig["tmp"] / "patches"
    pdir.mkdir()
    repo, diff = _patch_repo(rig["tmp"])
    (pdir / "basalt-pr632-unknown.patch").write_text(diff)
    r = _sh(rig, "ob_engine_load basalt; _eng_basalt_patches; echo rc=$?", OB_ENGINE_BASALT_PATCH_DIR=str(pdir))
    assert "rc=1" in r.stdout and "not in the reviewed patch table" in r.stderr
    # The shipped name with an edited body: same name, another hash.
    (pdir / "basalt-pr632-unknown.patch").unlink()
    text = SHIPPED_PATCH.read_text()
    i = re.search(r"^diff --git", text, re.M).start()
    edited = text[:i] + text[i:].replace("answer", "answr", 1)              # the body, not the comment header
    (pdir / SHIPPED_PATCH.name).write_text(edited)
    r = _sh(rig, "ob_engine_load basalt; _eng_basalt_patches; echo rc=$?", OB_ENGINE_BASALT_PATCH_DIR=str(pdir))
    assert "rc=1" in r.stdout and "the reviewed table pins" in r.stderr
    # The install stops on it too (before any fetch), not only the listing.
    r = _sh(rig, 'ob_engine_load basalt; eng_basalt_install v0.5.7 "" ""; echo rc=$?', OB_ENGINE_BASALT_PATCH_DIR=str(pdir),
            OB_ENGINE_BASALT_URL="file:///nonexistent")
    assert "rc=1" in r.stdout and "the reviewed table pins" in r.stderr and "could not fetch" not in r.stderr


def test_a_patch_reviewed_for_another_commit_is_refused(rig):
    repo, diff = _patch_repo(rig["tmp"])
    f = rig["tmp"] / "basalt-pr632-x.patch"
    f.write_text(diff)
    table = f"basalt-pr632-x.patch\t{'0' * 40}\t{_body_sha(diff)}"
    r = _sh(rig, f'ob_engine_load basalt; _eng_basalt_apply "{repo}" "{f}"; echo rc=$?', OB_ENGINE_BASALT_PATCHES_REVIEWED=table)
    assert "rc=1" in r.stdout and "is reviewed for 000000000000" in r.stderr
    assert "<|answer|>" not in (repo / "service.py").read_text()


def test_a_patch_that_does_not_apply_is_refused(rig):
    repo, diff = _patch_repo(rig["tmp"])
    bad = rig["tmp"] / "basalt-pr632-bad.patch"
    text = diff.replace("answer_lead = None", "something_else = 1")
    bad.write_text(text)
    table = f"basalt-pr632-bad.patch\t{_head(repo)}\t{_body_sha(text)}"
    r = _sh(rig, f'ob_engine_load basalt; _eng_basalt_apply "{repo}" "{bad}"; echo rc=$?', OB_ENGINE_BASALT_PATCHES_REVIEWED=table)
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
    assert r.stdout.strip() == SC117_PROFILE.stem == "inkling2-mini-sc117"


def _pf_sc117():
    return dict(line.split("=", 1) for line in SC117_PROFILE.read_text().splitlines()
                if line and not line.startswith("#"))


def test_sc117_profile_is_the_stock_serve_on_the_sc117_pack():
    """The default profile: the stock profile's serve args, setup and build decision, only the pack and row differ."""
    a, b = _pf(), _pf_sc117()
    assert {k: v for k, v in a.items() if k not in ("PACK", "MEASURED_ROW")} == \
        {k: v for k, v in b.items() if k not in ("PACK", "MEASURED_ROW")}
    assert b["PACK"] == SC117_PACK and b["MEASURED_ROW"] == "INK-T2-SC117" and b["PATCHES"] == SHIPPED_PATCH.name
    w = json.load(open(WEIGHTS))["weights"]["inkling2-mini-sc117-abliterated-basalt-nvfp4-mx8"]
    assert w["file"] == SC117_PACK and w["file_bytes"] == SC117_BYTES
    run = next(r for r in json.load(open(BOARD))["runs"] if r["id"] == "INK-T2-SC117")
    assert run["weights_id"] == "inkling2-mini-sc117-abliterated-basalt-nvfp4-mx8"
    text = SC117_PROFILE.read_text()
    assert "NOT exactly that row" in text and "inkling-sc117-pack.sh" in text


def test_the_sc117_pack_is_registry_pinned_as_derived():
    w = json.load(open(WEIGHTS))["weights"]["inkling2-mini-sc117-abliterated-basalt-nvfp4-mx8"]
    rows = [line.split("\t") for line in REGISTRY.read_text().splitlines()
            if line and not line.startswith("#") and line.split("\t")[2:3] == [SC117_PACK]]
    assert len(rows) == 1
    sha, size, _name, repo, remote = rows[0]
    assert sha == w["file_sha256"] and int(size) == SC117_BYTES
    assert repo == "derived" and remote == "scripts/engines/inkling-sc117-pack.sh"


def test_sc117_profile_loads_and_locates(rig):
    r = _sh(rig, 'ob_engine_load basalt; eng_basalt_profile_locate inkling2-mini-sc117; '
                 'echo "rc=$SP_LOCATE_RC sha=$PF_PACK_SHA256 bytes=$PF_PACK_BYTES row=$PF_MEASURED_ROW"')
    assert "rc=0" in r.stdout and f"bytes={SC117_BYTES}" in r.stdout and "row=INK-T2-SC117" in r.stdout
    assert "sha=6eaa50d3620a8f39" in r.stdout


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
    assert d["rtx5090"]["run"] == _pf_sc117()["MEASURED_ROW"] == "INK-T2-SC117"
    assert d["rtx5090"]["agent_profile"] == _pf_sc117()["AGENT_PROFILE"]
    # the stock pack, the default 2026-11-24 to 2026-11-25, is the rollback; Qwen3.8 the fallback before it
    assert [p["run"] for p in d["rtx5090"]["previous"]] == [_pf()["MEASURED_ROW"], "Q38U-T2"]


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
               "ENGINE_PROFILE=inkling2-mini-sc117", 'INFERENCE_MODEL="inkling-2-mini"', "INFERENCE_SLOTS=1"):
        assert f"set   {kv}" in r.stdout, kv
    assert "dry run" in r.stdout
    env["OB_ENGINE_GPU_NAME"] = "NVIDIA GeForce RTX 4090"
    r = subprocess.run([BASH, str(ROOT / "scripts" / "engine.sh"), "use", "default", "--dry-run"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
    assert "engine.sh use llama" in r.stdout


def test_use_default_moves_an_older_conf_to_the_shipped_profile(rig, tmp_path):
    """A conf from before 2026-11-25 names the stock profile: `use default` means what this tree ships (SC117);
    `use basalt` keeps the conf's profile; --profile wins over both (the rollback)."""
    env = dict(rig["env"], OB_ENGINE_GPU_NAME="NVIDIA GeForce RTX 5090")
    conf = tmp_path / "openbeast.conf"
    conf.write_text('INFERENCE_BACKEND=basalt\nINFERENCE_URL=http://127.0.0.1:8080\nINFERENCE_MANAGED=true\n'
                    'ENGINE_PROFILE=inkling2-mini-stock\nINFERENCE_MODEL="inkling-2-mini"\nINFERENCE_SLOTS=1\n')
    env["OB_ENGINE_CONF"] = str(conf)

    def dry(*args):
        r = subprocess.run([BASH, str(ROOT / "scripts" / "engine.sh"), "use", *args, "--dry-run"],
                           capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
        assert r.returncode == 0, r.stderr
        return r.stdout
    assert "set   ENGINE_PROFILE=inkling2-mini-sc117" in dry("default")
    out = dry("basalt")
    assert "ENGINE_PROFILE=inkling2-mini-sc117" not in out and "inkling2-mini-stock" in out
    assert "set   ENGINE_PROFILE=inkling2-mini-stock" in dry("default", "--profile", "inkling2-mini-stock")


# --- the sandbox probe: raw DNS and the gateway (review finding security-02) -------------

def test_the_probe_gets_the_gateway_and_tests_raw_dns(rig):
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode == 0, r.stderr
    probe = _probe(_calls(rig))
    assert probe[-1] == GATEWAY                                        # argv[1] of the probe script
    code = probe[probe.index("-c") + 1]
    for want in ('"172.17.0.1"', '"1.1.1.1"', "(gw, 22)", "SOCK_DGRAM", "ANSWERED"):
        assert want in code, want
    assert "no raw DNS answer" in r.stdout


def test_a_resolver_that_answers_a_raw_query_stops_the_launch(rig):
    (rig["state"] / "probe").write_text(PROBE_RAW_DNS)
    r = _sh(rig, "ob_engine_call basalt launch", LLAMA_API_KEY=KEY)
    assert r.returncode != 0 and "REFUSING" in r.stderr and "ANSWERED" in r.stderr
    assert "ufw insert 1 deny in on ob-basalt0" in r.stderr
    assert not [c for c in _calls(rig) if "--init" in c]


def test_the_probe_script_is_valid_python_and_prints_nine_lines():
    m = re.search(r"_ENG_BASALT_PROBE='(.*?)'\n_ENG_BASALT_PROBE_LINES=(\d+)", ADAPTER.read_text(), re.S)
    assert m and int(m.group(2)) == 9 == PROBE_CLOSED.count("\\n")
    compile(m.group(1), "probe", "exec")


def test_the_network_has_a_fixed_bridge_name(rig):
    (rig["state"] / "nonet").write_text("")
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode == 0, r.stderr
    create = [c for c in _calls(rig) if c[:2] == ["network", "create"]][0]
    assert "com.docker.network.bridge.name=ob-basalt0" in _opt(create, "-o")


def test_a_network_on_another_bridge_is_refused(rig):
    (rig["state"] / "bridge").write_text("br-0123456789ab\n")
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode != 0 and "ob-basalt0" in r.stderr
    assert not [c for c in _calls(rig) if c[:1] == ["run"]]


# --- the profile names the build it serves (engines-supply-01 / docs-01) -----------------

def test_the_default_profile_names_the_patched_build():
    pf = _pf()
    assert pf["PATCHES"] == SHIPPED_PATCH.name
    text = PROFILE.read_text()
    assert "Exactly the serve" not in text and "NOT exactly that row" in text


def test_an_unpatched_build_is_refused_under_the_patched_profile(rig):
    rec = rig["build"] / "OPENBEAST_BUILD"
    rec.write_text(f"VERSION=v0.5.7\nCOMMIT={COMMIT}\nPATCH_TAG=\nPATCHES=\nIMAGE_ID={IMAGE}\n")
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode != 0 and "carries patches 'none'" in r.stderr
    assert not [c for c in _calls(rig) if c[:1] == ["run"]]
    # … and a profile that says PATCHES=none serves it.
    pf = rig["tmp"] / "pin.env"
    pf.write_text(PROFILE.read_text().replace(f"PATCHES={SHIPPED_PATCH.name}", "PATCHES=none"))
    r = _sh(rig, "ob_engine_call basalt launch", OPENBEAST_ENGINE_PROFILE=str(pf))
    assert r.returncode == 0, r.stderr
    assert "(inkling-2-mini, row INK-T2-STOCK)" in r.stdout


def test_the_banner_says_the_build_is_patched(rig):
    r = _sh(rig, "ob_engine_call basalt launch")
    assert r.returncode == 0, r.stderr
    assert f"row INK-T2-SC117's serve; build + {SHIPPED_PATCH.name}" in r.stdout
    r = _sh(rig, "ob_engine_call basalt launch", OPENBEAST_ENGINE_PROFILE="inkling2-mini-stock")
    assert r.returncode == 0, r.stderr
    assert f"row INK-T2-STOCK's serve; build + {SHIPPED_PATCH.name}" in r.stdout


def test_a_profile_without_patches_is_refused(rig, tmp_path):
    bad = tmp_path / "nopatch.env"
    bad.write_text("\n".join(x for x in PROFILE.read_text().splitlines() if not x.startswith("PATCHES=")) + "\n")
    r = _sh(rig, f'ob_engine_load basalt; eng_basalt_profile_check "{bad}"; echo rc=$?')
    assert "rc=1" in r.stdout and "PATCHES is required" in r.stderr


# --- install --from: the offline path (testability finding) ------------------------------

def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def staged(rig):
    """A staged folder as `engine.sh stage basalt` writes it, around a fixture commit, and a docker
    stub that loads, tags, inspects and 'compiles' (writes bin/basalt into the mounted tree)."""
    t = rig["tmp"]
    src = t / "upstream"
    src.mkdir()
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "README").write_text("basalt\n")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "v0.5.7")
    commit = _git(src, "rev-parse", "HEAD")
    st = t / "stage"
    st.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(st / "basalt.git")], check=True)
    _git(st / "basalt.git", "fetch", "-q", str(src), "HEAD")
    _git(st / "basalt.git", "update-ref", "refs/heads/ob-stage", commit)
    image_id = "sha256:" + "cd" * 32
    import io
    import tarfile
    tarp = st / "openbeast-basalt-env-abc-x86_64.tar"
    with tarfile.open(tarp, "w") as tf:
        data = json.dumps([{"Config": "x.json", "RepoTags": None, "Layers": []}]).encode()
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    (st / "MANIFEST").write_text(
        f"ENGINE=basalt\nVERSION=v0.5.7\nCOMMIT={commit}\nIMAGE_TAR={tarp.name}\nIMAGE_TAR_SHA256={_sha(tarp)}\n"
        f"IMAGE_ID={image_id}\nDOCKERFILE_SHA256={_sha(DOCKERFILE)}\nARCH=x86_64\n")
    pins = re.search(r"_ENG_BASALT_PY_PINS='(.*?)'", ADAPTER.read_text(), re.S).group(1)
    state = rig["state"]
    (state / "dpkg").write_text(pins + "\n")
    (state / "loaded_id").write_text(image_id + "\n")
    log = rig["log"]
    (rig["tmp"] / "bin" / "docker").write_text(f"""#!/bin/bash
{{ for a in "$@"; do printf '%s\\0' "$a"; done; printf '\\n--END--\\n'; }} >> {log}
case "$1" in
  load) touch {state}/loaded; exit 0 ;;
  tag) exit 0 ;;
  image) [[ -f {state}/loaded ]] || exit 1
         [[ "$*" == *"--format"* ]] && cat {state}/loaded_id; exit 0 ;;
  inspect) [[ -f {state}/running_src ]] || exit 1
           if [[ "$*" == *State.Running* ]]; then echo true; else cat {state}/running_src; fi; exit 0 ;;
  stop) touch {state}/stopped; exit 0 ;;
  run) if [[ "$*" == *"--entrypoint cat"* ]]; then cat {state}/dpkg; exit 0; fi
       [[ -f {state}/fail-compile ]] && exit 1
       prev=""; for a in "$@"; do
         if [[ "$prev" == -v && "$a" == *:/src ]]; then d="${{a%:/src}}"; mkdir -p "$d/bin"; printf '#!/bin/sh\\n' > "$d/bin/basalt"; chmod +x "$d/bin/basalt"; fi
         prev="$a"; done; exit 0 ;;
esac
exit 0
""")
    env = {"OB_ENGINE_BASALT_REVIEWED": f"v0.5.7\t{commit}", "OB_ENGINE_BASALT_PATCH_DIR": str(t / "nopatches"),
           "OFFLINE": "true"}
    return {"dir": st, "commit": commit, "image": image_id, "tar": tarp, "env": env}


def _install(rig, staged, **extra):
    return _sh(rig, f'ob_engine_load basalt; eng_basalt_install "" "{staged["dir"]}" ""; echo "rc=$? ref=$ENG_RESULT_REF"',
               **dict(staged["env"], **extra))


def test_install_from_a_staged_folder_builds_and_records(rig, staged):
    r = _install(rig, staged)
    assert f"rc=0 ref={staged['commit']}" in r.stdout, r.stderr
    d = rig["tmp"] / "opt" / "basalt-v0.5.7"
    rec = dict(x.split("=", 1) for x in (d / "OPENBEAST_BUILD").read_text().splitlines())
    assert rec["COMMIT"] == staged["commit"] and rec["IMAGE_ID"] == staged["image"]
    assert rec["DOCKERFILE_SHA256"] == _sha(DOCKERFILE) and re.fullmatch(r"[0-9a-f]{64}", rec["BIN_SHA256"])
    assert rec["PATCH_TAG"] == "" and rec["PATCHES"] == ""
    assert (rig["tmp"] / "opt" / "basalt").resolve() == d.resolve()
    calls = _calls(rig)
    # The loaded image is tagged (never dangling: engines-supply-02), with this checkout's env tag.
    tags = [c for c in calls if c[:1] == ["tag"]]
    assert len(tags) == 1 and tags[0][1] == staged["image"] and re.fullmatch(r"openbeast-basalt-env:[0-9a-f]{12}", tags[0][2])
    # The compile ran offline, unprivileged.
    comp = [c for c in calls if c[:1] == ["run"] and any(a.endswith(":/src") for a in c)]
    assert len(comp) == 1 and _opt(comp[0], "--network") == ["none"] and "ALL" in _opt(comp[0], "--cap-drop")
    assert not list((rig["tmp"] / "opt").glob("*.partial.*")) and not list((rig["tmp"] / "opt").glob("*.old.*"))


@pytest.mark.parametrize("key,value,msg", [
    ("COMMIT", "0" * 40, "names commit"),
    ("IMAGE_TAR_SHA256", "0" * 64, "checksum mismatch"),
    ("IMAGE_ID", "sha256:" + "ef" * 32, "is not what its MANIFEST says"),
    ("DOCKERFILE_SHA256", "0" * 64, "staged from a Dockerfile"),
    ("IMAGE_TAR", "../evil.tar", "not their shape"),
])
def test_install_from_refuses_a_staged_folder_that_does_not_match(rig, staged, key, value, msg):
    m = staged["dir"] / "MANIFEST"
    m.write_text(re.sub(rf"^{key}=.*$", f"{key}={value}", m.read_text(), flags=re.M))
    before = (rig["build"] / "OPENBEAST_BUILD").read_text()
    r = _install(rig, staged)
    assert "rc=1" in r.stdout and msg in r.stderr, r.stderr
    assert (rig["build"] / "OPENBEAST_BUILD").read_text() == before    # the build on disk untouched
    assert not list((rig["tmp"] / "opt").glob("*.partial.*"))
    assert not [c for c in _calls(rig) if c[:1] == ["run"]]            # nothing compiled


def test_install_from_refuses_a_tarball_that_names_images(rig, staged):
    """security-05: docker load would move these tags onto the staged image."""
    import io
    import tarfile
    with tarfile.open(staged["tar"], "w") as tf:
        data = json.dumps([{"Config": "x.json", "RepoTags": ["openbeast-eval-host:latest"], "Layers": []}]).encode()
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    m = staged["dir"] / "MANIFEST"
    m.write_text(re.sub(r"^IMAGE_TAR_SHA256=.*$", f"IMAGE_TAR_SHA256={_sha(staged['tar'])}", m.read_text(), flags=re.M))
    r = _install(rig, staged)
    assert "rc=1" in r.stdout and "openbeast-eval-host:latest" in r.stderr
    assert not [c for c in _calls(rig) if c[:1] == ["load"]]           # refused before docker load


def test_reinstall_replaces_the_build_and_refuses_while_it_serves(rig, staged):
    d = rig["tmp"] / "opt" / "basalt-v0.5.7"                           # the rig fixture's build is this dir
    (d / "marker").write_text("old")
    (rig["state"] / "running_src").write_text(str(d) + "\n")
    r = _install(rig, staged)
    assert "rc=1" in r.stdout and "is serving from" in r.stderr
    assert (d / "marker").exists() and not list((rig["tmp"] / "opt").glob("*.partial.*"))
    (rig["state"] / "running_src").unlink()
    r = _install(rig, staged)
    assert "rc=0" in r.stdout, r.stderr
    assert not (d / "marker").exists() and (d / "OPENBEAST_BUILD").read_text().count(staged["commit"]) == 1
    assert not list((rig["tmp"] / "opt").glob("*.old.*"))


def test_a_failed_compile_leaves_the_old_build_in_place(rig, staged):
    d = rig["tmp"] / "opt" / "basalt-v0.5.7"
    before = (d / "OPENBEAST_BUILD").read_text()
    (rig["state"] / "fail-compile").write_text("")
    r = _install(rig, staged)
    assert "rc=1" in r.stdout and "build failed" in r.stderr
    assert (d / "OPENBEAST_BUILD").read_text() == before
    assert not list((rig["tmp"] / "opt").glob("*.partial.*"))


# --- activate (rollback): which of two builds at one commit (engines-supply-04) ----------

def _build(opt, name, tag, built):
    d = opt / name
    (d / "bin").mkdir(parents=True)
    (d / "bin" / "basalt").write_text("#!/bin/sh\n")
    (d / "bin" / "basalt").chmod(0o755)
    (d / "OPENBEAST_BUILD").write_text(f"VERSION=v0.5.7\nCOMMIT={COMMIT}\nPATCH_TAG={tag}\nPATCHES=\nIMAGE_ID={IMAGE}\nBUILT={built}\n")
    return d


def _unpatched(rig, built):
    """The rig fixture's build dir (basalt-v0.5.7) as what that name means: the pin, unpatched."""
    (rig["build"] / "OPENBEAST_BUILD").write_text(
        f"VERSION=v0.5.7\nCOMMIT={COMMIT}\nPATCH_TAG=\nPATCHES=\nIMAGE_ID={IMAGE}\nBUILT={built}\n")


@pytest.mark.parametrize("note,want", [
    ("image=sha256:x bin=abc patch=pr632-aaaaaaaaaaaa", "basalt-v0.5.7+pr632-aaaaaaaaaaaa"),
    ("relinked basalt-v0.5.7+pr632-aaaaaaaaaaaa", "basalt-v0.5.7+pr632-aaaaaaaaaaaa"),
    ("image=sha256:x bin=abc", "basalt-v0.5.7"),                    # a record of the unpatched build
    ("relinked basalt-v0.5.7", "basalt-v0.5.7"),
])
def test_activate_follows_the_history_note(rig, note, want):
    opt = rig["tmp"] / "opt"
    _unpatched(rig, "2026-11-01T00:00:00Z")
    _build(opt, "basalt-v0.5.7+pr632-aaaaaaaaaaaa", "pr632-aaaaaaaaaaaa", "2026-11-24T10:00:00Z")
    r = _sh(rig, f'ob_engine_load basalt; eng_basalt_activate v0.5.7 {COMMIT} "{note}"; echo "rc=$? $ENG_RESULT_NOTE"')
    assert "rc=0" in r.stdout, r.stderr
    assert (opt / "basalt").resolve().name == want


def test_activate_without_a_note_prefers_this_checkouts_patch_state(rig):
    """No note: the build the checkout's reviewed patches would make (§11: Inkling is served with #632)."""
    opt = rig["tmp"] / "opt"
    tag = _sh(rig, f'ob_engine_load basalt; _eng_basalt_patch_tag "{SHIPPED_PATCH}"').stdout.strip()
    assert re.fullmatch(r"pr632-[0-9a-f]{12}", tag)
    _build(opt, f"basalt-v0.5.7+{tag}", tag, "2026-11-01T00:00:00Z")   # older than the unpatched one
    _unpatched(rig, "2026-11-24T00:00:00Z")
    r = _sh(rig, f'ob_engine_load basalt; eng_basalt_activate v0.5.7 {COMMIT}; echo rc=$?')
    assert "rc=0" in r.stdout and (opt / "basalt").resolve().name == f"basalt-v0.5.7+{tag}"
    # Under ENGINE_BASALT_PATCHES=none the unpatched build is the one.
    r = _sh(rig, f'ob_engine_load basalt; eng_basalt_activate v0.5.7 {COMMIT}; echo rc=$?', OPENBEAST_ENGINE_BASALT_PATCHES="none")
    assert "rc=0" in r.stdout and (opt / "basalt").resolve().name == "basalt-v0.5.7"


def test_activate_falls_back_to_the_newest_build(rig):
    opt = rig["tmp"] / "opt"
    _build(opt, "basalt-v0.5.7+pr632-bbbbbbbbbbbb", "pr632-bbbbbbbbbbbb", "2026-11-01T00:00:00Z")
    _build(opt, "basalt-v0.5.7+pr632-cccccccccccc", "pr632-cccccccccccc", "2026-11-20T00:00:00Z")
    _unpatched(rig, "2026-11-10T00:00:00Z")
    r = _sh(rig, f'ob_engine_load basalt; eng_basalt_activate v0.5.7 {COMMIT} "patch=pr632-dddddddddddd"; echo rc=$?')
    assert "rc=0" in r.stdout and (opt / "basalt").resolve().name == "basalt-v0.5.7+pr632-cccccccccccc"


def test_activate_skips_a_build_whose_image_is_gone(rig):
    docker = rig["tmp"] / "bin" / "docker"
    docker.write_text("#!/bin/sh\n[ \"$1\" = image ] && exit 1\nexit 0\n")
    r = _sh(rig, f'ob_engine_load basalt; eng_basalt_activate v0.5.7 {COMMIT}; echo rc=$?')
    assert "rc=1" in r.stdout


# --- ready / owns_port / stop -----------------------------------------------------------

@pytest.mark.parametrize("body,ok", [
    ('{"status": "ok", "loaded": true, "service": "basalt"}', True),
    ('{"status": "ok", "loaded": false, "service": "basalt"}', False),     # still loading
    ('{"status": "ok", "loaded": true, "service": "llama"}', False),
    ('{"status": "ok", "loaded": "true", "service": "basalt"}', False),
    ("not json", False),
])
def test_ready_needs_the_model_loaded_live(rig, body, ok):
    curl = rig["tmp"] / "bin" / "curl"
    curl.write_text(f"#!/bin/sh\necho \"$@\" > {rig['state']}/curl.argv\ncat <<'J'\n{body}\nJ\n")
    curl.chmod(0o755)
    r = _sh(rig, "ob_engine_call basalt ready http://127.0.0.1:8080/ && echo READY")
    assert ("READY" in r.stdout) == ok, r.stderr
    assert "http://127.0.0.1:8080/health" in (rig["state"] / "curl.argv").read_text()


def test_ready_fails_when_nothing_answers(rig):
    curl = rig["tmp"] / "bin" / "curl"
    curl.write_text("#!/bin/sh\nexit 7\n")
    curl.chmod(0o755)
    r = _sh(rig, "ob_engine_call basalt ready http://127.0.0.1:8080 && echo READY")
    assert "READY" not in r.stdout


def test_stop_is_idempotent_and_owns_port_needs_a_running_container(rig):
    docker = rig["tmp"] / "bin" / "docker"
    st = rig["state"]
    docker.write_text(f"""#!/bin/bash
echo "$*" >> {st}/docker.log
case "$1" in
  inspect) [[ -f {st}/up ]] || exit 1; [[ "$*" == *State.Running* ]] && echo true; exit 0 ;;
  stop) rm -f {st}/up; exit 0 ;;
esac
""")
    r = _sh(rig, "ob_engine_call basalt stop; echo rc=$?")              # nothing there: still 0, no stop call
    assert "rc=0" in r.stdout and "stop " not in (st / "docker.log").read_text()
    (st / "up").write_text("")
    r = _sh(rig, "ob_engine_call basalt owns_port $$ 8080 && echo OWNS")
    assert "OWNS" in r.stdout
    r = _sh(rig, "ob_engine_call basalt owns_port 999999 8080 && echo OWNS")   # launcher gone
    assert "OWNS" not in r.stdout
    r = _sh(rig, "ob_engine_call basalt stop; ob_engine_call basalt stop; echo rc=$?")
    assert "rc=0" in r.stdout and "stopped" in r.stdout
    assert (st / "docker.log").read_text().count("stop -t 30") == 1
    r = _sh(rig, "ob_engine_call basalt owns_port $$ 8080 && echo OWNS")
    assert "OWNS" not in r.stdout


# --- scripts/engines/basalt-apply-pr632.sh: what it refuses -----------------------------

def _bash4():
    for b in (shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash"):
        if b and os.path.exists(b):
            v = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout.strip()
            if v.isdigit() and int(v) >= 4:
                return b
    return None


@pytest.fixture
def helper(tmp_path):
    b = _bash4()
    if not b or not shutil.which("sha256sum"):
        pytest.skip("basalt-apply-pr632.sh needs bash >= 4 and GNU sha256sum (the rig's tools)")
    h = tmp_path / "h"
    (h / "patches").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "engines" / "basalt-apply-pr632.sh", h / "basalt-apply-pr632.sh")
    shutil.copy(SHIPPED_PATCH, h / "patches" / SHIPPED_PATCH.name)
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "f").write_text("x\n")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "not the pin")
    return {"bash": b, "script": h / "basalt-apply-pr632.sh", "patch": h / "patches" / SHIPPED_PATCH.name,
            "src": src, "tmp": tmp_path}


def _run_helper(h, dst):
    return subprocess.run([h["bash"], str(h["script"]), str(h["src"]), str(dst)], capture_output=True, text=True, timeout=60)


def test_apply_helper_refuses_a_tampered_patch(helper):
    text = helper["patch"].read_text()
    i = re.search(r"^diff --git", text, re.M).start()
    helper["patch"].write_text(text[:i] + text[i:].replace("answer", "answr", 1))
    r = _run_helper(helper, helper["tmp"] / "dst")
    assert r.returncode != 0 and "patch body sha256" in r.stderr
    assert not (helper["tmp"] / "dst").exists()


def test_apply_helper_refuses_an_existing_dst_and_a_tree_off_the_pin(helper):
    (helper["tmp"] / "dst").mkdir()
    r = _run_helper(helper, helper["tmp"] / "dst")
    assert r.returncode != 0 and "exists" in r.stderr
    r = _run_helper(helper, helper["tmp"] / "dst2")
    assert r.returncode != 0 and "is not at v0.5.7" in r.stderr
    assert not (helper["tmp"] / "dst2").exists()
