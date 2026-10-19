"""Marketing performance analysis.

Attribution method (SIMULATED - read before interpreting)
--------------------------------------------------------
Each sales transaction carries at most ONE ``campaign_id``: the campaign whose code/offer the shopper redeemed
at the till or online. This is single-touch, redemption-based attribution. Because a transaction is attributed
to a single campaign, attributed revenue can never exceed total revenue and is never double counted across campaigns.

Attribution does NOT prove causation: some attributed shoppers would have bought anyway, and customers who saw an
advert but did not redeem a code are not counted. The ``baseline_share`` input lets you haircut attributed sales
with an explicit assumption about how many would have happened anyway.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from .accounting import safe_div
from .dataset import Dataset, between
from .finance import VARIABLE_COST_CATEGORIES


def variable_cost_ratio(ds: Dataset, start, end) -> float:
    """Variable selling costs (card fees, delivery, packaging) per rupee of net sales over the period."""
    ox = ds.t["operating_expenses"]
    var = ox[between(ox["expense_date"], start, end) & ox["category"].isin(VARIABLE_COST_CATEGORIES)]["amount"].sum()
    d = ds.daily[between(ds.daily["date"], start, end)]
    return float(safe_div(float(var), float(d["net"].sum()), 0.0))


def attributed_transactions(ds: Dataset, start=None, end=None) -> pd.DataFrame:
    """One row per attributed transaction with revenue, discount, COGS and refunds (grain: transaction)."""
    start = ds.start if start is None else start
    end = ds.end if end is None else end
    tx = ds.t["sales_transactions"]
    tx = tx[tx["campaign_id"].notna() & between(tx["transaction_date"], start, end)].copy()
    cogs = ds.lines.groupby("transaction_id")["cogs_amount"].sum()
    ret = ds.t["returns"].groupby("transaction_id").agg(refund=("refund_amount", "sum"), cogs_rev=("cogs_reversal", "sum"))
    tx["cogs"] = tx["transaction_id"].map(cogs).fillna(0.0)
    tx["refund"] = tx["transaction_id"].map(ret["refund"]).fillna(0.0)
    tx["cogs_rev"] = tx["transaction_id"].map(ret["cogs_rev"]).fillna(0.0)
    return tx


def campaign_performance(ds: Dataset, start=None, end=None, baseline_share: float = 0.0) -> pd.DataFrame:
    """Per-campaign funnel and profitability.

    Definitions
      CTR = clicks / impressions                         CPC = spend / clicks
      Conversion rate = attributed orders / clicks       (denominator: clicks - can exceed 100% for offline channels)
      CPA = spend / attributed orders                    CAC = spend / new customers whose first purchase was attributed
      ROAS = attributed revenue / spend                  (revenue after discounts, before returns)
      Contribution = attributed revenue - refunds - COGS(net of returns) - est. variable selling costs - media spend
      Adjusted contribution removes the assumed ``baseline_share`` of sales that would have happened anyway
      and charges the discounts given on them.
    """
    if not 0 <= baseline_share < 1:
        raise ValueError("baseline_share must be between 0 and 1 (exclusive of 1).")
    start = ds.start if start is None else pd.Timestamp(start)
    end = ds.end if end is None else pd.Timestamp(end)
    camps = ds.t["campaigns"]
    daily = ds.t["campaign_daily"]
    d = daily[between(daily["activity_date"], start, end)].groupby("campaign_id").agg(
        spend=("spend", "sum"), impressions=("impressions", "sum"), clicks=("clicks", "sum"))
    tx = attributed_transactions(ds, start, end)
    g = tx.groupby("campaign_id").agg(
        orders=("transaction_id", "nunique"), customers=("customer_id", "nunique"), gross_sales=("gross_amount", "sum"),
        discounts=("discount_amount", "sum"), attributed_revenue=("net_amount", "sum"), refunds=("refund", "sum"),
        cogs_sold=("cogs", "sum"), cogs_rev=("cogs_rev", "sum"))
    # new customers whose first-ever purchase was attributed to the campaign
    first = (ds.t["sales_transactions"].dropna(subset=["customer_id"]).sort_values(["transaction_date", "transaction_id"])
             .groupby("customer_id").head(1))
    cust = ds.t["customers"].set_index("customer_id")["registration_date"]
    first = first[first["campaign_id"].notna() & (first["customer_id"].map(cust) >= ds.start)]
    new_cust = first.groupby("campaign_id").size().rename("new_customers")
    out = camps.merge(d, on="campaign_id", how="left").merge(g, on="campaign_id", how="left").merge(new_cust, on="campaign_id", how="left")
    num = ["spend", "impressions", "clicks", "orders", "customers", "gross_sales", "discounts", "attributed_revenue", "refunds",
           "cogs_sold", "cogs_rev", "new_customers"]
    out[num] = out[num].fillna(0.0)
    out = out[(out["status"] != "planned") & (out["spend"] > 0)].copy()
    ratio = variable_cost_ratio(ds, start, end)
    out["cogs"] = out["cogs_sold"] - out["cogs_rev"]
    out["net_revenue_after_returns"] = out["attributed_revenue"] - out["refunds"]
    out["variable_costs_est"] = out["net_revenue_after_returns"] * ratio
    out["ctr"] = [safe_div(c, i) for c, i in zip(out["clicks"], out["impressions"])]
    out["cpc"] = [safe_div(s, c) for s, c in zip(out["spend"], out["clicks"])]
    out["conversion_rate"] = [safe_div(o, c) for o, c in zip(out["orders"], out["clicks"])]
    out["cpa"] = [safe_div(s, o) for s, o in zip(out["spend"], out["orders"])]
    out["cac"] = [safe_div(s, n) for s, n in zip(out["spend"], out["new_customers"])]
    out["roas"] = [safe_div(r, s) for r, s in zip(out["attributed_revenue"], out["spend"])]
    out["contribution"] = out["net_revenue_after_returns"] - out["cogs"] - out["variable_costs_est"] - out["spend"]
    out["contribution_before_media"] = out["contribution"] + out["spend"]
    q = baseline_share
    out["adjusted_contribution"] = ((1 - q) * (out["contribution_before_media"]) - q * out["discounts"] - out["spend"])
    out["contribution_per_rupee_spent"] = [safe_div(c, s) for c, s in zip(out["contribution"], out["spend"])]
    out["gross_margin_on_attributed"] = [safe_div(r - c, r) for r, c in zip(out["net_revenue_after_returns"], out["cogs"])]
    out["discount_rate"] = [safe_div(a, b) for a, b in zip(out["discounts"], out["gross_sales"])]
    out["conversion_rate_flag"] = np.where(out["conversion_rate"].fillna(0) > 1.0, "orders exceed clicks - redemptions include non-click paths", "")
    return out.sort_values("start_date").reset_index(drop=True)


def channel_summary(perf: pd.DataFrame) -> pd.DataFrame:
    """Aggregate campaign results by channel (ratios are recomputed from totals, never averaged)."""
    if perf.empty:
        return perf
    g = perf.groupby("channel", as_index=False)[["spend", "impressions", "clicks", "orders", "new_customers", "attributed_revenue",
                                                 "net_revenue_after_returns", "cogs", "variable_costs_est", "contribution",
                                                 "adjusted_contribution", "discounts"]].sum()
    g["campaigns"] = perf.groupby("channel").size().values
    g["ctr"] = [safe_div(c, i) for c, i in zip(g["clicks"], g["impressions"])]
    g["cpc"] = [safe_div(s, c) for s, c in zip(g["spend"], g["clicks"])]
    g["cpa"] = [safe_div(s, o) for s, o in zip(g["spend"], g["orders"])]
    g["roas"] = [safe_div(r, s) for r, s in zip(g["attributed_revenue"], g["spend"])]
    g["contribution_per_rupee_spent"] = [safe_div(c, s) for c, s in zip(g["contribution"], g["spend"])]
    return g.sort_values("contribution", ascending=False).reset_index(drop=True)


def attribution_check(ds: Dataset, perf: Optional[pd.DataFrame] = None) -> Dict:
    """Prove attribution does not duplicate revenue: attributed total <= company total and equals the transaction sum."""
    tx = ds.t["sales_transactions"]
    total = float(tx["net_amount"].sum())
    attributed_tx = float(tx.loc[tx["campaign_id"].notna(), "net_amount"].sum())
    perf = campaign_performance(ds) if perf is None else perf
    attributed_perf = float(perf["attributed_revenue"].sum())
    multi = tx.groupby("transaction_id")["campaign_id"].nunique().max()
    return {"total_revenue": total, "attributed_by_transactions": attributed_tx, "attributed_by_campaign_table": attributed_perf,
            "difference": round(attributed_tx - attributed_perf, 2), "attributed_share": safe_div(attributed_tx, total),
            "max_campaigns_per_transaction": int(multi), "within_total": attributed_tx <= total + 1e-6}


def marketing_budget_scenario(perf: pd.DataFrame, change_by_channel: Dict[str, float], elasticity: float = 0.6,
                              baseline_share: float = 0.0) -> pd.DataFrame:
    """Re-run channel economics under a different budget using an explicit diminishing-returns assumption.

    new_revenue = revenue x (new_spend / spend) ** elasticity       (0 < elasticity <= 1 means diminishing returns)
    Contribution before media is scaled with revenue; media spend is the new budget. This is an ASSUMPTION about
    how revenue responds to spend - not a measured relationship.
    """
    if not 0 <= elasticity <= 1.5:
        raise ValueError("Elasticity should be between 0 and 1.5 (1 = revenue grows in proportion to spend).")
    ch = channel_summary(perf)
    rows = []
    for r in ch.itertuples(index=False):
        chg = change_by_channel.get(r.channel, 0.0)
        if chg <= -1:
            raise ValueError("A budget cut of 100% or more is not meaningful.")
        new_spend = r.spend * (1 + chg)
        mult = (new_spend / r.spend) ** elasticity if r.spend > 0 else 1.0
        new_rev = r.attributed_revenue * mult
        before_media = r.contribution + r.spend
        new_before_media = before_media * mult
        q = baseline_share
        new_contrib = (1 - q) * new_before_media - q * r.discounts * mult - new_spend
        rows.append({
            "channel": r.channel, "current_spend": r.spend, "new_spend": new_spend, "current_attributed_revenue": r.attributed_revenue,
            "expected_attributed_revenue": new_rev, "revenue_change": new_rev - r.attributed_revenue,
            "current_contribution": r.contribution, "expected_contribution": new_contrib,
            "marginal_roas": safe_div(new_rev - r.attributed_revenue, new_spend - r.spend) if new_spend != r.spend else None,
            "new_roas": safe_div(new_rev, new_spend),
        })
    return pd.DataFrame(rows)
