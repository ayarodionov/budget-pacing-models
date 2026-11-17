defmodule PacingController do
  @moduledoc """
  The proposed budget-pacing controller (Bayes estimate of k + even pacing +
  overspend-risk budget ε), as a pure functional state machine.

  A port of `BayesRiskController` in `src/pacing/controllers.py`, for one
  campaign (the Python version runs many simulated campaigns at once). The model
  is in `models/probabilistic.md`; how k is estimated and how the pace is
  calculated is in `models/IMPLEMENTATION.md`.

  ## Usage

  Once per control period (every `step` seconds) call `step/2` with the spend the
  budget shows now. It returns the p to use for the next period, whether p must be
  broadcast, and the new state:

      c = PacingController.new(budget: 5000, horizon: 3600, delay: 30, step: 5,
                               prior_mean: 100, eps: 1.0e-3)
      {p, send?, c} = PacingController.step(c, visible_spend)

  `step/2` keeps the exposure bookkeeping itself: it remembers the p of every
  period and splits it into the exposure whose spend is already visible (E) and
  the exposure still in flight (W). The delay is rounded up to whole periods,
  `ceil(delay / step)`, so the spend of period j is assumed visible from the start
  of period j + 1 + ceil(delay / step).

  If a new p reaches the clients only after a delivery delay of up to
  `message_delay_max` seconds (a broker, or clients that poll), the in-flight
  window grows by `ceil(message_delay_max / step)` periods. That keeps the ε
  guarantee; see `models/system.md` §6, *Push or pull*.

  `decide/5` is the same decision with E and W passed in explicitly, for callers
  that track exposure themselves.

  ## Units

  Budget and spend are in budget units. A realised token costs `unit_cost` (c)
  units; internally everything is counted in spends (budget / c, spend / c).
  `prior_mean` and `known_rate` are in spends per second at p = 1.

  ## Options for `new/1`

    * `:budget`, `:horizon`, `:delay`, `:step` - B, T, d, Δ (required)
    * `:prior_mean` - guess of k before any spend is visible (required)
    * `:eps` - allowed overspend probability for the whole campaign (required)
    * `:unit_cost` - c, budget units per realised token (default 1.0)
    * `:prior_shape` - weight of the guess, in pseudo-spends (default 2.0)
    * `:eta`, `:eta_period` - share of the unused risk budget that may be used
      per `eta_period` seconds (defaults 0.5 and 5.0)
    * `:endgame` - finish the planned spend this many seconds before T (default 0)
    * `:message_delay_max` - largest delay, in seconds, before a new p reaches the
      clients (default 0: immediately)
    * `:max_rise` - p may grow by at most this factor per `:rise_period` seconds
      (default: the spend delay), relative to the larger of the last p and the
      first p; decreases are not limited (default nil: no limit). A guard against
      a delay the center does not know about, which makes the controller mistake
      "no spend yet" for "low k" and push p up. 1.5 is a sensible value.
    * `:broadcast_threshold` - send p only when it changes by more than this
      fraction (default nil: send every period)
    * `:known_rate` - k if known exactly (oracle; default nil)
    * `:flat_after_data` - drop the guess once spend is visible (default true)
    * `:math` - `PacingMath` (default) or `PacingMathNif`
  """

  @bisect_iters 30

  @enforce_keys [:budget, :horizon, :delay, :step, :prior_mean, :eps]
  defstruct [
    :budget,
    :horizon,
    :delay,
    :step,
    :prior_mean,
    :eps,
    :eta_step,
    :known_rate,
    :broadcast_threshold,
    unit_cost: 1.0,
    prior_shape: 2.0,
    eta: 0.5,
    eta_period: 5.0,
    endgame: 0.0,
    message_delay_max: 0.0,
    max_rise: nil,
    rise_period: nil,
    p_first: nil,
    flat_after_data: true,
    math: PacingMath,
    # state
    risk_used: 0.0,
    p_last: 0.0,
    broadcasts: 0,
    period: 0,
    observed_exposure: 0.0,
    in_flight: []
  ]

  @type t :: %__MODULE__{}

  @doc "A controller for one campaign. See the module doc for the options."
  @spec new(keyword) :: t
  def new(opts) do
    c = struct!(__MODULE__, Enum.map(opts, fn {k, v} -> {k, to_float(k, v)} end))
    %{c | eta_step: 1.0 - :math.pow(1.0 - c.eta, c.step / c.eta_period)}
  end

  defp to_float(k, v) when k in [:math, :flat_after_data], do: v
  defp to_float(_k, nil), do: nil
  defp to_float(_k, v) when is_number(v), do: v * 1.0

  @doc """
  Number of whole control periods a decided p stays in flight: until its spend
  is visible, plus the delivery delay. Rounded up separately, because
  ceil((delay + message_delay_max) / step) can be one period too short.
  """
  @spec delay_periods(t) :: non_neg_integer
  def delay_periods(c),
    do: ceil(c.delay / c.step - 1.0e-9) + ceil(c.message_delay_max / c.step - 1.0e-9)

  @doc """
  One control period: decide p from the visible spend, using and updating the
  controller's own exposure bookkeeping. Returns `{p, send?, state}`.
  """
  @spec step(t, number) :: {float, boolean, t}
  def step(c, observed_spend) do
    w = Enum.sum(c.in_flight) * c.step
    {p, send?, c} = decide(c, c.period * c.step, observed_spend, c.observed_exposure, w)

    # p is now in flight; periods older than the delay have landed in the budget
    {landed, in_flight} = split_landed(c.in_flight ++ [p], delay_periods(c))

    c = %{
      c
      | period: c.period + 1,
        in_flight: in_flight,
        observed_exposure: c.observed_exposure + Enum.sum(landed) * c.step
    }

    {p, send?, c}
  end

  @doc """
  Correct the exposure of the period just decided by `step/2`, when not every bid
  could be made: pass the p that actually reached the budget, for example
  `TokenCap.Reports.effective_p/3` when a token cap blocked bids. Without this,
  blocked bids look like a lower k, and pacing would push p up to compensate.
  """
  @spec correct_exposure(t, number) :: t
  def correct_exposure(%{in_flight: []} = c, p_eff),
    do: %{c | observed_exposure: c.observed_exposure + (p_eff - c.p_last) * c.step}

  def correct_exposure(c, p_eff),
    do: %{c | in_flight: List.replace_at(c.in_flight, -1, p_eff * 1.0)}

  # keep the newest `d` periods in flight, the rest has landed
  defp split_landed(ps, d) do
    n = max(length(ps) - d, 0)
    Enum.split(ps, n)
  end

  @doc """
  Decide p for the period starting at time `t`.

    * `observed_spend` - spend visible in the budget (budget units)
    * `observed_exposure` - E, the integral of p over the period whose spend is visible
    * `inflight_exposure` - W, the integral of p over the period whose spend is not visible yet

  Returns `{p, send?, state}`. `send?` is false only when a broadcast threshold
  is set and the clients' current p is kept.
  """
  @spec decide(t, number, number, number, number) :: {float, boolean, t}
  def decide(c, t, observed_spend, observed_exposure, inflight_exposure) do
    {seen, remaining} = events(c, observed_spend)
    {alpha, beta, k_est} = posterior(c, seen, observed_exposure)
    k_est = c.known_rate || k_est
    w = inflight_exposure * 1.0
    dt = c.step
    tail = fn exposure -> tail(c, remaining, alpha, beta, exposure) end

    {p, c} = rise_cap(c, pace(c, t, remaining - k_est * w, k_est))
    q0 = tail.(w)
    allow = c.eta_step * max(c.eps - c.risk_used, 0.0)
    q1 = tail.(w + p * dt)

    {p, q1} =
      if q1 - q0 > allow do
        p = bisect(p, fn pp -> tail.(w + pp * dt) - q0 <= allow end)
        {p, tail.(w + p * dt)}
      else
        {p, q1}
      end

    {p, q1, send?} =
      case c.broadcast_threshold do
        nil ->
          {p, q1, true}

        thr ->
          last = c.p_last
          q_last = tail.(w + last * dt)

          if abs(p - last) <= thr * last and q_last - q0 <= allow,
            do: {last, q_last, false},
            else: {p, q1, p != last}
      end

    c = %{
      c
      | risk_used: c.risk_used + max(q1 - q0, 0.0),
        p_last: p,
        broadcasts: c.broadcasts + if(send?, do: 1, else: 0)
    }

    {p, send?, c}
  end

  # ------------------------------------------------------------------ internals

  # visible spend in budget units -> {spends seen, budget left in spends}
  defp events(c, observed_spend) do
    seen = Float.round(observed_spend / c.unit_cost)
    {seen, c.budget / c.unit_cost - seen}
  end

  defp posterior(c, seen, exposure) do
    {alpha, beta} =
      # with a message delay, spend can show up before its exposure is counted
      if c.flat_after_data and seen > 0 and exposure > 0 do
        {1.0 + seen, exposure * 1.0}
      else
        {c.prior_shape + seen, c.prior_shape / c.prior_mean + exposure}
      end

    {alpha, beta, alpha / beta}
  end

  # limit how fast p may grow; the first p (from the prior) is not limited
  defp rise_cap(%{max_rise: nil} = c, p), do: {p, c}
  defp rise_cap(%{p_first: nil} = c, p), do: {p, %{c | p_first: p}}

  defp rise_cap(c, p) do
    period = c.rise_period || if(c.delay > 0, do: c.delay, else: c.step)
    {min(p, max(c.p_last, c.p_first) * :math.pow(c.max_rise, c.step / period)), c}
  end

  defp pace(c, t, to_spend, k_est) do
    plan_left = max(c.horizon - c.endgame - t, c.step)
    (to_spend / (k_est * plan_left)) |> max(0.0) |> min(1.0)
  end

  defp tail(%{known_rate: nil} = c, remaining, alpha, beta, exposure),
    do: c.math.nb_tail(remaining, alpha, beta, exposure)

  defp tail(c, remaining, _alpha, _beta, exposure),
    do: c.math.poisson_tail(remaining, c.known_rate * exposure)

  # largest p in [0, p_hi] with ok?(p), assuming ok? is monotone and true at 0
  defp bisect(p_hi, ok?) do
    {lo, _hi} =
      Enum.reduce(1..@bisect_iters, {0.0, p_hi}, fn _, {lo, hi} ->
        mid = 0.5 * (lo + hi)
        if ok?.(mid), do: {mid, hi}, else: {lo, mid}
      end)

    lo
  end
end
