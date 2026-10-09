import numpy as np
import pandas as pd


def series(x, start="2000-01-03"):
    return pd.Series(x, index=pd.bdate_range(start, periods=len(x)), name="r")


def simulate_garch_t(n, mu=0.03, omega=0.02, alpha=0.08, beta=0.9, nu=6.0, seed=0):
    """GARCH(1,1) with standardised-t shocks, returned as decimal log returns."""
    rng = np.random.default_rng(seed)
    k = np.sqrt((nu - 2) / nu)
    s2 = omega / (1 - alpha - beta)
    out = np.empty(n)
    eps_prev = 0.0
    for i in range(n):
        s2 = omega + alpha * eps_prev ** 2 + beta * s2
        eps_prev = np.sqrt(s2) * k * rng.standard_t(nu)
        out[i] = mu + eps_prev
    return series(out / 100.0)
