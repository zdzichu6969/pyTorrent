from __future__ import annotations

from ._shared import bp

# Note: Route modules are imported for their decorators; this keeps the public API unchanged.
from . import torrents as _torrents_routes
from . import profiles as _profiles_routes
from . import rss as _rss_routes
from . import automations as _automations_routes
from . import smart_queue as _smart_queue_routes
from . import system as _system_routes
from . import backup as _backup_routes
from . import operation_logs as _operation_logs_routes

__all__ = ["bp"]
