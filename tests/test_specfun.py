import math

import numpy as np
import pytest
from scipy import special

from pacing import specfun
from pacing.controllers import BayesRiskController
from pacing.model import Scenario, simulate

REL = 1e-9  # required relative accuracy wherever the value is representable


def _rel_err(got, want):
    got, want = np.asarray(got, float), np.asarray(want, float)
    mask = np.abs(want) > 1e-280
    return np.max(np.abs(got[mask] - want[mask]) / np.abs(want[mask]), initial=0.0)


# --------------------------------------------------------------------------- lgamma
@pytest.mark.parametrize("x", [1e-3, 0.1, 0.5, 1.0, 1.5, 2.0, 3.7, 10.0, 171.3, 1e3, 2.5e4, 1e6])
def test_lgamma_scalar(x):
    assert specfun.lgamma(x) == pytest.approx(math.lgamma(x), rel=1e-13, abs=1e-13)


def test_lgamma_vectorised_matches_scalar():
    xs = np.concatenate([np.linspace(0.01, 5, 200), np.geomspace(5, 1e6, 200)])
    v = specfun.lgamma_v(xs)
    s = np.array([specfun.lgamma(x) for x in xs])
    assert np.array_equal(v, s)


# --------------------------------------------------------------------------- incomplete beta
def _beta_grid():
    rng = np.random.default_rng(0)
    a = np.floor(np.exp(rng.uniform(0, np.log(2e4), 3000))) + 1  # a = m + 1, integer
    b = np.exp(rng.uniform(np.log(0.5), np.log(2e4), 3000))  # alpha (posterior shape)
    x = np.exp(rng.uniform(np.log(1e-7), np.log(0.999), 3000))
    return a, b, x


def test_betainc_vectorised_matches_scipy():
    a, b, x = _beta_grid()
    assert _rel_err(specfun.betainc_v(a, b, x), special.betainc(a, b, x)) < REL


def test_betainc_scalar_matches_vectorised():
    a, b, x = (v[:300] for v in _beta_grid())
    s = np.array([specfun.betainc(*t) for t in zip(a, b, x)])
    np.testing.assert_allclose(s, specfun.betainc_v(a, b, x), rtol=1e-12, atol=0)


@pytest.mark.parametrize("target", [1e-3, 1e-6, 1e-9, 1e-12])
def test_small_upper_tails_are_accurate(target):
    """The controller works with tails around eps; they must be accurate in relative terms."""
    alpha, beta = 5001.0, 50.0  # ~5000 units seen over 50 s of full-throttle exposure
    v = 0.5  # in-flight exposure -> mean in-flight spend ~50
    mean = alpha * v / beta
    # find m with tail ~ target using scipy, then compare
    ms = np.arange(int(mean), int(mean) + 400)
    tails = special.betainc(ms + 1.0, alpha, v / (beta + v))
    m = ms[np.argmin(np.abs(np.log(tails) - np.log(target)))]
    want = special.betainc(m + 1.0, alpha, v / (beta + v))
    assert specfun.nb_tail(m, alpha, beta, v) == pytest.approx(want, rel=REL)


# --------------------------------------------------------------------------- incomplete gamma
def test_gammainc_vectorised_matches_scipy():
    rng = np.random.default_rng(1)
    a = np.floor(np.exp(rng.uniform(0, np.log(2e4), 3000))) + 1
    x = a * np.exp(rng.uniform(np.log(0.05), np.log(3.0), 3000))
    assert _rel_err(specfun.gammainc_lower_v(a, x), special.gammainc(a, x)) < REL


def test_gammainc_scalar_matches_vectorised():
    a = np.array([1.0, 3.0, 17.0, 250.0, 4000.0, 4000.0])
    x = np.array([0.3, 9.0, 12.0, 300.0, 3500.0, 4300.0])  # both branches
    s = np.array([specfun.gammainc_lower(*t) for t in zip(a, x)])
    np.testing.assert_allclose(s, specfun.gammainc_lower_v(a, x), rtol=1e-12)


def test_tail_edge_cases():
    assert specfun.nb_tail(-1, 2.0, 1.0, 1.0) == 1.0
    assert specfun.nb_tail(3, 2.0, 1.0, 0.0) == 0.0
    assert specfun.poisson_tail(-0.5, 2.0) == 1.0
    assert specfun.poisson_tail(3, 0.0) == 0.0
    assert specfun.betainc(2.0, 3.0, 0.0) == 0.0 and specfun.betainc(2.0, 3.0, 1.0) == 1.0


# --------------------------------------------------------------------------- end to end
def test_controller_gives_same_results_with_portable_math():
    scn = Scenario(budget=800, horizon=900.0, request_rate=200.0, realization=0.05,
                   delay=20.0, step=5.0)
    out = {}
    for impl in ("scipy", "portable"):
        c = BayesRiskController(scn.center_view(), prior_mean=scn.spend_rate_at_p1,
                                eps=0.01, tails=impl)
        out[impl] = (simulate(scn, c, 3000, seed=42), c.risk_used.copy())
    (r_s, risk_s), (r_p, risk_p) = out["scipy"], out["portable"]
    assert np.mean(r_s.final_spend == r_p.final_spend) > 0.99
    np.testing.assert_allclose(risk_s, risk_p, rtol=1e-6, atol=1e-12)


@pytest.mark.parametrize("x", [-0.999, -0.3, -1e-5, -1e-12, 0.0, 1e-17, 1e-9, 0.25, 3.0])
def test_log1p_without_library(x):
    assert specfun.log1p(x) == pytest.approx(math.log1p(x), rel=1e-14, abs=1e-300)
    assert specfun.log1p_v(np.array([x]))[0] == specfun.log1p(x)
