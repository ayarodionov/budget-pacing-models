defmodule PacingMath do
  @moduledoc """
  Special functions for the budget-pacing controller, using only Erlang's `:math`
  (exp, log, sin) and arithmetic.

  A line-by-line port of `src/pacing/specfun.py` (same constants and stopping
  rules), checked against scipy in `pacing_math_test.exs`.

    * `lgamma/1`         - ln Gamma(x), Lanczos approximation (g = 7, 9 terms)
    * `betainc/3`        - regularised incomplete beta I_x(a, b), continued fraction
    * `gammainc_lower/2` - regularised lower incomplete gamma P(a, x)
    * `nb_tail/4`        - P(X > m), X | k ~ Poisson(k * exposure), k ~ Gamma(alpha, rate beta)
    * `poisson_tail/2`   - P(X > m), X ~ Poisson(mu)

  `nb_tail/4` is what the controller's risk check calls (MODEL.md, section 3).
  """

  @eps 1.0e-15
  @fpmin 1.0e-300
  @max_iter 10_000

  @lanczos_g 7.0
  @lanczos_coef [
    0.99999999999980993,
    676.5203681218851,
    -1259.1392167224028,
    771.32342877765313,
    -176.61502916214059,
    12.507343278686905,
    -0.13857109526572012,
    9.9843695780195716e-6,
    1.5056327351493116e-7
  ]
  @half_log_2pi 0.5 * :math.log(2.0 * :math.pi())

  @doc "ln(1 + x), accurate for small x (`:math` has no log1p)."
  @spec log1p(float) :: float
  def log1p(x) do
    u = 1.0 + x
    if u == 1.0, do: x * 1.0, else: :math.log(u) * x / (u - 1.0)
  end

  @doc "ln Gamma(x) for x > 0."
  @spec lgamma(number) :: float
  def lgamma(x) when x < 0.5 do
    :math.log(:math.pi() / :math.sin(:math.pi() * x)) - lgamma(1.0 - x)
  end

  def lgamma(x) do
    x = x - 1.0
    [c0 | rest] = @lanczos_coef

    s =
      rest
      |> Enum.with_index(1)
      |> Enum.reduce(c0, fn {c, i}, acc -> acc + c / (x + i) end)

    t = x + @lanczos_g + 0.5
    @half_log_2pi + (x + 0.5) * :math.log(t) - t + :math.log(s)
  end

  @doc "Regularised incomplete beta I_x(a, b) for a, b > 0 and 0 <= x <= 1."
  @spec betainc(number, number, number) :: float
  def betainc(_a, _b, x) when x <= 0, do: 0.0
  def betainc(_a, _b, x) when x >= 1, do: 1.0

  def betainc(a, b, x) do
    log_front =
      lgamma(a + b) - lgamma(a) - lgamma(b) + a * :math.log(x) + b * log1p(-x)

    if x < (a + 1.0) / (a + b + 2.0) do
      :math.exp(log_front) * betacf(a, b, x) / a
    else
      1.0 - :math.exp(log_front) * betacf(b, a, 1.0 - x) / b
    end
  end

  @doc "Regularised lower incomplete gamma P(a, x) for a > 0 and x >= 0."
  @spec gammainc_lower(number, number) :: float
  def gammainc_lower(_a, x) when x <= 0, do: 0.0

  def gammainc_lower(a, x) do
    log_front = -x + a * :math.log(x) - lgamma(a)

    if x < a + 1.0 do
      gser(x, a * 1.0, 1.0 / a, 1.0 / a, 0) * :math.exp(log_front)
    else
      b = x + 1.0 - a
      d = 1.0 / b
      1.0 - :math.exp(log_front) * gcf(1, a, b, 1.0 / @fpmin, d, d)
    end
  end

  @doc "P(X > m) for X ~ Poisson(mu)."
  @spec poisson_tail(number, number) :: float
  def poisson_tail(m, mu) do
    m = :math.floor(m * 1.0)

    cond do
      m < 0 -> 1.0
      mu <= 0 -> 0.0
      true -> gammainc_lower(m + 1.0, mu)
    end
  end

  @doc """
  P(X > m) where X | k ~ Poisson(k * exposure) and k ~ Gamma(alpha, rate beta)
  (negative binomial): I_{exposure/(beta+exposure)}(m + 1, alpha).
  """
  @spec nb_tail(number, number, number, number) :: float
  def nb_tail(m, alpha, beta, exposure) do
    m = :math.floor(m * 1.0)

    cond do
      m < 0 -> 1.0
      exposure <= 0 -> 0.0
      true -> betainc(m + 1.0, alpha, exposure / (beta + exposure))
    end
  end

  # ------------------------------------------------------------------ internals

  defp guard(v) when abs(v) > @fpmin, do: v
  defp guard(_v), do: @fpmin

  # Continued fraction for the incomplete beta, modified Lentz's method.
  defp betacf(a, b, x) do
    qab = a + b
    d = 1.0 / guard(1.0 - qab * x / (a + 1.0))
    betacf_loop(1, a, b, x, 1.0, d, d)
  end

  defp betacf_loop(m, a, b, x, _c, _d, _h) when m > @max_iter do
    raise ArithmeticError, message: "betacf did not converge: a=#{a} b=#{b} x=#{x}"
  end

  defp betacf_loop(m, a, b, x, c, d, h) do
    qab = a + b
    m2 = 2 * m
    aa = m * (b - m) * x / ((a - 1.0 + m2) * (a + m2))
    d = 1.0 / guard(1.0 + aa * d)
    c = guard(1.0 + aa / c)
    h = h * d * c
    aa = -(a + m) * (qab + m) * x / ((a + m2) * (a + 1.0 + m2))
    d = 1.0 / guard(1.0 + aa * d)
    c = guard(1.0 + aa / c)
    delta = d * c
    h = h * delta

    if abs(delta - 1.0) < @eps,
      do: h,
      else: betacf_loop(m + 1, a, b, x, c, d, h)
  end

  # Series for P(a, x), used when x < a + 1.
  defp gser(x, _ap, _term, _total, n) when n >= @max_iter do
    raise ArithmeticError, message: "gamma series did not converge: x=#{x}"
  end

  defp gser(x, ap, term, total, n) do
    ap = ap + 1.0
    term = term * x / ap
    total = total + term

    if abs(term) < abs(total) * @eps,
      do: total,
      else: gser(x, ap, term, total, n + 1)
  end

  # Continued fraction for Q(a, x) = 1 - P(a, x), used when x >= a + 1.
  defp gcf(i, a, _b, _c, _d, _h) when i > @max_iter do
    raise ArithmeticError, message: "gamma continued fraction did not converge: a=#{a}"
  end

  defp gcf(i, a, b, c, d, h) do
    an = -i * (i - a)
    b = b + 2.0
    d = 1.0 / guard(an * d + b)
    c = guard(b + an / c)
    delta = d * c
    h = h * delta

    if abs(delta - 1.0) < @eps,
      do: h,
      else: gcf(i + 1, a, b, c, d, h)
  end
end
