#!/usr/bin/env python3
"""configure-webui.sh + healthcheck.sh — WebUI's stored model connection key.

Open WebUI persists its OpenAI connection (openai.api_base_urls AND
openai.api_keys) in webui.db and ignores the env once the DB has a value.
configure-webui.sh synced only the URL. spark-1's DB kept the "not-needed"
key docker-compose seeded at first boot (GLM, unkeyed); once DeepSeek served
with an engine key every /v1/models the WebUI sent was a 401 and the WebUI
listed no models for anyone. Max hit it from his phone on cellular on
2026-11-24 (docs/reports/REMOTE_ACCESS-2026-11-25.md).

The rig: copies of the scripts, a private openbeast.conf, a real SQLite
webui.db in tmp_path, and a `docker` stub that runs `docker exec ...
open-webui python3 -c CODE` locally against that DB (the container path is
rewritten), feeding stdin through and logging argv. Nothing touches a real
WebUI or container.

Run: pytest tests/test_webui_model_connection.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "http://127.0.0.1:8000/v1"
NAMES = {"urls": "openai.api_base_urls", "keys": "openai.api_keys"}
KEY = "engine-sekrit-0123456789"

DOCKER_STUB = textwrap.dedent(r'''
    #!/usr/bin/env python3
    """docker stub: exec into "open-webui" = run python3 here on $STUB_DB."""
    import json, os, subprocess, sys
    args = sys.argv[1:]
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(json.dumps(args) + "\n")
    if os.environ.get("STUB_DOCKER_DOWN"):
        sys.exit(1)
    if args[:1] == ["inspect"]:
        print("true"); sys.exit(0)
    if args[:1] != ["exec"]:
        sys.exit(1)
    env = dict(os.environ)
    i = 1
    while args[i].startswith("-"):
        if args[i] == "-e":
            k, _, v = args[i + 1].partition("="); env[k] = v; i += 2
        else:
            i += 1
    if args[i] != "open-webui" or args[i + 1:i + 3] != ["python3", "-c"]:
        sys.exit(1)
    code = args[i + 3].replace("/app/backend/data/webui.db", os.environ["STUB_DB"])
    sys.exit(subprocess.run([sys.executable, "-c", code], env=env).returncode)
''').lstrip()


def _write_exec(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)


@pytest.fixture()
def rig(tmp_path):
    root = tmp_path / "rig"
    for rel in ("scripts/configure-webui.sh", "scripts/lib/conf.sh",
                "scripts/lib/net.sh", "scripts/lib/curl_auth.sh"):
        os.makedirs(os.path.dirname(root / rel), exist_ok=True)
        shutil.copy2(os.path.join(REPO, rel), root / rel)
    conf = root / "openbeast.conf"
    conf.write_text("SEARXNG_SECRET=stub\nINFERENCE_BACKEND=tensorfold\nINFERENCE_URL=http://127.0.0.1:8000\n")
    os.chmod(conf, 0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "docker", DOCKER_STUB)
    db = tmp_path / "webui.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER)")
    con.commit()
    con.close()
    log = tmp_path / "docker.jsonl"
    log.write_text("")

    class Rig:
        pass

    r = Rig()
    r.root, r.conf, r.db, r.log = root, conf, db, log

    def seed(**rows):
        con = sqlite3.connect(db)
        con.execute("DELETE FROM config")
        for k, v in rows.items():
            con.execute("INSERT INTO config VALUES (?, ?, 1)", (NAMES[k], json.dumps(v)))
        con.commit()
        con.close()
    r.seed = seed

    def row(k):
        con = sqlite3.connect(db)
        v = con.execute("SELECT value FROM config WHERE key=?", (k,)).fetchone()
        con.close()
        return json.loads(v[0]) if v else None
    r.row = row

    def run(*args, key=KEY, extra=None):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
               "STUB_DB": str(db), "STUB_LOG": str(log),
               }
        if key is not None:
            env["OPENBEAST_API_KEY"] = key
        env.update(extra or {})
        return subprocess.run(["bash", str(root / "scripts" / "configure-webui.sh"), *args],
                              env=env, capture_output=True, text=True, timeout=60)
    r.run = run
    # the URL conf.sh derives from this conf; the tests pin that it is URL
    got = subprocess.run(["bash", "-c", 'REPO_DIR="$1"; source "$1/scripts/lib/conf.sh"; printf %s "$OPENBEAST_MODEL_URL"',
                          "_", str(root)],
                         env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
                         capture_output=True, text=True, timeout=30).stdout
    assert got == URL, got
    return r


def _stale(rig):
    # spark-1 on 2026-11-24: right URL, compose's first-boot placeholder key
    rig.seed(urls=[URL], keys=["not-needed"])


def test_check_reports_the_stale_first_boot_key(rig):
    _stale(rig)
    assert rig.run("--check-connection").returncode == 1
    assert rig.row("openai.api_keys") == ["not-needed"], "a check must not write"


def test_sync_writes_the_engine_key_and_check_then_passes(rig):
    _stale(rig)
    p = rig.run("--sync-connection")
    assert p.returncode == 0, p.stderr
    assert rig.row("openai.api_keys") == [KEY]
    assert rig.row("openai.api_base_urls") == [URL]
    assert rig.run("--check-connection").returncode == 0


def test_sync_is_idempotent(rig):
    rig.seed(urls=[URL], keys=[KEY])
    assert rig.run("--check-connection").returncode == 0
    assert rig.run("--sync-connection").returncode == 0
    assert rig.row("openai.api_keys") == [KEY]


def test_unkeyed_host_gets_compose_placeholder(rig):
    """GLM window: LLAMA_API_KEY empty -> the key compose would seed."""
    rig.seed(urls=[URL], keys=[KEY])
    assert rig.run("--check-connection", key=None).returncode == 1
    assert rig.run("--sync-connection", key=None).returncode == 0
    assert rig.row("openai.api_keys") == ["not-needed"]


def test_stale_url_is_also_fixed(rig):
    rig.seed(urls=["http://localhost:8080/v1"], keys=[KEY])
    assert rig.run("--check-connection").returncode == 1
    assert rig.run("--sync-connection").returncode == 0
    assert rig.row("openai.api_base_urls") == [URL]


def test_missing_rows_are_inserted(rig):
    rig.seed()
    assert rig.run("--sync-connection").returncode == 0
    assert rig.row("openai.api_keys") == [KEY]
    assert rig.row("openai.api_base_urls") == [URL]


def test_key_never_rides_docker_argv(rig):
    _stale(rig)
    rig.run("--sync-connection")
    rig.run("--check-connection")
    calls = [json.loads(x) for x in rig.log.read_text().splitlines() if x.strip()]
    assert calls, "docker was never called"
    assert not [c for c in calls if any(KEY in a for a in c)]


def test_unreachable_container_is_3_not_stale(rig):
    _stale(rig)
    assert rig.run("--check-connection", extra={"STUB_DOCKER_DOWN": "1"}).returncode == 3
    assert rig.run("--sync-connection", extra={"STUB_DOCKER_DOWN": "1"}).returncode == 3


def test_check_mode_never_creates_the_conf(rig):
    rig.conf.unlink()
    _stale(rig)
    rig.run("--check-connection")
    assert not rig.conf.exists()


def test_healthcheck_wires_check_and_sync():
    """The watchdog must look at the connection, not just /api/version."""
    src = open(os.path.join(REPO, "scripts", "healthcheck.sh")).read()
    assert "configure-webui.sh\" --check-connection" in src
    assert "configure-webui.sh\" --sync-connection" in src
    i = src.index("--check-connection")
    assert "_may_restart" in src[i:i + 1500], "the heal must respect _may_restart"
