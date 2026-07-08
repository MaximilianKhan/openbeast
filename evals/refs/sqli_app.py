import sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)
DB = "/tmp/eval_sqli/app.db"


def _conn():
    return sqlite3.connect(DB)


def init_db():
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, password TEXT);
        DELETE FROM users;
        INSERT INTO users(name,password) VALUES ('alice','a1'),('bob','b2'),('carol','c3');
    """)
    c.commit()
    c.close()


@app.route('/login')
def login():
    name = request.args.get('name', '')
    pw = request.args.get('password', '')
    c = _conn()
    cur = c.cursor()
    cur.execute("SELECT id FROM users WHERE name = ? AND password = ?", (name, pw))
    row = cur.fetchone()
    c.close()
    return jsonify({'ok': row is not None})


@app.route('/search')
def search():
    q = request.args.get('q', '')
    c = _conn()
    cur = c.cursor()
    cur.execute("SELECT name FROM users WHERE name LIKE '%' || ? || '%'", (q,))
    rows = [r[0] for r in cur.fetchall()]
    c.close()
    return jsonify(rows)


@app.route('/user/<uid>')
def get_user(uid):
    c = _conn()
    cur = c.cursor()
    cur.execute("SELECT name FROM users WHERE id = ?", (uid,))
    row = cur.fetchone()
    c.close()
    return jsonify({'name': row[0] if row else None})


if __name__ == '__main__':
    init_db()
    app.run(port=5998)
