"""Rolling out-of-sample one-day-ahead predictive distributions.

Point-in-time rule: the forecast for day t is built only from returns dated t-1 or
earlier. Parameters are re-estimated every `refit` days on the trailing `window`
returns; between refits the GARCH variance is filtered forward with fixed parameters,
which also only uses past returns. `tests/test_no_lookahead.py` enforces this by
perturbing future returns and checking that earlier forecasts do not change.

Each model returns a DataFrame indexed by forecast date with:
    pit  - predictive CDF evaluated at the realised return, F_{t|t-1}(r_t)
    q01, q05 - predictive 1% and 5% quantiles (the 99% / 95% one-day VaR, as returns)
    es01, es05 - the matching Expected Shortfall E[r_t | r_t <= VaR], as returns.
                 ES is read off the same predictive distribution as the quantile, so
                 it inherits the point-in-time guarantee and is at least as extreme.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy import stats

WINDOW = 1000   # trading days in each estimation window (about four years)
REFIT = 21      # re-estimate parameters about once a month
HS_WINDOW = 250  # historical-simulation lookback (one trading year, the Basel convention)

# 100 midpoints of the unit interval, used to average the quantile function over a tail.
_ES_GRID = (np.arange(1, 101) - 0.5) / 100.0


def _frame(index, pit, q01, q05, es01=None, es05=None) -> pd.DataFrame:
    d = {"pit": pit, "q01": q01, "q05": q05}
    if es01 is not None:
        d["es01"], d["es05"] = es01, es05
    return pd.DataFrame(d, index=index)


def _tail_mean(ppf, alpha):
    """Expected shortfall E[X | X <= q_alpha] as the mean of the quantile function over
    the lowest `alpha` of the distribution. `ppf(u)` is evaluated on an array of
    probabilities; it may return a 1-D array (scalar parameters) or a (len(u), n) matrix
    (per-day parameters), in which case the tail is averaged along the probability axis."""
    v = np.asarray(ppf(_ES_GRID * alpha))
    return v.mean(axis=0) if v.ndim > 1 else float(v.mean())


def static_normal(r: pd.Series, window: int = WINDOW) -> pd.DataFrame:
    """Normal with mean and std from the trailing window (no volatility dynamics)."""
    mu = r.rolling(window).mean().shift(1)
    sd = r.rolling(window).std(ddof=1).shift(1)
    ok = mu.notna()
    mu, sd, x = mu[ok], sd[ok], r[ok]
    mv, sv = mu.to_numpy(), sd.to_numpy()
    ppf = lambda u: stats.norm.ppf(u[:, None], mv[None, :], sv[None, :])
    return _frame(x.index, stats.norm.cdf(x, mu, sd),
                  stats.norm.ppf(0.01, mu, sd), stats.norm.ppf(0.05, mu, sd),
                  _tail_mean(ppf, 0.01), _tail_mean(ppf, 0.05))


def static_t(r: pd.Series, window: int = WINDOW, refit: int = REFIT) -> pd.DataFrame:
    """Location-scale Student-t fitted by MLE on the trailing window, refit monthly."""
    vals = r.to_numpy()
    n = len(vals)
    pit = np.full(n, np.nan)
    q01 = np.full(n, np.nan)
    q05 = np.full(n, np.nan)
    es01 = np.full(n, np.nan)
    es05 = np.full(n, np.nan)
    for s in range(window, n, refit):
        df_, loc, scale = stats.t.fit(vals[s - window:s])
        e = min(s + refit, n)
        pit[s:e] = stats.t.cdf(vals[s:e], df_, loc, scale)
        q01[s:e] = stats.t.ppf(0.01, df_, loc, scale)
        q05[s:e] = stats.t.ppf(0.05, df_, loc, scale)
        es01[s:e] = _tail_mean(lambda u: stats.t.ppf(u, df_, loc, scale), 0.01)
        es05[s:e] = _tail_mean(lambda u: stats.t.ppf(u, df_, loc, scale), 0.05)
    sl = slice(window, n)
    return _frame(r.index[sl], pit[sl], q01[sl], q05[sl], es01[sl], es05[sl])


def historical_simulation(r: pd.Series, window: int = HS_WINDOW) -> pd.DataFrame:
    """Empirical distribution of the trailing window. PIT is the mid-rank of r_t in it.
    ES is the mean of the worst alpha-fraction of the window (the historical-simulation ES)."""
    vals = r.to_numpy()
    n = len(vals)
    pit = np.full(n, np.nan)
    q01 = np.full(n, np.nan)
    q05 = np.full(n, np.nan)
    es01 = np.full(n, np.nan)
    es05 = np.full(n, np.nan)
    k01 = max(1, int(0.01 * window))
    k05 = max(1, int(0.05 * window))
    for t in range(window, n):
        past = vals[t - window:t]
        pit[t] = (np.sum(past < vals[t]) + 0.5 * np.sum(past == vals[t]) + 0.5) / (window + 1)
        q01[t], q05[t] = np.quantile(past, [0.01, 0.05])
        sp = np.sort(past)
        es01[t] = sp[:k01].mean()
        es05[t] = sp[:k05].mean()
    sl = slice(window, n)
    return _frame(r.index[sl], pit[sl], q01[sl], q05[sl], es01[sl], es05[sl])


_DISTS = {"normal": "normal", "t": "t", "skewt": "skewt"}


def _fit_garch(x: np.ndarray, dist: str, o: int = 0) -> dict:
    """Fit constant-mean GARCH(1,1) (o=1 adds the GJR leverage term) on x in percent.

    Returns parameters in percent units plus the standardised shape parameters
    (`shape`: [] for normal, [nu] for t, [eta, lambda] for Hansen's skewed t).
    """
    from arch import arch_model

    am = arch_model(x, mean="Constant", vol="GARCH", p=1, o=o, q=1, dist=_DISTS[dist],
                    rescale=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = am.fit(disp="off", show_warning=False, options={"maxiter": 500})
    p = res.params
    out = {"mu": p["mu"], "omega": p["omega"], "alpha": p["alpha[1]"], "beta": p["beta[1]"],
           "gamma": p["gamma[1]"] if o else 0.0}
    names = am.distribution.parameter_names()
    out["shape"] = [float(p[n]) for n in names]
    for n in names:
        out[n] = float(p[n])
    return out


def _arch_dist(dist: str):
    from arch.univariate import Normal, SkewStudent, StudentsT

    return {"normal": Normal, "t": StudentsT, "skewt": SkewStudent}[dist]()


def garch(r: pd.Series, dist: str, o: int = 0, window: int = WINDOW, refit: int = REFIT,
          return_params: bool = False, fhs: bool = False):
    """GARCH(1,1) / GJR-GARCH(1,1) with normal, t or skewed-t shocks, rolling refit.

    The conditional variance for day t is
        sigma2_t = omega + (alpha + gamma * 1[e_{t-1} < 0]) * e_{t-1}^2 + beta * sigma2_{t-1},
    with e = r - mu, started at the sample variance of the estimation window and filtered
    through the window, so sigma2_t never sees r_t. All shock distributions are
    standardised to unit variance (arch's convention). ES uses the same standardised-shock
    tail mean scaled by the day's sigma, mirroring the quantile.

    With fhs=True the fitted dynamics are kept but the parametric shock law is replaced by
    the empirical distribution of the estimation window's standardised residuals
    (filtered historical simulation, Barone-Adesi et al. 1999). Those residuals are dated
    before the forecast block, so the point-in-time rule is unchanged.
    """
    x = r.to_numpy() * 100.0
    n = len(x)
    pit = np.full(n, np.nan)
    q01 = np.full(n, np.nan)
    q05 = np.full(n, np.nan)
    es01 = np.full(n, np.nan)
    es05 = np.full(n, np.nan)
    sig = np.full(n, np.nan)
    params = []
    D = _arch_dist(dist)
    for s in range(window, n, refit):
        est = x[s - window:s]
        p = _fit_garch(est, dist, o)
        params.append({"date": r.index[s], **{k: v for k, v in p.items() if k != "shape"}})
        e = min(s + refit, n)
        eps = x[s - window:e] - p["mu"]
        s2 = np.empty(len(eps))
        s2[0] = np.var(est)
        for i in range(1, len(eps)):
            a = p["alpha"] + (p["gamma"] if eps[i - 1] < 0 else 0.0)
            s2[i] = p["omega"] + a * eps[i - 1] ** 2 + p["beta"] * s2[i - 1]
        sd = np.sqrt(s2[window:])  # forecasts for days s .. e-1
        z = (x[s:e] - p["mu"]) / sd
        if fhs:
            zw = np.sort(eps[:window] / np.sqrt(s2[:window]))  # in-window residuals only
            lo = np.searchsorted(zw, z, side="left")
            hi = np.searchsorted(zw, z, side="right")
            pit[s:e] = (lo + 0.5 * (hi - lo) + 0.5) / (window + 1)
            zq = np.quantile(zw, [0.01, 0.05])
            ez01 = float(zw[zw <= zq[0]].mean())
            ez05 = float(zw[zw <= zq[1]].mean())
        else:
            shape = np.asarray(p["shape"]) if p["shape"] else None
            pit[s:e] = D.cdf(z, shape)
            zq = D.ppf(np.array([0.01, 0.05]), shape)
            ez01 = float(np.mean(D.ppf(_ES_GRID * 0.01, shape)))
            ez05 = float(np.mean(D.ppf(_ES_GRID * 0.05, shape)))
        q01[s:e] = (p["mu"] + sd * zq[0]) / 100.0
        q05[s:e] = (p["mu"] + sd * zq[1]) / 100.0
        es01[s:e] = (p["mu"] + sd * ez01) / 100.0
        es05[s:e] = (p["mu"] + sd * ez05) / 100.0
        sig[s:e] = sd / 100.0
    sl = slice(window, n)
    out = _frame(r.index[sl], pit[sl], q01[sl], q05[sl], es01[sl], es05[sl])
    out["sigma"] = sig[sl]
    if return_params:
        return out, pd.DataFrame(params).set_index("date")
    return out


MODELS = {
    "Normal": static_normal,
    "Student-t": static_t,
    "Hist. sim.": historical_simulation,
    "GARCH-N": lambda r: garch(r, "normal"),
    "GARCH-t": lambda r: garch(r, "t"),
    "GJR-skew-t": lambda r: garch(r, "skewt", o=1),
    "GJR-FHS": lambda r: garch(r, "skewt", o=1, fhs=True),
}


def all_forecasts(r: pd.Series) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Run every model, align them on the common out-of-sample dates.

    Returns the forecasts and the GJR-skew-t parameter path (one row per refit).
    """
    out = {}
    params = None
    for name, f in MODELS.items():
        if name == "GJR-skew-t":
            out[name], params = garch(r, "skewt", o=1, return_params=True)
        else:
            out[name] = f(r)
    common = None
    for df in out.values():
        idx = df.dropna(subset=["pit"]).index
        common = idx if common is None else common.intersection(idx)
    return {k: v.loc[common] for k, v in out.items()}, params
