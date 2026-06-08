# rTorrent service modules

The old `pytorrent/services/rtorrent.py` monolith is end-of-life.
Do not recreate it and do not add new rTorrent logic outside this directory.

Use focused modules in `pytorrent/services/rtorrent/` instead:
- `client.py` for SCGI/XMLRPC transport and shared caches.
- `system.py` for status, footer metrics, disk and remote host usage.
- `torrents.py` for torrent list and torrent operations.
- `files.py`, `config.py`, `diagnostics.py` for their dedicated areas.
