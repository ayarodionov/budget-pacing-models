"""Budget pacing model: the scenario, a client-level reference simulator, and a
fast vectorised aggregate simulator used for Monte Carlo experiments.

Model (see MODEL.md for the full statement)
-------------------------------------------
* Bid requests arrive as a Poisson process with rate ``request_rate`` (lambda) and
  are routed to one of N clients uniformly at random.
* A client that receives a request generates (spends) a token with probability
  ``p``. ``p`` is broadcast by the center and is the same for every client.
* A token turns into budget spend with probability ``realization`` (r); a realised
  token costs exactly 1 budget unit.
* A spend becomes visible to the center ``delay`` (d) seconds after it happens.
  A change of ``p`` takes effect immediately.
* The center knows the budget B, the horizon T, the delay d and its own history of
  ``p``. It does NOT know lambda, r or N.

By Poisson thinning the budget spend in an interval of length dt with constant
``p`` is Poisson(lambda * r * p * dt), independently of N and of how requests are
split between clients. The aggregate simulator uses this; the client-level
simulator exists to check it (see tests).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Scenario:
    budget: int = 5_000  # B, budget units (1 realised token = 1 unit)
    horizon: float = 3_600.0  # T, seconds
    request_rate: float = 1_000.0  # lambda, bid requests / s  (unknown to center)
    realization: float = 0.1  # r, P(token -> budget spend) (unknown to center)
    delay: float = 30.0  # d, seconds until a spend is visible in the budget
    step: float = 5.0  # control period: the center may change p every `step` s

    @property
    def spend_rate_at_p1(self) -> float:
        """k = lambda * r: budget units per second when p = 1 (unknown to center)."""
        return self.request_rate * self.realization

    @property
    def n_steps(self) -> int:
        return int(round(self.horizon / self.step))

    @property
    def delay_steps(self) -> int:
        """Spend made in step j is visible from the start of step j + 1 + delay_steps.

        Rounded up, so the simulated center never sees a spend earlier than d.
        """
        return math.ceil(self.delay / self.step - 1e-9)

    def center_view(self) -> "CenterView":
        return CenterView(self.budget, self.horizon, self.delay, self.step)


@dataclass(frozen=True)
class CenterView:
    """The part of the scenario the center is allowed to know."""

    budget: int
    horizon: float
    delay: float
    step: float


class Controller(Protocol):
    def reset(self, n_runs: int) -> None: ...

    def decide(
        self,
        t: float,
        observed_spend: np.ndarray,
        observed_exposure: np.ndarray,
        inflight_exposure: np.ndarray,
    ) -> np.ndarray:
        """Return p for the next control period, one value per run.

        observed_spend     spend already visible in the budget
        observed_exposure  integral of p over the period whose spend is visible
        inflight_exposure  integral of p over the period whose spend is not visible yet
        """
        ...


@dataclass
class SimResult:
    final_spend: np.ndarray  # total spend once everything in flight has landed
    overspent: np.ndarray  # final_spend > budget
    max_pace_deviation: np.ndarray  # max_t |S(t)/B - t/T|, how uneven spending was
    traj_time: np.ndarray = field(default_factory=lambda: np.empty(0))
    traj_spend: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    traj_p: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))

    def summary(self, budget: int) -> dict[str, float]:
        n = len(self.final_spend)
        k = int(self.overspent.sum())
        under = np.clip(budget - self.final_spend, 0, None) / budget
        over = np.clip(self.final_spend - budget, 0, None)
        return {
            "runs": n,
            "overspend_prob": k / n,
            "overspend_prob_upper95": _clopper_pearson_upper(k, n),
            "mean_overspend_units_when_over": float(over[self.overspent].mean()) if k else 0.0,
            "mean_underspend_pct": 100 * float(under.mean()),
            "p95_underspend_pct": 100 * float(np.quantile(under, 0.95)),
            "mean_max_pace_dev_pct": 100 * float(self.max_pace_deviation.mean()),
        }


def _clopper_pearson_upper(k: int, n: int, conf: float = 0.95) -> float:
    from scipy.stats import beta

    if k >= n:
        return 1.0
    return float(beta.ppf(conf, k + 1, n - k))


def simulate(
    scn: Scenario,
    controller: Controller,
    n_runs: int,
    seed: int = 0,
    true_rates: np.ndarray | None = None,
    keep: int = 0,
) -> SimResult:
    """Run ``n_runs`` independent replications of one campaign in parallel.

    ``true_rates`` optionally gives each run its own k = lambda * r (used to test the
    Bayesian guarantee with k drawn from the prior). ``keep`` trajectories are stored.
    """
    rng = np.random.default_rng(seed)
    k = (
        np.full(n_runs, scn.spend_rate_at_p1)
        if true_rates is None
        else np.asarray(true_rates, dtype=float).copy()
    )
    n_steps, d_steps, dt = scn.n_steps, scn.delay_steps, scn.step
    ring = d_steps + 1  # holds steps i - d_steps .. i
    ring_p = np.zeros((n_runs, ring))
    ring_x = np.zeros((n_runs, ring), dtype=np.int64)

    obs_spend = np.zeros(n_runs, dtype=np.int64)
    obs_exposure = np.zeros(n_runs)
    inflight = np.zeros(n_runs)
    true_spend = np.zeros(n_runs, dtype=np.int64)
    max_dev = np.zeros(n_runs)

    keep = min(keep, n_runs)
    traj_spend = np.zeros((keep, n_steps + 1))
    traj_p = np.zeros((keep, n_steps))

    controller.reset(n_runs)
    for i in range(n_steps):
        p = np.clip(
            controller.decide(i * dt, obs_spend, obs_exposure, inflight), 0.0, 1.0
        )
        x = rng.poisson(k * p * dt)
        true_spend += x
        slot = i % ring
        ring_p[:, slot] = p
        ring_x[:, slot] = x
        inflight += p * dt

        j = i - d_steps  # this step's spend becomes visible before step i + 1
        if j >= 0:
            sj = j % ring
            obs_spend += ring_x[:, sj]
            obs_exposure += ring_p[:, sj] * dt
            inflight -= ring_p[:, sj] * dt
            np.maximum(inflight, 0.0, out=inflight)

        dev = np.abs(true_spend / scn.budget - (i + 1) / n_steps)
        np.maximum(max_dev, dev, out=max_dev)
        if keep:
            traj_spend[:, i + 1] = true_spend[:keep]
            traj_p[:, i] = p[:keep]

    return SimResult(
        final_spend=true_spend,
        overspent=true_spend > scn.budget,
        max_pace_deviation=max_dev,
        traj_time=np.arange(n_steps + 1) * dt,
        traj_spend=traj_spend,
        traj_p=traj_p,
    )


def simulate_clients_once(
    scn: Scenario, n_clients: int, p: float, duration: float, rng: np.random.Generator
) -> tuple[int, np.ndarray]:
    """Event-level simulation of the original client story for a fixed p.

    Returns (budget spend, tokens per client). Used to check that the aggregate
    model (Poisson(lambda*r*p*duration)) does not depend on the number of clients.
    """
    n_requests = rng.poisson(scn.request_rate * duration)
    client_of_request = rng.integers(0, n_clients, size=n_requests)
    token = rng.random(n_requests) < p
    realised = rng.random(n_requests) < scn.realization
    tokens_per_client = np.bincount(client_of_request[token], minlength=n_clients)
    return int((token & realised).sum()), tokens_per_client
