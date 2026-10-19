"""Sales forecasting: baseline, model, chronological validation and leakage prevention."""
import numpy as np
import pandas as pd
import pytest

from retailiq.analytics import forecasting as FC


@pytest.fixture(scope="module")
def series(small_ds):
    wide = FC.units_matrix(small_ds, small_ds.start, small_ds.end)
    key = ("S01", "P09")
    y = wide[key].to_numpy().copy()
    dates = pd.date_range(small_ds.start, periods=len(y) + 30, freq="D")
    promo = FC.promo_flags(small_ds, key[0], key[1], dates)
    return y, dates, promo


def test_seasonal_naive_repeats_the_last_observed_week():
    y = np.arange(1, 15, dtype=float)            # last week = 8..14
    f = FC.seasonal_naive(y, 10)
    assert list(f[:7]) == list(range(8, 15)) and list(f[7:]) == [8, 9, 10]
    assert list(FC.seasonal_naive(np.array([4.0, 6.0]), 3)) == [5.0, 5.0, 5.0]   # too short for a week: mean
    assert list(FC.seasonal_naive(np.array([]), 2)) == [0.0, 0.0]


def test_features_use_only_strictly_earlier_days():
    y = np.arange(100, dtype=float)
    dates = pd.date_range("2026-01-01", periods=100)
    X, t = FC.training_matrix(y, dates, np.zeros(100))
    # first usable row is day 28; its lag_1 must be y[27], lag_28 = y[0], roll_mean_7 over y[21:28] - never y[28] itself
    assert t[0] == 28
    assert X[0, 0] == 27 and X[0, 3] == 0 and X[0, 4] == pytest.approx(y[21:28].mean()) and X[0, 5] == pytest.approx(y[0:28].mean())
    assert (X[:, 0] < t).all() and (X[:, 4] < t).all()


def test_forecast_does_not_change_when_future_actuals_change(series):
    y, dates, promo = series
    origin = len(y) - 45
    base, model, info = FC.forecast_series(y, dates, promo, origin, 30)
    tampered = y.copy()
    tampered[origin + 1:] = 10_000.0            # alter every value after the forecast origin
    base2, model2, _ = FC.forecast_series(tampered, dates, promo, origin, 30)
    assert info["status"] == "model" and info["train_rows"] > 100
    assert np.array_equal(base, base2) and np.array_equal(model, model2)


def test_multi_day_forecast_is_recursive_not_reading_actuals(series):
    y, dates, promo = series
    origin = len(y) - 40
    _, short, _ = FC.forecast_series(y, dates, promo, origin, 7)
    _, long_, _ = FC.forecast_series(y, dates, promo, origin, 30)
    assert np.allclose(short, long_[:7])        # day-by-day recursion: a longer horizon cannot change earlier days
    assert (long_ >= 0).all()


def test_changing_the_training_period_changes_the_model_but_not_the_future_data(series):
    y, dates, promo = series
    a = FC.forecast_series(y, dates, promo, len(y) - 60, 7)[1]
    b = FC.forecast_series(y, dates, promo, len(y) - 30, 7)[1]
    assert not np.allclose(a, b)


def test_insufficient_history_and_zero_sales_are_handled_safely(small_ds):
    dates = pd.date_range("2026-01-01", periods=140)
    promo = np.zeros(140)
    short = np.random.default_rng(1).poisson(3, 40).astype(float)
    base, model, info = FC.forecast_series(np.concatenate([short, np.zeros(100)]), dates, promo, 39, 7)
    assert "insufficient history" in info["status"] and np.array_equal(base, model)
    zeros = np.zeros(140)
    b, m, info = FC.forecast_series(zeros, dates, promo, 100, 7)
    assert info["status"].startswith("no sales") and (m == 0).all() and (b == 0).all()
    dead = np.concatenate([np.random.default_rng(2).poisson(4, 100), np.zeros(40)]).astype(float)
    b, m, info = FC.forecast_series(dead, dates, promo, 120, 10)     # sales stopped 20 days before the origin
    assert (m >= 0).all() and len(m) == 10


def test_chronological_folds_never_overlap_and_train_precedes_test():
    folds = FC.chronological_folds(n_days=420, horizon=30, n_folds=6, step=28, min_train=180)
    assert len(folds) == 6
    origins = [f[0] for f in folds]
    assert origins == sorted(origins) and len(set(origins)) == len(origins)
    for origin, a, b in folds:
        assert a == origin + 1 and b == origin + 30 and b <= 419 and origin >= 180
    assert FC.chronological_folds(n_days=100, horizon=30, n_folds=6, step=28, min_train=180) == []


def test_validation_reports_mae_and_wape_for_baseline_and_model(small_ds):
    v = FC.validate(small_ds, horizon=30, n_folds=3, step=28)
    assert v["valid"] and len(v["folds"]) == 3
    s = v["summary"].set_index("horizon_days")
    assert set(s.index) == {7, 30}
    for col in ["baseline_MAE_units_per_day", "model_MAE_units_per_day", "baseline_WAPE", "model_WAPE"]:
        assert np.isfinite(s[col]).all() and (s[col] >= 0).all()
    d = v["detail"]
    assert d["model_abs_err"].ge(0).all()
    for o, a, b in v["folds"]:
        assert o < a <= b
    h7 = d[d["horizon"] == 7]
    assert s.loc[7, "model_MAE_units_per_day"] == pytest.approx(h7["model_abs_err"].sum() / h7["days"].sum())
    assert s.loc[7, "model_WAPE"] == pytest.approx(h7["model_abs_err"].sum() / h7["actual_sum"].sum())


def test_validation_declines_when_history_is_too_short(small_ds):
    v = FC.validate(small_ds, end=small_ds.start + pd.Timedelta(days=100))
    assert not v["valid"]


def test_forecast_all_covers_every_series_without_negative_values(small_ds):
    fc = FC.forecast_all(small_ds, horizon=14)
    assert len(fc) == 2 * 20 * 14
    assert (fc["model_units"] >= 0).all() and (fc["baseline_units"] >= 0).all()
    assert fc["date"].min() == small_ds.ref_date + pd.Timedelta(days=1)
    s = FC.forecast_summary(small_ds, fc, (7, 14))
    assert set(s["horizon_days"]) == {7, 14}
    one = fc[(fc["store_id"] == "S01") & (fc["product_id"] == "P09")]
    assert s[(s["store_id"] == "S01") & (s["product_id"] == "P09") & (s["horizon_days"] == 7)]["model_units"].iloc[0] == pytest.approx(one[one["day_ahead"] <= 7]["model_units"].sum())


def test_model_forecast_is_deterministic(small_ds):
    a = FC.forecast_all(small_ds, 7)["model_units"].to_numpy()
    b = FC.forecast_all(small_ds, 7)["model_units"].to_numpy()
    assert np.array_equal(a, b)


def test_coefficients_are_available_for_inspection(small_ds):
    co = FC.coefficients(small_ds, "S01", "P09")
    assert set(co["feature"]) == set(FC.FEATURE_NAMES)
