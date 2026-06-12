from __future__ import annotations

import base64
import os
import platform
import sys
import time
import re
from datetime import datetime, timezone
import urllib.request
import urllib.parse
import socket
import json
import psutil
import zipfile
import tempfile
import queue
import threading
from pathlib import Path
from urllib.parse import quote
from flask import Blueprint, jsonify, request, abort, send_file, redirect, Response, stream_with_context, url_for
# Note: url_for is exported through this shared module for API routes that build temporary in-app links.
from ..config import DB_PATH, JOBS_RETENTION_DAYS, SMART_QUEUE_HISTORY_RETENTION_DAYS, LOG_RETENTION_DAYS, WORKERS, PYTORRENT_TMP_DIR
from ..db import connect, utcnow
from ..services.auth import current_user_id as default_user_id, current_user, list_users, save_user, delete_user, login_user, logout_user, enabled as auth_enabled, require_profile_write, require_admin, is_admin
from ..services import preferences, rtorrent, torrent_stats, speed_peaks, tracker_cache, rss as rss_service, ratio_rules, backup as backup_service, download_planner, operation_logs, poller_control, database_maintenance
from ..services.torrent_cache import torrent_cache
from ..services.torrent_summary import cached_summary
from ..services.workers import enqueue, list_jobs, cancel_job, retry_job, force_job, clear_jobs, emergency_clear_jobs
from ..services.geoip import lookup_ip
from ..services.torrent_meta import parse_torrent

bp = Blueprint("api", __name__, url_prefix="/api")

MOVE_BULK_MAX_HASHES = 100


from .auth_api import register_auth_routes
register_auth_routes(bp)


def _job_profile_id(job_id: str) -> int | None:
    with connect() as conn:
        row = conn.execute("SELECT profile_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    return int(row.get("profile_id") or 0) if row else None

def ok(payload=None):
    data = {"ok": True}
    if payload:
        data.update(payload)
    return jsonify(data)



PORT_CHECK_CACHE_SECONDS = 6 * 60 * 60


def _app_setting_get(key: str):
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row.get("value") if row else None


def _app_setting_set(key: str, value: str):
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (key, value))


def _iso_from_epoch(value) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def _public_ip(profile: dict | None = None, force: bool = False) -> str:
    if profile and bool(profile.get("is_remote")):
        return rtorrent.remote_public_ip(profile, force=force)
    req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "pyTorrent/port-check"})
    with urllib.request.urlopen(req, timeout=8) as res:
        return res.read(64).decode("utf-8", "replace").strip()


MAX_PORT_CHECK_CANDIDATES = 256


def _parse_port_candidates(value: str, limit: int = MAX_PORT_CHECK_CANDIDATES) -> tuple[list[int], bool]:
    """Return valid incoming port candidates from rTorrent network.port_range.

    Note: rTorrent may keep a range/list and pick a random port on start.
    The old checker used only the first number, which produced false "closed"
    results when another configured port was actually active.
    """
    ports: list[int] = []
    seen: set[int] = set()
    truncated = False

    def add(port: int) -> None:
        nonlocal truncated
        if not 1 <= port <= 65535 or port in seen:
            return
        if len(ports) >= limit:
            truncated = True
            return
        seen.add(port)
        ports.append(port)

    for start, end in re.findall(r"(\d{1,5})\s*-\s*(\d{1,5})", value or ""):
        a, b = int(start), int(end)
        if a > b:
            a, b = b, a
        for port in range(a, b + 1):
            add(port)
            if truncated:
                break

    without_ranges = re.sub(r"\d{1,5}\s*-\s*\d{1,5}", " ", value or "")
    for item in re.findall(r"\d{1,5}", without_ranges):
        add(int(item))

    return ports, truncated


def _incoming_ports(profile: dict) -> dict:
    try:
        raw_value = str(rtorrent.client_for(profile).call("network.port_range") or "")
    except Exception:
        raw_value = ""
    ports, truncated = _parse_port_candidates(raw_value)
    return {"ports": ports, "raw": raw_value, "truncated": truncated}


def _yougetsignal_check(public_ip: str, port: int) -> dict:
    body = urllib.parse.urlencode({"remoteAddress": public_ip, "portNumber": str(port)}).encode("utf-8")
    req = urllib.request.Request(
        "https://ports.yougetsignal.com/check-port.php",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "pyTorrent/port-check",
            "Accept": "text/html,application/json,*/*",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as res:
        text = res.read(8192).decode("utf-8", "replace")
    low = text.lower()
    if "is open" in low:
        return {"status": "open", "source": "yougetsignal", "raw": text[:500]}
    if "is closed" in low:
        return {"status": "closed", "source": "yougetsignal", "raw": text[:500]}
    return {"status": "unknown", "source": "yougetsignal", "raw": text[:500]}


def _local_port_fallback(public_ip: str, port: int) -> dict:
    try:
        with socket.create_connection((public_ip, port), timeout=3):
            return {"status": "open", "source": "local-fallback"}
    except Exception as exc:
        return {"status": "unknown", "source": "local-fallback", "error": f"Local fallback inconclusive: {exc}"}


def _check_ports(public_ip: str, ports: list[int], checker) -> dict:
    checked: list[int] = []
    first_closed: dict | None = None
    last_result: dict = {"status": "unknown"}

    for port in ports:
        checked.append(port)
        current = checker(public_ip, port)
        last_result = current
        if current.get("status") == "open":
            current.update({"port": port, "open_port": port, "checked_ports": checked})
            return current
        if current.get("status") == "closed" and first_closed is None:
            first_closed = current

    result = first_closed or last_result
    result.update({"port": ports[0] if ports else None, "open_port": None, "checked_ports": checked})
    return result


def port_check_status(force: bool = False) -> dict:
    profile = preferences.active_profile()
    prefs = preferences.get_preferences()
    enabled = bool((prefs or {}).get("port_check_enabled"))
    if not profile:
        return {"status": "unknown", "enabled": enabled, "error": "No profile"}

    port_info = _incoming_ports(profile)
    ports = port_info["ports"]
    if not ports:
        return {"status": "unknown", "enabled": enabled, "error": "Cannot read rTorrent network.port_range"}

    ports_key = ",".join(str(port) for port in ports)
    cache_key = f"port_check:{profile['id']}:{ports_key}:{int(bool(port_info['truncated']))}"
    if not force:
        cached = _app_setting_get(cache_key)
        if cached:
            try:
                data = json.loads(cached)
                if time.time() - float(data.get("checked_at_epoch") or 0) < PORT_CHECK_CACHE_SECONDS:
                    data["cached"] = True
                    data["enabled"] = enabled
                    if not data.get("checked_at"):
                        data["checked_at"] = _iso_from_epoch(data.get("checked_at_epoch"))
                    return data
            except Exception:
                pass

    checked_at_epoch = time.time()
    result = {
        "status": "unknown",
        "enabled": enabled,
        "port": ports[0],
        "ports": ports,
        "port_range": port_info["raw"],
        "ports_truncated": port_info["truncated"],
        "checked_at_epoch": checked_at_epoch,
        "checked_at": _iso_from_epoch(checked_at_epoch),
        "cached": False,
    }
    try:
        public_ip = _public_ip(profile, force=force)
        result["public_ip"] = public_ip
        result["remote"] = bool(profile.get("is_remote"))
        result.update(_check_ports(public_ip, ports, _yougetsignal_check))
    except Exception as exc:
        result["error"] = f"YouGetSignal failed: {exc}"
        try:
            public_ip = result.get("public_ip") or _public_ip(profile, force=force)
            result["public_ip"] = public_ip
            result["remote"] = bool(profile.get("is_remote"))
            result.update(_check_ports(public_ip, ports, _local_port_fallback))
        except Exception as fallback_exc:
            result["fallback_error"] = str(fallback_exc)
            result["source"] = "none"
    _app_setting_set(cache_key, json.dumps(result))
    return result






def _safe_len(callable_obj) -> int | None:
    try:
        return len(callable_obj())
    except Exception:
        return None

def _table_count(table: str, where: str = "", params: tuple = (), conn=None) -> int:
    """Count rows with one SQL statement; schema-created tables do not need a sqlite_master pre-check."""
    try:
        if conn is None:
            with connect() as owned_conn:
                row = owned_conn.execute(f"SELECT COUNT(*) AS n FROM {table} {where}", params).fetchone()
        else:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} {where}", params).fetchone()
        return int((row or {}).get("n") or 0)
    except Exception:
        return 0


def _db_size() -> dict:
    try:
        return database_maintenance.database_status()
    except Exception as exc:
        try:
            size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        except Exception:
            size = 0
        return {"path": str(DB_PATH), "size": size, "size_h": rtorrent.human_size(size), "error": str(exc)}


def _active_profile_cache_summary(profile_id: int | None = None, conn=None) -> dict:
    profile = preferences.active_profile() if profile_id is None else {"id": profile_id}
    profile_id = int((profile or {}).get("id") or 0)
    if not profile_id:
        return {"profile_id": 0, "profile_rows": 0, "runtime_items": 0}
    tracker_rows = _table_count("tracker_summary_cache", "WHERE profile_id=?", (profile_id,), conn=conn)
    stats_rows = _table_count("torrent_stats_cache", "WHERE profile_id=?", (profile_id,), conn=conn)
    runtime_items = 0
    try:
        runtime_items += len(torrent_cache.snapshot(profile_id))
    except Exception:
        pass
    return {"profile_id": profile_id, "profile_rows": tracker_rows + stats_rows, "tracker_rows": tracker_rows, "torrent_stats_rows": stats_rows, "runtime_items": runtime_items}


def cleanup_summary() -> dict:
    active_profile = preferences.active_profile()
    profile_id = int((active_profile or {}).get("id") or 0)
    with connect() as conn:
        operation_logs_total = _table_count(
            "operation_logs",
            "WHERE profile_id=? OR profile_id IS NULL",
            (profile_id,),
            conn=conn,
        ) if profile_id else _table_count("operation_logs", conn=conn)
        jobs_total = _table_count("jobs", conn=conn)
        jobs_clearable = _table_count("jobs", "WHERE status NOT IN ('pending', 'running')", conn=conn)
        smart_queue_history_total = _table_count("smart_queue_history", conn=conn)
        automation_history_total = _table_count("automation_history", conn=conn)
        cache_summary = _active_profile_cache_summary(profile_id if profile_id else None, conn=conn)
    operation_log_retention = operation_logs.get_settings(profile_id) if profile_id else operation_logs.get_settings(0)
    poller_runtime = poller_control.snapshot(profile_id) if profile_id else {}
    return {
        "jobs_total": jobs_total,
        "jobs_clearable": jobs_clearable,
        "smart_queue_history_total": smart_queue_history_total,
        "operation_logs_total": operation_logs_total,
        "automation_history_total": automation_history_total,
        "planner_history_total": download_planner.history_count(profile_id) if profile_id else 0,
        "cache": cache_summary,
        "poller_runtime": poller_runtime,
        "retention_days": {
            "jobs": JOBS_RETENTION_DAYS,
            "smart_queue_history": SMART_QUEUE_HISTORY_RETENTION_DAYS,
            "operation_logs": operation_log_retention.get("retention_days", LOG_RETENTION_DAYS),
            "automation_history": SMART_QUEUE_HISTORY_RETENTION_DAYS,
            "planner_history": SMART_QUEUE_HISTORY_RETENTION_DAYS,
        },
        "operation_log_retention": operation_log_retention,
        "retention_labels": {
            "operation_logs": operation_logs.retention_label(operation_log_retention),
        },
        "database": _db_size(),
        "admin": is_admin(current_user()),
    }

def active_default_download_path(profile: dict | None) -> str:
    if not profile:
        return ""
    try:
        return rtorrent.default_download_path(profile)
    except Exception:
        return ""


def enrich_bulk_payload(profile: dict, action_name: str, data: dict) -> dict:
    payload = dict(data or {})
    hashes = payload.get("hashes") or []
    if isinstance(hashes, str):
        hashes = [hashes]
    hashes = [str(h) for h in hashes if h]
    payload["hashes"] = hashes
    payload["job_context"] = {
        "source": "api",
        "action": action_name,
        "bulk": len(hashes) > 1,
        "hash_count": len(hashes),
        "requested_at": utcnow(),
    }
    if hashes:
        try:
            by_hash = {str(t.get("hash")): t for t in torrent_cache.snapshot(profile["id"])}
            payload["job_context"]["items"] = [
                {
                    "hash": h,
                    "name": str((by_hash.get(h) or {}).get("name") or ""),
                    "path": str((by_hash.get(h) or {}).get("path") or ""),
                }
                for h in hashes
            ]
        except Exception as exc:
            payload["job_context"]["items_error"] = str(exc)
    if action_name == "move":
        payload["job_context"]["target_path"] = str(payload.get("path") or "")
        payload["job_context"]["move_data"] = bool(payload.get("move_data"))
    if action_name == "remove":
        payload["job_context"]["remove_data"] = bool(payload.get("remove_data"))
    return payload


def _chunk_hashes(hashes: list[str], size: int = MOVE_BULK_MAX_HASHES) -> list[list[str]]:
    # Note: Splits very large torrent selections into predictable chunks so each queued job stays small and recoverable.
    safe_size = max(1, int(size or MOVE_BULK_MAX_HASHES))
    return [hashes[index:index + safe_size] for index in range(0, len(hashes), safe_size)]


def enqueue_bulk_parts(profile: dict, action_name: str, data: dict) -> list[dict]:
    # Note: One shared helper splits large move/remove operations into small ordered parts without changing other actions.
    base_payload = enrich_bulk_payload(profile, action_name, data)
    hashes = base_payload.get("hashes") or []
    chunks = _chunk_hashes(hashes)
    if len(chunks) <= 1:
        job_id = enqueue(action_name, profile["id"], base_payload)
        return [{"job_id": job_id, "label": "bulk-1", "part": 1, "parts": 1, "hashes": hashes, "hash_count": len(hashes)}]

    jobs = []
    items_by_hash = {str(item.get("hash")): item for item in (base_payload.get("job_context") or {}).get("items") or []}
    for index, chunk in enumerate(chunks, start=1):
        payload = dict(base_payload)
        payload["hashes"] = chunk
        context = dict(base_payload.get("job_context") or {})
        context.update({
            "bulk": True,
            "bulk_label": f"bulk-{index}",
            "bulk_part": index,
            "bulk_parts": len(chunks),
            "hash_count": len(chunk),
            "parent_hash_count": len(hashes),
            "items": [items_by_hash[h] for h in chunk if h in items_by_hash],
        })
        payload["job_context"] = context
        job_id = enqueue(action_name, profile["id"], payload)
        jobs.append({"job_id": job_id, "label": context["bulk_label"], "part": index, "parts": len(chunks), "hashes": chunk, "hash_count": len(chunk)})
    return jobs


def enqueue_move_bulk_parts(profile: dict, data: dict) -> list[dict]:
    # Note: Keep the old public move helper while using the same partitioning logic.
    return enqueue_bulk_parts(profile, "move", data)


def enqueue_remove_bulk_parts(profile: dict, data: dict) -> list[dict]:
    # Note: Remove/rm uses the same partitioning as move, which lowers rTorrent load.
    return enqueue_bulk_parts(profile, "remove", data)


def _user_disk_status(profile: dict) -> dict:
    # Note: Disk usage is user-preference aware, so it is read separately from the shared Socket.IO poller.
    prefs = preferences.get_disk_monitor_preferences(profile.get("id") if profile else None)
    try:
        paths = json.loads((prefs or {}).get("disk_monitor_paths_json") or "[]") if prefs else []
    except Exception:
        paths = []
    return rtorrent.disk_usage_for_paths(
        profile,
        paths,
        (prefs or {}).get("disk_monitor_mode") or "default",
        (prefs or {}).get("disk_monitor_selected_path") or "",
    )



# Note: Route modules import shared helpers with wildcard imports; include private helper names intentionally.
__all__ = [name for name in globals() if not name.startswith('__')]
