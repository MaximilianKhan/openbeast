"""beast-lang library acquirer — the selection rule that must not regress.

The C draft resolver is the one piece of this with a non-obvious failure mode,
and it already bit us: WG14's index lists every PAPER, carries no titles, and
"highest N-numbered PDF" selected n3962 — a two-page note called "clarify
H.11.4 encoding conversion requirements". We would have shipped a two-page
paper labelled "the C standard". A corpus that is confidently wrong is worse
than an empty one, because an agent will cite it.

These tests are offline: head_size is replaced, so nothing here touches the
network.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))

import wg14_pick  # noqa: E402

INDEX = """
<html><body>
<a href="n3880.pdf">n3880</a>
<a href="n3886.pdf">n3886</a>
<a href="n3960.pdf">n3960</a>
<a href="n3962.pdf">n3962</a>
</body></html>
"""

# n3886 is the draft (793 pages); everything newer here is a short paper.
SIZES = {
    "n3880.pdf": 40_000,
    "n3886.pdf": 3_400_000,
    "n3960.pdf": 90_000,
    "n3962.pdf": 120_000,
}


@pytest.fixture()
def index(tmp_path):
    p = tmp_path / "wg14-index.html"
    p.write_text(INDEX)
    return str(p)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(wg14_pick, "head_size",
                        lambda url, timeout=20.0: SIZES.get(os.path.basename(url), 0))


def test_candidates_are_newest_first(index):
    assert wg14_pick.candidates(open(index).read(), 40) == [3962, 3960, 3886, 3880]


def test_the_draft_wins_over_newer_papers(index):
    url, size = wg14_pick.pick(open(index).read(), 40, 1_000_000)
    assert url.endswith("n3886.pdf"), "picked a paper over the draft"
    assert size == 3_400_000


def test_a_paper_only_index_is_refused_not_accepted(index, monkeypatch):
    """The important half. If no draft is present the answer is NOTHING, so
    the caller fails loudly, rather than the largest paper on offer."""
    monkeypatch.setattr(wg14_pick, "head_size",
                        lambda url, timeout=20.0: 120_000)
    assert wg14_pick.pick(open(index).read(), 40, 1_000_000) is None


def test_one_unreachable_candidate_does_not_abort_the_sweep(index, monkeypatch):
    """pick() walks every candidate, so a single dead URL must not lose the
    draft that comes after it. head_size absorbs errors by contract (returns
    0), and this proves pick() still finds n3886 when a newer one fails."""
    def flaky(url, timeout=20.0):
        if url.endswith("n3962.pdf"):
            return 0                     # what head_size does on a failure
        return SIZES.get(os.path.basename(url), 0)
    monkeypatch.setattr(wg14_pick, "head_size", flaky)
    url, size = wg14_pick.pick(open(index).read(), 40, 1_000_000)
    assert url.endswith("n3886.pdf") and size == 3_400_000


def test_head_size_returns_zero_instead_of_raising(monkeypatch):
    """The contract the test above depends on: one unreachable URL is a 0,
    not an exception that ends the sweep."""
    import urllib.request

    def explode(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(urllib.request, "urlopen", explode)
    assert wg14_pick.head_size("https://example.invalid/n1.pdf") == 0


# --- scripts/lang-library.sh, driven through a STUB curl --------------------
# Review 2026-09-17. Nothing below touches the network: `curl` is a script
# placed first on PATH that RECORDS every call and serves files out of a
# directory the test fills. The record is half of each assertion — "refused"
# means zero calls, not merely a refusal message.
import hashlib      # noqa: E402
import json         # noqa: E402
import shutil       # noqa: E402
import subprocess   # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "lang-library.sh")

STUB_CURL = r"""#!/bin/bash
echo "$*" >> "$CURL_LOG"
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -A|--retry|--retry-delay|--max-time) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
name="$(basename "$url")"
if [ ! -f "$CURL_ROOT/$name" ]; then
  # CURL_PARTIAL: a transfer that died midway — bytes on disk, then failure
  [ -n "${CURL_PARTIAL:-}" ] && printf 'half a tarb' > "$out"
  exit 22                                      # what `curl -f` does on a 404
fi
cp "$CURL_ROOT/$name" "$out"
"""


class Rig:
    def __init__(self, tmp_path):
        self.bin = tmp_path / "bin"
        self.served = tmp_path / "served"
        self.lib = tmp_path / "library"
        self.log = tmp_path / "curl.log"
        self.bin.mkdir()
        self.served.mkdir()
        stub = self.bin / "curl"
        stub.write_text(STUB_CURL)
        stub.chmod(0o755)

    def serve(self, name, data: bytes):
        (self.served / name).write_bytes(data)

    def curl_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def run(self, *args, env=None, path=None):
        e = dict(os.environ, OPENBEAST_LANG_DIR=str(self.lib),
                 CURL_LOG=str(self.log), CURL_ROOT=str(self.served),
                 OPENBEAST_OFFLINE="false",
                 PATH=path or f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        e.update(env or {})
        return subprocess.run(["bash", SCRIPT, *args], env=e, capture_output=True,
                              text=True, timeout=120)


@pytest.fixture()
def rig(tmp_path):
    return Rig(tmp_path)


def _zig_index(rig, tarballs: dict, lie_about=None):
    """A release index for `tarballs` {version: bytes}, with TRUE shasums
    unless `lie_about` names a version."""
    idx = {"master": {"x86_64-linux": {"tarball": "https://z.invalid/master.tar.xz"}}}
    for ver, data in tarballs.items():
        name = f"zig-x86_64-linux-{ver}.tar.xz"
        sha = hashlib.sha256(data).hexdigest()
        if ver == lie_about:
            sha = hashlib.sha256(b"something else entirely").hexdigest()
        idx[ver] = {"x86_64-linux": {"tarball": f"https://z.invalid/{name}",
                                     "shasum": sha}}
    rig.serve("index.json", json.dumps(idx).encode())


def test_acquire_exits_nonzero_when_every_zig_download_fails(rig):
    """Reproduced by the reviewer: two "fetch failed" warnings, "library is
    empty", exit 0. The loop ran in a pipeline subshell and its last command
    on failure was `warn`."""
    _zig_index(rig, {"0.15.1": b"a", "0.16.0": b"b"})       # index only: tarballs 404
    p = rig.run("acquire", "zig")
    assert p.stderr.count("fetch failed") == 2, p.stderr
    assert p.returncode != 0, "every download failed and acquire reported success"
    assert "incomplete: zig" in p.stderr and "rerun to resume" in p.stderr
    assert len(rig.curl_calls()) == 3, "the case was not built: " + str(rig.curl_calls())


def test_acquire_exits_zero_when_every_zig_download_succeeds(rig):
    """The negative control — and the shasum check's happy path."""
    balls = {"0.15.1": b"zig fifteen", "0.16.0": b"zig sixteen"}
    _zig_index(rig, balls)
    for ver, data in balls.items():
        rig.serve(f"zig-x86_64-linux-{ver}.tar.xz", data)
    p = rig.run("acquire", "zig")
    assert p.returncode == 0, p.stderr
    assert p.stdout.count("sha256 matches the release index") == 2, p.stdout
    for ver in balls:
        man = json.load(open(rig.lib / "zig" / ver / "manifest.json"))
        assert f"zig-x86_64-linux-{ver}.tar.xz" in man["artifacts"]
    # resumable: a second run fetches the index again and NOTHING else
    before = len(rig.curl_calls())
    assert rig.run("acquire", "zig").returncode == 0
    assert len(rig.curl_calls()) == before + 1


def test_a_tarball_that_does_not_match_the_index_shasum_is_deleted(rig):
    """The index publishes a shasum per tarball and it was never compared:
    every tarball was trust-on-first-use, then blessed with our own manifest."""
    balls = {"0.15.1": b"honest bytes", "0.16.0": b"tampered bytes"}
    _zig_index(rig, balls, lie_about="0.16.0")
    for ver, data in balls.items():
        rig.serve(f"zig-x86_64-linux-{ver}.tar.xz", data)
    p = rig.run("acquire", "zig")
    assert p.returncode != 0, "a checksum mismatch was reported as success"
    assert "sha256 MISMATCH" in p.stderr and "incomplete: zig" in p.stderr
    bad = rig.lib / "zig" / "0.16.0"
    assert not (bad / "zig-x86_64-linux-0.16.0.tar.xz").exists(), "the bad file was kept"
    assert not (bad / "manifest.json").exists(), "the bad file was given a manifest"
    # negative control, same run: the honest tarball was kept and recorded
    good = rig.lib / "zig" / "0.15.1"
    assert (good / "zig-x86_64-linux-0.15.1.tar.xz").read_bytes() == b"honest bytes"
    assert (good / "manifest.json").exists()


def test_swift_failures_reach_the_exit_code_too(rig):
    rig.serve("latest", json.dumps({"tag_name": "v1", "tarball_url":
                                    "https://api.invalid/tarball/v1"}).encode())
    p = rig.run("acquire", "swift")                          # tarball "v1" is a 404
    assert p.returncode != 0 and "incomplete: swift" in p.stderr, p.stderr
    rig.serve("v1", b"a tarball")                            # negative control
    p = rig.run("acquire", "swift")
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("how", ["env", "conf", "conf-with-comment"])
def test_acquire_refuses_offline_and_never_calls_curl(rig, tmp_path, how):
    """Every other network script refuses under OFFLINE; this one tried seven
    hosts. The conf cases run a COPY of the script inside a fake repo, because
    the key is read from <repo>/openbeast.conf and the real one is not ours to
    edit — and they prove conf.sh was not sourced: the conf file is unchanged
    afterwards (sourcing it appends a SearXNG secret)."""
    _zig_index(rig, {"0.16.0": b"x"})
    env = {}
    script = SCRIPT
    conf = None
    if how == "env":
        env["OPENBEAST_OFFLINE"] = "TRUE"
    else:
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, repo / "scripts" / "lang-library.sh")
        conf = repo / "openbeast.conf"
        conf.write_text("LANG_PACKS=auto\n" + (
            'OFFLINE="yes"\n' if how == "conf" else "OFFLINE=true   # air-gapped rig\n"))
        script = str(repo / "scripts" / "lang-library.sh")
        env["OPENBEAST_OFFLINE"] = ""                        # unset-equivalent
    e = dict(os.environ, OPENBEAST_LANG_DIR=str(rig.lib), CURL_LOG=str(rig.log),
             CURL_ROOT=str(rig.served),
             PATH=f"{rig.bin}{os.pathsep}{os.environ['PATH']}", **env)
    before = conf.read_text() if conf else None
    p = subprocess.run(["bash", script, "acquire", "zig"], env=e,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode != 0 and "OFFLINE" in p.stderr, (p.returncode, p.stderr)
    assert rig.curl_calls() == [], f"curl was called while offline: {rig.curl_calls()}"
    assert not rig.lib.exists(), "acquire created the library before refusing"
    if conf:
        assert conf.read_text() == before, "reading OFFLINE modified openbeast.conf"
    # negative control: the same invocation with offline OFF does go to curl
    e["OPENBEAST_OFFLINE"] = "false"
    subprocess.run(["bash", script, "acquire", "zig"], env=e, capture_output=True,
                   text=True, timeout=60)
    assert rig.curl_calls(), "offline=false did not reach curl — the control is dead"


def test_the_offline_safe_subcommands_work_without_curl(rig, tmp_path):
    """`command -v curl || die` sat at the top of the script, so pack/verify/
    check/list/where all died on a box without curl."""
    nocurl = tmp_path / "nocurl"
    nocurl.mkdir()
    for tool in ("bash", "python3", "sed", "dirname", "basename", "find", "sort",
                 "grep", "tail", "tr", "cat", "mkdir", "sha256sum", "stat", "cut"):
        real = shutil.which(tool)
        if real:
            os.symlink(real, nocurl / tool)
    assert not (nocurl / "curl").exists()
    p = rig.run("where", path=str(nocurl))
    assert p.returncode == 0 and p.stdout.strip() == str(rig.lib), (p.stdout, p.stderr)
    p = rig.run("list", path=str(nocurl))
    assert p.returncode == 0 and "no library yet" in p.stdout, (p.stdout, p.stderr)
    # negative control: acquire DOES still need it, and says so
    p = rig.run("acquire", "zig", path=str(nocurl))
    assert p.returncode != 0 and "curl is required" in p.stderr


def test_a_failed_fetch_leaves_no_part_file_behind(rig):
    """fetch() downloads to <name>.part and renames on success. On failure the
    .part stayed — and nothing here passes `curl -C -`, so it could never be
    resumed, only mistaken for something."""
    _zig_index(rig, {"0.16.0": b"whole tarball"})
    p = rig.run("acquire", "zig", env={"CURL_PARTIAL": "1"})
    assert p.returncode != 0 and "fetch failed" in p.stderr
    left = [str(f.relative_to(rig.lib)) for f in rig.lib.rglob("*.part")]
    assert left == [], f"partial downloads left behind: {left}"
    # negative control: the stub really did write one (the index fetch is the
    # only call that succeeded, so exactly one tarball attempt was made)
    assert any("zig-x86_64-linux-0.16.0.tar.xz.part" in c for c in rig.curl_calls())
    # and a later successful run is unaffected
    rig.serve("zig-x86_64-linux-0.16.0.tar.xz", b"whole tarball")
    assert rig.run("acquire", "zig").returncode == 0
