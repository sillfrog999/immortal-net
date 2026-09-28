"""
db.py - tiny database abstraction layer.

Why this exists:
    The app is written once against a small set of helper functions
    (get_connection / run / run_returning_id) so the SAME application code
    works against:
      - SQLite   -> used automatically for local development / testing,
                    zero setup required.
      - PostgreSQL -> used in production on Render, selected automatically
                    the moment a DATABASE_URL environment variable is set.

    Queries are written with "?" placeholders everywhere (SQLite style).
    When running on PostgreSQL, placeholders are rewritten to "%s" and
    the connection returns dict-like rows too, so the rest of the app
    never has to care which database is actually running underneath.
"""

import os
import sqlite3
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
elif os.environ.get("RENDER"):
    # Render sets RENDER=true automatically. Its filesystem is wiped on every
    # deploy/restart, so a SQLite file there would silently lose ALL data.
    # Fail loudly instead of starting with a database that will vanish.
    raise RuntimeError(
        "DATABASE_URL is not set but the app is running on Render. Refusing to "
        "use SQLite (data would be erased on every deploy). Set DATABASE_URL "
        "to your PostgreSQL connection string."
    )

# Only THIS exception means "that unique value already exists".
# Anything else is a real bug and must not be reported as "already taken".
UniqueViolation = psycopg2.errors.UniqueViolation if IS_POSTGRES else sqlite3.IntegrityError

SQLITE_PATH = os.environ.get("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "immortalnet.db"))


def get_connection():
    """Return a new DB connection. Caller is responsible for closing it."""
    if IS_POSTGRES:
        # Render's DATABASE_URL sometimes uses the old "postgres://" scheme;
        # psycopg2 accepts both, so no rewriting is needed.
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _adapt_sql(sql):
    """Rewrite '?' placeholders to '%s' for PostgreSQL."""
    return sql.replace("?", "%s") if IS_POSTGRES else sql


def _row_to_dict(row):
    if row is None:
        return None
    if IS_POSTGRES:
        return dict(row)
    return dict(row)  # sqlite3.Row supports dict() too


def run(conn, sql, params=(), fetch=None, commit=False):
    """
    Execute a query.
      fetch=None   -> just execute (e.g. INSERT/UPDATE/DELETE without needing the id)
      fetch="one"  -> return a single row as a dict (or None)
      fetch="all"  -> return a list of dicts
    """
    if IS_POSTGRES:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()

    cur.execute(_adapt_sql(sql), params)

    result = None
    if fetch == "one":
        result = _row_to_dict(cur.fetchone())
    elif fetch == "all":
        result = [_row_to_dict(r) for r in cur.fetchall()]

    if commit:
        conn.commit()

    cur.close()
    return result


def run_insert_returning_id(conn, sql, params=()):
    """
    Run an INSERT and return the new row's id, across both backends.
    `sql` must be written WITHOUT "RETURNING id" - it is appended automatically
    for PostgreSQL; for SQLite we use cursor.lastrowid.
    """
    if IS_POSTGRES:
        cur = conn.cursor()
        cur.execute(_adapt_sql(sql) + " RETURNING id", params)
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return new_id
    else:
        cur = conn.cursor()
        cur.execute(_adapt_sql(sql), params)
        new_id = cur.lastrowid
        conn.commit()
        cur.close()
        return new_id


SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS websites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    domain TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    html TEXT DEFAULT '',
    css TEXT DEFAULT '',
    js TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    published INTEGER DEFAULT 1
);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS websites (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    domain VARCHAR(32) UNIQUE NOT NULL,
    title VARCHAR(200) NOT NULL,
    html TEXT DEFAULT '',
    css TEXT DEFAULT '',
    js TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    published BOOLEAN DEFAULT TRUE
);
"""


def init_db():
    """Create tables if they don't exist yet. Safe to call on every startup."""
    conn = get_connection()
    schema = SCHEMA_POSTGRES if IS_POSTGRES else SCHEMA_SQLITE
    if IS_POSTGRES:
        cur = conn.cursor()
        cur.execute(schema)
        conn.commit()
        cur.close()
    else:
        conn.executescript(schema)
        conn.commit()
    conn.close()


def dump_all():
    """Return every user and website as plain dicts (used by backups)."""
    conn = get_connection()
    users = run(conn, "SELECT * FROM users", fetch="all")
    websites = run(conn, "SELECT * FROM websites", fetch="all")
    conn.close()
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "backend": "postgres" if IS_POSTGRES else "sqlite",
        "users": users,
        "websites": websites,
    }
