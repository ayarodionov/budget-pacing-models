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

      {:pacing_p, campaign, epoch, seq, p, sent_at}         # new p
      {:pacing_keepalive, campaign, epoch, seq, sent_at}    # p unchanged (broadcast threshold)

  and in `:grants` mode each live lease also gets

      {:pacing_grant, campaign, epoch, seq, tokens, valid_ms, sent_at}

  `sent_at` is the center's wall-clock time in ms. Clients ignore a message older
  than `max_age_ms` (δ_max + `clock_skew_ms`, sent in `hello/1`), so a delay much
  larger than planned makes them stop instead of acting on an old p.

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

  ## Delivery delay of p (δ_max)

  If p (and grants) can reach clients late, through a broker, a relay or clients
  that poll, set `message_delay_max_ms` to the largest delay. One setting keeps
  every part consistent (`models/system.md` §6, *Push or pull*):

    * the controller's in-flight window grows by ceil(δ_max / step) periods
      (`message_delay_max` of `PacingController`, filled in from this option);
    * in `:grants` mode the grant margin defaults to δ_max, and a smaller margin,
      or a δ_max of a whole period or more, is refused: a grant used after its
      period ends is spend the center no longer counts;
    * clients get it in `hello/1` and wait 3 periods plus δ_max before they treat
      silence as a stop.

  Ignoring a real delivery delay breaks the guarantees: in the simulations a 4 s
  delay (5 s period) made 1.4 % of runs overspend with ε = 10⁻³, and 94 % in
  version (b) when every bid is charged. A delay of minutes that nobody planned
  for is worse: the controller mistakes "no spend yet" for "low k", pushes p to 1,
  and spends several times the budget. Three defences:

    * **Age check (clients).** Messages carry `sent_at`; clients drop those older
      than `max_age_ms` and stop on silence. Needs clocks synchronised within
      `clock_skew_ms`.
    * **Lag measurement (center).** In the token-cap modes clients report the
      last `seq` they applied in every heartbeat. The center knows when it sent
      each `seq`, so it can bound the delay from below with its own clock only. If
      that exceeds δ_max + `lag_allowance_ms`, the center *halts*: it sends p = 0
      and no grants until `resume/1`, and calls `on_alarm`.
    * **Rise limit (controller).** `max_rise` in the controller options limits
      how fast p may grow, which bounds the damage of an undetected delay.

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
    * `:step_ms` - control period in ms (required); must match the `step` of the
      controller and cap options, which are in seconds
    * `:message_delay_max_ms` - largest delay before p or a grant reaches the
      clients (default 0)
    * `:tick` - `:auto` (default: run every `step_ms`) or `:manual` (call `tick/1`)
    * `:lease_ttl_ms` - default `3 * step_ms`
    * `:grant_margin_ms` - default max(`step_ms / 10`, `message_delay_max_ms`); must
      cover the delivery delay of a grant plus clock drift, so it may not be smaller
      than `message_delay_max_ms`
    * `:clock_skew_ms` - allowed difference between the center's and the clients'
      wall clocks, added to δ_max for the age check (default 1000)
    * `:lag_allowance_ms` - how much the measured delay may exceed δ_max before the
      center halts (default `step_ms`)
    * `:on_alarm` - `fn {:lag, client_id, lag_ms} -> any end`, called once when the
      center halts (default: log an error)
    * `:wall_clock` - zero-arity function returning wall-clock ms for `sent_at`
      (default `System.os_time(:millisecond)`)
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

  @doc "Leave the halted state (after a lag alarm) and send p again."
  def resume(center), do: GenServer.call(center, :resume)

  # ------------------------------------------------------------------ server

  @impl true
  def init(opts) do
    ensure_scope()
    mode = Keyword.get(opts, :mode, :broadcast)
    step_ms = Keyword.fetch!(opts, :step_ms)

    if mode != :broadcast and not Keyword.has_key?(opts, :authenticate),
      do: raise(ArgumentError, "#{mode} mode needs :authenticate")

    dmax_ms = Keyword.get(opts, :message_delay_max_ms, 0)

    {ctl_mod, ctl} =
      case Keyword.fetch!(opts, :controller) do
        {mod, state} when is_atom(mod) ->
          {mod, state}

        ctl_opts ->
          check_step!(ctl_opts, step_ms, "controller")

          # a p may keep running until the clients' silence limit if the link drops
          ctl_opts =
            if Keyword.get(opts, :plan_for_lost_link, true),
              do: Keyword.put_new(ctl_opts, :max_silence, (3 * step_ms + dmax_ms) / 1000),
              else: ctl_opts

          {PacingController, PacingController.new(with_delay(ctl_opts, dmax_ms))}
      end

    spend_delay_ms =
      Keyword.get_lazy(opts, :spend_delay_ms, fn ->
        case Keyword.fetch!(opts, :controller) do
          {_mod, _state} ->
            0

          ctl_opts ->
            case Keyword.get(ctl_opts, :delay_pmf) do
              # a varying delay: spend is normally at most the longest delay old
              pmf when is_list(pmf) -> (length(pmf) - 1) * step_ms
              nil -> round(Keyword.get(ctl_opts, :delay, 0) * 1000)
            end
        end
      end)

    cap =
      case mode do
        :broadcast -> nil
        :reports -> Reports.new(Keyword.fetch!(opts, :cap))
        :grants -> Grants.new(check_step!(Keyword.fetch!(opts, :cap), step_ms, "cap"))
      end

    grant_margin_ms = Keyword.get(opts, :grant_margin_ms, max(div(step_ms, 10), dmax_ms))

    if mode == :grants do
      if dmax_ms >= step_ms,
        do: raise(ArgumentError, "grants mode needs message_delay_max_ms < step_ms")

      if grant_margin_ms < dmax_ms,
        do: raise(ArgumentError, "grant_margin_ms must be at least message_delay_max_ms")
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
      grant_margin_ms: grant_margin_ms,
      message_delay_max_ms: dmax_ms,
      max_age_ms: dmax_ms + Keyword.get(opts, :clock_skew_ms, 1_000),
      lag_allowance_ms: Keyword.get(opts, :lag_allowance_ms, step_ms),
      on_alarm: Keyword.get(opts, :on_alarm, &log_alarm/1),
      wall_clock: Keyword.get(opts, :wall_clock, fn -> System.os_time(:millisecond) end),
      sent: %{},
      halted: nil,
      feed_stale: false,
      spend_delay_ms: spend_delay_ms,
      feed_tolerance_ms: Keyword.get(opts, :feed_tolerance_ms, 2 * step_ms),
      wallet_share: Keyword.get(opts, :wallet_share, 0.5),
      store: Keyword.get(opts, :store),
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

    state = restore(state)
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

  def handle_call(:hello, _from, s) do
    reply = %{
      mode: s.mode,
      step_ms: s.step_ms,
      message_delay_max_ms: s.message_delay_max_ms,
      max_age_ms: s.max_age_ms,
      epoch: s.epoch,
      seq: s.seq,
      p: s.p
    }

    {:reply, reply, s}
  end

  def handle_call(:info, _from, s), do: {:reply, s, s}

  def handle_call(:resume, _from, s), do: {:reply, :ok, %{s | halted: nil}}

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

        s = persist(%{s | leases: Map.put(s.leases, id, lease), next_lease: id + 1})

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
        {credited, halted} = {lease.credited, s.halted}
        {lease, s} = credit(s, lease, Map.get(report, :failed), 0)
        s = s |> put_lease(%{lease | last_heartbeat: s.clock.()}) |> check_lag(lease, report)
        s = if lease.credited != credited or s.halted != halted, do: persist(s), else: s
        {:reply, {:ok, s.lease_ttl_ms}, s}

      :error ->
        {:reply, {:error, :lost}, s}
    end
  end

  def handle_call({:request_tokens, id, n, report}, _from, %{mode: :reports} = s) do
    case Map.fetch(s.leases, id) do
      {:ok, lease} ->
        {lease, s} = credit(s, lease, Map.get(report, :failed), 0)
        allowance = Reports.allowance(s.cap)
        # near the end each client may hold only its share of what is left, so a
        # client that is cut off holds back little budget
        share = max(1, ceil(s.wallet_share * allowance / max(map_size(s.leases), 1)))
        give = Enum.min([n, allowance, share])
        cap = Reports.record_use(s.cap, give)
        lease = %{lease | handed: lease.handed + give, last_heartbeat: s.clock.()}
        # written before the tokens leave the center
        s = %{s | cap: cap} |> put_lease(lease) |> check_lag(lease, report) |> persist()
        {:reply, {:ok, give}, s}

      :error ->
        {:reply, {:error, :lost}, s}
    end
  end

  def handle_call({:request_tokens, _, _, _}, _from, s), do: {:reply, {:error, :wrong_mode}, s}

  def handle_call({:deregister, id, report}, _from, s) do
    case Map.fetch(s.leases, id) do
      {:ok, lease} ->
        {_lease, s} = credit(s, lease, Map.get(report, :failed), Map.get(report, :unused, 0))
        {:reply, :ok, persist(%{s | leases: Map.delete(s.leases, id)})}

      :error ->
        {:reply, {:error, :lost}, s}
    end
  end

  # ------------------------------------------------------------------ internals

  defp do_tick(s) do
    s = expire_leases(s)
    {spend, s} = read_feed(s)
    halt? = s.halted != nil or s.feed_stale
    {p, send?, ctl} = s.ctl_mod.step(s.ctl, spend)

    # while halted p = 0 is sent; the controller must count 0 as the p in effect,
    # or its exposure would grow with nothing spent and bias k low
    {p, send?, ctl} =
      if halt?, do: {0.0, true, zero_exposure(s.ctl_mod, ctl)}, else: {p, send?, ctl}

    seq = s.seq + 1
    s = %{s | ctl: ctl, seq: seq, p: p, sent: prune(Map.put(s.sent, seq, s.clock.()), seq)}
    s = if s.mode == :grants, do: s, else: persist(s)
    sent_at = s.wall_clock.()

    msg =
      if send?,
        do: {:pacing_p, s.campaign, s.epoch, seq, p, sent_at},
        else: {:pacing_keepalive, s.campaign, s.epoch, seq, sent_at}

    for pid <- :pg.get_members(@scope, {:pacing, s.campaign}), do: send(pid, msg)

    if s.mode == :grants, do: send_grants(s, spend, sent_at, halt?), else: s
  end

  defp send_grants(s, spend, sent_at, halt?) do
    {g, cap} = Grants.grant(s.cap, spend)

    # halted: grant nothing, and record 0 in the window, since nothing was handed out
    {g, cap} =
      if halt?, do: {0, %{cap | window: List.replace_at(cap.window, -1, 0)}}, else: {g, cap}

    leases = s.leases |> Map.values() |> Enum.sort_by(& &1.id)
    valid_ms = s.step_ms - s.grant_margin_ms
    # the grant window is written before the grants leave the center
    s = persist(%{s | cap: cap})

    for {lease, share} <- Enum.zip(leases, split(g, length(leases))) do
      send(lease.pid, {:pacing_grant, s.campaign, s.epoch, s.seq, share, valid_ms, sent_at})
    end

    %{s | cap: cap}
  end

  # The budget feed may return the spend alone, or {spend, as_of_ms}: the wall-clock
  # time up to which it has processed spend. Spend is normally d old; if it is older
  # than d + feed_tolerance_ms, the feed has stalled and the center halts until it
  # is fresh again.
  defp read_feed(s) do
    case s.visible_spend.() do
      {spend, as_of} ->
        age = s.wall_clock.() - as_of
        stale? = age > s.spend_delay_ms + s.feed_tolerance_ms
        if stale? and not s.feed_stale, do: s.on_alarm.({:stale_budget, age})
        {spend, %{s | feed_stale: stale?}}

      spend ->
        {spend, s}
    end
  end

  defp zero_exposure(mod, ctl) do
    if Code.ensure_loaded?(mod) and function_exported?(mod, :correct_exposure, 2),
      do: mod.correct_exposure(ctl, 0.0),
      else: ctl
  end

  # ---- durable state: whatever has left the center must survive a restart

  @persisted [:ctl_mod, :ctl, :cap, :leases, :lost, :next_lease, :halted]

  defp persist(%{store: nil} = s), do: s

  defp persist(%{store: {mod, arg}} = s) do
    :ok = mod.save(arg, Map.take(s, [:campaign, :mode, :epoch | @persisted]))
    s
  end

  defp restore(%{store: nil} = s), do: s

  defp restore(%{store: {mod, arg}} = s) do
    case mod.load(arg) do
      :none ->
        persist(s)

      {:ok, saved} ->
        if {saved.campaign, saved.mode} != {s.campaign, s.mode},
          do: raise(ArgumentError, "the store holds another campaign or mode")

        now = s.clock.()
        # a new run of the center: a larger epoch, so clients accept its messages;
        # restored leases get a fresh ttl to reconnect, their tokens stay counted
        leases = Map.new(saved.leases, fn {id, l} -> {id, %{l | last_heartbeat: now}} end)

        s
        |> Map.merge(Map.take(saved, @persisted))
        |> Map.merge(%{leases: leases, epoch: max(s.epoch, saved.epoch + 1)})
        |> persist()
    end
  end

  # send times of recent seq, for lag measurement
  defp prune(sent, seq) when map_size(sent) > 2_000,
    do: Map.reject(sent, fn {k, _} -> k < seq - 1_000 end)

  defp prune(sent, _seq), do: sent

  # A heartbeat says the client has applied seq a, so seq a + 1, sent at t, had not
  # arrived when the client wrote it: the delay is at least now - t (an
  # overestimate only by the heartbeat's own travel time). Center clock only.
  defp check_lag(s, lease, %{applied_epoch: epoch, applied_seq: a})
       when epoch == s.epoch and a < s.seq do
    with nil <- s.halted, {:ok, t} <- Map.fetch(s.sent, a + 1) do
      lag = s.clock.() - t

      if lag > s.message_delay_max_ms + s.lag_allowance_ms do
        reason = {:lag, lease.client_id, lag}
        s.on_alarm.(reason)
        %{s | halted: reason}
      else
        s
      end
    else
      _ -> s
    end
  end

  defp check_lag(s, _lease, _report), do: s

  defp log_alarm(reason) do
    require Logger
    Logger.error("PacingCenter halted: delivery delay above plan #{inspect(reason)}")
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

  # the controller's message_delay_max (seconds) comes from message_delay_max_ms
  defp with_delay(ctl_opts, dmax_ms) do
    case Keyword.fetch(ctl_opts, :message_delay_max) do
      :error ->
        Keyword.put(ctl_opts, :message_delay_max, dmax_ms / 1000)

      {:ok, v} when abs(v * 1000 - dmax_ms) < 1.0e-6 ->
        ctl_opts

      {:ok, v} ->
        raise ArgumentError,
              "controller message_delay_max #{v} s differs from message_delay_max_ms #{dmax_ms}"
    end
  end

  defp check_step!(opts, step_ms, what) do
    case Keyword.fetch(opts, :step) do
      {:ok, step} when abs(step * 1000 - step_ms) > 1.0e-6 ->
        raise ArgumentError, "#{what} step #{step} s differs from step_ms #{step_ms}"

      _ ->
        opts
    end
  end

  defp ensure_scope do
    case :pg.start(@scope) do
      {:ok, _} -> :ok
      {:error, {:already_started, _}} -> :ok
    end
  end
end

defmodule PacingCenter.Store do
  @moduledoc """
  Where `PacingCenter` keeps the state that must survive a restart: token counts,
  leases, the grant window and the controller (with its risk used). Without it, a
  restarted center would hand out the same budget again.

  `save/2` is called before tokens, grants or p leave the center, so it must be
  durable when it returns. `load/1` returns `{:ok, state}` or `:none`.
  """

  @callback load(arg :: term) :: {:ok, map} | :none
  @callback save(arg :: term, state :: map) :: :ok
end

defmodule PacingCenter.FileStore do
  @moduledoc """
  `PacingCenter.Store` in one file: `store: {PacingCenter.FileStore, path}`.

  Each save writes a temporary file, syncs it to disk and renames it over the old
  one, so a crash leaves either the old or the new state, never half of one. One
  write per period and per top-up; for many campaigns or very frequent top-ups use
  a database or a replicated store with the same two callbacks.
  """

  @behaviour PacingCenter.Store

  @impl true
  def load(path) do
    case File.read(path) do
      {:ok, bin} -> {:ok, :erlang.binary_to_term(bin)}
      {:error, :enoent} -> :none
    end
  end

  @impl true
  def save(path, state) do
    tmp = path <> ".tmp"
    File.write!(tmp, :erlang.term_to_binary(state), [:sync])
    File.rename!(tmp, path)
  end
end
