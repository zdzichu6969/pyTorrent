from __future__ import annotations

# EOL note: do not recreate or edit the old pytorrent/services/rtorrent.py monolith.
# All rTorrent code belongs in this package directory.

# Note: Public functions are re-exported here so existing imports from services.rtorrent remain transparent.
# Compatibility note: module __all__ definitions include selected private helpers used by existing routes.
from .client import *
from .system import *
from .diagnostics import *
from .files import *
from .config import *
from .torrents import *
from .chunks import *
