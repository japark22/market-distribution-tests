import numpy as np

from mdt import forecast as F
from mdt import hypotheses as H
from tests.helpers import simulate_garch_t


def test_elementary_scores_integrate_to_the_pinball_loss():
    """Ehm et al. (2016): the elementary scores integrate over the threshold to the
    pinball loss, and each is non-negative."""
    rng = np.random.default_rng(0)
    x = rng.normal(size=120)
    y = rng.normal(size=120)
    a = 0.05
    theta = np.linspace(-8, 8, 20001)
    S = H.elementary_quantile_score(theta, x, y, a)
    integral = np.sum((S[:, :-1] + S[:, 1:]) / 2.0 * np.diff(theta)[None, :], axis=1)
    pinball = (np.where(y <= x, 1.0, 0.0) - a) * (x - y)
    assert np.abs(integral - pinball).max() < 2e-3
    assert S.min() >= -1e-12


def test_a_sharper_forecast_has_the_lower_murphy_curve():
    """The true GARCH-t VaR should have a lower pinball (integrated Murphy score) than the
    static Normal on GARCH-t data, and the best model is a dynamic one."""
    r = simulate_garch_t(4000, seed=22)
    fc, _ = F.all_forecasts(r)
    _, dom = H.murphy_quantile(r, fc)
    at99 = dom[dom.alpha == 0.01].set_index("model")
    assert at99.loc["GARCH-t", "pinball"] <= at99.loc["Normal", "pinball"]
    assert at99["best_model"].iloc[0] in ("GARCH-t", "GARCH-N", "GJR-skew-t")
    # the reference model is never scored worse than itself
    assert at99.loc[at99["best_model"].iloc[0], "max_gap"] == 0.0
