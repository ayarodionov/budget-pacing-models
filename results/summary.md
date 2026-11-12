# Results

Default scenario: B = 5000 units, T = 3600 s, lambda = 1000 req/s, r = 0.1, d = 30 s, control step = 5 s. Needed p ~ 0.014. Overspend tolerance eps = 0.001. 40000 runs per row unless stated.

## 1. Strategies compared

| strategy | P(overspend) | 95% upper bound | mean overspend, units (if over) | mean underspend % | p95 underspend % | max pacing deviation % |
|---|---|---|---|---|---|---|
| Naive (ignores in-flight spend) | 1.00e+00 | 1.0e+00 | 43.1 | 0.000 | 0.000 | 1.49 |
| Plug-in reserve, eps checked per step | 2.60e-03 | 3.1e-03 | 2.5 | 0.347 | 0.560 | 1.23 |
| **Proposed: Bayes + risk budget** | 1.10e-03 | 1.4e-03 | 3.3 | 0.401 | 0.600 | 1.23 |
| Proposed + 300 s end-game | 8.25e-04 | 1.1e-03 | 2.7 | 0.185 | 0.260 | 8.41 |
| Oracle (knows lambda*r exactly) | 1.05e-03 | 1.4e-03 | 2.7 | 0.399 | 0.600 | 1.21 |

## 2. Overspend tolerance holds

Proposed controller, 400000 runs per row.

| strategy | P(overspend) | 95% upper bound | mean overspend, units (if over) | mean underspend % | p95 underspend % | max pacing deviation % |
|---|---|---|---|---|---|---|
| eps = 0.01 | 8.90e-03 | 9.1e-03 | 3.0 | 0.310 | 0.520 | 1.23 |
| eps = 0.001 | 9.97e-04 | 1.1e-03 | 2.7 | 0.402 | 0.600 | 1.23 |
| eps = 0.0001 | 8.00e-05 | 1.1e-04 | 2.4 | 0.471 | 0.680 | 1.23 |

## 3. Effect of the delay d

| strategy | P(overspend) | 95% upper bound | mean overspend, units (if over) | mean underspend % | p95 underspend % | max pacing deviation % |
|---|---|---|---|---|---|---|
| d = 0 s | 9.50e-04 | 1.2e-03 | 1.4 | 0.123 | 0.200 | 1.20 |
| d = 5 s | 8.00e-04 | 1.1e-03 | 1.8 | 0.180 | 0.280 | 1.20 |
| d = 15 s | 8.75e-04 | 1.2e-03 | 2.7 | 0.283 | 0.440 | 1.22 |
| d = 30 s | 9.50e-04 | 1.2e-03 | 2.6 | 0.401 | 0.600 | 1.23 |
| d = 60 s | 8.75e-04 | 1.2e-03 | 2.5 | 0.574 | 0.860 | 1.26 |
| d = 120 s | 1.12e-03 | 1.4e-03 | 6.0 | 0.824 | 1.260 | 1.34 |
| d = 300 s | 9.25e-04 | 1.2e-03 | 6.4 | 1.346 | 2.040 | 1.64 |

## 4. Effect of the overspend tolerance eps

| strategy | P(overspend) | 95% upper bound | mean overspend, units (if over) | mean underspend % | p95 underspend % | max pacing deviation % |
|---|---|---|---|---|---|---|
| eps = 0.1 | 8.44e-02 | 8.7e-02 | 3.8 | 0.187 | 0.400 | 1.23 |
| eps = 0.01 | 1.02e-02 | 1.1e-02 | 2.8 | 0.310 | 0.520 | 1.23 |
| eps = 0.001 | 1.10e-03 | 1.4e-03 | 2.6 | 0.401 | 0.600 | 1.23 |
| eps = 0.0001 | 5.00e-05 | 1.6e-04 | 1.5 | 0.470 | 0.680 | 1.23 |
| eps = 1e-05 | 0 | 7.5e-05 | 0.0 | 0.524 | 0.720 | 1.23 |
| eps = 1e-06 | 0 | 7.5e-05 | 0.0 | 0.567 | 0.760 | 1.23 |

## 5. End-game time: evenness vs underspend

| strategy | P(overspend) | 95% upper bound | mean overspend, units (if over) | mean underspend % | p95 underspend % | max pacing deviation % |
|---|---|---|---|---|---|---|
| end-game 0 s | 8.75e-04 | 1.2e-03 | 2.2 | 0.402 | 0.600 | 1.23 |
| end-game 30 s | 9.75e-04 | 1.3e-03 | 2.7 | 0.280 | 0.420 | 1.45 |
| end-game 60 s | 6.00e-04 | 8.4e-04 | 2.5 | 0.235 | 0.340 | 2.08 |
| end-game 120 s | 1.15e-03 | 1.5e-03 | 2.3 | 0.205 | 0.300 | 3.59 |
| end-game 300 s | 9.75e-04 | 1.3e-03 | 2.2 | 0.185 | 0.260 | 8.41 |
| end-game 600 s | 9.75e-04 | 1.3e-03 | 2.9 | 0.174 | 0.220 | 16.63 |

## 6. Wrong initial guess of lambda*r (true rate fixed)

| strategy | P(overspend) | 95% upper bound | mean overspend, units (if over) | mean underspend % | p95 underspend % | max pacing deviation % |
|---|---|---|---|---|---|---|
| guess = 0.1 x true | 1.12e-03 | 1.4e-03 | 2.5 | 0.381 | 0.580 | 8.84 |
| guess = 0.5 x true | 8.75e-04 | 1.2e-03 | 1.9 | 0.400 | 0.600 | 1.54 |
| guess = 2 x true | 1.18e-03 | 1.5e-03 | 2.8 | 0.402 | 0.620 | 1.36 |
| guess = 10 x true | 7.25e-04 | 9.9e-04 | 2.3 | 0.402 | 0.620 | 1.73 |

## 7. Risk-spending rate eta (share of the unused risk budget one step may use)

| strategy | P(overspend) | 95% upper bound | mean overspend, units (if over) | mean underspend % | p95 underspend % | max pacing deviation % |
|---|---|---|---|---|---|---|
| eta = 0.1 | 4.25e-04 | 6.4e-04 | 2.4 | 0.412 | 0.620 | 1.23 |
| eta = 0.25 | 5.50e-04 | 7.9e-04 | 3.0 | 0.399 | 0.600 | 1.23 |
| eta = 0.5 | 8.75e-04 | 1.2e-03 | 2.9 | 0.402 | 0.600 | 1.23 |
| eta = 0.9 | 9.75e-04 | 1.3e-03 | 2.2 | 0.432 | 0.660 | 1.23 |
| eta = 1 | 1.08e-03 | 1.4e-03 | 2.5 | 0.443 | 0.680 | 1.23 |

_Total compute: 333 s._
