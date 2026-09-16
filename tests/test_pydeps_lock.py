"""The hash-pinned lock: parsing, staleness, and what a bad wheelhouse looks like.

Every test here is OFFLINE and uses a SYNTHETIC lock. That is deliberate:
resolving the real closure needs PyPI, and a test that reaches the network
fails for reasons that have nothing to do with the code — the repo has been
bitten four times today by checks that read the ambient environment instead of
constructing their own case.

What is worth pinning about this module:

  * the parser must read the file pip actually reads. A lock verified in one
    representation and installed from another is two artifacts, and only one
    of them was checked.
  * a requirement with NO hash must be a hard problem, because
    `--require-hashes` refuses the whole file over one unhashed line — a
    silent omission would turn "hash-pinned" into "not installable".
  * a version drift between requirements.txt and the lock must be caught: the
    lock going stale is the normal failure, not tampering.
  * a wheelhouse file the lock does not name must fail the audit. That is
    what a tampered USB stick looks like, and `--no-index` means no index will
    ever contradict it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts", "lib"))

import pydeps_lock as L        # noqa: E402

H1 = "a" * 64
H2 = "b" * 64
H3 = "c" * 64


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return str(path)


GOOD = f"""# a comment
# another

openai==3.9.0 \\
    --hash=sha256:{H1} \\
    --hash=sha256:{H2}
httpx==0.28.1 \\
    --hash=sha256:{H3}
"""


# --------------------------------------------------------------------------
# parsing the file pip reads
# --------------------------------------------------------------------------

def test_parse_reads_pins_and_every_hash(tmp_path):
    p = _write(tmp_path / "l.lock", GOOD)
    pkgs = L.parse(p)
    assert set(pkgs) == {"openai", "httpx"}
    assert pkgs["openai"]["version"] == "3.9.0"
    assert pkgs["openai"]["hashes"] == [H1, H2]
    assert pkgs["httpx"]["hashes"] == [H3]


def test_parse_normalises_names_the_way_pypi_does(tmp_path):
    """`huggingface_hub` and `huggingface-hub` are ONE package. A lock that
    treats them as two silently loses a pin, and the loss looks like a
    missing dependency much later."""
    p = _write(tmp_path / "l.lock",
               f"huggingface_hub==1.31.0 \\\n    --hash=sha256:{H1}\n")
    pkgs = L.parse(p)
    assert "huggingface-hub" in pkgs
    assert L._norm("huggingface_hub") == L._norm("Huggingface.Hub") == "huggingface-hub"


def test_parse_refuses_a_line_it_does_not_understand(tmp_path):
    """Skipping an unknown line is how a lock quietly stops covering
    something. pip would not skip it, so neither may the parser."""
    p = _write(tmp_path / "l.lock", GOOD + "\n-e ./some/editable\n")
    with pytest.raises(L.LockError) as e:
        L.parse(p)
    assert "neither a pin nor a hash" in str(e.value)


def test_parse_refuses_a_hash_with_no_requirement(tmp_path):
    p = _write(tmp_path / "l.lock", f"    --hash=sha256:{H1}\n")
    with pytest.raises(L.LockError):
        L.parse(p)


def test_parse_refuses_a_lock_that_pins_nothing(tmp_path):
    p = _write(tmp_path / "l.lock", "# only comments\n\n")
    with pytest.raises(L.LockError) as e:
        L.parse(p)
    assert "pins nothing" in str(e.value)


def test_a_truncated_hash_is_not_accepted_as_a_hash(tmp_path):
    """64 hex characters or it is not a sha256. A short hash would be a line
    pip rejects, discovered at install time on the box that can least afford
    it."""
    p = _write(tmp_path / "l.lock",
               "openai==3.9.0 \\\n    --hash=sha256:deadbeef\n")
    with pytest.raises(L.LockError):
        L.parse(p)


# --------------------------------------------------------------------------
# verify: staleness and unhashed requirements
# --------------------------------------------------------------------------

def test_verify_accepts_a_lock_that_covers_every_direct_pin(tmp_path):
    lock = _write(tmp_path / "l.lock", GOOD)
    req = _write(tmp_path / "r.txt", "openai==3.9.0\nhttpx==0.28.1\n")
    assert L.verify(lock, [req], []) == []


def test_verify_catches_a_stale_lock(tmp_path):
    """The NORMAL failure: somebody bumps requirements.txt and forgets the
    lock. The message has to say which way round it is, or the reader cannot
    tell whether to regenerate the lock or revert the bump."""
    lock = _write(tmp_path / "l.lock", GOOD)
    req = _write(tmp_path / "r.txt", "openai==3.10.0\nhttpx==0.28.1\n")
    problems = L.verify(lock, [req], [])
    assert len(problems) == 1
    assert "pins 3.10.0" in problems[0] and "lock pins 3.9.0" in problems[0]
    assert "stale" in problems[0]


def test_verify_catches_a_requirement_the_lock_never_pinned(tmp_path):
    lock = _write(tmp_path / "l.lock", GOOD)
    req = _write(tmp_path / "r.txt", "openai==3.9.0\nhttpx==0.28.1\nmcp==2.2.0\n")
    problems = L.verify(lock, [req], [])
    assert any("mcp" in p and "does not pin" in p for p in problems)


def test_verify_catches_an_unhashed_requirement(tmp_path):
    """One unhashed line makes pip refuse the WHOLE file, so this is not a
    cosmetic gap — it is the difference between a lock and a paperweight."""
    lock = _write(tmp_path / "l.lock", "openai==3.9.0\n")
    req = _write(tmp_path / "r.txt", "openai==3.9.0\n")
    problems = L.verify(lock, [req], [])
    assert any("NO hash" in p for p in problems)


def test_verify_catches_a_duplicate_hash(tmp_path):
    lock = _write(tmp_path / "l.lock",
                  f"openai==3.9.0 \\\n    --hash=sha256:{H1} \\\n"
                  f"    --hash=sha256:{H1}\n")
    req = _write(tmp_path / "r.txt", "openai==3.9.0\n")
    assert any("duplicate" in p for p in L.verify(lock, [req], []))


def test_verify_requires_the_extras_bootstrap_installs_by_name(tmp_path):
    """huggingface_hub is installed by bootstrap and is deliberately absent
    from requirements.txt. If the lock does not pin it, a locked install is
    missing the one tool that fetches weights."""
    lock = _write(tmp_path / "l.lock", GOOD)
    req = _write(tmp_path / "r.txt", "openai==3.9.0\nhttpx==0.28.1\n")
    problems = L.verify(lock, [req], ["huggingface_hub"])
    assert any("huggingface_hub" in p for p in problems)


def test_verify_tolerates_an_unpinned_line_in_requirements(tmp_path):
    """requirements.txt may carry a name with no `==` (presence is all that
    can be checked); the lock must still pin it, but no version comparison is
    possible and inventing one would be a false failure."""
    lock = _write(tmp_path / "l.lock", GOOD)
    req = _write(tmp_path / "r.txt", "openai\nhttpx==0.28.1\n")
    assert L.verify(lock, [req], []) == []


def test_verify_reports_a_missing_file_instead_of_raising(tmp_path):
    problems = L.verify(str(tmp_path / "nope.lock"), [], [])
    assert problems and "nope.lock" in problems[0]


# --------------------------------------------------------------------------
# audit: what a bad wheelhouse looks like
# --------------------------------------------------------------------------

def _wheelhouse(tmp_path, contents: dict):
    d = tmp_path / "wh"
    d.mkdir()
    for name, body in contents.items():
        with open(d / name, "wb") as fh:
            fh.write(body)
    return str(d)


def _lock_for(tmp_path, files: dict):
    """A lock naming the sha256 of each given body."""
    import hashlib
    lines = []
    for i, (name, body) in enumerate(sorted(files.items())):
        h = hashlib.sha256(body).hexdigest()
        lines.append(f"pkg{i}==1.0 \\\n    --hash=sha256:{h}")
    return _write(tmp_path / "l.lock", "\n".join(lines) + "\n")


def test_audit_accepts_a_wheelhouse_whose_every_file_is_named(tmp_path):
    files = {"a-1.0-py3-none-any.whl": b"AAA", "b-1.0-py3-none-any.whl": b"BBB"}
    lock = _lock_for(tmp_path, files)
    wh = _wheelhouse(tmp_path, files)
    matched, problems = L.audit_dir(lock, wh)
    assert sorted(matched) == sorted(files)
    assert problems == []


def test_audit_catches_a_tampered_file(tmp_path):
    """One byte. This is the tampered-USB-stick case, and `--no-index` means
    no index will ever contradict what the directory claims."""
    files = {"a-1.0-py3-none-any.whl": b"AAA"}
    lock = _lock_for(tmp_path, files)
    wh = _wheelhouse(tmp_path, {"a-1.0-py3-none-any.whl": b"AAB"})
    matched, problems = L.audit_dir(lock, wh)
    assert matched == []
    assert len(problems) == 1 and "is in no lock entry" in problems[0]


def test_audit_catches_an_extra_file_the_lock_never_named(tmp_path):
    files = {"a-1.0-py3-none-any.whl": b"AAA"}
    lock = _lock_for(tmp_path, files)
    wh = _wheelhouse(tmp_path, dict(files, **{"evil-9-py3-none-any.whl": b"X"}))
    matched, problems = L.audit_dir(lock, wh)
    assert matched == ["a-1.0-py3-none-any.whl"]
    assert any("evil" in p for p in problems)


def test_audit_ignores_dotfiles_and_subdirectories(tmp_path):
    """A `.DS_Store` from a Mac-mounted USB stick is not a supply-chain
    event, and failing on it would train an operator to ignore the audit."""
    files = {"a-1.0-py3-none-any.whl": b"AAA"}
    lock = _lock_for(tmp_path, files)
    wh = _wheelhouse(tmp_path, dict(files, **{".DS_Store": b"junk"}))
    os.mkdir(os.path.join(wh, "subdir"))
    matched, problems = L.audit_dir(lock, wh)
    assert matched == ["a-1.0-py3-none-any.whl"]
    assert problems == []


# --------------------------------------------------------------------------
# rendering: what pip will be handed
# --------------------------------------------------------------------------

def test_render_round_trips_through_the_parser():
    """The generator writes the file; the verifier reads it. If those two
    disagree the lock is unverifiable, which is why `build` round-trips
    before it writes anything."""
    lock = {
        "resolver": "pip 26.2.1", "python": "3.14",
        "requirement_files": ["agents/requirements.txt"],
        "resolved_from": ["huggingface_hub"],
        "packages": [
            {"name": "openai", "version": "3.9.0",
             "files": [{"filename": "a.whl", "sha256": H1},
                       {"filename": "b.tar.gz", "sha256": H2}]},
            {"name": "httpx", "version": "0.28.1",
             "files": [{"filename": "c.whl", "sha256": H3}]},
        ],
    }
    text = L.render(lock)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".lock", delete=False) as fh:
        fh.write(text)
        path = fh.name
    try:
        pkgs = L.parse(path)
    finally:
        os.unlink(path)
    assert set(pkgs) == {"openai", "httpx"}
    assert pkgs["openai"]["hashes"] == [H1, H2]
    # the header must record HOW it was made — a generated file that cannot
    # say what generated it is not reproducible
    assert "pip 26.2.1" in text and "3.14" in text
    assert "Regenerate" in text


def test_render_carries_no_absolute_path():
    """The lock is committed. A resolver banner like `pip 26.2.1 from
    /usr/lib/python3.14/site-packages/pip` would put one machine's filesystem
    layout into the repo — the same leak as zig's std_dir in the L1 artifacts.
    """
    lock = {"resolver": "pip 26.2.1", "python": "3.14",
            "requirement_files": ["agents/requirements.txt"],
            "resolved_from": [],
            "packages": [{"name": "x", "version": "1",
                          "files": [{"filename": "x.whl", "sha256": H1}]}]}
    text = L.render(lock)
    assert "/usr/" not in text and "/home/" not in text


# --------------------------------------------------------------------------
# the committed lock itself
# --------------------------------------------------------------------------

def _repo(*parts):
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), *parts)


def test_the_committed_lock_is_parseable_and_covers_requirements():
    lock, req = _repo("agents", "requirements.lock"), _repo("agents", "requirements.txt")
    if not os.path.exists(lock):
        pytest.skip("no committed lock")
    problems = L.verify(lock, [req], ["huggingface_hub"])
    assert problems == [], problems
    pkgs = L.parse(lock)
    # A closure of four would mean the lock only re-states requirements.txt.
    assert len(pkgs) > 20, len(pkgs)
    # and it must list more than one file per platform-specific package, or
    # the cross-platform claim in its own header is false
    multi = [p for p in pkgs.values() if len(p["hashes"]) > 1]
    assert len(multi) > 5, f"only {len(multi)} packages list multiple files"


def test_the_committed_lock_has_no_machine_specific_path():
    lock = _repo("agents", "requirements.lock")
    if not os.path.exists(lock):
        pytest.skip("no committed lock")
    text = open(lock, encoding="utf-8").read()
    assert "/home/" not in text
    assert os.path.expanduser("~") not in text
