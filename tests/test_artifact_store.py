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
import subprocess
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
    # No allowlist in the environment: every publish here is owned by the
    # "local" principal default_owner() falls back to (security model D3).
    monkeypatch.delenv("OPENBEAST_ARTIFACT_OPERATORS", raising=False)
    monkeypatch.delenv("OPENBEAST_CHAT_OPERATORS", raising=False)
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
    """The version directory is written BEFORE meta.json, so the failure that
    matters is one after the bytes are on disk. (This test used to trip a cap,
    which is checked before the directory is ever created — it asserted the
    absence of something that was never made.)"""
    a = store.publish(PAGE)
    v2 = os.path.join(store.store_root(), a["id"], "v2")

    def boom(aid, meta):
        assert os.path.isfile(os.path.join(v2, "index.html")), \
            "the crash must land AFTER the version bytes are written"
        raise OSError("disk full")

    real_write_meta = artifact._write_meta
    artifact._write_meta = boom          # NOT monkeypatch: undo() would also
    try:                                 # revert the fixture's env patches
        with pytest.raises(OSError):
            store.publish("two", artifact_id=a["id"])
    finally:
        artifact._write_meta = real_write_meta
    assert not os.path.exists(v2)                      # swept on the way out
    assert len(store.get_meta(a["id"])["versions"]) == 1
    # and the id is not wedged: v2 is still publishable
    assert store.publish("two", artifact_id=a["id"])["version"] == 2
    assert store.read_file(a["id"], 2)[0] == b"two"


def test_version_cap(store):
    """D15: an id that versions forever is an unbounded disk write from one
    stable URL (the campaign scripts republish into the same id on a loop)."""
    assert artifact.CAPS["versions"] == 200
    a = store.publish("v", artifact_id="capped")
    meta = store.get_meta("capped")
    meta["versions"] = [dict(meta["versions"][0], n=i)
                        for i in range(1, artifact.CAPS["versions"] + 1)]
    artifact._write_meta("capped", meta)
    with pytest.raises(artifact.ArtifactError) as e:
        store.publish("one too many", artifact_id="capped")
    assert "200 version cap" in str(e.value)
    assert not os.path.exists(
        os.path.join(store.store_root(), "capped", "v201"))
    assert a["version"] == 1


# --- crash recovery / corruption ---------------------------------------------

def test_orphan_version_dir_does_not_wedge_the_id(store):
    """D14: a crash between writing vN/ and updating meta.json left a version
    directory no meta entry names. publish() then hit FileExistsError on that
    same number forever — a permanent brick on exactly the stable ids the
    campaign verdicts auto-publish to."""
    a = store.publish("one", artifact_id="t117-verdict")
    v2 = os.path.join(store.store_root(), a["id"], "v2")
    os.makedirs(v2, mode=0o700)
    with open(os.path.join(v2, "index.html"), "wb") as fh:
        fh.write(b"half-written garbage")
    assert store.get_meta(a["id"])["current"] == 1

    r = store.publish("two", artifact_id=a["id"])
    assert r["version"] == 2
    assert store.read_file(a["id"], 2)[0] == b"two"     # the orphan is gone
    assert [v["n"] for v in store.get_meta(a["id"])["versions"]] == [1, 2]


def test_hard_kill_mid_publish_then_republish(store, tmp_path):
    """The same wedge, produced for real: a child process is killed with the
    version directory on disk and meta.json not yet updated."""
    child = tmp_path / "crash.py"
    child.write_text(
        "import os, sys\n"
        f"sys.path.insert(0, {os.path.join(REPO, 'agents')!r})\n"
        "import artifact\n"
        "real = artifact._write_bytes\n"
        "def killer(path, data):\n"
        "    real(path, data)\n"
        "    os._exit(9)          # SIGKILL-equivalent: no cleanup runs\n"
        "artifact._write_bytes = killer\n"
        "artifact.publish('crashing', artifact_id='wedge')\n")
    env = dict(os.environ, OPENBEAST_FILES_DIR=str(tmp_path / "files"))
    rc = subprocess.call([sys.executable, str(child)], env=env)
    assert rc == 9
    assert os.path.isfile(os.path.join(
        store.store_root(), "wedge", "v1", "index.html"))
    assert store.get_meta("wedge") is None              # meta never written

    r = store.publish("recovered", artifact_id="wedge")
    assert r["version"] == 1
    assert store.read_file("wedge", 1)[0] == b"recovered"


def test_corrupt_record_does_not_poison_the_listing(store):
    """D14: one bad meta.json used to raise straight out of list_artifacts,
    i.e. a 500 on the whole gallery — the one page an operator opens when the
    store has gone wrong."""
    good = store.publish("<title>Good</title>ok")
    for name, blob in (("corrupt1", "{not json at all"),
                       ("corrupt2", '"a string, not an object"'),
                       ("corrupt3", '{"id": "corrupt3", "versions": "nope",'
                                    ' "updated_at": 7}')):
        d = os.path.join(store.store_root(), name)
        os.makedirs(d, mode=0o700, exist_ok=True)
        with open(os.path.join(d, "meta.json"), "w") as fh:
            fh.write(blob)
    rows = {r["id"]: r for r in store.list_artifacts()}
    assert good["id"] in rows                    # the listing still answers
    assert "corrupt1" not in rows                # unparseable: skipped
    assert "corrupt2" not in rows                # not an object: skipped
    # a record that IS an object but the wrong shape degrades instead of
    # raising: no version count, no crash on the non-string sort key
    assert rows["corrupt3"]["versions"] == 0 and rows["corrupt3"]["bytes"] == 0
    assert rows["corrupt3"]["visibility"] == "private"
    assert [r["id"] for r in
            store.list_artifacts(owner="local")] == [good["id"]]
    assert [r["id"] for r in
            store.list_artifacts(viewer="max@example.com")] == ["corrupt3"]


def test_read_falls_back_to_the_versions_on_disk(store):
    """D14: version resolution coerces, and when meta's list is unusable it
    falls back to the highest version actually present."""
    a = store.publish("one")
    store.publish("two", artifact_id=a["id"])
    meta = store.get_meta(a["id"])
    meta["versions"] = [{"n": "2"}, None, {"n": "junk"}]     # coercible + junk
    artifact._write_meta(a["id"], meta)
    assert store.read_file(a["id"], 2)[0] == b"two"

    meta["versions"] = {"not": "a list"}                     # wrong shape
    artifact._write_meta(a["id"], meta)
    assert store.read_file(a["id"], 1)[0] == b"one"          # from the vN dirs
    assert store.read_file(a["id"], 2)[0] == b"two"
    with pytest.raises(artifact.ArtifactError):
        store.read_file(a["id"], 3)


# --- cross-process safety ----------------------------------------------------

def _racer(tmp_path, tag, n):
    script = tmp_path / f"racer_{tag}.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {os.path.join(REPO, 'agents')!r})\n"
        "import artifact\n"
        f"time.sleep(0.2)                      # start together\n"
        f"for i in range({n}):\n"
        f"    artifact.publish('{tag}%d' % i, artifact_id='race')\n"
        f"    time.sleep(0.002)\n")
    return subprocess.Popen(
        [sys.executable, str(script)],
        env=dict(os.environ, OPENBEAST_FILES_DIR=str(tmp_path / "files")),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_two_processes_publishing_lose_no_version(store, tmp_path):
    """D13: the store is written by three separate processes in the shipped
    stack, so a threading.Lock guarded nothing. Interleaved read-modify-write
    of meta.json dropped versions and left the id unpublishable."""
    n = 16
    procs = [_racer(tmp_path, "a", n), _racer(tmp_path, "b", n)]
    outs = [p.communicate(timeout=120) for p in procs]
    for p, (_, err) in zip(procs, outs):
        assert p.returncode == 0, err.decode()[-2000:]

    meta = store.get_meta("race")
    nums = sorted(v["n"] for v in meta["versions"])
    assert nums == list(range(1, 2 * n + 1)), "a version was lost"
    assert meta["current"] == 2 * n
    for i in nums:                       # every version is readable
        assert store.read_file("race", i)[0]
    assert sorted(
        d for d in os.listdir(os.path.join(store.store_root(), "race"))
        if d.startswith("v")) == sorted(f"v{i}" for i in nums)


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
    meta = store.set_visibility(r["id"], "tailnet", owner="max@example.com")
    assert store.can_view(meta, "other@example.com") is True
    with pytest.raises(artifact.ArtifactError):
        store.set_visibility(r["id"], "public", owner="max@example.com")


def test_can_view_fails_closed_for_anonymous(store):
    """D2: an unidentified viewer never reads a non-tailnet page. The old
    `viewer_login is None -> True` branch handed every private artifact to
    anyone who simply omitted the login header."""
    owned = store.get_meta(store.publish(PAGE, owner="max@example.com")["id"])
    assert store.can_view(owned, None) is False
    # ...not even a legacy record with no owner at all
    legacy = dict(owned, owner=None)
    assert store.can_view(legacy, None) is False
    assert store.can_view(legacy, "anyone@example.com") is True   # identified
    # tailnet is the one thing that is deliberately open
    assert store.can_view(dict(owned, visibility="tailnet"), None) is True
    assert store.can_view(None, None) is False
    assert store.can_view("not a dict", "max@example.com") is False
    # and the listing honours it: no viewer identity, no private rows
    assert store.list_artifacts(viewer=None) != []   # viewer unset = no filter


def test_republish_never_changes_visibility(store):
    """D5: publish() applies `visibility` on CREATION only. A republish moves
    it in neither direction — set_visibility() is the only path."""
    r = store.publish(PAGE, owner="max@example.com", visibility="tailnet")
    store.publish(PAGE, artifact_id=r["id"], owner="max@example.com")
    assert store.get_meta(r["id"])["visibility"] == "tailnet"   # not narrowed
    store.set_visibility(r["id"], "private", owner="max@example.com")
    assert store.get_meta(r["id"])["visibility"] == "private"
    # and the widening direction is gone too: this is what let a republish
    # re-share a page nobody asked to share
    store.publish(PAGE, artifact_id=r["id"], owner="max@example.com",
                  visibility="tailnet")
    assert store.get_meta(r["id"])["visibility"] == "private"


def test_republish_by_another_owner_is_refused(store):
    """D5 / the live confidentiality hole: a second operator republishing into
    someone else's id could take the page over, flip it to `tailnet` and read
    every earlier PRIVATE version at /a/<id>/v/<n>."""
    r = store.publish("<title>Mine</title>secret", owner="max@example.com",
                      artifact_id="t117-verdict")
    with pytest.raises(artifact.ArtifactError) as e:
        store.publish("<title>Yours</title>pwned", artifact_id="t117-verdict",
                      owner="kid@example.com", visibility="tailnet")
    assert "not your artifact" in str(e.value)
    # nothing moved: no v2, no owner change, still private, v1 intact
    meta = store.get_meta(r["id"])
    assert [v["n"] for v in meta["versions"]] == [1]
    assert meta["owner"] == "max@example.com"
    assert meta["visibility"] == "private"
    assert store.read_file(r["id"], 1)[0] == b"<title>Mine</title>secret"
    assert not os.path.exists(
        os.path.join(store.store_root(), r["id"], "v2"))
    # the owner is still free to publish over his own id
    assert store.publish("v2", artifact_id=r["id"],
                         owner="max@example.com")["version"] == 2


def test_set_visibility_by_another_owner_is_refused(store):
    """set_visibility() is the only path to `tailnet`, so it carries the same
    ownership check the republish hole used to bypass."""
    r = store.publish(PAGE, owner="max@example.com")
    with pytest.raises(artifact.ArtifactError) as e:
        store.set_visibility(r["id"], "tailnet", owner="kid@example.com")
    assert "not your artifact" in str(e.value)
    assert store.get_meta(r["id"])["visibility"] == "private"
    # an unowned legacy record is nobody's, so it stays settable
    legacy = store.publish(PAGE, artifact_id="legacy")
    meta = store.get_meta("legacy")
    meta["owner"] = None
    artifact._write_meta("legacy", meta)
    assert store.set_visibility("legacy", "tailnet",
                                owner="kid@example.com")["visibility"] \
        == "tailnet"
    assert legacy["id"] == "legacy"


def test_default_owner_is_never_none(store, monkeypatch):
    """D3: an ownerless artifact was readable by every operator forever, so
    publish always resolves an owner — "local" on an unconfigured rig."""
    assert store.default_owner() == "local"
    assert store.get_meta(store.publish(PAGE)["id"])["owner"] == "local"
    monkeypatch.setenv("OPENBEAST_CHAT_OPERATORS", " , max@Example.com ,x")
    assert store.default_owner() == "max@example.com"
    monkeypatch.setenv("OPENBEAST_ARTIFACT_OPERATORS", "Art@Example.com")
    assert store.default_owner() == "art@example.com"       # artifact wins
    token = store.set_owner_override("Ctx@Example.com")
    try:
        assert store.default_owner() == "ctx@example.com"   # ContextVar wins
    finally:
        store.reset_owner_override(token)


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


def test_wrap_skeleton_document_detection(store):
    """D19: "already a document" was decided wrongly in both directions."""
    # a real document whose first bytes are a BOM / comment / XML declaration
    doc = "<html><head><title>T</title></head><body>x</body></html>"
    for lead in ("\ufeff", "<!-- (c) 2026 OpenBeast -->\n",
                 "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n",
                 "\ufeff<!-- hi -->\n<!doctype html>\n"):
        out = store.wrap_skeleton(lead + doc).decode()
        assert out == lead + doc, lead      # passed through, not re-wrapped
        assert out.count("<html") == 1, lead
    # the theme stamp lands on the document's own tag, not one in a comment
    stamped = store.wrap_skeleton(
        "<!-- <html> in a comment -->\n" + doc, theme="dark").decode()
    assert stamped.startswith("<!-- <html> in a comment -->")
    assert '<html data-theme="dark">' in stamped

    # ...and a bare doctype on a FRAGMENT is not a document: it has no <html>,
    # so passing it through cost it the charset/viewport skeleton
    for frag in ("<!doctype html><p>hi</p>", "<!DOCTYPE html>\n<p>hi</p>",
                 "<!-- note --><p>hi</p>", "<p>hi</p>"):
        out = store.wrap_skeleton(frag).decode()
        assert out.startswith("<!doctype html>\n<html lang=\"en\">"), frag
        assert '<meta charset="utf-8">' in out, frag
        assert out.rstrip().endswith("</html>"), frag
    assert store.wrap_skeleton("").decode().startswith("<!doctype html>")


def test_artifact_url(store, monkeypatch):
    a = store.publish(PAGE)
    assert store.artifact_url(a["id"], 3) == \
        f"https://beast:8446/a/{a['id']}/v/3"
    monkeypatch.delenv("OPENBEAST_ARTIFACT_BASE_URL")
    assert store.artifact_url(a["id"]).startswith("https://")
    assert ":8446/a/" in store.artifact_url(a["id"])
