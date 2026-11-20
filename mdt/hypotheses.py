"""The five textbook hypotheses, each tested against real index returns.

H1 Normal     daily log returns are i.i.d. Normal
H2 Student-t  a fat-tailed Student-t describes them instead
H3 Bernoulli  the direction of each day is an independent coin flip
H4 Poisson    large moves arrive independently, so counts per month are Poisson
H5 Uniform    a correct forecast distribution gives Uniform(0,1) PIT values

Every function returns plain dicts / DataFrames so results can be written to CSV/JSON.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

TAIL_K = 2.5            # pre-registered threshold (in std devs) for a "large move" in H4
TAIL_K_SENSITIVITY = (2.0, 2.5, 3.0)
VAR_LEVELS = (0.01, 0.05)


# ----------------------------------------------------------------------------- H1
def h1_normal(r: pd.Series) -> dict:
    x = r.to_numpy()
    n = len(x)
    mu, sd = x.mean(), x.std(ddof=1)
    z = (x - mu) / sd
    jb = stats.jarque_bera(x)
    tails = []
    for k in (3, 4, 5):
        obs = int(np.sum(np.abs(z) > k))
        exp = n * 2 * stats.norm.sf(k)
        tails.append({"k": k, "observed": obs, "expected_normal": exp})
    agg = []
    for h in (1, 5, 21):
        # non-overlapping h-day sums
        m = n // h
        xs = x[: m * h].reshape(m, h).sum(axis=1)
        agg.append({"horizon_days": h, "n": m, "excess_kurtosis": float(stats.kurtosis(xs))})
    worst = int(np.argmin(z))
    best = int(np.argmax(z))
    p_worst = stats.norm.cdf(z[worst])
    return {
        "n": n,
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
        "mean_daily": mu,
        "std_daily": sd,
        "ann_vol": sd * np.sqrt(252),
        "skew": float(stats.skew(x)),
        "excess_kurtosis": float(stats.kurtosis(x)),
        "jb_stat": float(jb.statistic),
        "jb_p": float(jb.pvalue),
        "tails": tails,
        "aggregation": agg,
        "worst_date": str(r.index[worst].date()),
        "worst_return": float(x[worst]),
        "worst_z": float(z[worst]),
        "worst_simple_return": float(np.expm1(x[worst])),
        "best_date": str(r.index[best].date()),
        "best_simple_return": float(np.expm1(x[best])),
        "best_z": float(z[best]),
        # expected waiting time for a day this bad under the Normal, in years of 252 days
        "worst_normal_wait_years": float(1.0 / p_worst / 252.0) if p_worst > 0 else float("inf"),
    }


# ----------------------------------------------------------------------------- H2
def h2_student_t(r: pd.Series) -> dict:
    x = r.to_numpy()
    n = len(x)
    nu, loc, scale = stats.t.fit(x)
    mu, sd = x.mean(), x.std(ddof=0)
    ll_n = float(np.sum(stats.norm.logpdf(x, mu, sd)))
    ll_t = float(np.sum(stats.t.logpdf(x, nu, loc, scale)))
    z_mu, z_sd = x.mean(), x.std(ddof=1)
    tails = []
    for k in (3, 4, 5):
        lo, hi = z_mu - k * z_sd, z_mu + k * z_sd
        exp_t = n * (stats.t.cdf(lo, nu, loc, scale) + stats.t.sf(hi, nu, loc, scale))
        tails.append({"k": k, "observed": int(np.sum((x < lo) | (x > hi))),
                      "expected_normal": n * 2 * stats.norm.sf(k), "expected_t": float(exp_t)})
    return {
        "nu": float(nu), "loc": float(loc), "scale": float(scale),
        "loglik_normal": ll_n, "loglik_t": ll_t,
        "aic_normal": 4 - 2 * ll_n, "aic_t": 6 - 2 * ll_t,
        "ks_normal": float(stats.kstest(x, stats.norm(mu, sd).cdf).statistic),
        "ks_t": float(stats.kstest(x, stats.t(nu, loc, scale).cdf).statistic),
        "tails": tails,
    }


def kupiec(hits: np.ndarray, alpha: float) -> tuple[float, float]:
    """Kupiec (1995) proportion-of-failures LR test. Returns (LR, p)."""
    n = len(hits)
    x = int(hits.sum())
    if x == 0:
        lr = -2 * n * np.log(1 - alpha)
    elif x == n:
        lr = -2 * n * np.log(alpha)
    else:
        ph = x / n
        lr = -2 * ((n - x) * np.log(1 - alpha) + x * np.log(alpha)
                   - (n - x) * np.log(1 - ph) - x * np.log(ph))
    return float(lr), float(stats.chi2.sf(lr, 1))


def christoffersen(hits: np.ndarray) -> tuple[float, float]:
    """Christoffersen (1998) independence LR test on the hit sequence. Returns (LR, p)."""
    h = hits.astype(int)
    a, b = h[:-1], h[1:]
    n00 = np.sum((a == 0) & (b == 0)); n01 = np.sum((a == 0) & (b == 1))
    n10 = np.sum((a == 1) & (b == 0)); n11 = np.sum((a == 1) & (b == 1))
    if n01 + n11 == 0:
        return 0.0, 1.0
    p01 = n01 / (n00 + n01) if n00 + n01 else 0.0
    p11 = n11 / (n10 + n11) if n10 + n11 else 0.0
    p = (n01 + n11) / (n00 + n01 + n10 + n11)

    def ll(k, m, q):
        return (k * np.log(1 - q) if k else 0.0) + (m * np.log(q) if m else 0.0)

    lr = -2 * (ll(n00 + n10, n01 + n11, p) - ll(n00, n01, p01) - ll(n10, n11, p11))
    lr = max(float(lr), 0.0)
    return lr, float(stats.chi2.sf(lr, 1))


def var_backtest(r: pd.Series, forecasts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for name, f in forecasts.items():
        x = r.loc[f.index].to_numpy()
        for a in VAR_LEVELS:
            q = f["q01" if a == 0.01 else "q05"].to_numpy()
            hits = x < q
            lr_uc, p_uc = kupiec(hits, a)
            lr_ind, p_ind = christoffersen(hits)
            lr_cc = lr_uc + lr_ind
            rows.append({
                "model": name, "var_level": f"{int(round((1 - a) * 100))}%", "alpha": a,
                "n": len(hits), "violations": int(hits.sum()), "expected": a * len(hits),
                "rate": hits.mean(), "kupiec_lr": lr_uc, "kupiec_p": p_uc,
                "christoffersen_lr": lr_ind, "christoffersen_p": p_ind,
                "cc_lr": lr_cc, "cc_p": float(stats.chi2.sf(lr_cc, 2)),
                "max_cluster": int(_longest_cluster(hits, window=10)),
            })
    return pd.DataFrame(rows)


def _longest_cluster(hits: np.ndarray, window: int) -> int:
    """Most violations inside any `window`-day span (a crude clustering measure)."""
    if len(hits) < window:
        return int(hits.sum())
    c = np.convolve(hits.astype(int), np.ones(window, dtype=int), mode="valid")
    return int(c.max())


# ----------------------------------------------------------------------------- H3
def h3_bernoulli(r: pd.Series) -> dict:
    x = r[r != 0]
    up = (x > 0).astype(int).to_numpy()
    n = len(up)
    k = int(up.sum())
    binom = stats.binomtest(k, n, 0.5)

    # Wald-Wolfowitz runs test
    runs = 1 + int(np.sum(up[1:] != up[:-1]))
    n1, n2 = k, n - k
    mu_r = 2 * n1 * n2 / n + 1
    var_r = 2 * n1 * n2 * (2 * n1 * n2 - n) / (n ** 2 * (n - 1))
    z_runs = (runs - mu_r) / np.sqrt(var_r)

    # first-order Markov chain: does yesterday's direction change today's odds?
    a, b = up[:-1], up[1:]
    p_uu = b[a == 1].mean()
    p_ud = b[a == 0].mean()
    table = np.array([[np.sum((a == 0) & (b == 0)), np.sum((a == 0) & (b == 1))],
                      [np.sum((a == 1) & (b == 0)), np.sum((a == 1) & (b == 1))]])
    chi2, p_markov, _, _ = stats.chi2_contingency(table, correction=False)

    # memory in direction vs memory in size
    s = np.where(up == 1, 1.0, -1.0)
    ab = np.abs(x.to_numpy())
    acf_sign = [_acf(s, L) for L in range(1, 21)]
    acf_abs = [_acf(ab, L) for L in range(1, 21)]
    band = 1.96 / np.sqrt(n)
    lb_sign = _ljung_box(s, 10)
    lb_abs = _ljung_box(ab, 10)

    by_decade = []
    dec = (x.index.year // 10) * 10
    for d in sorted(set(dec)):
        u = up[dec == d]
        if len(u) > 200:
            by_decade.append({"decade": f"{d}s", "n": len(u), "p_up": float(u.mean()),
                              "sign_acf1": float(_acf(np.where(u == 1, 1.0, -1.0), 1))})
    return {
        "n": n, "up_days": k, "p_up": k / n, "binom_p_vs_half": float(binom.pvalue),
        "runs": runs, "runs_expected": mu_r, "runs_z": float(z_runs),
        "runs_p": float(2 * stats.norm.sf(abs(z_runs))),
        "p_up_after_up": float(p_uu), "p_up_after_down": float(p_ud),
        "markov_chi2": float(chi2), "markov_p": float(p_markov),
        "acf_sign": acf_sign, "acf_abs": acf_abs, "acf_band": band,
        "ljung_box10_sign_p": lb_sign, "ljung_box10_abs_p": lb_abs,
        "by_decade": by_decade,
    }


def _acf(x: np.ndarray, lag: int) -> float:
    x = x - x.mean()
    denom = np.dot(x, x)
    return float(np.dot(x[:-lag], x[lag:]) / denom) if denom > 0 else 0.0


def _ljung_box(x: np.ndarray, lags: int) -> float:
    n = len(x)
    q = n * (n + 2) * sum(_acf(x, L) ** 2 / (n - L) for L in range(1, lags + 1))
    return float(stats.chi2.sf(q, lags))


# ----------------------------------------------------------------------------- H4
def _count_test(events: pd.Series) -> dict:
    """Poisson checks on calendar-month counts of a 0/1 daily event series."""
    counts = events.groupby([events.index.year, events.index.month]).sum().to_numpy()
    m = len(counts)
    lam = counts.mean()
    var = counts.var(ddof=1)
    disp = var / lam if lam > 0 else np.nan
    stat = (m - 1) * disp
    p_over = float(stats.chi2.sf(stat, m - 1))
    kmax = int(max(counts.max(), 1))
    observed = np.bincount(counts.astype(int), minlength=kmax + 1)
    expected = m * stats.poisson.pmf(np.arange(kmax + 1), lam)
    expected[-1] += m * stats.poisson.sf(kmax, lam)
    # inter-arrival gaps in trading days; exponential arrivals give CV = 1
    pos = np.flatnonzero(events.to_numpy())
    gaps = np.diff(pos)
    cv = float(gaps.std(ddof=1) / gaps.mean()) if len(gaps) > 2 else float("nan")
    return {
        "months": m, "events": int(counts.sum()), "lambda": float(lam), "variance": float(var),
        "dispersion": float(disp), "dispersion_p": p_over,
        "zero_months_observed": float((counts == 0).mean()),
        "zero_months_poisson": float(np.exp(-lam)),
        "max_month": int(counts.max()),
        "p_max_or_more_poisson": float(stats.poisson.sf(counts.max() - 1, lam)),
        "count_hist_observed": observed.tolist(),
        "count_hist_poisson": expected.tolist(),
        "gap_cv": cv,
    }


def h4_poisson(r: pd.Series, garch_t: pd.DataFrame) -> dict:
    mu, sd = r.mean(), r.std(ddof=1)
    raw = {}
    for k in TAIL_K_SENSITIVITY:
        raw[str(k)] = _count_test(((r - mu).abs() > k * sd).astype(int))
    # Same tail probability, but measured against GARCH-t's day-by-day forecast.
    # If clustering is what breaks Poisson, these events should look Poisson again.
    p_tail = 2 * stats.norm.sf(TAIL_K)
    u = garch_t["pit"]
    filt = ((u < p_tail / 2) | (u > 1 - p_tail / 2)).astype(int)
    raw_oos = ((r.loc[u.index] - mu).abs() > TAIL_K * sd).astype(int)
    return {
        "threshold_k": TAIL_K,
        "raw": raw,
        "raw_oos_window": _count_test(raw_oos),
        "garch_filtered": _count_test(filt),
        "tail_probability": float(p_tail),
    }


# ----------------------------------------------------------------------------- H5
def berkowitz(u: np.ndarray) -> tuple[float, float]:
    """Berkowitz (2001) LR test: z = Phi^-1(u) should be i.i.d. N(0,1). Returns (LR, p)."""
    z = stats.norm.ppf(np.clip(u, 1e-12, 1 - 1e-12))
    y, x = z[1:], z[:-1]
    X = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    s2 = resid.var()
    ll_alt = np.sum(stats.norm.logpdf(resid, 0, np.sqrt(s2)))
    ll_null = np.sum(stats.norm.logpdf(y, 0, 1))
    lr = -2 * (ll_null - ll_alt)
    return float(lr), float(stats.chi2.sf(lr, 3))


def h5_uniform(forecasts: dict[str, pd.DataFrame], bins: int = 20) -> pd.DataFrame:
    rows = []
    for name, f in forecasts.items():
        u = f["pit"].to_numpy()
        n = len(u)
        hist, _ = np.histogram(u, bins=bins, range=(0, 1))
        chi2 = float(np.sum((hist - n / bins) ** 2 / (n / bins)))
        lr_b, p_b = berkowitz(u)
        ks = stats.kstest(u, "uniform")
        rows.append({
            "model": name, "n": n,
            "ks_stat": float(ks.statistic), "ks_p": float(ks.pvalue),
            "chi2_20bin": chi2, "chi2_p": float(stats.chi2.sf(chi2, bins - 1)),
            "berkowitz_lr": lr_b, "berkowitz_p": p_b,
            "below_1pct": float(np.mean(u < 0.01)), "above_99pct": float(np.mean(u > 0.99)),
            "hist": hist.tolist(),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------- Expected Shortfall
def _z2_bootstrap(contrib: np.ndarray, alpha: float, n_boot: int, seed: int):
    """Acerbi-Szekely (2014) Z2 statistic and a percentile-bootstrap p-value.

    Z2 = mean_t[ r_t * 1{r_t <= VaR_t} / ES_t ] / alpha - 1, which has expectation 0
    when the reported ES is correct. Z2 > 0 means realised tail losses are larger than
    the model's ES (risk understated); Z2 < 0 means the ES is conservative. The null is
    not analytically tractable, so the two-sided p-value is taken from the bootstrap
    distribution of Z2 over the per-day contributions.
    """
    z2 = float(contrib.mean() / alpha - 1.0)
    rng = np.random.default_rng(seed)
    n = len(contrib)
    boot = contrib[rng.integers(0, n, size=(n_boot, n))].mean(axis=1) / alpha - 1.0
    se = float(boot.std(ddof=1))
    p = 2.0 * min(float((boot <= 0).mean()), float((boot >= 0).mean()))
    return z2, se, min(p, 1.0)


def es_backtest(r: pd.Series, forecasts: dict[str, pd.DataFrame], n_boot: int = 2000,
                seed: int = 0) -> pd.DataFrame:
    """Expected-Shortfall calibration for every model at the 99% and 95% levels.

    For each model the breach days (r_t below the VaR quantile) are collected, the mean
    realised return on those days is compared with the mean forecast ES, and the
    Acerbi-Szekely Z2 statistic is tested against zero by bootstrap. ES is the Basel/FRTB
    tail measure; unlike VaR it is not elicitable, so a dedicated backtest is needed.
    """
    rows = []
    for name, f in forecasts.items():
        x = r.loc[f.index].to_numpy()
        for a in VAR_LEVELS:
            col = "01" if a == 0.01 else "05"
            q = f[f"q{col}"].to_numpy()
            e = f[f"es{col}"].to_numpy()
            breach = x < q
            nb = int(breach.sum())
            with np.errstate(divide="ignore", invalid="ignore"):
                contrib = np.where(breach & (e < 0), x / e, 0.0)
            z2, se, p = _z2_bootstrap(contrib, a, n_boot, seed)
            realized = float(x[breach].mean()) if nb else float("nan")
            predicted = float(e[breach].mean()) if nb else float("nan")
            ratio = float(realized / predicted) if nb and predicted != 0 else float("nan")
            rows.append({
                "model": name, "var_level": f"{int(round((1 - a) * 100))}%", "alpha": a,
                "n": len(x), "violations": nb, "expected": a * len(x),
                "es_realized": realized, "es_predicted": predicted, "es_ratio": ratio,
                "z2": z2, "z2_se": se, "z2_p": p,
            })
    return pd.DataFrame(rows)


# ----------------------------------------- Comparative (VaR, ES) forecast ranking
def fz0_loss(x: np.ndarray, v: np.ndarray, e: np.ndarray, alpha: float) -> np.ndarray:
    """Fissler-Ziegel 0-homogeneous loss for the joint (VaR, ES) functional.

    Nolde & Ziegel (2017); Patton, Ziegel & Chen (2019). `v` and `e` are the model's
    alpha-quantile and Expected Shortfall as returns (both negative in the left tail),
    `x` the realised return. The score

        L = -1{x<=v}(v-x)/(alpha*e) + v/e + ln(-e) - 1

    is strictly consistent for the pair (VaR_alpha, ES_alpha): its expectation is
    minimised only by the true quantile and ES together. Because ES alone is not
    elicitable, no loss scores ES by itself; this one scores the pair jointly. It is
    scale-invariant (0-homogeneous), so daily scores stay comparable as volatility
    moves, which is what makes the model comparison below fair. Lower is better.
    """
    e = np.minimum(np.asarray(e, dtype=float), -1e-12)
    hit = (np.asarray(x) <= np.asarray(v)).astype(float)
    return -hit * (v - x) / (alpha * e) + v / e + np.log(-e) - 1.0


def _hac_se(d: np.ndarray, lag: int | None = None) -> float:
    """Newey-West HAC standard error of the mean of d (Bartlett weights)."""
    n = len(d)
    if n < 2:
        return float("nan")
    if lag is None:
        lag = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    dc = d - d.mean()
    s = float(np.dot(dc, dc) / n)
    for L in range(1, min(lag, n - 1) + 1):
        w = 1.0 - L / (lag + 1.0)
        s += 2.0 * w * float(np.dot(dc[L:], dc[:-L]) / n)
    return np.sqrt(max(s, 0.0) / n)


def dm_test(loss_a: np.ndarray, loss_b: np.ndarray, lag: int | None = None):
    """Diebold-Mariano (1995) test of equal predictive accuracy on two loss series.

    One-day-ahead forecasts do not overlap, so the loss differential needs no special
    horizon correction beyond a HAC variance for any residual dependence. Returns
    (mean differential a-b, DM statistic, two-sided p). A negative statistic means
    model a scores lower (better) than model b.
    """
    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    se = _hac_se(d, lag)
    stat = float(d.mean() / se) if se and se > 0 else 0.0
    return float(d.mean()), stat, float(2 * stats.norm.sf(abs(stat)))


def _moving_block_indices(n: int, block: int, rng) -> np.ndarray:
    """One moving-block-bootstrap resample of the indices 0..n-1."""
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=n_blocks)
    idx = (starts[:, None] + np.arange(block)[None, :]).ravel()
    return idx[:n]


def model_confidence_set(losses: dict, alpha: float = 0.10, n_boot: int = 2000,
                         block: int | None = None, seed: int = 0) -> dict:
    """Hansen, Lunde & Nason (2011) Model Confidence Set via the range statistic T_R
    and a moving-block bootstrap of the loss differentials.

    Starting from all models, the test of equal predictive ability is the largest
    studentised pairwise loss gap T_R = max_{i,j} |t_ij|, with t_ij = mean(d_ij) over a
    bootstrap standard error of that mean. If the bootstrap p-value is below `alpha` the
    set is not equivalent and the worst model (largest average studentised gap) is
    dropped; the procedure repeats on the survivors. Each model's MCS p-value is the
    running maximum of the test p-values up to its elimination, so survivors carry the
    final (largest) p-value. Models with MCS p-value > alpha form the (1-alpha) set.

    Returns {model: {"mcs_p": float, "rank": int}} with rank 1 = eliminated first.
    """
    names = list(losses)
    L = np.column_stack([np.asarray(losses[m], dtype=float) for m in names])  # T x M
    T, M = L.shape
    if block is None:
        block = max(2, int(round(T ** (1.0 / 3.0))))
    rng = np.random.default_rng(seed)
    # one shared set of bootstrap index draws, reused as models are eliminated
    boot_idx = [_moving_block_indices(T, block, rng) for _ in range(n_boot)]
    # bootstrap means of each column: B x M
    boot_col = np.stack([L[ix].mean(axis=0) for ix in boot_idx])  # B x M
    col_mean = L.mean(axis=0)  # M

    alive = list(range(M))
    mcs_p = {m: None for m in names}
    rank = {m: None for m in names}
    order = 0
    running_p = 0.0
    while len(alive) > 1:
        a = np.array(alive)
        dbar = col_mean[a][:, None] - col_mean[a][None, :]          # k x k observed gaps
        bdiff = boot_col[:, a][:, :, None] - boot_col[:, a][:, None, :]  # B x k x k
        var = ((bdiff - dbar[None]) ** 2).mean(axis=0)              # k x k boot variance
        sd = np.sqrt(np.maximum(var, 1e-24))
        t_obs = np.abs(dbar) / sd
        np.fill_diagonal(t_obs, 0.0)
        TR = float(t_obs.max())
        tb = np.abs(bdiff - dbar[None]) / sd[None]
        for b in range(tb.shape[0]):
            np.fill_diagonal(tb[b], 0.0)
        TR_boot = tb.reshape(tb.shape[0], -1).max(axis=1)
        pval = float((TR_boot >= TR).mean())
        running_p = max(running_p, pval)
        if pval > alpha:
            break
        # eliminate the model with the largest average studentised gap to the others
        t_i = (dbar / sd).mean(axis=1)  # positive => scores higher (worse) than peers
        worst = a[int(np.argmax(t_i))]
        order += 1
        mcs_p[names[worst]] = running_p
        rank[names[worst]] = order
        alive.remove(worst)
    for m in alive:
        order += 1
        mcs_p[names[m]] = max(running_p, 1.0 if len(alive) else running_p)
        rank[names[m]] = order
    # survivors keep p = 1.0 (never rejected); clamp
    for m in names:
        if mcs_p[m] is None:
            mcs_p[m] = 1.0
    return {m: {"mcs_p": min(1.0, float(mcs_p[m])), "rank": int(rank[m])} for m in names}


def fz_ranking(r: pd.Series, forecasts: dict, alpha_levels=VAR_LEVELS, n_boot: int = 2000,
               seed: int = 0) -> pd.DataFrame:
    """Rank the models by joint (VaR, ES) forecast quality at each tail level.

    For every model the daily Fissler-Ziegel 0-homogeneous loss is averaged, the best
    (lowest-loss) model is identified, each other model is compared with it by a
    Diebold-Mariano test, and a Model Confidence Set is built over all models. This turns
    the pass/fail ES backtest into an ordering: which forecast is sharpest, and which
    models are statistically indistinguishable from the best.
    """
    rows = []
    for a in alpha_levels:
        col = "01" if a == 0.01 else "05"
        losses = {}
        for name, f in forecasts.items():
            x = r.loc[f.index].to_numpy()
            losses[name] = fz0_loss(x, f[f"q{col}"].to_numpy(), f[f"es{col}"].to_numpy(), a)
        avg = {k: float(np.mean(v)) for k, v in losses.items()}
        best = min(avg, key=avg.get)
        mcs = model_confidence_set(losses, n_boot=n_boot, seed=seed)
        for name in forecasts:
            dbar, stat, pv = dm_test(losses[name], losses[best])
            rows.append({
                "model": name, "var_level": f"{int(round((1 - a) * 100))}%", "alpha": a,
                "n": len(losses[name]), "fz_loss": avg[name], "best_model": best,
                "dm_vs_best": stat, "dm_p": pv,
                "mcs_p": mcs[name]["mcs_p"], "in_mcs": bool(mcs[name]["mcs_p"] > 0.10),
                "mcs_rank": mcs[name]["rank"],
            })
    return pd.DataFrame(rows)
