"""Step 4 — the interactive map.

Every live parkrun as context, the top N base points, and a dropdown that draws
any one base point's 25 driving legs with ferry segments dashed.

Run:  python3 build_map.py [--top 20]
Env:  MAP_CENTER="lat,lon"  MAP_ZOOM=n  MAP_INIT=<rank|all|none>  MAP_FIT=1
      MAP_OUT=<filename>          (all optional; used for QA screenshots)
"""
import argparse
import json
import os

import duckdb
import folium
from branca.element import Element
from folium.plugins import Fullscreen

from common import DB, OUTPUT

# Categorical slots 1 & 2 of the design-system palette — validated all-pairs in
# both light and dark. CONTEXT is deliberately below the chroma floor: the
# 2,368 parkruns are background, not a series.
BLUE, ORANGE, CONTEXT = "#2a78d6", "#eb6834", "#6b6a66"

ap = argparse.ArgumentParser()
ap.add_argument("--top", type=int, default=20)
args = ap.parse_args()

routes = json.loads((OUTPUT / f"routes_top{args.top}.json").read_text())

con = duckdb.connect(DB, read_only=True)
ev = con.sql("""
    select short_name, location, latitude, longitude
    from parkrun.events
    where live and seriesid = 1 and latitude is not null and longitude is not null
""").df()
con.close()

for t in routes:
    t["cluster"] = "Brisbane" if t["lat"] < 0 else "UK"
    t["color"] = BLUE if t["lat"] < 0 else ORANGE
n_bne = sum(1 for t in routes if t["lat"] < 0)

CENTER = [float(x) for x in os.environ.get("MAP_CENTER", "10,60").split(",")]
ZOOM = int(os.environ.get("MAP_ZOOM", "2"))
OUT = OUTPUT / os.environ.get("MAP_OUT", f"alphabet_map_top{args.top}.html")

m = folium.Map(location=CENTER, zoom_start=ZOOM, tiles=None, control_scale=True,
               min_zoom=2, max_bounds=True, prefer_canvas=True)
light_tiles = folium.TileLayer("cartodbpositron", name="Light basemap", show=True, no_wrap=True)
dark_tiles = folium.TileLayer("cartodbdark_matter", name="Dark basemap", show=False, no_wrap=True)
light_tiles.add_to(m)
dark_tiles.add_to(m)

# ---- context: every live main-series parkrun -------------------------------
ctx = folium.FeatureGroup(name=f"All live parkruns ({len(ev):,})", show=True)
for r in ev.itertuples():
    folium.CircleMarker(
        location=[r.latitude, r.longitude], radius=2.5,
        color=CONTEXT, weight=0, fill=True, fill_color=CONTEXT, fill_opacity=0.75,
        tooltip=f"{r.short_name} — {r.location or ''}".strip(" —"),
    ).add_to(ctx)
ctx.add_to(m)

# ---- one hidden layer per base point, driven by the dropdown ---------------
layer_names, bounds = {}, {}
for t in routes:
    grp = folium.FeatureGroup(name=f"routes_{t['rank']}", show=False, control=False)
    lats, lons = [t["lat"]], [t["lon"]]
    for leg in t["legs"]:
        for run in leg["runs"]:
            ferry = run["kind"] == "ferry"
            # 5 dp ~ 1 m: full visual detail at max zoom, far smaller than the
            # 6 dp OSRM returns (which dominates file size across 500 routes).
            coords = [[round(a, 5), round(o, 5)] for a, o in run["coords"]]
            folium.PolyLine(
                coords, color=t["color"], weight=2.5 if ferry else 2,
                opacity=0.9 if ferry else 0.75, dash_array="7,6" if ferry else None,
                tooltip=(f"{leg['letter']} → {leg['short_name']} · {leg['road_km']:.1f} km"
                         + (f" · ferry leg ({leg['ferry_km']:.1f} km at sea)" if ferry else "")),
            ).add_to(grp)
            for la, lo in coords:
                lats.append(la)
                lons.append(lo)
        folium.CircleMarker(
            location=[leg["lat"], leg["lon"]], radius=5,
            color="#ffffff", weight=2, fill=True, fill_color=t["color"], fill_opacity=1,
            tooltip=(f"<b>{leg['letter']} — {leg['short_name']}</b><br>"
                     f"{leg['road_km']:.1f} km by road"
                     + (f" — {leg['drive_km']:.1f} driving + {leg['ferry_km']:.1f} ferry"
                        if leg["ferry_km"] > 0 else "")
                     + f"<br>{leg['air_km']:.1f} km direct"),
        ).add_to(grp)
    grp.add_to(m)
    layer_names[t["rank"]] = grp.get_name()
    bounds[t["rank"]] = [[min(lats), min(lons)], [max(lats), max(lons)]]

# ---- the base points -------------------------------------------------------
bases = folium.FeatureGroup(name=f"Base points ({len(routes)})", show=True)
for t in routes:
    rows = "".join(
        f"<tr><td style='padding:1px 8px 1px 0'><b>{l['letter']}</b></td>"
        f"<td style='padding:1px 8px 1px 0'>{l['short_name']}</td>"
        f"<td style='padding:1px 0;text-align:right'>{l['road_km']:,.0f} km</td></tr>"
        for l in sorted(t["legs"], key=lambda l: -l["road_km"])[:3]
    )
    ferry_line = (f"<div style='color:#52514e;font-size:12px;margin-bottom:8px'>"
                  f"includes {t['ferry_km']:,.0f} km on ferries</div>"
                  if t["ferry_km"] > 0 else "")
    popup = f"""
    <div style="font:13px/1.45 system-ui,-apple-system,sans-serif;color:#0b0b0b;min-width:250px">
      <div style="font-size:15px;font-weight:650;margin-bottom:2px">
        #{t['rank']} · {t['cluster']}</div>
      <div style="color:#52514e;font-size:12px;margin-bottom:8px">
        {t['lat']:.4f}, {t['lon']:.4f}</div>
      <div style="font-size:22px;font-weight:680;letter-spacing:-0.02em">
        {t['road_km']:,.0f} km</div>
      <div style="color:#52514e;font-size:12px;margin-bottom:2px">
        total road distance · {t['air_km']:,.0f} km direct
        · detour &times;{t['road_km']/t['air_km']:.2f}</div>
      {ferry_line}
      <div style="color:#52514e;font-size:11px;text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:3px">Longest legs</div>
      <table style="font-size:12px;border-collapse:collapse">{rows}</table>
      <div style="margin-top:9px">
        <button onclick="vizShow({t['rank']})" style="font:12px system-ui;padding:4px 9px;
          cursor:pointer;border:1px solid #dcdbd6;border-radius:6px;background:#fff">
          Show these 25 legs</button></div>
    </div>"""
    folium.Marker(
        location=[t["lat"], t["lon"]],
        icon=folium.DivIcon(
            icon_size=(28, 28), icon_anchor=(14, 14),
            html=(f"<div style='width:26px;height:26px;border-radius:50%;"
                  f"background:{t['color']};border:2px solid #fff;"
                  f"box-shadow:0 1px 4px rgba(0,0,0,.45);color:#fff;"
                  f"font:650 13px/24px system-ui,-apple-system,sans-serif;"
                  f"text-align:center'>{t['rank']}</div>")),
        tooltip=f"#{t['rank']} — {t['road_km']:,.0f} km by road",
        popup=folium.Popup(popup, max_width=330),
    ).add_to(bases)
bases.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)
Fullscreen().add_to(m)

# ---- panels ----------------------------------------------------------------
mv = m.get_name()
options = "".join(f"<option value='{t['rank']}'>#{t['rank']} · {t['cluster']} · "
                  f"{t['road_km']:,.0f} km</option>" for t in routes)
n_ferry = sum(1 for t in routes if t["ferry_km"] > 0)
INIT_SEL = json.dumps(os.environ.get("MAP_INIT", "1"))
INIT_FIT = "true" if os.environ.get("MAP_FIT") else "false"

panel_html = f"""
<style>
  .viz-panel {{
    position:absolute; z-index:9999; background:#fcfcfbee; color:#0b0b0b;
    border:1px solid #dcdbd6; border-radius:10px; padding:12px 14px;
    font:13px/1.45 system-ui,-apple-system,sans-serif;
    box-shadow:0 2px 10px rgba(0,0,0,.12); backdrop-filter:blur(3px);
  }}
  #viz-title {{ top:12px; left:60px; max-width:340px; }}
  #viz-legend {{ bottom:48px; left:12px; }}
  #viz-title h1 {{ font-size:15px; font-weight:650; margin:0 0 4px; letter-spacing:-.01em; }}
  #viz-title p  {{ margin:0; font-size:12px; color:#52514e; }}
  .viz-key {{ display:flex; align-items:center; gap:8px; margin-top:6px; font-size:12px; }}
  .viz-sw {{ width:12px; height:12px; border-radius:50%; border:2px solid #fff;
             box-shadow:0 0 0 1px #00000022; flex:0 0 auto; }}
  .viz-row {{ margin-top:10px; display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
  .viz-row label {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em;
                    color:#52514e; width:100%; margin-bottom:-4px; }}
  .viz-panel select, .viz-panel button {{
    font:12px system-ui,-apple-system,sans-serif; padding:4px 9px; cursor:pointer;
    border:1px solid #dcdbd6; border-radius:6px; background:#fff; color:#0b0b0b;
  }}
  .viz-panel select {{ flex:1 1 auto; min-width:160px; }}
  .viz-panel button:hover {{ background:#f1f0ec; }}
  /* Panels follow the BASEMAP, not the OS: a light panel on dark tiles reads as
     two themes on one screen. */
  body.viz-dark .viz-panel {{ background:#1a1a19ee; color:#fff; border-color:#3a3a37; }}
  body.viz-dark #viz-title p, body.viz-dark .viz-panel .muted,
  body.viz-dark .viz-row label {{ color:#c3c2b7; }}
  body.viz-dark .viz-panel select, body.viz-dark .viz-panel button {{
    background:#252523; color:#fff; border-color:#3a3a37; }}
  body.viz-dark .viz-panel button:hover {{ background:#33332f; }}
</style>
<div id="viz-title" class="viz-panel">
  <h1>Where to base yourself for the parkrun alphabet challenge</h1>
  <p>The {len(routes)} best base points by total one-way <b>road</b> distance to a live
     parkrun for each letter A&ndash;Z (no X).</p>
  <div class="viz-row">
    <label>Show driving routes for</label>
    <select id="viz-pick">
      <option value="none">None</option>
      <option value="all">All {len(routes)} &mdash; {len(routes)*25} routes</option>
      {options}
    </select>
    <button onclick="{mv}.setView([10,60],2)">World</button>
  </div>
</div>
<div id="viz-legend" class="viz-panel">
  <div class="viz-key"><span class="viz-sw" style="background:{BLUE}"></span>
    Brisbane, Australia &mdash; {n_bne} of {len(routes)}</div>
  <div class="viz-key"><span class="viz-sw" style="background:{ORANGE}"></span>
    United Kingdom &mdash; {len(routes)-n_bne} of {len(routes)}</div>
  <div class="viz-key"><span class="viz-sw"
    style="background:{CONTEXT};border-color:transparent;width:8px;height:8px;
           margin:0 2px"></span> Live parkrun ({len(ev):,})</div>
  <!-- line-style swatches encode MODE, not identity -> neutral ink that flips
       with the theme, never a series colour -->
  <div class="viz-key">
    <span style="width:16px;height:0;border-top:2px solid currentColor;flex:0 0 auto;
                 opacity:.75"></span> Driving segment</div>
  <div class="viz-key">
    <span style="width:16px;height:0;border-top:2px dashed currentColor;flex:0 0 auto;
                 opacity:.75"></span> Ferry segment ({n_ferry} of {len(routes)} bases use one)</div>
  <div class="muted" style="color:#52514e;font-size:11px;margin-top:8px;
       max-width:225px;line-height:1.35">
    Letter = first character of the event's short name. 10 events with accented
    initials are excluded from the letter math, so 2,358 of the 2,368 count.</div>
</div>
"""

# NOTE: folium renders both html and script children BEFORE constructing the map
# object, so this must wait for 'load'. Running it inline throws a
# ReferenceError on the map var and silently kills every handler below.
panel_js = f"""
  window.addEventListener('load', function () {{
    var vizLayers = {json.dumps(layer_names)};
    var vizBounds = {json.dumps(bounds)};
    var vizMap = {mv};

    function clear() {{
      for (var k in vizLayers) {{
        var l = window[vizLayers[k]];
        if (l && vizMap.hasLayer(l)) vizMap.removeLayer(l);
      }}
    }}
    function add(rank) {{
      var l = window[vizLayers[rank]];
      if (l && !vizMap.hasLayer(l)) vizMap.addLayer(l);
    }}
    window.vizShow = function (rank, fit) {{
      rank = String(rank);
      clear();
      if (rank === 'all') {{ for (var k in vizLayers) add(k); }}
      else if (rank !== 'none') {{
        add(rank);
        if (fit !== false) vizMap.fitBounds(vizBounds[rank], {{padding: [40, 40]}});
      }}
      var sel = document.getElementById('viz-pick');
      if (sel && sel.value !== rank) sel.value = rank;
    }};
    document.getElementById('viz-pick').addEventListener('change', function () {{
      window.vizShow(this.value);
    }});

    var lightT = window['{light_tiles.get_name()}'],
        darkT  = window['{dark_tiles.get_name()}'];
    function paint(d) {{ document.body.classList.toggle('viz-dark', d); }}
    var prefersDark = window.matchMedia
      && window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (prefersDark && lightT && darkT) {{
      vizMap.removeLayer(lightT); vizMap.addLayer(darkT);
    }}
    paint(!!prefersDark);
    vizMap.on('baselayerchange', function (e) {{ paint(e.name === 'Dark basemap'); }});

    window.vizShow({INIT_SEL}, {INIT_FIT});
  }});
"""

m.get_root().html.add_child(Element(panel_html))
m.get_root().script.add_child(Element(panel_js))
m.save(str(OUT))
print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")
