#!/usr/bin/env python3
"""beast-artifact tool surface (agents/mcp_server.py) — the model-facing half.

The store and the HTTP server have their own suites; this one covers the two
tools the MODEL calls, where the attacker in the threat model is the model
itself:

  - publish_artifact reads through tools.read_file's guards (D7): no
    /proc,/sys,/dev, no FIFO wedge, no non-regular file, size before read —
    plus the containment those four do not give: /etc/passwd is a regular
    file under the cap, so the page must come from the caller's workspace
  - it returns a string for every failure, including a NUL byte in the path
    (which used to raise ValueError straight through the tool server as a 500)
  - it refuses to publish when BEAST_ARTIFACT is off (D8)
  - list_artifacts passes a viewer, so it cannot enumerate another operator's
    private artifacts (D6)

Run: pytest tests/test_artifact_mcp_tools.py
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import artifact  # noqa: E402
import mcp_server  # noqa: E402

PAGE = "<title>Hello</title><p>hi</p>"


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """An enabled beast-artifact with its store in a temp dir."""
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OPENBEAST_ARTIFACT_BASE_URL", "https://beast:8446")
    monkeypatch.setenv("BEAST_ARTIFACT", "true")
    monkeypatch.delenv("OPENBEAST_BEAST_ARTIFACT", raising=False)
    monkeypatch.setenv("OPENBEAST_ARTIFACT_OPERATORS", "max@example.com")
    # The tool publishes out of the caller's WORKSPACE (tools._base_dir()),
    # which is what write_file writes to — see _read_artifact_page.
    ws = tmp_path / "files"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    return ws


def _page(ws, name="page.html", body=PAGE):
    p = ws / name
    p.write_text(body)
    return str(p)


# --- D7: the read runs under tools.read_file's guards ------------------------

def test_publish_refuses_pseudo_filesystem_paths(rig):
    """The whole machine is NOT publishable. A reviewer turned /etc/passwd and
    /proc/self/maps into durable URLs through the old open().read()."""
    for path in ("/proc/self/maps", "/proc/version", "/sys/kernel/vmcoreinfo",
                 "/dev/zero"):
        out = mcp_server.publish_artifact(path)
        assert isinstance(out, str)
        assert out.startswith("Error:"), (path, out)
        assert "pseudo-filesystem" in out, (path, out)
        assert "http" not in out.lower() or "Published" not in out


def test_publish_refuses_a_named_pipe(rig):
    """A FIFO blocks open() forever without O_NONBLOCK — one tool call wedged
    a worker thread of the tool server for good."""
    fifo = rig / "wedge.html"
    os.mkfifo(str(fifo))
    out = mcp_server.publish_artifact(str(fifo))
    assert isinstance(out, str)
    assert out.startswith("Error:")
    assert "regular file" in out


def test_publish_refuses_a_directory(rig):
    d = rig / "adir"
    d.mkdir()
    out = mcp_server.publish_artifact(str(d))
    assert out.startswith("Error:")
    assert "regular file" in out or "cannot read" in out


def test_publish_refuses_a_page_over_the_cap(rig, monkeypatch):
    """Size is checked from fstat BEFORE any bytes are read."""
    monkeypatch.setitem(artifact.CAPS, "page_bytes", 1024)
    big = rig / "big.html"
    big.write_text("<p>" + "x" * 4096 + "</p>")
    out = mcp_server.publish_artifact(str(big))
    assert out.startswith("Error:")
    assert "page cap" in out


def test_publish_refuses_a_file_outside_the_workspace(rig, tmp_path):
    """The four guards ported from read_file are necessary, not sufficient:
    /etc/passwd is a regular file under the cap and published cleanly with all
    four in place. Publishing mints a durable URL, so the page must come from
    the caller's own workspace."""
    out = mcp_server.publish_artifact("/etc/passwd")
    assert out.startswith("Error:"), out
    assert "outside" in out and "workspace" in out
    assert artifact.list_artifacts(viewer="max@example.com") == []
    outsider = tmp_path / "elsewhere.html"
    outsider.write_text(PAGE)
    assert mcp_server.publish_artifact(str(outsider)).startswith("Error:")


def test_publish_refuses_a_symlink_out_of_the_workspace(rig):
    """The path is realpath'd first, so a symlink is not a way around it."""
    link = rig / "innocent.html"
    os.symlink("/etc/passwd", str(link))
    out = mcp_server.publish_artifact(str(link))
    assert out.startswith("Error:")
    assert "workspace" in out


def test_publish_returns_a_string_on_a_null_byte(rig):
    """A NUL in the path raises ValueError from os.open/realpath. The tool
    contract is 'always a string' — this used to surface as a 500."""
    out = mcp_server.publish_artifact("/tmp/evil\x00.html")
    assert isinstance(out, str)
    assert out.startswith("Error:")


def test_publish_returns_a_string_for_a_missing_file(rig):
    out = mcp_server.publish_artifact(str(rig / "nope.html"))
    assert isinstance(out, str)
    assert out.startswith("Error:")


def test_publish_still_works_for_a_real_page(rig):
    """The guards must not break the happy path."""
    out = mcp_server.publish_artifact(_page(rig), title="A page")
    assert out.startswith('Published "A page"'), out
    assert "https://beast:8446/a/" in out


# --- D8: no confident URL to nothing -----------------------------------------

def test_publish_refuses_when_the_feature_is_off(rig, monkeypatch):
    monkeypatch.setenv("BEAST_ARTIFACT", "false")
    path = _page(rig)
    out = mcp_server.publish_artifact(path)
    assert out.startswith("Error:")
    assert "BEAST_ARTIFACT=true" in out
    # And it wrote nothing: the store must still be empty.
    assert artifact.list_artifacts(viewer="max@example.com") == []


def test_publish_refuses_when_the_flag_is_unset(rig, monkeypatch):
    monkeypatch.delenv("BEAST_ARTIFACT", raising=False)
    out = mcp_server.publish_artifact(_page(rig))
    assert out.startswith("Error:")
    assert "BEAST_ARTIFACT=true" in out


# --- D6: the listing tool passes a viewer ------------------------------------

def test_list_artifacts_passes_the_viewer(rig, monkeypatch):
    """Without viewer=, the gallery tool enumerated every operator's private
    artifacts to whoever called it."""
    seen = {}
    real = artifact.list_artifacts

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(artifact, "list_artifacts", spy)
    mcp_server.list_artifacts(limit=5)
    assert "viewer" in seen, "list_artifacts was called without a viewer"
    assert seen["viewer"] == artifact.default_owner()
    assert seen["limit"] == 5


def test_list_artifacts_hides_another_owners_private_page(rig):
    mine = artifact.publish(PAGE, title="Mine", owner="max@example.com")
    artifact.publish(PAGE, title="Theirs", owner="someone@example.com")
    out = mcp_server.list_artifacts()
    assert isinstance(out, str)
    assert "Mine" in out or mine["id"] in out
    assert "Theirs" not in out
