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
(`uv run python -m pacing.seasonal_experiments`, about 8 min) · tests:
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

**The alternative: spend evenly in time** (option `pace_goal="time"`). The spend
rate is kept constant, so p must work against the traffic:

    p(t) = (budget left / time left) / (k₀ · s(t))

p is then high in quiet hours and low in busy ones, here swinging 5.6 times a
day. Compared with following the traffic:

| | Follow the traffic | Even in time |
|---|---|---|
| p over a day | almost constant | inversely proportional to the traffic |
| needs the current traffic for | the risk check only | pacing itself, every step |
| p messages per hour (2 % threshold) | about 0 | about 5 |
| feasible | always | only if even the quietest hour can keep up at p = 1: B/T ≤ k₀ · s_min |

When the quietest hours cannot keep up, the controller runs them at p = 1 and
catches up in the other hours. That is worse than following the traffic: in the
experiments it ends further from the even line and leaves budget unspent
([probabilistic.md](probabilistic.md) §3). Spending "as evenly as possible"
would need planning ahead with the profile (which hours run at p = 1, and the
rest spread over the busy hours); that is not implemented.

## 3. The math: time is replaced by traffic

The seasonal models keep almost all of the base math
([../system.md](../system.md) §2, [../probabilistic.md](../probabilistic.md)).
The one real change is that time is replaced by **traffic**. Below, step by
step, is what follows from that.

### 3.1 The spend process

**Constant traffic.** Requests form a Poisson process with rate λ. Thinning by p
(the client bids), r (the bid is charged) and the random choice of client gives
spends at rate k·p, with k = λ·r:

    spends in [t₁, t₂] ~ Poisson( k · ∫ p du )

**Seasonal traffic.** The rate is λ(t) = λ₀·s(t): an *inhomogeneous* Poisson
process. Thinning still keeps it Poisson, with the intensity multiplied by the
thinning probabilities:

    spends in [t₁, t₂] ~ Poisson( k₀ · ∫ p(u) · s(u) du ),     k₀ = λ₀ · r

The second line is the first with **du replaced by s(u) du**: a change of time
scale. Measure time in "seconds of average traffic",

    τ(t) = ∫₀ᵗ s(u) du

and the seasonal process becomes an ordinary constant-rate process in τ, with
rate k₀·p. Every formula of the base model holds in τ. The constant-traffic
model is the special case s ≡ 1, and the code reproduces it exactly
(`test_constant_profile_changes_nothing`).

### 3.2 Estimating the level k₀

The likelihood of the visible spend S_obs is

    L(k₀) ∝ k₀^S_obs · e^(−k₀·E),     E = ∫ p(u)·s(u) du   over the visible period

the same form as before, with the exposure weighted by s. So the flat-prior
posterior is unchanged:

    k₀ ~ Gamma(1 + S_obs, E)

It is exact, its upper tail is still the Garwood bound, and the ε guarantee
carries over unchanged. The only requirement is that s is known.

### 3.3 Pacing

**Following the traffic** means a constant p. The spend still expected at a
constant p is k₀·p·∫ₜᵀ s du, so

    p = (B − S_obs − k̂₀·W) / ( k̂₀ · ∫ₜᵀ s(u) du ),     W = ∫ p·s du over the last d

This is the base formula with "time left" (T − t) replaced by "traffic left"
τ(T) − τ(t).

**Spending evenly in time** means a constant spend rate R = budget left / (T − t).
The spend rate is k₀·s(t)·p, so

    p(t) = R / ( k̂₀ · s(t) )

This is feasible only while p ≤ 1 at every moment:

    B/T ≤ k₀ · min s(t)

### 3.4 The risk check

The spend not yet visible, plus the next step, is

    Poisson( k₀ · V ),     V = W + p · Δ · s(t)

Averaged over the Gamma distribution of k₀ it is again negative-binomial, so the
risk check keeps its closed form:

    P(spend in flight > B − S_obs) = I_x(m + 1, α),     x = V / (β + V)

with the same incomplete beta function, the same risk budget, and the same proof
that the charges q₁ − q₀ add up to at most ε. Only V carries the factor s.

### 3.5 Without a profile: what is no longer exact

**Assuming s ≡ 1 when it is not.** The posterior Gamma(1 + S_obs, ∫ p du) then
estimates

    k̄ = k₀ · (average of s over the visible period)

But the spend in flight depends on k₀·s(t) at the *end* of the campaign. If s(T)
is above the average, the reserve is too small: that is the 15 % overspend
measured in [probabilistic.md](probabilistic.md) §2.

**The short-memory estimate.** Old data are discounted with a factor
γ = 0.5^(Δ / half-life) per step:

    S_rec ← γ·S_rec + new spend,     E_rec ← γ·E_rec + new exposure,     k ~ Gamma(1 + S_rec, E_rec)

This is no longer an exact posterior. It tracks k₀·s(t) with a lag of about one
half-life, and it is wider than the full posterior, because it uses fewer
effective observations, which errs on the safe side. **There is no proof here:**
the guarantee is measured, not proved (0.18 % against ε = 1 % with a 1-hour
memory).

**A wrong profile ŝ.** The exposure becomes E = ∫ p·ŝ du. The posterior then
estimates the level of the wrong shape, and both pacing and the risk check use
ŝ(t) where s(t) holds. Nothing corrects the error, which is why it is the worst
case (24 % overspend following the traffic, 40 % spending evenly).

### 3.6 The token caps: no new math

Their rules count tokens and budget:

    (a)  tokens handed out ≤ B/c + failures reported + tokens returned
    (b)  A = B − S_obs − (tokens granted in the last d seconds)

No rate appears in them, so s(t) does not appear either, and their proofs are
unchanged. Only the pacing underneath changes, as in §3.3.

### 3.7 Summary

| | Constant traffic | Seasonal, profile known | Seasonal, profile unknown |
|---|---|---|---|
| time scale | t | τ = ∫ s du | t (wrong) |
| exposure | ∫ p du | ∫ p·s du | ∫ p du |
| posterior | Gamma(1 + S, E), exact | the same, exact | the same form, but it estimates the average |
| pacing denominator (follow the traffic) | k̂·(T − t) | k̂₀·∫ₜᵀ s du | k̂·(T − t) |
| risk-check exposure | W + p·Δ | W + p·Δ·s(t) | W + p·Δ, with a recent-data k (approximate) |
| ε guarantee | proof + simulation | the same | simulation only |

## 4. What each model has to change

The overview first, then each model in detail: what changes, from which formula
to which, and why. "Profile" means the shape s(t); Δ is the control period.

| | Probabilistic controller | Token cap (a) | Token cap (b) |
|---|---|---|---|
| Safety | depends on knowing the traffic **now**: with a wrong or no shape, the risk check misjudges the spend in flight | **unaffected**: counts tokens | **unaffected**: counts tokens and the budget |
| What to change | exposure, pacing and the risk check (below); without a profile, a short-memory estimate of the current rate | only the pacing underneath | only the pacing underneath |
| Clients | nothing | nothing | nothing |
| Cost | as with constant traffic | falls like 1/T: negligible over a month | falls like 1/T: negligible over a month |

### 4.1 Probabilistic controller

Four parts of the controller (see [../probabilistic.md](../probabilistic.md) §1)
change. What changes depends on whether the profile is known.

**1. Exposure: one second counts as s(t) seconds of average traffic.**

| | Constant traffic | Profile known | Profile unknown |
|---|---|---|---|
| visible exposure E | ∫ p du | ∫ p · s du | ∫ p du (unchanged) |
| in-flight exposure W | ∫ p du over the last d | ∫ p · s du over the last d | ∫ p du (unchanged) |
| what k̂ = S_obs / E estimates | k | the level k₀ | the average k over the campaign so far |

Why: spend from a period is Poisson with mean k₀ · ∫ p · s du. Weighting the
exposure by s turns the time-varying problem back into the constant one, so the
Gamma posterior Gamma(1 + S_obs, E) stays exact (§3).

**2. Pacing: spread the budget over what is left.** The formula keeps its form,
p = (budget left − spend in flight) / (k̂ × exposure still to come); only the
"exposure still to come" changes:

| Goal | Profile known | Profile unknown |
|---|---|---|
| follow the traffic | ∫ₜᵀ s(u) du: the remaining *traffic*, so p comes out constant | (T − t): the remaining time; with k̂ the long-run average, p comes out almost constant too |
| even in time | target rate = budget left / time left; p = target rate / (k̂₀ · s(t)) | the same, with the current rate estimated from the last few minutes instead of k̂₀ · s(t) |

Why: following the traffic means a constant p, which only works if pacing knows
how much traffic is still to come. Spending evenly means p against the traffic,
which needs the traffic *now*.

**3. Risk check: charge the next step for its traffic.** The risk added by the
next step is computed with exposure p · Δ · s(t) instead of p · Δ (and p · L · s(t)
when planning for the silence limit L). Without a profile, the spend in flight
is still judged with k for the risk check, but **that k must be the current one**,
not the long-run average:

| | Profile known | Profile unknown |
|---|---|---|
| k for the risk check | the level k₀ (exact, with weighted exposure) | a **second estimate from recent data**: spend and exposure discounted with a half-life (`risk_half_life`, about 1 h; 10 min when pacing evenly) |

Why: the risk check guards the spend in flight *at the end of the campaign*. If
the campaign ends at a busy hour, the long-run average is too low and the
reserve too small: 15 % overspend against ε = 1 % in the experiments, 0.18 % with
the last hour of data.

**4. Nothing else.** The risk budget ε, the share η, the end-game, the
delivery-delay and outage rules, and the proof all stay as they are.

**What the center needs:** the profile from history (preferred), or nothing
and the short-memory estimate. A **wrong** profile must be avoided: it breaks the
guarantee more than having none (24 % overspend following the traffic, 40 %
spending evenly).

Code: `SeasonalController` in `src/pacing/seasonal.py` (options `profile`,
`risk_half_life`, `pace_goal`); the simulator weights the exposure with
`center_profile`.

### 4.2 Token cap (a)

**The cap itself: nothing changes.** The rule is still

    tokens handed out ≤ floor(B/c) + failures reported + tokens returned

and neither the rule nor its proof mentions the traffic. A busy hour means more
top-ups; it cannot mean more spend than tokens.

**The pacing underneath** changes exactly as in parts 1 and 2 above, for either
goal. It needs **no risk check**: the cap prevents overspend, so the controller
under the cap is plain pacing (`SeasonalPacer`). This is why a missing or
uncertain profile is much less of a problem for (a) than for the probabilistic
controller.

**Wallets:** nothing changes. Near the end a top-up still gives at most a share
of the remaining allowance per live client (`wallet_share`), whatever the
traffic.

**Cost:** tokens whose outcome is not yet known when the budget runs out. It
falls like 1/T, so over a month it is at most 0.13 % of the budget even with
failure reports delayed by 10 minutes. One effect to know: pacing *without* the
profile leaves less unspent under the cap (it runs slightly ahead, and the cap
turns that into using the whole budget), at the cost of evenness.

### 4.3 Token cap (b)

**The cap itself: nothing changes.** Each step the grant is still at most

    A = B − S_obs − (tokens granted in the last d seconds)

The proof needs a spend delay that never exceeds d, which the traffic cycles do
not affect.

**The grants adapt by themselves.** A grant is half of the budget that is
certainly free, not a share of the traffic. In a busy hour clients use more of
it; in a quiet hour more of it expires. Early in a long campaign the free budget
is far above any hour's demand, so the grant never limits spending; only near
the end does it bind.

**The pacing underneath** changes as for (a): parts 1 and 2 above, no risk
check.

**Conditions:** unchanged, and none added by the traffic: a hard maximum for d,
grants that really expire, and a grant margin that covers the delivery delay of
grants ([../token-cap-b.md](../token-cap-b.md) §3, §6).

**Cost:** budget held by grants whose spend is not yet visible; at most 0.034 %
over a month.

### 4.4 What does not change in any model

- Clients: they flip the p-coin and hold tokens exactly as before; nothing tells
  them about the traffic.
- Registration, leases, the age check, the silence limit, durable state and the
  stale-feed halt ([../system.md](../system.md) §6).
- The Elixir implementation: not yet extended; `PacingController` has no profile
  options.

Details and measurements in each model file.
