"""beast-lang phase 3 — the synthesis harness (agents/lang/synthesize.py).

What must hold, each with its negative control:

  ORDERING      only a candidate the REAL verifier calls VERIFIED reaches
                staging; junk, a non-break, a duplicate and a hostile snippet
                do not — and the shipped claim files are byte-identical after.
  HOSTILE       a refused snippet never reaches a process; a snippet that
                NAMES A FILE is never opened.
  NOT SERVED    staging is invisible to verify.load_claims (so to every pack).
  PROMOTE       explicit, re-verified, all-or-nothing, rebuilds the index.
  NO ENDPOINT   the HTTP client is never constructed without
                OPENBEAST_LANG_SYNTH_URL; urlopen is a tripwire in every test.
  THE LEASE     HELD by someone else blocks, FREE permits, unknown blocks,
                our own `gpu-lease.sh run` ancestor permits.

Every case is BUILT here: the model is a stub that records its prompts, the
corpus and the shipped claim set are written into tmp_path, gpu-lease.sh is a
stub script. The one real toolchain is the python driver — the interpreter
running this file, so present on every box. The C++ case stubs `drivers._run`
(the only place a process starts) rather than asking whether g++ is installed.
"""
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

from lang import drivers as D        # noqa: E402
from lang import escalate as E       # noqa: E402
from lang import packs as P          # noqa: E402
from lang import synthesize as S     # noqa: E402
from lang import verify as V         # noqa: E402

REAL_CLAIMS = os.path.join(ROOT, "agents", "lang", "claims")


def _tree_digest(path):
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(path):
        dirs.sort()
        for n in sorted(files):
            if n.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, n)
            h.update(os.path.relpath(full, path).encode() + b"\0")
            with open(full, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def _real_claim(cid):
    """Snippets of a claim the repo ALREADY ships as verified."""
    doc = json.load(open(os.path.join(REAL_CLAIMS, "python-3.14.json")))
    return next(c for c in doc["claims"] if c["id"] == cid)


_MAKETRANS = _real_claim("string-maketrans-gone")
#: (a) good: an existing verified claim's own snippets, absent from the tmp set
GOOD = {"id": "maketrans", "topic": "str translation",
        "summary": _MAKETRANS["summary"], "since": "3.0",
        "old": _MAKETRANS["old"], "new": _MAKETRANS["new"]}
#: (c) the old form still resolves on every python: NOT_A_BREAK
NOT_A_BREAK = {"id": "getcwd-moved", "topic": "cwd",
               "summary": "`os.getcwd` moved to `pathlib` (it did not)",
               "old": ["import os\nf = os.getcwd\n"],
               "new": ["import pathlib\nf = pathlib.Path.cwd\n"]}
#: (d) hostile: importing unittest.__main__ RUNS test discovery
HOSTILE_PY = {"id": "evil", "topic": "unittest main",
              "summary": "`unittest.__main__` is how you run tests",
              "old": ["import string\nt = string.maketrans\n"],
              "new": ["import unittest.__main__\n"]}
#: (e) the claim the tmp shipped set already holds, reworded
SHIPPED = {"id": "unittest-aliases-removed", "topic": "unittest",
           "old": ["import unittest\nf = unittest.TestCase.assertEquals\n"],
           "new": ["import unittest\nf = unittest.TestCase.assertEqual\n"],
           "summary": "`assertEquals` is gone — use `assertEqual`"}
DUPLICATE = {"id": "assert-equals", "topic": "Unittest",
             "summary": "the alias `assertEquals` was removed; `assertEqual` remains",
             "old": ["import unittest\nx = unittest.TestCase.assertEquals\n"],
             "new": ["import unittest\nx = unittest.TestCase.assertEqual\n"]}


def _reply(*records):
    return json.dumps({"claims": list(records)})


@pytest.fixture(autouse=True)
def _no_network_and_nothing_real_changes(monkeypatch):
    """Tripwire + negative control for the whole file: no test may open a
    socket, and none may alter the claim files the repo ships."""
    def boom(*a, **k):
        raise AssertionError("a test reached urllib.request.urlopen")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.delenv(S.ENV_URL, raising=False)
    before = _tree_digest(REAL_CLAIMS)
    yield
    assert _tree_digest(REAL_CLAIMS) == before, "a test wrote into agents/lang/claims"


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    claims = tmp_path / "claims"
    claims.mkdir()
    (claims / "python-shipped.json").write_text(
        json.dumps({"lang": "python", "claims": [SHIPPED]}, indent=2))
    monkeypatch.setattr(P, "CLAIMS_DIR", str(claims))
    # verify() remembers verdicts per (toolchain, snippet text). A test that
    # asserts "the compiler WAS asked" must not inherit an answer from the
    # test before it — that passed alone and failed in the full file.
    monkeypatch.setattr(V, "_VERDICTS", {})
    lib = tmp_path / "library"
    (lib / "python").mkdir(parents=True)
    (lib / "python" / "whatsnew-3.99.txt").write_text(
        "What's new\n\n" + "\n\n".join(
            f"Paragraph {i}: string.maketrans was removed; use str.maketrans. " * 3
            for i in range(12)))
    monkeypatch.setenv("OPENBEAST_LANG_DIR", str(lib))
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    rebuilt = []
    monkeypatch.setattr(S, "_rebuild_index", rebuilt.append)

    class Rig:
        pass
    r = Rig()
    r.claims, r.lib, r.rebuilt, r.tmp = claims, lib, rebuilt, tmp_path
    r.sections = lambda: S.corpus_sections("python")[0]
    r.staging = lambda: sorted((claims / "staging").glob("*.json")) \
        if (claims / "staging").is_dir() else []
    return r


# --- the ordering: only VERIFIED reaches staging ------------------------------

def test_only_the_verified_candidate_is_staged(rig):
    shipped_before = (rig.claims / "python-shipped.json").read_bytes()
    junk = ("Sure! Here are the claims you asked for:\n```json\n"
            + '{"claims": [' + json.dumps(GOOD) + ', {"id": "half", "topic": "x", '
            '"summary": "trailing comma", "new": ["import os\\n"],}, ]}\n```\n'
            'and one more: {"id": "cut-off", "new": ["import o')
    client = S.StubClient([junk, _reply(NOT_A_BREAK, HOSTILE_PY, DUPLICATE,
                                        {"topic": "no summary", "old": ["import imp\n"],
                                         "new": ["import importlib\n"]},
                                        "not even an object")])
    rep = S.draft_run("python", client, rig.sections(), max_prompts=2,
                      max_prompt_chars=2600, today="20260917")
    assert len(client.prompts) == 2
    c = rep["counts"]
    assert c[S.VERIFIED] == 1 and c[S.NOT_A_BREAK] == 1
    assert c[S.REFUSED] == 1 and c[S.DUPLICATE] == 1
    # the trailing-comma record, the cut-off record, no-summary, the string
    assert c[S.MALFORMED] == 4, rep
    by_id = {x["id"]: x for x in rep["candidates"] if x.get("id")}
    assert "repeats unittest-aliases-removed" in by_id["assert-equals"]["reason"]
    assert "refused" in by_id["evil"]["reason"]
    assert "still compiles" in by_id["getcwd-moved"]["reason"]

    files = rig.staging()
    assert [f.name for f in files] == ["python-20260917.json"]
    staged = json.loads(files[0].read_text())
    assert [x["id"] for x in staged["claims"]] == ["maketrans"]      # ONLY (a)
    # exactly what the model said — nothing repaired, nothing added but
    # provenance
    assert staged["claims"][0]["old"] == GOOD["old"]
    assert staged["claims"][0]["summary"] == GOOD["summary"]
    assert staged["claims"][0]["source"] == "whatsnew-3.99.txt"
    # negative control: the shipped set is byte-identical
    assert (rig.claims / "python-shipped.json").read_bytes() == shipped_before
    assert sorted(p.name for p in rig.claims.iterdir()) == ["python-shipped.json", "staging"]


def test_staging_is_invisible_to_the_serving_path(rig):
    S.draft_run("python", S.StubClient([_reply(GOOD)]), rig.sections(), max_prompts=1)
    assert rig.staging(), "control: something was staged"
    ids = {c.id for c in V.load_claims(P.CLAIMS_DIR)}
    assert ids == {"unittest-aliases-removed"}
    # ...but the NEXT run knows about it, so a re-draft is a duplicate
    rep = S.draft_run("python", S.StubClient([_reply(GOOD)]), rig.sections(), max_prompts=1)
    assert rep["counts"][S.DUPLICATE] == 1 and rep["counts"][S.VERIFIED] == 0


def test_a_drafted_claim_must_show_what_stopped_working(rig):
    """verify() calls a claim with no OLD form VERIFIED. A model asserting a
    break with no evidence of one must not get in on that."""
    no_old = dict(GOOD, id="no-old", old=[])
    rep = S.draft_run("python", S.StubClient([_reply(no_old)]), rig.sections(), max_prompts=1)
    assert rep["counts"][S.MALFORMED] == 1 and not rig.staging()
    assert "no OLD form" in rep["candidates"][0]["reason"]


# --- hostile input ------------------------------------------------------------

def test_a_hostile_python_import_never_reaches_the_resolver(rig, monkeypatch):
    asked = []
    real = D.PythonDriver._resolve

    def spy(self, jobs):
        asked.extend(jobs)
        return real(self, jobs)
    monkeypatch.setattr(D.PythonDriver, "_resolve", spy)
    rep = S.draft_run("python", S.StubClient([_reply(HOSTILE_PY, GOOD)]),
                      rig.sections(), max_prompts=1)
    assert rep["counts"][S.REFUSED] == 1 and rep["counts"][S.VERIFIED] == 1
    assert asked, "control: the good claim WAS resolved"
    assert not any("__main__" in json.dumps(j) for j in asked)


def test_a_hostile_include_never_reaches_a_process(rig, monkeypatch):
    """`#include "/etc/passwd"` — the compiler would READ it and quote it back
    in a diagnostic. drivers._run is the one place a process starts, so it is
    the stub: refused means it was never called for that source."""
    ran = []

    def fake_run(argv, cwd=None, env=None, stdin=None):
        src = ""
        if cwd and os.path.exists(os.path.join(cwd, "claim.cpp")):
            src = open(os.path.join(cwd, "claim.cpp")).read()
        ran.append((argv, src))
        # "c++17 fails, c++20 compiles" — enough for an availability claim
        return D.Result("-std=c++17" not in argv, "stub", " ".join(argv))
    monkeypatch.setattr(D, "_run", fake_run)
    cpp = D.DRIVERS["cpp"]
    monkeypatch.setattr(type(cpp), "available", lambda self: True)
    monkeypatch.setattr(type(cpp), "version", lambda self: "g++ (stub) 1.0")
    (rig.lib / "cpp").mkdir()
    (rig.lib / "cpp" / "changes.html").write_text(
        "<html><style>p{}</style><body>" + "<p>ranges arrived in C++20 &amp; more</p>" * 20
        + "</body></html>")
    hostile = {"id": "leak", "topic": "headers", "summary": "`#include` a header",
               "old_variant": "c++17", "new_variant": "c++20",
               "new": ['#include "/etc/passwd"\nint main(){ return 0; }\n']}
    benign = {"id": "ranges", "topic": "std::ranges", "summary": "`std::ranges::sort` is C++20",
              "old_variant": "c++17", "new_variant": "c++20",
              "new": ["#include <algorithm>\nint main(){ return 0; }\n"]}
    client = S.StubClient([_reply(hostile, benign)])
    rep = S.draft_run("cpp", client, S.corpus_sections("cpp")[0], max_prompts=1)
    assert "<p>" not in client.prompts[0] and "ranges arrived in C++20 & more" in client.prompts[0]
    outcomes = {c["id"]: c["outcome"] for c in rep["candidates"]}
    assert outcomes == {"leak": S.REFUSED, "ranges": S.VERIFIED}, rep
    assert ran, "control: the benign claim DID reach the (stubbed) compiler"
    assert not any("/etc/passwd" in src for _, src in ran)
    staged = json.loads(rig.staging()[0].read_text())
    assert [c["id"] for c in staged["claims"]] == ["ranges"]


def test_a_snippet_that_names_a_file_is_never_opened(rig, monkeypatch):
    """verify.Claim reads a one-line snippet ending in .py as a FIXTURE PATH.
    For a drafted claim that is a file-read primitive."""
    secret = rig.tmp / "secret.py"
    secret.write_text("import os\n")           # would even "compile"
    judged = []
    real = V.verify
    monkeypatch.setattr(V, "verify", lambda c: (judged.append(c.id), real(c))[1])
    path_claim = dict(GOOD, id="reads-a-file", new=[str(secret)])
    rep = S.draft_run("python", S.StubClient([_reply(path_claim, GOOD)]),
                      rig.sections(), max_prompts=1)
    out = {c["id"]: c for c in rep["candidates"]}
    assert out["reads-a-file"]["outcome"] == S.REFUSED
    assert "names a FILE" in out["reads-a-file"]["reason"]
    assert judged == ["maketrans"]             # the path claim never got that far
    # the belt under it: even built directly, a Claim cannot read the file
    built = S._as_claim(dict(path_claim, lang="python"), str(rig.tmp))
    assert built.new == [str(secret) + "\n"]   # the PATH as text, not the file


@pytest.mark.parametrize("bad,why", [
    (dict(GOOD, lang="zig"), "drafted for"),
    (dict(GOOD, old_variant="c++17", new_variant="c++20"), "no language-level axis"),
    (dict(GOOD, new=["x"] * 9), "at most"),
    (dict(GOOD, new=["a" * 5000]), "over"),
    (dict(GOOD, summary="x" * 400), "one line"),
    (dict(GOOD, topic=""), "topic"),
    (dict(GOOD, new="import os"), "must be a list"),
])
def test_wrong_shapes_are_dropped_with_a_reason(bad, why):
    with pytest.raises(ValueError, match=why):
        S.validate(bad, "python")


def test_a_variant_is_not_a_place_for_arbitrary_text():
    bad = {"id": "v", "topic": "t", "summary": "s", "new": ["int main(){}\n"],
           "old_variant": "c++17 -include /etc/passwd", "new_variant": "c++20"}
    with pytest.raises(ValueError, match="bad variant"):
        S.validate(bad, "cpp")


# --- parsing ------------------------------------------------------------------

def test_junk_is_counted_and_never_repaired():
    rec = json.dumps(GOOD)
    assert S.extract_records("I found nothing worth drafting.") == ([], 1)
    assert S.extract_records("") == ([], 0)
    assert S.extract_records('{"claims": []}') == ([], 0)
    assert S.extract_records('{"claims": "none"}') == ([], 1)
    # prose + fence around a good envelope
    got, bad = S.extract_records(f"Here you go:\n```json\n{{\"claims\": [{rec}]}}\n```\nDone!")
    assert got == [GOOD] and bad == 0
    # a trailing comma INSIDE a record: that record is lost, not fixed
    got, bad = S.extract_records('{"claims": [' + rec + ', {"id": "b", "new": ["x"],}]}')
    assert got == [GOOD] and bad == 1
    # cut off mid-record: whole records before the cut survive
    got, bad = S.extract_records('{"claims": [' + rec + ', {"id": "c", "new": ["impo')
    assert got == [GOOD] and bad == 1
    # braces inside a snippet do not end the object
    tricky = dict(GOOD, new=['d = {"a": "}"}\n'])
    assert S.extract_records(_reply(tricky))[0] == [tricky]
    # what a reasoning model THOUGHT is not what it SAID
    assert S.extract_records('<think>{"claims": [' + rec + ']}</think>{"claims": []}') == ([], 0)


# --- budgets ------------------------------------------------------------------

def test_prompts_are_bounded_and_candidates_are_capped(rig, monkeypatch):
    sections = rig.sections()
    plans = S.plan_prompts("python", "Python 3.99", sections, ["a generated fact"] * 50,
                           ["known: claim"] * 200, max_prompts=3, max_chars=2600)
    assert len(plans) == 3
    assert all(len(p["prompt"]) <= 2600 for p in plans)
    assert all("Documentation excerpt" in p["prompt"] for p in plans)
    with pytest.raises(S.SynthError):
        S.plan_prompts("python", "v", sections, [], [], max_chars=900)

    judged = []
    real = V.verify
    monkeypatch.setattr(V, "verify", lambda c: (judged.append(c.id), real(c))[1])
    many = [dict(GOOD, id=f"m{i}", topic=f"topic {i}",
                 old=[f"import string\nt{i} = string.maketrans\n"]) for i in range(5)]
    rep = S.draft_run("python", S.StubClient([_reply(*many)]), sections,
                      max_prompts=1, max_candidates=2)
    assert rep["counts"][S.OVER_BUDGET] == 3 and len(judged) == 2


def test_the_corpus_reader_never_unpacks_and_says_what_it_skipped(rig):
    tar_path = rig.lib / "python" / "docs.tar.bz2"
    with tarfile.open(tar_path, "w:bz2") as tf:
        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        add("docs/whatsnew/3.99.txt", b"Removed: the imp module. " * 40)
        add("docs/library/os.txt", b"os docs, not a change log. " * 40)
        link = tarfile.TarInfo("docs/whatsnew/evil.txt")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(link)
    (rig.lib / "python" / "n1.pdf").write_bytes(b"%PDF-1.4")
    sections, notes = S.corpus_sections("python")
    origins = [s["origin"] for s in sections]
    assert "docs.tar.bz2::docs/whatsnew/3.99.txt" in origins
    assert not any("os.txt" in o or "evil" in o for o in origins)
    assert any("n1.pdf" in n and "PDF" in n for n in notes)
    assert not (rig.lib / "python" / "docs").exists()         # nothing unpacked
    # --match replaces the default pattern
    only = S.corpus_sections("python", match=r"library/os")[0]
    assert [s["origin"] for s in only] == ["docs.tar.bz2::docs/library/os.txt"]


def test_no_corpus_means_no_run_and_no_invention(rig, monkeypatch, capsys):
    monkeypatch.setenv("OPENBEAST_LANG_DIR", str(rig.tmp / "empty"))
    client = S.StubClient([_reply(GOOD)])
    rc = S.main(["draft", "python"], client=client, lease_fn=lambda i: (True, "FREE"))
    assert rc == 3 and client.prompts == [] and not rig.staging()
    assert "Nothing is drafted from thin air" in capsys.readouterr().err
    # negative control: the same call WITH a corpus drafts
    monkeypatch.setenv("OPENBEAST_LANG_DIR", str(rig.lib))
    assert S.main(["draft", "python", "--max-prompts", "1"], client=client,
                  lease_fn=lambda i: (True, "FREE")) == 0
    assert len(client.prompts) == 1 and rig.staging()


# --- the endpoint -------------------------------------------------------------

def test_the_http_client_is_never_constructed_without_the_env_var(rig, monkeypatch, capsys):
    built = []
    real_init = S.HTTPClient.__init__

    def spy(self, *a, **k):
        built.append(a)
        real_init(self, *a, **k)
    monkeypatch.setattr(S.HTTPClient, "__init__", spy)
    leased = []
    rc = S.main(["draft", "python"],
                lease_fn=lambda i: (leased.append(i), (True, "FREE"))[1])
    assert rc == 2 and built == []
    assert leased == []            # a typo is found without querying the card
    assert S.ENV_URL in capsys.readouterr().err
    assert not rig.staging()
    # and there is no default anywhere to fall back on
    src = open(S.__file__).read()
    assert "localhost:8080/" not in src and "127.0.0.1:8080" not in src
    # control: WITH the variable it is built, pointed where the operator said
    monkeypatch.setenv(S.ENV_URL, "http://drafter.example:9/v1/")
    c = S.client_from_env()
    assert built and c.url == "http://drafter.example:9/v1/chat/completions"
    with pytest.raises(S.SynthError):
        S.HTTPClient("file:///etc/passwd")


def test_the_http_client_speaks_openai_chat(monkeypatch):
    sent = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent["url"], sent["body"] = req.full_url, json.loads(req.data)
        sent["auth"] = req.get_header("Authorization")
        return Resp(json.dumps({"choices": [{"message": {"content": "DRAFT"}}]}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = S.client_from_env({S.ENV_URL: "http://drafter.example:9/v1", S.ENV_KEY: "k"})
    assert c.draft("PROMPT") == "DRAFT"
    assert sent["url"].endswith("/v1/chat/completions") and sent["auth"] == "Bearer k"
    assert sent["body"]["messages"][-1] == {"role": "user", "content": "PROMPT"}


def test_a_failing_model_call_is_a_note_not_a_crash(rig, capsys):
    class Down:
        def draft(self, prompt):
            raise OSError("connection refused")
    rc = S.main(["draft", "python", "--max-prompts", "2"], client=Down(),
                lease_fn=lambda i: (True, "FREE"))
    assert rc == 5 and not rig.staging()
    assert "every model call failed" in capsys.readouterr().err


def test_dry_run_sends_compiles_and_writes_nothing(rig, monkeypatch, capsys):
    monkeypatch.setattr(V, "verify", lambda c: pytest.fail("dry run verified"))
    client = S.StubClient([_reply(GOOD)])
    leased = []
    rc = S.main(["draft", "python", "--dry-run", "--max-prompts", "2",
                 "--max-prompt-chars", "2600"], client=client,
                lease_fn=lambda i: (leased.append(i), (False, "HELD"))[1])
    out = capsys.readouterr().out
    assert rc == 0 and client.prompts == [] and not rig.staging()
    assert leased == []                        # GPU-free: the lease is not its business
    assert "===== prompt 1/2" in out and "string.maketrans was removed" in out
    assert S.SYSTEM_PROMPT in out              # ALL of what would be sent
    assert "must now FAIL" in out and "Already covered" in out
    assert not (rig.tmp / "run").exists()      # not even a report


# --- the lease ----------------------------------------------------------------

@pytest.mark.parametrize("line,ignore,ok", [
    ("FREE", False, True),
    ("FREE (a stale lease from pid 7 is on disk; it will be taken over)", False, True),
    ("HELD by pid 4242 — T1.17 campaign  (since 2026-09-15)", False, False),
    ("HELD by pid 4242 — T1.17 campaign  (since 2026-09-15)", True, True),
    ("", False, False),                        # unknown is not free
    ("", True, True),
    ("nvidia-smi: command not found", False, False),
])
def test_the_lease_gate(line, ignore, ok):
    got, why = S.check_lease(ignore, status_fn=lambda: line, ancestors_fn=lambda: {10, 11})
    assert got is ok, why


def test_a_lease_held_by_our_own_wrapper_is_ours():
    line = "HELD by pid 11 — beast-lang P3  (since now)"
    assert S.check_lease(False, lambda: line, lambda: {10, 11})[0] is True
    assert S.check_lease(False, lambda: line, lambda: {10, 12})[0] is False
    assert os.getppid() in S._ancestors() and os.getpid() in S._ancestors()


def test_a_held_lease_blocks_the_cli_before_anything_is_sent(rig, monkeypatch, capsys):
    """The real subprocess path, against a STUB gpu-lease.sh that records that
    it was asked — never the real one, which would query the card."""
    fake_repo = rig.tmp / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    asked = fake_repo / "asked"
    (fake_repo / "scripts" / "gpu-lease.sh").write_text(
        f'#!/bin/bash\necho "$1" >> "{asked}"\n'
        'echo "HELD by pid 1 — the campaign  (since forever)"\necho "  GPU: 31000 MiB in use"\n')
    monkeypatch.setattr(S, "_REPO", str(fake_repo))
    client = S.StubClient([_reply(GOOD)])
    rc = S.main(["draft", "python"], client=client)
    assert rc == 4 and client.prompts == [] and not rig.staging()
    assert asked.read_text().split() == ["status"]
    assert "HELD by pid 1" in capsys.readouterr().err
    # --ignore-lease is the operator saying they looked
    assert S.main(["draft", "python", "--max-prompts", "1", "--ignore-lease"],
                  client=client) == 0
    assert len(client.prompts) == 1


# --- promote ------------------------------------------------------------------

def _stage(rig, *records):
    rep = S.draft_run("python", S.StubClient([_reply(*records)]), rig.sections(),
                      max_prompts=1, today="20260917")
    assert rep["staging_file"], rep
    return rep["staging_file"]


def test_promote_moves_reviewed_claims_and_rebuilds_the_index(rig):
    second = dict(GOOD, id="imp-gone", topic="imp",
                  summary="a second claim about `string.maketrans`",
                  old=["import string\nq = string.maketrans\n"])
    path = _stage(rig, GOOD, second)
    rc, msgs = S.promote(path, ["maketrans"])
    assert rc == 0, msgs
    target = rig.claims / "python-synthesized.json"
    assert [c["id"] for c in json.loads(target.read_text())["claims"]] == ["maketrans"]
    assert rig.rebuilt == ["python"]
    # served now — and the hand-authored file was not the one written to
    assert {c.id for c in V.load_claims(P.CLAIMS_DIR)} == {"unittest-aliases-removed", "maketrans"}
    assert json.loads((rig.claims / "python-shipped.json").read_text())["claims"] == [SHIPPED]
    # the unreviewed one is still waiting
    assert [c["id"] for c in json.loads(open(path).read())["claims"]] == ["imp-gone"]
    rc, _ = S.promote(path, None)
    assert rc == 0 and not os.path.exists(path)
    assert len(json.loads(target.read_text())["claims"]) == 2


def test_promote_refuses_a_claim_that_no_longer_verifies(rig):
    path = _stage(rig, GOOD, dict(GOOD, id="other", topic="other",
                                  old=["import string\nz = string.maketrans\n"]))
    doc = json.loads(open(path).read())
    doc["claims"][1]["old"] = ["import os\nf = os.getcwd\n"]     # now NOT a break
    open(path, "w").write(json.dumps(doc))
    before = open(path, "rb").read()
    rc, msgs = S.promote(path, None)
    assert rc == 1 and any("no longer verifies" in m and "other" in m for m in msgs)
    # ALL or nothing: the good one did not slip through beside it
    assert not (rig.claims / "python-synthesized.json").exists()
    assert open(path, "rb").read() == before and rig.rebuilt == []
    assert "nothing was promoted" in msgs[-1]


def test_promote_refuses_hand_edits_that_break_the_rules(rig):
    path = _stage(rig, GOOD)
    doc = json.loads(open(path).read())
    doc["claims"][0]["summary"] = ""                  # undeliverable
    open(path, "w").write(json.dumps(doc))
    assert S.promote(path, None)[0] == 1
    doc["claims"][0].update(summary="s", new=["import unittest.__main__\n"])
    open(path, "w").write(json.dumps(doc))
    rc, msgs = S.promote(path, None)
    assert rc == 1 and "refused" in " ".join(msgs)
    # only out of staging: a shipped file is not a staging file
    rc, msgs = S.promote(str(rig.claims / "python-shipped.json"), None)
    assert rc == 1 and "not in" in msgs[0]
    assert S.main(["promote", path]) == 2             # --all or --id, explicitly


def test_status_lists_what_awaits_review(rig, capsys):
    assert S.main(["status"]) == 0
    assert "nothing staged" in capsys.readouterr().out
    _stage(rig, GOOD)
    assert S.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "python-20260917.json: 1 claim(s)" in out and "maketrans" in out


# --- the index rebuild promote relies on --------------------------------------

def test_write_index_keeps_the_languages_it_did_not_rebuild(tmp_path, monkeypatch):
    """`--rebuild --lang python` used to json.dump the one-language result
    over the whole file: zig and cpp were deleted from the index."""
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"_comment": "old", "langs": {
        "zig": {"toolchain": "0.16.0", "signatures": {"ident:io": ["stdout"]}},
        "python": {"toolchain": "3.0", "signatures": {}}}}))
    monkeypatch.setattr(E, "INDEX_PATH", str(idx))
    E.write_index({"_comment": "new", "langs": {"python": {"toolchain": "3.99"}}})
    got = json.loads(idx.read_text())
    assert got["langs"]["python"] == {"toolchain": "3.99"}             # replaced
    assert got["langs"]["zig"]["signatures"] == {"ident:io": ["stdout"]}  # KEPT
    assert got["_comment"] == "new" and list(got["langs"]) == ["python", "zig"]
