"""23_sql_injection's validator, both ways (review 2026-10-09, evals F1).

The v4 validator flagged any `+` or f-string inside execute(...), parameter
tuple included, and never looked at SQL built outside the call: the
canonical fix FAILED and a still-injectable app PASSED. All 21 live v4 rows
fail the task on that assertion.

Each case runs the REAL spec (setup + validation, /tmp/eval_sqli redirected
into tmp_path) against one edit of the fixture app.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")

ROOT = Path(__file__).resolve().parent.parent
TASK = json.loads((ROOT / "evals" / "tasks" / "23_sql_injection.json").read_text())

LOGIN = '''cur.execute("SELECT id FROM users WHERE name = '" + name + "' AND password = '" + pw + "'")'''
SEARCH = '''cur.execute("SELECT name FROM users WHERE name LIKE '%" + q + "%'")'''
USER = '''cur.execute(f"SELECT name FROM users WHERE id = {uid}")'''
LOGIN_OK = 'cur.execute("SELECT id FROM users WHERE name = ? AND password = ?", (name, pw))'
SEARCH_OK = '''cur.execute("SELECT name FROM users WHERE name LIKE ?", ('%' + q + '%',))'''
USER_OK = 'cur.execute("SELECT name FROM users WHERE id = ?", (uid,))'
HELPER_AT = "def _conn():"

# name -> (verdict, {vulnerable line: replacement}, extra module-level code)
SOLUTIONS = {
    # --- the review's four, plus the untouched app
    "canonical, LIKE pattern built inline with +": (True, {SEARCH: SEARCH_OK}, ""),
    "pattern assigned on the previous line": (True, {
        SEARCH: '''pattern = '%' + q + '%'\n    cur.execute("SELECT name FROM users WHERE name LIKE ?", (pattern,))'''}, ""),
    "f-string parameter": (True, {
        SEARCH: '''cur.execute("SELECT name FROM users WHERE name LIKE ?", (f"%{q}%",))'''}, ""),
    "SQL concatenated into a variable": (False, {
        SEARCH: '''sql = "SELECT name FROM users WHERE name LIKE '%" + q + "%'"\n    cur.execute(sql)''',
        USER: '''sql = "SELECT name FROM users WHERE id = " + uid\n    cur.execute(sql)'''}, ""),
    "original vulnerable app": (False, {LOGIN: LOGIN, SEARCH: SEARCH, USER: USER}, ""),
    # --- other correct fixes must not be failed on style
    "named placeholders": (True, {
        LOGIN: 'cur.execute("SELECT id FROM users WHERE name = :n AND password = :p", {"n": name, "p": pw})',
        SEARCH: '''cur.execute("SELECT name FROM users WHERE name LIKE :q", {"q": "%" + q + "%"})'''}, ""),
    "SQL in a module constant": (True, {
        SEARCH: '''cur.execute(SEARCH_SQL, ('%' + q + '%',))'''},
        'SEARCH_SQL = "SELECT name FROM users " + "WHERE name LIKE ?"\n\n'),
    "multi-line call": (True, {
        SEARCH: '''cur.execute(\n        "SELECT name FROM users "\n        "WHERE name LIKE ?",\n        ("%" + q + "%",),\n    )'''}, ""),
    "uid cast to int (rejects a payload with an error)": (True, {
        USER: 'cur.execute("SELECT name FROM users WHERE id = ?", (int(uid),))'}, ""),
    "pattern concatenated in SQL with ||": (True, {
        SEARCH: '''cur.execute("SELECT name FROM users WHERE name LIKE '%' || ? || '%'", (q,))'''}, ""),
    # --- still injectable, by routes the static check cannot see
    "/search SQL built in a helper function": (False, {SEARCH: "cur.execute(_b(q))"},
        "def _b(q):\n    return \"SELECT name FROM users WHERE name LIKE '%\" + q + \"%'\"\n\n"),
    "/user SQL built in a helper function": (False, {USER: "cur.execute(_b(uid))"},
        "def _b(u):\n    return 'SELECT name FROM users WHERE id = ' + u\n\n"),
    "/login SQL built in a helper function": (False, {LOGIN: "cur.execute(_b(name, pw))"},
        "def _b(n, p):\n    return \"SELECT id FROM users WHERE name = '\" + n + \"' AND password = '\" + p + \"'\"\n\n"),
    "only /login fixed": (False, {SEARCH: SEARCH, USER: USER}, ""),
    "/user left as % formatting": (False, {
        USER: 'cur.execute("SELECT name FROM users WHERE id = %s" % uid)'}, ""),
    "/search left as str.format": (False, {
        SEARCH: '''cur.execute("SELECT name FROM users WHERE name LIKE '%{}%'".format(q))'''}, ""),
}


def _box(text, root):
    return text.replace("/tmp/eval", f"{root}/eval")


@pytest.mark.parametrize("name", SOLUTIONS)
def test_validator_verdict(name, tmp_path):
    want, edits, extra = SOLUTIONS[name]
    subprocess.run(["bash", "-c", _box(TASK["setup"], tmp_path)], check=True)
    app = tmp_path / "eval_sqli" / "app.py"
    src = app.read_text()
    for vulnerable, fixed in {LOGIN: LOGIN_OK, SEARCH: SEARCH_OK, USER: USER_OK, **edits}.items():
        assert src.count(vulnerable) == 1, "the fixture changed under this test"
        src = src.replace(vulnerable, fixed)
    if extra:
        src = src.replace(HELPER_AT, extra + HELPER_AT)
    app.write_text(src)
    r = subprocess.run([sys.executable, "-c", _box(TASK["validation"]["script"], tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert (r.returncode == 0) is want, f"{name}: {(r.stdout + r.stderr)[-400:]}"


def test_the_task_text_promises_what_the_validator_asserts():
    assert "/search and /user/<uid>" in TASK["task"]
    assert "o'brien" in TASK["task"]
