#!/usr/bin/env python3
"""scripts/artifact.sh against a REAL server (tests/_artifact_live.py).

tests/test_artifact_cli.sh deliberately starts nothing and covers argument
handling; this file covers what only a live round trip shows — each case
failed on origin/main 7699117:

  correctness-05  a republish reports the page's EFFECTIVE visibility, and a
                  --visibility that did not apply is said out loud
  F-A1            publishing with no operator configured warns that the phone
                  cannot open the page yet
  correctness-06  `list` says "Showing N of M" instead of silently truncating
                  to 25, and --all / --limit reach everything
  correctness-07  a low-byte .bin travels base64 (by extension), so it is not
                  inflated 6x into the server's body gate
  F-A2            pin / tag / chown / prune / remove --version
  F-A3            OPENBEAST_SESSION_ID is stamped as the source session

Run: pytest tests/test_artifact_cli_live.py
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "agents"))

CLI = os.path.join(REPO, "scripts", "artifact.sh")

pytestmark = pytest.mark.skipif(shutil.which("curl") is None,
                                reason="curl is required by scripts/artifact.sh")


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    sandbox = tmp_path / "repo"
    (sandbox / ".run").mkdir(parents=True)
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(sandbox / ".run"))
    monkeypatch.setenv("OPENBEAST_ARTIFACT_BASE_URL", "https://beast:8446")
    monkeypatch.setenv("OPENBEAST_CONF", str(tmp_path / "absent.conf"))
    monkeypatch.setenv("OPENBEAST_CHAT_BASE_URL", "off")
    for var in ("OPENBEAST_ARTIFACT_OPERATORS", "OPENBEAST_CHAT_OPERATORS",
                "OPENBEAST_ARTIFACT_ADMINS", "OPENBEAST_BIND",
                "OPENBEAST_SESSION_ID", "OPENBEAST_ARTIFACT_RETAIN_DAYS"):
        monkeypatch.delenv(var, raising=False)
    import _artifact_live
    import artifact
    srv = _artifact_live.LiveServer()
    with srv:
        def run(*args, env=None, check=True):
            e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                 "HOME": str(tmp_path), "REPO_DIR": str(sandbox),
                 "OPENBEAST_ARTIFACT_PORT": str(srv.port),
                 "TMPDIR": str(tmp_path)}
            e.update(env or {})
            p = subprocess.run(["nice", "-n", "19", "bash", CLI, *args],
                               env=e, capture_output=True, text=True,
                               timeout=60)
            if check and p.returncode != 0:
                raise AssertionError(f"{args} -> {p.returncode}\n"
                                     f"{p.stdout}\n{p.stderr}")
            return p
        run.tmp = tmp_path
        run.store = artifact
        run.srv = srv
        yield run


def _page(tmp_path, name="p.html", body="<title>Q3 report</title><p>x</p>"):
    path = tmp_path / name
    path.write_text(body)
    return str(path)


def _id(out: str) -> str:
    for line in out.splitlines():
        if line.strip().startswith("id:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(out)


def test_publish_reports_visibility_and_the_no_operator_warning(rig):
    p = rig("publish", _page(rig.tmp))
    assert "visibility: private" in p.stdout
    assert "No operator is configured" in p.stderr
    assert "ARTIFACT_OPERATORS=" in p.stderr
    aid = _id(p.stdout)
    # correctness-05: asked for tailnet on a republish, got private — say so
    p = rig("publish", _page(rig.tmp), "--id", aid, "--visibility", "tailnet")
    assert "visibility: private" in p.stdout
    assert "visibility unchanged (private)" in p.stderr
    # not asking is not a request, so no "unchanged" line
    p = rig("publish", _page(rig.tmp), "--id", aid)
    assert "unchanged" not in p.stderr


def test_list_never_truncates_silently(rig):
    for i in range(27):
        rig("publish", _page(rig.tmp, body=f"<title>T{i}</title>x"))
    p = rig("list")
    assert "Showing 25 of 27" in p.stdout
    p = rig("list", "--limit", "5")
    assert "Showing 5 of 27" in p.stdout
    p = rig("list", "--all", "--json")
    doc = json.loads(p.stdout)
    assert doc["count"] == 27 and doc["total"] == 27
    p = rig("list", "--all")
    assert "Showing" not in p.stdout
    import re
    assert sum(1 for line in p.stdout.splitlines()
               if re.search(r"\sT\d+\s", line)) == 27


def test_a_low_byte_binary_is_sent_base64_not_inflated(rig, monkeypatch):
    """correctness-07: the server's body gate is ~1.46x the version cap. A
    .bin of NULs sent as JSON TEXT is 6x its size and was refused as a flat
    404 the CLI blamed on ownership."""
    monkeypatch.setitem(rig.store.CAPS, "version_bytes", 1024 * 1024)
    blob = rig.tmp / "zeros.bin"
    blob.write_bytes(b"\x00" * (400 * 1024))           # 2.4 MB as JSON text
    p = rig("publish", _page(rig.tmp), "--file", f"data.bin={blob}")
    aid = _id(p.stdout)
    data, ctype = rig.store.read_file(aid, 1, "data.bin")
    assert data == b"\x00" * (400 * 1024)
    assert ctype == "application/octet-stream"


def test_lifecycle_verbs(rig):
    aid = _id(rig("publish", _page(rig.tmp)).stdout)
    for _ in range(3):
        rig("publish", _page(rig.tmp), "--id", aid)
    rig("pin", aid)
    rig("tag", aid, "report", "Q3")
    meta = rig.store.get_meta(aid)
    assert meta["pinned"] is True and meta["tags"] == ["report", "q3"]
    p = rig("show", aid)
    assert "pinned:      yes" in p.stdout and "report, q3" in p.stdout
    rig("tag", aid)                                    # no tags = clear
    assert "tags" not in rig.store.get_meta(aid)
    rig("unpin", aid)
    assert rig.store.get_meta(aid).get("pinned") is not True
    # prune: dry run refuses, --yes keeps the newest N and the current one
    p = rig("prune", aid, "--keep", "1", check=False)
    assert p.returncode == 2 and "v1 v2 v3" not in p.stderr
    rig("prune", aid, "--keep", "1", "--yes")
    assert [v["n"] for v in rig.store.get_meta(aid)["versions"]] == [4]
    # remove one version: refused for the current one
    rig("publish", _page(rig.tmp), "--id", aid)
    p = rig("remove", aid, "--version", "5", "--yes", check=False)
    assert p.returncode == 3 and "currently serves" in p.stderr
    rig("remove", aid, "--version", "4", "--yes")
    assert [v["n"] for v in rig.store.get_meta(aid)["versions"]] == [5]
    # chown: the rig is the admin
    rig("chown", aid, "max@example.com")
    assert rig.store.get_meta(aid)["owner"] == "max@example.com"
    p = rig("chown", aid, "not an email", check=False)
    assert p.returncode == 3


def test_the_session_id_is_stamped(rig):
    p = rig("publish", _page(rig.tmp),
            env={"OPENBEAST_SESSION_ID": "agent-20260930-deadbeef"})
    aid = _id(p.stdout)
    assert rig.store.get_meta(aid)["source_session"] == \
        "agent-20260930-deadbeef"
    p = rig("list", "--session", "agent-20260930-deadbeef", "--json")
    assert [r["id"] for r in json.loads(p.stdout)["artifacts"]] == [aid]
