"""Token-lease backstop: a hard cap that makes overspend impossible.

Models: models/token-cap-a.md (version a), models/token-cap-b.md (version b)
-----------------------------------------------------------------------------
* A client may bid only while it holds a token. Each bid uses one token.
* A used token is realised (c units of spend, visible after d) with probability r,
  otherwise it fails. Failures are reported to the center after ``failure_delay``
  (d_f). A token that has not failed yet may still be realised.
* At the start of each control step the center grants an allowance

      A = B/c + F_obs - U      (U = tokens used so far, F_obs = failures reported)

  and clients may use at most A tokens during the step; further bids are
  blocked. Hence realised spend <= c (U - F_total) <= c (U - F_obs) <= B at all times:
  **overspend is impossible**, whatever the traffic, the delay or the controller.
* Clients also report how many bids were blocked, so the center knows the
  effective exposure p * (used / wanted) * step and its estimate of k stays unbiased.

The probabilistic controller still chooses p. The backstop only blocks bids
when its allowance runs out; that is the cost measured here.

Run:  uv run python -m pacing.backstop [--quick]
      -> results/token-cap-a.md, results/token-cap-a.png, results/token-cap-b.md
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from .controllers import BayesRiskController, NaiveController
from .model import Controller, Scenario, SimResult


@dataclass
class BackstopResult(SimResult):
    blocked_bids: np.ndarray = None  # bids blocked by the cap, per run
    cap_bound: np.ndarray = None  # whether the cap ever blocked a bid, per run


def simulate_with_backstop(
    scn: Scenario,
    controller: Controller,
    n_runs: int,
    failure_delay: float = 0.0,
    seed: int = 0,
    cap: bool = True,
    mode: str = "reports",
    grant_share: float | None = None,
    grant_headroom: float | None = None,
    trace: list | None = None,
) -> BackstopResult:
    """Token-level simulation. ``cap=False`` gives the same process without the backstop.

    mode = "reports"  version (a): clients report failures (after ``failure_delay``),
                      tokens used and bids blocked. Allowance A = B/c + F_obs - U.
    mode = "grants"   version (b): no reports. Each step the center grants G tokens
                      that expire at the end of the step, with
                      G <= A = (B - S_obs)/c - (tokens granted in the last d seconds).
                      Grant rule: ``grant_share`` s gives G = ceil(s * A) (needs no
                      information); ``grant_headroom`` h gives G = min(A, ceil(h * lambda
                      * p * step)), i.e. it assumes the center knows the bid demand
                      (as it would from usage counts). Without reports the center
                      cannot correct its exposure for blocked bids.

    ``trace``, if a list, receives one dict per control step for run 0 (what the
    center saw and decided); used to generate the Elixir golden tests.

    Delivery delay (``scn.message_delay`` delta, planned bound ``message_delay_max``
    delta_max): clients use the p decided delta seconds ago, and the center's
    in-flight window is ``scn.center_delay_steps``. Version (a) counts tokens when
    they are used, so its cap needs no timing assumption. In version (b) a grant
    arrives delta late and is usable for step - delta_max after it arrives (the
    grant margin is delta_max); if delta > delta_max, it spills into the next step.
    Version (b) needs delta and delta_max shorter than a step.
    """
    if mode not in ("reports", "grants"):
        raise ValueError("mode must be 'reports' or 'grants'")
    if mode == "grants" and (grant_share is None) == (grant_headroom is None):
        raise ValueError("version (b) needs exactly one of grant_share / grant_headroom")
    if cap and mode == "grants" and max(scn.message_delay, scn.message_delay_max) >= scn.step:
        raise ValueError("version (b) needs delta and delta_max shorter than a step")
    rng = np.random.default_rng(seed)
    n_steps, d_steps, dt = scn.n_steps, scn.delay_steps, scn.step
    f_steps = math.ceil(failure_delay / dt - 1e-9)
    lam, r, c = scn.request_rate, scn.realization, scn.unit_cost
    budget = math.floor(scn.budget_events + 1e-9)  # B/c: whole tokens the budget can pay for

    ring = d_steps + 1
    ring_x = np.zeros((n_runs, ring), dtype=np.int64)  # realised tokens per step
    cd_steps = scn.center_delay_steps  # the center's in-flight window for p
    pring = cd_steps + 1
    ring_p = np.zeros((n_runs, pring))  # effective p per step (after blocking)
    instant = scn.message_delay == 0 and scn.message_delay_max == 0
    m_steps = int(scn.message_delay // dt)
    m_frac = scn.message_delay - m_steps * dt
    hist = m_steps + 2
    ring_dec = np.zeros((n_runs, hist))  # decided p, for the p clients actually use
    margin = scn.message_delay_max  # (b): a grant is usable for step - margin after it arrives
    leftover = np.zeros(n_runs, dtype=np.int64)  # (b): tokens of the last grant still usable
    fring = f_steps + 1
    ring_f = np.zeros((n_runs, fring), dtype=np.int64)  # failures per step
    ring_g = np.zeros((n_runs, ring), dtype=np.int64)  # (b): tokens granted per step
    granted_unresolved = np.zeros(n_runs, dtype=np.int64)  # (b): granted in the last d s

    obs_spend = np.zeros(n_runs, dtype=np.int64)  # visible realised tokens (spend / c)
    obs_exposure = np.zeros(n_runs)
    inflight = np.zeros(n_runs)
    used = np.zeros(n_runs, dtype=np.int64)  # U
    fail_obs = np.zeros(n_runs, dtype=np.int64)  # F_obs
    true_spend = np.zeros(n_runs, dtype=np.int64)
    blocked = np.zeros(n_runs, dtype=np.int64)
    max_dev = np.zeros(n_runs)

    controller.reset(n_runs)
    for i in range(n_steps):
        seen = float(c * obs_spend[0])
        p = np.clip(controller.decide(i * dt, c * obs_spend, obs_exposure, inflight), 0.0, 1.0)
        ring_dec[:, i % hist] = p
        if instant:
            def bids(lo, hi):  # noqa: E306  bids the clients want during [lo, hi) of this step
                return rng.poisson(lam * p * dt)
        else:
            # clients use p_a during [0, m_frac) of this step and p_b after it
            p_a = ring_dec[:, (i - m_steps - 1) % hist] if i - m_steps - 1 >= 0 else 0.0
            p_b = ring_dec[:, (i - m_steps) % hist] if i - m_steps >= 0 else 0.0

            def bids(lo, hi):  # noqa: E306
                a = max(0.0, min(hi, m_frac) - lo)
                b = max(0.0, hi - max(lo, m_frac))
                return rng.poisson(lam * (p_a * a + p_b * b))
        grant = None
        if not cap:
            wanted = bids(0.0, dt)
            tokens = wanted
        elif mode == "reports":
            wanted = bids(0.0, dt)
            allowance = np.maximum(budget + fail_obs - used, 0)
            tokens = np.minimum(wanted, allowance)
        else:
            allowance = np.maximum(budget - obs_spend - granted_unresolved, 0)
            if grant_share is not None:
                grant = np.ceil(grant_share * allowance).astype(np.int64)
            else:
                grant = np.ceil(grant_headroom * lam * p * dt).astype(np.int64)
            grant = np.minimum(grant, allowance)
            if instant:
                wanted = bids(0.0, dt)
                tokens = np.minimum(wanted, grant)
            else:
                dl = scn.message_delay
                spill = max(0.0, dl - margin)  # the last grant is still usable until here
                end = min(dt, dl + dt - margin)  # this grant: usable during [dl, end)
                w_old, w_new = bids(0.0, spill), bids(dl, end)
                w_none = bids(spill, dl) + bids(end, dt)  # no valid grant: blocked
                t_old = np.minimum(w_old, leftover) if spill > 0 else 0
                t_new = np.minimum(w_new, grant)
                leftover = grant - t_new
                wanted = w_old + w_new + w_none
                tokens = t_old + t_new
        blocked += wanted - tokens
        x = rng.binomial(tokens, r)  # realised
        used += tokens
        true_spend += x
        if mode == "reports" or not cap:
            p_eff = np.where(wanted > 0, p * tokens / np.maximum(wanted, 1), p)
        else:
            p_eff = p  # (b): blocked bids are not reported, exposure is not corrected

        slot = i % ring
        if grant is not None:
            ring_g[:, slot] = grant
            granted_unresolved += grant
        ring_p[:, i % pring] = p_eff
        ring_x[:, slot] = x
        inflight += p_eff * dt
        fslot = i % fring
        ring_f[:, fslot] = tokens - x

        j = i - d_steps
        if j >= 0:
            sj = j % ring
            obs_spend += ring_x[:, sj]
            granted_unresolved -= ring_g[:, sj]  # its spend is now visible
        j = i - cd_steps
        if j >= 0:
            obs_exposure += ring_p[:, j % pring] * dt
            inflight -= ring_p[:, j % pring] * dt
            np.maximum(inflight, 0.0, out=inflight)
        jf = i - f_steps
        reported = ring_f[:, jf % fring].copy() if jf >= 0 else np.zeros(n_runs, dtype=np.int64)
        fail_obs += reported
        if trace is not None:
            trace.append({"observed_spend": seen, "p": float(p[0]), "wanted": int(wanted[0]),
                          "allowance": int(allowance[0]) if cap else None,
                          "grant": None if grant is None else int(grant[0]),
                          "tokens": int(tokens[0]), "failures_reported": int(reported[0])})

        dev = np.abs(true_spend / scn.budget_events - (i + 1) / n_steps)
        np.maximum(max_dev, dev, out=max_dev)

    return BackstopResult(
        final_spend=c * true_spend,
        overspent=true_spend > scn.budget_events + 1e-9,
        max_pace_deviation=max_dev,
        blocked_bids=blocked,
        cap_bound=blocked > 0,
    )


# =========================================================================== experiment
def main() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .experiments import BLUE, INK2, ORANGE, AQUA  # noqa: F401  (also applies rcParams)

    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    n = 3_000 if args.quick else 20_000
    out = Path("results")
    out.mkdir(exist_ok=True)

    base = Scenario()
    k = base.spend_rate_at_p1  # keep k = lambda * r = 100 while r varies
    rs = (0.1, 0.5, 1.0)
    dfs = (0.0, 5.0, 30.0)

    def bayes(scn, eps, **kw):
        return BayesRiskController(scn.center_view(), prior_mean=k, eps=eps, **kw)

    strategies = {
        "Bayes ε=1e-3, no cap": (lambda s: bayes(s, 1e-3), False),
        "Bayes ε=1e-3 + cap": (lambda s: bayes(s, 1e-3), True),
        "Bayes ε=1e-9 + cap": (lambda s: bayes(s, 1e-9), True),
        "Bayes ε=1e-9 + cap + 60 s end-game": (lambda s: bayes(s, 1e-9, endgame_time=60), True),
        "Cap only (pacing, no risk check)": (lambda s: NaiveController(s.center_view(), prior_mean=k), True),
    }

    md = ["# Results: token cap, version (a), failure reports", "",
          "Model: [models/token-cap-a.md](../models/token-cap-a.md). "
          "Generated by `uv run python -m pacing.backstop`.", "",
          f"Default scenario (B = {base.budget}, T = {base.horizon:.0f} s, d = {base.delay:.0f} s, "
          f"step = {base.step:.0f} s), k = λ·r = {k:.0f} kept fixed while r varies, "
          f"so the spend process is the same in every row; only the share of bids "
          f"that fail (1 − r) and how fast failures are reported (d_f) change. "
          f"{n} runs per row.", "",
          "| r | d_f | strategy | overspends | mean underspend % | p95 underspend % | "
          "runs where cap blocked bids | max pacing deviation % |",
          "|---|---|---|---|---|---|---|---|"]
    chart: dict[str, list[tuple[float, float]]] = {}
    for r in rs:
        scn = replace(base, request_rate=k / r, realization=r)
        for df in dfs:
            for name, (make, cap) in strategies.items():
                if not cap and df != dfs[0]:
                    continue  # without the cap d_f plays no role
                res = simulate_with_backstop(scn, make(scn), n, failure_delay=df, seed=7, cap=cap)
                s = res.summary(scn.budget)
                md.append(
                    f"| {r:g} | {'–' if not cap else f'{df:g} s'} | {name} | {int(res.overspent.sum())} | "
                    f"{s['mean_underspend_pct']:.2f} | {s['p95_underspend_pct']:.2f} | "
                    f"{100 * res.cap_bound.mean():.1f} % | {s['mean_max_pace_dev_pct']:.2f} |")
                if cap and r == 0.1:
                    chart.setdefault(name, []).append((df, s["mean_underspend_pct"]))
                if not cap and r == 0.1:
                    baseline = s["mean_underspend_pct"]
        md.append("|  |  |  |  |  |  |  |  |")
    md += ["", "![Cost of the cap against the failure-report delay, r = 0.1](token-cap-a.png)"]
    (out / "token-cap-a.md").write_text("\n".join(md) + "\n")

    # ---- version (b): no reports from clients, grants expire after one step
    md = ["# Results: token cap, version (b), no reports from clients", "",
          "Model: [models/token-cap-b.md](../models/token-cap-b.md). "
          "Generated by `uv run python -m pacing.backstop`.", "",
           "Each step the center grants tokens that expire at the end of the step, at most "
           "A = B − S_obs − (tokens granted in the last d seconds). Clients report nothing. "
           "Grant rules: *share 0.5* grants half of A (needs no information). "
           "*Headroom 1.5* grants 1.5 × the expected bid demand λ·p·Δ, which assumes the "
           "center knows the demand, for example from usage counts. For comparison, "
           "version (a) cap only with d_f = d = 30 s.", "",
           "| r | strategy | overspends | mean underspend % | p95 underspend % | "
           "runs where cap blocked bids | max pacing deviation % |",
           "|---|---|---|---|---|---|---|"]
    b_strategies = {
        "(b) share 0.5, cap only": (dict(grant_share=0.5), 0.0),
        "(b) share 0.5, cap only + 60 s end-game": (dict(grant_share=0.5), 60.0),
        "(b) headroom 1.5, cap only": (dict(grant_headroom=1.5), 0.0),
        "(b) headroom 1.5, cap only + 60 s end-game": (dict(grant_headroom=1.5), 60.0),
    }
    compare: dict[float, dict[str, float]] = {}
    for r in rs:
        scn = replace(base, request_rate=k / r, realization=r)
        rows = []
        for name, (kw, eg) in b_strategies.items():
            c = NaiveController(scn.center_view(), prior_mean=k, endgame_time=eg)
            rows.append((name, simulate_with_backstop(scn, c, n, seed=7, mode="grants", **kw)))
        c = NaiveController(scn.center_view(), prior_mean=k)
        rows.append(("(a) d_f = 30 s, cap only (reference)",
                     simulate_with_backstop(scn, c, n, failure_delay=30.0, seed=7)))
        for name, res in rows:
            s = res.summary(scn.budget)
            compare.setdefault(r, {})[name] = s["mean_underspend_pct"]
            md.append(f"| {r:g} | {name} | {int(res.overspent.sum())} | {s['mean_underspend_pct']:.2f} | "
                      f"{s['p95_underspend_pct']:.2f} | {100 * res.cap_bound.mean():.1f} % | "
                      f"{s['mean_max_pace_dev_pct']:.2f} |")
        md.append("|  |  |  |  |  |  |  |")
    md += ["", "![Version (b) against version (a), by share of bids charged](token-cap-b-compare.png)"]

    d1 = base.delay_steps + 1
    share_pts: dict[float, list[tuple[float, float]]] = {}
    shares = [m / d1 for m in range(1, d1)]
    md += ["", "## Grant share s (cap only, plain pacing)", "",
           "| r | " + " | ".join(f"s = {x:.2f}" for x in shares) + " |",
           "|---|" + "---|" * len(shares)]
    for r in rs:
        scn = replace(base, request_rate=k / r, realization=r)
        cells = []
        for x in shares:
            res = simulate_with_backstop(scn, NaiveController(scn.center_view(), prior_mean=k), n,
                                         seed=7, mode="grants", grant_share=x)
            s = res.summary(scn.budget)
            share_pts.setdefault(r, []).append((x, s["mean_underspend_pct"]))
            cells.append(f"{s['mean_underspend_pct']:.2f} % (dev {s['mean_max_pace_dev_pct']:.1f} %)")
        md.append(f"| {r:g} | " + " | ".join(cells) + " |")
    md += ["", "Cells: mean underspend (max pacing deviation).", "",
           "![Underspend against the grant share s](token-cap-b-share.png)"]
    (out / "token-cap-b.md").write_text("\n".join(md) + "\n")
    _fig_b_compare(compare, out / "token-cap-b-compare.png")
    _fig_b_share(share_pts, out / "token-cap-b-share.png")

    fig, ax = plt.subplots(figsize=(6.5, 3.9))
    colors = [BLUE, ORANGE, AQUA, "#eda100"]
    for (name, pts), color in zip(chart.items(), colors):
        xs, ys = zip(*pts)
        ax.plot(xs, ys, "-o", color=color, ms=6, label=name)
    ax.axhline(baseline, color=INK2, lw=1, ls="--", label="Bayes ε=1e-3, no cap (may overspend)")
    ax.set(title="Cost of a hard cap when 90 % of bids fail (r = 0.1)",
           xlabel="delay of failure reports d_f, s", ylabel="mean underspend, % of budget")
    ax.legend(fontsize=7.5, loc="upper left")
    fig.tight_layout()
    fig.savefig(out / "token-cap-a.png", dpi=140)
    plt.close(fig)
    print((out / "token-cap-a.md").read_text())
    print((out / "token-cap-b.md").read_text())


def _fig_b_compare(compare, path):
    import matplotlib.pyplot as plt

    from .experiments import AQUA, BLUE, INK2, ORANGE, YELLOW

    names = ["(b) share 0.5, cap only", "(b) share 0.5, cap only + 60 s end-game",
             "(b) headroom 1.5, cap only", "(a) d_f = 30 s, cap only (reference)"]
    labels = ["(b) share 0.5", "(b) share 0.5 + 60 s end-game", "(b) headroom 1.5 (knows demand)",
              "(a) failure reports after 30 s"]
    colors = [BLUE, ORANGE, AQUA, YELLOW]
    rs = sorted(compare)
    fig, axes = plt.subplots(1, len(rs), figsize=(11, 3.6))
    for ax, r in zip(axes, rs):
        vals = [compare[r][n] for n in names]
        bars = ax.bar(range(len(vals)), vals, color=colors, width=0.7, edgecolor="#fcfcfb", linewidth=2)
        for b, v in zip(bars, vals):
            ax.annotate(f"{v:.2f}", (b.get_x() + b.get_width() / 2, v), textcoords="offset points",
                        xytext=(0, 3), ha="center", fontsize=8, color=INK2)
        ax.set_xticks([])
        ax.set_axisbelow(True)
        ax.set_title(f"r = {r:g}" + ("  (every bid charged)" if r == 1 else ""))
        ax.set_ylim(0, max(max(vals) * 1.25, 0.05))
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("mean underspend, % of budget")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in colors]
    fig.legend(handles, labels, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle("Token cap without client reports (b) vs with failure reports (a); "
                 "each panel has its own scale", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _fig_b_share(share_pts, path):
    import matplotlib.pyplot as plt

    from .experiments import BLUE, INK2

    rs = sorted(share_pts)
    fig, axes = plt.subplots(1, len(rs), figsize=(11, 3.4))
    for ax, r in zip(axes, rs):
        xs, ys = zip(*share_pts[r])
        ax.plot(xs, ys, "-o", color=BLUE, ms=6)
        ax.axvline(0.5, color=INK2, lw=1, ls="--")
        ax.annotate("s = 0.5", (0.5, max(ys)), textcoords="offset points", xytext=(4, -10),
                    fontsize=8, color=INK2)
        ax.set_title(f"r = {r:g}")
        ax.set_xlabel("grant share s")
        ax.set_ylim(0, max(ys) * 1.15)
    axes[0].set_ylabel("mean underspend, % of budget")
    fig.suptitle("Version (b): a larger grant share leaves less unspent; above s ≈ 0.7 spending "
                 "becomes bursty (see table). Each panel has its own scale", x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    main()
