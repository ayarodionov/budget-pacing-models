# Token cap (a) for traffic that varies

Token cap version (a) ([../token-cap-a.md](../token-cap-a.md)) with a request
rate that follows the time of day and the day of the week
([system.md](system.md)).

Results: [results/seasonal/token-cap-a.md](../../results/seasonal/token-cap-a.md).

## 1. What changes

**The rule and its guarantee: nothing.** The cap counts tokens:

    tokens handed out ≤ floor(B/c) + failures reported (+ tokens returned)

Traffic appears nowhere in this rule or its proof, so overspend stays
impossible whatever the traffic does. A busy hour only means that clients ask
for top-ups more often.

**The pacing underneath** changes as for the probabilistic controller
([probabilistic.md](probabilistic.md)): with a known profile it spreads the
budget over the remaining traffic, without one it assumes constant traffic. Under
a cap, pacing needs no risk check, because the cap prevents overspend; it only
has to keep the spending even.

**The cost** comes, as before, from tokens whose outcome is not yet known when
the budget runs out ([../token-cap-a.md](../token-cap-a.md) §4). It falls like
1/T, so over a month it is negligible.

## 2. Results

The 30-day campaign of the seasonal experiments (B = 3.6 million units), plain
pacing under the cap, 500 runs per row:

| r | failure reports after | profile | overspends | mean underspend | max deviation from the traffic target |
|---|---|---|---|---|---|
| 0.1 | 30 s | known | 0 | 791 units (0.022 %) | 0.05 % |
| 0.1 | 30 s | unknown | 0 | 96 units (0.003 %) | 0.57 % |
| 0.1 | 10 min | known | 0 | 4,855 units (0.13 %) | 0.13 % |
| 0.1 | 10 min | unknown | 0 | 1,551 units (0.04 %) | 0.57 % |
| 1 | either | either | 0 | 0 | 0.05–0.57 % |
| any | any | p = 1 throughout | 0 | 0 | about 98 % |

1. **No overspend in any setting,** including a controller that bids at p = 1
   for the whole month. (That one spends the budget in the first hours, hence
   the 98 % deviation; the cap only prevents overspend, not haste.)
2. **The cost is at most 0.13 % of the budget,** with slow failure reports and
   r = 0.1. With every bid charged (r = 1) it is zero.
3. **Pacing without the profile leaves less unspent under a cap.** It runs a
   little ahead of the traffic, and the cap turns that eagerness into using the
   whole budget. Pacing with the profile aims to finish exactly at T, so tokens
   still unresolved at the end cost budget. The price of pacing without the
   profile is evenness: 0.57 % deviation from the target against 0.05 %.
4. **The cap needs no traffic profile for safety.** Version (a) is the natural
   choice when the profile is unknown or unreliable: the probabilistic
   controller then needs a short-memory risk check to stay within ε, while the
   cap is safe without one.

**Spending evenly in time** (§3 of [probabilistic.md](probabilistic.md)) under
the cap, failure reports after 30 s: no overspend, and underspend 552–560 units
(0.016 %) at r = 0.1, 0 at r = 1, with or without the profile (the pacing
estimates the current rate from the last 10 minutes when the profile is
unknown). With failure reports after 10 min it is about 3,100 units (0.09 %).
Spending stays within 0.05–0.09 % of the even line. The cap's safety does not
depend on the pacing goal.
