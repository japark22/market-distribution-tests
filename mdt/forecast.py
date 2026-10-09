"""Rolling out-of-sample one-day-ahead predictive distributions.

Point-in-time rule: the forecast for day t is built only from returns dated t-1 or
earlier. Parameters are re-estimated every `refit` days on the trailing `window`
returns; between refits the GARCH variance is filtered forward with fixed parameters,
which also only uses past returns. `tests/test_no_lookahead.py` enforces this by
perturbing future returns and checking that earlier forecasts do not change.

Each model returns a DataFrame indexed by forecast date with:
    pit  - predictive CDF evaluated at the realised return, F_{t|t-1}(r_t)
    q01, q05 - predictive 1% and 5% quantiles (the 99% / 95% one-day VaR, as returns)
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy import stats

WINDOW = 1000   # trading days in each estimation window (about four years)
REFIT = 21      # re-estimate parameters about once a month
HS_WINDOW = 250  # historical-simulation lookback (one trading year, the Basel convention)


def _frame(index, pit, q01, q05) -> pd.DataFrame:
    return pd.DataFrame({"pit": pit, "q01": q01, "q05": q05}, index=index)


def static_normal(r: pd.Series, window: int = WINDOW) -> pd.DataFrame:
    """Normal with mean and std from the trailing window (no volatility dynamics)."""
    mu = r.rolling(window).mean().shift(1)
    sd = r.rolling(window).std(ddof=1).shift(1)
    ok = mu.notna()
    mu, sd, x = mu[ok], sd[ok], r[ok]
    return _frame(x.index, stats.norm.cdf(x, mu, sd),
                  stats.norm.ppf(0.01, mu, sd), stats.norm.ppf(0.05, mu, sd))


def static_t(r: pd.Series, window: int = WINDOW, refit: int = REFIT) -> pd.DataFrame:
    """Location-scale Student-t fitted by MLE on the trailing window, refit monthly."""
    vals = r.to_numpy()
    n = len(vals)
    pit = np.full(n, np.nan)
    q01 = np.full(n, np.nan)
    q05 = np.full(n, np.nan)
    for s in range(window, n, refit):
        df_, loc, scale = stats.t.fit(vals[s - window:s])
        e = min(s + refit, n)
        pit[s:e] = stats.t.cdf(vals[s:e], df_, loc, scale)
        q01[s:e] = stats.t.ppf(0.01, df_, loc, scale)
        q05[s:e] = stats.t.ppf(0.05, df_, loc, scale)
    sl = slice(window, n)
    return _frame(r.index[sl], pit[sl], q01[sl], q05[sl])


def historical_simulation(r: pd.Series, window: int = HS_WINDOW) -> pd.DataFrame:
    """Empirical distribution of the trailing window. PIT is the mid-rank of r_t in it."""
    vals = r.to_numpy()
    n = len(vals)
    pit = np.full(n, np.nan)
    q01 = np.full(n, np.nan)
    q05 = np.full(n, np.nan)
    for t in range(window, n):
        past = vals[t - window:t]
        pit[t] = (np.sum(past < vals[t]) + 0.5 * np.sum(past == vals[t]) + 0.5) / (window + 1)
        q01[t], q05[t] = np.quantile(past, [0.01, 0.05])
    sl = slice(window, n)
    return _frame(r.index[sl], pit[sl], q01[sl], q05[sl])


def _fit_garch(x: np.ndarray, dist: str) -> dict:
    """Fit constant-mean GARCH(1,1) on x (in percent). Returns parameters in percent units."""
    from arch import arch_model

    am = arch_model(x, mean="Constant", vol="GARCH", p=1, q=1,
                    dist="t" if dist == "t" else "normal", rescale=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = am.fit(disp="off", show_warning=False, options={"maxiter": 500})
    p = res.params
    out = {"mu": p["mu"], "omega": p["omega"], "alpha": p["alpha[1]"], "beta": p["beta[1]"]}
    out["nu"] = p["nu"] if dist == "t" else np.inf
    return out


def garch(r: pd.Series, dist: str, window: int = WINDOW, refit: int = REFIT,
          return_params: bool = False):
    """GARCH(1,1) with normal or standardised-t innovations, rolling refit.

    The conditional variance for day t is
        sigma2_t = omega + alpha * (r_{t-1} - mu)^2 + beta * sigma2_{t-1},
    started at the sample variance of the estimation window and filtered through the
    window, so sigma2_t never sees r_t.
    """
    x = r.to_numpy() * 100.0
    n = len(x)
    pit = np.full(n, np.nan)
    q01 = np.full(n, np.nan)
    q05 = np.full(n, np.nan)
    sig = np.full(n, np.nan)
    params = []
    for s in range(window, n, refit):
        est = x[s - window:s]
        p = _fit_garch(est, dist)
        params.append({"date": r.index[s], **p})
        e = min(s + refit, n)
        eps = x[s - window:e] - p["mu"]
        s2 = np.empty(len(eps))
        s2[0] = np.var(est)
        for i in range(1, len(eps)):
            s2[i] = p["omega"] + p["alpha"] * eps[i - 1] ** 2 + p["beta"] * s2[i - 1]
        sd = np.sqrt(s2[window:])  # forecasts for days s .. e-1
        z = (x[s:e] - p["mu"]) / sd
        if dist == "t":
            nu = p["nu"]
            k = np.sqrt((nu - 2.0) / nu)  # standardised t has unit variance
            pit[s:e] = stats.t.cdf(z / k, nu)
            q01[s:e] = (p["mu"] + sd * k * stats.t.ppf(0.01, nu)) / 100.0
            q05[s:e] = (p["mu"] + sd * k * stats.t.ppf(0.05, nu)) / 100.0
        else:
            pit[s:e] = stats.norm.cdf(z)
            q01[s:e] = (p["mu"] + sd * stats.norm.ppf(0.01)) / 100.0
            q05[s:e] = (p["mu"] + sd * stats.norm.ppf(0.05)) / 100.0
        sig[s:e] = sd / 100.0
    sl = slice(window, n)
    out = _frame(r.index[sl], pit[sl], q01[sl], q05[sl])
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
}


def all_forecasts(r: pd.Series) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Run every model, align them on the common out-of-sample dates.

    Returns the forecasts and the GARCH-t parameter path (one row per refit).
    """
    out = {}
    params = None
    for name, f in MODELS.items():
        if name == "GARCH-t":
            out[name], params = garch(r, "t", return_params=True)
        else:
            out[name] = f(r)
    common = None
    for df in out.values():
        idx = df.dropna(subset=["pit"]).index
        common = idx if common is None else common.intersection(idx)
    return {k: v.loc[common] for k, v in out.items()}, params
