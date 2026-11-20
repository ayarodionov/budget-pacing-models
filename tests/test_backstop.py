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


@pytest.mark.parametrize("mode", ["reports", "grants"])
@pytest.mark.parametrize("c", [0.7, 3.0])
def test_cap_with_unit_cost_never_overspends(mode, c):
    scn = replace(SMALL, budget=301.0, unit_cost=c, request_rate=20.0, realization=0.5)
    kw = {"grant_share": 0.5} if mode == "grants" else {}
    res = simulate_with_backstop(scn, _FullThrottle(), 1000, 5.0, seed=2, mode=mode, **kw)
    assert res.final_spend.max() <= scn.budget + 1e-9


# --------------------------------------------------------------------------- delivery delay
class _FixedP:
    def __init__(self, p):
        self.p = p

    def reset(self, n):
        pass

    def decide(self, t, obs, exp_obs, inflight):
        return np.full(obs.shape, self.p)


@pytest.mark.parametrize("r", [0.1, 1.0])
@pytest.mark.parametrize("delta_max", [0.0, 4.0])
def test_cap_a_needs_no_timing_assumption(r, delta_max):
    """(a) counts tokens when they are used: a delivery delay, even ignored, cannot overspend."""
    scn = replace(SMALL, request_rate=10.0 / r, realization=r, message_delay=4.0,
                  message_delay_max=delta_max)
    res = simulate_with_backstop(scn, _FullThrottle(), 2000, 5.0, seed=1)
    assert res.final_spend.max() <= scn.budget


@pytest.mark.parametrize("p", [1.0, 0.05, 0.01])
def test_cap_b_with_margin_covering_the_delay_never_overspends(p):
    scn = replace(SMALL, request_rate=100.0, realization=1.0, delay=5.0, message_delay=4.0,
                  message_delay_max=4.0)
    res = simulate_with_backstop(scn, _FixedP(p), 2000, seed=2, mode="grants", grant_share=0.5)
    assert res.final_spend.max() <= scn.budget


def test_cap_b_breaks_when_the_delay_is_ignored():
    """A grant used after its period ends is spend the center no longer counts."""
    scn = replace(SMALL, request_rate=100.0, realization=1.0, delay=5.0, message_delay=4.0)
    res = simulate_with_backstop(scn, _FixedP(0.05), 2000, seed=2, mode="grants", grant_share=0.5)
    assert res.overspent.mean() > 0.5


def test_cap_b_needs_the_delay_shorter_than_a_step():
    scn = replace(SMALL, message_delay=5.0, message_delay_max=5.0)
    with pytest.raises(ValueError):
        simulate_with_backstop(scn, _FixedP(0.1), 10, mode="grants", grant_share=0.5)


def test_age_limit_stops_capped_clients_too():
    scn = replace(SMALL, message_delay=4.0, message_age_limit=1.0)
    res = simulate_with_backstop(scn, _FixedP(0.5), 200, seed=2, mode="grants", grant_share=0.5)
    assert res.final_spend.max() == 0


# --------------------------------------------------------------------------- lost link
@pytest.mark.parametrize("mode", ["reports", "grants"])
@pytest.mark.parametrize("outage", [(300.0, 360.0), (450.0, 600.0)])
@pytest.mark.parametrize("r", [0.1, 1.0])
def test_caps_never_overspend_when_the_link_is_lost(mode, outage, r):
    scn = replace(SMALL, request_rate=10.0 / r, realization=r)
    kw = {"grant_share": 0.5} if mode == "grants" else {"failure_delay": 5.0, "wallet": 20}
    for ctl in (_FullThrottle(), NaiveController(scn.center_view(), prior_mean=10.0)):
        res = simulate_with_backstop(scn, ctl, 1000, seed=3, mode=mode, link_outage=outage, **kw)
        assert res.final_spend.max() <= scn.budget


def test_wallets_change_nothing_without_an_outage():
    scn = replace(SMALL, realization=0.1, request_rate=100.0)
    a = simulate_with_backstop(scn, NaiveController(scn.center_view(), prior_mean=10.0), 500, 5.0, seed=4)
    b = simulate_with_backstop(scn, NaiveController(scn.center_view(), prior_mean=10.0), 500, 5.0, seed=4,
                               wallet=30)
    np.testing.assert_array_equal(a.final_spend, b.final_spend)


def test_cut_off_clients_spend_only_their_wallets():
    """With p = 1 and the link lost for good at 50 s, (a) spends at most what was held."""
    scn = replace(SMALL, realization=1.0, request_rate=1.0)
    lost = dict(link_outage=(50.0, 600.0))
    pool = simulate_with_backstop(scn, _FullThrottle(), 300, 5.0, seed=5, **lost)
    wallets = simulate_with_backstop(scn, _FullThrottle(), 300, 5.0, seed=5, wallet=7, **lost)
    extra = wallets.final_spend - pool.final_spend
    assert extra.max() <= 7
    assert extra.mean() > 5


# --------------------------------------------------------------------------- random spend delay
@pytest.mark.parametrize("dist", [("exp", 10.0), ("lognormal", 8.0, 1.0)])
def test_cap_a_ignores_a_random_spend_delay(dist):
    scn = replace(SMALL, realization=1.0, request_rate=10.0, delay=10.0, delay_dist=dist)
    res = simulate_with_backstop(scn, _FullThrottle(), 1000, 5.0, seed=6)
    assert res.final_spend.max() <= scn.budget


def test_cap_b_needs_its_window_to_cover_the_longest_delay():
    """Window = mean delay: some spend shows up after the center stopped counting its grant."""
    mean_window = replace(SMALL, realization=1.0, request_rate=10.0, delay=30.0, delay_dist=("uniform", 10.0, 50.0))
    res = simulate_with_backstop(mean_window, _FixedP(0.5), 1000, seed=6, mode="grants", grant_share=0.5)
    assert res.overspent.mean() > 0.5
    max_window = replace(mean_window, delay=50.0)
    res = simulate_with_backstop(max_window, _FixedP(0.5), 1000, seed=6, mode="grants", grant_share=0.5)
    assert res.final_spend.max() <= max_window.budget


@pytest.mark.parametrize("mode", ["reports", "grants"])
def test_caps_never_overspend_with_bursty_traffic(mode):
    kw = {"failure_delay": 5.0} if mode == "reports" else {"mode": "grants", "grant_share": 0.5}
    scn = replace(SMALL, realization=1.0, request_rate=10.0)
    res = simulate_with_backstop(scn, _FullThrottle(), 1000, seed=4, rate_noise=(0.6, 30.0), **kw)
    assert res.final_spend.max() <= scn.budget
