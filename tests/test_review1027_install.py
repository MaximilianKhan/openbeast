#!/usr/bin/env python3
"""Review 2026-10-27 — configure-webui.sh fixes (R9-13, R9-14).

Reuses the stub rig from tests/test_webui_default_admin.py (a copy of the
scripts, a private openbeast.conf, stub curl), with a docker stub that RUNS
the script's embedded `docker exec open-webui python3 -c …` code against a
real sqlite file and captures `docker cp` of the system prompt — so these
assert on what landed in WebUI's DB, not on a grep of the script.

Run: pytest tests/test_review1027_install.py
"""

import json
import os
import sqlite3
import textwrap

import pytest

import test_webui_default_admin as _wda

REPO, _write_exec = _wda.REPO, _wda._write_exec
rig = _wda.rig          # the stub-rig fixture, re-exported for pytest

DOCKER_STUB = textwrap.dedent(r'''
    #!/usr/bin/env python3
    """exec [-i] [-e K=V]… open-webui python3 -c CODE runs CODE against
    $STUB_DB; `cp SRC open-webui:/tmp/system-prompt.txt` copies to $STUB_PROMPT."""
    import os, shutil, subprocess, sys
    a = sys.argv[1:]
    if a[:1] == ["cp"] and len(a) == 3 and a[2] == "open-webui:/tmp/system-prompt.txt":
        shutil.copy(a[1], os.environ["STUB_PROMPT"]); sys.exit(0)
    if not a or a[0] != "exec":
        sys.exit(1)
    a, env = a[1:], dict(os.environ)
    while a and a[0].startswith("-"):
        if a[0] == "-e":
            k, _, v = a[1].partition("="); env[k] = v; a = a[2:]
        else:
            a = a[1:]
    if a[:3] != ["open-webui", "python3", "-c"]:
        sys.exit(1)
    code = (a[3].replace("/app/backend/data/webui.db", os.environ["STUB_DB"])
                .replace("/tmp/system-prompt.txt", os.environ["STUB_PROMPT"]))
    sys.exit(subprocess.run([sys.executable, "-c", code], env=env).returncode)
''').lstrip()


@pytest.fixture()
def wr(rig):
    rig.db = rig.tmp / "webui.db"
    rig.prompt = rig.tmp / "prompt.txt"          # never the host's /tmp
    con = sqlite3.connect(rig.db)
    con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value JSON NOT NULL, updated_at BIGINT)")
    con.execute("CREATE TABLE model (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, meta TEXT, "
                "params TEXT, created_at BIGINT, updated_at BIGINT, is_active INTEGER)")
    con.commit()
    con.close()
    _write_exec(rig.bin / "docker", DOCKER_STUB)
    rig.set_state(auth=False, admin_pw="admin")
    # the prompt sources the script reads, as small stand-ins
    (rig.root / "system-prompt.md").write_text("SOUL: serve alongside Max.\n")
    (rig.root / "system-prompt-tools.md").write_text("`bash` — Run any shell command.\n")

    def configure(env_extra=None):
        env = {"STUB_DB": str(rig.db), "STUB_PROMPT": str(rig.prompt)}
        env.update(env_extra or {})
        return rig.run("configure-webui.sh", env_extra=env)
    rig.configure = configure
    return rig


# --- R9-14: the WebUI copy of the prompt tells the model its tool list rules --

def test_no_models_message_points_at_the_real_script(wr):
    # neither the inference endpoint nor WebUI lists a model
    wr.set_state(auth=False, admin_pw="admin", served=[], webui_models=[])
    p = wr.configure()
    assert p.returncode == 0, p.stderr
    assert "No models detected yet" in p.stdout
    assert "./scripts/configure-webui.sh" in p.stdout
    assert "Re-run ./configure-webui.sh" not in p.stdout
    assert "after first chat" not in p.stdout
    assert os.path.exists(os.path.join(REPO, "scripts", "configure-webui.sh"))
