/*
 * NIF version of PacingMath (elixir/pacing_math.ex): the same algorithms and
 * constants as src/pacing/specfun.py, in C.
 *
 *   lgamma/1, betainc/3, gammainc_lower/2, nb_tail/4, poisson_tail/2
 *
 * ln Gamma uses its own Lanczos approximation instead of libm lgamma(): libm's
 * lgamma writes the global `signgam`, which is not safe on concurrent scheduler
 * threads. Every call is pure and finishes in microseconds (at most a few hundred
 * loop iterations), so it runs on a normal scheduler; no dirty scheduler needed.
 *
 * Arguments may be Erlang floats or integers. Bad arguments raise badarg;
 * non-convergence raises {:error, :no_convergence}.
 */
#include <math.h>
#include <stdint.h>

#include "erl_nif.h"

#define EPS 1e-15
#define FPMIN 1e-300
#define MAX_ITER 10000

static const double LANCZOS_G = 7.0;
static const double LANCZOS_COEF[9] = {
    0.99999999999980993,  676.5203681218851,     -1259.1392167224028,
    771.32342877765313,   -176.61502916214059,   12.507343278686905,
    -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7};

static ERL_NIF_TERM atom_error, atom_no_convergence;

/* ------------------------------------------------------------------ math */

static double guard(double v) { return fabs(v) > FPMIN ? v : FPMIN; }

static double pm_lgamma(double x) {
    if (x < 0.5)
        return log(M_PI / sin(M_PI * x)) - pm_lgamma(1.0 - x);
    x -= 1.0;
    double s = LANCZOS_COEF[0];
    for (int i = 1; i < 9; i++) s += LANCZOS_COEF[i] / (x + i);
    double t = x + LANCZOS_G + 0.5;
    return 0.5 * log(2.0 * M_PI) + (x + 0.5) * log(t) - t + log(s);
}

/* continued fraction for the incomplete beta, modified Lentz; returns NAN if no convergence */
static double betacf(double a, double b, double x) {
    double qab = a + b, qap = a + 1.0, qam = a - 1.0;
    double c = 1.0;
    double d = 1.0 / guard(1.0 - qab * x / qap);
    double h = d;
    for (int m = 1; m <= MAX_ITER; m++) {
        int m2 = 2 * m;
        double aa = m * (b - m) * x / ((qam + m2) * (a + m2));
        d = 1.0 / guard(1.0 + aa * d);
        c = guard(1.0 + aa / c);
        h *= d * c;
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
        d = 1.0 / guard(1.0 + aa * d);
        c = guard(1.0 + aa / c);
        double delta = d * c;
        h *= delta;
        if (fabs(delta - 1.0) < EPS) return h;
    }
    return NAN;
}

static double pm_betainc(double a, double b, double x) {
    if (x <= 0.0) return 0.0;
    if (x >= 1.0) return 1.0;
    double log_front = pm_lgamma(a + b) - pm_lgamma(a) - pm_lgamma(b) + a * log(x) + b * log1p(-x);
    if (x < (a + 1.0) / (a + b + 2.0))
        return exp(log_front) * betacf(a, b, x) / a;
    return 1.0 - exp(log_front) * betacf(b, a, 1.0 - x) / b;
}

static double pm_gammainc_lower(double a, double x) {
    if (x <= 0.0) return 0.0;
    double log_front = -x + a * log(x) - pm_lgamma(a);
    if (x < a + 1.0) { /* series */
        double ap = a, term = 1.0 / a, total = term;
        for (int n = 0; n < MAX_ITER; n++) {
            ap += 1.0;
            term *= x / ap;
            total += term;
            if (fabs(term) < fabs(total) * EPS) return total * exp(log_front);
        }
        return NAN;
    }
    /* continued fraction for Q = 1 - P */
    double b = x + 1.0 - a, c = 1.0 / FPMIN, d = 1.0 / b, h = d;
    for (int i = 1; i <= MAX_ITER; i++) {
        double an = -i * (i - a);
        b += 2.0;
        d = 1.0 / guard(an * d + b);
        c = guard(b + an / c);
        double delta = d * c;
        h *= delta;
        if (fabs(delta - 1.0) < EPS) return 1.0 - exp(log_front) * h;
    }
    return NAN;
}

static double pm_poisson_tail(double m, double mu) {
    m = floor(m);
    if (m < 0) return 1.0;
    if (mu <= 0) return 0.0;
    return pm_gammainc_lower(m + 1.0, mu);
}

static double pm_nb_tail(double m, double alpha, double beta, double exposure) {
    m = floor(m);
    if (m < 0) return 1.0;
    if (exposure <= 0) return 0.0;
    return pm_betainc(m + 1.0, alpha, exposure / (beta + exposure));
}

/* ------------------------------------------------------------------ NIF glue */

static int get_number(ErlNifEnv *env, ERL_NIF_TERM t, double *out) {
    if (enif_get_double(env, t, out)) return 1;
    ErlNifSInt64 i;
    if (enif_get_int64(env, t, &i)) {
        *out = (double)i;
        return 1;
    }
    return 0;
}

static ERL_NIF_TERM result(ErlNifEnv *env, double v) {
    if (isnan(v)) return enif_raise_exception(env, enif_make_tuple2(env, atom_error, atom_no_convergence));
    return enif_make_double(env, v);
}

#define ARGS(n)                                                        \
    double x[n];                                                       \
    for (int k = 0; k < (n); k++)                                      \
        if (!get_number(env, argv[k], &x[k])) return enif_make_badarg(env);

static ERL_NIF_TERM nif_lgamma(ErlNifEnv *env, int argc, const ERL_NIF_TERM argv[]) {
    ARGS(1);
    if (x[0] <= 0) return enif_make_badarg(env);
    return result(env, pm_lgamma(x[0]));
}

static ERL_NIF_TERM nif_betainc(ErlNifEnv *env, int argc, const ERL_NIF_TERM argv[]) {
    ARGS(3);
    if (x[0] <= 0 || x[1] <= 0) return enif_make_badarg(env);
    return result(env, pm_betainc(x[0], x[1], x[2]));
}

static ERL_NIF_TERM nif_gammainc_lower(ErlNifEnv *env, int argc, const ERL_NIF_TERM argv[]) {
    ARGS(2);
    if (x[0] <= 0) return enif_make_badarg(env);
    return result(env, pm_gammainc_lower(x[0], x[1]));
}

static ERL_NIF_TERM nif_poisson_tail(ErlNifEnv *env, int argc, const ERL_NIF_TERM argv[]) {
    ARGS(2);
    return result(env, pm_poisson_tail(x[0], x[1]));
}

static ERL_NIF_TERM nif_nb_tail(ErlNifEnv *env, int argc, const ERL_NIF_TERM argv[]) {
    ARGS(4);
    if (x[1] <= 0 || x[2] < 0) return enif_make_badarg(env);
    return result(env, pm_nb_tail(x[0], x[1], x[2], x[3]));
}

static int load(ErlNifEnv *env, void **priv, ERL_NIF_TERM info) {
    atom_error = enif_make_atom(env, "error");
    atom_no_convergence = enif_make_atom(env, "no_convergence");
    return 0;
}

static ErlNifFunc funcs[] = {
    {"lgamma", 1, nif_lgamma, 0},
    {"betainc", 3, nif_betainc, 0},
    {"gammainc_lower", 2, nif_gammainc_lower, 0},
    {"poisson_tail", 2, nif_poisson_tail, 0},
    {"nb_tail", 4, nif_nb_tail, 0},
};

ERL_NIF_INIT(Elixir.PacingMathNif, funcs, load, NULL, NULL, NULL)
