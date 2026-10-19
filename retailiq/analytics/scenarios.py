"""Integrated what-if: marketing spend -> sales -> stock -> purchasing -> cash -> funding need.

Every link in the chain is an explicit, editable assumption (response elasticity, historical ROAS, share of sales that
would have happened anyway, stock buffer). The chain is NOT a causal model: it shows what follows IF those assumptions hold.

Chain
  1. Extra marketing spend S1 = S0 x (1 + change).
  2. Extra attributed revenue = baseline attributed revenue x ((S1/S0) ** elasticity - 1), where baseline attributed revenue
     = S0 x historical ROAS. The base cash forecast already contains the baseline campaign, so only the INCREMENT is added.
  3. Only (1 - baseline_share) of that extra revenue is treated as truly additional.
  4. Extra revenue is converted to units using the recent sales mix (target category or all products).
  5. Stock needed = extra units x (1 + buffer). Existing surplus stock (above the reorder policy) is used first; the rest
     must be purchased in case packs, within the supplier's maximum order size.
  6. Purchases are ordered one lead time before the campaign starts and paid after the supplier's terms.
  7. Extra collections follow the usual payment mix; marketing and variable selling costs are paid at month end.
  8. The cash difference is overlaid on the base 13-week forecast to show the new lowest cash and any funding need.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .accounting import safe_div
from .cashflow import _payment_mix
from .dataset import Dataset, between
from .finance import VARIABLE_COST_CATEGORIES
from .inventory import replenishment_plan, supplier_lead_times
from .marketing import variable_cost_ratio


def marketing_cash_scenario(ds: Dataset, base: Dict, campaign: Dict, spend_change_pct: float, roas: float, elasticity: float = 0.6,
                            baseline_share: float = 0.25, stock_buffer_pct: float = 0.10, reserve: float = 750_000.0,
                            cover_days: int = 40) -> Dict:
    """Overlay the scenario on a ``forecast_cash`` result.

    ``campaign`` needs: name, start_date, end_date, planned_budget, target_category (None = all), discount_pct.
    """
    problems = []
    if spend_change_pct <= -1:
        problems.append("A spend cut of 100% or more is not meaningful.")
    if not 0 <= elasticity <= 1.5:
        problems.append("Elasticity should be between 0 and 1.5.")
    if not 0 <= baseline_share < 1:
        problems.append("Share of sales that would have happened anyway must be between 0% and under 100%.")
    if campaign["planned_budget"] <= 0:
        problems.append("The campaign needs a positive baseline budget to scale from.")
    if roas < 0:
        problems.append("ROAS cannot be negative.")
    if problems:
        return {"valid": False, "problems": problems}

    daily = base["daily"].copy()
    dates = pd.DatetimeIndex(daily["date"])
    H = len(dates)
    ref = dates[0] - pd.Timedelta(days=1)
    pos = {d: i for i, d in enumerate(dates)}
    s0 = float(campaign["planned_budget"])
    s1 = s0 * (1 + spend_change_pct)
    extra_spend = s1 - s0
    rev0 = s0 * roas
    extra_attr_rev = rev0 * ((s1 / s0) ** elasticity - 1.0)
    extra_rev = extra_attr_rev * (1 - baseline_share)

    plan = replenishment_plan(ds, ref, demand_method="seasonal", cover_days=cover_days)
    cat = campaign.get("target_category")
    cat = None if (cat is None or pd.isna(cat) or cat == "") else cat
    win = ds.daily[between(ds.daily["date"], ref - pd.Timedelta(days=89), ref)]
    mix = win.groupby("product_id").agg(net=("net", "sum"), units=("units", "sum"))
    if cat:
        mix = mix[[ds.product_category.get(p) == cat for p in mix.index]]
    mix["share"] = mix["net"] / mix["net"].sum() if mix["net"].sum() > 0 else 0.0
    mix["price"] = mix["net"] / mix["units"].replace(0, np.nan)
    mix["extra_units"] = extra_rev * mix["share"] / mix["price"]
    mix["extra_units"] = mix["extra_units"].fillna(0.0)

    lts = supplier_lead_times(ds, ref).set_index("supplier_id")
    sup = ds.t["suppliers"].set_index("supplier_id")
    prod = ds.t["products"].set_index("product_id")
    start, end = pd.Timestamp(campaign["start_date"]), pd.Timestamp(campaign["end_date"])
    rows, purchase_flow = [], np.zeros(H + 400)
    for pid, r in mix.iterrows():
        pl = plan[plan["product_id"] == pid]
        position = float(pl["on_hand"].sum() + pl["on_order"].sum())
        surplus = float(max(0.0, (pl["on_hand"] + pl["on_order"] - pl["order_up_to"]).clip(lower=0).sum()))
        need = r["extra_units"] * (1 + stock_buffer_pct)
        buy_raw = max(0.0, need - surplus)
        cp, mx = int(prod.at[pid, "case_pack"]), int(prod.at[pid, "max_units_per_order"])
        qty = int(math.ceil(buy_raw / cp) * cp) if buy_raw > 0 else 0
        capped = qty > mx * len(ds.store_ids)
        qty = min(qty, (mx // cp) * cp * len(ds.store_ids))
        unit_cost = float(pl["unit_cost"].mean()) if len(pl) else float(prod.at[pid, "initial_unit_cost"])
        cost = qty * unit_cost
        sid = prod.at[pid, "supplier_id"]
        lt = int(round(lts.at[sid, "mean_lead_days"]))
        terms = int(sup.at[sid, "payment_terms_days"])
        order_day = max(start - pd.Timedelta(days=lt), ref + pd.Timedelta(days=1))
        pay_day = order_day + pd.Timedelta(days=lt + terms)
        if cost > 0 and pay_day in pos:
            purchase_flow[pos[pay_day]] -= cost
        rows.append({"product_id": pid, "product_name": ds.product_names.get(pid, pid), "extra_units_demand": r["extra_units"],
                     "stock_needed_with_buffer": need, "surplus_stock_used": min(surplus, need), "units_to_buy": qty,
                     "unit_cost": unit_cost, "purchase_cost": cost, "order_date": order_day, "payment_date": pay_day,
                     "capped_by_supplier_limit": capped})
    stock_table = pd.DataFrame(rows)
    purchase_cost = float(stock_table["purchase_cost"].sum()) if len(stock_table) else 0.0

    # extra collections: spread extra revenue evenly over the campaign days that fall inside the horizon
    pm = _payment_mix(ds, ref)
    camp_days = [d for d in pd.date_range(start, end, freq="D") if d in pos]
    coll = np.zeros(H + 120)
    refund_rate = base.get("refund_rate", 0.0)
    if camp_days:
        per_day = extra_rev / len(pd.date_range(start, end, freq="D"))
        for d in camp_days:
            t = pos[d]
            for method, lag in {"cash": 0, "upi": 1, "card": 2}.items():
                coll[t + lag] += per_day * pm["share"].get(method, 0.0)
            for lag, p in pm["credit_lags"].items():
                coll[t + max(lag, 1)] += per_day * pm["share"].get("credit", 0.0) * p
    collections = coll[:H] * (1 - refund_rate)

    ratio = variable_cost_ratio(ds, ref - pd.Timedelta(days=179), ref)
    marketing_flow = np.zeros(H)
    var_flow = np.zeros(H)
    n_camp = (end - start).days + 1
    for m in pd.period_range(start, end, freq="M"):
        ms, me = max(m.start_time, start), min(m.end_time.normalize(), end)
        share = ((me - ms).days + 1) / n_camp
        pay = m.end_time.normalize()
        if pay in pos:
            marketing_flow[pos[pay]] -= extra_spend * share
            var_flow[pos[pay]] -= extra_rev * share * ratio
    overlay = collections + purchase_flow[:H] + marketing_flow + var_flow
    new_close = daily["closing_cash"].to_numpy() + np.cumsum(overlay)
    low_i = int(np.argmin(new_close))
    cogs_extra = float((stock_table["extra_units_demand"] * stock_table["unit_cost"]).sum()) if len(stock_table) else 0.0
    gross_profit_extra = extra_rev * (1 - refund_rate) - cogs_extra
    contribution = gross_profit_extra - extra_spend - extra_rev * ratio
    out_daily = pd.DataFrame({"date": dates, "base_closing_cash": daily["closing_cash"].to_numpy(), "scenario_closing_cash": new_close,
                              "overlay_flow": overlay, "collections": collections, "purchases": purchase_flow[:H],
                              "marketing_and_variable_costs": marketing_flow + var_flow})
    chain = pd.DataFrame([
        ("1. Marketing spend", s0, s1, extra_spend, "Planned campaign budget plus the entered change"),
        ("2. Attributed revenue", rev0, rev0 + extra_attr_rev, extra_attr_rev, f"Baseline = budget x ROAS {roas:.2f}; response elasticity {elasticity:.2f}"),
        ("3. Truly additional revenue", 0.0, extra_rev, extra_rev, f"{(1 - baseline_share):.0%} of the extra attributed revenue (rest would have happened anyway)"),
        ("4. Extra units to sell", 0.0, float(stock_table["extra_units_demand"].sum()) if len(stock_table) else 0.0,
         float(stock_table["extra_units_demand"].sum()) if len(stock_table) else 0.0, "Revenue converted at recent average net prices"),
        ("5. Units to buy", 0.0, float(stock_table["units_to_buy"].sum()) if len(stock_table) else 0.0,
         float(stock_table["units_to_buy"].sum()) if len(stock_table) else 0.0, "After using surplus stock and adding the buffer"),
        ("6. Purchasing cost", 0.0, purchase_cost, purchase_cost, "Paid after supplier terms"),
    ], columns=["step", "baseline", "scenario", "change", "assumption"])
    return {
        "valid": True, "problems": [], "chain": chain, "stock": stock_table, "daily": out_daily, "extra_spend": extra_spend,
        "extra_attributed_revenue": extra_attr_rev, "extra_revenue": extra_rev, "purchase_cost": purchase_cost,
        "variable_costs": extra_rev * ratio, "gross_profit_extra": gross_profit_extra, "contribution_extra": contribution,
        "base_lowest_cash": float(daily["closing_cash"].min()), "scenario_lowest_cash": float(new_close[low_i]),
        "scenario_lowest_date": dates[low_i], "cash_dip_vs_base": float(daily["closing_cash"].min() - new_close[low_i]) if new_close[low_i] < daily["closing_cash"].min() else 0.0,
        "breach": bool(new_close.min() < reserve), "funding_need": float(max(reserve - new_close.min(), 0.0)), "reserve": reserve,
        "scenario_end_cash": float(new_close[-1]), "base_end_cash": float(daily["closing_cash"].iloc[-1]),
        "caveat": "Assumption-based: the relationships are inputs, not proven causal effects. Revenue beyond the horizon is not shown.",
    }
