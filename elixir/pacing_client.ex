defmodule PacingClient do
  @moduledoc """
  A client of one campaign: it receives p from `PacingCenter`, decides for each
  bid request whether to bid, and in the token-cap modes holds the lease and its
  tokens. Implements the client's side of `models/system.md` §6.

      {:ok, c} = PacingClient.start_link(center: center, campaign: :summer,
                                         client_id: "bidder-7", credentials: token)
      h = PacingClient.handle(c)          # once, in each bidding process
      if PacingClient.bid?(h), do: place_bid()
      PacingClient.report_failure(h)      # :reports mode: a bid was not charged

  ## Fast path: no message per bid

  The latest `{epoch, seq, p, heard_at}` is kept in an ETS row, and the client's
  tokens (wallet or grant) and their validity in `:atomics`. `bid?/1` with a
  `PacingClient.Handle` reads these directly from the calling process: no
  message, no call, safe from any number of processes at once. Tokens are taken
  with compare-and-swap, so they never go negative and are never counted twice.

  The client process is called only on the *slow path*: `:reports` mode when
  the wallet is empty (top-up) or the lease has run out (register again). With a
  wallet of L tokens that is about one call per L bids. After a top-up that got
  nothing (budget used up), bids are refused without a call until the next
  heartbeat or `step_ms` later.

  `bid?/1` with the client's pid still works; it makes one call per bid.

  ## Where p comes from

    * Default: the client process subscribes to the center's `:pg` group and
      writes p to its own ETS table.
    * `relay: table`: a `PacingRelay` on the node subscribes once and writes p to
      the named ETS `table`; all clients on the node read it. This is the hybrid
      of `models/system.md` §6: push once per node, read locally per bid.

  ## What the client enforces itself

    * **Stop on silence.** p counts as 0 if `seq` has not advanced for
      `silence_ms` (default 3 periods).
    * **Order.** A message is applied only if its `{epoch, seq}` is newer.
    * **Lease expiry (`:reports`).** The wallet is usable only until `lease_ttl_ms`
      after the last renewal the center acknowledged. Then the remaining tokens
      are dropped (kept as *unused*, returned when the client registers again).
    * **Grant expiry (`:grants`).** A grant is usable only for `valid_ms` after
      it arrives.

  All are checked at every bid against the `:clock` function.

  ## Options

    * `:center`, `:campaign` (required)
    * `:client_id`, `:credentials` - for registration (token-cap modes)
    * `:relay` - name of a `PacingRelay` ETS table on this node (optional)
    * `:wallet_size` - tokens per top-up in `:reports` mode (default 10)
    * `:silence_ms` - default 3 × the center's period + its `message_delay_max_ms`
    * `:heartbeat_ms` - default the lease ttl / 3; `:manual` for no timer
    * `:clock` - zero-arity function returning ms (default monotonic time)
    * `:coin` - zero-arity function returning a uniform number in [0, 1)
  """

  use GenServer

  defmodule Handle do
    @moduledoc "What a bidding process needs to call `PacingClient.bid?/1` without a message."
    defstruct [:client, :campaign, :table, :mode, :tokens, :counters, :clock, :coin, :silence_ms]
  end

  @never -4_611_686_018_427_387_904

  # :counters slots
  @wanted 1
  @bids 2
  @failed 3

  # :atomics slots
  @tok 1
  @until 2
  @retry 3

  # ------------------------------------------------------------------ API

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts, Keyword.take(opts, [:name]))

  @doc "The fast-path handle. Get it once per bidding process; it does not change."
  def handle(client), do: GenServer.call(client, :handle)

  @doc """
  A bid request arrived: bid or not? Takes a token when it says yes in a
  token-cap mode. With a `Handle` it usually sends no message (see the module doc).
  """
  def bid?(%Handle{} = h) do
    case check(h, h.clock.()) do
      :yes -> true
      :no -> false
      :slow -> GenServer.call(h.client, :take_slow)
    end
  end

  def bid?(client), do: GenServer.call(client, :bid?)

  @doc "`:reports` mode: `n` bids made with a token were not charged."
  def report_failure(client_or_handle, n \\ 1)

  def report_failure(%Handle{} = h, n), do: :counters.add(h.counters, @failed, n)
  def report_failure(client, n), do: GenServer.call(client, {:failed, n})

  @doc "Renew the lease now (also sends the failure count)."
  def heartbeat(client), do: GenServer.call(client, :heartbeat)

  @doc "Leave the campaign, returning unused tokens."
  def leave(client), do: GenServer.call(client, :leave)

  @doc "State and counters, for monitoring and tests."
  def info(client), do: GenServer.call(client, :info)

  # ------------------------------------------------------------------ fast path

  @doc false
  # :yes | :no | :slow (needs the client process: top-up or new lease)
  def check(h, now) do
    case current(h) do
      {_epoch, _seq, p, heard_at} when p > 0 and now - heard_at <= h.silence_ms ->
        if h.coin.() < p do
          :counters.add(h.counters, @wanted, 1)
          take_fast(h, now)
        else
          :no
        end

      _ ->
        :no
    end
  end

  # {epoch, seq, p, heard_at}, or nil if there is no p (or the relay's table is gone)
  defp current(h) do
    case :ets.lookup(h.table, h.campaign) do
      [{_, epoch, seq, p, heard_at}] -> {epoch, seq, p, heard_at}
      [] -> nil
    end
  rescue
    ArgumentError -> nil
  end

  defp take_fast(%{mode: :broadcast} = h, _now), do: counted(h, :yes)

  defp take_fast(%{mode: :grants} = h, now) do
    if now < :atomics.get(h.tokens, @until) and cas_take(h.tokens),
      do: counted(h, :yes),
      else: :no
  end

  defp take_fast(%{mode: :reports} = h, now) do
    cond do
      now >= :atomics.get(h.tokens, @until) -> :slow
      cas_take(h.tokens) -> counted(h, :yes)
      now < :atomics.get(h.tokens, @retry) -> :no
      true -> :slow
    end
  end

  defp counted(h, :yes) do
    :counters.add(h.counters, @bids, 1)
    :yes
  end

  # take one token if there is one; never below zero
  defp cas_take(ref) do
    v = :atomics.get(ref, @tok)

    cond do
      v <= 0 -> false
      :atomics.compare_exchange(ref, @tok, v, v - 1) == :ok -> true
      true -> cas_take(ref)
    end
  end

  # ------------------------------------------------------------------ server

  @impl true
  def init(opts) do
    center = Keyword.fetch!(opts, :center)
    campaign = Keyword.fetch!(opts, :campaign)
    clock = Keyword.get(opts, :clock, fn -> System.monotonic_time(:millisecond) end)
    hello = PacingCenter.hello(center)

    {table, own_table?} =
      case Keyword.get(opts, :relay) do
        nil ->
          t = :ets.new(:pacing_client, [:set, :protected, read_concurrency: true])
          :ets.insert(t, {campaign, hello.epoch, hello.seq, hello.p, clock.()})
          :ok = PacingCenter.subscribe(campaign)
          {t, true}

        name ->
          {name, false}
      end

    tokens = :atomics.new(3, signed: true)
    :atomics.put(tokens, @until, @never)
    :atomics.put(tokens, @retry, @never)

    h = %Handle{
      client: self(),
      campaign: campaign,
      table: table,
      mode: hello.mode,
      tokens: tokens,
      counters: :counters.new(3, [:write_concurrency]),
      clock: clock,
      coin: Keyword.get(opts, :coin, &:rand.uniform/0),
      # a late p is not silence: allow for the delivery delay on top of 3 periods
      silence_ms: Keyword.get(opts, :silence_ms, 3 * hello.step_ms + hello.message_delay_max_ms)
    }

    s = %{
      h: h,
      center: center,
      own_table?: own_table?,
      step_ms: hello.step_ms,
      client_id: Keyword.get(opts, :client_id),
      credentials: Keyword.get(opts, :credentials),
      wallet_size: Keyword.get(opts, :wallet_size, 10),
      heartbeat_ms: Keyword.get(opts, :heartbeat_ms),
      lease_id: nil,
      lease_ttl_ms: nil,
      unused: 0,
      lease_failed_base: 0
    }

    s = if h.mode == :broadcast, do: s, else: register(s, nil)
    schedule_heartbeat(s)
    {:ok, s}
  end

  @impl true
  def handle_call(:handle, _from, s), do: {:reply, s.h, s}

  def handle_call(:bid?, _from, s) do
    case check(s.h, s.h.clock.()) do
      :yes -> {:reply, true, s}
      :no -> {:reply, false, s}
      :slow -> take_slow(s)
    end
  end

  def handle_call(:take_slow, _from, s), do: take_slow(s)

  def handle_call({:failed, n}, _from, s) do
    :counters.add(s.h.counters, @failed, n)
    {:reply, :ok, s}
  end

  def handle_call(:heartbeat, _from, s), do: {:reply, :ok, do_heartbeat(s)}

  def handle_call(:leave, _from, %{lease_id: nil} = s), do: {:stop, :normal, :ok, s}

  def handle_call(:leave, _from, s) do
    left = :atomics.exchange(s.h.tokens, @tok, 0)
    PacingCenter.deregister(s.center, s.lease_id, lease_report(s, left + s.unused))
    {:stop, :normal, :ok, s}
  end

  def handle_call(:info, _from, s) do
    {epoch, seq, p, heard_at} = current(s.h) || {nil, nil, 0.0, nil}
    c = s.h.counters

    info =
      Map.merge(s, %{
        mode: s.h.mode,
        epoch: epoch,
        seq: seq,
        p: p,
        heard_at: heard_at,
        tokens: :atomics.get(s.h.tokens, @tok),
        valid_until: :atomics.get(s.h.tokens, @until),
        wanted: :counters.get(c, @wanted),
        bids: :counters.get(c, @bids),
        failed: :counters.get(c, @failed)
      })

    {:reply, info, s}
  end

  @impl true
  def handle_info({:pacing_p, campaign, epoch, seq, p}, %{own_table?: true} = s) do
    if campaign == s.h.campaign and newer?(s, epoch, seq),
      do: :ets.insert(s.h.table, {campaign, epoch, seq, p, s.h.clock.()})

    {:noreply, s}
  end

  def handle_info({:pacing_keepalive, campaign, epoch, seq}, %{own_table?: true} = s) do
    with true <- campaign == s.h.campaign and newer?(s, epoch, seq),
         {_, _, p, _} <- current(s.h) do
      :ets.insert(s.h.table, {campaign, epoch, seq, p, s.h.clock.()})
    end

    {:noreply, s}
  end

  def handle_info({:pacing_grant, campaign, epoch, seq, tokens, valid_ms}, s) do
    # sent right after the p of the same period, so {epoch, seq} is not older than the last p
    {e, sq, _, _} = current(s.h) || {epoch, seq, 0.0, 0}

    if campaign == s.h.campaign and {epoch, seq} >= {e, sq} do
      ref = s.h.tokens
      # empty first, so no bid can take an old token under the new validity
      :atomics.exchange(ref, @tok, 0)
      :atomics.put(ref, @until, s.h.clock.() + valid_ms)
      :atomics.put(ref, @tok, tokens)
    end

    {:noreply, s}
  end

  def handle_info(:heartbeat, s) do
    schedule_heartbeat(s)
    {:noreply, do_heartbeat(s)}
  end

  def handle_info(_msg, s), do: {:noreply, s}

  # ------------------------------------------------------------------ slow path

  defp newer?(s, epoch, seq) do
    case current(s.h) do
      {e, sq, _, _} -> {epoch, seq} > {e, sq}
      nil -> true
    end
  end

  defp take_slow(%{h: %{mode: :reports} = h} = s) do
    now = h.clock.()
    s = if now >= :atomics.get(h.tokens, @until), do: drop_lease(s), else: s

    cond do
      cas_take(h.tokens) ->
        {:reply, counted(h, :yes) == :yes, s}

      now < :atomics.get(h.tokens, @retry) ->
        {:reply, false, s}

      true ->
        s = top_up(s)

        if cas_take(h.tokens) do
          {:reply, counted(h, :yes) == :yes, s}
        else
          # budget used up for now: refuse without calls for a while
          :atomics.put(h.tokens, @retry, now + s.step_ms)
          {:reply, false, s}
        end
    end
  end

  defp take_slow(%{h: h} = s) do
    ok? = h.clock.() < :atomics.get(h.tokens, @until) and cas_take(h.tokens)
    if ok?, do: counted(h, :yes)
    {:reply, ok?, s}
  end

  # the lease ran out without renewal: drop the wallet and register again,
  # settling the old lease
  defp drop_lease(s) do
    left = :atomics.exchange(s.h.tokens, @tok, 0)
    s = %{s | unused: s.unused + left}

    register(s, %{
      lease_id: s.lease_id,
      failed: failed(s) - s.lease_failed_base,
      unused: s.unused
    })
  end

  defp top_up(s) do
    case PacingCenter.request_tokens(s.center, s.lease_id, s.wallet_size, lease_report(s, 0)) do
      {:ok, n} ->
        :atomics.add(s.h.tokens, @tok, n)
        :atomics.put(s.h.tokens, @until, s.h.clock.() + s.lease_ttl_ms)
        s

      {:error, :lost} ->
        drop_lease(s)
    end
  end

  defp do_heartbeat(%{lease_id: nil} = s), do: s

  defp do_heartbeat(s) do
    case PacingCenter.heartbeat(s.center, s.lease_id, lease_report(s, 0)) do
      {:ok, ttl} ->
        :atomics.put(s.h.tokens, @until, s.h.clock.() + ttl)
        # reported failures may have freed budget: allow a top-up again
        :atomics.put(s.h.tokens, @retry, @never)
        s

      {:error, :lost} when s.h.mode == :reports ->
        drop_lease(s)

      {:error, :lost} ->
        register(s, nil)
    end
  end

  defp failed(s), do: :counters.get(s.h.counters, @failed)

  # failures are counted per lease: the center knows each lease's cumulative count
  defp lease_report(s, unused), do: %{failed: failed(s) - s.lease_failed_base, unused: unused}

  defp register(s, previous) do
    opts = if previous, do: [previous: previous], else: []

    case PacingCenter.register(s.center, s.client_id, s.credentials, opts) do
      {:ok, r} ->
        if s.h.mode == :reports,
          do: :atomics.put(s.h.tokens, @until, s.h.clock.() + r.lease_ttl_ms)

        :atomics.put(s.h.tokens, @retry, @never)

        %{
          s
          | lease_id: r.lease_id,
            lease_ttl_ms: r.lease_ttl_ms,
            unused: 0,
            lease_failed_base: failed(s)
        }

      {:error, reason} ->
        raise "registration failed: #{inspect(reason)}"
    end
  end

  defp schedule_heartbeat(%{h: %{mode: :broadcast}}), do: :ok
  defp schedule_heartbeat(%{heartbeat_ms: :manual}), do: :ok

  defp schedule_heartbeat(s),
    do: Process.send_after(self(), :heartbeat, s.heartbeat_ms || div(s.lease_ttl_ms, 3))
end

defmodule PacingRelay do
  @moduledoc """
  The node-local half of the hybrid in `models/system.md` §6: one process per
  node and campaign subscribes to the center's p channel and writes the latest
  `{epoch, seq, p, heard_at}` to a named ETS table. Every `PacingClient` on the
  node started with `relay: table` reads p from there, so the center sends one
  message per node instead of one per client, and a bid reads p without a
  message.

  The relay keeps no state of its own and invents no messages. It writes only
  messages with a newer `{epoch, seq}`, so it cannot make a stale p look fresh.
  If it dies, its table goes with it, and the clients read no p, so they do not
  bid. That is safe; restart it under a supervisor.

      {:ok, _} = PacingRelay.start_link(center: center, campaign: :summer, table: :summer_p)
      {:ok, c} = PacingClient.start_link(center: center, campaign: :summer, relay: :summer_p)

  Options: `:center`, `:campaign`, `:table` (an atom, unique on the node), `:clock`.
  """

  use GenServer

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts)

  @impl true
  def init(opts) do
    campaign = Keyword.fetch!(opts, :campaign)
    clock = Keyword.get(opts, :clock, fn -> System.monotonic_time(:millisecond) end)

    table =
      :ets.new(Keyword.fetch!(opts, :table), [
        :named_table,
        :set,
        :protected,
        read_concurrency: true
      ])

    hello = PacingCenter.hello(Keyword.fetch!(opts, :center))
    :ets.insert(table, {campaign, hello.epoch, hello.seq, hello.p, clock.()})
    :ok = PacingCenter.subscribe(campaign)
    {:ok, %{table: table, campaign: campaign, clock: clock}}
  end

  @impl true
  def handle_info({:pacing_p, campaign, epoch, seq, p}, %{campaign: campaign} = s) do
    if newer?(s, epoch, seq), do: :ets.insert(s.table, {campaign, epoch, seq, p, s.clock.()})
    {:noreply, s}
  end

  def handle_info({:pacing_keepalive, campaign, epoch, seq}, %{campaign: campaign} = s) do
    if newer?(s, epoch, seq) do
      [{_, _, _, p, _}] = :ets.lookup(s.table, campaign)
      :ets.insert(s.table, {campaign, epoch, seq, p, s.clock.()})
    end

    {:noreply, s}
  end

  def handle_info(_msg, s), do: {:noreply, s}

  defp newer?(s, epoch, seq) do
    [{_, e, sq, _, _}] = :ets.lookup(s.table, s.campaign)
    {epoch, seq} > {e, sq}
  end
end
