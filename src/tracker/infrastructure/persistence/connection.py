"""SQLite connection bootstrap: `connect()`, `apply_schema()`, `seed_stores()`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

#: (slug, name, base_url) — the retailers in scope. Every slug here MUST also
#: exist in `cli.main.ALL_STORE_SLUGS` (and vice versa) — a mismatch fails
#: with a FOREIGN KEY error the first time that store records a run, since
#: `store_run.store_slug` and `product.store_slug` both reference `store.slug`.
STORE_SEED_DATA: tuple[tuple[str, str, str], ...] = (
    ("plaza_vea", "Plaza Vea", "https://www.plazavea.com.pe"),
    ("oechsle", "Oechsle", "https://www.oechsle.pe"),
    ("ripley", "Ripley", "https://simple.ripley.com.pe"),
    ("ilahui", "Ilahui", "https://ilahuiperu.com"),
    ("pharmax", "Pharmax", "https://pharmax.com.pe"),
    ("tailoy", "Tai Loy", "https://www.tailoy.com.pe"),
    ("falabella", "Saga Falabella", "https://www.falabella.com.pe"),
    ("metro", "Metro", "https://www.metro.pe"),
    ("wong", "Wong", "https://www.wong.pe"),
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if absent) the SQLite database at `db_path`."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Idempotent `CREATE TABLE IF NOT EXISTS` — safe to call on every start."""
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


def seed_stores(conn: sqlite3.Connection) -> None:
    """Insert the four known retailers if not already present."""
    conn.executemany(
        "INSERT OR IGNORE INTO store (slug, name, base_url) VALUES (?, ?, ?)",
        STORE_SEED_DATA,
    )
    conn.commit()
