"""Generate elixir/token_cap_golden.json: traces of the Python token-cap simulation
(src/pacing/backstop.py) for the Elixir port (elixir/token_cap.ex) to replay.

Each case runs one campaign with the token cap and records, for every control
period, what the center saw (visible spend), what it decided (p, allowance,
grant), and what the clients did (bids wanted, tokens used, failures reported).

Run: uv run python scripts/gen_elixir_token_cap_golden.py
"""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pacing.backstop import simulate_with_backstop
from pacing.controllers import BayesRiskController
from pacing.model import Scenario

OUT = Path(__file__).resolve().parent.parent / "elixir" / "token_cap_golden.json"
SHORT = Scenario(budget=1700, horizon=1200.0)  # same pace as the default, 240 periods


class FullThrottle:
    """Adversarial controller: always p = 1. Only the cap stands between it and overspend."""

    def reset(self, n):
        pass

    def decide(self, t, spend, exp_obs, inflight):
        return np.ones(spend.shape)


def case(name, r=0.1, c=1.0, mode="reports", failure_delay=0.0, full_throttle=False, **scn_kw):
    scn = replace(SHORT, realization=r, unit_cost=c, request_rate=100.0 / (r * c), **scn_kw)
    if full_throttle:
        ctl, ctl_opts = FullThrottle(), None
    else:
        ctl = BayesRiskController(scn.center_view(), prior_mean=scn.spend_rate_at_p1, eps=1e-3,
                                  tails="portable")
        ctl_opts = {"budget": scn.budget, "horizon": scn.horizon, "delay": scn.delay,
                    "step": scn.step, "unit_cost": c, "prior_mean": scn.spend_rate_at_p1, "eps": 1e-3}
    trace: list = []
    kw = {"grant_share": 0.5} if mode == "grants" else {}
    res = simulate_with_backstop(scn, ctl, 1, failure_delay=failure_delay, seed=3, mode=mode,
                                 trace=trace, **kw)
    blocked = sum(s["wanted"] - s["tokens"] for s in trace)
    print(f"{name}: spend {res.final_spend[0]:g} / {scn.budget:g}, blocked bids {blocked}")
    cap_opts = {"budget": scn.budget, "unit_cost": c}
    if mode == "grants":
        cap_opts |= {"delay": scn.delay, "step": scn.step, "share": 0.5}
    return {"name": name, "mode": mode, "controller": ctl_opts, "cap": cap_opts,
            "final_spend": float(res.final_spend[0]), "steps": trace}


def main():
    cases = [
        case("(a) r = 0.1, failures reported at once"),
        case("(a) r = 0.1, failures reported after 5 s", failure_delay=5.0),
        case("(a) r = 0.1, failures reported after 30 s", failure_delay=30.0),
        case("(a) r = 0.5, c = 2.5, reports after 30 s", r=0.5, c=2.5, failure_delay=30.0),
        case("(a) no spend delay, reports after 10 s", failure_delay=10.0, delay=0.0),
        case("(a) full throttle, reports after 30 s", failure_delay=30.0, full_throttle=True),
        case("(b) r = 0.1, share 0.5", mode="grants"),
        case("(b) r = 0.5, c = 2.5, share 0.5", r=0.5, c=2.5, mode="grants"),
        case("(b) delay 7 s, step 3 s, share 0.5", mode="grants", delay=7.0, step=3.0),
        case("(b) full throttle, share 0.5", mode="grants", full_throttle=True),
    ]
    OUT.write_text(json.dumps({"generated_by": "scripts/gen_elixir_token_cap_golden.py",
                               "cases": cases}, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
