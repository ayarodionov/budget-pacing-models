"""Turn log exports into the measured fields of a scenario file.

Usage:
    uv run python scripts/measure_logs.py --requests requests.csv [--delays delays.csv]
                                          [--utc-offset-hours 2]

Inputs (CSV with a header line; times are Unix seconds):
    requests.csv   minute_start,requests      bid requests per minute, at least one week
    delays.csv     bid_time,charge_time       one line per charged bid: when it was made,
                                              and when its charge appeared in the budget

Prints a TOML block for [traffic] and [spend] to paste into a scenario file
(see scenarios/example.toml and .claude/skills/create-scenario/SKILL.md):

    request_rate   the average requests per second over whole weeks
    hourly         168 factors, hour of the week (Monday 00:00 first, local time)
    fluctuations   cv and correlation_seconds of the per-minute rate after
                   removing the hourly profile and the Poisson noise
    delay_percentiles  p50, p90, p99, p999, max of charge_time - bid_time
"""

from __future__ import annotations

import argparse
import csv
import math
import sys

import numpy as np

WEEK_HOURS = 168


def read_columns(path: str, names: tuple[str, str]) -> tuple[np.ndarray, np.ndarray]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    missing = [n for n in names if rows and n not in rows[0]]
    if not rows or missing:
        sys.exit(f"{path}: needs a header with columns {', '.join(names)}")
    a = np.array([float(r[names[0]]) for r in rows])
    b = np.array([float(r[names[1]]) for r in rows])
    return a, b


def hour_of_week(t: np.ndarray, utc_offset_hours: float) -> np.ndarray:
    """0 = Monday 00:00-01:00 local time. The Unix epoch was a Thursday."""
    local = t + utc_offset_hours * 3600
    days = np.floor(local / 86_400).astype(int)
    weekday = (days + 3) % 7  # 1970-01-01 was a Thursday: (0 + 3) % 7 = 3
    hour = np.floor((local % 86_400) / 3600).astype(int)
    return weekday * 24 + hour


def measure_requests(minutes: np.ndarray, counts: np.ndarray, utc_offset_hours: float) -> dict:
    order = np.argsort(minutes)
    minutes, counts = minutes[order], counts[order]
    span_days = (minutes[-1] - minutes[0] + 60) / 86_400
    if span_days < 7:
        print(f"# warning: only {span_days:.1f} days of requests; a week or more is needed for the "
              "weekly profile", file=sys.stderr)
    how = hour_of_week(minutes, utc_offset_hours)
    per_hour = np.array([counts[how == h].mean() if np.any(how == h) else np.nan for h in range(WEEK_HOURS)])
    if np.isnan(per_hour).any():
        print("# warning: some hours of the week have no data; they are filled with the average",
              file=sys.stderr)
        per_hour = np.where(np.isnan(per_hour), np.nanmean(per_hour), per_hour)
    rate = float(per_hour.mean() / 60.0)  # requests per second, averaged over the week
    profile = per_hour / per_hour.mean()

    # fluctuations: per-minute counts relative to what the hourly profile predicts
    expected = per_hour[how]
    ratio = counts / expected
    # remove the Poisson part of the variance: Var(count/expected) = cv^2 (+ cv^2/expected) + 1/expected
    excess = float(np.var(ratio) - np.mean(1.0 / expected))
    cv = math.sqrt(max(excess, 0.0))
    tau = None
    if cv > 0.01 and len(ratio) > 10:
        x = ratio - ratio.mean()
        rho1 = float(np.dot(x[:-1], x[1:]) / np.dot(x, x))
        rho1 = rho1 / max(cv * cv / (cv * cv + float(np.mean(1.0 / expected))), 1e-9)  # undo the Poisson noise
        if 0 < rho1 < 1:
            tau = -60.0 / math.log(min(rho1, 0.999))
    return {"rate": rate, "profile": profile, "cv": cv, "tau": tau, "days": span_days}


def measure_delays(bid: np.ndarray, charge: np.ndarray) -> dict:
    d = charge - bid
    if np.any(d < 0):
        print(f"# warning: {int(np.sum(d < 0))} charges appear before their bid; ignored", file=sys.stderr)
        d = d[d >= 0]
    q = np.quantile(d, [0.5, 0.9, 0.99, 0.999])
    return {"p50": q[0], "p90": q[1], "p99": q[2], "p999": q[3], "max": float(d.max()), "n": len(d)}


def to_toml(req: dict | None, dly: dict | None) -> str:
    out = []
    if req is not None:
        prof = ", ".join(f"{v:.3f}" for v in req["profile"])
        out += ["[traffic]",
                f"request_rate = {req['rate']:.1f}          # measured over {req['days']:.1f} days",
                'profile = "hourly"',
                f"hourly = [{prof}]"]
        if req["cv"] > 0.02:
            out += ["", "[traffic.fluctuations]          # random variation beyond Poisson",
                    f"cv = {req['cv']:.2f}",
                    f"correlation_seconds = {req['tau']:.0f}" if req["tau"] else
                    "correlation_seconds = 60       # could not be measured; set it by hand"]
        else:
            out += ["# no fluctuations beyond Poisson found: no [traffic.fluctuations] needed"]
    if dly is not None:
        out += ["", "[spend.delay_percentiles]      # " + f"from {dly['n']} charged bids",
                f"p50 = {dly['p50']:.1f}", f"p90 = {dly['p90']:.1f}", f"p99 = {dly['p99']:.1f}",
                f"p999 = {dly['p999']:.1f}", f"max = {dly['max']:.1f}"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--requests", help="CSV: minute_start,requests")
    ap.add_argument("--delays", help="CSV: bid_time,charge_time")
    ap.add_argument("--utc-offset-hours", type=float, default=0.0,
                    help="local time of the traffic profile relative to UTC")
    args = ap.parse_args()
    if not args.requests and not args.delays:
        ap.error("give --requests, --delays or both")
    req = measure_requests(*read_columns(args.requests, ("minute_start", "requests")),
                           args.utc_offset_hours) if args.requests else None
    dly = measure_delays(*read_columns(args.delays, ("bid_time", "charge_time"))) if args.delays else None
    print(to_toml(req, dly))


if __name__ == "__main__":
    main()
