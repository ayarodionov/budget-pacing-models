from dataclasses import replace

import numpy as np
import pytest

from pacing.backstop import simulate_with_backstop
from pacing.controllers import BayesRiskController, NaiveController
from pacing.model import Scenario

SMALL = Scenario(budget=300, horizon=600.0, request_rate=200.0, realization=0.05,
                 delay=20.0, step=5.0)


class _FullThrottle:
    """Adversarial controller: always p = 1."""

    def reset(self, n):
        pass

    def decide(self, t, obs, exp_obs, inflight):
        return np.ones(obs.shape)


@pytest.mark.parametrize("r", [0.05, 0.5, 1.0])
@pytest.mark.parametrize("failure_delay", [0.0, 5.0, 60.0])
def test_cap_makes_overspend_impossible_even_at_full_throttle(r, failure_delay):
    scn = replace(SMALL, request_rate=10.0 / r, realization=r)
    res = simulate_with_backstop(scn, _FullThrottle(), 2000, failure_delay, seed=1)
    assert res.final_spend.max() <= scn.budget


def test_without_cap_full_throttle_overspends():
    res = simulate_with_backstop(SMALL, _FullThrottle(), 500, 5.0, seed=1, cap=False)
    assert res.overspent.mean() > 0.99


def test_every_bid_charged_means_cap_spends_exactly_the_budget():
    """r = 1: tokens are spend, counted when granted, so the delay no longer matters."""
    scn = replace(SMALL, request_rate=10.0, realization=1.0)
    c = NaiveController(scn.center_view(), prior_mean=10.0)
    res = simulate_with_backstop(scn, c, 1000, failure_delay=30.0, seed=2)
    assert (res.final_spend == scn.budget).all()


def test_cap_rarely_binds_under_bayes_controller_when_failures_are_fast():
    scn = replace(SMALL, request_rate=20.0, realization=0.5)
    c = BayesRiskController(scn.center_view(), prior_mean=10.0, eps=1e-6)
    res = simulate_with_backstop(scn, c, 2000, failure_delay=0.0, seed=3)
    assert res.cap_bound.mean() < 0.05
    assert not res.overspent.any()


def test_without_cap_matches_aggregate_model():
    """Token-level process with no cap = the Poisson(k p dt) aggregate model."""
    from pacing.model import simulate

    c1 = BayesRiskController(SMALL.center_view(), prior_mean=10.0, eps=0.01)
    c2 = BayesRiskController(SMALL.center_view(), prior_mean=10.0, eps=0.01)
    a = simulate_with_backstop(SMALL, c1, 4000, 5.0, seed=4, cap=False).final_spend
    b = simulate(SMALL, c2, 4000, seed=5).final_spend
    assert abs(a.mean() - b.mean()) < 4 * np.sqrt((a.var() + b.var()) / 4000)


# --------------------------------------------------------------------------- version (b)
@pytest.mark.parametrize("r", [0.05, 0.5, 1.0])
@pytest.mark.parametrize("rule", [dict(grant_share=0.5), dict(grant_share=1.0), dict(grant_headroom=1.5)])
def test_grants_only_makes_overspend_impossible_at_full_throttle(r, rule):
    scn = replace(SMALL, request_rate=10.0 / r, realization=r)
    res = simulate_with_backstop(scn, _FullThrottle(), 2000, seed=11, mode="grants", **rule)
    assert res.final_spend.max() <= scn.budget


def test_grants_only_needs_exactly_one_grant_rule():
    with pytest.raises(ValueError):
        simulate_with_backstop(SMALL, _FullThrottle(), 10, mode="grants")
    with pytest.raises(ValueError):
        simulate_with_backstop(SMALL, _FullThrottle(), 10, mode="grants",
                               grant_share=0.5, grant_headroom=1.5)


def test_grants_only_spends_nearly_all_when_every_bid_is_charged():
    scn = replace(SMALL, request_rate=10.0, realization=1.0)
    c = NaiveController(scn.center_view(), prior_mean=10.0)
    res = simulate_with_backstop(scn, c, 1000, seed=12, mode="grants", grant_share=0.5)
    assert not res.overspent.any()
    assert res.final_spend.mean() > 0.97 * scn.budget
