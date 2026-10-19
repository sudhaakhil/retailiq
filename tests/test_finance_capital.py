"""Finance and capital calculations, checked against values recomputed directly from the raw tables."""
import math

import numpy as np
import pandas as pd
import pytest

from retailiq.analytics import accounting as A
from retailiq.analytics import capital as K
from retailiq.analytics import finance as F


@pytest.fixture(scope="module")
def window(small_ds):
    return pd.Timestamp("2025-11-01"), pd.Timestamp("2026-01-31")


def _raw(ds, start, end):
    t = ds.t
    tx = t["sales_transactions"]
    tx = tx[(tx["transaction_date"] >= start) & (tx["transaction_date"] <= end)]
    ln = t["sales_lines"][t["sales_lines"]["transaction_id"].isin(tx["transaction_id"])]
    rt = t["returns"][(t["returns"]["return_date"] >= start) & (t["returns"]["return_date"] <= end)]
    return tx, ln, rt


# ------------------------------------------------------------------- revenue, returns, costs, profit, margin
def test_net_sales_is_after_discounts_and_returns(small_ds, window):
    tx, ln, rt = _raw(small_ds, *window)
    p = F.pnl(small_ds, *window)
    assert p["gross_sales"] == pytest.approx(tx["gross_amount"].sum())
    assert p["discounts"] == pytest.approx(tx["discount_amount"].sum())
    assert p["returns"] == pytest.approx(rt["refund_amount"].sum())
    assert p["net_sales"] == pytest.approx(tx["gross_amount"].sum() - tx["discount_amount"].sum() - rt["refund_amount"].sum())


def test_cogs_is_cost_of_units_sold_net_of_returned_stock_not_purchases(small_ds, window):
    tx, ln, rt = _raw(small_ds, *window)
    p = F.pnl(small_ds, *window)
    expected = ln["cogs_amount"].sum() - rt["cogs_reversal"].sum()
    assert p["cogs"] == pytest.approx(expected)
    purchases = small_ds.t["purchase_orders"]
    bought = purchases[(purchases["received_date"] >= window[0]) & (purchases["received_date"] <= window[1])]["total_cost"].sum()
    assert bought > 0 and abs(p["cogs"] - bought) > 1, "COGS must not simply equal what was purchased"


def test_gross_profit_margin_and_operating_profit_identities(small_ds, window):
    p = F.pnl(small_ds, *window)
    assert p["gross_profit"] == pytest.approx(p["net_sales"] - p["cogs"])
    assert p["gross_margin"] == pytest.approx(p["gross_profit"] / p["net_sales"])
    assert p["operating_profit"] == pytest.approx(p["gross_profit"] - p["operating_expenses"] - p["depreciation"])
    assert p["profit_before_tax"] == pytest.approx(p["operating_profit"] - p["interest_expense"])
    assert p["gross_profit"] != pytest.approx(p["operating_profit"]), "gross profit must not be presented as net profit"


def test_operating_expenses_match_the_expense_table(small_ds, window):
    ox = small_ds.t["operating_expenses"]
    expected = ox[(ox["expense_date"] >= window[0]) & (ox["expense_date"] <= window[1])]["amount"].sum()
    assert F.pnl(small_ds, *window)["operating_expenses"] == pytest.approx(expected)


def test_borrowing_and_owner_money_are_not_revenue_and_principal_is_not_an_expense(small_ds):
    ds = small_ds
    ledger = ds.t["cash_ledger"]
    financing_in = ledger[(ledger["activity"] == "financing") & (ledger["amount"] > 0)]["amount"].sum()
    assert financing_in > 0, "the dataset must contain borrowing/investment for this test to mean anything"
    tx, ln, rt = _raw(ds, ds.start, ds.end)
    p_all = F.pnl(ds, ds.start, ds.end)
    # revenue is built only from sales lines and returns, so financing inflows cannot leak into it
    assert p_all["net_sales"] == pytest.approx(tx["gross_amount"].sum() - tx["discount_amount"].sum() - rt["refund_amount"].sum())
    assert not any("principal" in c.lower() or "loan" in c.lower() for c in p_all["opex_table"]["category"])
    lp = ds.t["loan_payments"]
    assert p_all["interest_expense"] == pytest.approx(lp["interest_amount"].sum())
    assert p_all["principal_repaid_memo"] == pytest.approx(lp["principal_amount"].sum())
    assert p_all["profit_before_tax"] == pytest.approx(p_all["operating_profit"] - lp["interest_amount"].sum())


def test_capital_expenditure_is_depreciated_not_expensed(small_ds):
    ds = small_ds
    capex = ds.t["capital_expenditures"]
    bought = capex[capex["capex_date"] >= ds.start]["amount"].sum()
    p = F.pnl(ds, ds.start, ds.end)
    assert bought > 0
    sched = ds.dep[(ds.dep["dep_date"] >= ds.start) & (ds.dep["dep_date"] <= ds.end)]["amount"].sum()
    assert p["depreciation"] == pytest.approx(sched)            # only the depreciation slice reaches profit
    ox = ds.t["operating_expenses"]
    assert p["operating_expenses"] == pytest.approx(ox["amount"].sum())  # capex is not in operating expenses
    cf = F.cash_flow_statement(ds, ds.start, ds.end)
    assert cf["investing"] == pytest.approx(-bought)            # but the full purchase price leaves cash


def test_zero_sales_period_gives_none_ratios_not_errors(small_ds):
    empty = small_ds.start - pd.Timedelta(days=400)
    p = F.pnl(small_ds, empty, empty + pd.Timedelta(days=10))
    assert p["net_sales"] == 0 and p["gross_margin"] is None and p["operating_margin"] is None


def test_safe_division_and_percentage_change():
    assert A.safe_div(1, 0) is None and A.safe_div(1, None) is None and A.safe_div(float("nan"), 2) is None
    assert A.safe_div(6, 3) == 2
    assert F.pct_change(110, 100) == pytest.approx(0.10)
    assert F.pct_change(5, 0) is None and F.pct_change(None, 5) is None


def test_previous_period_is_equal_length_and_adjacent():
    s, e = F.previous_period("2026-04-01", "2026-06-30")
    assert e == pd.Timestamp("2026-03-31") and (e - s).days == 90


# ------------------------------------------------------------------- allocation of shared costs
def test_store_profits_add_up_to_company_profit(small_ds, window):
    for method in ("net_sales", "equal"):
        stores = F.store_profit_table(small_ds, *window, method)
        whole = F.pnl(small_ds, *window)
        assert stores["operating_profit"].sum() == pytest.approx(whole["operating_profit"])
        assert stores["hq_share"].sum() == pytest.approx(1.0)


def test_equal_allocation_splits_shared_costs_evenly(small_ds, window):
    p = F.pnl(small_ds, *window, ["S01"], allocate="equal")
    assert p["hq_allocation_share"] == pytest.approx(0.5)


# ------------------------------------------------------------------- cash
def test_cash_flow_reconciles_opening_to_closing(small_ds):
    ds = small_ds
    for a, b in [(ds.start, ds.end), (pd.Timestamp("2025-10-15"), pd.Timestamp("2026-02-10"))]:
        cf = F.cash_flow_statement(ds, a, b)
        assert cf["opening_cash"] + cf["inflows"] - cf["outflows"] == pytest.approx(cf["closing_cash"], abs=0.01)
        assert cf["reconciliation_difference"] == pytest.approx(0, abs=0.01)
        assert cf["operating"] + cf["investing"] + cf["financing"] == pytest.approx(cf["net_change"], abs=0.01)


def test_ledger_cash_equals_independent_rebuild_on_many_dates(small_ds):
    for d in pd.date_range(small_ds.start, small_ds.end, periods=6):
        d = d.normalize()
        assert F.cash_balance_at(small_ds, d) == pytest.approx(F.independent_cash_balance(small_ds, d), abs=0.01)


def test_balance_sheet_balances_at_any_date(small_ds):
    for d in pd.date_range(small_ds.opening_date, small_ds.end, periods=8):
        bs = F.balance_sheet(small_ds, d)
        assert bs["difference"] == pytest.approx(0, abs=0.01), d


def test_depreciation_schedule_is_straight_line_and_totals_cost():
    capex = pd.DataFrame({"capex_id": ["A"], "store_id": ["S01"], "capex_date": [pd.Timestamp("2025-01-15")], "amount": [1200.0], "useful_life_months": [12]})
    dep = A.depreciation_schedule(capex, pd.Timestamp("2027-01-01").date())
    assert len(dep) == 12 and dep["amount"].sum() == pytest.approx(1200.0)
    assert dep["dep_date"].min() == pd.Timestamp("2025-02-28")  # starts the month after purchase
    assert A.fixed_assets_net(capex, pd.Timestamp("2025-04-30").date()) == pytest.approx(1200 - 3 * 100)


def test_budget_variance_sign_is_favourable_positive(small_ds):
    b = F.budget_vs_actual(small_ds, by="month")
    assert ((b["a_net_sales"] - b["b_net_sales"]) - b["sales_variance"]).abs().max() < 0.01
    assert ((b["b_operating_expenses"] - b["a_operating_expenses"]) - b["opex_variance"]).abs().max() < 0.01


def test_break_even_uses_contribution_and_handles_loss_making_mix(small_ds, window):
    be = F.contribution_breakeven(small_ds, *window)
    if be["break_even_sales"] is not None:
        assert be["break_even_sales"] == pytest.approx(be["fixed_costs"] / be["contribution_margin_ratio"])
    empty = small_ds.start - pd.Timedelta(days=500)
    z = F.contribution_breakeven(small_ds, empty, empty + pd.Timedelta(days=30))
    assert z["break_even_sales"] is None and z["note"]


# ------------------------------------------------------------------- loans / amortisation
def test_amortisation_separates_interest_and_principal():
    s = A.amortization_schedule(1_200_000, 0.12, 12, pd.Timestamp("2026-01-10").date())
    assert s["principal"].sum() == pytest.approx(1_200_000, abs=0.05)
    assert (s["interest"] + s["principal"] - s["payment"]).abs().max() < 0.011
    assert s["closing_balance"].iloc[-1] == pytest.approx(0, abs=0.01)
    assert s["interest"].iloc[0] == pytest.approx(1_200_000 * 0.01)


def test_zero_interest_loan_amortises_evenly():
    s = A.amortization_schedule(120_000, 0.0, 12, pd.Timestamp("2026-01-10").date())
    assert s["interest"].sum() == 0 and s["principal"].nunique() == 1 and s["principal"].iloc[0] == pytest.approx(10_000)


def test_instalment_validation():
    with pytest.raises(ValueError):
        A.instalment_amount(100, 0.1, 0)
    with pytest.raises(ValueError):
        A.instalment_amount(-1, 0.1, 12)


# ------------------------------------------------------------------- working capital
def test_working_capital_is_inventory_plus_receivables_minus_payables(small_ds):
    s = K.working_capital_snapshot(small_ds, small_ds.end)
    assert s["operating_working_capital"] == pytest.approx(s["inventory"] + s["receivables"] - s["payables"])
    inv = small_ds.t["inventory_daily"]
    assert s["inventory"] == pytest.approx(inv[inv["balance_date"] == small_ds.end]["inventory_value"].sum(), abs=0.01)


def test_days_metrics_and_cash_conversion_cycle_formulas(small_ds):
    s = K.working_capital_snapshot(small_ds, small_ds.end, window_days=90)
    assert s["inventory_days"] == pytest.approx(s["inventory"] / (s["cogs_window"] / s["window_days"]))
    assert s["receivable_days"] == pytest.approx(s["receivables"] / (s["net_sales_window"] / s["window_days"]))
    assert s["payable_days"] == pytest.approx(s["payables"] / (s["cogs_window"] / s["window_days"]))
    assert s["cash_conversion_cycle"] == pytest.approx(s["inventory_days"] + s["receivable_days"] - s["payable_days"])


def test_unsuitable_window_is_flagged_and_zero_sales_gives_none(small_ds):
    early = K.working_capital_snapshot(small_ds, small_ds.start + pd.Timedelta(days=5))
    assert any("n/a" in n for n in early["notes"])
    assert early["inventory_days"] is None and early["receivable_days"] is None and early["cash_conversion_cycle"] is None
    opening = K.working_capital_snapshot(small_ds, small_ds.opening_date)
    assert opening["cash_conversion_cycle"] is None and opening["inventory_days"] is None
    assert opening["notes"], "a missing-flow explanation must be given"


def test_minimum_operating_cash_scales_with_reserve_days(small_ds):
    a = K.working_capital_snapshot(small_ds, small_ds.end, reserve_days=7)["min_operating_cash"]
    b = K.working_capital_snapshot(small_ds, small_ds.end, reserve_days=14)["min_operating_cash"]
    assert b == pytest.approx(2 * a) and a > 0


@pytest.fixture(scope="module")
def snap(small_ds):
    return K.working_capital_snapshot(small_ds, small_ds.end)


def test_scenario_with_unchanged_assumptions_needs_no_extra_capital(snap):
    r = K.wc_scenario(snap, 0.0, snap["gross_margin"], snap["inventory_days"], snap["receivable_days"], snap["payable_days"], snap["min_operating_cash"])
    assert r["valid"] and r["delta_owc"] == pytest.approx(0, abs=1e-6)
    assert r["baseline"]["owc"] == pytest.approx(snap["operating_working_capital"], rel=1e-9)


def test_each_assumption_moves_cash_requirement_in_the_expected_direction(snap):
    base = dict(sales_growth=0.0, gross_margin=snap["gross_margin"], inventory_days=snap["inventory_days"], collection_days=snap["receivable_days"],
                payable_days=snap["payable_days"], min_cash_reserve=snap["min_operating_cash"])

    def delta(**kw):
        return K.wc_scenario(snap, **{**base, **kw})["delta_owc"]

    assert delta(sales_growth=0.3) > 0
    assert delta(inventory_days=snap["inventory_days"] + 20) > 0
    assert delta(collection_days=snap["receivable_days"] + 10) > 0
    assert delta(payable_days=snap["payable_days"] + 15) < 0
    # higher margin = lower cost per rupee of sales: stock and payables shrink together, so the sign follows (inventory days - payable days)
    margin_effect = delta(gross_margin=base["gross_margin"] + 0.1)
    assert np.sign(margin_effect) == np.sign(snap["payable_days"] - snap["inventory_days"])


def test_scenario_rejects_invalid_inputs_and_zero_sales(snap):
    assert not K.wc_scenario(snap, 0.1, 1.2, 30, 5, 30, 1000)["valid"]
    assert not K.wc_scenario(snap, -1.5, 0.4, 30, 5, 30, 1000)["valid"]
    assert not K.wc_scenario(snap, 0.1, 0.4, -3, 5, 30, 1000)["valid"]
    zero = {**snap, "daily_net_sales": 0.0}
    r = K.wc_scenario(zero, 0.2, 0.4, 30, 5, 30, 1000)
    assert not r["valid"] and any("zero" in p.lower() for p in r["problems"])


def test_scenario_refuses_to_run_without_a_baseline_for_short_history(small_ds):
    early = K.working_capital_snapshot(small_ds, small_ds.start + pd.Timedelta(days=5))
    r = K.wc_scenario(early, 0.1, 0.4, 30, 5, 30, 1000)
    assert not r["valid"] and any("not available" in p for p in r["problems"])


def test_capital_for_sales_increase_and_shortfall_logic(snap):
    r = K.capital_for_sales_increase(snap, 0.5)
    assert r["valid"] and r["delta_owc"] > 0
    # the reduced-traffic test business has full fixed costs, so its cash can be negative: use a reserve far above any cash balance
    huge_reserve = abs(snap["cash"]) * 5 + 10_000_000
    big = K.wc_scenario(snap, 0.2, snap["gross_margin"], snap["inventory_days"], snap["receivable_days"], snap["payable_days"], huge_reserve)
    assert big["shortfall"] > 0 and big["capital_required"] == big["shortfall"]


def test_ageing_buckets_add_up_to_balances(small_ds):
    ar = K.receivable_aging(small_ds)["amount"].sum()
    assert ar == pytest.approx(small_ds.daily_balances["receivables"].loc[small_ds.ref_date], abs=0.5)
    ap = K.payable_aging(small_ds)["amount"].sum()
    assert ap == pytest.approx(small_ds.daily_balances["payables"].loc[small_ds.ref_date], abs=0.5)
