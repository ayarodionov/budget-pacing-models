# Probabilistic controller (risk budget)

The center's strategy that keeps P(overspend) ≤ ε while spending as much of the
budget as possible, using only the budget it sees. It builds on
[system.md](system.md) (configuration, formal model, symbols). How k is
estimated and how the pace is calculated is explained in
[IMPLEMENTATION.md](../IMPLEMENTATION.md) §1–2.

Code: `src/pacing/controllers.py` (`BayesRiskController`), Elixir port
[`elixir/pacing_controller.ex`](../elixir/pacing_controller.ex) · simulation:
`src/pacing/model.py` · results: [results/probabilistic.md](../results/probabilistic.md)
(`uv run python -m pacing.experiments`).

## 1. Strategy (`BayesRiskController`)

**Counting in spends.** A realised token costs c budget units
([system.md](system.md) §2), so the controller counts spends: the budget holds
B/c of them, and S_obs/c of them are visible. In the formulas below, B and S_obs
are these counts. With c = 1, the default, they are the budget units.

At every control step:

1. **Estimate k.** Use the posterior k ~ Gamma(α, β). Before any spend is visible,
   α and β come from the center's rough guess (`prior_mean`). After that,
   α = 1 + S_obs and β = E (flat prior). Details: [IMPLEMENTATION.md §1](../IMPLEMENTATION.md#1-how-k-is-estimated).
2. **Pace.** Spend the expected remaining budget evenly over the remaining planning time:

       p_pace = (B − S_obs − k̂·W) / (k̂ · (T − endgame − t)),    clipped to [0, 1]

   Details: [IMPLEMENTATION.md §2](../IMPLEMENTATION.md#2-how-the-pace-is-calculated).

3. **Check the risk and cap p.** Given k, the committed spend beyond S_obs is
   Poisson(k·(W + pΔ)). Mixed over the Gamma posterior, it is negative-binomial,
   so its tail has a closed form (an incomplete beta function). Compute
   - q₀ = P(in-flight spend > B − S_obs): the risk already locked in, and
   - q₁(p) = the same probability with the next step's spend pΔ added.

   Then use the largest p ≤ p_pace whose added risk satisfies
   q₁(p) − q₀ ≤ η_Δ·(ε − risk_used), and add q₁ − q₀ to risk_used.

   η is the share of the unused risk budget that may be used **per 5 seconds**,
   so one step may use η_Δ = 1 − (1 − η)^(Δ/5 s). An earlier version applied η
   per *step*. With a short Δ, that version used up its risk budget faster in
   real time and stopped too early, so Δ = 1 s did worse than Δ = 5 s.
4. **Send or keep** (only if a broadcast threshold is set). If the new p is within
   the threshold of the p that clients already use, *and* that old p also passes
   the risk check in step 3, keep the old p and send nothing. Otherwise send the
   new p. The risk is always charged for the p that clients actually use, so the
   guarantee below does not change.

## 2. Why this bounds total overspend by ε

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

Full tables: [results/probabilistic.md](../results/probabilistic.md).

| strategy | P(overspend) | mean underspend | max deviation from even pace |
|---|---|---|---|
| Naive (stops when the visible budget is empty) | **1.0** (≈ 43 units over) | 0 % | 1.5 % |
| Fixed reserve, ε checked per step | 2.6·10⁻³ (> ε) | 0.35 % | 1.2 % |
| **Proposed** | **1.0·10⁻³** (400k runs: 9.97·10⁻⁴) | **0.40 %** | 1.2 % |
| Proposed + 300 s end-game | 0.8·10⁻³ | 0.19 % | 8.4 % |
| Oracle that knows k exactly | 1.05·10⁻³ | 0.40 % | 1.2 % |

![Five sample runs: p, cumulative spend, budget left at the end](../results/trajectories.png)

![Observed overspend rate against the allowed ε](../results/overspend.png)

What the results show:

1. **Not knowing k costs almost nothing.** The proposed controller matches the
   oracle. Learning k is not the hard part; the delay is.
2. **The underspend is set by delay noise.** With even pacing, the shortfall is
   about z₁₋ε·√(rate·(d+Δ)), where rate = B/T. It grows with
   the square root of the delay and the spend rate, so as a share of the budget it
   *falls* as B grows (~1/√B).

   ![Underspend against the delay d](../results/delay.png)

3. **A smaller ε is cheap.** Going from ε = 10⁻³ to 10⁻⁶ raises the underspend
   from 0.40 % to 0.57 %.

   ![Underspend against ε](../results/eps.png)

4. **Evenness versus budget use is a policy choice (point 5).** Finishing the
   planned spend a little early and then creeping up to B with a small p halves
   the underspend. A 30–60 s end-game already gets most of the gain for a small
   loss of evenness. How the end-game works and why it helps:
   [IMPLEMENTATION.md](../IMPLEMENTATION.md#the-end-game).

   ![End-game time: evenness against underspend](../results/endgame.png)

5. **The initial guess of k matters only during the first d seconds**, when the
   center has no data yet. A 10× too-low guess makes spending uneven at the start
   (8.8 % deviation) but does not raise the overspend risk.
6. η (how fast the risk budget is used) hardly matters between 0.25 and 0.5.
7. **How often p is updated matters little while Δ is well below d.** With d = 30 s:

   | Δ | 1 s | 2 s | 5 s | 10 s | 30 s |
   |---|---|---|---|---|---|
   | mean underspend | 0.38 % | 0.39 % | 0.40 % | 0.42 % | 0.49 % |

   This follows the in-flight window d + Δ under the square root (item 2).
   Updating faster than about d/6 gains almost nothing.
8. **p does not need to be sent every period.** p stays almost flat for most of
   the campaign, so sending it only when it changes by more than 2–10 % cuts the
   messages to clients from 720 per hour (Δ = 5 s) to 14–53 per hour. Underspend
   and overspend stay the same. The cost is somewhat less even spending: the
   deviation from even pace goes from 1.2 % to 1.3–2.2 %. Recomputing every second
   but sending only on a > 5 % change gives the lowest underspend with about
   50 messages per hour.

   ![Messages to clients against underspend](../results/messages.png)

9. **This controller cannot make overspend impossible**, only rare. A very small
   ε is cheap: 0.67 % underspend at ε = 10⁻⁹ and 0.78 % at ε = 10⁻¹². For
   certainty, see the token caps ([token-cap-a.md](token-cap-a.md),
   [token-cap-b.md](token-cap-b.md)).
10. **Underspend grows like √c, the cost of one spend.** With B = 5000 units
    fixed, a realised token costing c units means B/c spends. The fewer and
    larger the spends, the noisier the spend relative to B, and the larger the
    reserve for in-flight spend: about z·√(c·(B/T)·(d + Δ)) units. The request
    rate is scaled as λ = 1000/c, so the needed p stays ≈ 0.014. With λ fixed, any
    c below 0.014 could not spend B even at p = 1.

    | c (units per spend) | 0.01 | 0.05 | 0.1 | 0.5 | 1 | 5 | 10 | 25 |
    |---|---|---|---|---|---|---|---|---|
    | spends in the budget, B/c | 500 000 | 100 000 | 50 000 | 10 000 | 5000 | 1000 | 500 | 200 |
    | mean underspend | 0.045 % | 0.10 % | 0.14 % | 0.29 % | 0.40 % | 0.86 % | 1.30 % | 2.36 % |

    So what matters is the number of spends in the budget, not its size in
    money. Small spends make the delay almost free: at c = 0.01 only 0.045 % of
    the budget is left. The overspend rate stays at or below about ε for every c
    (0.35–1.3·10⁻³ at ε = 10⁻³ in 40 000 runs; the highest, at c = 25 with only
    200 spends, is about two standard errors above ε). The controller itself does
    not change: it divides the budget and the visible spend by c and counts spends.

    ![Underspend against the cost per realised token c](../results/cost.png)
11. **A delivery delay of p must be planned for.** If a new p reaches the
    clients δ seconds after the center decides it (a broker, or clients that
    poll), the center plans with a bound δ_max and widens its in-flight window by
    ceil(δ_max/Δ) periods ([system.md](system.md) §6, *Push or pull*). Default
    scenario, ε = 10⁻³, 40 000 runs per row:

    | δ (true) | 0 | 0.5 s | 2 s | 5 s | 10 s | 30 s |
    |---|---|---|---|---|---|---|
    | planned for (δ_max = δ): overspend | 1.0·10⁻³ | 0 | 0.5·10⁻⁴ | 1.0·10⁻³ | 0.95·10⁻³ | 0.5·10⁻⁴ |
    | planned for: mean underspend | 0.40 % | 0.57 % | 0.53 % | 0.45 % | 0.50 % | 0.84 % |
    | ignored (δ_max = 0): overspend | | | | **2.3·10⁻²** | **1.3·10⁻¹** | **5.1·10⁻¹** |

    Ignoring even a 1 s delay doubles the overspend rate (2.1·10⁻³); a 5 s delay
    makes it 23 times ε. Planned for, overspend stays at or below ε.

    The cost is not smooth in δ, because the window grows in whole periods.
    - **δ much shorter than a period costs most within its band.** At δ = 0.5 s
      the window already grows by a full 5 s period, so about 4.5 s of exposure
      is counted both as visible and as in flight: 0.57 % against 0.45 % at
      δ = 5 s, where the extra period fits exactly.
    - **A large δ costs more than the in-flight noise alone.** At δ = 30 s,
      0.84 % against 0.59 % from the noise. The controller's corrections,
      including the final creep towards B, also reach the clients 30 s late.

    ![Underspend against the delivery delay of p](../results/message_delay.png)
12. **A delivery delay far larger than planned is a runaway; three defences stop
    it.** With δ = 2 min and δ_max = 0, every run overspends, on average by
    9,700 units (about 2 × B): the controller reads the long silence as a tiny k
    and pushes p to 1 ([system.md](system.md) §6). Default scenario, ε = 10⁻³,
    40 000 runs per row:

    | δ = 2 min, unplanned | runs that overspend | worst overspend | underspend |
    |---|---|---|---|
    | no defence | 100 % | 10,383 units | 0 % |
    | age check (clients ignore p older than 1 s) | 0 | 0 | 100 % (stopped) |
    | halt when the measured delay exceeds plan | 0 | 0 | 99.7 % (stopped) |
    | rise limit ×2 per d | 50 % | 534 units (11 % of B) | 0.06 % |
    | rise limit ×1.5 per d | 63 % | 61 units (1.2 % of B) | 0.07 % |
    | rise limit ×1.5 + halt | 0 | 0 | 99.7 % (stopped) |

    The age check and the halt stop the campaign and leave the decision to an
    operator. The rise limit keeps it running and bounds the damage without
    preventing overspend. When nothing is wrong, a rise limit of ×1.5 costs
    nothing measurable: 0.40 % underspend as without it, and 0.38 % with a 10×
    too low initial guess of k.

## 4. Limits

- **Optimality.** The controller is greedy for each step. It is not the solution
  of the full dynamic programme. It does match the known-k oracle, and the
  remaining gap is the delay noise that any steadily-pacing strategy has.
  Solving the dynamic programme for the end-game would tell how much more is
  possible.
- **Sensitivity to the traffic model.** The ε guarantee assumes Poisson traffic
  and a fixed delay ([system.md](system.md) §3 and §7).
