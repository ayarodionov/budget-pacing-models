# Token cap (b) for traffic that varies

Token cap version (b) ([../token-cap-b.md](../token-cap-b.md)) with a request
rate that follows the time of day and the day of the week
([system.md](system.md)).

Results: [results/seasonal/token-cap-b.md](../../results/seasonal/token-cap-b.md).

## 1. What changes

**The rule and its guarantee: nothing.** Each step the center grants at most

    A = B − S_obs − (tokens granted in the last d seconds)

Traffic appears nowhere in this rule or its proof. The proof needs a spend delay
that never exceeds d, and the traffic cycles do not change the delay. So
overspend stays impossible.

**The grants adapt by themselves.** A grant is half of the budget that is
certainly free. It does not depend on the traffic, so in a busy hour the
clients use more of it and in a quiet hour more of it expires. Early in the
campaign the free budget is far above any hour's demand, so the grant is never
the limit; only near the end, when the free budget shrinks, does it bind.

**The pacing underneath** changes as for the probabilistic controller
([probabilistic.md](probabilistic.md)): with a known profile it spreads the
budget over the remaining traffic, without one it assumes constant traffic.

**The cost** is the budget held by grants whose spend is not yet visible
([../token-cap-b.md](../token-cap-b.md)). It falls like 1/T, so over a month it
is negligible.

## 2. Results

The 30-day campaign of the seasonal experiments (B = 3.6 million units), plain
pacing under the cap, grant share 0.5, 500 runs per row:

| r | profile | overspends | mean underspend | max deviation from the traffic target |
|---|---|---|---|---|
| 0.1 | known | 0 | 1,234 units (0.034 %) | 0.05 % |
| 0.1 | unknown | 0 | 184 units (0.005 %) | 0.57 % |
| 1 | known | 0 | 32 units (0.001 %) | 0.05 % |
| 1 | unknown | 0 | 1 unit | 0.57 % |
| any | p = 1 throughout | 0 | 0 | about 98 % |

1. **No overspend in any setting,** including p = 1 for the whole month.
2. **The cost is at most 0.034 % of the budget.**
3. **As for version (a), pacing without the profile leaves less unspent under
   the cap** but follows the traffic less closely (0.57 % against 0.05 %).
4. **Version (b) keeps its usual conditions:** a hard maximum for d, grants that
   really expire, and a grant margin that covers the delivery delay of grants
   ([../token-cap-b.md](../token-cap-b.md) §3, §6). Varying traffic adds none.
