#!/usr/bin/env python3
"""Per-athlete estimator for whether a run was pushed with a buggy.

parkrun records nothing about the buggy, so a run is only labelled if someone
typed it in. This scores the ones nobody has, so the head-to-head judges them
against the right target.

**This module writes nothing.** It loads, computes features, fits, predicts and
reports. Wiring it into the refresh is a separate step.

Deliberately free of streamlit and plotly: `scripts/export_buggy_review.py`
imports `add_baselines` from here and must keep running without them.

    python buggy_estimator.py                 # walk-forward report, both athletes
    python buggy_estimator.py --db PATH       # against a specific DuckDB
    python buggy_estimator.py --athlete George

Design notes that are not obvious from the code:

* **Causal by construction.** Every feature for a run is computed only from
  runs *before* it. The review sheet's `add_baselines` is leave-one-out over
  the whole history, which is right for a human reading the past and wrong
  here — it would let a 2025 prediction learn from 2026.
* **Class balancing is required, not a tuning knob.** Duncan's buggy runs are
  ~7% of his labelled era. Unbalanced, the cheapest fit is "never buggy", which
  scores 0.75 accuracy by doing nothing and never fires once.
* **Recency weighting is capped by information, not taste.** A short half-life
  adapts faster but throws away the positives, which are the scarce thing. The
  half-life is chosen by log-loss subject to keeping at least
  `MIN_EFFECTIVE_POSITIVES` of them.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize

REPO = Path(__file__).resolve().parent
SNAPSHOT = REPO / "data" / "parkrun_snapshot.duckdb"

# The two athletes with a buggy dimension. Raju has never pushed one, so every
# run of his is non-buggy by construction and he is never scored.
ATHLETES = {3087156: "George", 5462426: "Duncan"}

# --- baseline cascade (shared with scripts/export_buggy_review.py) ----------
# `event` is the clean, course-controlled baseline; `window` is the fallback
# when there is too little history at that course.
BASE_MIN_EVENT_RUNS = 2
BASE_MIN_WINDOW_RUNS = 8
BASE_WINDOW_DAYS = 182

# --- features ---------------------------------------------------------------
# The form window matches the head-to-head's, deliberately: the estimator should
# judge "slow" the same way the app does. Kept in step with
# parkrun_pipeline.TARGET_WINDOW_DAYS by the test suite, not by importing it —
# the pipeline pulls in requests and bs4, which this module must not need.
TARGET_WINDOW_DAYS = 91
PRIOR_RATE_N = 10          # feature 5 looks back this many labelled runs
EVENT_RATE_PRIOR = 3.0     # pseudo-runs of shrinkage for feature 6

FEATURES = ["excess", "form_resid", "course_diff", "run_len", "prior_rate",
            "event_buggy_share"]

# Only these sources train. A `rule` row is what a deterministic rule says
# must be true — Raju has never pushed a buggy — so it is a statement about the
# rule, not evidence about the run, and hundreds of them would swamp the
# evidence that exists. `rule` rows are still used for FEATURES: a real run with
# a real time belongs in a form window and a course baseline whoever labelled
# it. It is only its label that carries no information.
TRAINING_SOURCES = ("user", "model")

# --- fitting ----------------------------------------------------------------
L2 = 1.0                   # ridge penalty on the coefficients, not the intercept
HALF_LIVES_MONTHS = [3, 6, 9, 12, 18, 24, 36, None]   # None = no decay
DEFAULT_HALF_LIFE = 12     # used until there is enough history to tune
MIN_TUNE_HISTORY = 25      # labelled runs before the inner sweep is trusted
MIN_EFFECTIVE_POSITIVES = 3.0
MIN_TRAIN_ROWS = 15        # below this, no model at all
DAYS_PER_MONTH = 30.44


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def resolve_db(db: str | None = None) -> str:
    """Read-only target, in the same priority order the apps use."""
    if db:
        return os.path.expanduser(db)
    env = os.environ.get("PARKRUN_DB")
    if env:
        return os.path.expanduser(env)
    local = Path.home() / ".config" / "parkrun" / "parkrun_local.duckdb"
    return str(local if local.exists() else SNAPSHOT)


def load_runs(db: str) -> pd.DataFrame:
    """Every run by the two buggy athletes, with its label and course score."""
    con = duckdb.connect(db, read_only=True)
    try:
        df = con.execute(
            f"""
            SELECT r.athlete_id, a.athlete_name, r.run_date, r.event_id,
                   e.short_name, r.time, r.time_seconds,
                   m.is_buggy, m.source, cd.difficulty AS course_diff
            FROM parkrun.results r
            JOIN parkrun.athletes a USING (athlete_id)
            JOIN parkrun.events e   USING (event_id)
            LEFT JOIN parkrun.run_modes m
                   ON (m.athlete_id, m.run_date, m.event_id)
                    = (r.athlete_id, r.run_date, r.event_id)
            LEFT JOIN parkrun.course_difficulty cd ON cd.event_id = r.event_id
            WHERE r.athlete_id IN ({','.join(str(a) for a in ATHLETES)})
              AND r.time_seconds IS NOT NULL
            ORDER BY r.athlete_id, r.run_date
            """
        ).fetchdf()
    finally:
        con.close()
    df["run_date"] = pd.to_datetime(df["run_date"])
    return df


# --------------------------------------------------------------------------- #
# Baselines — the cascade, shared with the review sheet
# --------------------------------------------------------------------------- #
def baseline_for(row, others: pd.DataFrame) -> tuple[float, str, int]:
    """Comparison baseline for one run, given whatever comparison set the
    caller supplies. First rule that fires:

      1. `event`  — median of that athlete's other runs at the same parkrun.
                    Course-controlled, so the only clean baseline.
      2. `window` — 25th percentile of their runs within ±182 days. q25 not
                    median: a buggy only ever makes you slower, so the fast tail
                    is the buggy-immune part of the distribution. Biased high by
                    construction, which is why no threshold is drawn on it.
      3. `none`   — too little history either way; NaN rather than a guess.

    The caller decides what `others` means. `add_baselines` passes every other
    run (right for a human reading history); the estimator passes only earlier
    runs (right for a prediction that must not see its own future).
    """
    same_event = others[others.event_id == row.event_id]
    if len(same_event) >= BASE_MIN_EVENT_RUNS:
        return float(same_event.time_seconds.median()), "event", len(same_event)
    near = others[(others.run_date - row.run_date).abs().dt.days <= BASE_WINDOW_DAYS]
    if len(near) >= BASE_MIN_WINDOW_RUNS:
        return float(near.time_seconds.quantile(0.25)), "window", len(near)
    return np.nan, "none", 0


def add_baselines(df: pd.DataFrame) -> pd.DataFrame:
    """Leave-one-out baselines for every run — evidence for a human, not a
    verdict. Used by the review sheet, where the reader is looking back over a
    settled history and future runs are legitimate context.

    **Not** what the estimator uses: see `build_features`.
    """
    out = [
        baseline_for(r, df[(df.athlete_id == r.athlete_id) & (df.index != i)])
        for i, r in df.iterrows()
    ]
    df[["expected", "basis", "n_base"]] = pd.DataFrame(out, index=df.index)
    df["excess"] = df.time_seconds / df.expected - 1
    return df


# --------------------------------------------------------------------------- #
# Features — all computed from strictly earlier runs
# --------------------------------------------------------------------------- #
def _form_residual(row, history: pd.DataFrame) -> float:
    """log(time / median of the athlete's NON-buggy times in the 91 days
    before). The same window and the same mode split the head-to-head judges
    on, so "slow" means here what it means there."""
    w = history[
        (history.run_date >= row.run_date - pd.Timedelta(days=TARGET_WINDOW_DAYS))
        & (history.run_date <= row.run_date - pd.Timedelta(days=1))
        & (~history.is_buggy.fillna(False).astype(bool))
    ]
    if not len(w):
        return np.nan
    return float(np.log(row.time_seconds / w.time_seconds.median()))


def _same_sign_run(history: pd.DataFrame) -> float:
    """Signed length of the current streak of same-sign form residuals.

    The one feature aimed at *sequence* rather than a single run. A buggy tends
    to come in spells — a child in it every week for months — so a run of
    consecutive slow weeks is evidence a single slow week is not. Named as the
    thing a human reviewer actually uses, in the review script's own notes, and
    never previously computed.
    """
    resid = history["_resid"].dropna()
    if not len(resid):
        return 0.0
    signs = np.sign(resid.values[::-1])
    first = signs[0]
    if first == 0:
        return 0.0
    n = 0
    for s in signs:
        if s != first:
            break
        n += 1
    return float(n * first)


def _prior_rate(history: pd.DataFrame) -> float:
    """Buggy rate over the previous PRIOR_RATE_N *labelled* runs.

    This is what lets the model learn an era rather than have one imposed on it:
    an athlete who has used the buggy for the last six weeks is likely using it
    today. It adapts to a regime change without discarding any history, which
    is why recency weighting can then be kept modest.
    """
    labelled = history[history.source.isin(TRAINING_SOURCES)].tail(PRIOR_RATE_N)
    if not len(labelled):
        return np.nan
    return float(labelled.is_buggy.astype(bool).mean())


def _event_buggy_share(row, history: pd.DataFrame) -> tuple[float, float]:
    """Share of this athlete's runs *at this event* that were pushed, counted
    from their first buggy run onward. Returns (share, n runs there).

    Aimed squarely at the course confound, which is the estimator's largest
    known failure: Duncan's false positives are almost all slow runs at
    Lordship, a hard course he has never taken a buggy to. The other course
    feature, `course_diff`, only knows a course is hard in general — it cannot
    know this athlete goes there alone. This can.

    Two decisions inside it:

    **The era starts at the first buggy run**, not at the start of history.
    Before that every run is non-buggy because no buggy existed, so including
    those years would drown a real 3-of-4 at a course under a decade of zeroes
    and make the feature a proxy for how long ago the athlete started running.
    The start date is read from the *prior* runs only, so it moves forward as
    history accumulates rather than being imposed from the future.

    **The share is shrunk toward the athlete's era-wide rate** by
    `EVENT_RATE_PRIOR` pseudo-runs, which is how the run count earns its say.
    A raw share cannot distinguish 1-of-1 from 8-of-8, and a first visit to a
    course would otherwise assert 0% — a confident claim from no evidence.
    Shrinkage makes an unvisited course say exactly what is true of it: nothing
    beyond what the athlete does in general. It also keeps the feature defined
    everywhere, so no run drops out of training over it.
    """
    lab = history[history.source.isin(TRAINING_SOURCES) & history.is_buggy.notna()]
    if lab.empty:
        return 0.0, 0.0
    buggy = lab[lab.is_buggy.astype(bool)]
    if buggy.empty:
        return 0.0, 0.0          # pre-era: no buggy has ever been pushed
    era = lab[lab.run_date >= buggy.run_date.min()]
    base = float(era.is_buggy.astype(bool).mean())
    here = era[era.event_id == row.event_id]
    k, n = float(here.is_buggy.astype(bool).sum()), float(len(here))
    return (k + EVENT_RATE_PRIOR * base) / (n + EVENT_RATE_PRIOR), n


def build_features(athlete: pd.DataFrame) -> pd.DataFrame:
    """Attach the six features to one athlete's runs, causally.

    Walks the runs in date order; every value for a run is derived only from
    rows above it. `_resid` is carried alongside because the streak feature
    needs each earlier run's own residual, not just the current one's.
    """
    a = athlete.sort_values("run_date").reset_index(drop=True).copy()
    a["_resid"] = np.nan
    rows = []
    for i, row in a.iterrows():
        hist = a.iloc[:i]
        expected, basis, n_base = (
            baseline_for(row, hist) if len(hist) else (np.nan, "none", 0)
        )
        ev_share, ev_n = _event_buggy_share(row, hist) if len(hist) else (0.0, 0.0)
        rows.append(
            {
                "excess": row.time_seconds / expected - 1 if expected == expected else np.nan,
                "form_resid": _form_residual(row, hist) if len(hist) else np.nan,
                # course_diff is already a column from load_runs — re-adding it
                # here would duplicate the name and make row lookups ambiguous.
                "run_len": _same_sign_run(hist) if len(hist) else 0.0,
                "prior_rate": _prior_rate(hist) if len(hist) else np.nan,
                "event_buggy_share": ev_share,
                "event_n": ev_n,
                "basis": basis,
                "n_base": n_base,
            }
        )
        a.loc[i, "_resid"] = rows[-1]["form_resid"]
    return pd.concat([a, pd.DataFrame(rows, index=a.index)], axis=1)


# --------------------------------------------------------------------------- #
# Model — L2 logistic regression, hand-rolled to avoid a new dependency
# --------------------------------------------------------------------------- #
def standardise(X: pd.DataFrame, mu=None, sd=None):
    """Median-impute then z-score. Imputation matters: `course_diff` is NULL for
    courses outside the published UK set, and dropping those runs would quietly
    exclude every overseas parkrun."""
    X = X.astype(float).copy()
    if mu is None:
        mu = X.median()
        filled = X.fillna(mu)
        sd = filled.std(ddof=0).replace(0, 1.0)
    else:
        filled = X.fillna(mu)
    return ((filled - mu) / sd).values, mu, sd


def fit(X: np.ndarray, y: np.ndarray, w: np.ndarray, lam: float = L2) -> np.ndarray:
    """Weighted logistic regression with an L2 penalty on the slopes only.

    The intercept is left unpenalised on purpose: it carries the athlete's base
    rate, and shrinking it toward zero would assert a 50/50 prior that is wrong
    for both of them.
    """
    Xd = np.column_stack([np.ones(len(X)), X])

    def nll(b):
        z = Xd @ b
        return -np.sum(w * (y * z - np.logaddexp(0, z))) + lam * np.sum(b[1:] ** 2)

    def grad(b):
        z = Xd @ b
        p = 1.0 / (1.0 + np.exp(-z))
        g = -(Xd * (w * (y - p))[:, None]).sum(axis=0)
        g[1:] += 2 * lam * b[1:]
        return g

    res = minimize(nll, np.zeros(Xd.shape[1]), jac=grad, method="L-BFGS-B")
    return res.x


def predict(beta: np.ndarray, X: np.ndarray) -> np.ndarray:
    z = beta[0] + X @ beta[1:]
    return 1.0 / (1.0 + np.exp(-z))


def recency_weights(train: pd.DataFrame, as_of: pd.Timestamp,
                    half_life_months) -> np.ndarray:
    """Exponential decay by age. A row from today weighs 1, one a half-life old
    weighs 0.5. `None` means no decay at all."""
    if half_life_months is None:
        return np.ones(len(train))
    age = (as_of - train.run_date).dt.days.values
    return 0.5 ** (age / (half_life_months * DAYS_PER_MONTH))


def weights(train: pd.DataFrame, as_of: pd.Timestamp, half_life_months) -> np.ndarray:
    """Recency decay times class balancing.

    Class balancing equalises the two classes' total weight, so the fit cannot
    win by always answering with the majority. Without it a 7%-buggy athlete
    gets a model that never fires once.
    """
    w = recency_weights(train, as_of, half_life_months)
    y = train.is_buggy.astype(float).values
    n_pos = y.sum()
    if 0 < n_pos < len(y):
        w = w * np.where(y == 1, (len(y) - n_pos) / n_pos, 1.0)
    return w


def effective_positives(train: pd.DataFrame, as_of: pd.Timestamp,
                        half_life_months) -> float:
    """How many full-weight positive examples the decay leaves.

    Deliberately the **sum of the recency weights** over the positives, not a
    Kish effective sample size. Kish measures how *uneven* weights are, so three
    equally-old positives score 3.0 however hard they are discounted — blind to
    the thing this number exists to catch. The sum answers the question actually
    being asked: at this half-life, are five confirmed buggy runs worth five
    examples or a fraction of one?

    Class balancing is excluded on purpose. It rescales the positives to match
    the negatives, which would mask the loss every time.
    """
    w = recency_weights(train, as_of, half_life_months)
    return float(w[train.is_buggy.astype(bool).values].sum())


def _log_loss(p: float, y: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def choose_half_life(train: pd.DataFrame) -> tuple[object, str]:
    """Pick a half-life by an inner walk-forward on `train` alone.

    Two constraints, both learned from real failures:

    * **Scored on log-loss, not accuracy.** Accuracy is degenerate at a low base
      rate — for Duncan it returns the same number at every half-life, because
      always-guess-regular scores it, and the tie-break then silently picks
      "no decay", the opposite of what a regime change needs.
    * **Floored on effective positives.** A half-life that discounts an
      athlete's confirmed buggy runs below `MIN_EFFECTIVE_POSITIVES` is refused
      however well it scores, because it is buying adaptivity with the only
      evidence there is.

    Returns the half-life and how it was chosen, so the report can say.
    """
    if len(train) < MIN_TUNE_HISTORY:
        return DEFAULT_HALF_LIFE, "default (too little history to tune)"

    scored = {}
    for hl in HALF_LIVES_MONTHS:
        losses = []
        for i in train.index[-MIN_TUNE_HISTORY:]:
            inner = train[train.run_date < train.loc[i, "run_date"]]
            if len(inner) < MIN_TRAIN_ROWS or inner.is_buggy.nunique() < 2:
                continue
            w = weights(inner, train.loc[i, "run_date"], hl)
            X, mu, sd = standardise(inner[FEATURES])
            beta = fit(X, inner.is_buggy.astype(float).values, w)
            Xt, _, _ = standardise(train.loc[[i], FEATURES], mu, sd)
            losses.append(_log_loss(predict(beta, Xt)[0], float(train.loc[i, "is_buggy"])))
        if losses:
            scored[hl] = float(np.mean(losses))
    if not scored:
        return DEFAULT_HALF_LIFE, "default (inner sweep found nothing to score)"

    ok = {
        hl: loss
        for hl, loss in scored.items()
        if effective_positives(train, train.run_date.max(), hl)
        >= MIN_EFFECTIVE_POSITIVES
    }
    if not ok:
        return None, f"no decay (every half-life fell below {MIN_EFFECTIVE_POSITIVES} effective positives)"
    best = min(ok, key=ok.get)
    note = "tuned"
    if best != min(scored, key=scored.get):
        note = f"tuned, {min(scored, key=scored.get)}m rejected on effective positives"
    return best, note


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score_one(train: pd.DataFrame, target: pd.DataFrame, half_life=...) -> dict | None:
    """Fit on `train` and score the single run in `target`. None if unfittable."""
    if len(train) < MIN_TRAIN_ROWS or train.is_buggy.nunique() < 2:
        return None
    if half_life is ...:
        half_life, hl_note = choose_half_life(train)
    else:
        hl_note = "given"
    as_of = target.run_date.iloc[0]
    w = weights(train, as_of, half_life)
    X, mu, sd = standardise(train[FEATURES])
    beta = fit(X, train.is_buggy.astype(float).values, w)
    Xt, _, _ = standardise(target[FEATURES], mu, sd)
    p = float(predict(beta, Xt)[0])
    return {
        "p": p,
        "is_buggy": p > 0.5,
        "confidence": max(p, 1 - p),
        "half_life": half_life,
        "half_life_note": hl_note,
        "n_train": len(train),
        "n_pos": int(train.is_buggy.sum()),
        "eff_pos": effective_positives(train, as_of, half_life),
        "beta": beta,
        "contributions": dict(zip(FEATURES, Xt[0] * beta[1:])),
    }


def walk_forward(feat: pd.DataFrame, uncorrected: bool = False) -> pd.DataFrame:
    """Predict every labelled run from its own past only.

    Two modes, and they are **bounds rather than alternatives** — the live
    system sits between them, depending on how much gets corrected:

    * `uncorrected=False` (default) — every prior run trains on its *true*
      label. This is the model measured on its own merits, and the optimistic
      bound: it assumes every wrong estimate gets corrected before the next fit.
    * `uncorrected=True` — each prediction is fed back as an `estimated` label
      and **never corrected**. The pessimistic bound, and the one that shows
      whether the loop is self-stabilising or self-reinforcing.

    Note what the pessimistic bound overstates. Production is write-once: the
    manual labels already in `run_modes` are a permanent anchor no estimate can
    overwrite, whereas this replaces them as it walks. So it is a genuine
    worst case, not a forecast — read it for the *direction and speed* of drift,
    not as the accuracy to expect.
    """
    labelled = feat[feat.source.isin(TRAINING_SOURCES)].copy()
    known = labelled.copy()
    known["_train_y"] = known.is_buggy.astype(bool)
    out = []
    for i in labelled.index:
        prior = known[known.run_date < labelled.loc[i, "run_date"]]
        prior = prior.assign(is_buggy=prior["_train_y"])
        res = score_one(prior, labelled.loc[[i]])
        rec = {
            "athlete_name": labelled.loc[i, "athlete_name"],
            "run_date": labelled.loc[i, "run_date"],
            "short_name": labelled.loc[i, "short_name"],
            "time": labelled.loc[i, "time"],
            "actual": bool(labelled.loc[i, "is_buggy"]),
        }
        if res is None:
            rec.update(p=np.nan, call=None, confidence=np.nan, correct=None,
                       warm_up=True, half_life=None)
        else:
            rec.update(p=res["p"], call=res["is_buggy"], confidence=res["confidence"],
                       correct=res["is_buggy"] == rec["actual"], warm_up=False,
                       half_life=res["half_life"])
            if uncorrected:
                known.loc[i, "_train_y"] = res["is_buggy"]
        out.append(rec)
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def summarise(wf: pd.DataFrame) -> dict:
    s = wf[~wf.warm_up]
    tp = int(((s.call) & (s.actual)).sum())
    fp = int(((s.call) & (~s.actual)).sum())
    fn = int(((~s.call) & (s.actual)).sum())
    tn = int(((~s.call) & (~s.actual)).sum())
    n = tp + fp + fn + tn
    nan = float("nan")
    return {
        "scored": n, "warm_up": int(wf.warm_up.sum()),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else nan,
        "recall": tp / (tp + fn) if tp + fn else nan,
        "accuracy": (tp + tn) / n if n else nan,
        "ppv": tp / (tp + fp) if tp + fp else nan,
        "npv": tn / (tn + fn) if tn + fn else nan,
        "baseline": max(tp + fn, fp + tn) / n if n else nan,
    }


def _hl(v) -> str:
    """Half-life for display. None means no decay; pandas turns that into NaN
    when it lands in a float column, so both spellings arrive here."""
    if v is None or v != v:
        return "—"
    return f"{int(v)}m"


def _fmt(v, spec=".2f"):
    return "  -  " if v is None or v != v else format(v, spec)


def report(db: str, only: str | None = None) -> None:
    runs = load_runs(db)
    print(f"database: {db}")
    for aid, name in ATHLETES.items():
        if only and name.lower() != only.lower():
            continue
        feat = build_features(runs[runs.athlete_id == aid])
        labelled = feat[feat.source.isin(TRAINING_SOURCES)]
        print(f"\n{'=' * 92}\n{name} — {len(labelled)} runs with evidence "
              f"(user/model; {int((feat.source == 'rule').sum())} "
              f"'rule' rows excluded from training), "
              f"{int(labelled.is_buggy.sum())} buggy "
              f"({labelled.is_buggy.mean():.0%})\n{'=' * 92}")

        wf = walk_forward(feat)                      # optimistic bound
        print(f"{'date':<12}{'parkrun':<30}{'time':>8}{'actual':>9}"
              f"{'call':>9}{'conf':>7}{'hl':>5}  ")
        for _, r in wf.iterrows():
            if r.warm_up:
                print(f"{r.run_date:%Y-%m-%d}  {r.short_name[:28]:<30}{r.time:>8}"
                      f"{'BUGGY' if r.actual else 'regular':>9}{'  —':>9}{'':>7}{'':>5}  warm-up")
                continue
            print(f"{r.run_date:%Y-%m-%d}  {r.short_name[:28]:<30}{r.time:>8}"
                  f"{'BUGGY' if r.actual else 'regular':>9}"
                  f"{'BUGGY' if r.call else 'regular':>9}"
                  f"{r.confidence:>7.2f}"
                  f"{_hl(r.half_life):>5}"
                  f"  {'hit' if r.correct else 'MISS'}")

        s = summarise(wf)
        s_unc = summarise(walk_forward(feat, uncorrected=True))
        print(f"\n  scored {s['scored']}, warm-up {s['warm_up']}   "
              f"TP {s['tp']}  FP {s['fp']}  FN {s['fn']}  TN {s['tn']}")
        print(f"  precision {_fmt(s['precision'])}   recall {_fmt(s['recall'])}   "
              f"accuracy {_fmt(s['accuracy'])}   (always-majority {_fmt(s['baseline'])})")
        print(f"  says BUGGY -> right {_fmt(s['ppv'], '.0%')}   "
              f"says regular -> right {_fmt(s['npv'], '.0%')}")
        print(f"\n  drift bounds — accuracy if every wrong call is corrected "
              f"{_fmt(s['accuracy'])}, if none ever is {_fmt(s_unc['accuracy'])}"
              f"  (precision {_fmt(s['precision'])} -> {_fmt(s_unc['precision'])})")
        if s_unc["accuracy"] == s_unc["accuracy"] and s["accuracy"] == s["accuracy"]:
            gap = s["accuracy"] - s_unc["accuracy"]
            if gap > 0.15:
                print(f"  ** the uncorrected loop collapses (-{gap:.0%}): estimates "
                      f"pull the base rate up, class balancing amplifies it, and it "
                      f"runs away. Reviewing is not optional for this athlete. **")

        band = wf[~wf.warm_up].copy()
        if len(band):
            print("\n  calibration — is confidence a guide to being right?")
            for lo, hi in [(.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.01)]:
                b = band[(band.confidence >= lo) & (band.confidence < hi)]
                if len(b):
                    print(f"    {lo:.1f}-{min(hi,1.0):.1f}  n={len(b):<4} "
                          f"{b.correct.mean():.0%} correct")

        # The runs with no label at all — what the estimator would say if it
        # were live. Displayed only; this module writes nothing.
        unlabelled = feat[feat.source.isna()]
        if len(unlabelled):
            train = labelled.copy()
            print("\n  UNLABELLED — what it would call these (nothing is written):")
            for i in unlabelled.index:
                res = score_one(train[train.run_date < unlabelled.loc[i, "run_date"]],
                                unlabelled.loc[[i]])
                r = unlabelled.loc[i]
                if res is None:
                    print(f"    {r.run_date:%Y-%m-%d}  {r.short_name}  {r.time}  (unfittable)")
                    continue
                hist = s["ppv"] if res["is_buggy"] else s["npv"]
                print(f"    {r.run_date:%Y-%m-%d}  {r.short_name:<28} {r.time:>8}  ->  "
                      f"{'BUGGY' if res['is_buggy'] else 'regular':<8} "
                      f"confidence {res['confidence']:.2f}   "
                      f"(that call has been right {_fmt(hist, '.0%')} of the time)")
                for f, c in sorted(res["contributions"].items(),
                                   key=lambda kv: -abs(kv[1])):
                    print(f"        {f:<12} {float(r[f]):>8.3f}   pushes {c:+.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", help="DuckDB to read (default: source of truth, else snapshot)")
    ap.add_argument("--athlete", help="George or Duncan; default both")
    args = ap.parse_args()
    report(resolve_db(args.db), args.athlete)
    print("\nNothing was written. This module only reports.")


if __name__ == "__main__":
    main()
