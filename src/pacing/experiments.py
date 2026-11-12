"""Monte Carlo experiments. Run:  uv run python -m pacing.experiments [--quick]

Writes results/summary.md and results/*.png.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.stats import norm  # noqa: E402

from .controllers import BayesRiskController, NaiveController, PluginController  # noqa: E402
from .model import Scenario, simulate  # noqa: E402

OUT = Path("results")
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2,
    "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "legend.frameon": False,
})


def bayes(scn: Scenario, eps: float, **kw) -> BayesRiskController:
    kw.setdefault("prior_mean", scn.spend_rate_at_p1)
    return BayesRiskController(scn.center_view(), eps=eps, **kw)


def fmt_row(name: str, s: dict) -> str:
    over = f"{s['overspend_prob']:.2e}" if s["overspend_prob"] else "0"
    return (f"| {name} | {over} | {s['overspend_prob_upper95']:.1e} | "
            f"{s['mean_overspend_units_when_over']:.1f} | {s['mean_underspend_pct']:.3f} | "
            f"{s['p95_underspend_pct']:.3f} | {s['mean_max_pace_dev_pct']:.2f} |")


HEADER = ("| strategy | P(overspend) | 95% upper bound | mean overspend, units (if over) | "
          "mean underspend % | p95 underspend % | max pacing deviation % |\n"
          "|---|---|---|---|---|---|---|")


def theory_shortfall(scn: Scenario, eps: float) -> float:
    """Rough steady-pacing shortfall: z_{1-eps} * sd of the spend in flight at the end."""
    rate = scn.budget / scn.horizon
    window = scn.delay_steps * scn.step + scn.step
    return norm.isf(eps) * np.sqrt(rate * window)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="fewer runs, for a smoke test")
    args = ap.parse_args()
    n = 4_000 if args.quick else 40_000
    n_big = 20_000 if args.quick else 400_000
    OUT.mkdir(exist_ok=True)
    scn = Scenario()
    k = scn.spend_rate_at_p1
    eps = 1e-3
    md: list[str] = []
    t_start = time.time()

    md += ["# Results", "",
           f"Default scenario: B = {scn.budget} units, T = {scn.horizon:.0f} s, "
           f"lambda = {scn.request_rate:.0f} req/s, r = {scn.realization}, "
           f"d = {scn.delay:.0f} s, control step = {scn.step:.0f} s. "
           f"Needed p ~ {scn.budget / (k * scn.horizon):.3f}. "
           f"Overspend tolerance eps = {eps:g}. {n} runs per row unless stated.", ""]

    # ---- E1: strategies
    rows = {
        "Naive (ignores in-flight spend)": NaiveController(scn.center_view(), prior_mean=k),
        "Plug-in reserve, eps checked per step": PluginController(scn.center_view(), prior_mean=k, eps=eps),
        "**Proposed: Bayes + risk budget**": bayes(scn, eps),
        "Proposed + 300 s end-game": bayes(scn, eps, endgame_time=300),
        "Oracle (knows lambda*r exactly)": bayes(scn, eps, known_rate=k),
    }
    md += ["## 1. Strategies compared", "", HEADER]
    traj = {}
    for name, c in rows.items():
        res = simulate(scn, c, n, seed=1, keep=5)
        traj[name] = res
        md.append(fmt_row(name, res.summary(scn.budget)))
    md.append("")

    # ---- E2: is eps really honoured? (large run, smaller eps)
    md += ["## 2. Overspend tolerance holds", "",
           f"Proposed controller, {n_big} runs per row.", "", HEADER]
    for e in (1e-2, 1e-3, 1e-4):
        s = simulate(scn, bayes(scn, e), n_big, seed=2).summary(scn.budget)
        md.append(fmt_row(f"eps = {e:g}", s))
    md.append("")

    # ---- E3: delay sweep
    delays = [0, 5, 15, 30, 60, 120, 300]
    under_d, theory_d = [], []
    md += ["## 3. Effect of the delay d", "", HEADER]
    for d in delays:
        sc = replace(scn, delay=float(d))
        s = simulate(sc, bayes(sc, eps), n, seed=3).summary(sc.budget)
        under_d.append(s["mean_underspend_pct"])
        theory_d.append(100 * theory_shortfall(sc, eps) / sc.budget)
        md.append(fmt_row(f"d = {d} s", s))
    md.append("")

    # ---- E4: eps sweep
    epss = [1e-1, 1e-2, 1e-3, 1e-4, 1e-5, 1e-6]
    under_e, over_e = [], []
    md += ["## 4. Effect of the overspend tolerance eps", "", HEADER]
    for e in epss:
        s = simulate(scn, bayes(scn, e), n, seed=4).summary(scn.budget)
        under_e.append(s["mean_underspend_pct"])
        over_e.append(s["overspend_prob"])
        md.append(fmt_row(f"eps = {e:g}", s))
    md.append("")

    # ---- E5: end-game trade-off (evenness vs underspend)
    egs = [0, 30, 60, 120, 300, 600]
    eg_under, eg_dev = [], []
    md += ["## 5. End-game time: evenness vs underspend", "", HEADER]
    for eg in egs:
        s = simulate(scn, bayes(scn, eps, endgame_time=eg), n, seed=5).summary(scn.budget)
        eg_under.append(s["mean_underspend_pct"])
        eg_dev.append(s["mean_max_pace_dev_pct"])
        md.append(fmt_row(f"end-game {eg} s", s))
    md.append("")

    # ---- E6: wrong initial guess of lambda*r
    md += ["## 6. Wrong initial guess of lambda*r (true rate fixed)", "", HEADER]
    for f in (0.1, 0.5, 2.0, 10.0):
        s = simulate(scn, bayes(scn, eps, prior_mean=k * f), n, seed=6).summary(scn.budget)
        md.append(fmt_row(f"guess = {f:g} x true", s))
    md.append("")

    # ---- E7: eta
    md += ["## 7. Risk-spending rate eta (share of the unused risk budget one step may use)", "", HEADER]
    for eta in (0.1, 0.25, 0.5, 0.9, 1.0):
        s = simulate(scn, bayes(scn, eps, eta=eta), n, seed=7).summary(scn.budget)
        md.append(fmt_row(f"eta = {eta:g}", s))
    md.append("")
    md.append(f"_Total compute: {time.time() - t_start:.0f} s._")
    (OUT / "summary.md").write_text("\n".join(md) + "\n")

    # ---- figures
    _fig_trajectories(scn, traj["**Proposed: Bayes + risk budget**"], traj["Proposed + 300 s end-game"])
    _fig_delay(delays, under_d, theory_d, scn)
    _fig_eps(epss, under_e)
    _fig_endgame(egs, eg_under, eg_dev)
    print((OUT / "summary.md").read_text())


def _fig_trajectories(scn, res_plain, res_eg):
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    t = res_plain.traj_time
    for res, color, label in ((res_plain, BLUE, "no end-game"), (res_eg, ORANGE, "300 s end-game")):
        for j in range(res.traj_p.shape[0]):
            kw = {"label": label} if j == 0 else {}
            axes[0].step(t[:-1], res.traj_p[j], where="post", color=color, lw=1, alpha=0.8, **kw)
            axes[1].plot(t, res.traj_spend[j], color=color, lw=1.2, alpha=0.8, **kw)
            axes[2].plot(t, scn.budget - res.traj_spend[j], color=color, lw=1.2, alpha=0.8, **kw)
    axes[0].set(title="Broadcast p", xlabel="time, s", ylabel="p")
    axes[1].plot([0, scn.horizon], [0, scn.budget], color=INK2, lw=1, ls="--", label="even pace")
    axes[1].set(title="Cumulative spend", xlabel="time, s", ylabel="units")
    axes[2].axhline(0, color=INK, lw=1)
    axes[2].set(title="Budget left, last 10 minutes", xlabel="time, s", ylabel="units",
                xlim=(scn.horizon - 600, scn.horizon), ylim=(-5, 200))
    for ax in axes:
        ax.legend(loc="best", fontsize=8)
    fig.suptitle("5 sample runs of the proposed controller (eps = 1e-3, d = 30 s)", x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(OUT / "trajectories.png", dpi=140)
    plt.close(fig)


def _fig_delay(delays, under, theory, scn):
    fig, ax = plt.subplots(figsize=(6, 3.8))
    ax.plot(delays, under, "-o", color=BLUE, ms=6, label="simulated (mean)")
    ax.plot(delays, theory, "--", color=ORANGE, label="z(1-eps) * sqrt(rate * (d + step))")
    ax.set(title="Underspend grows like the square root of the delay", xlabel="delay d, s",
           ylabel="underspend, % of budget")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "delay.png", dpi=140)
    plt.close(fig)


def _fig_eps(epss, under):
    fig, ax = plt.subplots(figsize=(6, 3.8))
    ax.semilogx(epss, under, "-o", color=BLUE, ms=6)
    ax.invert_xaxis()
    ax.set(title="Cost of a stricter overspend tolerance", xlabel="eps (P(overspend) allowed)",
           ylabel="mean underspend, % of budget")
    fig.tight_layout()
    fig.savefig(OUT / "eps.png", dpi=140)
    plt.close(fig)


def _fig_endgame(egs, under, dev):
    fig, ax = plt.subplots(figsize=(6, 3.8))
    ax.plot(dev, under, "-o", color=BLUE, ms=6)
    for x, y, eg in zip(dev, under, egs):
        ax.annotate(f"{eg} s", (x, y), textcoords="offset points", xytext=(6, 4), fontsize=8, color=INK2)
    ax.set(title="End-game time trades evenness for budget use",
           xlabel="max deviation from even pace, % of budget", ylabel="mean underspend, % of budget")
    fig.tight_layout()
    fig.savefig(OUT / "endgame.png", dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    main()
