#!/usr/bin/env python3
"""beast-artifact store (agents/artifact.py) — layout, versions, caps, safety.

Covers:
  - store root is 0700 and created on demand; publish → read round trip
  - republish keeps the id and the URL, adds v2, leaves v1 byte-identical
  - meta.json shape + atomic rewrite; index.jsonl publish log
  - caps: oversize page, 256th file, oversize binary, per-version total
  - path safety: traversal / absolute / backslash / reserved index.html keys
    rejected on the way IN and again on the way OUT
  - visibility, rollback, remove, can_view
  - extract_title only looks at the first 8 KB
  - wrap_skeleton wraps a fragment, passes a full document through

Run: pytest tests/test_artifact_store.py
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import artifact  # noqa: E402

PAGE = "<title>Hello</title><p>hi</p>"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OPENBEAST_ARTIFACT_BASE_URL", "https://beast:8446")
    return artifact


# --- layout ------------------------------------------------------------------

def test_store_root_created_0700(store, tmp_path):
    root = store.store_root()
    assert root == str(tmp_path / "files" / "artifacts")
    assert os.path.isdir(root)
    assert oct(os.stat(root).st_mode & 0o777) == "0o700"


def test_publish_read_round_trip(store):
    r = store.publish(PAGE, description="a greeting", favicon="👋")
    assert r["version"] == 1
    assert r["url"] == f"https://beast:8446/a/{r['id']}"
    assert r["title"] == "Hello"          # taken from <title>
    assert r["bytes"] == len(PAGE.encode())

    data, ctype = store.read_file(r["id"], 1)
    assert data == PAGE.encode()          # stored EXACTLY as handed over
    assert ctype == "text/html; charset=utf-8"

    meta = store.get_meta(r["id"])
    assert meta["id"] == r["id"]
    assert meta["visibility"] == "private"
    assert meta["current"] == 1
    assert meta["description"] == "a greeting" and meta["favicon"] == "👋"
    for key in ("owner", "created_at", "updated_at", "versions"):
        assert key in meta
    v1 = meta["versions"][0]
    assert v1["n"] == 1 and v1["files"] == [] and len(v1["sha256"]) == 64
    assert os.path.isfile(os.path.join(
        store.store_root(), r["id"], "v1", "index.html"))


def test_publish_log_appended(store):
    a = store.publish(PAGE)
    b = store.publish(PAGE, artifact_id=a["id"])
    rows = [json.loads(x) for x in open(
        os.path.join(store.store_root(), "index.jsonl")).read().splitlines()]
    assert [(r["id"], r["n"]) for r in rows] == [(a["id"], 1), (b["id"], 2)]
    assert "html" not in json.dumps(rows)   # never content


def test_supporting_files_and_types(store):
    r = store.publish(PAGE, files={
        "app.js": "console.log(1)",
        "data/points.json": b'{"a":1}',
        "img/logo.png": b"\x89PNG\r\n\x1a\n",
    })
    meta = store.get_meta(r["id"])
    assert meta["versions"][0]["files"] == [
        "app.js", "data/points.json", "img/logo.png"]
    data, ctype = store.read_file(r["id"], 1, "data/points.json")
    assert data == b'{"a":1}' and ctype.startswith("application/json")
    _, ctype = store.read_file(r["id"], 1, "img/logo.png")
    assert ctype == "image/png"
    # unknown extension is never sniffed
    r2 = store.publish(PAGE, files={"blob.weird": b"\x00\x01"})
    _, ctype = store.read_file(r2["id"], 1, "blob.weird")
    assert ctype == "application/octet-stream"


# --- versions ----------------------------------------------------------------

def test_republish_same_id_adds_version_and_keeps_v1(store):
    first = store.publish("<title>One</title>one")
    second = store.publish("<title>Two</title>two", artifact_id=first["id"],
                           label="second pass")
    assert second["id"] == first["id"]
    assert second["url"] == first["url"]
    assert second["version"] == 2

    assert store.read_file(first["id"], 1)[0] == b"<title>One</title>one"
    assert store.read_file(first["id"], 2)[0] == b"<title>Two</title>two"
    meta = store.get_meta(first["id"])
    assert meta["current"] == 2
    assert [v["n"] for v in meta["versions"]] == [1, 2]
    assert meta["versions"][1]["label"] == "second pass"
    assert meta["versions"][0]["sha256"] != meta["versions"][1]["sha256"]


def test_rollback_moves_pointer_only(store):
    a = store.publish("one")
    store.publish("two", artifact_id=a["id"])
    meta = store.set_current(a["id"], 1)
    assert meta["current"] == 1
    assert store.read_file(a["id"], 2)[0] == b"two"   # v2 still on disk
    with pytest.raises(artifact.ArtifactError):
        store.set_current(a["id"], 9)


def test_unknown_version_rejected(store):
    a = store.publish(PAGE)
    with pytest.raises(artifact.ArtifactError):
        store.read_file(a["id"], 2)


# --- caps --------------------------------------------------------------------

def test_oversize_page_rejected(store):
    big = b"<p>" + b"x" * artifact.CAPS["page_bytes"]
    with pytest.raises(artifact.ArtifactError) as e:
        store.publish(big)
    assert "page cap" in str(e.value)
    assert store.list_artifacts() == []          # nothing written


def test_file_count_cap(store):
    ok = {f"f{i}.txt": b"x" for i in range(artifact.CAPS["files"])}
    store.publish(PAGE, files=ok)                 # 255 is fine
    too_many = dict(ok, **{"f255.txt": b"x"})     # the 256th
    with pytest.raises(artifact.ArtifactError) as e:
        store.publish(PAGE, files=too_many)
    assert "file cap" in str(e.value)


def test_binary_file_cap_is_tighter_than_text(store):
    size = artifact.CAPS["binary_bytes"] + 1
    with pytest.raises(artifact.ArtifactError) as e:
        store.publish(PAGE, files={"big.png": b"\x00" * size})
    assert "binary cap" in str(e.value)
    # the same byte count as TEXT is under the 16 MB text cap
    r = store.publish(PAGE, files={"big.txt": b"x" * size})
    assert r["version"] == 1


def test_failed_publish_leaves_no_version_dir(store):
    a = store.publish(PAGE)
    with pytest.raises(artifact.ArtifactError):
        store.publish(PAGE, artifact_id=a["id"],
                      files={"nope.png": b"\x00" * (
                          artifact.CAPS["binary_bytes"] + 1)})
    assert not os.path.exists(
        os.path.join(store.store_root(), a["id"], "v2"))
    assert len(store.get_meta(a["id"])["versions"]) == 1


# --- path safety -------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "../escape.js",
    "a/../../escape.js",
    "/etc/passwd",
    "..",
    "./x.js",
    "dir\\win.js",
    "C:/x.js",
    "",
    "index.html",
    "INDEX.HTML",
])
def test_bad_file_keys_rejected(store, bad):
    with pytest.raises(artifact.ArtifactError):
        store.publish(PAGE, files={bad: b"x"})


def test_read_file_rejects_traversal(store):
    a = store.publish(PAGE, files={"app.js": b"x"})
    for bad in ("../index.html", "../../meta.json", "/etc/passwd"):
        with pytest.raises(artifact.ArtifactError):
            store.read_file(a["id"], 1, bad)


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", ".", "x" * 80, "-x",
                                 "a b", "id/../../etc"])
def test_bad_artifact_ids_rejected(store, bad):
    with pytest.raises(artifact.ArtifactError):
        store.publish(PAGE, artifact_id=bad)


def test_stable_human_id_allowed(store):
    r = store.publish(PAGE, artifact_id="t117-verdict")
    assert r["id"] == "t117-verdict"
    assert store.publish(PAGE, artifact_id="t117-verdict")["version"] == 2


# --- visibility / listing ----------------------------------------------------

def test_visibility_and_can_view(store):
    r = store.publish(PAGE, owner="max@example.com")
    meta = store.get_meta(r["id"])
    assert store.can_view(meta, "max@example.com") is True
    assert store.can_view(meta, "other@example.com") is False
    assert store.can_view(meta, None) is True        # single-user rig
    meta = store.set_visibility(r["id"], "tailnet")
    assert store.can_view(meta, "other@example.com") is True
    with pytest.raises(artifact.ArtifactError):
        store.set_visibility(r["id"], "public")


def test_republish_cannot_silently_unshare(store):
    r = store.publish(PAGE, owner="max@example.com", visibility="tailnet")
    store.publish(PAGE, artifact_id=r["id"])          # default "private"
    assert store.get_meta(r["id"])["visibility"] == "tailnet"
    store.set_visibility(r["id"], "private")          # explicit narrowing
    assert store.get_meta(r["id"])["visibility"] == "private"


def test_list_filters_and_orders(store):
    a = store.publish("<title>A</title>a", owner="max@example.com")
    b = store.publish("<title>B</title>b", owner="kid@example.com")
    ids = [r["id"] for r in store.list_artifacts()]
    assert set(ids) == {a["id"], b["id"]}
    assert ids[0] == b["id"]                           # newest first
    assert [r["id"] for r in store.list_artifacts(owner="max@example.com")] \
        == [a["id"]]
    assert [r["id"] for r in store.list_artifacts(viewer="kid@example.com")] \
        == [b["id"]]
    assert len(store.list_artifacts(limit=1)) == 1


def test_remove(store):
    a = store.publish(PAGE)
    assert store.remove(a["id"]) is True
    assert store.get_meta(a["id"]) is None
    assert store.remove(a["id"]) is False


def test_get_meta_missing_is_none(store):
    assert store.get_meta("00000000-0000-4000-8000-000000000000") is None


# --- html helpers ------------------------------------------------------------

def test_extract_title_first_8kb_only(store):
    assert store.extract_title("<title> Spaced   out </title>") == "Spaced out"
    assert store.extract_title("<p>no title</p>") is None
    assert store.extract_title("<title>A &amp; B</title>") == "A & B"
    buried = "<!-- " + "x" * 8200 + " --><title>Too late</title>"
    assert store.extract_title(buried) is None
    early = "<title>In time</title>" + "x" * 20000
    assert store.extract_title(early) == "In time"


def test_wrap_skeleton_fragment(store):
    out = store.wrap_skeleton("<p>hi</p>").decode()
    assert out.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in out
    assert "width=device-width" in out
    assert "color-scheme:light dark" in out
    assert "<p>hi</p>" in out
    assert out.rstrip().endswith("</html>")
    assert "data-theme" not in out                     # unstamped by default
    assert 'data-theme="dark"' in store.wrap_skeleton("<p>hi</p>",
                                                      theme="dark").decode()


def test_wrap_skeleton_passes_full_document_through(store):
    doc = "<!doctype html><html><head><title>T</title></head><body>x</body></html>"
    assert store.wrap_skeleton(doc).decode() == doc
    stamped = store.wrap_skeleton(doc, theme="light").decode()
    assert stamped.count("<!doctype html>") == 1
    assert 'data-theme="light"' in stamped


def test_artifact_url(store, monkeypatch):
    a = store.publish(PAGE)
    assert store.artifact_url(a["id"], 3) == \
        f"https://beast:8446/a/{a['id']}/v/3"
    monkeypatch.delenv("OPENBEAST_ARTIFACT_BASE_URL")
    assert store.artifact_url(a["id"]).startswith("https://")
    assert ":8446/a/" in store.artifact_url(a["id"])
