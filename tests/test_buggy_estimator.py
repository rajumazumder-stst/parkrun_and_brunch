"""Tests for buggy_estimator.

The estimator writes to `run_modes`, which decides which target every
head-to-head is judged against — so a silent regression here rewrites the
record. These are the checks that would catch that.

No database: every test builds its own frame. Run with `pytest` from the repo
root (pytest is a dev tool, deliberately not in requirements.txt).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import buggy_estimator as be


def runs(*specs, athlete_id=1):
    """Build a runs frame. Each spec is (day_offset, event_id, seconds) with an
    optional 4th item for the label and a 5th for its source."""
    rows = []
    for i, spec in enumerate(specs):
        day, event, secs = spec[0], spec[1], spec[2]
        is_buggy = spec[3] if len(spec) > 3 else None
        source = spec[4] if len(spec) > 4 else ("user" if is_buggy is not None else None)
        rows.append(
            dict(
                athlete_id=athlete_id,
                athlete_name="Tester",
                run_date=pd.Timestamp("2026-01-01") + pd.Timedelta(days=day),
                event_id=event,
                short_name=f"Event {event}",
                time="00:00",
                time_seconds=secs,
                is_buggy=is_buggy,
                source=source,
                course_diff=5.0,
            )
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# The baseline cascade — shared with the review sheet, so both must agree
# --------------------------------------------------------------------------- #
class TestBaselineCascade:
    def test_event_rule_needs_two_prior_runs_at_that_course(self):
        df = runs((0, 10, 1200), (7, 10, 1300), (14, 10, 1400))
        # one prior run at the course is not enough
        assert be.baseline_for(df.iloc[1], df.iloc[:1])[1] != "event"
        # two is
        expected, basis, n = be.baseline_for(df.iloc[2], df.iloc[:2])
        assert basis == "event" and n == 2
        assert expected == pytest.approx(1250.0)

    def test_window_rule_is_q25_not_median(self):
        # 8 runs at other courses, none repeated, so the event rule cannot fire
        prior = runs(*[(i * 7, 100 + i, 1000 + i * 100) for i in range(8)])
        target = runs((70, 999, 2000)).iloc[0]
        expected, basis, n = be.baseline_for(target, prior)
        assert basis == "window" and n == 8
        assert expected == pytest.approx(np.percentile(prior.time_seconds, 25))
        # q25 is deliberately below the median — a buggy only makes you slower
        assert expected < prior.time_seconds.median()

    def test_falls_back_to_none_rather_than_guessing(self):
        prior = runs((0, 1, 1200), (7, 2, 1300))
        expected, basis, n = be.baseline_for(runs((14, 3, 1400)).iloc[0], prior)
        assert basis == "none" and n == 0 and np.isnan(expected)

    def test_window_rule_ignores_runs_outside_182_days(self):
        old = runs(*[(-400 - i * 7, 100 + i, 1000) for i in range(8)])
        expected, basis, _ = be.baseline_for(runs((0, 999, 2000)).iloc[0], old)
        assert basis == "none" and np.isnan(expected)

    def test_add_baselines_excludes_the_run_itself(self):
        """The review sheet's leave-one-out view. A run must not be its own
        baseline, or every excess collapses toward zero."""
        df = runs((0, 10, 1000), (7, 10, 1000), (14, 10, 4000))
        out = be.add_baselines(df.copy())
        # the outlier is compared against the other two, not against itself
        assert out.loc[2, "expected"] == pytest.approx(1000.0)
        assert out.loc[2, "excess"] == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# Causality — the property the whole walk-forward rests on
# --------------------------------------------------------------------------- #
class TestCausality:
    def test_first_run_has_no_derived_features(self):
        f = be.build_features(runs((0, 1, 1200)))
        assert np.isnan(f.loc[0, "excess"])
        assert np.isnan(f.loc[0, "form_resid"])
        assert f.loc[0, "run_len"] == 0.0

    def test_a_later_run_cannot_change_an_earlier_one(self):
        """The guard against lookahead. Appending a run must leave every
        earlier row's features byte-identical."""
        base = runs((0, 1, 1200, False), (7, 1, 1250, False), (14, 2, 1300, False))
        extended = pd.concat([base, runs((21, 1, 9999, True))], ignore_index=True)
        a = be.build_features(base)[be.FEATURES]
        b = be.build_features(extended)[be.FEATURES].iloc[: len(base)]
        pd.testing.assert_frame_equal(a, b)

    def test_form_residual_uses_only_non_buggy_runs_in_window(self):
        hist = runs((0, 1, 1000, False), (7, 1, 2000, True))
        row = runs((14, 1, 1100)).iloc[0]
        # the buggy run must not drag the baseline up
        assert be._form_residual(row, hist) == pytest.approx(np.log(1100 / 1000))

    def test_form_residual_window_excludes_the_day_itself(self):
        hist = runs((0, 1, 1000, False))
        same_day = runs((0, 2, 1100)).iloc[0]
        assert np.isnan(be._form_residual(same_day, hist))


# --------------------------------------------------------------------------- #
# Individual features
# --------------------------------------------------------------------------- #
class TestFeatures:
    def test_same_sign_run_counts_the_current_streak_and_signs_it(self):
        h = pd.DataFrame({"_resid": [0.1, -0.2, -0.3, -0.4]})
        assert be._same_sign_run(h) == -3.0
        h = pd.DataFrame({"_resid": [-0.1, 0.2, 0.3]})
        assert be._same_sign_run(h) == 2.0

    def test_same_sign_run_is_zero_without_history(self):
        assert be._same_sign_run(pd.DataFrame({"_resid": []})) == 0.0

    def test_prior_rate_ignores_rule_rows(self):
        """A `rule` row is what a rule says must be true, not evidence about
        the run — CLAUDE.md's three-source rule. Counting them would drag the
        rate toward zero on runs nobody assessed."""
        h = runs(
            (0, 1, 1200, False, "rule"),
            (7, 1, 1200, False, "rule"),
            (14, 1, 1200, True, "user"),
        )
        assert be._prior_rate(h) == pytest.approx(1.0)

    def test_prior_rate_counts_model_alongside_user(self):
        h = runs((0, 1, 1200, True, "user"), (7, 1, 1200, False, "model"))
        assert be._prior_rate(h) == pytest.approx(0.5)

    def test_prior_rate_is_nan_with_no_evidence(self):
        assert np.isnan(be._prior_rate(runs((0, 1, 1200, False, "rule"))))


# --------------------------------------------------------------------------- #
# Weighting — class balance and recency
# --------------------------------------------------------------------------- #
class TestWeights:
    def test_class_balancing_equalises_the_two_classes(self):
        train = runs(*[(i, 1, 1200, i >= 8) for i in range(10)])  # 8 neg, 2 pos
        w = be.weights(train, train.run_date.max(), None)
        y = train.is_buggy.astype(bool).values
        assert w[y].sum() == pytest.approx(w[~y].sum())

    def test_recency_halves_the_weight_at_one_half_life(self):
        train = runs((0, 1, 1200, False), (-int(6 * be.DAYS_PER_MONTH), 1, 1200, False))
        w = be.weights(train, train.run_date.max(), 6)
        assert w.min() / w.max() == pytest.approx(0.5, rel=1e-2)

    def test_effective_positives_equals_count_when_undecayed(self):
        train = runs(*[(i, 1, 1200, i >= 7) for i in range(10)])  # 3 positives
        assert be.effective_positives(train, train.run_date.max(), None) == pytest.approx(3.0)

    def test_effective_positives_falls_when_positives_are_old(self):
        """The number the half-life floor exists to protect: old positives are
        still true, but they inform the fit as if there were fewer of them.

        A Kish effective sample size cannot see this — all three are equally
        old, so it reports 3.0 however hard they are discounted. That is why
        this is a sum of weights instead."""
        train = runs(
            (-700, 1, 1200, True), (-690, 1, 1200, True), (-680, 1, 1200, True),
            *[(i, 1, 1200, False) for i in range(10)],
        )
        as_of = train.run_date.max()
        assert be.effective_positives(train, as_of, None) == pytest.approx(3.0)
        assert be.effective_positives(train, as_of, 3) < 1.0

    def test_effective_positives_ignores_class_balancing(self):
        """Balancing rescales the positives to match the negatives, so folding
        it in here would hide every loss the decay causes."""
        train = runs(
            (-700, 1, 1200, True), *[(i, 1, 1200, False) for i in range(20)],
        )
        assert be.effective_positives(train, train.run_date.max(), 3) < 0.1


# --------------------------------------------------------------------------- #
# The fit itself
# --------------------------------------------------------------------------- #
class TestFit:
    def test_recovers_the_direction_of_a_clean_signal(self):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(200, 1))
        y = (X[:, 0] > 0).astype(float)
        beta = be.fit(X, y, np.ones(len(y)), lam=0.01)
        assert beta[1] > 2.0                       # positive slope, confidently
        assert be.predict(beta, np.array([[3.0]]))[0] > 0.9
        assert be.predict(beta, np.array([[-3.0]]))[0] < 0.1

    def test_l2_shrinks_slopes_but_not_the_intercept(self):
        """The intercept carries the base rate. Penalising it would assert a
        50/50 prior, which is wrong for both athletes."""
        rng = np.random.default_rng(1)
        X = rng.normal(size=(200, 1))
        y = (X[:, 0] > 0).astype(float)
        weak = be.fit(X, y, np.ones(len(y)), lam=0.01)
        strong = be.fit(X, y, np.ones(len(y)), lam=100.0)
        assert abs(strong[1]) < abs(weak[1])

    def test_a_base_rate_only_fit_predicts_the_base_rate(self):
        X = np.zeros((100, 1))
        y = np.concatenate([np.ones(20), np.zeros(80)])
        beta = be.fit(X, y, np.ones(100), lam=1.0)
        assert be.predict(beta, np.zeros((1, 1)))[0] == pytest.approx(0.2, abs=0.02)


# --------------------------------------------------------------------------- #
# Contracts that must not drift
# --------------------------------------------------------------------------- #
class TestContracts:
    def test_form_window_matches_the_pipeline(self):
        """The estimator judges "slow" on the same window the head-to-head
        does. The pipeline owns the constant; this module cannot import it
        (parkrun_pipeline pulls in requests and bs4), so the check lives here."""
        src = (be.REPO / "parkrun_pipeline.py").read_text()
        line = next(l for l in src.splitlines() if l.startswith("TARGET_WINDOW_DAYS"))
        assert int(line.split("=")[1].split("#")[0].strip()) == be.TARGET_WINDOW_DAYS

    def test_rule_rows_never_train(self):
        assert "rule" not in be.TRAINING_SOURCES
        assert set(be.TRAINING_SOURCES) == {"user", "model"}

    def test_the_three_sources_agree_with_the_pipeline(self):
        """One vocabulary. The pipeline owns the migration that renames old
        values, so a third spelling appearing anywhere is a bug."""
        src = (be.REPO / "parkrun_pipeline.py").read_text()
        line = next(l for l in src.splitlines() if l.startswith("LABEL_SOURCES"))
        assert set(be.TRAINING_SOURCES) | {"rule"} == eval(line.split("=", 1)[1].strip())

    def test_raju_is_out_of_scope(self):
        """He has never pushed a buggy, so scoring him could only invent one."""
        assert 5672 not in be.ATHLETES

    def test_score_one_refuses_a_single_class_training_set(self):
        train = be.build_features(runs(*[(i, 1, 1200, False) for i in range(20)]))
        target = be.build_features(runs((200, 1, 1500)))
        assert be.score_one(train, target) is None

    def test_score_one_refuses_too_little_history(self):
        train = be.build_features(runs(*[(i, 1, 1200, i % 2 == 0) for i in range(5)]))
        target = be.build_features(runs((200, 1, 1500)))
        assert be.score_one(train, target) is None
