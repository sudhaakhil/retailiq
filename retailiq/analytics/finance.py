"""Finance and profitability calculations.

Accounting policy (also documented in docs/metrics_and_assumptions.md)
----------------------------------------------------------------------
* Revenue ("net sales") = sales after discounts, less refunds recognised in the period.
  Amounts exclude GST. Borrowing and owner investment are NEVER revenue.
* Cost of goods sold (COGS) = weighted-average cost of units sold, less the cost of returned
  units put back into stock. Buying stock only increases inventory; it is not an expense.
* Operating expenses are recognised and paid on the same date (no accruals are simulated).
  Capital expenditure is capitalised and depreciated straight-line from the month after purchase.
* Interest is a financing cost shown below operating profit. Loan principal is never an expense.
* No income tax is modelled, so the bottom line is "profit before tax".
* Shared (head-office) costs are allocated to stores by share of net sales (or equally).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .accounting import fixed_assets_net, safe_div
from .dataset import Dataset, between

VARIABLE_COST_CATEGORIES = ["Payment processing fees", "Delivery & logistics", "Packaging & consumables"]


# ---------------------------------------------------------------------------- helpers
def previous_period(start, end) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """The equal-length window that ends the day before ``start``."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    n = (end - start).days + 1
    return start - pd.Timedelta(days=n), start - pd.Timedelta(days=1)


def year_ago(start, end) -> Tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(start) - pd.DateOffset(years=1), pd.Timestamp(end) - pd.DateOffset(years=1)


def pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Relative change; None when the previous value is zero/missing (avoids misleading infinities)."""
    if current is None or previous is None or pd.isna(current) or pd.isna(previous) or previous == 0:
        return None
    return (current - previous) / abs(previous)


def _sel(ds: Dataset, stores: Optional[Iterable[str]]) -> List[str]:
    sel = [s for s in (stores or ds.store_ids) if s in ds.store_ids]
    return sel or list(ds.store_ids)


def _daily_filter(ds: Dataset, start, end, stores=None, categories=None, products=None) -> pd.DataFrame:
    d = ds.daily
    m = between(d["date"], start, end)
    if stores:
        m &= d["store_id"].isin(list(stores))
    if categories:
        m &= d["category"].isin(list(categories))
    if products:
        m &= d["product_id"].isin(list(products))
    return d[m]


def sales_totals(ds: Dataset, start, end, stores=None, categories=None, products=None) -> Dict[str, Optional[float]]:
    """Revenue, COGS and gross profit for a period (all amounts in base currency)."""
    x = _daily_filter(ds, start, end, stores, categories, products)
    gross, disc, net_after_disc = x["gross"].sum(), x["discount"].sum(), x["net"].sum()
    refunds = x["refund"].sum()
    net_sales = net_after_disc - refunds
    cogs = x["cogs"].sum() - x["cogs_rev"].sum()
    gp = net_sales - cogs
    return {
        "gross_sales": float(gross), "discounts": float(disc), "sales_after_discounts": float(net_after_disc),
        "returns": float(refunds), "net_sales": float(net_sales), "cogs": float(cogs), "gross_profit": float(gp),
        "gross_margin": safe_div(gp, net_sales), "units": float(x["units"].sum() - x["ret_units"].sum()),
        "orders": float(x["orders"].sum()), "return_rate": safe_div(refunds, net_after_disc),
        "discount_rate": safe_div(disc, gross),
    }


def hq_allocation_share(ds: Dataset, start, end, stores: Sequence[str], method: str = "net_sales") -> float:
    """Share (0-1) of head-office cost allocated to the selected stores."""
    all_stores = ds.store_ids
    if set(stores) >= set(all_stores):
        return 1.0
    if method == "equal":
        return len(stores) / len(all_stores)
    total = sales_totals(ds, start, end)["net_sales"]
    mine = sales_totals(ds, start, end, stores)["net_sales"]
    share = safe_div(mine, total)
    return share if share is not None else len(stores) / len(all_stores)


# ---------------------------------------------------------------------------- P&L
def pnl(ds: Dataset, start, end, stores: Optional[Sequence[str]] = None, allocate: str = "net_sales") -> Dict:
    """Profit and loss for a period.

    Returns a dict containing the headline lines plus ``opex_table`` (by category, direct vs allocated).
    Head-office (store_id NULL) expenses, depreciation and interest are allocated to a store subset
    using ``allocate`` ("net_sales" or "equal"); for the whole company nothing is allocated.
    """
    sel = _sel(ds, stores)
    totals = sales_totals(ds, start, end, sel)
    share = hq_allocation_share(ds, start, end, sel, allocate)

    ox = ds.t["operating_expenses"]
    px = ox[between(ox["expense_date"], start, end)]
    direct = px[px["store_id"].isin(sel)].groupby("category")["amount"].sum()
    hq = px[px["store_id"].isna()].groupby("category")["amount"].sum() * share
    cats = sorted(set(direct.index) | set(hq.index))
    opex_table = pd.DataFrame({
        "category": cats,
        "direct": [float(direct.get(c, 0.0)) for c in cats],
        "allocated_shared": [float(hq.get(c, 0.0)) for c in cats],
    })
    opex_table["total"] = opex_table["direct"] + opex_table["allocated_shared"]
    behaviour = ox.drop_duplicates("category").set_index("category")["cost_behaviour"].to_dict()
    opex_table["behaviour"] = opex_table["category"].map(behaviour)
    opex_table = opex_table.sort_values("total", ascending=False).reset_index(drop=True)
    opex = float(opex_table["total"].sum())

    dep = ds.dep
    pdp = dep[between(dep["dep_date"], start, end)] if not dep.empty else dep
    dep_direct = float(pdp[pdp["store_id"].isin(sel)]["amount"].sum()) if not pdp.empty else 0.0
    dep_hq = float(pdp[pdp["store_id"].isna()]["amount"].sum()) * share if not pdp.empty else 0.0
    depreciation = dep_direct + dep_hq

    lp = ds.t["loan_payments"]
    plp = lp[between(lp["payment_date"], start, end)]
    interest = float(plp["interest_amount"].sum()) * share
    principal = float(plp["principal_amount"].sum()) * share

    operating_profit = totals["gross_profit"] - opex - depreciation
    out = dict(totals)
    out.update({
        "operating_expenses": opex, "depreciation": depreciation, "operating_profit": operating_profit,
        "operating_margin": safe_div(operating_profit, totals["net_sales"]),
        "interest_expense": interest, "profit_before_tax": operating_profit - interest,
        "principal_repaid_memo": principal, "hq_allocation_share": share, "opex_table": opex_table,
        "marketing": float(opex_table.loc[opex_table["category"] == "Marketing", "total"].sum()),
    })
    return out


def pnl_statement(p: Dict) -> pd.DataFrame:
    """Format a ``pnl`` result as a readable statement (lines in accounting order)."""
    rows = [
        ("Gross sales (list price x units)", p["gross_sales"], "Before discounts and returns"),
        ("Less: discounts", -p["discounts"], "Promotions, loyalty and trade discounts"),
        ("Less: returns (refunds)", -p["returns"], "Refunds recognised on the return date"),
        ("Net sales", p["net_sales"], "Revenue; excludes GST, loans and owner investment"),
        ("Less: cost of goods sold", -p["cogs"], "Weighted-average cost, net of returned stock"),
        ("Gross profit", p["gross_profit"], "NOT net profit: before operating costs"),
        ("Less: operating expenses", -p["operating_expenses"], "Rent, wages, utilities, marketing, fees ..."),
        ("Less: depreciation", -p["depreciation"], "Straight-line on capital expenditure"),
        ("Operating profit", p["operating_profit"], "Gross profit less operating expenses and depreciation"),
        ("Less: interest expense", -p["interest_expense"], "Loan interest only; principal is not an expense"),
        ("Profit before tax", p["profit_before_tax"], "No income tax is modelled"),
    ]
    return pd.DataFrame(rows, columns=["line", "amount", "note"])


def monthly_pnl(ds: Dataset, start=None, end=None, stores: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """Calendar-month P&L (head-office costs allocated if a store subset is chosen).

    A ``full_month`` column marks months that are only partly inside the requested range; fixed costs are booked
    on their payment dates, so partial months give misleading margins and should not be charted.
    """
    start = ds.start if start is None else max(pd.Timestamp(start), ds.start)
    end = ds.end if end is None else min(pd.Timestamp(end), ds.end)
    rows = []
    for m in pd.period_range(start, end, freq="M"):
        ms, me = max(m.start_time, start), min(m.end_time.normalize(), end)
        p = pnl(ds, ms, me, stores)
        row = {k: p[k] for k in ["gross_sales", "discounts", "returns", "net_sales", "cogs", "gross_profit", "gross_margin",
                                 "operating_expenses", "marketing", "depreciation", "operating_profit", "operating_margin",
                                 "interest_expense", "profit_before_tax", "units", "orders"]}
        row["month"] = m.start_time
        row["full_month"] = bool(ms == m.start_time and me == m.end_time.normalize())
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------- cash flow
def cash_balance_at(ds: Dataset, date) -> float:
    d = pd.Timestamp(date).normalize()
    if d < ds.opening_date:
        return float(ds.opening["cash"])
    return float(ds.daily_balances["cash"].loc[min(d, ds.end)])


def cash_flow_statement(ds: Dataset, start, end) -> Dict:
    """Cash movements grouped by activity with an opening-to-closing reconciliation."""
    led = ds.t["cash_ledger"]
    x = led[between(led["txn_date"], start, end)]
    by_cat = x.groupby(["activity", "category"], as_index=False)["amount"].sum()
    by_cat["direction"] = np.where(by_cat["amount"] >= 0, "inflow", "outflow")
    by_act = x.groupby("activity")["amount"].sum().to_dict()
    opening = cash_balance_at(ds, pd.Timestamp(start) - pd.Timedelta(days=1))
    inflows = float(x.loc[x["amount"] > 0, "amount"].sum())
    outflows = float(-x.loc[x["amount"] < 0, "amount"].sum())
    closing = cash_balance_at(ds, end)
    return {
        "opening_cash": opening, "inflows": inflows, "outflows": outflows, "net_change": inflows - outflows,
        "closing_cash": closing, "reconciliation_difference": round(opening + inflows - outflows - closing, 2),
        "by_category": by_cat, "operating": float(by_act.get("operating", 0.0)),
        "investing": float(by_act.get("investing", 0.0)), "financing": float(by_act.get("financing", 0.0)),
    }


def independent_cash_balance(ds: Dataset, date) -> float:
    """Cash computed straight from the source tables (does NOT use the cash ledger) - a cross-check."""
    t, d = ds.t, pd.Timestamp(date).normalize()
    pos = lambda df, col, amt: float(df.loc[df[col] <= d, amt].sum())  # noqa: E731
    cash = ds.opening["cash"]
    cash += pos(t["customer_receipts"], "receipt_date", "amount")
    cash -= pos(t["returns"], "return_date", "refund_amount")
    cash -= pos(t["supplier_payments"], "payment_date", "amount")
    cash -= pos(t["operating_expenses"], "expense_date", "amount")
    capex = t["capital_expenditures"]
    cash -= float(capex.loc[(capex["capex_date"] >= ds.start) & (capex["capex_date"] <= d), "amount"].sum())
    loans = t["loans"]
    cash += float(loans.loc[(loans["start_date"] >= ds.start) & (loans["start_date"] <= d), "principal"].sum())
    cash -= pos(t["loan_payments"], "payment_date", "interest_amount")
    cash -= pos(t["loan_payments"], "payment_date", "principal_amount")
    eq = t["equity_transactions"]
    e = eq[eq["txn_date"] <= d]
    cash += float(e.loc[e["txn_type"] == "owner_investment", "amount"].sum() - e.loc[e["txn_type"] == "owner_drawing", "amount"].sum())
    return round(float(cash), 2)


def balance_sheet(ds: Dataset, date=None) -> Dict:
    """Simplified balance sheet with an accounting-identity check (assets = liabilities + equity)."""
    d = (pd.Timestamp(date) if date is not None else ds.ref_date).normalize()
    d = min(max(d, ds.opening_date), ds.end)
    bal = ds.daily_balances.loc[d]
    capex = ds.t["capital_expenditures"]
    fixed = fixed_assets_net(capex, d.date())
    assets = float(bal["cash"] + bal["receivables"] + bal["inventory"] + fixed)
    liabilities = float(bal["payables"] + bal["loans"])
    # equity = opening equity + investments - drawings + retained profit since the opening date
    eq = ds.t["equity_transactions"]
    e = eq[eq["txn_date"] <= d]
    net_inv = float(e.loc[e["txn_type"] == "owner_investment", "amount"].sum() - e.loc[e["txn_type"] == "owner_drawing", "amount"].sum())
    retained = pnl(ds, ds.start, d)["profit_before_tax"] if d >= ds.start else 0.0
    equity = ds.opening["owner_equity"] + net_inv + retained
    return {
        "date": d, "cash": float(bal["cash"]), "receivables": float(bal["receivables"]), "inventory": float(bal["inventory"]),
        "fixed_assets_net": fixed, "total_assets": assets, "payables": float(bal["payables"]), "loans": float(bal["loans"]),
        "total_liabilities": liabilities, "opening_equity": ds.opening["owner_equity"], "owner_net_investment": net_inv,
        "retained_profit": float(retained), "total_equity": float(equity),
        "difference": round(assets - liabilities - equity, 2),
    }


# ---------------------------------------------------------------------------- budgets
def budget_vs_actual(ds: Dataset, start=None, end=None, by: str = "month", stores: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """Budget against actual for net sales, COGS, gross profit, operating expenses and operating profit.

    Head-office costs are budgeted under store 'HQ' and are unallocated here (``by='store'`` shows HQ separately).
    Variance sign convention: positive = favourable to profit.
    """
    start = ds.start if start is None else max(pd.Timestamp(start), ds.start)
    end = ds.end if end is None else min(pd.Timestamp(end), ds.end)
    b = ds.t["budgets"]
    b = b[(b["month"] >= start.to_period("M").start_time) & (b["month"] <= end.to_period("M").start_time)]
    if stores:
        b = b[b["store_id"].isin(list(stores) + ["HQ"])]
    pivot = b.pivot_table(index=["month", "store_id"], columns="line_item", values="amount", aggfunc="sum", fill_value=0.0).reset_index()
    for col in ["net_sales", "cogs", "operating_expenses", "depreciation"]:
        if col not in pivot:
            pivot[col] = 0.0
    act_rows = []
    ox = ds.t["operating_expenses"]
    for (month, store) in pivot[["month", "store_id"]].itertuples(index=False):
        ms, me = month, (month + pd.offsets.MonthEnd(0))
        if store == "HQ":
            net = cogs = 0.0
            opex = float(ox[(ox["store_id"].isna()) & between(ox["expense_date"], ms, me)]["amount"].sum())
            dep = ds.dep
            depv = float(dep[(dep["store_id"].isna()) & between(dep["dep_date"], ms, me)]["amount"].sum()) if not dep.empty else 0.0
        else:
            s = sales_totals(ds, ms, me, [store])
            net, cogs = s["net_sales"], s["cogs"]
            opex = float(ox[(ox["store_id"] == store) & between(ox["expense_date"], ms, me)]["amount"].sum())
            dep = ds.dep
            depv = float(dep[(dep["store_id"] == store) & between(dep["dep_date"], ms, me)]["amount"].sum()) if not dep.empty else 0.0
        act_rows.append((net, cogs, opex, depv))
    act = pd.DataFrame(act_rows, columns=["a_net_sales", "a_cogs", "a_operating_expenses", "a_depreciation"])
    df = pd.concat([pivot.reset_index(drop=True), act], axis=1)
    df["b_op_profit"] = df["net_sales"] - df["cogs"] - df["operating_expenses"] - df["depreciation"]
    df["a_op_profit"] = df["a_net_sales"] - df["a_cogs"] - df["a_operating_expenses"] - df["a_depreciation"]
    df = df.rename(columns={"net_sales": "b_net_sales", "cogs": "b_cogs", "operating_expenses": "b_operating_expenses",
                            "depreciation": "b_depreciation"})
    if by == "month":
        g = df.groupby("month", as_index=False).sum(numeric_only=True)
    elif by == "store":
        g = df.groupby("store_id", as_index=False).sum(numeric_only=True)
    else:
        g = df
    g["sales_variance"] = g["a_net_sales"] - g["b_net_sales"]
    g["cogs_variance"] = g["b_cogs"] - g["a_cogs"]
    g["opex_variance"] = g["b_operating_expenses"] - g["a_operating_expenses"]
    g["op_profit_variance"] = g["a_op_profit"] - g["b_op_profit"]
    g["sales_variance_pct"] = [safe_div(a, b) for a, b in zip(g["sales_variance"], g["b_net_sales"])]
    return g


# ---------------------------------------------------------------------------- profitability
def product_profitability(ds: Dataset, start, end, stores=None) -> pd.DataFrame:
    """Product-level profit after allocating variable selling costs.

    Rule: each store's variable operating costs (card fees, delivery, packaging) for the period are
    allocated to its products in proportion to net sales. Fixed costs are not allocated to products.
    """
    x = _daily_filter(ds, start, end, stores)
    g = x.groupby(["store_id", "product_id"], as_index=False).agg(
        units=("units", "sum"), ret_units=("ret_units", "sum"), gross=("gross", "sum"), discount=("discount", "sum"),
        net_after_disc=("net", "sum"), refund=("refund", "sum"), cogs=("cogs", "sum"), cogs_rev=("cogs_rev", "sum"))
    g["net_sales"] = g["net_after_disc"] - g["refund"]
    g["cogs_net"] = g["cogs"] - g["cogs_rev"]
    g["gross_profit"] = g["net_sales"] - g["cogs_net"]
    ox = ds.t["operating_expenses"]
    vx = ox[between(ox["expense_date"], start, end) & ox["category"].isin(VARIABLE_COST_CATEGORIES)]
    var_by_store = vx.groupby("store_id")["amount"].sum()
    store_net = g.groupby("store_id")["net_sales"].transform("sum")
    g["variable_costs"] = [float(var_by_store.get(s, 0.0)) * safe_div(n, tn, 0.0) for s, n, tn in zip(g["store_id"], g["net_sales"], store_net)]
    g["contribution"] = g["gross_profit"] - g["variable_costs"]
    g["product_name"] = g["product_id"].map(ds.product_names)
    g["category"] = g["product_id"].map(ds.product_category)
    g["gross_margin"] = [safe_div(a, b) for a, b in zip(g["gross_profit"], g["net_sales"])]
    g["contribution_margin"] = [safe_div(a, b) for a, b in zip(g["contribution"], g["net_sales"])]
    return g


def profitability_table(ds: Dataset, start, end, by: str = "product", stores=None) -> pd.DataFrame:
    """Aggregate ``product_profitability`` by product, category or store."""
    g = product_profitability(ds, start, end, stores)
    if by == "store":
        g["store"] = g["store_id"].map(ds.store_names)
        keys = ["store_id", "store"]
    elif by == "category":
        keys = ["category"]
    else:
        keys = ["product_id", "product_name", "category"]
    out = g.groupby(keys, as_index=False)[["units", "net_sales", "cogs_net", "gross_profit", "variable_costs", "contribution", "discount", "refund"]].sum()
    out["gross_margin"] = [safe_div(a, b) for a, b in zip(out["gross_profit"], out["net_sales"])]
    out["contribution_margin"] = [safe_div(a, b) for a, b in zip(out["contribution"], out["net_sales"])]
    return out.sort_values("gross_profit", ascending=False).reset_index(drop=True)


def store_profit_table(ds: Dataset, start, end, allocate: str = "net_sales") -> pd.DataFrame:
    """Operating profit by store with shared costs allocated, plus a reconciliation to the company total."""
    rows = []
    for sid in ds.store_ids:
        p = pnl(ds, start, end, [sid], allocate)
        rows.append({"store_id": sid, "store": ds.store_names[sid], "net_sales": p["net_sales"], "gross_profit": p["gross_profit"],
                     "gross_margin": p["gross_margin"], "operating_expenses": p["operating_expenses"],
                     "depreciation": p["depreciation"], "operating_profit": p["operating_profit"],
                     "operating_margin": p["operating_margin"], "hq_share": p["hq_allocation_share"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------- contribution & break-even
def contribution_breakeven(ds: Dataset, start, end, stores=None, allocate: str = "net_sales") -> Dict:
    """Contribution margin and break-even sales for a period.

    Variable costs = COGS + variable operating expenses (card fees, delivery, packaging).
    Fixed costs = all other operating expenses (incl. marketing, treated as a period cost) + depreciation.
    Break-even sales = fixed costs / contribution-margin ratio, on the period's own sales mix.
    """
    p = pnl(ds, start, end, stores, allocate)
    t = p["opex_table"]
    variable_opex = float(t.loc[t["category"].isin(VARIABLE_COST_CATEGORIES), "total"].sum())
    fixed_opex = p["operating_expenses"] - variable_opex
    contribution = p["net_sales"] - p["cogs"] - variable_opex
    cm_ratio = safe_div(contribution, p["net_sales"])
    fixed = fixed_opex + p["depreciation"]
    note = None
    if p["net_sales"] <= 0:
        be, note = None, "No sales in the period, so break-even cannot be calculated."
    elif cm_ratio is None or cm_ratio <= 0:
        be, note = None, "Contribution margin is zero or negative: more sales would not cover fixed costs."
    else:
        be = fixed / cm_ratio
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
    return {
        "net_sales": p["net_sales"], "cogs": p["cogs"], "variable_opex": variable_opex, "contribution": contribution,
        "contribution_margin_ratio": cm_ratio, "fixed_opex": fixed_opex, "depreciation": p["depreciation"], "fixed_costs": fixed,
        "break_even_sales": be, "margin_of_safety": safe_div(p["net_sales"] - be, p["net_sales"]) if be is not None else None,
        "break_even_per_day": safe_div(be, days) if be is not None else None, "days": days, "note": note,
    }
