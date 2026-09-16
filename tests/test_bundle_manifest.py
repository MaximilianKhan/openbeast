"""The offline bundle's manifest: what it must refuse to say.

A bundle is carried in on a USB stick and then INSTALLED — it writes a source
tree, installs python packages, loads container images and places a weight. So
the manifest is not bookkeeping, it is the only thing standing between "a
directory arrived" and "a directory is trusted".

The failures worth pinning are the quiet ones:

  * an EMPTY component must not be recordable. It would verify perfectly and
    install nothing, and the operator would have no way to tell the difference
    from a bundle that worked.
  * a file present but UNRECORDED must be a problem. `install` reads from this
    directory; an unrecorded file is what a tampered or half-rebuilt bundle
    looks like.
  * a size that matches with a hash that does not, and vice versa, must both
    be caught — a truncated transfer changes size, a substituted file often
    does not.
  * an unknown bundle_version must REFUSE rather than guess at a layout.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts", "lib"))

import bundle_manifest as B     # noqa: E402


def _bundle(tmp_path, files: dict) -> str:
    """A bundle directory with the given {relpath: bytes}, manifest written."""
    root = tmp_path / "bundle"
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(body)
    kinds = sorted({rel.split("/")[0] for rel in files})
    args = ["write", str(root), "--built-at", "2026-01-01T00:00:00Z",
            "--repo-commit", "deadbeef", "--eval-era", "abc123"]
    for k in kinds:
        args += ["--component", f"{k}:{k}"]
    assert B.main(args) == 0
    return str(root)


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def test_a_written_manifest_records_every_file_with_its_hash(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA", "wheels/b.whl": b"BBB"})
    doc = B.load(root)
    assert doc["bundle_version"] == B.VERSION
    assert doc["repo_commit"] == "deadbeef"
    assert doc["eval_era"] == "abc123"
    files = doc["components"][0]["files"]
    assert [f["path"] for f in files] == ["wheels/a.whl", "wheels/b.whl"]
    assert files[0]["bytes"] == 3
    assert files[0]["sha256"] == B.hashlib.sha256(b"AAA").hexdigest()


def test_an_empty_component_is_refused(tmp_path):
    """It would verify clean and install NOTHING, and nothing about the
    output would tell the operator which of those happened."""
    root = tmp_path / "bundle"
    (root / "wheels").mkdir(parents=True)
    rc = B.main(["write", str(root), "--component", "wheels:wheels"])
    assert rc == 1


def test_a_component_spec_must_name_a_directory(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    assert B.main(["write", str(root), "--component", "justakind"]) == 1


def test_the_manifest_never_records_itself(tmp_path):
    """MANIFEST.json cannot contain its own hash, and an entry claiming to
    would fail verification on every bundle, forever."""
    root = _bundle(tmp_path, {"meta/x": b"x"})
    doc = B.load(root)
    paths = [f["path"] for c in doc["components"] for f in c["files"]]
    assert B.MANIFEST not in paths


# --------------------------------------------------------------------------
# verifying
# --------------------------------------------------------------------------

def test_verify_passes_on_an_untouched_bundle(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA", "source/s.tar.gz": b"SRC"})
    ok, problems = B.verify(root)
    assert problems == []
    assert sorted(ok) == ["source/s.tar.gz", "wheels/a.whl"]


def test_verify_catches_a_substituted_file_of_the_same_length(tmp_path):
    """The case a size check alone would miss, and the one that matters: a
    substitution does not have to change the length."""
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    with open(os.path.join(root, "wheels/a.whl"), "wb") as fh:
        fh.write(b"BBB")
    ok, problems = B.verify(root)
    assert ok == []
    assert len(problems) == 1 and "sha256" in problems[0]


def test_verify_catches_a_truncated_file(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAAAAAAA"})
    with open(os.path.join(root, "wheels/a.whl"), "wb") as fh:
        fh.write(b"AAA")
    _ok, problems = B.verify(root)
    assert len(problems) == 1 and "bytes" in problems[0]


def test_verify_catches_a_missing_file(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA", "wheels/b.whl": b"BBB"})
    os.unlink(os.path.join(root, "wheels/b.whl"))
    ok, problems = B.verify(root)
    assert ok == ["wheels/a.whl"]
    assert any("MISSING" in p for p in problems)


def test_verify_catches_a_file_the_manifest_does_not_record(tmp_path):
    """`install` reads from this directory. An unrecorded file is what a
    tampered or half-rebuilt bundle looks like, and a manifest that only
    checks what it already knows about would never see it."""
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    with open(os.path.join(root, "wheels/evil.whl"), "wb") as fh:
        fh.write(b"X")
    _ok, problems = B.verify(root)
    assert any("NOT in the manifest" in p for p in problems)


def test_verify_refuses_a_bundle_version_it_does_not_know(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    path = B.manifest_path(root)
    doc = json.load(open(path))
    doc["bundle_version"] = 99
    json.dump(doc, open(path, "w"))
    with pytest.raises(B.BundleError) as e:
        B.verify(root)
    assert "99" in str(e.value)


def test_a_directory_with_no_manifest_is_not_a_bundle(tmp_path):
    d = tmp_path / "notabundle"
    d.mkdir()
    with pytest.raises(B.BundleError) as e:
        B.load(str(d))
    assert "not a bundle" in str(e.value)


def test_an_unparseable_manifest_says_so_rather_than_crashing(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    with open(B.manifest_path(str(d)), "w") as fh:
        fh.write("{ not json")
    with pytest.raises(B.BundleError) as e:
        B.load(str(d))
    assert "parseable" in str(e.value)


# --------------------------------------------------------------------------
# the summary a human reads before trusting it
# --------------------------------------------------------------------------

def test_the_summary_names_what_is_NOT_included(tmp_path):
    """Weights are opt-in, so the common bundle is missing the largest thing
    the target needs. That has to be visible at `show` time, not discovered
    at install time."""
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    path = B.manifest_path(root)
    doc = json.load(open(path))
    doc["skipped"] = ["weights (not requested)"]
    json.dump(doc, open(path, "w"))
    out = B.summarise(B.load(root))
    assert "NOT INCLUDED" in out and "weights" in out


def test_the_summary_shows_image_ids_because_that_is_what_gets_installed(tmp_path):
    """compose is rewritten to reference images by content ID, so the ID is
    the thing a reviewer needs to see — not just a filename."""
    root = _bundle(tmp_path, {"images/i.tar.gz": b"IMG"})
    path = B.manifest_path(root)
    doc = json.load(open(path))
    for comp in doc["components"]:
        if comp["kind"] == "images":
            comp["images"] = [{"ref": "searxng/searxng:latest@sha256:abc",
                               "id": "sha256:" + "d" * 64,
                               "file": "images/i.tar.gz"}]
    json.dump(doc, open(path, "w"))
    out = B.summarise(B.load(root))
    assert "searxng" in out
    assert "sha256:dddd" in out


def test_the_summary_reports_a_total_size(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"A" * 4096})
    out = B.summarise(B.load(root))
    assert "TOTAL" in out
    assert "KB" in out or "MB" in out


# --------------------------------------------------------------------------
# the detached signature (review of the air-gap threat model)
# --------------------------------------------------------------------------
# Hashes are integrity; a signature is authenticity. MANIFEST.json proves the
# bundle did not change in transit and proves NOTHING about who built it —
# anyone who can write to the medium can rebuild the manifest to match their
# own payload, and every hash would then verify. The signature closes that,
# which means the manifest machinery has to tolerate it existing.

def test_the_signature_is_not_treated_as_an_unrecorded_file(tmp_path):
    """It is created AFTER the manifest, so it can never be a manifest entry
    — and if the unrecorded-file scan flagged it, every signed bundle would
    fail its own verification for the crime of being signed."""
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    with open(os.path.join(root, B.SIGNATURE), "w") as fh:
        fh.write("-----BEGIN SSH SIGNATURE-----\nnot-a-real-signature\n")
    ok, problems = B.verify(root)
    assert problems == [], problems
    assert ok == ["wheels/a.whl"]


def test_the_signature_is_never_recorded_as_a_component_file(tmp_path):
    """A manifest that claimed a hash for its own signature could not be
    satisfied: signing changes the directory after the hashes were taken.

    THE COMPONENT MUST BE THE BUNDLE ROOT for this to test anything. The
    first version of this test used `--component meta:meta` with the signature
    at the bundle root — which no component walk reaches — so it passed
    whether or not the recorder skipped SIGNATURE at all. Verified: removing
    the skip left the whole suite green."""
    root = tmp_path / "bundle"
    root.mkdir()
    with open(root / "x", "wb") as fh:
        fh.write(b"x")
    with open(root / B.SIGNATURE, "w") as fh:
        fh.write("-----BEGIN SSH SIGNATURE-----\nstub\n")
    # "." walks the bundle root itself, where MANIFEST.json and its signature
    # live — the only arrangement in which the basename skip is load-bearing.
    assert B.main(["write", str(root), "--component", "all:."]) == 0
    doc = B.load(str(root))
    paths = [f["path"] for c in doc["components"] for f in c["files"]]
    assert B.SIGNATURE not in paths, paths
    assert B.MANIFEST not in paths, paths
    assert paths == ["x"], paths
    # and it verifies clean with the signature sitting there
    ok, problems = B.verify(str(root))
    assert problems == [], problems


# --------------------------------------------------------------------------
# path containment (adversarial review, 2026-09-15 — all SHIPPED)
# --------------------------------------------------------------------------
# A bundle's manifest is attacker-controlled in the threat model the bundle
# exists for: a directory that travelled on a stick. os.path.join is not a
# containment primitive, and os.walk does not descend into symlinked
# directories — both were load-bearing mistakes here.

def test_an_absolute_path_in_a_manifest_entry_is_refused(tmp_path):
    """SHIPPED BUG. join(root, "/etc/hostname") returns "/etc/hostname", so
    verify() hashed the TARGET MACHINE'S file and reported it as verified
    bundle content."""
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    path = B.manifest_path(root)
    doc = json.load(open(path))
    doc["components"][0]["files"].append(
        {"path": "/etc/hostname", "bytes": 1, "sha256": "0" * 64})
    json.dump(doc, open(path, "w"))
    ok, problems = B.verify(root)
    assert "/etc/hostname" not in ok
    assert any("not relative" in p for p in problems), problems


def test_a_parent_escape_in_a_manifest_entry_is_refused(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    path = B.manifest_path(root)
    doc = json.load(open(path))
    doc["components"][0]["files"].append(
        {"path": "../../../etc/hostname", "bytes": 1, "sha256": "0" * 64})
    json.dump(doc, open(path, "w"))
    ok, problems = B.verify(root)
    assert not any(".." in x for x in ok)
    assert any("not contained" in p for p in problems), problems


def test_safe_join_refuses_every_shape_of_escape(tmp_path):
    root = str(tmp_path)
    for bad in ("/etc/passwd", "../x", "a/../../x", "", "./x", "a//b",
                "a/./b", "x\x00y"):
        with pytest.raises(B.BundleError):
            B.safe_join(root, bad)
    # and accepts the ordinary case
    (tmp_path / "wheels").mkdir()
    assert B.safe_join(root, "wheels/a.whl").endswith("wheels/a.whl")


def test_safe_join_refuses_a_path_that_escapes_through_a_symlink(tmp_path):
    """Containment has to survive a symlink COMPONENT, not just a literal
    '..' — otherwise a link inside the bundle redirects the resolved path."""
    (tmp_path / "wheels").mkdir()
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    os.symlink(str(outside), str(tmp_path / "wheels" / "out"))
    with pytest.raises(B.BundleError) as e:
        B.safe_join(str(tmp_path), "wheels/out/payload")
    assert "escapes" in str(e.value) or "not contained" in str(e.value)


def test_the_writer_refuses_a_symlinked_directory(tmp_path):
    """SHIPPED BUG. os.walk does not descend into a symlinked directory, so
    everything under one was invisible to BOTH the manifest and verify()'s
    unrecorded-file scan: a bundle carrying a hidden payload.whl behind a
    symlink reported "1 file verified, 0 problems"."""
    root = tmp_path / "bundle"
    (root / "wheels").mkdir(parents=True)
    with open(root / "wheels" / "a.whl", "wb") as fh:
        fh.write(b"AAA")
    stash = tmp_path / "stash"
    stash.mkdir()
    with open(stash / "payload.whl", "wb") as fh:
        fh.write(b"PAYLOAD")
    os.symlink(str(stash), str(root / "wheels" / "sub"))
    rc = B.main(["write", str(root), "--component", "wheels:wheels"])
    assert rc == 1, "the writer recorded a bundle with a symlinked directory"


def test_verify_catches_a_symlink_planted_after_the_manifest_was_written(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    stash = tmp_path / "stash2"
    stash.mkdir()
    with open(stash / "payload.whl", "wb") as fh:
        fh.write(b"PAYLOAD")
    os.symlink(str(stash), os.path.join(root, "wheels", "sub"))
    _ok, problems = B.verify(root)
    assert any("symlink" in p for p in problems), problems


def test_a_symlinked_FILE_is_not_silently_accepted(tmp_path):
    root = _bundle(tmp_path, {"wheels/a.whl": b"AAA"})
    target = tmp_path / "elsewhere.whl"
    with open(target, "wb") as fh:
        fh.write(b"ELSEWHERE")
    os.symlink(str(target), os.path.join(root, "wheels", "b.whl"))
    _ok, problems = B.verify(root)
    assert any("symlink" in p for p in problems), problems
