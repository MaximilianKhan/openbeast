#!/usr/bin/env python3
"""tests/_cdp_pipe.py must hand the browser its pipes on fds 3/4 even when
os.pipe() itself returns 3 and 4 (review B-artifact-5).

Under pytest the process already holds many fds, so the pipe ends land on
high numbers and the bug hid. A bare `python3 -c` gets (3, 4) and (5, 6); the
wrapper's `3<&{cmd_r} ... {cmd_r}<&-` then closed the fd it had just set up
and chrome answered nothing. This drives the helper from exactly such a bare
process with a fake "chrome" that echoes one CDP reply — no browser needed.
"""
import os
import subprocess
import sys
import textwrap

import pytest

TESTS = os.path.dirname(os.path.abspath(__file__))

FAKE_CHROME = r"""#!/bin/bash
# Read one NUL-terminated command from fd 3, answer it on fd 4, then wait for
# the pipe to close (Browser.close / teardown).
IFS= read -r -d '' cmd <&3 || exit 3
id="$(printf '%s' "$cmd" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
printf '{"id": %s, "result": {"echo": "ok"}}\0' "$id" >&4
cat <&3 >/dev/null
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fds")
def test_pipes_reach_fds_3_and_4_from_a_bare_process(tmp_path):
    fake = tmp_path / "fake-chrome"
    fake.write_text(FAKE_CHROME)
    fake.chmod(0o755)
    driver = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {TESTS!r})
        a, b = os.pipe(); os.close(a); os.close(b)
        if (a, b) != (3, 4):              # the collision case must occur
            sys.exit("NO-LOW-FDS")
        import _cdp_pipe
        c = _cdp_pipe.Chrome({str(fake)!r})
        try:
            print(c.send("Probe.echo", timeout=10)["echo"])
        finally:
            os.close(c._cmd_w)
            c.proc.wait(timeout=10)
    """)
    r = subprocess.run([sys.executable, "-c", driver], capture_output=True,
                       text=True, timeout=60, close_fds=True,
                       stdin=subprocess.DEVNULL)
    if "NO-LOW-FDS" in r.stderr:             # this runner leaks extra fds
        pytest.skip("could not reproduce low pipe fds here")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"
