from __future__ import annotations

import sqlite3

MIGRATIONS: tuple[str, ...] = ()


def run_database_migrations(conn: sqlite3.Connection) -> int:
    """Run pending database migrations."""
    
    applied = 0
    for sql in MIGRATIONS:
        conn.execute(sql)
        applied += 1
    return applied
