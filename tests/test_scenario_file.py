"""Scenario files: loading, checks, and an end-to-end run."""

import numpy as np
import pytest

from pacing.scenario_file import ScenarioError, load, report, run_model

MINIMAL = """
[campaign]
budget = 30_000
length_hours = 6
eps = 0.01
[traffic]
request_rate = 1000
[spend]
charged_share = 0.1
delay_seconds = 30
[infrastructure]
control_period_seconds = 10
[center]
prior_mean = 100
[run]
runs = 50
"""


def write(tmp_path, text, name="s.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_example_file_loads_and_runs():
    plan = load("scenarios/example.toml")
    assert plan.scn.budget == 840_000 and plan.scn.horizon == 7 * 86_400
    assert plan.rate_profile is not None and plan.center_profile is plan.rate_profile
    assert plan.center_pmf is not None  # the center weights by the measured delays
    results = {m: run_model(plan, m, runs=30) for m in plan.models}
    for model in ("token_cap_a", "token_cap_b"):
        assert not results[model][0].overspent.any()
    text = report(plan, results)
    assert "| probabilistic" in text
    # every column of the results table is explained
    header = next(line for line in text.splitlines() if line.startswith("| model |"))
    explained = text.split("What the columns mean")[1]
    for column in [c.strip() for c in header.strip("|").split("|")]:
        assert f"| {column} |" in explained, column


def test_minimal_file(tmp_path):
    plan = load(write(tmp_path, MINIMAL))
    assert plan.name == "s" and plan.rate_profile is None and plan.center_pmf is None
    assert plan.scn.delay == 30 and plan.scn.step == 10
    assert plan.models == ["probabilistic", "token_cap_a", "token_cap_b"]


@pytest.mark.parametrize("change, message", [
    (("budget = 30_000", ""), "campaign.budget"),
    (("eps = 0.01", ""), "eps is required"),
    (("delay_seconds = 30", ""), "exactly one of spend.delay_seconds"),
    (("delay_seconds = 30", "delay_seconds = 30\n[spend.delay_percentiles]\np50 = 20\nmax = 60"), "exactly one"),
    (("charged_share = 0.1", "charged_share = 1.5"), "charged_share"),
    (("request_rate = 1000", "request_rate = 1000\nprofile = \"hourly\"\nhourly = [1, 2, 3]"), "168 values"),
    (("[run]", "[models]\nrun = [\"magic\"]\n[run]"), "unknown model"),
])
def test_bad_files_are_refused(tmp_path, change, message):
    with pytest.raises(ScenarioError, match=message):
        load(write(tmp_path, MINIMAL.replace(*change)))


def test_percentiles_must_increase(tmp_path):
    text = MINIMAL.replace("delay_seconds = 30", "[spend.delay_percentiles]\np50 = 40\np90 = 30\nmax = 60")
    with pytest.raises(ScenarioError, match="must increase"):
        load(write(tmp_path, text))


def test_unsupported_combinations_are_refused(tmp_path):
    profile_and_delay = MINIMAL.replace("request_rate = 1000", "request_rate = 1000\nprofile = \"builtin\"").replace(
        "control_period_seconds = 10", "control_period_seconds = 10\ndelivery_delay_seconds = 2")
    with pytest.raises(ScenarioError, match="not supported"):
        load(write(tmp_path, profile_and_delay))
    cap_b_slow = MINIMAL.replace("control_period_seconds = 10",
                                 "control_period_seconds = 10\ndelivery_delay_seconds = 12")
    with pytest.raises(ScenarioError, match="token cap"):
        load(write(tmp_path, cap_b_slow))


def test_hourly_profile_is_normalised_and_follows_the_clock(tmp_path):
    hours = [1.0] * 168
    hours[20] = 3.0  # Monday 20:00-21:00 is busy
    text = MINIMAL.replace("length_hours = 6", "length_hours = 24").replace(
        "request_rate = 1000", f"request_rate = 1000\nprofile = \"hourly\"\nhourly = {hours}")
    plan = load(write(tmp_path, text))
    prof = plan.rate_profile
    per_hour = prof.reshape(24, -1).mean(axis=1)
    assert per_hour.argmax() == 20
    assert np.isclose(per_hour[20] / per_hour[0], 3.0)


def test_infeasible_budget_is_reported(tmp_path):
    plan = load(write(tmp_path, MINIMAL.replace("budget = 30_000", "budget = 3_000_000")))
    assert any("cannot be spent" in n for n in plan.notes)


def test_minimal_file_has_only_required_fields_and_runs():
    plan = load("scenarios/minimal.toml")
    assert plan.rate_profile is None and plan.center_pmf is None and plan.runs == 1000
    res, _ = run_model(plan, "probabilistic", runs=30)
    assert res.final_spend.mean() > 0.95 * plan.scn.budget


def test_stream_patterns_combine(tmp_path):
    text = MINIMAL.replace("request_rate = 1000", """request_rate = 1000
profile = "periodic"
[traffic.trend]
start = 1.0
end = 2.0
[traffic.steps]
at_hours = [3]
factor = [0.5]
[traffic.fluctuations]
cv = 0.3
correlation_seconds = 60""")
    plan = load(write(tmp_path, text))
    n = len(plan.rate_profile)
    # after 3 h the steps halve the rate; the trend doubles it over 6 h
    assert plan.rate_profile[n // 2 + 1] < plan.rate_profile[n // 2 - 1]
    assert plan.rate_noise == (0.3, 60.0)
    assert any("Poisson" in note for note in plan.notes)
    res, _ = run_model(plan, "token_cap_a", runs=20)
    assert not res.overspent.any()


@pytest.mark.parametrize("block, message", [
    ("[traffic.trend]\nstart = 1.0", "traffic.trend.end"),
    ("[traffic.steps]\nat_hours = [1, 2]\nfactor = [1.5]", "same, non-zero length"),
    ("[traffic.fluctuations]\ncv = 0.3\ncorrelation_seconds = 0", "correlation_seconds > 0"),
])
def test_bad_stream_patterns_are_refused(tmp_path, block, message):
    with pytest.raises(ScenarioError, match=message):
        load(write(tmp_path, MINIMAL.replace("request_rate = 1000", "request_rate = 1000\n" + block)))


@pytest.mark.parametrize("name", ["stable", "periodic", "trend", "steps", "bursty"])
def test_stream_example_files_load(name):
    plan = load(f"scenarios/streams/{name}.toml")
    assert (plan.rate_profile is None) == (name in ("stable", "bursty"))
    assert (plan.rate_noise is not None) == (name == "bursty")
