"""Inventory valuation, stock health and replenishment planning.

Method summary (see docs/metrics_and_assumptions.md)
----------------------------------------------------
* Valuation: weighted-average cost per store x product. A receipt re-averages the cost; a sale
  removes stock at the current average; a return restores stock at the cost it left with.
* Demand per day: trailing-window average of units sold less units returned (``trailing``), or the
  same dates one year earlier scaled by recent year-on-year growth (``seasonal``).
* Safety stock = z * sqrt(LT * sigma_d^2 + d^2 * sigma_LT^2), where LT is the supplier's observed
  mean lead time, sigma_d the daily demand deviation, sigma_LT the lead-time deviation.
* Reorder point (ROP) = d * LT + safety stock.
* Order-up-to level = d * (LT + cover days) + safety stock.
* Suggested quantity = max(0, order-up-to - (on hand + on order)), rounded UP to the supplier case pack
  and capped at the supplier's maximum order size. It is never negative.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .accounting import safe_div
from .dataset import Dataset, between

DEFAULT_Z = 1.65


# ---------------------------------------------------------------------------- positions & valuation
def stock_position(ds: Dataset, date=None) -> pd.DataFrame:
    """Quantity, value and open orders per store x product at ``date`` (default: reference date)."""
    date = pd.Timestamp(date) if date is not None else ds.ref_date
    inv = ds.t["inventory_daily"]
    pos = inv[inv["balance_date"] == min(max(date, ds.start), ds.end)].copy()
    pos["product_name"] = pos["product_id"].map(ds.product_names)
    pos["category"] = pos["product_id"].map(ds.product_category)
    return pos.reset_index(drop=True)


def inventory_value_trend(ds: Dataset) -> pd.DataFrame:
    inv = ds.t["inventory_daily"]
    g = inv.groupby(["balance_date", "store_id"], as_index=False)["inventory_value"].sum()
    return g


def stock_movement_history(ds: Dataset, start=None, end=None, stores=None, products=None, types=None) -> pd.DataFrame:
    mv = ds.t["stock_movements"]
    m = pd.Series(True, index=mv.index)
    if start is not None and end is not None:
        m &= between(mv["movement_date"], start, end)
    if stores:
        m &= mv["store_id"].isin(list(stores))
    if products:
        m &= mv["product_id"].isin(list(products))
    if types:
        m &= mv["movement_type"].isin(list(types))
    out = mv[m].copy()
    out["product_name"] = out["product_id"].map(ds.product_names)
    return out


def reconcile_movements(ds: Dataset, date=None) -> pd.DataFrame:
    """Opening + movements up to ``date`` versus the stored balance. ``qty_diff``/``value_diff`` should be 0."""
    date = pd.Timestamp(date) if date is not None else ds.ref_date
    mv = ds.t["stock_movements"]
    mv = mv[mv["movement_date"] <= date]
    calc = mv.groupby(["store_id", "product_id"], as_index=False).agg(qty_calc=("quantity", "sum"), value_calc=("value", "sum"))
    bal = stock_position(ds, date)[["store_id", "product_id", "quantity_on_hand", "inventory_value"]]
    out = bal.merge(calc, on=["store_id", "product_id"], how="outer").fillna(0.0)
    out["qty_diff"] = out["quantity_on_hand"] - out["qty_calc"]
    out["value_diff"] = (out["inventory_value"] - out["value_calc"]).round(2)
    return out


# ---------------------------------------------------------------------------- demand & lead time
def daily_units_matrix(ds: Dataset, start, end) -> pd.DataFrame:
    """Net units (sold - returned) per day; rows = days, columns = (store_id, product_id); gaps filled with 0."""
    x = ds.daily[between(ds.daily["date"], start, end)].copy()
    x["net_units"] = x["units"] - x["ret_units"]
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    wide = x.pivot_table(index="date", columns=["store_id", "product_id"], values="net_units", aggfunc="sum").reindex(idx).fillna(0.0)
    return wide


def supplier_lead_times(ds: Dataset, as_of=None, lookback_days: int = 365) -> pd.DataFrame:
    """Observed lead time (order date -> received date) per supplier, with the quoted time for comparison."""
    as_of = pd.Timestamp(as_of) if as_of is not None else ds.ref_date
    po = ds.t["purchase_orders"]
    done = po[(po["status"] == "received") & (po["received_date"] <= as_of) & (po["received_date"] >= as_of - pd.Timedelta(days=lookback_days))].copy()
    done["lead_days"] = (done["received_date"] - done["order_date"]).dt.days
    done["late_days"] = (done["received_date"] - done["expected_date"]).dt.days
    g = done.groupby("supplier_id", as_index=False).agg(
        orders=("po_id", "count"), mean_lead_days=("lead_days", "mean"), std_lead_days=("lead_days", "std"),
        on_time_share=("late_days", lambda s: float((s <= 0).mean())), avg_days_late_when_late=("late_days", lambda s: float(s[s > 0].mean()) if (s > 0).any() else 0.0))
    sup = ds.t["suppliers"][["supplier_id", "supplier_name", "lead_time_days", "payment_terms_days"]]
    out = sup.merge(g, on="supplier_id", how="left").rename(columns={"lead_time_days": "quoted_lead_days"})
    out["orders"] = out["orders"].fillna(0).astype(int)
    out["mean_lead_days"] = out["mean_lead_days"].fillna(out["quoted_lead_days"])
    out["std_lead_days"] = out["std_lead_days"].fillna(0.0)
    out["on_time_share"] = out["on_time_share"].fillna(np.nan)
    return out


def _latest_unit_cost(ds: Dataset, date: pd.Timestamp) -> pd.Series:
    pol = ds.t["purchase_order_lines"].merge(ds.t["purchase_orders"][["po_id", "order_date"]], on="po_id")
    pol = pol[pol["order_date"] <= date].sort_values("order_date")
    return pol.groupby("product_id")["unit_cost"].last()


# ---------------------------------------------------------------------------- replenishment plan
def replenishment_plan(ds: Dataset, date=None, window_days: int = 90, z: float = DEFAULT_Z, cover_days: int = 30,
                       demand_method: str = "trailing", slow_days: int = 120, fast_days: int = 30,
                       overstock_days: int = 120, stores=None) -> pd.DataFrame:
    """Stock health and suggested replenishment for every store x product at ``date``."""
    date = pd.Timestamp(date) if date is not None else ds.ref_date
    date = min(max(date, ds.start), ds.end)
    win_start = max(date - pd.Timedelta(days=window_days - 1), ds.start)
    n_days = (date - win_start).days + 1
    wide = daily_units_matrix(ds, win_start, date)
    pos = stock_position(ds, date)
    if stores:
        pos = pos[pos["store_id"].isin(list(stores))]
    prod = ds.t["products"].set_index("product_id")
    lts = supplier_lead_times(ds, date).set_index("supplier_id")
    cost_latest = _latest_unit_cost(ds, date)
    sales_win = ds.daily[between(ds.daily["date"], win_start, date)].groupby(["store_id", "product_id"]).agg(
        units=("units", "sum"), net=("net", "sum"), cogs=("cogs", "sum"))

    # year-on-year growth factor for seasonal demand
    yago_end = date - pd.Timedelta(days=364)
    can_seasonal = demand_method == "seasonal" and (yago_end - pd.Timedelta(days=window_days)) >= ds.start

    ly_full = ly_prev = None
    if can_seasonal:
        max_h = int(math.ceil(lts["mean_lead_days"].max() + cover_days)) + 1
        ly_full = daily_units_matrix(ds, date - pd.Timedelta(days=363), date + pd.Timedelta(days=max_h) - pd.Timedelta(days=364))
        ly_prev = daily_units_matrix(ds, win_start - pd.Timedelta(days=364), date - pd.Timedelta(days=364))

    rows = []
    for r in pos.itertuples(index=False):
        key = (r.store_id, r.product_id)
        series = wide[key] if key in wide.columns else pd.Series(np.zeros(n_days))
        d_trailing = float(series.mean()) if len(series) else 0.0
        sigma_d = float(series.std(ddof=0)) if len(series) > 1 else 0.0
        sup_id = prod.at[r.product_id, "supplier_id"]
        lt = float(lts.at[sup_id, "mean_lead_days"])
        sigma_lt = float(lts.at[sup_id, "std_lead_days"])
        d, method_used = d_trailing, "trailing average"
        if can_seasonal:
            horizon = int(math.ceil(lt + cover_days))
            ly = ly_full.iloc[:horizon]
            if key in ly.columns:
                ly_d = float(ly[key].mean())
                base_prev = float(ly_prev[key].mean()) if key in ly_prev.columns else 0.0
                growth = min(max(safe_div(d_trailing, base_prev, 1.0), 0.5), 2.0) if base_prev > 0 else 1.0
                if ly_d > 0:
                    d, method_used = ly_d * growth, "seasonal (last year x growth)"
        safety = z * math.sqrt(max(lt * sigma_d ** 2 + (d ** 2) * (sigma_lt ** 2), 0.0))
        rop = d * lt + safety
        order_up_to = d * (lt + cover_days) + safety
        on_hand, on_order = int(r.quantity_on_hand), int(r.quantity_on_order)
        position = on_hand + on_order
        case_pack = int(prod.at[r.product_id, "case_pack"])
        max_order = int(prod.at[r.product_id, "max_units_per_order"])
        raw = max(0.0, order_up_to - position)
        qty = int(math.ceil(raw / case_pack) * case_pack) if raw > 0 else 0
        capped = qty > max_order
        qty = min(qty, (max_order // case_pack) * case_pack)
        qty = max(qty, 0)
        unit_cost = float(cost_latest.get(r.product_id, r.avg_unit_cost if r.avg_unit_cost > 0 else prod.at[r.product_id, "initial_unit_cost"]))
        s = sales_win.loc[key] if key in sales_win.index else None
        net_price = safe_div(s["net"], s["units"]) if s is not None else None
        net_price = net_price if net_price is not None else float(prod.at[r.product_id, "unit_price"])
        unit_margin = net_price - unit_cost
        days_stock = safe_div(on_hand, d)
        days_incl_order = safe_div(position, d)
        ess_raw = max(0.0, rop - position)
        essential = min(int(math.ceil(ess_raw / case_pack) * case_pack) if ess_raw > 0 else 0, qty)
        if d <= 0:
            flag = "No recent sales" if on_hand > 0 else "No stock, no sales"
        elif on_hand == 0:
            flag = "Out of stock"
        elif position <= rop:
            flag = "Low stock - reorder"
        elif days_incl_order is not None and days_incl_order > overstock_days:
            flag = "Overstock"
        else:
            flag = "OK"
        if d <= 0:
            speed = "No sales"
        elif days_stock is not None and days_stock <= fast_days:
            speed = "Fast-moving"
        elif days_stock is not None and days_stock > slow_days:
            speed = "Slow-moving"
        else:
            speed = "Normal"
        shortfall_days = max(0.0, lt - (days_stock if days_stock is not None else 0.0)) if d > 0 else 0.0
        avg_cost_now = float(r.avg_unit_cost)
        rows.append({
            "store_id": r.store_id, "product_id": r.product_id, "product_name": r.product_name, "category": r.category,
            "supplier_id": sup_id, "on_hand": on_hand, "on_order": on_order, "inventory_value": float(r.inventory_value),
            "avg_unit_cost": avg_cost_now, "daily_demand": d, "demand_basis": method_used, "demand_std": sigma_d,
            "lead_time_days": lt, "lead_time_std": sigma_lt, "safety_stock": safety, "reorder_point": rop,
            "order_up_to": order_up_to, "days_of_stock": days_stock, "days_incl_on_order": days_incl_order,
            "status": flag, "movement": speed, "suggested_qty": qty, "essential_qty": essential, "capped_by_supplier_limit": capped,
            "case_pack": case_pack, "max_units_per_order": max_order, "unit_cost": unit_cost, "unit_margin": unit_margin,
            "est_purchase_cost": qty * unit_cost,
            "cover_after_order_days": safe_div(position + qty, d), "shortfall_days": shortfall_days,
            "units_sold_window": float(s["units"]) if s is not None else 0.0,
        })
    plan = pd.DataFrame(rows)
    if plan.empty:
        return plan
    plan["cogs_window"] = [float(sales_win.loc[(a, b), "cogs"]) if (a, b) in sales_win.index else 0.0 for a, b in zip(plan["store_id"], plan["product_id"])]
    avg_inv = ds.t["inventory_daily"]
    avg_inv = avg_inv[between(avg_inv["balance_date"], win_start, date)].groupby(["store_id", "product_id"])["inventory_value"].mean()
    plan["avg_inventory_window"] = [float(avg_inv.get((a, b), 0.0)) for a, b in zip(plan["store_id"], plan["product_id"])]
    plan["turnover_annualised"] = [safe_div(c * 365.0 / n_days, i) for c, i in zip(plan["cogs_window"], plan["avg_inventory_window"])]
    # GMROI = annualised gross profit (units sold x unit margin) per rupee of average inventory at cost.
    plan["gmroi"] = [safe_div(u * m * 365.0 / n_days, i) for u, m, i in
                     zip(plan["units_sold_window"], plan["unit_margin"], plan["avg_inventory_window"])]
    return plan


def plan_summary(plan: pd.DataFrame) -> Dict:
    if plan.empty:
        return {"inventory_value": 0.0, "slow_value": 0.0, "slow_share": None, "low_stock_lines": 0, "out_of_stock_lines": 0,
                "overstock_lines": 0, "suggested_cost": 0.0}
    total = float(plan["inventory_value"].sum())
    slow = float(plan.loc[plan["movement"].isin(["Slow-moving", "No sales"]), "inventory_value"].sum())
    return {
        "inventory_value": total, "slow_value": slow, "slow_share": safe_div(slow, total),
        "low_stock_lines": int((plan["status"] == "Low stock - reorder").sum()),
        "out_of_stock_lines": int((plan["status"] == "Out of stock").sum()),
        "overstock_lines": int((plan["status"] == "Overstock").sum()),
        "suggested_cost": float(plan["est_purchase_cost"].sum()),
    }


# ---------------------------------------------------------------------------- budget prioritisation
def prioritise_orders(plan: pd.DataFrame, budget: Optional[float]) -> pd.DataFrame:
    """Allocate a purchasing budget with a transparent two-phase rule.

    Phase 1 - ESSENTIAL units (bring each line back to its reorder point), ranked by
        profit_at_risk_per_rupee = daily_demand * unit_margin * shortfall_days / essential_cost,
        where shortfall_days = days of lead time not covered by stock on hand.
    Phase 2 - EXTRA cover units (up to the order-up-to level), ranked by GMROI
        (annual gross profit per rupee of average inventory) - i.e. where cash earns the most.
    Units are bought in whole case packs. A line may be funded partially. Nothing is negative.
    ``budget=None`` means unlimited (every suggested order is funded).
    """
    orders = plan[plan["suggested_qty"] > 0].copy()
    if orders.empty:
        return orders.assign(funded_qty=0, funded_cost=0.0, phase="", decision="")
    orders["essential_cost"] = orders["essential_qty"] * orders["unit_cost"]
    orders["extra_qty"] = orders["suggested_qty"] - orders["essential_qty"]
    orders["profit_at_risk"] = orders["daily_demand"] * orders["unit_margin"].clip(lower=0) * orders["shortfall_days"]
    orders["priority_phase1"] = [safe_div(p, c, 0.0) for p, c in zip(orders["profit_at_risk"], orders["essential_cost"])]
    orders["funded_qty"] = 0
    remaining = float("inf") if budget is None else float(max(budget, 0.0))

    def fund(idx, qty_wanted):
        nonlocal remaining
        row = orders.loc[idx]
        pack_cost = row["case_pack"] * row["unit_cost"]
        if qty_wanted <= 0 or pack_cost <= 0:
            return 0
        packs_wanted = int(qty_wanted // row["case_pack"])
        packs_afford = packs_wanted if math.isinf(remaining) else int(remaining // pack_cost)
        packs = max(0, min(packs_wanted, packs_afford))
        if packs:
            remaining -= packs * pack_cost
        return packs * int(row["case_pack"])

    order1 = orders.sort_values(["priority_phase1", "unit_margin"], ascending=[False, False]).index
    for idx in order1:
        orders.at[idx, "funded_qty"] += fund(idx, int(orders.at[idx, "essential_qty"]))
    gm = orders["gmroi"].fillna(0.0)
    order2 = gm.sort_values(ascending=False).index
    for idx in order2:
        already = int(orders.at[idx, "funded_qty"])
        if already < int(orders.at[idx, "essential_qty"]):
            continue  # essential not fully funded, so no extra cover for this line
        orders.at[idx, "funded_qty"] += fund(idx, int(orders.at[idx, "extra_qty"]))
    orders["funded_cost"] = orders["funded_qty"] * orders["unit_cost"]
    orders["cover_after_funded_days"] = [safe_div(r.on_hand + r.on_order + r.funded_qty, r.daily_demand) for r in orders.itertuples()]

    def decision(r):
        if r.funded_qty >= r.suggested_qty:
            return "Funded in full"
        if r.funded_qty >= r.essential_qty and r.essential_qty > 0:
            return "Essential funded; extra cover deferred"
        if r.funded_qty > 0:
            return "Partly funded (budget)"
        return "Not funded (budget)"

    orders["decision"] = [decision(r) for r in orders.itertuples()]
    orders["phase"] = np.where(orders["essential_qty"] > 0, "Essential first, then cover", "Cover only")
    orders = orders.sort_values(["priority_phase1", "gmroi"], ascending=[False, False]).reset_index(drop=True)
    orders["cumulative_cost"] = orders["funded_cost"].cumsum()
    return orders


def order_cash_requirement(orders: pd.DataFrame, supplier_terms: pd.DataFrame) -> pd.DataFrame:
    """Cash needed per supplier for funded orders, with the date-offset when payment falls due."""
    if orders.empty:
        return pd.DataFrame(columns=["supplier_id", "order_cost", "payment_terms_days"])
    g = orders.groupby("supplier_id", as_index=False)["funded_cost"].sum().rename(columns={"funded_cost": "order_cost"})
    return g.merge(supplier_terms[["supplier_id", "supplier_name", "payment_terms_days"]], on="supplier_id", how="left")
