"""Cached wrappers around the heavier analytics.

Rule: cache the expensive computation on the shared, read-only ``Dataset`` and apply cheap filters afterwards.
The ``_ds`` argument is deliberately not hashed; ``key`` (database signature + reference date) is what invalidates the cache.
"""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd
import streamlit as st

from ..analytics import actions as AC
from ..analytics import cashflow as CF
from ..analytics import customers as CU
from ..analytics import forecasting as FC
from ..analytics import inventory as INV
from ..analytics import marketing as MK
from ..analytics import reviews as RV
from ..analytics.dataset import Dataset

TTL = 3600


@st.cache_data(ttl=TTL, max_entries=24, show_spinner=False)
def replenishment(_ds: Dataset, key: str, window_days: int, z: float, cover_days: int, demand_method: str, slow_days: int, fast_days: int,
                  overstock_days: int) -> pd.DataFrame:
    return INV.replenishment_plan(_ds, None, window_days, z, cover_days, demand_method, slow_days, overstock_days=overstock_days,
                                  fast_days=fast_days)


@st.cache_data(ttl=TTL, max_entries=24, show_spinner=False)
def cash_forecast(_ds: Dataset, key: str, sales_adjustment: float, collection_delay_days: int, supplier_delay_days: int, cover_days: int,
                  min_cash_reserve: float, marketing_uplift_pct: float, inventory_release_pct: float, planned_records: tuple) -> Dict:
    planned = pd.DataFrame(list(planned_records), columns=["planned_date", "category", "description", "amount", "activity"]) if planned_records else None
    if planned is not None:
        planned["planned_date"] = pd.to_datetime(planned["planned_date"])
    a = CF.CashAssumptions(sales_adjustment=sales_adjustment, collection_delay_days=collection_delay_days, supplier_delay_days=supplier_delay_days,
                           cover_days=cover_days, min_cash_reserve=min_cash_reserve, marketing_uplift_pct=marketing_uplift_pct,
                           inventory_release_pct=inventory_release_pct, planned_items=planned)
    return CF.forecast_cash(_ds, a)


@st.cache_data(ttl=TTL, max_entries=4, show_spinner="Running chronological validation (about 10 seconds) ...")
def forecast_validation(_ds: Dataset, key: str, horizon: int, n_folds: int) -> Dict:
    return FC.validate(_ds, horizon=horizon, n_folds=n_folds)


@st.cache_data(ttl=TTL, max_entries=4, show_spinner="Forecasting every product and store ...")
def forecasts(_ds: Dataset, key: str, horizon: int) -> pd.DataFrame:
    return FC.forecast_all(_ds, horizon)


@st.cache_data(ttl=TTL, max_entries=8, show_spinner=False)
def campaigns(_ds: Dataset, key: str, start: pd.Timestamp, end: pd.Timestamp, baseline_share: float) -> pd.DataFrame:
    return MK.campaign_performance(_ds, start, end, baseline_share)


@st.cache_data(ttl=TTL, max_entries=4, show_spinner=False)
def reviews(_ds: Dataset, key: str) -> pd.DataFrame:
    return RV.analysed_reviews(_ds)


@st.cache_data(ttl=TTL, max_entries=8, show_spinner=False)
def rfm(_ds: Dataset, key: str, window_days: int, at_risk_days: int, inactive_days: int, customer_type: Optional[str]) -> pd.DataFrame:
    return CU.rfm_table(_ds, None, window_days, at_risk_days, inactive_days, customer_type)


@st.cache_data(ttl=TTL, max_entries=4, show_spinner=False)
def cohorts(_ds: Dataset, key: str, customer_type: Optional[str]) -> pd.DataFrame:
    return CU.cohort_retention(_ds, customer_type)


@st.cache_data(ttl=TTL, max_entries=4, show_spinner="Evaluating business rules ...")
def action_findings(_ds: Dataset, key: str, settings: AC.Settings):
    return AC.run_all(_ds, settings)
