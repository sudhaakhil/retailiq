"""Capital and working-capital analysis.

Formulas (all documented in docs/metrics_and_assumptions.md)
------------------------------------------------------------
* Operating working capital (OWC) = inventory + accounts receivable - accounts payable.
* Inventory days  = inventory at date  / (COGS per day over the window).
* Receivable days = receivables at date / (net sales per day over the window).
* Payable days    = payables at date   / (COGS per day over the window).
* Cash conversion cycle (CCC) = inventory days + receivable days - payable days.
* Minimum operating cash = average daily operating cash outflow over the window x reserve days.

Balances are measured AT a date; flows (sales, COGS) are measured OVER a window. The days
metrics divide a balance by a daily flow, so the window must be long enough to be meaningful.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .accounting import safe_div
from .dataset import Dataset, between
from .finance import sales_totals

MIN_RELIABLE_WINDOW_DAYS = 28


def _window(ds: Dataset, date: pd.Timestamp, window_days: int) -> tuple[pd.Timestamp, pd.Timestamp, int, Optional[str]]:
    end = min(pd.Timestamp(date), ds.end)
    start = max(end - pd.Timedelta(days=window_days - 1), ds.start)
    days = (end - start).days + 1
    warn = None
    if days < MIN_RELIABLE_WINDOW_DAYS:
        warn = (f"Only {days} day(s) of history are available before this date, so inventory, receivable and payable days "
                f"and the cash conversion cycle are shown as n/a (they need at least {MIN_RELIABLE_WINDOW_DAYS} days).")
    elif days < window_days:
        warn = f"Window shortened to {days} days because the data starts on {ds.start:%d %b %Y}."
    return start, end, days, warn


def working_capital_snapshot(ds: Dataset, date=None, window_days: int = 90, reserve_days: int = 14) -> Dict:
    """Balances at ``date`` and the flow-based days metrics over the trailing window."""
    date = (pd.Timestamp(date) if date is not None else ds.ref_date).normalize()
    date = min(max(date, ds.opening_date), ds.end)
    bal = ds.daily_balances.loc[date]
    start, end, days, warn = _window(ds, date, window_days)
    s = sales_totals(ds, start, end)
    led = ds.t["cash_ledger"]
    x = led[between(led["txn_date"], start, end) & (led["activity"] == "operating") & (led["amount"] < 0)]
    op_outflow = float(-x["amount"].sum())
    daily_sales = safe_div(s["net_sales"], days, 0.0)
    daily_cogs = safe_div(s["cogs"], days, 0.0)
    daily_outflow = safe_div(op_outflow, days, 0.0)
    reliable = days >= MIN_RELIABLE_WINDOW_DAYS
    dio = safe_div(bal["inventory"], daily_cogs) if reliable else None
    dso = safe_div(bal["receivables"], daily_sales) if reliable else None
    dpo = safe_div(bal["payables"], daily_cogs) if reliable else None
    ccc = dio + dso - dpo if None not in (dio, dso, dpo) else None
    owc = float(bal["inventory"] + bal["receivables"] - bal["payables"])
    notes: List[str] = [warn] if warn else []
    if daily_sales == 0:
        notes.append("No sales in the window: receivable days, inventory days and the cash cycle cannot be calculated.")
    elif daily_cogs == 0:
        notes.append("No cost of goods sold in the window: inventory and payable days cannot be calculated.")
    return {
        "date": date, "window_start": start, "window_end": end, "window_days": days,
        "cash": float(bal["cash"]), "inventory": float(bal["inventory"]), "receivables": float(bal["receivables"]),
        "payables": float(bal["payables"]), "loans": float(bal["loans"]), "operating_working_capital": owc,
        "net_sales_window": s["net_sales"], "cogs_window": s["cogs"], "daily_net_sales": daily_sales, "daily_cogs": daily_cogs,
        "gross_margin": s["gross_margin"], "inventory_days": dio, "receivable_days": dso, "payable_days": dpo,
        "cash_conversion_cycle": ccc, "daily_operating_outflow": daily_outflow, "reserve_days": reserve_days,
        "min_operating_cash": daily_outflow * reserve_days, "notes": notes,
    }


def working_capital_trend(ds: Dataset, freq: str = "ME") -> pd.DataFrame:
    """Month-end balances (a point-in-time view, not a flow)."""
    b = ds.daily_balances.loc[ds.start - pd.Timedelta(days=1):]
    m = b.resample(freq).last()
    m["operating_working_capital"] = m["inventory"] + m["receivables"] - m["payables"]
    return m.reset_index(names="date")


def receivable_aging(ds: Dataset, date=None) -> pd.DataFrame:
    """Outstanding customer invoices at ``date`` grouped by how overdue they are."""
    date = pd.Timestamp(date) if date is not None else ds.ref_date
    tx = ds.txn_with_receipts()
    open_ = tx[(tx["transaction_date"] <= date) & (tx["receipt_date"].isna() | (tx["receipt_date"] > date))].copy()
    open_["days_overdue"] = (date - open_["due_date"]).dt.days
    bins = [-10 ** 6, 0, 30, 60, 10 ** 6]
    labels = ["Not yet due", "1-30 days overdue", "31-60 days overdue", "Over 60 days overdue"]
    open_["bucket"] = pd.cut(open_["days_overdue"], bins=bins, labels=labels)
    open_["kind"] = np.where(open_["payment_method"] == "credit", "Business credit", "Card/UPI settlement in transit")
    out = open_.groupby(["kind", "bucket"], observed=False)["net_amount"].sum().reset_index()
    ob = ds.t["opening_receivables"]
    paid = ds.t["customer_receipts"].dropna(subset=["opening_receivable_id"])
    paid_ids = set(paid.loc[paid["receipt_date"] <= date, "opening_receivable_id"])
    old = ob[~ob["opening_receivable_id"].isin(paid_ids)]
    if not old.empty:
        od = (date - old["due_date"]).dt.days
        add = pd.DataFrame({"kind": "Business credit", "bucket": pd.cut(od, bins=bins, labels=labels), "net_amount": old["amount"]})
        out = pd.concat([out, add.groupby(["kind", "bucket"], observed=False)["net_amount"].sum().reset_index()])
        out = out.groupby(["kind", "bucket"], observed=False)["net_amount"].sum().reset_index()
    return out.rename(columns={"bucket": "aging_bucket", "net_amount": "amount"})


def payable_aging(ds: Dataset, date=None) -> pd.DataFrame:
    date = pd.Timestamp(date) if date is not None else ds.ref_date
    si = ds.t["supplier_invoices"]
    sp = ds.t["supplier_payments"]
    paid = sp[sp["payment_date"] <= date].groupby("invoice_id")["amount"].sum()
    inv = si[si["invoice_date"] <= date].copy()
    inv["paid"] = inv["invoice_id"].map(paid).fillna(0.0)
    inv["outstanding"] = inv["amount"] - inv["paid"]
    inv = inv[inv["outstanding"] > 0.005]
    inv["days_overdue"] = (date - inv["due_date"]).dt.days
    labels = ["Not yet due", "1-30 days overdue", "Over 30 days overdue"]
    inv["bucket"] = pd.cut(inv["days_overdue"], bins=[-10 ** 6, 0, 30, 10 ** 6], labels=labels)
    return inv.groupby("bucket", observed=False)["outstanding"].sum().reset_index().rename(columns={"bucket": "aging_bucket", "outstanding": "amount"})


# ---------------------------------------------------------------------------- scenario tool
def wc_scenario(base: Dict, sales_growth: float, gross_margin: float, inventory_days: float, collection_days: float,
                payable_days: float, min_cash_reserve: float, base_daily_sales: Optional[float] = None) -> Dict:
    """What-if working-capital requirement. All inputs are explicit assumptions.

    ``sales_growth`` and ``gross_margin`` are fractions (0.10 = 10%). The model scales daily sales, derives daily COGS
    from the scenario margin, and recomputes inventory, receivables and payables from the chosen days.
    It captures only the TIMING of cash tied up in working capital - not extra profit, tax, or growth capex.
    """
    problems = []
    if gross_margin >= 1:
        problems.append("Gross margin must be below 100%.")
    for name, v in [("inventory days", inventory_days), ("collection days", collection_days), ("payable days", payable_days),
                    ("minimum cash reserve", min_cash_reserve)]:
        if v < 0:
            problems.append(f"{name.capitalize()} cannot be negative.")
    if sales_growth <= -1:
        problems.append("Sales growth must be above -100%.")
    if base_daily_sales is None and any(base.get(k) is None for k in ("inventory_days", "receivable_days", "payable_days")):
        problems.append("Today's inventory, receivable and payable days are not available (too little history or no sales/COGS in the window), "
                        "so the scenario has no baseline to start from. Choose a later date or a longer history.")
    s0 = base_daily_sales if base_daily_sales is not None else base["daily_net_sales"]
    if s0 is None or s0 <= 0:
        problems.append("Baseline daily sales are zero, so growth cannot be applied. Enter a manual daily sales figure.")
    if problems:
        return {"valid": False, "problems": problems}

    s1 = s0 * (1 + sales_growth)
    cogs0 = base["daily_cogs"] if base_daily_sales is None else s0 * (1 - (base["gross_margin"] or 0.0))
    cogs1 = s1 * (1 - gross_margin)

    def parts(sales_d, cogs_d, inv_d, col_d, pay_d):
        inv, ar, ap = cogs_d * inv_d, sales_d * col_d, cogs_d * pay_d
        return {"inventory": inv, "receivables": ar, "payables": ap, "owc": inv + ar - ap}

    b_inv_d, b_col_d, b_pay_d = base["inventory_days"] or 0.0, base["receivable_days"] or 0.0, base["payable_days"] or 0.0
    baseline = parts(s0, cogs0, b_inv_d, b_col_d, b_pay_d)
    scenario = parts(s1, cogs1, inventory_days, collection_days, payable_days)
    delta_owc = scenario["owc"] - baseline["owc"]
    cash0 = base["cash"]
    cash_after = cash0 - delta_owc
    shortfall = max(0.0, min_cash_reserve - cash_after)

    def requirement(sg=sales_growth, gm=gross_margin, idays=inventory_days, cdays=collection_days, pdays=payable_days):
        sc = parts(s0 * (1 + sg), s0 * (1 + sg) * (1 - gm), idays, cdays, pdays)
        return sc["owc"] - baseline["owc"]

    base_gm = base["gross_margin"] if base["gross_margin"] is not None else gross_margin
    sens = []
    drivers = [
        ("Sales growth", f"{sales_growth:+.1%}", dict(sg=sales_growth), dict(sg=0.0),
         "More sales need more stock and more receivables; payables fund part of the stock."),
        ("Gross margin", f"{gross_margin:.1%}", dict(gm=gross_margin), dict(gm=base_gm),
         "A higher margin lowers cost per rupee of sales, so less stock is needed to support the same sales."),
        ("Inventory days", f"{inventory_days:.0f} days", dict(idays=inventory_days), dict(idays=b_inv_d),
         "Holding stock longer ties up more cash in unsold goods."),
        ("Collection days", f"{collection_days:.1f} days", dict(cdays=collection_days), dict(cdays=b_col_d),
         "Customers paying later means sales are booked but cash arrives after a delay."),
        ("Payable days", f"{payable_days:.0f} days", dict(pdays=payable_days), dict(pdays=b_pay_d),
         "Paying suppliers later lets suppliers finance more of your stock."),
    ]
    for name, setting, own, reset, why in drivers:
        # effect of changing ONLY this driver away from baseline
        kwargs = dict(sg=0.0, gm=base_gm, idays=b_inv_d, cdays=b_col_d, pdays=b_pay_d)
        kwargs.update(own)
        sens.append({"driver": name, "your_setting": setting, "effect_on_cash_needed": requirement(**kwargs), "why_it_matters": why})
    reserve_effect = min_cash_reserve - base["min_operating_cash"]
    sens.append({"driver": "Minimum cash reserve", "your_setting": f"{min_cash_reserve:,.0f}", "effect_on_cash_needed": reserve_effect,
                 "why_it_matters": "A larger safety buffer must be held back from spending (compared with the estimated minimum operating cash)."})
    return {
        "valid": True, "problems": [], "baseline": baseline, "scenario": scenario, "delta_owc": delta_owc,
        "daily_sales_baseline": s0, "daily_sales_scenario": s1, "cash_now": cash0, "cash_after_working_capital": cash_after,
        "min_cash_reserve": min_cash_reserve, "shortfall": shortfall, "surplus": max(0.0, cash_after - min_cash_reserve),
        "capital_required": shortfall, "sensitivity": pd.DataFrame(sens),
    }


def capital_for_sales_increase(base: Dict, target_growth: float) -> Dict:
    """Extra working capital for a planned sales increase, holding today's days and margin constant."""
    return wc_scenario(base, target_growth, base["gross_margin"] or 0.0, base["inventory_days"] or 0.0,
                       base["receivable_days"] or 0.0, base["payable_days"] or 0.0, base["min_operating_cash"])
