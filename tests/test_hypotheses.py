"""Each test should recover the truth on data simulated from a known distribution."""
import numpy as np
import pandas as pd
from scipy import stats

from mdt import hypotheses as H
from tests.helpers import series, simulate_garch_t


def test_normal_data_passes_jarque_bera():
    r = series(np.random.default_rng(1).normal(0, 0.01, 5000))
    out = H.h1_normal(r)
    assert out["jb_p"] > 0.01
    assert abs(out["excess_kurtosis"]) < 0.3


def test_t_fit_recovers_degrees_of_freedom():
    x = stats.t.rvs(4, scale=0.01, size=20000, random_state=2)
    out = H.h2_student_t(series(x))
    assert 3.5 < out["nu"] < 4.6
    assert out["aic_t"] < out["aic_normal"]


def test_kupiec_accepts_correct_rate_and_rejects_double():
    rng = np.random.default_rng(3)
    ok = rng.random(5000) < 0.01
    bad = rng.random(5000) < 0.02
    assert H.kupiec(ok, 0.01)[1] > 0.01
    assert H.kupiec(bad, 0.01)[1] < 0.001


def test_christoffersen_detects_clustered_hits():
    hits = np.zeros(5000, dtype=bool)
    for s in range(100, 5000, 500):
        hits[s:s + 5] = True  # same total rate as 1%, but bunched
    assert H.christoffersen(hits)[1] < 0.001


def test_coin_flips_pass_bernoulli_tests():
    r = series(np.random.default_rng(4).choice([-0.01, 0.01], 6000))
    out = H.h3_bernoulli(r)
    assert out["runs_p"] > 0.01 and out["markov_p"] > 0.01
    assert out["binom_p_vs_half"] > 0.01


def test_iid_events_pass_poisson_dispersion_and_garch_breaks_it():
    rng = np.random.default_rng(5)
    iid = series(rng.normal(0, 0.01, 8000))
    out_iid = H._count_test((iid.abs() > 0.025).astype(int))
    assert out_iid["dispersion_p"] > 0.01
    assert abs(out_iid["gap_cv"] - 1) < 0.25
    clustered = simulate_garch_t(8000, alpha=0.12, beta=0.86, nu=30, seed=6)
    sd = clustered.std()
    out_cl = H._count_test(((clustered - clustered.mean()).abs() > 2.5 * sd).astype(int))
    assert out_cl["dispersion"] > 1.5 and out_cl["dispersion_p"] < 0.001


def test_uniform_pit_passes_and_miscalibrated_fails():
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2000-01-03", periods=5000)
    good = pd.DataFrame({"pit": rng.random(5000)}, index=idx)
    bad = pd.DataFrame({"pit": stats.norm.cdf(rng.normal(0, 1.4, 5000))}, index=idx)
    out = H.h5_uniform({"good": good, "bad": bad}).set_index("model")
    assert out.loc["good", "ks_p"] > 0.01 and out.loc["good", "berkowitz_p"] > 0.01
    assert out.loc["bad", "ks_p"] < 0.001 and out.loc["bad", "berkowitz_p"] < 0.001
