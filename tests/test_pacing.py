import numpy as np
import pytest
from scipy.stats import nbinom, poisson

from pacing.controllers import (
    BayesRiskController,
    NaiveController,
    PluginController,
    gamma_poisson_tail,
    poisson_tail,
)
from pacing.model import Scenario, simulate, simulate_clients_once

SMALL = Scenario(budget=300, horizon=600.0, request_rate=200.0, realization=0.05,
                 delay=20.0, step=5.0)


# --------------------------------------------------------------------------- tails
@pytest.mark.parametrize("m", [-1, 0, 3, 17.6, 80])
@pytest.mark.parametrize("mu", [0.5, 10.0, 60.0])
def test_poisson_tail_matches_scipy(m, mu):
    expected = 1.0 if m < 0 else poisson.sf(np.floor(m), mu)
    assert poisson_tail(m, mu) == pytest.approx(expected, rel=1e-9, abs=1e-300)


@pytest.mark.parametrize("alpha,beta,v,m", [(3, 0.5, 2.0, 5), (50, 10, 7, 40), (500.0, 4.0, 1.5, 230)])
def test_gamma_poisson_tail_is_negative_binomial(alpha, beta, v, m):
    expected = nbinom.sf(m, alpha, beta / (beta + v))
    assert gamma_poisson_tail(m, alpha, beta, v) == pytest.approx(expected, rel=1e-9)


def test_gamma_poisson_tail_by_sampling():
    rng = np.random.default_rng(0)
    k = rng.gamma(4.0, 1 / 2.0, size=400_000)
    x = rng.poisson(k * 6.0)
    assert gamma_poisson_tail(15, 4.0, 2.0, 6.0) == pytest.approx((x > 15).mean(), abs=2e-3)


def test_tails_edge_cases():
    assert poisson_tail(-0.5, 3.0) == 1.0
    assert poisson_tail(0, 0.0) == 0.0
    assert gamma_poisson_tail(-1, 2.0, 1.0, 1.0) == 1.0
    assert gamma_poisson_tail(4, 2.0, 1.0, 0.0) == 0.0


# --------------------------------------------------------------------------- model
@pytest.mark.parametrize("n_clients", [1, 7, 500])
def test_number_of_clients_does_not_matter(n_clients):
    """Client-level story reduces to Poisson(lambda*r*p*dt) whatever N is."""
    rng = np.random.default_rng(n_clients)
    scn, p, dur = SMALL, 0.3, 10.0
    spends = np.array([simulate_clients_once(scn, n_clients, p, dur, rng)[0] for _ in range(4000)])
    mu = scn.request_rate * scn.realization * p * dur
    se = np.sqrt(mu / len(spends))
    assert abs(spends.mean() - mu) < 4 * se
    assert spends.var() == pytest.approx(mu, rel=0.08)  # Poisson: var == mean


def test_clients_receive_requests_evenly():
    rng = np.random.default_rng(1)
    _, tokens = simulate_clients_once(SMALL, 10, 1.0, 200.0, rng)
    assert tokens.sum() > 30_000
    assert tokens.max() / tokens.min() < 1.1


class _Const:
    def __init__(self, p):
        self.p, self.seen = p, []

    def reset(self, n):
        pass

    def decide(self, t, obs, exp_obs, inflight):
        self.seen.append((t, obs.copy(), exp_obs.copy(), inflight.copy()))
        return np.full(obs.shape, self.p)


def test_delay_bookkeeping():
    scn = SMALL  # delay 20s, step 5s -> 4 steps in flight
    c = _Const(0.2)
    simulate(scn, c, n_runs=3)
    for i, (t, obs, exp_obs, inflight) in enumerate(c.seen):
        unobserved_steps = min(i, scn.delay_steps)
        assert inflight == pytest.approx(np.full(3, 0.2 * scn.step * unobserved_steps))
        assert exp_obs == pytest.approx(np.full(3, 0.2 * scn.step * (i - unobserved_steps)))
        if i <= scn.delay_steps:
            assert (obs == 0).all()


def test_spend_rate_matches_scenario():
    res = simulate(SMALL, _Const(0.5), n_runs=2000, seed=3)
    mu = SMALL.spend_rate_at_p1 * 0.5 * SMALL.horizon
    assert res.final_spend.mean() == pytest.approx(mu, rel=0.01)


# --------------------------------------------------------------------------- controllers
def _k():
    return SMALL.spend_rate_at_p1


def test_naive_controller_overspends_when_there_is_delay():
    res = simulate(SMALL, NaiveController(SMALL.center_view(), prior_mean=_k()), 2000)
    assert res.overspent.mean() > 0.9


def test_bayes_risk_budget_is_never_exceeded_pathwise():
    c = BayesRiskController(SMALL.center_view(), prior_mean=_k(), eps=0.01)
    simulate(SMALL, c, 3000, seed=5)
    assert (c.risk_used <= 0.01 * (1 + 1e-9)).all()
    assert c.risk_used.max() > 0.005  # the budget is actually used


def test_bayes_guarantee_holds_when_rate_is_drawn_from_prior():
    """The guarantee is exact in the Bayesian sense: P(overspend) <= eps when k ~ prior."""
    eps, shape, mean, n = 0.03, 3.0, _k(), 20_000
    rng = np.random.default_rng(11)
    k = rng.gamma(shape, mean / shape, size=n)
    c = BayesRiskController(SMALL.center_view(), prior_mean=mean, prior_shape=shape, eps=eps,
                            flat_after_data=False)
    res = simulate(SMALL, c, n, seed=12, true_rates=k)
    rate = res.overspent.mean()
    assert rate <= eps + 3 * np.sqrt(eps * (1 - eps) / n)
    assert rate > eps / 3  # and it is not needlessly conservative


def test_textbook_prior_is_biased_when_guess_is_too_low():
    """Why flat_after_data exists: a too-low guess drags the estimate down -> eps exceeded."""
    eps, n = 0.02, 15_000
    c = BayesRiskController(SMALL.center_view(), prior_mean=_k() * 0.2, eps=eps,
                            flat_after_data=False)
    res = simulate(SMALL, c, n, seed=21)
    assert res.overspent.mean() > eps + 2 * np.sqrt(eps / n)


@pytest.mark.parametrize("prior_factor", [0.1, 0.2, 1.0, 5.0, 10.0])
def test_bayes_respects_eps_for_fixed_rate_and_wrong_prior(prior_factor):
    eps, n = 0.02, 15_000
    c = BayesRiskController(SMALL.center_view(), prior_mean=_k() * prior_factor, eps=eps)
    res = simulate(SMALL, c, n, seed=21)
    assert res.overspent.mean() <= eps + 3 * np.sqrt(eps / n)
    assert np.mean(res.final_spend) > 0.9 * SMALL.budget


def test_plugin_per_step_eps_overshoots_total_eps():
    """Checking eps at every step without a risk budget lets the total risk exceed eps."""
    eps, n = 0.01, 20_000
    res = simulate(SMALL, PluginController(SMALL.center_view(), prior_mean=_k(), eps=eps), n, seed=8)
    assert res.overspent.mean() > eps + 3 * np.sqrt(eps / n)


def test_oracle_never_spends_when_budget_is_gone():
    c = BayesRiskController(SMALL.center_view(), prior_mean=_k(), eps=0.01, known_rate=_k())
    c.reset(1)
    p = c.decide(10.0, np.array([SMALL.budget]), np.array([100.0]), np.array([0.0]))
    assert p[0] == 0.0


def test_impossible_budget_means_full_throttle_and_no_overspend():
    scn = Scenario(budget=10_000, horizon=600.0, request_rate=200.0, realization=0.05,
                   delay=20.0, step=5.0)  # max possible spend ~ 6000 < budget
    c = BayesRiskController(scn.center_view(), prior_mean=scn.spend_rate_at_p1, eps=1e-3)
    res = simulate(scn, c, 200, keep=1)
    assert (res.traj_p[0, scn.delay_steps + 1:] == 1.0).all()
    assert not res.overspent.any()


# --------------------------------------------------------------------------- update period & broadcasts
def test_delay_rounds_up_to_whole_control_periods():
    assert Scenario(delay=30.0, step=7.0).delay_steps == 5
    assert Scenario(delay=30.0, step=5.0).delay_steps == 6
    assert Scenario(delay=0.0, step=5.0).delay_steps == 0


def test_without_threshold_p_is_sent_every_period():
    c = BayesRiskController(SMALL.center_view(), prior_mean=_k(), eps=0.01)
    simulate(SMALL, c, 50)
    assert (c.broadcasts == SMALL.n_steps).all()


@pytest.mark.parametrize("threshold", [0.05, 0.25])
def test_threshold_cuts_messages_and_keeps_guarantee(threshold):
    eps, n = 0.02, 15_000
    c = BayesRiskController(SMALL.center_view(), prior_mean=_k(), eps=eps,
                            broadcast_threshold=threshold)
    res = simulate(SMALL, c, n, seed=31)
    assert (c.risk_used <= eps * (1 + 1e-9)).all()
    assert res.overspent.mean() <= eps + 3 * np.sqrt(eps / n)
    assert c.broadcasts.mean() < 0.5 * SMALL.n_steps
    assert np.mean(res.final_spend) > 0.9 * SMALL.budget


def test_held_p_is_reused_and_stop_is_always_sent():
    c = BayesRiskController(SMALL.center_view(), prior_mean=_k(), eps=0.01,
                            broadcast_threshold=0.5)
    res = simulate(SMALL, c, 200, seed=4, keep=200)
    p = res.traj_p
    changes = (np.diff(p, axis=1) != 0).sum(axis=1) + (p[:, 0] != 0)
    assert (changes == c.broadcasts).all()  # p only changes when a message is sent
    # whenever the controller stops (p goes to 0) it is a real change, so it is sent
    stops = ((p[:, :-1] > 0) & (p[:, 1:] == 0)).sum(axis=1)
    assert (stops <= c.broadcasts).all() and stops.sum() > 0


def test_eta_is_a_rate_per_time_not_per_step():
    for step in (1.0, 5.0, 25.0):
        sc = Scenario(step=step)
        c = BayesRiskController(sc.center_view(), prior_mean=1.0, eps=0.01, eta=0.5, eta_period=5.0)
        # share of unused risk still unused after 5 s is 1 - eta, whatever the step
        assert (1 - c.eta_step) ** (5.0 / step) == pytest.approx(0.5)
