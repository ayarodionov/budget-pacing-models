defmodule PacingCenter do
  @moduledoc """
  The center of one campaign as a process: it runs the controller every control
  period, broadcasts p to subscribed clients, and, with a token cap, registers
  clients and hands out tokens under leases. Implements `models/system.md` §6.

  ## Modes

    * `:broadcast` - probabilistic controller only. Clients *subscribe* (no
      registration); the center never learns who they are.
    * `:reports` - token cap version (a) (`TokenCap.Reports`). Clients register,
      draw tokens into small wallets with `request_tokens/4`, and report failed
      bids. A token handed out counts as used until it is reported failed or
      returned unused.
    * `:grants` - token cap version (b) (`TokenCap.Grants`). Clients register and
      heartbeat; every period the grant is split evenly among live leases and is
      valid only for that period. Clients report nothing about bids.

  ## Messages to clients

  Clients join the `:pg` group `{:pacing, campaign}` (see `subscribe/2`). Every
  period every member gets one of

      {:pacing_p, campaign, epoch, seq, p}         # new p
      {:pacing_keepalive, campaign, epoch, seq}    # p unchanged (broadcast threshold)

  and in `:grants` mode each live lease also gets

      {:pacing_grant, campaign, epoch, seq, tokens, valid_ms}

  `epoch` identifies this run of the center (it grows across restarts); `seq`
  grows by one per period. Clients apply a message only if `{epoch, seq}` is newer
  than the last one applied (`PacingClient` does this).

  ## Safety rules kept here

    * Only authenticated clients register (`:authenticate` is required in the
      token-cap modes).
    * A lease without a heartbeat for `lease_ttl_ms` is *lost*. Its tokens stay
      counted as used: the center cannot tell a crashed client from one that is
      cut off and still bidding. They are credited only when the same client
      registers again and reports its old lease (`previous:`), or never.
    * Credits from a lease (failures + returned) never exceed the tokens it was
      handed, so a buggy client cannot inflate the allowance beyond its own tokens.
    * Grants are valid for `step_ms − grant_margin_ms`, measured from when the
      client receives them; the margin covers delivery delay and clock drift.

  The client's side (stop on silence, local lease expiry) is in `PacingClient`.

  ## Options

    * `:campaign` - any term naming the campaign (required)
    * `:mode` - `:broadcast` (default), `:reports` or `:grants`
    * `:controller` - options for `PacingController.new/1`, or
      `{module, state}` for any module with `step(state, visible_spend)`
      returning `{p, send?, state}` (required)
    * `:cap` - options for `TokenCap.Reports.new/1` or `TokenCap.Grants.new/1`
      (required in the token-cap modes)
    * `:visible_spend` - zero-arity function returning the spend the budget shows
      now, in budget units (required)
    * `:step_ms` - control period in ms (required)
    * `:tick` - `:auto` (default: run every `step_ms`) or `:manual` (call `tick/1`)
    * `:lease_ttl_ms` - default `3 * step_ms`
    * `:grant_margin_ms` - default `step_ms / 10`
    * `:authenticate` - `fn client_id, credentials -> boolean end`
    * `:clock` - zero-arity function returning ms (default monotonic time)
    * `:epoch` - default `System.os_time(:microsecond)` at start
    * `:name` - GenServer name
  """

  use GenServer

  alias TokenCap.{Grants, Reports}

  @scope PacingCenter.PG

  # ------------------------------------------------------------------ client API

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts, Keyword.take(opts, [:name]))

  @doc """
  Join the campaign's p channel. Starts the `:pg` scope if needed; in production
  start it in your supervision tree instead: `{:pg, #{inspect(@scope)}}` via
  `%{id: :pg, start: {:pg, :start_link, [#{inspect(@scope)}]}}`.
  """
  def subscribe(campaign, pid \\ self()) do
    ensure_scope()
    :pg.join(@scope, {:pacing, campaign}, pid)
  end

  @doc "Mode, period and the latest `{epoch, seq, p}`, for a client that just joined."
  def hello(center), do: GenServer.call(center, :hello)

  @doc """
  Register a client (token-cap modes). `opts`: `pid:` (default self()),
  `previous: %{lease_id: id, failed: n, unused: m}` to settle an old lease.
  Returns `{:ok, %{lease_id, lease_ttl_ms, ...}}` or `{:error, reason}`.
  """
  def register(center, client_id, credentials, opts \\ []),
    do:
      GenServer.call(
        center,
        {:register, client_id, credentials, Keyword.put_new(opts, :pid, self())}
      )

  @doc "Keep a lease alive; `report` is `%{failed: cumulative failed bids}` in `:reports` mode."
  def heartbeat(center, lease_id, report \\ %{}),
    do: GenServer.call(center, {:heartbeat, lease_id, report})

  @doc "`:reports` mode: ask for up to `n` tokens. Also renews the lease. Returns `{:ok, granted}`."
  def request_tokens(center, lease_id, n, report \\ %{}),
    do: GenServer.call(center, {:request_tokens, lease_id, n, report})

  @doc "Leave: `report` is `%{failed: cumulative, unused: tokens returned}`."
  def deregister(center, lease_id, report \\ %{}),
    do: GenServer.call(center, {:deregister, lease_id, report})

  @doc "Run one control period now (with `tick: :manual`)."
  def tick(center), do: GenServer.call(center, :tick)

  @doc "Internal state, for monitoring and tests."
  def info(center), do: GenServer.call(center, :info)

  # ------------------------------------------------------------------ server

  @impl true
  def init(opts) do
    ensure_scope()
    mode = Keyword.get(opts, :mode, :broadcast)
    step_ms = Keyword.fetch!(opts, :step_ms)

    if mode != :broadcast and not Keyword.has_key?(opts, :authenticate),
      do: raise(ArgumentError, "#{mode} mode needs :authenticate")

    {ctl_mod, ctl} =
      case Keyword.fetch!(opts, :controller) do
        {mod, state} when is_atom(mod) -> {mod, state}
        ctl_opts -> {PacingController, PacingController.new(ctl_opts)}
      end

    cap =
      case mode do
        :broadcast -> nil
        :reports -> Reports.new(Keyword.fetch!(opts, :cap))
        :grants -> Grants.new(Keyword.fetch!(opts, :cap))
      end

    state = %{
      campaign: Keyword.fetch!(opts, :campaign),
      mode: mode,
      ctl_mod: ctl_mod,
      ctl: ctl,
      cap: cap,
      visible_spend: Keyword.fetch!(opts, :visible_spend),
      step_ms: step_ms,
      lease_ttl_ms: Keyword.get(opts, :lease_ttl_ms, 3 * step_ms),
      grant_margin_ms: Keyword.get(opts, :grant_margin_ms, div(step_ms, 10)),
      authenticate: Keyword.get(opts, :authenticate, fn _, _ -> true end),
      clock: Keyword.get(opts, :clock, fn -> System.monotonic_time(:millisecond) end),
      epoch: Keyword.get(opts, :epoch, System.os_time(:microsecond)),
      seq: 0,
      p: 0.0,
      leases: %{},
      lost: %{},
      next_lease: 1,
      auto: Keyword.get(opts, :tick, :auto) == :auto
    }

    if state.auto, do: send(self(), :tick)
    {:ok, state}
  end

  @impl true
  def handle_info(:tick, s) do
    Process.send_after(self(), :tick, s.step_ms)
    {:noreply, do_tick(s)}
  end

  @impl true
  def handle_call(:tick, _from, s), do: {:reply, :ok, do_tick(s)}

  def handle_call(:hello, _from, s),
    do: {:reply, %{mode: s.mode, step_ms: s.step_ms, epoch: s.epoch, seq: s.seq, p: s.p}, s}

  def handle_call(:info, _from, s), do: {:reply, s, s}

  def handle_call({:register, client_id, credentials, opts}, _from, s) do
    cond do
      s.mode == :broadcast ->
        {:reply, {:error, :no_registration_in_broadcast_mode}, s}

      not s.authenticate.(client_id, credentials) ->
        {:reply, {:error, :unauthenticated}, s}

      true ->
        s = settle_previous(s, client_id, Keyword.get(opts, :previous))
        # a live lease of the same client is replaced; its tokens stay counted until settled
        s = replace_live_lease(s, client_id)
        id = s.next_lease

        lease = %{
          id: id,
          client_id: client_id,
          pid: Keyword.fetch!(opts, :pid),
          last_heartbeat: s.clock.(),
          handed: 0,
          credited: 0,
          failed: 0
        }

        s = %{s | leases: Map.put(s.leases, id, lease), next_lease: id + 1}

        reply = %{
          lease_id: id,
          lease_ttl_ms: s.lease_ttl_ms,
          step_ms: s.step_ms,
          mode: s.mode,
          epoch: s.epoch,
          seq: s.seq,
          p: s.p
        }

        {:reply, {:ok, reply}, s}
    end
  end

  def handle_call({:heartbeat, id, report}, _from, s) do
    case Map.fetch(s.leases, id) do
      {:ok, lease} ->
        {lease, s} = credit(s, lease, Map.get(report, :failed), 0)
        s = put_lease(s, %{lease | last_heartbeat: s.clock.()})
        {:reply, {:ok, s.lease_ttl_ms}, s}

      :error ->
        {:reply, {:error, :lost}, s}
    end
  end

  def handle_call({:request_tokens, id, n, report}, _from, %{mode: :reports} = s) do
    case Map.fetch(s.leases, id) do
      {:ok, lease} ->
        {lease, s} = credit(s, lease, Map.get(report, :failed), 0)
        give = min(n, Reports.allowance(s.cap))
        cap = Reports.record_use(s.cap, give)
        lease = %{lease | handed: lease.handed + give, last_heartbeat: s.clock.()}
        {:reply, {:ok, give}, put_lease(%{s | cap: cap}, lease)}

      :error ->
        {:reply, {:error, :lost}, s}
    end
  end

  def handle_call({:request_tokens, _, _, _}, _from, s), do: {:reply, {:error, :wrong_mode}, s}

  def handle_call({:deregister, id, report}, _from, s) do
    case Map.fetch(s.leases, id) do
      {:ok, lease} ->
        {_lease, s} = credit(s, lease, Map.get(report, :failed), Map.get(report, :unused, 0))
        {:reply, :ok, %{s | leases: Map.delete(s.leases, id)}}

      :error ->
        {:reply, {:error, :lost}, s}
    end
  end

  # ------------------------------------------------------------------ internals

  defp do_tick(s) do
    s = expire_leases(s)
    spend = s.visible_spend.()
    {p, send?, ctl} = s.ctl_mod.step(s.ctl, spend)
    s = %{s | ctl: ctl, seq: s.seq + 1, p: p}

    msg =
      if send?,
        do: {:pacing_p, s.campaign, s.epoch, s.seq, p},
        else: {:pacing_keepalive, s.campaign, s.epoch, s.seq}

    for pid <- :pg.get_members(@scope, {:pacing, s.campaign}), do: send(pid, msg)

    if s.mode == :grants, do: send_grants(s, spend), else: s
  end

  defp send_grants(s, spend) do
    {g, cap} = Grants.grant(s.cap, spend)
    leases = s.leases |> Map.values() |> Enum.sort_by(& &1.id)
    valid_ms = s.step_ms - s.grant_margin_ms

    for {lease, share} <- Enum.zip(leases, split(g, length(leases))) do
      send(lease.pid, {:pacing_grant, s.campaign, s.epoch, s.seq, share, valid_ms})
    end

    %{s | cap: cap}
  end

  # g tokens split as evenly as possible among n leases
  defp split(_g, 0), do: []
  defp split(g, n), do: for(i <- 0..(n - 1), do: div(g, n) + if(i < rem(g, n), do: 1, else: 0))

  defp expire_leases(s) do
    now = s.clock.()

    {lost, live} =
      Enum.split_with(s.leases, fn {_, l} -> now - l.last_heartbeat > s.lease_ttl_ms end)

    %{s | leases: Map.new(live), lost: Map.merge(s.lost, Map.new(lost))}
  end

  defp replace_live_lease(s, client_id) do
    case Enum.find(s.leases, fn {_, l} -> l.client_id == client_id end) do
      nil -> s
      {id, l} -> %{s | leases: Map.delete(s.leases, id), lost: Map.put(s.lost, id, l)}
    end
  end

  defp settle_previous(s, _client_id, nil), do: s

  defp settle_previous(s, client_id, %{lease_id: id} = prev) do
    with {:ok, lease} <- Map.fetch(Map.merge(s.lost, s.leases), id),
         true <- lease.client_id == client_id do
      {_lease, s} = credit(s, lease, Map.get(prev, :failed), Map.get(prev, :unused, 0))
      %{s | lost: Map.delete(s.lost, id), leases: Map.delete(s.leases, id)}
    else
      _ -> s
    end
  end

  # credit new failures (cumulative count) and returned tokens to the cap, never
  # more in total than the lease was handed
  defp credit(%{mode: :reports} = s, lease, failed_total, unused) do
    new_failed = max((failed_total || lease.failed) - lease.failed, 0)
    room = lease.handed - lease.credited
    f = min(new_failed, room)
    u = min(unused, room - f)
    cap = s.cap |> Reports.record_failures(f) |> Reports.record_returned(u)
    lease = %{lease | failed: lease.failed + f, credited: lease.credited + f + u}
    {lease, %{s | cap: cap}}
  end

  defp credit(s, lease, _failed, _unused), do: {lease, s}

  defp put_lease(s, lease), do: %{s | leases: Map.put(s.leases, lease.id, lease)}

  defp ensure_scope do
    case :pg.start(@scope) do
      {:ok, _} -> :ok
      {:error, {:already_started, _}} -> :ok
    end
  end
end
