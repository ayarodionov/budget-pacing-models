# System and common model

The system that all models share: who takes part, how a bid request turns into
spend, and the formal model. Each model of the center's strategy builds on this
file:

| Model | Overspend | Needs from clients |
|---|---|---|
| [Probabilistic controller](probabilistic.md) | probability ≤ ε | nothing (clients subscribe to p, §6) |
| [Token cap, version (a)](token-cap-a.md) | impossible | failure reports, tokens used (clients register, §6) |
| [Token cap, version (b)](token-cap-b.md) | impossible, if the delay d is a hard maximum | nothing (but clients must hold tokens and register, §6) |

Problem statement and clarifications: [initialmodel.md](initialmodel.md).

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
   nothing, for example because the bid lost or was never charged. A realised
   token always costs the same fixed amount, **c budget units**, so spend is
   proportional to the number of realised tokens. The center does not know r,
   but r is stable.
5. The c units are taken from the budget at time s, but the center sees them only
   at time s + d. The delay d is fixed and known.

```mermaid
flowchart LR
    SRC["Bid request source<br/>Poisson, rate λ"] -->|"each request goes to<br/>one random client (1/N)"| C1["Client 1"]
    SRC --> C2["Client 2"]
    SRC --> CN["Client N"]
    C1 & C2 & CN -->|"token with<br/>probability p"| TOK(("tokens"))
    TOK -->|"realised with probability r<br/>c units each"| BUD[("Budget B")]
    BUD -.->|"visible after delay d"| CTR["Center"]
    CTR ==>|"broadcast p<br/>(takes effect immediately)"| C1 & C2 & CN
```

### The control loop

The center acts every Δ seconds. This is the **control period**, the `step`
parameter, 5 s by default:

1. Read the visible budget. It shows all spend up to time t − d.
2. Update the estimate of how many spends per second p = 1 would produce (k = λ·r, §2).
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

λ = 1000 requests/s, r = 0.1 and c = 1, so k = λ·r = 100 spends/s, or
c·k = 100 units/s, at p = 1. To spend B = 5000 units in T = 1 h, the target
rate is about 1.39 units/s, which needs p ≈ 0.014. At that rate about 14 of the
1000 requests per second become tokens, and about 1.4 of those become spend. With
c = 10 the same budget is 500 spends of 10 units; p is then 10 times smaller,
≈ 0.0014. With d = 30 s, about 42 units are in flight
at any moment. Near the end of the hour, those 42 units are what can push the
total over B.

## 2. Formal model

| Symbol | Meaning | Known to center? |
|---|---|---|
| B | budget, in budget units | yes |
| c | cost of one realised token, in budget units (fixed; spend = c × realised tokens) | yes: every spend appears as a budget decrement of exactly c |
| T | horizon (time to spend the budget) | yes |
| d | delay between a spend and its appearance in the budget: fixed in the base model (answer A); it may also vary, see §7 | yes (or its distribution F) |
| Δ | control period (how often the center may broadcast a new p) | yes |
| δ | delivery delay: a new p reaches the clients δ seconds after the center decides it (0 in the base model, answer C) | no |
| δ_max | upper bound on δ that the center plans with (§6, *Push or pull*) | yes (chosen) |
| ε | allowed probability of overspend | yes (chosen) |
| λ | rate of bid requests (steady Poisson) | **no** |
| r | probability that a token turns into budget spend (stable) | **no** |
| N | number of clients | **no** for the probabilistic controller; the token caps learn it through registration (§5) |
| k | k = λ·r, realised tokens (spends) per second at p = 1; actual spend rate is c·k·p units/s (the only unknown the center needs; see [implementation.md §1](implementation.md#1-how-k-is-estimated)) | **no**, but estimated from the budget |
| p | token probability broadcast to all clients; takes effect after δ (immediately in the base model) | it sets it |

A request goes to a uniformly random client. That client creates a token with
probability p, and the token becomes c units of spend with probability r.

**Interpretation of answer B (revised).** Point 8 says "not every token realises
in budget spending" and "budget spend is proportional to the number of spent
tokens". The revised answer B fixes that proportion. I model it as two parts: a
fraction r of tokens is realised, and each realised token costs the same c units.
On average a token costs r·c. If every token is realised, set r = 1; the original
answer "1 token = 1 budget spend" is c = 1.

**Why c is treated as known.** A realised token always costs exactly c, so every
spend appears in the budget as a decrement of c. The first visible spend reveals
c (or it is simply the price in the contract). The unknown part of the proportion
is r, and it is learned together with λ as k = λ·r.

**Reduction.** Thinning a Poisson process keeps it Poisson. So the number of
realised tokens in an interval of length Δ with constant p is

    X ~ Poisson(k · p · Δ),     k = λ · r   (spends per second at p = 1)

and the spend is c·X units, whatever N is and however requests are split between
clients. The center does not need N, λ or r separately, only the single number k,
and it can learn k from its own budget. (This is about the math; the token caps
still need clients to register in order to hand out tokens, see §5.)

**Counting in spends.** Since spend = c·X, it is enough to count in spends:
the budget holds B/c of them, and overspend means more than B/c spends in total.
All the statistics below (the estimate of k, the risk check) work on these counts;
c only converts to and from budget units. The size of c matters through B/c: the
fewer and larger the spends, the noisier the spend relative to B, and the larger
the reserve the center must keep ([probabilistic.md](probabilistic.md) §3). `test_number_of_clients_does_not_matter` checks this against an
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

**Measured.** The simulator can now add such fluctuations (`rate_noise`: a
random traffic factor with mean 1, size cv and a correlation time; in scenario
files, `[traffic.fluctuations]`). The default scenario, ε = 10⁻³, 20,000 runs:

| Fluctuations (size cv, duration) | Overspend rate | Mean underspend |
|---|---|---|
| none (Poisson) | 0.9·10⁻³ | 0.40 % |
| ±30 %, about 1 min | **7.6·10⁻²** (76 × ε) | 0.44 % |
| ±30 %, about 10 min | **3.7·10⁻²** | 0.54 % |
| ±60 %, about 1 min | **2.1·10⁻¹** | 0.54 % |
| ±60 %, about 10 min | **1.2·10⁻¹** | 0.87 % |

The guarantee breaks badly. Bursts lasting about as long as the spend delay hurt
most: they swell the spend in flight, which the risk check sizes for Poisson.
A short-memory estimate of k helps only against slow fluctuations (±30 % over
10 min: 3.7 % → 1.4 % with a 2-minute memory), not against minute-long bursts.
**The token caps are not affected:** no overspend even at p = 1 with ±60 %
bursts. So if your traffic is burstier than Poisson, measure it (scenario file,
`[traffic.fluctuations]`) before relying on ε, or use a cap.

Possible remedies for the probabilistic model, not implemented:
- A negative-binomial count model with F estimated from the spend observed in
  each period, widening the reserve by about √F.
- A Hawkes process if bursts trigger further bursts.

(A time-varying rate for daily and weekly cycles is implemented: the seasonal
models, [seasonal/system.md](seasonal/system.md).)

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
- **Outcomes.** A used token either turns into spend (c units, visible after d) or
  fails (the bid lost or was not charged). The client reports the failure back
  after a delay d_f.
- **Center's rule:** visible spend + c × tokens outstanding ≤ B, where
  "outstanding" means leased tokens whose outcome is not yet known. No token can
  cost more than c units, so **overspend is impossible**. This is a guarantee with certainty, not
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
- **Different bid prices.** Here every realised token costs the same c. If
  prices vary from bid to bid, tokens can become the unit of money, and a bid uses
  as many tokens as its price.
- **Several campaigns or client groups with their own budgets.** A broadcast p
  is the same for everyone, while token allocations can differ per client.
- **Unreliable messages to clients.** If p cannot be changed immediately (your
  answer C does not hold), only a pre-set allowance limits what a client can spend
  after losing contact with the center.

## 5. Is the number of clients needed?

**It depends on the model.** The probabilistic controller never needs N. The
token caps need to know their clients, though not for their safety:

| Model | Does the center know N? | Is N needed for the math and the guarantee? | Is N needed to run it? |
|---|---|---|---|
| Probabilistic controller | **no** | **no** | **no**: p is broadcast to subscribers |
| Token cap (a) | **yes**, through registration | **no**: the rule counts total tokens | **yes**: tokens go into the wallets of registered clients |
| Token cap (b) | **yes**, through registration | **no**: the rule counts total tokens | **yes**: each grant is split among the registered clients |

- **Probabilistic controller.** Each request goes to one random client, and all
  clients bid with the same p. Splitting a Poisson stream at random and then
  thinning it keeps it Poisson, so total spend is Poisson(k·p) for any N (see
  §2, *Reduction*). N appears in each client's share and cancels when the shares
  are added. `test_number_of_clients_does_not_matter` checks this with 1, 7 and
  500 clients. Point 9 of the problem statement, "the center does not know the
  number of clients", holds as stated.
- **Token caps, versions (a) and (b)** ([token-cap-a.md](token-cap-a.md),
  [token-cap-b.md](token-cap-b.md)). The rules and their proofs use only totals:
  tokens handed out and failures reported in (a), tokens granted and the budget
  in (b). N appears in neither, so the guarantee holds for any number of
  clients, and for any split of the tokens among them. But a token is
  permission held by a *specific* client, so tokens must be delivered to
  someone. That needs registration (§6): clients identify themselves and keep a
  lease alive. As a side effect the center learns its live clients, and their
  count is its N. **So with a token cap, point 9 of the problem statement no
  longer holds:** the center knows the number of clients, because the mechanism
  requires them to register.
  - In (a) the center uses N only for wallet sizes: near the end a top-up gives
    at most a share of what is left per live client (`wallet_share`).
  - In (b) the center uses N only to split each grant.
  - In both, a wrong count costs underspend, never overspend: a dead client's
    tokens expire or stay counted, and a new client waits for its first
    tokens. Version (b) can even avoid counting clients altogether, by putting
    each step's grant in a pool that clients draw from
    ([token-cap-b.md](token-cap-b.md) §1); the clients still have to be
    authenticated, because whoever draws from the pool draws budget.

**Where N would start to matter:**

| Extension | Why | Does the center need N? |
|---|---|---|
| Tokens held in per-client wallets (leases of L tokens) | up to about N·L tokens can sit unused when the budget runs out, and that budget is lost | the center knows N from the leases anyway (token cap (a)); it uses N to shrink each top-up near the end (`wallet_share`) |
| Safety from a per-client rate limit (at most m bids/s per client) | worst-case in-flight spend is N·m·(d + Δ) | **yes**. This is one reason to prefer the token cap. |
| Clients that differ (acceptance probability, cost per bid) | one broadcast p no longer fits all, and the spend rate depends on the mix of clients | possibly, or at least the mix of client types |
| Requests that do not arrive independently (for example one request reaching several clients) | the random-split argument fails, and N can change the variance of spend | possibly |

For this model, point 3 of the problem statement (each request reaches one
client chosen at random with equal probability) and answer F (the same p for
every client) rule out the last two.

## 6. How clients join: subscription and registration

Whether the center must know its clients depends on the model:

| Model | What the center must reach | Must the center know its clients? |
|---|---|---|
| Probabilistic controller | broadcast p to everyone | **no**: clients *subscribe* to p |
| Token cap (a) | hand out tokens; receive used and failed counts | **yes**: clients *register* and hold leases |
| Token cap (b) | hand out each period's grant | **yes**, to split the grant; nothing comes back |

The models' math does not change: k is still learned from the budget alone,
and no formula needs N (§5); registration only delivers the tokens. What this section adds are the rules that keep the
guarantees true when clients join, leave, crash or lose contact.

### Probabilistic controller: subscription

- **Joining.** A client subscribes to the campaign's p channel (a pub/sub topic)
  and then asks for the current p, so it does not wait for the next broadcast.
  It starts with p = 0 until it has one.
- **Ordering.** Each broadcast carries `{campaign, epoch, seq, p}`. A client
  applies a message only if its `seq` is higher than the last one applied, so a
  delayed old p cannot overwrite a newer one. A new epoch (for example after the
  center restarts) resets `seq`.
- **Fail safe on silence.** The center sends p every period Δ even when it has
  not changed. With a broadcast threshold
  ([probabilistic.md](probabilistic.md) §1, step 4) it still sends a small
  keepalive every Δ. A client that hears nothing for L seconds (for example
  L = 3Δ) sets p = 0 by itself.

  This is the one real requirement. The controller assumes that a new p, and in
  particular p = 0, takes effect immediately (answer C). A client cut off from
  the center cannot receive a stop, so it has to stop on its own. A lost client
  then adds at most about L seconds of spend at its last p. Keep L small compared
  with the delay d, or count the extra spend into the in-flight reserve.

The center keeps no state about clients and never learns who they are.

### Push or pull

p can reach the clients in two ways. Both satisfy the rules above. They differ
in delay, in who carries the load, and in how they fail. Below, N is the number
of clients and P the polling interval.

- **Push.** The center publishes `{epoch, seq, p}` every Δ, and clients receive
  it. Clients subscribe to the channel (a pub/sub group or a broker topic). The
  delivery layer keeps their addresses, not their identities.
- **Pull.** Every client fetches the latest `{epoch, seq, p}` every P, from the
  center or from a shared store. Nobody keeps a list of clients.

| | Push | Pull |
|---|---|---|
| **Delay before a new p is used** | network latency, about 1 ms inside a cluster | up to P + latency (P/2 on average); about a microsecond from a store on the same node |
| **Delay of a stop (p = 0)** | about 1 ms | up to P: matters if p = 0 serves as an emergency stop |
| **Cost in underspend** (default scenario, ε = 10⁻³) | none: about 0.43 % of B | with P = Δ = 5 s, δ_max ≈ 5 s, so √(d + Δ + δ_max)/√(d + Δ) = √(40/35): about 0.46 %; with P = 1 s, under +0.01 points |
| **Center load per period** | N sends (one per node with `Phoenix.PubSub`) | N requests that each need an answer. Served by the center's own process they queue up on it; served from a cache (ETS) they cost the center nothing |
| **Who knows the clients** | the delivery layer knows their addresses | nobody (the poll rate shows about N/P) |
| **Clients joining and leaving** | join or leave the group; `:pg` removes dead processes | nothing to manage |
| **Clients outside BEAM or behind a load balancer** | needs a broker or open connections (websockets) | plain HTTP or gRPC requests |
| **Lost message** | the next period fixes it; silence rule after L | the next poll retries; silence rule after L |
| **Center crash** | messages stop; clients stop after L | polls fail; clients stop after L |
| **Main trap** | a relay that re-sends an old p as new | a shared store that keeps serving the last p after the center died. Clients must check that `seq` *advances*, not only that a poll succeeded |
| **Load spikes** | none: the center paces the sends | polls that happen at the same moment hit together; add random jitter to each client's polling time |
| **Slow client** | its mailbox queues the periods; it applies them in order | it polls late, and its silence rule may stop it (safe) |
| **Token cap (b)** | grants are per-client messages: natural to push | a client fetching its grant must identify itself, so it registers anyway; fetching late also shortens the usable part of the grant |

**Silence rule, in both cases.** A client sets p = 0 when `seq` has not
advanced for L seconds: no newer message (push), or polls that fail or keep
returning the same `seq` (pull).

**Delivery delay.** A new p takes effect δ seconds after the center decides it:
network or broker latency for push, up to P plus latency for pull. The center
does not know δ, only a bound δ_max that it plans with. During those δ seconds
clients still bid at the previous p. The base model has δ = δ_max = 0 (answer C).

*How the controller covers it: widen the in-flight window by δ_max.* With a
delay δ ≤ δ_max, the spend not yet visible after the next period comes from the
p decided during (t − d − δ, t], plus the new p for the next period. That is at
most the exposure over (t − d − δ_max, t] plus p_new · Δ. So the center counts a
decided p as in flight for ceil(d/Δ) + ceil(δ_max/Δ) periods instead of
ceil(d/Δ), and changes nothing else.
- **Round up separately.** ceil((d + δ_max)/Δ) can be one period too short.
- **The estimate of k stays on the safe side.** The exposure counted as visible
  then never covers more than what has really become visible, so k̂ can only
  come out a little high.
- Code: `Scenario.message_delay_max` in the Python simulation,
  `message_delay_max:` in `PacingController`, and `message_delay_max_ms:` in
  `PacingCenter`, which also sets the grant margin and the clients' silence
  limit from it.

*What it costs and what ignoring it costs*
([probabilistic.md](probabilistic.md) §3, item 11). Planning with δ_max keeps
overspend within ε and costs up to one extra period of in-flight window for
each started period of δ_max. The cost is largest when δ is much smaller than
the rounded window, because some spend is then counted both as visible and as
in flight. Ignoring a delivery delay (δ_max = 0 while δ > 0) breaks the
guarantee quickly: in the default scenario with ε = 10⁻³, a 1 s delay doubles
the overspend rate, a 5 s delay makes it 2.3·10⁻² and a 10 s delay 1.3·10⁻¹.

**When the delay is far larger than planned.** Beyond about one spend delay
(d = 30 s), an unplanned δ is not just inaccurate but unstable:

1. The center sends a small p. For d + δ seconds it sees no spend at all.
2. It believes spend shows up after d, so it reads the silence as a tiny k
   ("I bid and nothing happened"). Pacing and the risk check then push p to 1.
3. Those p = 1 messages reach the clients δ later, and the budget goes in
   seconds.
4. The stop that follows is also δ late, so clients keep bidding at p = 1 for
   another δ.

With δ = 2 min, every run overspends, on average by 2 × B
([probabilistic.md](probabilistic.md) §3, item 12). The existing safeguards do
not catch it: a steady backlog still delivers a message with a newer `seq` every
period, so neither the `seq` rule nor the silence rule fires.

Defences, all implemented in the Python simulation and in Elixir:

| Defence | Where | What it does | δ = 2 min unplanned | Cost when nothing is wrong |
|---|---|---|---|---|
| **Age check** | clients | Messages carry their send time (wall clock). A client ignores one older than δ_max + clock skew, so it stops on silence. Needs clocks synchronised within a known skew. | no overspend; campaign stops | none |
| **Halt on measured delay** | center | Clients report the last `seq` they applied in heartbeats. The center knows when it sent each `seq`, so it bounds the delay from below with its own clock (no clock sync). Above δ_max + allowance it sends p = 0 and raises an alarm. Needs heartbeats, so the token-cap modes. | no overspend; campaign stops | none |
| **Rise limit** | controller | p may grow by at most ×1.5 per d. | overspend still frequent, but the worst case falls from 2 × B to 1.2 % of B (11 % with ×2); campaign continues | none measurable, even with a 10× wrong guess of k |
| **Token cap (a)** | cap | Counts tokens, not time. | never overspends; spends B in a burst | the cap's usual cost |

The age check and the halt turn an unexpected delay into a stopped campaign and
an alarm: safe, but someone must act (`PacingCenter.resume/1`, or fix the delay
and plan for it). The rise limit keeps the campaign running and bounds the
damage, but does not prevent overspend on its own. Use the rise limit (×1.5) always,
and the age check or the halt wherever a broker or a polling path can back up.

**Lost connection, stalled budget feed, center restart.** A connection that is
usually fast can still drop for a while. What each model needs to stay safe:

| What breaks | Probabilistic controller | Cap (a) | Cap (b) |
|---|---|---|---|
| center → clients lost | clients keep the last p until their silence limit L: plan for it with `max_silence` | a client spends at most its wallet: safe | no grants arrive: safe |
| old messages after reconnecting | dropped by the age check | safe (tokens are counted) | a clean reconnect is safe (each new grant replaces the old one); a slow backlog needs the age check |
| clients → center lost | nothing needed | budget stays held: safe (underspend) | safe |
| budget feed stalls | halt on a stale feed, otherwise a runaway near the end | safe (does not use the feed) | halt on a stale feed, otherwise unsafe |
| center restarts | clients stop after L; risk used must survive | safe **only if the token counts survive** | safe only if the grant window survives |

Details per cap: [token-cap-a.md](token-cap-a.md) §7 and
[token-cap-b.md](token-cap-b.md) §6.

Rules, all implemented (Python simulation and `PacingCenter`):

1. **Cap (a) is the only certain guarantee** through any outage: it counts
   tokens, not time. Its conditions: no bid without a token, honest failure
   reports, and counters that are never lost.
2. **Durable center state.** Token counts, leases, the grant window and the
   controller (with its risk used) are written to a store *before* tokens,
   grants or p leave the center (`store:` option, `PacingCenter.FileStore`). A
   restarted center starts a new epoch with the old counts. Without this, a
   restart hands out the same budget again.
3. **Clients fail safe on their own:** the silence limit L, lease expiry, and
   the age check on messages.
4. **The budget feed reports how fresh it is** (`{spend, as_of}`). If it is
   older than d + a tolerance, the center halts (p = 0, no grants) until it is
   fresh again. While halted the controller counts p = 0 as the p in effect, so
   its estimate of k is not biased.
5. **The risk check plans for the silence limit:** each p is charged as if it
   may run for max(Δ, L), because after a lost link it does (`max_silence`;
   `PacingCenter` sets it from the clients' silence limit by default).
6. **Wallets shrink near the end:** a top-up gives at most a share of the
   remaining allowance per live client (`wallet_share`, 0.5), so a cut-off
   client holds back little budget.

Measured in the default scenario (ε = 10⁻³; [probabilistic.md](probabilistic.md)
§3, item 13): a link lost at 3,300 s raises the overspend rate to 1.3·10⁻³,
above ε; with `max_silence` = 15 s it falls to 5·10⁻⁵, at a cost of about 0.13
points of underspend. A budget feed that stalls from 3,300 s to the end makes
every campaign overspend, by up to 1,180 units; with the halt, none does (7.9 %
underspend, the budget not spent during the stall). A stall in the middle of
the campaign does no harm either way: when the feed returns, the controller
corrects.

**Relays.** A broker, or a forwarding process on each node, may sit between the
center and the clients. It must hold no state of its own and must not invent
messages. In particular it must never re-send an old p as if it were new,
because that would keep a cut-off client bidding. The `seq` rule protects
against this: only the center creates a newer `seq`, so repeats do not reset
the silence timer.

**The cost per bid.** Every bid asks "bid or not?". If that question is a
message to a process (a `GenServer.call`, as `PacingClient.bid?/1` does when given the pid),
that process becomes the bottleneck at thousands of bids per second, whether p
arrives by push or by pull. The fast design is a **hybrid**:

1. The center pushes `{epoch, seq, p}` once per node per period
   (`Phoenix.PubSub` or a forwarding process per node).
2. On each node the forwarder writes it, with its arrival time, to an ETS table
   with `read_concurrency: true`.
3. Each bid reads that table: a read of well under a microsecond, with no
   message. The silence rule checks the stored arrival time and `seq`.

This is implemented as `PacingRelay` and the `PacingClient.Handle` fast path
([../elixir/README.md](../elixir/README.md#registration-pacingcenter-and-pacingclient)):
about 0.1 µs per bid, against 0.8 µs when each bid asks the client process.

Push carries p across the network, at one message per node per period; pull
does the per-bid read, locally and at almost no cost. The delay stays at
network level, so δ_max ≈ 0 and underspend does not change.

**Which to use.**

| Situation | Use |
|---|---|
| Clients are processes in one Erlang cluster, moderate bid rates | **push** (`PacingCenter` sends to the `:pg` group) |
| High bid rates, or many bidders per node | **hybrid**: push to a node-local ETS table, read it at every bid (`PacingRelay`, `PacingClient.Handle`) |
| Clients outside BEAM, behind a load balancer, or very many | **pull** over HTTP or gRPC, with P ≤ Δ and jitter, served from a cache rather than the center's process; count δ_max = P + latency in the in-flight reserve |
| Token cap (b) | **push** the grants (per-client messages); pull would need registration anyway |

### Token caps: registration with leases

A token is permission to spend, so the center must know who holds tokens: a
client that disappears while holding tokens may still be spending them.

**Registering.**

1. The client calls `register(client_id, credentials)`. It must authenticate
   (for example mTLS or a signed token), so that nobody else can register and
   draw tokens.
2. The center replies `{lease_id, lease_ttl, period, epoch, p}`. Registering
   again with the same `client_id` replaces the old lease instead of adding a
   second client.
3. The client keeps the lease alive with heartbeats. In version (a) it sends its
   failure count with each heartbeat. A lease that gets no heartbeat within
   `lease_ttl` is *lost*.

**Handing out tokens.**

- **Version (b).** Each period the center splits the grant G among the live
  leases. Clients report nothing about their bids, so the split is even (or by
  a fixed weight declared at registration, such as a client's capacity). A
  grant is valid only for its period.
- **Version (a).** Each client holds a small wallet of L tokens and asks for a
  top-up when it runs out, reporting its failure count with the request.
  The allowance A = B/c + F_obs − U counts every token *handed out* as used
  until the client returns or reports it. Tokens sitting in wallets therefore
  hold budget, which adds to underspend, so wallets should shrink near the end
  of the campaign.

**Leaving and losing contact: where safety is decided.**

- **Graceful leave.** `deregister` returns the unused tokens and sends the
  final failure count. The center can reuse the returned tokens at once.
- **Lost lease.** The center cannot tell a crashed client from one that is cut
  off and still bidding, so it must assume the worst.
  - Version (a): the tokens handed to that client stay counted as used, with no
    time limit. Waiting does not help: the budget shows only total spend, so
    the center can never tell how many of a silent client's tokens were spent,
    failed or left unused. They are credited only if the same client registers
    again and reports the old lease (`previous`: its failures and unused
    tokens). A client that never comes back costs at most what it held: one
    wallet plus its unreported failures. Small wallets keep that cost small.
  - Version (b): nothing to do. Grants expire with their period, and the spend
    of the last grant is visible after d, as for any other grant.
- **Credits are bounded per lease.** The failures and returned tokens credited
  to a lease never exceed the tokens it was handed, so a buggy client cannot
  free more budget than it held. A false report within that bound would still
  break the guarantee: clients must be trusted to report honestly.
- **The client enforces its own lease.** In version (a), when its lease expires
  without renewal, the client drops its remaining tokens and stops bidding. In
  version (b) it uses a grant only within its period, by its own clock; leave a
  margin for clock drift (or shorten the grant's validity by that margin).
  Without this rule a cut-off client could keep spending, and the cap's
  guarantee would not hold.
- **In version (b) the grant margin must cover the delivery delay.** A grant
  arrives up to δ_max late; if the client then used it for a whole period, part
  of its spend would happen in the next period, after the center has stopped
  counting the grant. In the simulation, ignoring a 4 s delay (period 5 s) makes
  94 % of runs overspend when every bid is charged
  ([../results/overview.md](../results/overview.md)). So the client uses a grant
  for Δ − margin after it arrives, with margin ≥ δ_max plus clock drift, and δ_max
  must be shorter than a period. Version (a) needs no such rule: it counts tokens
  when they are handed out, not by time.

Combined with these rules, the caps keep their guarantee whatever clients do:
join, leave, crash, or keep bidding while cut off.

**Implementation in Elixir.** `PacingCenter` and `PacingClient`
([../elixir/README.md](../elixir/README.md#registration-pacingcenter-and-pacingclient))
implement this section. One `PacingCenter` process per campaign holds the
`PacingController` and `TokenCap` state and the lease table, and runs a step
every Δ. It broadcasts p through an `:pg` group, which works on one node and
across a cluster. A crashed client is noticed by its missing heartbeats, not by
`Process.monitor`, because a network split looks the same as a crash and must
be treated as a lost lease. `PacingClient` checks the silence limit, its lease
and its grant against its own clock at every bid, so it fails safe without the
center.

## 7. Extensions (all models)

- **Traffic that changes over time.** Forecast λ(t) and pace along it.
- **k that drifts.** Discount old observations (see
  [implementation.md](implementation.md) §1).
- **A spend delay that varies** (implemented, see below).
- **A delay before clients receive a new p** (implemented: δ_max, §6).
- **Traffic that varies more than Poisson** (§3). This is the assumption the
  probabilistic controller's ε guarantee is most sensitive to. The token caps do
  not depend on it.

### A spend delay that varies

If each spend shows up in the budget after its own random delay instead of a
fixed d, the three models are affected very differently.

**Probabilistic controller: weight by the delay distribution.** If each spend
is delayed independently, with distribution F, the Poisson structure survives;
splitting spend by delay is one more thinning:

    visible spend    ~ Poisson(k · E_F),   E_F = ∫ p(s) · F(t − s) ds
    spend in flight  ~ Poisson(k · W_F),   W_F = ∫ p(s) · (1 − F(t − s)) ds

The two are independent, so the estimate of k, the pace and the risk check stay
exactly as they are, with E and W replaced by these weighted sums. The fixed d
is the special case where F jumps from 0 to 1 at d. The alternatives fail:

- **Assume d = a typical value:** spend counted as visible is not there yet, so
  k̂ comes out low and too little is counted in flight. The ε guarantee breaks.
- **Assume d = a high quantile:** safe, but the controller pays for the tail
  all the time; with heavy tails the underspend becomes very large.

F must be known or estimated: if budget entries carry the time the spend
happened, the center can measure delays directly. If F is an estimate, make it
pessimistic (delays too long rather than too short). This covers independent
delays only. A delay that hits everything at once (a settlement batch, a
lagging budget system) is a stalled feed (§6): the stale-feed halt handles it,
and its tolerance must cover the normal spread of d.

**Token cap (a): unaffected.** Its rule counts tokens; d appears neither in the
rule nor in its proof.

**Token cap (b): needs a hard maximum.** It counts a grant as unresolved for its
window, then assumes the grant's spend is visible. The window must be the
longest delay that can ever occur; with a typical value, spend that arrives
later is spend the center no longer counts. With a long tail the window holds
budget for a long time, so (b) suits only delays with a hard, short bound.

Code: `Scenario.delay_dist` and `simulate(..., center_pmf=)` in Python;
`delay_pmf:` in `PacingController` (Elixir). Measured in
[probabilistic.md](probabilistic.md) §3, item 14, and in the token-cap results.

## 8. Simulation parameters

What has to be set to simulate each model, and what is optional. The columns
**P**, **(a)** and **(b)** show which model uses a parameter: the probabilistic
controller, token cap (a) and token cap (b). In the code, "required" means the
call fails without it; "core" means it has a default but defines the scenario
(the defaults are the example scenario of §1); "optional" means the default
switches the feature off or keeps the base model.

The parameters are grouped by the part of the simulation they belong to. The
brackets in each title name the Python object that holds them and the file
where it is defined.

| Section | Answers the question | In the code |
|---|---|---|
| 8.1 The world | What is the situation? Budget, campaign length, traffic, delays: what the center is given and cannot change | the fields of one `Scenario` object; `scn.center_view()` gives the center only the part it may know |
| 8.2 The center's controller | How does the center decide? The settings of the algorithm that chooses p | the arguments of a controller, such as `BayesRiskController(...)` |
| 8.3 The run | How is the experiment carried out? Which model (cap (a) or (b)), how many campaigns, and events injected on purpose, such as an outage | the arguments of `simulate` or `simulate_with_backstop` |
| 8.4 Traffic that varies | What changes for the seasonal models? All optional; without them traffic is constant | spread over the three above; the column "Where" says which |

In the tables: an empty cell in the P, (a) or (b) column means that model
ignores the parameter; a note next to ✓ gives a role or limit specific to that
model; a default of "none (…)" means the feature is off, and the brackets say
what happens instead.

### 8.1 The world (`Scenario`, `src/pacing/model.py`)

| Parameter | Symbol | Meaning | Default | Required? | P | (a) | (b) |
|---|---|---|---|---|---|---|---|
| `budget` | B | budget, in budget units | 5000 | core | ✓ | ✓ | ✓ |
| `horizon` | T | campaign length, s | 3600 | core | ✓ | ✓ | ✓ |
| `request_rate` | λ | bid requests per second (unknown to the center) | 1000 | core | ✓ | ✓ | ✓ |
| `realization` | r | share of bids that are charged (unknown to the center) | 0.1 | core | ✓ | ✓ | ✓ |
| `delay` | d | spend delay, s; with `delay_dist`, what the center assumes | 30 | core | ✓ | ✓ | ✓ (the grant window) |
| `step` | Δ | control period, s | 5 | core | ✓ | ✓ | ✓ |
| `unit_cost` | c | budget units per charged bid | 1 | optional | ✓ | ✓ | ✓ |
| `message_delay` | δ | true delay before p (and grants) reach the clients, s | 0 | optional | ✓ | ✓ | ✓ (must be < Δ) |
| `message_delay_max` | δ_max | the bound the center plans with, s | 0 | optional | ✓ | ✓ | ✓ (also the grant margin) |
| `message_age_limit` | | clients ignore a p older than this, s | none (no check) | optional | ✓ | ✓ | ✓ |
| `delay_dist` | F | random spend delay: `("exp", mean)`, `("uniform", lo, hi)`, `("lognormal", median, σ)` | none (fixed d) | optional | ✓ | ✓ | ✓ |

### 8.2 The center's controller (`src/pacing/controllers.py`)

The probabilistic model uses `BayesRiskController`. Under a token cap the
simulations use plain pacing, `NaiveController` ("cap only"), or
`BayesRiskController` for a cap with a risk check on top.

| Parameter | Meaning | Default | Required? | P | (a) | (b) |
|---|---|---|---|---|---|---|
| `view` | what the center knows: `scn.center_view()` | — | **required** | ✓ | ✓ | ✓ |
| `prior_mean` | rough guess of k = λ·r, used until spend is visible | — | **required** | ✓ | ✓ | ✓ |
| `eps` | allowed overspend probability ε | — | **required** for `BayesRiskController` | ✓ | only with a risk check | only with a risk check |
| `prior_shape` | weight of the guess, in spends | 2 | optional | ✓ | ✓ | ✓ |
| `endgame_time` | finish the planned spend this long before T, s | 0 | optional | ✓ | ✓ | ✓ |
| `flat_after_data` | drop the guess once spend is visible | true | optional | ✓ | ✓ | ✓ |
| `max_rise`, `rise_period` | p may grow at most × `max_rise` per `rise_period` (default d) | none | optional | ✓ | ✓ | ✓ |
| `eta`, `eta_period` | share of the unused risk budget per `eta_period` s | 0.5, 5 s | optional | ✓ | | |
| `max_silence` | clients' silence limit L: charge each p for max(Δ, L) | none | optional | ✓ | | |
| `broadcast_threshold` | send p only when it changes by more than this share | none (every period) | optional | ✓ | | |
| `known_rate` | the true k (an oracle, for comparison only) | none | optional | ✓ | | |
| `tails` | `"scipy"` or `"portable"` (the Elixir algorithms) | scipy | optional | ✓ | | |

### 8.3 The run (`simulate` for P, `simulate_with_backstop` for (a) and (b))

| Parameter | Meaning | Default | Required? | P | (a) | (b) |
|---|---|---|---|---|---|---|
| `scn`, `controller`, `n_runs` | the scenario, the controller, number of campaigns | — | **required** | ✓ | ✓ | ✓ |
| `seed` | random seed | 0 | optional | ✓ | ✓ | ✓ |
| `mode` | `"reports"` = (a), `"grants"` = (b) | reports | **required for (b)** | | ✓ | ✓ |
| `failure_delay` | d_f: delay of failure reports, s | 0 | optional | | ✓ | |
| `wallet`, `wallet_share` | tokens clients may hold; top-up share near the end | none (shared pool), 0.5 | optional | | ✓ | |
| `grant_share` **or** `grant_headroom` | grant rule: share s of the free budget, or h × expected demand | — | **required for (b): exactly one** | | | ✓ |
| `cap` | false = the same process without the cap | true | optional | | ✓ | ✓ |
| `keep` | number of trajectories to store (for charts) | 0 | optional | ✓ | | |
| `true_rates` | a different k for each run | none | optional | ✓ | | |
| `center_pmf` | delay distribution the center weights its exposure with | none (fixed d) | optional | ✓ | | |
| `halt_on_lag` | halt when the measured delivery delay exceeds δ_max + this, s | none | optional | ✓ | | |
| `link_outage`, `silence_limit` | (t0, t1) without messages; clients' silence limit (default 3Δ + δ_max) | none | optional | ✓ | ✓ | ✓ |
| `feed_outage`, `halt_on_stale_feed` | (t0, t1) with a frozen budget feed; halt tolerance, s | none | optional | ✓ | | |
| `trace` | record one run, step by step (for the Elixir tests) | none | optional | | ✓ | ✓ |
| `rate_noise` | random fluctuations of the request rate beyond Poisson: (cv, correlation seconds) | none (Poisson) | optional | ✓ | ✓ | ✓ |

### 8.4 Traffic that varies (seasonal models, `src/pacing/seasonal.py`)

All optional; without them traffic is constant. See
[seasonal/system.md](seasonal/system.md).

| Parameter | Where | Meaning | Default | P | (a) | (b) |
|---|---|---|---|---|---|---|
| `rate_profile` | run | the true traffic factor s of each step | none (s ≡ 1) | ✓ | ✓ | ✓ |
| `center_profile` | run | the profile the center weights its exposure with | none | ✓ | ✓ | ✓ |
| `target` | run | share of B to spend by each step, for the deviation measure | even in time | ✓ | ✓ | ✓ |
| `profile` | controller | the center's profile (same array as `center_profile`) | none | ✓ | ✓ | ✓ |
| `pace_goal` | controller | `"traffic"` (follow the traffic) or `"time"` (spend evenly) | traffic | ✓ | ✓ | ✓ |
| `risk_half_life` / `half_life` | controller | memory of the recent-rate estimate, s | none (1 h with `"time"` and no profile) | ✓ | ✓ (pacing only) | ✓ (pacing only) |
| `daily_amplitude`, `peak_hour`, `weekend`, `start_weekday` | profile | shape of s(t) | 0.6, 20 h, 0.7, Monday | ✓ | ✓ | ✓ |

The seasonal experiments use `month_scenario()`: B = 3.6 million, T = 30 days,
Δ = 60 s, d = 30 s.

### 8.5 Minimal calls

```python
from pacing.model import Scenario, simulate
from pacing.backstop import simulate_with_backstop
from pacing.controllers import BayesRiskController, NaiveController

scn = Scenario()                                   # the example scenario of §1

# probabilistic controller: needs prior_mean and eps
res = simulate(scn, BayesRiskController(scn.center_view(), prior_mean=100, eps=1e-3), n_runs=1000)

# token cap (a): plain pacing under the cap
res = simulate_with_backstop(scn, NaiveController(scn.center_view(), prior_mean=100), 1000,
                             failure_delay=5.0)

# token cap (b): mode and exactly one grant rule are required
res = simulate_with_backstop(scn, NaiveController(scn.center_view(), prior_mean=100), 1000,
                             mode="grants", grant_share=0.5)

print(res.summary(scn.budget))
```

### 8.6 Scenario files: running the models on your own measurements

Instead of writing Python, describe a real campaign in a TOML file and run

    uv run python -m pacing.scenario_file scenarios/example.toml

[scenarios/minimal.toml](../scenarios/minimal.toml) is the simplest possible
file: only the 8 required fields (budget, length, ε, request rate, charged share,
spend delay, control period, guess of k), with every other field at its default.
[scenarios/example.toml](../scenarios/example.toml) is the full template: every
field has a comment saying what it is, how to measure it, and whether it is
required. Note that scenario files plan for a lost link by default
(`plan_for_silence = true`), which costs about 0.1 point of underspend; set it to
false to reproduce the base results exactly. The results go to `results/scenarios/<name>.md`, one table with all
the chosen models side by side, plus notes on anything the simulator could not
do as asked.

| File section | What to provide | Required |
|---|---|---|
| `[campaign]` | budget, length, start day and hour, cost per bid, ε, pacing goal | budget, length; ε for the probabilistic model |
| `[traffic]` | request rate; profile: none, built-in, or 168 hourly values measured over a week | request rate |
| `[spend]` | charged share r; the spend delay, fixed or as measured percentiles (p50, p90, p99, p999, max); failure-report delay | r and the delay |
| `[infrastructure]` | control period; delivery delay of p and its planned bound; age limit; silence limit; outages to test | control period |
| `[center]` | the guess of k; what it believes about the profile and the delays; short memory, rise limit, end-game, halts | the guess |
| `[models]`, `[run]` | which models; wallet, grant share; runs, seed | nothing |

The loader refuses files that are incomplete or inconsistent, with a message
naming the field, and refuses combinations the simulator cannot do (a traffic
profile together with a delivery delay of p; token cap (b) with a delivery
delay of a whole period). Where it has to simplify, it says so in the report:
for example, token cap (b) uses the longest measured delay as its window, and a
center that cannot weight by the delay distribution assumes the longest delay.

**The bid-request stream.** The `[traffic]` section emulates the stream as a
product of optional parts; with none of them it is stable (constant Poisson).
Each part has a ready example in [scenarios/streams/](../scenarios/streams/):

| Pattern | Emulates | Fields | Known to the center? | Example |
|---|---|---|---|---|
| stable | constant Poisson traffic | `request_rate` only | — | `stable.toml` |
| periodic | daily and weekly cycles | `profile = "periodic"` (built-in shape) or `"hourly"` (168 measured values) | yes, from history (`profile_belief`) | `periodic.toml` |
| trend | steady growth or decline | `[traffic.trend]` start, end | yes, if forecast | `trend.toml` |
| steps | sudden lasting changes | `[traffic.steps]` at_hours, factor | yes, if planned; `steps.toml` shows a center that does not know | `steps.toml` |
| fluctuations | random bursts and lulls beyond Poisson | `[traffic.fluctuations]` cv, correlation_seconds | no, random | `bursty.toml` |

The rate is request_rate × periodic × trend × steps × fluctuations. With
`profile_belief = "true"` (the default when a profile, trend or steps are
given) the center knows the first three. Fluctuations break the probabilistic
model's Poisson assumption (§3); the report then carries a warning.

**Charts.** Add `--charts`, or set `charts = true` in `[run]`, to also draw
`results/scenarios/<name>.png`, linked from the report. It has four panels:
the bid-request stream (with one random sample if it fluctuates); one simulated
campaign per model, as spend ahead of or behind the target; the final spend of
every campaign minus B, where the right of 0 is overspend; and the share of
campaigns that overspent, against ε.

**Choosing the models.** In the `[models]` section, `run` lists the models to
simulate; leave it out to run all three:

```toml
[models]
run = ["probabilistic"]                    # only the probabilistic controller
# run = ["token_cap_a", "token_cap_b"]     # only the two caps
```

| Model | Its settings | Section |
|---|---|---|
| `probabilistic` | `eps` (required), `recent_memory_seconds`, `max_rise`, `endgame_seconds`, `broadcast_threshold`, `halt_on_stale_feed_seconds`, `halt_on_lag_seconds` | `[campaign]`, `[center]` |
| `token_cap_a` | `failure_report_seconds`, `wallet_tokens` | `[spend]`, `[models]` |
| `token_cap_b` | `grant_share` (default 0.5) | `[models]` |

Command-line options: `--runs N` overrides the number of runs (for a quick
check), `--out DIR` writes the report elsewhere.

**Comparing variants** of one campaign, for example with the center knowing the
traffic profile or not: make one file per variant, each with its own
`campaign.name`, and run each. The reports land side by side in
`results/scenarios/`.

**From Python**, for sweeps without editing files:

```python
from dataclasses import replace
from pacing.scenario_file import load, run_model, report

plan = load("scenarios/my-campaign.toml")        # checks the file first

res, msgs_per_hour = run_model(plan, "token_cap_a", runs=500)
print(res.summary(plan.scn.budget))

print(report(plan, {m: run_model(plan, m) for m in plan.models}))   # the same table as the command

for eps in (1e-2, 1e-3, 1e-4):                   # a sweep
    plan.eps = eps
    res, _ = run_model(plan, "probabilistic", runs=500)
    print(eps, res.summary(plan.scn.budget)["overspend_prob"])

plan.scn = replace(plan.scn, budget=2 * plan.scn.budget)   # change the scenario itself
```

