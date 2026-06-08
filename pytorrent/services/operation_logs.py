from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from ..db import connect, utcnow, default_user_id
from . import auth

DEFAULT_SETTINGS = {"retention_mode": "days", "retention_days": 30, "retention_lines": 5000}
VALID_RETENTION_MODES = {"days", "lines", "both", "manual"}
MAX_DETAIL_TEXT = 4000
MAX_DETAIL_ITEMS = 200


def _user_id(user_id: int | None = None) -> int:
    return int(user_id or auth.current_user_id() or default_user_id())


def _json_safe(value: Any, depth: int = 0) -> Any:
    """Convert operation details to JSON-safe data without dropping the whole payload on one bad value."""
    if depth > 8:
        return str(value)[:MAX_DETAIL_TEXT]
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > MAX_DETAIL_TEXT:
            return value[:MAX_DETAIL_TEXT] + "..."
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, (list, tuple, set)):
        data = list(value)
        safe = [_json_safe(item, depth + 1) for item in data[:MAX_DETAIL_ITEMS]]
        if len(data) > MAX_DETAIL_ITEMS:
            safe.append({"truncated_items": len(data) - MAX_DETAIL_ITEMS})
        return safe
    if isinstance(value, dict):
        items = list(value.items())
        safe = {str(k): _json_safe(v, depth + 1) for k, v in items[:MAX_DETAIL_ITEMS]}
        if len(items) > MAX_DETAIL_ITEMS:
            safe["truncated_keys"] = len(items) - MAX_DETAIL_ITEMS
        return safe
    return str(value)[:MAX_DETAIL_TEXT]


def _details(value: dict | None = None) -> str:
    """Serialize details defensively so partial non-serializable values do not erase the log details."""
    try:
        return json.dumps(_json_safe(value or {}), ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        return json.dumps({"serialization_error": str(exc), "raw_type": type(value).__name__}, ensure_ascii=False)


def _compact_detail_value(value: Any) -> str:
    """Build a readable one-line value for the Details column while keeping full JSON separately."""
    if value in (None, ""):
        return ""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        if not value:
            return ""
        return f"{len(value)} field(s)"
    text = str(value)
    return text if len(text) <= 160 else text[:157] + "..."


def _details_summary(details: dict) -> str:
    """Summarize important detail fields without hiding the full details_json payload."""
    priority = [
        "status", "job_id", "attempt", "attempts", "count", "hash_count", "action",
        "source", "source_label", "directory", "label", "target_path", "remove_data",
        "move_data", "keep_seeding", "error", "error_count", "result_count",
    ]
    parts: list[str] = []
    for key in priority:
        if key in details:
            value = _compact_detail_value(details.get(key))
            if value:
                parts.append(f"{key}: {value}")
    for key, raw in details.items():
        if key in priority:
            continue
        value = _compact_detail_value(raw)
        if value:
            parts.append(f"{key}: {value}")
        if len(parts) >= 10:
            break
    return ", ".join(parts)


def _row_to_public(row: dict) -> dict:
    item = dict(row)
    try:
        item["details"] = json.loads(item.get("details_json") or "{}")
    except Exception:
        item["details"] = {}
    item["details_h"] = _details_summary(item["details"])
    return item


def get_settings(profile_id: int = 0, user_id: int | None = None) -> dict:
    user_id = _user_id(user_id)
    profile_id = int(profile_id or 0)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM operation_log_settings WHERE profile_id=? ORDER BY updated_at DESC, user_id ASC LIMIT 1",
            (profile_id,),
        ).fetchone()
    if not row:
        return {"owner_user_id": user_id, "profile_id": profile_id, **DEFAULT_SETTINGS}
    data = {**DEFAULT_SETTINGS, **dict(row)}
    data["owner_user_id"] = int(data.pop("user_id", user_id) or user_id)
    data["retention_mode"] = data.get("retention_mode") if data.get("retention_mode") in VALID_RETENTION_MODES else "days"
    data["retention_days"] = max(1, int(data.get("retention_days") or DEFAULT_SETTINGS["retention_days"]))
    data["retention_lines"] = max(100, int(data.get("retention_lines") or DEFAULT_SETTINGS["retention_lines"]))
    return data


def save_settings(profile_id: int, data: dict, user_id: int | None = None) -> dict:
    user_id = _user_id(user_id)
    profile_id = int(profile_id or 0)
    mode = str(data.get("retention_mode") or "days").lower()
    if mode not in VALID_RETENTION_MODES:
        mode = "days"
    days = max(1, min(3650, int(data.get("retention_days") or DEFAULT_SETTINGS["retention_days"])))
    lines = max(100, min(1_000_000, int(data.get("retention_lines") or DEFAULT_SETTINGS["retention_lines"])))
    now = utcnow()
    if not auth.can_write_profile(profile_id, user_id):
        raise PermissionError("No write access to profile")
    with connect() as conn:
        conn.execute("DELETE FROM operation_log_settings WHERE profile_id=?", (profile_id,))
        conn.execute(
            """
            INSERT INTO operation_log_settings(user_id, profile_id, retention_mode, retention_days, retention_lines, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (user_id, profile_id, mode, days, lines, now, now),
        )
    return get_settings(profile_id, user_id)


def record(profile_id: int | None, event_type: str, message: str, *, severity: str = "info", source: str = "system", torrent_hash: str | None = None, torrent_name: str | None = None, action: str | None = None, details: dict | None = None, user_id: int | None = None) -> int:
    """Insert one operation log row and keep all details in JSON-safe form."""
    now = utcnow()
    user_id = _user_id(user_id)
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO operation_logs(user_id, profile_id, event_type, severity, source, torrent_hash, torrent_name, action, message, details_json, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (user_id, int(profile_id or 0) or None, str(event_type), str(severity or "info"), str(source or "system"), torrent_hash, torrent_name, action, str(message), _details(details), now),
        )
        return int(cur.lastrowid)


def _job_event_type(status: str) -> str:
    """Map worker states to explicit operation log event types without changing old done/failed names."""
    return {
        "queued": "job_queued",
        "started": "job_started",
        "done": "job_done",
        "failed": "job_failed",
        "retry": "job_retry",
        "cancelled": "job_cancelled",
        "timeout": "job_timeout",
        "resubmitted": "job_resubmitted",
        "forced": "job_forced",
    }.get(str(status), "job_event")


def _job_severity(status: str) -> str:
    """Use severity consistently for filtering and badge rendering."""
    if status in {"failed", "timeout"}:
        return "danger"
    if status in {"retry", "resubmitted", "cancelled", "forced"}:
        return "warning"
    return "info"


def _job_action_label(action: str) -> str:
    """Return a stable human-readable action label for log messages."""
    labels = {
        "add_magnet": "Magnet link",
        "add_torrent_raw": "Torrent file",
        "set_label": "Set label",
        "set_ratio_group": "Set ratio group",
        "set_limits": "Set speed limits",
        "smart_queue_check": "Smart Queue check",
    }
    return labels.get(str(action or ""), str(action or "job"))


def _result_summary(result: dict) -> dict:
    """Extract compact result counters while preserving full result in details."""
    result = result or {}
    results = result.get("results") if isinstance(result.get("results"), list) else []
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    ignored_errors = result.get("ignored_errors") if isinstance(result.get("ignored_errors"), list) else []
    return {
        "result_count": len(results) if results is not None else result.get("count"),
        "error_count": len(errors or []) + len(ignored_errors or []),
    }


def record_job_event(profile_id: int, action: str, status: str, payload: dict | None, result: dict | None = None, error: str = "", job_id: str | None = None, user_id: int | None = None) -> None:
    """Record queued, running and terminal job states with per-torrent context when available."""
    payload = payload or {}
    result = result or {}
    hashes = payload.get("hashes") or []
    ctx = payload.get("job_context") or {}
    items = ctx.get("items") or []
    by_hash = {str(item.get("hash")): item for item in items if item}
    event_type = _job_event_type(str(status))
    severity = _job_severity(str(status))
    context_source = str(ctx.get("source") or payload.get("source") or "user")
    source_label = str(ctx.get("rule_name") or ctx.get("source") or context_source)
    source = "job"
    base_details = {
        "job_id": job_id,
        "status": status,
        "source": context_source,
        "source_label": source_label,
        "directory": payload.get("directory"),
        "label": payload.get("label"),
        "target_path": ctx.get("target_path") or payload.get("path"),
        "remove_data": ctx.get("remove_data") or payload.get("remove_data"),
        "move_data": ctx.get("move_data") or payload.get("move_data"),
        "keep_seeding": payload.get("keep_seeding"),
        "hash_count": len(hashes),
        "error": error,
        "result": result,
        **_result_summary(result),
    }
    if action in {"add_magnet", "add_torrent_raw"}:
        name = str(payload.get("name") or payload.get("filename") or payload.get("uri") or "torrent")[:300]
        status_label = {"queued": "queued", "started": "started", "done": "added", "failed": "failed", "retry": "retry scheduled", "cancelled": "cancelled"}.get(str(status), str(status))
        msg = f"{_job_action_label(action)} {status_label}: {name}"
        record(profile_id, "torrent_added" if status == "done" else event_type, msg, severity=severity, source=source, action=action, details=base_details, user_id=user_id)
        return
    if not hashes:
        record(profile_id, event_type, f"{_job_action_label(action)} {status}", severity=severity, source=source, action=action, details=base_details, user_id=user_id)
        return
    for h in hashes:
        item = by_hash.get(str(h)) or {}
        name = str(item.get("name") or h)
        row_details = {**base_details, "item": item}
        record(profile_id, "torrent_removed" if action == "remove" and status == "done" else event_type, f"{_job_action_label(action)} {status}: {name}", severity=severity, source=source, torrent_hash=str(h), torrent_name=name, action=action, details=row_details, user_id=user_id)


def record_worker_event(profile_id: int, action: str, status: str, message: str, *, payload: dict | None = None, job_id: str | None = None, user_id: int | None = None, error: str = "", details: dict | None = None) -> None:
    """Log worker-only lifecycle events that do not execute the normal job action path."""
    payload = payload or {}
    merged = {"job_id": job_id, "status": status, "error": error, "payload": payload, **(details or {})}
    record(profile_id, _job_event_type(status), message, severity=_job_severity(status), source="worker", action=action, details=merged, user_id=user_id)


def record_cache_diff(profile_id: int, added: list[dict], removed: list[str], updated: list[dict], old_rows: dict[str, dict]) -> None:
    """Record torrent cache changes detected by the poller without depending on manual jobs."""
    for row in added or []:
        record(profile_id, "torrent_added", f"Torrent added: {row.get('name') or row.get('hash')}", source="poller", torrent_hash=row.get("hash"), torrent_name=row.get("name"), details={"size": row.get("size"), "path": row.get("path"), "label": row.get("label"), "tracker": row.get("tracker")})
    for h in removed or []:
        old = old_rows.get(str(h)) or {}
        record(profile_id, "torrent_removed", f"Torrent removed: {old.get('name') or h}", source="poller", torrent_hash=str(h), torrent_name=old.get("name"), details={"path": old.get("path"), "label": old.get("label"), "tracker": old.get("tracker")})
    for patch in updated or []:
        h = str(patch.get("hash") or "")
        old = old_rows.get(h) or {}
        was_complete = bool(old.get("complete")) or float(old.get("progress") or 0) >= 100
        is_complete = bool(patch.get("complete", old.get("complete"))) or float(patch.get("progress", old.get("progress") or 0) or 0) >= 100
        if h and not was_complete and is_complete:
            record(profile_id, "torrent_completed", f"Torrent completed: {old.get('name') or h}", source="poller", torrent_hash=h, torrent_name=old.get("name"), details={"ratio": patch.get("ratio", old.get("ratio")), "size": old.get("size"), "path": old.get("path"), "label": old.get("label"), "tracker": old.get("tracker")})


def list_logs(profile_id: int, *, limit: int = 200, offset: int = 0, event_type: str = "", q: str = "", hide_jobs: bool = False) -> dict:
    """Return operation logs with searchable messages, torrents, actions and detail JSON."""
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    where = ["(profile_id=? OR profile_id IS NULL)"]
    params: list[Any] = [int(profile_id or 0)]
    if event_type:
        where.append("event_type=?")
        params.append(event_type)
    if hide_jobs:
        where.append("COALESCE(source, '') <> 'job' AND event_type NOT LIKE 'job_%'")
    if q:
        where.append("(message LIKE ? OR torrent_name LIKE ? OR torrent_hash LIKE ? OR action LIKE ? OR details_json LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    sql_where = " WHERE " + " AND ".join(where)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM operation_logs{sql_where} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) AS n FROM operation_logs{sql_where}", tuple(params)).fetchone()["n"]
    return {"logs": [_row_to_public(r) for r in rows], "total": int(total or 0), "limit": limit, "offset": offset}


def stats(profile_id: int) -> dict:
    profile_id = int(profile_id or 0)
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM operation_logs WHERE profile_id=? OR profile_id IS NULL", (profile_id,)).fetchone()["n"]
        by_type = conn.execute("SELECT event_type, COUNT(*) AS n FROM operation_logs WHERE profile_id=? OR profile_id IS NULL GROUP BY event_type ORDER BY n DESC LIMIT 12", (profile_id,)).fetchall()
        by_day = conn.execute("SELECT substr(created_at,1,10) AS bucket, COUNT(*) AS n FROM operation_logs WHERE profile_id=? OR profile_id IS NULL GROUP BY bucket ORDER BY bucket DESC LIMIT 14", (profile_id,)).fetchall()
        by_month = conn.execute("SELECT substr(created_at,1,7) AS bucket, COUNT(*) AS n FROM operation_logs WHERE profile_id=? OR profile_id IS NULL GROUP BY bucket ORDER BY bucket DESC LIMIT 12", (profile_id,)).fetchall()
        top_actions = conn.execute("SELECT COALESCE(action, event_type) AS action, COUNT(*) AS n FROM operation_logs WHERE profile_id=? OR profile_id IS NULL GROUP BY COALESCE(action, event_type) ORDER BY n DESC LIMIT 12", (profile_id,)).fetchall()
    return {"total": int(total or 0), "by_type": by_type, "by_day": by_day, "by_month": by_month, "top_actions": top_actions, "settings": get_settings(profile_id)}


def retention_label(settings: dict) -> str:
    mode = settings.get("retention_mode") or "days"
    if mode == "manual":
        return "manual cleanup only"
    if mode == "lines":
        return f"retention {settings.get('retention_lines') or DEFAULT_SETTINGS['retention_lines']} lines"
    if mode == "both":
        return f"retention {settings.get('retention_days') or DEFAULT_SETTINGS['retention_days']} days and {settings.get('retention_lines') or DEFAULT_SETTINGS['retention_lines']} lines"
    return f"retention {settings.get('retention_days') or DEFAULT_SETTINGS['retention_days']} days"


def clear(profile_id: int, *, event_type: str = "") -> int:
    where = ["(profile_id=? OR profile_id IS NULL)"]
    params: list[Any] = [int(profile_id or 0)]
    if event_type:
        where.append("event_type=?")
        params.append(event_type)
    with connect() as conn:
        cur = conn.execute("DELETE FROM operation_logs WHERE " + " AND ".join(where), tuple(params))
        return int(cur.rowcount or 0)


def apply_retention(profile_id: int, user_id: int | None = None) -> dict:
    """Apply operation-log retention without touching torrent data or other history tables."""
    settings = get_settings(profile_id, user_id)
    mode = settings.get("retention_mode") or "manual"
    deleted_days = 0
    deleted_lines = 0
    with connect() as conn:
        if mode in {"days", "both"}:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=int(settings["retention_days"]))).isoformat(timespec="seconds")
            cur = conn.execute("DELETE FROM operation_logs WHERE (profile_id=? OR profile_id IS NULL) AND created_at<?", (int(profile_id or 0), cutoff))
            deleted_days = int(cur.rowcount or 0)
        if mode in {"lines", "both"}:
            keep = int(settings["retention_lines"])
            ids = conn.execute("SELECT id FROM operation_logs WHERE profile_id=? OR profile_id IS NULL ORDER BY id DESC LIMIT -1 OFFSET ?", (int(profile_id or 0), keep)).fetchall()
            if ids:
                placeholders = ",".join("?" for _ in ids)
                cur = conn.execute(f"DELETE FROM operation_logs WHERE id IN ({placeholders})", tuple(r["id"] for r in ids))
                deleted_lines = int(cur.rowcount or 0)
    return {"deleted_days": deleted_days, "deleted_lines": deleted_lines, "deleted": deleted_days + deleted_lines, "settings": settings}
