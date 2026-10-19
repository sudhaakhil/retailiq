"""Product x store demand forecasting (units per day) for 7- and 30-day horizons.

Models
  * Seasonal-naive baseline: each future day repeats the same weekday of the last observed week.
  * Ridge regression (scikit-learn): a regularised linear model on lagged demand, rolling averages, weekday,
    month seasonality and a planned-promotion flag. Linear models are easy to inspect (see ``coefficients``).

Leakage prevention
  * Every feature for day t is computed from values up to day t-1 only (lags and ``shift(1)`` rolling means).
  * Multi-day forecasts are RECURSIVE: day t+2 uses the model's own forecast for day t+1, never the actual value.
  * ``forecast_series`` only ever reads ``y[:origin+1]``; values after the origin cannot influence the result.
  * Validation is chronological (rolling origin): train strictly before the origin, test strictly after it.
  * The promotion flag comes from the campaign CALENDAR (known in advance), not from observed sales.

Metrics: MAE (units per day) and WAPE = sum|error| / sum(actual). WAPE is preferred to MAPE because many
product-days have zero sales, which makes percentage errors undefined.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from .accounting import safe_div
from .dataset import Dataset, between

LAGS = (1, 7, 14, 28)
MIN_HISTORY = 84        # days needed before the model is trusted; below this the baseline is used
MIN_TRAIN_ROWS = 56
FEATURE_NAMES = ["lag_1", "lag_7", "lag_14", "lag_28", "roll_mean_7", "roll_mean_28"] + [f"dow_{i}" for i in range(1, 7)] + \
                ["month_sin", "month_cos", "promo"]


# ---------------------------------------------------------------------------- data preparation
def units_matrix(ds: Dataset, start, end) -> pd.DataFrame:
    """Gross units sold per day (rows) for every store x product (columns), gaps filled with zero."""
    d = ds.daily[between(ds.daily["date"], start, end)]
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    wide = d.pivot_table(index="date", columns=["store_id", "product_id"], values="units", aggfunc="sum").reindex(idx)
    for s in ds.store_ids:
        for p in ds.t["products"]["product_id"]:
            if (s, p) not in wide.columns:
                wide[(s, p)] = np.nan
    return wide.fillna(0.0)


def promo_flags(ds: Dataset, store_id: str, product_id: str, dates: pd.DatetimeIndex) -> np.ndarray:
    """1 on days when a campaign (incl. planned ones) covering this store and the product's category is running."""
    cat = ds.product_category[product_id]
    flag = np.zeros(len(dates))
    for c in ds.t["campaigns"].itertuples(index=False):
        if c.store_id is not None and not pd.isna(c.store_id) and c.store_id != store_id:
            continue
        if c.target_category is not None and not pd.isna(c.target_category) and c.target_category != cat:
            continue
        flag[(dates >= c.start_date) & (dates <= c.end_date)] = 1.0
    return flag


def _calendar_features(dates: pd.DatetimeIndex) -> np.ndarray:
    dow = np.zeros((len(dates), 6))
    for i in range(1, 7):
        dow[:, i - 1] = (dates.dayofweek == i).astype(float)
    m = dates.month.to_numpy()
    return np.column_stack([dow, np.sin(2 * np.pi * m / 12), np.cos(2 * np.pi * m / 12)])


def training_matrix(y: np.ndarray, dates: pd.DatetimeIndex, promo: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Feature matrix and target for every day t >= 28. Features use only y[:t] (strictly earlier days)."""
    s = pd.Series(y)
    cols = [s.shift(l) for l in LAGS]
    cols += [s.shift(1).rolling(7).mean(), s.shift(1).rolling(28).mean()]
    lagged = np.column_stack([c.to_numpy() for c in cols])
    X = np.column_stack([lagged, _calendar_features(dates), promo])
    mask = ~np.isnan(lagged).any(axis=1)
    return X[mask], y[mask]


def _row_features(hist: Sequence[float], date: pd.Timestamp, promo: float) -> np.ndarray:
    h = np.asarray(hist, dtype=float)
    n = len(h)
    lag = [h[n - l] for l in LAGS]
    roll = [h[n - 7:].mean(), h[n - 28:].mean()]
    cal = _calendar_features(pd.DatetimeIndex([date]))[0]
    return np.concatenate([lag, roll, cal, [promo]])


# ---------------------------------------------------------------------------- forecasts
def seasonal_naive(y_hist: np.ndarray, horizon: int) -> np.ndarray:
    """Repeat the last observed week: day origin+h takes the value of the same weekday in the final 7 days."""
    if len(y_hist) < 7:
        return np.full(horizon, float(np.mean(y_hist)) if len(y_hist) else 0.0)
    last_week = np.asarray(y_hist[-7:], dtype=float)
    return np.array([last_week[(h - 1) % 7] for h in range(1, horizon + 1)])


def forecast_series(y: np.ndarray, dates: pd.DatetimeIndex, promo_all: np.ndarray, origin: int, horizon: int,
                    return_model: bool = False):
    """Forecast ``horizon`` days after index ``origin`` using ONLY y[:origin+1].

    ``dates`` and ``promo_all`` must cover origin+horizon (the calendar and promotion plan are known in advance).
    Returns (baseline, model, info). When history is too short the model forecast falls back to the baseline.
    """
    hist = np.asarray(y[: origin + 1], dtype=float).copy()   # <- the only place actual demand is read
    base = seasonal_naive(hist, horizon)
    info = {"status": "model", "train_rows": 0}
    if len(hist) < MIN_HISTORY:
        info["status"] = "insufficient history - baseline used"
        return (base, base.copy(), info) if not return_model else (base, base.copy(), info, None)
    if hist[-28:].sum() == 0:
        info["status"] = "no sales in last 28 days - forecast is zero"
        zeros = np.zeros(horizon)
        return (zeros, zeros.copy(), info) if not return_model else (zeros, zeros.copy(), info, None)
    X, t = training_matrix(hist, dates[: origin + 1], promo_all[: origin + 1])
    if len(t) < MIN_TRAIN_ROWS:
        info["status"] = "too few training rows - baseline used"
        return (base, base.copy(), info) if not return_model else (base, base.copy(), info, None)
    scaler = StandardScaler().fit(X)
    model = Ridge(alpha=5.0).fit(scaler.transform(X), t)
    info["train_rows"] = int(len(t))
    series = list(hist)
    preds = np.zeros(horizon)
    for h in range(horizon):
        day = origin + 1 + h
        x = _row_features(series, dates[day], promo_all[day])
        p = float(model.predict(scaler.transform(x.reshape(1, -1)))[0])
        p = max(p, 0.0)
        preds[h] = p
        series.append(p)                                   # recursive: later days see forecasts, never actuals
    if return_model:
        return base, preds, info, (model, scaler)
    return base, preds, info


def chronological_folds(n_days: int, horizon: int, n_folds: int, step: int, min_train: int = 180) -> List[Tuple[int, int, int]]:
    """Rolling-origin splits as (origin_index, test_start, test_end). Train is everything <= origin; test is strictly after."""
    folds = []
    last_origin = n_days - 1 - horizon
    for k in range(n_folds):
        origin = last_origin - (n_folds - 1 - k) * step
        if origin >= min_train:
            folds.append((origin, origin + 1, origin + horizon))
    return folds


def validate(ds: Dataset, horizon: int = 30, n_folds: int = 6, step: int = 28, end=None, min_train: int = 180,
             report_horizons: Tuple[int, ...] = (7, 30)) -> Dict:
    """Rolling-origin comparison of the baseline and the Ridge model for every store x product series."""
    end = ds.ref_date if end is None else min(pd.Timestamp(end), ds.end)
    wide = units_matrix(ds, ds.start, end)
    dates = wide.index
    n = len(dates)
    folds = chronological_folds(n, horizon, n_folds, step, min_train)
    if not folds:
        return {"valid": False, "reason": "Not enough history for chronological validation.", "folds": []}
    ext_dates = pd.date_range(ds.start, periods=n + horizon, freq="D")
    rows = []
    for (s, p) in wide.columns:
        y = wide[(s, p)].to_numpy()
        promo = promo_flags(ds, s, p, ext_dates)
        for fi, (origin, a, b) in enumerate(folds):
            base, mod, info = forecast_series(y, ext_dates, promo, origin, horizon)
            actual = y[a: b + 1]
            for hz in report_horizons:
                if hz > horizon:
                    continue
                act = actual[:hz]
                rows.append({"store_id": s, "product_id": p, "fold": fi + 1, "origin": dates[origin], "horizon": hz,
                             "actual_total": float(act.sum()), "baseline_total": float(base[:hz].sum()), "model_total": float(mod[:hz].sum()),
                             "baseline_abs_err": float(np.abs(base[:hz] - act).sum()), "model_abs_err": float(np.abs(mod[:hz] - act).sum()),
                             "actual_sum": float(act.sum()), "days": hz, "status": info["status"]})
    res = pd.DataFrame(rows)
    summary = []
    for hz, g in res.groupby("horizon"):
        days_total = g["days"].sum()
        tot_act = g["actual_sum"].sum()
        summary.append({
            "horizon_days": hz, "series_x_folds": len(g),
            "baseline_MAE_units_per_day": g["baseline_abs_err"].sum() / days_total, "model_MAE_units_per_day": g["model_abs_err"].sum() / days_total,
            "baseline_WAPE": safe_div(g["baseline_abs_err"].sum(), tot_act), "model_WAPE": safe_div(g["model_abs_err"].sum(), tot_act),
            "baseline_total_demand_WAPE": safe_div((g["baseline_total"] - g["actual_total"]).abs().sum(), tot_act),
            "model_total_demand_WAPE": safe_div((g["model_total"] - g["actual_total"]).abs().sum(), tot_act),
            "model_bias_pct": safe_div((g["model_total"] - g["actual_total"]).sum(), tot_act),
            "baseline_bias_pct": safe_div((g["baseline_total"] - g["actual_total"]).sum(), tot_act),
        })
    summary = pd.DataFrame(summary)
    summary["model_beats_baseline_on_MAE"] = summary["model_MAE_units_per_day"] < summary["baseline_MAE_units_per_day"]
    by_series = res[res["horizon"] == max(report_horizons)].groupby(["store_id", "product_id"], as_index=False).agg(
        baseline_abs_err=("baseline_abs_err", "sum"), model_abs_err=("model_abs_err", "sum"), actual=("actual_sum", "sum"))
    by_series["baseline_WAPE"] = [safe_div(a, b) for a, b in zip(by_series["baseline_abs_err"], by_series["actual"])]
    by_series["model_WAPE"] = [safe_div(a, b) for a, b in zip(by_series["model_abs_err"], by_series["actual"])]
    by_series["product_name"] = by_series["product_id"].map(ds.product_names)
    return {"valid": True, "folds": [(dates[o], dates[a], dates[b]) for o, a, b in folds], "detail": res, "summary": summary,
            "by_series": by_series, "n_series": wide.shape[1]}


def forecast_all(ds: Dataset, horizon: int = 30, origin_date=None) -> pd.DataFrame:
    """Daily forecasts (baseline and model) for every store x product after ``origin_date`` (default: reference date)."""
    origin_date = ds.ref_date if origin_date is None else min(pd.Timestamp(origin_date), ds.end)
    wide = units_matrix(ds, ds.start, origin_date)
    n = len(wide)
    ext_dates = pd.date_range(ds.start, periods=n + horizon, freq="D")
    rows = []
    for (s, p) in wide.columns:
        y = wide[(s, p)].to_numpy()
        promo = promo_flags(ds, s, p, ext_dates)
        base, mod, info = forecast_series(y, ext_dates, promo, n - 1, horizon)
        for h in range(horizon):
            rows.append({"store_id": s, "product_id": p, "date": ext_dates[n + h], "day_ahead": h + 1, "baseline_units": base[h],
                         "model_units": mod[h], "promotion_planned": bool(promo[n + h]), "status": info["status"]})
    out = pd.DataFrame(rows)
    out["product_name"] = out["product_id"].map(ds.product_names)
    return out


def forecast_summary(ds: Dataset, fc: pd.DataFrame, horizons: Tuple[int, ...] = (7, 30)) -> pd.DataFrame:
    """Total forecast units and estimated net sales per store x product for each horizon."""
    price_win = ds.daily[between(ds.daily["date"], ds.ref_date - pd.Timedelta(days=89), ds.ref_date)].groupby(["store_id", "product_id"]).agg(
        net=("net", "sum"), units=("units", "sum"))
    out = []
    for hz in horizons:
        g = fc[fc["day_ahead"] <= hz].groupby(["store_id", "product_id"], as_index=False).agg(
            baseline_units=("baseline_units", "sum"), model_units=("model_units", "sum"), status=("status", "first"))
        g["horizon_days"] = hz
        out.append(g)
    res = pd.concat(out, ignore_index=True)
    price = (price_win["net"] / price_win["units"].replace(0, np.nan)).rename("avg_net_price")
    res = res.merge(price.reset_index(), on=["store_id", "product_id"], how="left")
    res["avg_net_price"] = res["avg_net_price"].fillna(0.0)
    res["model_net_sales_est"] = res["model_units"] * res["avg_net_price"]
    res["product_name"] = res["product_id"].map(ds.product_names)
    return res


def coefficients(ds: Dataset, store_id: str, product_id: str, origin_date=None) -> pd.DataFrame:
    """Standardised Ridge coefficients for one series (positive = raises forecast demand)."""
    origin_date = ds.ref_date if origin_date is None else min(pd.Timestamp(origin_date), ds.end)
    wide = units_matrix(ds, ds.start, origin_date)
    y = wide[(store_id, product_id)].to_numpy()
    n = len(y)
    ext = pd.date_range(ds.start, periods=n + 7, freq="D")
    promo = promo_flags(ds, store_id, product_id, ext)
    out = forecast_series(y, ext, promo, n - 1, 7, return_model=True)
    model = out[3]
    if model is None:
        return pd.DataFrame(columns=["feature", "coefficient"])
    return pd.DataFrame({"feature": FEATURE_NAMES, "coefficient": model[0].coef_}).sort_values("coefficient", key=np.abs, ascending=False)
