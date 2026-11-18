"""Seasonal models: traffic that varies with the time of day and the day of the week."""

from dataclasses import replace

import numpy as np
import pytest

from pacing.backstop import simulate_with_backstop
from pacing.controllers import BayesRiskController
from pacing.model import Scenario, simulate
from pacing.seasonal import (DAY, WEEK, SeasonalController, SeasonalPacer, month_scenario, step_profile,
                             traffic_profile, traffic_target)

# a short campaign: 2 days, so the daily cycle shows, with a 60 s step
TWO_DAYS = Scenario(budget=240_000, horizon=2 * DAY, step=60.0, delay=30.0)
PROF = step_profile(TWO_DAYS.horizon, TWO_DAYS.step)


def test_profile_averages_one_over_a_week():
    t = np.arange(0, WEEK, 60.0)
    assert abs(traffic_profile(t).mean() - 1.0) < 1e-3
    # busy evening, quiet morning, quieter weekend
    assert traffic_profile(20 * 3600.0) > traffic_profile(8 * 3600.0)
    assert traffic_profile(5 * DAY + 20 * 3600.0) < traffic_profile(20 * 3600.0)


def test_target_follows_the_traffic():
    tgt = traffic_target(PROF)
    assert tgt[-1] == pytest.approx(1.0)
    assert np.all(np.diff(tgt) > 0)


def test_constant_profile_changes_nothing():
    """A profile of ones gives exactly the plain controller."""
    ones = np.ones(TWO_DAYS.n_steps)
    a = simulate(TWO_DAYS, BayesRiskController(TWO_DAYS.center_view(), prior_mean=100.0, eps=0.01), 100, seed=1)
    b = simulate(TWO_DAYS, SeasonalController(TWO_DAYS.center_view(), prior_mean=100.0, eps=0.01, profile=ones),
                 100, seed=1, rate_profile=ones, center_profile=ones)
    np.testing.assert_allclose(a.final_spend, b.final_spend)


def test_known_profile_keeps_eps_and_follows_the_traffic():
    eps = 0.02
    c = SeasonalController(TWO_DAYS.center_view(), prior_mean=100.0, eps=eps, profile=PROF)
    res = simulate(TWO_DAYS, c, 1000, seed=2, rate_profile=PROF, center_profile=PROF, target=traffic_target(PROF))
    assert res.overspent.mean() <= 1.5 * eps
    assert res.max_pace_deviation.mean() < 0.01


def test_unknown_profile_breaks_eps_unless_the_risk_check_uses_recent_data():
    """30 days ending at midnight, while traffic is still high: a long-run k is too low then."""
    scn = month_scenario()
    prof = step_profile(scn.horizon, scn.step)
    eps = 0.01
    plain = SeasonalController(scn.center_view(), prior_mean=100.0, eps=eps)
    recent = SeasonalController(scn.center_view(), prior_mean=100.0, eps=eps, risk_half_life=3600.0)
    assert simulate(scn, plain, 600, seed=3, rate_profile=prof).overspent.mean() > 5 * eps
    assert simulate(scn, recent, 600, seed=3, rate_profile=prof).overspent.mean() <= 1.5 * eps


@pytest.mark.parametrize("mode", ["reports", "grants"])
def test_caps_never_overspend_with_varying_traffic(mode):
    kw = {"failure_delay": 30.0} if mode == "reports" else {"mode": "grants", "grant_share": 0.5}
    scn = replace(TWO_DAYS, realization=1.0, request_rate=100.0)
    for known in (True, False):
        c = SeasonalPacer(scn.center_view(), prior_mean=100.0, profile=PROF if known else None)
        extra = {"center_profile": PROF} if known else {}
        res = simulate_with_backstop(scn, c, 200, seed=4, rate_profile=PROF, **extra, **kw)
        assert res.final_spend.max() <= scn.budget
