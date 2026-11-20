---
name: create-scenario
description: "Create a custom scenario file (a TOML description of a real campaign) for the budget-pacing models, from the user's measurements or logs, then check and run it. Use when the user wants to simulate their own campaign, describe their traffic, delays or budget, compare models on their numbers, turn logs into scenario parameters, or mentions a scenario file, scenarios/*.toml, or pacing.scenario_file."
---

# Create a custom scenario

A scenario file describes one campaign; `pacing.scenario_file` runs the models on
it and writes a report. Templates: `scenarios/minimal.toml` (only the 8 required
fields) and `scenarios/example.toml` (every field, with comments). Patterns of the
bid-request stream: `scenarios/streams/`. Reference: `models/system.md` §8.6.

Work in this order. Do not invent numbers: every value comes from the user, from
their logs, or is a default you name to them.

## 1. Find out what the user wants to learn

Ask, in one message, only what you cannot infer:

- Which question? (Is my campaign safe? Which model fits? What does a delay or
  outage cost?) This decides which models to run and which variants to compare.
- Do they have logs, or only rough numbers?

## 2. Collect the required fields

These 8 are required; ask for any that are missing.

| Field | Section | What to ask |
|---|---|---|
| `budget` | `[campaign]` | budget in their money units |
| `length_days` / `length_hours` / `length_seconds` | `[campaign]` | campaign length (exactly one of the three) |
| `eps` | `[campaign]` | allowed overspend probability; needed only for the probabilistic model. Typical: 0.001 |
| `request_rate` | `[traffic]` | bid requests per second, on average |
| `charged_share` | `[spend]` | charged bids / bids made |
| `delay_seconds` or `[spend.delay_percentiles]` | `[spend]` | time from a bid to its charge in the budget: fixed, or percentiles p50 and max at least |
| `control_period_seconds` | `[infrastructure]` | how often the center recomputes p. Use 60 s for campaigns of a week or more, so the simulation stays fast |
| `prior_mean` | `[center]` | rough guess of charged bids per second at p = 1, e.g. request_rate × charged_share |

## 3. Add what makes it realistic (optional fields)

Ask about each of these only if it plausibly applies; otherwise leave it out and
say it is at its default.

- **Traffic pattern** (`[traffic]`, combine freely; see `scenarios/streams/`):
  daily/weekly cycle (`profile = "periodic"` or `"hourly"` with 168 values), a
  trend, sudden steps, random fluctuations beyond Poisson.
- **Spend-delay distribution**: percentiles are far better than one number;
  assuming the average delay breaks the guarantee.
- **What the center knows**: `profile_belief` (`"true"`, `"none"`, or a different
  `hourly_belief`); without a profile, set `recent_memory_seconds` (about 3600).
- **Infrastructure**: delivery delay of p, outages to test, silence limit.
- **Token caps**: `failure_report_seconds` for (a); `grant_share` for (b).
- **Start time**: `start_weekday`, `start_hour` matter when traffic is periodic.

## 4. Turn logs into numbers

If the user has logs, ask for two CSV exports (times as Unix seconds) and run:

```bash
uv run python scripts/measure_logs.py --requests requests.csv --delays delays.csv --utc-offset-hours 2
```

- `requests.csv`: `minute_start,requests`, bid requests per minute, at least one
  week, ideally several: each hour of the week is averaged over the weeks given,
  so with bursty traffic two weeks give a noticeably noisy profile.
- `delays.csv`: `bid_time,charge_time`, one line per charged bid.

It prints a `[traffic]` block (rate, 168 hourly factors, fluctuations if any) and
a `[spend.delay_percentiles]` block, ready to paste. Check its warnings (too few
days, empty hours). `charged_share` is not in these files: ask for charged bids
and bids made over the same period.

## 5. Write the file

- Start from `scenarios/minimal.toml` or `scenarios/example.toml`; put the new
  file in `scenarios/`, named after the campaign. Set `campaign.name`.
- In TOML, all plain fields of a section must come **before** its sub-tables
  (`[spend.delay_percentiles]`, `[traffic.trend]`, ...).
- For variants to compare, make one file per variant, each with its own `name`.

## 6. Check, then run

```bash
uv run python -m pacing.scenario_file scenarios/NAME.toml --runs 200   # quick check
uv run python -m pacing.scenario_file scenarios/NAME.toml --charts     # full run, with a figure
```

- If the loader refuses the file, it names the field; fix it, do not work around it.
- Read the **Notes** in the report and pass them on: simplifications, an
  infeasible budget, the Poisson warning for fluctuating traffic.
- Use numbers only from the full run. An overspend rate from a few hundred runs
  is noise; the "95% upper bound" column says how large it could be.

## 7. Explain the result

Report per model: overspend (against ε, or 0 for the caps), underspend, and
deviation from the target; the report ends with a table explaining every column.
Read the figure (`results/scenarios/NAME.png`) before describing it; point the
user to it, especially the final-spend panel, which shows how close to B each
model ends and whether any campaign crosses it.
Point out what drives the result (for example the share of bids charged for the
caps, the delay tail or traffic bursts for the probabilistic model), and suggest
the next variant to try if the question is not yet answered.

## Known limits (tell the user when they apply)

- A cost that varies per bid is not modelled: use the largest cost as `unit_cost`.
- Traffic fluctuations beyond Poisson break the probabilistic model's guarantee
  (`models/system.md` §3); the token caps are not affected.
- A traffic profile cannot be combined with a delivery delay of p.
- Token cap (b) needs `delivery_delay_max_seconds` below the control period, and
  uses the longest measured spend delay as its window.
