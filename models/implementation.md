# Implementation details

Companion to [models/probabilistic.md](probabilistic.md), which states the
controller and its overspend guarantee. The shared system and formal model are in
[models/system.md](system.md). This file explains how the controller
computes its two inputs and how to implement the math in Elixir. The Elixir
controller itself is [elixir/pacing_controller.ex](../elixir/pacing_controller.ex); see
[elixir/README.md](../elixir/README.md) for its API.

1. [How k is estimated](#1-how-k-is-estimated)
2. [How the pace is calculated](#2-how-the-pace-is-calculated), including [the end-game](#the-end-game)
3. [Implementation without a numerics library (Elixir)](#3-implementation-without-a-numerics-library-elixir)

**Units.** A realised token costs a fixed c budget units
([system.md](system.md) §2). The controller works in **spends** (realised
tokens): it divides the visible spend and the budget by c on input, and nothing
else changes. In this file B, S_obs and "units" mean spends; with c = 1, the
default, they are also budget units. Code: `_Pacer._events` in `controllers.py`.

## 1. How k is estimated

k is the number of spends per second the system would produce at p = 1; the
spend rate in budget units is c·k·p. The center never
measures it directly. It has only two numbers, and both are enough
(code: `_Pacer._posterior` in `controllers.py`):

- **S_obs**, the spend visible in the budget (counted in spends, i.e. divided by
  c), which covers everything up to t − d;
- **E = ∫ p ds over the same period, up to t − d**. The center knows E exactly,
  because it set p itself. E is the number of "seconds at full throttle" that
  produced S_obs.

**Basic idea: k̂ ≈ S_obs / E.** For example, the center sent p = 0.014 for the
first 10 minutes, and spend up to t − d = 570 s is visible. Then
E = 0.014 × 570 ≈ 8.0 s at full throttle. If S_obs = 798 units, then
k̂ ≈ 798 / 8.0 ≈ 100 units/s.

**Why divide by E and not by elapsed time.** p changes over time, so each second
counts with the weight p it had. In period j the spend is Poisson(k·p_j·Δ)
([system.md](system.md) §2, *Reduction*). Multiplying these probabilities over all periods gives a
likelihood

    L(k) ∝ k^S_obs · e^(−k·E),

so S_obs and E together contain everything the data say about k. The order in
which spend arrived, and how p changed along the way, add nothing.

**Why only the period up to t − d.** Spend from the last d seconds is not visible
yet. If its exposure were added to E without its spend, k̂ would be biased low,
and low is the dangerous direction for overspend. That exposure, W, is used
separately: as the in-flight window in the risk check.

**With a spend delay that varies.** If each spend shows up after its own random
delay with distribution F, the sharp split at t − d becomes a weighting: each
past p counts in E with weight F(age) and in W with weight 1 − F(age). Splitting
by delay is one more Poisson thinning, so the Gamma posterior and the risk check
stay exact with these E and W. Details: [system.md](system.md) §7.

**With a delivery delay of p.** If a new p reaches the clients only after up to
δ_max seconds, the window is widened: a decided p counts as in flight for
ceil(d/Δ) + ceil(δ_max/Δ) periods, and only then moves into E. Both changes err
on the safe side: W covers all exposure whose spend is not visible yet, and E
never covers more than the visible spend came from, so k̂ can only come out a
little high. Details: [models/system.md](system.md) §6, *Push or pull*.

**Uncertainty: a whole distribution, not one number.** With the likelihood above,
the posterior of k is a Gamma distribution:

    k ~ Gamma(α = 1 + S_obs, β = E)       mean α/β,   relative sd ≈ 1/√S_obs

It sharpens as the center sees more spend:

| spend observed | relative sd of k |
|---|---|
| 100 units | ±10 % |
| 1000 units | ±3 % |
| 5000 units (the whole budget) | ±1.4 % |

**The start: before any spend is visible.** During the first d seconds there is no
data. The center uses its rough guess k_guess (`prior_mean`), entered as a Gamma
prior with shape a₀ (`prior_shape`, 2 by default). In effect the guess counts as
a₀ units of pseudo-spend. This is the only time the guess matters. A wrong guess
makes spending uneven during these first d seconds, and nothing worse ([probabilistic.md](probabilistic.md) §3, item 5).

**After the first spend is visible, the guess is dropped (`flat_after_data`).**
The usual Bayesian update would keep the guess forever as α = a₀ + S_obs,
β = a₀/k_guess + E. If the guess is too low, that pulls k̂ down for the whole
campaign. The tests caught exactly this: overspend 2.6 % against ε = 2 % with a
5× too-low guess. The flat-prior posterior Gamma(1 + S_obs, E) depends on the
data only. Its upper tail equals the exact (Garwood) upper confidence bound for a
Poisson rate, so it does not understate how large k could be. (The Garwood bound
after n events in exposure E is u = Gamma⁻¹(1 − α; shape n + 1) / E: the rate at
which seeing n or fewer events has probability α. See
[probabilistic.md](probabilistic.md) §1, step 1.)

**Two ways the estimate is used:**
- **Pacing** uses the mean k̂ = α/β. Its job is to aim the spend at B, and
  aiming deliberately low would only add underspend.
- **The risk check** uses the *whole* Gamma distribution. Mixing the Poisson
  in-flight spend over it gives the negative-binomial tail ([probabilistic.md](probabilistic.md) §1, step 3). So
  uncertainty about k automatically widens the reserve while data are scarce and
  narrows it as they accumulate. No separate safety margin for k is needed.

**If k were not stable** (out of scope by assumption), the same statistics would
work with forgetting: after each observed period, S ← γ·S + x and E ← γ·E + p·Δ,
with γ < 1. Old data then fade, and the posterior stays wide enough to follow drift.

## 2. How the pace is calculated

Pacing answers "how fast *should* we spend?". The risk check ([probabilistic.md](probabilistic.md) §1, step 3) then
answers "how fast *may* we spend?", and the controller uses the smaller of the
two. Code: `_Pacer._pace` in `controllers.py`.

**The formula, one piece at a time:**

    p_pace = (B − S_obs − k̂·W) / (k̂ · plan_left),   plan_left = max(T − endgame − t, Δ)

| Piece | Meaning |
|---|---|
| B − S_obs | budget not yet *visibly* spent |
| − k̂·W | minus the spend expected to be in flight (W = exposure of the last d seconds). That money is already committed and will appear in the budget. Without this term the center would plan to spend it twice. |
| = to_spend | what is still free to plan |
| plan_left | seconds left in which to spend it: until T, or until T − endgame if an end-game is used ([below](#the-end-game)) |
| to_spend / plan_left | the **target spend rate** in units/s, i.e. spread what is left evenly over the time that is left |
| ÷ k̂ | converts a spend rate into p, because spend rate = k·p |
| clip to [0, 1] | p is a probability |

**Example** (default numbers, halfway through): t = 1800 s and the budget shows
S_obs = 2450 units up to t − d = 1770 s. The current p ≈ 0.0139 has been used for
the last 30 s, so W ≈ 0.0139 × 30 = 0.417 and k̂·W ≈ 42 units are in flight. Then
to_spend = 5000 − 2450 − 42 = 2508 units, the target rate is 2508 / 1800 = 1.393
units/s, and p_pace = 1.393 / 100 = 0.01393. On an exactly even path the center
would have seen 1770 × 5000/3600 ≈ 2458 units by now. It has seen 8 fewer, so p
comes out slightly above the even-pace value of 0.01389.

**Why spend evenly?**
- **The problem asks for it.** Point 5: "should not spend all at once".
- **Nothing is lost by it.** Traffic is steady, and every unit of spend is
  worth the same whenever it happens, so no moment is better than another.
  Spending evenly costs nothing in the objective.
- **It needs the smallest p.** To spend B in T, constant p has the lowest peak p,
  and so the lowest peak spend in flight. Any uneven plan must run faster than
  B/T at some point.
- **The one deliberate exception is the end.** The final underspend scales with
  the spend in flight at the very end ([probabilistic.md](probabilistic.md) §3, item 2). So slowing down in the last
  minutes helps, which is what the optional [end-game](#the-end-game) does. It trades
  a little evenness for less unspent budget.
- **It allows time to learn k.** Spending gradually gives the center data on k
  before much budget is committed.

**Why recompute p at every step instead of fixing it once?** The formula re-plans
from the current state every Δ. That makes errors **self-correcting**:
- **Random luck.** If spending ran 50 units ahead by mid-campaign, to_spend is
  50 units smaller, and the target rate for the remaining 1800 s drops by
  50/1800 ≈ 0.028 units/s (2 %). The excess is absorbed gradually rather than by
  a sudden stop, so deviations do not add up.
- **Estimation error.** If k̂ is 10 % too high, p comes out 10 % too low, and
  spend falls behind plan. The next steps see a larger to_spend over less time
  and raise p, while k̂ itself improves with every observation.
- **A fixed p** computed once from the initial guess would keep any initial
  error for the whole campaign. That error can be 10× when the guess is poor.

**Why the mean k̂ and not a cautious upper bound?** Pacing is for *aiming*. Using
an upper bound of k would make p systematically too small and the campaign
would end with budget left unspent. Protection against overspend is not
pacing's job. The risk check ([probabilistic.md](probabilistic.md) §1, step 3) handles it with the whole distribution of
k, so pacing can aim straight.

**What happens at the edges:**
- **Budget too large to spend.** If k·T < B, even p = 1 cannot spend it all. The
  formula then asks for p > 1, which is clipped to 1, and the campaign runs at
  full throttle. This is tested in `test_impossible_budget_means_full_throttle_and_no_overspend`.
- **Ahead of plan.** If to_spend ≤ 0 (the in-flight spend is expected to use up
  everything), then p_pace = 0.
- **End of planning time.** At t ≥ T − endgame, plan_left becomes Δ, the floor
  that avoids dividing by zero. Pacing then asks to spend everything in the next
  step, and the **risk check alone** sets p. This is the creep phase: p shrinks
  as the remaining budget gets close to the in-flight spend. Without an end-game
  the same thing happens in the last few steps before T.
- **No spend where some was expected.** If spend stays invisible for longer than
  the controller expects, for example because of a delivery delay of p that
  nobody planned for, k̂ falls and pacing pushes p up, possibly to 1. The
  optional rise limit (`max_rise`) caps that: p may grow by at most ×1.5 per
  spend delay d, relative to the larger of the last p and the first. It costs
  nothing measurable when nothing is wrong, and it cuts the worst overspend of a
  2-minute unplanned delay from 2 × B to 1.2 % of B (11 % with ×2)
  ([system.md](system.md) §6).
- **A stalled budget feed.** The same misreading happens if the budget feed
  stops updating: the center sees no new spend while time passes. The feed must
  report how fresh it is; if it is older than d plus a tolerance, the center
  halts (p = 0) and counts p = 0 as the p in effect, so k̂ is not biased
  ([system.md](system.md) §6).
- **A lost link.** If the connection to the clients drops right after a
  decision, that p runs until the clients' silence limit L. The risk check then
  charges each p for max(Δ, L) instead of Δ (`max_silence`), which keeps the
  guarantee at a cost of about 0.13 points of underspend with L = 15 s.

### The end-game

The end-game is an optional parameter, `endgame` seconds (`endgame_time` in
`_Pacer`; 0 means off). Pacing then aims to have spent the budget `endgame`
seconds **before** T instead of exactly at T. The rest of the time is left to the
risk check, which approaches B in small steps.

**The two phases:**

| Phase | Time | What sets p |
|---|---|---|
| Pacing | t < T − endgame | p_pace, with plan_left = T − endgame − t: the controller runs slightly faster than the even pace B/T |
| Creep | t ≥ T − endgame | plan_left = Δ, so pacing asks for "everything now" and the **risk check alone** sets p. p shrinks step by step as the remaining budget approaches the spend still in flight. |

**Why it reduces underspend.** The budget shows spend only after the delay d, so
the center must always keep a margin for spend in flight that it cannot see yet.
At the deadline that margin is what stays unspent. Its size is set by how fast
spending is running at the end:

    margin ≈ z(1 − ε) · √(rate · (d + Δ))      spends, rate in spends/s

In budget units that is c times as much, √(c · money rate · (d + Δ)) under the
root: the larger each spend, the larger the margin relative to B
([probabilistic.md](probabilistic.md) §3, item 10).

With the default numbers and no end-game, the rate at the end is still the even
pace, B/T ≈ 1.39 units/s. The spend in flight over d + Δ = 35 s is about 49 units,
and the margin is about 3.1 × √49 ≈ 21 units, or 0.4 % of B. That is the
underspend measured in the results.

With an end-game, most of the budget is already spent when the creep phase
starts. The remaining budget is small, so the risk check keeps p, and therefore
the rate, low. A low rate means little spend in flight, so a small margin, and
the controller can get much closer to B by T.

**Why it does not increase overspend.** In both phases p is the smaller of the
pace and the risk check, and the risk check still enforces the total ε
([probabilistic.md](probabilistic.md) §2). The end-game only changes
where pacing *aims*.

**What it costs.** Spending runs ahead of the even-pace line before the creep
phase, so spending is less even. From
[results/probabilistic.md](../results/probabilistic.md) §5 (ε = 10⁻³, d = 30 s):

| endgame | mean underspend | max deviation from even pace |
|---|---|---|
| 0 s | 0.40 % | 1.2 % |
| 30 s | 0.28 % | 1.5 % |
| 60 s | 0.24 % | 2.1 % |
| 300 s | 0.19 % | 8.4 % |
| 600 s | 0.17 % | 16.6 % |

**Choosing it.** An end-game of about d to 2d (30–60 s here) gets most of the
gain for little loss of evenness. Longer ones buy little more underspend and
make spending visibly uneven. The choice is a policy decision between even
spending and full use of the budget (problem point 5).

**In code** it is one number, used in the single line that computes plan_left:

    plan_left = max(T - endgame - t, step)

## 3. Implementation without a numerics library (Elixir)

The production center will be written in Elixir. Erlang's `:math` module has
`exp`, `log`, `sin`, `sqrt` and `pow`, but not ln Γ, the incomplete beta or gamma
functions, or even `log1p`. The controller needs exactly one non-elementary
quantity, the tail of the in-flight spend:

    P(X > m) = I_x(m + 1, α),    x = V / (β + V)      (negative binomial, models/probabilistic.md §1)

where I is the regularized incomplete beta function. The oracle and plug-in
baselines also use the Poisson tail P(m + 1, μ), the regularized incomplete gamma
function. Both are computed from elementary math with standard algorithms:

| Function | Method | Accuracy |
|---|---|---|
| ln(1 + x) | Goldberg's trick: `u = 1 + x; u == 1 ? x : log(u)·x/(u − 1)` | a few ulps |
| ln Γ(x) | Lanczos approximation, g = 7, 9 coefficients; reflection for x < 0.5 | ~1e-15 relative |
| I_x(a, b) | prefactor exp(a·ln x + b·ln(1 − x) + lnΓ(a+b) − lnΓ(a) − lnΓ(b)) times a continued fraction (modified Lentz); use 1 − I_{1−x}(b, a) when x ≥ (a+1)/(a+b+2) | ~6e-11 relative |
| P(a, x) | power series for x < a + 1, continued fraction for Q = 1 − P otherwise | ~1e-12 relative |

Notes:

- **Small tails are computed directly, not as 1 − (something close to 1).**
  The tails that matter are around ε (10⁻³ to 10⁻⁹). They fall in the direct
  branch of both algorithms, so they keep full relative precision.
- **Few iterations are needed.** In the controller's working range the
  continued fraction converges in 2–20 iterations (tolerance 10⁻¹⁵). The generic
  worst case grows like √max(a, b), about 100–300 for budgets of 10⁴–10⁵ units.
  The 10,000-iteration cap is only a safeguard.
- **Precision.** With a, b ≈ 10⁴ the three ln Γ terms are about 10⁵ and mostly
  cancel. Double precision still leaves about 10⁻¹¹ relative error in the
  prefactor, far below what the risk check needs.

**Checked at three levels:**

1. `src/pacing/specfun.py`, the reference implementation, matches scipy within
   10⁻⁹ relative on 3,000 random cases each for I_x and P(a, x). The ranges cover
   a up to 2·10⁴, b from 0.5 to 2·10⁴, and x from 10⁻⁷ to 0.999, including tails
   down to 10⁻¹² (`tests/test_specfun.py`).
2. **End to end:** the full default simulation (40,000 runs) gives *identical*
   results with the portable math and with scipy: the same overspend count and
   the same underspend. Select it with `BayesRiskController(..., tails="portable")`.
3. `elixir/pacing_math.ex` is a line-by-line port. `elixir/pacing_math_test.exs`
   checks it on 126 cases whose expected values come from scipy (generated by
   `scripts/gen_elixir_golden.py`).

### Pure Elixir or a NIF?

`elixir/c_src/pacing_math_nif.c` is the same algorithm in C, loaded as a NIF by
`PacingMathNif`, with the same API as `PacingMath`. It uses its own Lanczos ln Γ
rather than libm's `lgamma()`, because `lgamma()` writes the global `signgam` and
so is not safe on concurrent scheduler threads. Build it with `make -C elixir`. The
same 126 scipy cases test both modules, and on 5,000 random controller-like
arguments the two agree to 10⁻¹¹ relative. Small differences are expected: C
uses libm's `log1p`, and the prefactor magnifies the last digit of terms of about
10⁵.

Speed (Apple M-series, OTP 28; `make -C elixir bench`):

| | pure Elixir | NIF |
|---|---|---|
| one tail evaluation | 0.8–9 µs | 0.06–0.65 µs (≈ 14× faster) |
| one controller decision (~31 tail evaluations incl. bisection) | 67 µs | 5.7 µs |

**Recommendation: use pure Elixir.** One campaign makes one decision every Δ = 5 s,
so 67 µs is about 0.001 % of one core. The NIF becomes worthwhile only with many
campaigns, roughly 10,000 or more deciding every second, where pure Elixir would
take most of a core. Even then, consider the cost:

- a bug in C can crash the whole BEAM node instead of one process;
- the library has to be built for every target architecture;
- C code is harder to review.

Because the two modules share an API, switching later is a one-line change.
