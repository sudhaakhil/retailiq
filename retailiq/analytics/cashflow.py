"""13-week cash-flow forecast (daily engine, weekly presentation).

Every component is built from data already in the database plus visible assumptions:

* Sales: same weekday one year earlier (364-day seasonal naive) x a store growth factor x (1 + adjustment).
* Collections: cash/UPI/card sales settle after their usual 0/1/2 days; business credit sales follow the
  empirical collection-lag distribution of the last 12 months; invoices already outstanding are collected
  on their due date (plus typical lateness), never earlier than tomorrow.
* Supplier payments: unpaid invoices on their due date; open purchase orders arrive at the expected date plus
  typical lateness and are paid after the supplier's terms; NEW purchase orders come from a day-by-day
  replenishment simulation using the reorder rules in ``inventory.py``.
* Operating expenses: fixed items repeat last month's amounts on the same day of month; variable items follow
  their recent ratio to sales; marketing follows planned campaign budgets.
* Loans: contractual instalments (interest and principal are shown separately).
* Capex, owner drawings and any other known items: the editable planned-items table.

The forecast is an assumption-based projection, not a prediction of what will certainly happen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .accounting import add_months, month_end, safe_div
from .dataset import Dataset, between
from .finance import VARIABLE_COST_CATEGORIES
from .inventory import replenishment_plan

HORIZON_WEEKS = 13
EXTRA_LOOKAHEAD = 140  # days beyond the horizon used for demand look-ahead in the purchase simulation


@dataclass
class CashAssumptions:
    ref_date: Optional[pd.Timestamp] = None
    weeks: int = HORIZON_WEEKS
    sales_adjustment: float = 0.0       # extra % on top of the seasonal baseline (0.05 = +5%)
    collection_delay_days: int = 0      # customers pay this many days later than usual
    supplier_delay_days: int = 0        # we pay suppliers this many days later than due
    cover_days: int = 40                # stock cover (days) targeted after each replenishment
    service_z: float = 1.65
    min_cash_reserve: float = 750_000.0
    marketing_uplift_pct: float = 0.0   # extra % on planned marketing budgets
    planned_items: Optional[pd.DataFrame] = None  # columns: planned_date, category, description, amount (signed), activity
    inventory_release_pct: float = 0.0  # one-off: reduce planned purchases (e.g. 0.10 = buy 10% less)
    notes: List[str] = field(default_factory=list)


def _growth_factors(ds: Dataset, ref: pd.Timestamp, notes: List[str]) -> Dict[str, float]:
    """Store-level year-on-year unit growth (trailing 90 days vs the same 90 days a year earlier)."""
    d = ds.daily
    out = {}
    cur = d[between(d["date"], ref - pd.Timedelta(days=89), ref)].groupby("store_id")["units"].sum()
    prev = d[between(d["date"], ref - pd.Timedelta(days=89 + 364), ref - pd.Timedelta(days=364))].groupby("store_id")["units"].sum()
    for s in ds.store_ids:
        c, p = float(cur.get(s, 0.0)), float(prev.get(s, 0.0))
        if p <= 0:
            out[s] = 1.0
            notes.append(f"Store {s}: no comparable period a year ago, so growth is assumed to be 0%.")
        else:
            out[s] = float(min(max(c / p, 0.7), 1.4))
    return out


def _seasonal_arrays(ds: Dataset, ref: pd.Timestamp, n_days: int, growth: Dict[str, float], adj: float):
    """Forecast units and net revenue per (store, product) for days ref+1 ... ref+n_days."""
    dates = pd.date_range(ref + pd.Timedelta(days=1), periods=n_days, freq="D")
    ly = dates - pd.Timedelta(days=364)
    d = ds.daily[between(ds.daily["date"], ly[0], ly[-1])]
    keys = [(s, p) for s in ds.store_ids for p in ds.t["products"]["product_id"]]
    units = {k: np.zeros(n_days) for k in keys}
    rev = {k: np.zeros(n_days) for k in keys}
    pos = {dt_: i for i, dt_ in enumerate(ly)}
    for r in d.itertuples(index=False):
        i = pos.get(r.date)
        if i is None:
            continue
        g = growth[r.store_id] * (1 + adj)
        units[(r.store_id, r.product_id)][i] += r.units * g
        rev[(r.store_id, r.product_id)][i] += r.net * g
    return dates, units, rev


def _payment_mix(ds: Dataset, ref: pd.Timestamp) -> Dict:
    """Value share by payment method and credit collection-lag distribution (last 12 months)."""
    tx = ds.t["sales_transactions"]
    win = tx[between(tx["transaction_date"], ref - pd.Timedelta(days=364), ref)]
    share = (win.groupby("payment_method")["net_amount"].sum() / max(win["net_amount"].sum(), 1.0)).to_dict()
    rec = ds.t["customer_receipts"].dropna(subset=["transaction_id"])
    cr = win[win["payment_method"] == "credit"].merge(rec[["transaction_id", "receipt_date"]], on="transaction_id", how="inner")
    lags = (cr["receipt_date"] - cr["transaction_date"]).dt.days
    if len(lags) >= 10:
        dist = lags.value_counts(normalize=True).sort_index()
        credit_lags = {int(k): float(v) for k, v in dist.items()}
        late_mean = float(lags.mean() - 30)
    else:
        credit_lags, late_mean = {30: 1.0}, 0.0
    return {"share": share, "credit_lags": credit_lags, "credit_late_mean": max(late_mean, 0.0)}


def _opex_schedule(ds: Dataset, ref: pd.Timestamp, dates: pd.DatetimeIndex, rev_by_day: np.ndarray, a: CashAssumptions) -> Dict[str, np.ndarray]:
    ox = ds.t["operating_expenses"]
    H = len(dates)
    fixed = np.zeros(H)
    variable = np.zeros(H)
    marketing = np.zeros(H)
    last_month_start = ref.to_period("M").start_time
    if ref != ref + pd.offsets.MonthEnd(0):
        last_month_start = (ref.to_period("M") - 1).start_time
    last_month_end = last_month_start + pd.offsets.MonthEnd(0)
    base = ox[between(ox["expense_date"], last_month_start, last_month_end) & (ox["cost_behaviour"] == "fixed")]
    pos = {d_: i for i, d_ in enumerate(dates)}
    # fixed: repeat the same items on the same day of month
    for r in base.itertuples(index=False):
        for k in range(0, 5):
            tgt_month = add_months(last_month_start.date(), k + 1, 1)
            day = min(r.expense_date.day, month_end(tgt_month).day)
            d_ = pd.Timestamp(tgt_month.replace(day=day))
            if d_ in pos:
                fixed[pos[d_]] += r.amount
    # variable: recent ratio to net sales, paid at month end for that month's forecast sales
    win = ox[between(ox["expense_date"], ref - pd.Timedelta(days=179), ref) & ox["category"].isin(VARIABLE_COST_CATEGORIES)]
    dd = ds.daily[between(ds.daily["date"], ref - pd.Timedelta(days=179), ref)]
    sales_win = float(dd["net"].sum())
    ratio = safe_div(float(win["amount"].sum()), sales_win, 0.0)
    month_idx = pd.Series(dates.to_period("M"))
    for m, idxs in month_idx.groupby(month_idx).groups.items():
        me = m.end_time.normalize()
        if me in pos:
            variable[pos[me]] += ratio * float(rev_by_day[list(idxs)].sum())
    # marketing: planned campaign budgets spread over campaign days, paid at month end
    camps = ds.t["campaigns"]
    for c in camps.itertuples(index=False):
        if c.end_date <= ref or c.status == "completed":
            continue
        n = (c.end_date - c.start_date).days + 1
        for m in pd.period_range(max(c.start_date, ref + pd.Timedelta(days=1)), c.end_date, freq="M"):
            ms, me = max(m.start_time, c.start_date), min(m.end_time.normalize(), c.end_date)
            overlap = (me - ms).days + 1
            pay_day = m.end_time.normalize()
            if pay_day in pos:
                marketing[pos[pay_day]] += c.planned_budget * overlap / n * (1 + a.marketing_uplift_pct)
    return {"fixed": fixed, "variable": variable, "marketing": marketing}


def _existing_flows(ds: Dataset, ref: pd.Timestamp, dates: pd.DatetimeIndex, a: CashAssumptions, mix: Dict):
    """Collections on invoices already issued and payments on invoices/purchase orders already placed."""
    H = len(dates)
    pos = {d_: i for i, d_ in enumerate(dates)}
    coll = np.zeros(H)
    tx = ds.txn_with_receipts()
    open_ = tx[(tx["transaction_date"] <= ref) & (tx["receipt_date"].isna() | (tx["receipt_date"] > ref))]
    tomorrow = ref + pd.Timedelta(days=1)
    for r in open_.itertuples(index=False):
        is_credit = r.payment_method == "credit"
        late = mix["credit_late_mean"] if is_credit else 0.0
        extra = a.collection_delay_days if is_credit else 0
        d_ = max(r.due_date + pd.Timedelta(days=round(late) + extra), tomorrow)
        if d_ in pos:
            coll[pos[d_]] += r.net_amount
    orec = ds.t["opening_receivables"]
    paid = set(ds.t["customer_receipts"].dropna(subset=["opening_receivable_id"]).query("receipt_date <= @ref")["opening_receivable_id"])
    for r in orec[~orec["opening_receivable_id"].isin(paid)].itertuples(index=False):
        d_ = max(r.due_date + pd.Timedelta(days=round(mix["credit_late_mean"]) + a.collection_delay_days), tomorrow)
        if d_ in pos:
            coll[pos[d_]] += r.amount
    # unpaid supplier invoices
    pay_existing = np.zeros(H)
    si, sp = ds.t["supplier_invoices"], ds.t["supplier_payments"]
    paid_amt = sp[sp["payment_date"] <= ref].groupby("invoice_id")["amount"].sum()
    inv = si[si["invoice_date"] <= ref].copy()
    inv["outstanding"] = inv["amount"] - inv["invoice_id"].map(paid_amt).fillna(0.0)
    for r in inv[inv["outstanding"] > 0.005].itertuples(index=False):
        d_ = max(r.due_date, ref + pd.Timedelta(days=1)) + pd.Timedelta(days=a.supplier_delay_days)
        if d_ in pos:
            pay_existing[pos[d_]] += r.outstanding
    return coll, pay_existing


def forecast_cash(ds: Dataset, a: Optional[CashAssumptions] = None) -> Dict:
    """Run the daily engine and return daily/weekly tables plus diagnostics."""
    a = a or CashAssumptions()
    ref = pd.Timestamp(a.ref_date) if a.ref_date is not None else ds.ref_date
    notes: List[str] = list(a.notes)
    H = a.weeks * 7
    dates = pd.date_range(ref + pd.Timedelta(days=1), periods=H, freq="D")
    n_ext = H + EXTRA_LOOKAHEAD
    mix = _payment_mix(ds, ref)
    growth = _growth_factors(ds, ref, notes)
    full_dates, units, rev = _seasonal_arrays(ds, ref, n_ext, growth, a.sales_adjustment)
    if ref - pd.Timedelta(days=364) - pd.Timedelta(days=0) < ds.start:
        notes.append("Less than one year of history before the reference date: the seasonal baseline is unreliable.")

    rev_total = np.zeros(n_ext)
    for k, v in rev.items():
        rev_total += v
    rev_h = rev_total[:H]

    # ---- customer collections from forecast sales
    share = mix["share"]
    lag_fixed = {"cash": 0, "upi": 1, "card": 2}
    coll_new = np.zeros(H + 120)
    for method, lag in lag_fixed.items():
        s = share.get(method, 0.0)
        for t in range(H):
            coll_new[t + lag] += rev_h[t] * s
    s_credit = share.get("credit", 0.0)
    delay = a.collection_delay_days  # applies to business-credit customers only (card/UPI settle via the processor)
    for lag, p in mix["credit_lags"].items():
        eff = max(lag + delay, 1)
        for t in range(H):
            coll_new[min(t + eff, len(coll_new) - 1)] += rev_h[t] * s_credit * p
    coll_forecast = coll_new[:H].copy()
    coll_existing, pay_existing = _existing_flows(ds, ref, dates, a, mix)

    # ---- refunds
    win_sales = ds.daily[between(ds.daily["date"], ref - pd.Timedelta(days=179), ref)]
    refund_rate = safe_div(float(win_sales["refund"].sum()), float(win_sales["net"].sum()), 0.0)
    refunds = rev_h * refund_rate

    # ---- purchases via replenishment simulation
    plan = replenishment_plan(ds, ref, demand_method="seasonal", z=a.service_z, cover_days=a.cover_days)
    sup = ds.t["suppliers"].set_index("supplier_id")
    prod = ds.t["products"].set_index("product_id")
    po = ds.t["purchase_orders"]
    pol = ds.t["purchase_order_lines"]
    open_lines = pol.merge(po[["po_id", "store_id", "supplier_id", "expected_date", "status"]], on="po_id")
    open_lines = open_lines[open_lines["status"] == "open"]
    lts = ds.t["purchase_orders"].copy()
    lts = lts[(lts["status"] == "received")]
    late_mean = float(np.clip(((lts["received_date"] - lts["expected_date"]).dt.days).mean(), 0, 10)) if len(lts) else 0.0
    pay_inv = np.zeros(H)       # cash paid for inventory (open POs + new POs)
    pay_open_po = np.zeros(H)
    purchases_ordered = np.zeros(H)
    new_orders = []
    release = 1.0 - a.inventory_release_pct
    for r in plan.itertuples(index=False):
        key = (r.store_id, r.product_id)
        U = units[key]
        arrivals = np.zeros(n_ext)
        terms = int(sup.at[r.supplier_id, "payment_terms_days"])
        pipeline = 0.0
        for ln in open_lines[(open_lines["store_id"] == r.store_id) & (open_lines["product_id"] == r.product_id)].itertuples(index=False):
            arr = max((ln.expected_date - ref).days + int(round(late_mean)) - 1, 0)
            arr = min(arr, n_ext - 1)
            arrivals[arr] += ln.quantity_ordered
            pipeline += ln.quantity_ordered
            pay_day = arr + terms + a.supplier_delay_days
            if pay_day < H:
                pay_open_po[pay_day] += ln.line_cost
        on_hand = float(r.on_hand)
        lt = max(int(round(r.lead_time_days)), 1)
        cp, mx = r.case_pack, (r.max_units_per_order // r.case_pack) * r.case_pack
        for t in range(H):
            on_hand += arrivals[t]
            pipeline -= arrivals[t]
            on_hand = max(on_hand - U[t], 0.0)
            horizon = U[t + 1: t + 1 + lt + a.cover_days]
            d = float(horizon.mean()) if len(horizon) else 0.0
            rop = d * lt + r.safety_stock
            up_to = d * (lt + a.cover_days) + r.safety_stock
            position = on_hand + pipeline
            if d > 0 and position <= rop:
                qty = int(math.ceil(max(up_to - position, cp) / cp) * cp)
                qty = int(min(qty, mx) * release)
                qty = (qty // cp) * cp if release < 1 else qty
                if qty > 0:
                    arr = t + lt
                    cost = qty * r.unit_cost
                    purchases_ordered[t] += cost
                    if arr < n_ext:
                        arrivals[arr] += qty
                    pipeline += qty
                    pay_day = arr + terms + a.supplier_delay_days
                    new_orders.append({"order_date": dates[t], "store_id": r.store_id, "product_id": r.product_id, "supplier_id": r.supplier_id,
                                       "quantity": qty, "unit_cost": r.unit_cost, "cost": cost, "arrival_date": ref + pd.Timedelta(days=arr + 1),
                                       "payment_date": ref + pd.Timedelta(days=pay_day + 1)})
                    if pay_day < H:
                        pay_inv[pay_day] += cost
    # ---- operating expenses, loans, planned items
    opex = _opex_schedule(ds, ref, dates, rev_h, a)
    loan_int = np.zeros(H)
    loan_prin = np.zeros(H)
    pos_map = {d_: i for i, d_ in enumerate(dates)}
    for sched in ds.loan_schedules.values():
        for r in sched[between(sched["payment_date"], dates[0], dates[-1])].itertuples(index=False):
            i = pos_map[r.payment_date]
            loan_int[i] += r.interest
            loan_prin[i] += r.principal
    capex = np.zeros(H)
    other_fin = np.zeros(H)
    other_op = np.zeros(H)
    planned = a.planned_items if a.planned_items is not None else ds.t["planned_cash_items"]
    if planned is not None and len(planned):
        for r in planned.itertuples(index=False):
            d_ = pd.Timestamp(r.planned_date)
            if d_ in pos_map:
                if r.activity == "investing":
                    capex[pos_map[d_]] += r.amount
                elif r.activity == "financing":
                    other_fin[pos_map[d_]] += r.amount
                else:
                    other_op[pos_map[d_]] += r.amount

    opening_cash = float(ds.daily_balances["cash"].loc[min(ref, ds.end)])
    daily = pd.DataFrame({
        "date": dates,
        "collections_from_existing_invoices": coll_existing,
        "collections_from_forecast_sales": coll_forecast,
        "customer_refunds": -refunds,
        "supplier_payments_existing_invoices": -pay_existing,
        "supplier_payments_inventory_purchases": -(pay_inv + pay_open_po),
        "opex_fixed": -opex["fixed"], "opex_variable": -opex["variable"], "opex_marketing": -opex["marketing"],
        "loan_interest": -loan_int, "loan_principal": -loan_prin,
        "capital_expenditure_and_planned_investing": capex, "owner_drawings_and_planned_financing": other_fin,
        "other_planned_operating": other_op,
    })
    flow_cols = [c for c in daily.columns if c != "date"]
    daily["net_cash_flow"] = daily[flow_cols].sum(axis=1)
    daily["closing_cash"] = opening_cash + daily["net_cash_flow"].cumsum()
    daily["forecast_net_sales"] = rev_h
    weekly = _weekly(daily, opening_cash)
    summary = shortfall_summary(daily, weekly, a.min_cash_reserve)
    summary.update({"opening_cash": opening_cash, "reference_date": ref})
    return {
        "assumptions": a, "daily": daily, "weekly": weekly, "summary": summary, "new_orders": pd.DataFrame(new_orders),
        "notes": notes, "growth": growth, "mix": mix, "refund_rate": refund_rate,
        "purchases_ordered_by_day": purchases_ordered,
    }


def _weekly(daily: pd.DataFrame, opening_cash: float) -> pd.DataFrame:
    d = daily.copy()
    d["week"] = (np.arange(len(d)) // 7) + 1
    flow_cols = [c for c in d.columns if c not in ("date", "week", "closing_cash", "forecast_net_sales")]
    w = d.groupby("week").agg({**{c: "sum" for c in flow_cols}, "forecast_net_sales": "sum", "date": ["min", "max"],
                               "closing_cash": ["last", "min"]})
    w.columns = ["_".join(c).rstrip("_") if isinstance(c, tuple) else c for c in w.columns]
    w = w.rename(columns={"date_min": "week_start", "date_max": "week_end", "closing_cash_last": "closing_cash",
                          "closing_cash_min": "lowest_daily_cash", "forecast_net_sales_sum": "forecast_net_sales"})
    w = w.rename(columns={f"{c}_sum": c for c in flow_cols})
    w["opening_cash"] = [opening_cash] + list(w["closing_cash"].iloc[:-1])
    return w.reset_index()


def shortfall_summary(daily: pd.DataFrame, weekly: pd.DataFrame, reserve: float) -> Dict:
    """When (and by how much) cash first falls below the reserve, plus the lowest point."""
    below = daily[daily["closing_cash"] < reserve]
    low_idx = int(daily["closing_cash"].idxmin())
    first = below.iloc[0] if len(below) else None
    wk_below = weekly[weekly["closing_cash"] < reserve]
    return {
        "reserve": reserve, "lowest_cash": float(daily.loc[low_idx, "closing_cash"]), "lowest_cash_date": daily.loc[low_idx, "date"],
        "breach": first is not None, "first_breach_date": first["date"] if first is not None else None,
        "first_breach_week": int(weekly.loc[weekly["week_end"] >= first["date"], "week"].iloc[0]) if first is not None else None,
        "max_shortfall": float(max(reserve - daily["closing_cash"].min(), 0.0)),
        "days_below_reserve": int(len(below)), "weeks_below_reserve_at_close": int(len(wk_below)),
        "closing_cash_end": float(daily["closing_cash"].iloc[-1]),
    }


def weekly_table_for_display(weekly: pd.DataFrame) -> pd.DataFrame:
    """Re-shape the weekly forecast into the statement layout (rows = line items, columns = weeks)."""
    lines = [
        ("Opening cash", "opening_cash"),
        ("Customer collections - invoices already issued", "collections_from_existing_invoices"),
        ("Customer collections - forecast sales", "collections_from_forecast_sales"),
        ("Customer refunds", "customer_refunds"),
        ("Supplier payments - invoices already received", "supplier_payments_existing_invoices"),
        ("Supplier payments - inventory purchases", "supplier_payments_inventory_purchases"),
        ("Operating expenses - fixed", "opex_fixed"), ("Operating expenses - variable", "opex_variable"),
        ("Operating expenses - marketing", "opex_marketing"),
        ("Loan interest", "loan_interest"), ("Loan principal repayment", "loan_principal"),
        ("Capital expenditure (planned)", "capital_expenditure_and_planned_investing"),
        ("Owner drawings / other financing (planned)", "owner_drawings_and_planned_financing"),
        ("Other planned operating items", "other_planned_operating"),
        ("Net cash flow", "net_cash_flow"), ("Closing cash", "closing_cash"), ("Lowest daily cash in week", "lowest_daily_cash"),
    ]
    out = pd.DataFrame({label: weekly[col].values for label, col in lines}, index=[f"W{w}" for w in weekly["week"]]).T
    return out
