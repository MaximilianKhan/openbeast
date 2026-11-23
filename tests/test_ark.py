#!/usr/bin/env python3
"""beast-ark (agents/ark, scripts/ark.sh): hermetic tests. docs/BEAST_ARK.md.

Everything runs against fakes on 127.0.0.1: an HTTP file server (Range, a
truncating fault, a request log), a fake OCI registry (bearer tokens, a CDN
redirect that must never see the token, push), and a tiny git repo. No
network, no docker, no real catalog download.

Covered: the TOML subset vs tomllib on the real catalog; the catalog lint
(and that the REAL catalog is clean: every entry has a licence, a pin, and a
hash or an explicit unverified mark); repo-pin parity (llama.cpp, TensorFold,
zig, the compose images); plan math; fetch -> ark.lock -> verify -> restore
for a toy toolchain, a git pin and a toy OCI image (registry push and a
docker load); a checksum mismatch refused (catalog pin, upstream sums,
signature); resume over Range; verify catching corruption and a missing file;
lock filling pins from upstream and update never writing without --write;
serve (static, Range, registry, pip index, read-only, unlisted files); the
offline refusal; Ed25519 / minisign; and no secret in any output.

Run: python3 -m pytest tests/test_ark.py -q
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agents"))

from ark import catalog as C   # noqa: E402
from ark import cli, oci, pins, tomlsub  # noqa: E402
from ark import fetch as F     # noqa: E402
from ark import restore as R   # noqa: E402
from ark import serve as SV    # noqa: E402
from ark import store as S     # noqa: E402
from ark import verify as V    # noqa: E402

ARK_SH = REPO / "scripts" / "ark.sh"
HAVE_GIT = shutil.which("git") is not None


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENBEAST_OFFLINE", "false")
    monkeypatch.delenv("ARK_FORCE_OFFLINE", raising=False)
    monkeypatch.delenv("ARK_CATALOG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    pins._SUMS.clear()
    pins._RELEASES.clear()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── fixture HTTP server ──────────────────────────────────────────────────

class Files:
    def __init__(self):
        self.files: dict = {}
        self.log: list = []
        self.truncate: dict = {}     # path -> bytes to send before dropping (once)
        self.ctypes: dict = {}       # path -> Content-Type (a PEP 503 page must say text/html)

    def handler(self):
        fs = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                fs.log.append((self.command, self.path, dict(self.headers)))
                data = fs.files.get(self.path)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                m = re.fullmatch(r"bytes=(\d+)-", self.headers.get("Range", ""))
                if m:
                    start = int(m.group(1))
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                body = data[start:]
                self.send_response(206 if m else 200)
                self.send_header("Content-Length", str(len(body)))
                if self.path in fs.ctypes:
                    self.send_header("Content-Type", fs.ctypes[self.path])
                if m:
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.end_headers()
                if self.command == "HEAD":
                    return
                cut = fs.truncate.pop(self.path, None)
                if cut is not None:
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.connection.shutdown(2)
                    return
                self.wfile.write(body)
        return H


@pytest.fixture
def http():
    fs = Files()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fs.handler())
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    fs.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield fs
    srv.shutdown()
    srv.server_close()


# ── toy payloads ─────────────────────────────────────────────────────────

def toy_toolchain(version="1.0") -> bytes:
    """toy-<v>/bin/toy, a shell script, in a .tar.gz with one top directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        data = f"#!/bin/sh\necho toy {version}\n".encode()
        ti = tarfile.TarInfo(f"toy-{version}/bin/toy")
        ti.size, ti.mode = len(data), 0o755
        t.addfile(ti, io.BytesIO(data))
        ti = tarfile.TarInfo(f"toy-{version}/README")
        ti.size = 3
        t.addfile(ti, io.BytesIO(b"hi\n"))
    return gzip.compress(buf.getvalue(), mtime=0)      # deterministic bytes, so a stable sha256


HEADER = '[ark]\nschema = 1\n'
ENTRY = '''
[[entry]]
name = "{name}"
category = "{cat}"
description = "test"
homepage = "https://example.org"
license = "MIT"
version = "{version}"
'''


def write_catalog(tmp_path, body: str) -> Path:
    p = tmp_path / "catalog.toml"
    p.write_text(HEADER + body)
    return p


def run(argv, capsys=None):
    rc = cli.main(argv)
    out = capsys.readouterr() if capsys else None
    return rc, out


# ── the real catalog ─────────────────────────────────────────────────────

def test_real_catalog_lints_clean():
    cat = C.load()
    errs = C.lint(cat)
    assert errs == [], "\n".join(errs[:40])
    assert len(cat.entries) >= 100 and len(cat.artifacts) >= 300


def test_every_real_entry_has_licence_pin_and_hash_or_unverified():
    cat = C.load()
    for e in cat.entries.values():
        assert e.license, e.name
        assert C.license_is_osi(e.license) or e.license_note, e.name
        assert e.version, e.name
        if e.git:
            assert re.fullmatch(r"[0-9a-f]{40}", e.commit) or e.git_verified == "unverified", e.name
    for a in cat.artifacts:
        if a.kind == "file":
            assert a.pinned() or a.verified == "unverified", a.id
            if a.pinned():
                assert a.verified in ("upstream-sums", "upstream-json", "pinned", "tofu", "signature"), a.id
        elif a.kind == "oci":
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", a.digest) or a.verified == "unverified", a.id
        else:
            assert a.verified == "tofu", a.id


def test_real_catalog_covers_the_required_ground():
    names = set(C.load().entries)
    need = {"docker", "containerd", "runc", "buildkit", "podman", "kubernetes", "k3s", "kind", "helm",
            "distribution", "cpython-3.14", "go", "rust", "zig", "node", "temurin-25", "llvm", "lang-images",
            "cmake", "ninja", "meson", "bazelisk", "bazel", "postgres", "valkey", "sqlite", "duckdb",
            "clickhouse", "nats", "kafka", "prometheus", "grafana", "loki", "otelcol-contrib", "headscale",
            "wireguard-tools", "caddy", "nginx", "coredns", "forgejo", "git", "gh", "minio", "restic",
            "step-ca", "trivy", "cosign", "llama.cpp", "vllm", "tensorfold", "tensorfold-1.2.10", "kiwix-tools", "devdocs-zim-core"}
    assert need <= names, sorted(need - names)
    plats = {a.platform for a in C.load().artifacts if a.kind == "file"}
    assert {"linux-x86_64", "linux-aarch64", "darwin-arm64"} <= plats


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is 3.11+")
def test_subset_parser_matches_tomllib_on_the_real_catalog():
    import tomllib
    text = (REPO / "agents" / "ark" / "catalog.toml").read_text()
    mine, _ = tomlsub.parse(text)
    theirs = tomllib.loads(text)

    def plain(x):
        if isinstance(x, dict):
            return {k: plain(v) for k, v in x.items()}
        if isinstance(x, list):
            return [plain(v) for v in x]
        return x
    assert plain(mine) == theirs


def test_repo_pins_and_the_catalog_agree():
    cat = C.load()
    ref = re.search(r"^LLAMA_CPP_REF=([0-9a-f]{40})$", (REPO / "scripts/llama.cpp.ref").read_text(), re.M).group(1)
    assert cat.entries["llama.cpp"].commit == ref
    inst = (REPO / "scripts/backends/tensorfold/install-native.sh").read_text()
    assert cat.entries["tensorfold"].version == re.search(r"^VERSION=(\S+)$", inst, re.M).group(1)
    # The default is the newest signed release with verified pins (1.3.4, 2026-11-23); the
    # Sparks' recipes pin their own release (TENSORFOLD_REQUIRES), so this moves none of them.
    tf = cat.entries["tensorfold"]
    assert tf.version == "1.3.4" and tf.commit == "12ebd414de443485d281768036ac4835e5abe8fe"
    assert {a.platform: a.sha256 for a in tf.artifacts} == {
        "linux-x86_64": "8336d2866cef7929ac8c68a44801956aa43c3cc24e3eeac003f4f6b4d87614f1",
        "linux-aarch64": "02566a5297dd67db9b0b834737fe73e192907454896ee7d097d3a0dfca303937",
        "darwin-arm64": "4e14cbfa94af7d45f1bb9ad05495b10009a58480e5644699b52a0cd7f1755823"}
    # A second, held entry: 1.2.10, the native build on both Sparks and the release the GLM
    # and MiniMax recipes pin. Same repo, key, signature scheme and archive layout as the default.
    old = cat.entries["tensorfold-1.2.10"]
    assert old.version == "1.2.10" and old.commit == "2a674b552498739e783a31c2573fc9ccf4d8cc0d"
    assert old.update == "manual" and old.git == tf.git and "core" in old.profiles
    assert {a.platform: (a.sha256, a.size) for a in old.artifacts} == {
        "linux-x86_64": ("bc77f6d4112dd67a3298bd2560b9f0e7c9a54d3c5b0036a9c52c0d27f6c7e9fd", 52293655),
        "linux-aarch64": ("c335c44f81d55847a5f81d74a94ac3d09f81bd6688247cff972cf1f63fc18105", 51781328),
        "darwin-arm64": ("76eb33cce9e297f156cfe5cbb0cec664b0266f4ecc3d103176adadc6aee21e58", 7651150)}
    for e in (tf, old):
        for a in e.artifacts:
            assert (a.verified, a.signature_kind, a.signature_key, a.signature_prefix) == (
                "upstream-sums", "ed25519-sums", "agents/ark/keys/tensorfold-release-key.pem",
                "tensorfold-native-checksum-v1\n"), a.id
            assert a.filename == f"tensorfold-{e.version}-{a.platform.replace('darwin', 'macos')}.tar.gz", a.id
    # Every native TensorFold release a shipped recipe pins has an ark copy.
    have = {e.version for e in cat.entries.values() if e.git == tf.git}
    pinned = {m.group(1) for f in (REPO / "scripts/backends/models").glob("recipe-*.env")
              for m in re.finditer(r"^TENSORFOLD_REQUIRES=(\S+)$", f.read_text(), re.M)}
    assert pinned and pinned <= have, sorted(pinned - have)
    assert (REPO / "agents/ark/keys/tensorfold-release-key.pem").read_bytes() == \
        (REPO / "scripts/backends/tensorfold/release-key.pem").read_bytes()
    ci = (REPO / ".github/workflows/ci.yml").read_text()
    zig_ci = re.search(r"ZIG_SHA256: ([0-9a-f]{64})", ci).group(1)
    zx = next(a for a in cat.entries["zig"].artifacts if a.platform == "linux-x86_64")
    assert cat.entries["zig"].version == "0.16.0" and zx.sha256 == zig_ci
    compose = (REPO / "docker-compose.yml").read_text()
    for entry, img in (("open-webui", "ghcr.io/open-webui/open-webui"), ("searxng", "searxng/searxng")):
        dig = re.search(re.escape(img) + r":[^@\s]+@(sha256:[0-9a-f]{64})", compose).group(1)
        assert cat.entries[entry].artifacts[0].digest == dig, entry


# ── catalog lint (negative controls) ─────────────────────────────────────

def test_lint_refuses_a_blank_pin_a_bad_licence_and_unknown_fields(tmp_path):
    body = ENTRY.format(name="a", cat="cli", version="1") + '''
[[artifact]]
entry = "a"
kind = "file"
platform = "linux-x86_64"
url = "https://example.org/a.tar.gz"
''' + ENTRY.format(name="b", cat="cli", version="1").replace('"MIT"', '"SSPL-1.0"') + '''
[[artifact]]
entry = "b"
kind = "oci"
image = "docker.io/library/b"
tag = "1"
platforms = ["linux/amd64"]
'''
    cat = C.loads(HEADER + body)
    errs = "\n".join(C.lint(cat))
    assert "artifact a/linux-x86_64/a.tar.gz: no sha256" in errs
    assert "license 'SSPL-1.0' is not OSI-approved" in errs
    assert "no digest" in errs
    with pytest.raises(C.CatalogError, match="unknown field 'sha265'"):
        C.loads(HEADER + ENTRY.format(name="c", cat="cli", version="1") + 'sha265 = "x"\n')
    with pytest.raises(C.CatalogError, match="missing license"):
        C.loads(HEADER + ENTRY.format(name="d", cat="cli", version="1").replace('license = "MIT"\n', ""))
    # the explicit mark is what makes a hashless artifact acceptable
    cat = C.loads(HEADER + body.replace('url = "https://example.org/a.tar.gz"\n',
                                        'url = "https://example.org/a.tar.gz"\nverified = "unverified"\n'))
    assert not [e for e in C.lint(cat) if e.startswith("artifact a/")]
    # a hash with no provenance is refused too
    cat = C.loads(HEADER + body.replace('url = "https://example.org/a.tar.gz"\n',
                                        'url = "https://example.org/a.tar.gz"\nsha256 = "' + "0" * 64 + '"\n'))
    assert any("say where the hash came from" in e for e in C.lint(cat))
    # plain http is for loopback only
    cat = C.loads(HEADER + body.replace("https://example.org/a.tar.gz", "http://example.org/a.tar.gz"))
    assert any("must be https://" in e for e in C.lint(cat))


def test_toml_editor_rewrites_one_line_only():
    text = '[ark]\nschema = 1\n\n[[entry]]\nname = "x"  # keep me\nsha256 = ""\nlist = [\n  "a",\n  "b",\n]\n\n[[entry]]\nname = "y"\n'
    doc, tables = tomlsub.parse(text)
    t = tables[2]
    ed = pins.Edits()
    ed.set(t, "sha256", "ab" * 32)
    ed.set(t, "list", ["c"])
    ed.set(t, "size", 5)
    ed.set(tables[3], "commit", "f" * 40)
    cat = C.Catalog("x", text, {}, {}, [], tables)
    new = ed.apply(cat)
    d2, _ = tomlsub.parse(new)
    assert d2["entry"][0]["sha256"] == "ab" * 32 and d2["entry"][0]["list"] == ["c"] and d2["entry"][0]["size"] == 5
    assert d2["entry"][1]["commit"] == "f" * 40 and d2["entry"][1]["name"] == "y"
    assert 'name = "x"  # keep me' in new


# ── plan math ────────────────────────────────────────────────────────────

def test_plan_math_per_platform_core_and_union(tmp_path, capsys):
    body = ENTRY.format(name="t", cat="languages", version="1") + 'profiles = ["core"]\n' + '''
[[artifact]]
entry = "t"
kind = "file"
platform = "linux-x86_64"
url = "https://example.org/t-x86.tgz"
sha256 = "''' + "1" * 64 + '''"
size = 100
verified = "pinned"

[[artifact]]
entry = "t"
kind = "file"
platform = "darwin-arm64"
url = "https://example.org/t-mac.tgz"
sha256 = "''' + "2" * 64 + '''"
size = 30
verified = "pinned"

[[artifact]]
entry = "t"
kind = "file"
platform = "any"
url = "https://example.org/t-src.tgz"
sha256 = "''' + "3" * 64 + '''"
size = 7
verified = "pinned"

[[artifact]]
entry = "t"
kind = "oci"
image = "docker.io/library/t"
tag = "1"
digest = "sha256:''' + "4" * 64 + '''"
platforms = ["linux/amd64", "linux/arm64"]
platform_sizes = ["linux/amd64=1000", "linux/arm64=900"]
verified = "registry"
''' + ENTRY.format(name="u", cat="databases", version="2") + '''
[[artifact]]
entry = "u"
kind = "file"
platform = "linux-aarch64"
url = "https://example.org/u.tgz"
verified = "unverified"
'''
    cat = C.loads(HEADER + body)
    t = cli.plan_table(cat, list(cat.entries.values()), list(C.PLATFORMS))
    lang = t["categories"]["languages"]
    assert lang["linux-x86_64"][0] == 100 + 7 + 1000
    assert lang["linux-aarch64"][0] == 7 + 900
    assert lang["darwin-arm64"][0] == 30 + 7 + 900          # a Mac runs linux/arm64 images
    assert t["categories"]["databases"]["linux-aarch64"] == [0, 1, 0]   # one artifact of unknown size
    # one drive with all three: 'any' once, each OCI platform once
    assert t["union"] == [100 + 30 + 7 + 1000 + 900, 1]
    core = cli.plan_table(cat, cat.select(profile="core"), list(C.PLATFORMS))
    assert "databases" not in core["categories"]
    p = write_catalog(tmp_path, body)
    rc, out = run(["--catalog", str(p), "plan", "--profile", "both", "--platform", "linux-x86_64"], capsys)
    assert rc == 0 and "profile full: 2 entries" in out.out and "profile core: 1 entries" in out.out


def test_real_catalog_plan_runs(capsys):
    rc, out = run(["plan", "--profile", "both", "--json"], capsys)
    assert rc == 0
    d = json.loads(out.out)
    assert d["full"]["entries"] > d["core"]["entries"] > 30


# ── git fixture ──────────────────────────────────────────────────────────

def make_repo(tmp_path) -> tuple:
    repo = tmp_path / "upstream"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")

    def g(*a):
        return subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True, env=env).stdout.strip()
    g("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n")
    g("add", "a.txt")
    g("commit", "-q", "-m", "one")
    (repo / "a.txt").write_text("two\n")
    g("commit", "-q", "-am", "two")
    g("tag", "-a", "v1.0", "-m", "v1.0")
    commit = g("rev-parse", "v1.0^{commit}")
    return repo, commit


# ── fetch / verify / restore ─────────────────────────────────────────────

def toy_catalog(http, tmp_path, repo=None, commit="", tar_pin=True, history="shallow") -> Path:
    tgz = toy_toolchain()
    http.files["/rel/toy-1.0-linux.tar.gz"] = tgz
    http.files["/rel/SHA256SUMS"] = f"{sha(tgz)}  toy-1.0-linux.tar.gz\n{'9' * 64}  other.tar.gz\n".encode()
    single = b"#!/bin/sh\necho single\n"
    http.files["/rel/single"] = single
    http.files["/rel/single.sha256"] = (sha(single) + "\n").encode()
    body = ENTRY.format(name="toy", cat="languages", version="1.0")
    if repo:
        body += f'git = "file://{repo}"\ncommit = "{commit}"\n'
        if history != "shallow":
            body += f'history = "{history}"\n'
    body += f'''
[[artifact]]
entry = "toy"
kind = "file"
platform = "any"
url = "{http.base}/rel/toy-{{version}}-linux.tar.gz"
{('sha256 = "' + sha(tgz) + '"') if tar_pin else ''}
checksum_url = "{http.base}/rel/SHA256SUMS"
install = "tar"
verified = "{'upstream-sums' if tar_pin else ''}"

[[artifact]]
entry = "toy"
kind = "file"
platform = "any"
url = "{http.base}/rel/single"
sha256 = "{sha(single)}"
checksum_url = "{http.base}/rel/single.sha256"
install = "bin"
bins = ["single"]
verified = "upstream-sums"
'''
    return write_catalog(tmp_path, body)


@pytest.mark.skipif(not HAVE_GIT, reason="git not installed")
@pytest.mark.parametrize("history", ["shallow", "full"])
def test_fetch_lock_verify_restore_round_trip(http, tmp_path, capsys, history):
    repo, commit = make_repo(tmp_path)
    cat = toy_catalog(http, tmp_path, repo, commit, history=history)
    drive = tmp_path / "drive"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive), "--platform", "linux-x86_64"], capsys)
    assert rc == 0, out.err
    lock = json.loads((drive / "ark.lock").read_text())
    assert set(lock["items"]) == {"git:toy", "toy/any/toy-V-linux.tar.gz", "toy/any/single"}
    g = lock["items"]["git:toy"]
    assert g["commit"] == commit and g["verified"] == "pinned"
    assert g["files"][0]["path"].endswith(".bundle" if history == "full" else ".shallow.git.tar")
    tar = lock["items"]["toy/any/toy-V-linux.tar.gz"]
    assert tar["verified"] == "pinned+upstream-sums"
    sums = (drive / "SHA256SUMS").read_text()
    assert f"{sha(toy_toolchain())}  files/toy/1.0/any/toy-1.0-linux.tar.gz" in sums
    assert (drive / "ark").exists() and (drive / "tool/agents/ark/cli.py").exists()
    assert (drive / "catalog.toml").read_text() == cat.read_text()
    assert "toy" in (drive / "ARK.md").read_text()
    # second run: everything present, nothing re-downloaded
    n = len(http.log)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive), "--platform", "linux-x86_64"], capsys)
    assert rc == 0 and "3 already present" in out.err
    assert not [r for r in http.log[n:] if "toy-1.0" in r[1]]
    rc, out = run(["verify", str(drive)], capsys)
    assert rc == 0 and "RESULT: OK" in out.out
    # restore the toolchain and run it; restore the source and check the commit
    prefix, bindir, src = tmp_path / "opt", tmp_path / "bin", tmp_path / "src"
    rc, out = run(["restore", "toy", "--from", str(drive), "--platform", "linux-x86_64", "--prefix", str(prefix),
                   "--bin", str(bindir), "--src", str(src)], capsys)
    assert rc == 0, out.err
    assert subprocess.run([str(bindir / "toy")], capture_output=True, text=True).stdout == "toy 1.0\n"
    assert subprocess.run([str(bindir / "single")], capture_output=True, text=True).stdout == "single\n"
    assert (prefix / "toy").resolve() == (prefix / "toy-1.0").resolve()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src / "toy", capture_output=True, text=True).stdout.strip()
    assert head == commit and (src / "toy" / "a.txt").read_text() == "two\n"
    # the drive's own wrapper runs the tool with no checkout
    r = subprocess.run([str(drive / "ark"), "verify", str(drive)], capture_output=True, text=True)
    assert r.returncode == 0 and "RESULT: OK" in r.stdout, r.stderr


@pytest.mark.skipif(not HAVE_GIT, reason="git not installed")
def test_a_moved_tag_is_refused(http, tmp_path, capsys):
    repo, commit = make_repo(tmp_path)
    cat = toy_catalog(http, tmp_path, repo, "0" * 40)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d"), "--kinds", "git"], capsys)
    assert rc == 4 and "REFUSED" in out.err and f"is commit {commit}" in out.err
    assert not list((tmp_path / "d").glob("git/toy/*"))


def test_checksum_mismatch_against_the_pin_is_refused_and_deleted(http, tmp_path, capsys):
    cat = toy_catalog(http, tmp_path)
    http.files["/rel/toy-1.0-linux.tar.gz"] = toy_toolchain() + b"tampered"
    drive = tmp_path / "drive"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)
    assert rc == 4 and "sha256 MISMATCH" in out.err and "REFUSED" in out.err
    d = drive / "files/toy/1.0/any"
    assert not (d / "toy-1.0-linux.tar.gz").exists() and not (d / "toy-1.0-linux.tar.gz.part").exists()
    lock = json.loads((drive / "ark.lock").read_text())
    assert "toy/any/toy-V-linux.tar.gz" not in lock["items"] and "toy/any/single" in lock["items"]


def test_upstream_sums_that_disagree_are_refused(http, tmp_path, capsys):
    cat = toy_catalog(http, tmp_path, tar_pin=False)
    http.files["/rel/SHA256SUMS"] = f"{'a' * 64}  toy-1.0-linux.tar.gz\n".encode()
    text = cat.read_text().replace('verified = ""', 'verified = "unverified"')
    cat.write_text(text)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 4 and "upstream checksum file says" in out.err


def test_a_sums_file_for_another_name_never_vouches(http, tmp_path, capsys):
    cat = toy_catalog(http, tmp_path, tar_pin=False)
    http.files["/rel/SHA256SUMS"] = f"{sha(toy_toolchain())}  toy-0.9-linux.tar.gz\n".encode()
    cat.write_text(cat.read_text().replace('verified = ""', 'verified = "unverified"'))
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 4 and "does not name 'toy-1.0-linux.tar.gz'" in out.err


def test_unverified_needs_allow_and_is_recorded_as_tofu(http, tmp_path, capsys):
    data = b"no sums upstream\n"
    http.files["/x/blob.bin"] = data
    body = ENTRY.format(name="nosum", cat="cli", version="1") + f'''
[[artifact]]
entry = "nosum"
kind = "file"
platform = "any"
url = "{http.base}/x/blob.bin"
install = "data"
verified = "unverified"
'''
    cat = write_catalog(tmp_path, body)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 4 and "--allow-unverified" in out.err
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d"), "--allow-unverified"], capsys)
    assert rc == 0
    it = json.loads((tmp_path / "d/ark.lock").read_text())["items"]["nosum/any/blob.bin"]
    assert it["verified"] == "tofu"
    rc, out = run(["verify", str(tmp_path / "d")], capsys)
    assert rc == 0 and "trusted on first use" in out.out
    # lock --from copies the drive's hash into the catalog, marked tofu
    rc, out = run(["--catalog", str(cat), "lock", "--from", str(tmp_path / "d")], capsys)
    assert rc == 0
    a = C.load(str(cat)).artifacts[0]
    assert a.sha256 == sha(data) and a.verified == "tofu" and a.size == len(data)
    assert C.lint(C.load(str(cat))) == []
    # a later drive filled from that catalog still says tofu, and verify still names it
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d2")], capsys)
    assert rc == 0, out.err
    it = json.loads((tmp_path / "d2/ark.lock").read_text())["items"]["nosum/any/blob.bin"]
    assert it["verified"] == "tofu-pinned"
    rc, out = run(["verify", str(tmp_path / "d2")], capsys)
    assert rc == 0 and "trusted on first use" in out.out and "nosum/any/blob.bin" in out.out


def test_resume_continues_a_partial_download_with_range(http, tmp_path, capsys):
    cat = toy_catalog(http, tmp_path)
    tgz = toy_toolchain()
    http.truncate["/rel/toy-1.0-linux.tar.gz"] = len(tgz) // 2     # the first attempt dies half way
    drive = tmp_path / "drive"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)
    assert rc == 0, out.err
    gets = [h for (m, p, h) in http.log if m == "GET" and p == "/rel/toy-1.0-linux.tar.gz"]
    assert len(gets) == 2 and "Range" not in gets[0] and gets[1]["Range"] == f"bytes={len(tgz) // 2}-"
    assert (drive / "files/toy/1.0/any/toy-1.0-linux.tar.gz").read_bytes() == tgz
    # a .part left by an interrupted run is picked up by the next one
    shutil.rmtree(drive)
    d = drive / "files/toy/1.0/any"
    d.mkdir(parents=True)
    (d / "toy-1.0-linux.tar.gz.part").write_bytes(tgz[:100])
    n = len(http.log)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)
    assert rc == 0
    assert [h.get("Range") for (m, p, h) in http.log[n:] if p == "/rel/toy-1.0-linux.tar.gz"] == ["bytes=100-"]
    assert (d / "toy-1.0-linux.tar.gz").read_bytes() == tgz


def test_fetch_hashes_each_file_once_and_each_sums_file_once(http, tmp_path, capsys, monkeypatch):
    from ark import net
    tgz = toy_toolchain()
    other = b"another file in the same release\n"
    http.files["/rel/toy-1.0-linux.tar.gz"] = tgz
    http.files["/rel/other.bin"] = other
    http.files["/rel/SHA256SUMS"] = f"{sha(tgz)}  toy-1.0-linux.tar.gz\n{sha(other)}  other.bin\n".encode()
    body = ENTRY.format(name="toy", cat="languages", version="1.0")
    for fn, data in (("toy-{version}-linux.tar.gz", tgz), ("other.bin", other)):
        body += f'''
[[artifact]]
entry = "toy"
kind = "file"
platform = "any"
url = "{http.base}/rel/{fn}"
sha256 = "{sha(data)}"
checksum_url = "{http.base}/rel/SHA256SUMS"
install = "data"
verified = "upstream-sums"
'''
    reads = []
    real_sha, real_hash = net.sha256_file, net.hash_file
    monkeypatch.setattr(net, "sha256_file", lambda p: reads.append(os.path.basename(p)) or real_sha(p))
    monkeypatch.setattr(net, "hash_file", lambda p, a: reads.append(os.path.basename(p)) or real_hash(p, a))
    rc, out = run(["--catalog", str(write_catalog(tmp_path, body)), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 0, out.err
    assert reads.count("toy-1.0-linux.tar.gz.part") == 1 and "toy-1.0-linux.tar.gz" not in reads
    assert [p for (m, p, h) in http.log if p == "/rel/SHA256SUMS"] == ["/rel/SHA256SUMS"]


def test_verify_detects_corruption_missing_and_planted_files(http, tmp_path, capsys):
    cat = toy_catalog(http, tmp_path)
    drive = tmp_path / "drive"
    assert run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)[0] == 0
    f = drive / "files/toy/1.0/any/toy-1.0-linux.tar.gz"
    b = bytearray(f.read_bytes())
    b[10] ^= 0xFF
    f.write_bytes(bytes(b))
    (drive / "files/toy/1.0/any/single").unlink()
    (drive / "files/toy/planted.sh").write_text("evil")
    rc, out = run(["verify", str(drive)], capsys)
    assert rc == 1 and "RESULT: FAILED" in out.out
    assert "CORRUPT: files/toy/1.0/any/toy-1.0-linux.tar.gz" in out.out
    assert "MISSING: files/toy/1.0/any/single" in out.out
    assert "files/toy/planted.sh" in out.out
    rc, out2 = run(["verify", str(drive), "--jobs", "4"], capsys)          # parallel: the same verdict
    assert rc == 1 and out2.out == out.out
    # --quick sees the missing file but NOT a same-size flipped byte, and says it did not re-hash
    rc, out = run(["verify", str(drive), "--quick"], capsys)
    assert rc == 1 and "NOT re-hashed" in out.out
    assert "MISSING: files/toy/1.0/any/single" in out.out and "CORRUPT" not in out.out
    # restore refuses the corrupt file
    rc, out = run(["restore", "toy", "--from", str(drive), "--prefix", str(tmp_path / "o"), "--bin", str(tmp_path / "b")],
                  capsys)
    assert rc == 1 and ("CORRUPT" in out.err or "missing" in out.err)


def test_offline_refuses_network_commands_and_keeps_offline_ones(http, tmp_path, capsys, monkeypatch):
    cat = toy_catalog(http, tmp_path)
    drive = tmp_path / "drive"
    assert run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)[0] == 0
    monkeypatch.setenv("ARK_FORCE_OFFLINE", "1")
    n = len(http.log)
    for argv in (["fetch", "--to", str(tmp_path / "d2")], ["lock"], ["update"]):
        rc, out = run(["--catalog", str(cat)] + argv, capsys)
        assert rc == 3 and "needs the network" in out.err
    assert len(http.log) == n
    assert run(["verify", str(drive)], capsys)[0] == 0
    rc, _ = run(["restore", "toy", "--from", str(drive), "--prefix", str(tmp_path / "o"), "--bin", str(tmp_path / "b")],
                capsys)
    assert rc == 0


# ── archive extraction: nothing lands outside the target ───────────────

def _tar(path, members, mode="w"):
    """members: (name, kind, payload) — kind "f" file, "d" dir, "s" symlink, "h" hardlink."""
    with tarfile.open(path, mode) as t:
        for name, kind, payload in members:
            ti = tarfile.TarInfo(name)
            if kind == "f":
                data = payload.encode()
                ti.size, ti.mode = len(data), 0o755
                t.addfile(ti, io.BytesIO(data))
                continue
            ti.type = {"d": tarfile.DIRTYPE, "s": tarfile.SYMTYPE, "h": tarfile.LNKTYPE}[kind]
            ti.mode = 0o755
            ti.linkname = payload or ""
            t.addfile(ti)
    return path


ESCAPES = {
    # the reviewed chain: y -> ".", x -> "y/..", then x/evil (checked up front, x looked inside)
    "chain": [("y", "s", "."), ("x", "s", "y/.."), ("x/evil", "f", "ESCAPED")],
    # the same chain built backwards: a resolves inside when made, outside once b exists
    "late-chain": [("a", "s", "b/.."), ("b", "s", "."), ("a/evil", "f", "ESCAPED")],
    "dotdot-name": [("../evil", "f", "ESCAPED")],
    "absolute-name": [("/tmp/ark-evil-absolute", "f", "ESCAPED")],
    "absolute-link": [("l", "s", "/etc"), ("l/evil", "f", "ESCAPED")],
    "dotdot-link": [("l", "s", "../"), ("l/evil", "f", "ESCAPED")],
    "hardlink-out": [("h", "h", "../outside.txt")],
    "dangling-out": [("a", "s", "b/.."), ("b", "s", ".")],
}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("data_filter", [True, False], ids=["data-filter", "no-filter-py3.9"])
@pytest.mark.parametrize("case", sorted(ESCAPES))
def test_safe_extract_refuses_every_escape(tmp_path, monkeypatch, case, data_filter):
    if data_filter and not hasattr(tarfile, "data_filter"):
        pytest.skip("this python has no tarfile.data_filter")
    # the no-filter leg is what a Mac's /usr/bin/python3 3.9.6 runs: ark's own checks, alone
    monkeypatch.setattr(F, "_DATA_FILTER", data_filter)
    work = tmp_path / "work"
    dest = work / "dest"
    dest.mkdir(parents=True)
    (work / "outside.txt").write_text("outside")
    src = _tar(tmp_path / "a.tar", ESCAPES[case])
    try:
        with tarfile.open(src, "r|") as t:
            F._safe_extract(t, str(dest))
        refused = False
    except F.FetchError:
        refused = True
    # ark's own checks must refuse every case. With the data filter on top, a
    # patched tarfile (3.12.11+, CVE-2025-4517) may instead REWRITE a chained
    # link so it stays inside (a -> "b/.." lands as a -> "."): not refused,
    # but just as contained, which is what the asserts below hold.
    assert refused or (data_filter and case in ("late-chain", "dangling-out")), case
    assert not (work / "evil").exists() and not Path("/tmp/ark-evil-absolute").exists()
    assert sorted(p.name for p in work.iterdir()) == ["dest", "outside.txt"]
    for p in dest.rglob("*"):                      # no link left pointing out of the target
        if p.is_symlink():
            assert str(p.resolve()).startswith(str(dest.resolve()))


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("data_filter", [True, False])
def test_safe_extract_keeps_ordinary_links_and_streams_compressed_archives(tmp_path, monkeypatch, data_filter):
    if data_filter and not hasattr(tarfile, "data_filter"):
        pytest.skip("this python has no tarfile.data_filter")
    monkeypatch.setattr(F, "_DATA_FILTER", data_filter)
    src = _tar(tmp_path / "ok.tar.xz", [("t-1/lib", "d", None), ("t-1/lib/tool", "f", "#!/bin/sh\necho ok\n"),
                                        ("t-1/bin", "d", None), ("t-1/bin/tool", "s", "../lib/tool"),
                                        ("t-1/bin/same", "h", "t-1/lib/tool")], mode="w:xz")
    dest = tmp_path / "out"
    R._extract(str(src), str(dest))                # restore's path: one streaming pass ("r|*")
    assert (dest / "t-1/bin/tool").resolve() == (dest / "t-1/lib/tool").resolve()
    assert (dest / "t-1/bin/same").read_text() == "#!/bin/sh\necho ok\n"
    assert os.access(dest / "t-1/lib/tool", os.X_OK)


def _evil_drive(tmp_path, item, payload=b"attacker content\n"):
    """A drive whose ark.lock (on the drive, so not trusted) names hostile paths."""
    drive = tmp_path / "evil"
    (drive / "files").mkdir(parents=True)
    (drive / "files/x").write_bytes(payload)
    lock = {"schema": 1, "items": {"k": dict({"kind": "file", "platform": "any", "version": "1", "primary": "files/x",
                                               "files": [{"path": "files/x", "sha256": sha(payload),
                                                          "size": len(payload)}]}, **item)}}
    S.Drive(str(drive)).write_lock(lock)
    return drive


@pytest.mark.parametrize("item,why", [
    ({"entry": "t", "install": "bin", "bins": ["../../../ESCAPED"]}, "not a file name"),
    ({"entry": "t", "install": "bin", "bins": [".."]}, "not a file name"),
    ({"entry": "t", "install": "gz", "bins": ["../.."]}, "not a file name"),
    ({"entry": "../../t", "install": "bin", "bins": ["x"]}, "not a catalog name"),
])
def test_restore_never_writes_where_the_drive_lock_says(tmp_path, item, why):
    payload = gzip.compress(b"x") if item["install"] == "gz" else b"attacker content\n"
    drive = _evil_drive(tmp_path, item, payload)
    home = tmp_path / "h"
    home.mkdir()
    with pytest.raises(R.RestoreError, match=why):
        it = S.Drive(str(drive)).read_lock()["items"]["k"]
        R.check_item(it)
        R.install_file(S.Drive(str(drive)), it, str(home / "opt"), str(home / "bin"))
    assert not list(tmp_path.rglob("ESCAPED"))
    assert not (home / "opt").exists() or not [p for p in (home / "opt").rglob("*") if p.is_file()]


def test_restore_refuses_tar_bins_and_git_values_that_leave_the_root(tmp_path, capsys):
    src = _tar(tmp_path / "t.tar.gz", [("t-1/bin", "d", None), ("t-1/bin/t", "f", "#!/bin/sh\n")], mode="w:gz")
    drive = _evil_drive(tmp_path, {"entry": "t", "install": "tar", "bins": ["../../../../victim"]},
                        Path(src).read_bytes())
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    victim.chmod(0o600)
    rc, out = run(["restore", "t", "--from", str(drive), "--prefix", str(tmp_path / "o"), "--bin", str(tmp_path / "b")],
                  capsys)
    assert rc == 1 and "outside the install root" in out.err
    assert victim.stat().st_mode & 0o777 == 0o600                   # never chmod +x'd
    lock = S.Drive(str(drive)).read_lock()
    git = {"entry": "t", "kind": "git", "platform": "any", "version": "1", "files": lock["items"]["k"]["files"]}
    for bad in ({"commit": "--upload-pack=touch x"}, {"commit": "a" * 40, "tag": "-f"},
                {"commit": "a" * 40, "tag": "v1/../../x"}):
        lock["items"]["k"] = dict(git, **bad)
        S.Drive(str(drive)).write_lock(lock)
        rc, out = run(["restore", "t", "--from", str(drive), "--src", str(tmp_path / "src")], capsys)
        assert rc == 1 and "refused" in out.err, bad
    assert not (tmp_path / "src").exists()
    rc, out = run(["restore", "../t", "--from", str(drive)], capsys)
    assert rc == 1 and "not an entry name" in out.err


# ── lock / update ────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAVE_GIT, reason="git not installed")
def test_lock_fills_pins_from_upstream_and_is_idempotent(http, tmp_path, capsys):
    repo, commit = make_repo(tmp_path)
    cat = toy_catalog(http, tmp_path, repo, "", tar_pin=False)
    cat.write_text(cat.read_text().replace('commit = ""\n', "").replace(f'git = "file://{repo}"\n',
                                                                        f'git = "file://{repo}"\ncommit = ""\n'))
    rc, out = run(["--catalog", str(cat), "lock", "--dry-run"], capsys)
    assert rc == 0 and f'+commit = "{commit}"' in out.out and "NOT written" in out.err
    assert 'commit = ""' in cat.read_text()
    rc, out = run(["--catalog", str(cat), "lock"], capsys)
    assert rc == 0
    c2 = C.load(str(cat))
    assert c2.entries["toy"].commit == commit
    a = c2.by_id()["toy/any/toy-V-linux.tar.gz"]
    assert a.sha256 == sha(toy_toolchain()) and a.verified == "upstream-sums" and a.size == len(toy_toolchain())
    assert C.lint(c2) == []
    rc, out = run(["--catalog", str(cat), "lock"], capsys)
    assert rc == 0 and "nothing to change" in out.err
    # a sha512 upstream lands in upstream_hash, not sha256
    tgz = toy_toolchain()
    http.files["/rel/SHA512SUMS"] = f"{hashlib.sha512(tgz).hexdigest()}  toy-1.0-linux.tar.gz\n".encode()
    text = cat.read_text().replace(f'sha256 = "{sha(tgz)}"\n', 'sha256 = ""\n', 1).replace(
        f"{http.base}/rel/SHA256SUMS", f"{http.base}/rel/SHA512SUMS")
    cat.write_text(text)
    pins._SUMS.clear()
    assert run(["--catalog", str(cat), "lock"], capsys)[0] == 0
    a = C.load(str(cat)).by_id()["toy/any/toy-V-linux.tar.gz"]
    assert a.upstream_hash == "sha512:" + hashlib.sha512(tgz).hexdigest() and not a.sha256
    assert run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d"), "--no-git"], capsys)[0] == 0


def test_lock_pins_a_github_asset_to_the_release_api_digest(http, tmp_path, capsys, monkeypatch):
    parse = pins.github_release_asset
    assert parse("https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-linux.zip") \
        == ("ninja-build/ninja", "v1.13.2", "ninja-linux.zip")
    assert parse("https://example.org/ninja-build/ninja/releases/download/v1/x") is None
    data = b"ninja binary\n"
    http.files["/rel/ninja-linux.zip"] = data
    http.files["/api/repos/ninja-build/ninja/releases/tags/v1.13.2"] = json.dumps({"assets": [
        {"name": "ninja-linux.zip", "digest": "sha256:" + sha(data)},
        {"name": "ninja-mac.zip", "digest": "sha256:" + "0" * 64}]}).encode()
    monkeypatch.setattr(pins, "GITHUB_API", f"{http.base}/api")
    monkeypatch.setattr(pins, "github_token", lambda: None)
    monkeypatch.setattr(pins, "github_release_asset",
                        lambda url: ("ninja-build/ninja", "v1.13.2", os.path.basename(url)) if "/rel/" in url else None)
    body = ENTRY.format(name="ninja", cat="build", version="1.13.2") + f'''
[[artifact]]
entry = "ninja"
kind = "file"
platform = "linux-x86_64"
url = "{http.base}/rel/ninja-linux.zip"
verified = "unverified"
'''
    cat = write_catalog(tmp_path, body)
    assert run(["--catalog", str(cat), "lock", "--no-git-sizes"], capsys)[0] == 0
    a = C.load(str(cat)).artifacts[0]
    assert a.sha256 == sha(data) and a.verified == "upstream-json"
    assert C.lint(C.load(str(cat))) == []
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 0 and "tofu" not in json.loads((tmp_path / "d/ark.lock").read_text())["items"][a.id]["verified"]


def test_update_prints_the_diff_and_writes_only_with_write(http, tmp_path, capsys, monkeypatch):
    cat = toy_catalog(http, tmp_path)
    cat.write_text(cat.read_text().replace('version = "1.0"\n', 'version = "1.0"\nupdate = "github:x/toy"\n'))
    monkeypatch.setattr(pins, "latest", lambda e: ("1.1", "test"))
    before = cat.read_text()
    rc, out = run(["--catalog", str(cat), "update"], capsys)
    assert rc == 0 and '+version = "1.1"' in out.out and "NOT written" in out.err
    assert cat.read_text() == before
    rc, out = run(["--catalog", str(cat), "update", "--write"], capsys)
    assert rc == 0
    c2 = C.load(str(cat))
    assert c2.entries["toy"].version == "1.1"
    a = c2.by_id()["toy/any/toy-V-linux.tar.gz"]
    assert a.sha256 == "" and a.verified == ""                        # cleared for `lock`
    assert c2.by_id()["toy/any/single"].sha256                         # not templated: untouched
    assert any("no sha256" in e for e in C.lint(c2))                   # lint fails until lock runs


def test_update_clears_pins_reached_through_tag_and_vars(http, tmp_path, capsys, monkeypatch):
    # jq's shape: the url never spells {version}; it goes through {tag} (and here a var too)
    body = ENTRY.format(name="jq", cat="cli", version="1.8.2") + \
        'tag = "jq-{version}"\nupdate = "github:jqlang/jq"\nvars = ["pu=v{version_pu}"]\n' + f'''
[[artifact]]
entry = "jq"
kind = "file"
platform = "linux-x86_64"
url = "{http.base}/releases/download/{{tag}}/jq-linux-amd64"
sha256 = "{'b' * 64}"
size = 2267912
verified = "upstream-sums"
checksum_url = "{http.base}/releases/download/{{tag}}/sha256sum.txt"

[[artifact]]
entry = "jq"
kind = "file"
platform = "darwin-arm64"
url = "{http.base}/x/{{pu}}/jq-macos"
sha256 = "{'c' * 64}"
verified = "upstream-sums"

[[artifact]]
entry = "jq"
kind = "file"
platform = "any"
url = "{http.base}/fixed/jq.1"
sha256 = "{'d' * 64}"
verified = "upstream-sums"
'''
    cat = write_catalog(tmp_path, body)
    monkeypatch.setattr(pins, "latest", lambda e: ("1.8.3", "test"))
    rc, out = run(["--catalog", str(cat), "update", "--write"], capsys)
    assert rc == 0, out.err
    c2 = C.load(str(cat))
    by = {a.platform: a for a in c2.artifacts}
    assert by["linux-x86_64"].url_x.endswith("/jq-1.8.3/jq-linux-amd64")
    assert by["linux-x86_64"].sha256 == "" and by["linux-x86_64"].size == 0      # {tag} moved: cleared
    assert by["darwin-arm64"].sha256 == ""                                       # a var built on the version
    assert by["any"].sha256 == "d" * 64                                          # nothing moved: kept
    errs = C.lint(c2)
    assert any("linux-x86_64" in e and "no sha256" in e for e in errs)           # lint fails until lock runs


def test_stable_version_filter():
    for v in ("1.2.3", "1.37.2+k3s1", "0.0.20250522", "2025.10.15", "21.0.13+9"):
        assert pins._stable(v), v
    for v in ("show", "v0.0.2020", "2.22.0b2", "1.0a1", "0.33.0rc3", "4.4.0-rc4", "1.2.3-beta.1"):
        assert not pins._stable(v), v


# ── checksum parsing and signatures ──────────────────────────────────────

def test_checksum_formats():
    h = "ab" * 32
    assert V.parse_checksum(f"{h}  dir/x.tgz\n{'cd' * 32}  y.tgz\n", "x.tgz") == ("sha256", h)
    assert V.parse_checksum(f"{h} *x.tgz\n", "x.tgz") == ("sha256", h)
    assert V.parse_checksum(f"SHA256 (x.tgz) = {h}\n", "x.tgz") == ("sha256", h)
    assert V.parse_checksum(f"{h}\n", "x.tgz", url_specific=True) == ("sha256", h)
    with pytest.raises(V.VerifyError):
        V.parse_checksum(f"{h}\n", "x.tgz")                       # a bare hash needs a per-file URL
    s512 = "0123456789ABCDEF" * 8
    apache = "kafka.tgz: " + " ".join(s512[i:i + 8] for i in range(0, 64, 8)) + "\n           " + \
        " ".join(s512[i:i + 8] for i in range(64, 128, 8)) + "\n"
    assert V.parse_checksum(apache, "kafka.tgz") == ("sha512", s512.lower())
    with pytest.raises(V.VerifyError, match="different hashes"):
        V.parse_checksum(f"{h}  x.tgz\n{'cd' * 32}  x.tgz\n", "x.tgz")
    with pytest.raises(V.VerifyError, match="does not name"):
        V.parse_checksum(f"{h}  x-0.9.tgz\n", "x.tgz")
    js = json.dumps({"0.16.0": {"x86_64-linux": {"shasum": h}}, "packages": [
        {"os": "linux", "arch": "amd64", "sha256": h}, {"os": "deb", "arch": "amd64", "sha256": "ff" * 32}]})
    assert V.parse_checksum(js, "n", "json:0.16.0/x86_64-linux/shasum") == ("sha256", h)
    assert V.parse_checksum(js, "n", "json:packages/[os=linux,arch=amd64]/sha256") == ("sha256", h)
    csv = "PRODUCT,3.54.1,2026/sqlite-tools-linux-x64-3540100.zip,4324601," + h + "\n"
    assert V.parse_checksum(csv, "sqlite-tools-linux-x64-3540100.zip", "sqlite-csv") == ("sha3-256", h)


def test_json_checksum_accepts_an_algo_prefixed_digest():
    doc = json.dumps({"assets": [{"name": "a.zip", "digest": "sha256:" + "a" * 64},
                                 {"name": "b.zip", "digest": "sha512:" + "b" * 128},
                                 {"name": "c.zip", "digest": "sha256:" + "c" * 10},
                                 {"name": "d.zip", "digest": "md5:" + "d" * 32}]})
    assert V.parse_checksum(doc, "a.zip", "json:assets/[name=a.zip]/digest") == ("sha256", "a" * 64)
    assert V.parse_checksum(doc, "b.zip", "json:assets/[name=b.zip]/digest") == ("sha512", "b" * 128)
    for bad in ("c.zip", "d.zip"):
        with pytest.raises(V.VerifyError):
            V.parse_checksum(doc, bad, f"json:assets/[name={bad}]/digest")


def test_ed25519_matches_rfc8032_vector_1():
    pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert V.ed25519_verify(pub, b"", sig)
    assert not V.ed25519_verify(pub, b"x", sig)
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    assert V.ed25519_keypair(seed)[1] == pub and V.ed25519_sign(seed, b"") == sig


def minisign_pair(tmp_path):
    seed = bytes(range(32))
    _s, pub = V.ed25519_keypair(seed)
    kid = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    pubkey = base64.b64encode(b"Ed" + kid + pub).decode()

    def sign(data: bytes) -> str:
        msg = hashlib.blake2b(data, digest_size=64).digest()
        s = V.ed25519_sign(seed, msg)
        trusted = "timestamp:1 file:x hashed"
        g = V.ed25519_sign(seed, s + trusted.encode())
        return (f"untrusted comment: test\n{base64.b64encode(b'ED' + kid + s).decode()}\n"
                f"trusted comment: {trusted}\n{base64.b64encode(g).decode()}\n")
    return pubkey, sign


def test_minisign_signature_verified_on_fetch_and_rechecked_by_verify(http, tmp_path, capsys):
    pubkey, sign = minisign_pair(tmp_path)
    data = b"zig-like tarball\n"
    http.files["/z/zig.tar.xz"] = data
    http.files["/z/zig.tar.xz.minisig"] = sign(data).encode()
    body = ENTRY.format(name="zg", cat="languages", version="1") + f'''
[[artifact]]
entry = "zg"
kind = "file"
platform = "any"
url = "{http.base}/z/zig.tar.xz"
sha256 = "{sha(data)}"
signature_url = "{http.base}/z/zig.tar.xz.minisig"
signature_kind = "minisign"
signature_key = "{pubkey}"
install = "data"
verified = "pinned"
'''
    cat = write_catalog(tmp_path, body)
    drive = tmp_path / "d"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)
    assert rc == 0, out.err
    it = json.loads((drive / "ark.lock").read_text())["items"]["zg/any/zig.tar.xz"]
    assert it["signature"]["status"] == "verified" and it["verified"] == "pinned+signature"
    # a bad signature is refused at fetch time
    http.files["/z/zig.tar.xz.minisig"] = sign(b"something else").encode()
    shutil.rmtree(drive)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)
    assert rc == 4 and "does NOT verify" in out.err
    # ...and a signature swapped on the drive afterwards is caught by verify
    http.files["/z/zig.tar.xz.minisig"] = sign(data).encode()
    assert run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)[0] == 0
    sigp = drive / "files/zg/1/any/zig.tar.xz.minisig"
    sigp.write_text(sign(b"other"))
    lock = json.loads((drive / "ark.lock").read_text())        # keep the lock self-consistent
    for f in lock["items"]["zg/any/zig.tar.xz"]["files"]:
        if f["path"].endswith(".minisig"):
            f["sha256"], f["size"] = sha(sigp.read_bytes()), sigp.stat().st_size
    (drive / "ark.lock").write_text(json.dumps(lock))
    rc, out = run(["verify", str(drive)], capsys)
    assert rc == 1 and "BAD_SIGNATURE" in out.out


def test_a_garbled_signature_fails_its_item_not_the_run(http, tmp_path, capsys):
    pubkey, sign = minisign_pair(tmp_path)
    data = b"zig-like tarball\n"
    http.files["/z/zig.tar.xz"] = data
    http.files["/z/zig.tar.xz.minisig"] = b"untrusted comment: x\nAAAAA\ntrusted comment: y\nZZ\n"
    http.files["/z/later.bin"] = b"later\n"
    body = ENTRY.format(name="zg", cat="languages", version="1") + f'''
[[artifact]]
entry = "zg"
kind = "file"
platform = "any"
url = "{http.base}/z/zig.tar.xz"
sha256 = "{sha(data)}"
signature_url = "{http.base}/z/zig.tar.xz.minisig"
signature_kind = "minisign"
signature_key = "{pubkey}"
install = "data"
verified = "pinned"

[[artifact]]
entry = "zg"
kind = "file"
platform = "any"
url = "{http.base}/z/later.bin"
sha256 = "{sha(b"later\n")}"
install = "data"
verified = "pinned"
'''
    drive = tmp_path / "d"
    rc, out = run(["--catalog", str(write_catalog(tmp_path, body)), "fetch", "--to", str(drive)], capsys)
    assert rc == 4 and "FAILED zg/any/zig.tar.xz" in out.err and "not valid base64" in out.err
    assert "Traceback" not in out.err
    items = json.loads((drive / "ark.lock").read_text())["items"]
    assert "zg/any/later.bin" in items and "zg/any/zig.tar.xz" not in items      # the run went on
    assert not list(drive.rglob("*.part"))
    # the same garbage swapped in on the drive is a BAD_SIGNATURE in verify, not a crash
    http.files["/z/zig.tar.xz.minisig"] = sign(data).encode()
    assert run(["--catalog", str(write_catalog(tmp_path, body)), "fetch", "--to", str(drive)], capsys)[0] == 0
    sigp = drive / "files/zg/1/any/zig.tar.xz.minisig"
    sigp.write_text("untrusted comment: x\nAAAAA\ntrusted comment: y\nZZ\n")
    lock = json.loads((drive / "ark.lock").read_text())
    for f in lock["items"]["zg/any/zig.tar.xz"]["files"]:
        if f["path"].endswith(".minisig"):
            f["sha256"], f["size"] = sha(sigp.read_bytes()), sigp.stat().st_size
    (drive / "ark.lock").write_text(json.dumps(lock))
    rc, out = run(["verify", str(drive)], capsys)
    assert rc == 1 and "BAD_SIGNATURE" in out.out


def test_a_registry_answering_html_fails_the_image_cleanly(registry, tmp_path, capsys):
    page = b"<html>rate limited</html>"
    dig = "sha256:" + sha(page)                          # pinned to the page itself: only the JSON check is left
    registry.blobs[dig] = page
    cat = oci_catalog(tmp_path, registry, digest=dig)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 4 and "FAILED app/oci/app" in out.err and "did not answer JSON" in out.err
    assert "Traceback" not in out.err


def test_ed25519_signed_sums_the_tensorfold_way(http, tmp_path, capsys):
    seed = bytes(range(1, 33))
    _s, pub = V.ed25519_keypair(seed)
    pem = tmp_path / "key.pem"
    pem.write_text("-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(bytes.fromhex("302a300506032b6570032100") + pub).decode()
                   + "\n-----END PUBLIC KEY-----\n")
    data = b"engine\n"
    sums = f"{sha(data)}  tf-1.0-linux-x86_64.tar.gz\n".encode()
    prefix = "tensorfold-native-checksum-v1\n"
    http.files["/tf/tf-1.0-linux-x86_64.tar.gz"] = data
    http.files["/tf/tf-1.0-linux-x86_64.tar.gz.sha256"] = sums
    http.files["/tf/tf-1.0-linux-x86_64.tar.gz.sha256.sig"] = V.ed25519_sign(seed, prefix.encode() + sums)
    body = ENTRY.format(name="tf", cat="ai", version="1.0") + f'''
[[artifact]]
entry = "tf"
kind = "file"
platform = "linux-x86_64"
url = "{http.base}/tf/tf-{{version}}-linux-x86_64.tar.gz"
checksum_url = "{http.base}/tf/tf-{{version}}-linux-x86_64.tar.gz.sha256"
signature_url = "{http.base}/tf/tf-{{version}}-linux-x86_64.tar.gz.sha256.sig"
signature_kind = "ed25519-sums"
signature_key = "{pem}"
signature_prefix = "tensorfold-native-checksum-v1\\n"
install = "data"
verified = "unverified"
'''
    cat = write_catalog(tmp_path, body)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 0, out.err
    it = json.loads((tmp_path / "d/ark.lock").read_text())["items"]["tf/linux-x86_64/tf-V-linux-x86_64.tar.gz"]
    assert it["signature"]["status"] == "verified" and "upstream-sums" in it["verified"]
    assert run(["verify", str(tmp_path / "d")], capsys)[0] == 0
    http.files["/tf/tf-1.0-linux-x86_64.tar.gz.sha256.sig"] = V.ed25519_sign(seed, sums)   # no prefix: wrong message
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d2")], capsys)
    assert rc == 4 and "does NOT verify" in out.err


# ── package sets ─────────────────────────────────────────────────────────

def demo_wheel() -> tuple:
    """A real, installable pure wheel: demo_pkg 1.0."""
    import zipfile
    name = "demo_pkg-1.0-py3-none-any.whl"
    files = {"demo_pkg/__init__.py": b"VALUE = 42\n",
             "demo_pkg-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0\n",
             "demo_pkg-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
                                             b"Tag: py3-none-any\n"}
    rec = "".join(f"{k},sha256={base64.urlsafe_b64encode(hashlib.sha256(v).digest()).rstrip(b'=').decode()},{len(v)}\n"
                  for k, v in files.items()) + "demo_pkg-1.0.dist-info/RECORD,,\n"
    files["demo_pkg-1.0.dist-info/RECORD"] = rec.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(zipfile.ZipInfo(k, (2020, 1, 1, 0, 0, 0)), v)
    return name, buf.getvalue()


def pip_catalog(tmp_path, platform="any", python=""):
    body = ENTRY.format(name="demo", cat="ai", version="1.0") + f'''
[[artifact]]
entry = "demo"
kind = "pip"
platform = "{platform}"
requirements = ["demo-pkg=={{version}}"]
{('python = "' + python + '"') if python else ''}
verified = "tofu"
'''
    return write_catalog(tmp_path, body)


def test_pip_set_fetches_from_one_explicit_index_and_restores_offline(http, tmp_path, capsys, monkeypatch):
    name, whl = demo_wheel()
    http.files[f"/simple/{name}"] = whl
    http.files["/simple/demo-pkg/"] = f'<a href="/simple/{name}#sha256={sha(whl)}">{name}</a>'.encode()
    http.ctypes["/simple/demo-pkg/"] = "text/html"
    monkeypatch.setenv("ARK_PIP_INDEX_URL", f"{http.base}/simple/")
    # ambient pip config pointing somewhere else entirely: it must not be read
    monkeypatch.setenv("PIP_INDEX_URL", "http://127.0.0.1:9/never")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "http://127.0.0.1:9/never-extra")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    cat = pip_catalog(tmp_path)
    drive = tmp_path / "d"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive), "--kinds", "pip"], capsys)
    assert rc == 0, out.err
    it = json.loads((drive / "ark.lock").read_text())["items"]["demo/pip/any"]
    assert it["source"] == f"{http.base}/simple/" and it["verified"] == "tofu"
    paths = sorted(os.path.basename(f["path"]) for f in it["files"])
    assert paths == [name, "requirements.ark.txt"]
    reqs = (drive / it["primary"] / "requirements.ark.txt").read_text()
    assert f"demo_pkg==1.0 --hash=sha256:{sha(whl)}" in reqs
    assert run(["verify", str(drive)], capsys)[0] == 0
    # offline: a venv from the drive alone, --no-index --require-hashes
    monkeypatch.setenv("ARK_FORCE_OFFLINE", "1")
    for k in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_NO_INDEX"):
        monkeypatch.delenv(k)
    rc, out = run(["restore", "demo", "--from", str(drive), "--prefix", str(tmp_path / "opt"),
                   "--python", sys.executable], capsys)
    assert rc == 0, out.err
    vpy = tmp_path / "opt/demo-1.0/venv/bin/python"
    r = subprocess.run([str(vpy), "-c", "import demo_pkg; print(demo_pkg.VALUE)"], capture_output=True, text=True)
    assert r.stdout.strip() == "42", r.stderr


def test_pip_set_for_another_platform_or_python_is_refused_unless_asked(http, tmp_path, capsys, monkeypatch):
    other = next(p for p in C.PLATFORMS if p != R.host_platform())
    drive = tmp_path / "d"
    rc, out = run(["--catalog", str(pip_catalog(tmp_path, platform=other)), "fetch", "--to", str(drive),
                   "--kinds", "pip", "--platform", other], capsys)
    assert rc == 4 and "dependency markers" in out.err and "--allow-cross-pip" in out.err
    assert not list(drive.glob("pkgs/pip/*/*/*.whl"))
    rc, out = run(["--catalog", str(pip_catalog(tmp_path, python="3.1")), "fetch", "--to", str(drive),
                   "--kinds", "pip"], capsys)
    assert rc == 4 and "the set is for 3.1" in out.err
    # asked for: fetched, and the lock says it was resolved cross-host
    name, whl = demo_wheel()
    http.files[f"/simple/{name}"] = whl
    http.files["/simple/demo-pkg/"] = f'<a href="/simple/{name}">{name}</a>'.encode()
    http.ctypes["/simple/demo-pkg/"] = "text/html"
    monkeypatch.setenv("ARK_PIP_INDEX_URL", f"{http.base}/simple/")
    rc, out = run(["--catalog", str(pip_catalog(tmp_path, platform=other)), "fetch", "--to", str(drive),
                   "--kinds", "pip", "--platform", other, "--allow-cross-pip"], capsys)
    assert rc == 0, out.err
    it = json.loads((drive / "ark.lock").read_text())["items"][f"demo/pip/{other}"]
    assert it["verified"] == "tofu+cross-host" and other in it["cross_host"]
    rc, out = run(["verify", str(drive)], capsys)
    assert rc == 0 and f"demo/pip/{other}" in out.out                 # named with the tofu items


def fake_npm(tmp_path, monkeypatch, plock: dict):
    """An npm that records its arguments and writes the given package-lock.json."""
    bindir = tmp_path / "fakenpm"
    bindir.mkdir(exist_ok=True)
    (bindir / "plock.json").write_text(json.dumps(plock))
    (bindir / "npm").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {bindir}/args\n'
                                f'env > {bindir}/env\ncp {bindir}/plock.json package-lock.json\n')
    (bindir / "npm").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return bindir


def npm_catalog(tmp_path):
    return write_catalog(tmp_path, ENTRY.format(name="lp", cat="dev", version="1.3.0") + '''
[[artifact]]
entry = "lp"
kind = "npm"
platform = "any"
packages = ["left-pad@{version}"]
verified = "tofu"
''')


def test_npm_set_uses_one_registry_and_refuses_unchecked_tarballs(http, tmp_path, capsys, monkeypatch):
    tgz = b"fake npm tarball"
    http.files["/npm/left-pad/-/left-pad-1.3.0.tgz"] = tgz
    integ = "sha512-" + base64.b64encode(hashlib.sha512(tgz).digest()).decode()
    reg = f"{http.base}/npm/"
    monkeypatch.setenv("ARK_NPM_REGISTRY", reg)
    monkeypatch.setenv("NPM_CONFIG_REGISTRY", "http://127.0.0.1:9/evil/")
    good = {"packages": {"": {}, "node_modules/left-pad": {"version": "1.3.0", "integrity": integ,
                                                           "resolved": f"{reg}left-pad/-/left-pad-1.3.0.tgz"}}}
    bindir = fake_npm(tmp_path, monkeypatch, good)
    cat = npm_catalog(tmp_path)
    drive = tmp_path / "d"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)
    assert rc == 0, out.err
    args = (bindir / "args").read_text().split("\n")
    assert f"--registry={reg}" in args and f"--userconfig={os.devnull}" in args
    assert "NPM_CONFIG_REGISTRY" not in (bindir / "env").read_text()
    it = json.loads((drive / "ark.lock").read_text())["items"]["lp/npm/any"]
    assert it["source"] == reg
    for bad, why in (({"integrity": ""}, "no integrity"),
                     ({"resolved": "http://127.0.0.1:9/evil/left-pad-1.3.0.tgz"}, "not the registry"),
                     ({"integrity": "sha512-" + base64.b64encode(b"x" * 64).decode()}, "does not match")):
        plock = json.loads(json.dumps(good))
        plock["packages"]["node_modules/left-pad"].update(bad)
        fake_npm(tmp_path, monkeypatch, plock)
        shutil.rmtree(drive)
        rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)
        assert rc == 4 and why in out.err, (why, out.err)
        assert not list(drive.rglob("*.tgz"))


# ── OCI ──────────────────────────────────────────────────────────────────

SECRET = "tok-SECRET-do-not-print-7f3a"


def toy_image():
    """An index with linux/amd64 + linux/arm64 (and an s390x child we never store)."""
    blobs, kids = {}, []

    def put(b: bytes) -> dict:
        d = "sha256:" + sha(b)
        blobs[d] = b
        return {"digest": d, "size": len(b)}
    for arch in ("amd64", "arm64", "s390x"):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as t:
            data = f"hello from {arch}\n".encode()
            ti = tarfile.TarInfo("hello.txt")
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
        layer = gzip.compress(raw.getvalue(), mtime=0)
        ld = put(layer)
        cfg = json.dumps({"architecture": arch, "os": "linux",
                          "rootfs": {"type": "layers", "diff_ids": ["sha256:" + sha(raw.getvalue())]}}).encode()
        cd = put(cfg)
        man = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                          "config": {"mediaType": "application/vnd.oci.image.config.v1+json", **cd},
                          "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", **ld}]}).encode()
        md = put(man)
        kids.append({"mediaType": "application/vnd.oci.image.manifest.v1+json", **md,
                     "platform": {"os": "linux", "architecture": arch}})
    index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                        "manifests": kids}).encode()
    idig = put(index)["digest"]
    return blobs, idig


class Registry:
    """A fake OCI registry: pull with a bearer token, blobs via a CDN redirect,
    and push (optionally refusing an index whose children are absent)."""

    def __init__(self, blobs=None, index="", token="", cdn=None, strict_index=False):
        self.blobs = dict(blobs or {})
        self.manifests = {}
        if index:
            self.manifests[("toy/app", "1.0")] = index
        self.token, self.cdn, self.strict, self.log = token, cdn, strict_index, []
        self.uploads = {}
        self.denied = 0              # 401s answered
        self.issued = 0              # tokens handed out
        self.rotate_after = 0        # >0: the token expires after this many blob GETs
        self.blob_gets = 0
        self.refuse_tag = 0          # >0: answer this status to a manifest PUT by tag

    def handler(self):
        reg = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _authed(self):
                if not reg.token or self.path.startswith("/token"):
                    return True
                if self.headers.get("Authorization") == f"Bearer {reg.token}":
                    return True
                port = self.server.server_address[1]
                reg.denied += 1
                self._send(401, b"", {"WWW-Authenticate": f'Bearer realm="http://127.0.0.1:{port}/token",service="fake",'
                                                          f'scope="repository:toy/app:pull"'})
                return False

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                reg.log.append((self.command, self.path, dict(self.headers)))
                if not self._authed():
                    return
                if self.path.startswith("/token"):
                    reg.issued += 1
                    return self._send(200, json.dumps({"token": reg.token}).encode())
                m = re.fullmatch(r"/v2/(.+)/(manifests|blobs)/([^?]+)", self.path.split("?")[0])
                if self.path.startswith("/v2/") and "/tags/list" in self.path:
                    return self._send(200, b'{"tags":[]}')
                if not m:
                    return self._send(404)
                repo, what, ref = m.groups()
                if what == "manifests":
                    dig = reg.manifests.get((repo, ref), ref)
                    body = reg.blobs.get(dig)
                    if body is None:
                        return self._send(404)
                    try:
                        mt = json.loads(body).get("mediaType", "application/vnd.oci.image.manifest.v1+json")
                    except ValueError:
                        mt = "text/html"
                    return self._send(200, body, {"Content-Type": mt, "Docker-Content-Digest": dig})
                if ref not in reg.blobs:
                    return self._send(404)
                if self.command == "GET":
                    reg.blob_gets += 1
                    if reg.rotate_after and reg.blob_gets == reg.rotate_after:
                        reg.token = reg.token + "-rotated"      # every token issued so far is now expired
                if reg.cdn and self.command == "GET":
                    return self._send(307, b"", {"Location": f"{reg.cdn.base}/cdn/{ref}"})
                return self._send(200, reg.blobs[ref])

            def do_POST(self):
                m = re.fullmatch(r"/v2/(.+)/blobs/uploads/", self.path)
                if not m:
                    return self._send(404)
                uid = str(len(reg.uploads))
                reg.uploads[uid] = b""
                return self._send(202, b"", {"Location": f"/upload/{uid}"})

            def do_PUT(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                if self.path.startswith("/upload/"):
                    dig = re.search(r"digest=(sha256:[0-9a-f]{64})", self.path).group(1)
                    if "sha256:" + sha(body) != dig:
                        return self._send(400)
                    reg.blobs[dig] = body
                    return self._send(201)
                m = re.fullmatch(r"/v2/(.+)/manifests/(.+)", self.path)
                if not m:
                    return self._send(404)
                if reg.refuse_tag and not m.group(2).startswith("sha256:"):
                    return self._send(reg.refuse_tag)
                doc = json.loads(body)
                if reg.strict and "manifests" in doc and any(d["digest"] not in reg.blobs for d in doc["manifests"]):
                    return self._send(400)
                dig = "sha256:" + sha(body)
                reg.blobs[dig] = body
                reg.manifests[(m.group(1), m.group(2))] = dig
                return self._send(201, b"", {"Docker-Content-Digest": dig})
        return H


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def registry(http):
    blobs, idig = toy_image()
    for d, b in blobs.items():
        http.files[f"/cdn/{d}"] = b                 # the CDN the registry redirects blobs to
    reg = Registry(blobs, idig, token=SECRET, cdn=http)
    srv, host = start(reg.handler())
    reg.host, reg.index = host, idig
    yield reg
    srv.shutdown()
    srv.server_close()


def oci_catalog(tmp_path, reg, digest=None) -> Path:
    body = ENTRY.format(name="app", cat="containers", version="1.0") + f'''
[[artifact]]
entry = "app"
kind = "oci"
image = "{reg.host}/toy/app"
tag = "{{version}}"
digest = "{digest or reg.index}"
platforms = ["linux/amd64", "linux/arm64"]
verified = "registry"
'''
    return write_catalog(tmp_path, body)


def test_oci_pull_by_digest_verify_push_and_load(registry, http, tmp_path, capsys, monkeypatch):
    cat = oci_catalog(tmp_path, registry)
    drive = tmp_path / "drive"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)
    assert rc == 0, out.err
    it = json.loads((drive / "ark.lock").read_text())["items"]["app/oci/app"]
    assert it["digest"] == registry.index and set(it["manifests"]) == {"linux/amd64", "linux/arm64"}
    # the token went to the registry, never to the CDN, and never to any output
    cdn_hits = [h for (m, p, h) in http.log if p.startswith("/cdn/")]
    assert cdn_hits and not any("Authorization" in h for h in cdn_hits)
    assert SECRET not in out.out + out.err and SECRET not in (drive / "ark.lock").read_text()
    arc = oci.OciArchive(str(drive / it["primary"]))
    assert arc.verify() == [] and set(arc.manifests()) == {"linux/amd64", "linux/arm64"}
    arc.close()
    rc, out = run(["verify", str(drive), "--deep"], capsys)
    assert rc == 0, out.out
    # push into a registry that insists on every index child: the reduced index is used
    target = Registry(strict_index=True)
    srv, host = start(target.handler())
    try:
        rc, out = run(["restore", "app", "--from", str(drive), "--registry", host], capsys)
        assert rc == 0 and "reduced index" in out.out, out.err
        kids = {d["digest"] for d in json.loads(target.blobs[target.manifests[("toy/app", "1.0")]])["manifests"]}
        assert kids == set(it["manifests"].values())
    finally:
        srv.shutdown()
        srv.server_close()
    # docker load gets a single-platform tar (a fake docker records it)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    rec = tmp_path / "loaded.tar"
    (fake / "docker").write_text(f'#!/bin/sh\n[ "$1" = load ] && cp "$3" {rec}\n')
    (fake / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    rc, out = run(["restore", "app", "--from", str(drive), "--docker", "--platform", "linux-aarch64"], capsys)
    assert rc == 0, out.err
    with tarfile.open(rec) as t:
        idx = json.loads(t.extractfile("index.json").read())
        dm = json.loads(t.extractfile("manifest.json").read())
    assert idx["manifests"][0]["digest"] == it["manifests"]["linux/arm64"]
    assert len(dm) == 1 and dm[0]["RepoTags"] == [f"{registry.host}/toy/app:1.0"]


def test_oci_pull_refreshes_an_expired_token_and_reuses_a_live_one(registry, tmp_path, capsys):
    registry.cdn = None                     # blobs straight from the registry, each one token-checked
    registry.rotate_after = 1               # the token expires after the first blob (Docker Hub: 300 s)
    cat = oci_catalog(tmp_path, registry)
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 0, out.err
    it = json.loads((tmp_path / "d/ark.lock").read_text())["items"]["app/oci/app"]
    assert set(it["manifests"]) == {"linux/amd64", "linux/arm64"}
    # one 401 to start, one when the token expired; none per manifest once a token is held
    assert registry.issued == 2 and registry.denied == 2, (registry.issued, registry.denied)
    assert SECRET not in out.out + out.err


def test_oci_push_streams_layers_and_fails_on_a_refused_tag(registry, tmp_path, capsys, monkeypatch):
    # a single-manifest archive: the amd64 child pulled by its own digest
    idx = json.loads(registry.blobs[registry.index])
    kid = next(d for d in idx["manifests"] if d["platform"]["architecture"] == "amd64")["digest"]
    tar = tmp_path / "one.oci.tar"
    oci.pull(f"{registry.host}/toy/app", "1.0", kid, ["linux/amd64"], str(tar), str(tmp_path / "stage"))
    arc = oci.OciArchive(str(tar))
    layers = {d["digest"] for d in json.loads(arc.blob(kid))["layers"]}
    read = []
    real_read = oci.OciArchive.read
    monkeypatch.setattr(oci.OciArchive, "read", lambda self, name: read.append(name) or real_read(self, name))
    target = Registry()
    srv, host = start(target.handler())
    try:
        res = oci.push(arc, host, "toy/app", "1.0", log=lambda s: None)
        assert res["code"] == 0 and target.manifests[("toy/app", "1.0")] == kid
        assert layers <= set(target.blobs)                                   # every layer arrived intact
        assert not [n for n in read if n.rsplit("/", 1)[-1] in {d.split(":")[1] for d in layers}]  # streamed
        target.refuse_tag = 403
        target.blobs.clear()
        with pytest.raises(oci.OciError, match="refused the tag"):
            oci.push(arc, host, "toy/app", "1.0", log=lambda s: None)
    finally:
        arc.close()
        srv.shutdown()
        srv.server_close()


def test_oci_top_up_for_a_second_platform_keeps_the_first(registry, http, tmp_path, capsys):
    cat = oci_catalog(tmp_path, registry)
    drive = tmp_path / "d"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive), "--platform", "linux-x86_64"], capsys)
    assert rc == 0, out.err
    it = json.loads((drive / "ark.lock").read_text())["items"]["app/oci/app"]
    assert set(it["manifests"]) == {"linux/amd64"}
    amd_layer = json.loads(registry.blobs[it["manifests"]["linux/amd64"]])["layers"][0]["digest"]
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive), "--platform", "linux-aarch64"], capsys)
    assert rc == 0, out.err
    it = json.loads((drive / "ark.lock").read_text())["items"]["app/oci/app"]
    assert set(it["manifests"]) == {"linux/amd64", "linux/arm64"} and it["platform"] == "linux/amd64,linux/arm64"
    arc = oci.OciArchive(str(drive / it["primary"]))
    assert set(arc.manifests()) == {"linux/amd64", "linux/arm64"} and arc.verify() == []
    arc.close()
    # the amd64 layer came from the drive's own archive, not the network, the second time
    assert len([p for (m, p, h) in http.log if p == f"/cdn/{amd_layer}"]) == 1
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive), "--platform", "linux-aarch64"], capsys)
    assert rc == 0 and "1 already present" in out.err
    assert run(["verify", str(drive), "--deep"], capsys)[0] == 0


def test_oci_refuses_a_registry_that_serves_the_wrong_manifest(registry, tmp_path, capsys):
    cat = oci_catalog(tmp_path, registry, digest="sha256:" + "e" * 64)
    registry.manifests[("toy/app", "sha256:" + "e" * 64)] = registry.index    # lies about the digest
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "d")], capsys)
    assert rc == 4 and "refused" in out.err.lower()


def test_lock_resolves_an_image_digest_and_platform_sizes(registry, tmp_path, capsys):
    cat = oci_catalog(tmp_path, registry, digest="")
    cat.write_text(cat.read_text().replace('verified = "registry"', 'verified = ""'))
    assert run(["--catalog", str(cat), "lock", "--no-git-sizes"], capsys)[0] == 0
    a = C.load(str(cat)).artifacts[0]
    assert a.digest == registry.index and a.verified == "registry"
    sizes = dict(x.split("=") for x in a.platform_sizes)
    assert set(sizes) == {"linux/amd64", "linux/arm64"} and all(int(v) > 0 for v in sizes.values())


# ── serve ────────────────────────────────────────────────────────────────

def _get(url, headers=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_serve_files_registry_pip_and_nothing_else(registry, http, tmp_path, capsys):
    drive = tmp_path / "drive"
    assert run(["--catalog", str(oci_catalog(tmp_path, registry)), "fetch", "--to", str(drive)], capsys)[0] == 0
    sub = tmp_path / "x"
    sub.mkdir()
    cat2 = toy_catalog(http, sub)
    assert run(["--catalog", str(cat2), "fetch", "--to", str(drive)], capsys)[0] == 0
    # a pip set, recorded the way fetch_pip records one
    wd = drive / "pkgs/pip/demo-1/any"
    wd.mkdir(parents=True)
    whl = wd / "demo_pkg-1.0-py3-none-any.whl"
    whl.write_bytes(b"PK fake wheel")
    lock = json.loads((drive / "ark.lock").read_text())
    rel = str(whl.relative_to(drive))
    lock["items"]["demo/pip/any"] = {"entry": "demo", "kind": "pip", "platform": "any", "version": "1",
                                     "files": [{"path": rel, "sha256": sha(whl.read_bytes()), "size": whl.stat().st_size}],
                                     "primary": str(wd.relative_to(drive)), "verified": "tofu"}
    S.Drive(str(drive)).write_lock(lock)
    (drive / "files/toy/planted").write_text("x")
    httpd = SV.serve(S.Drive(str(drive)), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        st, h, body = _get(base + "/files/toy/1.0/any/toy-1.0-linux.tar.gz", {"Range": "bytes=5-"})
        assert st == 206 and body == toy_toolchain()[5:]
        assert _get(base + "/files/toy/planted")[0] == 404                  # not in ark.lock: never served
        assert _get(base + "/.ark-stage/x")[0] == 404
        assert _get(base + "/../etc/passwd")[0] == 404
        assert _get(base + "/ark.lock")[0] == 200
        assert _get(base + "/files/toy/1.0/any/single", method="PUT")[0] == 405
        st, h, body = _get(base + "/v2/")
        assert st == 200 and h["Docker-Distribution-API-Version"] == "registry/2.0"
        st, h, idx = _get(base + "/v2/toy/app/manifests/1.0")
        assert st == 200 and h["Docker-Content-Digest"] == registry.index
        assert _get(base + f"/v2/toy/app/manifests/{registry.index}")[0] == 200
        kid = next(d for d in json.loads(idx)["manifests"] if d["platform"]["architecture"] == "arm64")
        st, h, man = _get(base + f"/v2/toy/app/manifests/{kid['digest']}")
        assert st == 200 and "sha256:" + sha(man) == kid["digest"]
        layer = json.loads(man)["layers"][0]["digest"]
        st, h, blob = _get(base + f"/v2/toy/app/blobs/{layer}")
        assert st == 200 and "sha256:" + sha(blob) == layer
        s390 = next(d for d in json.loads(idx)["manifests"] if d["platform"]["architecture"] == "s390x")
        assert _get(base + f"/v2/toy/app/manifests/{s390['digest']}")[0] == 404   # not archived
        assert json.loads(_get(base + "/v2/_catalog")[2]) == {"repositories": ["toy/app"]}
        st, h, page = _get(base + "/pypi/simple/demo-pkg/")
        assert st == 200 and f"#sha256={sha(b'PK fake wheel')}" in page.decode()
        # pull it back through ark's own client: the served registry is a real one
        stage = tmp_path / "stage"
        res = oci.pull(f"127.0.0.1:{httpd.server_address[1]}/toy/app", "1.0", registry.index, ["linux/amd64"],
                       str(tmp_path / "again.oci.tar"), str(stage))
        assert res["digest"] == registry.index
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_serve_never_follows_a_link_and_survives_a_bad_archive(registry, http, tmp_path, capsys):
    drive = tmp_path / "drive"
    assert run(["--catalog", str(oci_catalog(tmp_path, registry)), "fetch", "--to", str(drive)], capsys)[0] == 0
    sub = tmp_path / "x"
    sub.mkdir()
    assert run(["--catalog", str(toy_catalog(http, sub)), "fetch", "--to", str(drive)], capsys)[0] == 0
    lock = json.loads((drive / "ark.lock").read_text())
    # a lock-listed file swapped for a symlink to a secret outside the drive
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET-OUTSIDE-DRIVE")
    single = drive / "files/toy/1.0/any/single"
    single.unlink()
    single.symlink_to(secret)
    # a second, broken image archive, and an entry name built to inject markup
    bad = dict(lock["items"]["app/oci/app"], primary="oci/app/gone.oci.tar")
    lock["items"]["gone/oci"] = dict(bad, entry="gone")
    lock["items"]["xss"] = {"entry": "<script>alert(1)</script>", "kind": "file", "platform": "any",
                            "version": "1", "files": [], "primary": ""}
    (drive / "oci/app/trunc.oci.tar").write_bytes(os.urandom(100))
    lock["items"]["trunc/oci"] = dict(bad, entry="trunc", primary="oci/app/trunc.oci.tar")
    S.Drive(str(drive)).write_lock(lock)
    httpd = SV.serve(S.Drive(str(drive)), "127.0.0.1", 0)       # starts: the bad archives are skipped
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        st, _h, body = _get(base + "/files/toy/1.0/any/single")
        assert st == 404 and b"SECRET" not in body
        st, h, body = _get(base + "/files/toy/1.0/any/toy-1.0-linux.tar.gz", {"Range": "bytes=10-5"})
        assert st == 416 and h["Content-Range"] == f"bytes */{len(toy_toolchain())}"
        st, _h, body = _get(base + "/files/toy/1.0/any/toy-1.0-linux.tar.gz", {"Range": "bytes=2-3"})
        assert st == 206 and body == toy_toolchain()[2:4]
        st, _h, page = _get(base + "/", {"Host": "<b>h</b>"})
        assert st == 200 and b"<script>" not in page and b"&lt;script&gt;" in page and b"<b>h</b>" not in page
        assert _get(base + "/v2/toy/app/manifests/1.0")[0] == 200                # the good image still serves
    finally:
        httpd.shutdown()
        httpd.server_close()
    err = capsys.readouterr().err
    assert "not serving oci/app/gone.oci.tar" in err and "not serving oci/app/trunc.oci.tar" in err


def test_serve_keeps_connections_alive_and_closes_after_a_refused_write(http, tmp_path, capsys):
    from http.client import HTTPConnection
    drive = tmp_path / "drive"
    assert run(["--catalog", str(toy_catalog(http, tmp_path)), "fetch", "--to", str(drive)], capsys)[0] == 0
    httpd = SV.serve(S.Drive(str(drive)), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    c = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    try:
        for _ in range(3):                                   # three requests, one connection
            c.request("GET", "/files/toy/1.0/any/single")
            r = c.getresponse()
            assert r.status == 200 and r.version == 11 and r.read() == b"#!/bin/sh\necho single\n"
            assert not r.will_close
        c.request("HEAD", "/ark.lock")
        r = c.getresponse()
        r.read()
        assert r.status == 200 and not r.will_close
        c.request("POST", "/ark.lock", body=b"x" * 5000)     # an unread body: never reused
        r = c.getresponse()
        r.read()
        assert r.status == 405 and r.will_close
    finally:
        c.close()
        httpd.shutdown()
        httpd.server_close()


# ── the shell entry point ────────────────────────────────────────────────

def test_ark_sh_lint_and_usage():
    r = subprocess.run(["bash", str(ARK_SH), "lint"], capture_output=True, text=True)
    assert r.returncode == 0 and "0 problem(s)" in r.stdout, r.stdout[-2000:]
    r = subprocess.run(["bash", str(ARK_SH), "restore", "x", "--from", "/nonexistent"], capture_output=True, text=True)
    assert r.returncode == 2 and "not a beast-ark drive" in r.stderr


# ── review 2026-11-18 ────────────────────────────────────────────────────

def _tree(root: Path) -> dict:
    """Every path under root -> (sha256, mode, mtime_ns): any write shows."""
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (sha(p.read_bytes()) if p.is_file() and not p.is_symlink() else "",
                                         st.st_mode, st.st_mtime_ns)
    return out


def test_fetch_from_the_drive_itself_keeps_its_tool_and_dry_run_writes_nothing(http, tmp_path, capsys):
    cat = toy_catalog(http, tmp_path)
    drive = tmp_path / "drive"
    assert run(["--catalog", str(cat), "fetch", "--to", str(drive)], capsys)[0] == 0
    tool = drive / "tool/agents/ark/cli.py"
    before = _tree(drive)
    # the drive's own tool, filling the drive it lives on: src == dst
    argv = [sys.executable, "-I", "-B", str(tool), "--catalog", str(drive / "catalog.toml"), "fetch", "--to", str(drive)]
    r = subprocess.run(argv + ["--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0 and "nothing written" in r.stderr, r.stderr
    assert _tree(drive) == before                       # not the tool, the lock, ARK.md or the catalog
    r = subprocess.run(argv, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert tool.is_file() and (drive / "tool/agents/ark/fetch.py").is_file()
    assert {k: v[0] for k, v in _tree(drive).items() if k.startswith("tool/")} == \
        {k: v[0] for k, v in before.items() if k.startswith("tool/")}
    r = subprocess.run([str(drive / "ark"), "verify", str(drive)], capture_output=True, text=True)
    assert r.returncode == 0 and "RESULT: OK" in r.stdout, r.stderr
    # a dry run into a folder that does not exist creates nothing at all
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(tmp_path / "fresh"), "--dry-run"], capsys)
    assert rc == 0 and "would fetch toy/any/single" in out.err and not (tmp_path / "fresh").exists()


def test_install_tool_swaps_a_new_copy_in(tmp_path):
    drive = S.Drive(str(tmp_path / "d"))
    stale = tmp_path / "d/tool/agents/ark"
    stale.mkdir(parents=True)
    (stale / "stale.py").write_text("old\n")
    S.install_tool(drive)
    assert (stale / "cli.py").is_file() and not (stale / "stale.py").exists()
    assert not (tmp_path / "d/tool/agents/ark.tmp").exists() and not (tmp_path / "d/tool/agents/ark.old").exists()


def test_two_artifacts_whose_sums_files_share_a_name_keep_their_own(http, tmp_path, capsys):
    a, b = b"first file\n", b"second file\n"
    http.files["/a/one.bin"], http.files["/b/two.bin"] = a, b
    http.files["/a/SHA256SUMS"] = f"{sha(a)}  one.bin\n".encode()
    http.files["/b/SHA256SUMS"] = f"{sha(b)}  two.bin\n".encode()
    body = ENTRY.format(name="pair", cat="cli", version="1")
    for d, fn, data in (("a", "one.bin", a), ("b", "two.bin", b)):
        body += f'''
[[artifact]]
entry = "pair"
kind = "file"
platform = "any"
url = "{http.base}/{d}/{fn}"
sha256 = "{sha(data)}"
checksum_url = "{http.base}/{d}/SHA256SUMS"
install = "data"
verified = "upstream-sums"
'''
    drive = tmp_path / "d"
    rc, out = run(["--catalog", str(write_catalog(tmp_path, body)), "fetch", "--to", str(drive)], capsys)
    assert rc == 0, out.err
    items = json.loads((drive / "ark.lock").read_text())["items"]
    sums = [f["path"] for it in items.values() for f in it["files"] if f["path"].endswith("SHA256SUMS")]
    assert len(set(sums)) == 2, sums
    rc, out = run(["verify", str(drive)], capsys)
    assert rc == 0 and "RESULT: OK" in out.out, out.out


def test_a_vanished_checksum_file_falls_back_to_the_pin_with_a_warning(http, tmp_path, capsys):
    cat = toy_catalog(http, tmp_path)
    del http.files["/rel/single.sha256"]
    drive = tmp_path / "d"
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive), "--no-git"], capsys)
    assert rc == 0, out.err
    it = json.loads((drive / "ark.lock").read_text())["items"]["toy/any/single"]
    assert it["verified"] == "pinned" and "is gone (HTTP 404)" in it["warnings"][0]
    assert "WARNING toy/any/single" in out.err
    assert [f["path"] for f in it["files"]] == ["files/toy/1.0/any/single"]
    # no pin to fall back on: still refused
    cat2 = toy_catalog(http, tmp_path, tar_pin=False)
    cat2.write_text(cat2.read_text().replace('verified = ""', 'verified = "unverified"'))
    del http.files["/rel/SHA256SUMS"]
    rc, out = run(["--catalog", str(cat2), "fetch", "--to", str(tmp_path / "d2"), "--no-git"], capsys)
    assert rc == 4 and "HTTP 404" in out.err


def test_https_to_http_redirects_are_refused_except_to_loopback():
    import urllib.request
    from ark import net
    h = net._NoDowngrade()
    req = urllib.request.Request("https://example.org/sums")
    with pytest.raises(net.HTTPFailure, match="downgrades https"):
        h.redirect_request(req, None, 302, "Found", {}, "http://mirror.example.org/sums")
    assert h.redirect_request(req, None, 302, "Found", {}, "https://cdn.example.org/sums").full_url \
        == "https://cdn.example.org/sums"
    assert h.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1:8780/sums") is not None
    plain = urllib.request.Request("http://example.org/sums")
    assert h.redirect_request(plain, None, 302, "Found", {}, "http://other.example.org/x") is not None


def test_lock_refresh_never_moves_an_upstream_hash(http, tmp_path, capsys):
    tgz = toy_toolchain()
    http.files["/rel/toy-1.0-linux.tar.gz"] = tgz
    http.files["/rel/SHA512SUMS"] = f"{'e' * 128}  toy-1.0-linux.tar.gz\n".encode()   # upstream re-cut
    pinned = "sha512:" + hashlib.sha512(tgz).hexdigest()
    body = ENTRY.format(name="toy", cat="languages", version="1.0") + f'''
[[artifact]]
entry = "toy"
kind = "file"
platform = "any"
url = "{http.base}/rel/toy-{{version}}-linux.tar.gz"
upstream_hash = "{pinned}"
checksum_url = "{http.base}/rel/SHA512SUMS"
install = "tar"
verified = "upstream-sums"
'''
    cat = write_catalog(tmp_path, body)
    rc, out = run(["--catalog", str(cat), "lock", "--refresh", "--no-git-sizes"], capsys)
    assert rc == 0 and "UPSTREAM CHANGED" in out.err
    assert C.load(str(cat)).artifacts[0].upstream_hash == pinned


def test_lock_refresh_refuses_a_moved_image_unless_accepted(registry, tmp_path, capsys):
    old = "sha256:" + "1" * 64
    cat = oci_catalog(tmp_path, registry, digest=old)
    rc, out = run(["--catalog", str(cat), "lock", "--refresh", "--no-git-sizes"], capsys)
    assert rc == 0 and "UPSTREAM CHANGED" in out.err and "--accept-image-moves" in out.err
    assert C.load(str(cat)).artifacts[0].digest == old
    rc, out = run(["--catalog", str(cat), "lock", "--refresh", "--accept-image-moves", "--no-git-sizes"], capsys)
    assert rc == 0 and "MOVED" in out.err
    assert C.load(str(cat)).artifacts[0].digest == registry.index


@pytest.mark.skipif(not HAVE_GIT, reason="git not installed")
def test_lock_refresh_never_moves_a_commit(http, tmp_path, capsys):
    repo, commit = make_repo(tmp_path)
    cat = toy_catalog(http, tmp_path, repo, "0" * 40)
    rc, out = run(["--catalog", str(cat), "lock", "--refresh", "--no-git-sizes", "--no-sizes"], capsys)
    assert rc == 0 and "UPSTREAM CHANGED" in out.err
    assert C.load(str(cat)).entries["toy"].commit == "0" * 40


def test_a_package_set_is_refetched_when_its_requirements_or_python_change(tmp_path):
    cat = C.load(str(pip_catalog(tmp_path, python="3.12")))
    a = cat.artifacts[0]
    d = F.pkg_dir(a)
    rec = {"version": a.owner.version, "set_hash": F.pkg_set_hash(a), "requirements": ["demo-pkg==1.0"]}
    assert F._still_matches(rec, a, ["linux-x86_64"])
    a.requirements = ["demo-pkg==1.0", "other==2"]                 # same version, another set
    assert not F._still_matches(rec, a, ["linux-x86_64"]) and F.pkg_dir(a) != d
    a.requirements = ["demo-pkg=={version}"]
    a.python = "3.13"
    assert not F._still_matches(rec, a, ["linux-x86_64"]) and F.pkg_dir(a) != d
    # a record from before set_hash: judged by what it recorded
    a.python = "3.12"
    legacy = {"version": "1.0", "requirements": ["demo-pkg==1.0"], "python": "3.12"}
    assert F._still_matches(legacy, a, ["linux-x86_64"])
    assert not F._still_matches(dict(legacy, requirements=["demo-pkg==0.9"]), a, ["linux-x86_64"])


def test_pip_set_record_carries_its_set_hash(http, tmp_path, capsys, monkeypatch):
    name, whl = demo_wheel()
    http.files[f"/simple/{name}"] = whl
    http.files["/simple/demo-pkg/"] = f'<a href="/simple/{name}">{name}</a>'.encode()
    http.ctypes["/simple/demo-pkg/"] = "text/html"
    monkeypatch.setenv("ARK_PIP_INDEX_URL", f"{http.base}/simple/")
    drive = tmp_path / "d"
    cat = pip_catalog(tmp_path)
    assert run(["--catalog", str(cat), "fetch", "--to", str(drive), "--kinds", "pip"], capsys)[0] == 0
    it = json.loads((drive / "ark.lock").read_text())["items"]["demo/pip/any"]
    a = C.load(str(cat)).artifacts[0]
    assert it["set_hash"] == F.pkg_set_hash(a) and it["dir"] == F.pkg_dir(a) == it["primary"]
    assert it["set"]["python"] == "%d.%d" % sys.version_info[:2]
    rc, out = run(["--catalog", str(cat), "fetch", "--to", str(drive), "--kinds", "pip"], capsys)
    assert rc == 0 and "1 already present" in out.err


def test_lint_refuses_two_artifacts_on_one_drive_path(tmp_path):
    body = ENTRY.format(name="dup", cat="cli", version="1")
    for i, d in enumerate(("a", "b")):
        body += f'''
[[artifact]]
id = "dup/any/x{i}"
entry = "dup"
kind = "file"
platform = "any"
url = "https://example.org/{d}/tool.bin"
verified = "unverified"
'''
    errs = C.lint(C.load(str(write_catalog(tmp_path, body))))
    assert any("collides with dup/any/x0" in e and "files/dup/1/any/tool.bin" in e for e in errs), errs


def test_restore_never_replaces_a_users_own_bin_file_without_force(http, tmp_path, capsys):
    cat = toy_catalog(http, tmp_path)
    drive = tmp_path / "drive"
    assert run(["--catalog", str(cat), "fetch", "--to", str(drive), "--no-git"], capsys)[0] == 0
    prefix, bindir = tmp_path / "opt", tmp_path / "bin"
    bindir.mkdir()
    (bindir / "single").write_text("#!/bin/sh\necho mine\n")
    argv = ["restore", "toy", "--from", str(drive), "--prefix", str(prefix), "--bin", str(bindir)]
    rc, out = run(argv, capsys)
    assert rc == 1 and "restore did not make" in out.err and "--force" in out.err
    assert (bindir / "single").read_text() == "#!/bin/sh\necho mine\n"
    (bindir / "toy").symlink_to("/usr/bin/true")                  # a link somewhere else is the user's too
    (bindir / "single").unlink()
    rc, out = run(argv, capsys)
    assert rc == 1 and "a link to /usr/bin/true" in out.err
    rc, out = run(argv + ["--force"], capsys)
    assert rc == 0, out.err
    assert subprocess.run([str(bindir / "toy")], capture_output=True, text=True).stdout == "toy 1.0\n"
    rc, out = run(argv, capsys)                                     # its own links: replaced freely
    assert rc == 0, out.err
