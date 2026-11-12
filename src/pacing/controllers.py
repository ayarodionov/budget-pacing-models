"""Center strategies.

All controllers share the same pacing rule (spend the expected remaining budget
evenly over the remaining planning time) and differ in how they protect against
overspend:

* ``NaiveController``      ignores spend that is still in flight; stops when the
                           visible budget is exhausted.
* ``PluginController``     keeps a reserve so that, with the *point estimate* of the
                           spend rate, P(in-flight spend > remaining) <= eps at each step.
* ``BayesRiskController``  the proposed strategy: Bayesian estimate of the spend rate
                           plus an explicit overspend-risk budget eps that is never
                           exceeded over the whole campaign (see MODEL.md, section 4).
"""

from __future__ import annotations

import numpy as np
from scipy.special import betainc, gammainc

from .model import CenterView


# --------------------------------------------------------------------------- tails
def poisson_tail(m: np.ndarray, mu: np.ndarray) -> np.ndarray:
    """P(X > m) for X ~ Poisson(mu), elementwise; m may be non-integer or negative."""
    m, mu = np.broadcast_arrays(np.floor(np.asarray(m, float)), np.asarray(mu, float))
    out = np.where(m < 0, 1.0, 0.0)
    ok = (m >= 0) & (mu > 0)
    out[ok] = gammainc(m[ok] + 1.0, mu[ok])
    return out


def gamma_poisson_tail(
    m: np.ndarray, alpha: np.ndarray, beta: np.ndarray, exposure: np.ndarray
) -> np.ndarray:
    """P(X > m) where X | k ~ Poisson(k * exposure) and k ~ Gamma(alpha, rate=beta).

    X is negative binomial with n = alpha, success prob beta / (beta + exposure), and
    P(X > m) = I_{exposure/(beta+exposure)}(m + 1, alpha)  (regularised incomplete beta).
    """
    m, alpha, beta, v = np.broadcast_arrays(
        np.floor(np.asarray(m, float)),
        np.asarray(alpha, float),
        np.asarray(beta, float),
        np.asarray(exposure, float),
    )
    out = np.where(m < 0, 1.0, 0.0)
    ok = (m >= 0) & (v > 0)
    out[ok] = betainc(m[ok] + 1.0, alpha[ok], v[ok] / (beta[ok] + v[ok]))
    return out


# --------------------------------------------------------------------------- base
class _Pacer:
    def __init__(
        self,
        view: CenterView,
        prior_mean: float,
        prior_shape: float = 2.0,
        endgame_time: float = 0.0,
        flat_after_data: bool = True,
    ):
        """prior_mean      the center's guess of k = lambda * r (spend per second at p = 1)
        prior_shape     confidence in that guess, in "pseudo-observed spends"
        endgame_time    plan to finish the expected spend this many seconds before T and
                        use the rest of the time to creep up to the budget safely
        flat_after_data use the guess only until the first spend is visible, then the
                        flat-prior posterior Gamma(1 + n, E). Its upper tail is the exact
                        (Garwood) confidence bound, so a wrong guess cannot bias the risk
                        estimate downwards. False = textbook conjugate Bayes throughout
                        (exact guarantee only on average over the prior).
        """
        self.view = view
        self.a0 = float(prior_shape)
        self.b0 = float(prior_shape) / float(prior_mean)
        self.endgame_time = float(endgame_time)
        self.flat_after_data = flat_after_data

    def reset(self, n_runs: int) -> None:
        pass

    def _posterior(self, observed_spend, observed_exposure):
        alpha = self.a0 + observed_spend
        beta = self.b0 + observed_exposure
        if self.flat_after_data:
            seen = observed_spend > 0
            alpha = np.where(seen, 1.0 + observed_spend, alpha)
            beta = np.where(seen, observed_exposure, beta)
        return alpha, beta, alpha / beta

    def _pace(self, t: float, to_spend: np.ndarray, k_est: np.ndarray) -> np.ndarray:
        plan_left = max(self.view.horizon - self.endgame_time - t, self.view.step)
        return np.clip(to_spend / (k_est * plan_left), 0.0, 1.0)


def _bisect_p(p_hi, ok_fn, iters: int = 30):
    """Largest p in [0, p_hi] with ok_fn(p) True, assuming ok_fn is monotone (True at 0)."""
    lo = np.zeros_like(p_hi)
    hi = p_hi.copy()
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        ok = ok_fn(mid)
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    return lo


# --------------------------------------------------------------------------- strategies
class NaiveController(_Pacer):
    def decide(self, t, observed_spend, observed_exposure, inflight_exposure):
        _, _, k_est = self._posterior(observed_spend, observed_exposure)
        remaining = self.view.budget - observed_spend
        return np.where(remaining > 0, self._pace(t, remaining, k_est), 0.0)


class PluginController(_Pacer):
    def __init__(self, view, prior_mean, eps: float, **kw):
        super().__init__(view, prior_mean, **kw)
        self.eps = eps

    def decide(self, t, observed_spend, observed_exposure, inflight_exposure):
        _, _, k_est = self._posterior(observed_spend, observed_exposure)
        remaining = self.view.budget - observed_spend
        p = self._pace(t, remaining - k_est * inflight_exposure, k_est)
        dt = self.view.step

        def ok(pp):
            return poisson_tail(remaining, k_est * (inflight_exposure + pp * dt)) <= self.eps

        bad = ~ok(p)
        if bad.any():
            p = p.copy()
            p[bad] = _bisect_p(
                p[bad],
                lambda pp: poisson_tail(
                    remaining[bad], k_est[bad] * (inflight_exposure[bad] + pp * dt)
                )
                <= self.eps,
            )
        return p


class BayesRiskController(_Pacer):
    """Proposed strategy.

    Overspend happens iff the total committed spend C (visible + in flight) ever
    exceeds B. With C_i the committed spend after control step i and F_i what the
    center knows when choosing p_i,

        P(overspend) = sum_i E[ P(C_i > B | F_i) - P(C_{i-1} > B | F_i) ].

    The bracket is computable exactly (gamma-Poisson tails), so the controller
    charges it to a risk budget and never lets the cumulative charge exceed eps.
    Each step may use at most ``eta`` times the risk budget that is still unused.

    If ``known_rate`` is given the controller knows k exactly (an oracle used as a
    lower bound on achievable underspend).
    """

    def __init__(self, view, prior_mean, eps: float, eta: float = 0.5,
                 known_rate: float | None = None, **kw):
        super().__init__(view, prior_mean, **kw)
        self.eps = float(eps)
        self.eta = float(eta)
        self.known_rate = known_rate
        self.risk_used = np.zeros(0)

    def reset(self, n_runs):
        self.risk_used = np.zeros(n_runs)

    def _tail(self, remaining, alpha, beta, exposure):
        if self.known_rate is not None:
            return poisson_tail(remaining, self.known_rate * exposure)
        return gamma_poisson_tail(remaining, alpha, beta, exposure)

    def decide(self, t, observed_spend, observed_exposure, inflight_exposure):
        alpha, beta, k_est = self._posterior(observed_spend, observed_exposure)
        if self.known_rate is not None:
            k_est = np.full_like(k_est, self.known_rate)
        remaining = self.view.budget - observed_spend
        dt = self.view.step
        w = inflight_exposure

        p = self._pace(t, remaining - k_est * w, k_est)
        q0 = self._tail(remaining, alpha, beta, w)  # risk already locked in
        allow = self.eta * np.clip(self.eps - self.risk_used, 0.0, None)
        q1 = self._tail(remaining, alpha, beta, w + p * dt)

        bad = (q1 - q0) > allow
        if bad.any():
            rb, ab, bb, wb, q0b, allowb = (
                remaining[bad], alpha[bad], beta[bad], w[bad], q0[bad], allow[bad]
            )
            p = p.copy()
            p[bad] = _bisect_p(
                p[bad],
                lambda pp: self._tail(rb, ab, bb, wb + pp * dt) - q0b <= allowb,
            )
            q1 = q1.copy()
            q1[bad] = self._tail(rb, ab, bb, wb + p[bad] * dt)

        self.risk_used += np.clip(q1 - q0, 0.0, None)
        return p
