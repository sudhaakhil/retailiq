"""Funding comparisons built on top of the 13-week cash forecast.

Every rate, fee, date and amount is a user-entered assumption - nothing here describes a real lender's offer.
Principal and interest are always kept separate: borrowing is a financing inflow (never revenue), and
repaying principal is a financing outflow (never an operating expense).

The comparison shows consequences under assumptions; it does NOT declare a best option, because the right
choice also depends on risk appetite, security demanded by lenders, control and tax, none of which is modelled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .accounting import add_months, amortization_schedule, safe_div


# ---------------------------------------------------------------------------- term loan
def term_loan_schedule(principal: float, annual_rate: float, term_months: int, start_date, grace_months: int = 0,
                       upfront_fee_pct: float = 0.0, payment_day: Optional[int] = None) -> pd.DataFrame:
    """Repayment schedule for a term loan, with optional interest-only grace months.

    * Interest each month = opening balance x annual_rate / 12.
    * During grace months only interest is paid; afterwards equal instalments clear the loan.
    * Zero-interest loans are supported (principal repaid evenly, no interest).
    """
    if principal < 0 or annual_rate < 0 or upfront_fee_pct < 0:
        raise ValueError("Principal, interest rate and fees cannot be negative.")
    if int(term_months) != term_months or term_months < 1:
        raise ValueError("Term must be a whole number of months, at least 1.")
    if not 0 <= grace_months < term_months:
        raise ValueError("Grace months must be between 0 and (term - 1).")
    start = pd.Timestamp(start_date).date()
    payment_day = payment_day or start.day
    rows = []
    r = annual_rate / 12.0
    for k in range(1, int(grace_months) + 1):
        rows.append({"instalment_no": k, "payment_date": pd.Timestamp(add_months(start, k, payment_day)),
                     "opening_balance": principal, "payment": round(principal * r, 2), "interest": round(principal * r, 2),
                     "principal": 0.0, "closing_balance": principal})
    rest = int(term_months - grace_months)
    if principal > 0:
        amort = amortization_schedule(principal, annual_rate, rest, add_months(start, int(grace_months), payment_day), payment_day)
        amort["instalment_no"] += int(grace_months)
        rows.extend(amort.to_dict("records"))
    df = pd.DataFrame(rows, columns=["instalment_no", "payment_date", "opening_balance", "payment", "interest", "principal", "closing_balance"])
    df.attrs["upfront_fee"] = round(principal * upfront_fee_pct, 2)
    return df


def effective_annual_cost(net_proceeds: float, payments: List[float]) -> Optional[float]:
    """Annualised internal rate of return of the borrowing (monthly cash flows), or None if not defined."""
    if net_proceeds <= 0 or not payments or sum(payments) <= 0:
        return None
    flows = np.array([net_proceeds] + [-p for p in payments], dtype=float)

    def npv(rate):
        return float((flows / (1 + rate) ** np.arange(len(flows))).sum())

    lo, hi = -0.5, 5.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            break
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    monthly = (lo + hi) / 2
    annual = (1 + monthly) ** 12 - 1
    return 0.0 if abs(annual) < 1e-7 else annual


# ---------------------------------------------------------------------------- equity
def equity_dilution(investment: float, pre_money_valuation: float, existing_owner_pct: float = 1.0) -> Dict:
    """Simple ownership dilution: new investors own investment / (pre-money + investment)."""
    if investment < 0:
        raise ValueError("Investment cannot be negative.")
    if pre_money_valuation <= 0:
        raise ValueError("Pre-money valuation must be positive for a dilution estimate.")
    if not 0 < existing_owner_pct <= 1:
        raise ValueError("Existing ownership must be between 0 and 100%.")
    post = pre_money_valuation + investment
    new_pct = investment / post
    return {"post_money_valuation": post, "new_investor_pct": new_pct,
            "existing_owner_pct_after": existing_owner_pct * (1 - new_pct), "dilution_points": existing_owner_pct * new_pct,
            "note": "Simple pre-money/post-money arithmetic; ignores option pools, preferences and fees."}


# ---------------------------------------------------------------------------- overlays on the forecast
@dataclass
class OptionResult:
    name: str
    daily: pd.DataFrame          # date, closing_cash, financing_flow, debt_balance
    interest_in_horizon: float
    fees_in_horizon: float
    principal_repaid_in_horizon: float
    debt_end: float
    life_interest: Optional[float] = None
    life_fees: Optional[float] = None
    effective_rate: Optional[float] = None
    schedule: Optional[pd.DataFrame] = None
    extra: Optional[Dict] = None
    notes: Optional[List[str]] = None


def _path(base_daily: pd.DataFrame, flows: np.ndarray) -> np.ndarray:
    opening = float(base_daily["closing_cash"].iloc[0] - base_daily["net_cash_flow"].iloc[0])
    return opening + np.cumsum(base_daily["net_cash_flow"].to_numpy() + flows)


def baseline_option(base_daily: pd.DataFrame, name: str = "No funding action") -> OptionResult:
    flows = np.zeros(len(base_daily))
    return OptionResult(name, pd.DataFrame({"date": base_daily["date"], "closing_cash": _path(base_daily, flows),
                                            "financing_flow": flows, "debt_balance": 0.0}), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def owner_capital_option(base_daily: pd.DataFrame, amount: float, funding_date, pre_money: Optional[float] = None) -> OptionResult:
    if amount < 0:
        raise ValueError("Owner capital cannot be negative.")
    flows = np.zeros(len(base_daily))
    dates = pd.DatetimeIndex(base_daily["date"])
    fd = pd.Timestamp(funding_date)
    if fd in dates:
        flows[dates.get_loc(fd)] = amount
    note = [] if fd in dates else ["Funding date is outside the forecast horizon, so the money does not arrive within these weeks."]
    extra = equity_dilution(amount, pre_money) if pre_money else None
    return OptionResult("Owner capital injection", pd.DataFrame({"date": dates, "closing_cash": _path(base_daily, flows), "financing_flow": flows,
                                                                 "debt_balance": 0.0}), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None, None, extra, note)


def term_loan_option(base_daily: pd.DataFrame, principal: float, annual_rate: float, term_months: int, funding_date,
                     upfront_fee_pct: float = 0.0, grace_months: int = 0) -> OptionResult:
    sched = term_loan_schedule(principal, annual_rate, term_months, funding_date, grace_months, upfront_fee_pct)
    fee = sched.attrs["upfront_fee"]
    dates = pd.DatetimeIndex(base_daily["date"])
    flows = np.zeros(len(dates))
    debt = np.zeros(len(dates))
    fd = pd.Timestamp(funding_date)
    notes = []
    if fd in dates:
        flows[dates.get_loc(fd)] += principal - fee
    else:
        notes.append("Funding date is outside the forecast horizon, so the loan does not provide cash within these weeks.")
    interest_h = principal_h = 0.0
    for r in sched.itertuples(index=False):
        if r.payment_date in dates:
            i = dates.get_loc(r.payment_date)
            flows[i] -= r.payment
            interest_h += r.interest
            principal_h += r.principal
    # debt balance: outstanding principal from the funding date, reduced by each repayment
    for i, d in enumerate(dates):
        if d < fd:
            continue
        paid = sched.loc[sched["payment_date"] <= d, "principal"].sum()
        debt[i] = max(principal - paid, 0.0)
    eff = effective_annual_cost(principal - fee, list(sched["payment"]))
    return OptionResult("Term loan", pd.DataFrame({"date": dates, "closing_cash": _path(base_daily, flows), "financing_flow": flows, "debt_balance": debt}),
                        interest_h, fee if fd in dates else 0.0, principal_h, float(debt[-1]), float(sched["interest"].sum()), fee, eff, sched, None, notes)


def revolver_option(base_daily: pd.DataFrame, limit: float, annual_rate: float, reserve: float, start_date=None,
                    upfront_fee_pct: float = 0.0, undrawn_fee_pct: float = 0.0, sweep_buffer: float = 0.0) -> OptionResult:
    """Revolving credit facility simulated day by day.

    Rule: if cash would close below the reserve, draw just enough (up to the unused limit); if cash exceeds the reserve
    plus ``sweep_buffer``, repay as much drawn balance as possible. Interest accrues daily on the drawn balance
    and the commitment fee on the undrawn balance (annual %, /365); both are paid at each month end and on the last day.
    """
    if limit < 0 or annual_rate < 0 or upfront_fee_pct < 0 or undrawn_fee_pct < 0:
        raise ValueError("Limit, rate and fees cannot be negative.")
    dates = pd.DatetimeIndex(base_daily["date"])
    net = base_daily["net_cash_flow"].to_numpy()
    opening = float(base_daily["closing_cash"].iloc[0] - net[0])
    start = pd.Timestamp(start_date) if start_date is not None else dates[0]
    cash, drawn = opening, 0.0
    accrued_int = accrued_fee = 0.0
    total_int = total_fee = 0.0
    out_cash, out_drawn, flows, peak = [], [], [], 0.0
    fee_up = limit * upfront_fee_pct
    for i, d in enumerate(dates):
        flow_today = net[i]
        fin_flow = 0.0
        if d == start and fee_up:
            flow_today -= fee_up
            total_fee += fee_up
            fin_flow -= fee_up
        active = d >= start
        if active:
            accrued_int += drawn * annual_rate / 365.0
            accrued_fee += max(limit - drawn, 0.0) * undrawn_fee_pct / 365.0
        month_end_day = (d == d + pd.offsets.MonthEnd(0)) or i == len(dates) - 1
        if active and month_end_day and (accrued_int or accrued_fee):
            pay = accrued_int + accrued_fee
            flow_today -= pay
            fin_flow -= pay
            total_int += accrued_int
            total_fee += accrued_fee
            accrued_int = accrued_fee = 0.0
        cash += flow_today
        if active:
            if cash < reserve and drawn < limit:
                draw = min(reserve - cash, limit - drawn)
                cash += draw
                drawn += draw
                fin_flow += draw
            elif drawn > 0 and cash > reserve + sweep_buffer:
                repay = min(drawn, cash - reserve - sweep_buffer)
                cash -= repay
                drawn -= repay
                fin_flow -= repay
        peak = max(peak, drawn)
        out_cash.append(cash)
        out_drawn.append(drawn)
        flows.append(fin_flow)
    notes = []
    if peak >= limit - 1e-6 and limit > 0:
        notes.append("The facility limit was fully used - cash may still fall below the reserve.")
    daily = pd.DataFrame({"date": dates, "closing_cash": out_cash, "financing_flow": flows, "debt_balance": out_drawn})
    return OptionResult("Revolving credit facility", daily, total_int, total_fee, 0.0, float(out_drawn[-1]), None, None, None, None,
                        {"peak_drawn": peak, "limit": limit}, notes)


# ---------------------------------------------------------------------------- comparison table
def compare_options(options: List[OptionResult], reserve: float) -> pd.DataFrame:
    rows = []
    for o in options:
        c = o.daily["closing_cash"]
        low_i = int(c.idxmin())
        below = o.daily[c < reserve - 1e-6]
        rows.append({
            "option": o.name, "lowest_cash": float(c.min()), "lowest_cash_date": o.daily.loc[low_i, "date"],
            "days_below_reserve": int(len(below)), "remaining_shortfall_vs_reserve": float(max(reserve - c.min(), 0.0)),
            "closing_cash_end": float(c.iloc[-1]), "interest_in_horizon": o.interest_in_horizon, "fees_in_horizon": o.fees_in_horizon,
            "principal_repaid_in_horizon": o.principal_repaid_in_horizon, "debt_outstanding_end": o.debt_end,
            "interest_over_life": o.life_interest, "effective_annual_cost": o.effective_rate,
        })
    return pd.DataFrame(rows)


def cash_gap_to_fund(base_daily: pd.DataFrame, reserve: float) -> float:
    """Smallest one-off cash injection at the start of the horizon that keeps cash at or above the reserve."""
    return float(max(reserve - base_daily["closing_cash"].min(), 0.0))
