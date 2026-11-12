# Budget pacing with delayed feedback: model and strategy

Initial problem statement and clarifications: `INITIALMODEL.MD`.
Code: `src/pacing/` · tests: `tests/` · numbers and charts: `results/`.

## 1. Model

| Symbol | Meaning | Known to center? |
|---|---|---|
| B | budget, in units (one realised token costs exactly 1 unit) | yes |
| T | horizon (time to spend the budget) | yes |
| d | fixed delay between a spend and its appearance in the budget | yes |
| Δ | control period (how often the center may broadcast a new p) | yes |
| ε | allowed probability of overspend | yes (chosen) |
| λ | rate of bid requests (steady Poisson) | **no** |
| r | probability that a token turns into budget spend (stable) | **no** |
| N | number of clients | **no** |
| p | token probability broadcast to all clients; takes effect immediately | it sets it |

A request goes to a uniformly random client. That client creates a token with
probability p, and the token becomes 1 unit of spend with probability r.

**Interpretation of answer B.** Point 8 says "not every token realises in budget
spending". I read "1 token = 1 budget spend" as: a token that *is* realised costs
exactly 1 unit, and a fraction r of tokens is realised. If every token is realised,
set r = 1. Nothing else changes.

**Reduction.** Thinning a Poisson process keeps it Poisson. So the spend in an
interval of length Δ with constant p is

    X ~ Poisson(k · p · Δ),     k = λ · r   (spend per second at p = 1)

whatever N is and however requests are split between clients. The center does not
need N, λ or r separately, only the single number k, and it can learn k from its
own budget. `test_number_of_clients_does_not_matter` checks this against an
event-level simulation with 1, 7 and 500 clients.

**What the center sees.** At time t it sees spend S_obs from the period before
t − d, and it knows its exposure E = ∫ p ds over that period. The exposure over
the last d seconds, W = ∫ p ds, is still *in flight*: that spend has happened but
is not visible yet.

**Overspend.** Overspend means the final spend (visible + in flight) > B. With an
immediate stop, the center can always freeze the committed spend at its current
value. All of the risk comes from spend that is in flight and has not been
observed yet.

## 2. Strategy (`BayesRiskController`)

At every control step:

1. **Estimate k.** Use the posterior k ~ Gamma(α, β). Before any spend is visible,
   α and β come from the center's rough guess (`prior_mean`). After that,
   α = 1 + S_obs and β = E (flat prior).
2. **Pace.** Spend the expected remaining budget evenly over the remaining planning time:

       p_pace = (B − S_obs − k̂·W) / (k̂ · (T − endgame − t)),    clipped to [0, 1]

3. **Check the risk and cap p.** Given k, the committed spend beyond S_obs is
   Poisson(k·(W + pΔ)). Mixed over the Gamma posterior, it is negative-binomial,
   so its tail has a closed form (an incomplete beta function). Compute
   - q₀ = P(in-flight spend > B − S_obs): the risk already locked in, and
   - q₁(p) = the same probability with the next step's spend pΔ added.

   Then use the largest p ≤ p_pace whose added risk satisfies
   q₁(p) − q₀ ≤ η·(ε − risk_used), and add q₁ − q₀ to risk_used.

### Why this bounds total overspend by ε

Let Cᵢ be the committed spend after step i, and Fᵢ what the center knows when it
chooses pᵢ. Spend only goes up, so overspend happens at exactly one first step,
and

    P(overspend) = Σᵢ P(Cᵢ > B, Cᵢ₋₁ ≤ B) = Σᵢ E[ P(Cᵢ > B | Fᵢ) − P(Cᵢ₋₁ > B | Fᵢ) ].

Each bracket is exactly the q₁ − q₀ that the controller charges. The charges add up
to at most ε on every path, so P(overspend) ≤ ε. This is exact when k really is
drawn from the prior (`test_bayes_guarantee_holds_when_rate_is_drawn_from_prior`).

For a fixed but unknown k (the real case), the flat-prior posterior has an upper
tail equal to the exact Garwood confidence bound. In simulation it stays at or
below ε even when the initial guess is wrong by 10× in either direction. This part
is verified empirically, not proved.

**Why checking ε at each step is not enough.** If you check
"P(in-flight > remaining) ≤ ε" at every step, as a fixed-reserve rule does, the
risk adds up over the end-game steps. In the comparison below, that rule
overspends 2.6× more often than ε.

## 3. Results (default: B = 5000, T = 1 h, k = 100/s, d = 30 s, Δ = 5 s, ε = 10⁻³)

Full tables: `results/summary.md`. Charts: `results/*.png`.

| strategy | P(overspend) | mean underspend | max deviation from even pace |
|---|---|---|---|
| Naive (stops when the visible budget is empty) | **1.0** (≈ 43 units over) | 0 % | 1.5 % |
| Fixed reserve, ε checked per step | 2.6·10⁻³ (> ε) | 0.35 % | 1.2 % |
| **Proposed** | **1.0·10⁻³** (400k runs: 9.97·10⁻⁴) | **0.40 %** | 1.2 % |
| Proposed + 300 s end-game | 0.8·10⁻³ | 0.19 % | 8.4 % |
| Oracle that knows k exactly | 1.05·10⁻³ | 0.40 % | 1.2 % |

What the results show:

1. **Not knowing k costs almost nothing.** The proposed controller matches the
   oracle. Learning k is not the hard part; the delay is.
2. **The underspend is set by delay noise.** With even pacing, the shortfall is
   about z₁₋ε·√(rate·(d+Δ)), where rate = B/T (`results/delay.png`). It grows with
   the square root of the delay and the spend rate, so as a share of the budget it
   *falls* as B grows (~1/√B).
3. **A smaller ε is cheap.** Going from ε = 10⁻³ to 10⁻⁶ raises the underspend
   from 0.40 % to 0.57 % (`results/eps.png`).
4. **Evenness versus budget use is a policy choice (point 5).** Finishing the
   planned spend a little early and then creeping up to B with a small p halves
   the underspend. A 30–60 s end-game already gets most of the gain for a small
   loss of evenness (`results/endgame.png`).
5. **The initial guess of k matters only during the first d seconds**, when the
   center has no data yet. A 10× too-low guess makes spending uneven at the start
   (8.8 % deviation) but does not raise the overspend risk.
6. η (how fast the risk budget is used) hardly matters between 0.25 and 0.5.

## 4. Limits and next steps

- **Optimality.** The controller is greedy for each step. It is not the solution
  of the full dynamic programme. It does match the known-k oracle, and the
  remaining gap is the delay noise that any steadily-pacing strategy has.
  Solving the dynamic programme for the end-game would tell how much more is
  possible.
- Natural extensions, in order: traffic that changes over time (forecast λ(t) and
  pace along it), k that drifts (discount old observations), a random delay (use
  its upper quantile or model its distribution), and a delay before clients
  receive a new p (add it to the in-flight window).

## 5. Running

```bash
uv run pytest                          # 38 tests, ~5 s
uv run python -m pacing.experiments    # full Monte Carlo, ~6 min (--quick: ~25 s)
```
