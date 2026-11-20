import numpy as np
from scipy import stats

from mdt import forecast as F
from mdt import hypotheses as H
from tests.helpers import simulate_garch_t


def test_fz0_is_minimised_at_the_true_var_and_es():
    """The 0-homogeneous FZ loss is strictly consistent for (VaR, ES): with one argument
    held at its true value, the other is recovered by minimising the average loss."""
    x = np.random.default_rng(0).standard_normal(2_000_000)
    a = 0.025
    v_t = stats.norm.ppf(a)
    e_t = -stats.norm.pdf(v_t) / a
    grid = np.linspace(v_t - 0.4, v_t + 0.4, 17)
    v_hat = min(grid, key=lambda v: H.fz0_loss(x, np.full_like(x, v), np.full_like(x, e_t), a).mean())
    e_hat = min(grid - v_t + e_t,
                key=lambda e: H.fz0_loss(x, np.full_like(x, v_t), np.full_like(x, e), a).mean())
    assert abs(v_hat - v_t) < 0.06
    assert abs(e_hat - e_t) < 0.06


def test_dm_has_no_false_signal_between_identical_forecasts():
    r = simulate_garch_t(2500, seed=7)
    fc, _ = F.all_forecasts(r)
    x = r.loc[fc["GARCH-t"].index].to_numpy()
    f = fc["GARCH-t"]
    loss = H.fz0_loss(x, f["q01"].to_numpy(), f["es01"].to_numpy(), 0.01)
    dbar, stat, p = H.dm_test(loss, loss.copy())
    assert abs(dbar) < 1e-12 and p == 1.0


def test_mcs_keeps_the_true_model_and_drops_the_misspecified_one():
    """GARCH-t data: the dynamic true model is in the 90% MCS, the static Normal is not."""
    r = simulate_garch_t(4000, seed=22)
    fc, _ = F.all_forecasts(r)
    fz = H.fz_ranking(r, fc, n_boot=1000)
    at95 = fz[fz.alpha == 0.05].set_index("model")
    assert at95.loc["GARCH-t", "in_mcs"]
    assert not at95.loc["Normal", "in_mcs"]
    # the best model is a dynamic one, never an unconditional model
    assert fz[fz.alpha == 0.05]["best_model"].iloc[0] in ("GARCH-t", "GARCH-N", "GJR-skew-t")
