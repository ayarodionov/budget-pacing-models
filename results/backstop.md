# Token-lease backstop

Default scenario (B = 5000, T = 3600 s, d = 30 s, step = 5 s), k = λ·r = 100 kept fixed while r varies, so the spend process is the same in every row; only the share of bids that fail (1 − r) and how fast failures are reported (d_f) change. 20000 runs per row.

## Version (a): failure reports

| r | d_f | strategy | overspends | mean underspend % | p95 underspend % | runs where cap blocked bids | max pacing deviation % |
|---|---|---|---|---|---|---|---|
| 0.1 | – | Bayes ε=1e-3, no cap | 16 | 0.40 | 0.60 | 0.0 % | 1.23 |
| 0.1 | 0 s | Bayes ε=1e-3 + cap | 0 | 0.50 | 0.68 | 100.0 % | 1.23 |
| 0.1 | 0 s | Bayes ε=1e-9 + cap | 0 | 0.69 | 0.88 | 96.5 % | 1.24 |
| 0.1 | 0 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 0.49 | 0.62 | 97.0 % | 2.06 |
| 0.1 | 0 s | Cap only (pacing, no risk check) | 0 | 0.26 | 0.40 | 100.0 % | 1.45 |
| 0.1 | 5 s | Bayes ε=1e-3 + cap | 0 | 0.98 | 1.22 | 100.0 % | 1.28 |
| 0.1 | 5 s | Bayes ε=1e-9 + cap | 0 | 0.98 | 1.22 | 100.0 % | 1.28 |
| 0.1 | 5 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 0.60 | 0.76 | 100.0 % | 2.04 |
| 0.1 | 5 s | Cap only (pacing, no risk check) | 0 | 0.72 | 0.92 | 100.0 % | 1.44 |
| 0.1 | 30 s | Bayes ε=1e-3 + cap | 0 | 3.40 | 3.84 | 100.0 % | 3.40 |
| 0.1 | 30 s | Bayes ε=1e-9 + cap | 0 | 3.40 | 3.84 | 100.0 % | 3.40 |
| 0.1 | 30 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 2.89 | 3.30 | 100.0 % | 2.91 |
| 0.1 | 30 s | Cap only (pacing, no risk check) | 0 | 3.14 | 3.54 | 100.0 % | 3.14 |
|  |  |  |  |  |  |  |  |
| 0.5 | – | Bayes ε=1e-3, no cap | 18 | 0.40 | 0.60 | 0.0 % | 1.23 |
| 0.5 | 0 s | Bayes ε=1e-3 + cap | 0 | 0.40 | 0.60 | 0.9 % | 1.23 |
| 0.5 | 0 s | Bayes ε=1e-9 + cap | 0 | 0.67 | 0.86 | 0.0 % | 1.24 |
| 0.5 | 0 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 0.49 | 0.62 | 0.0 % | 2.07 |
| 0.5 | 0 s | Cap only (pacing, no risk check) | 0 | 0.00 | 0.02 | 100.0 % | 1.45 |
| 0.5 | 5 s | Bayes ε=1e-3 + cap | 0 | 0.40 | 0.60 | 8.2 % | 1.23 |
| 0.5 | 5 s | Bayes ε=1e-9 + cap | 0 | 0.67 | 0.86 | 0.0 % | 1.24 |
| 0.5 | 5 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 0.49 | 0.62 | 0.0 % | 2.07 |
| 0.5 | 5 s | Cap only (pacing, no risk check) | 0 | 0.02 | 0.06 | 100.0 % | 1.45 |
| 0.5 | 30 s | Bayes ε=1e-3 + cap | 0 | 0.52 | 0.70 | 99.5 % | 1.23 |
| 0.5 | 30 s | Bayes ε=1e-9 + cap | 0 | 0.69 | 0.88 | 53.7 % | 1.24 |
| 0.5 | 30 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 0.49 | 0.62 | 55.0 % | 2.07 |
| 0.5 | 30 s | Cap only (pacing, no risk check) | 0 | 0.29 | 0.42 | 100.0 % | 1.45 |
|  |  |  |  |  |  |  |  |
| 1 | – | Bayes ε=1e-3, no cap | 19 | 0.40 | 0.62 | 0.0 % | 1.23 |
| 1 | 0 s | Bayes ε=1e-3 + cap | 0 | 0.40 | 0.62 | 0.1 % | 1.23 |
| 1 | 0 s | Bayes ε=1e-9 + cap | 0 | 0.67 | 0.86 | 0.0 % | 1.24 |
| 1 | 0 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 0.49 | 0.62 | 0.0 % | 2.07 |
| 1 | 0 s | Cap only (pacing, no risk check) | 0 | 0.00 | 0.00 | 100.0 % | 1.45 |
| 1 | 5 s | Bayes ε=1e-3 + cap | 0 | 0.40 | 0.62 | 0.1 % | 1.23 |
| 1 | 5 s | Bayes ε=1e-9 + cap | 0 | 0.67 | 0.86 | 0.0 % | 1.24 |
| 1 | 5 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 0.49 | 0.62 | 0.0 % | 2.07 |
| 1 | 5 s | Cap only (pacing, no risk check) | 0 | 0.00 | 0.00 | 100.0 % | 1.45 |
| 1 | 30 s | Bayes ε=1e-3 + cap | 0 | 0.40 | 0.62 | 0.1 % | 1.23 |
| 1 | 30 s | Bayes ε=1e-9 + cap | 0 | 0.67 | 0.86 | 0.0 % | 1.24 |
| 1 | 30 s | Bayes ε=1e-9 + cap + 60 s end-game | 0 | 0.49 | 0.62 | 0.0 % | 2.07 |
| 1 | 30 s | Cap only (pacing, no risk check) | 0 | 0.00 | 0.00 | 100.0 % | 1.45 |
|  |  |  |  |  |  |  |  |

## Version (b): no reports from clients

Each step the center grants tokens that expire at the end of the step, at most A = B − S_obs − (tokens granted in the last d seconds). Clients report nothing. Grant rules: *share 0.5* grants half of A (needs no information). *Headroom 1.5* grants 1.5 × the expected bid demand λ·p·Δ, which assumes the center knows the demand, for example from usage counts. For comparison, version (a) cap only with d_f = d = 30 s.

| r | strategy | overspends | mean underspend % | p95 underspend % | runs where cap blocked bids | max pacing deviation % |
|---|---|---|---|---|---|---|
| 0.1 | (b) share 0.5, cap only | 0 | 3.63 | 4.08 | 100.0 % | 3.63 |
| 0.1 | (b) share 0.5, cap only + 60 s end-game | 0 | 3.16 | 3.58 | 100.0 % | 3.23 |
| 0.1 | (b) headroom 1.5, cap only | 0 | 3.17 | 3.58 | 100.0 % | 3.17 |
| 0.1 | (b) headroom 1.5, cap only + 60 s end-game | 0 | 2.69 | 3.08 | 100.0 % | 2.88 |
| 0.1 | (a) d_f = 30 s, cap only (reference) | 0 | 3.14 | 3.54 | 100.0 % | 3.14 |
|  |  |  |  |  |  |  |
| 0.5 | (b) share 0.5, cap only | 0 | 0.38 | 0.54 | 100.0 % | 1.44 |
| 0.5 | (b) share 0.5, cap only + 60 s end-game | 0 | 0.13 | 0.22 | 100.0 % | 2.77 |
| 0.5 | (b) headroom 1.5, cap only | 0 | 0.32 | 0.46 | 100.0 % | 1.43 |
| 0.5 | (b) headroom 1.5, cap only + 60 s end-game | 0 | 0.10 | 0.18 | 100.0 % | 2.77 |
| 0.5 | (a) d_f = 30 s, cap only (reference) | 0 | 0.29 | 0.42 | 100.0 % | 1.45 |
|  |  |  |  |  |  |  |
| 1 | (b) share 0.5, cap only | 0 | 0.01 | 0.06 | 100.0 % | 1.44 |
| 1 | (b) share 0.5, cap only + 60 s end-game | 0 | 0.00 | 0.00 | 100.0 % | 2.80 |
| 1 | (b) headroom 1.5, cap only | 0 | 0.03 | 0.10 | 100.0 % | 1.36 |
| 1 | (b) headroom 1.5, cap only + 60 s end-game | 0 | 0.00 | 0.00 | 100.0 % | 2.76 |
| 1 | (a) d_f = 30 s, cap only (reference) | 0 | 0.00 | 0.00 | 100.0 % | 1.45 |
|  |  |  |  |  |  |  |

### Version (b): grant share s (cap only, plain pacing)

| r | s = 0.14 | s = 0.29 | s = 0.43 | s = 0.57 | s = 0.71 | s = 0.86 |
|---|---|---|---|---|---|---|
| 0.1 | 6.16 % (dev 6.2 %) | 4.39 % (dev 4.4 %) | 3.81 % (dev 3.8 %) | 3.48 % (dev 3.5 %) | 3.17 % (dev 3.3 %) | 2.51 % (dev 6.7 %) |
| 0.5 | 0.80 % (dev 1.4 %) | 0.49 % (dev 1.4 %) | 0.40 % (dev 1.4 %) | 0.36 % (dev 1.4 %) | 0.33 % (dev 1.7 %) | 0.29 % (dev 4.5 %) |
| 1 | 0.13 % (dev 1.4 %) | 0.03 % (dev 1.4 %) | 0.01 % (dev 1.4 %) | 0.01 % (dev 1.4 %) | 0.02 % (dev 1.6 %) | 0.06 % (dev 3.9 %) |

Cells: mean underspend (max pacing deviation).
