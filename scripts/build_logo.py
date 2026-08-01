#!/usr/bin/env python3
"""Build the app logo: one SVG source per variant -> the PNG sizes the browser
and iOS want.

Two variants are kept:

  toast    (ACTIVE) "PR&B" on a slice of toast, the letters in the three
                    athletes' chart colours.
  runners           Three runners in those colours striding across a fried egg.

Only ACTIVE is rasterised into static/; the other variant's SVG is still built
so it stays current and reviewable.

    python3 scripts/build_logo.py            # build both SVGs + ACTIVE's PNGs
    python3 scripts/build_logo.py runners    # build just that variant's SVG

Needs cairosvg (rasterising) and fontTools + matplotlib (glyph outlines). All
three are build-time only and deliberately stay out of requirements.txt, which
is the deployed runtime: the PNGs are committed, so a deploy never rebuilds.

The lettering is converted from DejaVu Sans Bold into plain SVG paths at build
time, so the committed SVG renders identically on any machine with no font
installed. DejaVu is used rather than a system font like Arial because its
licence permits redistributing the outlines; Arial's does not.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
STATIC = ROOT / "static"

ACTIVE = "toast"

# Same hex values as ATHLETE_COLORS in app.py (Dark2).
GEORGE, RAJU, DUNCAN = "#1b9e77", "#d95f02", "#7570b3"

BG = "#23201d"        # warm charcoal: keeps the three hues legible at 32px
EGG_WHITE = "#fffdf7"
YOLK = "#f5b301"
CRUST = "#c8822f"
CRUMB = "#f7cf8e"
INK = "#3b2a17"

# --------------------------------------------------------------------------- #
# Lettering
# --------------------------------------------------------------------------- #
def _font_path() -> Path:
    import matplotlib
    return Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans-Bold.ttf"


def glyph_paths(text: str, size: float):
    """Outlines for `text` as SVG path data, plus each glyph's advance width.

    Returns (list[(path_d, advance)], total_width) already scaled to `size`.
    Font coordinates are y-up and SVG is y-down, so each path is emitted inside
    a flipped transform by the caller.
    """
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    font = TTFont(_font_path())
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    upm = font["head"].unitsPerEm
    scale = size / upm

    out, total = [], 0.0
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(pen)
        advance = font["hmtx"][name][0] * scale
        out.append((pen.getCommands(), advance))
        total += advance
    return out, total, scale


def lettering(text: str, colours: dict[str, str], cx: float, baseline: float,
              max_width: float, start_size: float = 150.0) -> str:
    """`text` centred on cx, shrunk until it fits max_width. Per-character fill."""
    size = start_size
    while size > 8:
        paths, total, scale = glyph_paths(text, size)
        if total <= max_width:
            break
        size -= 2

    x = cx - total / 2
    parts = []
    for ch, (d, advance) in zip(text, paths):
        if d:  # a space has no outline
            parts.append(
                f'<g transform="translate({x:.2f},{baseline}) scale({scale:.5f},'
                f'{-scale:.5f})"><path d="{d}" fill="{colours[ch]}"/></g>')
        x += advance
    return "\n  ".join(parts)


# --------------------------------------------------------------------------- #
# Variant: runners on a fried egg
# --------------------------------------------------------------------------- #
RUNNERS = [(GEORGE, 36), (RAJU, 184), (DUNCAN, 332)]
STROKE = 15


def _runner(colour: str, x: float, y: float, scale: float) -> str:
    """A mid-stride figure in a 0..100 box, feet at y=100, translated into place.

    Strokes with round caps rather than a filled silhouette: the shape stays
    readable when the figure is only ~20px tall in a favicon.
    """
    return f"""
  <g transform="translate({x},{y}) scale({scale})"
     stroke="{colour}" stroke-width="{STROKE}"
     stroke-linecap="round" stroke-linejoin="round" fill="none">
    <circle cx="54" cy="12" r="10" fill="{colour}" stroke="none"/>
    <path d="M 50,26 L 40,58"/>
    <path d="M 49,33 L 67,40 L 75,27"/>
    <path d="M 47,35 L 30,29 L 21,40"/>
    <path d="M 40,58 L 59,69 L 55,97"/>
    <path d="M 40,58 L 24,75 L 6,81"/>
  </g>"""


def svg_runners() -> str:
    # Feet land at y=340, below the egg's top edge, so the runners sit *on* the
    # egg instead of floating above it.
    figures = "".join(_runner(c, x, 139, 1.75) for c, x in RUNNERS)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"
     viewBox="0 0 512 512">
  <rect width="512" height="512" fill="{BG}"/>
  <g fill="{EGG_WHITE}">
    <ellipse cx="256" cy="378" rx="210" ry="78"/>
    <ellipse cx="152" cy="352" rx="98" ry="58"/>
    <ellipse cx="372" cy="366" rx="82" ry="52"/>
  </g>
  <circle cx="166" cy="364" r="44" fill="{YOLK}"/>
{figures}
</svg>
"""


# --------------------------------------------------------------------------- #
# Variant: PR&B on toast
# --------------------------------------------------------------------------- #
def _bread(i: float = 0) -> str:
    """A shallow elliptical cap over a body only slightly narrower.

    The small overhang reads as bread shoulders. A semicircular cap looks like
    an arch and twin humps look like a cloud — both were tried and rejected.
    `i` insets the whole shape, so drawing it twice gives the crust a border
    that survives at every size.
    """
    cy = 252
    rx, ry = 188 - i, 124 - i
    bx0, bx1 = 92 + i, 420 - i
    by1, rad = 424 - i, 48
    return (f'<ellipse cx="256" cy="{cy}" rx="{rx}" ry="{ry}"/>'
            f'<path d="M {bx0},{cy} L {bx1},{cy} L {bx1},{by1 - rad} '
            f'Q {bx1},{by1} {bx1 - rad},{by1} L {bx0 + rad},{by1} '
            f'Q {bx0},{by1} {bx0},{by1 - rad} Z"/>')


def svg_toast() -> str:
    # The & stays dark so the three athlete colours read as a set.
    colours = {"P": GEORGE, "R": RAJU, "&": INK, "B": DUNCAN}
    text = lettering("PR&B", colours, cx=256, baseline=332, max_width=268)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"
     viewBox="0 0 512 512">
  <rect width="512" height="512" fill="{BG}"/>
  <g fill="{CRUST}">{_bread(0)}</g>
  <g fill="{CRUMB}">{_bread(27)}</g>
  {text}
</svg>
"""


VARIANTS = {"toast": svg_toast, "runners": svg_runners}


def main(argv: list[str]) -> int:
    try:
        import cairosvg
    except ImportError:
        print("cairosvg not installed - pip install cairosvg", file=sys.stderr)
        return 1

    wanted = argv[1:] or list(VARIANTS)
    unknown = [v for v in wanted if v not in VARIANTS]
    if unknown:
        print(f"unknown variant(s): {', '.join(unknown)}; "
              f"choose from {', '.join(VARIANTS)}", file=sys.stderr)
        return 2

    ASSETS.mkdir(exist_ok=True)
    STATIC.mkdir(exist_ok=True)

    for name in wanted:
        svg = VARIANTS[name]()
        (ASSETS / f"logo-{name}.svg").write_text(svg)
        print(f"wrote assets/logo-{name}.svg")

        if name != ACTIVE:
            continue
        # 180 = iOS apple-touch-icon; 192 + 512 = what Chrome wants in a web app
        # manifest; 512 also feeds page_icon.
        for fname, size in (("apple-touch-icon.png", 180), ("logo-192.png", 192),
                            ("logo-512.png", 512)):
            cairosvg.svg2png(bytestring=svg.encode(), write_to=str(STATIC / fname),
                             output_width=size, output_height=size)
            print(f"wrote static/{fname} ({size}x{size}) from '{name}'")
        _write_manifest()
    return 0


def _write_manifest() -> None:
    """The web app manifest Android installs from.

    Streamlit Cloud serves its own manifest, which is why an Android "Add to
    Home screen" installs an app called "Streamlit" with their logo: a manifest
    always beats the apple-touch-icon fallback. app.py swaps this one in.

    purpose "any maskable" is safe here because the icon is full-bleed — Android
    crops maskable icons to a circle, and cropping a solid background just
    trims the charcoal, leaving the toast centred.
    """
    import json

    manifest = {
        "name": "parkrun & brunch",
        "short_name": "PR&B",
        "start_url": ".",
        "display": "standalone",
        "background_color": BG,
        "theme_color": BG,
        "icons": [
            {"src": "./logo-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any maskable"},
            {"src": "./logo-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }
    (STATIC / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("wrote static/manifest.json")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
