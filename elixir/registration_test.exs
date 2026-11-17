# PacingCenter and PacingClient: subscription, registration, leases (models/system.md §6).
# Run: make -C elixir test
ExUnit.start()

defmodule FixedP do
  @moduledoc "A controller that always answers the same p (to test the plumbing)."
  def step(p, _visible_spend), do: {p, true, p}
end

defmodule RegistrationTest do
  use ExUnit.Case, async: true

  alias TokenCap.Reports

  # ------------------------------------------------------------------ helpers

  defp clock do
    {:ok, a} = Agent.start_link(fn -> 0 end)
    {fn -> Agent.get(a, & &1) end, fn ms -> Agent.update(a, &(&1 + ms)) end}
  end

  defp center(mode, opts) do
    {now, _} = Keyword.fetch!(opts, :clock_pair)
    campaign = make_ref()

    base = [
      campaign: campaign,
      mode: mode,
      controller: {FixedP, Keyword.get(opts, :p, 1.0)},
      visible_spend: Keyword.get(opts, :visible_spend, fn -> 0 end),
      step_ms: 5,
      tick: :manual,
      lease_ttl_ms: 15,
      clock: now,
      authenticate: fn _id, cred -> cred == :secret end
    ]

    {:ok, c} =
      PacingCenter.start_link(
        Keyword.merge(base, Keyword.drop(opts, [:clock_pair, :p, :visible_spend]))
      )

    {c, campaign}
  end

  defp client(c, campaign, id, opts) do
    {now, _} = Keyword.fetch!(opts, :clock_pair)

    {:ok, pid} =
      PacingClient.start_link(
        Keyword.merge(
          [
            center: c,
            campaign: campaign,
            client_id: id,
            credentials: :secret,
            clock: now,
            coin: fn -> 0.0 end,
            heartbeat_ms: :manual
          ],
          Keyword.drop(opts, [:clock_pair])
        )
      )

    pid
  end

  defp tick(c) do
    :ok = PacingCenter.tick(c)
    # let clients process the broadcast before the next call
    Process.sleep(1)
  end

  defp bids(client, n), do: for(_ <- 1..n, do: PacingClient.bid?(client))

  defp allowance(c), do: Reports.allowance(PacingCenter.info(c).cap)

  # ------------------------------------------------------------------ broadcast

  test "broadcast: subscribers get p, ignore old messages, stop on silence" do
    cp = {_, advance} = clock()
    {c, campaign} = center(:broadcast, clock_pair: cp)
    a = client(c, campaign, nil, clock_pair: cp)
    b = client(c, campaign, nil, clock_pair: cp)

    # before the first broadcast p = 0
    refute PacingClient.bid?(a)
    tick(c)
    assert PacingClient.bid?(a) and PacingClient.bid?(b)

    # a delayed old message does not overwrite the newer p
    %{epoch: e, seq: s} = PacingClient.info(a)
    send(a, {:pacing_p, campaign, e, s - 1, 0.0, System.os_time(:millisecond)})
    assert PacingClient.bid?(a)

    # silence for longer than 3 periods: the client stops by itself
    advance.(16)
    refute PacingClient.bid?(a)
    tick(c)
    assert PacingClient.bid?(a)
  end

  test "broadcast: the center does not register clients" do
    cp = clock()
    {c, _} = center(:broadcast, clock_pair: cp)
    assert {:error, :no_registration_in_broadcast_mode} = PacingCenter.register(c, "x", :secret)
  end

  test "broadcast with real timers" do
    {:ok, c} =
      PacingCenter.start_link(
        campaign: :timers,
        controller: {FixedP, 0.5},
        visible_spend: fn -> 0 end,
        step_ms: 10
      )

    {:ok, a} = PacingClient.start_link(center: c, campaign: :timers)
    Process.sleep(50)
    assert PacingClient.info(a).p == 0.5
  end

  # ------------------------------------------------------------------ (a) reports

  test "(a) unauthenticated clients cannot register" do
    cp = clock()
    {c, _} = center(:reports, clock_pair: cp, cap: [budget: 10])
    assert {:error, :unauthenticated} = PacingCenter.register(c, "intruder", :guess)
  end

  test "(a) wallets draw from the budget; failure reports free it again" do
    cp = clock()
    {c, campaign} = center(:reports, clock_pair: cp, cap: [budget: 10])
    a = client(c, campaign, "a", clock_pair: cp, wallet_size: 4)
    tick(c)

    # 10 tokens in wallets of 4, 4, 2; then blocked
    assert Enum.all?(bids(a, 10))
    refute PacingClient.bid?(a)
    assert allowance(c) == 0

    # 3 of those bids were not charged
    :ok = PacingClient.report_failure(a, 3)
    :ok = PacingClient.heartbeat(a)
    assert allowance(c) == 3
    assert Enum.all?(bids(a, 3))
    refute PacingClient.bid?(a)
  end

  test "(a) leaving returns the unused tokens" do
    cp = clock()
    {c, campaign} = center(:reports, clock_pair: cp, cap: [budget: 10])
    a = client(c, campaign, "a", clock_pair: cp, wallet_size: 4)
    tick(c)
    assert PacingClient.bid?(a)
    assert allowance(c) == 6
    :ok = PacingClient.leave(a)
    assert allowance(c) == 9
  end

  test "(a) a lost lease keeps its tokens counted until the client settles it" do
    cp = {_, advance} = clock()
    {c, campaign} = center(:reports, clock_pair: cp, cap: [budget: 10])
    a = client(c, campaign, "a", clock_pair: cp, wallet_size: 4)
    tick(c)
    assert PacingClient.bid?(a)

    # no heartbeat for longer than the ttl: the center marks the lease lost...
    advance.(16)
    tick(c)
    assert map_size(PacingCenter.info(c).lost) == 1
    # ...but the 3 tokens still in the wallet stay counted: the client may be cut off and bidding
    assert allowance(c) == 6

    # the client notices its own lease expired, drops the wallet and registers again,
    # reporting the 3 unused tokens; then it gets a new wallet of 4
    assert PacingClient.bid?(a)
    info = PacingCenter.info(c)
    assert info.lost == %{}
    assert map_size(info.leases) == 1
    assert allowance(c) == 9 - 4
  end

  # ------------------------------------------------------------------ (b) grants

  test "(b) the grant is split evenly among live leases and expires with its period" do
    cp = {_, advance} = clock()

    {c, campaign} =
      center(:grants,
        clock_pair: cp,
        grant_margin_ms: 1,
        cap: [budget: 100, delay: 0.010, step: 0.005, share: 0.5]
      )

    a = client(c, campaign, "a", clock_pair: cp)
    b = client(c, campaign, "b", clock_pair: cp)
    tick(c)

    # grant ceil(0.5 * 100) = 50, 25 each
    assert Enum.all?(bids(a, 25))
    refute PacingClient.bid?(a)

    # b's grant is valid for step - margin = 4 ms only
    advance.(4)
    refute PacingClient.bid?(b)
  end

  test "(b) a client that stops heartbeating gets no grant" do
    cp = {_, advance} = clock()

    {c, campaign} =
      center(:grants, clock_pair: cp, cap: [budget: 100, delay: 0.010, step: 0.005, share: 0.5])

    a = client(c, campaign, "a", clock_pair: cp)
    b = client(c, campaign, "b", clock_pair: cp)

    advance.(16)
    :ok = PacingClient.heartbeat(a)
    tick(c)
    assert Map.keys(PacingCenter.info(c).leases) |> length() == 1
    assert Enum.all?(bids(a, 50))
    refute PacingClient.bid?(b)
  end

  # ------------------------------------------------------------------ safety

  test "(a) random joins, crashes, silence and late or missing reports never overspend" do
    :rand.seed(:exsss, {7, 8, 9})
    budget = 60
    cp = {_, advance} = clock()
    {c, campaign} = center(:reports, clock_pair: cp, cap: [budget: budget])
    tick(c)
    realised = :counters.new(1, [])

    clients =
      for i <- 1..6, do: {i, client(c, campaign, "c#{i}", clock_pair: cp, wallet_size: 3)}

    Enum.reduce(1..3000, Map.new(clients), fn _, live ->
      {i, pid} = Enum.random(live)

      case :rand.uniform(10) do
        n when n <= 6 ->
          # a bid; charged with probability 0.3, otherwise it failed, and the
          # failure is reported only sometimes (a client may die first)
          if PacingClient.bid?(pid) do
            if :rand.uniform() < 0.3,
              do: :counters.add(realised, 1, 1),
              else: if(:rand.uniform() < 0.7, do: PacingClient.report_failure(pid))
          end

          live

        7 ->
          PacingClient.heartbeat(pid)
          live

        8 ->
          advance.(:rand.uniform(10))
          tick(c)
          live

        9 ->
          # crash without telling anyone, and a new client joins
          Process.unlink(pid)
          Process.exit(pid, :kill)

          Map.put(
            live,
            i,
            client(c, campaign, "c#{i}-#{System.unique_integer([:positive])}",
              clock_pair: cp,
              wallet_size: 3
            )
          )

        10 ->
          live
      end
    end)

    spent = :counters.get(realised, 1)
    assert spent <= budget
    # the test is not trivial: failure reports and returned tokens let more tokens
    # than the budget be handed out, and many leases were lost on the way
    info = PacingCenter.info(c)
    assert info.cap.used > budget
    assert map_size(info.lost) > 10
    # what is still held: tokens of lost leases and failures never reported. This
    # is the price of the rule that a lost lease keeps its tokens counted.
    assert spent > 0
  end

  # ------------------------------------------------------------------ fast path (hybrid)

  defp relay(c, campaign, cp) do
    {now, _} = cp
    table = :"relay_#{System.unique_integer([:positive])}"
    {:ok, r} = PacingRelay.start_link(center: c, campaign: campaign, table: table, clock: now)
    {r, table}
  end

  test "fast path: a handle bids without any message to the client process" do
    cp = clock()
    {c, campaign} = center(:broadcast, clock_pair: cp)
    a = client(c, campaign, nil, clock_pair: cp)
    tick(c)
    h = PacingClient.handle(a)

    # a suspended process cannot answer a call, so these bids sent no message
    :sys.suspend(a)
    assert Enum.all?(for _ <- 1..1000, do: PacingClient.bid?(h))
    :sys.resume(a)
    assert PacingClient.info(a).bids == 1000
  end

  test "fast path, (a): a wallet is used without messages; the client is called only to top up" do
    cp = clock()
    {c, campaign} = center(:reports, clock_pair: cp, cap: [budget: 100])
    a = client(c, campaign, "a", clock_pair: cp, wallet_size: 4)
    tick(c)
    h = PacingClient.handle(a)
    # the first bid tops up the wallet (slow path), 3 tokens remain
    assert PacingClient.bid?(h)

    :sys.suspend(a)
    assert Enum.all?(for _ <- 1..3, do: PacingClient.bid?(h))
    :sys.resume(a)

    # wallet empty: the next bid goes through the client for a top-up
    assert PacingClient.bid?(h)
    assert allowance(c) == 100 - 8
  end

  test "fast path, (a): when the budget is used up, bids are refused without calls" do
    cp = clock()
    {c, campaign} = center(:reports, clock_pair: cp, cap: [budget: 3])
    a = client(c, campaign, "a", clock_pair: cp, wallet_size: 4)
    tick(c)
    h = PacingClient.handle(a)
    assert Enum.all?(for _ <- 1..3, do: PacingClient.bid?(h))
    # this one asks the center, gets nothing, and sets a retry time
    refute PacingClient.bid?(h)
    :sys.suspend(a)
    refute PacingClient.bid?(h)
    :sys.resume(a)
  end

  test "fast path, (a): many processes bidding at once never take more than they were handed" do
    cp = clock()
    {c, campaign} = center(:reports, clock_pair: cp, cap: [budget: 1000])
    a = client(c, campaign, "a", clock_pair: cp, wallet_size: 7)
    tick(c)
    h = PacingClient.handle(a)

    yes =
      1..20
      |> Task.async_stream(fn _ -> Enum.count(1..200, fn _ -> PacingClient.bid?(h) end) end)
      |> Enum.reduce(0, fn {:ok, n}, acc -> acc + n end)

    assert yes == 1000
    info = PacingClient.info(a)
    assert info.bids == 1000
    assert info.tokens == 0
    assert allowance(c) == 0
  end

  test "fast path, (b): concurrent bidders use exactly the grant" do
    cp = clock()

    {c, campaign} =
      center(:grants, clock_pair: cp, cap: [budget: 200, delay: 0.010, step: 0.005, share: 0.5])

    a = client(c, campaign, "a", clock_pair: cp)
    tick(c)
    h = PacingClient.handle(a)

    yes =
      1..10
      |> Task.async_stream(fn _ -> Enum.count(1..50, fn _ -> PacingClient.bid?(h) end) end)
      |> Enum.reduce(0, fn {:ok, n}, acc -> acc + n end)

    assert yes == 100
  end

  test "relay: one subscriber per node, clients read p from its table" do
    cp = {_, advance} = clock()
    {c, campaign} = center(:broadcast, clock_pair: cp)
    {r, table} = relay(c, campaign, cp)
    clients = for _ <- 1..3, do: client(c, campaign, nil, clock_pair: cp, relay: table)

    # only the relay is subscribed, so the center sends one message per period
    assert :pg.get_members(PacingCenter.PG, {:pacing, campaign}) == [r]

    tick(c)
    handles = Enum.map(clients, &PacingClient.handle/1)
    assert Enum.all?(handles, &PacingClient.bid?/1)

    # an old message to the relay changes nothing
    [{_, e, s, _, _}] = :ets.lookup(table, campaign)
    send(r, {:pacing_p, campaign, e, s - 1, 0.0, System.os_time(:millisecond)})
    assert Enum.all?(handles, &PacingClient.bid?/1)

    # silence: every client on the node stops
    advance.(16)
    refute Enum.any?(handles, &PacingClient.bid?/1)
    tick(c)
    assert Enum.all?(handles, &PacingClient.bid?/1)

    # the relay dies: its table goes with it, and nobody bids
    Process.unlink(r)
    Process.exit(r, :kill)
    Process.sleep(5)
    refute Enum.any?(handles, &PacingClient.bid?/1)
  end

  # ------------------------------------------------------------------ delivery delay (δ_max)

  defp ctl_opts(extra \\ []),
    do:
      Keyword.merge(
        [budget: 100, horizon: 60, delay: 10, step: 5, prior_mean: 10, eps: 0.01],
        extra
      )

  defp start_center(opts) do
    Process.flag(:trap_exit, true)

    PacingCenter.start_link(
      Keyword.merge(
        [
          campaign: make_ref(),
          visible_spend: fn -> 0 end,
          step_ms: 5_000,
          tick: :manual,
          authenticate: fn _, _ -> true end
        ],
        opts
      )
    )
  end

  test "δ_max: message_delay_max_ms widens the controller's in-flight window" do
    {:ok, c} = start_center(controller: ctl_opts(), message_delay_max_ms: 2_000)
    ctl = PacingCenter.info(c).ctl
    assert ctl.message_delay_max == 2.0
    # ceil(10 / 5) + ceil(2 / 5) = 3 periods instead of 2
    assert PacingController.delay_periods(ctl) == 3
  end

  test "δ_max: inconsistent settings are refused at start" do
    assert {:error, {%ArgumentError{}, _}} =
             start_center(
               controller: ctl_opts(message_delay_max: 1.0),
               message_delay_max_ms: 2_000
             )

    assert {:error, {%ArgumentError{}, _}} = start_center(controller: ctl_opts(step: 1))
  end

  test "δ_max, (b): the grant margin defaults to δ_max; a smaller one or δ_max >= a period is refused" do
    cap = [budget: 100, delay: 10, step: 5, share: 0.5]

    {:ok, c} =
      start_center(
        mode: :grants,
        controller: {FixedP, 1.0},
        cap: cap,
        message_delay_max_ms: 2_000
      )

    {:ok, _} = PacingCenter.register(c, "me", :any)
    :ok = PacingCenter.tick(c)
    assert_receive {:pacing_grant, _, _, _, 50, valid_ms, _sent_at}
    assert valid_ms == 5_000 - 2_000

    assert {:error, {%ArgumentError{}, _}} =
             start_center(
               mode: :grants,
               controller: {FixedP, 1.0},
               cap: cap,
               message_delay_max_ms: 2_000,
               grant_margin_ms: 1_000
             )

    assert {:error, {%ArgumentError{}, _}} =
             start_center(
               mode: :grants,
               controller: {FixedP, 1.0},
               cap: cap,
               message_delay_max_ms: 5_000
             )
  end

  test "δ_max, (b): a grant that arrives δ late still ends before its period does" do
    cp = {_, advance} = clock()

    {c, campaign} =
      center(:grants,
        clock_pair: cp,
        message_delay_max_ms: 2,
        cap: [budget: 100, delay: 0.010, step: 0.005, share: 0.5]
      )

    a = client(c, campaign, "a", clock_pair: cp)

    # the grant reaches the client 2 ms (= δ_max) after the center sent it at t = 0
    :sys.suspend(a)
    tick(c)
    advance.(2)
    :sys.resume(a)
    # usable until arrival + (5 - 2) = 5 ms: the end of its period, not later
    assert PacingClient.info(a).valid_until == 5
  end

  test "δ_max: clients wait 3 periods plus δ_max before treating silence as a stop" do
    cp = clock()
    {c, campaign} = center(:broadcast, clock_pair: cp, message_delay_max_ms: 4)
    a = client(c, campaign, nil, clock_pair: cp)
    assert PacingClient.info(a).h.silence_ms == 3 * 5 + 4
  end

  # ------------------------------------------------------------------ unplanned delay defences

  test "age check: a client whose messages arrive too old does not bid" do
    cp = clock()
    {c, campaign} = center(:broadcast, clock_pair: cp)
    on_time = client(c, campaign, nil, clock_pair: cp)
    # this client's messages look 5 s old: max_age is δ_max (0) + clock skew (1 s)
    late =
      client(c, campaign, nil,
        clock_pair: cp,
        wall_clock: fn -> System.os_time(:millisecond) + 5_000 end
      )

    tick(c)
    assert PacingClient.bid?(on_time)
    refute PacingClient.bid?(late)
  end

  test "age check: a steady backlog of late messages ends in a stop by silence" do
    cp = {_, advance} = clock()
    {:ok, offset} = Agent.start_link(fn -> 0 end)
    wall = fn -> System.os_time(:millisecond) + Agent.get(offset, & &1) end
    {c, campaign} = center(:broadcast, clock_pair: cp)
    a = client(c, campaign, nil, clock_pair: cp, wall_clock: wall)
    tick(c)
    assert PacingClient.bid?(a)

    # from now on every message arrives 5 s late, but one still arrives every period
    Agent.update(offset, fn _ -> 5_000 end)

    for _ <- 1..4 do
      advance.(5)
      tick(c)
    end

    # new seq kept arriving, yet none was fresh: after the silence limit the client stops
    refute PacingClient.bid?(a)
  end

  test "age check: the relay drops old messages too" do
    cp = clock()
    {c, campaign} = center(:broadcast, clock_pair: cp)
    {now, _} = cp
    table = :"relay_#{System.unique_integer([:positive])}"

    {:ok, _} =
      PacingRelay.start_link(
        center: c,
        campaign: campaign,
        table: table,
        clock: now,
        wall_clock: fn -> System.os_time(:millisecond) + 5_000 end
      )

    h = PacingClient.handle(client(c, campaign, nil, clock_pair: cp, relay: table))
    tick(c)
    refute PacingClient.bid?(h)
  end

  test "lag: the center halts when a heartbeat shows a delay above plan, and resumes on request" do
    cp = {_, advance} = clock()
    test = self()

    {c, campaign} =
      center(:reports,
        clock_pair: cp,
        cap: [budget: 100],
        on_alarm: fn reason -> send(test, {:alarm, reason}) end
      )

    :ok = PacingCenter.subscribe(campaign)
    {:ok, %{lease_id: id, epoch: epoch}} = PacingCenter.register(c, "slow", :secret)

    :ok = PacingCenter.tick(c)
    assert_receive {:pacing_p, ^campaign, ^epoch, 1, 1.0, _}

    # a heartbeat 4 ms later that has applied seq 1: within plan (δ_max 0 + allowance 5)
    advance.(4)
    {:ok, _} = PacingCenter.heartbeat(c, id, %{applied_epoch: epoch, applied_seq: 1})
    refute_received {:alarm, _}

    # seq 2 sent at 4 ms; 12 ms later the client still reports seq 1: delay >= 12 ms
    :ok = PacingCenter.tick(c)
    advance.(12)
    {:ok, _} = PacingCenter.heartbeat(c, id, %{applied_epoch: epoch, applied_seq: 1})
    assert_receive {:alarm, {:lag, "slow", 12}}

    # halted: p = 0 is sent, whatever the controller says
    :ok = PacingCenter.tick(c)
    assert_receive {:pacing_p, ^campaign, ^epoch, 3, +0.0, _}

    :ok = PacingCenter.resume(c)
    :ok = PacingCenter.tick(c)
    assert_receive {:pacing_p, ^campaign, ^epoch, 4, 1.0, _}
  end

  test "lag: a halted center in grants mode grants nothing" do
    cp = {_, advance} = clock()

    {c, _campaign} =
      center(:grants,
        clock_pair: cp,
        on_alarm: fn _ -> :ok end,
        cap: [budget: 100, delay: 0.010, step: 0.005, share: 0.5]
      )

    {:ok, %{lease_id: id, epoch: epoch}} = PacingCenter.register(c, "me", :secret)
    :ok = PacingCenter.tick(c)
    assert_receive {:pacing_grant, _, _, 1, 50, _, _}
    advance.(20)
    {:ok, _} = PacingCenter.heartbeat(c, id, %{applied_epoch: epoch, applied_seq: 0})
    assert PacingCenter.info(c).halted
    :ok = PacingCenter.tick(c)
    assert_receive {:pacing_grant, _, _, 2, 0, _, _}
  end

  test "lag: clients report the seq they applied with every heartbeat" do
    cp = clock()
    {c, campaign} = center(:reports, clock_pair: cp, cap: [budget: 100])
    a = client(c, campaign, "a", clock_pair: cp)
    tick(c)
    :ok = PacingClient.heartbeat(a)
    # applied seq 1 right away: no alarm, still running
    refute PacingCenter.info(c).halted
  end
end
