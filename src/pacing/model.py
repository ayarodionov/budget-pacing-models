"""Budget pacing model: the scenario, a client-level reference simulator, and a
fast vectorised aggregate simulator used for Monte Carlo experiments.

Model (see models/system.md for the full statement)
---------------------------------------------------
* Bid requests arrive as a Poisson process with rate ``request_rate`` (lambda) and
  are routed to one of N clients uniformly at random.
* A client that receives a request generates (spends) a token with probability
  ``p``. ``p`` is broadcast by the center and is the same for every client.
* A token turns into budget spend with probability ``realization`` (r); a realised
  token costs a fixed ``unit_cost`` (c) budget units, so spend is proportional to
  the number of realised tokens. c is known to the center: every visible spend is
  a budget decrement of exactly c, so the first one reveals it.
* A spend becomes visible to the center ``delay`` (d) seconds after it happens.
* A new ``p`` reaches the clients ``message_delay`` (delta) seconds after the
  center decides it (0 by default: immediately). The center knows only an upper
  bound ``message_delay_max`` (delta_max) and widens its in-flight window by it.
* The center knows the budget B, the horizon T, the delay d and its own history of
  ``p``. It does NOT know lambda, r or N.

By Poisson thinning the budget spend in an interval of length dt with constant
``p`` is c * Poisson(lambda * r * p * dt), independently of N and of how requests
are split between clients. Internally everything is counted in realised tokens
("spend events"); budget and spend are reported in budget units (c per event). The aggregate simulator uses this; the client-level
simulator exists to check it (see tests).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Scenario:
    budget: float = 5_000  # B, budget units
    horizon: float = 3_600.0  # T, seconds
    request_rate: float = 1_000.0  # lambda, bid requests / s  (unknown to center)
    realization: float = 0.1  # r, P(token -> budget spend) (unknown to center)
    delay: float = 30.0  # d, seconds until a spend is visible in the budget
    step: float = 5.0  # control period: the center may change p every `step` s
    unit_cost: float = 1.0  # c, budget units per realised token (known to center)
    message_delay: float = 0.0  # delta, seconds until a new p reaches the clients
    message_delay_max: float = 0.0  # delta_max >= delta, the bound the center plans with
    # clients ignore a p older than this (needs synchronised clocks); None = no check
    message_age_limit: float | None = None
    # random spend delay instead of the fixed d: ("exp", mean), ("uniform", lo, hi) or
    # ("lognormal", median, sigma), in seconds; truncated at 600 s. None = fixed d.
    # ``delay`` is then what a center with a fixed-delay model assumes.
    delay_dist: tuple | None = None

    @property
    def spend_rate_at_p1(self) -> float:
        """k = lambda * r: realised tokens (spend events) per second at p = 1.

        The spend rate in budget units is c * k * p. k is unknown to the center.
        """
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

    @property
    def center_delay_steps(self) -> int:
        """The center's in-flight window, in steps: ceil(d/step) + ceil(delta_max/step).

        Rounded up separately: ceil((d + delta_max)/step) can be one step shorter
        than the time a decided p needs to show up in the budget.
        """
        return self.delay_steps + math.ceil(self.message_delay_max / self.step - 1e-9)

    def center_view(self) -> "CenterView":
        return CenterView(self.budget, self.horizon, self.delay, self.step, self.unit_cost)

    @property
    def budget_events(self) -> float:
        """B / c: the budget counted in realised tokens."""
        return self.budget / self.unit_cost


@dataclass(frozen=True)
class CenterView:
    """The part of the scenario the center is allowed to know."""

    budget: float
    horizon: float
    delay: float
    step: float
    unit_cost: float = 1.0

    @property
    def budget_events(self) -> float:
        """B / c: the budget counted in realised tokens."""
        return self.budget / self.unit_cost


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

        observed_spend     spend already visible in the budget, in budget units
        observed_exposure  integral of p over the period whose spend is visible
        inflight_exposure  integral of p over the period whose spend is not visible yet
        """
        ...


@dataclass
class SimResult:
    final_spend: np.ndarray  # total spend (budget units) once everything in flight has landed
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
    halt_on_lag: float | None = None,
    link_outage: tuple[float, float] | None = None,
    silence_limit: float | None = None,
    feed_outage: tuple[float, float] | None = None,
    halt_on_stale_feed: float | None = None,
    center_pmf: np.ndarray | None = None,
    rate_profile: np.ndarray | None = None,
    center_profile: np.ndarray | None = None,
    target: np.ndarray | None = None,
) -> SimResult:
    """Run ``n_runs`` independent replications of one campaign in parallel.

    ``true_rates`` optionally gives each run its own k = lambda * r (used to test the
    Bayesian guarantee with k drawn from the prior). ``keep`` trajectories are stored.

    ``halt_on_lag`` (seconds, an allowance): the center measures the delivery delay
    from heartbeats (clients report the last p they applied; the first report comes
    one step after the first p arrives). If the measured delay exceeds
    ``message_delay_max`` + allowance, the center sends p = 0 from then on.

    ``link_outage`` (t0, t1): no message from the center reaches the clients during
    [t0, t1). Clients keep the last p they received until their ``silence_limit``
    (default 3 steps + delta_max) has passed since it arrived, then use p = 0. The
    center does not know about the outage and keeps deciding.

    ``feed_outage`` (t0, t1): the budget feed stalls during [t0, t1): the center
    keeps seeing the spend that was visible at t0. With ``halt_on_stale_feed``
    (seconds, a tolerance) the center notices that the feed is older than
    d + tolerance and sends p = 0 until it is fresh again.

    ``scn.delay_dist`` makes the spend delay random, drawn for each spend. The
    center then either keeps its fixed-delay bookkeeping (it assumes ``scn.delay``)
    or, with ``center_pmf``, weights its exposure by a delay distribution: the
    probability that a spend shows up after 0, 1, 2, ... steps (see ``delay_pmf``).
    E = sum of p * step * G(age) and W = sum of p * step * (1 - G(age)), where G is
    the cumulative of ``center_pmf``.

    Traffic that varies (see ``pacing.seasonal``): ``rate_profile[i]`` multiplies
    the request rate in step i (the truth). ``center_profile[i]`` is what the
    center believes it is: its exposure counts p * step * center_profile[i].
    ``target[i]`` is the share of the budget that should be spent by the end of
    step i (default: even in time); ``max_pace_deviation`` is measured against it.
    """
    rng = np.random.default_rng(seed)
    k = (
        np.full(n_runs, scn.spend_rate_at_p1)
        if true_rates is None
        else np.asarray(true_rates, dtype=float).copy()
    )
    n_steps, d_steps, dt = scn.n_steps, scn.delay_steps, scn.step
    ring = d_steps + 1  # spend of steps i - d_steps .. i (visibility in the budget)
    ring_x = np.zeros((n_runs, ring), dtype=np.int64)
    # the center's bookkeeping of decided p: in flight for center_delay_steps steps
    cd_steps = scn.center_delay_steps
    # decided p of the last steps, enough for the in-flight window and the message delay
    m_steps = int(scn.message_delay // dt)
    m_frac = scn.message_delay - m_steps * dt  # p_(i-m-1) is in effect for this long in step i
    hist = max(cd_steps, m_steps + 1) + 1
    true_pmf = delay_pmf(scn.delay_dist, dt) if scn.delay_dist is not None else None
    if true_pmf is not None:
        vis_ring = np.zeros((n_runs, len(true_pmf)), dtype=np.int64)  # spend by step it shows up
    if center_pmf is not None:
        if scn.message_delay or scn.message_delay_max:
            raise ValueError("center_pmf is not combined with a delivery delay of p")
        center_pmf = np.asarray(center_pmf, dtype=float)
        lc = len(center_pmf) - 1
        g_cdf = np.cumsum(center_pmf)[:lc]  # P(visible within m steps), m = 0 .. lc - 1
        lags = np.arange(lc)
        hist = max(hist, lc + 1)
        e_full = np.zeros(n_runs)  # exposure of steps old enough to be surely visible
    ring_p = np.zeros((n_runs, hist))
    if (rate_profile is not None or center_profile is not None) and (
            scn.message_delay or center_pmf is not None):
        raise ValueError("traffic profiles are simulated with a fixed spend delay and no delivery delay")
    cf = center_profile  # the center's belief about the traffic of each step

    obs_spend = np.zeros(n_runs, dtype=np.int64)
    obs_exposure = np.zeros(n_runs)
    inflight = np.zeros(n_runs)
    true_spend = np.zeros(n_runs, dtype=np.int64)
    max_dev = np.zeros(n_runs)

    keep = min(keep, n_runs)
    traj_spend = np.zeros((keep, n_steps + 1))
    traj_p = np.zeros((keep, n_steps))

    c = scn.unit_cost
    stale = scn.message_age_limit is not None and scn.message_delay > scn.message_age_limit
    halt_step = _halt_step(scn, halt_on_lag)
    if link_outage is not None and scn.message_delay:
        raise ValueError("link_outage is simulated with immediate delivery only")
    lo0, lo1 = (math.ceil(t / dt - 1e-9) for t in link_outage) if link_outage else (math.inf, math.inf)
    silence = silence_limit if silence_limit is not None else 3 * dt + scn.message_delay_max
    fo0, fo1 = (math.ceil(t / dt - 1e-9) for t in feed_outage) if feed_outage else (math.inf, math.inf)
    p_kept = np.zeros(n_runs)  # the last p the clients received before a link outage
    seen = obs_spend
    controller.reset(n_runs)
    for i in range(n_steps):
        if i < fo0 or i >= fo1:
            seen = obs_spend.copy()  # the feed is current; during an outage it stays frozen
        if center_pmf is not None:
            recent = ring_p[:, (i - 1 - lags) % hist]  # p of steps i-1, i-2, ...
            obs_exposure = e_full + (recent * g_cdf).sum(axis=1) * dt
            inflight = (recent * (1.0 - g_cdf)).sum(axis=1) * dt
        p = np.clip(
            controller.decide(i * dt, c * seen, obs_exposure, inflight), 0.0, 1.0
        )
        if i >= halt_step:
            p = np.zeros(n_runs)
        if halt_on_stale_feed is not None and fo0 <= i < fo1 and (i - fo0) * dt > halt_on_stale_feed:
            p = np.zeros(n_runs)  # the feed is too old: halt until it is fresh again
        ring_p[:, i % hist] = p
        p_clients = p
        if i == lo0 - 1:
            p_kept = p.copy()
        if lo0 <= i < lo1:
            # cut off: the last received p runs until the silence limit, then p = 0
            p_clients = p_kept if (i - (lo0 - 1)) * dt < silence else np.zeros(n_runs)
        if rate_profile is not None:
            x = rng.poisson(k * p_clients * dt * rate_profile[i])
        elif scn.message_delay == 0:
            x = rng.poisson(k * p_clients * dt)
        else:
            # clients use the p decided m_steps (+ a fraction) ago
            def decided(j):
                return ring_p[:, j % hist] if j >= 0 else 0.0

            exposure = decided(i - m_steps - 1) * m_frac + decided(i - m_steps) * (dt - m_frac)
            # clients that check the age of p ignore one that arrives too late
            x = np.zeros(n_runs, dtype=np.int64) if stale else rng.poisson(k * exposure)
        true_spend += x
        if true_pmf is None:
            ring_x[:, i % ring] = x
            j = i - d_steps  # this step's spend becomes visible before step i + 1
            if j >= 0:
                obs_spend += ring_x[:, j % ring]
        else:
            # each spend shows up after its own random number of steps
            r_len = len(true_pmf)
            vis_ring[:, (i + np.arange(r_len)) % r_len] += rng.multinomial(x, true_pmf)
            obs_spend += vis_ring[:, i % r_len]
            vis_ring[:, i % r_len] = 0

        if center_pmf is None and cf is None:
            inflight += p * dt
            j = i - cd_steps  # the center stops counting this decided p as in flight
            if j >= 0:
                obs_exposure += ring_p[:, j % hist] * dt
                inflight -= ring_p[:, j % hist] * dt
                np.maximum(inflight, 0.0, out=inflight)
        elif center_pmf is None:
            # exposure weighted by the traffic the center believes each step had
            inflight += p * dt * cf[i]
            j = i - cd_steps
            if j >= 0:
                obs_exposure += ring_p[:, j % hist] * dt * cf[j]
                inflight -= ring_p[:, j % hist] * dt * cf[j]
                np.maximum(inflight, 0.0, out=inflight)
        elif i - lc >= 0:
            e_full += ring_p[:, (i - lc) % hist] * dt

        aim = (i + 1) / n_steps if target is None else target[i]
        dev = np.abs(true_spend / scn.budget_events - aim)
        np.maximum(max_dev, dev, out=max_dev)
        if keep:
            traj_spend[:, i + 1] = c * true_spend[:keep]
            traj_p[:, i] = p[:keep]

    return SimResult(
        final_spend=c * true_spend,
        overspent=true_spend > scn.budget_events + 1e-9,
        max_pace_deviation=max_dev,
        traj_time=np.arange(n_steps + 1) * dt,
        traj_spend=traj_spend,
        traj_p=traj_p,
    )


def delay_pmf(dist: tuple, step: float, cap: float = 600.0) -> np.ndarray:
    """P(a spend shows up after exactly L steps), L = 0, 1, ...: L = ceil(delay / step).

    ``dist``: ("fixed", d), ("exp", mean), ("uniform", lo, hi) or ("lognormal",
    median, sigma), in seconds. Delays above ``cap`` are put at the cap.
    """
    from scipy import stats

    kind = dist[0]
    if kind == "fixed":
        pmf = np.zeros(math.ceil(dist[1] / step - 1e-9) + 1)
        pmf[-1] = 1.0
        return pmf
    f = {"exp": lambda: stats.expon(scale=dist[1]),
         "uniform": lambda: stats.uniform(loc=dist[1], scale=dist[2] - dist[1]),
         "lognormal": lambda: stats.lognorm(s=dist[2], scale=dist[1])}[kind]()
    lmax = math.ceil(cap / step)
    cdf = f.cdf(np.arange(lmax + 1) * step)
    pmf = np.diff(cdf, prepend=0.0)
    pmf[-1] += 1.0 - cdf[-1]
    last = np.nonzero(pmf > 1e-15)[0][-1]
    pmf = pmf[: last + 1]
    return pmf / pmf.sum()


def _halt_step(scn: Scenario, allowance: float | None) -> float:
    """Step from which a center that measures the delivery delay sends p = 0."""
    if allowance is None or scn.message_delay <= scn.message_delay_max + allowance:
        return math.inf
    # the first p arrives after delta; the next heartbeat, one step later, reports it
    # late enough to show that the delay exceeds delta_max + allowance
    detect = scn.message_delay_max + allowance + scn.step
    return math.ceil(detect / scn.step)


def simulate_clients_once(
    scn: Scenario, n_clients: int, p: float, duration: float, rng: np.random.Generator
) -> tuple[int, np.ndarray]:
    """Event-level simulation of the original client story for a fixed p.

    Returns (realised tokens, tokens per client); the budget spend is c times the
    first. Used to check that the aggregate model (Poisson(lambda*r*p*duration))
    does not depend on the number of clients.
    """
    n_requests = rng.poisson(scn.request_rate * duration)
    client_of_request = rng.integers(0, n_clients, size=n_requests)
    token = rng.random(n_requests) < p
    realised = rng.random(n_requests) < scn.realization
    tokens_per_client = np.bincount(client_of_request[token], minlength=n_clients)
    return int((token & realised).sum()), tokens_per_client
