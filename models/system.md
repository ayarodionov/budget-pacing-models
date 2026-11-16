# System and common model

The system that all models share: who takes part, how a bid request turns into
spend, and the formal model. Each model of the center's strategy builds on this
file:

| Model | Overspend | Needs from clients |
|---|---|---|
| [Probabilistic controller](probabilistic.md) | probability ≤ ε | nothing |
| [Token cap, version (a)](token-cap-a.md) | impossible | failure reports, tokens used |
| [Token cap, version (b)](token-cap-b.md) | impossible, if the delay d is a hard maximum | nothing (but clients must hold tokens) |

Problem statement and clarifications: [INITIALMODEL.MD](../INITIALMODEL.MD).

## 1. Configuration

### Actors

The system has three kinds of participants.

1. **Bid request source.** One common stream of bid requests (auction
   opportunities). Requests come one at a time at random moments, about λ per
   second on average. In this version the rate is steady: a Poisson process with
   constant λ. Nobody controls the source, and the center does not know λ.

2. **Clients** (bidders). There are N of them. The center does not know N, and it
   does not need to. Every client listens to the same source, and each request is
   delivered to **exactly one** client, chosen at random with equal probability
   1/N. A client that receives a request responds with a **token** with
   probability p, or ignores the request with probability 1 − p. A token is the
   client's decision to bid. It is not yet money: bidding and actually being
   charged are separate events. Clients have no token limit. Every client uses
   the same p, which it gets from the center and uses until the center sends a
   new one.

3. **Center.** The center owns the **budget B** and has to spend it within the
   **horizon T** (for example, one campaign hour). It never sees bid requests or
   tokens directly. Its only view is the budget, which shows the spend with a
   delay d. Its only control is p, which it broadcasts to all clients at once.
   Clients apply a new p immediately, so the center can stop all spending at once
   by sending p = 0.

### What happens to a single bid request

1. The source emits a request at time s.
2. The request reaches one client, chosen at random.
3. The client creates a token with probability p (it bids), or drops the request.
4. The token turns into real spend with probability r. Otherwise it costs
   nothing, for example because the bid lost or was never charged. Realised spend
   is always exactly **1 budget unit**. The center does not know r, but r is stable.
5. The 1 unit is taken from the budget at time s, but the center sees it only at
   time s + d. The delay d is fixed and known.

```mermaid
flowchart LR
    SRC["Bid request source<br/>Poisson, rate λ"] -->|"each request goes to<br/>one random client (1/N)"| C1["Client 1"]
    SRC --> C2["Client 2"]
    SRC --> CN["Client N"]
    C1 & C2 & CN -->|"token with<br/>probability p"| TOK(("tokens"))
    TOK -->|"realised with probability r<br/>1 unit each"| BUD[("Budget B")]
    BUD -.->|"visible after delay d"| CTR["Center"]
    CTR ==>|"broadcast p<br/>(takes effect immediately)"| C1 & C2 & CN
```

### The control loop

The center acts every Δ seconds. This is the **control period**, the `step`
parameter, 5 s by default:

1. Read the visible budget. It shows all spend up to time t − d.
2. Update the estimate of how much one unit of p spends per second (k = λ·r, §2).
3. Choose a new p. Pacing says how fast to spend. The risk check says how much
   spend can be in flight without risking overspend
   ([probabilistic.md](probabilistic.md) §1).
4. Broadcast p to all clients. By default the center sends p every period. With
   a **broadcast threshold** it sends p only when p changes by more than that
   fraction, and clients keep the last p they received. A stop (p = 0) is always
   sent.

Between updates p is constant. The budget is read only at the start of each
period, so the delay d is effectively rounded up to a whole number of periods.

The center therefore steers using a view of the budget that is d seconds old.
Spend that happened during the last d seconds is **in flight**: it has already
been charged but the center cannot see it yet. That is the whole difficulty. If
d = 0, the center could spend until exactly B and stop. With d > 0, it must stop
early enough that the in-flight spend still fits in the budget.

### Goals and constraints

- **Overspend is critical.** The probability that total spend exceeds B must stay
  at or below a small, chosen ε.
- **Underspend should be avoided, but it is not critical.** Spend as close to B as
  ε allows.
- **Do not spend too fast.** Spend should be spread over the whole horizon,
  ideally at a steady rate of about B/T. It should not all go at the start.

### Example with the default numbers

λ = 1000 requests/s and r = 0.1, so k = λ·r = 100 units/s at p = 1. To spend
B = 5000 units in T = 1 h, the target rate is about 1.39 units/s, which needs
p ≈ 0.014. At that rate about 14 of the 1000 requests per second become tokens,
and about 1.4 of those become spend. With d = 30 s, about 42 units are in flight
at any moment. Near the end of the hour, those 42 units are what can push the
total over B.

## 2. Formal model

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
| k | k = λ·r, budget spend per second at p = 1; actual spend rate is k·p (the only unknown the center needs; see [IMPLEMENTATION.md §1](../IMPLEMENTATION.md#1-how-k-is-estimated)) | **no**, but estimated from the budget |
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

## 3. Why a Poisson process?

The problem statement fixes only the *average* rate of requests ("steady
traffic"). How requests vary around that average is a modelling choice. Poisson
is the natural default, for three reasons.

1. **It fits the source.** Bid requests come from many users and pages acting
   independently, each one rarely. Merging many such independent streams gives
   approximately a Poisson process (Palm–Khintchine theorem). Among streams with a
   given rate, Poisson also assumes nothing more: no bursts, no regularity, no
   memory.
2. **The spend stream is close to Poisson even if requests are not.** Only
   p·r ≈ 0.0014 of requests become spend. Sparse random thinning of a stationary
   stream makes it close to Poisson (Rényi's theorem), so short-term burstiness
   in the requests mostly disappears before it reaches the budget.
3. **It makes the model solvable exactly.** Random routing, p and r each thin the
   stream, and thinning keeps it Poisson. So N drops out and only k = λ·r matters
   (see §2, *Reduction*). Counts in separate intervals are independent, so the
   estimate of k is a closed-form Gamma posterior. The in-flight spend is then
   negative-binomial, so the risk check computes exact probabilities. The ε
   guarantee ([probabilistic.md](probabilistic.md) §2) depends on this.

**Weak point.** Poisson assumes the variance of counts equals the mean. Real
traffic often varies more. Changes in rate that last tens of seconds or longer
(drift, campaigns starting and stopping, bursts) survive the thinning and give
counts with variance F × mean, where F > 1. The controller would then
underestimate the uncertainty in the in-flight spend: the reserve should be
about √F times larger, and the real overspend probability would be above ε.

Possible remedies, not implemented:
- A negative-binomial count model with F estimated from the spend observed in
  each period.
- A time-varying rate λ(t) for daily cycles.
- A Hawkes process if bursts trigger further bursts.

## 4. Are tokens needed?

**Not in this model.** Clients hold no stock of tokens, so "the client creates a
token" just means "the client accepts the request with probability p". The center
never sees tokens, only the budget, so the whole system reduces to
spend rate = k·p (see §2, *Reduction*). The controller and the simulator use only p.
A "token" is the Bernoulli(p) draw, and the word could be replaced by "bid".
Tokens are still useful as a name for one idea: *bidding* (probability p, which the
center controls) is separate from *being charged* (probability r, which nobody controls).

**Where tokens would matter: limited allowances.** Tokens matter once they are
something a client must *have* in order to bid. That is a separate control
channel from p, and it can provide a guarantee that p alone cannot.

### Example: the token-lease model

- **Wallets.** Each client has a token wallet. To bid, it needs one token *and* a
  "yes" from its p-coin. The bid uses up the token.
- **Leases.** Clients ask the center for a lease of L tokens when their wallet
  runs low. As a side effect, the center now learns who the clients are, so N
  becomes observable. Unused tokens are returned when the lease expires.
- **Outcomes.** A used token either turns into spend (1 unit, visible after d) or
  fails (the bid lost or was not charged). The client reports the failure back
  after a delay d_f.
- **Center's rule:** visible spend + tokens outstanding ≤ B, where "outstanding"
  means leased tokens whose outcome is not yet known. No token can cost more than
  1 unit, so **overspend is impossible**. This is a guarantee with certainty, not
  "probability ≤ ε". It holds even if a stop message is lost, a client stops
  responding, or the delay is longer than expected, which are exactly the failures
  that the probabilistic controller has to assume away.
- **p is still needed.** A wallet limits *how much* a client may spend, not *how
  fast*. The center still uses p for even spending (point 5).

**Cost of the certain guarantee.** The reserve has to cover every outstanding
*token*, not the expected *spend*. With the default numbers (λ·p ≈ 14 bids/s,
r = 0.1, d_f = 30 s), about 420 tokens are outstanding at any moment. They will
turn into only about 42 units of spend, and the probabilistic controller reserves
about 20 units. Near the end the lease rule throttles spending once the remaining
budget falls below about λ·p·d_f. The remaining budget then shrinks only with time
constant ≈ d_f / r (≈ 5 min), so a lot of budget is left unspent unless:

- r is close to 1 (most bids are charged), or
- failures are reported quickly (d_f ≪ d; for example, an auction loss is known
  within milliseconds), or
- leases are a **backstop**: the probabilistic controller paces as now, and token
  leases cap the damage from rare failures such as a lost stop message or a client
  that keeps bidding.

**This example is worked out as two models:** [token-cap-a.md](token-cap-a.md)
(clients report failures) and [token-cap-b.md](token-cap-b.md) (no reports from
clients, granted tokens expire). Both make overspend impossible. The cost is the
budget held by tokens whose outcome is not yet known, roughly
(1 − r)·d_f′ / (e·r·T) of the budget: 0 % when every bid is charged, and about
3 % when 90 % of bids fail and a failure becomes known only after 30 s.

**Other models where tokens would help:**
- **Different bid prices.** A bid costs c units instead of 1. Tokens become the
  unit of money, and a bid uses c tokens.
- **Several campaigns or client groups with their own budgets.** A broadcast p
  is the same for everyone, while token allocations can differ per client.
- **Unreliable messages to clients.** If p cannot be changed immediately (your
  answer C does not hold), only a pre-set allowance limits what a client can spend
  after losing contact with the center.

## 5. Is the number of clients needed?

**No, not in anything modelled here.**

- **Main model and controller.** Each request goes to one random client, and all
  clients bid with the same p. Splitting a Poisson stream at random and then
  thinning it keeps it Poisson, so total spend is Poisson(k·p) for any N (see
  §2, *Reduction*). N appears in each client's share and cancels when the shares are
  added. `test_number_of_clients_does_not_matter` checks this with 1, 7 and
  500 clients.
- **Token cap, versions (a) and (b)** ([token-cap-a.md](token-cap-a.md),
  [token-cap-b.md](token-cap-b.md)). Both
  rules limit the *total* number of tokens, through tokens used and failures
  reported, or through tokens granted and the budget. Neither the rules nor the
  proofs mention N.

**Where N would start to matter:**

| Extension | Why | Does the center need N? |
|---|---|---|
| Tokens held in per-client wallets (leases of L tokens) | up to about N·L tokens can sit unused when the budget runs out, and that budget is lost | helpful, not required. The center learns how many clients hold leases from the lease requests. Shrinking leases near the end works without N. |
| Safety from a per-client rate limit (at most m bids/s per client) | worst-case in-flight spend is N·m·(d + Δ) | **yes**. This is one reason to prefer the token cap. |
| Clients that differ (acceptance probability, cost per bid) | one broadcast p no longer fits all, and the spend rate depends on the mix of clients | possibly, or at least the mix of client types |
| Requests that do not arrive independently (for example one request reaching several clients) | the random-split argument fails, and N can change the variance of spend | possibly |

For this model, point 3 of the problem statement (each request reaches one
client chosen at random with equal probability) and answer F (the same p for
every client) rule out the last two.

## 6. Extensions (all models)

- **Traffic that changes over time.** Forecast λ(t) and pace along it.
- **k that drifts.** Discount old observations (see
  [IMPLEMENTATION.md](../IMPLEMENTATION.md) §1).
- **A random delay.** Use its upper quantile or model its distribution. The
  token cap (b) relies on d being a hard maximum.
- **A delay before clients receive a new p.** Add it to the in-flight window.
- **Traffic that varies more than Poisson** (§3). This is the assumption the
  probabilistic controller's ε guarantee is most sensitive to. The token caps do
  not depend on it.
