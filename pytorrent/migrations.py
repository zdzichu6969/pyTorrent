from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone


Migration = Callable[[sqlite3.Connection], bool]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_value(row: sqlite3.Row | dict[str, object] | tuple[object, ...], key: str, index: int) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return row[index]  # type: ignore[index]


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(_row_value(row, "name", 1)) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
    pk_columns = sorted(
        (
            (int(_row_value(row, "pk", 5) or 0), str(_row_value(row, "name", 1)))
            for row in columns
            if int(_row_value(row, "pk", 5) or 0)
        ),
        key=lambda item: item[0],
    )
    return [name for _, name in pk_columns]


def migrate_disk_monitor_preferences_to_profile_scope(conn: sqlite3.Connection) -> bool:
    if _primary_key_columns(conn, "disk_monitor_preferences") == ["profile_id"]:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_disk_monitor_preferences_owner ON disk_monitor_preferences(user_id)")
        return False

    now = _utcnow()
    conn.execute("DROP INDEX IF EXISTS idx_disk_monitor_preferences_owner")
    conn.execute("DROP TABLE IF EXISTS disk_monitor_preferences_new")
    conn.execute("DROP TABLE IF EXISTS disk_monitor_preferences_old_user_profile")
    conn.execute("""
        CREATE TABLE disk_monitor_preferences_new (
          profile_id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL,
          paths_json TEXT,
          mode TEXT DEFAULT 'default',
          selected_path TEXT,
          stop_enabled INTEGER DEFAULT 0,
          stop_threshold INTEGER DEFAULT 98,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id),
          FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id)
        )
    """)
    conn.execute("""
        INSERT INTO disk_monitor_preferences_new(
          profile_id, user_id, paths_json, mode, selected_path, stop_enabled, stop_threshold, created_at, updated_at
        )
        SELECT profile_id, user_id, paths_json, mode, selected_path, stop_enabled, stop_threshold,
               COALESCE(created_at, ?), COALESCE(updated_at, ?)
        FROM (
          SELECT d.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY profile_id
                   ORDER BY COALESCE(updated_at, created_at, '') DESC, user_id ASC
                 ) AS rn
          FROM disk_monitor_preferences d
          WHERE profile_id IS NOT NULL
        )
        WHERE rn = 1
    """, (now, now))
    conn.execute("ALTER TABLE disk_monitor_preferences RENAME TO disk_monitor_preferences_old_user_profile")
    conn.execute("ALTER TABLE disk_monitor_preferences_new RENAME TO disk_monitor_preferences")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_disk_monitor_preferences_owner ON disk_monitor_preferences(user_id)")
    return True


def migrate_profile_preferences_sidebar_columns(conn: sqlite3.Connection) -> bool:
    columns = _column_names(conn, "profile_preferences")
    changed = False
    if "sidebar_labels_expanded" not in columns:
        conn.execute("ALTER TABLE profile_preferences ADD COLUMN sidebar_labels_expanded INTEGER DEFAULT 0")
        changed = True
    if "sidebar_shortcuts_expanded" not in columns:
        conn.execute("ALTER TABLE profile_preferences ADD COLUMN sidebar_shortcuts_expanded INTEGER DEFAULT 0")
        changed = True
    return changed


MIGRATIONS: tuple[Migration, ...] = (
    migrate_disk_monitor_preferences_to_profile_scope,
    migrate_profile_preferences_sidebar_columns,
)


def run_database_migrations(conn: sqlite3.Connection) -> int:
    """Run idempotent database migrations and return how many changed the schema/data."""
    applied = 0
    for migration in MIGRATIONS:
        if migration(conn):
            applied += 1
    return applied
