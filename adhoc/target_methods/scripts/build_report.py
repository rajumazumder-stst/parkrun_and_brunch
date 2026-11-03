"""Compare head-to-head target methods: the current 91-day median against three
proposals, and write one standalone HTML report.

    python adhoc/target_methods/scripts/build_report.py

Reads the database `parkrun_core.resolve_db()` picks (local source of truth,
else the committed snapshot), read-only. Writes `output/target_methods.html`
(gitignored) and `results/summary.csv` (tracked). Changes nothing in the app.

Everything except the target is the app's own arithmetic, re-done in pandas so
each target can carry its working: the handicap bridge, the demote rule,
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
M2_DAYS, M2_RUNS = 182, 13       # M2 and M4: the latest 13 runs within 26 weeks

METHODS = {
    "M0": "Current — 91-day median",
    "M1": "M1 — 91 days, best/3rd-best midpoint",
    "M2": "M2 — last 13 runs in 182 days, best/3rd-best midpoint",
    "M4": "M4 — last 13 runs in 182 days, median",
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
    "M4": "The same <b>latest 13 runs within 26 weeks</b> as M2, but the target "
          "is their <b>middle</b> time, as today. So it is the current method "
          "with a fixed number of runs instead of a fixed number of weeks: a "
          "busy spell no longer piles 15 runs into the window, and a quiet one "
          "reaches back further instead of resting on one or two.",
}
def pct2(v) -> str:
    """A signed % to 2 dp for hover text; blank where there is no value."""
    return "" if pd.isna(v) else f"{v:+.2f}%"


PCT = ".2~f"      # every % on an axis: at most 2 dp, trailing zeros dropped
VERDICTS = ["Winner changed", "Places changed", "Roster changed", "Gained",
            "Lost", "Margin only", "Unchanged"]


# --------------------------------------------------------------------------- #
# Target rules — one per method, each (series, date) -> (target, n, how)
# --------------------------------------------------------------------------- #
def _window(s: dict, d: np.datetime64, days: int) -> slice:
    """Runs in [d - days, d - 1], the app's window (the event day excluded)."""
    lo = np.searchsorted(s["dates"], d - np.timedelta64(days, "D"), "left")
    return slice(lo, np.searchsorted(s["dates"], d, "left"))


def best_third_mid(times) -> float:
    """Midpoint of best and 3rd-best; 2 runs -> their midpoint; 1 -> itself."""
    t = np.sort(np.asarray(times, dtype=float))
    return float((t[0] + t[2]) / 2 if len(t) >= 3 else t[: len(t)].mean())


def _day(d) -> str:
    return pd.Timestamp(d).strftime("%-d %b %Y")


def how_median(dates, times) -> str:
    """Odd count: the one run the median is (its date). Even: the two middle times."""
    n, o = len(times), np.argsort(times, kind="stable")
    if n % 2:
        return f"median of {n} run{'s' * (n > 1)}: the run on {_day(dates[o[n // 2]])}"
    a, b = times[o[n // 2 - 1]], times[o[n // 2]]
    return f"median of {n} runs: midpoint of {fmt_time(a)} and {fmt_time(b)}"


def how_best3(dates, times) -> str:
    n, t = len(times), np.sort(times)
    if n >= 3:
        return (f"of {n} runs: best {fmt_time(t[0])} · 2nd {fmt_time(t[1])} · "
                f"3rd {fmt_time(t[2])} → midpoint of best and 3rd")
    if n == 2:
        return f"2 runs: midpoint of {fmt_time(t[0])} and {fmt_time(t[1])}"
    return f"1 run: the run on {_day(dates[0])}"


def window_runs(method: str, s: dict, d) -> tuple:
    """(dates, times) the method's target is taken over."""
    w = _window(s, d, M2_DAYS if method in ("M2", "M4") else TARGET_WINDOW_DAYS)
    dates, times = s["dates"][w], s["times"][w]
    if method in ("M2", "M4"):                 # latest 13; dates ascending
        dates, times = dates[-M2_RUNS:], times[-M2_RUNS:]
    return dates, times


def make_rule(method: str):
    median = method in ("M0", "M4")

    def rule(s, d):
        dates, times = window_runs(method, s, d)
        if not len(times):
            return np.nan, 0, ""
        if median:
            return float(np.median(times)), len(times), how_median(dates, times)
        return best_third_mid(times), len(times), how_best3(dates, times)
    return rule


RULES = {m: make_rule(m) for m in METHODS}


def self_check() -> None:
    assert best_third_mid([1300, 1200, 1250, 1400]) == 1250      # (1200+1300)/2
    assert best_third_mid([1300, 1200]) == 1250
    assert best_third_mid([1300]) == 1300
    days = np.array(["2026-01-03", "2026-01-10", "2026-01-17"], "datetime64[D]")
    s = {"dates": days, "times": np.array([1300.0, 1200.0, 1400.0])}
    d = np.datetime64("2026-01-24")
    assert RULES["M1"](s, d)[:2] == (1300.0, 3)
    assert RULES["M1"](s, np.datetime64("2026-01-10"))[:2] == (1300.0, 1)   # event day excluded
    assert RULES["M0"](s, d) == (1300.0, 3, "median of 3 runs: the run on 3 Jan 2026")
    assert RULES["M1"](s, d)[2].startswith("of 3 runs: best 20:00 · 2nd 21:40 · 3rd 23:20")
    assert RULES["M1"](s, np.datetime64("2026-01-10"))[2] == "1 run: the run on 3 Jan 2026"
    two = {"dates": days[:2], "times": np.array([1300.0, 1200.0])}
    assert RULES["M0"](two, d)[2] == "median of 2 runs: midpoint of 20:00 and 21:40"
    many = {"dates": np.arange(np.datetime64("2026-01-01"), np.datetime64("2026-01-21")),
            "times": np.arange(1000.0, 1020.0)}                    # 20 daily runs
    assert len(window_runs("M2", many, np.datetime64("2026-01-21"))[1]) == 13
    assert RULES["M2"](many, np.datetime64("2026-01-21"))[0] == 1008       # (1007+1009)/2
    assert RULES["M4"](many, np.datetime64("2026-01-21"))[:2] == (1013.0, 13)   # median 1007..1019


# --------------------------------------------------------------------------- #
# Head-to-heads under a rule — the app's arithmetic from the target on
# --------------------------------------------------------------------------- #
KIND = {"buggy": "buggy", "nonbuggy": "regular"}


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
        t, n, how = rule(series.get((r.athlete_id, r.mode), empty), d)
        basis = r.mode
        if n == 0:
            ot, n, ohow = rule(series.get((r.athlete_id, other), empty), d)
            h = 1 + handicap.get(r.athlete_id, HANDICAP_DEFAULT)
            t = ot * h if r.mode == "buggy" else ot / h
            basis = "nonbuggy+handicap" if r.mode == "buggy" else "buggy-handicap"
            if n:
                how = (f"no {KIND[r.mode]} runs → {KIND[other]} target {fmt_time(ot)} "
                       f"{'×' if r.mode == 'buggy' else '÷'} {h:.2f} handicap = {fmt_time(t)}"
                       f"; {KIND[other]} target: {ohow}")
        rows.append((t, n, basis, how))
    out = parts.copy()
    out[["target_seconds", "n_window", "target_basis", "how"]] = pd.DataFrame(rows, index=parts.index)
    return out


def rank(tg: pd.DataFrame) -> pd.DataFrame:
    v = tg[tg["n_window"] >= 1].copy()                       # demote rule
    v = v[v.groupby(["event_id", "run_date"])["athlete_id"].transform("size") >= 2]
    v["pct_diff"] = round2((v["actual_seconds"] - v["target_seconds"])
                           / v["target_seconds"] * 100)
    g = v.groupby(["event_id", "run_date"])
    v["place_rank"] = g["pct_diff"].rank(method="min").astype(int)
    v["n_ranked"] = g["athlete_id"].transform("size")
    order = list(ATHLETE_COLORS).index
    v["classification"] = g["athlete_name"].transform(lambda s: " vs ".join(sorted(s, key=order)))
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
    # The working goes under the table, not in a column: a 4-column panel is
    # too narrow for it, and a column made every panel scroll sideways.
    how = "".join(f"<li><b>{html.escape(r.athlete_name)}{' 🛒' if r.is_buggy else ''}</b>: "
                  f"{html.escape(r.how)}</li>" for r in d.itertuples())
    return ("<table class='res'><tr><th></th><th>Runner</th><th>Time</th><th>Target</th>"
            f"<th>vs form</th><th>Basis</th><th>Runs</th></tr>{body}</table>"
            f"<ul class='how'>{how}</ul>")


def occasion_panels(occ: dict, key) -> dict:
    """Per method: headline, victory chart and table, sharing one x-range."""
    present = {m: o[key] for m, o in occ.items() if key in o}
    pcts = pd.concat([g["pct_diff"] for g in present.values()])
    lo, hi = min(0.0, pcts.min()), max(0.0, pcts.max())
    pad = max(1.0, (hi - lo) * 0.30)
    out = {}
    for m, g in present.items():
        f = _victory_fig(g)
        f.update_xaxes(range=[hi + pad, lo - pad], tickformat=PCT)
        out[m] = {"head": bold(_h2h_headline(g)), "fig": fig_json(f), "table": table_html(g)}
    return out


def scatter_rows(tg: dict) -> list:
    """One point per runner per head-to-head per proposed method: how far its
    target moved from the current one, as a % of it. Plain rows, not a figure:
    the page builds the chart itself so the year filter can drop points and the
    best-fit lines can be refitted to what is shown."""
    k = ["event_id", "run_date", "athlete_id"]
    base = tg["M0"][tg["M0"]["n_window"] >= 1]
    out = []
    for m in (m for m in METHODS if m != "M0"):
        x = base.merge(tg[m][tg[m]["n_window"] >= 1], on=k, suffixes=("", "_n"))
        x["chg"] = (x["target_seconds_n"] - x["target_seconds"]).abs() / x["target_seconds"] * 100
        for r in x.itertuples():
            out.append({"date": f"{r.run_date:%Y-%m-%d}", "chg": round(float(r.chg), 4),
                        "ath": r.athlete_name, "mode": r.mode, "meth": m,
                        "who": r.athlete_name + (" 🛒" if r.is_buggy else ""),
                        "parkrun": r.short_name, "cur": fmt_time(r.target_seconds),
                        "new": fmt_time(r.target_seconds_n), "txt": f"{r.chg:.2f}%"})
    return out


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
            t, n, _ = rule(s, d)
            if n == 0:
                rows.append((name, mode, sat, *[np.nan] * 6))
                continue
            w = window_runs(method, s, d)[1]
            p = (w - t) / t * 100
            rows.append((name, mode, sat, p.min(), *np.percentile(p, [25, 50, 75]), p.max(), t))
    return pd.DataFrame(rows, columns=["athlete", "mode", "sat", "min", "q1", "med", "q3", "max",
                                       "target"])


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
            # Labels formatted here, not by plotly: d3 formats were not applied
            # to these values in the browser and showed 13 decimal places.
            customdata=d[["min", "q1", "med", "q3", "max"]].map(pct2).to_numpy(),
            hovertemplate="%{x|%d %b %Y}<br>best %{customdata[0]} · Q1 %{customdata[1]}"
                          "<br>median %{customdata[2]} · Q3 %{customdata[3]}"
                          "<br>worst %{customdata[4]}<extra></extra>"), row=i, col=1)
        fig.add_hline(y=0, line=dict(color="#555", width=1), row=i, col=1)
        # Clipped: one walked run reads +150% and would flatten every band.
        fig.update_yaxes(ticksuffix="%", tickformat=PCT, range=[-20, 40], row=i, col=1)
    fig.update_layout(height=230 * len(FACETS), margin=dict(t=40, b=30, l=50, r=10),
                      legend=dict(orientation="h", y=1.02), hovermode="x")
    return fig


def _rgba(hexc: str, a: float) -> str:
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{a})"


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def summary_table(occ: dict, vs: dict) -> pd.DataFrame:
    rows = []
    for m in METHODS:
        df = pd.concat(occ[m].values())
        firsts = df[df["place_rank"] == 1].groupby("athlete_name").size()
        row = {"method": m, "head_to_heads": len(occ[m])}
        if m != "M0":
            row |= {v: sum(x == v for x in vs[m].values()) for v in VERDICTS}
        row |= {f"1sts {a}": int(firsts.get(a, 0)) for a in ATHLETE_COLORS}
        rows.append(row)
    return pd.DataFrame(rows).fillna("").astype(str).replace(r"\.0$", "", regex=True)


def boxes(key: str, opts: dict) -> str:
    return "".join(f'<label><input type="checkbox" data-k="{key}" value="{html.escape(str(v))}" '
                   f'checked> {html.escape(str(t))}</label>' for v, t in opts.items())


DASH = {"M0": "solid", "M1": "dot", "M2": "dash", "M4": "dashdot"}


def trend_fig(frames: dict) -> go.Figure:
    """One line per athlete × mode × method: the target on every Saturday.
    meta carries the three keys the page's checkboxes filter on."""
    fig = go.Figure()
    allt = pd.concat(f["target"] for f in frames.values()).dropna()
    for name, mode in FACETS:
        for m, f in frames.items():
            d = f[(f["athlete"] == name) & (f["mode"] == mode)]
            if d["target"].notna().sum() == 0:
                continue
            label = f"{name}{' 🛒' if mode == 'buggy' else ''} · {m}"
            fig.add_trace(go.Scatter(
                x=d["sat"], y=d["target"], mode="lines", name=label,
                meta={"ath": name, "mode": mode, "meth": m},
                line=dict(color=ATHLETE_COLORS[name], dash=DASH[m],
                          width=2 if mode == "nonbuggy" else 1.5),
                opacity=1 if mode == "nonbuggy" else 0.6,
                customdata=d["target"].map(fmt_time),
                hovertemplate=f"<b>{label}</b><br>%{{x|%d %b %Y}}: %{{customdata}}<extra></extra>"))
    step = 120                                    # a tick every 2 minutes
    ticks = np.arange(allt.min() // step * step, allt.max() + step, step)
    fig.update_layout(height=520, margin=dict(t=20, b=40, l=60, r=10), hovermode="closest",
                      legend=dict(font=dict(size=11)),
                      yaxis=dict(title="target time", tickvals=ticks,
                                 ticktext=[fmt_time(t) for t in ticks]))
    return fig


def calc_rows(h2h: dict) -> list:
    """One row per runner per head-to-head per method: the working behind each
    target that decided a result. Newest first."""
    out = []
    for m, h in h2h.items():
        for r in h.itertuples():
            out.append({"date": f"{r.run_date:%Y-%m-%d}", "day": _day(r.run_date),
                        "parkrun": r.short_name, "ath": r.athlete_name, "mode": r.mode,
                        "meth": m, "place": MEDAL[int(r.place_rank)],
                        "time": fmt_time(r.actual_seconds), "target": fmt_time(r.target_seconds),
                        "pct": f"{r.pct_diff:+.2f}%", "n": int(r.n_window), "how": r.how})
    return sorted(out, key=lambda x: (x["date"], x["parkrun"], x["ath"], x["meth"]), reverse=True)


SCATTER_COLORS = {"M1": "#e7298a", "M2": "#66a61e", "M4": "#1f78b4"}


def page(summ, occ_index, panels, scatter, spreads, trend, calc) -> str:
    explain = "".join(f"<div class='card'><h3>{html.escape(METHODS[m])}</h3><p>{EXPLAIN[m]}</p></div>"
                      for m in METHODS)
    template = json.loads(pio.to_json(go.Figure(layout=dict(template="plotly_white"))))["layout"]["template"]
    years = sorted({r["date"][:4] for r in scatter}, reverse=True)      # newest first
    data = {"occ": occ_index, "panels": panels, "scatter": scatter, "scatterOrigin": int(years[-1]),
            "scatterColors": SCATTER_COLORS,
            "spreads": spreads, "trend": trend, "calc": calc, "methods": METHODS, "template": template,
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
 .col{{min-width:0;overflow-x:auto;background:#fff;border:1px solid #e3e3e3;border-radius:8px;padding:10px}} .col h3{{margin:0 0 6px;font-size:14px}}
 .badge{{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;background:#eee;margin-left:6px}}
 .badge.Winner{{background:#ffd6d6}} .badge.Places{{background:#ffe9c2}} .badge.Roster,.badge.Gained,.badge.Lost{{background:#e3dcff}}
 .res td,.res th{{font-size:13px;padding:2px 3px}}
 fieldset{{border:1px solid #e3e3e3;border-radius:8px;padding:4px 10px;display:flex;flex-wrap:wrap;gap:4px 12px}}
 legend{{font-size:12px;color:#777;padding:0 4px}} .muted{{color:#777}}
 ul.fits{{list-style:none;padding:0;margin:6px 0;display:grid;gap:4px;font-variant-numeric:tabular-nums}}
 ul.how{{margin:6px 0 0;padding-left:18px;font-size:12px;color:#444}}
 td.how{{text-align:left;font-size:12px;color:#444;min-width:16em}}
 #calc{{max-height:70vh;overflow:auto;border:1px solid #e3e3e3;border-radius:8px;background:#fff}}
 #calc table{{width:100%}} #calc th{{position:sticky;top:0;background:#fff}} #calc td,#calc th{{font-size:13px}}
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
<h2>Side by side</h2>
<div class="controls">
 <label><input type="checkbox" id="changed" checked> changed under any method</label>
 <select id="cls"></select><select id="yr"></select>
 <button id="prev">‹</button><select id="occsel"></select><button id="next">›</button>
 <span id="count" class="muted"></span></div>
<div class="cols" id="cols"></div>
<h2>How far each target moves</h2>
<p class="muted">One point per runner per head-to-head: the size of the change from the current target,
as a % of it. Colour is the method, shape the runner (● George, ■ Raju, ◆ Duncan), and an open shape a
buggy run. Tick boxes to choose points. The dashed lines are least-squares lines of best fit, one per
method, refitted to the points shown; their equations are under the chart.</p>
<div class="controls" id="scatterctl">
 <fieldset><legend>Runner</legend>{boxes("ath", {n: n for n in ATHLETE_COLORS})}</fieldset>
 <fieldset><legend>Mode</legend>{boxes("mode", {"nonbuggy": "Regular", "buggy": "Buggy 🛒"})}</fieldset>
 <fieldset><legend>Method</legend>{boxes("meth", {m: m for m in METHODS if m != "M0"})}</fieldset>
 <fieldset><legend>Year</legend>{boxes("year", {y: y for y in years})}</fieldset>
</div><div id="scatter"></div><div id="fits"></div>
<h2>Target time over time</h2>
<p class="muted">Each method's target on every Saturday from the first head-to-head on, per runner and kind of
run (no bridge, as on the app's Form tab). Colour is the runner, line style the method, and the 🛒 lines are
the buggy targets. A gap means no runs of that kind in the window. Tick boxes to choose lines; drag to zoom,
double-click to reset.</p>
<div class="controls" id="trendctl">
 <fieldset><legend>Runner</legend>{boxes("ath", {n: n for n in ATHLETE_COLORS})}</fieldset>
 <fieldset><legend>Mode</legend>{boxes("mode", {"nonbuggy": "Regular", "buggy": "Buggy 🛒"})}</fieldset>
 <fieldset><legend>Method</legend>{boxes("meth", {m: m + (" (current)" if m == "M0" else "") for m in METHODS})}</fieldset>
</div><div id="trend"></div>
<h2>Spread of runs around the target</h2>
<p class="muted">Every Saturday, per runner and kind of run: each run in that method's window, as a % from
the target it gives that Saturday, from the first head-to-head on. Light band best–worst, dark band Q1–Q3, dotted line the median run,
solid 0 line the target. Under the current method the dotted line sits on 0 by definition. Negative
is faster than the target. The y-axis stops at −20% and +40% (a walked run would flatten the
bands); hover for the exact figures, drag to zoom, double-click to reset.</p>
<div class="controls"><select id="spm"></select></div><div id="spread"></div>
<h2>How each target was worked out</h2>
<p class="muted">One row per runner per head-to-head per method: the target that decided the result, and the
runs behind it. A median of an odd number of runs is one run, shown by its date; of an even number, the
midpoint of the two middle times. M1 and M2 list the best, 2nd and 3rd times (or fewer, when there were fewer
runs). Where a runner had no runs of that kind, the target was borrowed from the other kind through the buggy
handicap, and the row says so. Tick boxes to choose rows.</p>
<div class="controls" id="calcctl">
 <fieldset><legend>Runner</legend>{boxes("ath", {n: n for n in ATHLETE_COLORS})}</fieldset>
 <fieldset><legend>Mode</legend>{boxes("mode", {"nonbuggy": "Regular", "buggy": "Buggy 🛒"})}</fieldset>
 <fieldset><legend>Method</legend>{boxes("meth", {m: m + (" (current)" if m == "M0" else "") for m in METHODS})}</fieldset>
 <span class="count" id="calccount"></span>
</div><div id="calc"></div>
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
fill();
function filterable(ctl,el,fig){{draw(el,fig,{{displayModeBar:true}});
 const vis=()=>{{const on=k=>[...document.querySelectorAll('#'+ctl+' input[data-k="'+k+'"]:checked')].map(i=>i.value);
  const a=on('ath'),md=on('mode'),me=on('meth');
  Plotly.restyle(el,{{visible:fig.data.map(t=>a.includes(t.meta.ath)&&md.includes(t.meta.mode)&&me.includes(t.meta.meth))}})}};
 document.querySelectorAll('#'+ctl+' input').forEach(i=>i.onchange=vis);vis()}}
const SCOL=D.scatterColors,SYM={{George:'circle',Raju:'square',Duncan:'diamond'}};
const T0=Date.UTC(D.scatterOrigin,0,1),YR=365.25*864e5,f2=v=>v.toFixed(2);
function scatterDraw(){{const on=k=>[...document.querySelectorAll('#scatterctl input[data-k="'+k+'"]:checked')].map(i=>i.value);
 const a=on('ath'),md=on('mode'),me=on('meth'),yrs=on('year');
 const pts=D.scatter.filter(p=>a.includes(p.ath)&&md.includes(p.mode)&&me.includes(p.meth)&&yrs.includes(p.date.slice(0,4)));
 const groups={{}};pts.forEach(p=>{{(groups[p.ath+'|'+p.mode+'|'+p.meth]??=[]).push(p)}});
 const traces=Object.entries(groups).map(([k,g])=>{{const [ath,mode,meth]=k.split('|');return {{type:'scattergl',mode:'markers',
  name:ath+(mode==='buggy'?' 🛒':'')+' · '+meth,x:g.map(p=>p.date),y:g.map(p=>p.chg),
  customdata:g.map(p=>[p.who,p.parkrun,p.cur,p.new,p.txt]),
  marker:{{size:6,color:SCOL[meth],opacity:0.6,symbol:SYM[ath]+(mode==='buggy'?'-open':'')}},
  hovertemplate:'%{{x|%d %b %Y}} · %{{customdata[1]}}<br><b>%{{customdata[0]}}</b><br>current %{{customdata[2]}} → %{{customdata[3]}}<br>%{{customdata[4]}} change<extra></extra>'}}}});
 // Least squares per method over the points shown: y = c + b·x, x in years since 1 Jan of the first year.
 const eq=[],day=x=>new Date(T0+x*YR).toISOString().slice(0,10);
 me.forEach(m=>{{const g=pts.filter(p=>p.meth===m),n=g.length;if(n<2)return;
  const xs=g.map(p=>(Date.parse(p.date)-T0)/YR),ys=g.map(p=>p.chg);
  const mx=xs.reduce((s,v)=>s+v,0)/n,my=ys.reduce((s,v)=>s+v,0)/n;let sxx=0,sxy=0,syy=0;
  for(let i=0;i<n;i++){{const dx=xs[i]-mx,dy=ys[i]-my;sxx+=dx*dx;sxy+=dx*dy;syy+=dy*dy}}
  if(sxx===0)return;const b=sxy/sxx,c=my-b*mx,r2=syy?sxy*sxy/(sxx*syy):0;
  const x0=Math.min(...xs),x1=Math.max(...xs);
  traces.push({{type:'scatter',mode:'lines',name:'best fit · '+m,x:[day(x0),day(x1)],y:[c+b*x0,c+b*x1],
   line:{{color:SCOL[m],width:2,dash:'dash'}},hoverinfo:'skip'}});
  const bs=f2(Math.abs(b));
  eq.push('<li><span style="color:'+SCOL[m]+'">■</span> <b>'+m+'</b>: change = '+f2(c)+'% '+(b<0&&bs!=='0.00'?'−':'+')+' '
   +bs+'% × <i>x</i> &nbsp;·&nbsp; R² = '+f2(r2)+' &nbsp;·&nbsp; '+n+' points</li>')}});
 const el=document.getElementById('scatter');
 Plotly.react(el,traces,{{template:D.template,autosize:true,height:460,margin:{{t:20,b:40,l:50,r:10}},
  yaxis:{{title:{{text:'|target change| vs current (%)'}},ticksuffix:'%',tickformat:'.2~f'}},legend:{{font:{{size:11}}}}}},
  {{responsive:true,displayModeBar:true}});
 document.getElementById('fits').innerHTML=eq.length?'<ul class="fits">'+eq.join('')+'</ul><p class="muted">'
  +'<i>x</i> = years since 1 Jan '+D.scatterOrigin+'. The slope is the change in the size of the move per year; R² is the share '
  +'of the spread the line explains (0 = none, 1 = all).</p>':'<p class="muted">Not enough points for a line of best fit.</p>'}}
document.querySelectorAll('#scatterctl input').forEach(i=>i.onchange=scatterDraw);scatterDraw();
const spm=document.getElementById('spm');M.forEach(m=>opt(spm,m,D.methods[m]));
filterable('trendctl',document.getElementById('trend'),structuredClone(D.trend));
const esc=t=>String(t).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
function calc(){{const on=k=>[...document.querySelectorAll('#calcctl input[data-k="'+k+'"]:checked')].map(i=>i.value);
 const a=on('ath'),md=on('mode'),me=on('meth');
 const rows=D.calc.filter(r=>a.includes(r.ath)&&md.includes(r.mode)&&me.includes(r.meth));
 document.getElementById('calccount').textContent=rows.length+' rows';
 document.getElementById('calc').innerHTML='<table><tr><th>Date</th><th>parkrun</th><th>Runner</th><th>Method</th>'
  +'<th></th><th>Time</th><th>Target</th><th>vs target</th><th>Runs</th><th style="text-align:left">How the target was worked out</th></tr>'
  +rows.map(r=>'<tr><td>'+r.day+'</td><td style="text-align:left">'+esc(r.parkrun)+'</td><td style="text-align:left">'+r.ath
   +(r.mode==='buggy'?' 🛒':'')+'</td><td>'+r.meth+'</td><td>'+r.place+'</td><td>'+r.time+'</td><td>'+r.target
   +'</td><td>'+r.pct+'</td><td>'+r.n+'</td><td class="how">'+esc(r.how)+'</td></tr>').join('')+'</table>';}}
document.querySelectorAll('#calcctl input').forEach(i=>i.onchange=calc);calc();
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
    summ = summary_table(occ, vs)
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
    frames = {m: spread_frame(runs, series, m, start) for m in METHODS}
    spreads = {m: fig_json(spread_fig(f), lock=False) for m, f in frames.items()}
    trend = fig_json(trend_fig(frames), lock=False)

    OUTPUT.parent.mkdir(exist_ok=True)
    SUMMARY.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(page(summ, occ_index, panels, scatter_rows(tg), spreads, trend, calc_rows(h2h)))
    summ.to_csv(SUMMARY, index=False)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1e6:.1f} MB) and {SUMMARY}")


if __name__ == "__main__":
    main()
