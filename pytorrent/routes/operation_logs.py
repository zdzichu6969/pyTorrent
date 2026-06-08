from __future__ import annotations

from ._shared import *
from ..services import operation_logs


def _active_profile_or_400():
    profile = preferences.active_profile()
    if not profile:
        return None
    return profile


@bp.get("/operation-logs")
def operation_logs_list():
    profile = _active_profile_or_400()
    if not profile:
        return ok({"logs": [], "total": 0, "stats": {}, "settings": operation_logs.get_settings(0), "error": "No profile"})
    operation_logs.apply_retention(int(profile["id"]))
    data = operation_logs.list_logs(
        int(profile["id"]),
        limit=int(request.args.get("limit") or 200),
        offset=int(request.args.get("offset") or 0),
        event_type=str(request.args.get("type") or "").strip(),
        q=str(request.args.get("q") or "").strip(),
        hide_jobs=str(request.args.get("hide_jobs") or "").lower() in {"1", "true", "yes", "on"},
    )
    data["stats"] = operation_logs.stats(int(profile["id"]))
    data["settings"] = data["stats"].get("settings")
    return ok(data)


@bp.post("/operation-logs/settings")
def operation_logs_settings_save():
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        settings = operation_logs.save_settings(int(profile["id"]), request.get_json(silent=True) or {})
        result = operation_logs.apply_retention(int(profile["id"]))
        return ok({"settings": settings, "retention": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403 if isinstance(exc, PermissionError) else 400


@bp.post("/operation-logs/clear")
def operation_logs_clear():
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    event_type = str((request.get_json(silent=True) or {}).get("event_type") or "").strip()
    return ok({"deleted": operation_logs.clear(int(profile["id"]), event_type=event_type)})


@bp.post("/operation-logs/apply-retention")
def operation_logs_apply_retention():
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    return ok(operation_logs.apply_retention(int(profile["id"])))
