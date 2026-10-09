"""model-fetch.sh against a stub Hub: only the pinned commit is requested,
every file is checked against the Hub's own hash, a corrupted file leaves
nothing under the final name, partial downloads resume with Range, the lock
makes re-runs verify instead of download, and a new REVISION never
overwrites the old directory. No network: the Hub is a loopback stub.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
# first: onboarding_helpers puts scripts/backends/pylib on sys.path
from onboarding_helpers import REPO, SHA, SHA2, clean_env, hf_profile, make_remote  # noqa: E402
import model_fetch  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    clean_env(monkeypatch)


@pytest.fixture
def remote():
    hub = make_remote()
    yield hub
    hub.close()


def fetch(tmp_path, profile: Path, *args) -> int:
    return model_fetch.main(["--profile", str(profile), "--models-dir", str(tmp_path / "models"), *args])


def test_fetch_verify_lock_and_idempotence(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    prof = hf_profile(tmp_path)
    assert fetch(tmp_path, prof) == 0
    final = tmp_path / "models" / "brandnew"
    assert (final / "config.json").is_file() and (final / "sub" / "extra.json").is_file()
    assert not (final / "pytorch_model.bin").exists(), "pickled weights are skipped by default"
    assert not (final / ".gitattributes").exists()
    assert not (tmp_path / "models" / ".brandnew.partial").exists()
    lock = json.loads(prof.with_suffix(".lock").read_text())
    art = lock["artifacts"]["model"]
    assert art["revision"] == SHA and art["source"] == "acme/Brand-New"
    w = art["files"]["model-00001-of-00001.safetensors"]
    assert w["lfs"] and w["sha256"] == hashlib.sha256(remote.repos["acme/Brand-New"]["files"][
        "model-00001-of-00001.safetensors"]).hexdigest()
    assert json.loads((final / ".openbeast-model.json").read_text())["revision"] == SHA
    assert all(f"/resolve/{SHA}/" in p for p in remote.downloads()), "only the pinned commit is ever requested"
    n = len(remote.downloads())
    assert fetch(tmp_path, prof) == 0                      # re-run: verify, no download
    assert len(remote.downloads()) == n
    assert "every file matches the lock" in capsys.readouterr().err
    assert fetch(tmp_path, prof, "--verify") == 0
    (final / "config.json").write_text("{}")
    assert fetch(tmp_path, prof, "--verify") == 1
    assert "MISMATCH" in capsys.readouterr().err


def test_fetch_corrupted_file_leaves_nothing_under_final_name(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    remote.corrupt.add("model-00001-of-00001.safetensors")
    prof = hf_profile(tmp_path)
    assert fetch(tmp_path, prof) == 1
    err = capsys.readouterr().err
    assert "does not match the Hub's LFS oid" in err
    assert not (tmp_path / "models" / "brandnew").exists()
    assert not prof.with_suffix(".lock").exists()
    stage = tmp_path / "models" / ".brandnew.partial"
    assert not (stage / "model-00001-of-00001.safetensors").exists(), "the bad file is deleted"
    remote.corrupt.clear()                                  # the mirror heals: the next run resumes
    assert fetch(tmp_path, prof) == 0
    assert (tmp_path / "models" / "brandnew" / "model-00001-of-00001.safetensors").is_file()


def test_fetch_resumes_a_partial_with_range(tmp_path, remote, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    prof = hf_profile(tmp_path)
    stage = tmp_path / "models" / ".brandnew.partial"
    stage.mkdir(parents=True)
    (stage / ".stage.json").write_text(json.dumps({"repo": "acme/Brand-New", "revision": SHA}))
    full = remote.repos["acme/Brand-New"]["files"]["model-00001-of-00001.safetensors"]
    (stage / "model-00001-of-00001.safetensors").write_bytes(full[:1000])
    assert fetch(tmp_path, prof) == 0
    assert any(rng == "bytes=1000-" for p, _, rng in remote.log if p.endswith(".safetensors"))
    assert (tmp_path / "models" / "brandnew" / "model-00001-of-00001.safetensors").read_bytes() == full


def test_fetch_refusals(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    remote.sha_override = SHA2                              # the Hub answers for another commit
    assert fetch(tmp_path, hf_profile(tmp_path, "a")) == 1
    assert "it answered for" in capsys.readouterr().err
    remote.sha_override = None
    remote.extra_entries = [{"type": "file", "path": "../evil.json", "size": 1, "oid": "0" * 40}]
    assert fetch(tmp_path, hf_profile(tmp_path, "b")) == 1
    assert "unsafe path" in capsys.readouterr().err
    remote.extra_entries = []
    assert not (tmp_path / "evil.json").exists()
    # a new REVISION never overwrites the directory the lock describes
    prof = hf_profile(tmp_path, "c")
    assert fetch(tmp_path, prof) == 0
    prof.write_text(prof.read_text().replace(SHA, SHA2))
    assert fetch(tmp_path, prof) == 1
    assert "Move" in capsys.readouterr().err and (tmp_path / "models" / "c").is_dir()
    assert fetch(tmp_path, prof, "--locate") == 1


def test_fetch_space_preflight(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)

    class DU:
        free = 10

    monkeypatch.setattr(model_fetch.shutil, "disk_usage", lambda p: DU)
    assert fetch(tmp_path, hf_profile(tmp_path)) == 1
    assert "not enough space" in capsys.readouterr().err


def test_locate_contract(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    prof = hf_profile(tmp_path)
    assert fetch(tmp_path, prof, "--locate") == model_fetch.EXIT_ABSENT
    assert fetch(tmp_path, prof) == 0
    capsys.readouterr()
    assert fetch(tmp_path, prof, "--locate") == 0
    assert capsys.readouterr().out.strip() == f"model\t{tmp_path / 'models' / 'brandnew'}"


def test_fetch_shell_wrapper_reads_spark_env(tmp_path, remote):
    prof = hf_profile(tmp_path)
    env_file = tmp_path / "spark.env"
    env_file.write_text(f"MODELS_DIR={tmp_path}/m2   # comment\nHF_ENDPOINT={remote.url}\n")
    # OPENBEAST_OFFLINE: the wrapper also reads the checkout's openbeast.conf,
    # and OFFLINE=true in the rig's own conf refused this (loopback) fetch.
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "OPENBEAST_OFFLINE": "false"}
    r = subprocess.run(["bash", str(REPO / "scripts/backends/model-fetch.sh"), "--profile", str(prof),
                        "--env", str(env_file)], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "m2" / "brandnew" / "config.json").is_file()


# --------------------------------------------------------------------------- the lock is the pin

WEIGHTS = "model-00001-of-00001.safetensors"


def test_lock_is_the_pin_when_the_hub_changes(tmp_path, remote, monkeypatch, capsys):
    """The review's t_lock.py: a committed lock, a wiped models dir (another Spark), and a Hub or
    mirror that now serves DIFFERENT bytes with self-consistent hashes. Refetch must refuse."""
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    prof = hf_profile(tmp_path)
    assert fetch(tmp_path, prof) == 0
    lock_before = prof.with_suffix(".lock").read_text()
    import shutil
    shutil.rmtree(tmp_path / "models")
    remote.repos["acme/Brand-New"]["files"][WEIGHTS] += b"EVIL"
    assert fetch(tmp_path, prof) == 1
    assert "different size than the lock pins" in capsys.readouterr().err
    assert not (tmp_path / "models" / "brandnew").exists()
    assert prof.with_suffix(".lock").read_text() == lock_before, "the lock is never rewritten"


def test_lock_pin_same_size_different_bytes(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    prof = hf_profile(tmp_path)
    assert fetch(tmp_path, prof) == 0
    lock_before = prof.with_suffix(".lock").read_text()
    import shutil
    shutil.rmtree(tmp_path / "models")
    b = remote.repos["acme/Brand-New"]["files"][WEIGHTS]
    remote.repos["acme/Brand-New"]["files"][WEIGHTS] = b[:-4] + b"EVIL"      # same size, Hub hashes consistent
    assert fetch(tmp_path, prof) == 1
    assert "is not the one the lock pins" in capsys.readouterr().err
    assert not (tmp_path / "models" / "brandnew").exists()
    assert not (tmp_path / "models" / ".brandnew.partial" / WEIGHTS).exists()
    assert prof.with_suffix(".lock").read_text() == lock_before


def test_lock_pin_file_set_change(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    prof = hf_profile(tmp_path)
    assert fetch(tmp_path, prof) == 0
    import shutil
    shutil.rmtree(tmp_path / "models")
    remote.repos["acme/Brand-New"]["files"]["added.py"] = b"import os\n"
    assert fetch(tmp_path, prof) == 1
    assert "differ from the lock" in capsys.readouterr().err


def test_publish_is_crash_safe(tmp_path, remote, monkeypatch, capsys):
    """Interrupted after the lock is written but before the rename: the next run must recognise its
    own complete stage, re-verify it without downloading, and publish."""
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    prof = hf_profile(tmp_path)
    real_rename = model_fetch.os.rename

    def crash(*a):
        raise SystemExit("simulated crash before publish")

    monkeypatch.setattr(model_fetch.os, "rename", crash)
    with pytest.raises(SystemExit):
        fetch(tmp_path, prof)
    assert prof.with_suffix(".lock").is_file() and not (tmp_path / "models" / "brandnew").exists()
    monkeypatch.setattr(model_fetch.os, "rename", real_rename)
    n = len(remote.downloads())
    assert fetch(tmp_path, prof) == 0, capsys.readouterr().err
    assert len(remote.downloads()) == n, "a complete stage is re-verified, not downloaded again"
    final = tmp_path / "models" / "brandnew"
    assert (final / WEIGHTS).is_file() and not (final / model_fetch.STAGE_META).exists()
    assert fetch(tmp_path, prof, "--verify") == 0


# --------------------------------------------------------------------------- one test per guard
# Each test below fails if the guard it names is removed (the review's mutation run found them
# unpinned).

def test_small_file_checked_against_git_blob_id(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    remote.corrupt.add("config.json")                      # not LFS: only the git blob id vouches for it
    assert fetch(tmp_path, hf_profile(tmp_path)) == 1
    assert "git blob id" in capsys.readouterr().err
    assert not (tmp_path / "models" / "brandnew").exists()


def test_resume_restarts_when_the_server_ignores_range(tmp_path, remote, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    remote.ignore_range = True
    prof = hf_profile(tmp_path)
    stage = tmp_path / "models" / ".brandnew.partial"
    stage.mkdir(parents=True)
    (stage / model_fetch.STAGE_META).write_text(json.dumps({"repo": "acme/Brand-New", "revision": SHA}))
    full = remote.repos["acme/Brand-New"]["files"][WEIGHTS]
    (stage / WEIGHTS).write_bytes(full[:1000])
    assert fetch(tmp_path, prof) == 0
    assert (tmp_path / "models" / "brandnew" / WEIGHTS).read_bytes() == full


def test_stage_of_another_revision_is_refused(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    stage = tmp_path / "models" / ".brandnew.partial"
    stage.mkdir(parents=True)
    (stage / model_fetch.STAGE_META).write_text(json.dumps({"repo": "acme/Brand-New", "revision": SHA2}))
    assert fetch(tmp_path, hf_profile(tmp_path)) == 1
    assert "holds a different download" in capsys.readouterr().err


def test_large_file_without_lfs_hash_is_refused(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    remote.extra_entries = [{"type": "file", "path": "big.safetensors", "size": 20 * 1024 * 1024,
                             "oid": "0" * 40}]
    assert fetch(tmp_path, hf_profile(tmp_path)) == 1
    assert "no LFS sha256" in capsys.readouterr().err
    assert remote.downloads() == []


def test_existing_unlocked_directory_is_never_overwritten(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    final = tmp_path / "models" / "brandnew"
    final.mkdir(parents=True)
    (final / "mine.txt").write_text("keep me")
    assert fetch(tmp_path, hf_profile(tmp_path)) == 1
    assert "lock does not describe it" in capsys.readouterr().err
    assert (final / "mine.txt").read_text() == "keep me"


def test_verify_flags_unlocked_files_and_same_size_tamper(tmp_path, remote, monkeypatch, capsys):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    prof = hf_profile(tmp_path)
    assert fetch(tmp_path, prof) == 0
    final = tmp_path / "models" / "brandnew"
    (final / "planted.py").write_text("import os\n")
    assert fetch(tmp_path, prof, "--verify") == 1
    assert "unlocked file planted.py" in capsys.readouterr().err
    (final / "planted.py").unlink()
    w = final / WEIGHTS
    b = bytearray(w.read_bytes())
    b[-1] ^= 0xFF                                          # same size, different content
    w.write_bytes(bytes(b))
    assert fetch(tmp_path, prof, "--locate") == 0          # sizes only: the fast path cannot see it
    capsys.readouterr()
    assert fetch(tmp_path, prof, "--verify") == 1
    assert "sha256 differs from the lock" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["../evil.json", "/etc/evil.json", "sub\\\\..\\\\..\\\\evil.json", "a/../../evil.json"])
def test_unsafe_tree_paths_refuse_the_revision(tmp_path, remote, monkeypatch, capsys, bad):
    monkeypatch.setenv("HF_ENDPOINT", remote.url)
    remote.extra_entries = [{"type": "file", "path": bad, "size": 1, "oid": "0" * 40}]
    assert fetch(tmp_path, hf_profile(tmp_path)) == 1
    assert "unsafe path" in capsys.readouterr().err and remote.downloads() == []
