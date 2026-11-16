defmodule TokenCap do
  @moduledoc """
  The center's side of the token cap: a hard limit that makes overspend
  impossible, whatever the traffic, the delay or the controller does. Every bid
  needs a token, and the center never lets out more tokens than the budget can pay
  for. The pacing controller (`PacingController`) still chooses p; the cap only
  blocks bids when its allowance runs out.

  Two versions, as in `models/token-cap-a.md` and `models/token-cap-b.md`:

    * `TokenCap.Reports` - version (a). Clients report used tokens and failed
      bids. Allowance A = B/c + F_obs − U. Safe whatever the delay.
    * `TokenCap.Grants` - version (b). Clients report nothing. Each period the
      center grants tokens that expire at its end, at most
      A = (B − S_obs)/c − (tokens granted in the last d seconds). Safe only if the
      delay d is a hard maximum.

  Both count tokens: a realised token costs `unit_cost` (c) budget units, so the
  budget pays for floor(B/c) tokens. Ports of `simulate_with_backstop` in
  `src/pacing/backstop.py`, checked against its traces in `token_cap_test.exs`.

  Enforcement is on the clients: a client must not bid without a token. How
  tokens reach clients (one shared pool, per-client wallets, leases) is outside
  this module; it only says how many may be used.
  """

  @doc false
  def budget_tokens(budget, unit_cost), do: floor(budget / unit_cost + 1.0e-9)
end

defmodule TokenCap.Reports do
  @moduledoc """
  Token cap, version (a): clients report used tokens and failures.

      cap = TokenCap.Reports.new(budget: 5000)
      a = TokenCap.Reports.allowance(cap)          # tokens that may be used now
      cap = TokenCap.Reports.record_use(cap, n)     # clients used n tokens
      cap = TokenCap.Reports.record_failures(cap, m) # m bids reported as failed

  Invariant: tokens used U ≤ floor(B/c) + F_obs + R, so realised spend
  ≤ c·(U − F_total − R) ≤ c·(U − F_obs − R) ≤ B, where R counts tokens returned
  unused (`record_returned/2`). A late failure report only holds budget longer;
  a false one would break the guarantee.

  Clients should also report how many bids were blocked, so the controller's
  exposure can be corrected: see `PacingController.correct_exposure/2` and
  `effective_p/3`.
  """

  @enforce_keys [:budget_tokens]
  defstruct [:budget_tokens, used: 0, failures_reported: 0, returned: 0]

  @type t :: %__MODULE__{}

  @doc "Options: `:budget` (B, budget units), `:unit_cost` (c, default 1)."
  @spec new(keyword) :: t
  def new(opts) do
    %__MODULE__{
      budget_tokens:
        TokenCap.budget_tokens(Keyword.fetch!(opts, :budget), Keyword.get(opts, :unit_cost, 1))
    }
  end

  @doc "Tokens that may still be used: max(floor(B/c) + F_obs − U, 0)."
  @spec allowance(t) :: non_neg_integer
  def allowance(cap),
    do: max(cap.budget_tokens + cap.failures_reported + cap.returned - cap.used, 0)

  @doc "Clients used `n` tokens. Using more than `allowance/1` breaks the guarantee."
  @spec record_use(t, non_neg_integer) :: t
  def record_use(cap, n) when is_integer(n) and n >= 0, do: %{cap | used: cap.used + n}

  @doc "Clients reported `n` used tokens whose bids failed (were not charged)."
  @spec record_failures(t, non_neg_integer) :: t
  def record_failures(cap, n) when is_integer(n) and n >= 0,
    do: %{cap | failures_reported: cap.failures_reported + n}

  @doc """
  `n` tokens counted as used were returned unused (for example from a client's
  wallet when it leaves). Like a failure, they can no longer cost anything.
  """
  @spec record_returned(t, non_neg_integer) :: t
  def record_returned(cap, n) when is_integer(n) and n >= 0,
    do: %{cap | returned: cap.returned + n}

  @doc """
  The p that actually reached the budget in a period where `used` of `wanted`
  bids got a token: p · used / wanted. Pass it to
  `PacingController.correct_exposure/2`, so blocked bids do not bias k down.
  """
  @spec effective_p(float, non_neg_integer, non_neg_integer) :: float
  def effective_p(p, _used, 0), do: p
  def effective_p(p, used, wanted), do: p * used / wanted
end

defmodule TokenCap.Grants do
  @moduledoc """
  Token cap, version (b): no reports from clients. Each control period the center
  grants G tokens, valid only during that period; unused tokens expire.

      cap = TokenCap.Grants.new(budget: 5000, delay: 30, step: 5, share: 0.5)
      {g, cap} = TokenCap.Grants.grant(cap, visible_spend)   # once per period

  The grant never exceeds A = floor(B/c) − S_obs/c − (tokens granted in the last
  d seconds). The spend of every grant older than d is already in S_obs, and a
  grant in the window can spend at most what it was granted, so total spend
  ≤ B. This needs the delay d to be a hard maximum.

  Grant rules (one of them):

    * `share: s` - G = min(ceil(s·A), A). Needs no information; s = 0.5 is the
      recommended value.
    * `headroom: h` - G = min(ceil(h · expected bids), A), with the expected bids
      λ·p·Δ passed to `grant/3`. Needs the bid demand, for example from usage
      counts.

  Like `PacingController`, the delay is rounded up to whole periods:
  ceil(delay / step).
  """

  @enforce_keys [:budget_tokens, :delay_periods, :unit_cost]
  defstruct [:budget_tokens, :delay_periods, :unit_cost, :share, :headroom, window: []]

  @type t :: %__MODULE__{}

  @doc """
  Options: `:budget`, `:delay`, `:step` (required), `:unit_cost` (default 1), and
  exactly one of `:share` or `:headroom`.
  """
  @spec new(keyword) :: t
  def new(opts) do
    share = Keyword.get(opts, :share)
    headroom = Keyword.get(opts, :headroom)

    if is_nil(share) == is_nil(headroom),
      do: raise(ArgumentError, "give exactly one of :share or :headroom")

    unit_cost = Keyword.get(opts, :unit_cost, 1) * 1.0
    delay = Keyword.fetch!(opts, :delay)
    step = Keyword.fetch!(opts, :step)

    %__MODULE__{
      budget_tokens: TokenCap.budget_tokens(Keyword.fetch!(opts, :budget), unit_cost),
      delay_periods: ceil(delay / step - 1.0e-9),
      unit_cost: unit_cost,
      share: share,
      headroom: headroom
    }
  end

  @doc "A = max(floor(B/c) − S_obs/c − tokens granted in the last d seconds, 0)."
  @spec allowance(t, number) :: non_neg_integer
  def allowance(cap, observed_spend) do
    seen = round(observed_spend / cap.unit_cost)
    max(cap.budget_tokens - seen - Enum.sum(cap.window), 0)
  end

  @doc """
  The grant for this period, from the spend the budget shows now. Call once per
  period. Option `expected_bids` (λ·p·Δ) is required with the `headroom` rule.
  Returns `{tokens granted, state}`.
  """
  @spec grant(t, number, keyword) :: {non_neg_integer, t}
  def grant(cap, observed_spend, opts \\ []) do
    a = allowance(cap, observed_spend)

    g =
      if cap.share,
        do: ceil(cap.share * a),
        else: ceil(cap.headroom * Keyword.fetch!(opts, :expected_bids))

    g = min(g, a)
    # the grant joins the window; grants older than d have landed in the budget
    window = cap.window ++ [g]
    {g, %{cap | window: Enum.take(window, -cap.delay_periods)}}
  end
end
