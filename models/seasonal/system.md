# Seasonal system: traffic that varies with the time of day and the day of the week

An extension of the base system ([../system.md](../system.md)) for long
campaigns, a month or more. Everything in the base system still holds except
one assumption: the request rate is no longer constant (answer D, "steady
traffic"). The base models are unchanged; this folder holds their extended
versions:

| Model | Base version | Extended for varying traffic | Results |
|---|---|---|---|
| Probabilistic controller | [../probabilistic.md](../probabilistic.md) | [probabilistic.md](probabilistic.md) | [results](../../results/seasonal/probabilistic.md) |
| Token cap (a) | [../token-cap-a.md](../token-cap-a.md) | [token-cap-a.md](token-cap-a.md) | [results](../../results/seasonal/token-cap-a.md) |
| Token cap (b) | [../token-cap-b.md](../token-cap-b.md) | [token-cap-b.md](token-cap-b.md) | [results](../../results/seasonal/token-cap-b.md) |

Code: `src/pacing/seasonal.py` · experiments: `src/pacing/seasonal_experiments.py`
(`uv run python -m pacing.seasonal_experiments`, about 4 min) · tests:
`tests/test_seasonal.py`.

## 1. The traffic

The request rate has two cycles on top of a level λ₀:

    λ(t) = λ₀ · s(t),     s(t) = daily(hour of t) × weekly(day of t),   average of s over a week = 1

| Cycle | Shape used in the experiments | Time scale |
|---|---|---|
| time of day | 1 + 0.6·cos(2π(hour − 20)/24): busiest at 20:00, quietest at 08:00 | 24 h |
| day of week | 1 on weekdays, 0.7 on Saturday and Sunday | 7 days |

So the traffic at the busiest hour of a weekday is about 5.6 times the traffic
at the quietest hour of a weekend day. The spend rate at p = 1 follows it:
k(t) = λ(t)·r = k₀·s(t). The center does not know the level k₀; it learns it
from the budget, as before. Whether it knows the *shape* s(t) is what the
extended models compare:

- **known:** s(t) from history, for example last month's traffic;
- **unknown:** the center assumes constant traffic;
- **wrong:** the center uses a shape that does not match, in the experiments a
  daily peak at 16:00 instead of 20:00 and no weekend dip.

**Time scales.** The cycles take hours and days, while the spend delay d = 30 s
and the control period are tiny in comparison. Over one in-flight window the
traffic is practically constant. What the cycles change is *estimation* (a k
estimated over hours is not the current k) and *planning* (the traffic still to
come is not the time still to come).

**Simulation.** A month at a 5 s step would be 518,000 steps per run, so the
seasonal experiments use a 60 s control period: 43,200 steps for 30 days.
With d = 30 s, spend becomes visible after one period.

## 2. What "spend evenly" means now

Point 5 of the problem says the budget must not be spent all at once. With
constant traffic, even in time and constant p were the same thing. With varying
traffic they differ, and the extended models **follow the traffic**: p stays
roughly constant, and more is spent in busy hours. The target share of the
budget spent by time t is the share of the campaign's traffic seen by then:

    target(t) = ∫₀ᵗ s(u) du / ∫₀ᵀ s(u) du

This is always feasible (it needs no p above 1) and is the usual choice in
advertising: a bid in a busy hour is worth as much as one in a quiet hour. The
results report the largest deviation from this target.

## 3. Why the math still works with a known shape

Requests still form a Poisson process, now with intensity λ₀·s(t). Thinning
keeps it Poisson, so the spend in an interval with constant p is

    X ~ Poisson(k₀ · p · ∫ s(u) du)

The only change is that exposure is weighted by the traffic: one second at
p = 1 counts as s(t) seconds of average traffic. With exposure defined as

    E = ∫ p(u) · s(u) du    over the period whose spend is visible
    W = ∫ p(u) · s(u) du    over the last d seconds (in flight)

the estimate of k₀ is the same Gamma posterior Gamma(1 + S_obs, E), the risk
check is the same negative-binomial tail, and the proofs are unchanged. The
constant-traffic model is the special case s ≡ 1, and the code reproduces it
exactly (`test_constant_profile_changes_nothing`).

## 4. What each model has to change

| | Probabilistic controller | Token cap (a) | Token cap (b) |
|---|---|---|---|
| Safety | depends on knowing the traffic **now**: with a wrong or no shape, the risk check misjudges the spend in flight | **unaffected**: counts tokens | **unaffected**: counts tokens and the budget |
| What to change | weight exposure by s(t); pace over the remaining traffic; charge the next step for p·Δ·s(t); without a shape, estimate k for the risk check from recent data | only the pacing underneath | only the pacing underneath |
| Cost | as with constant traffic | falls like 1/T: negligible over a month | falls like 1/T: negligible over a month |

Details and measurements in each model file.
