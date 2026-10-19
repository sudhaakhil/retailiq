"""Customer behaviour analytics.

Only IDENTIFIED customers (loyalty / business accounts) can be tracked over time. Anonymous walk-in baskets
(customer_id is NULL) are excluded from repeat, RFM, cohort and lifetime-value figures, and the share of
revenue they represent is reported so the limitation is visible.

Key definitions
  * New customer: first purchase observed in the data AND registered on/after the dataset start.
    Customers registered before the start are "existing" - their earlier history is not in the data.
  * Repeat-purchase rate (window): customers with >= 2 orders in the window / customers with >= 1 order in the window.
  * Purchase frequency: orders per active customer in the window.      AOV: net sales / orders.
  * Inactive: days since last purchase >= ``inactive_days``; "becoming inactive": >= ``at_risk_days`` but below that.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from .accounting import safe_div
from .dataset import Dataset, between


def customer_orders(ds: Dataset) -> pd.DataFrame:
    """One row per identified transaction with gross profit after refunds (grain: transaction)."""
    tx = ds.t["sales_transactions"]
    tx = tx[tx["customer_id"].notna()].copy()
    cogs = ds.lines.groupby("transaction_id")["cogs_amount"].sum()
    ret = ds.t["returns"].groupby("transaction_id").agg(refund=("refund_amount", "sum"), cogs_rev=("cogs_reversal", "sum"))
    tx["cogs"] = tx["transaction_id"].map(cogs).fillna(0.0)
    tx["refund"] = tx["transaction_id"].map(ret["refund"]).fillna(0.0)
    tx["cogs_rev"] = tx["transaction_id"].map(ret["cogs_rev"]).fillna(0.0)
    tx["net_after_returns"] = tx["net_amount"] - tx["refund"]
    tx["gross_profit"] = tx["net_after_returns"] - (tx["cogs"] - tx["cogs_rev"])
    ct = ds.t["customers"].set_index("customer_id")
    tx["customer_type"] = tx["customer_id"].map(ct["customer_type"])
    return tx


def first_purchase_table(ds: Dataset) -> pd.DataFrame:
    orders = customer_orders(ds)
    f = orders.groupby("customer_id").agg(first_purchase=("transaction_date", "min"), last_purchase=("transaction_date", "max"),
                                          orders=("transaction_id", "nunique"), net_spend=("net_after_returns", "sum"),
                                          gross_profit=("gross_profit", "sum")).reset_index()
    c = ds.t["customers"][["customer_id", "customer_type", "home_store_id", "registration_date", "acquisition_channel",
                           "acquisition_campaign_id"]]
    f = f.merge(c, on="customer_id", how="left")
    f["is_new_customer"] = f["registration_date"] >= ds.start
    return f


def identified_share(ds: Dataset, start, end) -> float:
    tx = ds.t["sales_transactions"]
    w = tx[between(tx["transaction_date"], start, end)]
    return float(safe_div(w.loc[w["customer_id"].notna(), "net_amount"].sum(), w["net_amount"].sum(), 0.0))


def new_vs_returning(ds: Dataset, start=None, end=None, customer_type: Optional[str] = None) -> pd.DataFrame:
    """Monthly count of active customers split into new (first purchase that month) and returning."""
    start = ds.start if start is None else pd.Timestamp(start)
    end = ds.end if end is None else pd.Timestamp(end)
    orders = customer_orders(ds)
    if customer_type:
        orders = orders[orders["customer_type"] == customer_type]
    fp = first_purchase_table(ds).set_index("customer_id")
    orders = orders[between(orders["transaction_date"], start, end)].copy()
    orders["month"] = orders["transaction_date"].dt.to_period("M").dt.start_time
    first_month = fp["first_purchase"].dt.to_period("M").dt.start_time
    new_flag = fp["is_new_customer"]
    act = orders.groupby(["month", "customer_id"], as_index=False).agg(orders=("transaction_id", "nunique"), spend=("net_amount", "sum"))
    act["is_new"] = [bool(new_flag.get(c, False)) and first_month.get(c) == m for m, c in zip(act["month"], act["customer_id"])]
    g = act.groupby("month").agg(active_customers=("customer_id", "nunique"), new_customers=("is_new", "sum"), orders=("orders", "sum"),
                                 net_sales=("spend", "sum")).reset_index()
    g["returning_customers"] = g["active_customers"] - g["new_customers"]
    g["orders_per_customer"] = [safe_div(o, a) for o, a in zip(g["orders"], g["active_customers"])]
    g["avg_order_value"] = [safe_div(s, o) for s, o in zip(g["net_sales"], g["orders"])]
    return g


def repeat_metrics(ds: Dataset, end=None, window_days: int = 365) -> Dict:
    """Repeat-purchase rate, frequency and AOV for identified customers in the trailing window."""
    end = ds.ref_date if end is None else pd.Timestamp(end)
    start = max(end - pd.Timedelta(days=window_days - 1), ds.start)
    o = customer_orders(ds)
    w = o[between(o["transaction_date"], start, end)]
    per = w.groupby("customer_id")["transaction_id"].nunique()
    n_cust = int(len(per))
    repeaters = int((per >= 2).sum())
    return {
        "window_start": start, "window_end": end, "active_customers": n_cust, "repeat_customers": repeaters,
        "repeat_rate": safe_div(repeaters, n_cust), "orders": int(per.sum()), "purchase_frequency": safe_div(per.sum(), n_cust),
        "avg_order_value": safe_div(float(w["net_amount"].sum()), float(len(w))), "identified_revenue_share": identified_share(ds, start, end),
    }


# ---------------------------------------------------------------------------- RFM and inactivity
def _score(series: pd.Series, ascending: bool, bins: int = 5) -> pd.Series:
    """Rank-based quintile score 1..5 (5 = best). Robust to ties and small groups."""
    r = series.rank(method="first", ascending=ascending)
    n = len(series)
    if n == 0:
        return pd.Series(dtype=int)
    return (np.ceil(r / n * bins).clip(1, bins)).astype(int)


def rfm_table(ds: Dataset, end=None, window_days: int = 365, at_risk_days: int = 60, inactive_days: int = 120,
              customer_type: Optional[str] = "retail") -> pd.DataFrame:
    """Recency / Frequency / Monetary scores plus status for every identified customer.

    Recency = days since last purchase at ``end``; Frequency = orders in the window; Monetary = net spend after
    returns in the window. Scores are quintiles among customers who bought in the window (5 = best).
    """
    end = ds.ref_date if end is None else pd.Timestamp(end)
    start = max(end - pd.Timedelta(days=window_days - 1), ds.start)
    o = customer_orders(ds)
    if customer_type:
        o = o[o["customer_type"] == customer_type]
    o = o[o["transaction_date"] <= end]
    last = o.groupby("customer_id")["transaction_date"].max()
    w = o[between(o["transaction_date"], start, end)]
    agg = w.groupby("customer_id").agg(frequency=("transaction_id", "nunique"), monetary=("net_after_returns", "sum"),
                                       gross_profit=("gross_profit", "sum"))
    t = pd.DataFrame({"last_purchase": last})
    t["recency_days"] = (end - t["last_purchase"]).dt.days
    t = t.join(agg).fillna({"frequency": 0, "monetary": 0.0, "gross_profit": 0.0})
    t["frequency"] = t["frequency"].astype(int)
    active = t[t["frequency"] > 0]
    t["R"] = _score(active["recency_days"], ascending=False).reindex(t.index)
    t["F"] = _score(active["frequency"], ascending=True).reindex(t.index)
    t["M"] = _score(active["monetary"], ascending=True).reindex(t.index)

    def segment(r):
        if r.frequency == 0:
            return "Lapsed (no purchase in window)"
        if r.R >= 4 and r.F >= 4:
            return "Champions"
        if r.R >= 3 and r.F >= 3:
            return "Loyal"
        if r.R >= 4 and r.F <= 2:
            return "New or promising"
        if r.R <= 2 and r.F >= 4:
            return "At risk (were frequent)"
        if r.R <= 2:
            return "Hibernating"
        return "Needs attention"

    t["segment"] = [segment(r) for r in t.itertuples()]
    t["status"] = np.where(t["recency_days"] >= inactive_days, "Inactive",
                           np.where(t["recency_days"] >= at_risk_days, "Becoming inactive", "Active"))
    names = ds.t["customers"].set_index("customer_id")["customer_name"]
    t["customer_name"] = t.index.map(names)
    return t.reset_index().sort_values("monetary", ascending=False).reset_index(drop=True)


def inactivity_summary(rfm: pd.DataFrame) -> pd.DataFrame:
    g = rfm.groupby("status").agg(customers=("customer_id", "count"), spend_in_window=("monetary", "sum"),
                                  avg_recency_days=("recency_days", "mean")).reset_index()
    return g


# ---------------------------------------------------------------------------- cohorts
def cohort_retention(ds: Dataset, customer_type: Optional[str] = "retail") -> pd.DataFrame:
    """Share of each monthly acquisition cohort that purchased again k months later.

    Cohort = month of first purchase for customers registered on/after the dataset start. A cell is blank when the
    cohort has not yet been observed for that many months (no future data is assumed).
    """
    fp = first_purchase_table(ds)
    fp = fp[fp["is_new_customer"]]
    if customer_type:
        fp = fp[fp["customer_type"] == customer_type]
    o = customer_orders(ds)
    o = o[o["customer_id"].isin(fp["customer_id"])].copy()
    o["pm"] = o["transaction_date"].dt.to_period("M")
    cohort = fp.set_index("customer_id")["first_purchase"].dt.to_period("M")
    o["cohort"] = o["customer_id"].map(cohort)
    o["k"] = (o["pm"] - o["cohort"]).map(lambda x: x.n)
    active = o.groupby(["cohort", "k"])["customer_id"].nunique().unstack(fill_value=0)
    size = fp.assign(c=fp["first_purchase"].dt.to_period("M")).groupby("c")["customer_id"].nunique()
    last_period = ds.end.to_period("M")
    ret = active.div(size.reindex(active.index), axis=0)
    for c in ret.index:
        max_k = (last_period - c).n
        ret.loc[c, [k for k in ret.columns if k > max_k]] = np.nan
    ret.insert(0, "cohort_size", size.reindex(ret.index).astype(int))
    return ret


# ---------------------------------------------------------------------------- lifetime value
def lifetime_value(ds: Dataset, end=None, max_years: float = 5.0) -> Dict:
    """Simple customer lifetime value with the method and its limits stated.

    Method (retail loyalty customers, last 12 months):
      margin_per_year = orders per active customer per year x average order value x gross margin on orders
      annual retention = of customers active 13-24 months ago, the share who bought again in the last 12 months
      expected lifetime (years) = 1 / (1 - retention), capped at ``max_years``
      CLV = margin_per_year x expected lifetime            (undiscounted)
    Limitations: needs 24 months of data; assumes constant retention and margin; ignores acquisition cost and
    discounting; the data is simulated, so the number illustrates the method rather than real customer value.
    """
    end = ds.ref_date if end is None else pd.Timestamp(end)
    o = customer_orders(ds)
    o = o[o["customer_type"] == "retail"]
    cur_start = end - pd.Timedelta(days=364)
    prev_start, prev_end = end - pd.Timedelta(days=729), end - pd.Timedelta(days=365)
    out: Dict = {"window": (cur_start, end), "method": lifetime_value.__doc__}
    if prev_start < ds.start:
        out.update({"valid": False, "reason": "Fewer than 24 months of history before the reference date, so annual retention cannot be measured."})
        return out
    cur = o[between(o["transaction_date"], cur_start, end)]
    prev = o[between(o["transaction_date"], prev_start, prev_end)]
    base_cust = set(prev["customer_id"])
    retained = base_cust & set(cur["customer_id"])
    retention = safe_div(len(retained), len(base_cust))
    n_active = cur["customer_id"].nunique()
    if n_active == 0 or retention is None:
        out.update({"valid": False, "reason": "No active customers in one of the two years."})
        return out
    freq = len(cur) / n_active
    aov = float(cur["net_after_returns"].sum()) / len(cur)
    gm = safe_div(float(cur["gross_profit"].sum()), float(cur["net_after_returns"].sum()), 0.0)
    margin_year = freq * aov * gm
    churn = 1 - retention
    lifetime = max_years if churn <= 0 else min(1 / churn, max_years)
    out.update({"valid": True, "orders_per_year": freq, "avg_order_value": aov, "gross_margin": gm, "margin_per_year": margin_year,
                "annual_retention": retention, "annual_churn": churn, "expected_lifetime_years": lifetime, "clv": margin_year * lifetime,
                "capped": churn <= 0 or 1 / churn > max_years, "customers_in_retention_base": len(base_cust),
                "active_customers": n_active})
    return out
