"""Special functions from elementary math only (+ - * / exp log sin), so they can be
ported to a language without a numerics library (the target is Elixir/Erlang,
whose :math module has exp, log, sin, sqrt, pow).

Contents
--------
log1p(x)                  ln(1 + x) without a library log1p (Goldberg's trick).
lgamma(x)                 ln Gamma(x), Lanczos approximation (g = 7, 9 terms),
                          relative error ~1e-15 for x > 0.
betainc(a, b, x)          regularised incomplete beta I_x(a, b), continued fraction
                          (modified Lentz), Numerical Recipes "betai"/"betacf".
gammainc_lower(a, x)      regularised lower incomplete gamma P(a, x): series for
                          x < a + 1, continued fraction for Q = 1 - P otherwise.
nb_tail / poisson_tail    the two tail probabilities the controllers need.

The scalar functions are the reference for a port. The ``*_v`` functions run the
same algorithms on numpy arrays (same constants, same stopping rule) so the Monte
Carlo can use them at speed; tests check they agree with the scalar versions and
with scipy.

Numerical notes for a port
--------------------------
* Work in logs: the prefactor x^a (1-x)^b / B(a, b) is exp(a ln x + b ln(1-x)
  + lgamma(a+b) - lgamma(a) - lgamma(b)). With a, b ~ 1e4 the lgamma terms are
  ~1e5 and cancel; double precision still leaves ~1e-11 relative error.
* The continued fraction converges fast only for x < (a+1)/(a+b+2); otherwise use
  I_x(a, b) = 1 - I_{1-x}(b, a). The controller needs *small* upper tails, which
  land in the direct (precise) branch.
* Iterations needed grow like sqrt(max(a, b)): ~100-300 for budgets of 1e4-1e5.
"""

from __future__ import annotations

import math

import numpy as np

EPS = 1e-15  # relative stopping tolerance
FPMIN = 1e-300  # guard against division by zero in Lentz's method
MAX_ITER = 10_000

LANCZOS_G = 7.0
LANCZOS_COEF = (
    0.99999999999980993,
    676.5203681218851,
    -1259.1392167224028,
    771.32342877765313,
    -176.61502916214059,
    12.507343278686905,
    -0.13857109526572012,
    9.9843695780195716e-6,
    1.5056327351493116e-7,
)
HALF_LOG_2PI = 0.5 * math.log(2.0 * math.pi)


# =========================================================================== scalar
def log1p(x: float) -> float:
    """ln(1 + x), accurate for small x (Goldberg's trick; Erlang :math has no log1p)."""
    u = 1.0 + x
    if u == 1.0:
        return x
    return math.log(u) * x / (u - 1.0)


def lgamma(x: float) -> float:
    """ln Gamma(x) for x > 0."""
    if x < 0.5:  # reflection: Gamma(x) Gamma(1-x) = pi / sin(pi x)
        return math.log(math.pi / math.sin(math.pi * x)) - lgamma(1.0 - x)
    x -= 1.0
    s = LANCZOS_COEF[0]
    for i in range(1, 9):
        s += LANCZOS_COEF[i] / (x + i)
    t = x + LANCZOS_G + 0.5
    return HALF_LOG_2PI + (x + 0.5) * math.log(t) - t + math.log(s)


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > FPMIN else FPMIN)
    h = d
    for m in range(1, MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > FPMIN else FPMIN)
        c = 1.0 + aa / c
        c = c if abs(c) > FPMIN else FPMIN
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > FPMIN else FPMIN)
        c = 1.0 + aa / c
        c = c if abs(c) > FPMIN else FPMIN
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            return h
    raise ArithmeticError(f"betacf did not converge: a={a} b={b} x={x}")


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b), a, b > 0, 0 <= x <= 1."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (lgamma(a + b) - lgamma(a) - lgamma(b)
                 + a * math.log(x) + b * log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_front) * _betacf(a, b, x) / a
    return 1.0 - math.exp(log_front) * _betacf(b, a, 1.0 - x) / b


def gammainc_lower(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x), a > 0, x >= 0."""
    if x <= 0.0:
        return 0.0
    log_front = -x + a * math.log(x) - lgamma(a)
    if x < a + 1.0:  # series
        ap, term = a, 1.0 / a
        total = term
        for _ in range(MAX_ITER):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * EPS:
                return total * math.exp(log_front)
        raise ArithmeticError(f"gamma series did not converge: a={a} x={x}")
    # continued fraction for Q(a, x) (modified Lentz)
    b = x + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, MAX_ITER + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1.0 / (d if abs(d) > FPMIN else FPMIN)
        c = b + an / c
        c = c if abs(c) > FPMIN else FPMIN
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            return 1.0 - math.exp(log_front) * h
    raise ArithmeticError(f"gamma continued fraction did not converge: a={a} x={x}")


def poisson_tail(m: float, mu: float) -> float:
    """P(X > m) for X ~ Poisson(mu)."""
    m = math.floor(m)
    if m < 0:
        return 1.0
    if mu <= 0.0:
        return 0.0
    return gammainc_lower(m + 1.0, mu)


def nb_tail(m: float, alpha: float, beta: float, exposure: float) -> float:
    """P(X > m), X | k ~ Poisson(k * exposure), k ~ Gamma(alpha, rate beta)."""
    m = math.floor(m)
    if m < 0:
        return 1.0
    if exposure <= 0.0:
        return 0.0
    return betainc(m + 1.0, alpha, exposure / (beta + exposure))


# =========================================================================== vectorised
def log1p_v(x: np.ndarray) -> np.ndarray:
    u = 1.0 + x
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(u == 1.0, x, np.log(u) * x / (u - 1.0))


def lgamma_v(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.empty_like(x)
    small = x < 0.5
    if small.any():
        xs = x[small]
        out[small] = np.log(np.pi / np.sin(np.pi * xs)) - lgamma_v(1.0 - xs)
    big = ~small
    xb = x[big] - 1.0
    s = np.full_like(xb, LANCZOS_COEF[0])
    for i in range(1, 9):
        s += LANCZOS_COEF[i] / (xb + i)
    t = xb + LANCZOS_G + 0.5
    out[big] = HALF_LOG_2PI + (xb + 0.5) * np.log(t) - t + np.log(s)
    return out


def _guard(v):
    return np.where(np.abs(v) > FPMIN, v, FPMIN)


def _betacf_v(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = np.ones_like(x)
    d = 1.0 / _guard(1.0 - qab * x / qap)
    h = d.copy()
    active = np.ones(x.shape, dtype=bool)
    for m in range(1, MAX_ITER + 1):
        idx = np.nonzero(active)[0]
        if idx.size == 0:
            return h
        A, B, X, C, D = a[idx], b[idx], x[idx], c[idx], d[idx]
        m2 = 2 * m
        aa = m * (B - m) * X / ((qam[idx] + m2) * (A + m2))
        D = 1.0 / _guard(1.0 + aa * D)
        C = _guard(1.0 + aa / C)
        H = h[idx] * D * C
        aa = -(A + m) * (qab[idx] + m) * X / ((A + m2) * (qap[idx] + m2))
        D = 1.0 / _guard(1.0 + aa * D)
        C = _guard(1.0 + aa / C)
        delta = D * C
        h[idx] = H * delta
        c[idx], d[idx] = C, D
        active[idx] = np.abs(delta - 1.0) >= EPS
    raise ArithmeticError("betacf_v did not converge")


def betainc_v(a, b, x) -> np.ndarray:
    a, b, x = (np.asarray(v, dtype=float) for v in np.broadcast_arrays(a, b, x))
    a, b, x = a.ravel(), b.ravel(), x.ravel()
    out = np.where(x >= 1.0, 1.0, 0.0)
    inner = (x > 0.0) & (x < 1.0)
    if inner.any():
        A, B, X = a[inner], b[inner], x[inner]
        log_front = lgamma_v(A + B) - lgamma_v(A) - lgamma_v(B) + A * np.log(X) + B * log1p_v(-X)
        front = np.exp(log_front)
        direct = X < (A + 1.0) / (A + B + 2.0)
        res = np.empty_like(X)
        if direct.any():
            res[direct] = front[direct] * _betacf_v(A[direct], B[direct], X[direct]) / A[direct]
        flip = ~direct
        if flip.any():
            res[flip] = 1.0 - front[flip] * _betacf_v(B[flip], A[flip], 1.0 - X[flip]) / B[flip]
        out[inner] = res
    return out


def gammainc_lower_v(a, x) -> np.ndarray:
    a, x = (np.asarray(v, dtype=float).ravel() for v in np.broadcast_arrays(a, x))
    out = np.zeros_like(x)
    pos = x > 0.0
    if not pos.any():
        return out
    A, X = a[pos], x[pos]
    log_front = -X + A * np.log(X) - lgamma_v(A)
    res = np.empty_like(X)

    ser = X < A + 1.0
    if ser.any():
        As, Xs = A[ser], X[ser]
        ap, term = As.copy(), 1.0 / As
        total = term.copy()
        active = np.ones(As.shape, dtype=bool)
        for _ in range(MAX_ITER):
            if not active.any():
                break
            ap = np.where(active, ap + 1.0, ap)
            term = np.where(active, term * Xs / ap, term)
            total = np.where(active, total + term, total)
            active &= np.abs(term) >= np.abs(total) * EPS
        else:
            raise ArithmeticError("gamma series did not converge")
        res[ser] = total * np.exp(log_front[ser])

    cf = ~ser
    if cf.any():
        Ac, Xc = A[cf], X[cf]
        b = Xc + 1.0 - Ac
        c = np.full_like(Xc, 1.0 / FPMIN)
        d = 1.0 / b
        h = d.copy()
        active = np.ones(Ac.shape, dtype=bool)
        for i in range(1, MAX_ITER + 1):
            if not active.any():
                break
            an = -i * (i - Ac)
            b = b + 2.0
            dn = 1.0 / _guard(an * d + b)
            cn = _guard(b + an / c)
            delta = dn * cn
            h = np.where(active, h * delta, h)
            d = np.where(active, dn, d)
            c = np.where(active, cn, c)
            active &= np.abs(delta - 1.0) >= EPS
        else:
            raise ArithmeticError("gamma continued fraction did not converge")
        res[cf] = 1.0 - np.exp(log_front[cf]) * h
    out[pos] = res
    return out
