# System and common model

The system that all models share: who takes part, how a bid request turns into
spend, and the formal model. Each model of the center's strategy builds on this
file:

| Model | Overspend | Needs from clients |
|---|---|---|
| [Probabilistic controller](probabilistic.md) | probability ≤ ε | nothing (clients subscribe to p, §6) |
| [Token cap, version (a)](token-cap-a.md) | impossible | failure reports, tokens used (clients register, §6) |
| [Token cap, version (b)](token-cap-b.md) | impossible, if the delay d is a hard maximum | nothing (but clients must hold tokens and register, §6) |

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
| d | fixed delay between a spend and its appearance in the budget | yes |
| Δ | control period (how often the center may broadcast a new p) | yes |
| δ | delivery delay: a new p reaches the clients δ seconds after the center decides it (0 in the base model, answer C) | no |
| δ_max | upper bound on δ that the center plans with (§6, *Push or pull*) | yes (chosen) |
| ε | allowed probability of overspend | yes (chosen) |
| λ | rate of bid requests (steady Poisson) | **no** |
| r | probability that a token turns into budget spend (stable) | **no** |
| N | number of clients | **no** |
| k | k = λ·r, realised tokens (spends) per second at p = 1; actual spend rate is c·k·p units/s (the only unknown the center needs; see [IMPLEMENTATION.md §1](../IMPLEMENTATION.md#1-how-k-is-estimated)) | **no**, but estimated from the budget |
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
and it can learn k from its own budget.

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

## 6. How clients join: subscription and registration

Whether the center must know its clients depends on the model:

| Model | What the center must reach | Must the center know its clients? |
|---|---|---|
| Probabilistic controller | broadcast p to everyone | **no**: clients *subscribe* to p |
| Token cap (a) | hand out tokens; receive used and failed counts | **yes**: clients *register* and hold leases |
| Token cap (b) | hand out each period's grant | **yes**, to split the grant; nothing comes back |

The models' math does not change: k is still learned from the budget alone, and
N is still not needed (§5). What this section adds are the rules that keep the
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
  `message_delay_max:` in `PacingController`.

*What it costs and what ignoring it costs*
([probabilistic.md](probabilistic.md) §3, item 11). Planning with δ_max keeps
overspend within ε and costs up to one extra period of in-flight window for
each started period of δ_max. The cost is largest when δ is much smaller than
the rounded window, because some spend is then counted both as visible and as
in flight. Ignoring a delivery delay (δ_max = 0 while δ > 0) breaks the
guarantee quickly: in the default scenario with ε = 10⁻³, a 1 s delay doubles
the overspend rate, a 5 s delay makes it 2.3·10⁻² and a 10 s delay 1.3·10⁻¹.

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
  [IMPLEMENTATION.md](../IMPLEMENTATION.md) §1).
- **A random delay.** Use its upper quantile or model its distribution. The
  token cap (b) relies on d being a hard maximum.
- **A delay before clients receive a new p.** Add it to the in-flight window.
- **Traffic that varies more than Poisson** (§3). This is the assumption the
  probabilistic controller's ε guarantee is most sensitive to. The token caps do
  not depend on it.
