defmodule PacingClient do
  @moduledoc """
  A client of one campaign: it receives p from `PacingCenter`, decides for each
  bid request whether to bid, and in the token-cap modes holds the lease and its
  tokens. Implements the client's side of `models/system.md` §6.

      {:ok, c} = PacingClient.start_link(center: center, campaign: :summer,
                                         client_id: "bidder-7", credentials: token)
      if PacingClient.bid?(c), do: place_bid()
      PacingClient.report_failure(c)      # :reports mode: a bid was not charged

  ## What the client enforces itself

  These rules keep the guarantees when the client loses contact with the center;
  the center cannot enforce them:

    * **Stop on silence.** If no message arrives from the center for
      `silence_ms` (default 3 periods), p is treated as 0.
    * **Order.** A message is applied only if its `{epoch, seq}` is newer than
      the last one applied, so a delayed old p cannot overwrite a newer one.
    * **Lease expiry (`:reports`).** The wallet is usable only until
      `lease_ttl_ms` after the last renewal the center acknowledged. Then the
      remaining tokens are dropped (kept as *unused*, to be returned when the
      client registers again) and the client re-registers.
    * **Grant expiry (`:grants`).** A grant is usable only for `valid_ms` after
      it arrives.

  All checks are made lazily when `bid?/1` is called, against the `:clock`
  function, so no timer is needed for safety. Timers are used only to send
  heartbeats (`:heartbeat_ms`; `:manual` to call `heartbeat/1` yourself).

  ## Options

    * `:center` - the `PacingCenter` (pid or name), `:campaign` (required)
    * `:client_id`, `:credentials` - for registration (token-cap modes)
    * `:wallet_size` - tokens per top-up in `:reports` mode (default 10). Keep it
      small: tokens in wallets hold budget until used or returned.
    * `:silence_ms` - default 3 × the center's period
    * `:heartbeat_ms` - default the lease ttl / 3; `:manual` for no timer
    * `:clock` - zero-arity function returning ms (default monotonic time)
    * `:coin` - zero-arity function returning a uniform number in [0, 1)
  """

  use GenServer

  # ------------------------------------------------------------------ API

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts, Keyword.take(opts, [:name]))

  @doc "A bid request arrived: bid or not? Uses up a token when it says yes in a token-cap mode."
  def bid?(client), do: GenServer.call(client, :bid?)

  @doc "`:reports` mode: `n` bids made with a token were not charged."
  def report_failure(client, n \\ 1), do: GenServer.call(client, {:failed, n})

  @doc "Renew the lease now (also sends the failure count)."
  def heartbeat(client), do: GenServer.call(client, :heartbeat)

  @doc "Leave the campaign, returning unused tokens."
  def leave(client), do: GenServer.call(client, :leave)

  @doc "Internal state, for monitoring and tests."
  def info(client), do: GenServer.call(client, :info)

  # ------------------------------------------------------------------ server

  @impl true
  def init(opts) do
    center = Keyword.fetch!(opts, :center)
    campaign = Keyword.fetch!(opts, :campaign)
    clock = Keyword.get(opts, :clock, fn -> System.monotonic_time(:millisecond) end)
    :ok = PacingCenter.subscribe(campaign)
    hello = PacingCenter.hello(center)

    s = %{
      center: center,
      campaign: campaign,
      mode: hello.mode,
      client_id: Keyword.get(opts, :client_id),
      credentials: Keyword.get(opts, :credentials),
      clock: clock,
      coin: Keyword.get(opts, :coin, &:rand.uniform/0),
      wallet_size: Keyword.get(opts, :wallet_size, 10),
      silence_ms: Keyword.get(opts, :silence_ms),
      heartbeat_ms: Keyword.get(opts, :heartbeat_ms),
      # last message applied
      epoch: hello.epoch,
      seq: hello.seq,
      p: hello.p,
      last_heard: clock.(),
      # lease
      lease_id: nil,
      lease_ttl_ms: nil,
      lease_until: nil,
      wallet: 0,
      grant: 0,
      grant_until: nil,
      # counters
      wanted: 0,
      bids: 0,
      failed: 0,
      unused: 0,
      lease_failed_base: 0
    }

    s = %{s | silence_ms: s.silence_ms || 3 * hello.step_ms}
    s = if s.mode == :broadcast, do: s, else: register(s, nil)
    schedule_heartbeat(s)
    {:ok, s}
  end

  @impl true
  def handle_call(:bid?, _from, s) do
    now = s.clock.()
    p = if now - s.last_heard > s.silence_ms, do: 0.0, else: s.p

    if p > 0.0 and s.coin.() < p do
      s = %{s | wanted: s.wanted + 1}
      {ok?, s} = take_token(s, now)
      {:reply, ok?, if(ok?, do: %{s | bids: s.bids + 1}, else: s)}
    else
      {:reply, false, s}
    end
  end

  def handle_call({:failed, n}, _from, s), do: {:reply, :ok, %{s | failed: s.failed + n}}

  def handle_call(:heartbeat, _from, s), do: {:reply, :ok, do_heartbeat(s)}

  def handle_call(:leave, _from, %{lease_id: nil} = s), do: {:stop, :normal, :ok, s}

  def handle_call(:leave, _from, s) do
    PacingCenter.deregister(s.center, s.lease_id, lease_report(s, s.wallet + s.unused))
    {:stop, :normal, :ok, %{s | wallet: 0}}
  end

  def handle_call(:info, _from, s), do: {:reply, s, s}

  @impl true
  def handle_info({:pacing_p, campaign, epoch, seq, p}, %{campaign: campaign} = s) do
    if newer?(s, epoch, seq),
      do: {:noreply, %{s | epoch: epoch, seq: seq, p: p, last_heard: s.clock.()}},
      else: {:noreply, s}
  end

  def handle_info({:pacing_keepalive, campaign, epoch, seq}, %{campaign: campaign} = s) do
    if newer?(s, epoch, seq),
      do: {:noreply, %{s | epoch: epoch, seq: seq, last_heard: s.clock.()}},
      else: {:noreply, s}
  end

  def handle_info(
        {:pacing_grant, campaign, epoch, seq, tokens, valid_ms},
        %{campaign: campaign} = s
      ) do
    # sent right after the p of the same period, so {epoch, seq} equals the last applied
    if {epoch, seq} >= {s.epoch, s.seq} do
      now = s.clock.()
      {:noreply, %{s | grant: tokens, grant_until: now + valid_ms, last_heard: now}}
    else
      {:noreply, s}
    end
  end

  def handle_info(:heartbeat, s) do
    schedule_heartbeat(s)
    {:noreply, do_heartbeat(s)}
  end

  def handle_info(_msg, s), do: {:noreply, s}

  # ------------------------------------------------------------------ internals

  defp newer?(s, epoch, seq), do: {epoch, seq} > {s.epoch, s.seq}

  defp take_token(%{mode: :broadcast} = s, _now), do: {true, s}

  defp take_token(%{mode: :grants} = s, now) do
    if s.grant > 0 and s.grant_until != nil and now < s.grant_until,
      do: {true, %{s | grant: s.grant - 1}},
      else: {false, s}
  end

  defp take_token(%{mode: :reports} = s, now) do
    s = if now < s.lease_until, do: s, else: drop_lease(s)
    s = if s.wallet == 0 and s.lease_id != nil, do: top_up(s), else: s

    if s.wallet > 0,
      do: {true, %{s | wallet: s.wallet - 1}},
      else: {false, s}
  end

  # the lease ran out without renewal: drop the wallet and register again,
  # settling the old lease
  defp drop_lease(s) do
    s = %{s | unused: s.unused + s.wallet, wallet: 0}
    register(s, %{lease_id: s.lease_id, failed: s.failed - s.lease_failed_base, unused: s.unused})
  end

  defp top_up(s) do
    case PacingCenter.request_tokens(s.center, s.lease_id, s.wallet_size, lease_report(s, 0)) do
      {:ok, n} -> %{s | wallet: n, lease_until: s.clock.() + s.lease_ttl_ms}
      {:error, :lost} -> drop_lease(s)
    end
  end

  defp do_heartbeat(%{lease_id: nil} = s), do: s

  defp do_heartbeat(s) do
    case PacingCenter.heartbeat(s.center, s.lease_id, lease_report(s, 0)) do
      {:ok, ttl} -> %{s | lease_until: s.clock.() + ttl}
      {:error, :lost} when s.mode == :reports -> drop_lease(s)
      {:error, :lost} -> register(s, nil)
    end
  end

  # failures are counted per lease: the center knows each lease's cumulative count
  defp lease_report(s, unused), do: %{failed: s.failed - s.lease_failed_base, unused: unused}

  defp register(s, previous) do
    opts = if previous, do: [previous: previous], else: []

    case PacingCenter.register(s.center, s.client_id, s.credentials, opts) do
      {:ok, r} ->
        %{
          s
          | lease_id: r.lease_id,
            lease_ttl_ms: r.lease_ttl_ms,
            lease_until: s.clock.() + r.lease_ttl_ms,
            wallet: 0,
            unused: 0,
            lease_failed_base: s.failed
        }

      {:error, reason} ->
        raise "registration failed: #{inspect(reason)}"
    end
  end

  defp schedule_heartbeat(%{mode: :broadcast}), do: :ok
  defp schedule_heartbeat(%{heartbeat_ms: :manual}), do: :ok

  defp schedule_heartbeat(s),
    do: Process.send_after(self(), :heartbeat, s.heartbeat_ms || div(s.lease_ttl_ms, 3))
end
