#!/usr/bin/env python3
"""scripts/lib/conf.sh _ob_conf_value — one read of openbeast.conf per source.

Each lookup used to fork `grep | tail` (~85 per source, 142 of the 320 ms a
source cost on a Spark; 2026-11-16 review). While conf.sh is being sourced
lookups now come from one pass over the file (_ob_conf_snapshot). These
tests pin that the snapshot answers exactly what the grep path answers, that
a write during the source is seen, and that the snapshot does not outlive
the source (a caller that writes the conf later must read the file again).

Run: pytest tests/test_conf_value.py
"""

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONF = (
    "# comment KEY=no\n"
    "  # SPACED=no\n"
    "PLAIN=one\n"
    "  INDENTED  =  two  \n"
    "QUOTED=\"three\"\n"
    "SINGLE='four'\n"
    "HALF=\"five\n"
    "EQUALS=a=b=c\n"
    "EMPTY=\n"
    "TWICE=first\n"
    "TWICE=second\n"
    "PREFIX_KEY=prefixed\n"
    "KEY=plain-key\n"
    "CRLF=win\r\n"
    "TABS=\tx y\t\n"
    "lower=ok\n"
    "LAST=no-newline"
)
KEYS = ["PLAIN", "INDENTED", "QUOTED", "SINGLE", "HALF", "EQUALS", "EMPTY",
        "TWICE", "PREFIX_KEY", "KEY", "CRLF", "TABS", "lower", "LAST",
        "MISSING", "SPACED", "comment"]


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(os.path.join(REPO, "scripts", "lib"), root / "scripts" / "lib")
    (root / "openbeast.conf").write_text(CONF)
    return root


def _bash(repo, script, tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "REPO_DIR": str(repo),
           "OB_CONF_READONLY": "1", "OB_CONF_LINTED": "1",
           "OPENBEAST_RUN_DIR": str(tmp_path / "run"),
           "OPENBEAST_SEARXNG_SECRET": "stub"}
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _lookups(prefix: str) -> str:
    return prefix + "; ".join(
        f'printf "%s|%s|%s\\n" {k} "$(_ob_conf_value {k})" "$?"' for k in KEYS)


def test_the_snapshot_answers_what_grep_answers(repo, tmp_path):
    lib = 'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; '
    via_grep = _bash(repo, _lookups(lib), tmp_path)
    via_snap = _bash(repo, _lookups(lib + "_ob_conf_snapshot; "), tmp_path)
    assert via_snap == via_grep
    got = dict(line.split("|")[:2] for line in via_snap.splitlines())
    assert got["TWICE"] == "second" and got["EQUALS"] == "a=b=c"
    assert got["INDENTED"] == "two" and got["QUOTED"] == "three"
    assert got["KEY"] == "plain-key" and got["CRLF"] == "win"
    assert got["LAST"] == "no-newline" and got["MISSING"] == ""


def test_the_snapshot_is_dropped_when_the_source_ends(repo, tmp_path):
    (repo / "openbeast.conf").write_text("PLAIN=one\n")
    out = _bash(repo, 'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; '
                      'echo "${_OB_CONF_SNAP+set}"; '
                      'echo "PLAIN=changed" >> "$REPO_DIR/openbeast.conf"; '
                      '_ob_conf_value PLAIN', tmp_path)
    assert out.splitlines() == ["", "changed"]


def test_a_source_reads_the_conf_without_forking_per_key(repo, tmp_path):
    """Sourcing conf.sh no longer runs grep once per key: count execs of
    grep through a PATH shim."""
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "grep.log"
    (shim / "grep").write_text(f'#!/bin/sh\necho x >> "{log}"\nexec /usr/bin/grep "$@"\n')
    (shim / "grep").chmod(0o755)
    env = {"PATH": f"{shim}:/usr/bin:/bin", "HOME": str(tmp_path), "REPO_DIR": str(repo),
           "OB_CONF_READONLY": "1", "OB_CONF_LINTED": "1",
           "OPENBEAST_RUN_DIR": str(tmp_path / "run"), "OPENBEAST_SEARXNG_SECRET": "stub"}
    r = subprocess.run(["bash", "-c", 'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null'],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    calls = len(log.read_text().splitlines()) if log.exists() else 0
    assert calls < 10, f"{calls} grep execs for one source"


def test_a_searxng_secret_written_during_the_source_is_read_back(repo, tmp_path):
    (repo / "openbeast.conf").write_text("PLAIN=one\n")
    env_script = ('unset OPENBEAST_SEARXNG_SECRET; OB_CONF_READONLY=; '
                  'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; '
                  'echo "$SEARXNG_SECRET"; _ob_conf_snapshot; _ob_conf_value SEARXNG_SECRET')
    a, b = _bash(repo, env_script, tmp_path).splitlines()
    assert a and a == b
