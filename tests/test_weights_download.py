"""scripts/weights-download.sh against a stub Hub: every URL form parses, the
default folder is WEIGHTS_DOWNLOAD_ROOT/ORG/REPO, every file is verified and
lands only after all passed, a re-run verifies instead of downloading, a bad
file leaves nothing in DEST and the next run resumes, one-file URLs merge into
the same folder, and another repo or a foreign folder is never overwritten.
No network: the Hub is a loopback stub.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "backends" / "pylib"))
import weights_download as wd
from onboarding_helpers import REPO, SHA, clean_env, make_remote

URL = "https://huggingface.co/acme/Brand-New"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    clean_env(monkeypatch)
    monkeypatch.setenv("WEIGHTS_DOWNLOAD_ROOT", str(tmp_path / "weights"))


@pytest.fixture
def remote(monkeypatch):
    hub = make_remote()
    monkeypatch.setenv("HF_ENDPOINT", hub.url)
    yield hub
    hub.close()


@pytest.mark.parametrize("url,want", [
    ("https://huggingface.co/acme/Brand-New", ("acme/Brand-New", None, None, None)),
    ("huggingface.co/acme/Brand-New/", ("acme/Brand-New", None, None, None)),
    ("acme/Brand-New", ("acme/Brand-New", None, None, None)),
    ("https://hf.co/acme/Brand-New/tree/v1.0", ("acme/Brand-New", "v1.0", None, None)),
    ("https://huggingface.co/acme/Brand-New/tree/main/sub", ("acme/Brand-New", "main", "sub", None)),
    ("https://huggingface.co/acme/Brand-New/blob/main/a/b.gguf", ("acme/Brand-New", "main", None, "a/b.gguf")),
    ("https://huggingface.co/acme/Brand-New/resolve/abc/x.safetensors?download=true",
     ("acme/Brand-New", "abc", None, "x.safetensors")),
])
def test_parse_url(url, want):
    p = wd.parse_url(url)
    assert (p["repo"], p["revision"], p["subdir"], p["file"]) == want


@pytest.mark.parametrize("url", ["https://huggingface.co/datasets/a/b", "https://example.com/a/b",
                                 "acme", "https://huggingface.co/acme/Brand-New/blob/main",
                                 "https://huggingface.co/acme/Brand-New/blob/main/../x"])
def test_parse_url_refuses(url):
    with pytest.raises(wd.DownloadError):
        wd.parse_url(url)


def test_default_folder_verify_and_rerun(tmp_path, remote, capsys):
    assert wd.main([URL]) == 0
    dest = tmp_path / "weights" / "acme" / "Brand-New"
    assert capsys.readouterr().out.strip() == str(dest.resolve())
    assert (dest / "config.json").is_file() and (dest / "sub" / "extra.json").is_file()
    assert (dest / "pytorch_model.bin").is_file(), "a generic download keeps every format"
    assert not (dest / ".gitattributes").exists()
    assert not (dest.parent / ".Brand-New.partial").exists()
    m = json.loads((dest / wd.MANIFEST).read_text())
    assert m["repo"] == "acme/Brand-New" and m["revision"] == SHA and len(m["files"]) == 5
    assert m["sources"][0]["url"] == URL
    assert all(f"/resolve/{SHA}/" in p for p in remote.downloads()), "only the resolved commit is requested"
    n = len(remote.downloads())
    assert wd.main([URL]) == 0                                   # re-run: verify, no download
    assert len(remote.downloads()) == n
    assert "every file matches" in capsys.readouterr().err
    assert wd.main(["--verify", str(dest)]) == 0
    (dest / "config.json").write_text("{}")
    assert wd.main(["--verify", str(dest)]) == wd.EXIT_MISMATCH


def test_explicit_dest_and_corrupt_file_resumes(tmp_path, remote, capsys):
    dest = tmp_path / "elsewhere" / "bn"
    remote.corrupt.add("model-00001-of-00001.safetensors")
    assert wd.main([URL, str(dest)]) == wd.EXIT_FAIL
    assert "does not match the Hub's LFS oid" in capsys.readouterr().err
    assert not dest.exists(), "nothing appears under DEST until every file passed"
    stage = dest.parent / ".bn.partial"
    assert (stage / "config.json").is_file() and not (stage / "model-00001-of-00001.safetensors").exists()
    remote.corrupt.clear()
    n = len(remote.downloads())
    assert wd.main([URL, str(dest)]) == 0
    assert len(remote.downloads()) - n == 1, "the good files are not downloaded again"
    assert (dest / "model-00001-of-00001.safetensors").is_file() and not stage.exists()


def test_single_files_merge_and_foreign_folders_are_refused(tmp_path, remote, capsys):
    dest = tmp_path / "w"
    assert wd.main([f"{URL}/blob/main/config.json", str(dest)]) == 0
    assert sorted(json.loads((dest / wd.MANIFEST).read_text())["files"]) == ["config.json"]
    assert wd.main([f"{URL}/resolve/main/sub/extra.json", str(dest)]) == 0
    m = json.loads((dest / wd.MANIFEST).read_text())
    assert sorted(m["files"]) == ["config.json", "sub/extra.json"] and len(m["sources"]) == 2
    remote.repos["acme/Other"] = {"sha": SHA, "files": {"config.json": b"{}"}}
    assert wd.main(["acme/Other", str(dest)]) == wd.EXIT_FAIL
    assert "never overwritten" in capsys.readouterr().err
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "mine.txt").write_text("x")
    assert wd.main([URL, str(foreign)]) == wd.EXIT_FAIL
    assert (foreign / "mine.txt").read_text() == "x" and not (foreign / "config.json").exists()


def test_include_exclude_and_dry_run(tmp_path, remote, capsys):
    dest = tmp_path / "d"
    assert wd.main([URL, str(dest), "--dry-run"]) == 0
    assert not dest.exists() and not remote.downloads()
    assert wd.main([URL, str(dest), "--include", "*.json", "--exclude", "tokenizer*"]) == 0
    assert sorted(json.loads((dest / wd.MANIFEST).read_text())["files"]) == ["config.json", "sub/extra.json"]
    assert wd.main([URL, str(tmp_path / "e"), "--include", "*.nothing"]) == wd.EXIT_FAIL
    assert "nothing to download" in capsys.readouterr().err


def test_wrapper_offline_and_usage(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "OFFLINE", "OPENBEAST_OFFLINE"))}
    env.update(OFFLINE="true", WEIGHTS_DOWNLOAD_ROOT=str(tmp_path / "w"), HOME=str(tmp_path),
               OPENBEAST_PY=sys.executable)
    sh = REPO / "scripts" / "weights-download.sh"
    p = subprocess.run([str(sh), URL], env=env, capture_output=True, text=True, check=False)
    assert p.returncode == 1 and "--verify DIR" in p.stderr, p.stderr
    p = subprocess.run([str(sh)], env=env, capture_output=True, text=True, check=False)
    assert p.returncode == 2
    p = subprocess.run([str(sh), "--verify", str(tmp_path / "none")], env=env, capture_output=True, text=True, check=False)
    assert p.returncode == wd.EXIT_MISMATCH and "has no" in p.stderr


SHARD = "model-00001-of-00001.safetensors"


def _shard_gets(remote) -> list[str | None]:
    return [r for p, _, r in remote.log if p.endswith("/" + SHARD)]


def test_a_stalled_download_is_dropped_and_resumed(tmp_path, remote, monkeypatch, capsys):
    """A trickling connection (the ~1 MB/s crawl seen on spark-1) is dropped and the file is
    requested again from where it stopped; a fresh connection is fast."""
    monkeypatch.setenv("FETCH_STALL_SECS", "0.3")
    monkeypatch.setenv("FETCH_STALL_MB", "0.01")          # 10.5 KB per 0.3 s; the trickle is ~3 KB
    remote.slow[SHARD] = 1
    assert wd.main([URL, str(tmp_path / "m"), "--include", SHARD]) == 0
    err = capsys.readouterr().err
    assert f"stalled {SHARD}" in err and "restart 1 of 5 (it resumes)" in err
    gets = _shard_gets(remote)
    assert len(gets) == 2 and gets[0] is None and gets[1] and gets[1].startswith("bytes=") and gets[1] != "bytes=0-"
    assert wd.main(["--verify", str(tmp_path / "m")]) == 0


def test_a_silent_connection_times_out_into_a_restart(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("FETCH_READ_TIMEOUT", "0.3")
    remote.hang[SHARD] = 1
    assert wd.main([URL, str(tmp_path / "m"), "--include", SHARD]) == 0
    assert "nothing received for 0.3 s" in capsys.readouterr().err
    assert len(_shard_gets(remote)) == 2


def test_stall_restarts_are_bounded_and_the_rerun_resumes(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("FETCH_STALL_SECS", "0.2")
    monkeypatch.setenv("FETCH_STALL_MB", "1")
    monkeypatch.setenv("FETCH_STALL_RESTARTS", "1")
    remote.slow[SHARD] = 99
    dest = tmp_path / "m"
    assert wd.main([URL, str(dest), "--include", SHARD]) == wd.EXIT_FAIL
    err = capsys.readouterr().err
    assert "stalled 2 times" in err and "re-run the same command to resume" in err
    assert len(_shard_gets(remote)) == 2 and not dest.exists()
    part = tmp_path / ".m.partial" / SHARD
    assert part.is_file() and part.stat().st_size > 0, "what arrived is kept for the resume"
    kept = part.stat().st_size
    remote.slow.clear()
    assert wd.main([URL, str(dest), "--include", SHARD]) == 0
    assert _shard_gets(remote)[-1] == f"bytes={kept}-", "the re-run resumes from the kept bytes"
    assert wd.main(["--verify", str(dest)]) == 0


def test_stall_settings_must_be_numbers(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("FETCH_STALL_MB", "lots")
    assert wd.main([URL, str(tmp_path / "m"), "--include", SHARD]) == wd.EXIT_FAIL
    assert "FETCH_STALL_MB='lots': a number" in capsys.readouterr().err
