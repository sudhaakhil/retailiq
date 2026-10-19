"""Business Action Center: traceable, rule-based findings.

Each rule is a small function with a stated trigger, the numbers that fired it, the period it refers to, a suggested
investigation, and its limitations. No external AI service is used - the same data always produces the same findings.
Rules that did not fire are returned too ("checked, nothing triggered") so the user can see what was examined.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import ANALYSIS_DEFAULTS
from .accounting import safe_div
from . import capital as K
from . import cashflow as CF
from . import customers as CU
from . import finance as F
from . import inventory as INV
from . import marketing as MK
from . import reviews as RV
from .dataset import Dataset

SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Info": 3}


@dataclass
class Settings:
    """Editable thresholds. Every rule quotes the thresholds it used."""
    min_cash_reserve: float = ANALYSIS_DEFAULTS.minimum_cash_reserve
    stress_sales_drop: float = 0.20
    stress_collection_delay: int = 15
    slow_stock_value_threshold: float = 50_000.0
    strong_roas: float = ANALYSIS_DEFAULTS.strong_roas
    weak_contribution_per_rupee: float = 0.10
    sales_growth_min: float = 0.0
    budget_overrun_pct: float = 0.10
    supplier_on_time_min: float = 0.80
    overdue_receivable_threshold: float = 50_000.0
    at_risk_days: int = ANALYSIS_DEFAULTS.at_risk_days
    inactive_days: int = ANALYSIS_DEFAULTS.inactive_days
    negative_review_min_count: int = ANALYSIS_DEFAULTS.negative_review_min_count
    cover_days: int = 40
    profitable_margin_min: float = 0.25


@dataclass
class Finding:
    rule_id: str
    area: str
    severity: str
    title: str
    rule: str
    numbers: Dict[str, str]
    reference: str
    action: str
    assumptions: str
    page: str
    triggered: bool = True
    detail: Optional[pd.DataFrame] = field(default=None, repr=False)


def _inr(x: float) -> str:
    return f"₹{x:,.0f}"


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.1%}"


# ---------------------------------------------------------------------------- rules
def rule_cash_reserve(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    res = ctx["forecast"]
    sm = res["summary"]
    reserve = s.min_cash_reserve
    breach = sm["lowest_cash"] < reserve
    nums = {"Lowest projected cash": _inr(sm["lowest_cash"]), "Date of lowest point": f"{sm['lowest_cash_date']:%d %b %Y}",
            "Minimum reserve": _inr(reserve), "Headroom (+) / shortfall (-)": _inr(sm["lowest_cash"] - reserve),
            "Days below reserve": str(int((res["daily"]["closing_cash"] < reserve).sum()))}
    return Finding(
        "R01", "Cash", "High" if breach else "Info",
        "Projected cash falls below the minimum reserve" if breach else "Projected cash stays above the minimum reserve",
        f"Fires when the lowest projected daily cash in the next {res['assumptions'].weeks} weeks is below the minimum reserve.",
        nums, f"{res['daily']['date'].iloc[0]:%d %b %Y} to {res['daily']['date'].iloc[-1]:%d %b %Y}",
        "Compare funding options on the Cash forecast & funding page; consider delaying planned capital spending or purchases."
        if breach else "No action needed now; re-check after changing planned spending.",
        "Base forecast: seasonal-naive sales (same weekday last year x growth), planned items from the database, no stress applied.",
        "cash_funding.py", triggered=breach, detail=res["weekly"][["week", "week_start", "week_end", "closing_cash", "lowest_daily_cash"]])


def rule_cash_stress(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    a = CF.CashAssumptions(sales_adjustment=-s.stress_sales_drop, collection_delay_days=s.stress_collection_delay,
                           min_cash_reserve=s.min_cash_reserve, cover_days=s.cover_days)
    res = CF.forecast_cash(ds, a)
    sm = res["summary"]
    breach = sm["lowest_cash"] < s.min_cash_reserve
    nums = {"Stress: sales vs baseline": f"-{s.stress_sales_drop:.0%}", "Stress: credit customers pay later by": f"{s.stress_collection_delay} days",
            "Lowest projected cash (stress)": _inr(sm["lowest_cash"]), "Minimum reserve": _inr(s.min_cash_reserve),
            "Headroom (+) / shortfall (-)": _inr(sm["lowest_cash"] - s.min_cash_reserve)}
    return Finding(
        "R02", "Cash", "Medium" if breach else "Info",
        "Cash falls below the reserve under a downside scenario" if breach else "Cash stays above the reserve even in the downside scenario",
        "Fires when the stress scenario (lower sales, slower credit collections) pushes projected cash below the reserve.",
        nums, f"{res['daily']['date'].iloc[0]:%d %b %Y} to {res['daily']['date'].iloc[-1]:%d %b %Y}",
        "Agree a standby facility or contingency (e.g. deferral of planned capex) before the downside could occur." if breach
        else "Keep monitoring; repeat the test whenever large commitments are added.",
        "Stress inputs are illustrative, editable assumptions - not predictions. Inventory purchases adjust automatically to lower demand.",
        "cash_funding.py", triggered=breach)


def rule_stockout_profitable(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    plan = ctx["plan"].copy()
    plan["margin_pct"] = plan["unit_margin"] / (plan["unit_margin"] + plan["unit_cost"])
    plan["profit_at_risk"] = plan["daily_demand"] * plan["unit_margin"].clip(lower=0) * plan["shortfall_days"]
    selling = plan["daily_demand"] > 0
    runs_short = (plan["days_of_stock"].fillna(0) < plan["lead_time_days"]) & (plan["on_hand"] + plan["on_order"] < plan["reorder_point"])
    stocked_out = plan["on_hand"] == 0
    risky = plan[selling & (runs_short | stocked_out) & (plan["margin_pct"] >= s.profitable_margin_min)]
    risky = risky.sort_values(["on_hand", "profit_at_risk"], ascending=[True, False])
    fired = len(risky) > 0
    top = risky.head(3)
    nums = {"Lines at risk": str(len(risky)),
            "Largest": "; ".join(f"{r.store_id} {r.product_name} ({r.on_hand} on hand, {r.on_order} on order, {r.days_of_stock:.0f} days of stock vs "
                                 f"{r.lead_time_days:.0f}-day lead time)" for r in top.itertuples()) or "-",
            "Estimated profit at risk (all lines)": _inr(float(risky["profit_at_risk"].sum())) if fired else "₹0"}
    stocked_now = int((risky["on_hand"] == 0).sum()) if fired else 0
    return Finding(
        "R03", "Inventory", "High" if stocked_now else ("Medium" if fired else "Info"),
        "Profitable products are out of stock or may run out before the next delivery" if fired
        else "No profitable product is out of stock or expected to run out before replenishment",
        f"Fires when a product with gross margin ≥ {s.profitable_margin_min:.0%} and recent sales is out of stock, or its stock covers fewer days than "
        "the supplier lead time and stock on order does not lift the position above the reorder point.",
        nums, f"Stock position at {ds.ref_date:%d %b %Y}",
        "Check open purchase orders and expedite or split orders for these lines; see the Inventory page for suggested quantities.",
        "Demand is a trailing/seasonal average and lead times are observed averages; real deliveries can be late (see supplier reliability).",
        "inventory.py", triggered=fired,
        detail=risky[["store_id", "product_id", "product_name", "on_hand", "on_order", "daily_demand", "days_of_stock", "lead_time_days", "reorder_point",
                      "margin_pct", "profit_at_risk"]] if fired else None)


def rule_slow_stock(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    plan = ctx["plan"]
    slow = plan[plan["movement"].isin(["Slow-moving", "No sales"]) & (plan["inventory_value"] > 0)]
    value = float(slow["inventory_value"].sum())
    total = float(plan["inventory_value"].sum())
    fired = value >= s.slow_stock_value_threshold
    nums = {"Slow-moving stock value": _inr(value), "Share of inventory": _pct(safe_div(value, total)),
            "Threshold": _inr(s.slow_stock_value_threshold), "Lines": ", ".join(f"{r.store_id} {r.product_name}" for r in slow.itertuples()) or "-"}
    return Finding(
        "R04", "Inventory", "Medium" if fired else "Info",
        "Slow-moving inventory ties up capital" if fired else "Slow-moving inventory is below the threshold",
        f"Fires when stock with more than the slow-moving days-of-stock limit (or no recent sales) is worth at least {_inr(s.slow_stock_value_threshold)}.",
        nums, f"Stock position at {ds.ref_date:%d %b %Y}",
        "Consider clearance pricing, transfers between stores, or pausing replenishment for these lines.",
        "Days of stock uses the trailing 90-day average demand; seasonal items can look slow out of season.",
        "inventory.py", triggered=fired, detail=slow[["store_id", "product_id", "product_name", "on_hand", "days_of_stock", "inventory_value", "movement"]])


def rule_sales_up_profit_down(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    end = ds.end.to_period("M").end_time.normalize()
    if ds.end != end:
        end = (ds.end.to_period("M") - 1).end_time.normalize()
    start = (end.to_period("M") - 2).start_time
    if start - pd.DateOffset(years=1) >= ds.start:
        ps, pe = F.year_ago(start, end)
        label = "same three months a year earlier"
    else:
        ps, pe = F.previous_period(start, end)
        label = "previous three months"
    cur, prev = F.pnl(ds, start, end), F.pnl(ds, ps, pe)
    sg = F.pct_change(cur["net_sales"], prev["net_sales"])
    fired = sg is not None and sg > s.sales_growth_min and cur["operating_profit"] < prev["operating_profit"]
    nums = {"Net sales": f"{_inr(cur['net_sales'])} vs {_inr(prev['net_sales'])} ({_pct(sg)})",
            "Operating profit": f"{_inr(cur['operating_profit'])} vs {_inr(prev['operating_profit'])}",
            "Operating margin": f"{_pct(cur['operating_margin'])} vs {_pct(prev['operating_margin'])}",
            "Gross margin": f"{_pct(cur['gross_margin'])} vs {_pct(prev['gross_margin'])}",
            "Operating expenses": f"{_inr(cur['operating_expenses'])} vs {_inr(prev['operating_expenses'])}"}
    return Finding(
        "R05", "Profitability", "High" if fired else "Info",
        "Sales are growing but operating profit is declining" if fired else "Operating profit is keeping pace with sales growth",
        "Fires when net sales are higher than the comparison period but operating profit is lower.",
        nums, f"{start:%b %Y}-{end:%b %Y} vs {label}",
        "Check the Finance page: look at gross-margin changes (discounts, supplier price rises) and cost growth (wages, rent, marketing).",
        "Three-month windows are short and seasonal; overheads are not allocated differently between periods.",
        "finance.py", triggered=fired)


def rule_campaign_weak_contribution(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    perf = ctx["campaigns"]
    weak = perf[(perf["roas"].fillna(0) >= s.strong_roas) & (perf["contribution_per_rupee_spent"].fillna(0) < s.weak_contribution_per_rupee)]
    fired = len(weak) > 0
    nums = {"Campaigns": "; ".join(f"{r.campaign_name}: ROAS {r.roas:.1f}, contribution {_inr(r.contribution)}" for r in weak.itertuples()) or "-",
            "Rule": f"ROAS ≥ {s.strong_roas:.1f} but contribution per rupee spent < {s.weak_contribution_per_rupee:.2f}"}
    return Finding(
        "R06", "Marketing", "Medium" if fired else "Info",
        "Campaigns with strong ROAS but weak contribution" if fired else "No campaign combines strong ROAS with weak contribution",
        "ROAS divides revenue by spend and ignores product cost, discounts and selling costs. Fires when ROAS is high yet profit after those costs is thin.",
        nums, f"Campaigns running {ds.start:%b %Y}-{ds.end:%b %Y}",
        "Review discount depth and product mix of these campaigns; steep discounts on low-margin goods can inflate revenue while destroying margin.",
        "Attribution is redemption-based and not causal; contribution uses an estimated variable-cost ratio.",
        "marketing.py", triggered=fired,
        detail=weak[["campaign_id", "campaign_name", "channel", "spend", "attributed_revenue", "roas", "discount_rate", "contribution"]] if fired else None)


def rule_quality_reviews(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    rep = RV.repeated_complaints(ds, ctx["reviews"], min_count=s.negative_review_min_count)
    q = rep[rep["theme"] == "quality"]
    fired = len(q) > 0
    nums = {"Products": "; ".join(f"{r.product_name}: {r.negative_mentions} negative quality reviews ({r.share_of_product_reviews:.0%} of its recent reviews)" for r in q.itertuples()) or "-",
            "Rule": f"≥ {s.negative_review_min_count} negative reviews on a quality theme in the last 120 days and ≥ 25% of the product's reviews"}
    return Finding(
        "R07", "Reviews", "High" if fired else "Info",
        "Product with repeated negative quality reviews" if fired else "No product has repeated negative quality reviews",
        "Fires when a product has repeated negative reviews mentioning quality themes within the recent window.",
        nums, f"120 days to {ds.ref_date:%d %b %Y}",
        "Inspect returned units and the latest supplier batch, hold new purchase orders until resolved, and raise a claim with the supplier if confirmed.",
        "Review text is simulated and theme detection is keyword-based; read the example reviews before acting.",
        "reviews.py", triggered=fired, detail=q)


def rule_falling_ratings(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    dec = RV.declining_products(ds, ctx["reviews"])
    flagged = dec[dec["flag"]]
    fired = len(flagged) > 0
    nums = {"Products": "; ".join(f"{r.product_name}: {r.prior_avg:.2f} → {r.recent_avg:.2f}" for r in flagged.itertuples()) or "-",
            "Rule": "Average rating fell ≥ 0.4 stars (last 90 days vs the prior 180 days) with ≥ 8 reviews in each window"}
    return Finding(
        "R08", "Reviews", "Medium" if fired else "Info", "Products with falling ratings" if fired else "No product shows a notable rating decline",
        "Fires when the recent average rating is at least 0.4 below the earlier average with enough reviews in both windows.",
        nums, f"Last 90 days to {ds.ref_date:%d %b %Y}",
        "Read the recent reviews on the Reviews page; check for stock-outs, late deliveries or quality problems behind the decline.",
        "Small samples fluctuate; a single window comparison is not proof of a trend.",
        "reviews.py", triggered=fired, detail=flagged)


def rule_supplier_reliability(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    lt = INV.supplier_lead_times(ds)
    poor = lt[(lt["orders"] >= 10) & (lt["on_time_share"] < s.supplier_on_time_min)]
    fired = len(poor) > 0
    nums = {"Suppliers": "; ".join(f"{r.supplier_name}: {r.on_time_share:.0%} on time, mean {r.mean_lead_days:.1f} vs {r.quoted_lead_days} quoted days" for r in poor.itertuples()) or "-",
            "Rule": f"On-time share below {s.supplier_on_time_min:.0%} over the last 12 months (≥ 10 orders)"}
    return Finding(
        "R09", "Procurement", "Medium" if fired else "Info", "Unreliable supplier deliveries" if fired else "Suppliers deliver on time",
        "Fires when the share of purchase orders received by the expected date is below the threshold.",
        nums, f"12 months to {ds.ref_date:%d %b %Y}",
        "Raise safety stock for affected products (the reorder logic already uses observed lead times) and discuss delivery penalties or alternative suppliers.",
        "Based on received orders only; open orders that are already late are not yet counted.",
        "inventory.py", triggered=fired, detail=poor[["supplier_id", "supplier_name", "orders", "quoted_lead_days", "mean_lead_days", "on_time_share"]])


def rule_overdue_receivables(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    ag = K.receivable_aging(ds)
    overdue = float(ag.loc[ag["aging_bucket"].isin(["1-30 days overdue", "31-60 days overdue", "Over 60 days overdue"]), "amount"].sum())
    fired = overdue >= s.overdue_receivable_threshold
    nums = {"Overdue customer invoices": _inr(overdue), "Threshold": _inr(s.overdue_receivable_threshold),
            "Total receivables": _inr(float(ds.daily_balances["receivables"].loc[ds.ref_date]))}
    return Finding(
        "R10", "Working capital", "Low" if fired else "Info", "Customer invoices are overdue" if fired else "Overdue receivables are below the threshold",
        f"Fires when overdue business-credit invoices total at least {_inr(s.overdue_receivable_threshold)}.", nums, f"At {ds.ref_date:%d %b %Y}",
        "Send reminders to business customers with overdue invoices and review credit terms for repeat late payers.",
        "Overdue is measured against the invoice due date; card/UPI settlement in transit is not treated as overdue.",
        "capital.py", triggered=fired, detail=ag)


def rule_inactive_customers(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    rfm = ctx["rfm"]
    at_risk = rfm[(rfm["status"] == "Becoming inactive") & (rfm["frequency"] >= 2)]
    spend = float(at_risk["monetary"].sum())
    fired = len(at_risk) >= 10
    nums = {"Customers becoming inactive (repeat buyers)": str(len(at_risk)), "Their spend in the last 12 months": _inr(spend),
            "Rule": f"{s.at_risk_days}-{s.inactive_days} days since last purchase and ≥ 2 orders in the last 12 months"}
    return Finding(
        "R11", "Customers", "Low" if fired else "Info", "Valuable customers are becoming inactive" if fired else "Few repeat customers are becoming inactive",
        "Fires when at least 10 repeat customers have gone quiet for longer than the at-risk limit but not yet the inactive limit.",
        nums, f"At {ds.ref_date:%d %b %Y}", "Run a targeted win-back message (e.g. the loyalty SMS campaign) and track redemptions separately.",
        "Purchase gaps vary by customer; the fixed day limits are a simple rule, not a churn prediction.", "customers.py", triggered=fired,
        detail=at_risk[["customer_id", "customer_name", "recency_days", "frequency", "monetary", "segment"]])


def rule_budget_variance(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    end = ds.end.to_period("M").start_time
    start = (end.to_period("M") - 2).start_time
    b = F.budget_vs_actual(ds, start, ds.end, by="month")
    opex_b, opex_a = float(b["b_operating_expenses"].sum()), float(b["a_operating_expenses"].sum())
    sales_b, sales_a = float(b["b_net_sales"].sum()), float(b["a_net_sales"].sum())
    over = safe_div(opex_a - opex_b, opex_b)
    fired = over is not None and over > s.budget_overrun_pct
    nums = {"Operating expenses (actual vs budget)": f"{_inr(opex_a)} vs {_inr(opex_b)} ({_pct(over)})",
            "Net sales (actual vs budget)": f"{_inr(sales_a)} vs {_inr(sales_b)} ({_pct(safe_div(sales_a - sales_b, sales_b))})"}
    return Finding(
        "R12", "Profitability", "Medium" if fired else "Info", "Operating expenses are over budget" if fired else "Operating expenses are within budget tolerance",
        f"Fires when operating expenses for the latest three months exceed budget by more than {s.budget_overrun_pct:.0%}.", nums,
        f"{start:%b %Y}-{ds.end:%b %Y}", "Open Finance > Budget vs actual to see which months and stores drive the overrun.",
        "Budgets are simulated planning figures.", "finance.py", triggered=fired)


def rule_data_integrity(ds: Dataset, s: Settings, ctx: Dict) -> Finding:
    bs = F.balance_sheet(ds, ds.ref_date)
    cash_diff = abs(F.cash_balance_at(ds, ds.ref_date) - F.independent_cash_balance(ds, ds.ref_date))
    inv = INV.reconcile_movements(ds)
    inv_diff = float(inv["value_diff"].abs().max()) if len(inv) else 0.0
    fired = abs(bs["difference"]) > 1.0 or cash_diff > 1.0 or inv_diff > 1.0
    nums = {"Balance sheet difference": _inr(bs["difference"]), "Cash ledger vs source tables": _inr(cash_diff),
            "Largest inventory movement-vs-balance difference": _inr(inv_diff)}
    return Finding(
        "R13", "Data quality", "High" if fired else "Info", "Reconciliation failure" if fired else "All reconciliation checks pass",
        "Fires when the balance sheet does not balance, ledger cash differs from cash rebuilt from source tables, or stock movements do not match balances (> ₹1).",
        nums, f"At {ds.ref_date:%d %b %Y}", "Do not rely on reports until the difference is explained.", "Tolerance is ₹1 to allow for rounding.", "data_definitions.py", triggered=fired)


RULES: List[Callable[[Dataset, Settings, Dict], Finding]] = [
    rule_cash_reserve, rule_cash_stress, rule_stockout_profitable, rule_slow_stock, rule_sales_up_profit_down, rule_campaign_weak_contribution,
    rule_quality_reviews, rule_falling_ratings, rule_supplier_reliability, rule_overdue_receivables, rule_inactive_customers,
    rule_budget_variance, rule_data_integrity,
]


def run_all(ds: Dataset, settings: Optional[Settings] = None) -> List[Finding]:
    """Evaluate every rule against the dataset at its reference date and return findings sorted by severity."""
    s = settings or Settings()
    ref = ds.ref_date
    ctx = {
        "forecast": CF.forecast_cash(ds, CF.CashAssumptions(ref_date=ref, min_cash_reserve=s.min_cash_reserve, cover_days=s.cover_days)),
        "plan": INV.replenishment_plan(ds, ref, demand_method="seasonal" if (ref - pd.Timedelta(days=500)) >= ds.start else "trailing", cover_days=s.cover_days),
        "campaigns": MK.campaign_performance(ds),
        "reviews": RV.analysed_reviews(ds),
        "rfm": CU.rfm_table(ds, at_risk_days=s.at_risk_days, inactive_days=s.inactive_days),
    }
    out = []
    for rule in RULES:
        try:
            out.append(rule(ds, s, ctx))
        except Exception as exc:  # a failing rule must never hide the others
            out.append(Finding(rule.__name__, "System", "Info", f"Rule could not run: {rule.__name__}", "n/a", {"Error": str(exc)}, "n/a",
                               "Report this to the developer.", "n/a", "data_definitions.py", triggered=False))
    out.sort(key=lambda f: (not f.triggered, SEVERITY_ORDER[f.severity], f.rule_id))
    return out
