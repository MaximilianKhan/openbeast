import sqlite3


def migrate(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        conn.execute(
            "CREATE TABLE users_new(id INTEGER PRIMARY KEY, "
            "first_name TEXT NOT NULL, last_name TEXT NOT NULL, email TEXT)"
        )
        rows = conn.execute("SELECT id, full_name, email FROM users").fetchall()
        for uid, full_name, email in rows:
            parts = full_name.split(" ", 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ""
            conn.execute(
                "INSERT INTO users_new(id, first_name, last_name, email) "
                "VALUES (?, ?, ?, ?)",
                (uid, first, last, email),
            )
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_new RENAME TO users")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
