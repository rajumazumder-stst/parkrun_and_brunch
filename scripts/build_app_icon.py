#!/usr/bin/env python3
"""Build the app icons and web app manifest from the raster logo.

Source: assets/app_icon_black_bg.jpg (1024x1024, the egg-and-runners logo on
black). assets/app_icon_cream_bg.jpg is the same design on cream, kept as the
alternative and not used.

Writes into static/ (served at /app/static/, see .streamlit/config.toml):

  apple-touch-icon.png  180x180  iPhone "Add to Home Screen"
  logo-192.png          192x192  Android, via the manifest
  logo-512.png          512x512  Android, via the manifest; also page_icon
  manifest.json                  what Android installs from

PNG, not JPEG: both platforms expect it. The source has no transparency and
the outputs keep none, which iOS needs (it fills a transparent pixel with
black on its own terms) and which makes the Android maskable crop safe: the
egg sits inside the central 40%-radius circle Android guarantees to keep
(measured at 35% on 9 Oct 2026), so a circle crop trims only background.

    python3 scripts/build_app_icon.py

Needs Pillow, which streamlit already installs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "app_icon_black_bg.jpg"
STATIC = ROOT / "static"

# Sampled from the source's background, so the Android splash screen and
# status bar run straight into the icon.
BG = "#1f1e19"

# The label under the icon on a home screen. iOS reads it from
# <meta name="apple-mobile-web-app-title">, which parkrun_app.py injects, so
# keep the two in step.
NAME = "parkrun & brunch"
SHORT_NAME = "p&b"

SIZES = (("apple-touch-icon.png", 180), ("logo-192.png", 192),
         ("logo-512.png", 512))


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed - pip install pillow", file=sys.stderr)
        return 1

    src = Image.open(SOURCE).convert("RGB")
    if src.width != src.height:
        print(f"{SOURCE.name} is {src.width}x{src.height}; an icon must be "
              "square", file=sys.stderr)
        return 2

    STATIC.mkdir(exist_ok=True)
    for fname, size in SIZES:
        src.resize((size, size), Image.Resampling.LANCZOS).save(
            STATIC / fname, optimize=True)
        print(f"wrote static/{fname} ({size}x{size})")

    manifest = {
        "name": NAME,
        "short_name": SHORT_NAME,
        # "/", not ".": start_url resolves against the manifest's own address,
        # and "." sent an Android install to /app/static/, a 404.
        "start_url": "/",
        "display": "standalone",
        "background_color": BG,
        "theme_color": BG,
        "icons": [
            {"src": f"./{fname}", "sizes": f"{size}x{size}",
             "type": "image/png", "purpose": "any maskable"}
            for fname, size in SIZES if fname.startswith("logo-")
        ],
    }
    (STATIC / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("wrote static/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
