"""Inventory/reorder logic, funding schedules and the 13-week cash forecast."""
import math

import numpy as np
import pandas as pd
import pytest

from retailiq.analytics import cashflow as CF
from retailiq.analytics import funding as FU
from retailiq.analytics import inventory as INV


# ===================================================================== inventory
@pytest.fixture(scope="module")
def plan(small_ds):
    return INV.replenishment_plan(small_ds, cover_days=30)


def test_stock_movements_reconcile_with_balances(small_ds):
    rec = INV.reconcile_movements(small_ds)
    assert rec["qty_diff"].abs().max() == 0 and rec["value_diff"].abs().max() < 0.01
    mid = INV.reconcile_movements(small_ds, small_ds.start + pd.Timedelta(days=120))
    assert mid["qty_diff"].abs().max() == 0


def test_valuation_equals_quantity_times_average_cost(small_ds):
    pos = INV.stock_position(small_ds)
    nz = pos[pos["quantity_on_hand"] > 0]
    assert ((nz["quantity_on_hand"] * nz["avg_unit_cost"]) - nz["inventory_value"]).abs().max() < 1.0


def test_reorder_quantities_are_never_negative_and_respect_case_packs_and_caps(plan):
    assert (plan["suggested_qty"] >= 0).all() and (plan["essential_qty"] >= 0).all()
    q = plan[plan["suggested_qty"] > 0]
    assert (q["suggested_qty"] % q["case_pack"] == 0).all()
    assert (q["suggested_qty"] <= (q["max_units_per_order"] // q["case_pack"]) * q["case_pack"]).all()
    assert np.allclose(plan["est_purchase_cost"], plan["suggested_qty"] * plan["unit_cost"])
    assert (plan["essential_qty"] <= plan["suggested_qty"]).all()


def test_reorder_point_and_safety_stock_follow_the_documented_formulas(plan):
    z = INV.DEFAULT_Z
    r = plan.iloc[0:40]
    ss = z * np.sqrt(r["lead_time_days"] * r["demand_std"] ** 2 + r["daily_demand"] ** 2 * r["lead_time_std"] ** 2)
    assert np.allclose(r["safety_stock"], ss)
    assert np.allclose(r["reorder_point"], r["daily_demand"] * r["lead_time_days"] + r["safety_stock"])
    assert np.allclose(r["order_up_to"], r["daily_demand"] * (r["lead_time_days"] + 30) + r["safety_stock"])


def test_stock_on_order_reduces_the_suggested_quantity(small_ds):
    pos = INV.stock_position(small_ds)
    with_orders = pos[pos["quantity_on_order"] > 0]
    assert len(with_orders) > 0, "the dataset needs open purchase orders for this test"
    plan = INV.replenishment_plan(small_ds)
    r = plan[plan["on_order"] > 0].iloc[0]
    raw_without_order = max(0.0, r["order_up_to"] - r["on_hand"])
    raw_with_order = max(0.0, r["order_up_to"] - r["on_hand"] - r["on_order"])
    assert raw_with_order <= raw_without_order
    assert r["suggested_qty"] <= math.ceil(raw_with_order / r["case_pack"]) * r["case_pack"] + 1e-9


def test_higher_service_level_means_more_safety_stock(small_ds):
    lo = INV.replenishment_plan(small_ds, z=1.28)["safety_stock"].sum()
    hi = INV.replenishment_plan(small_ds, z=2.33)["safety_stock"].sum()
    assert hi > lo


def test_status_flags_follow_rules(plan):
    out = plan[plan["status"] == "Out of stock"]
    assert (out["on_hand"] == 0).all()
    low = plan[plan["status"] == "Low stock - reorder"]
    assert ((low["on_hand"] + low["on_order"]) <= low["reorder_point"] + 1e-9).all()
    slow = plan[plan["movement"] == "Slow-moving"]
    assert (slow["days_of_stock"] > 120).all()


def test_days_of_stock_is_none_when_no_demand(small_ds):
    plan = INV.replenishment_plan(small_ds, window_days=14)
    nodem = plan[plan["daily_demand"] == 0]
    assert nodem["days_of_stock"].isna().all()


def _orders():
    return pd.DataFrame({
        "store_id": ["S01"] * 4, "product_id": list("ABCD"), "product_name": list("ABCD"), "supplier_id": ["X"] * 4,
        "on_hand": [0, 5, 40, 90], "on_order": [0, 0, 0, 0], "daily_demand": [5.0, 3.0, 2.0, 1.0], "shortfall_days": [10.0, 4.0, 0.0, 0.0],
        "unit_margin": [100.0, 50.0, 80.0, 30.0], "unit_cost": [100.0, 100.0, 100.0, 100.0], "case_pack": [10, 10, 10, 10],
        "suggested_qty": [100, 50, 40, 20], "essential_qty": [50, 20, 0, 0], "gmroi": [3.0, 2.0, 4.0, 1.0], "est_purchase_cost": [10000, 5000, 4000, 2000],
        "cover_after_order_days": [20, 17, 20, 110],
    })


def test_budget_allocation_never_exceeds_budget_and_funds_essentials_first():
    o = INV.prioritise_orders(_orders(), 7000)
    assert o["funded_cost"].sum() <= 7000
    first = o.iloc[0]
    assert first["product_id"] == "A" and first["funded_qty"] >= first["essential_qty"]
    assert (o["funded_qty"] >= 0).all() and (o["funded_qty"] % 10 == 0).all()


def test_zero_budget_funds_nothing_and_unlimited_budget_funds_everything():
    zero = INV.prioritise_orders(_orders(), 0)
    assert zero["funded_qty"].sum() == 0 and (zero["decision"] == "Not funded (budget)").all()
    full = INV.prioritise_orders(_orders(), None)
    assert (full["funded_qty"] == full["suggested_qty"]).all() and (full["decision"] == "Funded in full").all()


def test_extra_cover_is_funded_only_after_essentials_everywhere():
    o = INV.prioritise_orders(_orders(), 7600)  # enough for every essential unit (7000) plus a little extra
    ess = o["essential_qty"].sum() * 100
    assert o["funded_cost"].sum() >= ess
    assert (o["funded_qty"] >= o["essential_qty"]).all()


# ===================================================================== funding
def test_term_loan_schedule_principal_interest_separation():
    s = FU.term_loan_schedule(1_000_000, 0.12, 24, "2026-10-10")
    assert s["principal"].sum() == pytest.approx(1_000_000, abs=0.1)
    assert (s["interest"] + s["principal"]).sub(s["payment"]).abs().max() < 0.011
    assert s["closing_balance"].iloc[-1] == pytest.approx(0, abs=0.01)
    assert s["interest"].iloc[0] == pytest.approx(10_000)
    assert s["payment_date"].iloc[0] == pd.Timestamp("2026-11-10")


def test_zero_interest_loan_and_boundary_terms():
    s = FU.term_loan_schedule(120_000, 0.0, 12, "2026-10-10")
    assert s["interest"].sum() == 0 and s["payment"].nunique() == 1
    one = FU.term_loan_schedule(50_000, 0.10, 1, "2026-10-10")
    assert len(one) == 1 and one["principal"].iloc[0] == pytest.approx(50_000)
    zero = FU.term_loan_schedule(0, 0.1, 6, "2026-10-10")
    assert zero["principal"].sum() == 0


def test_interest_only_grace_period():
    s = FU.term_loan_schedule(600_000, 0.12, 12, "2026-10-10", grace_months=3)
    assert (s["principal"].iloc[:3] == 0).all() and np.allclose(s["interest"].iloc[:3], 6000)
    assert s["principal"].sum() == pytest.approx(600_000, abs=0.1) and s["closing_balance"].iloc[-1] == pytest.approx(0, abs=0.01)


@pytest.mark.parametrize("kwargs", [dict(principal=-1, annual_rate=0.1, term_months=12), dict(principal=1, annual_rate=-0.1, term_months=12),
                                    dict(principal=1, annual_rate=0.1, term_months=0), dict(principal=1, annual_rate=0.1, term_months=12, grace_months=12)])
def test_invalid_loan_inputs_raise(kwargs):
    with pytest.raises(ValueError):
        FU.term_loan_schedule(start_date="2026-10-10", **kwargs)


def test_effective_cost_matches_nominal_rate_without_fees_and_rises_with_fees():
    s = FU.term_loan_schedule(1_000_000, 0.12, 36, "2026-10-10")
    no_fee = FU.effective_annual_cost(1_000_000, list(s["payment"]))
    assert no_fee == pytest.approx((1 + 0.12 / 12) ** 12 - 1, abs=1e-4)
    with_fee = FU.effective_annual_cost(990_000, list(s["payment"]))
    assert with_fee > no_fee
    assert FU.effective_annual_cost(1000, [0.0]) is None
    assert FU.effective_annual_cost(1_000_000, [1_000_000 / 12] * 12) == 0.0


def test_equity_dilution_and_validation():
    d = FU.equity_dilution(2_000_000, 8_000_000)
    assert d["post_money_valuation"] == 10_000_000 and d["new_investor_pct"] == pytest.approx(0.2)
    assert d["existing_owner_pct_after"] == pytest.approx(0.8)
    for bad in [(1, 0), (-1, 100), (1, -5)]:
        with pytest.raises(ValueError):
            FU.equity_dilution(*bad)


@pytest.fixture(scope="module")
def base_forecast(small_ds):
    return CF.forecast_cash(small_ds, CF.CashAssumptions(min_cash_reserve=0.0))


def test_term_loan_cash_path_separates_inflow_from_repayments(base_forecast):
    bd = base_forecast["daily"]
    d0 = bd["date"].iloc[5]
    opt = FU.term_loan_option(bd, 600_000, 0.12, 12, d0, upfront_fee_pct=0.01)
    flows = opt.daily["financing_flow"]
    assert flows.iloc[5] == pytest.approx(600_000 - 6_000)           # principal received less fee: financing inflow
    assert opt.principal_repaid_in_horizon + opt.interest_in_horizon == pytest.approx(-flows[flows < 0].sum())
    assert opt.debt_end == pytest.approx(600_000 - opt.principal_repaid_in_horizon, abs=0.01)
    base_path = FU.baseline_option(bd).daily["closing_cash"]
    assert (opt.daily["closing_cash"].iloc[5] - base_path.iloc[5]) == pytest.approx(594_000)


def test_owner_capital_adds_cash_without_debt_or_interest(base_forecast):
    bd = base_forecast["daily"]
    opt = FU.owner_capital_option(bd, 500_000, bd["date"].iloc[3], pre_money=4_500_000)
    assert opt.debt_end == 0 and opt.interest_in_horizon == 0
    assert (opt.daily["closing_cash"] - FU.baseline_option(bd).daily["closing_cash"]).iloc[10] == pytest.approx(500_000)
    assert opt.extra["new_investor_pct"] == pytest.approx(0.1)


def test_revolver_draws_only_when_needed_and_never_beyond_limit(base_forecast):
    bd = base_forecast["daily"]
    reserve = float(bd["closing_cash"].max() + 50_000)  # force a permanent shortfall
    opt = FU.revolver_option(bd, 200_000, 0.12, reserve, bd["date"].iloc[0], 0.0, 0.0)
    assert opt.daily["debt_balance"].max() <= 200_000 + 1e-6
    assert opt.extra["peak_drawn"] == pytest.approx(200_000)
    comfortable = FU.revolver_option(bd, 200_000, 0.12, float(bd["closing_cash"].min() - 1_000_000), bd["date"].iloc[0], 0.0, 0.0)
    assert comfortable.daily["debt_balance"].max() == 0 and comfortable.interest_in_horizon == 0


def test_revolver_charges_interest_and_commitment_fees(base_forecast):
    bd = base_forecast["daily"]
    reserve = float(bd["closing_cash"].max() + 100_000)
    opt = FU.revolver_option(bd, 300_000, 0.10, reserve, bd["date"].iloc[0], 0.01, 0.02)
    assert opt.interest_in_horizon > 0 and opt.fees_in_horizon >= 300_000 * 0.01


def test_comparison_table_flags_shortfall(base_forecast):
    bd = base_forecast["daily"]
    reserve = float(bd["closing_cash"].min() + 400_000)
    opts = [FU.baseline_option(bd), FU.owner_capital_option(bd, 600_000, bd["date"].iloc[0])]
    cmp_ = FU.compare_options(opts, reserve)
    assert cmp_.loc[0, "remaining_shortfall_vs_reserve"] == pytest.approx(400_000, abs=1)
    assert cmp_.loc[1, "remaining_shortfall_vs_reserve"] == 0 and cmp_.loc[1, "days_below_reserve"] == 0
    assert FU.cash_gap_to_fund(bd, reserve) == pytest.approx(400_000, abs=1)


# ===================================================================== 13-week cash forecast
def test_forecast_covers_thirteen_weeks_from_the_next_day(small_ds, base_forecast):
    d = base_forecast["daily"]
    assert len(d) == 91 and d["date"].iloc[0] == small_ds.ref_date + pd.Timedelta(days=1)
    assert len(base_forecast["weekly"]) == 13


def test_closing_cash_equals_opening_plus_cumulative_net_flow(small_ds, base_forecast):
    d = base_forecast["daily"]
    opening = base_forecast["summary"]["opening_cash"]
    assert opening == pytest.approx(small_ds.daily_balances["cash"].loc[small_ds.ref_date])
    assert np.allclose(d["closing_cash"], opening + d["net_cash_flow"].cumsum())
    flow_cols = [c for c in d.columns if c not in ("date", "closing_cash", "net_cash_flow", "forecast_net_sales")]
    assert np.allclose(d[flow_cols].sum(axis=1), d["net_cash_flow"])


def test_weekly_totals_equal_daily_totals_and_weeks_chain(base_forecast):
    d, w = base_forecast["daily"], base_forecast["weekly"]
    assert w["net_cash_flow"].sum() == pytest.approx(d["net_cash_flow"].sum())
    assert w["closing_cash"].iloc[-1] == pytest.approx(d["closing_cash"].iloc[-1])
    assert np.allclose(w["opening_cash"].iloc[1:].to_numpy(), w["closing_cash"].iloc[:-1].to_numpy())
    assert w["lowest_daily_cash"].min() == pytest.approx(d["closing_cash"].min())


def test_loan_payments_in_forecast_split_interest_and_principal(small_ds, base_forecast):
    d = base_forecast["daily"]
    assert (d["loan_interest"] <= 0).all() and (d["loan_principal"] <= 0).all()
    due = 0.0
    for sched in small_ds.loan_schedules.values():
        w = sched[(sched["payment_date"] >= d["date"].iloc[0]) & (sched["payment_date"] <= d["date"].iloc[-1])]
        due += w["payment"].sum()
    assert -(d["loan_interest"].sum() + d["loan_principal"].sum()) == pytest.approx(due, abs=0.05)


def test_shortfall_summary_finds_first_breach_and_size():
    dates = pd.date_range("2026-10-01", periods=14)
    daily = pd.DataFrame({"date": dates, "closing_cash": [500, 450, 400, 300, 250, 260, 270, 280, 290, 300, 310, 320, 330, 340.0]})
    weekly = pd.DataFrame({"week": [1, 2], "week_start": [dates[0], dates[7]], "week_end": [dates[6], dates[13]], "closing_cash": [270.0, 340.0]})
    s = CF.shortfall_summary(daily, weekly, 300.0)
    assert s["breach"] and s["first_breach_date"] == dates[4] and s["lowest_cash"] == 250 and s["max_shortfall"] == 50
    assert s["first_breach_week"] == 1 and s["days_below_reserve"] == 5  # 250, 260, 270, 280, 290 are below 300
    ok = CF.shortfall_summary(daily, weekly, 100.0)
    assert not ok["breach"] and ok["first_breach_date"] is None and ok["max_shortfall"] == 0


def test_stress_assumptions_lower_the_cash_path(small_ds, base_forecast):
    stress = CF.forecast_cash(small_ds, CF.CashAssumptions(min_cash_reserve=0.0, sales_adjustment=-0.3, collection_delay_days=20))
    assert stress["daily"]["closing_cash"].iloc[-1] < base_forecast["daily"]["closing_cash"].iloc[-1]
    later = CF.forecast_cash(small_ds, CF.CashAssumptions(min_cash_reserve=0.0, supplier_delay_days=15))
    assert later["daily"]["supplier_payments_existing_invoices"].sum() >= base_forecast["daily"]["supplier_payments_existing_invoices"].sum() - 1e-6


def test_planned_items_enter_the_forecast_and_sit_outside_operating_lines(small_ds):
    ref = small_ds.ref_date
    planned = pd.DataFrame({"planned_date": [ref + pd.Timedelta(days=20)], "category": ["Capex"], "description": ["test"], "amount": [-500_000.0], "activity": ["investing"]})
    r = CF.forecast_cash(small_ds, CF.CashAssumptions(planned_items=planned))
    assert r["daily"]["capital_expenditure_and_planned_investing"].sum() == pytest.approx(-500_000)
    none = CF.forecast_cash(small_ds, CF.CashAssumptions(planned_items=planned.iloc[0:0]))
    assert none["daily"]["capital_expenditure_and_planned_investing"].sum() == 0


def test_no_collection_is_forecast_before_tomorrow_and_buying_less_stock_saves_cash(small_ds, base_forecast):
    assert base_forecast["daily"]["date"].min() > small_ds.ref_date
    cut = CF.forecast_cash(small_ds, CF.CashAssumptions(inventory_release_pct=0.5))
    assert cut["daily"]["supplier_payments_inventory_purchases"].sum() >= base_forecast["daily"]["supplier_payments_inventory_purchases"].sum()
