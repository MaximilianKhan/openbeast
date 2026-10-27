#!/usr/bin/env python3
"""Review 2026-10-27 — configure-webui.sh fixes (R8-05, R8-10, R9-13, R9-14).

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

from test_webui_default_admin import CURL_STUB, REPO, _write_exec, rig  # noqa: F401

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
def wr(rig):  # noqa: F811
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

    def seed(rows):
        con = sqlite3.connect(rig.db)
        for k, v in rows.items():
            con.execute("INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, 1)",
                        (k, json.dumps(v)))
        con.commit()
        con.close()
    rig.seed = seed

    def cfg(prefix="web.search."):
        con = sqlite3.connect(rig.db)
        got = {k: json.loads(v) for k, v in
               con.execute("SELECT key, value FROM config WHERE key LIKE ?", (prefix + "%",))}
        con.close()
        return got
    rig.cfg = cfg

    def configure(env_extra=None):
        env = {"STUB_DB": str(rig.db), "STUB_PROMPT": str(rig.prompt)}
        env.update(env_extra or {})
        return rig.run("configure-webui.sh", env_extra=env)
    rig.configure = configure
    return rig


# --- R8-05: built-in Web Search is OFF unless WEBUI_WEB_SEARCH=true ---------

def test_fresh_install_leaves_builtin_web_search_off(wr):
    p = wr.configure()
    assert p.returncode == 0, p.stderr
    got = wr.cfg()
    assert got.get("web.search.enable") is False
    assert "web.search.engine" not in got, "an engine was wired although the feature is off"
    assert "WEBUI_WEB_SEARCH=true" in p.stdout          # the operator is told the opt-in


def test_install_the_old_script_switched_on_is_switched_off(wr):
    wr.seed({"web.search.engine": "searxng", "web.search.enable": True,
             "web.search.searxng_query_url": "http://localhost:8888/search?q=<query>"})
    p = wr.configure()
    assert p.returncode == 0, p.stderr
    got = wr.cfg()
    assert got["web.search.enable"] is False
    assert got["web.search.engine"] == "searxng"        # engine/url left alone
    assert "switched off" in p.stdout


@pytest.mark.parametrize("how", ["conf", "env"])
def test_opt_in_wires_searxng_and_turns_it_on(wr, how):
    env = {}
    if how == "conf":
        with open(wr.conf, "a") as f:
            f.write("WEBUI_WEB_SEARCH=true   # composer toggle\n")
    else:
        env["OPENBEAST_WEBUI_WEB_SEARCH"] = "yes"
    p = wr.configure(env)
    assert p.returncode == 0, p.stderr
    got = wr.cfg()
    assert got["web.search.enable"] is True
    assert got["web.search.engine"] == "searxng"
    assert got["web.search.searxng_query_url"].endswith(":8888/search?q=<query>")


def test_opt_in_keeps_an_engine_the_operator_chose(wr):
    wr.seed({"web.search.engine": "brave", "web.search.enable": False})
    p = wr.configure({"OPENBEAST_WEBUI_WEB_SEARCH": "true"})
    assert p.returncode == 0, p.stderr
    got = wr.cfg()
    assert got["web.search.engine"] == "brave" and got["web.search.enable"] is True
    assert "web.search.searxng_query_url" not in got


def test_web_search_second_run_changes_nothing(wr):
    assert wr.configure().returncode == 0
    p = wr.configure()
    assert p.returncode == 0, p.stderr
    assert "already as configured" in p.stdout


# --- R9-14: the WebUI copy of the prompt tells the model its tool list rules --

def test_webui_prompt_has_the_tier_addendum_and_keeps_the_persona(wr):
    p = wr.configure()
    assert p.returncode == 0, p.stderr
    con = sqlite3.connect(wr.db)
    row = con.execute("SELECT params FROM model WHERE id='m1'").fetchone()
    con.close()
    assert row, p.stdout
    system = json.loads(row[0])["system"]
    assert system.startswith("SOUL: serve alongside Max.")          # persona untouched
    assert "`bash` — Run any shell command." in system              # tool guide kept
    assert "only `web_search` and `fetch`" in system
    assert "ground truth" in system


def test_tier_addendum_never_touches_the_era_files():
    """The addendum lives in configure-webui.sh, not the era-hashed prompts."""
    for f in ("system-prompt.md", "system-prompt-tools.md"):
        with open(os.path.join(REPO, f)) as fh:
            assert "Which tools you actually have here" not in fh.read()


# --- R9-13: the no-models message names a real path and the real trigger ---

def test_no_models_message_points_at_the_real_script(wr):
    stub = CURL_STUB.replace(
        'if path in ("/api/models", "/v1/models"): out({"data": [{"id": "m1"}]})',
        'if path in ("/api/models", "/v1/models"): out({"data": []})')
    assert stub != CURL_STUB
    _write_exec(wr.bin / "curl", stub)
    p = wr.configure()
    assert p.returncode == 0, p.stderr
    assert "No models detected yet" in p.stdout
    assert "./scripts/configure-webui.sh" in p.stdout
    assert "Re-run ./configure-webui.sh" not in p.stdout
    assert "after first chat" not in p.stdout
    assert os.path.exists(os.path.join(REPO, "scripts", "configure-webui.sh"))


# --- R8-10: the RBAC comment counts the privileged tools right ----------------

def test_rbac_comment_counts_16_privileged_tools():
    with open(os.path.join(REPO, "scripts", "configure-webui.sh")) as f:
        src = f.read()
    assert "(16 OS-touching tools)" in src and "(15 OS-touching tools)" not in src
