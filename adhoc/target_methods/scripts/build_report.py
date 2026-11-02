"""Compare head-to-head target methods: the current 91-day median against three
proposals, and write one standalone HTML report.

    python adhoc/target_methods/scripts/build_report.py

Reads the database `parkrun_core.resolve_db()` picks (local source of truth,
else the committed snapshot), read-only. Writes `output/target_methods.html`
(gitignored) and `results/summary.csv` (tracked). Changes nothing in the app.

Everything except the target is the app's own arithmetic, re-done in pandas so
the sequential method (M3) is possible: the handicap bridge, the demote rule,
pct_diff rounded to 2 dp and standard competition ranking. M0 must reproduce
`v_head_to_head` row for row before any comparison is trusted (`check_m0`).
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

TOPIC = Path(__file__).resolve().parents[1]
REPO = TOPIC.parents[1]
sys.path.insert(0, str(REPO))
from parkrun_core import TARGET_WINDOW_DAYS, resolve_db  # noqa: E402
import parkrun_ui  # noqa: E402
from parkrun_ui import (  # noqa: E402
    ATHLETE_COLORS, MEDAL, PLOTLY_CONFIG, _h2h_headline, _victory_fig,
    _winning_margin, fmt_time, lock_zoom,
)

# The page is light; skips st.context, which warns on every call outside a run.
parkrun_ui._surface_color = lambda: "#ffffff"

OUTPUT = TOPIC / "output" / "target_methods.html"
SUMMARY = TOPIC / "results" / "summary.csv"
PLOTLY_JS = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@3.6.0/plotly.min.js"
HANDICAP_DEFAULT = 0.15          # parkrun_pipeline.BUGGY_HANDICAP_DEFAULT
M2_DAYS, M2_RUNS = 182, 13
M3_DEFAULT = (0.5, 0.1)          # (a_fast, a_slow)
M3_GRID = [(f, s) for f in (0.3, 0.5, 0.7) for s in (0.05, 0.1, 0.2)]

METHODS = {
    "M0": "Current — 91-day median",
    "M1": "M1 — 91 days, best/3rd-best midpoint",
    "M2": "M2 — last 13 runs in 182 days, best/3rd-best midpoint",
    "M3": "M3 — asymmetric smoothing (½ down, ⅒ up)",
}
EXPLAIN = {
    "M0": "Your target is the <b>middle</b> of your times over the last 13 weeks "
          "(91 days): half your recent runs were faster than it, half slower. "
          "This is what the app uses today.",
    "M1": "Same 13 weeks, but the target is a <b>good day</b> rather than a "
          "typical one: take your best and your third-best time and split the "
          "difference. With only two runs, split the difference between them; "
          "with one, that run is the target.",
    "M2": "Look back up to <b>26 weeks</b> (182 days), but only at your "
          "<b>latest 13 runs</b> of the same kind (regular or buggy), then the "
          "same best/third-best midpoint and the same fallbacks for one or two "
          "runs. A weekly runner's 13 runs cover about the last three months; "
          "someone who runs less often reaches further back, so their target "
          "still rests on a decent number of runs.",
    "M3": "Think of the target as a <b>marker that moves after every run</b>. "
          "Beat it, and it jumps <b>half the way</b> to your new time at once. "
          "Miss it, and it creeps only <b>a tenth of the way</b> towards the "
          "slower time. So one good run raises the bar quickly; one bad run (a "
          "hilly course, a cold, a buggy day) barely moves it; but a long spell "
          "of slower runs does bring it back over a couple of months. It still "
          "needs a run of the same kind in the last 91 days, as now.",
}
VERDICTS = ["Winner changed", "Places changed", "Roster changed", "Gained",
            "Lost", "Margin only", "Unchanged"]


# --------------------------------------------------------------------------- #
# Target rules — one per method, each (series, date) -> (target, n)
# --------------------------------------------------------------------------- #
def _window(s: dict, d: np.datetime64, days: int) -> slice:
    """Runs in [d - days, d - 1], the app's window (the event day excluded)."""
    lo = np.searchsorted(s["dates"], d - np.timedelta64(days, "D"), "left")
    return slice(lo, np.searchsorted(s["dates"], d, "left"))


def best_third_mid(times) -> float:
    """Midpoint of best and 3rd-best; 2 runs -> their midpoint; 1 -> itself."""
    t = np.sort(np.asarray(times, dtype=float))
    return float((t[0] + t[2]) / 2 if len(t) >= 3 else t[: len(t)].mean())


def m0(s, d):
    t = s["times"][_window(s, d, TARGET_WINDOW_DAYS)]
    return (float(np.median(t)), len(t)) if len(t) else (np.nan, 0)


def m1(s, d):
    t = s["times"][_window(s, d, TARGET_WINDOW_DAYS)]
    return (best_third_mid(t), len(t)) if len(t) else (np.nan, 0)


def m2_runs(s, d) -> np.ndarray:
    return s["times"][_window(s, d, M2_DAYS)][-M2_RUNS:]   # dates ascending


def m2(s, d):
    t = m2_runs(s, d)
    return (best_third_mid(t), len(t)) if len(t) else (np.nan, 0)


def smooth(times, a_fast: float, a_slow: float) -> np.ndarray:
    """The target after each run: starts at the first run, then moves a_fast of
    the way to a faster time and a_slow of the way to a slower one."""
    out, T = [], None
    for t in times:
        T = t if T is None else T + (a_fast if t < T else a_slow) * (t - T)
        out.append(T)
    return np.array(out, dtype=float)


def m3_rule(a_fast: float, a_slow: float):
    key = ("smooth", a_fast, a_slow)

    def rule(s, d):
        n = len(s["times"][_window(s, d, TARGET_WINDOW_DAYS)])
        if n == 0:                       # same validity rule as today
            return np.nan, 0
        if key not in s:
            s[key] = smooth(s["times"], a_fast, a_slow)
        return float(s[key][np.searchsorted(s["dates"], d, "left") - 1]), n
    return rule


RULES = {"M0": m0, "M1": m1, "M2": m2, "M3": m3_rule(*M3_DEFAULT)}


def self_check() -> None:
    assert best_third_mid([1300, 1200, 1250, 1400]) == 1250      # (1200+1300)/2
    assert best_third_mid([1300, 1200]) == 1250
    assert best_third_mid([1300]) == 1300
    days = np.array(["2026-01-03", "2026-01-10", "2026-01-17"], "datetime64[D]")
    s = {"dates": days, "times": np.array([1300.0, 1200.0, 1400.0])}
    d = np.datetime64("2026-01-24")
    assert m1(s, d) == (1300.0, 3)
    assert m1(s, np.datetime64("2026-01-10")) == (1300.0, 1)      # event day excluded
    many = {"dates": np.arange(np.datetime64("2026-01-01"), np.datetime64("2026-01-21")),
            "times": np.arange(1000.0, 1020.0)}                    # 20 daily runs
    assert len(m2_runs(many, np.datetime64("2026-01-21"))) == 13
    assert m2(many, np.datetime64("2026-01-21"))[0] == 1008       # (1007+1009)/2
    sm = smooth([1300, 1200, 1400], 0.5, 0.1)
    assert list(sm) == [1300, 1250, 1265]                          # down ½, up ⅒


# --------------------------------------------------------------------------- #
# Head-to-heads under a rule — the app's arithmetic from the target on
# --------------------------------------------------------------------------- #
def round2(x):
    """DuckDB's round(): half away from zero."""
    return np.sign(x) * np.floor(np.abs(x) * 100 + 0.5) / 100


def targets(parts: pd.DataFrame, series: dict, handicap: dict, rule) -> pd.DataFrame:
    """Each participant's target, with the handicap bridge from the other mode
    when their own mode has no runs (× (1+h) for a buggy run, ÷ for regular)."""
    empty = {"dates": np.array([], "datetime64[D]"), "times": np.array([])}
    rows = []
    for r in parts.itertuples():
        d = np.datetime64(r.run_date, "D")
        other = "nonbuggy" if r.mode == "buggy" else "buggy"
        t, n = rule(series.get((r.athlete_id, r.mode), empty), d)
        basis = r.mode
        if n == 0:
            ot, n = rule(series.get((r.athlete_id, other), empty), d)
            h = 1 + handicap.get(r.athlete_id, HANDICAP_DEFAULT)
            t = ot * h if r.mode == "buggy" else ot / h
            basis = "nonbuggy+handicap" if r.mode == "buggy" else "buggy-handicap"
        rows.append((t, n, basis))
    out = parts.copy()
    out[["target_seconds", "n_window", "target_basis"]] = pd.DataFrame(rows, index=parts.index)
    return out


def rank(tg: pd.DataFrame) -> pd.DataFrame:
    v = tg[tg["n_window"] >= 1].copy()                       # demote rule
    v = v[v.groupby(["event_id", "run_date"])["athlete_id"].transform("size") >= 2]
    v["pct_diff"] = round2((v["actual_seconds"] - v["target_seconds"])
                           / v["target_seconds"] * 100)
    g = v.groupby(["event_id", "run_date"])
    v["place_rank"] = g["pct_diff"].rank(method="min").astype(int)
    v["n_ranked"] = g["athlete_id"].transform("size")
    v["classification"] = g["athlete_name"].transform(lambda s: " vs ".join(sorted(s)))
    return v


def check_m0(m0_h2h: pd.DataFrame, app: pd.DataFrame) -> None:
    k = ["event_id", "run_date", "athlete_id"]
    m = m0_h2h.merge(app, on=k, how="outer", suffixes=("", "_app"), indicator=True)
    assert (m["_merge"] == "both").all(), m[m["_merge"] != "both"][k + ["_merge"]]
    assert np.allclose(m["target_seconds"], m["target_seconds_app"])
    assert (m["pct_diff"] - m["pct_diff_app"]).abs().max() < 0.005
    assert (m["place_rank"] == m["place_rank_app"]).all()
    assert (m["target_basis"] == m["target_basis_app"]).all()


def _occ(df):
    return {k: g for k, g in df.groupby(["event_id", "run_date"])}


def verdict(n, o) -> str:
    """method_impact's ladder: first match wins (o = current, n = proposed)."""
    if o is None:
        return "Gained"
    if n is None:
        return "Lost"
    win = lambda g: frozenset(g.loc[g["place_rank"] == 1, "athlete_id"])  # noqa: E731
    if win(n) != win(o):
        return "Winner changed"
    if dict(zip(n["athlete_id"], n["place_rank"])) != dict(zip(o["athlete_id"], o["place_rank"])):
        return "Places changed"
    if len(n) != len(o):
        return "Roster changed"
    nm, om = _winning_margin(n), _winning_margin(o)
    if nm is not None and om is not None and abs(nm - om) >= 0.01:
        return "Margin only"
    return "Unchanged"


def verdicts(base: dict, other: dict) -> dict:
    return {k: verdict(other.get(k), base.get(k)) for k in set(base) | set(other)}


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
def load(db: str):
    con = duckdb.connect(db, read_only=True)
    q = lambda sql: con.execute(sql).df()  # noqa: E731
    runs = q("""SELECT r.athlete_id, a.athlete_name, r.event_id, r.run_date,
                       r.time_seconds, r.is_buggy, r.mode
                FROM parkrun.v_results_moded r JOIN parkrun.athletes a USING (athlete_id)
                WHERE r.time_seconds IS NOT NULL""")
    app = q("SELECT * FROM parkrun.v_head_to_head")
    events = q("SELECT event_id, short_name FROM parkrun.events")
    handicap = dict(q("SELECT athlete_id, handicap FROM parkrun.buggy_handicap").values)
    con.close()
    runs["run_date"] = pd.to_datetime(runs["run_date"])
    app["run_date"] = pd.to_datetime(app["run_date"])
    runs = runs.sort_values(["run_date", "time_seconds"], kind="stable")
    series = {k: {"dates": g["run_date"].values.astype("datetime64[D]"),
                  "times": g["time_seconds"].to_numpy(float)}
              for k, g in runs.groupby(["athlete_id", "mode"])}
    together = runs.groupby(["event_id", "run_date"])["athlete_id"].transform("size") >= 2
    parts = runs[together].rename(columns={"time_seconds": "actual_seconds"})
    parts = parts.merge(events, on="event_id").reset_index(drop=True)
    return runs, series, parts, app, handicap


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def fig_json(fig: go.Figure, lock: bool = True) -> dict:
    d = json.loads(pio.to_json(lock_zoom(fig) if lock else fig))
    d["layout"].pop("template", None)    # one shared template, set in the page
    return d


def bold(md: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(md, quote=False))


def table_html(rows: pd.DataFrame) -> str:
    d = rows.sort_values(["place_rank", "pct_diff"])
    basis = {"nonbuggy+handicap": "regular + handicap", "buggy-handicap": "🛒 ÷ handicap",
             "buggy": "🛒", "nonbuggy": "regular"}
    body = "".join(
        f"<tr><td>{MEDAL[int(r.place_rank)]}</td><td>{html.escape(r.athlete_name)}"
        f"{' 🛒' if r.is_buggy else ''}</td><td>{fmt_time(r.actual_seconds)}</td>"
        f"<td>{fmt_time(r.target_seconds)}</td><td>{r.pct_diff:+.2f}%</td>"
        f"<td>{basis.get(r.target_basis, '')}</td><td>{int(r.n_window)}</td></tr>"
        for r in d.itertuples())
    return ("<table class='res'><tr><th></th><th>Runner</th><th>Time</th><th>Target</th>"
            f"<th>vs form</th><th>Basis</th><th>Runs</th></tr>{body}</table>")


def occasion_panels(occ: dict, key) -> dict:
    """Per method: headline, victory chart and table, sharing one x-range."""
    present = {m: o[key] for m, o in occ.items() if key in o}
    pcts = pd.concat([g["pct_diff"] for g in present.values()])
    lo, hi = min(0.0, pcts.min()), max(0.0, pcts.max())
    pad = max(1.0, (hi - lo) * 0.30)
    out = {}
    for m, g in present.items():
        f = _victory_fig(g)
        f.update_xaxes(range=[hi + pad, lo - pad])
        out[m] = {"head": bold(_h2h_headline(g)), "fig": fig_json(f), "table": table_html(g)}
    return out


def scatter_fig(tg: dict) -> go.Figure:
    k = ["event_id", "run_date", "athlete_id"]
    base = tg["M0"][tg["M0"]["n_window"] >= 1]
    fig = go.Figure()
    colors = {"M1": "#e7298a", "M2": "#66a61e", "M3": "#1f78b4"}
    for m in ("M1", "M2", "M3"):
        x = base.merge(tg[m][tg[m]["n_window"] >= 1], on=k, suffixes=("", "_n"))
        x["chg"] = (x["target_seconds_n"] - x["target_seconds"]).abs() / x["target_seconds"] * 100
        x["who"] = x["athlete_name"] + np.where(x["is_buggy"], " 🛒", "")
        fig.add_trace(go.Scattergl(
            x=x["run_date"], y=x["chg"], mode="markers", name=METHODS[m],
            marker=dict(size=6, color=colors[m], opacity=0.6),
            customdata=np.stack([x["who"], x["short_name"],
                                 x["target_seconds"].map(fmt_time),
                                 x["target_seconds_n"].map(fmt_time)], axis=1),
            hovertemplate="%{x|%d %b %Y} · %{customdata[1]}<br><b>%{customdata[0]}</b><br>"
                          "current %{customdata[2]} → %{customdata[3]}<br>"
                          "%{y:.2f}% change<extra></extra>"))
    fig.update_layout(height=460, margin=dict(t=20, b=40, l=50, r=10),
                      yaxis=dict(title="|target change| vs current (%)", ticksuffix="%"),
                      legend=dict(orientation="h", y=1.08))
    return fig


FACETS = [("George", "nonbuggy"), ("George", "buggy"), ("Raju", "nonbuggy"),
          ("Duncan", "nonbuggy"), ("Duncan", "buggy")]


def spread_frame(runs: pd.DataFrame, series: dict, method: str, start) -> pd.DataFrame:
    """Every Saturday, per athlete × mode: each run in the method's window as a
    % from that Saturday's target -> min, Q1, median, Q3, max. No bridge, as in
    v_saturday_targets."""
    rule = RULES[method]
    sats = pd.date_range(start, runs["run_date"].max(), freq="W-SAT")
    ids = dict(zip(runs["athlete_name"], runs["athlete_id"]))
    rows = []
    for name, mode in FACETS:
        s = series.get((ids[name], mode))
        if s is None:
            continue
        for sat in sats:
            d = np.datetime64(sat.date(), "D")
            t, n = rule(s, d)
            if n == 0:
                rows.append((name, mode, sat, *[np.nan] * 5))
                continue
            w = m2_runs(s, d) if method == "M2" else s["times"][_window(s, d, TARGET_WINDOW_DAYS)]
            p = (w - t) / t * 100
            rows.append((name, mode, sat, p.min(), *np.percentile(p, [25, 50, 75]), p.max()))
    return pd.DataFrame(rows, columns=["athlete", "mode", "sat", "min", "q1", "med", "q3", "max"])


def spread_fig(sp: pd.DataFrame) -> go.Figure:
    fig = make_subplots(rows=len(FACETS), cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=[f"{a}{' 🛒' if m == 'buggy' else ''}" for a, m in FACETS])
    for i, (a, m) in enumerate(FACETS, start=1):
        d = sp[(sp["athlete"] == a) & (sp["mode"] == m)]
        c = ATHLETE_COLORS[a]
        def band(top, bot, alpha, name):
            fig.add_trace(go.Scatter(x=d["sat"], y=d[top], mode="lines", line=dict(width=0),
                                     hoverinfo="skip", showlegend=False), row=i, col=1)
            fig.add_trace(go.Scatter(x=d["sat"], y=d[bot], mode="lines", line=dict(width=0),
                                     fill="tonexty", fillcolor=_rgba(c, alpha), name=name,
                                     showlegend=i == 1, legendgroup=name, hoverinfo="skip"),
                          row=i, col=1)
        band("max", "min", 0.18, "best – worst")
        band("q3", "q1", 0.45, "Q1 – Q3")
        fig.add_trace(go.Scatter(
            x=d["sat"], y=d["med"], mode="lines", line=dict(color=c, width=1, dash="dot"),
            name="median run", showlegend=i == 1, legendgroup="median",
            customdata=d[["min", "q1", "q3", "max"]].to_numpy(),
            hovertemplate="%{x|%d %b %Y}<br>best %{customdata[0]:+.1f}% · Q1 %{customdata[1]:+.1f}%"
                          "<br>median %{y:+.1f}% · Q3 %{customdata[2]:+.1f}%"
                          "<br>worst %{customdata[3]:+.1f}%<extra></extra>"), row=i, col=1)
        fig.add_hline(y=0, line=dict(color="#555", width=1), row=i, col=1)
        # Clipped: one walked run reads +150% and would flatten every band.
        fig.update_yaxes(ticksuffix="%", range=[-20, 40], row=i, col=1)
    fig.update_layout(height=230 * len(FACETS), margin=dict(t=40, b=30, l=50, r=10),
                      legend=dict(orientation="h", y=1.02), hovermode="x")
    return fig


def _rgba(hexc: str, a: float) -> str:
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{a})"


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def summary_tables(occ: dict, vs: dict, grid: dict):
    rows = []
    for m in METHODS:
        df = pd.concat(occ[m].values())
        firsts = df[df["place_rank"] == 1].groupby("athlete_name").size()
        row = {"method": m, "head_to_heads": len(occ[m])}
        if m != "M0":
            row |= {v: sum(x == v for x in vs[m].values()) for v in VERDICTS}
        row |= {f"1sts {a}": int(firsts.get(a, 0)) for a in ATHLETE_COLORS}
        rows.append(row)
    summ = pd.DataFrame(rows).fillna("").astype(str).replace(r"\.0$", "", regex=True)
    g = pd.DataFrame([{"a_fast": f, "a_slow": s, **{v: sum(x == v for x in d.values())
                                                     for v in VERDICTS[:3]}}
                      for (f, s), d in grid.items()])
    return summ, g


def page(summ, grid, occ_index, panels, scatter, spreads) -> str:
    explain = "".join(f"<div class='card'><h3>{html.escape(METHODS[m])}</h3><p>{EXPLAIN[m]}</p></div>"
                      for m in METHODS)
    template = json.loads(pio.to_json(go.Figure(layout=dict(template="plotly_white"))))["layout"]["template"]
    data = {"occ": occ_index, "panels": panels, "scatter": fig_json(scatter, lock=False),
            "spreads": spreads, "methods": METHODS, "template": template,
            "config": PLOTLY_CONFIG}
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Target methods</title>
<script src="{PLOTLY_JS}"></script>
<style>
 body{{font:15px/1.45 system-ui,-apple-system,sans-serif;margin:0;padding:0 16px 48px;background:#fafafa;color:#1a1a1a}}
 main{{max-width:1500px;margin:auto}} h1{{margin:24px 0 4px}} h2{{margin-top:40px;border-bottom:1px solid #ddd;padding-bottom:4px}}
 .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
 .card{{background:#fff;border:1px solid #e3e3e3;border-radius:8px;padding:10px 14px}} .card h3{{margin:4px 0;font-size:15px}}
 table{{border-collapse:collapse;font-variant-numeric:tabular-nums}} td,th{{padding:4px 10px;border-bottom:1px solid #eee;text-align:right}}
 td:first-child,th:first-child{{text-align:left}} .wrap{{overflow-x:auto}}
 .controls{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0 14px}}
 select,button{{font:inherit;padding:4px 8px}} #occsel{{max-width:100%}}
 .cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}}
 .col{{min-width:0;background:#fff;border:1px solid #e3e3e3;border-radius:8px;padding:10px}} .col h3{{margin:0 0 6px;font-size:14px}}
 .badge{{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;background:#eee;margin-left:6px}}
 .badge.Winner{{background:#ffd6d6}} .badge.Places{{background:#ffe9c2}} .badge.Roster,.badge.Gained,.badge.Lost{{background:#e3dcff}}
 .res td,.res th{{font-size:13px;padding:2px 6px}} .muted{{color:#777}}
</style></head><body><main>
<h1>Head-to-head target methods</h1>
<p class="muted">Every head-to-head re-scored under each method. Only the target changes; the
handicap bridge, the demote rule, the % against form and the ranking are the app's own. The
current method is reproduced exactly before anything is compared.</p>
<h2>The methods</h2><div class="cards">{explain}</div>
<h2>How many results change</h2>
<p class="muted">Each proposed method against the current one, per head-to-head. First match wins:
winner changed › places changed › roster changed (someone ranked or demoted) › gained/lost
(a head-to-head under one method only) › margin only (≥ 0.01 pts) › unchanged.</p>
<div class="wrap">{summ.to_html(index=False, border=0)}</div>
<h3>M3 with other speeds</h3>
<p class="muted">a_fast = how far a faster run pulls the target; a_slow = how far a slower one does.
Counts of head-to-heads that change against the current method.</p>
<div class="wrap">{grid.to_html(index=False, border=0)}</div>
<h2>Side by side</h2>
<div class="controls">
 <label><input type="checkbox" id="changed" checked> changed under any method</label>
 <select id="cls"></select><select id="yr"></select>
 <button id="prev">‹</button><select id="occsel"></select><button id="next">›</button>
 <span id="count" class="muted"></span></div>
<div class="cols" id="cols"></div>
<h2>How far each target moves</h2>
<p class="muted">One point per runner per head-to-head: the size of the change from the current target,
as a % of it.</p><div id="scatter"></div>
<h2>Spread of runs around the target</h2>
<p class="muted">Every Saturday, per runner and kind of run: each run in that method's window, as a % from
the target it gives that Saturday, from the first head-to-head on. Light band best–worst, dark band Q1–Q3, dotted line the median run,
solid 0 line the target. Under the current method the dotted line sits on 0 by definition. Negative
is faster than the target. The y-axis stops at −20% and +40% (a walked run would flatten the
bands); hover for the exact figures, drag to zoom, double-click to reset.</p>
<div class="controls"><select id="spm"></select></div><div id="spread"></div>
</main><script>
const D={json.dumps(data, separators=(",", ":"), default=str)};
const M=Object.keys(D.methods), cfg=D.config;
function draw(el,f,cfg2){{f.layout.template=D.template;f.layout.autosize=true;Plotly.react(el,f.data,f.layout,Object.assign({{responsive:true}},cfg2||cfg))}}
const cls=document.getElementById('cls'),yr=document.getElementById('yr'),sel=document.getElementById('occsel');
const opt=(s,v,t)=>{{const o=document.createElement('option');o.value=v;o.textContent=t;s.appendChild(o)}};
opt(cls,'','All classifications');[...new Set(D.occ.map(o=>o.cls))].sort().forEach(c=>opt(cls,c,c));
opt(yr,'','All years');[...new Set(D.occ.map(o=>o.year))].sort().reverse().forEach(y=>opt(yr,y,y));
function fill(){{const ch=document.getElementById('changed').checked;sel.innerHTML='';
 const list=D.occ.filter(o=>(!ch||o.changed)&&(!cls.value||o.cls===cls.value)&&(!yr.value||o.year==yr.value));
 list.forEach(o=>opt(sel,o.key,o.label));document.getElementById('count').textContent=list.length+' head-to-heads';show()}}
function show(){{const c=document.getElementById('cols');c.innerHTML='';const p=D.panels[sel.value];if(!p)return;
 const o=D.occ.find(o=>o.key===sel.value);
 const todo=[];M.forEach(m=>{{const d=document.createElement('div');d.className='col';const v=o.v[m]||'';
  d.innerHTML='<h3>'+D.methods[m]+(v?'<span class="badge '+v.split(' ')[0]+'">'+v+'</span>':'')+'</h3>';
  if(p[m]){{d.innerHTML+='<div>'+p[m].head+'</div>';const g=document.createElement('div');d.appendChild(g);
   d.insertAdjacentHTML('beforeend',p[m].table);c.appendChild(d);todo.push([g,p[m].fig])}}
  else{{d.innerHTML+='<p class="muted">Not a head-to-head under this method: fewer than two runners had a target.</p>';c.appendChild(d)}}}});todo.forEach(([g,f])=>draw(g,structuredClone(f)))}}
['changed','cls','yr'].forEach(i=>document.getElementById(i).onchange=fill);sel.onchange=show;
document.getElementById('prev').onclick=()=>{{if(sel.selectedIndex>0){{sel.selectedIndex--;show()}}}};
document.getElementById('next').onclick=()=>{{if(sel.selectedIndex<sel.options.length-1){{sel.selectedIndex++;show()}}}};
fill();draw(document.getElementById('scatter'),D.scatter,{{displayModeBar:true}});
const spm=document.getElementById('spm');M.forEach(m=>opt(spm,m,D.methods[m]));
spm.onchange=()=>draw(document.getElementById('spread'),structuredClone(D.spreads[spm.value]),{{displayModeBar:true}});spm.onchange();
</script></body></html>"""


def main() -> None:
    self_check()
    runs, series, parts, app, handicap = load(resolve_db())
    tg = {m: targets(parts, series, handicap, r) for m, r in RULES.items()}
    h2h = {m: rank(t) for m, t in tg.items()}
    check_m0(h2h["M0"], app)
    print(f"M0 reproduces v_head_to_head: {len(app)} rows, "
          f"{app.groupby(['event_id', 'run_date']).ngroups} head-to-heads")
    occ = {m: _occ(h) for m, h in h2h.items()}
    vs = {m: verdicts(occ["M0"], occ[m]) for m in METHODS if m != "M0"}
    grid = {(f, s): verdicts(occ["M0"], _occ(rank(targets(parts, series, handicap, m3_rule(f, s)))))
            for f, s in M3_GRID}
    summ, grid_df = summary_tables(occ, vs, grid)
    print(summ.to_string(index=False))

    keys = sorted(set().union(*occ.values()), key=lambda k: k[1], reverse=True)
    occ_index, panels = [], {}
    for k in keys:
        sid = f"{k[0]}|{k[1]:%Y-%m-%d}"
        any_g = next(o[k] for o in occ.values() if k in o)
        v = {m: vs[m][k] for m in vs}
        occ_index.append({
            "key": sid, "year": int(k[1].year), "cls": (occ["M0"].get(k, any_g))["classification"].iloc[0],
            "changed": any(x not in ("Unchanged", "Margin only") for x in v.values()), "v": v,
            "label": f"{k[1]:%d %b %Y} · {any_g['short_name'].iloc[0]} · "
                     f"{(occ['M0'].get(k, any_g))['classification'].iloc[0]}"})
        panels[sid] = occasion_panels(occ, k)
    start = app["run_date"].min()              # the first head-to-head
    spreads = {m: fig_json(spread_fig(spread_frame(runs, series, m, start)), lock=False)
               for m in METHODS}

    OUTPUT.parent.mkdir(exist_ok=True)
    SUMMARY.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(page(summ, grid_df, occ_index, panels, scatter_fig(tg), spreads))
    summ.to_csv(SUMMARY, index=False)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1e6:.1f} MB) and {SUMMARY}")


if __name__ == "__main__":
    main()
