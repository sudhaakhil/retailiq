"""Shared accounting helpers used by both the data generator and the analytics.

Keeping these in one place guarantees that the simulated ledger and the reports
apply exactly the same rules (depreciation, loan amortisation, safe division).
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
from typing import Optional

import pandas as pd


def safe_div(numerator: float, denominator: float, default: Optional[float] = None) -> Optional[float]:
    """Divide, returning ``default`` (None) when the denominator is zero, missing or not finite."""
    try:
        if denominator is None or numerator is None:
            return default
        if pd.isna(denominator) or pd.isna(numerator) or denominator == 0:
            return default
        result = numerator / denominator
        return result if math.isfinite(result) else default
    except (TypeError, ZeroDivisionError):
        return default


def month_end(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def add_months(d: dt.date, months: int, day: Optional[int] = None) -> dt.date:
    """Add calendar months, clamping the day to the month length (e.g. 31 Jan + 1 -> 28/29 Feb)."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    target_day = day if day is not None else d.day
    return dt.date(y, m, min(target_day, calendar.monthrange(y, m)[1]))


# --------------------------------------------------------------------------- depreciation
def depreciation_schedule(capex: pd.DataFrame, until: dt.date) -> pd.DataFrame:
    """Straight-line monthly depreciation for every asset up to ``until``.

    Rule (documented in docs/metrics_and_assumptions.md):
    * depreciation starts in the calendar month AFTER the purchase month;
    * one charge of cost / useful_life_months is booked on each month-end date;
    * the final charge absorbs rounding so total depreciation equals cost.
    Returns columns: capex_id, store_id, dep_date (Timestamp), amount.
    """
    rows = []
    for asset in capex.itertuples(index=False):
        purchase = pd.Timestamp(asset.capex_date).date()
        life = int(asset.useful_life_months)
        cost = float(asset.amount)
        monthly = round(cost / life, 2) if life > 0 else cost
        booked = 0.0
        for k in range(1, life + 1):
            dep_date = month_end(add_months(purchase.replace(day=1), k))
            if dep_date > until:
                break
            amount = round(cost - booked, 2) if k == life else monthly
            booked = round(booked + amount, 2)
            rows.append((asset.capex_id, asset.store_id, pd.Timestamp(dep_date), amount))
    return pd.DataFrame(rows, columns=["capex_id", "store_id", "dep_date", "amount"])


def fixed_assets_net(capex: pd.DataFrame, as_of: dt.date) -> float:
    """Net book value (cost less accumulated depreciation) at the close of ``as_of``."""
    as_of_ts = pd.Timestamp(as_of)
    cost = capex.loc[pd.to_datetime(capex["capex_date"]) <= as_of_ts, "amount"].sum()
    dep = depreciation_schedule(capex, as_of)
    return round(float(cost) - float(dep["amount"].sum() if not dep.empty else 0.0), 2)


# --------------------------------------------------------------------------- loans
def instalment_amount(principal: float, annual_rate: float, term_months: int) -> float:
    """Equal monthly instalment (EMI). Zero-interest loans repay principal evenly."""
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    if principal < 0:
        raise ValueError("principal cannot be negative")
    if annual_rate < 0:
        raise ValueError("annual_rate cannot be negative")
    r = annual_rate / 12.0
    if r == 0:
        return principal / term_months
    factor = (1 + r) ** term_months
    return principal * r * factor / (factor - 1)


def amortization_schedule(
    principal: float,
    annual_rate: float,
    term_months: int,
    start_date: dt.date,
    payment_day: Optional[int] = None,
    upfront_fee_pct: float = 0.0,
) -> pd.DataFrame:
    """Full repayment schedule for an equal-instalment term loan.

    Interest for each month = opening balance x annual_rate / 12 (rounded to paise).
    Principal = instalment - interest; the last instalment clears the remaining balance.
    The first instalment falls one month after ``start_date``.
    """
    payment_day = payment_day or start_date.day
    emi = round(instalment_amount(principal, annual_rate, term_months), 2)
    r = annual_rate / 12.0
    balance = round(float(principal), 2)
    rows = []
    for k in range(1, term_months + 1):
        pay_date = add_months(start_date, k, payment_day)
        interest = round(balance * r, 2)
        principal_part = round(emi - interest, 2)
        if k == term_months or principal_part > balance:
            principal_part = balance
        payment = round(interest + principal_part, 2)
        opening = balance
        balance = round(balance - principal_part, 2)
        rows.append({
            "instalment_no": k,
            "payment_date": pd.Timestamp(pay_date),
            "opening_balance": opening,
            "payment": payment,
            "interest": interest,
            "principal": principal_part,
            "closing_balance": balance,
        })
    df = pd.DataFrame(rows)
    df.attrs["upfront_fee"] = round(principal * upfront_fee_pct, 2)
    df.attrs["instalment"] = emi
    return df


def loan_balance_at(schedule: pd.DataFrame, principal: float, as_of: dt.date) -> float:
    """Outstanding principal after all instalments dated on or before ``as_of``."""
    paid = schedule.loc[schedule["payment_date"] <= pd.Timestamp(as_of), "principal"].sum()
    return round(principal - float(paid), 2)
