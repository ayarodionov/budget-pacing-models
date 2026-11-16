"""Generate elixir/controller_golden.json: traces of the Python BayesRiskController
for the Elixir port (elixir/pacing_controller.ex) to replay.

Each case runs one simulated campaign with the portable math (the same algorithms
as PacingMath) and records, for every control period, what the controller saw and
what it decided.

Run: uv run python scripts/gen_elixir_controller_golden.py
"""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from pacing.controllers import BayesRiskController
from pacing.model import Scenario, simulate

OUT = Path(__file__).resolve().parent.parent / "elixir" / "controller_golden.json"


class Recorder:
    def __init__(self, inner):
        self.inner = inner
        self.steps = []

    def reset(self, n):
        self.inner.reset(n)

    def decide(self, t, spend, exp_obs, inflight):
        before = self.inner.broadcasts.copy()
        p = self.inner.decide(t, spend, exp_obs, inflight)
        sent = self.inner.broadcasts - before
        self.steps.append([float(t), float(spend[0]), float(exp_obs[0]), float(inflight[0]),
                           float(p[0]), int(sent[0])])
        return p


def case(name, scn: Scenario, seed=1, **kw):
    kw.setdefault("prior_mean", scn.spend_rate_at_p1)
    kw.setdefault("eps", 1e-3)
    rec = Recorder(BayesRiskController(scn.center_view(), tails="portable", **kw))
    res = simulate(scn, rec, 1, seed=seed)
    ctl = rec.inner
    opts = {"budget": scn.budget, "horizon": scn.horizon, "delay": scn.delay, "step": scn.step,
            "unit_cost": scn.unit_cost, **{k: v for k, v in kw.items() if v is not None}}
    if "endgame_time" in opts:
        opts["endgame"] = opts.pop("endgame_time")
    print(f"{name}: final spend {res.final_spend[0]:g} / {scn.budget:g}, "
          f"risk used {ctl.risk_used[0]:.3g}, broadcasts {ctl.broadcasts[0]}")
    return {"name": name, "opts": opts, "steps": rec.steps,
            "risk_used": float(ctl.risk_used[0]), "broadcasts": int(ctl.broadcasts[0])}


def main():
    full = Scenario()  # B = 5000, T = 3600, d = 30, step = 5
    short = Scenario(budget=1700, horizon=1200.0)  # same pace, 240 periods
    k = short.spend_rate_at_p1
    cases = [
        case("default scenario", full),
        case("end-game 60 s", short, endgame_time=60.0),
        case("broadcast threshold 5 %", short, broadcast_threshold=0.05),
        case("unit cost 2.5", replace(short, unit_cost=2.5, request_rate=short.request_rate / 2.5)),
        case("guess 10x too low", short, prior_mean=k / 10),
        case("known rate (oracle)", short, known_rate=k),
        case("delay 7 s, step 3 s, eps 0.01", replace(short, delay=7.0, step=3.0), eps=0.01),
        case("no delay", replace(short, delay=0.0)),
    ]
    OUT.write_text(json.dumps({"generated_by": "scripts/gen_elixir_controller_golden.py",
                               "columns": ["t", "observed_spend", "observed_exposure",
                                           "inflight_exposure", "p", "sent"],
                               "cases": cases}, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
