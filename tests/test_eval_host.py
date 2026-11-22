"""The eval harness host provisions its own toolchains (scripts/eval-host.sh,
evals/eval_host.py, evals/toolchains.lock).

Why: on 2026-11-16 the v5 harness ran from a venv and toolchains hand-placed
in a session scratch dir; something rewrote the venv's python symlink and 22
units died at interpreter startup, scored as model FAILs (docs/HARNESS_NOTES.md).

Everything here runs on fakes: fake release archives (shell scripts that
print the pinned version) behind file:// URLs, a fake lock with their real
sha256s, a temp prefix, and a PATH holding only those fakes plus the few
utilities the script needs. Nothing is downloaded and nothing outside
tmp_path is written. The script runs under /bin/bash, which is bash 3.2 on
macOS.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "eval-host.sh"
LOCK = ROOT / "evals" / "toolchains.lock"
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")
PLAT = "linux-x86_64"            # forced: the fakes are scripts, any pinned platform works
UTILS = ("bash", "awk", "sed", "find", "mkdir", "mv", "ln", "rm", "cp", "cat", "uname", "dirname",
         "basename", "readlink", "tr", "wc", "head", "sort", "env", "mktemp", "tar", "gzip",
         "curl", "shasum", "sha256sum", "perl", "chmod", "sleep", "sh", "grep", "xz")


# --- fakes ----------------------------------------------------------------------------

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _script_member(tf: tarfile.TarFile, name: str, body: str) -> None:
    data = body.encode()
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    ti.mode = 0o755
    tf.addfile(ti, io.BytesIO(data))


def _tgz(path: Path, members: dict[str, str], links: dict[str, str] | None = None) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, body in members.items():
            _script_member(tf, name, body)
        for name, target in (links or {}).items():
            ti = tarfile.TarInfo(name)
            ti.type = tarfile.SYMTYPE
            ti.linkname = target
            tf.addfile(ti)
    return path


class Host:
    """A sandbox: fake releases, a lock naming them, a prefix, a minimal PATH."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.rel = tmp / "releases"
        self.rel.mkdir()
        self.prefix = tmp / "prefix"
        self.fakebin = tmp / "fakebin"
        self.fakebin.mkdir()
        self.sysbin = tmp / "sysbin"
        self.sysbin.mkdir()
        for u in UTILS:
            w = shutil.which(u)
            if w:
                os.symlink(w, self.sysbin / u)
        self.home = tmp / "home"
        self.home.mkdir()
        self.rows: dict[str, tuple] = {}
        self.directives = {"@python-lock": str(tmp / "py.lock"),
                           "@python-imports": "json",
                           "@system": "gcc g++ git"}
        (tmp / "py.lock").write_text("fake==1.0\n")
        for t in ("gcc", "g++", "git"):
            self.fake_tool(t, f"echo '{t} (fake) 1.0'")
        self.add("zig", "0.16.0", "zig-x86_64-linux-0.16.0.tar.gz", ark="zig",
                 members={"zig-x86_64-linux-0.16.0/zig": "#!/bin/sh\necho 0.16.0\n"})
        self.add("go", "1.23.3", "go1.23.3.linux-amd64.tar.gz", ark="go",
                 members={"go/bin/go": '#!/bin/sh\n[ "$1" = version ] && '
                                       'echo "go version go1.23.3 linux/amd64"\n',
                          "go/bin/gofmt": "#!/bin/sh\nexit 0\n"})
        self.add("rust", "1.100.0", "rust-1.100.0-x86_64-unknown-linux-gnu.tar.gz", ark="rust",
                 members={"rust-1.100.0-x86_64-unknown-linux-gnu/install.sh": (
                     '#!/bin/sh\nfor a in "$@"; do case "$a" in --prefix=*) p="${a#--prefix=}";; '
                     'esac; done\nmkdir -p "$p/bin"\n'
                     'printf \'#!/bin/sh\\necho "rustc 1.100.0 (standalone fake)"\\n\' > "$p/bin/rustc"\n'
                     'printf \'#!/bin/sh\\necho cargo\\n\' > "$p/bin/cargo"\n'
                     'chmod +x "$p/bin/rustc" "$p/bin/cargo"\n')})
        init = self.rel / "rustup-init"
        init.write_text(
            '#!/bin/sh\ntc=""\nwhile [ $# -gt 0 ]; do case "$1" in --default-toolchain) '
            'tc="$2"; shift 2;; *) shift;; esac; done\n'
            'mkdir -p "$CARGO_HOME/bin" "$RUSTUP_HOME/toolchains/$tc"\n'
            'printf \'#!/bin/sh\\necho "rustc %s (rustup fake)"\\n\' "$tc" > "$CARGO_HOME/bin/rustc"\n'
            'chmod +x "$CARGO_HOME/bin/rustc"\necho "$tc" >> "$RUSTUP_HOME/init.log"\n')
        init.chmod(0o755)
        self.rows["rustup-init"] = ("1.29.3", _sha(init), "-", f"file://{init}")

    def add(self, name, ver, fname, ark="-", members=None, links=None):
        p = _tgz(self.rel / fname, members or {}, links)
        self.rows[name] = (ver, _sha(p), ark, f"file://{p}")
        return p

    def fake_tool(self, name, body, where=None):
        f = (where or self.fakebin) / name
        f.write_text(f"#!/bin/sh\n{body}\n")
        f.chmod(0o755)
        return f

    def write_lock(self) -> Path:
        lines = ["# fake lock"]
        for name, (ver, sha, ark, url) in self.rows.items():
            lines.append(f"{name} {PLAT} {ver} {sha} {ark} {url}")
        lines += [f"{k} {v}" for k, v in self.directives.items()]
        lock = self.tmp / "toolchains.lock"
        lock.write_text("\n".join(lines) + "\n")
        return lock

    def env(self, **extra) -> dict:
        env = {"PATH": f"{self.fakebin}:{self.sysbin}", "HOME": str(self.home),
               "OPENBEAST_EVAL_LOCK": str(self.write_lock()),
               "OPENBEAST_EVAL_PREFIX": str(self.prefix),
               "OPENBEAST_EVAL_PLATFORM": PLAT,
               "OPENBEAST_EVAL_PYDEPS": str(self.fake_pydeps()),
               "TMPDIR": str(self.tmp)}
        env.update(extra)
        return env

    def fake_pydeps(self) -> Path:
        f = self.tmp / "pydeps.sh"
        if not f.exists():
            f.write_text(f'#!/bin/sh\necho "$OPENBEAST_PYTHON $*" >> "{self.tmp}/pydeps.log"\n')
            f.chmod(0o755)
        return f

    def run(self, *args, **extra) -> subprocess.CompletedProcess:
        return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True,
                              env=self.env(**extra), timeout=300)


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def _states(out: str) -> dict:
    return {p[1]: p[0] for p in (ln.split(None, 2) for ln in out.splitlines())
            if len(p) >= 2 and p[0] in ("ok", "missing", "wrong", "system", "blocked")}


# --- check -------------------------------------------------------------------------------

def test_check_reports_missing_per_tool_and_exits_nonzero(host):
    r = host.run("check", "--only", "zig,go,rust,system")
    assert r.returncode == 1, r.stdout + r.stderr
    st = _states(r.stdout)
    assert st == {"zig": "missing", "go": "missing", "rust": "missing",
                  "gcc": "ok", "g++": "ok", "git": "ok"}, r.stdout
    assert "3 problem(s)" in r.stdout
    assert not host.prefix.exists(), "check must never write"


def test_check_reports_a_wrong_version(host):
    host.fake_tool("zig", "echo 0.15.1")
    r = host.run("check", "--only", "zig")
    assert r.returncode == 1
    assert _states(r.stdout) == {"zig": "wrong"}
    assert "0.15.1, want 0.16.0" in r.stdout


def test_missing_system_tools_are_reported_never_installed(host):
    (host.fakebin / "gcc").unlink()
    r = host.run("setup", "--only", "system")
    assert r.returncode == 1
    assert _states(r.stdout)["gcc"] == "system"
    assert "setup never installs system tools" in r.stdout
    assert re.search(r"xcode-select --install|apt install|dnf install|package manager", r.stdout)
    assert not (host.prefix / "bin").exists()


# --- setup (online, fake downloads) -------------------------------------------------------

def test_setup_installs_verified_downloads_and_is_idempotent(host):
    r = host.run("setup", "--only", "zig,go,rust")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _states(r.stdout) == {"zig": "ok", "go": "ok", "rust": "ok"}, r.stdout
    assert os.readlink(host.prefix / "bin" / "zig") == "../zig-0.16.0/zig"
    assert (host.prefix / "go-1.23.3" / "bin" / "go").is_file()
    assert (host.prefix / "cargo" / "bin" / "rustc").is_file()          # rustup, online
    assert (host.prefix / "rustup" / "init.log").read_text().split() == ["1.100.0"]
    # Again: nothing to do, nothing fetched. The URLs now point nowhere, so
    # any download attempt would fail the run.
    for k, (ver, sha, ark, _url) in list(host.rows.items()):
        host.rows[k] = (ver, sha, ark, f"file://{host.tmp}/gone/{k}")
    zig_dir_ino = (host.prefix / "zig-0.16.0").stat().st_ino
    r2 = host.run("setup", "--only", "zig,go,rust")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert r2.stderr.count("ok, nothing to do") == 3, r2.stderr
    assert "downloading" not in r2.stderr
    assert (host.prefix / "zig-0.16.0").stat().st_ino == zig_dir_ino
    assert (host.prefix / "rustup" / "init.log").read_text().split() == ["1.100.0"]


def test_setup_replaces_a_wrong_version_on_path(host):
    host.fake_tool("zig", "echo 0.15.1")
    r = host.run("setup", "--only", "zig")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "zig: wrong" in r.stderr
    assert re.search(r"^ok\s+zig\s+0\.16\.0 \(.*/prefix/bin/zig\)", r.stdout, re.M), r.stdout


def test_sha256_mismatch_refuses_and_installs_nothing(host):
    ver, _sha_ok, ark, url = host.rows["zig"]
    host.rows["zig"] = (ver, "0" * 64, ark, url)
    r = host.run("setup", "--only", "zig")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "SHA256 MISMATCH for zig 0.16.0" in r.stderr
    assert not (host.prefix / "zig-0.16.0").exists()
    assert not (host.prefix / "bin" / "zig").exists()
    assert list((host.prefix / ".downloads").iterdir()) == [], "the bad file must be deleted"


def test_mirrors_are_tried_before_the_url_and_still_hash_checked(host):
    good = host.tmp / "mirror-good"
    good.mkdir()
    ver, sha, ark, url = host.rows["zig"]
    shutil.copy(url[len("file://"):], good)
    host.rows["zig"] = (ver, sha, ark, f"file://{host.tmp}/gone/{url.rsplit('/', 1)[1]}")
    host.directives["@mirrors"] = f"zig file://{host.tmp}/mirror-dead file://{good}"
    r = host.run("setup", "--only", "zig")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"failed: file://{host.tmp}/mirror-dead/" in r.stderr
    assert f"downloading zig 0.16.0: file://{good}/" in r.stderr
    assert "gone" not in r.stderr                     # never reached the row's url
    # A mirror serving other bytes is a mismatch, not a reason to try the next.
    shutil.rmtree(host.prefix)
    (good / url.rsplit("/", 1)[1]).write_bytes(b"tampered")
    r = host.run("setup", "--only", "zig")
    assert r.returncode == 3 and "SHA256 MISMATCH" in r.stderr


def test_a_corrupted_cached_download_is_fetched_again(host):
    assert host.run("setup", "--only", "zig").returncode == 0
    cached = host.prefix / ".downloads" / "zig-x86_64-linux-0.16.0.tar.gz"
    cached.write_bytes(b"rot")
    shutil.rmtree(host.prefix / "zig-0.16.0")
    r = host.run("setup", "--only", "zig")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "downloading zig" in r.stderr
    assert _sha(cached) == host.rows["zig"][1]


# --- setup --from (offline) ---------------------------------------------------------------

def test_offline_setup_from_an_ark_drive_and_a_flat_mirror(host):
    drive = host.tmp / "ARK"
    for name in ("zig", "rust"):          # ark layout: files/<entry>/<version>/<platform>/<file>
        ver, _sha_, ark, url = host.rows[name]
        dest = drive / "files" / ark / ver / PLAT
        dest.mkdir(parents=True)
        shutil.copy(url[len("file://"):], dest)
    shutil.copy(host.rows["go"][3][len("file://"):], drive)   # flat mirror: <dir>/<file>
    # No network: the URLs point nowhere, and a curl that runs at all fails the test.
    for k, (ver, sha, ark, url) in list(host.rows.items()):     # same file names
        host.rows[k] = (ver, sha, ark, f"https://example.invalid/{k}/{url.rsplit('/', 1)[1]}")
    host.fake_tool("curl", f'echo "curl $*" >> "{host.tmp}/curl.log"; exit 7')
    r = host.run("setup", "--only", "zig,go,rust", "--from-ark", str(drive))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _states(r.stdout) == {"zig": "ok", "go": "ok", "rust": "ok"}, r.stdout
    assert not (host.tmp / "curl.log").exists()
    # Offline rust is the standalone installer, linked into bin/, not rustup.
    assert os.readlink(host.prefix / "bin" / "rustc") == "../rust-1.100.0/bin/rustc"
    assert not (host.prefix / "rustup").exists()


def test_offline_setup_names_what_is_missing_and_how_to_get_it(host):
    empty = host.tmp / "empty"
    empty.mkdir()
    r = host.run("setup", "--only", "go", "--from", str(empty))
    assert r.returncode == 1
    assert "go 1.23.3 (go1.23.3.linux-amd64.tar.gz) is not on" in r.stderr
    assert "eval-host.sh fetch --to" in r.stderr


def test_fetch_builds_a_mirror_that_setup_installs_from(host):
    mirror = host.tmp / "mirror"
    r = host.run("fetch", "--to", str(mirror), "--platform", PLAT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (mirror / "zig-x86_64-linux-0.16.0.tar.gz").is_file()
    assert (mirror / "rustup-init").is_file()
    r = host.run("setup", "--only", "zig,go,rust", "--from", str(mirror))
    assert r.returncode == 0, r.stdout + r.stderr


# --- env ------------------------------------------------------------------------------------

def test_env_prints_the_exports_and_json_agrees(host):
    assert host.run("setup", "--only", "zig,rust").returncode == 0
    venv_py = host.prefix / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/bin/sh\n")
    venv_py.chmod(0o755)
    r = host.run("env")
    assert r.returncode == 0, r.stderr
    p = str(host.prefix)
    assert f"export PATH='{p}/bin:{p}/cargo/bin':\"$PATH\"" in r.stdout
    assert f"export RUSTUP_HOME='{p}/rustup' CARGO_HOME='{p}/cargo'" in r.stdout
    assert f"export OPENBEAST_EVAL_PY='{p}/venv/bin/python'" in r.stdout
    # It evaluates: the prefix's zig is what a shell then runs.
    sh = subprocess.run([BASH, "-c", f'eval "$({BASH} {SCRIPT} env)"; zig version; '
                                     'echo "$RUSTUP_HOME"; echo "$OPENBEAST_EVAL_PY"'],
                        capture_output=True, text=True, env=host.env(), timeout=60)
    assert sh.stdout.split() == ["0.16.0", f"{p}/rustup", f"{p}/venv/bin/python"], sh.stderr
    j = json.loads(host.run("env", "--json").stdout)
    assert j["path_prepend"] == [f"{p}/bin", f"{p}/cargo/bin"]
    assert j["env"] == {"OPENBEAST_EVAL_PREFIX": p, "RUSTUP_HOME": f"{p}/rustup",
                        "CARGO_HOME": f"{p}/cargo", "OPENBEAST_EVAL_PY": f"{p}/venv/bin/python"}


def test_env_on_an_empty_prefix_exports_nothing_it_does_not_own(host):
    r = host.run("env")
    assert r.returncode == 0
    assert "PATH" not in r.stdout and "RUSTUP_HOME" not in r.stdout
    assert json.loads(host.run("env", "--json").stdout)["path_prepend"] == []


# --- the eval venv ---------------------------------------------------------------------------

def _py_links() -> dict:
    """A fake python-build-standalone tree: bin/python3 and bin/python3.X are
    links to this interpreter's real binary, so the fake cpython row can pin
    this python's version and `python3 -m venv` works from it.

    lib/ links to the real stdlib too. A venv's python finds its stdlib from
    pyvenv.cfg's home (this tree's bin/) via ../lib/python3.X; only a macOS
    framework build (python.org, Homebrew, anaconda) finds it through its
    dylib instead. Without lib/, a python-build-standalone base (what uv runs
    the tests on) made a venv that died with "Could not find platform
    independent libraries", and the setup tests failed for that reason, not
    the script's."""
    real = os.path.realpath(sys.executable)
    mm = "%d.%d" % sys.version_info[:2]
    return {"python/bin/python3": real, f"python/bin/python{mm}": real,
            "python/lib": os.path.join(os.path.realpath(sys.base_prefix), "lib")}


def test_python_setup_builds_the_venv_on_the_prefix_cpython(host):
    ver = _platform.python_version()
    host.add("cpython", ver, f"cpython-{ver}-fake-install_only.tar.gz", ark="cpython-3.12",
             links=_py_links())
    r = host.run("setup", "--only", "python")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _states(r.stdout) == {"python": "ok"}, r.stdout
    vpy = host.prefix / "venv" / "bin" / "python"
    # Its base is the prefix's cpython. (A real python-build-standalone tree
    # records home = <prefix>/cpython-<v>/bin; this fake links to a system
    # python, which a macOS framework build reports by its own path, so the
    # check compares the resolved binaries, as here.)
    cfg = (host.prefix / "venv" / "pyvenv.cfg").read_text()
    home = re.search(r"^home = (.*)$", cfg, re.M).group(1)
    mm = "%d.%d" % sys.version_info[:2]
    assert os.path.realpath(f"{home}/python{mm}") == \
        os.path.realpath(host.prefix / f"cpython-{ver}" / "bin" / f"python{mm}"), cfg
    log = (host.tmp / "pydeps.log").read_text()
    assert log.strip() == f"{vpy} install"
    assert (host.prefix / "venv" / ".openbeast-lock-sha256").read_text().strip() == \
        _sha(host.tmp / "py.lock")
    # A lock change is "wrong" until setup reinstalls from it.
    (host.tmp / "py.lock").write_text("fake==2.0\n")
    r = host.run("check", "--only", "python")
    assert r.returncode == 1 and "not built from the current" in r.stdout
    assert host.run("setup", "--only", "python").returncode == 0
    assert (host.tmp / "pydeps.log").read_text().count("install") == 2


def test_python_check_catches_the_2026_11_16_broken_interpreter(host):
    ver = _platform.python_version()
    host.add("cpython", ver, "cpython-fake.tar.gz", ark="cpython-3.12",
             links=_py_links())
    vpy = host.prefix / "venv" / "bin" / "python"
    vpy.parent.mkdir(parents=True)
    vpy.write_text("#!/bin/sh\necho 'Could not find platform independent libraries <prefix>' >&2\n"
                   "echo 'Fatal Python error: Failed to import encodings module' >&2\nexit 1\n")
    vpy.chmod(0o755)
    r = host.run("check", "--only", "python")
    assert r.returncode == 1
    assert _states(r.stdout) == {"python": "wrong"}
    assert "does not start: Could not find platform independent libraries" in r.stdout
    # setup rebuilds it from the prefix's own cpython
    r = host.run("setup", "--only", "python")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "building the eval venv" in r.stderr


def test_python_offline_needs_a_wheelhouse(host):
    ver = _platform.python_version()
    p = host.add("cpython", ver, "cpython-fake.tar.gz", ark="cpython-3.12",
                 links=_py_links())
    mirror = host.tmp / "mirror"
    mirror.mkdir()
    shutil.copy(p, mirror)
    r = host.run("setup", "--only", "python", "--from", str(mirror))
    assert r.returncode == 1
    assert "give --wheelhouse DIR" in r.stderr
    wh = mirror / f"wheels-{PLAT}"
    wh.mkdir()
    r = host.run("setup", "--only", "python", "--from", str(mirror))
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"install --from {wh}" in (host.tmp / "pydeps.log").read_text()


# --- the lock and the script themselves ----------------------------------------------------

def _lock_rows():
    rows = {}
    for line in LOCK.read_text().splitlines():
        if line.startswith(("#", "@")) or not line.strip():
            continue
        name, plat, ver, sha, ark, url = line.split()
        rows[(name, plat)] = (ver, sha, ark, url)
    return rows


def test_lock_reuses_the_ark_catalogs_pins():
    from ark import catalog
    cat = catalog.load()
    entries = cat.entries
    rows = _lock_rows()
    shared = 0
    for (name, plat), (ver, sha, ark, url) in rows.items():
        assert re.fullmatch(r"[0-9a-f]{64}", sha), (name, plat)
        assert url.startswith("https://"), (name, plat)
        assert url.rsplit("/", 1)[-1], (name, plat)
        if ark == "-" or ark not in entries or entries[ark].version != ver:
            continue                    # go 1.23.3, rustup-init: upstream sums
        arts = [a for a in cat.artifacts
                if a.entry == ark and a.platform == plat and a.kind == "file"]
        assert len(arts) == 1, (ark, plat)
        assert (arts[0].sha256, arts[0].url_x) == (sha, url), (name, plat)
        shared += 1
    assert shared == 9                  # zig, rust, cpython x 3 platforms
    for plat in ("darwin-arm64", "linux-aarch64", "linux-x86_64"):
        for name in ("zig", "go", "rust", "rustup-init", "cpython"):
            assert (name, plat) in rows, (name, plat)


def test_lock_versions_are_the_proven_ones():
    import profile as P
    rows = _lock_rows()
    assert {v[0] for (n, _), v in rows.items() if n == "zig"} == {P.ZIG_PIN}
    assert {v[0] for (n, _), v in rows.items() if n == "rust"} == {"1.100.0"}
    assert {v[0] for (n, _), v in rows.items() if n == "go"} == {"1.23.3"}
    assert {v[0][:5] for (n, _), v in rows.items() if n == "cpython"} == {"3.12."}
    text = LOCK.read_text()
    assert re.search(r"^@python-lock\s+agents/requirements.lock$", text, re.M)
    assert set(re.search(r"^@system\s+(.*)$", text, re.M).group(1).split()) >= {"gcc", "g++", "git"}


def test_script_is_bash_32_compatible():
    src = SCRIPT.read_text()
    for pat in (r"declare -A", r"\bmapfile\b", r"\breadarray\b", r"\$\{[^}]*,,\}",
                r"\$\{[^}]*\^\^\}", r"\|&", r"&>>", r"\bcoproc\b", r"\[\[ -v "):
        assert not re.search(pat, src), pat
    assert subprocess.run([BASH, "-n", str(SCRIPT)]).returncode == 0


def test_help_is_the_header():
    r = subprocess.run([BASH, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout.startswith("eval-host — ")
    assert "stops that). docs/SPARK_HYDRA.md F6 is the runbook." in r.stdout


# --- evals/eval_host.py: the harness side ---------------------------------------------------

@pytest.fixture
def eh(host, monkeypatch):
    import eval_host
    host.directives["@system"] = "gcc"
    for k, v in host.env().items():
        monkeypatch.setenv(k, v)
    # ensure_host writes these into os.environ itself; set-then-delete makes
    # monkeypatch restore them, or a fake RUSTUP_HOME would leak into every
    # later test that runs a real rustc.
    for k in ("OPENBEAST_EVAL_NO_PROVISION", "OPENBEAST_EVAL_REEXEC", "RUSTUP_HOME",
              "CARGO_HOME", "OPENBEAST_EVAL_PY"):
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    return eval_host


def test_ensure_host_provisions_then_puts_the_prefix_first(host, eh, monkeypatch, capsys):
    monkeypatch.setattr(eh, "check", _only("zig,go,rust,system"))
    monkeypatch.setattr(eh, "setup", _setup_only(eh, "zig,go,rust"))
    assert eh.ensure_host(argv=None) == []
    path = os.environ["PATH"].split(os.pathsep)
    assert path[:2] == [f"{host.prefix}/bin", f"{host.prefix}/cargo/bin"]
    assert os.environ["RUSTUP_HOME"] == f"{host.prefix}/rustup"
    assert "provisioning zig, go, rust" in capsys.readouterr().err
    # The toolchain the harness pins next resolves the provisioned copies.
    import vlib
    pins = vlib.pin_toolchain(("zig", "go"), path=os.environ["PATH"])
    assert pins["zig"]["found"] == f"{host.prefix}/bin/zig"
    assert pins["go"]["found"] == f"{host.prefix}/bin/go"


def test_ensure_host_with_no_provision_only_reports(host, eh, monkeypatch, capsys):
    monkeypatch.setenv("OPENBEAST_EVAL_NO_PROVISION", "1")
    path_before = os.environ["PATH"]
    probs = eh.ensure_host(argv=["x"])
    assert any(p.startswith("zig: missing") for p in probs), probs
    assert not host.prefix.exists()
    assert os.environ["PATH"] == path_before
    assert "provisioning is off" in capsys.readouterr().err


def test_ensure_host_reexecs_under_the_eval_venv(eh, monkeypatch):
    calls = []
    monkeypatch.setattr(eh, "check", lambda prefix=None: (0, [("ok", "zig", "0.16.0")]))
    py = "/opt/eval/venv/bin/python"
    monkeypatch.setattr(eh, "env_exports", lambda prefix=None: {
        "path_prepend": [], "env": {"OPENBEAST_EVAL_PY": py}})
    monkeypatch.setattr(eh.os, "access", lambda p, m: True)
    monkeypatch.setattr(eh.os, "execv", lambda exe, args: calls.append((exe, args)))
    assert eh.ensure_host(argv=["/repo/evals/run_eval.py", "--suite", "v5"]) == []
    assert calls == [(py, [py, "/repo/evals/run_eval.py", "--suite", "v5"])]
    assert os.environ["OPENBEAST_EVAL_REEXEC"] == "1"
    # Once re-exec'd, never again (no loop when the venv is not what it says).
    calls.clear()
    eh.ensure_host(argv=["/repo/evals/run_eval.py"])
    assert calls == []


def test_ensure_host_reports_what_setup_could_not_fix(eh, monkeypatch):
    seq = iter([(1, [("missing", "zig", "not on PATH"), ("system", "gcc", "gcc not found")]),
                (1, [("ok", "zig", "0.16.0"), ("system", "gcc", "gcc not found")])])
    ran = []
    monkeypatch.setattr(eh, "check", lambda prefix=None: next(seq))
    monkeypatch.setattr(eh, "setup", lambda prefix=None, out=None: ran.append(1) or 1)
    monkeypatch.setattr(eh, "env_exports", lambda prefix=None: {"path_prepend": [], "env": {}})
    assert eh.ensure_host(argv=None) == ["gcc: system: gcc not found"]
    assert ran == [1]


def _only(only):
    """eval_host.check restricted to some tools (the fake host has no python pin)."""
    def check(prefix=None):
        import eval_host
        r = subprocess.run([BASH, eval_host.SCRIPT, "check", "--only", only],
                           capture_output=True, text=True)
        rows = [tuple((ln.split(None, 2) + [""])[:3]) for ln in r.stdout.splitlines()
                if ln.split(None, 1)[:1] and ln.split()[0] in eval_host.STATES]
        return r.returncode, rows
    return check


def _setup_only(eh, only):
    def setup(prefix=None, out=None):
        return subprocess.run([BASH, eh.SCRIPT, "setup", "--only", only],
                              capture_output=True, text=True).returncode
    return setup


# --- the wiring: run_eval / profile / benchmark_all call it ---------------------------------

def test_run_eval_main_refuses_a_host_it_could_not_provision(monkeypatch, capsys):
    import eval_host
    import run_eval
    seen = {}

    def fake(argv=None, no_provision=False, prefix=None, log=None):
        seen.update(argv=argv, no_provision=no_provision)
        return ["zig: missing: zig 0.16.0 not on PATH"]

    monkeypatch.setattr(eval_host, "ensure_host", fake)
    monkeypatch.delenv("OPENBEAST_EVAL_NO_PROVISION", raising=False)
    monkeypatch.setattr(run_eval, "_install_signal_reaper", lambda: None)
    monkeypatch.setattr(run_eval, "run_eval", lambda **k: pytest.fail("ran on a broken host"))
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--suite", "v5"])
    with pytest.raises(SystemExit) as e:
        run_eval.main()
    assert e.value.code == 2
    assert seen["argv"][0].endswith("evals/run_eval.py") and seen["argv"][1:] == ["--suite", "v5"]
    assert "zig 0.16.0 not on PATH" in capsys.readouterr().err


def test_run_eval_cache_only_never_touches_the_host(monkeypatch):
    import eval_host
    import run_eval
    monkeypatch.setattr(eval_host, "ensure_host", lambda **k: pytest.fail("cache-only provisioned"))
    monkeypatch.setattr(run_eval, "_install_signal_reaper", lambda: None)
    monkeypatch.setattr(run_eval, "run_eval", lambda **k: {"summary": {"failed": 0}})
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--cache-only", "--model-name", "m"])
    run_eval.main()


def test_profile_main_provisions_before_preflight(monkeypatch):
    import eval_host
    import profile as P
    order = []

    class Stop(Exception):
        pass

    def fake_ensure(argv=None, no_provision=False, prefix=None, log=None):
        order.append(("ensure", no_provision, argv[0].endswith("evals/profile.py")))
        raise Stop

    monkeypatch.setattr(eval_host, "ensure_host", fake_ensure)
    monkeypatch.setattr(P, "preflight", lambda *a: order.append("preflight") or [])
    with pytest.raises(Stop):
        P.main(["--no-provision", "--budget-h", "1"])
    assert order == [("ensure", True, True)]


def test_profile_preflight_carries_the_host_check(monkeypatch):
    import eval_host
    import profile as P
    import run_eval
    monkeypatch.setattr(eval_host, "check_problems", lambda prefix=None: ["rust: wrong: rustc 1.99"])
    monkeypatch.setattr(run_eval, "capture_server_config", lambda *a, **k: {"reasoning_budget": "0"})
    probs = P.preflight("http://127.0.0.1:9/v1", "0", False)
    assert "eval host: rust: wrong: rustc 1.99" in probs


def test_benchmark_all_main_calls_the_host_check(monkeypatch, capsys):
    import benchmark_all
    import eval_host
    monkeypatch.setattr(eval_host, "ensure_host",
                        lambda argv=None, no_provision=False, **k: ["go: missing"])
    monkeypatch.delenv("OPENBEAST_EVAL_NO_PROVISION", raising=False)
    monkeypatch.setattr(benchmark_all, "_install_signal_handler", lambda: None)
    monkeypatch.setattr(sys, "argv", ["benchmark_all.py", "--models", benchmark_all.MODELS[0]["slug"]])
    with pytest.raises(SystemExit) as e:
        benchmark_all.main()
    assert e.value.code == 2
    assert "go: missing" in capsys.readouterr().err


# --- the interpreter guard -------------------------------------------------------------------

_DEAD = {"exit_code": 1, "elapsed_seconds": 0.4, "stdout": "",
         "stderr": "Could not find platform independent libraries <prefix>\n"
                   "Fatal Python error: Failed to import encodings module\n",
         "tokens": {"prompt": 0, "completion": 0, "total": 0}}


def test_interpreter_startup_failure_signatures():
    import eval_host
    f = eval_host.interpreter_startup_failure
    assert f(_DEAD) == "Could not find platform independent libraries <prefix>"
    assert f({**_DEAD, "stderr": "ModuleNotFoundError: No module named 'openai'"})
    assert f({**_DEAD, "stderr": "dyld[123]: Library not loaded: @rpath/libpython3.12.dylib"})
    # Not an interpreter death: slow, spoke to the model, clean exit, a signal, other text.
    assert f({**_DEAD, "elapsed_seconds": 30.0}) is None
    assert f({**_DEAD, "tokens": {"completion": 12}}) is None
    assert f({**_DEAD, "exit_code": 0}) is None
    assert f({**_DEAD, "exit_code": -9}) is None
    assert f({**_DEAD, "stderr": "Traceback: AssertionError"}) is None


def test_interpreter_problem_names_a_broken_python(tmp_path, monkeypatch):
    import eval_host
    bad = tmp_path / "python"
    bad.write_text("#!/bin/sh\necho 'Could not find platform independent libraries <prefix>' >&2\n"
                   "exit 1\n")
    bad.chmod(0o755)
    msg = eval_host.interpreter_problem(str(bad), force=True)
    assert msg and "Could not find platform independent libraries" in msg
    assert "eval-host.sh setup" in msg
    # A healthy python passes. The lock's @python-imports (openai, ...) need
    # not be installed in whatever runs pytest, so the check imports a stdlib
    # module here; a missing one is still caught and named.
    monkeypatch.setattr(eval_host, "lock_directive",
                        lambda name: "json" if name == "@python-imports" else "")
    assert eval_host.interpreter_problem(sys.executable, force=True) is None
    monkeypatch.setattr(eval_host, "lock_directive",
                        lambda name: "json no_such_module_xyz" if name == "@python-imports" else "")
    msg = eval_host.interpreter_problem(sys.executable, force=True)
    assert msg and "No module named 'no_such_module_xyz'" in msg


def _fresh(tmp_path, n_tasks=1):
    import importlib
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for i in range(n_tasks):
        tid = f"0{i + 1}_t{i}"
        (tasks / f"{tid}.json").write_text(json.dumps({
            "id": tid, "name": tid, "difficulty": "easy", "task": "do it",
            "validation": {"type": "bash", "script": "false"}, "max_iter": 3}))
    run_eval.TASKS_DIR = str(tasks)
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    return run_eval, cache


def _run(run_eval, monkeypatch, agent):
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: dict(agent))
    monkeypatch.setattr(run_eval, "run_validation", lambda t, **k: (False, "FileNotFound: out"))
    monkeypatch.setattr(run_eval, "capture_server_config", lambda *a, **k: {})
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info", lambda: {})
    return run_eval.run_eval(model_name="m")


def test_startup_death_is_an_unscored_uncached_infra_row_and_aborts(tmp_path, monkeypatch):
    import eval_host
    import scoring
    import ablation
    run_eval, cache = _fresh(tmp_path, n_tasks=3)
    checks = []

    def problem(python=None, force=False):
        checks.append(force)
        return "the eval interpreter is broken" if force else None

    monkeypatch.setattr(eval_host, "interpreter_problem", problem)
    res = _run(run_eval, monkeypatch, _DEAD)
    rows = res["tasks"]
    assert rows[0]["reason"] == "interpreter_error"
    assert rows[0]["interpreter_error"].startswith("Could not find platform independent")
    assert len(rows) == 1, "a broken interpreter must stop the run, not score every unit"
    assert checks == [False, True]
    assert not (cache.CACHE_DIR.exists() and list(cache.CACHE_DIR.glob("*.json")))
    assert run_eval.cacheable_result({**rows[0], "tokens_completion": 900}) is False
    assert "interpreter_error" in scoring.INFRA_REASONS
    assert "interpreter_error" in ablation.INVALID_REASONS


def test_a_one_off_startup_death_does_not_abort(tmp_path, monkeypatch):
    import eval_host
    run_eval, _ = _fresh(tmp_path, n_tasks=2)
    monkeypatch.setattr(eval_host, "interpreter_problem", lambda python=None, force=False: None)
    res = _run(run_eval, monkeypatch, _DEAD)
    assert [r["reason"] for r in res["tasks"]] == ["interpreter_error", "interpreter_error"]


def test_an_ordinary_fail_is_not_an_interpreter_error(tmp_path, monkeypatch):
    import eval_host
    run_eval, _ = _fresh(tmp_path)
    # The pre-run check would probe the python running pytest, which need not
    # have the runner's packages; this test is about the row, not that check.
    monkeypatch.setattr(eval_host, "interpreter_problem", lambda python=None, force=False: None)
    res = _run(run_eval, monkeypatch, {**_DEAD, "exit_code": 0, "elapsed_seconds": 40.0,
                                       "stderr": "", "tokens": {"prompt": 9, "completion": 500,
                                                                "total": 509}})
    assert "reason" not in res["tasks"][0]


def test_run_eval_refuses_a_broken_interpreter_before_any_unit(tmp_path, monkeypatch):
    import eval_host
    run_eval, _ = _fresh(tmp_path)
    monkeypatch.setattr(eval_host, "interpreter_problem",
                        lambda python=None, force=False: "the eval interpreter is broken")
    with pytest.raises(SystemExit) as e:
        _run(run_eval, monkeypatch, {"exit_code": "an agent ran"})
    assert "REFUSED: the eval interpreter is broken" in str(e.value)
    assert not (tmp_path / "results").exists() or not list((tmp_path / "results").glob("*.json"))
