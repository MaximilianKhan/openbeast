"""WRONG: adds and fills first_name/last_name with ALTER TABLE ADD COLUMN but never
removes full_name."""
import sqlite3


def migrate(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT ''")
        rows = conn.execute("SELECT id, full_name, email FROM users").fetchall()
        for uid, full_name, email in rows:
            parts = full_name.split(" ", 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ""
            conn.execute(
                "UPDATE users SET first_name = ?, last_name = ? WHERE id = ?",
                (first, last, uid),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
