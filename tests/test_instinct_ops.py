#!/usr/bin/env python3
"""beast-instinct's logs: the service keeps per-request HTTP chatter out of its
append-only log (B-instinct-09) and every hydra/instinct/openjev log is under
rotation (A-ops-2)."""
from __future__ import annotations

import shutil

import _instinct_helpers as H


def test_server_quiets_per_request_http_logs():
    """B-instinct-09: httpx logged every engine request at INFO into an
    append-only .run/instinct.log."""
    import logging

    from instinct.server import configure_logging
    configure_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_logrotate_covers_the_hydra_and_instinct_logs(tmp_path):
    """A-ops-2: .run/hydra.log, instinct.log and instinct-scorer.log (and the
    openjev host's logs) are append-only and were never rotated."""
    import fnmatch
    import subprocess
    lr = tmp_path / "repo"
    (lr / "scripts").mkdir(parents=True)
    (lr / ".run").mkdir()
    for f in ("logrotate.sh", "logrotate-openbeast.conf"):
        shutil.copy(H.REPO / "scripts" / f, lr / "scripts" / f)
    out = subprocess.run([str(lr / "scripts" / "logrotate.sh"), "--print-conf"],
                         capture_output=True, text=True, timeout=30).stdout
    pats = [ln.strip().strip('"') for ln in out.splitlines() if ln.strip().startswith('"')]
    for log in ("hydra.log", "instinct.log", "instinct-scorer.log", "instinct-stub.log",
                "openjev.log", "openjev-gate.log"):
        assert any(fnmatch.fnmatch(str(lr / ".run" / log), p) for p in pats), (log, pats)
