"""Step 2 — the interactive map.

Every live parkrun as context, plus the top N pairs drawn as true great-circle
arcs (the plane's path, not the Mercator straight line) with a ranked list that
highlights one pair at a time.

Run:  python3 build_map.py [--top 10]
Env:  MAP_CENTER="lat,lon"  MAP_ZOOM=n  MAP_INIT=<rank|all|none>
      MAP_OUT=<filename>          (all optional; used for QA screenshots)
"""
import argparse
import json
import os

import folium
import pandas as pd
from branca.element import Element
from folium.plugins import Fullscreen

from common import (ANTIPODAL_KM, OUTPUT, PAIRS_CSV, clip_mercator,
                    great_circle_points, load_events, split_antimeridian)

# The arcs are ONE series ("a furthest pair"), so they take categorical slot 1
# and rank is carried by the numbered endpoints and the list — not by ten hues.
# Slot 2 is the transient highlight. CONTEXT is deliberately below the chroma
# floor: the 2,374 parkruns are background, not a series. Light / dark steps.
BLUE = ("#2a78d6", "#3987e5")
ORANGE = ("#eb6834", "#d95926")
CONTEXT = "#6b6a66"

ap = argparse.ArgumentParser()
ap.add_argument("--top", type=int, default=10)
args = ap.parse_args()

pairs = pd.read_csv(PAIRS_CSV).head(args.top)
ev = load_events()

CENTER = [float(x) for x in os.environ.get("MAP_CENTER", "0,60").split(",")]
ZOOM = int(os.environ.get("MAP_ZOOM", "2"))
OUT = OUTPUT / os.environ.get("MAP_OUT", f"furthest_pairs_map_top{args.top}.html")

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
        color=CONTEXT, weight=0, fill=True, fill_color=CONTEXT, fill_opacity=0.7,
        tooltip=f"{r.short_name} — {r.location or ''}".strip(" —"),
    ).add_to(ctx)
ctx.add_to(m)

# ---- one hidden layer per pair, driven by the list -------------------------
arcs = folium.FeatureGroup(name=f"Furthest pairs ({len(pairs)})", show=True)
layer_names, arc_names, bounds = {}, {}, {}
for p in pairs.itertuples():
    label = (f"#{p.rank} · {p.event_a_name} ↔ {p.event_b_name} · "
             f"{p.distance_km:,.0f} km")
    path = clip_mercator(great_circle_points(
        p.event_a_lat, p.event_a_lon, p.event_b_lat, p.event_b_lon, n=400))
    grp = folium.FeatureGroup(name=f"pair_{p.rank}", show=False, control=False)
    names = []
    for seg in split_antimeridian(path):
        line = folium.PolyLine(
            [[round(a, 4), round(o, 4)] for a, o in seg],
            color=BLUE[0], weight=2, opacity=0.85, tooltip=label,
        )
        line.add_to(grp)
        names.append(line.get_name())
    grp.add_to(arcs)
    layer_names[p.rank] = grp.get_name()
    arc_names[p.rank] = names
    lats = [a for a, _ in path]
    lons = [o for _, o in path]
    bounds[p.rank] = [[min(lats), min(lons)], [max(lats), max(lons)]]
arcs.add_to(m)

# ---- endpoints: one marker per distinct event, listing the ranks it serves --
ends = {}
for p in pairs.itertuples():
    for side in ("a", "b"):
        eid = getattr(p, f"event_{side}_id")
        e = ends.setdefault(eid, {
            "name": getattr(p, f"event_{side}_name"),
            "location": getattr(p, f"event_{side}_location"),
            "country": getattr(p, f"event_{side}_country"),
            "lat": getattr(p, f"event_{side}_lat"),
            "lon": getattr(p, f"event_{side}_lon"),
            "ranks": [],
        })
        e["ranks"].append(int(p.rank))

pts = folium.FeatureGroup(name=f"Endpoints ({len(ends)})", show=True)
for e in ends.values():
    ranks = sorted(e["ranks"])
    badge = str(ranks[0]) if len(ranks) == 1 else f"×{len(ranks)}"
    rank_line = ("pair #" + str(ranks[0]) if len(ranks) == 1
                 else "pairs " + ", ".join(f"#{r}" for r in ranks))
    popup = f"""
    <div style="font:13px/1.45 system-ui,-apple-system,sans-serif;color:#0b0b0b;min-width:200px">
      <div style="font-size:15px;font-weight:650;margin-bottom:2px">{e['name']}</div>
      <div style="color:#52514e;font-size:12px">{e['location'] or ''}</div>
      <div style="color:#52514e;font-size:12px;margin-bottom:6px">{e['country']}</div>
      <div style="font-size:12px">One end of {rank_line}</div>
    </div>"""
    folium.Marker(
        location=[e["lat"], e["lon"]],
        icon=folium.DivIcon(
            icon_size=(26, 26), icon_anchor=(13, 13),
            html=(f"<div style='width:24px;height:24px;border-radius:50%;"
                  f"background:{BLUE[0]};border:2px solid #fff;"
                  f"box-shadow:0 1px 4px rgba(0,0,0,.45);color:#fff;"
                  f"font:650 12px/22px system-ui,-apple-system,sans-serif;"
                  f"text-align:center'>{badge}</div>")),
        tooltip=f"{e['name']} — {rank_line}",
        popup=folium.Popup(popup, max_width=320),
    ).add_to(pts)
pts.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)
Fullscreen().add_to(m)

# ---- panels ----------------------------------------------------------------
mv = m.get_name()
top1 = pairs.iloc[0]
rows = "".join(
    f"<tr data-rank='{p.rank}' style='cursor:pointer'>"
    f"<td style='padding:2px 8px 2px 0;color:#8a8880'>#{p.rank}</td>"
    f"<td style='padding:2px 8px 2px 0'>{p.event_a_name} &harr; {p.event_b_name}</td>"
    f"<td style='padding:2px 0;text-align:right;white-space:nowrap'>{p.distance_km:,.0f} km</td>"
    f"</tr>" for p in pairs.itertuples()
)
options = "".join(
    f"<option value='{p.rank}'>#{p.rank} · {p.distance_km:,.0f} km</option>"
    for p in pairs.itertuples()
)
INIT_SEL = json.dumps(os.environ.get("MAP_INIT", "all"))

panel_html = f"""
<style>
  .viz-panel {{
    position:absolute; z-index:9999; background:#fcfcfbee; color:#0b0b0b;
    border:1px solid #dcdbd6; border-radius:10px; padding:12px 14px;
    font:13px/1.45 system-ui,-apple-system,sans-serif;
    box-shadow:0 2px 10px rgba(0,0,0,.12); backdrop-filter:blur(3px);
  }}
  #viz-title {{ top:12px; left:60px; max-width:360px; }}
  #viz-list {{ bottom:48px; left:12px; max-width:400px; }}
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
  .viz-panel select {{ flex:1 1 auto; min-width:150px; }}
  .viz-panel button:hover {{ background:#f1f0ec; }}
  #viz-tbl {{ font-size:12px; border-collapse:collapse; margin-top:2px; }}
  #viz-tbl tr:hover td {{ background:#00000010; }}
  #viz-tbl tr.on td {{ font-weight:650; }}
  #viz-list summary {{ cursor:pointer; font-size:11px; text-transform:uppercase;
    letter-spacing:.06em; color:#52514e; }}
  #viz-list[open] summary {{ margin-bottom:4px; }}
  /* Panels follow the BASEMAP, not the OS: a light panel on dark tiles reads as
     two themes on one screen. */
  body.viz-dark .viz-panel {{ background:#1a1a19ee; color:#fff; border-color:#3a3a37; }}
  body.viz-dark #viz-title p, body.viz-dark .viz-panel .muted,
  body.viz-dark #viz-list summary, body.viz-dark .viz-row label {{ color:#c3c2b7; }}
  body.viz-dark .viz-panel select, body.viz-dark .viz-panel button {{
    background:#252523; color:#fff; border-color:#3a3a37; }}
  body.viz-dark .viz-panel button:hover {{ background:#33332f; }}
  body.viz-dark #viz-tbl tr:hover td {{ background:#ffffff1a; }}
  @media (max-width: 640px) {{
    html, body {{ overflow-x:hidden; }}
    .viz-panel {{ box-sizing:border-box; width:calc(100vw - 16px); max-width:calc(100vw - 16px); }}
    #viz-title {{ top:8px; left:8px; right:auto; padding:10px 12px; }}
    #viz-title h1 {{ font-size:14px; overflow-wrap:anywhere; }}
    #viz-title p {{ display:none; }}
    #viz-list {{ bottom:34px; left:8px; right:auto; max-height:42vh; overflow-y:auto; }}
    .viz-panel select {{ min-width:0; }}
    .leaflet-top.leaflet-left, .leaflet-top.leaflet-right {{ top:104px; }}
  }}
</style>
<div id="viz-title" class="viz-panel">
  <h1>The {len(pairs)} furthest-apart pairs of parkruns</h1>
  <p>Great-circle distance between every pair of the {len(ev):,} live Saturday
     5k events. The furthest &mdash; {top1.event_a_name} to {top1.event_b_name},
     {top1.distance_km:,.0f}&nbsp;km &mdash; is
     {top1.distance_km / ANTIPODAL_KM * 100:.1f}% of the maximum two points on
     Earth can be apart.</p>
  <div class="viz-row">
    <label>Highlight</label>
    <select id="viz-pick">
      <option value="all">All {len(pairs)} pairs</option>
      <option value="none">None</option>
      {options}
    </select>
    <button onclick="{mv}.setView([0,60],2)">World</button>
  </div>
</div>
<details id="viz-list" class="viz-panel" open>
  <summary>The {len(pairs)} pairs &amp; legend</summary>
  <table id="viz-tbl">{rows}</table>
  <div class="viz-key" style="margin-top:8px">
    <span style="width:16px;height:0;border-top:2px solid {BLUE[0]};flex:0 0 auto"
          class="viz-arc-sw"></span> Great-circle path between a pair</div>
  <div class="viz-key"><span class="viz-sw viz-end-sw" style="background:{BLUE[0]}"></span>
    Pair endpoint (&times;N = shared by N pairs)</div>
  <div class="viz-key"><span class="viz-sw"
    style="background:{CONTEXT};border-color:transparent;width:8px;height:8px;
           margin:0 2px"></span> Live parkrun ({len(ev):,})</div>
  <div class="muted" style="color:#52514e;font-size:11px;margin-top:8px;
       max-width:280px;line-height:1.35">
    Distances are as the crow flies over a sphere, not by any travelable route.
    Defunct events are excluded. Every pair is near-antipodal, so the shortest
    path between its ends can pass either side of the globe: the ones that go
    south run off the bottom of the map, because Mercator cannot draw the pole.</div>
</details>
"""

# NOTE: folium renders both html and script children BEFORE constructing the map
# object, so this must wait for 'load'. Running it inline throws a
# ReferenceError on the map var and silently kills every handler below.
panel_js = f"""
  window.addEventListener('load', function () {{
    var vizLayers = {json.dumps(layer_names)};
    var vizArcs   = {json.dumps(arc_names)};
    var vizBounds = {json.dumps(bounds)};
    var vizMap = {mv};
    var BLUE = {json.dumps(BLUE)}, ORANGE = {json.dumps(ORANGE)};
    var dark = false, sel = 'all';

    function accent() {{ return dark ? BLUE[1] : BLUE[0]; }}
    function hilite() {{ return dark ? ORANGE[1] : ORANGE[0]; }}

    function paintArcs() {{
      for (var r in vizArcs) {{
        var on = (sel === String(r));
        vizArcs[r].forEach(function (n) {{
          var l = window[n];
          if (l) l.setStyle({{color: on ? hilite() : accent(),
                              weight: on ? 3.5 : 2,
                              opacity: (sel === 'all' || on) ? 0.85 : 0.35}});
          if (l && on && l.bringToFront) l.bringToFront();
        }});
      }}
      document.querySelectorAll('.viz-arc-sw').forEach(function (e) {{
        e.style.borderTopColor = accent();
      }});
      document.querySelectorAll('.viz-end-sw').forEach(function (e) {{
        e.style.background = accent();
      }});
      document.querySelectorAll('.leaflet-marker-icon div').forEach(function (e) {{
        e.style.background = accent();
      }});
      document.querySelectorAll('#viz-tbl tr').forEach(function (tr) {{
        tr.classList.toggle('on', tr.dataset.rank === sel);
      }});
    }}

    window.vizShow = function (rank, fit) {{
      sel = String(rank);
      for (var k in vizLayers) {{
        var l = window[vizLayers[k]];
        if (!l) continue;
        var show = (sel !== 'none');
        if (show && !vizMap.hasLayer(l)) vizMap.addLayer(l);
        if (!show && vizMap.hasLayer(l)) vizMap.removeLayer(l);
      }}
      paintArcs();
      if (fit !== false && vizBounds[sel]) {{
        vizMap.fitBounds(vizBounds[sel], {{padding: [40, 40]}});
      }}
      var pick = document.getElementById('viz-pick');
      if (pick && pick.value !== sel) pick.value = sel;
    }};

    document.getElementById('viz-pick').addEventListener('change', function () {{
      window.vizShow(this.value);
    }});
    document.querySelectorAll('#viz-tbl tr').forEach(function (tr) {{
      tr.addEventListener('click', function () {{
        window.vizShow(tr.dataset.rank === sel ? 'all' : tr.dataset.rank);
      }});
    }});

    var lightT = window['{light_tiles.get_name()}'],
        darkT  = window['{dark_tiles.get_name()}'];
    function paint(d) {{ dark = d; document.body.classList.toggle('viz-dark', d); paintArcs(); }}
    var prefersDark = window.matchMedia
      && window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (prefersDark && lightT && darkT) {{
      vizMap.removeLayer(lightT); vizMap.addLayer(darkT);
    }}
    paint(!!prefersDark);
    vizMap.on('baselayerchange', function (e) {{ paint(e.name === 'Dark basemap'); }});

    var lg = document.getElementById('viz-list');
    if (lg && window.innerWidth <= 640) lg.removeAttribute('open');

    window.vizShow({INIT_SEL}, false);
  }});
"""

m.get_root().html.add_child(Element(panel_html))
m.get_root().script.add_child(Element(panel_js))
m.save(str(OUT))
print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
