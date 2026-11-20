"""scripts/measure_logs.py recovers known traffic and delay statistics from synthetic logs."""

import importlib.util
from pathlib import Path

import numpy as np

from pacing.seasonal import traffic_profile

spec = importlib.util.spec_from_file_location("measure_logs", Path(__file__).parent.parent / "scripts/measure_logs.py")
ml = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ml)

MONDAY = 345_600 + 2800 * 604_800  # a Monday 00:00 UTC


def synthetic(weeks=2, cv=0.3, tau=300.0, seed=0):
    rng = np.random.default_rng(seed)
    minutes = MONDAY + np.arange(weeks * 7 * 1440) * 60.0
    prof = traffic_profile(minutes - MONDAY)
    sig, rho = np.sqrt(np.log1p(cv ** 2)), np.exp(-60 / tau)
    z = np.empty(len(minutes))
    z[0] = rng.standard_normal()
    for i in range(1, len(z)):
        z[i] = rho * z[i - 1] + np.sqrt(1 - rho ** 2) * rng.standard_normal()
    counts = rng.poisson(1000 * 60 * prof * np.exp(sig * z - sig ** 2 / 2))
    return minutes, counts.astype(float), prof


def test_hour_of_week_starts_on_monday():
    assert ml.hour_of_week(np.array([MONDAY + 1.0]), 0)[0] == 0
    assert ml.hour_of_week(np.array([MONDAY + 2 * 86_400 + 5 * 3600 + 1.0]), 0)[0] == 2 * 24 + 5
    assert ml.hour_of_week(np.array([MONDAY + 1.0]), 2)[0] == 2  # 2 hours ahead of UTC


def test_recovers_rate_profile_and_fluctuations():
    minutes, counts, prof = synthetic()
    m = ml.measure_requests(minutes, counts, 0.0)
    assert abs(m["rate"] / 1000 - 1) < 0.02
    true_hourly = traffic_profile(np.arange(168) * 3600 + 1800.0)
    # two weeks of bursty minutes per hour of the week: noisy, but clearly the right shape
    assert np.corrcoef(m["profile"], true_hourly)[0, 1] > 0.95
    assert abs(m["cv"] - 0.3) < 0.05
    assert 150 < m["tau"] < 600


def test_poisson_traffic_shows_no_fluctuations():
    minutes, _, prof = synthetic(weeks=1)
    counts = np.random.default_rng(1).poisson(1000 * 60 * prof).astype(float)
    assert ml.measure_requests(minutes, counts, 0.0)["cv"] < 0.05


def test_delay_percentiles():
    rng = np.random.default_rng(2)
    bid = rng.uniform(0, 1e6, 50_000)
    d = ml.measure_delays(bid, bid + rng.exponential(30, 50_000))
    assert abs(d["p50"] - 30 * np.log(2)) < 1.0
    assert d["p50"] < d["p90"] < d["p99"] < d["p999"] <= d["max"]


def test_output_is_valid_toml_for_a_scenario_file(tmp_path):
    import tomllib
    minutes, counts, _ = synthetic(weeks=1)
    rng = np.random.default_rng(3)
    bid = rng.uniform(0, 1e5, 2000)
    text = ml.to_toml(ml.measure_requests(minutes, counts, 0.0), ml.measure_delays(bid, bid + rng.exponential(30, 2000)))
    cfg = tomllib.loads(text)
    assert len(cfg["traffic"]["hourly"]) == 168
    assert set(cfg["spend"]["delay_percentiles"]) == {"p50", "p90", "p99", "p999", "max"}
