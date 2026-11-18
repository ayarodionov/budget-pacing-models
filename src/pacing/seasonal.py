"""Traffic that varies with the time of day and the day of the week.

Model: models/seasonal/system.md
--------------------------------
The request rate is lambda(t) = lambda0 * s(t). The profile s(t) is a daily
cycle times a weekly one, normalised so that it averages 1 over a week:

    daily  : 1 + a * cos(2 pi (hour - peak_hour) / 24)     busy evenings, quiet mornings
    weekly : 1 on weekdays, ``weekend`` on Saturday and Sunday

So k(t) = lambda(t) * r = k0 * s(t), with the level k0 unknown. Pacing follows the
traffic: the budget is spread over the remaining *traffic*, not the remaining
time, so p stays roughly constant and more is spent in busy hours.

``SeasonalController`` extends ``BayesRiskController``:

* with a known profile (``profile``): the exposure is weighted by the profile
  (the simulator does that from ``center_profile``), so the Gamma posterior
  estimates the level k0 exactly as before; pacing divides by the remaining
  profile integral; the risk check charges the next step for p * step * s(t);
* without one: the plain controller, optionally with a short memory for the risk
  check (``risk_half_life``): pacing keeps the long-run estimate of k, the risk
  check uses recent data only, so it sees the current traffic.
"""

from __future__ import annotations

import numpy as np

from .controllers import BayesRiskController, NaiveController

DAY = 86_400.0
WEEK = 7 * DAY


def traffic_profile(t, daily_amplitude: float = 0.6, peak_hour: float = 20.0,
                    weekend: float = 0.7, start_weekday: int = 0) -> np.ndarray:
    """s(t) for t in seconds from the start (a Monday 00:00 by default), mean 1 per week."""
    t = np.asarray(t, dtype=float)
    hour = (t % DAY) / 3600.0
    daily = 1.0 + daily_amplitude * np.cos(2 * np.pi * (hour - peak_hour) / 24.0)
    weekday = (np.floor(t / DAY).astype(int) + start_weekday) % 7  # 0 = Monday
    weekly = np.where(weekday >= 5, weekend, 1.0)
    return daily * weekly / ((5 + 2 * weekend) / 7)


def step_profile(horizon: float, step: float, samples: int = 8, **kw) -> np.ndarray:
    """Average of s(t) over each control step: the factor that multiplies its traffic."""
    n = int(round(horizon / step))
    offsets = (np.arange(samples) + 0.5) / samples * step
    t = np.arange(n)[:, None] * step + offsets[None, :]
    return traffic_profile(t, **kw).mean(axis=1)


def traffic_target(profile: np.ndarray) -> np.ndarray:
    """Share of the budget to be spent by the end of each step when following traffic."""
    c = np.cumsum(profile)
    return c / c[-1]


class _ProfileMixin:
    """Traffic profile, recent-rate tracking and the two pacing goals."""

    def _init_profile(self, view, profile, pace_goal="traffic", half_life=None):
        if pace_goal not in ("traffic", "time"):
            raise ValueError("pace_goal must be 'traffic' or 'time'")
        self.pace_goal = pace_goal
        self.profile = None if profile is None else np.asarray(profile, dtype=float)
        if self.profile is not None:
            # exposure still to come at p = 1, from each step to the end of the planning time
            n_plan = max(1, int(round((view.horizon - self.endgame_time) / view.step)))
            prof = self.profile[:n_plan]
            self._remaining = np.concatenate([np.cumsum(prof[::-1])[::-1], [0.0]]) * view.step
        if pace_goal == "time" and self.profile is None and half_life is None:
            half_life = 3600.0  # even in time without a profile needs the current rate
        self.half_life = half_life
        self.discount = None if half_life is None else 0.5 ** (view.step / half_life)

    def _reset_recent(self, n_runs):
        self._s_prev = np.zeros(n_runs)
        self._e_prev = np.zeros(n_runs)
        self._s_recent = np.zeros(n_runs)
        self._e_recent = np.zeros(n_runs)

    def _track(self, observed_spend, observed_exposure):
        """Recent data only: discount old spend and exposure, add what became visible."""
        if self.discount is None:
            return
        g = self.discount
        self._s_recent = g * self._s_recent + (observed_spend - self._s_prev)
        self._e_recent = g * self._e_recent + (observed_exposure - self._e_prev)
        self._s_prev = np.array(observed_spend, dtype=float)
        self._e_prev = np.array(observed_exposure, dtype=float)

    def _step(self, t: float) -> int:
        return int(round(t / self.view.step))

    def _plan_exposure(self, t: float) -> float:
        if self.profile is None:
            return super()._plan_exposure(t)
        i = min(self._step(t), len(self._remaining) - 1)
        return max(self._remaining[i], self.view.step * self.profile[min(i, len(self.profile) - 1)])

    def _rate_factor(self, t: float) -> float:
        if self.profile is None:
            return 1.0
        return float(self.profile[min(self._step(t), len(self.profile) - 1)])

    def _pace(self, t, to_spend, k_est):
        if self.pace_goal == "traffic":
            return super()._pace(t, to_spend, k_est)
        # even in time: spend the rest at a constant rate, whatever the traffic now.
        # p = target rate / current rate; capped at 1 in hours that cannot keep up,
        # and the shortfall raises the target rate for the rest of the campaign.
        time_left = max(self.view.horizon - self.endgame_time - t, self.view.step)
        target_rate = to_spend / time_left
        if self.profile is not None:
            k_now = k_est * self._rate_factor(t)
        else:
            seen = (self._s_recent > 0) & (self._e_recent > 0)
            k_now = np.where(seen, (1.0 + self._s_recent) / np.where(seen, self._e_recent, 1.0), k_est)
        return np.clip(target_rate / k_now, 0.0, 1.0)


class SeasonalPacer(_ProfileMixin, NaiveController):
    """Plain pacing for traffic that varies, no risk check: for use under a token cap.

    ``pace_goal``: "traffic" (follow the traffic) or "time" (spend evenly in time).
    """

    def __init__(self, view, prior_mean, profile: np.ndarray | None = None,
                 pace_goal: str = "traffic", half_life: float | None = None, **kw):
        super().__init__(view, prior_mean, **kw)
        self._init_profile(view, profile, pace_goal, half_life)

    def reset(self, n_runs):
        super().reset(n_runs)
        self._reset_recent(n_runs)

    def decide(self, t, observed_spend, observed_exposure, inflight_exposure):
        seen, _ = self._events(observed_spend)
        self._track(seen, observed_exposure)
        return super().decide(t, observed_spend, observed_exposure, inflight_exposure)


class SeasonalController(_ProfileMixin, BayesRiskController):
    """``BayesRiskController`` for traffic that varies; see the module doc.

    profile         the center's belief about the traffic of each step (an array,
                    one factor per step, as from ``step_profile``), or None for
                    constant traffic. The simulator must weight the exposure with
                    the same array (``center_profile``).
    risk_half_life  seconds; if set, the risk check estimates k from recent data
                    only, discounting older spend and exposure with this half-life.
    pace_goal       "traffic" (follow the traffic, default) or "time" (spend evenly
                    in time: p inversely proportional to the current traffic).
                    Without a profile, "time" uses the recent-data rate (half-life
                    ``risk_half_life``, 1 h if not given) for pacing too.
    """

    def __init__(self, view, prior_mean, eps: float, profile: np.ndarray | None = None,
                 risk_half_life: float | None = None, pace_goal: str = "traffic", **kw):
        super().__init__(view, prior_mean, eps, **kw)
        self._init_profile(view, profile, pace_goal, risk_half_life)

    def reset(self, n_runs):
        super().reset(n_runs)
        self._reset_recent(n_runs)

    def _risk_posterior(self, observed_spend, observed_exposure, alpha, beta):
        if self.discount is None:
            return alpha, beta
        self._track(observed_spend, observed_exposure)
        seen = (self._s_recent > 0) & (self._e_recent > 0)
        return (np.where(seen, 1.0 + self._s_recent, alpha),
                np.where(seen, self._e_recent, beta))


def month_scenario(**kw):
    """A 30-day campaign at the default pace: B = 3.6 million units, step 60 s, d = 30 s."""
    from .model import Scenario

    base = dict(budget=3_600_000, horizon=30 * DAY, step=60.0, delay=30.0)
    base.update(kw)
    return Scenario(**base)


__all__ = ["traffic_profile", "step_profile", "traffic_target", "SeasonalController",
           "SeasonalPacer", "month_scenario", "DAY", "WEEK"]
