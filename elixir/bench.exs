# Pure Elixir vs NIF: time per nb_tail call, and per controller decision.
# Run: make -C elixir bench
defmodule Bench do
  def per_call(fun, n) do
    fun.()
    {us, _} = :timer.tc(fn -> Enum.each(1..n, fn _ -> fun.() end) end)
    us * 1000 / n
  end

  # one controller decision: q0, q1 and a 30-step bisection on p (~32 tail evaluations)
  def decision(mod, remaining, alpha, beta, w, dt) do
    q0 = mod.nb_tail(remaining, alpha, beta, w)
    allow = 1.0e-4

    Enum.reduce(1..30, {0.0, 1.0}, fn _, {lo, hi} ->
      mid = (lo + hi) / 2

      if mod.nb_tail(remaining, alpha, beta, w + mid * dt) - q0 <= allow,
        do: {mid, hi},
        else: {lo, mid}
    end)
  end
end

cases = [
  {"end-game tail (m=60, alpha=5001, beta=50, V=0.5)", {60, 5001.0, 50.0, 0.5}},
  {"mid-campaign (m=2500, alpha=2501, beta=25, V=0.5)", {2500, 2501.0, 25.0, 0.5}},
  {"large budget (m=2000, alpha=50001, beta=500, V=20)", {2000, 50001.0, 500.0, 20.0}}
]

n = 200_000
IO.puts("nb_tail, ns per call (#{n} calls)")

for {label, {m, a, b, v}} <- cases do
  e = Bench.per_call(fn -> PacingMath.nb_tail(m, a, b, v) end, n)
  c = Bench.per_call(fn -> PacingMathNif.nb_tail(m, a, b, v) end, n)

  IO.puts(
    "  #{String.pad_trailing(label, 52)} Elixir #{round(e)}  NIF #{round(c)}  (#{Float.round(e / c, 1)}x)"
  )
end

nd = 20_000
e = Bench.per_call(fn -> Bench.decision(PacingMath, 60, 5001.0, 50.0, 0.42, 0.07) end, nd)
c = Bench.per_call(fn -> Bench.decision(PacingMathNif, 60, 5001.0, 50.0, 0.42, 0.07) end, nd)

IO.puts(
  "one controller decision (31 tail evaluations), µs: Elixir #{Float.round(e / 1000, 1)}  NIF #{Float.round(c / 1000, 1)}"
)

# The real controller over a whole simulated campaign (default scenario, 720 periods),
# replaying the visible spend recorded from the Python controller.
g = "controller_golden.json" |> File.read!() |> JSON.decode!() |> Map.fetch!("cases") |> hd()
opts = Enum.map(g["opts"], fn {k, v} -> {String.to_existing_atom(k), v} end)
spends = Enum.map(g["steps"], fn [_, s | _] -> s end)

campaign = fn math ->
  Enum.reduce(spends, PacingController.new([math: math] ++ opts), fn s, c ->
    {_, _, c} = PacingController.step(c, s)
    c
  end)
end

for math <- [PacingMath, PacingMathNif] do
  campaign.(math)
  {us, _} = :timer.tc(fn -> for _ <- 1..20, do: campaign.(math) end)

  IO.puts(
    "PacingController.step/2 over a campaign, #{inspect(math)}: " <>
      "#{Float.round(us / 20 / length(spends), 1)} µs per period"
  )
end

# PacingClient: one bid decision through a call to the client process, against the
# fast path (a Handle read by the bidding process itself). Broadcast mode, p = 1.
defmodule BenchP do
  def step(p, _), do: {p, true, p}
end

{:ok, center} =
  PacingCenter.start_link(campaign: :bench, controller: {BenchP, 1.0}, visible_spend: fn -> 0 end,
                          step_ms: 1_000)

{:ok, client} = PacingClient.start_link(center: center, campaign: :bench)
Process.sleep(20)
h = PacingClient.handle(client)
nb = 200_000
call_ns = Bench.per_call(fn -> PacingClient.bid?(client) end, nb)
fast_ns = Bench.per_call(fn -> PacingClient.bid?(h) end, nb)
IO.puts("PacingClient.bid?/1, one bidder, ns per bid: call #{round(call_ns)}  handle #{round(fast_ns)}")

throughput = fn bid, procs ->
  per = div(nb, procs)
  {us, _} =
    :timer.tc(fn ->
      1..procs
      |> Task.async_stream(fn _ -> for _ <- 1..per, do: bid.() end, max_concurrency: procs, timeout: :infinity)
      |> Stream.run()
    end)

  round(per * procs / us)
end

procs = System.schedulers_online()
IO.puts("PacingClient.bid?/1, #{procs} bidders at once, million bids/s: " <>
          "call #{throughput.(fn -> PacingClient.bid?(client) end, procs)}  " <>
          "handle #{throughput.(fn -> PacingClient.bid?(h) end, procs)}")
