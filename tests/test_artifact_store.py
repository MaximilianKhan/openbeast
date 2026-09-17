#!/usr/bin/env python3
"""beast-artifact store (agents/artifact.py) — layout, versions, caps, safety.

Covers:
  - store root is 0700 and created on demand; publish → read round trip
  - republish keeps the id and the URL, adds v2, leaves v1 byte-identical
  - meta.json shape + atomic rewrite; index.jsonl publish log
  - caps: oversize page, 256th file, oversize binary, per-version total
  - path safety: traversal / absolute / backslash / reserved index.html keys
    rejected on the way IN and again on the way OUT
  - visibility, rollback, remove, can_view — every one of them owner-gated
  - the lock is per-artifact, bounded, and never in a reader's way
  - the store's own path is not publishable, by name OR by hardlink
  - the publishing surface's own id is provenance and grants nothing
  - extract_title only looks at the first 8 KB
  - wrap_skeleton wraps a fragment, passes a full document through

Run: pytest tests/test_artifact_store.py
"""
import contextlib
import json
import os
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import artifact  # noqa: E402

PAGE = "<title>Hello</title><p>hi</p>"


@contextlib.contextmanager
def as_user(login, alias=None):
    """Publish as `login` the only way a caller legitimately can.

    `publish(owner=...)` is an assertion the store ignores unless it matches
    the resolved caller (D28), so a test that wants a page owned by someone
    else has to BE someone else — which is exactly the constraint the server
    and the tool host live under.
    """
    token = artifact.set_owner_override(login, alias)
    try:
        yield
    finally:
        artifact.reset_owner_override(token)


def _child(tmp_path, name, body):
    """Write a helper script that talks to the same store and return its path."""
    script = tmp_path / name
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {os.path.join(REPO, 'agents')!r})\n"
        "import artifact\n" + body)
    return script


def _spawn(tmp_path, script):
    return subprocess.Popen(
        [sys.executable, str(script)],
        env=dict(os.environ, OPENBEAST_FILES_DIR=str(tmp_path / "files")),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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
    campaign verdicts auto-publish to.

    R4 changed the cure from DELETE to STEP OVER. "This directory is debris"
    is a judgement made from meta.json, and a corrupt meta.json makes it
    wrongly about a real version whose bytes are the only copy. So the next
    publish clears every vN directory that exists, the id still un-wedges, and
    nothing that was on disk is destroyed."""
    a = store.publish("one", artifact_id="t117-verdict")
    v2 = os.path.join(store.store_root(), a["id"], "v2")
    os.makedirs(v2, mode=0o700)
    with open(os.path.join(v2, "index.html"), "wb") as fh:
        fh.write(b"half-written garbage")
    assert store.get_meta(a["id"])["current"] == 1

    r = store.publish("two", artifact_id=a["id"])
    assert r["version"] == 3                            # stepped over, not 2
    assert store.read_file(a["id"], 3)[0] == b"two"
    assert [v["n"] for v in store.get_meta(a["id"])["versions"]] == [1, 3]
    assert store.read_file(a["id"], 1)[0] == b"one"     # untouched
    # the debris is not served — meta names it nowhere — and not deleted
    with pytest.raises(artifact.ArtifactError):
        store.read_file(a["id"], 2)
    assert open(os.path.join(v2, "index.html"), "rb").read() \
        == b"half-written garbage"


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
    assert r["version"] == 2                     # v1 is the crash's debris
    assert store.read_file("wedge", 2)[0] == b"recovered"
    assert [v["n"] for v in store.get_meta("wedge")["versions"]] == [2]


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
    # ...and the store's read paths degrade the same way rather than blowing
    # up: an unparseable record is an ArtifactError (the server turns it into
    # a 404/500 of its choosing), never a raw ValueError out of json.
    with pytest.raises(artifact.ArtifactError):
        store.get_meta("corrupt1")
    with pytest.raises(artifact.ArtifactError):
        store.read_file("corrupt1", 1)
    assert store.get_meta("corrupt2") is None
    with pytest.raises(artifact.ArtifactError):
        store.read_file("corrupt3", 1)              # no version resolves
    assert store.read_file(good["id"], 1)[0] == b"<title>Good</title>ok"


def test_republish_under_a_corrupt_version_list_destroys_nothing(store):
    """R4 (silent data loss): `n` came from meta's list alone while every
    reader resolves from meta FALLING BACK TO DISK. A version list corrupted
    into non-numeric entries — the very corruption the two tests either side of
    this one treat as ordinary — made n compute to 1; the orphan
    sweep then rmtree'd the REAL v1 as debris and v2 fell out of the resolver.
    Two versions gone, no error, no way back."""
    a = store.publish("one", artifact_id="verdict")
    store.publish("two", artifact_id=a["id"])
    root = store.store_root()
    meta = store.get_meta(a["id"])
    # the corruption: the list survives, every number in it does not
    meta["versions"] = [{"n": "one"}, {"n": None}]
    artifact._write_meta(a["id"], meta)
    assert artifact._version_numbers(store.get_meta(a["id"])) == []

    r = store.publish("three", artifact_id=a["id"])

    # nothing on disk was destroyed: both real versions are still there, byte
    # for byte, and the new one did not land on top of either. (Recovering
    # them is a meta.json repair — the point is that there is still something
    # to repair.)
    for n, body in ((1, b"one"), (2, b"two"), (3, b"three")):
        p = os.path.join(root, a["id"], f"v{n}", "index.html")
        assert os.path.isfile(p), f"v{n} was DELETED"
        assert open(p, "rb").read() == body, f"v{n} was OVERWRITTEN"
    assert r["version"] == 3
    assert store.read_file(a["id"], 3)[0] == b"three"


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


_HOLD = """
aid = sys.argv[1]
with artifact._artifact_lock(aid):
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def _hold_lock(tmp_path, aid, seconds=5.0):
    """Spawn a process that takes the lock on `aid` and sits on it.

    Returns the Popen once the child confirms it holds the lock. The caller
    kills it BY PID — never by pattern.
    """
    script = _child(tmp_path, f"hold_{aid}.py", _HOLD)
    p = subprocess.Popen(
        [sys.executable, str(script), aid, str(seconds)],
        env=dict(os.environ, OPENBEAST_FILES_DIR=str(tmp_path / "files")),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    line = p.stdout.readline()
    assert line.strip() == b"locked", (line, p.stderr.read()[-2000:])
    return p


def test_a_held_lock_blocks_nothing_but_that_artifact(store, tmp_path):
    """D25: the lock was ONE exclusive flock over the whole store, with no
    timeout, held across fsyncs of up to 64 MB. With it held, a reviewer
    measured /api/artifacts/health and /a/<id> timing out at 8 s — the health
    endpoint doctor.sh and healthcheck.sh --restart depend on.

    So: while a publish holds page A, reads of page A still answer (no read
    path takes the lock at all) and page B still publishes.
    """
    a = store.publish("<title>A</title>locked", artifact_id="held")
    b = store.publish("<title>B</title>other", artifact_id="free")
    holder = _hold_lock(tmp_path, "held", seconds=5.0)
    try:
        t0 = time.monotonic()
        assert store.read_file("held", 1)[0] == b"<title>A</title>locked"
        assert store.get_meta("held")["current"] == 1
        assert {r["id"] for r in store.list_artifacts()} == {"held", "free"}
        assert store.can_view(store.get_meta("held"), "local") is True
        # ...and a mutation of a DIFFERENT artifact is not queued behind it
        assert store.publish("v2", artifact_id="free")["version"] == 2
        assert store.set_description("free", "still mutable")["description"] \
            == "still mutable"
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"a held lock delayed unrelated work ({elapsed}s)"
    finally:
        holder.kill()                       # by pid, never by pattern
        holder.wait(timeout=30)
        holder.stdout.close()
        holder.stderr.close()
    assert a["id"] == "held" and b["id"] == "free"


def test_contended_publish_raises_instead_of_hanging(store, tmp_path,
                                                     monkeypatch):
    """D25: LOCK_NB + a bounded retry. A caller that cannot get in says so —
    the old LOCK_EX with no timeout parked the request (and its anyio worker
    thread) for as long as the holder felt like."""
    monkeypatch.setenv("OPENBEAST_ARTIFACT_LOCK_TIMEOUT", "0.4")
    store.publish("one", artifact_id="busy")
    holder = _hold_lock(tmp_path, "busy", seconds=5.0)
    try:
        t0 = time.monotonic()
        with pytest.raises(artifact.ArtifactError) as e:
            store.publish("two", artifact_id="busy")
        waited = time.monotonic() - t0
        assert "busy" in str(e.value)
        assert waited < 3.0, f"bounded retry took {waited}s"
        assert store.get_meta("busy")["current"] == 1     # nothing half-done
        assert not os.path.exists(
            os.path.join(store.store_root(), "busy", "v2"))
        # reads are still untouched by the contention
        assert store.read_file("busy", 1)[0] == b"one"
    finally:
        holder.kill()
        holder.wait(timeout=30)
        holder.stdout.close()
        holder.stderr.close()
    # and once the holder is gone the id publishes normally again
    assert store.publish("two", artifact_id="busy")["version"] == 2


def test_hard_kill_while_holding_the_lock_leaves_no_wedge(store, tmp_path):
    """The other half of what the flock bought: a SIGKILL'd holder must not
    wedge the id. The kernel drops an flock when the process dies, so the
    leftover .locks/<id>.lock file is just a file."""
    store.publish("one", artifact_id="killme")
    holder = _hold_lock(tmp_path, "killme", seconds=60.0)
    holder.kill()                           # by pid
    holder.wait(timeout=30)
    holder.stdout.close()
    holder.stderr.close()
    assert store.publish("two", artifact_id="killme")["version"] == 2
    assert store.set_current("killme", 1)["current"] == 1
    assert store.remove("killme") is True


def test_lock_files_live_beside_the_artifacts_not_inside_them(store):
    """A lock inside <store>/<id>/ is unlinked by remove()'s rmtree, and a
    lock held on an unlinked inode excludes nobody."""
    store.publish("one", artifact_id="locus")
    assert os.path.isfile(os.path.join(
        store.store_root(), ".locks", "locus.lock"))
    assert not [f for f in os.listdir(
        os.path.join(store.store_root(), "locus")) if f.endswith(".lock")]
    # the lock directory is not an artifact and never shows up as one
    assert "locks" not in {r["id"] for r in store.list_artifacts()}
    assert ".locks" not in {r["id"] for r in store.list_artifacts()}
    store.remove("locus")
    assert os.path.isfile(os.path.join(
        store.store_root(), ".locks", "locus.lock"))


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


@pytest.mark.parametrize("bad", ["index.jsonl", "INDEX.JSONL", "health",
                                 "Health"])
def test_reserved_artifact_ids_are_refused(store, bad):
    """R7: _ID_RE admitted the store's own ledger filename. <root>/index.jsonl
    is a FILE, so makedirs raised NotADirectoryError — an OSError neither
    `except FileExistsError` nor `except ArtifactError` caught, which is a
    server error handed to the page's own owner. And `health` is the server's
    one unauthenticated route."""
    real = store.publish(PAGE)                       # writes the ledger
    ledger = os.path.join(store.store_root(), "index.jsonl")
    before = open(ledger, "rb").read()
    with pytest.raises(artifact.ArtifactError) as e:
        store.publish(PAGE, artifact_id=bad)
    assert "invalid artifact id" in str(e.value)
    for call in (lambda: store.get_meta(bad),
                 lambda: store.read_file(bad, 1),
                 lambda: store.set_visibility(bad, "tailnet"),
                 lambda: store.set_description(bad, "x"),
                 lambda: store.set_current(bad, 1),
                 lambda: store.remove(bad),
                 lambda: store.artifact_url(bad)):
        with pytest.raises(artifact.ArtifactError):
            call()
    assert open(ledger, "rb").read() == before       # the ledger is intact
    assert [r["id"] for r in store.list_artifacts()] == [real["id"]]


def test_stable_human_id_allowed(store):
    r = store.publish(PAGE, artifact_id="t117-verdict")
    assert r["id"] == "t117-verdict"
    assert store.publish(PAGE, artifact_id="t117-verdict")["version"] == 2


# --- visibility / listing ----------------------------------------------------

def test_visibility_and_can_view(store):
    with as_user("max@example.com"):
        r = store.publish(PAGE)
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
    with as_user("max@example.com"):
        owned = store.get_meta(store.publish(PAGE)["id"])
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
    with as_user("max@example.com"):
        r = store.publish(PAGE, visibility="tailnet")
        store.publish(PAGE, artifact_id=r["id"])
        assert store.get_meta(r["id"])["visibility"] == "tailnet"  # not narrowed
        store.set_visibility(r["id"], "private", owner="max@example.com")
        assert store.get_meta(r["id"])["visibility"] == "private"
        # and the widening direction is gone too: this is what let a republish
        # re-share a page nobody asked to share
        store.publish(PAGE, artifact_id=r["id"], visibility="tailnet")
    assert store.get_meta(r["id"])["visibility"] == "private"


def test_republish_by_another_owner_is_refused(store):
    """D5 / the live confidentiality hole: a second operator republishing into
    someone else's id could take the page over, flip it to `tailnet` and read
    every earlier PRIVATE version at /a/<id>/v/<n>."""
    with as_user("max@example.com"):
        r = store.publish("<title>Mine</title>secret",
                          artifact_id="t117-verdict")
    with as_user("kid@example.com"), pytest.raises(artifact.ArtifactError) as e:
        store.publish("<title>Yours</title>pwned", artifact_id="t117-verdict",
                      visibility="tailnet")
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
    with as_user("max@example.com"):
        assert store.publish("v2", artifact_id=r["id"])["version"] == 2


def test_set_visibility_by_another_owner_is_refused(store):
    """set_visibility() is the only path to `tailnet`, so it carries the same
    ownership check the republish hole used to bypass."""
    with as_user("max@example.com"):
        r = store.publish(PAGE)
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
    with as_user("max@example.com"):
        a = store.publish("<title>A</title>a")
    with as_user("kid@example.com"):
        b = store.publish("<title>B</title>b")
    ids = [r["id"] for r in store.list_artifacts()]
    assert set(ids) == {a["id"], b["id"]}
    assert ids[0] == b["id"]                           # newest first
    assert [r["id"] for r in store.list_artifacts(owner="max@example.com")] \
        == [a["id"]]
    assert [r["id"] for r in store.list_artifacts(viewer="kid@example.com")] \
        == [b["id"]]
    assert len(store.list_artifacts(limit=1)) == 1


def test_remove(store):
    """The owner may remove; a second operator may not (D22).

    This test used to assert only that removal WORKS — never who may do it —
    which is precisely how `remove()` shipped as the one mutation with no
    ownership check at all: a second operator holding the locality token
    could destroy another owner's page and every version it ever had.
    """
    with as_user("max@example.com"):
        a = store.publish(PAGE)
    with as_user("kid@example.com"), pytest.raises(artifact.ArtifactError) as e:
        store.remove(a["id"])
    assert "not your artifact" in str(e.value)
    # nothing was deleted, and the page still reads
    assert store.get_meta(a["id"])["owner"] == "max@example.com"
    assert store.read_file(a["id"], 1)[0] == PAGE.encode()
    assert os.path.isdir(os.path.join(store.store_root(), a["id"]))
    # an explicit owner= is the server's path, and it is gated the same way
    with pytest.raises(artifact.ArtifactError):
        store.remove(a["id"], owner="kid@example.com")

    with as_user("max@example.com"):
        assert store.remove(a["id"]) is True
        assert store.get_meta(a["id"]) is None
        assert store.remove(a["id"]) is False       # gone stays gone
    # an unowned legacy record is nobody's, so anyone identified may remove it
    legacy = store.publish(PAGE, artifact_id="legacy-del")
    meta = store.get_meta("legacy-del")
    meta["owner"] = None
    artifact._write_meta("legacy-del", meta)
    with as_user("kid@example.com"):
        assert store.remove(legacy["id"]) is True


def test_list_owner_filter_is_case_insensitive(store):
    """D29: owners are stored lowercased, and the filter compared raw — so a
    caller who spelled their own login the way their identity provider does
    (Max@Example.com) got an empty gallery."""
    with as_user("Max@Example.COM"):
        a = store.publish("<title>A</title>a")
    assert store.get_meta(a["id"])["owner"] == "max@example.com"
    for spelling in ("Max@Example.COM", "max@example.com", " MAX@example.com "):
        assert [r["id"] for r in store.list_artifacts(owner=spelling)] \
            == [a["id"]], spelling
    assert store.list_artifacts(owner="someone@example.com") == []


# --- D28: the owner kwarg is not an identity ---------------------------------

def test_publish_owner_kwarg_cannot_forge_attribution(store):
    """D28: D4 deleted `owner` from the HTTP body, but the store kwarg stayed
    — an attribution-forging primitive for every in-process caller. It is an
    assertion now: honoured when it matches the resolved caller, ignored
    otherwise, and it never mints a page in someone else's name."""
    r = store.publish(PAGE, owner="victim@example.com")
    assert store.get_meta(r["id"])["owner"] == "local"       # the real caller
    assert store.can_view(store.get_meta(r["id"]), "victim@example.com") is False
    # ...and it cannot be used to dodge the republish guard either
    with as_user("max@example.com"):
        mine = store.publish("<title>Mine</title>x", artifact_id="attrib")
    with pytest.raises(artifact.ArtifactError):
        store.publish("pwned", artifact_id="attrib", owner="max@example.com")
    assert store.get_meta(mine["id"])["owner"] == "max@example.com"
    assert store.read_file("attrib", 1)[0] == b"<title>Mine</title>x"
    # agreeing with the caller is fine — that is the server's shape
    with as_user("max@example.com"):
        assert store.publish("v2", artifact_id="attrib",
                             owner="MAX@example.com")["version"] == 2


# --- D22: every mutator is owner-gated ---------------------------------------

def test_set_description_by_another_owner_is_refused(store):
    """D22: round one gated publish and visibility and left description open,
    so a second operator could rewrite the gallery text on anyone's page."""
    with as_user("max@example.com"):
        r = store.publish(PAGE)
        store.set_description(r["id"], "mine")
    with pytest.raises(artifact.ArtifactError) as e:
        store.set_description(r["id"], "defaced", owner="kid@example.com")
    assert "not your artifact" in str(e.value)
    with as_user("kid@example.com"), pytest.raises(artifact.ArtifactError):
        store.set_description(r["id"], "defaced")
    assert store.get_meta(r["id"])["description"] == "mine"
    with as_user("max@example.com"):
        assert store.set_description(r["id"], "still mine")["description"] \
            == "still mine"


def test_set_current_by_another_owner_is_refused(store):
    """D22: an ungated rollback silently serves an OLDER page at the URL the
    owner believes is current — a deface that leaves no trace in the version
    list."""
    with as_user("max@example.com"):
        r = store.publish("v1 body")
        store.publish("v2 body", artifact_id=r["id"])
    assert store.get_meta(r["id"])["current"] == 2
    with pytest.raises(artifact.ArtifactError) as e:
        store.set_current(r["id"], 1, owner="kid@example.com")
    assert "not your artifact" in str(e.value)
    with as_user("kid@example.com"), pytest.raises(artifact.ArtifactError):
        store.set_current(r["id"], 1)
    assert store.get_meta(r["id"])["current"] == 2           # not rolled back
    with as_user("max@example.com"):
        assert store.set_current(r["id"], 1)["current"] == 1


def test_mutators_leave_an_unowned_legacy_record_open(store):
    """The legacy branch is deliberate and must stay: a record with no
    recorded identity is nobody's, so an identified caller may still manage
    it. Anything WITH an owner is owner-only."""
    store.publish(PAGE, artifact_id="legacy2")
    meta = store.get_meta("legacy2")
    meta["owner"] = None
    artifact._write_meta("legacy2", meta)
    with as_user("kid@example.com"):
        assert store.set_description("legacy2", "hi")["description"] == "hi"
        assert store.set_current("legacy2", 1)["current"] == 1


# --- R6: the publishing surface's id is PROVENANCE, never a credential -------

def test_the_publishing_surfaces_id_grants_nothing(store):
    """R6: `owner_webui_id` used to be flattened into the same string set as
    the login a reader presents, so the Open WebUI id AUTHENTICATED — on a rig
    with no operator allowlist a stranger who presented the UUID as their
    login read the private page, and could manage and delete it. It is
    recorded, and consulted by nothing."""
    with as_user("max@example.com", "3f1c-uuid-9a2b"):
        r = store.publish(PAGE)
    meta = store.get_meta(r["id"])
    assert meta["owner"] == "max@example.com"            # the reader's namespace
    assert meta["owner_webui_id"] == "3f1c-uuid-9a2b"    # provenance, kept

    assert store.can_view(meta, "max@example.com") is True
    assert store.can_view(meta, "3f1c-uuid-9a2b") is False   # NOT a login
    assert store.can_view(meta, "kid@example.com") is False
    assert store.can_view(meta, None) is False

    for call in (lambda: store.set_visibility(r["id"], "tailnet",
                                              owner="3f1c-uuid-9a2b"),
                 lambda: store.set_description(r["id"], "d",
                                               owner="3f1c-uuid-9a2b"),
                 lambda: store.set_current(r["id"], 1,
                                           owner="3f1c-uuid-9a2b"),
                 lambda: store.remove(r["id"], owner="3f1c-uuid-9a2b")):
        with pytest.raises(artifact.ArtifactError) as e:
            call()
        assert "not your artifact" in str(e.value)
    with as_user("3f1c-uuid-9a2b"), pytest.raises(artifact.ArtifactError):
        store.publish("v2", artifact_id=r["id"])

    # the listing does not answer to it either
    assert [x["id"] for x in
            store.list_artifacts(owner="max@example.com")] == [r["id"]]
    assert store.list_artifacts(owner="3f1c-uuid-9a2b") == []
    assert store.list_artifacts(viewer="3f1c-uuid-9a2b") == []
    # ...and the page is still private, still whole, still its owner's
    assert store.get_meta(r["id"])["visibility"] == "private"
    assert store.read_file(r["id"], 1)[0] == PAGE.encode()


def test_publish_owner_alias_kwarg_cannot_name_a_third_party(store):
    """R6: `owner_alias=` was D28's forging primitive reborn — round two
    accepted any alias from any caller, so an in-process caller could plant a
    page carrying a named third party's id. Like `owner=`, it is an assertion:
    only the resolved caller's own alias is ever recorded."""
    with as_user("max@example.com", "3f1c-uuid-9a2b"):
        mine = store.publish(PAGE, owner_alias="victims-uuid")
    assert store.get_meta(mine["id"])["owner_webui_id"] == "3f1c-uuid-9a2b"
    # with no alias in context there is nothing to record, whatever is asserted
    plain = store.publish(PAGE, owner_alias="victims-uuid")
    assert store.get_meta(plain["id"])["owner_webui_id"] is None


def test_owner_override_alias_does_not_leak(store):
    token = store.set_owner_override("max@example.com", "3f1c-uuid")
    assert store.default_owner() == "max@example.com"
    assert store.default_owner_alias() == "3f1c-uuid"
    store.reset_owner_override(token)
    assert store.default_owner() == "local"
    assert store.default_owner_alias() == ""
    assert store.get_meta(store.publish(PAGE)["id"])["owner_webui_id"] is None


# --- D26: the store is not a publishable source ------------------------------

def test_is_store_path_refuses_the_store_and_everything_in_it(store, tmp_path,
                                                              monkeypatch):
    """D26: the guard that says "the page must come from your workspace" is
    satisfied BY THE STORE ITSELF whenever no per-user shard is set
    ($OPENBEAST_FILES_DIR/artifacts lives inside $OPENBEAST_FILES_DIR), so a
    second user published another user's private page by naming the store's
    own internal path — and then re-shared it as tailnet."""
    with as_user("max@example.com"):
        r = store.publish("<title>Private</title>secret")
    root = store.store_root()
    page = os.path.join(root, r["id"], "v1", "index.html")
    assert os.path.isfile(page)

    assert store.is_store_path(root) is True             # the store itself
    assert store.is_store_path(page) is True             # a page inside it
    assert store.is_store_path(os.path.join(root, "meta-does-not-exist")) \
        is True                                          # even if absent
    assert store.is_store_path(os.path.join(root, "index.jsonl")) is True
    assert store.is_store_path(os.path.join(root, ".locks")) is True

    # a relative path: resolved against the working directory first, so
    # "artifacts/<id>/v1/index.html" from the workspace is the same file
    monkeypatch.chdir(os.path.dirname(root))
    assert store.is_store_path(os.path.join("artifacts", r["id"], "v1",
                                            "index.html")) is True
    assert store.is_store_path("./artifacts") is True
    assert store.is_store_path(os.path.join("artifacts", "..", "artifacts")) \
        is True

    # a symlinked PARENT: the link is anywhere on the path, the target is the
    # store all the same
    link = tmp_path / "shortcut"
    os.symlink(root, link)
    assert store.is_store_path(str(link)) is True
    assert store.is_store_path(os.path.join(str(link), r["id"], "v1",
                                            "index.html")) is True
    deep = tmp_path / "files" / "deep"
    os.symlink(os.path.dirname(root), deep)
    assert store.is_store_path(os.path.join(str(deep), "artifacts",
                                            r["id"])) is True

    # a path that resolves INTO the store from outside it: the classic bypass,
    # a symlink sitting in the caller's own workspace
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    bait = outside / "innocent.html"
    os.symlink(page, bait)
    assert store.is_store_path(str(bait)) is True
    assert store.is_store_path(str(outside / "real.html")) is False

    # ...and it does not over-refuse: ordinary workspace files are publishable
    (tmp_path / "files" / "page.html").write_text("<p>hi</p>")
    assert store.is_store_path(str(tmp_path / "files" / "page.html")) is False
    assert store.is_store_path(str(tmp_path / "files")) is False
    assert store.is_store_path(root + "-not-the-store") is False
    assert store.is_store_path("") is False
    assert store.is_store_path(None) is False
    assert store.is_store_path("/etc/passwd") is False
    assert store.is_store_path("x\x00y") is False        # NUL: no exception


def test_a_hardlink_into_the_store_is_not_publishable(store, tmp_path):
    """R3: realpath resolves symlinks; a HARDLINK has nothing to resolve. With
    FILES_SHARDING off the store lives inside the workspace, so
    `ln <store>/<id>/v1/index.html loot.html` put another user's private page
    at a path that passed every guard — the reviewer who did it published it
    as tailnet and a third operator read it."""
    with as_user("max@example.com"):
        r = store.publish("<title>Private</title>secret")
    page = os.path.join(store.store_root(), r["id"], "v1", "index.html")
    ws = tmp_path / "files"
    loot = ws / "loot.html"
    os.link(page, loot)                              # the attack, verbatim

    assert os.path.realpath(str(loot)) == str(loot)  # nothing to resolve
    assert open(loot, "rb").read() == b"<title>Private</title>secret"
    assert store.is_store_path(str(loot)) is True    # ...and it is refused

    # a page the caller just wrote has exactly one link, so honest publishing
    # is untouched
    own = ws / "mine.html"
    own.write_text("<p>hi</p>")
    assert os.stat(own).st_nlink == 1
    assert store.is_store_path(str(own)) is False
    # and a second link to it is refused too: "how many links" is the only
    # question answerable without walking the whole store per publish, and it
    # errs toward refusing
    os.link(own, ws / "mine-again.html")
    assert store.is_store_path(str(own)) is True
    # directories carry many links and are judged by path alone, as before
    assert store.is_store_path(str(ws)) is False


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


SERVE_STATUS = """https://beast.tail4109f9.ts.net (tailnet only)
|-- / proxy http://127.0.0.1:3000

https://beast.tail4109f9.ts.net:8446 (tailnet only)
|-- / proxy http://127.0.0.1:3004

https://beast.tail4109f9.ts.net:8443 (tailnet only)
|-- / proxy http://127.0.0.1:8080
"""


def _fresh_base_url(store, monkeypatch, status):
    """The test BUILDS its own tailscale: the real one is never asked, so the
    result cannot depend on what this box happens to publish."""
    calls = []

    def fake():
        calls.append(1)
        return status

    monkeypatch.delenv("OPENBEAST_ARTIFACT_BASE_URL", raising=False)
    monkeypatch.setattr(store, "_serve_status", fake)
    monkeypatch.setitem(store._BASE_URL_CACHE, "value", "")
    return calls


def test_artifact_url(store, monkeypatch):
    a = store.publish(PAGE)
    assert store.artifact_url(a["id"], 3) == \
        f"https://beast:8446/a/{a['id']}/v/3"


def test_the_default_url_is_the_name_tailscale_actually_serves(store, monkeypatch):
    """It was https://<gethostname()>:8446 — and the tailnet machine name is
    chosen independently of the OS hostname, with a certificate for the full
    ts.net name only. On the rig this was written on that is `omarchy` vs
    `beast.tail4109f9.ts.net`: every URL the model handed out was dead."""
    a = store.publish(PAGE)
    calls = _fresh_base_url(store, monkeypatch, SERVE_STATUS)
    assert store.artifact_url(a["id"]) == \
        f"https://beast.tail4109f9.ts.net:8446/a/{a['id']}"
    # cached: a gallery listing builds one URL per row
    store.artifact_url(a["id"]); store.artifact_url(a["id"], 2)
    assert len(calls) == 1
    # an explicit base always wins over detection
    monkeypatch.setenv("OPENBEAST_ARTIFACT_BASE_URL", "https://proxy.example/x/")
    assert store.artifact_url(a["id"]) == f"https://proxy.example/x/a/{a['id']}"


@pytest.mark.parametrize("status", [
    "",                                             # no tailscale at all
    "No serve config\n",
    SERVE_STATUS.replace(":8446", ":8447"),          # published, but not us
    "https://evil.example:84460 (tailnet only)\n",  # a longer port is not 8446
])
def test_an_unpublished_viewer_gets_the_loopback_url(store, monkeypatch, status):
    """Negative control: nothing on :8446 means the viewer is loopback-only,
    and the honest URL says so instead of naming a port nothing listens on."""
    a = store.publish(PAGE)
    _fresh_base_url(store, monkeypatch, status)
    monkeypatch.setenv("OPENBEAST_ARTIFACT_PORT", "3999")
    assert store.artifact_url(a["id"]) == f"http://localhost:3999/a/{a['id']}"


def test_serve_status_never_raises_and_never_hangs(store, monkeypatch):
    import subprocess as sp

    def boom(*a, **kw):
        raise sp.TimeoutExpired(cmd="tailscale", timeout=3)
    monkeypatch.setattr(store.subprocess, "run", boom)
    assert store._serve_status() == ""
    monkeypatch.setattr(store.subprocess, "run",
                        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    assert store._serve_status() == ""


def test_the_lock_table_drains(store):
    """One Lock per uuid4, kept forever, is a leak in a server that publishes
    all day. An entry lives only while somebody holds or waits on it."""
    for _ in range(25):
        store.publish(PAGE)
    assert store._ID_LOCKS == {}
    with store._artifact_lock("held"):
        assert "held" in store._ID_LOCKS       # negative control: it IS used
    assert store._ID_LOCKS == {}
