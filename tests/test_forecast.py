import numpy as np
import pandas as pd
from scipy import stats

from mdt import forecast as F
from tests.helpers import simulate_garch_t


def test_no_lookahead_future_shock_does_not_move_past_forecasts():
    """Corrupt every return from day `cut` on; forecasts dated before `cut` must not change."""
    r = simulate_garch_t(1400, seed=11)
    cut = 1250
    r2 = r.copy()
    r2.iloc[cut:] = r2.iloc[cut:] * 5 + 0.05
    for name in ["Normal", "Student-t", "Hist. sim.", "GARCH-N"]:
        a = F.MODELS[name](r).dropna()
        b = F.MODELS[name](r2).dropna()
        before = a.index[a.index < r.index[cut]]
        pd.testing.assert_frame_equal(a.loc[before, ["q01", "q05"]], b.loc[before, ["q01", "q05"]],
                                      check_exact=False, rtol=1e-10)
        # the forecast for day `cut` itself also uses only days < cut
        assert np.isclose(a.loc[r.index[cut], "q01"], b.loc[r.index[cut], "q01"])


def test_garch_variance_matches_arch_filter():
    """Our hand-rolled variance recursion must agree with arch's on the estimation window."""
    from arch import arch_model

    r = simulate_garch_t(1200, seed=12)
    x = r.to_numpy()[:1000] * 100
    res = arch_model(x, mean="Constant", vol="GARCH", p=1, q=1, dist="normal",
                     rescale=False).fit(disp="off")
    p = F._fit_garch(x, "normal")
    eps = x - p["mu"]
    s2 = np.empty(len(x)); s2[0] = np.var(x)
    for i in range(1, len(x)):
        s2[i] = p["omega"] + p["alpha"] * eps[i - 1] ** 2 + p["beta"] * s2[i - 1]
    # different start values wash out within a few hundred days
    assert np.allclose(np.sqrt(s2[300:]), res.conditional_volatility[300:], rtol=1e-3)


def test_correct_model_gives_uniform_pit():
    r = simulate_garch_t(3000, seed=13)
    f = F.garch(r, "t")
    u = f["pit"].dropna().to_numpy()
    assert stats.kstest(u, "uniform").pvalue > 0.01
    assert 0.005 < np.mean(u < 0.01) < 0.017
