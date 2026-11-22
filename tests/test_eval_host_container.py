"""The eval host container (evals/host/Containerfile, scripts/eval-host-container.sh).

No docker needed: a fake docker client records its argv. What is held here
is what makes the container a sound harness host: the image is built from
the pins only, the base is pinned by digest, nothing loosens the sandbox,
and secrets travel by variable NAME, never by value. The image itself is
validated on a Linux box with `eval-host-container.sh check` (eval-host.sh
check + evals/host/probe.py) and the prover (docs/MODEL_CAMPAIGN.md).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "eval-host-container.sh"
CF = ROOT / "evals" / "host" / "Containerfile"
BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")


def _inputs() -> list[str]:
    m = re.search(r"INPUTS=\((.*?)\)", SCRIPT.read_text(), re.S)
    assert m
    return m.group(1).split()


@pytest.fixture
def fake_docker(tmp_path):
    log = tmp_path / "argv.log"
    d = tmp_path / "docker"
    d.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + str(log) + "\nexit 0\n")
    d.chmod(0o755)
    return d, log


def _run(args, env_extra=None, docker=None):
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.pop("OPENBEAST_EVAL_IMAGE", None)
    if docker:
        env["DOCKER"] = str(docker)
    env.update(env_extra or {})
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL, timeout=60)


def test_every_build_input_exists_and_is_copied():
    copied = re.findall(r"^COPY\s+(\S+)\s+/src/(\S+)$", CF.read_text(), re.M)
    assert copied, "the Containerfile copies its inputs"
    for src, dst in copied:
        assert src == dst
        assert (ROOT / src).is_file(), src
    # the wrapper stages exactly what the Containerfile copies (plus itself)
    assert sorted(_inputs()) == sorted([s for s, _ in copied] + ["evals/host/Containerfile"])


def test_base_image_is_pinned_by_digest():
    m = re.search(r"^ARG BASE=(\S+)$", CF.read_text(), re.M)
    assert m and re.search(r"@sha256:[0-9a-f]{64}$", m.group(1))


def test_toolchains_come_from_the_repo_installer_and_no_distro_compiler():
    text = CF.read_text()
    assert "eval-host.sh setup --prefix" in text
    for pkg in ("golang", "rustc", "cargo", "python3 ", "zig"):
        assert not re.search(r"apt-get install[^;]*\b" + re.escape(pkg), text), pkg
    assert "OPENBEAST_EVAL_NO_PROVISION=1" in text
    assert re.search(r"^USER eval$", text, re.M), "the harness runs as a non-root user"


def test_tag_is_stable_and_follows_the_pins():
    a = _run(["tag"]).stdout.strip()
    b = _run(["tag"]).stdout.strip()
    assert a == b and re.fullmatch(r"openbeast-eval-host:[0-9a-f]{12}", a)
    assert _run(["tag"], {"OPENBEAST_EVAL_IMAGE": "x:y"}).stdout.strip() == "x:y"


def test_run_passes_key_names_never_values_and_loosens_nothing(fake_docker):
    docker, log = fake_docker
    secret = "sk-not-a-real-key-0123456789"
    r = _run(["run", "--pass", "MY_TOKEN", "--", "python3", "-V"],
             {"OPENBEAST_SPARK_API_KEY": secret, "MY_TOKEN": secret}, docker)
    assert r.returncode == 0, r.stderr
    runs = [ln for ln in log.read_text().splitlines() if ln.startswith("run ")]
    assert len(runs) == 1
    argv = runs[0]
    assert secret not in argv and secret not in r.stdout + r.stderr
    assert "-e OPENBEAST_SPARK_API_KEY" in argv and "-e MY_TOKEN" in argv
    for need in ("--network host", "--init", "--cap-drop ALL",
                 "--security-opt no-new-privileges", f"-v {ROOT.resolve()}:{ROOT.resolve()}"):
        assert need in argv, need
    for bad in ("--privileged", "seccomp=unconfined", "apparmor=unconfined", "--cap-add",
                "--pid host", "--userns host", "--user root", "docker.sock"):
        assert bad not in argv, bad
    assert argv.endswith("python3 -V")


def test_pass_takes_a_name_only(fake_docker):
    docker, _log = fake_docker
    r = _run(["run", "--pass", "A=b", "--", "true"], docker=docker)
    assert r.returncode != 0 and "NAME" in r.stderr


def test_load_refuses_a_sha256_mismatch(tmp_path, fake_docker):
    docker, log = fake_docker
    f = tmp_path / "img.tar.gz"
    f.write_bytes(b"not an image")
    (tmp_path / "img.tar.gz.sha256").write_text("0" * 64 + "  img.tar.gz\n")
    r = _run(["load", str(f)], docker=docker)
    assert r.returncode != 0 and "SHA256 MISMATCH" in r.stderr
    assert not any(ln.startswith("load") for ln in log.read_text().splitlines())


def test_help_is_the_header():
    r = _run(["--help"])
    assert r.returncode == 0 and "eval-host-container" in r.stdout and "OFFLINE" in r.stdout


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck_clean():
    r = subprocess.run(["shellcheck", "-x", "-P", "SCRIPTDIR", str(SCRIPT)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
