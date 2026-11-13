defmodule PacingMathNif do
  @moduledoc """
  NIF (C) version of `PacingMath`: same functions, same algorithms, same results
  to ~1e-15. Build the shared library first with `make -C elixir`.

  Each call is pure and takes microseconds, so it runs on a normal scheduler.
  """

  @on_load :load_nif

  @doc false
  def load_nif do
    path = System.get_env("PACING_MATH_NIF") || Path.join(__DIR__, "priv/pacing_math_nif")
    :erlang.load_nif(String.to_charlist(path), 0)
  end

  @spec lgamma(number) :: float
  def lgamma(_x), do: :erlang.nif_error(:nif_not_loaded)

  @spec betainc(number, number, number) :: float
  def betainc(_a, _b, _x), do: :erlang.nif_error(:nif_not_loaded)

  @spec gammainc_lower(number, number) :: float
  def gammainc_lower(_a, _x), do: :erlang.nif_error(:nif_not_loaded)

  @spec poisson_tail(number, number) :: float
  def poisson_tail(_m, _mu), do: :erlang.nif_error(:nif_not_loaded)

  @spec nb_tail(number, number, number, number) :: float
  def nb_tail(_m, _alpha, _beta, _exposure), do: :erlang.nif_error(:nif_not_loaded)
end
