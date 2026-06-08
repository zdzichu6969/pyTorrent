#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LIBS_STATIC_DIR = "libs"
BOOTSTRAP_VERSION = "5.3.3"
BOOTSWATCH_VERSION = "5.3.3"
FONTAWESOME_VERSION = "6.5.2"
FLAG_ICONS_VERSION = "7.2.3"
SWAGGER_UI_VERSION = "5"
SOCKET_IO_VERSION = "4.7.5"
GOOGLE_FONT_FAMILIES = (
    "DM Sans",
    "Figtree",
    "Geist",
    "IBM Plex Sans",
    "Inter",
    "JetBrains Mono",
    "Lato",
    "Manrope",
    "Montserrat",
    "Nunito Sans",
    "Open Sans",
    "Poppins",
    "Roboto",
    "Source Sans 3",
)
GOOGLE_FONT_WEIGHTS = "400;500;600;700;800"


def google_fonts_css_url() -> str:
    families = "&".join(
        f"family={name.replace(' ', '+')}:wght@{GOOGLE_FONT_WEIGHTS}"
        for name in GOOGLE_FONT_FAMILIES
    )
    return f"https://fonts.googleapis.com/css2?{families}&display=swap"


DEVEXPRESS_BOOTSTRAP_THEMES = {
    "blazing-berry": "Blazing Berry",
    "office-white": "Office White",
    "purple": "Purple",
}

PYTORRENT_APP_THEMES = {
    "adaptive": "pyTorrent Adaptive",
    "ocean": "pyTorrent Ocean",
    "graphite": "pyTorrent Graphite",
    "forest": "pyTorrent Forest",
    "amber": "pyTorrent Amber",
    "nord": "pyTorrent Nord",
    "crimson": "pyTorrent Crimson",
    "sky": "pyTorrent Sky",
}


BOOTSTRAP_THEME_DEFINITIONS = {
    "default": {
        "label": "Default Bootstrap",
        "local": f"{LIBS_STATIC_DIR}/bootstrap/{BOOTSTRAP_VERSION}/css/bootstrap.min.css",
        "cdn": f"https://cdn.jsdelivr.net/npm/bootstrap@{BOOTSTRAP_VERSION}/dist/css/bootstrap.min.css",
    },
    # Bootswatch themes.
    "flatly": {"label": "Bootswatch: Flatly", "provider": "bootswatch"},
    "litera": {"label": "Bootswatch: Litera", "provider": "bootswatch"},
    "lumen": {"label": "Bootswatch: Lumen", "provider": "bootswatch"},
    "minty": {"label": "Bootswatch: Minty", "provider": "bootswatch"},
    "sketchy": {"label": "Bootswatch: Sketchy", "provider": "bootswatch"},
    "spacelab": {"label": "Bootswatch: Spacelab", "provider": "bootswatch"},
    "united": {"label": "Bootswatch: United", "provider": "bootswatch"},
    "zephyr": {"label": "Bootswatch: Zephyr", "provider": "bootswatch"},
    # Complete DevExpress Bootstrap v5 dist.v5 set.
    **{
        f"dx-{theme}": {
            "label": f"DevExpress: {label}",
            "provider": "devexpress",
            "local": f"{LIBS_STATIC_DIR}/devexpress-bootstrap-themes/dist.v5/{theme}/bootstrap.min.css",
            "cdn": f"https://cdn.jsdelivr.net/gh/DevExpress/bootstrap-themes@master/dist.v5/{theme}/bootstrap.min.css",
        }
        for theme, label in DEVEXPRESS_BOOTSTRAP_THEMES.items()
    },
    # App-specific Bootstrap variable overrides. These sit on top of default Bootstrap.
    **{
        f"pytorrent-{theme}": {
            "label": f"Custom: {label}",
            "provider": "pytorrent",
            "local": f"{LIBS_STATIC_DIR}/pytorrent-themes/{theme}/bootstrap.min.css",
            "cdn": f"/static/{LIBS_STATIC_DIR}/pytorrent-themes/{theme}/bootstrap.min.css",
        }
        for theme, label in PYTORRENT_APP_THEMES.items()
    },
}

def _theme_definition(theme: str | None) -> dict[str, str]:
    theme = theme if theme in BOOTSTRAP_THEME_DEFINITIONS else "default"
    item = dict(BOOTSTRAP_THEME_DEFINITIONS[theme])
    if item.get("provider") == "bootswatch":
        item["local"] = f"{LIBS_STATIC_DIR}/bootswatch/{BOOTSWATCH_VERSION}/{theme}/bootstrap.min.css"
        item["cdn"] = f"https://cdn.jsdelivr.net/npm/bootswatch@{BOOTSWATCH_VERSION}/dist/{theme}/bootstrap.min.css"
    return item


BOOTSTRAP_THEMES = tuple(BOOTSTRAP_THEME_DEFINITIONS.keys())
STATIC_ASSETS = {
    "bootstrap_js": {
        "local": f"{LIBS_STATIC_DIR}/bootstrap/{BOOTSTRAP_VERSION}/js/bootstrap.bundle.min.js",
        "cdn": f"https://cdn.jsdelivr.net/npm/bootstrap@{BOOTSTRAP_VERSION}/dist/js/bootstrap.bundle.min.js",
    },
    "socket_io_js": {
        "local": f"{LIBS_STATIC_DIR}/socket.io/{SOCKET_IO_VERSION}/socket.io.min.js",
        "cdn": f"https://cdn.socket.io/{SOCKET_IO_VERSION}/socket.io.min.js",
    },
    "fontawesome_css": {
        "local": f"{LIBS_STATIC_DIR}/fontawesome/{FONTAWESOME_VERSION}/css/all.min.css",
        "cdn": f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FONTAWESOME_VERSION}/css/all.min.css",
    },
    "flag_icons_css": {
        "local": f"{LIBS_STATIC_DIR}/flag-icons/{FLAG_ICONS_VERSION}/css/flag-icons.min.css",
        "cdn": f"https://cdn.jsdelivr.net/gh/lipis/flag-icons@{FLAG_ICONS_VERSION}/css/flag-icons.min.css",
    },
    "font_css": {
        "local": f"{LIBS_STATIC_DIR}/fonts/google-fonts.css",
        "cdn": google_fonts_css_url(),
    },
    "swagger_css": {
        "local": f"{LIBS_STATIC_DIR}/swagger-ui/{SWAGGER_UI_VERSION}/swagger-ui.css",
        "cdn": f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css",
    },
    "swagger_js": {
        "local": f"{LIBS_STATIC_DIR}/swagger-ui/{SWAGGER_UI_VERSION}/swagger-ui-bundle.js",
        "cdn": f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js",
    },
}
URL_RE = re.compile(r"url\((['\"]?)(?!data:)(?!https?:)([^)'\"]+)\1\)")
ANY_URL_RE = re.compile(r"url\((['\"]?)(?!data:)([^)'\"]+)\1\)")


def bootstrap_css_asset(theme: str) -> dict[str, str]:
    item = _theme_definition(theme)
    return {"local": item["local"], "cdn": item["cdn"]}


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "pyTorrent installer"})
    with urlopen(req, timeout=60) as response:
        data = response.read()
    if not data:
        raise RuntimeError(f"Empty response for {url}")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    print(f"OK {dest.relative_to(ROOT)}")


def download_css_with_assets(url: str, dest: Path) -> None:
    download(url, dest)
    text = dest.read_text(encoding="utf-8", errors="ignore")
    for match in URL_RE.finditer(text):
        rel = match.group(2).split("#", 1)[0].split("?", 1)[0]
        if not rel:
            continue
        asset_url = urljoin(url, rel)
        asset_dest = (dest.parent / rel).resolve()
        try:
            asset_dest.relative_to(ROOT)
        except ValueError:
            continue
        if not asset_dest.exists():
            download(asset_url, asset_dest)


def download_google_fonts_css(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 pyTorrent installer",
            "Accept": "text/css,*/*;q=0.1",
        },
    )
    with urlopen(req, timeout=60) as response:
        css = response.read().decode("utf-8", errors="ignore")
    if not css.strip():
        raise RuntimeError(f"Empty response for {url}")

    def replace_url(match: re.Match[str]) -> str:
        quote = match.group(1) or ""
        asset_url = match.group(2)
        parsed = urlparse(asset_url)
        if parsed.scheme not in {"http", "https"}:
            return match.group(0)
        filename = Path(parsed.path).name
        if not filename:
            return match.group(0)
        asset_dest = dest.parent / "files" / filename
        if not asset_dest.exists():
            download(asset_url, asset_dest)
        return f"url({quote}files/{filename}{quote})"

    rewritten = ANY_URL_RE.sub(replace_url, css)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(rewritten, encoding="utf-8")
    tmp.replace(dest)
    print(f"OK {dest.relative_to(ROOT)}")

def main() -> None:
    items = list(STATIC_ASSETS.values())
    items.extend(bootstrap_css_asset(theme) for theme in BOOTSTRAP_THEMES)
    for item in items:
        url = item["cdn"]
        dest = ROOT / "pytorrent" / "static" / item["local"]
        if url.startswith("/static/"):
            if not dest.is_file() or dest.stat().st_size <= 0:
                raise RuntimeError(f"Bundled app theme is missing: {dest.relative_to(ROOT)}")
            print(f"OK {dest.relative_to(ROOT)}")
        elif item.get("local") == STATIC_ASSETS["font_css"]["local"]:
            download_google_fonts_css(url, dest)
        elif dest.suffix == ".css":
            download_css_with_assets(url, dest)
        else:
            download(url, dest)


if __name__ == "__main__":
    main()
