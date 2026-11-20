import numpy as np

from mdt import forecast as F
from mdt import hypotheses as H
from tests.helpers import simulate_garch_t


def test_es_is_at_least_as_extreme_as_var():
    r = simulate_garch_t(3000, seed=21)
    for name in F.MODELS:
        f = F.MODELS[name](r).dropna()
        assert (f["es01"] <= f["q01"] + 1e-12).all(), name
        assert (f["es05"] <= f["q05"] + 1e-12).all(), name


def test_es_backtest_accepts_the_true_model():
    """GARCH-t data judged by the GARCH-t model: ES should be well calibrated."""
    r = simulate_garch_t(4000, seed=22)
    fc, _ = F.all_forecasts(r)
    es = H.es_backtest(r, fc, n_boot=1000)
    row = es[(es.model == "GARCH-t") & (es.alpha == 0.01)].iloc[0]
    assert row.z2_p > 0.05
    assert 0.6 < row.es_ratio < 1.6


def test_es_backtest_flags_a_thin_tailed_model():
    """GARCH-t data judged by the thin-tailed Normal: ES should be understated (z2 > 0)."""
    r = simulate_garch_t(4000, seed=23)
    fc, _ = F.all_forecasts(r)
    es = H.es_backtest(r, fc, n_boot=1000)
    row = es[(es.model == "Normal") & (es.alpha == 0.01)].iloc[0]
    assert row.z2 > 0
