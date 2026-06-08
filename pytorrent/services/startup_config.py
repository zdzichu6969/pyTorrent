from __future__ import annotations

from time import sleep
from . import preferences, rtorrent

_started = False


def schedule_startup_config_apply(socketio, delay_seconds: int = 60) -> None:
    """Apply saved rTorrent UI overrides after pyTorrent has been running for a moment."""
    global _started
    if _started:
        return
    _started = True

    def runner():
        sleep(max(0, int(delay_seconds)))
        try:
            for profile in preferences.list_profiles():
                result = rtorrent.apply_startup_overrides(profile)
                if not result.get("skipped"):
                    socketio.emit("rtorrent_config_applied", {"profile_id": profile["id"], "result": result})
        except Exception as exc:
            socketio.emit("rtorrent_config_applied", {"ok": False, "error": str(exc)})

    socketio.start_background_task(runner)
