"""One chart comparing the three models in the default scenario, by r and by c (for README.md).

Run:  uv run python -m pacing.overview [--quick]
      -> results/overview.md, results/overview.png
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np

from .backstop import simulate_with_backstop
from .controllers import BayesRiskController, NaiveController
from .experiments import AQUA, INK, INK2, ORANGE, OUT, YELLOW, plt
from .model import Scenario, simulate

RS = (0.1, 0.5, 1.0)  # share of bids charged, at c = 1
CS = (0.1, 0.3, 1.0, 3.0, 10.0)  # cost per realised token, at r = 0.1
MODELS = (
    "Probabilistic\nε = 10⁻³",
    "Token cap (a)\nreports after 5 s",
    "Token cap (a)\nreports after 30 s",
    "Token cap (b)\nno reports",
)
SHORT = ("Probabilistic", "Cap (a)\n5 s", "Cap (a)\n30 s", "Cap (b)")
R_SHADES = ("#a9c9f0", "#5b9be3", "#1f5fa8")  # r: light -> dark, one hue (ordinal)
MODEL_COLORS = (INK, ORANGE, AQUA, YELLOW)  # c panel: one colour per model


def _cell(r: float, c: float, n: int) -> dict[str, tuple[float, int, int]]:
    """All models for one (r, c): model -> (mean underspend %, overspends, runs).

    k = lambda * r = 100 / c spends per second, so the money rate at p = 1 and the
    needed p (~0.014) stay the same whatever r and c are.
    """
    base = Scenario()
    scn = replace(base, unit_cost=c, realization=r, request_rate=base.request_rate * base.realization / (r * c))
    k = scn.spend_rate_at_p1
    naive = lambda: NaiveController(scn.center_view(), prior_mean=k)  # noqa: E731
    results = {
        MODELS[0]: simulate(scn, BayesRiskController(scn.center_view(), prior_mean=k, eps=1e-3), n, seed=7),
        MODELS[1]: simulate_with_backstop(scn, naive(), n, failure_delay=5.0, seed=7),
        MODELS[2]: simulate_with_backstop(scn, naive(), n, failure_delay=30.0, seed=7),
        MODELS[3]: simulate_with_backstop(scn, naive(), n, seed=7, mode="grants", grant_share=0.5),
    }
    return {m: (res.summary(scn.budget)["mean_underspend_pct"], int(res.overspent.sum()), n)
            for m, res in results.items()}


def run(n: int) -> dict[tuple[float, float], dict[str, tuple[float, int, int]]]:
    """(r, c) -> model -> (mean underspend %, overspends, runs)."""
    cells = {(r, 1.0) for r in RS} | {(0.1, c) for c in CS}
    return {rc: _cell(*rc, n) for rc in sorted(cells)}


def _pooled(data, m) -> tuple[int, int]:
    return sum(v[m][1] for v in data.values()), sum(v[m][2] for v in data.values())


def chart(data) -> None:
    fig = plt.figure(figsize=(12, 8.4))
    gs = fig.add_gridspec(2, 3, width_ratios=[2, 0.15, 1])
    ax_u, ax_o, ax_c = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 2]), fig.add_subplot(gs[1, :])
    x = np.arange(len(MODELS))
    w = 0.26
    for i, (r, color) in enumerate(zip(RS, R_SHADES)):
        vals = [data[(r, 1.0)][m][0] for m in MODELS]
        bars = ax_u.bar(x + (i - 1) * w, vals, w * 0.92, color=color, label=f"r = {r:g}")
        ax_u.bar_label(bars, fmt="%.2f", fontsize=7, color=INK2, padding=2)
    ax_u.set_xticks(x, MODELS, fontsize=8)
    ax_u.set(title="Underspend by share of bids charged r (c = 1)", ylabel="mean underspend, % of budget")
    ax_u.legend(title="share of bids charged", fontsize=8, title_fontsize=8)

    rates = [100 * o / n for o, n in (_pooled(data, m) for m in MODELS)]
    bars = ax_o.bar(x, rates, 0.6, color=INK2)
    ax_o.bar_label(bars, labels=[f"{v:.2f} %" if v else "0" for v in rates],
                   fontsize=7, color=INK2, padding=2)
    ax_o.set_xticks(x, SHORT, fontsize=8)
    ax_o.set(title="Overspend (all runs pooled)", ylabel="runs that overspent, %",
             ylim=(0, max(rates) * 1.6 or 1))

    for m, color, short in zip(MODELS, MODEL_COLORS, SHORT):
        ys = [data[(0.1, c)][m][0] for c in CS]
        ax_c.plot(CS, ys, "-o", color=color, ms=6, lw=2, label=m.replace("\n", ", "))
        ax_c.annotate(short.replace("\n", " "), (CS[-1], ys[-1]), textcoords="offset points",
                      xytext=(8, 0), va="center", fontsize=8, color=INK2)
    ax_c.set_xscale("log")
    ax_c.set_xticks(CS, [f"{c:g}" for c in CS])
    ax_c.minorticks_off()
    ax_c.set_xlim(CS[0] / 1.4, CS[-1] * 3)
    ax_c.set(title="Underspend by cost per realised token c (r = 0.1): the caps barely depend on c, "
                   "the probabilistic controller grows like √c",
             xlabel="cost per realised token c, budget units (log scale)",
             ylabel="mean underspend, % of budget")
    ax_c.legend(fontsize=8, loc="center left", bbox_to_anchor=(0.01, 0.55))
    for ax in (ax_u, ax_o):
        ax.grid(axis="x", visible=False)
    for ax in (ax_u, ax_o, ax_c):
        ax.set_axisbelow(True)

    fig.suptitle("Three models, default scenario (B = 5000, T = 1 h, d = 30 s, "
                 "money rate at p = 1: c·k = 100 units/s)", x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(OUT / "overview.png", dpi=140)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    n = 3_000 if ap.parse_args().quick else 20_000
    OUT.mkdir(exist_ok=True)
    data = run(n)
    chart(data)
    md = ["# Results: the three models side by side", "",
          "Generated by `uv run python -m pacing.overview`. Default scenario (B = 5000, T = 3600 s, "
          "d = 30 s, step = 5 s). The money rate at p = 1 is kept at c·λ·r = 100 units/s while "
          "r (share of bids charged) and c (cost per realised token) vary, so the needed p stays "
          f"~0.014. {n} runs per cell. Token-cap rows use plain pacing with the cap "
          "(*cap only* in the token-cap results). Details: "
          "[probabilistic](probabilistic.md), [token cap (a)](token-cap-a.md), "
          "[token cap (b)](token-cap-b.md).", "",
          "## Mean underspend %, by share of bids charged r (c = 1)", "",
          "| model | " + " | ".join(f"r = {r:g}" for r in RS) + " |",
          "|---|" + "---|" * len(RS)]
    for m in MODELS:
        md.append(f"| {m.replace(chr(10), ', ')} | " + " | ".join(f"{data[(r, 1.0)][m][0]:.2f}" for r in RS) + " |")
    md += ["", "## Mean underspend %, by cost per realised token c (r = 0.1)", "",
           "| model | " + " | ".join(f"c = {c:g}" for c in CS) + " |",
           "|---|" + "---|" * len(CS)]
    for m in MODELS:
        md.append(f"| {m.replace(chr(10), ', ')} | " + " | ".join(f"{data[(0.1, c)][m][0]:.2f}" for c in CS) + " |")
    md += ["", "## Overspends, all cells pooled", "", "| model | overspends / runs |", "|---|---|"]
    for m in MODELS:
        o, n_ = _pooled(data, m)
        md.append(f"| {m.replace(chr(10), ', ')} | {o} / {n_} |")
    md += ["", "![The three models side by side](overview.png)", ""]
    (OUT / "overview.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
