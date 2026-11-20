"""Run the models on a scenario described in a TOML file with real-world measurements.

Run:  uv run python -m pacing.scenario_file scenarios/example.toml [--runs N]
      -> results/scenarios/<name>.md

The file format is documented in ``scenarios/example.toml`` (every field, with
what is required and what is optional) and in models/system.md §8.6. This module
reads the file, checks it, turns it into the simulator's parameters, runs the
chosen models and writes one table.
"""

from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .backstop import simulate_with_backstop
from .model import Scenario, delay_pmf, simulate
from .seasonal import DAY, SeasonalController, SeasonalPacer, traffic_profile, traffic_target

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MODELS = ("probabilistic", "token_cap_a", "token_cap_b")
PERCENTILES = (("p50", 0.5), ("p90", 0.9), ("p99", 0.99), ("p999", 0.999))


class ScenarioError(ValueError):
    """The scenario file is incomplete or inconsistent."""


@dataclass
class Plan:
    """Everything needed to run the models, built from a scenario file."""

    name: str
    scn: Scenario  # the world; ``delay`` = what a center with a fixed-delay model assumes
    models: list[str]
    runs: int
    seed: int
    eps: float | None
    pace_goal: str
    prior_mean: float
    rate_profile: np.ndarray | None
    center_profile: np.ndarray | None
    center_pmf: np.ndarray | None
    controller: dict = field(default_factory=dict)  # extra controller options
    rate_noise: tuple[float, float] | None = None  # random fluctuations beyond Poisson
    sim: dict = field(default_factory=dict)  # extra simulate options (probabilistic)
    cap_sim: dict = field(default_factory=dict)  # extra simulate_with_backstop options
    failure_delay: float = 0.0
    wallet: int | None = None
    grant_share: float = 0.5
    cap_b_window: float = 0.0
    notes: list[str] = field(default_factory=list)  # warnings and fallbacks, for the report
    charts: bool = False  # also draw results/scenarios/<name>.png


# ------------------------------------------------------------------ reading


def _get(d: dict, key: str, kind=float, default=..., where: str = ""):
    if key not in d:
        if default is ...:
            raise ScenarioError(f"missing required field {where}{key}")
        return default
    v = d[key]
    try:
        return kind(v)
    except (TypeError, ValueError):
        raise ScenarioError(f"{where}{key} must be a {kind.__name__}, got {v!r}") from None


def _hour_of_week(hourly) -> np.ndarray:
    a = np.asarray(hourly, dtype=float)
    if a.shape != (168,):
        raise ScenarioError(f"an hourly profile needs 168 values (24 h × 7 days from Monday 00:00), got {a.size}")
    if np.any(a < 0) or a.sum() <= 0:
        raise ScenarioError("an hourly profile must be non-negative and not all zero")
    return a / a.mean()


def _step_values(start: float, horizon: float, step: float, fn) -> np.ndarray:
    """Average of fn(t) over each control step; t in seconds from Monday 00:00."""
    n = int(round(horizon / step))
    sub = (np.arange(8) + 0.5) / 8 * step
    t = start + np.arange(n)[:, None] * step + sub[None, :]
    return fn(t).mean(axis=1)


def _profile(spec: dict, start: float, horizon: float, step: float, where: str) -> np.ndarray | None:
    kind = str(spec.get("profile", "none")).lower()
    kind = {"stable": "none", "periodic": "builtin"}.get(kind, kind)
    if kind == "none":
        return None
    if kind == "builtin":
        kw = {k: float(spec[k]) for k in ("daily_amplitude", "peak_hour", "weekend") if k in spec}
        return _step_values(start, horizon, step, lambda t: traffic_profile(t, **kw))
    if kind == "hourly":
        hw = _hour_of_week(_get(spec, "hourly", list, where=where))
        return _step_values(start, horizon, step, lambda t: hw[(np.floor(t / 3600).astype(int)) % 168])
    raise ScenarioError(f'{where}profile must be "stable", "periodic" ("builtin") or "hourly", got {kind!r}')


def _shape(traffic: dict, start: float, horizon: float, step: float) -> np.ndarray | None:
    """The deterministic traffic factor of each step: periodic × trend × steps (None = constant)."""
    n = int(round(horizon / step))
    t = (np.arange(n) + 0.5) * step  # seconds from the start of the campaign
    factor = _profile(traffic, start, horizon, step, "traffic.")
    parts = [] if factor is None else [factor]
    if "trend" in traffic:
        tr = traffic["trend"]
        a, b = _get(tr, "start", where="traffic.trend."), _get(tr, "end", where="traffic.trend.")
        if a <= 0 or b <= 0:
            raise ScenarioError("traffic.trend start and end must be > 0")
        parts.append(a + (b - a) * t / horizon)
    if "steps" in traffic:
        st = traffic["steps"]
        at = [float(v) * 3600 for v in _get(st, "at_hours", list, where="traffic.steps.")]
        fs = [float(v) for v in _get(st, "factor", list, where="traffic.steps.")]
        if len(at) != len(fs) or not at:
            raise ScenarioError("traffic.steps needs at_hours and factor of the same, non-zero length")
        if any(b <= a for a, b in zip(at, at[1:])) or any(f < 0 for f in fs):
            raise ScenarioError("traffic.steps: at_hours must increase and factors be >= 0")
        level = np.ones(n)
        for a, f in zip(at, fs):
            level[t >= a] = f
        parts.append(level)
    if not parts:
        return None
    out = np.ones(n)
    for part in parts:
        out = out * part
    return out


def _fluctuations(traffic: dict) -> tuple[float, float] | None:
    if "fluctuations" not in traffic:
        return None
    fl = traffic["fluctuations"]
    cv = _get(fl, "cv", where="traffic.fluctuations.")
    tau = _get(fl, "correlation_seconds", where="traffic.fluctuations.")
    if cv < 0 or tau <= 0:
        raise ScenarioError("traffic.fluctuations needs cv >= 0 and correlation_seconds > 0")
    return cv, tau


def _delay(spend: dict) -> tuple[float, tuple | None]:
    """(fixed delay or None, empirical distribution or None) from [spend]."""
    has_fixed = "delay_seconds" in spend
    pct = spend.get("delay_percentiles")
    if has_fixed == (pct is not None):
        raise ScenarioError("give exactly one of spend.delay_seconds and [spend.delay_percentiles]")
    if has_fixed:
        d = _get(spend, "delay_seconds", where="spend.")
        if d < 0:
            raise ScenarioError("spend.delay_seconds must be >= 0")
        return d, None
    where = "spend.delay_percentiles."
    pts = [(0.0, 0.0)]
    for key, q in PERCENTILES:
        if key in pct:
            pts.append((_get(pct, key, where=where), q))
    pts.append((_get(pct, "max", where=where), 1.0))
    if len(pts) < 3:
        raise ScenarioError("spend.delay_percentiles needs at least p50 and max")
    ds = [d for d, _ in pts]
    if any(b < a for a, b in zip(ds, ds[1:])):
        raise ScenarioError(f"spend.delay_percentiles must increase: {ds}")
    return None, ("empirical", tuple(pts))


def load(path: str | Path) -> Plan:
    path = Path(path)
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    camp, traffic, spend = cfg.get("campaign", {}), cfg.get("traffic", {}), cfg.get("spend", {})
    infra, center, models_cfg, run = (cfg.get(k, {}) for k in ("infrastructure", "center", "models", "run"))
    notes: list[str] = []

    # --- campaign
    budget = _get(camp, "budget", where="campaign.")
    lengths = [k for k in ("length_days", "length_hours", "length_seconds") if k in camp]
    if len(lengths) != 1:
        raise ScenarioError("give exactly one of campaign.length_days, length_hours, length_seconds")
    horizon = _get(camp, lengths[0], where="campaign.") * {"length_days": DAY, "length_hours": 3600.0,
                                                           "length_seconds": 1.0}[lengths[0]]
    wd = str(camp.get("start_weekday", "monday")).lower()
    if wd not in WEEKDAYS:
        raise ScenarioError(f"campaign.start_weekday must be a day name, got {wd!r}")
    start = WEEKDAYS.index(wd) * DAY + _get(camp, "start_hour", default=0.0) * 3600.0
    unit_cost = _get(camp, "unit_cost", default=1.0)
    pace_goal = str(camp.get("pace_goal", "traffic"))
    if pace_goal not in ("traffic", "time"):
        raise ScenarioError('campaign.pace_goal must be "traffic" or "time"')

    # --- models to run
    models = [m.lower() for m in models_cfg.get("run", list(MODELS))]
    for m in models:
        if m not in MODELS:
            raise ScenarioError(f"models.run: unknown model {m!r}; use {', '.join(MODELS)}")
    eps = _get(camp, "eps", default=None) if "eps" in camp else None
    if "probabilistic" in models and eps is None:
        raise ScenarioError("campaign.eps is required for the probabilistic model")

    # --- infrastructure
    step = _get(infra, "control_period_seconds", where="infrastructure.")
    if step <= 0 or horizon / step < 2:
        raise ScenarioError("infrastructure.control_period_seconds must be > 0 and well below the campaign length")
    delta = _get(infra, "delivery_delay_seconds", default=0.0)
    delta_max = _get(infra, "delivery_delay_max_seconds", default=delta)
    if delta_max < delta:
        notes.append(f"delivery_delay_max ({delta_max:g} s) is below the true delivery delay ({delta:g} s): "
                     "the center does not plan for the whole delay, so overspend is expected")
    age_limit = _get(infra, "message_age_limit_seconds", default=None) if "message_age_limit_seconds" in infra else None
    silence = _get(infra, "silence_limit_seconds", default=3 * step + delta_max)

    # --- traffic
    request_rate = _get(traffic, "request_rate", where="traffic.")
    rate_profile = _shape(traffic, start, horizon, step)
    rate_noise = _fluctuations(traffic)
    if (rate_profile is not None or rate_noise is not None) and delta > 0:
        raise ScenarioError("a traffic profile, trend, steps or fluctuations together with a delivery delay "
                            "of p is not supported by the simulator")
    if rate_noise is not None and "probabilistic" in models:
        notes.append(f"the traffic fluctuates beyond Poisson (cv {rate_noise[0]:g}, about "
                     f"{rate_noise[1]:g} s): the probabilistic model's ε guarantee assumes Poisson traffic and "
                     "may not hold (see models/system.md §3); the token caps are not affected")

    # --- spend
    r = _get(spend, "charged_share", where="spend.")
    if not 0 < r <= 1:
        raise ScenarioError("spend.charged_share must be in (0, 1]")
    fixed_d, dist = _delay(spend)
    failure_delay = _get(spend, "failure_report_seconds", default=0.0)

    # --- the center
    prior_mean = _get(center, "prior_mean", where="center.")
    belief = str(center.get("profile_belief", "true" if rate_profile is not None else "none")).lower()
    if belief == "true":
        if rate_profile is None:
            raise ScenarioError('center.profile_belief = "true" needs a traffic profile')
        center_profile = rate_profile
    elif belief == "none":
        center_profile = None
    elif belief == "hourly":
        center_profile = _profile({"profile": "hourly", "hourly": center.get("hourly_belief")},
                                  start, horizon, step, "center.")
    else:
        raise ScenarioError('center.profile_belief must be "true", "none" or "hourly"')

    center_pmf = None
    if dist is not None:
        dbelief = center.get("delay_belief", "true")
        pmf_true = delay_pmf(dist, step)
        longest = dist[1][-1][0]
        mean = float((np.arange(len(pmf_true)) * pmf_true).sum() * step)
        if str(dbelief).lower() == "true":
            if delta_max > 0:
                notes.append("the center cannot weight by the delay distribution together with a delivery "
                             f"delay of p; it assumes the longest delay ({longest:g} s) instead")
                assumed = longest
            else:
                center_pmf, assumed = pmf_true, mean
        else:
            assumed = float(dbelief)
        world_delay = assumed
    else:
        world_delay = fixed_d
        longest = fixed_d

    scn = Scenario(budget=budget, horizon=horizon, request_rate=request_rate,
                   realization=r, delay=world_delay, step=step, unit_cost=unit_cost, message_delay=delta,
                   message_delay_max=delta_max, message_age_limit=age_limit, delay_dist=dist)

    # --- controller and run options
    controller = {}
    for key, opt in (("recent_memory_seconds", "risk_half_life"), ("max_rise", "max_rise"),
                     ("endgame_seconds", "endgame_time"), ("broadcast_threshold", "broadcast_threshold")):
        if key in center:
            controller[opt] = _get(center, key)
    if center.get("plan_for_silence", True):
        controller["max_silence"] = silence
    sim, cap_sim = {}, {}
    for key, opt in (("link_outage", "link_outage"), ("feed_outage", "feed_outage")):
        if key in infra:
            v = infra[key]
            if not (isinstance(v, list) and len(v) == 2 and v[0] < v[1]):
                raise ScenarioError(f"infrastructure.{key} must be [start, end] in seconds from the start")
            sim[opt] = (float(v[0]), float(v[1]))
    if "link_outage" in sim:
        if delta > 0:
            raise ScenarioError("a link outage together with a delivery delay of p is not supported")
        sim["silence_limit"] = silence
        cap_sim = {"link_outage": sim["link_outage"], "silence_limit": silence}
    if "halt_on_stale_feed_seconds" in center:
        sim["halt_on_stale_feed"] = _get(center, "halt_on_stale_feed_seconds")
    if "halt_on_lag_seconds" in center:
        sim["halt_on_lag"] = _get(center, "halt_on_lag_seconds")
    if "feed_outage" in sim and any(m != "probabilistic" for m in models):
        notes.append("the token-cap simulation has no budget-feed outage; it is applied to the probabilistic model only")

    wallet = int(models_cfg["wallet_tokens"]) if "wallet_tokens" in models_cfg else None
    grant_share = _get(models_cfg, "grant_share", default=0.5)
    if "token_cap_b" in models:
        if delta_max >= step:
            raise ScenarioError("token cap (b) needs delivery_delay_max_seconds below the control period")
        if dist is not None:
            notes.append(f"token cap (b) counts each grant for the longest delay ({longest:g} s): its guarantee "
                         "needs a hard maximum")

    # --- feasibility
    k = request_rate * r
    mean_shape = 1.0 if rate_profile is None else float(rate_profile.mean())
    need = budget / unit_cost / (k * horizon * mean_shape)
    if need > 1:
        notes.append(f"the budget cannot be spent even at p = 1 (it needs p ≈ {need:.2f} on average)")
    if pace_goal == "time" and rate_profile is not None:
        worst = budget / unit_cost / horizon / (k * rate_profile.min())
        if worst > 1:
            notes.append(f"spending evenly needs p ≈ {worst:.2f} in the quietest hour: not possible, "
                         "the quiet hours will run at p = 1 (see models/seasonal/probabilistic.md §3)")

    return Plan(name=str(camp.get("name", path.stem)), scn=scn, models=models,
                runs=int(_get(run, "runs", default=1000)), seed=int(_get(run, "seed", default=1)),
                charts=bool(run.get("charts", False)),
                eps=eps, pace_goal=pace_goal, prior_mean=prior_mean, rate_profile=rate_profile,
                center_profile=center_profile, center_pmf=center_pmf, rate_noise=rate_noise,
                controller=controller, sim=sim,
                cap_sim=cap_sim, failure_delay=failure_delay, wallet=wallet, grant_share=grant_share,
                cap_b_window=longest, notes=notes)


# ------------------------------------------------------------------ running


def _target(plan: Plan) -> np.ndarray | None:
    if plan.pace_goal == "traffic" and plan.rate_profile is not None:
        return traffic_target(plan.rate_profile)
    return None  # even in time


def run_model(plan: Plan, model: str, runs: int | None = None):
    """(result, broadcasts per hour or None) for one model."""
    n = runs or plan.runs
    scn = plan.scn
    common = dict(rate_profile=plan.rate_profile, center_profile=plan.center_profile, target=_target(plan),
                  rate_noise=plan.rate_noise)
    if model == "probabilistic":
        ctl = SeasonalController(scn.center_view(), prior_mean=plan.prior_mean, eps=plan.eps,
                                 profile=plan.center_profile, pace_goal=plan.pace_goal, **plan.controller)
        res = simulate(scn, ctl, n, seed=plan.seed, center_pmf=plan.center_pmf, keep=1, **common, **plan.sim)
        return res, ctl.broadcasts.mean() / (scn.horizon / 3600)
    # under a cap: plain pacing, no risk check
    pacer_kw = {k: v for k, v in plan.controller.items() if k in ("endgame_time", "max_rise")}
    half_life = plan.controller.get("risk_half_life")
    if model == "token_cap_b":
        scn = replace(scn, delay=plan.cap_b_window)  # (b) counts a grant for the longest delay
    pacer = SeasonalPacer(scn.center_view(), prior_mean=plan.prior_mean, profile=plan.center_profile,
                          pace_goal=plan.pace_goal, half_life=half_life, **pacer_kw)
    if model == "token_cap_a":
        res = simulate_with_backstop(scn, pacer, n, failure_delay=plan.failure_delay, seed=plan.seed,
                                     wallet=plan.wallet, keep=1, **common, **plan.cap_sim)
    else:
        res = simulate_with_backstop(scn, pacer, n, seed=plan.seed, mode="grants",
                                     grant_share=plan.grant_share, keep=1, **common, **plan.cap_sim)
    return res, None


def _describe_stream(plan: Plan) -> str:
    parts = ["stable" if plan.rate_profile is None else "varies (periodic, trend or steps)"]
    if plan.rate_noise is not None:
        parts.append(f"random fluctuations beyond Poisson (cv {plan.rate_noise[0]:g}, about {plan.rate_noise[1]:g} s)")
    return ", ".join(parts)


MODEL_COLORS = {"probabilistic": "#2a78d6", "token_cap_a": "#eb6834", "token_cap_b": "#1baf7a"}
MODEL_LABELS = {"probabilistic": "probabilistic", "token_cap_a": "token cap (a)", "token_cap_b": "token cap (b)"}
MODEL_STYLES = {"probabilistic": "-", "token_cap_a": "--", "token_cap_b": ":"}


def chart(plan: Plan, results: dict, path: Path) -> None:
    """Four panels: the traffic, one campaign per model, final spend over all campaigns, overspend."""
    import matplotlib

    matplotlib.use("Agg")
    from .experiments import INK, INK2, plt  # also applies the project's chart style
    from .model import RateNoise

    scn = plan.scn
    n, budget = scn.n_steps, scn.budget
    unit, div = ("days", DAY) if scn.horizon >= 2 * DAY else ("hours", 3600.0)
    t = (np.arange(n) + 0.5) * scn.step / div
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.5))
    ax_t, ax_r, ax_f, ax_o = axes.ravel()

    # 1. the bid-request stream
    base = np.ones(n) if plan.rate_profile is None else plan.rate_profile
    if plan.rate_noise is not None:
        noise = RateNoise(plan.rate_noise, scn.step, 1, np.random.default_rng(0))
        sample = base * np.array([noise.next()[0] for _ in range(n)])
        ax_t.plot(t, sample, color=INK2, lw=0.6, alpha=0.6, label="one random sample")
    ax_t.plot(t, base, color=INK, lw=1.4, label="average pattern")
    ax_t.set(title="Bid-request stream", xlabel=f"time, {unit}", ylabel="rate, × the average")
    ax_t.set_ylim(bottom=0)
    ax_t.legend(fontsize=8)

    # 2. one campaign per model, against the target
    target = _target(plan)
    target = (np.arange(1, n + 1) / n) if target is None else target
    target = np.concatenate([[0.0], target])
    tt = np.arange(n + 1) * scn.step / div
    for model, (res, _) in results.items():
        ax_r.plot(tt, 100 * (res.traj_spend[0] / budget - target), color=MODEL_COLORS[model], lw=1.3,
                  ls=MODEL_STYLES[model], label=MODEL_LABELS[model])
    ax_r.axhline(0, color=INK, lw=0.8)
    goal = "the traffic" if _target(plan) is not None else "even spending"
    ax_r.set(title=f"One campaign per model (target: {goal})", xlabel=f"time, {unit}",
             ylabel="ahead (+) or behind (−), % of B")
    ax_r.legend(fontsize=8)

    # 3. final spend over all campaigns
    for model, (res, _) in results.items():
        d = 100 * (res.final_spend - budget) / budget
        ax_f.hist(d, bins=40, histtype="step", lw=1.5, color=MODEL_COLORS[model], label=MODEL_LABELS[model])
    ax_f.axvline(0, color=INK, lw=0.8)
    ax_f.set(title=f"Final spend, all {plan.runs} campaigns", xlabel="final spend − B, % of B "
             "(right of 0: overspend)", ylabel="campaigns")
    ax_f.legend(fontsize=8)

    # 4. overspend rate against ε
    models = list(results)
    rates = [100 * results[m][0].overspent.mean() for m in models]
    x = np.arange(len(models))
    bars = ax_o.bar(x, rates, 0.6, color=[MODEL_COLORS[m] for m in models])
    ax_o.bar_label(bars, labels=[f"{r:.2f} %" if r else "0" for r in rates], fontsize=8, color=INK2, padding=2)
    if plan.eps is not None and "probabilistic" in models:
        ax_o.axhline(100 * plan.eps, color=INK2, ls="--", lw=1, label=f"ε = {plan.eps:g}")
        ax_o.legend(fontsize=8)
    ax_o.set_xticks(x, [MODEL_LABELS[m] for m in models])
    top = max(rates + [100 * (plan.eps or 0)])
    ax_o.set(title="Campaigns that overspent", ylabel="% of campaigns", ylim=(0, top * 1.4 or 1))
    ax_o.grid(axis="x", visible=False)

    for ax in axes.ravel():
        ax.set_axisbelow(True)
    fig.suptitle(f"Scenario: {plan.name}", x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def report(plan: Plan, results: dict, chart_file: str | None = None) -> str:
    scn = plan.scn
    lines = [f"# Scenario: {plan.name}", "",
             "Generated by `uv run python -m pacing.scenario_file`. "
             "Model descriptions: [models/system.md](../../models/system.md).", "",
             "## The scenario as simulated", "",
             f"- campaign: B = {scn.budget:,.0f} units over {scn.horizon / DAY:g} days, "
             f"control period {scn.step:g} s, pacing goal: "
             f"{'follow the traffic' if plan.pace_goal == 'traffic' else 'even in time'}",
             f"- traffic: λ = {scn.request_rate:g} requests/s, charged share r = {scn.realization:g}, "
             f"stream: {_describe_stream(plan)}; "
             f"the center's belief: {'none' if plan.center_profile is None else ('the true profile' if plan.center_profile is plan.rate_profile else 'a different profile')}",
             f"- spend delay: {'fixed ' + format(scn.delay, 'g') + ' s' if scn.delay_dist is None else 'measured distribution (longest ' + format(plan.cap_b_window, 'g') + ' s)'}; "
             f"the center {'weights by the distribution' if plan.center_pmf is not None else 'assumes ' + format(scn.delay, 'g') + ' s'}",
             f"- delivery delay of p: {scn.message_delay:g} s (planned for {scn.message_delay_max:g} s)",
             f"- {plan.runs} runs per model, seed {plan.seed}", ""]
    if plan.notes:
        lines += ["**Notes:**", ""] + [f"- {n}" for n in plan.notes] + [""]
    lines += ["## Results", "",
              "| model | P(overspend) | 95% upper bound | worst overspend, units | mean underspend, units | "
              "mean underspend % | p95 underspend % | max deviation from the target % | p messages per hour |",
              "|---|---|---|---|---|---|---|---|---|"]
    names = {"probabilistic": f"probabilistic (ε = {plan.eps:g})" if plan.eps else "probabilistic",
             "token_cap_a": f"token cap (a), failure reports after {plan.failure_delay:g} s",
             "token_cap_b": f"token cap (b), grant share {plan.grant_share:g}"}
    for model, (res, msgs) in results.items():
        s = res.summary(scn.budget)
        over = res.final_spend - scn.budget
        p = f"{s['overspend_prob']:.2e}" if s["overspend_prob"] else "0"
        lines.append(f"| {names[model]} | {p} | {s['overspend_prob_upper95']:.1e} | {max(0.0, float(over.max())):.0f} | "
                     f"{s['mean_underspend_pct'] * scn.budget / 100:.0f} | {s['mean_underspend_pct']:.3f} | "
                     f"{s['p95_underspend_pct']:.3f} | {s['mean_max_pace_dev_pct']:.2f} | "
                     f"{'–' if msgs is None else format(msgs, '.1f')} |")
    target = ("the traffic-following curve (the share of the campaign's traffic seen so far)"
              if _target(plan) is not None else "even spending in time (a straight line from 0 to B)")
    if chart_file:
        lines += ["", f"![Scenario charts: traffic, one campaign per model, final spend, overspend]({chart_file})",
                  "", "*The charts:* top left, the bid-request stream (the average pattern, and one random sample if "
                  "the traffic fluctuates). Top right, one simulated campaign per model: how far its spend so far is "
                  "ahead of (+) or behind (−) the target, in % of B; the two token caps use the same pacing and the "
                  "same random traffic, so their lines coincide until a cap binds near the end. Bottom left, the "
                  "final spend of every simulated "
                  "campaign minus B: right of 0 is overspend, left of 0 underspend. Bottom right, the share of "
                  "campaigns that overspent, against ε."]
    lines += ["", "**What the columns mean** (each value is over all simulated campaigns of that model):", "",
              "| Column | Meaning |", "|---|---|",
              "| model | the model simulated, with its main setting |",
              "| P(overspend) | share of campaigns whose total spend exceeded the budget B. "
              "For the probabilistic model it should be at most ε; for the token caps it should be 0 |",
              "| 95% upper bound | the highest overspend probability still consistent with the runs "
              "(Clopper–Pearson, 95 %). With few runs, a P(overspend) of 0 does not mean the true "
              "probability is 0: this bound says how large it could be |",
              "| worst overspend, units | the largest amount by which any campaign exceeded B, in budget units "
              "(0 if none did) |",
              "| mean underspend, units | budget left unspent at the end, averaged over the campaigns |",
              "| mean underspend % | the same, as a percentage of B |",
              "| p95 underspend % | 95 % of the campaigns left at most this percentage of B unspent |",
              f"| max deviation from the target % | how far spending strayed from the target, as a percentage "
              f"of B: the largest gap during the campaign, averaged over the campaigns. The target here is "
              f"{target} |",
              "| p messages per hour | how often the center sent a new p to the clients (probabilistic "
              "model only; – for the caps, whose pacing is not counted) |", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--runs", type=int, help="override run.runs")
    ap.add_argument("--out", default="results/scenarios")
    ap.add_argument("--charts", action="store_true", help="also draw <out>/<name>.png (or set run.charts = true)")
    args = ap.parse_args()
    try:
        plan = load(args.file)
    except ScenarioError as e:
        raise SystemExit(f"scenario file error: {e}")
    if args.runs:
        plan.runs = args.runs
    results = {m: run_model(plan, m) for m in plan.models}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chart_file = None
    if args.charts or plan.charts:
        chart_file = f"{plan.name}.png"
        chart(plan, results, out / chart_file)
    text = report(plan, results, chart_file)
    (out / f"{plan.name}.md").write_text(text)
    print(text)
    print(f"written to {out / (plan.name + '.md')}" + (f" and {out / chart_file}" if chart_file else ""))


if __name__ == "__main__":
    main()
