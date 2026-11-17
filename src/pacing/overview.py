"""One chart comparing the three models in the default scenario, by r, by c and by
the delivery delay of p (for README.md).

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
DS = (0.0, 1.0, 2.0, 4.0)  # delivery delay of p, planned for (delta_max = delta), at r = 0.1
D_IGNORED = 4.0  # delivery delay that the center ignores (delta_max = 0)
RS_IGNORED = (0.1, 1.0)
MODELS = (
    "Probabilistic\nε = 10⁻³",
    "Token cap (a)\nreports after 5 s",
    "Token cap (a)\nreports after 30 s",
    "Token cap (b)\nno reports",
)
SHORT = ("Probabilistic", "Cap (a)\n5 s", "Cap (a)\n30 s", "Cap (b)")
R_SHADES = ("#a9c9f0", "#5b9be3", "#1f5fa8")  # r: light -> dark, one hue (ordinal)
MODEL_COLORS = (INK, ORANGE, AQUA, YELLOW)  # c and delta panels: one colour per model

Cell = tuple[float, float, float, float]  # (r, c, delta, delta_max)


def _cell(cell: Cell, n: int) -> dict[str, tuple[float, int, int]]:
    """All models for one cell: model -> (mean underspend %, overspends, runs).

    k = lambda * r = 100 / c spends per second, so the money rate at p = 1 and the
    needed p (~0.014) stay the same whatever r and c are.
    """
    r, c, delta, delta_max = cell
    base = Scenario()
    scn = replace(base, unit_cost=c, realization=r,
                  request_rate=base.request_rate * base.realization / (r * c),
                  message_delay=delta, message_delay_max=delta_max)
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


def run(n: int) -> dict[Cell, dict[str, tuple[float, int, int]]]:
    """(r, c, delta, delta_max) -> model -> (mean underspend %, overspends, runs)."""
    cells = ({(r, 1.0, 0.0, 0.0) for r in RS} | {(0.1, c, 0.0, 0.0) for c in CS}
             | {(0.1, 1.0, d, d) for d in DS} | {(r, 1.0, D_IGNORED, 0.0) for r in RS_IGNORED})
    return {cell: _cell(cell, n) for cell in sorted(cells)}


def _planned(data):
    """Cells where the center plans for the whole delivery delay."""
    return {k: v for k, v in data.items() if k[3] >= k[2]}


def _pooled(data, m) -> tuple[int, int]:
    return sum(v[m][1] for v in data.values()), sum(v[m][2] for v in data.values())


def chart(data) -> None:
    fig = plt.figure(figsize=(12, 12.6))
    gs = fig.add_gridspec(3, 3, width_ratios=[2, 0.15, 1])
    ax_u, ax_o = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 2])
    ax_c = fig.add_subplot(gs[1, :])
    ax_d, ax_i = fig.add_subplot(gs[2, 0]), fig.add_subplot(gs[2, 2])
    x = np.arange(len(MODELS))

    # row 1: share of bids charged r, and overspend when everything is planned for
    w = 0.26
    for i, (r, color) in enumerate(zip(RS, R_SHADES)):
        vals = [data[(r, 1.0, 0.0, 0.0)][m][0] for m in MODELS]
        bars = ax_u.bar(x + (i - 1) * w, vals, w * 0.92, color=color, label=f"r = {r:g}")
        ax_u.bar_label(bars, fmt="%.2f", fontsize=7, color=INK2, padding=2)
    ax_u.set_xticks(x, MODELS, fontsize=8)
    ax_u.set(title="Underspend by share of bids charged r (c = 1)", ylabel="mean underspend, % of budget")
    ax_u.legend(title="share of bids charged", fontsize=8, title_fontsize=8)

    planned = _planned(data)
    rates = [100 * o / n for o, n in (_pooled(planned, m) for m in MODELS)]
    bars = ax_o.bar(x, rates, 0.6, color=INK2)
    ax_o.bar_label(bars, labels=[f"{v:.2f} %" if v else "0" for v in rates],
                   fontsize=7, color=INK2, padding=2)
    ax_o.set_xticks(x, SHORT, fontsize=8)
    ax_o.set(title="Overspend (all runs pooled,\ndelivery delay planned for)", ylabel="runs that overspent, %",
             ylim=(0, max(rates) * 1.6 or 1))

    # row 2: cost per realised token c
    for m, color, short in zip(MODELS, MODEL_COLORS, SHORT):
        ys = [data[(0.1, c, 0.0, 0.0)][m][0] for c in CS]
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

    # row 3: delivery delay of p, planned for; and overspend when it is ignored
    for m, color, short in zip(MODELS, MODEL_COLORS, SHORT):
        ys = [data[(0.1, 1.0, d, d)][m][0] for d in DS]
        ax_d.plot(DS, ys, "-o", color=color, ms=6, lw=2)
        ax_d.annotate(short.replace("\n", " "), (DS[-1], ys[-1]), textcoords="offset points",
                      xytext=(8, 0), va="center", fontsize=8, color=INK2)
    ax_d.set_xticks(DS, [f"{d:g}" for d in DS])
    ax_d.set_xlim(-0.2, DS[-1] + 1.3)
    ax_d.set(title="Underspend by delivery delay of p, planned for (δ_max = δ, r = 0.1)",
             xlabel="delivery delay δ, s (control period 5 s)", ylabel="mean underspend, % of budget")
    ax_d.set_ylim(bottom=0)

    w = 0.36
    for i, r in enumerate(RS_IGNORED):
        vals = [100 * data[(r, 1.0, D_IGNORED, 0.0)][m][1] / data[(r, 1.0, D_IGNORED, 0.0)][m][2]
                for m in MODELS]
        bars = ax_i.bar(x + (i - 0.5) * w, vals, w * 0.92, color=R_SHADES[2 * i], label=f"r = {r:g}")
        ax_i.bar_label(bars, labels=[f"{v:.1f}" if v else "0" for v in vals], fontsize=7, color=INK2,
                       padding=2)
    ax_i.set_xticks(x, SHORT, fontsize=8)
    ax_i.set(title=f"Overspend when a {D_IGNORED:g} s delivery\ndelay is ignored (δ_max = 0)",
             ylabel="runs that overspent, %")
    ax_i.legend(title="share of bids charged", fontsize=8, title_fontsize=8, loc="upper left")
    for ax in (ax_u, ax_o, ax_i):
        ax.grid(axis="x", visible=False)
    for ax in (ax_u, ax_o, ax_c, ax_d, ax_i):
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
    name = lambda m: m.replace(chr(10), ", ")  # noqa: E731
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
        md.append(f"| {name(m)} | " + " | ".join(f"{data[(r, 1.0, 0.0, 0.0)][m][0]:.2f}" for r in RS) + " |")
    md += ["", "## Mean underspend %, by cost per realised token c (r = 0.1)", "",
           "| model | " + " | ".join(f"c = {c:g}" for c in CS) + " |",
           "|---|" + "---|" * len(CS)]
    for m in MODELS:
        md.append(f"| {name(m)} | " + " | ".join(f"{data[(0.1, c, 0.0, 0.0)][m][0]:.2f}" for c in CS) + " |")
    md += ["", "## Mean underspend %, by delivery delay of p, planned for (δ_max = δ, r = 0.1)", "",
           "A new p (and, in version (b), a grant) reaches the clients δ seconds after the center "
           "decides it. The probabilistic controller widens its in-flight window by "
           "ceil(δ_max/step) periods; version (b) uses each grant for step − δ_max after it arrives.",
           "", "| model | " + " | ".join(f"δ = {d:g} s" for d in DS) + " |",
           "|---|" + "---|" * len(DS)]
    for m in MODELS:
        md.append(f"| {name(m)} | " + " | ".join(f"{data[(0.1, 1.0, d, d)][m][0]:.2f}" for d in DS) + " |")
    md += ["", f"## Overspends when a {D_IGNORED:g} s delivery delay is ignored (δ_max = 0)", "",
           "| model | " + " | ".join(f"r = {r:g}" for r in RS_IGNORED) + " |",
           "|---|" + "---|" * len(RS_IGNORED)]
    for m in MODELS:
        md.append(f"| {name(m)} | " + " | ".join(
            f"{data[(r, 1.0, D_IGNORED, 0.0)][m][1]} / {data[(r, 1.0, D_IGNORED, 0.0)][m][2]}"
            for r in RS_IGNORED) + " |")
    md += ["", "## Overspends, all cells where the delivery delay is planned for, pooled", "",
           "| model | overspends / runs |", "|---|---|"]
    planned = _planned(data)
    for m in MODELS:
        o, n_ = _pooled(planned, m)
        md.append(f"| {name(m)} | {o} / {n_} |")
    md += ["", "![The three models side by side](overview.png)", ""]
    (OUT / "overview.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
